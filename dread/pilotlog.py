"""dread pilot's runs as events: what a run does, written as it happens, read by dread pilot --status and
by dread gui.

Every run of dread pilot that may ask a provider (each task; --score with a judge) gets a run id (its start
time, its task and four hex digits: 20261024-153012-name-1a2b) and a directory BUILD/pilot/runs/ID/ with
events.jsonl in it: one JSON object a line, each with its time ("t", seconds since the epoch) and its kind
("event"): the run's start, each stage's start and end, each round of questions, each Message Batch
submitted, looked at and ended, each answer (its tokens, its cost, the spend so far) and what the pilot made
of it, each function settled, the budget reached, warnings, and the run's end with how it ended (done,
stopped, failed, budget reached) and its totals. The kinds and their fields: docs/formats.md. A line is
written as its event happens, with one write() of the whole line to the file opened for appending (no
buffer: a reader sees every line whole once it is there); the threads of a run share the file through a
lock. BUILD/pilot/runs/index.jsonl has two lines a run, its start and its end, appended under a lock of
the file (fcntl), since runs of one target may start and end at once; runs of different targets write
different files.

The reading, for dread pilot --status and the GUI (each takes the target's build directory): runs(build),
the runs newest first, each with how it ended or whether it is still running; state(build, run), one run's
state folded from its events (its stage, the questions answered of those planned, the Message Batches
waiting and their age, the spend against the budget, the rate and an estimate of the time left, the last
answers; the latest run without a run id); events(build, run, offset), the events written since a byte
offset of the file and the offset to read from next. A reader takes whole lines only and skips a line it
cannot read; it ignores kinds and fields it does not know (format: FORMAT, in the start event).

What the events do not establish: anything after the run's process was killed outright (SIGKILL, the
interpreter's crash): such a run has no end event, and runs() and state() call it "gone" once its
process is (looked up on the same host by its pid and the process's start time; a run of another host
stays "running"). Times are the wall clock's: the events are a log, not a deterministic output, and the
run's tables do not depend on them.
"""
import collections
import json
import os
import socket
import threading
import time

FORMAT = 1                  # the events' format (the start event's "format")
ENDINGS = ("done", "stopped", "failed", "budget reached")
LAST = 8                    # the answers state() keeps as the last ones
WARNINGS = 5                # and the warnings
PROGRESS = 30.0             # seconds between two progress lines of follow()

CURRENT = None              # the RunLog of the run in this process (current()), or None


def runs_dir(build):
    """Where a target's runs are: BUILD/pilot/runs."""
    return os.path.join(build, "pilot", "runs")


def index_path(build):
    return os.path.join(runs_dir(build), "index.jsonl")


def events_path(build, run):
    return os.path.join(runs_dir(build), run, "events.jsonl")


def note(kind, **fields):
    """An event of the run in this process, if there is one (nothing otherwise)."""
    log = CURRENT
    if log is not None:
        log.event(kind, **fields)


class current:
    """A context in which log is the run of this process (CURRENT, note()); the one before comes back at
    its end."""

    def __init__(self, log):
        self.log, self.before = log, None

    def __enter__(self):
        global CURRENT
        self.before, CURRENT = CURRENT, self.log
        return self.log

    def __exit__(self, *exc):
        global CURRENT
        CURRENT = self.before
        return False


def line_of(record):
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode()


def append(path, data, lock=False):
    """data (bytes) at the end of the file at path, with one write() to it opened for appending (the
    rest written after, should a write take less), under an exclusive lock of the file with lock."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        if lock:
            try:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
        while data:
            data = data[os.write(fd, data):]
    finally:
        os.close(fd)


class RunLog:
    """One run's events (see the module): start() makes the run's directory and writes its start
    event and index line, event() writes an event, end() the end event and the index line, after which
    nothing more is written. Threads may share it."""

    def __init__(self, build, run):
        self.build, self.run = build, run
        self.path = events_path(build, run)
        self.lock = threading.Lock()
        self.fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        self.started = time.time()
        self.stage = None                   # the stage begun last (stage events), put into outcomes

    @classmethod
    def start(cls, build, task, image=None, provider=None, model=None, settings=None, budget=None, functions=None,
              argv=None, target=None):
        """A new run of task: a new directory under runs_dir(build) named by a new run id, its start
        event (FORMAT, the run id, task, image, target, provider and model, settings, budget, the
        functions planned when known, the process id, host and command line) and its index line."""
        folder = runs_dir(build)
        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        slug = "".join(c if c.isalnum() else "-" for c in str(task))[:24]
        for _ in range(1000):
            run = "%s-%s-%s" % (stamp, slug, os.urandom(2).hex())
            try:
                os.mkdir(os.path.join(folder, run))
                break
            except FileExistsError:
                continue
        else:
            raise OSError("no new run directory under %s" % folder)
        log = cls(build, run)
        fields = {"format": FORMAT, "run": run, "task": task, "image": image, "target": target, "provider": provider,
                  "model": model, "settings": settings or {}, "budget": budget, "functions": functions,
                  "pid": os.getpid(), "host": socket.gethostname(), "argv": list(argv) if argv is not None else None}
        log.event("start", **fields)
        log.index({"run": run, "state": "start", "t": round(log.started, 3), "task": task, "image": image,
                   "provider": provider, "model": model, "budget": budget, "pid": os.getpid(),
                   "host": fields["host"]})
        return log

    def event(self, kind, **fields):
        """One event, written at once: {"t": now, "event": kind, **fields} (fields that are None left
        out). A stage's start makes it the stage outcomes and settled events name."""
        record = {"t": round(time.time(), 3), "event": kind}
        if kind == "stage" and fields.get("state") == "start":
            self.stage = fields.get("stage")
        elif kind in ("outcome", "settled") and "stage" not in fields and self.stage is not None:
            record["stage"] = self.stage
        record.update((k, v) for k, v in fields.items() if v is not None)
        data = line_of(record)
        with self.lock:
            if self.fd is None:
                return
            while data:
                data = data[os.write(self.fd, data):]

    def index(self, record):
        append(index_path(self.build), line_of(record), lock=True)

    def end(self, how, **fields):
        """The end event (how: ENDINGS; fields: the totals) and the index line; the file is closed."""
        seconds = round(time.time() - self.started, 1)
        self.event("end", how=how, seconds=seconds, **fields)
        self.index({"run": self.run, "state": "end", "t": round(time.time(), 3), "how": how, "seconds": seconds,
                    "answers": fields.get("answers"), "cost": fields.get("cost"), "outputs": fields.get("outputs")})
        with self.lock:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None


# ---- reading ----

def read_lines(path, offset=0):
    """(records, offset): the JSON objects of the whole lines of a file from a byte offset on, and the
    offset after the last whole line (a line still being written is left for the next read; one that
    cannot be read is skipped)."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    out = []
    for line in data[:end].split(b"\n"):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out, offset + end + 1


def events(build, run, offset=0):
    """(events, offset) of a run written since a byte offset of its events file (0: all of them), and
    the offset to ask from next: what the GUI polls."""
    return read_lines(events_path(build, run), offset)


def process_started(pid):
    """When the process pid started (seconds since the epoch), from Linux's /proc; None where that cannot
    be read."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/stat") as f:
            boot = next(int(line.split()[1]) for line in f if line.startswith("btime "))
        return boot + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def alive(start):
    """Whether the run of a start event (or index line) still has its process: on this host, its pid
    exists and that process began before the run did (a pid used again by a later process is not the
    run's); a run of another host counts as alive (nothing here can tell)."""
    pid, host = start.get("pid"), start.get("host")
    if not pid:
        return False
    if host and host != socket.gethostname():
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError, ValueError):
        pass
    began = process_started(int(pid))
    return began is None or began <= float(start.get("t") or 0) + 2.0


def runs(build):
    """The runs of a target, newest first: [{"run", "task", "image", "provider", "model", "budget",
    "started", "pid", "host", "status": "running", "ended" or "gone", "how" (ENDINGS) and "ended",
    "seconds", "answers", "cost" for an ended run}], from the index; a run directory the index lacks is
    read from its start and end events."""
    by = collections.OrderedDict()
    records, _ = read_lines(index_path(build))
    for r in records:
        run = r.get("run")
        if not isinstance(run, str):
            continue
        entry = by.setdefault(run, {"run": run})
        if r.get("state") == "start":
            entry.update({k: r.get(k) for k in ("task", "image", "provider", "model", "budget", "pid", "host")})
            entry["started"] = r.get("t")
        elif r.get("state") == "end":
            entry.update({k: r.get(k) for k in ("how", "seconds", "answers", "cost")})
            entry["ended"] = r.get("t")
    folder = runs_dir(build)
    if os.path.isdir(folder):
        for run in sorted(os.listdir(folder)):
            if run in by or not os.path.isfile(events_path(build, run)):
                continue
            st = state(build, run)
            if st is not None:
                by[run] = {k: st.get(k) for k in ("run", "task", "image", "provider", "model", "budget", "started",
                                                   "pid", "host", "how", "ended", "seconds", "answers", "cost")}
    out = []
    for entry in by.values():
        if entry.get("how"):
            entry["status"] = "ended"
        else:
            entry["status"] = "running" if alive({"pid": entry.get("pid"), "host": entry.get("host"),
                                                   "t": entry.get("started")}) else "gone"
        out.append(entry)
    out.sort(key=lambda e: (e.get("started") or 0, e["run"]), reverse=True)
    return out


def latest(build):
    """The newest run's id, or None."""
    found = runs(build)
    return found[0]["run"] if found else None


def prior_outputs(build):
    """{kind: {"out": tokens out of an answer, "chars": characters of a question a token in, "per_in": tokens
    out a token in}} from the ended runs of a target (their end's outputs; a later run's figures for a kind
    replace an earlier one's): the first estimates of the next run's budget (dread.llm Meter's prior)."""
    out = {}
    records, _ = read_lines(index_path(build))
    for r in records:
        if r.get("state") == "end" and isinstance(r.get("outputs"), dict):
            for kind, told in r["outputs"].items():
                if isinstance(told, dict):
                    kept = {k: v for k, v in told.items() if k in ("out", "chars", "per_in")
                            and isinstance(v, (int, float)) and v > 0}
                    if kept:
                        out[kind] = dict(out.get(kind, {}), **kept)
    return out


class Run:
    """A run's state folded from its events, one update() an event (see state())."""

    def __init__(self, run=None):
        self.run, self.start, self.end = run, {}, None
        self.stages, self.stage = [], None
        self.answers = self.first = self.batch_answers = 0
        self.spent, self.batches = 0.0, collections.OrderedDict()
        self.last, self.warnings = collections.deque(maxlen=LAST), collections.deque(maxlen=WARNINGS)
        self.budget_reached = None
        self.outcomes = collections.Counter()
        self.first_t = self.last_t = None

    def update(self, e):
        kind, t = e.get("event"), e.get("t")
        if isinstance(t, (int, float)):
            self.last_t = t
        if kind == "start":
            self.start = e
            self.run = e.get("run", self.run)
        elif kind == "stage":
            if e.get("state") == "start":
                self.stage = {"stage": e.get("stage"), "started": t, "functions": e.get("functions"),
                              "questions": e.get("questions"), "at_most": bool(e.get("at_most")), "answered": 0,
                              "answers": 0}
                self.stages.append(self.stage)
                if self.first_t is None:
                    self.first_t = t
            elif self.stage is not None and e.get("stage") == self.stage["stage"]:
                self.stage["ended"] = t
                self.stage["end"] = {k: v for k, v in e.items() if k not in ("t", "event", "state", "stage")}
        elif kind == "answer":
            self.answers += 1
            self.batch_answers += e.get("mode") == "batch"
            first = e.get("turn", 1) == 1
            self.first += first
            if self.stage is not None and "ended" not in self.stage:
                self.stage["answers"] += 1
                self.stage["answered"] += first
            if isinstance(e.get("spent"), (int, float)):
                self.spent = max(self.spent, e["spent"])
            if self.first_t is None:
                self.first_t = t
        elif kind == "outcome":
            self.last.append(e)
            self.outcomes[e.get("result")] += 1
        elif kind == "batch":
            bid = e.get("id")
            state = e.get("state")
            if state == "failed" or bid is None:
                self.warnings.append({"t": t, "text": "a batch of %s questions could not be submitted: %s" % (
                    e.get("requests"), e.get("error"))})
                return
            b = self.batches.setdefault(bid, {"id": bid, "submitted": t, "requests": e.get("requests"),
                                              "status": "submitted"})
            if state == "submitted":
                b.update(submitted=t, requests=e.get("requests"), estimate=e.get("estimate"))
            elif state == "looked":
                b.update(status=e.get("status") or b["status"], counts=e.get("counts"), looked=t)
            elif state == "cancelled":
                b.update(status="cancelled", cancelled=e.get("why"))
            elif state == "ended":
                b.update(status="ended", ended=t, result={k: e.get(k) for k in (
                    "succeeded", "errored", "canceled", "expired", "missing", "seconds") if e.get(k) is not None})
        elif kind == "budget":
            self.budget_reached = e
        elif kind in ("warning", "error"):
            self.warnings.append({"t": t, "text": ("error: " if kind == "error" else "") + str(e.get("text"))})
        elif kind == "end":
            self.end = e
            if isinstance(e.get("cost"), (int, float)):
                self.spent = e["cost"]

    def summary(self, now=None):
        """The state as a dict (see state())."""
        now = time.time() if now is None else now
        s = self.start
        planned = sum(st["questions"] or 0 for st in self.stages)
        answered = sum(st["answered"] for st in self.stages)
        waiting = []
        for b in self.batches.values():
            if b["status"] not in ("ended", "cancelled") and b.get("ended") is None:
                counts = b.get("counts") or {}
                waiting.append({"id": b["id"], "requests": b.get("requests"), "status": b["status"],
                                "age": round(now - (b.get("submitted") or now), 1),
                                "answered": sum(counts.get(k) or 0 for k in ("succeeded", "errored", "canceled",
                                                                               "expired")) if counts else None})
        elapsed = (now if self.end is None else self.end.get("t", now)) - (self.first_t or now)
        rate = answered / elapsed if answered and elapsed > 0 else None
        left = None
        if self.end is None and rate and planned > answered:
            left = (planned - answered) / rate
        if self.end is not None:
            status = "ended"
        elif s and not alive(s):
            status = "gone"
        else:
            status = "running"
        stage = self.stages[-1] if self.stages else None
        return {"run": self.run, "task": s.get("task"), "image": s.get("image"), "target": s.get("target"),
                "provider": s.get("provider"), "model": s.get("model"), "settings": s.get("settings") or {},
                "budget": s.get("budget"), "functions": s.get("functions"), "started": s.get("t"),
                "pid": s.get("pid"), "host": s.get("host"), "status": status,
                "how": (self.end or {}).get("how"), "ended": (self.end or {}).get("t"),
                "seconds": (self.end or {}).get("seconds"), "exit": (self.end or {}).get("exit"),
                "error": (self.end or {}).get("error"), "end": self.end,
                "stage": None if stage is None or "ended" in stage else stage["stage"],
                "stages": [dict(st) for st in self.stages], "planned": planned, "answered": answered,
                "answers": self.answers, "batch_answers": self.batch_answers,
                "batches": [dict(b) for b in self.batches.values()], "waiting": waiting, "spent": self.spent,
                "budget_reached": self.budget_reached, "rate": rate, "left": left,
                "outcomes": dict(self.outcomes), "last": list(self.last), "warnings": list(self.warnings),
                "last_event": None if self.last_t is None else round(now - self.last_t, 1)}


def state(build, run=None, now=None):
    """One run's state (the latest run without run), or None when there is none: {"run", "task", "image",
    "target", "provider", "model", "settings", "budget", "functions", "started", "pid", "host", "status"
    ("running", "ended", "gone"), "how" (ENDINGS), "ended", "seconds", "exit", "error", "end" (the end
    event), "stage" (the stage going on, or None), "stages" ([{"stage", "started", "ended", "functions",
    "questions" planned, "at_most", "answered" (first questions answered), "answers", "end": the stage
    end's fields}]), "planned" (the questions of the stages begun so far), "answered", "answers" (every
    answer, questions asked again too), "batch_answers", "batches", "waiting" ([{"id", "requests",
    "status", "age" in seconds, "answered" by the API's counts or None}]), "spent", "budget_reached" (the
    budget event, or None), "rate" (questions answered a second since the first stage began, or None),
    "left" (seconds, estimated from the rate for the questions planned so far, or None), "outcomes"
    ({result: count}), "last" (the last LAST outcome events), "warnings", "last_event" (seconds since the
    last event)}. The rate counts answers as they come back: Message Batches return theirs at once, so
    the time left is rough while a batch is out."""
    run = run or latest(build)
    if run is None:
        return None
    records, _ = read_lines(events_path(build, run))
    if not records:
        return None
    folded = Run(run)
    for e in records:
        folded.update(e)
    return folded.summary(now)


# ---- the text dread pilot --status prints ----

def clock(t):
    return time.strftime("%H:%M:%S", time.localtime(t)) if isinstance(t, (int, float)) else "?"


def span(seconds):
    if seconds is None:
        return "?"
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return "%.0f s" % seconds
    if seconds < 5400:
        return "%.0f min" % (seconds / 60)
    return "%.1f h" % (seconds / 3600)


def money(x):
    return "$%.4f" % x if isinstance(x, (int, float)) else "?"


def usd(x):
    """A budget as text: to the cent where that is exact, else to a hundredth of a cent."""
    return "$%.2f" % x if abs(x * 100 - round(x * 100)) < 1e-6 else "$%.4f" % x


def outcome_text(e):
    """An outcome event as a short line: the function, what came of the answer, its cost."""
    what = e.get("function") or "?"
    result = e.get("result") or "?"
    detail = e.get("name") or ""
    if not detail and isinstance(e.get("names"), dict):
        detail = ", ".join("%s=%s" % kv for kv in list(e["names"].items())[:4]) + (
            " ..." if len(e["names"]) > 4 else "")
    elif not detail and e.get("names") is not None:
        detail = str(e["names"])
    if e.get("why"):
        detail = (detail + " " if detail else "") + "(%s)" % str(e["why"])[:100]
    cost = " %s" % money(e["cost"]) if isinstance(e.get("cost"), (int, float)) else ""
    return "%s %-24s %s %s%s" % (clock(e.get("t")), what, result, detail, cost)


def progress_text(st):
    """The questions answered of those planned, the spend against the budget, the rate and the time left."""
    parts = []
    stage = next((x for x in reversed(st["stages"]) if "ended" not in x), None)
    if not st["stages"] and st["status"] == "running":
        parts.append("preparing: no stage begun yet")
    if stage is not None:
        parts.append("stage %s%s: %d of %s%s questions answered" % (
            stage["stage"], " (not finished)" if st["status"] != "running" else "", stage["answered"],
            "at most " if stage["at_most"] else "", "?" if stage["questions"] is None else
            "{:,}".format(stage["questions"])))
    parts.append("%d of %s questions planned so far answered (%d answers)" % (
        st["answered"], "{:,}".format(st["planned"]), st["answers"]))
    budget = st.get("budget")
    parts.append("spent %s%s" % (money(st["spent"]), " of %s (%.0f %%)" % (usd(budget), 100.0 * st["spent"] / budget)
                                if isinstance(budget, (int, float)) and budget else ""))
    if st["rate"]:
        parts.append("%.2f questions/s" % st["rate"])
    if st["left"] is not None:
        parts.append("about %s left" % span(st["left"]))
    return "; ".join(parts)


def status_lines(st, now=None):
    """dread pilot --status's lines for a run's state (state())."""
    now = time.time() if now is None else now
    head = "run %s: %s%s, %s (%s), started %s" % (
        st["run"], st.get("task") or "?", " on %s" % st["image"] if st.get("image") else "", st.get("provider") or "?",
        st.get("model") or "?", clock(st.get("started")))
    if st["status"] == "ended":
        head += ", ended %s after %s: %s%s" % (clock(st.get("ended")), span(st.get("seconds")), st.get("how"),
                                               " (%s)" % st["error"] if st.get("error") else "")
    elif st["status"] == "gone":
        head += ", gone: no end event and its process (pid %s) is no more (killed?); last event %s ago" % (
            st.get("pid"), span(st.get("last_event")))
    else:
        head += ", running (pid %s, last event %s ago)" % (st.get("pid"), span(st.get("last_event")))
    lines = [head]
    if st["stages"]:
        done = ", ".join("%s %d%s" % (x["stage"], x["answered"], "/%s%s" % (
            "<=" if x["at_most"] else "", x["questions"]) if x["questions"] is not None else "") for x in st["stages"])
        lines.append("stages: %s" % done)
    lines.append(progress_text(st))
    if st["waiting"]:
        lines.append("batches %s: %s" % ("waiting" if st["status"] == "running" else "not seen to end", ", ".join(
            "%s (%s questions, %s%s)" % (b["id"], b.get("requests"), span(b["age"]),
                                         ", %d answered" % b["answered"] if b.get("answered") else "")
            for b in st["waiting"])))
        if st["status"] == "gone":          # (killed outright: nothing cancelled them, and they are billed)
            lines.append("they may still run at the provider: dread llm batches --provider %s, --cancel ID" % (
                st.get("provider") or "NAME"))
    if st.get("budget_reached"):
        lines.append("budget reached at %s: %s" % (clock(st["budget_reached"].get("t")),
                                                   st["budget_reached"].get("words", "")))
    end = st.get("end") or {}
    if end:
        told = [("%s answers" % end["answers"]) if end.get("answers") is not None else "",
                ("%s in Message Batches" % end["batch"]) if end.get("batch") else "",
                ("%s tokens in, %s out" % (end["input"], end.get("output"))) if end.get("input") is not None else "",
                money(end["cost"]) if end.get("cost") is not None else "",
                ("%s questions asked" % end["asked"]) if end.get("asked") is not None else "",
                ("%s held back by the budget" % end["held"]) if end.get("held") else ""]
        lines.append("totals: " + ", ".join(x for x in told if x))
    if st["outcomes"]:
        lines.append("answers: %s" % ", ".join("%s %d" % (k, v) for k, v in sorted(st["outcomes"].items(),
                                                                                    key=lambda kv: -kv[1])))
    if st["last"]:
        lines.append("last answers:")
        lines += ["  " + outcome_text(e) for e in st["last"]]
    for w in st["warnings"]:
        lines.append("warning %s: %s" % (clock(w.get("t")), w.get("text")))
    return lines


def event_line(e):
    """A line for dread pilot --status --follow of an event worth one, or None (answers and outcomes are
    many: follow() prints progress lines and the functions settled instead)."""
    kind, t = e.get("event"), clock(e.get("t"))
    if kind == "stage":
        if e.get("state") == "start":
            return "%s stage %s: %s%s questions, %s functions" % (
                t, e.get("stage"), "at most " if e.get("at_most") else "", e.get("questions", "?"),
                e.get("functions", "?"))
        rest = ", ".join("%s %s" % (k, v) for k, v in e.items() if k not in ("t", "event", "state", "stage"))
        return "%s stage %s ended%s" % (t, e.get("stage"), ": " + rest if rest else "")
    if kind == "round":
        return "%s a round of %s questions%s" % (t, e.get("questions"), " as a Message Batch" if e.get("batch") else
                                                 ", one at a time")
    if kind == "batch":
        state = e.get("state")
        if state == "submitted":
            return "%s batch %s submitted: %s questions (estimated %s)" % (t, e.get("id"), e.get("requests"),
                                                                          money(e.get("estimate")))
        if state == "looked":
            if e.get("status") == "ended":  # (its "ended" event follows, with the counts)
                return None
            counts = e.get("counts") or {}
            done = sum(counts.get(k) or 0 for k in ("succeeded", "errored", "canceled", "expired"))
            return "%s batch %s %s after %s%s" % (t, e.get("id"), e.get("status"), span(e.get("age")),
                                                  ": %d answered" % done if counts else "")
        if state == "ended":
            return "%s batch %s ended after %s: %s succeeded, %s errored%s" % (
                t, e.get("id"), span(e.get("seconds")), e.get("succeeded"), e.get("errored"),
                ", %s cancelled" % e["canceled"] if e.get("canceled") else "")
        if state == "cancelled":
            return "%s batch %s cancelled (%s)" % (t, e.get("id"), e.get("why"))
        return "%s a batch of %s questions failed: %s" % (t, e.get("requests"), e.get("error"))
    if kind == "settled":
        detail = e.get("name") or ""
        if e.get("confidence") is not None:
            detail += " (%s)" % e["confidence"]
        if e.get("why"):
            detail += " (%s)" % str(e["why"])[:100]
        return "%s %-24s %s %s" % (t, e.get("function"), e.get("result"), detail)
    if kind == "budget":
        return "%s budget reached: %s" % (t, e.get("words"))
    if kind in ("warning", "error"):
        return "%s %s: %s" % (t, kind, e.get("text"))
    return None


def follow(build, run=None, out=print, interval=1.0, progress=PROGRESS, sleep=time.sleep):
    """dread pilot --status --follow: the run's status, then a line for every event worth one (event_line)
    and a progress line every progress seconds, until the run's end event (its status again then) or until
    its process is gone without one. Returns 0, or 1 without such a run or for a run gone."""
    run = run or latest(build)
    if run is None:
        out("dread pilot: no run in %s" % runs_dir(build))
        return 1
    path = events_path(build, run)
    folded = Run(run)
    records, offset = read_lines(path)
    for e in records:
        folded.update(e)
    for line in status_lines(folded.summary()):
        out(line)
    shown = time.time()
    while folded.end is None:
        if folded.start and not alive(folded.start):
            records, offset = read_lines(path, offset)      # (what it wrote before it went)
            for e in records:
                folded.update(e)
            if folded.end is None:
                out("dread pilot: run %s is gone: no end event, and its process (pid %s) is no more (killed?)" % (
                    run, folded.start.get("pid")))
                return 1
            break
        sleep(interval)
        records, offset = read_lines(path, offset)
        for e in records:
            folded.update(e)
            line = event_line(e)
            if line:
                out(line)
        if folded.end is None and time.time() - shown >= progress:
            out("%s %s" % (clock(time.time()), progress_text(folded.summary())))
            shown = time.time()
    for line in status_lines(folded.summary()):
        out(line)
    return 0


def list_lines(found):
    """dread pilot --list's lines: a run a line, newest first, with how it ended and what it cost."""
    lines = []
    for r in found:
        how = r.get("how") if r["status"] == "ended" else r["status"]
        lines.append("%-34s %-10s %-10s %-24s %s  %-15s %6s answers  %s%s" % (
            r["run"], r.get("task") or "?", r.get("image") or "", "%s (%s)" % (r.get("provider"), r.get("model")),
            time.strftime("%Y-%m-%d %H:%M", time.localtime(r["started"])) if r.get("started") else "?",
            how, r.get("answers") if r.get("answers") is not None else "-", money(r.get("cost")),
            " of %s" % usd(r["budget"]) if isinstance(r.get("budget"), (int, float)) else ""))
    return lines
