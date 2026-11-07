"""dread lift for ARCompact (ARC600): one C statement per instruction, as dread.lift does for ARM.

Registers are u32 variables by the layer's names (r0-r25, gp, fp, blink, lp_count, r57-r59); pcl
and a long immediate read as constants, r61 and r62 as 0, and a write to a long immediate (no
destination) is dropped. Loads and stores are volatile accesses in the original's order, with .aw
(the base written before the access), .ab (after) and .as (the offset scaled by the access's size)
as the engine (dread.engines.arc) has them; branches are gotos. The stack pointer's depth is known
statically at every instruction (dread.lift's states: push_s and pop_s, st.aw rN,[sp,-4] and
ld.ab rN,[sp,4], any other write-back through sp, add and sub of sp with an immediate, mov sp from a
frame pointer), so an access at sp, or at a register that holds a frame address, is one into the
frame array; the words at the frame's top that only the prologue's saves and the epilogue's
restores touch are not in the array at all (dread.lift's saves_out). A call passes r0-r7 and takes
r0 (r0 and r1 when the callers read a 64-bit result); r1-r12 are 0 behind it unless the callee
never writes one (dread.regflow's preserved: libgcc's __udivmodsi4 keeps r7, in which __umodsi3
keeps its return address), and a function that reads lp_count, r57-r59 or a flag behind a call
before writing it is refused (C cannot hand them over). libgcc's arithmetic helpers (__mulsi3,
__udivmodsi4, ...) are functions like any other: their own registers are the convention's.

The flags are four u32 variables z, n, c and v (0 or 1), written only where the engine writes them
(.f, the compares, their 16-bit forms) and with its formulas: the additions and subtractions (add,
adc, sub, sbc, rsub, the scaled add1-3 and sub1-3, cmp, rcmp) set all four, C a carry for the
additions and a borrow (x < y unsigned; sbc: x < y + C) for the subtractions; max and min the flags
of b - c, C where c was chosen; the shifts by one (asl, asr, lsr, ror, rrc, rlc) Z, N and C, asl
also V; abs Z, N, C (the source's sign) and V (0x80000000); the barrel shifter's asl, lsr, asr and
ror Z, N and, where the count is not 0, C (the last bit out); every other operation Z and N. The
flags are followed one by one (flag_effects, ArcView): a flag read before anything writes it is
what the caller left, which the C reads from STATUS32 (lr) at its entry. Conditional instructions
(.eq and kin) are if statements over the conditions the engine has (COND).

Delay slots. A branch, jump or call written .d runs its slot first, on both ways of a conditional
branch; bl.d returns behind the slot. The analyses (states, the registers read before written, the
frame) take the pair in the order it runs: whatever leads to the branch leads to its slot, the slot
leads to the branch, and the branch to its targets and to what follows the slot (successors). The
C writes the pair where the branch is: the branch's condition (or a jump's register) into dc first,
which the slot may change, then the slot's statement, then the branch (`dc = !z; slot; if (dc) goto
L;`); a call's slot sets its arguments before it. Refused: a slot that something other than its
branch reaches (a jump into it, another instruction that falls into it), a slot that is itself a
branch, jump, call, lp or delay slot, a slot that writes the register a jump goes through, a
function that begins with a delayed branch.

Zero-overhead loops. lp sets LP_START (behind it) and LP_END (its target, loop_body); its C is
nothing, an lpcc whose condition fails `if (!COND) goto L_end;`. The body's last instruction (the
one LP_END follows) is followed by `if (--lp_count) goto L_start;`, as the engine counts down where
an instruction that does not branch is followed by LP_END: the analyses have that back edge
(successors). Refused where the C would be wrong: a body something other than its lp enters (a jump
into it from outside), a body that holds a call or another lp (the callee's own loop would move
LP_START and LP_END), a last instruction that is a branch, jump, call, delayed branch or delay
slot, two lp instructions with one body end and different starts. The L bit of STATUS32 (loops
off) is taken as clear, as it is out of reset and in every generated case (flag, which sets it, is
refused).

Gaps in the code: a switch's table of byte offsets inside the function (gizmo-arc's script_run)
leaves the import's linear decoding out of step behind it. The translator follows the flow from the
entry (walk), and where that reaches instructions the linear decoding does not have, the function
is lifted as a dread.lift Body of what the walk reaches (its cases cover those instructions).

Refused, with the reason: an instruction the engine has no semantics for (the extension
operations, asls and asrs, sat16, rnd16, abssw, negsw, mpyw, mpyuw: C multiplies through libgcc on
the ARC600) or that acts on the core (flag, lr, sr, sleep, brk, j.f [ilink1]: interrupt handlers
are not lifted yet), mul64 and mulu64 (C would call libgcc), ilink1 and ilink2 and the extension
core registers r32-r56, an extension condition (0x10-0x1f), stack arguments (ARC passes eight
arguments in registers), callers that read a register beyond r0 and r1 that the function sets (no
naked entry hands them back here), and what dread.lift refuses for every architecture (a stack
pointer that differs between paths or is not followed, a fall-through out of the function, a
register read before written).

Not confirmed: that GCC's ARC output never relies on LP_END staying set after a loop ends (the C's
own loops move it; generated cases call one function at a time).
"""
from dread import lift
from dread.arch.arc import CANON
from dread.lift import IDENT, Body, Refused

GENERAL = ["r%d" % i for i in range(26)] + ["gp", "fp"]
REGS = GENERAL + ["sp", "blink", "lp_count", "r57", "r58", "r59"]
VARS = [r for r in REGS if r != "sp"]               # the C's register variables
ARGS = tuple("r%d" % i for i in range(8))
SCRATCH = tuple("r%d" % i for i in range(13))
AFTER_CALL = set(SCRATCH) | {"blink", "flags", "lp_count", "r57", "r58", "r59"}
LEFT_BY_CALLS = ("lp_count", "r57", "r58", "r59", "flags:z", "flags:n", "flags:c", "flags:v")
M = 0xFFFFFFFF
SP, BLINK, LP_COUNT = 28, 31, 60

# The conditions on the flags variables (dread.engines.arc's COND_EXPR)
COND = {1: "z", 2: "!z", 3: "!n", 4: "n", 5: "c", 6: "!c", 7: "v", 8: "!v", 9: "(n == v && !z)",
        10: "(n == v)", 11: "(n != v)", 12: "(z || n != v)", 13: "(!c && !z)", 14: "(c || z)",
        15: "(!n && !z)"}
COND_FLAGS = {1: "z", 2: "z", 3: "n", 4: "n", 5: "c", 6: "c", 7: "v", 8: "v", 9: "znv", 10: "nv", 11: "nv",
              12: "znv", 13: "cz", 14: "cz", 15: "nz"}
BRCOND = {"eq": "{x} == {y}", "ne": "{x} != {y}", "lt": "(s32){x} < (s32){y}", "ge": "(s32){x} >= (s32){y}",
          "lo": "{x} < {y}", "hs": "{x} >= {y}"}
ARITH = ("add", "adc", "sub", "sbc", "rsub", "add1", "add2", "add3", "sub1", "sub2", "sub3", "cmp", "rcmp")
SCALE = {"add1": 1, "add2": 2, "add3": 3, "sub1": 1, "sub2": 2, "sub3": 3}
LOGIC = {"and": "x & y", "tst": "x & y", "or": "x | y", "xor": "x ^ y", "bic": "x & ~y",
         "bset": "x | (1u << (y & 31))", "bclr": "x & ~(1u << (y & 31))", "bxor": "x ^ (1u << (y & 31))",
         "btst": "x & (1u << (y & 31))", "bmsk": "x & ((2u << (y & 31)) - 1u)",
         "max": "((s32)x >= (s32)y ? x : y)", "min": "((s32)x <= (s32)y ? x : y)",
         "asl": "x << (y & 31)", "lsr": "x >> (y & 31)", "asr": "(u32)((s32)x >> (y & 31))",
         "ror": "((y & 31) ? (x >> (y & 31)) | (x << (32 - (y & 31))) : x)",
         "adds": "dread_sat((s64)(s32)x + (s32)y)", "subs": "dread_sat((s64)(s32)x - (s32)y)"}
SHIFT_OUT = {"asl": "(x >> (32 - (y & 31))) & 1", "lsr": "(x >> ((y & 31) - 1)) & 1",
             "asr": "(x >> ((y & 31) - 1)) & 1", "ror": "(x >> ((y & 31) - 1)) & 1"}
UNARY = {"asl1": "x << 1", "asr1": "(x >> 1) | (x & 0x80000000u)", "lsr1": "x >> 1",
         "ror1": "(x >> 1) | (x << 31)", "rrc": "(x >> 1) | (c << 31)", "rlc": "(x << 1) | c",
         "sexb": "(u32)(s32)(s8)x", "sexw": "(u32)(s32)(s16)x", "extb": "x & 0xffu", "extw": "x & 0xffffu",
         "abs": "((x & 0x80000000u) ? -x : x)", "not": "~x", "neg": "-x", "swap": "(x << 16) | (x >> 16)",
         "norm": "dread_norm(x)", "normw": "dread_normw(x)", "abss": "dread_sat((x & 0x80000000u) ? -(s64)(s32)x : (s64)x)",
         "negs": "dread_sat(-(s64)(s32)x)"}
TYPES = {(1, False): "u8", (1, True): "s8", (2, False): "u16", (2, True): "s16", (4, False): "u32", (4, True): "u32"}
CORE = ("flag", "lr", "sr", "sleep", "brk")
HELPERS = r"""static inline __attribute__((always_inline)) u32 dread_sat(s64 t)
{
    return t > 0x7fffffffLL ? 0x7fffffffu : t < -0x80000000LL ? 0x80000000u : (u32)t;
}
static inline __attribute__((always_inline)) u32 dread_norm(u32 x)
{
    u32 y = (x & 0x80000000u) ? ~x : x, k = 31;
    while (y) { y >>= 1; k--; }
    return k;
}
static inline __attribute__((always_inline)) u32 dread_normw(u32 x)
{
    u32 y = (x & 0x8000u) ? ~x & 0xffffu : x & 0xffffu, k = 15;
    while (y) { y >>= 1; k--; }
    return k;
}
"""


def cname(n):
    """The layer's name of core register n (r0-r25, gp, fp, sp, blink, lp_count, r57...)."""
    return CANON[n]


class _Inside:
    """The addresses of fn the walk may go to: a Body's own, else any instruction in its span."""

    def __init__(self, fn, prog):
        self.fn, self.prog = fn, prog
        self.own = set(fn.insns) if isinstance(fn, Body) else None

    def __contains__(self, x):
        if self.own is not None:
            return x in self.own
        return self.fn.entry <= x < self.fn.entry + self.fn.size and self.prog.insn(x) is not None


class ArcView:
    """The ARC layer as the translator's analyses see it: as the layer, with the flags one by one
    (flag_effects, FLAG_NAMES), which dread.lift's entry_states follows so (a logical .f writes Z
    and N and leaves C and V: the flags as one unit would take C as written there)."""

    FLAG_NAMES = ("z", "n", "c", "v")

    def __init__(self, arch):
        self._arch = arch

    def __getattr__(self, name):
        return getattr(self._arch, name)

    def flag_effects(self, insn):
        """(flags read, flags written), each a string of z, n, c, v (see the module)."""
        f = insn.fields
        op = f.op
        reads = ""
        if f.cond:
            reads += COND_FLAGS.get(f.cond, "zncv")
        if op in ("adc", "sbc", "rrc", "rlc"):
            reads += "c"
        if op == "lr":
            reads += "zncv"
        if op in ("flag",) or (op == "j" and f.setf):
            return set(reads), set("zncv")
        if not f.setf:
            return set(reads), set()
        if op in ARITH or op in ("max", "min", "abs", "asl1"):
            writes = "zncv"
        elif op in ("asr1", "lsr1", "ror1", "rrc", "rlc"):
            writes = "znc"
        elif op in SHIFT_OUT and len(f.srcs) == 2:
            y = f.srcs[1]
            writes = "znc" if y[0] == "i" and y[1] & 31 else "zn"     # (a count in a register: C may stay)
        else:
            writes = "zn"
        return set(reads), set(writes)


class ArcLifter(lift.Lifter):
    REGS, ARGS, AFTER_CALL, MORE = REGS, ARGS, AFTER_CALL, ()

    def __init__(self, program, flow=None, endless=None):
        super().__init__(program, flow, endless)
        self.arch = ArcView(program.arch)
        self._walks, self._walking = {}, {}

    # ---- the flow: delay slots and zero-overhead loops ----

    def _delayed(self, a):
        """Whether the instruction at a is a branch, jump or call written .d."""
        insn = self.prog.insn(a)
        return insn is not None and insn.fields.delay and insn.fields.op in ("b", "bl", "br", "bbit0", "bbit1",
                                                                              "j", "jl")

    def _redirect(self, a):
        """Where a path that goes to `a` goes in the order instructions run: to a delayed branch's slot."""
        return a + self.prog.insn(a).size if self._delayed(a) else a

    def _loop(self, lp):
        """(LP_START, LP_END, the body's last instruction) of the lp at address lp: the body is the
        straight run of instructions from LP_START up to LP_END. Refused when no instruction ends at
        LP_END."""
        start, end = self.arch.loop_body(self.prog.insn(lp))
        a, last = start, None
        while a < end:
            insn = self.prog.insn(a)
            if insn is None:
                break
            last, a = a, a + insn.size
        if a != end or last is None:
            raise Refused("0x%x: no instruction of the zero-overhead loop's body ends at its end (0x%x)" % (lp, end))
        return start, end, last

    def _loops(self, lps):
        """{the address of a loop body's last instruction: (lp's address, LP_START, LP_END)} of the
        lp instructions at the addresses lps."""
        out = {}
        for a in sorted(lps):
            start, end, last = self._loop(a)
            if last in out and out[last][1] != start:
                raise Refused("0x%x: two zero-overhead loops end at 0x%x from different starts" % (a, end))
            out[last] = (a, start, end)
        return out

    def _exec_succ(self, fn, inside, a, insn, strict, values, loops):
        """The addresses that may run after the instruction at a (not a delay slot) in the order the
        core runs them: behind a delayed branch its slot comes first, which successors puts in front."""
        f = insn.fields
        kind, target, falls = self.arch.flow(insn)
        if values is not None and self.ret_jump(insn, values):
            kind = "return"
        nxt = a + insn.size
        if self._delayed(a):
            nxt += self.prog.insn(nxt).size if self.prog.insn(nxt) is not None else 2
        out = []
        if f.op == "lp":
            start, end = self.arch.loop_body(insn)
            return [start] + ([end] if f.cond else [])
        if (kind in ("next", "call", "icall") or falls) and not self.dead_end(insn) and not (
                f.op == "j" and not f.cond and kind == "return"):
            if nxt in inside:
                out.append(nxt)
            elif kind == "next" and self.falls_into(fn, insn) is not None:
                pass                        # a tail call of the function it falls into (dread.lift)
            elif strict:
                raise Refused("0x%x falls through into what follows (data or other code; a call that does not "
                              "return?)" % a)
        if kind == "jump" and target is not None and target in inside:
            out.append(target)
        if kind == "ijump":
            got = self.resolve(fn, a)
            if got:
                out += [t for t in got[0] if t in inside]
        if a in loops:
            out.append(loops[a][1])
        return out

    def falls_into(self, fn, insn):
        """dread.lift's, except for a delay slot: its branch decides where it goes on."""
        walking = self._walking.get(getattr(fn, "key", fn.name))
        slots = walking if walking is not None else self._info(fn)[1]
        if insn.address in slots:
            return None
        return super().falls_into(fn, insn)

    def dead_end(self, insn):
        if insn.fields.cond:
            return None                     # (bl.eq: it may not run)
        return super().dead_end(insn)

    def walk(self, fn):
        """(the addresses a path from fn's entry reaches inside its span, {slot: its branch}, the loops)
        in the order the core runs them (see the module); Refused where a slot or a loop body is
        entered otherwise than the module says."""
        key = getattr(fn, "key", fn.name)
        if key in self._walks:
            got = self._walks[key]
            if isinstance(got, Refused):
                raise got
            return got
        try:
            got = self._walk(fn)
        except Refused as e:
            self._walks[key] = e
            raise
        finally:
            self._walking.pop(key, None)
        self._walks[key] = got
        return got

    def _walk(self, fn):
        prog = self.prog
        if self._delayed(fn.entry):
            raise Refused("0x%x: it begins with a delayed branch" % fn.entry)
        room = _Inside(fn, prog)
        reached, branch_of, direct, lps = set(), {}, {}, set()
        self._walking[getattr(fn, "key", fn.name)] = branch_of      # (falls_into, meanwhile)
        loops = {}
        work = [fn.entry]
        while work:
            a = work.pop()
            if a in reached:
                continue
            insn = prog.insn(a)
            if insn is None:
                continue
            reached.add(a)
            if a in branch_of:
                work.append(branch_of[a])
                continue
            if insn.fields.op == "lp" and a not in lps:
                lps.add(a)
                loops = self._loops(lps)
            for b in self._exec_succ(fn, room, a, insn, False, None, loops):
                if self._delayed(b):
                    s = b + prog.insn(b).size
                    if s not in room:
                        raise Refused("0x%x: the delay slot of the branch there lies outside the function" % b)
                    branch_of[s] = b
                    work.append(s)
                else:
                    direct.setdefault(b, a)
                    work.append(b)
        for s, b in sorted(branch_of.items()):
            if s in direct:
                raise Refused("0x%x, the delay slot of 0x%x, is reached from 0x%x too" % (s, b, direct[s]))
            slot = prog.insn(s)
            if self.arch.flow(slot)[0] != "next" or slot.fields.op == "lp" or self._delayed(s):
                raise Refused("0x%x: the delay slot of 0x%x is %s %s" % (s, b, slot.mnemonic, slot.op_str))
            branch = prog.insn(b)
            if branch.fields.op in ("j", "jl") and branch.fields.srcs[0][0] == "r":
                reg = cname(branch.fields.srcs[0][1])
                if reg in self.arch.reg_effects(slot)[1]:
                    raise Refused("0x%x: the delay slot of 0x%x writes %s, which the jump goes through" % (s, b, reg))
        for last, (at, start, end) in sorted(loops.items()):
            for b in sorted(x for x in reached if start <= x < end):
                insn = prog.insn(b)
                if self.arch.flow(insn)[0] in ("call", "icall") or insn.fields.op == "lp":
                    raise Refused("0x%x: the zero-overhead loop of 0x%x holds %s %s" % (
                        b, at, insn.mnemonic, insn.op_str))
            l_insn = prog.insn(last)
            if self.arch.flow(l_insn)[0] != "next" or last in branch_of or self._delayed(last):
                raise Refused("0x%x: the last instruction of the zero-overhead loop of 0x%x is %s %s"
                              % (last, at, l_insn.mnemonic, l_insn.op_str))
            for b, src in sorted(direct.items()):   # (entered only from lp and from its own body)
                if start <= b < end and not start <= src < end and src != at:
                    raise Refused("0x%x jumps into the zero-overhead loop of 0x%x" % (src, at))
            for s, b in branch_of.items():
                if start <= s < end and not start <= b < end:
                    raise Refused("0x%x jumps into the zero-overhead loop of 0x%x" % (b, at))
        return reached, branch_of, loops

    def _full(self, fn):
        """fn, or a Body of what the walk reaches where that holds instructions fn's linear decoding
        does not (a table of byte offsets inside the function left it out of step)."""
        if isinstance(fn, Body):
            return fn
        try:
            reached = self.walk(fn)[0]
        except Refused:
            return fn
        if reached - set(fn.insns):
            full = Body(fn, sorted(reached))
            full.key = fn.name + "+walked"
            return full
        return fn

    def entered_unseen(self, fn, lifted):
        """dread.lift's, without the bytes behind a switch's jump up to the next instruction a path
        reaches: its table (gizmo-arc's handle_key keeps its byte offsets there), which the linear
        decoding takes for instructions."""
        try:
            reached = sorted(self.walk(self._full(fn))[0])
        except Refused:
            return super().entered_unseen(fn, lifted)
        tables = []
        for i, a in enumerate(reached):
            insn = self.prog.insn(a)
            if self.arch.flow(insn)[0] == "ijump" and (self.resolve(fn, a) or ([], []))[0]:
                tables.append((a + insn.size, reached[i + 1] if i + 1 < len(reached) else a + insn.size))
        if not tables:
            return super().entered_unseen(fn, lifted)
        view = lift.Lifted(lifted.name, lifted.text, lifted.callees, lifted.frame, lifted.stack_params,
                           [a for a in lifted.unreached if not any(lo <= a < hi for lo, hi in tables)],
                           lifted.body, lifted.saves_out)
        return super().entered_unseen(fn, view)

    def _info(self, fn):
        try:
            return self.walk(fn)
        except Refused:
            return set(), {}, {}

    def successors(self, fn, index, address, insn, strict=True, values=None):
        """dread.lift's successors in the order instructions run: a delayed branch's predecessors
        lead to its slot, the slot to the branch, the branch to its targets and behind the slot; a
        zero-overhead loop's last instruction back to LP_START too; lp to LP_START (and, conditional,
        to LP_END)."""
        reached, branch_of, loops = self._info(fn)
        if address in branch_of:
            return [branch_of[address]]
        out = self._exec_succ(fn, index, address, insn, strict, values, loops)
        return [self._redirect(b) for b in out]

    def states(self, fn, strict=True, switch=False):
        return super().states(self._full(fn), strict, switch)

    def entry_states(self, fn, given=frozenset()):
        """dread.lift's, with what the order of a delayed pair hides: a register (or flag) the branch
        reads that its slot writes is read before the slot, and a zero-overhead loop's end reads
        lp_count."""
        fn = self._full(fn)
        undef, found = super().entry_states(fn, given)
        reached, branch_of, loops = self._info(fn)
        for s, b in branch_of.items():
            if s not in undef:
                continue
            br, sl = self.prog.insn(b), self.prog.insn(s)
            reads = set(self.arch.reg_effects(br)[0])
            writes = set(self.arch.reg_effects(sl)[1])
            hidden = (reads & writes & set(undef[s])) - {"sp"}
            if hidden:
                found.setdefault(s, set()).update(hidden)
        for last in loops:
            if last in undef and "lp_count" in undef[last]:
                found.setdefault(last, set()).add("lp_count")
        return undef, found

    # ---- save and restore "millicode" helpers ----
    # A compiler calls a shared routine to save the callee-saved registers it uses (GCC's
    # -mmillicode __st_r13_to_rNN, stores into a frame the caller reserved; a hand-written variant
    # pushes them) and tail-jumps to one that restores them, unwinds the frame and returns through
    # blink (__ld_r13_to_rNN_ret). _frame_helper analyses such a routine from an entry: a pure leaf
    # of stack stores (a "save") or stack loads (a "restore") and register moves, ending in a return.
    # A call to a save helper is treated as the saves (no scratch clobbered, the stack pointer moved
    # by what it pushes); a tail-jump to a restore helper as the function's epilogue (a return). The
    # saved words are the C's own frame's, laid out by its compiler and not compared (saves_out's
    # reason), so the C omits both. Facts come from the routine's code, not from its name (rule 2).

    def _frame_helper(self, entry):
        """("save", bytes pushed) or ("restore", None) of the frame helper at `entry`, else None
        (the ARC layer's frame_helper: analysed from the routine's code)."""
        return self.prog.arch.frame_helper(self.prog, entry)

    def _save_call(self, insn):
        """("save", depth pushed) when insn is an unconditional call of a save helper, else None."""
        kind, target, _ = self.arch.flow(insn)
        if kind != "call" or not isinstance(target, int) or insn.fields.cond:
            return None
        if self.prog.by_entry.get(target) is None:
            return None
        h = self._frame_helper(target)
        return h if h is not None and h[0] == "save" else None

    def _restore_jump(self, insn):
        """Whether insn is an unconditional tail-jump to a restore helper (the function's epilogue)."""
        kind, target, _ = self.arch.flow(insn)
        if kind != "jump" or not isinstance(target, int) or insn.fields.cond:
            return False
        if self.prog.by_entry.get(target) is None:
            return False
        h = self._frame_helper(target)
        return h is not None and h[0] == "restore"

    # ---- the stack pointer's depth and the frame (dread.lift's states) ----

    def ret_jump(self, insn, values):
        """j [rN] with the return address in rN (blink itself: flow calls that a return)."""
        f = insn.fields
        return (f.op == "j" and not f.setf and f.srcs and f.srcs[0][0] == "r" and f.srcs[0][1] != BLINK
                and values.get(cname(f.srcs[0][1])) == self.RET)

    def frame_value(self, number, depth, values):
        """("sp", k) when register `number` holds an address in the frame (sp itself included)."""
        if number == SP:
            return ("sp", depth) if depth is not None else None
        if number >= 61:
            return None
        v = values.get(cname(number))
        return v if isinstance(v, tuple) and v and v[0] == "sp" else None

    def _op_frame(self, op, depth, values):
        return self.frame_value(op[1], depth, values) if op[0] == "r" else None

    @staticmethod
    def _sp_move(f):
        """The bytes a load or store's write-back adds to sp (st.aw rN,[sp,-4]: -4), else None."""
        if f.op in ("ld", "st") and f.aa in (1, 2) and f.srcs[0] == ("r", SP) and f.srcs[1][0] == "i":
            return f.srcs[1][1]
        return None

    def _access(self, f, depth):
        """(base operand, displacement) of a load or store: where it accesses relative to its base
        before any write-back (None: an index register's value)."""
        base, off = f.srcs[0], f.srcs[1]
        disp = off[1] if off[0] == "i" else None
        if f.aa == 2:
            disp = 0                                # .ab: the access at the base, the offset added behind
        elif f.aa == 3 and off[0] == "i":
            disp = off[1] * f.width
        return base, disp

    def step(self, insn, depth, values):
        f = insn.fields
        after = dict(values)
        reads, writes = self.arch.reg_effects(insn)
        if f.op == "push":
            new = depth + 4
            self._put(after, new, values.get(cname(f.srcs[0][1])))
            return new, after
        if f.op == "pop":
            if f.dst is not None and f.dst != SP:
                self._put(after, cname(f.dst), values.get(depth))
            return depth - 4, after
        move = self._sp_move(f)
        save = self._save_call(insn)
        if save is not None:
            new = depth + save[1]           # the words the save helper pushes (0 for a reserved frame)
        elif move is not None:
            new = depth - move
        elif "sp" in writes:
            new = self.new_depth(insn, depth, values)
        else:
            new = depth
        known = self.made_value(insn, depth, values)
        slot = self.stored_slot(insn, depth, values)
        if self.arch.flow(insn)[0] in ("call", "icall"):
            writes = self.after_call(insn)  # (what the callee keeps keeps its value: dread.regflow)
        for r in writes:
            if r != "sp":
                after.pop(r, None)
        if f.op == "st":                    # (a store into the frame ends what the states knew of its words)
            base, disp = self._access(f, depth)
            v = self._op_frame(base, depth, values)
            if v is not None and disp is not None:
                at = v[1] - disp
                for k in [k for k in after if isinstance(k, int) and at - f.width < k < at + 4]:
                    del after[k]
        if known is not None:
            self._put(after, known[0], known[1])
        if slot is not None:
            self._put(after, slot[0], values.get(slot[1]) if slot[1] else None)
        return new, after

    def made_value(self, insn, depth, values):
        """(register, value): ("sp", k) for mov rd, sp or a frame address and add or sub rd of one and
        an immediate; a copy's value; what a word load from a known frame slot holds (a saved
        return address); ("const", value) for mov rd, imm and add of a constant and an immediate."""
        f = insn.fields
        if f.cond or f.dst is None or f.dst in (SP, 61, 62, 63) or f.op not in ("mov", "add", "sub", "ld"):
            return None
        rd = cname(f.dst)
        if f.op == "mov":
            src = f.srcs[0]
            if src[0] == "i":
                return rd, ("const", src[1] & M)
            v = self.frame_value(src[1], depth, values) if src[1] < 61 else None
            v = v or (values.get(cname(src[1])) if src[1] < 61 else None)
            return (rd, v) if v is not None else None
        if f.op in ("add", "sub") and len(f.srcs) == 2 and f.srcs[1][0] == "i":
            k = f.srcs[1][1] if f.op == "add" else -f.srcs[1][1]
            v = self._op_frame(f.srcs[0], depth, values)
            if v is not None:
                return rd, ("sp", v[1] - k)
            c = values.get(cname(f.srcs[0][1])) if f.srcs[0][0] == "r" and f.srcs[0][1] < 61 else None
            if isinstance(c, tuple) and len(c) == 2 and c[0] == "const":
                return rd, ("const", (c[1] + k) & M)
            return None
        if f.op == "ld" and f.width == 4 and not f.aa:
            base, disp = self._access(f, depth)
            v = self._op_frame(base, depth, values)
            if v is not None and disp is not None:
                return rd, values.get(v[1] - disp)
        return None

    def stored_slot(self, insn, depth, values):
        """(slot, register name or None) of a word store into the frame at a known place."""
        f = insn.fields
        if f.op != "st" or f.width != 4 or f.cond or f.aa:
            return None
        base, disp = self._access(f, depth)
        v = self._op_frame(base, depth, values)
        if v is None or disp is None:
            return None
        src = f.srcs[2]
        return v[1] - disp, cname(src[1]) if src[0] == "r" and src[1] < 61 else None

    def ldm_slots(self, insn, depth, values):
        return None

    def new_depth(self, insn, depth, values):
        """The depth after add or sub sp, sp or a frame address, imm and mov sp, rN (a frame pointer)."""
        f = insn.fields
        if f.cond:
            raise Refused("0x%x moves the stack pointer under a condition" % insn.address)
        if f.op in ("add", "sub") and f.dst == SP and len(f.srcs) == 2 and f.srcs[1][0] == "i":
            v = self._op_frame(f.srcs[0], depth, values)
            if v is not None:
                return v[1] - f.srcs[1][1] if f.op == "add" else v[1] + f.srcs[1][1]
        if f.op == "mov" and f.dst == SP and f.srcs[0][0] == "r":
            v = self._op_frame(f.srcs[0], depth, values)
            if v is not None:
                return v[1]
        if f.op == "ld" and f.dst == SP and f.srcs[0] != ("r", SP):
            raise lift.StackSwitch("0x%x loads the stack pointer from memory not on the stack (a context switch?): "
                                   "%s %s" % (insn.address, insn.mnemonic, insn.op_str))
        raise Refused("0x%x changes the stack pointer in a way not modelled: %s %s" % (
            insn.address, insn.mnemonic, insn.op_str))

    def saving_store(self, insn, values):
        """st rN into the frame: a save when rN still holds what the caller left."""
        f = insn.fields
        if f.op == "st" and f.width == 4 and values is not None and f.srcs[2][0] == "r":
            base = f.srcs[0]
            v = ("sp", 0) if base == ("r", SP) else (values.get(cname(base[1])) if base[0] == "r" else None)
            if isinstance(v, tuple) and v and v[0] == "sp":
                return cname(f.srcs[2][1])
        return None

    def plain_move(self, insn):
        f = insn.fields
        if f.op != "mov" or f.cond or f.setf or f.dst is None or f.srcs[0][0] != "r":
            return None
        d, s = f.dst, f.srcs[0][1]
        if d in (SP, 29, 30) or s in (SP, 29, 30) or d > LP_COUNT or s > LP_COUNT or 32 <= d < 57 or 32 <= s < 57:
            return None
        return cname(d), cname(s)

    def reads_above_entry(self, insn, depth, values):
        """How many words above the entry's stack pointer this instruction reads or writes through a
        frame address (sp when depth is known, or a register that holds one), 0 for none."""
        f = insn.fields
        if f.op == "pop":
            return 1 if depth is not None and depth <= 0 else 0
        if f.op not in ("ld", "st"):
            return 0
        base, disp = self._access(f, depth)
        v = self._op_frame(base, depth, values)
        if v is None or disp is None:
            return 0
        off = disp - v[1]
        return (off + f.width + 3) // 4 if off >= 0 else 0

    def stack_args(self, fn):
        """Words above the entry's stack pointer the function reads (its stack arguments): ARC passes
        eight arguments in registers, and the translation refuses the rest. [abi] stack_args
        overrides it."""
        if fn.name in self._stack_args:
            return self._stack_args[fn.name]
        declared = self.prog.target.abi.stack_args.get(fn.name)
        if declared is not None:
            self._stack_args[fn.name] = declared
            return declared
        self._stack_args[fn.name] = 0
        try:
            states = self.states(fn, strict=False, switch=True)
        except Refused:
            self._stack_args[fn.name] = None
            return None
        top = 0
        for a, (depth, values) in states.items():
            top = max(top, self.reads_above_entry(self.prog.insn(a), depth, values))
        self._stack_args[fn.name] = top
        return top

    # ---- the frame's top (dread.lift's saves_out) ----

    def results(self, fn, handler=None):
        if handler:
            return ()
        return ("r0", "r1") if self._wide(fn.name) else ("r0",)

    def frame_effects(self, fn, insn, depth, values, results, own):
        """dread.lift.FrameEffects of an ARC instruction: push_s and pop_s (and their st.aw and ld.ab
        forms) are whole-word stores and loads at sp (a pop of the return address the builtin), a word
        load into a register and a word store of one without write-back the same, other loads and
        stores accesses of their width (an index register: through it); calls, returns, tail calls
        and jumps through a register hand on what they pass."""
        f = insn.fields
        reads, writes = self.arch.reg_effects(insn)
        fx = lift.FrameEffects(reads, writes)
        kind, target, falls = self.arch.flow(insn)
        if f.op == "push":
            reg = cname(f.srcs[0][1])
            fx.accesses.append(("sp", None, -4, 4, "store", reg, False))
            fx.stored.add(reg)
        elif f.op == "pop":
            reg = cname(f.dst) if f.dst is not None else None
            fx.accesses.append(("sp", None, 0, 4, "load", reg, values.get(depth) == self.RET))
            if reg:
                fx.loaded.add(reg)
        elif f.op in ("ld", "st"):
            base, disp = self._access(f, depth)
            stack = self.arch.stack_kind(insn)
            if base[0] == "r" and base[1] < 61:
                bname = cname(base[1])
                off = f.srcs[1]
                idx = cname(off[1]) if off[0] == "r" and off[1] < 61 else None
                if disp is None and idx is None:
                    disp = 0                # (an index that reads 0: r61, r62)
                if stack == "push":
                    fx.accesses.append(("sp", None, -4, 4, "store", None, False))
                    fx.stored.add(cname(f.srcs[2][1]))
                    fx.accesses[-1] = ("sp", None, -4, 4, "store", cname(f.srcs[2][1]), False)
                elif stack == "pop":
                    reg = cname(f.dst) if f.dst is not None and f.dst < 61 else None
                    fx.accesses.append(("sp", None, 0, 4, "load", reg, values.get(depth) == self.RET))
                    if reg:
                        fx.loaded.add(reg)
                elif f.op == "ld":
                    whole = f.width == 4 and not f.aa and idx is None and f.dst is not None and f.dst < 61 \
                        and not f.cond
                    reg = cname(f.dst) if whole else None
                    made = self.made_value(insn, depth, values) if whole else None
                    fx.accesses.append((bname, idx, disp if idx is None else 0, f.width, "load" if whole else "other",
                                        reg, bool(made) and made[1] == self.RET))
                    if f.dst is not None and f.dst < 61:
                        fx.loaded.add(cname(f.dst))
                else:
                    whole = f.width == 4 and not f.aa and idx is None and not f.cond
                    src = f.srcs[2]
                    fx.accesses.append((bname, idx, disp if idx is None else 0, f.width,
                                        "store" if whole else "other", None, False))
                    if src[0] == "r" and src[1] < 61:
                        fx.stored.add(cname(src[1]))
            else:
                if f.op == "st" and f.srcs[2][0] == "r" and f.srcs[2][1] < 61:
                    fx.stored.add(cname(f.srcs[2][1]))
                if f.op == "ld" and f.dst is not None and f.dst < 61:
                    fx.loaded.add(cname(f.dst))
                off = f.srcs[1]
                if off[0] == "r" and off[1] < 61:       # ([limm, rN]: the index may be a frame address)
                    fx.accesses.append((cname(off[1]), None, 0, None, "other", None, False))
        callee = self.prog.by_entry.get(target) if isinstance(target, int) else None
        if kind == "call":
            fx.hands |= set(ARGS) | (set(self.leaves(callee)) if callee is not None else set())
        elif kind == "icall":
            fx.hands |= (set(ARGS) | set(reads)) - {"sp"}
        elif kind == "return" or self.ret_jump(insn, values):
            fx.hands |= set(results)
        elif kind == "jump" and self._restore_jump(insn):
            fx.hands |= set(results)                                 # the epilogue: a restore helper
        elif kind == "jump" and callee is not None and target not in own:
            fx.hands |= set(ARGS) | set(self.leaves(callee))         # a tail call
        elif kind == "ijump":
            fx.hands |= (set(ARGS) | set(reads)) - {"sp"}
        if kind in ("call", "icall"):
            fx.writes = (set(fx.writes) - set(AFTER_CALL)) | set(self.after_call(insn))
            fx.loaded |= set(self.after_call(insn))
        fx.opaque = f.op in CORE
        return fx

    # ---- what kind of function ----

    def lift(self, fn):
        self._wrapping = False
        n = self.stack_args(fn)
        if n:
            raise Refused("it takes %d stack words (arguments beyond the eighth)" % n)
        full = self._full(fn)
        self.walk(full)                     # (its refusals)
        return self._lift(full)

    def can_wrap(self, fn):
        return False

    def compiled_isa(self):
        return self.arch.isa

    def exception_return(self, insn):
        f = insn.fields
        return f.op == "j" and f.setf

    def handler_kind(self, fn):
        """"ilink1" (or "ilink2") when the function's returns are j.f [ilink1] (j.f [ilink2]): an
        interrupt handler of level 1 (2), whose C GCC's interrupt attribute gives the entry and the
        return (it saves what the C uses, and j.f restores STATUS32); else None. Refused: returns of
        both kinds, or to a caller as well."""
        if fn.name in self._handlers:
            return self._handlers[fn.name]
        returns = [i for i in self.prog.instructions(fn) if i is not None and self.arch.flow(i)[0] == "return"]
        traps = {"ilink1" if i.fields.srcs[0] == ("r", 29) else "ilink2" for i in returns if self.exception_return(i)}
        if traps and (len(traps) > 1 or any(not self.exception_return(i) for i in returns)):
            raise Refused("its returns differ in kind (j.f [ilink1], j.f [ilink2], j [blink])")
        kind = traps.pop() if traps else None
        self._handlers[fn.name] = kind
        return kind

    def _check_returns(self, fn, states, handler=None):
        """Refuse a return to anything but the return address (an interrupt handler's are GCC's)."""
        for a, (depth, values) in states.items():
            insn = self.prog.insn(a)
            if self.arch.flow(insn)[0] != "return" and not self.ret_jump(insn, values):
                continue
            if self.exception_return(insn):
                if handler:
                    if depth:
                        raise Refused("0x%x returns from the interrupt with %d bytes of frame" % (a, depth))
                    continue
                raise Refused("0x%x returns from an interrupt: %s %s" % (a, insn.mnemonic, insn.op_str))
            src = insn.fields.srcs[0]
            if values.get(cname(src[1])) != self.RET:
                raise Refused("0x%x returns to an address it computed, not to its caller" % a)

    def leaves(self, fn):
        """dread.lift's, except a frame helper reads none of its caller's registers for the C: the
        registers it saves are the caller's own, left out with the frame (saves_out)."""
        if self._frame_helper(fn.entry) is not None:
            return []
        return super().leaves(fn)

    def _wide(self, name):
        flow = self.flow
        return bool(flow and "r1" in flow.kept_regs(name) and "r1" not in flow.preserved_regs(name))

    def _check_kept(self, fn):
        """Refuse when callers read what C cannot hand back: the flags, or a register beyond r0 and r1
        that the function sets."""
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

    def _check_after_call(self, callee, insn, bind=False):
        """As the ARM translator's: a register the callee never writes keeps the caller's value; one
        it writes that the caller reads behind the call beyond the result is refused."""
        flow = self.flow
        if flow is None:
            return set()
        result = {"r0", "r1"} if self._wide(callee.name) else {"r0"}
        preserved = set(flow.preserved_regs(callee.name))
        undef = self.ctx["undef"].get(insn.address, frozenset())
        for reg in flow.kept_regs(callee.name):
            if reg in preserved and reg not in undef:
                continue
            if reg not in result and (self.ctx["fn"].name, insn.address) in flow.sites_of(callee.name, reg):
                raise Refused("0x%x reads %s behind the call of %s (what the callee leaves)" % (
                    insn.address, reg, callee.name))
        return set()

    def after_call(self, insn):
        """What a call leaves written: the scratch registers, blink, the flags, lp_count, r57-r59; a
        register the callee never writes (dread.regflow's preserved) keeps its value. A save helper
        preserves them all but blink (it only stores callee-saved registers and returns)."""
        if self._save_call(insn) is not None:
            return {"blink"}
        kind, target, _ = self.arch.flow(insn)
        callee = self.prog.by_entry.get(target) if kind == "call" and isinstance(target, int) else None
        if callee is not None and self.flow is not None:
            return self.AFTER_CALL - set(self.flow.preserved_regs(callee.name))
        return self.AFTER_CALL

    def _check_left_by_calls(self, fn, states):
        """Refuse a path that reads lp_count, r57-r59 or a flag behind a call before writing it: what
        the callee leaves there, which a C call does not hand over."""
        loops = self._info(fn)[2]
        index = {a: i for i, a in enumerate(fn.insns)}
        for a in states:
            insn = self.prog.insn(a)
            if self.arch.flow(insn)[0] not in ("call", "icall") or self._save_call(insn) is not None:
                continue
            work = [(b, frozenset(LEFT_BY_CALLS)) for b in self.successors(fn, index, a, insn, False, states[a][1])]
            seen = set()
            while work:
                b, pending = work.pop()
                if (b, pending) in seen or b not in states:
                    continue
                seen.add((b, pending))
                i = self.prog.insn(b)
                reads, writes = self.arch.reg_effects(i)
                fr, fw = self.arch.flag_effects(i)
                reads = (set(reads) - {"flags"}) | {"flags:" + x for x in fr} | ({"lp_count"} if b in loops else set())
                writes = (set(writes) - {"flags"}) | {"flags:" + x for x in fw}
                bad = reads & pending
                if bad:
                    raise Refused("0x%x reads %s behind the call at 0x%x (what the callee leaves)" % (
                        b, ", ".join(sorted(bad)).replace("flags:", "flag "), a))
                kind = self.arch.flow(i)[0]
                if kind == "return" or self.ret_jump(i, states[b][1]):
                    continue
                if not i.fields.cond:
                    pending = pending - writes
                if not pending:
                    continue
                work += [(x, pending) for x in self.successors(fn, index, b, i, False, states[b][1])]

    # ---- the translation ----

    def _lift(self, fn):
        states = self.states(fn)
        self._check_left_by_calls(fn, states)
        return super()._lift(fn)

    def _switches(self, fn):
        """dread.lift's switches, and the labels of the loops' starts."""
        super()._switches(fn)
        for last, (at, start, end) in self._info(fn)[2].items():
            self.ctx["targets"].update((start, end))
        for a in self.ctx["switch"]:
            check = self.ctx["switch"][a][0]
            if check in self._info(fn)[1]:
                raise Refused("0x%x: the bound check of the switch at 0x%x is a delay slot" % (check, a))

    def src(self, op, sp=False):
        """The C expression of a source operand: a constant, pcl's value, 0 for r61 and r62, a
        register's variable; sp only where `sp` allows it (an address)."""
        if op[0] == "i":
            return "0x%xu" % (op[1] & M)
        n = op[1]
        if n == 63:
            return "0x%xu" % (self.insn_now.address & ~3)
        if n in (61, 62):
            return "0u"
        if n == SP:
            if not sp:
                raise Refused("0x%x computes with the stack pointer: %s %s" % (
                    self.insn_now.address, self.insn_now.mnemonic, self.insn_now.op_str))
            return "(sp0 - %d)" % self.ctx["depth"][self.insn_now.address]
        if n in (29, 30) or 32 <= n < 57:
            raise Refused("0x%x uses %s" % (self.insn_now.address, cname(n)))
        return cname(n)

    def dest(self, n):
        """The C variable a result goes to, or None (no destination, a long immediate, pcl)."""
        if n is None or n >= 61:
            return None
        if n == SP:
            return None                     # (a frame adjustment: states follows it)
        if n in (29, 30) or 32 <= n < 57:
            raise Refused("0x%x writes %s" % (self.insn_now.address, cname(n)))
        return cname(n)

    def _set(self, n, expr):
        d = self.dest(n)
        return "%s = %s;" % (d, expr) if d else ""

    def insn(self, insn):
        """The C of one instruction; for a delayed branch, of the pair (see the module); for its
        slot nothing (the branch has it)."""
        self.insn_now = insn
        a = insn.address
        info = self._info(self.ctx["fn"])
        if a in info[1]:
            return None                     # a delay slot: its C is its branch's
        if self._delayed(a):
            slot = self.prog.insn(a + insn.size)
            return self._pair(insn, slot)
        text = self._one(insn)
        if a in info[2]:
            # (the barrier keeps GCC from replacing the loop by its final values: where it can count
            # the rounds, it computes them with a multiplication, which the ARC600 makes in libgcc)
            text = (text + " " if text else "") + \
                '__asm__ ("" : "+r"(lp_count)); if (--lp_count) goto L_%x;' % info[2][a][1]
        return text

    def _pair(self, branch, slot):
        """A delayed branch, jump or call and its slot: the condition first, then the slot, then the
        branch."""
        self.insn_now = branch
        cond = self._branch_cond(branch)
        pre = ""
        if cond is not None:
            pre, cond = "dc = %s; " % cond, "dc"
        self.insn_now = slot
        body = self._one(slot) or ""
        self.insn_now = branch
        go = self._branch(branch, cond)
        return "%s%s%s" % (pre, body + " " if body else "", go)

    def _branch_cond(self, insn):
        """The C condition a branch, jump or call goes under (None: always)."""
        f = insn.fields
        if f.op in ("br",):
            x, y = self.src(f.srcs[0]), self.src(f.srcs[1])
            return "(%s)" % BRCOND[f.brcond].format(x=x, y=y)
        if f.op in ("bbit0", "bbit1"):
            x, y = self.src(f.srcs[0]), self.src(f.srcs[1])
            bit = "((%s >> (%s & 31)) & 1)" % (x, y)
            return bit if f.op == "bbit1" else "!%s" % bit
        if f.cond:
            return self._cond(f.cond)
        return None

    def _cond(self, cond):
        if cond not in COND:
            raise Refused("0x%x: an extension condition (%d)" % (self.insn_now.address, cond))
        return COND[cond]

    def _one(self, insn):
        """The C of an instruction that is not a delayed pair: a branch with its own condition, or any
        other instruction (under its condition, if it has one)."""
        f = insn.fields
        if f.op in ("b", "bl", "br", "bbit0", "bbit1", "j", "jl"):
            return self._branch(insn, self._branch_cond(insn))
        if f.op == "lp":
            start, end = self.arch.loop_body(insn)
            if f.cond:
                return "if (!%s) goto L_%x;" % (self._cond(f.cond), end)
            return None
        text = self._plain(insn)
        if f.cond and text:
            # (the barrier hides that the condition is 0 or 1: GCC turns `if (c) r0 += r1` into
            # r0 + c * r1, which the ARC600 multiplies in libgcc)
            return '{ u32 k = %s; __asm__ ("" : "+r"(k)); if (k) { %s } }' % (self._cond(f.cond), text)
        if f.cond:
            self._cond(f.cond)
        return text

    def _branch(self, insn, cond):
        """A branch, jump, call or return going under the C condition cond (None: always)."""
        f = insn.fields
        a = insn.address
        kind, target, falls = self.arch.flow(insn)
        values = self.values()
        depth = self.ctx["depth"]
        guard = (lambda s: "if (%s) { %s }" % (cond, s)) if cond else (lambda s: s)
        if kind == "return" or self.ret_jump(insn, values):
            if self.exception_return(insn) and not self.ctx.get("handler"):
                raise Refused("0x%x returns from an interrupt: %s" % (a, insn.mnemonic))
            return guard(self._return())
        if kind == "jump":
            if target in depth:
                return "if (%s) goto L_%x;" % (cond, target) if cond else "goto L_%x;" % target
            if self._restore_jump(insn):            # the epilogue: restore helper, unwind, return
                return guard(self._return())
            callee = self.prog.by_entry.get(target)
            if callee is None:
                raise Refused("0x%x jumps into other code (0x%x)" % (a, target))
            return guard("%s %s" % (self._call(callee, insn), self._return()))
        if kind == "call":
            if self._save_call(insn) is not None:   # a save helper: its stores are the C's omitted saves
                return ""
            callee = self.prog.by_entry.get(target)
            if callee is None:
                raise Refused("0x%x calls into the middle of something (0x%x)" % (a, target))
            text = self._call(callee, insn)
            if self.never_returns(callee.name):
                text += " for (;;) { }"
            return guard(text)
        if kind == "icall":
            return guard(self._icall(insn, "(%s)" % self.src(f.srcs[0])))
        if kind == "ijump":
            if cond:
                raise Refused("0x%x: a conditional jump through a register" % a)
            return self._switch_jump(insn)
        raise Refused("0x%x: %s %s is not modelled" % (a, insn.mnemonic, insn.op_str))

    # -- what does not branch --

    STATUS32 = 0x0A

    def _status(self, insn):
        """The C of lr/flag/sr acting on STATUS32: an interrupt-atomic critical section saves it
        (lr), clears its interrupt-enable bits and restores it (flag/sr). The flags are the lifter's
        z/n/c/v variables; st32 holds STATUS32's other low bits (H, E1, E2), 0 at a case's entry
        where interrupts are off and never fire, so the enable bits are inert in a case run and only
        the flags are compared. lr reads them, flag updates the flags and the enable bits (its mask
        0xf07), sr writes STATUS32 whole. lr/sr of any other auxiliary register (a device) is not
        modelled."""
        f = insn.fields
        self.ctx["status_word"] = True
        pack = "(st32 | ((u32)z << 11) | ((u32)n << 10) | ((u32)c << 9) | ((u32)v << 8))"
        if f.op == "lr":
            if f.aux != ("i", self.STATUS32):
                raise Refused("0x%x: lr of an auxiliary register other than status32" % insn.address)
            return self._set(f.dst, pack)
        src = self.src(f.srcs[0])
        if f.op == "flag":              # masks to 0xf07: the flags (bits 8-11) and H, E1, E2 (0-2)
            return ("{ u32 fl = %s; z = fl >> 11 & 1; n = fl >> 10 & 1; c = fl >> 9 & 1; v = fl >> 8 & 1; "
                    "st32 = (st32 & ~7u) | (fl & 7u); }" % src)
        if f.aux != ("i", self.STATUS32):   # sr
            raise Refused("0x%x: sr to an auxiliary register other than status32" % insn.address)
        return ("{ u32 s = %s; z = s >> 11 & 1; n = s >> 10 & 1; c = s >> 9 & 1; v = s >> 8 & 1; "
                "st32 = s & ~0xf00u; }" % src)

    def _plain(self, insn):
        f = insn.fields
        op, a = f.op, insn.address
        if op in ("lr", "flag", "sr"):
            return self._status(insn)
        if op in ("unknown", "ext") or op in CORE:
            raise Refused("0x%x: %s %s is not modelled" % (a, insn.mnemonic, insn.op_str))
        if op == "nop":
            return None
        if op in ARITH:
            return self._arith(f)
        if op in LOGIC:
            return self._logic(f)
        if op == "mov":
            if f.dst == SP:
                return None                 # (a frame adjustment: states follows it)
            src = self.src(f.srcs[0], sp=True)
            out = self._set(f.dst, src)
            if f.setf:
                out = "{ u32 r = %s; %s z = r == 0; n = r >> 31; }" % (src, self._set(f.dst, "r"))
            return out
        if op in UNARY:
            return self._unary(f)
        if op == "push":
            k = self.ctx["depth"][a] + 4
            if self._left_out(k):
                return None                 # a save into a word left out (saves_out)
            return "*(volatile u32 *)(sp0 - %d) = %s;" % (k, self.src(f.srcs[0]))
        if op == "pop":
            k = self.ctx["depth"][a]
            if self._left_out(k) or self.dest(f.dst) is None:
                return None                 # a restore nothing reads, from a word left out (saves_out)
            return self._set(f.dst, "*(volatile u32 *)(sp0 - %d)" % k)
        if op in ("ld", "st"):
            return self._memory(f)
        raise Refused("0x%x: %s %s is not modelled" % (a, insn.mnemonic, insn.op_str))

    def _arith(self, f):
        """add, adc, sub, sbc, rsub, the scaled forms, cmp and rcmp: the engine's formulas."""
        op = f.op
        first, second = f.srcs[0], f.srcs[1]
        if op in ("rsub", "rcmp"):
            first, second = second, first
        sp_ok = op in ("add", "sub") and not f.setf
        if sp_ok and f.dst == SP:
            return None                     # sub sp, sp, N and kin: states follows them
        x, y = self.src(first, sp=sp_ok), self.src(second, sp=sp_ok)
        k = SCALE.get(op, 0)
        if k:
            y = "(%s << %d)" % (y, k)
        add = op in ("add", "adc", "add1", "add2", "add3")
        if add:
            res = "x + y" + (" + c" if op == "adc" else "")
        else:
            res = "x - y" + (" - c" if op == "sbc" else "")
        dst = self._set(f.dst, "r") if op not in ("cmp", "rcmp") else ""
        if not f.setf:
            if not dst:
                return None
            if op not in ("adc", "sbc") and not k:
                return self._set(f.dst, "%s %s %s" % (x, "+" if add else "-", y))
            return "{ u32 x = %s, y = %s, r = %s; %s }" % (x, y, res, dst)
        if add:
            carry = "(u32)(((u64)x + y%s) >> 32)" % (" + c" if op == "adc" else "")
            over = "((x ^ r) & (y ^ r)) >> 31"
        else:
            carry = "(u32)((u64)x < (u64)y%s)" % (" + c" if op == "sbc" else "")
            over = "((x ^ y) & (x ^ r)) >> 31"
        return ("{ u32 x = %s, y = %s, r = %s, nc = %s; %s z = r == 0; n = r >> 31; c = nc; v = %s; }"
                % (x, y, res, carry, dst, over))

    def _logic(self, f):
        """The logical and bit operations, max and min, the barrel shifter and adds and subs."""
        op = f.op
        x, y = self.src(f.srcs[0]), self.src(f.srcs[1])
        if op in ("adds", "subs"):
            self.ctx["helpers"] = True
        res = LOGIC[op]
        dst = self._set(f.dst, "r") if op not in ("tst", "btst") else ""
        if not f.setf:
            if not dst:
                return None
            return "{ u32 x = %s, y = %s, r = %s; %s }" % (x, y, res, dst)
        flags = "z = r == 0; n = r >> 31;"
        if op in ("max", "min"):
            flags = ("{ u32 d = x - y; z = d == 0; n = d >> 31; v = ((x ^ y) & (x ^ d)) >> 31; "
                     "c = r == y && x != y; }")
        elif op in SHIFT_OUT:
            if f.srcs[1][0] == "i":
                if f.srcs[1][1] & 31:
                    flags += " c = %s;" % SHIFT_OUT[op]
            else:
                flags += " if (y & 31) c = %s;" % SHIFT_OUT[op]
        return "{ u32 x = %s, y = %s, r = %s; %s %s }" % (x, y, res, dst, flags)

    def _unary(self, f):
        """The single-operand operations (asl, asr, lsr, ror by one, rrc, rlc, the extensions, abs,
        not, neg, swap, norm, the saturating ones)."""
        op = f.op
        if op in ("norm", "normw", "abss", "negs"):
            self.ctx["helpers"] = True
        x = self.src(f.srcs[0])
        dst = self._set(f.dst, "r")
        if not f.setf:
            if not dst:
                return None
            return "{ u32 x = %s, r = %s; %s }" % (x, UNARY[op], dst)
        flags = "z = r == 0; n = r >> 31;"
        if op in ("asl1",):
            flags += " c = x >> 31; v = ((x ^ r) >> 31) & 1;"
        elif op in ("asr1", "lsr1", "ror1", "rrc"):
            flags += " c = x & 1;"
        elif op == "rlc":
            flags += " c = x >> 31;"
        elif op == "abs":
            flags += " c = x >> 31; v = x == 0x80000000u;"
        return "{ u32 x = %s, r = %s; %s %s }" % (x, UNARY[op], dst, flags)

    def _memory(self, f):
        """ld and st: the address (with .aw, .ab, .as), the access, the base's write-back, the load's
        destination (behind the write-back: a destination that is the base takes the loaded value,
        as the engine has it)."""
        a = self.insn_now.address
        depth = self.ctx["depth"][a]
        base, off = f.srcs[0], f.srcs[1]
        ctype = TYPES[(f.width, f.sext and f.op == "ld")]
        on_sp = base == ("r", SP)
        if on_sp:
            # the frame: its words below the entry's stack pointer (the depth is static)
            if off[0] != "i":
                raise Refused("0x%x indexes the stack pointer with a register" % a)
            if f.aa == 1:
                k = depth - off[1]
            elif f.aa == 3:
                k = depth - off[1] * f.width
            else:
                k = depth if f.aa == 2 else depth - off[1]
            if k <= 0:
                raise Refused("0x%x reads or writes above its entry's stack pointer (stack arguments)" % a)
            whole = f.width == 4 and not f.cond
            if whole and self._left_out(k):
                return None                 # a save or a restore of a word left out (saves_out)
            address = "(sp0 - %d)" % k
            wb = None
        else:
            b = self.src(base)
            writable = base[0] == "r" and base[1] < 61
            o = self.src(off) if off[0] == "r" else "0x%xu" % (off[1] & M)
            if f.aa == 3:
                shift = {4: 2, 2: 1, 1: 0}[f.width]
                address = "%s + (%s << %d)" % (b, o, shift) if shift else "%s + %s" % (b, o)
                wb = None
            elif f.aa == 1 and writable:
                address, wb = "%s + %s" % (b, o), "%s = A;" % b
            elif f.aa == 2 and writable:
                address, wb = b, "%s = A + %s;" % (b, o)
            else:
                address, wb = "%s + %s" % (b, o), None
            if off == ("i", 0) and f.aa != 2:
                address = b
            if base[0] == "i" and off[0] == "i":
                address = "0x%xu" % ((base[1] + (off[1] * ({4: 4, 2: 2, 1: 1}[f.width] if f.aa == 3 else 1))) & M)
            if f.op == "ld" and base == ("r", 63) and off[0] == "i":
                lit = ((a & ~3) + off[1]) & M        # a literal: a constant of the image at lift time
                value = int.from_bytes(bytes(self.prog.read(lit, f.width)), "little")
                if f.sext and f.width < 4 and value & (1 << (8 * f.width - 1)):
                    value -= 1 << (8 * f.width)
                return self._set(f.dst, "0x%xu" % (value & M))
        if f.op == "ld":
            load = "*(volatile %s *)(%s)" % (ctype, "A" if wb else address)
            value = "(u32)(s32)%s" % load if ctype in ("s8", "s16") else "(u32)%s" % load if ctype != "u32" else load
            dst = self._set(f.dst, "t" if wb else value)
            if not wb:
                return dst or "(void)%s;" % value
            return "{ u32 A = %s, t = %s; %s %s }" % (address, value, wb, dst)
        v = self.src(f.srcs[2])
        store = "*(volatile %s *)(%s) = %s;" % (ctype, "A" if wb else address,
                                              v if ctype == "u32" else "(%s)%s" % (ctype, v))
        if not wb:
            return store
        return "{ u32 A = %s; %s %s }" % (address, store, wb)

    # -- calls and returns --

    def _ret(self):
        if self.ctx.get("handler"):
            return ""
        return "((u64)r1 << 32 | r0)" if self._wide(self.ctx["fn"].name) else "r0"

    def _return(self):
        return "return %s;" % self._ret() if self._ret() else "return;"

    def _call(self, callee, insn, bind=False):
        if not IDENT.match(callee.name):
            raise Refused("0x%x calls %s, whose name is not a C identifier" % (insn.address, callee.name))
        nstack = self.stack_args(callee)
        if nstack is None:
            raise Refused("0x%x calls %s, whose stack arguments are not known" % (insn.address, callee.name))
        if nstack:
            raise Refused("0x%x calls %s, which takes stack arguments" % (insn.address, callee.name))
        leaves = self.leaves(callee)
        if leaves:
            raise Refused("0x%x calls %s, which reads %s its caller leaves" % (
                insn.address, callee.name, ", ".join(leaves)))
        self._check_after_call(callee, insn)
        wide = self._wide(callee.name)
        self.ctx["callees"].add((callee.name, 0, wide))
        call = "%s(%s)" % (callee.name, ", ".join(ARGS))
        keep = set(self.flow.preserved_regs(callee.name)) if self.flow else set()
        cleared = [r for r in SCRATCH if r not in (("r0", "r1") if wide else ("r0",)) and r not in keep]
        clear = " = ".join(cleared) + " = 0;" if cleared else ""
        if wide:
            return "{ u64 t = %s; r0 = (u32)t; r1 = (u32)(t >> 32); } %s" % (call, clear)
        return "r0 = %s; %s" % (call, clear)

    def _icall(self, insn, fp):
        clear = " = ".join(r for r in SCRATCH if r != "r0") + " = 0;"
        return "r0 = ((u32 (*)(%s))%s)(%s); %s" % (", ".join(["u32"] * len(ARGS)), fp, ", ".join(ARGS), clear)

    def _jump_register(self, insn):
        """The C variable of the register j goes through (not blink), or None."""
        f = insn.fields
        if f.op == "j" and f.srcs[0][0] == "r" and f.srcs[0][1] not in (BLINK, SP) and f.srcs[0][1] < 61:
            return cname(f.srcs[0][1])
        return None

    def _entries_tail(self, insn, got):
        """A jump through a register whose every target the runs found is another function's entry
        (gizmo's dispatch: a table of handlers, jumped to with the frame gone): a tail call through
        the register, as dread.lift's _indirect_tail has it. None where that does not hold: the frame
        is not gone, a target reads registers its caller leaves or takes stack arguments, the callers
        read r1 and a target does not set it."""
        callees = [self.prog.by_entry.get(t) for t in got[1]] if got and not got[0] else []
        pointer = self._jump_register(insn)
        if not callees or not all(callees) or pointer is None or self.ctx["depth"][insn.address]:
            return None
        if any(self.leaves(c) or self.stack_args(c) != 0 for c in callees):
            return None
        wide = any(self._wide(c.name) for c in callees)
        if self._wide(self.ctx["fn"].name) and not all(self._wide(c.name) for c in callees):
            return None
        call = "((%s (*)(%s))%s)(%s)" % ("u64" if wide else "u32", ", ".join(["u32"] * len(ARGS)), pointer,
                                         ", ".join(ARGS))
        if self._wide(self.ctx["fn"].name):
            return "return %s;" % call
        return "return (u32)%s;" % call

    def _switch_jump(self, insn):
        got = self.ctx["switch"].get(insn.address)
        if got is None:
            found = self.resolve(self.ctx["fn"], insn.address)
            through = self._pointer_tail(insn, found) or self._entries_tail(insn, found)
            if through is not None:
                return through
            raise Refused("0x%x jumps through a register to targets not known" % insn.address)
        check, reg, values = got
        cases = " ".join("case %d: goto L_%x;" % (k, t) for k, t in sorted(values.items()))
        return "switch (index) { %s default: %s }" % (cases, self._return())

    def render(self, fn, body):
        ctx = self.ctx
        lines = ["/* %s at 0x%x, lifted by dread lift: one statement per instruction. */" % (fn.name, fn.entry),
                 '#include "dread.h"']
        for name, nstack, wide in sorted(ctx["callees"]):
            lines.append("extern %s %s(%s);" % ("u64" if wide else "u32", name, ", ".join(["u32"] * len(ARGS))))
        if ctx.get("helpers"):
            lines.append(HELPERS)
        if ctx.get("handler"):              # GCC writes the entry and j.f [ilink1]
            lines.append('void __attribute__((interrupt("%s"))) %s(void)' % (ctx["handler"], fn.name))
            lines.append("{")
            init = ["%s = 0" % r for r in VARS]
        else:
            params = ["u32 p%d" % i for i in range(len(ARGS))]
            lines.append("%s %s(%s)" % ("u64" if self._wide(fn.name) else "u32", fn.name, ", ".join(params)))
            lines.append("{")
            init = ["blink = (u32)__builtin_return_address(0)" if r == "blink" else
                    "%s = p%d" % (r, ARGS.index(r)) if r in ARGS else "%s = 0" % r for r in VARS]
        lines.append("    u32 %s;" % ", ".join(init))
        lines.append("    u32 z = 0, n = 0, c = 0, v = 0, dc = 0;")
        if ctx.get("status_word"):          # STATUS32's low bits (H, E1, E2), 0 at a case's entry
            lines.append("    u32 st32 = 0;")
        lines += self.fixed_lines()
        if ctx.get("flags_in"):             # read before written: the caller's flags, from STATUS32
            lines.append('    { u32 t; __asm__ volatile ("lr %0,[status32]" : "=r"(t)); z = t >> 11 & 1; '
                         'n = t >> 10 & 1; c = t >> 9 & 1; v = t >> 8 & 1; }')
        frame = (ctx["frame"] + 3) & ~3
        k = (frame - ctx["out"]) // 4 if ctx["out"] < frame else 0
        lines.append("    u32 frame[%d] __attribute__((aligned(4)));" % max(1, k))
        lines.append("    u32 sp0 = (u32)&frame[%d]%s;" % (k, " + %d" % ctx["out"] if ctx["out"] else ""))
        if ctx["switch"]:
            lines.append("    u32 index = 0;")
        lines.append("    %s (void)z; (void)n; (void)c; (void)v; (void)dc; (void)sp0;%s"
                     % (" ".join("(void)%s;" % r for r in VARS), " (void)st32;" if ctx.get("status_word") else ""))
        lines += body
        lines.append("}")
        return "\n".join(lines) + "\n"
