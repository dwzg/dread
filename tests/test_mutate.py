"""dread mutate: finding function bodies in C, the mutants, and a whole check with --kill on a copy
of gizmo (the equivalent survivors stay, the clamp mutant a search can catch is caught)."""
import json
import os
import shutil

import pytest

from dread import mutate
from dread.target import Target
from conftest import GIZMO, have

C = '''/* int fake(int x) { return x; } */
static const char *s = "int fake(int x) {";

int knr(int a, int b) {
    if (a < b) { return a; }
    return b + 1;
}

int
allman(int a)
{
    // a comment with a brace }
    return a == 2;
}

static inline __attribute__((always_inline)) int helper(int a)
{
    return a & 3;
}
'''


def test_find_function_in_both_styles(tmp_path):
    path = tmp_path / "x.c"
    path.write_text(C)
    _, lines, start, end, static = mutate.find_function([str(path)], "knr")
    assert lines[start].strip().startswith("if") and lines[end].strip() == "}" and not static
    _, lines, start, end, _ = mutate.find_function([str(path)], "allman")
    assert lines[start - 1].strip() == "{" and "return a == 2;" in lines[end - 1]
    _, lines, start, end, static = mutate.find_function([str(path)], "helper")
    assert static
    with pytest.raises(KeyError):
        mutate.find_function([str(path)], "fake")


def test_mutants_are_fixed_and_complete(tmp_path):
    path = tmp_path / "x.c"
    path.write_text(C)
    _, lines, start, end, _ = mutate.find_function([str(path)], "knr")
    found = [(i, what) for i, new, what in mutate.mutants(lines, start, end)]
    assert found == list(dict.fromkeys(found))              # no change twice
    whats = [w for _, w in found]
    assert "< -> <=" in whats and "+ -> -" in whats and "1 -> 2" in whats
    again = [(i, what) for i, new, what in mutate.mutants(lines, start, end)]
    assert found == again


@pytest.mark.slow
@pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_check_and_kill_on_a_copy(tmp_path):
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build", "extra_cases"))
    target = Target.load(str(copy / "a32"))
    image = target.image()
    from dread import build
    build.build(target, image, quiet=True)
    lines, entry = [], {}
    caught, survivors, unbuilt, away = mutate.check(target, image, "checksum", jobs=4, out=lines.append, record=entry)
    assert survivors == [] and "2 argued" in lines[0]                       # tests/argued.tsv
    assert entry["survived"] == 0 and len(entry["argued"]) == 2 and all(a["argument"] for a in entry["argued"])
    assert entry["file"].endswith(".c") and entry["first"] <= entry["argued"][0]["line"] <= entry["last"]
    os.remove(mutate.argued_path(image))
    entry = {}
    caught, survivors, unbuilt, away = mutate.check(target, image, "checksum", jobs=4, out=lines.append, record=entry)
    assert [what for _, _, what in survivors] == [">= -> >", "4 -> 5"]      # equivalent: the tail loop takes over
    # dread mutate --json's entry: each survivor's line before and after, no search without --kill
    assert [s["what"] for s in entry["survivors"]] == [">= -> >", "4 -> 5"] and entry["argued"] == []
    assert all(s["old"] != s["new"] and s["search"] is None for s in entry["survivors"])
    assert entry["caught"] == caught and entry["changes"] >= caught + 2 and not entry["kill"]
    entry = {}
    caught, survivors, unbuilt, away = mutate.check(target, image, "clamp", jobs=4, out=lines.append,
                                                    kill_survivors=True, record=entry)
    assert survivors == [] and os.path.exists(os.path.join(image.extra_cases, "clamp.json"))
    assert any("compiled to the same code" in line for line in lines)
    assert entry["kill"] and entry["killed"] and entry["survivors"] == [] and entry["same_code"] >= 1
    out = tmp_path / "mutate.json"
    assert mutate.main(["--target", str(copy / "a32"), "--json", str(out), "no_such_function"]) == 1
    got = json.loads(out.read_text())
    assert got["functions"] == {} and "no_such_function" in got["error"] and got["version"] == mutate.JSON_VERSION
