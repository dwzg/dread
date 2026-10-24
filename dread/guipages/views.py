"""dread gui's views of one function beyond its listing and its C: where a failing case parts (dread
trace), why code went unrun (dread uncovered), the changes to its C no case notices (dread mutate) and
its registers (dread.regflow, dread callconv). They sit on the function's page, under its header, each
with its listing and C beside it (dread/web/views.js), and come from the tools' own output: each view
runs its tool as a job (dread.guipages.run start_command) with --json into a file of its own, and reads
that file; nothing here computes what the tool computes.

The records: BUILD/gui/views/IMAGE/
  trace/NAME/MODE-KEY.json      dread trace NAME LABEL [--lifted | --readable] --json FILE (KEY: a digest
                                of the case's label, which the record holds)
  uncovered/NAME.json           dread uncovered NAME --json FILE
  mutate/NAME.json              dread mutate NAME --json FILE [--kill]
  callconv/NAME.json            dread callconv NAME --json FILE
The same command typed in a terminal with that file shows here too. The registers' facts of
dread.regflow are not a job: the page's server keeps the analysis (gui.State's "flow"), and the view
shows RegFlow.facts, what dread regflow --json writes.

Missing or stale: a record is stale where a file the tool read is newer than the record (by the files'
times, not their contents; a change of DREAD's own code is not seen): the case files of the function
(generated, kept, written and the cases directory's), and per view the rebuilt image (trace in the mode
of dread test), the lifted, readable and naming tables and the C of src (trace's own builds), the lift
check's verdicts (uncovered), the function's source file and argued.tsv (mutate), regflow's cache
(callconv). The view says which, and offers the job again; one without a record offers it first.

Which cases fail: the test record (tests.tsv's failing column, dread test: traced in the image dread test
runs), the lift check's verdict of a function it dropped for a failing case and the last round file that
names its FAIL lines (traced as dread lift --check builds it, --lifted), the readable check's likewise
(--readable). Any other case can be traced by its label.

Routes (dread.guipages):
- GET /api/views?name=: what the function page's strip shows of each view: {"name", "trace": {"failing",
  "traced", "parts", "agrees", "stale", "job"}, "uncovered": {"missed", "regions", "stale", "job"},
  "mutation": {"rewritten", "why", "survived", "caught", "stale", "job"}, "registers": {"pending", "reads",
  "kept", "measured", "disagree", "stale", "job"}};
- GET /api/views/trace?name=[&mode=&label=]: {"name", "cases" (the failing cases: label, mode, from,
  record: its brief, job), "notes", "modes" (which rebuilt images can be traced), "chosen" ({"mode",
  "label"}), "record" (the chosen case's record: dread trace --json's, with "when", "stale", "path"), "job"};
- GET /api/views/uncovered?name=: {"name", "coverage" (gui.State coverage: which instructions the cases ran),
  "verdicts" (whether a lift check's verdicts exist), "record" (the function's entry of dread uncovered
  --json, with "totals"), "when", "stale", "job"};
- GET /api/views/mutation?name=: {"name", "rewritten", "why", "source" ({"path", "first", "last"}), "record"
  (the function's entry of dread mutate --json; "error" where the run failed), "when", "stale", "job"};
- GET /api/views/registers?name=: {"name", "regflow" (RegFlow.facts with "names", "result"; {"pending": true}
  while the analysis runs), "callconv" (the function's entry of dread callconv --json, or null; "summary"),
  "when", "stale", "job"};
- POST /api/views/run {"view": "trace" | "uncovered" | "mutation" | "callconv", "name"[, "label", "mode"]
  [, "kill"]}: the job's record (409 where this server runs no jobs; dread gui --read-only refuses it).
"""
import glob
import hashlib
import json
import os
import re
import threading

from dread.guipages import Failure, page, route

MODES = {"test": [], "lifted": ["--lifted"], "readable": ["--readable"]}
MODE_FROM = {"test": "dread test", "lifted": "dread lift --check", "readable": "dread readable --check"}
CASES_SHOWN = 60            # failing cases of one record a view lists at most
JOBS_LOOKED_AT = 300        # the newest jobs looked through for a record's job
CACHE_MAX = 256             # records kept parsed (by path, time and size)
_cache, _lock = {}, threading.Lock()


def status(server):
    jobs = getattr(server, "jobs", None)
    return {"changed": jobs.status()["changed"]} if jobs is not None else None


page("views", script="views.js", style="views.css", status=status)


# ---- files ----

def mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def read_json(path):
    """A record, parsed once per version of the file (its time and size), or None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    with _lock:
        got = _cache.get(path)
        if got is not None and got[0] == key:
            return got[1]
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None
    with _lock:
        if len(_cache) >= CACHE_MAX:
            _cache.clear()
        _cache[path] = (key, data)
    return data


def read_tsv(path):
    """A table with a header line as {first column: row}; {} without the file."""
    from dread.gui import read_tsv as rows
    return {r[next(iter(r))]: r for r in rows(path)} if os.path.exists(path) else {}


def views_dir(request):
    return os.path.join(request.target.build, "gui", "views", request.image)


def safe(name):
    """A function's name as part of a path: as it is, where it is one (a C or mangled C++ name)."""
    if not re.fullmatch(r"[A-Za-z0-9_.$@-]+", name) or name.startswith("."):
        raise ValueError("%r cannot be part of a file name" % name)
    return name


def record_path(request, view, name, mode=None, label=None):
    base = views_dir(request)
    if view == "trace":
        key = hashlib.sha1(label.encode()).hexdigest()[:12]
        return os.path.join(base, "trace", safe(name), "%s-%s.json" % (mode, key))
    return os.path.join(base, {"uncovered": "uncovered", "mutation": "mutate", "callconv": "callconv"}[view],
                        safe(name) + ".json")


def shown_path(request, path):
    """A path as a job's command line gives it: relative to the target's directory (the job's) where
    inside it."""
    rel = os.path.relpath(path, request.target.root)
    return path if rel.startswith("..") else rel


def case_inputs(request, name):
    """The files a function's cases come from: generated, kept, written, and the cases directory's."""
    image = request.target.image(request.image)
    out = [os.path.join(request.target.cases_dir(image), name + ".json"),
           os.path.join(image.extra_cases, name + ".json"), os.path.join(image.kept_cases, name + ".json")]
    out += sorted(glob.glob(os.path.join(image.cases, "**", "*.py"), recursive=True))
    return out


def inputs(request, state, view, name, mode=None):
    """The files a view's tool reads (the module's docstring: what makes a record stale)."""
    image = request.target.image(request.image)
    files = case_inputs(request, name)
    build = request.target.build
    if view == "trace":
        if mode == "test":
            files.append(os.path.join(build, "recon", image.name, "payload.bin"))
        else:
            files += [image.table(t) for t in ("functions", "lifted")] + list(state_c_files(image))
            if mode == "readable":
                files += [image.table(t) for t in ("readable", "readable_types", "readable_names")]
    elif view == "uncovered":
        files.append(os.path.join(build, "lift-check", image.name, "verdicts.tsv"))
    elif view == "mutation":
        got = state.sources.get(name)
        if got:
            files.append(got[0])
        files.append(os.path.join(os.path.dirname(image.cases), "argued.tsv"))
    elif view == "callconv":
        files.append(os.path.join(build, "regflow", image.name + ".json"))
    return files


def state_c_files(image):
    from dread.rename import c_files
    try:
        return c_files(image)
    except OSError:
        return []


def stale(request, path, files):
    """The files (as the page shows them) newer than the record at path."""
    when = mtime(path)
    return [shown_path(request, f) for f in files if mtime(f) > when]


def recent_jobs(request):
    """The newest jobs' records (JOBS_LOOKED_AT of them), once a request."""
    got = getattr(request, "_recent_jobs", None)
    if got is None:
        jobs = request.jobs
        got = request._recent_jobs = jobs.list(JOBS_LOOKED_AT) if jobs is not None else []
    return got


def job_for(request, path):
    """The newest job that writes the record at path (its command line names the file), or None."""
    want = shown_path(request, path)
    for r in recent_jobs(request):
        if want in (r.get("argv") or []) or path in (r.get("argv") or []):
            return {k: r.get(k) for k in ("id", "state", "line", "queued", "started", "ended", "exit", "why")}
    return None


def function_of(request, state):
    name = request.query.get("name") or request.data.get("name") or ""
    if name not in state.program.by_name:
        raise KeyError(name)
    return name


def with_file(request, path, data, files):
    """A record for the page: its data with when it was written, which inputs are newer, its path."""
    if data is None:
        return None
    return dict(data, when=mtime(path), stale=stale(request, path, files), path=shown_path(request, path))


# ---- which cases fail ----

def round_labels(folder):
    """{function: labels} of the FAIL lines of the last round file of a check (round-N.txt) that names
    any for the function, kept per version of the files."""
    from dread.runner import failed_labels
    paths = sorted(glob.glob(os.path.join(folder, "round-*.txt")),
                   key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0))
    key = ("rounds", folder)
    stamp = tuple((p, mtime(p)) for p in paths)
    with _lock:
        got = _cache.get(key)
        if got is not None and got[0] == stamp:
            return got[1]
    out = {}
    for path in paths:
        try:
            with open(path, errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        names = set(re.findall(r"^FAIL (\S+) \[", text, re.M))
        for name in names:
            labels = failed_labels(name, [text])
            if labels:
                out[name] = labels
    with _lock:
        _cache[key] = (stamp, out)
    return out


def failing_cases(request, state, name):
    """([{"label", "mode", "from"}], notes) of the cases the records say fail (the module's docstring)."""
    from dread.runner import failed_labels
    cases, notes = [], []
    test = state.tests.get(name)
    if test and int(test.get("failed") or 0):
        try:
            labels = json.loads(test.get("failing") or "[]")
        except ValueError:
            labels = []
        cases += [{"label": label, "mode": "test", "from": MODE_FROM["test"]} for label in labels[:CASES_SHOWN]]
        if not labels:
            notes.append("%s of %s cases fail in the last dread test, whose tests.tsv names none of them (it was "
                         "written before it had a failing column): run dread test again"
                         % (test.get("failed"), test.get("cases")))
    build = request.target.build
    for mode, verdicts, folder in (("lifted", state.verdicts, os.path.join(build, "lift-check", request.image)),
                                   ("readable", read_tsv(os.path.join(build, "readable-check", request.image,
                                                                      "verdicts.tsv")),
                                    os.path.join(build, "readable-check", request.image))):
        v = verdicts.get(name)
        if not v or v.get("verdict") not in ("dropped", "lifted"):
            continue
        first = failed_labels(name, [v.get("reason", "").split(" | ")[0]])
        if not first:
            continue
        labels = first + [x for x in round_labels(folder).get(name, []) if x not in first]
        cases += [{"label": label, "mode": mode, "from": MODE_FROM[mode]} for label in labels[:CASES_SHOWN]]
    return cases, notes


def trace_records(request, name):
    """{(mode, label): (path, record)} of the function's trace records."""
    out = {}
    for path in sorted(glob.glob(os.path.join(views_dir(request), "trace", safe(name), "*.json"))):
        data = read_json(path)
        if isinstance(data, dict) and data.get("mode") in MODES and isinstance(data.get("label"), str):
            out[(data["mode"], data["label"])] = (path, data)
    return out


def brief(record):
    """What a list of cases shows of a trace record."""
    first = record.get("first") or {}
    return {"agrees": bool(record.get("agrees")), "exit": record.get("exit"), "error": record.get("error"),
            "what": first.get("what"), "kind": first.get("kind"), "when": record.get("when"),
            "stale": record.get("stale")}


# ---- the views ----

@route("GET", "/api/views/trace")
def trace_view(request):
    state = request.state()
    with state.lock:
        name = function_of(request, state)
        cases, notes = failing_cases(request, state, name)
    records = trace_records(request, name)
    modes = trace_modes(request, state)
    for (mode, label), (path, data) in records.items():         # (traced cases the records do not name)
        if not any(c["mode"] == mode and c["label"] == label for c in cases):
            cases.append({"label": label, "mode": mode, "from": None})
    for c in cases:
        got = records.get((c["mode"], c["label"]))
        path = record_path(request, "trace", name, c["mode"], c["label"])
        c["record"] = brief(with_file(request, got[0], got[1], inputs(request, state, "trace", name, c["mode"]))) \
            if got else None
        c["job"] = job_for(request, path)
    mode, label = request.query.get("mode"), request.query.get("label")
    if mode not in MODES or not label:
        pick = next((c for c in cases if c["record"]), None) or (cases[0] if cases else None)
        mode, label = (pick["mode"], pick["label"]) if pick else (None, None)
    record, job = None, None
    if mode is not None:
        path = record_path(request, "trace", name, mode, label)
        record = with_file(request, path, read_json(path), inputs(request, state, "trace", name, mode))
        job = job_for(request, path)
    return {"name": name, "cases": cases, "notes": notes, "modes": modes, "chosen": {"mode": mode, "label": label},
            "record": record, "job": job}


def trace_modes(request, state):
    """Which rebuilt images a trace can run on: dread test's where dread recon built one, a lift check's
    and a readable check's where the image's architecture has a lifter (the trace builds those itself)."""
    image = request.target.image(request.image)
    lifts = getattr(state, "lifter", None) is not None or os.path.exists(image.table("lifted"))
    return {"test": os.path.exists(os.path.join(request.target.build, "recon", image.name, "payload.bin")),
            "lifted": lifts, "readable": lifts}


@route("GET", "/api/views/uncovered")
def uncovered_view(request):
    state = request.state()
    with state.lock:
        name = function_of(request, state)
        coverage = state.coverage(state.program.by_name[name])
    path = record_path(request, "uncovered", name)
    data = read_json(path)
    record = None
    if isinstance(data, dict):
        entry = (data.get("functions") or {}).get(name)
        record = dict(entry, totals=data.get("totals")) if entry else {"absent": True, "totals": data.get("totals")}
    files = inputs(request, state, "uncovered", name)
    verdicts = os.path.exists(files[-1])
    return {"name": name, "coverage": coverage, "verdicts": verdicts, "record": record,
            "when": mtime(path) or None, "stale": stale(request, path, files) if data is not None else [],
            "job": job_for(request, path)}


@route("GET", "/api/views/mutation")
def mutation_view(request):
    state = request.state()
    with state.lock:
        name = function_of(request, state)
        source = state.sources.get(name)
    path = record_path(request, "mutation", name)
    data = read_json(path)
    record = None
    if isinstance(data, dict):
        record = (data.get("functions") or {}).get(name)
        if record is None:
            record = {"error": data.get("error") or "the run left no entry for %s" % name}
    why = None if source else "dread mutate changes C that src defines: %s is not written in C there (lifted and " \
                              "readable C are checked by their cases and dread lift --check)" % name
    where = {"path": shown_path(request, source[0]), "first": source[1], "last": source[2]} if source else None
    return {"name": name, "rewritten": bool(source), "why": why, "source": where,
            "record": record, "when": mtime(path) or None,
            "stale": stale(request, path, inputs(request, state, "mutation", name)) if data is not None else [],
            "job": job_for(request, path)}


@route("GET", "/api/views/registers")
def registers_view(request):
    state = request.state()
    with state.lock:
        name = function_of(request, state)
        flow = state.ready("flow")
        pending = "flow" not in state.analyses
        failed = state.analyses.get("flow") if isinstance(state.analyses.get("flow"), Exception) else None
        abi = state.target.abi
    if flow is not None:
        facts = dict(flow.facts(name), names=list(flow.names), result=abi.result[0] if abi.result else None)
    else:
        facts = {"pending": pending, "error": str(failed) if failed else None}
    path = record_path(request, "callconv", name)
    data = read_json(path)
    measured = None
    if isinstance(data, dict):
        measured = (data.get("functions") or {}).get(name) or {"absent": True}
        measured = dict(measured, summary=data.get("summary"))
    return {"name": name, "regflow": facts, "callconv": measured, "when": mtime(path) or None,
            "stale": stale(request, path, inputs(request, state, "callconv", name)) if data is not None else [],
            "job": job_for(request, path)}


@route("GET", "/api/views")
def summary(request):
    """The strip's share of each view (cheap: the records' files, the state's tables)."""
    state = request.state()
    with state.lock:
        name = function_of(request, state)
        cases, _ = failing_cases(request, state, name)
        coverage = state.coverage(state.program.by_name[name])
        source = state.sources.get(name)
        flow = state.ready("flow")
        pending = "flow" not in state.analyses
    records = trace_records(request, name)
    parts = sum(1 for _, data in records.values() if data.get("exit") == 1)
    agrees = sum(1 for _, data in records.values() if data.get("exit") == 0)
    out = {"name": name}
    out["trace"] = {"failing": len(cases), "traced": len(records), "parts": parts, "agrees": agrees,
                    "job": live_job(request, os.path.join(views_dir(request), "trace", safe(name)))}
    path = record_path(request, "uncovered", name)
    data = read_json(path)
    entry = (data.get("functions") or {}).get(name) if isinstance(data, dict) else None
    out["uncovered"] = {"missed": len(coverage["missed"]) if coverage else None,
                        "regions": len(entry["regions"]) if entry else None, "job": job_for(request, path),
                        "stale": bool(data is not None and stale(request, path, inputs(request, state, "uncovered",
                                                                                         name)))}
    path = record_path(request, "mutation", name)
    data = read_json(path)
    entry = (data.get("functions") or {}).get(name) if isinstance(data, dict) else None
    out["mutation"] = {"rewritten": bool(source), "survived": entry["survived"] if entry else None,
                       "caught": entry["caught"] if entry else None, "job": job_for(request, path),
                       "error": data.get("error") if isinstance(data, dict) and not entry else None,
                       "stale": bool(data is not None and source and stale(request, path, inputs(
                           request, state, "mutation", name)))}
    path = record_path(request, "callconv", name)
    data = read_json(path)
    entry = (data.get("functions") or {}).get(name) if isinstance(data, dict) else None
    out["registers"] = {"pending": pending and flow is None,
                        "reads": flow.read_regs(name) if flow is not None else None,
                        "kept": flow.kept_regs(name) if flow is not None else None,
                        "measured": entry is not None, "disagree": len(entry.get("found") or {}) if entry else None,
                        "job": job_for(request, path)}
    return out


def live_job(request, folder):
    """The newest job that writes a record in folder and has not ended, or None."""
    want = (shown_path(request, folder) + os.sep, folder + os.sep)
    for r in recent_jobs(request):
        if r.get("state") in ("queued", "running", "stopping") and any(
                str(a).startswith(want) for a in r.get("argv") or []):
            return {k: r.get(k) for k in ("id", "state", "line")}
    return None


# ---- jobs ----

@route("POST", "/api/views/run")
def run(request):
    from dread.guipages.run import shown, start_command
    state = request.state()
    view = str(request.data.get("view", ""))
    with state.lock:
        name = function_of(request, state)
        rewritten = name in state.sources
    if view == "trace":
        mode, label = str(request.data.get("mode", "test")), str(request.data.get("label", "")).strip()
        if mode not in MODES:
            raise ValueError("a trace's mode is one of %s" % ", ".join(MODES))
        if not label:
            raise ValueError("which case: its label, as dread test prints it (generated 3)")
        path = record_path(request, "trace", name, mode, label)
        args = MODES[mode] + ["--json", shown_path(request, path)]
        tail = ["--", name, label] if label.startswith("-") else [name, label]   # (a label is never an option)
        command = "trace"
    elif view == "uncovered":
        path = record_path(request, "uncovered", name)
        args, tail, command = ["--json", shown_path(request, path)], [name], "uncovered"
    elif view == "mutation":
        if not rewritten:
            raise Failure(409, "dread mutate changes C that src defines: %s is not written in C there" % name)
        path = record_path(request, "mutation", name)
        args = ["--json", shown_path(request, path)] + (["--kill"] if request.data.get("kill") else [])
        tail, command = [name], "mutate"
    elif view == "callconv":
        path = record_path(request, "callconv", name)
        args, tail, command = ["--json", shown_path(request, path)], [name], "callconv"
    else:
        raise ValueError("a view is trace, uncovered, mutation or callconv, not %r" % view)
    running = job_for(request, path)
    if running and running["state"] in ("queued", "running", "stopping"):
        raise Failure(409, "job %d writes this record already (%s)" % (running["id"], running["state"]))
    from dread import cli
    from dread.guipages.run import defaults
    for flag, value in defaults(request, cli.parser(command)).items():     # (--target, and --image where
        args += [flag, value]                                               # the page shows another one)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return shown(start_command(request, command, args=tail + args if tail[0] != "--" else args + tail))
