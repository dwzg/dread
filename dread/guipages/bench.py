"""dread gui's bench page (#/bench, key m): what DREAD made of a real project built in many ways (dread
bench), from the report's own data. The page shows and starts; dread bench computes:

- the builds of a recipe as a table: each build's figures as `dread bench report` shows them (dread.bench
  FIGURES: functions, lifted, readable, Ghidra's starts, never-returning functions, registers against
  regflow, the libraries' toolchain, the seconds of each step), its compiler (kind, release, the flags'
  optimisation and cpu) and whether the recipe changed since it was built; sorted and filtered on the page;
- each build's trend of one figure over its measurements (BUILD/history, the same figures of each);
- a build's whole measurement (#/bench/RECIPE/BUILD): every figure against the measurement before, the
  trend, what stops the rest, why the rest is not lifted, what it gained and lost against the measurement
  before, the registers measured against regflow, the readable C's figures; the build opened as the page's
  target (dread.gui Server.open_target, as the Setup page opens one);
- dread bench's commands as jobs (dread.guipages.run start_command, in DREAD's directory, where a recipe's
  name finds bench/NAME.toml): `dread bench report RECIPE --json BENCH/RECIPE/report.json`, and `dread bench
  measure RECIPE BUILD... [--steps ...] --json ...` (measure ends with the report, so one job does both).
  The page starts the report by itself where it is missing or stale (it reads files and is quick), not
  while a job of dread bench runs on the recipe; a measurement only on a button, with an estimate from the
  seconds the builds' last measurement took.

The record is BENCH/RECIPE/report.json (BENCH: $DREAD_BENCH, else ~/dread-bench, as dread bench takes it),
written by `dread bench report --json` (docs/formats.md): the same command typed in a terminal shows here.
It is stale where a build's metrics.json, build.json or history, or the recipe, is newer (by the files'
times). The recipes are DREAD's bench/*.toml; a directory of BENCH with measured builds and no recipe here
shows its report where one was written, and starts nothing (dread bench needs the recipe).

Routes (dread.guipages):
- GET /api/bench: {"bench", "recipes": [{"name", "file", "builds", "measured", "newest", "report": {"when",
  "stale"} or null}], "steps" (dread.bench STEPS), "changes" (why this server starts no job, or null)};
- GET /api/bench/report?recipe=[&trend=KEY]: {"recipe", "file", "bench", "path", "record" (the report's data,
  each build without its metrics and history, with "trend": [[when, value], ...] of figure KEY, default
  lifted), "when", "stale" (the newer files, at most STALE_SHOWN, and "stale_count"), "job" (the newest job
  that writes it), "live" (the bench jobs of this recipe queued or running), "auto" (the page may start the
  report itself, once for each "inputs": the time of the newest file it reads, so that a file dated in the
  future, which keeps the record stale, starts no report at every look), "why" (why no job can run for
  this recipe), "changes"};
- GET /api/bench/build?recipe=&build=: the build's entry of the record, with its metrics and history;
- GET /api/bench/measure-line?recipe=&builds=JSON&steps=JSON: {"args", "line", "estimate" (seconds: the
  chosen steps' seconds in the builds' last measurements, their metrics.json; null where none has any),
  "unknown" (the steps chosen that some build's last measurement has no time for: the estimate leaves them
  out), "error"};
- POST /api/bench/report {"recipe"}: the report as a job: its record;
- POST /api/bench/measure {"recipe", "builds", "steps"}: the measurement as a job: its record;
- POST /api/bench/open {"recipe", "build"}: the server shows that build as its target: {"target"}; 409
  with the reason where it does not (a job of the page still running, no target in that directory).

What it does not establish: staleness by the files' times misses a change of DREAD's own code (a report
made by older code reads the same metrics); the estimate is the last measurement's seconds, which depend
on what else the machine did then; a recipe outside DREAD's bench/ has no jobs here (the Run page's form
of dread bench takes a recipe's path).
"""
import glob
import json
import os
import threading

from dread import bench as bench_
from dread import cli
from dread import jobs as jobs_
from dread.guipages import Failure, page, route

STALE_SHOWN = 12            # newer files an answer names at most
JOBS_LOOKED_AT = 300        # the newest jobs looked through for a record's job
_lock = threading.Lock()
_cache = {}                 # path -> ((mtime_ns, size), data)


def live_jobs(server, recipe=None):
    """The jobs of dread bench queued or running (of one recipe where given), newest first."""
    jobs = getattr(server, "jobs", None)
    if jobs is None:
        return []
    out = []
    for r in jobs.list(JOBS_LOOKED_AT):
        if r.get("command") != "bench" or r.get("state") not in jobs_.LIVE:
            continue
        args = (r.get("argv") or [])[4:]
        if recipe is None or recipe_of(args) == recipe:
            out.append(brief_job(r))
    return out


def status(server):
    """The page's share of /api/status: the bench jobs going on (the bar's count)."""
    return {"live": [r["id"] for r in live_jobs(server)]}


page("bench", script="bench.js", style="bench.css", status=status)


# ---- the recipes and their records ----

def recipe_files():
    """{name: path} of DREAD's recipes (bench/*.toml)."""
    out = {}
    for path in sorted(glob.glob(os.path.join(bench_.ROOT, "bench", "*.toml"))):
        out[os.path.basename(path)[:-len(".toml")]] = path
    return out


def load_recipe(name):
    files = recipe_files()
    if name not in files:
        return None
    try:
        return bench_.load(files[name])
    except bench_.BenchError:
        return None


def measured_dirs(folder):
    """The build directories of a recipe's directory in BENCH that hold a measurement."""
    return sorted(os.path.dirname(p) for p in glob.glob(os.path.join(folder, "*", "metrics.json")))


def record_path(name):
    return os.path.join(bench_.bench_dir(), name, "report.json")


def mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def inputs(name, recipe):
    """The files a report reads: each build's metrics.json, build.json and history, and the recipe."""
    folder = os.path.join(bench_.bench_dir(), name)
    files = [recipe["_file"]] if recipe else []
    for where in measured_dirs(folder):
        files += [os.path.join(where, "metrics.json"), os.path.join(where, "build.json")]
        files += sorted(glob.glob(os.path.join(where, "history", "metrics-*.json")))
    return files


def stale(name, recipe, when):
    return [f for f in inputs(name, recipe) if mtime(f) > when]




def read_record(path):
    """The report's data, parsed once per version of the file, or None."""
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
    if not isinstance(data, dict) or not isinstance(data.get("builds"), list):
        data = None
    with _lock:
        if len(_cache) > 16:
            _cache.clear()
        _cache[path] = (key, data)
    return data


def recipe_of(args):
    """The recipe a dread bench command line names (its second word), as a name."""
    if len(args) < 2:
        return None
    word = args[1]
    return os.path.basename(word)[:-len(".toml")] if word.endswith(".toml") else word


def brief_job(r):
    return {k: r.get(k) for k in ("id", "state", "line", "queued", "started", "ended", "exit", "why")}


def job_for(request, path):
    """The newest job whose command line names the record at path, or None."""
    jobs = request.jobs
    if jobs is None:
        return None
    for r in jobs.list(JOBS_LOOKED_AT):
        if path in (r.get("argv") or []):
            return brief_job(r)
    return None


def changes(request):
    """Why this server starts no job (read-only, beyond this machine, another server's jobs), or None."""
    from dread.guipages.run import why_not
    return why_not(request, "bench")


def why_no_job(name, recipe):
    """Why dread bench cannot run for a recipe from this page, or None."""
    if recipe is None:
        return "no recipe %s in DREAD's bench/: dread bench needs the recipe (the Run page's form of dread bench " \
               "takes a recipe's path)" % name
    return None


def recipe_word(name):
    """How a job names the recipe: its name where dread bench finds it by that name from DREAD's directory,
    where the job runs (bench/NAME.toml, and no file or directory NAME there); else the recipe's path."""
    path = recipe_files()[name]
    own = os.path.join(bench_.ROOT, "bench", name + ".toml")
    if os.path.abspath(path) == os.path.abspath(own) and not os.path.exists(os.path.join(bench_.ROOT, name)):
        return name
    return path


def asked_recipe(request):
    name = str(request.query.get("recipe") or request.data.get("recipe") or "")
    if not name or "/" in name or name.startswith(".") or name not in known_names():
        raise KeyError(name or "no recipe given")
    return name


def known_names():
    names = set(recipe_files())
    try:
        for d in os.listdir(bench_.bench_dir()):
            if d not in names and d != "src" and measured_dirs(os.path.join(bench_.bench_dir(), d)):
                names.add(d)
    except OSError:
        pass
    return names


# ---- the routes ----

@route("GET", "/api/bench")
def overview(request):
    out = []
    files = recipe_files()
    for name in sorted(known_names()):
        recipe = load_recipe(name)
        folder = os.path.join(bench_.bench_dir(), name)
        dirs = measured_dirs(folder)
        path = record_path(name)
        when = mtime(path)
        out.append({"name": name, "file": files.get(name), "builds": len(recipe["builds"]) if recipe else None,
                    "measured": len(dirs), "newest": max((mtime(os.path.join(d, "metrics.json")) for d in dirs),
                                                        default=None),
                    "report": {"when": when, "stale": len(stale(name, recipe, when))} if when else None})
    return {"bench": bench_.bench_dir(), "recipes": out, "steps": list(bench_.STEPS), "changes": changes(request)}


def auto_start(request, name, when, newer, job):
    """Whether the page may start the report by itself: the record is missing or stale, no job of dread
    bench on the recipe is queued or running (a measurement ends with the report), none of the report failed
    since the newest file it reads changed, and this server runs jobs."""
    if changes(request) or (when and not newer):
        return False
    if (job and job["state"] in jobs_.LIVE) or live_jobs(request.server, name):
        return False
    if job and job["state"] not in ("done",) and (job.get("ended") or 0) >= max([mtime(f) for f in newer] + [0]):
        return False            # (it failed on these files: its log says why; a button runs it again)
    return True


@route("GET", "/api/bench/report")
def report(request):
    name = asked_recipe(request)
    recipe = load_recipe(name)
    path = record_path(name)
    data = read_record(path)
    when = mtime(path) if data is not None else 0
    times = {f: mtime(f) for f in inputs(name, recipe)}
    newer = [f for f, t in times.items() if t > when] if data is not None else []
    trend = str(request.query.get("trend") or "lifted")
    record = None
    if data is not None:
        record = {k: v for k, v in data.items() if k != "builds"}
        record["builds"] = [dict({k: v for k, v in b.items() if k not in ("metrics", "history")},
                                 trend=[[h.get("when"), (h.get("figures") or {}).get(trend)]
                                        for h in b.get("history") or []])
                            for b in data["builds"] if isinstance(b, dict)]
        record["trend"] = trend
    job = job_for(request, path)
    why = why_no_job(name, recipe)
    return {"recipe": name, "file": recipe["_file"] if recipe else None,
            "bench": os.path.join(bench_.bench_dir(), name),
            "path": path, "record": record, "when": when or None, "stale": newer[:STALE_SHOWN],
            "stale_count": len(newer), "job": job, "live": live_jobs(request.server, name),
            "auto": why is None and auto_start(request, name, when, newer, job),
            "inputs": max(times.values(), default=0), "why": why, "changes": changes(request),
            "builds": [b["name"] for b in recipe["builds"]] if recipe else None, "steps": list(bench_.STEPS)}


@route("GET", "/api/bench/build")
def build(request):
    name = asked_recipe(request)
    wanted = str(request.query.get("build") or "")
    data = read_record(record_path(name))
    if data is None:
        raise Failure(404, "no report of %s yet: dread bench report %s --json %s" % (name, name, record_path(name)))
    for b in data["builds"]:
        if isinstance(b, dict) and b.get("build") == wanted:
            recipe = load_recipe(name)
            return dict(b, recipe=name, figures_known=data.get("figures"), categories=data.get("categories"),
                        open=why_not_open(request, name, wanted), why=why_no_job(name, recipe),
                        changes=changes(request),
                        steps=list(bench_.STEPS))
    raise KeyError(wanted)


def build_directory(name, build):
    """BENCH/RECIPE/BUILD of a build's name (a plain name: never a path of the record's)."""
    if not build or "/" in build or os.sep in build or build.startswith("."):
        raise KeyError(build or "no build given")
    return os.path.join(bench_.bench_dir(), name, build)


def why_not_open(request, name, build):
    """Why the build cannot be opened as the page's target, or None (the server's checks come on the open)."""
    if request.read_only:
        return "this page is read-only (dread gui --read-only): it opens no other target"
    if not request.server.local:
        return "a server that listens beyond this machine opens no other target"
    where = build_directory(name, build)
    if not os.path.exists(os.path.join(where, "dread.toml")):
        return "no dread.toml in %s: the build is not there (only its figures)" % where
    target = request.target
    if target is not None and os.path.abspath(target.root) == os.path.abspath(where):
        return "this is the target shown"
    return None


def measure_args(request, name, recipe):
    """(args, line, builds, steps) of the measurement asked for: the builds named (at least one: dread bench
    measures every build where none is named, hours on a large recipe) and the steps (--steps left out where
    every one is chosen)."""
    builds = request.data.get("builds") if request.data else None
    steps = request.data.get("steps") if request.data else None
    if builds is None:
        builds = json.loads(request.query.get("builds") or "[]")
    if steps is None:
        steps = json.loads(request.query.get("steps") or "null")
    if not isinstance(builds, list) or not all(isinstance(b, str) for b in builds):
        raise ValueError("builds is a list of the recipe's builds")
    if not builds:
        raise ValueError("choose the builds to measure (each takes minutes: the lift check runs every function's "
                         "cases)")
    known = [b["name"] for b in recipe["builds"]]
    unknown = [b for b in builds if b not in known]
    if unknown:
        raise ValueError("no build %s in %s" % (", ".join(unknown), name))
    if steps is None:
        steps = list(bench_.STEPS)
    if not isinstance(steps, list) or not steps or any(s not in bench_.STEPS for s in steps):
        raise ValueError("steps: one or more of %s" % ", ".join(bench_.STEPS))
    steps = [s for s in bench_.STEPS if s in steps]
    args = ["measure", recipe_word(name)] + [b for b in known if b in builds]
    if steps != list(bench_.STEPS):
        args += ["--steps", ",".join(steps)]
    args += ["--json", record_path(name)]
    from dread import cliforms
    cliforms.check(cli.parser("bench"), args)
    return args, cliforms.line("bench", args), [b for b in known if b in builds], steps


def last_figures(name, build):
    """The figures (dread.bench figures) of a build's last measurement, its metrics.json; {} without one."""
    try:
        with open(os.path.join(bench_.bench_dir(), name, build, "metrics.json")) as f:
            return bench_.figures(json.load(f))
    except (OSError, ValueError, AttributeError):
        return {}


@route("GET", "/api/bench/measure-line")
def measure_line(request):
    name = asked_recipe(request)
    recipe = load_recipe(name)
    why = why_no_job(name, recipe)
    if why:
        return {"args": None, "line": None, "estimate": None, "unknown": [], "error": why}
    try:
        args, line, builds, steps = measure_args(request, name, recipe)
    except ValueError as e:
        return {"args": None, "line": None, "estimate": None, "unknown": [], "error": str(e)}
    last = {b: last_figures(name, b) for b in builds}
    total, known, unknown = 0.0, 0, []
    for s in steps:
        for b in builds:
            seconds = last.get(b, {}).get("seconds_" + s)
            if seconds is None:
                if s not in unknown:
                    unknown.append(s)
            else:
                total += seconds
                known += 1
    return {"args": args, "line": line, "estimate": round(total, 1) if known else None, "unknown": unknown,
            "error": None}


def start(request, args, grace=None):
    from dread.guipages.run import shown, start_command
    return shown(start_command(request, "bench", args=args, cwd=bench_.ROOT, grace=grace))


@route("POST", "/api/bench/report")
def report_start(request):
    name = asked_recipe(request)
    recipe = load_recipe(name)
    why = why_no_job(name, recipe)
    if why:
        raise Failure(409, why)
    path = record_path(name)
    running = job_for(request, path)
    if running and running["state"] in jobs_.LIVE:
        raise Failure(409, "job %d writes this report already (%s)" % (running["id"], running["state"]))
    return start(request, ["report", recipe_word(name), "--json", path])


@route("POST", "/api/bench/measure")
def measure_start(request):
    name = asked_recipe(request)
    recipe = load_recipe(name)
    why = why_no_job(name, recipe)
    if why:
        raise Failure(409, why)
    args, _, _, _ = measure_args(request, name, recipe)
    return start(request, args)


@route("POST", "/api/bench/open")
def open_build(request):
    from dread import target as tgt
    name = asked_recipe(request)
    where = build_directory(name, str(request.data.get("build") or ""))
    if not os.path.isdir(where):
        raise KeyError(where)
    try:
        request.server.open_target(where)
    except tgt.TargetError as e:
        raise Failure(409, str(e))
    return {"target": request.handler.target_brief()}
