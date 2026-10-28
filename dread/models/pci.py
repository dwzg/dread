"""A PCI configuration space: the words of a regs model (dread.models.regs), read and written both in
memory (the enhanced mechanism, a range where bus, device, function and register make the offset)
and through the ports 0xcf8 and 0xcfc (configuration mechanism 1: an address written to 0xcf8, the
word read or written at 0xcfc to 0xcff).

    [models.pci]
    kind = "pci"
    base = 0xe0000000      # the memory range of bus 0 and up: offset = bus << 20 | device << 15 |
    size = 0x100000        #   function << 12 | register
    default = 0xffffffff   # what every other word reads (no device there)
    words = {0xa5010 = 0x9000b000, 0xf8044 = 0x80001080}    # offsets as in memory: 32-bit words
    ports = true           # 0xcf8 and 0xcfc (the default); false: memory alone

Through the ports, an address with bit 31 clear reads 0xffffffff and takes no write, and the address
register at 0xcf8 reads back as written. FreeRTOS's IA32 demo for the Galileo reads its UART's base
address in memory (0xe00a5010: bus 0, device 20, function 5, BAR0) and the legacy bridge's GPIO base
through the ports (0x8000f844: device 31, function 0, register 0x44). Assumed as for regs: no register
with a side effect, no sizing of a base address.
"""
from dread.models.regs import Regs

ADDRESS, DATA = 0xCF8, 0xCFC


class Pci(Regs):
    kind = "pci"

    def __init__(self, system, name, config):
        super().__init__(system, name, config)
        self.address = 0
        self.ports = [(ADDRESS, DATA + 4)] if self.config.get("ports", True) else []

    def _offset(self, port):
        """The memory offset the ports' address selects for a data port, or None (bit 31 clear)."""
        a = self.address
        if not a & 0x80000000:
            return None
        return ((a >> 16) & 0xFF) << 20 | ((a >> 11) & 0x1F) << 15 | ((a >> 8) & 7) << 12 | (a & 0xFC) | (port - DATA)

    def port_read(self, port, size):
        mask = (1 << 8 * size) - 1
        if port < DATA:
            return (self.address >> 8 * (port - ADDRESS)) & mask
        offset = self._offset(port)
        if offset is None or not offset < self.ranges[0][1] - self.base:
            return mask
        return self.read(self.base + offset, size)

    def port_write(self, port, size, value):
        if port < DATA:
            shift = 8 * (port - ADDRESS)
            mask = ((1 << 8 * size) - 1) << shift
            self.address = (self.address & ~mask | (value << shift) & mask) & 0xFFFFFFFF
            return
        offset = self._offset(port)
        if offset is not None and offset < self.ranges[0][1] - self.base:
            self.write(self.base + offset, size, value)
