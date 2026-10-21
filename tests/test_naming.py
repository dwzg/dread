"""dread.naming: names for readable C. A map of new names is applied only when every name is a C
identifier no other thing of the file uses and the C was the one the map was made for (its digest);
renamed C compiles to the same code (what the pilot's readable task checks before it keeps names);
the source's names come from an ELF's debug information for the score."""
import os
import shutil
import subprocess

import pytest

from dread import cparse, naming

TEXT = """/* f at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 g(u32);
u32 f(u32 r0, u32 r1)
{
    u32 r4, r4_1;
    r4 = *(volatile u32 *)r1;
    if (r4 != 0xffffffffu)
        r4_1 = g(r0 + 8);
    else
        r4_1 = r0;
    return r4_1 + r4;
}
"""


def tree():
    t = cparse.parse(TEXT)
    return t, t.function("f")


def test_a_map_is_applied_only_when_it_holds():
    t, fn = tree()
    good = {"r0": "pxList", "r1": "pxItem", "r4": "xValue", "r4_1": "xResult"}
    assert naming.problems(t, fn, good) == []
    entry = {"digest": naming.digest(fn), "names": good, "what": "inserts an item", "by": "test"}
    assert naming.apply(t, fn, entry)
    text = cparse.unparse(t)
    assert "u32 f(u32 pxList, u32 pxItem)" in text and "xResult = g(pxList + 8);" in text, text
    assert "/* f: inserts an item (names: test) */" in text
    t, fn = tree()
    for bad, why in (({"r0": "g"}, "taken"), ({"r0": "u32"}, "taken"), ({"r0": "while"}, "taken"),
                     ({"r0": "2x"}, "no C name"), ({"r0": "a", "r1": "a"}, "both"),
                     ({"r0": "r4"}, "another variable"), ({"r9": "x"}, "no variable")):
        found = naming.problems(t, fn, bad)
        assert found and why in found[0], (bad, found)
    t, fn = tree()
    stale = {"digest": "0" * 16, "names": good, "what": "", "by": "test"}
    assert not naming.apply(t, fn, stale)                     # (made for other C: waits)
    assert "pxList" not in cparse.unparse(t)


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_renamed_c_is_the_same_code_and_a_swap_is_not(tmp_path):
    from conftest import gizmo_dir
    from dread.target import Target
    target = Target.load(gizmo_dir("m3"))
    image = target.image()
    t, fn = tree()
    before = cparse.unparse(t)
    naming.rename(fn, {"r0": "pxList", "r1": "pxItem", "r4": "xValue", "r4_1": "xResult"})
    assert naming.same_code(target, image, before, cparse.unparse(t), str(tmp_path / "a"))
    swapped = before.replace("return r4_1 + r4;", "return r4_1 - r4;")
    assert not naming.same_code(target, image, before, swapped, str(tmp_path / "b"))


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_source_names_come_from_debug_information(tmp_path):
    pytest.importorskip("elftools")
    (tmp_path / "t.c").write_text("int sum(int count, int *values) { int total = 0; for (int i = 0; i < count; i++) "
                                  "total += values[i]; return total; }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O1", "-g", "-nostdlib", "-e", "sum",
                    "-o", elf, str(tmp_path / "t.c")], check=True)
    found = [v for v in naming.truth(elf).values() if v["name"] == "sum"]
    assert found and found[0]["params"] == ["count", "values"] and {"total", "i"} <= found[0]["locals"], found
    assert naming.words("pxNewListItem") == {"new", "list", "item"}


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_pilot_names_a_readable_function(tmp_path):
    """dread pilot --task readable on gizmo's fmt_dec, the model played: its first answer names a
    variable u32 (taken: refused, told why), its second is kept in the readable_names table, and dread
    readable's C then has the names and the sentence."""
    import json
    import re
    from conftest import GIZMO
    from dread import lift, llm, pilot, readable, regflow
    from dread.program import Program
    from dread.target import Target
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    target = Target.load(str(copy / "m3"))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    asked = []

    def answer(messages):
        asked.append(messages)
        code = messages[0]["content"]
        found = sorted(set(re.findall(r"\b(r\d+(?:_\d+)?|s_?\d+)\b", code)))
        names = {v: ("u32" if len(asked) == 1 and i == 0 else "value_" + v) for i, v in enumerate(found)}
        return '```json\n{"what": "writes a number in decimal", "names": %s}\n```' % json.dumps(names)
    lines = []
    entry = pilot.name_readable(target, image, program, llm.Scripted(answer), "fmt_dec",
                                readable.Context(target, image, program, flow), lifter, tries=2, out=lines.append)
    assert entry and len(asked) == 2, lines
    assert "u32 is taken" in asked[1][-1]["content"]
    assert naming.load(image)["fmt_dec"]["digest"] == entry["digest"]
    out = readable.transform("fmt_dec", lifter.lift(program.by_name["fmt_dec"]).text,
                             context=readable.Context(target, image, program, flow))
    assert "value_r0" in out and "/* fmt_dec: writes a number in decimal" in out, out
    assert os.path.exists(os.path.join(target.build, "pilot", image.name, "fmt_dec.readable.md"))


def test_memory_at_fixed_addresses_is_written_as_names():
    """A macro per address and type, the access itself: the default name says where it is (a device
    register, read-only memory, anything else), the table's name is taken when the file does not
    use it otherwise."""
    from dread.target import Memory
    memory = Memory({"regions": [{"name": "flash", "base": 0, "size": 0x10000, "kind": "rom"},
                                 {"name": "ram", "base": 0x20000000, "size": 0x10000}],
                     "io": [[0x40000000, 0x50000000]]}, "test")
    text = ("/* f */\n#include \"dread.h\"\nu32 f(u32 r0)\n{\n    u32 r3;\n"
            "    r3 = *(volatile u32 *)0x20000034u;\n"
            "    *(volatile u32 *)0x40011000u = r3;\n"
            "    if (*(volatile u8 *)0x100u != 0) r3 = r3 + 1;\n"
            "    return r3;\n}\n")
    t = cparse.parse(text)
    fn = t.function("f")
    chosen = naming.apply_globals(t, fn, None, memory)
    out = cparse.unparse(t)
    assert chosen == {"0x20000034 u32": "g_20000034", "0x40011000 u32": "io_40011000", "0x100 u8": "rom_100_u8"}
    assert "#define io_40011000 (*(volatile u32 *)0x40011000u)" in out and "io_40011000 = r3;" in out, out
    assert "if (rom_100_u8 != 0)" in out and "r3 = g_20000034;" in out, out
    t = cparse.parse(text)
    fn = t.function("f")
    named = {"0x20000034 u32": {"name": "volume"}, "0x40011000 u32": {"name": "r3"}}
    naming.apply_globals(t, fn, named, memory)
    out = cparse.unparse(t)
    assert "r3 = volume;" in out and "io_40011000 = r3;" in out, out    # (r3 is a variable: the default)


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_pilot_names_the_globals_before_the_variables(tmp_path):
    """dread pilot --task readable on gizmo's uart_putc, the model played: the globals first (one
    question, a function's name refused), then the variables, asked with the globals' names in the C."""
    import json
    import re
    from conftest import GIZMO
    from dread import build, lift, llm, pilot, readable, regflow
    from dread.program import Program
    from dread.target import Target
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    target = Target.load(str(copy / "m3"))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = readable.Context(target, image, program, flow)
    asked = []

    def answer(messages):
        asked.append(messages)
        code = messages[0]["content"]
        if len(asked) == 1:
            assert "io_40011000 (0x40011000, u32, in " in code, code
            return '```json\n{"io_40011000": "UART_DATA", "io_40011004": "uart_putc"}\n```'
        assert "UART_DATA = " in code, code
        found = sorted(set(re.findall(r"\b(r\d+(?:_\d+)?)\b", code)))
        return '```json\n{"what": "sends a character", "names": %s}\n```' % json.dumps({v: "c_" + v for v in found})
    provider = llm.Scripted(answer)
    names = [f.name for f in program.functions]
    uses, by_function = pilot.global_uses(program, context, lifter, names)
    lines = []
    assert pilot.name_globals(target, image, program, provider, sorted(by_function["uart_putc"]), uses,
                              out=lines.append) == 1
    assert "refused io_40011004=uart_putc" in lines[-1], lines
    context.globals = naming.load(image, "globals")
    assert pilot.name_readable(target, image, program, provider, "uart_putc", context, lifter, out=lines.append)
    out = readable.transform("uart_putc", lifter.lift(program.by_name["uart_putc"]).text,
                             context=readable.Context(target, image, program, flow))
    assert "#define UART_DATA (*(volatile u32 *)0x40011000u)" in out and "UART_DATA = c_r0;" in out, out
