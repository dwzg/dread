"""dread regflow: the registers a function's callers read after it returns, and those it reads at
its entry.

The calling convention says which registers survive a call (callee-saved) and which do not
(scratch: arguments, result and the rest). A compiler that sees the callee may rely on more: GCC's
-fipa-ra (on at -O2 and -Os) keeps a value in a scratch register across a call when the callee,
compiled earlier in the same file, does not touch that register. Hand-written assembly does the
same with the condition flags or a second result register. The caller then depends on what the
callee actually leaves in those registers, and a callee rewritten in C, compiled alone and by the
convention, may break it while its own tests pass: they compare the result register, memory and
the peripheral log, not the scratch registers. gizmo-a32 has one: uart_puts keeps its string
pointer in r1 across bl uart_putc.

For every function F this finds:

  kept   the scratch registers and flags some caller reads after a call to F before writing them.
         F's value of them at the return is behaviour (a result, or a register F must leave as it
         found it). dread test and dread integration compare them at the return.
  reads  the scratch registers and flags F reads at its entry before writing them: its register
         arguments, and anything else its callers have to set up.

Scratch registers are the general registers the target's [abi] does not list as callee-saved (ARM
with the default ABI: r0-r3 and r12), and the flags.

Method: register liveness over each function's instructions, solved for all functions together. A
return passes on kept[F]; a call to G uses reads[G] and ends the registers G may write on any path
(its callees included), so a register G never writes flows through the call; a branch to another
function's entry is a tail call (G returns to F's caller, so kept[G] includes kept[F]).
Conditional instructions read the flags and end nothing.

It errs on the side of finding less, so that what it finds can be compared without false alarms:
pushes do not count as reads (push {r3, lr} that only keeps the stack aligned would make r3 an
argument); an indirect call (and an svc, whose handler may return values anywhere) reads nothing
and ends every scratch register; an indirect jump goes on
at the function's code that no direct path reaches (the cases of a switch), and where there is none
it is taken for an indirect tail call that passes nothing on. Not seen: what the callee of an
indirect call reads, paths through code the function table does not cover, and callee-saved
registers (a function that reads one its caller left: `caller_regs` in the case files).

Output: each function whose kept registers go beyond the first result register, with the call sites
that read them; with --all every function's reads and kept. Cached in BUILD/regflow/IMAGE.json,
keyed by the image, its tables, its startup copies, the ABI and the source of the analysis (this module,
dread.switches and the arch layer).

    dread regflow [--target DIR] [--image NAME] [--all] [NAME...]
"""
import argparse
import collections
import hashlib
import json
import os
import sys

from dread import target as tgt

VERSION = 24                # 3: entry spills (variadic functions) are no use; 4: only a variadic save area
                            # is (-O0 keeps its arguments so and uses them), and a save around a call of a
                            # register never set before is neither a read nor a write
                            # 5: MSP430's sub and subc of a register with itself read nothing
                            # 6: a call through a register that holds one function's entry (the
                            # layer's register_call_targets: MSP430) is a call of that function, and
                            # a runtime helper's result by the ABI (abi_results) counts as read;
                            # 7: and by the target's compiler profile (dread cclab)
                            # 8: umlal and smlal (and the DSP extension's accumulating pairs) read
                            # their RdLo and RdHi, ssat16 and usat16 their source: capstone's lists
                            # left those out (dread.arch.arm.operand_roles)
                            # 9: the FPU's block transfers (vldm writes, vstm reads, vpush and vpop),
                            # vmrs APSR_nzcv writes the flags, d<n> is s<2n> and s<2n+1>
                            # 10: with hard float, s0-s15 are followed like the scratch registers
                            # 11: vpush and vpop, and libgcc's eight-byte push and pop of one register
                            # (str lr, [sp, #-8]!; ldr pc, [sp], #8: a return), are pushes and pops;
                            # a bl to a helper that calls another is a call, not a switch's dispatch
                            # 12: the MSP430's flags one by one (the layer's flag_effects): clrc writes C
                            # and passes Z, N and V through instead of reading the flags as one
                            # 13: a register such a save and restore enclose a call with is not read
                            # behind that call (the restore replaces the callee's value unread)
                            # 14: sp stays a base of frame stores when an instruction moves it (sub sp):
                            # clang's variadic save areas (sub sp, #8; strd r2, r3, [sp, #16]) went unseen
                            # 15: a copy of a base plus a constant is a base too (the layer's base_copy):
                            # clang 18 keeps a frame pointer and reaches the save area through it (add r7,
                            # sp, #8; add.w r12, r7, #8; stm.w r12, {r1, r2, r3}), unseen until then
                            # 16: a conditional branch's taken edge carries the writes made under its
                            # condition, its fall-through edge those under the opposite one (it eq; ldreq
                            # r3, [r1]; beq L; ... L: cmp r2, r3 reads no r3 of the caller: Ubuntu's and
                            # Debian's newlib strcmp, which made its callers' callers look as if they read
                            # r3 on every older-toolchain build of the benchmark)
                            # 17: a flag write under a condition keeps what was written under that condition
                            # (it ran when the condition holds afterwards: libgcc's __aeabi_fmul seemed to
                            # read r3, and every soft-float caller that kept a value in r3 was refused)
                            # 18: the ARM layer's abi_results (the RTABI's helpers: __aeabi_uldivmod
                            # returns r0-r3, __aeabi_uidivmod r0 and r1, ...)
                            # 19: such a save and restore is a copy through the slot: the save reads the
                            # register when it is live behind the restore, which writes it (newlib's
                            # _free_r spills r0 around __malloc_lock, which writes r0, and stores errno
                            # through it after: r0 was not seen read; found by dread callconv)
                            # 20: what a call itself writes (its link register) is no read of the callee's
                            # in front of the call: picolibc's _init_tls enters GCC's millicode with jal t0,
                            # __riscv_save_0, which returns through t0, and every FreeRTOS function on a
                            # path to it looked as if it read t0 (GCC 12 to 14 with picolibc: 19 functions
                            # refused for reading t0 behind vAssertCalled alone)
                            # 21: an argument register an entry push of the argument registers saved (a
                            # variadic function's save area) is read where a load at a known offset reads
                            # its slot back: GCC's printf pushes r0-r3 and loads its format from r0's slot,
                            # sprintf keeps its format r1 there (found by dread callconv on every ARM build)
                            # 22: a save area lies right below the stack arguments (the last argument
                            # register just below the entry's stack pointer): FreeRTOS's xStreamBufferSend
                            # spills its fourth argument r3 at the entry far below, and was taken for
                            # variadic, its r3 no use (dread callconv, GCC -O2)
                            # 23: an instruction that only computes callee-saved registers nothing reads
                            # afterwards reads nothing: GCC 10 on RISC-V copies a register nothing set into
                            # s0 and never reads s0 again (xTaskCheckForTimeOut: mv s0, a4), and a4 seemed an
                            # input of it and of every function on a path to it (13 refused on that build)
                            # 24: where a store or load goes in the frame says the lifter's states (Frames:
                            # sp, a frame pointer, any copy, a frame address loaded back from a slot) instead
                            # of shapes of regflow's own (the layers' sp_copy, base_copy, frame_stores and
                            # frame_slot, sp-relative only); saves and restores pair by depth


class RegFlow:
    """The result: kept, reads and preserved per function name, with the call sites behind kept
    (sites: name -> register -> [[caller, address, always]], always: read on every path after the
    call). preserved: the kept registers the function never writes, its callees included: its
    callers rely on it leaving them alone."""

    def __init__(self, names, kept, reads, sites, preserved=None):
        self.names = names                  # register names in bit order
        self.kept, self.reads, self.sites = kept, reads, sites
        self.preserved = preserved or {}

    def _regs(self, value):
        return [n for i, n in enumerate(self.names) if value >> i & 1]

    def kept_regs(self, name):
        return self._regs(self.kept.get(name, 0))

    def read_regs(self, name):
        return self._regs(self.reads.get(name, 0))

    def preserved_regs(self, name):
        return self._regs(self.preserved.get(name, 0))

    def sites_of(self, name, reg):
        """[(caller, address)] of the calls after which `reg` of `name` is read; a tail call is
        listed with the address of its branch."""
        return [tuple(s[:2]) for s in self.sites.get(name, {}).get(reg, [])]

    def always(self, name, reg):
        """Whether some caller reads `reg` on every path after its call (else only on some)."""
        return any(len(s) > 2 and s[2] for s in self.sites.get(name, {}).get(reg, []))

    def why(self, name, reg, limit=3):
        """A short text for reports: who reads the register after the call, and how."""
        sites = self.sites_of(name, reg)
        text = "read after the call by " + ", ".join("%s 0x%x" % s if s[1] else "%s (its result)" % s[0]
                                                      for s in sites[:limit])
        if len(sites) > limit:
            text += " and %d more" % (len(sites) - limit)
        if not self.always(name, reg):
            text += ", on some paths only"
        if reg in self.preserved_regs(name):
            text += "; the original leaves it unchanged"
        return text

    def to_json(self):
        return {"names": self.names, "kept": self.kept, "reads": self.reads, "sites": self.sites,
                "preserved": self.preserved}

    @classmethod
    def from_json(cls, data):
        return cls(data["names"], data["kept"], data["reads"], data["sites"], data.get("preserved"))


def argued_path(image):
    """kept.tsv beside the image's cases directory."""
    return os.path.join(os.path.dirname(image.cases), "kept.tsv")


def load_argued(image):
    """{(function, register): argument} from the image's kept.tsv: kept registers that are not
    compared, each with the argument why the difference cannot matter."""
    out = {}
    path = argued_path(image)
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                fields = line.rstrip("\n").split("\t")
                if len(fields) >= 3 and not line.startswith("#"):
                    out[(fields[0].strip(), fields[1].strip())] = fields[2].strip()
    return out


Row = collections.namedtuple("Row", "address size kind target falls uses kills writes conditional stack written covered "
                                   "fall_covered", defaults=(0,))


def _rows(program, fn, mask, entry_name):
    """The function's instructions as Rows: control flow (arch.flow), and the scratch registers
    each one reads (uses) and certainly writes (kills), as masks.

    Predication: a register written under a condition counts as written for later reads under
    the same condition, and a write under the opposite condition completes it to a write that
    ends the register, as long as the flags do not change and no other path joins in between.
    (ldrls r3, =table; ldrls r0, [r3, r0, lsl #2] reads no r3 of the caller; ite eq; moveq r0, #1;
    movne r0, #0 ends r0.)"""
    arch = program.arch
    insns = [program.insn(a) for a in fn.insns]
    joins = {fn.entry}
    for insn in insns:
        if insn is not None:
            kind, target, _ = arch.flow(insn)
            if kind == "jump" and target is not None:
                joins.add(target & ~1)
    out, state = [], {}
    flags = mask(["flags"])
    # The entry's straight run: a variadic function's save area, the unnamed argument registers
    # stored in its frame before anything writes them, is no use, like a push: clang's variadic
    # functions keep r1-r3 so (add r0, sp, #12; stm r0!, {r1, r2, r3}) where GCC pushes them, and
    # printf made every caller of an assertion handler look as if it read r3. Other stores so stay
    # uses: -O0 keeps every argument in its frame and loads it later.
    entry, written = True, set()
    frames = Frames(program, fn) if hasattr(arch, "store_slots") else None
    save_area = variadic_save_area(program, fn, insns, joins, frames) if frames is not None else set()
    for a, insn in zip(fn.insns, insns):
        if a != fn.entry and a in joins:
            entry = False
        if a in joins or (out and out[-1].kind not in ("next",) and not out[-1].falls):
            state = {}
        if insn is None:
            out.append(Row(a, program.insn_size(a), "stop", None, False, 0, 0, 0, False, None, set(), 0))
            state = {}
            continue
        kind, target, falls = arch.flow(insn)
        reads, writes = arch.reg_effects(insn)
        if hasattr(arch, "flag_effects"):      # the flags one by one (scratch_names)
            fr, fw = arch.flag_effects(insn)
            reads = (set(reads) - {"flags"}) | {"flags:" + n for n in fr}
            writes = (set(writes) - {"flags"}) | {"flags:" + n for n in fw}
        stack = arch.stack_kind(insn)
        if entry and save_area:
            spilled = {r for r, _ in frames.stores(a, insn) if r not in written} & save_area
            if spilled:
                reads = set(reads) - spilled
            written |= set(writes)
            if kind != "next":
                entry = False
        rm, wm = (0 if stack == "push" else mask(reads)), mask(writes)
        pred = arch.predicate(insn)
        covered = fall_covered = 0
        if pred is None:
            uses, kills = rm, wm
        else:
            covered = state.get(pred, 0)        # also what a predicated call's callee reads
            uses = rm & ~covered
            kills = wm & state.get(arch.complement(pred), 0)
            if kind == "jump":                  # a conditional branch: what its fall-through edge knows written
                fall_covered = state.get(arch.complement(pred), 0)
            state[pred] = covered | wm
        out.append(Row(a, insn.size, kind, target, falls, uses, kills, wm, pred is not None, stack, writes, covered,
                       fall_covered))
        if kind in ("call", "icall"):
            state = {}
        elif wm & flags:
            # New flags end what was known under every condition but the instruction's own: one
            # under a condition that holds now ran (skipped, it would have left the flags, and the
            # condition false), and so did everything before it under that condition (libgcc's
            # __aeabi_fmul: ittt ne; andsne r3, ...; teqne r2, ip; teqne r3, ip reads its own r3).
            state = {pred: state[pred]} if pred is not None else {}
    loaded = save_area_reads(program, fn, insns, frames)
    if loaded and out:                      # the push saved them for those loads: a read of each
        out[0] = out[0]._replace(uses=out[0].uses | mask(loaded))
    return out


_STATES = {}                                # id(program) -> a lifter without regflow, for its states()


def frame_states(program, fn):
    """The lifter's states of fn (dread.lift: before each instruction a path reaches, how far sp is
    below the entry's and what each register and frame slot holds; ("sp", k) is the address k bytes
    below the entry's stack pointer), up to a context switch; None when the lifter refuses the stack
    pointer's moves (start-up code that sets sp) or anything else fails."""
    from dread import lift
    lifter = _STATES.get(id(program))
    try:
        if lifter is None or lifter.prog is not program:
            lifter = _STATES[id(program)] = lift.make_lifter(program)
        return lifter.states(fn, strict=False, switch=True)
    except Exception:
        return None


class Frames:
    """One function's frame as the lifter's states see it (frame_states): which depth below the
    entry's stack pointer a register addresses, whatever it is (sp, a frame pointer, a copy of either
    plus or minus a constant, a frame address kept in a slot and loaded back). One analysis instead of
    a shape per compiler: regflow followed sp and its copies by shapes of its own (a copy of sp, then a
    copy of that copy for clang 18's frame pointer, regflow 15), each found when a compiler used one
    it lacked. ok is False without states; every answer is then the cautious one (no slot known, a
    frame address taken). The states are made when first asked for (callers check cheaply first
    whether they could matter: making them for every function took regflow from 79 to 138 seconds
    over the benchmark's builds)."""

    def __init__(self, program, fn):
        self.program, self.arch, self.fn = program, program.arch, fn
        self._states = False                # (not made yet)
        self._addressed = None

    @property
    def states(self):
        if self._states is False:
            self._states = frame_states(self.program, self.fn)
        return self._states

    @property
    def ok(self):
        return self.states is not None

    def depth(self, address, base):
        """How far below the entry's stack pointer the register base points before the instruction at
        address, or None."""
        state = self.states.get(address) if self.ok else None
        if state is None:
            return None
        depth, values = state
        value = ("sp", depth) if base == "sp" else values.get(base)
        if isinstance(value, tuple) and len(value) == 2 and value[0] == "sp":
            return value[1]
        return None

    def access(self, address, insn):
        """("store" or "load", register, depth) of a single register's store or load in the frame (the
        layer's slot_access), else None."""
        got = self.arch.slot_access(insn) if insn is not None and hasattr(self.arch, "slot_access") else None
        if got is None:
            return None
        kind, reg, base, disp = got
        k = self.depth(address, base)
        return (kind, reg, k - disp) if k is not None else None

    def stores(self, address, insn):
        """[(register, depth)] of each register a store puts in the frame (the layer's store_slots)."""
        if insn is None or not hasattr(self.arch, "store_slots"):
            return []
        out = []
        for reg, base, disp in self.arch.store_slots(insn):
            k = self.depth(address, base)
            if k is not None:
                out.append((reg, k - disp))
        return out

    def takes_address(self, address, insn):
        """Whether the instruction at address leaves a frame address in a register other than sp (a
        frame pointer, a pointer to a local); True where it cannot be told."""
        if not self.ok:
            return True
        state = self.states.get(address)
        if state is None or insn is None:
            return False                    # (no path reaches it)
        try:
            _, after = _STATES[id(self.program)].step(insn, *state)
        except Exception:
            return True
        _, writes = self.arch.reg_effects(insn)
        return any(isinstance(after.get(r), tuple) and len(after[r]) == 2 and after[r][0] == "sp"
                   for r in writes if r != "sp")

    def addressed(self):
        """Whether an instruction a path reaches takes a frame address (True without states)."""
        if self._addressed is None:
            self._addressed = not self.ok or any(self.takes_address(a, self.program.insn(a)) for a in self.states)
        return self._addressed


def save_area_reads(program, fn, insns, frames=None):
    """The argument registers a function pushes at its entry together with the last one (a variadic
    function's save area, which may hold named arguments too) and later loads back from their slots
    (Frames: through sp or any register that holds a frame address): those are read, by the load if
    not by the push. GCC's printf pushes r0-r3 and loads its format from r0's slot through a copy of
    sp; sprintf pushes r1-r3, its format in r1. The unnamed ones a va_list walks over are read through
    a pointer that moves (in a callee, mostly) and stay no use, as before (an assertion handler's
    callers would look as if they read r3). Needs the layer's pushed_registers and load_address;
    else, and without states, the empty set."""
    arch = program.arch
    if not insns or insns[0] is None or not hasattr(arch, "pushed_registers") or not hasattr(arch, "load_address"):
        return set()
    args = list(program.target.abi.arg_regs)
    pushed = arch.pushed_registers(insns[0])
    named = [r for r in pushed if r is not None]
    if not named or args[-1] not in named or not set(named) <= set(args):
        return set()
    total = arch.stack_bytes(insns[0])
    slots = {}                              # depth below the entry's stack pointer -> the register there
    for k, r in enumerate(pushed):
        if r is not None:
            slots[total - 4 * k] = r
    frames = frames if frames is not None else Frames(program, fn)
    if not frames.ok:
        return set()
    found = set()
    for a, insn in zip(fn.insns, insns):
        got = arch.load_address(insn) if insn is not None else None
        if got is None:
            continue
        base, disp = got
        k = frames.depth(a, base)
        if k is not None and k - disp in slots:
            found.add(slots[k - disp])
    return found


def _stores_on_entry(program, fn, insns, joins, reg):
    """Whether the entry's straight run stores reg (anywhere, not by a push) before it writes it."""
    arch = program.arch
    for a, insn in zip(fn.insns, insns):
        if insn is None or (a != fn.entry and a in joins):
            return False
        if arch.stack_kind(insn) != "push" and any(r == reg for r, _, _ in arch.store_slots(insn)):
            return True
        if reg in arch.reg_effects(insn)[1] or arch.flow(insn)[0] != "next":
            return False
    return False


def variadic_save_area(program, fn, insns, joins, frames=None):
    """The argument registers a variadic function stores in its frame on the entry's straight run
    before anything writes them: the run of stored ones that ends with the last argument register and
    has an argument register below it that is not stored (r1-r3 or a1-a7 behind one named argument;
    sprintf also keeps its first argument there, a use: its address goes to a callee); else the empty
    set. -O0 stores every argument register from the first on, and those stay uses. Where a store
    goes says Frames (through sp, a frame pointer or any copy: clang keeps r1-r3 with add r0, sp, #12;
    stm r0!, {r1, r2, r3}, clang 18 through its frame pointer), and each register of the area must lie
    right below the stack arguments (the last argument register a word below the entry's stack
    pointer, the one before it a word lower and so on, so that a va_list walks on into the stack
    arguments): FreeRTOS's xStreamBufferSend at GCC -O2 spills its fourth argument r3 at the entry,
    far below, and was taken for variadic, its r3 for no use (found by dread callconv). Pushes are
    left to the rows (a push is no use anyway). The empty set without states."""
    arch = program.arch
    if not hasattr(arch, "store_slots"):
        return set()
    args = list(program.target.abi.arg_regs)
    if not _stores_on_entry(program, fn, insns, joins, args[-1]):
        return set()                        # (cheap: no states for most functions)
    frames = frames if frames is not None else Frames(program, fn)
    if not frames.ok:
        return set()
    word = getattr(arch, "reg_bits", 32) // 8
    written, where = set(), {}
    for a, insn in zip(fn.insns, insns):
        if insn is None or (a != fn.entry and a in joins):
            break
        if arch.stack_kind(insn) != "push":
            for reg, depth in frames.stores(a, insn):
                if reg in args and reg not in written and reg not in where:
                    where[reg] = depth
        _, writes = arch.reg_effects(insn)
        written |= set(writes)
        if arch.flow(insn)[0] != "next":
            break
    if args[-1] not in where:
        return set()
    first = len(args) - 1
    while first > 0 and args[first - 1] in where:
        first -= 1
    area = set(args[first:]) if first > 0 else set()     # (all of them stored: -O0 keeping its arguments)
    if any(where[r] != word * (len(args) - args.index(r)) for r in area):
        return set()
    return area


def scratch_names(target, arch):
    """The registers a call may leave changed, which the analysis follows: the scratch registers,
    the flags (one by one, "flags:c" and so on, where the layer has flag_effects: Analysis folds them
    into "flags" in its result), and where floats travel in the FPU (hard float) its argument
    registers (s0-s15)."""
    callee_saved = set(target.abi.callee_saved)
    flags = ["flags:" + n for n in arch.FLAG_NAMES] if hasattr(arch, "flag_effects") else ["flags"]
    return [r for r in arch.general_regs() if r not in callee_saved] + flags + list(target.abi.fp_arg_regs)


POOL_REACH = 0x1000         # how far behind a function's last instruction its pool may lie


class Analysis:
    """The analysis of one program: per-instruction rows and control flow, what each function may
    write, and the solution (kept, reads). Kept in memory for explanations (readers())."""

    def __init__(self, program):
        self.program = program
        arch = program.arch
        self.names = scratch_names(program.target, arch)
        self.bit = {n: 1 << i for i, n in enumerate(self.names)}
        flag_bits = [b for n, b in self.bit.items() if n.startswith("flags:")]
        if flag_bits:                       # "flags" stands for them all (the layer's and callers' name)
            self.bit["flags"] = sum(flag_bits)
        self.everything = (1 << len(self.names)) - 1
        # the registers a call may leave its return address in (the layer's link_regs: RISC-V's ra and
        # t0): what the call writes there the callee reads from the call, not from the caller. Only
        # those: the MSP430 layer counts what a call clobbers as its writes, which the callee reads
        # from the caller first (its arguments)
        self.link_mask = sum(self.bit.get(r, 0) for r in getattr(arch, "link_regs", ()))
        entries = {fn.entry: fn.name for fn in program.functions}

        def entry_name(address):
            return entries.get(address & ~1) if address is not None else None

        self.rows = {fn.name: _rows(program, fn, self.mask, entry_name) for fn in program.functions}
        ordered = sorted(fn.entry for fn in program.functions)
        following = {a: b for a, b in zip(ordered, ordered[1:])}
        self.graphs = {fn.name: self._graph(fn, self.rows[fn.name], entry_name,
                                            following.get(fn.entry, program.end(fn)))
                       for fn in program.functions}
        self.edges = {name: self._edge_masks(self.rows[name], self.graphs[name][0]) for name in self.rows}
        self.shadowed = {}                  # name -> {call index: registers not read behind it} (_neutral_saves)
        self.copies = {}                    # name -> {save index: (restore index, register bit)} (_neutral_saves)
        for fn in program.functions:
            self._neutral_saves(fn)
        self._saved_reads = self._callee_saved_reads()
        for fn in program.functions:
            self._dead_saved_writes(fn)
        self.may = self._may_write()
        # a runtime helper's result by the ABI is read, whatever the original's callers do with it:
        # compiled C calls the helper by that contract (the layer's abi_results)
        abi = getattr(arch, "abi_results", None)
        compiler = getattr(program.target, "compiler", None)
        measured = compiler.profile_results() if compiler is not None else {}      # dread cclab
        self.abi, self.abi_label = {}, {}
        for name in self.rows:
            by_abi = self.mask(abi(name)) if abi is not None else 0
            by_profile = self.mask(measured.get(name, ()))
            if by_abi | by_profile:
                self.abi[name] = by_abi | by_profile
                self.abi_label[name] = {reg: "the ABI" if by_abi & self.bit[reg] else "the compiler's profile"
                                        for reg in self.names if (by_abi | by_profile) & self.bit[reg]}
        self.kept = {name: self.abi.get(name, 0) for name in self.rows}
        self.reads = {name: 0 for name in self.rows}

    def mask(self, regs):
        m = 0
        for r in regs:
            m |= self.bit.get(r, 0)
        return m

    @staticmethod
    def _edge_masks(code, succ):
        """{(branch row, successor row): registers written on that edge}: a conditional branch's taken
        edge carries what was written under its condition since the flags last changed (it eq; ldreq
        r3, [r1]; beq L: r3 is written at L by that way), its fall-through edge what was written under
        the opposite condition. Where both ways lead to the same row, neither (the write is not
        certain there)."""
        index = {row.address: i for i, row in enumerate(code)}
        out = {}
        for i, row in enumerate(code):
            if row.kind != "jump" or not row.conditional or not (row.covered or row.fall_covered):
                continue
            taken = index.get((row.target or 0) & ~1)
            fall = i + 1 if i + 1 < len(code) and code[i + 1].address == row.address + row.size else None
            if taken is None or taken == fall:
                continue
            if row.covered and taken in succ[i]:
                out[(i, taken)] = row.covered
            if row.fall_covered and fall is not None and fall in succ[i]:
                out[(i, fall)] = row.fall_covered
        return out

    def _graph(self, fn, code, entry_name, span_end):
        """(successors, returns, calls [(index, callee or None)], tails [(index, callee)], unknown):
        the control flow inside one function. unknown: it may go where the analysis cannot follow
        (an indirect call or tail call, a branch into other code). Indirect jumps: see
        _switch_successors."""
        n = len(code)
        index = {row.address: i for i, row in enumerate(code)}
        succ = [[] for _ in range(n)]
        returns, calls, tails, ijumps, unknown = [], [], [], [], False
        # a call through a register that holds one function's entry (mov #f, r10; call r10): a call
        # of f, whose result the code behind it may read (gizmo-msp430's parse_uint reads r13 of
        # __mspabi_mpyl so; missed, the multiply was lifted with a 16-bit result and accepted)
        through = getattr(self.program.arch, "register_call_targets", None)
        resolved = through(self.program, fn) if through is not None else {}

        def nxt(i):
            return i + 1 if i + 1 < n and code[i + 1].address == code[i].address + code[i].size else None

        def falls_into(i):
            return nxt(i) is not None and (code[i].kind in ("next", "call", "icall") or code[i].falls)

        for i, row in enumerate(code):
            kind, target = row.kind, row.target
            if kind == "jump":
                t = target & ~1
                if t in index:
                    succ[i].append(index[t])
                elif entry_name(t):
                    tails.append((i, entry_name(t)))
                else:
                    unknown = True          # into the middle of other code: passes nothing on
            elif kind == "call":
                callee = entry_name(target)
                calls.append((i, callee))   # None: not a function's entry, treated as indirect
                unknown |= callee is None
            elif kind == "icall":
                targets = resolved.get(row.address, ())
                callee = entry_name(targets[0]) if len(targets) == 1 else None
                calls.append((i, callee))
                unknown |= callee is None
            elif kind == "return":
                returns.append(i)
            elif kind == "ijump":
                prev = code[i - 1] if i else None
                if target and prev is not None and prev.stack == "pop" and target in prev.written:
                    returns.append(i)       # pop {rN}; bx rN: a return (interworking on ARMv4T)
                else:
                    ijumps.append(i)
            if kind in ("next", "call", "icall") or row.falls:
                if nxt(i) is not None:
                    succ[i].append(nxt(i))
                elif kind == "next" and entry_name(row.address + row.size):
                    tails.append((i, entry_name(row.address + row.size)))     # falls into the next function
        if ijumps:
            unknown |= self._switch_successors(fn, code, index, ijumps, succ, falls_into, span_end, tails,
                                               entry_name)
        return succ, returns, calls, tails, unknown

    def _switch_successors(self, fn, code, index, ijumps, succ, falls_into, span_end, tails, entry_name):
        """Give each indirect jump its targets; True when one is left without, or with a target
        outside the function that is no function's entry (the analysis cannot follow it). In this
        order:
          0. the code in front of the jump run in an emulator (dread.switches), for every index
             value its bound check lets through, or twice for a jump without one: targets in the
             function are a switch's cases, targets elsewhere tail calls (a veneer, a far jump);
          1. a table right behind the instruction (arch.switch_targets: tbb/tbh, ldr pc, add pc);
          2. a table of addresses of the function's own instructions that lies between its entry
             and the next function (in a pool), whose address one of the eight instructions before
             the jump loads or computes;
          3. all such tables of the function;
          4. the code no path reaches: first the unreached blocks no direct edge leads to (switch
             cases are reached only through the table), then one root per region still unreached,
             in address order. Not every unreached block: a loop inside a switch case would be
             entered past the instructions that set it up, and what they set up would look like
             the caller's.
        """
        program, arch = self.program, self.program.arch
        n = len(code)

        def inside(address):
            return (address & ~1) in index

        # Tables lie in the function's own extent or in the pool behind it: up to the next function,
        # but no further than POOL_REACH behind the last instruction (the next entry may be far away,
        # code the startup copies to RAM), read in one piece.
        import struct
        lo = fn.entry & ~3
        hi = min(span_end, program.end(fn) + POOL_REACH)
        try:
            blob = program.read(lo, max(0, hi - lo) & ~3)
        except Exception:
            blob = b""
        words = struct.unpack("<%dI" % (len(blob) // 4), blob)
        tables, run = {}, None
        for k, word in enumerate(words):
            a = lo + 4 * k
            if a in program.code or a + 2 in program.code:
                word = None
            if word is not None and inside(word):
                if run is None:
                    run = a
                    tables[run] = []
                tables[run].append(word & ~1)
            else:
                run = None

        def table_at(value):
            for start, targets in tables.items():
                if start <= value < start + 4 * len(targets):
                    return targets[(value - start) // 4:]
            return None

        from dread import switches
        left, lost = [], False
        for i in ijumps:
            insn = program.insn(code[i].address)
            resolved = switches.resolve(program, fn, code[i].address)
            targets = resolved[0] if resolved else None
            if resolved and resolved[1]:
                for t in resolved[1]:
                    if entry_name(t):
                        tails.append((i, entry_name(t)))
                    else:
                        lost = True
                if not targets:
                    continue
            if not targets and insn is not None:
                targets = arch.switch_targets(insn, program, inside)
            if not targets:
                for back in range(max(0, i - 8), i):
                    near = program.insn(code[back].address)
                    if near is None:
                        continue
                    isa = program.isa_at(near.address)
                    lit = arch.literal(near, isa)
                    value = arch.pc_address(near, isa)
                    if lit and lit[1] == 4 and program.in_image(lit[0], 4):
                        value = program.word(lit[0])
                    found = table_at(value & ~1) if value is not None else None
                    if found:
                        targets = found
            if not targets and tables:
                targets = [t for ts in tables.values() for t in ts]
            if targets:
                succ[i].extend(sorted({index[t & ~1] for t in targets if inside(t)}))
            else:
                left.append(i)
        if not left:
            return lost
        seen, roots = set(), []

        def mark(j):
            stack = [j]
            while stack:
                k = stack.pop()
                if k not in seen:
                    seen.add(k)
                    stack.extend(succ[k])
        if n:
            mark(0)
        # Switch cases are reached only through the table: first the unreached blocks that no
        # direct edge leads to (a loop body inside a case has its back edge), then in address order
        # whatever is still unreached.
        entered = set()
        for i in range(n):
            entered.update(succ[i])
        for pass_ in ("no predecessor", "any"):
            for j in range(n):
                if j in seen or (j and falls_into(j - 1) and j - 1 not in seen):
                    continue
                if pass_ == "no predecessor" and j in entered:
                    continue
                roots.append(j)
                mark(j)
        for i in left:
            succ[i].extend(roots)
        return lost or not roots

    SAVE_RUN = 64                       # instructions from a save to its restore at most

    def _neutral_saves(self, fn):
        """A scratch register stored in the frame while it still holds its value from the entry on every
        path, and loaded back from that slot behind a call, all on one straight run, is neither read by
        the store nor written by the load: GCC saves a register it believes live around a call so, also
        where nothing has set it (FreeRTOS's tick on RISC-V saved t1 around its assertion call, and 135
        functions looked as if their callers read t1). Every load of the slot must be the restore of such
        a pair (GCC reuses a slot for several registers' saves), no frame address may be taken on the
        run, and an argument register only counts in a function that takes no frame address at all
        (g(&a) keeps an argument so for a callee to read)."""
        arch = self.program.arch
        code = self.rows[fn.name]
        if not hasattr(arch, "slot_access") or not code:
            return
        insns = [self.program.insn(row.address) for row in code]
        raw = [arch.slot_access(i) if i is not None else None for i in insns]
        if not {s[1] for s in raw if s and s[0] == "store" and self.bit.get(s[1])} & \
                {s[1] for s in raw if s and s[0] == "load"}:
            return                          # (cheap: no states for most functions)
        frames = Frames(self.program, fn)
        if not frames.ok:
            return
        succ = self.graphs[fn.name][0]
        args = set(self.program.target.abi.arg_regs)
        slots = [frames.access(row.address, i) for row, i in zip(code, insns)]  # (by depth: any base)
        preds = [0] * len(code)
        for i in range(len(code)):
            for j in succ[i]:
                preds[j] += 1
        pairs = []                          # (save, restore, register, slot) on one run around a call
        for i, s in enumerate(slots):
            if not s or s[0] != "store" or not self.bit.get(s[1]):
                continue
            reg, offset, bit = s[1], s[2], self.bit[s[1]]
            j, called = i, False
            for _ in range(self.SAVE_RUN):
                if len(succ[j]) != 1 or preds[succ[j][0]] != 1:
                    break                   # a branch, or something joins in
                called = called or code[j].kind in ("call", "icall")
                j = succ[j][0]
                if slots[j] == ("load", reg, offset):
                    if called:
                        pairs.append((i, j, reg, offset))
                    break
                if code[j].writes & bit or "sp" in code[j].written or (slots[j] and slots[j][2] == offset) or \
                        frames.takes_address(code[j].address, insns[j]):
                    break
        restores = {j for _, j, _, _ in pairs}
        held = {s[2] for k, s in enumerate(slots) if s and s[0] == "load" and k not in restores}
        pairs = [p for p in pairs if p[3] not in held]      # a slot another load reads holds a value
        restored = {j: (i, self.bit[reg]) for i, j, reg, _ in pairs}
        start = next((i for i, row in enumerate(code) if row.address == fn.entry), None)
        if start is None or not pairs:
            return
        # Forward: the registers that may be written on some path to each row (a call writes every
        # scratch register; a restore puts back what its save found)
        before = [None] * len(code)
        before[start], work = 0, [start]
        while work:
            i = work.pop()
            out = before[i] | code[i].writes | (self.everything if code[i].kind in ("call", "icall") else 0)
            if i in restored:
                save, bit = restored[i]
                out = (out & ~bit) | ((before[save] or 0) & bit)
            for j in succ[i]:
                new = out if before[j] is None else before[j] | out
                if new != before[j]:
                    before[j] = new
                    work.append(j)
        for i, j, reg, offset in pairs:
            bit = self.bit[reg]
            if before[i] is None or before[i] & bit or (reg in args and frames.addressed()):
                continue
            # A copy through the slot: the save reads the register only where something reads the
            # restored value (liveness: what is live behind the restore is live in front of the save),
            # and the restore writes it within the function (a callee between may have changed it:
            # newlib's __malloc_lock writes r0, which _free_r saves around it and stores errno through
            # after). For the function's callers the restore puts back what the save found: no write.
            code[i] = code[i]._replace(uses=code[i].uses & ~bit)
            code[j] = code[j]._replace(writes=code[j].writes & ~bit)
            self.copies.setdefault(fn.name, {})[i] = (j, bit)
            # The restore passes the entry's value on, so the register looked live behind the calls
            # the pair encloses, as if their callers read what the callee left there (FreeRTOS on
            # RISC-V saves a2 around its assertion call: 23 functions refused for reading a2 behind
            # vAssertCalled). Behind such a call the restore replaces the callee's value before
            # anything reads it: not read behind that call.
            path, k = [i], i
            while k != j:
                k = succ[k][0]
                path.append(k)
            for n, c in enumerate(path[:-1]):
                if code[c].kind in ("call", "icall") and not any(code[q].uses & bit for q in path[n + 1:-1]):
                    shadow = self.shadowed.setdefault(fn.name, {})
                    shadow[c] = shadow.get(c, 0) | bit

    def _saved_liveness(self, fn, summary, reads_only=False):
        """(live after each row, live at the entry, the rows' (reads, writes)) of the callee-saved
        registers inside fn, as bit masks over _saved_names: live where it returns or tail-calls (its
        caller has them back), used at a call as far as the callee reads them from its caller
        (summary; all of them for a call through a register), ended by a write not under a condition.
        A push is no use (it saves them, as regflow takes pushes of scratch registers). reads_only:
        what fn reads of its caller's (_callee_saved_reads): a return passes nothing on, and a store
        of one into the frame is no use either (a save: RISC-V's sw s0, 8(sp)). None for a function
        that may go where the analysis cannot follow."""
        arch, code = self.program.arch, self.rows[fn.name]
        succ, returns, calls, tails, unknown = self.graphs[fn.name]
        if unknown or not code:
            return None
        bit = self._saved_bit
        every = (1 << len(bit)) - 1
        access = getattr(arch, "slot_access", None) if reads_only else None
        frames = None
        n = len(code)
        uses, kills, effects = [every] * n, [0] * n, [None] * n
        for i, row in enumerate(code):
            insn = self.program.insn(row.address)
            if insn is None:
                continue
            reads, writes = arch.reg_effects(insn)
            effects[i] = (reads, writes)
            slot = access(insn) if access is not None else None
            save = slot is not None and slot[0] == "store" and slot[1] in bit
            if save and slot[2] != "sp":        # (through a frame pointer or another copy: Frames)
                frames = frames or Frames(self.program, fn)
                save = frames.depth(row.address, slot[2]) is not None
            uses[i] = 0 if row.stack == "push" or save else sum(bit.get(r, 0) for r in reads)
            kills[i] = 0 if row.conditional else sum(bit.get(r, 0) for r in writes)
        exits = [0] * n
        if not reads_only:
            for i in returns:
                exits[i] = every
            for i, _ in tails:
                exits[i] = every
        for i, callee in calls:
            uses[i] |= summary.get(callee, every) if callee else every
        live_in, live_out = [0] * n, [0] * n
        changed = True
        while changed:
            changed = False
            for i in range(n - 1, -1, -1):
                o = exits[i]
                for j in succ[i]:
                    o |= live_in[j]
                v = uses[i] | (o & ~kills[i])
                if o != live_out[i] or v != live_in[i]:
                    live_out[i], live_in[i], changed = o, v, True
        return live_out, live_in[0], effects

    def _callee_saved_reads(self):
        """{name: the callee-saved registers fn reads from its caller} (bit masks over _saved_names),
        all functions together: an ordinary function saves what it uses and reads none; a compiler's
        shared code reads its caller's (IAR's ?Subroutine0: mov r1, r6). A function that may go where
        the analysis cannot follow reads all of them."""
        saved = [r for r in self.program.target.abi.callee_saved if r != "sp"]
        self._saved_names = saved
        self._saved_bit = {r: 1 << k for k, r in enumerate(saved)}
        every = (1 << len(saved)) - 1
        summary = {name: 0 for name in self.rows}
        changed = True
        while changed:
            changed = False
            for fn in self.program.functions:
                got = self._saved_liveness(fn, summary, reads_only=True)
                new = every if got is None else got[1]
                if new != summary[fn.name]:
                    summary[fn.name], changed = new, True
        return summary

    def _dead_saved_writes(self, fn):
        """An instruction that only computes callee-saved registers (the layer's register_only: no
        memory, no branch) whose results nothing reads before the function puts them back reads none of
        its registers. GCC 10 on RISC-V copies a register nothing set into s0 and never reads s0 again
        (FreeRTOS's xTaskCheckForTimeOut: c.mv s0, a4 behind its critical section's entry), and a4
        seemed an input of it, and through it of vAssertCalled's callers: 13 functions of that build
        refused for registers their callers seemed to read. The callee-saved registers are followed
        inside the function (_saved_liveness), a call using what its callee reads from its caller
        (_callee_saved_reads)."""
        arch = self.program.arch
        only = getattr(arch, "register_only", None)
        code = self.rows[fn.name]
        if only is None or not code or not self._saved_bit:
            return
        succ = self.graphs[fn.name][0]
        reached = {j for ss in succ for j in ss} | {0}
        if len(reached) < len(code):
            # code no edge leads to: a switch whose cases were not all found (GCC 12's RISC-V
            # xTaskGenericNotifyFromISR: one case of five), so what is live there would be lost
            return
        got = self._saved_liveness(fn, self._saved_reads)
        if got is None:
            return
        live_out, _, effects = got
        saved, bit = set(self._saved_names), self._saved_bit
        insns = [self.program.insn(row.address) for row in code]
        for i, (row, insn) in enumerate(zip(code, insns)):
            if insn is None or effects[i] is None or row.kind != "next" or not only(insn):
                continue
            written = set(effects[i][1])
            if not written or written - saved:
                continue                    # it writes something else too (a scratch register, the flags)
            if live_out[i] & sum(bit[r] for r in written):
                continue
            code[i] = code[i]._replace(uses=0)

    def _may_write(self):
        """Registers each function may write, its callees included (least fixpoint)."""
        may = {}
        for name, code in self.rows.items():
            m = 0
            for row in code:
                m |= row.writes
            may[name] = self.everything if self.graphs[name][4] else m
        changed = True
        while changed:
            changed = False
            for name, (_, _, calls, tails, _) in self.graphs.items():
                m = may[name]
                for _, callee in calls + tails:
                    m |= may[callee] if callee else self.everything
                if m != may[name]:
                    may[name], changed = m, True
        return may

    def effects(self, name, own=False):
        """(uses, kills, exits) per instruction of a function with the current solution: calls use
        what the callee reads and end what it may write; returns and tail calls pass on what the
        function's callers read afterwards (exits). With own, returns pass nothing on: what the
        function itself reads, not what flows through it to its callers' later reads (a call site
        lets those through by not ending them)."""
        code = self.rows[name]
        _, returns, calls, tails, _ = self.graphs[name]
        uses = [row.uses for row in code]
        kills = [row.kills for row in code]
        exits = [0] * len(code)
        passed = 0 if own else self.kept[name]
        for i in returns:
            exits[i] |= passed
        for i, callee in tails:
            exits[i] |= self.reads[callee] | (passed & ~self.may[callee])
        for i, callee in calls:
            if callee is not None:      # (the call writes its link register before the callee reads it)
                uses[i] |= self.reads[callee] & ~code[i].covered & ~(code[i].writes & self.link_mask)
                kills[i] = 0 if code[i].conditional else self.may[callee]
            else:
                kills[i] = 0 if code[i].conditional else self.everything
        return uses, kills, exits

    def liveness(self, name, own=False):
        """(live at the entry, [(call index, callee, live after it)], tails)"""
        succ, _, calls, tails, _ = self.graphs[name]
        uses, kills, exits = self.effects(name, own)
        edges = self.edges.get(name, {})
        copies = self.copies.get(name, {})
        n = len(uses)
        live_in, live_out = [0] * n, [0] * n
        changed = True
        while changed:
            changed = False
            for i in range(n - 1, -1, -1):
                o = exits[i]
                for j in succ[i]:
                    o |= live_in[j] & ~edges.get((i, j), 0)
                v = uses[i] | (o & ~kills[i])
                if i in copies:             # a save: it reads what the restore's readers read (_neutral_saves)
                    j, bit = copies[i]
                    v |= live_out[j] & bit
                if o != live_out[i] or v != live_in[i]:
                    live_out[i], live_in[i], changed = o, v, True
        shadow = self.shadowed.get(name, {})
        return (live_in[0] if n else 0), [(i, c, live_out[i] & ~shadow.get(i, 0)) for i, c in calls if c is not None], \
            tails

    def solve(self):
        callers = collections.defaultdict(set)
        for name, (_, _, calls, tails, _) in self.graphs.items():
            for _, callee in calls + tails:
                if callee:
                    callers[callee].add(name)
        work = collections.deque(self.rows)
        queued = set(self.rows)
        while work:
            name = work.popleft()
            queued.discard(name)
            entry_live = self.liveness(name, own=True)[0]
            _, calls, tails = self.liveness(name)
            touched = []
            if entry_live != self.reads[name]:
                self.reads[name] = entry_live
                touched += callers[name]
            for _, callee, live in calls:
                if live & ~self.kept[callee]:
                    self.kept[callee] |= live
                    touched.append(callee)
            for _, callee in tails:
                if self.kept[name] & ~self.kept[callee]:
                    self.kept[callee] |= self.kept[name]
                    touched.append(callee)
            for t in touched:
                if t not in queued:
                    queued.add(t)
                    work.append(t)
        return self

    def result(self):
        sites = collections.defaultdict(lambda: collections.defaultdict(list))
        for name, code in self.rows.items():
            _, calls, tails = self.liveness(name)
            for i, callee, live in calls:
                for reg in self.names:
                    if live & self.bit[reg]:
                        sites[callee][reg].append([name, code[i].address, self.read_always(name, i, reg)])
            for i, callee in tails:
                for reg in self.names:
                    if self.kept[name] & self.bit[reg]:
                        sites[callee][reg].append([name, code[i].address, True])
        for name, labels in self.abi_label.items():
            for reg, label in labels.items():
                sites[name][reg].append([label, 0, True])
        preserved = {name: self.kept[name] & ~self.may[name] for name in self.rows if self.kept[name] & ~self.may[name]}
        return self._folded(self.names, dict(self.kept), dict(self.reads), {k: dict(v) for k, v in sites.items()},
                            preserved)

    def _folded(self, names, kept, reads, sites, preserved):
        """The RegFlow, with flags followed one by one folded into "flags" (a flag read is the flags
        read: what the layers and the tools name)."""
        if not any(n.startswith("flags:") for n in names):
            return RegFlow(names, kept, reads, sites, preserved)
        public = []
        for n in names:
            n = "flags" if n.startswith("flags:") else n
            if n not in public:
                public.append(n)
        moved = {1 << i: 1 << public.index("flags" if n.startswith("flags:") else n) for i, n in enumerate(names)}

        def fold(m):
            out = 0
            for bit, to in moved.items():
                if m & bit:
                    out |= to
            return out
        folded_sites = {}
        for callee, by_reg in sites.items():
            regs = {}
            for reg, entries in by_reg.items():
                key = "flags" if reg.startswith("flags:") else reg
                merged = {(e[0], e[1]): e for e in regs.get(key, [])}
                for e in entries:
                    k = (e[0], e[1])
                    merged[k] = [e[0], e[1], bool(merged[k][2] or e[2])] if k in merged else list(e)
                regs[key] = list(merged.values())
            folded_sites[callee] = regs
        return RegFlow(public, {k: fold(v) for k, v in kept.items()}, {k: fold(v) for k, v in reads.items()},
                       folded_sites, {k: fold(v) for k, v in preserved.items() if fold(v)})

    def read_always(self, name, start, reg):
        """Whether every path from the instruction behind index `start` reads `reg` before
        something ends it or the function is left without passing it on (a return passes it on
        when the function's own callers read it)."""
        code = self.rows[name]
        succ, returns, _, tails, _ = self.graphs[name]
        uses, kills, exits = self.effects(name)
        b = self.bit[reg]
        returns, tail_at = set(returns), dict(tails)
        seen, stack = set(), list(succ[start])
        if not stack:
            return False
        while stack:
            j = stack.pop()
            if j in seen:
                continue
            seen.add(j)
            if uses[j] & b:
                continue
            if kills[j] & b:
                return False
            if j in returns or j in tail_at:
                if not exits[j] & b:
                    return False
                continue
            if not succ[j]:
                return False
            stack.extend(succ[j])
        return True

    def readers(self, name, address, reg):
        """Where `reg` is read after the call (or tail call) at `address` in function `name`:
        [("read", address)] for an instruction that reads it, ("return", address) for a return
        that passes it on to the function's own callers, ("tail", address, callee) for a tail call
        that passes it on (the callee reads it or lets it through to the callers)."""
        code = self.rows[name]
        succ, returns, calls, tails, _ = self.graphs[name]
        index = {row.address: i for i, row in enumerate(code)}
        b = self.bit[reg]
        uses, kills, _ = self.effects(name)
        returns, tail_at = set(returns), dict(tails)
        start = index[address]
        if start in tail_at:
            return [("tail", address, tail_at[start])]
        out, seen, stack = [], set(), list(succ[start])
        while stack:
            j = stack.pop()
            if j in seen:
                continue
            seen.add(j)
            if uses[j] & b:
                out.append(("read", code[j].address))
                continue
            if kills[j] & b:
                continue
            if j in returns and self.kept[name] & b:
                out.append(("return", code[j].address))
            if j in tail_at and (self.reads[tail_at[j]] | (self.kept[name] & ~self.may[tail_at[j]])) & b:
                out.append(("tail", code[j].address, tail_at[j]))
            stack.extend(succ[j])
        return sorted(out)


def analyse(program):
    """RegFlow of a Program (see the module's docstring)."""
    return Analysis(program).solve().result()


def cache_key(target, image):
    h = hashlib.sha1()
    h.update(b"dread regflow %d\0" % VERSION)
    from dread import switches
    from dread import program as program_module
    for module in (__name__, switches.__name__, type(image.arch).__module__, program_module.__name__):
        # the analysis, the arch layer and the decoder (it marks calls through a register)
        with open(sys.modules[module].__file__, "rb") as f:
            h.update(f.read())
    h.update(image.payload())
    for table in ("functions", "code_ranges"):
        path = image.table(table)
        if os.path.exists(path):
            with open(path, "rb") as f:
                h.update(f.read())
        h.update(b"\0")
    copies = [(c.kind, c.run, c.size, c.stored) for c in image.startup()]
    h.update(repr((target.abi.callee_saved, image.arch.describe(), image.base, copies)).encode())
    profile = getattr(getattr(target, "compiler", None), "profile", None)
    if profile and os.path.exists(profile):                 # the compiler's profile (dread cclab)
        with open(profile, "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def load(target, image, program=None):
    """The RegFlow of an image, from the cache or analysed (and cached)."""
    path = os.path.join(target.build, "regflow", image.name + ".json")
    key = cache_key(target, image)
    try:
        with open(path) as f:
            data = json.load(f)
        if data.get("key") == key:
            return RegFlow.from_json(data["flow"])
    except (OSError, ValueError, KeyError):
        pass
    if program is None:
        from dread.program import Program
        program = Program(target, image)
    flow = analyse(program)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w") as f:
        json.dump({"key": key, "flow": flow.to_json()}, f, sort_keys=True)
    os.replace(tmp, path)
    return flow


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread regflow", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--all", action="store_true", help="every function's reads and kept")
    parser.add_argument("--why", action="store_true",
                        help="for the named functions: the instructions behind each kept register")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        flow = load(target, image)
    except tgt.TargetError as e:
        print("dread regflow: %s" % e, file=sys.stderr)
        return 1
    result = target.abi.result[0] if target.abi.result else None
    names = args.names or sorted(set(flow.kept) | set(flow.reads))
    analysis = None
    if args.why:
        from dread.program import Program
        analysis = Analysis(Program(target, image)).solve()
    for name in names:
        kept = flow.kept_regs(name)
        extra = [r for r in kept if r != result]
        if not (args.all or args.names or extra):
            continue
        print("%-28s reads %-16s kept %s" % (name, " ".join(flow.read_regs(name)) or "-", " ".join(kept) or "-"))
        for reg in extra:
            sites = flow.sites_of(name, reg)
            if analysis is None:
                print("    %s read after the call by %s" % (reg, ", ".join("%s 0x%x" % s for s in sites[:6])))
                continue
            print("    %s:" % reg)
            for caller, address in sites:
                if caller not in analysis.rows:     # "the ABI" or "the compiler's profile" (abi_label)
                    print("        by %s: read after every call" % caller)
                    continue
                for what in analysis.readers(caller, address, reg):
                    if what[0] == "read":
                        text = "read at 0x%x" % what[1]
                    elif what[0] == "return":
                        text = "passed on by the return at 0x%x to the callers of %s" % (what[1], caller)
                    else:
                        text = "passed on by the tail call at 0x%x to %s" % (what[1], what[2])
                    print("        after %s 0x%x: %s" % (caller, address, text))
    if not args.names:
        print("dread regflow: %d functions keep a register besides %s for their callers"
              % (sum(1 for n in flow.kept if [r for r in flow.kept_regs(n) if r != result]), result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
