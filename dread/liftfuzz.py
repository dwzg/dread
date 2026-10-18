"""dread liftfuzz: the lifter's instruction semantics, checked against the emulator.

The functions of a real image check the lifter (dread.lift) only as far as their cases go: a
planted mistake, eor translated as orr, passed every generated case of a gizmo function whose xor
loop the cases run once from zero. This builds functions out of random instructions instead,
assembles them into a small image of their own, lifts every one, links the lifted C over that image
and runs both images on many register values and buffer contents (dread test's comparison: the
result, every memory write). A difference, or a refusal, is a fault of the lifter.

Each function: push {r4-r11, lr}; a frame of FRAME bytes, every slot written; r4-r11 and the flags
set from the arguments, r12 the address of a buffer of random words; then LENGTH random
instructions: data processing with every operand form (immediates, shifts by constants and by
registers, rrx) and condition, flag setting or not; shifts; multiplies, long ones included; clz,
byte reversal, extends, bit fields, movw and movt; loads and stores of every width and addressing
mode (offsets, register offsets with shifts, writeback, post-index) on the buffer, doubleword and
block transfers, frame slots and addresses of them; forward branches on a bit of the buffer (so
that inputs go both ways; cbz and cbnz on Thumb) and on the flags; calls of three helpers (one takes
three registers, one returns r0 and r1, one takes a stack argument); push and pop around a few
instructions; IT blocks on Thumb-2; instructions that act on the core (interrupt masks, the flags through APSR,
barriers, an exclusive load and store). About a third of the functions keep a frame pointer (r7 on
Thumb, r11 in ARM state) that addresses the frame and restores sp at the end, and about a third
return the Thumb-1 way (pop {r3}; bx r3). At the end the registers are stored into a result buffer
and the flags with them, read back through conditional instructions, so that the comparison sees
them as memory.

A failing function is reduced (--reduce N: the first N): the shortest failing prefix of its units
(an instruction with its set-up lines, an IT block, a branch with what it jumps over), then every
unit whose absence keeps it failing is left out; what remains is printed. Found this way, on its
first runs: capstone reports adc, sbc and rsc without s as setting the flags, and the shifter's
carry of movs r1, r1, lsl #7 was taken after r1 was written. Its returns through r3 also made
dread.switches crash the process inside Unicorn (pages unmapped after a run, written by a later one).

The ISAs: a32 (ARMv7-A in ARM state) and t32 (Cortex-M3, Thumb-2). Needs the cross toolchain
(arm-none-eabi-). The image, its target directory and the lifted C go to a temporary directory,
kept when something fails (or with --keep).

    dread liftfuzz [--isa a32|t32] [--functions N] [--length L] [--inputs K] [--seed S] [--reduce N]
                   [--keep DIR]
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

FRAME = 32
BUF_WORDS = 96              # the buffer: words of random content, r12 points into its middle
CONDS = ["eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc", "hi", "ls", "ge", "lt", "gt", "le"]
OPPOSITE = {"eq": "ne", "ne": "eq", "cs": "cc", "cc": "cs", "mi": "pl", "pl": "mi", "vs": "vc", "vc": "vs",
            "hi": "ls", "ls": "hi", "ge": "lt", "lt": "ge", "gt": "le", "le": "gt"}
EDGES = [0, 1, 2, 31, 32, 33, 0x7F, 0x80, 0xFF, 0x100, 0x7FFF, 0x8000, 0xFFFF, 0x7FFFFFFF, 0x80000000,
         0x80000001, 0xFFFFFFFE, 0xFFFFFFFF]

ISAS = {
    "a32": {
        "flags": ["-march=armv7-a", "-marm", "-mfloat-abi=soft"],
        "toml": '''[target]
name = "liftfuzz-a32"
title = "dread liftfuzz, ARM state"

[arch]
name = "arm"
isa = "a32"
profile = "a"

[image.main]
original = "fuzz.bin"
base = 0x10000
limit = 0x80000
entry = "f0"

[memory]
regions = [
    {name = "flash", base = 0x0, size = 0x100000, kind = "rom"},
    {name = "ram", base = 0x40000000, size = 0x10000},
    {name = "scratch", base = 0x40100000, size = 0x20000},
]
io = [[0x80000000, 0x80010000]]
stack = {top = 0x40010000, size = 0x2000}
sentinel = 0x0fff0000
scratch = {base = 0x40100000, size = 0x20000}
''',
        "base": 0x10000, "ram": 0x40000000,
    },
    "t32": {
        "flags": ["-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft"],
        "toml": '''[target]
name = "liftfuzz-t32"
title = "dread liftfuzz, Thumb-2 (Cortex-M3)"

[arch]
name = "arm"
isa = "t32"
profile = "m"
cpu = "cortex-m3"

[image.main]
original = "fuzz.bin"
base = 0x08000000
limit = 0x08080000
entry = "f0"

[memory]
regions = [
    {name = "flash", base = 0x08000000, size = 0x100000, kind = "rom"},
    {name = "sram", base = 0x20000000, size = 0x10000},
    {name = "scratch", base = 0x20100000, size = 0x20000},
]
io = [[0x40000000, 0x40020000], [0xe000e000, 0xe000f000]]
stack = {top = 0x20010000, size = 0x2000}
sentinel = 0x1fff0000
scratch = {base = 0x20100000, size = 0x20000}
''',
        "base": 0x08000000, "ram": 0x20000000,
    },
}


def ror32(value, n):
    n &= 31
    return ((value >> n) | (value << (32 - n))) & 0xFFFFFFFF if n else value


class Generator:
    """Random functions of one ISA, as assembly lines."""

    def __init__(self, isa, rng):
        self.isa, self.rng = isa, rng
        self.thumb = isa == "t32"
        self.label = 0
        self.r12 = ISAS[isa]["ram"] + 0x1000 + 4 * (BUF_WORDS // 2)
        self.fp = None              # the frame pointer's number in a function that has one

    # pieces

    def regs(self, low=False):
        """The register numbers a body may write: r0-r11 (r0-r7 when low), without the frame
        pointer of a function that has one."""
        return [r for r in range(8 if low else 12) if r != self.fp]

    def dst(self, low=False):
        return "r%d" % self.rng.choice(self.regs(low))

    def src(self):
        return "r%d" % self.rng.choice(self.regs() + [12])

    def cond(self, p=0.25):
        return self.rng.choice(CONDS) if not self.thumb and self.rng.random() < p else ""

    def imm(self):
        """An immediate the data-processing instructions can encode."""
        rng = self.rng
        if self.thumb:
            b = rng.randrange(256)
            shape = rng.randrange(5)
            if shape == 0:
                return b
            if shape == 1:
                return b | b << 16
            if shape == 2:
                return (b | b << 16) << 8
            if shape == 3:
                return b * 0x01010101
            return ror32(0x80 | rng.randrange(128), rng.randrange(8, 32))
        if rng.random() < 0.4:
            return rng.randrange(256)
        return ror32(rng.randrange(256), 2 * rng.randrange(1, 16))

    def shift(self):
        """A constant shift of a register operand: ', lsl #n' ... or ', rrx'."""
        rng = self.rng
        kind = rng.choice(["lsl", "lsr", "asr", "ror", "rrx"])
        if kind == "rrx":
            return ", rrx"
        if kind == "lsl":
            return ", lsl #%d" % rng.randrange(1, 32)
        if kind == "ror":
            return ", ror #%d" % rng.randrange(1, 32)
        return ", %s #%d" % (kind, rng.choice([1, 2, 7, 16, 31, 32]))

    def small(self, reg):
        """Set a register to a small shift amount that still depends on the inputs."""
        rng = self.rng
        k = rng.randrange(4)
        if k == 0:
            return ["and %s, %s, #%d" % (reg, self.src(), rng.choice([0x1F, 0x3F, 0xFF]))]
        if k == 1:
            return ["mov %s, #%d" % (reg, rng.choice([0, 1, 31, 32, 33, 64, 255]))]
        if k == 2:
            return ["and %s, %s, #0x3f" % (reg, reg)]
        return ["movw %s, #%d" % (reg, rng.choice([256, 257, 0xFFFF]))]

    def operand2(self, pre):
        """A flexible second operand; may add set-up lines to `pre`."""
        rng = self.rng
        k = rng.random()
        if k < 0.3:
            return "#%d" % self.imm()
        if k < 0.55:
            return self.src()
        if k < 0.9 or self.thumb:
            return self.src() + self.shift()
        s = self.dst()
        pre += self.small(s)
        return "%s, %s %s" % (self.src(), rng.choice(["lsl", "lsr", "asr", "ror"]), s)

    # instruction classes; each returns lines (set-up first)

    def data(self, cond):
        rng = self.rng
        pre = []
        op = rng.choice(["and", "eor", "sub", "rsb", "add", "adc", "sbc", "orr", "bic"]
                        + (["orn"] if self.thumb else ["rsc"]))
        s = "s" if rng.random() < 0.5 else ""
        second = self.operand2(pre)
        return pre + ["%s%s%s %s, %s, %s" % (op, s, cond, self.dst(), self.src(), second)]

    def move(self, cond):
        rng = self.rng
        pre = []
        op = rng.choice(["mov", "mvn"])
        s = "s" if rng.random() < 0.5 else ""
        if op == "mov" and self.thumb:
            second = rng.choice(["#%d" % self.imm(), self.src()])
        else:
            second = self.operand2(pre)
        return pre + ["%s%s%s %s, %s" % (op, s, cond, self.dst(), second)]

    def compare(self, cond):
        pre = []
        op = self.rng.choice(["cmp", "cmn", "tst", "teq"])
        return pre + ["%s%s %s, %s" % (op, cond, self.src(), self.operand2(pre))]

    def shifts(self, cond):
        rng = self.rng
        op = rng.choice(["lsl", "lsr", "asr", "ror", "rrx"])
        s = "s" if rng.random() < 0.5 else ""
        if self.thumb and not cond and rng.random() < 0.3:
            r = self.dst(low=True)                   # the 16-bit two-operand forms
            if op == "rrx":
                return ["rrxs %s, %s" % (r, r)]
            amount = self.dst(low=True)
            return self.small(amount) + ["%ss %s, %s, %s" % (op, r, r, amount)]
        if op == "rrx":
            return ["rrx%s%s %s, %s" % (s, cond, self.dst(), self.src())]
        if rng.random() < 0.5:
            n = {"lsl": rng.randrange(1, 32), "ror": rng.randrange(1, 32)}.get(op, rng.choice([1, 5, 31, 32]))
            return ["%s%s%s %s, %s, #%d" % (op, s, cond, self.dst(), self.src(), n)]
        amount = self.dst()
        return self.small(amount) + ["%s%s%s %s, %s, %s" % (op, s, cond, self.dst(), self.src(), amount)]

    def multiply(self, cond):
        rng = self.rng
        k = rng.randrange(6)
        if k == 0:
            if self.thumb:
                if cond:
                    return ["mul%s %s, %s, %s" % (cond, self.dst(), self.src(), self.src())]
                r = self.dst(low=True)
                return ["muls %s, %s, %s" % (r, self.dst(low=True), r)]
            return ["mul%s%s %s, %s, %s" % ("s" if rng.random() < 0.5 else "", cond, self.dst(), self.src(), self.src())]
        if k == 1:
            return ["%s%s %s, %s, %s, %s" % (rng.choice(["mla", "mls"]), cond, self.dst(), self.src(), self.src(),
                                             self.src())]
        if k == 5 and self.thumb:
            return ["%s%s %s, %s, %s" % (rng.choice(["udiv", "sdiv"]), cond, self.dst(), self.src(), self.src())]
        lo, hi = rng.sample(self.regs(), 2)
        s = "s" if not self.thumb and rng.random() < 0.3 else ""
        return ["%s%s%s r%d, r%d, %s, %s" % (rng.choice(["umull", "smull", "umlal", "smlal"]), s, cond, lo, hi,
                                             self.src(), self.src())]

    def misc(self, cond):
        rng = self.rng
        k = rng.randrange(8)
        d = self.dst()
        if k == 0:
            return ["%s%s %s, %s" % (rng.choice(["clz", "rev", "rev16", "revsh"]), cond, d, self.src())]
        if k == 1:
            rot = rng.choice(["", ", ror #8", ", ror #16", ", ror #24"])
            return ["%s%s %s, %s%s" % (rng.choice(["uxtb", "uxth", "sxtb", "sxth"]), cond, d, self.src(), rot)]
        if k == 2 and not self.thumb:
            rot = rng.choice(["", ", ror #8", ", ror #16", ", ror #24"])
            return ["%s%s %s, %s, %s%s" % (rng.choice(["uxtab", "uxtah", "sxtab", "sxtah"]), cond, d, self.src(),
                                           self.src(), rot)]
        if k == 3:
            lsb = rng.randrange(32)
            width = rng.randrange(1, 33 - lsb)
            return ["%s%s %s, %s, #%d, #%d" % (rng.choice(["ubfx", "sbfx"]), cond, d, self.src(), lsb, width)]
        if k == 4:
            lsb = rng.randrange(32)
            width = rng.randrange(1, 33 - lsb)
            if rng.random() < 0.5:
                return ["bfc%s %s, #%d, #%d" % (cond, d, lsb, width)]
            return ["bfi%s %s, %s, #%d, #%d" % (cond, d, self.src(), lsb, width)]
        if k == 5:
            return ["%s%s %s, #%d" % (rng.choice(["movw", "movt"]), cond, d, rng.choice(
                [0, 1, 0x7FFF, 0x8000, 0xFFFF, rng.randrange(0x10000)]))]
        return self.data(cond)

    def base(self, size, span=0):
        """(set-up lines, base register): a register pointing into the buffer, aligned for `size`."""
        rng = self.rng
        b = self.dst()
        off = rng.randrange(-48, 48 - span) & ~(min(size, 8) - 1)    # 8: ldrexd needs doubleword alignment
        return ["%s %s, r12, #%d" % ("add" if off >= 0 else "sub", b, abs(off))], b

    def memory(self, cond):
        rng = self.rng
        op, size = rng.choice([("ldr", 4), ("str", 4), ("ldrb", 1), ("strb", 1), ("ldrh", 2), ("strh", 2),
                               ("ldrsb", 1), ("ldrsh", 2)])
        pre, b = self.base(size)
        t = self.dst()
        while t == b:
            t = self.dst()
        extra = op in ("ldrh", "strh", "ldrsb", "ldrsh")         # A32's addressing mode 3: no shifts, 8-bit offsets
        k = rng.randrange(6)
        if k == 0:
            return pre + ["%s%s %s, [%s]" % (op, cond, t, b)]
        if k == 1:
            off = rng.randrange(-32, 33) & ~(size - 1)
            return pre + ["%s%s %s, [%s, #%d]" % (op, cond, t, b, off)]
        if k in (2, 3):
            i = self.dst()
            while i in (b, t):
                i = self.dst()
            n = rng.randrange(0, 5)
            pre.append("mov %s, #%d" % (i, (n * 4) & ~(size - 1)))
            if k == 3 and not extra:
                shift = 2 if size == 4 else rng.choice([1, 2])     # word accesses stay aligned
                pre[-1] = "mov %s, #%d" % (i, n)
                return pre + ["%s%s %s, [%s, %s, lsl #%d]" % (op, cond, t, b, i, shift)]
            if not self.thumb and rng.random() < 0.3:
                return pre + ["%s%s %s, [%s, -%s]" % (op, cond, t, b, i)]
            return pre + ["%s%s %s, [%s, %s]" % (op, cond, t, b, i)]
        off = (rng.randrange(1, 9) * size) * rng.choice([1, -1])
        if k == 4:
            return pre + ["%s%s %s, [%s, #%d]!" % (op, cond, t, b, off)]
        if not self.thumb and rng.random() < 0.3:
            i = self.dst()
            while i in (b, t):
                i = self.dst()
            pre.append("mov %s, #%d" % (i, rng.randrange(0, 4) * 4))
            sign = rng.choice(["", "-"])
            shift = "" if extra else rng.choice(["", ", lsl #2"])
            return pre + ["%s%s %s, [%s], %s%s%s" % (op, cond, t, b, sign, i, shift)]
        return pre + ["%s%s %s, [%s], #%d" % (op, cond, t, b, off)]

    def double(self, cond):
        rng = self.rng
        op = rng.choice(["ldrd", "strd"])
        pre, b = self.base(4, span=8)
        if self.thumb:
            t1, t2 = rng.sample([r for r in self.regs() if "r%d" % r != b], 2)
        else:
            t1 = rng.choice([r for r in (0, 2, 4, 6, 8, 10) if "r%d" % r != b and "r%d" % (r + 1) != b
                             and self.fp not in (r, r + 1)])
            t2 = t1 + 1
        k = rng.randrange(3)
        off = rng.randrange(-4, 5) * 4
        if k == 0:
            return pre + ["%s%s r%d, r%d, [%s, #%d]" % (op, cond, t1, t2, b, off)]
        if k == 1:
            return pre + ["%s%s r%d, r%d, [%s, #%d]!" % (op, cond, t1, t2, b, off or 8)]
        return pre + ["%s%s r%d, r%d, [%s], #%d" % (op, cond, t1, t2, b, off or 8)]

    def block(self, cond):
        rng = self.rng
        pre, b = self.base(4, span=16)
        mode = rng.choice(["ia", "db"] if self.thumb else ["ia", "ib", "da", "db"])
        op = rng.choice(["ldm", "stm"])
        regs = sorted(rng.sample([r for r in self.regs() if "r%d" % r != b], rng.randrange(1, 5)))
        wb = "!" if rng.random() < 0.5 else ""
        if self.thumb and len(regs) == 1:
            regs.append(next(r for r in self.regs() if "r%d" % r != b and r not in regs))
            regs.sort()
        return pre + ["%s%s%s %s%s, {%s}" % (op, mode, cond, b, wb, ", ".join("r%d" % r for r in regs))]

    def frame(self, cond):
        rng = self.rng
        k = rng.randrange(4)
        slot = rng.randrange(FRAME // 4) * 4
        if self.fp is not None and rng.random() < 0.5:
            base = "r%d" % self.fp      # the frame pointer holds the frame's lowest address
            if k == 0:
                return ["str%s %s, [%s, #%d]" % (cond, self.src(), base, slot)]
            if k == 1:
                return ["ldr%s %s, [%s, #%d]" % (cond, self.dst(), base, slot)]
            return ["%s%s %s, [%s, #%d]" % (rng.choice(["ldrb", "strb", "ldrh", "strh"]), cond, self.dst(), base,
                                            slot + rng.choice([0, 2]))]
        if k == 0:
            return ["str%s %s, [sp, #%d]" % (cond, self.src(), slot)]
        if k == 1:
            return ["ldr%s %s, [sp, #%d]" % (cond, self.dst(), slot)]
        if k == 2:
            return ["%s%s %s, [sp, #%d]" % (rng.choice(["ldrb", "strb", "ldrh", "strh"]), cond,
                                            self.dst(), slot + rng.choice([0, 2]))]
        b = self.dst()
        t = self.dst()
        while t == b:
            t = self.dst()
        # the address is cleared again: a stack address differs between the images by design
        return ["add %s, sp, #%d" % (b, slot), "%s%s %s, [%s]" % (rng.choice(["ldr", "str"]), cond, t, b),
                "mov %s, #0" % b]

    def system(self, cond):
        """Instructions that act on the core (the lifter writes them as inline assembly): interrupt
        masks, status-register reads and writes (the flags through APSR), barriers, and an exclusive
        load and store on the buffer. Always unconditional."""
        rng = self.rng
        k = rng.randrange(6)
        d = self.dst()
        if k == 0:
            return [rng.choice(["cpsid i", "cpsie i"])]
        if k == 1:
            return [rng.choice(["dmb", "dsb", "isb"])]
        if k == 2:
            return ["mrs %s, apsr" % d]                          # the flags, read
        if k == 3:
            return ["msr apsr_nzcvq, %s" % self.src()]            # the flags, written
        if k == 4:
            if self.thumb:                                      # PRIMASK: the interrupt mask bit
                return ["and %s, %s, #1" % (d, self.src()), "msr primask, %s" % d, "mrs %s, primask" % self.dst()]
            return ["mrs %s, cpsr" % d, "%s %s, %s, #0x80" % (rng.choice(["orr", "bic"]), d, d),   # the I bit; the
                    "msr cpsr_c, %s" % d]                                                           # mode stays
        pre, b = self.base(8, span=8)
        if not self.thumb and rng.random() < 0.4:   # the doubleword pair (ARMv7-A; Cortex-M3 has none)
            pairs = [n for n in (0, 2, 4, 6, 8, 10) if b not in ("r%d" % n, "r%d" % (n + 1)) and self.fp not in (n, n + 1)]
            t, x = rng.sample(pairs, 2)
            r = rng.choice([n for n in self.regs() if "r%d" % n != b and n not in (t, t + 1, x, x + 1)])
            return pre + ["ldrexd r%d, r%d, [%s]" % (t, t + 1, b), "add r%d, r%d, #1" % (x, t),
                          "eor r%d, r%d, r%d" % (x + 1, t + 1, x), "strexd r%d, r%d, r%d, [%s]" % (r, x, x + 1, b)]
        t, x, r = rng.sample([n for n in self.regs() if "r%d" % n != b], 3)
        return pre + ["ldrex r%d, [%s]" % (t, b), "add r%d, r%d, #1" % (x, t), "strex r%d, r%d, [%s]" % (r, x, b)]

    def one(self, cond=""):
        rng = self.rng
        kind = rng.choices(["data", "move", "compare", "shifts", "multiply", "misc", "memory", "double", "block",
                            "frame", "system"], [26, 8, 10, 10, 6, 8, 12, 4, 4, 6, 3])[0]
        return getattr(self, kind)("" if kind == "system" else cond)

    def unit(self, name):
        """One unit of a body: lines that belong together (set-up lines and their instruction, an IT
        block, a forward branch with what it jumps over). Returns (lines, instructions)."""
        rng = self.rng
        r = rng.random()
        if self.thumb and r < 0.15:
            first = rng.choice(CONDS)
            pattern = [first] + [rng.choice([first, OPPOSITE[first]]) for _ in range(rng.randrange(0, 3))]
            mask = "".join("t" if c == first else "e" for c in pattern[1:])
            chunks = [self.single(c) for c in pattern]
            setup = [l for chunk in chunks for l in chunk[:-1]]
            return setup + ["it%s %s" % (mask, first)] + [chunk[-1] for chunk in chunks], len(pattern)
        if r < 0.22:
            # a forward branch; most test one bit of a register first, so that inputs go both ways
            self.label += 1
            label = ".L%s_%d" % (name, self.label)
            word = "[r12, #%d]" % (4 * rng.randrange(-8, 8))        # the buffer differs from input to input
            if self.thumb and rng.random() < 0.4:
                low = self.dst(low=True)
                lines = ["ldr %s, %s" % (low, word), "and %s, %s, #%d" % (low, low, 1 << rng.randrange(8)),
                         "%s %s, %s" % (rng.choice(["cbz", "cbnz"]), low, label)]
            elif rng.random() < 0.7:
                t = self.dst()
                lines = ["ldr %s, %s" % (t, word), "tst %s, #%d" % (t, 1 << rng.randrange(8)),
                         "b%s %s" % (rng.choice(["eq", "ne"]), label)]
            else:
                lines = ["b%s %s" % (rng.choice(CONDS), label)]
            k = rng.randrange(1, 4)
            for _ in range(k):
                lines += self.one()
            return lines + [label + ":"], k
        if r < 0.27:
            return self.call(), 1
        if r < 0.31:
            # push and pop around a few units, into other registers: the stack pointer moves inside
            # the function and frame slots are addressed from the moved pointer
            pushed = sorted(rng.sample(self.regs(), 2))
            popped = sorted(rng.sample(self.regs(), 2))
            lines = ["push {r%d, r%d}" % tuple(pushed)]
            k = rng.randrange(1, 3)
            for _ in range(k):
                lines += self.one()
            return lines + ["pop {r%d, r%d}" % tuple(popped)], k
        return self.one(self.cond()), 1

    def call(self):
        """A call of a helper (HELPERS): h0 takes three registers, h1 returns r0 and r1, h2 takes a
        fifth argument from the frame. r1-r3 and r12 are set again afterwards (the convention lets a
        callee change them) and so are the flags."""
        rng = self.rng
        helper = rng.choice(["h0", "h1", "h2"])
        cond = self.cond(0.3)
        lines = ["bl%s %s" % (cond, helper)]
        if cond:
            lines = [lines[0]]
        lines += ["ldr r12, =0x%x" % self.r12]
        high = [r for r in self.regs() if r >= 4]
        lines += ["mov r1, r%d" % rng.choice(high)] if helper != "h1" else []
        lines += ["mov r2, r%d" % rng.choice(high), "mov r3, r%d" % rng.choice(high),
                  "cmp r0, r%d" % rng.choice(high)]
        return lines

    def body(self, name, length):
        units, n = [], 0
        while n < length:
            lines, k = self.unit(name)
            units.append(lines)
            n += k
        return units

    def single(self, cond):
        """One instruction of an IT block, under `cond`: its set-up lines, then the instruction. No
        memory access: set-up lines go in front of the IT instruction, where an earlier instruction
        of the block could move a base register out of the buffer."""
        kind = self.rng.choices(["data", "move", "compare", "shifts", "multiply", "misc"], [30, 10, 10, 10, 6, 8])[0]
        return getattr(self, kind)(cond)

    def function(self, name, units, buf, result, style):
        """The function around a body. style: "fp" keeps a frame pointer (r7 on Thumb, r11 in ARM
        state; the body neither writes nor reads it except to address the frame) and restores sp
        from it; "ret" returns the Thumb-1 way, through a register the return address was popped
        into."""
        lines = [".align 2", ".global %s" % name, ".type %s, %%function" % name]
        if self.thumb:
            lines.append(".thumb_func")
        lines += ["%s:" % name, "push {r4-r11, lr}", "sub sp, sp, #%d" % FRAME]
        lines += ["str r0, [sp, #%d]" % k for k in range(0, FRAME, 4)]
        lines += ["mov r4, r0", "eor r5, r1, r0", "add r6, r2, r1", "sub r7, r3, r2", "orr r8, r0, r3",
                  "bic r9, r1, r2", "rsb r10, r2, r3", "eor r11, r3, r0, ror #7",
                  "ldr r12, =0x%x" % (buf + 4 * (BUF_WORDS // 2))]
        fp = self.fp_number() if "fp" in style else None
        if fp is not None:
            lines.append(self.rng.choice(["mov r%d, sp" % fp, "add r%d, sp, #0" % fp]))
        lines.append("cmp r0, r1")
        lines += [l for unit in units for l in unit]
        if fp is not None:
            restore = ["mov sp, r%d" % fp] + ([] if self.thumb else ["add sp, r%d, #0" % fp, "sub sp, r%d, #0" % fp])
            lines += [self.rng.choice(restore), "mov r%d, #0" % fp]     # a stack address differs by design
        lines += ["add sp, sp, #%d" % FRAME, "ldr lr, =0x%x" % result, "stm lr, {r0-r12}", "mov r0, #0"]
        for bit, c in ((8, "mi"), (4, "eq"), (2, "cs"), (1, "vs")):
            if self.thumb:
                lines.append("it %s" % c)
            lines.append("orr%s r0, r0, #%d" % (c, bit))
        lines.append("str r0, [lr, #52]")
        if "ret" in style:
            lines += ["pop {r4-r11}", "pop {r3}", "bx r3"]
        else:
            lines.append("pop {r4-r11, pc}")
        return lines + [".ltorg", ".size %s, . - %s" % (name, name)]

    def fp_number(self):
        return 7 if self.thumb else 11


HELPERS = [
    ("h0", ["eor r0, r0, r1", "add r0, r0, r2, ror #3", "bx lr"]),
    ("h1", ["umull r0, r1, r0, r1", "eor r1, r1, r2", "bx lr"]),
    ("h2", ["ldr r12, [sp]", "add r0, r0, r12", "eor r0, r0, r3", "bx lr"]),
]


def helpers(thumb):
    lines = []
    for name, body in HELPERS:
        lines += [".align 2", ".global %s" % name, ".type %s, %%function" % name]
        lines += [".thumb_func"] if thumb else []
        lines += ["%s:" % name] + body + [".size %s, . - %s" % (name, name)]
    return lines


def generate(isa, functions, length, inputs, seed):
    """({name: (style, units)}, {name: cases}) of random functions and their inputs."""
    rng = random.Random(seed)
    gen = Generator(isa, rng)
    buf = ISAS[isa]["ram"] + 0x1000
    bodies, cases = {}, {}
    for i in range(functions):
        name = "f%d" % i
        style = "".join(k for k in ("fp", "ret") if rng.random() < 0.3)
        gen.fp = gen.fp_number() if "fp" in style else None
        bodies[name] = (style, gen.body(name, length))
        cases[name] = []
        for _ in range(inputs):
            args = [rng.choice(EDGES) if rng.random() < 0.4 else rng.getrandbits(32) for _ in range(4)]
            mem = [[buf + 4 * k, 4, rng.choice(EDGES) if rng.random() < 0.2 else rng.getrandbits(32)]
                   for k in range(BUF_WORDS)]
            cases[name].append({"args": args, "mem": mem, "limit": 100_000})
    return bodies, cases


def make(isa, out, bodies, cases):
    """Write the target directory: the image assembled from the bodies, its tables, the cases."""
    from dread import elfimport
    from dread.elf import Elf
    from dread.target import Target
    conf = ISAS[isa]
    gen = Generator(isa, random.Random(0))
    buf, result = conf["ram"] + 0x1000, conf["ram"] + 0x2000
    text = [".syntax unified", ".thumb" if gen.thumb else ".arm", ".text"]
    for name, (style, units) in bodies.items():
        text += gen.function(name, units, buf, result, style)
    text += helpers(gen.thumb)
    os.makedirs(out, exist_ok=True)
    source = os.path.join(out, "fuzz.S")
    with open(source, "w") as f:
        f.write("\n".join(l if l.endswith(":") or l.startswith(".") else "    " + l for l in text) + "\n")
    elf_path = os.path.join(out, "fuzz.elf")
    first = next(iter(bodies))
    cmd = ["arm-none-eabi-gcc"] + conf["flags"] + ["-nostdlib", "-Wl,-Ttext=0x%x" % conf["base"], "-Wl,-e," + first,
                                                   "-Wl,--no-warn-rwx-segments", "-o", elf_path, source]
    got = subprocess.run(cmd, capture_output=True, text=True)
    if got.returncode:
        raise RuntimeError("the assembler refused the generated code:\n" + got.stderr)
    cflags = conf["flags"] + ["-Os", "-ffreestanding", "-fno-builtin", "-ffunction-sections", "-fno-common"]
    toml = conf["toml"].replace('entry = "f0"', 'entry = "%s"' % first)
    toml += '\n[compiler]\ncross = "arm-none-eabi-"\ncflags = [%s]\n' % ", ".join('"%s"' % f for f in cflags)
    with open(os.path.join(out, "dread.toml"), "w") as f:
        f.write(toml)
    target = Target.load(out)
    image = target.image()
    elf = Elf(elf_path)
    funcs, code, symbols = elfimport.tables(elf)
    elfimport.write_tables(image, funcs, code, symbols, header="dread liftfuzz")
    data, _ = elf.payload(image.base, image.erased)
    with open(os.path.join(out, "fuzz.bin"), "wb") as f:
        f.write(data)
    os.makedirs(image.extra_cases, exist_ok=True)
    for name in bodies:
        with open(os.path.join(image.extra_cases, name + ".json"), "w") as f:
            json.dump({"cases": cases[name]}, f)
    return target, image


def check(isa, work, bodies, cases, jobs=None):
    """Make the image in `work`, lift every function, build and compare: ({name: the runner's
    FAIL block}, {name: refusal}, [names with an instruction no input ran])."""
    from dread import build, lift, regflow, runner
    from dread.program import Program
    target, image = make(isa, work, bodies, cases)
    program = Program(target, image)
    lifter = lift.Lifter(program, regflow.load(target, image, program))
    names, refused = [], {}
    for fn in program.functions:
        try:
            lifter.lift(fn)
            names.append(fn.name)
        except lift.Refused as e:
            refused[fn.name] = str(e)
    names = [n for n in names if n in bodies]          # the helpers stay as they are
    failures = {}
    if names:
        payload = build.build(target, image, lifted=names, quiet=True)
        runner.prepare(target, image, payload)
        blocks = []
        _, uncovered, results = runner.run(names, only=True, out=blocks.append, jobs=jobs)
        bad = {name for name, count, fails in results if fails}
        for block in blocks:
            if block.startswith("FAIL") and block.split()[1] in bad:
                failures.setdefault(block.split()[1], block)
    else:
        uncovered = []
    return failures, refused, uncovered


def minimize(isa, body, cases, work, jobs=None):
    """The shortest run of units that still differs or is refused: the shortest failing prefix,
    then every unit left out whose absence keeps it failing. Returns (style, units, builds)."""
    style, units = body
    trials = [0]

    def fails(us):
        trials[0] += 1
        here = os.path.join(work, "trial")
        shutil.rmtree(here, ignore_errors=True)
        failures, refused, _ = check(isa, here, {"f0": (style, us)}, {"f0": cases}, jobs)
        return bool(failures or refused)

    lo, hi = 1, len(units)
    while lo < hi:
        mid = (lo + hi) // 2
        if fails(units[:mid]):
            hi = mid
        else:
            lo = mid + 1
    us, i = units[:hi], 0
    while i < len(us) - 1:
        trial = us[:i] + us[i + 1:]
        if fails(trial):
            us = trial
        else:
            i += 1
    shutil.rmtree(os.path.join(work, "trial"), ignore_errors=True)
    return style, us, trials[0]


def run(isa, functions=100, length=40, inputs=16, seed=1, keep=None, out=print, jobs=None, reduce=2):
    """Generate, lift, build and compare; reduce the first `reduce` failures. Returns (failures,
    refusals, workdir or None when removed)."""
    work = keep or tempfile.mkdtemp(prefix="dread-liftfuzz-%s-" % isa)
    bodies, cases = generate(isa, functions, length, inputs, seed)
    failures, refused, uncovered = check(isa, work, bodies, cases, jobs)
    for name, reason in sorted(refused.items()):
        out("refused  %-6s %s" % (name, reason))
    for name, block in sorted(failures.items()):
        out("\n".join(line[:300] for line in block.splitlines()[:8]))
    out("dread liftfuzz %s: %d functions of %d instructions, %d inputs each: %d differ, %d refused%s" % (
        isa, functions, length, inputs, len(failures), len(refused),
        " (%d with an instruction no input ran)" % len(uncovered) if uncovered else ""))
    for name in (sorted(refused) + sorted(failures))[:reduce]:
        style, units, trials = minimize(isa, bodies[name], cases[name], os.path.join(work, "reduce-" + name), jobs)
        out("reduced  %-6s in %d builds to: %s%s" % (name, trials, "[%s] " % style if style else "",
                                                     " | ".join("; ".join(u) for u in units)))
    if failures or refused or keep:
        out("kept in %s (fuzz.S, build/recon/main/lifted/)" % work)
        return failures, refused, work
    shutil.rmtree(work, ignore_errors=True)
    return failures, refused, None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread liftfuzz", description=__doc__.split("\n\n")[0])
    parser.add_argument("--isa", choices=sorted(ISAS), action="append", help="a32, t32 (default: both)")
    parser.add_argument("--functions", type=int, default=100)
    parser.add_argument("--length", type=int, default=40, help="random instructions per function")
    parser.add_argument("--inputs", type=int, default=16, help="written cases per function")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--reduce", type=int, default=2, help="failures reduced to their shortest form (default 2)")
    parser.add_argument("--keep", help="work in this directory and keep it")
    args = parser.parse_args(argv)
    if not shutil.which("arm-none-eabi-gcc"):
        print("dread liftfuzz: needs arm-none-eabi-gcc", file=sys.stderr)
        return 1
    from dread.util.polite import polite
    polite()
    bad = 0
    for isa in args.isa or sorted(ISAS):
        keep = os.path.join(args.keep, isa) if args.keep else None
        failures, refused, _ = run(isa, args.functions, args.length, args.inputs, args.seed, keep, reduce=args.reduce)
        bad += len(failures) + len(refused)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
