"""dread.tidy and dread.cexpr: the clean-up of lifted C, and the proofs behind its rewrites of
conditions and values. What the lifters compute for a compare's flags is proved equal to the
comparison it stands for; a proposal that holds on the values tried but not on all is refused (the
range check of gizmo's parse_kv, which testing alone let through)."""
import pytest

from dread import cexpr, cparse, tidy

needs_z3 = pytest.mark.skipif(cexpr.z3 is None, reason="needs z3-solver (the extra 'prove')")

TYPES = {"a": (32, False), "b": (32, False), "x": (32, False), "c8": (8, False)}


def expr(text):
    return cparse.parse("void f(void)\n{\n    q = %s;\n}\n" % text).function("f").body.body[0].e.value


def ctx():
    return cexpr.Ctx(types=TYPES)


SUB = "(u64)a + (u32)~b + 1"                 # the lifters' cmp a, b: a - b with its carry
R = "(u32)(%s)" % SUB


def test_c_values_with_c_types():
    run = lambda text, **env: cexpr.compile_expr(expr(text), ctx())(env)[0]
    assert run("a - b", a=1, b=2) == 0xffffffff
    assert run("(s32)a < (s32)b", a=0xffffffff, b=0) == 1 and run("a < b", a=0xffffffff, b=0) == 0
    assert run("(s32)a >> 4", a=0x80000000) == -0x8000000 and run("a >> 4", a=0x80000000) == 0x8000000
    assert run("c8 + 1", c8=255) == 256                  # (the promotion to int)
    assert run("(s32)a / 2", a=0xfffffffd) == -1         # (truncated toward 0)
    with pytest.raises(cexpr.Undefined):
        run("a << b", a=1, b=32)


@needs_z3
def test_the_flags_of_a_compare_are_the_comparisons_they_stand_for():
    c = ctx()
    proved = lambda lifted, simple: cexpr.prove(expr(lifted), expr(simple), c, as_bool=True)
    assert proved("%s == 0" % R, "a == b")                                       # z: eq
    assert proved("(u32)((%s) >> 32)" % SUB, "a >= b")                          # c: hs
    assert proved("!(u32)((%s) >> 32) || %s == 0" % (SUB, R), "a <= b")          # ls
    n, v = "%s >> 31" % R, "((a ^ %s) & (~b ^ %s)) >> 31" % (R, R)
    assert proved("(%s) != (%s)" % (n, v), "(s32)a < (s32)b")                    # lt
    assert proved("%s == 0 || (%s) != (%s)" % (R, n, v), "(s32)a <= (s32)b")     # le
    assert not proved("(u32)((%s) >> 32)" % SUB, "a > b")
    assert not proved("(%s) != (%s)" % (n, v), "a < b")


@needs_z3
def test_a_proposal_that_holds_on_the_values_tried_is_not_enough():
    """parse_kv's letter test: (x & ~0x20) - 0x41 <= 0x19. Before the constants' neighbours were among
    the test values, x == 0x41 agreed with it on all of them."""
    lifted = expr("!(u32)((u64)((x & ~0x20u) - 0x41u) + (u32)~0x19u + 1 >> 32) || "
                  "(u32)((u64)((x & ~0x20u) - 0x41u) + (u32)~0x19u + 1) == 0")
    c = ctx()
    wrong, right = expr("x == 0x41u"), expr("(x & ~0x20u) - 0x41u <= 0x19u")
    assert not cexpr.prove(lifted, wrong, c, as_bool=True)
    assert cexpr.prove(lifted, right, c, as_bool=True)
    check = cexpr.Checker(lifted, c, as_bool=True)
    assert not check.agrees(wrong) and check.stands_for(right)


SNIPPET = """/* f at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 g(u32, u32, u32, u32);
u32 f(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r12 = 0, lr = (u32)__builtin_return_address(0);
    u32 n = 0, z = 0, c = 0, v = 0;
    u32 frame[2] __attribute__((aligned(8)));
    u32 sp0 = (u32)&frame[2];
    (void)n; (void)z; (void)c; (void)v; (void)r12; (void)lr; (void)sp0;
    *(volatile u32 *)(sp0 - 4) = lr;  /* 0: push {lr} */
    r3 = 0x10u;  /* 2: movs r3, #16 */
    { u32 x = r0, y = r3; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 4: cmp r0, r3 */
    if (!c || z) goto L_c;  /* 6: bls #0xc */
    r0 = g(r0, r1, r2, r3); r1 = r2 = r3 = r12 = 0;  /* 8: bl g */
L_c: ;
    lr = *(volatile u32 *)(sp0 - 4);  /* c: pop {pc} */
    return r0;  /* c: pop {pc} */
}
"""


def tidied(text=SNIPPET):
    tree = cparse.parse(text)
    fn = tree.function("f")
    tidy.tidy(fn)
    return cparse.unparse(fn)


@needs_z3
def test_the_clean_up_leaves_the_comparison_and_the_call():
    text = tidied()
    assert "if (r0 <= 0x10u) goto L_c;" in text, text
    assert "r0 = g(r0, r1, r2, 0x10u);" in text, text
    for gone in ("(void)", "__builtin_return_address", "sp0", "frame", " n ", " v;", "u64"):
        assert gone not in text, (gone, text)
    assert "/* 6: bls #0xc */" in text                   # (each statement keeps its instruction)


def test_calls_pass_what_their_callees_read():
    """With what regflow measured (a context): g reads r0 and r1, so its call passes two arguments
    and its declaration follows; f's own parameters are its registers, the unread ones gone. A call
    whose value is read keeps its first argument even when the callee reads none: an empty callee
    leaves it as its result (newlib's lock functions)."""
    from dread import readable
    ctx = readable.Context()
    ctx.arg_regs = ["r0", "r1", "r2", "r3"]
    ctx.reads = {"g": {"r0", "r1"}, "f": {"r0"}}
    out = readable.transform("f", SNIPPET, context=ctx)
    assert "extern u32 g(u32, u32);" in out and "r0 = g(r0, r1);" in out, out
    assert "u32 f(u32 r0, u32 r1)" in out, out          # (r1 is passed on to g: read)
    ctx = readable.Context()                            # (a new one: transform remembers by context)
    ctx.arg_regs = ["r0", "r1", "r2", "r3"]
    ctx.reads = {"g": set(), "f": {"r0"}}
    out = readable.transform("f", SNIPPET, context=ctx)
    assert "r0 = g(r0);" in out and "extern u32 g(u32);" in out, out


def test_without_z3_no_condition_is_rewritten(monkeypatch):
    monkeypatch.setattr(cexpr, "z3", None)
    text = tidied()
    assert "if (r0 <= 0x10u)" not in text and "goto L_c;" in text, text
    assert "(void)" not in text                          # (what needs no proof is still done)


@needs_z3
def test_a_load_is_abstracted_with_its_own_type():
    """A byte loaded without a cast is promoted to int: its value minus 1 is below 0 exactly when it is
    0. Taken for an unsigned int, it never would be, and a proof would hold for the wrong semantics."""
    c = ctx()
    load = "*(volatile u8 *)p"
    assert cexpr.prove(expr("%s - 1 < 0" % load), expr("%s == 0" % load), c, as_bool=True)
    assert not cexpr.prove(expr("%s - 1 < 0" % load), expr("0"), c, as_bool=True)


FRAME = """/* h at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 g(u32, u32, u32, u32);
u32 h(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r12 = 0;
    u32 n = 0, z = 0, c = 0, v = 0;
    u32 frame[4] __attribute__((aligned(8)));
    u32 sp0 = (u32)&frame[4];
    (void)n; (void)z; (void)c; (void)v; (void)r12; (void)sp0;
    *(volatile u32 *)(sp0 - 8) = r1;  /* 0: str r1, [sp, #8] */
    r0 = g(r0, r1, r2, r3); r1 = r2 = r3 = r12 = 0;  /* 2: bl g */
    r1 = *(volatile u32 *)(sp0 - 8);  /* 6: ldr r1, [sp, #8] */
    r0 = r0 + r1;  /* 8: adds r0, r1 */
    return r0;  /* a: pop {pc} */
}
"""


def test_a_frame_word_nobody_else_sees_is_a_variable():
    tree = cparse.parse(FRAME)
    fn = tree.function("h")
    tidy.tidy(fn)
    text = cparse.unparse(fn, notes=False)
    assert "sp0" not in text and "frame" not in text, text
    assert "g(" in text and "return" in text, text
    escaped = FRAME.replace("r0 = g(r0, r1, r2, r3);", "r0 = g(r0, sp0 - 8, r2, r3);")
    tree = cparse.parse(escaped)
    fn = tree.function("h")
    tidy.tidy(fn)
    text = cparse.unparse(fn, notes=False)
    assert "*(volatile u32 *)(sp0 - 8)" in text, text      # (its address leaves: the frame stays)


def test_a_slot_below_a_frame_address_that_leaves_is_a_variable():
    """What a frame address is handed to may reach the frame above it, not below: a slot below the
    lowest address that leaves (an outgoing stack argument, a local nobody is given) becomes a
    variable, the slots from there up stay as they are."""
    text = FRAME.replace("    *(volatile u32 *)(sp0 - 8) = r1;  /* 0: str r1, [sp, #8] */\n",
                         "    *(volatile u32 *)(sp0 - 8) = r1;  /* 0: str r1, [sp, #8] */\n"
                         "    *(volatile u32 *)(sp0 - 16) = r2;  /* 0: str r2, [sp] */\n")
    text = text.replace("r0 = g(r0, r1, r2, r3);", "r0 = g(r0, sp0 - 8, r2, r3);")
    text = text.replace("    r0 = r0 + r1;  /* 8: adds r0, r1 */\n",
                        "    r0 = r0 + r1 + *(volatile u32 *)(sp0 - 16);  /* 8: adds r0, r1 */\n")
    fn = cparse.parse(text).function("h")
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False)
    assert "*(volatile u32 *)(sp0 - 8)" in out and "sp0 - 16" not in out, out
    fn = cparse.parse(text.replace("sp0 - 8, r2", "sp0 - 16, r2")).function("h")   # (the lowest leaves)
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False)
    assert "*(volatile u32 *)(sp0 - 16)" in out and "*(volatile u32 *)(sp0 - 8)" in out, out


@needs_z3
def test_a_carry_over_a_load_is_a_comparison_that_still_loads_once():
    tree = cparse.parse(SNIPPET.replace(
        "    { u32 x = r0, y = r3; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 4: cmp r0, r3 */\n"
        "    if (!c || z) goto L_c;  /* 6: bls #0xc */",
        "    c = (u32)((u64)r3 + (u32)~*(volatile u32 *)r1 + 1 >> 32);  /* 4: cmp r3, [r1] */\n"
        "    if (!c) { r0 = 1u; }  /* 6: movlo r0, #1 */\n"
        "    if (!c) { r2 = 5u; }  /* 6: movlo r2, #5 */\n"
        "    goto L_c;  /* 7: b */"))
    fn = tree.function("f")
    tidy.tidy(fn)
    text = cparse.unparse(fn, notes=False, style="readable")
    assert text.count("*(volatile u32 *)r1") == 1, text
    assert "if (0x10u < *(volatile u32 *)r1)" in text or "if (*(volatile u32 *)r1 > 0x10u)" in text, text
    assert text.count("if (") == 1, text                # (the two conditional moves under one if)


def test_an_if_that_writes_its_own_condition_inside_a_block_is_not_merged():
    """An IT block's conditional compare (cmpne r0, #0) is a block inside the if and writes z, which
    the next conditional instructions test: if (!z) { { z = r0 == 0; } } if (!z) r1 = 1; are two ifs."""
    body = ("    if (!z) { { u32 x = r0; z = x == 0; } }  /* 4: cmpne r0, #0 */\n"
            "    if (!z) { r1 = 0x1u; }  /* 6: movne r1, #1 */\n")
    text = SNIPPET.replace(
        "    { u32 x = r0, y = r3; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 4: cmp r0, r3 */\n"
        "    if (!c || z) goto L_c;  /* 6: bls #0xc */\n", "    z = r1 == 0;  /* 3: cmp r1, #0 */\n" + body)
    tree = cparse.parse(text)
    fn = tree.function("f")
    assert tidy.merge_ifs(fn) is False


def test_a_register_with_two_unrelated_values_becomes_two_variables():
    text = ("u32 f(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n"
            "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r5;\n"
            "    r5 = r1 + 1u;  /* 0: adds r5, r1, #1 */\n"
            "    r2 = r5 * 2u;  /* 2: lsls r2, r5, #1 */\n"
            "    r5 = r3 ^ r0;  /* 4: eors r5, r3, r0 */\n"
            "    return r5 + r2;  /* 6: adds r0, r5, r2 */\n}\n")
    fn = cparse.parse("typedef unsigned int u32;\n" + text).function("f")
    assert tidy.webs(fn)
    out = cparse.unparse(fn, notes=False)
    assert "r5_1 = r3 ^ r0;" in out and "return r5_1 + r2;" in out and "r2 = r5 * 2u;" in out, out
    assert not tidy.webs(fn)                            # (one web each now)


def test_a_split_renames_the_reads_inside_a_conditional_block_too():
    text = ("u32 f(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n"
            "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r5;\n"
            "    r5 = r1 + 1u;  /* 0: adds r5, r1, #1 */\n"
            "    r2 = r5 * 2u;  /* 2: lsls r2, r5, #1 */\n"
            "    r5 = r3 ^ r0;  /* 4: eors r5, r3, r0 */\n"
            "    if (r1 != 0) { *(volatile u8 *)r0 = (u8)r5; }  /* 6: strbne r5, [r0] */\n"
            "    return r5 + r2;  /* 8: adds r0, r5, r2 */\n}\n")
    fn = cparse.parse("typedef unsigned int u32;\ntypedef unsigned char u8;\n" + text).function("f")
    assert tidy.webs(fn)
    out = cparse.unparse(fn, notes=False)
    assert "= (u8)r5_1;" in out and "return r5_1 + r2;" in out, out


def test_a_variable_written_inside_a_conditional_block_is_split_too():
    """The webs see the block spread out: the value stored inside it is a variable of its own, and
    the load after it another (before, a variable written inside a block was left whole)."""
    text = ("u32 f(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n"
            "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r5;\n"
            "    r5 = *(volatile u32 *)r0;  /* 0: ldr r5, [r0] */\n"
            "    if (r5 == 0) { r5 = r1 + 2u; *(volatile u32 *)(r0 + 4) = r5; }  /* 2: it eq */\n"
            "    r5 = *(volatile u32 *)(r0 + 8);  /* 8: ldr r5, [r0, #8] */\n"
            "    return r5 + 1u;  /* 10: adds r0, r5, #1 */\n}\n")
    fn = cparse.parse("typedef unsigned int u32;\n" + text).function("f")
    assert tidy.webs(fn)
    out = cparse.unparse(fn, notes=False)
    assert "if (r5 == 0)" in out and "r5_1 = r1 + 2u;" in out and "(r0 + 4) = r5_1;" in out, out
    assert "r5_2 = *(volatile u32 *)(r0 + 8);" in out and "return r5_2 + 1u;" in out, out
    assert not tidy.webs(fn)


def test_a_parameter_keeps_its_name_for_its_value_at_the_entry_only():
    text = ("u32 f(u32 r0, u32 r1)\n{\n"
            "    u32 r3;\n"
            "    r3 = r0 + 4u;  /* 0: adds r3, r0, #4 */\n"
            "    r0 = *(volatile u32 *)r1;  /* 2: ldr r0, [r1] */\n"
            "    *(volatile u32 *)r3 = r0;  /* 4: str r0, [r3] */\n"
            "    return r0;  /* 6: bx lr */\n}\n")
    fn = cparse.parse("typedef unsigned int u32;\n" + text).function("f")
    assert tidy.webs(fn)
    out = cparse.unparse(fn, notes=False)
    assert "u32 f(u32 r0, u32 r1)" in out and "r3 = r0 + 4u;" in out, out
    assert "u32 r0_1;" in out and "r0_1 = *(volatile u32 *)r1;" in out and "return r0_1;" in out, out


def test_a_parameter_whose_value_at_the_entry_nothing_reads_is_none():
    """Trailing ones go, one before a parameter that stays is renamed; the register's variable is a
    local then (prvAddCurrentTaskToDelayedList's r2 and r3, written before every read, were two more
    parameters)."""
    text = ("u32 f(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n"
            "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3;\n"
            "    r1 = r0 + r2;  /* 0: adds r1, r0, r2 */\n"
            "    r3 = r1 * 2u;  /* 2: lsls r3, r1, #1 */\n"
            "    return r3;  /* 4: mov r0, r3 */\n}\n")
    fn = cparse.parse("typedef unsigned int u32;\n" + text).function("f")
    assert tidy.params(fn)
    out = cparse.unparse(fn, notes=False)
    assert "u32 f(u32 r0, u32 a1, u32 r2)" in out, out
    assert "u32 r1;" in out and "u32 r3;" in out and "r1 = r0 + r2;" in out, out
    assert not tidy.params(fn)


def test_plain_declarations_of_one_type_share_a_line_in_the_order_of_their_names():
    text = ("u32 f(u32 r0)\n{\n"
            "    u32 r3;\n    u32 r2, r10;\n    u64 t_7;\n    u32 r0_1;\n    u32 r5 = 0u;\n"
            "    r3 = r0; r2 = r3; r10 = r2; r0_1 = r10; t_7 = r0_1; r5 = (u32)t_7;\n    return r5;\n}\n")
    fn = cparse.parse("typedef unsigned int u32;\ntypedef unsigned long long u64;\n" + text).function("f")
    assert tidy.group_declarations(fn)
    out = cparse.unparse(fn, notes=False)
    assert "u32 r0_1, r2, r3, r10;" in out and "u64 t_7;" in out and "u32 r5 = 0u;" in out, out
    assert not tidy.group_declarations(fn)


@needs_z3
def test_a_compare_against_minus_one_names_the_constant():
    """The lifters' adds r3, r4, #1; bne is x + 1 == 0: x == 0xffffffff (FreeRTOS's portMAX_DELAY in
    vListInsert), a constant that is not in the expression itself (its negation is)."""
    tree = cparse.parse(SNIPPET.replace(
        "    { u32 x = r0, y = r3; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 4: cmp r0, r3 */\n"
        "    if (!c || z) goto L_c;  /* 6: bls #0xc */",
        "    { u32 x = r0, y = 0x1u; u64 t = (u64)x + y + 0; r12 = (u32)t; n = r12 >> 31; z = r12 == 0; c = (u32)(t >> 32); v = ((x ^ r12) & (y ^ r12)) >> 31; }  /* 4: adds r12, r0, #1 */\n"
        "    if (!z) goto L_c;  /* 6: bne #0xc */"))
    fn = tree.function("f")
    tidy.tidy(fn)
    text = cparse.unparse(fn, notes=False)
    assert "if (r0 != 0xffffffffu) goto L_c;" in text, text


LDRD = """/* f at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
u32 f(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r12 = 0;
    u32 n = 0, z = 0, c = 0, v = 0;
    (void)n; (void)z; (void)c; (void)v; (void)r12;
    { u32 a = r0 + 4u; r1 = *(volatile u32 *)a; r2 = *(volatile u32 *)(a + 4u); }  /* 0: ldrd r1, r2, [r0, #4] */
    r0 = r1 + r2;  /* 4: adds r0, r1, r2 */
    return r0;  /* 6: bx lr */
}
"""


def test_an_address_computed_once_is_written_into_its_uses():
    """The lifters' base of an ldrd (a_1 = r0 + 4) read by the two loads behind it, r0 unchanged:
    the loads read through r0 with one constant offset each, which the types write as members. When
    the first load writes the base (ldrd r0, r1, [r0, #4]), the second still reads the old one: the
    variables split first, so the loaded value is a variable of its own."""
    fn = cparse.parse(LDRD).function("f")
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False)
    assert "a_1" not in out and "(r0 + 4)" in out and "(r0 + 8)" in out, out
    overwritten = LDRD.replace("r1 = *(volatile u32 *)a; r2 = *(volatile u32 *)(a + 4u);",
                               "r0 = *(volatile u32 *)a; r1 = *(volatile u32 *)(a + 4u);").replace(
        "r0 = r1 + r2;", "r0 = r0 + r1;")
    fn = cparse.parse(overwritten).function("f")
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False)
    assert "(r0 + 8)" in out and "r0 = *(volatile u32 *)(r0 + 4)" not in out, out


def test_a_frame_address_computed_once_leaves_frame_slots_the_frame_pass_sees():
    """An stm into the frame through a base the lifter computed once (a_1 = sp0 - 16): written into
    its uses, the stores are frame slots at constant offsets, and the frame pass makes them variables."""
    text = FRAME.replace("    *(volatile u32 *)(sp0 - 8) = r1;  /* 0: str r1, [sp, #8] */\n",
                         "    { u32 a = sp0 - 16u; *(volatile u32 *)a = r1; *(volatile u32 *)(a + 4u) = r2; }"
                         "  /* 0: stm r3, {r1, r2} */\n")
    text = text.replace("    r1 = *(volatile u32 *)(sp0 - 8);  /* 6: ldr r1, [sp, #8] */\n",
                        "    r1 = *(volatile u32 *)(sp0 - 16) + *(volatile u32 *)(sp0 - 12);  /* 6: ldr */\n")
    fn = cparse.parse(text).function("h")
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False)
    assert "a_1" not in out and "sp0" not in out and "frame" not in out, out

