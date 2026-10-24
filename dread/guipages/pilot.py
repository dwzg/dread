"""dread gui's pilot page (#/pilot, key l): dread pilot started from a short form, followed live and
stopped, the runs before it, what the answers cost a month, and the pilot's comparisons of names. The
page starts DREAD's commands as jobs (dread.guipages.run start_command) and reads what they write
(dread.pilotlog: the runs' events and index; dread.llm: the usage ledger and the providers of llm.toml);
it computes nothing the tools do not.

- Start: a short form of the pilot's own options (its parser, dread.cliforms: the task, the provider with
  its prices, the budget in US dollars, all functions or some, Message Batches on or off, and the few
  options each task needs, SHORT); the command line is the Run page's (/api/command-line), and the Run
  page's form of dread pilot has every option. The estimate (POST /api/pilot/estimate) is the pilot's
  dry run's count (dread.pilot planned, what `dread pilot --dry-run` prints) times the tokens a function
  of an earlier run of the task on this target at the chosen provider's prices (dread.pilotlog estimate;
  the dry run prints the same line); without such a run there is none, and the page says why. Start runs
  `dread pilot` as a job with GRACE seconds from the stop's SIGTERM to SIGKILL: the pilot cancels its
  Message Batches and waits llm.STOP_WAIT (20 s) at most for the questions being asked before its end
  event "stopped".
- A run (#/pilot/ID): its state folded from its events (pilotlog.Run), kept per run in this process and
  advanced from the byte offset it has read (Follow), so that a page that polls every second reads only
  what was written since; a first look gets the newest KEEP events worth a line (NOTABLE: what the pilot
  made of each answer, each function settled, the stages, batches, the budget, warnings, errors, the end),
  later looks those since the page's offset. The run's job is the page's job whose process is the run's
  (the same pid, the run begun while the job ran): Stop is that job's stop. A run started elsewhere (a
  terminal, another server) is followed, not stopped. A run gone without an end (killed outright) may
  have left Message Batches at the provider: a button cancels them (dread llm batches --cancel, a job).
- The runs before: pilotlog.runs with what each settled (the end event's "settled", or counted over an
  older run's events, kept by the file's size and time) and its job.
- The cost per month: dread.llm usage_summary for the month asked and for every month of the ledger (one
  per line's first seven characters), per provider with the Message Batches' and the prompt cache's
  shares and what both saved against the standard prices; kept while the ledger and llm.toml are as they
  were.
- Comparisons: what dread pilot can compare is function names, names.tsv of --task name: with the true
  names (--score TRUTH, which asks nothing without a judge), with another copy's names.tsv (--against,
  with --grounded or --judge), two settings over replicate runs (--against ... --vs ..., with --grounded:
  docs/decisions.md). They run as jobs of dread pilot; their result is what the pilot prints and writes
  (names-score.tsv, names-pairwise.tsv, names-replicates.tsv). Two runs of the history are explained
  (compare_pair): the pilot has no comparison for the other tasks' names, and two naming runs of one
  target wrote one names.tsv (the second asked only what had no proposal), so they are one set of names.

No key, nor a key's value, reaches the page: a provider says whether its key is there and where a request
would take it from (dread.llm key_status: $KEY_ENV in the server's environment, or a key file that passes
its checks), never what it is; providers and keys are set up on the Setup page (#/setup). The jobs' logs
and records replace the environment's secrets and the key files' keys by their names (dread.jobs). A
server without a target (the Setup page alone, until it makes or opens one) shows the cost per month and
starts nothing here: the other routes answer 409.

Routes (dread.guipages):
- GET /api/pilot: {"providers": providers(), "fields": the form's fields (dread.cliforms describe of the
  pilot's parser: COMMON, SHORT and --task), "tasks": [{"name", "fields", "budget", "why"}], "common",
  "runnable", "why", "cwd", "defaults" (what a job fills in: --target), "image", "grace", "setup": whether
  a Setup page is registered, "truths": ELF files in the target's directory};
- POST /api/pilot/estimate {"values"}: estimate() (needs the page's token; also on a read-only server);
- POST /api/pilot/start {"values"}: the job's record (400 with the command's refusal);
- GET /api/pilot/runs[?limit=N]: {"runs": the newest RUNS runs, each with "settled", "named" and "job",
  "more": how many older ones are left out};
- GET /api/pilot/job?id=N: {"job", "run" (its run's id once it has begun, else null), "tail"};
- GET /api/pilot/follow?run=ID[&from=BYTE]: {"run", "next", "events", "dropped", "state", "job", "stop"};
- POST /api/pilot/stop {"run"} or {"job"}: the job's record (409 where the page has no job to stop);
- POST /api/pilot/cancel-batches {"run"}: a job of dread llm batches --cancel for a gone run's batches;
- GET /api/pilot/usage[?month=YYYY-MM]: usage();
- GET /api/pilot/compare[?a=ID&b=ID]: what can be compared (the names table, ELF files, other copies'
  names.tsv beside the target, the providers that can judge: those with prices, the cheapest first) and,
  with a and b, compare_pair;
- POST /api/pilot/compare/start {"mode": "score"|"against"|"replicates", "truth", "judge", "provider",
  "against", "vs", "names_only"}: the job's record.

What it does not establish: a run's job is found by its process id on this host (a run of another host
is shown without one); the estimate is rough (pilotlog.estimate says how); the comparisons are the
pilot's, with what docs/decisions.md says of them (one run against one shows a direction, not which
setting is better).
"""
import collections
import glob
import os
import re
import socket
import threading
import time

from dread import cli, cliforms, llm, pilotlog
from dread import jobs as jobs_
from dread import guipages
from dread.guipages import Failure, page, route
from dread.guipages import run as run_page

GRACE = max(jobs_.GRACE, 3 * llm.STOP_WAIT)    # seconds from a stop's SIGTERM to SIGKILL for a pilot run
KEEP = 400                  # events worth a line a first look at a run gets (the newest)
FOLLOWED = 16               # runs whose folded state this process keeps (the least recently asked go first)
COMMON = ("--provider", "--budget", "--batch", "--limit", "names", "--jobs", "--thinking")
SHORT = {"name": ("--samples", "--passes", "--subsystems", "--refine"), "readable": ("--tries",), "globals": (),
         "subsystems": ("--samples",), "device": ("--samples",), "input": ("--tries",),
         "write": ("--tries", "--no-mutate", "--no-integration"),
         "review": ("--samples", "--rounds", "--margin", "--decider")}
NOTABLE = ("outcome", "settled", "stage", "batch", "budget", "warning", "error", "end")
ASKS_NOTHING = ("status", "list", "usage", "dry_run", "follow")     # modes of dread pilot that are no run
NAMED = ("proposed", "named", "accepted", "inputs")     # settled results that kept something
RUN_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[A-Za-z0-9-]{1,24}-[0-9a-f]{4}")
MONTH = re.compile(r"[0-9]{4}-[0-9]{2}")
COPIES = 64                 # other copies' names.tsv the comparison offers at most
RUNS = 200                  # runs the history lists (the newest)
COMPARES = {"name": None, "readable": "the names of the variables (the readable_names table)",
            "globals": "the names of globals and structures (the readable_names table)",
            "subsystems": "the groups' names (subsystems.tsv; --score sets each group beside its source file in "
                          "names-subsystems.tsv)",
            "device": "the device it read", "input": "the inputs it found (written cases)",
            "write": "C, which the checks accept or not", "review": "the review's decisions (review.tsv)",
            "score": "verdicts: it is a comparison itself"}

_lock = threading.Lock()
_followed = collections.OrderedDict()   # (build, run) -> Follow
_kept = {}                              # what is kept by a file's stamp: key -> (stamp, value)


def status(server):
    """The page's share of /api/status: the index's stamp (the history reloads when it changes) and the
    runs going on (the bar's count); nothing of either while the server has no target."""
    target = server.RequestHandlerClass.target
    if target is None:
        return {"index": None, "live": []}
    path = pilotlog.index_path(target.build)
    try:
        st = os.stat(path)
    except OSError:
        return {"index": None, "live": []}
    return {"index": "%d-%d" % (st.st_mtime_ns, st.st_size),
            "live": [r["run"] for r in pilotlog.runs(target.build) if r.get("status") == "running"]}


page("pilot", script="pilot.js", style="pilot.css", status=status)


# ---- what is kept by a file's stamp ----

def stamp_of(*paths):
    out = []
    for p in paths:
        try:
            st = os.stat(p)
            out.append((p, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((p, None, None))
    return tuple(out)


def kept(key, stamp, make):
    """make()'s value, kept while stamp is the same."""
    with _lock:
        got = _kept.get(key)
        if got is not None and got[0] == stamp:
            return got[1]
    value = make()
    with _lock:
        _kept[key] = (stamp, value)
        if len(_kept) > 512:                # (a long-lived server: forget the oldest)
            for k in list(_kept)[:128]:
                del _kept[k]
    return value


# ---- the providers (llm.toml): no key, no key's value ----

def config_path():
    return llm.config_path()


def read_config():
    """llm.toml's contents ({} without the file), and the error reading it, if any."""
    import tomllib
    path = config_path()
    if not os.path.exists(path):
        return {}, "no provider configuration: %s (dread.llm says how to write one)" % path
    try:
        with open(path, "rb") as f:
            return tomllib.load(f), None
    except (OSError, ValueError) as e:
        return {}, "%s: %s" % (path, e)


def key_state(spec):
    """Whether a provider's key is there, never the key: dread.llm key_status ({"needed", "set", "how": "env
    NAME" or "file PATH", what a request would take; "problem": why its key file is refused})."""
    return llm.key_status(spec, config_path(), spec.get("kind"))


def providers():
    """The providers of llm.toml as the form shows them: {"path", "default", "error", "providers": [{"name",
    "kind", "model", "prices": {"in", "out", "cache_read", "cache_write" (US dollars a million tokens),
    "batch" (a Message Batch's share of them)} or None, "batches", "cache", "key": key_state}]}."""
    config, error = read_config()
    out = []
    for name, spec in sorted((config.get("providers") or {}).items()):
        if not isinstance(spec, dict):
            continue
        kind = spec.get("kind")
        prices = llm.prices_of(spec, kind)
        batches = kind == "anthropic" and bool(spec.get("batch", True))
        out.append({"name": name, "kind": kind, "model": spec.get("model"), "batches": batches,
                    "cache": spec.get("cache_min") is not None, "key": key_state(spec),
                    "prices": None if prices is None else {
                        "in": prices[0], "out": prices[1], "cache_read": prices[2], "cache_write": prices[3],
                        "batch": float(spec.get("price_batch", llm.BATCH if kind == "anthropic" else 1))}})
    default = config.get("default") or (out[0]["name"] if out else None)
    return {"path": config_path(), "default": default, "error": error, "providers": out}


# ---- the form ----

NO_TARGET = "no target yet: the Setup page (#/setup) makes one from an ELF or a raw image, or opens one"


def need_target(request):
    """The target the page shows; Failure(409) while the server has none."""
    if request.target is None:
        raise Failure(409, NO_TARGET)
    return request.target


def form_fields(parser):
    wanted = {"--task"} | set(COMMON) | {f for fields in SHORT.values() for f in fields}
    return {f["id"]: f for f in cliforms.describe(parser)["fields"] if f["id"] in wanted}


def truths(target):
    """ELF files in the target's directory and the directories in it (original/): candidates for the true
    names of --score."""
    found = glob.glob(os.path.join(target.root, "*.elf")) + glob.glob(os.path.join(target.root, "*", "*.elf"))
    return sorted(os.path.relpath(p, target.root) for p in found)[:20]


@route("GET", "/api/pilot")
def overview(request):
    parser = cli.parser("pilot")
    fields = form_fields(parser)
    tasks = []
    for name in fields["--task"]["choices"]:
        tasks.append({"name": name, "fields": [f for f in SHORT.get(name, ()) if f in fields],
                      "budget": name != "review",
                      "why": "dread pilot refuses --budget for --task review (dread.namereview would keep a challenge "
                             "whose questions the budget held back as one without a candidate)"
                      if name == "review" else None})
    why = NO_TARGET if request.target is None else run_page.why_not(request, "pilot")
    return {"providers": providers(), "fields": fields, "tasks": tasks, "common": list(COMMON), "runnable": why is None,
            "why": why, "cwd": run_page.home(request), "defaults": run_page.defaults(request, parser),
            "image": request.image, "grace": GRACE, "setup": "setup" in guipages.PAGES,
            "target": request.target is not None, "truths": truths(request.target) if request.target else []}


def parsed(request, values):
    """(args, namespace) of the form's values as the job would run them (Run page: the target filled in);
    cliforms.FormError where dread pilot would refuse them."""
    if not isinstance(values, dict):
        raise TypeError("values is a JSON object of the pilot's form")
    args, _ = run_page.command_line(request, "pilot", values)
    return args, cliforms.check(cli.parser("pilot"), args)


def same_target(request, given):
    """Whether --target (as the job gets it, relative to the target's directory) is this page's target."""
    if not given:
        return True
    path = os.path.realpath(os.path.join(request.target.root, given))
    return path in (os.path.realpath(request.target.root), os.path.realpath(request.target.file))


@route("POST", "/api/pilot/estimate", change=False)
def estimate(request):
    """What a run of the form's values would cost (see the module): {"task", "functions" (those it takes),
    "asked" (those it asks), "already" (those it leaves; null where it cannot tell), "approximate" (asked
    is a guess: dread.pilot planned), "cost" (US dollars, or null), "per_function", "basis", "differ",
    "batch_share", "words", "how", "line" (the dry run's command line, which prints the same)}."""
    from dread import pilot
    need_target(request)
    try:
        args, ns = parsed(request, request.data.get("values"))
    except cliforms.FormError as e:
        return 400, {"error": str(e), "field": e.field}
    out = {"task": ns.task, "functions": None, "asked": None, "already": None, "approximate": False, "cost": None,
           "per_function": None, "basis": None, "differ": [], "batch_share": None,
           "line": cliforms.line("pilot", [a for a in args] + ["--dry-run"]),
           "how": "the dry run's count (dread pilot --dry-run prints the same) times the tokens a function of an "
                  "earlier run of the task at the provider's prices"}
    if not same_target(request, ns.target):
        out["words"] = "no estimate here: the form names another target than this page's"
        return out
    state = request.handler.state({"image": ns.image or request.image}).refresh()
    with state.lock:
        try:
            plan = pilot.planned(request.target, state.image, state.program, ns.task, ns.names or None, ns.limit,
                                 ns.refine, out=lambda *a: None)
        except ValueError as e:
            return 400, {"error": str(e), "field": "names"}
    if plan is None:
        out["words"] = "no estimate before the run: the questions of --task %s are known only as it runs" % ns.task
        return out
    out.update(functions=len(plan["functions"]), asked=len(plan["asked"]), already=plan["already"],
               approximate=plan["approximate"])
    try:
        provider = llm.load(ns.provider)
    except llm.ProviderError as e:
        out["words"] = "no estimate: %s" % e
        return out
    got = pilotlog.estimate(request.target.build, ns.task, provider.config, provider.kind, len(plan["asked"]),
                            batch=ns.batch > 0 and provider.can_batch, settings=pilot.settings_of(ns),
                            name=provider.name)
    out.update({k: got[k] for k in ("cost", "per_function", "basis", "differ", "batch_share", "words")})
    return out


@route("POST", "/api/pilot/start")
def start(request):
    """A run of dread pilot as a job (start_command, GRACE): its record, 400 with the command's refusal
    or for a mode that asks nothing (the Run page runs those)."""
    need_target(request)
    values = request.data.get("values")
    try:
        _, ns = parsed(request, values)
    except cliforms.FormError as e:
        return 400, {"error": str(e), "field": e.field}
    asks_nothing = [k for k in ASKS_NOTHING if getattr(ns, k, None) not in (None, False)]
    if asks_nothing:
        return 400, {"error": "--%s asks nothing and is no run to follow: the Run page runs it" % asks_nothing[0].replace(
            "_", "-"), "field": "--" + asks_nothing[0].replace("_", "-")}
    try:
        record = run_page.start_command(request, "pilot", values=values, grace=GRACE)
    except cliforms.FormError as e:
        return 400, {"error": str(e), "field": e.field}
    return run_page.shown(record)


# ---- runs and their jobs ----

def pilot_jobs(request):
    """The records of the page's jobs of dread pilot that started a process, newest first."""
    if request.jobs is None:
        return []
    return [r for r in request.jobs.list() if r.get("command") == "pilot" and r.get("pid")]


def job_of(run, records):
    """The job whose process is the run's (the same pid on this host, the run begun while the job ran), or
    None."""
    if run.get("host") not in (None, socket.gethostname()) or not run.get("pid"):
        return None
    began = float(run.get("started") or 0)
    for r in records:
        if r["pid"] == run["pid"] and (r.get("started") or 0) <= began + 1 and \
                (r.get("ended") is None or r["ended"] >= began - 1):
            return r
    return None


def run_of(build, record):
    """The id of the run a job's process began, or None (not yet, or none)."""
    if not record.get("pid") or not record.get("started"):
        return None
    for r in pilotlog.runs(build):
        if r.get("pid") == record["pid"] and r.get("host") in (None, socket.gethostname()) and \
                float(r.get("started") or 0) >= record["started"] - 1 and \
                (record.get("ended") is None or float(r.get("started") or 0) <= record["ended"] + 1):
            return r["run"]
    return None


def job_brief(record):
    if record is None:
        return None
    return {k: record.get(k) for k in ("id", "state", "pid", "started", "ended", "exit", "why", "grace", "line",
                                       "stopping")}


def stop_state(request, record, status):
    """{"can", "why"}: whether the page can stop the run (its job, running, on a server that runs jobs)."""
    if status != "running":
        return {"can": False, "why": "it has ended" if status == "ended" else "its process is gone"}
    if request.read_only:
        return {"can": False, "why": "this page is read-only (dread gui --read-only)"}
    if record is None:
        return {"can": False, "why": "it was not started from this page: stop it where it runs (Ctrl-C there, or "
                                     "kill -TERM with its pid)"}
    if request.jobs is not None and request.jobs.owner:
        return {"can": False, "why": request.jobs.owner}
    if record.get("state") == "stopping":
        return {"can": False, "why": "stopping: SIGTERM sent; the run cancels its batches and ends"}
    return {"can": record.get("state") == "running", "why": None if record.get("state") == "running" else
            "its job is %s" % record.get("state")}


def settled_of(build, entry):
    """A run's settled counts (pilotlog settled), from its index line or kept by its events file's stamp."""
    if isinstance(entry.get("settled"), dict):
        return entry["settled"], entry.get("settled_other") or {}
    path = pilotlog.events_path(build, entry["run"])
    return kept(("settled", path), stamp_of(path), lambda: pilotlog.settled(build, entry["run"]))


@route("GET", "/api/pilot/runs")
def runs(request):
    """{"runs": the newest RUNS (or ?limit=N) runs, each with "settled", "named", "refused", "unasked" and
    "job", "more": how many older ones are left out}."""
    build = need_target(request).build
    records = pilot_jobs(request)
    out = []
    now = time.time()
    found = pilotlog.runs(build)
    limit = max(1, min(int(request.query.get("limit") or RUNS), 100000))
    for entry in found[:limit]:
        counts, other = settled_of(build, entry)
        record = job_of(entry, records)
        out.append(dict(entry, settled=counts, settled_other=other, named=sum(counts.get(k, 0) for k in NAMED),
                        refused=counts.get("none", 0) + counts.get("rejected", 0) + counts.get("refused", 0),
                        unasked=counts.get("unasked", 0), job=job_brief(record),
                        seconds=entry.get("seconds") if entry.get("status") == "ended" else
                        (round(now - entry["started"], 1) if entry.get("started") else None)))
    return {"runs": out, "more": max(0, len(found) - limit)}


@route("GET", "/api/pilot/job")
def job(request):
    if request.jobs is None:
        raise Failure(404, "this server keeps no jobs")
    record = request.jobs.get(run_page.number(request.query.get("id")))
    if record.get("command") != "pilot":
        raise Failure(404, "job %s is not a run of dread pilot" % record["id"])
    log = request.jobs.log(record["id"])
    lines = log["text"].splitlines()[-40:]
    return {"job": run_page.shown(record), "run": run_of(need_target(request).build, record), "tail": "\n".join(lines)}


class Follow:
    """A run's events folded as far as they are read (pilotlog.Run) and the newest events worth a line
    (NOTABLE), kept while the page follows the run; advanced from the byte offset it has read."""

    def __init__(self, build, run):
        self.build, self.run = build, run
        self.folded = pilotlog.Run(run)
        self.offset = 0
        self.kept = collections.deque(maxlen=KEEP)
        self.seen = 0               # events worth a line read so far
        self.lock = threading.Lock()

    def advance(self):
        """Read and fold what was written since; returns (the events read, the offset they began at)."""
        records, offset = pilotlog.events(self.build, self.run, self.offset)
        begun = self.offset
        for e in records:
            self.folded.update(e)
            if e.get("event") in NOTABLE:
                self.kept.append(e)
                self.seen += 1
        self.offset = offset
        return records, begun

    def since(self, start):
        """(events worth a line written since byte start (None: the newest KEEP), how many older ones were
        left out). Called with the lock held."""
        records, begun = self.advance()
        if start is None or start <= 0:
            return list(self.kept), self.seen - len(self.kept)
        if start == begun:
            return [e for e in records if e.get("event") in NOTABLE], 0
        if start >= self.offset:
            return [], 0
        found = [e for e in pilotlog.events(self.build, self.run, start)[0] if e.get("event") in NOTABLE]
        return found[-KEEP:], max(0, len(found) - KEEP)


def followed(build, run):
    key = (build, run)
    with _lock:
        f = _followed.pop(key, None) or Follow(build, run)
        _followed[key] = f                  # (the most recently asked last)
        while len(_followed) > FOLLOWED:
            _followed.popitem(last=False)
        return f


def run_id(request, text):
    """A run id of this target's runs (checked: it becomes a path), else KeyError."""
    text = str(text or "")
    if not RUN_ID.fullmatch(text) or not os.path.isfile(pilotlog.events_path(need_target(request).build, text)):
        raise KeyError("run %s" % text)
    return text


def state_of(folded):
    st = folded.summary()
    st.pop("last", None)
    if st.get("end"):
        st["end"] = {k: v for k, v in st["end"].items() if k != "outputs"}
    return st


@route("GET", "/api/pilot/follow")
def follow(request):
    run = run_id(request, request.query.get("run"))
    build = request.target.build
    start = request.query.get("from")
    start = int(start) if start not in (None, "") else None
    f = followed(build, run)
    with f.lock:
        events, dropped = f.since(start)
        st = state_of(f.folded)
        offset = f.offset
    record = job_of(st, pilot_jobs(request))
    return {"run": run, "next": offset, "events": events, "dropped": dropped, "state": st, "job": job_brief(record),
            "stop": stop_state(request, record, st["status"])}


def jobs_of(request):
    if request.jobs is None:
        raise Failure(409, "this server runs no jobs")
    return request.jobs


@route("POST", "/api/pilot/stop")
def stop(request):
    """Stop a run (its job: SIGTERM to its processes, SIGKILL after GRACE) or a job of dread pilot that
    has not begun its run yet."""
    need_target(request)
    jobs = jobs_of(request)
    if request.data.get("run") is not None:
        run = run_id(request, request.data.get("run"))
        entry = next((r for r in pilotlog.runs(request.target.build) if r["run"] == run), None) or {}
        record = job_of(entry, pilot_jobs(request))
        why = stop_state(request, record, entry.get("status"))
        if not why["can"] and (record is None or record.get("state") != "stopping"):
            raise Failure(409, "run %s: %s" % (run, why["why"]))
    else:
        record = jobs.get(run_page.number(request.data.get("job")))
        if record.get("command") != "pilot":
            raise Failure(409, "job %s is not a run of dread pilot" % record["id"])
    try:
        return run_page.shown(jobs.stop(record["id"], why="stopped from the pilot page"))
    except jobs_.JobsError as e:
        raise Failure(409, str(e))


@route("POST", "/api/pilot/cancel-batches")
def cancel_batches(request):
    """For a run gone without an end (killed outright): its Message Batches not seen to end, cancelled by a
    job of `dread llm batches --provider P --cancel ID...` (a batch left running is billed)."""
    run = run_id(request, request.data.get("run"))
    build = request.target.build
    st = pilotlog.state(build, run)
    if st is None or st["status"] != "gone":
        raise Failure(409, "run %s is %s: a run going on cancels its own batches when stopped" % (
            run, st["status"] if st else "unknown"))
    ids = [b["id"] for b in st["waiting"] if b.get("id")]
    if not ids or not st.get("provider"):
        raise Failure(409, "run %s left no batch it did not see end" % run)
    record = run_page.start_command(request, "llm", args=["batches", "--provider", st["provider"], "--cancel"] + ids)
    return run_page.shown(record)


# ---- the cost per month (the usage ledger) ----

def months_of(path):
    """The months of the ledger's lines (their first seven characters), oldest first."""
    found = set()
    try:
        with open(path) as f:
            for line in f:
                if MONTH.match(line[:7]) and line[7:8] == "-":
                    found.add(line[:7])
    except OSError:
        pass
    return sorted(found)


def priced(summary, specs):
    """The page's rows of llm.usage_summary: each provider with its shares and what the batches and the
    prompt cache saved against its standard prices."""
    rows = []
    for name, e in sorted(summary.items()):
        spec = specs.get(e.get("priced_as") or name) or {}       # (a provider not configured: as usage_summary prices it)
        prices = llm.prices_of(spec, spec.get("kind"))
        standard = llm.cost(prices, e["input"], e["output"]) if prices is not None else None
        rows.append(dict(e, name=name, priced=e["cost"] is not None,
                         batch_share=e["batch"] / e["answers"] if e["answers"] else None,
                         cache_read_share=e["cache_read"] / e["input"] if e["input"] else None,
                         cache_write_share=e["cache_write"] / e["input"] if e["input"] else None,
                         standard=standard,
                         saved=standard - e["cost"] if standard is not None and e["cost"] is not None else None))
    return rows


@route("GET", "/api/pilot/usage")
def usage(request):
    """{"month", "months": [{"month", "cost", "answers"}], "providers": [usage_summary's entries (with
    "priced_as" for a provider not configured, priced as one of the same model) with "name", "priced",
    "batch_share", "cache_read_share", "cache_write_share", "standard", "saved"],
    "total", "saved", "unpriced": [providers without prices], "ledger", "config"} of a month (default:
    this one)."""
    month = request.query.get("month") or time.strftime("%Y-%m")
    if not MONTH.fullmatch(month):
        raise ValueError("a month is YYYY-MM, not %r" % month)
    path, config = llm.USAGE, config_path()
    stamp = stamp_of(path, config)

    def make():                             # (every month's rows at once: the ledger can hold 100,000s of lines)
        specs = (read_config()[0].get("providers") or {})
        return specs, {m: priced(llm.usage_summary(m, path=path, config_path=config), specs) for m in months_of(path)}
    specs, by_month = kept(("usage", path, config), stamp, make)
    months = [{"month": m, "cost": sum(r["cost"] or 0 for r in rows), "answers": sum(r["answers"] for r in rows)}
              for m, rows in by_month.items()]
    rows = by_month.get(month, [])
    return {"month": month, "months": months, "providers": rows, "total": sum(r["cost"] or 0 for r in rows),
            "saved": sum(r["saved"] or 0 for r in rows), "unpriced": [r["name"] for r in rows if not r["priced"]],
            "ledger": path, "config": config}


# ---- comparisons (dread pilot --score, --against, --vs) ----

def names_table(target, image):
    from dread import pilot
    path = pilot.names_path(target, image)
    rows = pilot.last_rows(pilot.read_name_rows(path)) if os.path.exists(path) else {}
    return {"path": path, "exists": os.path.exists(path), "proposals": len(rows)}


def copies(target, image):
    """Other copies' names.tsv beside the target (its parent's other directories, as a bench's replicate
    copies lie), the same image's: candidates for --against and --vs."""
    from dread import pilot
    mine = os.path.realpath(pilot.names_path(target, image))
    rel = os.path.relpath(pilot.names_path(target, image), target.root)
    parent = os.path.dirname(os.path.realpath(target.root))
    found = []
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return found
    for name in names:
        path = os.path.join(parent, name, rel)
        if os.path.isfile(path) and os.path.realpath(path) != mine:
            found.append(path)
            if len(found) >= COPIES:
                break
    return found


def compare_pair(build, first, second):
    """Whether dread pilot can compare two runs of the target, and why not: {"can": false, "why"}."""
    found = {r["run"]: r for r in pilotlog.runs(build)}
    a, b = found.get(first), found.get(second)
    if a is None or b is None:
        return {"can": False, "why": "no run %s on this target" % (first if a is None else second)}
    for r in (a, b):
        what = COMPARES.get(r.get("task"), "its results")
        if what is not None:
            return {"can": False, "why": "run %s is --task %s: dread pilot compares function names, the proposals of "
                                         "--task name (names.tsv); %s have no comparison in the pilot (no true names "
                                         "to score them against, no judge for them)" % (r["run"], r.get("task"), what)}
    return {"can": False, "why": "both are naming runs of this target: their proposals go into its one names.tsv, "
                                 "which keeps a function's last proposal, and a run started again asks only what has "
                                 "none, so the two are parts of one set of names, not two. dread pilot compares this "
                                 "target's names with the true names (--score), or with the names.tsv of a separate "
                                 "copy named apart (--against; settings over replicate copies with --vs): below."}


@route("GET", "/api/pilot/compare")
def compare(request):
    target = need_target(request)
    image = target.image(request.image)
    judges = sorted((p for p in providers()["providers"] if p["prices"] is not None),
                    key=lambda p: (p["prices"]["in"], p["prices"]["out"], p["name"]))      # (the cheapest first)
    out = {"names": names_table(target, image), "truths": truths(target), "copies": copies(target, image),
           "judges": [{"name": p["name"], "model": p["model"], "key": p["key"], "prices": p["prices"]} for p in judges],
           "pair": None, "image": image.name}
    if request.query.get("a") and request.query.get("b"):
        out["pair"] = compare_pair(target.build, request.query["a"], request.query["b"])
    return out


@route("POST", "/api/pilot/compare/start")
def compare_start(request):
    """A comparison as a job of dread pilot: --task name --score TRUTH, with --grounded or --judge P,
    --against NAMES_TSV..., --vs NAMES_TSV...; 400 for what is missing (the pilot refuses the rest)."""
    need_target(request)
    data = request.data
    mode = data.get("mode") or "score"
    if mode not in ("score", "against", "replicates"):
        raise ValueError("mode is score, against or replicates")
    truth = str(data.get("truth") or "").strip()
    if not truth:
        return 400, {"error": "the true names: an ELF with symbols, or a functions table of a copy that has them",
                     "field": "truth"}
    path = os.path.expanduser(truth)
    if not os.path.isfile(path if os.path.isabs(path) else os.path.join(request.target.root, path)):
        return 400, {"error": "no file %s" % truth, "field": "truth"}
    values = {"--task": "name", "--score": path}
    judge, provider = data.get("judge") or "", str(data.get("provider") or "")
    if judge not in ("", "grounded", "judge"):
        raise ValueError("judge is grounded, judge or empty")
    if mode != "score" and not judge:
        return 400, {"error": "--against needs a judge: --grounded (against the source) or --judge", "field": "judge"}
    if mode == "replicates" and judge != "grounded":
        return 400, {"error": "--vs needs --grounded: each run judged once against the source", "field": "judge"}
    if judge:
        if not provider:
            return 400, {"error": "a provider to judge", "field": "provider"}
        values["--" + judge] = provider
    for key, flag in (("against", "--against"), ("vs", "--vs")):
        paths = [str(p).strip() for p in data.get(key) or [] if str(p).strip()]
        if (key == "against" and mode != "score") or (key == "vs" and mode == "replicates"):
            if not paths:
                return 400, {"error": "%s: the names.tsv of the runs to compare" % flag, "field": key}
            missing = [p for p in paths if not os.path.isfile(os.path.expanduser(p))]
            if missing:
                return 400, {"error": "no file %s" % missing[0], "field": key}
            values[flag] = [os.path.expanduser(p) for p in paths]
    if data.get("names_only"):
        values["--names-only"] = True
    try:
        record = run_page.start_command(request, "pilot", values=values, grace=GRACE)
    except cliforms.FormError as e:
        return 400, {"error": str(e), "field": e.field}
    return run_page.shown(record)
