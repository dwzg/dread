"""dread groups: an image's functions in groups found from their code alone, the subsystems a reader
would see (a kernel's queue, its timers, a driver, a demo's test, the C library's stdio). dread pilot
--task subsystems asks a language model for each group's name and prefix, and --task name
--subsystems shows them in the questions for the functions' names.

No name is used, so a stripped image groups as its named build does. What each function touches is
read as dread relations reads it (Facts: the accesses of its readable C at fixed addresses and the
constants it makes that point to a text, a table or a function; the addresses its machine code makes
where no C is made), kept in BUILD/relations. The graph has an edge between two functions for

- each thing both touch: a global (memory at a fixed address in RAM or rom, exactly that address), a
  device (its registers' block of DEVICE_BLOCK bytes), a text, a table of texts and functions. A thing
  k functions touch gives each pair SHARED / (k - 1), so that each of them gets SHARED from it however
  many share it; one that more than MAX_SHARED touch is left out (everyone's errno, a kernel's
  current task);
- a function's address that the other one makes (a task it creates, a callback it registers): TAKEN;
- neighbours in address order (compilers keep a source file's functions together): ADJACENT;
- calls: CALLS, 0 by default. Measured on four FreeRTOS builds against the source files (2026-10-22,
  docs/decisions.md): 7-17 % of the calls stay inside a file (a demo calls the kernel, the kernel the
  port), against 90-100 % of the shared globals and texts and 79-87 % of the neighbours; any weight
  on calls made the groups worse.

The groups are the communities of Louvain's method (modularity with resolution RESOLUTION: more than
1 gives more and smaller groups; 2 gave FreeRTOS the same index as 1 with purer groups, which a name
for a group needs more than completeness: a group of two files gets a name that fits one of them),
the functions taken in address order and a tie going to the community already held, then to the
lower one: the same image and the same evidence give the same groups. They are numbered by their
first function's address.

--score ELF compares the groups with the source file of each function (the compile unit that holds
it in the ELF's debug information, dread.cmetrics out_of_line; functions without one are left out):
the adjusted Rand index (1 the same partition, about 0 one as good as chance), purity (the share of
functions in their group's most common file: groups that mix files lower it) and completeness (the
share in their file's most common group: files split over groups lower it). On four FreeRTOS builds
(GCC -Os, clang -O2 and GCC -O0 for a Cortex-M3, GCC -Os for RISC-V; 2026-10-22, docs/decisions.md)
the index was 0.78-0.84, purity 0.74-0.85, completeness 0.89-0.98, 27-29 groups for 41-53 files: what
lowers purity is mostly small neighbouring files of the demo in one group, and two files whose
functions of the same names the linker put side by side. Not established: how the groups fall on
firmware whose source files are not laid out one after the other (link-time optimisation, functions
sorted by section names), and whether a file is the subsystem a reader means (a C library's many
one-function files make one group, which is what a reader wants and what the score counts as
impure).

    dread groups [--target DIR] [--image NAME] [--resolution R] [--jobs N] [--score ELF] [--brief]
"""
import argparse
import collections
import sys

from dread import target as tgt

DEVICE_BLOCK = 0x1000   # a device's registers: the block of this many bytes (aligned) they lie in
MAX_SHARED = 64         # a thing more functions touch says nothing of where they belong
SHARED = {"global": 1.0, "device": 1.0, "text": 1.0, "table": 1.0}
TAKEN = 1.0
ADJACENT = 0.5
CALLS = 0.0
RESOLUTION = 2.0
LEVELS = 32             # Louvain's levels at most, and its sweeps over the nodes of one level
SWEEPS = 100


def _facts_one(name):
    from dread.readable import _G
    return _G["facts"].get(name)


def things(program, fn, refs):
    """[(kind, key)] of what a function touches, sorted, from its references (dread.relations'
    facts: [[address, type, indexed, read, write], ...]): ("global", address), ("device", block),
    ("text", address), ("table", address), ("taken", entry) for another function's entry it makes."""
    from dread import relations
    memory = program.target.memory
    out = set()
    for address, ctype, _indexed, _read, _write in refs:
        if memory is not None and memory.is_io(address):
            out.add(("device", address // DEVICE_BLOCK))
            continue
        got = relations.classify(program, address, exact=ctype is not None)
        if got is None:
            continue
        kind, value = got
        if kind == "function":
            entry = program.by_name[value].entry
            if entry != fn.entry:
                out.add(("taken", entry))
        elif kind in ("text", "table"):
            out.add((kind, address))
        elif kind == "memory":
            out.add(("global", address))
    return sorted(out)


def evidence(target, image, program, flow=None, jobs=None):
    """{function name: things()} of every function of the program, from dread.relations Facts (made in
    a pool of workers where they are not kept yet: 7-30 s on 8 workers for a FreeRTOS build, about a
    second once kept)."""
    from dread import readable, relations
    facts = relations.Facts(target, image, program, flow)
    facts.prepare()
    names = [fn.name for fn in sorted(program.functions, key=lambda f: f.entry)]
    got = readable.in_pool(_facts_one, names, jobs, facts=facts)
    return {n: things(program, program.by_name[n], f["refs"]) for n, f in zip(names, got)}


def graph(program, touched, calls=None, shared=None, taken=TAKEN, adjacent=ADJACENT, call_weight=CALLS,
          max_shared=MAX_SHARED):
    """(names in address order, {(i, j): weight} with i < j): the graph of the module docstring.
    touched: evidence(); calls: {callee: [(caller, address)]} (dread.flat callers_of; made here when
    call_weight is not 0); shared: SHARED's weights by kind."""
    shared = dict(SHARED if shared is None else shared)
    order = [fn.name for fn in sorted(program.functions, key=lambda f: f.entry)]
    index = {n: i for i, n in enumerate(order)}
    edges = {}

    def add(i, j, w):
        if i != j and w:
            key = (i, j) if i < j else (j, i)
            edges[key] = edges.get(key, 0.0) + w
    if call_weight:
        if calls is None:
            from dread.flat import callers_of
            calls = callers_of(program)
        pairs = {(index[caller], index[callee]) for callee, sites in calls.items() if callee in index
                 for caller, _ in sites if caller in index}
        for i, j in sorted(pairs):
            add(i, j, call_weight)
    users = {}
    by_entry = {program.by_name[n].entry: index[n] for n in order}
    for n in order:
        for kind, key in touched.get(n, ()):
            if kind == "taken":
                if key in by_entry:
                    add(index[n], by_entry[key], taken)
            elif shared.get(kind):
                users.setdefault((kind, key), []).append(index[n])
    for (kind, _), members in sorted(users.items()):
        k = len(members)
        if 2 <= k <= max_shared:
            w = shared[kind] / (k - 1)
            for x in range(k):
                for y in range(x + 1, k):
                    add(members[x], members[y], w)
    for i in range(len(order) - 1):
        add(i, i + 1, adjacent)
    return order, edges


def louvain(n, edges, resolution=RESOLUTION):
    """[community of each node] of a weighted graph of n nodes ({(i, j): weight}) by Louvain's method:
    each node in turn moves to the neighbouring community that raises the modularity most (a gain over
    staying, ties to the lower community), sweeps until none moves, then each community becomes a
    node of the next level; ends when a level moves nothing (at most LEVELS levels of SWEEPS sweeps).
    Communities are numbered by their first node."""
    adj = [{} for _ in range(n)]
    for (i, j), w in edges.items():
        adj[i][j] = adj[i].get(j, 0.0) + w
        adj[j][i] = adj[j].get(i, 0.0) + w
    member = list(range(n))             # node -> its node of the current level
    inner = [0.0] * n                   # a node's weight inside it (a community's own edges)
    nodes = n
    for _ in range(LEVELS):
        k = [sum(adj[i].values()) + 2 * inner[i] for i in range(nodes)]
        m2 = sum(k)
        if not m2:
            break
        comm, tot, moved = list(range(nodes)), k[:], False
        for _ in range(SWEEPS):
            changed = False
            for i in range(nodes):
                here = comm[i]
                weights = {}
                for j, w in adj[i].items():
                    weights[comm[j]] = weights.get(comm[j], 0.0) + w
                tot[here] -= k[i]
                best, gain = here, weights.get(here, 0.0) - resolution * tot[here] * k[i] / m2
                for c in sorted(weights):
                    g = weights[c] - resolution * tot[c] * k[i] / m2
                    if g > gain + 1e-12:
                        best, gain = c, g
                tot[best] += k[i]
                if best != here:
                    comm[i], changed, moved = best, True, True
            if not changed:
                break
        if not moved:
            break
        renumber = {}
        for c in comm:
            renumber.setdefault(c, len(renumber))
        comm = [renumber[c] for c in comm]
        nadj, ninner = [{} for _ in renumber], [0.0] * len(renumber)
        for i in range(nodes):
            ci = comm[i]
            ninner[ci] += inner[i]
            for j, w in adj[i].items():
                cj = comm[j]
                if ci == cj:
                    ninner[ci] += w / 2.0   # (each inner edge is seen from both ends)
                else:
                    nadj[ci][cj] = nadj[ci].get(cj, 0.0) + w
        member = [comm[m] for m in member]
        adj, inner, nodes = nadj, ninner, len(renumber)
    renumber = {}
    for m in member:
        renumber.setdefault(m, len(renumber))
    return [renumber[m] for m in member]


def find(target, image, program, flow=None, jobs=None, touched=None, resolution=RESOLUTION, **weights):
    """The image's groups: [[function names in address order]], ordered by their first function's
    address (a group's number is its place here). touched: evidence(), made here when not given;
    weights: graph()'s keyword arguments."""
    if touched is None:
        touched = evidence(target, image, program, flow, jobs)
    order, edges = graph(program, touched, **weights)
    found = {}
    for name, c in zip(order, louvain(len(order), edges, resolution)):
        found.setdefault(c, []).append(name)
    return sorted(found.values(), key=lambda g: program.by_name[g[0]].entry)


def membership(groups):
    """{function name: its group's number} of find()'s groups."""
    return {n: k for k, g in enumerate(groups) for n in g}


def source_files(elf_path):
    """{entry address: source file path} of the functions an ELF's debug information places in a
    compile unit (dread.cmetrics out_of_line); {} without debug information."""
    from dread import cmetrics
    return {a: e["cu"] for a, e in cmetrics.out_of_line(elf_path).items() if e.get("cu")}


def _pairs(x):
    return x * (x - 1) / 2


def score(labels, truth):
    """How a grouping ({item: group}) matches a true one ({item: label}) over the items both have:
    {"functions", "groups", "files", "ari": the adjusted Rand index, "purity", "completeness"}
    (the module docstring)."""
    items = sorted(set(labels) & set(truth))
    n = len(items)
    both = collections.Counter((labels[i], truth[i]) for i in items)
    mine = collections.Counter(labels[i] for i in items)
    true = collections.Counter(truth[i] for i in items)
    out = {"functions": n, "groups": len(mine), "files": len(true), "ari": 0.0, "purity": 0.0, "completeness": 0.0}
    if not n:
        return out
    together = sum(_pairs(v) for v in both.values())
    a, b = sum(_pairs(v) for v in mine.values()), sum(_pairs(v) for v in true.values())
    expected = a * b / _pairs(n) if n > 1 else 0.0
    most = (a + b) / 2
    out["ari"] = (together - expected) / (most - expected) if most != expected else 1.0
    best_file, best_group = collections.Counter(), collections.Counter()
    for (g, t), v in both.items():
        best_file[g] = max(best_file[g], v)
        best_group[t] = max(best_group[t], v)
    out["purity"] = sum(best_file.values()) / n
    out["completeness"] = sum(best_group.values()) / n
    return out


def score_groups(program, groups, elf_path):
    """(score() of the groups against the source files of an ELF's debug information, {group number:
    [(file, functions)] most first}) by entry address; the files by their path's last part when
    that is unique among them."""
    import os
    files = source_files(elf_path)
    short = {f: os.path.basename(f) for f in set(files.values())}
    if len(set(short.values())) < len(short):
        short = {f: f for f in short}
    member = membership(groups)
    labels = {program.by_name[n].entry: k for n, k in member.items()}
    truth = {a: short[f] for a, f in files.items()}
    per = {}
    for k, g in enumerate(groups):
        c = collections.Counter(truth[program.by_name[n].entry] for n in g if program.by_name[n].entry in truth)
        per[k] = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
    return score(labels, truth), per


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread groups", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--resolution", type=float, default=RESOLUTION,
                        help="Louvain's resolution: more gives more and smaller groups (default %g)" % RESOLUTION)
    parser.add_argument("--jobs", type=int, help="workers that read the functions (default: the polite share)")
    parser.add_argument("--score", metavar="ELF",
                        help="compare the groups with the source files of the ELF's debug information")
    parser.add_argument("--brief", action="store_true", help="print the totals only")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
    except tgt.TargetError as e:
        print("dread groups: %s" % e, file=sys.stderr)
        return 1
    from dread import regflow
    from dread.program import Program
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    groups = find(target, image, program, flow, args.jobs, resolution=args.resolution)
    try:
        totals, per = score_groups(program, groups, args.score) if args.score else (None, {})
    except ImportError:
        print("dread groups --score: reading debug information needs pyelftools (pip install -e '.[dwarf]')",
              file=sys.stderr)
        return 1
    if not args.brief:
        for k, g in enumerate(groups):
            first, last = program.by_name[g[0]], program.by_name[g[-1]]
            files = "; files: %s" % ", ".join("%s %d" % kv for kv in per[k]) if per.get(k) else ""
            print("group %d: %d functions, 0x%x-0x%x: %s%s" % (k, len(g), first.entry, last.entry, " ".join(g), files))
    print("dread groups: %d functions in %d groups (resolution %g)" % (len(program.functions), len(groups),
                                                                          args.resolution))
    if totals is not None:
        if not totals["functions"]:
            print("dread groups --score: no function of the image has a source file in %s" % args.score)
            return 1
        print("dread groups --score: %d functions with a source file, %d files in %d groups: adjusted Rand index "
              "%.3f, purity %.3f, completeness %.3f" % (totals["functions"], totals["files"], totals["groups"],
                                                        totals["ari"], totals["purity"], totals["completeness"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
