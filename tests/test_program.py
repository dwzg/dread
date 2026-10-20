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
