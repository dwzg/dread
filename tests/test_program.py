"""The program database on gizmo, against the ELF as objdump sees it: every function's
instructions, the mapping of code the startup copies to RAM, and the tables written by import-elf."""
import os
import re
import subprocess

import pytest

from dread.elf import Elf
from dread.program import Program
from conftest import gizmo_dir, have


def objdump(variant):
    return "llvm-objdump" if variant == "rv32" else "arm-none-eabi-objdump"


def objdump_instructions(elf_path, variant="a32"):
    """{address: mnemonic} of every instruction objdump decodes (not .word/.short/.byte)."""
    # (the ISA explicitly: older llvm-objdump does not take it from the mapping symbols)
    extra = ["--mattr=+m,+c"] if variant == "rv32" else []
    out = subprocess.run([objdump(variant), "-d"] + extra + [elf_path], capture_output=True, text=True,
                         check=True).stdout
    # (llvm-objdump 18 prints the bytes in pairs, later versions as one word)
    pattern = (r"\s*([0-9a-f]+):\s+(?:[0-9a-f]{2,8} ?)+\s*\t(\S+)" if variant == "rv32" else
               r"\s*([0-9a-f]+):\t(?:[0-9a-f]{4,8}(?: [0-9a-f]{4})?)\s*\t(\S+)")
    insns = {}
    for line in out.splitlines():
        m = re.match(pattern, line)
        if m and not m.group(2).startswith("."):
            insns[int(m.group(1), 16)] = m.group(2)
    return insns


def test_function_instructions_match_objdump(program, variant):
    if not have(objdump(variant)):
        pytest.skip("needs " + objdump(variant))
    elf_path = os.path.join(gizmo_dir(variant), "original", "gizmo.elf")
    truth = objdump_instructions(elf_path, variant)
    elf = Elf(elf_path)
    sizes = {s.value & ~1: s.size for s in elf.symbols if s.kind == 2 and s.size}
    assert len(program.functions) > 40
    for fn in program.functions:
        expected = [a for a in sorted(truth) if fn.entry <= a < fn.entry + sizes[fn.entry]]
        # Left out may be only nops that never run: the trailing alignment nop in front of a pool,
        # the slot behind an unconditional add pc, pc, ... (a switch jumps to + 8 or beyond), and an
        # alignment nop behind an instruction that does not fall through, in front of a table.
        left = [a for a in expected if a not in set(fn.insns)]
        assert [a for a in expected if a not in left] == fn.insns, fn.name
        for a in left:
            assert truth[a] in ("nop", "mov"), (fn.name, hex(a))
            before = program.insn(expected[expected.index(a) - 1])
            kind, _, falls = program.arch.flow(before)
            assert a == expected[-1] or (before.mnemonic == "add" and before.op_str.startswith("pc, pc")) \
                or (kind in ("jump", "return", "ijump") and not falls), (fn.name, hex(a))
        assert sum(program.insn_size(a) for a in fn.insns) == fn.size, fn.name


def test_ram_function_reads_its_stored_bytes(program, variant):
    elf = Elf(os.path.join(gizmo_dir(variant), "original", "gizmo.elf"))
    ram = elf.section(".ramfunc")
    fn = program.by_name["ram_crc"]
    assert fn.entry == ram.addr and program.stored(fn.entry) == ram.lma
    assert program.read(fn.entry, 16) == elf.raw[ram.offset:ram.offset + 16]
    assert program.function_at(fn.insns[2]) is fn


def test_isa_comes_from_the_code_ranges(program, variant):
    isas = {fn.isa for fn in program.functions}
    assert isas == {{"m3": "t32", "m4": "t32", "rv32": "rv32"}.get(variant, "a32")}


def test_veneer_to_the_ram_function(program, variant):
    """The linker's long branch to the RAM function is a function of its own that loads its
    target from a literal: the thunk idiom the tools have to follow. RISC-V needs none: the far call
    is auipc + jalr, which the program database joins into a call to a known address."""
    if variant == "rv32":
        ram = program.by_name["ram_crc"].entry
        calls = [insn.address for fn in program.functions for insn in program.instructions(fn)
                 if insn is not None and program.arch.flow(insn)[:2] == ("call", ram)]
        assert calls and all(program.in_image(a) for a in calls)
        return
    veneer = program.by_name["__ram_crc_veneer"]
    insn = program.decode(veneer.insns[0])
    address, size = program.arch.literal(insn, veneer.isa)
    assert program.word(address) & ~1 == program.by_name["ram_crc"].entry


def test_decode_every_instruction(program):
    for fn in program.functions:
        for insn in program.instructions(fn):
            assert insn is not None, fn.name


def test_names_and_annotations(program):
    main = program.by_name["main"]
    assert program.name_at(main.entry) == "main"
    assert program.name_at(0xDEAD0000) is None
    assert any(name == "g_config" for kind, a, name in program.symbols() if kind == "d")


SHARED_POOL_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global f, g, h
    .type f, %function
    .thumb_func
f:  ldr     r0, shared      @ a constant behind g: a compiler's table that several functions load from
    bx      lr
    .type g, %function
    .thumb_func
g:  ldr     r0, own
    bx      lr
    .align 2
own:    .word 0x55667788    @ g's own pool
shared: .word 0x11223344
    .type h, %function
    .thumb_func
h:  bx      lr
"""


@pytest.mark.skipif(not (have("arm-none-eabi-as") and have("arm-none-eabi-ld")), reason="needs arm-none-eabi binutils")
def test_a_pool_another_function_loads_is_no_part_of_an_extent(tmp_path):
    """A function's extent runs to the next function, its pools included, but not into a pool
    another function loads from with a pc-relative load: a compiler that shares its constant tables
    between functions made such a load count as a load of code (another function's extent), and
    every generated input of a function that only loads a constant was thrown away."""
    from test_bench import TARGET
    from dread import autocase, elfimport
    from dread.target import Target
    (tmp_path / "t.s").write_text(SHARED_POOL_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "f", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    symbols = {s.name: s.value for s in Elf(elf).symbols if s.name in ("own", "shared")}
    owners = p.extent_owners()
    g = [f.name for f in p.functions].index("g") + 1
    assert owners[(symbols["own"] - p.base) >> 1] == g                 # its own pool: its extent
    assert owners[(symbols["shared"] - p.base) >> 1] == 0              # another's constants: nobody's
    gen = autocase.Generator(t, t.image(), program=p)
    m = gen.machine()
    m.trace_reads(t.image().base, t.image().end, gen.code, gen.owners)
    assert m.call(p.by_name["f"].entry, max_instructions=100).ret == 0x11223344 and not m.code_reads


INNER_POOL_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global f, g, h
    .type f, %function
    .thumb_func
f:  ldr     r0, shared      @ a constant inside g, behind a branch: where an old compiler puts its pools
    bx      lr
    .type g, %function
    .thumb_func
g:  ldr     r0, own
    b       1f
    .align 2
shared: .word 0x11223344
own:    .word 0x55667788
1:  bx      lr
    .type h, %function
    .thumb_func
h:  bx      lr
"""


@pytest.mark.skipif(not (have("arm-none-eabi-as") and have("arm-none-eabi-ld")), reason="needs arm-none-eabi binutils")
def test_a_pool_inside_a_function_another_loads_is_no_part_of_its_extent(tmp_path):
    """The same for a pool inside a function, behind a branch: an old ARM compiler's functions load
    from pools inside each other, and nearly every generated input of its firmware was thrown away
    as loading code. The function's own pool there stays its extent."""
    from test_bench import TARGET
    from dread import autocase, elfimport
    from dread.target import Target
    (tmp_path / "t.s").write_text(INNER_POOL_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "f", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    symbols = {s.name: s.value for s in Elf(elf).symbols if s.name in ("own", "shared")}
    owners = p.extent_owners()
    g = [f.name for f in p.functions].index("g") + 1
    assert owners[(symbols["own"] - p.base) >> 1] == g                 # its own pool: its extent
    assert owners[(symbols["shared"] - p.base) >> 1] == 0              # another's constants: nobody's
    gen = autocase.Generator(t, t.image(), program=p)
    m = gen.machine()
    m.trace_reads(t.image().base, t.image().end, gen.code, gen.owners)
    assert m.call(p.by_name["f"].entry, max_instructions=100).ret == 0x11223344 and not m.code_reads


CALLED_LABEL_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global f, add2
    .type f, %function
    .thumb_func
f:      push    {r6, lr}
        movs    r6, #5
        bl      tail            @ a call into code with a local label only (a compiler's shared code)
        pop     {r6, pc}
    .size f, . - f
tail:   mov     r1, r6
        b.w     add2
    .type add2, %function
    .thumb_func
add2:   adds    r0, r0, r1
        bx      lr
    .size add2, . - add2
"""


@pytest.mark.skipif(not (have("arm-none-eabi-as") and have("arm-none-eabi-ld")), reason="needs arm-none-eabi binutils")
def test_import_makes_a_called_label_a_function(tmp_path):
    """A call's target in code no function holds (a second compiler's older version calls its shared
    code by a local label: the lifter refused the caller, "calls into the middle of something")
    becomes a function of its own, FUN_address, up to the next function."""
    from test_bench import TARGET
    from dread import elfimport
    from dread.target import Target
    (tmp_path / "t.s").write_text(CALLED_LABEL_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "f", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    tail = [s.value for s in Elf(elf).symbols if s.name == "tail"][0]
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    fn = p.by_entry[tail]
    assert fn.name == "FUN_%08x" % tail and fn.insns == [tail, tail + 2]


RISCV_TARGET = """[target]
name = "mapping"
[arch]
name = "riscv"
isa = "rv32"
extensions = "imac"
[memory]
regions = [{name = "rom", base = 0x80000000, size = 0x10000, kind = "rom"},
           {name = "ram", base = 0x80080000, size = 0x10000}]
stack = {top = 0x80090000, size = 0x1000}
sentinel = 0x7fff0000
[image.main]
original = "original/t.bin"
base = 0x80000000
[compiler]
cross = "riscv64-elf-"
cflags = ["-march=rv32imac_zicsr", "-mabi=ilp32"]
"""

UNMARKED_FRONT_S = """
    .text
    .globl front, port_entry
    .type front, @function
front:  addi a0, a0, 1
        addi a0, a0, 2
        ret
    .size front, .-front
port_entry:                     # an untyped global label (the port's assembly names its routines so)
        addi a0, a0, 3
        ret
"""
MARKED_S = """
    .text
    .globl marked
    .type marked, @function
marked: addi a0, a0, 4
        ret
    .size marked, .-marked
    .word 0x12345678            # data at the end: $d
"""
UNMARKED_AFTER_S = """
    .text
    .globl after
    .type after, @function
after:  addi a0, a0, 5
        ret
    .size after, .-after
"""


def test_objects_without_mapping_symbols_beside_objects_with_them(tmp_path):
    """clang 16 writes no RISC-V mapping symbols ($x, $d), GNU as and LLVM 17 do; picolibc's objects
    put a few into the same .text, and everything of clang's in front of the first was taken for
    data: 299 of 315 FreeRTOS functions imported without code bytes, and the lift check stopped on
    the first. Here GNU as objects with their mapping symbols stripped stand for clang 16's: one in
    front of a marked object (a function and an untyped global label) and one after its closing $d."""
    from conftest import RISCV_CROSS, RISCV_MARCH, riscv_toml
    if RISCV_CROSS is None:
        pytest.skip("needs a RISC-V GCC")
    from dread import elfimport
    from dread.target import Target
    objs = []
    for name, text in (("front", UNMARKED_FRONT_S), ("marked", MARKED_S), ("after", UNMARKED_AFTER_S)):
        (tmp_path / (name + ".S")).write_text(text)
        obj = str(tmp_path / (name + ".o"))
        subprocess.run([RISCV_CROSS + "gcc", "-march=" + RISCV_MARCH, "-mabi=ilp32", "-c", "-o", obj,
                        str(tmp_path / (name + ".S"))], check=True)
        if name != "marked":
            subprocess.run([RISCV_CROSS + "objcopy", "--wildcard", "--strip-symbol=$x*", "--strip-symbol=$d*", obj],
                           check=True)
        objs.append(obj)
    if not any(s.name.startswith("$") for s in Elf(objs[1]).symbols):
        pytest.skip("this binutils writes no RISC-V mapping symbols")
    elf = str(tmp_path / "t.elf")
    subprocess.run([RISCV_CROSS + "ld", "-m", "elf32lriscv", "-Ttext=0x80000000", "-e", "front", "-o", elf] + objs,
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml(RISCV_TARGET))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    sym = {s.name: s.value for s in Elf(elf).symbols if s.name}
    assert len(p.by_name["front"].insns) == 3          # (none before: data in front of the first $x)
    assert len(p.by_name["after"].insns) == 2          # (none before: inside the marked object's $d)
    assert len(p.by_name["marked"].insns) == 2
    ranges = [tuple(int(x, 16) for x in line.split("\t")[:2])
              for line in open(tmp_path / "config" / "code_ranges.tsv") if line[0] != "#"]
    assert any(lo <= sym["port_entry"] < hi for lo, hi in ranges)      # code, not data
    assert "port_entry" not in p.by_name                # (code, not a function: as another build's marks)
    word = sym["marked"] + 4
    assert not any(lo <= word < hi for lo, hi in ranges)               # the marked data stays data


def test_a_constant_is_shown_with_the_text_function_or_table_it_points_to():
    """dread.constants pointed_note: text, a function's entry (the Thumb bit aside), a table whose
    words point to texts and functions (a localized message: a pointer a language), else nothing."""
    import struct
    import types
    from dread.constants import pointed_note
    base, data = 0x1000, bytearray(0x400)
    data[0x100:0x110] = struct.pack("<4I", 0x1200, 0x1301, 5, 0)       # a table at 0x1100
    data[0x200:0x206] = b"HELLO\0"
    data[0x380:0x384] = struct.pack("<I", 0x9999)                        # a word pointing nowhere

    class Image:
        def in_image(self, a, n=1):
            return base <= a and a + n <= base + len(data)

        def read(self, a, n):
            return bytes(data[a - base:a - base + n])

        def word(self, a):
            return struct.unpack_from("<I", data, a - base)[0]
    program = Image()
    program.by_entry = {0x1300: types.SimpleNamespace(entry=0x1300, name="draw")}
    assert pointed_note(program, 0x1200) == '"HELLO"'
    assert pointed_note(program, 0x1301) == "&draw"
    assert pointed_note(program, 0x1100) == '-> {"HELLO", draw, 0x5, 0x0, ...}'
    assert pointed_note(program, 0x1380) is None                        # (no text, no function)
    assert pointed_note(program, 0x1102) is None and pointed_note(program, 0x40) is None


def test_a_long_text_shows_its_start_and_its_end():
    """dread.constants string_at: a text longer than the limit shows its first third and its end (a
    path's file name: an assertion's __FILE__ names the source file it is in); one without an end
    within TEXT_SCAN characters shows its start."""
    import struct
    from dread import constants
    base, data = 0x1000, bytearray(0x400)
    path = b"/home/someone/work/rtos/FreeRTOS/Source/portable/MemMang/heap_4.c"
    data[0x100:0x100 + len(path) + 1] = path + b"\0"
    data[0x200:0x200 + constants.TEXT_SCAN] = b"x" * constants.TEXT_SCAN
    data[0x380:0x386] = b"short\0"

    class Image:
        def in_image(self, a, n=1):
            return base <= a and a + n <= base + len(data)

        def read(self, a, n):
            return bytes(data[a - base:a - base + n])

        def word(self, a):
            return struct.unpack_from("<I", data, a - base)[0]
    program = Image()
    assert constants.string_at(program, 0x1100) == path[:16].decode() + "..." + path[-32:].decode()
    assert constants.string_at(program, 0x1200) == "x" * 48 + "..."
    assert constants.string_at(program, 0x1380) == "short"
    assert constants.pointed_note(program, 0x1100).endswith('MemMang/heap_4.c"')


def _bl(isa, at, to):
    """A bl from `at` to `to`, encoded for `isa` (a32 or t32)."""
    import struct
    if isa == "a32":
        return struct.pack("<I", 0xeb000000 | (((to - at - 8) >> 2) & 0xffffff))
    off = (to - at - 4) & 0x1ffffff
    s, i1, i2 = off >> 24, (off >> 23) & 1, (off >> 22) & 1
    j1, j2 = (1 - i1) ^ s, (1 - i2) ^ s
    return struct.pack("<2H", 0xf000 | (s << 10) | ((off >> 12) & 0x3ff),
                       0xd000 | (j1 << 13) | (j2 << 11) | ((off >> 1) & 0x7ff))


def test_a_call_out_of_the_image_is_an_ordinary_call(program, variant):
    """A bl to an address outside the image (code another loader puts there at run time: an overlay
    module the resident code calls) is an ordinary call, and the program has no instruction there.
    mark_call looks at the target for a stub, and Program.insn read the bytes there, which raised
    TargetError and stopped dread asm and dread lift (2026-10-30)."""
    if program.arch.name != "arm":
        pytest.skip("ARM's mark_call")
    fn = program.functions[0]
    outside = program.image.base + len(program.data) + 0x1000
    insn = program.arch.decode(_bl(fn.isa, fn.entry, outside), fn.entry, fn.isa)
    assert program.arch.target(insn) & ~1 == outside
    program.arch.mark_call(program, insn)
    assert not hasattr(insn, "call_via") and not getattr(insn, "dispatch_call", False)
    assert program.insn(outside) is None
