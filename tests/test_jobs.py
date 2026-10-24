"""dread.jobs, the jobs of dread gui: a job's log and record (nothing of the environment kept, a secret
printed by a command not shown), the limit of jobs at a time, a stop that ends the whole process group
(SIGKILL after the grace for one that ignores SIGTERM), politeness, closing, and a new server taking
up what an old one left (a job that outlived it followed again, a lost one marked)."""
import json
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from dread import jobs as jobs_


def python(code):
    return [sys.executable, "-c", textwrap.dedent(code)]


def wait(jobs, number, states=jobs_.LIVE, timeout=60):
    """The record once it has left `states`."""
    deadline = time.time() + timeout
    while True:
        record = jobs.get(number)
        if record["state"] not in states:
            return record
        assert time.time() < deadline, record
        time.sleep(0.05)


def wait_for(predicate, timeout=30):
    deadline = time.time() + timeout
    while not predicate():
        assert time.time() < deadline
        time.sleep(0.05)


def gone(pid):
    """Whether a process no longer runs (gone, or a zombie its new parent has not reaped yet)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


@pytest.fixture
def jobs(tmp_path):
    made = []

    def make(**kw):
        j = jobs_.Jobs(str(tmp_path / "jobs"), **kw)
        made.append(j)
        return j
    yield make
    for j in made:
        assert j.close(timeout=5)


def test_a_job_its_log_and_record(jobs, tmp_path, monkeypatch):
    """The output (stdout and stderr) in N.log, the record with the command line, times and exit status;
    the environment reaches the job but is not kept, and a secret it prints is not shown."""
    monkeypatch.setenv("DREAD_TEST_API_KEY", "s3cret-value-0123456789")
    j = jobs()
    r = j.start(python("""
        import os, sys
        print("hello")
        print("to stderr", file=sys.stderr)
        print("key:", os.environ["DREAD_TEST_API_KEY"])
        sys.exit(3)
    """), str(tmp_path), line="dread x --y", command="x")
    assert r["state"] in ("queued", "running") and r["by"] == "dread gui" and r["line"] == "dread x --y"
    done = wait(j, r["id"])
    assert done["state"] == "failed" and done["exit"] == 3 and done["ended"] >= done["started"] >= done["queued"]
    log = j.log(r["id"], 0)
    assert "hello\n" in log["text"] and "to stderr\n" in log["text"] and log["next"] == log["size"]
    assert "key: [$DREAD_TEST_API_KEY]" in log["text"] and "s3cret" not in log["text"]
    raw = open(os.path.join(j.directory, "%d.log" % r["id"])).read()
    assert "s3cret-value-0123456789" in raw                 # (the job had the key: the server passes it on)
    text = open(os.path.join(j.directory, "%d.json" % r["id"])).read()
    record = json.loads(text)
    assert "s3cret" not in text and "env" not in record
    assert record["argv"][0] == sys.executable and record["cwd"] == str(tmp_path) and record["version"] == jobs_.VERSION
    assert set(record) >= {"id", "command", "argv", "line", "cwd", "by", "state", "queued", "started", "ended", "exit",
                           "pid", "grace", "log"}
    ok = j.start(python("print('fine')"), str(tmp_path))
    assert wait(j, ok["id"])["state"] == "done" and wait(j, ok["id"])["exit"] == 0
    assert [x["id"] for x in j.list()] == [ok["id"], r["id"]]
    with pytest.raises(jobs_.JobsError):
        j.start(python("pass"), str(tmp_path / "nowhere"))


def test_a_secret_in_the_command_line_is_not_kept(jobs, tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_TOKEN", "tok-abcdefgh-123")
    j = jobs()
    r = j.start(python("import sys; print(sys.argv[1])") + ["tok-abcdefgh-123"], str(tmp_path))
    done = wait(j, r["id"])
    assert done["state"] == "done" and done["redacted"] and "[$SOME_TOKEN]" in done["argv"]
    assert "tok-abcdefgh" not in open(os.path.join(j.directory, "%d.json" % r["id"])).read()
    assert "tok-abcdefgh-123" in open(os.path.join(j.directory, "%d.log" % r["id"])).read()   # (it ran the real words)


def test_the_limit_queues_the_rest(jobs, tmp_path):
    j = jobs(limit=1)
    gate = tmp_path / "gate"
    a = j.start(python("""
        import os, time
        while not os.path.exists("gate"):
            time.sleep(0.02)
    """), str(tmp_path))
    b = j.start(python("print('b ran')"), str(tmp_path))
    time.sleep(0.5)
    assert j.get(a["id"])["state"] == "running" and j.get(b["id"])["state"] == "queued"
    assert j.status()["running"] == 1 and j.status()["queued"] == 1
    gate.write_text("")
    assert wait(j, a["id"])["state"] == "done"
    assert wait(j, b["id"])["state"] == "done" and "b ran" in j.log(b["id"])["text"]
    assert j.get(b["id"])["started"] >= j.get(a["id"])["ended"]


def test_a_chain_stops_at_the_first_failure(jobs, tmp_path):
    """A job that waits for another starts once that one is done, and is cancelled where it failed; the
    limit counts only running jobs, so a job waiting for one that waits does not hold up the queue."""
    j = jobs(limit=2)
    first = j.start(python("import time; time.sleep(0.3); print('one')"), str(tmp_path))
    second = j.start(python("print('two')"), str(tmp_path), after=first["id"])
    third = j.start(python("import sys; sys.exit(1)"), str(tmp_path), after=second["id"])
    fourth = j.start(python("print('never')"), str(tmp_path), after=third["id"])
    other = j.start(python("print('free')"), str(tmp_path))
    assert wait(j, other["id"])["state"] == "done"
    assert j.get(second["id"])["state"] in ("queued", "running", "done")
    assert wait(j, second["id"])["started"] >= wait(j, first["id"])["ended"]
    assert wait(j, third["id"])["state"] == "failed"
    done = wait(j, fourth["id"])
    assert done["state"] == "cancelled" and done["started"] is None and done["why"] == "job %d, which it waited " \
        "for, ended failed" % third["id"]
    assert j.get(fourth["id"])["after"] == third["id"]
    with pytest.raises(jobs_.JobsError):
        j.start(python("pass"), str(tmp_path), after=999)


def test_a_stop_ends_the_whole_group(jobs, tmp_path):
    """SIGTERM to the job's process group: the job and what it started end; a queued job is cancelled."""
    j = jobs(limit=1)
    r = j.start(python("""
        import subprocess, time
        child = subprocess.Popen(["sleep", "60"])
        open("child", "w").write(str(child.pid))
        time.sleep(60)
    """), str(tmp_path))
    queued = j.start(python("print('never')"), str(tmp_path))
    wait_for(lambda: (tmp_path / "child").exists() and (tmp_path / "child").read_text())
    child = int((tmp_path / "child").read_text())
    leader = j.get(r["id"])["pid"]
    assert j.stop(queued["id"])["state"] == "cancelled"
    assert j.stop(r["id"])["state"] == "stopping"
    done = wait(j, r["id"])
    assert done["state"] == "stopped" and done["exit"] == -signal.SIGTERM and done["why"] == "stopped from the page"
    wait_for(lambda: gone(child) and gone(leader))
    assert j.get(queued["id"])["started"] is None and j.log(queued["id"])["text"] == ""


def test_sigkill_after_the_grace(jobs, tmp_path):
    j = jobs()
    r = j.start(python("""
        import signal, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print("ready", flush=True)
        time.sleep(60)
    """), str(tmp_path), grace=1)
    wait_for(lambda: "ready" in j.log(r["id"], 0)["text"])
    started = time.time()
    j.stop(r["id"])
    done = wait(j, r["id"])
    assert done["state"] == "stopped" and done["exit"] == -signal.SIGKILL and 0.9 < time.time() - started < 20


def test_jobs_run_polite(jobs, tmp_path, monkeypatch):
    """The lowest priority, applied before the job's own code runs, and DREAD_POLITE=1 in its environment.
    (A tool run in this process before may have made it polite and set DREAD_POLITE: the server's
    environment is the jobs', so it is taken out here.)"""
    monkeypatch.delenv("DREAD_NICE", raising=False)
    monkeypatch.delenv("DREAD_NICE_LEVEL", raising=False)
    monkeypatch.delenv("DREAD_POLITE", raising=False)
    j = jobs()
    r = j.start(python("""
        import os, time
        while not os.path.exists("gate"):
            time.sleep(0.02)
        print(os.nice(0), os.environ.get("DREAD_POLITE"), os.environ.get("PYTHONUNBUFFERED"))
    """), str(tmp_path))
    (tmp_path / "gate").write_text("")
    done = wait(j, r["id"])
    assert j.log(r["id"])["text"].split() == ["19", "1", "1"] and done["nice"] == 19
    monkeypatch.setenv("DREAD_NICE", "0")
    os.remove(tmp_path / "gate")
    r = j.start(python("""
        import os
        print(os.nice(0) == %d, os.environ.get("DREAD_POLITE"))
    """ % os.nice(0)), str(tmp_path))
    wait(j, r["id"])
    assert j.log(r["id"])["text"].split() == ["True", "None"] and j.get(r["id"])["nice"] is None


def test_closing_stops_the_jobs(tmp_path):
    j = jobs_.Jobs(str(tmp_path / "jobs"), limit=1)
    r = j.start(python("import time; print('up', flush=True); time.sleep(60)"), str(tmp_path))
    q = j.start(python("print('never')"), str(tmp_path))
    wait_for(lambda: "up" in j.log(r["id"], 0)["text"])
    pid = j.get(r["id"])["pid"]
    assert j.close(timeout=5)
    wait_for(lambda: gone(pid))
    records = {x["id"]: x for x in jobs_.Jobs(str(tmp_path / "jobs"), read_only=True).list()}
    assert records[r["id"]]["state"] == "stopped" and records[r["id"]]["why"] == "the server stopped"
    assert records[q["id"]]["state"] == "cancelled"
    assert j.close()                                        # (twice is fine)


SERVER = """
import sys, time
sys.path.insert(0, %r)
from dread import jobs
j = jobs.Jobs(%r)
r = j.start([sys.executable, "-c", "import time; print('working', flush=True); time.sleep(120)"], %r)
while "working" not in j.log(r["id"], 0)["text"]:
    time.sleep(0.05)
print(r["id"], j.get(r["id"])["pid"], flush=True)
import os
os._exit(0)                 # (a server killed: no close)
"""


def test_a_new_server_takes_up_what_the_last_left(jobs, tmp_path):
    """A job that outlived its server is followed again (and can be stopped), a queued one cancelled, a
    running one whose process is gone marked lost; the numbers go on."""
    directory = str(tmp_path / "jobs")
    out = subprocess.run([sys.executable, "-c", SERVER % (jobs_.ROOT, directory, str(tmp_path))],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    number, pid = map(int, out.stdout.split())
    try:
        assert not gone(pid)
        dead = subprocess.Popen(["true"])
        dead.wait()
        with open(os.path.join(directory, "%d.json" % (number + 1)), "w") as f:
            json.dump({"id": number + 1, "state": "running", "pid": dead.pid, "proc": [1, "x"], "argv": ["true"],
                       "line": "true", "log": "%d.log" % (number + 1)}, f)
        with open(os.path.join(directory, "%d.json" % (number + 2)), "w") as f:
            json.dump({"id": number + 2, "state": "queued", "argv": ["true"], "line": "true",
                       "log": "%d.log" % (number + 2)}, f)
        j = jobs()
        followed = j.get(number)
        assert followed["state"] == "running" and followed["adopted"] and "outlived" in followed["why"]
        assert j.get(number + 1)["state"] == "lost" and "killed or crashed" in j.get(number + 1)["why"]
        assert j.get(number + 2)["state"] == "cancelled"
        assert j.status()["running"] == 1
        j.stop(number)
        done = wait(j, number)
        assert done["state"] == "stopped" and done["exit"] is None
        wait_for(lambda: gone(pid))
        r = j.start(python("pass"), str(tmp_path))
        assert r["id"] == number + 3
    finally:
        if not gone(pid):
            os.killpg(pid, signal.SIGKILL)


def test_one_server_runs_the_jobs_of_a_directory(jobs, tmp_path):
    """A second Jobs on the same directory (another server) and a read-only one show the records and
    start nothing; a read-only one writes nothing."""
    first = jobs()
    r = first.start(python("print('one')"), str(tmp_path))
    wait(first, r["id"])
    second = jobs()
    assert second.owner and "another dread gui" in second.owner
    with pytest.raises(jobs_.JobsError):
        second.start(python("pass"), str(tmp_path))
    with pytest.raises(jobs_.JobsError):
        second.stop(r["id"])
    assert [x["id"] for x in second.list()] == [r["id"]] and "one" in second.log(r["id"])["text"]
    before = sorted(os.listdir(first.directory))
    viewer = jobs_.Jobs(first.directory, read_only=True)
    assert viewer.owner.startswith("this page is read-only") and viewer.get(r["id"])["state"] == "done"
    assert viewer.status()["running"] == 0 and sorted(os.listdir(first.directory)) == before
    assert not os.path.exists(tmp_path / "other") and jobs_.Jobs(str(tmp_path / "other"), read_only=True).list() == []
    assert not os.path.exists(tmp_path / "other")


def test_the_log_in_pieces(tmp_path, monkeypatch):
    """From an offset; the first look its last TAIL bytes from a line's start; while the job runs whole
    lines only (a character cut by the end of a piece waits too); everything once it has ended."""
    directory = tmp_path / "jobs"
    directory.mkdir()
    (directory / "1.log").write_bytes("line one\nline two\nhalf ä".encode()[:-1])
    record = {"id": 1, "state": "running", "argv": ["x"], "line": "x", "log": "1.log"}
    (directory / "1.json").write_text(json.dumps(record))
    j = jobs_.Jobs(str(directory), read_only=True)
    got = j.log(1, 0)
    assert got["text"] == "line one\nline two\n" and got["next"] == 18 and got["more"]
    assert j.log(1, 18)["text"] == "" and j.log(1, 18)["next"] == 18      # (the line is still being written)
    monkeypatch.setattr(jobs_, "TAIL", 15)
    assert j.log(1)["start"] == 9 and j.log(1)["text"] == "line two\n"
    monkeypatch.setattr(jobs_, "TAIL", 12)
    assert j.log(1)["start"] == 18 and j.log(1)["text"] == ""             # (from the next line's start)
    (directory / "1.json").write_text(json.dumps(dict(record, state="done")))
    got = j.log(1, 18)
    assert got["text"] == "half �" and not got["more"]
    piece = j.log(1, 0, limit=5)                            # (a line longer than a piece comes in pieces)
    assert piece["text"] == "line " and piece["next"] == 5 and piece["more"]


def test_the_command_of_a_dread_job():
    argv, line, env = jobs_.command("lift", ["--check", "a b"])
    assert argv == [sys.executable, "-m", "dread.cli", "lift", "--check", "a b"]
    assert line == "dread lift --check 'a b'" and env["PYTHONPATH"].split(os.pathsep)[0] == jobs_.ROOT
    assert os.path.isfile(os.path.join(jobs_.ROOT, "dread", "cli.py"))
