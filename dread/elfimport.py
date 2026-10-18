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


def code_intervals(elf, isa="a32"):
    """[(start, end, kind)] of every allocated section, kind an isa or "data", from the mapping
    symbols ($a, $t for ARM; $x, code of `isa`, for RISC-V; $d data). An executable section without
    mapping symbols (GNU as for MSP430 writes none) is code of `isa` where its function symbols say
    so (their extents) and data elsewhere; with no sized function symbol it is all code. A section
    that is not executable is data."""
    mapping = {index: [(a, isa if k == "code" else k) for a, k in marks] for index, marks in elf.mapping().items()}
    out = []
    for index, s in enumerate(elf.sections):
        if not s.alloc or s.nobits or not s.size:
            continue
        marks = [(a, k) for a, k in mapping.get(index, []) if s.addr <= a < s.addr + s.size]
        if not marks:
            spans = sorted((sym.value & ~1, (sym.value & ~1) + sym.size) for sym in elf.symbols
                           if sym.kind == STT_FUNC and sym.size and sym.shndx == index) if s.code else []
            if not spans:
                out.append((s.addr, s.addr + s.size, isa if s.code else "data"))
                continue
            at = s.addr
            for lo, hi in spans:
                if lo > at:
                    out.append((at, lo, "data"))
                if hi > at:
                    out.append((max(lo, at), hi, isa))
                    at = hi
            if at < s.addr + s.size:
                out.append((at, s.addr + s.size, "data"))
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
            entry = sym.value & ~1
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


def write_startup(toml_path, image_name, copies):
    """Put the startup list into [image.IMAGE] of a dread.toml, in place of the one there (a
    `startup = [` line up to the line `]`) or at the end of the section. The rest of the file is
    left as it is. Returns True when the file changed."""
    with open(toml_path) as f:
        lines = f.read().split("\n")
    header = "[image.%s]" % image_name
    try:
        top = next(i for i, l in enumerate(lines) if l.strip() == header)
    except StopIteration:
        raise tgt.TargetError("%s has no %s section" % (toml_path, header))
    end = next((i for i in range(top + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    block = startup_toml(copies).split("\n")
    first = next((i for i in range(top + 1, end) if lines[i].replace(" ", "").startswith("startup=[")), None)
    if first is not None:
        last = next((i for i in range(first, end) if lines[i].strip() == "]" or lines[i].rstrip().endswith("]")
                     and i == first), None)
        if last is None:
            raise tgt.TargetError("%s: the startup list in %s does not end with a line ']'" % (toml_path, header))
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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread import-elf", description=__doc__.split("\n\n")[0])
    parser.add_argument("elf")
    tgt.add_arguments(parser)
    parser.add_argument("--payload", metavar="FILE", help="also write the raw image (from the image's base)")
    parser.add_argument("--unnamed", action="store_true", help="FUN_xxxxxxxx for every function")
    parser.add_argument("--keep", default="", help="with --unnamed: names to keep, comma-separated")
    parser.add_argument("--write-startup", action="store_true",
                        help="put the startup list into the image's section of dread.toml instead of printing it")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        elf = Elf(args.elf)
    except (tgt.TargetError, ElfError, OSError) as e:
        print("dread import-elf: %s" % e, file=sys.stderr)
        return 1
    functions, code, symbols = tables(elf, args.unnamed, set(filter(None, args.keep.split(","))), image.arch.isa)
    write_tables(image, functions, code, symbols)
    print("%d functions, %d code ranges, %d symbols written to %s"
          % (len(functions), len(code), len(symbols), os.path.relpath(image.config)))
    if args.payload:
        data, base = elf.payload(image.base, image.erased)
        with open(args.payload, "wb") as f:
            f.write(data)
        print("%d bytes from 0x%x written to %s" % (len(data), base, args.payload))
    copies = startup(elf)
    if copies and args.write_startup:
        changed = write_startup(target.file, image.name, copies)
        print("startup list of [image.%s] %s in %s" % (image.name, "updated" if changed else "unchanged",
                                                         os.path.relpath(target.file)))
    elif copies:
        print("startup copies for [image.%s] in dread.toml:\n%s" % (image.name, startup_toml(copies)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
