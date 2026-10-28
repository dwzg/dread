"""dread status: README.md's status section, made from what the repository records (dread.status)."""

import os

from dread import status

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TODO = """# What to do next

## Readable C (the user, 2026-10-21)

- [x] **A parser.** Done.
- [ ] **Structures**: behind pointers.
  - [x] found by code
  - [ ] written into the C
- [ ] Names.

## More architectures (each: a gizmo build)

- [ ] MIPS32: delay slots. Paused after step 1 (the user, 2026-10-20).
- [ ] **x86 and x86-64 (amd64)**, freestanding ELF images first.
  - [x] The arch layer.
  - [x] The lifter.
  - [ ] AMD64.
- [ ] AArch64: adrp pairs.
- [x] RISC-V (RV32IMC), in the porting guide's order.

## MSP430 (of special interest)

**Paused by the user on 2026-10-20**.

- [x] gizmo-msp430
- [ ] MSP430X

## C++

- [x] **C++ originals**.

## Questions for the user

- [ ] Which compiler?
"""


def test_the_readme_status_is_current():
    """README.md's section between the markers is what dread status writes: run bin/dread status --write in a
    commit that changes docs/todo.md's checkboxes, a gizmo build's table, the bench recipe, the commands or
    the tests' count."""
    with open(os.path.join(ROOT, "README.md")) as f:
        _, current, _ = status.readme_parts(f.read())
    assert current == status.render(ROOT), "README.md's status section is not current: run bin/dread status --write"


def test_progress_of_items_and_sections():
    """An item's progress is that of the checkboxes under it; ticked, done; its own text saying paused, paused;
    with none under it, planned. A section is done when every checkbox in it is, paused where its text before
    its first checkbox says so."""
    sections, items = status.parse_todo(TODO)
    by = {s.title.split(" (")[0]: s for s in sections}
    assert (by["Readable C"].done, by["Readable C"].total) == (2, 5)
    assert by["MSP430"].progress() == ("paused", 1, 2)
    assert by["C++"].progress() == ("done", 1, 1)
    x86 = status.find(sections, items, ("item", r"\*\*x86 and x86-64"))
    assert x86.progress() == ("steps", 2, 3)
    assert status.find(sections, items, ("item", r"MIPS32")).progress()[0] == "paused"
    assert status.find(sections, items, ("item", r"AArch64")).progress() == ("planned", 0, 0)
    assert status.find(sections, items, ("item", r"RISC-V")).progress()[0] == "done"
    assert status.progress_text(x86) == "`%s` 2 of 3 steps" % ("\u2588" * 7 + "\u2591" * 3)
    assert status.short_title("Compiler lab (`dread cclab`; the user, 2026-10-19: before anything else)") == "Compiler lab"


def test_anchors_are_githubs():
    """The features link to docs/todo.md's sections by GitHub's anchors: lower case, punctuation left out,
    spaces as hyphens, a repeated heading numbered."""
    slug = status.slugger()
    assert slug("Readable C from lifted C (the user, 2026-10-21; first since then: docs/decisions.md)") == \
        "readable-c-from-lifted-c-the-user-2026-10-21-first-since-then-docsdecisionsmd"
    assert slug("C++") == "c"
    assert slug("Phase 4: cases, mutation, coverage (rest)") == "phase-4-cases-mutation-coverage-rest"
    assert slug("C++") == "c-1"


def test_the_section_needs_its_markers():
    try:
        status.readme_parts("# no markers here\n")
    except ValueError:
        return
    raise AssertionError("a README without markers was taken")


def test_a_lifted_table_git_does_not_track_is_not_counted(tmp_path):
    """The section counts a gizmo build's lifted functions from config/lifted.txt, but only one git
    tracks (or has staged): a lift check run by hand leaves an untracked table behind, which made the
    section say 31 / 47 for a build the repository keeps no table of (2026-10-28). Outside a git
    checkout the files there count (and without git at all: CI's container image has none, where the
    rest is skipped)."""
    import shutil
    import subprocess
    import pytest
    config = tmp_path / "examples" / "gizmo" / "x" / "config"
    config.mkdir(parents=True)
    (config / "functions.tsv").write_text("address\tname\tsize\n0x10\ta\t4\n0x14\tb\t4\n0x18\tc\t4\n")
    (config / "lifted.txt").write_text("a\nb\n")
    assert status.gizmo_figures(str(tmp_path), "x") == (3, 2)          # (no git checkout)
    if not shutil.which("git"):
        pytest.skip("needs git")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert status.gizmo_figures(str(tmp_path), "x") == (3, None)       # (untracked)
    subprocess.run(["git", "-C", str(tmp_path), "add", "examples/gizmo/x/config/lifted.txt"], check=True)
    assert status.gizmo_figures(str(tmp_path), "x") == (3, 2)          # (staged)
