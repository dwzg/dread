"""dread functions: the functions of an image with their extents, as the program database sees them.

One line per function: entry, name, instruction bytes, isa, the address behind its last
instruction, and whether the walk found as many instruction bytes as the table says (a shortfall
means the code ranges and the function table disagree, or another function's entry cut it short).
A C++ name is shown demangled behind the line (dread.cxx); the patterns match the names as the
tables keep them (mangled).

With --families, the instances of each function template instead (dread.cxx.families): one
template, its instances with their entries.

    dread functions [--target DIR] [--image NAME] [--families] [NAME...]
"""
import argparse
import fnmatch
import sys

from dread import cxx
from dread import target as tgt
from dread.program import Program


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread functions", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("names", nargs="*", help="names or patterns (default: all)")
    parser.add_argument("--families", action="store_true", help="the instances of each function template")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        prog = Program(target, image)
    except tgt.TargetError as e:
        print("dread functions: %s" % e, file=sys.stderr)
        return 1
    chosen = [fn for fn in prog.functions
              if not args.names or any(fnmatch.fnmatchcase(fn.name, p) for p in args.names)]
    shown = cxx.demangle(fn.name for fn in chosen)
    if args.families:
        by_name = {fn.name: fn for fn in chosen}
        for template, names in cxx.families([fn.name for fn in chosen]):
            print(template)
            for name in names:
                print("    0x%08x  %-32s %s" % (by_name[name].entry, name, shown[name]))
        return 0
    short = 0
    for fn in chosen:
        got = sum(prog.insn_size(a) for a in fn.insns)
        note = "" if got == fn.size else "  only %d of %d instruction bytes found" % (got, fn.size)
        short += bool(note)
        pretty = "  " + shown[fn.name] if shown[fn.name] != fn.name else ""
        print("0x%08x  %-32s %6d  %s  end 0x%08x%s%s" % (fn.entry, fn.name, fn.size, fn.isa, prog.end(fn), note,
                                                         pretty))
    if short:
        print("%d functions with fewer instruction bytes than the table says" % short, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
