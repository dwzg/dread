"""dread pilot's runs as events (dread.pilotlog) and its budget (--budget, dread.llm Meter), with a played
provider on copies of gizmo: a run writes its events and dread pilot --status reads them; a run with a
budget below its cost stops there with whole answers in its tables, and a run started again ends with the
tables of a run that was never stopped; a run ended by SIGTERM says so; the events file stays whole when
threads and runs write at once."""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

import pytest

from dread import llm, pilot, pilotlog, regflow
from dread.program import Program
from dread.target import Target
from conftest import GIZMO

PRICES = {"price_in": 1.0, "price_out": 5.0, "price_batch": 0.5}    # (US dollars per million tokens)
NAMES = ["led_on", "led_off", "beep_on", "beep_off", "gpio_set", "uart_putc", "uart_puts", "delay"]


def copy_of(path):
    """(target, image, program, flow) of a fresh copy of gizmo's m3 build at path."""
    shutil.copytree(GIZMO, path, ignore=shutil.ignore_patterns("build"))
    target = Target.load(str(path / "m3"))
    image = target.image()
    program = Program(target, image)
    return target, image, program, regflow.load(target, image, program)


def answering(system):
    """A played model for the name questions: each function named after itself (with _shown when its
    question shows a callee's proposal), 300 tokens out and as many in as the question's text has at
    three characters a token (the estimate's way: so the spend stays within what the meter allowed)."""
    def answer(messages):
        text = "".join(llm.content_text(m["content"]) for m in messages)
        name = re.search(r"The function (\w+)", llm.content_text(messages[0]["content"])).group(1)
        shown = "_shown" if re.search(r"\b\w+_x(_shown)?\(", text) else ""
        return {"text": "name: %s_x%s\nwhat: does what %s does" % (name, shown, name),
                "input": -(-(len(system) + len(text)) // 3), "tokens": 300}
    return answer


class Collected:
    """A stand-in for a run's RunLog (pilotlog.current) that keeps its events in a list."""

    def __init__(self, events):
        self.events = events

    def event(self, kind, **fields):
        self.events.append((kind, fields))


def provider_loader(made, **extra):
    def load(name=None, path=None):
        made.append(llm.Scripted(answering(pilot.NAME_SYSTEM), name="played", prices=PRICES, **extra))
        return made[-1]
    return load


def name_run(target, *options):
    return pilot.main(["--target", target.root, "--task", "name", "--no-subsystems", "--samples", "2",
                       "--passes", "1", "--no-device", "--jobs", "2"] + list(options) + NAMES)


def last_proposals(target, image):
    return {r["now"]: (r["proposed"], r["samples"], r["confidence"])
            for r in pilot.last_rows(pilot.read_name_rows(pilot.names_path(target, image))).values()}


def test_a_run_writes_its_events_and_the_status_reads_them(tmp_path, monkeypatch, capsys):
    """A naming run's events: its start (task, provider, settings, the functions planned), its stage
    with the questions planned, each answer (tokens, cost, the spend so far, its estimate), what the
    pilot made of each (the name it gave, the answer's number), each function settled, and its end
    (done, its totals); the index has the run; --status, --list and pilotlog.state read them; a second
    run's first estimates come from the first's answers."""
    target, image, program, flow = copy_of(tmp_path / "gizmo")
    made = []
    monkeypatch.setattr(llm, "load", provider_loader(made))
    assert name_run(target) == 0
    runs = pilotlog.runs(target.build)
    assert len(runs) == 1 and runs[0]["status"] == "ended" and runs[0]["how"] == "done", runs
    events, offset = pilotlog.events(target.build, runs[0]["run"])
    assert offset == os.path.getsize(pilotlog.events_path(target.build, runs[0]["run"]))
    assert pilotlog.events(target.build, runs[0]["run"], offset) == ([], offset)
    start, end = events[0], events[-1]
    assert (start["event"], end["event"]) == ("start", "end") and start["format"] == pilotlog.FORMAT
    assert start["task"] == "name" and start["provider"] == "played" and start["functions"] == len(NAMES)
    assert start["settings"]["samples"] == 2 and start["pid"] == os.getpid() and "budget" not in start
    stages = [e for e in events if e["event"] == "stage"]
    assert [(s["stage"], s["state"]) for s in stages] == [("pass 1", "start"), ("pass 1", "end")]
    assert stages[0]["questions"] == 2 * len(NAMES) and stages[1]["proposed"] == len(NAMES)
    answers = [e for e in events if e["event"] == "answer"]
    outcomes = [e for e in events if e["event"] == "outcome"]
    assert len(answers) == len(outcomes) == 2 * len(NAMES)
    assert {o["n"] for o in outcomes} == {a["n"] for a in answers} == set(range(1, 2 * len(NAMES) + 1))
    assert all(o["result"] == "accepted" and o["stage"] == "pass 1" and o["cost"] > 0 for o in outcomes)
    led = [o for o in outcomes if o["function"] == "led_on"]
    assert [o["name"] for o in led] == ["led_on_x_shown"] * 2           # (gpio_set's proposal shown)
    assert all(a["estimate"] > 0 and a["turn"] == 1 and a["output"] == 300 for a in answers)
    assert answers[-1]["spent"] == pytest.approx(sum(a["cost"] for a in answers))
    settled = [e for e in events if e["event"] == "settled"]
    assert sorted(s["function"] for s in settled) == sorted(NAMES) and settled[0]["confidence"] == "1.00"
    assert end["how"] == "done" and end["answers"] == 2 * len(NAMES) and end["exit"] == 0
    assert end["cost"] == pytest.approx(answers[-1]["spent"]) and end["held"] == 0
    st = pilotlog.state(target.build)
    assert (st["planned"], st["answered"], st["status"], st["stage"]) == (2 * len(NAMES), 2 * len(NAMES), "ended",
                                                                          None)
    assert st["spent"] == pytest.approx(end["cost"]) and len(st["last"]) == pilotlog.LAST
    capsys.readouterr()
    assert pilot.main(["--target", target.root, "--status"]) == 0
    text = capsys.readouterr().out
    assert runs[0]["run"] in text and ": done" in text and "16 of 16 questions planned so far answered" in text, text
    assert "last answers:" in text and "accepted" in text
    assert pilot.main(["--target", target.root, "--list"]) == 0
    listed = capsys.readouterr().out
    assert runs[0]["run"] in listed and "done" in listed
    assert pilot.main(["--target", target.root, "--status", "no-such-run"]) == 1
    kind = llm.kind_of(made[0], pilot.NAME_SYSTEM)
    assert pilotlog.prior_outputs(target.build)[kind]["out"] == 300.0
    assert name_run(target) == 0                    # (all named: a run that asks nothing)
    assert [r["how"] for r in pilotlog.runs(target.build)] == ["done", "done"]
    assert pilotlog.state(target.build)["answers"] == 0


@pytest.mark.parametrize("batch", [0, 1])
def test_a_run_stops_at_its_budget_with_whole_answers_and_goes_on_when_started_again(tmp_path, monkeypatch, batch):
    """--budget below what the run costs, the first estimates from a run of the target before (its
    index: pilotlog.prior_outputs, as a run started again has them): the run asks no question whose
    estimate would take its spend past the budget and ends with "budget reached"; names.tsv holds whole
    votes only (both samples of each function asked) and only functions whose callees were named before
    them; started again without a budget the run asks only the rest, and the tables end as those of a
    run never stopped (one question at a time, and in waves of Message Batches)."""
    whole = copy_of(tmp_path / "whole")
    target, image, program, flow = copy_of(tmp_path / "gizmo")
    made = []
    monkeypatch.setattr(llm, "load", provider_loader(made, batches=bool(batch)))
    assert name_run(whole[0], "--batch", str(batch)) == 0
    cost = pilotlog.state(whole[0].build)["spent"]
    assert cost > 0
    budget = round(cost / 2, 6)
    os.makedirs(pilotlog.runs_dir(target.build))
    shutil.copy(pilotlog.index_path(whole[0].build), pilotlog.index_path(target.build))
    assert pilotlog.prior_outputs(target.build)
    assert name_run(target, "--batch", str(batch), "--budget", str(budget)) == 0
    st = pilotlog.state(target.build)
    end = st["end"]
    assert st["how"] == "budget reached" and end["cost"] <= budget + 1e-9 and end["held"] > 0, st
    assert st["budget_reached"]["words"].startswith("not asked") and st["budget"] == budget
    first = last_proposals(target, image)
    assert 0 < len(first) < len(NAMES), first
    assert all(len(samples.split()) == 2 and "-" not in samples.split() for _, samples, _ in first.values())
    from dread.lift import direct_callees
    for name in first:                              # (callees first: what was named had its callees named)
        assert all(c in first for c in direct_callees(program, program.by_name[name]) if c in NAMES and c != name)
    asked_before = {o["function"] for o in pilotlog.events(target.build, st["run"])[0] if o["event"] == "outcome"}
    assert set(first) <= asked_before
    assert name_run(target, "--batch", str(batch)) == 0
    again = pilotlog.state(target.build)
    assert again["how"] == "done" and again["run"] != st["run"]
    asked_again = {o["function"] for o in pilotlog.events(target.build, again["run"])[0] if o["event"] == "outcome"}
    assert asked_again == set(NAMES) - set(first)
    assert last_proposals(target, image) == last_proposals(whole[0], whole[1])
    runs = pilotlog.runs(target.build)
    assert [r["how"] for r in runs] == ["done", "budget reached", "done"] and runs[1]["cost"] <= budget + 1e-9
    assert runs[2]["run"] == pilotlog.runs(whole[0].build)[0]["run"]     # (the run before, from the copied index)


def test_a_vote_the_budget_cuts_short_keeps_the_answers_it_got(tmp_path):
    """A function begun whole (its two samples weighed at the default estimate fit) whose first answer is
    longer than estimated: the meter holds the second sample back (its estimate, from that answer, no
    longer fits), and the vote takes the one answer there is, as for a failed question (samples "x -",
    confidence 0.50); the spend stays within the budget, the run's budget is reached."""
    target, image, program, flow = copy_of(tmp_path / "gizmo")

    def answer(messages):
        return {"text": "name: pin_write\nwhat: drives a pin", "input": 100, "tokens": 5000}
    meter = llm.Meter(budget=0.05)      # (two samples at 4,000 tokens out: 0.041; one answer costs 0.025)
    with llm.metered(meter):
        made, asked, _ = pilot.name_functions(target, image, program, lambda: llm.Scripted(answer, prices=PRICES),
                                              ["gpio_set"], flow, jobs=1, samples=2, out=lambda *a: None)
    assert (made, asked) == (1, 1) and meter.reached is not None and meter.spent <= meter.budget
    assert last_proposals(target, image) == {"gpio_set": ("pin_write", "pin_write -", "0.50")}


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
@pytest.mark.parametrize("batch", [0, 1])
def test_the_variables_stop_at_the_budget_and_the_rest_stay_unasked(tmp_path, batch):
    """name_variables under a meter with a budget of half what its questions cost, its first estimates
    from the answers of a run before (as dread pilot's are: pilotlog.prior_outputs): the functions whose
    question fits are named, in their order, the rest have neither an entry nor a conversation; the
    spend stays within the budget (one at a time, and in a Message Batch)."""
    from dread import naming, readable, lift
    asked = ["strlen", "clamp", "checksum", "memcpy", "fmt_hex", "ring_put"]

    def answer(messages):
        text = llm.content_text(messages[0]["content"])
        name = re.match(r"The function (\w+) ", text).group(1)
        found = sorted(set(re.findall(r"\b(r\d+(?:_\d+)?)\b", text)))
        return {"text": '```json\n{"what": "%s does it", "names": %s}\n```' % (name, json.dumps(
            {v: "%s_%s" % (name, v) for v in found})), "input": -(-(len(pilot.READABLE_SYSTEM) + len(text)) // 3),
            "tokens": 100}

    def variables(path, budget, prior=None):
        target, image, program, flow = copy_of(path)
        context, lifter = readable.Context(target, image, program, flow), lift.make_lifter(program, flow)
        events = []
        meter = llm.Meter(budget=budget, emit=lambda kind, **fields: events.append((kind, fields)), prior=prior)
        with llm.metered(meter), pilotlog.current(Collected(events)):
            named, n_asked, _ = pilot.name_variables(target, image, program, lambda: llm.Scripted(
                answer, batches=bool(batch), prices=PRICES), asked, context, lifter, jobs=1, batch=batch,
                out=lambda *a: None)
        return target, image, meter, events, named, n_asked
    *_, whole, _, all_named, _ = variables(tmp_path / "whole", None)
    assert all_named == len(asked) and whole.reached is None
    target, image, meter, events, named, n_asked = variables(tmp_path / "gizmo", round(whole.spent / 2, 7),
                                                             whole.outputs())
    table = naming.load(image)
    assert meter.reached is not None and meter.spent <= meter.budget + 1e-9
    assert 0 < named == len(table) == n_asked < len(asked), (named, n_asked, sorted(table))
    assert set(table) == set(asked[:named])
    folder = os.path.join(target.build, "pilot", image.name)
    assert {n for n in asked if os.path.exists(os.path.join(folder, n + ".readable.md"))} == set(table)
    assert [f["result"] for k, f in events if k == "settled"] == ["named"] * named + ["unasked"] * (
        len(asked) - named if batch else 0)


def test_a_run_ended_by_sigterm_says_it_was_stopped(tmp_path):
    """SIGTERM while the run waits for a Message Batch: the batch is cancelled, the end event says
    "stopped" (ended by SIGTERM) with the totals so far, the tables are as the run left them (no
    proposal), and --status shows it."""
    target, image, program, flow = copy_of(tmp_path / "gizmo")
    script = tmp_path / "run.py"
    script.write_text(
        "import sys\n"
        "from dread import llm, pilot\n"
        "def load(name=None, path=None):\n"
        "    p = llm.Scripted(lambda m: 'name: x_y\\nwhat: does it', batches=True, never=True, prices=%r)\n"
        "    p.config['batch_poll'] = 0.05\n"
        "    return p\n"
        "llm.load = load\n"
        "sys.exit(pilot.main(['--target', %r, '--task', 'name', '--no-subsystems', '--samples', '1', '--passes', "
        "'1', '--no-device', '--batch', '1', 'led_on', 'gpio_set']))\n" % (PRICES, target.root))
    root = os.path.dirname(os.path.dirname(os.path.abspath(pilot.__file__)))
    child = subprocess.Popen([sys.executable, str(script)], cwd=root, env=dict(os.environ, PYTHONPATH=root),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline, run = time.time() + 120, None
        while time.time() < deadline:
            found = pilotlog.runs(target.build)
            if found:
                run = found[0]["run"]
                if any(e.get("event") == "batch" and e.get("state") == "submitted"
                       for e in pilotlog.events(target.build, run)[0]):
                    break
            time.sleep(0.1)
        assert run is not None, child.stdout.read() if child.poll() is not None else "no run"
        child.send_signal(signal.SIGTERM)
        output, _ = child.communicate(timeout=60)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    assert child.returncode == 143, output
    st = pilotlog.state(target.build, run)
    assert st["how"] == "stopped" and st["error"] == "ended by SIGTERM" and st["status"] == "ended", st
    assert [b["state"] for b in [e for e in pilotlog.events(target.build, run)[0] if e["event"] == "batch"]][-1] == \
        "cancelled" and st["waiting"] == []
    assert not os.path.exists(pilot.names_path(target, image))
    lines = []
    assert pilotlog.follow(target.build, run, out=lines.append) == 0 and "stopped" in lines[0]


def test_follow_prints_as_the_run_goes_and_ends_with_it(tmp_path):
    """pilotlog.follow: the status, then a line for each stage, batch and function settled as the
    events come, until the end event, after which the status again."""
    build = str(tmp_path / "build")
    log = pilotlog.RunLog.start(build, "name", "main", "played", "m", {"samples": 1}, 1.0, 2)
    steps = [lambda: log.event("stage", state="start", stage="pass 1", functions=2, questions=2),
             lambda: log.event("batch", state="submitted", id="b1", requests=2, estimate=0.001),
             lambda: log.event("answer", n=1, provider="played", mode="batch", input=10, output=5, cost=0.0001,
                               spent=0.0001, estimate=0.0005, turn=1),
             lambda: log.event("outcome", n=1, function="f", result="accepted", name="f_x"),
             lambda: log.event("batch", state="ended", id="b1", requests=2, succeeded=2, errored=0, seconds=3),
             lambda: log.event("settled", function="f", result="proposed", name="f_x", confidence="1.00"),
             lambda: log.event("stage", state="end", stage="pass 1", asked=2, proposed=1),
             lambda: log.end("done", answers=1, cost=0.0001)]
    lines = []
    st = pilotlog.state(build)
    assert st["status"] == "running" and st["stage"] is None and st["budget"] == 1.0

    def sleep(seconds):
        if steps:
            steps.pop(0)()
    assert pilotlog.follow(build, None, out=lines.append, sleep=sleep, progress=1e9) == 0
    text = "\n".join(lines)
    assert "stage pass 1: 2 questions" in text and "batch b1 submitted: 2 questions" in text
    assert "batch b1 ended after 3 s: 2 succeeded" in text and "proposed f_x (1.00)" in text
    heads = [line for line in lines if line.startswith("run ")]
    assert len(heads) == 2 and "running" in heads[0] and "ended" in heads[1] and ": done" in heads[1], heads
    assert "totals: 1 answers, $0.0001" in text and not steps


def test_threads_and_runs_write_whole_lines(tmp_path):
    """Threads of one run writing at once, and two runs of two targets beside them: every line of
    every events file is whole JSON, none lost, and the index of each target has its run's start and
    end."""
    logs = [pilotlog.RunLog.start(str(tmp_path / t), "name", "main") for t in ("one", "two")]

    def write(log, k):
        for i in range(500):
            log.event("outcome", function="f%d_%d" % (k, i), result="accepted", why="x" * (i % 300))
    threads = [threading.Thread(target=write, args=(logs[k % 2], k)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for log in logs:
        log.end("done")
        with open(log.path, "rb") as f:
            lines = f.read().split(b"\n")
        assert lines[-1] == b"" and all(json.loads(line) for line in lines[:-1])
        assert len(lines) - 1 == 2 + 4 * 500
        assert [r["state"] for r in pilotlog.read_lines(pilotlog.index_path(log.build))[0]] == ["start", "end"]
    assert pilotlog.read_lines(str(tmp_path / "missing.jsonl")) == ([], 0)
    part = tmp_path / "part.jsonl"                  # (a line still being written is left for the next read)
    part.write_bytes(b'{"event": "a"}\n{"event": "b"')
    assert pilotlog.read_lines(str(part)) == ([{"event": "a"}], 15)


def test_a_run_whose_process_is_gone_is_said_to_be_gone(tmp_path):
    """A run without an end event whose process is no more (killed outright) is "gone", not running, and
    its status says that a batch it left may still run at the provider, and how to cancel it."""
    build, run = str(tmp_path / "build"), "20261024-120000-name-dead"
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    os.makedirs(os.path.dirname(pilotlog.events_path(build, run)))
    start = {"t": time.time(), "event": "start", "run": run, "task": "name", "pid": child.pid,
             "host": pilotlog.socket.gethostname(), "provider": "haiku"}
    with open(pilotlog.events_path(build, run), "w") as f:     # (as the process killed outright left it)
        f.write(json.dumps(start) + "\n")
        f.write(json.dumps({"t": time.time(), "event": "batch", "state": "submitted", "id": "msgbatch_1",
                            "requests": 9}) + "\n")
    with open(pilotlog.index_path(build), "w") as f:
        f.write(json.dumps(dict(start, state="start")) + "\n")
    assert pilotlog.runs(build)[0]["status"] == "gone" and pilotlog.state(build)["status"] == "gone"
    text = "\n".join(pilotlog.status_lines(pilotlog.state(build)))
    assert "batches not seen to end: msgbatch_1 (9 questions" in text and "--cancel ID" in text, text
    lines = []
    assert pilotlog.follow(build, run, out=lines.append, sleep=lambda s: None) == 1 and "gone" in lines[-1]


def test_a_budget_needs_prices_and_is_not_for_the_review(tmp_path, monkeypatch, capsys):
    """--budget with a provider without prices is refused (nothing can be weighed), and so is --budget
    with --task review (dread.namereview would keep unasked challenges as answered)."""
    target, image, program, flow = copy_of(tmp_path / "gizmo")
    monkeypatch.setattr(llm, "load", lambda name=None, path=None: llm.Scripted(answering(pilot.NAME_SYSTEM)))
    assert name_run(target, "--budget", "1") == 1
    assert "needs a provider with prices" in capsys.readouterr().err
    assert pilot.main(["--target", target.root, "--task", "review", "--budget", "1"]) == 1
    assert "not for --task review" in capsys.readouterr().err
    assert pilotlog.runs(target.build) == []


def test_the_end_counts_what_was_settled_and_older_runs_are_counted_from_their_events(tmp_path):
    """The end event and the index's end line count the functions settled by result, and apart the
    other subjects (groups, batches of globals, the device); runs() and settled() read them, and an
    older run's events (no counts in its end) are counted."""
    build = str(tmp_path / "build")
    log = pilotlog.RunLog.start(build, "readable", "main", "played", "m", {"thinking": True}, None, 3)
    for function, result in (("f1", "named"), ("f2", "named"), ("f3", "unasked"), ("globals 0", "named"),
                             ("group 4", "none"), ("device", "named")):
        log.event("settled", function=function, result=result)
    log.end("budget reached", answers=3, cost=0.002, input=3000, output=900, batch=2)
    end = pilotlog.events(build, log.run)[0][-1]
    assert end["settled"] == {"named": 2, "unasked": 1} and end["settled_other"] == {"named": 2, "none": 1}
    assert pilotlog.runs(build)[0]["settled"] == {"named": 2, "unasked": 1}
    assert pilotlog.settled(build, log.run) == ({"named": 2, "unasked": 1}, {"named": 2, "none": 1})
    assert pilotlog.asked_functions(end["settled"]) == 2
    assert pilotlog.edge_event(build, log.run)["event"] == "start"
    assert pilotlog.edge_event(build, log.run, last=True)["event"] == "end"
    old = "20261020-120000-name-0ld0"                   # (as a run of before the counts left its events)
    os.makedirs(os.path.dirname(pilotlog.events_path(build, old)))
    with open(pilotlog.events_path(build, old), "w") as f:
        for e in ({"event": "start", "run": old, "task": "name", "t": 1.0}, {"event": "settled", "function": "a",
                  "result": "proposed"}, {"event": "settled", "function": "group 1", "result": "named"},
                  {"event": "end", "how": "done", "answers": 2, "t": 2.0}):
            f.write(json.dumps(e) + "\n")
    assert pilotlog.settled(build, old) == ({"proposed": 1}, {"named": 1})
    assert pilotlog.is_function("FUN_00001234") and not pilotlog.is_function("structures 2")


def test_an_estimate_from_an_earlier_run_of_the_task(tmp_path):
    """estimate(): the tokens a function asked of the newest ended run of the task, at the prices given,
    the share of its answers in Message Batches at the batch rate where this run may batch; one with the
    same settings preferred, a run with other settings said to differ; none without such a run or without
    prices."""
    build = str(tmp_path / "build")
    config = {"price_in": 1.0, "price_out": 5.0}            # (an "openai" provider: no cache or batch factors)
    assert pilotlog.estimate(build, "readable", {}, "openai", 10)["words"].startswith("no estimate: the provider has "
                                                                                       "no prices")
    assert "no earlier run of --task readable" in pilotlog.estimate(build, "readable", config, "openai", 10)["words"]

    def run(settings, tokens_in, tokens_out, batch, asked=4):
        log = pilotlog.RunLog.start(build, "readable", "main", "played", "m", settings, None, asked)
        for k in range(asked):
            log.event("settled", function="f%d" % k, result="named")
        log.event("settled", function="f_late", result="unasked")
        log.end("done", answers=asked, batch=batch, input=tokens_in, output=tokens_out, cost=0.01)
        time.sleep(1.1)                                     # (runs are ordered by their start time)
        return log.run
    first = run({"thinking": True}, 4000, 800, 0)
    second = run({"thinking": False}, 2000, 400, 4)
    got = pilotlog.estimate(build, "readable", config, "openai", 10, batch=True, settings={"thinking": True}, name="p")
    assert got["basis"]["run"] == first and got["differ"] == [] and got["batch_share"] == 0.0
    assert got["per_function"] == pytest.approx((1000 * 1.0 + 200 * 5.0) / 1e6)
    assert got["cost"] == pytest.approx(10 * got["per_function"]) and "at p's prices" in got["words"]
    newest = pilotlog.estimate(build, "readable", config, "openai", 10, batch=True, settings={"thinking": None})
    assert newest["basis"]["run"] == second and newest["differ"] == [["thinking", False, None]]
    batched = pilotlog.estimate(build, "readable", dict(config, price_batch=0.5), "openai", 10, batch=True,
                                settings={"thinking": False})
    assert batched["basis"]["run"] == second and batched["batch_share"] == 1.0
    assert batched["per_function"] == pytest.approx(0.5 * (500 * 1.0 + 100 * 5.0) / 1e6)
    alone = pilotlog.estimate(build, "readable", dict(config, price_batch=0.5), "openai", 10, batch=False,
                              settings={"thinking": False})
    assert alone["per_function"] == pytest.approx(2 * batched["per_function"])
    assert pilotlog.estimate(build, "name", config, "openai", 10)["cost"] is None


def test_the_dry_run_plans_and_prices_a_naming_run(tmp_path, monkeypatch, capsys):
    """pilot.planned: the functions a naming run takes and those it asks (not those proposed already);
    a name the image lacks refused; no plan for a task whose questions come as it runs. The dry run
    prints the estimate from the run before (the tokens a function at the provider's prices)."""
    target, image, program, flow = copy_of(tmp_path / "gizmo")
    made = []
    monkeypatch.setattr(llm, "load", provider_loader(made))
    plan = pilot.planned(target, image, program, "name", NAMES)
    assert sorted(plan["functions"]) == sorted(NAMES) and plan["asked"] == plan["functions"] and plan["already"] == 0
    with pytest.raises(ValueError, match="no function nosuch"):
        pilot.planned(target, image, program, "name", ["led_on", "nosuch"])
    assert pilot.planned(target, image, program, "write") is None
    assert pilot.main(["--target", target.root, "--task", "name", "--dry-run", "led_on"]) == 0
    assert "dread pilot --dry-run: no estimate: no earlier run of --task name" in capsys.readouterr().out
    assert name_run(target) == 0
    capsys.readouterr()
    plan = pilot.planned(target, image, program, "name", NAMES + ["strlen", "clamp"])
    assert sorted(plan["asked"]) == ["clamp", "strlen"] and plan["already"] == len(NAMES)
    run = pilotlog.runs(target.build)[0]
    assert pilot.main(["--target", target.root, "--task", "name", "--samples", "2", "--passes", "1",
                       "--no-subsystems", "--dry-run", "strlen", "clamp"]) == 0
    out = capsys.readouterr().out
    assert "2 functions would be asked about" in out
    line = [l for l in out.splitlines() if l.startswith("dread pilot --dry-run: about $")][0]
    assert "2 functions at" in line and run["run"] in line and "settings differ" not in line, line
    assert pilot.main(["--target", target.root, "--task", "name", "--dry-run", "nosuch"]) == 1
    assert "no function nosuch" in capsys.readouterr().err
