"""dread gui's leak check page (#/leakcheck, key w): dread leakcheck run as a job against a tree, with the
private project's tables, words and allow list given by their paths, and its hits listed with their file,
their lines and the string each matched.

What the page may show of the private files is the strings the hits matched, nothing else: the server never
opens the tables, the words file or the allow list (it asks only whether a typed path exists); the check
reads them in its own process and writes what it found with `--json` (dread.leakcheck: each hit's file,
lines, string and whether it is allowed; the history's hits with their commits; the commit authors; how many
names, addresses and words it looked for; the paths as given). That record and the job's log (the tool's
HIT lines and its summary) are all the page reads.

The paths typed into the page are kept nowhere in a repository: the jobs of this page are its own (dread.gui
Server.jobs_of_page), with their records and logs in the user's state directory
($XDG_STATE_HOME/dread/gui/leakcheck/jobs, else ~/.local/state/...), not in the target's, and each check's
record beside them in checks/KEY.json (KEY: a digest of the check's tree, paths and options; a check run
again replaces its record), written with mode 600 by dread leakcheck. The form starts from the newest
check's paths. A typed path is taken as typed from the directory dread gui started in (no shell, no ~);
the tree defaults to the DREAD the server runs from. One server at a time runs these jobs (their directory's
lock); another one shows them and starts none.

Routes (dread.guipages):
- GET /api/leakcheck: {"where" (the state directory), "tree" (the default tree), "home", "checks": [{"key",
  "when", "tree", "tables", "words", "allow", "history", "min", "looked_for", "not_allowed", "allowed",
  "hits", "history_hits", "exit", "job"}] newest first (counts, no hit), "last" (the newest check's form
  values), "live" (the jobs queued or running), "changes" (why this server starts no check, or null)};
- GET /api/leakcheck/check?key=: the check's record (dread leakcheck --json's) with "key", "when", "job";
- GET /api/leakcheck/line?values=JSON: {"args", "line", "key", "missing": [typed paths that do not exist],
  "error"}: the command line of the form's values;
- POST /api/leakcheck/run {"values"}: the check as a job of this page: its record ("key": its check);
- POST /api/leakcheck/stop {"id"}: the job's record;
- GET /api/leakcheck/job?id=[&from=BYTE]: {"job", "log"} (dread.jobs log).

What it does not establish: that a tree without hits holds nothing private (the check finds the names of
--min characters and more that are not generic, addresses of five hex digits and more, and the words'
expressions; dread leakcheck's docstring); a record names the private files' paths, which the page shows
back to whoever can open it (the server's safety: 127.0.0.1, its token for every change).
"""
import glob
import hashlib
import json
import os
import threading

from dread import cli, cliforms
from dread import jobs as jobs_
from dread.guipages import Failure, page, route

MIN = 6                     # dread leakcheck's default --min
_lock = threading.Lock()
_cache = {}                 # path -> ((mtime_ns, size), record)


def where():
    """The page's state directory: $XDG_STATE_HOME/dread/gui/leakcheck, else ~/.local/state/dread/gui/leakcheck."""
    state = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(state, "dread", "gui", "leakcheck")


def checks_dir():
    return os.path.join(where(), "checks")


def leak_jobs(server):
    """This page's jobs (dread.gui Server.jobs_of_page), or None on a server that keeps none."""
    get = getattr(server, "jobs_of_page", None)
    if get is None:
        return None
    jobs = get("leakcheck", os.path.join(where(), "jobs"))
    if jobs.owner and jobs.owner.startswith("another dread gui"):
        jobs.owner = jobs.owner.replace("the jobs of this target", "the leak checks")
    return jobs


def brief_job(r):
    return {k: r.get(k) for k in ("id", "state", "line", "queued", "started", "ended", "exit", "why")}


def live_jobs(server):
    jobs = leak_jobs(server)
    return [brief_job(r) for r in jobs.list(50) if r.get("state") in jobs_.LIVE] if jobs is not None else []


def status(server):
    """The page's share of /api/status: its checks going on (the bar's count). Nothing is made for it: a
    server whose page was never opened has no jobs of this page yet."""
    jobs = (getattr(server, "page_jobs", None) or {}).get("leakcheck")
    return {"live": [r["id"] for r in jobs.list(50) if r.get("state") in jobs_.LIVE] if jobs is not None else []}


page("leakcheck", script="leakcheck.js", style="leakcheck.css", status=status)


# ---- the checks ----

def read_record(path):
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
    if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
        data = None
    with _lock:
        if len(_cache) > 64:
            _cache.clear()
        _cache[path] = (key, data)
    return data


def job_of(server, path):
    jobs = leak_jobs(server)
    if jobs is None:
        return None
    for r in jobs.list(200):
        if path in (r.get("argv") or []):
            return brief_job(r)
    return None


def form_values(record):
    """A record's check as the form's values."""
    return {"tree": record.get("tree"), "tables": list(record.get("tables") or []), "words": record.get("words") or "",
            "allow": record.get("allow") or "", "history": bool(record.get("history")), "min": record.get("min") or MIN}


def check_brief(server, path, record):
    out = {k: record.get(k) for k in ("tree", "tables", "words", "allow", "history", "min", "looked_for",
                                      "not_allowed", "allowed", "exit")}
    out.update(key=os.path.basename(path)[:-len(".json")], when=os.path.getmtime(path),
               hits=len(record.get("hits") or []), history_hits=len(record.get("history_hits") or []),
               job=job_of(server, path))
    return out


def typed(request, text):
    """A path as typed into the page: absolute, or from the directory dread gui started in; no shell, no ~."""
    text = str(text or "").strip()
    return os.path.normpath(os.path.join(request.server.home, text)) if text else None


def command(request, values):
    """(args, line, key, missing) of a form's values: dread leakcheck's command line with --json into the
    check's record; ValueError where it is not one."""
    if not isinstance(values, dict):
        raise ValueError("values is a JSON object of the form's fields")
    tree = typed(request, values.get("tree")) or jobs_.ROOT
    tables = values.get("tables") or []
    if isinstance(tables, str):
        tables = tables.splitlines()
    tables = [typed(request, t) for t in tables if str(t).strip()]
    if not tables:
        raise ValueError("which tables: the private project's function tables or symbol files, one path a line")
    words, allow = typed(request, values.get("words")), typed(request, values.get("allow"))
    history = bool(values.get("history"))
    try:
        least = int(values.get("min") or MIN)
    except (TypeError, ValueError):
        raise ValueError("the shortest name looked for is a number of characters")
    if least < 1:
        raise ValueError("the shortest name looked for is a number of characters, 1 or more")
    key = hashlib.sha1(json.dumps([tree, tables, words, allow, history, least]).encode()).hexdigest()[:16]
    args = ["--tree", tree, "--tables"] + tables
    if words:
        args += ["--words", words]
    if allow:
        args += ["--allow", allow]
    if history:
        args.append("--history")
    if least != MIN:
        args += ["--min", str(least)]
    args += ["--json", os.path.join(checks_dir(), key + ".json")]
    cliforms.check(cli.parser("leakcheck"), args)
    missing = [p for p in [tree] + tables + [words, allow] if p and not os.path.exists(p)]
    return args, cliforms.line("leakcheck", args), key, missing


def changes(request):
    jobs = leak_jobs(request.server)
    if jobs is None:
        return "this server runs no jobs"
    return jobs.owner


# ---- the routes ----

@route("GET", "/api/leakcheck")
def overview(request):
    checks = []
    for path in glob.glob(os.path.join(checks_dir(), "*.json")):
        record = read_record(path)
        if record is not None:
            checks.append(check_brief(request.server, path, record))
    checks.sort(key=lambda c: (-c["when"], c["key"]))
    newest = read_record(os.path.join(checks_dir(), checks[0]["key"] + ".json")) if checks else None
    return {"where": where(), "tree": jobs_.ROOT, "home": request.server.home, "checks": checks,
            "last": form_values(newest) if newest else None, "live": live_jobs(request.server),
            "changes": changes(request), "min": MIN}


@route("GET", "/api/leakcheck/check")
def check(request):
    key = str(request.query.get("key") or "")
    if not key or not all(c in "0123456789abcdef" for c in key):
        raise KeyError(key or "no check given")
    path = os.path.join(checks_dir(), key + ".json")
    record = read_record(path)
    if record is None:
        raise KeyError(key)
    return dict(record, key=key, when=os.path.getmtime(path), job=job_of(request.server, path),
                values=form_values(record))


@route("GET", "/api/leakcheck/line")
def line(request):
    try:
        values = json.loads(request.query.get("values") or "{}")
        args, text, key, missing = command(request, values)
    except ValueError as e:
        return {"args": None, "line": None, "key": None, "missing": [], "error": str(e)}
    return {"args": args, "line": text, "key": key, "missing": missing, "error": None}


@route("POST", "/api/leakcheck/run")
def run(request):
    args, text, key, missing = command(request, request.data.get("values"))
    if missing:
        raise Failure(400, "no such file or directory: %s" % ", ".join(missing))
    jobs = leak_jobs(request.server)
    why = changes(request)
    if why:
        raise Failure(409, why)
    path = os.path.join(checks_dir(), key + ".json")
    running = job_of(request.server, path)
    if running and running["state"] in jobs_.LIVE:
        raise Failure(409, "job %d runs this check already (%s)" % (running["id"], running["state"]))
    os.makedirs(checks_dir(), exist_ok=True)
    argv, line_, env = jobs_.command("leakcheck", args)
    try:
        record = jobs.start(argv, where(), line=line_, command="leakcheck", env=env)
    except jobs_.JobsError as e:
        raise Failure(409, str(e))
    return dict(brief_job(record), key=key)


@route("POST", "/api/leakcheck/stop")
def stop(request):
    jobs = leak_jobs(request.server)
    if jobs is None:
        raise Failure(404, "this server keeps no jobs")
    try:
        number = int(request.data.get("id"))
    except (TypeError, ValueError):
        raise ValueError("a job's number, not %r" % (request.data.get("id"),))
    try:
        return brief_job(jobs.stop(number))
    except jobs_.JobsError as e:
        raise Failure(409, str(e))


@route("GET", "/api/leakcheck/job")
def job(request):
    jobs = leak_jobs(request.server)
    if jobs is None:
        raise Failure(404, "this server keeps no jobs")
    try:
        number = int(request.query.get("id"))
    except (TypeError, ValueError):
        raise ValueError("a job's number, not %r" % (request.query.get("id"),))
    start = request.query.get("from")
    record = jobs.get(number)
    log = jobs.log(number, int(start) if start not in (None, "") else None)
    return {"job": brief_job(record), "log": log}
