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
