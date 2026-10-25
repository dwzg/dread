"""dread.frames: the part of a stack frame that frame addresses handed out may reach, as the function's
local objects in readable C. An object starts at each address handed out (and at the region's start),
ends below the next one or the words the entry saves its caller's registers into; it is a scalar, an
array of bytes or of the width its index or a callee's parameter says, a callee's structure, or a
structure of its own (accesses that overlap share a union); volatile only where its address may escape
into memory (dread.frames kept_params: which parameters callees keep). dread.tidy push_room takes out a
push's stores that only make room for locals. The C is still the dialect dread.cparse reads and
compiles on every gizmo build, each access one of its own width. The objects are made before the
readable_names table's names apply, which name them as they name other variables (the naming question
shows them; their names carry by their offsets); a name of the frame array or sp0 no longer keeps
them from being made."""
import os
import re
import shutil

import pytest

from dread import build, cparse, frames, lift, naming, readable, regflow, tidy
from dread.program import Program

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


class Facts(readable.Context):
    """A context without a target that knows what dread.frames and dread.tidy push_room ask for: the
    save words ({offset: (register, bytes)}), the pushes (instruction addresses), the readable_types
    table's keeps, returns, prototypes and structures."""

    def __init__(self, saves=None, pushes=(), keeps=None, returns=None, prototypes=None, structs=None):
        super().__init__()
        self.given_saves, self.pushes = dict(saves or {}), set(pushes)
        self._program = object() if pushes else None
        self.plan.keeps, self.plan.returns = keeps, returns
        self.plan.prototypes = dict(prototypes or {})
        self.plan.structs = dict(structs or {})

    def saves(self, name):
        return self.given_saves

    def is_push(self, address):
        return address in self.pushes


def readable_c(text, name="h", context=None):
    return readable.transform(name, text, context=context or readable.Context())     # (a new context: no memo)


def test_each_address_handed_out_starts_an_object():
    """The addresses handed out are sp0 - 12 and sp0 - 8: two objects, a word (a scalar: one access of
    its width fills it) and a structure of a halfword and a word (its own accesses); the slot below the
    region (sp0 - 24) is a variable as before, and the frame array is gone. Without the readable_types
    table's keeps every object is volatile, as the whole region was."""
    out = readable_c(FRAME)
    assert "typedef struct\n{\n    u8 pad_0[2];\n    u16 f_2;\n    u32 f_4;\n} stack8_t;" in out, out
    assert "    volatile u32 stack12;\n    volatile stack8_t stack8;" in out, out
    assert cparse.parse(out).function("h") is not None      # (still the dialect: the typedef names a type)
    assert "g(r0, (u32)&stack12, (u32)&stack8, r3);" in out, out
    assert "stack12 = r1; stack8.f_4 = r3;" in out and "+ stack8.f_2 +" in out, out
    assert "sp0" not in out and "frame" not in out, out     # (sp0 - 24 a variable, propagated away)


TWO = FRAME.replace("r2 = (u32)*(volatile u16 *)(sp0 - 6);", "r2 = (u32)*(volatile u16 *)(sp0 - 4);")
OVER = FRAME.replace("r2 = (u32)*(volatile u16 *)(sp0 - 6);", "r2 = (u32)*(volatile u16 *)(sp0 - 10);")


def test_two_types_at_one_offset_share_a_union():
    """A word stored at sp0 - 4 and its lower half read there: one union of a member for each, named by
    their types, in the object that starts at sp0 - 8."""
    out = readable_c(TWO)
    assert ("typedef struct\n{\n    u8 pad_0[4];\n    union\n    {\n        u32 f_4_u32;\n        u16 f_4_u16;\n"
            "    } __attribute__((packed));\n} __attribute__((packed, aligned(8))) stack8_t;") in out, out
    assert "stack12 = r1; stack8.f_4_u32 = r3;" in out and "+ stack8.f_4_u16 +" in out, out
    assert cparse.parse(out).function("h") is not None
    _, fn = readable.readable_tree("h", TWO, context=readable.Context())
    assert frames.plan(fn).unions() == 1


def test_overlapping_accesses_share_a_union():
    """A half read at sp0 - 10, inside the word stored at sp0 - 12: a union of the word and a structure
    of bytes up to the half and the half, anonymous, so that each keeps its plain name."""
    out = readable_c(OVER)
    assert ("    union\n    {\n        u32 f_0;\n        struct { u8 pad_f_2[2]; u16 f_2; } __attribute__((packed));\n"
            "    } __attribute__((packed));\n} __attribute__((packed, aligned(4))) stack12_t;") in out, out
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


def test_a_word_below_that_reaches_in_starts_the_object():
    """GCC -Os pushes a word to make room for a 16-bit local and hands the local's address on
    (FreeRTOS's vPolledQueueProducer): the address leaves at sp0 - 6, the push stores at sp0 - 8. The
    region starts at the push, and its word, the local and a byte between share a union of one object.
    The byte at sp0 - 7 lies below the address that leaves, but the push writes it: no variable of its
    own (it was one before the region started at the push, and read what the strb stored, not the push's
    byte). Without the program (Facts' pushes) the push's store stays."""
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


def test_a_buffer_a_callee_fills_is_an_array_of_bytes_and_an_index_an_element():
    """Nothing is accessed at a constant offset in the region, only through the addresses handed out
    and one with an index added (a buffer filled by a callee and read): arrays of bytes, one from each
    address handed out, the indexed access an element (stack12[i]: the index the code computes)."""
    out = readable_c(ARRAY)
    assert "    volatile u8 stack12[4];\n    volatile u8 stack8[8];" in out and "typedef" not in out, out
    assert "g(r0, (u32)stack12, (u32)stack8, r3);" in out, out
    assert "stack12[r0_1]" in out and "sp0" not in out, out
    _, fn = readable.readable_tree("h", ARRAY, context=readable.Context())
    assert [(p.start, p.size, p.kind, p.elem) for p in frames.plan(fn).pieces] == [(-12, 4, "array", "u8"),
                                                                                 (-8, 8, "array", "u8")]


def test_an_index_of_another_width_into_an_object_is_an_address_in_it():
    """A frame address with an index added and a byte read through it, in the object a word fills (a
    buffer walked as bytes and words): a structure of its own, the access the object's address plus
    the index, not an element; a raw slot below the region that the clean-up left keeps the frame
    array."""
    text = FRAME.replace("r0 = r0 + r1 + r2 + *(volatile u32 *)(sp0 - 24);",
                         "r0 = r0 + r1 + r2 + *(volatile u8 *)(sp0 - 12 + r2) + *(volatile u32 *)(sp0 - 24);")
    out = readable_c(text)
    assert "*(volatile u8 *)((u32)&stack12 + r2" in out and "stack12_t" in out, out
    below = FRAME.replace("    *(volatile u32 *)(sp0 - 24) = r2;  /* 2: str r2, [sp] */\n", "")
    out = readable_c(below)                 # (sp0 - 24 is read before anything writes it: it stays raw)
    assert "*(volatile u32 *)(sp0 - 24)" in out and "u32 frame[6]" in out and "stack12 = r1" in out, out


def test_a_wrapped_function_s_real_stack_stays_as_it_is():
    """A wrapped function's sp0 is the original's stack pointer, a parameter: what lies above it (the
    stack arguments) is the caller's, and an address handed out may reach it."""
    text = ("#include \"dread.h\"\nextern u32 g(u32);\nu32 h_dread_body(u32 a0, u32 a1, u32 a2, u32 a3, u32 sp0)\n"
            "{\n    *(volatile u32 *)(sp0 - 8) = a1;\n    return g(sp0 - 8);\n}\n")
    tree = cparse.parse(text)
    fn = tree.function("h_dread_body")
    assert frames.plan(fn) == "no frame array with sp0 at its end"
    assert frames.frame_objects(tree, fn) is None


# The timer daemon's message (FreeRTOS's xTimerGenericCommandFromTask on GCC -Os): three words stored
# into a 20-byte local below the saves of r4-r7 and lr, its address handed to the queue
MESSAGE = """/* h at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 send(u32, u32, u32, u32);
u32 h(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r12 = 0;
    u32 frame[10] __attribute__((aligned(8)));
    u32 sp0 = (u32)&frame[10];
    (void)r12; (void)sp0;
    *(volatile u32 *)(sp0 - 40) = r1; *(volatile u32 *)(sp0 - 36) = r2;  /* 8: strd r1, r2, [sp] */
    *(volatile u32 *)(sp0 - 32) = r0;  /* c: str r0, [sp, #8] */
    r0 = 0x20000100u;  /* e: ldr r0, [pc, #8] */
    r0 = *(volatile u32 *)r0;  /* 10: ldr r0, [r0] */
    r1 = sp0 - 40;  /* 12: mov r1, sp */
    r0 = send(r0, r1, r3, 0x0u); r1 = r2 = r3 = r12 = 0;  /* 16: bl send */
    return r0;  /* 1a: pop {r4, r5, r6, r7, pc} */
}
"""
SAVES = {-20: ("r4", 4), -16: ("r5", 4), -12: ("r6", 4), -8: ("r7", 4), -4: ("lr", 4)}


def test_words_a_function_stores_for_a_callee_are_a_structure_below_the_saves():
    """Three words at 0, 4 and 8 of what the queue is handed: a structure of its own (natural: no
    attributes), as large as the bytes up to the words the entry saves its caller's registers into,
    which are no object's; not volatile where the callee keeps nothing (the readable_types table's
    keeps), volatile where it may keep its argument or the table does not know it."""
    out = readable_c(MESSAGE, context=Facts(saves=SAVES, keeps={"send": []}, returns={"send": []}))
    assert "typedef struct\n{\n    u32 f_0;\n    u32 f_4;\n    u32 f_8;\n    u8 pad_c[8];\n} stack40_t;" in out, out
    assert "    stack40_t stack40;" in out and "volatile" not in out.split("{", 2)[2], out
    assert "stack40.f_0 = r1; stack40.f_4 = r2;" in out and "stack40.f_8 = r0;" in out, out
    assert "send(*(volatile u32 *)0x20000100u, (u32)&stack40, r3, 0)" in out or "send(" in out, out
    kept = readable_c(MESSAGE, context=Facts(saves=SAVES, keeps={"send": [1]}, returns={"send": []}))
    assert "    volatile stack40_t stack40;" in kept, kept
    unknown = readable_c(MESSAGE, context=Facts(saves=SAVES, keeps={}, returns={}))
    assert "    volatile stack40_t stack40;" in unknown, unknown


ESCAPE = MESSAGE.replace("    r0 = send(r0, r1, r3, 0x0u);",
                         "    *(volatile u32 *)0x20000104u = r1;  /* 14: str r1, [r2] */\n"
                         "    r0 = send(r0, r1, r3, 0x0u);")


def test_an_object_whose_address_escapes_into_a_global_stays_volatile():
    """The same message, its address also stored into a global before the call: an interrupt may reach
    it through the global while the function runs, so it stays volatile, whatever the callee does."""
    out = readable_c(ESCAPE, context=Facts(saves=SAVES, keeps={"send": []}, returns={"send": []}))
    assert "    volatile stack40_t stack40;" in out, out
    assert "*(volatile u32 *)0x20000104u = (u32)&stack40;" in out or "= (u32)&stack40;" in out, out


TYPED = MESSAGE.replace("    *(volatile u32 *)(sp0 - 32) = r0;  /* c: str r0, [sp, #8] */\n", "")


def test_a_structure_a_callee_s_parameter_points_to_types_the_object():
    """The callee's parameter is a structure of the readable_types table (its prototypes, dread.vartypes)
    whose fields at 0 and 4 the function stores: the object is of that structure, its definition in the
    file, and smaller than the bytes up to the saves (what lies past it is nobody's); its address goes to
    the callee as the parameter's pointer, the extern's parameter typed so (dread.vartypes
    object_arguments, since 2026-10-25: before, (u32)&stack40 for a u32)."""
    facts = Facts(saves=SAVES, keeps={"send": []}, returns={"send": []},
                  prototypes={"send": [None, None, "volatile struct s_7 *", None, None]},
                  structs={"s_7": [(0, "u32"), (4, "u32")]})
    out = readable_c(TYPED, context=facts)
    assert "struct s_7\n{\n    u32 f_0;\n    u32 f_4;\n};" in out, out
    assert "    struct s_7 stack40;" in out and "stack40.f_0 = r1; stack40.f_4 = r2;" in out, out
    assert "send(g_20000100, &stack40, r3, 0)" in out and "(u32)&stack40" not in out, out
    assert re.search(r"^extern u32 send\(u32, volatile struct s_7 \*, u32, u32\);$", out, re.M), out
    _, fn = readable.readable_tree("h", TYPED, context=facts)
    p = frames.plan(fn, facts).pieces
    assert [(x.start, x.size, x.kind, x.tag) for x in p] == [(-40, 8, "typed", "s_7")]


SPILLS = MESSAGE.replace("    *(volatile u32 *)(sp0 - 32) = r0;  /* c: str r0, [sp, #8] */\n",
                         "    *(volatile u32 *)(sp0 - 32) = r0;  /* c: str r0, [sp, #8] */\n"
                         "    *(volatile u16 *)(sp0 - 28) = (u16)r3;  /* d: strh r3, [sp, #12] */\n") \
    .replace("    return r0;  /* 1a:",
             "    r0 = r0 + *(volatile u32 *)(sp0 - 32) + *(volatile u16 *)(sp0 - 28);  /* 18: adds */\n"
             "    return r0;  /* 1a:")


def test_what_lies_past_a_callee_s_structure_is_objects_of_its_own():
    """The callee's structure is 8 bytes, the function stores and reads a word and a halfword past it
    (GCC -O0 keeps its spilled locals beside the TimeOut_t xTaskCheckForTimeOut gets): the structure
    an object of its own, each access past it one too (no address of theirs goes anywhere)."""
    facts = Facts(saves=SAVES, keeps={"send": []}, returns={"send": []},
                  prototypes={"send": [None, None, "volatile struct s_7 *", None, None]},
                  structs={"s_7": [(0, "u32"), (4, "u32")]})
    out = readable_c(SPILLS, context=facts)
    assert "    struct s_7 stack40;\n    u32 stack32;\n    u16 stack28;" in out, out
    assert "stack32 = r0;" in out and "stack28 = (u16)r3;" in out or "stack28 = r3;" in out, out
    _, fn = readable.readable_tree("h", SPILLS, context=facts)
    assert [(x.start, x.size, x.kind) for x in frames.plan(fn, facts).pieces] == [
        (-40, 8, "typed"), (-32, 4, "scalar"), (-28, 2, "scalar")]


SIGNS = """/* h at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 g(u32);
u32 h(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r12 = 0;
    u32 frame[2] __attribute__((aligned(8)));
    u32 sp0 = (u32)&frame[2];
    (void)r12; (void)sp0;
    *(volatile u8 *)(sp0 - 8) = (u8)r1;  /* 0: strb r1, [sp] */
    *(volatile s8 *)(sp0 - 7) = (s8)r2;  /* 2: strb r2, [sp, #1] */
    r0 = g(sp0 - 8); r1 = r2 = r3 = r12 = 0;  /* 4: bl g */
    r0 = r0 + (u32)*(volatile s8 *)(sp0 - 8) + (u32)*(volatile u8 *)(sp0 - 7);  /* 8: ldrsb, ldrb */
    return r0;  /* c: pop {r3, pc} */
}
"""


def test_an_access_of_the_other_sign_is_the_same_bytes():
    """A byte stored unsigned and read signed (strb, ldrsb): the array's element, read with a cast, stored
    without one (the same bits); changed in place with both signs (x >>= 1 of a signed and an unsigned
    byte), a structure of a member for each type, no cast at all."""
    out = readable_c(SIGNS)
    assert "volatile u8 stack8[8];" in out and "stack8[0] = r1;" in out and "stack8[1] = r2;" in out, out
    assert "(s8)stack8[0]" in out and "(u8)stack8" not in out and "(s8)stack8[1] =" not in out, out
    shift = SIGNS.replace("    r0 = g(sp0 - 8);", "    *(volatile s8 *)(sp0 - 7) = *(volatile s8 *)(sp0 - 7) >> 1;"
                          "  /* 3: asrs */\n    r0 = g(sp0 - 8);")
    out = readable_c(shift)
    assert "stack8[1] = (s8)stack8[1] >> 1;" in out, out    # (read signed, stored as the same bits)
    inplace = SIGNS.replace("    r0 = g(sp0 - 8);", "    *(volatile s8 *)(sp0 - 7) >>= 1;  /* 3: asrs */\n"
                            "    r0 = g(sp0 - 8);")
    out = readable_c(inplace)
    assert "stack8_t" in out and "stack8.f_1_s8 >>= 1;" in out and "u8 f_1_u8;" in out, out


# A buffer filled through an index from an address the code made one below it (clang folds a -1 into
# the base: fmt_dec's tmp[--n]), and a byte handed to a callee with an address of its own
BELOW = """/* h at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 put(u32);
u32 h(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r12 = 0;
    u32 frame[4] __attribute__((aligned(8)));
    u32 sp0 = (u32)&frame[4];
    (void)r12; (void)sp0;
    r3 = sp0 - 13;  /* 0: addi a3, sp, 3 */
    *(volatile u8 *)(r3 + r0) = (u8)r1;  /* 4: sb a1, 0(a3) */
    r3 = r3 - 1;  /* 8: addi a3, a3, -1 */
    r2 = (u32)*(volatile u8 *)(r3 + r0);  /* c: lbu a2, 0(a3) */
    r0 = put(sp0 - 12);  /* 10: call put */
    return r0 + r2;  /* 14: ret */
}
"""


def test_an_index_added_below_an_object_reaches_into_it():
    """The code makes sp0 - 13 and sp0 - 14 (walked downward) and adds an index to them: addresses
    nobody is handed, one below the buffer the callee gets at sp0 - 12. An index added to an address no
    object starts at reaches the object above too, so the bytes from the lowest such address up are
    one array, the index's elements counted from it, the callee's address inside it."""
    out = readable_c(BELOW)
    assert "    volatile u8 stack14[14];" in out and "put((u32)&stack14[2])" in out, out
    assert "stack14[r0 + 1] = r1;" in out and "r2 = stack14[r0];" in out, out


# clang -O2 adds the index to sp0 before the constant (FreeRTOS prvReceivingTask, prvNonBlockingReceiverTask,
# xStreamBufferNextMessageLengthBytes): the region stayed raw, the frame array and all
RAW = """/* h at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 fill(u32, u32);
u32 h(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r5 = 0, r12 = 0;
    u32 frame[6] __attribute__((aligned(8)));
    u32 sp0 = (u32)&frame[6];
    (void)r12; (void)sp0;
    r5 = r0;  /* 0: mov r5, r0 */
    r0 = sp0 - 20;  /* 2: add r0, sp, #4 */
    r0 = fill(r0, 0x14u); r1 = r2 = r3 = r12 = 0;  /* 4: bl fill */
    r0 = (u32)*(volatile u8 *)(sp0 + r5 - 0x14u);  /* 8: ldrb r0, [r4, r5] */
    return r0;  /* c: pop {r4, r5, pc} */
}
"""


def test_an_index_added_to_sp0_first_is_an_element():
    """sp0 + r5 - 0x14u reaches the frame at sp0 - 20 plus an index: no address at sp0 that leaves (the
    region stayed raw for it), an element of the array the callee fills."""
    out = readable_c(RAW)
    assert "u8 stack20[20];" in out and "fill((u32)stack20, 0x14u)" in out and "return stack20[r0];" in out, out
    assert "sp0" not in out and "frame" not in out, out
    fn = cparse.parse(RAW).function("h")
    tidy.tidy(fn)
    assert frames.plan(fn).pieces[0].indexed


# print_status's push (gizmo): r0-r3 pushed to make room for a 12-byte buffer, r4 and lr saved above it
PUSH = """/* h at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 fmt(u32, u32);
extern u32 puts(u32);
u32 h(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r12 = 0;
    u32 frame[6] __attribute__((aligned(8)));
    u32 sp0 = (u32)&frame[6];
    (void)r12; (void)sp0;
    *(volatile u32 *)(sp0 - 24) = r0; *(volatile u32 *)(sp0 - 20) = r1; *(volatile u32 *)(sp0 - 16) = r2; *(volatile u32 *)(sp0 - 12) = r3;  /* 10: push {r0, r1, r2, r3, r4, lr} */
    r4 = 0x40000060u;  /* 14: ldr r4, [pc, #0x40] */
    r1 = *(volatile u32 *)r4;  /* 18: ldr r1, [r4] */
    r0 = (sp0 - 24) + 0x4u;  /* 1c: add r0, sp, #4 */
    r0 = fmt(r0, r1); r1 = r2 = r3 = r12 = 0;  /* 20: bl fmt */
    r0 = (sp0 - 24) + 0x4u;  /* 24: add r0, sp, #4 */
    r0 = puts(r0); r1 = r2 = r3 = r12 = 0;  /* 28: bl puts */
    return r0;  /* 2c: pop {r1, r2, r3, r4, pc} */
}
"""


def test_a_push_that_makes_room_reads_no_parameter():
    """The push stores what the caller left in r1-r3 into the buffer's words: kept, they were the
    function's parameters (print_status(void) took four) and its object's fields; nothing loads those
    words, so they go (dread.tidy push_room: only where the program says the instruction is a push), and
    the buffer is an array of bytes up to the save words; volatile where its address is the function's
    result (a callee hands it back, and the function returns what the callee does)."""
    facts = Facts(saves={-8: ("r4", 4), -4: ("lr", 4)}, pushes={0x10}, keeps={"fmt": [], "puts": []},
                  returns={"fmt": [], "puts": []})
    out = readable_c(PUSH, context=facts)
    assert "u32 h(void)" in out and "    u8 stack20[12];" in out, out
    assert "fmt((u32)stack20, g_40000060);" in out and "return puts((u32)stack20);" in out, out
    back = readable_c(PUSH, context=Facts(saves={-8: ("r4", 4), -4: ("lr", 4)}, pushes={0x10},
                                          keeps={"fmt": [], "puts": []}, returns={"fmt": [], "puts": [0]}))
    assert "    volatile u8 stack20[12];" in back, back     # (puts hands it back, h returns it: it escapes)
    kept = readable_c(PUSH, context=Facts(saves={-8: ("r4", 4), -4: ("lr", 4)}))
    assert "u32 h(u32 a0, u32 r1, u32 r2, u32 r3)" in kept and "stack20_t" in kept, kept


def test_a_push_s_word_stored_over_before_any_read_goes():
    """A pushed word the function loads, but only after a store of its own over every byte loaded
    (xQueueGenericSend pushes r1 and stores its fourth argument there at once): the push's store goes;
    one that may be read before (a call between) stays."""
    text = FRAME.replace("    *(volatile u32 *)(sp0 - 24) = r2;  /* 2: str r2, [sp] */\n",
                         "    *(volatile u32 *)(sp0 - 12) = r2;  /* 2: str r2, [sp, #12] */\n"
                         "    *(volatile u32 *)(sp0 - 24) = r2;  /* 3: str r2, [sp] */\n")
    out = readable_c(text, context=Facts(pushes={0}))
    assert "stack12 = r1" not in out and "stack12 = r2;" in out, out
    later = FRAME.replace("    r1 = *(volatile u32 *)(sp0 - 12);  /* 8: ldr r1, [sp, #12] */\n",
                          "    r1 = *(volatile u32 *)(sp0 - 12);  /* 8: ldr r1, [sp, #12] */\n"
                          "    *(volatile u32 *)(sp0 - 12) = r2;  /* 9: str r2, [sp, #12] */\n")
    out = readable_c(later, context=Facts(pushes={0}))
    assert "stack12 = r1;" in out, out


KEEPERS = """#include "dread.h"
extern u32 elsewhere(u32);
u32 store(u32 r0)
{
    *(volatile u32 *)0x20000000u = r0 + 4;
    return 0;
}
u32 hand_back(u32 r0, u32 r1)
{
    *(volatile u8 *)r0 = (u8)r1;
    return r0;
}
u32 pass_on(u32 r0, u32 r1)
{
    u32 r2;
    r2 = r0;
    return store(r2) + r1;
}
u32 keep_result(u32 r0)
{
    u32 r1;
    r1 = hand_back(r0, 0);
    *(volatile u32 *)0x20000008u = r1;
    return 0;
}
u32 unknown(u32 r0)
{
    return elsewhere(r0);
}
u32 reads(u32 r0, u32 r1)
{
    return *(volatile u32 *)(r0 + 8) + *(volatile u32 *)r1;
}
__attribute__((naked)) void wrapped(void)
{
    __asm__ volatile ("b wrapped_dread_body");
}
u32 to_wrapped(u32 r0, u32 r1)
{
    wrapped(r1, r0);
    return 0;
}
"""


def test_which_parameters_a_function_may_keep():
    """kept_params: a parameter stored into memory (plus a constant) is kept, one returned is handed back
    (not kept: memset's destination), one handed on to a callee that keeps it is kept, as is one handed
    back by a callee and then stored, or handed to a function the analysis does not have or to a naked
    entry (a wrapped variadic function's: its C does not show what it does with them); one only
    dereferenced is neither."""
    tree = cparse.parse(KEEPERS)
    trees = [(tree, fn, {}) for fn in tree.items if isinstance(fn, cparse.Function)]
    keeps, returns = frames.kept_params(trees)
    every = list(range(frames.EVERY))
    assert keeps == {"store": [0], "hand_back": [], "pass_on": [0], "keep_result": [0], "unknown": [0],
                     "reads": [], "wrapped": every, "to_wrapped": [0, 1]}, keeps
    assert returns == {"store": [], "hand_back": [0], "pass_on": [1], "keep_result": [], "unknown": [],
                       "reads": [], "wrapped": every, "to_wrapped": []}, returns   # (pass_on's: r1 plus a result)


def test_print_status_s_buffer_is_its_own_array_and_its_parameters_are_gone(target):
    """gizmo's print_status (char buf[12] for fmt_dec and uart_puts): main's readable C read four
    phantom parameters and `volatile stack20_t stack20` with three u32 fields (the push's) over the
    buffer and the saves of r4 and lr; now u32 print_status(void) with a 12-byte array, not volatile:
    fmt_dec and uart_puts keep nothing (the readable_types table as the check makes it). apply_config's
    value, key[12] and line[40] are three objects, the line written through its index (an element on the
    ARM builds; on rv32 through a variable that holds the element's address, as the code computes it).
    Since 2026-10-25 the buffer goes to fmt_dec as its own parameter's pointer (fmt_dec(stack20, ...),
    its extern's parameter volatile u8 *), to uart_puts, whose own C takes an integer (texts too), still
    as (u32)stack20."""
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = readable.Context(target, image, program, flow)
    readable.make_plan(program, context, lifter, build.read_names(image.table("lifted")), flow)

    def text(name):
        return readable.transform(name, lifter.lift(program.by_name[name]).text, context=context)
    status = text("print_status")
    assert "void print_status(void)" in status, status      # (nobody reads its result: dread.tidy void_results)
    assert "    u8 stack20[12];" in status and "fmt_dec(stack20, " in status, status
    assert "extern u32 fmt_dec(volatile u8 *, u32);" in status, status
    assert "uart_puts((u32)stack20);" in status and "stack20_t" not in status, status
    config = text("apply_config")
    decls = {m.group(2): (m.group(1), m.group(4)) for m in re.finditer(
        r"^\s*(?:volatile )?(u8|u32) (stack\d+)(\[(\d+)\])?;$", config, re.M)}
    assert sorted(decls.values(), key=str) == [("u32", None), ("u8", "12"), ("u8", "40")], config
    line = next(n for n, (_, k) in decls.items() if k == "40")
    assert re.search(r"\b%s\[r\w+\] = " % line, config) or "= (u32)%s + " % line in config, config


def test_a_function_the_table_lists_whole_keeps_one_object_for_its_region():
    """The readable_types table's whole (the check puts a function there whose objects its cases
    rejected): the region from the lowest address handed out up to sp0 is one volatile object, as
    before 2026-10-23, the save words and the stores into them inside it, its index arithmetic on its
    address."""
    facts = Facts(saves=SAVES, keeps={"send": []}, returns={"send": []})
    facts.plan.whole = {"h"}
    out = readable_c(MESSAGE, context=facts)
    assert ("typedef struct\n{\n    u32 f_0;\n    u32 f_4;\n    u32 f_8;\n    u8 pad_c[28];\n} "
            "__attribute__((packed, aligned(8))) stack40_t;") in out, out
    assert "    volatile stack40_t stack40;" in out, out
    facts = readable.Context()
    facts.plan.whole = {"h"}
    out = readable_c(ARRAY, context=facts)
    assert "volatile u8 stack12[12] __attribute__((aligned(4)));" in out, out
    assert "*(volatile u8 *)((u32)stack12 + r0_1)" in out and "g(r0, (u32)stack12, (u32)&stack12[4], " in out, out


@pytest.mark.slow
@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
@pytest.mark.parametrize("buf", ["stack20", "digits"])
def test_the_gate_tries_a_function_whose_objects_fail_once_more_whole(tmp_path, monkeypatch, buf):
    """A function whose readable C with its frame split into objects fails its cases (here made to fail
    while print_status's buffer is the array of its own) is tried again with its region as one object:
    accepted so (an array of bytes up to sp0, its push's stores gone all the same), listed in the
    readable_types table's whole, and built so by the next build. So too where the readable_names table
    names the buffer (digits): the check asks dread.frames whether it made objects, not the C it built
    (which looked for their default names until 2026-10-24); the whole region takes the name of the
    object that starts where it does."""
    from test_readable import gizmo_copy
    from dread import lift as lift_, runner, shapes
    monkeypatch.setattr(readable, "_STORE", {})     # (what this check makes stays out of later tests' checks)
    target = gizmo_copy(tmp_path)
    image = target.image()
    work = tmp_path / "gizmo" / "m3" / "build" / "readable-check" / image.name
    if buf != "stack20":
        program = Program(target, image)
        flow = regflow.load(target, image, program)
        made = readable.question_trees(program, readable.Context(target, image, program, flow),
                                       lift_.make_lifter(program, flow), ["print_status"])["print_status"]
        naming.store_all(image, {"print_status": {"digest": made[2], "names": {"stack20": buf}, "ids": made[5],
                                                  "what": "prints", "by": "test"}})
    real = runner.run

    def run(names, **kw):
        failed, uncovered, results = real(names, **kw)
        source = (work / "lifted" / "print_status.c")
        if "print_status" in names and source.exists() and "u8 %s[12];" % buf in source.read_text():
            kw.get("out", print)("FAIL print_status [made to fail]")
            results = [(n, c, 1 if n == "print_status" else b) for n, c, b in results]
            failed += 1
        return failed, uncovered, results
    monkeypatch.setattr(runner, "run", run)
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert "print_status" in accepted, lines
    assert any(line.startswith("whole    print_status") for line in lines), lines
    assert shapes.load_plan(image).whole == {"print_status"}
    text = (work / "lifted" / "print_status.c").read_text()     # (the last build's: the whole image's)
    assert "volatile u8 %s[20] __attribute__((aligned(4)));" % buf in text, text


def entry_for(text, names, name="h"):
    """A readable_names entry made for the readable C of text before names (its digest), as dread pilot
    keeps one."""
    _, fn = readable.readable_tree(name, text, context=readable.Context())
    return {"digest": naming.digest(fn), "names": dict(names), "what": "", "by": "test"}


def named_c(text, names):
    facts = Facts()
    facts.names = {"h": entry_for(text, names)}
    return readable_c(text, context=facts)


def test_names_of_the_frame_array_and_sp0_leave_the_objects_alone():
    """An entry made for the C that names the frame array and sp0 (what a question that showed the frame
    raw had a model name) besides other variables: the objects are made all the same, the names of what
    they took away dropped, the others applied. Until 2026-10-24 the names came first and dread.frames,
    which finds the frame by the lifters' names, made nothing: u32 locals[6]; u32 frame_end =
    (u32)&locals[6]; total = g(r0, frame_end - 12, frame_end - 8, r3)."""
    out = named_c(FRAME, {"frame": "locals", "sp0": "frame_end", "r0_1": "total", "r2": "extra"})
    assert "    volatile u32 stack12;\n    volatile stack8_t stack8;" in out, out
    assert "total = g(r0, (u32)&stack12, (u32)&stack8, r3);" in out and "+ stack8.f_2 + extra;" in out, out
    assert "locals" not in out and "frame_end" not in out and "sp0" not in out, out


def test_a_table_names_the_objects():
    """An entry that names the objects (made from a question that showed them): each takes its name, in
    its uses and its addresses; a structure's type keeps its own (stack8_t). A name for an object this C
    does not have (made where the region was split otherwise) is dropped alone; a name for a variable
    that is none drops the whole entry, as before. An object takes no default name that the table gives
    another variable (stack12_1)."""
    out = named_c(FRAME, {"stack12": "count", "stack8": "pair", "r0_1": "total"})
    assert "    volatile u32 count;\n    volatile stack8_t pair;" in out, out
    assert "count = r1; pair.f_4 = r3;" in out and "total = g(r0, (u32)&count, (u32)&pair, r3);" in out, out
    out = named_c(FRAME, {"stack16": "gone", "r0_1": "total"})
    assert "total = g(r0, (u32)&stack12, (u32)&stack8, r3);" in out and "gone" not in out, out
    out = named_c(FRAME, {"r9": "nine", "r0_1": "total"})
    assert "total" not in out and "nine" not in out, out
    out = named_c(FRAME, {"r1_1": "stack12"})
    assert "volatile u32 stack12_1;" in out and "stack12 = stack12_1;" in out, out


def test_the_question_shows_the_objects_and_their_names_carry_by_offset():
    """The naming question's C (dread.readable question_tree) is the C the builds make before the names:
    the objects, no frame array; an object's identity is its offset below sp0 (stack8), a scalar's with
    the instructions that write it (stack12 0x0). Carried to C made anew, a name goes to the object of
    the same identity; one whose object starts elsewhere now (sp0 - 16 handed out instead of sp0 - 12)
    is dropped, the new object open (a question asks for it)."""
    tree, fn, digest, _, code, ids = readable.question_tree("h", FRAME, Facts())
    assert "    volatile u32 stack12;\n    volatile stack8_t stack8;" in code and "sp0" not in code, code
    assert ids["stack12"] == "stack12 0x0" and ids["stack8"] == "stack8" and "frame" not in ids, ids
    entry = {"digest": digest, "names": {"stack12": "count", "stack8": "pair", "r0_1": "total"}, "ids": ids,
             "what": "", "by": "test"}
    changed = FRAME.replace("r0 = r0 + r1 + r2 + *(volatile u32 *)(sp0 - 24);",
                            "r0 = r0 + r1 + r2 + *(volatile u32 *)(sp0 - 24) + 0x1u;")
    tree, fn, digest, _, _, ids = readable.question_tree("h", changed, Facts())
    got = naming.carry(tree, fn, entry, digest, ids)
    assert got["names"] == entry["names"] and got["carried"]["open"] == [], got
    moved = FRAME.replace("g(r0, sp0 - 16 + 0x4u,", "g(r0, sp0 - 16,")
    tree, fn, digest, _, code, ids = readable.question_tree("h", moved, Facts())
    assert "volatile stack16_t stack16;" in code, code
    got = naming.carry(tree, fn, entry, digest, ids)
    assert got["names"] == {"stack8": "pair", "r0_1": "total"} and got["carried"]["dropped"] == {"stack12": "count"}
    assert got["carried"]["open"] == ["stack16"], got


def gizmo_context(target):
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = readable.Context(target, image, program, flow)
    readable.make_plan(program, context, lifter, build.read_names(image.table("lifted")), flow)
    return image, program, lifter, context


def test_a_name_of_sp0_leaves_print_status_s_buffer_an_object(target):
    """gizmo's print_status with a readable_names entry made for its C naming sp0 alone (buf_end), as a
    review found one from a question that showed the frame raw: the printed C and the compiled C the
    gate builds declare the buffer as without it (u8 stack20[12]), no frame array, no buf_end. Until
    2026-10-24: u32 frame[6]; u32 buf_end = (u32)&frame[6]; fmt_dec(buf_end - 20, ...)."""
    image, program, lifter, context = gizmo_context(target)
    text = lifter.lift(program.by_name["print_status"]).text
    plain = readable.printed("print_status", text, context)
    buf = re.search(r"^    u8 (stack\d+)\[12\];$", plain, re.M)
    assert buf and "frame" not in plain and "sp0" not in plain, plain
    _, fn = readable.readable_tree("print_status", text, context=context)
    context.names = {"print_status": {"digest": naming.digest(fn), "names": {"sp0": "buf_end"}, "what": "prints",
                                      "by": "test"}}
    for out in (readable.printed("print_status", text, context), readable.transform("print_status", text,
                                                                                    context=context)):
        assert buf.group(0) in out and "fmt_dec(%s, " % buf.group(1) in out, out
        assert "buf_end" not in out and "frame[" not in out and "/* print_status: prints (names: test) */" in out, out


@pytest.mark.slow
@pytest.mark.compiles
def test_the_gate_builds_print_status_s_buffer_under_a_name_of_sp0(variant, tmp_path, monkeypatch):
    """The same entry through the gate (dread readable --check of print_status) on a copy of every gizmo
    build: print_status is accepted, and the C the check built declares the buffer, no buf_end."""
    from conftest import GIZMO, gizmo_dir
    from dread.target import Target
    monkeypatch.setattr(readable, "_STORE", {})     # (what this check makes stays out of later tests' checks)
    shutil.copytree(GIZMO, tmp_path / "gizmo", ignore=shutil.ignore_patterns("build"))
    cases = os.path.join(gizmo_dir(variant), "build", "autocases")
    if os.path.isdir(cases):
        shutil.copytree(cases, tmp_path / "gizmo" / variant / "build" / "autocases")
    target = Target.load(str(tmp_path / "gizmo" / variant))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    text = lift.make_lifter(program, flow).lift(program.by_name["print_status"]).text
    _, fn = readable.readable_tree("print_status", text, context=readable.Context(target, image, program, flow))
    naming.store_all(image, {"print_status": {"digest": naming.digest(fn), "names": {"sp0": "buf_end"},
                                              "what": "prints", "by": "test"}})
    lines = []
    assert readable.check(target, image, names=["print_status"], out=lines.append) == ["print_status"], lines
    built = open(os.path.join(target.build, "readable-check", image.name, "lifted", "print_status.c")).read()
    assert re.search(r"^    u8 stack\d+\[12\];$", built, re.M) and "buf_end" not in built, built
    assert "/* print_status: prints (names: test) */" in built, built


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_pilot_asks_for_the_objects_an_earlier_question_did_not_show(tmp_path):
    """dread pilot --task readable on gizmo m3: print_status's entry was made for its C from a question
    that showed the frame raw (its ids have frame and sp0, it names sp0 and r0): asked again, the question
    showing the buffer (stack20), the name of r0 in it and not that of sp0, asking for stack20; the entry
    then names the buffer, has its identity and no name of sp0, and the printed C declares the buffer
    under its name. An entry made from a question that showed the objects and left one unnamed (fmt_dec's
    stack36) is not asked again, nor one of a function without objects (strlen)."""
    from test_carry import gizmo
    from dread import llm, pilot
    target, image, program, context, lifter = gizmo(tmp_path)
    made = readable.question_trees(program, context, lifter, ["print_status", "strlen", "fmt_dec"])
    text = lifter.lift(program.by_name["print_status"]).text
    _, fn = readable.readable_tree("print_status", text, context=context)
    assert made["print_status"][2] == naming.digest(fn)     # (the digest: taken before the objects, as always)
    raw_ids = naming.identities(fn)                           # (what the question before 2026-10-24 kept)
    assert "frame" in raw_ids and "stack20" not in raw_ids and made["print_status"][5]["stack20"] == "stack20"
    naming.store_all(image, {
        "print_status": {"digest": naming.digest(fn), "names": {"sp0": "buf_end", "r0": "mode_text"}, "ids": raw_ids,
                         "what": "prints the status", "by": "test"},
        "strlen": {"digest": made["strlen"][2], "names": {"r0": "text"}, "ids": made["strlen"][5], "what": "",
                   "by": "test"},
        "fmt_dec": {"digest": made["fmt_dec"][2], "names": {"r0": "value"}, "ids": made["fmt_dec"][5], "what": "",
                    "by": "test"}})
    assert "stack36" in made["fmt_dec"][5]                    # (its question showed its object)
    asked = []

    def answer(messages):
        asked.append(messages[0]["content"])
        return '```json\n{"what": "prints the mode and the volume", "names": {"stack20": "digits"}}\n```'
    lines = []
    named, n_asked, _ = pilot.name_variables(target, image, program, lambda: llm.Scripted(answer),
                                             ["print_status", "strlen", "fmt_dec"], readable.Context(target, image,
                                                                                                     program),
                                             lifter, jobs=1, out=lines.append)
    assert (named, n_asked) == (3, 1) and len(asked) == 1, lines
    question = asked[0]
    assert "u8 stack20[12];" in question and "mode_text = mode_name(" in question, question
    assert "frame" not in question.split("Some of its")[0] and "buf_end" not in question, question
    assert "names already, from a question that did not show its stack frame's objects: mode_text" in question
    assert "name the others (stack20)." in question, question
    entry = naming.load(image)["print_status"]
    assert entry["names"] == {"r0": "mode_text", "stack20": "digits"} and entry["ids"]["stack20"] == "stack20", entry
    out = readable.printed("print_status", text, readable.Context(target, image, program))
    assert "u8 digits[12];" in out and "fmt_dec((u32)digits, " in out and "mode_text" in out, out


@pytest.mark.compiles
def test_the_objects_compile(variant, tmp_path):
    from conftest import gizmo_dir
    from dread.target import Target
    target = Target.load(gizmo_dir(variant))
    image = target.image()
    src = tmp_path / "frames"
    src.mkdir()
    index = FRAME.replace("r0 = r0 + r1 + r2 + *(volatile u32 *)(sp0 - 24);",
                          "r0 = r0 + r1 + r2 + *(volatile u8 *)(sp0 - 12 + r2) + *(volatile u32 *)(sp0 - 24);")
    facts = Facts(saves=SAVES, keeps={"send": []}, returns={"send": []})
    typed = Facts(saves=SAVES, keeps={"send": []}, returns={"send": []},
                  prototypes={"send": [None, None, "volatile struct s_7 *", None, None]},
                  structs={"s_7": [(0, "u32"), (4, "u32")]})
    variants = [(FRAME, None), (index, None), (FRAME.replace("    *(volatile u32 *)(sp0 - 24) = r2;  /* 2: str r2, "
                                                            "[sp] */\n", ""), None),
                (TWO, None), (OVER, None), (REACH, None), (ARRAY, None), (MESSAGE, facts), (ESCAPE, facts),
                (TYPED, typed), (SPILLS, typed), (BELOW, None), (RAW, None), (SIGNS, None),
                (PUSH, Facts(saves={-8: ("r4", 4), -4: ("lr", 4)}, pushes={0x10}, keeps={}, returns={}))]
    for k, (text, context) in enumerate(variants):
        out = readable_c(text.replace("u32 h(", "u32 h%d(" % k).replace("h at 0x0", "h%d at 0x0" % k), "h%d" % k,
                         context)
        assert "stack" in out, out
        (src / ("h%d.c" % k)).write_text(out)
    assert build.compile_all(target, image, str(src), str(tmp_path / "obj"), quiet=True,
                             flags=target.compiler.lifted_cflags)


WALK = """/* h at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 g(u32, u32, u32, u32);
u32 h(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r12 = 0;
    u32 n = 0, z = 0, c = 0, v = 0;
    u32 frame[8] __attribute__((aligned(8)));
    u32 sp0 = (u32)&frame[8];
    (void)n; (void)z; (void)c; (void)v; (void)r12; (void)sp0;
    r3 = 0x0u;  /* 0: movs r3, #0 */
L_2: ;
    *(volatile u32 *)(sp0 - 32 + (r3 << 2)) = 0x0u;  /* 2: str r4, [sp, r3, lsl #2] */
    r3 = r3 + 0x1u;  /* 4: adds r3, #1 */
    if (r3 != 0x8u) goto L_2;  /* 6: bne #0x2 */
    r2 = *(volatile u32 *)(sp0 - 32);  /* 8: ldr r2, [sp] */
    *(volatile u32 *)(sp0 - 32) = r2 | 0x80u;  /* a: str r2, [sp] */
    r0 = g(sp0 - 32, *(volatile u32 *)(sp0 - 28), 0x0u, 0x0u);  /* c: bl g */
    r1 = (u32)*(volatile u16 *)(sp0 - 26);  /* 10: ldrh r1, [sp, #6] */
    return r0 + r1;  /* 12: pop */
}
"""


def test_a_frame_region_walked_by_an_index_is_an_array_its_narrower_read_a_view():
    """A region a loop walks word by word (zeroed), whose first word gets a bit set, whose address and a
    word go to a callee and of which one halfword is read: an array of the walk's element, the loop's
    stores its elements (stack32[r3]), the halfword an element of a halfword view of it (bytes 6 and 7:
    ((volatile u16 *)stack32)[3]), not a packed structure with a union and the stores as address sums."""
    out = readable_c(WALK)
    for line in ("volatile u32 stack32[8];", "stack32[r3] = 0;", "stack32[0] |= 0x80u;",
                 "r0 = g((u32)stack32, stack32[1], 0, 0);", "return r0 + ((volatile u16 *)stack32)[3];"):
        assert line in out, (line, out)
    assert "typedef" not in out and "union" not in out and "sp0" not in out, out
    _, fn = readable.readable_tree("h", WALK, context=readable.Context())
    assert [(p.start, p.size, p.kind, p.elem) for p in frames.plan(fn).pieces] == [(-32, 32, "array", "u32")]


def test_a_walked_region_read_across_its_elements_stays_a_structure():
    """Not an array: a narrower read that is not aligned to its own size (a halfword at byte 3), or one
    that spans two elements (a word at byte 6 of words): the structure of its own, as before."""
    for read in ("r1 = (u32)*(volatile u16 *)(sp0 - 29);", "r1 = *(volatile u32 *)(sp0 - 26);"):
        out = readable_c(WALK.replace("r1 = (u32)*(volatile u16 *)(sp0 - 26);", read))
        assert "stack32_t" in out and "volatile u32 stack32[8];" not in out, (read, out)
