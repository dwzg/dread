"""Link rewritten C functions over the original image (the overlay).

The image keeps its layout. Each C function that has the name of a function of the image (its
function table) replaces it:

- **in place** when it fits into the run of instructions at the original entry;
- **behind a literal pool** when it fits into a later run of the original function's own
  instructions, with a branch at the entry;
- **moved** otherwise: into space other rewritten functions freed inside the image or into the
  image's free ranges ([image] free: erased flash inside the image; best fit, freed space first),
  or into the room behind the image up to the image's limit, with a branch at the entry.

A function may also take its whole extent, from its entry to the next function's, literal pools
included, when nothing else uses those pools: no other original function loads from them relative
to pc, no word of the image points into them, and no C function's constants do (--no-extent turns
this off). Helper functions, string literals and constants of the C go behind the image, or
into the largest free range when that has more room (an image that fills its flash up to a vector
table at the top).

Every function of the table that no C replaces stays where it is and becomes a linker symbol
(PROVIDE, with bit 0 set for Thumb code), so C calls into the original code by name and the original
code keeps calling the same addresses. Data of the original is reached by address (VAR(type, addr)
in dread.h); the C may not add zero-initialised or writable initialised data, because the startup
of the original would not set it up.

After linking, every byte that differs from the original must lie in a slot that was handed out,
in freed space, or be the branch at a moved function's entry; anything else aborts. A function
whose C did not land exactly at its slot (an alignment the slot does not have) aborts as well.

Refused: replacing a function that other original code branches into behind its entry; a function
that runs from a RAM copy; a function whose C is in another instruction set than the original.

Writes the image, report.tsv (function, address, original bytes, C bytes, placement) and
placement.json (the same for programs: entry, address, branch at the old entry or not).

    dread overlay [--target DIR] [--image NAME] --out PAYLOAD [--workdir DIR] [--no-extent] OBJECT...
"""
import argparse
import bisect
import json
import os
import struct
import subprocess
import sys

from dread import target as tgt
from dread.elf import SHT_NOBITS, STT_FUNC, Elf
from dread.program import Program

SHT_PROGBITS = 1
EXTENT_TAIL = 1024      # bytes behind a function's last instruction that may still count as its own


class OverlayError(Exception):
    """The link cannot be made; `function` names the one function at fault when there is one."""

    def __init__(self, message, function=None):
        super().__init__(message)
        self.function = function


def align_up(value, alignment):
    return (value + alignment - 1) // alignment * alignment


def runs_of(prog, fn):
    """The function's runs of contiguous instructions [(start, length)], entry run first."""
    out = []
    for a in fn.insns:
        n = prog.insn_size(a)
        if out and out[-1][0] + out[-1][1] == a:
            out[-1][1] += n
        else:
            out.append([a, n])
    return [tuple(r) for r in out]


def read_objects(objects, arch):
    """{function: (size, alignment, words of its contents, isa)} from the .text.NAME sections; the
    isa from the function's symbol (arch.isa_of_symbol: ARM marks Thumb with bit 0)."""
    out = {}
    for obj in objects:
        elf = Elf(obj)
        isa = {s.name: arch.isa_of_symbol(s.value) for s in elf.symbols if s.kind == STT_FUNC}
        for sec in elf.sections:
            if sec.name.startswith(".text.") and sec.kind == SHT_PROGBITS and sec.alloc:
                fn = sec.name[len(".text."):]
                if fn in out:
                    raise OverlayError("function %s is defined in more than one object" % fn)
                data = elf.data(sec)
                words = set(struct.unpack_from("<%dI" % (len(data) // 4), data))
                out[fn] = (sec.size, sec.align, words, isa.get(fn, arch.isa))
    return out


def link(target, image, objects, out, workdir, extent=True, why=()):
    """Link `objects` over the image; write the image to `out`. Returns (report lines, placement)."""
    prog = Program(target, image)
    arch = image.arch
    base, limit, erased = image.base, image.limit, image.erased
    original = prog.data
    picture = bytearray(original)
    end_of_image = base + len(original)
    funcs = {fn.name: fn for fn in prog.functions}
    ram = [(c.run, c.run + c.size) for c in image.startup() if c.kind == "code"]
    slots = {name: runs_of(prog, fn) for name, fn in funcs.items()}
    objs = read_objects(objects, arch)
    sizes = {fn: v[0] for fn, v in objs.items()}
    aligns = {fn: v[1] for fn, v in objs.items()}
    constants = {fn: v[2] for fn, v in objs.items()}

    replaced = [fn for fn in sizes if fn in funcs]
    for fn in replaced:
        f = funcs[fn]
        if any(lo <= f.entry < hi for lo, hi in ram) or not (base <= f.entry < end_of_image):
            raise OverlayError("%s runs from a RAM copy or lies outside the image; it cannot be overlaid" % fn, fn)
        if objs[fn][3] != f.isa:
            raise OverlayError("%s is %s in the original but %s in the C: mixing instruction sets is not supported"
                               % (fn, f.isa, objs[fn][3]), fn)

    # Who owns which instruction, and which original code branches into a replaced function
    # behind its entry (the branch would land in whatever lies there afterwards).
    owner = {a: fn.name for fn in prog.functions for a in fn.insns}
    free = image.free
    for lo, hi in free:
        inside = [a for a in owner if lo <= a < hi]
        if inside:
            raise OverlayError("the free range 0x%x-0x%x holds code of %s" % (lo, hi, owner[min(inside)]))
    entered = {}
    loaded, loaders, all_loaders = set(), {}, {}
    for fn in prog.functions:
        for a in fn.insns:
            insn = prog.insn(a)
            if insn is None:
                continue
            t = arch.target(insn)
            if t is not None:
                to = owner.get(t)
                if to is not None and to != fn.name and t != funcs[to].entry and fn.name not in sizes:
                    entered.setdefault(to, (fn.name, a, t))
            ref = arch.literal(insn, fn.isa)
            ref = ref[0] if ref else arch.pc_address(insn, fn.isa)
            if ref is not None:
                ref &= ~3
                all_loaders.setdefault(ref, set()).add(fn.name)
                if fn.name not in sizes:
                    loaded.add(ref)
                    loaders.setdefault(ref, set()).add(fn.name)
    for fn in replaced:
        if fn in entered:
            raise OverlayError("%s cannot be replaced: %s branches into it at 0x%x from 0x%x"
                               % (fn, entered[fn][0], entered[fn][2], entered[fn][1]))

    # The whole extent, pools included, where nothing else needs the pools.
    whole, first_run = set(), {fn: slots[fn][0][1] for fn in replaced}
    if extent:
        entries = sorted(f.entry for f in prog.functions if base <= f.entry < end_of_image)
        words = sorted((v, base + 4 * i) for i, v in enumerate(struct.unpack_from("<%dI" % (len(original) // 4), original))
                       if base < v < end_of_image)
        values = [v for v, _ in words]
        for fn in replaced:
            f = funcs[fn]
            step = arch.min_insn(f.isa)
            last = slots[fn][-1][0] + slots[fn][-1][1]
            i = bisect.bisect_right(entries, f.entry)
            stop = min(entries[i] if i < len(entries) else last, end_of_image)
            if stop - last > EXTENT_TAIL:
                stop = last
            for lo, hi in free:                 # a free range is handed out as such, not as an extent
                if f.entry < lo < stop:         # (the alignment gap in front of an aligned section:
                    stop = lo                   # given twice once, and the link failed)
            if stop <= slots[fn][0][0] + slots[fn][0][1]:
                continue
            inside = set(range(f.entry, stop, step))
            mine = {a + k for a in f.insns for k in range(0, prog.insn_size(a), step)}
            reason = None
            if any(a in loaded for a in inside - mine):
                reason = "pc-relative loads into it from %s" % " ".join(sorted(
                    {g for a in inside - mine if a in loaded for g in loaders[a]}))
            elif any(at not in inside and (v & ~1) not in mine for v, at in
                     words[bisect.bisect_right(values, f.entry):bisect.bisect_left(values, stop)]):
                reason = "words outside point at data inside"
            elif any(f.entry < v < stop for v in constants.get(fn, ())):
                reason = "the C reads inside it"
            elif any(f.entry < v < stop and (v & ~1) not in mine for g, vs in constants.items() if g != fn for v in vs):
                reason = "the C of other functions reads inside it"
            elif any(owner.get(a, fn) != fn for a in inside):
                reason = "instructions of other functions inside"
            if reason:
                if fn in why or "all" in why:
                    sys.stderr.write("extent of %s refused: %s\n" % (fn, reason))
                continue
            whole.add(fn)
            slots[fn] = [(f.entry, stop - f.entry)]

    # Placement.
    def fit(start, length, fn):
        """(address, room) where fn fits in [start, start + length) with its alignment, or None."""
        at = align_up(start, aligns[fn])
        return (at, start + length - at) if sizes[fn] <= start + length - at else None

    branch_len = {fn: arch.branch_length(funcs[fn].isa, funcs[fn].entry, (base, limit)) for fn in replaced}
    in_slot, in_body, relocated, helpers = [], [], [], [fn for fn in sizes if fn not in funcs]
    place = {}
    for fn in sorted(replaced):
        runs = slots[fn]
        here = fit(runs[0][0], runs[0][1], fn) if runs[0][0] % aligns[fn] == 0 else None
        if here:
            in_slot.append(fn)
            place[fn] = here
            continue
        if runs[0][1] < branch_len[fn]:
            raise OverlayError("%s does not fit at its entry and leaves no room for a branch there" % fn, fn)
        later = [fit(s, n, fn) for s, n in runs[1:]]
        later = [x for x in later if x]
        if later:
            in_body.append(fn)
            place[fn] = max(later, key=lambda x: x[1])
        else:
            relocated.append(fn)

    holes = []
    for fn in in_slot + in_body:
        start = align_up(place[fn][0] + sizes[fn], 4)
        if place[fn][0] + place[fn][1] - start >= 8:
            holes.append([start, place[fn][0] + place[fn][1]])
    for fn in in_body + relocated:
        for start, length in slots[fn]:
            if fn in in_body and place[fn][0] >= start and place[fn][0] < start + length:
                continue
            if start == funcs[fn].entry:
                start, length = start + branch_len[fn], length - branch_len[fn]
            if length >= 8:
                holes.append([start, start + length])
    hole_space = [tuple(h) for h in holes]
    free_holes = [[lo, hi] for lo, hi in free]
    holes += free_holes                 # after the freed holes: best fit takes those first
    in_hole = []
    for fn in sorted(relocated, key=lambda n: (-sizes[n], n)):
        fits = [(h, fit(h[0], h[1] - h[0], fn)) for h in holes]
        fits = [(h, x) for h, x in fits if x]
        if fits:
            h, (at, _) = min(fits, key=lambda hx: (hx[0][1] - hx[0][0], hx[0][0]))
            place[fn] = (at, sizes[fn])
            h[0] = align_up(at + sizes[fn], 2)
            in_hole.append(fn)
    in_tail = [fn for fn in relocated if fn not in in_hole]

    # No two placements may overlap (a fault in the placement above, said here rather than by the linker).
    spans = sorted((place[fn][0], place[fn][0] + sizes[fn], fn) for fn in in_slot + in_body + in_hole)
    for (a_lo, a_hi, a), (b_lo, b_hi, b) in zip(spans, spans[1:]):
        if b_lo < a_hi:
            raise OverlayError("internal: %s at 0x%x-0x%x overlaps %s at 0x%x" % (a, a_lo, a_hi, b, b_lo))

    # Link. The C's own code and constants go where the most room is left: behind the image, or a
    # free range inside it.
    os.makedirs(workdir, exist_ok=True)
    script = os.path.join(workdir, "recon.ld")
    rooms = [(align_up(end_of_image, 8), max(limit, align_up(end_of_image, 8)))] + \
        [(align_up(h[0], 8), h[1]) for h in free_holes if align_up(h[0], 8) < h[1]]
    tail, tail_end = max(rooms, key=lambda r: r[1] - r[0])
    tail_inside = tail < end_of_image
    with open(script, "w") as f:
        f.write("/* written by dread overlay */\nSECTIONS\n{\n")
        for fn in sorted(in_slot + in_body + in_hole, key=lambda n: place[n][0]):
            f.write("    .recon.%s 0x%x : { *(.text.%s) }\n" % (fn, place[fn][0], fn))
        # the linker's own code (interworking glue for ARMv4T: Thumb calling ARM and back) goes there too:
        # left to itself it lands behind whatever section came before, inside the image
        # (RISC-V: small constants in .srodata, small data in .sdata and .sbss)
        f.write("    .recon.tail 0x%x : { *(.text .text.* .rodata .rodata.* .srodata .srodata.* .glue_7 .glue_7t"
                " .v4_bx .vfp11_veneer .iplt) }\n" % tail)
        f.write("    .recon.data : { *(.data .data.* .sdata .sdata.*) }\n"
                "    .bss (NOLOAD) : { *(.bss .bss.* .sbss .sbss.* COMMON) }\n"
                "    /DISCARD/ : { *(.comment) *(.ARM.attributes) *(.riscv.attributes) *(.note*) *(.ARM.exidx*)"
                " *(.ARM.extab*) }\n}\n"
                'ASSERT(SIZEOF(.bss) == 0, "the C may not add zero-initialised data")\n'
                'ASSERT(SIZEOF(.recon.data) == 0, "the C may not add writable initialised data")\n'
                'ASSERT(ADDR(.recon.tail) + SIZEOF(.recon.tail) <= 0x%x, "the room for the C is full")\n'
                % tail_end)
        names = [(name, fn) for name, fn in sorted(funcs.items(), key=lambda kv: kv[1].entry)
                 if name not in sizes and name.replace("_", "a").isalnum() and not name[0].isdigit()]
        typed = getattr(arch, "symbol_asm", None)
        if typed is None:
            for name, fn in names:
                f.write("PROVIDE(%s = 0x%x);\n" % (name, arch.start_address(fn.entry, fn.isa)))
    extra = []
    if names and typed is not None:
        # The original's functions as absolute symbols that carry their instruction set: a linker
        # script's PROVIDE has no type, and the ARM linker then takes a Thumb function for ARM code and
        # puts an interworking stub in front of every call of it (inside the caller's slot).
        source = os.path.join(workdir, "original_symbols.s")
        with open(source, "w") as f:
            f.write("".join(typed(name, fn.entry, fn.isa) for name, fn in names))
        obj = os.path.join(workdir, "original_symbols.o")
        r = subprocess.run(target.compiler.command("cc") + target.compiler.cflags + ["-c", "-x", "assembler", "-o", obj,
                                                                                     source],
                           capture_output=True, text=True)
        if r.returncode:
            raise OverlayError("assembling the original's symbols failed:\n" + r.stderr)
        extra.append(obj)
    elf_path = os.path.join(workdir, "recon.elf")
    r = subprocess.run(target.compiler.command("ld") + target.compiler.ldflags + ["-T", script, "-o", elf_path]
                       + list(objects) + extra, capture_output=True, text=True)
    if r.returncode:
        message = r.stderr
        if "the room for the C is full" in r.stderr:
            message += "no space for: %s\n" % " ".join(sorted(in_tail))
        raise OverlayError("the link failed:\n" + message)
    linked = Elf(elf_path)
    for sec in linked.sections:
        if sec.kind == SHT_PROGBITS and sec.alloc and sec.size:
            off = sec.addr - base
            if off < 0:
                raise OverlayError("section %s at 0x%x lies before the image" % (sec.name, sec.addr))
            if off > len(picture):
                picture.extend(bytes([erased]) * (off - len(picture)))
            picture[off:off + sec.size] = linked.data(sec)
    syms = {s.name: s.value & ~1 for s in linked.symbols if s.kind == STT_FUNC and s.name in sizes}
    for fn in in_slot + in_body + in_hole:
        if syms.get(fn) != place[fn][0]:
            raise OverlayError("%s was linked at 0x%x, not at its slot 0x%x (alignment?)"
                               % (fn, syms.get(fn, 0), place[fn][0]), fn)
    for fn in in_body + relocated:
        f = funcs[fn]
        code = arch.entry_branch(f.entry, syms[fn], f.isa, branch_len[fn])
        picture[f.entry - base:f.entry - base + len(code)] = code
    picture.extend(bytes([erased]) * (-len(picture) % 4))

    # Nothing outside what was handed out may have changed.
    allowed = set()
    for fn in in_slot + in_body:
        allowed.update(range(place[fn][0], place[fn][0] + place[fn][1]))
    for start, stop in hole_space + free:
        allowed.update(range(start, stop))
    for fn in in_body + relocated:
        allowed.update(range(funcs[fn].entry, funcs[fn].entry + branch_len[fn]))
    stray = [base + i for i in range(len(original)) if picture[i] != original[i] and base + i not in allowed]
    if stray:
        raise OverlayError("the overlay changed %d bytes outside the rewritten functions, first at 0x%x"
                           % (len(stray), stray[0]))
    if base + len(picture) > limit:
        raise OverlayError("the image grew past its limit 0x%x" % limit)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "wb") as f:
        f.write(picture)

    placement, lines = {}, ["function\taddress\toriginal_bytes\tc_bytes\tplacement"]
    for fn in sorted(sizes, key=lambda n: (funcs[n].entry if n in funcs else syms.get(n, 0), n)):
        if fn in helpers:
            how, text = "helper", "new helper " + ("in the image's free space" if tail_inside else "behind the image")
            lines.append("%s\t0x%x\t-\t%d\t%s" % (fn, syms.get(fn, 0), sizes[fn], text))
            placement[fn] = {"address": syms.get(fn, 0), "c_bytes": sizes[fn], "placement": how}
            continue
        f = funcs[fn]
        if fn in in_slot:
            how = "over its pools" if fn in whole and sizes[fn] > first_run[fn] else "in place"
            text = "in place" + (" over its literal pools" if how == "over its pools" else "")
        elif fn in in_body:
            how, text = "behind a pool", "in place at 0x%x behind a literal pool, branch at original entry" % syms[fn]
        elif fn in in_hole and any(lo <= syms[fn] < hi for lo, hi in free):
            how, text = "free space", "moved to 0x%x into the image's free space, branch at original entry" % syms[fn]
        elif fn in in_hole:
            how, text = "freed space", "moved to 0x%x into space freed by other functions, branch at original entry" % syms[fn]
        elif tail_inside:
            how, text = "free space", "moved to 0x%x into the image's free space, branch at original entry" % syms[fn]
        else:
            how, text = "behind the image", "moved to 0x%x behind the image, branch at original entry" % syms[fn]
        lines.append("%s\t0x%x\t%d\t%d\t%s" % (fn, f.entry, f.size, sizes[fn], text))
        placement[fn] = {"entry": f.entry, "address": syms[fn], "original_bytes": f.size, "c_bytes": sizes[fn],
                         "placement": how, "branch": fn in in_body + relocated, "isa": f.isa}
    done = len(replaced)
    tail_size = next((s.size for s in linked.sections if s.name == ".recon.tail"), 0)
    free_left = sum(h[1] - h[0] for h in free_holes) - (tail_size if tail_inside else 0)
    summary = {"replaced": done, "functions": len(funcs), "image_bytes": len(picture),
               "room_left": limit - base - len(picture), "freed": sum(e - b for b, e in hole_space),
               "reused": sum(sizes[fn] for fn in in_hole if any(lo <= place[fn][0] < hi for lo, hi in hole_space)),
               "free_left": free_left}
    lines.append("# %d of %d functions replaced by C; image %d bytes, %d left before the limit%s; "
                 "%d bytes freed inside the image, %d of them reused"
                 % (done, len(funcs), len(picture), summary["room_left"],
                    ", %d in its free ranges" % free_left if free else "", summary["freed"], summary["reused"]))
    with open(os.path.join(workdir, "report.tsv"), "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(workdir, "placement.json"), "w") as f:
        json.dump({"functions": placement, "summary": summary}, f, indent=1, sort_keys=True)
    return lines, placement


def load_placement(path):
    """(aliases {original entry: new address} for functions behind a branch, names of the functions
    built from C) from a placement.json; empty when the file does not exist."""
    if not os.path.exists(path):
        return {}, []
    with open(path) as f:
        data = json.load(f)["functions"]
    aliases = {v["entry"]: v["address"] for v in data.values() if v.get("branch")}
    built = [name for name, v in data.items() if v["placement"] != "helper"]
    return aliases, built


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread overlay", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--out", required=True, help="the image with the C linked in")
    parser.add_argument("--workdir", help="linker script, ELF and reports (default: the output's directory)")
    parser.add_argument("--no-extent", action="store_true", help="never give a function its literal pools")
    parser.add_argument("--why", default="", help="names (or 'all') whose refused extent is explained")
    parser.add_argument("objects", nargs="*")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        lines, _ = link(target, image, args.objects, args.out, args.workdir or os.path.dirname(os.path.abspath(args.out)),
                        extent=not args.no_extent, why=set(args.why.split()))
    except (tgt.TargetError, OverlayError) as e:
        print("dread overlay: %s" % e, file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
