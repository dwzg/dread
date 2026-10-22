"""dread gui on a copy of gizmo: the page and its files, the answers of the API, the two kinds of change
and the refusals (another host name, a change without the page's token); and the C reader that finds
the definitions."""
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


@pytest.fixture
def server(tmp_path):
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    for cache in ("autocases", "regflow"):
        here = os.path.join(gizmo_dir("m3"), "build", cache)
        if os.path.isdir(here):
            shutil.copytree(here, copy / "m3" / "build" / cache)
    target = Target.load(str(copy / "m3"))
    srv = gui.serve(target, target.image(), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    deadline = time.time() + 60
    while srv.state.pending() and time.time() < deadline:
        time.sleep(0.05)
    yield srv, target
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
    for path, kind in (("/app.js", "text/javascript"), ("/app.css", "text/css")):
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
    page = request(srv, "/")[2].decode()
    assert "<kbd>r</kbd>" in page and "relations" in page


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
