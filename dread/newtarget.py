"""dread new: a new target from an ELF or a raw image: its directory, its dread.toml, the image in its
original/ and, from an ELF with symbols, its tables; then what to run next.

    dread new DIR IMAGE [--arch NAME] [--base ADDRESS] [--entry ADDRESS|SYMBOL] [--limit ADDRESS]
              [--ram BASE:SIZE]... [--io LOW:HIGH]... [--name NAME] [--title TEXT]
              [--compiler gcc|clang] [--cross PREFIX] [--unnamed]

DIR must not exist, or be empty, or hold nothing but original/ with the image in it (where dread gui puts
a file uploaded from the browser): dread new never mixes a target into other files, and never touches an
existing target (a dread.toml). IMAGE is an ELF (by its first bytes) or a raw image; it is copied into
DIR/original/ unless it lies there already. What fails on the way takes back what dread new made.

From an ELF (32-bit little-endian: ARM, RISC-V, MSP430, MIPS):
- [arch] from the ELF: its machine, ARM's build attributes (the profile, the architecture version, the
  CPU, whether floats travel in VFP registers) and RISC-V's (the extensions) or the ELF's flags; --arch
  (ARCHES) overrides it;
- [image.main]: the image is what `objcopy -O binary` writes from the sections loaded where the code
  lies (dread import-elf --payload writes it to original/NAME.bin); its base the lowest load address
  there, its entry the ELF's (by the name of the function symbol there, else the address);
- [memory]: a region for the image, regions for the RAM its writable sections and the code it runs
  from RAM use (clustered by address), extended to the initial stack pointer (an M-profile vector
  table's first word) or a stack symbol (_estack, __StackTop, __stack_top, ...) above them; the
  architecture's peripheral ranges where it has a fixed map (Cortex-M: 0x40000000-0x60000000 and the
  system range 0xe0000000-0xe0100000; the MSP430's 0x0-0x200), else none; a stack at that top; a
  scratch region and a sentinel at addresses nothing else uses (an MSP430 whose flash begins right above
  its RAM has none: the scratch then takes the erased flash between the code and the vectors, which
  become a loader's bytes, and limit ends below it);
- the tables: dread import-elf --write-startup (functions, code ranges and symbols from the ELF's symbols,
  the startup copies, the free ranges, RISC-V's gp); --unnamed gives every function a FUN_ name (a
  stripped variant of the same image, for the naming tools);
- [compiler]: the compiler the original was built with (producer: the debug information's producer of
  the compile unit that holds main, else the entry, else the most code, as pyelftools reads it, or the
  only one; GCC's records its code-generation flags, which become cflags; else the architecture's
  defaults), where this machine has it for the architecture, else the other one it has (gcc or clang;
  --compiler, --cross choose). This part depends on the compilers here (dread doctor lists them): the
  same inputs on another machine may give another [compiler].

From a raw image: --arch (ARCHES, the architectures DREAD runs) and --base (its load address) are needed;
the entry is --entry, else an M-profile vector table's reset handler at the base, else the base. RAM
comes from --ram, else from an M-profile vector table's initial stack pointer (the RAM from the SRAM
region's start up to it), else a placeholder region at an unused address; the tables are left to the
first analyses (dread ghidra finds the functions).

--ram and --io replace what dread new would take; --limit gives the room behind the image for C that
grows (the ELF does not say where the flash ends; without it, as much room again as the image takes,
rounded up to 64 KiB, a guess). Every value that is a guess carries a comment in
dread.toml saying so and what it rests on: check them against the device's documentation
(docs/target.md). What it prints at the end is what to run next: the first analyses (first_analyses:
the functions where the image has none yet, dread regflow, dread lift --check, dread readable --check),
which dread gui's Setup page starts as a chain of jobs.

Not established: that a guessed memory map is the device's (the peripheral ranges of a device without
a fixed map are not known at all, so its device accesses fault in the emulator until [memory] io names
them); that the compiler chosen is the original's version.
"""
import argparse
import os
import re
import shutil
import struct
import sys

from dread import target as tgt
from dread.elf import Elf, ElfError

SHF_WRITE = 0x1
EM_ARM, EM_MIPS, EM_MSP430, EM_RISCV = 40, 8, 105, 243
FAMILIES = {EM_ARM: "arm", EM_RISCV: "riscv", EM_MSP430: "msp430", EM_MIPS: "mips"}
COMMON_FLAGS = ["-Os", "-ffreestanding", "-fno-builtin", "-ffunction-sections", "-fno-common"]

# The architectures DREAD runs, as --arch names them: [arch], GCC's flags, clang's --target, in words.
ARCHES = {
    "arm": ({"name": "arm", "isa": "a32", "profile": "a"}, ["-march=armv4t", "-marm", "-mfloat-abi=soft"],
            "armv4t-none-eabi", "ARM state, ARMv4T (an ARM7TDMI and the like)"),
    "arm926": ({"name": "arm", "isa": "a32", "profile": "a", "cpu": "arm926"},
               ["-mcpu=arm926ej-s", "-marm", "-mfloat-abi=soft"], "armv5te-none-eabi", "ARM926EJ-S, ARM state"),
    "arm1176": ({"name": "arm", "isa": "a32", "profile": "a", "cpu": "arm1176"},
                ["-mcpu=arm1176jzf-s", "-marm", "-mfloat-abi=soft"], "armv6-none-eabi", "ARM1176JZF-S, ARM state"),
    "cortex-m0": ({"name": "arm", "isa": "t32", "profile": "m", "cpu": "cortex-m0"},
                  ["-mcpu=cortex-m0", "-mthumb", "-mfloat-abi=soft"], "thumbv6m-none-eabi", "Cortex-M0 (ARMv6-M)"),
    "cortex-m3": ({"name": "arm", "isa": "t32", "profile": "m", "cpu": "cortex-m3"},
                  ["-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft"], "thumbv7m-none-eabi", "Cortex-M3 (ARMv7-M)"),
    "cortex-m4": ({"name": "arm", "isa": "t32", "profile": "m", "cpu": "cortex-m4"},
                  ["-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft"], "thumbv7em-none-eabi",
                  "Cortex-M4 (ARMv7E-M), floats in core registers"),
    "cortex-m4f": ({"name": "arm", "isa": "t32", "profile": "m", "cpu": "cortex-m4"},
                   ["-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=hard", "-mfpu=fpv4-sp-d16"], "thumbv7em-none-eabihf",
                   "Cortex-M4 with its FPU, floats in its registers (the FPU is not modelled yet)"),
    "cortex-m7": ({"name": "arm", "isa": "t32", "profile": "m", "cpu": "cortex-m7"},
                  ["-mcpu=cortex-m7", "-mthumb", "-mfloat-abi=soft"], "thumbv7em-none-eabi", "Cortex-M7 (ARMv7E-M)"),
    "cortex-m33": ({"name": "arm", "isa": "t32", "profile": "m", "cpu": "cortex-m33"},
                   ["-mcpu=cortex-m33", "-mthumb", "-mfloat-abi=soft"], "thumbv8m.main-none-eabi",
                   "Cortex-M33 (ARMv8-M mainline)"),
    "rv32imc": ({"name": "riscv", "isa": "rv32", "extensions": "imc"}, ["-march=rv32imc", "-mabi=ilp32"],
                "riscv32-unknown-elf", "RISC-V RV32IMC"),
    "rv32imac": ({"name": "riscv", "isa": "rv32", "extensions": "imac"}, ["-march=rv32imac", "-mabi=ilp32"],
                 "riscv32-unknown-elf", "RISC-V RV32IMAC"),
    "msp430": ({"name": "msp430", "isa": "msp430"}, ["-mcpu=msp430", "-mhwmult=none"], None,
               "the 16-bit MSP430 (under way in DREAD)"),
    "mips32": ({"name": "mips", "isa": "mips32"}, ["-march=mips32r2", "-mno-abicalls", "-fno-pic"],
               "mipsel-unknown-elf", "MIPS32 release 2, little endian (under way: the listing works, the "
                                     "analyses after it are not expected to)"),
}
CPU_OF_ARCH = {11: "cortex-m0", 12: "cortex-m0", 10: "cortex-m3", 13: "cortex-m4", 16: "cortex-m33", 17: "cortex-m33",
               21: "cortex-m33"}                    # ARM's Tag_CPU_arch on an M-profile core: Unicorn's model
A_CPU_OF_ARCH = {4: "arm926", 5: "arm926", 6: "arm1176", 7: "arm1176", 9: "arm1176"}
CPU_NAMES = {"arm926ej-s": "arm926", "arm946e-s": "arm946", "arm1176jzf-s": "arm1176", "arm1136j-s": "arm1136",
             "cortex-m0plus": "cortex-m0", "cortex-m0+": "cortex-m0", "cortex-m1": "cortex-m0"}
FPU_OF_CPU = {"cortex-m4": "fpv4-sp-d16", "cortex-m7": "fpv5-d16", "cortex-m33": "fpv5-sp-d16"}
TRIPLES = {"cortex-m0": "thumbv6m-none-eabi", "cortex-m3": "thumbv7m-none-eabi", "cortex-m4": "thumbv7em-none-eabi",
           "cortex-m7": "thumbv7em-none-eabi", "cortex-m33": "thumbv8m.main-none-eabi", "arm926": "armv5te-none-eabi",
           "arm1176": "armv6-none-eabi"}
STACK_SYMBOLS = ("_estack", "__StackTop", "__stack_top", "_stack_top", "__stack", "_stack", "__initial_sp",
                 "_sp", "__sp", "stack_top", "_stack_end", "__stack_end__")
M_PERIPHERALS = [(0x40000000, 0x60000000), (0xE0000000, 0xE0100000)]   # ARMv7-M's Peripheral and System regions
MSP430_PERIPHERALS = [(0x0000, 0x0200)]             # the 16-bit MSP430's special function and peripheral registers
M_SRAM = 0x20000000                                 # ARMv7-M's SRAM region begins here
M_XN = 0xE0000000                                   # and from here everything is execute-never (no sentinel there)
MSP430_VECTORS = 0xFFE0                             # the classic MSP430's 16 interrupt vectors, reset at 0xfffe
ROOM = 0x10000                                      # a guessed limit: as much room again as the image takes,
                                                    # rounded up to this (FreeRTOS's lift check placed 16 KB of
                                                    # C behind its 60 KB image: a limit at the next 64 KiB left
                                                    # it 5 KB and dropped functions for room)


class NewTargetError(Exception):
    pass


def number(text):
    """An address or size as people write it: 0x hex, decimal, _ between digits."""
    return int(str(text).replace("_", ""), 0)


def address(text):
    try:
        return number(text)
    except ValueError:
        raise argparse.ArgumentTypeError("not an address: %r" % text)


def span(text, what):
    """BASE:SIZE (--ram) or LOW:HIGH (--io) as two numbers."""
    m = re.fullmatch(r"\s*([0-9a-fA-Fx_]+)\s*[:,]\s*([0-9a-fA-Fx_]+)\s*", text or "")
    if not m:
        raise NewTargetError("%s takes %s, not %r" % (what, "BASE:SIZE" if what == "--ram" else "LOW:HIGH", text))
    try:
        return number(m.group(1)), number(m.group(2))
    except ValueError:
        raise NewTargetError("%s: not numbers: %r" % (what, text))


def down(value, step):
    return value // step * step


def up(value, step):
    return -(-value // step) * step


# ---- what an ELF says ----

def attributes(data, vendor):
    """The file attributes of an ELF build-attributes section (ARM's .ARM.attributes with vendor "aeabi",
    RISC-V's .riscv.attributes with "riscv"): {tag: number or text}. Odd tags above 32 hold text, as do
    ARM's 4, 5, 65 and 67 and RISC-V's 5; the rest numbers (ULEB128)."""
    out = {}
    if not data or data[0:1] != b"A":
        return out

    def uleb(at):
        value, shift = 0, 0
        while at < len(data):
            b = data[at]
            at += 1
            value |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        return value, at

    def text(at):
        end = data.find(b"\0", at)
        end = len(data) if end < 0 else end
        return data[at:end].decode("latin-1"), end + 1
    at = 1
    while at + 4 <= len(data):
        length = struct.unpack_from("<I", data, at)[0]
        stop = min(len(data), at + max(length, 4))
        name, inner = text(at + 4)
        if name == vendor:
            while inner + 5 <= stop:
                tag, size = data[inner], struct.unpack_from("<I", data, inner + 1)[0]
                end = min(stop, inner + max(size, 5))
                if tag == 1:                        # Tag_File: the attributes of the whole file
                    k = inner + 5
                    while k < end:
                        attr, k = uleb(k)
                        strings = (4, 5, 65, 67) if vendor == "aeabi" else (5,)
                        if attr in strings or (attr > 32 and attr % 2):
                            out[attr], k = text(k)
                        elif vendor == "aeabi" and attr == 32:      # Tag_compatibility: a number, then text
                            _, k = uleb(k)
                            _, k = text(k)
                        else:
                            out[attr], k = uleb(k)
                inner = end
        at = stop
    return out


C_PRODUCER = re.compile(r"GNU C(\+\+)?\d*\s|(\S+ )?clang version \d")
DROPPED_FLAGS = ("-g", "-W", "-I", "-D", "-M", "-o", "-fdebug-", "-ffile-prefix-map", "-fmacro-prefix-map", "-fdiagnostics",
                 "-fno-diagnostics", "-fdump", "-fprofile", "-fplugin", "-fstack-usage", "-fbuilding-libgcc")


def unit_producers(path, addresses):
    """{producer: [code bytes of its compile units' functions, whether one of addresses lies in them]} of an
    ELF's debug information (pyelftools; ImportError without it, {} without debug information)."""
    from elftools.elf.elffile import ELFFile
    out = {}
    with open(path, "rb") as f:
        info = ELFFile(f)
        if not info.has_dwarf_info():
            return out
        for cu in info.get_dwarf_info().iter_CUs():
            top = cu.get_top_DIE()
            attr = top.attributes.get("DW_AT_producer")
            text = attr.value.decode("latin-1") if attr is not None and isinstance(attr.value, bytes) else ""
            row = out.setdefault(text, [0, False])
            for die in top.iter_children():
                low = die.attributes.get("DW_AT_low_pc")
                if die.tag != "DW_TAG_subprogram" or low is None or not isinstance(low.value, int):
                    continue
                high = die.attributes.get("DW_AT_high_pc")
                lo = low.value & ~1
                hi = lo if high is None else high.value if high.form == "DW_FORM_addr" else lo + high.value
                row[0] += max(0, hi - lo)
                row[1] = row[1] or any(lo <= a < hi for a in addresses)
    return out


def producer(elf, path=None, addresses=()):
    """(kind "gcc" or "clang" or None, the code-generation flags it recorded, its words, how it was chosen):
    the compiler of the image's own C, from the producer strings of its debug information (GCC's record
    its flags, clang's none). A C library linked in has a producer of its own (newlib's GCC beside an
    application of clang's; the strings are kept once each, so counting them says nothing): with several,
    the one whose compile unit holds main, else one of addresses (the entry), else the one with the most
    code (pyelftools reads which is which; without it several producers give none). Else .comment's
    compiler, where it names one only."""
    texts = []
    for name in (".debug_str", ".debug_line_str", ".comment"):
        s = elf.section(name)
        if s is not None and not s.nobits:
            texts += [t.decode("latin-1") for t in elf.data(s).split(b"\0") if t]
    candidates = sorted({t for t in texts if C_PRODUCER.match(t)})
    best, how = None, None
    if len(candidates) == 1:
        best, how = candidates[0], "the only producer"
    elif candidates and path:
        try:
            units = {k: v for k, v in unit_producers(path, addresses).items() if C_PRODUCER.match(k)}
        except ImportError:
            units = {}
        except Exception:                       # (debug information pyelftools cannot read)
            units = {}
        if units:
            holding = [k for k, (_, holds) in units.items() if holds]
            if len(holding) == 1:
                best, how = holding[0], "its compile unit holds main or the entry"
            else:
                best = max(sorted(units), key=lambda k: units[k][0])
                how = "the most code of the image's compile units"
    if best is None and candidates:
        return None, [], None, "several compilers made it (%s) and nothing tells which made its own code " \
                               "(pyelftools would)" % "; ".join(c.split(" -")[0] for c in candidates)
    if best is None:
        comments = sorted({t for t in texts if t.startswith("GCC:") or re.match(r"(\S+ )?clang version", t)})
        kinds = {"clang" if "clang" in t else "gcc" for t in comments}
        if len(kinds) == 1:
            return kinds.pop(), [], comments[0], "the ELF's .comment"
        return None, [], None, None
    if "clang version" in best:
        return "clang", [], best, how
    keep = [w for w in best.split()[3:] if w.startswith("-") and not w.startswith(DROPPED_FLAGS)
            and not re.search(r"=\S*/", w)]   # (a path of the machine that built it: TI's -mdevices-csv-loc=...)
    if elf.machine == EM_ARM and any(w.startswith("-mcpu=") for w in keep):    # (GCC records the -march it
        keep = [w for w in keep if not w.startswith("-march=")]               # derived from -mcpu too)
    return "gcc", list(dict.fromkeys(keep)), best, how


def arm_arch(elf):
    """(the [arch] keys, GCC's default flags, clang's triple, words) of an ARM ELF, from its attributes."""
    s = elf.section(".ARM.attributes")
    attrs = attributes(elf.data(s), "aeabi") if s is not None else {}
    arch, profile_tag, name = attrs.get(6), attrs.get(7), str(attrs.get(5, "")).lower()
    m = profile_tag == ord("M") or arch in (11, 12, 13, 16, 17, 21)
    hard = bool(elf.flags & 0x400) or attrs.get(28) == 1          # EF_ARM_ABI_FLOAT_HARD, Tag_ABI_VFP_args
    from dread.arch.arm import CPUS
    cpu = CPU_NAMES.get(name, name if name in CPUS else None)
    if m:
        cpu = cpu if cpu and cpu.startswith("cortex-m") else CPU_OF_ARCH.get(arch, "cortex-m3")
        keys = {"name": "arm", "isa": "t32", "profile": "m", "cpu": cpu}
        flags = ["-mcpu=" + cpu, "-mthumb", "-mfloat-abi=" + ("hard" if hard else "soft")]
        if hard and cpu in FPU_OF_CPU:
            flags.append("-mfpu=" + FPU_OF_CPU[cpu])
        words = "%s (M profile)" % cpu
    else:
        cpu = cpu or A_CPU_OF_ARCH.get(arch)
        isa = "t32" if attrs.get(8) == 0 and 8 in attrs else "a32"     # Tag_ARM_ISA_use 0: no ARM code
        keys = {"name": "arm", "isa": isa, "profile": "a"}
        if cpu:
            keys["cpu"] = cpu
        gcc_cpu = {"arm926": "arm926ej-s", "arm946": "arm946e-s", "arm1176": "arm1176jzf-s",
                   "arm1136": "arm1136j-s"}.get(cpu, cpu)
        flags = (["-mcpu=" + gcc_cpu] if cpu else ["-march=armv4t"]) + \
            ["-marm" if isa == "a32" else "-mthumb", "-mfloat-abi=" + ("hard" if hard else "soft")]
        words = "%s (A profile, %s)" % (cpu or "Unicorn's default core", "ARM state" if isa == "a32" else "Thumb")
    triple = TRIPLES.get(cpu, "armv4t-none-eabi" if not m else "thumbv7m-none-eabi")
    if hard and triple.startswith("thumb"):
        triple += "hf"
    return keys, flags, triple, words + (", floats in VFP registers" if hard else "") + \
        (" (from the ELF's build attributes)" if attrs else " (no build attributes: guessed)")


def riscv_arch(elf):
    """(the [arch] keys, GCC's default flags, clang's triple, words) of a RISC-V ELF: the extensions from
    .riscv.attributes, else from the ELF's flags (C) and DREAD's default (I, M)."""
    s = elf.section(".riscv.attributes")
    attrs = attributes(elf.data(s), "riscv") if s is not None else {}
    letters, guessed = set(), False
    if isinstance(attrs.get(5), str) and attrs[5].startswith("rv32"):
        parts = attrs[5][4:].split("_")
        letters = set(re.findall(r"([a-z])(?:\d+(?:p\d+)?)?", parts[0]))      # (the base and its single letters)
        letters |= {q[0] for q in parts[1:] if re.match(r"[a-z](\d|$)", q)}    # (multi-letter ones: z..., x...)
    else:
        letters, guessed = {"i", "m"} | ({"c"} if elf.flags & 1 else set()), True
    floats = {0: "ilp32", 2: "ilp32f", 4: "ilp32d"}.get(elf.flags & 6, "ilp32")
    order = "imafdc"
    march = "rv32" + "".join(c for c in order if c in letters)
    extensions = "".join(c for c in "imac" if c in letters)
    keys = {"name": "riscv", "isa": "rv32", "extensions": extensions}
    words = "RV32%s" % "".join(c.upper() for c in order if c in letters) + \
        (" (from the ELF's flags and DREAD's default: not confirmed)" if guessed else " (from the ELF's attributes)")
    if letters & {"f", "d"}:
        words += "; DREAD's RISC-V layer does not model the F and D extensions"
    return keys, ["-march=" + march, "-mabi=" + floats], "riscv32-unknown-elf", words


def stack_top(elf, ram_ok):
    """(the initial stack pointer and where it comes from) of an ELF, or (None, None)."""
    by_name = {s.name: s.value for s in elf.symbols if s.name}
    for name in STACK_SYMBOLS:
        if name in by_name and ram_ok(by_name[name]):
            return by_name[name], "the symbol %s" % name
    return None, None


def vector_words(read, at):
    """(initial stack pointer, reset handler) of an M-profile vector table: its first two words."""
    try:
        sp, reset = struct.unpack("<II", read(at, 8))
    except (ElfError, struct.error, TypeError):
        return None, None
    return sp, reset


def clusters(spans, gap):
    """Spans (lo, hi) merged where they lie closer than gap."""
    out = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1] + gap:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


# ---- the memory map ----

class Plan:
    """What dread new writes: the [arch] keys, the image, the memory map and the compiler, each guess with
    its note (notes: {key: words})."""

    def __init__(self):
        self.arch, self.notes, self.guessed = {}, {}, []
        self.base = self.end = self.entry = self.limit = self.vectors = None
        self.limit_guessed = False
        self.split = None           # (scratch base, scratch size, the flash's end): the scratch in the erased
                                    # flash between the code and the vectors (a 16-bit space with no other room)
        self.regions, self.io, self.stack, self.sentinel, self.scratch = [], [], None, None, None
        self.compiler, self.gcc_flags, self.original_flags, self.triple, self.kind = {}, [], None, None, None
        self.bits = 32
        self.elf_name = self.payload = None
        self.functions = None


def free_slot(size, used, bits, align, above=None, below=None, limit=None):
    """An aligned address where [it, it + size) touches none of the used spans, inside the address space
    (and below limit): the first above `above` (upward), else the last below `below` (downward), else any."""
    top = min(limit or (1 << bits), 1 << bits)

    def fits(at):
        return 0 < at and at + size <= top and not any(lo < at + size and at < hi for lo, hi in used)
    if above is not None:
        at = up(above, align)
        while at + size <= top:
            if fits(at):
                return at
            at += align
    if below is not None:
        at = down(below - size, align)
        while at > 0:
            if fits(at):
                return at
            at -= align
        return None
    at = align
    while at + size <= top:
        if fits(at):
            return at
        at += align
    return None


def plan_memory(p, ram_spans, top, top_from, given_ram=None, given_io=None, writable_in_image=False):
    """Fill the plan's regions (each with its note), io, stack, scratch and sentinel (see the module):
    ram_spans, where the image's writable data and its code that runs from RAM lie; top, the initial
    stack pointer where known (top_from: from what)."""
    small = p.bits == 16
    grain = 0x10 if small else 0x1000
    gap = 0x400 if small else 0x100000
    m = p.arch.get("profile") == "m"
    xn = M_XN if m else None
    if p.split:
        p.notes["limit"] = "room behind the image for C that grows, up to the scratch in the erased flash (chosen)"
    if p.limit is None and not small:           # (room behind the image for C that grows: a guess)
        room = up(p.end + max(p.end - p.base, 1), ROOM)
        above = [lo for lo, _ in list(ram_spans) + list(given_io or []) + [(b, b + s) for b, s in given_ram or []]
                 if lo >= p.end] + ([top - 1] if top is not None and top > p.end else [])
        p.limit = min([room] + [down(a, grain) for a in above if down(a, grain) > p.end])
        p.limit_guessed = True
        p.guessed.append("the room behind the image (limit)")
    image = (down(p.base, grain), up(max(p.end, p.limit or p.end), grain))
    if given_ram:
        rams = [(b, b + s) for b, s in given_ram]
        ram_note = "as given (--ram)"
    elif ram_spans:
        rams = [(down(lo, grain), up(hi, grain)) for lo, hi in clusters(ram_spans, gap)]
        if top is not None:                     # (the RAM reaches up to the stack's top above it)
            below = [k for k, (lo, hi) in enumerate(rams) if lo < top]
            k = max(below, key=lambda k: rams[k][0]) if below else None
            if k is not None and top - rams[k][1] <= (0x4000 if small else 0x1000000):
                rams[k] = (rams[k][0], max(rams[k][1], up(top, grain)))
            elif not any(lo < top <= hi for lo, hi in rams):
                rams.append((down(top - (0x200 if small else 0x2000), grain), up(top, grain)))
                rams.sort()
        ram_note = "the image's writable data%s" % (" up to %s" % top_from if top_from else
                                                    " (its size beyond that guessed)")
        if not top_from:
            p.guessed.append("the size of the RAM")
    elif top is not None and m and M_SRAM < top <= M_SRAM + 0x4000000:
        rams = [(M_SRAM, up(top, grain))]
        ram_note = "from the SRAM region's start up to %s (guessed)" % top_from
        p.guessed.append("the RAM (from the stack pointer)")
    else:
        rams, ram_note = [], ""
    merged = []
    for lo, hi in rams:
        if lo < image[1] and image[0] < hi + grain:             # (code and data in one RAM: one region)
            image = (min(image[0], lo), max(image[1], hi))
            writable_in_image = True
        else:
            merged.append((lo, hi))
    rams = merged
    if writable_in_image:
        p.regions = [{"name": "ram", "base": image[0], "size": image[1] - image[0], "kind": "ram",
                      "note": "the image and the RAM its data lies in"}]
    else:
        p.regions = [{"name": "flash", "base": image[0], "size": image[1] - image[0], "kind": "rom",
                      "note": "the image" + (" and the room behind it up to limit" if p.limit else "")}]
    if not rams and not writable_in_image:
        size = 0x800 if small else 0x10000
        at = free_slot(size, [image] + list(given_io or []), p.bits, 0x100 if small else 0x10000, above=image[1],
                       limit=xn)
        if at is None:
            raise NewTargetError("no room for a placeholder RAM region: give --ram BASE:SIZE")
        rams, ram_note = [(at, at + size)], "a placeholder: nothing says where RAM is (guessed: give --ram BASE:SIZE)"
        p.guessed.append("the RAM (a placeholder)")
    for k, (lo, hi) in enumerate(rams):
        p.regions.append({"name": "ram" if k == 0 and not writable_in_image else "ram%d" % (k + 2 - (not writable_in_image)),
                          "base": lo, "size": hi - lo, "kind": "ram", "note": ram_note if k == 0 else ""})
    if given_io is not None:
        p.io = sorted(given_io)
        p.notes["io"] = "as given (--io)"
    else:
        default = M_PERIPHERALS if m else MSP430_PERIPHERALS if p.arch.get("name") == "msp430" else []
        p.io = []
        for lo, hi in default:                  # (trimmed where a region lies)
            pieces = [(lo, hi)]
            for r in p.regions:
                pieces = [q for a, b in pieces for q in ((a, min(b, r["base"])), (max(a, r["base"] + r["size"]), b))
                          if q[1] > q[0]]
            p.io += pieces
        if m:
            p.notes["io"] = "the Cortex-M memory map's Peripheral and System regions (the device's own are narrower)"
        elif p.io:
            p.notes["io"] = "the MSP430's peripheral registers"
        else:
            p.notes["io"] = "none known: give the device's registers (--io LOW:HIGH); an access elsewhere faults"
            p.guessed.append("the device registers (none known)")
    rams_regions = [r for r in p.regions if r["kind"] == "ram"]
    home = next((r for r in rams_regions if top is not None and r["base"] < top <= r["base"] + r["size"]), None)
    if home is None:
        home = max(rams_regions, key=lambda r: r["base"] + r["size"])
        stack_top_at = home["base"] + home["size"]
        p.notes["stack"] = "the end of the region %s (guessed)" % home["name"]
        p.guessed.append("the stack's top")
    else:
        stack_top_at = top
        p.notes["stack"] = top_from or "as given"
    p.stack = (stack_top_at, min(0x200 if small else 0x2000, stack_top_at - home["base"]))
    used = [(r["base"], r["base"] + r["size"]) for r in p.regions] + list(p.io)
    highest = max(r["base"] + r["size"] for r in rams_regions)
    if p.split:                                 # (the erased flash between the code and the vectors)
        at, size, flash_end = p.split
        p.regions.append({"name": "flash2", "base": at + size, "size": flash_end - at - size, "kind": "rom",
                          "note": "the rest of the flash, with the vectors (a loader's bytes beside the image)"})
        used.append((at + size, flash_end))
    else:
        for size in ((0x4000, 0x2000, 0x1000) if small else (0x20000, 0x10000)):
            at = free_slot(size, used, p.bits, 0x100 if small else 0x100000, above=None if small else highest,
                           limit=xn)
            if at is not None:
                break
        if at is None:
            raise NewTargetError("no unused addresses for the scratch memory of generated inputs")
    p.scratch = (at, size)
    p.regions.append({"name": "scratch", "base": at, "size": size, "kind": "ram",
                      "note": "for generated inputs' buffers: %s (chosen)" % (
                          "erased flash between the code and the vectors" if p.split else "addresses nothing else uses")})
    used.append((at, at + size))
    slot = 0x10 if small else 0x10000
    sentinel = None
    for below in sorted({r["base"] for r in rams_regions if r["name"] != "scratch"}) + [image[0], None]:
        sentinel = free_slot(slot, used, p.bits, slot, below=below, limit=xn) if below is not None else \
            free_slot(slot, used, p.bits, slot, limit=xn)
        if sentinel is not None:
            break
    if sentinel is None:
        raise NewTargetError("no unused address for the return sentinel")
    p.sentinel = sentinel
    p.notes["sentinel"] = "a called function's return address: an address nothing else uses (chosen)"


# ---- the plan of an ELF or a raw image ----

def elf_plan(path, arch=None, limit=None, ram=None, io=None, unnamed=False):
    """The plan of a new target from an ELF (see the module)."""
    try:
        elf = Elf(path)
    except (ElfError, OSError) as e:
        raise NewTargetError("%s: %s" % (path, e))
    family = FAMILIES.get(elf.machine)
    if family is None:
        raise NewTargetError("%s: machine %d is none of DREAD's architectures (ARM, RISC-V, MSP430, MIPS)"
                             % (path, elf.machine))
    p = Plan()
    p.elf_name = os.path.basename(path)
    if family == "arm":
        p.arch, p.gcc_flags, p.triple, words = arm_arch(elf)
    elif family == "riscv":
        p.arch, p.gcc_flags, p.triple, words = riscv_arch(elf)
    else:
        preset = ARCHES["msp430" if family == "msp430" else "mips32"]
        p.arch, p.gcc_flags, p.triple = dict(preset[0]), list(preset[1]), preset[2]
        words = preset[3]
    if arch:
        preset = ARCHES[arch]
        if preset[0]["name"] != family:
            raise NewTargetError("--arch %s is %s, the ELF is %s" % (arch, preset[0]["name"], family))
        p.arch, p.gcc_flags, p.triple, words = dict(preset[0]), list(preset[1]), preset[2], preset[3] + " (--arch)"
    p.notes["arch"] = words
    p.bits = 16 if family == "msp430" else 32
    main = next((s.value & ~1 if family == "arm" else s.value for s in elf.symbols if s.name == "main" and s.kind == 2),
                None)
    p.kind, flags, said, how = producer(elf, path, [a for a in (main, elf.entry & ~1 if family == "arm" else elf.entry)
                                                    if a is not None])
    if family == "riscv" and flags:             # (the extensions as DREAD reads them; GCC 16 spells out more)
        flags = [f for f in flags if not f.startswith(("-march=", "-mabi="))] + \
            [f for f in p.gcc_flags if f.startswith(("-march=", "-mabi="))]
    if p.kind == "gcc" and flags and not arch:
        p.original_flags = flags
        p.notes["cflags"] = "the original's, from its debug information (%s; %s)" % (" ".join(said.split()[:3]), how)
    elif p.kind:
        p.notes["cflags"] = "the architecture's defaults (the original's compiler: %s)" % (said or p.kind)[:60]
    else:
        p.notes["cflags"] = "the architecture's defaults (%s)" % (how or "the ELF does not say which compiler made it")
    sections = [s for s in elf.sections if s.alloc and s.size]
    entry = elf.entry & ~1 if family == "arm" else elf.entry
    loaded = [s for s in sections if not s.nobits]
    if not loaded:
        raise NewTargetError("%s has no section with contents to make an image of" % path)
    writable = lambda s: bool(s.flags & SHF_WRITE)
    # the image: the sections loaded near each other (by load address) that hold the entry, else the
    # lowest; what loads elsewhere is put there by a loader (dread import-elf: "load" copies)
    groups = clusters([(s.lma, s.lma + s.size) for s in loaded], 0x400 if p.bits == 16 else 0x10000)
    group = next((g for g in groups if g[0] <= entry < g[1]), groups[0])
    p.base, p.end = group
    p.limit = limit
    in_image = lambda a: p.base <= a < p.end
    ram_spans = [(s.addr, s.addr + s.size) for s in sections
                 if (writable(s) or s.addr != s.lma) and not in_image(s.addr)
                 and (family != "msp430" or s.addr < MSP430_VECTORS)]
    if family == "msp430":                      # (the vectors at the top of the 64 KiB)
        vectors = [s for s in loaded if s.lma >= MSP430_VECTORS]
        if vectors:
            p.vectors = MSP430_VECTORS              # (the table's start, whichever vectors the image fills)
            ram_end = max([hi for _, hi in ram_spans] + [MSP430_PERIPHERALS[0][1]])
            if p.base - up(ram_end, 0x100) >= 0x1000 + 0x100 or limit is not None:
                p.end = max(p.end, max(s.lma + s.size for s in vectors))     # (part of the image: gizmo's)
            else:                               # (no room below the flash: the F449's code from 0x1100)
                gap = (up(p.end, 0x100), down(p.vectors, 0x100))
                size = min(0x4000, down((gap[1] - gap[0]) // 2, 0x100))
                if size < 0x800:
                    raise NewTargetError("no room for the scratch memory of generated inputs in the 16-bit address "
                                         "space: give --ram and --limit")
                at = up((gap[0] + gap[1] - size) // 2, 0x100)
                p.split = (at, size, 0x10000)
                p.limit = at
    # code and data in one RAM (an image a loader puts into RAM): zeroed data right behind the image
    writable_in_image = any(s.nobits and writable(s) and p.base <= s.addr < p.end + 0x10000 for s in sections)
    top, top_from = None, None
    if p.arch.get("profile") == "m":
        try:
            sp, _ = vector_words(elf.read, p.base)
        except ElfError:
            sp = None
        if sp and not in_image(sp) and sp % 4 == 0:
            top, top_from = sp, "the vector table's initial stack pointer"
    if top is None:
        near = lambda a: any(lo - 0x1000 <= a <= hi + 0x1000000 for lo, hi in ram_spans) or (
            writable_in_image and p.base <= a <= p.end + 0x1000000)
        top, top_from = stack_top(elf, near)
    plan_memory(p, ram_spans, top, top_from, ram, io, writable_in_image)
    by_address = {s.value & ~1 if family == "arm" else s.value: s.name for s in elf.symbols
                  if s.kind == 2 and s.name and re.fullmatch(r"[A-Za-z_]\w*", s.name)}
    p.entry = by_address.get(entry) if not unnamed and entry in by_address else entry
    p.functions = sum(1 for s in elf.symbols if s.kind == 2 and s.size)
    return p


def raw_plan(path, arch, base, entry=None, limit=None, ram=None, io=None):
    """The plan of a new target from a raw image (see the module)."""
    if not arch:
        raise NewTargetError("a raw image needs --arch (one of %s)" % ", ".join(ARCHES))
    if base is None:
        raise NewTargetError("a raw image needs --base (its load address)")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise NewTargetError("%s: %s" % (path, e.strerror))
    if not data:
        raise NewTargetError("%s is empty" % path)
    p = Plan()
    preset = ARCHES[arch]
    p.arch, p.gcc_flags, p.triple = dict(preset[0]), list(preset[1]), preset[2]
    p.notes["arch"] = preset[3] + " (--arch)"
    p.notes["cflags"] = "the architecture's defaults (a raw image does not say which compiler made it)"
    p.bits = 16 if p.arch["name"] == "msp430" else 32
    if base + len(data) > 1 << p.bits:
        raise NewTargetError("the image (%d bytes from 0x%x) does not fit the %d-bit address space" % (
            len(data), base, p.bits))
    p.base, p.end, p.limit = base, base + len(data), limit
    top, top_from = None, None
    read = lambda at, n: data[at - base:at - base + n] if base <= at and at + n <= base + len(data) else None
    if p.arch.get("profile") == "m":
        sp, reset = vector_words(read, base)
        if sp and not (base <= sp < base + len(data)) and sp % 4 == 0 and sp < M_XN:
            top, top_from = sp, "the vector table's initial stack pointer"
        if entry is None and reset and reset & 1 and base <= reset & ~1 < base + len(data):
            entry = reset & ~1
            p.notes["entry"] = "the vector table's reset handler"
    if p.arch["name"] == "msp430":
        if base <= MSP430_VECTORS and base + len(data) >= 0x10000:
            p.vectors = MSP430_VECTORS
            word = read(0xFFFE, 2)
            if entry is None and word:
                candidate = struct.unpack("<H", word)[0]
                if base <= candidate < base + len(data):
                    entry = candidate
                    p.notes["entry"] = "the reset vector at 0xfffe"
    p.entry = base if entry is None else entry
    if entry is None:
        p.notes["entry"] = "the base (guessed: give --entry)"
        p.guessed.append("the entry")
    plan_memory(p, [], top, top_from, ram, io)
    return p


# ---- the compiler ----

def gcc_prefix(family):
    """The prefix of this machine's GCC for an architecture, or None."""
    from dread import doctor
    prefixes = {"arm": ("arm-none-eabi-",), "riscv": doctor.RISCV_GCC, "msp430": ("msp430-elf-",)}.get(family, ())
    return next((p for p in prefixes if shutil.which(p + "gcc")), None)


def clang_has(triple):
    """Whether this machine's clang has the target of a triple."""
    from dread import doctor
    if not triple or not shutil.which("clang"):
        return False
    arch = triple.split("-")[0]
    name = "thumb" if arch.startswith("thumb") else "arm" if arch.startswith("arm") else arch
    return name in doctor.clang_targets("clang")


def takes(command, flags):
    """Whether a compiler takes flags (an empty file preprocessed with them)."""
    from dread import doctor
    return doctor.run(command + flags + ["-E", "-x", "c", "-"])[0] == 0


def choose_compiler(p, kind=None, cross=None):
    """Fill the plan's [compiler]: kind (gcc or clang), cross or triple, cflags (see the module)."""
    family = p.arch["name"]
    wanted = kind or p.kind or ("clang" if family == "mips" else "gcc")
    prefix = cross or gcc_prefix(family)
    have = {"gcc": prefix is not None and (cross is not None or shutil.which(prefix + "gcc") is not None),
            "clang": p.triple is not None and clang_has(p.triple)}
    chosen = wanted if kind or have.get(wanted) else next((k for k in ("gcc", "clang") if have[k]), wanted)
    if family == "msp430" and chosen == "clang":
        chosen = "gcc"                          # (ld.lld does not link the MSP430)
    if chosen != p.kind and p.kind is not None:
        p.notes["compiler"] = "the original was made by %s, which this machine lacks for %s" % (p.kind, family)
    if chosen == p.kind == "gcc" and p.original_flags:
        flags = list(p.original_flags)
    else:
        level = [f for f in (p.original_flags or []) if f.startswith("-O")][-1:] or ["-Os"]
        flags = list(p.gcc_flags) + level + [f for f in COMMON_FLAGS if not f.startswith("-O")]
        if p.original_flags:
            p.notes["cflags"] = "the architecture's defaults at the original's %s (its flags are GCC's)" % level[0]
    if chosen == "clang":
        flags = [f for f in flags if f not in ("-mthumb", "-marm")]
        if family == "riscv" and takes(["clang", "--target=" + p.triple], ["-march=" + _zicsr(flags)]):
            flags = [("-march=" + _zicsr(flags)) if f.startswith("-march=") else f for f in flags]
        p.compiler = {"kind": "clang", "triple": p.triple, "cflags": flags}
        found = have["clang"]
    else:
        prefix = prefix or {"arm": "arm-none-eabi-", "riscv": "riscv64-unknown-elf-", "msp430": "msp430-elf-",
                            "mips": "mipsel-elf-"}[family]
        if family == "riscv" and shutil.which(prefix + "gcc"):
            march = next((f for f in flags if f.startswith("-march=")), None)
            if march and "_zicsr" not in march and takes([prefix + "gcc"], [march + "_zicsr", "-mabi=ilp32"]):
                flags = [f + "_zicsr" if f == march else f for f in flags]
            elif march and "_zicsr" in march and not takes([prefix + "gcc"], [march, "-mabi=ilp32"]):
                flags = [f.replace("_zicsr", "") if f == march else f for f in flags]
        p.compiler = {"cross": prefix, "cflags": flags}
        found = shutil.which(prefix + "gcc") is not None
    if not found:
        p.notes["compiler"] = "not on this machine: dread doctor says what to install"
        p.guessed.append("the compiler (not installed here)")


def _zicsr(flags):
    march = next((f[len("-march="):] for f in flags if f.startswith("-march=")), "rv32imac")
    return march if "_zicsr" in march else march + "_zicsr"


# ---- dread.toml ----

def h(value, bits=32):
    return ("0x%04x" if bits == 16 else "0x%08x") % value


def toml_string(text):
    import json
    return json.dumps(text)


def render(p, name, title, original):
    """The dread.toml of a plan, with each guess's comment."""
    b = p.bits
    out = ["# %s: made by dread new from %s. A comment says where a value comes from; check what it calls a" % (
        name, p.elf_name or os.path.basename(original)),
           "# guess against the device's documentation (docs/target.md describes every key).", "",
           "[target]", "name = %s" % toml_string(name), "title = %s" % toml_string(title), "",
           "[arch]                                  # %s" % p.notes.get("arch", "")]
    for key in ("name", "isa", "profile", "cpu", "extensions"):
        if key in p.arch:
            out.append("%s = %s" % (key, toml_string(p.arch[key])))
    out += ["", "[image.main]", "original = %s" % toml_string(original), "base = %s" % h(p.base, b)]
    if p.limit is not None:
        out.append("limit = %s                # %s" % (h(p.limit, b), "room behind the image for C that grows: as "
                   "much again as the image, rounded up to 64 KiB (guessed: the flash may end elsewhere; dread new "
                   "--limit)" if p.limit_guessed else p.notes.get("limit", "as given (--limit)")))
    entry = toml_string(p.entry) if isinstance(p.entry, str) else h(p.entry, b)
    out.append("entry = %s%s" % (entry, "                # " + p.notes["entry"] if "entry" in p.notes else ""))
    if p.vectors is not None and p.vectors != p.base:
        out.append("vectors = %s" % h(p.vectors, b))
    if p.limit is None:
        out.append("# limit = ...: the end of the flash behind the image, room for C that grows (none here)")
    out += ["", "[memory]", "regions = ["]
    for r in p.regions:
        note = r.get("note", "")
        out.append("    {name = %s, base = %s, size = 0x%x%s},%s" % (
            toml_string(r["name"]), h(r["base"], b), r["size"], ', kind = "rom"' if r["kind"] == "rom" else "",
            "    # " + note if note else ""))
    out.append("]")
    out.append("io = [%s]%s" % (", ".join("[%s, %s]" % (h(lo, b), h(hi, b)) for lo, hi in p.io),
                                "    # " + p.notes["io"]))
    out.append("stack = {top = %s, size = 0x%x}    # %s" % (h(p.stack[0], b), p.stack[1], p.notes["stack"]))
    out.append("sentinel = %s    # %s" % (h(p.sentinel, b), p.notes["sentinel"]))
    out.append("scratch = {base = %s, size = 0x%x}" % (h(p.scratch[0], b), p.scratch[1]))
    out += ["", "[compiler]%s" % ("                              # " + p.notes["compiler"] if "compiler" in p.notes
                                   else "")]
    for key in ("kind", "cross", "triple"):
        if key in p.compiler:
            out.append("%s = %s" % (key, toml_string(p.compiler[key])))
    out.append("cflags = [%s]    # %s" % (", ".join(toml_string(f) for f in p.compiler["cflags"]), p.notes["cflags"]))
    return "\n".join(out) + "\n"


# ---- making it ----

def unusable(directory):
    """Why a directory cannot become a new target, or None (see the module)."""
    if not os.path.exists(directory):
        return None
    if not os.path.isdir(directory):
        return "%s is a file" % directory
    if os.path.exists(os.path.join(directory, tgt.FILE)):
        return "%s is a target already (it has a %s)" % (directory, tgt.FILE)
    entries = sorted(os.listdir(directory))
    if entries and entries != ["original"]:
        return "%s is not empty: dread new makes a target in a new or empty directory (or one that holds only " \
               "original/ with the image)" % directory
    return None


def target_name(directory):
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", os.path.basename(os.path.abspath(directory))).strip("-")
    return name or "target"


def is_elf(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError as e:
        raise NewTargetError("%s: %s" % (path, e.strerror))


def make(directory, image, arch=None, base=None, entry=None, limit=None, ram=None, io=None, name=None, title=None,
         compiler=None, cross=None, unnamed=False, out=print):
    """Make a new target in directory from image (see the module): the loaded Target. NewTargetError
    where anything refuses; what was made on the way is taken back."""
    directory = os.path.abspath(directory)
    why = unusable(directory)
    if why:
        raise NewTargetError(why)
    if not os.path.isfile(image):
        raise NewTargetError("no file %s" % image)
    elf = is_elf(image)
    if elf:
        if base is not None:
            raise NewTargetError("--base is for a raw image: an ELF says where its sections load")
        p = elf_plan(image, arch, limit, ram, io, unnamed)
        if entry is not None:
            p.entry = entry
            p.notes["entry"] = "as given (--entry)"
    else:
        if unnamed:
            raise NewTargetError("--unnamed is for an ELF with symbols")
        p = raw_plan(image, arch, base, entry, limit, ram, io)
    choose_compiler(p, compiler, cross)
    name = name or target_name(directory)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise NewTargetError("a target's name is letters, digits, ., - and _, not %r" % name)
    title = title or "%s (from %s)" % (name, os.path.basename(image))
    made = not os.path.exists(directory)
    created = []
    keep = os.path.realpath(image)
    try:
        os.makedirs(os.path.join(directory, "original"), exist_ok=True)
        inside = os.path.dirname(keep) == os.path.realpath(os.path.join(directory, "original"))
        copy = keep if inside else os.path.join(directory, "original", os.path.basename(image))
        if not inside:
            shutil.copyfile(image, copy)
            created.append(copy)
        if elf:
            stem = os.path.splitext(os.path.basename(copy))[0] or "image"
            payload = os.path.join(directory, "original", stem + ".bin")
            if os.path.realpath(payload) == os.path.realpath(copy):
                payload = os.path.join(directory, "original", stem + ".image.bin")
            original = os.path.relpath(payload, directory)
        else:
            original = os.path.relpath(copy, directory)
        toml = os.path.join(directory, tgt.FILE)
        with open(toml, "w") as f:
            f.write(render(p, name, title, original))
        created.append(toml)
        if elf:
            from dread import elfimport
            created += [os.path.join(directory, "config"), payload]
            before = set(os.listdir(os.path.join(directory, "original")))
            args = ["--target", directory, "--payload", payload, "--write-startup"] + (["--unnamed"] if unnamed else [])
            if elfimport.main(args + [copy]):
                raise NewTargetError("dread import-elf failed on %s (its words are above)" % image)
            created += [os.path.join(directory, "original", f) for f in
                        set(os.listdir(os.path.join(directory, "original"))) - before]
        target = tgt.Target.load(directory)
        img = target.image()
        img.payload()
        img.entry                                # (a symbol it names is in the tables)
    except BaseException as e:
        if made:
            shutil.rmtree(directory, ignore_errors=True)
        else:
            for path in reversed(created):
                if os.path.realpath(path) == keep:
                    continue
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                elif os.path.exists(path):
                    os.remove(path)
        if isinstance(e, tgt.TargetError):
            raise NewTargetError("the target written does not load: %s" % e)
        raise
    summary(target, p, image, out)
    return target


def summary(target, p, image, out=print):
    img = target.image()
    b = p.bits
    functions = functions_in(target)
    out("dread new: %s in %s, from %s" % (target.name, target.root, image))
    out("  arch: %s" % p.notes.get("arch", ""))
    out("  image: %s, %s-%s (%d bytes), entry %s" % (os.path.relpath(img.original, target.root), h(img.base, b),
                                                     h(img.end, b), img.end - img.base, p.entry if isinstance(p.entry, str)
                                                     else h(p.entry, b)))
    out("  functions: %s" % ("%d in %s" % (functions, os.path.relpath(img.table("functions"), target.root))
                             if functions else "none yet (the first analyses find them)"))
    out("  memory: %s; io %s; stack top %s" % (", ".join("%s %s-%s" % (r["name"], h(r["base"], b),
                                                                       h(r["base"] + r["size"], b))
                                                         for r in p.regions),
                                               ", ".join("%s-%s" % (h(lo, b), h(hi, b)) for lo, hi in p.io) or "none",
                                               h(p.stack[0], b)))
    c = target.compiler
    out("  compiler: %s %s" % (" ".join(c.command("cc")), " ".join(c.cflags)))
    if p.guessed:
        out("  guessed, to check against the device (dread.toml's comments): %s" % "; ".join(p.guessed))
    try:
        steps = first_analyses(target)
        out("next, in %s:" % target.root)
        out("  %s" % chain_line(steps))
        out("  dread gui        (the Setup page runs the same as a chain of jobs and follows it)")
    except NewTargetError as e:
        out("next: %s" % e)


def functions_in(target, image=None):
    """How many functions the image's table has (0 without one)."""
    from dread.program import read_functions
    path = target.image(image).table("functions")
    if not os.path.exists(path):
        return 0
    return len(read_functions(path))


def first_analyses(target, image=None):
    """The first analyses of a target, in order: [{"step", "command" (None for one that is done), "values"
    (the form's values: dread.guipages.run start_command), "why"}]: the functions (from the tables where
    there are any, else dread ghidra --no-decompile where Ghidra is found), dread regflow, dread lift
    --check, dread readable --check. NewTargetError where the image has no functions and no Ghidra can
    find them."""
    from dread.ghidra import find_ghidra
    n = functions_in(target, image)
    steps = []
    if n:
        steps.append({"step": "functions", "command": None, "values": None,
                      "why": "%d functions in the tables already" % n})
    elif find_ghidra(target):
        steps.append({"step": "functions", "command": "ghidra", "values": {"--no-decompile": True},
                      "why": "Ghidra finds them (the image has no symbols)"})
    else:
        raise NewTargetError("the image has no functions yet and Ghidra is not installed to find them (dread doctor "
                             "says how to get it, or make the target from an ELF with symbols)")
    steps += [{"step": "regflow", "command": "regflow", "values": {},
               "why": "the registers callers read after each call, and those each function reads at its entry"},
              {"step": "lift", "command": "lift", "values": {"--check": True},
               "why": "every function lifted to C, kept where its generated cases agree and run every instruction"},
              {"step": "readable", "command": "readable", "values": {"--check": True},
               "why": "the lifted functions turned into readable C, kept where their cases still agree"}]
    return steps


def chain_line(steps):
    """The first analyses as one command line (each after the one before succeeded)."""
    words = []
    for s in steps:
        if s["command"]:
            flags = " ".join(k for k, v in (s["values"] or {}).items() if v is True)
            words.append("dread %s%s" % (s["command"], " " + flags if flags else ""))
    return " && ".join(words)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread new", description=__doc__.split("\n\n")[0])
    parser.add_argument("dir", metavar="DIR", help="the new target's directory (new, empty, or holding only original/ "
                                                   "with the image)")
    parser.add_argument("image", metavar="IMAGE", help="an ELF (32-bit little-endian) or a raw image")
    parser.add_argument("--arch", choices=list(ARCHES), metavar="NAME", help="the architecture, one of %(choices)s: "
                        "needed for a raw image; for an ELF it overrides what the ELF says")
    parser.add_argument("--base", type=address, metavar="ADDRESS", help="a raw image's load address")
    parser.add_argument("--entry", metavar="ADDRESS|SYMBOL", help="the reset entry (default: the ELF's, a vector "
                        "table's reset handler, else the base)")
    parser.add_argument("--limit", type=address, metavar="ADDRESS", help="the end of the flash behind the image: room "
                        "for C that grows (default: as much again as the image, rounded up to 64 KiB: a guess)")
    parser.add_argument("--ram", action="append", metavar="BASE:SIZE", help="a RAM region (repeated for several); "
                        "replaces what dread new would take")
    parser.add_argument("--io", action="append", metavar="LOW:HIGH", help="a range of device registers (repeated); "
                        "replaces the architecture's")
    parser.add_argument("--name", help="the target's name (default: the directory's)")
    parser.add_argument("--title", help="a line for reports (default: the name and the image's file)")
    parser.add_argument("--compiler", choices=("gcc", "clang"), help="the compiler of the C (default: the original's "
                        "where this machine has it, else the one it has)")
    parser.add_argument("--cross", metavar="PREFIX", help="with gcc: the prefix of the GNU cross tools")
    parser.add_argument("--unnamed", action="store_true", help="an ELF's functions as FUN_xxxxxxxx (a stripped "
                        "variant of the same image)")
    args = parser.parse_args(argv)
    try:
        entry = args.entry
        if entry is not None and re.fullmatch(r"(0x[0-9a-fA-F_]+|[0-9_]+)", entry):
            entry = number(entry)
        ram = [span(r, "--ram") for r in args.ram] if args.ram else None
        io = [span(r, "--io") for r in args.io] if args.io else None
        if io and any(lo >= hi for lo, hi in io):
            raise NewTargetError("--io takes LOW:HIGH with LOW below HIGH")
        if ram and any(size <= 0 for _, size in ram):
            raise NewTargetError("--ram takes BASE:SIZE with a size above 0")
        make(args.dir, args.image, args.arch, args.base, entry, args.limit, ram, io, args.name, args.title,
             args.compiler, args.cross, args.unnamed)
    except NewTargetError as e:
        print("dread new: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
