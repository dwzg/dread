"""A PC's high precision event timer (HPET): a main counter that runs with the shared clock and timers
that compare it and raise interrupt lines.

    [models.hpet]
    kind = "hpet"
    base = 0xfed00000
    timers = 3             # timers 0 to timers - 1
    rate = 4               # shared-clock units per count
    period = 69841279      # femtoseconds per count, as the capabilities say (the Quark's 14.318 MHz)

Registers (64 bits wide, read and written as two words): the capabilities and identification at
0x000 (revision 1, the number of timers less one at bits 8 to 12, a 64-bit counter, legacy routing,
vendor 0x8086 at bits 16 to 31, the period at bits 32 to 63), the general configuration at 0x010 (bit
0 runs the counter, bit 1 legacy routing), the interrupt status at 0x020 (a timer's bit while its
level-triggered interrupt is active; a 1 written clears it), the main counter at 0x0f0, and per timer
n at 0x100 + 0x20 * n its configuration (bit 1 level-triggered, bit 2 enabled, bit 3 periodic, bit 6
the next comparator write sets the comparator, bit 8 32-bit mode, bits 9 to 13 the route; periodic
and 64-bit capable, any route allowed) and its comparator at 0x108 + 0x20 * n.

The counter counts while bit 0 is set (a write of it, done while it stands, sets it). A timer fires
when the counter reaches its comparator: enabled, it raises its line (routed: legacy routing takes
timer 0 to line 2 and timer 1 to line 8, as an I/O APIC's inputs; else the route), a pulse for an
edge-triggered one, held with its status bit until the status is cleared for a level-triggered one.
Periodic, it adds its period to the comparator: a comparator write while bit 6 is set sets the
comparator (bit 6 clears), the next one the period, as Linux and the HPET specification do it; a
one-shot timer's write sets the comparator. FreeRTOS's IA32 demo checks the vendor at its start
(HPET.c: configASSERT) and runs its timers 0 and 1 periodically. Every read but the counter's and
the status's is plain; no write is idempotent. A timer not enabled fires nothing (its status bit stays
clear). Not modelled: the FSB interrupt, the counter's wrap.
"""
from dread.models import Model, without

CAPS, CONF, STATUS, COUNTER, TIMERS = 0x000, 0x010, 0x020, 0x0F0, 0x100
ENABLE, LEGACY = 1, 2
T_LEVEL, T_ENABLE, T_PERIODIC, T_SETVAL, T_32BIT = 1 << 1, 1 << 2, 1 << 3, 1 << 6, 1 << 8
T_FIXED = 1 << 4 | 1 << 5                   # periodic capable, 64 bits wide (read-only)


class Timer:
    def __init__(self):
        self.conf, self.comparator, self.period, self.setval = 0, 0xFFFFFFFFFFFFFFFF, 0, False
        self.due = None                     # the shared clock of its next firing, or None


class Hpet(Model):
    kind = "hpet"

    def __init__(self, system, name, config):
        super().__init__(system, name, config)
        self.base = self.number("base", 0xFED00000)
        self.timers = [Timer() for _ in range(max(1, min(32, self.number("timers", 3))))]
        self.rate = max(1, self.number("rate", 4))
        self.period = self.number("period", 69841279) & 0xFFFFFFFF
        self.ranges = [(self.base, self.base + 0x400)]
        self.plain = without(self.ranges, [(self.base + COUNTER, self.base + COUNTER + 8),
                                           (self.base + STATUS, self.base + STATUS + 8)])
        self.conf, self.status = 0, 0
        self.offset, self.since = 0, None   # counter = offset + (clock - since) // rate while it runs

    # ---- time ----

    def counter(self):
        if self.since is None:
            return self.offset
        return (self.offset + (self.system.clock - self.since) // self.rate) & 0xFFFFFFFFFFFFFFFF

    def _line(self, n):
        if self.conf & LEGACY and n < 2:
            return (2, 8)[n]
        return (self.timers[n].conf >> 9) & 0x1F

    def _arm(self):
        """Each timer's next firing on the shared clock, from the counter now."""
        now = self.counter()
        for t in self.timers:
            t.due = None
            if self.since is None or not t.conf & T_ENABLE:
                continue                    # (a timer not enabled raises nothing: no event for it)
            mask = 0xFFFFFFFF if t.conf & T_32BIT else 0xFFFFFFFFFFFFFFFF
            ahead = (t.comparator - now) & mask
            if ahead or t.conf & T_PERIODIC:
                t.due = self.system.clock + (ahead or mask + 1) * self.rate - (self.system.clock - self.since) % self.rate

    def next_event(self):
        dues = [t.due for t in self.timers if t.due is not None]
        return min(dues) if dues else None

    def event(self, clock):
        for n, t in enumerate(self.timers):
            if t.due is None or t.due > clock:
                continue
            if t.conf & T_PERIODIC and t.period:
                mask = 0xFFFFFFFF if t.conf & T_32BIT else 0xFFFFFFFFFFFFFFFF
                t.comparator = (t.comparator + t.period) & mask
            if t.conf & T_ENABLE:
                line = self._line(n)
                if t.conf & T_LEVEL:
                    self.status |= 1 << n
                    self.system.interrupt_level(line, True)
                else:
                    self.system.interrupt_level(line, True)
                    self.system.interrupt_level(line, False)
        self._arm()
        for t in self.timers:
            if t.due is not None and t.due <= clock and not t.conf & T_PERIODIC:
                t.due = None                # (a one-shot fires once: not again at the same clock)

    # ---- registers ----

    def _register(self, address):
        """(name, timer, byte offset in the 64-bit register) of an address, or None."""
        at = address - self.base
        reg, shift = at & ~7, at & 7
        if reg in (CAPS, CONF, STATUS, COUNTER):
            return reg, None, shift
        n, field = (reg - TIMERS) // 0x20, (reg - TIMERS) % 0x20
        if reg >= TIMERS and n < len(self.timers) and field in (0x00, 0x08):
            return field, n, shift
        return None

    def _value(self, reg, n):
        if n is not None:
            t = self.timers[n]
            if reg == 0x00:
                return t.conf & ~T_SETVAL | T_FIXED | 0xFFFFFFFF << 32
            return t.comparator
        if reg == CAPS:
            return 1 | (len(self.timers) - 1) << 8 | 1 << 13 | 1 << 15 | 0x8086 << 16 | self.period << 32
        if reg == CONF:
            return self.conf
        if reg == STATUS:
            return self.status
        return self.counter()

    def read(self, address, size):
        reg = self._register(address)
        if reg is None:
            return None
        name, n, shift = reg
        return (self._value(name, n) >> (8 * shift)) & ((1 << (8 * size)) - 1)

    def write(self, address, size, value):
        reg = self._register(address)
        if reg is None:
            return
        name, n, shift = reg
        mask = ((1 << (8 * size)) - 1) << (8 * shift)
        new = (self._value(name, n) & ~mask) | ((value << (8 * shift)) & mask)
        if n is not None:
            t = self.timers[n]
            if name == 0x00:
                t.conf = new & 0xFFFF & ~T_FIXED
                if new & T_SETVAL:
                    t.setval = True
            elif t.conf & T_PERIODIC and not t.setval:
                # (periodic: a write after the comparator's sets the period, its own bytes merged)
                t.period = (t.period & ~mask | (value << (8 * shift)) & mask) & 0xFFFFFFFFFFFFFFFF
            else:
                t.comparator = new & 0xFFFFFFFFFFFFFFFF
                t.setval = False            # (bit 6 clears with the comparator's write: the demo sets
                                            # it again in front of the other half)
        elif name == CONF:
            was = self.conf & ENABLE
            if was and not new & ENABLE:
                self.offset, self.since = self.counter(), None
            elif not was and new & ENABLE:
                self.since = self.system.clock
            self.conf = new & (ENABLE | LEGACY)
        elif name == STATUS:
            cleared = self.status & (value << (8 * shift)) & mask
            self.status &= ~cleared
            for k in range(len(self.timers)):
                if cleared >> k & 1:
                    self.system.interrupt_level(self._line(k), False)
        elif name == COUNTER:
            self.offset = new & 0xFFFFFFFFFFFFFFFF
            if self.since is not None:
                self.since = self.system.clock
        self._arm()
        self.system.interrupts_changed()
