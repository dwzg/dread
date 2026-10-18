"""The RISC-V layer: its own decoder against LLVM's assembler (random operands for every form of
RV32IMC, Zicsr and the machine-mode instructions, assembled by llvm-mc and decoded back), the
two-instruction forms, the branch the overlay writes, and the flow of gizmo-rv32's code."""
import os
import random
import shutil
import subprocess

import pytest

from dread.arch.riscv import NAMES, RiscV, decode_fields, fields

needs_llvm = pytest.mark.skipif(not (shutil.which("llvm-mc") and shutil.which("llvm-objcopy")),
                                reason="needs llvm-mc and llvm-objcopy")
R = random.Random(7)
X = list(range(32))
NONZERO = list(range(1, 32))
PRIME = list(range(8, 16))                  # the registers of the compressed forms' 3-bit fields


def sign(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def pick(lo, hi, step=1, nonzero=False):
    while True:
        v = R.randrange(lo, hi + 1, step) if step == 1 else R.randrange(lo // step, hi // step + 1) * step
        if v or not nonzero:
            return v


def wide_forms():
    """(text, expected Fields as a dict) for the 32-bit forms."""
    out = []
    for _ in range(12):
        rd, rs1, rs2 = R.choice(X), R.choice(X), R.choice(X)
        u20 = R.randrange(0, 1 << 20)
        imm12 = pick(-2048, 2047)
        out += [("lui %s, %d" % (NAMES[rd], u20), dict(op="lui", rd=rd, imm=sign(u20 << 12, 32))),
                ("auipc %s, %d" % (NAMES[rd], u20), dict(op="auipc", rd=rd, imm=sign(u20 << 12, 32)))]
        off = pick(-(1 << 20), (1 << 20) - 2, 2)
        out.append(("jal %s, %d" % (NAMES[rd], off), dict(op="jal", rd=rd, imm=off)))
        out.append(("jalr %s, %d(%s)" % (NAMES[rd], imm12, NAMES[rs1]), dict(op="jalr", rd=rd, rs1=rs1, imm=imm12)))
        for name in ("beq", "bne", "blt", "bge", "bltu", "bgeu"):
            off = pick(-4096, 4094, 2)
            out.append(("%s %s, %s, %d" % (name, NAMES[rs1], NAMES[rs2], off), dict(op=name, rs1=rs1, rs2=rs2, imm=off)))
        for name, width in (("lb", 1), ("lh", 2), ("lw", 4), ("lbu", 1), ("lhu", 2)):
            out.append(("%s %s, %d(%s)" % (name, NAMES[rd], imm12, NAMES[rs1]),
                        dict(op=name, rd=rd, rs1=rs1, imm=imm12, width=width)))
        for name, width in (("sb", 1), ("sh", 2), ("sw", 4)):
            out.append(("%s %s, %d(%s)" % (name, NAMES[rs2], imm12, NAMES[rs1]),
                        dict(op=name, rs1=rs1, rs2=rs2, imm=imm12, width=width)))
        for name in ("addi", "slti", "sltiu", "xori", "ori", "andi"):
            out.append(("%s %s, %s, %d" % (name, NAMES[rd], NAMES[rs1], imm12), dict(op=name, rd=rd, rs1=rs1, imm=imm12)))
        for name in ("slli", "srli", "srai"):
            shamt = R.randrange(32)
            out.append(("%s %s, %s, %d" % (name, NAMES[rd], NAMES[rs1], shamt), dict(op=name, rd=rd, rs1=rs1, imm=shamt)))
        for name in ("add", "sub", "sll", "slt", "sltu", "xor", "srl", "sra", "or", "and",
                     "mul", "mulh", "mulhsu", "mulhu", "div", "divu", "rem", "remu"):
            out.append(("%s %s, %s, %s" % (name, NAMES[rd], NAMES[rs1], NAMES[rs2]),
                        dict(op=name, rd=rd, rs1=rs1, rs2=rs2)))
        csr = R.choice([0x300, 0x304, 0x305, 0x341, 0x342, 0x344, 0xB00])
        for name in ("csrrw", "csrrs", "csrrc"):
            out.append(("%s %s, %d, %s" % (name, NAMES[rd], csr, NAMES[rs1]), dict(op=name, rd=rd, rs1=rs1, csr=csr)))
        for name in ("csrrwi", "csrrsi", "csrrci"):
            u = R.randrange(32)
            out.append(("%s %s, %d, %d" % (name, NAMES[rd], csr, u), dict(op=name, rd=rd, imm=u, csr=csr)))
    out += [("ecall", dict(op="ecall")), ("ebreak", dict(op="ebreak")), ("mret", dict(op="mret")),
            ("wfi", dict(op="wfi")), ("fence", dict(op="fence"))]
    return out


def compressed_forms():
    out = []
    for _ in range(24):
        rd = R.choice(NONZERO)
        rdp, rs1p, rs2p = R.choice(PRIME), R.choice(PRIME), R.choice(PRIME)
        n = NAMES
        out.append(("c.addi4spn %s, sp, %d" % (n[rdp], pick(4, 1020, 4)), None))
        u = pick(0, 124, 4)
        out.append(("c.lw %s, %d(%s)" % (n[rdp], u, n[rs1p]), dict(op="lw", rd=rdp, rs1=rs1p, imm=u, width=4)))
        out.append(("c.sw %s, %d(%s)" % (n[rs2p], u, n[rs1p]), dict(op="sw", rs1=rs1p, rs2=rs2p, imm=u, width=4)))
        i6 = pick(-32, 31, nonzero=True)
        out.append(("c.addi %s, %d" % (n[rd], i6), dict(op="addi", rd=rd, rs1=rd, imm=i6)))
        off = pick(-2048, 2046, 2)
        out.append(("c.jal %d" % off, dict(op="jal", rd=1, imm=off)))
        out.append(("c.j %d" % off, dict(op="jal", rd=0, imm=off)))
        i6 = pick(-32, 31)
        out.append(("c.li %s, %d" % (n[rd], i6), dict(op="addi", rd=rd, rs1=0, imm=i6)))
        s16 = pick(-512, 496, 16)
        if s16:
            out.append(("c.addi16sp sp, %d" % s16, dict(op="addi", rd=2, rs1=2, imm=s16)))
        lui_rd = R.choice([r for r in NONZERO if r != 2])
        k = R.choice(list(range(1, 32)) + list(range(0xFFFE0, 0x100000)))
        out.append(("c.lui %s, %d" % (n[lui_rd], k), dict(op="lui", rd=lui_rd, imm=sign(k, 20) << 12)))
        sh = R.randrange(1, 32)
        out.append(("c.srli %s, %d" % (n[rs1p], sh), dict(op="srli", rd=rs1p, rs1=rs1p, imm=sh)))
        out.append(("c.srai %s, %d" % (n[rs1p], sh), dict(op="srai", rd=rs1p, rs1=rs1p, imm=sh)))
        i6 = pick(-32, 31)
        out.append(("c.andi %s, %d" % (n[rs1p], i6), dict(op="andi", rd=rs1p, rs1=rs1p, imm=i6)))
        for name in ("sub", "xor", "or", "and"):
            out.append(("c.%s %s, %s" % (name, n[rs1p], n[rs2p]), dict(op=name, rd=rs1p, rs1=rs1p, rs2=rs2p)))
        off = pick(-256, 254, 2)
        out.append(("c.beqz %s, %d" % (n[rs1p], off), dict(op="beq", rs1=rs1p, rs2=0, imm=off)))
        out.append(("c.bnez %s, %d" % (n[rs1p], off), dict(op="bne", rs1=rs1p, rs2=0, imm=off)))
        out.append(("c.slli %s, %d" % (n[rd], sh), dict(op="slli", rd=rd, rs1=rd, imm=sh)))
        u = pick(0, 252, 4)
        out.append(("c.lwsp %s, %d(sp)" % (n[rd], u), dict(op="lw", rd=rd, rs1=2, imm=u, width=4)))
        rs2 = R.choice(X)
        out.append(("c.swsp %s, %d(sp)" % (n[rs2], u), dict(op="sw", rs1=2, rs2=rs2, imm=u, width=4)))
        out.append(("c.jr %s" % n[rd], dict(op="jalr", rd=0, rs1=rd, imm=0)))
        out.append(("c.jalr %s" % n[rd], dict(op="jalr", rd=1, rs1=rd, imm=0)))
        rs2 = R.choice(NONZERO)
        out.append(("c.mv %s, %s" % (n[rd], n[rs2]), dict(op="add", rd=rd, rs1=0, rs2=rs2)))
        out.append(("c.add %s, %s" % (n[rd], n[rs2]), dict(op="add", rd=rd, rs1=rd, rs2=rs2)))
    out += [("c.ebreak", dict(op="ebreak")), ("c.nop", dict(op="addi", rd=0, rs1=0, imm=0))]
    return out


def assemble(lines, attrs, tmp):
    src, obj, raw = (os.path.join(tmp, n) for n in ("t.s", "t.o", "t.bin"))
    with open(src, "w") as f:
        f.write("\t.text\n" + "".join("\t%s\n" % l for l in lines))
    subprocess.run(["llvm-mc", "-triple=riscv32", "-mattr=" + attrs, "-filetype=obj", "-o", obj, src],
                   check=True, capture_output=True, text=True)
    subprocess.run(["llvm-objcopy", "-O", "binary", "--only-section=.text", obj, raw], check=True)
    with open(raw, "rb") as f:
        return f.read()


def check(forms, attrs, size, tmp):
    code = assemble([t for t, _ in forms], attrs, tmp)
    pos = 0
    for text, want in forms:
        f = decode_fields(code[pos:pos + 4])
        assert f.size == size, (text, f)
        if text.startswith("c.addi4spn"):
            imm = int(text.rsplit(",", 1)[1])
            want = dict(op="addi", rd=f.rd, rs1=2, imm=imm)
            assert NAMES[f.rd] == text.split()[1].rstrip(",")
        for key, value in want.items():
            assert getattr(f, key) == value, (text, key, f)
        pos += f.size
    assert pos == len(code)


@needs_llvm
def test_the_decoder_agrees_with_llvm_on_the_wide_forms(tmp_path):
    check(wide_forms(), "+m,-c", 4, str(tmp_path))


@needs_llvm
def test_the_decoder_agrees_with_llvm_on_the_compressed_forms(tmp_path):
    check(compressed_forms(), "+m,+c", 2, str(tmp_path))


class Insn:
    """Enough of a capstone instruction for the layer: address, size, bytes."""

    def __init__(self, address, code):
        self.address, self.bytes = address, code
        self.size = decode_fields(code).size


def test_flow_and_pairs():
    arch = RiscV()
    enc = arch.branch
    call = Insn(0x1000, enc(0x1000, 0x1400, "rv32", link=True))
    assert arch.flow(call) == ("call", 0x1400, True)
    jump = Insn(0x1000, enc(0x1000, 0x0F00, "rv32"))
    assert arch.flow(jump) == ("jump", 0x0F00, False)
    far = enc(0x20000000, 0x80000000, "rv32", link=True)
    assert len(far) == 8
    auipc, jalr = Insn(0x20000000, far[:4]), Insn(0x20000004, far[4:])
    assert arch.flow(jalr)[0] == "icall"                    # alone: through a register
    arch.link_pair(auipc, jalr, "rv32")
    assert arch.flow(jalr) == ("call", 0x80000000, True)    # joined: the call's target
    ret = Insn(0x1000, bytes([0x82, 0x80]))                 # c.jr ra
    assert arch.flow(ret) == ("return", None, False)
    assert arch.reg_effects(Insn(0x1000, bytes([0x2e, 0x95]))) == ({"a0", "a1"}, {"a0"})   # c.add a0, a1
    lui = Insn(0x1000, (0x20001537).to_bytes(4, "little"))          # lui a0, 0x20001
    addi = Insn(0x1004, (0xa3850513).to_bytes(4, "little"))         # addi a0, a0, -0x5c8
    arch.link_pair(lui, addi, "rv32")
    assert arch.pc_address(addi, "rv32") == 0x20000a38


def test_far_branch_lands():
    """auipc + jalr over 1.5 GiB, both directions, decoded back."""
    arch = RiscV()
    for src, dst in ((0x20000000, 0x80000000), (0x80000010, 0x20000400), (0x1000, 0x1000 + (1 << 20))):
        code = arch.branch(src, dst, "rv32")
        a, j = decode_fields(code[:4]), decode_fields(code[4:])
        assert (a.op, j.op, a.rd, j.rs1, j.rd) == ("auipc", "jalr", 6, 6, 0)
        assert (src + a.imm + j.imm) & 0xFFFFFFFF == dst
