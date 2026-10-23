"""dread groups: an image's functions in groups found from their code alone, the subsystems a reader
would see (a kernel's queue, its timers, a driver, a demo's test, the C library's stdio). dread pilot
--task subsystems asks a language model for each group's name and prefix, and --task name
--subsystems shows them in the questions for the functions' names.

No name is used, so a stripped image groups as its named build does. What each function touches is
read from its machine code (the default, EVIDENCE "listing", since 2026-10-22: dread.relations
flow_references follows the constants its registers hold over its control flow, and finds its loads
and stores at a known address, the addresses it makes, and those it stores, passes or returns), or
with --evidence readable from its readable C as dread relations reads it (Facts: the accesses of its
readable C at fixed addresses and the constants it makes that point to a text, a table or a function;
the addresses its machine code makes where no C is made; kept in BUILD/relations). Both say which
globals, devices, texts, tables and functions' entries it touches (things()); the listing costs a
few milliseconds a function, the readable C 0.1-0.5 s. The graph has an edge between two functions for

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
the index from the readable C was 0.78-0.84, purity 0.74-0.85, completeness 0.89-0.98, 27-29 groups
for 41-53 files: what lowers purity is mostly small neighbouring files of the demo in one group, and
two files whose functions of the same names the linker put side by side. From the listing (the same
day, 38 FreeRTOS builds, docs/decisions.md): on the 27 ARM builds the index 0.004 higher on average
(20 builds higher, 5 lower), the two groupings' index against each other 0.98; on RISC-V as high
with clang (-0.01 to +0.05), 0.06-0.11 lower with GCC -Os (0.015 with -O2), where the listing sees
one array's field in 11-13 of the kernel's functions that the readable C keeps behind a variable,
and resolution 2 splits that file around it (given to the readable C's evidence alone, it lowers
that score as much). Not established: how the groups fall on firmware whose source files are not
laid out one after the other (link-time optimisation, functions sorted by section names), and
whether a file is the subsystem a reader means (a C library's many one-function files make one
group, which is what a reader wants and what the score counts as impure).

The groups and the evidence they were found from are kept in BUILD/groups/IMAGE.json (load(), since
2026-10-22) under a key made without reading a function: every module of DREAD (what the evidence
depends on is most of the package, so any change makes them anew), regflow's key (the image's bytes,
its functions, code ranges and startup), the target's description and the image's tables, the
resolution, the weights and where the evidence comes from. A second look costs the key (milliseconds)
instead of finding them: from the listing 0.3-0.5 s for a FreeRTOS build on 8 workers, the whole
command; from the readable C 7-23 s with no facts kept and 1-5 s with them (each function's lift);
--fresh finds them anew.

--json prints the whole program as a graph of its groups (program_graph(), docs/formats.md, "The
whole-program graph"): each group with a label (the pilot's subsystem name for exactly these
functions, else the name prefix a third of its functions share where no other group's is the same,
the name of a group's only function, or "group N") and the naming status of its functions, how sure
their names are (dread.relations certainty) and the marks the name review left on them, and what the
review said of the group's own name; between two groups the calls each way, the addresses of
functions taken each way, and the things both touch (as the grouping counts them: none that more than
MAX_SHARED functions touch); every function with its group, how sure its name is and the review's
word on it, the calls and taken addresses between functions, and each thing functions of two groups
touch with those functions. dread gui draws it (/api/program).

    dread groups [--target DIR] [--image NAME] [--resolution R] [--jobs N] [--score ELF] [--brief]
                 [--json] [--fresh] [--evidence listing|readable]
"""
import argparse
import collections
import json
import os
import re
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
KEEP_VERSION = 2        # what load() keeps and how its key is made: bump when either changes (2: the evidence)
EVIDENCE = "listing"    # where what a function touches is read from: "listing" or "readable" (evidence())
SOURCES = ("listing", "readable")
PREFIX_SHARE = 1 / 3    # a group is labelled by a name prefix that at least this share of its functions has


def _facts_one(name):
    from dread.readable import _G
    return _G["facts"].get(name)["refs"]


def _flow_one(name):
    from dread import relations
    from dread.readable import _G
    program = _G["program"]
    refs = relations.flow_references(program, program.by_name[name], scratch=_G["scratch"])
    return [[a, t, i, r, w] for (a, t, i), (r, w) in refs.items()]


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


def evidence(target, image, program, flow=None, jobs=None, source=EVIDENCE):
    """{function name: things()} of every function of the program, in a pool of workers. source
    "listing": dread.relations flow_references, from the machine code alone (0.1-0.5 s on 8 workers for
    a FreeRTOS build); "readable": dread.relations Facts, from each function's readable C (made where it
    is not kept yet: 6-32 s on 8 workers for a FreeRTOS build, 1-5 s once kept, each function lifted)."""
    from dread import readable, regflow, relations
    if source not in SOURCES:
        raise ValueError("evidence from %r: one of %s" % (source, ", ".join(SOURCES)))
    names = [fn.name for fn in sorted(program.functions, key=lambda f: f.entry)]
    if source == "readable":
        facts = relations.Facts(target, image, program, flow)
        facts.prepare()
        got = readable.in_pool(_facts_one, names, jobs, facts=facts)
    else:
        got = readable.in_pool(_flow_one, names, jobs, program=program,
                               scratch=regflow.scratch_names(target, program.arch))
    return {n: things(program, program.by_name[n], refs) for n, refs in zip(names, got)}


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


def find(target, image, program, flow=None, jobs=None, touched=None, resolution=RESOLUTION, source=EVIDENCE,
         **weights):
    """The image's groups: [[function names in address order]], ordered by their first function's
    address (a group's number is its place here). touched: evidence() (from source), made here when
    not given; weights: graph()'s keyword arguments."""
    if touched is None:
        touched = evidence(target, image, program, flow, jobs, source)
    order, edges = graph(program, touched, **weights)
    found = {}
    for name, c in zip(order, louvain(len(order), edges, resolution)):
        found.setdefault(c, []).append(name)
    return sorted(found.values(), key=lambda g: program.by_name[g[0]].entry)


def membership(groups):
    """{function name: its group's number} of find()'s groups."""
    return {n: k for k, g in enumerate(groups) for n in g}


# ---- kept: the groups of an image, found once ----

def kept_path(target, image):
    return os.path.join(target.build, "groups", image.name + ".json")


def cache_key(target, image, resolution=RESOLUTION, weights=None, source=EVIDENCE):
    """A digest of what the groups are made from, made without reading a function (the module
    docstring): every Python module of DREAD, regflow's key, the target's description, each table of
    the image (absent ones too), the resolution, graph()'s weights and where the evidence is read from.
    Conservative: an edit anywhere in DREAD, or a note in annotations.txt, makes the groups anew."""
    import hashlib
    from dread import regflow
    h = hashlib.sha1(b"dread groups %d\0" % KEEP_VERSION)
    package = os.path.dirname(os.path.abspath(__file__))
    for root, dirs, files in os.walk(package):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "__")))
        for name in sorted(files):
            if name.endswith(".py"):
                path = os.path.join(root, name)
                with open(path, "rb") as f:
                    h.update(os.path.relpath(path, package).encode() + b"\0" + f.read() + b"\0")
    h.update(regflow.cache_key(target, image).encode())
    for key, path in [("dread.toml", target.file)] + sorted((k, image.table(k)) for k in image.tables):
        h.update(key.encode() + b"\0")
        try:
            with open(path, "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"(none)")
        h.update(b"\0")
    h.update(json.dumps([float(resolution), weights or {}, source], sort_keys=True).encode())
    return h.hexdigest()


def kept(target, image, key=None, resolution=RESOLUTION, weights=None, source=EVIDENCE):
    """The record load() kept for the image when its key is key (cache_key() of the resolution, the
    weights and the evidence's source when not given), else None: {"groups": find()'s groups,
    "touched": evidence(), "key", "resolution", "evidence"}."""
    try:
        with open(kept_path(target, image)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if key is None:
        key = cache_key(target, image, resolution, weights, source)
    if not isinstance(data, dict) or data.get("key") != key or data.get("version") != KEEP_VERSION:
        return None
    found, touched = data.get("groups"), data.get("touched")
    if not isinstance(found, list) or not isinstance(touched, dict):
        return None
    return {"groups": [list(g) for g in found], "key": key, "resolution": data.get("resolution"),
            "evidence": data.get("evidence"),
            "touched": {n: [tuple(t) for t in things] for n, things in touched.items()}}


def load(target, image, program, flow=None, jobs=None, resolution=RESOLUTION, fresh=False, source=EVIDENCE,
         **weights):
    """kept()'s record, or the groups found now (find(), with the evidence from source) and kept for
    the next time (BUILD/groups/IMAGE.json, docs/formats.md); fresh: found now whatever is kept."""
    key = cache_key(target, image, resolution, weights, source)
    if not fresh:
        got = kept(target, image, key)
        if got is not None:
            return got
    touched = evidence(target, image, program, flow, jobs, source)
    found = find(target, image, program, flow, jobs, touched=touched, resolution=resolution, **weights)
    path = kept_path(target, image)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp = "%s.%d.tmp" % (path, os.getpid())
        with open(temp, "w") as f:
            json.dump({"version": KEEP_VERSION, "key": key, "resolution": resolution, "evidence": source,
                       "groups": found, "touched": {n: [list(t) for t in touched[n]] for n in sorted(touched)}},
                      f, sort_keys=True)
        os.replace(temp, path)
    except OSError:                         # (a build directory that cannot be written: found each time)
        pass
    return {"groups": found, "touched": touched, "key": key, "resolution": resolution, "evidence": source}


# ---- labels ----

_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def prefix_of(name):
    """The first word of a function's name, by which a reader groups names: up to its first underscore,
    with it (uart_putc: uart_, SysTick_Handler: SysTick_); in a name without one, its first capitalised
    word after a lower-case mark of one or two letters, Hungarian notation's type (xQueueSend: Queue,
    vTaskDelay: Task, prvIdleTask: prv; numbers are no words). None for a name a tool gave (FUN_...,
    dread.samecode UNNAMED) and for a name of one word (main, memcpy, f0)."""
    from dread.samecode import UNNAMED
    if UNNAMED.match(name):
        return None
    core = name.strip("_")
    if "_" in core:
        return core.split("_", 1)[0] + "_"
    words = [w for w in _WORDS.findall(core) if not w.isdigit()]
    if len(words) > 2 and words[0].islower() and len(words[0]) <= 2:
        words = words[1:]
    return words[0] if len(words) > 1 else None


def common_prefix(names, share=PREFIX_SHARE):
    """(prefix, how many names have it) of the prefix (prefix_of) most of names have, when at least two
    and at least share of them have it (ties: the longer, then the first in the alphabet); else None."""
    counts = collections.Counter(p for p in map(prefix_of, names) if p)
    if not counts:
        return None
    prefix, n = min(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    return (prefix, n) if n >= 2 and n >= share * len(names) else None


# ---- the whole program as a graph of its groups ----

def program_graph(program, found, touched, calls=None, names=None, subsystems=None, extra=None,
                  max_shared=MAX_SHARED):
    """The whole program by groups (docs/formats.md, "The whole-program graph"): {"groups",
    "between", "functions", "calls", "refers", "shared", "counts"}. found: find()'s groups; touched:
    evidence(); calls: dread.relations call_maps(program) (made here when None); names: a
    dread.relations Names (each function's naming, how sure its name is and what the review said of
    it, and the review of a group's name); subsystems: {members key: dread.pilot subsystems.tsv row}
    (read_subsystems), a group's name where its key is there; extra: name -> more attributes of a
    function (dread gui: its origin). Functions are numbered in address order; "calls", "refers" and
    "shared" (the things functions of two groups or more touch, with the functions that do) name them
    by their number."""
    from dread import relations
    from dread.pilot import confidence, members_key
    _, down = calls if calls is not None else relations.call_maps(program)
    names = names if names is not None else relations.Names(program.target, program.image, program)
    order = sorted(program.functions, key=lambda f: f.entry)
    index = {fn.name: i for i, fn in enumerate(order)}
    by_entry = {fn.entry: i for i, fn in enumerate(order)}
    member = membership(found)
    functions, naming = [], [collections.Counter() for _ in found]
    sureness, marked = [collections.Counter() for _ in found], [collections.Counter() for _ in found]
    for fn in order:
        status, proposal = names.function(fn)
        about = names.about(fn)
        node = {"name": fn.name, "entry": fn.entry, "size": program.end(fn) - fn.entry,
                "group": member.get(fn.name), "naming": status, "certainty": about["certainty"]}
        if proposal:
            node["proposed"] = proposal[0]
        if about["confidence"] is not None:
            node["confidence"] = about["confidence"]
        if about["review"] is not None:
            node["review"] = about["review"]
        if extra is not None:
            node.update(extra(fn.name))
        functions.append(node)
        if node["group"] is not None:
            naming[node["group"]][status] += 1
            sureness[node["group"]][about["certainty"]] += 1
            if about["review"] is not None and about["review"]["mark"]:
                marked[node["group"]][about["review"]["mark"]] += 1
    edges = sorted({(index[a], index[b], len(sites)) for a, callees in down.items() if a in index
                    for b, sites in callees.items() if b in index})
    refers = sorted({(index[n], by_entry[key]) for n, things in touched.items() if n in index
                     for kind, key in things if kind == "taken" and key in by_entry and by_entry[key] != index[n]})
    pairs = {}

    def pair(a, b):
        key = (min(a, b), max(a, b))
        if key not in pairs:
            pairs[key] = {"a": key[0], "b": key[1], "calls": [0, 0], "refers": [0, 0], "shared": {}}
        return pairs[key], 0 if a < b else 1
    for kind, links in (("calls", edges), ("refers", refers)):
        for link in links:
            ga, gb = functions[link[0]]["group"], functions[link[1]]["group"]
            if ga is not None and gb is not None and ga != gb:
                rec, way = pair(ga, gb)
                rec[kind][way] += 1
    users = {}
    for n, things in touched.items():
        for kind, key in things:
            if kind in SHARED and n in member:
                users.setdefault((kind, key), set()).add(n)
    shared = []
    for (kind, key), who in sorted(users.items()):
        if len(who) > max_shared:
            continue
        gs = sorted({member[n] for n in who})
        for x in range(len(gs)):
            for y in range(x + 1, len(gs)):
                rec, _ = pair(gs[x], gs[y])
                rec["shared"][kind] = rec["shared"].get(kind, 0) + 1
        if len(gs) > 1:
            shared.append([kind, key, sorted(index[n] for n in who if n in index)])
    groups = []
    commons = [common_prefix(group) for group in found]
    labels = collections.Counter(c[0] for c in commons if c)      # (a prefix of several groups tells none apart)
    for k, group in enumerate(found):
        key = members_key(program, group)
        row = (subsystems or {}).get(key)
        common = commons[k]
        if row and row.get("name"):
            label, source = row["name"], "pilot"
        elif common and labels[common[0]] == 1:
            label, source = common[0], "prefix"
        elif len(group) == 1 and names.function(program.by_name[group[0]])[0] == "named":
            label, source = group[0], "function"
        else:
            label, source = "group %d" % k, "number"
        first, last = program.by_name[group[0]], program.by_name[group[-1]]
        groups.append({"group": k, "label": label, "from": source, "members": key,
                       "name": row["name"] if row and row.get("name") else None,
                       "prefix": (row.get("prefix") or None) if row else (common[0] if common else None),
                       "what": (row.get("what") or None) if row else None,
                       "confidence": (row.get("confidence") or None) if row else None,
                       "name_certainty": relations.certainty("proposed", confidence(row)) if source == "pilot" else None,
                       "common": {"prefix": common[0], "functions": common[1]} if common else None,
                       "functions": len(group), "bytes": sum(program.end(program.by_name[n]) - program.by_name[n].entry
                                                             for n in group),
                       "first": first.entry, "last": last.entry,
                       "naming": {s: naming[k][s] for s in ("named", "proposed", "unnamed")},
                       "certainty": {s: sureness[k][s] for s in relations.CERTAINTY},
                       "marks": dict(sorted(marked[k].items())),
                       "review": names.group_review(key, row) if row and row.get("name") else None})
    return {"groups": groups, "between": [pairs[k] for k in sorted(pairs)], "functions": functions,
            "calls": [list(e) for e in edges], "refers": [list(r) for r in refers], "shared": shared,
            "counts": {"functions": len(functions), "groups": len(groups), "calls": len(edges), "refers": len(refers),
                       "between": len(pairs), "shared": len(shared)}}


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
    parser.add_argument("--json", action="store_true",
                        help="print the whole program as a graph of its groups (docs/formats.md)")
    parser.add_argument("--fresh", action="store_true", help="find the groups anew, whatever is kept")
    parser.add_argument("--evidence", choices=SOURCES, default=EVIDENCE,
                        help="what each function touches, read from its machine code (listing) or from its "
                             "readable C (readable, slower; default %s)" % EVIDENCE)
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
    except tgt.TargetError as e:
        print("dread groups: %s" % e, file=sys.stderr)
        return 1
    from dread.program import Program
    program = Program(target, image)
    record = load(target, image, program, None, args.jobs, args.resolution, fresh=args.fresh, source=args.evidence)
    groups = record["groups"]
    if args.json:
        from dread import pilot
        subsystems = pilot.read_subsystems(pilot.subsystems_path(target, image))
        print(json.dumps(program_graph(program, groups, record["touched"], subsystems=subsystems), sort_keys=True))
        return 0
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
