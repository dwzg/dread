"""dread discover: function and code tables from following the code, on gizmo-arc's raw image
against the tables its ELF gives."""
import os
import shutil

from conftest import gizmo_dir
from dread import discover
from dread.program import Program, read_functions
from dread.target import Target


def raw_copy(tmp_path, variant):
    """The build's target without its tables (the raw image alone), the entry as an address."""
    root = tmp_path / variant
    root.mkdir()
    shutil.copytree(os.path.join(gizmo_dir(variant), "original"), root / "original")
    example = Target.load(gizmo_dir(variant))
    with open(os.path.join(gizmo_dir(variant), "dread.toml")) as f:
        text = f.read()
    (root / "dread.toml").write_text(text.replace('entry = "_start"', "entry = 0x%x" % example.image().entry))
    return Target.load(str(root)), example


def test_the_functions_of_gizmo_arc_from_its_raw_image(tmp_path):
    """Every function the ELF names is found at its entry and nothing else: calls, the vectors,
    the tail calls (led_on to gpio_set), the switch tables (handle_key's and script_run's: their
    cases are the function's own), a table of handlers (dispatch's, through dread.switches), code
    only a pointer reaches and code nothing reaches (memcpy and the other library functions gizmo
    does not call), the code the startup copies to RAM (at its run address); not the vector slots,
    the stored copy, or the strings behind the code. Each function's instructions lie in its
    function in the ELF; what the ELF counts beside them is a switch table or a padding nop."""
    target, example = raw_copy(tmp_path, "arc")
    functions, runs, cut, left, _ = discover.discover(target, target.image())
    truth = {e: (name, size) for e, name, size in read_functions(example.image().table("functions"))}
    assert sorted(e for e, _, _ in functions) == sorted(truth)
    # left: libgcc's __umodsi3 returns through a copy of blink kept in r7 across __udivmodsi4 (mov
    # r7,blink; bl; j.d [r7]), which no run resolves; until 2026-11-07 dread.switches took the jump's
    # delay slot for where it lands, and the jump counted as resolved
    found, elf = Program(target), Program(example)
    assert cut == 0 and left == [elf.by_name["__umodsi3"].entry + 8]
    for fn in found.functions:
        true = elf.by_entry[fn.entry]
        end = true.insns[-1] + elf.insn_size(true.insns[-1])     # (the ELF's side decodes a table's
        assert all(true.entry <= a and a + found.insn_size(a) <= end for a in fn.insns), true.name  # bytes too)
        assert fn.size == true.size or true.name in ("script_run", "handle_key", "__muldi3"), true.name


def test_the_tables_are_the_same_bytes_every_time(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a, _ = raw_copy(tmp_path / "a", "arc")
    b, _ = raw_copy(tmp_path / "b", "arc")
    discover.discover(a, a.image())
    discover.discover(b, b.image())
    for name in ("functions.tsv", "code_ranges.tsv"):
        with open(os.path.join(a.image().config, name), "rb") as f, open(os.path.join(b.image().config, name), "rb") as g:
            assert f.read() == g.read(), name
