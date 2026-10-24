"""Structures and arrays of static data: memory at fixed addresses grouped into the objects it was declared as.

Readable C writes memory at a fixed address as a macro per address and type (g_2000004c,
g_2000a0f1_u8: dread.naming apply_globals). Where the source keeps its data in static structures,
the machine code reaches their fields from one base: it makes the structure's address (a literal
pool's word, movw and movt, lui with addi) and loads and stores at constant offsets from it. This
finds such structures from the machine code alone and readable C then writes their accesses as
members of one object per structure: `g_20000030.f_4`, its macro `#define g_20000030 (*(volatile
struct g_20000030_t *)0x20000030u)` and the structure defined above the function (dread.naming
apply_globals with the plan). Every access stays one volatile access of its width at its address:
a field is declared only where C puts a member of that type at that offset without packing (each
naturally aligned, from a base aligned for the widest), an access the structure has no such field
for stays the macro it was (a read of the field's width and the other signedness is cast:
(s16)g_x.f_4).

The evidence (evidence()): a forward pass over each function's machine code follows the constants
its registers hold (the arch layer's value_step, as dread.relations flow_references) and, with each,
the address the function made it from, its origin: an address the code makes as compilers make
addresses (dread.relations materialized), moved, copied or added to on the way (add r3, r4, #8; a
base register's writeback). A load or store at a register with an origin plus a constant offset is
an access from that base; an access whose register has no origin (RISC-V's gp, a fixed register; an
upper half made by lui alone) gives the place it touches, not a base. What the function hands out
with a known address (the argument registers its callee reads at a call or tail call, a register it
stores, its result at a return) escapes: an object starts there, or a member whose address is taken.
An access with a register index at an origin plus a constant is an array that starts at that
constant (GCC's section anchors reach a table so: gizmo's dispatch takes commands[] at the anchor
plus 52, and the anchor is no structure).

The decision (plan()): a base is the origin of an access at a constant offset at or above it in
some function. Its structure reaches as far as its accesses do over every function (the widest
offset plus its width) and ends earlier where something else starts: a base of other accesses, an
address that escapes, an array's start, a data symbol of the target's table (symbols.txt;
symbols=False leaves them out), a range of the startup's copies, the end of its memory region. Its
fields are every access at a constant address inside it, through any base (the gp-relative ones
too), laid out as dread.shapes lays out a kind (layout()). A structure needs two fields, one of them
wider than a byte (bytes alone are mostly a string or a buffer, read better as they are), and a base
with an access below it is none (the object goes on below). Device registers ([memory] io) are left
out: no structure the source declares, and their names (io_40011004) already say where they are.

The hard part is a section anchor: GCC without -fdata-sections reaches every variable of a source
file from one base, clang merges a file's globals into one object (on RISC-V, and with its global
merge on ARM; newlib's malloc keeps its state so too), and an older compiler may reach a file's data
from its section's start. Within a function, an anchor's accesses at constant offsets look exactly
like a structure's. What tells them apart is what the code does next to the base (separate_bases): a
compiler that makes a base per object reaches the neighbours a function uses together through bases
of their own (or through RISC-V's gp), while an anchor is the one base its file reaches all of its
variables through, so the functions that use it reach nothing next to it apart from it. A base
counts only where one of its functions reaches a place within NEAR bytes in front of it or behind
its structure apart from it (trust="all" counts every base, for measuring what this keeps out). The
data symbols only end structures: a symbol at a base would make it count, but one that a person gave
the first variable of an anchor's run would make the whole run one.

What callees reach (handed()): where the readable C of a lifted function hands the address of static
data to a callee for a parameter, dread.shapes' template of that parameter (what the callee and its
callees reach through it at constant offsets) says what lies at the address: a parameter points into
one object. Such a base counts without the neighbour test, its structure reaches as far as the
callee reaches (or its own accesses), ends only where a data symbol, a startup range, an array or
another such base starts (an address handed out inside it is a member's), and is no evidence for
other bases' neighbours (GCC makes an anchor's variable's address as a literal of its own, so it
would let an anchor's run count). A template needs two fields from offset 0 up and none below, and
memory a walk reached is no structure (dread.shapes Shapes.walked_template: memcpy's buffers, also
where a function hands its parameter on to it). RISC-V's lists, which their functions reach at no
constant address, are structures so (vListInitialise(&xDelayedTaskList1) reaches its five words).

Arrays (find_arrays(), from the lifted functions' readable C, whose arithmetic dread.tidy folded): a
load or store at a constant plus an index times a scale (indexed_accesses: 0x200299f8u + 0x14u * r9
+ 4, (r1 << 3) + 0x2002990cu, also through a variable that holds the index times the scale,
scales_of) says that elements of that size lie there, its place a field of one. Accesses of one
scale less than an element apart are one array. Where they all lie below every address the code made
them from (base_of: the literal the sum adds whole), it starts at the lowest of those, or at a data
symbol within an element below it (table[id - 1].level, for which GCC and clang make table's own
address and fold the -1 into the index's arithmetic or the access's offset, so that the sum's
constant lies an element below the array; a switch's table indexed by c - 'b'); else at a data
symbol within an element below the lowest of them, else at the address the code made them from or
handed out there, else at the lowest. It counts the elements its accesses reach (at least one) and,
where its start is a base that counts, those the code reaches at constant places from it. Its
elements' fields are every access inside it at its offset in an element, laid out as a structure's
and aligned in every element (an element's first field that nothing reads a gap, not where the
structure starts), and what callees reach through an element's address. Without readable C (a
function that does not lift), a structure whose fields are one type one after another, which the
machine code reaches with an index of that width, is an array of that type (a run of equal fields
that the code also indexes). A pointer that walks static data (walks: given an address, stepped by
one constant of two bytes or more) says the same of its accesses as an index would, its stride the
elements' size and its start the base (since 2026-10-24: FreeRTOS GCC -Os's vStartTimerDemoTask fills
xAutoReloadTimers through r5 = 0x200017dcu ... r5 = r5 + 4, and the timers printed as a structure
of a word at 0 and two at 0x4c and 0x50 with 72 bytes of padding between); only where its start is
a base that counts, tried with the end its structure would have (a walk alone makes no array).
Readable C writes an element's field as one (g_200299f8[r9].f_4,
g_20000c74[1], the index plus the element its constant lies in, g_x[r3 + 1], or minus the elements
below the array's start it lies, g_x[r0 - 1].f_4: the index as the code computes it) through a macro
that is a pointer to the first element (#define g_200299f8 ((volatile struct g_200299f8_t
*)0x200299f8u)), so that C knows no bound for the index (GCC derives loop bounds from one); a
variable that held the index times the scale and that nothing reads any more goes (straight_facts
says where it holds the product). Where the code uses the address of an object, a field or an
element as an address (an argument the callee uses as a pointer, a value assigned to a variable used
as one or stored, compared with a pointer, a result), it reads as such: (u32)&g_20029988,
(u32)&g_20000030.f_8, (u32)&g_200299f8[r8]; never a value compared with a mask's or a shift's
result, nor an object where the image keeps no static data (image_spans; the rules are dread.naming
apply_globals').

Measured against debug information (score(), dread readable --statics --score ELF) on the 39 FreeRTOS
builds of the benchmark and GCC -Os and -O2 built without -fdata-sections (2026-10-23): no structure
and no array lies over several source variables on any of them. Structures 585, arrays 594 (with the
code's own bases alone 331 structures); every array that lies in a source variable has its element
size (583; the other 11 are tables in ROM at no variable), and 5,414 of 5,526 fields, an element's
counted in every element, are leaves of their variable at their offset with their size (most of the
rest lie in GCC 4.9's and 5's function-local static buffers, which the score does not place, and in
newlib's FILE, whose _flags and _file one store writes as a word). FreeRTOS GCC -Os: 21 structures
(xStart, the task and timer lists, the static queue, semaphore and event group buffers handed to their
create functions, newlib's __sglue, and arrays the code reaches only at constant places, such as
ulLastCycleCounters and xParameters) and 13 arrays (pxReadyTasksLists, xQueueRegistry, ulCycleCounters,
sCheckVariables, ...); RISC-V GCC -Os 7 structures (its lists, from what vListInitialise and the others
reach; none before) and 8 arrays; the two builds without -fdata-sections 13 and 14 structures and 12
arrays each (none before); MSP430 none. Counting every base instead (trust="all", 2026-10-23, before
the arrays and the callees' reach): 23 and 21 structures on GCC -Os and -O2 without -fdata-sections,
20 on RISC-V clang -Os, one (newlib's malloc state) on thirteen ARM builds, each over several source
variables, and 385 inside one variable on the 39 builds where the evidence keeps 329. What the code
alone cannot tell stays as it was: gizmo is built without -fdata-sections, and its g_config is no
structure (its callees walk it as bytes).

Not established: whether a structure or an array found is the source's (an array of two words
reached only at constant places reads as a structure, an equally spaced structure that the code
indexes as an array, an index into the middle of a structure that holds an array as an array of its
own); how many elements an array has (the count is what the code reaches); where one source
structure is reached from several bases (GCC's .word sym+8), it comes out as pieces, and accesses at
an array's element in a further element than the first through a base of its own (arr[i + 1]) can
make an array of their own; a structure whose functions use nothing next to it and that no lifted
function hands to a callee is missed; MSP430 and MIPS have no value_step yet and get none. That the
literal a compiler loads is its array's start: GCC 4.9 and 16 and clang 23 made the array's own
address for table[i - 1] on ARM, GCC 16 and clang 23 on RISC-V (2026-10-23); a compiler
that loads table + 4 to read a field with a register offset (none of them did) would shift the
array's start by that field's offset.
"""
import bisect
import heapq

MASK = 0xFFFFFFFF
SIZES = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "u64": 8, "s64": 8}
FLOW_ROUNDS = 50        # visits of the pass at most: this many an instruction of the function, and 100
LEAST_FIELDS = 2        # fields a structure needs


class Evidence:
    """What one function's machine code says about memory at fixed addresses: accesses [(address,
    type, origin or None, indexed)] (address the constant part where indexed), escapes {addresses
    handed out}, made {the addresses its code makes} (dread.relations materialized)."""

    def __init__(self, name):
        self.name = name
        self.accesses, self.escapes, self.made = [], set(), set()


def _address(memory, v):
    """Whether v is an address of memory the plan looks at: in a region of the memory map, not a
    device's, at least SMALLEST_POINTER (where an image starts at 0, small numbers point into it)."""
    from dread.constants import SMALLEST_POINTER
    if v < SMALLEST_POINTER or memory is None:
        return memory is None and v >= SMALLEST_POINTER
    return memory.region(v) is not None and not memory.is_io(v)


def evidence(program, fn, reads=None, scratch=None):
    """The Evidence of one function's machine code (see the module's docstring). reads: {callee:
    the registers it reads at its entry} (dread.regflow read_regs; without it a call hands out every
    argument register); scratch: the registers a call leaves changed (dread.regflow scratch_names,
    found when None). Without the arch layer's value_step (MIPS, MSP430: not written yet) nothing
    but made."""
    from dread.relations import materialized
    arch = program.arch
    out = Evidence(fn.name)
    out.made = materialized(program, fn)
    step = getattr(arch, "value_step", None)
    addrs = fn.insns
    if step is None or not addrs:
        return out
    if scratch is None:
        from dread.regflow import scratch_names
        scratch = scratch_names(program.target, arch)
    memory = program.target.memory
    insns = program.instructions(fn)
    n = len(addrs)
    pos = {a: k for k, a in enumerate(addrs)}
    succ, kinds, targets, effects, ijumps = [[] for _ in range(n)], [None] * n, [None] * n, [None] * n, []
    for k, insn in enumerate(insns):
        if insn is None:
            continue
        kind, target, falls = arch.flow(insn)
        if kind == "jump" and isinstance(target, int) and (target & ~1) in pos:
            succ[k].append(pos[target & ~1])
        elif kind in ("jump", "ijump"):
            kind = "tail"                   # (out of the function: its arguments go with it)
            if not isinstance(target, int):
                ijumps.append(k)
        kinds[k], targets[k] = kind, target
        reg_reads, writes = arch.reg_effects(insn)
        effects[k] = (reg_reads, writes, arch.is_conditional(insn))
        if k + 1 < n and addrs[k] + insn.size == addrs[k + 1] and (kind in ("next", "call", "icall") or falls):
            succ[k].append(k + 1)
    starts = {0, pos.get(fn.entry, pos.get(fn.entry & ~1, 0))}
    reached = starts | {s for o in succ for s in o}
    unreached = [k for k in range(n) if k not in reached]
    for k in ijumps:
        succ[k].extend(unreached)
    fixed = {r: (v & MASK, False, None) for r, v in program.image.fixed.items()}
    isas = [program.isa_at(a) for a in addrs]
    before = [None] * n
    for k in starts | (set() if ijumps else set(unreached)):
        before[k] = dict(fixed)

    def run(k, known):
        """(the registers after instruction k, its accesses, the registers it stores): known and the
        result map a register to (value, indexed, origin)."""
        plain = {r: v[:2] for r, v in known.items()}
        values, accesses, stored = step(insns[k], isas[k], plain, program)
        reg_reads, writes, conditional = effects[k]
        origins = [known[r][2] for r in sorted(reg_reads) if r in known and known[r][2] is not None]
        after = dict(known)
        for r in writes:
            after.pop(r, None)
        for r, v in values.items():
            if v is None or (conditional and plain.get(r) != v):
                after.pop(r, None)
                continue
            if not v[1] and v[0] in out.made and _address(memory, v[0]):
                origin = v[0]               # (made here: a literal, movw and movt, lui with addi)
            elif origins:                   # (moved, copied or added to: what it came from)
                origin = origins[0]
            else:
                origin = None
            after[r] = (v[0], v[1], origin)
        if kinds[k] in ("call", "icall"):
            for r in scratch:
                after.pop(r, None)
        after.update(fixed)
        return after, accesses, stored
    work = [k for k in range(n) if before[k] is not None]
    heapq.heapify(work)
    queued, budget = set(work), FLOW_ROUNDS * n + 100
    while work and budget:
        budget -= 1
        k = heapq.heappop(work)
        queued.discard(k)
        if insns[k] is None:
            continue
        after = run(k, before[k])[0]
        for s in succ[k]:
            old = before[s]
            new = dict(after) if old is None else {r: v for r, v in old.items() if after.get(r) == v}
            if new != old:
                before[s] = new
                if s not in queued:
                    heapq.heappush(work, s)
                    queued.add(s)
    abi = program.target.abi
    for k in range(n):
        known = before[k]
        if known is None or insns[k] is None:
            continue
        _, accesses, stored = run(k, known)
        reg_reads = sorted(effects[k][0])
        for base, offset, ctype, ix, _, _ in accesses:
            address = (base + offset) & MASK
            if not _address(memory, address):
                continue
            # (the origin of the register the access went through: one it reads that holds the base)
            origin = next((known[r][2] for r in reg_reads if r in known and r not in fixed
                           and known[r][0] == base and known[r][2] is not None), None)
            out.accesses.append((address, ctype, origin, ix))
        handed = list(stored)
        if kinds[k] in ("call", "icall", "tail"):
            callee = program.by_entry.get(targets[k] & ~1) if isinstance(targets[k], int) else None
            if reads is not None and callee is not None and callee.name in reads:
                handed += [r for r in abi.arg_regs if r in reads[callee.name]]
            else:
                handed += list(abi.arg_regs)
        elif kinds[k] == "return":
            handed += list(abi.result)
        for r in handed:
            v = known.get(r)
            if v is not None and r not in fixed and _address(memory, v[0]):
                out.escapes.add(v[0])
    return out


def gather(program, names=None, flow=None):
    """{name: Evidence} of the functions of names (default: every function of the program), with
    dread.regflow's flow for what each callee reads (flow: a dread.regflow result, or None). A
    function whose pass fails (an instruction a layer's value_step does not expect) gets an empty
    Evidence: an aid to reading must not stop the gate that uses it."""
    from dread.regflow import scratch_names
    scratch = scratch_names(program.target, program.arch)
    fns = [program.by_name[n] for n in names if n in program.by_name] if names is not None else program.functions
    reads = {fn.name: set(flow.read_regs(fn.name)) for fn in program.functions} if flow is not None else None
    out = {}
    for fn in fns:
        try:
            out[fn.name] = evidence(program, fn, reads, scratch)
        except Exception:
            out[fn.name] = Evidence(fn.name)
    return out


def layout(fields, base):
    """[(offset from base, type)] of the fields ({offset from base: {type: count}}) a C structure at
    base holds at their offsets without packing: naturally aligned both at their offset and at their
    address, not overlapping a field before them; an offset accessed with several sizes takes its most
    frequent one (a tie the wider), unsigned unless only signed accesses have that size (as
    dread.shapes lays out a kind). A 64-bit access counts as two words (the lifters write it so)."""
    rows, end = [], 0
    for off in sorted(o for o in fields if o >= 0):
        by_size = {}
        for t, c in fields[off].items():
            by_size[SIZES.get(t, 4)] = by_size.get(SIZES.get(t, 4), 0) + c
        size = max(by_size, key=lambda z: (by_size[z], z))
        signed = [t for t in fields[off] if SIZES.get(t, 4) == size and t.startswith("s")]
        unsigned = [t for t in fields[off] if SIZES.get(t, 4) == size and not t.startswith("s")]
        ctype = ("s%d" if signed and not unsigned else "u%d") % (size * 8)
        if off % size or (base + off) % size or off < end:
            continue
        rows.append((off, ctype))
        end = off + size
    return rows


# ---- what readable C indexes: the scale, which the machine code's pass does not follow

WORD = 1 << 32
WIDE = ("u32", "s32", "u64", "s64")     # casts that keep a value modulo 2**32 (narrower ones cut it)


def linear(e):
    """(constant, {key: [coefficient, term]}) of an integer expression of readable C as a constant plus
    terms times constants, modulo 2**32: sums and differences, a constant times a term, a term shifted
    left by a constant, a negation, and casts to an integer type of 32 bits or more or to a pointer
    (which readable C only converts back) looked through; anything else is a term of its own, keyed by
    its text, so that terms alike add up. 0x200299f8u + (((r2 << 2) + r2) << 2) + 4 is (0x200299fc,
    {"r2": [20, r2]}). Lifted C computes in 32-bit registers, so the sum modulo 2**32 is the value; a
    narrower variable promoted to int gives the same value modulo 2**32."""
    from dread import cparse as C
    terms = {}

    def add(x, k):
        k %= WORD
        if isinstance(x, C.Num) and isinstance(x.value, int):
            return k * x.value % WORD
        if isinstance(x, C.Cast) and (x.type.endswith("*") or " ".join(x.type.split()) in WIDE):
            return add(x.e, k)
        if isinstance(x, C.Binary) and x.op in ("+", "-"):
            return (add(x.l, k) + add(x.r, k if x.op == "+" else -k)) % WORD
        if isinstance(x, C.Binary) and x.op == "*":
            for a, b in ((x.l, x.r), (x.r, x.l)):
                c = constant(a)
                if c is not None:
                    return add(b, k * c)
        if isinstance(x, C.Binary) and x.op == "<<":
            c = constant(x.r)
            if c is not None and c < 32:
                return add(x.l, k << c)
        if isinstance(x, C.Unary) and x.op in ("-", "+"):
            return add(x.e, -k if x.op == "-" else k)
        slot = terms.setdefault(C.expr_text(x), [0, x])
        slot[0] = (slot[0] + k) % WORD
        return 0
    const = add(e, 1)
    return const, {key: slot for key, slot in terms.items() if slot[0]}


def constant(e):
    """The value of a constant expression of readable C modulo 2**32 (linear), None for any other."""
    const, terms = linear(e)
    return None if terms else const


def scaled(addr, facts=None, used=None):
    """(constant, scale, index) of an address that is a constant plus one term times a scale from 1 to
    2**31 - 1, the term the index: 0x200299f8u + 0x14u * r3 + 4 is (0x200299fc, 20, r3). facts:
    {variable: (k, index, c[, base])} of variables that hold index times k plus c (straight_facts,
    scales_of; base: base_of): such a variable as the term counts as that (index None where only the
    scale is known), and goes into used (a set) when given. None for anything else (no term, several, a
    scale that is negative modulo 2**32)."""
    from dread import cparse as C
    const, terms = linear(addr)
    if len(terms) != 1:
        return None
    k, term = next(iter(terms.values()))
    via = None
    if facts and isinstance(term, C.Name) and term.id in facts:
        via = term.id
        k2, index, c = facts[term.id][:3]
        const, k, term = (const + k * c) % WORD, k * k2 % WORD, index
    if not 0 < k < 1 << 31:
        return None
    if via is not None and used is not None:
        used.add(via)
    return const, k, term


BELOW = 256     # elements below the base the code made that an index it computes may reach (base_of)


def literals(e):
    """[(value, coefficient)] of the constants a sum of readable C adds whole: through sums and
    differences, a negation and the casts linear looks through, not inside a product, a shift or any
    other term (0x14u * (r0 - 1) + 0x20000020u + 4 adds 0x20000020 and 4; its 0x14 and 1 are a
    product's)."""
    from dread import cparse as C
    out = []

    def walk(x, k):
        if isinstance(x, C.Num) and isinstance(x.value, int):
            out.append((x.value % WORD, k))
        elif isinstance(x, C.Cast) and (x.type.endswith("*") or " ".join(x.type.split()) in WIDE):
            walk(x.e, k)
        elif isinstance(x, C.Binary) and x.op in ("+", "-"):
            walk(x.l, k)
            walk(x.r, k if x.op == "+" else -k)
        elif isinstance(x, C.Unary) and x.op in ("-", "+"):
            walk(x.e, -k if x.op == "-" else k)
    walk(e, 1)
    return out


def base_of(addr, facts=None, memory=None):
    """The base of an address of readable C that a constant plus an index times a scale makes (scaled):
    the address the code made and added the index and offsets to, the literal it loads. It is the one
    constant the sum adds whole (literals) that is an address the plan looks at, or, where the sum's one
    term is a variable of facts (straight_facts, scales_of) taken whole, the base that variable's value
    adds; None where there is none or more than one. linear folds it with the rest: in 0x14u * (r0 - 1)
    + 0x20000020u + 4, table[id - 1].level of an array at 0x20000020, the constant is 0x20000010, an
    element below the array, and the base is 0x20000020 (GCC and clang make the array's own address
    and fold the -1 into the index's arithmetic or the access's offset, on ARM and RISC-V alike)."""
    from dread import cparse as C
    found = {v for v, k in literals(addr) if k == 1 and _address(memory, v)}
    if facts:
        _, terms = linear(addr)
        if len(terms) == 1:
            k, term = next(iter(terms.values()))
            if isinstance(term, C.Name) and term.id in facts and k == 1 and len(facts[term.id]) > 3 \
                    and facts[term.id][3] is not None:
                found.add(facts[term.id][3])
    return found.pop() if len(found) == 1 else None


def below_base(base, c, k, memory=None):
    """Whether an access's constant c lies below its base (base_of) by at most BELOW elements of k
    bytes: an index the code computes below the address it made (table[i - 1], lut[c - '0'])."""
    return base is not None and base - BELOW * k <= c < base and _address(memory, base)


def drop_unread(fn, names):
    """Removes the plain assignments to the variables of names that nothing reads any more (an index
    times a scale, which the rewrite wrote as the index: r8_3 = 0x14u * r8_2 with g_200299f8[r8_2] in
    its place), where the value has no effect (no call, no volatile access) and the statement stands in
    a list of statements; the declaration stays (a names proposal names the variables it was made for)."""
    from dread import cparse as C
    from dread.cflow import has_effects, walk_expr
    from dread.naming import _exprs, _inner
    if not names:
        return
    read = set()
    for s in _statements(fn):
        got = _assignment(s)
        for e in _exprs(s):
            for x in walk_expr(e.value if got is not None and e is s.e else e):
                if isinstance(x, C.Name):
                    read.add(x.id)
            for x in walk_expr(e):
                if isinstance(x, C.Assign) and x.op != "=" and isinstance(x.target, C.Name):
                    read.add(x.target.id)
    gone = set(names) - read

    def keep(s):
        got = _assignment(s)
        return got is None or got[0] not in gone or has_effects(got[1])
    for s in [fn.body] + _statements(fn):
        if isinstance(s, C.Block):
            s.body[:] = [x for x in s.body if keep(x)]


def _statements(fn):
    """Every statement of the function, nested ones too, each before what it holds."""
    from dread.naming import _inner
    out, work = [], list(reversed(fn.body.body))
    while work:
        s = work.pop()
        out.append(s)
        work.extend(reversed(_inner(s)))
    return out


def _written(statements):
    """(names written, whether a label or case is among them) of statements and what they hold: assigned
    with any operator, ++ or --, a declaration's initial value, an asm's outputs."""
    from dread import cparse as C
    from dread.cflow import walk_expr
    from dread.naming import _exprs, _inner
    names, labelled, work = set(), False, list(statements)
    while work:
        s = work.pop()
        if isinstance(s, (C.Label, C.Case)):
            labelled = True
        if isinstance(s, C.Decl):
            names.update(d.name for d in s.items if d.init is not None)
        if isinstance(s, C.Asm):
            names.update(x.id for o in s.outputs for x in walk_expr(o.e) if isinstance(x, C.Name))
        for e in _exprs(s):
            for x in walk_expr(e):
                if isinstance(x, C.Assign) and isinstance(x.target, C.Name):
                    names.add(x.target.id)
                elif isinstance(x, (C.Post, C.Unary)) and x.op in ("++", "--") and isinstance(x.e, C.Name):
                    names.add(x.e.id)
        work.extend(_inner(s))
    return names, labelled


def _unsure(fn, params=True):
    """The function's variables whose value no assignment tells for sure: their address taken (a
    callee may write them), an asm's outputs, and with params its parameters (the caller's)."""
    from dread import cparse as C
    from dread.cflow import walk_expr
    from dread.naming import _exprs
    out = {n for _, n in fn.params} if params else set()
    for s in _statements(fn):
        if isinstance(s, C.Asm):
            out.update(x.id for o in s.outputs for x in walk_expr(o.e) if isinstance(x, C.Name))
        for e in _exprs(s):
            out.update(x.e.id for x in walk_expr(e) if isinstance(x, C.Unary) and x.op == "&"
                       and isinstance(x.e, C.Name))
    return out


def _assignment(s):
    """(variable, value) of a statement that is a plain assignment to a variable, else None."""
    from dread import cparse as C
    if isinstance(s, C.ExprStmt) and isinstance(s.e, C.Assign) and s.e.op == "=" and isinstance(s.e.target, C.Name):
        return s.e.target.id, s.e.value
    return None


def scales_of(fn, memory=None):
    """{variable: (k, None, c, base)} of the function's variables that hold some index times k plus c
    wherever they are read (for scaled): each assignment to one gives it k times one term plus c
    (linear), all the same k and c, the term perhaps another such variable (r8_3 = 0x14u * r8_2; r10 =
    0x200299f8u + r8_3), nothing but plain assignment statements write it (no ++, +=, assignment inside
    an expression or initial value), and it is none of _unsure (a parameter's value is the caller's).
    base: the address the assignments add whole where they all add the same one (base_of, with memory;
    the variable it is made from may carry it), else None. What the plan learns the scale of an access
    through such a variable from, and its base."""
    from dread import cparse as C
    from dread.cflow import walk_expr
    from dread.naming import _exprs
    defs, bad = {}, set(_unsure(fn))
    for s in _statements(fn):
        got = _assignment(s)
        if got is not None:
            defs.setdefault(got[0], []).append(linear(got[1]) + (base_of(got[1], None, memory),))
        if isinstance(s, C.Decl):
            bad.update(d.name for d in s.items if d.init is not None)
        for e in _exprs(s):
            for x in walk_expr(e):
                if isinstance(x, C.Assign) and isinstance(x.target, C.Name) and not (got is not None and x is s.e):
                    bad.add(x.target.id)
                elif isinstance(x, (C.Post, C.Unary)) and x.op in ("++", "--") and isinstance(x.e, C.Name):
                    bad.add(x.e.id)
    facts, undecided = {}, {v for v in defs if v not in bad}
    for _ in range(4):                      # (a variable made from another: a few levels)
        changed = False
        for v in sorted(undecided):
            got, bases, wait = set(), set(), False
            for const, terms, base in defs[v]:
                if len(terms) != 1:
                    got = None
                    break
                k, term = next(iter(terms.values()))
                if isinstance(term, C.Name) and term.id in facts:
                    k2, _, c2, b2 = facts[term.id]
                    if base is None and k == 1:
                        base = b2           # (r10 = r8_3 + 4 where r8_3 adds the base)
                    const, k = (const + k * c2) % WORD, k * k2 % WORD
                elif isinstance(term, C.Name) and term.id in undecided:
                    wait = True             # (made from a variable not decided yet: the next round)
                    break
                got.add((k, const))
                bases.add(base)
            if wait:
                continue
            undecided.discard(v)
            changed = True
            if got and len(got) == 1 and 0 < next(iter(got))[0] < 1 << 31:
                k, c = next(iter(got))
                facts[v] = (k, None, c, bases.pop() if len(bases) == 1 else None)
        if not changed:
            break
    return facts


def straight_facts(fn, visit, memory=None):
    """Calls visit(statement, facts) for every statement of the function, each before what it holds,
    facts {variable: (k, index, c, base)}: the variables that hold an index variable times k plus c all
    through the statement (for scaled), by an assignment v = k * x + c earlier in the same list of
    statements or in one around it (k above 1 where c is 0: no copy), and nothing between it and the
    statement, nor the statement with what it holds, writes v or x, nor is there a label or case
    between them (a jump could come past the assignment). A variable whose value an assignment does not
    tell (_unsure) is in none; base the address the assignment adds whole (base_of, with memory), or
    None. Where the rewrite of an indexed access may write the index itself (g[r8_2] for 0x200299f8u +
    r8_3; g[s1].f_4 for a3 + 4 after a3 = 0x80095914u + s1 * 20; g[r0 - 1].f_4 for r3 + 4 after r3 =
    0x14u * (r0 - 1) + 0x20000020u)."""
    from dread import cparse as C
    from dread.naming import _inner, variables
    unsure = _unsure(fn, params=False)      # (a parameter assigned holds what it was assigned)
    own = set(variables(fn)) - unsure
    cache = {}

    def written(s):
        if id(s) not in cache:
            cache[id(s)] = _written([s])
        return cache[id(s)]

    def walk(stmts, facts):
        for s in stmts:
            names, labelled = written(s)
            if labelled:
                facts = {}
            facts = {v: f for v, f in facts.items() if v not in names and f[1].id not in names}
            # (what the statement makes known, taken before visit rewrites it: the index a variable of
            # the function, never memory, which the use would read again; a scale, not a copy)
            got, fact = _assignment(s), None
            if got is not None and got[0] in own:
                found = scaled(got[1], facts)
                if found is not None and (found[0] or found[1] > 1) and isinstance(found[2], C.Name) \
                        and found[2].id != got[0] and found[2].id in own:
                    fact = (got[0], (found[1], found[2], found[0], base_of(got[1], facts, memory)))
            visit(s, facts)
            for inner in ([s.body] if isinstance(s, C.Block) else [[x] for x in _inner(s)]):
                walk(inner, facts)  # (an if's arms each alone: they are alternatives)
            if fact is not None:
                facts = dict(facts)
                facts[fact[0]] = fact[1]
    walk(fn.body.body, {})


LEAST_STRIDE = 2        # the least step of a walk that says an array lies there (walks; bytes: a text, a buffer)


def walks(fn, memory=None):
    """{variable: (start, stride)} of the function's variables that walk static data with a pointer: one
    plain assignment gives it a constant address the plan looks at (its start) and every other one adds
    one constant stride to it, at least LEAST_STRIDE and below 2**31 (v = v + 4: a pointer that goes up an
    element at a time), nothing else writes it (no ++, +=, assignment inside an expression or initial
    value: readable C before dread.polish) and it is none of _unsure. FreeRTOS GCC -Os's
    vStartTimerDemoTask fills xAutoReloadTimers so: r5 = 0x200017dcu; ... *(volatile u32 *)r5 = r0_1; r5 =
    r5 + 4. A walk that goes down, steps by a variable or starts at two addresses is none."""
    from dread import cparse as C
    from dread.cflow import walk_expr
    from dread.naming import _exprs
    defs, bad = {}, set(_unsure(fn))
    for s in _statements(fn):
        got = _assignment(s)
        if got is not None:
            defs.setdefault(got[0], []).append(got[1])
        if isinstance(s, C.Decl):
            bad.update(d.name for d in s.items if d.init is not None)
        for e in _exprs(s):
            for x in walk_expr(e):
                if isinstance(x, C.Assign) and isinstance(x.target, C.Name) and not (got is not None and x is s.e):
                    bad.add(x.target.id)
                elif isinstance(x, (C.Post, C.Unary)) and x.op in ("++", "--") and isinstance(x.e, C.Name):
                    bad.add(x.e.id)
    out = {}
    for v, values in sorted(defs.items()):
        if v in bad:
            continue
        starts, steps = set(), set()
        for value in values:
            const, terms = linear(value)
            if not terms:
                starts.add(const)
            elif list(terms) == [v] and terms[v][0] == 1 and isinstance(terms[v][1], C.Name):
                steps.add(const)
            else:
                break
        else:
            if len(starts) == 1 and len(steps) == 1:
                a, k = starts.pop(), steps.pop()
                if LEAST_STRIDE <= k < 1 << 31 and _address(memory, a):
                    out[v] = (a, k)
    return out


def _walked(addr, found):
    """(the address, stride, start) of an address of readable C that is a walking variable (found: walks)
    plus a constant c, at the start plus c in the walk's first step, else None."""
    from dread import cparse as C
    const, terms = linear(addr)
    if len(terms) != 1:
        return None
    k1, term = next(iter(terms.values()))
    if k1 != 1 or not isinstance(term, C.Name) or term.id not in found:
        return None
    a, k = found[term.id]
    return (a + const) % WORD, k, a


def indexed_accesses(fn, memory=None):
    """[(constant, scale, type, base[, walk])] of the function's loads and stores (readable C: *(volatile
    u32 *)(...)) at a constant plus a scaled index (scaled, with the scales of scales_of), the scale at
    least the access's width: what says that an array of elements of that size lies there. base: the
    address the code made (base_of) or None. The constant is an address the plan looks at, or lies below
    the base by at most BELOW elements (below_base: table[i - 1], whose constant can lie in front of the
    memory region the array starts). 0x200299f8u + 0x14u * r9_2 + 4 is (0x200299fc, 20, "u32",
    0x200299f8). An access through a variable that walks static data (walks) plus a constant c is one
    at its start plus c with its stride as the scale and its start as the base, marked (walk True: the
    plan takes it only where the start is a base that counts, find_arrays): *(volatile u32 *)(r5 - 4)
    with r5 walking from 0x200017dc by 4 is (0x200017d8, 4, "u32", 0x200017dc, True)."""
    from dread import cparse as C
    from dread.cflow import walk_expr
    from dread.naming import ACCESS, _exprs
    facts = scales_of(fn, memory)
    walking = walks(fn, memory)
    out = []
    for s in _statements(fn):
        for e in _exprs(s):
            for x in walk_expr(e):
                if not (isinstance(x, C.Unary) and x.op == "*" and isinstance(x.e, C.Cast)):
                    continue
                m = ACCESS.match(" ".join(x.e.type.split()))
                if not m or isinstance(x.e.e, C.Num):
                    continue
                walked = _walked(x.e.e, walking) if walking else None
                if walked is not None:
                    c, k, a = walked
                    if k >= SIZES.get(m.group(1), 4) and (_address(memory, c) or below_base(a, c, k, memory)):
                        out.append((c, k, m.group(1), a, True))
                    continue
                got = scaled(x.e.e, facts)
                if got is None or got[1] < SIZES.get(m.group(1), 4):
                    continue
                base = base_of(x.e.e, facts, memory)
                if _address(memory, got[0]) or below_base(base, got[0], got[1], memory):
                    out.append((got[0], got[1], m.group(1), base))
    return out


def handed(trees, shapes_, memory=None):
    """{function: [(address, {offset: {type: count}}, scale or None, base or None)]} of the addresses of
    static data
    the readable C of the trees ([(tree, fn, macros)]) hands to a callee for a parameter that the callee
    (and what it calls) reaches at constant offsets: its template in the shapes' summaries (shapes_:
    dread.shapes analyse, its .summaries {callee: {"params": {i: template}}}) holds at least two fields
    from offset 0 up (memset and strcmp touch offset 0 of whatever they get), none below (a callee that
    takes its pointer as one into something larger: a header in front), and describes no memory a walk
    reached (Shapes.walked_template: a buffer handed on to memcpy, whose unrolled copy reaches words 0
    to 60 of whatever it gets). A parameter points into one object, so what the callee
    reaches through it lies in the object at that address (RISC-V's lists, which their functions reach
    at no constant address: vListInitialise(&xDelayedTaskList1) reaches all five words of it). An
    address that is a constant plus an index times a scale (scaled: &pxReadyTasksLists[uxPriority]
    handed to vListInsertEnd) comes with the scale, its constant the address, and its base (base_of):
    what the callee reaches lies in an element of an array of that element size (the element the
    constant lies in counted from the base where it lies below it: &table[i - 1]); so does a variable
    that walks static data (walks) plus a constant, with its stride as the scale and its start as the
    base (vListInitialise(r4) with r4 walking pxReadyTasksLists by 20)."""
    from dread import cparse as C
    from dread.cflow import walk_expr
    from dread.constants import SMALLEST_POINTER
    from dread.naming import _exprs
    summaries = shapes_.summaries
    out = {}
    for _, fn, _ in trees:
        facts, walking = None, None
        for s in _statements(fn):
            for e in _exprs(s):
                for x in walk_expr(e):
                    if not (isinstance(x, C.Call) and isinstance(x.f, C.Name) and x.f.id in summaries):
                        continue
                    params = summaries[x.f.id].get("params", {})
                    for i, a in enumerate(x.args):
                        tmpl = params.get(i)
                        if tmpl is None or tmpl[0] != "copy" or any(rel < 0 for rel in tmpl[1]) \
                                or shapes_.walked_template(tmpl):
                            continue
                        v, k, base = constant(a), None, None
                        if v is None:
                            if facts is None:
                                facts, walking = scales_of(fn, memory), walks(fn, memory)
                            got = _walked(a, walking) if walking else None
                            if got is not None:
                                v, k, base = got
                            else:
                                got = scaled(a, facts)
                                if got is None or got[1] < 2:
                                    continue
                                v, k, base = got[0], got[1], base_of(a, facts, memory)
                        if not (v >= SMALLEST_POINTER and _address(memory, v)) and not (
                                k is not None and below_base(base, v, k, memory)):
                            continue
                        fields = {rel: {t: 1 for t in types} for rel, (types, _, _) in sorted(tmpl[1].items())
                                  if types}
                        if len(fields) >= LEAST_FIELDS:
                            out.setdefault(fn.name, []).append((v, fields, k, base))
    return out


NEAR = 64               # bytes around a structure where its neighbours lie (separate_bases)


def plan(program, evidence_, extra=None, symbols=True, near=NEAR, trust="evidence", indexed=None, arrays=None,
         handed=None):
    """{base: [(offset, type)]} of the structures of static data the evidence ({name: Evidence},
    gather) shows (see the module's docstring). extra: more accesses at fixed addresses {function:
    [(address, type)]} for the fields (the readable C's: an ldm's or stm's words, which the evidence
    knows only by their first, a conditional literal's); symbols: whether the target's data symbols end
    a structure (they never make one: a symbol a person gave the first variable of an anchor's run would
    make the run one); trust: "evidence" (a base counts only where the code reaches its neighbours
    apart from it: separate_bases), "all" (every base: for measuring what the evidence keeps out).
    indexed: the readable C's accesses at a constant plus a scaled index {function: [(constant, scale,
    type[, base])]} (indexed_accesses); with arrays, a dict, the arrays they show go into it ({start: (element
    size, count, [(offset in the element, type)])}, find_arrays) and the structures that lie inside one
    are left out (its elements' fields hold their accesses). handed: what callees reach through the
    addresses of static data the readable C hands them ({function: [(address, {offset: {type:
    count}}, scale, base)]}, statics.handed): each such address is a base whose structure reaches as far as the
    callee does, its fields there too; it counts without separate_bases (a parameter points into one
    object), ends only where a data symbol, a startup range, an array or another such base starts (an
    address handed out inside it is a member's), and a structure inside it is left out."""
    memory = program.target.memory
    accesses, starts, reach, below, seen, through = {}, set(), {}, set(), {}, {}

    def note(fname, a, t, origin=None):
        a &= MASK
        accesses.setdefault(a, {}).setdefault(t, 0)
        accesses[a][t] += 1
        seen.setdefault(fname, {}).setdefault(a, set()).add(origin)
    for ev in evidence_.values():
        starts |= ev.escapes
        for address, ctype, origin, ix in ev.accesses:
            if ix:
                # (an array starts at its constant part, or at the address the code made where the
                # access lies below it: an index the code computes below its base, table[i - 1])
                starts.add(origin if origin is not None and address < origin else address)
                continue
            for a, t in ([(address, "u32"), (address + 4, "u32")] if SIZES.get(ctype, 4) == 8 else [(address, ctype)]):
                note(ev.name, a, t, origin)
            if origin is None:
                continue
            starts.add(origin)
            through.setdefault(origin, set()).add(ev.name)
            off = (address - origin) & MASK
            if off >= 0x80000000:
                below.add(origin)
                continue
            reach[origin] = max(reach.get(origin, 0), off + SIZES.get(ctype, 4))
    for fname, rows in sorted((extra or {}).items()):
        for address, ctype in rows:
            if _address(memory, address):
                note(fname, address, ctype)
    owned = {}                              # a handed base -> how far its callees reach
    for fname, rows in sorted((handed or {}).items()):
        for a, fields, k, *_ in rows:
            if not _address(memory, a) or k is not None:
                continue                    # (an element's: find_arrays)
            for off, types in fields.items():
                for t, c in types.items():
                    accesses.setdefault((a + off) & MASK, {}).setdefault(t, 0)
                    accesses[(a + off) & MASK][t] += c      # (no access of the caller's: not in seen)
            owned[a] = max([owned.get(a, 0)] + [off + SIZES.get(t, 4) for off, types in fields.items() for t in types])
    data_symbols = {a for kind, a, _ in program.symbols() if kind == "d"} if symbols else set()
    starts |= data_symbols
    bounds = set(data_symbols)              # (what ends an array: a variable or a startup range starts)
    for c in program.image.startup():       # (an object lies in one of the startup's ranges)
        starts |= {c.run, c.run + c.size}
        bounds |= {c.run, c.run + c.size}
    found = {}
    if indexed:
        before = sorted(starts)

        def own_end(o):
            """Where a structure at base o ends (as below, before the arrays): its reach, cut where the next
            object starts and at its region's end; the end a base that counts is tried with (on RISC-V GCC
            reaches the variables behind an array through the array's upper half: the array's base reached
            them, and tried up to its full reach it counted for no array where it counted for a structure)."""
            end = o + reach.get(o, 0)
            i = bisect.bisect_right(before, o)
            if i < len(before):
                end = min(end, before[i])
            region = memory.region(o) if memory is not None else None
            return min(end, region.end) if region is not None else end
        found = find_arrays(indexed, evidence_, accesses, data_symbols, bounds, memory,
                            [r for _, rows in sorted((handed or {}).items()) for r in rows],
                            lambda o: trust == "all" or (o in through and separate_bases(
                                o, own_end(o), through[o], seen, near)))
        starts |= set(found)
        if arrays is not None:
            arrays.update(found)
    spans = sorted((a, a + k * n) for a, (k, n, _) in found.items())
    hard = sorted(bounds | set(found) | set(owned))
    ends = {}                               # a handed base -> where its structure ends
    for a in sorted(owned):
        i = bisect.bisect_right(spans, (a, MASK + 1)) - 1
        if i >= 0 and spans[i][0] <= a < spans[i][1]:
            continue                        # (an array's element: the array holds it)
        end = a + max(owned[a], reach.get(a, 0))       # (and the code's own accesses from it)
        i = bisect.bisect_right(hard, a)
        if i < len(hard):
            end = min(end, hard[i])
        region = memory.region(a) if memory is not None else None
        if region is not None:
            end = min(end, region.end)
        ends[a] = end
    starts |= set(ends)
    held = sorted(ends.items())
    order = sorted(starts)
    places = sorted(accesses)
    out = {}
    for base in sorted(set(reach) | set(ends)):
        if base in below and base not in ends:
            continue
        i = bisect.bisect_right(spans, (base, MASK + 1)) - 1
        if i >= 0 and spans[i][0] <= base < spans[i][1]:
            continue                        # (inside an array: its elements' fields)
        i = bisect.bisect_right(held, (base, -1)) - 1
        if i >= 0 and held[i][0] < base < held[i][1]:
            continue                        # (inside what a callee reaches from a handed address)
        if base in ends:
            end = ends[base]
        else:
            end = base + reach[base]
            i = bisect.bisect_right(order, base)
            if i < len(order):
                end = min(end, order[i])
            region = memory.region(base) if memory is not None else None
            if region is not None:
                end = min(end, region.end)
            if trust != "all" and not separate_bases(base, end, through[base], seen, near):
                continue
        fields = {}
        for a in places[bisect.bisect_left(places, base):bisect.bisect_left(places, end)]:
            for t, c in accesses[a].items():
                if a + SIZES.get(t, 4) <= end:
                    fields.setdefault(a - base, {})[t] = c
        rows = layout(fields, base)
        if len(rows) >= LEAST_FIELDS and any(SIZES[t] > 1 for _, t in rows):
            out[base] = rows
    if arrays is not None:
        # A structure that is a run of fields of one type, one after another, which the machine code
        # also reaches with an index of that width at one of them, is an array of that type (the
        # readable C shows no scale where its function does not lift). An indexed access below the
        # address it was made from is that address's array's (clang -O2's pxReadyTasksLists[i - 2]
        # reached into xQueueRegistry in front of it and made it an array of words).
        ix = {(a, SIZES.get(t, 4)) for ev in evidence_.values() for a, t, o, i in ev.accesses
              if i and (o is None or a >= o)}
        for base, rows in sorted(out.items()):
            z = SIZES[rows[0][1]]
            if all(SIZES[t] == z and off == j * z for j, (off, t) in enumerate(rows)) \
                    and any((base + off, z) in ix for off, _ in rows):
                arrays[base] = (z, len(rows), [(0, layout({0: {t: 1 for _, t in rows}}, base)[0][1])])
                del out[base]
    return out


def find_arrays(indexed, evidence_, accesses, data_symbols=(), bounds=(), memory=None, handed=(), trusted=None):
    """{start: (element size, count, [(offset in the element, type)])} of the arrays that the readable
    C's indexed accesses show ({function: [(constant, scale, type[, base[, walk]])]}, indexed_accesses):
    an access at a constant plus an index times k says that elements of k bytes lie there, the access's
    place a field of one (an access through a pointer that walks static data, walk True, says so too,
    but only where the array's start is a base that counts, trusted: what the code reaches from it at
    constant places is then the array's; a walk alone makes no array). Accesses with one scale whose
    constants lie less than k apart are one array (the fields of one element: pxReadyTasksLists' counts
    at 0x200299f8 + 20 * i, its indices at 0x200299fc + 20 * i). Where it starts: where every address
    the code made the accesses from (base_of's base, else their origin in evidence_) lies above all of
    them, by at most BELOW elements, the array starts at the lowest of those addresses, or at a data
    symbol within an element below it (_made_above), and the accesses are at elements below its start
    (table[i - 1].level: GCC and clang make table's own address, and the index's -1 and the field's
    offset fold into a constant an element below it; without this the array started at that constant,
    its structure at the lowest field read, and a data symbol in front of it, another variable's, could
    even be taken for its start); else at the greatest data symbol at most k below the lowest of them,
    else at the greatest address the code made such an access from or handed out there, else at the
    lowest constant. It reaches as far as its accesses' elements (at least one) and, where its start is
    a base that counts (trusted(start): plan's separate_bases; not a section anchor nor clang's merged
    globals, whose next variables lie at further elements' places), the elements the code reaches at
    constant addresses from its start (xAutoReloadTimers' [0], [19] and [20]: 21 elements, as declared),
    never past what bounds says starts (a data symbol, a startup range) nor its memory region; arrays of
    one element size where one goes on from the other are one, and of two that overlap with different
    sizes the one with fewer accesses goes. An element's fields are every access inside the array at its
    offset in an element (indexed or at a constant address, through any base; an indexed one below the
    start at its offset in the element it lies in), laid out as layout() does and also aligned in every
    element (the element size a multiple of each field's), none across an element's end; and what
    callees reach through an element's address handed to them (handed: [(address, {offset: {type:
    count}}, scale or None[, base])], statics.handed: a constant one inside the array, or an indexed one
    with the array's element size, also one below the array's start that the array's start is the base
    of), at their offsets from that address. What is not established: that the array is the source's (an
    index into the middle of a structure that holds an array reads as an array of its own) and how many
    elements it has (the count is what the code reaches at constant places, at least one); that a
    compiler which folds a field's offset or the index's constant into the literal it loads (none of GCC
    4.9 and 16 and clang 23 did in our tests) makes the array's start: such a literal is taken for it."""
    origins, escapes = {}, set()
    for ev in evidence_.values():
        escapes |= ev.escapes
        for a, t, o, ix in ev.accesses:
            if ix and o is not None:
                origins.setdefault((ev.name, a, t), o)
    by_scale = {}
    for fname, rows in sorted(indexed.items()):
        for row in rows:
            c0, k, t0 = row[:3]
            made = row[3] if len(row) > 3 and row[3] is not None else origins.get((fname, c0, t0))
            walk = len(row) > 4 and bool(row[4])
            for c, t in _words(c0, t0):
                if k % SIZES.get(t, 4) or not (_address(memory, c) or below_base(made, c, k, memory)):
                    continue                # (an element holds every access whole and aligned)
                by_scale.setdefault(k, []).append((c, t, made, walk))
    symbols_ = sorted(data_symbols)
    candidates = sorted(set(o for rows in by_scale.values() for _, _, o, _ in rows if o is not None) | escapes)
    ends = sorted(bounds)
    through = {}                            # origin -> the addresses accessed through it at constant offsets
    for ev in evidence_.values():
        for a, t, o, ix in ev.accesses:
            if not ix and o is not None:
                through.setdefault(o, set()).add(a)
    groups = []                             # [element size, start, count, rows]
    for k, rows in sorted(by_scale.items()):
        rows.sort(key=lambda r: (r[0], r[1], r[2] or 0, r[3]))
        run = []
        for row in rows + [None]:
            if run and (row is None or row[0] - run[0][0] >= k):
                lo, hi = run[0][0], run[-1][0]
                start = _made_above(run, k, lo, hi, symbols_, memory)
                if start is None:
                    start = _greatest(symbols_, hi - k, lo)
                if start is None:
                    start = _greatest(sorted({o for _, _, o, _ in run if o is not None} | set(
                        candidates[bisect.bisect_right(candidates, hi - k):bisect.bisect_right(candidates, lo)])),
                        hi - k, lo)
                if start is None:
                    start = lo
                n = max([1] + [(c - start) // k + 1 for c, _, _, _ in run])
                counts = trusted is None or trusted(start)  # (a base of its own: what it reaches is the array's)
                if not counts and all(w for _, _, _, w in run):
                    run = [] if row is None else [row]      # (a walk alone: no array)
                    continue
                if counts:
                    n = max([n] + [(a - start) // k + 1 for a in through.get(start, ()) if a >= start])
                end = start + n * k
                i = bisect.bisect_right(ends, start)
                if i < len(ends):
                    end = min(end, ends[i])
                region = memory.region(start) if memory is not None else None
                if region is not None:
                    end = min(end, region.end)
                if end - start >= k:
                    groups.append([k, start, (end - start) // k, list(run)])
                run = []
            if row is not None:
                run.append(row)
    groups.sort(key=lambda g: (g[1], g[0]))
    merged = []
    for g in groups:
        for m in merged:                    # (one array going on from the other: arr[i] and arr[i + 1])
            if m[0] == g[0] and (g[1] - m[1]) % g[0] == 0 and m[1] <= g[1] <= m[1] + m[2] * m[0]:
                m[2] = max(m[2], (g[1] - m[1]) // g[0] + g[2])
                m[3] += g[3]
                break
        else:
            merged.append(g)
    kept = []
    for g in sorted(merged, key=lambda g: (-len(g[3]), g[1], g[0])):
        if all(g[1] + g[0] * g[2] <= h[1] or h[1] + h[0] * h[2] <= g[1] for h in kept):
            kept.append(g)
    places = sorted(accesses)
    out = {}
    for k, start, n, run in sorted(kept, key=lambda g: g[1]):
        fields = {}
        for c, t, *_ in run + [(w, u, o) for (_, a, t), o in sorted(origins.items())
                               if o == start and start <= a < start + k * n for w, u in _words(a, t)]:
            fields.setdefault((c - start) % k, {}).setdefault(t, 0)
            fields[(c - start) % k][t] += 1     # (and the machine code's through its start: scale not shown)
        for a in places[bisect.bisect_left(places, start):bisect.bisect_left(places, start + k * n)]:
            for t, count in accesses[a].items():
                off = (a - start) % k
                fields.setdefault(off, {}).setdefault(t, 0)
                fields[off][t] += count
        for row in handed:                  # (what callees reach through an element's address)
            a, reached, scale = row[:3]
            if scale == k and len(row) > 3 and row[3] == start and below_base(start, a, k, memory):
                a = start + (a - start) % k     # (&table[i - 1]: the element counted from the base)
            if scale not in (None, k) or not start <= a < start + k * n:
                continue
            for rel, types in reached.items():
                off = (a - start) % k + rel
                for t, count in types.items():
                    if off + SIZES.get(t, 4) <= k:
                        fields.setdefault(off, {}).setdefault(t, 0)
                        fields[off][t] += count
        rows = [(off, t) for off, t in layout(fields, start) if off + SIZES[t] <= k and k % SIZES[t] == 0]
        if rows:
            out[start] = (k, n, rows)
    return out


def _made_above(run, k, lo, hi, symbols_=(), memory=None):
    """Where a run of indexed accesses ([(constant, type, made)], find_arrays; lo and hi its lowest and
    greatest constant) starts when the addresses the code made them from lie above the lowest of them:
    the lowest such address that every access lies at most BELOW elements below or within an element
    above (below_base; an anchor further below, a base further above count not), where it lies above lo
    (an index the code computes below the base it made: table[i - 1], gizmo's jump table indexed by
    c - 'b'), or the greatest data symbol (symbols_, sorted) within an element below it (the variable
    it lies in: a field's address a function made, pxReadyTasksLists + 4 on FreeRTOS GCC -Os; or its
    own symbol; not one further below, between the accesses and it: gizmo a32's commands in front of an
    anonymous jump table). None where there is none or the lowest lies at or below lo: the start is
    decided as before (a data symbol, an origin within an element below the lowest, the lowest), so
    that a field's address one function made does not move an array whose start another function
    made."""
    made = sorted({m for _, _, m, *_ in run if m is not None and (below_base(m, lo, k, memory) or hi - k < m <= lo)})
    if not made or made[0] <= lo:
        return None
    m = made[0]
    s = _greatest(symbols_, m - k, m)
    return s if s is not None else m


def _words(address, ctype):
    """[(address, type)] of an access, a 64-bit one as its two words (the lifters write it so; GCC -O2
    stores both words of a registry entry with one strd, which made the element one u64 field)."""
    return [(address, "u32"), (address + 4, "u32")] if SIZES.get(ctype, 4) == 8 else [(address, ctype)]


def _greatest(values, low, high):
    """The greatest of the sorted values above low and at most high, or None."""
    i = bisect.bisect_right(values, high)
    return values[i - 1] if i and values[i - 1] > low else None


def separate_bases(base, end, users, seen, near=NEAR):
    """Whether the code reaches what lies next to [base, end) apart from base: one of the functions
    that reach through base (users) also reaches a place within near bytes in front of base or behind
    end, through another base or none (RISC-V's gp, an upper half made by lui alone). A compiler that
    makes a base per object (GCC with -fdata-sections) does so for neighbours a function uses
    together; a section anchor is the one base its file reaches all of its variables through, so its
    neighbours come only through it, or belong to other files, which seldom lie that close. Measured
    (2026-10-23): of the anchors' runs taken for structures on FreeRTOS GCC -Os and -O2 built without
    -fdata-sections (23 and 21) none passes at up to 64 bytes, one at 256; of the structures of the
    gate's three ARM builds with -fdata-sections as many pass at 64 bytes as at 256, two fewer at 32,
    four at 16. Addresses
    handed out next to the base, made apart from it, are no evidence: GCC makes an anchor's variable's
    address as a literal of its own (.LANCHOR0+20), and five runs of each of those two builds passed."""
    for fname in sorted(users):
        for a, origins in seen.get(fname, {}).items():
            if (base - near <= a < base or end <= a < end + near) and origins - {base}:
                return True
    return False


def image_spans(program):
    """[(start, end)] of the memory where the image keeps static data, sorted and merged: its own bytes
    (a rom's tables), the ranges its startup copies code and data to, zeroes or a loader fills (.data,
    .bss); where the image describes no startup, also every region of the memory map but rom ones (where
    its RAM data lies is not known then). An object of the plan outside them (a number a callee takes for
    a pointer, a place in RAM no section holds, flash behind the image) is no static data of the image:
    dread.naming apply_globals writes no &-expression for it (holds)."""
    spans = [(program.base, program.base + len(program.data))]
    copies = program.image.startup()
    spans += [(c.run, c.run + c.size) for c in copies]
    memory = program.target.memory
    if not copies and memory is not None:
        spans += [(r.base, r.end) for r in memory.regions if r.kind != "rom"]
    out = []
    for a, b in sorted(s for s in spans if s[1] > s[0]):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def holds(spans, address):
    """Whether address lies in one of spans ([(start, end)], sorted and apart: image_spans)."""
    i = bisect.bisect_right(spans, (address, MASK + 1)) - 1
    return i >= 0 and spans[i][0] <= address < spans[i][1]


def describe(statics, arrays=None):
    """Lines saying what the plan holds: per structure its base, its size and its fields; per array
    (arrays, find_arrays) its start, its elements' count and size and an element's fields."""
    lines = []
    for base, rows in sorted(statics.items()):
        end = max(off + SIZES[t] for off, t in rows)
        lines.append("0x%08x %4d bytes: %s" % (base, end, ", ".join("%x:%s" % r for r in rows)))
    for base, (k, n, rows) in sorted((arrays or {}).items()):
        lines.append("0x%08x %4d bytes: %d x %d: %s" % (base, k * n, n, k, ", ".join("%x:%s" % r for r in rows)))
    return lines


# ---- against the source (debug information)

def source_objects(elf_path):
    """[(address, size, name, kind, leaves, element)] of the variables at fixed addresses in an ELF's
    debug information (DW_OP_addr, DWARF 5's DW_OP_addrx), sorted: kind "struct", "union", "array of
    struct", "array of scalar" or "scalar"; leaves {(offset, size)} of what it holds (a structure's
    members' scalars at their offsets, an array's elements', at most 1024 of them); element an array's
    element size (None for the rest). [] without pyelftools or debug information."""
    try:
        from elftools.elf.elffile import ELFFile
    except ImportError:
        return []
    out = []
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        if not elf.has_dwarf_info():
            return []
        dwarf = elf.get_dwarf_info()
        complete = {}

        def strip(die):
            while die is not None and die.tag in ("DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type",
                                                  "DW_TAG_atomic_type", "DW_TAG_restrict_type"):
                if "DW_AT_type" not in die.attributes:
                    return None
                die = die.get_DIE_from_attribute("DW_AT_type")
            if die is not None and "DW_AT_declaration" in die.attributes and "DW_AT_name" in die.attributes:
                die = complete.get((die.tag, die.attributes["DW_AT_name"].value), die)
            return die

        def count_of(die):
            n = 1
            for sub in die.iter_children():
                if sub.tag == "DW_TAG_subrange_type":
                    if "DW_AT_count" in sub.attributes:
                        n *= sub.attributes["DW_AT_count"].value
                    elif "DW_AT_upper_bound" in sub.attributes and isinstance(sub.attributes["DW_AT_upper_bound"].value,
                                                                               int):
                        n *= sub.attributes["DW_AT_upper_bound"].value + 1
                    else:
                        return None
            return n

        def size_of(die):
            die = strip(die)
            if die is None:
                return None
            if "DW_AT_byte_size" in die.attributes:
                return die.attributes["DW_AT_byte_size"].value
            if die.tag == "DW_TAG_pointer_type":
                return 4
            if die.tag == "DW_TAG_array_type" and "DW_AT_type" in die.attributes:
                es, n = size_of(die.get_DIE_from_attribute("DW_AT_type")), count_of(die)
                return es * n if es and n else None
            return None

        def leaves(die, base, into, depth=0):
            die = strip(die)
            if die is None or depth > 8 or len(into) > 4096:
                return
            if die.tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
                for m in die.iter_children():
                    if m.tag == "DW_TAG_member" and "DW_AT_type" in m.attributes:
                        loc = m.attributes.get("DW_AT_data_member_location")
                        off = loc.value if loc is not None and isinstance(loc.value, int) else 0
                        leaves(m.get_DIE_from_attribute("DW_AT_type"), base + off, into, depth + 1)
            elif die.tag == "DW_TAG_array_type" and "DW_AT_type" in die.attributes:
                elem = die.get_DIE_from_attribute("DW_AT_type")
                es, n = size_of(elem), count_of(die)
                if es and n:
                    for k in range(min(n, 1024)):
                        leaves(elem, base + k * es, into, depth + 1)
            else:
                z = size_of(die)
                if z:
                    into.add((base, z))

        def kind_of(die):
            die = strip(die)
            if die is None:
                return "scalar"
            if die.tag == "DW_TAG_array_type":
                e = strip(die.get_DIE_from_attribute("DW_AT_type")) if "DW_AT_type" in die.attributes else None
                inner = e is not None and e.tag in ("DW_TAG_structure_type", "DW_TAG_union_type", "DW_TAG_array_type")
                return "array of struct" if inner else "array of scalar"
            if die.tag == "DW_TAG_structure_type":
                return "struct"
            if die.tag == "DW_TAG_union_type":
                return "union"
            return "scalar"
        for cu in dwarf.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag in ("DW_TAG_structure_type", "DW_TAG_union_type") and "DW_AT_name" in die.attributes \
                        and "DW_AT_declaration" not in die.attributes:
                    complete.setdefault((die.tag, die.attributes["DW_AT_name"].value), die)
        for cu in dwarf.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_variable" or "DW_AT_location" not in die.attributes:
                    continue
                loc = die.attributes["DW_AT_location"]
                v = loc.value if loc.form in ("DW_FORM_exprloc", "DW_FORM_block1") else None
                place = location(v, lambda index: dwarf.get_addr(cu, index)) if v else None
                if place is None:
                    continue
                address, stored = place
                d = die
                if "DW_AT_type" not in d.attributes and "DW_AT_specification" in d.attributes:
                    d = d.get_DIE_from_attribute("DW_AT_specification")
                if "DW_AT_type" not in d.attributes:
                    continue
                t = d.get_DIE_from_attribute("DW_AT_type")
                name = d.attributes.get("DW_AT_name")
                into = set()
                if stored:                      # (a flag the compiler shrank to stored bytes)
                    out.append((address, stored, name.value.decode(errors="replace") if name else "?", "scalar",
                                frozenset({(0, stored)}), None))
                    continue
                leaves(t, 0, into)
                bare, element = strip(t), None
                if bare is not None and bare.tag == "DW_TAG_array_type" and "DW_AT_type" in bare.attributes:
                    element = size_of(bare.get_DIE_from_attribute("DW_AT_type"))
                out.append((address, size_of(t) or 0, name.value.decode(errors="replace") if name else "?",
                            kind_of(t), frozenset(into), element))
    return sorted(set(out), key=lambda o: (o[0], o[1], o[2]))


def _uleb(v, i):
    value, shift = 0, 0
    while i < len(v):
        value |= (v[i] & 0x7f) << shift
        shift += 7
        i += 1
        if not v[i - 1] & 0x80:
            return value, i
    return None, i


def location(v, addr):
    """(address, stored bytes or 0) of a variable's location expression (bytes) where it is a fixed
    address: DW_OP_addr or DWARF 5's DW_OP_addrx (an index addr turns into the address), each perhaps
    with DW_OP_plus_uconst (clang's globals merged into one: an offset in it); stored bytes N where the
    value is computed from N bytes there (DW_OP_deref_size N ... DW_OP_stack_value: a flag clang
    shrank to a byte). None for any other expression (a value, a register, a list of locations)."""
    if not v:
        return None
    if v[0] == 0x03 and len(v) >= 5:                    # DW_OP_addr
        address, i = int.from_bytes(bytes(v[1:5]), "little"), 5
    elif v[0] == 0xa1:                                  # DW_OP_addrx
        index, i = _uleb(v, 1)
        if index is None:
            return None
        address = addr(index)
    else:
        return None
    while i < len(v) and v[i] == 0x23:                  # DW_OP_plus_uconst
        k, i = _uleb(v, i + 1)
        if k is None:
            return None
        address += k
    if i == len(v):
        return address, 0
    if v[i] == 0x94 and i + 1 < len(v) and v[-1] == 0x9f:   # DW_OP_deref_size N ... DW_OP_stack_value
        return address, v[i + 1]
    return None


def score(statics, elf_path, accesses=(), arrays=None):
    """The plan's structures against the source's variables (debug information): {"structures": how
    many, "one": those inside one source variable, by its kind ({kind: count}), "several": those over
    several source variables (an anchor or neighbours taken for one structure: wrong), "unknown": those
    at no variable the debug information has (a library built without it), "fields": the fields
    declared, "right": of them a leaf of the source variable they lie in, at that offset with that size,
    "rows": [(base, verdict, the variables' names)]}; with accesses [(address, type)] (what the readable
    C or the machine code reaches at fixed addresses) also "reached": the source's structures, unions and
    arrays reached there at two places or more, "grouped": of them those whose places one structure of
    the plan holds all of, "partly": those it holds some of. With arrays (find_arrays) also "arrays": how
    many, "arrays_one" ({kind: count} of those inside one source variable), "arrays_several",
    "arrays_unknown", "arrays_size": of those inside one source array, the ones whose element size is
    its element's, "array_fields" (an element's fields in every element the plan counts) and
    "array_right" (of them a leaf of the variable at that place with that size); their rows carry the
    verdict "array: KIND"; and an array holds a reached place where an element's field lies there."""
    objects = source_objects(elf_path)
    starts = [o[0] for o in objects]

    def containing(a):
        """The variable that holds address a: of those that start nearest below it (variables do not
        nest), the smallest that reaches it; None for none."""
        i = bisect.bisect_right(starts, a) - 1
        if i < 0:
            return None
        group = [o for o in objects[bisect.bisect_left(starts, objects[i][0]):i + 1] if a < o[0] + max(o[1], 1)]
        return min(group, key=lambda o: (o[1], o[2])) if group else None
    out = {"structures": 0, "one": {}, "several": 0, "unknown": 0, "fields": 0, "right": 0, "rows": [],
           "reached": 0, "grouped": 0, "partly": 0, "arrays": 0, "arrays_one": {}, "arrays_several": 0,
           "arrays_unknown": 0, "arrays_size": 0, "array_fields": 0, "array_right": 0}
    for base, (k, n, rows) in sorted((arrays or {}).items()):
        out["arrays"] += 1
        places = [(base + j * k + off, SIZES[t]) for j in range(min(n, 1024)) for off, t in rows]
        inside = {containing(a) for a, _ in places} - {None}
        out["array_fields"] += len(places)
        for a, z in places:
            o = containing(a)
            if o is not None and (a - o[0], z) in o[4]:
                out["array_right"] += 1
        if not inside:
            out["arrays_unknown"] += 1
            out["rows"].append((base, "array: unknown", []))
            continue
        o = next(iter(inside))
        if len(inside) == 1 and o[0] <= base and base + k * n <= o[0] + o[1]:
            out["arrays_one"][o[3]] = out["arrays_one"].get(o[3], 0) + 1
            if o[5] == k and (base - o[0]) % k == 0:
                out["arrays_size"] += 1
            out["rows"].append((base, "array: " + o[3], [o[2]]))
        else:
            out["arrays_several"] += 1
            out["rows"].append((base, "array: several", sorted(x[2] for x in inside)))
    for base, rows in sorted(statics.items()):
        end = max(off + SIZES[t] for off, t in rows)
        inside = {containing(base + off) for off, _ in rows} - {None}
        out["structures"] += 1
        out["fields"] += len(rows)
        if not inside:
            out["unknown"] += 1
            out["rows"].append((base, "unknown", []))
            continue
        for off, t in rows:
            o = containing(base + off)
            if o is not None and (base + off - o[0], SIZES[t]) in o[4]:
                out["right"] += 1
        if len(inside) == 1 and next(iter(inside))[0] <= base and base + end <= next(iter(inside))[0] + \
                next(iter(inside))[1]:
            o = next(iter(inside))
            out["one"][o[3]] = out["one"].get(o[3], 0) + 1
            out["rows"].append((base, o[3], [o[2]]))
        else:
            out["several"] += 1
            out["rows"].append((base, "several", sorted(o[2] for o in inside)))
    if accesses:
        reached = {}
        for a, t in accesses:
            o = containing(a)
            if o is not None and o[3] != "scalar":
                reached.setdefault(o, set()).add(a)
        order = sorted(statics)
        spans = sorted((b, b + k * n, k, rows) for b, (k, n, rows) in (arrays or {}).items())
        grouped = partly = 0
        multi = {o: places for o, places in reached.items() if len(places) >= 2}
        for o, places in multi.items():
            held = set()
            for a in places:
                i = bisect.bisect_right(order, a) - 1
                if i >= 0:
                    b = order[i]
                    if any(b + off == a for off, _ in statics[b]):
                        held.add((b, a))
                j = bisect.bisect_right(spans, (a, MASK + 1)) - 1
                if j >= 0 and spans[j][0] <= a < spans[j][1] and \
                        any((a - spans[j][0]) % spans[j][2] == off for off, _ in spans[j][3]):
                    held.add((spans[j][0], a))
            bases = {b for b, _ in held}
            if len(held) == len(places) and len(bases) == 1:
                grouped += 1
            elif held:
                partly += 1
        out.update({"reached": len(multi), "grouped": grouped, "partly": partly})
    return out
