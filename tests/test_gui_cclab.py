"""dread gui's compiler lab page (dread/guipages/cclab.py) through its API: a work directory typed as a path
from where dread gui started, the built-in adapters with their matrices, a run's command line from the
page's choices (builds, one's own probes, --lift, --guard) and its refusals, the doctor's; with GCC, a
compiler's probes run as a job from the page (one build, a probe of one's own that never returns), the report
made as a job (dread cclab report --json) and read back with the probe that failed, a record made stale by a
newer run; a read-only server that starts nothing and a change without the token."""
import json
import os
import threading
import time
import urllib.parse

import pytest

from dread import gui
from dread.guipages import cclab as page
from test_cclab import SPIN_PROBE, needs_gcc
from test_gui import answer
from test_gui_run import wait_job


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("DREAD_TARGET", raising=False)
    where = tmp_path / "home"
    where.mkdir()
    return where


def serve(home, **kw):
    srv = gui.serve(None, None, port=0, home=str(home), **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def server(home):
    srv = serve(home)
    yield srv, home
    srv.shutdown()
    srv.server_close()


def q(path, **params):
    return path + "?" + urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, list) else v for k, v in params.items()})


def test_the_work_directory_and_a_run_s_command_line(server):
    srv, home = server
    g = answer(srv, "/api/gui")[1]
    assert {"name": "cclab", "script": "cclab.js", "style": "cclab.css"} in g["pages"]
    d = answer(srv, "/api/cclab")[1]
    assert d["work"] == str(home / "cclab-work") and not d["exists"] and d["adapters"] == [] and not d["auto"]
    gcc = next(a for a in d["builtin"] if a["name"] == "gcc-arm")
    assert gcc["arch"] == "arm" and "cortex-m3-Os" in gcc["builds"] and gcc["matrix"]["opt"] == ["O0", "Os", "O2", "O3"]
    (home / "own").mkdir()
    line = answer(srv, q("/api/cclab/line", work="w", adapter="gcc-arm", builds=["cortex-m3-Os", "cortex-m0-O2"],
                         probe_dirs=["own"], lift="1"))[1]
    assert line["error"] is None and line["adapter"]["name"] == "gcc-arm", line
    assert line["args"] == ["run", "--adapter", "gcc-arm", "--work", str(home / "w"), "--build", "cortex-m0-O2",
                            "--build", "cortex-m3-Os", "--probe-dir", str(home / "own"), "--lift"]
    assert line["line"].startswith("dread cclab run --adapter gcc-arm --work ")
    every = answer(srv, q("/api/cclab/line", work="w", adapter="gcc-arm", builds=gcc["builds"]))[1]["args"]
    assert "--build" not in every                       # (all of the matrix: no --build)
    doctor = answer(srv, q("/api/cclab/line", work="w", adapter="gcc-arm", action="doctor", guard="1"))[1]["args"]
    assert doctor == ["doctor", "--adapter", "gcc-arm", "--work", str(home / "w"), "--guard"]
    assert "no build nope" in answer(srv, q("/api/cclab/line", adapter="gcc-arm", builds=["nope"]))[1]["error"]
    assert "no adapter" in answer(srv, q("/api/cclab/line", adapter="nonesuch"))[1]["error"]
    assert "no directory" in answer(srv, q("/api/cclab/line", adapter="gcc-arm", probe_dirs=["missing"]))[1]["error"]
    assert answer(srv, q("/api/cclab/line", adapter="ce:carmug1320"))[1]["adapter"]["name"] == "ce-carmug1320"
    assert answer(srv, "/api/cclab/run", {"adapter": "gcc-arm", "builds": ["nope"]})[0] == 400
    assert answer(srv, "/api/cclab/run", {"adapter": "gcc-arm"}, token=False)[0] == 403
    assert answer(srv, "/api/cclab/report", {"work": "w"})[0] == 409          # (nothing measured there)
    assert answer(srv, q("/api/cclab/adapter", work="w", adapter="gcc-arm"))[0] == 404


def test_a_read_only_server_starts_nothing(home):
    srv = serve(home, read_only=True)
    try:
        d = answer(srv, "/api/cclab")[1]
        assert "read-only" in d["changes"] and not d["auto"]
        assert answer(srv, "/api/cclab/run", {"adapter": "gcc-arm"})[0] == 403
        assert answer(srv, "/api/cclab/report", {})[0] == 403
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.slow
@pytest.mark.compiles
@needs_gcc
def test_a_compiler_s_probes_run_and_its_report_read_back(server):
    srv, home = server
    own = home / "own"
    own.mkdir()
    (own / "spin.c").write_text(SPIN_PROBE[0])
    (own / "spin.toml").write_text(SPIN_PROBE[1])
    status, job = answer(srv, "/api/cclab/run", {"work": "lab", "adapter": "gcc-arm", "builds": ["cortex-m3-Os"],
                                                 "probe_dirs": ["own"], "lift": False})
    assert status == 200 and job["command"] == "cclab" and job["args"][:2] == ["run", "--adapter"], job
    assert wait_job(srv, job["id"], timeout=600)["state"] == "done"
    d = answer(srv, q("/api/cclab", work="lab"))[1]
    assert d["exists"] and d["auto"] and d["when"] is None
    assert d["adapters"] == [{"name": "gcc-arm", "version": d["adapters"][0]["version"], "builds": 1, "measured": 1,
                              "failed": 0, "reported": False}] and d["adapters"][0]["version"]
    status, made = answer(srv, "/api/cclab/report", {"work": "lab"})
    assert status == 200 and made["args"] == ["report", "--work", str(home / "lab"), "--json", str(home / "lab" / "report.json")]
    assert wait_job(srv, made["id"], timeout=600)["state"] == "done"
    d = answer(srv, q("/api/cclab", work="lab"))[1]
    assert d["when"] and d["stale"] == [] and not d["auto"] and d["adapters"][0]["reported"]
    a = answer(srv, q("/api/cclab/adapter", work="lab", adapter="gcc-arm"))[1]
    rec = a["record"]
    assert rec["measured"] == ["cortex-m3-Os"] and rec["profile"]["helpers"] is not None
    failed, = rec["failed_probes"]
    assert failed["probe"] == "spin_forever" and failed["results"][0]["builds"] == ["cortex-m3-Os"]
    args = {s["kind"]: s for s in rec["sections"]}["arg"]["rows"]
    assert any(r["probe"] == "pick_i5_5" and r["results"][0]["text"] == "stack+0" for r in args)
    later = time.time() + 5                                 # (a newer run: the report is stale)
    results = home / "lab" / "gcc-arm" / "cortex-m3-Os" / "results.json"
    os.utime(results, (later, later))
    d = answer(srv, q("/api/cclab", work="lab"))[1]
    assert d["stale"] == [os.path.join("gcc-arm", "cortex-m3-Os", "results.json")] and d["auto"]
    a = answer(srv, q("/api/cclab/adapter", work="lab", adapter="gcc-arm"))[1]
    assert a["stale_count"] == 1 and a["record"] is not None
