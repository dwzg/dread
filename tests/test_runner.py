"""The overlay and the differential runner on gizmo: the rewritten functions agree with the original
on both builds, and the runner catches a planted bug, missing coverage and a wrong unreachable()."""
import os
import shutil

import pytest

from dread import build, runner
from conftest import GIZMO, have

pytestmark = pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")


def run_all(target, payload, cases_dir=None, names=None):
    image = target.image()
    if cases_dir:
        image.cases = str(cases_dir)
    suite = runner.prepare(target, image, payload)
    lines = []
    names = names or list(suite.cases)
    failed, uncovered, results = runner.run(names, out=lines.append, jobs=2)
    return failed, uncovered, "\n".join(lines)


def src_copy(tmp_path, variant, replace=None):
    """The target's src with rewritten.c inlined from examples/gizmo/rewritten, optionally edited."""
    src = tmp_path / "src"
    src.mkdir(parents=True)
    shutil.copy(os.path.join(GIZMO, variant, "src", "addresses.h"), src)
    with open(os.path.join(GIZMO, "rewritten", "rewritten.c")) as f:
        text = f.read()
    if replace:
        assert replace[0] in text
        text = text.replace(*replace)
    (src / "rewritten.c").write_text('#include "addresses.h"\n' + text)
    return src


def test_rewritten_functions_agree(target, variant, tmp_path):
    payload = build.build(target, target.image(), src=str(src_copy(tmp_path, variant)), out=str(tmp_path / "recon"),
                          quiet=True)
    failed, uncovered, text = run_all(target, payload)
    assert failed == 0 and not uncovered, text
    assert "ok   checksum" in text and "ok   dispatch" in text


def test_planted_bug_is_caught(target, variant, tmp_path):
    """An unsigned comparison where the original compares signed: -1 is clamped to 9, not 0.
    (x <= lo ? lo or x >= hi ? hi would not do: they are equivalent to the original.)"""
    src = src_copy(tmp_path, variant, ("return x < lo ? lo : x > hi ? hi : x;",
                                       "return (u32)x < (u32)lo ? lo : (u32)x > (u32)hi ? hi : x;"))
    payload = build.build(target, target.image(), src=str(src), out=str(tmp_path / "recon"), quiet=True)
    failed, uncovered, text = run_all(target, payload, names=["clamp", "mode_name"])
    assert "FAIL clamp [-1 in [0, 9]]" in text and "ok   mode_name" in text


def test_missing_coverage_and_wrong_unreachable(target, variant, tmp_path):
    payload = build.build(target, target.image(), src=str(src_copy(tmp_path, variant)), out=str(tmp_path / "recon"),
                          quiet=True)
    cases = tmp_path / "cases"
    cases.mkdir()
    # clamp would not do: it compiles to conditional instructions without branches, and coverage
    # counts a conditional instruction as run whether its condition held or not
    (cases / "few.py").write_text(
        'def text(m):\n'
        '    m.write(SCRATCH, b"7\\0")\n'
        'case("parse_uint", "decimal only", text, SCRATCH, SCRATCH + 0x100)\n'
        'fn = program.by_name["mode_name"]\n'
        'case("mode_name", "mode 1", lambda m: None, 1)\n'
        'unreachable("mode_name", "a wrong claim, for the test", fn.insns[0])\n')
    failed, uncovered, text = run_all(target, payload, cases_dir=cases)
    assert "UNCOVERED parse_uint" in text and "parse_uint" in uncovered
    assert "FAIL mode_name [unreachable]" in text
