"""dread.tidy's flags as comparisons (flags, sums, pairs; conditions in calls and stores): a compare's
flags read by several statements, an add's carry tested away from its add, a chain of subtractions, a
conditional compare's two flags, a borrow added back, a sum through 64 bits whose carry nobody reads,
two-word additions and compares; each rule on small functions of the lifters' dialect, the public
examples on gizmo, a dispatch by successive subtractions in ARM-state assembly of our own through the
gate, and random functions of flag code cleaned up and compiled beside the originals."""
import ctypes
import random
import re
import shutil
import subprocess

import pytest

from dread import cexpr, cparse, tidy
from conftest import gizmo_dir

needs_z3 = pytest.mark.skipif(cexpr.z3 is None, reason="needs z3-solver (the extra 'prove')")
needs_arm = pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                               reason="needs arm-none-eabi-as and -gcc")

HEAD = """/* f at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 probe(u32, u32, u32, u32);
u32 f(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r5 = 0, r12 = 0;
    u32 n = 0, z = 0, c = 0, v = 0;
    (void)n; (void)z; (void)c; (void)v; (void)r4; (void)r5; (void)r12;
"""


def cmp(a, b):
    return ("{ u32 x = %s, y = %s; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; "
            "c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }" % (a, b))


def arith(d, a, b, kind, carry="0"):
    """The lifters' flag-setting adds/subs (adcs/sbcs with carry "c")."""
    y = "y" if kind == "add" else "(u32)~y"
    carry = carry if kind == "add" or carry != "0" else "1"
    return ("{ u32 x = %s, y = %s; u64 t = (u64)x + %s + %s; %s = (u32)t; n = %s >> 31; z = %s == 0; "
            "c = (u32)(t >> 32); v = ((x ^ %s) & (%s ^ %s)) >> 31; }" % (
                a, b, y, carry, d, d, d, d, "y" if kind == "add" else "~y", d))


def function(lines):
    return HEAD + "".join("    %s  /* %x: i */\n" % (line, 2 * k) for k, line in enumerate(lines)) + "}\n"


def tidy_text(text, name="f"):
    fn = cparse.parse(text).function(name)
    tidy.tidy(fn)
    return cparse.unparse(fn, notes=False, style="readable")


def flags_left(out):
    """The flag variables and 64-bit sums left in cleaned-up C."""
    return re.findall(r"\b[nzcv](?:_\d+)?\b", out.split("{", 1)[1]) + re.findall(r"\(u64\)", out)


@needs_z3
def test_a_compare_read_by_an_if_and_a_return_is_a_comparison_in_both():
    """gizmo's cmd_mode: cmp r0, #7; ldrls; strls; movls r0, #0; mvnhi r0, #0. The flags (ls: !c || z)
    are read by the conditional store and by the select of the result: each a comparison now, the
    carry arithmetic gone."""
    out = tidy_text(function([cmp("r0", "0x7u"),
                              "if ((!c || z)) { r3 = 0x40000064u; }",
                              "if ((!c || z)) { *(volatile u32 *)((r3 + 0)) = (u32)r0; }",
                              "if ((!c || z)) { r0 = 0x0u; }",
                              "if ((c && !z)) { r0 = ~0x0u; }",
                              "return r0;"]))
    assert out.count("r0 <= 7") == 2 and not flags_left(out), out


@needs_z3
def test_an_adds_carry_tested_after_a_store_is_the_sum_below_an_operand():
    """adds r5, r5, r0; str r5, [r3, #4]; blo: the wake time's overflow test (FreeRTOS's
    prvAddCurrentTaskToDelayedList) as the source writes it, the sum less than what was added to."""
    out = tidy_text(function([arith("r5", "r1", "r0", "add"),
                              "*(volatile u32 *)((r3 + 4)) = (u32)r5;",
                              "if (!c) goto L_a;",
                              "return probe(r5, 0, 0, 0);",
                              "L_a: ;",
                              "return r5;"]))
    assert "r5 = r1 + r0;" in out and re.search(r"if \(r5 >= r1\) goto|if \(r1 <= r5\) goto", out), out
    assert not flags_left(out), out



@needs_z3
def test_a_chain_of_subtractions_compares_its_first_variable():
    """subs r0, #1; blo; beq; subs r0, #2; beq; cmp r0, #5; beq (a compiler's dispatch by successive
    subtractions): each branch a comparison of r0 as it came, with what the constants come to (x - 1 - 2
    == 5 is x == 8); the borrow of x - 1 as x == 0."""
    out = tidy_text(function([arith("r0", "r0", "0x1u", "sub"), "if (!c) goto L_a;", "if (z) goto L_b;",
                              arith("r0", "r0", "0x2u", "sub"), "if (z) goto L_c;", cmp("r0", "0x5u"),
                              "if (z) goto L_d;", "return 0x0u;", "L_a: ;", "return 0x1u;", "L_b: ;", "return 0x2u;",
                              "L_c: ;", "return 0x3u;", "L_d: ;", "return 0x4u;"]))
    for cond in ("r0 == 0", "r0 == 1", "r0 == 3", "r0 == 8"):
        assert "if (%s) goto" % cond in out, (cond, out)
    assert not flags_left(out) and "r0_" not in out, out


@needs_z3
def test_a_borrow_added_back_is_an_equality():
    """rsbs r0, r1, #0; adcs r0, r1 (GCC's x == 0): the borrow of 0 - r1 added to the difference and r1."""
    out = tidy_text(function([arith("r0", "0x0u", "r1", "sub"), "{ u32 x = r0, y = r1; r0 = (u32)((u64)x + y + c); }",
                              "return r0;"]))
    assert "return r1 == 0;" in out and not flags_left(out), out


@needs_z3
def test_two_words_added_with_a_carry_are_one_64_bit_sum_and_compared_one_64_bit_comparison():
    """adds r2, r0, #1; adc r3, r1, #0 (a 64-bit counter's increment) as one 64-bit sum whose words go
    on; cmp r0, #5; sbcs r1, #0; blo (its 64-bit compare) as one comparison."""
    out = tidy_text(function([arith("r2", "r0", "0x1u", "add"), "{ u32 x = r1, y = 0x0u; r3 = (u32)((u64)x + y + c); }",
                              "r0 = probe(r2, r3, 0, 0);", "return r0;"]))
    assert "t_1 = (((u64)r1 << 32) | r0) + 1;" in out and "probe((u32)t_1, (u32)(t_1 >> 32), 0, 0)" in out, out
    out = tidy_text(function([cmp("r0", "0x5u"), "{ u32 x = r1, y = 0x0u; u64 t = (u64)x + (u32)~y + c; u32 r = (u32)t; "
                              "n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }",
                              "if (!c) goto L_a;", "return 0x1u;", "L_a: ;", "return 0x2u;"]))
    assert "if ((((u64)r1 << 32) | r0) < 5) goto L_a;" in out and "c" not in out.split("{", 1)[1].replace("(", " "), out


@needs_z3
def test_a_conditional_compares_two_flags_are_one_condition_at_each_reader():
    """gizmo's parse_kv: cmp r0, #0x5f; it ne; cmpne r1, #0x19, its ls read by a select passed on and
    its hi by a branch: each reader a condition of the two comparisons, the flags and the conditional
    compare gone (the flags a conditional block chooses: FlagFlow.chosen; _split_search)."""
    out = tidy_text(function([cmp("r0", "0x5fu"), "if (!z) { %s }" % cmp("r1", "0x19u"),
                              "if ((!c || z)) { r2 = 0x1u; }", "if ((c && !z)) { r2 = 0x0u; }",
                              "r3 = probe(r2, 0, 0, 0);", "if ((c && !z)) goto L_a;", "return r3;", "L_a: ;",
                              "return 0x0u;"]))
    assert "probe(r0 == 0x5fu || r1 <= 0x19u ? 0x1u : 0x0u, 0, 0, 0)" in out, out
    assert "if (r0 != 0x5fu && r1 > 0x19u) goto L_a;" in out and not flags_left(out), out


@needs_z3
def test_a_condition_whose_operand_changes_is_computed_where_its_flags_are_set():
    """gizmo-m3's fmt_dec: cmp r1, #9; mov r1, r5; ...; bhi: what the compare read changes before the
    branch (a loop's variable, one web), so the condition is computed into a variable of its own where
    the flags are set (SINK), as the comparison it is there."""
    out = tidy_text(function(["L_top: ;", cmp("r1", "0x9u"), "r1 = r1 >> 1;", "r3 = r3 + 0x1u;",
                              "if ((c && !z)) goto L_top;", "return r3;"]))
    assert "cc_1 = r1 > 9;" in out and "if (cc_1) goto L_top;" in out and not flags_left(out), out
    assert out.index("cc_1 = r1 > 9;") < out.index("r1 = r1 >> 1;"), out


@needs_z3
def test_a_sum_through_64_bits_whose_carry_nobody_reads_is_a_32_bit_sum_anywhere():
    """add r1, r0, #4 written through 64 bits (adds without its flags) and passed to a call: r0 + 4 in
    the argument; a constant one folded."""
    out = tidy_text(function(["{ u32 x = r0, y = 0x4u; r1 = (u32)((u64)x + y + 0); }", "r0 = probe(r1, r0, 0, 0);",
                              "return r0;"]))
    assert "return probe(r0 + 4, r0, 0, 0);" in out, out
    e = tidy.wide_sum(cparse.parse("void g(void)\n{\n    q = (u32)((u64)0x0u + (u32)~0x8u + 1) + a;\n}\n")
                      .function("g").body.body[0].e.value)
    assert e is None                        # (only a cast to u32 of the sum is one)
    e = cparse.parse("void g(void)\n{\n    q = (u32)((u32)((u64)0x0u + (u32)~0x8u + 1) + a);\n}\n").function(
        "g").body.body[0].e.value
    assert cparse.expr_text(tidy.wide_sum(e)) == "a - 8"


@needs_z3
def test_a_borrow_over_a_load_is_read_from_the_difference_not_a_new_load():
    """subs r2, r1, [r0, #8]; movlo r2, r1 (clang's min of a count): the borrow as r1 < r2, the
    difference larger than what it was taken from; the load stays where it was, made once."""
    out = tidy_text(function(["{ u32 x = r1, y = *(volatile u32 *)((r0 + 8)); u64 t = (u64)x + (u32)~y + 1; r2 = (u32)t; "
                              "n = r2 >> 31; z = r2 == 0; c = (u32)(t >> 32); v = ((x ^ r2) & (~y ^ r2)) >> 31; }",
                              "if (!c) { r2 = r1; }", "return r2;"]))
    assert "r2 = r1 - *(volatile u32 *)(r0 + 8);" in out and "if (r1 < r2) r2 = r1;" in out, out
    assert out.count("*(volatile u32 *)") == 1 and not flags_left(out), out


@needs_z3
def test_a_flag_that_is_a_comparison_goes_into_all_its_readers_or_none():
    """z = r0 == r1 read by a conditional store and by a branch: both readers comparisons, z gone; when
    a conditional block between writes r0 (if (z) { r0 = 5; }), the branch cannot read r0 == r1, and
    the block keeps z too (only the comparison written twice would come of it)."""
    out = tidy_text(function([cmp("r0", "r1"), "if (z) { r2 = 0x1u; }", "r0 = probe(r0, r1, r2, 0);", "if (!z) goto L_a;",
                              "return r0;", "L_a: ;", "return 0x5u;"]))
    assert "if (r0 == r1) r2 = 1;" in out and "if (r0 != r1) goto L_a;" in out and not flags_left(out), out
    out = tidy_text(function([cmp("r0", "r1"), "if (z) { r0 = 0x5u; }", "if (!z) goto L_a;", "return probe(r0, r1, 0, 0);",
                              "L_a: ;", "return 0x5u;"]))
    assert "z = r0 == r1;" in out and "if (z)" in out and "if (!z)" in out, out


def test_a_declarations_value_read_at_a_loops_head_keeps_its_web():
    """webs: r0 = a0 and r1 = a1 (parameters read elsewhere too, so the registers are variables of their
    own) are read at a loop's head, where the values the loop's end writes arrive too; other webs of r0
    and r1 start earlier in the loop. The declarations' webs keep the registers' names, so their values
    reach the head (until 2026-10-23 the earlier webs kept them, the declarations' values were lost to
    reads renamed away from them, and the loop read uninitialised variables: found by
    test_random_flag_code_cleaned_up_computes_the_same; the gate turned such functions down)."""
    text = """u32 g(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r7 = 3, r12 = a0 ^ a1;
L_top: ;
    r1 = r1 + r0;  /* 0: add */
    r0 = r12 - r1;  /* 2: sub */
    r1 = r1 ^ r0;  /* 4: eor */
    r12 = r12 + r0;  /* 6: add */
    r0 = r12 | r1;  /* 8: orr */
    r7 = r7 - 1;  /* a: subs */
    if (r7 != 0) goto L_top;  /* c: bne */
    return r0 ^ r1 ^ r12;
}
"""
    fn = cparse.parse(text).function("g")
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False)
    assert "u32 r0 = a0, r1 = a1, r7 = 3, r12 = a0 ^ a1;" in out and "r1_1 = r1 + r0;" in out, out


@needs_z3
def test_a_call_alone_in_a_condition_is_kept_once_by_a_simpler_one():
    """loads_once counts a call as the condition's only effect: f() - 0x1e == 0 is f() == 0x1e (the
    call made once either way); with a load beside it, or two calls, nothing (their order)."""
    e = expr("f(a) - 0x1eu == 0")
    assert tidy.loads_once(e) == ["f(a)"] and tidy.loads_once(expr("f(a) + g(b) == 0")) is None
    assert tidy.loads_once(expr("f(a) - *(volatile u32 *)0x20000000u == 0")) is None
    out = tidy_text(function(["r0 = probe(r0, 0, 0, 0);", "{ u32 x = r0, y = 0x1eu; u64 t = (u64)x + (u32)~y + 1; "
                              "u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }",
                              "if (z) goto L_a;", "return 0x1u;", "L_a: ;", "return 0x2u;"]))
    assert "if (probe(r0, 0, 0, 0) == 0x1eu) goto L_a;" in out, out


def expr(text):
    return cparse.parse("void g(void)\n{\n    q = %s;\n}\n" % text).function("g").body.body[0].e.value


@needs_z3
def test_a_checker_gives_the_same_verdicts_in_any_order_of_its_values():
    """cexpr.Checker evaluates the expression lazily and tries first the value that refuted the last
    proposal: the verdicts are those of each value tried in order (tested), for proposals near and far."""
    ctx = cexpr.Ctx(types={"a": (32, False), "b": (32, False)})
    e = expr("!(u32)(((u64)a + (u32)~b + 1) >> 32) || (u32)((u64)a + (u32)~b + 1) == 0")      # (a <= b)
    check = cexpr.Checker(e, ctx, as_bool=True)
    for text, verdict in (("a < b", False), ("a == b", False), ("a <= b", True), ("b >= a", True), ("a != b", False),
                          ("(s32)a <= (s32)b", False), ("a <= b", True)):
        assert check.agrees(expr(text)) is verdict, text
        assert cexpr.tested(e, expr(text), ctx, as_bool=True) is verdict, text


def gizmo_readable(variant, *names):
    """{name: readable C} of gizmo functions, as dread readable prints them (no tables of names)."""
    from dread import lift, readable, regflow
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir(variant))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = readable.Context(target, image, program, flow)
    return {n: readable.transform(n, lifter.lift(program.by_name[n]).text, context=context) for n in names}


@needs_z3
@pytest.mark.parametrize("variant", ["a32", "m3", "m4"])
def test_gizmo_cmd_mode_reads_its_bound(variant):
    """gizmo's cmd_mode (if (mode > 7) return -1; g_config.mode = mode; return 0;): the compare's ls,
    read by the conditional store and by the result's select, is mode <= 7 in both; it printed as t_1 =
    (u64)r0 + (u32)~0x7u + 1; ... return !c || z ? 0x0u : ~0x0u; until 2026-10-23."""
    out = gizmo_readable(variant, "cmd_mode")["cmd_mode"]
    body = out.split("cmd_mode(u32 r0)")[1]
    assert body.count("r0 <= 7") == 2 and not flags_left(out), out


@needs_z3
def test_gizmo_parse_kv_reads_its_key_test_as_two_comparisons():
    """gizmo a32's parse_kv: the key's characters (letters and '_') tested by cmp r5, #0x5f; it ne;
    cmpne lr, #0x19 (lr the letter's offset from 'A', its case dropped), the ls read by a select and the
    hi by the loop's exit: each one condition of the two comparisons."""
    out = gizmo_readable("a32", "parse_kv")["parse_kv"]
    # (the constants as dread.polish prints them by use: the character the key compares, the bound decimal)
    assert "r5 == '_' || lr_1 <= 25 ? 1 : 0" in out and "if (r5 != '_' && lr_1 > 25) break;" in out, out
    assert "c = " not in out and "(u64)" not in out, out


@needs_z3
@pytest.mark.parametrize("variant", ["m3", "m4"])
def test_gizmo_fmt_dec_tests_its_digits_where_it_compares(variant):
    """gizmo-m3's fmt_dec: cmp r1, #9 in the digits' loop, the quotient moved into r1 before the bhi
    that closes it: the loop's condition computed where the compare is, cc_1 = r1 > 9."""
    out = gizmo_readable(variant, "fmt_dec")["fmt_dec"]
    assert "cc_1 = r1 > 9;" in out and "while (cc_1);" in out and not flags_left(out), out


DISPATCH_S = """
    .syntax unified
    .arch armv4t
    .arm
    .text
    .global step_action, use
    .type step_action, %function
step_action:                    @ a step's action code, by successive subtractions (as a switch compiles)
    subs    r0, r0, #1
    bcc     .Lnone              @ 0
    beq     .Lfirst             @ 1
    subs    r0, r0, #2
    beq     .Lthird             @ 3
    cmp     r0, #5
    beq     .Leighth            @ 8: x - 1 - 2 == 5
    mvn     r0, #0
    bx      lr
.Lnone:
    mov     r0, #10
    bx      lr
.Lfirst:
    mov     r0, #20
    bx      lr
.Lthird:
    mov     r0, #30
    bx      lr
.Leighth:
    mov     r0, #40
    bx      lr
    .size step_action, . - step_action
    .type use, %function
use:
    push    {r4, lr}
    mov     r4, r0
    bl      step_action
    add     r0, r0, r4
    pop     {r4, lr}
    bx      lr
    .size use, . - use
"""


@needs_z3
@needs_arm
def test_a_dispatch_by_successive_subtractions_through_the_gate(tmp_path):
    """A switch compiled as successive subtractions (subs r0, #1; blo; beq; subs r0, #2; beq; cmp r0,
    #5; beq), in ARM-state assembly of our own: its readable C compares the step with 0, 1, 3 and 8,
    and the gate accepts it (its cases run every instruction and agree)."""
    from dread import lift, readable, regflow
    from dread.program import Program
    from test_lift_results import arm_program
    t = arm_program(tmp_path, "a32", DISPATCH_S)
    p = Program(t, t.image())
    flow = regflow.analyse(p)
    text = lift.make_lifter(p, flow).lift(p.by_name["step_action"]).text
    out = readable.transform("step_action", text, context=readable.Context(t, t.image(), p, flow))
    for cond in ("r0 == 0", "r0 == 1", "r0 == 3", "r0 != 8"):
        assert "if (%s) return" % cond in out, (cond, out)
    assert not flags_left(out), out
    lines = []
    assert lift.check(t, t.image(), ["step_action", "use"], out=lines.append, jobs=2) == ["step_action", "use"], lines
    assert readable.check(t, t.image(), out=lines.append, jobs=2) == ["step_action", "use"], lines


CONDS = ["z", "!z", "c", "!c", "n", "!n", "v", "!v", "(c && !z)", "(!c || z)", "(n == v)", "(n != v)",
         "(!z && n == v)", "(z || n != v)"]      # (eq ne hs lo mi pl vs vc hi ls ge lt gt le)


def random_flag_function(rng, name, steps=12):
    """A function in the lifters' dialect made of flag code: compares, flag-setting additions and
    subtractions (with a carry in: adcs, sbcs; rsbs), additions with a carry and no flags (adc), a
    conditional compare, conditional blocks and branches forward on all fourteen conditions, plain
    statements between that may change what a compare read, calls to probe that log their
    arguments, and a loop around it all that runs three times."""
    regs = ["r0", "r1", "r2", "r3", "r12"]
    lines = ["u32 %s(u32 a0, u32 a1, u32 a2, u32 a3)" % name, "{",
             "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r7 = 3, r12 = a0 ^ a2;",
             "    u32 n = 0, z = 0, c = 0, v = 0;",
             "    (void)n; (void)z; (void)c; (void)v; (void)r12;",
             "L_top: ;"]
    labels = []

    def operand():
        r = rng.random()
        if r < 0.6:
            return rng.choice(regs)
        return "0x%xu" % rng.choice([0, 1, 2, 5, 7, 9, 0x5f, 0x7fffffff, 0x80000000, 0xfffffffe, 0xffffffff])

    def flagged():
        r = rng.random()
        a, b, d = rng.choice(regs), operand(), rng.choice(regs)
        if r < 0.35:
            return cmp(a, b)
        if r < 0.55:
            return arith(d, a, b, "sub")
        if r < 0.7:
            return arith(d, a, b, "add")
        if r < 0.8:
            return arith(d, a, b, rng.choice(["add", "sub"]), "c")
        if r < 0.9:
            return arith(d, "0x0u", a, "sub")                               # (rsbs d, a, #0)
        y = "y" if rng.random() < 0.5 else "(u32)~y"
        return "{ u32 x = %s, y = %s; %s = (u32)((u64)x + %s + c); }" % (a, b, d, y)   # (adc, sbc)

    def plain():
        a, b = rng.choice(regs), rng.choice(regs)
        if rng.random() < 0.15:
            return "%s = probe(r0, r1, r2, r3);" % a
        return "%s = %s %s %s;" % (a, b, rng.choice(["+", "-", "^", "&", "|"]), operand())
    lines.append("    " + flagged() + "  /* 0: flags */")
    for k in range(steps):
        r = rng.random()
        addr = 2 * k + 2
        if r < 0.3:
            cond = rng.choice(CONDS)
            body = plain() if rng.random() < 0.8 else "%s = %s;" % (rng.choice(regs), rng.choice(["c", "z", "n", "v"]))
            if rng.random() < 0.15:
                body = cmp(rng.choice(regs), operand())             # (a conditional compare)
            lines.append("    if (%s) { %s }  /* %x: it */" % (cond, body, addr))
        elif r < 0.45:
            label = "L_%x" % (100 + k)
            labels.append(label)
            lines.append("    if (%s) goto %s;  /* %x: b */" % (rng.choice(CONDS), label, addr))
        elif r < 0.65:
            lines.append("    %s  /* %x: op */" % (flagged(), addr))
        elif r < 0.9:
            lines.append("    %s  /* %x: op */" % (plain(), addr))
        else:
            d = rng.choice(regs)
            lines.append("    %s = %s ? %s : %s;  /* %x: sel */" % (d, rng.choice(CONDS), operand(), operand(), addr))
        if labels and rng.random() < 0.3:
            lines.append("%s: ;" % labels.pop(0))
    for label in labels:
        lines.append("%s: ;" % label)
    lines.append("    r7 = r7 - 1;  /* fe: subs */")
    lines.append("    if (r7 != 0) goto L_top;  /* ff: bne */")
    lines.append("    return r0 ^ (r1 << 1) ^ (r2 << 2) ^ (r3 << 3) ^ (r12 << 4) ^ (%s << 5);" % rng.choice(
        ["c", "z", "n", "v", "0"]))
    lines.append("}")
    return "\n".join(lines) + "\n"


PROBE = ("typedef unsigned int u32;\ntypedef unsigned long long u64;\ntypedef signed int s32;\ntypedef signed long long s64;\n"
         "static u32 trace;\nstatic u32 probe(u32 a, u32 b, u32 c, u32 d)\n{\n"
         "    trace = trace * 33u + a + 3u * b + 5u * c + 7u * d;\n    return trace ^ 0x5au;\n}\n")


def probe_caller(name, arity=4):
    return ("u32 call_%s(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n    u32 r;\n    trace = 0;\n"
            "    r = %s(%s);\n    return r ^ (trace * 3u);\n}\n" % (name, name, ", ".join(
                ["a0", "a1", "a2", "a3"][:arity])))


@needs_z3
@pytest.mark.slow
@pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")
def test_random_flag_code_cleaned_up_computes_the_same(tmp_path):
    """Random functions of flag code (random_flag_function), cleaned up (every pass), compiled with the
    host's C compiler beside the originals and called with random arguments (equal ones and the
    edges among them, where flags matter): the same results and the same calls in the same order with
    the same arguments; and the clean-up left fewer flag variables and 64-bit sums than it found."""
    rng = random.Random(23)
    count, originals, results, arity = 100, [], [], []
    before = after = 0
    for i in range(count):
        text = random_flag_function(rng, "orig_%d" % i, steps=rng.randint(6, 16))
        originals.append(text)
        fn = cparse.parse(PROBE + text.replace("orig_", "tidy_")).function("tidy_%d" % i)
        tidy.tidy(fn)
        out = cparse.unparse(fn, notes=False)
        before += len(re.findall(r"\b[nzcv]\b|\(u64\)", text.split("L_top: ;", 1)[1]))
        after += len(flags_left(out))
        results.append(out)
        arity.append(len(fn.params))
    source = tmp_path / "flags.c"
    source.write_text(PROBE + "".join(originals) + "\n".join(results) + "\n" + "".join(
        probe_caller("orig_%d" % i) + probe_caller("tidy_%d" % i, arity[i]) for i in range(count)))
    lib = tmp_path / "flags.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-o", str(lib), str(source)], check=True)
    so = ctypes.CDLL(str(lib))
    edges = [0, 1, 2, 5, 7, 9, 0x5f, 0x7fffffff, 0x80000000, 0x80000001, 0xfffffffe, 0xffffffff]
    for i in range(count):
        a, b = getattr(so, "call_orig_%d" % i), getattr(so, "call_tidy_%d" % i)
        for f in (a, b):
            f.restype, f.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 4
        for _ in range(40):
            args = [rng.choice(edges + [rng.getrandbits(32)]) for _ in range(4)]
            if rng.random() < 0.3:
                args[rng.randrange(4)] = args[rng.randrange(4)]
            assert a(*args) == b(*args), (i, args, originals[i], results[i])
    assert after < before * 0.5, (before, after)
