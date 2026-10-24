"""dread readable --check where a function's readable C calls what the original does not (a loop GCC
turns into memcpy, a helper): the build's vet keeps it from the link (dread.lift foreign_calls), and
where that misses, a link the linker refuses drops the functions whose objects its errors name
(dread.overlay blame) and links again. Until 2026-10-25 the check asked after the link, and a refused
link failed the whole check. A call of a function nobody defines, put into one function's readable C."""
import re
import shutil

import pytest

from dread import lift, readable

NAME = "strlen"
HELPER = "dread_missing_helper"

needs_arm = pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")


def gizmo(tmp_path):
    from conftest import GIZMO
    from dread.target import Target
    shutil.copytree(GIZMO, tmp_path / "gizmo", ignore=shutil.ignore_patterns("build"))
    target = Target.load(str(tmp_path / "gizmo" / "m3"))
    return target, target.image()


def inject(monkeypatch):
    """NAME's readable C calls HELPER, which nothing defines, first thing in its body (nothing kept of the
    readable C an earlier test made of the same program: readable_texts' store, the memo)."""
    monkeypatch.setattr(readable, "_STORE", {})
    monkeypatch.setattr(readable, "_MEMO", {})
    real = readable._transform

    def made(name, text, stages, context=None, **kwargs):
        got = real(name, text, stages, context, **kwargs)
        if name == NAME and got is not None:
            m = re.search(r"^[^\n]*\b%s\([^\n]*\)\n\{\n" % NAME, got, re.M)
            assert m, got
            got = (got[:m.start()] + "extern void %s(void);\n" % HELPER + got[m.start():m.end()] +
                   "    %s();\n" % HELPER + got[m.end():])
        return got
    monkeypatch.setattr(readable, "_transform", made)


@pytest.mark.slow
@needs_arm
def test_readable_c_that_calls_what_the_original_does_not_is_kept_from_the_link(tmp_path, monkeypatch):
    target, image = gizmo(tmp_path)
    inject(monkeypatch)
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert NAME not in accepted and len(accepted) > 30, (accepted, lines)
    assert any(NAME in l and "its readable C calls %s, which the original does not call" % HELPER in l
               for l in lines), lines


@pytest.mark.slow
@needs_arm
def test_a_link_the_readable_c_makes_the_linker_refuse_drops_that_function(tmp_path, monkeypatch):
    """With the vet told nothing, the linker's undefined reference names NAME's object: it is dropped
    with the linker's words, and the others are checked as before."""
    target, image = gizmo(tmp_path)
    inject(monkeypatch)
    monkeypatch.setattr(lift, "foreign_calls", lambda *args, **kwargs: {})
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert NAME not in accepted and len(accepted) > 30, (accepted, lines)
    assert any(NAME in l and "link" in l and HELPER in l for l in lines), lines
