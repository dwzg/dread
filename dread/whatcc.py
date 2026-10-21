"""dread whatcc: which toolchain's libraries an image links, and which of its functions are theirs.

A function of a library (the C library, libgcc, libm) that an image links unchanged has the bytes of
the library's object, except where the linker writes (a call's offset, an address in a literal pool):
its relocations. Each toolchain image of bench/toolchains, and the host's compilers, give their
archives for the image's multilib (the target's [compiler] flags: -mthumb, -mcpu, -mfloat-abi and
kin); every function of every member is a signature (its bytes, the relocated ones masked), and every
function entry of the image is compared with them. What it reports:

- per toolchain: how many of the image's functions have the bytes of one of its library functions,
  and how many of those no other toolchain's library gives: the toolchain with the most is the
  likeliest to have linked the image (its C library, its libgcc; the compiler of the image's own code
  can differ: clang links GCC's libraries). Toolchains that match the same functions are a tie, said
  so: their libraries cannot be told apart by this image (Debian 11's and Ubuntu 20.04's newlib give
  FreeRTOS's build the same 35 functions);
- per function: the names it matched, the best toolchain's first, and whether they agree with the
  name it has (a firmware without symbols gets names for its library functions, which no analysis
  of the code gives). A match whose calls go to functions that matched other names than the call's
  symbol is dropped (newlib's _close_r and _lseek_r differ only in what they call), and a name that
  still lands on several functions is given as ambiguous: the code of newlib's __errno (load a
  global, return it) is that of every such getter, FreeRTOS's xTaskGetTickCount among them.

Archives given with --archive (a vendor's library at work) join as a toolchain of their own. Nothing of
them is written anywhere but the archive cache and build/whatcc/; signatures are made where whatcc
runs.

Not handled yet: RISC-V's linker relaxes calls (auipc + jalr to jal) and so shortens linked code; a
library function with a relaxed call does not match byte for byte (one without calls does): on the
benchmark's RISC-V builds 3 to 12 functions match, against 26 to 49 on ARM.

    dread whatcc [--target DIR] [--image NAME] [--toolchain NAME...] [--archive PATH...] [--no-host]
                 [--no-toolchains] [--json FILE]

--json writes a summary for dread bench: the best toolchains (ties included), how many functions
matched, how many got a name no other function gets, and how many of those agree with the name the
function has.
"""
import json
import argparse
import hashlib
import os
import shutil
import subprocess
import sys

from dread.elf import Elf, ElfError, SHT_SYMTAB, STT_FUNC
from dread.elfimport import IDENT
from dread import target as tgt

CACHE = os.path.join(os.path.expanduser(os.environ.get("XDG_CACHE_HOME", "~/.cache")), "dread", "whatcc")
LIBS = ("libc.a", "libc_nano.a", "libg.a", "libg_nano.a", "libm.a", "libnosys.a")
MULTILIB_FLAGS = ("-mthumb", "-marm", "-mcpu=", "-march=", "-mfpu=", "-mfloat-abi=", "-mabi=", "-mlittle-endian",
                  "-mbig-endian")
EM_ARM, EM_RISCV = 40, 243
MIN_BYTES = 8                   # bytes of its own a signature must have (not written by the linker): a
                                # b.w to another function is all relocation, bx lr is every stub

# The bytes a relocation writes, by type (ELF for the Arm Architecture; the RISC-V psABI). Those not
# named write a word; markers (R_ARM_NONE, V4BX, RISC-V's RELAX and ALIGN) write none.
ARM_WIDTH = {0: 0, 11: 2, 40: 0, 102: 2, 103: 2}
RISCV_WIDTH = {0: 0, 18: 8, 19: 8, 43: 0, 44: 2, 45: 2, 51: 0}
# The relocations of calls and tail calls (R_ARM_THM_CALL, THM_JUMP24, CALL, JUMP24; R_RISCV_CALL,
# CALL_PLT, JAL, RVC_JUMP): where the linked code goes there says which function the call reached.
ARM_CALLS = {10, 30, 28, 29}
RISCV_CALLS = {17, 18, 19, 45}


class Signature:
    """A library function: its bytes, which of them must match (mask: 1), its name, the archive and
    member it comes from, and its instruction set where the symbol says (Thumb: bit 0)."""
    __slots__ = ("name", "data", "mask", "where", "thumb", "calls")

    def __init__(self, name, data, mask, where, thumb, calls=()):
        self.name, self.data, self.mask, self.where, self.thumb = name, data, mask, where, thumb
        self.calls = calls                  # [(offset, the called symbol's name)]

    def matches(self, image, at):
        end = at + len(self.data)
        if at < 0 or end > len(image):
            return False
        window = image[at:end]
        if self.mask is None:
            return window == self.data
        return all(m == 0 or a == b for a, b, m in zip(window, self.data, self.mask))


def archive_members(path):
    """[(name, bytes)] of a Unix ar archive (GNU long names, the symbol table left out)."""
    with open(path, "rb") as f:
        raw = f.read()
    if not raw.startswith(b"!<arch>\n"):
        raise ValueError("%s is not an ar archive" % path)
    out, at, longnames = [], 8, b""
    while at + 60 <= len(raw):
        head = raw[at:at + 60]
        name = head[:16].decode("latin-1").rstrip()
        size = int(head[48:58].decode("latin-1").strip() or 0)
        data = raw[at + 60:at + 60 + size]
        at += 60 + size + (size & 1)
        if name in ("/", "/SYM64/"):
            continue
        if name == "//":
            longnames = data
            continue
        if name.startswith("/") and name[1:].isdigit():
            start = int(name[1:])
            end = longnames.find(b"/\n", start)
            name = longnames[start:end if end >= 0 else None].decode("latin-1")
        out.append((name.rstrip("/"), data))
    return out


def signatures_of(path, label):
    """The signatures of every function in the archive (or object) at path."""
    members = archive_members(path) if path.endswith(".a") else [(os.path.basename(path), open(path, "rb").read())]
    out = []
    for member, data in members:
        try:
            elf = Elf(member, raw=data)
        except (ElfError, Exception):
            continue
        widths = ARM_WIDTH if elf.machine == EM_ARM else RISCV_WIDTH if elf.machine == EM_RISCV else None
        if widths is None:
            continue
        calls_of = ARM_CALLS if elf.machine == EM_ARM else RISCV_CALLS
        for index, sec in enumerate(elf.sections):
            if not sec.code or not sec.size or sec.nobits:
                continue
            body = elf.data(sec)
            relocs = elf.relocations(index)
            for sym in elf.symbols:
                if sym.kind != STT_FUNC or not sym.size or sym.shndx != index or not sym.name:
                    continue
                start = sym.value & ~1 if elf.machine == EM_ARM else sym.value
                if start + sym.size > len(body):
                    continue
                mask = None
                for offset, kind, _, _ in relocs:
                    if start <= offset < start + sym.size:
                        width = widths.get(kind, 4)
                        if width:
                            mask = mask or bytearray(b"\x01" * sym.size)
                            for k in range(offset - start, min(offset - start + width, sym.size)):
                                mask[k] = 0
                own = sym.size if mask is None else sum(mask)
                if own < MIN_BYTES:
                    continue
                calls = [(offset - start, elf.symbols[k].name) for offset, kind, k, _ in relocs
                         if kind in calls_of and start <= offset < start + sym.size and k < len(elf.symbols)
                         and elf.symbols[k].name]
                out.append(Signature(sym.name, body[start:start + sym.size], bytes(mask) if mask else None,
                                     "%s(%s)" % (label, member), bool(sym.value & 1) if elf.machine == EM_ARM else None,
                                     calls))
    return out


def multilib_flags(cflags):
    """The flags among cflags that pick a library's multilib variant."""
    return [f for f in cflags if any(f == m or (m.endswith("=") and f.startswith(m)) for m in MULTILIB_FLAGS)]


def _fetch_script(cross, flags):
    flags = " ".join("'%s'" % f for f in flags)
    libs = " ".join(LIBS)
    return ('for lib in %s; do f=$(%sgcc %s -print-file-name=$lib); [ -f "$f" ] && cp "$f" "$OUT/$lib"; done; '
            'f=$(%sgcc %s -print-libgcc-file-name); [ -f "$f" ] && cp "$f" "$OUT/libgcc.a"; '
            '%sgcc --version | head -1 > "$OUT/version"' % (libs, cross, flags, cross, flags, cross))


def toolchain_archives(name, cross, flags, out=print):
    """(version line, [archive paths]) of a toolchain's libraries for flags: name "host" for the
    host's compilers, else a toolchain image of bench/toolchains (built with podman if missing). Kept
    in CACHE by the image (its file's digest) and the flags."""
    if name == "host":
        tag = hashlib.sha1(("%s %s" % (shutil.which(cross + "gcc"), os.path.getmtime(shutil.which(cross + "gcc"))
                                       if shutil.which(cross + "gcc") else "")).encode()).hexdigest()[:12]
    else:
        from dread import bench
        tag = bench.toolchain_digest(name)[:12]
    where = os.path.join(CACHE, "%s-%s" % (name, tag), hashlib.sha1((cross + " " + " ".join(flags)).encode()).hexdigest()[:12])
    done = os.path.join(where, "version")
    if not os.path.exists(done):
        os.makedirs(where, exist_ok=True)
        script = _fetch_script(cross, flags)
        if name == "host":
            if shutil.which(cross + "gcc") is None:
                return None, []
            subprocess.run(["sh", "-c", script], env=dict(os.environ, OUT=where), capture_output=True)
        else:
            from dread import bench
            image = bench.ensure_toolchain(name, out=out)
            subprocess.run(["podman", "run", "--rm", "--network=none", "-e", "OUT=" + where, "-v",
                            "%s:%s" % (where, where), image, "sh", "-c", script], capture_output=True)
    version = open(done).read().strip() if os.path.exists(done) else None
    archives = sorted(os.path.join(where, f) for f in os.listdir(where) if f.endswith(".a")) if os.path.isdir(where) else []
    return version, archives


def match(program, groups):
    """{group: {entry: [signatures]}}: for each group of signatures (a toolchain's), the image's
    function entries whose bytes are one of its functions', the matches whose calls contradict the
    matches of what they call left out (consistent)."""
    image, base = bytes(program.data), program.base
    found = {}
    for group, sigs in groups.items():
        index = {}
        loose = []
        for s in sigs:
            if len(s.data) >= 4 and (s.mask is None or all(s.mask[:4])):
                index.setdefault(s.data[:4], []).append(s)
            else:
                loose.append(s)
        hits = {}
        for fn in program.functions:
            at = fn.entry - base
            if not 0 <= at < len(image):
                continue
            for s in index.get(image[at:at + 4], []) + loose:
                if s.thumb is not None and program.isa_at(fn.entry) not in (("t32",) if s.thumb else ("a32",)):
                    continue
                if s.matches(image, at):
                    hits.setdefault(fn.entry, []).append(s)
        found[group] = consistent(program, hits)
    return found


def consistent(program, hits):
    """hits without the matches whose calls contradict: a call of the signature (its relocation's
    symbol) whose linked target is a function of the image that matched, but not under that name.
    Repeated until nothing changes (a dropped match can make another call's target unmatched)."""
    arch = program.arch
    for _ in range(4):
        names = {e: {s.name for s in sigs} for e, sigs in hits.items()}
        changed = False
        for entry, sigs in list(hits.items()):
            keep = []
            for s in sigs:
                ok = True
                for offset, callee in s.calls:
                    insn = program.insn(entry + offset)
                    target = arch.target(insn) if insn is not None else None
                    if target is None:
                        continue
                    there = names.get(target & ~1)
                    if there and callee not in there:
                        ok = False
                        break
                if ok:
                    keep.append(s)
            if len(keep) != len(sigs):
                changed = True
                if keep:
                    hits[entry] = keep
                else:
                    del hits[entry]
        if not changed:
            break
    return hits


def report(program, found, versions):
    """(lines, rows, best): the toolchains by matched functions (and those only they match), and per
    function its names. rows: (entry, its name, the names matched (the best toolchain's; those of the
    first toolchain that matches it where the best does not), the ambiguous ones among them (they
    land on other functions too), all toolchains that match)."""
    by_entry = {}
    for group, hits in found.items():
        for entry in hits:
            by_entry.setdefault(entry, set()).add(group)
    ranking = sorted(found, key=lambda g: (-len(found[g]), g))
    lines = ["%-14s %4s %5s  %s" % ("toolchain", "fns", "alone", "version")]
    for g in ranking:
        alone = sum(1 for e in found[g] if by_entry[e] == {g})
        lines.append("%-14s %4d %5d  %s" % (g, len(found[g]), alone, versions.get(g) or ""))
    best = ranking[0] if ranking and found[ranking[0]] else None     # (none where nothing matched)
    if best is not None:
        tied = [g for g in ranking if g != best and set(found[g]) == set(found[best])]
        lines.append("best: %s%s" % (best, " = " + ", ".join(tied) + " (the same functions: a tie)" if tied else ""))
    rows, chosen = [], {}
    for entry in sorted(by_entry):
        sigs = found[best].get(entry) if best else None
        if not sigs:
            sigs = next(found[g][entry] for g in ranking if entry in found[g])
        chosen[entry] = list(dict.fromkeys(s.name for s in sigs))
    lands = {}
    for entry, names in chosen.items():
        for n in names:
            lands.setdefault(n, []).append(entry)
    for entry in sorted(chosen):
        fn = program.by_entry.get(entry)
        names = chosen[entry]
        ambiguous = [n for n in names if len(lands[n]) > 1]
        rows.append((entry, fn.name if fn else "", names, ambiguous, sorted(by_entry[entry])))
    return lines, rows, best


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread whatcc", description=__doc__.split("\n\n")[0])
    parser.add_argument("--target")
    parser.add_argument("--image")
    parser.add_argument("--toolchain", action="append", default=None,
                        help="a toolchain image of bench/toolchains (default: all of them)")
    parser.add_argument("--archive", action="append", default=[], help="an archive or object of one's own")
    parser.add_argument("--no-host", action="store_true", help="leave out the host's compilers")
    parser.add_argument("--no-toolchains", action="store_true", help="no toolchain images (no podman)")
    parser.add_argument("--json", help="write a summary here")
    args = parser.parse_args(argv)
    from dread.program import Program
    target = tgt.Target.load(args.target)
    image = target.image(args.image)
    program = Program(target, image)
    compiler = target.compiler
    cross = getattr(compiler, "cross", None) or {"arm": "arm-none-eabi-", "riscv": "riscv64-elf-"}.get(program.arch.name)
    flags = multilib_flags(list(getattr(compiler, "cflags", None) or []))
    groups, versions = {}, {}
    names = [] if args.no_toolchains else (args.toolchain or sorted(
        f[:-len(".containerfile")] for f in os.listdir(os.path.join(os.path.dirname(__file__), "..", "bench", "toolchains"))
        if f.endswith(".containerfile")))
    if not args.no_host:
        names = ["host"] + names
    for name in names:
        try:
            version, archives = toolchain_archives(name, cross, flags)
        except Exception as e:                  # (no podman, an image that does not build)
            print("whatcc: %s: %s" % (name, e), file=sys.stderr)
            continue
        sigs = [s for path in archives for s in signatures_of(path, os.path.basename(path))]
        if sigs:
            groups[name], versions[name] = sigs, version
    for path in args.archive:
        groups[os.path.basename(path)] = signatures_of(path, os.path.basename(path))
    found = match(program, groups)
    lines, rows, best = report(program, found, versions)
    print("dread whatcc: %d functions of %s, %s (%s)" % (len(program.functions), image.name, cross, " ".join(flags)))
    print("\n".join(lines))
    sure = [r for r in rows if not set(r[2]) <= set(r[3])]
    agree = sum(1 for _, have, names_, amb, _ in sure if have in {IDENT.sub("_", n) for n in names_ if n not in amb})
    print("%d functions have a library's bytes, %d of them with a name no other function gets; %d of those "
          "by the name they have" % (len(rows), len(sure), agree))
    out_dir = os.path.join(target.build, "whatcc", image.name)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "matches.tsv"), "w") as f:
        f.write("entry\tname\tmatched\tambiguous\ttoolchains\n")
        for entry, have, names_, amb, groups_ in rows:
            f.write("0x%x\t%s\t%s\t%s\t%s\n" % (entry, have, ",".join(names_), ",".join(amb), ",".join(groups_)))
    with open(os.path.join(out_dir, "toolchains.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    if args.json:
        tied = [g for g in found if best is not None and set(found[g]) == set(found[best])]
        with open(args.json, "w") as f:
            json.dump({"best": sorted(tied, key=lambda g: g != best), "matched": len(rows), "sure": len(sure),
                       "agree": agree, "toolchains": {g: len(found[g]) for g in found}}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
