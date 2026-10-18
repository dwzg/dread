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


@pytest.mark.rv32
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


@pytest.mark.rv32
def test_ram_function_reads_its_stored_bytes(program, variant):
    elf = Elf(os.path.join(gizmo_dir(variant), "original", "gizmo.elf"))
    ram = elf.section(".ramfunc")
    fn = program.by_name["ram_crc"]
    assert fn.entry == ram.addr and program.stored(fn.entry) == ram.lma
    assert program.read(fn.entry, 16) == elf.raw[ram.offset:ram.offset + 16]
    assert program.function_at(fn.insns[2]) is fn


@pytest.mark.rv32
def test_isa_comes_from_the_code_ranges(program, variant):
    isas = {fn.isa for fn in program.functions}
    assert isas == {{"m3": "t32", "rv32": "rv32"}.get(variant, "a32")}


@pytest.mark.rv32
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


@pytest.mark.rv32
def test_decode_every_instruction(program):
    for fn in program.functions:
        for insn in program.instructions(fn):
            assert insn is not None, fn.name


@pytest.mark.rv32
def test_names_and_annotations(program):
    main = program.by_name["main"]
    assert program.name_at(main.entry) == "main"
    assert program.name_at(0xDEAD0000) is None
    assert any(name == "g_config" for kind, a, name in program.symbols() if kind == "d")
