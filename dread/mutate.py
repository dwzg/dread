"""dread mutate: break rewritten C on purpose and check that the cases notice.

For each named function, small changes are made to its body one at a time:

- a statement removed;
- a comparison or operator swapped (== and !=, <= and <, >= and >, && and ||, + and -, < and <=,
  > and >=, & and |);
- two neighbouring arguments of a call swapped;
- a numeric constant raised by one.

Each variant is compiled from a copy of the function's source file, the copy's object is linked
over the original image (everything else stays original machine code), and the function's cases
run against it in a worker process. A change no case notices is a survivor: the cases miss a branch
or a boundary, or the change cannot alter behaviour (code the original never reaches, a value that
is never read). Each survivor needs an argument or a case.

For a `static` helper (inlined into its callers) the cases of the functions of the same file that
call it are run. A variant that does not compile, or does not link, counts as "did not build". One
that runs longer than --seconds counts as caught and is listed as "ran away". The unchanged source
is built and judged first: when its cases fail, nothing is concluded. A variant that compiles to
the same code as the unchanged source (a line the preprocessor leaves out, a change the compiler
folds away) is not run: no test can tell it apart, so it is reported apart and is no survivor.

The changes are made to the text, so they follow the source as written: a constant hidden in a
macro is not changed (use static inline functions for shared code), and the survivors refer to
lines of the copy, which are the lines of the source file.

With --kill, a search runs for every survivor before anyone has to argue it: the function's
generated and written cases (or freshly generated ones) are varied one value at a time (an
argument, a memory value the original reads, a stub's answer) through the boundary values and the
function's constants and their neighbours, and the original and the mutant are run on each. The
first input on which they differ, that the original runs to the end without reading code as data
or writing into the image, and that does not also separate the original from the unchanged C (that
would be a real difference, printed for tracing), is appended to the image's extra_cases with
"by" and "why". The survivor then counts as caught. What survives the search may be equivalent;
only an argument settles that.

A survivor that cannot be caught needs an argument why it changes nothing. Arguments are kept in
the image's argued.tsv (beside its cases directory: tests/argued.tsv by default), one per line:
function, the source line as written (stripped), the change as the output names it, the argument.
A survivor that matches is listed with its argument and not counted; when the line changes, the
argument no longer matches and the mutant counts again.

Output per function: `NAME  N changes caught, M survived, K did not build`, then one line per
survivor `  FILE:LINE  text   [what was changed]`. Exit status 1 when a survivor is not argued.

    dread mutate [--target DIR] [--image NAME] [--lines FIRST-LAST] [--seconds N] [--jobs N] NAME...
"""
import argparse
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import sys
import tempfile

from dread import build, overlay, runner
from dread import target as tgt
from dread.elf import Elf
from dread.util.polite import die_with_parent, polite, workers

SWAPS = (("==", "!="), ("!=", "=="), ("<=", "<"), (">=", ">"), ("&&", "||"), ("||", "&&"),
         (" + ", " - "), (" - ", " + "), (" < ", " <= "), (" > ", " >= "), (" & ", " | "), (" | ", " & "))
TYPES = r"(?:struct \w+|union \w+|enum \w+|u8|u16|u32|u64|s8|s16|s32|s64|int|char|short|long|void|float|double|\w+_t)"
DECL = re.compile(r"\s*(?:const |volatile |register |static |unsigned |signed )*" + TYPES +
                  r"(?:\s+(?:const |volatile ))*\s*\**\s*\w+\s*(?:=|;|\[|__asm__|,)")


def strip_code(text):
    """The text with comments, strings and character literals blanked (same length, newlines
    kept), so that braces and names inside them do not count."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(c + " " * (j - i - 2) + (c if j - i >= 2 else ""))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def find_function(paths, name):
    """(path, lines, first body line, closing line, static) of the definition of `name` in the
    given C files: the line after its opening brace up to the line of its closing brace (0-based).
    Raises KeyError when none of the files defines it."""
    # a definition starts in the first column; the prefix may hold __attribute__((...))
    pattern = re.compile(r"(?m)^(?:[A-Za-z_][^;{}\n]*?\b)?%s\s*\(" % re.escape(name))
    for path in paths:
        with open(path) as f:
            text = f.read()
        code = strip_code(text)
        for m in pattern.finditer(code):
            depth, i = 0, m.end() - 1
            while i < len(code):                    # the parameter list
                if code[i] == "(":
                    depth += 1
                elif code[i] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            j = i + 1
            while j < len(code) and code[j] in " \t\n":
                j += 1
            rest = code[j:j + 64]
            if rest.startswith("__asm__") or rest.startswith("__attribute__"):
                k = code.find("{", j)
                semi = code.find(";", j)
                if semi != -1 and (k == -1 or semi < k):
                    continue
                j = k
            if j >= len(code) or code[j] != "{":
                continue                            # a declaration
            depth, k = 0, j
            while k < len(code):
                if code[k] == "{":
                    depth += 1
                elif code[k] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            lines = text.split("\n")
            open_line = code.count("\n", 0, j)
            close_line = code.count("\n", 0, k)
            head = code[code.rfind("\n", 0, m.start()) + 1:m.end()]
            return path, lines, open_line + 1, close_line, "static" in head.split()
    raise KeyError(name)


def mutants(lines, start, end):
    """(line index, new line or "" to remove it, what) for every change of the body lines
    [start, end), in a fixed order."""
    comment = False
    for i in range(start, end):
        line = lines[i]
        text = line.strip()
        if comment or text.startswith("/*"):
            comment = "*/" not in text
            continue
        code = strip_code(line.split("/*")[0])
        if not code.strip() or code.strip() in ("{", "}") or (DECL.match(code) and "=" not in code):
            continue
        if code.rstrip().endswith(";") and not DECL.match(code) and "return" not in code \
                and code.strip() != ";" and not lines[i - 1].rstrip().endswith(")"):
            yield i, "", "removed"
        for old, new in SWAPS:
            for m in re.finditer(re.escape(old), code):
                yield i, line[:m.start()] + new + line[m.end():], "%s -> %s" % (old.strip(), new.strip())
        for m in re.finditer(r"\(([^()]*,[^()]*)\)", code):
            items = line[m.start(1):m.end(1)].split(", ")
            for k in range(len(items) - 1):
                if items[k] != items[k + 1]:
                    swapped = items[:k] + [items[k + 1], items[k]] + items[k + 2:]
                    yield i, line[:m.start(1)] + ", ".join(swapped) + line[m.end(1):], "%s <-> %s" % (items[k], items[k + 1])
        for m in re.finditer(r"(?<![\w.])(0x[0-9a-fA-F]+|\d+)(?![\w.])", code):
            value = int(m.group(1), 0)
            yield i, line[:m.start()] + "%d" % (value + 1) + line[m.end():], "%s -> %d" % (m.group(1), value + 1)


INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)


def sources(src):
    """The C files of a src directory and, recursively, the files they include with quotes:
    [(path, including file or None, index of the include line there)]."""
    out, seen = [], set()

    def visit(path, parent, index):
        path = os.path.normpath(path)
        if path in seen or not os.path.exists(path):
            return
        seen.add(path)
        out.append((path, parent, index))
        with open(path) as f:
            lines = f.read().split("\n")
        for i, line in enumerate(lines):
            m = INCLUDE_RE.match(line)
            if m:
                visit(os.path.join(os.path.dirname(path), m.group(1)), path, i)

    for d, _, files in sorted(os.walk(src)):
        for f in sorted(files):
            if f.endswith((".c", ".h")):
                visit(os.path.join(d, f), None, None)
    return out


def variant_text(origin, path, changed_lines):
    """(file name, text) to compile for a variant of `path`: the changed file itself when it is a
    source of its own, else the file that includes it with the changed text put in place of the
    include line (with #line, so that messages and line numbers stay those of the changed file)."""
    _, parent, index = origin
    if parent is None:
        return os.path.basename(path), "\n".join(changed_lines)
    with open(parent) as f:
        outer = f.read().split("\n")
    inlined = ['#line 1 "%s"' % path] + changed_lines + ['#line %d "%s"' % (index + 2, parent)]
    return os.path.basename(parent), "\n".join(outer[:index] + inlined + outer[index + 1:])


def users_of(lines, name, suite):
    """For a static helper: the functions of the same file that call it and have cases."""
    code = strip_code("\n".join(lines))
    out = []
    for fn in list(suite.cases) + list(suite.auto):
        try:
            _, _, start, end, _ = find_function_in_text(code, fn)
        except KeyError:
            continue
        body = "\n".join(code.split("\n")[start:end])
        if re.search(r"\b%s\s*\(" % re.escape(name), body) and fn not in out:
            out.append(fn)
    return out


def find_function_in_text(code, name):
    path = tempfile.mktemp(suffix=".c")
    try:
        with open(path, "w") as f:
            f.write(code)
        return find_function([path], name)
    finally:
        os.remove(path)


STATE = {}      # filled before the workers are forked


def code_digest(objects):
    """A digest of what the compiled variant consists of: the contents of every allocated
    section and of the relocations, so that two sources that compile to the same code (a change in
    a line the preprocessor leaves out, or one the compiler folds away) are recognised."""
    h = hashlib.sha1()
    for obj in sorted(objects):
        elf = Elf(obj)
        for sec in elf.sections:
            if sec.alloc or sec.name.startswith(".rel"):
                h.update(sec.name.encode() + b"\0" + elf.data(sec))
    return h.hexdigest()


def judge(task):
    """Build one variant and run the users' cases on it: (index, outcome) with outcome "caught",
    "alive" or "unbuilt"."""
    index, filename, text, include_dir, workdir = task
    target, image, names = STATE["target"], STATE["image"], STATE["names"]
    os.makedirs(workdir, exist_ok=True)
    src = os.path.join(workdir, "src")
    os.makedirs(src, exist_ok=True)
    with open(os.path.join(src, filename), "w") as f:
        f.write(text)
    try:
        objects = build.compile_all(target, image, src, os.path.join(workdir, "obj"),
                                    extra_include=[include_dir], quiet=True)
    except (overlay.OverlayError, OSError):
        return index, "unbuilt"
    digest = code_digest(objects)
    if index and digest == STATE.get("baseline"):
        return index, "same code"
    try:
        payload = os.path.join(workdir, "payload.bin")
        overlay.link(target, image, objects, payload, workdir)
    except (overlay.OverlayError, OSError):
        return index, "unbuilt"
    with open(payload, "rb") as f:
        runner.RUN["recon"] = f.read()
    runner.RUN["aliases"], _ = overlay.load_placement(os.path.join(workdir, "placement.json"))
    for name in names:
        _, _, bad, lines, missed = runner.run_function(name)
        if bad:
            return index, "caught"
    return index, ("alive", digest) if not index else ("alive", digest)


KILL_BUDGET = 6000      # runs of the original per survivor in the --kill search


def build_variant(target, image, filename, text, include_dir, workdir):
    """(image bytes, aliases) of one variant, or None when it does not build."""
    src = os.path.join(workdir, "src")
    os.makedirs(src, exist_ok=True)
    with open(os.path.join(src, filename), "w") as f:
        f.write(text)
    try:
        objects = build.compile_all(target, image, src, os.path.join(workdir, "obj"), extra_include=[include_dir],
                                    quiet=True)
        payload = os.path.join(workdir, "payload.bin")
        overlay.link(target, image, objects, payload, workdir)
    except (overlay.OverlayError, OSError):
        return None
    with open(payload, "rb") as f:
        data = f.read()
    return data, overlay.load_placement(os.path.join(workdir, "placement.json"))[0]


def kill(gen, name, fn, mutant, aliases, unchanged, base_cases, label):
    """Search an input on which `mutant` differs from the original (see the module's docstring).
    Returns ("found", case), ("suspect", case), ("refused", case) or ("none", None)."""
    from dread.emu import EmuError
    exact, near = gen.constants(fn.insns)
    values = sorted(set(gen.boundary) | {(c + d) & 0xFFFFFFFF for c in list(exact) + list(near) for d in (-1, 0, 1)})

    def run(payload, case, al=None, plan=None, replay=False):
        m = gen.machine(payload, al)
        m.plan, m.replay, m.function = plan if plan is not None else [], replay, name
        gen.apply(m, case)
        return m, m.call(fn.entry, *case["args"], max_instructions=case.get("limit", 200_000))

    def differs(payload, al, case):
        plan = []
        try:
            _, a = run(gen.payload, case, plan=plan)
        except EmuError:
            return None
        try:
            _, b = run(payload, case, al, plan=plan, replay=True)
        except EmuError:
            return True
        return bool(a.diff(b))

    def with_reads(case):
        """The case with every location the original loads from as an explicit value."""
        from unicorn import UC_HOOK_MEM_READ
        m = gen.machine()
        m.plan, m.function = [], name
        seen = {}

        def on_read(uc, access, addr, size, value, user):
            if size <= 4 and not gen.image.base <= addr < gen.image.end and not gen.layout.in_stack(addr):
                seen.setdefault(addr, size)
        try:
            gen.apply(m, case)
            m.hook_add(UC_HOOK_MEM_READ, on_read)
            m.call(fn.entry, *case["args"], max_instructions=case.get("limit", 200_000))
        except Exception:
            return case
        case = json.loads(json.dumps(case))
        have = {a for a, _, _ in case["mem"]}
        for addr, size in sorted(seen.items()):
            if addr not in have and not gen.target.memory.is_io(addr):
                have.add(addr)
                case["mem"].append([addr, size, int.from_bytes(m.read(addr, size), "little")])
        return case

    runs = 0
    for base in base_cases[:16]:
        base = with_reads(base)
        if differs(mutant, aliases, base) is None:
            continue
        slots = [("args", k) for k in range(len(base["args"]))] + [("returns", k) for k in range(len(base.get("returns", ())))] \
            + [("mem", k) for k in range(len(base["mem"]))]
        for kind, k in slots:
            for v in values:
                if runs >= KILL_BUDGET:
                    return "none", None
                case = json.loads(json.dumps(base))
                if kind == "mem":
                    case["mem"][k][2] = v & ((1 << 8 * case["mem"][k][1]) - 1)
                else:
                    case[kind][k] = v
                runs += 1
                if differs(mutant, aliases, case):
                    if differs(unchanged[0], unchanged[1], case) is not False:
                        return "suspect", case
                    m = gen.machine()
                    m.plan, m.function = [], name
                    gen.apply(m, case)
                    m.trace_reads(gen.image.base, gen.image.end, gen.code)
                    try:
                        m.call(fn.entry, *case["args"], max_instructions=case.get("limit", 200_000))
                    except Exception:
                        continue
                    if gen.touches_image(m):
                        return "refused", case
                    return "found", case
    return "none", None


def base_cases_for(gen, run, name, fn):
    """JSON cases to start the --kill search from: the function's generated, kept and written
    cases when it has auto(), else freshly generated ones (callees running for real)."""
    from dread import autocase
    if name in run["suite"].auto:
        return [c for _, _, _, _, _, c in autocase.cases_for(run, name, fn) if c is not None] + \
            autocase.extra_load(gen.image, name)
    try:
        return gen.generate(name, fn.entry, fn.insns)
    except autocase.GenerationError:
        return []


def argued_path(image):
    return os.path.join(os.path.dirname(image.cases), "argued.tsv")


def load_argued(image):
    """{(function, line, what): argument} from the image's argued.tsv."""
    out = {}
    path = argued_path(image)
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                fields = line.rstrip("\n").split("\t")
                if len(fields) >= 4 and not line.startswith("#"):
                    out[(fields[0], fields[1].strip(), fields[2].strip())] = fields[3]
    return out


def append_case(image, name, case, label):
    from dread import autocase
    path = os.path.join(image.extra_cases, name + ".json")
    old = []
    if os.path.exists(path):
        with open(path) as f:
            old = json.load(f)["cases"]
    keep = {k: case[k] for k in ("args", "mem", "returns", "effects", "fills", "io", "poll", "limit") if case.get(k)}
    keep["args"] = case["args"]
    keep["mem"] = case["mem"]
    keep["by"] = "dread mutate --kill"
    keep["why"] = "tells the original from the change: %s" % label
    os.makedirs(image.extra_cases, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"cases": old + [keep]}, f, indent=1, sort_keys=True)


def check(target, image, name, line_range=None, seconds=300, jobs=None, out=print, kill_survivors=False):
    """Mutate one function; returns (caught, survivors [(line, text, what)], unbuilt, ran away)."""
    origins = sources(image.src)
    path, lines, start, end, static = find_function([p for p, _, _ in origins], name)
    origin = next(o for o in origins if o[0] == path)
    compiled = origin[1] or path            # the file the compiler gets
    include_dir = os.path.dirname(compiled)
    suite = runner.prepare(target, image, os.path.join(build.recon_dir(target, image), "payload.bin"))
    names = users_of(lines, name, suite) if static else [name]
    if not names or not any(n in suite.cases or n in suite.auto for n in names):
        raise KeyError("%s has no cases (and no function with cases calls it)" % name)
    variants = [(i, new, what) for i, new, what in mutants(lines, start, end)
                if line_range is None or line_range[0] <= i + 1 <= line_range[1]]
    work = tempfile.mkdtemp(prefix="dread-mutate-", dir=os.path.join(target.build))
    STATE.update(target=target, image=image, names=names)
    tasks = [(0,) + variant_text(origin, path, lines) + (include_dir, os.path.join(work, "0"))]
    for k, (i, new, what) in enumerate(variants, 1):
        changed = list(lines)
        changed[i] = new
        tasks.append((k,) + variant_text(origin, path, changed) + (include_dir, os.path.join(work, str(k))))
    results = {}
    try:
        first = judge(tasks[0])[1]          # the unchanged source first: its code is the baseline
        results[0] = first[0] if isinstance(first, tuple) else first
        STATE["baseline"] = first[1] if isinstance(first, tuple) else None
        with multiprocessing.get_context("fork").Pool(jobs or workers(), initializer=die_with_parent,
                                                      maxtasksperchild=8) as pool:
            handles = [(t[0], pool.apply_async(judge, (t,))) for t in tasks[1:]]
            for k, h in handles:
                try:
                    outcome = h.get(timeout=seconds)[1]
                    results[k] = outcome[0] if isinstance(outcome, tuple) else outcome
                except multiprocessing.TimeoutError:
                    results[k] = "ran away"
            pool.terminate()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if results.get(0) != "alive":
        raise RuntimeError("%s: the unchanged source %s (build or cases fail before any change)"
                           % (name, "does not build" if results.get(0) == "unbuilt" else "fails its cases"))
    caught = [k for k in range(1, len(tasks)) if results[k] in ("caught", "ran away")]
    away = [k for k in range(1, len(tasks)) if results[k] == "ran away"]
    unbuilt = [k for k in range(1, len(tasks)) if results[k] == "unbuilt"]
    same = [k for k in range(1, len(tasks)) if results[k] == "same code"]
    survivors = [variants[k - 1] for k in range(1, len(tasks)) if results[k] == "alive"]
    rel = os.path.relpath(path, target.root)
    killed = []
    if kill_survivors and survivors:
        from dread import autocase
        gen = autocase.generator_for(runner.RUN)
        fn = runner.RUN["program"].by_name[name]
        bases = base_cases_for(gen, runner.RUN, name, fn)
        work2 = tempfile.mkdtemp(prefix="dread-kill-", dir=target.build)
        try:
            unchanged = build_variant(target, image, *variant_text(origin, path, lines), include_dir, os.path.join(work2, "u"))
            for n, (i, new, what) in enumerate(survivors):
                label = "%s:%d  %s   [%s]" % (rel, i + 1, lines[i].strip(), what)
                changed = list(lines)
                changed[i] = new
                built = build_variant(target, image, *variant_text(origin, path, changed), include_dir,
                                      os.path.join(work2, str(n)))
                if built is None or unchanged is None:
                    continue
                result, case = kill(gen, name, fn, built[0], built[1], unchanged, bases, label)
                if result == "found":
                    append_case(image, name, case, label)
                    killed.append((i, new, what))
                elif result == "suspect":
                    out("  the input found for [%s] separates the original from the unchanged C too: %s"
                        % (what, json.dumps(case)[:300]))
        finally:
            shutil.rmtree(work2, ignore_errors=True)
        survivors = [s for s in survivors if s not in killed]
        caught += [None] * len(killed)
    known = load_argued(image)
    argued = [(i, new, what, known[(name, lines[i].strip(), what)]) for i, new, what in survivors
              if (name, lines[i].strip(), what) in known]
    survivors = [(i, new, what) for i, new, what in survivors if (name, lines[i].strip(), what) not in known]
    out("%-26s %d changes caught, %d survived, %d did not build%s%s%s"
        % (name, len(caught), len(survivors), len(unbuilt), ", %d argued" % len(argued) if argued else "",
           ", %d compiled to the same code" % len(same) if same else "",
           ", %d of the caught ran away (over %d s)" % (len(away), seconds) if away else ""))
    if killed:
        out("  %d caught by new written cases (%s)" % (len(killed), os.path.relpath(
            os.path.join(image.extra_cases, name + ".json"), target.root)))
    for i, new, what in survivors:
        out("  %s:%d  %s   [%s]" % (rel, i + 1, lines[i].strip(), what))
    for i, new, what, why in argued:
        out("  argued %s:%d  [%s]: %s" % (rel, i + 1, what, why))
    return len(caught), survivors, len(unbuilt), len(away)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread mutate", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--lines", help="only change lines FIRST-LAST of the source file")
    parser.add_argument("--seconds", type=int, default=300, help="time a variant may take (default 300)")
    parser.add_argument("--jobs", type=int, help="worker processes (default: the polite share of the cores)")
    parser.add_argument("--kill", action="store_true", help="search inputs that catch the survivors and keep them")
    parser.add_argument("names", nargs="+")
    args = parser.parse_args(argv)
    polite()
    line_range = tuple(int(x) for x in args.lines.split("-")) if args.lines else None
    try:
        target, image = tgt.from_args(args)
        if not os.path.exists(os.path.join(build.recon_dir(target, image), "payload.bin")):
            build.build(target, image, quiet=True)
        alive = 0
        for name in args.names:
            _, survivors, _, _ = check(target, image, name, line_range, args.seconds, args.jobs,
                                       kill_survivors=args.kill)
            alive += len(survivors)
    except (tgt.TargetError, KeyError, RuntimeError, overlay.OverlayError) as e:
        print("dread mutate: %s" % e, file=sys.stderr)
        return 1
    return 1 if alive else 0


if __name__ == "__main__":
    sys.exit(main())
