"""A down-counting timer: a load register, a control register (run, interrupt), a status register
(expired; writing the bit clears it) and the current count.

    [models.timer]
    kind = "timer"
    base = 0x80001000
    count = 0x0         # offsets of the registers
    load = 0x4
    ctrl = 0x8
    stat = 0xc
    run = 0x1           # control bits: count, raise the interrupt line when it expires
    irq_enable = 0x2
    expired = 0x1       # status bit, cleared by writing it
    rate = 16           # shared-clock units per count
    irq = 0             # the line it raises (an interrupt controller model delivers it)
    periodic = true     # reload from the load register when it expires (else stop)
    size = 0x10

Time is the system run's shared clock, so the timer expires at corresponding places in the original
and the rebuilt image. The interrupt line is a level: high while the status bit is set and the
interrupt is enabled. Every read but the count's (which moves with the clock) is plain
(dread.models), and every write but those of the control and the status register (which set the
interrupt line) is idempotent. Assumed: counting starts when run is set, from the load value;
writing the load register while running takes effect at the next reload; no prescaler.
"""
from dread.models import Model, without


class Timer(Model):
    kind = "timer"

    def __init__(self, system, name, config):
        super().__init__(system, name, config)
        base = self.number("base")
        self.at = {k: base + self.number(k, d) for k, d in (("count", 0), ("load", 4), ("ctrl", 8), ("stat", 12))}
        self.run_bit, self.irq_bit = self.number("run", 1), self.number("irq_enable", 2)
        self.expired_bit, self.rate = self.number("expired", 1), self.number("rate", 16)
        self.irq = self.config.get("irq")
        self.periodic = bool(self.config.get("periodic", True))
        self.ranges = [(base, base + self.number("size", 0x10))]
        self.plain = without(self.ranges, [(self.at["count"], self.at["count"] + 1)])
        self.idempotent = without(self.ranges, [(self.at[k], self.at[k] + 1) for k in ("ctrl", "stat")])
        self.load_value, self.ctrl, self.expired = 0, 0, False
        self.started = self.deadline = None

    def read(self, address, size):
        if address == self.at["count"]:
            if self.deadline is None:
                return 0
            return max(0, (self.deadline - self.system.clock) // self.rate)
        if address == self.at["stat"]:
            return self.expired_bit if self.expired else 0
        return None

    def write(self, address, size, value):
        if address == self.at["load"]:
            self.load_value = value
        elif address == self.at["ctrl"]:
            was = self.ctrl & self.run_bit
            self.ctrl = value
            if value & self.run_bit and not was:
                self.deadline = self.system.clock + max(1, self.load_value) * self.rate
            elif not value & self.run_bit:
                self.deadline = None
            self._line()
        elif address == self.at["stat"] and value & self.expired_bit:
            self.expired = False
            self._line()

    def next_event(self):
        return self.deadline

    def event(self, clock):
        self.expired = True
        if self.periodic and self.ctrl & self.run_bit:
            self.deadline += max(1, self.load_value) * self.rate
        else:
            self.deadline = None
        self._line()

    def _line(self):
        if self.irq is not None:
            self.system.interrupt_level(int(self.irq), self.expired and bool(self.ctrl & self.irq_bit))

    def report(self):
        return {}
