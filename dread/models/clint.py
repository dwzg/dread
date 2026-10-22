"""The RISC-V core-local interruptor (CLINT, as SiFive's and QEMU's virt board have it): the machine
timer (mtime, mtimecmp) and the software interrupt (msip) of each hart.

    [models.clint]
    kind = "clint"
    base = 0x02000000
    harts = 1               # mtimecmp and msip per hart (the system run's core is hart 0)
    rate = 4                # shared-clock units per mtime count

Registers: msip at base + 4 * hart (bit 0), mtimecmp at base + 0x4000 + 8 * hart and mtime at
base + 0xbff8, both 64 bits wide and accessed as two words (a write of one half keeps the other).
mtime counts the shared clock (so it reads alike in both images); a write of it sets it from then
on. mtimecmp and msip start at zero, so by the spec's level the timer interrupt is pending from
reset until mtimecmp is written (firmware writes it before it sets mie.MTIE).

Interrupts, as the core numbers them (mcause): the machine timer interrupt (7) is pending while
mtime >= mtimecmp, the machine software interrupt (3) while msip's bit 0 is set; the software one
first when both are (the privileged spec's order). The system run takes one when mstatus.MIE and the
interrupt's bit in mie are set (may_take, with the layer's cpu_masks); both are levels, so a handler
ends one by writing mtimecmp or msip. Every read but mtime's (the clock) is plain (dread.models); no
write is idempotent (each arms the timer against mtime, which moves with the clock). Assumed: one
hart runs.
"""
from dread.models import Model, without

MSIP, MTIMECMP, MTIME = 0x0, 0x4000, 0xBFF8
SOFTWARE, TIMER = 3, 7
MSTATUS_MIE = 1 << 3


class Clint(Model):
    kind = "clint"

    def __init__(self, system, name, config):
        super().__init__(system, name, config)
        self.base = self.number("base")
        self.harts = self.number("harts", 1)
        self.rate = max(1, self.number("rate", 4))
        self.ranges = [(self.base, self.base + 0xC000)]
        self.plain = without(self.ranges, [(self.base + MTIME, self.base + MTIME + 8)])
        self.msip = [0] * self.harts
        self.mtimecmp = [0] * self.harts
        self.offset = 0                     # mtime = clock // rate + offset (a write of mtime moves it)
        self.due = None                     # the clock at which hart 0's timer interrupt comes pending

    # ---- time ----

    def mtime(self):
        return (self.system.clock // self.rate + self.offset) & 0xFFFFFFFFFFFFFFFF

    def _arm(self):
        """The time hart 0's timer interrupt comes pending, kept until event() takes it (the system
        run asks only at its stops), or None when it is pending already."""
        self.due = None if self.mtimecmp[0] <= self.mtime() else (self.mtimecmp[0] - self.offset) * self.rate

    def next_event(self):
        return self.due

    def event(self, clock):
        self.due = None
        self.system.interrupts_changed()

    # ---- registers ----

    def _register(self, address):
        """(name, hart, byte offset in the register) of an address, or None."""
        at = address - self.base
        if MSIP <= at < MSIP + 4 * self.harts:
            return "msip", (at - MSIP) // 4, (at - MSIP) % 4
        if MTIMECMP <= at < MTIMECMP + 8 * self.harts:
            return "mtimecmp", (at - MTIMECMP) // 8, (at - MTIMECMP) % 8
        if MTIME <= at < MTIME + 8:
            return "mtime", 0, at - MTIME
        return None

    def _value(self, name, hart):
        if name == "msip":
            return self.msip[hart]
        if name == "mtimecmp":
            return self.mtimecmp[hart]
        return self.mtime()

    def read(self, address, size):
        reg = self._register(address)
        if reg is None:
            return None
        name, hart, shift = reg
        return (self._value(name, hart) >> (8 * shift)) & ((1 << (8 * size)) - 1)

    def write(self, address, size, value):
        reg = self._register(address)
        if reg is None:
            return
        name, hart, shift = reg
        mask = ((1 << (8 * size)) - 1) << (8 * shift)
        new = (self._value(name, hart) & ~mask) | ((value << (8 * shift)) & mask)
        if name == "msip":
            self.msip[hart] = new & 1
        elif name == "mtimecmp":
            self.mtimecmp[hart] = new & 0xFFFFFFFFFFFFFFFF
        else:
            self.offset = new - self.system.clock // self.rate      # signed: _arm counts with it
        self._arm()
        self.system.interrupts_changed()

    # ---- interrupts ----

    def pending_interrupt(self):
        if self.msip[0] & 1:
            return SOFTWARE
        if self.mtime() >= self.mtimecmp[0]:
            return TIMER
        return None

    def may_take(self, number, masks):
        return bool(masks.get("mstatus", 0) & MSTATUS_MIE) and bool(masks.get("mie", 0) >> number & 1)

    def core_cause(self, number):
        """The interrupt as mcause names it: the model's numbers are the core's."""
        return number

    def line(self, number, level):
        pass                                # no external lines come here

    def taken(self, number):
        pass                                # levels: the handler ends them

    def returned(self):
        pass
