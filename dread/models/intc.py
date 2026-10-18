"""A simple interrupt controller for the A profile: a pending register, an enable register and an
acknowledge register; the core's IRQ is asked while a pending source is enabled.

    [models.intc]
    kind = "intc"
    base = 0x80002000
    pending = 0x0       # offsets: pending sources (read)
    enable = 0x4        #          one enable bit per source (read and written by the firmware)
    ack = 0x8           #          writing a source's bit clears its latched request
    size = 0x10

A source is pending while its line is high, and from a rising edge until it is acknowledged. The IRQ
is taken by the system run (the core's exception entry: vectors + 0x18) at a place where the core
has interrupts enabled. Assumed: one IRQ line to the core, no priorities, no FIQ.
"""
from dread.models import Model


class Intc(Model):
    kind = "intc"

    def __init__(self, system, name, config):
        super().__init__(system, name, config)
        base = self.number("base")
        self.at = {k: base + self.number(k, d) for k, d in (("pending", 0), ("enable", 4), ("ack", 8))}
        self.ranges = [(base, base + self.number("size", 0x10))]
        self.levels, self.latched, self.enabled = 0, 0, 0

    def read(self, address, size):
        if address == self.at["pending"]:
            return self.latched | self.levels
        if address == self.at["enable"]:
            return self.enabled
        return None

    def write(self, address, size, value):
        if address == self.at["enable"]:
            self.enabled = value
            self.system.interrupts_changed()
        elif address == self.at["ack"]:
            self.latched &= ~value

    def line(self, number, level):
        bit = 1 << number
        if level and not self.levels & bit:
            self.latched |= bit
        self.levels = self.levels | bit if level else self.levels & ~bit

    def pending_interrupt(self):
        return 0 if (self.latched | self.levels) & self.enabled else None

    def taken(self, number):
        """The core took the IRQ (the handler acknowledges the source itself)."""

    def returned(self):
        """An exception return (M profile only)."""
