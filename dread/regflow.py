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

VERSION = 5                 # 3: entry spills (variadic functions) are no use; 4: only a variadic save area
                            # is (-O0 keeps its arguments so and uses them), and a save around a call of a
                            # register never set before is neither a read nor a write
                            # 5: MSP430's sub and subc of a register with itself read nothing


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
        text = "read after the call by " + ", ".join("%s 0x%x" % s for s in sites[:limit])
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


Row = collections.namedtuple("Row", "address size kind target falls uses kills writes conditional stack written covered")


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
    # stored where sp points (or a copy of sp plus a constant) before anything writes them, is no use,
    # like a push: clang's variadic functions keep r1-r3 so (add r0, sp, #12; stm r0!, {r1, r2, r3})
    # where GCC pushes them, and printf made every caller of an assertion handler look as if it read
    # r3. Other stores so stay uses: -O0 keeps every argument in its frame and loads it later.
    entry, sp_based, written = True, {"sp"}, set()
    spills = hasattr(arch, "frame_stores")
    save_area = variadic_save_area(program, fn, insns, joins) if spills else set()
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
        stack = arch.stack_kind(insn)
        if entry and spills:
            spilled = {r for r in arch.frame_stores(insn, sp_based) if r not in written} & save_area
            if spilled:
                reads = set(reads) - spilled
            copy = arch.sp_copy(insn)
            sp_based -= set(writes)
            if copy is not None:
                sp_based.add(copy)
            written |= set(writes)
            if kind != "next":
                entry = False
        rm, wm = (0 if stack == "push" else mask(reads)), mask(writes)
        pred = arch.predicate(insn)
        covered = 0
        if pred is None:
            uses, kills = rm, wm
        else:
            covered = state.get(pred, 0)        # also what a predicated call's callee reads
            uses = rm & ~covered
            kills = wm & state.get(arch.complement(pred), 0)
            state[pred] = covered | wm
        out.append(Row(a, insn.size, kind, target, falls, uses, kills, wm, pred is not None, stack, writes, covered))
        if wm & flags or kind in ("call", "icall"):
            state = {}
    return out


def variadic_save_area(program, fn, insns, joins):
    """The argument registers a variadic function stores in its frame on the entry's straight run
    before anything writes them: the run of stored ones that ends with the last argument register and
    has an argument register below it that is not stored (r1-r3 or a1-a7 behind one named argument;
    sprintf also keeps its first argument there, a use: its address goes to a callee); else the empty
    set. -O0 stores every argument register from the first on, and those stay uses."""
    arch = program.arch
    args = list(program.target.abi.arg_regs)
    sp_based, written, stored = {"sp"}, set(), set()
    for a, insn in zip(fn.insns, insns):
        if insn is None or (a != fn.entry and a in joins):
            break
        kind, _, _ = arch.flow(insn)
        _, writes = arch.reg_effects(insn)
        stored |= {r for r in arch.frame_stores(insn, sp_based) if r not in written}
        copy = arch.sp_copy(insn)
        sp_based -= set(writes)
        if copy is not None:
            sp_based.add(copy)
        written |= set(writes)
        if kind != "next":
            break
    saved = stored & set(args)
    if args[-1] not in saved:
        return set()
    first = len(args) - 1
    while first > 0 and args[first - 1] in saved:
        first -= 1
    return set(args[first:]) if first > 0 else set()     # (all of them stored: -O0 keeping its arguments)


def scratch_names(target, arch):
    callee_saved = set(target.abi.callee_saved)
    return [r for r in arch.general_regs() if r not in callee_saved] + ["flags"]


POOL_REACH = 0x1000         # how far behind a function's last instruction its pool may lie


class Analysis:
    """The analysis of one program: per-instruction rows and control flow, what each function may
    write, and the solution (kept, reads). Kept in memory for explanations (readers())."""

    def __init__(self, program):
        self.program = program
        arch = program.arch
        self.names = scratch_names(program.target, arch)
        self.bit = {n: 1 << i for i, n in enumerate(self.names)}
        self.everything = (1 << len(self.names)) - 1
        entries = {fn.entry: fn.name for fn in program.functions}

        def entry_name(address):
            return entries.get(address & ~1) if address is not None else None

        self.rows = {fn.name: _rows(program, fn, self.mask, entry_name) for fn in program.functions}
        ordered = sorted(fn.entry for fn in program.functions)
        following = {a: b for a, b in zip(ordered, ordered[1:])}
        self.graphs = {fn.name: self._graph(fn, self.rows[fn.name], entry_name,
                                            following.get(fn.entry, program.end(fn)))
                       for fn in program.functions}
        for fn in program.functions:
            self._neutral_saves(fn)
        self.may = self._may_write()
        self.kept = {name: 0 for name in self.rows}
        self.reads = {name: 0 for name in self.rows}

    def mask(self, regs):
        m = 0
        for r in regs:
            m |= self.bit.get(r, 0)
        return m

    def _graph(self, fn, code, entry_name, span_end):
        """(successors, returns, calls [(index, callee or None)], tails [(index, callee)], unknown):
        the control flow inside one function. unknown: it may go where the analysis cannot follow
        (an indirect call or tail call, a branch into other code). Indirect jumps: see
        _switch_successors."""
        n = len(code)
        index = {row.address: i for i, row in enumerate(code)}
        succ = [[] for _ in range(n)]
        returns, calls, tails, ijumps, unknown = [], [], [], [], False

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
                calls.append((i, None))
                unknown = True
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
        slot_of = getattr(arch, "frame_slot", None)
        code = self.rows[fn.name]
        if slot_of is None or not code:
            return
        succ = self.graphs[fn.name][0]
        insns = [self.program.insn(row.address) for row in code]
        addressed = any(i is not None and arch.sp_copy(i) not in (None, "sp") for i in insns)
        args = set(self.program.target.abi.arg_regs) if addressed else set()
        slots = [slot_of(i) if i is not None else None for i in insns]
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
                        (insns[j] is not None and arch.sp_copy(insns[j]) not in (None, "sp")):
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
            if before[i] is None or before[i] & bit or reg in args:
                continue
            code[i] = code[i]._replace(uses=code[i].uses & ~bit)
            code[j] = code[j]._replace(kills=code[j].kills & ~bit, writes=code[j].writes & ~bit)

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
            if callee is not None:
                uses[i] |= self.reads[callee] & ~code[i].covered
                kills[i] = 0 if code[i].conditional else self.may[callee]
            else:
                kills[i] = 0 if code[i].conditional else self.everything
        return uses, kills, exits

    def liveness(self, name, own=False):
        """(live at the entry, [(call index, callee, live after it)], tails)"""
        succ, _, calls, tails, _ = self.graphs[name]
        uses, kills, exits = self.effects(name, own)
        n = len(uses)
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
        return (live_in[0] if n else 0), [(i, c, live_out[i]) for i, c in calls if c is not None], tails

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
        preserved = {name: self.kept[name] & ~self.may[name] for name in self.rows if self.kept[name] & ~self.may[name]}
        return RegFlow(self.names, dict(self.kept), dict(self.reads), {k: dict(v) for k, v in sites.items()},
                       preserved)

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
