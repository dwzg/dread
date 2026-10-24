"""dread gui's leak check page (dread/guipages/leakcheck.py) through its API, on a made-up private project
(tables, words, an allow list) and a tree with a history that leaks some of it: the form's command line, the
check run as a job of the page's own and read back (each hit with its file, its lines and whether it is
allowed; the history's hits; the authors), the strings of the private files that did not match in no answer
and no log (nor their comments), the job's record and log in the user's state directory and none in the
target's, a check run again replacing its record; a read-only server, a missing path and a change without
the token refused."""
import glob
import json
import os
import subprocess
import time
import urllib.parse

import pytest

from dread import gui
from dread import jobs as jobs_
from dread.guipages import leakcheck as page
from test_gui import answer, gizmo_copy, request, serving

# Strings of the private files that are in no file of the tree: no answer may hold them.
NEVER = ("quuxify_frobnicator", "vexilla_handler", "g_wibble_state", "0x13800", "0x14000", "0x200400",
         "canary-words-4711", "canary-allow-4711", r"project\s+gnarvel")


def private_project(folder):
    """A made-up private project's files and a public tree with a history that leaks some of it."""
    private = folder / "private"
    private.mkdir()
    (private / "functions.tsv").write_text("address\tname\tbytes\n0x12340\tzorblax_widget_init\t40\n"
                                           "0x13800\tquuxify_frobnicator\t12\n0x22000\tblorpt_sensor_poll\t64\n"
                                           "0x12400\tmain\t8\n0x2fe000\tgnarvel_task\t96\n")
    (private / "symbols.txt").write_text("f 0x14000 vexilla_handler\nd 0x200400 g_wibble_state\n")
    (private / "words.txt").write_text("# canary-words-4711\nflorpcorp\nproject\\s+gnarvel\n")
    (private / "allow.txt").write_text("# canary-allow-4711\ndocs/notes.md:0x12400\n")
    tree = folder / "tree"
    (tree / "docs").mkdir(parents=True)
    (tree / "src").mkdir()
    (tree / "src" / "a.c").write_text("/* a driver */\nvoid zorblax_widget_init(void);\nint x = 0x22000;\n")
    (tree / "README.md").write_text("# Gadget\n\nMade for FlorpCorp.\n")
    (tree / "docs" / "notes.md").write_text("Notes\n\nThe table at 0x12400 is a coincidence.\nblorpt_sensor_poll reads it,\n"
                                            "zorblax_widget_init starts it.\n")
    git = ["git", "-C", str(tree), "-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "first: gnarvel_task runs here"], check=True)
    return private, tree


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("DREAD_TARGET", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    private, tree = private_project(home)
    return home, private, tree


def values(history=True):
    return {"tree": "tree", "tables": ["private/functions.tsv", "private/symbols.txt"], "words": "private/words.txt",
            "allow": "private/allow.txt", "history": history, "min": 6}


def wait(srv, number, timeout=120):
    deadline = time.time() + timeout
    while True:
        r = answer(srv, "/api/leakcheck/job?id=%d" % number)[1]
        if r["job"]["state"] not in jobs_.LIVE:
            return r
        assert time.time() < deadline, r
        time.sleep(0.1)


def test_a_check_as_a_job_of_the_page_s_own(project, tmp_path):
    home, private, tree = project
    target = gizmo_copy(tmp_path)
    srv = serving(target)
    srv.home = str(home)                    # (typed paths start where dread gui started: here)
    answers = []

    def got(path, body=None, **kw):
        status, data = answer(srv, path, body, **kw)
        answers.append(json.dumps(data))
        return status, data
    try:
        g = got("/api/gui")[1]
        assert {"name": "leakcheck", "script": "leakcheck.js", "style": "leakcheck.css"} in g["pages"]
        d = got("/api/leakcheck")[1]
        assert d["checks"] == [] and d["last"] is None and d["tree"] == jobs_.ROOT and d["changes"] is None
        assert d["where"] == str(tmp_path / "state" / "dread" / "gui" / "leakcheck")
        line = got("/api/leakcheck/line?" + urllib.parse.urlencode({"values": json.dumps(values())}))[1]
        assert line["error"] is None and line["missing"] == [] and "--history" in line["args"]
        assert line["args"][:4] == ["--tree", str(tree), "--tables", str(private / "functions.tsv")]
        assert line["args"][-1] == os.path.join(page.checks_dir(), line["key"] + ".json")
        missing = got("/api/leakcheck/line?" + urllib.parse.urlencode({"values": json.dumps(dict(values(), words="nope.txt"))}))[1]
        assert missing["missing"] == [str(home / "nope.txt")]
        assert got("/api/leakcheck/run", {"values": dict(values(), words="nope.txt")})[0] == 400
        assert got("/api/leakcheck/run", {"values": values()}, token=False)[0] == 403
        assert "which tables" in got("/api/leakcheck/line?values=%7B%7D")[1]["error"]
        status, job = got("/api/leakcheck/run", {"values": values()})
        assert status == 200 and job["key"] == line["key"], job
        ended = wait(srv, job["id"])
        answers.append(json.dumps(ended))
        assert ended["job"]["state"] == "failed" and ended["job"]["exit"] == 1     # (a hit not allowed)
        assert "HIT src/a.c: zorblax_widget_init" in ended["log"]["text"]
        d = got("/api/leakcheck")[1]
        c, = d["checks"]
        assert c["key"] == job["key"] and c["not_allowed"] >= 6 and c["allowed"] == 1 and c["hits"] == 6
        assert c["tables"] == [str(private / "functions.tsv"), str(private / "symbols.txt")]
        assert "zorblax_widget_init" not in json.dumps(c) and "florpcorp" not in json.dumps(c)     # (counts, no hit)
        assert d["last"]["tree"] == str(tree) and d["last"]["history"] is True
        check = got("/api/leakcheck/check?key=" + job["key"])[1]
        hits = {(h["file"], h["hit"]): h for h in check["hits"]}
        assert hits[("docs/notes.md", "zorblax_widget_init")]["lines"] == [5]
        assert hits[("src/a.c", "0x22000")]["lines"] == [3] and hits[("README.md", "florpcorp")]["lines"] == [3]
        assert hits[("docs/notes.md", "0x12400")]["allowed"] is True
        assert {h["hit"] for h in check["history_hits"]} >= {"gnarvel_task", "zorblax_widget_init"}
        assert check["authors"] == ["Test <test@example.invalid>"] and check["job"]["id"] == job["id"]
        assert got("/api/leakcheck/check?key=../x")[0] == 404
        status, again = got("/api/leakcheck/run", {"values": values()})       # (the same check: its record replaced)
        assert status == 200 and again["key"] == job["key"]
        wait(srv, again["id"])
        assert len(got("/api/leakcheck")[1]["checks"]) == 1
        for path in ("/", "/leakcheck.js", "/leakcheck.css"):
            answers.append(request(srv, path)[2].decode())
        for text in answers:
            for never in NEVER:
                assert never.lower() not in text.lower(), (never, text[:300])
        jobs_dir = tmp_path / "state" / "dread" / "gui" / "leakcheck" / "jobs"
        assert sorted(os.path.basename(p) for p in glob.glob(str(jobs_dir / "*.json"))) == ["1.json", "2.json"]
        for p in glob.glob(os.path.join(target.build, "gui", "jobs", "*")):          # (nothing in the target's)
            assert "functions.tsv" not in open(p, errors="replace").read(), p
        record = os.path.join(page.checks_dir(), job["key"] + ".json")
        assert oct(os.stat(record).st_mode & 0o777) == "0o600"
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_read_only_server_checks_nothing(project):
    home, private, tree = project
    srv = gui.serve(None, None, port=0, home=str(home), read_only=True)
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        d = answer(srv, "/api/leakcheck")[1]
        assert "read-only" in d["changes"]
        assert answer(srv, "/api/leakcheck/run", {"values": values()})[0] == 403
        assert answer(srv, "/api/leakcheck/stop", {"id": 1})[0] == 403
    finally:
        srv.shutdown()
        srv.server_close()
