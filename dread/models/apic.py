"""A PC's local APIC and I/O APIC as one interrupt controller: the vectors the core takes through its
IDT (dread.arch.x86 enter_interrupt), from the local APIC's timer and from the lines devices raise
(dread.sysrun interrupt_level), routed by the I/O APIC's redirection table.

    [models.apic]
    kind = "apic"
    base = 0xfee00000      # the local APIC's registers (4 KiB)
    ioapic = 0xfec00000    # the I/O APIC's (its select register, its window at + 0x10); optional
    lines = 24             # the I/O APIC's inputs
    rate = 4               # shared-clock units per count of the timer before its divider

Local APIC (offsets): the ID at 0x020 (0), the version at 0x030 (0x14, five LVT entries), the task
priority at 0x080, end of interrupt at 0x0b0, the spurious vector at 0x0f0 (bit 8 enables the APIC:
nothing is delivered while it is clear), the in-service (0x100), trigger (0x180) and request (0x200)
registers, eight words each, the error status at 0x280, the LVT entries for the timer (0x320; bits 0
to 7 the vector, bit 16 masked, bit 17 periodic), thermal, performance, LINT0, LINT1 and error (0x330
to 0x370), the timer's initial count (0x380), its current count (0x390, read: what is left) and its
divider (0x3e0: bits 0, 1 and 3, by 2 to 128 or 1). I/O APIC: a write to the select register picks a
register, the window reads and writes it: the ID (0), the version (1: 0x20, lines - 1 at bits 16 to
23) and the redirection entries at 0x10 + 2 * n (low: bits 0 to 7 the vector, bit 15 level-triggered,
bit 16 masked; high: the destination, kept).

The timer counts down from its initial count (a write starts it, 0 stops it) every rate * divider
units of the shared clock; at 0 it requests its vector unless masked, and periodic it starts again.
An input of the I/O APIC requests its entry's vector on a rising edge, or while it is high for a
level-triggered one (again behind its end of interrupt). The core takes the highest requested vector
whose priority class (vector >> 4) lies above the task priority's and above every vector in service,
while eflags.IF is set (the layer's interrupts_enabled); taking it moves it from requested to in
service, and end of interrupt ends the highest in service. FreeRTOS's IA32 port ticks on the timer's
vector 0x21, raises the task priority to 0xaf in its critical sections and yields with int 0x20 (no
APIC: a software interrupt). Every read but the current count's is plain; no write is idempotent.
Not modelled: other destinations than the one core, delivery modes other than fixed, the error
interrupt's causes, logical destinations, the thermal and performance sources.
"""
from dread.models import Model, without

ID, VERSION, TPR, EOI, SVR, ISR, TMR, IRR, ESR = 0x020, 0x030, 0x080, 0x0B0, 0x0F0, 0x100, 0x180, 0x200, 0x280
LVT_TIMER, LVT_LAST, INITIAL, CURRENT, DIVIDE = 0x320, 0x370, 0x380, 0x390, 0x3E0
MASKED, PERIODIC, LEVEL = 1 << 16, 1 << 17, 1 << 15
DIVIDERS = {0: 2, 1: 4, 2: 8, 3: 16, 8: 32, 9: 64, 10: 128, 11: 1}


class Apic(Model):
    kind = "apic"

    def __init__(self, system, name, config):
        super().__init__(system, name, config)
        self.base = self.number("base", 0xFEE00000)
        self.ioapic = self.number("ioapic") if self.config.get("ioapic") is not None else None
        self.rate = max(1, self.number("rate", 4))
        self.nlines = max(1, min(240, self.number("lines", 24)))
        self.ranges = [(self.base, self.base + 0x1000)]
        if self.ioapic is not None:
            self.ranges.append((self.ioapic, self.ioapic + 0x20))
        self.plain = without(self.ranges, [(self.base + CURRENT, self.base + CURRENT + 4)])
        self.regs = {}                      # local APIC registers kept as written (offset -> word)
        self.lvt = {k: MASKED for k in range(LVT_TIMER, LVT_LAST + 1, 0x10)}
        self.tpr, self.svr, self.esr, self.divide = 0, 0xFF, 0, 0
        self.irr, self.isr = set(), []      # requested vectors; vectors in service, the latest last
        self.initial, self.started, self.due = 0, None, None
        self.select, self.entries = 0, [MASKED] * self.nlines
        self.high = [0] * self.nlines
        self.levels = [False] * self.nlines

    # ---- the timer ----

    def _period(self):
        return self.rate * DIVIDERS.get(self.divide & 0xB, 2)

    def _current(self):
        if self.started is None or not self.initial:
            return 0
        elapsed = (self.system.clock - self.started) // self._period()
        if self.lvt[LVT_TIMER] & PERIODIC:
            return self.initial - elapsed % self.initial
        return max(0, self.initial - elapsed)

    def _arm(self):
        self.due = None if self.started is None or not self.initial else self.started + self.initial * self._period()

    def next_event(self):
        return self.due

    def event(self, clock):
        if self.due is None or self.due > clock:
            return
        if not self.lvt[LVT_TIMER] & MASKED:
            self._request(self.lvt[LVT_TIMER] & 0xFF)
        if self.lvt[LVT_TIMER] & PERIODIC:
            self.started = self.due
            self._arm()
        else:
            self.started, self.due = None, None

    # ---- requests and delivery ----

    def _request(self, vector):
        if vector >= 16:
            self.irr.add(vector)
            self.system.interrupts_changed()

    def line(self, number, level):
        if not 0 <= number < self.nlines:
            return
        was, self.levels[number] = self.levels[number], level
        entry = self.entries[number]
        if entry & MASKED or not level:
            return
        if entry & LEVEL or not was:        # (level: while high; edge: on the rising edge)
            self._request(entry & 0xFF)

    def pending_interrupt(self):
        if not self.svr & 0x100 or not self.irr:
            return None
        vector = max(self.irr)
        floor = max([self.tpr >> 4] + [v >> 4 for v in self.isr])
        return vector if vector >> 4 > floor else None

    def taken(self, number):
        self.irr.discard(number)
        self.isr.append(number)
        self.system.interrupts_changed()

    def returned(self):
        """An exception return (iret runs in the emulator: end of interrupt is the APIC's register)."""

    def core_cause(self, number):
        """The vector the core takes through its IDT: the model's numbers are vectors."""
        return number

    def _eoi(self):
        if not self.isr:
            return
        vector = max(self.isr)
        self.isr.remove(vector)
        for n, entry in enumerate(self.entries):    # (a level-triggered input still high asks again)
            if entry & LEVEL and not entry & MASKED and self.levels[n] and entry & 0xFF == vector:
                self._request(vector)
        self.system.interrupts_changed()

    # ---- registers ----

    def _bits(self, vectors, word):
        return sum(1 << (v - 32 * word) for v in vectors if v // 32 == word)

    def _local(self, at):
        if at == ID:
            return 0
        if at == VERSION:
            return 0x00050014
        if at == TPR:
            return self.tpr
        if at == SVR:
            return self.svr
        if at == ESR:
            return self.esr
        if ISR <= at < ISR + 0x80:
            return self._bits(self.isr, (at - ISR) // 0x10)
        if TMR <= at < TMR + 0x80:
            return 0
        if IRR <= at < IRR + 0x80:
            return self._bits(self.irr, (at - IRR) // 0x10)
        if at in self.lvt:
            return self.lvt[at]
        if at == INITIAL:
            return self.initial
        if at == CURRENT:
            return self._current()
        if at == DIVIDE:
            return self.divide
        return self.regs.get(at, 0)

    def read(self, address, size):
        if self.ioapic is not None and self.ioapic <= address < self.ioapic + 0x20:
            at = address - self.ioapic
            if at == 0x00:
                return self.select & 0xFF
            if 0x10 <= at < 0x14:
                return (self._io_register(self.select) >> 8 * (at - 0x10)) & ((1 << 8 * size) - 1)
            return 0
        at = address - self.base
        word, shift = at & ~3, (at & 3) * 8
        return (self._local(word) >> shift) & ((1 << 8 * size) - 1)

    def _io_register(self, index):
        if index == 0:
            return 0
        if index == 1:
            return 0x20 | (self.nlines - 1) << 16
        if 0x10 <= index < 0x10 + 2 * self.nlines:
            n = (index - 0x10) // 2
            return self.high[n] if index & 1 else self.entries[n]
        return 0

    def write(self, address, size, value):
        if self.ioapic is not None and self.ioapic <= address < self.ioapic + 0x20:
            at = address - self.ioapic
            if at == 0x00:
                self.select = value & 0xFF
            elif at == 0x10 and 0x10 <= self.select < 0x10 + 2 * self.nlines:
                n = (self.select - 0x10) // 2
                if self.select & 1:
                    self.high[n] = value & 0xFFFFFFFF
                else:
                    self.entries[n] = value & 0x1FFFF
                    self.line(n, self.levels[n])    # (unmasked while high: a level-triggered one asks)
            self.system.interrupts_changed()
            return
        at = (address - self.base) & ~3
        if at == TPR:
            self.tpr = value & 0xFF
        elif at == EOI:
            self._eoi()
        elif at == SVR:
            self.svr = value & 0x3FF
        elif at == ESR:
            self.esr = 0                    # (a write latches the errors: none are modelled)
        elif at in self.lvt:
            self.lvt[at] = value & (0x700FF | MASKED | PERIODIC)
        elif at == INITIAL:
            self.initial = value & 0xFFFFFFFF
            self.started = self.system.clock if self.initial else None
            self._arm()
        elif at == DIVIDE:
            self.divide = value & 0xB
            self._arm()
        else:
            self.regs[at] = value & 0xFFFFFFFF
        self.system.interrupts_changed()
