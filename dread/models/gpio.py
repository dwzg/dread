"""General-purpose pins: an input register (keys, switches) and an output register (LEDs, a beeper).

    [models.gpio]
    kind = "gpio"
    base = 0x80003000
    input = 0x0         # offsets: the pins the firmware reads
    output = 0x4        #          the pins it drives
    size = 0x10

A scenario sets the inputs ([[clock, "gpio", 5]]: pins 0 and 2 high from then on); every store to
the output register is noted in the device log with the value. Assumed: no direction or pull
registers, outputs not read back from the pins.
"""
from dread.models import Model


class Gpio(Model):
    kind = "gpio"

    def __init__(self, system, name, config):
        super().__init__(system, name, config)
        base = self.number("base")
        self.input_at, self.output_at = base + self.number("input", 0), base + self.number("output", 4)
        self.ranges = [(base, base + self.number("size", 0x10))]
        self.pins_in, self.pins_out = 0, 0

    def read(self, address, size):
        if address == self.input_at:
            return self.pins_in
        return None

    def write(self, address, size, value):
        if address == self.output_at:
            self.pins_out = value
            self.system.note(self.name, "pins", value)

    def input(self, data):
        self.pins_in = int(data, 0) if isinstance(data, str) else int(data)

    def report(self):
        return {"pins": self.pins_out}
