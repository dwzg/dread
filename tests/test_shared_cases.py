"""[paths] cases: where generated cases are cached (Target.cases_dir; default BUILD/autocases). Two
copies of one target that point it at one directory share the cache: the second copy's lift check
makes no case anew and keeps no cache of its own, and accepts what the first did."""
import os
import shutil

import pytest


def copy_of(tmp_path, name, shared):
    from conftest import GIZMO
    from dread.target import Target
    shutil.copytree(GIZMO, tmp_path / name, ignore=shutil.ignore_patterns("build"))   # (m3's C includes its
    root = tmp_path / name / "m3"                                                        # siblings' files)
    toml = root / "dread.toml"
    text = toml.read_text()
    assert "[paths]" not in text
    toml.write_text(text + '\n[paths]\ncases = "%s"\n' % os.path.relpath(shared, root))
    return Target.load(str(root))


def test_the_default_and_a_relative_cases_path(tmp_path):
    from conftest import GIZMO
    from dread.target import Target
    plain = Target.load(os.path.join(GIZMO, "m3"))
    assert plain.cases_dir(plain.image()) == os.path.join(plain.build, "autocases", plain.image().name)
    t = copy_of(tmp_path, "a", tmp_path / "shared")
    assert t.cases_dir(t.image()) == os.path.join(str(tmp_path / "shared"), t.image().name)


@pytest.mark.slow
@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_two_copies_share_their_generated_cases(tmp_path):
    from dread import build, lift
    shared = tmp_path / "shared"
    a, b = copy_of(tmp_path, "a", shared), copy_of(tmp_path, "b", shared)
    assert lift.main(["--target", a.root, "--check"]) == 0
    entries = {p: os.path.getmtime(p) for p in shared.rglob("*.json")}
    assert len(entries) > 30 and not os.path.exists(os.path.join(a.build, "autocases"))
    assert lift.main(["--target", b.root, "--check"]) == 0
    assert {p: os.path.getmtime(p) for p in shared.rglob("*.json")} == entries     # (none made anew)
    assert not os.path.exists(os.path.join(b.build, "autocases"))
    accepted = [sorted(build.read_names(t.image().table("lifted"))) for t in (a, b)]
    assert accepted[0] == accepted[1] and len(accepted[0]) > 30
