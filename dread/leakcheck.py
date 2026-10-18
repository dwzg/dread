"""dread leakcheck: look for a private project's material in a tree and its git history.

For publishing tools that grew inside a private project: before a push to a public remote, check
that none of the private project's names, addresses or words got into the code, the documentation,
the tests or the commit messages. The facts to look for come from the private project at run time,
never from the repository being checked:

  --tables FILE...  the private project's function tables or symbol files (functions.tsv,
                    symbols.txt, DREAD's formats): every name of at least --min characters that is
                    not generic, and every address of at least five hex digits
  --words FILE      one word or regular expression per line, matched case-insensitively
  --allow FILE      hits that are coincidences, one per line: a hit ("0x10000", "timer_start") or
                    "path:hit"

Scans every text file of the tree (not .git, .venv, build directories) and, with --history, the
message and diff of every commit on every branch, and the commit authors. Prints each hit with where
it is found; exit status 1 when a hit is not on the allow list. Keep the words and allow files with
the private project or in your own configuration, not in the repository that is checked.

    dread leakcheck [--tree DIR] --tables FILE... [--words FILE] [--allow FILE] [--history] [--min N]
"""
import argparse
import os
import re
import subprocess
import sys

GENERIC = set("""main start entry reset hang delay memcpy memset memcmp memmove strlen strcmp strncmp strcpy
strncpy strcat strchr strstr sprintf printf malloc free calloc realloc abort exit atoi itoa""".split())
SKIP_DIRS = {".git", ".venv", "build", "__pycache__", ".pytest_cache", "node_modules"}
HEX = re.compile(r"0x([0-9a-fA-F]{5,8})\b")
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


def scan(text, names, addresses, words):
    hits = set()
    for word in words:
        for m in word.finditer(text):
            hits.add(m.group(0).lower())
    hits |= set(IDENT.findall(text)) & names
    for m in HEX.finditer(text):
        if int(m.group(1), 16) in addresses:
            hits.add(m.group(0).lower())
    return hits


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
    args = parser.parse_args(argv)
    names, addresses = deny_lists(args.tables, args.min)
    words = []
    if args.words:
        with open(args.words) as f:
            words = [re.compile(line.strip(), re.I) for line in f if line.strip() and not line.startswith("#")]
    allowed = set()
    if args.allow:
        with open(args.allow) as f:
            allowed = {line.strip().lower() for line in f if line.strip() and not line.startswith("#")}
    tree = os.path.abspath(args.tree)
    bad = 0
    for path, text in text_files(tree):
        rel = os.path.relpath(path, tree)
        for hit in sorted(scan(text, names, addresses, words)):
            ok = hit.lower() in allowed or ("%s:%s" % (rel, hit)).lower() in allowed
            print("%s %s: %s" % ("allowed" if ok else "HIT", rel, hit))
            bad += not ok
    if args.history:
        log = subprocess.run(["git", "-C", tree, "log", "-p", "--all", "--format=commit %H%nauthor %an <%ae>%n%B"],
                             capture_output=True, text=True, errors="replace")
        if log.returncode:
            print("dread leakcheck: %s is not a git repository" % tree, file=sys.stderr)
            return 1
        commit, seen = None, set()
        for line in log.stdout.splitlines():
            if line.startswith("commit "):
                commit = line.split()[1][:10]
                continue
            for hit in sorted(scan(line, names, addresses, words)):
                if hit.lower() not in allowed and (commit, hit) not in seen:
                    seen.add((commit, hit))
                    print("HIT history %s: %s" % (commit, hit))
                    bad += 1
        authors = sorted(set(re.findall(r"^author (.*)$", log.stdout, re.M)))
        print("commit authors: %s" % "; ".join(authors))
    print("dread leakcheck: %d names and %d addresses looked for; %d hits not allowed"
          % (len(names), len(addresses), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
