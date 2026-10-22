"""A serial port: a data register, a status register with a "can send" and a "byte arrived" bit.

    [models.uart]
    kind = "uart"
    base = 0x80000000      # the registers
    data = 0x0             # offsets: the data register (a store sends, a load takes the next byte)
    status = 0x4           #          the status register
    tx_ready = 0x1         # status bits: can send (always set: sending takes no time),
    rx_ready = 0x2         #              a byte is waiting (while the receive queue is not empty)
    size = 0x10            # the bytes of register space the model owns
    irq = 3                # optional: the interrupt raised while a byte waits (an interrupt model
                           # must deliver it)

A store to the data register sends its low byte: the bytes sent are the model's report, the text a
scenario can wait for. A load takes the next byte of the receive queue (0 when it is empty). Reads
of other registers in the range are plain memory. Every read but the data register's is plain
(dread.models), and every write but the data register's (it sends) is idempotent. Assumed: no
transmit delay, no overrun, no other status bits.
"""
from dread.models import Model, without


class Uart(Model):
    kind = "uart"

    def __init__(self, system, name, config):
        super().__init__(system, name, config)
        self.base = self.number("base")
        self.data_at = self.base + self.number("data", 0)
        self.status_at = self.base + self.number("status", 4)
        self.tx_ready, self.rx_ready = self.number("tx_ready", 1), self.number("rx_ready", 2)
        self.irq = self.config.get("irq")
        self.ranges = [(self.base, self.base + self.number("size", 0x10))]
        self.plain = without(self.ranges, [(self.data_at, self.data_at + 1)])     # a read takes a byte
        self.idempotent = without(self.ranges, [(self.data_at, self.data_at + 1)])    # a write sends one
        self.sent, self.queue = bytearray(), bytearray()

    def read(self, address, size):
        if address == self.status_at:
            return self.tx_ready | (self.rx_ready if self.queue else 0)
        if address == self.data_at:
            if not self.queue:
                return 0
            byte = self.queue.pop(0)
            self._irq()
            return byte
        return None

    def write(self, address, size, value):
        if address == self.data_at:
            self.sent.append(value & 0xFF)
            self.system.note(self.name, "sent", value & 0xFF)

    def input(self, data):
        self.queue.extend(data.encode() if isinstance(data, str) else bytes(data))
        self._irq()

    def _irq(self):
        if self.irq is not None:
            self.system.interrupt_level(int(self.irq), bool(self.queue))

    def report(self):
        return {"sent": bytes(self.sent).decode("latin-1")}
