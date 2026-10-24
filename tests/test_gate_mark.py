"""dread readable's first line, and the GUI's reading view, say where the readable check stands on a
function (dread.readable gate_states, gate_note): accepted, rejected with the check's reason, never
checked (not in the lifted table) or not checked by any check yet. Until 2026-10-25 every function
printed alike."""
import os
import shutil

import pytest

from dread import readable

VERDICTS = ("function\tverdict\treason\n"
            "strlen\treadable\t\n"
            "memcpy\tlifted\tFAIL memcpy [generated 1]\n"
            "Reset_Handler\tnot lifted\tnot in the lifted table: dread lift --check first\n")


class Image:
    name = "main"

    def __init__(self, root):
        self.root = root

    def table(self, kind):
        return os.path.join(self.root, kind + ".txt")


class Target:
    def __init__(self, root):
        self.build = os.path.join(root, "build")


def plant(root, build, image_name="main"):
    """A readable table that names strlen, and the check's verdicts beside it."""
    work = os.path.join(build, "readable-check", image_name)
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "verdicts.tsv"), "w") as f:
        f.write(VERDICTS)


def test_states_and_notes(tmp_path):
    plant(str(tmp_path), str(tmp_path / "build"))
    (tmp_path / "readable.txt").write_text("# what dread readable --check accepted\nstrlen\n")
    states = readable.gate_states(Target(str(tmp_path)), Image(str(tmp_path)))
    assert states == {"strlen": ("accepted", ""), "memcpy": ("rejected", "FAIL memcpy [generated 1]"),
                      "Reset_Handler": ("not lifted", "not in the lifted table: dread lift --check first")}
    assert readable.gate_note("strlen", states) == "accepted by dread readable --check"
    assert readable.gate_note("memcpy", states) == \
        "not accepted by dread readable --check: FAIL memcpy [generated 1]"
    assert readable.gate_note("Reset_Handler", states).startswith("never checked")
    assert readable.gate_note("fmt_dec", states) == "not checked: no dread readable --check has seen it"
    line = "/* f at 0x100: readable C from its lifted C (dread readable) */"
    assert readable.marked(line, "accepted by dread readable --check") == \
        "/* f at 0x100: readable C from its lifted C (dread readable); accepted by dread readable --check */"


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_dread_readable_prints_the_standing_in_its_first_line(tmp_path, capsys):
    """On a copy of gizmo m3 with a planted readable table and verdicts: strlen accepted, memcpy rejected
    with the check's reason, fmt_dec unchecked; the printed and the --plain forms alike."""
    from conftest import GIZMO
    from dread.target import Target as RealTarget
    shutil.copytree(GIZMO, tmp_path / "gizmo", ignore=shutil.ignore_patterns("build"))
    target = RealTarget.load(str(tmp_path / "gizmo" / "m3"))
    image = target.image()
    plant(target.root, target.build, image.name)
    with open(image.table("readable"), "w") as f:
        f.write("# what dread readable --check accepted\nstrlen\n")
    for plain in (False, True):
        assert readable.main(["--target", target.root] + (["--plain"] if plain else []) +
                             ["strlen", "memcpy", "fmt_dec"]) == 0
        out = capsys.readouterr().out
        firsts = [line.strip() for line in out.splitlines() if "readable C from its lifted C" in line]
        assert len(firsts) == 3, out
        assert firsts[0].endswith("(dread readable); accepted by dread readable --check */"), firsts
        assert "not accepted by dread readable --check: FAIL memcpy [generated 1] */" in firsts[1], firsts
        assert firsts[2].endswith("not checked: no dread readable --check has seen it */"), firsts
