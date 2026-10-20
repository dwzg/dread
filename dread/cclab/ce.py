"""Compiler Explorer as a compiler for dread cclab: a probe compiled by one of the compilers its
public instance hosts (godbolt.org: on 2026-10-20 52 ARM GCC versions, 21 ARM clang, 48 RISC-V GCC,
42 RISC-V clang, TI's Arm clang, 17 MSP430 GCC, SDCC), the assembly it gives back assembled here,
and the link done here. A compiler profile for dozens of versions without installing one.

An adapter is made from a Compiler Explorer compiler by its id: `dread cclab --adapter ce:ID`
(dread.cclab.adapters.load), on the built-in adapter of its architecture and kind (gcc-arm,
clang-arm, gcc-riscv, gcc-msp430), with this module as its compile command. `dread cclab
--ce-list [WORD]` lists the ids.

Only DREAD's own probes may be sent (rule 1, as for language models): such an adapter refuses a
guarded run. Answers are cached by instance, compiler, language, flags and source in
~/.cache/dread/compiler-explorer, and requests are spaced (REQUEST_GAP seconds, across processes),
as the public instance asks of heavy users. DREAD_CE_URL points at another instance: one of our
own (Compiler Explorer is open source, it runs in podman) for code that may not leave the machine.

As a compiler driver (the adapter's compile command):
    python -m dread.cclab.ce --compiler ID [--lang c|c++] --as ASSEMBLER -- -c SRC -o OBJ FLAGS...
The assembler is one argument with its words separated by spaces; it gets the machine options of
FLAGS (-m..., --target=...) and the assembly in a file beside OBJ.
"""
import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

URL = "https://godbolt.org"
REQUEST_GAP = 0.5           # seconds between two requests to the instance, across processes
RETRIES = 5
LANGUAGES = {"c": "c", "c++": "c++"}
FILTERS = {"labels": False, "directives": False, "commentOnly": False, "demangle": False, "libraryCode": False,
           "trim": False, "binary": False, "binaryObject": False, "execute": False, "intel": False}


class CompilerExplorerError(Exception):
    pass


def url():
    return os.environ.get("DREAD_CE_URL", URL).rstrip("/")


def cache_dir():
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"),
                        "dread", "compiler-explorer")


def _request(path, body=None, timeout=120):
    """JSON from the instance (GET, or POST with body), with the spacing and retries on a busy answer."""
    os.makedirs(cache_dir(), exist_ok=True)
    stamp = os.path.join(cache_dir(), ".last-request")
    for attempt in range(RETRIES):
        with open(stamp, "a+") as f:            # one request at a time, REQUEST_GAP apart
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                last = float(f.read().strip() or 0)
            except ValueError:
                last = 0
            wait = last + REQUEST_GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            f.seek(0)
            f.truncate()
            f.write("%f" % time.time())
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url() + path, data=data, headers={
            "Accept": "application/json", "Content-Type": "application/json",
            "User-Agent": "dread-cclab (https://github.com/dwzg/dread)"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt + 1 < RETRIES:
                time.sleep(2 ** attempt * 2)
                continue
            raise CompilerExplorerError("%s%s: HTTP %d" % (url(), path, e.code))
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt + 1 < RETRIES:
                time.sleep(2 ** attempt * 2)
                continue
            raise CompilerExplorerError("%s%s: %s" % (url(), path, e))
    raise CompilerExplorerError("%s%s: no answer" % (url(), path))


def compilers(lang="c"):
    """[{id, name, semver, instructionSet, compilerType}] of the instance's compilers for a
    language (cached for a day)."""
    path = os.path.join(cache_dir(), "compilers-%s-%s.json" % (lang, hashlib.sha1(url().encode()).hexdigest()[:8]))
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 86400:
        with open(path) as f:
            return json.load(f)
    got = _request("/api/compilers/%s?fields=id,name,semver,instructionSet,compilerType" % LANGUAGES[lang])
    os.makedirs(cache_dir(), exist_ok=True)
    with open(path, "w") as f:
        json.dump(got, f)
    return got


def compiler(ident, lang="c"):
    for c in compilers(lang):
        if c["id"] == ident:
            return c
    raise CompilerExplorerError("no compiler %s on %s (dread cclab --ce-list)" % (ident, url()))


def assembly(ident, source, flags, lang="c"):
    """The assembly the compiler ident writes for source with flags (cached)."""
    key = hashlib.sha1(json.dumps([url(), ident, lang, list(flags), source]).encode()).hexdigest()
    path = os.path.join(cache_dir(), "asm", key[:2], key + ".s")
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    body = {"source": source, "lang": LANGUAGES[lang], "options": {
        "userArguments": " ".join(flags), "filters": FILTERS, "compilerOptions": {"skipAsm": False}}}
    got = _request("/api/compiler/%s/compile" % ident, body)
    if got.get("code"):
        raise CompilerExplorerError("%s: exit %s\n%s" % (ident, got.get("code"),
                                    "\n".join(e.get("text", "") for e in got.get("stderr", []))[-3000:]))
    text = "\n".join(a.get("text", "") for a in got.get("asm", [])) + "\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.replace(path + ".tmp", path)
    return text


def machine_flags(flags):
    """The flags an assembler takes from a compile line: -m options and clang's --target."""
    return [f for f in flags if f.startswith(("-m", "--target=", "-march=", "-mabi="))]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        k = argv.index("--")
        own, rest = argv[:k], argv[k + 1:]
    else:
        own, rest = argv, []
    parser = argparse.ArgumentParser(prog="python -m dread.cclab.ce", description=__doc__.split("\n\n")[0])
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--lang", default="c", choices=sorted(LANGUAGES))
    parser.add_argument("--as", dest="assembler", default="")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(own)
    try:
        if args.version:
            c = compiler(args.compiler, args.lang)
            print("%s (Compiler Explorer %s, %s)" % (c["name"], c["id"], url()))
            return 0
        src = obj = None
        flags, i = [], 0
        while i < len(rest):
            if rest[i] == "-c":
                src = rest[i + 1]
                i += 2
            elif rest[i] == "-o":
                obj = rest[i + 1]
                i += 2
            else:
                flags.append(rest[i])
                i += 1
        if not src or not obj or not args.assembler:
            raise CompilerExplorerError("usage: --compiler ID --as ASSEMBLER -- -c SRC -o OBJ FLAGS...")
        with open(src) as f:
            source = f.read()
        text = assembly(args.compiler, source, flags, args.lang)
        asm = obj + ".s"
        with open(asm, "w") as f:
            f.write(text)
        r = subprocess.run(args.assembler.split() + machine_flags(flags) + ["-o", obj, asm],
                           capture_output=True, text=True)
        if r.returncode:
            raise CompilerExplorerError("assembling %s: %s" % (asm, (r.stderr or r.stdout)[-3000:]))
    except (CompilerExplorerError, OSError) as e:
        print("dread.cclab.ce: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
