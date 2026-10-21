"""dread.switches: a switch's cases found by running its dispatch, whatever the bound check's shape."""
import subprocess

from dread import elfimport, switches
from dread.program import Program
from dread.target import Target
from conftest import RISCV_CROSS, RISCV_MARCH, needs_riscv_gcc, riscv_toml

BOUND_FIRST_S = """
    .text
    .globl sw
    .type sw, @function
sw: li a5, 4
    andi a2, a2, 0xff           # another register bounded in between (GCC 12)
    bltu a5, a0, 9f             # the bound first, the index second
    lui a5, %hi(table)
    addi a5, a5, %lo(table)
    slli a3, a0, 2
    add a3, a3, a5
    lw a5, 0(a3)
    jr a5
1:  li a0, 10
    ret
2:  li a0, 20
    ret
3:  li a0, 30
    ret
4:  li a0, 40
    ret
5:  li a0, 50
    ret
9:  li a0, 0
    ret
    .size sw, .-sw
    .section .rodata
    .align 2
table:
    .word 1b, 2b, 3b, 4b, 5b
"""

TARGET = """[target]
name = "switch"
[arch]
name = "riscv"
isa = "rv32"
extensions = "imac"
[memory]
regions = [{name = "rom", base = 0x80000000, size = 0x10000, kind = "rom"},
           {name = "ram", base = 0x80080000, size = 0x10000}]
stack = {top = 0x80090000, size = 0x1000}
sentinel = 0x7fff0000
[image.main]
original = "original/t.bin"
base = 0x80000000
[compiler]
cross = "riscv64-elf-"
cflags = ["-march=rv32imac_zicsr", "-mabi=ilp32"]
"""


@needs_riscv_gcc
def test_a_risc_v_bound_check_with_the_bound_first_and_an_instruction_between(tmp_path):
    """GCC 12 on RISC-V loads the bound with li, puts an andi of another register between, and
    branches with the bound first (bltu a5, s3: FreeRTOS's xTaskGenericNotify and
    xTaskGenericNotifyFromISR): the li was only joined to the branch right behind it, and the andi
    of the other register was taken for the bound check, so the run went the same way for every
    value and one case of five was found. The bound's li is found a few instructions in front now,
    and a bound check counts only when its register feeds the jump: all five cases."""
    (tmp_path / "t.S").write_text(BOUND_FIRST_S)
    (tmp_path / "t.ld").write_text("SECTIONS { . = 0x80000000; .text : { *(.text*) } .rodata : { *(.rodata*) } }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run([RISCV_CROSS + "gcc", "-march=" + RISCV_MARCH, "-mabi=ilp32", "-nostdlib", "-T",
                    str(tmp_path / "t.ld"), "-Wl,-e,sw", "-o", elf, str(tmp_path / "t.S")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml(TARGET))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    fn = p.by_name["sw"]
    jump = next(a for a in fn.insns if p.arch.flow(p.insn(a))[0] == "ijump")
    inside, outside = switches.resolve(p, fn, jump)
    assert len(inside) == 5 and not outside, [hex(a) for a in inside]
    assert switches.mapping(p, fn, jump) is not None


LOOP_SWITCHES_S = """
    .text
    .globl next_index, base_sw, bound_sw, adjusted_sw
    .type next_index, @function
next_index:
    li a0, 0
    ret
    .size next_index, .-next_index
    .type base_sw, @function
base_sw:                        # the table's address kept in s1 across the loop (GCC: prvBlockingTask)
    addi sp, sp, -16
    sw ra, 12(sp)
    sw s1, 8(sp)
    lui s1, %hi(table_a)
    addi s1, s1, %lo(table_a)
1:  call next_index
    li a4, 3
    bltu a4, a0, 9f
    slli a0, a0, 2
    add a0, a0, s1
    lw a0, 0(a0)
    jr a0
.La2:  j 1b
.La3:  j 1b
.La4:  j 1b
.La5:  j 1b
9:  lw ra, 12(sp)
    lw s1, 8(sp)
    addi sp, sp, 16
    ret
    .size base_sw, .-base_sw
    .type bound_sw, @function
bound_sw:                       # the bound kept in s5 as well (clang: prvBlockingTask)
    addi sp, sp, -16
    sw ra, 12(sp)
    sw s1, 8(sp)
    sw s5, 4(sp)
    lui s1, %hi(table_b)
    addi s1, s1, %lo(table_b)
    li s5, 3
1:  call next_index
    bltu s5, a0, 9f
    slli a0, a0, 2
    add a0, a0, s1
    lw a0, 0(a0)
    jr a0
.Lb2:  j 1b
.Lb3:  j 1b
.Lb4:  j 1b
.Lb5:  j 1b
9:  lw ra, 12(sp)
    lw s1, 8(sp)
    lw s5, 4(sp)
    addi sp, sp, 16
    ret
    .size bound_sw, .-bound_sw
    .type adjusted_sw, @function
adjusted_sw:                    # the index adjusted between the bound's li and the branch (prvTimerTask)
    addi sp, sp, -16
    sw ra, 12(sp)
    sw s1, 8(sp)
    lui s1, %hi(table_c)
    addi s1, s1, %lo(table_c)
1:  call next_index
    li a4, 3
    addi a0, a0, -1
    bltu a4, a0, 9f
    slli a0, a0, 2
    add a0, a0, s1
    lw a0, 0(a0)
    jr a0
.Lc2:  j 1b
.Lc3:  j 1b
.Lc4:  j 1b
.Lc5:  j 1b
9:  lw ra, 12(sp)
    lw s1, 8(sp)
    addi sp, sp, 16
    ret
    .size adjusted_sw, .-adjusted_sw
    .section .rodata
    .align 2
table_a: .word .La2, .La3, .La4, .La5
table_b: .word .Lb2, .Lb3, .Lb4, .Lb5
table_c: .word .Lc2, .Lc3, .Lc4, .Lc5
"""


def _riscv_program(tmp_path, source, entry):
    (tmp_path / "t.S").write_text(source)
    (tmp_path / "t.ld").write_text("SECTIONS { . = 0x80000000; .text : { *(.text*) } .rodata : { *(.rodata*) } }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run([RISCV_CROSS + "gcc", "-march=" + RISCV_MARCH, "-mabi=ilp32", "-nostdlib", "-T",
                    str(tmp_path / "t.ld"), "-Wl,-e," + entry, "-o", elf, str(tmp_path / "t.S")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml(TARGET))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    return Program(t, t.image())


@needs_riscv_gcc
def test_risc_v_switches_whose_table_or_bound_was_loaded_before_the_loop(tmp_path):
    """RISC-V has no table jump relative to pc, so compilers load a switch's table address in front
    of the loop into a callee-saved register (GCC: s1 in FreeRTOS's prvBlockingTask), clang the bound
    as well (s5), and GCC adjusts the index between the bound's li and the branch (prvTimerTask:
    li a4, 8; addi a5, a5, -1; bltu a4, a5). The runs in front of the jump knew neither; they start
    with the constants the lifter's states know there now, a register known to hold a constant can
    be the bound, and the bound's li may stand behind a write of the index. Before: none of the three
    resolved (on every RISC-V build of the benchmark, three functions refused)."""
    p = _riscv_program(tmp_path, LOOP_SWITCHES_S, "base_sw")
    for name in ("base_sw", "bound_sw", "adjusted_sw"):
        fn = p.by_name[name]
        jump = next(a for a in fn.insns if p.arch.flow(p.insn(a))[0] == "ijump")
        got = switches.resolve(p, fn, jump)
        assert got is not None and len(got[0]) == 4 and not got[1], (name, got)
        mapping = switches.mapping(p, fn, jump)
        assert mapping is not None and len(set(mapping[2].values())) == 4, (name, mapping)
        assert not switches.pointer_tail(p, fn, jump), name            # (a switch, not a call)
