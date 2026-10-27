"""dread lift for 32-bit x86 (i386): one C statement per instruction, as dread.lift does for ARM.

The registers eax, ecx, edx, ebx, esi, edi and ebp are u32 variables; a write to a part of one (al, ah,
ax) keeps the rest, as the core does. The stack pointer's depth below the entry's is known statically
at every instruction (dread.lift's states: push and pop, sub esp, N and add esp, N, a move or lea of a
frame address into esp, leave, a call's return address, which the callee's ret takes off again), so
esp as an operand is the C expression of that address in a frame array, and an access through it, or
through a register that holds a frame address (ebp behind push ebp; mov ebp, esp), lands there; the
array leaves out the words at the frame's top that only pushes and pops nobody reads behind touch
(dread.lift's saves_out). The flags are four variables cf, zf, sf and of, computed as the core computes
them for the 8-, 16- and 32-bit forms (add, adc, sub, sbb, cmp, neg: all four; and, or, xor, test: cf
and of cleared; inc and dec leave cf; the shifts and rotations by a count that is 0 change nothing;
imul and mul set cf and of from the product's upper half); the conditional jumps, setcc and cmovcc test
them. An instruction that reads the parity or the auxiliary flag (jp, setnp, lahf, pushfd) is refused.
Memory accesses are volatile and in the original's order. What C would make a libgcc call of (a 64-bit
division) is the instruction itself in inline assembly: div and idiv.

The return address lies in the slot the call pushed (depth 0): ret is a return from there. Every
argument lies above it (cdecl), a word each: the words a function reads or writes there, directly or
through a register that holds a frame address, or passes on to a tail call (stack_args), are its
parameters sa0, sa1, ..., and an access of one goes through the parameter's address, which GCC keeps
in the slot the caller passed it in once its address is taken: the C reads and writes where the
original does. A call passes the words of its frame that lie where the callee reads them (at sp and
above when it calls: GCC pushes them, or stores them at esp + k), and eax: every function's C takes
eax as it comes, in eax (compiled with -mregparm=1, dread.build's REGPARM line: the stack arguments
stay where cdecl has them, so that original callers and callees keep theirs; an attribute in the text
would not survive dread.readable, which writes heads anew), since a function that does not write eax
hands its caller's eax back,
and the cases compare a void function's result register too (gizmo's uart_init: one store and ret). A
call takes eax, or eax and edx where the callers read a 64-bit result (dread.regflow); ecx, edx (or
ecx alone) and the flags are 0 behind it. A call through a register or memory passes the lowest
frame words (dread.lift.INDIRECT_STACK_WORDS).

Refused, with the reason: a write of esp that is not a frame adjustment the analysis follows (and esp:
-16, main's alignment), an access of the return address, the address of a stack argument or of the
return address in a register (va_start, &argument: the C's arguments do not lie above its frame array),
a frame word read again behind the call it was passed to (the callee owns its stack arguments), a tail
call with a frame, ret with an immediate (stdcall), a jump through a register or memory that is neither
a switch dread.switches resolves nor a tail call through a loaded pointer, segment registers, string
instructions with a repeat that compares (repe cmpsb), the
direction flag set (std), port I/O, interrupts and system instructions, the x87 and the vector units,
and what dread.lift refuses for every architecture (a stack pointer that differs between paths, a
fall-through out of the function, a register read before written).

Not established: an access of a stack argument through a register whose frame address the analysis
does not know goes to the C's frame array; nothing refuses it, the cases have to notice. The flags a
core leaves undefined are the emulator's (QEMU's), which dread liftfuzz checks against: sf and zf of
mul's and imul's low half, of behind a shift by more than one from the last bit shifted, all four
unchanged behind div.
"""
from capstone import x86 as X

from dread import lift
from dread.arch.x86 import FULL, LANES, M32
from dread.lift import IDENT, INDIRECT_STACK_WORDS, Refused

REGS = ["eax", "ecx", "edx", "ebx", "esi", "edi", "ebp"]
# cdecl passes every argument on the stack. eax is handed in and on all the same (the C is compiled with
# -mregparm=1: eax_in, a first parameter in eax, the stack arguments where cdecl has them): a function
# that does not write eax returns what its caller left there, which the cases compare (a void
# function's result register too)
ARGS = ("eax",)
SCRATCH = ("eax", "ecx", "edx")
AFTER_CALL = set(SCRATCH) | {"flags"}
RESULTS = {1: ("eax",), 2: ("eax", "edx")}
CTYPES = {1: "u32", 2: "u64"}
CONDITIONS = {"o": "of", "no": "!of", "b": "cf", "ae": "!cf", "e": "zf", "ne": "!zf", "be": "(cf | zf)",
              "a": "!(cf | zf)", "s": "sf", "ns": "!sf", "l": "(sf != of)", "ge": "(sf == of)",
              "le": "(zf | (sf != of))", "g": "!(zf | (sf != of))"}
JUMPS = {"j" + c: c for c in CONDITIONS}
SETS = {"set" + c: c for c in CONDITIONS}
MOVES = {"cmov" + c: c for c in CONDITIONS}
MUSTTAIL = "typedef unsigned (*f_)(unsigned);\nunsigned g_(f_ p, unsigned x) { __attribute__((musttail)) return p(x); }\n"
NOPS = ("nop", "cld", "endbr32", "pause")   # cld: the direction flag is clear at every call (the ABI)
ARITH = ("add", "adc", "sub", "sbb", "cmp", "and", "or", "xor", "test")
SHIFTS = ("shl", "sal", "shr", "sar", "rol", "ror")
MASKS = {1: 0xFF, 2: 0xFFFF, 4: M32}
UTYPES = {1: "u8", 2: "u16", 4: "u32"}
STYPES = {1: "s8", 2: "s16", 4: "s32"}
REFUSED = {"std": "sets the direction flag", "in": "reads a port", "out": "writes a port", "cli": "masks interrupts",
           "sti": "allows interrupts", "lahf": "reads the flags register", "sahf": "writes the flags register",
           "pushfd": "pushes the flags register", "popfd": "pops into the flags register", "int": "raises an interrupt",
           "int3": "is a breakpoint", "ud2": "is an undefined instruction", "hlt": "halts", "iretd": "returns from an "
           "interrupt", "rdtsc": "reads the time-stamp counter", "cpuid": "asks the core what it is",
           "pushal": "pushes every register", "popal": "pops every register", "enter": "makes a frame with enter"}


def _mask(size):
    return MASKS[size]


class X86Lifter(lift.Lifter):
    REGS, ARGS, AFTER_CALL = REGS, ARGS, AFTER_CALL
    regparm = 1                             # dread.build compiles the C with -mregparm=1 (its REGPARM line)
    MORE = ()                               # results are as wide as the callers read them (eax, edx): no entry
    seed_switches = True

    # ---- the stack pointer's depth and the frame (dread.lift's states) ----

    def entry_values(self):
        return {0: self.RET}                # the return address: in the slot the call pushed

    def ret_jump(self, insn, values):
        return False

    def _reg(self, insn, reg):
        """The analyses' name of a register capstone names (esp: "sp")."""
        return FULL.get(insn.reg_name(reg), insn.reg_name(reg))

    @staticmethod
    def _index(insn, op):
        """A memory operand's index register, 0 for none (capstone's eiz, the index of a padding lea
        esi, [esi + eiz], is none)."""
        index = op.mem.index
        return 0 if not index or insn.reg_name(index) == "eiz" else index

    def _base(self, insn, op, depth, values):
        """("sp", k) of the frame address a memory operand's base register holds (esp itself
        included), when there is no index; else None."""
        if op.type != X.X86_OP_MEM or self._index(insn, op) or op.mem.segment or not op.mem.base:
            return None
        name = self._reg(insn, op.mem.base)
        if name == "sp":
            return ("sp", depth)
        v = values.get(name)
        return v if isinstance(v, tuple) and v and v[0] == "sp" else None

    def _mem_slot(self, insn, op, depth, values):
        """How many bytes below the entry's stack pointer a memory operand accesses the frame, else None."""
        base = self._base(insn, op, depth, values)
        return base[1] - op.mem.disp if base is not None else None

    def _known_op(self, insn, op, depth, values):
        """What is known of a source operand's value: a constant, a frame address, a frame slot's value."""
        if op.type == X.X86_OP_IMM:
            return ("const", op.imm & M32)
        if op.type == X.X86_OP_REG:
            lane = LANES.get(insn.reg_name(op.reg))
            if lane is None or lane[1] != M32:
                return None
            if lane[0] == "sp":
                return ("sp", depth)
            return values.get(lane[0])
        if op.type == X.X86_OP_MEM and op.size == 4:
            s = self._mem_slot(insn, op, depth, values)
            if s is not None:
                return values.get(s)
        return None

    def step(self, insn, depth, values):
        m, ops = insn.mnemonic, insn.operands
        after = dict(values)
        if m in REFUSED and m in ("pushal", "popal", "enter"):
            raise Refused("0x%x %s" % (insn.address, REFUSED[m]))
        if m == "push":
            if ops[0].size != 4:
                raise Refused("0x%x pushes a word of %d bytes" % (insn.address, ops[0].size))
            new = depth + 4
            self._put(after, new, self._known_op(insn, ops[0], depth, values))
            return new, after
        if m == "pop":
            if ops[0].type == X.X86_OP_REG:
                name = self._reg(insn, ops[0].reg)
                if name == "sp":
                    raise Refused("0x%x pops into the stack pointer" % insn.address)
                self._put(after, name, values.get(depth))
            elif ops[0].type == X.X86_OP_MEM:
                s = self._mem_slot(insn, ops[0], depth + 4, values)     # (esp is incremented first)
                if s is not None:
                    self._put(after, s, values.get(depth))
            return depth - 4, after
        if m == "leave":                    # mov esp, ebp; pop ebp
            v = values.get("ebp")
            if not (isinstance(v, tuple) and v and v[0] == "sp"):
                raise Refused("0x%x leaves a frame whose address ebp does not hold" % insn.address)
            self._put(after, "ebp", values.get(v[1]))
            return v[1] - 4, after
        reads, writes = self.arch.reg_effects(insn)
        new = self.new_depth(insn, depth, values) if "sp" in writes else depth
        known = self.made_value(insn, depth, values)
        slot = self.stored_slot(insn, depth, values)
        for r in writes:
            if r not in ("sp", "pc"):
                after.pop(r, None)
        for s, size, read, written in self._accesses(None, insn, depth, values, calls=False):
            if written:                     # (what else writes a frame slot: its value is no longer known)
                for k in range(s - size - 2, s + 4):
                    after.pop(k, None)
        if self.arch.flow(insn)[0] in ("call", "icall"):
            for r in self.after_call(insn):
                after.pop(r, None)
        if known is not None:
            self._put(after, known[0], known[1])
        if slot is not None:
            self._put(after, slot[0], slot[1])
        return new, after

    def made_value(self, insn, depth, values):
        """(register, value) for a mov of a known value into a whole register (a constant, a frame
        address, a frame slot's value), lea of a frame address plus a constant, add or sub of a
        constant to a frame address."""
        m, ops = insn.mnemonic, insn.operands
        if len(ops) != 2 or ops[0].type != X.X86_OP_REG:
            return None
        lane = LANES.get(insn.reg_name(ops[0].reg))
        if lane is None or lane[1] != M32 or lane[0] == "sp":
            return None
        name = lane[0]
        if m == "mov":
            v = self._known_op(insn, ops[1], depth, values)
            return (name, v) if v is not None else None
        if m == "lea":
            base = self._base(insn, ops[1], depth, values)
            if base is not None:
                return name, ("sp", base[1] - ops[1].mem.disp)
            return None
        if m in ("add", "sub") and ops[1].type == X.X86_OP_IMM:
            mine = values.get(name)
            if isinstance(mine, tuple) and mine and mine[0] == "sp":
                k = ops[1].imm if ops[1].imm < 0x80000000 else ops[1].imm - 0x100000000
                return name, ("sp", mine[1] - k if m == "add" else mine[1] + k)
        return None

    def stored_slot(self, insn, depth, values):
        """(slot, value) of a mov of a whole word into the frame (mov dword ptr [esp + 8], eax)."""
        ops = insn.operands
        if insn.mnemonic != "mov" or len(ops) != 2 or ops[0].type != X.X86_OP_MEM or ops[0].size != 4:
            return None
        s = self._mem_slot(insn, ops[0], depth, values)
        if s is None:
            return None
        return s, self._known_op(insn, ops[1], depth, values)

    def ldm_slots(self, insn, depth, values):
        return None

    def new_depth(self, insn, depth, values):
        """The depth after an instruction that writes esp: a call (its callee returns past the return
        address it pushed), sub or add of a constant, a mov or lea of a frame address."""
        m, ops = insn.mnemonic, insn.operands
        kind = self.arch.flow(insn)[0]
        if kind in ("call", "icall"):
            return depth
        if kind == "return":
            if ops:
                raise Refused("0x%x returns and pops %d bytes of arguments (stdcall): not modelled" % (
                    insn.address, ops[0].imm))
            return depth
        if len(ops) == 2 and ops[0].type == X.X86_OP_REG and self._reg(insn, ops[0].reg) == "sp" \
                and insn.reg_name(ops[0].reg) == "esp":
            if m in ("add", "sub") and ops[1].type == X.X86_OP_IMM:
                k = ops[1].imm if ops[1].imm < 0x80000000 else ops[1].imm - 0x100000000
                return depth - k if m == "add" else depth + k
            if m == "mov":
                v = self._known_op(insn, ops[1], depth, values)
                if isinstance(v, tuple) and v and v[0] == "sp":
                    return v[1]
                if ops[1].type == X.X86_OP_MEM:
                    raise lift.StackSwitch("0x%x loads the stack pointer from memory (a context switch?): %s %s" % (
                        insn.address, insn.mnemonic, insn.op_str))
            if m == "lea":
                base = self._base(insn, ops[1], depth, values)
                if base is not None:
                    return base[1] - ops[1].mem.disp
        raise Refused("0x%x changes the stack pointer in a way not modelled: %s %s" % (
            insn.address, insn.mnemonic, insn.op_str))

    def entry_states(self, fn, given=frozenset()):
        """dread.lift's, without a register an instruction reads only to keep the bits outside the part it
        writes (the layer's merge_only: mov bl, cl; mov dl, byte ptr [eax]) where dread.regflow does not
        follow it (ebx, esi, edi, ebp) or finds no bit of it read at the entry: the C's register starts
        at 0 there, and those bits are what nothing uses (regflow's bits), or what the cases see used
        (they poison what the caller leaves in each register)."""
        undef, found = super().entry_states(fn, given)
        if not found:
            return undef, found
        flow = self.flow
        followed = set(getattr(flow, "names", ()) or ()) if flow else set()
        reads = set(flow.read_regs(fn.name)) if flow else set()
        out = {}
        for a, regs in found.items():
            merged = self.arch.merge_only(self.prog.insn(a))
            keep = frozenset(r for r in regs if r not in merged or (r in followed and r in reads))
            if keep:
                out[a] = keep
        return undef, out

    def saving_store(self, insn, values):
        """None: GCC and clang save the registers they keep with push."""
        return None

    def plain_move(self, insn):
        ops = insn.operands
        if insn.mnemonic == "mov" and len(ops) == 2 and all(o.type == X.X86_OP_REG for o in ops):
            a, b = LANES.get(insn.reg_name(ops[0].reg)), LANES.get(insn.reg_name(ops[1].reg))
            if a and b and a[1] == b[1] == M32 and "sp" not in (a[0], b[0]):
                return a[0], b[0]
        return None

    def stack_args(self, fn):
        """How many words above its return address a function reads or writes, or passes on to a tail
        call: its stack arguments (0, the count, or None when its stack pointer is not static).
        [abi] stack_args overrides it for a function whose stack pointer is not static."""
        if fn.name in self._stack_args:
            return self._stack_args[fn.name]
        declared = self.prog.target.abi.stack_args.get(fn.name)
        if declared is not None:
            self._stack_args[fn.name] = declared
            return declared
        self._stack_args[fn.name] = 0           # a recursive call counts nothing while this is worked out
        try:
            states = self.states(fn, strict=False, switch=True)
        except Refused:
            self._stack_args[fn.name] = None
            return None
        top = 0
        for a, (depth, values) in states.items():
            for s, size, read, written in self._accesses(fn, self.prog.insn(a), depth, values):
                if s - size + 1 <= -4:      # (the bytes s, s - 1, ... at addresses upwards)
                    top = max(top, (-(s - size + 1)) // 4)
        self._stack_args[fn.name] = top
        return top

    def _accesses(self, fn, insn, depth, values, calls=True):
        """[(slot, size, read, written)]: the stack bytes an instruction reads or writes whose place the
        analysis knows: memory operands through esp or through a register that holds a frame address,
        a push's and a call's store, a pop's load, and the stack words a call passes (its callee's
        stack arguments; for a tail call those above our own return address). slot: how many bytes
        below the entry's stack pointer the access starts (0 to -3: the return address; -4 and below:
        the stack arguments)."""
        m, ops = insn.mnemonic, insn.operands
        kind, target, falls = self.arch.flow(insn)
        out = []
        if m == "push":
            out.append((depth + 4, 4, False, True))
            if ops[0].type == X.X86_OP_MEM:
                s = self._mem_slot(insn, ops[0], depth, values)
                if s is not None:
                    out.append((s, 4, True, False))
            return out
        if m == "pop":
            out.append((depth, 4, True, False))
            if ops[0].type == X.X86_OP_MEM:
                s = self._mem_slot(insn, ops[0], depth + 4, values)
                if s is not None:
                    out.append((s, 4, False, True))
            return out
        if m == "leave":
            v = values.get("ebp")
            if isinstance(v, tuple) and v and v[0] == "sp":
                out.append((v[1], 4, True, False))
            return out
        if kind in ("call", "icall"):
            out.append((depth + 4, 4, False, True))     # the return address
        for k, o in enumerate(ops):
            if o.type != X.X86_OP_MEM or m in ("lea", "nop"):
                continue
            s = self._mem_slot(insn, o, depth, values)
            if s is None:
                continue
            first = k == 0 and len(ops) >= 2
            if first:
                read = m not in ("mov",) and not m.startswith("set")
                written = m not in ("cmp", "test")
            else:
                read, written = True, (m == "xchg")
            if len(ops) == 1 and m not in ("push", "pop", "call", "jmp"):     # inc, dec, neg, not, mul, div...
                read, written = True, m in ("inc", "dec", "neg", "not") or m in SHIFTS
            out.append((s, o.size, read, written))
        callee, tail = None, False
        if not calls:
            return out
        if kind == "call":
            callee = self.prog.by_entry.get(target)
        elif kind == "jump" and not falls and target is not None:
            callee, tail = self.prog.by_entry.get(target), True
            if callee is fn:
                callee = None
        if callee is not None:
            for k in range(self.stack_args(callee) or 0):
                out.append((depth - 4 - 4 * k if tail else depth - 4 * k, 4, True, False))
        return out

    # ---- the frame's top (dread.lift's saves_out) ----

    def results(self, fn, handler=None):
        return RESULTS[self._width(fn.name)]

    def frame_effects(self, fn, insn, depth, values, results, own):
        """dread.lift.FrameEffects of an x86 instruction: its memory operands (a mov of a register or a
        constant into memory is the whole-word store, push of one too, a mov from memory into a whole
        register, pop among them, the whole-word load; anything else that touches memory reads it for a
        value or writes it as part of more), the return address a call pushes, the frame words a call
        passes as stack arguments (_call, _icall), calls, returns and jumps through a register."""
        m, ops = insn.mnemonic, insn.operands
        reads, writes = self.arch.reg_effects(insn)
        fx = lift.FrameEffects(reads, writes)
        kind, target, falls = self.arch.flow(insn)
        whole_reg = lambda o: o.type == X.X86_OP_REG and LANES.get(insn.reg_name(o.reg), (0, 0))[1] == M32 \
            and self._reg(insn, o.reg) != "sp"
        if m == "push":
            plain = ops[0].type == X.X86_OP_IMM or whole_reg(ops[0])
            fx.accesses.append(("sp", None, -4, 4, "store" if plain else "other", None, False))
            if ops[0].type == X.X86_OP_REG:
                fx.stored.add(self._reg(insn, ops[0].reg))
        elif m == "pop":
            into = whole_reg(ops[0])
            fx.accesses.append(("sp", None, 0, 4, "load" if into else "other",
                                self._reg(insn, ops[0].reg) if into else None, False))
            if into:
                fx.loaded.add(self._reg(insn, ops[0].reg))
        elif m == "leave":
            fx.accesses.append(("ebp", None, 0, 4, "load", "ebp", False))
            fx.loaded.add("ebp")
        elif kind in ("call", "icall"):
            fx.accesses.append(("sp", None, -4, 4, "store", None, False))     # the return address
        for k, o in enumerate(ops):
            if o.type != X.X86_OP_MEM or m in ("lea", "nop", "push", "pop") or o.mem.segment:
                continue
            base = self._reg(insn, o.mem.base) if o.mem.base else None
            index = self._reg(insn, self._index(insn, o)) if self._index(insn, o) else None
            if base is None and index is None:
                continue                    # an absolute address: no frame
            if k == 0 and len(ops) == 2:
                plain = m == "mov" and o.size == 4 and (ops[1].type == X.X86_OP_IMM or whole_reg(ops[1]))
                fx.accesses.append((base, index, o.mem.disp, o.size, "store" if plain else "other", None, False))
                if m == "mov" and ops[1].type == X.X86_OP_REG:
                    fx.stored.add(self._reg(insn, ops[1].reg))
            else:
                into = m == "mov" and len(ops) == 2 and o.size == 4 and whole_reg(ops[0])
                fx.accesses.append((base, index, o.mem.disp, o.size, "load" if into else "other",
                                    self._reg(insn, ops[0].reg) if into else None, False))
                if len(ops) == 2 and ops[0].type == X.X86_OP_REG:
                    fx.loaded.add(self._reg(insn, ops[0].reg))
        callee = None
        if kind == "call":
            callee = self.prog.by_entry.get(target)
        elif kind == "jump" and not falls and target is not None and target not in own:
            callee = self.prog.by_entry.get(target)
        if kind in ("call", "icall"):
            fx.hands |= self._takes_eax(callee) | (set(self.leaves(callee)) if callee is not None else set())
            n = (self.stack_args(callee) or 0) if callee is not None else min(depth // 4, INDIRECT_STACK_WORDS)
            fx.words += [(4 * k, 4) for k in range(n) if 4 * k < depth]   # (_call, _icall)
            fx.writes |= set(self.after_call(insn))
            fx.loaded |= set(self.after_call(insn)) - {"flags"}
        elif kind == "return":
            fx.hands |= set(results)
        elif callee is not None:
            fx.hands |= self._takes_eax(callee) | set(self.leaves(callee))       # a tail call
        elif kind == "ijump":
            fx.hands |= set(reads) - {"sp"}
        return fx

    def _takes_eax(self, callee):
        """{"eax"} where a call hands its eax on in a way that matters: a callee that reads it at its entry
        (dread.regflow: one that hands it back as its result unwritten), or one not known; else nothing
        (the C passes eax all the same, which the callee does not read: GCC's pop eax that takes an
        argument off again before the next call is dead)."""
        if callee is None or not self.flow:
            return set(ARGS)
        return set(ARGS) & set(self.flow.read_regs(callee.name))

    def c_uses(self, fn, insn, values, results, mirror, own):
        """dread.lift's, with a call (or a tail call) reading eax only where its callee takes it
        (_takes_eax)."""
        reads, kills, ends = super().c_uses(fn, insn, values, results, mirror, own)
        kind, target, falls = self.arch.flow(insn)
        if kind in ("call", "jump") and isinstance(target, int) and "eax" in reads:
            callee = self.prog.by_entry.get(target)
            if callee is not None and not self._takes_eax(callee) and "eax" not in self.leaves(callee) \
                    and "eax" not in self.arch.reg_effects(insn)[0]:
                reads = set(reads) - {"eax"}
        return reads, kills, ends

    def compiled_isa(self):
        return "i386"

    def handler_kind(self, fn):
        return None

    def _check_returns(self, fn, states, handler=None):
        """ret at depth 0 (the return address on top)."""
        for a, (depth, values) in states.items():
            insn = self.prog.insn(a)
            if self.arch.flow(insn)[0] != "return":
                continue
            if depth != 0 or values.get(0) != self.RET:
                raise Refused("0x%x returns to an address it computed, not to its caller" % a)

    def _width(self, name):
        """How many result registers the callers read: 1 (eax) or 2 (edx:eax); both for a function that
        the hand-written C calls (src_calls): a caller the original does not have may read more."""
        if name in self.src_calls:
            return 2
        flow = self.flow
        if not flow:
            return 1
        kept, kept_safe = set(flow.kept_regs(name)), set(flow.preserved_regs(name))
        return 2 if "edx" in kept - kept_safe else 1

    def _check_kept(self, fn):
        flow = self.flow
        if flow is None:
            return
        result = set(RESULTS[self._width(fn.name)])
        written = sorted(set(flow.kept_regs(fn.name)) - result - set(flow.preserved_regs(fn.name)) - {"flags"})
        if written:
            raise Refused("its callers read %s, which it sets (%s); C returns eax and edx only" % (
                written[0], flow.why(fn.name, written[0])))

    def _check_after_call(self, callee, insn):
        """A scratch register beyond the result that the caller reads behind the call: refused, unless the
        callee leaves it as it was (dread.regflow's preserved: GCC's -fipa-ra keeps a value in edx across
        uart_putc), which the C keeps too (_call does not clear it; the callee's C is compiled with
        -fcall-saved-REG: call_saved) where it is not what the function's own caller left there."""
        flow = self.flow
        if flow is None:
            return
        result = set(RESULTS[self._width(callee.name)])
        preserved = set(flow.preserved_regs(callee.name))
        undef = self.ctx["undef"].get(insn.address, frozenset())
        for reg in flow.kept_regs(callee.name):
            if reg in result or reg == "flags" or (reg in preserved and reg not in undef):
                continue
            if (self.ctx["fn"].name, insn.address) in flow.sites_of(callee.name, reg):
                raise Refused("0x%x reads %s behind the call of %s (what the callee leaves)" % (
                    insn.address, reg, callee.name))

    def after_call(self, insn):
        return self.AFTER_CALL

    # ---- the translation ----

    def _slot(self, s):
        """The C address of the stack byte s bytes below the entry's stack pointer: in the frame array
        for s > 0, in a stack argument for s <= -4 (the parameter's own address); the return address
        (0 to -3) is refused."""
        a = self.insn_now.address
        if s > 0:
            return "(u32)(sp0 - %d)" % s
        if s > -4:
            raise Refused("0x%x reads or writes its return address" % a)
        word, off = (-s - 4) // 4, (-s - 4) % 4
        if word >= self.ctx["stack_params"]:
            raise Refused("0x%x reads or writes a stack word beyond its arguments" % a)
        return "((u32)&sa%d + %du)" % (word, off) if off else "(u32)&sa%d" % word

    def _sp(self, k=0):
        """The C address k bytes above esp now."""
        return self._slot(self.ctx["depth"][self.insn_now.address] - k)

    def _value(self, insn, op):
        """The C expression of a register operand's value (u32; a part's bits at the bottom)."""
        name = insn.reg_name(op.reg)
        lane = LANES.get(name)
        if lane is None:
            raise Refused("0x%x reads %s: not modelled" % (insn.address, name))
        full, mask, shift = lane
        if full == "sp":
            depth = self.ctx["depth"][insn.address]
            if mask != M32:
                raise Refused("0x%x reads part of esp" % insn.address)
            if depth <= 0:                  # the address of the return address: no place in the C
                raise Refused("0x%x takes the stack pointer's value at or above its entry's" % insn.address)
            return "(u32)(sp0 - %d)" % depth
        if mask == M32:
            return full
        if shift:
            return "((%s >> %d) & 0x%xu)" % (full, shift, mask >> shift)
        return "(%s & 0x%xu)" % (full, mask)

    def _write(self, insn, op, value):
        """The C that writes a register operand (a part keeps the rest)."""
        name = insn.reg_name(op.reg)
        lane = LANES.get(name)
        if lane is None:
            raise Refused("0x%x writes %s: not modelled" % (insn.address, name))
        full, mask, shift = lane
        if full == "sp":
            raise Refused("0x%x computes the stack pointer: %s %s" % (insn.address, insn.mnemonic, insn.op_str))
        if mask == M32:
            return "%s = %s;" % (full, value)
        if shift:
            return "%s = (%s & 0x%xu) | (((u32)(%s) & 0x%xu) << %d);" % (full, full, ~mask & M32, value,
                                                                         mask >> shift, shift)
        return "%s = (%s & 0x%xu) | ((u32)(%s) & 0x%xu);" % (full, full, ~mask & M32, value, mask)

    def _address(self, insn, op):
        """The C address of a memory operand."""
        mem = op.mem
        if mem.segment:
            raise Refused("0x%x addresses through a segment register" % insn.address)
        depth, values = self.ctx["states"][insn.address]
        index = self._index(insn, op)
        if not index and mem.base:
            base = self._base(insn, op, depth, values)
            if base is not None:
                return self._slot(base[1] - mem.disp)
        parts = []
        if mem.base:
            name = self._reg(insn, mem.base)
            parts.append("(u32)(sp0 - %d)" % depth if name == "sp" else name)
            if name == "sp" and depth <= 0:
                raise Refused("0x%x addresses above its entry's stack pointer through esp and an index" % insn.address)
        if index:
            name = self._reg(insn, index)
            if name == "sp":
                raise Refused("0x%x indexes with esp" % insn.address)
            parts.append("%s * %du" % (name, mem.scale) if mem.scale != 1 else name)
        if mem.disp or not parts:
            parts.append("0x%xu" % (mem.disp & M32))
        return "(u32)(%s)" % " + ".join(parts) if len(parts) > 1 else parts[0]

    @staticmethod
    def _load(address, size):
        return "(u32)*(volatile %s *)(%s)" % (UTYPES[size], address) if size != 4 else \
            "*(volatile u32 *)(%s)" % address

    @staticmethod
    def _store(address, size, value):
        return "*(volatile %s *)(%s) = (%s)(%s);" % (UTYPES[size], address, UTYPES[size], value)

    def _source(self, insn, op, size=None):
        """The C expression of a source operand's value, masked to its size."""
        size = size or op.size
        if op.type == X.X86_OP_IMM:
            return "0x%xu" % (op.imm & _mask(size))
        if op.type == X.X86_OP_REG:
            return self._value(insn, op)
        return self._load(self._address(insn, op), op.size)

    def _target_op(self, insn, op):
        """(the C of the destination's value, a function: value -> the C that writes it back, address
        statements to run first)."""
        if op.type == X.X86_OP_REG:
            return self._value(insn, op), (lambda v: self._write(insn, op, v)), []
        if op.type == X.X86_OP_MEM:
            pre = ["a0 = %s;" % self._address(insn, op)]
            return self._load("a0", op.size), (lambda v: self._store("a0", op.size, v)), pre
        raise Refused("0x%x writes an immediate" % insn.address)

    @staticmethod
    def _zs(res, size):
        """zf and sf of a result of `size` bytes."""
        return "zf = %s == 0; sf = (%s >> %d) & 1u;" % (res, res, 8 * size - 1)

    def _arith(self, insn, m, size, e, s, res="d0"):
        """The statements of a two-operand operation e OP s into res, with its flags."""
        mask, top = _mask(size), 8 * size - 1
        if m in ("add", "adc"):
            cin = " + cf" if m == "adc" else ""
            if size == 4:
                body = "{ u64 t_ = (u64)%s + %s%s; %s = (u32)t_; cf = (u32)(t_ >> 32); }" % (e, s, cin, res)
            else:
                body = "{ u32 t_ = %s + %s%s; %s = t_ & 0x%xu; cf = (t_ >> %d) & 1u; }" % (e, s, cin, res, mask,
                                                                                         8 * size)
            return "%s of = ((~(%s ^ %s) & (%s ^ %s)) >> %d) & 1u; %s" % (body, e, s, e, res, top, self._zs(res, size))
        if m in ("sub", "sbb", "cmp"):
            if m == "sbb":
                body = "{ u64 b_ = (u64)%s + cf; %s = (u32)((u64)%s - b_) & 0x%xu; cf = (u64)%s < b_; }" % (
                    s, res, e, mask, e)
            else:
                body = "%s = (%s - %s) & 0x%xu; cf = %s < %s;" % (res, e, s, mask, e, s)
            return "%s of = (((%s ^ %s) & (%s ^ %s)) >> %d) & 1u; %s" % (body, e, s, e, res, top, self._zs(res, size))
        op = {"and": "&", "or": "|", "xor": "^", "test": "&"}[m]
        return "%s = (%s %s %s) & 0x%xu; cf = of = 0; %s" % (res, e, op, s, mask, self._zs(res, size))

    def insn(self, insn):
        self.insn_now = insn
        m, ops, a = insn.mnemonic, insn.operands, insn.address
        kind, target, falls = self.arch.flow(insn)
        depth = self.ctx["depth"]
        if insn.prefix[0] and kind != "return" and m not in ("movsb", "movsd", "stosb", "stosd", "movsw", "stosw"):
            raise Refused("0x%x: %s with a prefix (lock, rep) is not modelled" % (a, m))
        if m in REFUSED:
            raise Refused("0x%x %s" % (a, REFUSED[m]))
        if m in NOPS:
            return None
        made = self.made_value(insn, *self.ctx["states"][a])
        if made is not None and made[1] and made[1][0] == "sp" and made[1][1] <= 0:
            raise Refused("0x%x takes the address of %s" % (a, "a stack argument" if made[1][1] <= -4
                                                            else "its return address"))
        if kind == "return":
            return "return %s;" % self._ret()
        if m in JUMPS:
            if target not in depth:
                raise Refused("0x%x jumps out of the function (0x%x)" % (a, target))
            return "if (%s) goto L_%x;" % (CONDITIONS[JUMPS[m]], target)
        if m in ("jecxz", "loop", "loope", "loopne"):
            raise Refused("0x%x: %s is not modelled" % (a, m))
        if m == "jmp":
            if kind == "jump":
                if target in depth:
                    return "goto L_%x;" % target
                callee = self.prog.by_entry.get(target)
                if callee is None:
                    raise Refused("0x%x jumps into other code (0x%x)" % (a, target))
                return "%s return %s;" % (self._call(callee, insn), self._ret())
            return self._indirect_jump(insn)
        if m == "call":
            if kind == "call":
                callee = self.prog.by_entry.get(target)
                if callee is None:
                    raise Refused("0x%x calls into the middle of something (0x%x)" % (a, target))
                text = self._call(callee, insn)
                return text + " for (;;) { }" if self.never_returns(callee.name) else text
            return self._icall(self._source(insn, ops[0]))
        if m == "push":
            if self._left_out(depth[a] + 4):
                return None                 # a save into a word left out (saves_out)
            return self._store(self._slot(depth[a] + 4), 4, self._source(insn, ops[0]))
        if m == "pop":
            if self._left_out(depth[a]):
                return None                 # a restore of a word left out
            value = self._load(self._slot(depth[a]), 4)
            if ops[0].type == X.X86_OP_REG:
                return self._write(insn, ops[0], value)
            return "{ s0 = %s; %s }" % (value, self._store(self._address(insn, ops[0]), 4, "s0"))
        if m == "leave":
            v = self.ctx["states"][a][1].get("ebp")
            if self._left_out(v[1]):
                return None
            return "ebp = %s;" % self._load(self._slot(v[1]), 4)
        if len(ops) == 2 and ops[0].type == X.X86_OP_REG and self._reg(insn, ops[0].reg) == "sp":
            if m in ("add", "sub", "mov", "lea"):
                return None                 # a frame adjustment: states follows it
            raise Refused("0x%x computes the stack pointer: %s %s" % (a, m, insn.op_str))
        if m == "mov":
            return self._mov(insn, ops)
        if m in ("movzx", "movsx"):
            size = ops[1].size
            v = self._source(insn, ops[1])
            if m == "movsx":
                v = "(u32)(s32)(%s)(%s)(%s)" % (STYPES[size], UTYPES[size], v)
            return self._write(insn, ops[0], v)
        if m == "lea":
            return self._write(insn, ops[0], self._address(insn, ops[1]))
        if m == "xchg":
            return self._xchg(insn, ops)
        if m in ARITH:
            return self._two(insn, m, ops)
        if m in ("inc", "dec", "neg", "not"):
            return self._one(insn, m, ops[0])
        if m in SHIFTS:
            return self._shift(insn, m, ops)
        if m in ("imul", "mul"):
            return self._mul(insn, m, ops)
        if m in ("div", "idiv"):
            return self._div(insn, m, ops[0])
        if m in SETS:
            dst, put, pre = self._target_op(insn, ops[0])
            return "{ %s }" % " ".join(pre + [put("%s ? 1u : 0u" % CONDITIONS[SETS[m]])])
        if m in MOVES:
            s = self._source(insn, ops[1])
            return "{ s0 = %s; if (%s) %s }" % (s, CONDITIONS[MOVES[m]], self._write(insn, ops[0], "s0"))
        if m == "cdq":
            return "edx = (u32)((s32)eax >> 31);"
        if m == "cwde":
            return "eax = (u32)(s32)(s16)(u16)eax;"
        if m == "cbw":
            return "eax = (eax & 0xffff0000u) | ((u32)(s32)(s8)(u8)eax & 0xffffu);"
        if m == "cwd":
            return "edx = (edx & 0xffff0000u) | ((u32)((s32)(s16)(u16)eax >> 16) & 0xffffu);"
        if m == "bswap":
            name = self._reg(insn, ops[0].reg)
            return "%s = __builtin_bswap32(%s);" % (name, name)
        if m in ("movsb", "movsw", "movsd", "stosb", "stosw", "stosd"):
            return self._string(insn, m)
        raise Refused("0x%x: %s is not modelled" % (a, m))

    def _mov(self, insn, ops):
        dst, src = ops
        if dst.type == X.X86_OP_MEM and dst.size == 4 and self._left_out(self._operand_slot(insn, dst)) \
                and src.type in (X.X86_OP_REG, X.X86_OP_IMM):
            return None                     # a save nothing reads, into a word left out (saves_out)
        if dst.type == X.X86_OP_REG and src.type == X.X86_OP_MEM and src.size == 4 \
                and self._left_out(self._operand_slot(insn, src)):
            return None                     # a restore of a word left out
        v = self._source(insn, src, dst.size)
        if dst.type == X.X86_OP_REG:
            return self._write(insn, dst, v)
        return self._store(self._address(insn, dst), dst.size, v)

    def _operand_slot(self, insn, op):
        depth, values = self.ctx["states"][insn.address]
        return self._mem_slot(insn, op, depth, values)

    def _xchg(self, insn, ops):
        a, b = ops
        if a.type == X.X86_OP_REG and b.type == X.X86_OP_REG:
            if a.reg == b.reg:
                return None                 # (xchg ax, ax: a nop)
            return "{ s0 = %s; e0 = %s; %s %s }" % (self._value(insn, a), self._value(insn, b),
                                                     self._write(insn, a, "e0"), self._write(insn, b, "s0"))
        mem, reg = (a, b) if a.type == X.X86_OP_MEM else (b, a)
        return "{ a0 = %s; s0 = %s; e0 = %s; %s %s }" % (
            self._address(insn, mem), self._value(insn, reg), self._load("a0", mem.size),
            self._store("a0", mem.size, "s0"), self._write(insn, reg, "e0"))

    def _two(self, insn, m, ops):
        dst, src = ops
        size = dst.size
        e, put, pre = self._target_op(insn, dst)
        out = list(pre)
        out.append("s0 = %s;" % self._source(insn, src, size))
        if m in ("xor", "sub") and dst.type == X.X86_OP_REG and src.type == X.X86_OP_REG and dst.reg == src.reg:
            out.append("e0 = 0u; s0 = 0u;")     # (xor eax, eax: 0, whatever eax was)
        else:
            out.append("e0 = %s;" % e)
        out.append(self._arith(insn, m, size, "e0", "s0"))
        if m not in ("cmp", "test"):
            out.append(put("d0"))
        return "{ %s }" % " ".join(out)

    def _one(self, insn, m, op):
        size = op.size
        mask, top = _mask(size), 8 * size - 1
        e, put, pre = self._target_op(insn, op)
        out = list(pre) + ["e0 = %s;" % e]
        if m == "inc":
            out.append("d0 = (e0 + 1u) & 0x%xu; of = d0 == 0x%xu; %s" % (mask, 1 << top, self._zs("d0", size)))
        elif m == "dec":
            out.append("d0 = (e0 - 1u) & 0x%xu; of = e0 == 0x%xu; %s" % (mask, 1 << top, self._zs("d0", size)))
        elif m == "neg":
            out.append("d0 = (0u - e0) & 0x%xu; cf = e0 != 0; of = e0 == 0x%xu; %s" % (mask, 1 << top,
                                                                                    self._zs("d0", size)))
        else:
            out.append("d0 = ~e0 & 0x%xu;" % mask)
        out.append(put("d0"))
        return "{ %s }" % " ".join(out)

    def _shift(self, insn, m, ops):
        dst = ops[0]
        size = dst.size
        bits, mask, top = 8 * size, _mask(size), 8 * size - 1
        count = ops[1] if len(ops) > 1 else None
        if count is None:
            k = "1u"
        elif count.type == X.X86_OP_IMM:
            if not count.imm & 31:          # a count of 0: the flags stay, a memory operand is written back
                if dst.type != X.X86_OP_MEM:
                    return None
                e, put, pre = self._target_op(insn, dst)
                return "{ %s }" % " ".join(pre + ["e0 = %s;" % e, put("e0")])
            k = "%du" % (count.imm & 31)
        else:
            k = "(ecx & 31u)"
        e, put, pre = self._target_op(insn, dst)
        out = list(pre) + ["e0 = %s; k0 = %s;" % (e, k)]
        if m in ("shl", "sal"):
            body = ("d0 = k0 < 32u ? (e0 << k0) & 0x%xu : 0u; cf = k0 <= %du ? (e0 >> (%du - k0)) & 1u : 0u; "
                    "of = ((d0 >> %d) ^ cf) & 1u; %s" % (mask, bits, bits, top, self._zs("d0", size)))
        elif m == "shr":
            body = ("d0 = k0 < 32u ? e0 >> k0 : 0u; cf = (e0 >> (k0 - 1u)) & 1u; of = (((e0 >> (k0 - 1u)) ^ d0) "
                    ">> %d) & 1u; %s" % (top, self._zs("d0", size)))
        elif m == "sar":
            sx = "(s32)(%s)(%s)e0" % (STYPES[size], UTYPES[size]) if size != 4 else "(s32)e0"
            body = ("d0 = (u32)(%s >> (k0 < 31u ? k0 : 31u)) & 0x%xu; "
                    "cf = (u32)(%s >> (k0 - 1u < 31u ? k0 - 1u : 31u)) & 1u; of = 0u; %s"
                    % (sx, mask, sx, self._zs("d0", size)))
        else:                               # rol, ror: sf and zf stay
            r = "(k0 %% %du)" % bits if bits != 32 else "k0"
            if m == "rol":
                rot = "(e0 << %s | e0 >> ((%du - %s) %% %du)) & 0x%xu" % (r, bits, r, bits, mask)
                flags = "cf = d0 & 1u; of = ((d0 >> %d) ^ cf) & 1u;" % top
            else:
                rot = "(e0 >> %s | e0 << ((%du - %s) %% %du)) & 0x%xu" % (r, bits, r, bits, mask)
                flags = "cf = (d0 >> %d) & 1u; of = ((d0 >> %d) ^ (d0 >> %d)) & 1u;" % (top, top, top - 1)
            body = "d0 = %s; %s" % (rot, flags)
        if count is not None and count.type != X.X86_OP_IMM:
            # (a count of 0 leaves the flags; the operand is written back all the same: QEMU does, a
            # read-modify-write of memory; the fuzzer found it, shl word ptr [esi - 42], cl)
            out.append("if (k0) { %s } else { d0 = e0; } %s" % (body, put("d0")))
        else:
            out.append(body)
            out.append(put("d0"))
        return "{ %s }" % " ".join(out)

    def _mul(self, insn, m, ops):
        if len(ops) == 1:                   # edx:eax (dx:ax, ax) = eax * operand
            size = ops[0].size
            s = self._source(insn, ops[0])
            if size == 4:
                if m == "mul":
                    return ("{ s0 = %s; { u64 p_ = (u64)eax * s0; eax = (u32)p_; edx = (u32)(p_ >> 32); } "
                            "cf = of = edx != 0; zf = eax == 0; sf = eax >> 31; }" % s)
                return ("{ s0 = %s; { s64 p_ = (s64)(s32)eax * (s32)s0; eax = (u32)p_; edx = (u32)((u64)p_ >> 32); "
                        "cf = of = p_ != (s64)(s32)eax; } zf = eax == 0; sf = eax >> 31; }" % s)
            if size == 2:
                if m == "mul":
                    return ("{ s0 = %s; d0 = (eax & 0xffffu) * s0; eax = (eax & 0xffff0000u) | (d0 & 0xffffu); "
                            "edx = (edx & 0xffff0000u) | (d0 >> 16); cf = of = (d0 >> 16) != 0; "
                            "zf = (d0 & 0xffffu) == 0; sf = (d0 >> 15) & 1u; }" % s)
                return ("{ s0 = %s; d0 = (u32)((s32)(s16)(u16)eax * (s32)(s16)(u16)s0); eax = (eax & 0xffff0000u) | "
                        "(d0 & 0xffffu); edx = (edx & 0xffff0000u) | (d0 >> 16); cf = of = (s32)d0 != "
                        "(s32)(s16)(u16)d0; zf = (d0 & 0xffffu) == 0; sf = (d0 >> 15) & 1u; }" % s)
            if m == "mul":
                return ("{ s0 = %s; d0 = (eax & 0xffu) * s0; eax = (eax & 0xffff0000u) | (d0 & 0xffffu); "
                        "cf = of = (d0 >> 8) != 0; zf = (d0 & 0xffu) == 0; sf = (d0 >> 7) & 1u; }" % s)
            return ("{ s0 = %s; d0 = (u32)((s32)(s8)(u8)eax * (s32)(s8)(u8)s0); eax = (eax & 0xffff0000u) | "
                    "(d0 & 0xffffu); cf = of = (s32)(s16)(u16)d0 != (s32)(s8)(u8)d0; zf = (d0 & 0xffu) == 0; "
                    "sf = (d0 >> 7) & 1u; }" % s)
        dst = ops[0]
        size = dst.size
        a = self._value(insn, dst) if len(ops) == 2 else self._source(insn, ops[1])
        b = self._source(insn, ops[1]) if len(ops) == 2 else self._source(insn, ops[2], size)
        if size == 4:
            body = ("{ s0 = %s; e0 = %s; { s64 p_ = (s64)(s32)e0 * (s32)s0; d0 = (u32)p_; "
                    "cf = of = p_ != (s64)(s32)d0; } zf = d0 == 0; sf = d0 >> 31; }" % (b, a))
        else:
            body = ("{ s0 = %s; e0 = %s; { s32 p_ = (s32)(s16)(u16)e0 * (s32)(s16)(u16)s0; d0 = (u32)p_ & 0xffffu; "
                    "cf = of = p_ != (s32)(s16)(u16)d0; } zf = d0 == 0; sf = d0 >> 15; }" % (b, a))
        return body[:-1] + " %s }" % self._write(insn, dst, "d0")

    def _div(self, insn, m, op):
        """div and idiv as themselves (a 64-bit division is a libgcc call in C), faulting where the core
        faults (a divisor of 0, a quotient too wide). The flags stay as they were: the core leaves them
        undefined, the emulator (QEMU) as they were (the fuzzer found it)."""
        size = op.size
        s = self._source(insn, op)
        if size == 4:
            return ('{ s0 = %s; __asm__ volatile ("%sl %%4" : "=a"(eax), "=d"(edx) : "0"(eax), "1"(edx), "r"(s0)); '
                    "}" % (s, m))
        if size == 2:
            return ('{ s0 = %s; { u16 q_ = (u16)eax, r_ = (u16)edx; __asm__ volatile ("%sw %%4" : "=a"(q_), '
                    '"=d"(r_) : "0"(q_), "1"(r_), "r"((u16)s0)); eax = (eax & 0xffff0000u) | q_; '
                    "edx = (edx & 0xffff0000u) | r_; } }" % (s, m))
        return ('{ s0 = %s; { u16 x_ = (u16)eax; __asm__ volatile ("%sb %%2" : "=a"(x_) : "0"(x_), "q"((u8)s0)); '
                "eax = (eax & 0xffff0000u) | x_; } }" % (s, m))

    def _string(self, insn, m):
        """movs and stos of a byte, a word or a dword, with rep (the direction flag clear: the ABI)."""
        size = {"b": 1, "w": 2, "d": 4}[m[-1]]
        if m.startswith("movs"):
            one = "%s esi += %du; edi += %du;" % (self._store("edi", size, self._load("esi", size)), size, size)
        else:
            one = "%s edi += %du;" % (self._store("edi", size, "eax"), size)
        if insn.prefix[0] == X.X86_PREFIX_REP:
            return "while (ecx) { %s ecx--; }" % one
        if insn.prefix[0]:
            raise Refused("0x%x: %s with that prefix is not modelled" % (insn.address, m))
        return "{ %s }" % one

    def _ret(self):
        if self._width(self.ctx["fn"].name) == 2:
            return "((u64)edx << 32) | eax"
        return "eax"

    def _call(self, callee, insn):
        if not IDENT.match(callee.name):
            raise Refused("0x%x calls %s, whose name is not a C identifier" % (insn.address, callee.name))
        nstack = self.stack_args(callee)
        if nstack is None:
            raise Refused("0x%x calls %s, whose stack arguments are not known" % (insn.address, callee.name))
        leaves = self.leaves(callee)
        if leaves:
            raise Refused("0x%x calls %s, which reads %s its caller leaves" % (
                insn.address, callee.name, ", ".join(leaves)))
        self._check_after_call(callee, insn)
        width = self._width(callee.name)
        d = self.ctx["depth"][insn.address]
        tail = self.arch.flow(insn)[0] == "jump"
        if tail and d:
            raise Refused("0x%x jumps to %s with %d bytes of its own on the stack (the callee returns through "
                          "them)" % (insn.address, callee.name, d))
        # the callee's stack word k: the frame word at sp + 4k when it calls, or for a tail call our own
        # stack argument k
        slots = [d - 4 - 4 * k if tail else d - 4 * k for k in range(nstack)]
        words = [self._load(self._slot(s), 4) for s in slots]
        if nstack and not tail:
            back = self._read_back(insn, slots)
            if back is not None:
                raise Refused("0x%x reads a stack word that the call at 0x%x passed to %s, which may change it "
                              "(the C passed a copy)" % (back, insn.address, callee.name))
        self.ctx["callees"].add((callee.name, nstack, width))
        call = "%s(%s)" % (callee.name, ", ".join(["eax"] + words))
        keep = set(self.flow.preserved_regs(callee.name)) if self.flow else set()
        scratch = [r for r in ("ecx", "edx") if r not in RESULTS[width] and r not in keep]
        clear = ("%s = 0; " % " = ".join(scratch) if scratch else "") + "cf = zf = sf = of = 0;"
        if width == 2:
            return "{ u64 x_ = %s; eax = (u32)x_; edx = (u32)(x_ >> 32); } %s" % (call, clear)
        return "eax = %s; %s" % (call, clear)

    def _read_back(self, insn, slots):
        """The first instruction behind a call that reads one of the frame words the call passed as
        stack arguments before something writes the whole word again, or None. A pop into a register
        nothing reads behind it is no read: GCC takes the arguments off again so (pop eax; pop edx)."""
        fn, states = self.ctx["fn"], self.ctx["states"]
        index = {a: i for i, a in enumerate(fn.insns)}
        results = self.results(fn)

        def discards(i, a):
            ops = i.operands
            if i.mnemonic != "pop" or ops[0].type != X.X86_OP_REG:
                return False
            lane = LANES.get(i.reg_name(ops[0].reg))
            return lane is not None and lane[1] == M32 and self.restore_dead(fn, states, index, a, lane[0], results)
        live = frozenset(slots)
        work = [(b, live) for b in self.successors(fn, index, insn.address, insn, False, states[insn.address][1])]
        seen = set()
        while work:
            a, live = work.pop()
            if not live or (a, live) in seen or a not in states:
                continue
            seen.add((a, live))
            i = self.prog.insn(a)
            depth, values = states[a]
            written = set()
            for s, size, read, wrote in self._accesses(fn, i, depth, values):
                if read and any(s - size + 1 <= w and w - 3 <= s for w in live) and not discards(i, a):
                    return a
                if wrote and size == 4:
                    written.add(s)
            for b in self.successors(fn, index, a, i, False, values):
                work.append((b, live - written))
        return None

    def _icall(self, fp):
        """A call through a register or memory: the lowest frame words (at most
        dread.lift.INDIRECT_STACK_WORDS), where the original's callee would find stack arguments."""
        d = self.ctx["depth"][self.insn_now.address]
        words = [self._load(self._slot(d - 4 * k), 4) for k in range(min(d // 4, INDIRECT_STACK_WORDS))]
        return ("{ s0 = %s; eax = ((u32 (*)(%s))(s0))(%s); ecx = edx = 0; cf = zf = sf = of = 0; }"
                % (fp, ", ".join(["u32"] * (1 + len(words))), ", ".join(["eax"] + words)))

    def _indirect_jump(self, insn):
        """A jump through a register or memory: a switch whose targets dread.switches finds for every
        index its bound check lets through (dread.lift's _switches: the index taken at the check, the
        targets as cases), or a tail call through a loaded pointer (GCC's dispatch: mov eax, dword ptr
        [eax*4 + commands]; pop ebp; jmp eax) with the frame gone and the return address on top: a call
        of the pointer with eax and the function's own stack arguments as they are now, its result
        returned. Refused otherwise."""
        from dread import switches
        a = insn.address
        got = self.ctx["switch"].get(a)
        if got is not None:
            check, reg, values = got
            cases = " ".join("case %d: goto L_%x;" % (k, t) for k, t in sorted(values.items()))
            return "switch (index) { %s default: return %s; }" % (cases, self._ret())
        fn = self.ctx["fn"]
        depth, values = self.ctx["states"][a]
        if depth == 0 and values.get(0) == self.RET and self._width(fn.name) == 1 \
                and switches.pointer_tail(self.prog, fn, a):
            # A tail call the compiler must make a jump (musttail), with the stack arguments where they
            # lie: GCC's with eax alone, which writes no argument word; clang's musttail takes the
            # caller's signature, so ours go along, each written back into its own slot. A compiler
            # without musttail (GCC before 15) makes no promise: refused.
            if not self.prog.target.compiler.accepts(MUSTTAIL):
                raise Refused("0x%x jumps through a pointer, and the compiler has no musttail (GCC 15 and later, "
                              "clang) to make the C's call of it a jump" % a)
            words = []
            if self.prog.target.compiler.kind == "clang":
                words = [self._load(self._slot(-4 - 4 * k), 4) for k in range(self.ctx["stack_params"])]
            return "{ s0 = %s; __attribute__((musttail)) return ((u32 (*)(%s))(s0))(%s); }" % (
                self._source(insn, insn.operands[0]), ", ".join(["u32"] * (1 + len(words))),
                ", ".join(["eax"] + words))
        raise Refused("0x%x jumps through a register or memory to targets not known" % a)

    def render(self, fn, body):
        ctx = self.ctx
        lines = ["/* %s at 0x%x, lifted by dread lift: one statement per instruction. */" % (fn.name, fn.entry),
                 '#include "dread.h"']
        widest, words = {}, {}
        for name, nstack, width in ctx["callees"]:       # one declaration: the widest of its calls
            widest[name] = max(widest.get(name, 1), width)
            words[name] = nstack
        for name, width in sorted(widest.items()):
            lines.append("extern %s %s(%s);" % (CTYPES[width], name, ", ".join(["u32"] * (1 + words[name]))))
        params = ["u32 eax_in"] + ["u32 sa%d" % k for k in range(ctx["stack_params"])]
        lines.append("%s %s(%s)" % (CTYPES[self._width(fn.name)], fn.name, ", ".join(params)))
        lines.append("{")
        lines.append("    u32 %s;" % ", ".join("%s = %s" % (r, "eax_in" if r == "eax" else "0") for r in REGS))
        lines.append("    u32 cf = 0, zf = 0, sf = 0, of = 0, s0 = 0, d0 = 0, e0 = 0, a0 = 0, k0 = 0;")
        if ctx["switch"]:
            lines.append("    u32 index = 0;")
        lines += self.fixed_lines()
        frame = (ctx["frame"] + 7) & ~7     # the frame below the saves left out (saves_out), sp0 its top
        if ctx["out"]:
            lines.append("    u8 frame[%d] __attribute__((aligned(4)));" % max(4, frame - ctx["out"]))
            lines.append("    u32 sp0 = (u32)&frame[%d] + %d;" % (frame - ctx["out"], ctx["out"]))
        else:
            lines.append("    u8 frame[%d] __attribute__((aligned(4)));" % max(8, frame))
            lines.append("    u32 sp0 = (u32)&frame[%d];" % max(8, frame))
        lines.append("    %s (void)cf; (void)zf; (void)sf; (void)of; (void)s0; (void)d0; (void)e0; (void)a0; "
                     "(void)k0; (void)sp0;" % " ".join("(void)%s;" % r for r in REGS))
        lines += body
        lines.append("}")
        return "\n".join(lines) + "\n"
