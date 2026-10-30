"""dread lift: functions whose callers read registers beyond a C result (an old ARM compiler's
__value_in_regs returns a structure of up to four words in r0-r3; a RISC-V function can leave values
in a2 and a3) are lifted with a naked entry that hands them back (Lifter.more_results): ARM state,
Thumb-2, Thumb-1 and RISC-V. The cases compare those registers, a wrong one is caught, callers that
stay machine code find them where the original leaves them, and the system run agrees on the whole
image. Callers that read them behind the call are bound calls (RISC-V has them since). A bound call
hands r12 to a callee that reads it as its caller left it. Programs of our own, in assembly."""
import shutil
import subprocess

import pytest

from dread import elfimport, lift, regflow
from dread.program import Program
from dread.target import Target
from conftest import RISCV_CROSS, RISCV_MARCH, needs_riscv_gcc, riscv_toml

needs_arm = pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                               reason="needs arm-none-eabi-as and -gcc")
needs_clang = pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")), reason="needs clang and ld.lld")

# The four-word function, two that hand its words on (a tail call, a call), two that read them, one
# with a fifth argument on the stack and its caller, one that reads what its caller left in r4.
QUAD_A32_S = """
    .syntax unified
    .arch armv4t
    .arm
    .text
    .global quad_pair_split, quad_forward_tail, quad_forward_call, quad_sum_reader, quad_store_reader
    .global quad_fifth_arg, quad_fifth_user, quad_held_bias, use
    .type quad_pair_split, %function
quad_pair_split:                @ four words back in r0-r3
    add     r2, r0, r1
    eor     r3, r0, r1
    sub     r1, r0, r1
    and     r0, r0, r2
    bx      lr
    .size quad_pair_split, . - quad_pair_split
    .type quad_forward_tail, %function
quad_forward_tail:              @ its callers read the four words of the function it jumps to
    add     r0, r0, #1
    b       quad_pair_split
    .size quad_forward_tail, . - quad_forward_tail
    .type quad_forward_call, %function
quad_forward_call:              @ hands back the callee's four words, one changed
    push    {r4, lr}
    mov     r4, r1
    bl      quad_pair_split
    add     r3, r3, r4
    pop     {r4, lr}
    bx      lr
    .size quad_forward_call, . - quad_forward_call
    .type quad_sum_reader, %function
quad_sum_reader:                @ reads all four
    push    {r4, lr}
    bl      quad_pair_split
    add     r0, r0, r1, lsl #1
    add     r0, r0, r2, lsl #2
    add     r0, r0, r3, lsl #3
    pop     {r4, lr}
    bx      lr
    .size quad_sum_reader, . - quad_sum_reader
    .type quad_store_reader, %function
quad_store_reader:              @ stores the four words of both forwarders
    push    {r4, r5, r6, lr}
    mov     r5, r0
    mov     r6, r1
    bl      quad_forward_tail
    ldr     r4, =0x40000100
    stmia   r4!, {r0-r3}
    mov     r0, r5
    mov     r1, r6
    bl      quad_forward_call
    stmia   r4!, {r0-r3}
    pop     {r4, r5, r6, lr}
    bx      lr
    .ltorg
    .size quad_store_reader, . - quad_store_reader
    .type quad_fifth_arg, %function
quad_fifth_arg:                 @ a fifth argument on the stack
    push    {r4, lr}
    ldr     r4, [sp, #8]
    bl      quad_pair_split
    eor     r2, r2, r4
    pop     {r4, lr}
    bx      lr
    .size quad_fifth_arg, . - quad_fifth_arg
    .type quad_fifth_user, %function
quad_fifth_user:                @ passes it a stack word and reads two of its words
    push    {r4, lr}
    sub     sp, sp, #8
    str     r1, [sp]
    bl      quad_fifth_arg
    add     sp, sp, #8
    add     r0, r0, r3
    add     r0, r0, r2
    pop     {r4, lr}
    bx      lr
    .size quad_fifth_user, . - quad_fifth_user
    .type quad_held_bias, %function
quad_held_bias:                 @ reads what its caller left in r4
    add     r0, r0, r4
    b       quad_pair_split
    .size quad_held_bias, . - quad_held_bias
    .type use, %function
use:
    push    {r4, lr}
    bl      quad_sum_reader
    bl      quad_store_reader
    mov     r4, #5
    bl      quad_held_bias
    add     r0, r0, r2
    bl      quad_fifth_user
    pop     {r4, lr}
    bx      lr
    .size use, . - use
"""

QUAD_T2_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global quad_pair_split, quad_forward_tail, quad_forward_call, quad_sum_reader, quad_store_reader
    .global quad_fifth_arg, quad_fifth_user, quad_held_bias, use
    .type quad_pair_split, %function
    .thumb_func
quad_pair_split:                @ four words back in r0-r3
    adds    r2, r0, r1
    eors    r3, r0, r1
    subs    r1, r0, r1
    ands    r0, r0, r2
    bx      lr
    .size quad_pair_split, . - quad_pair_split
    .type quad_forward_tail, %function
    .thumb_func
quad_forward_tail:              @ its callers read the four words of the function it jumps to
    adds    r0, r0, #1
    b       quad_pair_split
    .size quad_forward_tail, . - quad_forward_tail
    .type quad_forward_call, %function
    .thumb_func
quad_forward_call:              @ hands back the callee's four words, one changed
    push    {r4, lr}
    mov     r4, r1
    bl      quad_pair_split
    add     r3, r3, r4
    pop     {r4, pc}
    .size quad_forward_call, . - quad_forward_call
    .type quad_sum_reader, %function
    .thumb_func
quad_sum_reader:                @ reads all four
    push    {r4, lr}
    bl      quad_pair_split
    add     r0, r0, r1, lsl #1
    add     r0, r0, r2, lsl #2
    add     r0, r0, r3, lsl #3
    pop     {r4, pc}
    .size quad_sum_reader, . - quad_sum_reader
    .type quad_store_reader, %function
    .thumb_func
quad_store_reader:              @ stores the four words of both forwarders
    push    {r4, r5, r6, lr}
    mov     r5, r0
    mov     r6, r1
    bl      quad_forward_tail
    ldr     r4, =0x20000100
    stmia   r4!, {r0-r3}
    mov     r0, r5
    mov     r1, r6
    bl      quad_forward_call
    stmia   r4!, {r0-r3}
    pop     {r4, r5, r6, pc}
    .ltorg
    .size quad_store_reader, . - quad_store_reader
    .type quad_fifth_arg, %function
    .thumb_func
quad_fifth_arg:                 @ a fifth argument on the stack
    push    {r4, lr}
    ldr     r4, [sp, #8]
    bl      quad_pair_split
    eors    r2, r2, r4
    pop     {r4, pc}
    .size quad_fifth_arg, . - quad_fifth_arg
    .type quad_fifth_user, %function
    .thumb_func
quad_fifth_user:                @ passes it a stack word and reads two of its words
    push    {r4, lr}
    sub     sp, #8
    str     r1, [sp]
    bl      quad_fifth_arg
    add     sp, #8
    add     r0, r0, r3
    add     r0, r0, r2
    pop     {r4, pc}
    .size quad_fifth_user, . - quad_fifth_user
    .type quad_held_bias, %function
    .thumb_func
quad_held_bias:                 @ reads what its caller left in r4
    adds    r0, r0, r4
    b       quad_pair_split
    .size quad_held_bias, . - quad_held_bias
    .type use, %function
    .thumb_func
use:
    push    {r4, lr}
    bl      quad_sum_reader
    bl      quad_store_reader
    movs    r4, #5
    bl      quad_held_bias
    add     r0, r0, r2
    bl      quad_fifth_user
    pop     {r4, pc}
    .size use, . - use
"""

# Thumb-1 (a Cortex-M0): no stores of lr, no wrapped frame (Lifter.can_wrap). Functions shorter than
# a branch of their own (16 bytes) need room within reach of a narrow branch: the free range behind.
QUAD_T1_S = """
    .syntax unified
    .cpu cortex-m0
    .thumb
    .text
    .global quad_pair_split, quad_forward_tail, quad_forward_call, quad_sum_reader, quad_store_reader
    .global quad_fifth_arg, quad_fifth_user, quad_held_bias, use
    .type quad_pair_split, %function
    .thumb_func
quad_pair_split:                @ four words back in r0-r3
    adds    r2, r0, r1
    mov     r3, r0
    eors    r3, r1
    subs    r1, r0, r1
    ands    r0, r2
    bx      lr
    .size quad_pair_split, . - quad_pair_split
    .type quad_forward_tail, %function
    .thumb_func
quad_forward_tail:              @ its callers read the four words of the function it jumps to
    adds    r0, r0, #1
    b       quad_pair_split
    .size quad_forward_tail, . - quad_forward_tail
    .type quad_forward_call, %function
    .thumb_func
quad_forward_call:              @ hands back the callee's four words, one changed
    push    {r4, lr}
    mov     r4, r1
    bl      quad_pair_split
    adds    r3, r3, r4
    pop     {r4, pc}
    .size quad_forward_call, . - quad_forward_call
    .type quad_sum_reader, %function
    .thumb_func
quad_sum_reader:                @ reads all four
    push    {r4, lr}
    bl      quad_pair_split
    lsls    r1, r1, #1
    adds    r0, r0, r1
    lsls    r2, r2, #2
    adds    r0, r0, r2
    lsls    r3, r3, #3
    adds    r0, r0, r3
    pop     {r4, pc}
    .size quad_sum_reader, . - quad_sum_reader
    .type quad_store_reader, %function
    .thumb_func
quad_store_reader:              @ stores the four words of both forwarders
    push    {r4, r5, r6, lr}
    mov     r5, r0
    mov     r6, r1
    bl      quad_forward_tail
    ldr     r4, =0x20000100
    stmia   r4!, {r0-r3}
    mov     r0, r5
    mov     r1, r6
    bl      quad_forward_call
    stmia   r4!, {r0-r3}
    pop     {r4, r5, r6, pc}
    .ltorg
    .size quad_store_reader, . - quad_store_reader
    .type quad_fifth_arg, %function
    .thumb_func
quad_fifth_arg:                 @ a fifth argument on the stack
    push    {r4, lr}
    ldr     r4, [sp, #8]
    bl      quad_pair_split
    eors    r2, r4
    pop     {r4, pc}
    .size quad_fifth_arg, . - quad_fifth_arg
    .type quad_fifth_user, %function
    .thumb_func
quad_fifth_user:                @ passes it a stack word and reads two of its words
    push    {r4, lr}
    sub     sp, #8
    str     r1, [sp]
    bl      quad_fifth_arg
    add     sp, #8
    adds    r0, r0, r3
    adds    r0, r0, r2
    pop     {r4, pc}
    .size quad_fifth_user, . - quad_fifth_user
    .type quad_held_bias, %function
    .thumb_func
quad_held_bias:                 @ reads what its caller left in r4
    adds    r0, r0, r4
    b       quad_pair_split
    .size quad_held_bias, . - quad_held_bias
    .type use, %function
    .thumb_func
use:
    push    {r4, lr}
    bl      quad_sum_reader
    bl      quad_store_reader
    movs    r4, #5
    bl      quad_held_bias
    adds    r0, r0, r2
    bl      quad_fifth_user
    pop     {r4, pc}
    .size use, . - use
    .org    0x200
    .space  0x400
"""

M_TOML = '''
[target]
name = "quad-%(cpu)s"

[arch]
name = "arm"
isa = "t32"
profile = "m"
cpu = "%(cpu)s"

[memory]
regions = [
    {name = "flash", base = 0x08000000, size = 0x100000, kind = "rom"},
    {name = "sram", base = 0x20000000, size = 0x5000},
    {name = "scratch", base = 0x20100000, size = 0x20000},
]
io = [[0x40000000, 0x40020000], [0xe000e000, 0xe000f000]]
stack = {top = 0x20005000, size = 0x1000}
sentinel = 0x1fff0000
scratch = {base = 0x20100000, size = 0x20000}

[image.main]
original = "original/t.bin"
base = 0x08000000
limit = 0x08010000
%(more)s
%(compiler)s
'''
M_GCC = '''[compiler]
cross = "arm-none-eabi-"
cflags = ["-mthumb", "-mcpu=%s", "-Os"]
'''
M_CLANG = '''[compiler]
kind = "clang"
triple = "thumbv7m-none-eabi"
cflags = ["-mcpu=cortex-m3", "-mfloat-abi=soft", "-Os", "-ffreestanding"]
'''

A_TOML = '''
[target]
name = "quad-a32"

[arch]
name = "arm"
isa = "a32"
profile = "a"

[memory]
regions = [
    {name = "flash", base = 0x0, size = 0x100000, kind = "rom"},
    {name = "ram", base = 0x40000000, size = 0x10000},
    {name = "scratch", base = 0x40100000, size = 0x20000},
]
io = [[0x80000000, 0x80010000]]
stack = {top = 0x40010000, size = 0x2000}
sentinel = 0x0fff0000
scratch = {base = 0x40100000, size = 0x20000}

[image.main]
original = "original/t.bin"
base = 0x20000
limit = 0x30000

[compiler]
cross = "arm-none-eabi-"
cflags = ["-march=armv4t", "-marm", "-mabi=aapcs", "-mfloat-abi=soft", "-Os", "-ffreestanding"]
'''

QUAD_NAMES = ["quad_pair_split", "quad_forward_tail", "quad_forward_call", "quad_sum_reader", "quad_store_reader",
              "quad_fifth_arg", "quad_fifth_user", "quad_held_bias", "use"]


def arm_program(tmp_path, kind, source=None, entry="use", toml=None):
    """The program built and its tables imported: a target in tmp_path. kind: "a32" (ARMv4T in ARM
    state), "t2" (a Cortex-M3), "t2-clang" (the same, its C compiled by clang) or "t1" (a Cortex-M0)."""
    source = source or {"a32": QUAD_A32_S, "t2": QUAD_T2_S, "t2-clang": QUAD_T2_S, "t1": QUAD_T1_S}[kind]
    (tmp_path / "t.s").write_text(source)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    base = "0x20000" if kind == "a32" else "0x08000000"
    subprocess.run(["arm-none-eabi-ld", "-Ttext=" + base, "-e", entry, "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    if toml is None:
        cpu = "cortex-m0" if kind == "t1" else "cortex-m3"
        toml = A_TOML if kind == "a32" else M_TOML % {
            "cpu": cpu, "more": "free = [[0x08000200, 0x08000600]]\n" if kind == "t1" else "",
            "compiler": M_CLANG if kind == "t2-clang" else M_GCC % cpu}
    (tmp_path / "dread.toml").write_text(toml)
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    return Target.load(str(tmp_path))


@needs_arm
@pytest.mark.parametrize("kind", ["a32", "t2", pytest.param("t2-clang", marks=needs_clang), "t1"])
def test_results_in_r2_and_r3_are_handed_back_by_an_entry(tmp_path, kind):
    """quad_pair_split returns four words in r0-r3, and its callers read all four: C returns r0 and r1
    only, so it was refused ("its callers read r2, which it sets ...; C returns r0 and r1 only"), and so
    were quad_forward_tail and quad_forward_call, which hand its words on, and the others that hand
    back four words. Now the C body stores r2 and r3 into an area its naked entry reserves, and the
    entry loads them before it returns: lifted, every case agrees and runs every instruction. The
    entry changes no register but lr (Thumb-1: one of r4-r7 it saves, not one handed in: r5 for
    quad_held_bias, which reads its caller's r4) before the body runs. A tail call and a call whose
    words the function hands back are bound calls (the bl in an asm statement with r0-r3 bound).
    quad_fifth_arg takes a stack argument too: wrapped (its frame and stack arguments on the real
    stack, the area below them), not on Thumb-1, where it stays refused. Its caller quad_fifth_user
    reads r2 behind it: a bound call that also passes the stack argument (the asm reserves it below the
    stack pointer and stores it before the bl), lifted and accepted, except on Thumb-1, where a bound
    call makes no stack arguments and it stays refused. With clang the C gets no register as its caller
    left it (no -ffixed-REG): quad_held_bias stays refused there, its caller use binds r4 all the
    same."""
    t = arm_program(tmp_path, kind)
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))
    lifted, refused = {}, {}
    for name in QUAD_NAMES:
        try:
            lifted[name] = lifter.lift(p.by_name[name])
        except lift.Refused as e:
            refused[name] = str(e)
    for name in ("quad_pair_split", "quad_forward_tail", "quad_forward_call"):
        got = lifted[name]
        assert got.more == ["r2", "r3"], (name, got.more)
        assert "__attribute__((naked)) void %s(void)" % name in got.text
        assert "u64 %s_dread_body(u32 a0, u32 a1, u32 a2, u32 a3, u32 more)" % name in got.text
        assert "*(volatile u32 *)(more) = r2; *(volatile u32 *)(more + 4) = r3; return ((u64)r1 << 32 | r0);" \
            in got.text, got.text
    if kind == "t2-clang":
        assert "reads r4 before anything writes it" in refused["quad_held_bias"], refused
        assert 'x4 __asm__("r4") = r4' in lifted["use"].text
    else:
        held = lifted["quad_held_bias"]         # (use reads r0, r1 and r2 behind it: r2 alone handed back)
        assert held.more == ["r2"] and "*(volatile u32 *)(more) = r2; return ((u64)r1 << 32 | r0);" in held.text, \
            held.text
    for name in ("quad_sum_reader", "quad_store_reader"):
        assert lifted[name].more == [] and '"bl quad_' in lifted[name].text
    assert '"bl quad_pair_split"' in lifted["quad_forward_tail"].text           # a bound tail call
    entry = lifted["quad_pair_split" if kind == "t2-clang" else "quad_held_bias"].text
    if kind == "t1":
        assert lifted["quad_held_bias"].handed == ["r4"] and '"push {r5, lr}' in entry, entry
        assert "takes stack arguments" in refused["quad_fifth_arg"], refused
        # Thumb-1 makes no bound call with stack arguments: the caller stays refused
        assert "Thumb-1 does not make" in refused["quad_fifth_user"], refused
    else:
        assert '"sub sp, sp, #16\\n\\tstr lr, [sp, #4]\\n\\tadd lr, sp, #8\\n\\tstr lr, [sp]' in entry, entry
        wrapped = lifted["quad_fifth_arg"].text
        assert "u32 a3, u32 sp0)" in wrapped and "*(volatile u32 *)(sp0 - 16) = r2;" in wrapped, wrapped
        # quad_fifth_user reads r2 behind quad_fifth_arg (which takes a stack argument): a bound call
        # that reserves the stack argument below the stack pointer and stores it before the bl
        user = lifted["quad_fifth_user"].text
        assert 'sub sp, sp, #8\\n\\tstr %[sa0], [sp, #0]\\n\\tbl quad_fifth_arg\\n\\tadd sp, sp, #8' in user \
            and '[sa0] "r"(*(volatile u32 *)(sp0 - 16))' in user, user
    lines = []
    accepted = lift.check(t, t.image(), QUAD_NAMES, out=lines.append, jobs=2)
    expected = set(QUAD_NAMES) - set(refused)
    assert set(accepted) == expected, "\n".join(lines)


@needs_arm
def test_a_wrong_r3_is_caught_by_the_cases(tmp_path, monkeypatch):
    """The cases compare r2 and r3 at the return (dread.runner: every register the callers read after
    the call, dread.regflow's kept): a body that hands back a wrong r3 is dropped."""
    t = arm_program(tmp_path, "t2")
    more_out = lift.Lifter._more_out

    def wrong_r3(self):
        return more_out(self).replace("= r3;", "= r3 ^ 0x10;")
    monkeypatch.setattr(lift.Lifter, "_more_out", wrong_r3)
    lines = []
    assert lift.check(t, t.image(), ["quad_pair_split"], out=lines.append, jobs=2) == [], "\n".join(lines)
    from dread.gui import read_tsv
    verdicts = {r["function"]: r for r in read_tsv(str(tmp_path / "build" / "lift-check" / "main" / "verdicts.tsv"))}
    assert verdicts["quad_pair_split"]["verdict"] == "dropped", verdicts
    assert "r3 after the return" in verdicts["quad_pair_split"]["reason"], verdicts["quad_pair_split"]


@needs_arm
@pytest.mark.parametrize("kind", ["a32", "t1"])
def test_callers_that_stay_machine_code_find_the_registers(tmp_path, kind):
    """The entry is at the original's place (or a branch there leads to it), so callers that stay the
    original's code read r2 and r3 where the original leaves them: with only the four-word functions
    built from C, the callers in machine code compute and store what they do on the original."""
    from dread.emu import Layout, Machine
    from dread.overlay import load_placement
    t = arm_program(tmp_path, kind)
    p = Program(t, t.image())
    names = ["quad_pair_split", "quad_forward_tail", "quad_forward_call"]
    lines = []
    assert sorted(lift.check(t, t.image(), names, out=lines.append, jobs=2)) == sorted(names), "\n".join(lines)
    work = tmp_path / "build" / "lift-check" / "main"
    aliases, _ = load_placement(str(work / "placement.json"))
    rebuilt = (work / "payload.bin").read_bytes()
    assert rebuilt != t.image().payload()
    got = []
    for payload, al in ((t.image().payload(), None), (rebuilt, aliases)):
        m = Machine(Layout(t, t.image()), payload, al)
        runs = []
        for name, args in (("quad_sum_reader", (0x1234, 0x0f0f)), ("quad_store_reader", (0x8001, 0x7fff))):
            r = m.call(p.by_name[name].entry, *args, max_instructions=20_000)
            runs.append((r.ret, sorted(r.writes.items())))
        m.kept = ("r1", "r2", "r3")
        r = m.call(p.by_name["quad_pair_split"].entry, 0x1234, 0x0f0f, max_instructions=20_000)
        runs.append((r.ret, r.kept))
        got.append(runs)
    assert got[0] == got[1], got
    assert dict(got[0][2][1]) == {"r1": 0x1234 - 0x0f0f, "r2": 0x1234 + 0x0f0f, "r3": 0x1234 ^ 0x0f0f}, got[0]


@needs_arm
def test_a_stub_of_a_four_word_function_answers_the_extra_words(tmp_path):
    """A generic stub (dread.autocase: a case's calls) of a function whose callers read r2 and r3 that
    it sets answers those too, derived from its answer as a wide_results callee's r1 is: quad_sum_reader
    with quad_pair_split stubbed adds the stub's r2 and r3, not what was in them before the call (a
    lifted caller's C has 0 there behind an earlier C call, where the original has what that callee
    left). The functions whose runs reach such a function get case keys of their own (dread.autocase's
    scopes: the stub's answers are a fact of the function stubbed)."""
    from dread.autocase import Generator
    t = arm_program(tmp_path, "t2")
    g = Generator(t, t.image())
    entry = g.program.by_name["quad_pair_split"].entry
    assert g.more()[entry] == ["r2", "r3"]
    case = {"args": [3, 5, 0, 0], "mem": [], "returns": [7], "calls": [[entry, 3, 0]], "frame": 0}
    m = g.machine()
    m.plan = []
    g.apply(m, case)
    r = m.call(g.program.by_name["quad_sum_reader"].entry, *case["args"], max_instructions=10_000)
    word = lambda k: (7 * 0x9E3779B1 + 0x7F4A7C15 * k) & 0xFFFFFFFF
    assert r.ret == (7 + 2 * 5 + 4 * word(2) + 8 * word(3)) & 0xFFFFFFFF      # (r1 as passed: no wide_results row)
    plain = Generator(t, t.image())
    plain._more = {}
    assert plain.environment("quad_sum_reader") != g.environment("quad_sum_reader")


QUAD_RV_S = """
    .option norelax
    .text
    .global quad_pair_split, quad_forward_tail, quad_forward_call, quad_sum_reader, quad_store_reader
    .global quad_ninth_arg, held_scale_reader, held_scale_user, use
    .type quad_pair_split, @function
quad_pair_split:                # four words back in a0-a3 (the convention returns a0 and a1 only)
    add     a2, a0, a1
    xor     a3, a0, a1
    sub     a1, a0, a1
    and     a0, a0, a2
    ret
    .size quad_pair_split, . - quad_pair_split
    .type quad_forward_tail, @function
quad_forward_tail:              # its callers read the four words of the function it jumps to
    addi    a0, a0, 1
    j       quad_pair_split
    .size quad_forward_tail, . - quad_forward_tail
    .type quad_forward_call, @function
quad_forward_call:              # hands back the callee's four words, one changed
    addi    sp, sp, -16
    sw      ra, 12(sp)
    sw      s0, 8(sp)
    mv      s0, a1
    call    quad_pair_split
    add     a3, a3, s0
    lw      s0, 8(sp)
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size quad_forward_call, . - quad_forward_call
    .type quad_sum_reader, @function
quad_sum_reader:                # reads all four
    addi    sp, sp, -16
    sw      ra, 12(sp)
    call    quad_pair_split
    slli    a1, a1, 1
    add     a0, a0, a1
    slli    a2, a2, 2
    add     a0, a0, a2
    slli    a3, a3, 3
    add     a0, a0, a3
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size quad_sum_reader, . - quad_sum_reader
    .type quad_store_reader, @function
quad_store_reader:              # stores the four words of both forwarders
    addi    sp, sp, -16
    sw      ra, 12(sp)
    sw      s0, 8(sp)
    sw      s1, 4(sp)
    sw      s2, 0(sp)
    mv      s1, a0
    mv      s2, a1
    call    quad_forward_tail
    li      s0, 0x80000100
    sw      a0, 0(s0)
    sw      a1, 4(s0)
    sw      a2, 8(s0)
    sw      a3, 12(s0)
    mv      a0, s1
    mv      a1, s2
    call    quad_forward_call
    sw      a0, 16(s0)
    sw      a1, 20(s0)
    sw      a2, 24(s0)
    sw      a3, 28(s0)
    lw      s2, 0(sp)
    lw      s1, 4(sp)
    lw      s0, 8(sp)
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size quad_store_reader, . - quad_store_reader
    .type quad_ninth_arg, @function
quad_ninth_arg:                 # a ninth argument on the stack
    addi    sp, sp, -16
    sw      ra, 12(sp)
    sw      s0, 8(sp)
    lw      s0, 16(sp)
    call    quad_pair_split
    xor     a2, a2, s0
    lw      s0, 8(sp)
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size quad_ninth_arg, . - quad_ninth_arg
    .type held_scale_reader, @function
held_scale_reader:              # reads what its caller left in s1
    mul     a0, a0, s1
    ret
    .size held_scale_reader, . - held_scale_reader
    .type held_scale_user, @function
held_scale_user:
    addi    sp, sp, -16
    sw      ra, 12(sp)
    sw      s1, 8(sp)
    addi    s1, a1, 7
    call    held_scale_reader
    lw      s1, 8(sp)
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size held_scale_user, . - held_scale_user
    .type use, @function
use:
    addi    sp, sp, -32
    sw      ra, 28(sp)
    call    quad_sum_reader
    call    quad_store_reader
    sw      a1, 0(sp)
    call    quad_ninth_arg
    add     a0, a0, a3
    call    held_scale_user
    lw      ra, 28(sp)
    addi    sp, sp, 32
    ret
    .size use, . - use
"""

RV_TOML = """
[target]
name = "quad-rv32"

[arch]
name = "riscv"
isa = "rv32"
extensions = "imac"

[memory]
regions = [
    {name = "flash", base = 0x20000000, size = 0x100000, kind = "rom"},
    {name = "ram", base = 0x80000000, size = 0x10000},
    {name = "scratch", base = 0x80100000, size = 0x20000},
]
io = [[0x10000000, 0x10010000]]
stack = {top = 0x80010000, size = 0x2000}
sentinel = 0x1fff0000
scratch = {base = 0x80100000, size = 0x20000}

[image.main]
original = "original/t.bin"
base = 0x20000000
limit = 0x20020000

"""
RV_GCC = """[compiler]
cross = "riscv64-elf-"
cflags = ["-march=rv32imac_zicsr", "-mabi=ilp32", "-Os"]
"""
RV_CLANG = """[compiler]
kind = "clang"
triple = "riscv32-unknown-elf"
cflags = ["-march=rv32imac", "-mabi=ilp32", "-Os", "-mno-relax", "-ffreestanding"]
"""


@needs_riscv_gcc
@pytest.mark.parametrize("compiler", ["gcc", pytest.param("clang", marks=needs_clang)])
def test_risc_v_results_in_a2_and_a3(tmp_path, compiler):
    """RISC-V returns a0 and a1 only: the four-word function and the two that hand its words on were
    refused, and so were their callers ("reads a2 behind the call of ..."). The same entry hands a2
    and a3 back (ra the only register it changes before the body runs), the callers' calls are bound
    (the call in an asm statement with a0-a7 bound), and quad_ninth_arg, with a ninth argument on the
    stack, is wrapped with the area below its frame. Its caller, use, reads a3 behind it: a bound call
    that also passes the stack argument (the asm reserves it below the stack pointer and stores it
    before the call), lifted and accepted. A function that reads what its caller left in s1 stays
    refused, and so does its caller: the C cannot get registers as the caller left them on RISC-V
    (Lifter.can_hand). The lifted C compiled by GCC and by clang."""
    (tmp_path / "t.s").write_text(QUAD_RV_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run([RISCV_CROSS + "gcc", "-march=" + RISCV_MARCH, "-mabi=ilp32", "-c", "-o", obj, str(tmp_path / "t.s")],
                   check=True)
    subprocess.run([RISCV_CROSS + "ld", "-m", "elf32lriscv", "-Ttext=0x20000000", "-e", "use", "-o", elf, obj],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml(RV_TOML + (RV_GCC if compiler == "gcc" else RV_CLANG)))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))
    for name in ("quad_pair_split", "quad_forward_tail", "quad_forward_call"):
        got = lifter.lift(p.by_name[name])
        assert got.more == ["a2", "a3"] and "__attribute__((naked)) void %s(void)" % name in got.text, got.text
        assert "*(volatile u32 *)(more) = a2; *(volatile u32 *)(more + 4) = a3;" in got.text
    assert '"call quad_pair_split" : "+r"(x0)' in lifter.lift(p.by_name["quad_sum_reader"]).text
    ninth = lifter.lift(p.by_name["quad_ninth_arg"])      # (use reads a3 behind it: a3 alone handed back)
    assert ninth.more == ["a3"] and "u32 p7, u32 sp0)" in ninth.text and "(sp0 - 32) = a3;" in ninth.text, ninth.text
    use = lifter.lift(p.by_name["use"]).text              # a bound call to quad_ninth_arg that passes its stack argument
    assert 'addi sp, sp, -16\\n\\tsw %[sa0], 0(sp)\\n\\tcall quad_ninth_arg\\n\\taddi sp, sp, 16' in use \
        and '[sa0] "r"(*(volatile u32 *)(sp0 - 32))' in use, use
    for name, why in (("held_scale_reader", "reads s1 before anything writes it"),
                      ("held_scale_user", "which reads s1 its caller leaves")):
        with pytest.raises(lift.Refused, match=why):
            lifter.lift(p.by_name[name])
    names = ["quad_pair_split", "quad_forward_tail", "quad_forward_call", "quad_sum_reader", "quad_store_reader",
             "quad_ninth_arg", "use"]
    lines = []
    assert sorted(lift.check(t, t.image(), names, out=lines.append, jobs=2)) == sorted(names), "\n".join(lines)


IP_T2_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global ip_shift_tail, ip_shift_user, use
    .type ip_shift_tail, %function
    .thumb_func
ip_shift_tail:                  @ reads ip where its predicated write did not run (libgcc's soft-float tails)
    cmp     r1, #32
    it      le
    rsble   r12, r1, #32
    lsr     r12, r0, r12
    it      le
    orrle   r0, r0, r12
    bx      lr
    .size ip_shift_tail, . - ip_shift_tail
    .type ip_shift_user, %function
    .thumb_func
ip_shift_user:
    push    {r4, lr}
    mov     r4, r0
    bl      ip_shift_tail
    add     r0, r0, r4
    pop     {r4, pc}
    .size ip_shift_user, . - ip_shift_user
    .type use, %function
    .thumb_func
use:
    push    {r4, lr}
    bl      ip_shift_user
    pop     {r4, pc}
    .size use, . - use
"""


@needs_arm
def test_a_bound_call_hands_r12_to_a_callee_that_reads_it(tmp_path):
    """ip_shift_tail reads r12 on the path where its predicated write did not run: its C gets r12 as
    the caller left it (-ffixed-r12). Its callers were refused ("calls ip_shift_tail, which reads r12
    its caller leaves"), as a bound call handed on r4-r11 only (FreeRTOS's hard-float builds: the
    tasks that call __aeabi_d2f); now it binds r12 too, from the C's variable."""
    t = arm_program(tmp_path, "t2", IP_T2_S)
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))
    text = lifter.lift(p.by_name["ip_shift_user"]).text
    assert 'x12 __asm__("r12") = r12' in text and '"+r"(x12)' in text, text
    lines = []
    names = ["ip_shift_tail", "ip_shift_user", "use"]
    assert sorted(lift.check(t, t.image(), names, out=lines.append, jobs=2)) == names, "\n".join(lines)


QUAD_RUN_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .section .vectors, "a", %progbits
    .word   0x20004000
    .word   reset
""" + QUAD_T2_S.split(".text", 1)[1].replace("    .global quad_fifth_arg, quad_fifth_user, quad_held_bias, use\n", "") \
    .split("    .type quad_fifth_arg", 1)[0] + """
    .type quad_print_hex, %function
    .thumb_func
quad_print_hex:                 @ r0 as eight hex digits and a newline on the UART
    push    {r4, r5, lr}
    mov     r4, r0
    movs    r5, #28
    ldr     r1, =0x40004000
1:  lsr     r0, r4, r5
    and     r0, r0, #15
    cmp     r0, #10
    ite     lo
    addlo   r0, r0, #48
    addhs   r0, r0, #87
    str     r0, [r1]
    subs    r5, r5, #4
    bpl     1b
    movs    r0, #10
    str     r0, [r1]
    pop     {r4, r5, pc}
    .ltorg
    .size quad_print_hex, . - quad_print_hex
    .type reset, %function
    .thumb_func
reset:                          @ sums what the four-word functions hand back, prints it, stops
    movs    r0, #0x35
    movw    r1, #0x1234
    bl      quad_sum_reader
    mov     r6, r0
    movs    r0, #7
    movs    r1, #9
    bl      quad_store_reader
    ldr     r5, =0x20000100
    movs    r4, #8
2:  ldmia   r5!, {r0}
    eor     r6, r0, r6, ror #7
    subs    r4, r4, #1
    bne     2b
    mov     r0, r6
    bl      quad_print_hex
3:  b       3b
    .ltorg
    .size reset, . - reset
"""

RUN_TABLES = """
[models.uart]
kind = "uart"
base = 0x40004000

[scenarios.sums]
until = {uart = "\\n"}
limit = 100_000
"""


@needs_arm
def test_the_system_run_agrees_with_the_entries_in_the_whole_image(tmp_path):
    """A program that runs from reset, sums what the four-word functions and their callers hand back
    and prints it: with every function built from C, and with only the four-word ones (their callers
    the original's code), the scenario prints the same on the whole image (dread lift --check's
    whole_image)."""
    toml = M_TOML % {"cpu": "cortex-m3", "more": 'entry = "reset"\nvectors = 0x08000000\n',
                     "compiler": M_GCC % "cortex-m3"} + RUN_TABLES
    t = arm_program(tmp_path, "t2", QUAD_RUN_S, entry="reset", toml=toml)
    names = ["quad_pair_split", "quad_forward_tail", "quad_forward_call", "quad_sum_reader", "quad_store_reader",
             "quad_print_hex", "reset"]
    for chosen in (names, names[:3]):
        lines = []
        accepted = lift.check(t, t.image(), chosen, out=lines.append, jobs=2)
        assert sorted(accepted) == sorted(chosen), "\n".join(lines)
        assert "the whole image agrees" in "\n".join(lines), "\n".join(lines)


ROOMY_A32_S = """
    .syntax unified
    .arch armv4t
    .arm
    .text
    .global roomy_quad_split, roomy_reader
    .type roomy_quad_split, %function
roomy_quad_split:               @ four words back in r0-r3, long enough for the naked entry in place
    add     r2, r0, r1
    eor     r3, r0, r1
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    sub     r1, r0, r1
    and     r0, r0, r2
    bx      lr
    .size roomy_quad_split, . - roomy_quad_split
    .type roomy_reader, %function
roomy_reader:                   @ reads r2 and r3 behind the call
    push    {r4, lr}
    bl      roomy_quad_split
    add     r0, r0, r2
    add     r0, r0, r3
    pop     {r4, lr}
    bx      lr
    .size roomy_reader, . - roomy_reader
"""


@needs_arm
def test_a_body_without_room_drops_its_function_not_the_build(tmp_path):
    """A body behind a naked entry is a helper of its function's object and goes into the room for the
    C, while the entry stays in the function's place. With that room full and nothing else there, the
    lift check drops the function the body belongs to (dread.overlay.helper_owners): before, nothing
    the overlay had planned stood in the room, nothing could be dropped and the build failed ("no
    space for: " and nothing after it)."""
    t = arm_program(tmp_path, "a32", source=ROOMY_A32_S, entry="roomy_reader")
    image = t.image()
    end = (image.base + len(image.payload()) + 7) & ~7
    toml = tmp_path / "dread.toml"
    toml.write_text(toml.read_text().replace("limit = 0x30000", "limit = 0x%x" % end))
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    got = lift.make_lifter(p, regflow.analyse(p)).lift(p.by_name["roomy_quad_split"])
    assert got.more == ["r2", "r3"]
    lines = []
    accepted = lift.check(t, t.image(), ["roomy_quad_split"], out=lines.append, jobs=2)
    text = "\n".join(lines)
    assert accepted == [] and "the build failed" not in text, text
    assert "dropped  roomy_quad_split" in text and "no room left for its C" in text, text
    # with the room the body needs, it is accepted with its entry in place
    toml.write_text(toml.read_text().replace("limit = 0x%x" % end, "limit = 0x30000"))
    t = Target.load(str(tmp_path))
    lines = []
    assert lift.check(t, t.image(), ["roomy_quad_split"], out=lines.append, jobs=2) == ["roomy_quad_split"], \
        "\n".join(lines)


EARLY_RETURN_T2_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global early_sum, early_local, use
    .type early_sum, %function
    .thumb_func
early_sum:                      @ r0 bytes from r1 summed; nothing at all for none
    push    {r4, r5}
    movs    r2, #0
    adds    r4, r0, #0
    itt     eq
    popeq   {r4, r5}
    bxeq    lr
1:  ldrb    r5, [r1], #1
    add     r2, r2, r5
    subs    r4, r4, #1
    bne     1b
    mov     r0, r2
    pop     {r4, r5}
    bx      lr
    .size early_sum, . - early_sum
    .type early_local, %function
    .thumb_func
early_local:                    @ with locals: the frame and the registers go under one condition
    push    {r4, lr}
    sub     sp, #8
    str     r0, [sp, #4]
    cmp     r0, #0
    itt     eq
    addeq   sp, #8
    popeq   {r4, pc}
    ldr     r4, [sp, #4]
    adds    r0, r4, r1
    cmp     r0, #100
    it      hi
    addhi   sp, #8
    it      hi
    pophi   {r4, pc}
    adds    r0, r0, #1
    add     sp, #8
    pop     {r4, pc}
    .size early_local, . - early_local
    .type use, %function
    .thumb_func
use:
    push    {r4, lr}
    bl      early_sum
    movs    r1, #5
    bl      early_local
    pop     {r4, pc}
    .size use, . - use
"""


@needs_arm
def test_a_pop_and_a_return_under_one_condition_are_an_early_return(tmp_path):
    """armcc returns early with `itt eq; popeq {r4, r5}; bxeq lr`, and with locals `itt eq; addeq sp,
    #8; popeq {r4, pc}` or `it hi; addhi sp, #8; it hi; pophi {r4, pc}`: the frame goes and the return runs
    under one condition, and the path that does not take them keeps its frame. They were refused
    ("moves the stack pointer under a condition"), as GCC's `popeq {r4, r5, pc}` is not; now each is
    one conditional return, lifted and accepted on the original's cases."""
    t = arm_program(tmp_path, "t2", EARLY_RETURN_T2_S)
    p = Program(t, t.image())
    text = lift.make_lifter(p, regflow.analyse(p)).lift(p.by_name["early_sum"]).text
    assert "if (z) { return r0; }" in text, text
    lines = []
    assert "if (z) { return r0; }" in lift.make_lifter(p, regflow.analyse(p)).lift(p.by_name["early_local"]).text
    names = ["early_local", "early_sum", "use"]
    assert sorted(lift.check(t, t.image(), names, out=lines.append, jobs=2)) == names, "\n".join(lines)


SWITCH_HS_T2_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global pick, use
    .type pick, %function
    .thumb_func
pick:                           @ armcc's bound: cmp #3; bhs out lets 0, 1 and 2 through
    movs    r1, #0
    cmp     r0, #3
    bhs     2f
    tbb     [pc, r0]
1:  .byte   (10f - 1b) / 2, (11f - 1b) / 2, (10f - 1b) / 2, 0
10: movs    r1, #7
    b       2f
11: movs    r1, #9
2:  mov     r0, r1
    bx      lr
    .size pick, . - pick
    .type use, %function
    .thumb_func
use:
    push    {r4, lr}
    bl      pick
    pop     {r4, pc}
    .size use, . - use
"""


@needs_arm
def test_a_switch_bounded_by_bhs_has_as_many_cases_as_it_compares_with(tmp_path):
    """armcc bounds a switch with cmp rN, #3; bhs out: three values reach the table, where GCC writes
    cmp rN, #2; bhi out. The bound was taken for GCC's, and the lifter refused the switch ("a switch
    whose target for index 3 is not known"); now the branch decides how many pass."""
    t = arm_program(tmp_path, "t2", SWITCH_HS_T2_S)
    p = Program(t, t.image())
    from dread import switches
    fn = p.by_name["pick"]
    jump = next(a for a in fn.insns if p.insn(a).mnemonic == "tbb")
    check, reg, values, limit = switches.mapping(p, fn, jump)
    assert limit == 2 and sorted(values) == [0, 1, 2], (limit, values)
    lines = []
    names = ["pick", "use"]
    assert sorted(lift.check(t, t.image(), names, out=lines.append, jobs=2)) == names, "\n".join(lines)
