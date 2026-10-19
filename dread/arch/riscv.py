"""The RISC-V layer: RV32 with the M and C extensions, Zicsr and the machine-mode instructions.

Capstone 5 gives the text of an instruction but not the registers it uses (regs_access is not
implemented for RISC-V, and a system register is no operand), so this layer decodes the bits
itself (`fields`) for everything but the text: what an instruction reads and writes, where it goes,
the immediates. Two-instruction forms are joined when the program database decodes a range
(`link_pair`): auipc + jalr is a call (or a tail call) to a known address, auipc or lui + addi an
address, auipc or lui + a load or store a known memory address.

The calling convention is ilp32: arguments in a0-a7, results in a0 and a1, s0-s11 and sp kept,
the return address in ra. There are no condition flags and no predicated instructions.

Constructed from [arch]: isa ("rv32"), extensions (default "imc": the C extension decides whether
instructions may be 2 bytes long), endian, ghidra.
"""
import os
import struct

import capstone as cs
import unicorn as uc
from unicorn import riscv_const as ur

NAMES = ["zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2", "s0", "s1"] + ["a%d" % i for i in range(8)] + \
        ["s%d" % i for i in range(2, 12)] + ["t3", "t4", "t5", "t6"]
NUMBER = {name: i for i, name in enumerate(NAMES)}
NUMBER.update({"x%d" % i: i for i in range(32)})
NUMBER["fp"] = 8
UC_REGS = {name: getattr(ur, "UC_RISCV_REG_X%d" % i) for i, name in enumerate(NAMES)}
UC_REGS["pc"] = ur.UC_RISCV_REG_PC

BRANCHES = {0: "beq", 1: "bne", 4: "blt", 5: "bge", 6: "bltu", 7: "bgeu"}
LOADS = {0: ("lb", 1), 1: ("lh", 2), 2: ("lw", 4), 4: ("lbu", 1), 5: ("lhu", 2)}
STORES = {0: ("sb", 1), 1: ("sh", 2), 2: ("sw", 4)}
OP_IMM = {0: "addi", 2: "slti", 3: "sltiu", 4: "xori", 6: "ori", 7: "andi"}
OP = {(0, 0): "add", (0, 0x20): "sub", (1, 0): "sll", (2, 0): "slt", (3, 0): "sltu", (4, 0): "xor",
      (5, 0): "srl", (5, 0x20): "sra", (6, 0): "or", (7, 0): "and",
      (0, 1): "mul", (1, 1): "mulh", (2, 1): "mulhsu", (3, 1): "mulhu", (4, 1): "div", (5, 1): "divu",
      (6, 1): "rem", (7, 1): "remu"}
CSR_OPS = {1: "csrrw", 2: "csrrs", 3: "csrrc", 5: "csrrwi", 6: "csrrsi", 7: "csrrci"}
SYSTEM = {0x00000073: "ecall", 0x00100073: "ebreak", 0x30200073: "mret", 0x10200073: "sret",
          0x00200073: "uret", 0x10500073: "wfi"}


class Fields:
    """What an instruction is, from its bits: op (the base instruction's name; a compressed one is
    named by what it expands to, c.addi -> addi), rd, rs1, rs2 (register numbers, None when not
    used), imm, csr, size (bytes), width (of a memory access), compressed."""

    __slots__ = ("op", "rd", "rs1", "rs2", "imm", "csr", "size", "width", "compressed")

    def __init__(self, op, rd=None, rs1=None, rs2=None, imm=0, csr=None, size=4, width=None, compressed=False):
        self.op, self.rd, self.rs1, self.rs2, self.imm = op, rd, rs1, rs2, imm
        self.csr, self.size, self.width, self.compressed = csr, size, width, compressed

    def __repr__(self):
        return "Fields(%s rd=%s rs1=%s rs2=%s imm=%d%s)" % (self.op, self.rd, self.rs1, self.rs2, self.imm,
                                                          " csr=0x%x" % self.csr if self.csr is not None else "")


def _bits(value, hi, lo):
    return (value >> lo) & ((1 << (hi - lo + 1)) - 1)


def _sign(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def decode_fields(code):
    """Fields of the instruction at the start of `code` (bytes), or Fields("unknown")."""
    if len(code) < 2:
        return Fields("unknown", size=2)
    half = code[0] | code[1] << 8
    if half & 3 != 3:
        return _compressed(half)
    if len(code) < 4:
        return Fields("unknown", size=4)
    i = struct.unpack_from("<I", code)[0]
    opcode, rd, f3, rs1, rs2, f7 = i & 0x7F, _bits(i, 11, 7), _bits(i, 14, 12), _bits(i, 19, 15), _bits(i, 24, 20), i >> 25
    imm_i = _sign(i >> 20, 12)
    if opcode == 0x37:
        return Fields("lui", rd=rd, imm=_sign(i & 0xFFFFF000, 32))
    if opcode == 0x17:
        return Fields("auipc", rd=rd, imm=_sign(i & 0xFFFFF000, 32))
    if opcode == 0x6F:
        imm = (_bits(i, 31, 31) << 20) | (_bits(i, 19, 12) << 12) | (_bits(i, 20, 20) << 11) | (_bits(i, 30, 21) << 1)
        return Fields("jal", rd=rd, imm=_sign(imm, 21))
    if opcode == 0x67 and f3 == 0:
        return Fields("jalr", rd=rd, rs1=rs1, imm=imm_i)
    if opcode == 0x63 and f3 in BRANCHES:
        imm = (_bits(i, 31, 31) << 12) | (_bits(i, 7, 7) << 11) | (_bits(i, 30, 25) << 5) | (_bits(i, 11, 8) << 1)
        return Fields(BRANCHES[f3], rs1=rs1, rs2=rs2, imm=_sign(imm, 13))
    if opcode == 0x03 and f3 in LOADS:
        name, width = LOADS[f3]
        return Fields(name, rd=rd, rs1=rs1, imm=imm_i, width=width)
    if opcode == 0x23 and f3 in STORES:
        name, width = STORES[f3]
        return Fields(name, rs1=rs1, rs2=rs2, imm=_sign((f7 << 5) | rd, 12), width=width)
    if opcode == 0x13:
        if f3 == 1 and f7 == 0:
            return Fields("slli", rd=rd, rs1=rs1, imm=rs2)
        if f3 == 5 and f7 in (0, 0x20):
            return Fields("srai" if f7 else "srli", rd=rd, rs1=rs1, imm=rs2)
        if f3 in OP_IMM:
            return Fields(OP_IMM[f3], rd=rd, rs1=rs1, imm=imm_i)
    if opcode == 0x33 and (f3, f7) in OP:
        return Fields(OP[(f3, f7)], rd=rd, rs1=rs1, rs2=rs2)
    if opcode == 0x0F:
        return Fields("fence.i" if f3 == 1 else "fence")
    if opcode == 0x73:
        if i in SYSTEM:
            return Fields(SYSTEM[i])
        if f3 in CSR_OPS:
            csr = i >> 20
            if f3 >= 5:
                return Fields(CSR_OPS[f3], rd=rd, imm=rs1, csr=csr)
            return Fields(CSR_OPS[f3], rd=rd, rs1=rs1, csr=csr)
    return Fields("unknown")


def _compressed(h):
    q, f3 = h & 3, h >> 13
    rd = _bits(h, 11, 7)
    rs2 = _bits(h, 6, 2)
    rdp, rs2p = 8 + _bits(h, 4, 2), 8 + _bits(h, 4, 2)     # rd' and rs2' (bits 4:2)
    rs1p = 8 + _bits(h, 9, 7)                              # rs1' and rd' (bits 9:7)

    def c(op, **kw):
        return Fields(op, size=2, compressed=True, **kw)

    if h == 0:
        return c("unknown")                                 # c.unimp
    if q == 0:
        if f3 == 0:
            imm = (_bits(h, 10, 7) << 6) | (_bits(h, 12, 11) << 4) | (_bits(h, 5, 5) << 3) | (_bits(h, 6, 6) << 2)
            return c("addi", rd=rdp, rs1=2, imm=imm) if imm else c("unknown")
        if f3 in (2, 6):
            imm = (_bits(h, 5, 5) << 6) | (_bits(h, 12, 10) << 3) | (_bits(h, 6, 6) << 2)
            if f3 == 2:
                return c("lw", rd=rdp, rs1=rs1p, imm=imm, width=4)
            return c("sw", rs1=rs1p, rs2=rs2p, imm=imm, width=4)
        return c("unknown")                                 # the floating-point loads and stores
    if q == 1:
        imm6 = _sign((_bits(h, 12, 12) << 5) | _bits(h, 6, 2), 6)
        if f3 == 0:
            return c("addi", rd=rd, rs1=rd, imm=imm6)       # c.addi; c.nop when rd is zero
        if f3 in (1, 5):
            imm = (_bits(h, 12, 12) << 11) | (_bits(h, 8, 8) << 10) | (_bits(h, 10, 9) << 8) | \
                  (_bits(h, 6, 6) << 7) | (_bits(h, 7, 7) << 6) | (_bits(h, 2, 2) << 5) | \
                  (_bits(h, 11, 11) << 4) | (_bits(h, 5, 3) << 1)
            return c("jal", rd=1 if f3 == 1 else 0, imm=_sign(imm, 12))   # c.jal (RV32), c.j
        if f3 == 2:
            return c("addi", rd=rd, rs1=0, imm=imm6)        # c.li
        if f3 == 3:
            if rd == 2:
                imm = (_bits(h, 12, 12) << 9) | (_bits(h, 4, 3) << 7) | (_bits(h, 5, 5) << 6) | \
                      (_bits(h, 2, 2) << 5) | (_bits(h, 6, 6) << 4)
                return c("addi", rd=2, rs1=2, imm=_sign(imm, 10))           # c.addi16sp
            return c("lui", rd=rd, imm=imm6 << 12)
        if f3 == 4:
            kind = _bits(h, 11, 10)
            shamt = (_bits(h, 12, 12) << 5) | _bits(h, 6, 2)
            if kind == 0:
                return c("srli", rd=rs1p, rs1=rs1p, imm=shamt)
            if kind == 1:
                return c("srai", rd=rs1p, rs1=rs1p, imm=shamt)
            if kind == 2:
                return c("andi", rd=rs1p, rs1=rs1p, imm=imm6)
            if not _bits(h, 12, 12):
                op = ("sub", "xor", "or", "and")[_bits(h, 6, 5)]
                return c(op, rd=rs1p, rs1=rs1p, rs2=rs2p)
            return c("unknown")
        imm = (_bits(h, 12, 12) << 8) | (_bits(h, 6, 5) << 6) | (_bits(h, 2, 2) << 5) | \
              (_bits(h, 11, 10) << 3) | (_bits(h, 4, 3) << 1)
        return c("beq" if f3 == 6 else "bne", rs1=rs1p, rs2=0, imm=_sign(imm, 9))
    if q == 2:
        if f3 == 0:
            return c("slli", rd=rd, rs1=rd, imm=(_bits(h, 12, 12) << 5) | rs2)
        if f3 == 2:
            imm = (_bits(h, 3, 2) << 6) | (_bits(h, 12, 12) << 5) | (_bits(h, 6, 4) << 2)
            return c("lw", rd=rd, rs1=2, imm=imm, width=4)  # c.lwsp
        if f3 == 4:
            if not _bits(h, 12, 12):
                if rs2 == 0:
                    return c("jalr", rd=0, rs1=rd, imm=0) if rd else c("unknown")   # c.jr
                return c("add", rd=rd, rs1=0, rs2=rs2)      # c.mv
            if rd == 0 and rs2 == 0:
                return c("ebreak")
            if rs2 == 0:
                return c("jalr", rd=1, rs1=rd, imm=0)       # c.jalr
            return c("add", rd=rd, rs1=rd, rs2=rs2)         # c.add
        if f3 == 6:
            imm = (_bits(h, 8, 7) << 6) | (_bits(h, 12, 9) << 2)
            return c("sw", rs1=2, rs2=rs2, imm=imm, width=4)  # c.swsp
    return c("unknown")


def fields(insn):
    """The Fields of a decoded instruction (cached on it)."""
    f = getattr(insn, "rv", None)
    if f is None:
        f = decode_fields(bytes(insn.bytes))
        insn.rv = f
    return f


class _Disassembler:
    """capstone's disassembler for the text, with the layer's Fields on every instruction."""

    def __init__(self, md):
        self.md = md

    def disasm(self, code, address, count=0):
        for insn in self.md.disasm(code, address, count):
            insn.rv = decode_fields(bytes(insn.bytes))
            yield insn


class RiscV:
    """The RISC-V layer. Constructed from [arch]: isa, extensions, endian, ghidra."""

    name = "riscv"
    default_args = 8
    default_callee_saved = ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11")
    default_result = ("a0", "a1")
    word = 4
    reg_bits = 32
    link_reg = "ra"
    veneer_reg = "t1"       # the overlay's far branch and the linker's call stubs go through t1

    def __init__(self, isa="rv32", extensions="imc", endian="little", ghidra=None, cpu=None):
        if isa != "rv32":
            raise ValueError("isa is rv32, not %r (RV64 is not supported yet)" % (isa,))
        if endian != "little":
            raise ValueError("only endian = \"little\" is supported")
        self.isa, self.endian, self.cpu = isa, endian, cpu
        self.extensions = extensions.lower()
        self.compressed = "c" in self.extensions
        self.ghidra = ghidra or "RISCV:LE:32:default"     # Ghidra's one RV32 language (its spec: gcc)
        self.ghidra_cspec = "gcc"
        self.profile = None
        self._cs = {}

    def configure(self, cflags):
        """-march in the compiler flags says whether the C extension is there."""
        for flag in cflags:
            if flag.startswith("-march=rv32"):
                letters = flag[len("-march=rv32"):].split("_")[0]
                self.compressed = "c" in letters

    def describe(self):
        return "riscv %s%s, little-endian" % (self.isa, self.extensions)

    # ---- registers ----

    def reg_name(self, name):
        name = name.lower()
        if name == "pc":
            return name
        if name not in NUMBER:
            raise ValueError("unknown RISC-V register %r" % name)
        return NAMES[NUMBER[name]]

    def reg_id(self, name):
        """The Unicorn register id of a register name (ABI names, x0-x31, fp, pc)."""
        return UC_REGS[self.reg_name(name)]

    def arg_regs(self, n):
        return ["a%d" % i for i in range(n)]

    def general_regs(self):
        """The general registers a function may use for values (not zero, sp, gp, tp, ra)."""
        return [n for n in NAMES if n not in ("zero", "sp", "gp", "tp", "ra")]

    @staticmethod
    def _name(name):
        return NAMES[NUMBER[name]] if name in NUMBER else name

    # ---- instruction sets ----

    def isas(self):
        return ("rv32",)

    def min_insn(self, isa):
        return 2 if self.compressed else 4

    def insn_length(self, code, isa):
        """2 when the low two bits of the first halfword are not 0b11 (a compressed instruction)."""
        return 4 if code[0] & 3 == 3 else 2

    def disassembler(self, isa=None):
        md = self._cs.get("rv32")
        if md is None:
            raw = cs.Cs(cs.CS_ARCH_RISCV, cs.CS_MODE_RISCV32 | (cs.CS_MODE_RISCVC if self.compressed else 0))
            raw.detail = True
            md = self._cs["rv32"] = _Disassembler(raw)
        return md

    def decode(self, code, address, isa=None):
        for insn in self.disassembler(isa).disasm(bytes(code), address, 1):
            return insn
        return None

    def pc_value(self, address, isa):
        """pc-relative forms count from the instruction's own address."""
        return address

    # ---- what an instruction does ----

    def flow(self, insn):
        """(kind, target, falls), as Arm.flow documents: jal with a link register is a call, jal
        x0 a jump; jalr x0, 0(ra) a return, jalr x0 through another register an indirect jump (a
        tail call or a switch), jalr with a link a call through a register; auipc + jalr (joined
        by link_pair) has its target; the branches are conditional jumps; mret, sret and uret return
        from a trap."""
        f = fields(insn)
        op = f.op
        if op == "jal":
            target = insn.address + f.imm
            return ("jump", target, False) if f.rd == 0 else ("call", target, True)
        if op == "jalr":
            target = getattr(insn, "rv_target", None)
            if f.rd == 0:
                if target is not None:
                    return "jump", target, False
                if f.rs1 == 1 and f.imm == 0:
                    return "return", None, False
                return "ijump", NAMES[f.rs1], False
            return ("call", target, True) if target is not None else ("icall", None, True)
        if op in ("beq", "bne", "blt", "bge", "bltu", "bgeu"):
            return "jump", insn.address + f.imm, True
        if op in ("mret", "sret", "uret"):
            return "return", None, False
        if op == "ecall":
            return "icall", None, True      # its handler may return values in any register
        if op in ("ebreak", "unknown"):
            return "stop", None, False
        return "next", None, False

    def target(self, insn):
        """The destination of a direct jump, call or branch (auipc + jalr included), else None."""
        kind, target, _ = self.flow(insn)
        return target if kind in ("jump", "call") else None

    def is_call(self, insn):
        return self.flow(insn)[0] in ("call", "icall")

    def is_conditional(self, insn):
        """No predicated instructions on RISC-V (a branch is a conditional jump: flow's falls)."""
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
        """Join auipc/lui with the instruction behind it when that one uses the register it wrote:
        auipc + jalr is a call or tail call to a known address (rv_target), auipc or lui + addi
        an address (rv_address), auipc or lui + a load or store a known memory address
        (rv_memory: (address, width)). li rN, K in front of a branch that compares rN gives the
        branch its constant (rv_compare: (the other register's number, K)). Returns False: none of
        these is a call through a register."""
        if prev is None:
            return False
        p, f = fields(prev), fields(insn)
        if (p.op == "addi" and p.rs1 == 0 and p.rd and f.op in ("beq", "bne", "blt", "bge", "bltu", "bgeu")
                and prev.address + prev.size == insn.address and p.rd in (f.rs1, f.rs2)):
            other = f.rs2 if f.rs1 == p.rd else f.rs1
            if other:
                insn.rv_compare = (other, p.imm)
            return False
        if p.op not in ("auipc", "lui") or not p.rd or prev.address + prev.size != insn.address:
            return False
        if f.rs1 != p.rd:
            return False
        base = (prev.address if p.op == "auipc" else 0) + p.imm
        value = (base + f.imm) & 0xFFFFFFFF
        if f.op == "jalr" and p.op == "auipc":
            insn.rv_target = value & ~1
        elif f.op == "addi":
            insn.rv_address = value
        elif f.width is not None:
            insn.rv_memory = (value, f.width)
        return False

    def reg_effects(self, insn):
        """(reads, writes) by ABI name; zero is neither read nor written. No flags on RISC-V."""
        f = fields(insn)
        reads, writes = set(), set()
        for r in (f.rs1, f.rs2):
            if r:
                reads.add(NAMES[r])
        if f.rd:
            writes.add(NAMES[f.rd])
        if f.op == "ecall":                 # the handler's arguments and results
            reads |= {"a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7"}
            writes |= {"a0", "a1"}
        return reads, writes

    def sets_flags(self, insn):
        return False

    def stack_kind(self, insn):
        """RISC-V has no push and pop (frames are made with addi sp and single stores)."""
        return None

    def literal(self, insn, isa):
        """(address, size) of a load whose address an auipc or lui in front of it gives."""
        memory = getattr(insn, "rv_memory", None)
        if memory is not None and fields(insn).op in ("lb", "lh", "lw", "lbu", "lhu"):
            return memory
        return None

    def pc_address(self, insn, isa):
        """The address auipc or lui + addi computes (joined by link_pair), else None."""
        return getattr(insn, "rv_address", None)

    def bound_check(self, insn):
        """(register, limit, before) when the instruction bounds a switch index (dread.switches):
        andi rN, rM, 2^n - 1 (rN takes 0 .. 2^n - 1; set after it), or a branch that compares the
        index with a constant loaded right in front of it (li + bltu and kin, joined by link_pair:
        values up to the constant are tried; set before the branch)."""
        f = fields(insn)
        if f.op == "andi" and f.rd and 0 < f.imm < 0x10000 and f.imm & (f.imm + 1) == 0:
            return NAMES[f.rd], f.imm, False
        compare = getattr(insn, "rv_compare", None)
        if compare is not None and 0 <= compare[1] < 0x10000:
            return NAMES[compare[0]], compare[1], True
        return None

    def switch_targets(self, insn, program, inside):
        return None

    # ---- what the case generator learns from the code (dread.autocase) ----

    generator_version = 1       # in the generated-case cache's key: bump when these change

    def prologue_frame(self, program, fn, look=32):
        """The stack a function's prologue reserves for locals: addi sp, sp, -N makes the frame, and
        the stores of ra and the callee-saved registers at its top are not locals: the locals lie
        below the lowest of them."""
        total, saved = 0, []
        keep = set(program.target.abi.callee_saved) | {"ra"}
        for a in fn.insns[:look]:
            insn = program.insn(a)
            if insn is None or self.flow(insn)[0] != "next":
                break
            f = fields(insn)
            if f.op == "addi" and f.rd == 2 and f.rs1 == 2 and f.imm < 0:
                total += -f.imm
            elif f.op == "sw" and f.rs1 == 2 and NAMES[f.rs2] in keep:
                saved.append(f.imm)
        return min(saved + [total]) if total else 0

    def immediates(self, insn):
        """Candidate input values: the immediates of the arithmetic and logic instructions and of
        lui, and the address or constant an auipc/lui pair makes (not offsets of loads, stores,
        jumps)."""
        f = fields(insn)
        out = []
        if f.op in ("addi", "slti", "sltiu", "xori", "ori", "andi", "lui"):
            out.append(f.imm & 0xFFFFFFFF)
        pair = getattr(insn, "rv_address", None)
        if pair is not None:
            out.append(pair)
        return out

    def switch_hints(self, insns):
        """(bounds, table): small constants that a branch compares with (li rN, k in front of a
        bltu; sltiu, slti), and whether the code jumps through a register other than ra (a table)."""
        limits, table = set(), False
        for ins in insns:
            f = fields(ins)
            if ((f.op == "addi" and f.rs1 == 0) or f.op in ("slti", "sltiu")) and 0 < f.imm <= 0xFF:
                limits.add(f.imm)
            if self.flow(ins)[0] == "ijump":
                table = True
        return limits, table

    def compare_chars(self, insn):
        """Values that may be characters a parser checks: li rN, c (the other operand of a branch),
        addi rN, rM, -c (rM - c, compared with a bound), xori, slti, sltiu with an immediate."""
        f = fields(insn)
        if f.op == "addi" and f.rs1 == 0:
            return [f.imm & 0xFFFFFFFF]
        if f.op == "addi" and f.imm < 0:
            return [-f.imm]
        if f.op in ("xori", "slti", "sltiu"):
            return [f.imm & 0xFFFFFFFF]
        return []

    def branch_deciders(self, insns):
        """{conditional branch: (itself, first, second, 0, "compare", 1)}: a RISC-V branch compares
        its two registers itself; zero stands for the constant 0 (None)."""
        out = {}
        for ins in insns:
            f = fields(ins)
            if f.op in ("beq", "bne", "blt", "bge", "bltu", "bgeu"):
                first = UC_REGS[NAMES[f.rs1]] if f.rs1 else None
                second = UC_REGS[NAMES[f.rs2]] if f.rs2 else None
                out[ins.address] = (ins.address, first, second, 0, "compare", 1)
        return out

    # ---- placing C ----

    def branch(self, src, dst, isa, link=False):
        """The bytes of a jump (jal x0, or jal ra with link) at `src` to `dst`, 4 bytes. Raises
        ValueError when `dst` is farther than 1 MiB (far_branch has the 8-byte form)."""
        off = dst - src
        rd = 1 if link else 0
        if off % 2 or not -(1 << 20) <= off < (1 << 20):
            raise ValueError("no RISC-V jump from 0x%x to 0x%x (jal reaches 1 MiB)" % (src, dst))
        imm = off & 0x1FFFFF
        word = ((imm >> 20) & 1) << 31 | ((imm >> 1) & 0x3FF) << 21 | ((imm >> 11) & 1) << 20 | \
               ((imm >> 12) & 0xFF) << 12 | rd << 7 | 0x6F
        return struct.pack("<I", word)

    def far_branch(self, src, dst, isa, link=False):
        """auipc t1 + jalr x0 (jalr ra with link) at `src` to `dst`, 8 bytes, anywhere in the 4 GiB:
        t1 is free at a function's entry by the convention."""
        off = dst - src
        rd = 1 if link else 0
        if off % 2:
            raise ValueError("no RISC-V jump from 0x%x to an odd address 0x%x" % (src, dst))
        hi = (off + 0x800) & 0xFFFFF000
        lo = (off - hi) & 0xFFF
        auipc = (hi & 0xFFFFF000) | 6 << 7 | 0x17                    # auipc t1, hi
        jalr = lo << 20 | 6 << 15 | rd << 7 | 0x67                    # jalr rd, lo(t1)
        return struct.pack("<II", auipc & 0xFFFFFFFF, jalr)

    def branch_length(self, isa, src=None, span=None):
        """The room the branch at a moved function's entry needs: 4 bytes (jal) when every address
        of span (the image's base to its limit) lies within 1 MiB of src, else 8 (auipc + jalr)."""
        if src is not None and span is not None and max(span[1] - src, src - span[0]) >= 1 << 20:
            return 8
        return 4

    def entry_branch(self, src, dst, isa, room=8):
        """jal when it reaches, else auipc t1 + jalr when the room allows (dread.overlay, dread.emu)."""
        try:
            return self.branch(src, dst, isa)
        except ValueError:
            if room < 8:
                raise
            return self.far_branch(src, dst, isa)

    def start_address(self, address, isa):
        return address

    def assembler_flags(self, compiler):
        """GNU as takes the instruction set from its own flags (clang's driver has cflags already)."""
        if compiler.kind == "clang":
            return []
        return [f for f in compiler.cflags if f.startswith(("-march=", "-mabi="))] or \
            ["-march=rv32%s" % self.extensions, "-mabi=ilp32"]

    def fixed_from_symbols(self, symbols):
        """The registers that keep one value (dread.target.Image.fixed) from an ELF's symbols: gp is
        __global_pointer$ (the psABI's convention; GCC's code addresses small data from it)."""
        value = symbols.get("__global_pointer$")
        return {"gp": value} if value is not None else {}

    def linker_flags(self, compiler):
        """GNU ld for RISC-V links 64-bit by default; lld takes the class from the objects."""
        return [] if compiler.kind == "clang" else ["-m", "elf32lriscv"]

    def isa_of_symbol(self, value):
        return "rv32"

    @staticmethod
    def symbol_asm(name, address, isa):
        return "\t.global %s\n\t.type %s, @function\n\t.set %s, 0x%x\n" % (name, name, name, address)

    # ---- Unicorn ----

    def uc_mode(self, isa=None):
        return uc.UC_MODE_RISCV32

    def new_uc(self, isa=None):
        return uc.Uc(uc.UC_ARCH_RISCV, uc.UC_MODE_RISCV32)

    CALL_CSRS = ("MSTATUS", "MIE", "MSCRATCH", "MEPC", "MCAUSE", "MTVAL", "MTVEC")

    def initial_status(self, engine):
        """Machine mode with interrupts off and the machine CSRs a function may read at zero, as
        every call finds them: a CSR keeps its value from one call to the next in an emulator, and
        each image has its own (dread liftfuzz found it: an mscratch the other image's earlier case
        had written)."""
        for name in self.CALL_CSRS:
            engine.reg_write(getattr(ur, "UC_RISCV_REG_" + name), 0)

    def status_reg(self):
        return ur.UC_RISCV_REG_MSTATUS

    def settle_it(self, engine):
        pass

    def flags_value(self, engine):
        return 0                            # no condition flags on RISC-V

    def pc_reg(self):
        return UC_REGS["pc"]

    def asm(self):
        """The listing's syntax (dread asm): RiscVAsm."""
        return RiscVAsm(self)

    # ---- the system run (dread.sysrun) ----

    MSTATUS_MIE, MSTATUS_MPIE, MIE_MEIE, EXTERNAL = 1 << 3, 1 << 7, 1 << 11, 11

    def reset(self, engine, read_word, entry, vectors):
        """Machine mode with interrupts off (mstatus.MIE and MPIE clear, mie clear), at the reset
        entry."""
        status = engine.reg_read(ur.UC_RISCV_REG_MSTATUS)
        engine.reg_write(ur.UC_RISCV_REG_MSTATUS, status & ~(self.MSTATUS_MIE | self.MSTATUS_MPIE))
        engine.reg_write(ur.UC_RISCV_REG_MIE, 0)
        return entry

    def interrupts_enabled(self, engine):
        """Whether the machine external interrupt (the interrupt controller's line to the core) may
        be taken: mstatus.MIE and mie.MEIE set. The handler runs with MIE clear: no nesting."""
        return bool(engine.reg_read(ur.UC_RISCV_REG_MSTATUS) & self.MSTATUS_MIE) and \
            bool(engine.reg_read(ur.UC_RISCV_REG_MIE) & self.MIE_MEIE)

    def enter_interrupt(self, engine, number, resume, vectors, read_word, write_word):
        """Take the machine external interrupt as the core does, returning to `resume`: mepc =
        resume, mcause = 11 with the interrupt bit, MPIE = MIE, MIE = 0, MPP = machine; on at mtvec
        (vectored mode: base + 4 * 11). `number` is the controller's line, not the core's cause.
        mret, at the handler's end, runs in the emulator itself."""
        status = engine.reg_read(ur.UC_RISCV_REG_MSTATUS)
        engine.reg_write(ur.UC_RISCV_REG_MEPC, resume)
        engine.reg_write(ur.UC_RISCV_REG_MCAUSE, 0x80000000 | self.EXTERNAL)
        engine.reg_write(ur.UC_RISCV_REG_MSTATUS, (status & ~(self.MSTATUS_MIE | self.MSTATUS_MPIE))
                         | (self.MSTATUS_MPIE if status & self.MSTATUS_MIE else 0) | 3 << 11)
        vector = engine.reg_read(ur.UC_RISCV_REG_MTVEC)
        return (vector & ~3) + (4 * self.EXTERNAL if vector & 3 == 1 else 0)

    def exception_return(self, engine, read_word):
        """Nothing to finish: mret runs in the emulator."""
        return None

    def resume_address(self, engine):
        return engine.reg_read(UC_REGS["pc"])


_EXACT = {}


def supports_exact(compiler, flags=()):
    """Whether the target's assembler knows `.option exact` (LLVM 19 and later; LLVM 18 warns and
    goes on compressing; GNU as warns "unrecognized .option directive" and goes on). flags: what
    the layer adds to the assembler (assembler_flags)."""
    import subprocess
    import tempfile
    command = tuple(compiler.assembler()) + tuple(flags)
    if command not in _EXACT:
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "probe.s")
            with open(source, "w") as f:
                f.write("\t.option exact\n\tnop\n")
            try:
                r = subprocess.run(list(command) + ["-o", os.path.join(folder, "probe.o"), source],
                                   capture_output=True, text=True)
                _EXACT[command] = r.returncode == 0 and "unknown option" not in r.stderr \
                    and "unrecognized" not in r.stderr
            except OSError:
                _EXACT[command] = False
    return _EXACT[command]


class RiscVAsm:
    """How dread asm writes RISC-V code: for clang's integrated assembler (llvm-mc) with `.option
    exact`, so that nothing is compressed or relaxed that the listing does not say. capstone's text
    is taken as it is: llvm-mc and GNU as read its compressed mnemonics (c.addi) and aliases (beqz,
    mv, li, jalr rd, rs1, imm) as the same encodings. Direct jumps and branches go to labels; an auipc
    or lui pair keeps its numbers (the same bytes at the same place). An assembler without `.option
    exact` (LLVM 18, GNU as) gets `.option norelax` and `.option norvc`, and `.option rvc` around each
    run of compressed instructions: it compresses only what is written compressed."""

    comment = "#"
    directives = {"word": ".4byte", "half": ".2byte", "byte": ".byte", "text": ".asciz"}

    def __init__(self, arch):
        self.arch = arch
        self.exact = True
        self.rvc = False

    def configure(self, compiler):
        self.exact = supports_exact(compiler, self.arch.assembler_flags(compiler))

    def prelude(self, isa):
        self.rvc = False
        return ["\t.option exact"] if self.exact else ["\t.option norelax", "\t.option norvc"]

    def before(self, insn):
        """Lines in front of an instruction: without `.option exact`, compression on for a
        compressed one and off for a full one."""
        if self.exact or (insn.size == 2) == self.rvc:
            return []
        self.rvc = insn.size == 2
        return ["\t.option rvc" if self.rvc else "\t.option norvc"]

    def switch(self, isa):
        return []

    def raw(self, b, isa):
        if len(b) == 2:
            return ".2byte", "0x%04x" % struct.unpack("<H", b)[0]
        return ".4byte", "0x%08x" % struct.unpack("<I", b)[0]

    def function_lines(self, label, isa, code):
        return ["\t.type   %s, @function" % label]

    def operands(self, listing, insn, run, st, isa):
        """(mnemonic, operands, comment) of one instruction for the listing, or None to write it raw."""
        f = fields(insn)
        mnem, ops = insn.mnemonic, insn.op_str
        if f.op == "jal" or f.op in ("beq", "bne", "blt", "bge", "bltu", "bgeu"):
            target = insn.address + f.imm
            tst = listing.prog.stored(target)
            if listing.space(target) == listing.space(run) and listing.has(tst, 2) and listing.labelable(tst):
                listing.labels.add(tst)
                return mnem, ("label", ops.rsplit(",", 1)[0] + "," if "," in ops else "", tst), ""
            return None
        comment = ""
        at = getattr(insn, "rv_address", None)
        if at is not None:
            comment = ("=0x%x %s" % (at, listing.describe_value(at))).strip()
        memory = getattr(insn, "rv_memory", None)
        if memory is not None:
            comment = "[0x%x]" % memory[0]
        return mnem, ops, comment
