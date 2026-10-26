"""dread callpair: names for another image's functions from the order in which paired functions call.

When code was compiled again (another compiler, other options, another instruction set) its bytes
differ but its calls mostly do not: main still calls uart_init, then uart_puts, then apply_config.
Starting from pairs known on both sides (functions with the same name, the pairs of dread samecode,
and --seed HERE=THERE), the calls of each pair are lined up in address order: runs that agree on
pairs already known anchor the alignment, and between two anchors calls are paired position by
position when both sides make the same number of them. A callee pairing becomes a pair when the
call sites vote for it and against nothing else; the new pairs line up their own calls, until
nothing changes.

Output: the pairs found beyond the seeds, here and there, with the votes; with --symbols, those
where this side has no name yet, as symbols.txt lines (f ADDRESS NAME). Proposals: read the code
before taking a name (sizes that differ a lot are suspect).

    dread callpair [--target DIR] [--image NAME] --other DIR [--other-image NAME] [--seed HERE=THERE]...
                   [--symbols]
"""
import argparse
import collections
import difflib
import sys

from dread import target as tgt
from dread.program import Program
from dread.samecode import UNNAMED, pairs as same_pairs


def calls_of(program, fn):
    """The entries fn calls or tail-calls directly, in address order, repeats included."""
    out = []
    for insn in program.instructions(fn):
        if insn is None:
            continue
        kind, target, _ = program.arch.flow(insn)
        if kind in ("call", "jump") and isinstance(target, int):
            t = program.code_address(target)
            if t in program.by_entry and t not in fn.insns:
                out.append(t)
    return out


def propagate(here, there, seeds, rounds=20):
    """{entry here: entry there} grown from `seeds` by call order; with the votes behind each new pair."""
    pairs = dict(seeds)
    votes_of = {}
    for _ in range(rounds):
        votes = collections.Counter()
        taken = set(pairs.values())
        for a, b in list(pairs.items()):
            fa, fb = here.by_entry.get(a), there.by_entry.get(b)
            if fa is None or fb is None:
                continue
            sa, sb = calls_of(here, fa), calls_of(there, fb)
            ta = [("p", pairs[x]) if x in pairs else ("a", x) for x in sa]
            tb = [("p", y) if y in taken else ("b", y) for y in sb]
            for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
                if op == "replace" and i2 - i1 == j2 - j1:
                    for x, y in zip(sa[i1:i2], sb[j1:j2]):
                        if x not in pairs and y not in taken:
                            votes[(x, y)] += 1
        best = {}
        for (x, y), v in votes.items():
            rivals = [w for (x2, y2), w in votes.items() if (x2 == x) != (y2 == y)]
            if v > max(rivals, default=0):
                best.setdefault(x, (y, v))
        new = {}
        used = set()
        for x, (y, v) in sorted(best.items(), key=lambda kv: -kv[1][1]):
            if y not in used:
                new[x] = y
                used.add(y)
                votes_of[x] = v
        if not new:
            break
        pairs.update(new)
    return pairs, votes_of


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread callpair", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--other", required=True, help="the other target directory (the named side)")
    parser.add_argument("--other-image", help="its image (default: its first)")
    parser.add_argument("--seed", action="append", default=[], help="HERE=THERE: names or addresses known to pair")
    parser.add_argument("--symbols", action="store_true", help="only names this side lacks, as symbols.txt lines")
    parser.add_argument("--json", help="write the pairs here as well (docs/formats.md)")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        other = tgt.Target.load(args.other)
        other_image = other.image(args.other_image)
    except tgt.TargetError as e:
        print("dread callpair: %s" % e, file=sys.stderr)
        return 1
    here, there = Program(target, image), Program(other, other_image)

    def entry(program, text):
        fn = program.by_name.get(text)
        if fn is None:
            try:
                fn = program.function_at(int(text, 0))
            except ValueError:
                fn = None
        if fn is None:
            raise tgt.TargetError("no function %s" % text)
        return fn.entry
    try:
        seeds = {here.by_name[n].entry: there.by_name[n].entry for n in here.by_name
                 if n in there.by_name and not UNNAMED.match(n)}
        seeds.update({a.entry: b.entry for a, b in same_pairs(here, there)})
        for item in args.seed:
            a, _, b = item.partition("=")
            seeds[entry(here, a)] = entry(there, b)
    except tgt.TargetError as e:
        print("dread callpair: %s" % e, file=sys.stderr)
        return 1
    found, votes = propagate(here, there, seeds)
    new = sorted((a, b) for a, b in found.items() if a not in seeds)
    for a, b in new:
        fa, fb = here.by_entry[a], there.by_entry[b]
        if args.symbols:
            if UNNAMED.match(fa.name) and not UNNAMED.match(fb.name):
                print("f 0x%x %s" % (a, fb.name))
        else:
            print("0x%08x %-28s %-28s %d votes; %d and %d instructions" % (
                a, fa.name, fb.name, votes.get(a, 0), len(fa.insns), len(fb.insns)))
    if not args.symbols:
        print("dread callpair: %d seeds, %d pairs found by call order" % (len(seeds), len(new)))
    if args.json:
        from dread.samecode import write_json
        write_json(args.json, {"here": {"target": target.root, "image": image.name},
                               "other": {"target": other.root, "image": other_image.name},
                               "seeds": len(seeds),
                               "pairs": [{"entry": a, "here": here.by_entry[a].name, "there": there.by_entry[b].name,
                                          "votes": votes.get(a, 0), "instructions_here": len(here.by_entry[a].insns),
                                          "instructions_there": len(there.by_entry[b].insns),
                                          "new_name": bool(UNNAMED.match(here.by_entry[a].name) and
                                                           not UNNAMED.match(there.by_entry[b].name))}
                                         for a, b in new]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
