"""System calls in readable C: what the target says a handler reads ([abi] syscall_reads).

The lifters bind every argument register to a system call (svc r0-r3, ecall a0-a7), in and out: their C
cannot know what the handler reads. A function whose entry registers flow into the binding took them as
parameters (FreeRTOS's RISC-V vTaskDelay around its yield). Where the target says what the handler reads,
readable C binds those and the registers read after the call (dread.tidy syscall_binds), dread.regflow
counts them read at the call, and the cases log them at each system call (dread.emu); where it says
nothing, all stays as it was. The programs here are assembly of our own."""
import re
import shutil

import pytest

from conftest import needs_riscv_gcc
from dread import cparse, lift, readable, regflow
from dread.program import Program

needs_arm = pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-ld")),
                               reason="needs arm-none-eabi binutils")

YIELD_RV_S = r"""
    .text
    .global yield_if, yield_in_loop, sys_write, use
    .type yield_if, @function
yield_if:                       # ecall when a0 is zero (FreeRTOS's portYIELD); a0 comes back as it went
    bnez    a0, 1f
    ecall
1:  ret
    .size yield_if, . - yield_if
    .type yield_in_loop, @function
yield_in_loop:                  # yields a0 times, counting in a3; returns the count
    li      a3, 0
2:  beqz    a0, 3f
    ecall
    addi    a3, a3, 1
    addi    a0, a0, -1
    j       2b
3:  mv      a0, a3
    ret
    .size yield_in_loop, . - yield_in_loop
    .type sys_write, @function
sys_write:                      # write(fd, buf, len) as Linux's RISC-V ABI makes it: the number in a7
    li      a7, 64
    ecall
    ret
    .size sys_write, . - sys_write
    .type use, @function
use:
    addi    sp, sp, -16
    sw      ra, 12(sp)
    call    yield_if
    lui     a1, %hi(0x80000210)
    sw      a0, %lo(0x80000210)(a1)
    li      a0, 3
    call    yield_in_loop
    lui     a1, %hi(0x80000214)
    sw      a0, %lo(0x80000214)(a1)
    li      a0, 1
    lui     a1, %hi(0x80000300)
    li      a2, 5
    call    sys_write
    lui     a1, %hi(0x80000218)
    sw      a0, %lo(0x80000218)(a1)
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size use, . - use
"""
NAMES = ["yield_if", "yield_in_loop", "sys_write", "use"]


def rv_target(tmp_path, reads=None):
    from test_results import riscv_program
    from dread.target import Target
    tmp_path.mkdir(parents=True, exist_ok=True)
    t = riscv_program(tmp_path, YIELD_RV_S, "use")
    if reads is None:
        return t
    toml = tmp_path / "dread.toml"
    toml.write_text(toml.read_text() + "\n[abi]\nsyscall_reads = [%s]\n" % ", ".join('"%s"' % r for r in reads))
    return Target.load(str(tmp_path))


def texts(t, names):
    p = Program(t, t.image())
    flow = regflow.analyse(p)
    context = readable.Context(t, t.image(), p, flow)
    lifter = lift.make_lifter(p, flow)
    return {n: readable.transform(n, lifter.lift(p.by_name[n]).text, context=context) for n in names}, flow


@needs_riscv_gcc
def test_without_a_statement_every_register_stays_bound(tmp_path):
    """A target that says nothing of its system calls: every argument register is bound to the ecall,
    and the functions take them all, as before."""
    got, _ = texts(rv_target(tmp_path), ["yield_if"])
    assert "u32 yield_if(u32 a0, u32 a1, u32 a2, u32 a3, u32 a4, u32 a5, u32 a6, u32 a7)" in got["yield_if"]
    assert '"+r"(x7)' in got["yield_if"], got["yield_if"]


@needs_riscv_gcc
def test_a_handler_that_reads_nothing_takes_no_parameters(tmp_path):
    """syscall_reads = [] (FreeRTOS's yield reads no argument register): yield_if binds a0 alone, which
    it returns, and takes a0; yield_in_loop binds a0 and a3, which the loop reads after each ecall, and
    takes a0; both gates take every function (the cases log no register at an ecall, as before)."""
    t = rv_target(tmp_path, [])
    got, _ = texts(t, ["yield_if", "yield_in_loop"])
    y = got["yield_if"]
    assert "u32 yield_if(u32 a0)" in y and 'register u32 x0 __asm__("a0") = a0;' in y, y
    assert '"ecall" : "+r"(x0) :' in y and "a1" not in y, y
    loop = got["yield_in_loop"]
    assert "u32 yield_in_loop(u32 a0)" in loop, loop
    assert 'register u32 x0 __asm__("a0") = a0, x3 __asm__("a3") = a3;' in loop, loop
    lines = []
    assert sorted(lift.check(t, t.image(), out=lines.append, jobs=2)) == sorted(NAMES), lines
    assert sorted(readable.check(t, t.image(), out=lines.append, jobs=2)) == sorted(NAMES), lines


def _calls(stmts):
    for s in stmts:
        if isinstance(s, cparse.Asm):
            yield s
        for inner in readable.tidy._inner(s):
            yield from _calls([inner])


@needs_riscv_gcc
def test_a_handler_that_reads_registers_gets_them_and_the_gate_compares_them(tmp_path, monkeypatch):
    """syscall_reads = ["a0", "a1", "a2", "a7"] (a write as Linux's RISC-V ABI makes it): regflow counts
    them read at the ecall, so sys_write reads the three it hands on and its caller passes them (before,
    an ecall read nothing and the call lost them); readable C binds the four; each ecall of a case logs
    them. A stage that hands the handler another number in a7 keeps sys_write lifted."""
    t = rv_target(tmp_path, ["a0", "a1", "a2", "a7"])
    got, flow = texts(t, ["sys_write", "use"])
    assert flow.read_regs("sys_write") == ["a0", "a1", "a2"]
    w = got["sys_write"]
    assert re.search(r"u32 sys_write\(u32 a0, u32 a1, u32 a2\)", w), w
    assert all('__asm__("a%d")' % i in w for i in (0, 1, 2, 7)) and '__asm__("a3")' not in w, w
    assert re.search(r"sys_write\(1, 0x80000000u, 5\)", got["use"]), got["use"]
    lines = []
    assert sorted(lift.check(t, t.image(), out=lines.append, jobs=2)) == sorted(NAMES), lines
    assert sorted(readable.check(t, t.image(), out=lines.append, jobs=1)) == sorted(NAMES), lines

    def other_number(fn, run=None):
        if fn.name != "sys_write":
            return
        for s in readable.tidy.every_list(fn) + [b.body for b in _blocks(fn.body.body)]:
            for x in s:
                if isinstance(x, cparse.Decl):
                    for d in x.items:
                        if d.asm == '__asm__("a7")':
                            d.init = cparse.Num("65")
    monkeypatch.setattr(readable, "STAGES", readable.STAGES + [("break", other_number)])
    lines = []
    accepted = readable.check(t, t.image(), out=lines.append, jobs=1)     # (in this process: the stage)
    assert accepted == sorted(set(NAMES) - {"sys_write"}), lines


def _blocks(stmts):
    for s in stmts:
        if isinstance(s, cparse.Block):
            yield s
        for inner in readable.tidy._inner(s):
            yield from _blocks([inner])


TRAP_A32_S = r"""
    .syntax unified
    .arch armv4t
    .arm
    .text
    .global trap_if, use
    .type trap_if, %function
trap_if:                        @ svc #0 when r0 is zero (a first task's start, a yield); r0 back as it went
    push    {r4, lr}
    cmp     r0, #0
    bne     1f
    svc     #0
1:  pop     {r4, lr}
    bx      lr
    .size trap_if, . - trap_if
    .type use, %function
use:
    push    {r4, lr}
    bl      trap_if
    ldr     r1, =0x40000110
    str     r0, [r1]
    pop     {r4, lr}
    bx      lr
    .ltorg
    .size use, . - use
"""


@needs_arm
def test_an_svc_binds_what_is_read_after_it(tmp_path):
    """The same on ARM: syscall_reads = [] and trap_if binds r0 alone (it returns it) and takes r0; both
    gates take both functions."""
    from test_lift_results import A_TOML, arm_program
    t = arm_program(tmp_path, "a32", TRAP_A32_S, toml=A_TOML + "\n[abi]\nsyscall_reads = []\n")
    got, _ = texts(t, ["trap_if"])
    c = got["trap_if"]
    assert "u32 trap_if(u32 r0)" in c and '"svc #0" : "+r"(x0) :' in c and '__asm__("r1")' not in c, c
    lines = []
    assert sorted(lift.check(t, t.image(), out=lines.append, jobs=2)) == ["trap_if", "use"], lines
    assert sorted(readable.check(t, t.image(), out=lines.append, jobs=2)) == ["trap_if", "use"], lines


def test_the_setting_names_registers():
    """[abi] syscall_reads takes register names of the architecture; anything else is refused."""
    from dread.arch import get as arch_get
    from dread.target import Abi
    arch = arch_get({"name": "riscv", "isa": "rv32", "extensions": "imac"})
    assert Abi({"syscall_reads": ["a7", "a0"]}, arch).syscall_reads == ["a7", "a0"]
    assert Abi({}, arch).syscall_reads is None
    with pytest.raises(Exception):
        Abi({"syscall_reads": ["q9"]}, arch)
