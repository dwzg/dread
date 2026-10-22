"""dread groups: Louvain's communities on graphs made by hand, the scores against a true partition,
and the groups of gizmo's builds from both sources of evidence (the listing, the default, and the
readable C): the same every time, every function in one, mostly one source file a group (DWARF), the
same with every name taken away, and the command line; the UART's device registers found from the
listing; the record kept in BUILD/groups and its key; the labels; the whole program as a graph of
its groups (--json)."""
import json
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
    """Each gizmo build, with the evidence from the listing (the default) and from the readable C: every
    function in one group, the groups in address order, the same groups a second time, the UART
    driver's four functions in one group, and most of a group's functions from one source file (purity
    0.84-0.90 from the readable C, 0.86-0.96 from the listing on 2026-10-22); the command line prints
    the groups and the scores."""
    pytest.importorskip("elftools")
    target = copy_of(tmp_path, variant)
    program = Program(target)
    for source in groups.SOURCES:
        found = groups.find(target, target.image(), program, source=source)
        assert sorted(n for g in found for n in g) == sorted(fn.name for fn in program.functions)
        firsts = [program.by_name[g[0]].entry for g in found]
        assert firsts == sorted(firsts) and all([program.by_name[n].entry for n in g] == sorted(
            program.by_name[n].entry for n in g) for g in found)
        assert groups.find(target, target.image(), program, source=source) == found
        member = groups.membership(found)
        assert len({member[n] for n in UART}) == 1, source
        totals, per = groups.score_groups(program, found, elf_of(target))
        assert totals["purity"] >= 0.8 and totals["functions"] >= 40, (source, totals)
        assert per[member["uart_putc"]][0] == ("hw.c", per[member["uart_putc"]][0][1])
    for source in groups.SOURCES:
        assert groups.main(["--target", target.root, "--score", elf_of(target), "--evidence", source]) == 0
        out = capsys.readouterr().out
        assert "group 0: " in out and "files: " in out and "adjusted Rand index" in out


def test_the_listing_finds_the_uart_device(m3_copy):
    """evidence() from the listing (flow_references): the UART driver's functions touch the UART's
    block of device registers, and nothing else does; a text main prints is one of main's things."""
    target, program = m3_copy
    touched = groups.evidence(target, target.image(), program, source="listing")
    block = ("device", target.models["uart"]["base"] // groups.DEVICE_BLOCK)
    assert {n for n, things in touched.items() if block in things} == {"uart_init", "uart_putc", "uart_getc"}
    assert any(kind == "text" for kind, _ in touched["main"])
    with pytest.raises(ValueError, match="evidence from"):
        groups.evidence(target, target.image(), program, source="tea leaves")


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


def test_a_name_prefix_and_the_one_a_group_shares():
    """prefix_of: up to the first underscore, else the first word after a Hungarian mark of one or two
    letters; nothing for a tool's name or one word. common_prefix: the prefix most names have, when two
    and a third of them have it."""
    cases = {"uart_putc": "uart_", "SysTick_Handler": "SysTick_", "__sfp_lock_acquire": "sfp_", "xQueueSend": "Queue",
             "vTaskDelay": "Task", "uxTaskGetNumberOfTasks": "Task", "prvIdleTask": "prv", "getChar": "get",
             "main": None, "memcpy": None, "f0": None, "FUN_08000100": None, "sub_1234": None}
    assert {n: groups.prefix_of(n) for n in cases} == cases
    assert groups.common_prefix(["uart_init", "uart_putc", "uart_puts", "memcpy"]) == ("uart_", 3)
    assert groups.common_prefix(["a_x", "b_y", "c_z"]) is None
    assert groups.common_prefix(["io_a", "io_b"] + ["fn%d" % i for i in range(5)]) is None      # (2 of 7)
    assert groups.common_prefix(["xQueueSend", "vQueueDelete", "prvUnlockQueue"]) == ("Queue", 2)


@pytest.mark.slow
def test_the_groups_are_kept_under_a_key(m3_copy, monkeypatch):
    """load() finds the groups once and keeps them, with where their evidence was read from; a second
    load() reads them without reading a function; another resolution, the other source of evidence, a
    changed table or --fresh finds them anew."""
    target, program = m3_copy
    image = target.image()
    first = groups.load(target, image, program)
    assert os.path.exists(groups.kept_path(target, image)) and first["evidence"] == groups.EVIDENCE == "listing"
    assert sorted(n for g in first["groups"] for n in g) == sorted(fn.name for fn in program.functions)
    assert groups.cache_key(target, image, source="readable") != first["key"]

    def no_facts(*args, **kw):
        raise AssertionError("the facts were read again")
    with monkeypatch.context() as m:
        m.setattr(groups, "evidence", no_facts)
        again = groups.load(target, image, program)
        assert again["groups"] == first["groups"] and again["touched"] == first["touched"]
        assert again["evidence"] == "listing"
        assert groups.kept(target, image, resolution=1.0) is None
        assert groups.kept(target, image, source="readable") is None
        with pytest.raises(AssertionError, match="read again"):
            groups.load(target, image, program, fresh=True)
        with pytest.raises(AssertionError, match="read again"):
            groups.load(target, image, program, source="readable")
    readable = groups.load(target, image, program, source="readable")
    assert readable["evidence"] == "readable" and groups.kept(target, image, source="readable")["key"] == readable["key"]
    assert groups.kept(target, image) is None               # (one record an image: the last one made)
    with open(image.table("annotations"), "a") as f:
        f.write("0x%x | a note changes a table\n" % program.functions[0].entry)
    assert groups.kept(target, image, source="readable") is None


@pytest.mark.slow
def test_the_whole_program_as_a_graph_of_its_groups(m3_copy, capsys):
    """program_graph(): every function once with its group, each group's functions in address order;
    the UART driver's group labelled by its prefix, and by the pilot's name once subsystems.tsv names
    exactly its functions; the calls between groups add up to the calls between their functions; and
    the command line prints the same graph (--json)."""
    from dread import pilot
    target, program = m3_copy
    image = target.image()
    record = groups.load(target, image, program)
    found = record["groups"]
    g = groups.program_graph(program, found, record["touched"])
    names = [f["name"] for f in g["functions"]]
    assert names == [fn.name for fn in sorted(program.functions, key=lambda f: f.entry)]
    for k, group in enumerate(found):
        assert [f["name"] for f in g["functions"] if f["group"] == k] == group
        assert g["groups"][k]["functions"] == len(group) and g["groups"][k]["group"] == k
    uart = groups.membership(found)["uart_putc"]
    assert g["groups"][uart]["label"] == "uart_" and g["groups"][uart]["from"] == "prefix"
    crossing = {}
    for i, j, sites in g["calls"]:
        a, b = g["functions"][i]["group"], g["functions"][j]["group"]
        assert sites >= 1
        if a != b:
            key = (min(a, b), max(a, b))
            crossing.setdefault(key, [0, 0])[0 if a < b else 1] += 1
    assert {(e["a"], e["b"]): e["calls"] for e in g["between"] if sum(e["calls"])} == crossing
    assert all(e["a"] < e["b"] for e in g["between"]) and crossing
    assert g["counts"]["functions"] == len(names) and g["counts"]["groups"] == len(found)
    pilot.keep_subsystem(target, image, {"group": uart, "members": pilot.members_key(program, found[uart]),
                                         "name": "Serial port", "prefix": "uart_", "what": "the UART's driver"})
    named = groups.program_graph(program, found, record["touched"],
                                 subsystems=pilot.read_subsystems(pilot.subsystems_path(target, image)))
    assert named["groups"][uart]["label"] == "Serial port" and named["groups"][uart]["from"] == "pilot"
    assert named["groups"][uart]["what"] == "the UART's driver"
    capsys.readouterr()
    assert groups.main(["--target", target.root, "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["groups"][uart]["label"] == "Serial port" and printed["counts"] == named["counts"]
