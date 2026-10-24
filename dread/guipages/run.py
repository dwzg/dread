"""dread gui's Run page: every command of DREAD as a form made from its own parser, the command line the
form makes, and the jobs that run it (dread.jobs) with their logs and their history.

The forms come from dread.cli parser and dread.cliforms: the page sends the form's values and shows the
command line the server makes of them, the same code that starts the job. A job is `sys.executable -m
dread.cli COMMAND ARGS...` (dread.jobs command) in the target's directory, with --target (and --image
for an image other than the target's first) of what the page shows where the command takes them and
the form leaves them empty: the command line says so, and is what a person would type there. A server
without a target runs them in the directory it started in, with nothing filled in.
NOT_AS_JOB says which commands do not run as a job, and why. Other pages start DREAD's commands with
start_command (the setup's analyses, a pilot run).

Routes (dread.guipages):
- GET /api/commands: [{"name", "about", "runnable", "why"}] in dread.cli's order;
- GET /api/command?name=[&args=JSON list]: {"name", "about", "form" (dread.cliforms describe),
  "runnable", "why", "cwd", "defaults" ({field: what is filled in when the form leaves it empty}),
  "values" (of args where given: a job to change and run again), "error"};
- GET /api/command-line?name=&values=JSON: {"args", "line", "cwd", "error", "field"}: the command line
  of a form's values as the job would run it; "error" why the command would refuse it, as argparse
  says (and "field" the one it names), "args" then null;
- GET /api/jobs[?limit=N]: {"jobs": the records, newest first, each with "args" (the words after
  `dread COMMAND`), "status": dread.jobs status};
- GET /api/job?id=N: a record; GET /api/job-log?id=N[&from=BYTE]: dread.jobs log;
- POST /api/jobs/start {"command", "values" or "args"[, "grace"][, "after": a job it waits for]}: the
  job's record, 400 with the command's refusal ({"error", "field"}), 409 where this server starts no
  jobs;
- POST /api/jobs/stop {"id"}: the record (stopping, or cancelled where it waited).
"""
import json
import os

from dread import cli, cliforms
from dread import jobs as jobs_
from dread import target as tgt
from dread.guipages import Failure, page, route

NOT_AS_JOB = {"gui": "dread gui is this page's server: it runs until it is stopped. Start another one from a "
                     "terminal."}
LISTED = 100            # job records /api/jobs gives unless asked for more
LISTED_MAX = 5000
GRACE_RANGE = (1.0, 3600.0)


def status(server):
    jobs = getattr(server, "jobs", None)
    return jobs.status() if jobs is not None else None


page("run", script="run.js", style="run.css", status=status)


def why_not(request, name):
    """Why a command does not run as a job here, or None."""
    if name in NOT_AS_JOB:
        return NOT_AS_JOB[name]
    if request.jobs is None:
        return "this server runs no jobs"
    return request.jobs.owner


def home(request):
    """Where a job runs: the target's directory, or without a target the directory the server started in."""
    return request.target.root if request.target is not None else request.server.home


def defaults(request, parser):
    """What a job fills in where the form leaves it empty: --target (the target the page shows: "." in
    its directory) and --image (the image it shows, where that is not the target's first); nothing while
    the server has no target."""
    flags = {s for a in parser._actions for s in a.option_strings}
    target, out = request.target, {}
    if target is None:
        return out
    if "--target" in flags:
        out["--target"] = "." if os.path.basename(target.file) == tgt.FILE else target.file
    if "--image" in flags and request.image != target.default_image:
        out["--image"] = request.image
    return out


def filled(values, extra):
    out = dict(values or {})
    for k, v in extra.items():
        if not str(out.get(k) or "").strip():
            out[k] = v
    return out


def command_line(request, name, values=None, args=None):
    """(args, line) of a command as its job runs it: from a form's values (with defaults()) or from
    args as they are (a job run again). cliforms.FormError where the command would refuse it."""
    if name not in cli.COMMANDS:
        raise KeyError(name)
    if values is not None and not isinstance(values, dict):
        raise TypeError("values is a JSON object of the form's fields")
    if args is None:
        parser = cli.parser(name)
        args = cliforms.argv(parser, filled(values, defaults(request, parser)))
    else:
        if not isinstance(args, list):
            raise TypeError("args is a list of words")
        args = [str(a) for a in args]
    cliforms.check(cli.parser(name), args)
    return args, cliforms.line(name, args)


def start_command(request, name, values=None, args=None, grace=None, after=None, cwd=None):
    """Start a job of one of DREAD's commands (dread.jobs command) in the target's directory (or cwd):
    its record. after: a job it waits for (it runs once that one is done, and is cancelled where that one
    failed: a chain of jobs); grace: seconds from SIGTERM to SIGKILL on a stop. Failure(409) where the
    command does not run as a job here, cliforms.FormError where it would refuse its command line."""
    why = why_not(request, name)
    if name in cli.COMMANDS and why:
        raise Failure(409, why)
    args, line = command_line(request, name, values, args)
    if grace is not None:
        grace = min(max(float(grace), GRACE_RANGE[0]), GRACE_RANGE[1])
    argv, line, env = jobs_.command(name, args)
    try:
        return request.jobs.start(argv, cwd or home(request), line=line, command=name, env=env, grace=grace,
                                  after=after)
    except jobs_.JobsError as e:
        raise Failure(409, str(e))


def shown(record):
    """A record for the page, with "args" for a command of DREAD."""
    argv = record.get("argv") or []
    if len(argv) >= 4 and argv[1:3] == ["-m", "dread.cli"]:
        record = dict(record, args=argv[4:])
    return record


def jobs_of(request):
    if request.jobs is None:
        raise Failure(404, "this server keeps no jobs")
    return request.jobs


def number(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        raise ValueError("a job's number, not %r" % (text,))


@route("GET", "/api/commands")
def commands(request):
    return [{"name": name, "about": about, "runnable": why_not(request, name) is None, "why": why_not(request, name)}
            for name, (_, about) in cli.COMMANDS.items()]


@route("GET", "/api/command")
def command(request):
    name = request.query.get("name", "")
    if name not in cli.COMMANDS:
        raise KeyError(name)
    parser = cli.parser(name)
    why = why_not(request, name)
    out = {"name": name, "about": cli.COMMANDS[name][1], "form": cliforms.describe(parser), "runnable": why is None,
           "why": why, "cwd": home(request), "defaults": defaults(request, parser), "values": None, "error": None}
    if request.query.get("args"):
        try:
            args = json.loads(request.query["args"])
            if not isinstance(args, list):
                raise ValueError("args is a list of words")
            out["values"] = cliforms.values(cli.parser(name), [str(a) for a in args])
        except (ValueError, cliforms.FormError) as e:
            out["error"] = str(e)
    return out


@route("GET", "/api/command-line")
def preview(request):
    name = request.query.get("name", "")
    if name not in cli.COMMANDS:
        raise KeyError(name)
    values = json.loads(request.query.get("values") or "{}")
    if not isinstance(values, dict):
        raise ValueError("values is a JSON object")
    out = {"args": None, "line": None, "cwd": home(request), "error": None, "field": None}
    try:
        parser = cli.parser(name)
        args = cliforms.argv(parser, filled(values, defaults(request, parser)))
        out["line"] = cliforms.line(name, args)
        cliforms.check(cli.parser(name), args)
        out["args"] = args
    except cliforms.FormError as e:
        out.update(error=str(e), field=e.field)
    return out


@route("GET", "/api/jobs")
def job_list(request):
    jobs = jobs_of(request)
    limit = max(1, min(int(request.query.get("limit") or LISTED), LISTED_MAX))
    return {"jobs": [shown(r) for r in jobs.list(limit)], "status": jobs.status()}


@route("GET", "/api/job")
def job(request):
    return shown(jobs_of(request).get(number(request.query.get("id"))))


@route("GET", "/api/job-log")
def job_log(request):
    start = request.query.get("from")
    return jobs_of(request).log(number(request.query.get("id")), number(start) if start not in (None, "") else None)


@route("POST", "/api/jobs/start")
def job_start(request):
    data = request.data
    name = str(data.get("command", ""))
    if name not in cli.COMMANDS:
        raise Failure(404, "no command %r" % name)
    after = data.get("after")
    try:
        record = start_command(request, name, values=data.get("values"), args=data.get("args"), grace=data.get("grace"),
                               after=number(after) if after is not None else None)
    except cliforms.FormError as e:
        return 400, {"error": str(e), "field": e.field}
    return shown(record)


@route("POST", "/api/jobs/stop")
def job_stop(request):
    try:
        return shown(jobs_of(request).stop(number(request.data.get("id"))))
    except jobs_.JobsError as e:
        raise Failure(409, str(e))
