"""A PC's programmable interval timer (an 8254 at ports 0x40 to 0x43) and the system control port at
0x61, as far as a firmware uses them to time a delay: a counter in mode 0 or 1, counted against the
shared clock, its output read at port 0x61.

    [models.pit]
    kind = "pit"
    rate = 4            # shared-clock units per count (the 8254 counts at 1.193182 MHz)

Ports: the counters at 0x40, 0x41 and 0x42, the control word at 0x43 (counter, access, mode, binary
or BCD), and port 0x61, whose bit 0 is counter 2's gate and bit 5 counter 2's output (bits 0 to 3 as
written, bit 4 0). A count is written low byte then high byte (access 3), or one of them (1, 2);
access 0 latches the count, read back the same way. Mode 0: the output goes low with the control word
and high when the count written runs down (the count starts once written, while the gate is high).
Mode 1: the output is high until the gate rises, then low until the count runs down from there (the
gate's rising edge starts it again). FreeRTOS's IA32 demo times its calibration so (uiCalibrateTimer:
counter 2 in mode 1, 0x2e9c counts, about 10 ms, its output polled at port 0x61). The count runs down
with the shared clock, so both images see it alike. Not modelled: modes 2 to 5, BCD, the counters'
interrupt (counter 0's IRQ 0), port 0x61's refresh toggle and speaker.
"""
from dread.models import Model


class Counter:
    def __init__(self):
        self.mode, self.access, self.count = 0, 3, 0x10000
        self.ctrl = False                   # a control word was written
        self.low = None                     # the low byte written, waiting for the high one (access 3)
        self.start = None                   # the shared clock at which the count began, or None
        self.armed = False                  # mode 0: a count was written; mode 1: armed for the gate
        self.latched, self.read_high = None, False


class Pit(Model):
    kind = "pit"

    def __init__(self, system, name, config):
        super().__init__(system, name, config)
        self.base = self.number("base", 0x40)
        self.control_port = self.number("control_port", 0x61)
        self.rate = max(1, self.number("rate", 4))
        self.ports = [(self.base, self.base + 4), (self.control_port, self.control_port + 1)]
        self.counters = [Counter() for _ in range(3)]
        self.port61 = 0

    # ---- time ----

    def _running(self, c):
        """The counts left of counter c, or None while it does not count."""
        if c.start is None:
            return None
        return c.count - (self.system.clock - c.start) // self.rate

    def _out(self, k):
        """Counter k's output. Mode 0: low from the control word until the count runs down; mode 1:
        high, low from the gate's rising edge until the count runs down."""
        c = self.counters[k]
        left = self._running(c)
        if c.mode == 0:
            return 1 if not c.ctrl or (left is not None and left <= 0) else 0
        if c.mode == 1:
            return 0 if left is not None and left > 0 else 1
        return 1

    def _gate(self, k):
        return bool(self.port61 & 1) if k == 2 else True

    # ---- ports ----

    def port_read(self, port, size):
        if port == self.control_port:
            return (self.port61 & 0x0F) | (self._out(2) << 5)
        k = port - self.base
        if k == 3:
            return 0
        c = self.counters[k]
        value = c.latched
        if value is None:
            left = self._running(c)
            value = c.count if left is None else max(0, left)
        value &= 0xFFFF
        if c.access == 1:
            return value & 0xFF
        if c.access == 2:
            return value >> 8
        byte = value >> 8 if c.read_high else value & 0xFF
        c.read_high = not c.read_high
        if not c.read_high:
            c.latched = None                # (both bytes read: the latch is free again)
        return byte

    def port_write(self, port, size, value):
        value &= 0xFF
        if port == self.control_port:
            rising = not (self.port61 & 1) and value & 1
            self.port61 = value & 0x0F
            c = self.counters[2]
            if rising and c.mode == 1 and c.armed:
                c.start = self.system.clock     # (the gate's rising edge starts the one-shot)
            return
        k = port - self.base
        if k == 3:
            sel, access, mode = value >> 6, (value >> 4) & 3, (value >> 1) & 7
            if sel == 3:
                return                      # (the read-back command: not modelled)
            c = self.counters[sel]
            if access == 0:                 # latch the count
                left = self._running(c)
                c.latched, c.read_high = (c.count if left is None else max(0, left)) & 0xFFFF, False
                return
            c.access, c.mode, c.low, c.start, c.armed, c.ctrl = access, mode, None, None, False, True
            c.latched, c.read_high = None, False
            return
        c = self.counters[k]
        if c.access == 3 and c.low is None:
            c.low = value
            return
        if c.access == 3:
            count = c.low | value << 8
        elif c.access == 1:
            count = value
        else:
            count = value << 8
        c.low, c.count, c.armed = None, count or 0x10000, True
        if c.mode == 0 and self._gate(k):
            c.start = self.system.clock     # (mode 0 counts once the count is written)
        elif c.mode == 1:
            c.start = None                  # (mode 1 waits for the gate)

    def report(self):
        return {}
