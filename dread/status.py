"""dread status: the status section of README.md, made from what the repository records.

The part of README.md between its two markers (BEGIN and END) is written by this command, and
tests/test_status.py fails when it differs from what the command would write, so the README never
says more or less than the repository does:

- a line of totals: the commands (dread.cli), the test functions (def test_ in tests/), the FreeRTOS
  builds of the bench recipe (bench/freertos.toml) and the architectures they are on;
- the architectures (ARCHES): each with its gizmo builds (examples/gizmo/NAME: the functions of its
  table, config/functions.tsv, and how many of them are built from lifted C, config/lifted.txt), its
  FreeRTOS builds (the recipe's projects on it), and how far its part of docs/todo.md is (progress);
- the features: docs/todo.md's sections (questions and known gaps left out), each with the share of
  its checkboxes that are ticked, linked to the section.

Progress of a section or of an item of docs/todo.md: "paused" where its own text says so (the text
before its first checkbox: the user pauses work in words); "done" for an item that is ticked and for
a section whose checkboxes all are; "planned" where it has no checkbox under it; else the share of
the checkboxes under it that are ticked, as a bar. The features' table lists the sections with work
left; a line under it names those that are complete.

    dread status [--root DIR]            print the section
    dread status --write [--root DIR]    put it into README.md between the markers
    dread status --check [--root DIR]    exit 1 when README.md's section is not what --write writes

Run --write in a commit that changes docs/todo.md's checkboxes, a gizmo build's table, the bench
recipe, the commands or the tests' count (the suite says so otherwise).
"""

import argparse
import os
import re
import sys
import tomllib

BEGIN = "<!-- status: written by bin/dread status from docs/todo.md, the gizmo builds' tables and the bench recipe; do not edit by hand -->"
END = "<!-- /status -->"
SKIP = ("Questions for the user", "Known gaps")      # docs/todo.md sections that are no feature
BAR = 10                                             # the cells of a progress bar

# The architectures, in the order the README lists them: (name, gizmo builds, the bench recipe's
# projects on it, where docs/todo.md follows it: ("section", start of its heading) or ("item", a
# regular expression its first line matches), or None for the one the tools began on, and what the
# row says about it then).
ARCHES = [
    ("ARM: ARM state, Thumb, Thumb-2 (Cortex-M3)", ("a32", "m3", "m3cpp"), ("mps2",), None,
     "the architecture the tools began on"),
    ("Cortex-M4F (FPv4-SP, DSP)", ("m4",), ("mps2f",), ("section", "Cortex-M4"), None),
    ("RISC-V (RV32IMC)", ("rv32", "rv32cpp"), ("virt",), ("item", r"RISC-V \(RV32IMC\)"), None),
    ("MSP430", ("msp430",), ("msp430",), ("section", "MSP430"), None),
    ("x86, 32-bit (i386)", ("i386", "i386clang"), ("ia32",), ("item", r"\*\*x86 and x86-64"), None),
    ("MIPS32", ("mips",), (), ("item", r"MIPS32"), None),
    ("AArch64", (), (), ("item", r"AArch64"), None),
    ("PowerPC 32", (), (), ("item", r"PowerPC 32"), None),
    ("ARM big-endian (BE-8)", (), (), ("item", r"Big-endian ARM"), None),
    ("AVR", (), (), ("item", r"AVR "), None),
    ("Z80", (), (), ("item", r"Z80"), None),
    ("8051", (), (), ("item", r"8051"), None),
]

CHECKBOX = re.compile(r"^(\s*)- \[([ xX])\] (.*)$")
HEADING = re.compile(r"^(#{1,6}) (.*)$")


class Node:
    """A section (## heading) or an item (a checkbox line) of docs/todo.md: its first line, whether
    it is ticked (items), its own text (the lines before its first checkbox), and the checkboxes
    under it (done, total)."""

    def __init__(self, title, ticked, text, done, total, slug=None):
        self.title, self.ticked, self.text = title, ticked, text
        self.done, self.total, self.slug = done, total, slug
        self.title_is_section = slug is not None    # (a section is done when every checkbox in it is)

    def progress(self):
        """("done" | "paused" | "planned" | "steps", done, total) as the module's docstring says."""
        if re.search(r"\bpaused\b", self.text, re.IGNORECASE) and not self.ticked:
            return ("paused", self.done, self.total)
        if self.ticked or (self.title_is_section and self.total and self.done == self.total):
            return ("done", self.done, self.total)
        if not self.total:
            return ("planned", 0, 0)
        return ("steps", self.done, self.total)


def slugger():
    """GitHub's anchors of a document's headings, in order (github-slugger): lower case, everything but
    letters, digits, underscores, hyphens and spaces left out, spaces as hyphens, a repeat numbered."""
    seen = {}

    def slug(heading):
        base = re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")
        n = seen.get(base, 0)
        seen[base] = n + 1
        return base if not n else "%s-%d" % (base, n)
    return slug


def parse_todo(text):
    """(sections, items) of docs/todo.md: Nodes; an item counts the checkboxes indented under it."""
    lines = text.splitlines()
    slug = slugger()
    sections, items = [], []
    marks = []                                       # (line index, indent, ticked, first line)
    heads = []                                       # (line index, level, title, slug)
    for i, line in enumerate(lines):
        h = HEADING.match(line)
        if h:
            heads.append((i, len(h.group(1)), h.group(2).strip(), slug(h.group(2))))
            continue
        c = CHECKBOX.match(line)
        if c:
            marks.append((i, len(c.group(1).expandtabs()), c.group(2) in "xX", c.group(3)))
    for k, (i, level, title, s) in enumerate(heads):
        if level != 2:
            continue
        end = next((h[0] for h in heads[k + 1:] if h[1] <= 2), len(lines))
        inside = [m for m in marks if i < m[0] < end]
        first = inside[0][0] if inside else end
        sections.append(Node(title, False, "\n".join(lines[i + 1:first]),
                             sum(1 for m in inside if m[2]), len(inside), s))
    for n, (i, indent, ticked, first_line) in enumerate(marks):
        end = len(lines)
        for j in range(i + 1, len(lines)):
            line = lines[j]
            if not line.strip():
                continue
            if HEADING.match(line) or len(line) - len(line.lstrip()) <= indent:
                end = j
                break
        under = [m for m in marks[n + 1:] if m[0] < end]
        own_end = under[0][0] if under else end
        own = "\n".join([first_line] + [lines[j] for j in range(i + 1, own_end)])
        items.append(Node(first_line, ticked, own, sum(1 for m in under if m[2]), len(under)))
    return sections, items


def find(sections, items, where):
    """The Node docs/todo.md follows an architecture in (ARCHES' where), or None."""
    if where is None:
        return None
    kind, key = where
    if kind == "section":
        return next((s for s in sections if s.title.startswith(key)), None)
    rx = re.compile(key)
    return next((it for it in items if rx.match(it.title)), None)


def bar(done, total):
    """A progress bar of BAR cells, in backquotes (monospaced in a table)."""
    full = round(BAR * done / total) if total else 0
    return "`%s%s`" % ("█" * full, "░" * (BAR - full))


def progress_text(node, fallback=None):
    """What a row says of how far an architecture or a feature is."""
    if node is None:
        return fallback or "–"
    kind, done, total = node.progress()
    if kind == "done":
        return "done"
    if kind == "planned":
        return "planned"
    steps = "%s %d of %d steps" % (bar(done, total), done, total) if total else ""
    return ("paused" + (" · " + steps if steps else "")) if kind == "paused" else steps


def short_title(title):
    """A section's heading without the parentheses at its end (who asked for it, and when)."""
    while True:
        cut = re.sub(r"\s*\((?:[^()]|\([^()]*\))*\)\s*$", "", title)
        if cut == title:
            return title.strip()
        title = cut


def count_lines(path, skip_comments=True, header=0):
    """Non-empty lines of a table (comments left out; header lines too), or None without the file."""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        rows = [line for line in f if line.strip() and not (skip_comments and line.startswith("#"))]
    return max(0, len(rows) - header)


def gizmo_figures(root, build):
    """(functions, lifted) of a gizmo build's tables (lifted None without config/lifted.txt)."""
    config = os.path.join(root, "examples", "gizmo", build, "config")
    return (count_lines(os.path.join(config, "functions.tsv"), header=1) or 0,
            count_lines(os.path.join(config, "lifted.txt")))


def bench_builds(root):
    """{project: its builds} of the FreeRTOS bench recipe, and {project: its architecture's name}."""
    path = os.path.join(root, "bench", "freertos.toml")
    if not os.path.exists(path):
        return {}, {}
    with open(path, "rb") as f:
        recipe = tomllib.load(f)
    per, arch = {}, {}
    for name, project in recipe.get("projects", {}).items():
        target = tomllib.loads(project["target"]) if "target" in project else {}
        arch[name] = target.get("arch", {}).get("name")
    for b in recipe.get("builds", []):
        per[b.get("project", "")] = per.get(b.get("project", ""), 0) + 1
    return per, arch


def count_tests(root):
    """The test functions in tests/ (def test_ at the start of a line; parametrisations count once)."""
    n = 0
    folder = os.path.join(root, "tests")
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
        if name.startswith("test_") and name.endswith(".py"):
            with open(os.path.join(folder, name)) as f:
                n += sum(1 for line in f if line.startswith("def test_"))
    return n


def render(root):
    """The status section (between and with the markers)."""
    from dread import cli
    with open(os.path.join(root, "docs", "todo.md")) as f:
        sections, items = parse_todo(f.read())
    per, arch = bench_builds(root)
    builds = sum(per.values())
    families = sorted({arch[p] for p in per if arch.get(p)})
    out = [BEGIN, ""]
    out.append("{:,} commands, {:,} test functions run by CI on every push, {:,} FreeRTOS builds in the benchmark "
               "on {:,} architectures (`dread bench`). What each part does: [docs/features.md](docs/features.md); "
               "what comes next: [docs/todo.md](docs/todo.md).".format(len(cli.COMMANDS), count_tests(root), builds,
                                                                       len(families)))
    out += ["", "| Architecture | Gizmo builds: lifted / functions | FreeRTOS builds | Progress |",
            "|---|---|---|---|"]
    for name, gizmos, projects, where, fallback in ARCHES:
        cells = []
        for g in gizmos:
            functions, lifted = gizmo_figures(root, g)
            cells.append("%s %s / %d" % (g, "–" if lifted is None else lifted, functions))
        bench = sum(per.get(p, 0) for p in projects)
        out.append("| %s | %s | %s | %s |" % (name, " · ".join(cells) or "–", bench or "–",
                                             progress_text(find(sections, items, where), fallback)))
    out += ["", "Gizmo (`examples/gizmo`) is DREAD's own test firmware, built for each architecture: of each build's "
            "functions, those its lift check builds from lifted C (– where the build has no lift table yet)."]
    out += ["", "| Feature (docs/todo.md) | Progress | Steps |", "|---|---|---|"]
    complete = []
    for s in sections:
        if s.title.startswith(SKIP) or not s.total:
            continue
        link = "[%s](docs/todo.md#%s)" % (short_title(s.title), s.slug)
        if s.done == s.total:
            complete.append(link)
            continue
        out.append("| %s | %s | %d of %d |" % (link, bar(s.done, s.total), s.done, s.total))
    if complete:
        out += ["", "Complete: %s." % ", ".join(complete)]
    out += ["", END]
    return "\n".join(out)


def readme_parts(text):
    """(before, section, after) of README.md's text around the markers; ValueError without them."""
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 or end < start:
        raise ValueError("README.md has no status markers (%s ... %s)" % (BEGIN[:30], END))
    return text[:start], text[start:end + len(END)], text[end + len(END):]


def default_root():
    """The checkout this package lies in (the directory with README.md above dread/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dread status", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=None, help="the checkout (default: the one this package lies in)")
    ap.add_argument("--write", action="store_true", help="put the section into README.md")
    ap.add_argument("--check", action="store_true", help="exit 1 when README.md's section is not current")
    args = ap.parse_args(argv)
    root = args.root or default_root()
    readme = os.path.join(root, "README.md")
    section = render(root)
    if not (args.write or args.check):
        print(section)
        return 0
    with open(readme) as f:
        text = f.read()
    before, current, after = readme_parts(text)
    if args.check:
        if current != section:
            print("dread status: README.md's status section is not current; run bin/dread status --write",
                  file=sys.stderr)
            return 1
        return 0
    if current != section:
        with open(readme, "w") as f:
            f.write(before + section + after)
        print("dread status: README.md's status section written")
    else:
        print("dread status: README.md's status section is current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
