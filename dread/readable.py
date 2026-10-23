"""dread readable: lifted C made readable by transformations in code, each result checked as lifted C is.

dread lift writes C that is exact and unreadable: every register a variable, every branch a goto,
every load and store volatile. This turns it into C a person can read, one transformation after
another on the tree of dread.cparse (STAGES), without a language model. None of it has to be right
by construction: `--check` builds every function of the lifted table from its readable C, runs the
cases the lift check accepted it with, and keeps a function's readable C only when they agree and
run every instruction of the original, as for lifted C, and when the whole image's scenarios agree;
a function that fails is built from its lifted C again. The functions accepted go into the image's
readable table (config/readable.txt), and dread recon builds them from readable C. Names and types
are left to the pilot (dread pilot).

Stages so far: the clean-up (dread.tidy: what only the machine needed out, compares and their
branches as comparisons), then the structure (dread.structure: gotos as if/else and loops). After
them the tables (_named): memory at fixed addresses as names, the fields of structures of static
data as members of one object (dread.naming apply_globals with the readable_types plan's statics,
dread.statics: g_20000030.f_4), the structures behind pointers (dread.shapes), the variables' names;
then the part of the frame that addresses handed out reach becomes one local object (dread.frames: a
structure, stack36.f_4, (u32)&stack36.f_4, with a union where accesses overlap; an array of bytes
where none is at a constant offset, (u32)stack40).

A build from readable C (dread.build with a chooser: the check's rounds and its whole image, dread
recon on an image with a readable table, dread pilot's builds) makes the readable C of every chosen
function at once, before it writes the first file (readable_texts): in a pool of worker processes,
and kept in this process by the context's tables (context_key), the stages and each function's
lifted C, so that the check's next round, its whole image and the next build with the same tables
take it as it was. The check's first pass makes them so too. Until 2026-10-23 each was made in the
building process, one after another (48 s of a 55 s build of FreeRTOS's GCC -Os demo; 97 % of that
the clean-up); a build in a process with another thread running still does so (in_pool).

Where the image with lifted C alone already differs from the original in the scenarios (the system
run's clock on some builds until 2026-10-22; a lifted table the lift check wrote since agrees), the
scenarios decide nothing and the function-level runs decide alone; elsewhere a difference with
readable C sends the guilty functions back to lifted C, found by halving (dread.lift.culprits).

`--figures FILE` measures the readable table's functions (dread.cmetrics: statements, nesting,
gotos, casts, raw accesses, members, typed variables) in their readable C as built, and their lifted
C beside it, into a JSON file: what dread bench's readable step compares with the source.

Not yet: functions that never return and were accepted by the system run (they stay lifted).
"""
import argparse
import os
import re
import sys
import weakref

from dread import cparse, naming, structure, tidy
from dread import target as tgt

STAGES = [("tidy", tidy.tidy), ("structure", structure.structure)]     # (name, function(tree)): in order


class Context:
    """What the stages may know beyond the lifted file: the target's argument registers (in order) and
    the registers each function reads at its entry (dread.regflow). Empty without a target."""

    def __init__(self, target=None, image=None, program=None, flow=None):
        self.arg_regs, self.reads, self.preserved, self.variadic = [], {}, {}, set()
        self.unread = set()             # functions whose result no caller reads (dread.shapes analyse)
        self.names = {}                 # the image's readable_names table (dread.naming): functions
        self.globals = {}               # and memory at fixed addresses
        self.memory = None              # the target's [memory] (default names for fixed addresses)
        from dread import shapes
        self.plan = shapes.Plan()       # the image's readable_types table (dread.shapes)
        self.structs = {}               # the readable_names table's structs part
        self._struct_names = None
        self._static_names = None
        if target is None:
            return
        self.plan = shapes.load_plan(image)
        self.structs = naming.load(image, "structs")
        self.names = naming.load(image)
        self.globals = naming.load(image, "globals")
        self.memory = target.memory
        from dread import regflow
        from dread.program import Program
        program = program or Program(target, image)
        flow = flow or regflow.load(target, image, program)
        self.arg_regs = list(target.abi.arg_regs)
        self.reads = {fn.name: set(flow.read_regs(fn.name)) for fn in program.functions}
        self.preserved = {fn.name: set(flow.preserved_regs(fn.name)) for fn in program.functions}
        result = target.abi.result[0] if target.abi.result else None
        self.unread = {fn.name for fn in program.functions if result not in flow.kept_regs(fn.name)}
        self.variadic = variadic_reach(program)

    def struct_names(self):
        """{s_N: (name, {offset: field name})} for the plan's structures the table names (computed
        once; set _struct_names to None after the table or the plan changes)."""
        if self._struct_names is None:
            taken = {e.get("name") for e in self.globals.values() if isinstance(e, dict)}
            self._struct_names = naming.struct_names(self.plan, self.structs, taken)
        return self._struct_names

    def static_names(self):
        """dread.naming static_names of the plan's structures of static data (computed once; set
        _static_names to None after the table or the plan changes)."""
        if self._static_names is None:
            shaped = set(self.plan.structs) | {n for n, _ in self.struct_names().values()}
            self._static_names = naming.static_names(self.plan.statics, self.globals, self.memory, avoid=shaped,
                                                     arrays=self.plan.arrays)
        return self._static_names

    def unknown(self, name):
        """Whether what a function reads cannot be told from regflow: it is variadic or calls one on
        (regflow counts a variadic function's unnamed argument registers in its save area as no use,
        right for the lifter, which passes them all; FreeRTOS's vAssertCalled hands its line number on
        to printf, and trimmed to its first argument it printed garbage on clang -O2)."""
        return self.variadic is None or name in self.variadic

    def arity(self, name):
        """How many argument registers a function reads at its entry, counted from the first up to the
        last it reads; None when it is not known (unknown)."""
        if name not in self.reads or self.unknown(name):
            return None
        return max((i + 1 for i, r in enumerate(self.arg_regs) if r in self.reads[name]), default=0)


def variadic_reach(program):
    """The functions that are variadic (dread.regflow's save area) or call one directly (a wrapper
    that hands its arguments on: vAssertCalled to printf); None when it cannot be worked out. Not
    further up: nearly every FreeRTOS function calls vAssertCalled, with constants of its own. A
    function that hands an argument on two levels down is not seen; the gate is there for it."""
    try:
        from dread.lift import direct_callees
        from dread.regflow import variadic_save_area
        found = set()
        for fn in program.functions:
            insns = [program.insn(a) for a in fn.insns]
            joins = {fn.entry}
            for insn in insns:
                if insn is not None:
                    kind, target, _ = program.arch.flow(insn)
                    if kind == "jump" and target is not None:
                        joins.add(target & ~1)
            if variadic_save_area(program, fn, insns, joins):
                found.add(fn.name)
        calls = {fn.name: set(direct_callees(program, fn)) for fn in program.functions}
    except Exception:
        return None
    return found | {name for name, callees in calls.items() if callees & found}


class Run:
    """One transform's state for the stages: the context, and the callees whose calls lost arguments
    nobody reads ({name: arguments left}: their extern declarations follow)."""

    def __init__(self, context):
        self.context = context or Context()
        self.trimmed = {}
        self.returns = {}           # callee -> its declared result type (the file's extern declarations)


_MEMO = {}                              # (name, text, stages) -> readable C made without a context, and
_MEMO_OF = weakref.WeakKeyDictionary()  # context -> {(name, text, stages): readable C}: a check's rounds
                                        # build the same again


def transform(name, text, stages=None, context=None):
    """The readable C of a lifted file (text) whose function is name, or None when it is not the
    lifters' dialect (it then stays lifted). The file's other functions (an MSP430 file's helpers)
    are printed as they were written. context: a Context (the calls' arguments). Kept for each context
    while it lives (weakly: its results go with it) and for the stages themselves; until 2026-10-23 by
    the ids of both, and a context made where one had died got the dead one's results, made with its
    tables (38 of 50 new contexts in a row did)."""
    key = (name, text, None if stages is None else tuple(stages))
    memo = _MEMO if context is None else _MEMO_OF.setdefault(context, {})
    if key not in memo:
        memo[key] = _transform(name, text, stages, context)
    return memo[key]


EXTERN = re.compile(r"^extern (\w+) (\w+)\(([^)]*)\);$")


def readable_tree(name, text, stages=None, context=None):
    """(tree, function) of a lifted file after the stages, before names (dread.naming): what a
    names proposal is made for. None when the text is not the lifters' dialect."""
    try:
        tree = cparse.parse(text)
    except cparse.ParseError:
        return None
    fn = tree.function(name)
    if fn is None:
        return None
    if tidy.fixed_registers(tree):
        stages = []
    run = Run(context)
    for item in tree.items:
        m = EXTERN.match(item.text) if isinstance(item, cparse.Raw) else None
        if m:
            run.returns[m.group(2)] = m.group(1)
    for _, stage in (STAGES if stages is None else stages):
        stage(fn, run)
    for item in tree.items:
        m = EXTERN.match(item.text) if isinstance(item, cparse.Raw) else None
        if m and m.group(2) in run.trimmed:
            params = [p.strip() for p in m.group(3).split(",")]
            item.text = "extern %s %s(%s);" % (m.group(1), m.group(2),
                                                ", ".join(params[:run.trimmed[m.group(2)]]) or "void")
    return tree, fn


POOL_MIN = 64       # functions from which the trees are made in a pool of worker processes
_G = {}             # what the workers share with the process that forked them (copy-on-write)


def in_pool(work, names, jobs=None, chunksize=4, **shared):
    """[work(name)] for each of names, in order, work a module's function (pickled by its name: of this
    module, or of one that reads this module's _G, as dread.groups and dread.pilot do) that reads shared
    from _G: in forked worker processes (dread.util.polite.workers(), or jobs), names handed out
    chunksize at a time in their order, or in this process for fewer than POOL_MIN names or one
    worker. On a large firmware, making the analysis trees took minutes on one core. A process with
    another thread running makes them all here, one after another: a lock that thread holds at the
    fork stays held in the child, which may then wait for it forever (checked since 2026-10-23, when
    builds began to make their readable C in a pool: a build may run where threads do)."""
    import multiprocessing
    import threading
    from dread.util.polite import die_with_parent, polite, resident, workers
    _G.clear()
    _G.update(shared)
    try:
        if len(names) < POOL_MIN or threading.active_count() > 1:
            return [work(name) for name in names]
        polite()                            # (a bulk run: the pilot's tasks had not lowered theirs)
        n = min(len(names), jobs or workers(memory_each=resident()))     # (a worker may copy all of this one)
        if n <= 1:
            return [work(name) for name in names]
        with multiprocessing.get_context("fork").Pool(n, initializer=die_with_parent) as pool:
            return pool.map(work, list(names), chunksize=chunksize)
    finally:
        _G.clear()


def lifted_text(program, lifter, name):
    """The lifted C of the function name, or None (no such function, or the lifter refuses it)."""
    from dread import lift
    fn_p = program.by_name.get(name)
    if fn_p is None:
        return None
    try:
        return lifter.lift(fn_p).text
    except lift.Refused:
        return None


def _lifted_text(name):
    return lifted_text(_G["program"], _G["lifter"], name)


def readable_one(program, context, lifter, name, named=None, statics=False):
    """(tree, function, macros, digest) of one function as readable_trees makes them (in the worker
    processes of a pool too: dread pilot global_uses), or None where it does not lift or is not the
    dialect."""
    text = lifted_text(program, lifter, name)
    made = readable_tree(name, text, context=context) if text is not None else None
    if made is None:
        return None
    tree, fn = made
    digest = naming.digest(fn)
    with_statics = statics and context is not None
    chosen = naming.apply_globals(tree, fn, named, context.memory if context is not None else None,
                                  statics=context.plan.statics if with_statics else None,
                                  names=context.static_names() if with_statics else None,
                                  arrays=context.plan.arrays if with_statics else None)
    return tree, fn, {n: (int(k.split()[0], 16), k.split()[1]) for k, n in chosen.items()}, digest


def _readable_one(name):
    return readable_one(_G["program"], _G["context"], _G["lifter"], name, _G["named"], _G.get("statics"))


def _analysis_one(name):
    import copy
    text = _lifted_text(name)
    context = _G["context"]
    early = [st for st in STAGES if st[0] != "structure"]
    made = readable_tree(name, text, stages=early, context=context) if text is not None else None
    if made is None:
        return None
    tree, fn = made
    full = copy.deepcopy(fn)
    if not tidy.fixed_registers(tree):
        structure.structure(full, Run(context))
    digest = naming.digest(full)
    chosen = naming.apply_globals(tree, fn, _G["named"], context.memory if context is not None else None)
    return tree, fn, {n: (int(k.split()[0], 16), k.split()[1]) for k, n in chosen.items()}, digest


def _view_one(name):
    text = _lifted_text(name)
    return view(name, text, context=_G["context"]) if text is not None else None


def _question_one(name):
    from dread import lift, shapes
    fn_p = _G["program"].by_name.get(name)
    if fn_p is None:
        return "no function of that name"
    try:
        text = _G["lifter"].lift(fn_p).text
    except lift.Refused as e:
        return str(e)
    context = _G["context"]
    made = readable_tree(name, text, context=context)
    if made is None:
        return "its lifted C is not the dialect"
    tree, fn = made
    digest = naming.digest(fn)
    naming.apply_globals(tree, fn, context.globals, context.memory, statics=context.plan.statics,
                         names=context.static_names(), arrays=context.plan.arrays)
    shapes.apply_types(tree, fn, context.plan, digest, names=context.struct_names())
    return tree, fn, digest, print_file(tree, fn), cparse.unparse(fn, notes=False, style="readable")


def _figures_one(name):
    from dread import cmetrics
    text = _lifted_text(name)
    made = _named(name, text, None, _G["context"]) if text is not None else None
    if made is None:
        return None
    tree, fn, stages = made
    lifted = cparse.parse(text).function(name)      # (it parsed in _named)
    return {"readable": cmetrics.readable_figures(tree, fn),
            "lifted": cmetrics.count(cmetrics.skeleton(lifted.body.body)),
            "kept_lifted": stages is not None and not stages}       # (_named: [] for fixed registers)


def figures(program, context, lifter, names, jobs=None):
    """{name: {"readable": dread.cmetrics.readable_figures of its readable C (the tables applied, as
    built), "lifted": the statements, depth, gotos and labels of its lifted C, "kept_lifted": whether
    its readable C is its lifted C (fixed registers: no stage ran)}} for the functions that lift and
    are the dialect, made in a pool (in_pool)."""
    warm(lifter)
    got = in_pool(_figures_one, list(names), jobs, program=program, context=context, lifter=lifter)
    return {n: g for n, g in zip(names, got) if g is not None}


def warm(lifter):
    """Fill the lifter's tables for the whole program here, before a pool forks: the workers share
    them instead of each making its own (dread.noreturn's: on a large firmware, much of its first
    lift)."""
    if hasattr(lifter, "never_returns"):
        lifter.never_returns("")


def _trees(work, program, context, lifter, names, named, digests, jobs, statics=False):
    out = []
    warm(lifter)
    if statics and context is not None:
        context.static_names()              # (once, before the pool forks)
    for made in in_pool(work, list(names), jobs, program=program, context=context, lifter=lifter, named=named,
                        statics=statics):
        if made is not None:
            tree, fn, macros, digest = made
            if digests is not None:
                digests[fn.name] = digest
            out.append((tree, fn, macros))
    return out


def readable_trees(program, context, lifter, names, named=None, digests=None, jobs=None, statics=False):
    """[(tree, function, macros)] of the functions' readable C after the stages, their memory at
    fixed addresses written as names (dread.naming apply_globals: named, the table's globals part, or
    the default names); macros: {name: (address, type)}, with statics (the context's plan's structures
    of static data applied) a field's name OBJECT.FIELD. Functions that do not lift or are not the
    dialect are left out. digests, a dict: filled with each function's digest before the names (what
    the readable_names and readable_types tables are made for). Made in a pool (in_pool)."""
    return _trees(_readable_one, program, context, lifter, names, named, digests, jobs, statics)


def analysis_trees(program, context, lifter, names, named=None, digests=None, jobs=None):
    """As readable_trees, but before the structure stage, for dread.shapes: every statement a node of
    the flow graph, so that it follows definitions, not names; digests: of the C after every stage,
    what the plan is made for (the structure stage moves statements, it renames nothing)."""
    return _trees(_analysis_one, program, context, lifter, names, named, digests, jobs)


def views(program, context, lifter, names, jobs=None):
    """{name: view()} of the functions that lift and are the dialect, made in a pool (in_pool)."""
    warm(lifter)
    texts = in_pool(_view_one, list(names), jobs, program=program, context=context, lifter=lifter)
    return {n: t for n, t in zip(names, texts) if t is not None}


def question_trees(program, context, lifter, names, jobs=None):
    """{name: (tree, function, digest, file, code), or why there is none (a text)}: what a question for
    the names of a function's variables needs (dread pilot --task readable), made in a pool (in_pool)
    before any question is asked. The tree is the readable C after the stages, digest what a proposal
    is made for (dread.naming digest, before the names), then the context's globals and structures
    applied (its tables as they are now); file: the whole file as it compiles without the variables'
    names (print_file), code: the function as the question shows it. context must not be None."""
    warm(lifter)
    context.struct_names()                  # (once, before the pool forks: the workers share them)
    context.static_names()
    names = list(names)
    return dict(zip(names, in_pool(_question_one, names, jobs, program=program, context=context, lifter=lifter)))


def print_file(tree, fn, stages=None):
    others = [f.name for f in tree.items if isinstance(f, cparse.Function) and f.name != fn.name]
    return cparse.unparse(tree, verbatim=others, style="readable" if (STAGES if stages is None else stages)
                          else "lifted")


def _named(name, text, stages, context=None):
    """(tree, function, stages) of the readable C with the tables applied: the globals' names, the
    structures, the variables' names (each only for the C it was made for), then the frame's region
    that addresses handed out reach as one object (dread.frames); None when the lifted C is not the
    dialect. stages: [] when the function keeps fixed registers (left as the lifter wrote it)."""
    made = readable_tree(name, text, stages, context)
    if made is None:
        return None
    tree, fn = made
    if tidy.fixed_registers(tree):          # (kept as the lifter wrote it: readable_tree ran no stage)
        stages = []
    if STAGES if stages is None else stages:
        made_for = naming.digest(fn)        # (a function's names are made for its C before the globals')
        entry = context.names.get(name) if context is not None else None
        mine = set((entry or {}).get("names", {}).values()) if (entry or {}).get("digest") == made_for else set()
        naming.apply_globals(tree, fn, context.globals if context is not None else None,
                             context.memory if context is not None else None, avoid=mine,
                             statics=context.plan.statics if context is not None else None,
                             names=context.static_names() if context is not None else None,
                             arrays=context.plan.arrays if context is not None else None)
        if context is not None:
            from dread import shapes
            shapes.apply_types(tree, fn, context.plan, made_for, names=context.struct_names())
        if entry:
            naming.apply(tree, fn, entry, made_for)     # (only for the C it was made for: its digest)
        from dread import frames            # (the frame that addresses handed out reach as one object:
        frames.frame_struct(tree, fn)       # last, on the names the table gave)
    return tree, fn, stages


def _transform(name, text, stages, context=None):
    made = _named(name, text, stages, context)
    return print_file(*made) if made is not None else None


def view(name, text, context=None):
    """The function as a reader sees it (the questions of dread pilot): its readable C with the tables
    applied, or its lifted C when it keeps fixed registers, after the file's other functions as the
    lifter wrote them (a variadic function's body behind its naked entry, which alone named FreeRTOS's
    printf a trampoline); None when it is not the dialect."""
    made = _named(name, text, None, context)
    if made is None:
        return None
    tree, fn, stages = made
    others = [cparse.unparse(f, notes=False, style="lifted") for f in tree.items
              if isinstance(f, cparse.Function) and f is not fn]
    return "\n\n".join(others + [cparse.unparse(fn, notes=False, style="readable" if stages is None or stages
                                                 else "lifted")])


STORE_KEEP = 4      # contexts (by their tables) whose readable C readable_texts keeps, the latest used
_STORE = {}         # (context_key, the stages, whether z3 is there) -> {(name, sha1 of its lifted C): readable C}


def context_key(context):
    """A digest of what the stages and the tables read from a context, as it is now: its tables
    (regflow's registers, the variadic functions, the readable_names and readable_types tables, the
    memory map: every attribute not starting with _), whatever object holds them. Two contexts with
    the same tables have the same key (a build's context made anew from the same tables, the next
    round's); a table changed in place changes it. "" without a context."""
    if context is None:
        return ""
    import hashlib
    import json

    def plain(x):
        if isinstance(x, (set, frozenset)):
            return sorted(repr(v) for v in x)
        if hasattr(x, "to_json"):           # (shapes.Plan)
            return x.to_json()
        if hasattr(x, "__dict__"):          # (the target's Memory, its regions)
            return [type(x).__name__, {k: v for k, v in vars(x).items() if not k.startswith("_")}]
        return repr(x)
    tables = {k: v for k, v in vars(context).items() if not k.startswith("_")}
    try:
        text = json.dumps(tables, sort_keys=True, default=plain).encode()
    except (TypeError, ValueError):         # (keys that do not sort, or no JSON key: kept apart all the same)
        import pickle
        text = pickle.dumps(sorted(tables.items()), protocol=4)
    return hashlib.sha1(text).hexdigest()


def _text_one(name):
    return _transform(name, _G["texts"][name], None, _G["context"])


def readable_texts(texts, context=None, jobs=None):
    """{name: its readable C, or None where its lifted C is not the dialect} for texts ({name: lifted
    C}): what transform makes, all at once. Those made before in this process from the same lifted C
    with a context of the same tables (context_key) and the same stages are taken as they were (a
    check's next round, its whole image, the next build of the same image: dread pilot builds again
    and again); the others are made in a pool of worker processes (in_pool, jobs workers at most;
    in this process for fewer than POOL_MIN). Until 2026-10-23 a build made every function's
    readable C in its own process, one after another: 48 s of a 55 s build of FreeRTOS's GCC -Os
    demo, 97 % of it the clean-up (dread.tidy), more than half its conditions pass. Kept for the
    STORE_KEEP contexts used last."""
    import hashlib
    from dread import cexpr
    key = (context_key(context), tuple(STAGES), cexpr.z3 is not None)
    store = _STORE.pop(key, None) or {}
    _STORE[key] = store                     # (the latest used last: dicts keep their order)
    while len(_STORE) > STORE_KEEP:
        del _STORE[next(iter(_STORE))]
    digests = {n: hashlib.sha1(t.encode()).digest() for n, t in texts.items()}
    # The longest lifted C first, one at a time: a pool that took them in the table's order, four at a
    # time, ended on a few large functions (FreeRTOS's GCC -Os demo: 7.4 s simulated for its 316 with
    # eight workers, 6.5 s so, 6.3 s ideal; the largest takes 3.9 s alone)
    todo = sorted((n for n in texts if (n, digests[n]) not in store), key=lambda n: -len(texts[n]))
    if todo:
        if context is not None:             # (once, before the pool forks: the workers share them)
            context.struct_names()
            context.static_names()
        for n, made in zip(todo, in_pool(_text_one, todo, jobs, chunksize=1, texts=texts, context=context)):
            store[(n, digests[n])] = made
    return {n: store[(n, digests[n])] for n in texts}


def chooser(names, context=None, jobs=None):
    """build.build's transform for the functions of names (the others stay lifted). dread.build
    lift_sources hands its prepare every function's lifted C before it writes the first file: the
    readable C of those of names is then made at once (readable_texts: kept from before, or in a pool
    of jobs workers at most), and the transform takes it from there."""
    names = set(names)
    made = {}                               # name -> (its lifted C, its readable C or None)

    def change(name, text):
        if name not in names:
            return None
        if name in made and made[name][0] == text:
            return made[name][1]
        return transform(name, text, context=context)

    def prepare(texts):
        chosen = {n: t for n, t in texts.items() if n in names}
        for n, out in readable_texts(chosen, context, jobs).items():
            made[n] = (chosen[n], out)
    change.prepare = prepare
    return change


def static_plan(program, context, trees, flow=None, symbols=True, trust="evidence", evidence=None):
    """(structures, arrays, shapes) of static data (dread.statics plan and find_arrays) from every
    function's machine code (evidence: dread.statics gather, made when None) and the trees' readable C
    ([(tree, fn, macros)]: analysis_trees of the lifted functions): its accesses at fixed addresses
    (more fields), at a constant plus a scaled index (the arrays), and what callees reach through the
    addresses of static data it hands them (dread.statics handed, from the shapes' templates). The
    shapes (dread.shapes analyse of the trees) are made between, with the static data found without
    the callees' reach, an index added to whose addresses is no pointer for them; they are returned
    for the readable_types plan. symbols, trust: as for dread.statics plan."""
    from dread import shapes, statics
    evidence = evidence if evidence is not None else statics.gather(program, flow=flow)
    seen = {fn.name: sorted(set(macros.values())) for _, fn, macros in trees}
    indexed = {fn.name: statics.indexed_accesses(fn, context.memory) for _, fn, _ in trees}
    arrays = {}
    found = statics.plan(program, evidence, extra=seen, symbols=symbols, trust=trust, indexed=indexed, arrays=arrays)
    s = shapes.analyse(trees, unread=context.unread, static_spans=static_spans(found, arrays))
    given = statics.handed(trees, s, context.memory)
    if given:
        arrays = {}
        found = statics.plan(program, evidence, extra=seen, symbols=symbols, trust=trust, indexed=indexed,
                             arrays=arrays, handed=given)
    return found, arrays, s


def static_spans(found, arrays):
    """[(start, end)] of the structures and arrays of static data (static_plan)."""
    from dread.statics import SIZES
    out = [(b, b + max(off + SIZES[t] for off, t in rows)) for b, rows in found.items()]
    return sorted(out + [(b, b + k * n) for b, (k, n, _) in arrays.items()])


def check(target, image, names=None, out=print, jobs=None):
    """The acceptance loop for readable C (see the module's docstring); returns the accepted names.
    names: functions of the lifted table to try (default: all of it). A build that dropping functions
    cannot mend raises dread.build BuildFailed, the readable table as it was (dread readable --check
    exits 1; until 2026-10-23 it exited 0 with the old table in place)."""
    from dread import build, lift, overlay, regflow, runner
    from dread.program import Program
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = Context(target, image, program, flow)
    work = os.path.join(target.build, "readable-check", image.name)
    lifted = build.read_names(image.table("lifted"))
    # The structures and arrays of static data (dread.statics), then the structures behind pointers
    # (dread.shapes), from every lifted function's readable C before any is checked: the readable_types
    # table, which the builds read too. Static data first: an index added to an address inside it is
    # no pointer for the shapes.
    from dread import shapes
    digests = {}
    trees = analysis_trees(program, context, lifter, lifted, named=context.globals, digests=digests)
    found, arrays, s = static_plan(program, context, trees, flow)
    context.plan = shapes.plan(s, trees, digests)
    context.plan.statics, context.plan.arrays = found, arrays
    context._struct_names = context._static_names = None
    shapes.store_plan(image, context.plan)
    out("dread readable --check: %d structures, pointers to them in %d functions; %d structures and %d arrays of "
        "static data (readable_types)" % (len(context.plan.structs), len(context.plan.typed),
                                          len(context.plan.statics), len(context.plan.arrays)))
    by_system = build.system_names(image.table("lifted"))
    verdicts = {}                           # name -> (verdict, reason)
    texts = {}                              # name -> its lifted C, None where there is no such function
    for name in names or lifted:
        if name not in lifted:
            verdicts[name] = ("not lifted", "not in the lifted table: dread lift --check first")
        elif name in by_system:
            verdicts[name] = ("lifted", "accepted by the system run: readable C is not checked there yet")
        else:
            fn = program.by_name.get(name)
            try:
                texts[name] = build.lifted_result(lifter, fn).text if fn is not None else None
            except lift.Refused as e:
                verdicts[name] = ("lifted", "no longer lifts: %s" % e)
    # Every candidate's readable C at once, in a pool (readable_texts); the rounds' builds and the whole
    # image's take it from there as long as the context's tables and the lifted C stay the same.
    made = readable_texts({n: t for n, t in texts.items() if t is not None}, context, jobs)
    candidates = []
    for name, text in texts.items():
        if text is None or made[name] is None:
            verdicts[name] = ("lifted", "its lifted C is not the dialect dread.cparse reads")
        else:
            candidates.append(name)
    accepted, rounds = list(candidates), 0
    # The function-level runs build what the lift check's do: the lifted functions but those the
    # system run accepted (functions that never return, handlers: the lift check tests them alone),
    # which come in for the whole image only. With them built from lifted C, a case of FreeRTOS's
    # prvRegTestTaskEntry1 that the lift check accepted failed: the configurations differed
    base = [n for n in lifted if n not in by_system]

    def drop(names_, why):
        for n in names_:
            out("lifted   %-24s %s" % (n, why[:200]))
            verdicts[n] = ("lifted", why[:300])
    while accepted:
        rounds += 1
        try:
            payload = build.build(target, image, out=work, lifted=base, quiet=True, lifter=lifter,
                                  transform=chooser(accepted, context, jobs))
        except overlay.OverlayError as e:
            if e.function in accepted:
                drop([e.function], "the overlay: %s" % e)
                accepted = [n for n in accepted if n != e.function]
                continue
            bad = [n for n in accepted if any(os.path.basename(c) == n + ".c" for c in e.failed)]
            if bad:                         # (a function whose readable C does not compile stays lifted)
                drop(bad, "its readable C does not compile")
                accepted = [n for n in accepted if n not in bad]
                continue
            if e.room:                      # no room for all: the largest readable ones without room go
                over, unplaced, sizes = e.room
                gone, freed = [], 0
                for name in unplaced:
                    if name in accepted and freed < over:
                        gone.append(name)
                        freed += sizes[name]
                if gone:
                    drop(gone, "the overlay: no room left for its readable C (%d bytes short)" % over)
                    accepted = [n for n in accepted if n not in gone]
                    continue
            out("dread readable: the build failed: %s" % e)
            raise build.BuildFailed(str(e))
        foreign = lift.foreign_calls(program, work, accepted, lifter)
        if foreign:
            for name, symbols in sorted(foreign.items()):
                drop([name], "its readable C calls %s, which the original does not call" % ", ".join(sorted(symbols)))
            accepted = [n for n in accepted if n not in foreign]
            continue
        runner.prepare(target, image, payload)
        lines = []
        _, uncovered, results = runner.run(accepted, only=True, out=lines.append, jobs=jobs)
        with open(os.path.join(work, "round-%d.txt" % rounds), "w") as f:
            f.write("\n".join(lines) + "\n")
        failing = {name for name, count, bad in results if bad}
        gone = failing | set(uncovered)
        for text in lines:
            for line in text.splitlines():
                for name in gone:
                    if line.startswith(("FAIL %s " % name, "UNCOVERED %s:" % name)) and name in accepted:
                        if verdicts.get(name, ("",))[0] != "lifted":
                            drop([name], line)
        accepted = [n for n in accepted if n not in gone]
        if not gone:
            break
    runs = target.scenarios and hasattr(program.arch, "reset")
    whole_note = "not run"

    tried = {}

    def whole(names):
        key = tuple(sorted(names))
        if key not in tried:
            tried[key] = lift.whole_image(target, image, program, lifted, out, lifter,
                                          transform=chooser(key, context, jobs))
        return tried[key]
    found = whole(accepted) if runs and accepted else None
    baseline = whole([]) if found else None
    if baseline:
        # The image with lifted C alone differs already (a lifted table the lift check wrote before
        # 2026-10-22, when it reported the difference and kept the functions; it now keeps out those
        # that make it). There the scenarios cannot tell readable C from lifted C, so the function-level
        # runs decide, as for lifted C. (FreeRTOS on RISC-V GCC -Os and Cortex-M clang -O2 until
        # 2026-10-22: the system run's wait rule counted blocks of code the images do not share and
        # fell at other places in them, so whether a run agreed depended on how it fell: the halves
        # of RISC-V's 258 readable functions each agreed, all together not; dread.sysrun.)
        out("the whole image differs with lifted C alone (%s): the scenarios decide nothing here" % baseline)
        whole_note = "differs with lifted C alone: not decisive"
        found = None
    else:
        whole_note = "agrees"
    while found:
        # The function-level cases agreed, the scenarios do not: a readable function behaves otherwise
        # on an input its cases lack, or only together with others (frames that grew). The smallest
        # set that still differs, found by halving, goes back to lifted C and the rest is tried again
        # (RISC-V GCC -Os: the image printed nothing). Until 2026-10-22 halves of the accepted ones,
        # each tried alone: two that differ only together, one in each half, sent both halves back.
        out("the whole image: %s" % found)
        differs = lambda names: whole(names) if names else None    # noqa: E731
        guilty = lift.culprits(sorted(accepted), differs)
        one = lift.enough(accepted, guilty, differs, lift.function_bytes(program))
        drop([one] if one else guilty, "the whole image differs with its readable C%s (found by halving): %s" % (
            "; without it alone it agrees, of " + ", ".join(guilty) if one else "", found))
        accepted = [n for n in accepted if n not in ([one] if one else guilty)]
        found = whole(accepted) if accepted else None
    for name in accepted:
        verdicts[name] = ("readable", "")
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "verdicts.tsv"), "w") as f:
        f.write("function\tverdict\treason\n")
        f.writelines("%s\t%s\t%s\n" % (n, v, r.replace("\t", " ").replace("\n", " "))
                     for n, (v, r) in sorted(verdicts.items()))
    path = image.table("readable")
    kept = sorted(accepted)
    with open(path, "w") as f:
        f.write("# Functions built from readable C (dread readable): made from their lifted C at every build;\n"
                "# their cases agree with the original and run every instruction it can reach, and the whole\n"
                "# image agrees. Written by dread readable --check; not edited by hand.\n")
        f.write("".join(n + "\n" for n in kept))
    out("dread readable --check: %d of %d lifted functions readable after %d rounds (%d stay lifted; the whole "
        "image %s), into %s" % (len(kept), len(lifted), rounds, len(lifted) - len(kept),
                                whole_note if runs and candidates else "not run", os.path.relpath(path)))
    return kept


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread readable", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--check", action="store_true", help="test the readable C and write the readable table")
    parser.add_argument("--out", help="write NAME.c files there instead of printing")
    parser.add_argument("--score", metavar="ELF", help="how close the readable_names table's names come to the "
                        "source's, from ELF's debug information (dread.naming.score); with --shapes, the "
                        "structures' fields too")
    parser.add_argument("--shapes", action="store_true", help="the structures behind pointers (dread.shapes) of "
                        "the readable functions (or those named), largest first")
    parser.add_argument("--statics", action="store_true", help="the structures and arrays of static data "
                        "(dread.statics) every function's machine code and the lifted functions' readable C show; "
                        "with --score ELF, against the ELF's variables")
    parser.add_argument("--no-symbols", action="store_true", help="--statics: the target's data symbols end no "
                        "structure (what the code alone decides)")
    parser.add_argument("--every-base", action="store_true", help="--statics: every base, also where the code does "
                        "not reach its neighbours apart from it (what that evidence keeps out)")
    parser.add_argument("--figures", metavar="FILE", help="write the figures of the readable functions' (or the "
                        "named ones') readable C and lifted C to FILE as JSON (dread.cmetrics: statements, "
                        "depth, gotos, casts, raw accesses, members, typed variables; dread bench's readable step)")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
    except tgt.TargetError as e:
        print("dread readable: %s" % e, file=sys.stderr)
        return 1
    if args.check:
        from dread.util.polite import polite
        polite()
        from dread import build
        try:
            check(target, image, args.names or None)
        except build.BuildFailed:
            print("dread readable --check: the build failed (above); the readable table is as it was",
                  file=sys.stderr)
            return 1
        return 0
    if args.statics:
        from dread import build, lift, regflow, statics
        from dread.program import Program
        from dread.util.polite import polite
        polite()
        program = Program(target, image)
        flow = regflow.load(target, image, program)
        evidence = statics.gather(program, flow=flow)
        context = Context(target, image, program, flow)
        lifted = build.read_names(image.table("lifted")) if os.path.exists(image.table("lifted")) else [
            fn.name for fn in program.functions]   # (no lifted table: every function that lifts)
        trees = analysis_trees(program, context, lift.make_lifter(program, flow), lifted, named=context.globals)
        found, arrays, _ = static_plan(program, context, trees, flow, symbols=not args.no_symbols,
                                       trust="all" if args.every_base else "evidence", evidence=evidence)
        if not args.score:
            for line in statics.describe(found, arrays):
                print(line)
        else:
            reached = [(a, t) for ev in evidence.values() for a, t, _, ix in ev.accesses if not ix]
            s = statics.score(found, args.score, reached, arrays=arrays)
            for base, verdict, names in s["rows"]:
                print("0x%08x %-16s %s" % (base, verdict, ", ".join(names)))
            print("dread readable --statics --score: %d structures of static data: %d inside one source variable (%s), "
                  "%d over several, %d at none; %d fields, %d the source's; of %d source structures and arrays "
                  "reached at two places or more, %d grouped whole, %d in part" % (
                      s["structures"], sum(s["one"].values()), ", ".join(
                          "%d %s" % (n, k) for k, n in sorted(s["one"].items())) or "-", s["several"], s["unknown"],
                      s["fields"], s["right"], s["reached"], s["grouped"], s["partly"]))
            print("dread readable --statics --score: %d arrays of static data: %d inside one source variable (%s), "
                  "%d over several, %d at none; %d of an element size the source's; %d fields in their elements, %d "
                  "the source's" % (
                      s["arrays"], sum(s["arrays_one"].values()), ", ".join(
                          "%d %s" % (n, k) for k, n in sorted(s["arrays_one"].items())) or "-", s["arrays_several"],
                      s["arrays_unknown"], s["arrays_size"], s["array_fields"], s["array_right"]))
        return 0
    if args.figures:
        import json
        from dread import build, lift, regflow
        from dread.program import Program
        from dread.util.polite import polite
        polite()
        program = Program(target, image)
        flow = regflow.load(target, image, program)
        names = args.names or build.read_names(image.table("readable"))
        got = figures(program, Context(target, image, program, flow), lift.make_lifter(program, flow), names)
        with open(args.figures, "w") as f:
            json.dump(got, f, indent=1, sort_keys=True)
        print("dread readable --figures: %d of %d functions measured, into %s" % (len(got), len(names), args.figures))
        return 0
    if args.shapes:
        from dread import build, lift, regflow, shapes
        from dread.program import Program
        program = Program(target, image)
        flow = regflow.load(target, image, program)
        names = args.names or build.read_names(image.table("readable"))
        context = Context(target, image, program, flow)
        trees = readable_trees(program, context, lift.make_lifter(program, flow), names)
        s = shapes.analyse(trees, unread=context.unread)
        for line in shapes.report(s, trees):
            print(line)
        if args.score:
            per, total = shapes.score(s, program, [(fn.name, len(fn.params)) for _, fn, _ in trees], args.score)
            for sname, (found, right, src, n) in sorted(per.items(), key=lambda kv: -kv[1][3]):
                print("%-32s %3d parameters: %4d fields found, %4d the source's, %4d in the source" % (
                    sname, n, found, right, src))
            print("dread readable --shapes --score: %d parameters pointing to a structure: %d fields found, %d the "
                  "source's (%.0f %%); %.0f %% of the source's members found" % (
                      total[3], total[0], total[1], 100.0 * total[1] / max(total[0], 1),
                      100.0 * total[1] / max(total[2], 1)))
            shapes.kinds(s, trees)
            sk = shapes.score_kinds(program, shapes.Plan(), args.score, s, trees)["kinds"]
            print("dread readable --shapes --score: %d source structures in %d kinds that parameters point to (%d "
                  "beyond one each); %d kinds paired with several source structures (%d of different sizes)" % (
                      sk["source"], sk["ours"], sk["extra"], sk["several"], sk["sizes"]))
        return 0
    if args.score:
        from dread.program import Program
        rows, totals = naming.score(Program(target, image), image, args.score)
        for r in rows:
            print("%-32s %s; locals %d of %d among the source's" % (r["function"], ", ".join(
                "%s/%s" % pair for pair in r["pairs"]) or "no parameters", r["local_hits"], r["locals"]))
        p = max(totals["params"], 1)
        print("dread readable --score: %d functions, %d parameters: %d named as the source (%.0f %%), %d with a "
              "word of the source's (%.0f %%); %d of %d local names among the source's" % (
                  totals["functions"], totals["params"], totals["exact"], 100.0 * totals["exact"] / p,
                  totals["words"], 100.0 * totals["words"] / p, totals["local_hits"], totals["locals"]))
        context = Context(target, image)
        if context.plan.structs:
            srows, st = naming.score_structs(Program(target, image), context.plan, context.struct_names(), args.score)
            for r in srows:
                print("%-8s %s/%s: %s" % (r["struct"], r["name"], "/".join(r["source"]), ", ".join(
                    "%s/%s" % (a, b) for a, b, _, _ in r["fields"])[:300]))
            s_ = max(st["structs"], 1)
            f_ = max(st["fields"], 1)
            print("dread readable --score: %d structures named against a parameter's source type: %d as the source "
                  "(%.0f %%), %d with a word of its; %d fields: %d as the source (%.0f %%), %d with a word of its" % (
                      st["structs"], st["exact"], 100.0 * st["exact"] / s_, st["words"], st["fields"],
                      st["field_exact"], 100.0 * st["field_exact"] / f_, st["field_words"]))
            from dread import shapes
            pt = shapes.score_pointers(Program(target, image), context.plan, args.score)
            print("dread readable --score: %d fields declared pointers to a structure, %d such pointers in the "
                  "source (%.0f %%); %d of the source's pointers to a structure left integers" % (
                      pt["fields"], pt["right"], 100.0 * pt["right"] / max(pt["fields"], 1), pt["missed"]))
            sp = shapes.score_plan(Program(target, image), context.plan, args.score)
            print("dread readable --score: %d structures of typed parameters: %d fields declared (those of the "
                  "structures they hold included), %d the source's (%.0f %%); %d structures held as members, %d "
                  "where the source holds one" % (
                      sp["structures"], sp["fields"], sp["right"], 100.0 * sp["right"] / max(sp["fields"], 1),
                      sp["held"], sp["held_right"]))
            sk = shapes.score_kinds(Program(target, image), context.plan, args.score)["structures"]
            print("dread readable --score: %d source structures in %d structures (typed parameters', those their "
                  "pointer fields point to and they hold; %d beyond one each); %d paired with several source "
                  "structures (%d of different sizes)" % (sk["source"], sk["ours"], sk["extra"], sk["several"],
                                                          sk["sizes"]))
        rows, totals = naming.score_globals(image, args.score)
        for r in rows:
            where = ("%s+%d" % (r["symbol"], r["offset"]) if r["offset"] else r["symbol"]) if r["symbol"] else "?"
            print("%-32s %s" % (r["key"], "%s/%s" % (r["name"], where)))
        g = max(totals["globals"] - totals["inside"] - totals["unknown"], 1)
        print("dread readable --score: %d globals: %d at a symbol's address, %d named as the source (%.0f %%), %d with a "
              "word of the source's; %d inside a symbol (a member, an element), %d at no symbol" % (
                  totals["globals"], totals["globals"] - totals["inside"] - totals["unknown"], totals["exact"],
                  100.0 * totals["exact"] / g, totals["words"], totals["inside"], totals["unknown"]))
        return 0
    from dread import lift, regflow
    from dread.program import Program
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = Context(target, image, program, flow)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
    status = 0
    for name in args.names or [fn.name for fn in program.functions]:
        fn = program.by_name.get(name)
        if fn is None:
            print("dread readable: no function %s" % name, file=sys.stderr)
            status = 1
            continue
        try:
            text = transform(name, lifter.lift(fn).text, context=context)
        except lift.Refused as e:
            print("dread readable: %s does not lift: %s" % (name, e), file=sys.stderr)
            continue
        if text is None:
            print("dread readable: %s: its lifted C is not the dialect dread.cparse reads" % name, file=sys.stderr)
            continue
        if args.out:
            with open(os.path.join(args.out, name + ".c"), "w") as f:
                f.write(text)
        else:
            sys.stdout.write(text)
    return status


if __name__ == "__main__":
    sys.exit(main())
