"""dread gui's Setup page: what this machine has (dread doctor), a new target from an image (dread new, run
as a job; the image a path on this machine or a file uploaded from the browser into the new target), the
target opened once it is made (the server switches: dread.gui Server.open_target), its first analyses as
a chain of jobs (dread.newtarget first_analyses), and the language-model providers (the functions of
dread llm set and dread llm key, dread.llmconfig: llm.toml edited line by line, a key written into a key
file of mode 600 and never shown back; a provider tried with dread llm ask, as a job).

Every action is a command first: the page shows the command line that does the same (dread new ..., dread
llm set ..., dread llm key NAME, which reads the key from standard input), and runs the commands that take
a while as jobs (dread.guipages.run start_command). Paths typed into the page are taken as they are typed,
relative to the directory dread gui started in (also while a target is shown: a new target lands beside
it, not inside it), and dread new runs there: no shell, no ~.

Routes (dread.guipages):
- GET /api/setup/doctor: dread.doctor check() (with the target shown, its compiler);
- GET /api/setup/state: {"target", "functions", "home" (where dread gui started: typed paths start there
  and dread new runs there), "arches" ([{"name", "about"}]: dread new's), "upload_limit", "changes" (why
  this server changes nothing, or null), "llm" (the providers' file)};
- POST /api/setup/inspect {"path"}: what a typed path is: {"path" (absolute), "kind": "elf", "raw",
  "target" (a directory with a dread.toml), "directory", "missing", "size", "machine" (an ELF's: arm,
  riscv, msp430, mips, or its number)}; the token needed, a read-only server allowed;
- POST /api/setup/upload?dir=DIR&name=FILE, the file's bytes (a raw route: refused beyond the server's
  upload limit before it is read): written to DIR/original/FILE (FILE reduced to a plain name; DIR must
  be what dread new takes: new, empty, or holding only original/; neither DIR nor its original/ a
  symbolic link), written beside and renamed into place, no link followed: {"path" (DIR/original/FILE,
  as dread new is given it), "absolute", "size", "sha256"};
- POST /api/setup/new {"values"}: dread new's form run as a job in the directory dread gui started in:
  its record, 400 with the command's refusal ({"error", "field"});
- POST /api/setup/open {"path"} or {"job": N}: the server shows that target (N: a dread new job that is
  done, its directory taken from its command line): {"target"}; 409 with the reason where it does not
  (a job still running or waiting, a directory that is no target);
- GET /api/setup/chain: the first analyses: {"planned" (not started: what Start would run), "steps":
  [{"step", "command", "line", "why", "job", "state", "exit", "started", "ended", "last" (the log's last
  line)}], "started", "live" (a step still waiting or running), "error"};
- POST /api/setup/chain: start them as chained jobs (each waits for the one before and is cancelled
  where that one fails), kept in BUILD/gui/chain.json (docs/formats.md): the chain's state;
- GET /api/setup/providers: {"path", "exists", "default", "providers": [{"name", "kind", "model", "url",
  "price_in", "price_out", "cache_min", "batch", "key_env", "key_file", "command", "default", "key":
  dread.llm key_status (set or not, how; never the key)}], "kinds", "error"};
- POST /api/setup/provider {"name", "changes": {field: value}, "unset": [fields], "default"}: dread llm
  set's change: {"done", "line", "providers"};
- POST /api/setup/key {"name", "key", "file"}: dread llm key's: the key into its key file (mode 600):
  {"key": its status, "file", "line"}; the key is not in the answer, a log or an error message;
- POST /api/setup/ask {"name"[, "question"]}: dread llm ask as a job: its record.

The changes (every POST but inspect) need the page's token, are refused by a read-only server (dread gui
--read-only) and by one that listens beyond this machine (the jobs' rule: whoever can load the page has
its token).
"""
import hashlib
import json
import os
import re
import shlex
import struct
import time

from dread import cli, cliforms, doctor, llm, llmconfig, newtarget
from dread import jobs as jobs_
from dread import target as tgt
from dread.guipages import Failure, page, route
from dread.guipages import run

ASK = "Answer in five words: are you there?"        # the question a provider is tried with
FILE = re.compile(r"[A-Za-z0-9._-]+")
VERSION = 1                                         # of chain.json


def status(server):
    """The Setup page's share of /api/status: the first analyses' jobs, as numbers (the page polls the
    chain itself while it shows one running)."""
    target = server.RequestHandlerClass.target
    if target is None:
        return {"target": None}
    record = read_chain(target)
    return {"target": target.root, "chain": [s.get("job") for s in record["steps"]] if record else None}


page("setup", script="setup.js", style="setup.css", status=status)


def changeable(request):
    """Failure where this server makes no change: read-only, or listening beyond this machine."""
    if request.read_only:
        raise Failure(403, "this page is read-only (dread gui --read-only): it changes nothing")
    if not request.server.local:
        raise Failure(403, "this server listens beyond this machine: whoever reaches it could change files, so it "
                           "changes none (for another machine use ssh -L)")


def why_no_changes(request):
    try:
        changeable(request)
    except Failure as e:
        return str(e)
    return None


def typed(request, path):
    """A path as typed into the page: absolute, or relative to the directory dread gui started in (not the
    target's: a new target is no target's subdirectory); no shell, no ~."""
    path = str(path or "").strip()
    if not path:
        raise ValueError("no path given")
    return os.path.normpath(os.path.join(request.server.home, path))


# ---- the machine ----

@route("GET", "/api/setup/doctor")
def doctor_route(request):
    return dict(doctor.check(request.target), line="dread doctor")


@route("GET", "/api/setup/state")
def state_route(request):
    target = request.target
    return {"target": request.handler.target_brief(), "functions": newtarget.functions_in(target) if target else None,
            "home": request.server.home, "arches": [{"name": k, "about": v[3]} for k, v in newtarget.ARCHES.items()],
            "upload_limit": request.server.upload_limit, "changes": why_no_changes(request), "llm": llm.config_path()}


# ---- a new target ----

@route("POST", "/api/setup/inspect", change=False)
def inspect(request):
    path = typed(request, request.data.get("path"))
    out = {"path": path, "kind": "missing", "size": None, "machine": None}
    if os.path.isdir(path):
        out["kind"] = "target" if os.path.exists(os.path.join(path, tgt.FILE)) else "directory"
        why = newtarget.unusable(path)
        out["new"] = why is None
        out["why"] = why
        return out
    if not os.path.isfile(path):
        return out
    out["size"] = os.path.getsize(path)
    try:
        with open(path, "rb") as f:
            head = f.read(20)
    except OSError as e:
        return dict(out, kind="unreadable", error=e.strerror)
    if head[:4] == b"\x7fELF":
        out["kind"] = "elf"
        if len(head) >= 20 and head[5] == 1:
            machine = struct.unpack_from("<H", head, 18)[0]
            out["machine"] = newtarget.FAMILIES.get(machine, machine)
        out["supported"] = head[4] == 1 and head[5] == 1 and out["machine"] in newtarget.FAMILIES.values()
    else:
        out["kind"] = "raw"
    return out


def plain_name(name):
    """A file name reduced to a plain one (letters, digits, ., -, _; not starting with a dot)."""
    base = os.path.basename(str(name or "").replace("\\", "/"))
    base = "".join(c if FILE.fullmatch(c) else "_" for c in base).lstrip(".")
    return base[:120] or "image.bin"


@route("POST", "/api/setup/upload", raw=True)
def upload(request):
    changeable(request)
    folder = str(request.query.get("dir") or "").strip()
    if not folder:
        raise ValueError("the new target's directory comes first: the file goes into its original/")
    directory = typed(request, folder)
    why = newtarget.unusable(directory)
    if why:
        raise Failure(409, why)
    name = plain_name(request.query.get("name"))
    original = os.path.join(directory, "original")
    if os.path.islink(directory) or os.path.islink(original):     # (it would land elsewhere)
        raise Failure(409, "%s is a symbolic link: the file goes into the new target's own original/" % (
            original if os.path.islink(original) else directory))
    os.makedirs(original, exist_ok=True)
    path = os.path.join(original, name)
    tmp = os.path.join(original, ".%s.upload" % name)
    body = request.body or b""
    if os.path.lexists(tmp):
        os.unlink(tmp)
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        os.replace(tmp, path)                   # (a link at path is replaced, not followed)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)
    return {"path": os.path.join(folder, "original", name), "absolute": path, "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest()}


@route("POST", "/api/setup/new")
def new_route(request):
    """dread new as a job, run in the directory dread gui started in (where the page's paths start)."""
    changeable(request)
    values = request.data.get("values")
    if not isinstance(values, dict):
        raise ValueError("values is the form's object")
    try:
        record = run.start_command(request, "new", values=values, cwd=request.server.home)
    except cliforms.FormError as e:
        return 400, {"error": str(e), "field": e.field}
    return run.shown(record)


@route("POST", "/api/setup/open")
def open_route(request):
    changeable(request)
    data = request.data
    if data.get("job") is not None:
        try:
            record = request.jobs.get(int(data["job"]))
        except (KeyError, ValueError, TypeError):
            raise Failure(404, "no job %r" % data.get("job"))
        argv = record.get("argv") or []
        if record.get("command") != "new" or len(argv) < 5:
            raise Failure(409, "job %s is no dread new" % record.get("id"))
        if record.get("state") != "done":
            raise Failure(409, "job %s has not made its target (it is %s)" % (record["id"], record.get("state")))
        values = cliforms.values(cli.parser("new"), argv[4:])
        path = os.path.normpath(os.path.join(record["cwd"], values["dir"]))
    else:
        path = typed(request, data.get("path"))
    try:
        request.server.open_target(path)
    except tgt.TargetError as e:
        raise Failure(409, str(e))
    return {"target": request.handler.target_brief()}


# ---- the first analyses ----

def chain_file(target):
    return os.path.join(target.build, "gui", "chain.json")


def read_chain(target):
    try:
        with open(chain_file(target)) as f:
            record = json.load(f)
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and isinstance(record.get("steps"), list) else None


def step_line(step):
    if not step.get("command"):
        return None
    words = []
    for key, value in (step.get("values") or {}).items():
        if value is True:
            words.append(key)
        elif value not in (None, False, ""):
            words += [key, str(value)]
    return shlex.join(["dread", step["command"]] + words)


def last_line(request, number):
    """The last line a job's log has (its tail, without the redaction's work skipped)."""
    try:
        piece = request.jobs.log(number)
    except (KeyError, OSError):
        return ""
    lines = [line for line in re.split(r"[\r\n]", piece.get("text") or "") if line.strip()]
    return lines[-1][-300:] if lines else ""


def chain_state(request):
    target = request.target
    out = {"planned": False, "steps": [], "started": None, "live": False, "error": None}
    if target is None:
        return dict(out, error="no target yet: make or open one above")
    record = read_chain(target)
    if record is None:
        try:
            steps = newtarget.first_analyses(target, request.image)
        except newtarget.NewTargetError as e:
            return dict(out, planned=True, error=str(e))
        out["planned"] = True
        for s in steps:
            out["steps"].append({"step": s["step"], "command": s["command"], "line": step_line(s), "why": s["why"],
                                 "job": None, "state": "done" if s["command"] is None else "planned", "exit": None,
                                 "started": None, "ended": None, "last": ""})
        return out
    out["started"] = record.get("started")
    for s in record["steps"]:
        row = {"step": s.get("step"), "command": s.get("command"), "line": s.get("line"), "why": s.get("why"),
               "job": s.get("job"), "state": "done" if s.get("job") is None else "unknown", "exit": None,
               "started": None, "ended": None, "last": ""}
        if s.get("job") is not None and request.jobs is not None:
            try:
                job = request.jobs.get(int(s["job"]))
                row.update(state=job["state"], exit=job.get("exit"), started=job.get("started"), ended=job.get("ended"),
                           why_not=job.get("why"))
                if job.get("started"):
                    row["last"] = last_line(request, int(s["job"]))
            except KeyError:
                pass
        out["steps"].append(row)
    out["live"] = any(r["state"] in jobs_.LIVE for r in out["steps"])
    return out


@route("GET", "/api/setup/chain")
def chain_route(request):
    return chain_state(request)


@route("POST", "/api/setup/chain")
def chain_start(request):
    changeable(request)
    target = request.target
    if target is None:
        raise Failure(409, "no target yet: make or open one first")
    if chain_state(request)["live"]:
        raise Failure(409, "the first analyses run already")
    try:
        steps = newtarget.first_analyses(target, request.image)
    except newtarget.NewTargetError as e:
        raise Failure(409, str(e))
    kept, previous = [], None
    for s in steps:
        if s["command"] is None:
            kept.append({"step": s["step"], "command": None, "line": None, "why": s["why"], "job": None})
            continue
        record = run.start_command(request, s["command"], values=s["values"], after=previous)
        previous = record["id"]
        kept.append({"step": s["step"], "command": s["command"], "line": record["line"], "why": s["why"],
                     "job": record["id"]})
    path = chain_file(target)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump({"version": VERSION, "started": time.time(), "image": request.image, "steps": kept}, f, indent=1,
                  sort_keys=True)
        f.write("\n")
    os.replace(path + ".tmp", path)
    return chain_state(request)


# ---- the language-model providers ----

def provider_rows(path, data):
    out = []
    for name, spec in sorted((data.get("providers") or {}).items()):
        if not isinstance(spec, dict):
            continue
        command = spec.get("command")
        out.append({"name": name, "kind": spec.get("kind"), "model": spec.get("model"), "url": spec.get("url"),
                    "price_in": spec.get("price_in"), "price_out": spec.get("price_out"),
                    "cache_min": spec.get("cache_min"), "batch": spec.get("batch"), "key_env": spec.get("key_env"),
                    "key_file": spec.get("key_file"),
                    "command": shlex.join(command) if isinstance(command, list) else None,
                    "default": name == data.get("default"), "key": llm.key_status(spec, path)})
    return out


@route("GET", "/api/setup/providers")
def providers_route(request):
    path = llm.config_path()
    out = {"path": path, "exists": os.path.exists(path), "default": None, "providers": [], "kinds": list(llm.KINDS),
           "fields": list(llmconfig.FIELDS), "error": None}
    try:
        _, data = llmconfig.read(path)
    except llmconfig.ConfigError as e:
        return dict(out, error=str(e))
    return dict(out, default=data.get("default"), providers=provider_rows(path, data))


def set_line(name, changes, unset, default):
    words = ["dread", "llm", "set", name]
    for field in llmconfig.FIELDS:
        if field not in changes:
            continue
        value = changes[field]
        if field == "batch":
            words.append("--batch" if value else "--no-batch")
        else:
            words += ["--" + field.replace("_", "-"), str(value)]
    if default:
        words.append("--default")
    for field in unset:
        words += ["--unset", field]
    return shlex.join(words)


@route("POST", "/api/setup/provider")
def provider_route(request):
    changeable(request)
    data = request.data
    name = str(data.get("name") or "").strip()
    changes = data.get("changes") or {}
    unset = data.get("unset") or []
    if not isinstance(changes, dict) or not isinstance(unset, list):
        raise ValueError("changes is an object of fields, unset a list of them")
    changes = {k: v for k, v in changes.items() if v is not None and v != ""}
    try:
        done = llmconfig.set_provider(llm.config_path(), name, changes, [str(u) for u in unset],
                                      bool(data.get("default")))
    except llmconfig.ConfigError as e:
        raise Failure(400, str(e))
    return {"done": [{"field": f, "before": b, "after": a} for f, b, a in done],
            "line": set_line(name, changes, unset, bool(data.get("default"))), "providers": providers_route(request)}


@route("POST", "/api/setup/key")
def key_route(request):
    changeable(request)
    data = request.data
    name = str(data.get("name") or "").strip()
    file = str(data.get("file") or "").strip() or None
    path = llm.config_path()
    try:
        where, _ = llmconfig.write_key(path, name, data.get("key"), file)
        _, config = llmconfig.read(path)
    except llmconfig.ConfigError as e:
        raise Failure(400, str(e))
    finally:
        data.pop("key", None)                   # (the key is held no longer than this)
    if request.jobs is not None:                # (a job that printed it would show it no more)
        try:
            request.jobs.add_secret(llm.read_key_file(where), "key_file of %s" % name)
        except llm.ProviderError:
            pass
    spec = (config.get("providers") or {}).get(name) or {}
    return {"key": llm.key_status(spec, path), "file": where,
            "line": "dread llm key %s%s" % (shlex.quote(name), " --file " + shlex.quote(file) if file else "")}


@route("POST", "/api/setup/ask")
def ask_route(request):
    changeable(request)
    name = str(request.data.get("name") or "").strip()
    if not llmconfig.NAME.match(name):
        raise ValueError("which provider? (its name in llm.toml)")
    question = str(request.data.get("question") or "").strip() or ASK
    try:
        record = run.start_command(request, "llm", args=["ask", "--provider", name, question])
    except cliforms.FormError as e:
        raise Failure(400, str(e))
    return run.shown(record)
