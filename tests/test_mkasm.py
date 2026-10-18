"""dread asm: the listing of every gizmo build reassembles to the image, byte for byte."""
import os

import pytest

from dread import mkasm
from dread.program import Program
from conftest import have

needs_binutils = pytest.mark.skipif(not have("arm-none-eabi-as"), reason="needs arm-none-eabi binutils")


@pytest.mark.rv32
def test_listing_rebuilds_identically(target, variant, tmp_path):
    tools = ("clang", "ld.lld", "llvm-objcopy") if variant == "rv32" else ("arm-none-eabi-as",)
    if not all(have(t) for t in tools):
        pytest.skip("needs " + ", ".join(tools))
    raw_table = tmp_path / "asm_raw_words.txt"          # not the target's: keep the checkout clean
    out = tmp_path / "asm"
    rc = mkasm.main(["--target", target.root, "--verify", "--out", str(out), "--build", str(tmp_path / "b"),
                     "--raw", str(raw_table)])
    assert rc == 0
    raw = mkasm.load_raw_words(str(raw_table))
    assert len(raw) <= 2                    # a32, rv32: none; m3: the veneer's ldr.w pc, [pc, #-0]
    text = (out / "firmware.s").read_text()
    assert ".include" in text and (".option exact" if variant == "rv32" else ".syntax unified") in text
    body = "".join(p.read_text() for p in out.glob("[0-9a-f]*.s"))
    assert "parse_kv:" in body and ("# ---- main (" if variant == "rv32" else "@ ---- main (") in body


def test_thumb_it_conditions_survive_decoding(variant, program):
    """Instructions inside an IT block carry the IT's condition when decoded through Program."""
    if variant != "m3":
        pytest.skip("Thumb only")
    seen = 0
    for fn in program.functions:
        insns = program.instructions(fn)
        for i, insn in enumerate(insns):
            if insn is not None and insn.mnemonic.startswith("it"):
                count = len(insn.mnemonic) - 1          # it, itt, ite, ittt, ...
                for follower in insns[i + 1:i + 1 + count]:
                    assert program.arch.is_conditional(follower), (fn.name, follower.mnemonic)
                    seen += 1
    assert seen > 5


@pytest.mark.rv32
def test_labels_only_within_one_copy(target, tmp_path):
    """The RAM function is written at its stored address with labels for its own branches."""
    prog = Program(target)
    listing = mkasm.Listing(prog, set(), target.title)
    listing.decode()
    ram = prog.by_name["ram_crc"]
    stored = prog.stored(ram.entry)
    assert stored in listing.funcs and listing.funcs[stored][0] == "ram_crc"
    inner = [st for st in listing.ins if stored <= st < stored + 0x100 and isinstance(listing.ins[st][1], tuple)]
    assert inner, "the RAM function's own branches should use labels"
