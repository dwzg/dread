"""dread gui's Run page and its jobs on a copy of gizmo: the pages registered as modules (their scripts
served, /api/gui), every command's form and the command line a form makes (with the target the page
shows), jobs started and stopped through the page (the token needed, the command's refusal, a command
that does not run as a job), the record and the log, the overview and the list reloaded after a lift
check run from the page, a read-only server and a second server on the same target, and a server
closed with a job running leaving no process behind."""
import json
import os
import sys
import time
import urllib.parse

import pytest

from dread import cli, gui, guipages
from dread import jobs as jobs_
from dread.target import Target
from test_gui import answer, gizmo_copy, needs_gcc, request, serving


@pytest.fixture
def server(tmp_path):
    target = gizmo_copy(tmp_path)
    srv = serving(target)
    yield srv, target
    srv.shutdown()
    srv.server_close()


def quoted(value):
    return urllib.parse.quote(json.dumps(value))


def wait_job(srv, number, timeout=600):
    deadline = time.time() + timeout
    while True:
        status, r = answer(srv, "/api/job?id=%d" % number)
        assert status == 200, r
        if r["state"] not in jobs_.LIVE:
            return r
        assert time.time() < deadline, r
        time.sleep(0.1)


def gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


def test_the_pages_and_their_files(server):
    """dread/guipages' modules register their routes and pages: /api/gui names the pages' files, the
    server serves them, app.js imports them and exports what they use; /api/status carries their share."""
    srv, _ = server
    status, g = answer(srv, "/api/gui")
    assert status == 200 and g["read_only"] is False and g["version"]
    assert {"name": "run", "script": "run.js", "style": "run.css"} in g["pages"]
    assert g["status"]["run"]["running"] == 0 and g["status"]["run"]["limit"] == 1
    for path, kind in (("/run.js", "text/javascript"), ("/run.css", "text/css")):
        status, headers, _ = request(srv, path)
        assert status == 200 and headers["Content-Type"].startswith(kind), path
    script = request(srv, "/run.js")[2].decode()
    assert 'from "./app.js"' in script and "register({" in script and "/api/jobs/start" in script
    app = request(srv, "/app.js")[2].decode()
    assert "export {" in app and "function register(" in app and 'import("/" + p.script)' in app
    assert answer(srv, "/api/status")[1]["pages"]["run"]["queued"] == 0
    assert request(srv, "/nosuch.js")[0] == 404 and request(srv, "/../dread/gui.py")[0] == 404
    assert {("GET", "/api/commands"), ("POST", "/api/jobs/start"), ("POST", "/api/jobs/stop")} <= set(guipages.ROUTES)


def test_the_commands_and_their_forms(server):
    srv, target = server
    status, commands = answer(srv, "/api/commands")
    assert [c["name"] for c in commands] == list(cli.COMMANDS)
    by = {c["name"]: c for c in commands}
    assert by["lift"]["runnable"] and not by["gui"]["runnable"] and "terminal" in by["gui"]["why"]
    status, d = answer(srv, "/api/command?name=lift")
    fields = {f["id"]: f for f in d["form"]["fields"]}
    assert fields["--check"]["kind"] == "flag" and fields["names"]["kind"] == "list"
    assert d["defaults"] == {"--target": "."} and d["cwd"] == target.root and d["runnable"]
    status, line = answer(srv, "/api/command-line?name=lift&values=" + quoted({"--check": True, "names": "clamp beep_on"}))
    assert line["line"] == "dread lift --target . --check clamp beep_on" and line["error"] is None, line
    assert line["args"] == ["--target", ".", "--check", "clamp", "beep_on"] and line["cwd"] == target.root
    status, line = answer(srv, "/api/command-line?name=lift&values=" + quoted({"--target": "../a32"}))
    assert line["args"] == ["--target", "../a32"]                  # (the form's own target is kept)
    status, bad = answer(srv, "/api/command-line?name=pilot&values=" + quoted({"--tries": "x"}))
    assert "invalid int value" in bad["error"] and bad["field"] == "--tries" and bad["args"] is None and bad["line"]
    status, d = answer(srv, "/api/command?name=bench&args=" + quoted(["all", "freertos"]))
    assert d["values"] == {"action": "all", "recipe": "freertos"} and d["defaults"] == {}
    status, d = answer(srv, "/api/command?name=llm")
    assert d["form"]["fields"][0]["kind"] == "subcommands"
    assert answer(srv, "/api/command?name=nosuch")[0] == 404
    assert answer(srv, "/api/command-line?name=lift&values=notjson")[0] == 400


def test_the_target_and_image_a_job_gets(tmp_path):
    """--target is the target the page shows (\".\": the job runs in its directory) and --image the image
    it shows where that is not the target's first, for a command that takes them and where the form leaves
    them empty."""
    from dread.guipages import run
    target = gizmo_copy(tmp_path)
    with open(target.file, "a") as f:
        f.write('\n[image.second]\noriginal = "original/gizmo.bin"\nbase = 0x08000000\n')
    target = Target.load(target.root)

    class Request:
        pass
    request = Request()
    request.target, request.image = target, "second"
    assert run.defaults(request, cli.parser("lift")) == {"--target": ".", "--image": "second"}
    assert run.command_line(request, "lift", {"--check": True})[1] == "dread lift --target . --image second --check"
    request.image = "main"
    assert run.defaults(request, cli.parser("lift")) == {"--target": "."}
    assert run.defaults(request, cli.parser("bench")) == {}
    assert run.command_line(request, "bench", {"action": "report", "recipe": "freertos"})[0] == ["report", "freertos"]
    with pytest.raises(TypeError):
        run.command_line(request, "lift", ["--check"])


def test_a_job_through_the_page(server):
    """A job started from a form: the token needed, the command line with the target, the record and the
    log, the history; the command's refusal and a command that does not run as a job; a stop."""
    srv, target = server
    body = {"command": "info", "values": {}}
    assert request(srv, "/api/jobs/start", body, token=False)[0] == 403
    status, r = answer(srv, "/api/jobs/start", body)
    assert status == 200, r
    assert r["line"] == "dread info --target ." and r["args"] == ["--target", "."] and r["by"] == "dread gui"
    assert r["cwd"] == target.root and r["command"] == "info"
    done = wait_job(srv, r["id"])
    assert done["state"] == "done" and done["exit"] == 0 and done["ended"] >= done["started"]
    status, log = answer(srv, "/api/job-log?id=%d&from=0" % r["id"])
    assert "gizmo-m3" in log["text"] and log["state"] == "done"
    assert os.path.exists(os.path.join(target.build, "gui", "jobs", "%d.json" % r["id"]))
    status, bad = answer(srv, "/api/jobs/start", {"command": "pilot", "values": {"--tries": "x"}})
    assert status == 400 and bad["field"] == "--tries" and "invalid int" in bad["error"]
    assert answer(srv, "/api/jobs/start", {"command": "gui", "values": {}})[0] == 409
    assert answer(srv, "/api/jobs/start", {"command": "nosuch"})[0] == 404
    again = answer(srv, "/api/jobs/start", {"command": "info", "args": r["args"]})[1]
    assert again["line"] == r["line"] and wait_job(srv, again["id"])["state"] == "done"
    sleeper = srv.jobs.start([sys.executable, "-c", "import time; print('up', flush=True); time.sleep(60)"], target.root)
    deadline = time.time() + 30
    while "up" not in answer(srv, "/api/job-log?id=%d&from=0" % sleeper["id"])[1]["text"]:
        assert time.time() < deadline
        time.sleep(0.05)
    assert request(srv, "/api/jobs/stop", {"id": sleeper["id"]}, token=False)[0] == 403
    status, stopping = answer(srv, "/api/jobs/stop", {"id": sleeper["id"]})
    assert status == 200 and stopping["state"] == "stopping"
    assert wait_job(srv, sleeper["id"])["state"] == "stopped"
    status, listed = answer(srv, "/api/jobs")
    assert [j["id"] for j in listed["jobs"]] == [sleeper["id"], again["id"], r["id"]] and listed["status"]["running"] == 0
    assert answer(srv, "/api/job?id=999")[0] == 404 and answer(srv, "/api/job?id=x")[0] == 400


@needs_gcc
def test_a_lift_check_from_the_page_reloads_the_views(server):
    """The tables a job changes reload the page's state as a run in a terminal does: after a lift check
    from the Run page, /api/status has a new generation and the overview shows its verdicts."""
    srv, target = server
    before = answer(srv, "/api/status")[1]["generation"]
    o = answer(srv, "/api/overview")[1]
    assert o["runs"]["lift_check"] is None and o["refused"] == []
    status, r = answer(srv, "/api/jobs/start", {"command": "lift", "values": {"--check": True}})
    assert status == 200 and r["line"] == "dread lift --target . --check", r
    done = wait_job(srv, r["id"])
    assert done["state"] == "done", answer(srv, "/api/job-log?id=%d&from=0" % r["id"])[1]["text"]
    assert answer(srv, "/api/status")[1]["generation"] > before
    o = answer(srv, "/api/overview")[1]
    assert o["runs"]["lift_check"] and any("ram_crc" in g["functions"] for g in o["refused"]), o["refused"]
    by = {f["name"]: f for f in answer(srv, "/api/functions")[1]}
    assert by["ram_crc"]["origin"] == "refused"


def test_read_only_and_a_second_server(server, tmp_path):
    """dread gui --read-only changes nothing and runs nothing, but shows the jobs; a second server on the
    same target shows them too and starts none (the first holds the jobs); a server listening beyond this
    machine runs no jobs."""
    srv, target = server
    r = answer(srv, "/api/jobs/start", {"command": "info", "values": {}})[1]
    wait_job(srv, r["id"])
    second = gui.serve(target, target.image(), port=0)
    import threading
    threading.Thread(target=second.serve_forever, daemon=True).start()
    try:
        status, refused = answer(second, "/api/jobs/start", {"command": "info", "values": {}})
        assert status == 409 and "another dread gui" in refused["error"]
        assert not {c["name"]: c for c in answer(second, "/api/commands")[1]}["info"]["runnable"]
        assert [j["id"] for j in answer(second, "/api/jobs")[1]["jobs"]] == [r["id"]]
    finally:
        second.shutdown()
        second.server_close()
    remote = gui.serve(Target.load(target.root), target.image(), host="0.0.0.0", port=0)
    try:                                                # (whoever can load the page has its token)
        assert "127.0.0.1" in remote.jobs.owner
        with pytest.raises(jobs_.JobsError):
            remote.jobs.start([sys.executable, "-c", "pass"], target.root)
    finally:
        remote.server_close()
    viewer = gui.serve(Target.load(target.root), target.image(), port=0, read_only=True)
    threading.Thread(target=viewer.serve_forever, daemon=True).start()
    try:
        assert answer(viewer, "/api/gui")[1]["read_only"] is True
        status, refused = answer(viewer, "/api/jobs/start", {"command": "info", "values": {}})
        assert status == 403 and "read-only" in refused["error"]
        assert answer(viewer, "/api/rename", {"old": "beep_on", "new": "x"})[0] == 403
        assert answer(viewer, "/api/annotate", {"address": 0, "text": "x"})[0] == 403
        assert "read-only" in {c["name"]: c for c in answer(viewer, "/api/commands")[1]}["info"]["why"]
        assert [j["id"] for j in answer(viewer, "/api/jobs")[1]["jobs"]] == [r["id"]]
        assert "gizmo-m3" in answer(viewer, "/api/job-log?id=%d" % r["id"])[1]["text"]
        assert answer(viewer, "/api/command-line?name=lift&values=" + quoted({"--check": True}))[1]["line"]
    finally:
        viewer.shutdown()
        viewer.server_close()


def test_closing_the_server_stops_its_jobs(tmp_path):
    target = gizmo_copy(tmp_path)
    srv = serving(target)
    try:
        r = srv.jobs.start([sys.executable, "-c", "import time; print('up', flush=True); time.sleep(60)"], target.root)
        deadline = time.time() + 30
        while "up" not in srv.jobs.log(r["id"], 0)["text"]:
            assert time.time() < deadline
            time.sleep(0.05)
        pid = srv.jobs.get(r["id"])["pid"]
    finally:
        srv.shutdown()
        srv.server_close()
    deadline = time.time() + 30
    while not gone(pid):
        assert time.time() < deadline
        time.sleep(0.05)
    record = json.load(open(os.path.join(gui.jobs_dir(target), "%d.json" % r["id"])))
    assert record["state"] == "stopped" and record["why"] == "the server stopped"
