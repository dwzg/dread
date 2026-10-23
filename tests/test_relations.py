"""dread relations on copies of gizmo: a function's neighbourhood (callers up, callees down, to a
depth), the memory its readable C reads and writes, the texts and tables its constants point to, the
structures of the readable_types plan, how sure each name is; the numbers that are not addresses; the
facts kept per function; what the listing alone reads (flow_references); the command line."""
import json
import os
import shutil

import pytest

from dread import cparse, relations
from dread.program import Program
from dread.target import Target
from conftest import GIZMO, gizmo_dir

NEAR = {"print_status", "handle_key", "main", "mode_name", "fmt_dec", "uart_puts"}


def copy_of(tmp, variant):
    copy = os.path.join(str(tmp), "gizmo")
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    here = os.path.join(gizmo_dir(variant), "build", "regflow")
    if os.path.isdir(here):
        shutil.copytree(here, os.path.join(copy, variant, "build", "regflow"))
    return Target.load(os.path.join(copy, variant))


@pytest.fixture(scope="module")
def m3(tmp_path_factory):
    """gizmo m3 copied once, with its program, call maps and facts shared by the tests."""
    target = copy_of(tmp_path_factory.mktemp("relations"), "m3")
    program = Program(target)
    return target, program, relations.call_maps(program), relations.Facts(target, target.image(), program)


def graph_of(m3, name, depth=1, names=None):
    target, program, calls, made = m3
    found, _ = relations.neighbourhood(program, name, depth, *calls)
    return relations.graph(program, name, depth, calls, {n: made.get(n) for n in found}, names)


def by_name(g):
    return {n["name"]: n for n in g["nodes"]}


def edges(g, kind=None):
    names = {n["id"]: n["name"] for n in g["nodes"]}
    return {(names[e["from"]], e["kind"], names[e["to"]]) for e in g["edges"] if kind is None or e["kind"] == kind}


@pytest.mark.slow
def test_the_neighbourhood_its_memory_and_texts(m3):
    """print_status: its callers above, its callees below; what its readable C reads (two fields of
    g_config, at their own addresses), the texts it prints."""
    g = graph_of(m3, "print_status")
    nodes = by_name(g)
    functions = {n for n, d in nodes.items() if d["kind"] == "function"}
    assert functions == NEAR and not g["missing"]
    assert {(n, nodes[n]["side"]) for n in ("handle_key", "main")} == {("handle_key", "caller"), ("main", "caller")}
    assert {nodes[n]["side"] for n in ("mode_name", "fmt_dec", "uart_puts")} == {"callee"}
    assert {("main", "calls", "print_status"), ("print_status", "calls", "mode_name"),
            ("main", "calls", "uart_puts")} <= edges(g, "calls")
    program = m3[1]
    config = [a for k, a, n in program.symbols() if n == "g_config"][0]
    read = {e for e in edges(g, "reads") if e[0] == "print_status"}
    assert read == {("print_status", "reads", "g_%x" % (config + 4)), ("print_status", "reads", "g_%x" % (config + 8))}
    assert {nodes["g_%x" % (config + 4)][k] for k in ("kind", "naming")} == {"global", "unnamed"}
    texts = {to for src, kind, to in edges(g, "refers-to-text") if src == "print_status"}
    assert texts == {'"mode "', '" volume "', '"\\r\\n"'}
    assert nodes["print_status"]["facts"] == "readable C" and nodes["main"]["naming"] == "named"
    assert nodes["main"]["more"]["callers"] == 1          # Reset_Handler, two calls up
    assert nodes["g_config"]["naming"] == "named"         # (a symbol: main passes its address on)
    assert ("main", "uses", "g_config") in edges(g)
    assert ("main", "uses", "g_rx") in edges(g)           # (g_ticks's address plus 4, folded into one constant)
    assert ("main", "reads", "g_ticks") in edges(g)


@pytest.mark.slow
def test_a_function_without_callees_and_a_depth_of_two(m3):
    """cmd_mode calls nothing and writes the mode; the constant its C keeps in a register nobody reads
    (r3 = g_config) is no relation. At depth 2, the callers' callers and the callees' callees join."""
    g = graph_of(m3, "cmd_mode")
    nodes = by_name(g)
    assert {n for n, d in nodes.items() if d["kind"] == "function"} == {"cmd_mode", "apply_config"}
    assert nodes["cmd_mode"]["more"] == {"callers": 0, "callees": 0}
    mine = {e for e in edges(g) if e[0] == "cmd_mode"}
    assert len(mine) == 1 and next(iter(mine))[1] == "writes"
    g2 = graph_of(m3, "print_status", depth=2)
    nodes = by_name(g2)
    assert {"Reset_Handler", "uart_putc"} <= set(nodes) and NEAR <= set(nodes)
    assert (nodes["Reset_Handler"]["side"], nodes["Reset_Handler"]["distance"]) == ("caller", 2)
    assert (nodes["uart_putc"]["side"], nodes["uart_putc"]["distance"]) == ("callee", 2)
    assert ("uart_putc", "writes", "io_40011000") in edges(g2) and ("uart_putc", "reads", "io_40011004") in edges(g2)
    assert nodes["io_40011000"]["kind"] == "register"


@pytest.mark.slow
def test_a_table_of_functions_through_a_section_anchor(m3):
    """dispatch reads commands[cmd] as anchor + index + 52 (GCC's section anchor is mode_name's
    switch table): the sum of the constants is the table, whose words are the commands' handlers."""
    g = graph_of(m3, "dispatch")
    nodes = by_name(g)
    assert nodes["commands"]["kind"] == "table" and nodes["commands"]["naming"] == "named"
    assert ("dispatch", "reads", "commands") in edges(g) and "CSWTCH_24" not in nodes
    assert {to for src, kind, to in edges(g, "refers-to-function") if src == "commands"} == {
        "cmd_led", "cmd_beep", "cmd_mode", "cmd_volume"}
    assert nodes["cmd_led"]["side"] == "referred" and nodes["cmd_led"]["facts"] is None
    g = graph_of(m3, "mode_name")
    assert {to for src, kind, to in edges(g, "refers-to-text") if src == "CSWTCH_24"} == {
        '"off"', '"idle"', '"run"', '"test"'}


@pytest.mark.slow
def test_names_proposed_and_structures(m3, tmp_path):
    """How sure a name is: a FUN_ function with a proposal in names.tsv, a global the readable_names
    table names, a structure of the plan with a proposed name; a plan entry made for other C is not
    shown."""
    target, program = m3[0], m3[1]
    image = target.image()
    folder = os.path.join(target.build, "pilot", image.name)
    os.makedirs(folder, exist_ok=True)
    entry = program.by_name["ring_get"].entry
    with open(os.path.join(folder, "names.tsv"), "w") as f:
        f.write("address\tnow\tproposed\twhat\tprovider\tshown\n0x%x\tring_get\tring_take\ttakes a byte\tplayed\t"
                "readable\n" % entry)
    with open(image.table("readable_names"), "w") as f:
        json.dump({"version": 1, "functions": {},
                   "globals": {"0x2000004c u32": {"name": "tick_count", "by": "played"}},
                   "structs": {"ring_get:r0": {"name": "ring", "fields": {"32": "head", "36": "tail"},
                                               "anchors": ["ring_get:r0", "ring_put:r0"], "by": "played"}}}, f)
    digest = m3[3].get("ring_get")["digest"]
    with open(image.table("readable_types"), "w") as f:
        json.dump({"version": 1, "structs": {"s_1": [[32, "u32"], [36, "u32"]], "s_2": [[0, "u32"], [4, "u16"]]},
                   "embeds": {"s_1": {"40": "s_2"}},
                   "functions": {"ring_get": {"digest": digest, "vars": {"r0": "s_1", "r2": "s_1"}},
                                 "ring_put": {"digest": "made for other C", "vars": {"r0": "s_1"}}}}, f)
    try:
        names = relations.Names(target, image, program)
        assert names.function(program.by_name["ring_get"]) == ("named", ("ring_take", "takes a byte"))
        g = graph_of(m3, "ring_get", names=names)
        nodes = by_name(g)
        assert nodes["ring_get"]["proposed"] == {"name": "ring_take", "what": "takes a byte"}
        assert nodes["ring"]["kind"] == "structure" and nodes["ring"]["naming"] == "proposed"
        assert [f[2] for f in nodes["ring"]["fields"]] == ["head", "tail", "f_28"]
        assert nodes["ring"]["fields"][2][1] == "struct s_2" and nodes["ring"]["size"] == 48     # (s_2 held at 40)
        e = [e for e in g["edges"] if e["kind"] == "holds"]
        assert len(e) == 1 and e[0]["offsets"] == [40] and e[0]["to"] == "s:s_2", e
        e = [e for e in g["edges"] if e["kind"] == "uses-structure"]
        assert len(e) == 1 and e[0]["variables"] == ["r0", "r2"]
        assert nodes["g_ticks"]["naming"] == "named" and nodes["g_ticks"]["proposed"] == {"name": "tick_count"}
        g = graph_of(m3, "ring_put", names=names)
        assert not [e for e in g["edges"] if e["kind"] == "uses-structure"]     # (its C is not the plan's)
        fn = type("F", (), {"name": "FUN_08000362", "entry": entry})()
        assert names.function(fn) == ("proposed", ("ring_take", "takes a byte"))
        assert names.function(type("F", (), {"name": "FUN_1", "entry": 1})())[0] == "unnamed"
    finally:
        for path in (image.table("readable_names"), image.table("readable_types"), os.path.join(folder, "names.tsv")):
            os.remove(path)


def test_how_sure_a_name_is():
    """certainty(): a name in the tables, none, and a proposal by its vote's agreement: every answer
    agreed (sure), at most where dread.namereview's vote trigger alone challenges it (unsure: 0.5 by its
    weights), between (likely), no vote taken (unvoted)."""
    assert relations.unsure_at() == pytest.approx(0.5)
    assert [relations.certainty("proposed", v) for v in (1.0, 0.8, 0.6, 0.5, 0.4, 0.2, None)] == [
        "sure", "likely", "likely", "unsure", "unsure", "unsure", "unvoted"]
    assert [relations.certainty(n, 0.2) for n in ("named", "unnamed", None)] == ["named", "none", "none"]
    assert relations.CERTAINTY[0] == "none" and relations.CERTAINTY[-1] == "named"


def test_numbers_are_not_addresses():
    """In the C: an access at a constant address always counts; an indexed access and a named constant
    only when the machine code makes their constant as an address (anchored); masks, comparisons,
    case labels and a constant only stored into a variable nobody reads never."""
    text = ("/* f */\n#include \"dread.h\"\nextern u32 g(u32);\nu32 f(u32 r0, u32 r1)\n{\n    u32 r2, r3;\n"
            "    r3 = 0x20000030u;\n"
            "    *(volatile u32 *)0x20000038u = r0;\n"
            "    *(volatile u32 *)0x20000040u += 1;\n"
            "    r2 = *(volatile u32 *)(0x8000820u + (r0 << 2) + 52);\n"
            "    r2 = r2 + *(volatile u32 *)(r1 + 0x104u);\n"
            "    g(0x123u);\n    g(0x8000900u);\n    g(r0 & 0x20000000u);\n"
            "    if (r0 == 0x20000100u) return 1;\n"
            "    switch (r0) { case 0x8000900: return 2; }\n"
            "    return r2;\n}\n")
    fn = cparse.parse(text).function("f")
    made = {0x8000820, 0x8000900}                         # (what literal pools hold: no offset 0x104)
    found = relations.c_references(fn, lambda value, terms: any(t in made for t in terms))
    assert found == {(0x20000038, "u32", False): [False, True], (0x20000040, "u32", False): [True, True],
                     (0x8000854, "u32", True): [True, False], (0x8000900, None, False): [False, False]}
    everything = relations.c_references(fn)                 # (without anchors: every constant is named)
    assert (0x123, None, False) in everything and (0x20000030, None, False) not in everything
    assert (0x20000000, None, False) not in everything and (0x20000100, None, False) not in everything


def test_the_listing_alone_reads_what_the_readable_c_reads(variant, tmp_path, monkeypatch):
    """flow_references, from the machine code alone, on each gizmo build: print_status reads the two
    fields of g_config that its readable C reads (test_every_build); uart_putc reads the UART's status
    register and writes its data register, offsets from the device's base; on the ARM builds dispatch
    reads the commands table at GCC's section anchor plus an index plus 52 (indexed, as c_references
    reads it from the C: test_a_table_of_functions_through_a_section_anchor); without a value_step
    (MIPS and MSP430 so far) the addresses the code makes, each only named (listing_references)."""
    target = copy_of(tmp_path, variant)
    program = Program(target)

    def accesses(name):
        found = relations.flow_references(program, program.by_name[name])
        return {(a, i): rw for (a, t, i), rw in found.items() if t is not None}
    config = [a for k, a, n in program.symbols() if n == "g_config"][0]
    assert {a for (a, i), (r, w) in accesses("print_status").items() if r and not w and not i} == {config + 4,
                                                                                                 config + 8}
    base = target.models["uart"]["base"]
    assert accesses("uart_putc") == {(base + 4, False): [True, False], (base, False): [False, True]}
    if program.arch.name == "arm":
        table = [a for k, a, n in program.symbols() if n == "commands"][0]
        assert accesses("dispatch") == {(table, True): [True, False]}
    fn = program.by_name["uart_putc"]
    made = relations.materialized(program, fn)
    monkeypatch.delattr(type(program.arch), "value_step")
    assert relations.flow_references(program, fn) == relations.listing_references(made)


def test_the_neighbourhood_is_bounded():
    """Callers up and callees down, nearest first, by address at one distance; a function both above
    and below is "both"; what does not fit the limit is counted."""
    class P:
        by_name = {n: type("F", (), {"entry": i})() for i, n in enumerate("abcdefgh")}
    up = {"c": {"a": [1], "b": [2]}, "a": {"h": [3]}}
    down = {"a": {"c": [1]}, "b": {"c": [2]}, "c": {"d": [3], "e": [4]}, "d": {"f": [5]}, "h": {"a": [3]}}
    found, left = relations.neighbourhood(P, "c", 1, up, down)
    assert found == {"c": (0, "center"), "a": (1, "caller"), "b": (1, "caller"), "d": (1, "callee"), "e": (1, "callee")}
    found, left = relations.neighbourhood(P, "c", 2, up, down)
    assert found["h"] == (2, "caller") and found["f"] == (2, "callee") and not left
    found, left = relations.neighbourhood(P, "c", 2, up, down, limit=4)
    assert list(found) == ["c", "a", "b", "d"] and left == ["e", "f", "h"]
    up["c"]["d"] = [9]                                      # (d calls c, c calls d)
    found, _ = relations.neighbourhood(P, "c", 1, up, down)
    assert found["d"] == (1, "both")


@pytest.mark.slow
def test_facts_are_kept_and_the_listing_stands_in(m3, monkeypatch):
    """A function's facts are kept under the key of what they are made from: a second Facts finds
    them without making readable C. A function without C is read from its listing: names only."""
    target, program = m3[0], m3[1]
    path = os.path.join(target.build, "relations", "main", "print_status.json")
    m3[3].get("print_status")
    kept = json.load(open(path))
    assert kept["facts"] == m3[3].get("print_status") and kept["key"]

    def nothing(*args, **kw):
        raise AssertionError("made again")
    monkeypatch.setattr(relations, "references", nothing)
    again = relations.Facts(target, target.image(), program)
    assert again.get("print_status") == kept["facts"]
    monkeypatch.undo()
    fn = program.by_name["main"]
    facts = relations.references(program, fn, None, "refused for the test")
    assert facts["from"] == "listing" and facts["why"] == "refused for the test" and facts["refs"]
    assert all(not r and not w and t is None for _, t, _, r, w in facts["refs"])
    ticks = [a for k, a, n in program.symbols() if n == "g_ticks"][0]
    assert ticks in {a for a, *_ in facts["refs"]}


@pytest.mark.slow
def test_the_command_line(m3, capsys):
    target = m3[0]
    assert relations.main(["--target", target.root, "--json", "cmd_mode"]) == 0
    g = json.loads(capsys.readouterr().out)
    assert g["center"] == "cmd_mode" and g["depth"] == 1 and {"f:cmd_mode", "f:apply_config"} <= {n["id"] for n in g["nodes"]}
    assert relations.main(["--target", target.root, "cmd_mode"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("; cmd_mode, callers and callees 1 call away") and "  apply_config calls cmd_mode" in out
    assert relations.main(["--target", target.root, "nosuch"]) == 1


@pytest.mark.slow
def test_every_build(variant, tmp_path):
    """print_status on each gizmo build: the same neighbourhood, and the two fields of g_config it reads."""
    target = copy_of(tmp_path, variant)
    program = Program(target)
    calls = relations.call_maps(program)
    made = relations.Facts(target, target.image(), program)
    found, _ = relations.neighbourhood(program, "print_status", 1, *calls)
    g = relations.graph(program, "print_status", 1, calls, {n: made.get(n) for n in found})
    assert {n["name"] for n in g["nodes"] if n["kind"] == "function"} == NEAR
    config = [a for k, a, n in program.symbols() if n == "g_config"][0]
    assert {n["address"] for n in g["nodes"] if n["kind"] == "global" and ("print_status", "reads", n["name"]) in edges(g)} \
        == {config + 4, config + 8}
