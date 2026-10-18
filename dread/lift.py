"""dread lift: translate functions of the original to C, instruction by instruction ("lifting").

Every register becomes a local variable, every instruction a C statement (with the instruction as a
comment), every branch a goto, every load and store a volatile access of the same width in the same
order; the condition flags are variables computed exactly (carry and overflow through 64-bit
arithmetic), conditional instructions (ARM's condition field, Thumb's IT blocks) are if statements.
The function's own stack is a local array: the stack pointer's depth below its value at the entry
is known statically at every instruction (refused otherwise), so [sp, #k] is a word of that array,
or, above the entry, a stack argument, which is a parameter. Literal loads and adr are constants read
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
code that falls through into what follows (a call that does not return, for one), or an entry in
the asm_only table.

With --check the lifted C is put to the test: every function that lifts (and that src does not
define by hand) is built over the original together with the others, its generated cases run
(callees real, dread.autocase), and a function whose cases disagree or leave an instruction of the
original unrun is dropped; the build and the run are repeated until nothing is dropped. A function
that failed while a callee of it was lifted and failed too gets one more round without that callee.
What stays is written to the image's lifted table (config/lifted.txt), which dread recon builds
from then on (it lifts those functions again at every build: the C is not kept).

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
MAX_STACK_ARGS = 16      # stack words a function may take; more is refused
INDIRECT_STACK_WORDS = 4  # frame words a call through a register passes on as stack arguments
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Refused(Exception):
    """The function uses something the translation does not model exactly."""


class Lifted:
    """The result for one function."""

    def __init__(self, name, text, callees, frame, stack_params):
        self.name, self.text, self.callees, self.frame, self.stack_params = name, text, callees, frame, stack_params


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


class Lifter:
    def __init__(self, program, flow=None):
        self.prog, self.arch, self.flow = program, program.arch, flow
        self._stack_args, self._entry, self._resolved = {}, {}, {}
        path = program.image.table("asm_only")
        self.asm_only = read_ranges(path) if path and os.path.exists(path) else []

    # ---- control flow and the stack pointer ----

    def resolve(self, fn, address):
        key = (fn.name, address)
        if key not in self._resolved:
            from dread import switches
            self._resolved[key] = switches.resolve(self.prog, fn, address)
        return self._resolved[key]

    def successors(self, fn, index, address, insn, strict=True):
        """The instructions of fn that may run after the one at `address`. Falling through out of
        the function or into data raises Refused when strict, else has no successor."""
        kind, target, falls = self.arch.flow(insn)
        out = []
        if kind in ("next", "call", "icall") or falls:
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

    def depths(self, fn, strict=True):
        """{address: bytes below the entry's sp before the instruction} for every instruction a path
        from the entry reaches; raises Refused when it is not the same on every path or changes in a
        way not modelled (and, when strict, when the code falls through out of the function)."""
        prog = self.prog
        index = {a: i for i, a in enumerate(fn.insns)}
        depth = {fn.insns[0]: 0}
        work = [fn.insns[0]]
        while work:
            a = work.pop()
            insn = prog.insn(a)
            if insn is None:
                raise Refused("0x%x does not decode" % a)
            change = self.sp_change(insn)
            kind = self.arch.flow(insn)[0]
            if change and self.arch.is_conditional(insn) and kind != "return":
                raise Refused("0x%x moves the stack pointer under a condition" % a)
            d = depth[a] + (0 if kind == "return" else change)     # a conditional return goes on unpopped
            for b in self.successors(fn, index, a, insn, strict):
                if b in depth:
                    if depth[b] != d:
                        raise Refused("the stack pointer differs at 0x%x (%d and %d bytes)" % (b, depth[b], d))
                else:
                    depth[b] = d
                    work.append(b)
        return depth

    def sp_change(self, insn):
        """How far an instruction moves the stack pointer down (negative: up)."""
        ops, i = insn.operands, insn.id
        kind = self.arch.stack_kind(insn)
        if kind in ("push", "pop"):
            regs = [o for o in ops if o.type == A.ARM_OP_REG and o.reg != A.ARM_REG_SP]
            return 4 * len(regs) if kind == "push" else -4 * len(regs)
        reads, writes = self.arch.reg_effects(insn)
        if "sp" not in writes:
            return 0
        if i in (A.ARM_INS_SUB, A.ARM_INS_ADD, A.ARM_INS_SUBW, A.ARM_INS_ADDW) and ops[-1].type == A.ARM_OP_IMM \
                and all(o.type == A.ARM_OP_REG and o.reg == A.ARM_REG_SP for o in ops[:-1]):
            imm = ops[-1].imm
            return imm if i in (A.ARM_INS_SUB, A.ARM_INS_SUBW) else -imm
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
        top = 0
        for a in fn.insns:
            if a not in depth:
                continue
            insn, d = self.prog.insn(a), depth[a]
            for o in insn.operands:
                if o.type == A.ARM_OP_MEM and o.mem.base == A.ARM_REG_SP and not o.mem.index:
                    off = o.mem.disp - d
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
        nothing; a call leaves the scratch registers, lr and the flags written."""
        if fn.name in self._entry:
            return self._entry[fn.name]
        prog, arch = self.prog, self.arch
        index = {a: i for i, a in enumerate(fn.insns)}
        start = frozenset(r for r in REGS + ["flags"] if r not in ARGS + ("sp", "pc"))
        state, found = {fn.insns[0]: start}, {}
        work = [fn.insns[0]]
        while work:
            a = work.pop()
            insn = prog.insn(a)
            if insn is None:
                continue
            undef = state[a]
            kind = arch.flow(insn)[0]
            reads, writes = arch.reg_effects(insn)
            if arch.stack_kind(insn) == "push" or kind == "return":
                reads = set()
            if kind in ("call", "icall"):
                writes = set(writes) | AFTER_CALL
            bad = (reads & undef) - {"sp", "pc"}
            if bad:
                found[a] = bad
            after = undef if arch.predicate(insn) is not None else undef - writes
            for b in self.successors(fn, index, a, insn, strict=False):
                old = state.get(b)
                new = after if old is None else old | after
                if new != old:
                    state[b] = new
                    work.append(b)
        self._entry[fn.name] = (state, found)
        return state, found

    def leaves(self, fn):
        """The registers beyond r0-r3 a function reads before anything writes them."""
        found = self.entry_states(fn)[1]
        return sorted(set().union(*found.values()) - set(ARGS)) if found else []

    # ---- the translation ----

    def lift(self, fn):
        prog, arch = self.prog, self.arch
        for lo, hi, reason in self.asm_only:
            if fn.entry == lo or (hi is not None and lo <= fn.entry < hi):
                raise Refused("listed in asm_only: %s" % reason)
        if not IDENT.match(fn.name):
            raise Refused("its name is not a C identifier")
        insns = prog.instructions(fn)
        if not insns or any(i is None for i in insns):
            raise Refused("an instruction does not decode")
        depth = self.depths(fn)
        undef, found = self.entry_states(fn)
        if found:
            a = min(found)
            raise Refused("0x%x reads %s before anything writes it (what its caller leaves)" % (
                a, ", ".join(sorted(found[a]))))
        self._check_kept(fn)
        stack_params = self.stack_args(fn)
        if stack_params > MAX_STACK_ARGS:
            raise Refused("it takes %d stack words" % stack_params)
        frame = (max(depth.values()) + 7) & ~7
        targets = set()
        for insn in insns:
            kind, target, _ = arch.flow(insn)
            if kind == "jump" and target is not None and (target & ~1) in depth:
                targets.add(target & ~1)
        self.ctx = {"fn": fn, "depth": depth, "undef": undef, "frame": frame, "callees": set(),
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
        return Lifted(fn.name, self.render(fn, body), sorted(self.ctx["callees"]), frame, stack_params)

    def render(self, fn, body):
        ctx = self.ctx
        params = ["u32 a%d" % i for i in range(4)] + ["u32 s%d" % i for i in range(ctx["stack_params"])]
        lines = ["/* %s at 0x%x, lifted by dread lift: one statement per instruction. */" % (fn.name, fn.entry),
                 '#include "dread.h"']
        for name, nstack, wide in sorted(ctx["callees"]):
            lines.append("extern %s %s(%s);" % ("u64" if wide else "u32", name, ", ".join(["u32"] * (4 + nstack))))
        lines.append("%s %s(%s)" % ("u64" if self._wide(fn.name) else "u32", fn.name, ", ".join(params)))
        lines.append("{")
        lines.append("    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r5 = 0, r6 = 0, r7 = 0, r8 = 0, r9 = 0, r10 = 0,"
                     " r11 = 0, r12 = 0, lr = 0;")
        lines.append("    u32 n = 0, z = 0, c = 0, v = 0;")
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

    def reg(self, r, sp=False):
        """The C expression of a register read; the stack pointer only where `sp` allows it."""
        name = self.name(r)
        if name == "pc":
            raise Refused("0x%x reads pc" % self.insn_now.address)
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
        if i in (A.ARM_INS_NOP, A.ARM_INS_IT):
            return None
        if kind == "return":
            if i in (A.ARM_INS_SUB, A.ARM_INS_SUBS if hasattr(A, "ARM_INS_SUBS") else -1) or \
                    (self.s and i == A.ARM_INS_MOV) or getattr(insn, "usermode", False):
                raise Refused("0x%x returns from an exception: %s %s" % (insn.address, insn.mnemonic, insn.op_str))
            loads = self._pop(insn, returning=True) if arch.stack_kind(insn) == "pop" else ""
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
            return self._call(callee, insn)
        if kind == "icall":
            if i != A.ARM_INS_BLX:
                raise Refused("0x%x: %s" % (insn.address, insn.mnemonic))
            return self._icall(insn, self.reg(ops[0].reg))
        if kind == "ijump":
            return self._switch_jump(insn)
        if kind == "stop":
            raise Refused("0x%x: %s" % (insn.address, insn.mnemonic))
        stack = arch.stack_kind(insn)
        if stack == "push":
            return self._push(insn)
        if stack == "pop":
            return self._pop(insn)
        if kind == "next" and self.sp_change(insn):
            return None                     # the stack pointer's depth is known statically
        lit = arch.literal(insn, isa)
        if lit is not None:
            return self._literal(insn, ops, *lit)
        at = arch.pc_address(insn, isa)
        if at is not None:
            return "%s = 0x%xu;" % (self.dest(ops[0]), at)
        handler = getattr(self, "op_" + OPS[i], None) if i in OPS else None
        if handler is None:
            raise Refused("0x%x: %s %s is not modelled" % (insn.address, insn.mnemonic, insn.op_str))
        return handler(insn, ops)

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
        out = []
        for k, r in enumerate(regs):
            if r == "pc":
                raise Refused("0x%x pushes pc" % insn.address)
            if r not in undef:
                out.append("*(volatile u32 *)(sp0 - %d) = %s;" % (d - 4 * k, r))
        return " ".join(out)

    def _pop(self, insn, returning=False):
        """The popped registers from the frame; when returning, only those that carry the result."""
        d = self.ctx["depth"][insn.address]
        keep = ({"r0", "r1"} if self._wide(self.ctx["fn"].name) else {"r0"}) if returning else None
        out = []
        for k, r in enumerate(self._regs_of(insn)):
            if r == "pc" or (keep is not None and r not in keep):
                continue
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
            if self.stack_args(callee) != 0:
                raise Refused("0x%x jumps to %s, which takes stack arguments" % (insn.address, callee.name))
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


def check(target, image, names=None, out=print, jobs=None):
    """The acceptance loop (see the module's docstring); returns the accepted names."""
    from dread import build, overlay, regflow, runner
    program = Program(target, image)
    lifter = Lifter(program, regflow.load(target, image, program))
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
    candidates, refused = [], 0
    for name in names or [fn.name for fn in program.functions]:
        fn = program.by_name.get(name)
        if fn is None or name in by_hand:
            continue
        refused += 1
        if program.stored(fn.entry) != fn.entry or not program.in_image(fn.entry):
            out("refused  %-24s runs from a copy in RAM (the overlay cannot place C there)" % name)
            continue
        if name in entered:
            out("refused  %-24s other code branches into its middle (the overlay cannot replace it)" % name)
            continue
        try:
            lifter.lift(fn)
            candidates.append(name)
            refused -= 1
        except Refused as e:
            out("refused  %-24s %s" % (name, e))
    callees = {n: direct_callees(program, program.by_name[n]) for n in candidates}
    accepted, retry, retried, rounds = list(candidates), [], False, 0
    while accepted:
        rounds += 1
        try:
            payload = build.build(target, image, out=work, lifted=accepted, quiet=True)
        except overlay.OverlayError as e:
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
    path = image.table("lifted")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("# Functions built from lifted C: their generated cases agree with the original and run\n"
                "# every instruction of it. Written by dread lift --check; not edited by hand.\n")
        f.write("".join(n + "\n" for n in sorted(accepted)))
    out("dread lift --check: %d accepted after %d rounds (%d written by hand, %d refused, %d dropped), into %s" % (
        len(accepted), rounds, len(by_hand & set(program.by_name)), refused, len(candidates) - len(accepted),
        os.path.relpath(path)))
    return accepted


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
    lifter = Lifter(program, regflow.load(target, image, program))
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
