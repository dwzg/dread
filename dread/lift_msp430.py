"""dread lift for the 16-bit MSP430: one C statement per instruction, as dread.lift does for ARM.

Registers r4-r15 are u16 variables. The stack pointer's depth below the entry's is known statically
at every instruction (dread.lift's states: push and pop, sub #N, sp and add #N, sp, @sp+, a call's
return address, which the callee's return takes off again), so sp as an operand is the C expression
of that address in a frame array, and an access through it lands there. The flags are four variables
c, z, n and v, computed as the engine (dread.engines.msp430) and TI's guide say: add, addc, sub, subc
and cmp set all four; and, bit and sxt set C to not Z and clear V; xor sets V when both operands are
negative; rrc and rra shift into C and clear V; dadd sets C, Z and N and leaves V. The conditional
jumps test them. The flags are 0 or 1, made without a shift: what GCC would compute with libgcc's shift
routines (a sign bit, x != 0, swpb, rra, rrc, dadd) is the instruction itself in inline assembly
(HELPERS), since a case that stubs such a routine would answer the C's own calls. Memory accesses are volatile and in the original's order: the source's (with
@rN+'s increment behind it), then the destination's address and value, then its store. A word access
at an odd address stays one in the C (the hardware ignores bit 0 either way).

The return address lies in the slot the call pushed (depth 0): ret is a return from there, and a
branch to one of GCC's shared epilogues (br #__mspabi_func_epilog_N: pops that fall into one another,
then ret) is a return at the depth of the registers it pops. A call passes r12-r15 and takes r12, or
r12 and r13, or r12-r15 when the callers read a wider result (dread.regflow); r11, the other result
registers and the flags are 0 behind it. A call through a register that holds a known constant
(mov #uart_puts, r10; call r10) is a call of that function. An instruction that reads or writes sr
beyond its flags (dint, eint, the low-power modes, push sr, mov r15, sr) runs on the real register
in inline assembly, the flags merged in and taken back. A function that returns with reti is an
interrupt handler: GCC's interrupt attribute writes its entry and its return.

Refused, with the reason: a write of sp that is not a frame adjustment the analysis follows, an
access at or above the entry's stack pointer (the return address, stack arguments), a jump through
a register (a switch table: not yet), a call of a function that takes stack arguments, MSP430X, and
what dread.lift refuses for every architecture (a stack pointer that differs between paths, a
fall-through out of the function, a register read before written).
"""
from dread import lift
from dread.arch.msp430 import NAMES
from dread.lift import IDENT, Refused

REGS = ["r%d" % i for i in range(4, 16)]     # sr is no C variable: a read of it is the real register
ARGS = ("r12", "r13", "r14", "r15")
SCRATCH = ("r11", "r12", "r13", "r14", "r15")
AFTER_CALL = set(SCRATCH) | {"flags"}
JUMPS = {"jne": "!z", "jeq": "z", "jlo": "!c", "jhs": "c", "jn": "n", "jge": "n == v", "jl": "n != v"}
# What GCC would make into calls of libgcc runs as the instructions themselves, in inline assembly. The
# MSP430 has no barrel shifter: GCC computes a sign bit (x >> 15), x != 0 ((x | -x) >> 15), a byte swap
# and two rotations in a row (merged into one shift by two) with __mspabi_srli and __mspabi_slli. A case
# that stubs such a helper (one a run hung in, deeper in the call tree) then answers the C's own calls
# too: gizmo's script_run looped forever so. The flags are 0 or 1; dadd runs with C set from c first.
HELPERS = r"""static inline __attribute__((always_inline)) u16 dread_sign16(u16 x)
{
    u16 r;
    __asm__ ("mov %1, %0\n\trla %0\n\tclr %0\n\tadc %0" : "=&r"(r) : "r"(x));
    return r;
}
static inline __attribute__((always_inline)) u16 dread_sign8(u16 x)
{
    u16 r;
    __asm__ ("mov %1, %0\n\trla.b %0\n\tclr %0\n\tadc %0" : "=&r"(r) : "r"(x));
    return r;
}
static inline __attribute__((always_inline)) u16 dread_nz(u16 x)
{
    u16 r;
    __asm__ ("clr %0\n\ttst %1\n\tjz 1f\n\tmov #1, %0\n1:" : "=&r"(r) : "r"(x));
    return r;
}
static inline __attribute__((always_inline)) u16 dread_z(u16 x)
{
    u16 r;
    __asm__ ("clr %0\n\ttst %1\n\tjnz 1f\n\tmov #1, %0\n1:" : "=&r"(r) : "r"(x));
    return r;
}
static inline __attribute__((always_inline)) u16 dread_rra16(u16 x)
{
    __asm__ ("rra %0" : "+r"(x));
    return x;
}
static inline __attribute__((always_inline)) u16 dread_rra8(u16 x)
{
    __asm__ ("rra.b %0" : "+r"(x));
    return x;
}
static inline __attribute__((always_inline)) u16 dread_rrc16(u16 x, u16 c)
{
    __asm__ ("rrc %1\n\trrc %0" : "+r"(x), "+r"(c));
    return x;
}
static inline __attribute__((always_inline)) u16 dread_rrc8(u16 x, u16 c)
{
    __asm__ ("rrc %1\n\trrc.b %0" : "+r"(x), "+r"(c));
    return x;
}
static inline __attribute__((always_inline)) u16 dread_swpb(u16 x)
{
    __asm__ ("swpb %0" : "+r"(x));
    return x;
}
static inline __attribute__((always_inline)) u16 dread_dadd16(u16 a, u16 b, u16 *c)
{
    u16 k, t = *c;
    __asm__ ("rrc %2\n\tdadd %3, %0\n\tclr %1\n\tadc %1" : "+r"(a), "=&r"(k), "+r"(t) : "r"(b));
    *c = k;
    return a;
}
static inline __attribute__((always_inline)) u16 dread_dadd8(u16 a, u16 b, u16 *c)
{
    u16 k, t = *c;
    __asm__ ("rrc %2\n\tdadd.b %3, %0\n\tclr %1\n\tadc %1" : "+r"(a), "=&r"(k), "+r"(t) : "r"(b));
    *c = k;
    return a;
}"""
FLAGS = 0x107                   # C, Z, N and V in sr
# Before a store to memory the value goes through an empty asm: TI's GCC 8.2 joins a volatile load and
# a store of the same value back to the same address into a memory-to-memory move and deletes that as
# a move to itself (add.b #256, 3(r10) left no write; the fuzzer found it)
OPAQUE = '__asm__ ("" : "+r"(d0)); '
RESULTS = {1: ("r12",), 2: ("r12", "r13"), 4: ("r12", "r13", "r14", "r15")}
CTYPES = {1: "u16", 2: "u32", 4: "u64"}


class Msp430Lifter(lift.Lifter):
    REGS, ARGS, AFTER_CALL = REGS, ARGS, AFTER_CALL

    def __init__(self, program, flow=None, endless=None):
        super().__init__(program, flow, endless)
        self._epilogues = {}

    # ---- the stack pointer's depth and the frame (dread.lift's states) ----

    def entry_values(self):
        return {0: self.RET}            # the return address: in the slot the call pushed

    def epilogue(self, insn):
        """The registers a br #X pops when the code at X is pops ending in ret (GCC's shared
        epilogues fall into one another), else None."""
        f = insn.fields
        kind, target, _ = self.arch.flow(insn)
        if kind != "jump" or f.op != "mov" or target is None:
            return None
        if target not in self._epilogues:
            regs, a, found = [], target, None
            for _ in range(16):
                i = self.prog.insn(a)
                if i is None:
                    break
                if self.arch.stack_kind(i) == "pop":
                    regs.append(NAMES[i.fields.dst.reg])
                    a += i.size
                    continue
                if self.arch.flow(i)[0] == "return" and i.fields.op != "reti":
                    found = regs
                break
            self._epilogues[target] = found
        return self._epilogues[target]

    def ret_jump(self, insn, values):
        return self.epilogue(insn) is not None

    @staticmethod
    def _frame(o, depth, values):
        """("sp", k) when an operand's register holds a frame address (sp itself included)."""
        if o is None or o.mode not in ("reg", "idx", "ind", "inc"):
            return None
        if o.reg == 1:
            return ("sp", depth)
        v = values.get(NAMES[o.reg]) if o.reg >= 4 else None
        return v if isinstance(v, tuple) and v and v[0] == "sp" else None

    def step(self, insn, depth, values):
        f = insn.fields
        after = dict(values)
        stack = self.arch.stack_kind(insn)
        if stack == "push":
            new = depth + 2
            self._put(after, new, self._known(f.src, depth, values))
            return new, after
        if stack == "pop":
            self._put(after, NAMES[f.dst.reg], values.get(depth))
            return depth - 2, after
        reads, writes = self.arch.reg_effects(insn)
        new = self.new_depth(insn, depth, values) if "sp" in writes else depth
        known = self.made_value(insn, depth, values)
        slot = self.stored_slot(insn, depth, values)
        for r in writes:
            if r not in ("sp", "pc"):
                after.pop(r, None)
        if self.arch.flow(insn)[0] in ("call", "icall"):
            for r in self.after_call(insn):
                after.pop(r, None)
        if known is not None:
            self._put(after, known[0], known[1])
        if slot is not None:
            self._put(after, slot[0], slot[1])
        return new, after

    def _known(self, o, depth, values):
        """What is known of a source operand's value: a constant, a frame address, a slot's value."""
        if o is None:
            return None
        if o.mode == "imm":
            return ("const", o.value & 0xFFFF)
        if o.mode == "reg":
            if o.reg == 1:
                return ("sp", depth)
            return values.get(NAMES[o.reg]) if o.reg >= 4 else None
        if o.mode in ("idx", "ind"):
            base = self._frame(o, depth, values)
            if base is not None:
                return values.get(base[1] - (o.value if o.mode == "idx" else 0))
        return None

    def made_value(self, insn, depth, values):
        """(register, value) for mov of a known value into a register (a constant, a frame address,
        a frame slot's value), add of a constant to a frame address, add of sp to a constant."""
        f = insn.fields
        if f.dst is None or f.dst.mode != "reg" or f.dst.reg < 4 or f.byte:
            return None
        name = NAMES[f.dst.reg]
        if f.op == "mov":
            v = self._known(f.src, depth, values)
            return (name, v) if v is not None else None
        if f.op in ("add", "sub"):
            mine, other = values.get(name), self._known(f.src, depth, values)
            if isinstance(mine, tuple) and mine and mine[0] == "sp" and other and other[0] == "const":
                k = other[1] if other[1] < 0x8000 else other[1] - 0x10000
                return name, ("sp", mine[1] - k if f.op == "add" else mine[1] + k)
            if f.op == "add" and other and other[0] == "sp" and mine and mine[0] == "const":
                k = mine[1] if mine[1] < 0x8000 else mine[1] - 0x10000
                return name, ("sp", other[1] - k)
        return None

    def stored_slot(self, insn, depth, values):
        """(slot, value) of a word mov into the frame (mov rN, X(sp))."""
        f = insn.fields
        if f.op != "mov" or f.byte or f.dst is None or f.dst.mode != "idx":
            return None
        base = self._frame(f.dst, depth, values)
        if base is None:
            return None
        return base[1] - f.dst.value, self._known(f.src, depth, values)

    def ldm_slots(self, insn, depth, values):
        return None

    def new_depth(self, insn, depth, values):
        """The depth after an instruction that writes sp: a call (its callee returns past the return
        address it pushed), sub or add of a constant, a move of a frame address, @sp+ as a source."""
        f = insn.fields
        if self.arch.flow(insn)[0] in ("call", "icall") or self.exception_return(insn):
            return depth                    # (reti ends the path: what it pops is the handler's own)
        if f.src is not None and f.src.mode == "inc" and f.src.reg == 1:
            if f.dst is not None and f.dst.mode == "reg" and f.dst.reg == 1:
                raise Refused("0x%x pops into the stack pointer" % insn.address)
            return depth - 2
        d = f.dst
        if d is not None and d.mode == "reg" and d.reg == 1 and not f.byte:
            if f.op in ("add", "sub") and f.src.mode == "imm":
                k = f.src.value if f.src.value < 0x8000 else f.src.value - 0x10000
                return depth - k if f.op == "add" else depth + k
            if f.op == "mov":
                v = self._known(f.src, depth, values)
                if isinstance(v, tuple) and v and v[0] == "sp":
                    return v[1]
        raise Refused("0x%x changes the stack pointer in a way not modelled: %s %s" % (
            insn.address, insn.mnemonic, insn.op_str))

    def saving_store(self, insn, values):
        """None: GCC saves the registers it keeps with push (a save the analysis knows), and a mov of
        a register nothing wrote into the frame is a read (libgcc's __mspabi_mpyll takes its second
        operand in r8-r11 and spills it so)."""
        return None

    def plain_move(self, insn):
        f = insn.fields
        if f.op == "mov" and not f.byte and f.src is not None and f.src.mode == "reg" and f.src.reg >= 4 \
                and f.dst is not None and f.dst.mode == "reg" and f.dst.reg >= 4:
            return NAMES[f.dst.reg], NAMES[f.src.reg]
        return None

    def stack_args(self, fn):
        """Bytes above the entry's return address the function reads or writes (its stack
        arguments, which the translation refuses): 0, the count of words, or None."""
        if fn.name in self._stack_args:
            return self._stack_args[fn.name]
        self._stack_args[fn.name] = 0
        try:
            states = self.states(fn, strict=False)
        except Refused:
            self._stack_args[fn.name] = None
            return None
        top = 0
        for a, (depth, values) in states.items():
            f = self.prog.insn(a).fields
            for o in (f.src, f.dst):
                base = self._frame(o, depth, values)
                if base is not None and o.mode == "idx" and o.value - base[1] >= 2:
                    top = max(top, (o.value - base[1]) // 2)
        self._stack_args[fn.name] = top
        return top

    # ---- what kind of function ----

    def lift(self, fn):
        n = self.stack_args(fn)
        if n:
            raise Refused("it takes stack arguments (beyond r12-r15)")
        return super().lift(fn)

    def compiled_isa(self):
        return "msp430"

    def exception_return(self, insn):
        return insn.fields.op == "reti"

    def handler_kind(self, fn):
        """"interrupt" when the function's returns are reti, else None."""
        if fn.name in self._handlers:
            return self._handlers[fn.name]
        returns = [i for i in self.prog.instructions(fn) if i is not None and self.arch.flow(i)[0] == "return"]
        traps = [i for i in returns if self.exception_return(i)]
        if traps and len(traps) != len(returns):
            raise Refused("it returns both from an interrupt and to a caller")
        kind = "interrupt" if traps else None
        self._handlers[fn.name] = kind
        return kind

    def _check_returns(self, fn, states, handler=None):
        """ret at depth 0 (the return address on top), an epilogue at the depth of what it pops."""
        for a, (depth, values) in states.items():
            insn = self.prog.insn(a)
            if self.exception_return(insn):
                if handler:
                    continue
                raise Refused("0x%x returns from an interrupt" % a)
            regs = self.epilogue(insn)
            if regs is not None:
                if depth != 2 * len(regs):
                    raise Refused("0x%x branches to an epilogue that pops %d registers at depth %d" % (
                        a, len(regs), depth))
                continue
            if self.arch.flow(insn)[0] != "return":
                continue
            if depth != 0 or values.get(0) != self.RET:
                raise Refused("0x%x returns to an address it computed, not to its caller" % a)

    def _width(self, name):
        """How many result registers the callers read: 1 (r12), 2 (r12:r13) or 4 (r12-r15)."""
        flow = self.flow
        if not flow:
            return 1
        kept, kept_safe = set(flow.kept_regs(name)), set(flow.preserved_regs(name))
        if {"r14", "r15"} & (kept - kept_safe):
            return 4
        return 2 if "r13" in kept - kept_safe else 1

    def _read_after(self, insn):
        """The result registers beyond r12 (r13-r15) the function reads behind a call before writing
        them, on any path: its own code says how wide the result is where the register flow saw no
        site (a call through a register that holds the callee's address). A return reads the
        function's own result registers; a call the arguments its callee reads."""
        fn = self.ctx["fn"]
        index = {a: i for i, a in enumerate(fn.insns)}
        want = frozenset(("r13", "r14", "r15"))
        found, seen = set(), set()
        work = [(insn.address + insn.size, want)]
        mine = set(RESULTS[self._width(fn.name)])
        while work:
            a, live = work.pop()
            if a not in index or (a, live) in seen or not live:
                continue
            seen.add((a, live))
            i = self.prog.insn(a)
            if i is None:
                continue
            kind, target, falls = self.arch.flow(i)
            if kind == "return" or self.epilogue(i) is not None:
                found |= live & mine
                continue
            reads, writes = self.arch.reg_effects(i)
            if kind in ("call", "icall"):
                callee = self.prog.by_entry.get(target) if kind == "call" else self._known_callee(i)
                reads = set(self.flow.read_regs(callee.name)) if callee is not None and self.flow else set(ARGS)
                writes = set(writes) | self.AFTER_CALL
            found |= live & set(reads)
            live = live - set(writes)
            for b in self.successors(fn, index, a, i, False, None):
                work.append((b, live))
        return found

    def _site_width(self, callee, insn):
        """The width of a call's result: what the register flow says of the callee, or more where
        the caller reads r13-r15 behind this call."""
        width = self._width(callee.name)
        extra = self._read_after(insn)
        if extra & {"r14", "r15"}:
            return 4
        if "r13" in extra:
            return max(width, 2)
        return width

    def _check_kept(self, fn):
        flow = self.flow
        if flow is None:
            return
        result = set(RESULTS[self._width(fn.name)])
        written = sorted(set(flow.kept_regs(fn.name)) - result - set(flow.preserved_regs(fn.name)))
        if written:
            raise Refused("its callers read %s, which it sets (%s); C returns r12-r15 only" % (
                written[0], flow.why(fn.name, written[0])))

    def _check_after_call(self, callee, insn):
        flow = self.flow
        if flow is None:
            return
        result = set(RESULTS[self._width(callee.name)])
        for reg in flow.kept_regs(callee.name):
            if reg not in result and (self.ctx["fn"].name, insn.address) in flow.sites_of(callee.name, reg):
                raise Refused("0x%x reads %s behind the call of %s (what the callee leaves)" % (
                    insn.address, reg, callee.name))

    def after_call(self, insn):
        return self.AFTER_CALL

    # ---- the translation ----

    def _sp(self, k=0):
        """The C address k bytes above sp now (refused at or above the entry's stack pointer)."""
        depth = self.ctx["depth"][self.insn_now.address]
        off = k - depth
        if off >= 0:
            raise Refused("0x%x reads or writes at or above its entry's stack pointer (the return address, "
                          "stack arguments)" % self.insn_now.address)
        return "(u16)(sp0 - %d)" % -off

    def _reg(self, n):
        """A register's value as a source (sp: its address in the frame; pc: behind the first word)."""
        if n == 0:
            return "0x%xu" % ((self.insn_now.address + 2) & 0xFFFF)
        if n == 1:                          # a value (add r1, r14: a frame address), not an access
            return "(u16)(sp0 - %d)" % self.ctx["depth"][self.insn_now.address]
        if n == 2:
            return self._sr()
        if n == 3:
            return "0u"
        return NAMES[n]

    def _sr(self):
        # without shifts: the MSP430 has no barrel shifter, and GCC calls libgcc for one (the
        # image need not hold it)
        self.ctx["sr"] = True
        return "(u16)((dread_sr() & ~0x%xu) | c | ((u16)(0u - z) & 2u) | ((u16)(0u - n) & 4u) | " \
               "((u16)(0u - v) & 0x100u))" % FLAGS

    def _address(self, o):
        """The C address of a memory operand."""
        if o.mode == "abs":
            return "0x%xu" % (o.value & 0xFFFF)
        if o.mode == "sym":
            return "0x%xu" % o.address
        if o.reg == 1:
            return self._sp(o.value if o.mode == "idx" else 0)
        if o.reg == 0:
            return "0x%xu" % (((self.insn_now.address + 2) + (o.value if o.mode == "idx" else 0)) & 0xFFFF)
        base = self._reg(o.reg)
        if o.mode == "idx" and o.value:
            return "(u16)(%s + 0x%xu)" % (base, o.value & 0xFFFF)
        return base

    @staticmethod
    def _load(address, byte):
        return "(u16)*(volatile u8 *)(%s)" % address if byte else "*(volatile u16 *)(%s)" % address

    def _source(self, o, byte, pre):
        """The C expression of a source operand's value; statements it needs first (an @rN+ load and
        its increment) go into `pre`."""
        mask = 0xFF if byte else 0xFFFF
        if o.mode == "imm":
            return "0x%xu" % (o.value & mask)
        if o.mode == "reg":
            v = self._reg(o.reg)
            return "(u16)(%s & 0xffu)" % v if byte else v
        if o.mode == "inc":
            if o.reg == 1:                  # @sp+: the depth moves (states); the C reads the slot
                pre.append("s0 = %s;" % self._load(self._sp(0), byte))
                return "s0"
            if o.reg in (0, 2, 3):
                raise Refused("0x%x: @%s+ is not modelled" % (self.insn_now.address, NAMES[o.reg]))
            r = NAMES[o.reg]
            pre.append("s0 = %s; %s += %d;" % (self._load(r, byte), r, 1 if byte else 2))
            return "s0"
        return self._load(self._address(o), byte)

    def _neg(self, expr, byte):
        """The sign bit of expr, 0 or 1 (see HELPERS)."""
        self.ctx["helpers"] = True
        return "dread_sign%s(%s)" % ("8" if byte else "16", expr)

    def _z(self, expr):
        self.ctx["helpers"] = True
        return "dread_z(%s)" % expr

    def _nz(self, expr):
        self.ctx["helpers"] = True
        return "dread_nz(%s)" % expr

    def _flags_add(self, a, b, cin, byte, res):
        """res = a + b + cin with the four flags; the carry from the sum's high word (a union: a
        32-bit shift is a libgcc call) or, for a byte, from bit 8 of a 16-bit sum."""
        sign = self._neg(res, byte)
        over = self._neg("~(%s ^ %s) & (%s ^ %s)" % (a, b, a, res), byte)
        if byte:                            # the sum of two bytes and a carry: bit 8 is C (a byte of
            # a union in a register is a shift by 8, a call, for GCC)
            return ("{ u16 t_ = (u16)(%s + %s + %s); %s = (u16)(t_ & 0xffu); c = %s; z = %s; n = %s; v = %s; }"
                    % (a, b, cin, res, self._nz("t_ & 0x100u"), self._z(res), sign, over))
        return ("{ dread_w32 t_; t_.w = (u32)%s + (u32)%s + %s; %s = t_.h[0]; c = t_.h[1]; z = %s; n = %s; v = %s; }"
                % (a, b, cin, res, self._z(res), sign, over))

    def insn(self, insn):
        self.insn_now = insn
        f = insn.fields
        op, a = f.op, insn.address
        kind, target, falls = self.arch.flow(insn)
        depth = self.ctx["depth"]
        if op == "unknown":
            raise Refused("0x%x: an instruction the decoder does not know (MSP430X?)" % a)
        if kind == "return":
            if self.exception_return(insn):
                if self.ctx.get("handler"):
                    return "return;"        # gcc writes the epilogue and reti
                raise Refused("0x%x returns from an interrupt" % a)
            return "return %s;" % self._ret()
        if self.epilogue(insn) is not None:
            return "return %s;" % self._ret()
        if f.target is not None:            # the jumps
            if target not in depth:
                callee = self.prog.by_entry.get(target)
                if op == "jmp" and callee is not None:
                    return "%s return %s;" % (self._call(callee, insn), self._ret())
                raise Refused("0x%x jumps out of the function (0x%x)" % (a, target))
            if op == "jmp":
                return "goto L_%x;" % target
            return "if (%s) goto L_%x;" % (JUMPS[op], target)
        if kind == "jump":                  # br #X
            if target in depth:
                return "goto L_%x;" % target
            callee = self.prog.by_entry.get(target)
            if callee is None:
                raise Refused("0x%x jumps into other code (0x%x)" % (a, target))
            return "%s return %s;" % (self._call(callee, insn), self._ret())
        if kind == "ijump":
            raise Refused("0x%x jumps through a register (a table?): not modelled" % a)
        if op == "call":
            return self._call_insn(insn)
        if op == "push":
            pre = []
            v = self._source(f.src, f.byte, pre)
            new = depth[a] + 2
            store = "*(volatile %s *)((u16)(sp0 - %d)) = %s;" % ("u8" if f.byte else "u16", new,
                                                                 "(u8)(%s)" % v if f.byte else v)
            return " ".join(pre + [store]) if pre else store
        if f.dst is None:
            return self._single(insn, f)
        return self._double(insn, f)

    def _single(self, insn, f):
        """rrc, rra, swpb, sxt: the result goes back where the operand came from."""
        op, byte, o = f.op, f.byte, f.src
        mask = 0xFF if byte else 0xFFFF
        pre, out = [], []
        if o.mode == "reg":
            if o.reg in (0, 1):
                raise Refused("0x%x: %s of %s is not modelled" % (insn.address, op, NAMES[o.reg]))
            value, put = self._source(o, byte, pre), (lambda res: self._put_reg(o.reg, res, byte))
        elif o.mode == "imm":
            if o.reg == 0:
                raise Refused("0x%x: %s of an immediate writes the code" % (insn.address, op))
            value, put = "0x%xu" % (o.value & mask), (lambda res: None)
        else:
            if o.mode == "inc":
                if o.reg in (0, 1, 2, 3):
                    raise Refused("0x%x: %s of @%s+ is not modelled" % (insn.address, op, NAMES[o.reg]))
                address = NAMES[o.reg]
                pre.append("a0 = %s;" % address)
                pre.append("%s += %d;" % (NAMES[o.reg], 1 if byte else 2))
                address = "a0"
            else:
                address = self._address(o)
                pre.append("a0 = %s;" % address)
                address = "a0"
            value = self._load(address, byte)
            put = (lambda res, address=address: OPAQUE + "*(volatile %s *)(%s) = %s;" % (
                "u8" if byte else "u16", address, "(u8)(%s)" % res if byte else res))
        out += pre
        out.append("s0 = %s;" % value)
        if op in ("rrc", "rra"):            # as themselves (see HELPERS): GCC merged two rra written
            self.ctx["helpers"] = True      # as shifts into one shift by two, a call of __mspabi_srli
            out.append("d0 = dread_%s%s(s0%s); c = s0 & 1u; z = %s; n = %s; v = 0;"
                       % (op, "8" if byte else "16", ", c" if op == "rrc" else "", self._z("d0"), self._neg("d0", byte)))
        elif op == "swpb":
            self.ctx["helpers"] = True
            out.append("d0 = dread_swpb(s0);")     # swpb itself (GCC's __builtin_bswap16 is two shifts, calls)
        elif op == "sxt":
            out.append("d0 = (u16)(s16)(s8)(u8)s0; c = %s; z = %s; n = %s; v = 0;"
                       % (self._nz("d0"), self._z("d0"), self._neg("d0", False)))
        else:
            raise Refused("0x%x: %s is not modelled" % (insn.address, op))
        stored = put("d0")
        if stored:
            out.append(stored)
        return "{ %s }" % " ".join(out)

    def _put_reg(self, n, value, byte):
        """The C that writes a register (sr through the real register; r3 nowhere)."""
        if n == 3:
            return None
        if n == 2:
            self.ctx["sr"] = True
            return ("{ u16 w_ = %s; __asm__ volatile (\"mov %%0, r2\" : : \"r\"(w_) : \"memory\"); "
                    "c = w_ & 1u; z = %s; n = %s; v = %s; }"
                    % ("(u16)(%s & 0xffu)" % value if byte else value, self._nz("w_ & 2u"), self._nz("w_ & 4u"),
                       self._nz("w_ & 0x100u")))
        return "%s = %s;" % (NAMES[n], "(u16)(%s & 0xffu)" % value if byte else value)

    def _double(self, insn, f):
        op, byte, d = f.op, f.byte, f.dst
        mask = 0xFF if byte else 0xFFFF
        pre = []
        s = self._source(f.src, byte, pre)
        if d.mode == "reg" and d.reg == 1:
            if op in ("add", "sub") and f.src.mode == "imm" and not byte:
                return None                 # a frame adjustment: states follows it
            if op == "mov":
                return None                 # sp from a frame address: states follows it
            raise Refused("0x%x computes the stack pointer: %s %s" % (insn.address, insn.mnemonic, insn.op_str))
        if d.mode == "reg" and d.reg == 0:
            raise Refused("0x%x computes pc: %s %s" % (insn.address, insn.mnemonic, insn.op_str))
        if op == "mov" and d.mode == "reg" and d.reg == 3 and f.src.mode == "imm":
            return '__asm__ volatile ("nop");'  # a nop: dint and eint want one behind them on hardware
        out = list(pre)
        if pre or "s0" not in s:
            out.append("s0 = %s;" % s)
        if d.mode == "reg":
            dread = self._reg(d.reg)
            dv = "(u16)(%s & 0xffu)" % dread if byte else dread
            store = (lambda res: self._put_reg(d.reg, res, byte))
        else:
            out.append("a0 = %s;" % self._address(d))
            dv = self._load("a0", byte)
            store = (lambda res: OPAQUE + "*(volatile %s *)(a0) = %s;" % ("u8" if byte else "u16",
                                                                          "(u8)(%s)" % res if byte else res))
        res = "d0"
        if op == "mov":
            out.append("d0 = s0;")
        else:
            out.append("e0 = %s;" % dv)
            if op == "add":
                out.append(self._flags_add("e0", "s0", "0u", byte, res))
            elif op == "addc":
                out.append(self._flags_add("e0", "s0", "c", byte, res))
            elif op == "sub":
                out.append(self._flags_add("e0", "(u16)(~s0 & 0x%xu)" % mask, "1u", byte, res))
            elif op == "subc":
                out.append(self._flags_add("e0", "(u16)(~s0 & 0x%xu)" % mask, "c", byte, res))
            elif op == "cmp":
                out.append(self._flags_add("e0", "(u16)(~s0 & 0x%xu)" % mask, "1u", byte, res))
                res = None
            elif op == "dadd":              # as itself (see HELPERS): the hardware's own decimal add
                out.append("d0 = dread_dadd%s(e0, s0, &c); z = %s; n = %s;"
                           % ("8" if byte else "16", self._z("d0"), self._neg("d0", byte)))
            elif op == "bit":
                out.append("d0 = s0 & e0; c = %s; z = %s; n = %s; v = 0;"
                           % (self._nz("d0"), self._z("d0"), self._neg("d0", byte)))
                res = None
            elif op == "bic":
                out.append("d0 = (u16)(~s0 & e0 & 0x%xu);" % mask)
            elif op == "bis":
                out.append("d0 = s0 | e0;")
            elif op == "xor":
                out.append("d0 = s0 ^ e0; c = %s; z = %s; n = %s; v = %s;"
                           % (self._nz("d0"), self._z("d0"), self._neg("d0", byte), self._neg("s0 & e0", byte)))
            elif op == "and":
                out.append("d0 = s0 & e0; c = %s; z = %s; n = %s; v = 0;"
                           % (self._nz("d0"), self._z("d0"), self._neg("d0", byte)))
            else:
                raise Refused("0x%x: %s is not modelled" % (insn.address, op))
        if res is not None:
            if d.mode == "reg" and d.reg == 2 and op not in ("mov", "bic", "bis"):
                # the flags win over the value written (the engine; not confirmed on hardware)
                out.append("{ u16 k_[4] = {c, z, n, v}; %s c = k_[0]; z = k_[1]; n = k_[2]; v = k_[3]; }"
                           % store(res))
            else:
                stored = store(res)
                if stored:
                    out.append(stored)
        return "{ %s }" % " ".join(out)

    def _known_callee(self, insn):
        """The function a call through a register calls when the register holds its entry (mov
        #uart_puts, r10; call r10), else None."""
        f = insn.fields
        state = self.ctx["states"].get(insn.address)
        if f.src is None or f.src.mode != "reg" or state is None:
            return None
        known = self._known(f.src, state[0], state[1])
        return self.prog.by_entry.get(known[1]) if known and known[0] == "const" else None

    def _call_insn(self, insn):
        f = insn.fields
        kind, target, _ = self.arch.flow(insn)
        a = insn.address
        if kind == "call":
            callee = self.prog.by_entry.get(target)
            if callee is None:
                raise Refused("0x%x calls into the middle of something (0x%x)" % (a, target))
        else:
            callee = self._known_callee(insn)
            if callee is None:
                pre = []
                fp = self._source(f.src, False, pre)
                return " ".join(pre + [self._icall(fp)])
        text = self._call(callee, insn)
        return text + " for (;;) { }" if self.never_returns(callee.name) else text

    def _ret(self):
        if self.ctx.get("handler"):
            return ""
        width = self._width(self.ctx["fn"].name)     # words through a union: a shift by 16 is a libgcc
        if width == 4:                                # call on the MSP430 (__mspabi_slll)
            return "((dread_w64){ .h = { r12, r13, r14, r15 } }).w"
        if width == 2:
            return "((dread_w32){ .h = { r12, r13 } }).w"
        return "r12"

    def _call(self, callee, insn):
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
        width = self._site_width(callee, insn)
        self.ctx["callees"].add((callee.name, 0, width))
        call = "%s(r12, r13, r14, r15)" % callee.name
        results = RESULTS[width]
        clear = " = ".join(r for r in SCRATCH if r not in results) + " = 0; c = z = n = v = 0;"
        if width == 4:
            return "{ dread_w64 x_; x_.w = %s; r12 = x_.h[0]; r13 = x_.h[1]; r14 = x_.h[2]; r15 = x_.h[3]; } %s" % (
                call, clear)
        if width == 2:
            return "{ dread_w32 x_; x_.w = %s; r12 = x_.h[0]; r13 = x_.h[1]; } %s" % (call, clear)
        return "r12 = %s; %s" % (call, clear)

    def _icall(self, fp):
        return ("r12 = ((u16 (*)(u16, u16, u16, u16))(%s))(r12, r13, r14, r15); r11 = r13 = r14 = r15 = 0; "
                "c = z = n = v = 0;" % fp)

    def _switches(self, fn):
        pass                                # jumps through tables are refused (insn)

    def render(self, fn, body):
        ctx = self.ctx
        lines = ["/* %s at 0x%x, lifted by dread lift: one statement per instruction. */" % (fn.name, fn.entry),
                 '#include "dread.h"', "typedef union { u32 w; u16 h[2]; } dread_w32;",
                 "typedef union { u64 w; u16 h[4]; } dread_w64;"]
        widest = {}
        for name, nstack, width in ctx["callees"]:       # one declaration: the widest of its calls
            widest[name] = max(widest.get(name, 1), width)
        for name, width in sorted(widest.items()):
            lines.append("extern %s %s(u16, u16, u16, u16);" % (CTYPES[width], name))
        if ctx.get("helpers"):
            lines.append(HELPERS)
        if ctx.get("sr"):
            lines.append("static inline __attribute__((always_inline)) u16 dread_sr(void)\n"
                         "{\n    u16 s;\n    __asm__ volatile (\"mov r2, %0\" : \"=r\"(s) : : \"memory\");\n"
                         "    return s;\n}")
        regs = ["r%d" % i for i in range(4, 16)]
        if ctx.get("handler"):
            lines.append("void __attribute__((interrupt)) %s(void)" % fn.name)
            lines.append("{")
            init = ["%s = 0" % r for r in regs]
        else:
            lines.append("%s %s(u16 p12, u16 p13, u16 p14, u16 p15)" % (CTYPES[self._width(fn.name)], fn.name))
            lines.append("{")
            init = ["%s = p%s" % (r, r[1:]) if r in ARGS else "%s = 0" % r for r in regs]
        lines.append("    u16 %s;" % ", ".join(init))
        lines.append("    u16 c = 0, z = 0, n = 0, v = 0, s0 = 0, d0 = 0, e0 = 0, a0 = 0;")
        lines += self.fixed_lines()
        frame = (ctx["frame"] + 7) & ~7
        lines.append("    u8 frame[%d] __attribute__((aligned(2)));" % max(8, frame))
        lines.append("    u16 sp0 = (u16)&frame[%d];" % max(8, frame))
        lines.append("    %s (void)c; (void)z; (void)n; (void)v; (void)s0; (void)d0; (void)e0; (void)a0; (void)sp0;"
                     % " ".join("(void)%s;" % r for r in regs))
        lines += body
        lines.append("}")
        return "\n".join(lines) + "\n"
