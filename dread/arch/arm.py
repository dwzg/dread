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
        if getattr(insn, "link_call", False):
            return "icall", None, True      # mov lr, pc in front (dread.program marks it: link_pair)
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
            return "ijump", self._name(insn.reg_name(reg)), conditional
        _, written = insn.regs_access()
        if csarm.ARM_REG_PC in written:
            if self.stack_kind(insn) == "pop":
                return "return", None, conditional
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

    def stack_kind(self, insn):
        """"push" for push, stmdb sp! and str rN, [sp, #-4]!; "pop" for pop, ldm sp! and
        ldr rN, [sp], #4; else None. (Capstone shows the single-register forms as str/ldr or as
        push/pop depending on the encoding.)"""
        i = insn.id
        if i == csarm.ARM_INS_PUSH:
            return "push"
        if i == csarm.ARM_INS_POP:
            return "pop"
        ops = insn.operands
        if i in (csarm.ARM_INS_STR, csarm.ARM_INS_LDR) and insn.writeback and len(ops) >= 2 \
                and ops[1].type == csarm.ARM_OP_MEM and ops[1].mem.base == csarm.ARM_REG_SP and not ops[1].mem.index:
            if i == csarm.ARM_INS_STR and ops[1].mem.disp == -4 and len(ops) == 2:
                return "push"
            if i == csarm.ARM_INS_LDR and len(ops) == 3 and ops[2].type == csarm.ARM_OP_IMM and ops[2].imm == 4:
                return "pop"
        if i in (csarm.ARM_INS_STMDB, csarm.ARM_INS_LDM) and insn.writeback and insn.operands \
                and insn.operands[0].type == csarm.ARM_OP_REG and insn.operands[0].reg == csarm.ARM_REG_SP:
            return "push" if i == csarm.ARM_INS_STMDB else "pop"
        return None

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
        if self.sets_flags(insn):
            writes.add("flags")
        if self.is_conditional(insn) and insn.id != csarm.ARM_INS_IT:
            reads.add("flags")
        reads.discard(None)
        writes.discard(None)
        return reads, writes

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
        """Whether an interrupt may be taken now: A profile, the I bit is clear; M profile,
        PRIMASK is clear and the core runs no exception handler (one level, no nesting)."""
        if self.profile == "m":
            return not engine.reg_read(ua.UC_ARM_REG_PRIMASK) & 1 and not engine.reg_read(ua.UC_ARM_REG_XPSR) & 0x1FF
        return not engine.reg_read(ua.UC_ARM_REG_CPSR) & 0x80

    def enter_interrupt(self, engine, number, resume, vectors, read_word, write_word):
        """Take interrupt `number` (A profile: the IRQ, number ignored; M profile: the exception
        number, 16 + the line) as the core does, returning to `resume`. Returns where to go on.

        A profile: SPSR_irq = CPSR, IRQ mode with I set in ARM state, LR_irq = the return address
        + 4, at vectors + 0x18. M profile: r0-r3, r12, lr, the return address and xPSR pushed on the
        main stack (aligned to 8 bytes, bit 9 of the stacked xPSR telling so), LR = EXC_RETURN for
        thread mode on the main stack, IPSR = number, at the handler of the vector table. The
        process stack, priorities and FPU frames are not modelled."""
        if self.profile == "m":
            sp = engine.reg_read(ua.UC_ARM_REG_SP)
            xpsr = engine.reg_read(ua.UC_ARM_REG_XPSR) & ~self.IT_MASK
            if sp & 4:
                sp -= 4
                xpsr |= 1 << 9
            regs = [engine.reg_read(getattr(ua, "UC_ARM_REG_" + r)) for r in ("R0", "R1", "R2", "R3", "R12", "LR")]
            frame = regs + [resume & ~1, xpsr | 0x01000000]
            sp -= 32
            for i, value in enumerate(frame):
                write_word(sp + 4 * i, value)
            engine.reg_write(ua.UC_ARM_REG_SP, sp)
            engine.reg_write(ua.UC_ARM_REG_LR, 0xFFFFFFF9)
            engine.reg_write(ua.UC_ARM_REG_XPSR, 0x01000000 | (number & 0x1FF))
            return read_word(vectors + 4 * number) | 1
        cpsr = engine.reg_read(ua.UC_ARM_REG_CPSR)
        engine.reg_write(ua.UC_ARM_REG_CPSR, (cpsr & ~0x3F) | 0x12 | 0x80)
        engine.reg_write(ua.UC_ARM_REG_SPSR, cpsr)
        engine.reg_write(ua.UC_ARM_REG_LR, (resume & ~1) + 4)
        return vectors + 0x18

    def exception_return(self, engine, read_word):
        """M profile: finish the return the core began by branching to EXC_RETURN (Unicorn stops
        there): pop the frame of enter_interrupt and return where to go on, or None when pc is no
        EXC_RETURN value."""
        pc = engine.reg_read(ua.UC_ARM_REG_PC)
        if self.profile != "m" or pc < self.EXC_RETURN:
            return None
        sp = engine.reg_read(ua.UC_ARM_REG_SP)
        frame = [read_word(sp + 4 * i) for i in range(8)]
        for r, value in zip(("R0", "R1", "R2", "R3", "R12", "LR"), frame):
            engine.reg_write(getattr(ua, "UC_ARM_REG_" + r), value)
        sp += 32 + (4 if frame[7] & (1 << 9) else 0)
        engine.reg_write(ua.UC_ARM_REG_SP, sp)
        engine.reg_write(ua.UC_ARM_REG_XPSR, frame[7] & ~(1 << 9))
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

    def start_address(self, address, isa):
        """The address to start or branch to for code of `isa` at `address` (bit 0 set for Thumb)."""
        return address | 1 if isa == "t32" else address & ~1

    def pc_reg(self):
        return UC_REGS["pc"]
