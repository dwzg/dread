"""Structures of static data: memory at fixed addresses grouped into the structures it was declared as.

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
for stays the macro it was.

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
file from one base, clang merges a file's globals into one object on RISC-V (and newlib's malloc
keeps its state so on ARM), and an older compiler may reach a file's data from its section's start.
Within a function, an anchor's accesses at constant offsets look exactly like a structure's. What
tells them apart is what the code does next to the base (separate_bases): a compiler that makes a
base per object reaches the neighbours a function uses together through bases of their own (or
through RISC-V's gp), while an anchor is the one base its file reaches all of its variables through,
so the functions that use it reach nothing next to it apart from it. A base counts only where one of
its functions reaches a place within NEAR bytes in front of it or behind its structure apart from it
(trust="all" counts every base, for measuring what this keeps out). The data symbols only end
structures: a symbol at a base would make it count, but one that a person gave the first variable of
an anchor's run would make the whole run one.

Measured against debug information (score(), dread readable --statics --score ELF) on the 39 FreeRTOS
builds of the benchmark and two built without -fdata-sections (2026-10-23): no structure lies over
several source variables on any of them; each lies inside one (but two of the Cortex-M4F GCC build,
where no variable has debug information), and each field that can be checked is a leaf of its
variable at its offset with its size. FreeRTOS GCC -Os has 15 (11 arrays of scalars, an array of
structures, 3 structures: xStart, xSuspendedTaskList, xPendingReadyList), clang -O2 13, GCC -O0 5,
RISC-V GCC none (its scalars are small data near gp, far from the lists, and the lists' functions
reach no neighbour apart from their bases). Counting every base instead: 23 and 21 structures on GCC
-Os and -O2 without -fdata-sections, 20 on RISC-V clang -Os, one (newlib's malloc state) on thirteen
ARM builds, each over several source variables, and 385 inside one variable on the 39 builds where
the evidence keeps 329. What the code alone cannot tell stays as it was: gizmo is built without
-fdata-sections, and its g_config is no structure.

Not established: whether a structure found is the source's (an array of scalars is one too, and an
equally spaced structure reads as one); where one source structure is reached from several bases
(GCC's .word sym+8), it comes out as pieces; a structure whose functions use nothing next to it is
missed, however the compiler addressed it; MSP430 and MIPS have no value_step yet and get none.
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


NEAR = 64               # bytes around a structure where its neighbours lie (separate_bases)


def plan(program, evidence_, extra=None, symbols=True, near=NEAR, trust="evidence"):
    """{base: [(offset, type)]} of the structures of static data the evidence ({name: Evidence},
    gather) shows (see the module's docstring). extra: more accesses at fixed addresses {function:
    [(address, type)]} for the fields (the readable C's: an ldm's or stm's words, which the evidence
    knows only by their first, a conditional literal's); symbols: whether the target's data symbols end
    a structure (they never make one: a symbol a person gave the first variable of an anchor's run would
    make the run one); trust: "evidence" (a base counts only where the code reaches its neighbours
    apart from it: separate_bases), "all" (every base: for measuring what the evidence keeps out)."""
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
                starts.add(address)         # (an array starts at its constant part)
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
    data_symbols = {a for kind, a, _ in program.symbols() if kind == "d"} if symbols else set()
    starts |= data_symbols
    for c in program.image.startup():       # (an object lies in one of the startup's ranges)
        starts |= {c.run, c.run + c.size}
    order = sorted(starts)
    places = sorted(accesses)
    out = {}
    for base in sorted(reach):
        if base in below:
            continue
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
    return out


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


def describe(statics):
    """Lines saying what the plan holds: per structure its base, its size and its fields."""
    lines = []
    for base, rows in sorted(statics.items()):
        end = max(off + SIZES[t] for off, t in rows)
        lines.append("0x%08x %4d bytes: %s" % (base, end, ", ".join("%x:%s" % r for r in rows)))
    return lines


# ---- against the source (debug information)

def source_objects(elf_path):
    """[(address, size, name, kind, leaves)] of the variables at fixed addresses in an ELF's debug
    information (DW_OP_addr, DWARF 5's DW_OP_addrx), sorted: kind "struct", "union", "array of
    struct", "array of scalar" or "scalar"; leaves {(offset, size)} of what it holds (a structure's
    members' scalars at their offsets, an array's elements', at most 1024 of them). [] without
    pyelftools or debug information."""
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
                                frozenset({(0, stored)})))
                    continue
                leaves(t, 0, into)
                out.append((address, size_of(t) or 0, name.value.decode(errors="replace") if name else "?",
                            kind_of(t), frozenset(into)))
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


def score(statics, elf_path, accesses=()):
    """The plan's structures against the source's variables (debug information): {"structures": how
    many, "one": those inside one source variable, by its kind ({kind: count}), "several": those over
    several source variables (an anchor or neighbours taken for one structure: wrong), "unknown": those
    at no variable the debug information has (a library built without it), "fields": the fields
    declared, "right": of them a leaf of the source variable they lie in, at that offset with that size,
    "rows": [(base, verdict, the variables' names)]}; with accesses [(address, type)] (what the readable
    C or the machine code reaches at fixed addresses) also "reached": the source's structures, unions and
    arrays reached there at two places or more, "grouped": of them those whose places one structure of
    the plan holds all of, "partly": those it holds some of."""
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
           "reached": 0, "grouped": 0, "partly": 0}
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
            bases = {b for b, _ in held}
            if len(held) == len(places) and len(bases) == 1:
                grouped += 1
            elif held:
                partly += 1
        out.update({"reached": len(multi), "grouped": grouped, "partly": partly})
    return out
