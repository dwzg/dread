"""dread rename on a copy of gizmo: every file that names the function follows, the result builds and
its cases agree; refused pairs and a rename the tree contradicts change nothing."""
import os
import shutil

import pytest

from dread import rename
from dread.program import Program
from dread.target import Target
from conftest import GIZMO, gizmo_dir, have

needs_gcc = pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")


@pytest.fixture
def gizmo(tmp_path):
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    for cache in ("autocases", "regflow"):
        here = os.path.join(gizmo_dir("m3"), "build", cache)
        if os.path.isdir(here):
            shutil.copytree(here, copy / "m3" / "build" / cache)
    target = Target.load(str(copy / "m3"))
    return target, target.image()


def tables(image):
    return {t: open(image.table(t)).read() for t in ("functions", "symbols", "lifted")}


@needs_gcc
def test_refused_pairs_change_nothing(gizmo):
    target, image = gizmo
    before = tables(image)
    for pairs, why in (({"nosuch": "x"}, "no function nosuch"),
                       ({"beep_on": "9lives"}, "not a C identifier"),
                       ({"beep_on": "beep_off"}, "already the name"),
                       ({"clamp": "clamp_range"}, "outside the target's directory")):
        with pytest.raises(rename.Refused, match=why):
            rename.rename(target, image, pairs)
    assert tables(image) == before


@needs_gcc
def test_a_rename_the_tree_contradicts_is_undone(gizmo):
    """A case file that builds the name from pieces does not load after the change: undone."""
    target, image = gizmo
    with open(os.path.join(image.cases, "odd.py"), "w") as f:
        f.write('auto("beep" + "_on")\n')
    before = tables(image)
    cache = os.path.join(target.build, "autocases", image.name)
    with pytest.raises(rename.Refused, match="do not load.*nothing was changed"):
        rename.rename(target, image, {"beep_on": "buzzer_start"})
    assert tables(image) == before
    assert os.path.exists(os.path.join(cache, "beep_on.json"))
    assert not os.path.exists(os.path.join(cache, "buzzer_start.json"))


@pytest.mark.slow
@needs_gcc
def test_a_renamed_function_builds_and_agrees(gizmo):
    target, image = gizmo
    changes = rename.rename(target, image, {"beep_on": "buzzer_start"})
    changed = {os.path.basename(p) for p, _, _ in changes}
    assert {"functions.tsv", "symbols.txt", "lifted.txt", "beep_on.json"} <= changed
    program = Program(target, image)
    assert "buzzer_start" in program.by_name and "beep_on" not in program.by_name
    assert os.path.exists(os.path.join(target.build, "autocases", image.name, "buzzer_start.json"))
    from dread import build, runner
    payload = build.build(target, image, quiet=True)
    runner.prepare(target, image, payload)
    lines = []
    failed, uncovered, results = runner.run(["buzzer_start", "cmd_beep"], only=True, out=lines.append, jobs=4)
    assert not failed and not uncovered and len(results) == 2, "\n".join(lines)
