"""The ARC layer: decoding against binutils' objdump, flow, register effects, and gizmo-arc.

tests/data/arc_objdump.tsv holds random 16-bit and 32-bit instructions with objdump's text
(tests/arc_vectors.py writes it), so the decoder's test needs no toolchain; where arc-elf32-objdump
is installed, a fresh random set is compared too. The gizmo-arc listing test needs arc-elf32-as.
"""
import random
import shutil

import pytest

import arc_vectors
from conftest import gizmo_dir
from dread.arch import get
from dread.arch.arc_decode import EXT_OTHER, decode

ARC = get({"name": "arc", "isa": "arc600"})


def agrees(code, address, size, mnem, ops):
    """Whether the decoder gives objdump's (size, mnemonic, operands); objdump's .word and .short
    (no instruction) must be "unknown" of the same size, and major 0x05's DSP operations, which
    the engine does not run, only have their size compared."""
    f = decode(code, address)
    if mnem in (".word", ".short"):
        return f.op == "unknown" or f.op == "ext"
    if f.op == "ext" or mnem.split(".")[0] in EXT_OTHER.values():
        return f.size == size
    return (f.size, f.mnemonic, f.op_str) == (size, mnem, ops)


def test_the_decoder_agrees_with_objdumps_vectors():
    rows = arc_vectors.read()
    assert len(rows) > 4000
    wrong = [(c.hex(), m, o, decode(c, a)) for c, a, s, m, o in rows if not agrees(c, a, s, m, o)]
    assert not wrong, wrong[:10]


@pytest.mark.skipif(not arc_vectors.available(), reason="needs arc-elf32-objdump")
def test_the_decoder_agrees_with_objdump_on_fresh_words():
    codes = arc_vectors.random_codes(random.Random(), 1000, 3000)
    out = arc_vectors.objdump(codes)
    wrong = [(c.hex(), o, decode(c, arc_vectors.BASE + i * arc_vectors.SLOT))
             for i, (c, o) in enumerate(zip(codes, out))
             if o is not None and not agrees(c, arc_vectors.BASE + i * arc_vectors.SLOT, *o)]
    assert not wrong, wrong[:10]


def test_majors_8_to_11_are_32_bit():
    # ARCompact's 16-bit instructions are majors 0x0c-0x1f; 0x08-0x0b are 32-bit extension space
    # (objdump: a 4-byte .word), which a first decoder took for 16-bit.
    for major in (0x08, 0x09, 0x0A, 0x0B):
        code = arc_vectors.word32(major << 27) + bytes(4)
        assert ARC.insn_length(code) == 4 and decode(code, 0).op == "unknown"


def test_long_immediates():
    assert ARC.insn_length(bytes.fromhex("0a20800f34127856")) == 8          # mov r0,0x12345678
    insn = ARC.decode(bytes.fromhex("0a20800f34127856"), 0)
    assert (insn.mnemonic, insn.op_str, insn.fields.limm) == ("mov", "r0,0x12345678", 0x12345678)
    assert ARC.insn_length(bytes.fromhex("cf7034127856")) == 6              # mov_s r0,limm
    assert ARC.decode(bytes.fromhex("0a20800f"), 0) is None                 # the limm is missing


def flow_of(hexbytes, address):
    insn = ARC.decode(bytes.fromhex(hexbytes), address)
    return ARC.flow(insn), insn.fields.delay


def test_flow():
    # (bytes from arc-elf32-as, laid out from 0; label t at 0)
    assert flow_of("e107cfff", 0x22) == (("jump", 0, False), False)         # b t
    assert flow_of("de0fcfff", 0x26) == (("call", 0, True), False)          # bl t
    assert flow_of("d807c1ff", 0x2A) == (("jump", 0, True), False)          # beq t
    assert flow_of("2020c007", 0x2E) == (("return", None, False), False)    # j [blink]
    assert flow_of("22208000", 0x32) == (("icall", None, True), False)      # jl [r2]
    assert flow_of("cd084080", 0x36) == (("jump", 0, True), False)          # breq r0,r1,t
    assert flow_of("e07e", 0x46) == (("return", None, False), False)        # j_s [blink]
    assert flow_of("e07f", 0x46) == (("return", None, False), True)         # j_s.d [blink]
    assert flow_of("e07c", 0x46) == (("return", None, True), False)         # jeq_s [blink]
    assert flow_of("40f2", 0x40) == (("jump", 0xC0, True), False)           # beq_s to 0xc0
    assert flow_of("20204087", 0)[0] == ("return", None, False)             # j.f [ilink1]
    assert flow_of("2020800f00000010", 0) == (("jump", 0x1000, False), False)   # j 0x1000
    assert flow_of("a8204001", 0x11A)[0] == ("jump", 0x122, True)           # lp 10: to the loop's end
    assert ARC.loop_body(ARC.decode(bytes.fromhex("a8204001"), 0x11A)) == (0x11E, 0x122)
    assert flow_of("ff7f", 0)[0][0] == "stop"                               # brk_s


def test_register_effects():
    insn = ARC.decode(bytes.fromhex("00218000"), 0)                         # add r0,r1,r2
    assert ARC.reg_effects(insn) == ({"r1", "r2"}, {"r0"})
    insn = ARC.decode(bytes.fromhex("c020c100"), 0)                         # add.eq r0,r0,r3 (P = 3)
    reads, writes = ARC.reg_effects(insn)
    assert "flags" in reads and "r0" in reads and writes == {"r0"}
    insn = ARC.decode(bytes.fromhex("04140434"), 0)                         # ld.ab r4,[sp,4] (a pop)
    assert ARC.stack_kind(insn) == "pop" and ARC.reg_effects(insn) == ({"sp"}, {"r4", "sp"})
    insn = ARC.decode(bytes.fromhex("f1c0"), 0)                             # push_s blink
    assert ARC.stack_kind(insn) == "push" and ARC.reg_effects(insn) == ({"blink", "sp"}, {"sp"})
    insn = ARC.decode(bytes.fromhex("0c218080"), 0)                         # cmp r1,r2
    assert ARC.reg_effects(insn) == ({"r1", "r2"}, {"flags"})
    insn = ARC.decode(bytes.fromhex("fc1c48b3"), 0)                         # st.aw r13,[sp,-4] (a push)
    assert ARC.stack_kind(insn) == "push" and ARC.reg_effects(insn) == ({"r13", "sp"}, {"sp"})


def test_registers():
    assert ARC.reg_name("r31") == "blink" and ARC.reg_name("blink") == "blink"
    assert ARC.reg_id("sp") == 28 and ARC.reg_id("pc") == 64
    assert ARC.arg_regs(10) == ["r%d" % i for i in range(8)]
    assert ARC.isa_bit == 0


def test_branches_for_the_overlay_decode_to_their_destination():
    for src, dst in ((0x1000, 0x2000), (0x2002, 0x1000), (0x0, 0xFFFFFC)):
        insn = ARC.decode(ARC.branch(src, dst), src)
        assert ARC.flow(insn) == ("jump", dst, False)
        insn = ARC.decode(ARC.branch(src, dst & ~3, link=True), src)
        assert ARC.flow(insn) == ("call", dst & ~3, True)
    insn = ARC.decode(ARC.far_branch(0, 0x12345678), 0)
    assert ARC.flow(insn) == ("jump", 0x12345678, False)
    with pytest.raises(ValueError):
        ARC.branch(0, 1 << 25)


def test_gizmo_arc_decodes_and_its_jumps_land_on_code():
    """From each function's entry, along its flow (delay slots and loop ends included, up to an
    indirect jump: script_run's and handle_key's switch tables are data behind theirs), every
    instruction decodes and every direct jump and call lands on an instruction or an entry."""
    from dread.program import Program
    from dread.target import Target
    p = Program(Target.load(gizmo_dir("arc")))
    assert len(p.functions) > 50
    starts = {a for fn in p.functions for a in fn.insns}
    for fn in p.functions:
        todo, seen = [fn.entry], set()
        while todo:
            a = todo.pop()
            if a in seen or not fn.entry <= a < fn.entry + fn.size:
                continue
            seen.add(a)
            insn = p.insn(a)
            assert insn is not None and insn.fields.op != "unknown", (fn.name, hex(a))
            kind, target, falls = ARC.flow(insn)
            if target is not None and kind in ("jump", "call"):
                assert target in starts or target in p.by_entry, (fn.name, hex(a), insn.mnemonic)
                if kind == "jump":
                    todo.append(target)
            if falls or insn.fields.delay:
                todo.append(a + insn.size)


@pytest.mark.skipif(not shutil.which("arc-elf32-as"), reason="needs arc-elf32-as")
def test_the_gizmo_arc_listing_rebuilds_identically(tmp_path):
    from dread import mkasm
    out = tmp_path / "gizmo-arc.s"
    rc = mkasm.main(["--target", gizmo_dir("arc"), "--verify", "--out", str(out), "--build", str(tmp_path / "b")])
    assert rc == 0


@pytest.mark.skipif(not shutil.which("arc-elf32-gcc"), reason="needs arc-elf32-gcc")
def test_gizmo_arc_rewritten_functions_agree(tmp_path):
    """The rewritten C over the original on the ARC engine: what does not fit moves (behind the
    image, or into room other functions freed), and every function agrees with generated cases
    covering every instruction (handle_key's switch resolves, so that no scratch register looks
    kept across main's calls)."""
    from dread import build, runner
    from dread.target import Target
    from test_runner import src_copy
    target = Target.load(gizmo_dir("arc"))
    payload = build.build(target, target.image(), src=str(src_copy(tmp_path, "arc")), out=str(tmp_path / "recon"),
                          quiet=True)
    with open(tmp_path / "recon" / "report.tsv") as f:
        report = f.read()
    assert "behind the image" in report
    suite = runner.prepare(target, target.image(), payload)
    lines = []
    failed, uncovered, _ = runner.run(list(suite.cases), out=lines.append, jobs=4)
    text = "\n".join(lines)
    assert failed == 0 and not uncovered, text
    assert "ok   checksum" in text and "ok   parse_uint" in text and "FAIL" not in text


@pytest.mark.skipif(not shutil.which("arc-elf32-gcc"), reason="needs arc-elf32-gcc")
def test_gizmo_arc_system_run(tmp_path):
    """gizmo-arc from reset with its models, original against the image with the rewritten C: the
    reset vector's j _start, the startup's copies, the timer's interrupt through the controller
    (line 0: vector 3, entered through ilink1 and STATUS32_L1, left by j.f [ilink1]); every
    scenario agrees, at the same clock (libgcc's helpers take no time: the rewritten C calls them
    where the original's code did not)."""
    import json
    from dread import build, sysrun
    from dread.target import Target
    from test_runner import src_copy
    target = Target.load(gizmo_dir("arc"))
    payload = build.build(target, target.image(), src=str(src_copy(tmp_path, "arc")), out=str(tmp_path / "recon"),
                          quiet=True)
    with open(tmp_path / "recon" / "placement.json") as f:
        placed = json.load(f)["functions"]
    built = [n for n, p in placed.items() if p["placement"] != "helper"]
    with open(payload, "rb") as f:
        rebuilt = f.read()
    for name, scenario in sorted(target.scenarios.items()):
        a = sysrun.run_image(target, target.image(), target.image().payload(), scenario, built=built)
        b = sysrun.run_image(target, target.image(), rebuilt, scenario, placement=placed)
        assert a.log == b.log and a.clock == b.clock, (name, a.clock, b.clock)
    outcome = sysrun.run_image(target, target.image(), target.image().payload(), target.scenarios["keys"])
    assert outcome.reports["interrupts"].get(0) and "mode run volume 7" in outcome.reports["uart"]["sent"], outcome
