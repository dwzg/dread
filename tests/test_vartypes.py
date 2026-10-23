"""dread.vartypes: readable C's variables, parameters and prototypes typed by their use. The rewrites on
small functions of the readable dialect (pointer arithmetic scaled by the pointee: a u16 pointer's + 2
bytes is + 1, a structure pointer's advance by its size is + 1; accesses as p[k]; signed compares;
the externs a file's calls fit), the plan's choices (what saves casts), gizmo's strncpy, read_line and
parse_kv, a test program of our own through the gate on three builds, and random functions compiled
by the host in 32 bits beside their lifted C (the same results)."""
import json
import os
import random
import re
import shutil
import subprocess

import pytest

from dread import cparse, polish, shapes, vartypes
from conftest import gizmo_dir

HEAD = ('#include "dread.h"\n'
        "extern u32 sink(u32, u32);\n")


def typed(text, name="f", p=None):
    """(printed file, plan) of text's function name after the plan made over the text's functions and
    applied (dread.shapes' structures of p first, as readable._named applies them), then polished."""
    p = p or shapes.Plan()

    def fresh():
        tree = cparse.parse(text)
        for fn in [f for f in tree.items if isinstance(f, cparse.Function)]:
            if p.typed.get(fn.name):
                shapes.apply_types(tree, fn, p)
        return tree
    tree = fresh()
    vartypes.plan(p, [(tree, fn, {}) for fn in tree.items if isinstance(fn, cparse.Function)])
    tree = fresh()
    fn = tree.function(name)
    vartypes.apply(tree, fn, p)
    polish.polish(tree, fn)
    others = [f.name for f in tree.items if isinstance(f, cparse.Function) and f.name != name]
    return cparse.unparse(tree, notes=False, style="readable", verbatim=others), p


def test_a_byte_walk_is_a_pointer_to_bytes_const_where_nothing_is_stored():
    """gizmo a32's strncpy as the stages leave it: the destination's group (the parameter, its copy and
    the end pointer made from it) is volatile u8 *, the source's (the parameter one below its first
    byte, advanced) const volatile u8 *; the accesses p[k] and *p, the advances element counts, the
    result (u32)r0 (the result stays u32)."""
    out, p = typed(HEAD + """u32 f(u32 r0, u32 r1, u32 r2)
{
    u32 r1_1, r2_1, r3, r12;
    r3 = r0;
    r1_1 = r1 - 1;
    for (;;)
    {
        if (r2 == 0) break;
        r12 = *(volatile u8 *)(r1_1 + 1); r1_1 = r1_1 + 1;
        if (r12 == 0) break;
        *(volatile u8 *)r3 = (u8)r12; r3 = r3 + 1;
        r2 = r2 - 1;
    }
    r2_1 = r3 + r2;
    for (;;)
    {
        if (r3 == r2_1) return r0;
        *(volatile u8 *)r3 = (u8)0x0u; r3 = r3 + 1;
    }
}
""")
    assert p.variables["f"]["types"] == {"r0": "volatile u8 *", "r3": "volatile u8 *", "r2_1": "volatile u8 *",
                                         "r1": "const volatile u8 *", "r1_1": "const volatile u8 *"}, p.variables
    assert p.prototypes["f"] == [None, "volatile u8 *", "const volatile u8 *", None], p.prototypes
    for line in ("u32 f(volatile u8 *r0, const volatile u8 *r1, u32 r2)", "const volatile u8 *r1_1;",
                 "volatile u8 *r2_1, *r3;", "r1_1 = r1 - 1;", "r12 = r1_1[1];", "r1_1++;", "*r3 = r12;", "r3++;",
                 "r2_1 = r3 + r2;", "if (r3 == r2_1) return (u32)r0;", "*r3 = 0;"):
        assert line in out, (line, out)
    assert "volatile u8 *)" not in out.split("{", 1)[1], out      # (no raw access left)
    assert out.startswith("/* dread: overflow wraps */\n/* dread: null checks kept */\n"), out


def test_pointer_arithmetic_on_a_u16_pointer_counts_elements():
    """A pointer read at 16 bits: + 2 bytes is + 1, an access at + 4 is p[2], an index shifted by one
    the index, + 6 is += 3; two such pointers compare as pointers. An offset that is no whole number of
    elements keeps its integer arithmetic and the access its cast."""
    out, p = typed(HEAD + """u32 f(u32 r0, u32 r1)
{
    u32 r2, r3, r4, r5;
    r2 = r0 + 2;
    r3 = *(volatile u16 *)(r2 + 4);
    r4 = *(volatile u16 *)(r0 + (r1 << 1));
    r0 = r0 + 6;
    r5 = *(volatile u16 *)r0;
    if (r0 == r2) r3 = r3 + 1;
    if (r0 < r2) r3 = r3 + 2;
    return r3 + r4 + r5;
}
""")
    assert p.variables["f"]["types"] == {"r0": "const volatile u16 *", "r2": "const volatile u16 *"}, p.variables
    for line in ("u32 f(const volatile u16 *r0, u32 r1)", "r2 = r0 + 1;", "r3 = r2[2];", "r4 = r0[r1];",
                 "r0 += 3;", "r5 = *r0;", "if (r0 == r2) r3++;", "if (r0 < r2) r3 += 2;"):
        assert line in out, (line, out)
    tree = cparse.parse(HEAD + "u32 f(u32 r0)\n{\n    u32 r3;\n    r3 = *(volatile u16 *)(r0 + 1);\n"
                        "    r3 = r3 + *(volatile u16 *)(r0 + 2);\n    return r3;\n}\n")
    fn = tree.function("f")
    r = vartypes.Rewriter(fn, {"r0": "const volatile u16 *"}, polish.Types(tree, fn))
    for st in fn.body.body:
        r.stmt(st)
    text = cparse.unparse(fn, notes=False, style="readable")
    assert "r3 = *(volatile u16 *)((u32)r0 + 1);" in text and "r3 = r3 + r0[1];" in text, text


def test_a_structure_pointer_advanced_by_its_size_is_plus_one():
    """dread.shapes' structure pointer advanced by the structure's size (12: fields at 0 and 8) is r0++
    (C adds sizeof); an advance that is no whole number of structures keeps dread.shapes' cast, and an
    access past the structure stays raw; a structure only declared in the file (no size) is never
    advanced."""
    p = shapes.Plan()
    p.structs = {"s_1": [(0, "u32"), (8, "u32")]}
    p.typed = {"f": {"r0": "s_1"}}
    out, _ = typed(HEAD + """u32 f(u32 r0, u32 r1)
{
    u32 r2;
    r2 = 0;
    do
    {
        r2 = r2 + *(volatile u32 *)r0 + *(volatile u32 *)(r0 + 8);
        r0 = r0 + 12;
        r1 = r1 - 1;
    }
    while (r1 != 0);
    r0 = r0 + 4;
    return r2 + *(volatile u32 *)(r0 + 24);
}
""", p=p)
    for line in ("u32 f(volatile struct s_1 *r0, u32 r1)", "r2 = r2 + r0->f_0 + r0->f_8;", "r0++;",
                 "r0 = (volatile struct s_1 *)((u32)r0 + 4);", "return r2 + *(volatile u32 *)((u32)r0 + 24);"):
        assert line in out, (line, out)
    tree = cparse.parse(HEAD + "struct s_2;\nu32 f(u32 r0)\n{\n    sink(r0, 0);\n    r0 = r0 + 12;\n"
                        "    return sink(r0, 1);\n}\n")
    fn = tree.function("f")
    types = polish.Types(tree, fn)
    r = vartypes.Rewriter(fn, {"r0": "volatile struct s_2 *"}, types, sizes={"struct s_2": 12})
    assert r.sizes == {}, r.sizes             # (declared only: an incomplete type)
    for st in fn.body.body:
        r.stmt(st)
    assert "r0 = (volatile struct s_2 *)((u32)r0 + 12);" in cparse.unparse(fn, notes=False, style="readable")


def test_a_variable_compared_signed_is_s32():
    """A parameter compared only signed is s32 (its compares without the cast, its other uses as they
    were: equality, arithmetic converted back); a counter that a right shift and a division read too
    stays u32 (each would need (u32)v back), and a variable compared both ways stays u32."""
    out, p = typed(HEAD + """u32 f(u32 r0, u32 r1)
{
    u32 r2, r3, r4;
    r2 = 0;
    r3 = 0;
    for (;;)
    {
        if ((s32)r2 > 4) break;
        r3 = r3 + (r2 >> 1) + r2 / 3;
        r2 = r2 + 1;
    }
    if ((s32)r1 < 0) r3 = r3 + 1;
    if (r1 == 0xffffffffu) r3 = r3 + 2;
    r4 = r0;
    if ((s32)r4 < 0) r3 = r3 + 4;
    if (r4 < r3) r3 = r3 + 8;
    return r3 + r1;
}
""")
    assert p.variables["f"]["types"] == {"r1": "s32"}, p.variables
    for line in ("u32 f(u32 r0, s32 r1)", "if (r1 < 0) r3++;", "if (r1 == -1) r3 += 2;", "return r3 + r1;",
                 "if ((s32)r2 > 4) break;", "if ((s32)r4 < 0) r3 += 4;"):
        assert line in out, (line, out)


def test_a_loop_counter_compared_signed_is_s32():
    """FreeRTOS GCC -Os vPrimaryBlockTimeTestTask's counters: stored, compared (s32)r2_1 > 4, given the
    stored value plus one: s32 r2_1, if (r2_1 > 4) break;."""
    out, p = typed(HEAD + """#define g_2000184c (*(volatile u32 *)0x2000184cu)
u32 f(u32 r0)
{
    u32 r2_1, r2_4;
    r2_1 = 0;
    for (;;)
    {
        *(volatile u32 *)(r0 + 4) = r2_1;
        if ((s32)r2_1 > 4) break;
        sink(g_2000184c, r0 + 8);
        r2_4 = *(volatile u32 *)(r0 + 4);
        r2_1 = r2_4 + 1;
    }
    return 0;
}
""")
    assert p.variables["f"]["types"].get("r2_1") == "s32", p.variables
    assert "s32 r2_1;" in out and "if (r2_1 > 4) break;" in out and "(s32)" not in out, out


def test_rewritten_signed_uses_keep_their_meaning():
    """An s32 variable where the lifted C read it unsigned: (u32)v in an unsigned order, a right shift, a
    division, a 64-bit conversion; (s32)v is v; equality, sums, stores and arguments as they are."""
    tree = cparse.parse(HEAD + """u64 f(u32 r0, u32 r1)
{
    u32 r2;
    r2 = 0;
    if ((s32)r0 > (s32)r1) r2 = r0 >> 3;
    if (r0 < r1) r2 = r2 + r0 / 7;
    if (r0 == 5) r2 = r2 + r0;
    *(volatile u32 *)r1 = r0;
    sink(r0, r2);
    return (u64)r0 + r2;
}
""")
    fn = tree.function("f")
    r = vartypes.Rewriter(fn, {"r0": "s32"}, polish.Types(tree, fn))
    for st in fn.body.body:
        r.stmt(st)
    text = cparse.unparse(fn, notes=False, style="readable")
    for line in ("if (r0 > (s32)r1) r2 = (u32)r0 >> 3;",
                 "if ((u32)r0 < r1) r2 = r2 + (u32)r0 / 7;", "if (r0 == 5) r2 = r2 + r0;",
                 "*(volatile u32 *)r1 = r0;", "sink(r0, r2);", "return (u64)(u32)r0 + r2;"):
        assert line in text, (line, text)


def test_steps_and_compound_assignments_keep_their_meaning():
    """x++ of the lifted C adds one byte: on a u16 pointer that is no element, so it is written through
    the integer ((volatile u16 *)((u32)r0 + 1)), as is the value of r0++ ((u32)(r0 = ...) - 1); an s32
    variable shifted or divided as the lifted C's u32 is (u32)r1 >> 2, (u32)r1 / 3; += stays."""
    tree = cparse.parse(HEAD + """u32 f(u32 r0, u32 r1)
{
    u32 r2, r3;
    r2 = *(volatile u16 *)r0;
    r0++;
    r3 = r0++;
    r1 >>= 2;
    r1 /= 3;
    r1 += 5;
    if ((s32)r1 < 0) r2 = r2 + 1;
    return r2 + r3 + r1;
}
""")
    fn = tree.function("f")
    r = vartypes.Rewriter(fn, {"r0": "volatile u16 *", "r1": "s32"}, polish.Types(tree, fn))
    for st in fn.body.body:
        r.stmt(st)
    text = cparse.unparse(fn, notes=False, style="readable")
    for line in ("r2 = *r0;", "r0 = (volatile u16 *)((u32)r0 + 1);",
                 "r3 = (u32)(r0 = (volatile u16 *)((u32)r0 + 1)) - 1;", "r1 = (u32)r1 >> 2;", "r1 = (u32)r1 / 3;",
                 "r1 += 5;", "if (r1 < 0) r2 = r2 + 1;"):
        assert line in text, (line, text)


def test_a_result_is_signed_where_its_callers_compare_it_signed():
    """A callee whose result reaches a signed compare in its caller (through a variable every reaching
    definition of which is the call, as read_line's uart_getc) is declared s32 (its head and the
    caller's extern); a variable that holds a parameter and then the callee's result is no evidence
    there (handle_key's r0 and led_on)."""
    text = HEAD + """extern u32 getc_like(void);
extern u32 led_like(void);
u32 getc_like(void)
{
    u32 r0;
    r0 = *(volatile u32 *)0x40000000u;
    if (r0 == 0) return ~0x0u;
    return r0 & 0xffu;
}
u32 led_like(void)
{
    *(volatile u32 *)0x40000004u = 1;
    return 0;
}
u32 f(u32 r0)
{
    u32 r1, r2;
    r1 = getc_like();
    if ((s32)r1 < 0) return 0;
    if ((s32)r0 > 7) r0 = led_like();
    r2 = r1 + r0;
    return r2;
}
"""
    out, p = typed(text)
    assert p.prototypes.get("getc_like") == ["s32"] and "led_like" not in p.prototypes, p.prototypes
    assert "extern s32 getc_like(void);" in out and "extern u32 led_like(void);" in out, out
    assert "s32 r1;" in out and "if (r1 < 0) return 0;" in out, out
    head, _ = typed(text, name="getc_like")
    assert "s32 getc_like(void)" in head and "return -1;" in head, head


def test_an_extern_takes_a_pointer_type_where_its_calls_fit_it():
    """A callee that walks bytes takes volatile u8 *; a caller that hands it the address of static data
    declares the extern so and passes &g_x[0] (no (u32)& cast), one that hands it a number keeps the
    extern u32 (a cast at every call would be noise) and a caller's variable handed only to it takes
    its type where that adds no cast."""
    text = HEAD + """#define g_200017c5 ((volatile u8 *)0x200017c5u)
extern u32 fill(u32, u32, u32);
u32 fill(u32 r0, u32 r1, u32 r2)
{
    u32 r2_1, r3;
    r3 = r0;
    r2_1 = r2 + r0;
    while (r3 != r2_1)
    {
        *(volatile u8 *)r3 = r1; r3 = r3 + 1;
    }
    return r0;
}
u32 by_address(void)
{
    fill((u32)&g_200017c5[0], 0, 0x15u);
    return 0;
}
u32 by_number(u32 r0)
{
    fill(0x2000u, 0, 8);
    return fill(r0 + 0x100u, 0, 8);
}
"""
    out, p = typed(text, name="by_address")
    assert p.variables["by_address"]["calls"] == {"fill": {"0": "volatile u8 *"}}, p.variables
    assert "extern u32 fill(volatile u8 *, u32, u32);" in out and "fill(&g_200017c5[0], 0, " in out, out
    out, p = typed(text, name="by_number")
    assert "by_number" not in p.variables and "extern u32 fill(u32, u32, u32);" in out, (p.variables, out)
    out, _ = typed(text, name="fill")
    assert "u32 fill(volatile u8 *r0, u32 r1, u32 r2)" in out and "*r3 = r1;" in out, out


def test_a_structure_parameter_takes_a_held_members_address_and_hands_on_a_type():
    """A callee whose parameter dread.shapes types as a structure (an item) is declared so in a caller
    that hands it the address of the item its own structure holds (&r0->f_4, not (u32)&r0->f_4); a
    caller's parameter only handed to a callee that reads words through it takes that callee's type
    (count_items' const volatile u32 *)."""
    text = HEAD + """extern u32 unlink_item(u32);
extern u32 count_items(u32);
u32 unlink_item(u32 r0)
{
    u32 r1, r2;
    r1 = *(volatile u32 *)(r0 + 4);
    r2 = *(volatile u32 *)(r0 + 8);
    *(volatile u32 *)(r1 + 8) = r2;
    *(volatile u32 *)(r2 + 4) = r1;
    return r1;
}
u32 count_items(u32 r0)
{
    return *(volatile u32 *)r0 + *(volatile u32 *)(r0 + 4);
}
u32 task_drop(u32 r0, u32 r1)
{
    u32 r3;
    r3 = *(volatile u32 *)(r0 + 32);
    unlink_item(r0 + 4);
    count_items(r1);
    return r3 + count_items(r1);
}
"""
    p = shapes.Plan()
    p.structs = {"s_1": [(0, "u32"), (32, "u32")], "s_2": [(0, "u32"), (4, "u32"), (8, "u32")]}
    p.embeds = {"s_1": {4: "s_2"}}
    p.typed = {"task_drop": {"r0": "s_1"}, "unlink_item": {"r0": "s_2"}}
    out, p = typed(text, name="task_drop", p=p)
    assert p.prototypes["unlink_item"] == [None, "volatile struct s_2 *"], p.prototypes
    assert p.variables["task_drop"]["calls"] == {"count_items": {"0": "const volatile u32 *"},
                                                 "unlink_item": {"0": "volatile struct s_2 *"}}, p.variables
    for line in ("extern u32 unlink_item(volatile struct s_2 *);", "extern u32 count_items(const volatile u32 *);",
                 "u32 task_drop(volatile struct s_1 *r0, const volatile u32 *r1)", "unlink_item(&r0->f_4);",
                 "count_items(r1);", "r3 = r0->f_20;"):
        assert line in out, (line, out)


def test_a_function_that_calls_itself_declares_itself_as_its_head():
    """The extern of a function in its own file is its head's prototype (C takes no other): typed
    parameters there too, the self-call's arguments converted to them."""
    out, p = typed(HEAD + """extern u32 f(u32, u32);
u32 f(u32 r0, u32 r1)
{
    u32 r2;
    if (r1 == 0) return 0;
    r2 = *(volatile u8 *)r0;
    return r2 + f(r0 + 1, r1 - 1);
}
""")
    assert "extern u32 f(const volatile u8 *, u32);" in out and "u32 f(const volatile u8 *r0, u32 r1)" in out, out
    assert "return r2 + f(r0 + 1, r1 - 1);" in out, out


def test_frame_addresses_and_asm_operands_stay_untyped():
    """A variable given a frame address (dread.frames' object) or named in an asm keeps its type."""
    tree = cparse.parse(HEAD + """u32 f(u32 r0)
{
    u32 a_1, r3, sp0;
    u32 frame[4];
    sp0 = (u32)&frame[4];
    a_1 = sp0 - 8;
    *(volatile u32 *)a_1 = r0;
    r3 = r0;
    __asm__ volatile ("" : "+r" (r3));
    return *(volatile u32 *)(a_1 + 4) + *(volatile u32 *)r3;
}
""")
    fn = tree.function("f")
    assert vartypes.eligible(fn) == {"r0"}, vartypes.eligible(fn)


def test_the_lifters_stack_arguments_stay_integers():
    """The lifter reads a stack argument and the one after it through the address of the first
    (a_1 = (u32)&s1; *(volatile u32 *)(a_1 + 4)) and writes one at its slot (*(volatile u32 *)(u32)&s0 =
    0): a_1 stays u32 and &s0 no pointer of a typed access (dread.polish writes the access's cast of it
    without the u32 between, *(volatile u32 *)&s0: the same volatile access). Typed, a_1[1] read past
    s1 (clang -O2 took it for never so: FreeRTOS's xTaskCreateStatic failed its cases), and *&s0 = 0 was
    a store the compiler may keep in a register, never at the slot (GCC -O0's xEventGroupWaitBits)."""
    text = HEAD + """u32 f(u32 r0, u32 r1, u32 r2, u32 r3, u32 s0, u32 s1)
{
    u32 a_1, r4, r5;
    a_1 = (u32)&s1; r4 = *(volatile u32 *)a_1; r5 = *(volatile u32 *)(a_1 + 4);
    if (*(volatile u32 *)(u32)&s0 != 0) *(volatile u32 *)(u32)&s0 = 0;
    *(volatile u32 *)r0 = r4 + r5;
    return sink((u32)&s0, r4);
}
"""
    tree = cparse.parse(text)
    assert vartypes.eligible(tree.function("f")) == {"r0", "r1", "r2", "r3", "r4", "r5"}
    out, p = typed(text)
    assert "a_1" not in p.variables["f"]["types"], p.variables
    for line in ("a_1 = (u32)&s1;", "r4 = *(volatile u32 *)a_1;", "r5 = *(volatile u32 *)(a_1 + 4);",
                 "if (*(volatile u32 *)&s0 != 0) *(volatile u32 *)&s0 = 0;", "return sink((u32)&s0, r4);",
                 "*r0 = r4 + r5;"):
        assert line in out, (line, out)


def test_the_plan_keeps_its_variables_and_prototypes():
    """The readable_types table's variables (with the externs' parameters) and prototypes survive
    writing and reading; a table without them (older) reads as none."""
    p = shapes.Plan()
    p.variables = {"f": {"digest": "ab", "types": {"r0": "volatile u8 *", "r1": "s32"},
                         "calls": {"g": {"0": "const volatile u8 *"}}}}
    p.prototypes = {"f": [None, "volatile u8 *", "s32"], "g": ["s32"]}
    q = shapes.Plan.from_json(json.loads(json.dumps(p.to_json())))
    assert q.variables == p.variables and q.prototypes == p.prototypes, (q.variables, q.prototypes)
    old = shapes.Plan.from_json({"version": 1, "structs": {}})
    assert old.variables == {} and old.prototypes == {}


def test_polish_takes_a_pointer_cast_through_a_word_as_a_pointer_cast():
    """(T *)(u32)&x (dread.frames writes a frame address inside dread.vartypes' cast) is (T *)&x; an
    integer cast of a pointer keeps its (u32): (u8)(u32)p."""
    tree = cparse.parse(HEAD + """typedef struct
{
    u32 f_0;
} __attribute__((packed, aligned(4))) stack8_t;
extern u32 puts_like(const volatile u8 *);
u32 f(u32 r0)
{
    volatile stack8_t stack8;
    volatile u8 *r3;
    r3 = (volatile u8 *)r0;
    puts_like((const volatile u8 *)(u32)&stack8.f_0);
    return (u8)(u32)r3;
}
""")
    fn = tree.function("f")
    polish.polish(tree, fn)
    text = cparse.unparse(fn, notes=False, style="readable")
    assert "puts_like((const volatile u8 *)&stack8.f_0);" in text and "return (u8)(u32)r3;" in text, text


# --- gizmo ---

def gizmo_plan(variant):
    """(program, context, lifter) of a gizmo build with the readable_types plan dread readable --check
    makes (in memory: no table written, no gate run)."""
    from dread import build, lift, readable, regflow
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir(variant))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = readable.Context(target, image, program, flow)
    lifted = build.read_names(image.table("lifted"))
    digests = {}
    trees = readable.analysis_trees(program, context, lifter, lifted, named=context.globals, digests=digests)
    found, arrays, s = readable.static_plan(program, context, trees, flow)
    context.plan = shapes.plan(s, trees, digests)
    context.plan.statics, context.plan.arrays = found, arrays
    context.plan.params = shapes.pointer_params(s, trees)
    context._struct_names = context._static_names = None
    vartypes.plan(context.plan, trees, digests, context)
    return program, context, lifter


def code(text):
    return re.sub(r"\s*/\*.*?\*/", "", text)


def test_gizmos_char_pointers_are_pointers(variant):
    """gizmo's strncpy, read_line and parse_kv (char pointers the lifted C declared u32): their buffers
    are pointers to bytes, const where only read, written p[k] and *p, advanced by elements; read_line's
    character is s32 (uart_getc's int, compared with 0 signed) and uart_getc's result s32; every gizmo
    build."""
    from dread import readable
    program, context, lifter = gizmo_plan(variant)
    got = {n: code(readable.transform(n, lifter.lift(program.by_name[n]).text, context=context))
           for n in ("strncpy", "read_line", "parse_kv")}
    copy = got["strncpy"]
    assert re.search(r"u32 strncpy\(volatile u8 \*\w+, const volatile u8 \*\w+, u32 \w+\)", copy), copy
    assert "*(volatile u8 *)" not in copy, copy
    line = got["read_line"]
    assert re.search(r"s32 read_line\(volatile u8 \*\w+, u32 \w+\)", line), line
    assert "extern s32 uart_getc(void);" in line and re.search(r"\b\w+ (<|>=) 0\)", line), line
    assert "(s32)" not in line, line
    assert re.search(r"\w+\[\w+\] = 0;", line) and "*(volatile u8 *)" not in line, line
    kv = got["parse_kv"]
    assert re.search(r"u32 parse_kv\(const volatile u8 \*\w+, volatile u8 \*\w+, u32 \w+, u32 \w+\)", kv), kv
    assert "*(volatile u8 *)" not in kv and re.search(r"\w+\[\w+\] = \w+;", kv), kv


# --- a program of our own through the gate ---

SPOOL = """typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;
typedef int s32;
%s
struct spool_cell { u32 count; u16 low; u16 high; u32 total; };
struct spool_cell spool_cells[6];
u16 spool_marks[8];
u8 spool_name[21];
u32 spool_level;
NOIPA void spool_fill(u8 *d, u32 n) { while (n--) *d++ = (u8)(n * 3 + 1); }
NOIPA u32 spool_marks_sum(const u16 *h, u32 n) { u32 s = 0; while (n--) s += *h++; return s; }
NOIPA u32 spool_cells_sum(const struct spool_cell *c, u32 n)
{
    u32 s = 0;
    while (n--) { s += c->count + c->total; c++; }
    return s;
}
NOIPA s32 spool_lowest(const s32 *v, u32 n)
{
    s32 low = 0;
    while (n--) { s32 x = *v++; if (x < low) low = x; }
    return low;
}
NOIPA u32 spool_copy(char *d, const char *s, u32 n)
{
    u32 k = 0;
    while (k < n && s[k]) { d[k] = s[k]; k++; }
    d[k] = 0;
    return k;
}
int main(void)
{
    spool_fill(spool_name, sizeof spool_name);
    spool_level = spool_marks_sum(spool_marks, 8) + spool_cells_sum(spool_cells, 6);
    spool_level += (u32)spool_lowest((const s32 *)spool_marks, 4) + spool_copy((char *)spool_name, "spool", 20);
    return 0;
}
"""


def _kind(kind):
    from test_statics import needs_kind
    return needs_kind(kind)


@pytest.mark.slow
@pytest.mark.parametrize("kind", [pytest.param(k, marks=_kind(k)) for k in ("a32", "m3", "rv32")])
def test_a_program_of_typed_pointers_through_the_gate(kind, tmp_path):
    """spool: a byte walk, a u16 walk (p + 1 is 2 bytes), a walk over an array of 12-byte structures
    (c + 1 is 12 bytes where dread.shapes makes it a structure, RISC-V; else its words, + 3), signed
    values compared, char pointers; built with GCC -Os for ARM state, Thumb-2 and RISC-V. The gate
    (dread readable --check) accepts every function with the types, and the readable C has them."""
    from conftest import NOIPA_DEF
    from test_statics import kind_target
    from dread import build, readable
    from dread.program import Program
    target = kind_target(tmp_path, kind, SPOOL % NOIPA_DEF)
    image = target.image()
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    lifted = sorted(build.read_names(image.table("lifted")))
    assert accepted == lifted, (accepted, lifted, lines)
    plan = shapes.load_plan(image)
    assert plan.variables and plan.prototypes, plan.to_json()
    program = Program(target, image)
    from dread import lift, regflow
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = readable.Context(target, image, program, flow)

    def text(name):
        return code(readable.transform(name, lifter.lift(program.by_name[name]).text, context=context))
    found = {name: text(name) for name in lifted if name.startswith("spool_")}
    assert len(found) == 5, sorted(found)
    t = found["spool_marks_sum"]          # (a u16 pointer advanced by one element: 2 bytes)
    assert "const volatile u16 *" in t and "*(volatile u16 *)" not in t and re.search(r"\+\+|\+ 1\b", t), t
    t = found["spool_fill"]
    assert re.search(r"spool_fill\(volatile u8 \*\w+", t) and "*(volatile u8 *)" not in t, t
    t = found["spool_cells_sum"]          # (12-byte structures: a structure pointer + 1, or words + 3)
    assert re.search(r"volatile struct s_\d+ \*", t) and re.search(r"\+\+|\+ 1\b", t) or \
        re.search(r"const volatile u32 \*\w+", t) and re.search(r"\w+ (\+= 3|= \w+ \+ 3);", t), t
    assert "*(volatile u32 *)" not in t, t
    t = found["spool_lowest"]             # (the values compared signed)
    assert re.search(r"s32 \w+", t.split("{", 1)[1]) and not re.search(r"\(s32\)\w+ <", t), t
    t = found["spool_copy"]
    assert re.search(r"spool_copy\((volatile u8 \*|u32 )\w+, const volatile u8 \*\w+", t), t
    assert "*(volatile u8 *)" not in t, t


# --- random functions, typed and lifted, compiled by the host in 32 bits ---

def _host32():
    """The host's C compiler with -m32 where it builds and runs a 32-bit program, else None."""
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        return None
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        src, exe = os.path.join(d, "p.c"), os.path.join(d, "p")
        with open(src, "w") as f:
            f.write("int main(void) { return sizeof(void *) == 4 ? 0 : 1; }\n")
        try:
            subprocess.run([cc, "-m32", "-o", exe, src], check=True, capture_output=True)
            subprocess.run([exe], check=True, capture_output=True)
        except (subprocess.CalledProcessError, OSError):
            return None
    return cc


HOST32 = _host32()
WIDTHS = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4}


def random_pointer_function(rng, name):
    """A function of the lifted dialect over two pointers into a buffer (a group read at one width with
    copies, derived pointers, advances, indices; a group written at another), a counter compared signed
    that goes below 0 and is read unsigned too (a shift, a division, an unsigned compare), and values:
    its accesses stay inside the 192 bytes from each base address for any arguments."""
    rw = rng.choice(list(WIDTHS))
    ww = rng.choice(["u8", "u16", "u32"])
    a, b = WIDTHS[rw], WIDTHS[ww]
    lines = ["u32 %s(u32 a0, u32 a1, u32 a2, u32 a3)" % name, "{",
             "    u32 p, p1, q, q1, i, n, v, w, t;",
             "    p = a0; q = a1; i = a2 & 0xfu; n = (a3 & 0x7u) - 3; v = 0; w = 0; t = 0;",
             "    p1 = p + %d; q1 = q;" % (a * rng.randrange(4))]
    off_p = 0
    for _ in range(rng.randint(6, 14)):
        k = rng.randrange(12)
        if k == 0:
            lines.append("    v = v + *(volatile %s *)(p + %d);" % (rw, a * rng.randrange(8)))
        elif k == 1:
            idx = "i" if a == 1 else "(i << %d)" % (a.bit_length() - 1)
            lines.append("    v = v ^ *(volatile %s *)(p + %s);" % (rw, idx))
        elif k == 2:
            lines.append("    w = w + *(volatile %s *)p1;" % rw)
        elif k == 3 and off_p < 64:
            step = a * rng.randrange(1, 4)
            off_p += step
            lines.append("    p = p + %d;" % step)
        elif k == 4:
            lines.append("    *(volatile %s *)(q + %d) = v;" % (ww, b * rng.randrange(8)))
        elif k == 5:
            idx = "(i & 7u)" if b == 1 else "((i & 7u) << %d)" % (b.bit_length() - 1)
            lines.append("    *(volatile %s *)(q1 + %s) = w;" % (ww, idx))
        elif k == 6:
            lines.append("    if (p == p1) v = v + 1;")
        elif k == 7:
            lines.append("    if (p < p1) w = w ^ v;")
        elif k == 8:
            lines.append(rng.choice(["    if ((s32)n > 3) n = n - 5; else n = n + 1;",
                                     "    if ((s32)n < 0) w = w + (n >> 1); else n = n - 3;",
                                     "    if ((s32)n >= -2) t = t + n / 3; else n = n + 2;",
                                     "    if ((s32)n < 2) w = w + n % 5;",
                                     "    if (n < 9) w = w ^ n;"]))
        elif k == 9:
            lines.append("    t = t + (p1 - p) + (p & 3u);")
        elif k == 10:
            lines.append("    w = w + sink(p1, (s32)n < 2);")
        else:
            lines.append("    if (p != 0) t = t ^ (v >> 1);")
    lines.append("    return v ^ (w << 1) ^ (t << 2) ^ (p - a0) ^ (n << 7) ^ trace;")
    lines.append("}")
    return "\n".join(lines) + "\n"


@pytest.mark.skipif(HOST32 is None, reason="needs the host's C compiler with -m32")
def test_random_functions_typed_compute_the_same(tmp_path):
    """Random functions of the lifted dialect over pointers (random_pointer_function), typed every way
    the plan may take (each candidate forced) and as the plan takes them, compiled by the host in 32 bits
    (pointers of 32 bits, as on the targets) beside the lifted C and run on a buffer with boundary and
    random arguments: the same results and the same memory."""
    rng = random.Random(41)
    count = 120
    originals, results = [], []
    sink = ("static u32 trace;\nu32 sink(u32 a, u32 b)\n{\n    trace = trace * 33u + a + 5u * b;\n"
            "    return trace ^ 0x5au;\n}\n")
    for i in range(count):
        text = random_pointer_function(rng, "orig_%d" % i)
        originals.append(text)
        tree = cparse.parse("extern u32 sink(u32, u32);\n" + text.replace("orig_", "typed_"))
        fn = tree.function("typed_%d" % i)
        if i % 2:
            facts = vartypes.Facts(fn)
            typing = {}
            for cand in vartypes.candidates(facts, {}):
                if cand.members:
                    typing.update({v: cand.types[-1] for v in cand.members})
        else:
            p = shapes.Plan()
            vartypes.plan(p, [(tree, fn, {})])
            typing = (p.variables.get(fn.name) or {}).get("types", {})
        p = shapes.Plan()
        p.variables = {fn.name: {"digest": None, "types": typing}} if typing else {}
        vartypes.apply(tree, fn, p)
        polish.polish(tree, fn)
        results.append(cparse.unparse(fn, notes=False, style="readable"))
    typed_any = sum(1 for r in results if "volatile u" in r.split("{", 1)[0] or re.search(r"volatile \w+ \*\w", r))
    assert typed_any > count * 0.8, typed_any
    from dread.build import INCLUDE
    calls = "".join("    run((fn_t)%s_%d, %d, \"%s\");\n" % (k, i, i, k)
                    for i in range(count) for k in ("orig", "typed"))
    driver = r"""
#include <stdio.h>
#include <string.h>
typedef u32 (*fn_t)(u32, u32, u32, u32);
static u8 buf[1024];
static u32 got[%(count)d][2][8];
static u8 mem[%(count)d][2][1024];
static const u32 edges[] = {0, 1, 2, 3, 7, 8, 15, 16, 0x7f, 0x80, 0xff, 0x7fffffff, 0x80000000, 0xfffffffe, 0xffffffff};
static void run(fn_t f, int i, const char *kind)
{
    int k = kind[0] == 't', j;
    u32 seed = 12345;
    for (j = 0; j < 8; j++) {
        int b;
        for (b = 0; b < 1024; b++) { seed = seed * 1103515245u + 12345u; buf[b] = (u8)(seed >> 16); }
        trace = 0;
        u32 x = j < 4 ? edges[(i + j * 3) %% 15] : seed;
        u32 y = j < 4 ? edges[(i * 7 + j) %% 15] : seed * 31u;
        got[i][k][j] = f((u32)&buf[256], (u32)&buf[640], x, y) ^ trace;
        if (j == 7) memcpy(mem[i][k], buf, 1024);
    }
}
int main(void)
{
    int i, j, bad = 0;
%(calls)s    for (i = 0; i < %(count)d; i++) {
        for (j = 0; j < 8; j++)
            if (got[i][0][j] != got[i][1][j]) { printf("result %%d %%d\n", i, j); bad++; }
        if (memcmp(mem[i][0], mem[i][1], 1024)) { printf("memory %%d\n", i); bad++; }
    }
    return bad != 0;
}
""" % {"count": count, "calls": calls}
    source = tmp_path / "typed.c"
    source.write_text('#include "dread.h"\n' + sink + "".join(originals) + "\n".join(results) + driver)
    exe = tmp_path / "typed"
    built = subprocess.run([HOST32, "-m32", "-O2", "-std=gnu11", "-w", "-fno-strict-overflow",
                            "-fno-delete-null-pointer-checks", "-I", INCLUDE, "-o", str(exe), str(source)],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr[-3000:]
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    bad = [int(line.split()[1]) for line in r.stdout.splitlines()]
    assert r.returncode == 0, [(originals[i], results[i]) for i in sorted(set(bad))[:2]]
