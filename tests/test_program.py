"""The program database on gizmo, against the ELF as objdump sees it: every function's
instructions, the mapping of code the startup copies to RAM, and the tables written by import-elf."""
import os
import re
import subprocess

import pytest

from dread.elf import Elf
from dread.program import Program
from conftest import gizmo_dir, have


def objdump_instructions(elf_path):
    """{address: mnemonic} of every instruction objdump decodes (not .word/.short/.byte)."""
    out = subprocess.run(["arm-none-eabi-objdump", "-d", elf_path], capture_output=True, text=True,
                         check=True).stdout
    insns = {}
    for line in out.splitlines():
        m = re.match(r"\s*([0-9a-f]+):\t(?:[0-9a-f]{4,8}(?: [0-9a-f]{4})?)\s*\t(\S+)", line)
        if m and not m.group(2).startswith("."):
            insns[int(m.group(1), 16)] = m.group(2)
    return insns


@pytest.mark.skipif(not have("arm-none-eabi-objdump"), reason="needs arm-none-eabi-objdump")
def test_function_instructions_match_objdump(program, variant):
    elf_path = os.path.join(gizmo_dir(variant), "original", "gizmo.elf")
    truth = objdump_instructions(elf_path)
    elf = Elf(elf_path)
    sizes = {s.value & ~1: s.size for s in elf.symbols if s.kind == 2 and s.size}
    assert len(program.functions) > 40
    padded = 0
    for fn in program.functions:
        expected = [a for a in sorted(truth) if fn.entry <= a < fn.entry + sizes[fn.entry]]
        if fn.insns != expected:                # only the trailing alignment nop may be left out
            assert fn.insns == expected[:-1] and truth[expected[-1]] in ("nop", "mov"), fn.name
            padded += 1
        assert fn.insns == expected or fn.insns == expected[:-1], fn.name
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
    assert isas == ({"t32"} if variant == "m3" else {"a32"})


def test_veneer_to_the_ram_function(program):
    """The linker's long branch to the RAM function is a function of its own that loads its
    target from a literal: the thunk idiom the tools have to follow."""
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
