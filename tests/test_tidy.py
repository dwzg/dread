"""dread.tidy and dread.cexpr: the clean-up of lifted C, and the proofs behind its rewrites of
conditions and values. What the lifters compute for a compare's flags is proved equal to the
comparison it stands for; a proposal that holds on the values tried but not on all is refused (the
range check of gizmo's parse_kv, which testing alone let through)."""
import ctypes
import random
import shutil
import subprocess

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
    leaves it as its result (newlib's lock functions); not where the callee overwrites r0 (regflow's
    overwritten: written on every path, the caller's never read)."""
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
    ctx = readable.Context()
    ctx.arg_regs = ["r0", "r1", "r2", "r3"]
    ctx.reads, ctx.overwritten = {"g": set(), "f": {"r0"}}, {"g": {"r0"}}
    out = readable.transform("f", SNIPPET, context=ctx)
    assert "r0 = g();" in out and "extern u32 g(void);" in out, out


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


@needs_z3
def test_a_call_is_abstracted_with_its_declared_result_type():
    """A callee declared to hand back two words (u64: a quotient and its remainder) gives a 64-bit
    value: its low word compared, x > (u32)f(a), is not the whole call compared. Taken for unsigned int,
    as every call was until 2026-10-24, the proof held for the wrong semantics. A call through a cast
    pointer has the cast's result type; a callee the context does not know returns unsigned int."""
    wide = cexpr.Ctx(types=TYPES, returns={"f": "u64"})
    low, whole = expr("x > (u32)f(a)"), expr("x > f(a)")
    assert not cexpr.prove(low, whole, wide, as_bool=True)
    assert cexpr.prove(low, whole, cexpr.Ctx(types=TYPES, returns={"f": "u32"}), as_bool=True)
    assert cexpr.prove(low, whole, ctx(), as_bool=True)             # (f not known: unsigned int)
    assert wide.result_type(expr("f(a)")) == (64, False) and wide.result_type(expr("g(a)")) == (32, False)
    assert wide.result_type(expr("((u64 (*)(u32, u32))a)(x, b)")) == (64, False)
    assert wide.result_type(expr("((u32 (*)(u32))a)(x)")) == (32, False)


TWO_WORDS = """/* f at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u64 divmod(u32, u32, u32, u32);
u32 f(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r5 = 0, r12 = 0;
    u32 n = 0, z = 0, c = 0, v = 0;
    (void)n; (void)z; (void)c; (void)v; (void)r4; (void)r5; (void)r12;
    r5 = r0;  /* 0: mov r5, r0 */
    r0 = r1 + 0x1u;  /* 4: add r0, r1, #1 */
    r1 = 0xffffu;  /* 8: ldr r1, [pc, #0x20] */
    { u64 t = divmod(r0, r1, r2, r3); r0 = (u32)t; r1 = (u32)(t >> 32); } r2 = r3 = r12 = 0;  /* c: bl divmod */
%s}
"""
LOW_COMPARED = """    { u32 x = r5, y = r0; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 10: cmp r5, r0 */
    if (!c || z) goto L_20;  /* 14: bls #0x20 */
    r0 = 0x0u;  /* 18: mov r0, #0 */
    return r0;  /* 1c: bx lr */
L_20: ;
    r0 = 0x1u;  /* 20: mov r0, #1 */
    return r0;  /* 24: bx lr */
"""
LOW_SHIFTED = """    r2 = r0;  /* 10: mov r2, r0 */
    r0 = r2 >> 1;  /* 14: lsr r0, r2, #1 */
    r0 = r0 + r5;  /* 18: add r0, r0, r5 */
    return r0;  /* 1c: bx lr */
"""


@needs_z3
@pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")
@pytest.mark.parametrize("use", [LOW_COMPARED, LOW_SHIFTED], ids=["compared", "shifted"])
def test_a_two_word_results_low_word_keeps_its_cast_where_it_is_folded(tmp_path, use):
    """divmod hands back two words (declared u64), f reads the first only: whether x <= 0xffff / (n + 1),
    or that quotient halved plus x. The values pass writes r0 = divmod(...) for r0 = (u32)divmod(...)
    (the assignment converts), and until 2026-10-24 fold then wrote that value into its one reader
    bare, and the conditions search, which took the call for unsigned int, kept it so: x <= divmod(...)
    compared 64 bits, the remainder as the high word, and divmod(...) >> 1 shifted the remainder's bit
    in. Now fold writes the value as the assignment converted it ((u32)divmod(...)) and the proofs take
    the call for the u64 it is. Cleaned up and compiled with the host's C compiler beside the lifted C,
    with a divmod whose remainder is not 0, both give the same."""
    from dread import readable
    text = TWO_WORDS % use
    out = readable.transform("f", text)
    assert "(u32)divmod(" in out, out
    head = ("typedef unsigned int u32;\ntypedef unsigned long long u64;\ntypedef signed int s32;\n"
            "u64 divmod(u32 d, u32 x, u32 c, u32 e)\n{\n    (void)c; (void)e;\n"
            "    return d ? (u64)(x % d) << 32 | x / d : x;\n}\n")

    def own(text, name):
        lines = [line for line in text.splitlines() if not line.startswith(("#include", "extern "))]
        return "\n".join(lines).replace(" f(", " %s(" % name, 1) + "\n"
    source = tmp_path / "two.c"
    source.write_text(head + own(text, "orig") + own(out, "cleaned"))
    lib = tmp_path / "two.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-o", str(lib), str(source)], check=True)
    so = ctypes.CDLL(str(lib))
    a, b = so.orig, so.cleaned
    params = [p[1] for p in cparse.parse(out).function("f").params]
    a.restype, a.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 4
    b.restype, b.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * len(params)
    for count in (1, 3, 6):
        q = 0xffff // (count + 1)
        for x in (0, 1, q - 1, q, q + 1, 100000, 0xffffffff):
            given = {"r0": x, "r1": count}
            assert a(x, count, 0, 0) == b(*[given.get(p, 0) for p in params]), (count, hex(x), out)


IS_FULL = """/* xStreamBufferIsFull at 0xc4bc, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 vAssertCalled(u32, u32, u32, u32);
extern u32 xStreamBufferSpacesAvailable(u32, u32, u32, u32);
u32 xStreamBufferIsFull(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r5 = 0, r6 = 0, r7 = 0, r8 = 0, r9 = 0, r10 = 0, r11 = 0, r12 = 0, lr = (u32)__builtin_return_address(0);
    u32 n = 0, z = 0, c = 0, v = 0;
    u32 frame[1] __attribute__((aligned(8)));
    u32 sp0 = (u32)&frame[0] + 16;
    (void)n; (void)z; (void)c; (void)v; (void)r12; (void)lr; (void)sp0;
    /* c4bc: push {r3, r4, r5, lr} */
    r5 = r0;  /* c4be: mov r5, r0 */
    if (r0 != 0) goto L_c4cc;  /* c4c0: cbnz r0, #0xc4cc */
    r1 = 0x557u;  /* c4c2: movw r1, #0x557 */
    r0 = 0x10a3u;  /* c4c6: ldr r0, [pc, #0x1c] */
    r0 = vAssertCalled(r0, r1, r2, r3); r1 = r2 = r3 = r12 = 0;  /* c4c8: bl #0x17dc */
L_c4cc: ;
    r0 = r5;  /* c4cc: mov r0, r5 */
    r4 = (u32)*(volatile u8 *)((r5 + 28));  /* c4ce: ldrb r4, [r5, #0x1c] */
    r0 = xStreamBufferSpacesAvailable(r0, r1, r2, r3); r1 = r2 = r3 = r12 = 0;  /* c4d0: bl #0xc10c */
    r4 = r4 & 0x1u;  /* c4d4: and r4, r4, #1 */
    { u32 x = r4; r4 = (x << 2); n = r4 >> 31; z = r4 == 0; c = ((x >> 30) & 1); }  /* c4d8: lsls r4, r4, #2 */
    { u32 x = r0, y = r4; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* c4da: cmp r0, r4 */
    /* c4dc: ite hi */
    if ((c && !z)) { r0 = 0x0u; }  /* c4de: movhi r0, #0 */
    if ((!c || z)) { r0 = 0x1u; }  /* c4e0: movls r0, #1 */
    return r0;  /* c4e2: pop {r3, r4, r5, pc} */
}"""


@needs_z3
def test_a_conversion_no_reader_sees_writes_no_cast(monkeypatch):
    """FreeRTOS GCC -Os xStreamBufferIsFull (its lifted C, of public code): a byte loaded into r4 before
    a call, its bit 0 shifted after it. The values pass drops the load's (u32) (r4 = *(volatile u8
    *)(r5 + 28)), and fold writes the load into its reader where the conversion to r4's type (a byte
    promoted to int there) changes nothing: z3 proves the reader the same with and without it
    (same_reads), so no cast goes in, as before 2026-10-24; without the proof (1)
    `((u32)*(volatile u8 *)(r0 + 28) & 1) << 2`. (The same proof keeps a two-word comparison's carry
    (r3 >= 5) bare, so it pairs into one 64-bit comparison: prvSanityCheckCreatedQueue.)"""
    from dread import readable
    out = readable.transform("xStreamBufferIsFull", IS_FULL)
    assert "r4 = (*(volatile u8 *)(r0 + 28) & 1) << 2;" in out, out
    monkeypatch.setattr(tidy, "same_reads", lambda *a, **k: False)
    monkeypatch.setattr(readable, "_MEMO", {})          # (transform keeps what it made: made anew)
    out = readable.transform("xStreamBufferIsFull", IS_FULL)
    assert "r4 = ((u32)*(volatile u8 *)(r0 + 28) & 1) << 2;" in out, out     # (1: correct, and a cast more)



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


def test_a_slot_whose_name_is_taken_gets_another_prefix():
    """A slot is named s8 for sp0 - 8; where the function has a name s8 already (RISC-V's saved
    register, a VFP register, here a parameter), its slots take the next prefix instead of staying
    raw."""
    text = FRAME.replace("u32 a3)", "u32 s8)").replace("r3 = a3", "r3 = s8")
    fn = cparse.parse(text).function("h")
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False)
    assert "sp0" not in out and "frame" not in out, out
    fn = cparse.parse(text.replace(" (void)sp0;", "")).function("h")     # (the pass alone)
    assert tidy.frame_locals(fn)
    out = cparse.unparse(fn, notes=False)
    assert "sl8" in out and "u32 s8" in out, out
def test_a_frame_pointer_read_on_every_path_is_written_into_its_uses():
    """GCC -O0's frame pointer: set once from the frame (r4 = sp0 - 16), read on both arms of a
    branch and more than eight times. sp0 never changes, so each use reached by that one assignment
    alone takes sp0 - 16 in its place (r4 + 8 is sp0 - 8), and the slots become variables."""
    text = FRAME.replace("    *(volatile u32 *)(sp0 - 8) = r1;  /* 0: str r1, [sp, #8] */\n",
                         "    r4 = (sp0 - 16) + 0x0u;  /* 0: add r4, sp, #0 */\n"
                         "    *(volatile u32 *)(r4 + 8) = r1;  /* 0: str r1, [r4, #8] */\n"
                         "    *(volatile u32 *)(r4 + 4) = r2;  /* 0: str r2, [r4, #4] */\n"
                         "    if (r0 == 0) goto L_x;  /* 0: cbz r0 */\n"
                         "    *(volatile u32 *)(r4 + 4) = r3;  /* 0: str r3, [r4, #4] */\n"
                         "L_x:\n")
    text = text.replace("    r1 = *(volatile u32 *)(sp0 - 8);  /* 6: ldr r1, [sp, #8] */\n",
                        "    r1 = *(volatile u32 *)(r4 + 8);  /* 6: ldr r1, [r4, #8] */\n" +
                        "".join("    r1 = r1 + *(volatile u32 *)(r4 + %d);  /* 6: ldr */\n" % (4 + 4 * (k % 2))
                                for k in range(9)))
    fn = cparse.parse(text).function("h")
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False)
    assert "r4" not in out and "sp0" not in out and "frame" not in out, out
    twice = text.replace("    if (r0 == 0) goto L_x;", "    r4 = sp0 - 8;  /* 0: add */\n    if (r0 == 0) goto L_x;")
    fn = cparse.parse(twice).function("h")
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False)                  # (two values of r4: each use takes its own)
    assert "r4" not in out, out
    for source, kept in ((text, False), (text.replace("L_x:\n", "L_x:\n    sp0 = sp0 - 4;  /* 0: sub */\n"), True)):
        fn = cparse.parse(source).function("h")            # (the pass alone: with sp0 written, its
        while tidy.addresses(fn):                          # value may differ where r4 is read)
            pass
        out = cparse.unparse(fn, notes=False)
        assert ("r4 + 8" in out) == kept, out


def test_a_slot_stored_as_a_byte_and_read_signed_is_a_variable_with_a_cast():
    """strb, then ldrsb of the same slot: one size, both signs. The slot becomes a variable of the
    unsigned type and the signed read a cast, (s8)s12, which sign-extends as ldrsb did."""
    text = FRAME.replace("    *(volatile u32 *)(sp0 - 8) = r1;  /* 0: str r1, [sp, #8] */\n",
                         "    *(volatile u8 *)(sp0 - 12) = (u8)r1;  /* 0: strb r1, [sp, #4] */\n")
    text = text.replace("    r1 = *(volatile u32 *)(sp0 - 8);  /* 6: ldr r1, [sp, #8] */\n",
                        "    r1 = (u32)*(volatile s8 *)(sp0 - 12);  /* 6: ldrsb r1, [sp, #4] */\n")
    fn = cparse.parse(text.replace(" (void)sp0;", "")).function("h")
    assert tidy.frame_locals(fn)
    out = cparse.unparse(fn, notes=False)
    assert "u8 s12;" in out and "(s8)s12" in out and "sp0 - 12" not in out, out
    inplace = text.replace("    r0 = r0 + r1;  /* 8: adds r0, r1 */\n",
                           "    (*(volatile s8 *)(sp0 - 12))++;  /* 8: (made up) */\n    r0 = r0 + r1;\n")
    fn = cparse.parse(inplace.replace(" (void)sp0;", "")).function("h")
    assert not tidy.frame_locals(fn)                       # (changed in place with the other sign: raw)


def test_a_slot_read_before_it_is_written_stays_and_the_others_become_variables():
    """A call through a pointer is handed a frame word nothing stored (sp0 - 16): that slot stays as
    it is, what earlier frames left there; the slot the function stores (sp0 - 8) becomes a variable."""
    text = FRAME.replace("r0 = g(r0, r1, r2, r3);", "r0 = g(r0, r1, r2, *(volatile u32 *)(sp0 - 16));")
    fn = cparse.parse(text.replace(" (void)sp0;", "")).function("h")
    assert tidy.frame_locals(fn)
    out = cparse.unparse(fn, notes=False)
    assert "*(volatile u32 *)(sp0 - 16)" in out and "sp0 - 8" not in out and "u32 s8;" in out, out
    assert "s16" not in out, out                           # (nor the s16 the raw slot would have been)


def test_an_index_added_to_a_frame_address_counts_from_where_its_constants_come_to():
    """sp0 - 8 + r0 + 0xfffffff4u adds the index r0 to sp0 - 20 (frame_index: RISC-V's addi, add and a
    load with a negative offset write such sums). The slot at sp0 - 16, stored and read, lies below
    sp0 - 8, the only frame address written in the sum or handed to g, but above sp0 - 20, where the
    index starts: it stays in the frame, where the byte read through the index may find it (it became a
    variable before, and that read found what nobody had stored)."""
    got = tidy.frame_index(expr("sp0 - 64 + 0x20u + s0_1 + 0xffffffecu"))
    assert got[0] == -52 and [(s, cparse.expr_text(t)) for s, t in got[1]] == [(1, "s0_1")]
    assert tidy.frame_index(expr("sp0 - 8 + 0x4u")) is None and tidy.frame_index(expr("r1 + 4 + r2")) is None
    assert tidy.frame_index(expr("sp0 - (r3 + 0x10u)"))[0] == -16      # (the index subtracted: sign -1)
    text = FRAME.replace("    *(volatile u32 *)(sp0 - 8) = r1;  /* 0: str r1, [sp, #8] */\n",
                         "    *(volatile u32 *)(sp0 - 16) = r1;  /* 0: str r1, [sp] */\n")
    text = text.replace("r0 = g(r0, r1, r2, r3);", "r0 = g(r0, sp0 - 8, r2, r3);")
    text = text.replace("    r1 = *(volatile u32 *)(sp0 - 8);  /* 6: ldr r1, [sp, #8] */\n",
                        "    r1 = *(volatile u32 *)(sp0 - 16) + (u32)*(volatile u8 *)(sp0 - 8 + r0 + 0xfffffff4u);"
                        "  /* 6: (made up) */\n")
    fn = cparse.parse(text.replace(" (void)sp0;", "")).function("h")
    assert not tidy.frame_locals(fn)
    assert "*(volatile u32 *)(sp0 - 16)" in cparse.unparse(fn, notes=False)


def test_a_slot_does_not_take_the_name_of_a_type_the_function_uses():
    """The slot at sp0 - 8 would be s8, which is also a type; where the function uses that type (a
    signed byte load elsewhere), its slots take the next prefix."""
    text = FRAME.replace("    r0 = r0 + r1;  /* 8: adds r0, r1 */\n",
                         "    r0 = r0 + r1 + (u32)*(volatile s8 *)(r0 + 3);  /* 8: (made up) */\n")
    fn = cparse.parse(text.replace(" (void)sp0;", "")).function("h")
    assert tidy.frame_locals(fn)
    out = cparse.unparse(fn, notes=False)
    assert "u32 sl8;" in out and "(volatile s8 *)" in out, out


@needs_z3
def test_a_16_bit_variable_does_not_make_int_16_bits():
    """int is 16 bits where the lifter's registers are (MSP430); a u16 variable the readable C made (an
    ARM function's 16-bit stack slot) left it 32: in 16 bits ~0xcu is 0xfff3, the subtraction's zero
    test (u32)(s16)x != 12 never held, and the conditions pass proved it to be x >= 0."""
    src = ('#include "dread.h"\nu32 f(u32 r0)\n{\n    u16 x;\n    x = (u16)r0;\n'
           '    if (!((u32)((u64)(u32)(s16)x + (u32)~0xcu + 1) == 0)) return 1;\n    return 0;\n}\n')
    fn = cparse.parse(src).function("f")
    assert tidy.context(fn).int_bits == 32
    assert tidy.conditions(fn)
    out = cparse.unparse(fn, notes=False)
    assert "if (x != 0xcu)" in out, out
    msp = cparse.parse('#include "dread.h"\nu16 g(void)\n{\n    u16 r4 = 0, r12_1;\n    r12_1 = r4;\n'
                       '    return r12_1;\n}\n').function("g")
    assert tidy.context(msp).int_bits == 16                 # (the lifter's registers: MSP430)


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


def test_a_parameter_both_arms_of_an_if_write_before_a_read_is_none():
    """params reads the entry's value in the flow of flat_view: an if/else whose arms both write r0 ends
    it, so r0 is no parameter (in a graph of the body's statements the if was one node that wrote r0
    on some path only)."""
    text = ("u32 f(u32 a0, u32 a1)\n{\n"
            "    u32 r0 = a0, r1 = a1;\n"
            "    if (r1 != 0u) { r0 = 1u; } else { r0 = 2u; }  /* 0: ite ne */\n"
            "    return r0;  /* 6: bx lr */\n}\n")
    fn = cparse.parse("typedef unsigned int u32;\n" + text).function("f")
    assert tidy.params(fn)
    out = cparse.unparse(fn, notes=False)
    assert "u32 f(u32 a0, u32 r1)" in out and "u32 r0;" in out, out


def test_a_select_waits_for_the_merge_of_an_it_block_that_writes_its_variable():
    """ittte mi; ldrmi r0, [r3]; uxtbmi r0, r0; movpl r0, #-1 (gizmo's uart_getc): the pair of the last
    two is no select while the block before it on the same condition writes r0, which the select would
    read; merge_ifs makes the three one if/else, both of whose arms write r0, and the function takes no
    parameter. Without that block the pair is a select as before."""
    text = ("u32 f(u32 a0)\n{\n"
            "    u32 r0 = a0, n;\n"
            "    n = (*(volatile u32 *)0x40011004u >> 1) & 1u;  /* 0: lsls r2, r2, #0x1e */\n"
            "    if (n) { r0 = *(volatile u32 *)0x40011000u; }  /* 2: ldrmi r0, [r3] */\n"
            "    if (n) { r0 = (u32)(u8)r0; }  /* 4: uxtbmi r0, r0 */\n"
            "    if (!n) { r0 = 0xffffffffu; }  /* 6: movpl.w r0, #-1 */\n"
            "    return r0;  /* a: bx lr */\n}\n")
    fn = cparse.parse("typedef unsigned int u32;\ntypedef unsigned char u8;\n" + text).function("f")
    assert not tidy.selects(fn)
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False)
    assert "u32 f(void)" in out and "?" not in out, out
    alone = cparse.parse("typedef unsigned int u32;\ntypedef unsigned char u8;\n" + text.replace(
        "    if (n) { r0 = *(volatile u32 *)0x40011000u; }  /* 2: ldrmi r0, [r3] */\n", "")).function("f")
    assert tidy.selects(alone) and "r0 = n ? (u32)(u8)r0 : 0xffffffffu;" in cparse.unparse(alone, notes=False)


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



IT = """/* f at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
u32 f(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r12 = 0, lr = (u32)__builtin_return_address(0);
    u32 n = 0, z = 0, c = 0, v = 0;
    (void)n; (void)z; (void)c; (void)v; (void)r12; (void)lr;
    r2 = 0x20000100u;  /* 0: ldr r2, [pc, #0x10] */
    r1 = 0x20000200u;  /* 2: ldr r1, [pc, #0x14] */
    r0 = (u32)*(volatile u32 *)((r2 + 0));  /* 4: ldr r0, [r2] */
    r3 = (u32)*(volatile u32 *)((r1 + 0));  /* 6: ldr r3, [r1] */
    { u32 x = r0, y = r3; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 8: cmp r0, r3 */
    /* a: ite eq */
    if (z) { r0 = 0x5u; }  /* c: moveq r0, #5 */
    if (!z) { r0 = (u32)*(volatile u32 *)((r1 + 0)); }  /* e: ldrne r0, [r1] */
    r3 = 0x20000300u;  /* 10: ldr r3, [pc, #0x10] */
    /* 12: ite ne */
    if (!z) { *(volatile u32 *)((r2 + 0)) = (u32)r0; }  /* 14: strne r0, [r2] */
    if (z) { *(volatile u32 *)((r3 + 0)) = (u32)r0; }  /* 16: streq r0, [r3] */
    r0 = (u32)*(volatile u32 *)((r3 + 0));  /* 18: ldr r0, [r3] */
    return r0;  /* 1a: bx lr */
}
"""


def tidy_text(text, name="f"):
    fn = cparse.parse(text).function(name)
    tidy.tidy(fn)
    return cparse.unparse(fn, notes=False, style="readable")


@needs_z3
def test_an_it_blocks_conditional_instructions_are_one_if_else():
    """GCC's IT blocks for `if (a == b) x = 5; else a = b;`: cmp; ite eq; moveq; ldrne; (a constant
    load); ite ne; strne; streq. One if per instruction, on z and !z, with the instructions' notes and
    a load between them, becomes one if/else on the comparison; each path keeps its loads and stores
    in their order (b is loaded twice where the original loads it twice), the flag goes."""
    out = tidy_text(IT)
    assert out.count("if (") == 1 and "else" in out, out
    assert "r0 == *(volatile u32 *)0x20000200u" in out, out              # (z's carry arithmetic: a ==)
    assert "*(volatile u32 *)0x20000300u = 0x5u;" in out, out
    assert "*(volatile u32 *)0x20000100u = *(volatile u32 *)0x20000200u;" in out, out
    assert out.count("*(volatile u32 *)0x20000200u") == 2 and "u64" not in out and " z" not in out, out
    assert out.index("r0 = *(volatile u32 *)0x20000100u;") < out.index("if ("), out


def test_an_if_and_its_negation_are_merged_only_while_the_condition_holds():
    body = ("    if (z) { r0 = 0x5u; }  /* 4: moveq r0, #5 */\n"
            "    if (!z) { r1 = 0x6u; }  /* 6: movne r1, #6 */\n"
            "    if (z) { r2 = 0x7u; }  /* 8: moveq r2, #7 */\n")
    src = SNIPPET.replace(
        "    { u32 x = r0, y = r3; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 4: cmp r0, r3 */\n"
        "    if (!c || z) goto L_c;  /* 6: bls #0xc */\n", body)
    fn = cparse.parse(src).function("f")
    assert tidy.merge_ifs(fn)
    out = cparse.unparse(fn, notes=False)
    assert out.count("if (") == 1 and "else" in out and "r2 = 0x7u;" in out.split("else")[0], out
    writes = src.replace("if (!z) { r1 = 0x6u; }", "if (!z) { z = r1 == 0; }")    # (the else writes z)
    fn = cparse.parse(writes).function("f")
    tidy.merge_ifs(fn)
    assert cparse.unparse(fn, notes=False).count("if (") == 2
    jumps = src.replace("if (!z) { r1 = 0x6u; }", "if (!z) goto L_c;")         # (a branch stays one)
    fn = cparse.parse(jumps).function("f")
    tidy.merge_ifs(fn)
    assert cparse.unparse(fn, notes=False).count("if (") == 3


BETWEEN = """/* f at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 take(u32, u32, u32, u32);
u32 f(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r5 = 0, r12 = 0, lr = (u32)__builtin_return_address(0);
    u32 n = 0, z = 0, c = 0, v = 0;
    (void)n; (void)z; (void)c; (void)v; (void)r12; (void)lr;
    r5 = 0x20000400u;  /* 0: ldr r5, [pc, #0x20] */
    r0 = take(r0, r1, r2, r3); r1 = r2 = r3 = r12 = 0;  /* 2: bl take */
    { u32 x = r0, y = 0x1u; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 6: cmp r0, #1 */
    /* 8: itt ne */
    if (!z) { r2 = 0xc1u; }  /* a: movne r2, #0xc1 */
    if (!z) { r3 = 0x20000500u; }  /* c: movne r3, #0x... */
    r0 = (u32)*(volatile u32 *)((r5 + 0));  /* e: ldr r0, [r5] */
    /* 10: it ne */
    if (!z) { *(volatile u32 *)((r3 + 0)) = (u32)r2; }  /* 12: strne r2, [r3] */
    return r0;  /* 14: pop {pc} */
}
"""


@needs_z3
def test_a_load_between_two_it_blocks_goes_into_both_arms():
    """GCC puts a load between the IT block that sets up a store and the one that makes it (itt ne;
    movne; movne; ldr r0, [r5]; it ne; strne): the load is written into both arms of the one if, so
    the store's address is a constant on its path and the registers the call zeroed are dead."""
    out = tidy_text(BETWEEN)
    assert out.count("if (") == 1 and "else" in out, out
    assert "if (take(r0, r1, r2, r3) != 1)" in out, out
    assert "*(volatile u32 *)0x20000500u = 0xc1u;" in out, out
    assert out.count("*(volatile u32 *)0x20000400u;") == 2 and " z" not in out and "= 0;" not in out, out
    assert out.index("*(volatile u32 *)0x20000400u") < out.index("0x20000500u"), out   # (load, then store)
    call = BETWEEN.replace("r0 = (u32)*(volatile u32 *)((r5 + 0));  /* e: ldr r0, [r5] */",
                           "r0 = take(r0, r1, r2, r3);  /* e: bl take */")
    out = tidy_text(call)                                     # (a call between: not written twice)
    assert out.count("take(") == 2 and out.count("if (") == 2, out


CCMP = """/* f at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
u32 f(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r5 = 0, r12 = 0;
    u32 n = 0, z = 0, c = 0, v = 0;
    (void)n; (void)z; (void)c; (void)v; (void)r12;
    r5 = r2 + 0x3u;  /* 0: adds r5, r2, #3 */
    r4 = (u32)*(volatile u32 *)((r3 + 0));  /* 2: ldr r4, [r3] */
    r1 = (u32)*(volatile u32 *)((r3 + 4));  /* 4: ldr r1, [r3, #4] */
    { u32 x = r0, y = r5; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 6: cmp r0, r5 */
    /* 8: it ne */
    if (!z) { { u32 x = r4, y = r1; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; } }  /* a: cmpne r4, r1 */
    /* c: ite ne */
    if (!z) { r0 = 0x1u; }  /* e: movne r0, #1 */
    if (z) { r0 = 0x0u; }  /* 10: moveq r0, #0 */
    return r0;  /* 12: bx lr */
}
"""


@needs_z3
def test_a_conditional_compare_is_one_condition_and_its_loads_stay_unconditional():
    """cmp r0, r5; it ne; cmpne r4, r1 leaves z = r0 == r5 || r4 == r1: the compare's block inside the
    if is flattened like any other, and z = A; if (!z) z = B; is z = A || B. The second word was
    loaded before the compare on every path: it is not folded into the right of the ||, where it
    would be loaded only when r0 != r5. Since 2026-10-23 the select that reads z reads the
    conditional compare's two ways (tidy.flags, FlagFlow.chosen), its negation taken in: r0 != r2 + 3
    && r4 != r1 (before, !(r0 == r2 + 3 || r4 == r1))."""
    out = tidy_text(CCMP)
    assert "r0 != r2 + 3 && r4 != r1" in out, out
    assert "r1 = *(volatile u32 *)(r3 + 4);" in out and "u64" not in out, out
    assert out.index("r1 = *(volatile u32 *)(r3 + 4);") < out.index("&&"), out


@needs_z3
def test_the_conditions_search_shares_its_candidates_and_searches_once_a_run(monkeypatch):
    """shared_candidates are candidates() in its order, each size counted as size() walks it, sharing
    the condition's subexpressions (made_candidate makes the one kept of copies, as candidates()
    always made them); below leaves out the larger ones. In one tidy run each condition is searched
    once, the next rounds take its answer: CCMP's five, eleven when every round searches again
    (tidy._tidy, outside a run), and the clean-up comes out the same."""
    from dread.cflow import walk_expr
    e = expr("!(u32)((%s) >> 32) || %s == 0" % (SUB, R))         # (ls: a <= b)
    c = ctx()
    shared, made = tidy.shared_candidates(e, c), tidy.candidates(e, c)
    assert len(shared) > 20 and [cparse.expr_text(x) for _, x, _ in shared] == [cparse.expr_text(x) for x in made]
    assert all(n == tidy.size(x) for n, x, _ in shared)
    inside = {id(x) for x in walk_expr(e)}
    assert all(id(x.l) in inside for _, x, (kind, _, _, _, _) in shared if kind == "plain")
    assert not any(id(y) in inside for x in made for y in walk_expr(x))
    limit = tidy.size(e)
    assert [x for n, x, _ in shared if n < limit] == [x for _, x, _ in tidy.shared_candidates(e, c, below=limit)]
    searched, real = [], tidy.shared_candidates
    monkeypatch.setattr(tidy, "shared_candidates", lambda e, ctx, most=10, below=None, **more:
                        searched.append(cparse.expr_text(e)) or real(e, ctx, most, below, **more))
    runs = {}
    for way, run in (("a run", tidy.tidy), ("every round again", lambda fn: tidy._tidy(fn, None, None, 8))):
        del searched[:]
        fn = cparse.parse(CCMP).function("f")
        run(fn)
        runs[way] = cparse.unparse(fn), list(searched)
    assert runs["a run"][0] == runs["every round again"][0] and "r0 != r2 + 3 && r4 != r1" in runs["a run"][0]
    assert len(runs["a run"][1]) == len(set(runs["a run"][1])) == len(set(runs["every round again"][1]))
    assert len(runs["a run"][1]) < len(runs["every round again"][1])


FLAG = """/* f at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 g(u32, u32, u32, u32);
u32 f(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r5 = 0, r12 = 0;
    u32 n = 0, z = 0, c = 0, v = 0;
    (void)n; (void)z; (void)c; (void)v; (void)r12;
    r5 = r2;  /* 2: mov r5, r2 */
    r12 = (u32)*(volatile u32 *)((r1 + 0));  /* 4: ldr ip, [r1] */
    { u32 x = r0, y = r12; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 6: cmp r0, ip */
    /* 8: it eq */
    if (z) { r1 = 0x1u; }  /* a: moveq r1, #1 */
    r0 = g(r0, r1, r2, r3); r1 = r2 = r3 = r12 = 0;  /* c: bl g */
    /* 10: it ne */
    if (!z) { r0 = (u32)((u64)r0 + (u32)~r5 + 1); }  /* 12: subne r0, r0, r5 */
    return r0;  /* 14: pop {r4, r5, pc} */
}
"""


@needs_z3
def test_a_flag_read_twice_is_a_comparison_and_a_return_a_plain_value():
    """A flag two statements read stays a variable, its carry arithmetic a comparison: a call between
    keeps the ifs apart, and its operand in memory keeps it out of the conditions; a subtraction the
    lifter computed through 64 bits is one, in an arm as in a return."""
    out = tidy_text(FLAG)
    assert "z = r0 == *(volatile u32 *)r1;" in out and "if (z)" in out and "if (!z)" in out, out
    assert "r0_1 = r0_1 - r2;" in out and "u64" not in out, out
    out = tidy_text(FLAG.replace("    return r0;", "    return (u32)((u64)r0 + (u32)~r5 + 1);"))
    assert "return r0_1 - r2;" in out and "u64" not in out, out


def test_a_mask_that_keeps_a_select_of_constants_goes():
    c = ctx()
    out = tidy.peephole(expr("(a == b ? 0x1u : 0x0u) & 0x1u"), c)
    assert cparse.expr_text(out) == "a == b ? 0x1u : 0x0u", cparse.expr_text(out)
    for kept in ("(a == b ? 0x3u : 0x0u) & 0x1u", "(a == b ? 1 : 0) & 0x1ull", "(a == b ? x : 0x0u) & 0x1u"):
        assert "&" in cparse.expr_text(tidy.peephole(expr(kept), c)), kept


def random_it_function(rng, name, steps=10):
    """A function in the lifters' dialect made of what Thumb-2's IT blocks become: compares (a block
    that sets n, z, c, v), conditional blocks on the flags (most on z or !z, the rest on any condition
    an IT block tests), some with a conditional compare inside, and plain statements between; calls
    to probe log their order and arguments."""
    regs = ["r0", "r1", "r2", "r3"]
    conds = ["z", "!z", "c", "!c", "n != v", "n == v", "c && !z", "!c || z"]
    lines = ["u32 %s(u32 a0, u32 a1, u32 a2, u32 a3)" % name, "{",
             "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r12 = 0;",
             "    u32 n = 0, z = 0, c = 0, v = 0;",
             "    (void)n; (void)z; (void)c; (void)v; (void)r12;"]

    def compare():
        a, b = rng.sample(regs, 2)
        return ("{ u32 x = %s, y = %s; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; "
                "c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }" % (a, b))

    def op():
        a, b = rng.choice(regs), rng.choice(regs)
        if rng.random() < 0.12:
            return "%s = probe(r0, r1, r2, r3);" % a
        return "%s = %s %s %s;" % (a, b, rng.choice(["+", "-", "^", "&", "|"]),
                                   rng.choice([b, rng.choice(regs), "%du" % rng.randint(0, 9)]))
    lines.append("    " + compare() + "  /* 0: cmp */")
    for k in range(steps):
        r = rng.random()
        if r < 0.6:
            cond = rng.choice(conds[:2]) if rng.random() < 0.7 else rng.choice(conds)
            body = " ".join(op() for _ in range(rng.randint(1, 2)))
            if rng.random() < 0.15:
                body += " " + compare()
            lines.append("    if (%s) { %s }  /* %x: it */" % (cond, body, 2 * k + 2))
        elif r < 0.9:
            lines.append("    %s  /* %x: op */" % (op(), 2 * k + 2))
        else:
            lines.append("    %s  /* %x: cmp */" % (compare(), 2 * k + 2))
    lines.append("    return r0 ^ (r1 << 1) ^ (r2 << 2) ^ (r3 << 3);")
    lines.append("}")
    return "\n".join(lines) + "\n"


PROBE = ("typedef unsigned int u32;\ntypedef unsigned long long u64;\ntypedef signed int s32;\nstatic u32 trace;\n"
         "static u32 probe(u32 a, u32 b, u32 c, u32 d)\n{\n    trace = trace * 33u + a + 3u * b + 5u * c + 7u * d;\n"
         "    return trace ^ 0x5au;\n}\n")


def probe_caller(name, arity=4):
    """call_NAME: NAME called with its first arity arguments (the clean-up drops trailing parameters
    nothing reads), its result and probe's trace in one value."""
    return ("u32 call_%s(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n    u32 r;\n    trace = 0;\n"
            "    r = %s(%s);\n    return r ^ (trace * 3u);\n}\n" % (name, name, ", ".join(
                ["a0", "a1", "a2", "a3"][:arity])))


@pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")
def test_random_it_blocks_cleaned_up_compute_the_same(tmp_path):
    """Random functions of IT-block-like code (random_it_function), cleaned up (every pass), compiled
    with the host's C compiler beside the originals and called with random arguments: the same result
    and the same calls in the same order with the same arguments (probe's trace); and the clean-up
    merged most of the conditional blocks."""
    rng = random.Random(11)
    count, originals, results, arity = 300, [], [], []
    ifs_before = ifs_after = 0
    for i in range(count):
        text = random_it_function(rng, "orig_%d" % i, steps=rng.randint(4, 14))
        originals.append(text)
        fn = cparse.parse(PROBE + text.replace("orig_", "tidy_")).function("tidy_%d" % i)
        tidy.tidy(fn)
        out = cparse.unparse(fn, notes=False)
        ifs_before += text.count("if (")
        ifs_after += out.count("if (")
        results.append(out)
        arity.append(len(fn.params))
    source = tmp_path / "its.c"
    source.write_text(PROBE + "".join(originals) + "\n".join(results) + "\n" + "".join(
        probe_caller("orig_%d" % i) + probe_caller("tidy_%d" % i, arity[i]) for i in range(count)))
    lib = tmp_path / "its.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-o", str(lib), str(source)], check=True)
    so = ctypes.CDLL(str(lib))
    for i in range(count):
        a, b = getattr(so, "call_orig_%d" % i), getattr(so, "call_tidy_%d" % i)
        for f in (a, b):
            f.restype, f.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 4
        for _ in range(30):
            args = [rng.choice([0, 1, 2, 0x7fffffff, 0x80000000, 0xffffffff, rng.getrandbits(32)]) for _ in range(4)]
            assert a(*args) == b(*args), (i, args, originals[i], results[i])
    assert ifs_after < ifs_before * 0.75, (ifs_before, ifs_after)
