"""dread.frames: the part of a stack frame that frame addresses handed out may reach, as one local
object in readable C. Its accesses become members, the addresses handed out point into it; accesses
that overlap (two types at one offset, a word and its upper half) share a union; a word below that
reaches into the region starts it; a region the function accesses at no constant offset is an array of
bytes. The C is still the dialect dread.cparse reads and compiles on every gizmo build, each access
one of its own width."""
import pytest

from dread import cparse, frames, readable, tidy

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
    assert "stack12.f_0 = r1; stack12.f_8 = r3;" in out and "+ stack12.f_6 +" in out, out     # (no cast: dread.polish)
    assert "sp0" not in out and "frame" not in out, out     # (sp0 - 24 a variable, propagated away)


TWO = FRAME.replace("r2 = (u32)*(volatile u16 *)(sp0 - 6);", "r2 = (u32)*(volatile u16 *)(sp0 - 4);")
OVER = FRAME.replace("r2 = (u32)*(volatile u16 *)(sp0 - 6);", "r2 = (u32)*(volatile u16 *)(sp0 - 10);")


def test_two_types_at_one_offset_share_a_union():
    """A word stored at sp0 - 4 and its lower half read there: one union of a member for each, named by
    their types; the address handed out at sp0 - 8, where nothing is accessed, is the object's."""
    out = readable_c(TWO)
    assert ("typedef struct\n{\n    u32 f_0;\n    u8 pad_4[4];\n    union\n    {\n        u32 f_8_u32;\n"
            "        u16 f_8_u16;\n    } __attribute__((packed));\n} __attribute__((packed, aligned(4))) stack12_t;") \
        in out, out
    assert "stack12.f_0 = r1; stack12.f_8_u32 = r3;" in out and "+ stack12.f_8_u16 +" in out, out
    assert "g(r0, (u32)&stack12.f_0, (u32)&stack12 + 4, " in out, out
    assert cparse.parse(out).function("h") is not None
    _, fn = readable.readable_tree("h", TWO, context=readable.Context())
    assert frames.plan(fn).unions() == 1


def test_overlapping_accesses_share_a_union():
    """A half read at sp0 - 10, inside the word stored at sp0 - 12: a union of the word and a structure
    of bytes up to the half and the half, anonymous, so that each keeps its plain name."""
    out = readable_c(OVER)
    assert ("    union\n    {\n        u32 f_0;\n        struct { u8 pad_f_2[2]; u16 f_2; } __attribute__((packed));\n"
            "    } __attribute__((packed));\n    u8 pad_4[4];\n    u32 f_8;\n} __attribute__((packed, aligned(4))) "
            "stack12_t;") in out, out
    assert "stack12.f_0 = r1;" in out and "(u32)stack12.f_2" in out, out


REACH = """/* h at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 g(u32, u32, u32, u32);
u32 h(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r12 = 0;
    u32 n = 0, z = 0, c = 0, v = 0;
    u32 frame[4] __attribute__((aligned(8)));
    u32 sp0 = (u32)&frame[4];
    (void)n; (void)z; (void)c; (void)v; (void)r12; (void)sp0;
    *(volatile u8 *)(sp0 - 7) = (u8)r2;  /* 0: strb r2, [sp, #9] */
    *(volatile u32 *)(sp0 - 8) = r1;  /* 2: push {r1, lr} */
    *(volatile u16 *)(sp0 - 6) = (u16)r3;  /* 4: strh r3, [sp, #2] */
    r0 = g(r0, sp0 - 8 + 0x2u, r2, r3); r1 = r2 = r3 = r12 = 0;  /* 6: bl g */
    r1 = (u32)*(volatile u8 *)(sp0 - 7);  /* a: ldrb r1, [sp, #1] */
    r2 = (u32)*(volatile u16 *)(sp0 - 6);  /* c: ldrh r2, [sp, #2] */
    r0 = r0 + r1 + r2;  /* e: adds r0, r1 */
    return r0;  /* 10: pop {r1, pc} */
}
"""


def test_a_word_below_that_reaches_in_starts_the_region():
    """GCC -Os pushes a word to make room for a 16-bit local and hands the local's address on
    (FreeRTOS's vPolledQueueProducer): the address leaves at sp0 - 6, the push stores at sp0 - 8. The
    region starts at the push, and its word, the local and a byte between share a union. The byte at
    sp0 - 7 lies below the address that leaves, but the push writes it: no variable of its own (it was
    one before the region started at the push, and read what the strb stored, not the push's byte)."""
    out = readable_c(REACH)
    assert ("    union\n    {\n        u32 f_0;\n        struct { u8 pad_f_1[1]; u8 f_1; } __attribute__((packed));\n"
            "        struct { u8 pad_f_2[2]; u16 f_2; } __attribute__((packed));\n    } __attribute__((packed));\n"
            "    u8 pad_4[4];\n} __attribute__((packed, aligned(8))) stack8_t;") in out, out
    assert "stack8.f_1 = (u8)r2;" in out and "stack8.f_0 = r1;" in out and "g(r0, (u32)&stack8.f_2, " in out, out
    assert "= stack8.f_1;" in out and "(u32)stack8.f_2" in out and "s7" not in out and "sp0" not in out, out
    fn = cparse.parse(REACH).function("h")
    tidy.tidy(fn)
    assert "*(volatile u8 *)(sp0 - 7)" in cparse.unparse(fn, notes=False)


ARRAY = FRAME.replace(
    "    *(volatile u32 *)(sp0 - 12) = r1; *(volatile u32 *)(sp0 - 4) = r3;  /* 0: push {r1, r2, r3} */\n", "") \
    .replace("    r1 = *(volatile u32 *)(sp0 - 12);  /* 8: ldr r1, [sp, #12] */\n", "") \
    .replace("    r2 = (u32)*(volatile u16 *)(sp0 - 6);  /* a: ldrh r2, [sp, #18] */\n",
             "    r2 = (u32)*(volatile u8 *)(sp0 - 12 + r0);  /* a: ldrb r2, [r1, r0] */\n")


def test_a_region_accessed_only_through_addresses_is_an_array_of_bytes():
    """Nothing is accessed at a constant offset in the region, only through the addresses handed out
    and one with an index added (a buffer filled by a callee and read): an array of bytes, aligned as
    the region's start, its addresses an element's, the index added to its address (an index past an
    array is undefined in C; the address's arithmetic stays as the lifted C had it)."""
    out = readable_c(ARRAY)
    assert "    volatile u8 stack12[12] __attribute__((aligned(4)));" in out and "typedef" not in out, out
    assert "g(r0, (u32)stack12, (u32)&stack12[4], " in out, out
    assert "*(volatile u8 *)((u32)stack12 + r0_1)" in out and "sp0" not in out, out
    _, fn = readable.readable_tree("h", ARRAY, context=readable.Context())
    assert frames.plan(fn).array()


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
    variants = (FRAME, index, FRAME.replace("    *(volatile u32 *)(sp0 - 24) = r2;  /* 2: str r2, [sp] */\n", ""),
                TWO, OVER, REACH, ARRAY)
    for k, text in enumerate(variants):
        out = readable_c(text.replace("u32 h(", "u32 h%d(" % k).replace("h at 0x0", "h%d at 0x0" % k), "h%d" % k)
        assert "stack12" in out or "stack8" in out, out
        (src / ("h%d.c" % k)).write_text(out)
    assert build.compile_all(target, image, str(src), str(tmp_path / "obj"), quiet=True,
                             flags=target.compiler.lifted_cflags)
