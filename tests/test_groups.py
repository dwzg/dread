"""dread groups: Louvain's communities on graphs made by hand, the scores against a true partition,
and the groups of gizmo's builds: the same every time, every function in one, mostly one source file
a group (DWARF), the same with every name taken away, and the command line."""
import os
import shutil

import pytest

from dread import elfimport, groups
from dread.program import Program
from dread.target import Target
from conftest import GIZMO, gizmo_dir

UART = {"uart_init", "uart_putc", "uart_puts", "uart_getc"}


def copy_of(tmp, variant):
    copy = os.path.join(str(tmp), "gizmo")
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    here = os.path.join(gizmo_dir(variant), "build", "regflow")
    if os.path.isdir(here):
        shutil.copytree(here, os.path.join(copy, variant, "build", "regflow"))
    return Target.load(os.path.join(copy, variant))


def elf_of(target):
    return os.path.join(target.root, "original", "gizmo.elf")


def test_two_cliques_and_a_weak_bridge():
    """Two groups of four functions that share everything among themselves, one weak edge between
    them: two communities, numbered by their first node; a graph without edges keeps every node
    alone; the same graph gives the same answer."""
    edges = {(i, j): 1.0 for part in (range(4), range(4, 8)) for i in part for j in part if i < j}
    edges[(3, 4)] = 0.1
    assert groups.louvain(8, edges, resolution=1.0) == [0, 0, 0, 0, 1, 1, 1, 1]
    assert groups.louvain(8, dict(reversed(list(edges.items()))), resolution=1.0) == [0, 0, 0, 0, 1, 1, 1, 1]
    assert groups.louvain(3, {}) == [0, 1, 2]
    chain = {(i, i + 1): 1.0 for i in range(11)}           # (neighbours only: runs of neighbours)
    found = groups.louvain(12, chain, resolution=1.0)
    assert found == sorted(found) and 2 <= len(set(found)) <= 6


def test_the_scores_of_a_partition():
    """The adjusted Rand index, purity and completeness: 1 for the true partition under other labels;
    one group for everything is as good as chance and pure only by its largest file; a case worked by
    hand."""
    truth = {"a": "x", "b": "x", "c": "y", "d": "y"}
    assert groups.score({"a": 7, "b": 7, "c": 3, "d": 3}, truth) == {
        "functions": 4, "groups": 2, "files": 2, "ari": 1.0, "purity": 1.0, "completeness": 1.0}
    one = groups.score({k: 0 for k in truth}, truth)
    assert one["ari"] == 0.0 and one["purity"] == 0.5 and one["completeness"] == 1.0
    mine = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2}
    true = {1: "x", 2: "x", 3: "y", 4: "y", 5: "z", 6: "z", 7: "z"}     # (7 has no group: left out)
    s = groups.score(mine, true)
    assert s["functions"] == 6 and abs(s["ari"] - 0.8 / 3.3) < 1e-9
    assert abs(s["purity"] - 4 / 6) < 1e-9 and abs(s["completeness"] - 5 / 6) < 1e-9


def test_shared_things_weigh_by_how_many_share_them(m3_copy):
    """graph(): a thing k functions touch gives each pair 1/(k - 1); one more than MAX_SHARED touch
    gives nothing; neighbours in address order get ADJACENT; an address taken links the two."""
    target, program = m3_copy
    order = [fn.name for fn in sorted(program.functions, key=lambda f: f.entry)]
    a, b, c, d = order[:4]
    touched = {a: [("global", 0x100)], b: [("global", 0x100), ("text", 9)], c: [("global", 0x100), ("text", 9)],
               d: [("taken", program.by_name[a].entry)]}
    names, edges = groups.graph(program, touched, adjacent=0.0)
    i = {n: k for k, n in enumerate(names)}
    assert edges[(i[a], i[b])] == 0.5 and edges[(i[b], i[c])] == 1.5 and edges[(i[a], i[d])] == groups.TAKEN
    names, edges = groups.graph(program, touched, adjacent=0.0, max_shared=2)
    assert (i[a], i[b]) not in edges and edges[(i[b], i[c])] == 1.0
    names, edges = groups.graph(program, {}, adjacent=0.25)
    assert edges == {(k, k + 1): 0.25 for k in range(len(names) - 1)}


@pytest.fixture(scope="module")
def m3_copy(tmp_path_factory):
    target = copy_of(tmp_path_factory.mktemp("groups"), "m3")
    return target, Program(target)


@pytest.mark.slow
def test_every_build_groups_its_functions_by_their_files(variant, tmp_path, capsys):
    """Each gizmo build: every function in one group, the groups in address order, the same groups a
    second time (from the facts kept), the UART driver's four functions in one group, and most of a
    group's functions from one source file (purity 0.84-0.90 on 2026-10-22); the command line prints
    the groups and the scores."""
    pytest.importorskip("elftools")
    target = copy_of(tmp_path, variant)
    program = Program(target)
    found = groups.find(target, target.image(), program)
    assert sorted(n for g in found for n in g) == sorted(fn.name for fn in program.functions)
    firsts = [program.by_name[g[0]].entry for g in found]
    assert firsts == sorted(firsts) and all([program.by_name[n].entry for n in g] == sorted(
        program.by_name[n].entry for n in g) for g in found)
    assert groups.find(target, target.image(), program) == found
    member = groups.membership(found)
    assert len({member[n] for n in UART}) == 1
    totals, per = groups.score_groups(program, found, elf_of(target))
    assert totals["purity"] >= 0.8 and totals["functions"] >= 40, totals
    assert per[member["uart_putc"]][0] == ("hw.c", per[member["uart_putc"]][0][1])
    assert groups.main(["--target", target.root, "--score", elf_of(target)]) == 0
    out = capsys.readouterr().out
    assert "group 0: " in out and "files: " in out and "adjusted Rand index" in out


@pytest.mark.slow
def test_the_groups_use_no_names(tmp_path):
    """The same image with every function named FUN_ (dread import-elf --unnamed) falls into the same
    groups, function by function (their entries)."""
    named = copy_of(tmp_path / "named", "m3")
    stripped = copy_of(tmp_path / "stripped", "m3")
    shutil.rmtree(os.path.join(stripped.root, "build"), ignore_errors=True)     # (regflow made anew)
    assert elfimport.main([elf_of(stripped), "--target", stripped.root, "--unnamed"]) == 0
    stripped = Target.load(stripped.root)
    by_entry = []
    for target in (named, stripped):
        program = Program(target)
        assert (target is named) != all(fn.name.startswith("FUN_") for fn in program.functions)
        by_entry.append([[program.by_name[n].entry for n in g] for g in groups.find(target, target.image(), program)])
    assert by_entry[0] == by_entry[1]
