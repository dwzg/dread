"""Registers that answer configured words: identification and capability registers, a PCI
configuration space (the devices' identifiers and base addresses), whatever a firmware reads to find
its devices and does not expect to change.

    [models.pci]
    kind = "regs"
    base = 0xe0000000      # the registers
    size = 0x100000        # the bytes of register space the model owns
    words = {0xa5010 = 0x9000b000, 0xaa000 = 0x09348086}    # offsets from base: 32-bit words
    default = 0xffffffff   # what every other word reads (PCI: no device there); 0 when left out

A read of one, two or four bytes takes those bytes of the word at the aligned offset below it
(little-endian), a store is kept and read back so (a configuration space's base address written, a
command register), and a store never does anything else. FreeRTOS's IA32 demo for the Galileo reads
its UART's registers' base address from the Quark's configuration space this way (0xe00a5010: bus 0,
device 20, function 5, BAR0). Every read is plain and every write idempotent (dread.models): the
values change through stores alone. Assumed: no register with a side effect, none that moves with
the clock, no sizing of a base address (a store of 0xffffffff reads back as written).
"""
from dread.models import Model


class Regs(Model):
    kind = "regs"

    def __init__(self, system, name, config):
        super().__init__(system, name, config)
        self.base = self.number("base")
        size = self.number("size")
        self.ranges = [(self.base, self.base + size)]
        self.plain = list(self.ranges)
        self.idempotent = list(self.ranges)
        self.default = self.number("default", 0) & 0xFFFFFFFF
        self.words = {}
        for key, value in (self.config.get("words") or {}).items():
            offset = int(key, 0) if isinstance(key, str) else int(key)
            if offset % 4 or not 0 <= offset < size:
                raise ValueError("model %s: word offset 0x%x is not 4-aligned inside its 0x%x bytes"
                                 % (name, offset, size))
            self.words[offset] = int(value) & 0xFFFFFFFF

    def _word(self, offset):
        return self.words.get(offset, self.default)

    def read(self, address, size):
        offset = address - self.base
        aligned, shift = offset & ~3, (offset & 3) * 8
        value = self._word(aligned) | (self._word(aligned + 4) << 32 if (offset & 3) + size > 4 else 0)
        return (value >> shift) & ((1 << 8 * size) - 1)

    def write(self, address, size, value):
        offset = address - self.base
        for k in range(size):               # (byte by byte: a narrow store keeps the word's other bytes)
            at = offset + k
            aligned, shift = at & ~3, (at & 3) * 8
            byte = (value >> 8 * k) & 0xFF
            self.words[aligned] = (self._word(aligned) & ~(0xFF << shift) | byte << shift) & 0xFFFFFFFF
