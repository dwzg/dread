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
them and the tables (_named), the part of the frame that addresses handed out reach becomes one local
structure (dread.frames: stack36.f_4, (u32)&stack36.f_4).

Where the image with lifted C alone already differs from the original in the scenarios (the system
run's clock on some builds), the scenarios decide nothing and the function-level runs decide alone;
elsewhere a difference with readable C sends the guilty functions back to lifted C, found by halves.

Not yet: functions that never return and were accepted by the system run (they stay lifted).
"""
import argparse
import os
import re
import sys

from dread import cparse, naming, structure, tidy
from dread import target as tgt

STAGES = [("tidy", tidy.tidy), ("structure", structure.structure)]     # (name, function(tree)): in order


class Context:
    """What the stages may know beyond the lifted file: the target's argument registers (in order) and
    the registers each function reads at its entry (dread.regflow). Empty without a target."""

    def __init__(self, target=None, image=None, program=None, flow=None):
        self.arg_regs, self.reads, self.preserved, self.variadic = [], {}, {}, set()
        self.names = {}                 # the image's readable_names table (dread.naming): functions
        self.globals = {}               # and memory at fixed addresses
        self.memory = None              # the target's [memory] (default names for fixed addresses)
        from dread import shapes
        self.plan = shapes.Plan()       # the image's readable_types table (dread.shapes)
        self.structs = {}               # the readable_names table's structs part
        self._struct_names = None
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
        self.variadic = variadic_reach(program)

    def struct_names(self):
        """{s_N: (name, {offset: field name})} for the plan's structures the table names (computed
        once; set _struct_names to None after the table or the plan changes)."""
        if self._struct_names is None:
            taken = {e.get("name") for e in self.globals.values() if isinstance(e, dict)}
            self._struct_names = naming.struct_names(self.plan, self.structs, taken)
        return self._struct_names

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


_MEMO = {}          # (name, text, stages, context) -> the readable C: a check's rounds build the same again


def transform(name, text, stages=None, context=None):
    """The readable C of a lifted file (text) whose function is name, or None when it is not the
    lifters' dialect (it then stays lifted). The file's other functions (an MSP430 file's helpers)
    are printed as they were written. context: a Context (the calls' arguments)."""
    key = (name, text, id(STAGES if stages is None else stages), id(context))
    if key not in _MEMO:
        _MEMO[key] = _transform(name, text, stages, context)
    return _MEMO[key]


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


def in_pool(work, names, jobs=None, **shared):
    """[work(name)] for each of names, in order, work a function of this module that reads shared from
    _G: in forked worker processes (dread.util.polite.workers(), or jobs), or in this process for
    fewer than POOL_MIN names or one worker. On a large firmware, making the analysis trees took
    minutes on one core."""
    import multiprocessing
    from dread.util.polite import die_with_parent, polite, resident, workers
    _G.clear()
    _G.update(shared)
    try:
        if len(names) < POOL_MIN:
            return [work(name) for name in names]
        polite()                            # (a bulk run: the pilot's tasks had not lowered theirs)
        n = min(len(names), jobs or workers(memory_each=resident()))     # (a worker may copy all of this one)
        if n <= 1:
            return [work(name) for name in names]
        with multiprocessing.get_context("fork").Pool(n, initializer=die_with_parent) as pool:
            return pool.map(work, list(names), chunksize=4)
    finally:
        _G.clear()


def _lifted_text(name):
    from dread import lift
    fn_p = _G["program"].by_name.get(name)
    if fn_p is None:
        return None
    try:
        return _G["lifter"].lift(fn_p).text
    except lift.Refused:
        return None


def _readable_one(name):
    text = _lifted_text(name)
    context = _G["context"]
    made = readable_tree(name, text, context=context) if text is not None else None
    if made is None:
        return None
    tree, fn = made
    digest = naming.digest(fn)
    chosen = naming.apply_globals(tree, fn, _G["named"], context.memory if context is not None else None)
    return tree, fn, {n: (int(k.split()[0], 16), k.split()[1]) for k, n in chosen.items()}, digest


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


def warm(lifter):
    """Fill the lifter's tables for the whole program here, before a pool forks: the workers share
    them instead of each making its own (dread.noreturn's: on a large firmware, much of its first
    lift)."""
    if hasattr(lifter, "never_returns"):
        lifter.never_returns("")


def _trees(work, program, context, lifter, names, named, digests, jobs):
    out = []
    warm(lifter)
    for made in in_pool(work, list(names), jobs, program=program, context=context, lifter=lifter, named=named):
        if made is not None:
            tree, fn, macros, digest = made
            if digests is not None:
                digests[fn.name] = digest
            out.append((tree, fn, macros))
    return out


def readable_trees(program, context, lifter, names, named=None, digests=None, jobs=None):
    """[(tree, function, macros)] of the functions' readable C after the stages, their memory at
    fixed addresses written as names (dread.naming apply_globals: named, the table's globals part, or
    the default names); macros: {name: (address, type)}. Functions that do not lift or are not the
    dialect are left out. digests, a dict: filled with each function's digest before the names (what
    the readable_names and readable_types tables are made for). Made in a pool (in_pool)."""
    return _trees(_readable_one, program, context, lifter, names, named, digests, jobs)


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
                             context.memory if context is not None else None, avoid=mine)
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


def chooser(names, context=None):
    """build.build's transform for the functions of names (the others stay lifted)."""
    names = set(names)

    def change(name, text):
        return transform(name, text, context=context) if name in names else None
    return change


def check(target, image, names=None, out=print, jobs=None):
    """The acceptance loop for readable C (see the module's docstring); returns the accepted names.
    names: functions of the lifted table to try (default: all of it)."""
    from dread import build, lift, overlay, regflow, runner
    from dread.program import Program
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = Context(target, image, program, flow)
    work = os.path.join(target.build, "readable-check", image.name)
    lifted = build.read_names(image.table("lifted"))
    # The structures (dread.shapes) from every lifted function's readable C, before any is checked:
    # the readable_types table, which the builds read too.
    from dread import shapes
    digests = {}
    trees = analysis_trees(program, context, lifter, lifted, named=context.globals, digests=digests)
    context.plan = shapes.plan(shapes.analyse(trees), trees, digests)
    context._struct_names = None
    shapes.store_plan(image, context.plan)
    out("dread readable --check: %d structures, pointers to them in %d functions (readable_types)" % (
        len(context.plan.structs), len(context.plan.typed)))
    by_system = build.system_names(image.table("lifted"))
    verdicts = {}                           # name -> (verdict, reason)
    candidates = []
    for name in names or lifted:
        if name not in lifted:
            verdicts[name] = ("not lifted", "not in the lifted table: dread lift --check first")
        elif name in by_system:
            verdicts[name] = ("lifted", "accepted by the system run: readable C is not checked there yet")
        else:
            fn = program.by_name.get(name)
            try:
                result = lifter.lift(fn) if fn is not None else None
            except lift.Refused as e:
                verdicts[name] = ("lifted", "no longer lifts: %s" % e)
                continue
            if result is None or transform(name, result.text, context=context) is None:
                verdicts[name] = ("lifted", "its lifted C is not the dialect dread.cparse reads")
                continue
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
                                  transform=chooser(accepted, context))
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
            return []
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

    def whole(names):
        return lift.whole_image(target, image, program, lifted, out, lifter, transform=chooser(names, context))
    found = whole(accepted) if runs and accepted else None
    baseline = whole([]) if found else None
    if baseline:
        # The image with lifted C alone differs already (the lift check reports it and keeps its
        # functions: FreeRTOS on RISC-V GCC -Os and Cortex-M clang -O2, where the rebuilt runs get
        # further before the limit than the original, by the clock of code both share). There the
        # scenarios cannot tell readable C from lifted C: whether a run agrees depends on how the
        # clock falls (the halves of RISC-V's 258 readable functions each agreed, all together not),
        # so the function-level runs decide, as for lifted C
        out("the whole image differs with lifted C alone (%s): the scenarios decide nothing here" % baseline)
        whole_note = "differs with lifted C alone: not decisive"
        found = None
    else:
        whole_note = "agrees"
    while found:
        # The function-level cases agreed, the scenarios do not: a readable function behaves otherwise
        # on an input its cases lack. Halves of the accepted ones until the guilty ones are found; they
        # go back to lifted C and the rest is tried again (RISC-V GCC -Os: the image printed nothing)
        out("the whole image: %s" % found)
        guilty = bisect(accepted, lambda names: whole(names) if names else None)
        drop(guilty, "the whole image differs with its readable C (found by halves): %s" % found)
        accepted = [n for n in accepted if n not in guilty]
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


def bisect(names, differs):
    """The names whose readable C makes differs(subset) true: halves tried until single ones are left
    (a few whole-image runs per guilty function). Two that differ only together are both returned."""
    if len(names) <= 1:
        return list(names)
    half = len(names) // 2
    left, right = names[:half], names[half:]
    a, b = differs(left), differs(right)
    if not a and not b:
        return list(names)              # (only together: both halves stay lifted)
    return (bisect(left, differs) if a else []) + (bisect(right, differs) if b else [])


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
        check(target, image, args.names or None)
        return 0
    if args.shapes:
        from dread import build, lift, regflow, shapes
        from dread.program import Program
        program = Program(target, image)
        flow = regflow.load(target, image, program)
        names = args.names or build.read_names(image.table("readable"))
        trees = readable_trees(program, Context(target, image, program, flow), lift.make_lifter(program, flow), names)
        s = shapes.analyse(trees)
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
