"""The MSP430 layer: the 16-bit MSP430 (the 20-bit MSP430X extension words are not decoded yet).

Neither capstone nor Unicorn knows the MSP430, so the layer decodes the instructions itself: three
formats (two operands, one operand, jumps), seven addressing modes (register, indexed, symbolic,
absolute, indirect, indirect with increment, immediate) and the constant generators (r2 and r3 give
0, 1, 2, 4, 8 and -1 without an extension word). The text is what llvm-objdump prints (aliases such
as clr, tst, inc, pop, ret, br, eint included), which llvm-mc reads back as the same encoding: the
listing and the decoder's test rest on that (tests/test_msp430.py checks the text against
llvm-mc's disassembler).

Registers: r0 is pc, r1 sp, r2 sr (the flags: C bit 0, Z bit 1, N bit 2, GIE bit 3, V bit 8), r3 the
constant generator; r4-r15 hold values. The calling convention (the MSP430 EABI): arguments in
r12-r15, results in r12 (r12:r13 for 32 bits, r12-r15 for 64), r4-r10 kept, r11-r15 not. A call
pushes the return address; there is no link register.

Constructed from [arch]: isa ("msp430"), endian, ghidra.
"""
import re
import struct

NAMES = ["pc", "sp", "sr", "cg"] + ["r%d" % i for i in range(4, 16)]
NUMBER = {name: i for i, name in enumerate(NAMES)}
NUMBER.update({"r%d" % i: i for i in range(16)})
DOUBLE = {4: "mov", 5: "add", 6: "addc", 7: "subc", 8: "sub", 9: "cmp", 0xA: "dadd", 0xB: "bit", 0xC: "bic",
          0xD: "bis", 0xE: "xor", 0xF: "and"}
SINGLE = {0: "rrc", 1: "swpb", 2: "rra", 3: "sxt", 4: "push", 5: "call", 6: "reti"}
JUMPS = {0: "jne", 1: "jeq", 2: "jlo", 3: "jhs", 4: "jn", 5: "jge", 6: "jl", 7: "jmp"}
WRITES_DST = ("mov", "add", "addc", "subc", "sub", "dadd", "bic", "bis", "xor", "and")     # not cmp, bit
READS_DST = ("add", "addc", "subc", "sub", "cmp", "dadd", "bit", "bic", "bis", "xor", "and")


class Operand:
    """mode: "reg", "idx" (X(rN)), "sym" (X(pc): the address in `address`, X counted from the
    extension word's own address), "abs" (&X), "ind" (@rN),
    "inc" (@rN+), "imm" (#X, from a constant generator when `cg`). reg: its register number; value:
    X, the immediate, or the absolute address."""

    __slots__ = ("mode", "reg", "value", "cg", "address")

    def __init__(self, mode, reg, value=0, cg=False, address=None):
        self.mode, self.reg, self.value, self.cg, self.address = mode, reg, value, cg, address

    def __repr__(self):
        return "Operand(%s r%d %#x)" % (self.mode, self.reg, self.value)


class Fields:
    """op (the base instruction: mov, rrc, jne, ...), byte (.b), src and dst (Operands or None),
    size (bytes), target (a jump's destination)."""

    __slots__ = ("op", "byte", "src", "dst", "size", "target")

    def __init__(self, op, byte=False, src=None, dst=None, size=2, target=None):
        self.op, self.byte, self.src, self.dst, self.size, self.target = op, byte, src, dst, size, target


def _sign16(v):
    return v - 0x10000 if v & 0x8000 else v


def _source(as_, reg, words, at, address, byte):
    """(Operand, words used) of a source; `at` is where its extension word would be."""
    if reg == 3:                            # the constant generator: no extension word
        value = {0: 0, 1: 1, 2: 2, 3: 0xFF if byte else 0xFFFF}[as_]
        return Operand("imm", 3, value, cg=True), 0
    if reg == 2 and as_ >= 2:
        return Operand("imm", 2, 4 if as_ == 2 else 8, cg=True), 0
    if as_ == 0:
        return Operand("reg", reg), 0
    if as_ == 1:
        if len(words) <= at:
            return None, 0
        x = words[at]
        if reg == 2:
            return Operand("abs", 2, x), 1
        if reg == 0:
            return Operand("sym", 0, _sign16(x), address=(address + 2 * at + _sign16(x)) & 0xFFFF), 1
        return Operand("idx", reg, _sign16(x)), 1
    if as_ == 2:
        return Operand("ind", reg), 0
    if reg == 0:                            # @pc+: an immediate
        if len(words) <= at:
            return None, 0
        return Operand("imm", 0, words[at]), 1
    return Operand("inc", reg), 0


def decode_fields(code, address):
    """Fields of the instruction at the start of `code`, or Fields("unknown")."""
    if len(code) < 2:
        return Fields("unknown")
    words = list(struct.unpack_from("<%dH" % min(3, len(code) // 2), code))
    w = words[0]
    top = w >> 12
    if top >= 4:                            # two operands
        op = DOUBLE[top]
        rs, ad, byte, as_, rd = (w >> 8) & 15, (w >> 7) & 1, bool(w & 0x40), (w >> 4) & 3, w & 15
        src, used = _source(as_, rs, words, 1, address, byte)
        if src is None:
            return Fields("unknown")
        at = 1 + used
        if ad == 0:
            dst = Operand("reg", rd)
        else:
            if len(words) <= at:
                return Fields("unknown")
            x = words[at]
            if rd == 2:
                dst = Operand("abs", 2, x)
            elif rd == 0:
                dst = Operand("sym", 0, _sign16(x), address=(address + 2 * at + _sign16(x)) & 0xFFFF)
            else:
                dst = Operand("idx", rd, _sign16(x))
            at += 1
        return Fields(op, byte, src, dst, 2 * at)
    if (w & 0xE000) == 0x2000:              # jumps
        offset = w & 0x3FF
        offset = offset - 0x400 if offset & 0x200 else offset
        return Fields(JUMPS[(w >> 10) & 7], target=(address + 2 + 2 * offset) & 0xFFFF)
    if (w & 0xFC00) == 0x1000:              # one operand
        code_ = (w >> 7) & 7
        if code_ == 6:
            return Fields("reti") if w == 0x1300 else Fields("unknown")
        if code_ == 7:
            return Fields("unknown")        # MSP430X (calla)
        op = SINGLE[code_]
        byte, as_, reg = bool(w & 0x40), (w >> 4) & 3, w & 15
        if byte and op in ("swpb", "sxt", "call"):
            return Fields("unknown")
        operand, used = _source(as_, reg, words, 1, address, byte)
        if operand is None:
            return Fields("unknown")
        return Fields(op, byte, operand, None, 2 * (1 + used))
    return Fields("unknown")                # MSP430X: address instructions, pushm/popm, extension words


def length_of(w):
    """The instruction's length in bytes from its first word alone: the addressing modes say how
    many extension words follow (indexed, symbolic and absolute operands and immediates take one;
    the constant generators none)."""
    def source_words(as_, reg):
        return 1 if (as_ == 1 and reg != 3) or (as_ == 3 and reg == 0) else 0
    if w >> 12 >= 4:
        return 2 * (1 + source_words((w >> 4) & 3, (w >> 8) & 15) + ((w >> 7) & 1))
    if (w & 0xFC00) == 0x1000 and ((w >> 7) & 7) < 6:
        return 2 * (1 + source_words((w >> 4) & 3, w & 15))
    return 2


def _text(operand, byte):
    if operand.mode == "reg":
        return "r%d" % operand.reg
    if operand.mode == "imm":
        if operand.cg:
            return "#%d" % (-1 if operand.value in (0xFF, 0xFFFF) else operand.value)
        return "#%d" % operand.value
    if operand.mode == "abs":
        return "&%d" % operand.value
    if operand.mode in ("idx", "sym"):
        return "%d(r%d)" % (operand.value, operand.reg)
    if operand.mode == "ind":
        return "@r%d" % operand.reg
    return "@r%d+" % operand.reg


def text_of(f, address):
    """(mnemonic, operands) as llvm-objdump prints them, aliases included."""
    op, suffix = f.op, ".b" if f.byte else ""
    if op == "unknown":
        return "<unknown>", ""
    if f.target is not None:
        delta = (f.target - address) & 0xFFFF
        delta = delta - 0x10000 if delta & 0x8000 else delta
        return op, "$%+d" % delta
    if op == "reti":
        return "reti", ""
    src, dst = f.src, f.dst
    if dst is None:                         # one operand
        return op + suffix, _text(src, f.byte)
    s, d = _text(src, f.byte), _text(dst, f.byte)
    imm = src.value if src.mode == "imm" else None
    cg = src.mode == "imm" and src.cg
    if op == "mov":
        if src.mode == "inc" and src.reg == 1 and dst.mode == "reg":
            if dst.reg == 0 and not f.byte:
                return "ret", ""
            return "pop" + suffix, d
        if dst.mode == "reg" and dst.reg == 0 and not f.byte:
            return "br", s
        if cg and imm == 0:
            if dst.mode == "reg" and dst.reg == 3 and not f.byte:
                return "nop", ""
            return "clr" + suffix, d
    if op == "cmp" and cg and imm == 0:
        return "tst" + suffix, d
    if op in ("add", "sub") and cg and imm in (1, 2):
        return ("inc" if op == "add" else "dec") + ("d" if imm == 2 else "") + suffix, d
    if op == "xor" and cg and imm in (0xFF, 0xFFFF):
        return "inv" + suffix, d
    if op in ("addc", "subc", "dadd") and cg and imm == 0:
        return {"addc": "adc", "subc": "sbc", "dadd": "dadc"}[op] + suffix, d
    if op in ("bic", "bis") and cg and dst.mode == "reg" and dst.reg == 2 and not f.byte \
            and imm in (1, 2, 4, 8):
        bit = {1: "c", 2: "z", 4: "n", 8: "int"}[imm]
        if bit == "int":
            return ("dint" if op == "bic" else "eint"), ""
        return ("clr" if op == "bic" else "set") + bit, ""
    return op + suffix, "%s, %s" % (s, d)


class Insn:
    """One decoded instruction: address, size, bytes, mnemonic, op_str (llvm-objdump's text) and
    its Fields (fields)."""

    __slots__ = ("address", "size", "bytes", "mnemonic", "op_str", "fields", "__dict__")

    def __init__(self, address, code, f):
        self.address, self.size, self.fields = address, f.size, f
        self.bytes = bytes(code[:f.size])
        self.mnemonic, self.op_str = text_of(f, address)


class _Disassembler:
    def disasm(self, code, address, count=0):
        code, pos, n = bytes(code), 0, 0
        while pos + 2 <= len(code):
            f = decode_fields(code[pos:pos + 6], address + pos)
            if f.op == "unknown" or pos + f.size > len(code):
                return
            yield Insn(address + pos, code[pos:pos + f.size], f)
            pos += f.size
            n += 1
            if count and n >= count:
                return


def fields(insn):
    return insn.fields


# The MSP430 EABI's integer runtime helpers by the width of their result (TI's "MSP430 Embedded
# Application Binary Interface"; GCC's libgcc implements them): multiply, divide, remainder and
# shifts of 16-bit (i, u), 32-bit (l) and 64-bit (ll) operands, and the widening multiplies.
ABI_RESULTS = {}
for _regs, _names in ((("r12",), "mpyi divi divu remi remu slli srai srli"),
                      (("r12", "r13"), "mpyl mpysl mpyul divli divul remli remul slll sral srll"),
                      (("r12", "r13", "r14", "r15"),
                       "mpyll mpysll mpyull divlli divull remlli remull sllll srall srlll")):
    for _n in _names.split():
        ABI_RESULTS["__mspabi_" + _n] = _regs


class Msp430:
    """The MSP430 layer. Constructed from [arch]: isa, endian, ghidra."""

    name = "msp430"
    default_args = 4
    default_callee_saved = ("r4", "r5", "r6", "r7", "r8", "r9", "r10")
    default_result = ("r12", "r13")
    word = 2
    reg_bits = 16

    def vector_entries(self, read_word, vectors):
        """[(index, address, "reset" or "handler")] of the vector table at `vectors` (16 words below
        0x10000, the reset vector last); unprogrammed words (0xffff, 0) are left out. read_word(address)
        gives a word or None where nothing is loaded."""
        out = []
        for i in range(16):
            word = read_word(vectors + 2 * i)
            if word is None or word in (0, 0xFFFF):
                continue
            out.append((i, word & ~1, "reset" if i == 15 else "handler"))
        return out
    link_reg = None             # a call pushes the return address
    own_epilogues = ("__mspabi_func_epilog_",)  # GCC's shared epilogues, which compiled C jumps to
    # Flags the lifted C needs from a compiler besides -Os ([compiler] lifted_cflags' default): GCC's
    # tree phi optimisation turns a flag's `z ? 1 : 0` into (-z | z) >> 15, and a shift by 15 is a
    # libgcc call (__mspabi_srli) the original does not make, which drops the function (FreeRTOS's
    # build: prvIsQueueEmpty, vParTestToggleLED, the two queue tests from interrupts); without it the
    # C is a branch, and shorter
    lifted_cflags = {"gcc": ["-fno-ssa-phiopt"]}

    def abi_results(self, name):
        """The registers of a runtime helper's result by the MSP430 EABI (16 bits in r12, 32 in
        r12:r13, 64 in r12-r15), whatever the original's own callers read of it: compiled C calls
        the helper by this contract (gizmo's parse_uint, rewritten in C, multiplies through
        __mspabi_mpyl and reads r13, which the original's callers never did). dread.regflow takes
        these registers as read after every call. () for anything else."""
        base = re.sub(r"_\d+$", "", name)      # the fixed-count shifts: __mspabi_srli_4
        return ABI_RESULTS.get(base, ())
    veneer_reg = "r11"
    profile = None

    def __init__(self, isa="msp430", endian="little", ghidra=None, cpu=None, extensions=None):
        if isa != "msp430":
            raise ValueError("isa is msp430, not %r (MSP430X is not supported yet)" % (isa,))
        if endian != "little":
            raise ValueError("only endian = \"little\" is supported")
        self.isa, self.endian, self.cpu = isa, endian, cpu
        self.ghidra = ghidra or "TI_MSP430:LE:16:default"
        self.ghidra_cspec = "default"

    def configure(self, cflags):
        pass

    def describe(self):
        return "msp430, 16-bit, little-endian"

    # ---- registers ----

    def reg_name(self, name):
        name = name.lower()
        if name not in NUMBER:
            raise ValueError("unknown MSP430 register %r" % name)
        return NAMES[NUMBER[name]]

    def reg_id(self, name):
        """The engine's register id: the register number (dread.engines.msp430)."""
        return NUMBER[self.reg_name(name)]

    def arg_regs(self, n):
        return ["r%d" % (12 + i) for i in range(n)]

    def general_regs(self):
        return ["r%d" % i for i in range(4, 16)]

    @staticmethod
    def _name(name):
        return NAMES[NUMBER[name]] if name in NUMBER else name

    # ---- decoding ----

    def isas(self):
        return ("msp430",)

    def min_insn(self, isa):
        return 2

    def insn_length(self, code, isa):
        """From the first word alone (length_of)."""
        return length_of(code[0] | code[1] << 8)

    def disassembler(self, isa=None):
        return _Disassembler()

    def decode(self, code, address, isa=None):
        for insn in _Disassembler().disasm(bytes(code), address, 1):
            return insn
        return None

    def pc_value(self, address, isa):
        return address

    # ---- what an instruction does ----

    def flow(self, insn):
        """(kind, target, falls) as Arm.flow documents: call #imm a call, call through anything
        else an icall; ret (mov @sp+, pc) a return; br #imm (mov #imm, pc) a jump, a move of
        anything else into pc an indirect jump; reti a return from an interrupt; jmp a jump, the
        other jumps conditional."""
        f = insn.fields
        op = f.op
        if f.target is not None:
            return ("jump", f.target, op != "jmp")
        if op == "reti":
            return "return", None, False
        if op == "call":
            if f.src.mode == "imm" and not f.src.cg:
                return "call", f.src.value, True
            return "icall", None, True
        if op == "mov" and f.dst is not None and f.dst.mode == "reg" and f.dst.reg == 0 and not f.byte:
            if f.src.mode == "inc" and f.src.reg == 1:
                return "return", None, False
            if f.src.mode == "imm" and not f.src.cg:
                return "jump", f.src.value, False
            return "ijump", None, False
        if f.dst is not None and f.dst.mode == "reg" and f.dst.reg == 0 and op in WRITES_DST:
            return "ijump", None, False     # pc computed (add rN, pc: a table)
        if op == "unknown":
            return "stop", None, False
        return "next", None, False

    def target(self, insn):
        kind, target, _ = self.flow(insn)
        return target if kind in ("jump", "call") else None

    def is_call(self, insn):
        return self.flow(insn)[0] in ("call", "icall")

    def register_call_targets(self, program, fn):
        """{address of a call through a register: [function entries]} for the calls of fn through
        a register whose every write in fn (pops aside: the epilogue's restores) is mov #entry: GCC
        loads a function it calls more than once into a register (mov #uart_puts, r10; call r10).
        Flow-insensitive: each entry moved into the register counts, which can name one too many,
        never one too few."""
        moved, other, calls = {}, set(), []
        for insn in program.instructions(fn):
            if insn is None:
                continue
            f = insn.fields
            if self.flow(insn)[0] == "icall":
                if f.src is not None and f.src.mode == "reg" and f.src.reg >= 4:
                    calls.append((insn.address, f.src.reg))
                continue
            if self.stack_kind(insn) == "pop":
                continue
            reads, writes = self.reg_effects(insn)
            for name in writes:
                if name not in NAMES[4:]:
                    continue
                reg = NAMES.index(name)
                if f.op == "mov" and not f.byte and f.src.mode == "imm" and f.dst.mode == "reg" \
                        and f.dst.reg == reg and (f.src.value & 0xFFFF) in program.by_entry:
                    moved.setdefault(reg, set()).add(f.src.value & 0xFFFF)
                else:
                    other.add(reg)
        return {a: sorted(moved[r]) for a, r in calls if r in moved and r not in other}

    def is_conditional(self, insn):
        return False

    def predicate(self, insn):
        return None

    @staticmethod
    def complement(predicate):
        return None

    def condition(self, insn):
        return None

    def switches_isa(self, insn):
        return False

    def link_pair(self, prev, insn, isa):
        return False

    def reg_effects(self, insn):
        """(reads, writes) by name, the flags as "flags" (sr's C, Z, N and V)."""
        f = insn.fields
        reads, writes = set(), set()

        def address_regs(o):
            if o.mode in ("idx", "ind", "inc") and o.reg != 3:
                reads.add(NAMES[o.reg])
            if o.mode == "inc":
                writes.add(NAMES[o.reg])

        op = f.op
        if f.target is not None:
            if op != "jmp":
                reads.add("flags")
            return reads, writes
        if op == "reti":
            return {"sp"}, {"sp", "sr", "flags", "pc"}
        if f.src is not None:
            if f.src.mode == "reg":
                reads.add(NAMES[f.src.reg])
            address_regs(f.src)
        if f.dst is None:                   # one operand
            if op in ("rrc", "swpb", "rra", "sxt") and f.src.mode == "reg":
                writes.add(NAMES[f.src.reg])
            if op == "rrc":
                reads.add("flags")
            if op in ("push", "call"):
                reads.add("sp")
                writes.add("sp")
            if op in ("rrc", "rra", "sxt"):
                writes.add("flags")
            if op == "call":
                writes |= {"r11", "r12", "r13", "r14", "r15", "flags"}
            return reads - {"cg"}, writes - {"cg"}
        if f.dst.mode == "reg":
            if op in ("sub", "subc") and f.src.mode == "reg" and f.src.reg == f.dst.reg:
                reads.discard(NAMES[f.src.reg])     # sub r, r is 0 and subc r, r is C - 1 whatever r
            elif op in READS_DST:                   # holds (GCC's sign extension); xor's V reads it
                reads.add(NAMES[f.dst.reg])
            if op in WRITES_DST:
                writes.add(NAMES[f.dst.reg])
        else:
            address_regs(f.dst)
        if op in ("addc", "subc", "dadd"):
            reads.add("flags")
        if op not in ("mov", "bic", "bis"):
            writes.add("flags")
        if "sr" in writes:
            writes.add("flags")
        if "sr" in reads:
            reads.add("flags")
        return reads - {"cg"}, writes - {"cg"}

    def sets_flags(self, insn):
        return "flags" in self.reg_effects(insn)[1]

    FLAG_NAMES = ("c", "z", "n", "v")       # sr's flags one by one, for dread.regflow (flag_effects)
    FLAG_BITS = {"c": 1, "z": 2, "n": 4, "v": 0x100}
    FLAGS_ALL = frozenset(FLAG_NAMES)
    FLAGS_READ = {"addc": {"c"}, "subc": {"c"}, "dadd": {"c"}, "rrc": {"c"}}
    FLAGS_WRITTEN = {"add": FLAGS_ALL, "addc": FLAGS_ALL, "sub": FLAGS_ALL, "subc": FLAGS_ALL, "cmp": FLAGS_ALL,
                     "and": FLAGS_ALL, "xor": FLAGS_ALL, "bit": FLAGS_ALL, "rrc": FLAGS_ALL, "rra": FLAGS_ALL,
                     "sxt": FLAGS_ALL, "dadd": frozenset("czn")}
    JUMP_FLAGS = {"jmp": (), "jne": "z", "jeq": "z", "jlo": "c", "jhs": "c", "jn": "n", "jge": "nv", "jl": "nv"}

    def flag_effects(self, insn):
        """(read, written): sr's flags one by one (C, Z, N, V as "c", "z", "n", "v"), for dread.regflow;
        a flag neither read nor written passes through. reg_effects has them as one unit "flags",
        so clrc (bic #1, sr), which writes C and passes Z, N and V through, read them all there: the
        logical right shifts of libgcc begin with it, and their callers looked as if they had to
        leave the flags for them, although the rrc behind clrc writes all four."""
        f = insn.fields
        op = f.op
        if f.target is not None:
            return set(self.JUMP_FLAGS[op]), set()
        if op in ("reti", "call"):
            return set(), set(self.FLAGS_ALL)       # sr restored; a callee may change them
        read = set(self.FLAGS_ALL) if f.src is not None and f.src.mode == "reg" and f.src.reg == 2 else set()
        d = f.dst
        if d is None:                       # one operand: rrc, rra, swpb, sxt, push, call
            if f.src is not None and f.src.mode == "reg" and f.src.reg == 2 and op in ("rrc", "rra", "swpb", "sxt"):
                return set(self.FLAGS_ALL), set(self.FLAGS_ALL)
            return read | self.FLAGS_READ.get(op, set()), set(self.FLAGS_WRITTEN.get(op, ()))
        if d.mode == "reg" and d.reg == 2:  # an instruction on sr itself
            if op in ("bic", "bis") and f.src.mode == "imm":
                return read, {n for n, bit in self.FLAG_BITS.items() if f.src.value & bit}
            if op == "mov":
                return read, set(self.FLAGS_ALL)
            return set(self.FLAGS_ALL), set(self.FLAGS_ALL)
        return read | self.FLAGS_READ.get(op, set()), set(self.FLAGS_WRITTEN.get(op, ()))

    def stack_kind(self, insn):
        """"push" for push, "pop" for pop (mov @sp+, rN), else None."""
        f = insn.fields
        if f.op == "push":
            return "push"
        if f.op == "mov" and f.src is not None and f.src.mode == "inc" and f.src.reg == 1 and \
                f.dst.mode == "reg" and f.dst.reg != 0:
            return "pop"
        return None

    def literal(self, insn, isa):
        """(address, size) of a source the instruction reads at a fixed address (absolute or
        symbolic), else None."""
        f = insn.fields
        src = f.src
        if src is not None and src.mode in ("abs", "sym") and f.op != "call":
            return (src.value if src.mode == "abs" else src.address), 1 if f.byte else 2
        return None

    def pc_address(self, insn, isa):
        return None

    def immediates(self, insn):
        """The immediates an instruction carries in an extension word (the constant generators'
        are left out): candidate input values for the case generator, and on MSP430 the way
        addresses come into registers (mov #uart_puts, r10; call r10)."""
        f = insn.fields
        return [o.value for o in (f.src, f.dst) if o is not None and o.mode == "imm" and not o.cg]

    def bound_check(self, insn, prior=(), known=None):
        return None

    # ---- what the case generator learns from the code (dread.autocase) ----

    generator_version = 1       # in the generated-case cache's key: bump when these change

    @staticmethod
    def _sp_adjust(f):
        """How far an instruction lowers sp by a constant (sub #N, sp; add #-N, sp), else 0."""
        if f.dst is None or f.dst.mode != "reg" or f.dst.reg != 1 or f.src is None or f.src.mode != "imm" or f.byte:
            return 0
        if f.op == "sub":
            return f.src.value
        if f.op == "add" and f.src.value & 0x8000:
            return 0x10000 - f.src.value
        return 0

    def prologue_frame(self, program, fn, look=32):
        """The stack a function's prologue reserves for locals: what sub #N, sp lowers sp by behind
        the pushes of the registers it keeps (those are not locals)."""
        total = 0
        for a in fn.insns[:look]:
            insn = program.insn(a)
            if insn is None or self.flow(insn)[0] != "next":
                break
            total += self._sp_adjust(insn.fields)
        return total

    def switch_hints(self, insns):
        """(bounds, table): small constants a compare uses (cmp #k, rN in front of a switch), and
        whether the code jumps through a register or a computed pc (a table)."""
        limits, table = set(), False
        for ins in insns:
            f = ins.fields
            if f.op == "cmp" and f.src is not None and f.src.mode == "imm" and 0 < f.src.value <= 0xFF:
                limits.add(f.src.value)
            if self.flow(ins)[0] == "ijump":
                table = True
        return limits, table

    def compare_chars(self, insn):
        """Values that may be characters a parser checks: the constant of cmp (cmp.b #c, rN), of
        sub #c and of add #-c (rN - c, then compared with a bound)."""
        f = insn.fields
        src = f.src
        if src is None or f.dst is None or src.mode != "imm":
            return []
        if f.op in ("cmp", "sub"):
            return [src.value]
        if f.op == "add":
            return [-src.value & (0xFF if f.byte else 0xFFFF)]
        return []

    def branch_deciders(self, insns):
        """{conditional jump: (the instruction that sets its flags, first, second, imm, kind, 1)}:
        a cmp or bit right in front of it. cmp src, dst compares dst with src ("compare": first dst,
        second src), bit src, dst tests their common bits ("test"); a constant source is imm with
        second None. Only register operands: a memory operand is in no register to read."""
        out, prev = {}, None
        for ins in insns:
            f = ins.fields
            if f.target is not None and f.op != "jmp" and prev is not None and \
                    prev.address + prev.size == ins.address:
                p = prev.fields
                if p.op in ("cmp", "bit") and p.dst is not None and p.dst.mode == "reg" and p.dst.reg not in (0, 3):
                    second = imm = None
                    if p.src.mode == "reg" and p.src.reg not in (0, 3):
                        second, imm = p.src.reg, 0
                    elif p.src.mode == "imm":
                        imm = p.src.value
                    if imm is not None:
                        out[ins.address] = (prev.address, p.dst.reg, second, imm,
                                            "compare" if p.op == "cmp" else "test", 1)
            prev = ins
        return out

    def switch_targets(self, insn, program, inside):
        return None

    # ---- placing C ----

    def branch(self, src, dst, isa, link=False):
        """br #dst (mov #dst, pc; call #dst with link), 4 bytes: it reaches the whole 64 KiB."""
        if dst % 2:
            raise ValueError("no MSP430 branch to an odd address 0x%x" % dst)
        return struct.pack("<HH", 0x12B0 if link else 0x4030, dst & 0xFFFF)

    def branch_length(self, isa, src=None, span=None):
        return 4

    def entry_branch(self, src, dst, isa, room=None):
        return self.branch(src, dst, isa)

    def start_address(self, address, isa):
        return address

    def isa_of_symbol(self, value):
        return "msp430"

    @staticmethod
    def symbol_asm(name, address, isa):
        return "\t.global %s\n\t.type %s, @function\n\t.set %s, 0x%x\n" % (name, name, name, address)

    def asm(self):
        return Msp430Asm(self)

    # ---- the engine (dread.engines.msp430: Unicorn has no MSP430) ----

    def uc_mode(self, isa=None):
        return None

    def new_uc(self, isa=None):
        from dread.engines.msp430 import Msp430Engine
        return Msp430Engine()

    def initial_status(self, engine):
        """sr clear: no flags, interrupts off, the CPU running."""
        engine.reg_write(2, 0)

    def status_reg(self):
        return 2

    def settle_it(self, engine):
        pass

    def flags_value(self, engine):
        return engine.reg_read(2) & 0x107

    def pc_reg(self):
        return 0

    def push_return(self, engine, address, write):
        """Set up the return to `address` as a call does: pushed below sp (write(address, bytes)
        writes the memory)."""
        sp = (engine.reg_read(1) - 2) & 0xFFFE
        write(sp, struct.pack("<H", address & 0xFFFF))
        engine.reg_write(1, sp)

    def pop_return(self, engine, read):
        """Return from a stub at a function's entry as ret does: pc from the stack, sp behind it."""
        sp = engine.reg_read(1)
        engine.reg_write(0, int.from_bytes(read(sp, 2), "little"))
        engine.reg_write(1, sp + 2)

    # ---- the system run (dread.sysrun) ----
    # Words are read and written through the engine here: the system run's read_word and write_word
    # move four bytes, which at the reset vector (0xfffe) would run past the 64 KiB.

    GIE = 8                                 # sr bit 3: interrupts enabled

    def reset(self, engine, read_word, entry, vectors):
        """As the core comes out of reset: sr clear (interrupts off, the CPU on), pc from the reset
        vector, the table's last word (vectors + 0x1e)."""
        engine.reg_write(2, 0)
        return int.from_bytes(engine.mem_read(vectors + 0x1E, 2), "little") & 0xFFFE

    def interrupts_enabled(self, engine):
        """GIE: an interrupt may be taken. The handler runs with sr cleared, so it nests only where
        it sets GIE itself."""
        return bool(engine.reg_read(2) & self.GIE)

    def system_exception(self, intno):
        """The MSP430 has no system call, and the engine raises no exception of its own."""
        return None

    def enter_interrupt(self, engine, number, resume, vectors, read_word, write_word, cause=None):
        """Take interrupt `number` as the core does (TI's MSP430x2xx guide, 2.2.3): pc pushed, then
        sr, sr cleared (a low-power mode ends, GIE off), on at the word of vector `number` (its
        index from vectors: a controller's line N is vector N; gizmo's one line, 0, is the table's
        first). reti, at the handler's end, runs in the engine and restores sr, a low-power mode
        too unless the handler cleared CPUOFF in the stacked sr."""
        sp = (engine.reg_read(1) - 2) & 0xFFFE
        engine.mem_write(sp, struct.pack("<H", resume & 0xFFFF))
        sp = (sp - 2) & 0xFFFE
        engine.mem_write(sp, struct.pack("<H", engine.reg_read(2)))
        engine.reg_write(1, sp)
        engine.reg_write(2, 0)
        return int.from_bytes(engine.mem_read(vectors + 2 * number, 2), "little") & 0xFFFE

    def exception_return(self, engine, read_word):
        """Nothing to finish: reti runs in the engine."""
        return None

    def resume_address(self, engine):
        return engine.reg_read(0)


class Msp430Asm:
    """How dread asm writes MSP430 code: llvm-objdump's text, which llvm-mc reads back as the same
    encoding; jumps go to labels. What llvm-mc encodes otherwise (it has no @rN+ source with an
    indexed destination; it takes the constant generator wherever it can) the verify loop writes as
    raw words."""

    comment = ";"
    directives = {"word": ".long", "half": ".short", "byte": ".byte", "text": ".asciz"}

    def __init__(self, arch):
        self.arch = arch

    def prelude(self, isa):
        return []

    def switch(self, isa):
        return []

    def raw(self, b, isa):
        return ".short", ", ".join("0x%04x" % w for w in struct.unpack("<%dH" % (len(b) // 2), b))

    def function_lines(self, label, isa, code):
        return ["\t.type   %s, @function" % label]

    def operands(self, listing, insn, run, st, isa):
        f = insn.fields
        if f.target is not None:
            tst = listing.prog.stored(f.target)
            if listing.space(f.target) == listing.space(run) and listing.has(tst, 2) and listing.labelable(tst):
                listing.labels.add(tst)
                return insn.mnemonic, ("label", "", tst), ""
            return None
        return insn.mnemonic, insn.op_str, ""
