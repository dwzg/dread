"""dread import-elf: an image's tables from an ELF that still has its symbols.

For firmware that comes with symbols, and for test images built from source (gizmo), the ELF knows
what an analysis otherwise has to work out: where functions start, how many instruction bytes they
have, which bytes are code of which instruction set and which are data. This tool writes the
image's functions.tsv, code_ranges.tsv and symbols.txt from it, in the formats Ghidra's export uses
(docs/formats.md). It prints the startup copies for dread.toml: sections whose run address differs
from their load address, and zeroed sections. With --payload it also writes the raw image.

Names that are not C identifiers (GCC's clones such as foo.part.0) get their other characters
replaced by "_"; a name that occurs twice (two static functions in different files) gets the
address appended.

--unnamed writes FUN_xxxxxxxx for every function except those named with --keep. That gives a
stripped variant of the same image, to test the tools that help with naming.

    dread import-elf ELF [--target DIR] [--image NAME] [--payload FILE] [--unnamed] [--keep A,B]
"""
import argparse
import os
import re
import sys

from dread import target as tgt
from dread.elf import STT_FUNC, STT_OBJECT, Elf, ElfError

IDENT = re.compile(r"[^A-Za-z0-9_]")


def code_intervals(elf):
    """[(start, end, kind)] of every allocated section, kind "a32", "t32" or "data", from the
    mapping symbols; a section without mapping symbols is all code of its section flag's kind
    when executable (a32 assumed), data otherwise."""
    mapping = elf.mapping()
    out = []
    for index, s in enumerate(elf.sections):
        if not s.alloc or s.nobits or not s.size:
            continue
        marks = [(a, k) for a, k in mapping.get(index, []) if s.addr <= a < s.addr + s.size]
        if not marks:
            out.append((s.addr, s.addr + s.size, "a32" if s.code else "data"))
            continue
        if marks[0][0] != s.addr:
            out.append((s.addr, marks[0][0], "data"))
        for (a, k), nxt in zip(marks, marks[1:] + [(s.addr + s.size, None)]):
            if nxt[0] > a:
                out.append((a, nxt[0], k))
    out.sort()
    merged = []
    for start, end, kind in out:                # adjacent intervals of one kind become one
        if merged and merged[-1][2] == kind and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end, kind)
        else:
            merged.append((start, end, kind))
    return merged


PADDING = {"t32": (b"\x00\xbf", b"\xc0\x46"), "a32": (b"\x00\x00\xa0\xe1", b"\x00\xf0\x20\xe3")}


def padding(elf, start, end, isa):
    """How many bytes at the end of a function's last run of instructions [start, end) are
    alignment padding in front of a literal pool: a nop (Thumb: nop or mov r8, r8; ARM: mov r0, r0
    or nop) that ends on a word boundary right behind an instruction that does not fall through.
    GCC counts it in the symbol's size, but it never runs, and a test that has to run every
    instruction would never be complete."""
    from dread.arch.arm import Arm
    nop = 2 if isa == "t32" else 4
    if end % 4 or end - start <= nop or elf.read(end - nop, nop) not in PADDING[isa]:
        return 0
    arm = Arm(isa=isa, profile="m" if isa == "t32" else "a")
    insns = list(arm.disassembler(isa).disasm(elf.read(start, end - nop - start), start))
    if not insns or insns[-1].address + insns[-1].size != end - nop:
        return 0
    last = insns[-1]
    if arm.is_conditional(last):
        return 0
    text = last.mnemonic.split(".")[0]
    ends = (text in ("b", "bx") or (text == "pop" and "pc" in last.op_str)
            or (text in ("ldr", "mov", "ldm", "ldmia") and last.op_str.startswith("pc")))
    return nop if ends else 0


def tables(elf, unnamed=False, keep=()):
    """(functions, code_ranges, symbols): functions [(entry, name, insn_bytes)], code ranges
    [(start, end, isa)], symbols [(kind, address, name)]."""
    intervals = code_intervals(elf)
    code = [(a, b, k) for a, b, k in intervals if k != "data"]

    def insn_bytes(lo, hi):
        return sum(max(0, min(hi, b) - max(lo, a)) for a, b, _ in code)

    funcs = {}
    for sym in elf.symbols:
        if sym.kind != STT_FUNC or not sym.size or not sym.name:
            continue
        entry = sym.value & ~1
        if entry in funcs:                      # an alias of a function already seen
            continue
        runs = [(max(a, entry), min(b, entry + sym.size), k) for a, b, k in code
                if a < entry + sym.size and entry < b]
        pad = padding(elf, runs[-1][0], runs[-1][1], runs[-1][2]) if runs else 0
        funcs[entry] = (IDENT.sub("_", sym.name), insn_bytes(entry, entry + sym.size) - pad)
    names = {}
    for entry, (name, _) in funcs.items():
        names.setdefault(name, []).append(entry)
    functions = []
    for entry in sorted(funcs):
        name, size = funcs[entry]
        if len(names[name]) > 1:
            name = "%s_%x" % (name, entry)
        if unnamed and name not in keep:
            name = "FUN_%08x" % entry
        functions.append((entry, name, size))
    data = []
    seen = set()
    for sym in elf.symbols:
        if sym.kind == STT_OBJECT and sym.size and sym.name and sym.value not in seen:
            seen.add(sym.value)
            data.append(("d", sym.value, IDENT.sub("_", sym.name)))
    symbols = [("f", entry, name) for entry, name, _ in functions] + sorted(data, key=lambda d: d[1])
    if unnamed:
        symbols = [s for s in symbols if s[0] == "f" and not s[2].startswith("FUN_")]
    return functions, code, symbols


def startup(elf):
    """The copies a startup makes, read from the sections: [(kind, run, size, stored)], kind
    "code" or "data" for a section loaded at one address and run at another, "zero" for an
    allocated section without contents (stored None)."""
    out = []
    for s in elf.sections:
        if not s.alloc or not s.size:
            continue
        if s.nobits:
            out.append(("zero", s.addr, s.size, None))
        elif s.lma != s.addr:
            out.append(("code" if s.code else "data", s.addr, s.size, s.lma))
    return out


def write_tables(image, functions, code, symbols, header="dread import-elf"):
    os.makedirs(image.config, exist_ok=True)
    with open(image.table("functions"), "w") as f:
        f.write("address\tname\tbytes\n")
        for entry, name, size in functions:
            f.write("0x%x\t%s\t%d\n" % (entry, name, size))
    with open(image.table("code_ranges"), "w") as f:
        f.write("# instruction runs: start, end (exclusive), isa; written by %s\n" % header)
        for start, end, isa in code:
            f.write("0x%x\t0x%x\t%s\n" % (start, end, isa))
    with open(image.table("symbols"), "w") as f:
        f.write("# f/d ADDRESS NAME; written by %s\n" % header)
        for kind, address, name in symbols:
            f.write("%s 0x%x %s\n" % (kind, address, name))


def startup_toml(copies):
    lines = ["startup = ["]
    for kind, run, size, stored in copies:
        if stored is None:
            lines.append('    {kind = "%s", run = 0x%x, size = 0x%x},' % (kind, run, size))
        else:
            lines.append('    {kind = "%s", run = 0x%x, size = 0x%x, stored = 0x%x},' % (kind, run, size, stored))
    lines.append("]")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread import-elf", description=__doc__.split("\n\n")[0])
    parser.add_argument("elf")
    tgt.add_arguments(parser)
    parser.add_argument("--payload", metavar="FILE", help="also write the raw image (from the image's base)")
    parser.add_argument("--unnamed", action="store_true", help="FUN_xxxxxxxx for every function")
    parser.add_argument("--keep", default="", help="with --unnamed: names to keep, comma-separated")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        elf = Elf(args.elf)
    except (tgt.TargetError, ElfError, OSError) as e:
        print("dread import-elf: %s" % e, file=sys.stderr)
        return 1
    functions, code, symbols = tables(elf, args.unnamed, set(filter(None, args.keep.split(","))))
    write_tables(image, functions, code, symbols)
    print("%d functions, %d code ranges, %d symbols written to %s"
          % (len(functions), len(code), len(symbols), os.path.relpath(image.config)))
    if args.payload:
        data, base = elf.payload(image.base, image.erased)
        with open(args.payload, "wb") as f:
            f.write(data)
        print("%d bytes from 0x%x written to %s" % (len(data), base, args.payload))
    copies = startup(elf)
    if copies:
        print("startup copies for [image.%s] in dread.toml:\n%s" % (image.name, startup_toml(copies)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
