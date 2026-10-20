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
import re
import struct

import capstone as cs
import unicorn as uc
from capstone import arm as csarm
from unicorn import arm_const as ua

REGS = tuple("r%d" % i for i in range(13)) + ("sp", "lr", "pc")
ALIASES = {"sb": "r9", "sl": "r10", "fp": "r11", "ip": "r12", "r13": "sp", "r14": "lr", "r15": "pc"}
UC_REGS = {name: getattr(ua, "UC_ARM_REG_" + name.upper()) for name in REGS}
FP_REGS = tuple("s%d" % i for i in range(32)) + tuple("d%d" % i for i in range(16))   # VFP (FPv4-SP and up)
UC_REGS.update({name: getattr(ua, "UC_ARM_REG_" + name.upper()) for name in FP_REGS})


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

THUMB2_CPUS = ("cortex-m3", "cortex-m4", "cortex-m7", "cortex-m33", "cortex-m35", "cortex-m55", "cortex-m85",
               "cortex-a", "cortex-r", "arm1156t2")


def thumb2_of(cpu, cflags):
    """Whether -mcpu, -march or the cpu name says the core has Thumb-2; None when nothing says."""
    for flag in cflags:
        if flag.startswith("-mcpu="):
            return flag[6:].startswith(THUMB2_CPUS)
        if flag.startswith("-march="):
            arch = flag[7:]
            return arch.startswith(("armv6t2", "armv7", "armv8")) and not arch.startswith("armv8-m.base")
    return cpu.startswith(THUMB2_CPUS) if cpu else None

# Loads that may take a pc-relative address (a literal), with the bytes they read.
LITERAL_LOADS = {
    csarm.ARM_INS_LDR: 4, csarm.ARM_INS_LDRB: 1, csarm.ARM_INS_LDRSB: 1, csarm.ARM_INS_LDRH: 2,
    csarm.ARM_INS_LDRSH: 2, csarm.ARM_INS_LDRD: 8, csarm.ARM_INS_VLDR: 0,   # 0: size from the register
}
CALLS = (csarm.ARM_INS_BL, csarm.ARM_INS_BLX)
# The DSP extension (ARMv7E-M; ARMv5TE and later in ARM state)
DSP_IDS = {getattr(csarm, "ARM_INS_" + n) for n in (
    "QADD QSUB QDADD QDSUB QADD16 QADD8 QASX QSAX QSUB16 QSUB8 SADD16 SADD8 SASX SSAX SSUB16 SSUB8 SHADD16 SHADD8 "
    "SHASX SHSAX SHSUB16 SHSUB8 UADD16 UADD8 UASX USAX USUB16 USUB8 UHADD16 UHADD8 UHASX UHSAX UHSUB16 UHSUB8 "
    "UQADD16 UQADD8 UQASX UQSAX UQSUB16 UQSUB8 SEL SSAT SSAT16 USAT USAT16 SMLAD SMLADX SMLSD SMLSDX SMUAD SMUADX "
    "SMUSD SMUSDX SMLALD SMLALDX SMLSLD SMLSLDX SMMUL SMMULR SMMLA SMMLAR SMMLS SMMLSR SMLABB SMLABT SMLATB SMLATT "
    "SMULBB SMULBT SMULTB SMULTT SMULWB SMULWT SMLAWB SMLAWT SMLALBB SMLALBT SMLALTB SMLALTT PKHBT PKHTB SXTAB16 "
    "UXTAB16 SXTB16 UXTB16 USAD8 USADA8 UMAAL").split() if hasattr(csarm, "ARM_INS_" + n)}
# Instructions that add into their destination pair (RdLo, RdHi: read and written)
ACCUMULATE_PAIR = {getattr(csarm, "ARM_INS_" + n) for n in (
    "UMLAL SMLAL SMLALD SMLALDX SMLSLD SMLSLDX SMLALBB SMLALBT SMLALTB SMLALTT UMAAL").split()
    if hasattr(csarm, "ARM_INS_" + n)}
# The FPU's block transfers: capstone lists vldm's registers as not written, vstm's as not read and
# vpush's and vpop's as both (operand_roles replaces its lists for these)
FPU_BLOCK = {getattr(csarm, "ARM_INS_" + n) for n in "VLDMIA VLDMDB VSTMIA VSTMDB VPUSH VPOP".split()
             if hasattr(csarm, "ARM_INS_" + n)}
# Where capstone's own access lists are wrong (operand_roles): the accumulating pairs, which it
# lists as written only, ssat16 and usat16, whose source it does not list at all, and FPU_BLOCK
OWN_ROLES = DSP_IDS | ACCUMULATE_PAIR | FPU_BLOCK
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
    reg_bits = 32
    syscall_numbers = (2,)      # Unicorn's exception number of svc (dread.emu runs it as a logged no-op)
    link_reg = "lr"         # where a call leaves the return address
    veneer_reg = "r12"      # the register a veneer or a call through a register may go through (ip)

    def __init__(self, isa="a32", profile="a", cpu=None, endian="little", ghidra=None, thumb2=None):
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
        if profile == "m":
            self.syscall_exception = 11     # SVCall
        self.thumb2 = thumb2        # whether Thumb code may use 32-bit Thumb-2 (b.w): configure()
        # Ghidra's language id: Cortex for M profile, v4t for an A-profile core that can run Thumb
        # (ARM7TDMI and later), v4 otherwise. Override in [arch] ghidra for another variant.
        self.ghidra = ghidra or ("ARM:LE:32:Cortex" if profile == "m" else "ARM:LE:32:v4t")
        self.ghidra_cspec = "default"
        self._cs = {}

    def configure(self, cflags):
        """What the target's compiler flags say about the core: whether its Thumb has Thumb-2
        (-mcpu or -march; else [arch] cpu; unknown: yes on the M profile, no on the A profile, where
        Thumb without a stated architecture is ARM7 or ARM9 code)."""
        if self.thumb2 is None:
            self.thumb2 = thumb2_of(self.cpu, cflags)
        if self.thumb2 is None:
            self.thumb2 = self.profile == "m"

    def describe(self):
        return "arm %s, %s profile, cpu %s, %s-endian, Thumb%s" % (
            self.isa, "M" if self.profile == "m" else "A", self.cpu or "Unicorn's default", self.endian,
            "-2" if self.thumb2 or self.thumb2 is None else "-1 only")

    # ---- registers ----

    def reg_name(self, name):
        name = name.lower()
        name = ALIASES.get(name, name)
        if name not in UC_REGS:
            raise ValueError("unknown ARM register %r" % name)
        return name

    def reg_id(self, name):
        """The Unicorn register id of a register name (r0-r12, sp, lr, pc and the aliases; the VFP
        registers s0-s31 and d0-d15, which the core's own model has or has not)."""
        return UC_REGS[self.reg_name(name)]

    def arg_regs(self, n):
        return ["r%d" % i for i in range(n)]

    def fp_arg_regs(self):
        """The floating-point argument registers of the procedure call standard's VFP variant
        (s0-s15, which are d0-d7): where a hard-float build passes float and double arguments."""
        return ["s%d" % i for i in range(16)]

    def fp_saved_regs(self):
        """The floating-point registers a call keeps (AAPCS: s16-s31, d8-d15)."""
        return ["s%d" % i for i in range(16, 32)]

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

    def link_pair(self, prev, insn, isa):
        """Whether `insn` is ARMv4T's call through a register: an indirect jump (bx rN, mov pc, rN or
        ldr pc, [...]) right behind mov lr, pc under the same condition, in ARM state, so that lr
        holds the address behind the jump. dread.program marks such jumps (insn.link_call) when it
        decodes a range, and flow() then calls them indirect calls."""
        if isa != "a32" or prev is None or prev.id != csarm.ARM_INS_MOV or len(prev.operands) != 2:
            return False
        lr, pc = prev.operands
        if not (lr.type == pc.type == csarm.ARM_OP_REG and lr.reg == csarm.ARM_REG_LR and pc.reg == csarm.ARM_REG_PC):
            return False
        if prev.address + prev.size != insn.address or prev.cc != insn.cc:
            return False
        ops = insn.operands
        if insn.id == csarm.ARM_INS_BX:
            return bool(ops) and ops[0].reg != csarm.ARM_REG_LR
        if insn.id in (csarm.ARM_INS_MOV, csarm.ARM_INS_LDR) and ops and ops[0].type == csarm.ARM_OP_REG \
                and ops[0].reg == csarm.ARM_REG_PC:
            return len(ops) == 2 and (ops[1].type == csarm.ARM_OP_MEM or ops[1].type == csarm.ARM_OP_REG)
        return False

    def mark_call(self, program, insn):
        """Marks a direct call that is not an ordinary one (dread.program calls this once for every
        instruction it hands out): bl to a stub that is only bx rN (GCC's Thumb-1 call through a
        register, bl __call_via_r3 or a bx r3 behind the function) gets insn.call_via = "r3"; bl to a
        helper that computes its return address from lr (GCC's __gnu_thumb1_case_* switches, whose
        table follows the call) gets insn.dispatch_call. flow() reads both."""
        if insn.id != csarm.ARM_INS_BL:
            return
        target = self.target(insn)
        if target is None:
            return
        stub = program.insn(target & ~1)
        if stub is None:
            return
        ops = stub.operands
        if stub.id == csarm.ARM_INS_BX and ops and ops[0].reg not in (csarm.ARM_REG_LR, csarm.ARM_REG_PC):
            insn.call_via = self._name(stub.reg_name(ops[0].reg))
        elif self._computes_return(program, target & ~1):
            insn.dispatch_call = True

    def _computes_return(self, program, address, limit=16):
        """Whether the straight code at `address` reads lr and then writes it, or leaves through a
        register other than lr: a helper that returns behind its call site, not to it. A call ends
        the search with no: its bl writes lr by calling, and a dispatcher calls nothing (libgcc's
        __aeabi_dcmpgt, str lr, [sp, #-8]! then bl __aeabi_cdrcmple, was taken for one, and every
        call of it for a jump through a table)."""
        read = False
        for _ in range(limit):
            insn = program.insn(address)
            if insn is None:
                return False
            kind = self.flow(insn)[0]
            reads, writes = self.reg_effects(insn)
            stack = self.stack_kind(insn)
            if kind in ("return", "call", "icall"):
                return False
            if stack != "push" and "lr" in reads:
                read = True
            if "lr" in writes and stack != "pop":
                return read
            if kind == "ijump":
                return read
            if kind != "next":
                return False
            address += insn.size
        return False

    def is_call(self, insn):
        """bl or blx (direct or through a register)."""
        return insn.id in CALLS

    def is_conditional(self, insn):
        return insn.cc not in (csarm.ARM_CC_AL, csarm.ARM_CC_INVALID)

    def switches_isa(self, insn):
        """Whether a direct call changes the instruction set (blx #imm)."""
        return insn.id == csarm.ARM_INS_BLX and self.target(insn) is not None

    # ---- control and register flow (dread.regflow) ----

    def general_regs(self):
        """The general registers a function may use for values (not sp, lr, pc)."""
        return ["r%d" % i for i in range(13)]

    def flow(self, insn):
        """(kind, target, falls): how control leaves an instruction. kind is "next", "jump" (direct,
        to target), "call" (direct, to target), "icall" (through a register; also svc, whose handler
        may return values in any register and in the flags), "return", "ijump" (indirect jump; for
        bx rN the target is the register's name) or "stop" (udf). falls: the next instruction may
        run after it (conditional, cbz/cbnz, calls).

        Capstone does not list every pc write (tbb, tbh, cbz, cbnz show none), so the kind is
        decided by instruction first."""
        i = insn.id
        conditional = self.is_conditional(insn)
        if getattr(insn, "link_call", False) or getattr(insn, "call_via", None):
            return "icall", None, True      # mov lr, pc in front, or bl to a bx rN stub (mark_call)
        if getattr(insn, "dispatch_call", False):
            return "ijump", None, False     # bl to a switch helper, its table behind (mark_call)
        if i == csarm.ARM_INS_SVC:
            return "icall", None, True      # its handler may return values in any register and the flags
        if i in CALLS:
            target = self.target(insn)
            return ("call" if target is not None else "icall"), target, True
        if i == csarm.ARM_INS_B:
            return "jump", self.target(insn), conditional
        if i in (csarm.ARM_INS_CBZ, csarm.ARM_INS_CBNZ):
            return "jump", self.target(insn), True
        if i in (csarm.ARM_INS_TBB, csarm.ARM_INS_TBH):
            return "ijump", None, conditional
        if i == csarm.ARM_INS_UDF:
            return "stop", None, False
        if i == csarm.ARM_INS_BX:
            reg = insn.operands[0].reg if insn.operands else 0
            if reg == csarm.ARM_REG_LR:
                return "return", None, conditional
            if reg == csarm.ARM_REG_PC:     # into ARM state at the next word (an ARMv4T veneer's bx pc)
                return "jump", (insn.address + (4 if insn.size == 2 else 8)) & ~3, conditional
            return "ijump", self._name(insn.reg_name(reg)), conditional
        _, written = insn.regs_access()
        if csarm.ARM_REG_PC in written:
            if self.stack_kind(insn) == "pop":
                return "return", None, conditional
            if i in (csarm.ARM_INS_LDM, csarm.ARM_INS_LDMIB, csarm.ARM_INS_LDMDA, csarm.ARM_INS_LDMDB) and any(
                    o.type == csarm.ARM_OP_REG and o.reg == csarm.ARM_REG_SP for o in insn.operands[1:]):
                return "return", None, conditional      # an APCS frame's epilogue: ldmdb fp, {.., sp, pc}
            ops = insn.operands
            if (i == csarm.ARM_INS_MOV and len(ops) == 2 and ops[1].type == csarm.ARM_OP_REG
                    and ops[1].reg == csarm.ARM_REG_LR):
                return "return", None, conditional
            if (i == csarm.ARM_INS_SUB and len(ops) == 3 and ops[1].type == csarm.ARM_OP_REG
                    and ops[1].reg == csarm.ARM_REG_LR and ops[2].type == csarm.ARM_OP_IMM):
                return "return", None, conditional      # subs pc, lr, #4: the return from an exception
            return "ijump", None, conditional
        return "next", None, False

    def bound_check(self, insn):
        """(register, limit, before) when the instruction bounds a switch index: cmp rN, #K (the
        index is rN, at most K + 1 values pass; set before the compare) or and rN, rM, #(2^n - 1)
        (rN takes 0 .. 2^n - 1; set after the and). None otherwise. Used by dread.switches."""
        ops = insn.operands
        if insn.id == csarm.ARM_INS_CMP and len(ops) == 2 and ops[0].type == csarm.ARM_OP_REG \
                and ops[1].type == csarm.ARM_OP_IMM and 0 <= ops[1].imm < 0x10000:
            return self._name(insn.reg_name(ops[0].reg)), ops[1].imm, True
        if insn.id == csarm.ARM_INS_AND and len(ops) == 3 and ops[0].type == csarm.ARM_OP_REG \
                and ops[2].type == csarm.ARM_OP_IMM and ops[2].imm > 0 and ops[2].imm & (ops[2].imm + 1) == 0 \
                and ops[2].imm < 0x10000:
            return self._name(insn.reg_name(ops[0].reg)), ops[2].imm, False
        return None

    SWITCH_MAX = 1024       # table entries read at most

    def switch_targets(self, insn, program, inside):
        """The targets of a switch dispatch whose table lies right behind the instruction, or None
        when the instruction is not such a dispatch:
          tbb/tbh [pc, rN]               Thumb: byte or halfword offsets (target = table + 2 * entry)
          ldr pc, [pc, rN, lsl #2]       ARM: addresses
          add pc, pc, rN, lsl #2         ARM: a run of branches, each one a target
        A table's length is not encoded; it is read while its entries lie in the function
        (`inside(address)`) and, for offsets, before the lowest target found (the code that follows
        the table)."""
        from dread.target import TargetError
        i, ops = insn.id, insn.operands
        if i in (csarm.ARM_INS_TBB, csarm.ARM_INS_TBH):
            size = 1 if i == csarm.ARM_INS_TBB else 2
            base = insn.address + 4
            targets, a, low = [], base, None
            for _ in range(self.SWITCH_MAX):
                if low is not None and a >= low:
                    break
                try:
                    entry = int.from_bytes(program.read(a, size), "little")
                except TargetError:
                    break
                target = base + 2 * entry
                if not inside(target):
                    break
                targets.append(target)
                low = target if low is None else min(low, target)
                a += size
            return targets
        if len(ops) < 2 or ops[0].type != csarm.ARM_OP_REG or ops[0].reg != csarm.ARM_REG_PC:
            return None
        if (i == csarm.ARM_INS_LDR and ops[1].type == csarm.ARM_OP_MEM and ops[1].mem.base == csarm.ARM_REG_PC
                and ops[1].mem.index and ops[1].shift.type == csarm.ARM_SFT_LSL and ops[1].shift.value == 2):
            targets, a = [], insn.address + 8
            for _ in range(self.SWITCH_MAX):
                try:
                    word = program.word(a)
                except TargetError:
                    break
                if not inside(word & ~1):
                    break
                targets.append(word & ~1)
                a += 4
            return targets
        if (i == csarm.ARM_INS_ADD and len(ops) == 3 and ops[1].type == csarm.ARM_OP_REG
                and ops[1].reg == csarm.ARM_REG_PC and ops[2].type == csarm.ARM_OP_REG
                and ops[2].shift.type == csarm.ARM_SFT_LSL and ops[2].shift.value == 2):
            targets, a = [], insn.address + 8
            for _ in range(self.SWITCH_MAX):
                if not inside(a):
                    break
                branch = program.insn(a)
                if branch is None or branch.id != csarm.ARM_INS_B or self.is_conditional(branch):
                    break
                targets.append(a)
                a += 4
            return targets
        return None

    def sp_copy(self, insn):
        """rd of an instruction that sets rd to sp plus a constant (add rd, sp, #imm; mov rd, sp), else
        None (dread.regflow follows such copies through a function's entry)."""
        ops = insn.operands
        if insn.id in (csarm.ARM_INS_ADD, csarm.ARM_INS_MOV) and self.predicate(insn) is None and len(ops) >= 2 \
                and ops[0].type == csarm.ARM_OP_REG and ops[1].type == csarm.ARM_OP_REG \
                and ops[1].reg == csarm.ARM_REG_SP and (len(ops) == 2 or ops[2].type == csarm.ARM_OP_IMM):
            return self._name(insn.reg_name(ops[0].reg))
        return None

    def frame_stores(self, insn, sp_based):
        """The registers a store puts where a register of sp_based (sp, or a copy of it plus a
        constant) points: str, strd, strh, strb, stm and stmdb. An entry spill (a variadic function
        keeps r1-r3 next to its stack arguments) is no use of them (dread.regflow)."""
        ops = insn.operands
        i = insn.id
        if i in (csarm.ARM_INS_STR, csarm.ARM_INS_STRD, csarm.ARM_INS_STRH, csarm.ARM_INS_STRB) and ops \
                and ops[-1].type == csarm.ARM_OP_MEM and not ops[-1].mem.index:
            if self._name(insn.reg_name(ops[-1].mem.base)) in sp_based:
                return {self._name(insn.reg_name(o.reg)) for o in ops[:-1] if o.type == csarm.ARM_OP_REG}
        if i in (csarm.ARM_INS_STM, csarm.ARM_INS_STMDB) and ops and ops[0].type == csarm.ARM_OP_REG \
                and self._name(insn.reg_name(ops[0].reg)) in sp_based:
            return {self._name(insn.reg_name(o.reg)) for o in ops[1:] if o.type == csarm.ARM_OP_REG}
        return set()

    def pushed_registers(self, insn):
        """The core registers a push stores, from the lowest address up (an FPU register or the
        unused word of str lr, [sp, #-8]! as None): a function's save area (dread.runner)."""
        if self.stack_kind(insn) != "push":
            return []
        regs = [self._name(insn.reg_name(o.reg)) for o in insn.operands
                if o.type == csarm.ARM_OP_REG and o.reg != csarm.ARM_REG_SP]
        regs = [r if r in self.general_regs() or r == "lr" else None for r in regs]
        return regs + [None] * (self.stack_bytes(insn) // 4 - len(regs))

    def frame_slot(self, insn):
        """("store" or "load", register, offset) of str rN, [sp, #imm] and ldr rN, [sp, #imm] without
        writeback, index or condition (not ldr pc), else None (dread.regflow: saves and restores around
        a call)."""
        ops = insn.operands
        if insn.id in (csarm.ARM_INS_STR, csarm.ARM_INS_LDR) and not insn.writeback and self.predicate(insn) is None \
                and len(ops) == 2 and ops[0].type == csarm.ARM_OP_REG and ops[1].type == csarm.ARM_OP_MEM \
                and not ops[1].mem.index and ops[1].mem.base == csarm.ARM_REG_SP and ops[0].reg != csarm.ARM_REG_PC:
            return ("store" if insn.id == csarm.ARM_INS_STR else "load"), self._name(insn.reg_name(ops[0].reg)), \
                ops[1].mem.disp
        return None

    def stack_kind(self, insn):
        """"push" for push, vpush, stmdb sp! and str rN, [sp, #-4]! (or #-8); "pop" for pop, vpop,
        ldm sp! and ldr rN, [sp], #4 (or #8); else None. (Capstone shows the single-register forms as
        str/ldr or as push/pop depending on the encoding.) stack_bytes says how far sp moves."""
        i = insn.id
        if i in (csarm.ARM_INS_PUSH, csarm.ARM_INS_VPUSH):
            return "push"
        if i in (csarm.ARM_INS_POP, csarm.ARM_INS_VPOP):
            return "pop"
        ops = insn.operands
        if i in (csarm.ARM_INS_STR, csarm.ARM_INS_LDR) and insn.writeback and len(ops) >= 2 \
                and ops[1].type == csarm.ARM_OP_MEM and ops[1].mem.base == csarm.ARM_REG_SP and not ops[1].mem.index:
            if i == csarm.ARM_INS_STR and ops[1].mem.disp in (-4, -8) and len(ops) == 2:
                return "push"
            if i == csarm.ARM_INS_LDR and len(ops) == 3 and ops[2].type == csarm.ARM_OP_IMM and ops[2].imm in (4, 8):
                return "pop"
        if i in (csarm.ARM_INS_STMDB, csarm.ARM_INS_LDM) and insn.writeback and insn.operands \
                and insn.operands[0].type == csarm.ARM_OP_REG and insn.operands[0].reg == csarm.ARM_REG_SP:
            return "push" if i == csarm.ARM_INS_STMDB else "pop"
        return None

    def stack_bytes(self, insn):
        """The bytes a push or pop (stack_kind) moves sp by: four for each core or single-precision
        register and eight for each double one (vpush {d8}), or the offset of the single-register
        forms (str rN, [sp, #-8]! and ldr rN, [sp], #8, with which libgcc's assembly helpers keep the
        stack aligned to eight bytes: the word above the register is left as it was)."""
        ops = insn.operands
        if insn.id == csarm.ARM_INS_STR:
            return -ops[1].mem.disp
        if insn.id == csarm.ARM_INS_LDR:
            return ops[2].imm
        return sum(8 if (insn.reg_name(o.reg) or "")[:1] == "d" else 4
                   for o in ops if o.type == csarm.ARM_OP_REG and o.reg != csarm.ARM_REG_SP)

    def operand_roles(self, insn):
        """[(capstone register id, read, written)] of an instruction's register operands, in order,
        where capstone's access lists are wrong (OWN_ROLES; capstone 5 lists umlal's RdLo and RdHi
        as written only and leaves ssat16's source out): the first register is written, the second
        too where the instruction adds into a pair (and both are read then); the others are read.
        None for other instructions."""
        if insn.id not in OWN_ROLES:
            return None
        if insn.id in FPU_BLOCK:            # the base register (or sp), then the registers moved
            regs = [o.reg for o in insn.operands if o.type == csarm.ARM_OP_REG]
            load = insn.id in (csarm.ARM_INS_VLDMIA, csarm.ARM_INS_VLDMDB, csarm.ARM_INS_VPOP)
            if insn.id in (csarm.ARM_INS_VPUSH, csarm.ARM_INS_VPOP):
                return [(csarm.ARM_REG_SP, True, True)] + [(r, not load, load) for r in regs]
            return [(regs[0], True, bool(insn.writeback))] + [(r, not load, load) for r in regs[1:]]
        pair = 2 if insn.id in ACCUMULATE_PAIR else 1
        regs = [o.reg for o in insn.operands if o.type == csarm.ARM_OP_REG]
        return [(r, k >= 1 or pair == 2, k < pair) for k, r in enumerate(regs)]

    def reg_effects(self, insn):
        """(reads, writes): the registers an instruction reads and writes, by name (r0-r12, sp, lr,
        pc, "flags" for the condition flags; others by capstone's name). Flags are read by every
        conditional instruction and written by every instruction that sets them (capstone reports
        that in update_flags, not in its register lists, and claims it for adc, sbc and rsc without
        s as well: sets_flags() decides). A conditional instruction's writes may not happen:
        regflow does not let them end a register."""
        r, w = insn.regs_access()
        reads = {self._name(insn.reg_name(x)) for x in r}
        writes = {self._name(insn.reg_name(x)) for x in w}
        if insn.id in FPU_BLOCK:
            reads, writes = set(), set()   # (capstone's lists are wrong both ways: ours alone)
        for reg, read, written in self.operand_roles(insn) or ():       # capstone's gaps
            if read:
                reads.add(self._name(insn.reg_name(reg)))
            if written:
                writes.add(self._name(insn.reg_name(reg)))
        reads, writes = self._singles(reads), self._singles(writes)    # d<n> as s<2n>, s<2n+1>
        if self.sets_flags(insn) or insn.id == csarm.ARM_INS_FMSTAT:     # (vmrs APSR_nzcv, fpscr)
            writes.add("flags")
        if self.is_conditional(insn) and insn.id != csarm.ARM_INS_IT:
            reads.add("flags")
        reads.discard(None)
        writes.discard(None)
        return reads, writes

    @staticmethod
    def _singles(names):
        """FPU register names in single-precision units: d<n> (n < 16) as s<2n> and s<2n+1>."""
        out = set()
        for name in names:
            if name and name[0] == "d" and name[1:].isdigit() and int(name[1:]) < 16:
                out.update(("s%d" % (2 * int(name[1:])), "s%d" % (2 * int(name[1:]) + 1)))
            else:
                out.add(name)
        return out

    COMPARES = (csarm.ARM_INS_CMP, csarm.ARM_INS_CMN, csarm.ARM_INS_TST, csarm.ARM_INS_TEQ)

    def sets_flags(self, insn):
        """Whether an instruction sets the condition flags. Capstone's update_flags is also true for
        adc, sbc and rsc without s (and for the 16-bit forms inside an IT block, which set nothing),
        so the mnemonic decides: the s in front of the condition and the width suffix. Compares
        always set them; other instructions whose mnemonic does not end in s (msr, the exception
        returns) as capstone says."""
        if insn.id in self.COMPARES:
            return True
        if not insn.update_flags:
            return False
        mnemonic = insn.mnemonic.split(".")[0]
        cond = self.condition(insn)
        if cond and mnemonic.endswith(cond):
            mnemonic = mnemonic[:-len(cond)]
        if insn.id in (csarm.ARM_INS_ADC, csarm.ARM_INS_SBC, csarm.ARM_INS_RSC) or mnemonic.endswith("s"):
            return mnemonic.endswith("s")
        return True

    @staticmethod
    def _name(name):
        if name is None:
            return None
        name = name.lower()
        if name in ("cpsr", "apsr", "apsr_nzcv", "apsr_nzcvq", "apsr_nzcvqg"):
            return "flags"
        if name == "itstate":
            return None
        return ALIASES.get(name, name)

    def predicate(self, insn):
        """The condition a predicated instruction runs under (an opaque value), or None. The IT
        instruction itself is not predicated; the instructions it covers are."""
        if insn.id == csarm.ARM_INS_IT or not self.is_conditional(insn):
            return None
        return insn.cc

    @staticmethod
    def complement(predicate):
        """The opposite condition: eq/ne, hs/lo, mi/pl, vs/vc, hi/ls, ge/lt, gt/le."""
        return predicate + 1 if predicate % 2 else predicate - 1

    # ---- the whole system (dread.sysrun) ----

    def reset(self, engine, read_word, entry, vectors):
        """Put the core into its state after reset and return where it starts (bit 0 set for
        Thumb). A profile: supervisor mode with IRQ and FIQ masked, at the reset entry. M profile:
        the stack pointer and the reset handler from the first two words of the vector table,
        Thumb state, interrupts enabled."""
        if self.profile == "m":
            engine.reg_write(ua.UC_ARM_REG_SP, read_word(vectors))
            engine.reg_write(ua.UC_ARM_REG_XPSR, 0x01000000)
            return read_word(vectors + 4) | 1
        engine.reg_write(ua.UC_ARM_REG_CPSR, 0xD3)
        return entry

    EXC_RETURN = 0xFFFFFFE0                 # M profile: branches at or above this return from an exception

    def interrupts_enabled(self, engine):
        """Whether an interrupt may be taken now, for a controller that does not decide itself
        (may_take): A profile, the I bit is clear; M profile, PRIMASK is clear and the core runs no
        exception handler (one level)."""
        if self.profile == "m":
            return not engine.reg_read(ua.UC_ARM_REG_PRIMASK) & 1 and not engine.reg_read(ua.UC_ARM_REG_XPSR) & 0x1FF
        return not engine.reg_read(ua.UC_ARM_REG_CPSR) & 0x80

    def mask_registers(self, priorities):
        """[(register, bits)]: what cpu_masks (priorities) or interrupts_enabled reads, for the system
        run's C hook to tell that an answer still holds (dread.sysrun). Keep the three alike."""
        if self.profile == "m":
            if priorities:
                return [(ua.UC_ARM_REG_PRIMASK, 1), (ua.UC_ARM_REG_FAULTMASK, 1), (ua.UC_ARM_REG_BASEPRI, 0xFF)]
            return [(ua.UC_ARM_REG_PRIMASK, 1), (ua.UC_ARM_REG_XPSR, 0x1FF)]
        return [] if priorities else [(ua.UC_ARM_REG_CPSR, 0x80)]

    def cpu_masks(self, engine):
        """M profile: the core's PRIMASK, FAULTMASK and BASEPRI, for a controller with priorities."""
        if self.profile != "m":
            return {}
        return {"primask": engine.reg_read(ua.UC_ARM_REG_PRIMASK) & 1,
                "faultmask": engine.reg_read(ua.UC_ARM_REG_FAULTMASK) & 1,
                "basepri": engine.reg_read(ua.UC_ARM_REG_BASEPRI) & 0xFF}

    # Unicorn's exception numbers in a system run: svc (2) and, M profile, the start of an exception
    # return (8, a branch to EXC_RETURN in handler mode; with an interrupt hook in place Unicorn would
    # go on at the EXC_RETURN address)
    syscall_exception = None                # the exception svc raises (M profile: SVCall)

    def system_exception(self, intno):
        """What an exception Unicorn raised means in a system run: "syscall", "return" or None (a
        fault). Only the M profile's are modelled."""
        if self.profile == "m":
            return {2: "syscall", 8: "return"}.get(intno)
        return None

    def enter_interrupt(self, engine, number, resume, vectors, read_word, write_word, cause=None):
        """Take interrupt `number` (A profile: the IRQ, number ignored; M profile: the exception
        number, 16 + the line) as the core does, returning to `resume`. Returns where to go on.

        A profile: SPSR_irq = CPSR, IRQ mode with I set in ARM state, LR_irq = the return address
        + 4, at vectors + 0x18. M profile: r0-r3, r12, lr, the return address and xPSR pushed on the
        stack in use (the process stack in thread mode with CONTROL.SPSEL, else the main stack;
        aligned to 8 bytes, bit 9 of the stacked xPSR telling so), the main stack from then on,
        LR = EXC_RETURN (0xFFFFFFF1 from a handler, 0xFFFFFFF9 from thread mode on the main stack,
        0xFFFFFFFD on the process stack), IPSR = number, at the handler of the vector table. With
        the FPU's state active (CONTROL.FPCA, which the core sets when it runs an FPU instruction)
        the frame is the extended one, s0-s15, FPSCR and a reserved word behind the eight (0x68
        bytes), EXC_RETURN's bit 4 is clear (0xFFFFFFE1, E9, ED) and FPCA is cleared for the
        handler. The hardware's lazy stacking (space reserved, the registers written when the
        handler first uses the FPU) is modelled as eager stacking: the registers come out the same;
        only the reserved words' memory differs when a handler never uses the FPU.

        Both profiles keep the interrupted code's IT state (in the stacked xPSR, in SPSR) and clear it
        for the handler: an interrupt can come between the instructions of an IT block, since the
        system run takes interrupts where translation blocks start and QEMU's 1 KiB pages end one
        inside an IT block (FreeRTOS's Cortex-M4F build, newlib's strcmp: `it cs` at 0x7fe,
        `cmpcs` at 0x800). Stacked cleared, the rest of the block ran unconditionally, and strcmp
        compared past the end of equal strings."""
        if self.profile == "m":
            xpsr = engine.reg_read(ua.UC_ARM_REG_XPSR)
            handler = xpsr & 0x1FF
            control = engine.reg_read(ua.UC_ARM_REG_CONTROL)
            process = not handler and control & 2
            sp = engine.reg_read(ua.UC_ARM_REG_SP)
            if sp & 4:
                sp -= 4
                xpsr |= 1 << 9
            regs = [engine.reg_read(getattr(ua, "UC_ARM_REG_" + r)) for r in ("R0", "R1", "R2", "R3", "R12", "LR")]
            frame = regs + [resume & ~1, xpsr | 0x01000000]
            fp = control & 4                # CONTROL.FPCA: the extended frame
            if fp:
                frame += [engine.reg_read(getattr(ua, "UC_ARM_REG_S%d" % k)) for k in range(16)]
                frame += [engine.reg_read(ua.UC_ARM_REG_FPSCR), 0]
            sp -= 4 * len(frame)
            for i, value in enumerate(frame):
                write_word(sp + 4 * i, value)
            engine.reg_write(ua.UC_ARM_REG_SP, sp)
            if process or fp:               # in thread mode, a write of SPSEL switches the stack
                engine.reg_write(ua.UC_ARM_REG_CONTROL, control & ~(2 if process else 0) & ~4)
            exc_return = 0xFFFFFFF1 if handler else 0xFFFFFFFD if process else 0xFFFFFFF9
            engine.reg_write(ua.UC_ARM_REG_LR, exc_return & ~0x10 if fp else exc_return)
            engine.reg_write(ua.UC_ARM_REG_XPSR, 0x01000000 | (number & 0x1FF))
            return read_word(vectors + 4 * number) | 1
        cpsr = engine.reg_read(ua.UC_ARM_REG_CPSR)
        engine.reg_write(ua.UC_ARM_REG_CPSR, (cpsr & ~0x3F & ~self.IT_MASK) | 0x12 | 0x80)
        engine.reg_write(ua.UC_ARM_REG_SPSR, cpsr)
        engine.reg_write(ua.UC_ARM_REG_LR, (resume & ~1) + 4)
        return vectors + 0x18

    def exception_return(self, engine, read_word):
        """M profile: finish the return the core began by branching to EXC_RETURN (Unicorn stops
        there): pop the frame from the stack EXC_RETURN names (bit 2: the process stack), the
        extended one with s0-s15 and FPSCR where bit 4 is clear (CONTROL.FPCA set again, else
        cleared), to thread mode (bit 3) or the handler that was interrupted, and return where to go
        on; None when pc is no EXC_RETURN value."""
        pc = engine.reg_read(ua.UC_ARM_REG_PC)
        if self.profile != "m" or pc < self.EXC_RETURN:
            return None
        process, thread = pc & 4, pc & 8
        sp = engine.reg_read(ua.UC_ARM_REG_PSP if process else ua.UC_ARM_REG_MSP)
        frame = [read_word(sp + 4 * i) for i in range(8)]
        for r, value in zip(("R0", "R1", "R2", "R3", "R12", "LR"), frame):
            engine.reg_write(getattr(ua, "UC_ARM_REG_" + r), value)
        fp = not pc & 0x10
        if fp:
            for k in range(16):
                engine.reg_write(getattr(ua, "UC_ARM_REG_S%d" % k), read_word(sp + 32 + 4 * k))
            engine.reg_write(ua.UC_ARM_REG_FPSCR, read_word(sp + 96))
        sp += (0x68 if fp else 32) + (4 if frame[7] & (1 << 9) else 0)
        xpsr = frame[7] & ~(1 << 9)
        if thread:
            xpsr &= ~0x1FF
        engine.reg_write(ua.UC_ARM_REG_XPSR, xpsr)      # the mode first: SPSEL changes in thread mode only
        control = engine.reg_read(ua.UC_ARM_REG_CONTROL)
        control = (control | 2 if process else control) & ~4 | (4 if fp else 0)
        if control != engine.reg_read(ua.UC_ARM_REG_CONTROL):
            engine.reg_write(ua.UC_ARM_REG_CONTROL, control)
        engine.reg_write(ua.UC_ARM_REG_SP, sp)
        return frame[6] | 1

    def resume_address(self, engine):
        """Where to go on after emu_start returned: pc, with bit 0 set in Thumb state."""
        pc = engine.reg_read(ua.UC_ARM_REG_PC)
        if self.profile == "m" or engine.reg_read(ua.UC_ARM_REG_CPSR) & 0x20:
            return pc | 1
        return pc

    def flags_value(self, engine):
        """The condition flags (N, Z, C, V) as they lie in the status register."""
        return engine.reg_read(self.status_reg()) & 0xF0000000

    # ---- encoding ----

    def branch(self, src, dst, isa, link=False):
        """The bytes of an unconditional b (or bl) at `src` to `dst`, in the isa of the code at
        `src`. Raises ValueError when `dst` is out of reach (32 MiB in ARM state, 16 MiB in Thumb).
        A core whose Thumb has no Thumb-2 (ARMv4T to ARMv6, Cortex-M0) has no b.w: there the branch
        is a veneer of 14 or 16 bytes that goes through ip, which a call may change by the
        convention: push {r0}; ldr r0, [pc, #k]; mov ip, r0; pop {r0}; bx ip; the address."""
        if isa == "t32" and self.thumb2 is False and not link:
            k = 8 if src % 4 == 0 else 4
            code = struct.pack("<HHHHH", 0xB401, 0x4800 | (k >> 2), 0x4684, 0xBC01, 0x4760)
            if src % 4 == 0:
                code += struct.pack("<H", 0x46C0)           # mov r8, r8: the literal is word aligned
            return code + struct.pack("<I", dst | 1)
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

    def branch_length(self, isa, src=None, span=None):
        """The room the branch at a moved function's entry needs (src, span: where it is and the
        range its destination lies in; ARM's branches reach the whole image)."""
        return 16 if isa == "t32" and self.thumb2 is False else 4

    def entry_branch(self, src, dst, isa, room=None):
        """The branch the overlay leaves at a moved function's entry (dread.emu checks it)."""
        return self.branch(src, dst, isa)

    # ---- what the case generator learns from the code (dread.autocase) ----

    generator_version = 0       # in the generated-case cache's key when not 0: bump when these change

    SETTERS = (csarm.ARM_INS_CMP, csarm.ARM_INS_CMN, csarm.ARM_INS_TST, csarm.ARM_INS_TEQ)
    TESTS = (csarm.ARM_INS_TST, csarm.ARM_INS_TEQ, csarm.ARM_INS_AND, csarm.ARM_INS_BIC, csarm.ARM_INS_EOR,
             csarm.ARM_INS_ORR)

    def prologue_frame(self, program, fn, look=12):
        """The stack a function's prologue reserves for locals: the sum of sub sp, sp, #N (ARM and
        Thumb forms) before the first branch or call, and the scratch registers its first push stores
        below the saved ones (GCC reserves small frames with push {r0, r1, r2, r3, r4, lr}; the
        lowest registers lie lowest, where sp then points)."""
        total, pushed = 0, False
        scratch = [r for r in self.general_regs() if r not in program.target.abi.callee_saved]
        for a in fn.insns[:look]:
            insn = program.insn(a)
            if insn is None or self.flow(insn)[0] != "next":
                break
            regs = [self.reg_name(insn.reg_name(o.reg)) for o in insn.operands if o.type == csarm.ARM_OP_REG
                    and o.reg != csarm.ARM_REG_SP]
            if not pushed and self.stack_kind(insn) == "push" and insn.id != csarm.ARM_INS_VPUSH \
                    and self.stack_bytes(insn) == 4 * len(regs):    # (a core push of registers only)
                pushed = True
                total += 4 * sum(1 for r in regs if r in scratch)
                continue
            ops = insn.operands
            if insn.id in (csarm.ARM_INS_SUB, csarm.ARM_INS_SUBW) and ops and ops[0].type == csarm.ARM_OP_REG \
                    and ops[0].reg == csarm.ARM_REG_SP and ops[-1].type == csarm.ARM_OP_IMM \
                    and all(o.type == csarm.ARM_OP_REG and o.reg == csarm.ARM_REG_SP for o in ops[:-1]):
                total += ops[-1].imm
        return total

    def immediates(self, insn):
        """The immediates of an instruction, as candidate input values (none of jumps and calls)."""
        if (insn.group(cs.CS_GRP_JUMP) or insn.group(cs.CS_GRP_CALL)) and insn.id != csarm.ARM_INS_BIC:
            return []
        return [op.imm & 0xFFFFFFFF for op in insn.operands if op.type == csarm.ARM_OP_IMM]

    def switch_hints(self, insns):
        """(bounds, table): the small constants compares check (cmp rN, #k), and whether the code
        jumps through a table (tbb, tbh, or a write to pc by mov, ldr or add)."""
        limits, table = set(), False
        for ins in insns:
            ops = ins.operands
            if ins.id == csarm.ARM_INS_CMP and len(ops) == 2 and ops[1].type == csarm.ARM_OP_IMM and 0 < ops[1].imm <= 0xFF:
                limits.add(ops[1].imm)
            if ins.id in (csarm.ARM_INS_TBB, csarm.ARM_INS_TBH) or (
                    ins.id in (csarm.ARM_INS_MOV, csarm.ARM_INS_LDR, csarm.ARM_INS_ADD) and ops
                    and ops[0].type == csarm.ARM_OP_REG and ops[0].reg == csarm.ARM_REG_PC):
                table = True
        return limits, table

    def compare_chars(self, insn):
        """Values an instruction compares against that may be characters (cmp, cmn with an immediate)."""
        ops = insn.operands
        if insn.id in (csarm.ARM_INS_CMP, csarm.ARM_INS_CMN) and len(ops) == 2 and ops[1].type == csarm.ARM_OP_IMM:
            return [ops[1].imm & 0xFFFFFFFF]
        return []

    def branch_deciders(self, insns):
        """{conditional branch: (address of what decides it, first, second, imm, kind, sign)}: first
        and second are Unicorn register ids (second None: the constant imm), kind "test" or "compare",
        sign -1 when the decider compares with the negation (adds rN, rN, #4; beq asks whether rN was
        -4; cmn). Plain forms only: a register against a register or a constant, no shifted operand,
        the setter at most 16 bytes before the branch with nothing between that sets the flags or
        leaves the straight run (other conditional branches, an IT, and instructions that do not
        touch the flags are passed: GCC puts a literal's load between cmp and bne, and an eighth of
        the branches of FreeRTOS's builds had their setter so; the values are read at the setter,
        so a register the load writes afterwards does not matter); cbz and cbnz decide themselves
        (register against 0)."""
        index = {ins.address: k for k, ins in enumerate(insns)}
        out = {}

        def reg(capstone_reg, ins):
            try:
                return self.reg_id(ins.reg_name(capstone_reg))
            except ValueError:
                return None

        for ins in insns:
            if ins.id in (csarm.ARM_INS_CBZ, csarm.ARM_INS_CBNZ):
                r = reg(ins.operands[0].reg, ins)
                if r is not None:
                    out[ins.address] = (ins.address, r, None, 0, "compare", 1)
                continue
            if ins.id != csarm.ARM_INS_B or not self.is_conditional(ins):
                continue
            k = index[ins.address] - 1
            while k >= 0 and ins.address - insns[k].address <= 16:
                s = insns[k]
                if (s.id == csarm.ARM_INS_B and self.is_conditional(s)) or s.id == csarm.ARM_INS_IT:
                    k -= 1
                    continue
                sets = s.id in self.SETTERS or self.sets_flags(s)
                if not sets and self.flow(s)[0] == "next":
                    k -= 1                  # (it does not touch the flags: look further back)
                    continue
                ops = s.operands[-2:]
                if sets and len(s.operands) >= 2 and ops[0].type == csarm.ARM_OP_REG \
                        and ops[1].type in (csarm.ARM_OP_REG, csarm.ARM_OP_IMM) and not any(o.shift.type for o in ops):
                    kind = "test" if s.id in self.TESTS else "compare"
                    first = reg(ops[0].reg, s)
                    second = reg(ops[1].reg, s) if ops[1].type == csarm.ARM_OP_REG else None
                    imm = ops[1].imm & 0xFFFFFFFF if second is None else 0
                    sign = -1 if s.id in (csarm.ARM_INS_ADD, csarm.ARM_INS_CMN) else 1
                    if first is not None and (second is not None or ops[1].type == csarm.ARM_OP_IMM):
                        out[ins.address] = (s.address, first, second, imm, kind, sign)
                break
        return out

    # ---- Unicorn ----

    def uc_mode(self, isa=None):
        """M profile with a cpu: Thumb only. Unicorn 2.1.4 builds a Cortex-M33 for UC_MODE_MCLASS
        whatever ctl_set_cpu_model asked for (cpu_arm_init), so a cortex-m3 target ran on a v8-M
        core with the Security Extension, DSP and FPU; without the flag the model decides, and
        the model's own features make the core M profile. Without a cpu, Unicorn's M33 as before."""
        if self.profile == "m":
            return uc.UC_MODE_THUMB if self.cpu else uc.UC_MODE_THUMB | uc.UC_MODE_MCLASS
        return uc.UC_MODE_ARM if (isa or self.isa) == "a32" else uc.UC_MODE_THUMB

    def new_uc(self, isa=None):
        """A Unicorn engine for this core. In ARM state the engine changes to Thumb by itself when
        a start address or a branch target has bit 0 set."""
        engine = uc.Uc(uc.UC_ARCH_ARM, self.uc_mode(isa))
        if self.cpu is not None:
            engine.ctl_set_cpu_model(CPUS[self.cpu])
        return engine

    def initial_status(self, engine):
        """Put the core into the state a called function expects: on the A profile system mode
        with IRQ and FIQ masked (ARM or Thumb state follows from the start address), on the M
        profile Thumb state. Both clear the condition flags and the IT state: Unicorn keeps the IT
        state the last translation block left when it stops at the return sentinel, and the next
        call would then start inside a phantom IT block (seen on Cortex-M3: the first instructions
        of the next function ran conditionally)."""
        if self.profile == "a":
            engine.reg_write(ua.UC_ARM_REG_CPSR, 0xDF)
        else:
            engine.reg_write(ua.UC_ARM_REG_XPSR, 0x01000000)

    IT_MASK = (3 << 25) | (0x3F << 10)     # the IT state in CPSR and xPSR

    def status_reg(self):
        return ua.UC_ARM_REG_XPSR if self.profile == "m" else ua.UC_ARM_REG_CPSR

    def settle_it(self, engine):
        """Clear a stale IT state; called at the end of every hook on cores with IT blocks.

        Unicorn 2.1.4 restores the CPU state of the instruction's start when a memory hook fires
        (to give the hook a precise pc), IT state included. When that instruction is the last
        of an IT block, the translated code writes no IT state back at the end of the block (it
        assumes the state is already zero), so the restored state survives into the next block
        and makes its first instruction conditional: a loop on gizmo-m3 skipped the load at its
        head and never ended, depending on which hooks were installed. Clearing the IT bits in the
        hook is safe: the translated code checks its conditions statically, and where a block ends
        inside an IT block it writes the right state itself."""
        reg = self.status_reg()
        value = engine.reg_read(reg)
        if value & self.IT_MASK:
            engine.reg_write(reg, value & ~self.IT_MASK)

    @staticmethod
    def symbol_asm(name, address, isa):
        """Assembly that defines `name` as the absolute address of a function of `isa`, with its
        type: .thumb_set marks a Thumb function (the linker then calls it without an ARM stub)."""
        if isa == "t32":
            return "\t.global %s\n\t.thumb_set %s, 0x%x\n" % (name, name, address & ~1)
        return "\t.global %s\n\t.type %s, %%function\n\t.set %s, 0x%x\n" % (name, name, name, address & ~1)

    @staticmethod
    def isa_of_symbol(value):
        """The instruction set of a function from its symbol's value: bit 0 set for Thumb."""
        return "t32" if value & 1 else "a32"

    def start_address(self, address, isa):
        """The address to start or branch to for code of `isa` at `address` (bit 0 set for Thumb)."""
        return address | 1 if isa == "t32" else address & ~1

    def pc_reg(self):
        return UC_REGS["pc"]

    def asm(self):
        """The listing's syntax (dread asm): ArmAsm."""
        return ArmAsm(self)


# ---- dread asm: the listing in GNU as syntax ----

PCREL_RE = re.compile(r"\[pc(?:, #(-?(?:0x[0-9a-f]+|\d+)))?\]")
TARGET_RE = re.compile(r"#(?:0x[0-9a-f]+|\d+)$")
ROTIMM_RE = re.compile(r", #(\d+), #(\d+)$")
WIDTH_FREE = ("bl", "blx", "cbz", "cbnz")       # Thumb branches with one width only


class ArmAsm:
    """How dread asm writes ARM code: GNU as, unified syntax; branches and literal loads by label,
    Thumb ones with an explicit width so that the assembler keeps the original's."""

    comment = "@"
    directives = {"word": ".word", "half": ".hword", "byte": ".byte", "text": ".asciz"}

    def __init__(self, arch):
        self.arch = arch

    def prelude(self, isa):
        """The directives that put GNU as into the right instruction set."""
        arch = self.arch
        lines = ["\t.syntax unified"]
        if getattr(arch, "gas", None):
            lines.append("\t" + arch.gas)
        elif arch.profile == "m":
            lines.append("\t.cpu %s" % (arch.cpu or "cortex-m3"))
        elif arch.cpu and arch.cpu.startswith("cortex-a"):
            lines.append("\t.arch armv7-a")
        elif arch.cpu and arch.cpu.startswith("arm11"):
            lines.append("\t.arch armv6k")
        else:
            lines.append("\t.arch armv5te")
        return lines + self.switch(isa)

    def switch(self, isa):
        return ["\t.arm" if isa == "a32" else "\t.thumb"]

    def raw(self, b, isa):
        if isa == "a32":
            return ".inst", "0x%08x" % struct.unpack("<I", b)[0]
        if len(b) == 2:
            return ".inst.n", "0x%04x" % struct.unpack("<H", b)[0]
        first, second = struct.unpack("<HH", b)
        return ".inst.w", "0x%04x%04x" % (first, second)

    def function_lines(self, label, isa, code):
        return (["\t.thumb_func"] if isa == "t32" and code else []) + ["\t.type   %s, %%function" % label]

    def operands(self, listing, insn, run, st, isa):
        """(mnemonic, operands, comment) of one instruction for the listing, or None to write it raw."""
        arch, prog = self.arch, listing.prog
        mnem, ops, comment = insn.mnemonic, insn.op_str, ""
        length = insn.size
        target = arch.target(insn)
        if target is not None:
            tst = prog.stored(target)
            if listing.space(target) == listing.space(run) and listing.has(tst, 2) and listing.labelable(tst):
                listing.labels.add(tst)
                if isa == "t32" and mnem.split(".")[0] not in WIDTH_FREE and "." not in mnem:
                    mnem += ".w" if length == 4 else ".n"
                return mnem, ("label", TARGET_RE.sub("", ops), tst), ""
            return None
        lit = arch.literal(insn, isa)
        if lit is not None and PCREL_RE.search(ops):
            address, size = lit
            tst = prog.stored(address)
            if listing.space(address) == listing.space(run) and listing.has(tst, 4) and tst not in listing.code:
                if size == 4 and not tst & 3:
                    value = listing.word(tst)
                    comment = "=0x%x" % value
                    what = listing.describe_value(value)
                    if what:
                        comment += " " + what
                if isa == "t32" and "." not in mnem:
                    mnem += ".w" if length == 4 else ".n"
                listing.labels.add(tst)
                return mnem, ("sub", PCREL_RE, ops, tst), comment
        if mnem.split(".")[0] == "adr":
            # Capstone prints the offset (adr r2, #4); the assembler needs the place, and a
            # number would be taken for an absolute address and change the instruction's size.
            at = arch.pc_address(insn, isa)
            tst = prog.stored(at) if at is not None else None
            if tst is not None and listing.space(at) == listing.space(run) and listing.has(tst, 1) \
                    and listing.labelable(tst):
                listing.labels.add(tst)
                if isa == "t32" and "." not in mnem and length == 4:
                    mnem += ".w"
                return mnem, ("label", ops.split(",")[0] + ",", tst), ""
            return None
        if isa == "a32":
            ops = ROTIMM_RE.sub(r", #\1, \2", ops)
        if mnem.startswith(("mrs", "msr")):
            ops = ops.replace("apsr", "cpsr")
        if mnem.startswith(("mrc", "mcr", "cdp", "ldc", "stc")):
            ops = ops.replace("#", "")
        return mnem, ops, comment
