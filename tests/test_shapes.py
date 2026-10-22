"""dread.shapes: the structures behind pointers. A callee's fields are copied onto its caller's memory
(polymorphic functions: two callers of one helper keep their structures apart); a header in front of
a structure is no member; a list's link points back to its own kind; a structure held inside another
is a member of it (p->f_18.f_10, &p->f_4), unless it overlaps another or two kinds claim its place;
the source's layouts come from debug information, a structure a file only declares looked up by
name."""
import shutil
import subprocess

import pytest

from dread import cparse, shapes

HEAD = "typedef unsigned int u32;\ntypedef unsigned short u16;\ntypedef unsigned char u8;\n"


def analysed(text):
    tree = cparse.parse(HEAD + text)
    fns = [item for item in tree.items if isinstance(item, cparse.Function)]
    return shapes.analyse([(tree, fn, {}) for fn in fns])


def fields(s, name, i=0):
    r = s.find(("param", name, i))
    return shapes.fields_at(s, s.points[r]) if r in s.points else set()


def test_a_callee_s_fields_are_copied_onto_its_caller_s_memory():
    s = analysed("u32 get(u32 r0)\n{\n    return *(volatile u32 *)(r0 + 8);\n}\n"
                 "u32 caller(u32 r0)\n{\n    *(volatile u32 *)(r0 + 4) = 1u;\n    return get(r0);\n}\n")
    assert fields(s, "get") == {(8, 4)}
    assert fields(s, "caller") == {(4, 4), (8, 4)}


def test_two_callers_of_one_helper_keep_their_structures_apart():
    s = analysed("u32 clear(u32 r0)\n{\n    *(volatile u32 *)r0 = 0u;\n    return r0;\n}\n"
                 "u32 a(u32 r0)\n{\n    *(volatile u8 *)(r0 + 12) = 1;\n    clear(r0);\n    return 0;\n}\n"
                 "u32 b(u32 r0)\n{\n    *(volatile u16 *)(r0 + 6) = 2;\n    clear(r0);\n    return 0;\n}\n")
    assert fields(s, "a") == {(0, 4), (12, 1)}
    assert fields(s, "b") == {(0, 4), (6, 2)}           # (unification alone made them one: all three fields)


def test_a_header_in_front_of_a_structure_is_no_member_of_it():
    """vPortFree reads the heap's block header in front of what it frees: real memory, not a member."""
    s = analysed("u32 release(u32 r0)\n{\n    return *(volatile u32 *)(r0 - 4);\n}\n"
                 "u32 user(u32 r0)\n{\n    *(volatile u32 *)r0 = 5u;\n    return release(r0);\n}\n")
    assert fields(s, "user") == {(0, 4)}


def test_a_callee_that_reaches_below_its_pointer_lends_its_caller_nothing():
    """A free with a block header in front (FreeRTOS's vPortFree: its free list's link at -8, the size
    at -4 of what it frees) sees its own structure there, not its caller's: its template is not copied
    onto the caller's memory, nor does the call link the two kinds."""
    text = ("u32 release(u32 r0)\n{\n    u32 r1;\n    r1 = *(volatile u32 *)(r0 - 4);\n"
            "    *(volatile u32 *)(r0 - 4) = r1 & ~1u;\n"
            "    *(volatile u32 *)(r0 - 8) = *(volatile u32 *)0x20000100u;\n"
            "    *(volatile u32 *)0x20000100u = r0 - 8;\n    return 0;\n}\n"
            "u32 user(u32 r0)\n{\n    *(volatile u32 *)r0 = 5u;\n    *(volatile u32 *)(r0 + 4) = 6u;\n"
            "    *(volatile u32 *)(r0 + 8) = 7u;\n    return release(r0);\n}\n")
    s = analysed(text)
    root, off = s.loc(s.points[s.find(("param", "user", 0))])
    assert sorted(o - off for o in s.fields[root]) == [0, 4, 8], sorted(s.fields[root])
    assert not s.calls


def test_a_field_that_points_into_its_own_object_is_no_pointer_field():
    """A queue's pcHead: the queue itself for a semaphore (no storage), its storage behind the
    structure for the others, and pcWriteTo a copy of it. Pointing into its own object at two
    offsets, it is no pointer to a structure (it held the queue's own type before)."""
    text = ("u32 init(u32 r0, u32 r1)\n{\n    if (r1 == 0) *(volatile u32 *)r0 = r0;\n"
            "    else *(volatile u32 *)r0 = r0 + 80;\n"
            "    *(volatile u32 *)(r0 + 4) = *(volatile u32 *)r0;\n    *(volatile u32 *)(r0 + 8) = 1u;\n"
            "    return 0;\n}\n"
            "u32 create(u32 r0, u32 r1)\n{\n    *(volatile u32 *)(r0 + 12) = 0u;\n    return init(r0, r1);\n}\n")
    tree = cparse.parse(HEAD + text)
    trees = [(tree, fn, {}) for fn in tree.items if isinstance(fn, cparse.Function)]
    p = shapes.plan(shapes.analyse(trees), trees)
    assert p.typed.get("init") == {"r0": "s_1"} and p.structs["s_1"][:3] == [(0, "u32"), (4, "u32"), (8, "u32")]
    assert not p.pointers, p.pointers
    only_self = text.replace("    else *(volatile u32 *)r0 = r0 + 80;\n", "")
    tree = cparse.parse(HEAD + only_self)
    trees = [(tree, fn, {}) for fn in tree.items if isinstance(fn, cparse.Function)]
    p = shapes.plan(shapes.analyse(trees), trees)
    assert p.pointers == {"s_1": {0: "s_1", 4: "s_1"}}, p.pointers    # (only itself: a pointer to its kind)


def test_the_results_of_a_function_whose_result_nobody_reads_stay_apart():
    """A function the source declares void hands back what its result register holds on each path
    (vPortFree: its parameter, or the freed block's size): made one result, the size would point to
    the parameter's memory."""
    text = ("u32 drop(u32 r0)\n{\n    if (r0 == 0) return r0;\n    r0 = *(volatile u32 *)(r0 - 4);\n"
            "    return r0;\n}\n")
    tree = cparse.parse(HEAD + text)
    trees = [(tree, tree.function("drop"), {})]
    for unread, points in ((frozenset(), True), (frozenset({"drop"}), False)):
        s = shapes.analyse(trees, unread=unread)
        root, off = s.loc(s.points[s.find(("var", "drop", "r0", None))]) if s.find(
            ("var", "drop", "r0", None)) in s.points else s.loc(s.points[s.find(("param", "drop", 0))])
        size = s.fields[root][off - 4]
        assert (s.find(size.content) in s.points) == points


def test_a_link_points_back_to_its_own_kind_and_reaches_the_caller():
    s = analysed("u32 walk(u32 r0)\n{\n    u32 r3;\n    r3 = r0;\n    while (r3 != 0) r3 = *(volatile u32 *)(r3 + 4);\n"
                 "    return *(volatile u32 *)(r0 + 8);\n}\n"
                 "u32 top(u32 r0)\n{\n    return walk(r0);\n}\n")
    root, off = s.loc(s.points[s.find(("param", "walk", 0))])
    link = s.fields[root][off + 4]
    assert s.loc(s.points[s.find(link.content)]) == (root, off)    # (the next one is the same kind)
    root, off = s.loc(s.points[s.find(("param", "top", 0))])
    link = s.fields[root][off + 4]
    assert s.loc(s.points[s.find(link.content)]) == (root, off)    # (copied with its link)
    assert fields(s, "top") == {(4, 4), (8, 4)}


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_source_s_layouts_come_from_debug_information(tmp_path):
    pytest.importorskip("elftools")
    (tmp_path / "a.c").write_text(
        "struct S { int x; char y; short z; struct { int p; int q; } in; char name[3]; };\n"
        "int use(struct S *s) { return s->x + s->in.q; }\n")
    (tmp_path / "b.c").write_text("struct S;\nint opaque(int k, struct S *s) { return s != 0 && k; }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O1", "-g", "-nostdlib", "-e", "use",
                    "-o", elf, str(tmp_path / "a.c"), str(tmp_path / "b.c")], check=True)
    layouts = shapes.source_layouts(elf)
    found = [v for v in layouts.values() if 0 in v and v[0][0] == "S"]
    leaves = {(0, 4), (4, 1), (6, 2), (8, 4), (12, 4), (16, 1), (17, 1), (18, 1)}
    assert found and found[0][0][1] == leaves, layouts
    opaque = [v for v in layouts.values() if 1 in v]        # (b.c only declares struct S: looked up by name)
    assert opaque and opaque[0][1] == ("S", leaves), layouts
    held = {}                                               # (the structures it holds: in at 8, 8 bytes)
    shapes.source_params(elf, held=held)
    assert sorted(v[0] for v in held.values() if 0 in v) == [{8: 8}], held


GET = """/* get */
#include "dread.h"
u32 get(u32 r0)
{
    return *(volatile u32 *)(r0 + 8) + *(volatile u32 *)(r0 + 4);
}
"""
CALLER = """/* caller */
#include "dread.h"
extern u32 get(u32);
extern u32 caller(u32);
u32 caller(u32 r0)
{
    u32 r2, r3;
    *(volatile u32 *)(r0 + 4) = 1u;
    r3 = *(volatile u16 *)(r0 + 12);
    r2 = (u32)*(volatile s16 *)(r0 + 12);
    if (r0 == 0) return 0;
    if (r3 == 7) return caller(r0);
    return get(r0) + r3 + r2;
}
"""


def typed_plan():
    trees = []
    for text, name in ((GET, "get"), (CALLER, "caller")):
        tree = cparse.parse(text)
        trees.append((tree, tree.function(name), {}))
    return shapes.plan(shapes.analyse(trees), trees)


def test_a_plan_declares_a_structure_for_what_pointers_reach_with_its_fields_at_their_offsets():
    p = typed_plan()
    assert p.structs == {"s_1": [(4, "u32"), (8, "u32"), (12, "u16")]}, p.structs
    assert p.typed == {"caller": {"r0": "s_1"}, "get": {"r0": "s_1"}}, p.typed
    assert shapes.struct_text("s_1", p.structs["s_1"]) == (
        "struct s_1\n{\n    u8 pad_0[4];\n    u32 f_4;\n    u32 f_8;\n    u16 f_c;\n};")
    again = shapes.Plan.from_json(p.to_json())
    assert again.structs == p.structs and again.typed == p.typed


def test_the_rewrite_writes_members_through_a_volatile_struct_pointer():
    p = typed_plan()
    tree = cparse.parse(CALLER)
    fn = tree.function("caller")
    assert shapes.apply_types(tree, fn, p) == ["s_1"]
    out = cparse.unparse(tree, notes=False)
    assert "u32 caller(volatile struct s_1 *r0)" in out, out
    assert "extern u32 caller(volatile struct s_1 *);" in out, out        # (it calls itself)
    assert out.index("struct s_1\n{") < out.index("extern u32 get"), out
    assert "r0->f_4 = 1u;" in out and "r3 = r0->f_c;" in out, out
    assert "r2 = (u32)(s16)r0->f_c;" in out, out           # (a signed read of the field: cast, not zero-extended)
    assert "if (r0 == 0)" in out and "get((u32)r0)" in out, out
    assert "struct s_1\n{" in out, out


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_rewritten_functions_compile_and_keep_their_null_checks(tmp_path):
    """get compiles to the same code; caller compiles (to other code: the integer version computed
    r0 + 4 and r0 + 12 ahead of the loop GCC makes of its call of itself, the typed one addresses
    [r0, #4]: the gate decides on behaviour, not bytes). The file asks for
    -fno-delete-null-pointer-checks: without it GCC drops caller's r0 == 0 once r0 is dereferenced."""
    from conftest import gizmo_dir
    from dread import build, naming
    from dread.target import Target
    target = Target.load(gizmo_dir("m3"))
    image = target.image()
    p = typed_plan()
    tree = cparse.parse(GET)
    before = cparse.unparse(tree, notes=False)
    shapes.apply_types(tree, tree.function("get"), p)
    after = cparse.unparse(tree, notes=False)
    assert "return r0->f_8 + r0->f_4;" in after and after.startswith(build.NULL_CHECKS), after
    assert naming.same_code(target, image, before, after, str(tmp_path / "get"))
    tree = cparse.parse(CALLER)
    shapes.apply_types(tree, tree.function("caller"), p)
    src = tmp_path / "caller"
    src.mkdir()
    (src / "caller.c").write_text(cparse.unparse(tree, notes=False))
    assert "-fno-delete-null-pointer-checks" in build.file_flags(target, str(src / "caller.c"))
    assert build.compile_all(target, image, str(src), str(tmp_path / "obj"), quiet=True,
                             flags=target.compiler.generated_cflags())


def plan_of(*texts):
    trees = []
    for text in texts:
        tree = cparse.parse(HEAD + text)
        trees += [(tree, item, {}) for item in tree.items if isinstance(item, cparse.Function)]
    return shapes.plan(shapes.analyse(trees), trees)


TWO = ("u32 t(u32 r0)\n{\n    return *(volatile u32 *)(r0 + 4) + *(volatile u32 *)(r0 + 8);\n}\n"
       "u32 u(u32 r0)\n{\n    return *(volatile u16 *)(r0 + 2) + *(volatile u16 *)(r0 + 6);\n}\n")


def test_what_touches_one_field_or_is_null_makes_no_two_structures_one():
    """memset, memcpy and strcmp touch offset 0 of whatever they get, and FreeRTOS hands NULL for the
    current task to a dozen functions: neither may make the structures given to them one kind."""
    p = plan_of(TWO, "u32 clear(u32 r0)\n{\n    *(volatile u8 *)r0 = 0;\n    return r0;\n}\n"
                "u32 a(u32 r0)\n{\n    clear(r0);\n    return t(r0) + *(volatile u32 *)(r0 + 4);\n}\n"
                "u32 b(u32 r0)\n{\n    clear(r0);\n    return u(r0) + *(volatile u16 *)(r0 + 2);\n}\n"
                "u32 n(u32 r0)\n{\n    return t(0) + u(0);\n}\n")
    assert p.typed["t"]["r0"] == p.typed["a"]["r0"] and p.typed["u"]["r0"] == p.typed["b"]["r0"], p.typed
    assert p.typed["t"]["r0"] != p.typed["u"]["r0"], p.typed
    assert "n" not in p.typed                       # (it dereferences nothing: no pointers)


def test_a_stack_slot_given_as_two_structures_makes_no_kind():
    """GCC gives locals whose lives do not overlap one stack slot: FreeRTOS's
    prvStaticallyAllocatedCreator handed one as a static queue and later as a static event group."""
    p = plan_of(TWO, "u32 creator(u32 r0)\n{\n    u32 sp0, r4;\n    *(volatile u32 *)(sp0 - 4) = 0u;\n"
                "    r4 = sp0 - 64;\n    t(r4);\n    return u(sp0 - 64);\n}\n")
    assert p.typed["t"]["r0"] != p.typed["u"]["r0"], p.typed


def test_an_offset_added_as_a_large_constant_is_negative():
    """+ 0xffffffffu is - 1 in 32 bits (printi's buffer walk declared a structure with a pad of 4 GB)."""
    assert shapes.signed32(0xffffffff) == -1 and shapes.signed32(8) == 8
    s = analysed("u32 back(u32 r0)\n{\n    return *(volatile u8 *)(r0 + 0xffffffffu) + *(volatile u8 *)(r0 + 4);\n}\n")
    assert fields(s, "back") == {(4, 1)}                # (-1 is in front of the pointer: no member)


def test_only_what_a_function_dereferences_and_declares_u32_becomes_a_pointer():
    """A u64 the carry arithmetic holds stays a u64 (cut to a pointer it lost its carry: clang -O2's
    prvAddCurrentTaskToDelayedList), a value only stored or handed on stays an integer."""
    p = plan_of(TWO, "u32 f(u32 r0)\n{\n    u32 r3, r4;\n    u64 t_7;\n    r3 = r0;\n    r4 = *(volatile u32 *)(r3 + 4);\n"
                "    t_7 = (u64)r4 + r0;\n    return t(r0) + (u32)t_7;\n}\n")
    assert set(p.typed.get("f", {})) == {"r3"}, p.typed


def test_a_variable_given_a_c_variable_s_address_stays_an_integer():
    """a_1 = (u32)&s1: the lifter reads a stack argument and the next one through it; as a pointer to
    a structure, clang -O2 took the second read as outside s1 (xTaskCreateStatic read unmapped memory)."""
    p = plan_of(TWO, "u32 g(u32 r0, u32 r1, u32 r2, u32 r3, u32 s0, u32 s1)\n{\n    u32 a_1, r6;\n"
                "    a_1 = (u32)&s1;\n    r6 = *(volatile u32 *)a_1 + *(volatile u32 *)(a_1 + 4);\n"
                "    return t(a_1) + r6;\n}\n")
    assert "a_1" not in p.typed.get("g", {}), p.typed


def test_a_variable_the_c_keeps_whole_is_as_many_values_as_it_holds():
    """RISC-V's xQueueGenericReset: a0 is the queue, then a0 + 16 its first list, kept as one variable
    (an asm block writes a0); the analysis follows definitions, so the list function's structure does
    not take the queue's fields, and a0, two structures' bases, stays an integer."""
    p = plan_of("u32 init(u32 a0)\n{\n    *(volatile u32 *)a0 = 0u;\n    *(volatile u32 *)(a0 + 4) = a0;\n"
                "    *(volatile u32 *)(a0 + 8) = a0;\n    return a0;\n}\n"
                "u32 reset(u32 a0)\n{\n    u32 s0;\n    s0 = a0;\n    *(volatile u32 *)(a0 + 60) = 0u;\n"
                "    *(volatile u32 *)(a0 + 64) = 0u;\n    a0 = a0 + 16u;\n    init(a0);\n    init(s0 + 36u);\n"
                "    return 0;\n}\n")
    assert p.structs[p.typed["init"]["a0"]] == [(0, "u32"), (4, "u32"), (8, "u32")], p.structs
    assert "a0" not in p.typed.get("reset", {}), p.typed


LINK = ("u32 touch(u32 r0)\n{\n    *(volatile u32 *)(r0 + 12) = 1u;\n"
        "    return *(volatile u32 *)(r0 + 4) + *(volatile u32 *)(r0 + 8);\n}\n"
        "u32 link(u32 r0, u32 r1, u32 r2)\n{\n    *(volatile u32 *)(r0 + 4) = r1;\n    *(volatile u32 *)(r1 + 8) = r0;\n"
        "    *(volatile u32 *)(r1 + 4) = r2 + 0u;\n    *(volatile u32 *)(r0 + 8) = 0u;\n"
        "    touch(r0);\n    touch(r1);\n    return 0;\n}\n"
        "u32 follow(u32 r0)\n{\n    u32 r3;\n    r3 = *(volatile u32 *)(r0 + 4);\n    if (r3 == r0) return 0;\n"
        "    if (*(volatile u32 *)(r3 + 8) != r0) return 3;\n"
        "    *(volatile u32 *)(r3 + 12) = 2u;\n    touch(r3);\n    return touch(r0) + *(volatile u32 *)(r0 + 4);\n}\n")


def test_a_field_that_points_to_a_structure_is_declared_a_pointer_to_it():
    """A list item's links: every object of the kind that points somewhere from the field points to
    the base of one structure, so the field is volatile struct s_N *. A load of it into a variable of
    that structure needs no cast, nor a comparison of two such pointers or a store of one; an integer
    stored into it is cast, a use of it as a number is cast to u32."""
    p = plan_of(LINK)
    assert p.structs == {"s_1": [(4, "u32"), (8, "u32"), (12, "u32")]} and p.pointers == {"s_1": {4: "s_1", 8: "s_1"}}
    assert shapes.Plan.from_json(p.to_json()).pointers == p.pointers
    assert "    volatile struct s_1 *f_4;\n    volatile struct s_1 *f_8;\n    u32 f_c;" in shapes.struct_text(
        "s_1", p.structs["s_1"], pointers=p.pointers["s_1"])
    tree = cparse.parse(HEAD + LINK)
    shapes.apply_types(tree, tree.function("follow"), p)
    shapes.apply_types(tree, tree.function("link"), p)
    out = cparse.unparse(tree, notes=False)
    assert "struct s_1;" in out and out.index("struct s_1;") < out.index("struct s_1\n{"), out
    assert "r3 = r0->f_4;" in out and "if (r3 == r0) return 0;" in out and "if (r3->f_8 != r0) return 3;" in out, out
    assert "return touch((u32)r0) + (u32)r0->f_4;" in out, out
    assert "r0->f_4 = r1;" in out and "r1->f_8 = r0;" in out and "r0->f_8 = 0u;" in out, out
    assert "r1->f_4 = (volatile struct s_1 *)(r2 + 0u);" in out, out


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_pointer_fields_compile(tmp_path):
    """Two functions typed in one file define each structure once: C17 forbids a second definition
    (GCC 15 and later default to C23, which allows an identical one, and CI's GCC 10 and 13 failed);
    DREAD compiles its C to gnu11 ([compiler] generated_std), which forbids it everywhere."""
    from conftest import gizmo_dir
    from dread import build
    from dread.target import Target
    target = Target.load(gizmo_dir("m3"))
    image = target.image()
    p = plan_of(LINK)
    tree = cparse.parse('#include "dread.h"\n' + LINK.replace("u32 link(", "u32 touch(u32);\nu32 link("))
    for name in ("follow", "link"):
        shapes.apply_types(tree, tree.function(name), p)
    src = tmp_path / "linked"
    src.mkdir()
    text = cparse.unparse(tree, notes=False)
    assert text.count("struct s_1\n{") == 1 and text.count("struct s_1;") == 1, text
    (src / "linked.c").write_text(text)
    assert build.compile_all(target, image, str(src), str(tmp_path / "obj"), quiet=True,
                             flags=target.compiler.generated_cflags())


HELD = ("u32 unlink(u32 r0)\n{\n    u32 r1, r2;\n    r1 = *(volatile u32 *)(r0 + 4);\n"
        "    r2 = *(volatile u32 *)(r0 + 8);\n    *(volatile u32 *)(r1 + 8) = r2;\n    *(volatile u32 *)(r2 + 4) = r1;\n"
        "    *(volatile u32 *)(r0 + 16) = 0u;\n    return *(volatile u32 *)r0;\n}\n"
        "u32 drop(u32 r0)\n{\n    u32 r5;\n    r5 = r0 + 4u;\n    unlink(r5);\n    *(volatile u32 *)(r0 + 16) = r0;\n"
        "    if (*(volatile u32 *)(r0 + 40) != 0) unlink(r0 + 24u);\n"
        "    return *(volatile u32 *)(r5 + 8) + *(volatile u32 *)(r0 + 44);\n}\n")


def test_a_structure_held_inside_another_is_a_member_of_it():
    """A task block holds two list items (at 4 and 24): an item at 4 handed to the list function
    through a variable (r5 = r0 + 4, met after the call gave r5 an object of its own) and one at 24
    handed directly. The block's structure declares them as members of the item's structure; what the
    block accesses inside one (its owner at 12 of the first) becomes the item's field, and the block
    keeps what lies outside them."""
    p = plan_of(HELD)
    assert p.embeds == {"s_1": {4: "s_2", 24: "s_2"}}, p.embeds
    assert p.structs["s_1"] == [(44, "u32")], p.structs
    assert p.structs["s_2"] == [(0, "u32"), (4, "u32"), (8, "u32"), (12, "u32"), (16, "u32")], p.structs
    assert p.typed["drop"] == {"r0": "s_1", "r5": "s_2"} and p.typed["unlink"] == {"r0": "s_2"}, p.typed
    assert p.size("s_2") == 20 and p.size("s_1") == 48 and p.align("s_1") == 4
    assert p.leaves("s_1")[:2] == [(4, "u32"), (8, "u32")] and (40, "u32") in p.leaves("s_1")
    assert p.place("s_1", 40) == ([("s_1", 24)], "s_2", 16) and p.place("s_1", 44) == ([], "s_1", 44)
    assert shapes.held_offsets(p, "s_1") == [4, 24]
    again = shapes.Plan.from_json(p.to_json())
    assert again.embeds == p.embeds and again.structs == p.structs
    data = p.to_json()
    data["embeds"]["s_1"]["8"] = "s_9"                  # (a member of a structure the table lacks: none)
    assert shapes.Plan.from_json(data).embeds == p.embeds
    assert shapes.struct_text("s_1", p.structs["s_1"], embeds={4: ("s_2", 20), 24: ("s_2", 20)}) == (
        "struct s_1\n{\n    u8 pad_0[4];\n    struct s_2 f_4;\n    struct s_2 f_18;\n    u32 f_2c;\n};")


def test_accesses_inside_a_held_structure_are_its_members_and_its_address_is_taken_as_such():
    """p->f_18.f_10 for the second item's container, &p->f_18 for its address handed on, r5 = &p->f_4
    for a variable that points to the first item; the item's structure defined before the block's."""
    p = plan_of(HELD)
    tree = cparse.parse(HEAD + HELD)
    assert shapes.apply_types(tree, tree.function("drop"), p) == ["s_1", "s_2"]
    out = cparse.unparse(tree, notes=False)
    assert out.index("struct s_2\n{") < out.index("struct s_1\n{"), out
    assert "    struct s_2 f_4;\n    struct s_2 f_18;\n" in out, out
    body = out[out.index("u32 drop("):]
    assert body == ("u32 drop(volatile struct s_1 *r0)\n{\n    volatile struct s_2 *r5;\n    r5 = &r0->f_4;\n"
                    "    unlink((u32)r5);\n    r0->f_4.f_c = (u32)r0;\n"
                    "    if (r0->f_18.f_10 != 0) unlink((u32)&r0->f_18);\n"
                    "    return r5->f_8 + r0->f_2c;\n}\n"), body


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_held_structures_compile_to_the_same_code(tmp_path):
    """Every access through a member of a held structure is the volatile access of the same width at
    the same address, and &p->f_4 the address p + 4: the typed function compiles to the integer
    one's code."""
    from conftest import gizmo_dir
    from dread import naming
    from dread.target import Target
    target = Target.load(gizmo_dir("m3"))
    p = plan_of(HELD)
    text = '#include "dread.h"\n' + HELD.replace("u32 drop(", "extern u32 unlink(u32);\nu32 drop(")
    tree = cparse.parse(text)
    tree.items = [i for i in tree.items if not (isinstance(i, cparse.Function) and i.name == "unlink")]
    before = cparse.unparse(tree, notes=False)
    shapes.apply_types(tree, tree.function("drop"), p)
    after = cparse.unparse(tree, notes=False)
    assert "r0->f_18.f_10" in after and "&r0->f_4" in after, after
    assert naming.same_code(target, target.image(), before, after, str(tmp_path / "drop"))


def test_a_member_that_overlaps_another_or_that_two_kinds_claim_is_left_out():
    """Two items handed at 4 and at 8 would overlap: the first is the member, what the second covered
    beyond it stays the block's. Two kinds that cannot be one (words, halfwords) at one place: no
    member there, the fields stay the block's."""
    two = ("u32 a(u32 r0)\n{\n    return *(volatile u32 *)r0 + *(volatile u32 *)(r0 + 4);\n}\n"
           "u32 b(u32 r0)\n{\n    return *(volatile u16 *)r0 + *(volatile u16 *)(r0 + 2);\n}\n")
    p = plan_of(two, "u32 o(u32 r0)\n{\n    *(volatile u32 *)(r0 + 12) = 1u;\n    a(r0 + 4u);\n"
                "    return a(r0 + 8u) + *(volatile u32 *)r0;\n}\n")
    outer = p.typed["o"]["r0"]
    assert p.embeds == {outer: {4: p.typed["a"]["r0"]}}, p.embeds
    assert p.structs[outer] == [(0, "u32"), (12, "u32")], p.structs
    # (handed through a variable, r5 = r0 + 4 met late: the blocks keep no fields of what they hold,
    # so the two blocks are one kind, which holds a's at 4 for o and b's at 4 for q)
    p = plan_of(two, "u32 o(u32 r0)\n{\n    u32 r5;\n    r5 = r0 + 4u;\n    a(r5);\n"
                "    return *(volatile u32 *)r0 + *(volatile u32 *)(r0 + 16);\n}\n"
                "u32 q(u32 r0)\n{\n    u32 r5;\n    r5 = r0 + 4u;\n    b(r5);\n    *(volatile u32 *)(r0 + 16) = 2u;\n"
                "    return o(r0);\n}\n")
    outer = p.typed["o"]["r0"]
    assert p.typed["q"]["r0"] == outer and p.structs[outer] == [(0, "u32"), (16, "u32")], (p.typed, p.structs)
    assert not p.embeds, p.embeds


def test_a_held_structure_ends_where_its_holder_s_fields_reach():
    """FreeRTOS keeps a list's end marker, a 12-byte mini item, at 8 and hands it on as a 20-byte item:
    held there, the list would grow to 28 bytes and a queue's two lists 20 bytes apart could not both
    be members. The list holds no item; its links still point to items, the end marker among them."""
    p = plan_of(HELD.split("u32 drop(")[0],
                "u32 init(u32 r0)\n{\n    u32 r3;\n    r3 = r0 + 8u;\n    unlink(r3);\n    *(volatile u32 *)r0 = 0u;\n"
                "    *(volatile u32 *)(r0 + 4) = r3;\n    *(volatile u32 *)(r0 + 8) = 0xffffffffu;\n"
                "    *(volatile u32 *)(r0 + 12) = r3;\n    *(volatile u32 *)(r0 + 16) = r3;\n    return 0;\n}\n"
                "u32 make(u32 r0)\n{\n    *(volatile u32 *)(r0 + 56) = 0u;\n    init(r0 + 16u);\n"
                "    return init(r0 + 36u);\n}\n")
    item, lst, queue = p.typed["unlink"]["r0"], p.typed["init"]["r0"], p.typed["make"]["r0"]
    assert p.embeds == {queue: {16: lst, 36: lst}}, p.embeds
    assert p.structs[lst] == [(0, "u32"), (4, "u32"), (8, "u32"), (12, "u32"), (16, "u32")] and p.size(lst) == 20
    assert p.pointers[lst] == {4: item, 12: item, 16: item}, p.pointers
