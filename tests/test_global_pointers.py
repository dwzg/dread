"""Words at fixed addresses that hold a pointer to a structure (dread.shapes pointer_globals, the
readable_types table's global_pointers): their macro is that pointer, a typed variable takes the word
without a cast, members are read through it, other uses are (u32). Until 2026-10-25 such a word stayed
a u32, cast to the structure at every use (FreeRTOS's pxCurrentTCB: six casts in one function). A
program of our own: a list whose current node a global holds, read and stepped by small functions."""
import re
import shutil

import pytest

from dread import readable
from dread.elf import Elf

LIST_C = r"""
struct node { int value; struct node *next; int count; };
struct node pool[4];
struct node *current;

__attribute__((noinline)) int peek(void)
{
    struct node *n = current;
    return n ? n->value + n->count : -1;
}

__attribute__((noinline)) void step(void)
{
    struct node *n = current;
    if (n) {
        n->count++;
        current = n->next;
    }
}

__attribute__((noinline)) void setup(void)
{
    for (int i = 0; i < 3; i++) {
        pool[i].value = i;
        pool[i].next = &pool[i + 1];
        pool[i].count = 0;
    }
    pool[3].next = 0;
    current = &pool[0];
}

int main(void)
{
    setup();
    for (;;) {
        step();
        if (peek() < 0)
            setup();
    }
}
"""


def needs(kind):
    from conftest import needs_riscv_gcc
    from test_statics import needs_gcc
    return needs_riscv_gcc if kind == "rv32" else needs_gcc


@pytest.mark.slow
@pytest.mark.parametrize("kind", [pytest.param(k, marks=needs(k)) for k in ("a32", "m3", "rv32")])
def test_a_word_holding_a_structures_pointer_reads_as_that_pointer(kind, tmp_path):
    from test_statics import kind_target
    target = kind_target(tmp_path, kind, LIST_C)
    image = target.image()
    address = next(s.value for s in Elf(str(tmp_path / "t.elf")).symbols if s.name == "current")
    accepted = readable.check(target, image, out=lambda line: None)
    assert {"peek", "step"} <= set(accepted), accepted
    from dread import shapes
    plan = shapes.load_plan(image)
    assert address in (plan.global_pointers or {}), (hex(address), plan.global_pointers)
    from dread import lift, regflow
    from dread.program import Program
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    context = readable.Context(target, image, program, flow)
    lifter = lift.make_lifter(program, flow)
    for name in ("peek", "step"):
        text = readable.printed(name, lifter.lift(program.by_name[name]).text, context=context)
        macro = re.search(r"^#define (\w+) \(\*\(volatile struct (\w+) \*volatile \*\)0x%xu\)$" % address, text, re.M)
        assert macro, text
        word, tag = macro.groups()
        assert "(volatile struct %s *)%s" % (tag, word) not in text, text      # (no cast of the word)
        assert re.search(r"\b%s\b" % word, text.split(macro.group(0))[1]), text   # (and it is used)
