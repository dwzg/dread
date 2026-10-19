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


@pytest.mark.parametrize("cpu, flags, expected", [
    (None, ["-mcpu=cortex-m0"], False), (None, ["-mcpu=cortex-m3"], True), (None, ["-march=armv4t"], False),
    (None, ["-march=armv7-a", "-mthumb"], True), (None, ["-march=armv8-m.base"], False),
    (None, ["-march=armv6-m"], False), ("cortex-m4", [], True), ("arm926", [], False), (None, [], None)])
def test_thumb2_from_flags_and_cpu(cpu, flags, expected):
    from dread.arch.arm import thumb2_of
    assert thumb2_of(cpu, flags) == expected


@pytest.mark.parametrize("src", [0x08000100, 0x08000102])
def test_thumb1_entry_veneer_runs(src):
    """Without Thumb-2 (no b.w) a moved function's old entry holds a veneer through ip: it reaches
    the new place with every register but ip and the stack pointer as they were."""
    from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP)
    arm = Arm(isa="t32", profile="m", cpu="cortex-m0", thumb2=False)
    dst = 0x08000800
    code = arm.branch(src, dst, "t32")
    assert len(code) == (16 if src % 4 == 0 else 14) and arm.branch_length("t32") == 16
    uc = arm.new_uc()
    uc.mem_map(0x08000000, 0x1000)
    uc.mem_map(0x20000000, 0x1000)
    uc.mem_map(0x1FFF0000, 0x1000)
    uc.mem_write(src, code)
    uc.mem_write(dst, bytes.fromhex("401c021c7047"))     # adds r0, r0, #1; adds r2, r0, #0; bx lr
    for reg, value in ((UC_ARM_REG_R0, 41), (UC_ARM_REG_R1, 7), (UC_ARM_REG_SP, 0x20001000),
                       (UC_ARM_REG_LR, 0x1FFF0001)):
        uc.reg_write(reg, value)
    uc.emu_start(src | 1, 0x1FFF0000)
    assert (uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)) == (42, 7, 42)
    assert uc.reg_read(UC_ARM_REG_SP) == 0x20001000


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_original_symbols_carry_their_instruction_set(tmp_path):
    """The overlay's symbols for the original's functions: Thumb ones marked as such, so that the
    linker calls them without an ARM interworking stub."""
    import subprocess
    from dread.elf import Elf
    source = tmp_path / "s.s"
    source.write_text(Arm.symbol_asm("thumb_fn", 0x8001, "t32") + Arm.symbol_asm("arm_fn", 0x8100, "a32"))
    subprocess.run(["arm-none-eabi-gcc", "-march=armv4t", "-c", "-x", "assembler", "-o", str(tmp_path / "s.o"),
                    str(source)], check=True)
    syms = {s.name: s for s in Elf(str(tmp_path / "s.o")).symbols}
    assert syms["thumb_fn"].value == 0x8001 and syms["arm_fn"].value == 0x8100
    assert syms["thumb_fn"].kind == syms["arm_fn"].kind == 2          # STT_FUNC


def test_what_capstone_leaves_out_of_an_instructions_registers():
    """umlal and smlal add into RdLo:RdHi, and so do the DSP extension's smlald and the like: the
    registers are read as well as written, which capstone 5 does not list; nor ssat16's and usat16's
    source. The register flow and the lifter take the ARM layer's own roles (operand_roles)."""
    import capstone as cs
    from dread import arch as archmod
    arm = archmod.get({"name": "arm", "isa": "t32", "profile": "m"})
    md = cs.Cs(cs.CS_ARCH_ARM, cs.CS_MODE_THUMB)
    md.detail = True
    for hx, reads, writes in (("e3fb0201", {"r0", "r1", "r2", "r3"}, {"r0", "r1"}),      # umlal r0, r1, r3, r2
                              ("c1fbc201", {"r0", "r1", "r2"}, {"r0", "r1"}),            # smlald r0, r1, r1, r2
                              ("24f30002", {"r4"}, {"r2"}),                              # ssat16 r2, #1, r4
                              ("81fa82f0", {"r1", "r2"}, {"r0"})):                       # qadd r0, r2, r1
        insn = next(md.disasm(bytes.fromhex(hx), 0x1000))
        r, w = arm.reg_effects(insn)
        assert reads <= r and writes <= w, (insn.mnemonic, insn.op_str, r, w)


def test_an_exception_with_the_fpus_state_takes_the_extended_frame():
    """On a Cortex-M4 that has run an FPU instruction (CONTROL.FPCA), an exception pushes s0-s15 and
    FPSCR behind the eight words (0x68 bytes), returns through EXC_RETURN 0xFFFFFFE9 and runs its
    handler with FPCA clear; the return restores s0-s15 and FPCA. Without FPU state: eight words,
    0xFFFFFFF9."""
    import unicorn as uc
    from unicorn import arm_const as ua
    from dread import arch as archmod
    arm = archmod.get({"name": "arm", "isa": "t32", "profile": "m", "cpu": "cortex-m4"})
    for fpu in (True, False):
        engine = uc.Uc(uc.UC_ARCH_ARM, arm.uc_mode())
        engine.ctl_set_cpu_model(ua.UC_CPU_ARM_CORTEX_M4)
        engine.mem_map(0x08000000, 0x10000)
        engine.mem_map(0x20000000, 0x10000)
        engine.mem_write(0x08000000, bytes.fromhex("00ee100a" "00bf"))     # vmov s0, r0; nop
        engine.reg_write(ua.UC_ARM_REG_SP, 0x20008000)
        engine.reg_write(ua.UC_ARM_REG_R0, 0x3F800000)
        if fpu:
            engine.emu_start(0x08000001, 0x08000004, count=1)            # sets CONTROL.FPCA
        for k in range(1, 16):
            engine.reg_write(getattr(ua, "UC_ARM_REG_S%d" % k), 0x40000000 + k)
        memory = {}

        def write_word(address, value):
            engine.mem_write(address, (value & 0xFFFFFFFF).to_bytes(4, "little"))

        def read_word(address):
            return int.from_bytes(engine.mem_read(address, 4), "little")
        engine.mem_write(0x08000400 + 4 * 16, (0x08000101).to_bytes(4, "little"))     # a handler's vector
        arm.enter_interrupt(engine, 16, 0x08000005, 0x08000400, read_word, write_word)
        sp = engine.reg_read(ua.UC_ARM_REG_SP)
        lr = engine.reg_read(ua.UC_ARM_REG_LR)
        assert (0x20008000 - sp, lr) == ((0x68, 0xFFFFFFE9) if fpu else (32, 0xFFFFFFF9))
        assert not engine.reg_read(ua.UC_ARM_REG_CONTROL) & 4
        if fpu:
            assert read_word(sp + 32) == 0x3F800000 and read_word(sp + 32 + 4 * 15) == 0x4000000F
        for k in range(16):                                              # the handler changes them
            engine.reg_write(getattr(ua, "UC_ARM_REG_S%d" % k), 0)
        engine.reg_write(ua.UC_ARM_REG_PC, lr)
        assert arm.exception_return(engine, read_word) == 0x08000005
        assert engine.reg_read(ua.UC_ARM_REG_SP) == 0x20008000
        assert bool(engine.reg_read(ua.UC_ARM_REG_CONTROL) & 4) == fpu
        assert engine.reg_read(ua.UC_ARM_REG_S15) == (0x4000000F if fpu else 0)
