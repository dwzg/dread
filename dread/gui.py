"""dread gui: browse an image in the web browser.

A small web server on this machine and one page without a framework or a build step. It shows what
the tools know and decided, and runs nothing heavy itself:
- the overview: the functions by origin (written by hand, by dread pilot, lifted, refused or dropped
  by the lift check, original) in counts and bytes, the functions by how sure their names are, a map
  of the image (by origin or by names), what the name reviews left (flagged, suspects, changed, the
  groups' names), the refusal reasons grouped, the failures of the last test run, when the tools last
  ran;
- the function list, filtered (origin, how sure the name is, the review's mark) and sorted, each
  function by the name it goes by (dread pilot's proposal, in italics, where the tables have none);
- one function: the flat listing (dread flat) with calls and branches linked, the instructions the
  cases ran or missed (from the last dread test or dread lift --check), the C (the source of a
  rewritten function; else its readable C in the reading form, dread readable --plain: without the
  instructions' comments, each statement's first instruction in a margin that selects it in the
  listing, made on the first request; and the lifted C, built or lifted on the spot), its verdict,
  cases, placement and annotations, its name's proposal and vote, and every challenge of the review
  with its reasons;
- one function's relations (dread relations, /api/graph): its callers and callees to a depth of 1 to
  3, the memory its readable C reads and writes, the texts and tables its constants point to, the
  structures of the readable_types plan; a click walks to another function. The functions come at
  once; the memory, texts and structures follow as a worker reads each function's C, nearest first
  (kept per function in BUILD/relations, so a second look costs a lift);
- the whole program (dread groups --json, /api/program): each group of functions dread groups finds
  one node, named by the pilot where it named that group, joined by the calls, taken addresses and
  shared memory between their functions; a group opens into its functions in place (the memory and
  texts it shares with other groups then joined to the functions that touch them), and a small
  program shows all its functions at once. The groups are found on the first request, by dread groups
  in a process of its own (its pool forks, which this threaded server must not), and kept in
  BUILD/groups, so a second look reads them. A search there (/api/program-search) lights the
  functions it finds by name, the name proposed, address or text, and the groups that hold them;
  Enter opens those groups;
- the system run (#/s, key s): the records dread sysrun and dread lift --check's whole image keep
  (dread.sysrun, BUILD/sysrun), a scenario at a time: how each image's run ended, its clock, the
  serial port's text, and both images' device logs side by side, entry by entry, each with the function
  whose instruction made it (a link to it there); where they first differ marked, with the rows after
  it that differ too (key d goes there). A virtual list: the page asks for the entries around what is
  on screen, TIMELINE_ROWS at a time;
- what the system run saw beyond the calls, in both graphs and on the function's page: who called a
  function through a pointer (a timer's callback from the timers' task), who started it by a return
  (a task from its scheduler), how the core entered it (an exception), from the records' transfers
  (dread.sysrun ran), edges of their own (calls-pointer, enters; dashed in the whole program);
- how sure each name is, everywhere a function is shown (dread.relations Names: certainty from
  names.tsv's vote, the review's standing from review.tsv): one hue from unsure to sure, a neutral grey
  for a proposal without a vote and an empty disc for none; each function's disc filled as far as its
  vote agreed (whole for a name in the tables), its outline dashed without a vote, so the scale reads
  without the colour; the review's suspects marked with a glyph (? kept without a confirmation, !
  a person's name the decision went against, a turn for a name the review changed), its reasons and
  the old name on hover and in the function's page;
- both graphs lay themselves out as the page draws them: a force simulation with velocities and
  friction runs over the animation frames, so that nodes overshoot a little and settle, starts from
  the last positions when the graph changes, keeps running while a node is dragged (its neighbours
  follow; released, it settles again) and stops when the layout is at rest. With prefers-reduced-motion
  the page shows the settled layout without the motion. The whole program is drawn on a canvas, its
  repulsion approximated by Barnes-Hut beyond a few hundred nodes (dread/web/sim.js);
- search by name, by the name dread pilot proposed, by address and by the text a function refers to;
- two kinds of change, made as the command line makes them: rename (dread rename, checked and
  undone when the tree contradicts it) and annotate (annotations.txt);
- the Run page (#/run, key x, a button in the bar; dread/guipages/run.py): every command of DREAD as a
  form made from its own parser (dread.cli parser, dread.cliforms: positionals, options with their
  choices, defaults and help, flags as checkboxes, repeated options, subcommands as nested forms, the
  options a help text ties to a choice of another field shown for that choice), the command line the
  form makes, as the server makes it, run as a job in the target's directory with --target of the
  target shown; a command that needs what the server cannot give (dread gui itself) says so instead;
- the jobs (#/jobs, #/jobs/N): each one's command line, state, times and exit status, its log
  followed while it runs (polled from an offset), stop, run again, change and run (its form filled
  from its command line). dread.jobs runs them: `sys.executable -m dread.cli COMMAND ARGS...`, each in
  a process group of its own, polite, at most --jobs at a time (default 1) and the rest queued, stopped
  by SIGTERM to the group and SIGKILL after a grace period, the output in BUILD/gui/jobs/N.log and a
  record N.json beside it (docs/formats.md) that survives the server: a job that was running when the
  server stopped is marked so. The server's environment reaches the jobs and nothing of it is kept or
  shown. Stopping the server stops its jobs.
- the Setup page (#/setup, key u, a button in the bar; dread/guipages/setup.py): what this machine has
  (dread doctor), a new target from an ELF or a raw image (dread new as a job; the image a path on this
  machine or a file uploaded from the browser into the new target's original/), the target opened once
  it is made, its first analyses as a chain of jobs (the functions where the image has none yet, dread
  regflow, dread lift --check, dread readable --check), and the language-model providers (llm.toml set
  up line by line, a key written into a key file of mode 600 and never shown back, a provider tried
  with one question as a job).
- the pilot page (#/pilot, key l; dread/guipages/pilot.py): dread pilot started from a short form (the
  task, the provider with its prices, the budget, all functions or some, Message Batches, the task's few
  options; the Run page's form has every option), its estimate (the dry run's count, an earlier run's
  tokens a function at the provider's prices), a run followed live from its events (the stage, the
  questions answered of those planned, the batches out and their age, the spend against the budget, the
  names as they come) and stopped as its job; the runs before, what the answers cost a month, the
  pilot's comparisons of names.
A server started where there is no target (no --target, no $DREAD_TARGET, no dread.toml above the
working directory) comes up with the Setup page alone: the browsing views wait for a target, its jobs
(dread new, a provider's test) run in the directory it started in with their records in the user's
state directory ($XDG_STATE_HOME/dread/gui/jobs, else ~/.local/state/dread/gui/jobs). The Setup page
makes or opens a target, and the server then shows that one (Server.open_target): one server, one target
at a time, switched only while none of its jobs runs or waits (they would lose their server), never on a
read-only server or one that listens beyond this machine; the page reloads itself when the server shows
another target. For two targets at once, two servers (another --port).
It reloads by itself when the tables or the tools' records change (the names' tables alone, names.tsv
and review.tsv among them, and the system run's records, without loading the image again), also where
a job from the Run page changed them. The analyses that take a while on a large image (callers,
regflow, never-returning functions, the text index, the relations' readable C, the groups) run in the
background; the page fills them in when they are ready.

Pages of their own: the Run page is the first page that is a module of dread/guipages (its API routes
registered from it, imported at start) with a script of its own in dread/web (run.js, imported by
app.js and registered with its router); the Setup page (setup.py, setup.js) the second. Later pages are
added the same way without touching the lines of another; dread/guipages/__init__.py says how. The pages
of browsing above stay here and in app.js.

The answers, JSON, all computed here from the tables and the tools' records (the page computes
nothing; what the names give is kept until a names table changes):
- /api/gui: {"pages": [{"name", "script", "style"}] (dread/guipages' pages, whose scripts the page
  imports), "read_only", "version", "status" (as /api/status's "pages"), "target" ({"name", "title",
  "root", "image", "images"}, null without one), "local" (whether it listens on this machine alone),
  "home" (where it started), "upload_limit" (bytes)};
- /api/status: {"generation" (a load of the image), "pending" (analyses), "names" (a change of the
  names' tables), "runs" (a change of the system run's records), "pages" ({page: what it adds}: the
  Run page's dread.jobs status), "target" (the root of the target shown: the page reloads when it
  changes; null without a target, when the browsing answers say 409 with "setup": true)};
- the Setup page's: /api/setup/... (dread/guipages/setup.py says what each answers);
- the Run page's: /api/commands, /api/command, /api/command-line, /api/jobs, /api/job, /api/job-log,
  POST /api/jobs/start and /api/jobs/stop (dread/guipages/run.py says what each answers);
- the pilot page's: /api/pilot, /api/pilot/runs, /api/pilot/job, /api/pilot/follow, /api/pilot/usage,
  /api/pilot/compare, POST /api/pilot/estimate, /api/pilot/start, /api/pilot/stop,
  /api/pilot/cancel-batches and /api/pilot/compare/start (dread/guipages/pilot.py);
- /api/functions: each function's name, entry, size, origin and test record, its "certainty"
  (dread.relations CERTAINTY), "confidence" (its vote's agreement, where there was a vote), "proposed"
  (the name it goes by where the tables have none) and "review" ({"mark", "outcome", "old": the name
  before where the review changed it}, where the review left a mark);
- /api/overview: ... and "names": {"counts": {certainty: {"functions", "bytes"}}, "review": the
  flagged, suspects and changed names with their last challenge, the confirmed ones, the groups'
  names challenged, "challenges", "reviews", "stale" (rows about a name no longer in use); null
  without review.tsv}; "sysrun": the system run's records as /api/timeline's "scenarios" lists them;
  "runs" has "sysrun" (when the newest was written);
- /api/function: ... and "name" (Names.about with the history: the proposal, its vote's answers, and
  every review row as dread.namereview challenge, those about an older name marked stale); "ran":
  {"callers", "callees": [{"name", "how", "times", "site", "scenarios"}] of the transfers no call
  shows (dread.relations RAN_HOWS), "entered_by": [{"how", "exception"?, "times", "scenarios"}],
  "scenarios": the records}, null without records;
- /api/graph and /api/program: dread relations' and dread groups' records (docs/formats.md), each
  function with its certainty, confidence and review, each group with its functions' certainties, the
  marks it holds and its own name's certainty and review, and the things functions of two groups
  touch (the edges down to an open group's functions); both with the system run's transfers (the
  relations' calls-pointer and enters edges and entered_by, the whole program's "ran");
- /api/timeline?name=&from=&count=: {"scenarios": [{"name", "scenario", "by", "when", "built", "limit",
  "runs": [{"label", "how", "clock", "entries", "kept"}], "first", "differ", "transfers", "pointers",
  "unexplained"}], "name" (the record shown: the one asked for, else the first that differs, else the
  first), "scenario", "by", "first", "kept", "keep", "differ", "built", "limit", "runs" (each run's
  record without its log), "start", "rows": {label: {"log", "at", "in": the function names}}} for the
  entries [from, from + count) (default: a quarter of count before the first difference) that the
  record kept; {"scenarios": []} without records;
- /api/reading?name=: {"name", "lines": [[address or null, line]], "accepted": whether the readable
  table has the function, "why": why there are no lines}, or {"pending": true} while the register
  analysis runs: dread.readable reading, made outside the lock and kept for the load and the readable
  C's tables (/api/function's sources announce it as {"kind": "readable", "deferred": true});
- /api/search?q=: [{"kind": "address", "name", "proposal" or "text", ...}], at most 40;
- /api/program-search?q=: {"query", "functions": [{"name", "group", "how": the kinds that found it,
  "text", "proposed"}], "groups": [{"group", "functions": how many found}], "more": left out beyond
  SEARCH_LIMIT}, or what /api/program says while the groups are found.

Safety: the server listens on 127.0.0.1 unless told otherwise, answers only requests addressed to that
host name (no DNS rebinding), and a change needs the token of the page it served (no other web page can
make one): a rename, a note, starting or stopping a job, the Setup page's changes (a file uploaded, a
target made or opened, llm.toml and key files written). Jobs run DREAD's commands with whatever
arguments the form gives, as the user who runs the server, so they run only where the server listens on
this machine alone (127.0.0.1, the default): whoever can load the page has its token, and on another
address that is anyone who can reach it; the Setup page changes nothing there either. --read-only turns
off every change and job (the jobs' records and logs are still shown). A file uploaded to the Setup
page is refused beyond --upload-limit MiB (default 64) before it is read. One server per target runs
jobs (dread.jobs: a lock in BUILD/gui/jobs); another one on the same target shows them and starts none.
Over SSH: ssh -L 8350:127.0.0.1:8350 HOST, then open http://127.0.0.1:8350/.

    dread gui [--target DIR] [--image NAME] [--host 127.0.0.1] [--port 8350] [--allow-host NAME] [--open]
              [--read-only] [--jobs N] [--upload-limit MIB]
"""
import argparse
import glob
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dread import guipages
from dread import jobs as jobs_
from dread import target as tgt

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # (dread groups runs from here)
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/sim.js": ("sim.js", "text/javascript; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8")}
ORIGINS = ("hand", "pilot", "lifted", "refused", "dropped", "original")
SEARCH_LIMIT = 5000     # the functions one search lights in the whole program at most
TIMELINE_ROWS = 400     # device log entries of each image one timeline answer holds by default
TIMELINE_MAX = 5000     # and at most
UPLOAD_LIMIT = 64 << 20  # bytes a file uploaded to the Setup page may have (dread gui --upload-limit)
KEYWORDS = {"if", "while", "for", "switch", "return", "sizeof", "do", "else"}


# ---- reading the C ----

def strip_c(text):
    """Comments and string literals blanked (newlines kept), so that braces and names in them do not
    count."""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    return re.sub(r'//[^\n]*|/\*.*?\*/|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'', blank, text, flags=re.S)


def definitions(path):
    """{name: (first line, last line)} of the global function definitions in a C file (1-based); a
    static function is left out (it cannot take an original function's place)."""
    with open(path, errors="replace") as f:
        text = f.read()
    plain = strip_c(text)
    out, depth, i, n = {}, 0, 0, len(plain)
    start_of_decl = 0
    while i < n:
        c = plain[i]
        if c == "{":
            if depth == 0:
                head = plain[start_of_decl:i]
                m = re.search(r"\b([A-Za-z_]\w*)\s*\((?:[^()]|\([^()]*\))*\)\s*(?:__attribute__\s*\(\(.*?\)\)\s*)*$",
                              head, re.S)
                if m and m.group(1) not in KEYWORDS and "=" not in head[:m.start()].split("\n")[-1]:
                    body_end = matching_brace(plain, i)
                    first = text.count("\n", 0, start_of_decl + len(head) - len(head.lstrip())) + 1
                    if not re.search(r"\bstatic\b", head[:m.start()]):
                        out[m.group(1)] = (first, text.count("\n", 0, body_end) + 1)
                    i = body_end + 1
                    start_of_decl = i
                    continue
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
            if depth == 0:
                start_of_decl = i + 1
        elif c == ";" and depth == 0:
            start_of_decl = i + 1
        elif c == "#" and depth == 0 and (i == 0 or plain[i - 1] == "\n"):
            end = plain.find("\n", i)
            while end != -1 and plain[end - 1] == "\\":
                end = plain.find("\n", end + 1)
            i = n if end == -1 else end
            start_of_decl = i
            continue
        i += 1
    return out


def matching_brace(plain, i):
    depth = 0
    for j in range(i, len(plain)):
        if plain[j] == "{":
            depth += 1
        elif plain[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(plain) - 1


# ---- the state of one image ----

def mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def read_tsv(path):
    """A table with a header line as [dict]."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = [l.rstrip("\n").split("\t") for l in f if l.strip()]
    if not lines:
        return []
    head = lines[0]
    return [dict(zip(head, row + [""] * (len(head) - len(row)))) for row in lines[1:] if not row[0].startswith("#")]


def addresses(text):
    return [int(a, 16) for a in (text or "").split() if a.startswith("0x")]


def number(text):
    """A number written in a table (0x... or decimal), or -1."""
    try:
        return int(text, 0)
    except (TypeError, ValueError):
        return -1


class State:
    """What the page shows of one image, reloaded when the files it comes from change."""

    def __init__(self, target, image):
        self.target, self.image = target, image
        self.lock = threading.RLock()
        self.stamp = None
        self.analyses = {}
        self.generation = 0
        self.names_generation = 0           # changes when names.tsv, review.tsv or another names table does
        self.runs_generation = 0            # changes when a record of the system run does (BUILD/sysrun)
        self.runs_seen = None               # (the records' stamp, {scenario: record without logs}, transfers)
        self.timeline_record = None         # ((path, mtime), the record with its logs) last shown
        self.threads = []                   # the analyses' and the facts worker's (close joins them)
        self.refresh()

    # -- files --

    def paths(self):
        img, build = self.image, self.target.build
        recon = os.path.join(build, "recon", img.name)
        check = os.path.join(build, "lift-check", img.name)
        return {"verdicts": os.path.join(check, "verdicts.tsv"), "tests": os.path.join(recon, "tests.tsv"),
                "placement": os.path.join(recon, "placement.json"), "recon_lifted": os.path.join(recon, "lifted.json"),
                "check_lifted": os.path.join(check, "lifted.json"), "recon_c": os.path.join(recon, "lifted"),
                "check_c": os.path.join(check, "lifted"), "lift_c": os.path.join(build, "lifted", img.name),
                "autocases": os.path.join(build, "autocases", img.name), "pilot": os.path.join(img.src, "pilot"),
                "payload": os.path.join(recon, "payload.bin")}

    def watched(self):
        from dread.rename import c_files
        img = self.image
        files = [img.table(t) for t in ("functions", "symbols", "annotations", "lifted", "code_ranges", "readable")]
        p = self.paths()
        files += [p[k] for k in ("verdicts", "tests", "placement", "recon_lifted", "check_lifted")]
        files += glob.glob(os.path.join(p["pilot"], "*.c"))
        try:
            files += c_files(img)
        except OSError:
            pass
        return tuple(sorted((f, mtime(f)) for f in files))

    def refresh(self):
        """Reload when a file changed; returns self."""
        with self.lock:
            stamp = self.watched()
            if stamp != self.stamp:
                self.load()
                self.stamp = stamp
            return self

    def load(self):
        from dread import relations
        from dread.build import read_names
        from dread.program import Program
        from dread.rename import c_files
        img, p = self.image, self.paths()
        process = getattr(self, "grouping", None)
        if process is not None and process.poll() is None:  # (it groups the tables as they were)
            process.terminate()
        self.program = Program(self.target, img)
        self.generation += 1
        self.verdicts = {r["function"]: r for r in read_tsv(p["verdicts"])}
        self.tests = {r["function"]: r for r in read_tsv(p["tests"])}
        self.placement = {}
        if os.path.exists(p["placement"]):
            with open(p["placement"]) as f:
                self.placement = json.load(f).get("functions", {})
        self.unreached, self.system = {}, set()
        for key in ("check_lifted", "recon_lifted"):        # the build's record wins
            if os.path.exists(p[key]):
                with open(p[key]) as f:
                    data = json.load(f)
                self.unreached.update({n: list(a) for n, a in data.get("unreached", {}).items()})
                self.system |= set(data.get("system", ()))
        self.lifted = set(read_names(img.table("lifted")))
        self.readable = set(read_names(img.table("readable")))     # (what dread readable --check accepted)
        self.readings = {}                                  # name -> (the tables' stamp, its reading form)
        self.reading_context = None                         # (the tables' stamp, a dread.readable Context)
        self.pilot = {os.path.basename(f)[:-2] for f in glob.glob(os.path.join(p["pilot"], "*.c"))}
        self.sources = {}                                   # name -> (path, first, last)
        for path in c_files(img):
            if path.endswith(".c"):
                try:
                    for name, (first, last) in definitions(path).items():
                        self.sources.setdefault(name, (path, first, last))
                except OSError:
                    pass
        self.origin = {}
        for fn in self.program.functions:
            name = fn.name
            verdict = self.verdicts.get(name, {}).get("verdict")
            if name in self.pilot:
                self.origin[name] = "pilot"
            elif name in self.sources:
                self.origin[name] = "hand"
            elif name in self.lifted:
                self.origin[name] = "lifted"
            elif verdict in ("refused", "dropped"):
                self.origin[name] = verdict
            else:
                self.origin[name] = "original"
        self.suite = None
        self.lifter = None                                  # one per load: it keeps what it learns
        self.analyses = {}
        self.names = relations.Names(self.target, img, self.program)     # how sure each name is (name_table)
        self.names_stamp = self.names.stamp
        self.made = {}                                      # what is made from the names: key -> (stamp, value)
        self.relations = None                               # the call maps for the graphs (call_names)
        self.known = {}                                     # name -> its facts (dread.relations), made
        self.wanted, self.worker = [], None                 # what the facts worker makes next
        self.grouped = None                                 # dread groups' record (load()), or why not
        self.grouper, self.grouping, self.grouped_since = None, None, None     # its thread, process, start
        self.grouped_from = None                            # "kept" (BUILD/groups) or "dread groups" (run now)
        self.whole = None                                   # (its inputs' stamp, the program graph made)
        self.start(self.analyse, self.program, self.generation)

    def start(self, work, *args):
        """A thread of this state's, kept so that close() can wait for it. Called with the lock held."""
        self.threads = [t for t in self.threads if t.is_alive()]
        thread = threading.Thread(target=work, args=args, daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread

    def close(self, timeout=120):
        """Stop the state's threads (the analyses end after the step they are in, the facts worker after
        its function) and wait for them; returns whether all ended. A thread left running while the
        process forks a pool (DREAD's pools fork) may hold a lock the child then waits for forever: the
        tests after the GUI's hung on CI so (2026-10-22), Python warning that the process was
        multi-threaded at every fork."""
        with self.lock:
            self.generation += 1
            self.wanted = []
            threads = list(self.threads)
            process = self.grouping
        if process is not None and process.poll() is None:      # (dread groups: its thread waits for it)
            process.terminate()
        deadline = time.time() + timeout
        for thread in threads:
            thread.join(max(0.0, deadline - time.time()))
        return not any(t.is_alive() for t in threads)

    def analyse(self, program, generation):
        """The slow analyses, one after another, each stored as soon as it is done."""
        from dread import flat, noreturn, regflow
        steps = [("callers", lambda: flat.callers_of(program)), ("endless", lambda: noreturn.find(program)),
                 ("flow", lambda: regflow.load(self.target, self.image, program)),
                 ("texts", lambda: self.text_index(program))]
        for key, work in steps:
            try:
                value = work()
            except Exception as e:                      # the page shows the gap and goes on
                value = e
            with self.lock:
                if generation != self.generation:
                    return
                self.analyses[key] = value

    def ready(self, key):
        value = self.analyses.get(key)
        return None if isinstance(value, Exception) else value

    def pending(self):
        return [k for k in ("callers", "endless", "flow", "texts") if k not in self.analyses]

    def text_index(self, program):
        """[(text, function name, address)] of the texts the functions load the address of."""
        from dread.constants import string_at
        out = []
        for fn in program.functions:
            for insn in program.instructions(fn):
                if insn is None:
                    continue
                lit = program.arch.literal(insn, program.isa_at(insn.address))
                value = None
                if lit is not None and lit[1] == 4 and program.in_image(lit[0], 4):
                    value = program.word(lit[0])
                else:
                    value = program.arch.pc_address(insn, program.isa_at(insn.address))
                if value is not None:
                    text = string_at(program, value)
                    if text:
                        out.append((text, fn.name, insn.address))
        return out

    def cases_suite(self):
        if self.suite is None:
            from dread.cases import Suite
            from dread.emu import Layout
            try:
                self.suite = Suite(self.target, self.image, self.program, Layout(self.target, self.image)).load()
            except Exception as e:
                self.suite = e
        return None if isinstance(self.suite, Exception) else self.suite

    # -- answers --

    def status(self):
        self.name_table()
        self.sysruns()
        return {"generation": self.generation, "pending": self.pending(), "names": self.names_generation,
                "runs": self.runs_generation}

    def name_table(self):
        """The image's dread.relations Names (how sure each name is, what the review said of it), read
        again where one of its tables changed; names_generation counts the changes, so that the page
        reloads what it shows of the names. Called with the lock held."""
        names = self.names.refresh()
        if names.stamp != self.names_stamp:
            self.names_stamp = names.stamp
            self.names_generation += 1
        return names

    def cached(self, key, stamp, make):
        """What make() returns, kept until stamp changes (the names' and the tables' records)."""
        got = self.made.get(key)
        if got is None or got[0] != stamp:
            got = self.made[key] = (stamp, make())
        return got[1]

    def runs(self):
        p = self.paths()
        out = {k: mtime(p[f]) or None for k, f in (("recon", "payload"), ("test", "tests"), ("lift_check", "verdicts"))}
        stamp = self.sysruns_stamp()
        out["sysrun"] = max((t for _, t in stamp), default=0) or None
        return out

    # -- the system run's records (dread sysrun, dread lift --check's whole image: dread.sysrun) --

    def sysruns_stamp(self):
        from dread import sysrun
        return tuple((path, mtime(path)) for path in sysrun.record_paths(self.target, self.image))

    def sysruns(self):
        """({scenario: its record without the logs}, the transfers into functions of them all, merged:
        dread.sysrun ran), read again where a record changed (runs_generation counts the changes, so
        that the page shows them anew). Records of other bytes than this image's are left out. Called
        with the lock held."""
        from dread import sysrun
        stamp = self.sysruns_stamp()
        if self.runs_seen is None or self.runs_seen[0] != stamp:
            found = sysrun.records(self.target, self.image, payload=self.program.data, logs=False)
            if self.runs_seen is not None:
                self.runs_generation += 1
            self.runs_seen = (stamp, found, sysrun.ran(self.program, found))
        return self.runs_seen[1], self.runs_seen[2]

    def sysrun_list(self):
        """The records for the overview and the timeline's choice: each scenario's tool, when, how each
        image's run ended, its clock and entries, where the logs first differ, the transfers counted."""
        from dread import sysrun
        found, _ = self.sysruns()
        stamps = dict(self.runs_seen[0])
        out = []
        for name, record in sorted(found.items()):
            path = os.path.join(sysrun.record_dir(self.target, self.image), name + ".json")
            calls = record.get("calls", [])
            pointers = [t for t in calls if t.get("how") in ("pointer", "pointer-jump")]
            out.append({"name": name, "scenario": record.get("scenario", name), "by": record.get("by"),
                        "when": stamps.get(path) or None,
                        "built": record.get("built", 0), "limit": record.get("limit"),
                        "runs": [{k: run.get(k) for k in ("label", "how", "clock", "entries", "kept")}
                                 for run in record["runs"]],
                        "first": record.get("first", 0), "differ": record.get("differ"),
                        "transfers": sum(t.get("times", 0) for t in calls),
                        "pointers": sum(t.get("times", 0) for t in pointers),
                        "unexplained": record.get("unexplained", 0)})
        return out

    def timeline(self, name=None, start=None, count=TIMELINE_ROWS):
        """The device logs of a record side by side (/api/timeline): the records (by name: the scenario's,
        or SCENARIO.lift-check for the lift check's whole image), the chosen one's runs (how each ended,
        clock, entries, model reports, interrupts), where the logs first
        differ (compare's lines) and, from the absolute entry `start` (default: a little before the
        first difference, else 0), count entries of each run's log with the pc of each and the function
        it lies in (the original's as the tables name it now, the rebuilt one's as the record does), as
        far as the record kept them (its window: "first" and "kept")."""
        from dread import sysrun
        listed = self.sysrun_list()
        out = {"scenarios": listed, "status": self.status()}
        if not listed:
            return out
        names = [r["name"] for r in listed]
        name = name if name in names else next((r["name"] for r in listed if r["differ"]), names[0])
        path = os.path.join(sysrun.record_dir(self.target, self.image), name + ".json")
        key = (path, mtime(path))
        if self.timeline_record is None or self.timeline_record[0] != key:
            record = sysrun.read_record(path, sysrun.digest(self.program.data))
            if record is None:
                return dict(out, name=name, error="the record %s does not read" % name)
            self.timeline_record = (key, record)
        record = self.timeline_record[1]
        first, runs = record.get("first", 0), record["runs"]
        kept = max((len(r.get("log", ())) for r in runs), default=0)
        differ = record.get("differ")
        try:
            count = max(1, min(int(count), TIMELINE_MAX))
            start = int(start) if start is not None else max(first, ((differ or {}).get("at") or first) - count // 4)
        except (TypeError, ValueError):
            start, count = first, TIMELINE_ROWS
        lo, hi = max(start, first) - first, min(start + count, first + kept) - first
        functions = record.get("functions", [])
        rows = {}
        for run in runs:
            log, at, where = run.get("log", []), run.get("at"), run.get("in")
            got = {"log": log[lo:hi] if hi > lo else []}
            if at is not None and where is not None:
                got["at"] = at[lo:hi] if hi > lo else []
                fns = []
                for pc, k in zip(got["at"], where[lo:hi]):
                    fn = self.program.function_at(pc) if run.get("label") == "original" and pc is not None else None
                    fns.append(fn.name if fn is not None else functions[k] if 0 <= k < len(functions) else None)
                got["in"] = fns
            rows[run.get("label")] = got
        out.update(name=name, scenario=record.get("scenario", name), by=record.get("by"), first=first, kept=kept,
                   differ=differ, built=record.get("built", 0),
                   limit=record.get("limit"), keep=record.get("keep"),
                   runs=[{k: v for k, v in run.items() if k not in ("log", "at", "in")} for run in runs],
                   start=lo + first, rows=rows)
        return out

    def functions(self):
        names = self.name_table()
        return self.cached("functions", names.stamp, lambda: self.make_functions(names))

    def make_functions(self, names):
        """The function list: each function's place in the image, origin and test record, and how sure
        its name is (Names.about: its certainty, the vote's agreement, the proposal it goes by, the
        review's mark with the outcome, and the old name where the review changed it)."""
        prog = self.program
        out = []
        for fn in prog.functions:
            name = fn.name
            test = self.tests.get(name)
            verdict = self.verdicts.get(name, {})
            missed = bool(test.get("missed")) if test else \
                verdict.get("verdict") == "dropped" and bool(verdict.get("missed"))
            row = {"name": name, "entry": fn.entry, "size": prog.end(fn) - fn.entry, "origin": self.origin[name],
                   "system": name in self.system, "failed": bool(test and int(test.get("failed") or 0)),
                   "missed": missed}
            row.update(self.name_brief(names, fn))
            out.append(row)
        return out

    def name_brief(self, names, fn):
        """A function's certainty, the vote's agreement and the proposal it goes by, and the review's
        mark (with its outcome, and the old name where it changed the name): the list's and the
        overview's share of Names.about."""
        about = names.about(fn)
        out = {"certainty": about["certainty"]}
        naming_, proposal = names.function(fn)
        if naming_ == "proposed":
            out["proposed"] = proposal[0]
        if about["confidence"] is not None:
            out["confidence"] = about["confidence"]
        review = about["review"]
        if review and review["mark"]:
            made = review["changed"] or review["last"]
            out["review"] = {"mark": review["mark"], "outcome": review["last"].get("outcome")}
            if review["mark"] == "changed" and made.get("old"):
                out["review"]["old"] = made["old"]
        return out

    def overview(self):
        prog, img, target = self.program, self.image, self.target
        counts = {o: {"functions": 0, "bytes": 0} for o in ORIGINS}
        for fn in prog.functions:
            c = counts[self.origin[fn.name]]
            c["functions"] += 1
            c["bytes"] += prog.end(fn) - fn.entry
        groups = {}
        for name, row in self.verdicts.items():
            if row["verdict"] == "refused" and name in prog.by_name:
                key = re.sub(r"0x[0-9a-f]+", "…", row["reason"])
                key = re.sub(r"\b\d+\b", "N", key)
                groups.setdefault(key, []).append(name)
        refused = sorted(({"reason": k, "functions": sorted(v, key=lambda n: prog.by_name[n].entry)}
                          for k, v in groups.items()), key=lambda g: -len(g["functions"]))
        dropped = [{"name": n, "reason": r["reason"]} for n, r in sorted(self.verdicts.items())
                   if r["verdict"] == "dropped" and n in prog.by_name and self.origin.get(n) == "dropped"]
        failing = [{"name": n, "cases": int(r["cases"] or 0), "failed": int(r["failed"] or 0)}
                   for n, r in sorted(self.tests.items()) if int(r.get("failed") or 0)]
        uncovered = [{"name": n, "missed": len(addresses(r.get("missed")))}
                     for n, r in sorted(self.tests.items()) if r.get("missed")]
        return {"target": {"name": target.name, "title": getattr(target, "title", None) or target.name,
                           "root": target.root},
                "image": {"name": img.name, "images": list(target.images), "arch": img.arch.name,
                          "isa": getattr(img.arch, "isa", None), "base": img.base, "end": img.end,
                          "functions": len(prog.functions)},
                "counts": counts, "refused": refused, "dropped": dropped, "failing": failing,
                "uncovered": uncovered, "names": self.names_overview(), "runs": self.runs(),
                "sysrun": self.sysrun_list(), "status": self.status()}

    def names_overview(self):
        """How sure the names are, for the overview: the functions and bytes of each level of
        dread.relations CERTAINTY, and what the reviews said (review.tsv, dread.namereview standing):
        the names they flagged, the suspects they left, the names they changed (the old name and why),
        how many they confirmed, the groups' names they challenged, and how many rows speak of a name
        no longer in use ("stale")."""
        from dread import pilot
        names = self.name_table()
        path = pilot.subsystems_path(self.target, self.image)
        return self.cached("names", (names.stamp, mtime(path)), lambda: self.make_names_overview(names, path))

    def make_names_overview(self, names, path):
        from dread import namereview, pilot, relations
        prog = self.program
        counts = {c: {"functions": 0, "bytes": 0} for c in relations.CERTAINTY}
        review = {"flagged": [], "suspect": [], "changed": [], "confirmed": [], "groups": [], "stale": 0,
                  "challenges": 0, "reviews": 0}
        numbers = set()
        for fn in prog.functions:
            about = names.about(fn)
            c = counts[about["certainty"]]
            c["functions"] += 1
            c["bytes"] += prog.end(fn) - fn.entry
            got = about["review"]
            if got is None:
                continue
            review["stale"] += got["stale"]
            review["challenges"] += got["challenges"]
            numbers |= {r.get("review") for r in names.reviewed.get(fn.entry, [])}
            if got["mark"]:
                item = dict(self.name_brief(names, fn), name=fn.name, entry=fn.entry, last=got["last"])
                if got["changed"]:
                    item["changed"] = got["changed"]
                review[got["mark"]].append(item)
        rows = pilot.read_subsystems(path)
        for key, challenged in sorted(names.reviewed_groups.items(), key=lambda kv: (number(kv[1][0].get("address")),
                                                                                        kv[0])):
            numbers |= {r.get("review") for r in challenged}
            row = rows.get(key)
            got = namereview.standing(challenged, namereview.group_label(row) if row else None)
            review["challenges"] += got["challenges"]
            review["stale"] += got["stale"]
            if got["mark"]:
                review["groups"].append({"members": key, "label": row.get("name") if row else None,
                                         "prefix": row.get("prefix") if row else None, "mark": got["mark"],
                                         "last": got["last"], "changed": got["changed"]})
        review["reviews"] = len(numbers - {None, ""})
        for k in ("flagged", "suspect", "changed"):
            review[k].sort(key=lambda item: (-(item["last"].get("score") or 0), item["entry"]))
        review["confirmed"] = [{"name": i["name"], "proposed": i.get("proposed")} for i in review["confirmed"]]
        return {"counts": counts, "review": review if review["challenges"] else None}

    def function(self, name):
        from dread import flat
        prog = self.program
        fn = prog.by_name.get(name)
        if fn is None:
            raise KeyError(name)
        header, rows = flat.rows(prog, fn, self.ready("flow"), self.ready("endless"), self.ready("callers"))
        header["callers"] = [{"name": n, "address": a} for n, a in header["callers"]]
        out = {"header": header, "rows": rows, "origin": self.origin[name], "system": name in self.system,
               "size": prog.end(fn) - fn.entry, "status": self.status()}
        out["name"] = self.name_table().about(fn, history=True)
        out["verdict"] = self.verdicts.get(name)
        out["test"] = self.tests.get(name)
        out["placement"] = self.placement.get(name)
        out["coverage"] = self.coverage(fn)
        out["sources"] = self.c_sources(fn)
        out["cases"] = self.cases_of(name)
        out["ran"] = self.ran_of(name)
        return out

    def ran_of(self, name):
        """What the system run's records saw of a function beyond its calls (dread.relations RAN_HOWS:
        through a pointer, a return to its entry, the code before running into it): its callers and
        callees so, and how the core entered it (CORE_HOWS: an exception, the reset), each with how,
        how often, the call's address and the scenarios; None without records."""
        from dread import relations
        found, ran = self.sysruns()
        if not found:
            return None
        pick = ("how", "times", "site", "scenarios", "exception")
        went = [r for r in ran if r["how"] in relations.RAN_HOWS and r.get("from") and r.get("to")
                and r["from"] != r["to"]]
        return {"callers": [dict({k: r[k] for k in pick if k in r}, name=r["from"]) for r in went if r["to"] == name],
                "callees": [dict({k: r[k] for k in pick if k in r}, name=r["to"]) for r in went if r["from"] == name],
                "entered_by": [{k: r[k] for k in pick if k in r} for r in ran
                               if r.get("to") == name and r["how"] in relations.CORE_HOWS],
                "scenarios": sorted(found)}

    def coverage(self, fn):
        """Which instructions the cases ran, from the newest record that has the function."""
        name, p = fn.name, self.paths()
        unreached = self.unreached.get(name, [])
        test = self.tests.get(name)
        verdict = self.verdicts.get(name, {})
        if test is not None:
            return {"from": "dread test", "when": mtime(p["tests"]), "missed": addresses(test.get("missed")),
                    "unreached": unreached}
        if verdict.get("verdict") == "accepted" or (name in self.lifted and verdict.get("verdict") != "dropped"):
            return {"from": "dread lift --check", "when": mtime(p["verdicts"]) or None, "missed": [],
                    "unreached": unreached}
        if verdict.get("verdict") == "dropped" and verdict.get("missed"):
            return {"from": "dread lift --check", "when": mtime(p["verdicts"]), "missed": addresses(verdict["missed"]),
                    "unreached": unreached}
        return None

    def c_sources(self, fn):
        """The C of the function: its source when src defines it, else its readable C to be asked for
        (/api/reading: "deferred"), then the lifted C (built, or lifted now)."""
        out = []
        root = self.target.root
        if fn.name in self.sources:
            path, first, last = self.sources[fn.name]
            with open(path, errors="replace") as f:
                lines = f.read().splitlines()
            out.append({"kind": "pilot" if fn.name in self.pilot else "source", "path": os.path.relpath(path, root),
                        "first": first, "text": "\n".join(lines[first - 1:last])})
        else:
            out.append({"kind": "readable", "deferred": True, "accepted": fn.name in self.readable})
        p = self.paths()
        for key, how in (("recon_c", "built by dread recon"), ("check_c", "built by dread lift --check"),
                         ("lift_c", "written by dread lift")):
            path = os.path.join(p[key], fn.name + ".c")
            if os.path.exists(path):
                with open(path) as f:
                    out.append({"kind": "lifted", "path": os.path.relpath(path, root), "first": 1, "text": f.read(),
                                "how": how})
                break
        else:
            from dread import lift
            if "flow" not in self.analyses:
                out.append({"kind": "lifted", "path": None, "pending": True})
                return out
            try:
                if self.lifter is None:
                    self.lifter = lift.make_lifter(self.program, self.ready("flow"), self.ready("endless"))
                text = self.lifter.lift(fn).text
                out.append({"kind": "lifted", "path": None, "first": 1, "text": text,
                            "how": "lifted now, not built or tested"})
            except lift.Refused as e:
                out.append({"kind": "lifted", "path": None, "refused": str(e)})
            except Exception as e:
                out.append({"kind": "lifted", "path": None, "refused": "the lifter failed: %s" % e})
        return out

    def reading_stamp(self):
        """What the reading form is made from beside the lifted C: the readable C's tables."""
        return tuple(mtime(self.image.table(t)) for t in ("readable_types", "readable_names"))

    def reading(self, name):
        """The function's readable C in the reading form (dread readable --plain: dread.readable
        reading): {"name", "lines": [[address or None, line]], "accepted": whether the readable table
        has it (the gate built it from this C and it agreed), "why": why there is none ("lines" then
        null)}, or {"pending": true} while the register analysis runs. The lift is made under the lock
        (it may run the program in the emulator), the readable C and its context outside it (0.1-0.5 s
        a function on a FreeRTOS build, seconds for the largest: the page goes on meanwhile); kept for
        this load and these tables (reading_stamp)."""
        from dread import lift, readable
        with self.lock:
            fn = self.program.by_name.get(name)
            if fn is None:
                raise KeyError(name)
            if "flow" not in self.analyses or "endless" not in self.analyses:
                return {"name": name, "pending": True, "status": self.status()}
            key = (self.generation, self.reading_stamp())
            got = self.readings.get(name)
            if got is not None and got[0] == key:
                return dict(got[1], status=self.status())
            context = self.reading_context[1] if self.reading_context and self.reading_context[0] == key else None
            program, flow = self.program, self.ready("flow")
            out = {"name": name, "lines": None, "accepted": name in self.readable, "why": None}
            try:
                if self.lifter is None:
                    self.lifter = lift.make_lifter(self.program, flow, self.ready("endless"))
                text = self.lifter.lift(fn).text
            except lift.Refused as e:
                text, out["why"] = None, "the lifter refuses it: %s" % e
            except Exception as e:
                text, out["why"] = None, "the lifter failed: %s" % e
        if text is not None:
            try:
                if context is None:
                    context = readable.Context(self.target, self.image, program, flow)
                lines = readable.reading(name, text, context)
            except Exception as e:              # (a stage's fault: the page says so)
                lines, out["why"] = None, "dread readable failed: %s" % e
            if lines is None and out["why"] is None:
                out["why"] = "its lifted C is not the dialect dread.cparse reads"
            out["lines"] = [[a, line] for a, line in lines] if lines is not None else None
        with self.lock:
            if self.generation == key[0]:
                self.readings[name] = (key, out)
                if context is not None and (self.reading_context is None or self.reading_context[0] != key):
                    self.reading_context = (key, context)
            return dict(out, status=self.status())

    def cases_of(self, name):
        img, p = self.image, self.paths()
        out = {}
        for key, path in (("generated", os.path.join(p["autocases"], name + ".json")),
                          ("written", os.path.join(img.extra_cases, name + ".json")),
                          ("kept", os.path.join(img.kept_cases, name + ".json"))):
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        out[key] = len(json.load(f).get("cases", []))
                except (OSError, ValueError):
                    pass
        suite = self.cases_suite()
        if suite is not None:
            if suite.cases.get(name):
                out["by hand"] = len(suite.cases[name])
            if name in suite.auto:
                out["auto"] = True
        return out

    def graph(self, name, depth=1):
        """The relations around a function (dread.relations graph) with what is known now: the calls
        once the callers are found; the memory, texts and structures of each function once its facts
        are made, which the worker does in the background, nearest first ("pending": the functions
        still without them; the page asks again)."""
        from dread import relations
        prog = self.program
        if name not in prog.by_name:
            raise KeyError(name)
        try:
            depth = max(0, min(int(depth), relations.MAX_DEPTH))
        except ValueError:
            depth = relations.DEPTH
        if "callers" not in self.analyses:
            return {"center": name, "depth": depth, "nodes": [], "edges": [], "pending": [], "waiting": "callers",
                    "status": self.status()}
        calls, names = self.call_names()
        origin = self.origin
        _, ran = self.sysruns()
        out = relations.graph(prog, name, depth, calls, self.known, names,
                              extra=lambda n: {"origin": origin.get(n, "original")}, ran=ran)
        self.want(out["missing"])
        out["pending"] = out.pop("missing")
        out["status"] = self.status()
        return out

    def call_names(self):
        """(dread.relations call maps, its Names read again where a table changed), once the callers are
        found. Called with the lock held."""
        from dread import relations
        if self.relations is None:
            self.relations = relations.call_maps(self.program, self.ready("callers") or {})
        return self.relations, self.name_table()

    def program_graph(self):
        """The whole program by groups (dread.groups program_graph, docs/formats.md), once the callers
        are found and dread groups has found the groups ("waiting": what is not yet; the page asks
        again). The groups are found on the first request (make_groups) and kept for this load; the
        graph is made again when the names or the pilot's subsystem names change."""
        from dread import groups, pilot
        if "callers" not in self.analyses:
            return {"waiting": "callers", "status": self.status()}
        if self.grouped is None:
            if self.grouper is None:
                self.grouped_since = time.time()
                self.grouper = self.start(self.make_groups, self.generation)
            return {"waiting": "groups", "seconds": round(time.time() - self.grouped_since, 1),
                    "status": self.status()}
        if isinstance(self.grouped, Exception):
            return {"error": "dread groups: %s" % self.grouped, "status": self.status()}
        calls, names = self.call_names()
        path = pilot.subsystems_path(self.target, self.image)
        _, ran = self.sysruns()
        stamp = (names.stamp, mtime(path), self.runs_seen[0])
        if self.whole is None or self.whole[0] != stamp:
            origin = self.origin
            made = groups.program_graph(self.program, self.grouped["groups"], self.grouped["touched"], calls, names,
                                        pilot.read_subsystems(path),
                                        extra=lambda n: {"origin": origin.get(n, "original")}, ran=ran)
            self.whole = (stamp, made)
        return dict(self.whole[1], groups_from=self.grouped_from, status=self.status())

    def make_groups(self, generation):
        """The groups thread: what dread groups kept for these tables, or dread groups run now in a
        process of its own (its pool forks; this server has threads): at once with the evidence from
        the listing (the default), once the register analysis is done with the evidence from the
        readable C (the tool would make it again). close() and a new load end the process."""
        from dread import groups
        while groups.EVIDENCE == "readable":
            with self.lock:
                if generation != self.generation:
                    return
                if "flow" in self.analyses:
                    break
            time.sleep(0.2)
        try:
            record, source = groups.kept(self.target, self.image), "kept"
            if record is None:
                source = "dread groups"
                command = [sys.executable, "-m", "dread.cli", "groups", "--target", self.target.root,
                           "--image", self.image.name, "--brief"]
                with self.lock:
                    if generation != self.generation:
                        return
                    self.grouping = process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                                               text=True)
                _, err = process.communicate()
                if generation != self.generation:
                    return
                if process.returncode:
                    lines = [l for l in (err or "").splitlines() if l.strip()]
                    raise RuntimeError(lines[-1] if lines else "it ended with %d" % process.returncode)
                record = groups.kept(self.target, self.image)
                if record is None:
                    raise RuntimeError("it kept no groups for these tables (did they change meanwhile?)")
        except Exception as e:                  # (the page says so)
            record = e
        with self.lock:
            if generation == self.generation:
                self.grouped, self.grouper, self.grouping = record, None, None
                self.grouped_from = source if not isinstance(record, Exception) else None

    def want(self, names):
        """Facts for these functions next, in this order, instead of what was wanted before (the page
        shows one neighbourhood at a time). Called with the lock held."""
        self.wanted = [n for n in names if n not in self.known]
        if self.wanted and self.worker is None:
            self.worker = self.start(self.make_facts, self.generation)

    def make_facts(self, generation):
        """The facts worker: one function at a time, the lift under the lock, the readable C without
        it (dread.relations Facts); it waits for the register analysis and the never-returning
        functions, and ends when nothing is wanted or the state was loaded anew."""
        from dread import relations
        made = None
        while True:
            with self.lock:
                if generation != self.generation or not self.wanted:
                    if generation == self.generation:
                        self.worker = None
                    return
                ready = "flow" in self.analyses and "endless" in self.analyses
                if ready:
                    name = self.wanted.pop(0)
                    if made is None:
                        made = relations.Facts(self.target, self.image, self.program, self.ready("flow"),
                                               self.ready("endless"), lock=self.lock)
            if not ready:
                time.sleep(0.2)
                continue
            try:
                facts = made.get(name)
            except Exception as e:              # (the page says so and goes on)
                facts = {"from": "nothing", "why": "dread relations failed: %s" % e, "digest": None, "refs": []}
            with self.lock:
                if generation != self.generation:
                    return
                self.known[name] = facts

    def search(self, query, limit=40):
        """[{"kind", "name", "origin", ...}] of the functions a query finds, at most limit (None: all):
        the one an address is in ("address"), the names that hold it ("name"; the best first), the
        names dread pilot proposed that hold it ("proposal", with "proposed"), the texts that hold it
        ("text", with the address that refers to it, once the text index is made)."""
        prog = self.program
        query = query.strip()
        if not query:
            return []
        out, seen = [], set()

        def add(kind, name, **more):
            if (kind, name, more.get("address")) not in seen and (limit is None or len(out) < limit):
                seen.add((kind, name, more.get("address")))
                out.append(dict(kind=kind, name=name, origin=self.origin.get(name), **more))

        try:
            value = int(query, 16 if re.fullmatch(r"[0-9a-fA-F]{4,}", query) else 0)
            fn = prog.function_at(value & ~1)
            if fn is not None:
                add("address", fn.name, address=value, entry=fn.entry)
        except ValueError:
            pass
        low = query.lower()

        def best(fns, of):
            return sorted(fns, key=lambda fn: (of(fn).lower() != low, not of(fn).lower().startswith(low), len(of(fn)),
                                               of(fn), fn.entry))
        for fn in best([fn for fn in prog.functions if low in fn.name.lower()], lambda fn: fn.name):
            add("name", fn.name, entry=fn.entry)
        names = self.name_table()
        proposed = {}
        for fn in prog.functions:
            naming_, proposal = names.function(fn)
            if proposal and naming_ == "proposed" and low in proposal[0].lower():
                proposed[fn.name] = proposal[0]
        for fn in best([prog.by_name[n] for n in proposed], lambda fn: proposed[fn.name]):
            add("proposal", fn.name, entry=fn.entry, proposed=proposed[fn.name])
        texts = self.ready("texts") or []
        for text, name, address in texts:
            if low in text.lower():
                add("text", name, address=address, text=text[:120])
        return out

    def program_search(self, query):
        """What a search lights in the whole program (/api/program-search): every function the query
        finds (search(): by its address, its name, the name proposed for it, the texts it refers to),
        each once with its group and how it was found, and the groups that hold them with how many
        each; SEARCH_LIMIT functions at most ("more": how many were left out). Waits like
        program_graph for the groups."""
        graph = self.program_graph()
        if "groups" not in graph:
            return graph
        group_of = {f["name"]: f["group"] for f in graph["functions"]}
        found, order, left = {}, [], set()
        for r in self.search(query, limit=None):
            item = found.get(r["name"])
            if item is None:
                if len(order) >= SEARCH_LIMIT:
                    left.add(r["name"])
                    continue
                item = found[r["name"]] = {"name": r["name"], "group": group_of.get(r["name"]), "how": []}
                order.append(item)
            if r["kind"] not in item["how"]:
                item["how"].append(r["kind"])
            if r["kind"] == "text" and "text" not in item:
                item["text"] = r["text"]
            if r["kind"] == "proposal":
                item["proposed"] = r["proposed"]
        groups = {}
        for item in order:
            if item["group"] is not None:
                groups[item["group"]] = groups.get(item["group"], 0) + 1
        return {"query": query.strip(), "functions": order, "more": len(left),
                "groups": [{"group": g, "functions": n} for g, n in sorted(groups.items())], "status": self.status()}

    # -- changes --

    def rename(self, old, new):
        from dread import rename
        with self.lock:
            changes = rename.rename(self.target, self.image, {old: new})
            self.stamp = None
            self.refresh()
        return [os.path.relpath(path, self.target.root) + (" -> " + os.path.relpath(move, self.target.root)
                                                           if move else "") for path, _, move in changes]

    def annotate(self, address, text):
        from dread.program import write_annotation
        with self.lock:
            if self.program.function_at(address) is None and address not in self.program.code:
                raise ValueError("0x%x is not in the code of the image" % address)
            write_annotation(self.image.table("annotations"), address, text.splitlines())
            self.stamp = None
            self.refresh()


# ---- the server ----

class Handler(BaseHTTPRequestHandler):
    server_version = "dread-gui"
    target = None           # set by serve()
    default = None
    states = None
    token = None
    hosts = ()

    def log_message(self, fmt, *args):      # quiet: the terminal is for the user
        pass

    def allowed(self):
        host = self.headers.get("Host", "")
        name = host[:host.rindex("]") + 1] if host.startswith("[") else host.rsplit(":", 1)[0]
        if name.lower() not in self.hosts:
            self.reply(403, {"error": "unknown host name %r: open the page by 127.0.0.1 or localhost" % host})
            return False
        return True

    def state(self, query):
        """The State of the image the request names (default: the server's)."""
        with self.lock:
            if self.target is None:
                raise tgt.TargetError("no target yet: the Setup page (#/setup) makes or opens one")
            name = query.get("image") or self.default
            if name not in self.states:
                self.states[name] = State(self.target, self.target.image(name))
            return self.states[name]

    def target_brief(self):
        """{"name", "title", "root", "image", "images"} of the target shown, or None."""
        t = self.target
        return None if t is None else {"name": t.name, "title": t.title, "root": t.root, "image": self.default,
                                       "images": list(t.images)}

    def reply(self, code, data, kind="application/json"):
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if kind.startswith("text/html"):
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; "
                                                        "style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed():
            return
        url = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        if url.path in STATIC:
            name, kind = STATIC[url.path]
            with open(os.path.join(WEB, name), "rb") as f:
                body = f.read()
            if name == "index.html":
                body = body.replace(b"{{TOKEN}}", self.token.encode())
            return self.reply(200, body, kind)
        asset = guipages.assets().get(url.path)
        if asset is not None:                       # (a page's script or stylesheet)
            with open(asset[0], "rb") as f:
                return self.reply(200, f.read(), asset[1])
        if not url.path.startswith("/api/"):
            return self.reply(404, {"error": "not found"})
        if url.path == "/api/gui":
            from dread import __version__
            return self.reply(200, {"pages": [p.brief() for p in guipages.PAGES.values()],
                                    "read_only": bool(self.server.read_only), "version": __version__,
                                    "status": guipages.statuses(self.server), "target": self.target_brief(),
                                    "local": bool(self.server.local), "home": self.server.home,
                                    "upload_limit": self.server.upload_limit})
        found = guipages.ROUTES.get(("GET", url.path))
        if found is not None:
            if found.change and self.refused():
                return
            return self.answer(found, query)
        if url.path == "/api/status" and self.target is None:      # (no target yet: the pages' share alone)
            return self.reply(200, {"generation": 0, "pending": [], "names": None, "runs": None, "target": None,
                                    "pages": guipages.statuses(self.server)})
        try:
            state = self.state(query).refresh()
        except tgt.TargetError as e:
            return self.reply(409 if self.target is None else 404, {"error": str(e), "setup": self.target is None})
        try:
            if url.path == "/api/reading":          # (makes readable C outside the lock: reading())
                return self.reply(200, state.reading(query.get("name", "")))
            with state.lock:
                if url.path == "/api/overview":
                    return self.reply(200, state.overview())
                if url.path == "/api/functions":
                    return self.reply(200, state.functions())
                if url.path == "/api/function":
                    return self.reply(200, state.function(query.get("name", "")))
                if url.path == "/api/search":
                    return self.reply(200, state.search(query.get("q", "")))
                if url.path == "/api/graph":
                    return self.reply(200, state.graph(query.get("name", ""), query.get("depth", "1")))
                if url.path == "/api/program":
                    return self.reply(200, state.program_graph())
                if url.path == "/api/program-search":
                    return self.reply(200, state.program_search(query.get("q", "")))
                if url.path == "/api/timeline":
                    return self.reply(200, state.timeline(query.get("name"), query.get("from"),
                                                          query.get("count", TIMELINE_ROWS)))
                if url.path == "/api/status":
                    return self.reply(200, dict(state.status(), pages=guipages.statuses(self.server),
                                                target=self.target.root))
        except KeyError as e:
            return self.reply(404, {"error": "no function %s" % e})
        return self.reply(404, {"error": "not found"})

    def answer(self, found, query, data=None, body=None):
        """A page's route (dread.guipages): its answer, or the error it ended with."""
        try:
            got = found.fn(guipages.Request(self, query, data, body))
        except guipages.Failure as e:
            return self.reply(e.status, {"error": str(e)})
        except tgt.TargetError as e:
            return self.reply(404, {"error": str(e)})
        except KeyError as e:
            return self.reply(404, {"error": "not found: %s" % e})
        except (ValueError, TypeError) as e:
            return self.reply(400, {"error": str(e)})
        except Exception as e:                      # (the page says so; the traceback for whoever runs the server)
            import traceback
            traceback.print_exc()
            return self.reply(500, {"error": "%s: %s" % (type(e).__name__, e)})
        status, body = got if isinstance(got, tuple) else (200, got)
        return self.reply(status, body)

    def refused(self, change=True):
        """Why a request may not be answered (a reply sent), or False: a change on a read-only server,
        a request without the page's token."""
        if change and self.server.read_only:
            self.reply(403, {"error": "this page is read-only (dread gui --read-only): it changes nothing and runs no "
                                      "jobs"})
            return True
        if not secrets.compare_digest(self.headers.get("X-Dread-Token", ""), self.token):
            self.reply(403, {"error": "a change needs the page's token"})
            return True
        return False

    def do_POST(self):
        if not self.allowed():
            return
        url = urllib.parse.urlsplit(self.path)
        found = guipages.ROUTES.get(("POST", url.path))
        if self.refused(found is None or found.change):
            return
        if found is not None and found.raw:         # (a file's bytes: an upload, up to the server's limit)
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self.reply(411, {"error": "a file's upload says its length"})
            if length > self.server.upload_limit or length < 0:
                self.close_connection = True
                return self.reply(413, {"error": "the file has %d bytes, more than the %d this server takes "
                                                 "(dread gui --upload-limit)" % (length, self.server.upload_limit)})
            body = b""
            while len(body) < length:
                piece = self.rfile.read(min(1 << 20, length - len(body)))
                if not piece:
                    return self.reply(400, {"error": "the upload ended after %d of %d bytes" % (len(body), length)})
                body += piece
            return self.answer(found, dict(urllib.parse.parse_qsl(url.query)), body=body)
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.reply(415, {"error": "expected JSON"})
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 1 << 20)
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.reply(400, {"error": "not JSON"})
        if not isinstance(data, dict):
            return self.reply(400, {"error": "expected a JSON object"})
        if found is not None:
            return self.answer(found, dict(urllib.parse.parse_qsl(url.query)), data)
        try:
            state = self.state(dict(urllib.parse.parse_qsl(url.query)))
            if url.path == "/api/rename":
                return self.reply(200, {"changed": state.rename(str(data.get("old", "")), str(data.get("new", "")))})
            if url.path == "/api/annotate":
                state.annotate(int(data.get("address", -1)), str(data.get("text", "")))
                return self.reply(200, {"ok": True})
        except (tgt.TargetError, ValueError, TypeError) as e:
            return self.reply(400, {"error": str(e)})
        return self.reply(404, {"error": "not found"})


class Server(ThreadingHTTPServer):
    """The page's server; closing it also stops its jobs (dread.jobs Jobs.close: the running ones get
    SIGTERM, the queued ones are cancelled) and the threads of the images' states (State.close). One
    target at a time, or none (the Setup page alone, until one is made or opened: open_target)."""
    read_only = False
    jobs = None
    state = None
    local = True
    home = None                 # the directory the server started in: the jobs' without a target
    upload_limit = UPLOAD_LIMIT
    jobs_limit = 1

    def server_close(self):
        super().server_close()
        if self.jobs is not None:
            self.jobs.close()
        for state in list(self.RequestHandlerClass.states.values()):
            state.close()

    def open_target(self, path):
        """Show another target (a dread.toml or its directory), or the first one: refused (TargetError)
        while a job of this server is queued or running (they would lose their server), on a read-only
        server and on one that listens beyond this machine. The images' states of the one before are
        closed, its jobs' directory given up; the new target's jobs are this server's from then on (or
        another server's, which then runs them). Returns the Target."""
        if self.read_only:
            raise tgt.TargetError("this page is read-only (dread gui --read-only): it opens no other target")
        if not self.local:
            raise tgt.TargetError("a server that listens beyond this machine opens no other target")
        handler = self.RequestHandlerClass
        with handler.lock:
            target = tgt.Target.load(path)
            if handler.target is not None and target.file == handler.target.file:
                return target
            image = target.image()
            busy = self.jobs.status() if self.jobs is not None and not self.jobs.owner else None
            if busy and (busy["running"] or busy["queued"]):
                raise tgt.TargetError("%d job%s of this page still run or wait: stop them first, or start another "
                                      "dread gui for %s (dread gui --target %s --port 0)" % (
                                          busy["running"] + busy["queued"], "" if busy["running"] + busy["queued"] == 1
                                          else "s", target.name, target.root))
            state = State(target, image)
            old_states, old_jobs = list(handler.states.values()), self.jobs
            handler.target, handler.default, handler.states = target, image.name, {image.name: state}
            self.state = state
            if old_jobs is not None:
                old_jobs.close()
            self.jobs = self.make_jobs(jobs_dir(target))
        for old in old_states:
            old.close()
        return target

    def make_jobs(self, directory):
        """The jobs of a directory as this server runs them (dread.jobs Jobs): none where it is read-only
        or listens beyond this machine."""
        try:
            jobs = jobs_.Jobs(directory, limit=self.jobs_limit, read_only=self.read_only or not self.local)
        except OSError as e:                # (the page still shows the image; it runs no jobs)
            jobs = jobs_.Jobs(directory, read_only=True)
            jobs.owner = "no jobs: %s" % e
        if not self.local and not self.read_only:       # (whoever can load the page has its token)
            jobs.owner = ("jobs run only where the server listens on this machine alone (127.0.0.1): anyone who "
                          "can reach %s could run commands; for another machine use ssh -L" % self.server_address[0])
        from dread import llmconfig
        for value, name in llmconfig.file_keys():       # (the keys of key files: hidden in the logs as well)
            jobs.add_secret(value, name)
        return jobs


def jobs_dir(target):
    """Where the page's jobs keep their logs and records: BUILD/gui/jobs."""
    return os.path.join(target.build, "gui", "jobs")


def home_jobs_dir():
    """Where a server without a target keeps its jobs (the Setup page's: dread new, a provider's test):
    $XDG_STATE_HOME/dread/gui/jobs, else ~/.local/state/dread/gui/jobs."""
    state = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(state, "dread", "gui", "jobs")


def serve(target, image, host="127.0.0.1", port=8350, allow_hosts=(), read_only=False, jobs=1,
          upload_limit=UPLOAD_LIMIT, home=None):
    """The server, not yet serving (serve_forever() or a thread does that); its address is
    server.server_address. Requests are answered for the host names 127.0.0.1, localhost, [::1],
    the address listened on and allow_hosts (any port: a tunnel may use another one). read_only: no
    change and no job (the jobs' records are still shown); jobs: how many run at a time. target None:
    no target yet (the Setup page alone, its jobs in home_jobs_dir(), run in home: default the working
    directory); upload_limit: the bytes a file uploaded to the Setup page may have."""
    import socket
    guipages.load()
    state = State(target, image) if target is not None else None
    token = secrets.token_urlsafe(24)
    names = {host, "127.0.0.1", "localhost", "[::1]"} | set(allow_hosts)
    if host in ("0.0.0.0", "::", ""):
        names |= {socket.gethostname(), socket.getfqdn()}
    handler = type("BoundHandler", (Handler,), {"target": target, "default": image.name if image else None,
                                                "states": {image.name: state} if state else {},
                                                "token": token, "lock": threading.RLock(),
                                                "hosts": frozenset(n.lower() for n in names)})
    try:
        server = Server((host, port), handler)
    except OSError:
        if state is not None:
            state.close()
        raise
    server.daemon_threads = True
    server.token, server.state, server.read_only = token, state, bool(read_only)
    server.local = host in ("localhost", "::1") or host.startswith("127.")
    server.jobs_limit, server.upload_limit = jobs, int(upload_limit)
    server.home = os.path.abspath(home or os.getcwd())
    server.jobs = server.make_jobs(jobs_dir(target) if target is not None else home_jobs_dir())
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread gui", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1: this machine)")
    parser.add_argument("--port", type=int, default=8350, help="port (default 8350; 0: any free one)")
    parser.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                        help="also answer requests addressed to this host name")
    parser.add_argument("--open", action="store_true", help="open the page in the web browser")
    parser.add_argument("--read-only", action="store_true",
                        help="change nothing and run no jobs: no rename, no note, no command (the jobs' records are "
                             "still shown)")
    parser.add_argument("--jobs", type=int, default=1, metavar="N",
                        help="commands the Run page runs at a time (default 1; the others wait in a queue)")
    parser.add_argument("--upload-limit", type=float, default=UPLOAD_LIMIT / (1 << 20), metavar="MIB",
                        help="the size of a file the Setup page takes from the browser, in MiB (default %(default)g)")
    args = parser.parse_args(argv)
    try:
        try:
            target, image = tgt.from_args(args)
        except tgt.TargetError:
            if args.target or os.environ.get("DREAD_TARGET"):
                raise
            target = image = None           # (no target here: the Setup page makes or opens one)
        server = serve(target, image, args.host, args.port, args.allow_host, read_only=args.read_only,
                       jobs=max(1, args.jobs), upload_limit=max(0.0, args.upload_limit) * (1 << 20))
    except (tgt.TargetError, OSError) as e:
        print("dread gui: %s" % e, file=sys.stderr)
        return 1
    url = "http://%s:%d/" % ("127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host, server.server_address[1])
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("dread gui: listening on %s: anyone who can reach it can read the image; changes still need "
              "the page's token" % args.host, file=sys.stderr)
    if server.jobs.owner and not args.read_only:
        print("dread gui: %s: this page starts no jobs" % server.jobs.owner, file=sys.stderr)
    if target is None:
        print("dread gui: no target here or above %s: the Setup page makes one (or opens one) on %s (Ctrl-C ends it)"
              % (os.getcwd(), url + "#/setup"), flush=True)
    else:
        print("dread gui: %s, image %s, on %s (Ctrl-C ends it%s)" % (
            target.name, image.name, url, " and stops its jobs" if not server.jobs.owner else ""), flush=True)
    if args.open:
        import webbrowser
        webbrowser.open(url + ("#/setup" if target is None else ""))
    import signal

    def stop(signum, frame):            # (a terminal closed, or kill: the jobs stop with the server)
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_DFL)
        running = server.jobs.status()["running"] if not server.jobs.owner else 0
        if running:
            print("dread gui: stopping %d job%s" % (running, "" if running == 1 else "s"), file=sys.stderr, flush=True)
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
