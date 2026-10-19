"""dread lift: translate functions of the original to C, instruction by instruction ("lifting").

Every register becomes a local variable, every instruction a C statement (with the instruction as a
comment), every branch a goto, every load and store a volatile access of the same width in the same
order; the condition flags are variables computed exactly (carry and overflow through 64-bit
arithmetic), conditional instructions (ARM's condition field, Thumb's IT blocks) are if statements.
The function's own stack is a local array: the stack pointer's depth below its value at the entry
is known statically at every instruction (refused otherwise), so [sp, #k] is a word of that array,
or, above the entry, a stack argument, which is a parameter. The analysis also follows addresses in
the frame through registers (a frame pointer: mov r7, sp ... mov sp, r7, and accesses through it)
and the return address through pushes and pops (Thumb-1's pop {r3}; bx r3 is a return). Literal loads and adr are constants read
from the image at lift time. A call passes the four argument registers and as many stack words as
the callee reads above its entry (from the caller's outgoing area, or its own stack arguments for a
tail call); the result is r0, or r0 and r1 for a callee whose callers read r1 (dread.regflow). A
call through a register passes the four registers and the lowest frame words, which is where the
original's callee would find stack arguments. A switch is a C switch over the index its bound check
compares, with the target dread.switches finds for every value the check lets through.

Lifted C is a stepping stone, not a reconstruction: no names, no structure, nobody read it. It is
useful because it builds and comes with generated cases that must cover every instruction of the
original and agree on all of them before it is accepted (see --check). The translation of each
instruction is checked on its own by dread liftfuzz: cases that cover a function need not tell a
wrong translation apart (an xor loop run once from zero agrees with an or).

A function is refused, with the reason, when it uses something not modelled exactly: an unknown
instruction (coprocessor and status register access included), a stack pointer that is not static
or moves under a condition, a register or the flags read before anything writes them (what the
caller leaves: C receives r0-r3 only), callers that read a register it sets other than r0 and r1 or
the flags it leaves (C cannot return them), a read of a scratch register or the flags behind a call
that the callee leaves (dread.regflow), a call of a function that reads registers its caller leaves
(beyond r0-r3; lr included: such a helper finds data behind the call), the address of a stack
argument, an indirect jump that is neither a resolved switch nor a tail call, an exception return,
code that falls through into what follows, or an entry in the asm_only table. A call of a function
that never returns (dread.noreturn) does not fall through: an endless loop follows it in the C.

With --check the lifted C is put to the test: every function that lifts (and that src does not
define by hand) is built over the original together with the others, its generated cases run
(callees real, dread.autocase), and a function whose cases disagree or leave an instruction of the
original unrun is dropped; the build and the run are repeated until nothing is dropped. A function
that failed while a callee of it was lifted and failed too gets one more round without that callee.
A function that never returns gets no case that ends; when the target has scenarios, the system
run decides for it instead (system_check: every scenario agrees on the original and the rebuilt
image, and the original's runs execute every instruction a path reaches). What stays is written to
the image's lifted table (config/lifted.txt), which dread recon builds from then on (it lifts those
functions again at every build: the C is not kept).

    dread lift [--target DIR] [--image NAME] [--out DIR] [NAME...]
    dread lift --check [--target DIR] [--image NAME] [NAME...]
"""
import argparse
import os
import re
import sys

from capstone import arm as A

from dread import target as tgt
from dread.program import Program, read_ranges

COND = {A.ARM_CC_EQ: "z", A.ARM_CC_NE: "!z", A.ARM_CC_HS: "c", A.ARM_CC_LO: "!c", A.ARM_CC_MI: "n",
        A.ARM_CC_PL: "!n", A.ARM_CC_VS: "v", A.ARM_CC_VC: "!v", A.ARM_CC_HI: "(c && !z)",
        A.ARM_CC_LS: "(!c || z)", A.ARM_CC_GE: "(n == v)", A.ARM_CC_LT: "(n != v)",
        A.ARM_CC_GT: "(!z && n == v)", A.ARM_CC_LE: "(z || n != v)"}
REGS = ["r%d" % i for i in range(13)] + ["sp", "lr", "pc"]
ARGS = ("r0", "r1", "r2", "r3")
AFTER_CALL = {"r0", "r1", "r2", "r3", "r12", "lr", "flags"}    # what a call leaves written
# capstone instruction id -> the op_ method that translates it (mnemonics carry condition suffixes)
OPS = {getattr(A, "ARM_INS_" + name.upper()): name for name in (
    "and orr eor bic orn mov mvn add addw adc sub subw sbc rsb rsc cmp cmn tst teq lsl lsr asr ror rrx "
    "mul mla mls umull smull umlal smlal udiv sdiv clz uxtb uxth sxtb sxth uxtab uxtah sxtab sxtah rev rev16 "
    "revsh movw movt ubfx sbfx bfc bfi ldr ldrb ldrh ldrsb ldrsh str strb strh ldrd strd ldm ldmib ldmda "
    "ldmdb stm stmib stmda stmdb").split() if hasattr(A, "ARM_INS_" + name.upper())}
OPS[A.ARM_INS_MOVS] = "mov"     # Thumb's 16-bit movs rd, rm (an lsls #0) has an id of its own
SIGNED = {A.ARM_INS_LDRSB: "s8", A.ARM_INS_LDRSH: "s16"}
# Instructions that run as themselves, in inline assembly: they act on the core, not on registers
# the C models (interrupt masks, barriers, waits, the exclusive monitor, coprocessors, status registers)
SYSTEM = {getattr(A, "ARM_INS_" + n) for n in (
    "CPS DMB DSB ISB CLREX BKPT HINT MRS MSR LDREX LDREXB LDREXH LDREXD STREX STREXB STREXH STREXD MCR MRC MCR2 "
    "MRC2").split()
    if hasattr(A, "ARM_INS_" + n)}
STATUS = ("apsr", "cpsr", "xpsr", "iapsr", "eapsr")     # status registers whose top bits are the flags
STACK_POINTERS = ("msp", "psp", "msp_ns", "psp_ns", "sp")
MAX_STACK_ARGS = 16      # stack words a function may take; more is refused
INDIRECT_STACK_WORDS = 4  # frame words a call through a register passes on as stack arguments
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Refused(Exception):
    """The function uses something the translation does not model exactly."""


class Lifted:
    """The result for one function; unreached: its instructions no path from the entry reaches
    (padding behind an ARMv4T veneer's bx pc, say), which no case can run."""

    def __init__(self, name, text, callees, frame, stack_params, unreached=()):
        self.name, self.text, self.callees, self.frame, self.stack_params = name, text, callees, frame, stack_params
        self.unreached = list(unreached)


def direct_callees(program, fn):
    """The functions fn calls or tail-calls directly, by name."""
    out = set()
    for insn in program.instructions(fn):
        if insn is None:
            continue
        kind, target, _ = program.arch.flow(insn)
        if kind in ("call", "jump") and target is not None:
            callee = program.by_entry.get(target & ~1)
            if callee is not None and callee is not fn:
                out.add(callee.name)
    return out


def make_lifter(program, flow=None, endless=None):
    """The lifter for the program's architecture: Lifter for ARM, dread.lift_riscv.RiscVLifter for
    RISC-V."""
    if program.arch.name == "riscv":
        from dread.lift_riscv import RiscVLifter
        return RiscVLifter(program, flow, endless)
    return Lifter(program, flow, endless)


class Lifter:
    """The ARM translator (ARM state and Thumb), and the analyses every translator shares: control
    flow, the stack pointer's depth, the registers a function reads before writing them, the
    acceptance. Another instruction set subclasses it (dread.lift_riscv)."""

    REGS, ARGS, AFTER_CALL = REGS, ARGS, AFTER_CALL     # the architecture's names (class attributes)

    def __init__(self, program, flow=None, endless=None):
        """flow: the image's RegFlow (dread.regflow); endless: the names of the functions that never
        return, when the caller has them already (dread.noreturn.find, which otherwise runs here)."""
        self.prog, self.arch, self.flow = program, program.arch, flow
        self._stack_args, self._entry, self._resolved, self._states, self._handlers = {}, {}, {}, {}, {}
        self._noreturn = set(endless) if endless is not None else None
        path = program.image.table("asm_only")
        self.asm_only = read_ranges(path) if path and os.path.exists(path) else []

    # ---- control flow and the stack pointer ----

    def resolve(self, fn, address):
        key = (fn.name, address)
        if key not in self._resolved:
            from dread import switches
            self._resolved[key] = switches.resolve(self.prog, fn, address)
        return self._resolved[key]

    def never_returns(self, name):
        """Whether the function never returns (dread.noreturn)."""
        if self._noreturn is None:
            from dread import noreturn
            self._noreturn = noreturn.find(self.prog)
        return name in self._noreturn

    def dead_end(self, insn):
        """The callee of an unconditional call that never returns, or None."""
        kind, target, _ = self.arch.flow(insn)
        if kind != "call" or self.arch.predicate(insn) is not None or not isinstance(target, int):
            return None
        callee = self.prog.by_entry.get(target & ~1)
        return callee if callee is not None and self.never_returns(callee.name) else None

    def successors(self, fn, index, address, insn, strict=True, values=None):
        """The instructions of fn that may run after the one at `address`. Falling through out of
        the function or into data raises Refused when strict, else has no successor. A call of a
        function that never returns does not fall through; a jump through a register that holds the
        return address (values: see states) is a return."""
        kind, target, falls = self.arch.flow(insn)
        if values is not None and self.ret_jump(insn, values):
            kind = "return"
        out = []
        if (kind in ("next", "call", "icall") or falls) and not self.dead_end(insn):
            i = index[address]
            if i + 1 < len(fn.insns) and fn.insns[i + 1] == address + insn.size:
                out.append(fn.insns[i + 1])
            elif strict:
                raise Refused("0x%x falls through into what follows (data or other code; a call that does not "
                              "return?)" % address)
        if kind == "jump" and target is not None and (target & ~1) in index:
            out.append(target & ~1)
        if kind == "ijump":
            got = self.resolve(fn, address)
            if got:
                out += [t for t in got[0] if t in index]
        return out

    RET = ("ret",)

    def states(self, fn, strict=True):
        """{address: (depth, values)} before every instruction a path from the entry reaches.
        depth: how many bytes the stack pointer is below its value at the entry. values: what is
        known of registers (by name) and frame slots (by their depth, an int): ("sp", k) for the
        address k bytes below the entry's stack pointer (a frame pointer, a pointer to a local) and
        RET for the return address (lr at the entry, saved by a push, popped into another register:
        Thumb-1's pop {r3}; bx r3 is a return). Where paths meet, the values they agree on are kept.
        Raises Refused when the depth is not the same on every path or changes in a way not modelled
        (and, when strict, when the code falls through out of the function)."""
        key = (fn.name, strict)
        if key in self._states:
            return self._states[key]
        prog, arch = self.prog, self.arch
        index = {a: i for i, a in enumerate(fn.insns)}
        states = {fn.insns[0]: (0, {self.arch.link_reg: self.RET})}
        work = [fn.insns[0]]
        while work:
            a = work.pop()
            insn = prog.insn(a)
            if insn is None:
                raise Refused("0x%x does not decode" % a)
            depth, values = states[a]
            returning = arch.flow(insn)[0] == "return" or self.ret_jump(insn, values)
            new, after = self.step(insn, depth, values)
            if arch.predicate(insn) is not None:
                if new != depth and not returning:
                    raise Refused("0x%x moves the stack pointer under a condition" % a)
                after = {k: v for k, v in after.items() if values.get(k) == v}    # it may not run
            if returning:
                new, after = depth, values      # what follows a conditional return runs unpopped
            for b in self.successors(fn, index, a, insn, strict, values):
                if b in states:
                    old_depth, old = states[b]
                    if old_depth != new:
                        raise Refused("the stack pointer differs at 0x%x (%d and %d bytes)" % (b, old_depth, new))
                    met = {k: v for k, v in old.items() if after.get(k) == v}
                    if met != old:
                        states[b] = (old_depth, met)
                        work.append(b)
                else:
                    states[b] = (new, after)
                    work.append(b)
        self._states[key] = states
        return states

    def depths(self, fn, strict=True):
        """{address: depth} of states()."""
        return {a: st[0] for a, st in self.states(fn, strict).items()}

    def ret_jump(self, insn, values):
        """bx rN or mov pc, rN with the return address in rN."""
        ops = insn.operands
        if insn.id == A.ARM_INS_BX and ops and ops[0].type == A.ARM_OP_REG:
            return values.get(self.arch._name(insn.reg_name(ops[0].reg))) == self.RET
        if insn.id == A.ARM_INS_MOV and len(ops) == 2 and ops[0].reg == A.ARM_REG_PC and ops[1].type == A.ARM_OP_REG \
                and ops[1].shift.type in (0, A.ARM_SFT_INVALID):
            return values.get(self.arch._name(insn.reg_name(ops[1].reg))) == self.RET
        return False

    @staticmethod
    def _put(values, key, value):
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value

    def step(self, insn, depth, values):
        """(depth, values) after an instruction (see states)."""
        arch, ops = self.arch, insn.operands
        rname = lambda r: arch._name(insn.reg_name(r))
        after = dict(values)
        stack = arch.stack_kind(insn)
        if stack in ("push", "pop"):
            regs = [rname(o.reg) for o in ops if o.type == A.ARM_OP_REG and o.reg != A.ARM_REG_SP]
            if stack == "push":
                new = depth + 4 * len(regs)
                for k, r in enumerate(regs):
                    self._put(after, new - 4 * k, values.get(r))
                return new, after
            for k, r in enumerate(regs):
                self._put(after, r, values.get(depth - 4 * k))
            return depth - 4 * len(regs), after
        reads, writes = arch.reg_effects(insn)
        new = self.new_depth(insn, depth, values) if "sp" in writes else depth
        known = self.made_value(insn, depth, values)
        slot = self.stored_slot(insn, depth, values)
        for r in writes:
            if r not in ("sp", "pc", "flags"):
                after.pop(r, None)
        if arch.flow(insn)[0] in ("call", "icall"):
            for r in self.AFTER_CALL:
                after.pop(r, None)
        if known is not None:
            self._put(after, known[0], known[1])
        if slot is not None:
            self._put(after, slot[0], values.get(slot[1]))
        loaded = self.ldm_slots(insn, depth, values) if not insn.writeback and self.arch.predicate(insn) is None \
            else None
        for r, key in loaded or ():
            if r not in ("sp", "pc"):
                self._put(after, r, values.get(key))
        return new, after

    def frame_value(self, insn, reg, depth, values):
        """("sp", k) when the register holds an address in the frame (sp itself included)."""
        name = self.arch._name(insn.reg_name(reg))
        if name == "sp":
            return ("sp", depth)
        v = values.get(name)
        return v if v is not None and v[0] == "sp" else None

    def made_value(self, insn, depth, values):
        """(register, value) for the instructions that make a frame address or copy a known value:
        mov rX, sp; mov rX, rY; add or sub rX, sp or a frame address, #imm; ldr rX from a frame slot."""
        if self.arch.predicate(insn) is not None:
            return None
        i, ops = insn.id, insn.operands
        rname = lambda r: self.arch._name(insn.reg_name(r))
        if i == A.ARM_INS_LDR and len(ops) == 2 and ops[1].type == A.ARM_OP_MEM and not ops[1].mem.index \
                and not insn.writeback:
            v = self.frame_value(insn, ops[1].mem.base, depth, values)
            if v is not None:
                return rname(ops[0].reg), values.get(v[1] - ops[1].mem.disp)
        if i in (A.ARM_INS_MOV, A.ARM_INS_MOVS) and len(ops) == 2 and ops[1].type == A.ARM_OP_REG \
                and ops[1].shift.type in (0, A.ARM_SFT_INVALID) and ops[0].type == A.ARM_OP_REG:
            v = self.frame_value(insn, ops[1].reg, depth, values) or values.get(rname(ops[1].reg))
            return (rname(ops[0].reg), v) if v is not None else None
        if i in (A.ARM_INS_ADD, A.ARM_INS_ADDW, A.ARM_INS_SUB, A.ARM_INS_SUBW) and len(ops) in (2, 3) \
                and ops[-2].type == A.ARM_OP_REG and ops[-1].type == A.ARM_OP_IMM and ops[0].type == A.ARM_OP_REG:
            v = self.frame_value(insn, ops[-2].reg, depth, values)  # also Thumb's adds r7, #12
            if v is not None:
                k = v[1] - ops[-1].imm if i in (A.ARM_INS_ADD, A.ARM_INS_ADDW) else v[1] + ops[-1].imm
                return rname(ops[0].reg), ("sp", k)
        return None

    def stored_slot(self, insn, depth, values):
        """(slot, register) for a word store into the frame at a known place (str rX, [sp or a
        frame address, #k] without writeback)."""
        if insn.id != A.ARM_INS_STR or self.arch.predicate(insn) is not None or insn.writeback:
            return None
        ops = insn.operands
        if len(ops) != 2 or ops[1].type != A.ARM_OP_MEM or ops[1].mem.index:
            return None
        v = self.frame_value(insn, ops[1].mem.base, depth, values)
        if v is None:
            return None
        return v[1] - ops[1].mem.disp, self.arch._name(insn.reg_name(ops[0].reg))

    LDMS = {A.ARM_INS_LDM: (False, True), A.ARM_INS_LDMIB: (True, True), A.ARM_INS_LDMDA: (False, False),
            A.ARM_INS_LDMDB: (True, False)}

    def ldm_slots(self, insn, depth, values):
        """[(register, slot)] of an ldm whose base holds a frame address (sp included), or None."""
        mode = self.LDMS.get(insn.id)
        ops = insn.operands
        if mode is None or not ops or ops[0].type != A.ARM_OP_REG:
            return None
        v = self.frame_value(insn, ops[0].reg, depth, values)
        if v is None:
            return None
        regs = [self.arch._name(insn.reg_name(o.reg)) for o in ops[1:]]
        n = len(regs)
        start = {(False, True): 0, (True, True): 4, (False, False): -4 * n + 4, (True, False): -4 * n}[mode]
        return [(r, v[1] - start - 4 * k) for k, r in enumerate(regs)]

    def new_depth(self, insn, depth, values):
        """The depth after an instruction that writes sp: add or sub sp, sp, #imm, and mov sp, rX or
        add or sub sp, rX, #imm with a frame address in rX (a frame pointer)."""
        ops, i = insn.operands, insn.id
        if ops and ops[0].type == A.ARM_OP_REG and ops[0].reg == A.ARM_REG_SP:
            if i in (A.ARM_INS_SUB, A.ARM_INS_ADD, A.ARM_INS_SUBW, A.ARM_INS_ADDW) and ops[-1].type == A.ARM_OP_IMM:
                src = ops[1] if len(ops) == 3 else ops[0]
                v = self.frame_value(insn, src.reg, depth, values) if src.type == A.ARM_OP_REG else None
                if v is not None:
                    return v[1] + ops[-1].imm if i in (A.ARM_INS_SUB, A.ARM_INS_SUBW) else v[1] - ops[-1].imm
            if i == A.ARM_INS_MOV and len(ops) == 2 and ops[1].type == A.ARM_OP_REG \
                    and ops[1].shift.type in (0, A.ARM_SFT_INVALID):
                v = self.frame_value(insn, ops[1].reg, depth, values)
                if v is not None:
                    return v[1]
        slots = self.ldm_slots(insn, depth, values) if not insn.writeback else None
        if slots is not None:               # an APCS epilogue: ldm sp, {fp, sp, lr} takes sp from its slot
            v = values.get(dict(slots).get("sp"))
            if v is not None and v[0] == "sp":
                return v[1]
        raise Refused("0x%x changes the stack pointer in a way not modelled: %s %s" % (
            insn.address, insn.mnemonic, insn.op_str))

    def stack_args(self, fn):
        """How many words above its entry's stack pointer a function reads or passes on to a tail
        call: its stack arguments. None when that is not known (its stack pointer is not static)."""
        if fn.name in self._stack_args:
            return self._stack_args[fn.name]
        self._stack_args[fn.name] = 0           # a recursive call counts nothing while this is worked out
        try:
            depth = self.depths(fn, strict=False)
        except Refused:
            self._stack_args[fn.name] = None
            return None
        states = self.states(fn, strict=False)
        top = 0
        for a in fn.insns:
            if a not in depth:
                continue
            insn, d = self.prog.insn(a), depth[a]
            for o in insn.operands:
                if o.type == A.ARM_OP_MEM and not o.mem.index:
                    v = self.frame_value(insn, o.mem.base, d, states[a][1])
                    if v is None:
                        continue
                    off = o.mem.disp - v[1]
                    width = 8 if insn.id in (A.ARM_INS_LDRD, A.ARM_INS_STRD) else 4
                    if off >= 0:
                        top = max(top, (off + width + 3) // 4)
            kind, target, _ = self.arch.flow(insn)
            callee = self.prog.by_entry.get(target & ~1) if kind in ("call", "jump") and target is not None else None
            if callee is not None and callee is not fn:
                n = self.stack_args(callee) or 0
                if 4 * n > d:
                    top = max(top, (4 * n - d + 3) // 4)
        self._stack_args[fn.name] = top
        return top

    def entry_states(self, fn):
        """({address: registers that may still hold what the caller left, before the instruction},
        {address: such registers the instruction reads}). Arguments (r0-r3), sp and pc count as
        written at the entry; pushes (they only save) and returns (lr is only the way back) read
        nothing; a call leaves the scratch registers, lr and the flags written. A conditional write
        counts once the register is written under the opposite condition too (addeq r7, ...;
        addne r7, ...), or on the edge of a conditional branch whose condition it was written
        under, as long as the flags do not change in between."""
        if fn.name in self._entry:
            return self._entry[fn.name]
        prog, arch = self.prog, self.arch
        index = {a: i for i, a in enumerate(fn.insns)}
        try:
            known = self.states(fn, strict=False)   # for returns through a register
        except Refused:
            known = {}
        try:
            args = () if self.handler_kind(fn) else self.ARGS   # an exception handler gets no arguments
        except Refused:
            args = self.ARGS
        link = self.arch.link_reg                                # the C knows the return address
        fixed = tuple(self.prog.image.fixed)                    # one value everywhere ([image] fixed)
        start = frozenset(r for r in self.REGS + ["flags"] if r not in tuple(args) + ("sp", "pc", link) + fixed)
        undef_at, partial_at, found = {fn.insns[0]: start}, {fn.insns[0]: frozenset()}, {}
        work = [fn.insns[0]]
        while work:
            a = work.pop()
            insn = prog.insn(a)
            if insn is None:
                continue
            undef, partial = undef_at[a], partial_at[a]
            kind, target, _ = arch.flow(insn)
            reads, writes = arch.reg_effects(insn)
            if arch.stack_kind(insn) == "push" or kind == "return":
                reads = set()
            saved = self.saving_store(insn, known.get(a, (0, {}))[1])
            if saved is not None and saved in undef:
                reads = set(reads) - {saved}    # stored into the frame to be restored: a save, no use
            if kind in ("call", "icall"):
                writes = set(writes) | self.AFTER_CALL
            copy = self.plain_move(insn)
            if copy is not None and copy[1] in undef:
                reads = set(reads) - {copy[1]}   # a copy of what the caller left (mov r8, r8 is a nop;
            bad = (reads & undef) - {"sp", "pc"}  # mov r3, r8 before push {r3} saves r8)
            if bad:
                found[a] = bad
            pred = arch.predicate(insn)
            if pred is None:
                after_u = undef - writes
                after_p = frozenset(x for x in partial if x[0] not in writes)
            else:
                u, part = set(undef), set(partial)
                for r in writes & undef:
                    if (r, arch.complement(pred)) in part:
                        u.discard(r)
                        part = {x for x in part if x[0] != r}
                    else:
                        part.add((r, pred))
                after_u, after_p = frozenset(u), frozenset(part)
            if "flags" in writes:
                after_p = frozenset()       # the conditions written under no longer hold
            if copy is not None and copy[1] in undef:
                after_u = after_u | {copy[0]}
            for b in self.successors(fn, index, a, insn, strict=False, values=known.get(a, (0, None))[1]):
                bu, bp = after_u, after_p
                if kind == "jump" and pred is not None and bp:
                    cond = pred if isinstance(target, int) and b == target & ~1 else arch.complement(pred)
                    done = {r for r, c in bp if c == cond}
                    bu, bp = bu - done, frozenset(x for x in bp if x[0] not in done)
                if b not in undef_at:
                    undef_at[b], partial_at[b] = bu, bp
                    work.append(b)
                else:
                    nu, np = undef_at[b] | bu, partial_at[b] & bp
                    if nu != undef_at[b] or np != partial_at[b]:
                        undef_at[b], partial_at[b] = nu, np
                        work.append(b)
        self._entry[fn.name] = (undef_at, found)
        return undef_at, found

    def saving_store(self, insn, values):
        """The register a store saves into the frame (to restore it later), or None. ARM saves with
        push, which stack_kind tells; a single-register store into the frame is a save on RISC-V."""
        return None

    def plain_move(self, insn):
        """(destination, source) of mov rd, rs: unconditional, no shift, no flags; else None."""
        ops = insn.operands
        if insn.id != A.ARM_INS_MOV or len(ops) != 2 or self.arch.predicate(insn) is not None \
                or self.arch.sets_flags(insn) or ops[1].type != A.ARM_OP_REG \
                or ops[1].shift.type not in (0, A.ARM_SFT_INVALID):
            return None
        rd, rs = (self.arch._name(insn.reg_name(o.reg)) for o in ops)
        return (rd, rs) if rd in REGS and rs in REGS and "pc" not in (rd, rs) else None

    def fixed_lines(self, name=lambda r: r):
        """The lifted C's assignments of the registers that hold one value ([image] fixed)."""
        return ["    %s = 0x%x;    /* [image] fixed */" % (name(r), v) for r, v in sorted(self.prog.image.fixed.items())]

    def leaves(self, fn):
        """The registers beyond r0-r3 a function reads before anything writes them."""
        found = self.entry_states(fn)[1]
        return sorted(set().union(*found.values()) - set(self.ARGS)) if found else []

    # ---- the translation ----

    def compiled_isa(self):
        """The instruction set the target's C compiles to: Thumb on M profile or with -mthumb, else
        ARM (GCC's default on the A profile)."""
        flags = self.prog.target.compiler.cflags
        if getattr(self.arch, "profile", None) == "m" or "-mthumb" in flags:
            return "t32"
        return "a32" if "-marm" in flags or self.arch.name == "arm" else None

    def lift(self, fn):
        prog, arch = self.prog, self.arch
        isa = prog.isa_at(fn.entry)
        if isa != self.compiled_isa():
            raise Refused("its code is %s, the C compiles to %s ([compiler] cflags)" % (isa, self.compiled_isa()))
        for lo, hi, reason in self.asm_only:
            if fn.entry == lo or (hi is not None and lo <= fn.entry < hi):
                raise Refused("listed in asm_only: %s" % reason)
        if not IDENT.match(fn.name):
            raise Refused("its name is not a C identifier")
        insns = prog.instructions(fn)
        if not insns or any(i is None for i in insns):
            raise Refused("an instruction does not decode")
        states = self.states(fn)
        depth = {a: st[0] for a, st in states.items()}
        undef, found = self.entry_states(fn)
        flags_in = any("flags" in regs for regs in found.values())
        found = {a: regs - {"flags"} for a, regs in found.items() if regs - {"flags"}}
        if found:
            a = min(found)
            raise Refused("0x%x reads %s before anything writes it (what its caller leaves)" % (
                a, ", ".join(sorted(found[a]))))
        fixed = set(self.prog.image.fixed)
        for insn in insns:
            changed = fixed & set(arch.reg_effects(insn)[1])
            if changed:
                raise Refused("0x%x writes %s, which holds one value in the whole program ([image] fixed)"
                              % (insn.address, ", ".join(sorted(changed))))
        self._check_kept(fn)
        handler = self.handler_kind(fn)
        self._check_returns(fn, states, handler)
        stack_params = self.stack_args(fn)
        if stack_params > MAX_STACK_ARGS:
            raise Refused("it takes %d stack words" % stack_params)
        frame = (max(depth.values()) + 7) & ~7
        targets = set()
        for insn in insns:
            kind, target, _ = arch.flow(insn)
            if kind == "jump" and target is not None and (target & ~1) in depth:
                targets.add(target & ~1)
        self.ctx = {"fn": fn, "depth": depth, "states": states, "undef": undef, "frame": frame, "callees": set(),
                    "flags_in": flags_in, "handler": handler,
                    "stack_params": stack_params, "switch": {}, "switch_targets": set()}
        self._switches(fn)
        body = []
        for insn in insns:
            if insn.address not in depth:
                continue                    # never reached (padding, a dead stretch)
            if insn.address in targets or insn.address in self.ctx["switch_targets"]:
                body.append("L_%x: ;" % insn.address)
            for check, reg, _ in self.ctx["switch"].values():
                if check == insn.address:
                    body.append("    index = %s;" % reg)
            text = self.insn(insn)
            note = "/* %x: %s %s */" % (insn.address, insn.mnemonic, insn.op_str)
            body.append("    %s  %s" % (text, note) if text else "    " + note)
        stubs = {self.arch.target(i) & ~1 for i in insns if getattr(i, "call_via", None)}   # bl into a bx rN
        return Lifted(fn.name, self.render(fn, body), sorted(self.ctx["callees"]), frame, stack_params,
                      [a for a in fn.insns if a not in depth and a not in stubs])

    def handler_kind(self, fn):
        """"IRQ" or "SWI" when the function is an A-profile exception handler (its returns are
        exception returns: subs pc, lr, #4 for IRQ, ldm .. {.., pc}^ or movs pc, lr for IRQ after its
        own sub lr, lr, #4 at the entry and for SWI without one), else None: the C becomes a function
        with gcc's interrupt attribute, which writes the entry and the return. Refused: a data
        abort's return (subs pc, lr, #8), mixed forms."""
        if getattr(self.arch, "profile", None) != "a" or not fn.insns:
            return None
        if fn.name in self._handlers:
            return self._handlers[fn.name]
        returns = [i for i in self.prog.instructions(fn)
                   if i is not None and self.arch.flow(i)[0] == "return" and self.exception_return(i)]
        kind = None
        if returns:
            first = self.prog.insn(fn.insns[0])
            ops = first.operands if first is not None else ()
            adjusted = first is not None and first.id == A.ARM_INS_SUB and len(ops) == 3 \
                and all(o.type == A.ARM_OP_REG and o.reg == A.ARM_REG_LR for o in ops[:2]) \
                and ops[2].type == A.ARM_OP_IMM and ops[2].imm == 4
            kinds = set()
            for r in returns:
                if r.id == A.ARM_INS_SUB:
                    if r.operands[-1].type != A.ARM_OP_IMM or r.operands[-1].imm != 4 or adjusted:
                        raise Refused("0x%x: an exception return the C cannot make: %s %s" % (
                            r.address, r.mnemonic, r.op_str))
                    kinds.add("IRQ")
                else:
                    kinds.add("IRQ" if adjusted else "SWI")
            if len(kinds) != 1:
                raise Refused("its exception returns differ in kind")
            kind = kinds.pop()
        self._handlers[fn.name] = kind
        return kind

    def system_only(self, fn):
        """Whether only the system run can test the function: it never returns, or it is an exception
        handler."""
        return self.never_returns(fn.name) or self.handler_kind(fn) is not None

    def exception_return(self, insn):
        """subs pc, lr, #k, movs pc, lr and ldm ... {.., pc}^: the A profile's return from an exception."""
        return insn.id == A.ARM_INS_SUB or (insn.id == A.ARM_INS_MOV and self.arch.sets_flags(insn)) \
            or bool(getattr(insn, "usermode", False))

    def _check_returns(self, fn, states, handler=None):
        """Refuse a return to anything but the return address: a helper that returns behind its call
        site (GCC's __gnu_thumb1_case_*: add lr, r1; bx lr) is a jump the C cannot make. An exception
        handler's returns are gcc's to write (handler_kind)."""
        for a, (depth, values) in states.items():
            insn = self.prog.insn(a)
            if self.arch.flow(insn)[0] != "return":
                continue
            if self.exception_return(insn):
                if handler:
                    continue
                raise Refused("0x%x returns from an exception: %s %s" % (a, insn.mnemonic, insn.op_str))
            slots = self.ldm_slots(insn, depth, values)
            if self.arch.stack_kind(insn) == "pop":
                regs = [self.arch._name(insn.reg_name(o.reg)) for o in insn.operands
                        if o.type == A.ARM_OP_REG and o.reg != A.ARM_REG_SP]
                where = values.get(depth - 4 * regs.index("pc")) if "pc" in regs else self.RET
            elif slots is not None and "pc" in dict(slots):
                where = values.get(dict(slots)["pc"])
            else:
                where = values.get("lr")
            if where != self.RET:
                raise Refused("0x%x returns to an address it computed, not to its caller" % a)

    def render(self, fn, body):
        ctx = self.ctx
        params = ["u32 a%d" % i for i in range(4)] + ["u32 s%d" % i for i in range(ctx["stack_params"])]
        lines = ["/* %s at 0x%x, lifted by dread lift: one statement per instruction. */" % (fn.name, fn.entry),
                 '#include "dread.h"']
        for name, nstack, wide in sorted(ctx["callees"]):
            lines.append("extern %s %s(%s);" % ("u64" if wide else "u32", name, ", ".join(["u32"] * (4 + nstack))))
        if ctx.get("handler"):              # gcc writes the entry and the exception return
            lines.append('void __attribute__((interrupt("%s"))) %s(void)' % (ctx["handler"], fn.name))
            lines.append("{")
            lines.append("    u32 r0 = 0, r1 = 0, r2 = 0, r3 = 0, r4 = 0, r5 = 0, r6 = 0, r7 = 0, r8 = 0, r9 = 0,"
                         " r10 = 0, r11 = 0, r12 = 0, lr = 0;")
        else:
            lines.append("%s %s(%s)" % ("u64" if self._wide(fn.name) else "u32", fn.name, ", ".join(params)))
            lines.append("{")
            lines.append("    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r5 = 0, r6 = 0, r7 = 0, r8 = 0, r9 = 0,"
                         " r10 = 0, r11 = 0, r12 = 0, lr = (u32)__builtin_return_address(0);")
        lines.append("    u32 n = 0, z = 0, c = 0, v = 0;")
        lines += self.fixed_lines()
        if ctx.get("flags_in"):             # read before written (mrs r0, cpsr first): the caller's flags
            lines.append('    { u32 t; __asm__ volatile ("mrs %0, apsr" : "=r"(t)); n = t >> 31; z = t >> 30 & 1; '
                         'c = t >> 29 & 1; v = t >> 28 & 1; }')
        lines.append("    u32 frame[%d] __attribute__((aligned(8)));" % max(2, ctx["frame"] // 4))
        lines.append("    u32 sp0 = (u32)&frame[%d];" % (ctx["frame"] // 4))
        if ctx["switch"]:
            lines.append("    u32 index = 0;")
        lines.append("    (void)n; (void)z; (void)c; (void)v; (void)r12; (void)lr; (void)sp0;")
        lines += body
        lines.append("}")
        return "\n".join(lines) + "\n"

    def _wide(self, name):
        flow = self.flow
        return bool(flow and "r1" in flow.kept_regs(name) and "r1" not in flow.preserved_regs(name))

    def _check_kept(self, fn):
        """Refuse when callers read what C cannot hand back: the flags, or a register other than r0
        and r1 that the function sets."""
        flow = self.flow
        if flow is None:
            return
        result = {"r0", "r1"} if self._wide(fn.name) else {"r0"}
        kept = set(flow.kept_regs(fn.name)) - result
        if "flags" in kept:
            raise Refused("its callers read the flags it leaves (%s)" % flow.why(fn.name, "flags"))
        written = sorted(kept - set(flow.preserved_regs(fn.name)))
        if written:
            raise Refused("its callers read %s, which it sets (%s); C returns r0 and r1 only" % (
                written[0], flow.why(fn.name, written[0])))

    # operands

    def name(self, r):
        return self.arch._name(self.insn_now.reg_name(r))

    def values(self):
        """What states() knows before the instruction being translated."""
        return self.ctx["states"][self.insn_now.address][1]

    def reg(self, r, sp=False):
        """The C expression of a register read; the stack pointer only where `sp` allows it."""
        name = self.name(r)
        if name == "pc":                    # a constant: where the original's code is
            insn = self.insn_now
            return "0x%xu" % self.arch.pc_value(insn.address, self.prog.isa_at(insn.address))
        if name == "sp":
            if not sp:
                raise Refused("0x%x computes with the stack pointer: %s %s" % (
                    self.insn_now.address, self.insn_now.mnemonic, self.insn_now.op_str))
            return "(sp0 - %d)" % self.ctx["depth"][self.insn_now.address]
        if name not in REGS:
            raise Refused("0x%x uses %s" % (self.insn_now.address, name))
        return name

    def dest(self, o):
        name = self.name(o.reg)
        if name in ("sp", "pc") or name not in REGS:
            raise Refused("0x%x writes %s" % (self.insn_now.address, name))
        return name

    REG_SHIFT = {"lsl": "({s} == 0 ? {x} : {s} < 32 ? {x} << {s} : 0u)",
                 "lsr": "({s} == 0 ? {x} : {s} < 32 ? {x} >> {s} : 0u)",
                 "asr": "({s} == 0 ? {x} : (u32)((s32){x} >> ({s} < 32 ? {s} : 31)))",
                 "ror": "(({s} & 31) == 0 ? {x} : ({x} >> ({s} & 31)) | ({x} << (32 - ({s} & 31))))"}
    REG_CARRY = {"lsl": "({s} == 0 ? c : {s} < 32 ? ({x} >> (32 - {s})) & 1 : {s} == 32 ? {x} & 1 : 0u)",
                 "lsr": "({s} == 0 ? c : {s} < 32 ? ({x} >> ({s} - 1)) & 1 : {s} == 32 ? {x} >> 31 : 0u)",
                 "asr": "({s} == 0 ? c : {s} < 32 ? ({x} >> ({s} - 1)) & 1 : {x} >> 31)",
                 "ror": "({s} == 0 ? c : ({s} & 31) == 0 ? {x} >> 31 : ({x} >> (({s} & 31) - 1)) & 1)"}

    def reg_shift(self, kind, value, amount):
        """(value, carry) expressions of `value` shifted by the low byte of `amount`, as ARM does
        it (0 keeps the carry, 32 and more have their own results)."""
        x, s = "(%s)" % value, "(%s & 0xffu)" % amount
        return self.REG_SHIFT[kind].format(x=x, s=s), self.REG_CARRY[kind].format(x=x, s=s)

    def imm_shift(self, kind, value, amount):
        """(value, carry or None) expressions of `value` shifted by a constant, ARM's encodings:
        lsr and asr #0 mean #32 in the instruction, ror #0 is rrx (capstone shows it so)."""
        if kind == "lsl":
            if amount == 0:
                return value, None
            return "(%s << %d)" % (value, amount), "((%s >> %d) & 1)" % (value, 32 - amount)
        if kind == "lsr":
            amount = amount or 32
            if amount == 32:
                return "0u", "(%s >> 31)" % value
            return "(%s >> %d)" % (value, amount), "((%s >> %d) & 1)" % (value, amount - 1)
        if kind == "asr":
            amount = min(amount or 32, 32)
            if amount == 32:
                return "(u32)((s32)%s >> 31)" % value, "(%s >> 31)" % value
            return "(u32)((s32)%s >> %d)" % (value, amount), "((%s >> %d) & 1)" % (value, amount - 1)
        if kind == "ror":
            amount &= 31
            if amount == 0:
                return value, "(%s >> 31)" % value
            return "((%s >> %d) | (%s << %d))" % (value, amount, value, 32 - amount), "((%s >> %d) & 1)" % (
                value, amount - 1)
        if kind == "rrx":
            return "((%s >> 1) | (c << 31))" % value, "(%s & 1)" % value
        raise Refused("0x%x: shift %s" % (self.insn_now.address, kind))

    SHIFTS = {A.ARM_SFT_LSL: "lsl", A.ARM_SFT_LSR: "lsr", A.ARM_SFT_ASR: "asr", A.ARM_SFT_ROR: "ror",
              A.ARM_SFT_RRX: "rrx"}
    REG_SHIFTS = {A.ARM_SFT_LSL_REG: "lsl", A.ARM_SFT_LSR_REG: "lsr", A.ARM_SFT_ASR_REG: "asr",
                  A.ARM_SFT_ROR_REG: "ror"}

    def shifted(self, o, sp=False):
        """(C expression, carry expression or None) of a register operand with its shift."""
        value = self.reg(o.reg, sp=sp)
        st, amount = o.shift.type, o.shift.value
        if st in (0, A.ARM_SFT_INVALID) or (st == A.ARM_SFT_LSL and amount == 0):
            return value, None
        if sp:
            raise Refused("0x%x shifts the stack pointer" % self.insn_now.address)
        if st in self.REG_SHIFTS:
            return self.reg_shift(self.REG_SHIFTS[st], value, self.reg(amount))
        if st in self.SHIFTS:
            return self.imm_shift(self.SHIFTS[st], value, amount)
        raise Refused("0x%x: shift type %d" % (self.insn_now.address, st))

    def operand2(self, o):
        """(value, carry-out or None) of a flexible second operand. The carry of an immediate:
        A32 sets it to bit 31 when the immediate is rotated, Thumb-2 when its modified-immediate
        encoding is a rotation (ThumbExpandImm_C); otherwise it is left alone."""
        insn = self.insn_now
        if o.type == A.ARM_OP_IMM:
            value = o.imm & 0xFFFFFFFF
            carry = None
            raw = bytes(insn.bytes)
            if self.prog.isa_at(insn.address) == "a32":
                word = int.from_bytes(raw, "little")
                if (word >> 25) & 1 and (word >> 8) & 0xF:
                    carry = str(value >> 31)
            elif len(raw) == 4:
                hw1, hw2 = int.from_bytes(raw[:2], "little"), int.from_bytes(raw[2:], "little")
                if (hw1 & 0xF800) == 0xF000 and not hw1 & 0x0200 and not hw2 & 0x8000:
                    imm12 = (hw1 >> 10 & 1) << 11 | (hw2 >> 12 & 7) << 8 | (hw2 & 0xFF)
                    if imm12 >> 10:
                        carry = str(value >> 31)
            return "0x%xu" % value, carry
        if o.type == A.ARM_OP_REG:
            return self.shifted(o)
        raise Refused("0x%x: an operand of type %d" % (insn.address, o.type))

    def cond(self, insn, text):
        if self.arch.predicate(insn) is not None and insn.id != A.ARM_INS_B:
            if insn.cc not in COND:
                raise Refused("0x%x: condition %d" % (insn.address, insn.cc))
            return "if (%s) { %s }" % (COND[insn.cc], text)
        return text

    def flags_nz(self, result):
        return "n = %s >> 31; z = %s == 0;" % (result, result)

    def logical(self, d, value, carry):
        """d = value, and with s the flags of a logical instruction: n and z of the result, c from
        the shifter when it gives one. The carry is taken before d is written: the shifted register
        may be d itself (movs r1, r1, lsl #7)."""
        if not self.s:
            return "%s = %s;" % (d, value)
        if carry is None:
            return "%s = %s; %s" % (d, value, self.flags_nz(d))
        return "{ u32 cy = %s; %s = %s; %s c = cy; }" % (carry, d, value, self.flags_nz(d))

    # instructions

    def insn(self, insn):
        self.insn_now = insn
        self.s = self.arch.sets_flags(insn)
        out = self._insn(insn, insn.id, insn.operands, self.prog.isa_at(insn.address))
        return self.cond(insn, out) if out else ""

    def _insn(self, insn, i, ops, isa):
        arch, prog = self.arch, self.prog
        kind, target, falls = arch.flow(insn)
        if i in (A.ARM_INS_NOP, A.ARM_INS_IT) or (i == A.ARM_INS_HINT and insn.mnemonic.split(".")[0] == "nop"):
            return None
        if kind == "return":
            if self.exception_return(insn):
                if self.ctx.get("handler"):
                    return "return;"        # gcc's epilogue restores the registers and returns
                raise Refused("0x%x returns from an exception: %s %s" % (insn.address, insn.mnemonic, insn.op_str))
            if arch.stack_kind(insn) == "pop":
                loads = self._pop(insn, returning=True)
            elif i in self.LDMS:
                loads = self._frame_ldm(insn, returning=True)
            else:
                loads = ""
            return (loads + " " if loads else "") + "return %s;" % self._ret()
        if kind == "jump":
            t = target & ~1
            if i in (A.ARM_INS_CBZ, A.ARM_INS_CBNZ):
                if t not in self.ctx["depth"]:
                    raise Refused("0x%x: %s out of the function" % (insn.address, insn.mnemonic))
                return "if (%s %s 0) goto L_%x;" % (self.reg(ops[0].reg), "==" if i == A.ARM_INS_CBZ else "!=", t)
            if t in self.ctx["depth"]:
                if arch.predicate(insn) is not None:
                    return "if (%s) goto L_%x;" % (COND[insn.cc], t)
                return "goto L_%x;" % t
            callee = prog.by_entry.get(t)
            if callee is None:
                raise Refused("0x%x branches into other code (0x%x)" % (insn.address, t))
            call = self._call(callee, insn)
            if arch.predicate(insn) is not None:
                return "if (%s) { %s return %s; }" % (COND[insn.cc], call, self._ret())
            return "%s return %s;" % (call, self._ret())
        if kind == "call":
            callee = prog.by_entry.get(target & ~1)
            if callee is None:
                raise Refused("0x%x calls into the middle of something (0x%x)" % (insn.address, target))
            text = self._call(callee, insn)
            return text + " for (;;) { }" if self.never_returns(callee.name) else text
        if kind == "icall":
            if i == A.ARM_INS_BLX:
                return self._icall(insn, self.reg(ops[0].reg))
            if getattr(insn, "call_via", None) in REGS:     # bl to a bx rN stub: Thumb-1's call through rN
                return self._icall(insn, insn.call_via)
            if getattr(insn, "link_call", False):   # mov lr, pc in front: ARMv4T's call through a register
                if i == A.ARM_INS_BX:
                    return self._icall(insn, self.reg(ops[0].reg))
                if i == A.ARM_INS_MOV:
                    return self._icall(insn, self.reg(ops[1].reg))
                addr, newbase, _ = self._mem(insn)
                if newbase is not None:
                    raise Refused("0x%x: a call through memory with writeback" % insn.address)
                return "{ u32 f = *(volatile u32 *)(%s); %s }" % (addr, self._icall(insn, "f"))
            if i == A.ARM_INS_SVC:
                return self._svc(insn, ops)
            raise Refused("0x%x: %s" % (insn.address, insn.mnemonic))
        if kind == "ijump":
            if self.ret_jump(insn, self.values()):
                return "return %s;" % self._ret()
            return self._switch_jump(insn)
        if kind == "stop":
            raise Refused("0x%x: %s" % (insn.address, insn.mnemonic))
        stack = arch.stack_kind(insn)
        if stack == "push":
            return self._push(insn)
        if stack == "pop":
            return self._pop(insn)
        if kind == "next" and "sp" in arch.reg_effects(insn)[1]:
            return None                     # the stack pointer's depth is known statically
        lit = arch.literal(insn, isa)
        if lit is not None:
            return self._literal(insn, ops, *lit)
        at = arch.pc_address(insn, isa)
        if at is not None:
            return "%s = 0x%xu;" % (self.dest(ops[0]), at)
        if i in SYSTEM:
            return self._system(insn, ops)
        if i in self.LDMS and any(o.type == A.ARM_OP_REG and o.reg == A.ARM_REG_SP for o in ops[1:]):
            return self._frame_ldm(insn)
        handler = getattr(self, "op_" + OPS[i], None) if i in OPS else None
        if handler is None:
            raise Refused("0x%x: %s %s is not modelled" % (insn.address, insn.mnemonic, insn.op_str))
        return handler(insn, ops)

    def _system(self, insn, ops):
        """An instruction that acts on the core, as inline assembly (see SYSTEM). Status registers:
        a read of APSR/CPSR takes the flags from the C variables and the rest from the core; a write
        of their flags field sets the variables (and the core, for Q and GE). Refused: reading or
        writing a stack pointer (the C's stack is not the original's)."""
        i, a = insn.id, insn.address
        mnemonic = insn.mnemonic.split(".")[0]
        cond = self.arch.condition(insn)
        if cond and mnemonic.endswith(cond):
            mnemonic = mnemonic[:-len(cond)]
        text = (mnemonic + " " + insn.op_str).strip()
        regs = [o for o in ops if o.type == A.ARM_OP_REG]
        if i == A.ARM_INS_HINT and mnemonic not in ("wfi", "wfe", "sev", "sevl", "yield"):
            raise Refused("0x%x: hint %s is not modelled" % (a, insn.op_str))
        if i in (A.ARM_INS_CPS, A.ARM_INS_DMB, A.ARM_INS_DSB, A.ARM_INS_ISB, A.ARM_INS_CLREX, A.ARM_INS_BKPT,
                 A.ARM_INS_HINT):
            return '__asm__ volatile ("%s" ::: "memory");' % text
        if i == A.ARM_INS_MRS:
            sysreg = insn.op_str.split(",", 1)[1].strip().lower()
            d = self.dest(ops[0])
            if sysreg in STACK_POINTERS:
                raise Refused("0x%x reads a stack pointer (%s): the C's stack is not the original's" % (a, sysreg))
            if sysreg.split("_")[0] in STATUS:
                return ('{ u32 t; __asm__ volatile ("mrs %%0, %s" : "=r"(t)); %s = (t & 0x0fffffffu) | n << 31 | '
                        'z << 30 | c << 29 | v << 28; }') % (sysreg, d)
            return '__asm__ volatile ("mrs %%0, %s" : "=r"(%s) :: "memory");' % (sysreg, d)
        if i == A.ARM_INS_MSR:
            sysreg = insn.op_str.split(",", 1)[0].strip().lower()
            if sysreg in STACK_POINTERS:
                raise Refused("0x%x writes a stack pointer (%s)" % (a, sysreg))
            src = ops[-1]
            value = self.reg(src.reg) if src.type == A.ARM_OP_REG else "0x%xu" % (src.imm & 0xFFFFFFFF)
            out = '{ u32 t = %s; __asm__ volatile ("msr %s, %%0" :: "r"(t) : "memory", "cc");' % (value, sysreg)
            base, _, fields = sysreg.partition("_")
            if base in STATUS and ("f" in fields or "nzcv" in fields or fields == ""):
                out += " n = t >> 31; z = t >> 30 & 1; c = t >> 29 & 1; v = t >> 28 & 1;"
            return out + " }"
        if i in (A.ARM_INS_LDREX, A.ARM_INS_LDREXB, A.ARM_INS_LDREXH):
            m = ops[1].mem
            d = self.dest(ops[0])
            addr = "%s + %d" % (self.reg(m.base, sp=True), m.disp)
            return '{ u32 a = %s; __asm__ volatile ("%s %%0, [%%1]" : "=r"(%s) : "r"(a) : "memory"); }' % (
                addr, mnemonic, d)
        if i in (A.ARM_INS_STREX, A.ARM_INS_STREXB, A.ARM_INS_STREXH):
            m = ops[2].mem
            d, value = self.dest(ops[0]), self.reg(ops[1].reg)
            addr = "%s + %d" % (self.reg(m.base, sp=True), m.disp)
            return ('{ u32 a = %s, x = %s, r; __asm__ volatile ("%s %%0, %%2, [%%1]" : "=&r"(r) : "r"(a), "r"(x) : '
                    '"memory"); %s = r; }') % (addr, value, mnemonic, d)
        if i == A.ARM_INS_LDREXD:           # a register pair through a 64-bit operand (gcc picks the pair)
            lo, hi = self.dest(ops[0]), self.dest(ops[1])
            m = ops[2].mem
            addr = "%s + %d" % (self.reg(m.base, sp=True), m.disp)
            return ('{ u32 a = %s; u64 t; __asm__ volatile ("ldrexd %%0, %%H0, [%%1]" : "=&r"(t) : "r"(a) : "memory"); '
                    '%s = (u32)t; %s = (u32)(t >> 32); }') % (addr, lo, hi)
        if i == A.ARM_INS_STREXD:
            d, lo, hi = self.dest(ops[0]), self.reg(ops[1].reg), self.reg(ops[2].reg)
            m = ops[3].mem
            addr = "%s + %d" % (self.reg(m.base, sp=True), m.disp)
            return ('{ u32 a = %s, r; u64 t = (u64)%s << 32 | %s; __asm__ volatile ("strexd %%0, %%1, %%H1, [%%2]" : '
                    '"=&r"(r) : "r"(t), "r"(a) : "memory"); %s = r; }') % (addr, hi, lo, d)
        if i in (A.ARM_INS_MCR, A.ARM_INS_MCR2, A.ARM_INS_MRC, A.ARM_INS_MRC2):
            if len(regs) != 1:
                raise Refused("0x%x: %s %s" % (a, insn.mnemonic, insn.op_str))
            name = self.arch._name(insn.reg_name(regs[0].reg))
            asm = text.replace(insn.reg_name(regs[0].reg), "%0", 1)
            if i in (A.ARM_INS_MRC, A.ARM_INS_MRC2):
                return '__asm__ volatile ("%s" : "=r"(%s) :: "memory");' % (asm, self.dest(regs[0]))
            return '__asm__ volatile ("%s" :: "r"(%s) : "memory");' % (asm, self.reg(regs[0].reg))
        raise Refused("0x%x: %s %s is not modelled" % (a, insn.mnemonic, insn.op_str))

    def _svc(self, insn, ops):
        """svc #n with r0-r3 bound to the handler (its arguments and results); r12, lr and the flags
        are taken as changed, as the convention lets a call change them."""
        return ('{ register u32 x0 __asm__("r0") = r0, x1 __asm__("r1") = r1, x2 __asm__("r2") = r2, '
                'x3 __asm__("r3") = r3; __asm__ volatile ("svc #%d" : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3) :: '
                '"r12", "lr", "memory", "cc"); r0 = x0; r1 = x1; r2 = x2; r3 = x3; }') % (ops[0].imm & 0xFFFFFF)

    def _literal(self, insn, ops, address, size):
        prog = self.prog
        if size not in (1, 2, 4, 8) or not prog.in_image(address, size):
            raise Refused("0x%x: a literal load of %d bytes at 0x%x" % (insn.address, size, address))
        raw = prog.read(address, size)
        if size == 8:
            return "%s = 0x%xu; %s = 0x%xu;" % (self.dest(ops[0]), int.from_bytes(raw[:4], "little"),
                                                self.dest(ops[1]), int.from_bytes(raw[4:], "little"))
        value = int.from_bytes(raw, "little")
        if insn.id in SIGNED and value >> (8 * size - 1):
            value = (value - (1 << 8 * size)) & 0xFFFFFFFF
        return "%s = 0x%xu;" % (self.dest(ops[0]), value)

    def _ret(self):
        if self.ctx.get("handler"):
            return ""
        return "((u64)r1 << 32 | r0)" if self._wide(self.ctx["fn"].name) else "r0"

    def _call(self, callee, insn):
        if not IDENT.match(callee.name):
            raise Refused("0x%x calls %s, whose name is not a C identifier" % (insn.address, callee.name))
        nstack = self.stack_args(callee)
        if nstack is None:
            raise Refused("0x%x calls %s, whose stack arguments are not known (its stack pointer is not static)"
                          % (insn.address, callee.name))
        leaves = self.leaves(callee)
        if leaves:
            raise Refused("0x%x calls %s, which reads %s its caller leaves" % (
                insn.address, callee.name, ", ".join(leaves)))
        self._check_after_call(callee, insn)
        wide = self._wide(callee.name)
        self.ctx["callees"].add((callee.name, nstack, wide))
        d = self.ctx["depth"][insn.address]
        args = list(ARGS)
        for k in range(nstack):
            off = 4 * k - d
            if off < 0:
                args.append("*(volatile u32 *)(sp0 - %d)" % -off)
            elif off // 4 < self.ctx["stack_params"]:
                args.append("s%d" % (off // 4))
            else:
                raise Refused("0x%x passes %s a stack word it does not have" % (insn.address, callee.name))
        call = "%s(%s)" % (callee.name, ", ".join(args))
        if wide:
            return "{ u64 t = %s; r0 = (u32)t; r1 = (u32)(t >> 32); } r2 = r3 = r12 = 0;" % call
        return "r0 = %s; r1 = r2 = r3 = r12 = 0;" % call

    def _icall(self, insn, fp):
        d = self.ctx["depth"][insn.address]
        words = min(d // 4, INDIRECT_STACK_WORDS)
        types = ", ".join(["u32"] * (4 + words))
        args = list(ARGS) + ["*(volatile u32 *)(sp0 - %d)" % (d - 4 * k) for k in range(words)]
        return "r0 = ((u32 (*)(%s))%s)(%s); r1 = r2 = r3 = r12 = 0;" % (types, fp, ", ".join(args))

    def _check_after_call(self, callee, insn):
        """Refuse when the caller reads a scratch register or the flags behind the call that the
        callee leaves (the C cannot know them)."""
        flow = self.flow
        if flow is None:
            return
        result = {"r0", "r1"} if self._wide(callee.name) else {"r0"}
        for reg in flow.kept_regs(callee.name):
            if reg not in result and (self.ctx["fn"].name, insn.address) in flow.sites_of(callee.name, reg):
                raise Refused("0x%x reads %s behind the call of %s (what the callee leaves)" % (
                    insn.address, reg, callee.name))

    def _regs_of(self, insn):
        return [self.name(o.reg) for o in insn.operands if o.type == A.ARM_OP_REG and o.reg != A.ARM_REG_SP]

    def _push(self, insn):
        """The pushed registers into the frame, except those still holding what the caller left
        (the saved callee-saved registers and lr: nothing reads them back but the pop)."""
        regs = self._regs_of(insn)
        d = self.ctx["depth"][insn.address] + 4 * len(regs)
        undef = self.ctx["undef"].get(insn.address, frozenset())
        values = self.values()
        out = []
        for k, r in enumerate(regs):
            if r == "pc":                   # an APCS frame: the address of the push, plus 8
                out.append("*(volatile u32 *)(sp0 - %d) = 0x%xu;" % (d - 4 * k, insn.address + 8))
            elif r not in undef and values.get(r) != self.RET:     # a pop of the return address
                out.append("*(volatile u32 *)(sp0 - %d) = %s;" % (d - 4 * k, r))  # uses the builtin
        return " ".join(out)

    def _frame_ldm(self, insn, returning=False):
        """An ldm through a frame address that loads sp (an APCS epilogue): the other registers from
        their slots (the return address as the builtin), sp left to the static depth; when returning,
        only the registers that carry the result."""
        st = self.ctx["states"][insn.address]
        slots = self.ldm_slots(insn, st[0], st[1])
        if slots is None or insn.writeback:
            raise Refused("0x%x: %s %s is not modelled" % (insn.address, insn.mnemonic, insn.op_str))
        keep = ({"r0", "r1"} if self._wide(self.ctx["fn"].name) else {"r0"}) if returning else None
        out = []
        for r, key in slots:
            if r in ("sp", "pc") or (keep is not None and r not in keep):
                continue
            if r not in REGS:
                raise Refused("0x%x loads %s" % (insn.address, r))
            if st[1].get(key) == self.RET:
                out.append("%s = (u32)__builtin_return_address(0);" % r)
            else:
                out.append("%s = *(volatile u32 *)(sp0 - %d);" % (r, key))
        return " ".join(out)

    def _pop(self, insn, returning=False):
        """The popped registers from the frame; when returning, only those that carry the result."""
        d = self.ctx["depth"][insn.address]
        keep = ({"r0", "r1"} if self._wide(self.ctx["fn"].name) else {"r0"}) if returning else None
        values = self.values()
        out = []
        for k, r in enumerate(self._regs_of(insn)):
            if r == "pc" or (keep is not None and r not in keep):
                continue
            if values.get(d - 4 * k) == self.RET:   # Thumb-1: pop {r0}; bx r0 leaves the address in r0
                out.append("%s = (u32)__builtin_return_address(0);" % r)
            else:
                out.append("%s = *(volatile u32 *)(sp0 - %d);" % (r, d - 4 * k))
        return " ".join(out)

    # data processing

    def _logic(self, insn, ops, expr_fn):
        d = self.dest(ops[0])
        srcs = ops[1:]
        if len(srcs) == 1:
            a, (b, carry) = d, self.operand2(srcs[0])
        else:
            a, (b, carry) = self.reg(srcs[0].reg), self.operand2(srcs[1])
        return self.logical(d, expr_fn(a, b), carry)

    def op_and(self, insn, ops):
        return self._logic(insn, ops, lambda a, b: "%s & %s" % (a, b))

    def op_orr(self, insn, ops):
        return self._logic(insn, ops, lambda a, b: "%s | %s" % (a, b))

    def op_eor(self, insn, ops):
        return self._logic(insn, ops, lambda a, b: "%s ^ %s" % (a, b))

    def op_bic(self, insn, ops):
        return self._logic(insn, ops, lambda a, b: "%s & ~%s" % (a, b))

    def op_orn(self, insn, ops):
        return self._logic(insn, ops, lambda a, b: "%s | ~%s" % (a, b))

    def op_mov(self, insn, ops):
        d = self.dest(ops[0])
        if len(ops) != 2:
            raise Refused("0x%x: %s %s" % (insn.address, insn.mnemonic, insn.op_str))
        src = ops[1]
        if src.type == A.ARM_OP_REG and src.reg == A.ARM_REG_SP and not self.s:
            return "%s = %s;" % (d, self.reg(src.reg, sp=True))
        b, carry = self.operand2(src)
        return self.logical(d, b, carry)

    def op_mvn(self, insn, ops):
        d = self.dest(ops[0])
        b, carry = self.operand2(ops[1])
        return self.logical(d, "~%s" % b, carry)

    def _arith(self, insn, ops, kind):
        d = self.dest(ops[0])
        srcs = ops[1:]
        if len(srcs) == 1:
            first, second = ops[0], srcs[0]
        else:
            first, second = srcs[0], srcs[1]
        if first.type == A.ARM_OP_REG and first.reg == A.ARM_REG_SP:
            # an address in the frame: sp plus a constant below the entry's stack pointer, or plus a
            # register (an index into a local array; the frame mirrors the original's layout)
            if kind != "add" or self.s or (second.type == A.ARM_OP_REG and
                                                       second.shift.type not in (0, A.ARM_SFT_INVALID)):
                raise Refused("0x%x computes with the stack pointer: %s %s" % (
                    insn.address, insn.mnemonic, insn.op_str))
            if second.type == A.ARM_OP_IMM and second.imm >= self.ctx["depth"][insn.address]:
                raise Refused("0x%x takes the address of a stack argument" % insn.address)
            a = self.reg(first.reg, sp=True)
        else:
            a = self.reg(first.reg) if len(srcs) > 1 else d
            v = self.values().get(self.name(first.reg)) if first.type == A.ARM_OP_REG else None
            if v is not None and v[0] == "sp" and second.type == A.ARM_OP_IMM and kind in ("add", "sub"):
                if (v[1] - second.imm if kind == "add" else v[1] + second.imm) <= 0:
                    raise Refused("0x%x takes the address of a stack argument" % insn.address)
        b, _ = self.operand2(second)
        if kind in ("rsb", "rsc"):
            a, b = b, a
        carry_in = {"add": "0", "sub": "1", "adc": "c", "sbc": "c", "rsb": "1", "rsc": "c"}[kind]
        plus = kind in ("add", "adc")
        if not self.s:
            if kind == "add":
                return "%s = %s + %s;" % (d, a, b)
            if kind in ("sub", "rsb"):
                return "%s = %s - %s;" % (d, a, b)
            return "{ u32 x = %s, y = %s; %s = (u32)((u64)x + %s + %s); }" % (a, b, d, "y" if plus else "(u32)~y",
                                                                             carry_in)
        return ("{ u32 x = %s, y = %s; u64 t = (u64)x + %s + %s; %s = (u32)t; n = %s >> 31; z = %s == 0; "
                "c = (u32)(t >> 32); v = ((x ^ %s) & (%s ^ %s)) >> 31; }") % (
                    a, b, "y" if plus else "(u32)~y", carry_in, d, d, d, d, "y" if plus else "~y", d)

    def op_add(self, insn, ops):
        return self._arith(insn, ops, "add")

    def op_adc(self, insn, ops):
        return self._arith(insn, ops, "adc")

    def op_sub(self, insn, ops):
        return self._arith(insn, ops, "sub")

    def op_sbc(self, insn, ops):
        return self._arith(insn, ops, "sbc")

    def op_rsb(self, insn, ops):
        return self._arith(insn, ops, "rsb")

    def op_rsc(self, insn, ops):
        return self._arith(insn, ops, "rsc")

    op_addw, op_subw = op_add, op_sub

    def _compare(self, insn, ops, kind):
        a = self.reg(ops[0].reg)
        b, carry = self.operand2(ops[1])
        if kind in ("tst", "teq"):
            r = "%s %s %s" % (a, "&" if kind == "tst" else "^", b)
            return "{ u32 r = %s; n = r >> 31; z = r == 0;%s }" % (r, " c = %s;" % carry if carry else "")
        if kind == "cmp":
            return ("{ u32 x = %s, y = %s; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; "
                    "c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }") % (a, b)
        return ("{ u32 x = %s, y = %s; u64 t = (u64)x + y; u32 r = (u32)t; n = r >> 31; z = r == 0; "
                "c = (u32)(t >> 32); v = ((x ^ r) & (y ^ r)) >> 31; }") % (a, b)

    def op_cmp(self, insn, ops):
        return self._compare(insn, ops, "cmp")

    def op_cmn(self, insn, ops):
        return self._compare(insn, ops, "cmn")

    def op_tst(self, insn, ops):
        return self._compare(insn, ops, "tst")

    def op_teq(self, insn, ops):
        return self._compare(insn, ops, "teq")

    def _shift(self, insn, ops, kind):
        """lsl, lsr, asr, ror: A32 shows the immediate form as a shifted second operand, Thumb's
        16-bit register form has two operands (lsls rd, rm shifts rd by rm), the rest three."""
        d = self.dest(ops[0])
        if len(ops) == 2 and ops[1].type == A.ARM_OP_REG and ops[1].shift.type not in (0, A.ARM_SFT_INVALID):
            value, carry = self.shifted(ops[1])
            return self.logical(d, value, carry)
        if len(ops) == 2:
            src, amount = d, self.reg(ops[1].reg)
        elif ops[2].type == A.ARM_OP_IMM:
            value, carry = self.imm_shift(kind, "x", ops[2].imm)
            out = "{ u32 x = %s; %s = %s;" % (self.reg(ops[1].reg), d, value)
            if self.s:
                out += " " + self.flags_nz(d) + (" c = %s;" % carry if carry else "")
            return out + " }"
        else:
            src, amount = self.reg(ops[1].reg), self.reg(ops[2].reg)
        value, carry = self.reg_shift(kind, "x", "a")
        out = "{ u32 x = %s, a = %s; %s = %s;" % (src, amount, d, value)
        if self.s:
            out += " c = %s; %s" % (carry, self.flags_nz(d))
        return out + " }"

    def op_lsl(self, insn, ops):
        return self._shift(insn, ops, "lsl")

    def op_lsr(self, insn, ops):
        return self._shift(insn, ops, "lsr")

    def op_asr(self, insn, ops):
        return self._shift(insn, ops, "asr")

    def op_ror(self, insn, ops):
        return self._shift(insn, ops, "ror")

    def op_rrx(self, insn, ops):
        d, s = self.dest(ops[0]), self.reg(ops[1].reg)
        out = "{ u32 x = %s; %s = (x >> 1) | (c << 31);" % (s, d)
        if self.s:
            out += " c = x & 1; " + self.flags_nz(d)
        return out + " }"

    def op_mul(self, insn, ops):
        d = self.dest(ops[0])
        a, b = (self.reg(ops[1].reg), self.reg(ops[2].reg)) if len(ops) == 3 else (d, self.reg(ops[1].reg))
        out = "%s = %s * %s;" % (d, a, b)
        if self.s:
            out += " " + self.flags_nz(d)
        return out

    def op_mla(self, insn, ops):
        d = self.dest(ops[0])
        out = "%s = %s * %s + %s;" % (d, self.reg(ops[1].reg), self.reg(ops[2].reg), self.reg(ops[3].reg))
        if self.s:
            out += " " + self.flags_nz(d)
        return out

    def op_mls(self, insn, ops):
        return "%s = %s - %s * %s;" % (self.dest(ops[0]), self.reg(ops[3].reg), self.reg(ops[1].reg), self.reg(ops[2].reg))

    def _long_mul(self, insn, ops, signed, accumulate):
        lo, hi = self.dest(ops[0]), self.dest(ops[1])
        if lo == hi:
            raise Refused("0x%x: a long multiply into one register" % insn.address)
        a, b = self.reg(ops[2].reg), self.reg(ops[3].reg)
        prod = "(u64)((s64)(s32)%s * (s32)%s)" % (a, b) if signed else "(u64)%s * %s" % (a, b)
        acc = " + ((u64)%s << 32 | %s)" % (hi, lo) if accumulate else ""
        out = "{ u64 t = %s%s; %s = (u32)t; %s = (u32)(t >> 32);" % (prod, acc, lo, hi)
        if self.s:
            out += " n = (u32)(t >> 63); z = t == 0;"
        return out + " }"

    def op_umull(self, insn, ops):
        return self._long_mul(insn, ops, False, False)

    def op_smull(self, insn, ops):
        return self._long_mul(insn, ops, True, False)

    def op_umlal(self, insn, ops):
        return self._long_mul(insn, ops, False, True)

    def op_smlal(self, insn, ops):
        return self._long_mul(insn, ops, True, True)

    def op_udiv(self, insn, ops):
        d, a, b = self.dest(ops[0]), self.reg(ops[1].reg), self.reg(ops[2].reg)
        return "{ u32 x = %s, y = %s; %s = y ? x / y : 0; }" % (a, b, d)

    def op_sdiv(self, insn, ops):
        d, a, b = self.dest(ops[0]), self.reg(ops[1].reg), self.reg(ops[2].reg)
        return ("{ u32 x = %s, y = %s; %s = y == 0 ? 0 : (x == 0x80000000u && y == 0xffffffffu) ? x : "
                "(u32)((s32)x / (s32)y); }") % (a, b, d)

    def op_clz(self, insn, ops):
        d, s = self.dest(ops[0]), self.reg(ops[1].reg)
        return "{ u32 x = %s; %s = x ? (u32)__builtin_clz(x) : 32; }" % (s, d)

    def _extend(self, insn, ops, cast, add=False):
        d = self.dest(ops[0])
        src = ops[2] if add else ops[1]
        value = self.reg(src.reg)
        rot = src.shift.value if src.shift.type == A.ARM_SFT_ROR else 0
        if rot:
            value = "((%s >> %d) | (%s << %d))" % (value, rot, value, 32 - rot)
        expr = "(u32)(%s)(%s)" % (cast, value)
        if add:
            expr = "%s + %s" % (self.reg(ops[1].reg), expr)
        return "%s = %s;" % (d, expr)

    def op_uxtb(self, insn, ops):
        return self._extend(insn, ops, "u8")

    def op_uxth(self, insn, ops):
        return self._extend(insn, ops, "u16")

    def op_sxtb(self, insn, ops):
        return self._extend(insn, ops, "s8")

    def op_sxth(self, insn, ops):
        return self._extend(insn, ops, "s16")

    def op_uxtab(self, insn, ops):
        return self._extend(insn, ops, "u8", add=True)

    def op_uxtah(self, insn, ops):
        return self._extend(insn, ops, "u16", add=True)

    def op_sxtab(self, insn, ops):
        return self._extend(insn, ops, "s8", add=True)

    def op_sxtah(self, insn, ops):
        return self._extend(insn, ops, "s16", add=True)

    def op_rev(self, insn, ops):
        return "%s = __builtin_bswap32(%s);" % (self.dest(ops[0]), self.reg(ops[1].reg))

    def op_rev16(self, insn, ops):
        s = self.reg(ops[1].reg)
        return "{ u32 x = %s; %s = ((x >> 8) & 0x00ff00ffu) | ((x << 8) & 0xff00ff00u); }" % (s, self.dest(ops[0]))

    def op_revsh(self, insn, ops):
        s = self.reg(ops[1].reg)
        return "{ u32 x = %s; %s = (u32)(s16)(((x >> 8) & 0xff) | ((x << 8) & 0xff00)); }" % (s, self.dest(ops[0]))

    def op_movw(self, insn, ops):
        return "%s = 0x%xu;" % (self.dest(ops[0]), ops[1].imm & 0xFFFF)

    def op_movt(self, insn, ops):
        d = self.dest(ops[0])
        return "%s = (%s & 0xffffu) | 0x%xu;" % (d, d, (ops[1].imm & 0xFFFF) << 16)

    def op_ubfx(self, insn, ops):
        d, s, lsb, width = self.dest(ops[0]), self.reg(ops[1].reg), ops[2].imm, ops[3].imm
        return "%s = (%s >> %d) & 0x%xu;" % (d, s, lsb, (1 << width) - 1)

    def op_sbfx(self, insn, ops):
        d, s, lsb, width = self.dest(ops[0]), self.reg(ops[1].reg), ops[2].imm, ops[3].imm
        return "%s = (u32)((s32)(%s << %d) >> %d);" % (d, s, 32 - lsb - width, 32 - width)

    def op_bfc(self, insn, ops):
        d, lsb, width = self.dest(ops[0]), ops[1].imm, ops[2].imm
        return "%s &= 0x%xu;" % (d, ~(((1 << width) - 1) << lsb) & 0xFFFFFFFF)

    def op_bfi(self, insn, ops):
        d, s, lsb, width = self.dest(ops[0]), self.reg(ops[1].reg), ops[2].imm, ops[3].imm
        mask = ((1 << width) - 1) << lsb
        return "%s = (%s & 0x%xu) | ((%s << %d) & 0x%xu);" % (d, d, ~mask & 0xFFFFFFFF, s, lsb, mask)

    # memory

    def _mem(self, insn):
        """(address expression, new base expression or None, base register) of the memory operand.
        Both expressions are computed before the access. A post-indexed access is recognised by the
        operand behind the memory operand (capstone does not always set post_index)."""
        ops = list(insn.operands)
        k = next(i for i, o in enumerate(ops) if o.type == A.ARM_OP_MEM)
        o, m = ops[k], ops[k].mem
        base = self.name(m.base)
        post = next((x for x in ops[k + 1:] if x.type in (A.ARM_OP_IMM, A.ARM_OP_REG)), None)
        if base == "pc":
            raise Refused("0x%x: a pc-relative access that is not a literal" % insn.address)
        if base == "sp":
            if post is not None or insn.writeback:
                raise Refused("0x%x: sp written back outside push and pop" % insn.address)
            d = self.ctx["depth"][insn.address]
            if not m.index:
                off = m.disp - d
                if off >= 0:
                    if off % 4:
                        raise Refused("0x%x: a stack argument access not on a word" % insn.address)
                    if off // 4 >= self.ctx["stack_params"]:
                        raise Refused("0x%x: a stack word beyond the arguments" % insn.address)
                    return "(u32)&s%d" % (off // 4), None, base
                return "(sp0 - %d)" % (d - m.disp), None, base
        v = self.values().get(base)
        if v is not None and v[0] == "sp" and not m.index and post is None and not insn.writeback:
            off = m.disp - v[1]             # through a frame pointer: a stack argument or a frame word
            if off >= 0:
                if off % 4 or off // 4 >= self.ctx["stack_params"]:
                    raise Refused("0x%x: a stack argument access not on a word" % insn.address)
                return "(u32)&s%d" % (off // 4), None, base
        b = self.reg(m.base, sp=True)
        if post is not None:
            if m.index or m.disp:
                raise Refused("0x%x: post-indexed with an offset in the brackets" % insn.address)
            if post.type == A.ARM_OP_IMM:
                imm = -abs(post.imm) if post.subtracted or post.imm < 0 else post.imm
                step = "%s %d" % ("-" if imm < 0 else "+", abs(imm))
            else:
                value, _ = self.shifted(post)
                step = ("- " if post.subtracted else "+ ") + value
            return b, "%s %s" % (b, step), base
        if m.index:
            idx = self.reg(m.index)
            if o.shift.type in self.SHIFTS and (o.shift.value or o.shift.type == A.ARM_SFT_RRX):
                idx, _ = self.imm_shift(self.SHIFTS[o.shift.type], idx, o.shift.value)
            elif o.shift.type not in (0, A.ARM_SFT_INVALID, A.ARM_SFT_LSL):
                raise Refused("0x%x: an index shifted by a register" % insn.address)
            offset = ("- " if o.subtracted else "+ ") + idx
        else:
            disp = -abs(m.disp) if (o.subtracted and m.disp > 0) else m.disp
            offset = "+ %d" % disp if disp >= 0 else "- %d" % -disp
        expr = "(%s %s)" % (b, offset)
        if insn.writeback:
            if base == "sp":
                raise Refused("0x%x: sp written back outside push and pop" % insn.address)
            return expr, expr, base
        return expr, None, base

    def _load(self, insn, ops, ctype):
        d = self.dest(ops[0])
        if ops[1].type != A.ARM_OP_MEM:
            raise Refused("0x%x: %s %s" % (insn.address, insn.mnemonic, insn.op_str))
        st = self.ctx["states"][insn.address]
        made = self.made_value(insn, st[0], st[1]) if ctype == "u32" else None
        if made is not None and made[1] == self.RET:    # the saved return address (not stored: see _push)
            return "%s = (u32)__builtin_return_address(0);" % d
        addr, newbase, base = self._mem(insn)
        if newbase is None:
            return "%s = (u32)*(volatile %s *)(%s);" % (d, ctype, addr)
        if base == d:
            raise Refused("0x%x loads into its own base with writeback" % insn.address)
        return "{ u32 a = %s, w = %s; %s = (u32)*(volatile %s *)a; %s = w; }" % (addr, newbase, d, ctype, base)

    def _store(self, insn, ops, ctype):
        s = self.reg(ops[0].reg)
        addr, newbase, base = self._mem(insn)
        if newbase is None:
            return "*(volatile %s *)(%s) = (%s)%s;" % (ctype, addr, ctype, s)
        if base == s:
            raise Refused("0x%x stores its own base with writeback" % insn.address)
        return "{ u32 a = %s, w = %s; *(volatile %s *)a = (%s)%s; %s = w; }" % (addr, newbase, ctype, ctype, s, base)

    def op_ldr(self, insn, ops):
        return self._load(insn, ops, "u32")

    def op_ldrb(self, insn, ops):
        return self._load(insn, ops, "u8")

    def op_ldrh(self, insn, ops):
        return self._load(insn, ops, "u16")

    def op_ldrsb(self, insn, ops):
        return self._load(insn, ops, "s8")

    def op_ldrsh(self, insn, ops):
        return self._load(insn, ops, "s16")

    def op_str(self, insn, ops):
        return self._store(insn, ops, "u32")

    def op_strb(self, insn, ops):
        return self._store(insn, ops, "u8")

    def op_strh(self, insn, ops):
        return self._store(insn, ops, "u16")

    def op_ldrd(self, insn, ops):
        lo, hi = self.dest(ops[0]), self.dest(ops[1])
        addr, newbase, base = self._mem(insn)
        if newbase is not None and base in (lo, hi):
            raise Refused("0x%x loads into its own base with writeback" % insn.address)
        return "{ u32 a = %s, w = %s; %s = *(volatile u32 *)a; %s = *(volatile u32 *)(a + 4);%s }" % (
            addr, newbase or "0", lo, hi, " %s = w;" % base if newbase else "")

    def op_strd(self, insn, ops):
        lo, hi = self.reg(ops[0].reg), self.reg(ops[1].reg)
        addr, newbase, base = self._mem(insn)
        if newbase is not None and base in (lo, hi):
            raise Refused("0x%x stores its own base with writeback" % insn.address)
        return "{ u32 a = %s, w = %s; *(volatile u32 *)a = %s; *(volatile u32 *)(a + 4) = %s;%s }" % (
            addr, newbase or "0", lo, hi, " %s = w;" % base if newbase else "")

    def _block(self, insn, ops, load, before, up):
        base = self.name(ops[0].reg)
        regs = [self.name(o.reg) for o in ops[1:]]
        if "pc" in regs or "sp" in regs:
            raise Refused("0x%x: a block transfer of pc or sp" % insn.address)
        n = len(regs)
        start = {(False, True): 0, (True, True): 4, (False, False): -4 * n + 4, (True, False): -4 * n}[(before, up)]
        if base == "sp":
            d = self.ctx["depth"][insn.address]
            if insn.writeback or start + 4 * n > d or start - d < -self.ctx["frame"]:
                raise Refused("0x%x: a block transfer on sp outside push, pop and the frame" % insn.address)
        b = self.reg(ops[0].reg, sp=True)
        if insn.writeback and base in regs:
            if load or regs[0] != base:
                raise Refused("0x%x: a block transfer with its own base written back" % insn.address)
        out = ["{ u32 a = %s + (%d);" % (b, start)]
        for k, r in enumerate(regs):
            if load:
                out.append("%s = *(volatile u32 *)(a + %d);" % (r, 4 * k))
            else:
                out.append("*(volatile u32 *)(a + %d) = %s;" % (4 * k, r))
        if insn.writeback:
            out.append("%s = %s %s %d;" % (base, b, "+" if up else "-", 4 * n))
        return " ".join(out) + " }"

    def op_ldm(self, insn, ops):
        return self._block(insn, ops, True, False, True)

    def op_ldmib(self, insn, ops):
        return self._block(insn, ops, True, True, True)

    def op_ldmda(self, insn, ops):
        return self._block(insn, ops, True, False, False)

    def op_ldmdb(self, insn, ops):
        return self._block(insn, ops, True, True, False)

    def op_stm(self, insn, ops):
        return self._block(insn, ops, False, False, True)

    def op_stmib(self, insn, ops):
        return self._block(insn, ops, False, True, True)

    def op_stmda(self, insn, ops):
        return self._block(insn, ops, False, False, False)

    def op_stmdb(self, insn, ops):
        return self._block(insn, ops, False, True, False)

    # switches and indirect jumps

    def _indirect_tail(self, insn):
        """An indirect jump whose every target is another function's entry: a tail call. With one
        target (a veneer, a far jump through a literal) the call goes to that address; with several
        (bx rN after a table of function pointers) through the register."""
        got = self.resolve(self.ctx["fn"], insn.address)
        callees = [self.prog.by_entry.get(t & ~1) for t in got[1]] if got and not got[0] else []
        if not callees or not all(callees):
            raise Refused("0x%x: an indirect jump that is neither a resolved switch nor a tail call" % insn.address)
        if self.ctx["depth"][insn.address]:
            raise Refused("0x%x: an indirect tail call with a frame" % insn.address)
        for callee in callees:
            leaves = self.leaves(callee)
            if leaves:
                raise Refused("0x%x jumps to %s, which reads %s its caller leaves" % (
                    insn.address, callee.name, ", ".join(leaves)))
            n = self.stack_args(callee)
            if n != 0:
                raise Refused("0x%x jumps to %s, %s" % (insn.address, callee.name, "which takes stack arguments" if n
                                                        else "whose stack arguments are not known"))
        wide = any(self._wide(c.name) for c in callees)
        if self._wide(self.ctx["fn"].name) and not wide:
            raise Refused("0x%x: its callers read r1 behind a tail call that does not set it" % insn.address)
        if len(callees) == 1:
            t = callees[0].entry
            pointer = "0x%xu" % (t | (1 if self.prog.isa_at(t) == "t32" else 0))
        else:
            ops = insn.operands
            if insn.id not in (A.ARM_INS_BX, A.ARM_INS_MOV) or not ops or ops[-1].type != A.ARM_OP_REG:
                raise Refused("0x%x: an indirect tail call through %s" % (insn.address, insn.mnemonic))
            pointer = self.reg(ops[-1].reg)
        return "return ((%s (*)(u32, u32, u32, u32))%s)(r0, r1, r2, r3);" % ("u64" if wide else "u32", pointer)

    def _switches(self, fn):
        """Indirect jumps that are switches: the index their bound check compares is saved there
        and switched over at the jump, with the target dread.switches finds for every value."""
        from dread import switches
        for a in fn.insns:
            insn = self.prog.insn(a)
            if a not in self.ctx["depth"] or self.arch.flow(insn)[0] != "ijump":
                continue
            mapping = switches.mapping(self.prog, fn, a)
            if not mapping:
                continue
            check, reg, values, limit = mapping
            missing = [k for k in range(limit + 1) if k not in values]
            if missing:
                raise Refused("0x%x: a switch whose target for index %d is not known" % (a, missing[0]))
            self.ctx["switch"][a] = (check, reg, values)
            self.ctx["switch_targets"].update(values.values())

    def _switch_jump(self, insn):
        got = self.ctx["switch"].get(insn.address)
        if got is None:
            return self._indirect_tail(insn)
        check, reg, values = got
        cases = " ".join("case %d: goto L_%x;" % (k, t) for k, t in sorted(values.items()))
        return "switch (index) { %s default: return r0; }" % cases


def check(target, image, names=None, out=print, jobs=None, system=True):
    """The acceptance loop (see the module's docstring); returns the accepted names."""
    from dread import build, overlay, regflow, runner
    program = Program(target, image)
    lifter = make_lifter(program, regflow.load(target, image, program))
    work = os.path.join(target.build, "lift-check", image.name)
    by_hand = build.defined_functions(build.compile_all(target, image, image.src, os.path.join(work, "obj"),
                                                        quiet=True))
    entered = set()                         # branched into past their entry: the overlay cannot replace them
    for fn in program.functions:
        for insn in program.instructions(fn):
            t = program.arch.target(insn) if insn is not None else None
            owner = program.function_at(t & ~1) if t is not None else None
            if owner is not None and owner is not fn and (t & ~1) != owner.entry:
                entered.add(owner.name)
    candidates, refused, endless = [], 0, []
    verdicts = {}                           # name -> (verdict, reason, missed): verdicts.tsv for readers

    def refuse(name, reason):
        verdicts[name] = ("refused", str(reason), "")
        out("refused  %-24s %s" % (name, reason))

    for name in names or [fn.name for fn in program.functions]:
        fn = program.by_name.get(name)
        if fn is None:
            continue
        if name in by_hand:
            verdicts[name] = ("by hand", "src defines it", "")
            continue
        refused += 1
        if program.stored(fn.entry) != fn.entry or not program.in_image(fn.entry):
            refuse(name, "runs from a copy in RAM (the overlay cannot place C there)")
            continue
        if name in entered:
            refuse(name, "other code branches into its middle (the overlay cannot replace it)")
            continue
        try:
            system_only = lifter.system_only(fn)
        except Refused as e:
            refuse(name, e)
            continue
        if system_only:
            try:
                lifter.lift(fn)
                endless.append(name)        # no case can end or call it: the system run decides (system_check)
                refused -= 1
            except Refused as e:
                refuse(name, e)
            continue
        try:
            lifter.lift(fn)
            candidates.append(name)
            refused -= 1
        except Refused as e:
            refuse(name, e)
    callees = {n: direct_callees(program, program.by_name[n]) for n in candidates}
    accepted, retry, retried, rounds = list(candidates), [], False, 0
    while accepted:
        rounds += 1
        try:
            payload = build.build(target, image, out=work, lifted=accepted, quiet=True)
        except overlay.OverlayError as e:
            if e.function in accepted:      # one function the overlay cannot place: without it
                out("dropped  %-24s %s" % (e.function, e))
                verdicts[e.function] = ("dropped", "the overlay: %s" % e, "")
                accepted = [n for n in accepted if n != e.function]
                continue
            out("dread lift: the build failed: %s" % e)
            return []
        runner.prepare(target, image, payload)
        lines = []
        _, uncovered, results = runner.run(accepted, only=True, out=lines.append, jobs=jobs)
        failing = {name for name, count, bad in results if bad}
        drop = failing | set(uncovered)
        ungenerated = set()                 # no cases from the original (it never returns, say): final
        for text in lines:
            for line in text.splitlines():
                for name in drop:
                    if line.startswith(("FAIL %s " % name, "UNCOVERED %s:" % name)):
                        out("dropped  " + line[:200])
                        if name not in verdicts or verdicts[name][0] != "dropped":
                            missed = " ".join("0x%x" % a for a in runner.RUN.get("missed", {}).get(name, ()))
                            verdicts[name] = ("dropped", line[:300], missed)
                        if "[case generation]" in line:
                            ungenerated.add(name)
        if not retried:
            # failed while a lifted callee failed too: perhaps only through that callee
            retry += [n for n in failing - ungenerated if callees[n] & failing]
        accepted = [n for n in accepted if n not in drop]
        if not drop:
            if retry and not retried:
                out("retry    %s (a lifted callee of each failed too)" % ", ".join(sorted(retry)))
                accepted += retry
                retried = True
                continue
            break
    by_system = system_check(target, image, program, lifter, accepted, endless, out, verdicts) \
        if system and target.scenarios else []
    for name in endless:
        verdicts.setdefault(name, ("dropped", "never returns or an exception handler, and the target has no "
                                              "system-run scenarios to test it", ""))
    dropped = len(candidates) - len(accepted) + len(endless) - len(by_system)
    accepted = accepted + by_system
    for name in accepted:
        verdicts[name] = ("accepted", "by the system run" if name in by_system else "", "")
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "verdicts.tsv"), "w") as f:
        f.write("function\tverdict\treason\tmissed\n")
        f.writelines("%s\t%s\t%s\t%s\n" % (n, v, r.replace("\t", " ").replace("\n", " "), m)
                     for n, (v, r, m) in sorted(verdicts.items()))
    path = image.table("lifted")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("# Functions built from lifted C: their cases (for one that never returns: every scenario\n"
                "# of the system run) agree with the original and run every instruction it can reach.\n"
                "# Written by dread lift --check; not edited by hand.\n")
        f.write("".join(n + "\n" for n in sorted(accepted)))
    out("dread lift --check: %d accepted after %d rounds (%d by the system run; %d written by hand, %d refused, "
        "%d dropped), into %s" % (len(accepted), rounds, len(by_system), len(by_hand & set(program.by_name)), refused,
                                  dropped, os.path.relpath(path)))
    return accepted


class Coverage:
    """A system run's observer: the instructions of some functions that ran."""

    def __init__(self, program, names):
        self.program = program
        self.insns = {program.by_name[n].entry: sorted(program.by_name[n].insns) for n in names}
        self.spans = sorted((program.by_name[n].insns[0], program.end(program.by_name[n])) for n in names)
        self.ran = set()

    def block(self, addr, size):
        import bisect
        for lo, hi in self.spans:
            if lo <= addr < hi:
                fn = self.program.function_at(addr)
                if fn is None:
                    return
                insns = self.insns.get(fn.entry, ())
                i = bisect.bisect_left(insns, addr)
                while i < len(insns) and insns[i] < addr + size:
                    self.ran.add(insns[i])
                    i += 1
                return

    def io(self, addr, size, value):
        pass


def system_check(target, image, program, lifter, accepted, names, out=print, verdicts=None):
    """Functions that never return (a main loop, a reset handler) get no function-level case. Each
    one is built from lifted C on its own (with the functions already accepted), and every scenario
    of the target runs on the original and on the rebuilt image (dread.sysrun): accepted when the
    device logs, model reports and endings agree and the original's runs executed every instruction
    of it that a path reaches. Returns the accepted names; verdicts, a dict, gets the reason of each
    one dropped (see check)."""
    import json
    verdicts = {} if verdicts is None else verdicts
    from dread import build, overlay, sysrun
    work = os.path.join(target.build, "lift-system", image.name)
    original = image.payload()
    kept = []
    for name in names:
        fn = program.by_name[name]
        try:
            unreached = set(lifter.lift(fn).unreached)
            payload = build.build(target, image, out=work, lifted=list(accepted) + kept + [name], quiet=True)
        except (Refused, overlay.OverlayError) as e:
            out("dropped  %-24s %s" % (name, e))
            verdicts[name] = ("dropped", str(e), "")
            continue
        with open(os.path.join(work, "placement.json")) as f:
            placed = json.load(f)["functions"]
        built = [n for n, p in placed.items() if p["placement"] != "helper"]
        with open(payload, "rb") as f:
            rebuilt = f.read()
        coverage, why = Coverage(program, [name]), None
        for scenario_name, scenario in sorted(target.scenarios.items()):
            scenario = dict(scenario, _name=scenario_name)
            system = sysrun.System(target, image, original, program=program, built=built, scenario=scenario)
            system.observers.append(coverage)
            a = system.run(int(scenario.get("limit", sysrun.LIMIT)))
            b = sysrun.run_image(target, image, rebuilt, scenario, placement=placed)
            diff = sysrun.compare(a, b)
            if diff:
                why = "scenario %s: %s" % (scenario_name, " ".join(diff)[:200])
                break
        missing = sorted(set(fn.insns) - coverage.ran - unreached) if why is None else []
        if why is None and missing:
            why = "the system run never ran %d of its %d instructions: %s" % (
                len(missing), len(fn.insns), " ".join("0x%x" % a for a in missing[:8]))
        if why:
            kind = "an exception handler" if lifter.handler_kind(fn) else "never returns"
            out("dropped  %-24s %s; %s" % (name, kind, why))
            verdicts[name] = ("dropped", "%s; %s" % (kind, why), " ".join("0x%x" % a for a in missing))
        else:
            out("accepted %-24s %s; every scenario agrees and runs all of it" % (
                name, "an exception handler" if lifter.handler_kind(fn) else "never returns"))
            kept.append(name)
    return kept


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread lift", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--out", help="directory for the lifted C (default: BUILD/lifted/IMAGE)")
    parser.add_argument("--check", action="store_true", help="test the lifted C and write the lifted table")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
    except tgt.TargetError as e:
        print("dread lift: %s" % e, file=sys.stderr)
        return 1
    if args.check:
        from dread.util.polite import polite
        polite()
        check(target, image, args.names or None)
        return 0
    from dread import regflow
    program = Program(target, image)
    lifter = make_lifter(program, regflow.load(target, image, program))
    out = args.out or os.path.join(target.build, "lifted", image.name)
    os.makedirs(out, exist_ok=True)
    lifted = refused = 0
    for name in args.names or [fn.name for fn in program.functions]:
        fn = program.by_name.get(name)
        if fn is None:
            print("dread lift: no function %s" % name, file=sys.stderr)
            continue
        try:
            result = lifter.lift(fn)
        except Refused as e:
            refused += 1
            print("refused  %-24s %s" % (name, e))
            continue
        with open(os.path.join(out, name + ".c"), "w") as f:
            f.write(result.text)
        lifted += 1
        print("lifted   %-24s %d instructions" % (name, len(fn.insns)))
    print("dread lift: %d lifted, %d refused, into %s" % (lifted, refused, os.path.relpath(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
