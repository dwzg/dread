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
instructions (also libgcc's eight-byte push of one register, str rN, [sp, #-8]!, and with an FPU
vpush and vpop); IT blocks on Thumb-2; instructions that act on the core (interrupt masks, the flags through APSR,
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

The ISAs: a32 (ARMv7-A in ARM state), t32 (Cortex-M3, Thumb-2), m4 (Cortex-M4: Thumb-2 with the DSP
extension, whose saturating, packed and halfword-multiply instructions it draws as well) and m4f
(the Cortex-M4F: the FPU as well, on s0-s15, which the function sets from the arguments at its
start and stores into the result buffer at its end with FPSCR; its arithmetic, multiply-
accumulates, conversions of every kind, compares with vmrs APSR_nzcv, moves and constants, loads
and stores), with the cross toolchain (arm-none-eabi-); rv32 (RV32IMC, dread.lift_riscv), with clang and ld.lld: RiscVGenerator, which
frames a function the same way (ra and s0-s11 saved, every frame slot written, every register set
from the arguments before the body reads it, s11 the buffer's address, s0 the frame pointer in the
"fp" style, a return through another register in the "ret" style) and draws from the base, M and
Zicsr instructions; the assembler compresses what it can, so the compressed forms come along; msp430
(the 16-bit MSP430, dread.lift_msp430), with TI's msp430-elf-gcc: Msp430Generator (r4-r10 pushed,
the frame written, r10 the buffer's address, r4 the frame pointer in the "fp" style, GCC's shared
epilogues in the "ret" style; every addressing mode in word and byte form, dadd, rrc, rra, swpb, sxt,
dint and eint; two stack arguments of its own, which the cases pass and the body reads and writes
through sp and the frame pointer, and a helper that takes two stack words). Its image holds
libgcc's shared epilogues and nothing else of libgcc: lifted C that called a shift routine fails
the build, reported per function and reduced like a difference. Found on its first runs: TI's GCC
8.2 deletes a volatile load and a store of the same value back to the same address as a move to
itself. The image, its target directory and the lifted C go to a temporary directory, kept when
something fails (or with --keep).

    dread liftfuzz [--isa a32|t32|m4|m4f|rv32|msp430] [--functions N] [--length L] [--inputs K] [--seed S]
                   [--reduce N] [--keep DIR]
"""
import argparse
import json
import os
import random
import re
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
    "m4": {
        "flags": ["-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft"], "thumb": True, "dsp": True,
        "toml": '''[target]
name = "liftfuzz-m4"
title = "dread liftfuzz, Thumb-2 with the DSP extension (Cortex-M4)"

[arch]
name = "arm"
isa = "t32"
profile = "m"
cpu = "cortex-m4"

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
    "m4f": {
        "flags": ["-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=hard", "-mfpu=fpv4-sp-d16"], "thumb": True, "dsp": True, "fpu": True,
        "toml": '''[target]
name = "liftfuzz-m4f"
title = "dread liftfuzz, Thumb-2 with the DSP extension and the FPU (Cortex-M4F)"

[arch]
name = "arm"
isa = "t32"
profile = "m"
cpu = "cortex-m4"

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
    "rv32": {
        "flags": ["--target=riscv32-unknown-elf", "-march=rv32imc", "-mabi=ilp32", "-mno-relax"],
        "toml": '''[target]
name = "liftfuzz-rv32"
title = "dread liftfuzz, RV32IMC"

[arch]
name = "riscv"
isa = "rv32"

[image.main]
original = "fuzz.bin"
base = 0x20000000
limit = 0x20080000
entry = "f0"

[memory]
regions = [
    {name = "flash", base = 0x20000000, size = 0x100000, kind = "rom"},
    {name = "ram", base = 0x80000000, size = 0x10000},
    {name = "scratch", base = 0x80100000, size = 0x20000},
]
io = [[0x10000000, 0x10010000]]
stack = {top = 0x80010000, size = 0x2000}
sentinel = 0x1fff0000
scratch = {base = 0x80100000, size = 0x20000}
''',
        "base": 0x20000000, "ram": 0x80000000,
    },
    "msp430": {
        "flags": ["-mcpu=msp430", "-mhwmult=none"],
        "toml": '''[target]
name = "liftfuzz-msp430"
title = "dread liftfuzz, MSP430"

[arch]
name = "msp430"
isa = "msp430"

[image.main]
original = "fuzz.bin"
base = 0x4000
limit = 0xff00
entry = "f0"

[memory]
regions = [
    {name = "flash", base = 0x4000, size = 0xc000, kind = "rom"},
    {name = "ram", base = 0x0200, size = 0x2400},
    {name = "scratch", base = 0x2800, size = 0x1800},
]
io = [[0x0000, 0x0200]]
stack = {top = 0x1000, size = 0x0400}
sentinel = 0x3ff0
scratch = {base = 0x2800, size = 0x1800}
''',
        "base": 0x4000, "ram": 0x0200,
    },
}


def ror32(value, n):
    n &= 31
    return ((value >> n) | (value << (32 - n))) & 0xFFFFFFFF if n else value


class Generator:
    """Random functions of one ISA, as assembly lines."""

    def __init__(self, isa, rng):
        self.isa, self.rng = isa, rng
        self.thumb = ISAS[isa].get("thumb", isa == "t32")
        self.has_dsp = ISAS[isa].get("dsp", False)  # the DSP extension (the Cortex-M4's)
        self.has_fpu = ISAS[isa].get("fpu", False)  # the FPU (the Cortex-M4F's): s0-s15 in the body
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
        if k == 2 and (not self.thumb or self.has_dsp):
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

    def dsp(self, cond):
        """The DSP extension: saturating arithmetic (the Q flag), packed additions and subtractions
        (the GE flags) and sel, which reads them; the dual, halfword and most-significant-word
        multiplies with and without accumulation; packing, extending of byte pairs, sums of absolute
        differences. One instruction each, so that it also fills an IT block's slot."""
        rng = self.rng
        d, a, b, c = self.dst(), self.src(), self.src(), self.src()
        k = rng.randrange(10)
        if k == 0:
            return ["%s%s %s, %s, %s" % (rng.choice(["qadd", "qsub", "qdadd", "qdsub"]), cond, d, a, b)]
        if k == 1:
            shift = rng.choice(["", ", lsl #%d" % rng.randrange(1, 32), ", asr #%d" % rng.randrange(1, 32)])
            if rng.random() < 0.5:
                return ["ssat%s %s, #%d, %s%s" % (cond, d, rng.randrange(1, 33), a, shift)]
            return ["usat%s %s, #%d, %s%s" % (cond, d, rng.randrange(0, 32), a, shift)]
        if k == 2:
            if rng.random() < 0.5:
                return ["ssat16%s %s, #%d, %s" % (cond, d, rng.randrange(1, 17), a)]
            return ["usat16%s %s, #%d, %s" % (cond, d, rng.randrange(0, 16), a)]
        if k == 3:
            op = rng.choice(["s", "q", "sh", "u", "uq", "uh"]) + rng.choice(["add16", "sub16", "asx", "sax", "add8", "sub8"])
            return ["%s%s %s, %s, %s" % (op, cond, d, a, b)]
        if k == 4:
            return ["sel%s %s, %s, %s" % (cond, d, a, b)]
        if k == 5:
            op = rng.choice(["smlad", "smladx", "smlsd", "smlsdx", "smmla", "smmlar", "smmls", "smmlsr", "smlabb",
                             "smlabt", "smlatb", "smlatt", "smlawb", "smlawt", "usada8"])
            return ["%s%s %s, %s, %s, %s" % (op, cond, d, a, b, c)]
        if k == 6:
            op = rng.choice(["smuad", "smuadx", "smusd", "smusdx", "smmul", "smmulr", "smulbb", "smulbt", "smultb",
                             "smultt", "smulwb", "smulwt", "usad8"])
            return ["%s%s %s, %s, %s" % (op, cond, d, a, b)]
        if k == 7:
            lo, hi = rng.sample(self.regs(), 2)
            op = rng.choice(["smlald", "smlaldx", "smlsld", "smlsldx", "smlalbb", "smlalbt", "smlaltb", "smlaltt",
                             "umaal"])
            return ["%s%s r%d, r%d, %s, %s" % (op, cond, lo, hi, a, b)]
        if k == 8:
            if rng.random() < 0.5:
                return ["pkhbt%s %s, %s, %s%s" % (cond, d, a, b, rng.choice(["", ", lsl #%d" % rng.randrange(1, 32)]))]
            return ["pkhtb%s %s, %s, %s, asr #%d" % (cond, d, a, b, rng.randrange(1, 32))]
        rot = rng.choice(["", ", ror #8", ", ror #16", ", ror #24"])
        if rng.random() < 0.5:
            return ["%s%s %s, %s, %s%s" % (rng.choice(["sxtab16", "uxtab16"]), cond, d, a, b, rot)]
        return ["%s%s %s, %s%s" % (rng.choice(["sxtb16", "uxtb16"]), cond, d, a, rot)]

    def vfp(self, cond):
        """The FPU (VFPv4-SP) on s0-s15: arithmetic, the multiply-accumulates (chained and fused),
        negation, absolute value, square root, conversions (to and from integers, rounded both ways,
        to and from fixed point, to and from half precision), compares with the flags moved to APSR,
        moves between core and FPU registers and of float constants, loads and stores on the
        buffer. One conditional instruction under `cond` (an IT block's slot), more freely without."""
        rng = self.rng
        d, a, b = ("s%d" % rng.randrange(16) for _ in range(3))
        k = rng.randrange(11 if cond else 13)
        if k == 0:
            return ["%s%s.f32 %s, %s, %s" % (rng.choice(["vadd", "vsub", "vmul", "vdiv", "vnmul"]), cond, d, a, b)]
        if k == 1:
            return ["%s%s.f32 %s, %s, %s" % (rng.choice(["vmla", "vmls", "vnmla", "vnmls", "vfma", "vfms", "vfnma",
                                                          "vfnms"]), cond, d, a, b)]
        if k == 2:
            return ["%s%s.f32 %s, %s" % (rng.choice(["vneg", "vabs", "vsqrt"]), cond, d, a)]
        if k == 3:
            op = rng.choice(["vcvt.s32.f32", "vcvtr.s32.f32", "vcvt.u32.f32", "vcvtr.u32.f32", "vcvt.f32.s32",
                             "vcvt.f32.u32"])
            m, _, dt = op.partition(".")
            return ["%s%s.%s %s, %s" % (m, cond, dt, d, a)]
        if k == 4:
            dt = rng.choice(["s32.f32", "u32.f32", "f32.s32", "f32.u32", "s16.f32", "u16.f32", "f32.s16", "f32.u16"])
            return ["vcvt%s.%s %s, %s, #%d" % (cond, dt, d, d, rng.randrange(1, 17 if "16" in dt else 33))]
        if k == 5:
            return ["%s%s.%s %s, %s" % (rng.choice(["vcvtb", "vcvtt"]), cond, rng.choice(["f32.f16", "f16.f32"]), d, a)]
        if k == 6:
            return ["vmov%s %s, %s" % (cond, self.dst(), a)]
        if k == 7:
            return ["vmov%s %s, %s" % (cond, d, self.src())]
        if k == 8:
            return ["vmov%s.f32 %s, #%s" % (cond, d, rng.choice(VFP_CONSTANTS))]
        if k == 9:
            return ["vmov%s.f32 %s, %s" % (cond, d, a)]
        if k == 10:
            return ["%s%s.f32 %s, %s" % (rng.choice(["vcmp", "vcmpe"]), cond, a, rng.choice([b, "#0"]))]
        if k == 11:                         # (unconditional) a compare and its flags in APSR
            return ["%s.f32 %s, %s" % (rng.choice(["vcmp", "vcmpe"]), a, rng.choice([b, "#0"])),
                    "vmrs APSR_nzcv, fpscr"]
        off = 4 * rng.randrange(-8, 8)
        return ["%s %s, [r12, #%d]" % (rng.choice(["vldr", "vstr"]), d, off)]

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
                            "frame", "system", "dsp", "vfp"], [26, 8, 10, 10, 6, 8, 12, 4, 4, 6, 3, 12 if self.has_dsp else 0,
                                                     18 if self.has_fpu else 0])[0]
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
            # the function and frame slots are addressed from the moved pointer (the pushed words
            # read back among them). Also the single-register form of libgcc's assembly helpers
            # (str rN, [sp, #-8]!; ldr rM, [sp], #8: eight bytes for one register) and, with an
            # FPU, vpush and vpop of s0-s15 by single or double registers
            form = rng.choices(["pair", "single8", "fpu"], [6, 2, 2 if self.has_fpu else 0])[0]
            if form == "pair":
                pushed = sorted(rng.sample(self.regs(), 2))
                popped = sorted(rng.sample(self.regs(), 2))
                lines, end = ["push {r%d, r%d}" % tuple(pushed)], ["pop {r%d, r%d}" % tuple(popped)]
            elif form == "single8":
                lines = ["str r%d, [sp, #-8]!" % rng.choice(self.regs())]
                end = ["ldr r%d, [sp], #8" % rng.choice(self.regs())]
            else:
                n = rng.choice([1, 2, 3, 4])
                double = n % 2 == 0 and rng.random() < 0.5

                def registers():
                    if double:
                        d = rng.randrange(0, 8 - n // 2 + 1)
                        return "{d%d}" % d if n == 2 else "{d%d-d%d}" % (d, d + n // 2 - 1)
                    s = rng.randrange(0, 16 - n + 1)
                    return "{s%d}" % s if n == 1 else "{s%d-s%d}" % (s, s + n - 1)
                lines, end = ["vpush %s" % registers()], ["vpop %s" % registers()]
            k = rng.randrange(1, 3)
            for _ in range(k):
                lines += self.one()
            return lines + end, k
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
        kind = self.rng.choices(["data", "move", "compare", "shifts", "multiply", "misc", "vfp"],
                                [30, 10, 10, 10, 6, 8, 12 if self.has_fpu else 0])[0]
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
        if self.has_fpu:                    # s0-s15 from the arguments too (some float constants)
            lines += ["vmov s%d, r%d" % (k, (5 * k + 3) % 12) for k in range(16)]
            lines += ["vmov.f32 s%d, #%s" % (k, self.rng.choice(VFP_CONSTANTS)) for k in self.rng.sample(range(16), 3)]
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
        if self.has_fpu:                    # s0-s15 and FPSCR (its flags, its cumulative exceptions) as well
            lines += ["add lr, lr, #56", "vstmia lr, {s0-s15}", "vmrs r0, fpscr", "str r0, [lr, #64]"]
        if "ret" in style:
            lines += ["pop {r4-r11}", "pop {r3}", "bx r3"]
        else:
            lines.append("pop {r4-r11, pc}")
        return lines + [".ltorg", ".size %s, . - %s" % (name, name)]

    def fp_number(self):
        return 7 if self.thumb else 11


VFP_CONSTANTS = ["1.0", "-1.0", "0.5", "2.0", "0.125", "31.0", "-0.25", "1.5", "3.0", "-16.0"]

HELPERS = [
    ("h0", ["eor r0, r0, r1", "add r0, r0, r2, ror #3", "bx lr"]),
    ("h1", ["umull r0, r1, r0, r1", "eor r1, r1, r2", "bx lr"]),
    ("h2", ["ldr r12, [sp]", "add r0, r0, r12", "eor r0, r0, r3", "bx lr"]),
]


RV_ALU = ["add", "sub", "sll", "srl", "sra", "slt", "sltu", "xor", "or", "and"]
RV_IMM = ["addi", "slti", "sltiu", "xori", "ori", "andi"]
RV_MUL = ["mul", "mulh", "mulhsu", "mulhu", "div", "divu", "rem", "remu"]
RV_BRANCH = ["beq", "bne", "blt", "bge", "bltu", "bgeu"]
RV_LOADS = [("lb", 1), ("lbu", 1), ("lh", 2), ("lhu", 2), ("lw", 4)]
RV_STORES = [("sb", 1), ("sh", 2), ("sw", 4)]
RV_SAVED = ["s%d" % i for i in range(12)]
RV_HELPERS = [
    ("h0", ["xor a0, a0, a1", "add a0, a0, a2", "ret"]),                    # three registers in
    ("h1", ["mul a2, a0, a1", "mulhu a1, a0, a1", "mv a0, a2", "ret"]),     # a 64-bit result: a0 and a1
    ("h2", ["add a0, a3, a4", "xor a0, a0, a5", "ret"]),                    # the sixth argument
]


class RiscVGenerator:
    """Random RV32IMC functions (see the module): the same interface as Generator."""

    thumb = False

    def __init__(self, isa, rng):
        self.isa, self.rng, self.label, self.fp = isa, rng, 0, None
        self.buf = ISAS[isa]["ram"] + 0x1000 + 4 * (BUF_WORDS // 2)

    def regs(self):
        """The registers the body computes with: not zero, ra, sp, gp, tp, s11 (the buffer), s0
        when it is the frame pointer."""
        out = ["t%d" % i for i in range(7)] + ["a%d" % i for i in range(8)] + ["s%d" % i for i in range(1, 11)]
        return out + ([] if self.fp else ["s0"])

    def dst(self):
        return self.rng.choice(self.regs())

    def src(self):
        return self.rng.choice(self.regs() + ["zero"] * 1)

    def imm12(self):
        rng = self.rng
        return rng.choice([0, 1, -1, 2, 31, 32, -2048, 2047, 0x7F, 0x80, -0x80, 0x7FF]) if rng.random() < 0.5 \
            else rng.randrange(-2048, 2048)

    def one(self):
        rng = self.rng
        kind = rng.choices(["alu", "imm", "shift", "upper", "mul", "load", "store", "frame", "system"],
                           [24, 18, 8, 4, 8, 12, 10, 8, 2])[0]
        if kind == "alu":
            return ["%s %s, %s, %s" % (rng.choice(RV_ALU), self.dst(), self.src(), self.src())]
        if kind == "imm":
            return ["%s %s, %s, %d" % (rng.choice(RV_IMM), self.dst(), self.src(), self.imm12())]
        if kind == "shift":
            return ["%s %s, %s, %d" % (rng.choice(["slli", "srli", "srai"]), self.dst(), self.src(), rng.randrange(32))]
        if kind == "upper":
            return ["%s %s, %d" % (rng.choice(["lui", "auipc"]), self.dst(), rng.getrandbits(20))]
        if kind == "mul":
            return ["%s %s, %s, %s" % (rng.choice(RV_MUL), self.dst(), self.src(), self.src())]
        if kind == "load":
            op, width = rng.choice(RV_LOADS)
            return ["%s %s, %d(s11)" % (op, self.dst(), width * rng.randrange(-32 // width * 4, 32 // width * 4))]
        if kind == "store":
            op, width = rng.choice(RV_STORES)
            return ["%s %s, %d(s11)" % (op, self.src(), width * rng.randrange(-32 // width * 4, 32 // width * 4))]
        if kind == "frame":
            base = "s0" if self.fp and rng.random() < 0.5 else "sp"
            off = 4 * rng.randrange(FRAME // 4) - (FRAME if base == "s0" else 0)
            if rng.random() < 0.5:
                return ["sw %s, %d(%s)" % (self.src(), off, base)]
            return ["lw %s, %d(%s)" % (self.dst(), off, base)]
        csr = rng.choice(["csrrw %s, mscratch, %s", "csrrs %s, mscratch, zero", "csrrwi %s, mscratch, 5", "fence"])
        if csr == "fence":
            return ["fence"]
        return [csr % ((self.dst(), self.src()) if csr.count("%s") == 2 else (self.dst(),))]

    def unit(self, name):
        rng = self.rng
        r = rng.random()
        if r < 0.2:
            # a forward branch; most test a bit of a buffer word first, so that inputs go both ways
            self.label += 1
            label = ".L%s_%d" % (name, self.label)
            t = self.dst()
            if rng.random() < 0.6:
                lines = ["lw %s, %d(s11)" % (t, 4 * rng.randrange(-8, 8)),
                         "andi %s, %s, %d" % (t, t, 1 << rng.randrange(11)),
                         "%s %s, %s" % (rng.choice(["beqz", "bnez"]), t, label)]
            else:
                lines = ["%s %s, %s, %s" % (rng.choice(RV_BRANCH), self.src(), self.src(), label)]
            k = rng.randrange(1, 4)
            for _ in range(k):
                lines += self.one()
            return lines + [label + ":"], k
        if r < 0.25:
            return self.call(), 1
        return self.one(), 1

    def call(self):
        """A call of a helper; the scratch registers it may change are set again afterwards."""
        rng = self.rng
        helper = rng.choice(["h0", "h1", "h2"])
        keep = {"a0", "a1"} if helper == "h1" else {"a0"}
        saved = ["s%d" % i for i in range(1, 11)]
        lines = ["call %s" % helper]
        for r in ["t%d" % i for i in range(7)] + ["a%d" % i for i in range(8)]:
            if r not in keep:
                lines.append("mv %s, %s" % (r, rng.choice(saved)))
        return lines

    def body(self, name, length):
        units, n = [], 0
        while n < length:
            lines, k = self.unit(name)
            units.append(lines)
            n += k
        return units

    def fp_number(self):
        return 8                                # s0

    def prelude(self):
        return [".text"]

    def helpers(self):
        lines = []
        for name, body in RV_HELPERS:
            lines += [".p2align 2", ".global %s" % name, ".type %s, @function" % name, "%s:" % name] + body
            lines += [".size %s, . - %s" % (name, name)]
        return lines

    def function(self, name, units, buf, result, style):
        """The function around a body (see the module)."""
        save = 4 * (len(RV_SAVED) + 1)
        total = FRAME + save
        lines = [".p2align 2", ".global %s" % name, ".type %s, @function" % name, "%s:" % name,
                 "addi sp, sp, -%d" % total, "sw ra, %d(sp)" % (total - 4)]
        lines += ["sw %s, %d(sp)" % (r, total - 8 - 4 * k) for k, r in enumerate(RV_SAVED)]
        lines += ["sw a0, %d(sp)" % k for k in range(0, FRAME, 4)]
        lines += ["mv a4, a0", "xor a5, a1, a0", "add a6, a2, a1", "sub a7, a3, a2",
                  "mv t0, a0", "xor t1, a1, a3", "add t2, a2, a0", "sub t3, a3, a1", "or t4, a0, a2",
                  "and t5, a1, a3", "slli t6, a2, 3"]
        lines += ["mv s%d, a%d" % (i, (i - 1) % 4) if i < 5 else "xor s%d, a%d, a%d" % (i, i % 4, (i + 1) % 4)
                  for i in range(1, 11)]
        lines += ["lui s11, %%hi(0x%x)" % self.buf, "addi s11, s11, %%lo(0x%x)" % self.buf]
        if "fp" in style:
            lines.append("addi s0, sp, %d" % FRAME)     # the frame's top: slots at negative offsets
        else:
            lines.append("xor s0, a0, a1")
        lines += [l for unit in units for l in unit]
        if "fp" in style:
            lines += ["addi sp, s0, -%d" % FRAME, "li s0, 0"]     # a stack address differs by design
        lines += ["lui ra, %%hi(0x%x)" % result, "addi ra, ra, %%lo(0x%x)" % result]
        regs = ["t%d" % i for i in range(7)] + ["a%d" % i for i in range(8)] + ["s%d" % i for i in range(11)]
        lines += ["sw %s, %d(ra)" % (r, 4 * k) for k, r in enumerate(regs)]
        lines += ["lw ra, %d(sp)" % (total - 4)]
        lines += ["lw %s, %d(sp)" % (r, total - 8 - 4 * k) for k, r in enumerate(RV_SAVED)]
        lines.append("addi sp, sp, %d" % total)
        if "ret" in style:
            lines += ["mv t1, ra", "jr t1"]
        else:
            lines.append("ret")
        return lines + [".size %s, . - %s" % (name, name)]


MSP_DOUBLE = ["mov", "add", "addc", "sub", "subc", "cmp", "bit", "bic", "bis", "xor", "and", "dadd"]
MSP_JUMPS = ["jne", "jeq", "jlo", "jhs", "jn", "jge", "jl", "jmp"]
MSP_SAVED = ["r4", "r5", "r6", "r7", "r8", "r9", "r10"]
# libgcc's shared epilogues, which GCC's -Os C jumps to (one object, the pops falling into one
# another): linked into the image, as a firmware GCC built holds them. The lifted C calls nothing else
# of libgcc (its flags and rotations are the instructions themselves: dread.lift_msp430's HELPERS); C
# that did would fail the overlay's link and show here.
MSP_LIBGCC = ["__mspabi_func_epilog_1"]
MSP_HELPERS = [
    ("h0", ["xor r13, r12", "add r14, r12", "ret"]),                           # three registers in
    ("h1", ["mov r12, r14", "add r13, r12", "addc r14, r13", "ret"]),         # a 32-bit result: r12 and r13
    ("h2", ["sub r15, r12", "rra r12", "ret"]),                               # the fourth argument
    ("h3", ["add 2(r1), r12", "mov.b 5(r1), r13", "xor r13, r12", "ret"]),    # stack word 0, word 1's top
]
MSP_STACK_ARGS = 2          # stack words each function takes beyond r12-r15: the cases pass them


class Msp430Generator:
    """Random 16-bit MSP430 functions (see the module): the same interface as Generator. The
    function saves r4-r10 (pushes from r10 down, as GCC does), writes every frame slot, sets every
    register from the arguments, keeps the buffer's address in r10 and, in the "fp" style, sp's
    value in r4 to address the frame. The body draws the two-operand instructions in word and byte
    form with every source mode (registers, the constant generators, immediates, indexed, indirect,
    @r10+ undone behind it, absolute) and the destinations register, indexed, absolute and frame
    slots; rrc, rra, swpb, sxt; push and pop around a few instructions; dint, eint and nop; forward
    jumps on the flags (most behind a bit test of a buffer word); calls of four helpers, one of which
    takes two stack words (pushed, or stored below the frame, as GCC's callers do: never read again).
    The function takes MSP_STACK_ARGS stack words of its own: a quarter of the frame-slot operands
    address them (read and written, words and either byte). The "ret" style returns through a chain of
    shared epilogues (pops that fall into one another, as __mspabi_func_epilog_N), the other pops and
    returns itself."""

    thumb = False
    stack_words = MSP_STACK_ARGS
    TOP = 2 * len(MSP_SAVED) + FRAME        # the body's sp: bytes below the entry's

    def __init__(self, isa, rng):
        self.isa, self.rng, self.label, self.fp = isa, rng, 0, None
        self.buf = ISAS[isa]["ram"] + 0x1000 + 4 * (BUF_WORDS // 2)

    def regs(self):
        out = ["r5", "r6", "r7", "r8", "r9", "r11", "r12", "r13", "r14", "r15"]
        return out + ([] if self.fp else ["r4"])

    def dst(self):
        return self.rng.choice(self.regs())

    def imm(self):
        rng = self.rng
        if rng.random() < 0.4:
            return rng.choice([0, 1, 2, 4, 8, -1])          # the constant generators
        return rng.choice([0x7F, 0x80, 0xFF, 0x100, 0x7FFF, 0x8000, 0xFFFF, 9, 10, 0x99, 0x9999]) \
            if rng.random() < 0.4 else rng.randrange(0x10000)

    def frame_slot(self, byte=False):
        """A word of the frame or, a quarter of the time, one of the function's stack arguments
        (above the return address), through sp or the frame pointer; for a byte either half."""
        rng = self.rng
        base = "r4" if self.fp and rng.random() < 0.5 else "r1"
        if rng.random() < 0.25:
            at = self.TOP + 2 + 2 * rng.randrange(MSP_STACK_ARGS)
        else:
            at = 2 * rng.randrange(FRAME // 2)
        return "%d(%s)" % (at + (rng.randrange(2) if byte else 0), base)

    def source(self, byte):
        """(text, lines after it): an operand and what undoes @r10+."""
        rng = self.rng
        r = rng.random()
        if r < 0.55:
            return self.rng.choice(self.regs()), []
        if r < 0.8:
            return "#%d" % self.imm(), []
        if r < 0.9:
            return "%d(r10)" % (rng.randrange(-32, 32) if byte else 2 * rng.randrange(-16, 16)), []
        if r < 0.94:
            return "@r10", []
        if r < 0.97:
            return "@r10+", ["sub #%d, r10" % (1 if byte else 2)]
        return "&0x%x" % (self.buf + (rng.randrange(-32, 32) if byte else 2 * rng.randrange(-16, 16))), []

    def destination(self, byte):
        rng = self.rng
        r = rng.random()
        if r < 0.75:
            return self.dst()
        if r < 0.88:
            return "%d(r10)" % (rng.randrange(-32, 32) if byte else 2 * rng.randrange(-16, 16))
        if r < 0.94:
            return "&0x%x" % (self.buf + (rng.randrange(-32, 32) if byte else 2 * rng.randrange(-16, 16)))
        return self.frame_slot(byte)

    def one(self):
        rng = self.rng
        kind = rng.choices(["double", "single", "frame", "pushpop", "system"], [66, 14, 10, 6, 4])[0]
        if kind == "double":
            op = rng.choice(MSP_DOUBLE)
            byte = rng.random() < 0.3
            src, after = self.source(byte)
            return ["%s%s %s, %s" % (op, ".b" if byte else "", src, self.destination(byte))] + after
        if kind == "single":
            op = rng.choice(["rrc", "rra", "swpb", "sxt"])
            byte = op in ("rrc", "rra") and rng.random() < 0.3
            where = self.dst() if rng.random() < 0.8 else "%d(r10)" % (2 * rng.randrange(-16, 16))
            return ["%s%s %s" % (op, ".b" if byte else "", where)]
        if kind == "frame":
            byte = rng.random() < 0.3
            op = "mov.b" if byte else "mov"
            if rng.random() < 0.5:
                return ["%s %s, %s" % (op, self.dst(), self.frame_slot(byte))]
            return ["%s %s, %s" % (op, self.frame_slot(byte), self.dst())]
        if kind == "pushpop":
            src, after = self.source(False)
            middle = []
            while len(middle) < 2:
                line = self.one()
                if not any("r1" in l.replace("r10", "").replace("r11", "").replace("r12", "").replace(
                        "r13", "").replace("r14", "").replace("r15", "") or "push" in l or "pop" in l for l in line):
                    middle += line
            return ["push %s" % src] + after + middle + ["pop %s" % self.dst()]
        return rng.choice([["dint", "nop"], ["eint"], ["nop"]])

    def unit(self, name):
        rng = self.rng
        r = rng.random()
        if r < 0.2:
            self.label += 1
            label = ".L%s_%d" % (name, self.label)
            if rng.random() < 0.6:
                lines = ["bit #%d, %d(r10)" % (1 << rng.randrange(16), 2 * rng.randrange(-16, 16)),
                         "%s %s" % (rng.choice(["jne", "jeq"]), label)]
            else:
                lines = ["cmp %s, %s" % (self.source(False)[0].replace("@r10+", "@r10"), self.dst()),
                         "%s %s" % (rng.choice(MSP_JUMPS), label)]
            k = rng.randrange(1, 4)
            for _ in range(k):
                lines += self.one()
            return lines + [label + ":"], k
        if r < 0.25:
            return self.call(), 1
        return self.one(), 1

    def call(self):
        """A call of a helper; the scratch registers it may change are set again afterwards. h3's
        two stack words are pushed, or stored into room made below the frame, and dropped behind
        the call: a caller that read them again would see what the callee left there, which the
        lifter refuses (dread.lift_msp430: the C passes copies)."""
        rng = self.rng
        helper = rng.choice(["h0", "h1", "h2", "h3"])
        keep = {"r12", "r13"} if helper == "h1" else {"r12"}
        saved = ["r5", "r6", "r7", "r8", "r9"]
        lines = ["call #%s" % helper]
        if helper == "h3":
            words = [rng.choice(saved) if rng.random() < 0.7 else "#%d" % self.imm() for _ in range(2)]
            if rng.random() < 0.5:
                lines = ["push %s" % words[1], "push %s" % words[0]] + lines
            else:
                lines = ["sub #4, r1", "mov %s, 2(r1)" % words[1], "mov %s, 0(r1)" % words[0]] + lines
            lines.append("add #4, r1")
        for r in ["r11", "r12", "r13", "r14", "r15"]:
            if r not in keep:
                lines.append("mov %s, %s" % (rng.choice(saved), r))
        return lines + ["cmp %s, %s" % (rng.choice(saved), rng.choice(saved))]   # the callee's flags: unknown

    def body(self, name, length):
        units, n = [], 0
        while n < length:
            lines, k = self.unit(name)
            units.append(lines)
            n += k
        return units

    def fp_number(self):
        return 4

    def prelude(self):
        return [".text"]

    def helpers(self):
        lines = []
        for name, body in MSP_HELPERS:
            lines += [".p2align 1", ".global %s" % name, ".type %s, @function" % name, "%s:" % name] + body
            lines += [".size %s, . - %s" % (name, name)]
        # a chain of shared epilogues as GCC's __mspabi_func_epilog_N: each pops one register and
        # falls into the next; the last returns
        for k, reg in enumerate(MSP_SAVED):
            name = "epilog_%d" % (len(MSP_SAVED) - k)
            lines += [".global %s" % name, ".type %s, @function" % name, "%s:" % name, "pop %s" % reg,
                      ".size %s, . - %s" % (name, name)]
        return lines[:-1] + ["ret", lines[-1]]

    def function(self, name, units, buf, result, style):
        lines = [".p2align 1", ".global %s" % name, ".type %s, @function" % name, "%s:" % name]
        lines += ["push %s" % r for r in reversed(MSP_SAVED)]        # r4 on top, as GCC's
        lines.append("sub #%d, r1" % FRAME)
        lines += ["mov r12, %d(r1)" % k for k in range(0, FRAME, 2)]
        lines += ["mov r12, r5", "mov r13, r6", "mov r14, r7", "mov r15, r8", "mov r12, r9", "add r13, r9",
                  "mov r14, r11", "xor r15, r11", "mov r13, r4", "sub r12, r4", "mov #%d, r10" % buf]
        if "fp" in style:
            lines.append("mov r1, r4")                                # the frame: slots at X(r4)
        lines += [l for unit in units for l in unit]
        if "fp" in style:
            lines += ["mov r4, r1", "mov #0, r4"]                     # a stack address differs by design
        regs = ["r%d" % i for i in range(4, 16)]
        lines += ["mov %s, &0x%x" % (r, result + 2 * k) for k, r in enumerate(regs)]
        lines += ["mov r2, r11", "and #0x107, r11", "mov r11, &0x%x" % (result + 2 * len(regs))]
        lines.append("add #%d, r1" % FRAME)
        if "ret" in style:
            lines.append("br #epilog_%d" % len(MSP_SAVED))
        else:
            lines += ["pop %s" % r for r in MSP_SAVED] + ["ret"]
        return lines + [".size %s, . - %s" % (name, name)]


GENERATORS = {"a32": None, "t32": None, "m4": None, "m4f": None, "rv32": RiscVGenerator,
              "msp430": Msp430Generator}   # None: ARM


def generator(isa, rng):
    return (GENERATORS.get(isa) or Generator)(isa, rng)


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
    gen = generator(isa, rng)
    buf = ISAS[isa]["ram"] + 0x1000
    bodies, cases = {}, {}
    for i in range(functions):
        name = "f%d" % i
        style = "".join(k for k in ("fp", "ret") if rng.random() < 0.3)
        gen.fp = gen.fp_number() if "fp" in style else None
        bodies[name] = (style, gen.body(name, length))
        cases[name] = []
        for _ in range(inputs):
            args = [rng.choice(EDGES) if rng.random() < 0.4 else rng.getrandbits(32)
                    for _ in range(4 + getattr(gen, "stack_words", 0))]
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
    gen = generator(isa, random.Random(0))
    buf, result = conf["ram"] + 0x1000, conf["ram"] + 0x2000
    riscv, msp = isa == "rv32", isa == "msp430"
    text = gen.prelude() if riscv or msp else [".syntax unified", ".thumb" if gen.thumb else ".arm", ".text"]
    for name, (style, units) in bodies.items():
        text += gen.function(name, units, buf, result, style)
    text += gen.helpers() if riscv or msp else helpers(gen.thumb)
    os.makedirs(out, exist_ok=True)
    source = os.path.join(out, "fuzz.S")
    with open(source, "w") as f:
        f.write("\n".join(l if l.endswith(":") or l.startswith(".") else "    " + l for l in text) + "\n")
    elf_path = os.path.join(out, "fuzz.elf")
    first = next(iter(bodies))
    if riscv:
        cmd = ["clang"] + conf["flags"] + ["-nostdlib", "-fuse-ld=lld", "-Wl,-Ttext=0x%x" % conf["base"],
                                           "-Wl,-e," + first, "-o", elf_path, source]
    elif msp:                           # a script of its own: the default's text region starts at 0x8000
        script = os.path.join(out, "fuzz.ld")
        with open(script, "w") as f:
            f.write("MEMORY { text (rx) : ORIGIN = 0x%x, LENGTH = 0x%x }\n"
                    "SECTIONS { .text : { *(.text .text.*) } > text }\n" % (conf["base"], 0xff00 - conf["base"]))
        cmd = ["msp430-elf-gcc"] + conf["flags"] + ["-nostdlib", "-T", script, "-Wl,-e," + first, "-o", elf_path,
                                                    source] + ["-Wl,-u," + h for h in MSP_LIBGCC] + ["-lgcc"]
    else:
        cmd = ["arm-none-eabi-gcc"] + conf["flags"] + ["-nostdlib", "-Wl,-Ttext=0x%x" % conf["base"],
                                                       "-Wl,-e," + first, "-Wl,--no-warn-rwx-segments", "-o", elf_path,
                                                       source]
    got = subprocess.run(cmd, capture_output=True, text=True)
    if got.returncode:
        raise RuntimeError("the assembler refused the generated code:\n" + got.stderr)
    cflags = [f for f in conf["flags"] if not f.startswith("--target=")] + \
        ["-Os", "-ffreestanding", "-fno-builtin", "-ffunction-sections", "-fno-common"]
    toml = conf["toml"].replace('entry = "f0"', 'entry = "%s"' % first)
    compiler = 'kind = "clang"\ntriple = "riscv32-unknown-elf"' if riscv else \
        'cross = "msp430-elf-"\nldflags = []' if msp else 'cross = "arm-none-eabi-"'
    toml += '\n[compiler]\n%s\ncflags = [%s]\n' % (compiler, ", ".join('"%s"' % f for f in cflags))
    with open(os.path.join(out, "dread.toml"), "w") as f:
        f.write(toml)
    target = Target.load(out)
    image = target.image()
    elf = Elf(elf_path)
    funcs, code, symbols = elfimport.tables(elf, isa=image.arch.isa)
    elfimport.write_tables(image, funcs, code, symbols, header="dread liftfuzz")
    data, _ = elf.payload(image.base, image.erased)
    with open(os.path.join(out, "fuzz.bin"), "wb") as f:
        f.write(data)
    os.makedirs(image.extra_cases, exist_ok=True)
    for name in bodies:
        with open(os.path.join(image.extra_cases, name + ".json"), "w") as f:
            json.dump({"cases": cases[name]}, f)
    return target, image


def check(isa, work, bodies, cases, jobs=None, generate=True):
    """Make the image in `work`, lift every function, build and compare: ({name: the runner's
    FAIL block}, {name: refusal}, [names with an instruction no input ran]). generate=False: the
    written cases alone, nothing generated (the reduction's first try)."""
    from dread import build, lift, regflow, runner
    from dread.program import Program
    target, image = make(isa, work, bodies, cases)
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    names, refused = [], {}
    for fn in program.functions:
        try:
            lifter.lift(fn)
            names.append(fn.name)
        except lift.Refused as e:
            refused[fn.name] = str(e)
    names = [n for n in names if n in bodies]          # the helpers stay as they are
    refused = {n: r for n, r in refused.items() if n in bodies}
    failures = {}
    if names:
        from dread.overlay import OverlayError
        try:
            payload = build.build(target, image, lifted=names, quiet=True, lifter=lifter)
        except OverlayError as e:           # the C needs what the image does not hold (a libgcc helper)
            lines = str(e).splitlines()
            for name in names:
                own = [line for line in lines if "/%s.o:" % name in line or "%s.c:" % name in line]
                if own:
                    failures[name] = "FAIL %s  the build failed:\n%s" % (name, "\n".join(own))
            return failures or {n: "FAIL %s  the build failed: %s" % (n, str(e)) for n in names}, refused, []
        runner.prepare(target, image, payload)
        if not generate:
            for name in names:
                runner.RUN["suite"].auto.setdefault(name, {})["generate"] = False
        blocks = []
        _, uncovered, results = runner.run(names, only=True, out=blocks.append, jobs=jobs)
        bad = {name for name, count, fails in results if fails}
        for block in blocks:
            if block.startswith("FAIL") and block.split()[1] in bad:
                failures.setdefault(block.split()[1], block)
    else:
        uncovered = []
    return failures, refused, uncovered


def minimize(isa, body, cases, work, jobs=None, hints=()):
    """The shortest run of units that still differs or is refused: the shortest failing prefix,
    then every unit left out whose absence keeps it failing. Returns (style, units, builds).

    hints: generated inputs that showed the failure. Each try runs them with the written inputs
    first, generating nothing; only a try they do not fail is checked with generated cases as
    well. Most tries end at the first step: generating cases for every try took 40 of a reduction's
    50 seconds. A try counts as failing when either step fails, so the result is never longer than
    with generated cases alone."""
    style, units = body
    trials = [0]

    def fails(us):
        trials[0] += 1
        here = os.path.join(work, "trial")
        if hints:
            shutil.rmtree(here, ignore_errors=True)
            failures, refused, _ = check(isa, here, {"f0": (style, us)}, {"f0": list(cases) + list(hints)}, jobs,
                                         generate=False)
            if failures or refused:
                return True
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


def failing_inputs(work, name, block):
    """The generated cases a FAIL block names ([generated N]), from the cache of the check in `work`:
    inputs for the reduction's first try. Cases with stubs are left out (a stub's address is a
    callee's, which a shorter body moves)."""
    import glob
    import json
    numbers = [int(n) for n in re.findall(r"\[generated (\d+)\]", block)]
    found = []
    for path in glob.glob(os.path.join(work, "build", "autocases", "*", name + ".json")):
        try:
            with open(path) as f:
                made = json.load(f).get("cases", [])
        except (OSError, ValueError):
            continue
        found += [made[n] for n in numbers if n < len(made) and not made[n].get("calls")]
    return found


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
        hints = failing_inputs(work, name, failures.get(name, ""))
        style, units, trials = minimize(isa, bodies[name], cases[name], os.path.join(work, "reduce-" + name), jobs,
                                        hints)
        out("reduced  %-6s in %d builds to: %s%s" % (name, trials, "[%s] " % style if style else "",
                                                     " | ".join("; ".join(u) for u in units)))
    if failures or refused or keep:
        out("kept in %s (fuzz.S, build/recon/main/lifted/)" % work)
        return failures, refused, work
    shutil.rmtree(work, ignore_errors=True)
    return failures, refused, None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread liftfuzz", description=__doc__.split("\n\n")[0])
    parser.add_argument("--isa", choices=sorted(ISAS), action="append",
                        help="a32, t32, m4, m4f, rv32, msp430 (default: those whose toolchain is installed)")
    parser.add_argument("--functions", type=int, default=100)
    parser.add_argument("--length", type=int, default=40, help="random instructions per function")
    parser.add_argument("--inputs", type=int, default=16, help="written cases per function")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--reduce", type=int, default=2, help="failures reduced to their shortest form (default 2)")
    parser.add_argument("--keep", help="work in this directory and keep it")
    args = parser.parse_args(argv)
    tools = {isa: ("clang", "ld.lld") if isa == "rv32" else ("msp430-elf-gcc",) if isa == "msp430" else
             ("arm-none-eabi-gcc",) for isa in ISAS}
    isas = args.isa or [isa for isa in sorted(ISAS) if all(shutil.which(t) for t in tools[isa])]
    missing = [t for isa in isas for t in tools[isa] if not shutil.which(t)]
    if missing or not isas:
        print("dread liftfuzz: needs %s" % (", ".join(sorted(set(missing))) or "a toolchain"), file=sys.stderr)
        return 1
    from dread.util.polite import polite
    polite()
    bad = 0
    for isa in isas:
        keep = os.path.join(args.keep, isa) if args.keep else None
        failures, refused, _ = run(isa, args.functions, args.length, args.inputs, args.seed, keep, reduce=args.reduce)
        bad += len(failures) + len(refused)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
