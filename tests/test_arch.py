"""The ARM layer: decoding helpers checked against capstone, branch encodings round-tripped, and
Unicorn set up for both profiles."""
import random

import pytest

from dread.arch.arm import Arm


@pytest.mark.parametrize("isa, link", [("a32", False), ("a32", True), ("t32", False), ("t32", True)])
def test_branch_encoding_round_trips(isa, link):
    arm = Arm(isa=isa, profile="a")
    rng = random.Random(1)
    reach = (1 << 25) if isa == "a32" else (1 << 24)
    align = 4 if isa == "a32" else 2
    for _ in range(300):
        src = rng.randrange(0x1000, 0x4000000) & ~(align - 1)
        off = rng.randrange(-reach, reach) & ~(align - 1)
        dst = src + (8 if isa == "a32" else 4) + off
        if dst < 0:
            continue
        code = arm.branch(src, dst, isa, link=link)
        insn = arm.decode(code, src, isa)
        assert insn is not None and arm.target(insn) == dst, (hex(src), hex(dst), code.hex())
        assert arm.is_call(insn) == link


def test_branch_out_of_reach():
    arm = Arm()
    with pytest.raises(ValueError):
        arm.branch(0, 0x4000000, "a32")
    with pytest.raises(ValueError):
        arm.branch(0, 0x2000000, "t32")


# Assembled from our own probe (GNU as, -march=armv7-a): address, bytes, isa, literal address
LITERALS = [
    (0x00, "18009fe5", "a32", (0x20, 4)),          # ldr r0, [pc, #24]
    (0x04, "04101fe5", "a32", (0x08, 4)),          # ldr r1, [pc, #-4]
    (0x24, "0648", "t32", (0x40, 4)),              # ldr r0, [pc, #24]
    (0x26, "dff81810", "t32", (0x40, 4)),          # ldr.w r1, [pc, #24]
]


@pytest.mark.parametrize("address, code, isa, expected", LITERALS)
def test_literal_addresses(address, code, isa, expected):
    arm = Arm(profile="a")
    insn = arm.decode(bytes.fromhex(code), address, isa)
    assert arm.literal(insn, isa) == expected


@pytest.mark.parametrize("address, code, isa, expected", [
    (0x00, "08008fe2", "a32", 0x10),               # add r0, pc, #8
    (0x04, "08104fe2", "a32", 0x04),               # sub r1, pc, #8
    (0x14, "02a0", "t32", 0x20),                   # adr r0, lit
    (0x16, "0ff20801", "t32", 0x20),               # addw r1, pc, #8
])
def test_pc_relative_addresses(address, code, isa, expected):
    arm = Arm(profile="a")
    insn = arm.decode(bytes.fromhex(code), address, isa)
    assert arm.pc_address(insn, isa) == expected


def test_thumb_lengths():
    arm = Arm(isa="t32", profile="m")
    assert arm.insn_length(bytes.fromhex("dff8"), "t32") == 4
    assert arm.insn_length(bytes.fromhex("0648"), "t32") == 2
    assert arm.insn_length(bytes.fromhex("00f0"), "t32") == 4      # bl, first half
    assert arm.insn_length(bytes.fromhex("7047"), "t32") == 2      # bx lr


def test_it_block_conditions():
    arm = Arm(isa="t32", profile="m")
    md = arm.disassembler("t32")
    insns = list(md.disasm(bytes.fromhex("08bf0120"), 0))           # it eq; moveq r0, #1
    assert arm.condition(insns[1]) == "eq" and arm.is_conditional(insns[1])


def test_unicorn_runs_thumb_on_m_profile():
    """A Thumb function on a Cortex-M3 returns to a sentinel in executable space."""
    from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_SP
    arm = Arm(isa="t32", profile="m", cpu="cortex-m3")
    uc = arm.new_uc()
    uc.mem_map(0x08000000, 0x1000)
    uc.mem_map(0x20000000, 0x1000)
    uc.mem_map(0x1FFF0000, 0x1000)
    uc.mem_write(0x08000000, bytes.fromhex("41f23420c5f2786001307047"))  # movw, movt, adds #1, bx lr
    uc.reg_write(UC_ARM_REG_SP, 0x20001000)
    uc.reg_write(UC_ARM_REG_LR, 0x1FFF0001)
    uc.emu_start(arm.start_address(0x08000000, "t32"), 0x1FFF0000)
    assert uc.reg_read(UC_ARM_REG_R0) == 0x56781235


def test_unicorn_runs_arm_state():
    from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0
    arm = Arm(isa="a32", profile="a")
    uc = arm.new_uc()
    uc.mem_map(0x10000, 0x1000)
    uc.mem_map(0xFFF00000, 0x1000)
    uc.mem_write(0x10000, bytes.fromhex("2a00a0e31eff2fe1"))       # mov r0, #42; bx lr
    uc.reg_write(UC_ARM_REG_LR, 0xFFF00000)
    uc.emu_start(0x10000, 0xFFF00000)
    assert uc.reg_read(UC_ARM_REG_R0) == 42
