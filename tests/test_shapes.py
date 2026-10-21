"""dread.shapes: the structures behind pointers. A callee's fields are copied onto its caller's memory
(polymorphic functions: two callers of one helper keep their structures apart); a header in front of
a structure is no member; a list's link points back to its own kind; the source's layouts come from
debug information, a structure a file only declares looked up by name."""
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
