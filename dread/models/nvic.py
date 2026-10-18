"""The Cortex-M NVIC's enable and pending registers for external interrupts, and SysTick.

    [models.nvic]
    kind = "nvic"
    lines = 32              # external interrupt lines (exception numbers 16 and up)
    systick_rate = 4        # shared-clock units per SysTick count (SysTick is always there)

Registers (the architecture's addresses): SysTick at 0xE000E010 (CSR: enable, interrupt, countflag;
RVR reload; CVR current, a write clears it), NVIC_ISER/ICER (set and clear enable), NVIC_ISPR/ICPR
(set and clear pending) at 0xE000E100, 0xE000E180, 0xE000E200, 0xE000E280; the priority registers
are memory. A line pends its interrupt while high (and again after the handler returns if it is
still high); the lowest pending enabled exception is taken, SysTick (15) before the lines. Assumed:
no priorities, no nesting (the system run takes an exception only from thread mode), no PendSV.
"""
from dread.models import Model

SYST, NVIC = 0xE000E010, 0xE000E100


class Nvic(Model):
    kind = "nvic"

    def __init__(self, system, name, config):
        super().__init__(system, name, config)
        self.lines = self.number("lines", 32)
        self.rate = self.number("systick_rate", 4)
        self.ranges = [(SYST, SYST + 0x10), (NVIC, NVIC + 0x400)]
        self.enabled = self.pending = self.levels = 0
        self.csr = self.rvr = 0
        self.systick_pending = False
        self.deadline = None

    def _bank(self, address, base):
        return (address - base) // 4 if base <= address < base + 4 * ((self.lines + 31) // 32) else None

    def read(self, address, size):
        if address == SYST:
            value, self.csr = self.csr, self.csr & ~(1 << 16)     # countflag clears on read
            return value
        if address == SYST + 4:
            return self.rvr
        if address == SYST + 8:
            if self.deadline is None:
                return 0
            return max(0, (self.deadline - self.system.clock) // self.rate) & 0xFFFFFF
        if address == SYST + 12:
            return 0
        for base, which in ((NVIC, "enabled"), (NVIC + 0x80, "enabled"), (NVIC + 0x100, "pending"),
                            (NVIC + 0x180, "pending")):
            bank = self._bank(address, base)
            if bank is not None:
                return getattr(self, which) >> (32 * bank) & 0xFFFFFFFF
        return None

    def write(self, address, size, value):
        if address == SYST:
            self.csr = (self.csr & (1 << 16)) | (value & 7)
            self.deadline = self.system.clock + (self.rvr + 1) * self.rate if value & 1 else None
        elif address == SYST + 4:
            self.rvr = value & 0xFFFFFF
        elif address == SYST + 8:
            self.csr &= ~(1 << 16)
            if self.csr & 1:
                self.deadline = self.system.clock + (self.rvr + 1) * self.rate
        else:
            for base, op in ((NVIC, "set enabled"), (NVIC + 0x80, "clear enabled"), (NVIC + 0x100, "set pending"),
                             (NVIC + 0x180, "clear pending")):
                bank = self._bank(address, base)
                if bank is not None:
                    verb, which = op.split()
                    bits = (value & 0xFFFFFFFF) << (32 * bank)
                    current = getattr(self, which)
                    setattr(self, which, current | bits if verb == "set" else current & ~bits)
                    break
        self.system.interrupts_changed()

    def next_event(self):
        return self.deadline

    def event(self, clock):
        self.csr |= 1 << 16
        if self.csr & 2:
            self.systick_pending = True
            self.system.interrupts_changed()
        self.deadline += (self.rvr + 1) * self.rate

    def line(self, number, level):
        bit = 1 << number
        self.levels = self.levels | bit if level else self.levels & ~bit
        if level:
            self.pending |= bit

    def pending_interrupt(self):
        if self.systick_pending:
            return 15
        live = self.pending & self.enabled
        return 16 + (live & -live).bit_length() - 1 if live else None

    def taken(self, number):
        if number == 15:
            self.systick_pending = False
        elif number >= 16:
            self.pending &= ~(1 << (number - 16))

    def returned(self):
        self.pending |= self.levels         # a line still high pends its interrupt again
