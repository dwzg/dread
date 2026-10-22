"""dread.frames: the part of a stack frame that frame addresses handed out may reach, as one local
structure in readable C. Its accesses become members, the addresses handed out point into it; a region
with two types at one offset or overlapping accesses stays as it was; the C is still the dialect
dread.cparse reads and compiles on every gizmo build."""
import pytest

from dread import cparse, frames, readable

FRAME = """/* h at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 g(u32, u32, u32, u32);
u32 h(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r12 = 0;
    u32 n = 0, z = 0, c = 0, v = 0;
    u32 frame[6] __attribute__((aligned(8)));
    u32 sp0 = (u32)&frame[6];
    (void)n; (void)z; (void)c; (void)v; (void)r12; (void)sp0;
    *(volatile u32 *)(sp0 - 12) = r1; *(volatile u32 *)(sp0 - 4) = r3;  /* 0: push {r1, r2, r3} */
    *(volatile u32 *)(sp0 - 24) = r2;  /* 2: str r2, [sp] */
    r0 = g(r0, sp0 - 16 + 0x4u, sp0 - 16 + 0x8u, r3); r1 = r2 = r3 = r12 = 0;  /* 4: bl g */
    r1 = *(volatile u32 *)(sp0 - 12);  /* 8: ldr r1, [sp, #12] */
    r2 = (u32)*(volatile u16 *)(sp0 - 6);  /* a: ldrh r2, [sp, #18] */
    r0 = r0 + r1 + r2 + *(volatile u32 *)(sp0 - 24);  /* c: adds r0, r1 */
    return r0;  /* e: pop {r1, r2, r3, pc} */
}
"""


def readable_c(text, name="h"):
    return readable.transform(name, text, context=readable.Context())     # (a new context: no memo)


def test_the_region_an_address_leaves_to_is_one_object():
    """The lowest address handed out is sp0 - 12: from there up to sp0 the frame is one object, its
    accesses members, the addresses handed out a field's or the object's own (where no field starts);
    the slot below (sp0 - 24) is a variable as before, and the frame array is gone."""
    out = readable_c(FRAME)
    assert ("typedef struct\n{\n    u32 f_0;\n    u8 pad_4[2];\n    u16 f_6;\n    u32 f_8;\n} "
            "__attribute__((packed, aligned(4))) stack12_t;") in out, out
    assert "    volatile stack12_t stack12;" in out, out
    assert cparse.parse(out).function("h") is not None      # (still the dialect: the typedef names a type)
    assert "g(r0, (u32)&stack12.f_0, (u32)&stack12 + 4, " in out, out
    assert "stack12.f_0 = r1; stack12.f_8 = r3;" in out and "(u32)stack12.f_6" in out, out
    assert "sp0" not in out and "frame" not in out, out     # (sp0 - 24 a variable, propagated away)


def test_a_region_with_two_types_at_one_offset_or_overlapping_accesses_stays_as_it_was():
    two = FRAME.replace("r2 = (u32)*(volatile u16 *)(sp0 - 6);", "r2 = (u32)*(volatile u16 *)(sp0 - 4);")
    over = FRAME.replace("r2 = (u32)*(volatile u16 *)(sp0 - 6);", "r2 = (u32)*(volatile u16 *)(sp0 - 10);")
    for text in (two, over):
        out = readable_c(text)
        assert "stack12" not in out and "*(volatile u32 *)(sp0 - 12) = r1;" in out and "sp0 - 16 + 0x4u" in out, out
    _, fn = readable.readable_tree("h", two, context=readable.Context())
    assert frames.plan(fn) == "two types at one offset in the region"
    _, fn = readable.readable_tree("h", over, context=readable.Context())
    assert frames.plan(fn) == "overlapping accesses in the region"


def test_an_index_into_the_region_is_an_address_in_the_object():
    """A frame address with an index added (a buffer walked) is the object's address plus the index,
    not a field's, also where a field starts there; a raw slot below the region that the clean-up left
    keeps the frame array."""
    text = FRAME.replace("r0 = r0 + r1 + r2 + *(volatile u32 *)(sp0 - 24);",
                         "r0 = r0 + r1 + r2 + *(volatile u8 *)(sp0 - 12 + r2) + *(volatile u32 *)(sp0 - 24);")
    out = readable_c(text)
    assert "*(volatile u8 *)((u32)&stack12 + r2" in out, out
    below = FRAME.replace("    *(volatile u32 *)(sp0 - 24) = r2;  /* 2: str r2, [sp] */\n", "")
    out = readable_c(below)                 # (sp0 - 24 is read before anything writes it: it stays raw)
    assert "*(volatile u32 *)(sp0 - 24)" in out and "u32 frame[6]" in out and "stack12.f_0" in out, out


def test_a_wrapped_function_s_real_stack_stays_as_it_is():
    """A wrapped function's sp0 is the original's stack pointer, a parameter: what lies above it (the
    stack arguments) is the caller's, and an address handed out may reach it."""
    text = ("#include \"dread.h\"\nextern u32 g(u32);\nu32 h_dread_body(u32 a0, u32 a1, u32 a2, u32 a3, u32 sp0)\n"
            "{\n    *(volatile u32 *)(sp0 - 8) = a1;\n    return g(sp0 - 8);\n}\n")
    tree = cparse.parse(text)
    fn = tree.function("h_dread_body")
    assert frames.plan(fn) == "no frame array with sp0 at its end"
    assert frames.frame_struct(tree, fn) is None


@pytest.mark.compiles
def test_the_object_compiles(variant, tmp_path):
    from conftest import gizmo_dir
    from dread import build
    from dread.target import Target
    target = Target.load(gizmo_dir(variant))
    image = target.image()
    src = tmp_path / "frames"
    src.mkdir()
    index = FRAME.replace("r0 = r0 + r1 + r2 + *(volatile u32 *)(sp0 - 24);",
                          "r0 = r0 + r1 + r2 + *(volatile u8 *)(sp0 - 12 + r2) + *(volatile u32 *)(sp0 - 24);")
    for k, text in enumerate((FRAME, index, FRAME.replace("    *(volatile u32 *)(sp0 - 24) = r2;  /* 2: str r2, [sp] */\n",
                                                           ""))):
        out = readable_c(text.replace("u32 h(", "u32 h%d(" % k).replace("h at 0x0", "h%d at 0x0" % k), "h%d" % k)
        assert "stack12" in out, out
        (src / ("h%d.c" % k)).write_text(out)
    assert build.compile_all(target, image, str(src), str(tmp_path / "obj"), quiet=True,
                             flags=target.compiler.lifted_cflags)
