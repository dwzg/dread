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
argument); an indirect call reads nothing and ends every scratch register; an indirect jump goes on
at the function's code that no direct path reaches (the cases of a switch), and where there is none
it is taken for an indirect tail call that passes nothing on. Not seen: what the callee of an
indirect call reads, paths through code the function table does not cover, and callee-saved
registers (a function that reads one its caller left: `caller_regs` in the case files).

Output: each function whose kept registers go beyond the first result register, with the call sites
that read them; with --all every function's reads and kept. Cached in BUILD/regflow/IMAGE.json,
keyed by the image, its tables, the ABI and the source of this module and of the arch layer.

    dread regflow [--target DIR] [--image NAME] [--all] [NAME...]
"""
import argparse
import collections
import hashlib
import json
import os
import sys

from dread import target as tgt

VERSION = 1


class RegFlow:
    """The result: kept and reads per function name, with the call sites behind kept."""

    def __init__(self, names, kept, reads, sites):
        self.names = names                  # register names in bit order
        self.kept, self.reads, self.sites = kept, reads, sites

    def _regs(self, value):
        return [n for i, n in enumerate(self.names) if value >> i & 1]

    def kept_regs(self, name):
        return self._regs(self.kept.get(name, 0))

    def read_regs(self, name):
        return self._regs(self.reads.get(name, 0))

    def sites_of(self, name, reg):
        """[(caller, address)] of the calls after which `reg` of `name` is read; a tail call is
        listed with the address of its branch."""
        return [tuple(s) for s in self.sites.get(name, {}).get(reg, [])]

    def to_json(self):
        return {"names": self.names, "kept": self.kept, "reads": self.reads, "sites": self.sites}

    @classmethod
    def from_json(cls, data):
        return cls(data["names"], data["kept"], data["reads"], data["sites"])


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
    for a, insn in zip(fn.insns, insns):
        if a in joins or (out and out[-1].kind not in ("next",) and not out[-1].falls):
            state = {}
        if insn is None:
            out.append(Row(a, program.insn_size(a), "stop", None, False, 0, 0, 0, False, None, set(), 0))
            state = {}
            continue
        kind, target, falls = arch.flow(insn)
        reads, writes = arch.reg_effects(insn)
        stack = arch.stack_kind(insn)
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


def scratch_names(target, arch):
    callee_saved = set(target.abi.callee_saved)
    return [r for r in arch.general_regs() if r not in callee_saved] + ["flags"]


def analyse(program):
    """RegFlow of a Program (see the module's docstring)."""
    arch = program.arch
    names = scratch_names(program.target, arch)
    bit = {n: 1 << i for i, n in enumerate(names)}
    everything = (1 << len(names)) - 1

    def mask(regs):
        m = 0
        for r in regs:
            m |= bit.get(r, 0)
        return m

    entries = {fn.entry: fn.name for fn in program.functions}

    def entry_name(address):
        return entries.get(address & ~1) if address is not None else None

    rows = {}
    for fn in program.functions:
        rows[fn.name] = _rows(program, fn, mask, entry_name)

    # The control flow inside each function, and its calls and tail calls (fixed from here on).
    graphs = {}
    for name, code in rows.items():
        n = len(code)
        index = {row.address: i for i, row in enumerate(code)}
        succ = [[] for _ in range(n)]
        returns, calls, tails, unknown = [], [], [], False
        ijumps = []

        def nxt(i):
            return i + 1 if i + 1 < n and code[i + 1].address == code[i].address + code[i].size else None

        for i, row in enumerate(code):
            a, size, kind, target, falls = row.address, row.size, row.kind, row.target, row.falls
            follow = nxt(i)
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
                if callee is None:
                    unknown = True
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
            if kind in ("next", "call", "icall") or falls:
                if follow is not None:
                    succ[i].append(follow)
                elif kind == "next" and entry_name(a + size):
                    tails.append((i, entry_name(a + size)))     # falls into the next function
        def falls_into(i):
            return nxt(i) is not None and (code[i].kind in ("next", "call", "icall") or code[i].falls)

        if ijumps:
            seen, stack = set(), [0] if n else []
            while stack:
                i = stack.pop()
                if i not in seen:
                    seen.add(i)
                    stack.extend(succ[i])
            starts = [j for j in range(n) if j not in seen and not (j and j - 1 not in seen and falls_into(j - 1))]
            for i in ijumps:
                succ[i].extend(starts)
                if not starts:
                    unknown = True          # an indirect tail call
        graphs[name] = (succ, returns, calls, tails, unknown)

    # Registers a function may write, its callees included (least fixpoint).
    may = {}
    for name, code in rows.items():
        m = 0
        for row in code:
            m |= row.writes
        may[name] = everything if graphs[name][4] else m
    changed = True
    while changed:
        changed = False
        for name, (succ, returns, calls, tails, unknown) in graphs.items():
            m = may[name]
            for _, callee in calls + tails:
                m |= may[callee] if callee else everything
            if m != may[name]:
                may[name] = m
                changed = True

    kept = {name: 0 for name in rows}
    reads = {name: 0 for name in rows}

    def liveness(name, own=False):
        """(live at the entry, [(call index, callee, live after it)], [(index, callee)]). With own,
        returns pass nothing on: what the function itself reads, not what flows through it to the
        registers its callers read afterwards (a call site lets those through by not ending them)."""
        code = rows[name]
        succ, returns, calls, tails, _ = graphs[name]
        n = len(code)
        uses = [row.uses for row in code]
        kills = [row.kills for row in code]
        exits = [0] * n
        passed = 0 if own else kept[name]
        for i in returns:
            exits[i] |= passed
        for i, callee in tails:
            exits[i] |= reads[callee] | (passed & ~may[callee])
        for i, callee in calls:
            if callee is not None:
                uses[i] |= reads[callee] & ~code[i].covered
                kills[i] = 0 if code[i].conditional else may[callee]
            else:
                kills[i] = 0 if code[i].conditional else everything
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

    callers = collections.defaultdict(set)
    for name, (_, _, calls, tails, _) in graphs.items():
        for _, callee in calls + tails:
            if callee:
                callers[callee].add(name)
    work = collections.deque(rows)
    queued = set(rows)
    while work:
        name = work.popleft()
        queued.discard(name)
        entry_live = liveness(name, own=True)[0]
        _, calls, tails = liveness(name)
        touched = []
        if entry_live != reads[name]:
            reads[name] = entry_live
            touched += callers[name]
        for _, callee, live in calls:
            if live & ~kept[callee]:
                kept[callee] |= live
                touched.append(callee)
        for _, callee in tails:
            if kept[name] & ~kept[callee]:
                kept[callee] |= kept[name]
                touched.append(callee)
        for t in touched:
            if t not in queued:
                queued.add(t)
                work.append(t)

    sites = collections.defaultdict(lambda: collections.defaultdict(list))
    for name in rows:
        _, calls, tails = liveness(name)
        code = rows[name]
        for i, callee, live in calls:
            for reg in names:
                if live & bit[reg]:
                    sites[callee][reg].append([name, code[i].address])
        for i, callee in tails:
            for reg in names:
                if kept[name] & bit[reg]:
                    sites[callee][reg].append([name, code[i].address])
    return RegFlow(names, kept, reads, {k: dict(v) for k, v in sites.items()})


def cache_key(target, image):
    h = hashlib.sha1()
    h.update(b"dread regflow %d\0" % VERSION)
    for module in (__name__, type(image.arch).__module__):     # the analysis and the arch layer
        with open(sys.modules[module].__file__, "rb") as f:
            h.update(f.read())
    h.update(image.payload())
    for table in ("functions", "code_ranges"):
        path = image.table(table)
        if os.path.exists(path):
            with open(path, "rb") as f:
                h.update(f.read())
        h.update(b"\0")
    h.update(repr((target.abi.callee_saved, image.arch.describe(), image.base)).encode())
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
    shown = 0
    for name in names:
        kept = flow.kept_regs(name)
        extra = [r for r in kept if r != result]
        if not (args.all or args.names or extra):
            continue
        shown += 1
        print("%-28s reads %-16s kept %s" % (name, " ".join(flow.read_regs(name)) or "-", " ".join(kept) or "-"))
        for reg in extra:
            where = ", ".join("%s 0x%x" % (c, a) for c, a in flow.sites_of(name, reg)[:6])
            print("    %s read after the call by %s" % (reg, where))
    if not args.names:
        print("dread regflow: %d functions keep a register besides %s for their callers"
              % (sum(1 for n in flow.kept if [r for r in flow.kept_regs(n) if r != result]), result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
