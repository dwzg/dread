"""ARM: ARM state (a32) and Thumb (t32: 16- and 32-bit Thumb instructions), A profile (CPSR;
ARM7, ARM9, ARM11) and M profile (Cortex-M, Thumb only).

One place for what the tools need to know about the instruction set: how to decode, how long an
instruction is, where pc points when an instruction reads it, which instructions load a literal,
branch or call and where to, how to encode a branch, and how to set up Unicorn.

An image may mix both instruction sets (interworking on ARMv4T and later A-profile cores); the code
ranges table says which range is which (docs/formats.md), and every method here takes the isa of
the code it looks at. The target's [arch] isa is the default for code the table does not mark.

Only little-endian is supported. Big-endian ARM (BE-32 on old cores, BE-8 later) is not
implemented and not tested.
"""
import struct

import capstone as cs
import unicorn as uc
from capstone import arm as csarm
from unicorn import arm_const as ua

REGS = tuple("r%d" % i for i in range(13)) + ("sp", "lr", "pc")
ALIASES = {"sb": "r9", "sl": "r10", "fp": "r11", "ip": "r12", "r13": "sp", "r14": "lr", "r15": "pc"}
UC_REGS = {name: getattr(ua, "UC_ARM_REG_" + name.upper()) for name in REGS}


def _cpus():
    """The Unicorn CPU models by the names dread.toml uses: cortex-m3, arm926, arm1176, ..."""
    out = {}
    for attr in dir(ua):
        if attr.startswith("UC_CPU_ARM_") and attr != "UC_CPU_ARM_ENDING":
            suffix = attr[len("UC_CPU_ARM_"):].lower().replace("_", "-")
            name = suffix if suffix.startswith(("cortex", "pxa", "sa", "ti")) else "arm" + suffix
            out[name] = getattr(ua, attr)
    return out


CPUS = _cpus()

# Loads that may take a pc-relative address (a literal), with the bytes they read.
LITERAL_LOADS = {
    csarm.ARM_INS_LDR: 4, csarm.ARM_INS_LDRB: 1, csarm.ARM_INS_LDRSB: 1, csarm.ARM_INS_LDRH: 2,
    csarm.ARM_INS_LDRSH: 2, csarm.ARM_INS_LDRD: 8, csarm.ARM_INS_VLDR: 0,   # 0: size from the register
}
CALLS = (csarm.ARM_INS_BL, csarm.ARM_INS_BLX)
CONDITION_NAMES = {
    csarm.ARM_CC_EQ: "eq", csarm.ARM_CC_NE: "ne", csarm.ARM_CC_HS: "hs", csarm.ARM_CC_LO: "lo",
    csarm.ARM_CC_MI: "mi", csarm.ARM_CC_PL: "pl", csarm.ARM_CC_VS: "vs", csarm.ARM_CC_VC: "vc",
    csarm.ARM_CC_HI: "hi", csarm.ARM_CC_LS: "ls", csarm.ARM_CC_GE: "ge", csarm.ARM_CC_LT: "lt",
    csarm.ARM_CC_GT: "gt", csarm.ARM_CC_LE: "le", csarm.ARM_CC_AL: "",
}


class Arm:
    """The ARM layer. Constructed from [arch]: isa, profile, cpu, endian, ghidra."""

    name = "arm"
    default_args = 4
    default_callee_saved = ("r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11")
    default_result = ("r0", "r1")
    word = 4

    def __init__(self, isa="a32", profile="a", cpu=None, endian="little", ghidra=None):
        if isa not in ("a32", "t32"):
            raise ValueError("isa is a32 or t32, not %r" % (isa,))
        if profile not in ("a", "m"):
            raise ValueError("profile is a (classic, CPSR) or m (Cortex-M), not %r" % (profile,))
        if profile == "m" and isa != "t32":
            raise ValueError("an M-profile core runs Thumb only: isa = \"t32\"")
        if endian != "little":
            raise ValueError("only endian = \"little\" is supported")
        if cpu is not None and cpu not in CPUS:
            raise ValueError("unknown cpu %r (Unicorn knows: %s)" % (cpu, ", ".join(sorted(CPUS))))
        self.isa, self.profile, self.cpu, self.endian = isa, profile, cpu, endian
        # Ghidra's language id: Cortex for M profile, v4t for an A-profile core that can run Thumb
        # (ARM7TDMI and later), v4 otherwise. Override in [arch] ghidra for another variant.
        self.ghidra = ghidra or ("ARM:LE:32:Cortex" if profile == "m" else "ARM:LE:32:v4t")
        self._cs = {}

    def describe(self):
        return "arm %s, %s profile, cpu %s, %s-endian" % (
            self.isa, "M" if self.profile == "m" else "A", self.cpu or "Unicorn's default", self.endian)

    # ---- registers ----

    def reg_name(self, name):
        name = name.lower()
        name = ALIASES.get(name, name)
        if name not in UC_REGS:
            raise ValueError("unknown ARM register %r" % name)
        return name

    def reg_id(self, name):
        """The Unicorn register id of a register name (r0-r12, sp, lr, pc and the aliases)."""
        return UC_REGS[self.reg_name(name)]

    def arg_regs(self, n):
        return ["r%d" % i for i in range(n)]

    # ---- instruction sets ----

    def isas(self):
        return ("t32",) if self.profile == "m" else ("a32", "t32")

    def min_insn(self, isa):
        """The size of the shortest instruction, and the alignment of instructions."""
        return 4 if isa == "a32" else 2

    def insn_length(self, code, isa):
        """The length of the instruction that starts with `code`, without decoding it: 4 in ARM
        state; in Thumb 4 when the first halfword begins with 0b11101, 0b11110 or 0b11111."""
        if isa == "a32":
            return 4
        first = code[0] | code[1] << 8
        return 4 if (first >> 11) in (0x1D, 0x1E, 0x1F) else 2

    def disassembler(self, isa=None):
        """A capstone disassembler with details for one isa (cached)."""
        isa = isa or self.isa
        md = self._cs.get(isa)
        if md is None:
            mode = cs.CS_MODE_ARM if isa == "a32" else cs.CS_MODE_THUMB
            if self.profile == "m":
                mode |= cs.CS_MODE_MCLASS
            md = cs.Cs(cs.CS_ARCH_ARM, mode)
            md.detail = True
            self._cs[isa] = md
        return md

    def decode(self, code, address, isa=None):
        """The instruction at the start of `code` (capstone CsInsn), or None if it does not decode."""
        for insn in self.disassembler(isa).disasm(bytes(code), address, 1):
            return insn
        return None

    def pc_value(self, address, isa):
        """What pc reads as in the instruction at `address`."""
        return address + (8 if isa == "a32" else 4)

    # ---- what an instruction does (capstone instructions in) ----

    def condition(self, insn):
        """The condition suffix ("eq", "ne", ...; "" for always). Inside a Thumb IT block capstone
        gives each instruction the condition the IT instruction set for it."""
        return CONDITION_NAMES.get(insn.cc, "")

    def literal(self, insn, isa):
        """(address, size) a pc-relative load reads (ldr rX, [pc, #d], its byte, halfword and
        doubleword forms, vldr), or None. Size 0 for vldr: the register decides."""
        size = LITERAL_LOADS.get(insn.id)
        if size is None:
            return None
        for op in insn.operands:
            if op.type == csarm.ARM_OP_MEM and op.mem.base == csarm.ARM_REG_PC and op.mem.index == 0:
                base = self.pc_value(insn.address, isa)
                if isa == "t32":
                    base &= ~3
                disp = op.mem.disp
                if op.subtracted and disp > 0:
                    disp = -disp
                return (base + disp) & 0xFFFFFFFF, size
        return None

    def pc_address(self, insn, isa):
        """The address `adr rX, label` (or add/sub rX, pc, #imm) computes, or None."""
        if insn.id not in (csarm.ARM_INS_ADR, csarm.ARM_INS_ADD, csarm.ARM_INS_SUB, csarm.ARM_INS_ADDW,
                           csarm.ARM_INS_SUBW):
            return None
        ops = insn.operands
        base = self.pc_value(insn.address, isa)
        if isa == "t32":
            base &= ~3
        if insn.id == csarm.ARM_INS_ADR and len(ops) == 2 and ops[1].type == csarm.ARM_OP_IMM:
            return (base + ops[1].imm) & 0xFFFFFFFF     # capstone gives the offset, not the address
        if (len(ops) == 3 and ops[1].type == csarm.ARM_OP_REG and ops[1].reg == csarm.ARM_REG_PC
                and ops[2].type == csarm.ARM_OP_IMM):
            sign = -1 if insn.id in (csarm.ARM_INS_SUB, csarm.ARM_INS_SUBW) else 1
            return (base + sign * ops[2].imm) & 0xFFFFFFFF
        return None

    def target(self, insn):
        """The destination of a direct branch or call (b, bl, blx #imm, cbz, cbnz), or None for an
        indirect one or any other instruction."""
        if not (insn.group(cs.CS_GRP_JUMP) or insn.group(cs.CS_GRP_CALL)):
            return None
        for op in reversed(insn.operands):
            if op.type == csarm.ARM_OP_IMM:
                return op.imm & 0xFFFFFFFF
            break
        return None

    def is_call(self, insn):
        """bl or blx (direct or through a register)."""
        return insn.id in CALLS

    def is_conditional(self, insn):
        return insn.cc not in (csarm.ARM_CC_AL, csarm.ARM_CC_INVALID)

    def switches_isa(self, insn):
        """Whether a direct call changes the instruction set (blx #imm)."""
        return insn.id == csarm.ARM_INS_BLX and self.target(insn) is not None

    # ---- encoding ----

    def branch(self, src, dst, isa, link=False):
        """The bytes of an unconditional b (or bl) at `src` to `dst`, in the isa of the code at
        `src`. Raises ValueError when `dst` is out of reach (32 MiB in ARM state, 16 MiB in Thumb)."""
        if isa == "a32":
            off = dst - (src + 8)
            if off % 4 or not -(1 << 25) <= off < (1 << 25):
                raise ValueError("no ARM branch from 0x%x to 0x%x" % (src, dst))
            return struct.pack("<I", (0xEB000000 if link else 0xEA000000) | ((off >> 2) & 0xFFFFFF))
        off = dst - (src + 4)
        if off % 2 or not -(1 << 24) <= off < (1 << 24):
            raise ValueError("no Thumb branch from 0x%x to 0x%x" % (src, dst))
        s = (off >> 24) & 1
        j1 = ((~(off >> 23)) ^ s) & 1           # I1 = NOT(J1 XOR S)
        j2 = ((~(off >> 22)) ^ s) & 1
        first = 0xF000 | (s << 10) | ((off >> 12) & 0x3FF)
        second = (0xD000 if link else 0x9000) | (j1 << 13) | (j2 << 11) | ((off >> 1) & 0x7FF)
        return struct.pack("<HH", first, second)

    def branch_length(self, isa):
        return 4

    # ---- Unicorn ----

    def uc_mode(self, isa=None):
        if self.profile == "m":
            return uc.UC_MODE_THUMB | uc.UC_MODE_MCLASS
        return uc.UC_MODE_ARM if (isa or self.isa) == "a32" else uc.UC_MODE_THUMB

    def new_uc(self, isa=None):
        """A Unicorn engine for this core. In ARM state the engine changes to Thumb by itself when
        a start address or a branch target has bit 0 set."""
        engine = uc.Uc(uc.UC_ARCH_ARM, self.uc_mode(isa))
        if self.cpu is not None:
            engine.ctl_set_cpu_model(CPUS[self.cpu])
        return engine

    def start_address(self, address, isa):
        """The address to start or branch to for code of `isa` at `address` (bit 0 set for Thumb)."""
        return address | 1 if isa == "t32" else address & ~1

    def pc_reg(self):
        return UC_REGS["pc"]
