"""dread gui's names page (#/names, key e): names for this image's functions from elsewhere, each source a
command run as a job, its proposals listed, and the chosen ones applied with dread rename (a job too):
- the toolchains' libraries: dread whatcc matches every function with the functions of the toolchains'
  libraries by their code (its summary, --json, and its per-function table, BUILD/whatcc/IMAGE/matches.tsv);
  a function without a name that matched names not given as ambiguous is offered the first of them;
- the same code in another image: dread samecode --other DIR --json (pairs of functions that are the same
  instructions; a pair offers the other image's name where this one has none);
- the call order: dread callpair --other DIR [--seed HERE=THERE] --json (names that follow from the order in
  which paired functions call, from the names both images share and the same code, seeds added).
A proposal is offered for applying only where it gives a function of this image a name it lacks (FUN_... here,
a name there: the tools' new_name); dread rename does the renaming as on the command line, everywhere a name is
written, and undoes it where the tree then contradicts it. The records of samecode and callpair are kept in
BUILD/gui/names/TOOL.json, whatcc's where it writes them.

Routes (dread.guipages):
- GET /api/names: {"whatcc": {"summary", "proposals": [{"entry", "here", "there", "matched", "toolchains"}],
  "matched", "when"} or null, "samecode" and "callpair": {"record": the tool's --json, "proposals", "when"}
  or null, "live": this page's jobs queued or running, "other": the other target used last};
- POST /api/names/run {"tool": "whatcc", "samecode" or "callpair", "other", "other_image", "seeds", "min"}:
  its job's record;
- POST /api/names/apply {"names": {OLD: NEW}}: the record of the dread rename job that applies them.

What it does not establish: that a proposal is right (the same code can be another function's that does
the same, a library's getter shared by many; the call order can pair wrongly where calls repeat); the tools'
docstrings say what each one checks.
"""
import json
import os
import re

from dread import jobs as jobs_
from dread.guipages import Failure, page, route

TOOLS = ("whatcc", "samecode", "callpair")
UNNAMED = re.compile(r"^FUN_[0-9a-fA-F]+$")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def status(server):
    return {}


page("names", script="names.js", style="names.css", status=status)


def names_dir(request):
    return os.path.join(request.target.build, "gui", "names")


def record_path(request, tool):
    if tool == "whatcc":
        return os.path.join(names_dir(request), "whatcc.json")
    return os.path.join(names_dir(request), tool + ".json")


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _when(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def whatcc_result(request, image):
    """dread whatcc's summary and its per-function matches, with what they offer; None before its first run."""
    summary_path = record_path(request, "whatcc")
    matches = os.path.join(request.target.build, "whatcc", image, "matches.tsv")
    summary = _read_json(summary_path)
    if summary is None and not os.path.exists(matches):
        return None
    proposals, matched = [], 0
    try:
        with open(matches) as f:
            next(f, None)
            for line in f:
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 5:
                    continue
                entry, here, names_, ambiguous, toolchains = cols[:5]
                found = [n for n in names_.split(",") if n]
                if not found:
                    continue
                matched += 1
                sure = [n for n in found if n not in set(ambiguous.split(","))]
                if UNNAMED.match(here) and sure:
                    proposals.append({"entry": int(entry, 16), "here": here, "there": sure[0], "matched": found,
                                      "toolchains": [t for t in toolchains.split(",") if t]})
    except OSError:
        pass
    return {"summary": summary, "proposals": proposals, "matched": matched, "when": _when(matches)}


def pairs_result(request, tool):
    """samecode's or callpair's record with what it offers; None before its first run."""
    path = record_path(request, tool)
    record = _read_json(path)
    if record is None:
        return None
    proposals = [{"entry": p["entry"], "here": p["here"], "there": p["there"]}
                 for p in record.get("pairs", []) if p.get("new_name")]
    return {"record": record, "proposals": proposals, "when": _when(path)}


def page_jobs(request):
    """This page's jobs in the server's list (the commands it starts) that are queued or running."""
    if request.jobs is None:
        return []
    out = []
    for r in request.jobs.list(50):
        argv = r.get("argv") or []
        line = r.get("line") or ""
        if r.get("state") in jobs_.LIVE and any(" %s " % t in " %s " % line for t in TOOLS + ("rename",)):
            out.append({k: r.get(k) for k in ("id", "state", "line", "queued", "started")})
    return out


@route("GET", "/api/names")
def names_state(request):
    if request.target is None:
        raise Failure(409, "no target yet: make or open one on the Setup page")
    image = request.image
    last = _read_json(os.path.join(names_dir(request), "other.json")) or {}
    return {"whatcc": whatcc_result(request, image), "samecode": pairs_result(request, "samecode"),
            "callpair": pairs_result(request, "callpair"), "live": page_jobs(request), "other": last}


@route("POST", "/api/names/run")
def names_run(request):
    from dread.guipages.run import start_command
    if request.target is None:
        raise Failure(409, "no target yet: make or open one on the Setup page")
    data = request.data or {}
    tool = data.get("tool")
    if tool not in TOOLS:
        raise Failure(400, "tool must be one of %s" % ", ".join(TOOLS))
    args = ["--image", request.image]
    if tool in ("samecode", "callpair"):
        other = (data.get("other") or "").strip()
        if not other:
            raise Failure(400, "%s needs the other target's directory" % tool)
        args += ["--other", other]
        if data.get("other_image"):
            args += ["--other-image", str(data["other_image"])]
        if tool == "samecode" and data.get("min"):
            args += ["--min", str(int(data["min"]))]
        if tool == "callpair":
            for seed in data.get("seeds") or []:
                if not re.match(r"^[^=\s]+=[^=\s]+$", str(seed)):
                    raise Failure(400, "a seed is HERE=THERE: %r" % seed)
                args += ["--seed", str(seed)]
        os.makedirs(names_dir(request), exist_ok=True)
        with open(os.path.join(names_dir(request), "other.json"), "w") as f:
            json.dump({"other": other, "other_image": data.get("other_image") or ""}, f)
    os.makedirs(names_dir(request), exist_ok=True)
    args += ["--json", record_path(request, tool)]
    return start_command(request, tool, args=args)


@route("POST", "/api/names/apply")
def names_apply(request):
    from dread.guipages.run import start_command
    if request.target is None:
        raise Failure(409, "no target yet: make or open one on the Setup page")
    names = (request.data or {}).get("names") or {}
    if not isinstance(names, dict) or not names:
        raise Failure(400, "names: {OLD: NEW, ...}, at least one")
    pairs = []
    for old, new in sorted(names.items()):
        if not (NAME.match(str(old)) and NAME.match(str(new))):
            raise Failure(400, "not a name: %r=%r" % (old, new))
        pairs.append("%s=%s" % (old, new))
    return start_command(request, "rename", args=["--image", request.image] + pairs)
