"""dread trace on gizmo: a case in both images to the first point where they part. A function's lifted
C changed on purpose (a constant, a stored value, a call's argument, a loop that never ends) is named at
the instruction and the C line where the runs part, a case that agrees has no divergence, a moved
function is entered through its old entry, and readable C's frame objects are compared where their
addresses reach a call."""
import os
import re
import shutil

import pytest

from dread import lift, readable, runner, trace
from dread.program import Program
from dread.target import Target
from conftest import GIZMO, gizmo_dir

pytestmark = pytest.mark.compiles

NAME = "apply_config"       # moved on every build (its lifted C is larger than its code); calls clamp and
                            # stores clamp's result into a global


@pytest.fixture(scope="module")
def copies(tmp_path_factory):
    """One copy of gizmo for the module, with the cases and the register flow the example has cached."""
    copy = tmp_path_factory.mktemp("trace") / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    for variant in ("a32", "m3", "m4", "rv32"):
        for cache in ("autocases", "regflow"):
            here = os.path.join(gizmo_dir(variant), "build", cache)
            if os.path.isdir(here):
                shutil.copytree(here, copy / variant / "build" / cache)
    return copy


@pytest.fixture
def gizmo(copies, variant):
    target = Target.load(str(copies / variant))
    return target, target.image()


@pytest.fixture(autouse=True)
def _fresh_readable(monkeypatch):
    """Readable C made in one test (a changed one too) is not taken from the process's store by the next."""
    monkeypatch.setattr(readable, "_STORE", {})


def plant(monkeypatch, name, change):
    """Every lifter made from now on lifts name with change(its text) (a deliberately wrong C)."""
    made = lift.make_lifter

    def make(*args, **kwargs):
        lifter = made(*args, **kwargs)
        real = lifter.lift

        def lifted(fn, *a, **k):
            got = real(fn, *a, **k)
            if fn.name == name and not getattr(got, "planted", False):
                text = change(got.text)
                assert text != got.text, "the change does not apply"
                got.text, got.planted = text, True
            return got
        lifter.lift = lifted
        return lifter
    monkeypatch.setattr(lift, "make_lifter", make)


def call_and_store(target, image):
    """The addresses of apply_config's call of clamp and of the store of its result (the first store
    behind the call), from the original's code."""
    program = Program(target, image)
    fn, clamp = program.by_name[NAME], program.by_name["clamp"].entry
    call = store = None
    for a in fn.insns:
        insn = program.insn(a)
        kind, dest, _ = program.arch.flow(insn)
        if call is None and kind == "call" and dest is not None and dest & ~1 == clamp:
            call = a
        elif call is not None and a > call and insn.mnemonic.startswith(("str", "sw", "c.sw")):
            store = a
            break
    assert call and store
    return call, store


def line_of(text, address):
    m = re.search(r"^.*/\* %x: .*$" % address, text, re.M)
    assert m, "no line for 0x%x" % address
    return m.group(0)


def change_line(address, edit):
    """A change of the lifted text: edit(line) for the line of the instruction at address."""
    def change(text):
        line = line_of(text, address)
        return text.replace(line, edit(line), 1)
    return change


def failing(target, image, name, mode="lifted"):
    """The labels of name's failing cases in the image dread trace builds for a mode (as dread lift
    --check or dread readable --check builds it)."""
    payload = trace.rebuilt_image(target, image, mode, name, lines=False)[0]
    runner.prepare(target, image, payload)
    lines = []
    runner.run([name], only=True, out=lines.append, jobs=2)
    return re.findall(r"^FAIL %s \[(.+)\]$" % re.escape(name), "\n".join(lines), re.M)


def traced(target, image, name, label, mode="lifted"):
    result, heading = trace.trace(target, image, name, label, mode=mode)
    return result, heading, heading + "\n" + result.text()


def test_a_case_that_agrees_has_no_divergence(gizmo, capsys):
    target, image = gizmo
    assert trace.main(["--target", target.root, "print_status", "generated 0", "--lifted"]) == 0
    out = capsys.readouterr().out
    assert "no divergence:" in out and "and so does the end" in out, out
    result, _, _ = traced(target, image, "print_status", "[generated 0]")
    assert result.agrees and not result.verdict and result.cmp.pairs


def test_a_case_that_is_not_there_is_named(gizmo, capsys):
    target, _ = gizmo
    assert trace.main(["--target", target.root, "clamp", "no such case", "--lifted"]) == 2
    err = capsys.readouterr().err
    assert "clamp has no case labelled 'no such case'" in err and "generated 0 to " in err


@pytest.mark.slow
def test_a_changed_constant_is_named_at_the_store_it_moves(gizmo, monkeypatch):
    """The offset of the store's address changed: the stores of that stretch differ, named at the
    original's store and at the C line the rebuilt image's store came from (the changed one)."""
    target, image = gizmo
    _, store = call_and_store(target, image)

    def edit(line):
        lhs, rhs = line.split(" = ", 1)
        return re.sub(r"(?<!\w)(0x[0-9a-fA-F]+|\d+)u?(?!\w)", lambda m: "%du" % (int(m.group(1), 0) + 4), lhs,
                      count=1) + " = " + rhs
    plant(monkeypatch, NAME, change_line(store, edit))
    labels = failing(target, image, NAME)
    assert labels
    result, heading, text = traced(target, image, NAME, labels[0])
    d = result.first
    assert d.kind == "stores" and not d.soft and result.hard is d, text
    assert result.site("original", d.original) == store, text
    assert "moved to 0x" in heading                     # (entered through the branch at its old entry)
    rebuilt_pc = result.site("rebuilt", d.rebuilt)
    v = result.tracer.placement.functions[NAME]
    assert v["branch"] and v["address"] <= rebuilt_pc < v["address"] + v["c_bytes"], text
    assert "  original  0x%-8x" % store in text and "first divergence: the stores of this stretch differ" in text
    pytest.importorskip("elftools")
    assert re.search(r"from the C line lifted/%s\.c:\d+:\n.*/\* %x: " % (NAME, store), text), text


@pytest.mark.slow
def test_a_changed_stored_value_is_named_at_its_store(gizmo, monkeypatch):
    target, image = gizmo
    _, store = call_and_store(target, image)
    plant(monkeypatch, NAME, change_line(store, lambda line: line.replace(";", " + 1u;", 1)))
    labels = failing(target, image, NAME)
    assert labels
    result, _, text = traced(target, image, NAME, labels[0])
    d = result.first
    assert d.kind == "stores" and result.site("original", d.original) == store, text
    first_o, first_r = d.stores["first"]
    assert first_o.data[0] == first_r.data[0] and first_o.data[2] + 1 == first_r.data[2], text   # (same place, +1)
    assert "its statement in the C (its comment names the instruction), lifted/%s.c:" % NAME in text
    pytest.importorskip("elftools")
    assert re.search(r"from the C line lifted/%s\.c:\d+:\n.* \+ 1u;\s+/\* %x: " % (NAME, store), text), text


@pytest.mark.slow
def test_a_changed_call_argument_is_where_the_runs_part(gizmo, monkeypatch):
    """clamp gets 7 for the value it clamps: the runs part at the call (the argument register named,
    with both values), before what dread test compares differs (clamp's result, stored)."""
    target, image = gizmo
    call, _ = call_and_store(target, image)
    plant(monkeypatch, NAME, change_line(call, lambda line: re.sub(r"clamp\(([^,()]+),", r"clamp(0x7u,", line,
                                                                     count=1)))
    labels = failing(target, image, NAME)
    assert labels
    result, _, text = traced(target, image, NAME, labels[0])
    d = result.first
    assert d.kind == "arguments" and d.soft, text
    assert result.site("original", d.original) == call and d.original.data[0] == d.rebuilt.data[0], text
    assert re.search(r"a call of clamp with other arguments: \w+ 0x[0-9a-f]+ vs 0x7\b", text), text
    assert result.hard is not None and result.hard is not d and result.verdict      # (then what dread test sees)
    assert "the first difference dread test compares:" in text
    pytest.importorskip("elftools")
    assert re.search(r"from the C line lifted/%s\.c:\d+:\n.*clamp\(0x7u,.*/\* %x: " % (NAME, call), text), text


@pytest.mark.slow
def test_a_run_that_hangs_is_named_where_it_circles(gizmo, monkeypatch):
    """A loop that never ends in front of the store: the rebuilt image runs into its limit there, and the
    report says what the original did next."""
    target, image = gizmo
    _, store = call_and_store(target, image)
    plant(monkeypatch, NAME, change_line(store, lambda line: re.sub(r"^(\s*)", r'\1for (;;) __asm__ volatile(""); ',
                                                                     line, count=1)))
    labels = failing(target, image, NAME)
    assert labels
    result, _, text = traced(target, image, NAME, labels[0])
    d = result.first
    assert d.kind == "limit" and result.hard is d, text
    assert "the rebuilt image runs into its limit" in d.what and d.original is not None, text
    got = result.tracer.placement.c_function(result.end_r.pc)
    assert got is not None and got[0] == NAME, text
    pytest.importorskip("elftools")
    assert re.search(r"from the C line lifted/%s\.c:\d+:\n.*for \(;;\)" % NAME, text), text


@pytest.mark.slow
def test_readable_frame_objects_reach_the_call_they_are_handed_to(gizmo, monkeypatch):
    """Readable C that writes a byte buffer of its frame wrong: the runs part where the buffer's address
    goes to a callee (its bytes compared there, the object named), and the frame shows the object in
    both images."""
    target, image = gizmo
    real = readable._transform
    hit = []

    def made(name, text, stages, context=None):
        got = real(name, text, stages, context)
        if name == NAME and got is not None:
            changed = re.sub(r"(\bstack\d+\[\w+\] = )(\w+);", r"\1\2 + 1;", got, count=1)
            if changed != got:
                hit.append(name)
            return changed
        return got
    monkeypatch.setattr(readable, "_transform", made)
    labels = failing(target, image, NAME, "readable")
    if not hit:
        pytest.skip("%s's readable C indexes no byte buffer of its frame here" % NAME)
    # a failing case, else (where the bytes it changes decide nothing a case compares) the first case
    # whose run hands the buffer to a callee. A case that returns before the call can part at the end
    # alone: NAME's result is read by nobody, so its readable C is void here, and what a void function
    # leaves in the result register is the compiler's (on CI, GCC 13 and 10 for RV32 left a loaded byte
    # there where the original leaves its argument; the host's compiler did not part there); the runner
    # compares it, so the gate gives such a function its result back, but this test's C has not been
    # through the gate: such a case is passed over.
    for label in labels[:1] + ["generated %d" % i for i in range(8)]:
        result, heading, text = traced(target, image, NAME, label, mode="readable")
        if result.first is not None and result.first.kind == "arguments":
            break
    assert "%s is built from readable C" % NAME in heading
    d = result.first
    assert d.kind == "arguments" and "with other bytes behind" in d.what and "stack" in d.what, text
    assert re.search(r"frame of %s \(readable C\).*\n(.*\n)*\s+sp0-0x\w+\s+stack\d+ .*\| rebuilt " % NAME, text), text


def test_store_stretches_compare_what_they_leave():
    """Stores of one stretch in another order, or a word as two halves, leave the same bytes: no
    divergence; another value names the first store in order that differs; two words that both point
    into the stack count as the same."""
    class Lay:
        word = 4

        @staticmethod
        def in_stack(a):
            return 0x4000 <= a < 0x5000

    def stores(*items):
        return [trace.Event("store", i, 0x100 + 4 * i, None, item, 1) for i, item in enumerate(items)]
    lay = Lay()
    a = stores((0x10, 4, 0x11223344), (0x20, 1, 0x55))
    assert trace.compare_stores(lay, a, stores((0x20, 1, 0x55), (0x10, 2, 0x3344), (0x12, 2, 0x1122))) is None
    got = trace.compare_stores(lay, a, stores((0x10, 4, 0x11223344), (0x20, 1, 0x56)))
    assert got["first"][0].data == (0x20, 1, 0x55) and got["first"][1].data == (0x20, 1, 0x56)
    assert [r[:3] for r in got["bytes"]] == [(0x20, 0x55, 0x56)]
    got = trace.compare_stores(lay, a, stores((0x10, 4, 0x11223344)))
    assert got["first"] == (a[1], None)
    assert trace.compare_stores(lay, stores((0x30, 4, 0x4100)), stores((0x30, 4, 0x4200))) is None
    assert trace.compare_stores(lay, stores((0x30, 4, 0x4100)), stores((0x30, 4, 0x6200))) is not None


@pytest.mark.slow
def test_a_long_run_is_compared_as_far_as_its_events_are_kept(gizmo, monkeypatch):
    """Beyond MAX_EVENTS of the original's events nothing more is compared (the memory they take): the
    report says so, and the end is still what dread test compares."""
    target, image = gizmo
    monkeypatch.setattr(trace, "MAX_EVENTS", 5)
    result, _, text = traced(target, image, "print_status", "generated 0")
    assert result.cmp.truncated and result.agrees, text
    assert "the original's run made more than 5 shared events" in text
