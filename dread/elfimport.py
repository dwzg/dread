"""dread import-elf: an image's tables from an ELF that still has its symbols.

For firmware that comes with symbols, and for test images built from source (gizmo), the ELF knows
what an analysis otherwise has to work out: where functions start, how many instruction bytes they
have, which bytes are code of which instruction set and which are data. This tool writes the
image's functions.tsv, code_ranges.tsv and symbols.txt from it, in the formats Ghidra's export uses
(docs/formats.md). It prints the startup copies for dread.toml: sections whose run address differs
from their load address, and zeroed sections, and the image's free ranges: gaps between the
sections inside the payload (erased flash between the code and a vector table at the top), where
the overlay may put C. With --payload it also writes the raw image.

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
STB_GLOBAL, STB_WEAK, STT_NOTYPE = 1, 2, 0


def entry_labels(elf):
    """[(entry, end, name)] of functions hand-written assembly marks only partly: in every executable
    section, functions declared without a size (.type without .size: FreeRTOS's RISC-V start-up
    names _start so); in executable sections without mapping symbols also the global and weak labels
    without a type (GNU as for MSP430: libgcc's shift routines and shared epilogues have no .type).
    Each runs to the next label or function of its section; one inside a sized function is left
    out. Untyped labels in sections with mapping symbols (ARM, RISC-V) are left alone: one there (an
    ARM vector table named _start) is not a function."""
    marked = set(elf.mapping())
    out = []
    for index, s in enumerate(elf.sections):
        if not (s.alloc and s.code and s.size) or s.nobits:
            continue
        funcs = [(elf.code_value(sym.value), elf.code_value(sym.value) + sym.size) for sym in elf.symbols
                 if sym.kind == STT_FUNC and sym.size and sym.shndx == index]
        labels = {}
        for sym in elf.symbols:
            if sym.shndx != index or not sym.name or sym.name[0] in ".$":
                continue
            value = elf.code_value(sym.value) if sym.kind == STT_FUNC else sym.value
            if not s.addr <= value < s.addr + s.size or any(lo <= value < hi for lo, hi in funcs):
                continue
            if (sym.kind == STT_FUNC and not sym.size) or \
                    (index not in marked and sym.kind == STT_NOTYPE and sym.bind in (STB_GLOBAL, STB_WEAK)):
                labels.setdefault(value, sym.name)
        starts = sorted(set(labels) | {lo for lo, _ in funcs})
        for entry, name in sorted(labels.items()):
            later = [a for a in starts if a > entry]
            out.append((entry, min(later[0] if later else s.addr + s.size, s.addr + s.size), name))
    return out


def code_intervals(elf, isa="a32"):
    """[(start, end, kind)] of every allocated section, kind an isa or "data", from the mapping
    symbols ($a, $t for ARM; $x, code of `isa`, for RISC-V; $d data). An executable section without
    mapping symbols (GNU as for MSP430 writes none) is code of `isa` where its function symbols and
    its entry labels say so (their extents) and data elsewhere; with neither it is all code.

    Objects without mapping symbols can be linked beside objects with them (clang 16 writes none for
    RISC-V, LLVM 17 and GNU as do; the C library's objects put a few into the same .text, and
    everything of clang's in front of the first was taken for data: 299 of 315 FreeRTOS functions
    without code bytes). So in a section with mapping symbols, what lies in front of the first is
    read like a section without them (with its global and weak untyped labels: the port's assembly
    names its routines so; they make code, not functions, as the marks of another build would), and
    what the symbols leave as data inside a sized function symbol that holds none is code of `isa`
    (of Thumb for an odd ARM symbol). An unmarked function inside a code run keeps its run's kind
    (libgcc's assembly has several functions under one $t). A section that is not executable is
    data."""
    mapping = {index: [(a, isa if k == "code" else k) for a, k in marks] for index, marks in elf.mapping().items()}
    labels = entry_labels(elf)
    out = []
    for index, s in enumerate(elf.sections):
        if not s.alloc or s.nobits or not s.size:
            continue
        marks = [(a, k) for a, k in mapping.get(index, []) if s.addr <= a < s.addr + s.size]
        if not marks:
            spans = sorted([(elf.code_value(sym.value), elf.code_value(sym.value) + sym.size) for sym in elf.symbols
                            if sym.kind == STT_FUNC and sym.size and sym.shndx == index] +
                           [(lo, hi) for lo, hi, _ in labels if s.addr <= lo < s.addr + s.size]) if s.code else []
            if not spans:
                out.append((s.addr, s.addr + s.size, isa if s.code else "data"))
                continue
            out.extend(_runs(s.addr, s.addr + s.size, spans, isa))
            continue
        here = []
        first = marks[0][0]
        if first != s.addr and s.code:          # (an object without mapping symbols in front)
            funcs = [(elf.code_value(sym.value), min(first, elf.code_value(sym.value) + sym.size))
                     for sym in elf.symbols
                     if sym.kind == STT_FUNC and sym.size and sym.shndx == index
                     and s.addr <= elf.code_value(sym.value) < first]
            starts = sorted({lo for lo, _ in funcs} | {lo for lo, _, _ in labels if s.addr <= lo < first} |
                            {sym.value for sym in elf.symbols
                             if sym.shndx == index and sym.name and sym.name[0] not in ".$" and s.addr <= sym.value < first
                             and sym.kind == STT_NOTYPE and sym.bind in (STB_GLOBAL, STB_WEAK)
                             and not any(lo <= sym.value < hi for lo, hi in funcs)})
            spans = funcs + [(lo, min([a for a in starts if a > lo] + [first])) for lo in starts
                             if not any(f_lo <= lo < f_hi for f_lo, f_hi in funcs)]
            here.extend(_runs(s.addr, first, sorted(spans), isa))
        elif first != s.addr:
            here.append((s.addr, first, "data"))
        for (a, k), nxt in zip(marks, marks[1:] + [(s.addr + s.size, None)]):
            if nxt[0] > a:
                here.append((a, nxt[0], k))
        if s.code:
            unmarked = [(elf.code_value(sym.value), elf.code_value(sym.value) + sym.size,
                         THUMB.get(isa, isa) if sym.value & 1 else isa)
                        for sym in elf.symbols
                        if sym.kind == STT_FUNC and sym.size and sym.shndx == index
                        and not any(elf.code_value(sym.value) <= a < elf.code_value(sym.value) + sym.size
                                    for a, _ in marks)]
            for lo, hi, kind in sorted(unmarked):
                pieces = []
                for a, b, k in here:
                    if k != "data" or b <= lo or hi <= a:
                        pieces.append((a, b, k))
                        continue
                    pieces += [p for p in ((a, lo, k), (max(a, lo), min(b, hi), kind), (hi, b, k))
                               if p[0] < p[1]]
                here = pieces
        out.extend(here)
    out.sort()
    merged = []
    for start, end, kind in out:                # adjacent intervals of one kind become one
        if merged and merged[-1][2] == kind and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end, kind)
        else:
            merged.append((start, end, kind))
    return merged


def _runs(start, end, spans, isa):
    """[start, end) cut into code of `isa` where the sorted spans [(lo, hi)] are, data elsewhere."""
    out, at = [], start
    for lo, hi in spans:
        if lo > at:
            out.append((at, lo, "data"))
        if hi > at:
            out.append((max(lo, at), hi, isa))
            at = hi
    if at < end:
        out.append((at, end, "data"))
    return out


PADDING = {"t32": (b"\x00\xbf", b"\xc0\x46"), "a32": (b"\x00\x00\xa0\xe1", b"\x00\xf0\x20\xe3")}
THUMB = {"a32": "t32"}                  # the instruction set of an odd function symbol (code_intervals)


def padding(elf, start, end, isa):
    """How many bytes at the end of a function's last run of instructions [start, end) are
    alignment padding in front of a literal pool: a nop (Thumb: nop or mov r8, r8; ARM: mov r0, r0
    or nop) that ends on a word boundary right behind an instruction that does not fall through.
    GCC counts it in the symbol's size, but it never runs, and a test that has to run every
    instruction would never be complete."""
    from dread.arch.arm import Arm
    if isa not in PADDING:                  # ARM's padding only: other architectures have no pools in code
        return 0
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


def dispatch_slots(elf, code):
    """Addresses of padding slots inside functions that never run: in ARM state, the nop right
    behind an unconditional add pc, pc, ... (a switch: pc reads as the instruction + 8, so every
    target lies at + 8 or further; GCC fills the slot at + 4 with a nop) when no branch goes there.
    Left in the code, it would be an instruction of the function no input can run."""
    from dread.arch.arm import Arm
    from capstone import arm as csarm
    slots = []
    arm = Arm(isa="a32")
    for start, end, isa in code:
        if isa != "a32":
            continue
        insns = list(arm.disassembler("a32").disasm(elf.read(start, end - start), start))
        targets = {arm.target(i) for i in insns}
        for insn, after in zip(insns, insns[1:]):
            ops = insn.operands
            if (insn.id == csarm.ARM_INS_ADD and not arm.is_conditional(insn) and len(ops) == 3
                    and all(o.type == csarm.ARM_OP_REG for o in ops[:2])
                    and ops[0].reg == csarm.ARM_REG_PC and ops[1].reg == csarm.ARM_REG_PC
                    and after.address == insn.address + 4 and bytes(after.bytes) in PADDING["a32"]
                    and after.address not in targets):
                slots.append(after.address)
    return slots


def inner_padding(elf, runs):
    """Addresses of alignment nops that end a run of instructions inside a function (not its last
    run: padding() handles that one): a nop right behind an instruction that does not fall through,
    in front of data (a jump table, a pool), that no branch of the function goes to. GCC puts one
    behind ldr.w pc, [rN, rM, lsl #2] to align the table that follows; it never runs."""
    from dread.arch.arm import Arm
    out, insns_of = [], {}
    if any(isa not in PADDING for _, _, isa in runs):
        return out
    for start, end, isa in runs:
        arm = Arm(isa=isa, profile="m" if isa == "t32" else "a")
        insns_of[(start, end)] = (arm, list(arm.disassembler(isa).disasm(elf.read(start, end - start), start)))
    targets = {arm.target(i) for arm, insns in insns_of.values() for i in insns}
    for start, end, isa in runs[:-1]:
        arm, insns = insns_of[(start, end)]
        if len(insns) < 2 or insns[-1].address + insns[-1].size != end:
            continue
        last, before = insns[-1], insns[-2]
        kind, _, falls = arm.flow(before)
        if (bytes(last.bytes) in PADDING[isa] and kind in ("jump", "return", "ijump") and not falls
                and last.address not in targets):
            out.append(last.address)
    return out


def tables(elf, unnamed=False, keep=(), isa="a32"):
    """(functions, code_ranges, symbols): functions [(entry, name, insn_bytes)], code ranges
    [(start, end, isa)], symbols [(kind, address, name)]. isa: the instruction set of code without
    an ARM mapping symbol (the architecture's default)."""
    intervals = code_intervals(elf, isa)
    code = [(a, b, k) for a, b, k in intervals if k != "data"]
    def cut(code, slot, size):
        return [piece for a, b, k in code
                for piece in ([(a, slot, k), (slot + size, b, k)] if a <= slot < b else [(a, b, k)])
                if piece[0] < piece[1]]

    for slot in dispatch_slots(elf, code):          # cut out of their ranges
        code = cut(code, slot, 4)
    for sym in elf.symbols:
        if sym.kind == STT_FUNC and sym.size and sym.name:
            entry = elf.code_value(sym.value)
            runs = [(max(a, entry), min(b, entry + sym.size), k) for a, b, k in code
                    if a < entry + sym.size and entry < b]
            for slot in inner_padding(elf, runs):
                code = cut(code, slot, 2 if elf.read(slot, 2) in PADDING["t32"] and
                           any(k == "t32" for a, b, k in runs if a <= slot < b) else 4)

    def insn_bytes(lo, hi):
        return sum(max(0, min(hi, b) - max(lo, a)) for a, b, _ in code)

    funcs = {}
    for sym in elf.symbols:
        if sym.kind != STT_FUNC or not sym.size or not sym.name:
            continue
        entry = elf.code_value(sym.value)
        if entry in funcs:                      # an alias of a function already seen
            continue
        runs = [(max(a, entry), min(b, entry + sym.size), k) for a, b, k in code
                if a < entry + sym.size and entry < b]
        pad = padding(elf, runs[-1][0], runs[-1][1], runs[-1][2]) if runs else 0
        funcs[entry] = (IDENT.sub("_", sym.name), insn_bytes(entry, entry + sym.size) - pad)
    for entry, end, name in entry_labels(elf):
        if entry not in funcs:
            funcs[entry] = (IDENT.sub("_", name), insn_bytes(entry, end))
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


def startup(elf, region=None):
    """The copies a startup makes, read from the sections: [(kind, run, size, stored)], kind
    "code" or "data" for a section loaded at one address and run at another, "zero" for an
    allocated section without contents (stored None). With region (lo, hi), the memory the image
    lies in: a section with contents loaded outside it is "load" (stored None), bytes a loader puts
    into memory beside the image (QEMU's ELF loader fills RAM so, and the startup copies nothing;
    load_bytes() reads them). Entries of one kind that follow each other are merged."""
    out = []
    for s in sorted((s for s in elf.sections if s.alloc and s.size), key=lambda s: s.addr):
        if s.nobits:
            entry = ["zero", s.addr, s.size, None]
        elif region is not None and not region[0] <= s.lma < region[1]:
            entry = ["load", s.addr, s.size, None]
        elif s.lma != s.addr:
            entry = ["code" if s.code else "data", s.addr, s.size, s.lma]
        else:
            continue
        last = out[-1] if out else None
        if last and last[0] == entry[0] and last[1] + last[2] == entry[1] and \
                (last[3] is None or last[3] + last[2] == entry[3]):
            last[2] += entry[2]
        else:
            out.append(entry)
    return [tuple(e) for e in out]


def load_bytes(elf, run, size):
    """The bytes the sections hold at run addresses [run, run + size) (zero between them)."""
    out = bytearray(size)
    for s in elf.sections:
        if s.alloc and not s.nobits and s.size and s.addr < run + size and run < s.addr + s.size:
            lo, hi = max(s.addr, run), min(s.addr + s.size, run + size)
            out[lo - run:hi - run] = elf.data(s)[lo - s.addr:hi - s.addr]
    return bytes(out)


def image_region(target, image):
    """(lo, hi) of the memory region the image's base lies in (its flash), or None."""
    for r in target.memory.regions:
        if r.base <= image.base < r.end:
            return r.base, r.end
    return None


FREE_MIN = 16           # smaller gaps between sections are alignment, not room


def free_ranges(elf, base, end=None, reserved=()):
    """[(start, end)] of the gaps between the sections loaded into the payload from `base` (below
    `end`, when given; none behind the last one: that is the room behind the image), without the
    reserved ranges (the target's scratch memory: on a 16-bit MSP430 the generated inputs' buffers
    can only lie in the erased flash between the code and the vectors)."""
    spans = sorted((lma, lma + len(data)) for lma, data, _ in elf.loaded() if lma >= base and (end is None or lma < end))
    gaps, at = [], None
    for lo, hi in spans:
        if at is not None and lo - at >= FREE_MIN:
            gaps.append(((at + 7) & ~7, lo))
        at = hi if at is None else max(at, hi)
    out = []
    for lo, hi in gaps:
        pieces = [(lo, hi)]
        for rlo, rhi in reserved:
            pieces = [p for a, b in pieces for p in ((a, min(b, rlo)), (max(a, rhi), b)) if p[1] > p[0]]
        out += [((a + 7) & ~7, b) for a, b in pieces if b - ((a + 7) & ~7) >= FREE_MIN]
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


def startup_toml(copies, files=None):
    """The startup list as TOML; files: {run address: file} of the "load" entries."""
    lines = ["startup = ["]
    for kind, run, size, stored in copies:
        if kind == "load":
            lines.append('    {kind = "load", run = 0x%x, size = 0x%x, file = "%s"},' % (run, size, files[run]))
        elif stored is None:
            lines.append('    {kind = "%s", run = 0x%x, size = 0x%x},' % (kind, run, size))
        else:
            lines.append('    {kind = "%s", run = 0x%x, size = 0x%x, stored = 0x%x},' % (kind, run, size, stored))
    lines.append("]")
    return "\n".join(lines)


def free_toml(ranges):
    return "free = [%s]" % ", ".join("[0x%x, 0x%x]" % r for r in ranges)


def fixed_toml(fixed):
    return "fixed = {%s}" % ", ".join("%s = 0x%x" % kv for kv in sorted(fixed.items()))


def write_startup(toml_path, image_name, copies, key="startup", block=None):
    """Put the startup list (or another list: key and its TOML text in block) into [image.IMAGE]
    of a dread.toml, in place of the one there (a `KEY = [` line up to the line `]`, or that one
    line) or at the end of the section. The rest of the file is left as it is. Returns True when
    the file changed."""
    with open(toml_path) as f:
        lines = f.read().split("\n")
    header = "[image.%s]" % image_name
    try:
        top = next(i for i, l in enumerate(lines) if l.strip() == header)
    except StopIteration:
        raise tgt.TargetError("%s has no %s section" % (toml_path, header))
    end = next((i for i in range(top + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    block = (block if block is not None else startup_toml(copies)).split("\n")
    first = next((i for i in range(top + 1, end) if lines[i].replace(" ", "").startswith((key + "=[", key + "={"))),
                 None)
    if first is not None:
        last = next((i for i in range(first, end) if lines[i].strip() == "]" or lines[i].rstrip().endswith(("]", "}"))
                     and i == first), None)
        if last is None:
            raise tgt.TargetError("%s: the %s list in %s does not end with a line ']'" % (toml_path, key, header))
        new = lines[:first] + block + lines[last + 1:]
    else:
        at = end
        while at > top + 1 and not lines[at - 1].strip():
            at -= 1
        new = lines[:at] + block + lines[at:]
    if new == lines:
        return False
    with open(toml_path, "w") as f:
        f.write("\n".join(new))
    return True


def untyped_entries(elf):
    """The entries entry_labels made from global or weak labels without a type (not from functions
    declared without a size), but the ELF's entry: those data_labels may take back."""
    typed = {elf.code_value(s.value) for s in elf.symbols if s.kind == STT_FUNC}
    untyped = {s.value for s in elf.symbols if s.kind == STT_NOTYPE and s.bind in (STB_GLOBAL, STB_WEAK)}
    return {e for e, _, _ in entry_labels(elf)} & untyped - typed - {elf.code_value(elf.entry)}


def data_labels(target, image, elf, candidates):
    """[(start, end)] of the untyped labels among candidates (untyped_entries) that name data in an
    executable section rather than a function: nothing points at one (no call or jump of other code
    goes there, no other instruction holds its address as an immediate, no word the sections load
    equals it outside its own bytes), no code that is kept runs on into it (_start into _restart;
    padding behind a jmp runs on into nothing: its entry does not reach it),
    and its bytes do not end as code does: an instruction that does not decode (so that its
    instructions do not fill it), a call or jump out of the image, or a run on into the next label
    without a return or jump at its end through an instruction the layer finds unlikely there
    (unlikely_unreferenced: x86's port I/O and interrupt masking; libgcc's MSP430 shift routines run
    into each other through entries nothing calls, and stay functions). FreeRTOS's IA32 start-up puts the multiboot header between
    a jump and _start under a global label without a type (_mboot_hdr): as a function it read as code
    calling 0x24101205 with the host's GCC, and with Debian's for i686 (other addresses in it) as
    code that ran on into _start. A label nothing points at whose code ends (a ret) stays: dead code,
    or code entered from elsewhere."""
    from dread.program import Program
    if not candidates:
        return []
    try:
        program = Program(target, image)
    except Exception:
        return []
    fns = {e: program.by_entry[e] for e in candidates if e in program.by_entry}
    own = {a: e for e, fn in fns.items() for a in fn.insns}
    runs = [(c.run, c.run + c.size) for c in program.copies if c.kind == "code"]
    pointed = set()
    broken = {e for e, fn in fns.items() if sum(program.insn_size(a) for a in fn.insns) < fn.size}
    for fn in program.functions:
        for a in fn.insns:
            insn = program.insn(a)
            if insn is None:
                broken.add(own.get(a))
                continue
            kind, dest, _ = program.arch.flow(insn)
            refs = set(program.arch.immediates(insn))
            if kind in ("call", "jump") and isinstance(dest, int):
                t = program.code_address(dest)
                refs.add(t)
                if not program.in_image(t, 1) and not any(lo <= t < hi for lo, hi in runs):
                    broken.add(own.get(a))
            pointed |= {r for r in refs if r in fns and own.get(a) != r}
    for s in elf.sections:
        if not (s.alloc and not s.nobits and s.size):
            continue
        body = elf.data(s)
        for i in range((-s.addr) % 4, len(body) - 3, 4):
            v = int.from_bytes(body[i:i + 4], "little")
            if v in fns and not v <= s.addr + i < v + max(fns[v].size, 1):
                pointed.add(v)

    def runs_on(a):
        insn = program.insn(a)
        if insn is None:                    # (bytes that do not decode run on into nothing: MSP430's data)
            return False
        kind, _, falls = program.arch.flow(insn)
        return kind in ("next", "call", "icall") or (kind == "jump" and falls)

    def reached(fn):
        """The instructions of fn its entry reaches (padding behind a jmp is none)."""
        have, seen, work = set(fn.insns), set(), [fn.entry]
        while work:
            a = work.pop()
            if a in seen or a not in have or program.insn(a) is None:
                continue
            seen.add(a)
            kind, dest, falls = program.arch.flow(program.insn(a))
            if kind == "jump" and isinstance(dest, int):
                work.append(program.code_address(dest))
            if runs_on(a):
                work.append(a + program.insn_size(a))
        return seen
    ends = {}                               # address an instruction runs on to -> the function it ends
    for fn in program.functions:
        if fn.insns:
            last = max(fn.insns)
            if runs_on(last) and last in reached(fn):
                ends[last + program.insn_size(last)] = fn.entry
    odd = getattr(program.arch, "unlikely_unreferenced", None)
    dangling = {e for e, fn in fns.items() if fn.insns and e not in broken and runs_on(max(fn.insns))
                and odd is not None and any(program.insn(a) is not None and odd(program.insn(a)) for a in fn.insns)}
    data = set()
    while True:                             # (code falls into a label only from code that is kept)
        fallen = {t for t, e in ends.items() if t in fns and e not in data}
        now = {e for e in fns if e not in pointed and e not in fallen and (e in broken or e in dangling)}
        if now == data:
            break
        data = now
    return sorted((e, e + fns[e].size) for e in data)


def called_without_function(target, image):
    """[(entry, name, size)] for the targets of direct calls that lie in code no function holds: a
    compiler's shared code with a local label only, called with bl from several functions (a reader
    of the stripped image, Ghidra, makes such a target a function too). The size runs to the next
    function or the end of the code run. [] when the image's bytes are not there."""
    from dread.program import Program
    try:
        program = Program(target, image)
    except Exception:
        return []
    entries = sorted(f.entry for f in program.functions)
    out = {}
    for fn in program.functions:
        for a in fn.insns:
            insn = program.insn(a)
            if insn is None:
                continue
            kind, dest, _ = program.arch.flow(insn)
            if kind != "call" or not isinstance(dest, int):
                continue
            t = program.code_address(dest)
            if t in out or not program.in_image(t, 1) or program.function_at(t) is not None or \
                    program.insn(t) is None:
                continue
            end = next((e for e in entries if e > t), None)
            for lo, hi, _ in program.ranges:
                if lo <= t < hi:
                    end = hi if end is None else min(end, hi)
            if end:
                out[t] = ("FUN_%08x" % t, end - t)
    return [(t, name, size) for t, (name, size) in sorted(out.items())]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread import-elf", description=__doc__.split("\n\n")[0])
    parser.add_argument("elf")
    tgt.add_arguments(parser)
    parser.add_argument("--payload", metavar="FILE", help="also write the raw image (from the image's base)")
    parser.add_argument("--unnamed", action="store_true", help="FUN_xxxxxxxx for every function")
    parser.add_argument("--keep", default="", help="with --unnamed: names to keep, comma-separated")
    parser.add_argument("--write-startup", action="store_true",
                        help="put the startup list and the free ranges into the image's section of dread.toml "
                             "instead of printing them")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        elf = Elf(args.elf)
    except (tgt.TargetError, ElfError, OSError) as e:
        print("dread import-elf: %s" % e, file=sys.stderr)
        return 1
    functions, code, symbols = tables(elf, args.unnamed, set(filter(None, args.keep.split(","))), image.arch.isa)
    region = image_region(target, image)
    copies = startup(elf, region)
    if region is not None:
        # Code lies in the image's region, or runs from RAM where a startup copy puts it. A section
        # loaded elsewhere is loader data ("load" in the startup list): MSP430's interrupt vectors are
        # executable sections without symbols, in a flash region of their own above the scratch.
        spans = [region] + [(run, run + size) for kind, run, size, _ in copies if kind == "code"]
        inside = lambda lo, hi=None: any(a <= lo and (hi or lo + 1) <= b for a, b in spans)
        code = [c for c in code if inside(c[0], c[1])]
        dropped = {e for e, _, _ in functions if not inside(e)}
        functions = [f for f in functions if f[0] not in dropped]
        symbols = [y for y in symbols if not (y[0] == "f" and y[1] in dropped)]
    write_tables(image, functions, code, symbols)
    print("%d functions, %d code ranges, %d symbols written to %s"
          % (len(functions), len(code), len(symbols), os.path.relpath(image.config)))
    files = {}
    if args.payload:
        data, base = elf.payload(image.base, image.erased, region[1] if region else None)
        with open(args.payload, "wb") as f:
            f.write(data)
        print("%d bytes from 0x%x written to %s" % (len(data), base, args.payload))
        for kind, run, size, _ in copies:       # what the loader puts beside the image, next to it
            if kind == "load":
                path = os.path.join(os.path.dirname(os.path.abspath(args.payload)), "load-%x.bin" % run)
                with open(path, "wb") as f:
                    f.write(load_bytes(elf, run, size))
                files[run] = os.path.relpath(path, target.root)
                print("%d bytes the loader puts at 0x%x written to %s" % (size, run, path))
    elif any(kind == "load" for kind, _, _, _ in copies):
        print("dread import-elf: the image needs bytes a loader puts beside it: give --payload", file=sys.stderr)
        files = {run: "original/load-%x.bin" % run for kind, run, _, _ in copies if kind == "load"}
    if copies and args.write_startup:
        changed = write_startup(target.file, image.name, None, "startup", startup_toml(copies, files))
        print("startup list of [image.%s] %s in %s" % (image.name, "updated" if changed else "unchanged",
                                                         os.path.relpath(target.file)))
    elif copies:
        print("startup copies for [image.%s] in dread.toml:\n%s" % (image.name, startup_toml(copies, files)))
    fixer = getattr(image.arch, "fixed_from_symbols", None)
    fixed = fixer({s.name: s.value for s in elf.symbols if s.name}) if fixer else {}
    if fixed and args.write_startup:
        changed = write_startup(target.file, image.name, None, "fixed", fixed_toml(fixed))
        print("fixed registers of [image.%s] %s in %s" % (image.name, "updated" if changed else "unchanged",
                                                            os.path.relpath(target.file)))
    elif fixed:
        print("fixed registers for [image.%s] in dread.toml:\n%s" % (image.name, fixed_toml(fixed)))
    mem = target.memory
    reserved = [(mem.scratch_base, mem.scratch_base + mem.scratch_size)] if mem.scratch_base is not None else []
    free = free_ranges(elf, image.base, region[1] if region else None, reserved)
    if free and args.write_startup:
        changed = write_startup(target.file, image.name, None, "free", free_toml(free))
        print("free ranges of [image.%s] %s in %s" % (image.name, "updated" if changed else "unchanged",
                                                        os.path.relpath(target.file)))
    elif free:
        print("free ranges for [image.%s] in dread.toml:\n%s" % (image.name, free_toml(free)))
    if args.write_startup and (copies or fixed or free):    # (read again: the program then knows code that runs
        target = tgt.Target.load(target.file)               # from RAM; a dread.toml without the startup list
        image = target.image(image.name)                    # made the walk below read 0x40000000 outside the image)
    drops = data_labels(target, image, elf, untyped_entries(elf))
    if drops:
        lows = {lo for lo, _ in drops}
        names = {e: n for e, n, _ in functions}
        functions = [f for f in functions if f[0] not in lows]
        symbols = sorted([y for y in symbols if not (y[0] == "f" and y[1] in lows)] +
                         [("d", lo, names[lo]) for lo in lows], key=lambda y: (y[0] != "f", y[1]))
        for lo, hi in drops:
            code = [piece for a, b, k in code
                    for piece in ([(a, lo, k), (hi, b, k)] if a < hi and lo < b else [(a, b, k)])
                    if piece[0] < piece[1]]
        write_tables(image, functions, code, symbols)
        print("%d labels without a type that nothing points at or runs into and that do not end as code, data: %s"
              % (len(drops), ", ".join(names[lo] for lo, _ in drops)))
    called = called_without_function(target, image)
    if called:
        write_tables(image, sorted(functions + called), code, symbols)
        print("%d calls into code no function held: %s, functions of their own"
              % (len(called), ", ".join(name for _, name, _ in called)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
