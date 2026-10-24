"""dread readable: results as the source has them. A function whose result no caller reads (dread.regflow
over the whole image, callers through a pointer counted as reading) is printed void, its calls
statements (dread.tidy void_results); one the cases reject so keeps its result (the plan's results).
A second word that is only a leftover (regflow's leftover) is handed back through dread.h's RETURN2,
the function printed one-word, a caller that reads it calling through CALL2; a real second result
stays u64. The comparison is the one the lift check makes: the gate decides. Programs of our own, in
assembly, and gizmo."""
import shutil

import pytest

from dread import lift, readable, regflow
from dread.program import Program
from conftest import RISCV_CROSS, RISCV_MARCH, needs_riscv_gcc, riscv_toml

needs_arm = pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                               reason="needs arm-none-eabi-as and -gcc")


def texts(t, names):
    p = Program(t, t.image())
    flow = regflow.analyse(p)
    lifter = lift.make_lifter(p, flow)
    context = readable.Context(t, t.image(), p, flow)
    return {n: readable.transform(n, lifter.lift(p.by_name[n]).text, context=context) for n in names}, context


@needs_arm
def test_a_leftover_second_word_reads_as_one_word(tmp_path):
    """leaf leaves the constant it stored in r1 and mid hands it on; peek stores it, so the lifters
    return two words from both (and the cases compare r1). Readable C printed u64 leaf(u32 r0) with
    `return ((u64)0x55u << 32) | (r0 + 1);`, u64 mid, and its callers took (u32)(... >> 32). Now leaf
    and mid are u32 with `return RETURN2(lo, hi);` and a comment naming peek, the reader regflow found;
    peek, which reads both words, calls mid through CALL2; relay, which hands the first word on, calls it
    plainly. udivmod's remainder is its own result: u64 as before, read by remainder_of with >> 32. The
    functions nobody reads the result of (top, relay, peek, sink) are void. The lift check and the gate
    take every function."""
    from test_regflow_bits import LEFT_NAMES, left_program
    t = left_program(tmp_path)
    lines = []
    assert sorted(lift.check(t, t.image(), out=lines.append, jobs=2)) == sorted(LEFT_NAMES), lines
    got, context = texts(t, LEFT_NAMES)
    assert context.leftover == {"leaf", "mid"}
    leaf = got["leaf"]
    assert "u32 leaf(u32 r0)" in leaf and "return RETURN2(r0 + 1, 0x55u);" in leaf, leaf
    assert "r1 at its returns (RETURN2)" in leaf and "read after the call by mid" in leaf, leaf
    mid = got["mid"]
    assert "u32 mid(u32 r0)" in mid and "extern u32 leaf(u32);" in mid and "CALL2(leaf, r0)" in mid, mid
    assert "return RETURN2((u32)t_1, (u32)(t_1 >> 32));" in mid and "read after the call by peek" in mid, mid
    peek = got["peek"]
    assert "void peek(u32 r0)" in peek and "t_1 = CALL2(mid, r0);" in peek and "(u32)(t_1 >> 32);" in peek, peek
    relay = got["relay"]
    assert "void relay(u32 r0)" in relay and "= mid(r0);" in relay and "CALL2" not in relay, relay
    assert "extern void sink(u32);" in relay, relay
    assert "u64 udivmod(u32 r0, u32 r1)" in got["udivmod"], got["udivmod"]
    rem = got["remainder_of"]
    assert "u32 remainder_of(u32 r0, u32 r1)" in rem and "(u32)(udivmod(r0, r1) >> 32)" in rem, rem
    assert "extern u64 udivmod(u32, u32);" in rem, rem
    for name in ("top", "sink"):
        assert "void %s(" % name in got[name], got[name]
    lines = []
    accepted = readable.check(t, t.image(), out=lines.append, jobs=2)
    assert sorted(accepted) == sorted(LEFT_NAMES), lines


@needs_arm
def test_a_wrong_leftover_is_caught_by_the_cases(tmp_path, monkeypatch):
    """The cases compare r1 at leaf's return (its callers read it): readable C that hands back another
    value through RETURN2 is turned down, and leaf stays lifted."""
    from dread import cparse
    from test_regflow_bits import LEFT_NAMES, left_program
    t = left_program(tmp_path)
    assert sorted(lift.check(t, t.image(), jobs=2)) == sorted(LEFT_NAMES)
    results = readable.results

    def wrong(tree, fn, context):
        results(tree, fn, context)
        if fn.name == "leaf":
            for st in fn.body.body:
                if isinstance(st, cparse.Return) and isinstance(st.e, cparse.Call):
                    st.e.args[1] = cparse.Binary("^", st.e.args[1], cparse.Num("1"))
    monkeypatch.setattr(readable, "results", wrong)
    monkeypatch.setattr(readable, "_STORE", {})     # (not the readable C an earlier test made of this program)
    lines = []
    accepted = readable.check(t, t.image(), out=lines.append, jobs=2)
    assert "leaf" not in accepted and any(line.startswith("lifted   leaf") for line in lines), lines
    rounds = "".join(p.read_text() for p in (tmp_path / "build" / "readable-check" / "main").glob("round-*.txt"))
    assert "r1 after the return: 0x55 vs 0x54" in rounds, rounds


@needs_arm
def test_gizmo_s_void_functions_are_void(target):
    """gizmo's timer_start and delay return nothing (examples/gizmo/fw/hw.c): readable C printed u32
    timer_start(u32 r0) { ...; return r0; } and u32 delay(u32 r0) { r0_1 = timer_start(r0); ...; return
    r0_1; }, the register they leave as if it were a result. Now both are void, the call a statement and
    its extern void; ring_get, whose result main reads, keeps it."""
    got, context = texts(target, ["timer_start", "delay", "ring_get"])
    first = target.abi.arg_regs[0]
    assert "void timer_start(u32 %s)" % first in got["timer_start"], got["timer_start"]
    assert "return" not in got["timer_start"].split("{", 1)[1], got["timer_start"]
    delay = got["delay"]
    assert "void delay(u32 %s)" % first in delay and "return" not in delay.split("{", 1)[1], delay
    if "timer_start" in delay:              # (clang inlines it on rv32)
        assert "    timer_start(%s);" % first in delay and "extern void timer_start(u32);" in delay, delay
    assert "void ring_get" not in got["ring_get"] and "ring_get" not in context.void


KEEP_S = """
    .syntax unified
    .arch armv4t
    .arm
    .text
    .global keep_main, note, tally
    .type keep_main, %function
keep_main:
    push    {r4, lr}
    mov     r4, r0
    bl      note                @ its result is read by nobody: r0 is set anew
    mov     r0, r4
    bl      tally               @ nor this one's
    add     r0, r4, #1
    ldr     r1, =0x40000300
    str     r0, [r1]
    pop     {r4, lr}
    bx      lr
    .ltorg
    .size keep_main, . - keep_main
    .type note, %function
note:                           @ stores its argument, then 7 through r0, and leaves 7 there
    ldr     r3, =0x40000304
    str     r0, [r3]
    mov     r0, #7
    str     r0, [r3, #4]
    bx      lr
    .ltorg
    .size note, . - note
    .type tally, %function
tally:                          @ a count nobody reads: r0 computed for the return alone
    ldr     r3, =0x40000308
    ldr     r3, [r3]
    add     r0, r3, r0
    bx      lr
    .ltorg
    .size tally, . - tally
"""


@needs_arm
def test_a_void_function_the_cases_reject_keeps_its_result(tmp_path):
    """Nobody reads note's or tally's result. tally computes its r0 for the return alone (regflow's
    computed): a count its source returns, kept (u32). note uses r0 for a value it stores and leaves it
    there, so readable C prints it void; but the cases compare r0 at its return, and its void C leaves
    its argument there: turned down, it is tried once more with its result (the plan's results) and
    taken so, `u32 note(u32 r0)` returning 7, as before."""
    from dread import shapes
    from test_lift_results import arm_program
    t = arm_program(tmp_path, "a32", KEEP_S, entry="keep_main")
    names = ["keep_main", "note", "tally"]
    assert sorted(lift.check(t, t.image(), jobs=2)) == names
    got, context = texts(t, ["note", "tally"])
    assert "note" in context.void and "void note(u32 r0)" in got["note"], got["note"]
    assert "tally" not in context.void and "u32 tally(u32 r0)" in got["tally"], got["tally"]
    lines = []
    assert sorted(readable.check(t, t.image(), out=lines.append, jobs=2)) == names, lines
    assert any(line.startswith("result   note") for line in lines), lines
    plan = shapes.load_plan(t.image())
    assert "note" in plan.results, plan.results
    got, context = texts(t, ["note", "keep_main"])
    assert "note" not in context.void and "u32 note(u32 r0)" in got["note"] and "return 7;" in got["note"], got["note"]


LEFT_RV_S = """
    .option norelax
    .text
    .global use, leaf, mid, top, peek, divmod, remainder_of
    .type use, @function
use:
    addi    sp, sp, -16
    sw      ra, 12(sp)
    sw      s0, 8(sp)
    mv      s0, a0
    call    top
    mv      a0, s0
    call    peek
    mv      a1, s0
    li      a0, 7
    call    remainder_of
    add     a0, a0, s0
    lui     a1, %hi(0x80000210)
    sw      a0, %lo(0x80000210)(a1)
    lw      s0, 8(sp)
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size use, . - use
    .type leaf, @function
leaf:                           # a0 + 1; stores a constant through a1, which it leaves there
    lui     a2, %hi(0x80000200)
    li      a1, 0x55
    sw      a1, %lo(0x80000200)(a2)
    addi    a0, a0, 1
    ret
    .size leaf, . - leaf
    .type mid, @function
mid:                            # leaf's result; what leaf left in a1 passes through
    addi    sp, sp, -16
    sw      ra, 12(sp)
    call    leaf
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size mid, . - mid
    .type top, @function
top:                            # the low byte of mid's result stored at a0, through a1, whose upper bits it keeps
    addi    sp, sp, -16
    sw      ra, 12(sp)
    sw      s0, 8(sp)
    mv      s0, a0
    call    mid
    andi    a0, a0, 0xff
    andi    a1, a1, -256
    or      a1, a1, a0
    sb      a1, 0(s0)
    lw      s0, 8(sp)
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size top, . - top
    .type peek, @function
peek:                           # stores mid's result and all of what mid left in a1
    addi    sp, sp, -16
    sw      ra, 12(sp)
    call    mid
    lui     a2, %hi(0x80000208)
    addi    a2, a2, %lo(0x80000208)
    sw      a0, 0(a2)
    sw      a1, 4(a2)
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size peek, . - peek
    .type divmod, @function
divmod:                         # a1 / a0 in a0, a1 % a0 in a1
    divu    a2, a1, a0
    remu    a1, a1, a0
    mv      a0, a2
    ret
    .size divmod, . - divmod
    .type remainder_of, @function
remainder_of:                   # the remainder: a caller that reads divmod's second word
    addi    sp, sp, -16
    sw      ra, 12(sp)
    call    divmod
    mv      a0, a1
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size remainder_of, . - remainder_of
"""


def riscv_program(tmp_path, source, entry):
    """A target in tmp_path of RISC-V assembly of our own (as tests/test_lift_results.py builds them)."""
    import subprocess
    from dread import elfimport
    from dread.target import Target
    from test_lift_results import RV_GCC, RV_TOML
    (tmp_path / "t.s").write_text(source)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run([RISCV_CROSS + "gcc", "-march=" + RISCV_MARCH, "-mabi=ilp32", "-c", "-o", obj,
                    str(tmp_path / "t.s")], check=True)
    subprocess.run([RISCV_CROSS + "ld", "-m", "elf32lriscv", "-Ttext=0x20000000", "-e", entry, "-o", elf, obj],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml(RV_TOML + RV_GCC))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    return Target.load(str(tmp_path))


@needs_riscv_gcc
def test_a_leftover_second_word_on_risc_v(tmp_path):
    """The same on RISC-V, where the second result register is a1: leaf and mid one-word with RETURN2
    (dread.h leaves the word in a1), peek through CALL2; top keeps a1's upper bits through an andi and
    stores a byte, so regflow finds it reading none of what mid left (its C still names the high word in
    the byte it stores: the clean-up does not narrow a stored value); divmod's remainder stays u64. Both
    gates take every function."""
    t = riscv_program(tmp_path, LEFT_RV_S, "use")
    names = ["use", "leaf", "mid", "top", "peek", "divmod", "remainder_of"]
    lines = []
    assert sorted(lift.check(t, t.image(), out=lines.append, jobs=2)) == sorted(names), lines
    got, context = texts(t, names)
    assert context.leftover == {"leaf", "mid"}
    assert "u32 leaf(u32 a0)" in got["leaf"] and "return RETURN2(a0 + 1, 0x55u);" in got["leaf"], got["leaf"]
    assert "CALL2(leaf, a0)" in got["mid"] and "CALL2(mid, a0)" in got["peek"], (got["mid"], got["peek"])
    assert "void top(u32 a0)" in got["top"], got["top"]
    flow = regflow.analyse(Program(t, t.image()))
    assert [caller for caller, _ in flow.sites_of("mid", "a1")] == ["peek"], flow.sites.get("mid")
    assert "u64 divmod(u32 a0, u32 a1)" in got["divmod"], got["divmod"]
    lines = []
    assert sorted(readable.check(t, t.image(), out=lines.append, jobs=2)) == sorted(names), lines


@pytest.mark.slow
@needs_arm
def test_a_function_given_its_result_back_keeps_its_types(tmp_path):
    """The check gives gizmo-m3's memcpy, memset and strncpy their results back (void, their C leaves
    another r0 than the original, which hands the destination back and the cases compare). The
    readable_types table keeps each function's types for the digest of its C, and the first table was
    made for the void forms: the check makes it anew then, and the functions printed with their result
    keep the pointer types of their parameters (until then strncpy printed `u32 strncpy(u32 r0, u32 r1,
    u32 r2)` after the check, for `u32 strncpy(volatile u8 *r0, const volatile u8 *r1, u32 r2)`)."""
    import re
    from dread import shapes
    from test_readable import gizmo_copy
    target = gizmo_copy(tmp_path)
    image = target.image()
    lines = []
    readable.check(target, image, out=lines.append)
    plan = shapes.load_plan(image)
    typed = {n: [t for t in plan.prototypes.get(n, [])[1:] if t and "*" in t] for n in sorted(plan.results)}
    typed = {n: types for n, types in typed.items() if types}
    assert typed, (sorted(plan.results), lines)
    p = Program(target, image)
    flow = regflow.load(target, image, p)
    lifter = lift.make_lifter(p, flow)
    context = readable.Context(target, image, p, flow)
    for name, types in typed.items():
        text = readable.transform(name, lifter.lift(p.by_name[name]).text, context=context)
        head = re.search(r"^u32 %s\((.*)\)$" % name, text, re.M)
        assert head and all(t in head.group(1) for t in types), (name, types, text)


def test_a_void_function_that_differs_in_its_return_value_alone_takes_its_result_first():
    """dread readable --check gives a void function whose cases differ in the value it returns alone its
    result first (what its void form changes); one whose cases differ otherwise (a memory word, a case
    made to fail without a difference) has its frame's region tried as one object first. An UNCOVERED
    line is no difference."""
    lines = ["FAIL a [generated 0]\n     return value 0x4 vs 0x5\nFAIL a [generated 1]\n     return value 0x4 vs 0x6",
             "UNCOVERED a: 7 of 71 instructions of the original never ran: 0xb14e",
             "FAIL b [generated 0]\n     return value 0x1 vs 0x2\n     memory 0x20000000: 1 vs 2",
             "FAIL c [made to fail]", "UNCOVERED d: 3 of 4 instructions of the original never ran: 0x10",
             "REFUSED a [generated 2]: the input makes the original touch its own saved registers"]
    assert readable.returned_only(lines) == {"a"}


PTR_RV_S = """
    .option norelax
    .text
    .global start, run, twice, bump
    .type start, @function
start:                          # hands run a callback whose address it makes by a lui and an addi apart
    addi    sp, sp, -16
    sw      ra, 12(sp)
    lui     a0, %hi(twice)
    li      a1, 21              # (between them, as GCC schedules the pair)
    addi    a0, a0, %lo(twice)
    call    run
    lui     a1, %hi(0x80000300)
    sw      a0, %lo(0x80000300)(a1)
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size start, . - start
    .type run, @function
run:                            # calls a0 with a1 through the pointer and returns its result
    addi    sp, sp, -16
    sw      ra, 12(sp)
    mv      t0, a0
    mv      a0, a1
    jalr    t0
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size run, . - run
    .type twice, @function
twice:                          # bump's result twice over, a value its own code does not compute last
    addi    sp, sp, -16
    sw      ra, 12(sp)
    call    bump
    call    bump
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size twice, . - twice
    .type bump, @function
bump:
    addi    a0, a0, 1
    ret
    .size bump, . - bump
"""


@needs_riscv_gcc
def test_a_pointer_made_by_a_lui_and_an_addi_apart_reads_the_result(tmp_path):
    """start makes twice's address by a lui and an addi with an instruction between them, as GCC's
    RISC-V code does, and run calls it through the pointer and returns its result, which start stores:
    twice's result is read, by a caller through a pointer that regflow finds (made_pointers; the layer's
    pc_address joins neighbours only, and until then twice printed void, as if nobody read what it
    returns). Its own code computes nothing for the return (bump's value), so only the pointer keeps
    it u32. Both gates take every function."""
    t = riscv_program(tmp_path, PTR_RV_S, "start")
    names = ["start", "run", "twice", "bump"]
    p = Program(t, t.image())
    assert "twice" in regflow.pointer_entries(p)
    flow = regflow.analyse(p)
    assert [regflow.POINTER_CALLERS, 0, False] in flow.sites["twice"]["a0"], flow.sites.get("twice")
    assert "twice" not in flow.computed, flow.computed
    lines = []
    assert sorted(lift.check(t, t.image(), out=lines.append, jobs=2)) == sorted(names), lines
    got, context = texts(t, names)
    assert "u32 twice(u32 a0)" in got["twice"], got["twice"]
    assert "void start(" in got["start"], got["start"]
    lines = []
    assert sorted(readable.check(t, t.image(), out=lines.append, jobs=2)) == sorted(names), lines


VPRINT_S = """
    .syntax unified
    .arch armv4t
    .arm
    .text
    .global vuse, vprint, emit
    .type vuse, %function
vuse:
    push    {r4, lr}
    mov     r1, r0
    ldr     r0, =0x40000400
    bl      vprint
    pop     {r4, lr}
    bx      lr
    .ltorg
    .size vuse, . - vuse
    .type vprint, %function
vprint:                         @ variadic: its arguments where a va_list finds them, handed to emit
    push    {r0, r1, r2, r3}
    push    {r4, lr}
    ldr     r0, [sp, #8]        @ the first argument, from its slot
    add     r1, sp, #12         @ the va_list: the word behind it
    bl      emit
    pop     {r4, lr}
    add     sp, sp, #16
    bx      lr
    .size vprint, . - vprint
    .type emit, %function
emit:                           @ stores its first argument and the word its second points to
    ldr     r1, [r1]
    ldr     r2, =0x40000304
    str     r0, [r2]
    str     r1, [r2, #4]
    bx      lr
    .ltorg
    .size emit, . - emit
"""


@needs_arm
def test_a_void_callee_of_a_body_behind_a_naked_entry_keeps_its_type(tmp_path):
    """vprint takes the address of its saved arguments (a va_list), so the lifter wraps it: its body
    behind a naked entry, which readable C leaves as the lifter wrote it (`r0 = emit(...)`). emit's result
    nobody reads, so it is void; but its extern in vprint's file stays u32, as the body assigns its value
    (FreeRTOS's printf and tiny_print on GCC -Os: `invalid use of void expression`, and both stayed
    lifted). Both gates take every function."""
    from test_lift_results import arm_program
    t = arm_program(tmp_path, "a32", VPRINT_S, entry="vuse")
    names = ["emit", "vprint", "vuse"]
    lines = []
    assert sorted(lift.check(t, t.image(), out=lines.append, jobs=2)) == names, lines
    got, context = texts(t, ["vprint", "emit"])
    assert "emit" in context.void and "void emit(" in got["emit"], got["emit"]
    assert "_dread_body" in got["vprint"] and "extern u32 emit(" in got["vprint"], got["vprint"]
    lines = []
    assert sorted(readable.check(t, t.image(), out=lines.append, jobs=2)) == names, lines
