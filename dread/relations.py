"""dread relations: a function's neighbourhood as a graph, the pieces of a puzzle and how they join.

The nodes are the function, its callers and callees up to a depth (the direct calls and tail calls
dread flat finds: callers upwards, callees downwards, not the other callers of a callee), and what
each of them touches:

- memory at fixed addresses, named as readable C names it (dread.naming: g_ in RAM, io_ for a
  device register, rom_ in a rom region; a symbol's name or the readable_names table's where there
  is one): an access whose address is a constant, or a constant plus something (an array or a
  structure reached from its base: the sum of a sum's constants, which is how GCC's section anchors
  reach several objects from one base), reads, writes or both; an address the C only names (passed
  on, kept) "uses" it;
- texts and functions its constants point to (dread.constants pointed): a text, a function's entry
  (its address taken: a callback, a task), or a table of texts and functions, which is a node of its
  own that refers to them (only its first POINTED_WORDS words are read);
- the structures its variables point to (the readable_types plan, dread.shapes, only where the plan
  was made for the C the function has now) and the structures their fields point to.

All of it is read from the function's readable C (dread.readable, made here when no tool made it:
the stages without names, as dread pilot's questions show it). Beyond an access at a constant
address, a constant counts as an address only where the machine code makes it as compilers make
addresses (anchoring: a literal pool's word, an address computed from the pc, a fixed address it
loads from, or a RAM address shortly after one of these): a count, an offset or a mask is not taken
for one, also where the image begins at 0 and small numbers point into it. A function the lifter
refuses, or whose C is not the lifters' dialect, is read from its listing instead: the addresses its
code makes, each only "uses".

Each node says how sure its name is: named (a symbol: a person's or the ELF's; a function's own name
unless it is FUN_ and the like, dread.samecode.UNNAMED), proposed (dread pilot's: names.tsv for
functions, the readable_names table for memory and structures) or unnamed (FUN_..., g_..., s_N); and
its certainty (certainty(), CERTAINTY: from none through unvoted, unsure, likely and sure to named), a
proposal's from its vote's agreement (names.tsv's confidence: sure where every answer agreed, unsure
where the vote alone makes dread pilot --task review challenge it). A function's node also says what
the review's rows say of the name it goes by now (dread.namereview standing: flagged, suspect,
changed, confirmed, with the reasons and the old name).

Not established: an address the code makes may still be a number (a large constant from a literal
pool); an indexed access is shown at its base; an address passed as an immediate (MSP430's
#address) is missed; a call through a pointer is seen only where the address is taken; what runs
is not looked at. Making readable C costs 0.1-0.5 s a function on a FreeRTOS build (its clean-up,
dread.tidy; several seconds for the largest): the facts are kept per function (Facts), dread gui
makes them in the background and fills the graph in.

flow_references reads the same kinds of facts from the machine code alone, by following the
constants its registers hold (a few milliseconds a function): dread groups' evidence by default.

    dread relations [--target DIR] [--image NAME] [--depth N] [--json] NAME|ADDRESS
"""
import argparse
import heapq
import json
import os
import sys

from dread import cparse as C
from dread import target as tgt
from dread.constants import SMALLEST_POINTER, pointed

DEPTH = 1               # callers and callees this many calls away (default)
MAX_DEPTH = 3
MAX_FUNCTIONS = 150     # function nodes at most, nearest first: a hub's callers two calls up fill no page
MAX_NODES = 400         # nodes at most; what does not fit is counted in left_out
SIDES = ("center", "caller", "callee", "both", "referred")
SUMS = ("+", "-")
NUMBERS = {"&", "|", "^", "<<", ">>", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=", "&&", "||", "~", "!",
           "neg", "[]", "?"}    # what a constant is an operand of when it is a number, not an address
SIZES = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "u64": 8, "s64": 8}
MASK = 0xFFFFFFFF


# ---- calls ----

def call_maps(program, callers=None):
    """(up, down): {callee: {caller: [call addresses]}} and {caller: {callee: [addresses]}} of the
    direct calls and tail calls (dread.flat callers_of: given, or found now)."""
    if callers is None:
        from dread.flat import callers_of
        callers = callers_of(program)
    up, down = {}, {}
    for callee, sites in sorted(callers.items()):
        for caller, address in sites:
            up.setdefault(callee, {}).setdefault(caller, []).append(address)
            down.setdefault(caller, {}).setdefault(callee, []).append(address)
    return up, down


def neighbourhood(program, center, depth, up, down, limit=MAX_FUNCTIONS):
    """({name: (distance, side)}, names left out): the center, its callers up to depth calls up and
    its callees as far down, nearest first and by address at one distance, at most limit functions.
    side: "center", "caller", "callee", or "both" for one that is both (a recursion through two)."""
    def entry(name):
        fn = program.by_name.get(name)
        return (fn.entry if fn is not None else -1, name)
    found = {center: (0, "center")}
    left = set()
    frontier = {"caller": [center], "callee": [center]}
    for distance in range(1, depth + 1):
        for side, links in (("caller", up), ("callee", down)):
            nxt = []
            for name in frontier[side]:
                for other in sorted(links.get(name, {}), key=entry):
                    if other in found:
                        d, s = found[other]
                        if s not in (side, "center"):
                            found[other] = (d, "both")
                        continue
                    if len(found) >= limit:
                        left.add(other)
                        continue
                    found[other] = (distance, side)
                    nxt.append(other)
            frontier[side] = nxt
    return found, sorted(left - set(found))


# ---- what a function touches ----

def _access(e):
    """(address expression, type) when e is an access through a pointer of the lifters' dialect,
    *(volatile u32 *)(...)."""
    from dread.naming import ACCESS
    if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast):
        m = ACCESS.match(" ".join(e.e.type.split()))
        if m:
            return e.e.e, m.group(1)
    return None


def _split(e):
    """(the sum of a sum's constants or None, the constants, its other terms) for + and -, nested."""
    terms, rest, stack = [], [], [(e, 1)]
    while stack:
        x, sign = stack.pop()
        if isinstance(x, C.Binary) and x.op in SUMS:
            stack.append((x.r, sign if x.op == "+" else -sign))
            stack.append((x.l, sign))
        elif isinstance(x, C.Num) and isinstance(x.value, int):
            terms.append(sign * x.value)
        else:
            rest.append(x)
    return (sum(terms) if terms else None), terms, rest


def materialized(program, fn):
    """The addresses a function's machine code makes as compilers make addresses (the arch layer's
    literal and pc_address): the words of its literal pools, addresses computed from the pc (adr,
    auipc or lui with addi), the fixed addresses it loads from, and the constants pairs of
    instructions make whole (the arch layer's wide_constants, where it has one: ARM's movw with
    movt, clang's way of making an address). Immediates are not among them: a count, an offset or a
    mask is made so, and in an image that starts at 0 it would point into it (MSP430's #address is
    missed so)."""
    arch, out = program.arch, set()
    insns = list(program.instructions(fn))
    for insn in insns:
        if insn is None:
            continue
        isa = program.isa_at(insn.address)
        lit = arch.literal(insn, isa)
        if lit is not None:
            out.add(lit[0])
            if lit[1] == 4 and program.in_image(lit[0], 4):
                out.add(program.word(lit[0]))
        at = arch.pc_address(insn, isa)
        if at is not None:
            out.add(at)
    wide = getattr(arch, "wide_constants", None)
    if wide is not None:
        out.update(wide(insns))
    return out


ANCHOR_SPAN = 0x1000    # an address in RAM this far after one the code makes may be made from it


def anchoring(program, made):
    """anchored(value, terms) for c_references: whether a constant is an address the function's
    machine code makes (made: materialized()): one of the sum's constants is one, or the value lies
    in RAM or a device's range at most ANCHOR_SPAN after one in the same region (a base and an offset
    folded into one constant: gizmo's main passes g_rx as 0x20000050u, made from g_ticks's
    0x2000004c). Not so in rom or the image, where counts and offsets point when the image begins
    at 0."""
    import bisect
    memory = program.target.memory

    def region(a):
        if memory is None:
            return None
        for lo, hi in memory.io:
            if lo <= a < hi:
                return ("io", lo)
        r = memory.region(a)
        return ("ram", r.base) if r is not None and r.kind != "rom" else None
    near = sorted(m for m in made if region(m) is not None)

    def anchored(value, terms):
        if any((t & MASK) in made for t in terms if t > 0):
            return True
        here = region(value)
        i = bisect.bisect_right(near, value) - 1
        return here is not None and i >= 0 and value - near[i] < ANCHOR_SPAN and region(near[i]) == here
    return anchored


def c_references(fn, anchored=None):
    """{(address, type, indexed): [read, write]}: what a function's C (a dread.cparse Function) does
    at fixed addresses. An access whose address is a constant (indexed False: what dread.naming
    globals_in finds and names) or a constant plus something (indexed True, at the sum of the
    sum's constants: an array or a structure from its base; GCC's section anchors reach several
    objects from one base so) reads, writes or both (=, op=, ++); any other constant, or sum of
    constants, of at least SMALLEST_POINTER is only named (type None, neither read nor written).
    anchored(value, terms) (anchoring(): the addresses the machine code makes; None: any constant)
    decides what is an address beyond the accesses at a constant address, so that a count, an offset
    or a mask is not taken for one. Left out
    too: constants in masks, shifts, products, comparisons, indices and case labels, sums below zero
    (an offset from the frame), and a constant that only goes into a variable the function never
    reads (a register the lifter keeps for a caller: gizmo's cmd_mode, r3 = 0x20000030u)."""
    from dread.naming import _exprs, _inner
    found = {}
    read_names = names_read(fn)

    def note(address, ctype, indexed, read, write):
        r = found.setdefault((address, ctype, indexed), [False, False])
        r[0], r[1] = r[0] or read, r[1] or write

    anchored = anchored or (lambda value, terms: True)

    def visit(e, read=True, write=False, op=None, kept=True):
        def plain(value, terms):
            if kept and value is not None and SMALLEST_POINTER <= value <= MASK and anchored(value, terms):
                note(value, None, False, False, False)
        if e is None:
            return
        a = _access(e)
        if a is not None:
            value, terms, rest = _split(a[0])
            if value is not None and value >= 0:
                value &= MASK
                if not rest or (value >= SMALLEST_POINTER and anchored(value, terms)):
                    note(value, a[1], bool(rest), read, write)
            for x in rest:
                visit(x, op="+")
            return
        if isinstance(e, C.Assign):
            visit(e.target, read=e.op != "=", write=True)
            visit(e.value, kept=not (e.op == "=" and isinstance(e.target, C.Name) and e.target.id not in read_names))
        elif isinstance(e, C.Post) or (isinstance(e, C.Unary) and e.op in ("++", "--")):
            visit(e.e, read=True, write=True)
        elif isinstance(e, C.Binary) and e.op in SUMS:
            value, terms, rest = _split(e)
            if op not in NUMBERS:
                plain(value, terms)
            for x in rest:
                visit(x, op="+", kept=kept)
        elif isinstance(e, C.Num):
            if isinstance(e.value, int) and op not in NUMBERS:
                plain(e.value, [e.value])
        elif isinstance(e, C.Binary):
            visit(e.l, op=e.op, kept=kept)
            visit(e.r, op=e.op, kept=kept)
        elif isinstance(e, C.Unary):
            visit(e.e, op={"-": "neg", "~": "~", "!": "!"}.get(e.op), kept=kept)
        elif isinstance(e, C.Cast):
            visit(e.e, read, write, op, kept)
        elif isinstance(e, C.Cond):
            visit(e.c, op="?")
            visit(e.t, op=op, kept=kept)
            visit(e.f, op=op, kept=kept)
        elif isinstance(e, C.Call):
            visit(e.f)
            for x in e.args:
                visit(x)
        elif isinstance(e, C.Index):
            visit(e.base, kept=kept)
            visit(e.index, op="[]")
        elif isinstance(e, C.Member):
            visit(e.base, kept=kept)
        elif isinstance(e, C.Sizeof):
            return
        elif isinstance(e, C.Expr):
            for slot in type(e).__slots__:
                v = getattr(e, slot, None)
                for x in v if isinstance(v, list) else [v]:
                    if isinstance(x, C.Expr):
                        visit(x, kept=kept)

    def stmt(s):
        if isinstance(s, C.Asm):
            for o in s.outputs:
                visit(o.e, read=False, write=True)
            for o in s.inputs:
                visit(o.e)
        elif isinstance(s, C.Decl):
            for d in s.items:
                visit(d.init, kept=d.name in read_names)
                for x in d.dims:
                    visit(x, op="[]")
        elif not isinstance(s, C.Case):         # (a case label is a number)
            for e in _exprs(s):
                visit(e)
        for inner in _inner(s):
            stmt(inner)
    for s in fn.body.body:
        stmt(s)
    return found


def names_read(fn):
    """The names a function's C reads somewhere: every name but the target of a plain assignment."""
    from dread.cflow import walk_expr
    from dread.naming import _exprs, _inner
    targets, out = set(), set()

    def stmt(s):
        for e in _exprs(s):
            for x in walk_expr(e):          # (an assignment comes before its target)
                if isinstance(x, C.Assign) and x.op == "=" and isinstance(x.target, C.Name):
                    targets.add(id(x.target))
                elif isinstance(x, C.Name) and id(x) not in targets:
                    out.add(x.id)
        for inner in _inner(s):
            stmt(inner)
    for s in fn.body.body:
        stmt(s)
    return out


def listing_references(made):
    """{(address, None, False): [False, False]} of the addresses a function's machine code makes
    (made: materialized()), those of at least SMALLEST_POINTER: each only named, since a listing
    alone does not say what is done with it."""
    return {(a, None, False): [False, False] for a in made if SMALLEST_POINTER <= a <= MASK}


FLOW_ROUNDS = 50        # flow_references' visits at most: this many an instruction of the function, and 100


def flow_references(program, fn, made=None, scratch=None, made_named=True, indexed=True):
    """{(address, type, indexed): [read, write]} of what a function does at fixed addresses, read from
    its machine code alone, as c_references reads it from the readable C: a forward pass over the
    function's control flow (dread.arch flow) follows the constants its registers hold (the arch
    layer's value_step: literals, addresses made from the pc, immediates, sums; the target's fixed
    registers, RISC-V's gp, everywhere), a value where every path brings the same one. A load or store
    at a known base plus its offset reads or writes that address (indexed where a register index, or
    a value that is a constant plus something unknown, is added: then only at an anchored address of
    at least SMALLEST_POINTER, as there); a known value a store writes, a call is given in its argument
    registers or a return leaves in its result registers is named (type None) when it is an anchored
    address of at least SMALLEST_POINTER; so is every address the code makes (made: materialized(),
    listing_references) with made_named, but where a literal lies whose word the layer reads as a
    value (an ARM literal pool's slot: the C has its word). A call ends the scratch registers' values
    (scratch: dread.regflow scratch_names, found when None); a conditional instruction keeps a
    register's value only where it writes the same; code no direct path reaches (a switch's cases)
    starts from what the indirect jumps in front of it leave. indexed False: a sum with an unknown term
    is not known. Without a value_step (MIPS, MSP430: not written yet) it is listing_references(made).

    Costs 0.5-2.3 ms a function on FreeRTOS builds in one process (8-30 us an instruction, 2026-10-22)
    where readable C costs 0.1-0.5 s. Not established: values kept in the frame (spilled and loaded
    back) are lost, and so is a value that differs by path where the paths join (the readable C names
    each constant where a path sets it); a constant only compared is named when the code makes it
    (made_named), where the readable C leaves comparisons out; the readable C names less where it
    keeps an address in a variable it uses more than once (no access at a constant plus an index)."""
    arch = program.arch
    step = getattr(arch, "value_step", None)
    if made is None:
        made = materialized(program, fn)
    found = {}

    def note(address, ctype, ix, read, write):
        r = found.setdefault((address, ctype, ix), [False, False])
        r[0], r[1] = r[0] or read, r[1] or write
    addrs = fn.insns
    if step is None or not addrs:
        return listing_references(made)
    if scratch is None:
        from dread.regflow import scratch_names
        scratch = scratch_names(program.target, arch)
    anchored = anchoring(program, made)
    insns = program.instructions(fn)
    n = len(addrs)
    pos = {a: k for k, a in enumerate(addrs)}
    succ, kinds, effects, ijumps = [[] for _ in range(n)], [None] * n, [None] * n, []
    for k, insn in enumerate(insns):
        if insn is None:
            continue
        kind, target, falls = arch.flow(insn)
        if kind == "jump" and isinstance(target, int) and (target & ~1) in pos:
            succ[k].append(pos[target & ~1])
        elif kind == "jump" or kind == "ijump":
            kind = "tail"                   # (out of the function: its arguments go with it)
            if not isinstance(target, int):
                ijumps.append(k)
        kinds[k] = kind
        effects[k] = (arch.reg_effects(insn)[1], arch.is_conditional(insn))
        if k + 1 < n and addrs[k] + insn.size == addrs[k + 1] and (kind in ("next", "call", "icall") or falls):
            succ[k].append(k + 1)
    starts = {0, pos.get(fn.entry, pos.get(fn.entry & ~1, 0))}
    reached = starts | {s for out in succ for s in out}
    unreached = [k for k in range(n) if k not in reached]
    for k in ijumps:
        succ[k].extend(unreached)
    fixed = {r: (v & MASK, False) for r, v in program.image.fixed.items()}
    isas = [program.isa_at(a) for a in addrs]
    before = [None] * n
    for k in starts | (set() if ijumps else set(unreached)):
        before[k] = dict(fixed)

    def run(k, known):
        values, accesses, stored = step(insns[k], isas[k], known, program)
        writes, conditional = effects[k]
        out = dict(known)
        for r in writes:
            out.pop(r, None)
        for r, v in values.items():
            if v is None or (conditional and known.get(r) != v) or (not indexed and v[1]):
                out.pop(r, None)
            else:
                out[r] = v
        if kinds[k] in ("call", "icall"):
            for r in scratch:
                out.pop(r, None)
        out.update(fixed)
        return out, accesses, stored, values
    work = [k for k in range(n) if before[k] is not None]
    heapq.heapify(work)
    queued, budget = set(work), FLOW_ROUNDS * n + 100
    while work and budget:
        budget -= 1
        k = heapq.heappop(work)
        queued.discard(k)
        if insns[k] is None:
            continue
        out = run(k, before[k])[0]
        for s in succ[k]:
            old = before[s]
            new = dict(out) if old is None else {r: v for r, v in old.items() if out.get(r) == v}
            if new != old:
                before[s] = new
                if s not in queued:
                    heapq.heappush(work, s)
                    queued.add(s)
    abi = program.target.abi
    slots = set()
    for k in range(n):
        known = before[k]
        if known is None or insns[k] is None:
            continue
        _, accesses, stored, values = run(k, known)
        if values and made_named:
            lit = arch.literal(insns[k], isas[k])
            if lit is not None:
                slots.add(lit[0])
        for base, offset, ctype, ix, read, write in accesses:
            address = (base + offset) & MASK
            if not ix or (address >= SMALLEST_POINTER and anchored(address, [base, offset])):
                note(address, ctype, ix, read, write)
        used = list(stored)
        if kinds[k] in ("call", "icall", "tail"):
            used += abi.arg_regs
        elif kinds[k] == "return":
            used += abi.result
        for r in used:
            v = known.get(r)
            if v is not None and r not in fixed and SMALLEST_POINTER <= v[0] <= MASK and anchored(v[0], [v[0]]):
                note(v[0], None, False, False, False)
    if made_named:
        for a in made - slots:
            if SMALLEST_POINTER <= a <= MASK:
                note(a, None, False, False, False)
    return found


def lifted_text(lifter, fn):
    """(the function's lifted C, None) or (None, why there is none)."""
    from dread import lift
    try:
        return lifter.lift(fn).text, None
    except lift.Refused as e:
        return None, "the lifter refuses it: %s" % e
    except Exception as e:                  # (a fault of the lifter's: the listing still reads)
        return None, "the lifter failed: %s" % e


def references(program, fn, text, why=None, context=None, made=None):
    """What the graph shows of one function besides its calls: {"from": "readable C" or "listing",
    "why": why not its C (None when from it), "digest": its readable C's digest before names
    (dread.naming digest: what the readable_types plan is made for) or None, "refs": [[address, type
    or None, indexed, read, write], ...] (c_references or listing_references, sorted)}. text: its
    lifted C (lifted_text), None with why. context: a dread.readable Context (the calls' arguments).
    made: materialized(program, fn) when known (made here otherwise: it reads the listing; the rest
    works on the C alone)."""
    from dread import naming, readable
    if made is None:
        made = materialized(program, fn)
    tree = None
    if text is not None:
        try:
            tree = readable.readable_tree(fn.name, text, context=context)
        except Exception as e:              # (a stage's fault: the listing still reads)
            why = "no readable C: %s" % e
        else:
            if tree is None:
                why = "its lifted C is not the dialect dread.cparse reads"
    if tree is not None:
        _, f = tree
        refs = c_references(f, anchoring(program, made))
        source, digest = "readable C", naming.digest(f)
    else:
        refs, source, digest = listing_references(made), "listing", None
    rows = sorted(([a, t, i, r, w] for (a, t, i), (r, w) in refs.items()), key=lambda x: (x[0], x[1] or "", x[2]))
    return {"from": source, "why": why, "digest": digest, "refs": rows}


FACTS_VERSION = 2       # what references() makes of the same C: bump when that changes (2: movw + movt)
PIPELINE = ("dread.relations", "dread.readable", "dread.tidy", "dread.structure", "dread.cparse", "dread.cexpr",
            "dread.cflow", "dread.naming")     # the code that makes and reads readable C: in the key


class Facts:
    """references() of an image's functions, each kept in BUILD/relations/IMAGE/NAME.json under a key
    of what it is made from: the function's lifted C (or why there is none: then its listing, whose
    bytes regflow's key holds), regflow's key (the calls' arguments), the code that makes and reads
    readable C (PIPELINE), the memory map. Making one costs the readable C's clean-up (0.1-0.5 s a
    function on a FreeRTOS build, several seconds for the largest); finding one kept costs its lift
    (about 0.03 s). One thread uses an instance (dread gui's worker); lock, a context manager, is held
    where it may run the program's code in the emulator (the lift resolves switches with dread.switches,
    regflow when none was given) and while it reads the listing, not while it makes readable C, which
    works on the C alone."""

    def __init__(self, target, image, program, flow=None, endless=None, lock=None):
        import contextlib
        self.target, self.image, self.program = target, image, program
        self.flow, self.endless = flow, endless
        self.lock = lock if lock is not None else contextlib.nullcontext()
        self.lifter = self.context = self.base = None
        self.made = {}                      # name -> references, for this program

    def folder(self):
        return os.path.join(self.target.build, "relations", self.image.name)

    def prepare(self):
        """The lifter, the readable C's context and the part of the key every function shares (once)."""
        if self.base is not None:
            return
        import hashlib
        from dread import lift, readable, regflow
        try:
            if self.flow is None:
                with self.lock:
                    self.flow = regflow.load(self.target, self.image, self.program)
            self.lifter = lift.make_lifter(self.program, self.flow, self.endless)
            self.context = readable.Context(self.target, self.image, self.program, self.flow)
        except Exception:                   # (an architecture the analyses do not know yet: its listings)
            self.lifter = self.context = None
        h = hashlib.sha1(b"dread relations %d\0" % FACTS_VERSION)
        for module in PIPELINE:
            __import__(module)
            with open(sys.modules[module].__file__, "rb") as f:
                h.update(f.read())
        h.update(regflow.cache_key(self.target, self.image).encode())
        memory = self.target.memory
        h.update(repr((self.image.base, len(self.program.data),
                       [(r.base, r.size, r.kind) for r in memory.regions] if memory else None,
                       memory.io if memory else None)).encode())
        self.base = h.hexdigest()

    def get(self, name):
        """The function's references (references()): made in this process, kept, or made now and kept."""
        if name in self.made:
            return self.made[name]
        import hashlib
        fn = self.program.by_name[name]
        self.prepare()
        with self.lock:
            if self.lifter is not None:
                text, why = lifted_text(self.lifter, fn)
            else:
                text, why = None, "no lifter for %s" % self.program.arch.name
            made = materialized(self.program, fn)
        key = hashlib.sha1(("%s\0%s\0%s" % (self.base, name, text if text is not None else "why\0" + why))
                           .encode()).hexdigest()
        path = os.path.join(self.folder(), name + ".json")
        kept = None
        try:
            with open(path) as f:
                kept = json.load(f)
        except (OSError, ValueError):
            pass
        if isinstance(kept, dict) and kept.get("key") == key and isinstance(kept.get("facts"), dict):
            facts = kept["facts"]
        else:
            facts = references(self.program, fn, text, why, self.context, made)
            try:
                os.makedirs(self.folder(), exist_ok=True)
                temp = "%s.%d.tmp" % (path, os.getpid())
                with open(temp, "w") as f:
                    json.dump({"key": key, "facts": facts}, f, sort_keys=True)
                os.replace(temp, path)
            except OSError:                 # (a build directory that cannot be written: made each time)
                pass
        self.made[name] = facts
        return facts


def classify(program, address, exact=False):
    """What sits at an address: ("text", text), ("function", name) or ("table", parts) (dread.constants
    pointed; only outside RAM and the devices, whose bytes in the image are at most initial values),
    ("memory", kind) with kind "register" ([memory] io), "rom" (a rom region) or "global", or None (a
    number, or an address in a function's code). exact: an access at the address, which is memory
    wherever it is."""
    memory = program.target.memory
    if memory is not None and memory.is_io(address):
        return "memory", "register"
    region = memory.region(address) if memory is not None else None
    if region is None or region.kind == "rom":
        got = pointed(program, address)
        if got is not None:
            return got
        if not exact and program.function_at(address & ~1) is not None:
            return None
        if region is None and not exact and not program.in_image(address):
            return None
    return "memory", "rom" if region is not None and region.kind == "rom" else "global"


# ---- names ----

def mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


# How sure a name is, from unsure to sure (certainty()): the levels in that order, and the vote's
# agreement (names.tsv's confidence) they begin at. A proposal is "sure" where every answer agreed
# with it, "unsure" where its vote alone makes dread pilot --task review challenge it (agreement at
# most 1 - THRESHOLD / WEIGHTS["vote"]: 0.5 by the review's weights; with five questions 0.2 and 0.4),
# "likely" between (0.6 and 0.8 of five). Names with a vote below 1 were judged wrong 22 % of the
# time on FreeRTOS, against 9 % of all names (docs/decisions.md, 2026-10-23).
CERTAINTY = ("none", "unvoted", "unsure", "likely", "sure", "named")
SURE = 1.0


def unsure_at():
    """The agreement at or below which a vote leaves a name unsure: where dread.namereview's vote
    trigger alone reaches its threshold."""
    from dread import namereview
    return 1 - namereview.THRESHOLD / namereview.WEIGHTS["vote"]


def certainty(naming, confidence=None):
    """The level of CERTAINTY of a name: "named" (a name in the tables: a person's, the ELF's),
    "none" (no name yet), and for a proposal by its vote's agreement (confidence, 0 to 1): "sure",
    "likely", "unsure", or "unvoted" where no vote was taken (one question; a memory's or a
    structure's name, which dread pilot asks once; a review's more general name)."""
    if naming == "named":
        return "named"
    if naming != "proposed":
        return "none"
    if confidence is None:
        return "unvoted"
    if confidence >= SURE - 1e-9:
        return "sure"
    return "unsure" if confidence <= unsure_at() + 1e-9 else "likely"


class Names:
    """What the tables say about names, read again when one of their files changes: the pilot's
    proposals for functions (names.tsv: the last line of each address, with its vote), the review's
    challenges (review.tsv, dread.namereview standing), the readable_names table's globals and
    structures, the readable_types plan, the lifted and readable tables, and the symbols' data names
    (the program's: it is made anew when symbols.txt changes)."""

    def __init__(self, target, image, program):
        self.target, self.image, self.program = target, image, program
        self.stamp = None
        self.data = {a: n for kind, a, n in program.symbols() if kind == "d" and n}
        self.refresh()

    def files(self):
        from dread.namereview import review_path
        from dread.pilot import names_path
        img = self.image
        return [names_path(self.target, img), review_path(self.target, img)] + \
            [img.table(t) for t in ("readable_names", "readable_types", "readable", "lifted")]

    def refresh(self):
        stamp = tuple(mtime(f) for f in self.files())
        if stamp == self.stamp:
            return self
        from dread import naming, namereview, pilot, shapes
        from dread.build import read_names
        img = self.image
        self.rows = pilot.last_rows(pilot.read_name_rows(pilot.names_path(self.target, img)))
        self.proposals = {a: (r["proposed"], r["what"]) for a, r in self.rows.items()}
        self.reviewed, self.reviewed_groups = {}, {}     # entry, members key -> review.tsv rows in order
        for r in namereview.read_review(namereview.review_path(self.target, img)):
            if r["kind"] == "group":
                if r["group"]:
                    self.reviewed_groups.setdefault(r["group"], []).append(r)
            else:
                try:
                    self.reviewed.setdefault(int(r["address"], 16), []).append(r)
                except ValueError:
                    continue
        self.globals = naming.load(img, "globals")
        self.plan = shapes.load_plan(img)
        taken = {e.get("name") for e in self.globals.values() if isinstance(e, dict)}
        self.structs = naming.struct_names(self.plan, naming.load(img, "structs"), taken)
        self.lifted = set(read_names(img.table("lifted")))
        self.readable = set(read_names(img.table("readable")))
        self.stamp = stamp
        self.made = {}                      # (entry, name, history) -> about()
        return self

    def function(self, fn):
        """(naming, proposal or None) of a function: named, proposed or unnamed."""
        from dread.samecode import UNNAMED
        proposal = self.proposals.get(fn.entry)
        if not UNNAMED.match(fn.name):
            return "named", proposal
        return ("proposed" if proposal else "unnamed"), proposal

    def about(self, fn, history=False):
        """How sure a function's name is and what the review said of it: {"naming", "certainty"
        (certainty()), "confidence" (the vote's agreement behind the proposal it goes by, or None),
        "review" (dread.namereview standing of the name it goes by, or None)}; history adds the
        proposal's "proposed", "what", "samples" (the vote's answers), "provider", "shown" and the
        review's every row. Kept until a table changes."""
        key = (fn.entry, fn.name, history)
        if key in self.made:
            return self.made[key]
        from dread import namereview, pilot
        naming_, proposal = self.function(fn)
        row = self.rows.get(fn.entry)
        confidence = pilot.confidence(row) if naming_ == "proposed" and row is not None else None
        now = fn.name if naming_ == "named" else proposal[0] if proposal else None
        out = {"naming": naming_, "certainty": certainty(naming_, confidence), "confidence": confidence,
               "review": namereview.standing(self.reviewed.get(fn.entry), now, history)}
        if history and row is not None:
            out.update(proposed=row["proposed"], what=row["what"], samples=row.get("samples") or None,
                       provider=row.get("provider") or None, shown=row.get("shown") or None,
                       proposal_confidence=pilot.confidence(row))
        self.made[key] = out
        return out

    def group_review(self, key, row):
        """dread.namereview standing of a group's name (its subsystems.tsv row) by its members key, or
        None."""
        from dread import namereview
        if row is None or key not in self.reviewed_groups:
            return None
        return namereview.standing(self.reviewed_groups[key], namereview.group_label(row))

    def memory(self, address, types):
        """(name, naming, default name, proposed name or None) of memory at an address accessed with
        types (none: "u32"): a symbol's name, else the readable_names table's for one of the types,
        else the default (dread.naming default_name)."""
        from dread import naming
        types = sorted(types, key=lambda t: (t != "u32", t)) or ["u32"]
        default = naming.default_name(address, types[0], self.target.memory)
        proposed = None
        for t in types:
            entry = self.globals.get(naming.global_key(address, t))
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                proposed = entry["name"]
                break
        if address in self.data:
            return self.data[address], "named", default, proposed
        if proposed:
            return proposed, "proposed", default, proposed
        return default, "unnamed", default, None


# ---- the graph ----

def graph(program, center, depth=DEPTH, calls=None, facts=None, names=None, extra=None,
          max_functions=MAX_FUNCTIONS, max_nodes=MAX_NODES):
    """The neighbourhood of center: {"center", "depth", "nodes", "edges", "missing", "left_out",
    "counts"} (docs/formats.md, "The relations graph"). calls: call_maps(program) (found when None);
    facts: {name: references()}, a function of the neighbourhood without one is listed in "missing"
    and has no data nodes yet; names: a Names; extra: name -> more attributes of a function node
    (dread gui: its origin)."""
    if center not in program.by_name:
        raise KeyError(center)
    up, down = calls if calls is not None else call_maps(program)
    names = names if names is not None else Names(program.target, program.image, program)
    facts = facts if facts is not None else {}
    depth = max(0, min(int(depth), MAX_DEPTH))
    found, left = neighbourhood(program, center, depth, up, down, max_functions)
    nodes, edges, dropped = {}, {}, set()

    def function_node(name, distance, side):
        fn = program.by_name[name]
        naming_, proposal = names.function(fn)
        about = names.about(fn)
        node = {"id": "f:" + name, "kind": "function", "name": name, "address": fn.entry,
                "size": program.end(fn) - fn.entry, "naming": naming_, "certainty": about["certainty"],
                "distance": distance, "side": side, "lifted": name in names.lifted,
                "readable": name in names.readable, "facts": None}
        if proposal:
            node["proposed"] = {"name": proposal[0], "what": proposal[1]}
        if about["confidence"] is not None:
            node["confidence"] = about["confidence"]
        if about["review"] is not None:
            node["review"] = about["review"]
        if extra is not None:
            node.update(extra(name))
        nodes[node["id"]] = node
        return node

    def room(node_id):
        if node_id in nodes:
            return True
        if len(nodes) >= max_nodes:
            dropped.add(node_id)
            return False
        return True

    def edge(a, b, kind, **more):
        key = (a, b, kind)
        e = edges.get(key)
        if e is None:
            e = edges[key] = dict({"from": a, "to": b, "kind": kind}, **more)
        return e

    order = sorted(found, key=lambda n: (found[n][0], SIDES.index(found[n][1]), program.by_name[n].entry, n))
    for name in order:
        function_node(name, *found[name])

    def refer_function(src, name, distance):
        """An edge to a function whose address src takes; the function joins the graph if there is room."""
        node_id = "f:" + name
        if node_id not in nodes:
            if not room(node_id) or name not in program.by_name:
                return
            function_node(name, distance, "referred")
        if node_id != src:
            edge(src, node_id, "refers-to-function")

    def text_node(address, text):
        node_id = "t:0x%x" % address
        if node_id not in nodes and room(node_id):
            nodes[node_id] = {"id": node_id, "kind": "text", "name": '"%s"' % text, "text": text,
                              "address": address, "naming": None}
        return node_id if node_id in nodes else None

    memory_types = {}                               # node id -> set of access types

    def memory_node(address, kind, ctype):
        node_id = "m:0x%x" % address
        if node_id not in nodes:
            if not room(node_id):
                return None
            nodes[node_id] = {"id": node_id, "kind": kind, "address": address}
            memory_types[node_id] = set()
        if ctype:
            memory_types[node_id].add(ctype)
        return node_id

    missing = []
    for name in order:
        f = facts.get(name)
        src = "f:" + name
        node = nodes[src]
        if f is None:
            node["facts"] = "pending"
            missing.append(name)
            continue
        node["facts"] = f["from"]
        if f.get("why"):
            node["why"] = f["why"]
        distance = found[name][0]
        for address, ctype, indexed, read, write in f["refs"]:
            got = classify(program, address, exact=ctype is not None and not indexed)
            if got is None:
                continue
            kind, value = got
            if kind == "text":
                t = text_node(address, value)
                if t:
                    edge(src, t, "refers-to-text")
            elif kind == "function":
                refer_function(src, value, distance + 1)
            else:
                m = memory_node(address, "table" if kind == "table" else value, ctype)
                if m is None:
                    continue
                for how, flag in (("reads", read), ("writes", write), ("uses", not (read or write))):
                    if flag:
                        e = edge(src, m, how, types=[], indexed=False)
                        if ctype and ctype not in e["types"]:
                            e["types"].append(ctype)
                        e["indexed"] = e["indexed"] or bool(indexed)
                if kind == "table":
                    nodes[m]["words"] = len(value)
                    for part, v, word in value:
                        if part == "text":
                            t = text_node(word, v)
                            if t:
                                edge(m, t, "refers-to-text")
                        elif part == "function":
                            refer_function(m, v, distance + 1)
    for key in [k for k in edges if k[2] == "uses"]:        # (read or written there says more)
        if (key[0], key[1], "reads") in edges or (key[0], key[1], "writes") in edges:
            del edges[key]
    for node_id, types in memory_types.items():
        node = nodes[node_id]
        name, naming_, default, proposed = names.memory(node["address"], types)
        node.update({"name": name, "naming": naming_, "certainty": certainty(naming_), "default": default,
                     "types": sorted(types)})
        if proposed:
            node["proposed"] = {"name": proposed}
        node["size"] = None if node["kind"] == "table" else max((SIZES.get(t, 4) for t in types), default=None)

    # the structures: the plan's, for the functions whose C it was made for
    plan = names.plan

    def struct_node(k):
        node_id = "s:" + k
        if node_id not in nodes:
            if not room(node_id):
                return None
            label, fields = names.structs.get(k, (k, {}))
            from dread.shapes import field_name
            held = plan.embeds.get(k, {})       # (a structure held inside it: struct NAME, its size)
            rows = [(off, t if off not in held else "struct " + names.structs.get(held[off], (held[off], {}))[0],
                     SIZES.get(t, 4) if off not in held else plan.size(held[off])) for off, t in plan.members(k)]
            naming_ = "proposed" if label != k else "unnamed"
            nodes[node_id] = {"id": node_id, "kind": "structure", "name": label, "plan": k,
                              "naming": naming_, "certainty": certainty(naming_), "address": None,
                              "size": max((off + z for off, _, z in rows), default=0),
                              "fields": [[off, t, field_name(fields, off)] for off, t, _ in rows]}
        return node_id
    for name in order:
        f = facts.get(name)
        typed = plan.typed.get(name)
        if not f or not typed or f.get("digest") is None or plan.digests.get(name) not in (None, f["digest"]):
            continue
        for variable, k in sorted(typed.items()):
            s = struct_node(k) if k in plan.structs else None
            if s:
                e = edge("f:" + name, s, "uses-structure", variables=[])
                e["variables"].append(variable)
    for node_id in [i for i in nodes if i.startswith("s:")]:
        k = nodes[node_id]["plan"]
        for offset, other in sorted(plan.pointers.get(k, {}).items()):
            s = struct_node(other) if other in plan.structs else None
            if s:
                e = edge(node_id, s, "points-to", offsets=[])
                e["offsets"].append(offset)
        for offset, other in sorted(plan.embeds.get(k, {}).items()):
            s = struct_node(other) if other in plan.structs else None
            if s:
                e = edge(node_id, s, "holds", offsets=[])
                e["offsets"].append(offset)

    # the calls between the functions shown, and how many each has beyond them
    for node_id, node in list(nodes.items()):
        if node["kind"] != "function":
            continue
        name = node["name"]
        for callee, sites in sorted(down.get(name, {}).items()):
            if "f:" + callee in nodes:
                edge(node_id, "f:" + callee, "calls", sites=sites)
        node["more"] = {"callers": sum(1 for n in up.get(name, {}) if "f:" + n not in nodes),
                        "callees": sum(1 for n in down.get(name, {}) if "f:" + n not in nodes)}
    counts = {}
    for node in nodes.values():
        counts[node["kind"]] = counts.get(node["kind"], 0) + 1
    return {"center": center, "depth": depth, "nodes": list(nodes.values()), "edges": list(edges.values()),
            "missing": missing, "left_out": {"functions": len(left), "nodes": len(dropped)}, "counts": counts}


# ---- the command line ----

def text_lines(g):
    """The graph for a terminal: the nodes by kind, then the edges."""
    by_id = {n["id"]: n for n in g["nodes"]}
    lines = ["; %s, callers and callees %d call%s away: %s" % (
        g["center"], g["depth"], "" if g["depth"] == 1 else "s",
        ", ".join("%d %s" % (n, k) for k, n in sorted(g["counts"].items())))]
    if g["left_out"]["functions"] or g["left_out"]["nodes"]:
        lines.append("; left out: %d functions, %d other nodes (limits)" % (g["left_out"]["functions"],
                                                                          g["left_out"]["nodes"]))
    for node in g["nodes"]:
        where = "0x%08x" % node["address"] if node.get("address") is not None else " " * 10
        what = [node["kind"], node.get("naming") or ""]
        if node.get("naming") == "proposed" and node.get("certainty"):
            what.append(node["certainty"] + (" %.2f" % node["confidence"] if node.get("confidence") is not None
                                             else ""))
        if node["kind"] == "function":
            what.append("%s %d" % (node["side"], node["distance"]) if node["side"] != "center" else "center")
            what.append("from its %s" % node["facts"] if node.get("facts") else "")
            if node.get("proposed"):
                what.append("proposed %s" % node["proposed"]["name"])
            if (node.get("review") or {}).get("mark"):
                what.append("review: %s" % node["review"]["mark"])
        elif node.get("types"):
            what.append(" ".join(node["types"]))
        lines.append("%-30s %s  %s" % (node["name"][:30], where, "  ".join(w for w in what if w)))
    for e in g["edges"]:
        more = ""
        if e.get("types"):
            more = " " + " ".join(e["types"]) + (" indexed" if e.get("indexed") else "")
        elif e.get("variables"):
            more = " " + ", ".join(e["variables"])
        lines.append("  %s %s %s%s" % (by_id[e["from"]]["name"], e["kind"], by_id[e["to"]]["name"], more))
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread relations", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--depth", type=int, default=DEPTH,
                        help="callers and callees this many calls away (default %d, at most %d)" % (DEPTH, MAX_DEPTH))
    parser.add_argument("--json", action="store_true", help="the graph as JSON (docs/formats.md)")
    parser.add_argument("name", help="a function's name or an address inside it")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
    except tgt.TargetError as e:
        print("dread relations: %s" % e, file=sys.stderr)
        return 1
    from dread import noreturn
    from dread.program import Program
    program = Program(target, image)
    fn = program.by_name.get(args.name)
    if fn is None:
        try:
            fn = program.function_at(int(args.name, 0))
        except ValueError:
            fn = None
    if fn is None:
        print("dread relations: no function %s" % args.name, file=sys.stderr)
        return 1
    calls = call_maps(program)
    found, _ = neighbourhood(program, fn.name, max(0, min(args.depth, MAX_DEPTH)), *calls)
    try:
        endless = noreturn.find(program)
    except Exception:                       # (an architecture without an emulator yet: the lifter finds none)
        endless = None
    made = Facts(target, image, program, endless=endless)
    g = graph(program, fn.name, args.depth, calls, {name: made.get(name) for name in found})
    if args.json:
        print(json.dumps(g, indent=1, sort_keys=True))
    else:
        print("\n".join(text_lines(g)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
