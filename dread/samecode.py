"""dread samecode: functions of one image that are the same code as functions of another.

The same library routine, startup code or driver often sits in two images (two firmware versions,
a boot loader and the firmware it loads) at different addresses. This compares every function of
this image with every function of another (--other: a target directory, and --other-image when it has
several) by its code made independent of where it lies: the instructions with their operands, but a
branch inside the function as its distance, a call or a branch elsewhere as "call", and a literal
or an adr that points into the image as "address" (a literal that does not is compared as the value it
is). Functions whose code is the same and unique on both sides are pairs.

Output, one line per pair: the address and name here, the name there, the instructions. With
--symbols, only the pairs where this side has no name yet (FUN_..., unnamed), in the form of
symbols.txt lines (f ADDRESS NAME), to read and then add.

    dread samecode [--target DIR] [--image NAME] --other DIR [--other-image NAME] [--symbols] [--min N]
"""
import argparse
import collections
import hashlib
import re
import sys

from dread import target as tgt
from dread.program import Program

UNNAMED = re.compile(r"^(FUN_|fun_|sub_|unnamed)", re.I)


def shape(program, fn):
    """A digest of the function's code, independent of where it lies (see the module)."""
    arch = program.arch
    h = hashlib.sha1()
    inside = set(fn.insns)
    for insn in program.instructions(fn):
        if insn is None:
            h.update(b"?")
            continue
        kind, target, _ = arch.flow(insn)
        isa = program.isa_at(insn.address)
        text = insn.mnemonic
        if kind in ("call", "jump") and isinstance(target, int):
            t = target & ~1
            text += " L%+d" % (t - insn.address) if t in inside else " call"
        else:
            lit = arch.literal(insn, isa)
            at = arch.pc_address(insn, isa)
            if lit is not None:
                value = program.word(lit[0]) if lit[1] == 4 and program.in_image(lit[0], 4) else None
                shown = "address" if value is None or program.in_image(value & ~1) else "=0x%x" % value
                text += " %s, %s" % (insn.op_str.split(",")[0], shown)
            elif at is not None:
                text += " %s, address" % insn.op_str.split(",")[0]
            else:
                text += " " + insn.op_str
        h.update(text.encode() + b"\n")
    return h.hexdigest()


def pairs(here, there, minimum=3):
    """[(function here, function there)] whose shapes are equal and unique on both sides."""
    def index(program):
        out = collections.defaultdict(list)
        for fn in program.functions:
            if len(fn.insns) >= minimum:
                out[shape(program, fn)].append(fn)
        return out
    a, b = index(here), index(there)
    return sorted(((x[0], b[k][0]) for k, x in a.items() if len(x) == 1 and len(b.get(k, ())) == 1),
                  key=lambda p: p[0].entry)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread samecode", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--other", required=True, help="the other target directory")
    parser.add_argument("--other-image", help="its image (default: its first)")
    parser.add_argument("--symbols", action="store_true", help="only names this side lacks, as symbols.txt lines")
    parser.add_argument("--min", type=int, default=3, help="instructions a function needs at least (default 3)")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        other = tgt.Target.load(args.other)
        other_image = other.image(args.other_image)
    except tgt.TargetError as e:
        print("dread samecode: %s" % e, file=sys.stderr)
        return 1
    here, there = Program(target, image), Program(other, other_image)
    found = pairs(here, there, args.min)
    for a, b in found:
        if args.symbols:
            if UNNAMED.match(a.name) and not UNNAMED.match(b.name):
                print("f 0x%x %s" % (a.entry, b.name))
        else:
            print("0x%08x %-28s %-28s %d instructions" % (a.entry, a.name, b.name, len(a.insns)))
    if not args.symbols:
        print("dread samecode: %d pairs of %d functions here and %d there" % (
            len(found), len(here.functions), len(there.functions)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
