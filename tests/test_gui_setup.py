"""dread gui's Setup page through its API: a server started without a target (its jobs in the user's state
directory, run where it started), a file uploaded into a new target (the token, the size limit, a plain
name, the new target's original/ and nowhere else), dread new run as a job and its target opened (the
server switches; not while a job of its runs), the first analyses as a chain of jobs (one that fails
cancels the rest), the providers set up from the page (llm.toml written, a key into a key file of mode
600 that no answer holds), a provider tried as a job; a read-only server and one that listens beyond
this machine change nothing."""
import hashlib
import json
import os
import shutil
import stat
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from conftest import GIZMO
from dread import gui
from dread import jobs as jobs_
from dread.guipages import setup
from test_gui import answer, request

KEY = "sk-page-0123456789abcdefghij"
STANDIN = "import sys\nprint('pong: ' + sys.argv[1][-30:])\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    where = tmp_path / "home"
    where.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config = tmp_path / "cfg" / "llm.toml"
    config.parent.mkdir(mode=0o700)
    monkeypatch.setenv("DREAD_LLM", str(config))
    monkeypatch.delenv("DREAD_TARGET", raising=False)
    return where


def started(srv):
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def bare(home):
    srv = started(gui.serve(None, None, port=0, home=str(home)))
    yield srv, home
    srv.shutdown()
    srv.server_close()


def upload(srv, folder, name, data, token=True):
    import urllib.parse
    req = urllib.request.Request("http://127.0.0.1:%d/api/setup/upload?%s" % (
        srv.server_address[1], urllib.parse.urlencode({"dir": folder, "name": name})), data=data, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    if token:
        req.add_header("X-Dread-Token", srv.token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except (ConnectionError, urllib.error.URLError) as e:     # (refused before the body was read)
        return 413, {"error": str(e)}


def wait_job(srv, number, timeout=300):
    deadline = time.time() + timeout
    while True:
        status, r = answer(srv, "/api/job?id=%d" % number)
        if status == 200 and r["state"] not in jobs_.LIVE:
            return r
        assert time.time() < deadline, r
        time.sleep(0.1)


def test_a_server_without_a_target(bare):
    srv, home = bare
    g = answer(srv, "/api/gui")[1]
    assert g["target"] is None and g["local"] is True and g["home"] == str(home) and g["upload_limit"] == gui.UPLOAD_LIMIT
    assert {"name": "setup", "script": "setup.js", "style": "setup.css"} in g["pages"]
    status = answer(srv, "/api/status")[1]
    assert status["target"] is None and status["pages"]["setup"] == {"target": None}
    assert answer(srv, "/api/overview") == (409, {"error": "no target yet: the Setup page (#/setup) makes or opens one",
                                                  "setup": True})
    assert answer(srv, "/api/functions")[0] == 409
    d = answer(srv, "/api/command?name=new")[1]
    assert d["cwd"] == str(home) and d["defaults"] == {} and d["runnable"]
    for path in ("/setup.js", "/setup.css"):
        assert request(srv, path)[0] == 200
    state = answer(srv, "/api/setup/state")[1]
    assert state["target"] is None and state["home"] == str(home) and state["changes"] is None
    assert any(a["name"] == "cortex-m3" for a in state["arches"])
    doctor = answer(srv, "/api/setup/doctor")[1]
    assert doctor["line"] == "dread doctor" and any(f["id"] == "unicorn" for f in doctor["findings"])
    assert "no target yet" in answer(srv, "/api/setup/chain")[1]["error"]
    i = answer(srv, "/api/setup/inspect", {"path": os.path.join(GIZMO, "m3", "original", "gizmo.elf")})[1]
    assert i["kind"] == "elf" and i["machine"] == "arm" and i["supported"]
    assert answer(srv, "/api/setup/inspect", {"path": "nothing.bin"})[1]["kind"] == "missing"
    assert answer(srv, "/api/setup/inspect", {"path": "x"}, token=False)[0] == 403


def test_an_upload_lands_in_the_new_target_alone(bare, home):
    srv, home = bare
    data = open(os.path.join(GIZMO, "m3", "original", "gizmo.elf"), "rb").read()
    assert upload(srv, "fw", "gizmo.elf", data, token=False)[0] == 403
    status, r = upload(srv, "fw", "../../up/../evil name.elf", data)
    assert status == 200, r
    assert r["path"] == "fw/original/evil_name.elf" and r["size"] == len(data)
    assert r["sha256"] == hashlib.sha256(data).hexdigest()
    assert sorted(os.listdir(home)) == ["fw"] and os.listdir(home / "fw") == ["original"]
    assert os.listdir(home / "fw" / "original") == ["evil_name.elf"]
    assert open(home / "fw" / "original" / "evil_name.elf", "rb").read() == data
    (home / "busy").mkdir()
    (home / "busy" / "notes.txt").write_text("mine")
    status, r = upload(srv, "busy", "x.bin", b"1234")
    assert status == 409 and "not empty" in r["error"]
    assert upload(srv, "", "x.bin", b"1234")[0] == 400
    (home / "linked").mkdir()
    (home / "linked" / "original").symlink_to(home / "busy")
    status, r = upload(srv, "linked", "x.bin", b"1234")
    assert status == 409 and "symbolic link" in r["error"] and not (home / "busy" / "x.bin").exists()
    small = started(gui.serve(None, None, port=0, home=str(home), upload_limit=1000))
    try:
        status, r = upload(small, "other", "big.bin", b"x" * 2000)
        assert status == 413
        assert not (home / "other").exists()
        assert upload(small, "other", "fits.bin", b"x" * 900)[0] == 200
    finally:
        small.shutdown()
        small.server_close()


def test_a_target_made_and_opened_from_the_page(bare, tmp_path):
    srv, home = bare
    data = open(os.path.join(GIZMO, "m3", "original", "gizmo.elf"), "rb").read()
    assert upload(srv, "fw", "gizmo.elf", data)[0] == 200
    status, r = answer(srv, "/api/jobs/start", {"command": "new", "values": {"dir": "fw", "image": "fw/original/gizmo.elf"}})
    assert status == 200 and r["line"] == "dread new fw fw/original/gizmo.elf" and r["cwd"] == str(home)
    done = wait_job(srv, r["id"])
    assert done["state"] == "done", answer(srv, "/api/job-log?id=%d&from=0" % r["id"])[1]["text"]
    home_jobs = os.path.join(str(tmp_path / "state"), "dread", "gui", "jobs")
    assert os.path.exists(os.path.join(home_jobs, "%d.json" % r["id"]))
    assert answer(srv, "/api/setup/open", {"path": "nowhere"})[0] == 409
    status, opened = answer(srv, "/api/setup/open", {"job": r["id"]})
    assert status == 200 and opened["target"]["name"] == "fw" and opened["target"]["root"] == str(home / "fw")
    assert answer(srv, "/api/gui")[1]["target"]["name"] == "fw"
    assert answer(srv, "/api/status")[1]["target"] == str(home / "fw")
    assert answer(srv, "/api/overview")[0] == 200
    assert srv.jobs.directory == os.path.join(str(home / "fw"), "build", "gui", "jobs") and srv.jobs.owner is None
    assert answer(srv, "/api/command?name=lift")[1]["defaults"] == {"--target": "."}
    state = answer(srv, "/api/setup/state")[1]
    assert state["functions"] == 50 and state["home"] == str(home)          # (typed paths start where it started)
    steps = answer(srv, "/api/setup/chain")[1]
    assert steps["planned"] and [s["step"] for s in steps["steps"]] == ["functions", "regflow", "lift", "readable"]
    assert steps["steps"][0]["state"] == "done" and steps["steps"][2]["line"] == "dread lift --check"
    assert answer(srv, "/api/setup/open", {"path": str(home / "fw")})[0] == 200          # (the same: nothing to do)
    shutil.copy(os.path.join(GIZMO, "rv32", "original", "gizmo.elf"), home / "rv.elf")
    status, r = answer(srv, "/api/setup/new", {"values": {"dir": "beside", "image": "rv.elf"}})
    assert status == 200 and r["cwd"] == str(home) and r["line"] == "dread new beside rv.elf"
    assert wait_job(srv, r["id"])["state"] == "done"
    assert (home / "beside" / "dread.toml").exists() and not (home / "fw" / "beside").exists()
    status, r = answer(srv, "/api/setup/new", {"values": {"dir": "x", "image": "rv.elf", "--base": "nowhere"}})
    assert status == 400 and r["field"] == "--base"
    second = shutil.copytree(os.path.join(GIZMO, "m3"), str(tmp_path / "second"), ignore=shutil.ignore_patterns("build"))
    r = srv.jobs.start([sys.executable, "-c", "import time; time.sleep(60)"], str(home / "fw"))
    status, refused = answer(srv, "/api/setup/open", {"path": second})
    assert status == 409 and "still run or wait" in refused["error"] and "dread gui --target" in refused["error"]
    srv.jobs.stop(r["id"])
    wait_job(srv, r["id"])
    status, opened = answer(srv, "/api/setup/open", {"path": second})
    assert status == 200 and opened["target"]["name"] == "gizmo-m3"
    status, refused = answer(srv, "/api/setup/open", {"job": 1})
    assert status in (404, 409)


def test_the_first_analyses_as_a_chain(bare, monkeypatch):
    """The page starts the steps as jobs, each waiting for the one before; a step that fails cancels the rest;
    the chain is kept in BUILD/gui/chain.json and shown with each step's state. (Cheap commands stand for the
    analyses here.)"""
    srv, home = bare
    target = shutil.copytree(os.path.join(GIZMO, "m3"), str(home / "fw"), ignore=shutil.ignore_patterns("build"))
    assert answer(srv, "/api/setup/open", {"path": "fw"})[0] == 200
    steps = [{"step": "functions", "command": None, "values": None, "why": "there"},
             {"step": "regflow", "command": "info", "values": {}, "why": "first"},
             {"step": "lift", "command": "functions", "values": {}, "why": "second"},
             {"step": "readable", "command": "flat", "values": {"names": "no_such_function"}, "why": "fails"},
             {"step": "after", "command": "info", "values": {}, "why": "never"}]
    monkeypatch.setattr(setup.newtarget, "first_analyses", lambda target, image=None: [dict(s) for s in steps])
    planned = answer(srv, "/api/setup/chain")[1]
    assert planned["planned"] and [s["state"] for s in planned["steps"]] == ["done"] + ["planned"] * 4
    assert answer(srv, "/api/setup/chain", {}, token=False)[0] == 403
    status, chain = answer(srv, "/api/setup/chain", {})
    assert status == 200 and not chain["planned"]
    ids = [s["job"] for s in chain["steps"]]
    assert ids[0] is None and all(isinstance(i, int) for i in ids[1:])
    records = [answer(srv, "/api/job?id=%d" % i)[1] for i in ids[1:]]
    assert [r["after"] for r in records] == [None] + ids[1:-1]
    assert records[0]["line"] == "dread info --target ."
    deadline = time.time() + 300
    while answer(srv, "/api/setup/chain")[1]["live"]:
        assert time.time() < deadline
        time.sleep(0.2)
    chain = answer(srv, "/api/setup/chain")[1]
    assert [s["state"] for s in chain["steps"]] == ["done", "done", "done", "failed", "cancelled"]
    assert chain["steps"][3]["exit"] not in (None, 0) and chain["steps"][3]["last"]
    kept = json.load(open(os.path.join(target, "build", "gui", "chain.json")))
    assert kept["version"] == 1 and [s["job"] for s in kept["steps"]] == ids
    status, again = answer(srv, "/api/setup/chain", {})
    assert status == 200 and again["steps"][1]["job"] > ids[-1]
    deadline = time.time() + 300
    while answer(srv, "/api/setup/chain")[1]["live"]:
        assert time.time() < deadline
        time.sleep(0.2)


def test_providers_from_the_page(bare, tmp_path):
    srv, home = bare
    config = tmp_path / "cfg" / "llm.toml"
    assert answer(srv, "/api/setup/providers")[1]["exists"] is False
    status, r = answer(srv, "/api/setup/provider", {"name": "claude", "changes": {"kind": "anthropic", "model": "m",
                                                                                 "price_in": "3", "url": ""},
                                                    "default": True})
    assert status == 200, r
    assert r["line"] == "dread llm set claude --kind anthropic --model m --price-in 3 --default"
    assert {"field": "default", "before": None, "after": "claude"} in r["done"]
    assert 'default = "claude"' in config.read_text() and r["providers"]["providers"][0]["key"]["set"] is False
    status, r = answer(srv, "/api/setup/provider", {"name": "claude", "changes": {"price_in": "-1"}})
    assert status == 400 and "0 or more" in r["error"]
    status, r = answer(srv, "/api/setup/key", {"name": "claude", "key": KEY})
    assert status == 200 and KEY not in json.dumps(r)
    assert r["key"]["set"] is True and r["key"]["how"] == "file " + str(config.parent / "keys" / "claude")
    assert r["line"] == "dread llm key claude"
    key_file = config.parent / "keys" / "claude"
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600 and key_file.read_text().strip() == KEY
    listed = request(srv, "/api/setup/providers")[2].decode()
    assert KEY not in listed and json.loads(listed)["providers"][0]["key"]["set"] is True
    status, r = answer(srv, "/api/setup/key", {"name": "claude", "key": "two words"})
    assert status == 400 and "one word" in r["error"]
    assert answer(srv, "/api/setup/key", {"name": "claude", "key": KEY}, token=False)[0] == 403
    assert answer(srv, "/api/setup/ask", {"name": "../x"})[0] == 400


def test_a_provider_tried_as_a_job(bare, tmp_path):
    srv, home = bare
    script = tmp_path / "standin.py"
    script.write_text(STANDIN)
    config = tmp_path / "cfg" / "llm.toml"
    config.write_text('[providers.here]\nkind = "command"\nmodel = "x"\ncommand = %s\n'
                      % json.dumps([sys.executable, str(script), "{prompt}"]))
    status, r = answer(srv, "/api/setup/ask", {"name": "here"})
    assert status == 200 and r["line"] == "dread llm ask --provider here 'Answer in five words: are you there?'"
    done = wait_job(srv, r["id"])
    text = answer(srv, "/api/job-log?id=%d&from=0" % r["id"])[1]["text"]
    assert done["state"] == "done" and "pong: " in text and "are you there?" in text


def test_a_read_only_or_far_server_changes_nothing(home, tmp_path):
    viewer = started(gui.serve(None, None, port=0, home=str(home), read_only=True))
    try:
        assert answer(viewer, "/api/setup/state")[1]["changes"].startswith("this page is read-only")
        assert upload(viewer, "fw", "x.bin", b"1234")[0] == 403
        for path, body in (("/api/setup/provider", {"name": "a", "changes": {"kind": "openai", "model": "m"}}),
                           ("/api/setup/key", {"name": "a", "key": KEY}), ("/api/setup/ask", {"name": "a"}),
                           ("/api/setup/open", {"path": "x"}), ("/api/setup/chain", {})):
            status, r = answer(viewer, path, body)
            assert status == 403 and "read-only" in r["error"], path
        assert answer(viewer, "/api/setup/inspect", {"path": "x"})[0] == 200        # (it changes nothing)
    finally:
        viewer.shutdown()
        viewer.server_close()
    assert not (home / "fw").exists() and not (tmp_path / "cfg" / "llm.toml").exists()
    far = started(gui.serve(None, None, host="0.0.0.0", port=0, home=str(home)))
    try:
        assert "beyond this machine" in answer(far, "/api/setup/state")[1]["changes"]
        status, r = answer(far, "/api/setup/provider", {"name": "a", "changes": {"kind": "openai", "model": "m"}})
        assert status == 403 and "beyond this machine" in r["error"]
        assert upload(far, "fw", "x.bin", b"1234")[0] == 403
    finally:
        far.shutdown()
        far.server_close()
    assert not (home / "fw").exists() and not (tmp_path / "cfg" / "llm.toml").exists()
