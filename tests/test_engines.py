"""The engine contract (dread.engines) on Unicorn and on DREAD's MSP430 engine alike, the MSP430
engine against TI's MSP430x2xx family user's guide (SLAU144) where GDB's simulator differs from it,
and a short run of dread.engines.msp430_check against that simulator (when TI's MSP430 GCC is
installed)."""
import shutil
import struct

import pytest
import unicorn.unicorn_const as C
from unicorn import Uc, UcError
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, \
    UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6

from dread.engines.msp430 import Msp430Engine

CODE, DATA = 0x1000, 0x8000


class ArmProgram:
    """mov r0, #1; mov r1, #2; str r1, [r2]; ldr r3, [r2]; b +8; mov r4, #4; mov r5, #5; mov r6, #6"""
    code = struct.pack("<8I", 0xE3A00001, 0xE3A01002, 0xE5821000, 0xE5923000, 0xEA000000, 0xE3A04004,
                       0xE3A05005, 0xE3A06006)
    at = [CODE + 4 * i for i in range(8)]           # the instructions' addresses
    end = CODE + 32
    regs = {"a": UC_ARM_REG_R0, "b": UC_ARM_REG_R1, "ptr": UC_ARM_REG_R2, "loaded": UC_ARM_REG_R3,
            "skipped": UC_ARM_REG_R4, "x": UC_ARM_REG_R5, "y": UC_ARM_REG_R6, "pc": UC_ARM_REG_PC}
    blocks = [(CODE, 20), (CODE + 24, 8)]           # up to and including the branch; then to the end
    size = 4

    @staticmethod
    def engine():
        from unicorn import UC_ARCH_ARM, UC_MODE_ARM
        return Uc(UC_ARCH_ARM, UC_MODE_ARM)


class Msp430Program:
    """mov #1, r12; mov #2, r13; mov r13, 0(r14); mov @r14, r15; jmp +2; mov #4, r4; mov #5, r5;
    mov #6, r6 (sizes 2, 2, 4, 2, 2, 2... with the constant generators for 1, 2, 4)"""
    code = bytes.fromhex("1c43" "2d43" "8e4d0000" "2f4e" "013c" "2442" "35400500" "36400600")
    at = [CODE, CODE + 2, CODE + 4, CODE + 8, CODE + 10, CODE + 12, CODE + 14, CODE + 18]
    end = CODE + 22
    regs = {"a": 12, "b": 13, "ptr": 14, "loaded": 15, "skipped": 4, "x": 5, "y": 6, "pc": 0}
    blocks = [(CODE, 12), (CODE + 14, 8)]
    size = None                                     # the sizes differ: take them from `at`

    @staticmethod
    def engine():
        return Msp430Engine()


PROGRAMS = {"unicorn-arm": ArmProgram, "msp430": Msp430Program}


@pytest.fixture(params=sorted(PROGRAMS))
def prog(request):
    return PROGRAMS[request.param]


def fresh(p):
    e = p.engine()
    e.mem_map(CODE, 0x1000)
    e.mem_map(DATA, 0x1000)
    e.mem_write(CODE, p.code)
    e.reg_write(p.regs["ptr"], DATA)
    return e


def reg(e, p, name):
    return e.reg_read(p.regs[name])


def test_a_stop_in_a_code_hook_leaves_the_instruction_unrun(prog):
    e = fresh(prog)
    e.hook_add(C.UC_HOOK_CODE, lambda uc, a, s, d: uc.emu_stop(), begin=prog.at[1], end=prog.at[1])
    e.emu_start(CODE, prog.end)
    assert (reg(e, prog, "a"), reg(e, prog, "b"), reg(e, prog, "pc")) == (1, 0, prog.at[1])


def test_a_stop_in_a_block_hook_leaves_the_block_unrun(prog):
    e = fresh(prog)
    second = prog.blocks[1][0]
    e.hook_add(C.UC_HOOK_BLOCK, lambda uc, a, s, d: uc.emu_stop() if a == second else None)
    e.emu_start(CODE, prog.end)
    assert (reg(e, prog, "loaded"), reg(e, prog, "x"), reg(e, prog, "pc")) == (2, 0, second)


def test_blocks_end_at_a_branch_and_at_until(prog):
    e = fresh(prog)
    seen = []
    e.hook_add(C.UC_HOOK_BLOCK, lambda uc, a, s, d: seen.append((a, s)))
    e.emu_start(CODE, prog.end)
    assert seen == prog.blocks


def test_writing_pc_in_a_code_hook_redirects(prog):
    e = fresh(prog)
    second = prog.blocks[1][0]
    e.hook_add(C.UC_HOOK_CODE, lambda uc, a, s, d: uc.reg_write(prog.regs["pc"], second),
               begin=prog.at[1], end=prog.at[1])
    e.emu_start(CODE, prog.end)
    assert (reg(e, prog, "b"), reg(e, prog, "x"), reg(e, prog, "y")) == (0, 5, 6)


def test_a_read_hook_supplies_the_value(prog):
    e = fresh(prog)
    e.hook_add(C.UC_HOOK_MEM_READ, lambda uc, acc, a, s, v, d: uc.mem_write(a, b"\x77\x00\x00\x00"[:s]))
    e.emu_start(CODE, prog.end)
    assert reg(e, prog, "loaded") == 0x77


def test_a_write_hook_sees_the_old_contents_and_the_store_follows(prog):
    e = fresh(prog)
    e.mem_write(DATA, b"\x55\x00")
    seen = []

    def hook(uc, acc, a, s, v, d):
        seen.append((acc, a, v, bytes(uc.mem_read(a, 2))))
        uc.mem_write(a, b"\x99\x00")
    e.hook_add(C.UC_HOOK_MEM_WRITE, hook)
    e.emu_start(CODE, prog.end)
    assert seen == [(C.UC_MEM_WRITE, DATA, 2, b"\x55\x00")]
    assert bytes(e.mem_read(DATA, 2)) == b"\x02\x00"


def test_until_and_count(prog):
    e = fresh(prog)
    seen = []
    e.hook_add(C.UC_HOOK_CODE, lambda uc, a, s, d: seen.append(a))
    e.emu_start(CODE, prog.at[3])
    assert seen == prog.at[:3] and reg(e, prog, "pc") == prog.at[3]
    e = fresh(prog)
    seen = []
    e.hook_add(C.UC_HOOK_CODE, lambda uc, a, s, d: seen.append(a))
    e.emu_start(CODE, prog.end, count=2)
    assert seen == prog.at[:2] and reg(e, prog, "pc") == prog.at[2]


def test_hook_ranges_include_both_ends(prog):
    e = fresh(prog)
    seen = []
    e.hook_add(C.UC_HOOK_CODE, lambda uc, a, s, d: seen.append(a), begin=prog.at[1], end=prog.at[2])
    e.emu_start(CODE, prog.end)
    assert seen == prog.at[1:3]


def test_after_a_stop_the_other_hooks_of_the_instruction_are_skipped(prog):
    e = fresh(prog)
    order = []
    e.hook_add(C.UC_HOOK_CODE, lambda uc, a, s, d: (order.append("first"), uc.emu_stop()),
               begin=prog.at[1], end=prog.at[1])
    e.hook_add(C.UC_HOOK_CODE, lambda uc, a, s, d: order.append("second"), begin=prog.at[1], end=prog.at[1])
    e.emu_start(CODE, prog.end)
    assert order == ["first"]


def test_unmapped_accesses(prog):
    e = fresh(prog)
    e.reg_write(prog.regs["ptr"], 0x20000 if prog is ArmProgram else 0x4000)
    seen = []
    e.hook_add(C.UC_HOOK_MEM_WRITE, lambda uc, acc, a, s, v, d: seen.append("write hook"))
    e.hook_add(C.UC_HOOK_MEM_INVALID, lambda uc, acc, a, s, v, d: (seen.append(acc), False)[1])
    with pytest.raises(UcError) as err:
        e.emu_start(CODE, prog.end)
    assert err.value.errno == C.UC_ERR_WRITE_UNMAPPED
    assert seen == ["write hook", C.UC_MEM_WRITE_UNMAPPED]
    assert reg(e, prog, "pc") == prog.at[2]
    # a hook that maps the page: the access is tried again
    e = fresh(prog)
    page = 0x20000 if prog is ArmProgram else 0x4000
    e.reg_write(prog.regs["ptr"], page)
    e.hook_add(C.UC_HOOK_MEM_UNMAPPED, lambda uc, acc, a, s, v, d: (uc.mem_map(page, 0x1000), True)[1])
    e.emu_start(CODE, prog.end)
    assert reg(e, prog, "loaded") == 2
    with pytest.raises(UcError):
        e.mem_read(0x6000, 2)
    with pytest.raises(UcError):
        e.mem_write(0x6000, b"\0")
    with pytest.raises(UcError) as err:
        e.mem_map(CODE, 0x1000)
    assert err.value.errno == C.UC_ERR_MAP


def test_protected_memory(prog):
    e = fresh(prog)
    e.mem_protect(DATA, 0x1000, C.UC_PROT_READ)
    with pytest.raises(UcError) as err:
        e.emu_start(CODE, prog.end)
    assert err.value.errno == C.UC_ERR_WRITE_PROT


# ---- the MSP430 engine against the guide, where GDB's simulator differs ----

SR_C, SR_Z, SR_N, SR_V, CPUOFF = 1, 2, 4, 0x100, 0x10


def run430(code, regs=None, sr=0, memory=None, count=100):
    """Run MSP430 code (hex) at CODE on the engine; returns the engine."""
    e = Msp430Engine()
    e.mem_map(0, 0x10000)
    e.mem_write(CODE, bytes.fromhex(code))
    for address, data in (memory or {}).items():
        e.mem_write(address, data)
    e.reg_write(1, 0x0A00)
    for r, v in (regs or {}).items():
        e.reg_write(r, v)
    e.reg_write(2, sr)
    e.emu_start(CODE, CODE + len(bytes.fromhex(code)), count=count)
    return e


def test_overflow_is_sr_bit_8():
    e = run430("0c5d", {12: 0x7FFF, 13: 1})                 # add r13, r12
    assert e.reg_read(12) == 0x8000 and e.reg_read(2) == SR_V | SR_N


def test_sub_sets_overflow():
    e = run430("0c8d", {12: 0x8000, 13: 1})                 # sub r13, r12
    assert e.reg_read(12) == 0x7FFF and e.reg_read(2) == SR_V | SR_C


def test_sxt_sets_carry_to_not_zero():
    assert run430("8c11", {12: 0x0080}).reg_read(2) == SR_N | SR_C     # sxt r12
    assert run430("8c11", {12: 0x0100}, sr=SR_C).reg_read(2) == SR_Z


def test_rrc_clears_overflow():
    e = run430("0c10", {12: 0}, sr=SR_C | SR_V)             # rrc r12
    assert e.reg_read(12) == 0x8000 and e.reg_read(2) == SR_N


def test_dadd():
    e = run430("0cad", {12: 0x0049, 13: 0x0049})            # dadd r13, r12
    assert e.reg_read(12) == 0x0098 and e.reg_read(2) == 0
    e = run430("0cad", {12: 0x9999, 13: 0x0001})
    assert e.reg_read(12) == 0 and e.reg_read(2) == SR_C | SR_Z
    # digits above 9 (undefined in the guide): each digit sum above 9 adds 6 and carries 1
    e = run430("0cad", {12: 0x000F, 13: 0x000F}, sr=SR_C)
    assert e.reg_read(12) == 0x0015


def test_an_increment_comes_before_the_destination():
    # add @r5+, 0(r5): the source at r5, then r5 + 2, then the destination at the new r5, read
    # and written there
    e = run430("b5550000", {5: 0x200}, memory={0x200: b"\x04\x15\x05\x16"})
    assert e.reg_read(5) == 0x202 and bytes(e.mem_read(0x200, 4)) == b"\x04\x15\x09\x2b"


def test_word_accesses_ignore_bit_0():
    e = run430("2f45", {5: 0x201}, memory={0x200: b"\x04\x15\x05\x16"})    # mov @r5, r15
    assert e.reg_read(15) == 0x1504


def test_push_call_reti():
    e = run430("0112", {})                                  # push sp: the old sp
    assert e.reg_read(1) == 0x09FE and bytes(e.mem_read(0x09FE, 2)) == b"\x00\x0a"
    e = run430("4c12", {12: 0x1234})                        # push.b r12: one byte at the new sp
    assert e.reg_read(1) == 0x09FE and e.mem_read(0x09FE, 1)[0] == 0x34
    e = Msp430Engine()
    e.mem_map(0, 0x10000)
    e.mem_write(CODE, bytes.fromhex("b0120020"))            # call #0x2000
    e.reg_write(1, 0x0A00)
    e.emu_start(CODE, 0x2000)
    assert e.reg_read(1) == 0x09FE and bytes(e.mem_read(0x09FE, 2)) == struct.pack("<H", CODE + 4)
    e.mem_write(0x09FA, struct.pack("<HH", SR_C | SR_N, 0x3000))
    e.mem_write(0x2000, bytes.fromhex("0013"))              # reti: sr, then pc
    e.reg_write(1, 0x09FA)
    e.emu_start(0x2000, 0x3000)
    assert (e.reg_read(0), e.reg_read(1), e.reg_read(2)) == (0x3000, 0x09FE, SR_C | SR_N)


def test_registers():
    e = Msp430Engine()
    e.reg_write(0, 0x1235)
    e.reg_write(1, 0x0A01)
    e.reg_write(3, 7)
    e.reg_write(12, 0x12345)
    assert (e.reg_read(0), e.reg_read(1), e.reg_read(3), e.reg_read(12)) == (0x1234, 0x0A00, 0, 0x2345)
    e = run430("7c403412", {})                              # mov.b #0x1234, r12: the high byte cleared
    assert e.reg_read(12) == 0x34


def test_cpuoff_ends_the_run_behind_the_instruction():
    e = run430("32d010001c43", {})                          # bis #CPUOFF, sr; mov #1, r12
    assert e.sleeping and e.reg_read(0) == CODE + 4 and e.reg_read(12) == 0


def test_code_written_while_running_is_decoded_again():
    # mov r13, &CODE+4; mov #1, r12. The first run writes the second instruction's own bytes over
    # it (decoded and cached), the second writes a nop there.
    e = Msp430Engine()
    e.mem_map(0, 0x10000)
    e.mem_write(CODE, bytes.fromhex("824d0410" "1c43"))
    for r13, r12 in ((0x431C, 1), (0x4303, 0)):
        e.reg_write(12, 0)
        e.reg_write(13, r13)
        e.emu_start(CODE, CODE + 6)
        assert e.reg_read(12) == r12


@pytest.mark.skipif(not (shutil.which("msp430-elf-gcc") and shutil.which("msp430-elf-run")),
                    reason="needs TI's MSP430 GCC (msp430-elf-gcc and msp430-elf-run)")
def test_the_engine_agrees_with_gdbs_simulator():
    from dread.engines import msp430_check
    bad = msp430_check.run_check(240, 7)
    assert not bad, bad[:3]
