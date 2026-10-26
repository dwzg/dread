"""The x86 layer (32-bit, i386): flow and register effects of chosen encodings, lengths up to fifteen
bytes, and gizmo-i386's two builds (the host's GCC with -m32, clang for i686-unknown-elf) listed so
that GNU as and clang rebuild them byte for byte."""
import os
import shutil

import pytest

from dread.arch.x86 import X86
from conftest import gizmo_dir


def insn(hexbytes, address=0x08000000):
    return X86().decode(bytes.fromhex(hexbytes), address)


def test_flow_and_effects():
    """Calls, returns, jumps (direct, conditional, through a register), hlt and int; the registers an
    instruction reads and writes, a write to al keeping (so reading) the rest of eax, xor of a register
    with itself reading nothing; push and pop; the conditions and their opposites; cdecl takes no
    register argument, -mregparm=3 eax, edx and ecx."""
    a = X86()
    assert a.flow(insn("e8fb000000")) == ("call", 0x08000100, True)        # call rel32
    assert a.flow(insn("c3")) == ("return", None, False)                   # ret
    assert a.flow(insn("eb10")) == ("jump", 0x08000012, False)             # jmp rel8
    assert a.flow(insn("7410")) == ("jump", 0x08000012, True)              # je rel8
    assert a.flow(insn("ffe0")) == ("ijump", "eax", False)                 # jmp eax
    assert a.flow(insn("ffd0")) == ("icall", None, True)                   # call eax
    assert a.flow(insn("f4")) == ("stop", None, False)                     # hlt
    assert a.flow(insn("cd80")) == ("icall", None, True)                   # int 0x80
    assert a.reg_effects(insn("01c8")) == ({"eax", "ecx"}, {"eax", "flags"})        # add eax, ecx
    assert a.reg_effects(insn("31c0")) == (set(), {"eax", "flags"})                 # xor eax, eax
    assert a.reg_effects(insn("b001")) == ({"eax"}, {"eax"})                        # mov al, 1
    assert a.reg_effects(insn("0f94c0")) == ({"flags", "eax"}, {"eax"})             # sete al
    assert a.stack_kind(insn("53")) == "push" and a.pushed_registers(insn("53")) == ["ebx"]
    assert a.stack_kind(insn("5b")) == "pop" and a.stack_kind(insn("89c8")) is None
    assert a.predicate(insn("7410")) == "e" and a.complement("e") == "ne" and a.complement("a") == "be"
    assert a.arg_regs(0) == [] and a.arg_regs(3) == ["eax", "edx", "ecx"]
    with pytest.raises(ValueError):
        a.arg_regs(4)


def test_lengths_take_up_to_fifteen_bytes():
    """An instruction's length from its bytes (the program database reads max_insn of them): a
    one-byte push, a seven-byte store of an immediate, a ten-byte one with a 32-bit displacement and
    immediate, an operand-size prefix."""
    a = X86()
    for hexbytes, n in (("53", 1), ("c7450400000000", 7), ("c78578563412efbeadde", 10), ("66b80100", 4)):
        assert a.insn_length(bytes.fromhex(hexbytes) + b"\x90" * 8, "i386") == n, hexbytes
    assert a.max_insn == 15


@pytest.mark.parametrize("variant, tool", [("i386", "as"), ("i386clang", "clang")])
def test_gizmo_i386_listings_rebuild_byte_for_byte(variant, tool, tmp_path):
    """dread asm --verify on both builds: the AT&T listing (jumps and calls to labels, the read-only
    data in .text as data) that GNU as (the GCC build) and clang (the clang build) assemble back to
    the image, raw bytes only where the build's table says."""
    if not shutil.which(tool):
        pytest.skip("needs " + tool)
    from dread import mkasm
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir(variant))
    program = Program(target)
    assert len(program.functions) >= 40 and program.by_name["main"]
    raw = tmp_path / "raw.txt"
    shipped = os.path.join(gizmo_dir(variant), "config", "asm_raw_words.txt")
    if os.path.exists(shipped):
        shutil.copy(shipped, raw)
    assert mkasm.main(["--target", gizmo_dir(variant), "--out", str(tmp_path / "asm"), "--verify",
                       "--raw", str(raw)]) == 0
    shipped_raw = mkasm.load_raw_words(shipped) if os.path.exists(shipped) else set()
    assert mkasm.load_raw_words(str(raw)) == shipped_raw
