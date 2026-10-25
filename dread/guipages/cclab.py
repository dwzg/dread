"""dread gui's compiler lab page (#/cclab, key i): what dread cclab found out about a compiler by running its
probes in the emulator, from the report's own data. The page shows and starts; dread cclab computes:

- a work directory (a path typed into the page, taken as typed from the directory dread gui started in, as
  the Setup page takes paths; default ./cclab-work there, dread cclab's own default) and the compilers
  measured in it, each by its adapter: the compiler's version line, the builds measured and those that
  failed;
- a compiler's profile (#/cclab/ADAPTER): what its probes found, as dread cclab report writes it (dread.cclab
  report data): where arguments arrive and results come back (the calling convention), the types and values,
  what each operation calls or does inline (the idioms: division instructions, table jumps, helpers), what
  a function keeps across a call and the stack alignment, where the functions lie, the runtime helpers with
  the registers they change and, from the profile (profile.toml: dread.cclab.profile), the registers the
  compiler's own code reads after each, what DREAD lifts of the probes; the probes whose measurement failed
  (each error with its builds) and the builds that failed; every result with the builds that gave it;
- dread cclab's commands as jobs (dread.guipages.run start_command, in the directory dread gui started in):
  `dread cclab run --adapter A --work WORK [--build KEY]... [--probe-dir DIR]... [--lift] [--guard]` (the
  adapter a built-in name, ce:ID, relink:DIR or a TOML file; its builds from its matrix; one's own probes),
  `dread cclab doctor --adapter A --work WORK` (its tools, version and one probe built and run: for setting a
  compiler up) and `dread cclab report --work WORK --json WORK/report.json`. The page starts the report by
  itself where it is missing or stale (a run that measured something makes it stale; the report reads files
  and is quick), not while a job of dread cclab runs on the work directory;
- two compilers measured in the work directory side by side (#/cclab/compare/ORIGINAL/REBUILD): `dread cclab
  compare --work WORK ORIGINAL REBUILD --json WORK/compare-ORIGINAL-REBUILD.json` (docs/formats.md), started
  by the page as the report is, where its record is missing or older than a result of either compiler: where
  the two differ in what linking depends on (the calling convention, the types, what a call keeps, the stack
  alignment), the options of the rebuilding compiler that close a difference, where their code differs
  (helpers, inline code, saves, frames), what DREAD lifts of each one's probes.

The record is WORK/report.json, written by `dread cclab report --json` (docs/formats.md): every adapter of
the work directory, the same command typed in a terminal shows here. It is stale where a build's
results.json, build.json or failed.json, or an adapter's version.txt, is newer (by the files' times).

Routes (dread.guipages):
- GET /api/cclab?work=: {"work" (absolute), "typed", "exists", "home", "adapters": [{"name", "version",
  "builds", "measured", "failed", "reported": in the record}], "builtin": [{"name", "family", "arch",
  "builds": [keys], "matrix": {dimension: [values]}}], "path", "when", "stale", "stale_count", "job", "live",
  "auto" (the page may start the report itself, once for each "inputs": the time of the newest file it
  reads), "changes"};
- GET /api/cclab/adapter?work=&adapter=: {"work", "adapter", "record" (the adapter's data in the record, null
  where it is not there), "when", "stale", "job", "changes"};
- GET /api/cclab/line?work=&adapter=&builds=JSON&probe_dirs=JSON&lift=&guard=&action=run|doctor: {"args",
  "line", "adapter" ({"name", "arch", "family", "builds": [keys], "matrix"}: the adapter as dread cclab loads
  it), "error"}; probe_dirs: directories of one's own probes (--probe-dir), as typed;
- POST /api/cclab/run {"work", "adapter", "builds", "probe_dirs", "lift", "guard", "action": "run" or "doctor"}:
  the job;
- POST /api/cclab/report {"work"}: the report as a job;
- GET /api/cclab/compare?work=&original=&rebuild=: {"work", "original", "rebuild", "record" (the comparison's
  data, null where it is not made yet), "path", "when", "stale", "stale_count", "job", "live", "auto", "inputs",
  "changes"}, as GET /api/cclab for the report; both names must be compilers measured in the work directory;
- POST /api/cclab/compare {"work", "original", "rebuild"}: the comparison as a job.

GET /api/cclab also lists the comparisons made in the work directory ("compares": [{"original", "rebuild",
"when"}], from the records' own names).

What it does not establish: staleness by the files' times misses a change of DREAD's own code; what a
compiler's profile says holds for the code the probes contain (dread cclab's docstring); a guarded work
directory's rules (CLAUDE.md, deny rules) are dread cclab's, the page shows what the report holds and
nothing of a build's image or tables.
"""
import glob
import json
import os
import threading

from dread import jobs as jobs_
from dread.guipages import Failure, page, route

DEFAULT_WORK = "cclab-work"
STALE_SHOWN = 12
JOBS_LOOKED_AT = 300
ACTIONS = ("run", "doctor")
_lock = threading.Lock()
_cache = {}


def live_jobs(server, work=None):
    """The jobs of dread cclab queued or running (of one work directory where given), newest first."""
    jobs = getattr(server, "jobs", None)
    if jobs is None:
        return []
    out = []
    for r in jobs.list(JOBS_LOOKED_AT):
        if r.get("command") != "cclab" or r.get("state") not in jobs_.LIVE:
            continue
        argv = r.get("argv") or []
        if work is None or work_of(argv) == work:
            out.append(brief_job(r))
    return out


def status(server):
    """The page's share of /api/status: the cclab jobs going on (the bar's count)."""
    return {"live": [r["id"] for r in live_jobs(server)]}


page("cclab", script="cclab.js", style="cclab.css", status=status)


# ---- the work directory and its record ----

def brief_job(r):
    return {k: r.get(k) for k in ("id", "state", "line", "queued", "started", "ended", "exit", "why")}


def work_of(argv):
    """The --work of a dread cclab command line, absolute (as the page passes it), or None."""
    for i, a in enumerate(argv):
        if a == "--work" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--work="):
            return a[len("--work="):]
    return None


def typed(request, path):
    """A path as typed into the page: absolute, or from the directory dread gui started in; no shell, no ~."""
    path = str(path or "").strip() or DEFAULT_WORK
    return os.path.normpath(os.path.join(request.server.home, path))


def work_dir(request):
    return typed(request, request.query.get("work") or (request.data or {}).get("work"))


def record_path(work):
    return os.path.join(work, "report.json")


def mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def adapter_dirs(work):
    """{adapter name: its directory} of the adapters measured in a work directory: those with a version.txt
    or a build directory that holds a build.json, results.json or failed.json."""
    out = {}
    try:
        names = sorted(os.listdir(work))
    except OSError:
        return out
    for name in names:
        d = os.path.join(work, name)
        if not os.path.isdir(d) or name.startswith("."):
            continue
        if os.path.exists(os.path.join(d, "version.txt")) or glob.glob(os.path.join(d, "*", "build.json")) or \
                glob.glob(os.path.join(d, "*", "results.json")) or glob.glob(os.path.join(d, "*", "failed.json")):
            out[name] = d
    return out


def inputs(work):
    """The files a report reads: each adapter's version.txt, each build's results, build and failure."""
    files = []
    for d in adapter_dirs(work).values():
        files.append(os.path.join(d, "version.txt"))
        for kind in ("results.json", "build.json", "failed.json"):
            files += glob.glob(os.path.join(d, "*", kind))
    return sorted(f for f in files if os.path.exists(f))


def report_valid(data):
    return isinstance(data.get("adapters"), dict)


def compare_valid(data):
    return data.get("version") == 1 and isinstance(data.get("abi"), list) and isinstance(data.get("code"), list)


def read_record(path, valid=report_valid):
    """A record's data (the report's; with valid=compare_valid a comparison's), parsed once per version of the
    file, or None."""
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
    if not isinstance(data, dict) or not valid(data):
        data = None
    with _lock:
        if len(_cache) > 16:
            _cache.clear()
        _cache[path] = (key, data)
    return data


def job_for(request, path):
    jobs = request.jobs
    if jobs is None:
        return None
    for r in jobs.list(JOBS_LOOKED_AT):
        if path in (r.get("argv") or []):
            return brief_job(r)
    return None


def changes(request):
    from dread.guipages.run import why_not
    return why_not(request, "cclab")


def builtin():
    """The built-in adapters with their matrices."""
    from dread.cclab import adapters as ad
    out = []
    for name, path in ad.builtin().items():
        try:
            a = ad.load(path)
        except ad.AdapterError:
            continue
        out.append(adapter_brief(a))
    return out


def adapter_brief(a):
    return {"name": a.name, "family": a.family, "arch": a.arch, "builds": [k for k, _, _ in a.builds()],
            "matrix": {dim: list(choices) for dim, choices in a.matrix.items()}}


def version_of(d):
    try:
        with open(os.path.join(d, "version.txt")) as f:
            lines = f.read().strip().splitlines()
        return lines[0] if lines else ""
    except OSError:
        return None


def auto_start(request, data, when, newer, job, adapters, work):
    """Whether the page may start the report by itself: something is measured, the record is missing or
    stale, no job of dread cclab on this work directory is queued or running (a run's report waits for its
    end), none of the report failed since the newest file it reads changed, and this server runs jobs."""
    if changes(request) or not adapters or (data is not None and not newer):
        return False
    if (job and job["state"] in jobs_.LIVE) or live_jobs(request.server, work):
        return False
    if job and job["state"] != "done" and (job.get("ended") or 0) >= max([mtime(f) for f in newer] + [0]):
        return False
    return True


# ---- the routes ----

@route("GET", "/api/cclab")
def overview(request):
    work = work_dir(request)
    path = record_path(work)
    data = read_record(path)
    when = mtime(path) if data is not None else 0
    newer = [f for f in inputs(work) if mtime(f) > when] if data is not None else inputs(work)
    dirs = adapter_dirs(work)
    adapters = []
    for name, d in dirs.items():
        builds = sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(d, "*", "build.json"))
                        if not os.path.basename(os.path.dirname(p)).startswith("doctor-"))
        measured = [b for b in builds if os.path.exists(os.path.join(d, b, "results.json"))]
        failed = sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(d, "*", "failed.json"))
                        if not os.path.basename(os.path.dirname(p)).startswith("doctor-"))
        adapters.append({"name": name, "version": version_of(d), "builds": len(set(builds) | set(failed)),
                         "measured": len(measured), "failed": len(failed),
                         "reported": bool(data and name in data["adapters"])})
    job = job_for(request, path)
    return {"work": work, "typed": str(request.query.get("work") or DEFAULT_WORK), "exists": os.path.isdir(work),
            "home": request.server.home, "adapters": adapters, "builtin": builtin(), "path": path,
            "when": when or None, "stale": [os.path.relpath(f, work) for f in newer[:STALE_SHOWN]],
            "stale_count": len(newer), "job": job, "live": live_jobs(request.server, work),
            "auto": auto_start(request, data, when, newer, job, dirs, work),
            "inputs": max((mtime(f) for f in inputs(work)), default=0), "changes": changes(request),
            "compares": compares(work, dirs)}


@route("GET", "/api/cclab/adapter")
def adapter(request):
    work = work_dir(request)
    name = str(request.query.get("adapter") or "")
    path = record_path(work)
    data = read_record(path)
    record = (data or {}).get("adapters", {}).get(name)
    if record is None and name not in adapter_dirs(work):
        raise KeyError(name or "no adapter given")
    when = mtime(path) if data is not None else 0
    newer = [f for f in inputs(work) if mtime(f) > when and os.path.relpath(f, work).split(os.sep)[0] == name] \
        if data is not None else []
    return {"work": work, "adapter": name, "record": record, "when": when or None,
            "stale": [os.path.relpath(f, work) for f in newer[:STALE_SHOWN]], "stale_count": len(newer),
            "job": job_for(request, path), "live": live_jobs(request.server, work), "changes": changes(request)}


def adapter_word(request, text):
    """The adapter as the command line names it: a built-in name, ce:ID, relink:DIR (DIR as typed into the
    page, from where dread gui started) or a TOML file (likewise)."""
    from dread.cclab import adapters as ad
    text = str(text or "").strip()
    if not text:
        raise ValueError("which adapter: a built-in one (%s), ce:ID, relink:DIR or a TOML file"
                         % ", ".join(ad.builtin()))
    if text in ad.builtin() or text.startswith("ce:"):
        return text
    if text.startswith("relink:"):
        return "relink:" + os.path.normpath(os.path.join(request.server.home, text[len("relink:"):]))
    return os.path.normpath(os.path.join(request.server.home, text))


def command_args(request):
    """(args, line, the adapter's brief) of the run or doctor asked for (the request's JSON or query)."""
    from dread import cli, cliforms
    from dread.cclab import adapters as ad
    data = request.data or {}
    q = request.query
    action = str(data.get("action") or q.get("action") or "run")
    if action not in ACTIONS:
        raise ValueError("an action is run or doctor, not %r" % action)
    word = adapter_word(request, data.get("adapter") if "adapter" in data else q.get("adapter"))
    try:
        a = ad.load(word)
    except ad.AdapterError as e:
        raise ValueError(str(e))
    builds = data.get("builds") if "builds" in data else json.loads(q.get("builds") or "[]")
    if not isinstance(builds, list) or not all(isinstance(b, str) for b in builds):
        raise ValueError("builds is a list of the adapter's builds")
    keys = [k for k, _, _ in a.builds()]
    unknown = [b for b in builds if b not in keys]
    if unknown:
        raise ValueError("no build %s in %s's matrix" % (", ".join(unknown), a.name))
    flag = lambda key: data.get(key) if key in data else q.get(key) in ("1", "true", "yes")   # noqa: E731
    own = data.get("probe_dirs") if "probe_dirs" in data else json.loads(q.get("probe_dirs") or "[]")
    if isinstance(own, str):
        own = own.splitlines()
    if not isinstance(own, list):
        raise ValueError("probe_dirs is a list of directories")
    own = [os.path.normpath(os.path.join(request.server.home, str(p).strip())) for p in own if str(p).strip()]
    missing = [p for p in own if not os.path.isdir(p)]
    if missing:
        raise ValueError("no directory %s" % ", ".join(missing))
    args = [action, "--adapter", word, "--work", work_dir(request)]
    if action == "run":
        for b in keys:                      # (the matrix's order)
            if b in builds and len(builds) < len(keys):
                args += ["--build", b]
        for p in own:
            args += ["--probe-dir", p]
        if flag("lift"):
            args.append("--lift")
    if flag("guard"):
        args.append("--guard")
    cliforms.check(cli.parser("cclab"), args)
    return args, cliforms.line("cclab", args), adapter_brief(a)


@route("GET", "/api/cclab/line")
def line(request):
    try:
        args, text, brief = command_args(request)
    except ValueError as e:
        return {"args": None, "line": None, "adapter": None, "error": str(e)}
    return {"args": args, "line": text, "adapter": brief, "error": None}


def start(request, args):
    from dread.guipages.run import home, shown, start_command
    return shown(start_command(request, "cclab", args=args, cwd=home(request)))


@route("POST", "/api/cclab/run")
def run(request):
    try:
        args, _, _ = command_args(request)
    except ValueError as e:
        raise Failure(400, str(e))
    return start(request, args)


@route("POST", "/api/cclab/report")
def report(request):
    work = work_dir(request)
    if not adapter_dirs(work):
        raise Failure(409, "nothing is measured in %s yet: run an adapter first" % work)
    path = record_path(work)
    running = job_for(request, path)
    if running and running["state"] in jobs_.LIVE:
        raise Failure(409, "job %d writes this report already (%s)" % (running["id"], running["state"]))
    return start(request, ["report", "--work", work, "--json", path])


# ---- two compilers side by side ----

def compare_path(work, original, rebuild):
    """The comparison's record, beside dread cclab compare's Markdown: WORK/compare-ORIGINAL-REBUILD.json."""
    return os.path.join(work, "compare-%s-%s.json" % (original, rebuild))


def compare_inputs(work, names):
    """The files a comparison of these adapters reads: each build's results.json and build.json (not the
    doctor's builds: dread.cclab.report load)."""
    dirs = adapter_dirs(work)
    files = []
    for name in names:
        d = dirs.get(name)
        if d is None:
            continue
        for kind in ("results.json", "build.json"):
            files += [f for f in glob.glob(os.path.join(d, "*", kind))
                      if not os.path.basename(os.path.dirname(f)).startswith("doctor-")]
    return sorted(set(files))


def compares(work, dirs):
    """[{"original", "rebuild", "when"}] of the comparisons made in the work directory between compilers
    measured there, by the names their records hold (an adapter's name may hold a dash)."""
    out = []
    for path in sorted(glob.glob(os.path.join(work, "compare-*.json"))):
        data = read_record(path, valid=compare_valid)
        if data is None or data.get("original") not in dirs or data.get("rebuild") not in dirs or \
                path != compare_path(work, data["original"], data["rebuild"]):
            continue
        out.append({"original": data["original"], "rebuild": data["rebuild"], "when": mtime(path)})
    return out


def compare_names(request, body=None):
    """(work, original, rebuild) of a request: two compilers measured in its work directory, by name (the
    record's file name is made of them), else Failure 400."""
    work = work_dir(request)
    given = body if body is not None else request.query
    names = [str(given.get(k) or "").strip() for k in ("original", "rebuild")]
    dirs = adapter_dirs(work)
    for name in names:
        if name not in dirs:
            raise Failure(400, "%s is no compiler measured in %s" % (repr(name) if name else "(none given)", work))
    return work, names[0], names[1]


@route("GET", "/api/cclab/compare")
def compare_view(request):
    work, original, rebuild = compare_names(request)
    path = compare_path(work, original, rebuild)
    data = read_record(path, valid=compare_valid)
    when = mtime(path) if data is not None else 0
    files = compare_inputs(work, (original, rebuild))
    newer = [f for f in files if mtime(f) > when] if data is not None else files
    job = job_for(request, path)
    return {"work": work, "original": original, "rebuild": rebuild, "record": data, "path": path,
            "when": when or None, "stale": [os.path.relpath(f, work) for f in newer[:STALE_SHOWN]],
            "stale_count": len(newer), "job": job, "live": live_jobs(request.server, work),
            "auto": auto_start(request, data, when, newer, job, files, work),
            "inputs": max((mtime(f) for f in files), default=0), "changes": changes(request)}


@route("POST", "/api/cclab/compare")
def compare_run(request):
    work, original, rebuild = compare_names(request, body=request.data or {})
    path = compare_path(work, original, rebuild)
    running = job_for(request, path)
    if running and running["state"] in jobs_.LIVE:
        raise Failure(409, "job %d compares these already (%s)" % (running["id"], running["state"]))
    return start(request, ["compare", "--work", work, original, rebuild, "--json", path])

