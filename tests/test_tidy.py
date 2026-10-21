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
