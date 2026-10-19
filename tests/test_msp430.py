"""The MSP430 layer: its decoder against LLVM's disassembler (random encodings of every format,
addressing mode, register and width, with random extension words: the text and the length must be
llvm-mc's), the lengths from the first word alone, flow and register effects, and gizmo-msp430's code
against llvm-objdump."""
import os
import random
import re
import shutil
import struct
import subprocess

import pytest

from dread.arch.msp430 import Msp430, decode_fields, length_of, text_of
from conftest import gizmo_dir

needs_llvm = pytest.mark.skipif(not shutil.which("llvm-mc"), reason="needs llvm-mc")
R = random.Random(11)


def random_instruction(address):
    """Bytes of one random MSP430 instruction (first word, then extension words)."""
    kind = R.choices(["double", "single", "jump"], [70, 18, 12])[0]
    if kind == "double":
        w = R.randrange(4, 16) << 12 | R.randrange(16) << 8 | R.randrange(2) << 7 | R.randrange(2) << 6 \
            | R.randrange(4) << 4 | R.randrange(16)
    elif kind == "single":
        w = 0x1000 | R.randrange(6) << 7 | R.randrange(2) << 6 | R.randrange(4) << 4 | R.randrange(16)
    else:
        w = 0x2000 | R.randrange(8) << 10 | R.randrange(0x400)
    words = [w] + [R.choice([0, 1, 2, 0x7FFF, 0x8000, 0xFFFF, R.randrange(0x10000)])
                   for _ in range(length_of(w) // 2 - 1)]
    return struct.pack("<%dH" % len(words), *words)


CG_VALUES = (0, 1, 2, 4, 8, 0xFFFF, 0xFF)


def canonical(f):
    """Whether llvm-mc would encode the instruction the same way: an immediate that a constant
    generator gives is written with the generator, not an extension word."""
    for o in (f.src, f.dst):
        if o is not None and o.mode == "imm" and not o.cg and o.value in CG_VALUES:
            return False
    branch = f.op == "call" or (f.op == "mov" and f.dst.mode == "reg" and f.dst.reg == 0)
    if branch and f.src.mode == "imm" and f.src.cg:
        return False            # (a call or br to 0, 1, 2, 4, 8 or -1: llvm-mc writes the target as a word)
    return True


@needs_llvm
def test_the_text_assembles_back_to_the_same_bytes(tmp_path):
    """Random encodings of every format, addressing mode, register and width: the decoder's text,
    assembled by llvm-mc, gives back the bytes (what dread asm needs). llvm-mc refuses a few valid
    forms (no @rN+ source with a memory destination, no push.b, no push X(rN), no @pc source);
    those become raw words in a listing, and they may be no more than a small share."""
    address, cases = 0x8000, []
    while len(cases) < 3000:
        code = random_instruction(address)
        f = decode_fields(code, address)
        if f.op != "unknown" and f.target is None and canonical(f):
            cases.append((address, code, f))
        address += len(code)
    source = tmp_path / "t.s"
    source.write_text("".join("\t%s %s\n" % text_of(f, a) for a, _, f in cases))
    got = subprocess.run(["llvm-mc", "-triple=msp430", "-show-encoding", str(source)], capture_output=True, text=True)
    refused = {int(m.group(1)) - 1 for m in re.finditer(r"t\.s:(\d+):\d+: error", got.stderr)}
    encodings = [bytes(int(b, 16) for b in m.group(1).split(","))
                 for m in re.finditer(r"encoding: \[([0-9a-fx,]+)\]", got.stdout)]
    accepted = [c for k, c in enumerate(cases) if k not in refused]
    assert len(encodings) == len(accepted)
    assert len(refused) < len(cases) // 10, "llvm-mc refuses %d of %d" % (len(refused), len(cases))
    differ = [(text_of(f, a), code.hex(), enc.hex()) for (a, code, f), enc in zip(accepted, encodings) if enc != code]
    assert not differ, differ[:10]


def test_lengths_from_the_first_word():
    assert length_of(0x4C0D) == 2                   # mov r12, r13
    assert length_of(0x403E) == 4                   # mov #N, r14: an immediate word
    assert length_of(0x4392) == 4                   # mov #1, &N (constant generator, absolute destination)
    assert length_of(0x5292) == 6                   # add &N, &N
    assert length_of(0x12B0) == 4                   # call #N
    assert length_of(0x3FFF) == 2                   # jmp


class Insn:
    def __init__(self, address, code):
        f = decode_fields(code, address)
        self.address, self.size, self.fields, self.bytes = address, f.size, f, code[:f.size]
        self.mnemonic, self.op_str = text_of(f, address)


def test_flow_and_effects():
    arch = Msp430()
    call = Insn(0x8000, struct.pack("<HH", 0x12B0, 0x8100))
    assert arch.flow(call) == ("call", 0x8100, True)
    assert arch.flow(Insn(0x8000, struct.pack("<H", 0x4130))) == ("return", None, False)      # ret
    assert arch.flow(Insn(0x8000, struct.pack("<HH", 0x4030, 0x8200))) == ("jump", 0x8200, False)  # br #N
    assert arch.flow(Insn(0x8000, struct.pack("<H", 0x2402))) == ("jump", 0x8006, True)        # jeq $+6
    assert arch.flow(Insn(0x8000, struct.pack("<H", 0x1300))) == ("return", None, False)       # reti
    assert arch.reg_effects(Insn(0x8000, struct.pack("<H", 0x5D0C))) == ({"r12", "r13"}, {"r12", "flags"})
    assert arch.stack_kind(Insn(0x8000, struct.pack("<H", 0x120A))) == "push"                  # push r10
    assert arch.stack_kind(Insn(0x8000, struct.pack("<H", 0x413A))) == "pop"                   # pop r10
    assert arch.branch(0x8000, 0x9000, "msp430") == struct.pack("<HH", 0x4030, 0x9000)


def test_gizmo_msp430_against_llvm_objdump():
    from dread.program import Program
    from dread.target import Target
    if not shutil.which("llvm-objdump"):
        pytest.skip("needs llvm-objdump")
    target = Target.load(gizmo_dir("msp430"))
    program = Program(target)
    out = subprocess.run(["llvm-objdump", "-d", os.path.join(target.root, "original", "gizmo.elf")],
                         capture_output=True, text=True, check=True).stdout
    truth = {}
    for line in out.splitlines():
        m = re.match(r"\s*([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*\t(\S+)(?:\t(.*))?$", line)
        if m:
            truth[int(m.group(1), 16)] = (m.group(3), (m.group(4) or "").strip(), len(m.group(2).split()))
    n = 0
    for fn in program.functions:
        for insn in program.instructions(fn):
            assert insn is not None, fn.name
            assert (insn.mnemonic, insn.op_str, insn.size) == truth[insn.address], (fn.name, hex(insn.address))
            n += 1
    assert n > 1500


def test_import_elf_reads_untyped_entries_and_free_ranges():
    """libgcc's MSP430 shift routines and shared epilogues are assembly without .type: their global
    labels are entries, each running to the next; the gap between the code and the vectors is free."""
    from dread.elf import Elf
    from dread.elfimport import entry_labels, free_ranges, tables
    elf = Elf(os.path.join(gizmo_dir("msp430"), "original", "gizmo.elf"))
    labels = {name: (lo, hi) for lo, hi, name in entry_labels(elf)}
    assert labels["__mspabi_func_epilog_7"][1] - labels["__mspabi_func_epilog_7"][0] == 2
    assert labels["__mspabi_func_epilog_1"][1] - labels["__mspabi_func_epilog_1"][0] == 4    # pop r10; ret
    functions, code, _ = tables(elf, isa="msp430")
    names = {name for _, name, _ in functions}
    assert {"__mspabi_func_epilog_3", "__mspabi_srli", "__mspabi_divllu", "memcpy"} <= names
    assert any(lo <= labels["__mspabi_srli"][0] < hi for lo, hi, _ in code)
    assert free_ranges(elf, 0x8000) == [(0x9710, 0xFFE0)]


@pytest.mark.skipif(not shutil.which("msp430-elf-gcc"), reason="needs TI's MSP430 GCC")
def test_gizmo_msp430_rewritten_functions_agree(tmp_path):
    """The rewritten C over the original on the MSP430 engine: moved functions land in the image's
    free flash, and every function agrees with generated cases covering every instruction."""
    from dread import build, runner
    from dread.target import Target
    from test_runner import src_copy
    target = Target.load(gizmo_dir("msp430"))
    payload = build.build(target, target.image(), src=str(src_copy(tmp_path, "msp430")), out=str(tmp_path / "recon"),
                          quiet=True)
    with open(tmp_path / "recon" / "report.tsv") as f:
        report = f.read()
    assert "into the image's free space" in report
    suite = runner.prepare(target, target.image(), payload)
    lines = []
    failed, uncovered, _ = runner.run(list(suite.cases), out=lines.append, jobs=4)
    text = "\n".join(lines)
    assert failed == 0 and not uncovered, text
    assert "ok   checksum" in text and "ok   fmt_hex" in text
