"""dread gui: browse an image in the web browser.

A small web server on this machine and one page without a framework or a build step. It shows what
the tools know and decided, and runs nothing heavy itself:
- the overview: the functions by origin (written by hand, by dread pilot, lifted, refused or dropped
  by the lift check, original) in counts and bytes, a map of the image, the refusal reasons grouped,
  the failures of the last test run, when the tools last ran;
- the function list, filtered and sorted;
- one function: the flat listing (dread flat) with calls and branches linked, the instructions the
  cases ran or missed (from the last dread test or dread lift --check), the C (the source of a
  rewritten function; the lifted C, built or lifted on the spot), its verdict, cases, placement and
  annotations;
- search by name, by address and by the text a function refers to;
- two kinds of change, made as the command line makes them: rename (dread rename, checked and
  undone when the tree contradicts it) and annotate (annotations.txt).
It reloads by itself when the tables or the tools' records change. The analyses that take a while on
a large image (callers, regflow, never-returning functions, the text index) run in the background;
the page fills them in when they are ready.

Safety: the server listens on 127.0.0.1 unless told otherwise, answers only requests addressed to
that host name (no DNS rebinding), and a change needs the token of the page it served (no other web
page can make one). Over SSH: ssh -L 8350:127.0.0.1:8350 HOST, then open http://127.0.0.1:8350/.

    dread gui [--target DIR] [--image NAME] [--host 127.0.0.1] [--port 8350] [--allow-host NAME] [--open]
"""
import argparse
import glob
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dread import target as tgt

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}
ORIGINS = ("hand", "pilot", "lifted", "refused", "dropped", "original")
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


class State:
    """What the page shows of one image, reloaded when the files it comes from change."""

    def __init__(self, target, image):
        self.target, self.image = target, image
        self.lock = threading.RLock()
        self.stamp = None
        self.analyses = {}
        self.generation = 0
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
        files = [img.table(t) for t in ("functions", "symbols", "annotations", "lifted", "code_ranges")]
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
        from dread.build import read_names
        from dread.program import Program
        from dread.rename import c_files
        img, p = self.image, self.paths()
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
        threading.Thread(target=self.analyse, args=(self.program, self.generation), daemon=True).start()

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
        return {"generation": self.generation, "pending": self.pending()}

    def runs(self):
        p = self.paths()
        return {k: mtime(p[f]) or None for k, f in (("recon", "payload"), ("test", "tests"), ("lift_check", "verdicts"))}

    def functions(self):
        prog = self.program
        out = []
        for fn in prog.functions:
            name = fn.name
            test = self.tests.get(name)
            verdict = self.verdicts.get(name, {})
            missed = bool(test.get("missed")) if test else \
                verdict.get("verdict") == "dropped" and bool(verdict.get("missed"))
            out.append({"name": name, "entry": fn.entry, "size": prog.end(fn) - fn.entry, "origin": self.origin[name],
                        "system": name in self.system, "failed": bool(test and int(test.get("failed") or 0)),
                        "missed": missed})
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
                "uncovered": uncovered, "runs": self.runs(), "status": self.status()}

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
        out["verdict"] = self.verdicts.get(name)
        out["test"] = self.tests.get(name)
        out["placement"] = self.placement.get(name)
        out["coverage"] = self.coverage(fn)
        out["sources"] = self.c_sources(fn)
        out["cases"] = self.cases_of(name)
        return out

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
        """The C of the function: its source when src defines it, the lifted C (built, or lifted now)."""
        out = []
        root = self.target.root
        if fn.name in self.sources:
            path, first, last = self.sources[fn.name]
            with open(path, errors="replace") as f:
                lines = f.read().splitlines()
            out.append({"kind": "pilot" if fn.name in self.pilot else "source", "path": os.path.relpath(path, root),
                        "first": first, "text": "\n".join(lines[first - 1:last])})
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
                    self.lifter = lift.Lifter(self.program, self.ready("flow"), self.ready("endless"))
                text = self.lifter.lift(fn).text
                out.append({"kind": "lifted", "path": None, "first": 1, "text": text,
                            "how": "lifted now, not built or tested"})
            except lift.Refused as e:
                out.append({"kind": "lifted", "path": None, "refused": str(e)})
            except Exception as e:
                out.append({"kind": "lifted", "path": None, "refused": "the lifter failed: %s" % e})
        return out

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

    def search(self, query, limit=40):
        prog = self.program
        query = query.strip()
        if not query:
            return []
        out, seen = [], set()

        def add(kind, name, **more):
            if (kind, name, more.get("address")) not in seen and len(out) < limit:
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
        names = [fn for fn in prog.functions if low in fn.name.lower()]
        names.sort(key=lambda fn: (fn.name.lower() != low, not fn.name.lower().startswith(low), len(fn.name),
                                   fn.name))
        for fn in names:
            add("name", fn.name, entry=fn.entry)
        texts = self.ready("texts") or []
        for text, name, address in texts:
            if low in text.lower():
                add("text", name, address=address, text=text[:120])
        return out

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
        name = query.get("image") or self.default
        with self.lock:
            if name not in self.states:
                self.states[name] = State(self.target, self.target.image(name))
            return self.states[name]

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
        if not url.path.startswith("/api/"):
            return self.reply(404, {"error": "not found"})
        try:
            state = self.state(query).refresh()
        except tgt.TargetError as e:
            return self.reply(404, {"error": str(e)})
        try:
            with state.lock:
                if url.path == "/api/overview":
                    return self.reply(200, state.overview())
                if url.path == "/api/functions":
                    return self.reply(200, state.functions())
                if url.path == "/api/function":
                    return self.reply(200, state.function(query.get("name", "")))
                if url.path == "/api/search":
                    return self.reply(200, state.search(query.get("q", "")))
                if url.path == "/api/status":
                    return self.reply(200, state.status())
        except KeyError as e:
            return self.reply(404, {"error": "no function %s" % e})
        return self.reply(404, {"error": "not found"})

    def do_POST(self):
        if not self.allowed():
            return
        if not secrets.compare_digest(self.headers.get("X-Dread-Token", ""), self.token):
            return self.reply(403, {"error": "a change needs the page's token"})
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.reply(415, {"error": "expected JSON"})
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 1 << 20)
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.reply(400, {"error": "not JSON"})
        url = urllib.parse.urlsplit(self.path)
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


def serve(target, image, host="127.0.0.1", port=8350, allow_hosts=()):
    """The server, not yet serving (serve_forever() or a thread does that); its address is
    server.server_address. Requests are answered for the host names 127.0.0.1, localhost, [::1],
    the address listened on and allow_hosts (any port: a tunnel may use another one)."""
    import socket
    state = State(target, image)
    token = secrets.token_urlsafe(24)
    names = {host, "127.0.0.1", "localhost", "[::1]"} | set(allow_hosts)
    if host in ("0.0.0.0", "::", ""):
        names |= {socket.gethostname(), socket.getfqdn()}
    handler = type("BoundHandler", (Handler,), {"target": target, "default": image.name, "states": {image.name: state},
                                                "token": token, "lock": threading.Lock(),
                                                "hosts": frozenset(n.lower() for n in names)})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    server.token, server.state = token, state
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread gui", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1: this machine)")
    parser.add_argument("--port", type=int, default=8350, help="port (default 8350; 0: any free one)")
    parser.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                        help="also answer requests addressed to this host name")
    parser.add_argument("--open", action="store_true", help="open the page in the web browser")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        server = serve(target, image, args.host, args.port, args.allow_host)
    except (tgt.TargetError, OSError) as e:
        print("dread gui: %s" % e, file=sys.stderr)
        return 1
    url = "http://%s:%d/" % ("127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host, server.server_address[1])
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("dread gui: listening on %s: anyone who can reach it can read the image; changes still need "
              "the page's token" % args.host, file=sys.stderr)
    print("dread gui: %s, image %s, on %s (Ctrl-C ends it)" % (target.name, image.name, url), flush=True)
    if args.open:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
