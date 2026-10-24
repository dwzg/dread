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


def test_a_proposal_keeps_no_type_suffix():
    """untyped: the type default_name gives a narrow access (g_20001830_u8) goes from a proposal that has
    other words; a proposal that is nothing but a type or a keyword with one, or has no such suffix at its
    end, stays as it is."""
    for proposal, kept in (("uxTimerFlags_u8", "uxTimerFlags"), ("offset_s16", "offset"), ("a_b_u64", "a_b"),
                           ("count", "count"), ("u8", "u8"), ("_u8", "_u8"), ("int_u8", "int_u8"),
                           ("level_U8", "level_U8"), ("width_u12", "width_u12"), ("u16_mode", "u16_mode")):
        assert naming.untyped(proposal) == kept, (proposal, naming.untyped(proposal))


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_globals_names_keep_no_type_suffix(tmp_path):
    """name_globals keeps a proposal without the type suffix the default name it answers showed
    (rx_count_u8 for g_20000010_u8 is rx_count), unless the name without it is taken (a function's) or
    another proposal of the batch (a word and its byte at one address: state and state_u8 stay apart)."""
    from dread import llm, pilot
    target, image, program, _, _ = gizmo_copy(tmp_path / "gizmo")
    uses = {key: {"name": name, "functions": ["main"], "uses": [("main", "%s = 1;" % name)]}
            for key, name in (("0x20000010 u8", "g_20000010_u8"), ("0x20000014 u32", "g_20000014"),
                              ("0x20000014 u8", "g_20000014_u8"), ("0x20000018 s16", "g_20000018_s16"),
                              ("0x2000001c u8", "g_2000001c_u8"))}
    answer = ('```json\n{"g_20000010_u8": "rx_count_u8", "g_20000014": "state", "g_20000014_u8": "state_u8", '
              '"g_20000018_s16": "offset_s16", "g_2000001c_u8": "main_u8"}\n```')
    lines = []
    assert pilot.name_globals(target, image, program, llm.Scripted([answer]), sorted(uses), uses,
                              out=lines.append) == 5, lines
    table = naming.load(image, "globals")
    assert {k: e["name"] for k, e in table.items()} == {
        "0x20000010 u8": "rx_count", "0x20000014 u32": "state", "0x20000014 u8": "state_u8",
        "0x20000018 s16": "offset", "0x2000001c u8": "main_u8"}, table


def gizmo_copy(path):
    """(target, image, program, context, lifter) of a fresh copy of gizmo's m3 build at path."""
    from conftest import GIZMO
    from dread import lift, readable, regflow
    from dread.program import Program
    from dread.target import Target
    shutil.copytree(GIZMO, path, ignore=shutil.ignore_patterns("build"))
    target = Target.load(str(path / "m3"))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    return target, image, program, readable.Context(target, image, program, flow), lift.make_lifter(program, flow)


ASKED = ["strlen", "clamp", "checksum", "memcpy", "fmt_hex", "ring_put"]


def played(jobs, first="strlen", declined="checksum", corrected="fmt_hex"):
    """The model, played for the threads of name_variables: each variable named after its function;
    with more than one job the first function's answer waits until jobs - 1 others have been answered
    (so the answers end out of order), one function's answer is declined, another's first answer gives
    a variable a taken name (u32) and its second is right. Returns (answer, the functions in the order
    they were answered)."""
    import json
    import re
    import threading
    lock, others, order = threading.Lock(), threading.Event(), []

    def answer(messages):
        name = re.match(r"The function (\w+) ", messages[0]["content"]).group(1)
        if name == first and jobs > 1:
            assert others.wait(60), "the other functions were not asked beside the first"
        if name == declined:
            text = {"text": "", "finish": "refusal"}
        else:
            found = sorted(set(re.findall(r"\b(r\d+(?:_\d+)?)\b", messages[0]["content"])))
            wrong = name == corrected and len(messages) == 1
            text = '```json\n{"what": "%s does its thing", "names": %s}\n```' % (name, json.dumps(
                {v: "u32" if wrong and i == 0 else "%s_%s" % (name, v) for i, v in enumerate(found)}))
        with lock:
            order.append(name)
            if len(set(order) - {first}) >= jobs - 1:
                others.set()
        return text
    return answer, order


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_pilot_names_functions_jobs_at_a_time(tmp_path, monkeypatch):
    """dread pilot --task readable with --jobs (name_variables): the questions made first (with four
    jobs in a pool of worker processes, POOL_MIN lowered: forked before any thread starts; with one in
    this process), then asked four at a time, their answers ending out of order (the first function's
    after three others'), one declined (no entry, the run goes on), one corrected after the tools'
    words; the readable_names table ends byte for byte as one question after another (--jobs 1) leaves
    it, and each function's C has its names."""
    from dread import llm, pilot, readable
    tables, lines = {}, {}
    for jobs in (1, 4):
        monkeypatch.setattr(readable, "POOL_MIN", 2 if jobs > 1 else 10 ** 6)
        target, image, program, context, lifter = gizmo_copy(tmp_path / ("jobs%d" % jobs))
        answer, order = played(jobs)
        made = []

        def provider():
            made.append(llm.Scripted(answer))
            return made[-1]
        lines[jobs] = []
        named, asked, _ = pilot.name_variables(target, image, program, provider, ASKED, context, lifter, jobs=jobs,
                                               tries=2, out=lines[jobs].append)
        assert (named, asked) == (5, 6), lines[jobs]
        assert 1 <= len(made) <= jobs and order.count("fmt_hex") == 2, (made, order)
        assert order.index("strlen") >= 3 if jobs > 1 else order.index("strlen") == 0, order   # (four: after others)
        assert any(line.startswith("checksum") and "no names: the provider declined" in line for line in lines[jobs])
        with open(image.table("readable_names")) as f:
            tables[jobs] = f.read()
        table = naming.load(image)
        assert sorted(table) == sorted(set(ASKED) - {"checksum"}), sorted(table)
        folder = os.path.join(target.build, "pilot", image.name)
        assert "u32 is taken" in open(os.path.join(folder, "fmt_hex.readable.md")).read()
        assert os.path.exists(os.path.join(folder, "checksum.readable.md"))
        out = readable.transform("fmt_hex", lifter.lift(program.by_name["fmt_hex"]).text,
                                 context=readable.Context(target, image, program))
        assert "fmt_hex_r0" in out and "/* fmt_hex: fmt_hex does its thing" in out, out
    assert tables[1] == tables[4]
    assert sorted(line for line in lines[1] if not line.startswith("dread pilot")) == sorted(
        line for line in lines[4] if not line.startswith("dread pilot"))


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_variables_asked_in_batches_end_as_asked_alone(tmp_path):
    """name_variables with batch: every function's question goes in one Message Batch (played in memory),
    the answer the checks refuse asks again in a second batch, the declined one gets no entry; the
    readable_names table ends byte for byte as one question after another leaves it, and the answers are
    counted as a batch's."""
    from dread import llm, pilot
    tables, made = {}, []
    for batch in (0, 1):
        target, image, program, context, lifter = gizmo_copy(tmp_path / ("batch%d" % batch))
        answer, order = played(1)

        def provider():
            made.append(llm.Scripted(answer, batches=True))
            return made[-1]
        lines = []
        named, asked, spent = pilot.name_variables(target, image, program, provider, ASKED, context, lifter, jobs=2,
                                                   tries=2, out=lines.append, batch=batch)
        assert (named, asked) == (5, 6) and order.count("fmt_hex") == 2, (lines, order)
        with open(image.table("readable_names")) as f:
            tables[batch] = f.read()
        if batch:
            batcher = next(p for p in made if p.submitted)
            assert [len(b["requests"]) for b in batcher.submitted] == [6, 1], batcher.submitted
            assert spent["batch"] == 7 and spent["answers"] == 7
            assert any("as 1 Message Batch" in line for line in lines), lines
            assert "u32 is taken" in open(os.path.join(target.build, "pilot", image.name, "fmt_hex.readable.md")).read()
    assert tables[0] == tables[1]


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_readable_task_asks_jobs_at_a_time_from_the_command_line(tmp_path, monkeypatch, capsys):
    """dread pilot --task readable --jobs 2 NAME...: the globals' question first (uart_putc's device
    registers, answered with no names), then the functions' variables, two at a time, each thread with
    a provider of its own; the summary says how many were named and asked, how many at a time."""
    from dread import llm, pilot
    target, image, program, context, lifter = gizmo_copy(tmp_path / "gizmo")
    answer, _ = played(1, declined=None, corrected=None)
    made = []

    def load(name=None, path=None):
        made.append(llm.Scripted(lambda messages: answer(messages) if messages[0]["content"].startswith(
            "The function ") else '```json\n{}\n```'))
        return made[-1]
    monkeypatch.setattr(llm, "load", load)
    asked = ["uart_putc", "strlen", "clamp"]
    assert pilot.main(["--target", target.root, "--task", "readable", "--jobs", "2"] + asked) == 0
    out = capsys.readouterr().out
    assert "3 of 3 functions named, 3 asked, 2 at a time" in out, out
    assert sorted(naming.load(image)) == sorted(asked) and 2 <= len(made) <= 3, out
    assert [system for system, _ in made[0].asked] == [pilot.GLOBALS_SYSTEM], made[0].asked
    assert all(p.asked and {system for system, _ in p.asked} == {pilot.READABLE_SYSTEM} for p in made[1:])


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_pilot_stops_asking_a_provider_that_keeps_declining_or_failing(tmp_path, monkeypatch):
    """name_variables asks no more after DECLINED_IN_A_ROW answers declined in a row, nor after
    FAILED_IN_A_ROW questions the provider failed in a row; the questions asked by then end; a run
    started again asks what has no names, and says which are named already."""
    from dread import llm, pilot
    monkeypatch.setattr(pilot, "FAILED_IN_A_ROW", 3)
    target, image, program, context, lifter = gizmo_copy(tmp_path / "gizmo")
    for jobs, answer, said in ((1, lambda messages: {"text": "", "finish": "refusal"}, "declined 3 questions in a row"),
                               (2, lambda messages: 1 / 0, "failed 3 questions in a row")):
        lines = []
        named, asked, _ = pilot.name_variables(target, image, program, lambda: llm.Scripted(answer), ASKED,
                                               context, lifter, jobs=jobs, out=lines.append)
        assert named == 0 and 3 <= asked <= 3 + jobs - 1 and any(said in line for line in lines), lines
        assert not os.path.exists(image.table("readable_names"))
    answer, _ = played(1)
    pilot.name_variables(target, image, program, lambda: llm.Scripted(answer), ASKED[:2], context, lifter, jobs=2,
                         out=lambda *_: None)
    lines = []
    context.names = naming.load(image)
    named, asked, _ = pilot.name_variables(target, image, program, lambda: llm.Scripted(answer), ASKED[:3], context,
                                           lifter, jobs=2, out=lines.append)
    assert (named, asked) == (2, 1) and "%-24s named already" % "strlen" in lines, lines


def shared_copy(path):
    """gizmo_copy with a readable_types plan put together by hand (gizmo's code alone gives none): the
    ring's two counters (ring_put's and ring_get's r0) one structure, copy_words' two pointers a
    structure of a word each, and g_config a structure of static data (as tests/test_statics.py)."""
    from dread import readable, shapes
    target, image, program, _, lifter = gizmo_copy(path)
    p = shapes.Plan()
    p.structs = {"s_1": [(32, "u32"), (36, "u32")], "s_2": [(0, "u32")], "s_3": [(0, "u32")]}
    p.typed = {"ring_put": {"r0": "s_1"}, "ring_get": {"r0": "s_1"}, "copy_words": {"r0": "s_2", "r1": "s_3"}}
    at = {name: a for kind, a, name in program.symbols() if kind == "d"}["g_config"]
    p.statics = {at: [(0, "u32"), (4, "u32"), (8, "u32")]}
    shapes.store_plan(image, p)
    return target, image, program, readable.Context(target, image, program), lifter


def shared_model(jobs, firsts, batches, said):
    """The model, played for the threads of name_shared: each global named after its address, but the
    first one a question shows "shared_name", which every batch thus proposes; the structures s_1 and s_2
    both named Ring (s_1 has more anchors), s_3 Words. With more than one job the first batch of each
    round (the one whose first shown name is firsts[kind]) is answered only after min(jobs,
    batches[kind]) - 1 others of its round, and after the thread that settles had a second to write a
    line (said: its out) for one of them, which it must not do before the first batch's: the answers
    end out of order. Returns (answer, (kind, first shown name) in the order answered)."""
    import json
    import re
    import threading
    import time
    lock, order, count = threading.Lock(), [], {"globals": 0, "structs": 0}
    others = {kind: threading.Event() for kind in count}
    for kind in count:
        if min(jobs, batches[kind]) <= 1:
            others[kind].set()

    def answer(messages):
        user = messages[0]["content"]
        structs = re.findall(r"^struct (s_\d+)$", user, re.M)
        kind = "structs" if structs else "globals"
        shown = structs or re.findall(r"^(\S+) \(0x[0-9a-f]+, \w+, in \d+ functions\):$", user, re.M)
        first = shown[0] == firsts[kind]
        if first and jobs > 1:
            assert others[kind].wait(60), "the other batches were not asked beside the first"
            before, until = len(said), time.time() + 1.0
            while len(said) == before and time.time() < until:
                time.sleep(0.01)
        if structs:
            names = {s: {"name": "Words", "fields": {"f_0": "word"}} if s == "s_3" else
                     {"name": "Ring", "fields": {"f_20": "head", "f_24": "tail"} if s == "s_1" else {"f_0": "word"}}
                     for s in structs}
        else:
            names = {n: "shared_name" if i == 0 else "var_" + re.sub(r"\W", "_", n.split("_", 1)[1])
                     for i, n in enumerate(shown)}
        with lock:
            order.append((kind, shown[0]))
            if not first:
                count[kind] += 1
                if count[kind] >= min(jobs, batches[kind]) - 1:
                    others[kind].set()
        return "```json\n%s\n```" % json.dumps(names)
    return answer, order


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_shared_names_asked_jobs_at_a_time_end_as_one_after_another(tmp_path, monkeypatch):
    """name_shared with jobs: the uses made in a pool of worker processes (POOL_MIN lowered) or in this
    process, every batch's question made first (three globals a batch, one structure a batch), then
    asked four at a time, the first batch of each round answered after the others. This thread settles
    the answers in the order of the batches: the name every batch proposes is kept once, by the first
    batch, as one after another keeps it, and of the two structures given one name the one with more
    anchors keeps it (struct_names), the other's entry refused as one after another refuses it; the
    table, the conversations and the lines end byte for byte as with jobs 1. (Settled as they ended,
    the second batch took the shared name, and the second structure's entry stayed in the table.)"""
    from dread import llm, naming, pilot, readable
    monkeypatch.setattr(pilot, "GLOBALS_BATCH", 3)
    monkeypatch.setattr(pilot, "STRUCTS_BATCH", 1)
    tables, talks, lines = {}, {}, {}
    for jobs in (1, 4):
        monkeypatch.setattr(readable, "POOL_MIN", 1 if jobs > 1 else 10 ** 6)
        target, image, program, context, lifter = shared_copy(tmp_path / ("jobs%d" % jobs))
        names = [f.name for f in program.functions]
        uses, _ = pilot.global_uses(program, context, lifter, names)
        first = sorted(uses)[0]
        batches = {"globals": -(-len(uses) // 3), "structs": 3}
        lines[jobs] = []
        answer, order = shared_model(jobs, {"globals": uses[first]["name"], "structs": "s_1"}, batches, lines[jobs])
        made = []

        def provider():
            made.append(llm.Scripted(answer))
            return made[-1]
        named, structs, spent = pilot.name_shared(target, image, program, provider, context, lifter, names, names,
                                                  out=lines[jobs].append, jobs=jobs)
        assert len(uses) >= 12 and batches["globals"] >= 4, sorted(uses)
        assert (named, structs) == (len(uses) - batches["globals"] + 1, 2), lines[jobs]
        assert sum(len(p.asked) for p in made) == batches["globals"] + 3 and 1 <= len(made) <= jobs, made
        assert spent == pilot.summed(p.spent() for p in made)
        if jobs > 1:                        # (the first batch of each round ended after the others)
            assert order.index(("globals", uses[first]["name"])) >= 3, order
            assert [s for kind, s in order if kind == "structs"][-1] == "s_1", order
        table = naming.load(image, "globals")
        assert table[first]["name"] == "shared_name", table
        assert [e["name"] for e in table.values()].count("shared_name") == 1
        assert sum("=shared_name" in line for line in lines[jobs]) == batches["globals"] - 1, lines[jobs]
        assert naming.struct_names(context.plan, naming.load(image, "structs"), {e["name"] for e in table.values()}) \
            == {"s_1": ("Ring", {32: "head", 36: "tail"}), "s_3": ("Words", {0: "word"})}
        assert sorted(naming.load(image, "structs")) == ["copy_words:r1", "ring_get:r0"]
        with open(image.table("readable_names")) as f:
            tables[jobs] = f.read()
        folder = os.path.join(target.build, "pilot", image.name)
        talks[jobs] = {n: open(os.path.join(folder, n)).read() for n in sorted(os.listdir(folder))
                       if n.endswith(".md")}
        assert len(talks[jobs]) == batches["globals"] + 3, sorted(talks[jobs])
    assert tables[1] == tables[4] and talks[1] == talks[4]
    assert [line for line in lines[1] if "their uses" not in line] == [
        line for line in lines[4] if "their uses" not in line]


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_shared_names_stop_asking_after_a_failure(tmp_path):
    """name_globals, one global a batch, the provider failing the second batch: one after another asks
    nothing after it, as before; with every batch asked at once the others' answers are kept, and the
    failed batch's global waits for a run started again."""
    import json
    import re
    import threading
    from dread import llm, naming, pilot
    keys = ["0x2000%04x u32" % (4 * i) for i in range(3)]
    uses = {k: {"name": "g_2000%04x" % (4 * i), "functions": ["main"], "uses": [("main", "g_2000%04x = 1;" % (4 * i))]}
            for i, k in enumerate(keys)}
    for jobs in (1, 3):
        target, image, program, _, _ = gizmo_copy(tmp_path / ("jobs%d" % jobs))
        together, asked = threading.Barrier(jobs, timeout=60), []

        def answer(messages):
            shown = re.findall(r"^(g_\w+) \(", messages[0]["content"], re.M)
            asked.append(shown[0])
            together.wait()
            if shown[0] == "g_20000004":
                raise llm.ProviderError("scripted: down")
            return "```json\n%s\n```" % json.dumps({shown[0]: "named_" + shown[0]})
        lines = []
        named = pilot.name_globals(target, image, program, lambda: llm.Scripted(answer), keys, uses, batch=1,
                                   out=lines.append, jobs=jobs)
        table = naming.load(image, "globals")
        assert "dread pilot: the globals' names: the provider: scripted: down" in lines, lines
        if jobs == 1:
            assert asked == ["g_20000000", "g_20000004"] and named == 1 and sorted(table) == keys[:1], (asked, lines)
        else:
            assert sorted(asked) == ["g_20000000", "g_20000004", "g_20000008"], asked
            assert named == 2 and sorted(table) == [keys[0], keys[2]], (table, lines)


def plan_with(number):
    from dread import shapes
    p = shapes.Plan()
    p.structs = {"s_%d" % number: [(0, "u32"), (4, "u32"), (8, "u16")]}
    p.typed = {"get": {"r0": "s_%d" % number}, "put": {"r1": "s_%d" % number}}
    return p


def test_a_structure_s_names_follow_its_anchors_and_hold_only_as_c_names():
    """An entry goes to the structure its anchors point to now (the numbers change whenever the plan
    is made anew); a name that is a keyword, a global's macro or a default name is left out."""
    entries = {"get:r0": {"name": "Item", "fields": {"0": "value", "4": "next", "8": "u32", "12": "gone"},
                          "anchors": ["get:r0", "put:r1"]}}
    assert naming.struct_names(plan_with(1), entries) == {"s_1": ("Item", {0: "value", 4: "next"})}
    assert naming.struct_names(plan_with(7), entries) == {"s_7": ("Item", {0: "value", 4: "next"})}
    assert naming.struct_names(plan_with(1), entries, taken={"Item"}) == {}
    assert naming.struct_names(plan_with(1), {"get:r0": {"name": "s_3", "anchors": ["get:r0"]}}) == {}


def test_named_structures_and_fields_in_the_c():
    from dread import shapes
    text = ("/* get */\n#include \"dread.h\"\nu32 get(u32 r0)\n{\n"
            "    return *(volatile u32 *)(r0 + 4) + *(volatile u16 *)(r0 + 8);\n}\n")
    tree = cparse.parse(text)
    names = {"s_1": ("Item", {4: "next"})}
    assert shapes.apply_types(tree, tree.function("get"), plan_with(1), names=names) == ["s_1"]
    out = cparse.unparse(tree, notes=False)
    assert "struct Item\n{\n    u32 f_0;\n    u32 next;\n    u16 f_8;\n};" in out, out
    assert "u32 get(volatile struct Item *r0)" in out and "return r0->next + r0->f_8;" in out, out


def test_the_pilot_names_structures_from_their_uses(tmp_path):
    from conftest import GIZMO
    from dread import llm, pilot
    from dread.program import Program
    from dread.target import Target
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    target = Target.load(str(copy / "m3"))
    image = target.image()
    asked = []

    def answer(messages):
        asked.append(messages[0]["content"])
        return '```json\n{"s_1": {"name": "Item", "fields": {"f_4": "next", "f_8": "while"}}}\n```'
    uses = {"s_1": [("get", "return r0->f_4 + r0->f_8;"), ("put", "r1->f_0 = 0u;")]}
    lines = []
    assert pilot.name_structs(target, image, Program(target, image), llm.Scripted(answer), plan_with(1), uses,
                              ["s_1"], out=lines.append) == 1
    assert "struct s_1\n{" in asked[0] and "get: return r0->f_4 + r0->f_8;" in asked[0], asked
    entry = naming.load(image, "structs")["get:r0"]
    assert entry["name"] == "Item" and entry["anchors"] == ["get:r0", "put:r1"], entry
    assert naming.struct_names(plan_with(3), naming.load(image, "structs")) == {"s_3": ("Item", {4: "next"})}


def test_names_alike_by_their_words_and_a_vote_over_them():
    """naming.similarity pairs words (a short word that begins a longer one counts: init and
    initialise); naming.vote keeps the name most samples agree with, a name kept from before winning
    ties, and says how many agree."""
    assert naming.similarity("task_delay", "vTaskDelay") == 1.0
    assert naming.similarity("init_list", "initialise_list") == 1.0
    assert naming.similarity("list_insert_end", "list_insert_tail") == 0.5
    assert naming.similarity("queue_send", "queue_receive") < naming.AGREE
    assert naming.similarity("f1", "F1") == 1.0 and naming.similarity("f1", "f2") == 0.0
    assert naming.similarity("strcmp", "str_cmp") == 1.0 and naming.similarity("ListInit", "list_init") == 1.0
    assert naming.similarity("uart_putc", "serial_write_char") == 0.0     # (words, not meaning)
    assert naming.vote(["queue_send", "queue_send_item", "queue_receive", None, "msg_queue_send"]) == (
        "queue_send", 3, 0)
    assert naming.vote([None, None]) is None
    assert naming.vote(["list_append", "list_insert_end"], keep="list_insert_tail") == ("list_insert_tail", 1, None)
    assert naming.vote(["uart_send", "uart_send_byte"], keep="gpio_toggle") == ("uart_send", 2, 0)
    assert naming.vote(["a_b", "c_d", "c_d"]) == ("c_d", 2, 1)            # (names of short pieces: alike or not)


def test_words_whatever_the_naming_style():
    """naming.style_words drops a camelCase name's Hungarian head (vTaskDelay), cuts a run of letters into
    known words (strcmp), takes abbreviations and synonyms to one word and the forms of a word to one
    stem; style_similarity pairs them as similarity pairs words, so that a valid name in another style
    scores as the same while send and receive, on and off, timer 0 and timer 1 stay apart."""
    assert naming.style_words("vTaskDelay") == ["task", "delay"] and naming.style_words("pxCurrentTCB") == ["current",
                                                                                                         "tcb"]
    assert naming.style_words("strcmp") == ["string", "compare"] == naming.style_words("str_cmp")
    assert naming.style_words("global_init.part.0") == ["global", "init"]
    assert naming.style_words("TIMER0_Handler") == ["timer", "0", "handler"]
    assert naming.style_words("x_value") == ["value"]
    assert naming.style_words("isEmpty") == ["empty"]                # (is: a word, not a prefix; a stop word)
    assert naming.style_similarity("vTaskDelay", "task_sleep") == 1.0
    assert naming.style_similarity("eTaskGetState", "task_read_status") == 1.0
    assert naming.style_similarity("uxListRemove", "list_delete_item") == pytest.approx(2 / 3)
    assert naming.style_similarity("xQueueReceive", "queue_send") < 0.5
    assert naming.style_similarity("led_on", "led_off") < 0.5 and naming.style_similarity("TIMER0_Handler",
                                                                                          "timer1_handler") < 1.0
    assert naming.stem("notified") == naming.stem("notifies") == "notify"
    assert naming.stem("stopped") == "stop" and naming.stem("filled") == "fill" and naming.stem("tasks") == "task"
    rows, totals = naming.score_functions({1: "task_sleep", 2: "queue_send"}, {1: "vTaskDelay", 2: "xQueueReceive"})
    assert [r["style"] for r in rows] == [1.0, pytest.approx(1 / 3)] and totals["style_half"] == 1
    assert totals["style"] == pytest.approx(2 / 3) and totals["style_same"] == 1


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_variables_questions_begin_with_what_the_image_is(tmp_path):
    """readable_jobs with a device line (dread pilot known_device): each function's question begins
    with it; what a proposal is made for (the digest of the C) is the same without it, so a run that
    learns the device asks nothing again."""
    from dread import pilot
    target, image, program, context, lifter = gizmo_copy(tmp_path / "gizmo")
    line = pilot.DEVICE_READ.format(device="a test gadget")
    with_line, _ = pilot.readable_jobs(target, program, context, lifter, ["strlen"], {"strlen": ["main"]},
                                       out=lambda *_: None, device=line)
    without, _ = pilot.readable_jobs(target, program, context, lifter, ["strlen"], {"strlen": ["main"]},
                                     out=lambda *_: None)
    assert with_line[0]["user"] == line + "\n\n" + without[0]["user"], with_line[0]["user"][:200]
    assert without[0]["user"].startswith("The function strlen (readable C):")
    assert with_line[0]["digest"] == without[0]["digest"]


def test_the_shared_names_prompts_show_no_name_that_could_be_asked():
    """The globals' and the structures' prompts show the answer's form with placeholders: with real-looking
    examples (g_20029aac, io_40004004, g_20000030) Haiku 5.5 took them for the names asked and named
    nothing in 3 of 27 batches on FreeRTOS GCC -Os (2026-10-23), with placeholders in none."""
    import re
    from dread import pilot
    for prompt in (pilot.GLOBALS_SYSTEM, pilot.STRUCTS_SYSTEM):
        example = re.search(r"```json\n(.*?)```", prompt, re.S).group(1)       # (the answer's form)
        assert not re.search(r"\b(g|io|rom)_[0-9a-f]{3,}|\bs_\d+\b|\bf_[0-9a-f]+\b", example), example
        assert "<your name>" in example and "placeholders" in prompt
    assert not re.search(r"\b(g|io|rom)_[0-9a-f]{3,}", pilot.GLOBALS_SYSTEM)
