"""dread.behaviour: what a function did in runs of its cases, as the naming questions of dread pilot
show it (written cases here, so that what a run shows does not depend on the generator)."""
import json
import os
import re
import shutil

from dread import autocase, behaviour, regflow
from dread.emu import Layout
from dread.program import Program
from dread.target import Target
from conftest import GIZMO, gizmo_dir


def loaded(root):
    target = Target.load(root)
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    return target, image, program, flow, autocase.Generator(target, image, program=program)


def text_case(address, text, args=None, mem=()):
    """A written case: the text's bytes at address (NUL-terminated), the address in the first argument."""
    cells = [[address + k, 1, b] for k, b in enumerate(text + b"\0")]
    return {"args": args or [address, 0, 0, 0], "mem": cells + [list(m) for m in mem], "returns": [], "calls": [],
            "frame": 0}


def test_a_run_shows_its_calls_with_texts_and_results_and_what_it_wrote(variant):
    """apply_config on every build: the text it was given, its calls in order with the texts their
    arguments point to and their results, the global it set; a pointer into its stack is &local."""
    target, image, program, flow, gen = loaded(gizmo_dir(variant))
    buf = target.memory.scratch_base + 0x1000
    out = behaviour.section(gen, program.by_name["apply_config"], flow, cases=[text_case(buf, b"volume=7\n")])
    lines = out.splitlines()
    reg = target.abi.arg_regs[0]
    assert lines[0] == behaviour.HEADER.format(reg=reg)
    assert lines[1] == '1. apply_config(%s=0x%x "volume=7\\n") returned' % (reg, buf)
    assert lines[2].startswith('   does: parse_kv(&local "volume=7", &local, 0xc, &local) = 0; strcmp(&local "volume", ')
    assert '"volume") = 0; clamp(7, 0, 9) = 7' in lines[2]
    assert re.match(r"   writes: g_[0-9a-f]+ = 7$", lines[3]), lines[3]


def test_a_repeated_call_folds_and_device_accesses_are_the_functions_own():
    """uart_puts given "hi!" calls uart_putc three times (one line, the argument listed as a text);
    the device accesses inside uart_putc are its own, not uart_puts's; uart_putc's run shows them."""
    target, image, program, flow, gen = loaded(gizmo_dir("m3"))
    uart = target.models["uart"]
    status, data = uart["base"] + uart["status"], uart["base"] + uart["data"]
    buf = target.memory.scratch_base + 0x1000
    case = text_case(buf, b"hi!", mem=[(status, 4, uart["tx_ready"])])
    out = behaviour.section(gen, program.by_name["uart_puts"], flow, cases=[case])
    assert '   does: uart_putc(x) (3 times), x = "hi!"' in out.splitlines() and "io_" not in out
    out = behaviour.section(gen, program.by_name["uart_putc"], flow,
                            cases=[{"args": [0x68, 0, 0, 0], "mem": [[status, 4, uart["tx_ready"]]]}])
    assert out.splitlines()[1:] == ["1. uart_putc(r0=0x68) returned",
                                    "   does: read io_%x = 1; io_%x = 0x68" % (status, data)]


def test_memory_behind_an_input_pointer_is_named_after_it():
    """ring_get's ring in a scratch buffer: its result, the field it moved (*(r0 + 36), offsets in
    decimal as the readable C writes them), the byte it read; an empty ring returns -1."""
    target, image, program, flow, gen = loaded(gizmo_dir("m3"))
    buf = target.memory.scratch_base + 0x1000
    full = {"args": [buf, 0, 0, 0], "mem": [[buf, 1, 0x41], [buf + 32, 4, 1], [buf + 36, 4, 0]]}
    empty = {"args": [buf, 0, 0, 0], "mem": [[buf + 32, 4, 3], [buf + 36, 4, 3]]}
    out = behaviour.section(gen, program.by_name["ring_get"], flow, cases=[empty, full])
    assert out.splitlines()[1:] == ["1. ring_get(r0=0x%x) returned 0x41" % buf, "   reads: *r0 = 0x41",
                                    "   writes: *(r0 + 36) = 1",
                                    "2. ring_get(r0=0x%x) returned 0xffffffff" % buf]


def test_places_name_pointers_globals_the_stack_and_stack_arguments():
    target = Target.load(gizmo_dir("m3"))
    image = target.image()
    layout = Layout(target, image)
    buf = target.memory.scratch_base + 0x1000
    inner = target.memory.scratch_base + 0x2000
    places = behaviour.Places(target, layout, {"args": [buf, 5], "mem": [[buf + 8, 4, inner],
                                                                         [layout.stack_top + 8, 4, 7]]}, 2)
    assert places.name(buf) == "*r0" and places.name(buf + 12) == "*(r0 + 12)"
    assert places.name(inner + 4) == "*(*(r0 + 8) + 4)" and places.value(inner) == "*(r0 + 8)"
    assert places.value(buf + 3) == "r0 + 3" and places.value(5) == "5" and places.value(0x41) == "0x41"
    assert places.value(layout.stack_top - 16) == "&local" and places.name(layout.stack_top + 8) == "s2"
    assert places.name(0x20000030) == "g_20000030" and places.name(0x20000030, "u8") == "g_20000030_u8"
    assert places.name(0x40011004) == "io_40011004"
    named = behaviour.Places(target, layout, {"args": []}, 0, {"0x20000030 u32": {"name": "g_volume"}})
    assert named.name(0x20000030) == "g_volume"


def test_long_texts_keep_their_end_and_patterns_fold():
    path = b"/home/someone/project/src/kernel/queue.c"
    assert behaviour.quoted(path + b"xyz", 30) == '"/home/some...rc/kernel/queue.cxyz"'
    assert behaviour.quoted(b'say "hi"\n') == '"say \\"hi\\"\\n"'
    target = Target.load(gizmo_dir("m3"))
    places = behaviour.Places(target, Layout(target, target.image()), {"args": []}, 0)
    seq = []
    for c in b"ok!":
        seq += [("io read", 0x40011004, 4, 1), ("io write", 0x40011000, 4, c)]
    assert behaviour.fold_at(seq, 0, places) == (6, '(read io_40011004 = 1; io_40011000 = x) (3 times), x = "ok!"')
    calls = [{"to": "getc", "args": [], "texts": [], "ret": [v], "stubbed": False} for v in (1, 2, 3, 4)]
    assert behaviour.fold_at(calls, 0, places) == (4, "getc() = x (4 times), x = 1, 2, 3, 4")
    assert behaviour.fold_at(calls[:2], 0, places) is None


def test_a_run_that_faults_is_left_out_and_without_runs_there_is_no_section():
    target, image, program, flow, gen = loaded(gizmo_dir("m3"))
    fn = program.by_name["ring_get"]
    assert behaviour.section(gen, fn, flow, cases=[{"args": [0xF0000000, 0, 0, 0], "mem": []}]) is None
    assert behaviour.section(gen, fn, flow, cases=[]) is None


def gizmo_copy(tmp_path):
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    for cache in ("autocases", "regflow"):
        here = os.path.join(gizmo_dir("m3"), "build", cache)
        if os.path.isdir(here):
            shutil.copytree(here, copy / "m3" / "build" / cache)
    return str(copy / "m3")


def test_a_function_without_cases_gets_some_from_a_short_search_kept_for_the_next_run(tmp_path, monkeypatch):
    root = gizmo_copy(tmp_path)
    target, image, program, flow, gen = loaded(root)
    cache = os.path.join(target.build, "autocases", image.name, "checksum.json")
    if os.path.exists(cache):
        os.remove(cache)
    assert behaviour.cases_of(target, image, "checksum") == []
    fn = program.by_name["checksum"]
    first = behaviour.section(gen, fn, flow)
    kept = os.path.join(target.build, "behaviour", image.name, "checksum.json")
    assert first and "1. checksum(" in first and json.load(open(kept))["cases"]

    def no_search(*args, **kw):
        raise AssertionError("searched again")
    monkeypatch.setattr(gen, "generate", no_search)
    assert behaviour.section(gen, fn, flow) == first == behaviour.section(gen, fn, flow, make=False)
    os.remove(kept)
    assert behaviour.section(gen, fn, flow, make=False) is None


def test_sections_run_in_a_pool_and_say_whether_a_function_without_callers_returns_something(tmp_path):
    """sections(): a pool of workers gives what section() gives in this process; strlen has no
    callers in gizmo, so its result is shown by its register's name. The pool searches for the
    functions the copy has no cases of (a checkout without the lift check's caches, as CI's), and
    the run in this process reads the cases it kept."""
    root = gizmo_copy(tmp_path)
    target, image, program, flow, gen = loaded(root)
    names = ["strlen", "ring_get", "gpio_set", "uart_puts", "apply_config", "checksum", "memset", "clamp", "led_on"]
    pooled = behaviour.sections(target, image, program, flow, names, jobs=2, make=True)
    here = behaviour.sections(target, image, program, flow, names[:3], jobs=1, make=False)
    assert all(pooled[n] == here[n] for n in here)
    assert set(pooled) <= set(names) and len(pooled) >= 7
    assert "returned (r0 = " in pooled["strlen"]
