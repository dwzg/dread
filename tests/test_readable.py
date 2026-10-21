"""dread readable: the transformations from lifted C to readable C and the gate that decides which
functions are built from it. The gate on a copy of gizmo-m3: with the transformations as they are,
every lifted function is accepted and the build is the one lifted C gives (so far they change no
code); a stage that breaks one function leaves that one lifted and the others readable."""
import os
import shutil

import pytest

from dread import build, cparse, readable
from conftest import gizmo_dir

needs_gcc = pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")


def lifted_text(target, image, name, tmp_path):
    build.lift_sources(target, image, [name], str(tmp_path / "one"), quiet=True)
    return (tmp_path / "one" / (name + ".c")).read_text()


@needs_gcc
def test_a_lifted_file_comes_back_readable_or_not_at_all(tmp_path):
    from dread.target import Target
    target = Target.load(gizmo_dir("m3"))
    image = target.image()
    text = lifted_text(target, image, "fmt_dec", tmp_path)
    out = readable.transform("fmt_dec", text, stages=[])         # (no stage: the same tree)
    assert out is not None and cparse.parse(out) == cparse.parse(text)
    out = readable.transform("fmt_dec", text)                    # (the stages: shorter, still C of the dialect)
    assert out is not None and cparse.parse(out).function("fmt_dec") is not None and len(out) < len(text)
    assert readable.transform("fmt_dec", "u32 fmt_dec(u32 a0) { return ({ a0; }); }") is None   # not the dialect
    assert readable.transform("other", text) is None                                         # no such function
    seen = []
    out = readable.transform("fmt_dec", text, stages=[("look", lambda fn, run: seen.append(fn.name))])
    assert seen == ["fmt_dec"]
    change = readable.chooser(["fmt_dec"])
    assert change("fmt_dec", text) is not None and change("parse_uint", text) is None


def gizmo_copy(tmp_path):
    """gizmo in tmp_path (its builds share sources), gizmo-m3's target with the cases the lift check
    accepted its functions with when they are cached."""
    from conftest import GIZMO
    from dread.target import Target
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    cases = os.path.join(gizmo_dir("m3"), "build", "autocases")
    if os.path.isdir(cases):
        shutil.copytree(cases, copy / "m3" / "build" / "autocases")
    return Target.load(str(copy / "m3"))


@pytest.mark.slow
@needs_gcc
def test_the_gate_takes_every_lifted_function_and_builds_the_same_image(tmp_path, monkeypatch):
    """With no stage (the identity): every lifted function, and the image lifted C gives."""
    monkeypatch.setattr(readable, "STAGES", [])
    target = gizmo_copy(tmp_path)
    image = target.image()
    lifted = build.read_names(image.table("lifted"))
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert accepted == sorted(lifted), lines
    assert build.read_names(image.table("readable")) == sorted(lifted)
    a = open(build.build(target, image, out=str(tmp_path / "with"), quiet=True), "rb").read()
    os.remove(image.table("readable"))
    b = open(build.build(target, image, out=str(tmp_path / "without"), quiet=True), "rb").read()
    assert a == b


@pytest.mark.slow
@needs_gcc
def test_a_stage_that_breaks_a_function_leaves_it_lifted(tmp_path, monkeypatch):
    """A stage that makes fmt_dec's every return one more: its cases fail, so fmt_dec is built from
    its lifted C again and is not in the readable table; the others are."""
    target = gizmo_copy(tmp_path)
    image = target.image()

    def off_by_one(fn, run=None):
        if fn.name != "fmt_dec":
            return

        def walk(stmts):
            for s in stmts:
                if isinstance(s, cparse.Return) and s.e is not None:
                    s.e = cparse.Binary("+", s.e, cparse.Num("1"))
                for child in ("body", "then", "orelse"):
                    inner = getattr(s, child, None)
                    if isinstance(inner, cparse.Block):
                        walk(inner.body)
                    elif isinstance(inner, cparse.Stmt):
                        walk([inner])
        walk(fn.body.body)
    monkeypatch.setattr(readable, "STAGES", [("off by one", off_by_one)])
    lifted = build.read_names(image.table("lifted"))
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert "fmt_dec" in lifted and "fmt_dec" not in accepted, lines
    assert accepted == sorted(n for n in lifted if n != "fmt_dec"), lines
    assert any(line.startswith("lifted   fmt_dec") and "FAIL fmt_dec" in line for line in lines), lines
    verdicts = open(os.path.join(target.build, "readable-check", image.name, "verdicts.tsv")).read()
    assert "fmt_dec\tlifted\tFAIL fmt_dec" in verdicts


@pytest.mark.slow
@needs_gcc
def test_the_clean_up_passes_the_gate_and_is_shorter(tmp_path):
    """With the clean-up (dread.tidy): every lifted function of gizmo-m3 is accepted, the whole image
    agrees, and the readable C has fewer lines than the lifted (the flags, the (void)s, the zeroed
    registers after calls are gone)."""
    target = gizmo_copy(tmp_path)
    image = target.image()
    lifted = build.read_names(image.table("lifted"))
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert accepted == sorted(lifted), lines
    before = after = 0
    for name in lifted:
        text = lifted_text(target, image, name, tmp_path)
        before += text.count("\n")
        after += readable.transform(name, text).count("\n")
    assert after < before * 0.8, (before, after)
