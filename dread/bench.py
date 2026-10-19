"""dread bench: decompile a real project built in many ways, with its ELF as the ground truth.

A recipe (bench/NAME.toml) names a pinned source (a git commit, or a local directory), the
projects in it (the sources of one firmware, its include directories and defines, text
substitutions in its files, its linker script and link options, and the DREAD target its image
becomes) and the builds (a project with a compiler, a link driver and flags). For each build the
tool compiles the sources with the build's flags, links, and imports the ELF with dread import-elf:
functions, code ranges, symbols, the startup copies and the free ranges are the ground truth. Then
it measures what DREAD makes of the image, running its tools as they are run on a real target:

- functions and instruction bytes, from the ground truth;
- the listing: whether dread asm --verify rebuilds the image byte for byte, and with how many raw
  words;
- Ghidra: the function starts its analysis finds without the ELF's symbols (dread ghidra
  --no-symbols), against the ELF's: found, missed, extra;
- the lifter: dread lift --check, the functions whose lifted C agrees with the original on
  generated cases that run every instruction (accepted), and why the others are not;
- recorded inputs (when the target has system-run scenarios): dread sysrecord records calls of the
  functions the lifter left uncovered or got no inputs for in each scenario, and the lift check
  runs again with the cases kept (a rebuild whose image differs drops them);
- noreturn: the functions found never to return;
- the seconds each step took.

A build's figures go to BENCH/NAME/BUILD/metrics.json, and `report` gathers every build's into
BENCH/NAME/report.md. BENCH is ~/dread-bench (or $DREAD_BENCH). Only recipes are kept in DREAD's
repository; sources, builds and reports stay in BENCH (CLAUDE.md, rule 1).

Recipe format:

    name = "NAME"
    [source]                  # git = URL, commit = SHA (sparse = [paths], submodules = [paths]),
    git = "..."               # or path = DIR (relative to the recipe)
    commit = "..."
    [projects.P]
    sources = ["path/a.c", "path/start.S", ...]     # relative to the source's root
    includes = ["path", ...]
    defines = ["NAME=VALUE", ...]
    patches = [{file = "path/main.c", old = "...", new = "..."}]   # in a copy, never in the source
                              # (a source, or a header: its directory is copied, see patched_sources)
    ldscript = "path/x.ld"
    link = ["-nostartfiles", ...]                   # link options after the objects
    base = 0x0                # the image's load address; entry: a symbol name; limit: the end of the
    entry = "Reset_Handler"   # room behind the image (its flash), for C that moves
    target = '''...'''        # the dread.toml's sections other than [image.main] and [compiler]
    [[builds]]
    name = "B"
    project = "P"
    cc = "arm-none-eabi-gcc"  # the compiler; ld: the link driver (default cc)
    cflags = [...]            # for compiling (and linking, unless lflags gives the link driver's own)
    dread_cflags = [...]      # optional: the flags rewritten C is compiled with (default: cflags
                              # without -g, -W, -M, -I and -D options)
    [[builds.patches]]        # optional: substitutions of this build only, after the project's

    dread bench fetch RECIPE
    dread bench build RECIPE [BUILD...]
    dread bench measure RECIPE [BUILD...] [--steps asm,ghidra,lift,record,noreturn]
    dread bench report RECIPE
    dread bench all RECIPE [BUILD...] [--steps ...]
"""
import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import time

STEPS = ("asm", "ghidra", "lift", "record", "noreturn")
RECORDABLE = ("UNCOVERED", "[case generation]")     # refusals that recorded inputs may lift
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class BenchError(Exception):
    pass


def bench_dir():
    return os.environ.get("DREAD_BENCH") or os.path.join(os.path.expanduser("~"), "dread-bench")


def load(path):
    """The recipe at path (a file, or a name of bench/ in DREAD's repository)."""
    import tomllib
    if not os.path.exists(path) and os.path.exists(os.path.join(ROOT, "bench", path + ".toml")):
        path = os.path.join(ROOT, "bench", path + ".toml")
    try:
        with open(path, "rb") as f:
            recipe = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise BenchError("recipe %s: %s" % (path, e))
    for key in ("name", "source", "projects", "builds"):
        if key not in recipe:
            raise BenchError("recipe %s: %s is missing" % (path, key))
    recipe["_file"] = os.path.abspath(path)
    names = [b["name"] for b in recipe["builds"]]
    if len(set(names)) != len(names):
        raise BenchError("recipe %s: a build name is used twice" % path)
    for b in recipe["builds"]:
        if b.get("project") not in recipe["projects"]:
            raise BenchError("recipe %s: build %s names no project of the recipe" % (path, b["name"]))
    return recipe


def run(cmd, cwd=None, quiet=True, timeout=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise BenchError("%s failed (%d):\n%s" % (" ".join(cmd[:3]), r.returncode, (r.stderr or r.stdout)[-3000:]))
    return r.stdout


# ---- the source ----

def source_dir(recipe, bench=None):
    src = recipe["source"]
    if "path" in src:
        return os.path.normpath(os.path.join(os.path.dirname(recipe["_file"]), src["path"]))
    return os.path.join(bench or bench_dir(), "src", recipe["name"])


def fetch(recipe, bench=None, out=print):
    """The source at its pinned commit (cloned on first use: no blobs until needed, sparse when the
    recipe lists paths); returns its directory."""
    src, where = recipe["source"], source_dir(recipe, bench)
    if "path" in src:
        if not os.path.isdir(where):
            raise BenchError("source %s does not exist" % where)
        return where
    commit = src["commit"]
    if os.path.isdir(os.path.join(where, ".git")):
        head = run(["git", "rev-parse", "HEAD"], cwd=where).strip()
        if head == commit:
            for sub in src.get("submodules", ()):
                run(["git", "submodule", "update", "--init", "--filter=blob:none", sub], cwd=where)
            return where
    else:
        os.makedirs(os.path.dirname(where), exist_ok=True)
        out("fetching %s" % src["git"])
        run(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", src["git"], where])
    if src.get("sparse"):
        run(["git", "sparse-checkout", "set", "--no-cone"] + list(src["sparse"]), cwd=where)
    run(["git", "checkout", "--quiet", commit], cwd=where)
    for sub in src.get("submodules", ()):
        run(["git", "submodule", "update", "--init", "--filter=blob:none", sub], cwd=where)
    return where


# ---- building ----

def build_dir(recipe, name, bench=None):
    return os.path.join(bench or bench_dir(), recipe["name"], name)


def the_build(recipe, name):
    for b in recipe["builds"]:
        if b["name"] == name:
            return b
    raise BenchError("no build %s in %s (there are: %s)" % (name, recipe["_file"],
                                                            ", ".join(b["name"] for b in recipe["builds"])))


def patched_sources(project, src, work, extra=()):
    """({source path: the file to compile}, [include directories to search first]): copies with the
    project's substitutions (and the build's, extra). A patched header's directory is copied whole
    into the work directory: the sources in it compile from the copy, so a header they include by
    name is the patched one (the including file's own directory is searched first), and the copy is
    searched before the project's includes by the others. (A file there that includes another by a
    relative path upwards would miss it.)"""
    files = {s: os.path.join(src, s) for s in project["sources"]}
    patches = list(project.get("patches", ())) + list(extra)
    mirror = os.path.join(work, "patched")
    shutil.rmtree(mirror, ignore_errors=True)       # an old patch must not shadow anything
    mirrored = sorted({os.path.dirname(p["file"]) for p in patches if p["file"] not in files})
    for d in mirrored:
        if not os.path.isdir(os.path.join(src, d)):
            raise BenchError("patch of a file in %s, which is not in the project" % (d or "."))
        os.makedirs(os.path.join(mirror, d), exist_ok=True)
        for name in sorted(os.listdir(os.path.join(src, d))):
            if os.path.isfile(os.path.join(src, d, name)):
                shutil.copyfile(os.path.join(src, d, name), os.path.join(mirror, d, name))
    for name in files:
        if os.path.dirname(name) in mirrored:
            files[name] = os.path.join(mirror, name)
    for p in patches:
        copy = os.path.join(mirror, p["file"])
        path = files.get(p["file"], copy)           # (a second substitution in one file builds on the first)
        if not os.path.isfile(path):
            raise BenchError("patch of %s, which is not in the project" % p["file"])
        with open(path) as f:
            text = f.read()
        if text.count(p["old"]) != 1:
            raise BenchError("patch of %s: the text to replace occurs %d times" % (p["file"], text.count(p["old"])))
        os.makedirs(os.path.dirname(copy), exist_ok=True)
        with open(copy, "w") as f:
            f.write(text.replace(p["old"], p["new"]))
        if p["file"] in files:
            files[p["file"]] = copy
    return files, [os.path.join(mirror, d) for d in mirrored]


def dread_compiler(b):
    """The target's [compiler] section for rewritten C, from the build's compiler and flags."""
    cc = os.path.basename(b["cc"])
    flags = b.get("dread_cflags")
    if flags is None:
        flags = [f for f in b["cflags"] if not f.startswith(("-g", "-W", "-M", "-I", "-D"))]
    if "clang" in cc:
        triple = next((f.split("=", 1)[1] for f in flags if f.startswith("--target=")), None)
        rest = [f for f in flags if not f.startswith("--target=")]
        lines = ['kind = "clang"'] + (['triple = "%s"' % triple] if triple else [])
    else:
        if not cc.endswith("gcc"):
            raise BenchError("build %s: cannot tell the compiler kind of %s" % (b["name"], cc))
        lines, rest = ['cross = "%s"' % cc[:-3]], flags
    lines.append("cflags = [%s]" % ", ".join(json.dumps(f) for f in rest))
    return "[compiler]\n" + "\n".join(lines) + "\n"


def build(recipe, name, bench=None, jobs=None, out=print):
    """Compile and link one build, make its DREAD target and import the ELF; returns the target
    directory."""
    b = the_build(recipe, name)
    project = recipe["projects"][b["project"]]
    src = fetch(recipe, bench, out)
    where = build_dir(recipe, name, bench)
    work = os.path.join(where, "work")
    objdir = os.path.join(work, "obj")
    os.makedirs(objdir, exist_ok=True)
    files, first = patched_sources(project, src, work, b.get("patches", ()))
    flags = list(b["cflags"]) + ["-D" + d for d in project.get("defines", ())] + ["-I" + d for d in first] + \
        ["-I" + os.path.join(src, i) for i in project.get("includes", ())]
    cc, ld = b["cc"], b.get("ld", b["cc"])
    started = time.time()
    objects = []

    def compile_one(item):
        source, path = item
        obj = os.path.join(objdir, source.replace("/", "__") + ".o")
        run([cc] + flags + ["-c", path, "-o", obj], cwd=src)
        return obj
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs or os.cpu_count() or 4) as pool:
        objects = list(pool.map(compile_one, sorted(files.items())))
    elf = os.path.join(where, "firmware.elf")
    run([ld] + list(b.get("lflags", b["cflags"])) + ["-T", os.path.join(src, project["ldscript"]),
                                                                 "-Wl,-Map=" + os.path.join(work, "firmware.map"),
                                                                 "-o", elf] + objects + list(project.get("link", ())),
        cwd=src)
    seconds = time.time() - started
    os.makedirs(os.path.join(where, "original"), exist_ok=True)
    with open(os.path.join(where, "dread.toml"), "w") as f:
        f.write("# %s, build %s (dread bench %s): made by dread bench from %s; not part of DREAD.\n\n"
                % (recipe["name"], name, os.path.basename(recipe["_file"]), b["project"]))
        f.write(project["target"].strip() + "\n\n")
        f.write('[image.main]\noriginal = "original/firmware.bin"\nbase = 0x%x\nentry = "%s"\n'
                % (project.get("base", 0), project.get("entry", "Reset_Handler")))
        if "limit" in project:
            f.write("limit = 0x%x\n" % project["limit"])
        if "vectors" in project:
            f.write("vectors = 0x%x\n" % project["vectors"])
        f.write("\n" + dread_compiler(b))
    from dread import elfimport
    payload = os.path.join(where, "original", "firmware.bin")
    old = open(payload, "rb").read() if os.path.exists(payload) else None
    if elfimport.main(["--target", where, "--payload", payload, "--write-startup", elf]):
        raise BenchError("dread import-elf failed on %s" % elf)
    if old is not None and open(payload, "rb").read() != old:     # cases of the old image fit it only
        from dread.target import Target
        image = Target.load(where).image()
        for stale in (image.extra_cases, image.kept_cases):
            shutil.rmtree(stale, ignore_errors=True)
    with open(os.path.join(where, "build.json"), "w") as f:
        json.dump({"recipe": recipe["name"], "build": name, "project": b["project"], "cc": cc,
                   "cflags": b["cflags"], "sources": len(files), "seconds": round(seconds, 1)}, f, indent=1)
    out("%s: built %d sources in %.0f s" % (name, len(files), seconds))
    return where


# ---- measuring ----

def dread(*args, cwd=None, timeout=None, log=None):
    """Run a dread command as a separate process (a tool's state stays its own); (seconds, exit
    status, output). With log, the output also goes to that file."""
    started = time.time()
    r = subprocess.run([sys.executable, "-m", "dread.cli"] + list(args), cwd=cwd or ROOT, capture_output=True,
                       text=True, timeout=timeout)
    if log:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "w") as f:
            f.write("$ dread %s\n%s%s\nexit %d\n" % (" ".join(args), r.stdout, r.stderr, r.returncode))
    return time.time() - started, r.returncode, r.stdout + r.stderr


def read_entries(path):
    from dread.program import read_functions
    return {entry: name for entry, name, _ in read_functions(path)}


def measure(recipe, name, bench=None, steps=STEPS, out=print):
    """Measure one build (see the module's docstring); returns and writes its metrics."""
    from dread.program import Program
    from dread.target import Target
    where = build_dir(recipe, name, bench)
    if not os.path.exists(os.path.join(where, "dread.toml")):
        raise BenchError("%s is not built yet" % name)
    target = Target.load(where)
    image = target.image()
    program = Program(target, image)
    metrics = {}
    previous = os.path.join(where, "metrics.json")
    if os.path.exists(previous):                # the steps not run now keep their figures
        with open(previous) as f:
            metrics = {k: v for k, v in json.load(f).items() if k in STEPS}
    metrics.update({"build": name, "functions": len(program.functions),
                    "instruction_bytes": sum(e - s for s, e, _ in program.ranges),
                    "image_bytes": len(image.payload())})
    with open(os.path.join(where, "build.json")) as f:
        info = json.load(f)
    metrics.update(cc=info["cc"], cflags=" ".join(info["cflags"]))
    truth = {fn.entry for fn in program.functions}
    if "asm" in steps:
        raw_file = os.path.join(where, "work", "raw.txt")
        if os.path.exists(raw_file):            # each measurement starts without raw words
            os.remove(raw_file)
        seconds, code, text = dread("asm", "--target", where, "--verify", "--out", os.path.join(where, "work", "asm-out"),
                                    "--build", os.path.join(where, "work", "asm"),
                                    "--raw", os.path.join(where, "work", "raw.txt"), log=os.path.join(where, "work", "asm.log"))
        raw = 0
        for line in text.splitlines():
            if line.startswith("pass "):
                raw = int(line.split(",")[1].split()[0])
        metrics["asm"] = {"identical": "byte-identical" in text, "raw_words": raw, "seconds": round(seconds, 1)}
    if "ghidra" in steps:
        tables = os.path.join(where, "work", "ghidra")
        seconds, code, text = dread("ghidra", "--target", where, "--no-symbols", "--no-decompile", "--out", tables,
                                    "--project", os.path.join(where, "work", "ghidra-project"), log=os.path.join(where, "work", "ghidra.log"))
        found = set(read_entries(os.path.join(tables, "functions.tsv"))) if code == 0 else set()
        metrics["ghidra"] = {"ran": code == 0, "found": len(found & truth), "missed": len(truth - found),
                             "extra": len(found - truth), "seconds": round(seconds, 1)}
    if "lift" in steps:
        metrics["lift"] = lift_step(target, image, where, "lift.log")
    if "record" in steps:
        metrics["record"] = record_step(target, image, where)
        if metrics["record"].get("kept"):
            before = metrics.get("lift", {}).get("verdicts", {}).get("accepted")
            metrics["lift"] = lift_step(target, image, where, "lift-recorded.log")
            metrics["record"]["accepted_before"] = before
    if "noreturn" in steps:
        seconds, code, text = dread("noreturn", "--target", where, log=os.path.join(where, "work", "noreturn.log"))
        metrics["noreturn"] = {"functions": len([l for l in text.splitlines() if l.strip()]) if code == 0 else None,
                               "seconds": round(seconds, 1)}
    with open(os.path.join(where, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=1, sort_keys=True)
    out("%s: %s" % (name, summary_line(metrics)))
    return metrics


def verdicts_of(target, image):
    """[(function, verdict, reason)] of the image's last lift check."""
    path = os.path.join(target.build, "lift-check", image.name, "verdicts.tsv")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        next(f)
        return [tuple((line.rstrip("\n").split("\t") + ["", ""])[:3]) for line in f]


def lift_step(target, image, where, log):
    """dread lift --check, and its verdicts counted."""
    seconds, code, text = dread("lift", "--target", where, "--check", log=os.path.join(where, "work", log))
    verdicts, reasons = {}, {}
    for fn, verdict, reason in verdicts_of(target, image):
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        if verdict != "accepted":
            key = verdict + ": " + reason_kind(reason)
            reasons[key] = reasons.get(key, 0) + 1
    return {"ran": code == 0, "verdicts": verdicts, "reasons": reasons, "seconds": round(seconds, 1)}


def record_step(target, image, where):
    """dread sysrecord in every scenario of the target for the functions the last lift check left
    uncovered or got no inputs for (RECORDABLE): {functions, kept, seconds} (kept: the cases kept,
    which add instructions), or {"ran": False} without scenarios or verdicts."""
    import re
    names = sorted(fn for fn, verdict, reason in verdicts_of(target, image)
                   if verdict != "accepted" and any(k in reason for k in RECORDABLE))
    if not target.scenarios or not names:
        return {"ran": False, "functions": len(names)}
    kept, seconds, ran = 0, 0.0, True
    for scenario in sorted(target.scenarios):
        s, code, text = dread("sysrecord", "--target", where, "--image", image.name, "--scenario", scenario, *names,
                              log=os.path.join(where, "work", "record-%s.log" % scenario))
        seconds += s
        ran = ran and code == 0
        kept += sum(int(n) for n in re.findall(r"(\d+) kept;", text))
    return {"ran": ran, "functions": len(names), "kept": kept, "seconds": round(seconds, 1)}


def reason_kind(reason):
    """A refusal's reason without its particulars (registers, addresses, numbers), for counting."""
    import re
    text = re.sub(r"0x[0-9a-fA-F]+|\b\d+\b", "N", reason)
    text = re.sub(r"\br\d+\b|\b[as]\d+\b|\bt\d\b", "REG", text)
    return text[:90]


def summary_line(m):
    parts = ["%d functions" % m["functions"]]
    if "asm" in m:
        parts.append("listing %s" % ("identical" if m["asm"]["identical"] else "DIFFERS"))
    if "ghidra" in m and m["ghidra"]["ran"]:
        parts.append("ghidra found %d, missed %d, extra %d" % (m["ghidra"]["found"], m["ghidra"]["missed"],
                                                              m["ghidra"]["extra"]))
    if "lift" in m:
        v = m["lift"]["verdicts"]
        parts.append("lifted %d of %d" % (v.get("accepted", 0), sum(v.values())))
    r = m.get("record", {})
    if r.get("kept"):
        parts.append("%d recorded cases (%d before them)" % (r["kept"], r.get("accepted_before") or 0))
    return ", ".join(parts)


def report(recipe, bench=None, out=print):
    """BENCH/NAME/report.md from every build's metrics.json."""
    rows = []
    for b in recipe["builds"]:
        path = os.path.join(build_dir(recipe, b["name"], bench), "metrics.json")
        if os.path.exists(path):
            with open(path) as f:
                rows.append(json.load(f))
    lines = ["# %s: what DREAD makes of it" % recipe["name"], "",
             "Made by `dread bench report` from each build's metrics.json; figures only.", "",
             "| build | compiler | functions | code bytes | listing | Ghidra found / missed / extra | lifted | "
             "recorded cases (lifted before them) | never return | seconds (asm, ghidra, lift) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for m in rows:
        g, lf = m.get("ghidra", {}), m.get("lift", {})
        v = lf.get("verdicts", {})
        r = m.get("record", {})
        lines.append("| %s | %s | %d | %d | %s | %s | %s | %s | %s | %s |" % (
            m["build"], os.path.basename(m.get("cc", "")), m["functions"], m["instruction_bytes"],
            ("identical" if m["asm"]["identical"] else "differs") + (" (%d raw)" % m["asm"]["raw_words"]
                                                                     if m["asm"]["raw_words"] else "")
            if "asm" in m else "-",
            "%d / %d / %d" % (g["found"], g["missed"], g["extra"]) if g.get("ran") else "-",
            "%d of %d (%.0f %%)" % (v.get("accepted", 0), sum(v.values()), 100.0 * v.get("accepted", 0) /
                                    max(1, sum(v.values()))) if v else "-",
            "%d (%s)" % (r["kept"], r.get("accepted_before", "-")) if r.get("kept") else "-",
            m.get("noreturn", {}).get("functions", "-"),
            ", ".join("%.0f" % m[s]["seconds"] if s in m else "-" for s in ("asm", "ghidra", "lift"))))
    for m in rows:
        reasons = m.get("lift", {}).get("reasons")
        if reasons:
            lines += ["", "## %s: why the rest is not lifted" % m["build"], ""]
            lines += ["- %d x %s" % (n, r) for r, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:15]]
    path = os.path.join(bench or bench_dir(), recipe["name"], "report.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    out("report: %s" % path)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread bench", description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=("fetch", "build", "measure", "report", "all"))
    parser.add_argument("recipe", help="a recipe file, or the name of one in DREAD's bench/")
    parser.add_argument("builds", nargs="*", help="builds of the recipe (default: all)")
    parser.add_argument("--steps", default=",".join(STEPS), help="measuring steps (default: %s)" % ",".join(STEPS))
    parser.add_argument("--jobs", type=int, help="compilers at once")
    args = parser.parse_args(argv)
    try:
        recipe = load(args.recipe)
        names = args.builds or [b["name"] for b in recipe["builds"]]
        steps = [s for s in args.steps.split(",") if s]
        unknown = set(steps) - set(STEPS)
        if unknown:
            raise BenchError("unknown steps %s (known: %s)" % (", ".join(sorted(unknown)), ", ".join(STEPS)))
        if args.action == "fetch":
            print(fetch(recipe))
        for name in names:
            if args.action in ("build", "all"):
                build(recipe, name, jobs=args.jobs)
            if args.action in ("measure", "all"):
                measure(recipe, name, steps=steps)
        if args.action in ("report", "all", "measure"):
            report(recipe)
    except BenchError as e:
        print("dread bench: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
