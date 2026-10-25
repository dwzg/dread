"""dread.polish: readable C's constants and casts written by their use. Each rule on small functions of
the readable dialect; the printed forms compiled by the host's C compiler beside the originals and
called with boundary and random arguments (the same results); the comparison sets the literals are
chosen by against brute force; and gizmo's parse_kv, read_line, cmd_mode and strncpy and the FreeRTOS
forms the review named (xTimerPendFunctionCallFromISR's command, prvCheckExpectedTimeIsWithinAnAccept-
ableMargin's line numbers, xTimerCreate's list item, vPortFree's header, eTaskGetState's states)."""
import ctypes
import itertools
import random
import re
import shutil
import subprocess

import pytest

from dread import cexpr, cparse, polish
from conftest import gizmo_dir

needs_gcc = pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")

HEAD = ('#include "dread.h"\n'
        "extern u32 g(u32, u32);\n"
        "extern u32 parse_uint(u32, u32);\n"
        "#define g_20000930 (*(volatile u32 *)0x20000930u)\n"
        "#define io_40004004 (*(volatile u32 *)0x40004004u)\n")
IO = [(0x40000000, 0x50000000)]


def polished(body, name="f", int_bits=None, head=HEAD, raw=(), pointers=None):
    """The function name of head + body after the pass; raw: definitions put into the tree as the
    stages put them (a structure of dread.shapes), after the head's own items; pointers: {variable:
    structure} declared as dread.shapes declares them (volatile struct NAME *v, which the parser does
    not read), in place of the body's own declaration of the variable."""
    tree = cparse.parse(head + body)
    fn = tree.function(name)
    for text in raw:
        tree.items.insert(tree.items.index(fn), cparse.Raw(text))
    for v, struct in (pointers or {}).items():
        for s in fn.body.body:
            if isinstance(s, cparse.Decl):
                s.items = [d for d in s.items if d.name != v]
        fn.body.body.insert(0, cparse.Decl("volatile struct %s" % struct, [cparse.Declarator(v, ptr="*")]))
    polish.polish(tree, fn, int_bits=int_bits, io=IO)
    return cparse.unparse(fn, notes=False, style="readable")


def test_negative_numbers_where_a_value_is_one():
    """A complement (mvn) or a constant near the top of its type is -k where it is assigned, returned,
    passed, compared for equality, compared in order where the comparison is signed, or added; it stays
    as it was where it is a mask (an operand of &, a variable only & reads), in an unsigned order (x <
    -2 would read wrong), beside a 64-bit operand (-1 would be 64 bits of ones) and in a byte."""
    out = polished("""u32 f(u32 r0, u32 r1)
{
    u32 r2, r3, m;
    u64 w;
    r2 = ~0x1u;
    if (r0 == 0xffffffffu) return ~0x0u;
    if (r0 < 0xfffffffeu) r2 = r0 & ~0x20u;
    if ((s32)r0 < (s32)~0x1u) r2 = 3;
    m = ~0xfu;
    r3 = r1 & m;
    w = (u64)r0;
    if (w == 0xffffffffu) r2 = 0;
    *(volatile u8 *)r1 = 0xffu;
    r2 = r1 + 0xfffffff8u;
    g(~0x2u, r3);
    return r2 + r3;
}
""")
    for line in ("r2 = -2;", "if (r0 == -1) return -1;", "if (r0 < 0xfffffffeu) r2 = r0 & ~0x20u;",
                 "if ((s32)r0 < -2) r2 = 3;", "m = ~0xfu;", "if (w == 0xffffffffu) r2 = 0;",
                 "*(volatile u8 *)r1 = 0xffu;", "r2 = r1 - 8;", "g(-3, r3);"):
        assert line in out, (line, out)


def test_casts_a_store_implies_and_constants_folded():
    """(u8)0x1u stored into a byte is 1, a byte variable's (u8)0x2u is 2; a cast chain keeps the
    narrowest cast that matters; x + 0, 0x0u | x go; x = (u32)(x + 1) is x++, x = x + 4 is x += 4;
    a sum through 64 bits truncated is the 32-bit sum, its 0 + ~y + 1 a negation; ((u64)0x0u << 32) |
    0x0u is 0; x = x of a local goes."""
    out = polished("""u64 f(u32 r0, u32 r1)
{
    u32 r2, r3;
    u8 sl9;
    *(volatile u8 *)r1 = (u8)0x1u;
    sl9 = (u8)0x2u;
    r2 = (u16)(u32)(s16)r0;
    r3 = r0 + 0;
    r3 = 0x0u | r3;
    r2 = (u32)(r2 + 1);
    r3 = r3 + 4;
    r1 = r1 - 1;
    g((u32)((u64)(u32)r0 + 4 + 0), (u32)((u64)0x0u + (u32)~0x8u + 1) + r1);
    return ((u64)0x0u << 32) | 0x0u;
}
""")
    for line in ("*(volatile u8 *)r1 = 1;", "sl9 = 2;", "r2 = (u16)r0;", "r3 = r0;", "r2++;", "r3 += 4;",
                 "r1--;", "g(r0 + 4, r1 - 8);", "return 0;"):
        assert line in out, (line, out)
    assert "r3 = r3;" not in out and "0x0u" not in out, out


def test_selects_written_for_their_conversion():
    """A converted select's arms are written for the conversion where each arm converts as before
    through the select's type (uart_getc's result, nested flags), one kept to the select's type where it
    is not converted, and a select on a constant condition is its arm (the lifters' guard of a division
    by a constant); a 64-bit target keeps the 32-bit all-ones arm (-1 would be 64 bits of ones)."""
    out = polished("""u32 f(u32 r0, u32 r1)
{
    u32 n, z, r5, r6;
    u64 w;
    n = r1 & 1;
    z = r1 >> 1;
    r5 = 0xau ? r1 / 0xau : 0;
    r6 = (n ? 0x1u : 0x0u) & r0;
    w = n ? ~0x0u : 0x1u;
    g(r5, r6 + (u32)w);
    if (r1 == 0) return n ? (u32)(u8)r0 : 0xffffffffu;
    return r0 != 0 ? 0x0u : !z ? 0x1u : 0x0u;
}
""")
    for line in ("r5 = r1 / 10;", "r6 = (n ? 1u : 0u) & r0;", "w = n ? ~0x0u : 1;",
                 "if (r1 == 0) return n ? (u8)r0 : -1;", "return r0 != 0 ? 0 : !z ? 1 : 0;"):
        assert line in out, (line, out)


def test_characters_in_a_function_that_handles_text():
    """Bytes loaded from memory compared with printable or escape codes print them as characters, so
    do a byte variable's sum with one (c - '0') and a constant stored into a byte, escaped where C needs
    it; one character alone is no text (a byte field compared with 32)."""
    out = polished("""u32 f(u32 r0, u32 r1)
{
    u32 r2, r3;
    r2 = *(volatile u8 *)r0;
    if (r2 == 0x20u || r2 == 9) return 1;
    r3 = (u32)*(volatile u8 *)(r0 + 1);
    if (r3 != 0x3du) return ~0x1u;
    if (r3 - 0x30u <= 9) return r3 - 0x30u;
    *(volatile u8 *)r1 = 0x2du;
    *(volatile u8 *)(r1 + 1) = 0x5cu;
    *(volatile u8 *)(r1 + 2) = 0x27u;
    *(volatile u8 *)(r1 + 3) = 0x0u;
    return 0;
}
""")
    for line in ("if (r2 == ' ' || r2 == '\\t') return 1;", "r3 = *(volatile u8 *)(r0 + 1);",
                 "if (r3 != '=') return -2;", "if (r3 - '0' <= 9) return r3 - '0';", "*(volatile u8 *)r1 = '-';",
                 "*(volatile u8 *)(r1 + 1) = '\\\\';", "*(volatile u8 *)(r1 + 2) = '\\'';",
                 "*(volatile u8 *)(r1 + 3) = 0;"):
        assert line in out, (line, out)
    assert cparse.parse(HEAD + out).function("f") is not None       # (still the dialect)
    one = polished("""u32 f(u32 r0)
{
    u32 r2;
    r2 = *(volatile u8 *)r0;
    if (r2 == 0x20u) return 1;
    return 0;
}
""")
    assert "r2 == 0x20u" in one, one


def test_a_callees_result_stored_as_a_byte_holds_characters():
    """read_line's form: uart_getc's result, copied, stored into the buffer as a byte, compared with
    the line ends."""
    out = polished("""u32 f(u32 r0, u32 r1)
{
    u32 r2, r3;
    r2 = g(r0, r1);
    r3 = r2;
    if (r3 == 0xdu) return 0;
    if (r3 == 0xau) return 1;
    *(volatile u8 *)r1 = (u8)r3;
    return 2;
}
""")
    assert "if (r3 == '\\r') return 0;" in out and "if (r3 == '\\n') return 1;" in out, out
    assert "*(volatile u8 *)r1 = r3;" in out, out


def test_radix_by_use():
    """Decimal for a bound in an ordered comparison, a multiplier, and the place they or two stored
    numbers make a number (line numbers stored into one error variable, a value compared with them);
    hex for a device register, a reciprocal multiplier, a mask-like bound, a pointer that is counted
    (an address walked), a single store."""
    out = polished("""u32 f(u32 r0, u32 r1)
{
    u32 r2, r3, r6;
    if (r0 < 0x2f7u) g_20000930 = 0x2f7u;
    if (r1 > 0x6bu) g_20000930 = 0x2ffu;
    io_40004004 = 0x2f7u;
    io_40004004 = 0x2ffu;
    r2 = r0 * 0xau;
    r3 = r0 * 0x5556u >> 16;
    if (r3 <= 0xffffu) r2 = 0;
    r6 = 0x908u;
    r3 = r3 + *(volatile u8 *)r6;
    r6 = r6 + 1;
    return r2 + r3;
}
""")
    for line in ("if (r0 < 759) g_20000930 = 759;", "if (r1 > 107) g_20000930 = 767;", "io_40004004 = 0x2f7u;",
                 "r2 = r0 * 10;", "r3 = r0 * 0x5556u >> 16;", "if (r3 <= 0xffffu) r2 = 0;", "r6 = 0x908u;", "r6++;"):
        assert line in out, (line, out)
    single = polished("u32 f(u32 r0)\n{\n    if (r0 != 0) g_20000930 = 0xb4u;\n    return 0;\n}\n")
    assert "g_20000930 = 0xb4u;" in single, single
    # (an element's index is a number: FreeRTOS's vTaskSwitchContext computes the priority it indexes
    # its ready lists with as 31 - clz, which read 0x1fu once the element's address was no longer the
    # sum 20 * r4 + ...)
    index = polished("u32 f(u32 r0)\n{\n    u32 r4;\n    r4 = 0x1fu - (r0 >> 3);\n    return ((volatile u32 *)0x20000100u)[r4];\n}\n")
    assert "r4 = 31 - (r0 >> 3);" in index, index


def test_masks_stay_masks():
    """All ones in a variable read only by bit operations and shifts (ram_crc's crc: ^=, >>, &, ~) is
    0xffffffffu, not -1, whatever the lifter wrote (~0x0u); so is a constant for a callee's parameter
    that it uses only as a mask (masks), a complement there stays ~N; without that a parameter's -1
    stays a value; a counter that & also reads is no mask (its -1 is a value), and the value of &= is a
    mask's operand (m's ~0x20u)."""
    head = HEAD + "extern u32 clear_bits(u32, u32);\n"
    text = """u32 f(u32 r0, u32 r1)
{
    u32 r2, r3, r4, m;
    r3 = ~0x0u;
    r3 ^= r0;
    r3 = (r3 >> 1) ^ (0xedb88320u & ((s32)(r3 << 31) >> 31));
    r4 = ~0x0u;
    r4++;
    r2 = r4 & 1;
    m = ~0x20u;
    r1 &= m;
    clear_bits(~0x0u, ~0x20u);
    g(~0x0u, r2);
    return ~r3 + r1;
}
"""
    out = polished(text, head=head)
    for line in ("r3 = 0xffffffffu;", "r4 = -1;", "m = ~0x20u;", "clear_bits(-1, -33);", "g(-1, r2);"):
        assert line in out, (line, out)
    tree = cparse.parse(head + text)
    fn = tree.function("f")
    polish.polish(tree, fn, io=IO, masks={"clear_bits": [0, 1]})
    out = cparse.unparse(fn, notes=False, style="readable")
    assert "clear_bits(0xffffffffu, ~0x20u);" in out and "g(-1, r2);" in out, out


def test_parameters_used_only_as_masks():
    """mask_params: a parameter read only by &, |, ^, ~ (bic: & ~), also through a copy and &=, is a
    mask; one compared, stored, counted, shifted (r1 << 2: a count scaled), a shift's count (ARM's 1 <<
    (r2 & 0xffu)) or never read is none, and one only returned besides (r0) is none either; one handed to
    a callee's mask parameter is one (the next round); every function is listed."""
    tree = cparse.parse(HEAD + """extern u32 bits_clear(u32, u32, u32);
u32 bits_pass(u32 r0, u32 r1, u32 r2, u32 r3)
{
    if (r1 == 0) return 0;
    g_20000930 = r2;
    return bits_clear(0, r0, r3);
}
u32 bits_clear(u32 r0, u32 r1, u32 r2)
{
    u32 r4;
    r4 = r1;
    g_20000930 &= ~r4;
    g_20000930 |= r2 & 0xf0u;
    return r0;
}
u32 bits_count(u32 r0, u32 r1, u32 r2)
{
    r0++;
    g_20000930 = 1u << (r2 & 0xffu);
    return g(r1 << 2, r0 & 1);
}
""")
    trees = [(tree, fn, {}) for fn in tree.items if isinstance(fn, cparse.Function)]
    assert polish.mask_params(trees) == {"bits_clear": [1, 2], "bits_pass": [0, 3], "bits_count": []}
    assert polish.mask_params(trees, rounds=1)["bits_pass"] == []     # (its callee's masks come after it)


def test_a_mask_moved_by_a_shift_stays_a_mask():
    """mask_params since 2026-10-25: a parameter used as a mask in one arm and moved to another field
    before its mask use in the other ((r1 >> 8) & REG, a port's read-modify-write) is a mask, and so is the
    value it sets there (r2 | ..., r2 >> 8 | ...); a caller's constants for them print as masks (~0xfu,
    0xffffffffu), never as negative numbers; a parameter only shifted into a mask's place ((r0 << 2) &
    0x3cu: a count scaled) is none, nor is one compared besides."""
    tree = cparse.parse(HEAD + """u32 port_set(u32 r0, u32 r1, u32 r2)
{
    if (r0 == 0)
    {
        g_20000930 = r2 | (r1 & g_20000930);
        return 0;
    }
    g_20000934 = (r2 >> 8) | ((r1 >> 8) & g_20000934);
    return (r0 << 2) & 0x3cu;
}
u32 scaled(u32 r0)
{
    return (r0 << 2) & 0x3cu;
}
""")
    trees = [(tree, fn, {}) for fn in tree.items if isinstance(fn, cparse.Function)]
    masks = polish.mask_params(trees)
    assert masks == {"port_set": [1, 2], "scaled": []}, masks
    head = HEAD + "extern u32 port_set(u32, u32, u32);\nextern u32 scaled(u32);\n"
    text = """u32 f(u32 r0)
{
    port_set(2u, ~0xfu, 0u);
    port_set(0u, ~0x0u, 1u);
    return scaled(~0x0u);
}
"""
    caller = cparse.parse(head + text)
    fn = caller.function("f")
    polish.polish(caller, fn, io=IO, masks=masks)
    out = cparse.unparse(fn, notes=False, style="readable")
    for line in ("port_set(2, ~0xfu, 0);", "port_set(0, 0xffffffffu, 1);", "return scaled(-1);"):
        assert line in out, (line, out)


def test_one_radix_per_object():
    """The elements of a local array (a colour triple) come out in one radix: decimal where the object
    holds numbers (one element given two numbers), hex where nothing says so (the mvn of 150 stored into a
    byte, ~0x69u, is the byte it leaves, 0x96u); sizes stored through two pointers after the argument was
    compared with them (note_pick: x <= 63 for x < 64) are decimal, the bound 255 beside 63 too, and a sum
    in a sum stored into a number takes the place too (one constant); a bound that is a run of ones
    alone (0xffu), a shifted run (0xff00u) and a pad character's ' ' and '0' stay as they were."""
    out = polished("""u32 f(u32 r0)
{
    volatile u8 stack12[8];
    stack12[0] = ~0x69u;
    stack12[1] = 0xf0u;
    stack12[2] = r0 == 0 ? 0x1eu : 0xfu;
    return g((u32)stack12, 0);
}
""")
    for line in ("stack12[0] = 0x96u;", "stack12[1] = 0xf0u;", "stack12[2] = r0 == 0 ? 0x1eu : 0xfu;"):
        assert line in out, (line, out)
    text = """u32 f(u32 r0)
{
    volatile u8 stack12[8];
    stack12[0] = (u8)0x96u;
    stack12[1] = (u8)0x35u;
    stack12[2] = (u8)(r0 == 0 ? 0x1eu : 0xfu);
    if (stack12[2] > 0x14u) r0 = r0 + 1;
    return g((u32)stack12, r0);
}
"""
    out = polished(text)                    # (before: 0x96u, 0x35u, then 15 for the element compared in order)
    for line in ("stack12[0] = 150;", "stack12[1] = 53;", "stack12[2] = r0 == 0 ? 30 : 15;", "stack12[2] > 20"):
        assert line in out, (line, out)
    out = polished("""u32 f(u32 r0, u32 r1, u32 r2)
{
    u32 r0_1, r3;
    if (r0 <= 0x3fu)
    {
        r0_1 = 0;
        r3 = 0x40u;
    }
    else if (r0 > 0xffu)
    {
        r0_1 = 0x100u;
        r3 = 0x400u;
    }
    else
    {
        r0_1 = 0x40u;
        r3 = 0x100u;
    }
    *(volatile u32 *)r1 = r0_1;
    *(volatile u32 *)r2 = r3;
    return r0_1;
}
""")
    for line in ("if (r0 <= 63)", "else if (r0 > 255)", "r0_1 = 256;", "r3 = 1024;", "r0_1 = 64;", "r3 = 256;"):
        assert line in out, (line, out)
    out = polished("""u32 f(u32 r0, u32 r1, u32 r2)
{
    u32 r0_1, r3;
    if (r0 > 0x3fu)
    {
        r0_1 = r0 < 0x100u ? 0x40u : 0x100u;
        r3 = r0 < 0x100u ? 0x100u : 0x400u;
    }
    else
    {
        r0_1 = 0;
        r3 = 0x40u;
    }
    *(volatile u32 *)r1 = r0_1;
    *(volatile u32 *)r2 = r3;
    return r0_1;
}
""")                                        # (GCC 13's selects of the sizes: the arms are what the places get)
    for line in ("r0_1 = r0 < 256 ? 64 : 256;", "r3 = r0 < 256 ? 256 : 1024;", "r3 = 64;"):
        assert line in out, (line, out)
    out = polished("""u32 f(u32 r0)
{
    g_20000930 = 0x41c64e6du * g_20000930 + 0x3000u + 0x39u;
    if (g_20000930 > 0x3e8u) r0 = 1;
    return r0;
}
""")                                        # (GCC -O0's 12345 in two adds: one number, not 0x3000u + 57)
    assert "g_20000930 = 0x41c64e6du * g_20000930 + 12345;" in out and "> 1000" in out, out
    out = polished("""u32 f(u32 r0, u32 r1)
{
    u32 r8;
    if (r0 <= 0xffu) return 1;
    if (r1 > 0xff00u) return 2;
    r8 = 0x20u;
    if (r0 == 1) r8 = 0x30u;
    return g(r8, r0);
}
""")
    for line in ("if (r0 <= 0xffu) return 1;", "if (r1 > 0xff00u) return 2;", "r8 = 0x20u;", "r8 = 0x30u;"):
        assert line in out, (line, out)


class _Texts:
    """A stand-in for dread.constants Pointed: texts start at 0x1e2 and 0x215, no function's entry."""

    def function(self, value):
        return None

    def text(self, value):
        return ("Error", False) if value in (0x1e2, 0x215) else None

    def begins(self, value):
        return True


def test_addresses_and_bit_patterns_hold_no_numbers():
    """An object given only texts' starts (FreeRTOS clang -O2's status message: its texts lie at small
    addresses on an image at 0) holds addresses and stays hex, though it is given two numbers (decimal
    without the image's texts); one where a value falls on a text beside a number holds numbers (line
    numbers on such an image); a register that holds an event group's 0x55 and then a line number takes
    no decimal from the line numbers stored elsewhere (its 0x55 stays a pattern)."""
    text = """u32 f(u32 r0)
{
    u32 r6;
    r6 = 0x1e2u;
    if (r0 == 1) r6 = 0x215u;
    g_20000930 = r6;
    return 0;
}
"""
    assert "r6 = 482;" in polished(text) and "r6 = 533;" in polished(text)
    tree = cparse.parse(HEAD + text)
    fn = tree.function("f")
    polish.polish(tree, fn, io=IO, pointed=_Texts())
    out = cparse.unparse(fn, notes=False, style="readable")
    assert "r6 = 0x1e2u;" in out and "r6 = 0x215u;" in out, out
    tree = cparse.parse(HEAD + text.replace("0x215u", "0x2f7u"))       # (a line number on a text by chance)
    fn = tree.function("f")
    polish.polish(tree, fn, io=IO, pointed=_Texts())
    out = cparse.unparse(fn, notes=False, style="readable")
    assert "r6 = 482;" in out and "r6 = 759;" in out, out
    out = polished("""u32 f(u32 r0)
{
    u32 r0_2;
    if (g(r0, 0) != 0) g_20000930 = 0x35bu;
    if (g(r0, 1) != 0) g_20000930 = 0x365u;
    r0_2 = g(r0, 2);
    if (r0_2 != 0x55u)
    {
        r0_2 = 0x379u;
        g_20000930 = 0x379u;
    }
    return r0_2;
}
""")
    for line in ("g_20000930 = 859;", "g_20000930 = 869;", "if (r0_2 != 0x55u)", "r0_2 = 0x379u;", "g_20000930 = 889;"):
        assert line in out, (line, out)


def test_an_int_of_16_bits():
    """MSP430's widths (int 16 bits, u32 an unsigned long): -1 for a u16's 0xffffu and a u32's
    0xffffffffu, not for a u32's 0xffffu."""
    out = polished("""u16 f(u16 r12, u16 r13)
{
    u16 r14;
    u32 w;
    w = (u32)r12;
    if (r12 == 0xffffu) return 0xfffeu;
    if (w == 0xffffffffu) r14 = 0x0u;
    r14 = r13 + 0xfff8u;
    if (w == 0xffffu) r14 = 2;
    return r14;
}
""", int_bits=16)
    for line in ("if (r12 == -1) return -2;", "if (w == -1) r14 = 0;", "r14 = r13 - 8;", "if (w == 0xffffu) r14 = 2;"):
        assert line in out, (line, out)


def test_the_comparison_sets_are_exact():
    """cmp_set against brute force: every value of 8- and 16-bit operand types (and the edges of the
    32-bit ones) compared with constants of every kind of literal, both ways round, with C's
    conversions (cexpr)."""
    ctx = cexpr.Ctx()
    consts = [(0, (32, True)), (1, (32, False)), (0x20, (32, True)), (0x7f, (32, False)), (0xff, (32, False)),
              (-1, (32, True)), (-2, (32, True)), (0xffffffff, (32, False)), (0xfffffffe, (32, False)),
              (0x8000, (32, True)), (-0x8000, (32, True)), (0xffff, (32, False)), (-129, (32, True))]
    for bits, signed in itertools.product((8, 16), (False, True)):
        ta = (bits, signed)
        lo, hi = polish.int_range(ta)
        for (c, tc), op, left in itertools.product(consts, polish.COMPARE, (False, True)):
            k = (cexpr.wrap(c, tc), tc)
            t = cexpr.common(ta, tc, ctx)
            w = cexpr.wrap(k[0], t)
            truth = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, "<": lambda a, b: a < b,
                     "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}[op]
            want = [a for a in range(lo, hi + 1)
                    if (truth(w, cexpr.wrap(a, t)) if left else truth(cexpr.wrap(a, t), w))]
            got = [a for x, y in polish.cmp_set(op, ta, k, left, ctx) for a in range(x, y + 1)]
            assert got == want, (ta, k, op, left)


# --- the printed forms compiled and run ---

PROBE = ("typedef unsigned char u8;\ntypedef signed char s8;\ntypedef unsigned short u16;\ntypedef signed short s16;\n"
         "typedef unsigned int u32;\ntypedef signed int s32;\ntypedef unsigned long long u64;\n"
         "typedef signed long long s64;\nstatic u32 trace;\n"
         "u32 sink(u32 a, u32 b)\n{\n    trace = trace * 33u + a + 5u * b;\n    return trace ^ 0x5au;\n}\n")
VARS = {"b0": "u8", "c0": "s8", "h0": "u16", "k0": "s16", "u0": "u32", "u1": "u32", "i0": "s32", "w0": "u64"}
UNSIGNED = ["b0", "h0", "u0", "u1", "w0"]


def random_constant(rng):
    """A constant as the lifters and the stages spell them: hex with u, a complement, a cast of a
    literal, a decimal, a character's code, a value near a type's top or its sign bit."""
    k = rng.choice([0, 1, 2, 7, 9, 10, 0x20, 0x30, 0x3d, 0x41, 0x7f, 0x80, 0xff, 0x2f7, 0xfffe, 0xffff,
                    0x10000, 0x7fffffff, 0x80000000])
    form = rng.random()
    if form < 0.3:
        return "~0x%xu" % rng.choice([0, 1, 2, 7, 0x1f, 0xff, 0x20])
    if form < 0.4:                          # (not s32: h0 + (s32)0x7fffffffu overflows int, no test of anything)
        return "(%s)0x%xu" % (rng.choice(["u8", "u16", "s8", "s16", "u32"]), k)
    if form < 0.45:
        return "(u32)((u64)0x0u + (u32)~0x%xu + 1)" % rng.choice([0, 7, 8])
    if form < 0.5:
        return "%d" % (k & 0x7fff)
    if form < 0.55:
        return "0x%xull" % k
    return "0x%xu" % k


def random_polish_function(rng, name, steps=12):
    """A function in the readable dialect whose constants stand in every use the pass knows: assigned
    to every width, compared (equality, order, signed order) with every width, operands of arithmetic,
    masks, shifts, selects with constant arms, increments, truncated 64-bit sums, a prototyped call; its
    result mixes every variable and the calls' trace. No undefined behaviour in the original (signed
    variables are only compared, converted and assigned; a byte's or a halfword's sum with a constant
    stays inside int): where the original has it, any result is C's, and the comparison would test
    nothing."""
    lines = ["u64 %s(u32 a0, u32 a1, u32 a2, u32 a3)" % name, "{",
             "    u8 b0; s8 c0; u16 h0; s16 k0; u32 u0, u1, r; s32 i0; u64 w0; u8 arr[4];",
             "    b0 = (u8)a0; c0 = (s8)a1; h0 = (u16)a2; k0 = (s16)a3; u0 = a0; u1 = a1; i0 = (s32)a2;",
             "    w0 = ((u64)a3 << 32) | a0; r = 0; arr[0] = (u8)a3; arr[1] = 0; arr[2] = 0; arr[3] = (u8)a1;"]
    names = list(VARS)
    for _ in range(steps):
        x, y = rng.choice(names), rng.choice(names)
        uy = rng.choice(UNSIGNED)
        k = random_constant(rng)
        kind = rng.randrange(15)
        if kind == 0:
            lines.append("    %s = %s;" % (x, k))
        elif kind == 1:
            op = rng.choice(["+", "-", "&", "|", "^"])
            lines.append("    %s = %s %s %s;" % (x, uy, op, k))
        elif kind == 2:
            lines.append("    %s = %s - %s;" % (x, k, uy))
        elif kind == 3:
            factor = rng.choice([3, 0xa, 0x30, 0x5556])
            lines.append("    %s = %s * 0x%xu;" % (x, rng.choice(["u0", "u1", "w0"]), factor))
        elif kind == 4:
            op = rng.choice(polish.COMPARE)
            other = rng.choice([y, "(s32)" + rng.choice(["u0", "u1"])])
            lines.append("    if (%s %s %s) %s = %s;" % (other, op, k, x, rng.choice(names)))
        elif kind == 5:
            op = rng.choice(polish.COMPARE)
            lines.append("    %s = %s %s %s ? %s : %s;" % (x, y, op, k, random_constant(rng), random_constant(rng)))
        elif kind == 6:
            lines.append("    %s = %s != 0 ? %s : %s;" % (x, y, uy, k))
        elif kind == 7:
            lines.append("    %s = (%s)(%s + %s);" % (uy, VARS[uy], uy, rng.choice(["1", "0x1u", k])))
        elif kind == 8:
            lines.append("    u0 = (u32)((u64)%s + %s + 0);" % (rng.choice(["u1", "(u32)b0", "(u32)h0"]), k))
        elif kind == 9 and rng.random() < 0.5:
            lines.append("    u0 = (u32)((u64)0x0u + (u32)~%s + 1) + u1;" % rng.choice(["u0", "u1", "(u32)h0"]))
        elif kind == 9:                     # (the lifters' subtraction of a constant: cmp's x - k)
            lines.append("    u0 = (u32)((u64)%s + (u32)~0x%xu + 1);" % (rng.choice(["u1", "(u32)b0"]),
                                                                         rng.choice([0, 3, 0x5f, 0xff])))
        elif kind == 10:
            lines.append("    r = r ^ sink(%s, %s);" % (k, rng.choice(names)))
        elif kind == 11:
            lines.append("    %s = %s << 0x%xu;" % (x, uy, rng.randrange(8)))
        elif kind == 12:                    # (an array's elements: one object)
            lines.append("    arr[%d] = %s;" % (rng.randrange(4), k))
        elif kind == 13:
            lines.append("    if (arr[%d] %s %s) %s = arr[%d];" % (rng.randrange(4), rng.choice(polish.COMPARE), k, x,
                                                                  rng.randrange(4)))
        else:                               # (copies join objects; a store through a pointer)
            lines.append("    u1 = %s; *(volatile u32 *)&u0 = %s;" % (rng.choice(["u0", "(u32)arr[1]", k]), k))
    lines.append("    return w0 ^ ((u64)u0 << 3) ^ ((u64)u1 << 7) ^ ((u64)(u32)i0 << 11) ^ ((u64)b0 << 17) ^ "
                 "((u64)(u8)c0 << 23) ^ ((u64)h0 << 29) ^ ((u64)(u16)k0 << 37) ^ ((u64)r << 41) ^ ((u64)trace << 13) ^ "
                 "((u64)arr[0] << 5) ^ ((u64)arr[1] << 19) ^ ((u64)arr[2] << 31) ^ ((u64)arr[3] << 47);")
    lines.append("}")
    return "\n".join(lines) + "\n"


@pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")
def test_random_functions_polished_compute_the_same(tmp_path):
    """Random functions (random_polish_function), polished, compiled with the host's C compiler beside
    the originals and called with boundary and random arguments: the same results. Every rule is among
    the changes (negative numbers, decimal, folded casts and constant expressions, narrowed sums and
    their negation, increments, masks for a callee's mask parameter (half the functions), an array's
    elements in one radix; a character literal is an int constant of its code, so the comparisons and
    stores here stand for the characters' too), and most functions change."""
    rng = random.Random(23)
    count, originals, results, changed = 250, [], [], 0
    for i in range(count):
        text = random_polish_function(rng, "orig_%d" % i, steps=rng.randint(6, 16))
        originals.append(text)
        tree = cparse.parse("extern u32 sink(u32, u32);\n" + text.replace("orig_", "pol_"))
        fn = tree.function("pol_%d" % i)
        changed += polish.polish(tree, fn, masks={"sink": [0]} if i % 2 else None)
        results.append(cparse.unparse(fn, notes=False, style="readable"))
    source = tmp_path / "polish.c"
    source.write_text(PROBE + "".join(originals) + "\n".join(results) + "\n" + "".join(
        "u64 call_%s_%d(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n    trace = 0;\n    return %s_%d(a0, a1, a2, a3);\n}\n"
        % (k, i, k, i) for i in range(count) for k in ("orig", "pol")))
    lib = tmp_path / "polish.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O1", "-w", "-o", str(lib), str(source)], check=True)
    so = ctypes.CDLL(str(lib))
    edges = [0, 1, 2, 9, 0x20, 0x7f, 0x80, 0xff, 0x100, 0x7fff, 0x8000, 0xffff, 0x10000, 0x7fffffff, 0x80000000,
             0xfffffffe, 0xffffffff]
    for i in range(count):
        a, b = getattr(so, "call_orig_%d" % i), getattr(so, "call_pol_%d" % i)
        for f in (a, b):
            f.restype, f.argtypes = ctypes.c_uint64, [ctypes.c_uint32] * 4
        for _ in range(40):
            args = [rng.choice(edges + [rng.getrandbits(32)]) for _ in range(4)]
            assert a(*args) == b(*args), (i, args, originals[i], results[i])
    assert changed > count * 0.9, changed
    text = "".join(results)
    for form in ("= -", "== -", "sink(-", "++;", " += ", "? 1 : 0", " - u0;"):
        assert form in text, form
    assert re.search(r"u0 = (u1|b0) - \d+;", text)       # (the lifters' subtraction of a constant)
    for gone in ("(u64)0x0u +", "= 0x0u;", "= 0x1u;", " + 0;"):
        assert gone not in text, gone


# --- gizmo and FreeRTOS ---

def gizmo_texts(variant, names):
    from dread import lift, readable, regflow
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir(variant))
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    context = readable.Context(target, image, program)
    return {n: readable.transform(n, lifter.lift(program.by_name[n]).text, context=context) for n in names}


def code(text):
    return re.sub(r"\s*/\*.*?\*/", "", text)


@needs_gcc
def test_gizmos_text_functions_read_as_their_source():
    """gizmo-a32's parse_kv returns -1, -2 and -3 (its source's errors) and compares characters; read_line
    compares the line ends and terminates the buffer with 0; cmd_mode returns 0 or -1; strncpy stores
    (through r3++) and counts without casts. Characters on the other builds too (Thumb, RISC-V)."""
    got = {n: code(t) for n, t in gizmo_texts("a32", ["parse_kv", "read_line", "cmd_mode", "strncpy"]).items()}
    kv = got["parse_kv"]
    for want in ("= -1;", "= -2;", "== -1 ? -3 : 0;", "== '\\t' || ", " == ' '", " != '=') "):
        assert want in kv, (want, kv)
    for gone in ("~0x0u", "~0x1u", "~0x2u", "== 0x20u", "0x3du", "(u8)"):
        assert gone not in kv, (gone, kv)
    assert "(r5 & ~0x20u) - 'A'" in kv, kv  # (a mask keeps its complement; the letter's code beside a byte)
    line = got["read_line"]
    assert re.search(r"\w+ != '\\r'", line) and re.search(r"\w+ == '\\n'", line), line
    assert re.search(r"\*\(volatile u8 \*\)\(\w+ \+ r4\) = 0;", line) and "= -1;" in line, line
    assert "-1;" in got["cmd_mode"] and "~0x0u" not in got["cmd_mode"], got["cmd_mode"]
    copy = got["strncpy"]
    assert "*(volatile u8 *)r3++ = 0;" in copy and "r2--;" in copy and "(u8)" not in copy, copy
    for variant in ("m3", "m4", "rv32"):
        kv = code(gizmo_texts(variant, ["parse_kv"])["parse_kv"])
        assert "' '" in kv and "'\\t'" in kv and "'='" in kv and "~0x1u" not in kv, (variant, kv)


def test_gizmos_crc_starts_from_all_ones(variant):
    """gizmo's ram_crc (crc = 0xFFFFFFFFu, then ^=, >>, & and ~ only) writes its start as 0xffffffffu on
    every build, never -1 (ARM state's mvn ~0x0u, Thumb-2's and RISC-V's all ones)."""
    crc = code(gizmo_texts(variant, ["ram_crc"])["ram_crc"])
    m = re.search(r"(?m)^    (\w+) = 0xffffffffu;", crc)
    assert m and not re.search(r"\b%s = -1;" % m.group(1), crc), crc


FRAME = ("typedef struct\n{\n    u32 f_0;\n    u32 f_4;\n    u32 f_8;\n    u32 f_c;\n    u8 pad_10[8];\n"
         "} __attribute__((packed, aligned(8))) stack24_t;\n")


def test_freertos_forms():
    """The forms the review found in FreeRTOS's readable C: xTimerPendFunctionCallFromISR's command
    (tmrCOMMAND_EXECUTE_CALLBACK_FROM_ISR, -2) stored into the message on the frame, prvCheckExpected-
    TimeIsWithinAnAcceptableMargin's line numbers (__LINE__), xTimerCreate's list item inside the timer,
    vPortFree's step back over the block's header, eTaskGetState's states."""
    out = polished("""u32 xTimerPendFunctionCallFromISR(u32 r0, u32 r1, u32 r2, u32 r3)
{
    volatile stack24_t stack24;
    stack24.f_0 = ~0x1u; stack24.f_4 = r0;
    stack24.f_8 = r1; stack24.f_c = r2;
    return g(r3, (u32)&stack24.f_0);
}
""", name="xTimerPendFunctionCallFromISR", head=HEAD + FRAME)
    assert "stack24.f_0 = -2;" in out and "~0x" not in out, out
    out = polished("""u32 prvCheckExpectedTimeIsWithinAnAcceptableMargin(u32 r0, u32 r1)
{
    u32 r0_2, r4;
    r0_2 = g(r0, r1) - r0;
    r4 = r1 + 7;
    if (r0_2 < r1) g_20000930 = 0x2f7u;
    if (r4 < r0_2) g_20000930 = 0x2ffu;
    return r0_2;
}
""", name="prvCheckExpectedTimeIsWithinAnAcceptableMargin")
    assert "g_20000930 = 759;" in out and "g_20000930 = 767;" in out, out
    out = polished("""u32 f(u32 r0, u32 r1)
{
    u32 r0_1;
    u8 sl9;
    r0_1 = (volatile struct s_50 *)g(0x28u, 0);
    g((u32)((u64)(u32)r0_1 + 4 + 0), 0);
    g((u32)((u64)0x0u + (u32)~0x8u + 1) + r0, 0);
    sl9 = (u8)0x0u;
    if (r1 == 1) sl9 = (u8)0x2u;
    return (u32)sl9;
}
""", raw=["struct s_50\n{\n    u32 f_0;\n    u8 pad_4[32];\n    u8 f_24;\n};"], pointers={"r0_1": "s_50"})
    for line in ("g((u32)r0_1 + 4, 0);", "g(r0 - 8, 0);", "sl9 = 0;", "if (r1 == 1) sl9 = 2;", "return sl9;"):
        assert line in out, (line, out)


# --- a program of our own through the gate ---

TALLY = """typedef unsigned int u32;
typedef unsigned char u8;
typedef int s32;
%s
u32 tally_flags;
u32 tally_sizes[2];
u8 tally_rgb[3];
u8 tally_ring[8];
u32 tally_head, tally_tail;
NOIPA void tally_clear(u32 keep, u32 clear) { tally_flags = (tally_flags & keep) & ~clear; }
NOIPA void tally_pick(u32 n, u32 *lo, u32 *hi)
{
    if (n < 64) { *lo = 0; *hi = 64; }
    else if (n < 256) { *lo = 64; *hi = 256; }
    else { *lo = 256; *hi = 1024; }
}
NOIPA void tally_paint(const u8 *c) { tally_rgb[0] = c[0]; tally_rgb[1] = c[1]; tally_rgb[2] = c[2]; }
NOIPA void tally_colour(u32 dark)
{
    volatile u8 c[3];
    c[0] = 150; c[1] = 53; c[2] = dark ? 15 : 20;
    tally_paint((const u8 *)c);
}
NOIPA int tally_get(void)
{
    int c;
    if (tally_head == tally_tail) return -1;
    c = tally_ring[tally_tail];
    tally_tail = (tally_tail + 1) & 7;
    return c;
}
NOIPA u32 tally_crc(const u8 *p, u32 n)
{
    u32 crc = 0xFFFFFFFFu;
    int k;
    while (n--) {
        crc ^= *p++;
        for (k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0xEDB88320u & -(crc & 1));
    }
    return ~crc;
}
int main(void)
{
    int c;
    tally_clear(0xffffffffu, 0);
    tally_clear(~0x20u, 0xffffffffu);
    tally_pick(tally_flags, &tally_sizes[0], &tally_sizes[1]);
    tally_colour(tally_flags & 1);
    c = tally_get();
    if (c >= 0) tally_flags |= 2;
    tally_sizes[0] = tally_crc(tally_rgb, 3);
    return 0;
}
"""


def _kind(kind):
    from test_statics import needs_kind
    return needs_kind(kind)


@pytest.mark.slow
@pytest.mark.parametrize("kind", [pytest.param(k, marks=_kind(k)) for k in ("a32", "m3", "rv32")])
def test_masks_radix_and_negative_values_through_the_gate(kind, tmp_path):
    """tally, built with GCC -Os for ARM state, Thumb-2 and RISC-V: masks for a callee's mask parameters
    (main's tally_clear(0xffffffffu, 0), never -1), the crc's all ones as 0xffffffffu, sizes stored
    through two pointers in one radix with the bounds that select them (64, 256, 1024; 63, 255), a colour
    triple in one radix (150, 53, 15, 20), and a ring's -1 or byte in an s32 variable of an s32 function.
    The gate (dread readable --check) accepts every function so."""
    from conftest import NOIPA_DEF
    from test_statics import kind_target
    from dread import build, lift, readable, regflow
    from dread.program import Program
    target = kind_target(tmp_path, kind, TALLY % NOIPA_DEF)
    image = target.image()
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    lifted = sorted(build.read_names(image.table("lifted")))
    assert accepted == lifted and "tally_get" in lifted, (accepted, lifted, lines)
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = readable.Context(target, image, program, flow)
    got = {n: code(readable.transform(n, lifter.lift(program.by_name[n]).text, context=context)) for n in lifted}
    main = got["main"]
    assert re.search(r"tally_clear\(0xffffffffu, 0\);", main) and not re.search(r"tally_clear\(-", main), main
    assert re.search(r"tally_clear\((~0x20u|0xffffffdfu), 0xffffffffu\);", main), main
    pick = got["tally_pick"]                # (GCC 13 writes x < 256 where GCC 12 and older wrote x <= 255, and
    for want in ("63", "64", "256", "1024"):  # selects of the sizes on Thumb-2 and in ARM state)
        assert want in pick, (want, pick)
    assert re.search(r"(<=|<|>=|>) (255|256)\b|\b(255|256) (<=|<|>=|>) ", pick), pick
    assert not re.search(r"0x(3f|ff|40|100|400)u", pick), pick
    colour = got["tally_colour"]
    assert "= 150;" in colour and "= 53;" in colour and not re.search(r"0x96u|0x35u|~0x69u", colour), colour
    ring = got["tally_get"]
    unsigned = {n.strip() for line in re.findall(r"(?m)^    u32 ([\w, ]+);$", ring) for n in line.split(",")}
    assert "s32 tally_get(void)" in ring and not any(re.search(r"\b%s = -1;" % n, ring) for n in unsigned), ring
    crc = got["tally_crc"]                 # (ARM state's -(crc & 1) through a branch stays -1: a negation)
    assert re.search(r"(?m)^    \w+ = 0xffffffffu;", crc), crc


def test_declarations_nobody_names_any_more_go_at_the_end():
    """drop_unused_declarations: a variable a pass after the clean-up folded away is no longer declared;
    one an asm operand names, one with an initial value that has effects and a parameter stay."""
    from dread import cparse
    from dread.polish import drop_unused_declarations
    text = """#include "dread.h"
extern u32 g(u32);
u32 f(u32 r0, u32 r1)
{
    u32 r2, r3, r4;
    u32 r5 = g(1);
    u32 r6 = 7;
    r2 = r0 + 1;  /* 4: adds r2, r0, #1 */
    __asm__ volatile ("" : : "r"(r4));
    return r2;  /* 8: bx lr */
}
"""
    tree = cparse.parse(text)
    fn = tree.function("f")
    assert drop_unused_declarations(fn)
    decls = [d.name for s in fn.body.body if isinstance(s, cparse.Decl) for d in s.items]
    assert decls == ["r2", "r4", "r5"], decls          # (r3 and r6 named nowhere: gone; r5's call stays)
    assert [n for _, n in fn.params] == ["r0", "r1"]
    assert not drop_unused_declarations(fn)


WIDE = ("typedef unsigned int u32;\ntypedef int s32;\ntypedef unsigned long long u64;\n"
        "static u64 divmod(u32 a, u32 b)\n{\n    return ((u64)(a % b) << 32) | (a / b);\n}\n")
WIDE_FN = """u32 f(u32 r0, u32 r1, u32 r2)
{
    u64 t_1, t_2, t_3, t_4, t_5, t_6;
    u32 r3;
    t_1 = (u64)r1 + 1;
    t_2 = (u64)r0 + ~0x2u + 1;
    t_3 = divmod(r0, r2 | 1u);
    t_4 = (u64)r0 * r1;
    t_5 = ((u64)r0 * r1) >> 3;
    t_6 = (u64)r2 << 4;
    r3 = (u32)t_1 + (u32)t_2;
    if ((u32)t_2 > 8) r3 = r3 + (u32)t_3;
    return r3 + (u32)(t_4 >> 32) + (u32)t_4 + (u32)t_5 + (u32)t_6;
}
"""


def test_a_two_word_value_read_only_as_its_low_word_is_32_bits(tmp_path):
    """narrow_wide: a u64 variable read only as (u32)v (a carry kept by the lifter, a routine's two
    result words) is u32, its values computed in 32 bits where their low word depends on their operands'
    low words alone (t_1 = r1 + 1; the borrow form polish then folds, r0 - 2; a call, cut to its low word
    by the 32-bit place); one whose high word is read (t_4), or whose value shifts a product right (t_5:
    its low word needs the high one), stays. Compiled before and after with the host's C compiler: the
    same results."""
    import ctypes
    import random
    import shutil
    import subprocess
    tree = cparse.parse(WIDE + WIDE_FN)
    fn = tree.function("f")
    assert polish.narrow_wide(tree, fn)
    polish.polish(tree, fn)
    out = cparse.unparse(fn, notes=False, style="readable")
    for line in ("u64 t_4, t_5;", "t_1 = r1 + 1;", "t_2 = r0 - 2;", "t_3 = divmod(r0, r2 | 1);",
                 "t_6 = r2 << 4;", "if (t_2 > 8) r3 += t_3;"):
        assert line in out, (line, out)
    assert "(u32)t_4" in out and "(u32)(t_4 >> 32)" in out and "(u32)t_5" in out, out
    if not shutil.which("cc"):
        return
    source = tmp_path / "wide.c"
    source.write_text(WIDE + WIDE_FN.replace("u32 f(", "u32 orig(") + out.replace("u32 f(", "u32 rest("))
    lib = tmp_path / "wide.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-o", str(lib), str(source)], check=True)
    so = ctypes.CDLL(str(lib))
    a, b = so.orig, so.rest
    for g in (a, b):
        g.restype, g.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 3
    rng = random.Random(9)
    for _ in range(20000):
        args = [rng.choice([0, 1, 2, 3, 0xFFFFFFFF, rng.getrandbits(32)]) for _ in range(3)]
        assert a(*args) == b(*args), args


def test_a_wide_value_stays_where_it_is_read_otherwise():
    """Not narrowed: a parameter, a variable whose address is taken, one compared whole, one given a value
    inside another expression, one given its own value shifted right."""
    text = WIDE + """u32 f(u64 p, u32 r0)
{
    u64 t_1, t_2, t_3, t_4;
    u32 r1;
    t_1 = (u64)r0 + 1;
    divmod((u32)&t_1, 0);
    t_2 = (u64)r0 + 2;
    t_3 = (u64)r0 + 3;
    r1 = (u32)(t_4 = (u64)r0 + 4);
    return (u32)p + (u32)t_1 + (t_2 == 7) + (u32)t_3 + (u32)(t_3 >> 1) + r1 + (u32)t_4;
}
"""
    tree = cparse.parse(text)
    fn = tree.function("f")
    assert not polish.narrow_wide(tree, fn)


def test_a_routines_quotient_read_alone_becomes_a_division():
    """A division routine's two words kept in a u64 variable whose low word, the quotient, alone is read:
    narrowed first (readable.polish_tree), the 32-bit place given the call is written as / by
    dread.divisions."""
    from dread import divisions
    text = WIDE + """u32 f(u32 r0, u32 r1)
{
    u64 t_1;
    t_1 = divmod(r0, r1);
    return (u32)t_1 + 1;
}
"""
    tree = cparse.parse(text)
    fn = tree.function("f")
    assert polish.narrow_wide(tree, fn)
    assert divisions.rewrite(tree, fn, {"divmod": ["u", "qr", "nd"]})
    out = cparse.unparse(fn, notes=False, style="readable")
    assert "t_1 = r0 / r1;" in out and "u32 t_1;" in out, out
