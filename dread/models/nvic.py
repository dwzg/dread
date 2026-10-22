"""The Cortex-M (ARMv7-M) exception controller: the NVIC, the exception registers of the system
control block and SysTick, with priorities, nesting and the core's masks.

    [models.nvic]
    kind = "nvic"
    lines = 32              # external interrupt lines (exception numbers 16 and up)
    systick_rate = 4        # shared-clock units per SysTick count (SysTick is always there)
    priority_bits = 8       # implemented bits of a priority byte (the high ones; the rest read as 0)

Registers (the architecture's addresses):

- SysTick at 0xE000E010: CSR (enable, interrupt, countflag), RVR reload, CVR current (a write
  clears it), CALIB (0).
- NVIC at 0xE000E100: ISER/ICER (set and clear enable), ISPR/ICPR (set and clear pending) at +0x000,
  +0x080, +0x100, +0x180, IABR (active) at +0x200, IPR (a priority byte per line) at +0x300.
- SCB: ICSR at 0xE000ED04 (writes: PENDSVSET/CLR, PENDSTSET/CLR, NMIPENDSET; reads: VECTACTIVE,
  RETTOBASE, VECTPENDING, ISRPENDING and the two pending bits), VTOR at 0xE000ED08 (its value at
  reset is the target's vector table), AIRCR at 0xE000ED0C (PRIGROUP; a write needs the key 0x05FA),
  SHPR1-3 at 0xE000ED18 (the priorities of exceptions 4-15). The other SCB registers are memory.

An exception pends: a line while it is high (and again when a handler returns while the line is
still high); SysTick when it counts to zero with its interrupt enabled; PendSV and SysTick through
ICSR; SVCall when the core runs svc (the system run pends it, pend()). The pending enabled exception
with the highest priority (the lowest value, then the lowest number) is offered, and the system run
takes it when its group priority (PRIGROUP) is higher than the core's execution priority
(may_take): the highest of the active exceptions' group priorities, BASEPRI's when not 0, PRIMASK's
(0) and FAULTMASK's (-1), read from the core by the layer (cpu_masks). So a handler is preempted by
an exception of higher priority, and the active exceptions form a stack (returns come in reverse
order). With every priority 0, the state after reset, no exception preempts another.

Entry and return push and pop the frame through the architecture layer (dread.arch.arm
enter_interrupt, exception_return): the extended frame with s0-s15 and FPSCR where the FPU's state
is active (CONTROL.FPCA), with EXC_RETURN's bit 4 clear; the hardware's lazy stacking is modelled
eagerly (the same registers; the reserved words' memory differs when a handler never uses the FPU).

Every read but CSR's (COUNTFLAG clears) and CVR's (the clock) is plain (dread.models): FreeRTOS's
Cortex-M port reads ICSR in every critical section.

Assumed: the model raises no NMI or fault exceptions of its own (an svc that cannot be taken would
escalate to HardFault; the system run stops with a fault instead), tail-chaining is a return
followed by an entry (the same memory and registers in the end), no late arrival, SHCSR and the
fault status registers are memory.
"""
from dread.models import Model, without

SYST, NVIC, SCB = 0xE000E010, 0xE000E100, 0xE000ED00
ICSR, VTOR, AIRCR, SHPR = SCB + 0x4, SCB + 0x8, SCB + 0xC, SCB + 0x18
IPR = NVIC + 0x300
NMI, SVCALL, PENDSV, SYSTICK = 2, 11, 14, 15
FIXED = {1: -3, 2: -2, 3: -1}               # reset, NMI and HardFault: priorities nothing sets
SET_CLEAR = ((NVIC, "enabled", True), (NVIC + 0x80, "enabled", False),
             (NVIC + 0x100, "pending", True), (NVIC + 0x180, "pending", False))


class Nvic(Model):
    kind = "nvic"

    def __init__(self, system, name, config):
        super().__init__(system, name, config)
        self.lines = self.number("lines", 32)
        self.rate = self.number("systick_rate", 4)
        bits = self.number("priority_bits", 8)
        if not 1 <= bits <= 8:
            raise ValueError("model %s: priority_bits must be 1 to 8" % name)
        self.prio_mask = (0xFF << (8 - bits)) & 0xFF
        self.ranges = [(SYST, SYST + 0x10), (NVIC, IPR + self.lines), (ICSR, AIRCR + 4), (SHPR, SHPR + 12)]
        self.plain = without(self.ranges, [(SYST, SYST + 4), (SYST + 8, SYST + 12)])
        self.enabled = self.pending = self.levels = 0       # bit n: line n
        self.system_pending = set()                         # pending exceptions below 16
        self.active = []                                    # active exceptions, the latest last
        self.ipr, self.shpr = bytearray(self.lines), bytearray(12)
        self.prigroup = 0
        image = getattr(system, "image", None)
        self.vtor = getattr(image, "vectors", 0) or 0
        self.csr = self.rvr = 0
        self.deadline = None

    # ---- registers ----

    def _bank(self, address, base):
        return (address - base) // 4 if base <= address < base + 4 * ((self.lines + 31) // 32) else None

    def _active_lines(self):
        bits = 0
        for n in self.active:
            if n >= 16:
                bits |= 1 << (n - 16)
        return bits

    def _icsr(self):
        value = self.active[-1] if self.active else 0                   # VECTACTIVE
        if len(self.active) <= 1:
            value |= 1 << 11                                            # RETTOBASE
        best = self.pending_interrupt()
        if best is not None:
            value |= (best & 0x1FF) << 12                               # VECTPENDING
        if self.pending:
            value |= 1 << 22                                            # ISRPENDING
        if SYSTICK in self.system_pending:
            value |= 1 << 26
        if PENDSV in self.system_pending:
            value |= 1 << 28
        if NMI in self.system_pending:
            value |= 1 << 31
        return value

    def _word(self, address):
        """The value of the word register at `address` (with a read's side effects), or None."""
        if address == SYST:
            value, self.csr = self.csr, self.csr & ~(1 << 16)           # countflag clears on read
            return value
        if address == SYST + 4:
            return self.rvr
        if address == SYST + 8:
            if self.deadline is None:
                return 0
            return max(0, (self.deadline - self.system.clock) // self.rate) & 0xFFFFFF
        if address == SYST + 12:
            return 0
        if address == ICSR:
            return self._icsr()
        if address == VTOR:
            return self.vtor
        if address == AIRCR:
            return 0xFA050000 | self.prigroup << 8
        for base, which in ((NVIC, self.enabled), (NVIC + 0x80, self.enabled), (NVIC + 0x100, self.pending),
                            (NVIC + 0x180, self.pending), (NVIC + 0x200, self._active_lines())):
            bank = self._bank(address, base)
            if bank is not None:
                return which >> (32 * bank) & 0xFFFFFFFF
        return None

    def read(self, address, size):
        for table, base in ((self.ipr, IPR), (self.shpr, SHPR)):
            if base <= address < base + len(table):
                at = address - base
                return int.from_bytes(table[at:at + size].ljust(size, b"\0"), "little")
        value = self._word(address & ~3)
        return None if value is None else value >> (8 * (address & 3))

    def write(self, address, size, value):
        for table, base in ((self.ipr, IPR), (self.shpr, SHPR)):
            if base <= address < base + len(table):
                at = address - base
                for i in range(min(size, len(table) - at)):
                    table[at + i] = (value >> (8 * i)) & self.prio_mask
                self.system.interrupts_changed()
                return
        word, shift = address & ~3, 8 * (address & 3)
        bits = (value << shift) & 0xFFFFFFFF            # a narrow store to a register of set and clear bits
        if word in (SYST, SYST + 4, SYST + 8, VTOR, AIRCR) and size < 4:   # a narrow store to a value
            old = self._word(word) if word != SYST else self.csr
            mask = ((1 << (8 * size)) - 1) << shift
            bits = (old & ~mask) | bits
        if word == SYST:
            self.csr = (self.csr & (1 << 16)) | (bits & 7)
            self.deadline = self.system.clock + (self.rvr + 1) * self.rate if bits & 1 else None
        elif word == SYST + 4:
            self.rvr = bits & 0xFFFFFF
        elif word == SYST + 8:
            self.csr &= ~(1 << 16)
            if self.csr & 1:
                self.deadline = self.system.clock + (self.rvr + 1) * self.rate
        elif word == ICSR:
            for bit, number, add in ((28, PENDSV, True), (27, PENDSV, False), (26, SYSTICK, True),
                                     (25, SYSTICK, False), (31, NMI, True)):
                if bits >> bit & 1:
                    (self.system_pending.add if add else self.system_pending.discard)(number)
        elif word == VTOR:
            self.vtor = bits & 0xFFFFFF80
        elif word == AIRCR:
            if bits >> 16 == 0x05FA:
                self.prigroup = (bits >> 8) & 7         # SYSRESETREQ and VECTRESET are not modelled
        else:
            for base, which, add in SET_CLEAR:
                bank = self._bank(word, base)
                if bank is not None:
                    mask = bits << (32 * bank)
                    current = getattr(self, which)
                    setattr(self, which, current | mask if add else current & ~mask)
                    break
        self.system.interrupts_changed()

    # ---- time ----

    def next_event(self):
        return self.deadline

    def event(self, clock):
        self.csr |= 1 << 16
        if self.csr & 2:
            self.system_pending.add(SYSTICK)
            self.system.interrupts_changed()
        self.deadline += (self.rvr + 1) * self.rate

    # ---- exceptions ----

    def line(self, number, level):
        bit = 1 << number
        self.levels = self.levels | bit if level else self.levels & ~bit
        if level:
            self.pending |= bit

    def pend(self, number):
        """Pend an exception below 16 the core raises itself (SVCall for svc)."""
        self.system_pending.add(number)

    def priority(self, number):
        if number in FIXED:
            return FIXED[number]
        if 4 <= number < 16:
            return self.shpr[number - 4]
        return self.ipr[number - 16] if number - 16 < self.lines else 0

    def group(self, priority):
        """The group priority (PRIGROUP's upper field), which alone decides preemption."""
        return priority if priority < 0 else priority & ~((2 << self.prigroup) - 1) & 0xFF

    def _pending(self):
        out = list(self.system_pending)
        live = self.pending & self.enabled
        while live:
            low = live & -live
            out.append(16 + low.bit_length() - 1)
            live ^= low
        return out

    def pending_interrupt(self):
        """The pending enabled exception of the highest priority, or None (masks aside)."""
        pending = self._pending()
        return min(pending, key=lambda n: (self.priority(n), n)) if pending else None

    def execution_priority(self, masks):
        """The core's execution priority: what an exception's group priority must be below to be
        taken. masks: the core's primask, faultmask and basepri (the layer's cpu_masks)."""
        priority = 256
        for n in self.active:
            priority = min(priority, self.group(self.priority(n)))
        basepri = masks.get("basepri", 0) & self.prio_mask
        if basepri:
            priority = min(priority, self.group(basepri))
        if masks.get("primask"):
            priority = min(priority, 0)
        if masks.get("faultmask"):
            priority = min(priority, -1)
        return priority

    def may_take(self, number, masks):
        return self.group(self.priority(number)) < self.execution_priority(masks)

    def vector_base(self):
        return self.vtor

    def taken(self, number):
        if number < 16:
            self.system_pending.discard(number)
        else:
            self.pending &= ~(1 << (number - 16))
        self.active.append(number)

    def returned(self):
        if self.active:
            self.active.pop()
        self.pending |= self.levels         # a line still high pends its interrupt again
