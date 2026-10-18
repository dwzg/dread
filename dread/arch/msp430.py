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
    """mode: "reg", "idx" (X(rN)), "sym" (X(pc): the address in `address`), "abs" (&X), "ind" (@rN),
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
            return Operand("sym", 0, _sign16(x), address=(address + 2 * (at + 1) + _sign16(x)) & 0xFFFF), 1
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
                dst = Operand("sym", 0, _sign16(x), address=(address + 2 * (at + 1) + _sign16(x)) & 0xFFFF)
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


class Msp430:
    """The MSP430 layer. Constructed from [arch]: isa, endian, ghidra."""

    name = "msp430"
    default_args = 4
    default_callee_saved = ("r4", "r5", "r6", "r7", "r8", "r9", "r10")
    default_result = ("r12", "r13")
    word = 2
    link_reg = None             # a call pushes the return address
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
            if op in READS_DST:
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

    def bound_check(self, insn):
        return None

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
