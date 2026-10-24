"""dread gui's bench page (dread/guipages/bench.py) through its API, on a bench of its own ($DREAD_BENCH in a
temporary directory) with a recipe built from gizmo's sources: the recipes and their reports, the report run
as a job (dread bench report --json) and read back, a record made stale by a newer measurement, the trend of
a figure over a build's measurements, a build's entry, the command line and estimate of a measurement and
its refusals, a server that starts nothing (read-only) and a change without the token; and, with GCC, a
build measured from the page as a job and opened as the page's target."""
import json
import os
import threading
import time

import pytest

from dread import bench, gui
from dread.guipages import bench as page
from test_bench import needs_gcc, recipe, measurement, write_measurements
from test_gui import answer
from test_gui_run import wait_job


@pytest.fixture
def bench_dir(tmp_path, monkeypatch):
    """A bench of the test's own with the gizmo recipe, which the page lists as DREAD's own would be."""
    monkeypatch.setenv("DREAD_BENCH", str(tmp_path / "bench"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("DREAD_TARGET", raising=False)
    r = recipe(tmp_path)
    monkeypatch.setattr(page, "recipe_files", lambda: {"gizmo": r["_file"]})
    return r


def serve(home, **kw):
    srv = gui.serve(None, None, port=0, home=str(home), **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def server(bench_dir, tmp_path):
    srv = serve(tmp_path)
    yield srv, bench_dir
    srv.shutdown()
    srv.server_close()


def where(r):
    return os.path.join(bench.bench_dir(), "gizmo", "m3-Os")


def q(path, **params):
    """A GET's address with its query (JSON for lists)."""
    import urllib.parse
    return path + "?" + urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, list) else v for k, v in params.items()})


def test_the_report_as_a_job_and_read_back(server):
    srv, r = server
    write_measurements(where(r), r, [("20261020-101500", measurement(30, 40))], measurement(33, 40, readable=31))
    g = answer(srv, "/api/gui")[1]
    assert {"name": "bench", "script": "bench.js", "style": "bench.css"} in g["pages"]
    o = answer(srv, "/api/bench")[1]
    assert o["bench"] == bench.bench_dir() and o["steps"] == list(bench.STEPS) and o["changes"] is None
    assert o["recipes"] == [{"name": "gizmo", "file": r["_file"], "builds": 1, "measured": 1, "newest": o["recipes"][0]["newest"],
                             "report": None}]
    d = answer(srv, "/api/bench/report?recipe=gizmo")[1]
    assert d["record"] is None and d["auto"] and d["why"] is None and d["path"] == page.record_path("gizmo")
    status, job = answer(srv, "/api/bench/report", {"recipe": "gizmo"})
    assert status == 200 and job["command"] == "bench" and job["args"][:2] == ["report", r["_file"]], job
    assert job["args"][-2:] == ["--json", page.record_path("gizmo")]
    assert wait_job(srv, job["id"])["state"] == "done"
    d = answer(srv, "/api/bench/report?recipe=gizmo&trend=lifted")[1]
    b, = d["record"]["builds"]
    assert d["stale"] == [] and not d["auto"] and d["job"]["id"] == job["id"] and "metrics" not in b and "history" not in b
    assert b["figures"]["lifted"] == 33 and b["kind"] == "gcc" and b["release"] == "13.2.1" and b["current"] is True
    assert [p[1] for p in b["trend"]] == [30, 33] and b["trend"][0][0] < b["trend"][1][0]
    assert [p[1] for p in answer(srv, "/api/bench/report?recipe=gizmo&trend=readable")[1]["record"]["builds"][0]["trend"]] \
        == [None, 31]
    full = answer(srv, "/api/bench/build?recipe=gizmo&build=m3-Os")[1]
    assert full["metrics"]["lift"]["verdicts"]["accepted"] == 33 and len(full["history"]) == 2
    assert full["open"].startswith("no dread.toml") and full["why"] is None and full["steps"] == list(bench.STEPS)
    assert answer(srv, "/api/bench/build?recipe=gizmo&build=nothing")[0] == 404
    later = time.time() + 5                                     # (a newer measurement: the report is stale)
    os.utime(os.path.join(where(r), "metrics.json"), (later, later))
    d = answer(srv, "/api/bench/report?recipe=gizmo")[1]
    assert d["stale"] == [os.path.join(where(r), "metrics.json")] and d["stale_count"] == 1 and d["auto"]
    assert answer(srv, "/api/bench/report?recipe=nothing")[0] == 404
    assert answer(srv, "/api/bench/report?recipe=../etc")[0] == 404


def test_a_measurement_s_command_line_and_its_refusals(server):
    srv, r = server
    write_measurements(where(r), r, [], measurement(33, 40))
    line = answer(srv, q("/api/bench/measure-line", recipe="gizmo", builds=["m3-Os"], steps=["asm", "lift", "readable"]))[1]
    assert line["args"] == ["measure", r["_file"], "m3-Os", "--steps", "asm,lift,readable", "--json", page.record_path("gizmo")]
    assert line["estimate"] == 100.5 and line["unknown"] == ["readable"] and line["error"] is None   # (asm 0.5 + lift 100)
    every = answer(srv, q("/api/bench/measure-line", recipe="gizmo", builds=["m3-Os"], steps=list(bench.STEPS)))[1]
    assert "--steps" not in every["args"]
    assert "choose the builds" in answer(srv, q("/api/bench/measure-line", recipe="gizmo", builds=[]))[1]["error"]
    assert "no build nope" in answer(srv, q("/api/bench/measure-line", recipe="gizmo", builds=["nope"]))[1]["error"]
    assert "steps" in answer(srv, q("/api/bench/measure-line", recipe="gizmo", builds=["m3-Os"], steps=["asm", "bogus"]))[1]["error"]
    assert answer(srv, "/api/bench/measure", {"recipe": "gizmo", "builds": []})[0] == 400
    assert answer(srv, "/api/bench/measure", {"recipe": "gizmo", "builds": ["m3-Os"]}, token=False)[0] == 403
    assert answer(srv, "/api/bench/report", {"recipe": "gizmo"}, token=False)[0] == 403
    status, r2 = answer(srv, "/api/bench/open", {"recipe": "gizmo", "build": "m3-Os"})
    assert status == 409 and "dread.toml" in r2["error"], r2       # (only its figures are here)
    assert answer(srv, "/api/bench/open", {"recipe": "gizmo", "build": "../m3-Os"})[0] == 404


def test_a_read_only_server_shows_and_starts_nothing(bench_dir, tmp_path):
    r = bench_dir
    write_measurements(where(r), r, [], measurement(33, 40))
    srv = serve(tmp_path, read_only=True)
    try:
        d = answer(srv, "/api/bench/report?recipe=gizmo")[1]
        assert d["record"] is None and not d["auto"] and "read-only" in d["changes"]
        assert answer(srv, "/api/bench/report", {"recipe": "gizmo"})[0] == 403
        assert answer(srv, "/api/bench/measure", {"recipe": "gizmo", "builds": ["m3-Os"]})[0] == 403
        assert answer(srv, "/api/bench/open", {"recipe": "gizmo", "build": "m3-Os"})[0] == 403
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.slow
@pytest.mark.compiles
@needs_gcc
def test_a_build_measured_from_the_page_and_opened(server):
    """The gizmo recipe's build made here, measured once in this process and once from the page as a job
    (two steps): the record has both measurements as its trend; the build then opens as the page's target."""
    srv, r = server
    bench.build(r, "m3-Os", out=lambda *a: None)
    bench.measure(r, "m3-Os", steps=("asm",), out=lambda *a: None)
    status, job = answer(srv, "/api/bench/measure", {"recipe": "gizmo", "builds": ["m3-Os"], "steps": ["asm", "noreturn"]})
    assert status == 200 and job["args"][:3] == ["measure", r["_file"], "m3-Os"], job
    assert "--steps" in job["args"] and job["args"][-2:] == ["--json", page.record_path("gizmo")]
    assert wait_job(srv, job["id"], timeout=600)["state"] == "done"
    d = answer(srv, "/api/bench/report?recipe=gizmo&trend=noreturn")[1]
    b, = d["record"]["builds"]
    assert d["stale"] == [] and b["figures"]["listing_identical"] is True and b["figures"]["noreturn"] >= 2
    assert len(b["trend"]) == 2 and b["trend"][0][1] is None and b["trend"][1][1] >= 2 and b["target"] is True
    full = answer(srv, "/api/bench/build?recipe=gizmo&build=m3-Os")[1]
    assert full["open"] is None
    status, opened = answer(srv, "/api/bench/open", {"recipe": "gizmo", "build": "m3-Os"})
    assert status == 200 and opened["target"]["root"] == where(r), opened
    assert answer(srv, "/api/gui")[1]["target"]["root"] == where(r)
    assert answer(srv, "/api/bench/build?recipe=gizmo&build=m3-Os")[1]["open"] == "this is the target shown"
