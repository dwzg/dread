"""dread leakcheck: look for a private project's material in a tree and its git history.

For publishing tools that grew inside a private project: before a push to a public remote, check
that none of the private project's names, addresses or words got into the code, the documentation,
the tests or the commit messages. The facts to look for come from the private project at run time,
never from the repository being checked:

  --tables FILE...  the private project's function tables or symbol files (functions.tsv,
                    symbols.txt, DREAD's formats): every name of at least --min characters that is
                    not generic, and every address of at least five hex digits
  --words FILE      one word or regular expression per line, matched case-insensitively (in the tree
                    over a whole file: an expression may span lines; in the history line by line); a
                    match is reported with its whitespace collapsed to single spaces
  --allow FILE      hits that are coincidences, one per line: a hit ("0x10000", "timer_start") or
                    "path:hit" (also in the history, for lines of that file's diffs), its whitespace
                    collapsed as a hit's is (a match across a line break is allowed by one line)

Scans every text file of the tree (not .git, .venv, build directories) and, with --history, the
message and diff of every commit on every branch, and the commit authors. Prints each hit with where
it is found; exit status 1 when a hit is not on the allow list. Keep the words and allow files with
the private project or in your own configuration, not in the repository that is checked.

--json FILE also writes the hits as data (docs/formats.md): each hit in the tree with its file, the
lines it is on and whether it is allowed; each one in the history with its commit and file; the commit
authors; how many names, addresses and words were looked for. Of the private files it holds their
paths as given and the strings the hits matched, nothing else (dread gui's leak check page shows it,
and must never show more of them). The file is written with mode 600: the hits are the private
project's strings.

    dread leakcheck [--tree DIR] --tables FILE... [--words FILE] [--allow FILE] [--history] [--min N]
                    [--json FILE]
"""
import argparse
import os
import re
import subprocess
import sys

GENERIC = set("""main start entry reset hang delay memcpy memset memcmp memmove strlen strcmp strncmp strcpy
strncpy strcat strchr strstr sprintf printf malloc free calloc realloc abort exit atexit atoi itoa""".split())
SKIP_DIRS = {".git", ".venv", "build", "__pycache__", ".pytest_cache", "node_modules"}
HEX = re.compile(r"0x([0-9a-fA-F]{5,8})\b")
JSON_VERSION = 1            # of the --json record
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def deny_lists(tables, min_length=6):
    """(names, addresses) from the private project's tables."""
    names, addresses = set(), set()
    for path in tables:
        with open(path, errors="replace") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                fields = line.replace("\t", " ").split()
                if not fields:
                    continue
                if fields[0] in ("f", "d", "-") and len(fields) >= 2:      # symbols.txt
                    address, name = fields[1], fields[2] if len(fields) > 2 else None
                else:                                                      # functions.tsv
                    address, name = fields[0], fields[1] if len(fields) > 1 else None
                try:
                    value = int(address, 16)
                except ValueError:
                    continue                                               # a header
                if value >= 0x10000:
                    addresses.add(value)
                if (name and name != "-" and len(name) >= min_length and name not in GENERIC
                        and not name.startswith(("FUN_", "thunk_", "DAT_", "LAB_"))):
                    names.add(name)
    return names, addresses


def collapsed(text):
    """text's runs of whitespace as single spaces, lower case: how a word's match is reported and an
    allow entry compared (a match across a line break, with the next line's indentation, is one line)."""
    return " ".join(text.lower().split())


def scan(text, names, addresses, words):
    hits = set()
    for word in words:
        for m in word.finditer(text):
            hits.add(collapsed(m.group(0)))
    hits |= set(IDENT.findall(text)) & names
    for m in HEX.finditer(text):
        if int(m.group(1), 16) in addresses:
            hits.add(m.group(0).lower())
    return hits


def found(text, names, addresses, words):
    """{hit: [the lines it is on, from 1]}: scan's hits (words in lower case, names as written, addresses'
    hex text in lower case) with where they are, for --json (scan alone is quicker)."""
    import bisect
    hits = {}
    for word in words:
        for m in word.finditer(text):
            hits.setdefault(collapsed(m.group(0)), []).append(m.start())
    for m in IDENT.finditer(text):
        if m.group(0) in names:
            hits.setdefault(m.group(0), []).append(m.start())
    for m in HEX.finditer(text):
        if int(m.group(1), 16) in addresses:
            hits.setdefault(m.group(0).lower(), []).append(m.start())
    if not hits:
        return hits
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    return {hit: sorted({bisect.bisect_right(starts, o) for o in offsets}) for hit, offsets in hits.items()}


def write_json(path, data):
    """The record written whole with mode 600 (it holds the private strings the hits matched) and renamed
    into place."""
    import json
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def text_files(tree):
    for root, dirs, files in os.walk(tree):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            path = os.path.join(root, name)
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            yield path, data.decode("utf-8", errors="replace")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread leakcheck", description=__doc__.split("\n\n")[0])
    parser.add_argument("--tree", default=".", help="the tree to check (default: the working directory)")
    parser.add_argument("--tables", nargs="+", required=True, help="the private project's tables")
    parser.add_argument("--words", help="file of words or expressions to look for")
    parser.add_argument("--allow", help="file of hits that are coincidences")
    parser.add_argument("--history", action="store_true", help="also every commit of the tree's git repository")
    parser.add_argument("--min", type=int, default=6, help="shortest name looked for (default 6)")
    parser.add_argument("--json", metavar="FILE",
                        help="also write the hits as JSON to FILE, mode 600: each with its file and lines; of the "
                             "private files only their paths and the strings the hits matched (docs/formats.md)")
    args = parser.parse_args(argv)
    names, addresses = deny_lists(args.tables, args.min)
    words = []
    if args.words:
        with open(args.words) as f:
            words = [re.compile(line.strip(), re.I) for line in f if line.strip() and not line.startswith("#")]
    allowed = set()
    if args.allow:
        with open(args.allow) as f:
            allowed = {collapsed(line) for line in f if line.strip() and not line.startswith("#")}
    tree = os.path.abspath(args.tree)
    bad = 0
    record = {"version": JSON_VERSION, "tree": tree, "tables": list(args.tables), "words": args.words,
              "allow": args.allow, "history": bool(args.history), "min": args.min,
              "looked_for": {"names": len(names), "addresses": len(addresses), "words": len(words)},
              "hits": [], "history_hits": [], "authors": None}
    for path, text in text_files(tree):
        rel = os.path.relpath(path, tree)
        hits = found(text, names, addresses, words) if args.json else scan(text, names, addresses, words)
        for hit in sorted(hits):
            ok = hit.lower() in allowed or ("%s:%s" % (rel, hit)).lower() in allowed
            print("%s %s: %s" % ("allowed" if ok else "HIT", rel, hit))
            bad += not ok
            if args.json:
                record["hits"].append({"file": rel, "hit": hit, "lines": hits[hit], "allowed": ok})
    if args.history:
        log = subprocess.run(["git", "-C", tree, "log", "-p", "--all", "--format=commit %H%nauthor %an <%ae>%n%B"],
                             capture_output=True, text=True, errors="replace")
        if log.returncode:
            print("dread leakcheck: %s is not a git repository" % tree, file=sys.stderr)
            return 1
        commit, path, seen = None, None, set()
        for line in log.stdout.splitlines():
            if line.startswith("commit "):
                commit, path = line.split()[1][:10], None
                continue
            if line.startswith("diff --git "):
                path = line.split(" b/", 1)[1] if " b/" in line else None
                continue
            for hit in sorted(scan(line, names, addresses, words)):
                ok = hit.lower() in allowed or (path and ("%s:%s" % (path, hit)).lower() in allowed)
                if not ok and (commit, hit) not in seen:
                    seen.add((commit, hit))
                    print("HIT history %s%s: %s" % (commit, " " + path if path else "", hit))
                    bad += 1
                    record["history_hits"].append({"commit": commit, "file": path, "hit": hit})
        authors = sorted(set(re.findall(r"^author (.*)$", log.stdout, re.M)))
        print("commit authors: %s" % "; ".join(authors))
        record["authors"] = authors
    print("dread leakcheck: %d names and %d addresses looked for; %d hits not allowed"
          % (len(names), len(addresses), bad))
    if args.json:
        record.update(not_allowed=bad, allowed=sum(1 for h in record["hits"] if h["allowed"]), exit=1 if bad else 0)
        write_json(args.json, record)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
