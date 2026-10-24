"""dread readable: lifted C made readable by transformations in code, each result checked as lifted C is.

dread lift writes C that is exact and unreadable: every register a variable, every branch a goto,
every load and store volatile. This turns it into C a person can read, one transformation after
another on the tree of dread.cparse (STAGES), without a language model. None of it has to be right
by construction: `--check` builds every function of the lifted table from its readable C, runs the
cases the lift check accepted it with, and keeps a function's readable C only when they agree and
run every instruction of the original, as for lifted C, and when the whole image's scenarios agree;
a function that fails is built from its lifted C again (one whose frame's objects, dread.frames, its
cases reject is tried once more first, its frame's region one object: the readable_types table's
whole; one whose code reaches another function that fails in the same round is tried once more with
that one from lifted C, since its cases run their callees for real). The functions accepted go into the image's
readable table (config/readable.txt), and dread recon builds them from readable C. Names and types
are left to the pilot (dread pilot).

Stages so far: the clean-up (dread.tidy: what only the machine needed out, compares and their
branches as comparisons), then the structure (dread.structure: gotos as if/else and loops). After
them the tables (_named): memory at fixed addresses as names, the fields of structures of static
data as members of one object (dread.naming apply_globals with the readable_types plan's statics,
dread.statics: g_20000030.f_4), the structures behind pointers (dread.shapes), the types of variables,
parameters and externs (dread.vartypes: volatile u8 *p, p[1], s32), the variables' names;
then the part of the frame that addresses handed out reach becomes the function's local objects
(dread.frames: one at each address handed out, below the words the entry saves its caller's registers
into; a scalar stack36, an array of bytes u8 stack20[12] or of the width an index or a callee's
parameter says, stack48[r3], a callee's structure struct s_29 stack32, a structure of its own for
mixed accesses, stack40.f_4, with a union where accesses overlap; volatile only where an address
escapes into memory, by the readable_types table's keeps). Last, constants and casts are written by their use
(dread.polish: -1 for ~0x0u returned, ' ' for 0x20u compared with a character, 0 for (u8)0x0u stored
into a byte, x++); the naming questions' C gets it too, after the digest their names are made for.

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

Readable C has three forms. The compiled form (transform, print_file) is what dread.build compiles
and the gate checks: one file a function, every structure it uses defined in it. The printed form
(printed: `dread readable NAME`, `--out DIR`) leaves the image's structures, those behind pointers
and of static data, to one header (HEADER, readable_types.h: header_text, `--header`; `--out` writes
it beside the files) that it includes instead, names in a comment the members it uses (their types
and offsets), and keeps what is the function's own: its frame's structure, its extern declarations
(trimmed to what its calls pass) and its macros (named per function, and not scoped: in a shared
header they would expand inside other files); compiled with the header beside it, it is the compiled
form's code. The reading form (reading: `--plain`, dread gui's reading view) is the printed form
without the instructions' comments, their notes (pushes, pops, stack adjustments) and dread.build's
lines for the compiler, each statement's first instruction in a margin (dread.cparse
first_addresses: of its own instruction and those dread.tidy folded into it alone, the lowest).
Since 2026-10-23; before, every form was the compiled one (docs/decisions.md).

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
    result registers, the registers each function reads at its entry and those whose value at its
    return is its own (dread.regflow reads and overwritten). Empty without a target."""

    def __init__(self, target=None, image=None, program=None, flow=None):
        self.arg_regs, self.reads, self.preserved, self.variadic = [], {}, {}, set()
        self.save_areas = set()         # functions whose entry pushes argument registers only (entry_save_areas)
        self.results = []               # the registers a result comes back in (the target's [abi])
        self.overwritten = {}           # function -> what it writes on every path, its caller's never read
        self.unread = set()             # functions whose result no caller reads (dread.shapes analyse)
        self.names = {}                 # the image's readable_names table (dread.naming): functions
        self.globals = {}               # and memory at fixed addresses
        self.memory = None              # the target's [memory] (default names for fixed addresses)
        from dread import shapes
        self.plan = shapes.Plan()       # the image's readable_types table (dread.shapes)
        self.structs = {}               # the readable_names table's structs part
        self.image_spans = None         # where the image keeps static data (dread.statics image_spans)
        self._struct_names = None
        self._static_names = None
        self._program = None            # the program (its machine code: pushes and the frame's save words,
        self._saves = {}                # dread.frames; no table, so not in context_key)
        if target is None:
            return
        self.plan = shapes.load_plan(image)
        self.structs = naming.load(image, "structs")
        self.names = naming.load(image)
        self.globals = naming.load(image, "globals")
        self.memory = target.memory
        from dread import regflow, statics
        from dread.program import Program
        program = program or Program(target, image)
        self._program = program
        flow = flow or regflow.load(target, image, program)
        self.image_spans = statics.image_spans(program)
        self.arg_regs = list(target.abi.arg_regs)
        self.results = list(target.abi.result)
        self.reads = {fn.name: set(flow.read_regs(fn.name)) for fn in program.functions}
        self.preserved = {fn.name: set(flow.preserved_regs(fn.name)) for fn in program.functions}
        self.overwritten = {fn.name: set(flow.overwritten_regs(fn.name)) for fn in program.functions}
        result = target.abi.result[0] if target.abi.result else None
        self.unread = {fn.name for fn in program.functions if result not in flow.kept_regs(fn.name)}
        self.variadic = variadic_reach(program)
        self.save_areas = entry_save_areas(program)

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

    def saves(self, name):
        """{offset from sp0 (negative): register} of the frame words a function's entry saves what it keeps
        for its caller into (dread.frames save_words: its callee-saved registers and the return
        address), {} without a program or for a function the program does not have; made once a
        function. They are no local's bytes (dread.frames)."""
        if name not in self._saves:
            fn = self._program.by_name.get(name) if self._program is not None else None
            out = {}
            if fn is not None:
                from dread import frames
                out = {-k: r for k, r in frames.save_words(self._program, fn).items()}
            self._saves[name] = out
        return self._saves[name]

    def is_push(self, address):
        """Whether the instruction at address is a push (the arch layer's stack_kind); False without a
        program or where it says nothing."""
        prog = self._program
        if prog is None or address is None or not hasattr(prog.arch, "stack_kind"):
            return False
        insn = prog.insn(address)
        return insn is not None and prog.arch.stack_kind(insn) == "push"

    def unknown(self, name):
        """Whether what a function reads cannot be told from regflow: it is variadic or calls one on
        (regflow counts a variadic function's unnamed argument registers in its save area as no use,
        right for the lifter, which passes them all; FreeRTOS's vAssertCalled hands its line number on
        to printf, and trimmed to its first argument it printed garbage on clang -O2)."""
        return self.variadic is None or name in self.variadic

    def arity(self, name):
        """How many argument registers a function reads at its entry, counted from the first up to the
        last it reads; None when it is not known (unknown, or its entry keeps argument registers in a
        save area: entry_save_areas)."""
        if name not in self.reads or self.unknown(name) or name in self.save_areas:
            return None
        return max((i + 1 for i, r in enumerate(self.arg_regs) if r in self.reads[name]), default=0)

    def handed_back(self, name, wide=False):
        """The argument registers in which a call of name may hand back what its caller passed: those of
        its result registers (the first, both for a 64-bit result) that are not its own at its return
        (regflow overwritten; all of them where that is not known). Without as many result registers of
        the target's (a Context made by hand), the first one or two argument registers."""
        count = 2 if wide else 1
        results = (self.results if len(self.results) >= count else self.arg_regs)[:count]
        own = self.overwritten.get(name, set())
        return [r for r in results if r in self.arg_regs and r not in own]


SAVE_AREA_LEAD = 2      # the most instructions in front of a save area's push that write neither sp nor an
                        # argument register (APCS's mov ip, sp: entry_save_areas)


def entry_save_areas(program):
    """The functions whose first push stores argument registers only, the last of them among them (the
    arch layer's stack_kind and pushed_registers; the lifter's save_area): a variadic function's save
    area as GCC's newlib keeps it (printf pushes r0-r3, sprintf r1-r3, before it saves anything else),
    which variadic_reach does not find (regflow's variadic_save_area leaves pushes to its rows). The
    push is the first instruction, or follows at most SAVE_AREA_LEAD instructions that write neither
    sp nor an argument register and go on to the next: an APCS frame keeps the entry's stack pointer
    first (mov ip, sp; push {r1, r2, r3}; push {r4, ip, lr}: a variadic function of a private
    firmware, until 2026-10-24 not found: its callers' calls lost the arguments its va_list reads, and
    push_room took its pushes out). A call of one keeps all its arguments (Context.arity): trimmed to
    what regflow says it reads, FreeRTOS's GCC -Os tasks called sprintf(buf, fmt) without the value it
    prints, and the whole image agreed only as long as the C compiler happened to leave that value in
    r2 (2026-10-23, when dread.frames changed their frames' layout). Not propagated to their callers'
    callers as variadic_reach does (a function that hands its arguments on to printf, vAssertCalled,
    keeps its trimmed calls): empty where the layer has no pushes."""
    arch = program.arch
    if not (hasattr(arch, "stack_kind") and hasattr(arch, "pushed_registers")):
        return set()
    args = list(program.target.abi.arg_regs)
    out = set()
    for fn in program.functions:
        for a in fn.insns[:SAVE_AREA_LEAD + 1]:
            insn = program.insn(a)
            if insn is None:
                break
            if arch.stack_kind(insn) == "push":
                regs = [r for r in arch.pushed_registers(insn) if r is not None]
                if regs and args and set(regs) <= set(args) and args[-1] in regs:
                    out.add(fn.name)
                break
            if not hasattr(arch, "reg_effects") or arch.flow(insn)[0] != "next" or \
                    arch.reg_effects(insn)[1] & (set(args) | {"sp"}):
                break
    return out


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
FRAME_OBJECT = re.compile(r"^\s*(?:volatile\s+)?[\w ]+\bstack\d+(?:_\d+)?\b(?:\[\d+\])?"
                          r"(?:\s+__attribute__[^;]*)?;$", re.M)     # (dread.frames' objects, declared)


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
                                  arrays=context.plan.arrays if with_statics else None,
                                  pointers=context.plan.params if with_statics else None,
                                  image=context.image_spans if with_statics else None)
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
    ids = naming.identities(fn)
    int_bits = tidy.context(fn).int_bits
    naming.apply_globals(tree, fn, context.globals, context.memory, statics=context.plan.statics,
                         names=context.static_names(), arrays=context.plan.arrays, pointers=context.plan.params,
                         image=context.image_spans)
    shapes.apply_types(tree, fn, context.plan, digest, names=context.struct_names())
    from dread import vartypes
    vartypes.apply(tree, fn, context.plan, digest, names=context.struct_names(), int_bits=int_bits)
    if not tidy.fixed_registers(tree):
        polish_tree(tree, fn, context, int_bits)
    return tree, fn, digest, print_file(tree, fn), cparse.unparse(fn, notes=False, style="readable"), ids


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
    """{name: (tree, function, digest, file, code, ids), or why there is none (a text)}: what a question
    for the names of a function's variables needs (dread pilot --task readable), made in a pool
    (in_pool) before any question is asked. The tree is the readable C after the stages, digest what a
    proposal is made for (dread.naming digest, before the names), ids its variables' identities there
    (dread.naming identities: what carries names to C made anew), then the context's globals and
    structures applied (its tables as they are now); file: the whole file as it compiles without the
    variables' names (print_file), code: the function as the question shows it. context must not be
    None."""
    warm(lifter)
    context.struct_names()                  # (once, before the pool forks: the workers share them)
    context.static_names()
    names = list(names)
    return dict(zip(names, in_pool(_question_one, names, jobs, program=program, context=context, lifter=lifter)))


def print_file(tree, fn, stages=None):
    """The compiled form: the whole file as dread.build compiles it, every structure it uses defined
    in it (see printed for the form dread readable prints)."""
    others = [f.name for f in tree.items if isinstance(f, cparse.Function) and f.name != fn.name]
    return cparse.unparse(tree, verbatim=others, style="readable" if (STAGES if stages is None else stages)
                          else "lifted")


HEADER = "readable_types.h"     # the image's structures, defined once (dread readable --out writes it)
COMPILE_LINE = "/* dread: "     # dread.build's lines for the compiler (NULL_CHECKS, CALL_SAVED, FIXED)
LIFTED_LINE = re.compile(r"^/\* (\S+) at 0x([0-9a-fA-F]+), lifted by dread lift: one statement per instruction\. \*/$")
FORWARD = re.compile(r"^struct (\w+);$")
OBJECT = re.compile(r"^#define (\w+) \(\*\(volatile struct (\w+) \*\)0x[0-9a-fA-F]+u\)$", re.M)
ARRAY = re.compile(r"^#define (\w+) \(\(volatile struct (\w+) \*\)0x[0-9a-fA-F]+u\)$", re.M)
STRUCT_TYPE = re.compile(r"^(?:(?:volatile|const)\s+)*struct\s+(\w+)\s*(\*?)$")


class Types:
    """The image's structures as its readable C defines them: those behind pointers (the readable_types
    table's structs, dread.shapes) and those of static data (its statics and arrays, dread.statics),
    named by the readable_names table, each written as dread.shapes apply_types and dread.naming
    apply_globals write it into the file of every function that uses it (shapes.struct_text with the
    same arguments). defs: {name: definition} in an order C takes (a structure another holds before
    it); forward: the structures a pointer field names (declared first); fields: {name: {field:
    (offset, its type as the definition declares it)}}."""

    def __init__(self, context):
        from dread import naming, shapes
        context = context or Context()
        p = context.plan
        names = context.struct_names()

        def cname(k):
            return names.get(k, (k, {}))[0]
        order = []

        def visit(k, depth=0):              # (as apply_types orders them: a held one before its holder)
            if k in order or depth > 8:
                return
            for inner in sorted(set(p.embeds.get(k, {}).values())):
                visit(inner, depth + 1)
            order.append(k)
        for k in sorted(p.structs):
            visit(k)
        self.defs, self.fields = {}, {}
        for k in order:
            fields = names.get(k, (k, {}))[1]
            ptrs = {off: cname(t) for off, t in p.pointers.get(k, {}).items()}
            embeds = {off: (cname(i), p.size(i)) for off, i in p.embeds.get(k, {}).items()}
            self.defs[cname(k)] = shapes.struct_text(cname(k), p.structs[k], fields, ptrs, embeds)
            mine = {shapes.field_name(fields, off): (off, ctype) for off, ctype in p.structs[k]}
            mine.update({shapes.field_name(fields, off): (off, "volatile struct %s *" % t) for off, t in ptrs.items()})
            mine.update({shapes.field_name(fields, off): (off, "struct %s" % n) for off, (n, _) in embeds.items()})
            self.fields[cname(k)] = mine
        self.forward = sorted({cname(t) for ps in p.pointers.values() for t in ps.values()})
        rows_of = naming._static_rows(p.statics, p.arrays)
        statics = context.static_names() if rows_of else {}
        for b in sorted(rows_of):               # (as apply_globals defines them; an array of scalars has none)
            rows, k, _ = rows_of[b]
            if b not in statics or (k is not None and naming.scalar_array(k, rows)):
                continue
            _, tag, fields = statics[b]
            self.defs[tag] = shapes.struct_text(tag, rows, fields, size=k)
            self.fields[tag] = {shapes.field_name(fields, off): (off, ctype) for off, ctype in rows}
        self.texts = set(self.defs.values())

    def header(self, image=None):
        """The header that defines them all (HEADER), or None when the image has none."""
        if not self.defs:
            return None
        lines = ["/* The structures of the readable C%s, behind pointers (dread.shapes) and of static data" % (
                     " of image %s" % image if image else ""),
                 " * (dread.statics): its readable_types table, named by its readable_names table. Written by",
                 " * dread readable; each function's printed form includes it. */",
                 "#ifndef READABLE_TYPES_H", "#define READABLE_TYPES_H", '#include "dread.h"']
        lines += ["struct %s;" % n for n in self.forward]
        lines += list(self.defs.values())
        lines.append("#endif")
        return "\n".join(lines) + "\n"

    def image_level(self, item):
        """Whether a Raw item of a function's file is one of these definitions, or a declaration of
        one of these structures (what the header holds instead)."""
        if item.text in self.texts:
            return True
        m = FORWARD.match(item.text)
        return bool(m) and m.group(1) in self.fields

    def members(self, tree, fn):
        """{structure: {field}} of these structures that fn's expressions reach as members: through a
        variable or parameter declared a pointer to one, an object or array of static data (its macro),
        a member that holds one or points to one, a cast to a pointer to one."""
        env = {}

        def declared(spec, ptr=""):
            m = STRUCT_TYPE.match(" ".join((spec + " " + ptr).split()) if ptr else " ".join(spec.split()))
            if m and m.group(1) in self.fields:
                return ("ptr" if m.group(2) else "obj", m.group(1))
            return None
        for t, n in fn.params:
            env[n] = declared(t)
        for item in tree.items:
            if isinstance(item, cparse.Raw):
                for n, tag in OBJECT.findall(item.text):
                    env[n] = ("obj", tag) if tag in self.fields else None
                for n, tag in ARRAY.findall(item.text):
                    env[n] = ("ptr", tag) if tag in self.fields else None
        statements = []

        def visit(s):
            statements.append(s)
            if isinstance(s, cparse.Decl):
                for d in s.items:
                    env[d.name] = declared(s.specs, d.ptr) if not d.dims else None
            for inner in tidy._inner(s):
                visit(inner)
        visit(fn.body)
        used = {}

        def type_of(e):
            if isinstance(e, cparse.Name):
                return env.get(e.id)
            if isinstance(e, cparse.Cast):
                return declared(e.type)
            if isinstance(e, (cparse.Index, cparse.Unary)) and (isinstance(e, cparse.Index) or e.op == "*"):
                t = type_of(e.base if isinstance(e, cparse.Index) else e.e)
                return ("obj", t[1]) if t and t[0] == "ptr" else None
            if isinstance(e, cparse.Member):
                s = struct_of(e)
                field = self.fields.get(s, {}).get(e.name) if s else None
                return declared(field[1]) if field else None
            return None

        def struct_of(e):
            t = type_of(e.base)
            return t[1] if t and t[0] == ("ptr" if e.arrow else "obj") else None
        from dread.cflow import walk_expr
        for s in statements:
            for e in tidy._stmt_all_exprs(s):
                for x in walk_expr(e):
                    if isinstance(x, cparse.Member):
                        k = struct_of(x)
                        if k is not None and x.name in self.fields[k]:
                            used.setdefault(k, set()).add(x.name)
        return used

    def comment(self, used):
        """The comment lines that name the members a function uses, a structure a line, in the
        header's order: /* struct NAME: TYPE FIELD at 0xOFFSET, ... */."""
        out = []
        for name in self.defs:
            if name in used:
                fields = sorted((self.fields[name][f][0], f) for f in used[name])
                out.append("/* struct %s: %s */" % (name, ", ".join(
                    "%s%s%s at 0x%x" % (_short(self.fields[name][f][1]),
                                        "" if self.fields[name][f][1].endswith("*") else " ", f, off)
                    for off, f in fields)))
        return out


def _short(ctype):
    """A field's type as the members' comment names it (volatile struct s_9 * as struct s_9 *)."""
    return " ".join(w for w in ctype.split() if w != "volatile")


_TYPES = weakref.WeakKeyDictionary()    # context -> Types (made once a context)


def types_of(context):
    """Types of a context (kept while it lives; a context without a target has none)."""
    if context is None:
        return Types(None)
    if context not in _TYPES:
        _TYPES[context] = Types(context)
    return _TYPES[context]


def header_text(context, image=None):
    """The image's header (HEADER): every structure its readable C defines, once; None without any."""
    return types_of(context).header(image)


def _printed_items(name, text, context, plain):
    """(items of the printed file as (Raw text or a Function), the function, whether stages ran), or
    None when the lifted C is not the dialect (printed)."""
    made = _named(name, text, None, context)
    if made is None:
        return None
    tree, fn, stages = made
    ran = stages is None or bool(stages)
    types = types_of(context)
    items, moved = [], False
    for item in tree.items:
        if isinstance(item, cparse.Raw):
            if ran and types.image_level(item):
                moved = True
                continue
            if plain and item.text.startswith(COMPILE_LINE):
                continue
            m = LIFTED_LINE.match(item.text) if ran else None
            items.append("/* %s at 0x%s: readable C from its lifted C (dread readable) */" % m.groups() if m
                         else item.text)
        else:
            items.append(item)
    if moved:
        used = types.members(tree, fn)
        at = next((i + 1 for i, x in enumerate(items) if isinstance(x, str) and x.startswith("#include")), 0)
        items[at:at] = ['#include "%s"' % HEADER] + types.comment(used)
    return items, fn, ran


def printed(name, text, context=None):
    """The function's readable C as dread readable prints it: the compiled form (transform) with the
    image's structures left to the image's header (HEADER: an #include where they were, and a comment
    a structure that names the members the function uses, with their types and offsets), its first
    comment saying it is readable C; None when the lifted C is not the dialect. Compiled with the
    header beside it, it is the compiled form's code (the definitions are the same texts)."""
    got = _printed_items(name, text, context, plain=False)
    if got is None:
        return None
    items, fn, ran = got
    others = {f.name for f in items if isinstance(f, cparse.Function) and f is not fn}
    return "\n".join(x if isinstance(x, str) else x.raw if x.name in others else
                     cparse.unparse(x, style="readable" if ran else "lifted") for x in items) + "\n"


def reading(name, text, context=None):
    """The reading form (dread readable --plain, dread gui's reading view): [(address or None, line)]
    of the printed form (printed) without the instructions' comments, their notes (pushes, pops, stack
    adjustments) and dread.build's lines for the compiler, each statement's line with the address of
    its first instruction (cparse.first_addresses: its own, or the first of those the clean-up folded
    into it alone), other lines with None; None when the lifted C is not the dialect. Not C to
    compile as it stands: its margin."""
    got = _printed_items(name, text, context, plain=True)
    if got is None:
        return None
    items, fn, ran = got
    out = []
    for x in items:
        if isinstance(x, str):
            out += [(None, line) for line in x.splitlines()]
        else:
            out += cparse.unparse_lines(x, style="readable" if ran and x is fn else "lifted")
    return out


def margin_text(lines):
    """The reading form as text: each line behind a margin with its address in hex (blank without)."""
    width = max((len("%x" % a) for a, _ in lines if a is not None), default=0)
    return "".join(("%-*s  %s" % (width, "%x" % a if a is not None else "", line)).rstrip() + "\n"
                   for a, line in lines)


def _named(name, text, stages, context=None):
    """(tree, function, stages) of the readable C with the tables applied: the globals' names, the
    structures, the variables' names (each only for the C it was made for), then the frame's region
    that addresses handed out reach as the function's objects (dread.frames); None when the lifted C is not the
    dialect. stages: [] when the function keeps fixed registers (left as the lifter wrote it)."""
    made = readable_tree(name, text, stages, context)
    if made is None:
        return None
    tree, fn = made
    if tidy.fixed_registers(tree):          # (kept as the lifter wrote it: readable_tree ran no stage)
        stages = []
    if STAGES if stages is None else stages:
        made_for = naming.digest(fn)        # (a function's names are made for its C before the globals')
        int_bits = tidy.context(fn).int_bits    # (while the lifter's register names tell it: polish)
        entry = context.names.get(name) if context is not None else None
        mine = set((entry or {}).get("names", {}).values()) if (entry or {}).get("digest") == made_for else set()
        naming.apply_globals(tree, fn, context.globals if context is not None else None,
                             context.memory if context is not None else None, avoid=mine,
                             statics=context.plan.statics if context is not None else None,
                             names=context.static_names() if context is not None else None,
                             arrays=context.plan.arrays if context is not None else None,
                             pointers=context.plan.params if context is not None else None,
                             image=context.image_spans if context is not None else None)
        if context is not None:
            from dread import shapes, vartypes
            shapes.apply_types(tree, fn, context.plan, made_for, names=context.struct_names())
            vartypes.apply(tree, fn, context.plan, made_for, names=context.struct_names(), int_bits=int_bits)
        if entry:
            naming.apply(tree, fn, entry, made_for)     # (only for the C it was made for: its digest)
        from dread import frames            # (the frame that addresses handed out reach as its objects:
        frames.frame_objects(tree, fn, context)     # on the names the table gave)
        polish_tree(tree, fn, context, int_bits)    # (constants and casts by their use: last)
    return tree, fn, stages


def polish_tree(tree, fn, context=None, int_bits=None):
    """dread.polish on the finished tree: constants and casts written by their use (the device regions
    of the context's memory map keep their registers' constants as they are)."""
    from dread import polish
    memory = context.memory if context is not None else None
    polish.polish(tree, fn, int_bits=int_bits, io=getattr(memory, "io", ()) or ())


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


def carry(target, image, names=None, out=print, jobs=None, program=None, flow=None, lifter=None, context=None,
          digests=None):
    """dread readable --carry, and the end of --check: the readable_names table's entries that were
    made for other C carried to the functions' C as it is now (dread.naming carry_table: each name to
    the variable of the same identity, kept where the C with them compiles to the same code; no
    question is asked). names: the functions to look at (default: every function the table names);
    digests ({name: digest}, the check's) leaves out those whose entry was made for that digest
    without making their C again. An entry without identities (made before 2026-10-23) is left as it
    is. Returns {name: the entry carried}."""
    entries = naming.load(image)
    names = [n for n in (names or sorted(entries)) if n in entries]
    if digests is not None:
        names = [n for n in names if entries[n].get("digest") != digests.get(n)]
    if not names:
        return {}
    from dread import lift, regflow
    from dread.program import Program
    program = program or Program(target, image)
    names = [n for n in names if n in program.by_name]
    flow = flow or regflow.load(target, image, program)
    lifter = lifter or lift.make_lifter(program, flow)
    context = context or Context(target, image, program, flow)
    made = question_trees(program, context, lifter, names, jobs)
    other = [n for n in names if not isinstance(made[n], str) and entries[n].get("digest") != made[n][2]]
    got = naming.carry_table(target, image, made, os.path.join(target.build, "readable-check", image.name, "naming"),
                             jobs, out)
    context.names.update(got)
    out("dread readable --carry: of %d function%s named for other C, %d carried (%d names, %d dropped; %d with "
        "variables left without a name), %d without identities, %d carried nothing" % (
            len(other), "s"[len(other) == 1:], len(got), sum(e["carried"]["names"] for e in got.values()),
            sum(len(e["carried"]["dropped"]) for e in got.values()),
            sum(1 for e in got.values() if e["carried"]["open"]),
            sum(1 for n in other if not isinstance(entries[n].get("ids"), dict)),
            sum(1 for n in other if n not in got and isinstance(entries[n].get("ids"), dict))))
    return got


def make_plan(program, context, lifter, names, flow=None, digests=None):
    """The readable_types table of the functions names (the lifted table's) into context.plan, as the
    check makes it before any function is checked, and returned: the structures and arrays of static
    data (dread.statics), then the structures behind pointers (dread.shapes), from every function's
    readable C (static data first: an index added to an address inside it is no pointer for the
    shapes); the types of variables and prototypes (dread.vartypes); which parameters a function may
    keep or return (dread.frames kept_params). The builds read the table too. digests, a dict: filled
    with each function's digest (what the check's carry of names compares with)."""
    from dread import frames, shapes, vartypes
    digests = {} if digests is None else digests
    trees = analysis_trees(program, context, lifter, names, named=context.globals, digests=digests)
    found, arrays, s = static_plan(program, context, trees, flow)
    context.plan = shapes.plan(s, trees, digests)
    context.plan.statics, context.plan.arrays = found, arrays
    context.plan.params = shapes.pointer_params(s, trees)
    context._struct_names = context._static_names = None
    vartypes.plan(context.plan, trees, digests, context)
    context.plan.keeps, context.plan.returns = frames.kept_params(trees, context)
    return context.plan


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
    from dread import shapes
    digests = {}
    make_plan(program, context, lifter, lifted, flow, digests=digests)
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
    reach, direct = {}, {}

    def reaches(name):
        """The functions name's code reaches through direct calls (the original's call graph, as the lift
        check's call_trees has it), worked out for the functions that fail only."""
        if name not in reach:
            seen, todo = set(), [name]
            while todo:
                n = todo.pop()
                if n not in direct:
                    fn = program.by_name.get(n)
                    direct[n] = lift.direct_callees(program, fn) if fn is not None else set()
                for callee in direct[n]:
                    if callee not in seen:
                        seen.add(callee)
                        todo.append(callee)
            reach[name] = seen - {name}
        return reach[name]
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
            files = {os.path.basename(c)[:-2]: c for c in e.failed if c.endswith(".c")}
            bad = [n for n in accepted if n in files]
            if bad:                         # (a function whose readable C does not compile stays lifted,
                for n in bad:               # with the compiler's first error: dread.build compiler_error)
                    error = e.errors.get(files[n], "")
                    drop([n], "its readable C does not compile" + (": " + error if error else ""))
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
        # A function whose frame's region readable C split into objects (dread.frames) is tried once more
        # with the region as one object, as before 2026-10-23: a callee given one of its objects may reach
        # past it where the original reached the next (a generated case's queue with another item size),
        # which the cases see and the split cannot keep (the readable_types table's whole)
        whole = {n for n in gone if n not in context.plan.whole and n in texts and
                 FRAME_OBJECT.search(readable_texts({n: texts[n]}, context, jobs)[n] or "")}
        # A function whose code reaches another that went wrong in this round is tried once more with that
        # one built from lifted C: its cases run its callees for real, so one callee's wrong readable C
        # fails every caller whose cases reach it (2026-10-24, on a private firmware: one function's
        # comparison of a two-word result sent its callers back with it, whose own readable C was
        # right). Not where every function that went wrong reaches another (recursion): then all go,
        # as before. Every round sends one back at least, so the rounds end.
        culprits = {n for n in gone if not (reaches(n) & (gone - {n}))}
        again = {n: sorted(reaches(n) & (gone - {n})) for n in gone - culprits - whole} if culprits else {}
        told = set()                        # (one line a function tried again, its first failure)
        for text in lines:
            for line in text.splitlines():
                for name in gone:
                    if line.startswith(("FAIL %s " % name, "UNCOVERED %s:" % name)) and name in accepted:
                        if name in whole:
                            out("whole    %-24s %s" % (name, ("its frame as one object again: " + line)[:200]))
                        elif name in again:
                            if name not in told:
                                told.add(name)
                                out("again    %-24s %s" % (name, ("with %s from lifted C, which went wrong too: %s" % (
                                    ", ".join(again[name][:3]) + (", ..." if len(again[name]) > 3 else ""),
                                    line))[:200]))
                        elif verdicts.get(name, ("",))[0] != "lifted":
                            drop([name], line)
        if whole:
            context.plan.whole |= whole         # (in context_key: the next round's readable C is made anew)
        accepted = [n for n in accepted if n not in gone - whole - set(again)]
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
    if context.plan.whole:
        context.plan.whole &= set(accepted)
        from dread import shapes
        shapes.store_plan(image, context.plan)  # (the functions whose frame is one object: the builds read it)
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
    # The names proposed for C that the transformations have changed since go to this C where their
    # variables are still there (carry): the check is what runs after the transformations change.
    carry(target, image, out=out, jobs=jobs, program=program, flow=flow, lifter=lifter, context=context,
          digests=digests)
    return kept


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread readable", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--check", action="store_true", help="test the readable C and write the readable table "
                        "(then carry the names, as --carry)")
    parser.add_argument("--carry", action="store_true", help="carry the readable_names table's names that were made "
                        "for other C to the variables of the same identity in the C as it is now (dread.naming "
                        "carry_table: kept where the C still compiles to the same code; no question asked)")
    parser.add_argument("--out", help="write NAME.c files there instead of printing, and the image's header "
                        "(%s) beside them" % HEADER)
    parser.add_argument("--plain", action="store_true", help="the reading form: no instructions' comments, the "
                        "address of each statement's first instruction in a margin (not C to compile)")
    parser.add_argument("--header", action="store_true", help="print the image's header (%s): every structure "
                        "its readable C defines" % HEADER)
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
    if args.carry:
        from dread.util.polite import polite
        polite()
        carry(target, image, args.names or None)
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
    header = header_text(context, image.name)
    if args.header:
        sys.stdout.write(header or "")
        return 0
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, HEADER)
        if header is not None:
            with open(path, "w") as f:
                f.write(header)
        elif os.path.exists(path):          # (from tables that had structures: no file includes it now)
            os.remove(path)
    status = 0
    for name in args.names or [fn.name for fn in program.functions]:
        fn = program.by_name.get(name)
        if fn is None:
            print("dread readable: no function %s" % name, file=sys.stderr)
            status = 1
            continue
        try:
            lifted = lifter.lift(fn).text
        except lift.Refused as e:
            print("dread readable: %s does not lift: %s" % (name, e), file=sys.stderr)
            continue
        if args.plain:
            lines = reading(name, lifted, context)
            text = margin_text(lines) if lines is not None else None
        else:
            text = printed(name, lifted, context)
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
