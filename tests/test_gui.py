"""dread gui on a copy of gizmo: the page and its files, the answers of the API (the whole program by
groups among them, found by dread groups in a process of its own; a function's readable C in the
reading form), how sure each name is and what a review said of it (names.tsv and review.tsv written
here), the search that lights the whole program, the two kinds of change and the refusals (another
host name, a change without the page's token); and the C reader that finds the definitions."""
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request

import pytest

from dread import gui
from dread.target import Target
from conftest import GIZMO, gizmo_dir, have

needs_gcc = pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")


def gizmo_copy(tmp_path):
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    for cache in ("autocases", "regflow"):
        here = os.path.join(gizmo_dir("m3"), "build", cache)
        if os.path.isdir(here):
            shutil.copytree(here, copy / "m3" / "build" / cache)
    return Target.load(str(copy / "m3"))


def serving(target):
    """The page's server for a target, serving in a thread, once the analyses are done."""
    srv = gui.serve(target, target.image(), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    deadline = time.time() + 120
    while srv.state.pending() and time.time() < deadline:
        time.sleep(0.05)
    return srv


@pytest.fixture
def server(tmp_path):
    target = gizmo_copy(tmp_path)
    srv = serving(target)
    yield srv, target
    srv.shutdown()
    srv.server_close()


# A naming run and a review on gizmo-m3 (written here, as dread pilot writes them): these functions made
# FUN_address in the tables, each proposed for (names.tsv) with a vote's agreement ("": one question)
NAMELESS = {"fmt_dec": ("format_decimal", "1.00"), "fmt_hex": ("print_hex", "0.40"), "ring_put": ("ring_push", "0.60"),
            "ring_get": ("ring_pop", "0.40"), "checksum": ("sum_bytes", ""), "mode_name": (None, None)}


def plant_names(target):
    """gizmo-m3 as a naming run and a review leave it: NAMELESS made FUN_address in its tables and
    proposed for; the review changed fmt_hex's proposal (a new names.tsv row marked , review), kept
    ring_put's against a candidate (a suspect; its "same" reason's sentence holds "; "), confirmed a name
    ring_get no longer goes by (stale), flagged print_status (a person's name) and confirmed uart_putc;
    the UART's group named by the pilot and its name kept by the review. Returns {old name: name now}."""
    from dread import llm, namereview, pilot
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    now = {n: "FUN_%08x" % program.by_name[n].entry for n in NAMELESS}
    by_address = {"0x%x" % program.by_name[n].entry: new for n, new in now.items()}
    path = image.table("functions")
    rows = [r.split("\t") for r in open(path).read().splitlines()]
    with open(path, "w") as f:
        f.write("".join("\t".join([r[0], by_address.get(r[0], r[1])] + r[2:]) + "\n" for r in rows))
    path = image.table("symbols")
    lines = open(path).read().splitlines()
    with open(path, "w") as f:
        f.write("".join("f %s %s\n" % (l.split()[1], by_address[l.split()[1]]) if l.startswith("f ") and
                        l.split()[1] in by_address else l + "\n" for l in lines))
    target = Target.load(target.root)
    image = target.image()
    program = Program(target, image)
    played = llm.Scripted([])
    for old, (proposed, sure) in NAMELESS.items():
        if proposed:
            pilot.keep_proposal(target, image, program.by_name[now[old]], proposed, "does %s" % proposed, played,
                                "readable", {"confidence": sure, "samples": " ".join([proposed] * 5) if sure else "",
                                             "pass": "1"})
    pilot.keep_proposal(target, image, program.by_name[now["fmt_hex"]], "format_hex", "writes a number in hex", played,
                        "readable" + namereview.REVIEWED, {"confidence": "0.80", "samples": "format_hex " * 4 + "hex_out",
                                                           "pass": "2"})
    uart = ["uart_init", "uart_putc", "uart_puts", "uart_getc"]
    key = pilot.members_key(program, uart)
    pilot.keep_subsystem(target, image, {"group": "0", "first": "0x%x" % program.by_name["uart_init"].entry,
                                         "functions": 4, "members": key, "name": "UART driver", "prefix": "uart_",
                                         "what": "sends and receives bytes", "confidence": "0.40",
                                         "samples": "uart_ serial_ uart_ tty_ io_"})

    def challenge(function, **values):
        fn = program.by_name[function]
        namereview.keep_review(target, image, dict({"review": 1, "round": 1, "kind": "function", "address": "0x%x" % fn.entry,
                                                    "now": function, "source": "proposal", "score": "1.20"}, **values))
    namereview.keep_review(target, image, {
        "review": 1, "round": 1, "kind": "group", "address": "0x%x" % program.by_name["uart_init"].entry,
        "now": "group 0", "group": key, "source": "proposal", "score": "1.20",
        "reasons": "vote 1.20: the vote over its prefix agreed 40 % (its answers: uart_ serial_ uart_ tty_ io_)",
        "old": "UART driver (uart_)", "candidate": "serial port (serial_)", "agreement": "3 of 5", "votes": "new 1, old 1",
        "outcome": "kept", "name": "UART driver (uart_)", "what": "sends and receives bytes"})
    challenge(now["fmt_hex"], reasons="vote 1.20: the vote that chose it agreed 40 % (its answers: print_hex)",
              old="print_hex", candidate="format_hex", agreement="4 of 5", votes="new 2", outcome="changed",
              name="format_hex", what="writes a number in hex")
    challenge(now["ring_put"], score="1.80", reasons="vote 0.80: the vote that chose it agreed 60 % (its answers: x); "
              "same 1.00: another function is named the same: FUN_1 (puts; takes)", old="ring_push",
              candidate="ring_append", agreement="3 of 5", votes="new 1, old 1", outcome="kept", name="ring_push")
    challenge(now["ring_get"], reasons="vote 1.20: the vote that chose it agreed 40 % (its answers: x)", old="ring_take",
              candidate="ring_take", agreement="5 of 5", outcome="confirmed", name="ring_take")
    challenge("print_status", source="person", score="1.00", reasons="vague 1.00: its name says little",
              old="print_status", candidate="show_mode_and_volume", agreement="4 of 5", votes="new 2", outcome="flagged",
              name="print_status")
    challenge("uart_putc", source="person", round=2, score="1.00", reasons="renamed 1.00: its group was renamed",
              old="uart_putc", candidate="uart_putc", agreement="5 of 5", outcome="confirmed", name="uart_putc")
    return now


@pytest.fixture
def named(tmp_path):
    """The page's server on a gizmo copy with a naming run and a review planted (plant_names)."""
    target = gizmo_copy(tmp_path)
    now = plant_names(target)
    target = Target.load(target.root)
    srv = serving(target)
    yield srv, target, now
    srv.shutdown()
    srv.server_close()


def request(srv, path, body=None, token=True, host=None, kind="application/json"):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (srv.server_address[1], path),
                                 data=None if body is None else json.dumps(body).encode())
    if body is not None:
        req.add_header("Content-Type", kind)
        if token:
            req.add_header("X-Dread-Token", srv.token)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def answer(srv, path, body=None, **kw):
    status, _, data = request(srv, path, body, **kw)
    return status, json.loads(data)


def test_the_page_its_files_and_nothing_else(server):
    srv, _ = server
    status, headers, body = request(srv, "/")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert ('content="%s"' % srv.token).encode() in body and b"{{TOKEN}}" not in body
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    for path, kind in (("/app.js", "text/javascript"), ("/sim.js", "text/javascript"), ("/app.css", "text/css")):
        status, headers, _ = request(srv, path)
        assert status == 200 and headers["Content-Type"].startswith(kind)
    assert request(srv, "/../dread.toml")[0] == 404
    assert request(srv, "/api/nothing")[0] == 404
    assert request(srv, "/web/index.html")[0] == 404


def test_overview_and_function_list(server):
    srv, target = server
    status, o = answer(srv, "/api/overview")
    assert status == 200 and o["target"]["name"] == "gizmo-m3" and o["image"]["name"] == "main"
    status, functions = answer(srv, "/api/functions")
    by = {f["name"]: f for f in functions}
    assert sum(c["functions"] for c in o["counts"].values()) == len(functions) == o["image"]["functions"]
    assert by["clamp"]["origin"] == "hand"              # defined in the C that src includes
    assert by["print_status"]["origin"] == "lifted"     # in the lifted table
    assert o["status"]["pending"] == []


def test_one_function(server):
    srv, _ = server
    status, d = answer(srv, "/api/function?name=print_status")
    assert status == 200 and d["origin"] == "lifted"
    callers = {c["name"] for c in d["header"]["callers"]}
    assert {"handle_key", "main"} <= callers
    linked = {c.get("name") for r in d["rows"] if r["kind"] == "insn" for c in r["comment"]}
    assert {"uart_puts", "mode_name", "fmt_dec"} <= linked
    lifted = [s for s in d["sources"] if s["kind"] == "lifted"][0]
    assert "print_status" in lifted["text"] and lifted["how"] == "lifted now, not built or tested"
    status, d = answer(srv, "/api/function?name=clamp")
    source = [s for s in d["sources"] if s["kind"] == "source"][0]
    assert source["text"].lstrip().startswith(("int", "u32", "s32", "unsigned")) and "clamp" in source["text"]
    assert answer(srv, "/api/function?name=nosuch")[0] == 404


def test_the_reading_view_of_a_function(server):
    """A function without source of its own offers its readable C in the reading form (/api/reading,
    asked for when its tab is shown): the lines and margins dread readable --plain makes (dread.readable
    reading), without the instructions' comments, each statement's first instruction an address of the
    function; "pending" while the register analysis runs, "accepted" once the readable table has it (the
    state loads anew with the table). A function written by hand has none; an unknown name is a 404.
    The page script asks for it and links the margin to the listing."""
    from dread import lift, readable, regflow
    from dread.program import Program
    srv, target = server
    status, d = answer(srv, "/api/function?name=print_status")
    kinds = [s["kind"] for s in d["sources"]]
    assert kinds == ["readable", "lifted"] and d["sources"][0] == {"kind": "readable", "deferred": True,
                                                                  "accepted": False}, d["sources"]
    status, r = answer(srv, "/api/reading?name=print_status")
    assert status == 200 and r["why"] is None and r["accepted"] is False, r
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    text = lift.make_lifter(program, flow).lift(program.by_name["print_status"]).text
    made = readable.reading("print_status", text, readable.Context(target, image, program, flow))
    assert r["lines"] == [[a, line] for a, line in made], r["lines"]
    assert not any("/* 8" in line for _, line in r["lines"]), r["lines"]       # (no instruction's comment)
    fn = program.by_name["print_status"]
    at = [a for a, _ in r["lines"] if a is not None]
    assert len(at) >= 5 and all(fn.entry <= a < program.end(fn) for a in at), r["lines"]
    with open(image.table("readable"), "w") as f:
        f.write("print_status\n")
    deadline = time.time() + 120
    while True:                             # (the state loads anew: its analyses run again meanwhile)
        status, r = answer(srv, "/api/reading?name=print_status")
        if not r.get("pending") or time.time() > deadline:
            break
        time.sleep(0.1)
    assert status == 200 and r["accepted"] is True and r["lines"] == [[a, line] for a, line in made], r
    status, d = answer(srv, "/api/function?name=clamp")
    assert "readable" not in [s["kind"] for s in d["sources"]], d["sources"]
    assert answer(srv, "/api/reading?name=nosuch")[0] == 404
    script = request(srv, "/app.js")[2].decode()
    assert "/api/reading" in script and "function readingBody" in script and "a.at[data-at]" in script


def test_search_by_name_address_and_text(server):
    srv, target = server
    status, found = answer(srv, "/api/search?q=uart_p")
    assert [f["name"] for f in found][:2] == ["uart_putc", "uart_puts"]
    entry = srv.state.program.by_name["clamp"].entry
    status, found = answer(srv, "/api/search?q=0x%x" % (entry + 2))
    assert found[0]["kind"] == "address" and found[0]["name"] == "clamp"
    status, found = answer(srv, "/api/search?q=mode")
    assert any(f["kind"] == "text" and f["name"] == "print_status" for f in found)


def graph(srv, name, depth=1, timeout=120):
    """The relations of a function once the facts worker has read every function of it."""
    deadline = time.time() + timeout
    while True:
        status, g = answer(srv, "/api/graph?name=%s&depth=%d" % (name, depth))
        assert status == 200, g
        if not g.get("waiting") and not g["pending"]:
            return g
        assert time.time() < deadline, g["pending"]
        time.sleep(0.1)


@pytest.mark.slow
def test_the_relations_of_a_function(server):
    """/api/graph: the functions at once, the memory and texts as the worker reads each function's C
    (the function first); calls and globals right; a function without callees; a depth of 2."""
    srv, _ = server
    status, first = answer(srv, "/api/graph?name=print_status")
    assert status == 200 and first["pending"][0] == "print_status"
    assert {n["name"] for n in first["nodes"]} == {"print_status", "handle_key", "main", "mode_name", "fmt_dec",
                                                   "uart_puts"}
    g = graph(srv, "print_status")
    nodes = {n["name"]: n for n in g["nodes"]}
    names = {n["id"]: n["name"] for n in g["nodes"]}
    found = {(names[e["from"]], e["kind"], names[e["to"]]) for e in g["edges"]}
    assert {("main", "calls", "print_status"), ("handle_key", "calls", "print_status"),
            ("print_status", "calls", "mode_name"), ("print_status", "calls", "fmt_dec"),
            ("print_status", "calls", "uart_puts"), ("print_status", "reads", "g_20000034"),
            ("print_status", "reads", "g_20000038"), ("print_status", "refers-to-text", '"mode "'),
            ("main", "reads", "g_ticks")} <= found
    assert nodes["print_status"]["origin"] == "lifted" and nodes["print_status"]["side"] == "center"
    assert nodes["g_20000034"]["kind"] == "global" and nodes["g_20000034"]["naming"] == "unnamed"
    assert nodes["g_ticks"]["naming"] == "named"
    g = graph(srv, "cmd_mode")
    assert {n["name"] for n in g["nodes"] if n["kind"] == "function"} == {"cmd_mode", "apply_config"}
    assert not [e for e in g["edges"] if e["kind"] == "calls" and e["from"] == "f:cmd_mode"]
    assert [e["kind"] for e in g["edges"] if e["from"] == "f:cmd_mode"] == ["writes"]
    g = graph(srv, "print_status", depth=2)
    nodes = {n["name"]: n for n in g["nodes"]}
    assert g["depth"] == 2 and nodes["Reset_Handler"]["distance"] == 2 and nodes["uart_putc"]["side"] == "callee"
    assert answer(srv, "/api/graph?name=nosuch")[0] == 404


def test_the_page_draws_the_relations(server):
    srv, _ = server
    script = request(srv, "/app.js")[2].decode()
    assert "/api/graph" in script and "function relationsHTML" in script and "function layoutGraph" in script
    assert "#/r/" in script                                 # (a function's relations have an address of their own)
    assert 'import { Sim } from "./sim.js"' in script and "requestAnimationFrame" in script
    assert "prefers-reduced-motion" in script               # (without motion: the layout at rest at once)
    assert "/api/program" in script and "function programHTML" in script and "#/p" in script
    assert "/api/program-search" in script and "function pie(" in script     # (the search, how sure names are)
    assert "/api/timeline" in script and "function showTimeline" in script and "#/s" in script   # (the system run)
    assert "calls-pointer" in script and "d.ran" in script                  # (its transfers in both graphs)
    sim = request(srv, "/sim.js")[2].decode()
    assert "export class Sim" in sim and "class Quadtree" in sim and "document" not in sim
    page = request(srv, "/")[2].decode()
    assert "<kbd>r</kbd>" in page and "relations" in page and "<kbd>p</kbd>" in page and "<kbd>s</kbd>" in page


def program(srv, timeout=300):
    """/api/program once dread groups has grouped the functions (in a process of its own)."""
    deadline = time.time() + timeout
    while True:
        status, g = answer(srv, "/api/program")
        assert status == 200 and "error" not in g, g
        if "waiting" not in g:
            return g
        assert g["waiting"] in ("callers", "groups") and time.time() < deadline, g
        time.sleep(0.2)


@pytest.mark.slow
def test_the_whole_program(server):
    """/api/program: waits for the groups, which dread groups finds and keeps in BUILD/groups; then
    every function once in its group, the groups labelled (the UART driver's by its prefix), the calls
    between groups, a group's functions, the transfers of the system run's records; a second state of
    the same tables reads the kept groups."""
    srv, target = server
    plant_runs(target)
    status, first = answer(srv, "/api/program")
    assert status == 200 and first.get("waiting") == "groups", first
    g = program(srv)
    functions = {f["name"]: f for f in g["functions"]}
    assert set(functions) == {fn.name for fn in srv.state.program.functions}
    assert all(f["origin"] in gui.ORIGINS for f in g["functions"]) and functions["clamp"]["origin"] == "hand"
    uart = functions["uart_putc"]["group"]
    assert {n for n, f in functions.items() if f["group"] == uart} == {"uart_init", "uart_putc", "uart_puts", "uart_getc"}
    assert g["groups"][uart]["label"] == "uart_" and g["groups"][uart]["functions"] == 4
    assert sum(gr["functions"] for gr in g["groups"]) == len(functions) == g["counts"]["functions"]
    assert g["between"] and all(e["a"] < e["b"] and (sum(e["calls"]) or sum(e["refers"]) or sum(e["ran"]) or e["shared"])
                                for e in g["between"])
    index = {f["name"]: i for i, f in enumerate(g["functions"])}
    assert [index["print_status"], index["uart_puts"], 2] in g["calls"] or \
        any(c[:2] == [index["print_status"], index["uart_puts"]] for c in g["calls"])
    assert [index["dispatch"], index["cmd_volume"], "pointer-jump", 1] in g["ran"]      # (the system run's)
    from dread import groups
    assert g["groups_from"] == "dread groups" and os.path.exists(groups.kept_path(target, target.image()))
    state = gui.State(target, target.image())                       # (the next look reads them: no process)
    try:
        deadline = time.time() + 120
        while True:
            with state.lock:
                again = state.program_graph()
            if "waiting" not in again:
                break
            assert time.time() < deadline, again
            time.sleep(0.1)
        assert again["groups_from"] == "kept" and again["groups"] == g["groups"]
    finally:
        assert state.close()


def test_closing_ends_the_grouping(server):
    """A server closed while dread groups runs for it ends that process and its thread."""
    srv, _ = server
    status, first = answer(srv, "/api/program")
    assert status == 200 and first.get("waiting") in ("callers", "groups")
    deadline = time.time() + 30
    while srv.state.grouping is None and srv.state.grouper is not None and time.time() < deadline:
        time.sleep(0.05)
    process = srv.state.grouping
    started = time.time()
    assert srv.state.close(timeout=60)
    assert time.time() - started < 30
    if process is not None:
        assert process.poll() is not None


def test_a_note_goes_into_the_annotations(server):
    srv, target = server
    fn = srv.state.program.by_name["print_status"]
    second = sorted(fn.insns)[1]
    status, reply = answer(srv, "/api/annotate", {"address": second, "text": "the configuration\nin RAM"})
    assert status == 200, reply
    text = open(target.image().table("annotations")).read()
    assert "0x%x | the configuration\n0x%x | in RAM\n" % (second, second) in text
    status, d = answer(srv, "/api/function?name=print_status")
    assert [r["text"] for r in d["rows"] if r["kind"] == "note"] == ["the configuration", "in RAM"]
    answer(srv, "/api/annotate", {"address": second, "text": ""})
    assert "the configuration" not in open(target.image().table("annotations")).read()
    assert answer(srv, "/api/annotate", {"address": 0x10, "text": "x"})[0] == 400


@needs_gcc
def test_rename_through_the_page(server):
    srv, target = server
    status, reply = answer(srv, "/api/rename", {"old": "beep_on", "new": "buzzer_on"})
    assert status == 200 and any(p.startswith("config/functions.tsv") for p in reply["changed"]), reply
    names = {f["name"] for f in answer(srv, "/api/functions")[1]}
    assert "buzzer_on" in names and "beep_on" not in names
    status, reply = answer(srv, "/api/rename", {"old": "clamp", "new": "clamp_range"})
    assert status == 400 and "outside the target's directory" in reply["error"]
    assert "../shared_cases.py" in reply["error"]
    status, reply = answer(srv, "/api/rename", {"old": "buzzer_on", "new": "beep_off"})
    assert status == 400 and "already the name" in reply["error"]


def test_refusals(server):
    srv, _ = server
    assert request(srv, "/api/overview", host="evil.example:%d" % srv.server_address[1])[0] == 403
    assert request(srv, "/api/overview", host="localhost:9999")[0] == 200       # a tunnel's own port
    assert request(srv, "/api/rename", {"old": "beep_on", "new": "x"}, token=False)[0] == 403
    assert request(srv, "/api/rename", {"old": "beep_on", "new": "x"}, kind="text/plain")[0] == 415
    assert "beep_on" in srv.state.program.by_name


def test_how_sure_names_are_in_the_list_and_the_overview(named):
    """/api/functions: each function's certainty (dread.relations certainty: the vote's agreement of
    the proposal it goes by), the proposal and the review's mark; a challenge of a name the function no
    longer goes by marks nothing. /api/overview: the functions of each level, and what the review left
    (flagged, suspects, changed with the old name, confirmed, the group's name, rows no longer about a
    name in use)."""
    srv, target, now = named
    status, functions = answer(srv, "/api/functions")
    by = {f["name"]: f for f in functions}
    got = {old: (by[n]["certainty"], by[n].get("confidence"), by[n].get("proposed"), (by[n].get("review") or {}).get("mark"))
           for old, n in now.items()}
    assert got == {"fmt_dec": ("sure", 1.0, "format_decimal", None), "fmt_hex": ("likely", 0.8, "format_hex", "changed"),
                   "ring_put": ("likely", 0.6, "ring_push", "suspect"), "ring_get": ("unsure", 0.4, "ring_pop", None),
                   "checksum": ("unvoted", None, "sum_bytes", None), "mode_name": ("none", None, None, None)}, got
    assert by[now["fmt_hex"]]["review"] == {"mark": "changed", "outcome": "changed", "old": "print_hex"}
    assert by["print_status"]["certainty"] == "named" and by["print_status"]["review"]["mark"] == "flagged"
    assert by["uart_putc"]["review"]["mark"] == "confirmed" and "review" not in by["uart_puts"]
    status, o = answer(srv, "/api/overview")
    counts = {k: v["functions"] for k, v in o["names"]["counts"].items()}
    assert counts == {"named": len(functions) - 6, "sure": 1, "likely": 2, "unsure": 1, "unvoted": 1, "none": 1}
    review = o["names"]["review"]
    assert [i["name"] for i in review["flagged"]] == ["print_status"]
    assert review["flagged"][0]["last"]["candidate"] == "show_mode_and_volume"
    assert [i["name"] for i in review["suspect"]] == [now["ring_put"]]
    assert [(i["name"], i["review"]["old"]) for i in review["changed"]] == [(now["fmt_hex"], "print_hex")]
    assert [i["name"] for i in review["confirmed"]] == ["uart_putc"]
    assert [(g["label"], g["mark"]) for g in review["groups"]] == [("UART driver", "suspect")]
    assert (review["challenges"], review["reviews"], review["stale"]) == (6, 1, 1)


def test_a_functions_name_and_what_the_review_said(named):
    """/api/function's name: the proposal with its vote, and every challenge of the review with its
    reasons split (a reason's sentence may hold "; "), its outcome and mark; a challenge of a name it
    no longer goes by kept as history, marked stale."""
    srv, target, now = named
    status, d = answer(srv, "/api/function?name=" + now["fmt_hex"])
    name = d["name"]
    assert (name["naming"], name["certainty"], name["confidence"], name["proposed"]) == ("proposed", "likely", 0.8,
                                                                                          "format_hex")
    assert name["samples"].startswith("format_hex") and name["shown"].endswith(", review")
    row = name["review"]["rows"][0]
    assert (row["outcome"], row["mark"], row["old"], row["name"], row["stale"]) == ("changed", "changed", "print_hex",
                                                                                    "format_hex", False)
    assert row["reasons"][0]["kind"] == "vote" and row["reasons"][0]["weight"] == pytest.approx(1.2)
    d = answer(srv, "/api/function?name=" + now["ring_put"])[1]
    reasons = d["name"]["review"]["last"]["reasons"]
    assert [r["kind"] for r in reasons] == ["vote", "same"] and reasons[1]["text"].endswith("(puts; takes)")
    assert d["name"]["review"]["mark"] == "suspect" and d["name"]["review"]["last"]["candidate"] == "ring_append"
    d = answer(srv, "/api/function?name=" + now["ring_get"])[1]
    assert d["name"]["review"]["mark"] is None and d["name"]["review"]["stale"] == 1
    assert d["name"]["review"]["rows"][0]["stale"] is True
    d = answer(srv, "/api/function?name=print_status")[1]
    assert d["name"]["certainty"] == "named" and d["name"]["review"]["mark"] == "flagged"
    assert answer(srv, "/api/function?name=uart_puts")[1]["name"]["review"] is None


def test_the_names_reload_when_the_review_changes(named):
    """A review written after the page loaded: /api/status counts the names' tables anew (the page then
    reloads its list), and the list shows the new mark."""
    from dread import namereview
    srv, target, now = named
    before = answer(srv, "/api/status")[1]["names"]
    path = namereview.review_path(target, target.image())
    with open(path, "a") as f:
        f.write("\t".join({"review": "2", "round": "1", "kind": "function", "address": "0x%x" % srv.state.program.by_name[
            "uart_puts"].entry, "now": "uart_puts", "source": "person", "score": "1.00", "reasons": "vague 1.00: says little",
            "old": "uart_puts", "candidate": "uart_write", "outcome": "kept", "name": "uart_puts"}.get(k, "")
            for k in namereview.REVIEW_COLUMNS) + "\n")
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert answer(srv, "/api/status")[1]["names"] == before + 1
    by = {f["name"]: f for f in answer(srv, "/api/functions")[1]}
    assert by["uart_puts"]["review"]["mark"] == "suspect"
    assert answer(srv, "/api/overview")[1]["names"]["review"]["reviews"] == 2


def test_the_search_finds_proposals_too(named):
    """/api/search: a name dread pilot proposed is found ("proposal"), after the names in the tables."""
    srv, target, now = named
    found = answer(srv, "/api/search?q=ring_p")[1]
    assert [(f["kind"], f["name"], f.get("proposed")) for f in found] == [("proposal", now["ring_get"], "ring_pop"),
                                                                          ("proposal", now["ring_put"], "ring_push")]
    found = answer(srv, "/api/search?q=uart")[1]
    assert [f["kind"] for f in found][:4] == ["name"] * 4


@pytest.mark.slow
def test_names_in_the_graphs_and_the_search_that_lights_the_program(named):
    """The relations' and the whole program's nodes: each function's certainty, the vote's agreement
    and the review's mark; a group's functions by certainty, the marks it holds, its own name's
    certainty and review; the things functions of two groups touch, with those functions (the edges
    from shared memory down to an open group's functions). /api/program-search: every function a query
    finds by its name, the name proposed for it, an address in it or a text it refers to, each with its
    group, and the groups that hold them."""
    srv, target, now = named
    status, g = answer(srv, "/api/graph?name=print_status")
    assert status == 200
    nodes = {n["name"]: n for n in g["nodes"] if n["kind"] == "function"}
    assert nodes["print_status"]["certainty"] == "named" and nodes["print_status"]["review"]["mark"] == "flagged"
    assert (nodes[now["fmt_dec"]]["certainty"], nodes[now["fmt_dec"]]["confidence"]) == ("sure", 1.0)
    assert nodes[now["mode_name"]]["certainty"] == "none" and "review" not in nodes[now["mode_name"]]
    p = program(srv)
    functions = {f["name"]: f for f in p["functions"]}
    assert functions[now["ring_put"]]["review"]["mark"] == "suspect" and functions[now["ring_put"]]["confidence"] == 0.6
    assert functions[now["checksum"]]["certainty"] == "unvoted" and "confidence" not in functions[now["checksum"]]
    uart = p["groups"][functions["uart_putc"]["group"]]
    assert uart["label"] == "UART driver" and uart["name_certainty"] == "unsure" and uart["review"]["mark"] == "suspect"
    assert uart["certainty"]["named"] == 4 and uart["marks"] == {"confirmed": 1}
    assert p["groups"][functions["print_status"]["group"]]["marks"].get("flagged") == 1
    group_of = [f["group"] for f in p["functions"]]
    for kind, key, users in p["shared"]:
        assert kind in ("global", "device", "text", "table") and users == sorted(users)
        assert len({group_of[i] for i in users}) > 1
    assert p["counts"]["shared"] == len(p["shared"])
    found = answer(srv, "/api/program-search?q=ring")[1]
    how = {f["name"]: f["how"] for f in found["functions"]}
    assert how[now["ring_put"]] == ["proposal"] and how[now["ring_get"]] == ["proposal"]
    assert all(f["group"] == functions[f["name"]]["group"] for f in found["functions"])
    counted = {}
    for f in found["functions"]:
        counted[f["group"]] = counted.get(f["group"], 0) + 1
    assert {g["group"]: g["functions"] for g in found["groups"]} == counted and found["more"] == 0
    entry = srv.state.program.by_name["uart_putc"].entry
    found = answer(srv, "/api/program-search?q=0x%x" % (entry + 2))[1]
    assert [(f["name"], f["how"]) for f in found["functions"]] == [("uart_putc", ["address"])]
    found = answer(srv, "/api/program-search?q=mode")[1]
    assert any(f["name"] == "print_status" and "text" in f["how"] and f["text"] for f in found["functions"])
    assert answer(srv, "/api/program-search?q=")[1]["functions"] == []


DIGITS = {"input": [[40_000, "uart", "3"], [120_000, "uart", "s"]], "until": {"uart": "volume 3"}, "limit": 5_000_000}


def plant_runs(target):
    """Two records of the system run on gizmo-m3, as dread sysrun keeps them: "digits", the original
    alone (a digit key: dispatch jumps to cmd_volume through its table of commands), and "planted", both
    images, the rebuilt one's log changed at entry 9 (where they first differ)."""
    import copy
    from dread import sysrun
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    o = sysrun.System(target, image, image.payload(), program=program, scenario=DIGITS).run(DIGITS["limit"])
    sysrun.write_record(target, image, sysrun.make_record(program, "digits", DIGITS, [("original", o)], "dread sysrun",
                                                          {"original": image.payload()}))
    boot = target.scenarios["boot"]
    a = sysrun.System(target, image, image.payload(), program=program, scenario=boot).run(int(boot["limit"]))
    b = copy.copy(a)
    b.log = list(a.log)
    b.log[9] = ("write", "uart", 0, 4, 99)
    sysrun.write_record(target, image, sysrun.make_record(program, "boot", boot, [("original", a), ("rebuilt", b)],
                                                          "dread sysrun", {"original": image.payload(), "rebuilt": b"x"},
                                                          name="planted"))
    return o


def test_the_system_run_in_the_page(server):
    """The system run's records (BUILD/sysrun): /api/status counts their changes; /api/timeline lists the
    scenarios, picks the one that differs, and gives windows of both images' device logs with each
    entry's pc and the function there, the first difference marked by the record; a record of other
    bytes than the image's is left out. The transfers the run saw reach the relations (dispatch a
    caller of cmd_volume, through a pointer), the function's page and the overview."""
    import json as json_
    from dread import sysrun
    srv, target = server
    before = answer(srv, "/api/status")[1]["runs"]
    status, empty = answer(srv, "/api/timeline")
    assert status == 200 and empty["scenarios"] == [] and "rows" not in empty
    assert answer(srv, "/api/function?name=cmd_volume")[1]["ran"] is None
    plant_runs(target)
    assert answer(srv, "/api/status")[1]["runs"] == before + 1
    status, t = answer(srv, "/api/timeline")
    assert [(s["name"], s["scenario"]) for s in t["scenarios"]] == [("digits", "digits"), ("planted", "boot")]
    assert t["name"] == "planted" and t["differ"]["at"] == 9 and t["first"] == 0
    assert t["differ"]["lines"][0].startswith("device logs differ at entry 9")
    assert [r["label"] for r in t["runs"]] == ["original", "rebuilt"] and t["start"] <= 9 < t["start"] + 400
    rows, k = t["rows"], 9 - t["start"]
    assert rows["rebuilt"]["log"][k] == ["write", "uart", 0, 4, 99] and rows["original"]["log"][k] != rows["rebuilt"]["log"][k]
    assert sum(len(r["log"]) for r in answer(srv, "/api/timeline?count=1")[1]["rows"].values()) == 2
    status, w = answer(srv, "/api/timeline?name=digits&from=2&count=3")
    original = w["rows"]["original"]
    assert w["start"] == 2 and list(w["rows"]) == ["original"] and len(original["log"]) == len(original["in"]) == 3
    names = {name for e, name in zip(original["log"], original["in"]) if e[:2] == ["uart", "sent"]}
    assert names <= {"uart_putc"} and all(isinstance(pc, int) for pc in original["at"])
    status, all_ = answer(srv, "/api/timeline?name=digits&from=0&count=5000")
    sent = [e[2] for e in all_["rows"]["original"]["log"] if e[:2] == ["uart", "sent"]]
    assert bytes(sent).decode().endswith("volume 3") and all_["kept"] == len(all_["rows"]["original"]["log"])
    g = answer(srv, "/api/graph?name=cmd_volume")[1]
    nodes = {n["name"]: n for n in g["nodes"]}
    assert nodes["dispatch"]["side"] == "caller"
    assert [(e["from"], e["to"], e["how"]) for e in g["edges"] if e["kind"] == "calls-pointer"] == [
        ("f:dispatch", "f:cmd_volume", ["pointer-jump"])]
    assert answer(srv, "/api/graph?name=TIMER_IRQHandler")[1]["nodes"][0]["entered_by"][0]["exception"] == 16
    ran = answer(srv, "/api/function?name=cmd_volume")[1]["ran"]
    assert [(c["name"], c["how"], c["scenarios"]) for c in ran["callers"]] == [("dispatch", "pointer-jump", ["digits"])]
    assert ran["scenarios"] == ["digits", "planted"]
    o = answer(srv, "/api/overview")[1]
    assert [(s["name"], s["pointers"] > 0, bool(s["differ"])) for s in o["sysrun"]] == [("digits", True, False),
                                                                                      ("planted", True, True)]
    assert o["runs"]["sysrun"]
    path = os.path.join(sysrun.record_dir(target, target.image()), "digits.json")
    record = json_.load(open(path))
    record["runs"][0]["digest"] = "0" * 40                          # (the record of another image's bytes)
    with open(path, "w") as f:
        json_.dump(record, f)
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert [s["name"] for s in answer(srv, "/api/timeline")[1]["scenarios"]] == ["planted"]
    assert answer(srv, "/api/function?name=cmd_volume")[1]["ran"]["callers"] == []


C_SAMPLE = r'''/* { a brace in a comment */
#include "dread.h"
#define BLOCK { 1, 2 }
struct pair { u32 a, b; };
static const u32 table[] = { 1, 2, 3 };
u32 prototype(u32 x);

static u32 helper(u32 x)
{
    return x + 1;
}

u32 first(u32 x)
{
    if (x) {
        return helper(x);   /* } */
    }
    return '}';
}

__attribute__((noinline)) u32
second(const char *s, u32 (*f)(u32))
{
    const char *t = "{ not a block }";
    return f(s[0]) + t[0];
}
'''


def test_the_c_reader_finds_global_definitions(tmp_path):
    path = tmp_path / "sample.c"
    path.write_text(C_SAMPLE)
    found = gui.definitions(str(path))
    assert found == {"first": (13, 19), "second": (21, 26)}
