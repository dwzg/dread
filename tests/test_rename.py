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


@needs_gcc
def test_the_entry_in_the_target_file_follows(gizmo):
    """The image's entry names a function in the target file (entry = "Reset_Handler"): it follows
    the rename, or the target no longer loads (found renaming every function of a FreeRTOS build to
    FUN_address for the pilot without names)."""
    target, image = gizmo
    old = next(fn.name for fn in Program(target, image).functions if fn.entry == image.entry & ~1)
    rename.rename(target, image, {old: "start_here"}, outside=True)
    again = Target.load(target.file)
    assert 'entry = "start_here"' in open(target.file).read()
    assert Program(again, again.image()).by_name["start_here"].entry == again.image().entry & ~1


@needs_gcc
def test_names_before_comments_and_the_readable_tables_follow(gizmo):
    """A list table's name with a comment after it (the lift check's "# accepted by the system run")
    and the readable C's tables (the readable list, the names' functions and structure anchors, the
    structures plan's functions) follow a rename (found renaming a FreeRTOS build's functions to
    FUN_address: the system run's entries kept their old names)."""
    import json
    from dread import shapes
    target, image = gizmo
    names = [n for n in open(image.table("lifted")).read().split() if n == "beep_on"]
    with open(image.table("lifted"), "a") as f:
        f.write("" if names else "beep_on\n")
    text = open(image.table("lifted")).read().replace("beep_on\n", "beep_on  # accepted by the system run\n")
    with open(image.table("lifted"), "w") as f:
        f.write(text)
    with open(image.table("readable"), "w") as f:
        f.write("beep_on\nbeep_off\n")
    with open(image.table("readable_names"), "w") as f:
        json.dump({"version": 1, "functions": {"beep_on": {"digest": "d", "names": {"r0": "x"}, "what": "w", "by": "t"}},
                   "structs": {"beep_on:r0": {"name": "beeper", "fields": {}, "anchors": ["beep_on:r0", "beep_off:r4"],
                                              "by": "t"}},
                   "globals": {"0x20000030 u32": {"name": "volume", "by": "t"}}}, f)
    plan = shapes.Plan()
    plan.structs, plan.typed, plan.digests = {"s_1": [(0, "u32")]}, {"beep_on": {"r0": "s_1"}}, {"beep_on": "d"}
    shapes.store_plan(image, plan)
    rename.rename(target, image, {"beep_on": "buzzer_start"})
    assert "buzzer_start  # accepted by the system run\n" in open(image.table("lifted")).read()
    assert open(image.table("readable")).read() == "buzzer_start\nbeep_off\n"
    data = json.load(open(image.table("readable_names")))
    assert list(data["functions"]) == ["buzzer_start"] and list(data["structs"]) == ["buzzer_start:r0"]
    assert data["structs"]["buzzer_start:r0"]["anchors"] == ["buzzer_start:r0", "beep_off:r4"]
    assert data["globals"] == {"0x20000030 u32": {"name": "volume", "by": "t"}}
    after = shapes.load_plan(image)
    assert after.typed == {"buzzer_start": {"r0": "s_1"}} and after.digests == {"buzzer_start": "d"}
