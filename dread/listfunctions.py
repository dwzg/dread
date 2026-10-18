"""dread functions: the functions of an image with their extents, as the program database sees them.

One line per function: entry, name, instruction bytes, isa, the address behind its last
instruction, and whether the walk found as many instruction bytes as the table says (a shortfall
means the code ranges and the function table disagree, or another function's entry cut it short).

    dread functions [--target DIR] [--image NAME] [NAME...]
"""
import argparse
import fnmatch
import sys

from dread import target as tgt
from dread.program import Program


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread functions", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("names", nargs="*", help="names or patterns (default: all)")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        prog = Program(target, image)
    except tgt.TargetError as e:
        print("dread functions: %s" % e, file=sys.stderr)
        return 1
    short = 0
    for fn in prog.functions:
        if args.names and not any(fnmatch.fnmatchcase(fn.name, p) for p in args.names):
            continue
        got = sum(prog.insn_size(a) for a in fn.insns)
        note = "" if got == fn.size else "  only %d of %d instruction bytes found" % (got, fn.size)
        short += bool(note)
        print("0x%08x  %-32s %6d  %s  end 0x%08x%s" % (fn.entry, fn.name, fn.size, fn.isa, prog.end(fn), note))
    if short:
        print("%d functions with fewer instruction bytes than the table says" % short, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
