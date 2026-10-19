"""dread test: every rewritten function against the original, in the emulator.

Each case calls one function in two emulated machines, one per image (the original and the image
with the C linked in, from `dread recon`), with identical inputs, and the results must agree
(dread.cases says what is compared). The rebuilt image may run REBUILT_BUDGET times a case's
instruction limit (C is slower than the original; dread integration does the same), except in a
case marked "hang". A function the overlay built from C that has no case is
reported UNTESTED.

Besides what dread.cases lists, the scratch registers the function's callers read after the call
(dread.regflow: a second result, or a register a compiler with -fipa-ra kept a value in across the
call) are compared at the return. A difference names the reader, says whether it reads on every
path or on some only, and whether the original leaves the register unchanged. A register whose
difference cannot matter is left out with an argument in kept.tsv beside the cases directory
(function, register, argument; tab-separated), and listed with it under the function's result.

Coverage: while the original runs, the instructions executed inside the function under test are
recorded. A function whose cases leave instructions of the original unexecuted is reported
UNCOVERED and fails the run, unless those addresses are declared with unreachable() and a reason;
a declared address that does execute fails the function (the declaration is wrong). Coverage is
counted per executed block: a conditional instruction counts as run whether its condition held or
not, so a function made of conditional instructions without branches is covered by one case. The
mutation check is what shows whether its cases tell both outcomes apart.

Output, one block per function, in this form (other tools read it):

    ok   NAME                       n/m cases agree
    FAIL NAME [label]               followed by the differences, indented by five spaces
    UNCOVERED NAME: a of b instructions of the original never ran: ADDRESS ...
    REFUSED NAME [label]: ...       a written case that reads code as data or writes into the image,
                                    or a case whose two images differ where the original's own
                                    outcome changes when its stack starts STACK_SHIFT bytes lower
                                    (it took a stack address for a value: C's frames lie otherwise),
                                    or where every difference changes in the original too when what
                                    lies below the stack pointer at its function entries changes
                                    (it read stale stack data, an uninitialised local: what earlier
                                    frames left there, which C, laying out its frames and saving
                                    other values in them, leaves otherwise; stale_explains); such a
                                    case is not compared and runs nothing for coverage
    UNTESTED NAME

Exit status 1 when anything failed, is uncovered or untested. Functions run in parallel worker
processes (fork; Linux and macOS), slowest of the previous run first. With names given, only those
run and no summary file is written; DREAD_ALL_MISSED=1 lists every missed address.

    dread test [--target DIR] [--image NAME] [--recon PAYLOAD] [--keep-failing] [NAME...]
"""
import argparse
import multiprocessing
import os
import sys

from dread import target as tgt
from dread.build import recon_dir
from dread.cases import Suite
from dread.emu import EmuError, Layout, Machine
from dread.overlay import load_placement
from dread.program import Program
from dread.util.polite import die_with_parent, polite, workers
from dread.util.poolorder import longest_first

RUN = {}        # what the workers need; filled before they are forked
STACK_SHIFT = 0x40      # bytes the stack moves for the check that a case's outcome depends on it
STALE_SPAN = 0x400      # bytes below the stack pointer the stale-data check fills at every function entry
STALE_FILLS = (0xA5, 0x5A)  # the bytes it fills them with, one run each
REBUILT_BUDGET = 10     # the rebuilt image may take this many times a case's limit (C runs slower than
                        # the original: lifted C's loops take more instructions); a hang case gets the same


def generated_cases(name, fn):
    """The generated, kept and written cases of a function with auto(): [(label, addr, setup,
    args, limit, case dict or None)]."""
    from dread import autocase
    return autocase.cases_for(RUN, name, fn)


def kept_for(name):
    """(registers, why, argued) for a function: the scratch registers its callers read after the
    call (dread.regflow), to compare at the return besides the result; argued: those left out by
    an argument in the image's kept.tsv, {register: argument}."""
    flow = RUN.get("regflow")
    if flow is None:
        return (), {}, {}
    result = RUN["target"].abi.result[:1]
    arguments = RUN.get("kept_argued", {})
    regs = [r for r in flow.kept_regs(name) if r not in result]
    argued = {r: arguments[(name, r)] for r in regs if (name, r) in arguments}
    regs = tuple(r for r in regs if r not in argued)
    return regs, {r: flow.why(name, r) for r in regs}, argued


def stack_moves_it(name, addr, args, limit, setup, kept, why, first):
    """Whether the original's outcome changes when its stack starts STACK_SHIFT bytes lower: then
    the case made it take a stack address for a value (a seed of a random generator, a pointer
    past a local array by an index the input made too large), which C, whose frames lie otherwise,
    cannot reproduce. Writes into the room for stack arguments are left out (that room moves into
    the stack, whose writes are not compared)."""
    from dread.emu import ARGS_ROOM, Result
    lay = RUN["layout"]
    m = Machine(lay, RUN["original"], isa_of=RUN["isa_of"])
    m.plan, m.replay, m.function = [], False, name
    m.kept, m.kept_why = kept, why
    m.stack_shift = STACK_SHIFT
    try:
        setup(m)
        again = m.call(addr, *args, max_instructions=limit)
    except Exception:                       # it fails or hangs only there
        return True
    lo, hi = lay.stack_top, lay.stack_top + ARGS_ROOM

    def outside(r):
        return Result(r.ret, {a: v for a, v in r.writes.items() if not lo <= a < hi}, r.calls, r.saved,
                      r.layout, r.kept, r.why)
    return bool(outside(first).diff(outside(again)))


def stale_explains(name, addr, args, limit, setup, kept, why, first, rebuilt):
    """Whether stale stack data the original read explains every difference between the images: the
    original runs again with STALE_SPAN bytes below the stack pointer filled at every function entry
    (once with each of STALE_FILLS), so that an uninitialised local, of the function or of a callee,
    reads the fill instead of what earlier frames left; when every output in which the images differ
    changes there too, C cannot be held to it (it lays out its frames otherwise and saves other
    values in them; FreeRTOS's timer commands carry a union member nobody set into the timer queue).
    Returns a short text of those outputs, or None. A difference the fills do not move, or an
    original that fails with a fill, keeps the case compared."""
    images = first.differences(rebuilt)
    if not images:
        return None
    lay = RUN["layout"]
    entries = frozenset(e & ~1 for e in Machine.entries.get((lay.target.file, lay.image.name), ()))
    moved = set()
    for fill in STALE_FILLS:
        m = Machine(lay, RUN["original"], isa_of=RUN["isa_of"])
        m.plan, m.replay, m.function = [], False, name
        m.kept, m.kept_why = kept, why
        m.stale_fill = (fill, STALE_SPAN, entries | {addr & ~1})
        try:
            setup(m)
            again = m.call(addr, *args, max_instructions=limit)
        except Exception:
            return None
        moved |= first.differences(again)
    if not images <= moved:
        return None
    memory = sorted(k[1] for k in images if isinstance(k, tuple) and k[0] == "memory")
    runs = []
    for a in memory:
        if runs and a == runs[-1][1] + 1:
            runs[-1][1] = a
        else:
            runs.append([a, a])
    shown = ["memory " + ", ".join("0x%x" % lo if lo == hi else "0x%x-0x%x" % (lo, hi) for lo, hi in runs[:4])
             + (", ..." if len(runs) > 4 else "")] if runs else []
    shown += sorted(k if isinstance(k, str) else "%s %s" % k for k in images if not (isinstance(k, tuple)
                                                                                    and k[0] == "memory"))
    return "; ".join(shown)


def run_function(name):
    """All cases of one function: (name, cases, failed, output lines, the instructions of the original
    no case ran)."""
    suite, prog, layout = RUN["suite"], RUN["program"], RUN["layout"]
    fn = prog.by_name.get(name)
    words = fn.insns if fn else []
    cases = [c + (None,) for c in suite.cases.get(name, [])]
    lines = []
    if name in suite.auto:
        try:
            cases += generated_cases(name, fn)
        except Exception as e:             # the generator failed: written cases still count
            if not cases:
                return name, 0, 1, ["FAIL %s [case generation]" % name, "     %s" % e], []
            lines.append("FAIL %s [case generation]: %s" % (name, e))
    image = RUN["image"]
    kept, why, argued = kept_for(name)
    bad, covered, failing, refused = 0, set(), [], 0
    for label, addr, setup, args, limit, generated in cases:
        out, plan, case_covered = [], [], set()
        for which, payload in (("original", RUN["original"]), ("rebuilt", RUN["recon"])):
            m = Machine(layout, payload, RUN["aliases"] if which == "rebuilt" else None, isa_of=RUN["isa_of"])
            m.plan, m.replay = plan, which == "rebuilt"
            m.function = name
            m.kept, m.kept_why = kept, why
            try:
                setup(m)
            except Exception as e:
                out = [EmuError("the case's setup failed: %s: %s" % (type(e).__name__, e)), None]
                break
            if (addr & ~1) in m.stubs:
                out = [EmuError("the case stubs the function it is meant to test"), None]
                break
            if which == "original" and words and addr == fn.entry:
                m.cover(words[0], prog.end(fn))
            written = label.startswith("written")
            if which == "original" and written:
                m.trace_reads(image.base, image.end, RUN["code"], RUN["owners"])
            # a case that ends at a call (a loop case's stub call, a stop) not at the limit: the C may take
            # more instructions to get there
            loop_case = bool(generated and generated.get("hang") and (generated.get("stub_calls") or generated.get("stops")))
            budget = limit if which == "original" or (getattr(m, "may_hang", False) and not loop_case) \
                else limit * REBUILT_BUDGET
            try:
                out.append(m.call(addr, *args, max_instructions=budget))
            except Exception as e:          # EmuError, or anything a case's own code raises
                out.append(e if isinstance(e, EmuError) else EmuError("%s: %s" % (type(e).__name__, e)))
            if which == "original" and written and (m.code_reads or m.wrote_into(image.base, prog.writable_end())):
                out = None
                break
            if which == "original":
                case_covered = m.covered_insns(words)
        if out is None:
            refused += 1
            lines.append("REFUSED %s [%s]: the input loads instructions as data or stores into the image; "
                         "remove it" % (name, label))
            continue
        depends = None
        regs = RUN["suite"].caller_regs.get(name)
        if regs and out and not isinstance(out[0], Exception):
            m = Machine(layout, RUN["original"], isa_of=RUN["isa_of"])
            m.plan, m.replay, m.function = [], False, name
            setup(m)
            for r in regs:
                m.reg("r%d" % r, 0x3C5A0F00 + 0x13 * r)
            try:
                again = m.call(addr, *args, max_instructions=limit)
                if (again.ret, again.ranges, again.calls) != (out[0].ret, out[0].ranges, out[0].calls):
                    depends = "the original's result depends on what its caller left in %s" % \
                        " or ".join("r%d" % r for r in regs)
            except EmuError as e:
                depends = "the original fails with other values in %s: %s" % (" or ".join("r%d" % r for r in regs), e)
        if len(out) == 2 and out[1] is None:
            diff = [str(out[0])]
        elif isinstance(out[0], Exception) or isinstance(out[1], Exception):
            diff = ["original: %s" % out[0] if isinstance(out[0], Exception) else "original ran",
                    "rebuilt: %s" % out[1] if isinstance(out[1], Exception) else "rebuilt ran"]
        else:
            diff = out[0].diff(out[1])
        if depends:
            diff = list(diff) + [depends]
        if diff and not depends and not isinstance(out[0], Exception) and not isinstance(out[1], Exception) \
                and stack_moves_it(name, addr, args, limit, setup, kept, why, out[0]):
            refused += 1
            lines.append("REFUSED %s [%s]: the original's outcome depends on where the stack lies (it differs "
                         "with the stack %d bytes lower): a stack address taken for a value; not compared"
                         % (name, label, STACK_SHIFT))
            continue
        stale = stale_explains(name, addr, args, limit, setup, kept, why, out[0], out[1]) \
            if diff and not depends and not isinstance(out[0], Exception) and not isinstance(out[1], Exception) \
            else None
        if stale:
            refused += 1
            lines.append("REFUSED %s [%s]: the original read stale stack data (an uninitialised local): every "
                         "difference between the images (%s) changes in the original too when what lies below "
                         "the stack pointer at its function entries changes; not compared" % (name, label, stale))
            continue
        covered |= case_covered
        if diff:
            bad += 1
            if generated is not None:
                failing.append(generated)
            lines.append("FAIL %s [%s]" % (name, label))
            lines += ["     " + d for d in diff]
    if failing and RUN["keep_failing"]:
        from dread import autocase
        autocase.kept_add(RUN["image"], name, failing, "stubbed")
    declared = suite.unreachable.get(name, set())
    missed = sorted(set(words) - covered - declared)
    wrong = sorted(covered & declared)
    if wrong:
        bad += 1
        lines.append("FAIL %s [unreachable]: %d instructions declared unreachable did run: %s"
                     % (name, len(wrong), " ".join("0x%x" % a for a in wrong[:12])))
    if missed:
        shown = missed if os.environ.get("DREAD_ALL_MISSED") else missed[:12]
        lines.append("UNCOVERED %s: %d of %d instructions of the original never ran: %s"
                     % (name, len(missed), len(words), " ".join("0x%x" % a for a in shown)))
    total = len(cases) - refused
    lines.append("%-4s %-26s %d/%d cases agree%s" % ("ok" if not bad else "FAIL", name, total - bad, total,
                                                    ", %d refused" % refused if refused else ""))
    for reg, argument in sorted(argued.items()):
        lines.append("     %s at the return not compared (kept.tsv): %s" % (reg, argument))
    return name, len(cases), bad, lines, missed


def prepare(target, image, recon_path, keep_failing=False):
    """Fill RUN for a run over one image; returns the suite."""
    prog = Program(target, image)
    layout = Layout(target, image)
    suite = Suite(target, image, prog, layout).load()
    with open(recon_path, "rb") as f:
        recon = f.read()
    aliases, built = load_placement(os.path.join(os.path.dirname(os.path.abspath(recon_path)), "placement.json"))
    from dread.build import read_names
    here = os.path.dirname(os.path.abspath(recon_path))
    lifted_info = {}
    if os.path.exists(os.path.join(here, "lifted.json")):
        import json
        with open(os.path.join(here, "lifted.json")) as f:
            lifted_info = json.load(f)
    system = set(lifted_info.get("system", ()))  # never return: dread sysrun's scenarios test them
    loops = set(lifted_info.get("loop", ()))    # never return: loop cases test them (dread.autocase)
    system -= loops
    for name in read_names(os.path.join(here, "lifted.txt")):
        if name in loops:
            suite.auto.setdefault(name, {"loop": True})
        elif name not in system:
            suite.auto.setdefault(name, {})     # lifted C is tested with generated cases, callees real
    for name, addrs in lifted_info.get("unreached", {}).items():
        # no path from the entry reaches them (dread lift); one that runs fails the function
        suite.unreachable.setdefault(name, set()).update(addrs)
    Machine.entries[(target.file, image.name)] = {fn.entry for fn in prog.functions}
    from dread import regflow
    flow = regflow.load(target, image, prog)
    kept_argued = regflow.load_argued(image)
    RUN.clear()
    RUN.update(system=system)
    RUN.update(target=target, image=image, program=prog, layout=layout, suite=suite, original=image.payload(),
               recon=recon, aliases=aliases, built=built, keep_failing=keep_failing,
               isa_of=prog.isa_at, code=prog.instruction_halves(), owners=prog.extent_owners(), regflow=flow,
               kept_argued=kept_argued)
    return suite


def run(names, only=False, out=print, jobs=None):
    """Run the functions `names` (RUN prepared); returns (failed, uncovered, results). RUN["missed"]
    keeps, per function, the instructions of the original no case ran."""
    failed, uncovered, results = 0, [], []
    RUN["missed"] = {}
    times = os.path.join(RUN["target"].build, "times", "tests-%s%s.json" % (RUN["image"].name, "-some" if only else ""))
    with multiprocessing.get_context("fork").Pool(jobs or workers(), initializer=die_with_parent) as pool:
        for name, count, bad, lines, missed in longest_first(pool, run_function, names, str, times):
            out("\n".join(lines))
            failed += bad
            results.append((name, count, bad))
            RUN["missed"][name] = missed
            if missed:
                uncovered.append(name)
    return failed, uncovered, results


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread test", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--recon", help="the rebuilt image (default: BUILD/recon/IMAGE/payload.bin)")
    parser.add_argument("--keep-failing", action="store_true", help="keep failing generated cases")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)
    polite()
    try:
        target, image = tgt.from_args(args)
        recon_path = args.recon or os.path.join(recon_dir(target, image), "payload.bin")
        if not os.path.exists(recon_path):
            raise tgt.TargetError("no rebuilt image at %s: run `dread recon` first" % recon_path)
        if not args.recon and os.path.isdir(image.src):
            built = os.path.getmtime(recon_path)
            newer = [os.path.join(d, f) for d, _, fs in os.walk(image.src) for f in fs
                     if f.endswith((".c", ".h")) and os.path.getmtime(os.path.join(d, f)) > built]
            if newer:
                raise tgt.TargetError("%s is older than %s: run `dread recon`" % (recon_path, ", ".join(sorted(newer))))
        suite = prepare(target, image, recon_path, args.keep_failing)
    except (tgt.TargetError, EmuError) as e:
        print("dread test: %s" % e, file=sys.stderr)
        return 1
    names = [n for n in list(suite.cases) + [n for n in suite.auto if n not in suite.cases]
             if not args.names or n in args.names]
    failed, uncovered, results = run(names, only=bool(args.names))
    if args.names:
        missing = [n for n in args.names if n not in names]
        for n in missing:
            print("UNTESTED %s" % n)
        return 1 if failed or missing or uncovered else 0
    with open(os.path.join(os.path.dirname(os.path.abspath(recon_path)), "tests.tsv"), "w") as f:
        f.write("function\tcases\tfailed\tmissed\n")
        f.writelines("%s\t%d\t%d\t%s\n" % (n, c, b, " ".join("0x%x" % a for a in RUN["missed"].get(n, ())))
                     for n, c, b in results)
    system = RUN.get("system", set())
    for fn in sorted(system & set(RUN["built"])):
        print("SYSTEM %s: it never returns or is an exception handler; the system run's scenarios test it"
              " (dread sysrun)" % fn)
    untested = [fn for fn in RUN["built"] if fn not in names and fn not in system]
    for fn in untested:
        print("UNTESTED %s" % fn)
    cases = sum(c for _, c, _ in results)
    print("dread test: %d functions, %d cases, %d failed, %d uncovered, %d untested"
          % (len(results), cases, failed, len(uncovered), len(untested)))
    return 1 if failed or untested or uncovered else 0


if __name__ == "__main__":
    sys.exit(main())
