"""dread ghidra: analyse an image with Ghidra (headless) and write its function and code tables.

Imports the image's raw bytes at its base with Ghidra's binary loader and the language of the
target's [arch]. Before the analysis, DreadSetup.java sets the image up from a directive file this
module writes from the target description:

- the startup's copies (RAM code and data at their run addresses);
- the memory regions and peripheral ranges as uninitialised blocks, peripherals volatile;
- the image's data ranges as not executable;
- the names and function starts of the image's symbols and function_starts tables;
- the reset entry. On an M-profile core the reset entry and every handler come from the vector
  table at the image's base.

After the analysis DreadExportMap.java writes functions.tsv and code_ranges.tsv (docs/formats.md)
and, unless --no-decompile is given, DreadExportC.java writes Ghidra's C for reading.

Ghidra is found through $GHIDRA_INSTALL_DIR, [host] ghidra in the target, or the usual install
places. Ghidra refuses a project path with an element that starts with '.' (a git worktree under
.claude/worktrees, for instance); --project then puts the project elsewhere.

Not handled yet: Thumb code in an A-profile image is found only where the analysis finds it by
itself (symbols carry no instruction set).

    dread ghidra [--target DIR] [--image NAME] [--out DIR] [--decompiled DIR] [--project DIR]
                 [--no-decompile] [--no-symbols]
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys

from dread import target as tgt
from dread.program import read_ranges, read_symbols

HERE = os.path.dirname(os.path.abspath(__file__))
PLACES = ("/opt/ghidra", "/opt/homebrew/opt/ghidra/libexec", "/usr/share/ghidra", "/usr/local/ghidra")
M_VECTORS = 16 + 496            # the most an M-profile vector table can have


def find_ghidra(target=None):
    """The Ghidra installation directory, or None."""
    candidates = [os.environ.get("GHIDRA_INSTALL_DIR")]
    if target is not None:
        candidates.append(target.host.get("ghidra"))
    candidates += PLACES
    for d in candidates:
        if d and os.path.exists(os.path.join(d, "support", "analyzeHeadless")):
            return d
    return None


def subtract(span, taken):
    """The parts of span (lo, hi) that none of the ranges in `taken` covers."""
    parts = [span]
    for t_lo, t_hi in taken:
        nxt = []
        for lo, hi in parts:
            if t_hi <= lo or hi <= t_lo:
                nxt.append((lo, hi))
                continue
            if lo < t_lo:
                nxt.append((lo, t_lo))
            if t_hi < hi:
                nxt.append((t_hi, hi))
        parts = nxt
    return parts


def vector_handlers(image):
    """[(index, handler address)] of an M-profile vector table at the image's base: entries from 1
    on, 0 (reserved) skipped, ending at the first word that is not a Thumb address in the image."""
    data, base = image.payload(), image.base
    out = []
    for i in range(1, min(M_VECTORS, len(data) // 4)):
        word = struct.unpack_from("<I", data, 4 * i)[0]
        if word == 0:
            continue
        if not word & 1 or not (base <= (word & ~1) < base + len(data)):
            break
        out.append((i, word & ~1))
    return out


def loaded_word(image, address, size=2):
    """The little-endian word at address in the image, or in what the loader puts beside it (the
    startup's "load" entries: an MSP430's vectors in a flash region of their own), or None."""
    data, base = image.payload(), image.base
    if base <= address and address + size <= base + len(data):
        return int.from_bytes(data[address - base:address - base + size], "little")
    for c in image.startup():
        stored = getattr(c, "data", None)
        if c.kind == "load" and stored and c.run <= address and address + size <= c.run + len(stored):
            return int.from_bytes(stored[address - c.run:address - c.run + size], "little")
    return None


def directives(target, image, symbols=True, options=()):
    """The lines of DreadSetup.java's directive file."""
    out = ["# written by dread ghidra: %s, image %s" % (target.name, image.name)]
    out += ["option %s" % o for o in options]
    base, end = image.base, image.end
    out.append("image 0x%x 0x%x" % (base, end))
    copies = image.startup()
    taken = [(base, end)]
    for i, c in enumerate(copies):
        if c.kind in ("code", "data"):          # (a loader's bytes, kind load, stay plain RAM here)
            out.append("copy %s_%x 0x%x 0x%x 0x%x %d" % (c.kind, c.run, c.run, c.stored, c.size, c.kind == "code"))
            taken.append((c.run, c.run + c.size))
    for r in target.memory.regions:
        for lo, hi in subtract((r.base, r.end), taken):
            out.append("%s %s_%x 0x%x 0x%x" % ("rom" if r.kind == "rom" else "ram", r.name, lo, lo, hi - lo))
    for lo, hi in target.memory.io:
        out.append("io io_%x 0x%x 0x%x" % (lo, lo, hi - lo))
    covered = [(r.base, r.end) for r in target.memory.regions] + taken
    for c in copies:
        if c.kind == "zero":
            for lo, hi in subtract((c.run, c.run + c.size), covered):
                out.append("ram bss_%x 0x%x 0x%x" % (lo, lo, hi - lo))
    for start, stop, _ in read_ranges(image.table("data_ranges")):
        if stop is not None:
            out.append("data 0x%x 0x%x" % (start, stop))
    entries = getattr(image.arch, "vector_entries", None)
    if image.arch.profile == "m":
        handlers = vector_handlers(image)
        if handlers:
            out.append("entry 0x%x" % handlers[0][1])
        for _, address in handlers[1:]:
            out.append("func 0x%x -" % address)
    elif entries is not None and image.vectors is not None:     # a vector table the layer reads (MSP430)
        table = entries(lambda a: loaded_word(image, a), image.vectors)
        for _, address, kind in table:
            out.append(("entry 0x%x" if kind == "reset" else "func 0x%x -") % address)
        if not any(kind == "reset" for _, _, kind in table):
            try:
                out.append("entry 0x%x" % image.entry)
            except tgt.TargetError:
                pass
    else:
        try:
            out.append("entry 0x%x" % image.entry)
        except tgt.TargetError:
            pass
    if symbols:
        for kind, address, name in read_symbols(image.table("symbols")):
            if kind == "d" and name:
                out.append("label 0x%x %s" % (address, name))
            else:
                out.append("func 0x%x %s" % (address, name or "-"))
        path = image.table("function_starts")
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.split("#", 1)[0].strip()
                    if line:
                        out.append("func 0x%x -" % int(line.split()[0], 0))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread ghidra", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--out", help="directory for functions.tsv and code_ranges.tsv (default: the image's config)")
    parser.add_argument("--decompiled", help="directory for Ghidra's C (default: decompiled, or decompiled/IMAGE)")
    parser.add_argument("--project", help="Ghidra project directory (default: BUILD/ghidra/IMAGE)")
    parser.add_argument("--no-decompile", action="store_true", help="skip Ghidra's C (much faster)")
    parser.add_argument("--no-symbols", action="store_true",
                        help="ignore the image's symbols and function starts (what the analysis finds alone)")
    parser.add_argument("--region", type=lambda v: int(v, 0), default=0x10000, help="bytes per decompiled file")
    parser.add_argument("--option", action="append", default=[], metavar="NAME=VALUE",
                        help="a Ghidra analysis option, e.g. 'ARM Aggressive Instruction Finder=true'")
    parser.add_argument("--aggressive", action="store_true",
                        help="turn on Ghidra's ARM Aggressive Instruction Finder (finds code nothing calls)")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        image.payload()
    except tgt.TargetError as e:
        print("dread ghidra: %s" % e, file=sys.stderr)
        return 1
    ghidra = find_ghidra(target)
    if ghidra is None:
        print("dread ghidra: no Ghidra found (set GHIDRA_INSTALL_DIR)", file=sys.stderr)
        return 1
    project = os.path.abspath(args.project or os.path.join(target.build, "ghidra", image.name))
    if any(part.startswith(".") for part in project.split(os.sep) if part):
        print("dread ghidra: Ghidra refuses the project path %s (an element starts with '.'); use --project"
              % project, file=sys.stderr)
        return 1
    out = os.path.abspath(args.out or image.config)
    decompiled = os.path.abspath(args.decompiled or target.path(
        "decompiled" if len(target.images) == 1 else os.path.join("decompiled", image.name)))
    os.makedirs(project, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    setup = os.path.join(project, "setup.txt")
    with open(setup, "w") as f:
        options = list(args.option) + (["ARM Aggressive Instruction Finder=true"] if args.aggressive else [])
        f.write("\n".join(directives(target, image, symbols=not args.no_symbols, options=options)) + "\n")
    functions = os.path.join(project, "functions.tsv")
    code_ranges = os.path.join(project, "code_ranges.tsv")
    cmd = [os.path.join(ghidra, "support", "analyzeHeadless"), project, "%s-%s" % (target.name, image.name),
           "-import", image.original, "-overwrite",
           "-loader", "BinaryLoader", "-loader-baseAddr", "0x%x" % image.base, "-loader-blockName", "image",
           "-processor", image.arch.ghidra, "-cspec", getattr(image.arch, "ghidra_cspec", "default"),
           "-scriptPath", HERE,
           "-preScript", "DreadSetup.java", setup,
           "-postScript", "DreadExportMap.java", functions, code_ranges, image.arch.isas()[0]]
    if not args.no_decompile:
        if os.path.isdir(decompiled):
            shutil.rmtree(decompiled)
        cmd += ["-postScript", "DreadExportC.java", decompiled, target.title, "0x%x" % args.region]
    log = os.path.join(project, "analyze.log")
    with open(log, "w") as f:
        r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    with open(log) as f:
        text = f.read()
    for line in text.splitlines():
        if "(GhidraScript)" in line or "ERROR" in line:
            print(line.split("> ", 1)[-1] if "> " in line else line)
    if r.returncode or not os.path.exists(functions) or not os.path.exists(code_ranges):
        print("dread ghidra: the analysis failed; see %s" % log, file=sys.stderr)
        return 1
    shutil.copy(functions, os.path.join(out, image.tables["functions"]))
    shutil.copy(code_ranges, os.path.join(out, image.tables["code_ranges"]))
    print("functions.tsv and code_ranges.tsv written to %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
