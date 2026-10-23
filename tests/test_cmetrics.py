"""dread.cmetrics: the figures of readable C and of its source, counted alike on both sides (a function
written by hand whose statements, depth, gotos and labels are known, read by dread.cparse and by
clang); what readable C alone is measured for (casts, raw accesses, members, typed variables); which
source function DWARF says each out-of-line function is."""
import os
import shutil

import pytest

from dread import cmetrics, cparse
from conftest import gizmo_dir

needs_clang = pytest.mark.skipif(not shutil.which("clang"), reason="needs clang")

# 20 statements: the initialised declaration (the other one is none), each if, while, switch and for
# once, the assignments, both breaks, both gotos and the return; depth 3: the while in the else if's
# arm (an else if is no deeper than its if), the if in the while; do { } while (0) is a block, so two
# loops (the while and the for).
KNOWN = """int known(int a, int b)
{
    int x = a + 1;
    int y;
    if (a > b)
        x = 2;
    else if (a < 0)
    {
        x = 3;
        while (x < 10)
        {
            x = x + 1;
            if (x == 5)
                break;
        }
    }
    else
        x = 4;
    do
    {
        y = x;
    } while (0);
    switch (y)
    {
    case 1:
        y = 2;
        break;
    default:
        goto out;
    }
    for (;;)
    {
        y = y - 1;
        if (y < 0)
            goto out;
    }
out:
    return y;
}
"""
KNOWN_FIGURES = {"statements": 20, "depth": 3, "gotos": 2, "labels": 1, "loops": 2}


def test_a_function_written_by_hand_counts_as_known():
    fn = cparse.parse(KNOWN).function("known")
    assert cmetrics.count(cmetrics.skeleton(fn.body.body)) == KNOWN_FIGURES


@needs_clang
def test_the_source_side_counts_the_same_function_alike(tmp_path):
    """clang's tree of the same text gives the same figures (one definition on both sides); a static
    local is no statement, a GNU statement expression's statements count where it stands, a nested
    else that holds more than an if is a level deeper."""
    path = tmp_path / "known.i"
    path.write_text(KNOWN + """
int extras(int a)
{
    static int n = 0;
    int r = ({ int t = a; t + 1; });
    if (a)
        r = 1;
    else
    {
        if (r)
            r = 2;
        r = 3;
    }
    return r + n;
}
""")
    ast, errors = cmetrics.clang_ast(str(path), [])
    assert errors == []
    got = cmetrics.source_functions(ast)
    assert got["known"] == KNOWN_FIGURES
    assert got["extras"] == {"statements": 9, "depth": 2, "gotos": 0, "labels": 0, "loops": 0}, got["extras"]


def readable_file():
    """A function of readable C as dread.readable leaves it: a frame object (its typedef), a structure
    (inserted as the stages insert it: dread.cparse reads no structure definitions), a device
    register's name."""
    tree = cparse.parse("""#include "dread.h"
typedef struct
{
    u32 f_0;
    u8 pad_4[4];
} __attribute__((packed, aligned(4))) stack8_t;
#define io_40000000 (*(volatile u32 *)0x40000000u)
u32 g(u32 r0, volatile struct s_1 *r1)
{
    u32 r2, r3;
    volatile stack8_t stack8;
    r2 = *(volatile u32 *)(r0 + 4);
    r3 = *(volatile u32 *)(sp0 - 8);
    stack8.f_0 = r1->f_4;
    io_40000000 = (u32)r1 + 1;
    if (r2 == 0) goto L_1;
    return r2 + (u8)r3;
L_1: ;
    return 0;
}
""")
    tree.items.insert(1, cparse.Raw("struct s_1\n{\n    u32 f_0;\n    u32 f_4;\n};"))
    return tree


def test_what_readable_c_alone_is_measured_for():
    """Casts but those of raw accesses, raw accesses through pointer casts and those through the
    frame's base, members of structures behind pointers and of the frame's object, fixed addresses by
    name, variables (parameters too) and the typed ones."""
    tree = readable_file()
    assert cmetrics.readable_figures(tree, tree.function("g")) == {
        "statements": 8, "depth": 1, "gotos": 1, "labels": 1, "loops": 0, "casts": 2, "raw": 2, "frame_raw": 1, "members": 1,
        "frame_members": 1, "fixed": 1, "fixed_members": 0, "variables": 5, "typed": 2, "fixed_indexed": 0,
        "fixed_addresses": 0, "pointers": 0, "signed": 0}


def test_pointers_and_signed_variables_are_counted_apart_from_structures():
    """Variables and parameters declared pointers to bytes, words (dread.vartypes) count as pointers, a
    structure pointer as typed, s32 and s16 ones as signed (an s32 pointer is a pointer)."""
    tree = cparse.parse("""#include "dread.h"
u32 k(const volatile u8 *r0, s32 r1, volatile struct s_1 *r2)
{
    volatile u32 *r3, *r4;
    s16 r5;
    const volatile s32 *r6;
    u32 r7;
    r3 = (volatile u32 *)r0;
    r4 = r3 + 1;
    r6 = (const volatile s32 *)r4;
    r5 = (s16)*r6;
    r7 = *r4 + r5 + r1;
    return r7 + r2->f_0;
}
""")
    got = cmetrics.readable_figures(tree, tree.function("k"))
    assert (got["pointers"], got["signed"], got["typed"], got["variables"]) == (4, 2, 1, 8), got


def test_a_member_of_a_held_structure_is_one_member():
    """p->f_4.f_8 (a field of the structure held at 4) is one member access, &p->f_4 one member."""
    tree = cparse.parse("""#include "dread.h"
u32 h(volatile struct s_1 *r0)
{
    r0->f_4.f_8 = (u32)&r0->f_4;
    return r0->f_0;
}
""")
    got = cmetrics.readable_figures(tree, tree.function("h"))
    assert (got["members"], got["frame_members"], got["casts"]) == (3, 0, 1), got


def test_quartiles_and_median():
    assert cmetrics.median([3, 1, 2]) == 2 and cmetrics.median([4, 1, 2, 3]) == 2.5 and cmetrics.median([]) is None
    assert cmetrics.quartiles([1, 2, 3, 4, 5, 6, 7, 8]) == (2.5, 4.5, 6.5)
    assert cmetrics.quartiles([5]) == (5, 5, 5) and cmetrics.quartiles([]) is None


def test_dwarf_names_each_out_of_line_function_and_its_unit():
    """gizmo-m3's ELF: every function with code has its source function's name and translation unit
    (the path as compiled), and what was inlined into it; a clone (GCC's zero_words.constprop.0,
    zero_words_constprop_0 in DREAD's table) has its source function's name."""
    import re
    pytest.importorskip("elftools")
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir("m3"))
    found = cmetrics.out_of_line(os.path.join(gizmo_dir("m3"), "original", "gizmo.elf"))
    program = Program(target)
    named = {fn.entry: fn.name for fn in program.functions}
    assert len(found) >= 40
    clones = 0
    for entry, d in found.items():
        assert d["cu"] in ("start_m3.c", "lib.c", "util.c", "hw.c", "main.c"), d
        assert all(isinstance(n, str) for n in d["inlined"])
        if named.get(entry) != d["name"]:
            assert re.fullmatch(re.escape(d["name"]) + r"_(constprop|isra|part)_\d+", named.get(entry, "")), (
                hex(entry), d)
            clones += 1
    assert clones                           # (the committed ELF has one: zero_words)
