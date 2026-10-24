"""dread gui's pilot page on a copy of gizmo, with a fake provider: Anthropic's Messages API and its
Message Batches played on 127.0.0.1 (FakeAnthropic; no network, its key a made-up one), reached by the
dread pilot the page starts as a job through llm.toml ($DREAD_LLM) and the usage ledger ($XDG_STATE_HOME)
of the test's own. A run started from the page, its events followed from an offset while its batch is out,
stopped (the batch cancelled, the end "stopped"); a run to its end (its names in the function pages);
the history, the month's costs, the estimate from the run before, the comparisons; no key in any answer;
a read-only server."""
import json
import os
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dread import llm
from dread import jobs as jobs_
from test_gui import answer, gizmo_copy, serving

NAMES = ["led_on", "led_off", "beep_on", "beep_off", "gpio_set"]
KEY = "fake-key-for-the-tests-0123456789"     # (made up; the page must never show it)


class FakeAnthropic:
    """Anthropic's Messages API and Message Batches, played: /v1/messages answers at once; a batch is in
    progress until its first look, or, with hold, until release() (or a cancel); its results answer each
    request. A question gets "name: NAME_x", "prefix: NAME_" and a sentence (NAME from "The function NAME",
    else "thing": a group's question takes the three lines too), 300 tokens out and a token in for every
    three characters. Records the batches cancelled."""

    def __init__(self, hold=False):
        self.hold, self.batches, self.cancelled, self.lock = hold, {}, [], threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, data, raw=False):
                body = data.encode() if raw else json.dumps(data).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"] or 0)) or b"{}")
                if self.headers.get("x-api-key") != KEY:
                    self.send_response(401)
                    self.end_headers()
                    return
                path = urllib.parse.urlsplit(self.path).path
                if path == "/v1/messages":
                    return self.reply(fake.message(body))
                if path == "/v1/messages/batches":
                    return self.reply(fake.submit(body["requests"]))
                m = re.fullmatch(r"/v1/messages/batches/([\w-]+)/cancel", path)
                if m:
                    return self.reply(fake.cancel(m.group(1)))
                self.send_response(404)
                self.end_headers()

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                m = re.fullmatch(r"/v1/messages/batches/([\w-]+)(/results)?", path)
                if m and m.group(2):
                    return self.reply(fake.results(m.group(1)), raw=True)
                if m:
                    return self.reply(fake.look(m.group(1)))
                if path == "/v1/messages/batches":
                    return self.reply({"data": [fake.state(b) for b in fake.batches]})
                self.send_response(404)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_port

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(30)

    @staticmethod
    def message(params):
        text = params.get("system", "") + "".join(
            llm.content_text(m["content"]) if isinstance(m["content"], str) else
            "".join(b.get("text", "") for b in m["content"]) for m in params["messages"])
        found = re.search(r"The function (\w+)", text)
        name = found.group(1) if found else "thing"
        return {"content": [{"type": "text", "text": "name: %s_x\nprefix: %s_\nwhat: does what %s does" % (
                    name, name, name)}],
                "stop_reason": "end_turn", "usage": {"input_tokens": len(text) // 3 + 1, "output_tokens": 300}}

    def submit(self, requests):
        with self.lock:
            bid = "msgbatch_%d" % (len(self.batches) + 1)
            self.batches[bid] = {"requests": requests, "status": "in_progress", "canceled": False, "looks": 0}
        return self.state(bid)

    def state(self, bid):
        b = self.batches[bid]
        n = len(b["requests"])
        ended = b["status"] == "ended"
        return {"id": bid, "type": "message_batch", "processing_status": b["status"],
                "request_counts": {"processing": 0 if ended else n, "succeeded": n if ended and not b["canceled"] else 0,
                                   "errored": 0, "canceled": n if b["canceled"] else 0, "expired": 0},
                "created_at": "2026-10-24T00:00:00Z", "results_url": None}

    def look(self, bid):
        with self.lock:
            b = self.batches[bid]
            b["looks"] += 1
            if not self.hold and b["status"] == "in_progress":
                b["status"] = "ended"
        return self.state(bid)

    def release(self):
        with self.lock:
            self.hold = False

    def cancel(self, bid):
        with self.lock:
            b = self.batches[bid]
            if b["status"] != "ended":
                b.update(status="ended", canceled=True)
                self.cancelled.append(bid)
        return dict(self.state(bid), processing_status="canceling")

    def results(self, bid):
        b = self.batches[bid]
        lines = []
        for r in b["requests"]:
            result = {"type": "canceled"} if b["canceled"] else {"type": "succeeded", "message": self.message(r["params"])}
            lines.append(json.dumps({"custom_id": r["custom_id"], "result": result}))
        return "\n".join(lines) + "\n"


def serving_fake(tmp_path, monkeypatch):
    """(server, target, fake provider), yielded: the page's server on a gizmo copy whose jobs reach
    FakeAnthropic through an llm.toml of the test's own, with a usage ledger of its own (the jobs':
    $XDG_STATE_HOME; the server's: llm.USAGE). The fixture pilot_page here and in the browser's tests."""
    fake = FakeAnthropic(hold=True)
    config = tmp_path / "llm.toml"
    config.write_text('default = "fake"\n\n[providers.fake]\nkind = "anthropic"\nmodel = "fake-model"\nurl = "%s"\n'
                      'key_env = "DREAD_FAKE_KEY"\nprice_in = 1.0\nprice_out = 5.0\nbatch_poll = 0.2\n'
                      'max_tokens = 1000\n\n[providers.local]\nkind = "openai"\nurl = "http://127.0.0.1:9"\n'
                      'model = "nothing"\n' % fake.url)
    monkeypatch.setenv("DREAD_LLM", str(config))
    monkeypatch.setenv("DREAD_FAKE_KEY", KEY)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(llm, "USAGE", str(tmp_path / "state" / "dread" / "llm-usage.tsv"))
    target = gizmo_copy(tmp_path)
    srv = serving(target)
    try:
        yield srv, target, fake
    finally:
        srv.shutdown()
        srv.server_close()
        fake.close()


@pytest.fixture
def pilot_page(tmp_path, monkeypatch):
    yield from serving_fake(tmp_path, monkeypatch)


def values(**more):
    v = {"--task": "name", "--provider": "fake", "--samples": "2", "--passes": "1", "--subsystems": False,
         "--batch": "1", "--jobs": "2", "names": " ".join(NAMES)}
    v.update(more)
    return v


def until(what, check, timeout=240):
    deadline = time.time() + timeout
    while True:
        got = check()
        if got:
            return got
        assert time.time() < deadline, "waited %d s for %s" % (timeout, what)
        time.sleep(0.1)


def run_of_job(srv, number):
    def check():
        status, d = answer(srv, "/api/pilot/job?id=%d" % number)
        assert status == 200, d
        assert d["run"] or d["job"]["state"] in jobs_.LIVE, d["tail"]
        return d["run"]
    return until("the run of job %d" % number, check)


def follow(srv, run, start=None):
    status, d = answer(srv, "/api/pilot/follow?run=%s%s" % (run, "" if start is None else "&from=%d" % start))
    assert status == 200, d
    return d


def test_a_run_started_followed_and_stopped(pilot_page):
    """A naming run started from the page's form: its job, its run found by its process, followed from
    offsets while its Message Batch is out (the stage, the batch waiting and its age, the spend), stopped
    from the page: SIGTERM to its job, the batch cancelled at the provider, the end "stopped"."""
    srv, target, fake = pilot_page
    status, d = answer(srv, "/api/pilot")
    assert status == 200 and d["runnable"] and d["grace"] >= 60 and d["defaults"] == {"--target": "."}
    by = {p["name"]: p for p in d["providers"]["providers"]}
    assert by["fake"]["prices"] == {"in": 1.0, "out": 5.0, "cache_read": 0.1, "cache_write": 1.25, "batch": 0.5}
    assert by["fake"]["key"] == {"needed": True, "set": True, "how": "env DREAD_FAKE_KEY", "problem": None}
    assert by["local"]["prices"] is None and by["local"]["batches"] is False and d["providers"]["default"] == "fake"
    assert {"name", "readable", "review"} <= {t["name"] for t in d["tasks"]} and d["fields"]["--samples"]["default"]
    assert [t for t in d["tasks"] if t["name"] == "review"][0]["budget"] is False
    status, e = answer(srv, "/api/pilot/estimate", {"values": values()})
    assert status == 200 and e["asked"] == len(NAMES) and e["cost"] is None, e
    assert e["words"].startswith("no estimate: no earlier run") and e["line"].endswith("--dry-run")
    status, bad = answer(srv, "/api/pilot/start", {"values": values(**{"--samples": "x"})})
    assert status == 400 and bad["field"] == "--samples"
    assert answer(srv, "/api/pilot/start", {"values": values(**{"--list": True})})[0] == 400
    status, job = answer(srv, "/api/pilot/start", {"values": values()})
    assert status == 200 and job["command"] == "pilot" and job["grace"] >= 60, job
    assert "--provider fake" in job["line"] and "--task name" in job["line"]
    run = run_of_job(srv, job["id"])
    first = follow(srv, run)
    assert first["job"]["id"] == job["id"] and first["state"]["task"] == "name" and first["state"]["provider"] == "fake"
    offset, seen = first["next"], list(first["events"])

    def batch_out():
        nonlocal offset
        d = follow(srv, run, offset)
        assert d["next"] >= offset
        offset = d["next"]
        seen.extend(d["events"])
        return d if d["state"]["waiting"] else None
    d = until("a batch out", batch_out)
    assert d["state"]["stage"] == "pass 1" and d["state"]["status"] == "running" and d["stop"]["can"], d["state"]
    waiting = d["state"]["waiting"][0]
    assert waiting["requests"] > 0 and waiting["age"] >= 0 and waiting["id"] in fake.batches
    assert any(e["event"] == "stage" and e["state"] == "start" for e in seen)
    assert any(e["event"] == "batch" and e["state"] == "submitted" for e in seen)
    again = follow(srv, run, offset)                    # (nothing new: nothing sent)
    assert again["events"] == [] and again["next"] == offset
    status, stopping = answer(srv, "/api/pilot/stop", {"run": run})
    assert status == 200 and stopping["state"] == "stopping", stopping

    def ended():
        nonlocal offset
        d = follow(srv, run, offset)
        offset = d["next"]
        seen.extend(d["events"])
        return d if d["state"]["status"] == "ended" else None
    d = until("the end", ended)
    assert d["state"]["how"] == "stopped" and d["state"]["error"] == "ended by SIGTERM", d["state"]
    assert fake.cancelled == [waiting["id"]] and seen[-1]["event"] == "end" and seen[-1]["how"] == "stopped"
    assert any(e["event"] == "batch" and e["state"] == "cancelled" for e in seen)
    assert not d["stop"]["can"] and d["state"]["waiting"] == []
    record = until("the job's end", lambda: (lambda r: r if r["state"] not in jobs_.LIVE else None)(
        answer(srv, "/api/job?id=%d" % job["id"])[1]))
    assert record["state"] == "stopped"
    assert answer(srv, "/api/pilot/stop", {"run": run})[0] == 409
    whole = follow(srv, run, 0)                          # (from the start: the newest events, none older left out)
    assert whole["dropped"] == 0 and [e["event"] for e in whole["events"]] == [e["event"] for e in seen]
    assert answer(srv, "/api/pilot/follow?run=../../etc")[0] == 404


def test_a_run_to_its_end_the_history_the_costs_and_the_estimate(pilot_page):
    """A run that ends: its names settled on the list and in the function pages (the server reloads
    names.tsv), the history with how each run ended, what it cost and named, the month's costs by
    provider (the Message Batches' share), the next run's estimate from it; the comparisons say what
    the pilot can compare; no answer of the page holds the key."""
    srv, target, fake = pilot_page
    fake.release()
    names_before = answer(srv, "/api/status")[1]["names"]
    status, job = answer(srv, "/api/pilot/start", {"values": values()})
    assert status == 200, job
    run = run_of_job(srv, job["id"])
    d = until("the end", lambda: (lambda d: d if d["state"]["status"] == "ended" else None)(follow(srv, run)))
    assert d["state"]["how"] == "done" and d["state"]["answers"] == 2 * len(NAMES), d["state"]
    settled = {e["function"]: e for e in d["events"] if e["event"] == "settled"}
    assert {n: settled[n]["name"] for n in NAMES} == {n: n + "_x" for n in NAMES}
    assert d["state"]["end"]["settled"] == {"proposed": len(NAMES)} and d["state"]["batch_answers"] == 2 * len(NAMES)
    until("the names reloaded", lambda: answer(srv, "/api/status")[1]["names"] != names_before)
    assert answer(srv, "/api/function?name=led_on")[1]["name"]["proposed"] == "led_on_x"
    status, runs = answer(srv, "/api/pilot/runs")
    r = runs["runs"][0]
    assert r["run"] == run and r["how"] == "done" and r["named"] == len(NAMES) and r["job"]["id"] == job["id"]
    assert r["cost"] > 0 and r["provider"] == "fake" and r["seconds"] > 0
    status, u = answer(srv, "/api/pilot/usage")
    assert status == 200 and u["month"] == time.strftime("%Y-%m") and [m["month"] for m in u["months"]] == [u["month"]]
    fake_row = [p for p in u["providers"] if p["name"] == "fake"][0]
    assert fake_row["answers"] == 2 * len(NAMES) and fake_row["batch_share"] == 1.0 and fake_row["saved"] > 0
    assert u["total"] == pytest.approx(r["cost"]) == pytest.approx(fake_row["cost"]) and not u["unpriced"]
    status, e = answer(srv, "/api/pilot/estimate", {"values": values(names="led_on beep_on")})
    assert e["asked"] == 0 and e["already"] == 2                  # (both proposed already)
    status, e = answer(srv, "/api/pilot/estimate", {"values": values(names="uart_putc uart_puts delay")})
    assert e["functions"] == e["asked"] == 3 and e["basis"]["run"] == run and e["cost"] == pytest.approx(
        e["per_function"] * 3) and e["batch_share"] == 1.0, e
    assert e["per_function"] == pytest.approx(r["cost"] / len(NAMES))
    status, e = answer(srv, "/api/pilot/estimate", {"values": values(**{"--samples": "5"})})
    assert e["differ"] == [["samples", 2, 5]] and "settings differ" in e["words"]
    assert answer(srv, "/api/pilot/estimate", {"values": values(names="no_such_function")})[0] == 400
    status, c = answer(srv, "/api/pilot/compare")
    assert c["names"]["exists"] and c["names"]["proposals"] == len(NAMES) and "original/gizmo.elf" in c["truths"]
    assert c["judges"][0]["name"] == "fake"
    status, cmp = answer(srv, "/api/pilot/compare/start", {"mode": "score", "truth": "original/gizmo.elf"})
    assert status == 200 and cmp["line"].startswith("dread pilot --target . --score") and "--task name" in cmp["line"]
    record = until("the comparison", lambda: (lambda r: r if r["state"] not in jobs_.LIVE else None)(
        answer(srv, "/api/job?id=%d" % cmp["id"])[1]))
    log = answer(srv, "/api/job-log?id=%d&from=0" % cmp["id"])[1]["text"]
    assert record["state"] == "done" and "dread pilot --score: %d proposals with a true name" % len(NAMES) in log, log
    assert answer(srv, "/api/pilot/compare/start", {"mode": "against", "truth": "original/gizmo.elf"})[0] == 400
    assert answer(srv, "/api/pilot/compare/start", {"mode": "score", "truth": "no/such.elf"})[0] == 400
    for path in ("/api/pilot", "/api/pilot/runs", "/api/pilot/usage", "/api/pilot/compare",
                 "/api/pilot/follow?run=%s&from=0" % run, "/api/pilot/job?id=%d" % job["id"]):
        status, got = answer(srv, path)
        assert status == 200 and KEY not in json.dumps(got), path
    for name in os.listdir(os.path.join(target.build, "gui", "jobs")):
        assert KEY not in open(os.path.join(target.build, "gui", "jobs", name), errors="replace").read(), name


def test_two_runs_and_what_the_pilot_compares(pilot_page):
    """Two runs of the history: a naming run's names and another task's are not compared (the pilot
    compares function names only), two naming runs of one target are one set of names (one names.tsv)."""
    from dread import guipages, pilotlog
    srv, target, fake = pilot_page
    build = target.build
    for task in ("name", "name", "readable"):
        log = pilotlog.RunLog.start(build, task, "main", "fake", "fake-model", {}, None, 1)
        log.end("done", answers=1, cost=0.001)
    first, second, readable = [r["run"] for r in reversed(pilotlog.runs(build))]
    status, listed = answer(srv, "/api/pilot/runs?limit=2")
    assert [r["run"] for r in listed["runs"]] == [readable, second] and listed["more"] == 1
    assert listed["runs"][0]["settled"] == {} and listed["runs"][0]["job"] is None     # (not started from the page)
    status, c = answer(srv, "/api/pilot/compare?a=%s&b=%s" % (first, second))
    assert c["pair"]["can"] is False and "one set of names" in c["pair"]["why"]
    status, c = answer(srv, "/api/pilot/compare?a=%s&b=%s" % (first, readable))
    assert "--task readable" in c["pair"]["why"] and "variables" in c["pair"]["why"]
    assert guipages.pilot.compare_pair(build, first, "20990101-000000-name-0000")["can"] is False


def test_a_server_without_a_target(tmp_path, monkeypatch):
    """dread gui started where there is no target (the Setup page alone): the pilot page's start says so
    and runs nothing, its routes about runs answer 409 with the way to a target, the cost per month shows
    (the ledger is the machine's, not a target's), and its status has nothing."""
    from dread import gui
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    ledger = tmp_path / "state" / "dread" / "llm-usage.tsv"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("\t".join(llm.USAGE_COLUMNS) + "\n" + time.strftime("%Y-%m-%dT%H:%M:%S") +
                      "\tsomeone\tm\t1000\t100\tend_turn\t0\t0\tbatch\t0\n")
    monkeypatch.setattr(llm, "USAGE", str(ledger))
    monkeypatch.setenv("DREAD_LLM", str(tmp_path / "no-llm.toml"))
    srv = gui.serve(None, None, port=0, home=str(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, d = answer(srv, "/api/pilot")
        assert status == 200 and d["target"] is False and not d["runnable"] and "#/setup" in d["why"]
        assert d["truths"] == [] and d["providers"]["error"].startswith("no provider configuration")
        for path in ("/api/pilot/runs", "/api/pilot/compare", "/api/pilot/follow?run=20261024-120000-name-0000"):
            status, refused = answer(srv, path)
            assert status == 409 and "Setup page" in refused["error"], path
        assert answer(srv, "/api/pilot/start", {"values": values()})[0] == 409
        assert answer(srv, "/api/pilot/estimate", {"values": values()})[0] == 409
        status, u = answer(srv, "/api/pilot/usage")
        assert status == 200 and u["providers"][0]["name"] == "someone" and u["providers"][0]["batch_share"] == 1.0
        assert answer(srv, "/api/status")[1]["pages"]["pilot"] == {"index": None, "live": []}
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_read_only_page_starts_and_stops_nothing(pilot_page):
    from dread import gui
    from dread.target import Target
    srv, target, fake = pilot_page
    viewer = gui.serve(Target.load(target.root), target.image(), port=0, read_only=True)
    threading.Thread(target=viewer.serve_forever, daemon=True).start()
    try:
        status, d = answer(viewer, "/api/pilot")
        assert status == 200 and not d["runnable"] and "read-only" in d["why"]
        assert answer(viewer, "/api/pilot/start", {"values": values()})[0] == 403
        assert answer(viewer, "/api/pilot/stop", {"job": 1})[0] == 403
        assert answer(viewer, "/api/pilot/compare/start", {"mode": "score", "truth": "original/gizmo.elf"})[0] == 403
        status, e = answer(viewer, "/api/pilot/estimate", {"values": values()})     # (asks and changes nothing)
        assert status == 200 and e["asked"] == len(NAMES)
        assert answer(viewer, "/api/pilot/estimate", {"values": values()}, token=False)[0] == 403
    finally:
        viewer.shutdown()
        viewer.server_close()
