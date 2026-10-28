"""A DesignWare I2C controller (the Quark's, behind PCI device 21, function 2, BAR0) with register-file
devices on its bus: as far as a firmware writes and reads device registers, as FreeRTOS's IA32 demo
does with the Galileo Gen 2's PCAL9555 port expanders (its LEDs).

    [models.i2c]
    kind = "dwi2c"
    base = 0x9000c000
    devices = [0x25, 0x26, 0x27]     # 7-bit addresses of register-file devices: 256 registers each, 0

Registers: IC_TAR at 0x04 (the target, bits 0 to 9); IC_DATA_CMD at 0x10 (a write: the byte in bits 0 to
7, bit 8 a read command, bit 9 a stop behind it; a read: the receive FIFO's next byte, 0 when empty);
IC_RAW_INTR_STAT at 0x34 (bit 9 stop detected, bit 6 transmit aborted); the clear registers, which
clear on a read and read 0 (0x40 all, 0x44 rx under, 0x48 rx over, 0x4c tx over, 0x54 tx abort, 0x60
stop, 0x64 start); IC_ENABLE at 0x6c (bit 0); IC_STATUS at 0x70 (transmit FIFO not full and empty,
always; bit 3 while the receive FIFO holds a byte); IC_RXFLR at 0x78 (its level); IC_ENABLE_STATUS at
0x9c (bit 0 as IC_ENABLE's). The others, IC_CON among them, are plain memory.

A command goes to the device at IC_TAR at once: the first byte written in a transfer (from the first
command behind a stop) selects the device's register, later ones write it and those behind it, a
read command reads the selected one into the receive FIFO and moves on; a stop ends the transfer and
sets the stop bit. A target no device answers aborts the transfer (the abort and the stop bits). The
devices' registers that are not 0 are the report. Every read is plain but IC_DATA_CMD's and the clear
registers' (they change the model); no write is idempotent. Not modelled: time (a transfer takes
none), FIFO overflows, interrupts, 10-bit addressing, a start without a stop between transfers.
"""
from dread.models import Model, without

TAR, DATA_CMD, RAW, ENABLE, STATUS, RXFLR, ENABLE_STATUS = 0x04, 0x10, 0x34, 0x6C, 0x70, 0x78, 0x9C
READ, STOP = 1 << 8, 1 << 9
RX_UNDER, RX_OVER, TX_OVER, TX_ABRT, STOP_DET, START_DET = 1 << 0, 1 << 1, 1 << 3, 1 << 6, 1 << 9, 1 << 10
CLEAR = {0x40: 0xFFFF, 0x44: RX_UNDER, 0x48: RX_OVER, 0x4C: TX_OVER, 0x54: TX_ABRT, 0x60: STOP_DET,
         0x64: START_DET}


class DwI2c(Model):
    kind = "dwi2c"

    def __init__(self, system, name, config):
        super().__init__(system, name, config)
        self.base = self.number("base")
        self.ranges = [(self.base, self.base + 0x100)]
        self.plain = without(self.ranges, [(self.base + DATA_CMD, self.base + DATA_CMD + 4)]
                             + [(self.base + k, self.base + k + 4) for k in CLEAR])
        self.devices = {int(a) & 0x7F: bytearray(256) for a in self.config.get("devices", ())}
        self.pointer = {a: 0 for a in self.devices}
        self.tar, self.enabled, self.raw, self.rx, self.fresh = 0, 0, 0, [], True

    def read(self, address, size):
        at = address - self.base
        if at == DATA_CMD:
            return self.rx.pop(0) if self.rx else 0
        if at in CLEAR:
            self.raw &= ~CLEAR[at]
            return 0
        value = {TAR: self.tar, RAW: self.raw, ENABLE: self.enabled, ENABLE_STATUS: self.enabled,
                 STATUS: 0x6 | (0x8 if self.rx else 0), RXFLR: len(self.rx)}.get(at)
        return None if value is None else value & ((1 << 8 * size) - 1)

    def write(self, address, size, value):
        at = address - self.base
        if at == TAR:
            self.tar = value & 0x3FF
        elif at == ENABLE:
            self.enabled = value & 1
        elif at == DATA_CMD:
            self._command(value)

    def _command(self, value):
        target = self.tar & 0x7F
        regs = self.devices.get(target)
        if regs is None:                    # (no device answers: the transfer aborts and stops)
            self.raw |= TX_ABRT | STOP_DET
            self.fresh = True
            return
        if value & READ:
            p = self.pointer[target]
            self.rx.append(regs[p])
            self.pointer[target] = (p + 1) & 0xFF
        elif self.fresh:
            self.pointer[target] = value & 0xFF
        else:
            p = self.pointer[target]
            regs[p] = value & 0xFF
            self.pointer[target] = (p + 1) & 0xFF
        self.fresh = False
        if value & STOP:
            self.raw |= STOP_DET
            self.fresh = True

    def report(self):
        return {"0x%02x" % a: {"0x%02x" % k: v for k, v in enumerate(regs) if v}
                for a, regs in sorted(self.devices.items())}
