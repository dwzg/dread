"""The MIPS layer: its own decoder against LLVM's assembler (random operands for the MIPS32r2 integer
forms, assembled by llvm-mc and decoded back), flow with the delay slot, register effects, the lui
pairs, and gizmo-mips: its listing rebuilds byte for byte and every jump lands on code."""
import os
import random
import shutil
import subprocess

import pytest

from dread import mkasm
from dread.arch.mips import NAMES, Mips, decode_fields
from dread.program import Program
from dread.target import Target
from conftest import gizmo_dir

needs_llvm = pytest.mark.skipif(not (shutil.which("llvm-mc") and shutil.which("llvm-objcopy")),
                                reason="needs llvm-mc and llvm-objcopy")
R = random.Random(11)
X = list(range(32))
NONZERO = list(range(1, 32))


def forms():
    """(text, expected Fields as a dict) for MIPS32r2's integer forms; branches go to . + an
    offset (counted by the decoder from the delay slot: the offset minus 4)."""
    out = []
    for _ in range(10):
        rd, rs, rt = R.choice(NONZERO), R.choice(X), R.choice(X)
        sa = R.randrange(1, 32)
        simm, uimm = R.randrange(-32768, 32768), R.randrange(0, 65536)
        n = NAMES
        for name in ("addu", "subu", "and", "or", "xor", "nor", "slt", "sltu", "movz", "movn", "mul"):
            out.append(("%s $%s, $%s, $%s" % (name, n[rd], n[rs], n[rt]), dict(op=name, rd=rd, rs=rs, rt=rt)))
        for name in ("sllv", "srlv", "srav", "rotrv"):
            out.append(("%s $%s, $%s, $%s" % (name, n[rd], n[rt], n[rs]), dict(op=name, rd=rd, rs=rs, rt=rt)))
        for name in ("sll", "srl", "sra", "rotr"):
            out.append(("%s $%s, $%s, %d" % (name, n[rd], n[rt], sa), dict(op=name, rd=rd, rt=rt, sa=sa)))
        for name in ("addiu", "slti", "sltiu"):
            out.append(("%s $%s, $%s, %d" % (name, n[rt or 1], n[rs], simm), dict(op=name, rt=rt or 1, rs=rs, imm=simm)))
        for name in ("andi", "ori", "xori"):
            out.append(("%s $%s, $%s, %d" % (name, n[rt or 1], n[rs], uimm), dict(op=name, rt=rt or 1, rs=rs, imm=uimm)))
        out.append(("lui $%s, %d" % (n[rd], uimm), dict(op="lui", rt=rd, imm=uimm << 16)))
        for name, width in (("lb", 1), ("lh", 2), ("lw", 4), ("lbu", 1), ("lhu", 2), ("lwl", 4), ("lwr", 4),
                            ("sb", 1), ("sh", 2), ("sw", 4), ("swl", 4), ("swr", 4), ("ll", 4), ("sc", 4)):
            out.append(("%s $%s, %d($%s)" % (name, n[rt], simm, n[rs]), dict(op=name, rt=rt, rs=rs, imm=simm,
                                                                          width=width)))
        for name in ("mult", "multu", "madd", "maddu", "msub", "msubu"):
            out.append(("%s $%s, $%s" % (name, n[rs], n[rt]), dict(op=name, rs=rs, rt=rt)))
        for name in ("div", "divu"):                # (with two operands, LLVM's macro: checks for zero)
            out.append(("%s $zero, $%s, $%s" % (name, n[rs], n[rt]), dict(op=name, rs=rs, rt=rt)))
        for name in ("mfhi", "mflo"):
            out.append(("%s $%s" % (name, n[rd]), dict(op=name, rd=rd)))
        for name in ("mthi", "mtlo", "jr"):
            out.append(("%s $%s" % (name, n[rs]), dict(op=name, rs=rs)))
        out.append(("jalr $%s, $%s" % (n[rd], n[rs]), dict(op="jalr", rd=rd, rs=rs)))
        for name in ("clz", "clo"):
            out.append(("%s $%s, $%s" % (name, n[rd], n[rs]), dict(op=name, rd=rd, rs=rs)))
        for name in ("seb", "seh", "wsbh"):
            out.append(("%s $%s, $%s" % (name, n[rd], n[rt]), dict(op=name, rd=rd, rt=rt)))
        pos, size = R.randrange(0, 31), R.randrange(1, 32)
        size = min(size, 32 - pos)
        out.append(("ext $%s, $%s, %d, %d" % (n[rt], n[rs], pos, size), dict(op="ext", rt=rt, rs=rs, sa=pos,
                                                                          rd=size - 1)))
        out.append(("ins $%s, $%s, %d, %d" % (n[rt], n[rs], pos, size), dict(op="ins", rt=rt, rs=rs, sa=pos,
                                                                          rd=pos + size - 1)))
        off = R.randrange(-0x2000, 0x2000) * 4
        for name in ("beq", "bne", "beql", "bnel"):
            out.append(("%s $%s, $%s, . + %d" % (name, n[rs], n[rt], off), dict(op=name, rs=rs, rt=rt, imm=off - 4)))
        for name in ("blez", "bgtz", "bltz", "bgez", "bltzal", "bgezal", "blezl", "bgtzl", "bltzl", "bgezl"):
            out.append(("%s $%s, . + %d" % (name, n[rs], off), dict(op=name, rs=rs, imm=off - 4)))
        sel = R.randrange(0, 8)
        out.append(("mfc0 $%s, $%d, %d" % (n[rt], rd, sel), dict(op="mfc0", rt=rt, rd=rd, sel=sel)))
        out.append(("mtc0 $%s, $%d, %d" % (n[rt], rd, sel), dict(op="mtc0", rt=rt, rd=rd, sel=sel)))
        for name in ("teq", "tne", "tge", "tgeu", "tlt", "tltu"):
            out.append(("%s $%s, $%s" % (name, n[rs], n[rt]), dict(op=name, rs=rs, rt=rt)))
    out += [("syscall", dict(op="syscall")), ("break", dict(op="break")), ("eret", dict(op="eret")),
            ("wait", dict(op="wait")), ("sync", dict(op="sync")), ("nop", dict(op="nop")),
            ("di $t0", dict(op="di", rt=8)), ("ei $zero", dict(op="ei", rt=0)), ("rdhwr $t1, $29", dict(op="rdhwr", rt=9, rd=29))]
    return out


def assemble(lines, tmp):
    src, obj, raw = (os.path.join(tmp, n) for n in ("t.s", "t.o", "t.bin"))
    with open(src, "w") as f:
        f.write("\t.set noreorder\n\t.set noat\n\t.set nomacro\n\t.text\n" + "".join("\t%s\n" % l for l in lines))
    subprocess.run(["llvm-mc", "-triple=mipsel", "-mcpu=mips32r2", "-filetype=obj", "-o", obj, src],
                   check=True, capture_output=True, text=True)
    subprocess.run(["llvm-objcopy", "-O", "binary", "--only-section=.text", obj, raw], check=True)
    with open(raw, "rb") as f:
        return f.read()


@needs_llvm
def test_the_decoder_agrees_with_llvm(tmp_path):
    cases = forms()
    code = assemble([t for t, _ in cases], str(tmp_path))
    assert len(code) == 4 * len(cases)
    for k, (text, want) in enumerate(cases):
        f = decode_fields(code[4 * k:4 * k + 4])
        for key, value in want.items():
            assert getattr(f, key) == value, (text, key, f)


class Insn:
    """Enough of a capstone instruction for the layer: address, size, bytes."""

    def __init__(self, address, word):
        self.address, self.bytes, self.size = address, word.to_bytes(4, "little"), 4


def test_flow_with_the_delay_slot():
    arch = Mips()
    at = 0xbfc00100
    jal = Insn(at, 0x0c000000 | (0x0fc01300 >> 2))                     # jal 0xbfc01300
    assert arch.flow(jal) == ("call", 0xbfc01300, True) and arch.delay_slot(jal)
    j = Insn(at, 0x08000000 | (0x0fc00080 >> 2))                       # j 0xbfc00080
    assert arch.flow(j) == ("jump", 0xbfc00080, False)
    assert arch.flow(Insn(at, 0x03e00008)) == ("return", None, False)   # jr ra
    assert arch.flow(Insn(at, 0x03200008)) == ("ijump", "t9", False)    # jr t9
    assert arch.flow(Insn(at, 0x0320f809)) == ("icall", None, True)     # jalr t9
    beq = Insn(at, 0x10850003)                                          # beq a0, a1, +12 from the slot
    assert arch.flow(beq) == ("jump", at + 4 + 12, True) and arch.delay_slot(beq) and not arch.likely(beq)
    assert arch.flow(Insn(at, 0x10000003)) == ("jump", at + 16, False)  # b (beq zero, zero)
    assert arch.flow(Insn(at, 0x04110003)) == ("call", at + 16, True)   # bal
    assert arch.likely(Insn(at, 0x50850003))                             # beql
    eret = Insn(at, 0x42000018)
    assert arch.flow(eret) == ("return", None, False) and not arch.delay_slot(eret)
    assert arch.flow(Insn(at, 0x0000000c)) == ("icall", None, True)     # syscall
    assert arch.flow(Insn(at, 0x0000000d))[0] == "stop"                 # break
    assert not arch.delay_slot(Insn(at, 0x24020001))                    # addiu v0, zero, 1
    assert arch.pc_value(at, "mips32") == at + 4


def test_register_effects():
    arch = Mips()
    eff = lambda word: arch.reg_effects(Insn(0x1000, word))
    assert eff(0x00851021) == ({"a0", "a1"}, {"v0"})                     # addu v0, a0, a1
    assert eff(0x0085100b) == ({"a0", "a1", "v0"}, {"v0"})               # movn v0, a0, a1: may keep v0
    assert eff(0x00850019) == ({"a0", "a1"}, {"hi", "lo"})               # multu a0, a1
    assert eff(0x00001010) == ({"hi"}, {"v0"})                           # mfhi v0
    assert eff(0x0c000000) == (set(), {"ra"})                            # jal
    assert eff(0xe0850000) == ({"a0", "a1"}, {"a1"})                     # sc a1, 0(a0)
    assert eff(0x88850000) == ({"a0", "a1"}, {"a1"})                     # lwl a1, 0(a0): merged
    assert eff(0x00000000) == (set(), set())                             # nop
    assert eff(0x8c000000) == (set(), set())                             # lw zero, 0(zero)


def test_lui_pairs_give_addresses():
    arch = Mips()
    lui = Insn(0x1000, 0x3c048000)                                      # lui a0, 0x8000
    addiu = Insn(0x1004, 0x24840060)                                    # addiu a0, a0, 0x60
    arch.link_pair(lui, addiu, "mips32")
    assert arch.pc_address(addiu, "mips32") == 0x80000060
    lw = Insn(0x1004, 0x8c850070)                                       # lw a1, 0x70(a0)
    arch.link_pair(lui, lw, "mips32")
    assert arch.literal(lw, "mips32") == (0x80000070, 4)
    neg = Insn(0x1004, 0x2484fff0)                                      # addiu a0, a0, -16
    arch.link_pair(lui, neg, "mips32")
    assert arch.pc_address(neg, "mips32") == 0x7ffffff0


@pytest.fixture(scope="module")
def mips():
    t = Target.load(gizmo_dir("mips"))
    return t, Program(t, t.image())


def test_gizmo_mips_decodes_and_its_jumps_land_on_code(mips):
    """Every instruction of every function decodes to a known form, and every jal, j and branch goes
    to an instruction of a function: a jal reaches only the 256 MiB region of its delay slot, and
    ld.lld wrote gizmo's call of ram_crc (in RAM, kseg0, from the flash in kseg1) into the flash's
    region without a word (fixed by a long call; the listing showed it as the one raw jal)."""
    t, p = mips
    starts = {a for fn in p.functions for a in fn.insns}
    assert len(p.functions) >= 45
    for fn in p.functions:
        for a in fn.insns:
            insn = p.insn(a)
            assert insn is not None and decode_fields(bytes(insn.bytes)).op != "unknown", (fn.name, hex(a))
            kind, target, _ = p.arch.flow(insn)
            if kind in ("call", "jump"):
                assert target in starts, (fn.name, hex(a), insn.mnemonic, insn.op_str)


@pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")), reason="needs clang and ld.lld")
def test_the_gizmo_mips_listing_rebuilds_identically(tmp_path):
    raw_table = tmp_path / "asm_raw_words.txt"
    out = tmp_path / "asm"
    rc = mkasm.main(["--target", gizmo_dir("mips"), "--verify", "--out", str(out), "--build", str(tmp_path / "b"),
                     "--raw", str(raw_table)])
    assert rc == 0
    assert not mkasm.load_raw_words(str(raw_table))         # every instruction as text, none as a word
    text = (out / "firmware.s").read_text()
    assert ".set noreorder" in text
    body = "".join(p.read_text() for p in out.glob("[0-9a-f]*.s"))
    assert "# ---- main (" in body and "\tjal     main" in body
