"""dread gui's compiler lab page (dread/guipages/cclab.py) through its API: a work directory typed as a path
from where dread gui started, the built-in adapters with their matrices, a run's command line from the
page's choices (builds, one's own probes, --lift, --guard) and its refusals, the doctor's; with GCC, a
compiler's probes run as a job from the page (one build, a probe of one's own that never returns), the report
made as a job (dread cclab report --json) and read back with the probe that failed, a record made stale by a
newer run; two compilers compared from the page (dread cclab compare --json as a job, its record read back,
listed on the work page, made stale by a newer result, names that are no compiler refused); a read-only
server that starts nothing and a change without the token."""
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
        assert answer(srv, "/api/cclab/compare", {"original": "a", "rebuild": "b"})[0] == 403
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


@pytest.mark.slow
@pytest.mark.compiles
@needs_gcc
def test_two_compilers_compared_from_the_page(server):
    """GCC with int-sized enums and signed plain char as the original's compiler and GCC as it comes as the
    rebuilding one, measured in one work directory: the comparison's view has no record, may start it, and
    starts dread cclab compare --json as a job; its record read back says where they differ in what linking
    depends on (each result with its builds), the options that close a difference and how many probes
    agree; the work page lists it; a newer result makes it stale; names that are no compiler measured there
    (a path, an unknown one, one missing) are refused, by both routes."""
    from dread.cclab import adapters as ad
    from test_cclab import measured
    srv, home = server
    work = home / "lab"
    text = open(ad.builtin()["gcc-arm"]).read().replace('name = "gcc-arm"', 'name = "variant"')
    text = text.replace('support_flags = ["-fno-builtin"]',
                        'support_flags = ["-fno-builtin"]\ncflags = ["-fno-short-enums", "-fsigned-char"]')
    (home / "variant.toml").write_text(text)
    measured(work, str(home / "variant.toml"), ["cortex-m3-O2"])
    measured(work, "gcc-arm", ["cortex-m3-O2"])
    d = answer(srv, q("/api/cclab", work="lab"))[1]
    assert {a["name"] for a in d["adapters"]} == {"variant", "gcc-arm"} and d["compares"] == [], d
    pair = {"original": "variant", "rebuild": "gcc-arm"}
    c = answer(srv, q("/api/cclab/compare", work="lab", **pair))[1]
    assert c["record"] is None and c["auto"] and c["path"] == str(work / "compare-variant-gcc-arm.json"), c
    status, job = answer(srv, "/api/cclab/compare", dict(pair, work="lab"))
    assert status == 200 and job["args"] == ["compare", "--work", str(work), "variant", "gcc-arm", "--json",
                                             c["path"]], job
    assert wait_job(srv, job["id"], timeout=600)["state"] == "done"
    c = answer(srv, q("/api/cclab/compare", work="lab", **pair))[1]
    rec = c["record"]
    assert not c["auto"] and c["stale"] == [] and (rec["original"], rec["rebuild"]) == ("variant", "gcc-arm"), c
    abi = {r["probe"]: r for r in rec["abi"]}
    assert abi["size_enum_small"]["original"] == [{"text": "4", "builds": ["cortex-m3-O2"], "shown": "all 1"}]
    assert abi["size_enum_small"]["rebuild"][0]["text"] == "1" and "char_is_signed" in abi
    assert {"option": "-fno-short-enums", "probes": ["size_enum_small"]} in rec["hints"]
    assert rec["same"] > 40 and rec["markdown"] == str(work / "compare-variant-gcc-arm.md")
    assert os.path.exists(rec["markdown"])
    d = answer(srv, q("/api/cclab", work="lab"))[1]
    assert [(x["original"], x["rebuild"]) for x in d["compares"]] == [("variant", "gcc-arm")]
    later = time.time() + 5                                 # (a newer result: the comparison is stale)
    results = work / "gcc-arm" / "cortex-m3-O2" / "results.json"
    os.utime(results, (later, later))
    c = answer(srv, q("/api/cclab/compare", work="lab", **pair))[1]
    assert c["stale"] == [os.path.join("gcc-arm", "cortex-m3-O2", "results.json")] and c["auto"], c
    for bad in ({"original": "variant", "rebuild": "../lab"}, {"original": "nothing", "rebuild": "gcc-arm"},
                {"original": "variant"}):
        assert answer(srv, "/api/cclab/compare", dict(bad, work="lab"))[0] == 400, bad
        assert answer(srv, q("/api/cclab/compare", work="lab", **bad))[0] == 400, bad

