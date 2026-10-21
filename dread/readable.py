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

Stages so far: none, the identity (parse and print again: dread.cparse's round trip).

Not yet: functions that never return and were accepted by the system run (they stay lifted).
"""
import argparse
import os
import sys

from dread import cparse
from dread import target as tgt

STAGES = []     # (name, function(tree: cparse.Function)): applied in order to the lifted function


def transform(name, text, stages=None):
    """The readable C of a lifted file (text) whose function is name, or None when it is not the
    lifters' dialect (it then stays lifted). The file's other functions (an MSP430 file's helpers)
    are printed as they were written."""
    try:
        tree = cparse.parse(text)
    except cparse.ParseError:
        return None
    fn = tree.function(name)
    if fn is None:
        return None
    for _, stage in (STAGES if stages is None else stages):
        stage(fn)
    others = [f.name for f in tree.items if isinstance(f, cparse.Function) and f.name != name]
    return cparse.unparse(tree, verbatim=others)


def chooser(names):
    """build.build's transform for the functions of names (the others stay lifted)."""
    names = set(names)

    def change(name, text):
        return transform(name, text) if name in names else None
    return change


def check(target, image, names=None, out=print, jobs=None):
    """The acceptance loop for readable C (see the module's docstring); returns the accepted names.
    names: functions of the lifted table to try (default: all of it)."""
    from dread import build, lift, overlay, regflow, runner
    from dread.program import Program
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    work = os.path.join(target.build, "readable-check", image.name)
    lifted = build.read_names(image.table("lifted"))
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
            if result is None or transform(name, result.text) is None:
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
                                  transform=chooser(accepted))
        except overlay.OverlayError as e:
            if e.function in accepted:
                drop([e.function], "the overlay: %s" % e)
                accepted = [n for n in accepted if n != e.function]
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
    whole = lift.whole_image(target, image, program, lifted, out, lifter, transform=chooser(accepted)) \
        if runs and accepted else None
    if whole:
        out("the whole image: %s" % whole)
    for name in accepted:
        verdicts[name] = ("readable", "")
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "verdicts.tsv"), "w") as f:
        f.write("function\tverdict\treason\n")
        f.writelines("%s\t%s\t%s\n" % (n, v, r.replace("\t", " ").replace("\n", " "))
                     for n, (v, r) in sorted(verdicts.items()))
    path = image.table("readable")
    kept = [] if whole else sorted(accepted)        # (an image that differs keeps none: not yet traced)
    with open(path, "w") as f:
        f.write("# Functions built from readable C (dread readable): made from their lifted C at every build;\n"
                "# their cases agree with the original and run every instruction it can reach, and the whole\n"
                "# image agrees. Written by dread readable --check; not edited by hand.\n")
        f.write("".join(n + "\n" for n in kept))
    out("dread readable --check: %d of %d lifted functions readable after %d rounds (%d stay lifted; the whole "
        "image %s), into %s" % (len(kept), len(lifted), rounds, len(lifted) - len(kept),
                                "differs" if whole else "agrees" if runs else "not run", os.path.relpath(path)))
    return kept


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread readable", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--check", action="store_true", help="test the readable C and write the readable table")
    parser.add_argument("--out", help="write NAME.c files there instead of printing")
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
    from dread import lift, regflow
    from dread.program import Program
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
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
            text = transform(name, lifter.lift(fn).text)
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
