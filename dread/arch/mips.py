"""The MIPS layer: MIPS32 release 2, little endian, the o32 calling convention.

Capstone 5 gives the text of an instruction but not the registers it reads and writes (regs_access
is not implemented for MIPS), and it names coprocessor 0's registers as general ones ($t4 for
Status, $12): this layer decodes the bits itself (`fields`) for everything but the text, and writes
the operands of the forms capstone gets wrong for the assembler (MipsAsm).

The delay slot: a branch or jump (not eret) runs the instruction behind it before it goes, on both
ways of a conditional branch; a branch-likely (beql and kin) runs it only when it is taken.
`delay_slot(insn)` says which instructions have one. A call (jal, jalr, bal) returns behind its
slot: its arguments may be set in the slot.

The calling convention is o32: arguments in a0-a3 (and on the stack, where the caller reserves
16 bytes for the first four), results in v0 and v1, s0-s7 and fp (s8) kept, sp, gp kept, the
return address in ra. HI and LO hold a multiply's or a divide's result. There are no condition
flags: a branch compares registers itself.

Constructed from [arch]: isa ("mips32"), endian ("little"), ghidra.

Paused after step 1 of docs/porting.md (the user, 2026-10-20: ARM and RISC-V and compiler diversity
first): decoding, flow, the listing. The emulator, the case generator's hooks, the overlay's entry
branch and regflow's delay slots are under way on the branch `mips`.
"""
import struct

import capstone as cs

NAMES = ["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3", "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
         "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra"]
NUMBER = {name: i for i, name in enumerate(NAMES)}
NUMBER.update({str(i): i for i in range(32)})
NUMBER["s8"] = 30

SPECIAL = {0: "sll", 2: "srl", 3: "sra", 4: "sllv", 6: "srlv", 7: "srav", 8: "jr", 9: "jalr", 10: "movz",
           11: "movn", 12: "syscall", 13: "break", 15: "sync", 16: "mfhi", 17: "mthi", 18: "mflo", 19: "mtlo",
           24: "mult", 25: "multu", 26: "div", 27: "divu", 32: "add", 33: "addu", 34: "sub", 35: "subu",
           36: "and", 37: "or", 38: "xor", 39: "nor", 42: "slt", 43: "sltu", 48: "tge", 49: "tgeu", 50: "tlt",
           51: "tltu", 52: "teq", 54: "tne"}
REGIMM = {0: "bltz", 1: "bgez", 2: "bltzl", 3: "bgezl", 8: "tgei", 9: "tgeiu", 10: "tlti", 11: "tltiu",
          12: "teqi", 14: "tnei", 16: "bltzal", 17: "bgezal", 18: "bltzall", 19: "bgezall", 31: "synci"}
OPCODES = {2: "j", 3: "jal", 4: "beq", 5: "bne", 6: "blez", 7: "bgtz", 8: "addi", 9: "addiu", 10: "slti",
           11: "sltiu", 12: "andi", 13: "ori", 14: "xori", 15: "lui", 20: "beql", 21: "bnel", 22: "blezl",
           23: "bgtzl", 29: "jalx", 32: "lb", 33: "lh", 34: "lwl", 35: "lw", 36: "lbu", 37: "lhu", 38: "lwr",
           40: "sb", 41: "sh", 42: "swl", 43: "sw", 46: "swr", 47: "cache", 48: "ll", 49: "lwc1", 50: "lwc2",
           51: "pref", 53: "ldc1", 54: "ldc2", 56: "sc", 57: "swc1", 58: "swc2", 61: "sdc1", 62: "sdc2"}
SPECIAL2 = {0: "madd", 1: "maddu", 2: "mul", 4: "msub", 5: "msubu", 32: "clz", 33: "clo", 63: "sdbbp"}
COP0_CO = {1: "tlbr", 2: "tlbwi", 6: "tlbwr", 8: "tlbp", 24: "eret", 31: "deret", 32: "wait"}

LOADS = {"lb": 1, "lh": 2, "lw": 4, "lbu": 1, "lhu": 2, "lwl": 4, "lwr": 4, "ll": 4}
STORES = {"sb": 1, "sh": 2, "sw": 4, "swl": 4, "swr": 4, "sc": 4}
FP_MEMORY = ("lwc1", "ldc1", "swc1", "sdc1", "lwc2", "ldc2", "swc2", "sdc2")
BRANCHES = ("beq", "bne", "blez", "bgtz", "bltz", "bgez", "beql", "bnel", "blezl", "bgtzl", "bltzl", "bgezl",
            "bltzal", "bgezal", "bltzall", "bgezall", "bc1f", "bc1t", "bc1fl", "bc1tl")
LIKELY = ("beql", "bnel", "blezl", "bgtzl", "bltzl", "bgezl", "bltzall", "bgezall", "bc1fl", "bc1tl")
JUMPS = ("j", "jal", "jalx", "jr", "jalr")
ARITH_IMM = ("addi", "addiu", "slti", "sltiu", "andi", "ori", "xori")
R3 = ("add", "addu", "sub", "subu", "and", "or", "xor", "nor", "slt", "sltu", "sllv", "srlv", "srav", "rotrv",
      "mul", "movz", "movn")


class Fields:
    """What an instruction is, from its bits: op (the instruction's name; aliases are named by what
    they are: move is addu or or), rs, rt, rd (register numbers or None), sa, imm (sign-extended
    where the instruction extends it; lui's already shifted; a branch's in bytes from behind it),
    target (a j or jal's 28 low bits), sel (coprocessor 0's select), width (of a memory access)."""

    __slots__ = ("op", "rs", "rt", "rd", "sa", "imm", "target", "sel", "width", "word")

    def __init__(self, op, rs=None, rt=None, rd=None, sa=0, imm=0, target=None, sel=0, width=None, word=0):
        self.op, self.rs, self.rt, self.rd, self.sa, self.imm = op, rs, rt, rd, sa, imm
        self.target, self.sel, self.width, self.word = target, sel, width, word

    def __repr__(self):
        return "Fields(%s rs=%s rt=%s rd=%s sa=%d imm=%d)" % (self.op, self.rs, self.rt, self.rd, self.sa, self.imm)


def decode_fields(code):
    """Fields of the 4 bytes of one instruction (little endian)."""
    if len(code) < 4:
        return Fields("unknown")
    word = struct.unpack("<I", bytes(code[:4]))[0]
    op = word >> 26
    rs, rt, rd, sa, funct = (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31, (word >> 6) & 31, word & 63
    imm16 = word & 0xFFFF
    simm = imm16 - 0x10000 if imm16 & 0x8000 else imm16

    def f(name, **kw):
        return Fields(name, word=word, **kw)
    if op == 0:
        name = SPECIAL.get(funct)
        if name is None:
            return f("unknown")
        if name == "srl" and rs == 1:
            name = "rotr"
        elif name == "srlv" and sa == 1:
            name = "rotrv"
        if name in ("sll", "srl", "sra", "rotr"):
            if word == 0:
                return f("nop")
            return f(name, rt=rt, rd=rd, sa=sa)
        if name in ("jr", "mthi", "mtlo"):
            return f(name, rs=rs)
        if name == "jalr":
            return f(name, rs=rs, rd=rd)
        if name in ("mfhi", "mflo"):
            return f(name, rd=rd)
        if name in ("mult", "multu", "div", "divu") or name.startswith("t"):
            return f(name, rs=rs, rt=rt)
        if name in ("syscall", "break", "sync"):
            return f(name)
        return f(name, rs=rs, rt=rt, rd=rd, sa=sa)
    if op == 1:
        name = REGIMM.get(rt)
        if name is None:
            return f("unknown")
        if name == "synci":
            return f(name, rs=rs, imm=simm)
        if name.startswith("t"):
            return f(name, rs=rs, imm=simm)
        return f(name, rs=rs, imm=simm << 2)
    if op in (2, 3, 29):
        return f(OPCODES[op], target=(word & 0x3FFFFFF) << 2)
    if op in (4, 5, 20, 21):
        return f(OPCODES[op], rs=rs, rt=rt, imm=simm << 2)
    if op in (6, 7, 22, 23):
        return f(OPCODES[op], rs=rs, imm=simm << 2)
    if op in (8, 9, 10, 11):
        return f(OPCODES[op], rs=rs, rt=rt, imm=simm)
    if op in (12, 13, 14):
        return f(OPCODES[op], rs=rs, rt=rt, imm=imm16)
    if op == 15:
        return f("lui", rt=rt, imm=imm16 << 16)
    if op == 16:                                    # coprocessor 0
        if rs == 0:
            return f("mfc0", rt=rt, rd=rd, sel=word & 7)
        if rs == 4:
            return f("mtc0", rt=rt, rd=rd, sel=word & 7)
        if rs == 11 and rd == 12:
            return f("ei" if word & 0x20 else "di", rt=rt)
        if rs >= 16:
            return f(COP0_CO.get(funct, "unknown"))
        return f("unknown")
    if op == 17:                                    # coprocessor 1 (floating point): no general registers but these
        if rs in (0, 2, 3):
            return f({0: "mfc1", 2: "cfc1", 3: "mfhc1"}[rs], rt=rt)
        if rs in (4, 6, 7):
            return f({4: "mtc1", 6: "ctc1", 7: "mthc1"}[rs], rt=rt)
        if rs == 8:
            return f(("bc1f", "bc1t", "bc1fl", "bc1tl")[rt & 3], imm=simm << 2)
        return f("cop1")
    if op in (18, 19):
        return f("cop2" if op == 18 else "cop1x")
    if op == 28:
        name = SPECIAL2.get(funct)
        if name is None:
            return f("unknown")
        if name in ("madd", "maddu", "msub", "msubu"):
            return f(name, rs=rs, rt=rt)
        if name == "sdbbp":
            return f(name)
        return f(name, rs=rs, rt=rt, rd=rd)
    if op == 31:
        if funct == 0:
            return f("ext", rs=rs, rt=rt, rd=rd, sa=sa)
        if funct == 4:
            return f("ins", rs=rs, rt=rt, rd=rd, sa=sa)
        if funct == 32 and sa in (2, 16, 24):
            return f({2: "wsbh", 16: "seb", 24: "seh"}[sa], rt=rt, rd=rd)
        if funct == 59:
            return f("rdhwr", rt=rt, rd=rd)
        return f("unknown")
    name = OPCODES.get(op)
    if name is None:
        return f("unknown")
    if name in LOADS or name in STORES:
        return f(name, rs=rs, rt=rt, imm=simm, width=LOADS.get(name) or STORES.get(name))
    if name in FP_MEMORY or name in ("cache", "pref"):
        return f(name, rs=rs, rt=rt, imm=simm)
    return f("unknown")


def fields(insn):
    """The Fields of a capstone instruction (decoded once, kept on it)."""
    got = getattr(insn, "mips", None)
    if got is None:
        got = insn.mips = decode_fields(bytes(insn.bytes))
    return got


class _Disassembler:
    """capstone's disassembler for the text, with the layer's Fields on every instruction."""

    def __init__(self, md):
        self.md = md

    def disasm(self, code, address, count=0):
        for insn in self.md.disasm(code, address, count):
            insn.mips = decode_fields(bytes(insn.bytes))
            yield insn


class Mips:
    """The MIPS layer. Constructed from [arch]: isa, endian, ghidra."""

    name = "mips"
    default_args = 4
    default_callee_saved = ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "fp")
    default_result = ("v0", "v1")
    word = 4
    reg_bits = 32
    link_reg = "ra"
    veneer_reg = "at"       # the overlay's far jump goes through the assembler's temporary
    delay = 4               # bytes of the delay slot behind a branch or jump
    # Allocated sections the toolchain adds to every object that no image of ours wants: ld.lld puts
    # .MIPS.abiflags and .reginfo in front of .text (the listing's j and jal then pointed 0x30 off),
    # and makes a .got even for code without one (dread asm discards them)
    discard_sections = (".MIPS.abiflags", ".reginfo", ".got", ".pdr", ".gnu.attributes", ".mdebug*")

    def __init__(self, isa="mips32", endian="little", ghidra=None, cpu=None):
        if isa != "mips32":
            raise ValueError("isa is mips32, not %r (MIPS64 and microMIPS are not supported yet)" % (isa,))
        if endian != "little":
            raise ValueError("only endian = \"little\" is supported")
        self.isa, self.endian, self.cpu = isa, endian, cpu
        self.ghidra = ghidra or "MIPS:LE:32:default"
        self.ghidra_cspec = "default"
        self.profile = None
        self._cs = None

    def configure(self, cflags):
        pass

    def describe(self):
        return "mips %s (release 2), little-endian, o32" % self.isa

    # ---- registers ----

    def reg_name(self, name):
        name = name.lower().lstrip("$")
        if name in ("pc", "hi", "lo"):
            return name
        if name not in NUMBER:
            raise ValueError("unknown MIPS register %r" % name)
        return NAMES[NUMBER[name]]

    def reg_id(self, name):
        """The Unicorn register id of a register name (o32 names, $0-$31, s8, pc, hi, lo)."""
        from unicorn import mips_const as um
        name = self.reg_name(name)
        if name in ("pc", "hi", "lo"):
            return getattr(um, "UC_MIPS_REG_" + name.upper())
        return getattr(um, "UC_MIPS_REG_%d" % NUMBER[name])

    def arg_regs(self, n):
        return ["a%d" % i for i in range(n)]

    def general_regs(self):
        """The registers a function may use for values (not zero, the kernel's k0 and k1, gp, sp, ra)."""
        return [n for n in NAMES if n not in ("zero", "k0", "k1", "gp", "sp", "ra")]

    @staticmethod
    def _name(name):
        name = name.lstrip("$")
        return NAMES[NUMBER[name]] if name in NUMBER else name

    # ---- instruction sets ----

    def isas(self):
        return ("mips32",)

    def min_insn(self, isa):
        return 4

    def insn_length(self, code, isa):
        return 4

    def disassembler(self, isa=None):
        if self._cs is None:
            raw = cs.Cs(cs.CS_ARCH_MIPS, cs.CS_MODE_MIPS32 | cs.CS_MODE_LITTLE_ENDIAN)
            raw.detail = True
            self._cs = _Disassembler(raw)
        return self._cs

    def decode(self, code, address, isa=None):
        for insn in self.disassembler(isa).disasm(bytes(code), address, 1):
            return insn
        return None

    def pc_value(self, address, isa):
        """A branch counts from its delay slot (the instruction's address + 4)."""
        return address + 4

    # ---- what an instruction does ----

    def delay_slot(self, insn):
        """Whether the instruction behind this one runs before it goes: every branch and jump (a
        branch-likely only when taken: likely())."""
        op = fields(insn).op
        return op in BRANCHES or op in JUMPS

    def likely(self, insn):
        """A branch-likely: its delay slot runs only when it is taken."""
        return fields(insn).op in LIKELY

    def flow(self, insn):
        """(kind, target, falls), as Arm.flow documents; the delay slot behind a branch or jump is
        not part of it (delay_slot). j is a jump, jal a call (both within the 256 MiB region of their
        delay slot), jr ra a return, jr through another register an indirect jump (a tail call or a
        switch), jalr a call through a register; beq and kin conditional jumps, beq with the same
        register twice (b) and bgez zero an unconditional one, bal (bgezal zero) a call; eret and
        deret return from an exception; syscall is a call through the system; break and sdbbp
        stop."""
        f = fields(insn)
        op = f.op
        if op in ("j", "jal"):
            target = ((insn.address + 4) & 0xF0000000) | f.target
            return ("jump", target, False) if op == "j" else ("call", target, True)
        if op == "jalx":
            return "stop", None, False      # to microMIPS or MIPS16: not supported
        if op == "jr":
            return ("return", None, False) if f.rs == 31 else ("ijump", NAMES[f.rs], False)
        if op == "jalr":
            return "icall", None, True
        if op in BRANCHES:
            target = insn.address + 4 + f.imm
            if op in ("bltzal", "bgezal", "bltzall", "bgezall"):
                return "call", target, True
            always = (op == "beq" and f.rs == f.rt) or (op == "bgez" and f.rs == 0) or \
                (op == "blez" and f.rs == 0)
            return "jump", target, not always
        if op in ("eret", "deret"):
            return "return", None, False
        if op == "syscall":
            return "icall", None, True
        if op in ("break", "sdbbp", "unknown"):
            return "stop", None, False
        return "next", None, False

    def target(self, insn):
        kind, target, _ = self.flow(insn)
        return target if kind in ("jump", "call") else None

    def is_call(self, insn):
        return self.flow(insn)[0] in ("call", "icall")

    def is_conditional(self, insn):
        """No predicated instructions (movz and movn write or keep their register: reg_effects reads
        it)."""
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
        """Join lui with the instruction behind it that uses the register it wrote: lui + addiu or
        ori is an address or a constant (mips_address), lui + a load or store a known memory address
        (mips_memory: (address, width)). The two may be apart by other instructions in compiled
        code; only the adjacent pair is joined here. Returns False: none of these is a call through
        a register."""
        if prev is None:
            return False
        p, f = fields(prev), fields(insn)
        if p.op != "lui" or not p.rt or prev.address + 4 != insn.address or f.rs != p.rt:
            return False
        if f.op == "addiu":
            insn.mips_address = (p.imm + f.imm) & 0xFFFFFFFF
        elif f.op == "ori":
            insn.mips_address = (p.imm | f.imm) & 0xFFFFFFFF
        elif f.width is not None:
            insn.mips_memory = ((p.imm + f.imm) & 0xFFFFFFFF, f.width)
        return False

    def reg_effects(self, insn):
        """(reads, writes) by o32 name, "hi" and "lo" for HI and LO; zero is neither read nor
        written. No flags on MIPS."""
        f = fields(insn)
        op = f.op
        reads, writes = set(), set()

        def r(*numbers):
            reads.update(NAMES[n] for n in numbers if n)

        def w(*numbers):
            writes.update(NAMES[n] for n in numbers if n)
        if op in ("sll", "srl", "sra", "rotr", "seb", "seh", "wsbh"):
            r(f.rt)
            w(f.rd)
        elif op in R3:
            r(f.rs, f.rt)
            if op in ("movz", "movn"):
                r(f.rd)                         # kept when the condition fails
            w(f.rd)
        elif op in ("clz", "clo"):
            r(f.rs)
            w(f.rd)
        elif op in ARITH_IMM:
            r(f.rs)
            w(f.rt)
        elif op == "lui":
            w(f.rt)
        elif op in LOADS:
            r(f.rs)
            if op in ("lwl", "lwr"):
                r(f.rt)                         # merged with what the register holds
            w(f.rt)
        elif op in STORES:
            r(f.rs, f.rt)
            if op == "sc":
                w(f.rt)
        elif op in FP_MEMORY or op in ("cache", "pref", "synci"):
            r(f.rs)
        elif op in ("mult", "multu", "div", "divu"):
            r(f.rs, f.rt)
            writes.update(("hi", "lo"))
        elif op in ("madd", "maddu", "msub", "msubu"):
            r(f.rs, f.rt)
            reads.update(("hi", "lo"))
            writes.update(("hi", "lo"))
        elif op in ("mfhi", "mflo"):
            reads.add(op[2:])
            w(f.rd)
        elif op in ("mthi", "mtlo"):
            r(f.rs)
            writes.add(op[2:])
        elif op == "jr":
            r(f.rs)
        elif op == "jalr":
            r(f.rs)
            w(f.rd)
        elif op in ("jal", "bltzal", "bgezal", "bltzall", "bgezall"):
            if op != "jal":
                r(f.rs)
            w(31)
        elif op in ("beq", "bne", "beql", "bnel"):
            r(f.rs, f.rt)
        elif op in ("blez", "bgtz", "bltz", "bgez", "blezl", "bgtzl", "bltzl", "bgezl"):
            r(f.rs)
        elif op.startswith("t") and op not in ("tlbr", "tlbwi", "tlbwr", "tlbp"):
            r(f.rs, f.rt)                       # the traps compare
        elif op == "ext":
            r(f.rs)
            w(f.rt)
        elif op == "ins":
            r(f.rs, f.rt)
            w(f.rt)
        elif op in ("mfc0", "mfc1", "cfc1", "mfhc1", "di", "ei", "rdhwr"):
            w(f.rt)
        elif op in ("mtc0", "mtc1", "ctc1", "mthc1"):
            r(f.rt)
        # syscall reads and writes nothing of its own (its handler's convention is the firmware's),
        # as RISC-V's ecall
        return reads, writes

    def sets_flags(self, insn):
        return False

    def stack_kind(self, insn):
        """MIPS has no push and pop (frames are made with addiu sp and single stores)."""
        return None

    def pushed_registers(self, insn):
        return []

    def literal(self, insn, isa):
        """(address, size) of a load whose address a lui in front of it gives."""
        memory = getattr(insn, "mips_memory", None)
        if memory is not None and fields(insn).op in LOADS:
            return memory
        return None

    def pc_address(self, insn, isa):
        """The address lui + addiu or ori computes (joined by link_pair), else None."""
        return getattr(insn, "mips_address", None)

    def bound_check(self, insn, prior=()):
        """(register, limit, before) when the instruction bounds a switch index: andi rN, rM,
        2^n - 1 (rN takes 0 .. 2^n - 1; set after it), sltiu rN, rM, k (rM below k when rN is set)."""
        f = fields(insn)
        if f.op == "andi" and f.rt and 0 < f.imm < 0x10000 and f.imm & (f.imm + 1) == 0:
            return NAMES[f.rt], f.imm, False
        return None

    def switch_targets(self, insn, program, inside):
        return None

    # ---- placing C ----

    def start_address(self, address, isa):
        return address

    def isa_of_symbol(self, value):
        return "mips32"

    @staticmethod
    def symbol_asm(name, address, isa):
        return "\t.global %s\n\t.type %s, @function\n\t.set %s, 0x%x\n" % (name, name, name, address)

    def assembler_flags(self, compiler):
        """clang's driver has the target and -march in cflags; GNU as takes them as its own flags."""
        if compiler.kind == "clang":
            return []
        return [f for f in compiler.cflags if f.startswith(("-march=", "-mabi=", "-EL"))] or ["-march=mips32r2", "-EL"]

    def linker_flags(self, compiler):
        return [] if compiler.kind == "clang" else ["-EL"]

    def fixed_from_symbols(self, symbols):
        """The registers that keep one value (dread.target.Image.fixed) from an ELF's symbols: gp is
        _gp (the ABI's convention; small data is addressed from it)."""
        value = symbols.get("_gp")
        return {"gp": value} if value is not None else {}

    def asm(self):
        """The listing's syntax (dread asm): MipsAsm."""
        return MipsAsm(self)


class MipsAsm:
    """How dread asm writes MIPS code: for clang's integrated assembler (llvm-mc) or GNU as, with
    `.set noreorder` (the delay slots as they are: nothing filled, nothing moved), `.set noat` (at is
    the code's) and `.set nomacro` (no instruction becomes two). capstone's text is taken where the
    assemblers read it as the same encoding; branches and jumps go to labels; coprocessor 0's
    registers are written by number with their select ($12, 0), which capstone names as general
    registers."""

    comment = "#"
    directives = {"word": ".4byte", "half": ".2byte", "byte": ".byte", "text": ".asciz"}

    def __init__(self, arch):
        self.arch = arch

    def configure(self, compiler):
        pass

    def prelude(self, isa):
        return ["\t.set noreorder", "\t.set noat", "\t.set nomacro"]

    def before(self, insn):
        return []

    def switch(self, isa):
        return []

    def raw(self, b, isa):
        return ".4byte", "0x%08x" % struct.unpack("<I", b)[0]

    def function_lines(self, label, isa, code):
        return ["\t.type   %s, @function" % label]

    def operands(self, listing, insn, run, st, isa):
        """(mnemonic, operands, comment) of one instruction for the listing, or None to write it raw."""
        f = fields(insn)
        mnem, ops = insn.mnemonic, insn.op_str
        if f.op in ("mfc0", "mtc0"):
            return f.op, "$%s, $%d, %d" % (NAMES[f.rt], f.rd, f.sel), ""
        kind, target, _ = self.arch.flow(insn)
        if target is not None and (f.op in BRANCHES or f.op in ("j", "jal")):
            tst = listing.prog.stored(target)
            if listing.space(target) == listing.space(run) and listing.has(tst, 4) and listing.labelable(tst):
                listing.labels.add(tst)
                head = ops.rsplit(",", 1)[0] + "," if "," in ops else ""
                return mnem, ("label", head, tst), ""
            return None
        if f.op == "unknown":
            return None
        comment = ""
        at = getattr(insn, "mips_address", None)
        if at is not None:
            comment = ("=0x%x %s" % (at, listing.describe_value(at))).strip()
        memory = getattr(insn, "mips_memory", None)
        if memory is not None:
            comment = "[0x%x]" % memory[0]
        return mnem, ops, comment
