"""dread.cparse: the lifters' C as a tree and back. Every function of a gizmo build, lifted, parsed
and printed again, compiles to the code the lifter's own C compiles to (on FreeRTOS: all 39 bench
builds, ARM, RISC-V and MSP430); and the pieces of the dialect one by one, each statement with the
instruction its line's comment names."""
import os

import pytest

from dread import build, cparse
from dread.elf import Elf


def code(obj):
    """The code sections of an object: their bytes and relocations (offset, type, symbol, addend)."""
    e = Elf(obj)
    out = {}
    for i, s in enumerate(e.sections):
        if s.flags & 4 and s.size:          # SHF_EXECINSTR
            out[s.name] = (e.data(s), [(o, t, e.symbols[k].name, a) for o, t, k, a in e.relocations(i)])
    return out


@pytest.mark.compiles
def test_lifted_c_printed_again_is_the_same_code(target, tmp_path):
    from dread.program import Program
    image = target.image()
    names = [fn.name for fn in Program(target, image).functions]
    made = build.lift_sources(target, image, names, str(tmp_path / "lifted"), quiet=True)[0]
    assert len(made) > 10
    printed = tmp_path / "printed"
    printed.mkdir()
    for name in made:
        text = (tmp_path / "lifted" / (name + ".c")).read_text()
        tree = cparse.parse(text)
        assert tree.function(name) is not None
        again = cparse.unparse(tree)
        assert cparse.parse(again) == tree
        (printed / (name + ".c")).write_text(again)
    flags = target.compiler.lifted_cflags
    a = build.compile_all(target, image, str(tmp_path / "lifted"), str(tmp_path / "obj-a"), quiet=True, flags=flags)
    b = build.compile_all(target, image, str(printed), str(tmp_path / "obj-b"), quiet=True, flags=flags)
    assert len(a) == len(b) == len(made)
    assert [os.path.basename(x) for x, y in zip(sorted(a), sorted(b)) if code(x) != code(y)] == []


DIALECT = """/* f at 0x100, lifted by dread lift: one statement per instruction. */
#include "dread.h"
typedef union { u32 w; u16 h[2]; } dread_w32;
extern u32 g(u32, u32, u32, u32);
static inline __attribute__((always_inline)) u16 helper(u16 x)
{
    u16 r;
    __asm__ ("mov %1, %0" : "=&r"(r) : "r"(x));
    return r;
}
u32 f(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3;
    u32 n = 0, z = 0;
    u32 frame[2] __attribute__((aligned(8)));
    u32 sp0 = (u32)&frame[2];
    /* 100: push {r4, lr} */
    { u32 x = r0, y = r1; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; }  /* 102: cmp r0, r1 */
    if (z) goto L_10a;  /* 104: beq #0x10a */
    { register u32 x0 __asm__("r0") = r0; __asm__ volatile ("bl g" : "+r"(x0) :: "lr", "memory"); r0 = x0; }  /* 106: bl g */
    *(volatile u32 *)(sp0 - 8) = (u32)r0; r0 = g(r0, r1, r2, r3); r1 = r2 = r3 = 0;  /* 108: bl g */
L_10a: ;
    return ((dread_w32){ .h = { r0, r1 } }).w;  /* 10a: bx lr */
}
"""


def test_each_statement_keeps_the_instruction_it_came_from():
    tree = cparse.parse(DIALECT)
    f = tree.function("f")
    assert [p[1] for p in f.params] == ["a0", "a1", "a2", "a3"]
    assert tree.function("helper") is not None
    body = f.body.body
    note = next(s for s in body if isinstance(s, cparse.Note))
    assert note.addr == (0x100, "push {r4, lr}")
    branch = next(s for s in body if isinstance(s, cparse.If))
    assert branch.addr == (0x104, "beq #0x10a") and isinstance(branch.then, cparse.Goto)
    call = [s for s in body if s.addr and s.addr[0] == 0x108]
    assert len(call) == 3 and isinstance(call[1].e.value, cparse.Call)      # one line, three statements
    bound = next(s for s in body if isinstance(s, cparse.Block) and s.addr and s.addr[0] == 0x106)
    assert bound.body[0].items[0].asm == '__asm__("r0")' and isinstance(bound.body[1], cparse.Asm)
    assert bound.body[1].outputs[0].constraint.parts == ['"+r"'] and bound.body[1].clobbers[0].parts == ['"lr"']
    ret = body[-1]
    assert isinstance(ret, cparse.Return) and isinstance(ret.e, cparse.Member)
    assert isinstance(ret.e.base, cparse.Compound) and ret.e.base.init.items[0].designator == ".h"
    text = cparse.unparse(tree)
    assert cparse.parse(text) == tree
    assert "    if (z) goto L_10a;  /* 104: beq #0x10a */" in text.splitlines()
    assert "L_10a: ;" in text.splitlines()
    assert "/* 108: bl g */" in [l.split("  ")[-1] for l in text.splitlines() if "r0 = g(" in l]


def statement(text):
    return cparse.parse("void f(void)\n{\n%s\n}\n" % text).function("f").body.body[0]


def test_printing_keeps_precedence_and_the_else_with_its_if():
    for source in ("x = a - (b - c);", "x = a - b - c;", "x = (a + b) << c;", "x = a << b + c;",
                   "x = -(-a);", "x = *(volatile u32 *)(p + 4);", "x = a ? b : c ? d : e;",
                   "x = (a ? b : c) ? d : e;", "x = (u32)~(a & b);", "r1 = r2 = r3 = 0;",
                   "x = ((u32 (*)(u32, u32))p)(1, 2);", "x = a[i + 1]--;"):
        tree = statement(source)
        assert statement(cparse.unparse(tree, notes=False)) == tree, source
    # if (a) { if (b) x = 1; } else x = 2: printed without its braces, the else would be the inner if's
    inner = cparse.If(cparse.Name("b"), cparse.ExprStmt(cparse.Assign("=", cparse.Name("x"), cparse.Num("1"))))
    outer = cparse.If(cparse.Name("a"), inner, cparse.ExprStmt(cparse.Assign("=", cparse.Name("x"), cparse.Num("2"))))
    printed = cparse.unparse(outer, notes=False)
    again = statement(printed)
    assert isinstance(again, cparse.If) and again.orelse is not None, printed
    assert isinstance(again.then, cparse.Block) and again.then.body[0].orelse is None, printed


def test_printing_adds_the_parentheses_a_reader_needs():
    """Those GCC's -Wparentheses asks for, though C does not need them: the tree stays the same."""
    for source, printed in (("x = a >> 1 & 1;", "x = (a >> 1) & 1;"), ("x = a << b + c;", "x = a << (b + c);"),
                            ("x = a & b == c;", "x = a & (b == c);"), ("x = a | b & c;", "x = a | (b & c);"),
                            ("x = a || b && c;", "x = a || (b && c);"), ("x = a + b * c;", "x = a + b * c;")):
        tree = statement(source)
        assert cparse.unparse(tree, notes=False).strip() == printed, source
        assert statement(printed) == tree, source


def test_what_is_not_the_dialect_is_an_error():
    for source in ("x = ({ int y = 1; y; });", "x = a +;", "if (a) goto;"):
        with pytest.raises(cparse.ParseError):
            statement(source)
