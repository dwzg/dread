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
  generated cases that run every instruction (accepted, by name), and why the others are not; the
  names gained and lost against the build's previous measurement (against_last), so that a change
  of a few functions can be traced to them;
- recorded inputs (when the target has system-run scenarios): dread sysrecord records calls of the
  functions the lifter left uncovered or got no inputs for in each scenario, and the loops of the
  tasks whose loop cases left instructions unrun (as loop cases: the answers their callees gave them
  in the run), and the lift check runs again with the cases kept (a rebuild whose image differs drops
  them);
- readable C (readable_step): dread readable --check on the lifted table (run again only when a
  digest of what it decides from changed: DREAD's code, the build's tables, image and cases kept;
  otherwise its last results are taken), the functions lifted, turned readable (the gate built
  them from readable C) and accepted, and why the rest stays lifted, by category; then the
  readable C of the accepted ones measured by dread.cmetrics (dread readable --figures): statements,
  the deepest nesting of control statements, gotos and labels left, casts, raw memory accesses
  through pointer casts (those through the frame's base apart), members of structures (p->f_..;
  the frame's object apart), fixed addresses by name, variables and the typed ones, as sums,
  medians and maxima, beside the same functions' lifted C; and against the source: each readable
  function is matched by its entry to the subprogram DWARF has there (a clone, .constprop, .isra,
  .part, to its source function; .part is a piece of it and stays out of the ratios), its
  translation unit to the project's source by the end of its path, and the source function's
  statements, depth and gotos are counted alike on clang's tree of that source as the build's
  compiler preprocessed it (source_side); per function readable / source (and against the source
  with the functions DWARF says were inlined into it), median and quartiles, and the ten worst
  functions by statements and by depth, by name, for reading. What did not match is counted by why
  (no debug information at its entry: none, or a subprogram without code there; a library's source;
  not in the source's tree). Every readable function's
  figures go to BUILD/work/readable.tsv. Without clang or pyelftools the source side is skipped and
  says so. It does not establish that readable C reads well, only how it compares;
- noreturn: the functions found never to return;
- callconv: dread callconv, the registers each function's cases show it reads and changes, against
  dread.regflow's answers: the functions that disagree, by kind and by name;
- whatcc: dread whatcc, which toolchain's libraries the image links by the bytes of their functions,
  against the toolchain that built it (its image, or the host's), and how many of the names it gives
  as certain are the functions' own;
- groups: dread groups, the functions grouped by their code alone, against the source file of each
  in the build's debug information: the adjusted Rand index, purity and completeness;
- the seconds each step took.

A build keeps a digest of what in the recipe made it (build.json, made_from): measuring a build
whose recipe changed since builds it again first, so a figure always belongs to the recipe as it is.

A build's figures go to BENCH/NAME/BUILD/metrics.json (the previous one to BUILD/history/, named
by its time), and `report` gathers every build's into BENCH/NAME/report.md. BENCH is ~/dread-bench (or $DREAD_BENCH). Only recipes are kept in DREAD's
repository; sources, builds and reports stay in BENCH. `report --json FILE` (also `measure` and `all`,
which end with the report) writes the same as data (report_data, docs/formats.md): each build's
figures as the report shows them (FIGURES: one number each, figures()), what stops the rest by
category, its compiler (kind, release, the flags' optimisation and cpu), whether the recipe changed
since it was built, its metrics without the lists of every function's name, and the same figures of
each earlier measurement in BUILD/history for a trend. dread gui's bench page reads that file.

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
    ldscript = "path/x.ld"   # optional (a toolchain may pick the device's script itself: -mmcu)
    link = ["-nostartfiles", ...]                   # link options after the objects
    base = 0x0                # the image's load address; entry: a symbol name; limit: the end of the
    entry = "Reset_Handler"   # room behind the image (its flash), for C that moves
    target = '''...'''        # the dread.toml's sections other than [image.main] and [compiler]
    [[builds]]
    name = "B"
    project = "P"
    toolchain = "ubuntu-22.04"  # optional: compile and link in that toolchain's container (below)
    cc = "arm-none-eabi-gcc"  # the compiler; ld: the link driver (default cc)
    cflags = [...]            # for compiling (and linking, unless lflags gives the link driver's own)
    link = [...]              # optional: replaces the project's link options (another C library)
    dread_cflags = [...]      # optional: the flags rewritten C is compiled with (default: cflags
                              # without -g, -W, -M, -I and -D options)
    [[builds.patches]]        # optional: substitutions of this build only, after the project's

Toolchains: a build that names `toolchain = NAME` is compiled and linked in a podman container of the
image localhost/dread-tc-NAME:DIGEST, made from bench/toolchains/NAME.containerfile, DIGEST the start
of the file's (a distribution's packaged cross compilers; `dread bench toolchain NAME...` builds
images, which a build also does when the image of its file is missing; `--prune` removes the images
of older files). With the digest in the name, a worktree that changes a file builds an image of its
own and does not take the image from under a measurement that runs from the checkout. The source is mounted read-only and the build's
directory read-write, both at their own paths, without network; DREAD itself runs on the host and
compiles rewritten and lifted C with the host's compiler. Every build records the version its
compiler gives (cc --version), which the report shows: a compiler's version decides code shapes.

    dread bench toolchain NAME... [--rebuild] [--prune]
    dread bench fetch RECIPE
    dread bench build RECIPE [BUILD...]
    dread bench measure RECIPE [BUILD...] [--steps asm,ghidra,lift,record,readable,noreturn,callconv,whatcc,groups]
                        [--json FILE]
    dread bench report RECIPE [--json FILE]
    dread bench all RECIPE [BUILD...] [--steps ...] [--json FILE]
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

STEPS =("asm", "ghidra", "lift", "record", "readable", "noreturn", "callconv", "whatcc", "groups")
RECORDABLE = ("UNCOVERED", "[case generation]",     # refusals that recorded inputs may lift (a task's
              "its loop cases never ran")           # loop: dread sysrecord's loop cases)
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


# ---- toolchains in containers ----

IMAGE_PREFIX = "localhost/dread-tc-"


def toolchain_file(name):
    return os.path.join(ROOT, "bench", "toolchains", name + ".containerfile")


def toolchain_digest(name):
    """What a toolchain's image is made from (its Containerfile), for build.json and the image's label."""
    path = toolchain_file(name)
    if not os.path.exists(path):
        raise BenchError("no toolchain %s (%s)" % (name, path))
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def toolchain_image(name):
    """The name of a toolchain's image: its file's digest is the tag, so that an image is never
    replaced under a run that uses it (a worktree that changes the file builds one of its own)."""
    return "%s%s:%s" % (IMAGE_PREFIX, name, toolchain_digest(name)[:12])


def ensure_toolchain(name, rebuild=False, out=print):
    """The image of a toolchain, built with podman when the image of its file is missing or rebuild
    is set; returns the image's name."""
    if shutil.which("podman") is None:
        raise BenchError("toolchain %s needs podman" % name)
    digest, image = toolchain_digest(name), toolchain_image(name)
    if rebuild or subprocess.run(["podman", "image", "exists", image]).returncode:
        out("toolchain %s: building %s" % (name, image))
        import tempfile
        with tempfile.TemporaryDirectory() as context:
            run(["podman", "build", "-q", "--label", "dread.digest=" + digest, "-t", image, "-f",
                 toolchain_file(name), context], timeout=3600)
    return image


def prune_toolchain(name, out=print):
    """Remove the images of a toolchain other than the one of its file as it is now (older files',
    and the untagged name of before 2026-10-21); returns their names. An image a worktree still uses
    is built again when that worktree needs it."""
    keep = toolchain_image(name)
    r = subprocess.run(["podman", "images", "--format", "{{.Repository}}:{{.Tag}}", IMAGE_PREFIX + name],
                       capture_output=True, text=True)
    old = [i for i in r.stdout.split() if i != keep and i.startswith(IMAGE_PREFIX + name + ":")]
    for image in old:
        out("toolchain %s: removing %s" % (name, image))
        run(["podman", "rmi", image])
    return old


def in_container(cmd, image, mounts, cwd):
    """cmd as podman runs it in image: each (directory, read_only) of mounts at its own path, the
    working directory cwd, no network (rootless podman leaves what it writes the user's)."""
    argv = ["podman", "run", "--rm", "--network=none"]
    for path, read_only in mounts:
        argv += ["-v", "%s:%s%s" % (path, path, ":ro" if read_only else "")]
    return argv + ["-w", cwd, image] + list(cmd)


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


def home(flags):
    """Flags with ~ expanded where a path starts (-I~/..., -L~/..., ~/...): a toolchain's files that
    lie in the user's home (TI's MSP430 support files)."""
    out = []
    for f in flags:
        for prefix in ("-I", "-L", "-T", "--sysroot=", ""):
            if f.startswith(prefix + "~"):
                f = prefix + os.path.expanduser(f[len(prefix):])
                break
        out.append(f)
    return out


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
    if "dread_ldflags" in b:                # (TI's MSP430 binutils predate --no-warn-rwx-segments)
        lines.append("ldflags = [%s]" % ", ".join(json.dumps(f) for f in b["dread_ldflags"]))
    return "[compiler]\n" + "\n".join(lines) + "\n"


def made_from(recipe, name):
    """A digest of what in the recipe makes a build (the pinned source, its project with the
    target's models and scenarios, the build's own entry): build.json keeps it, and measure() builds
    again when it changed since."""
    b = the_build(recipe, name)
    made = {"source": recipe.get("source"), "project": recipe["projects"][b["project"]], "build": b}
    if b.get("toolchain"):
        made["toolchain"] = toolchain_digest(b["toolchain"])
    blob = json.dumps(made, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def compiling(recipe, name, work, bench=None, out=print):
    """How a build compiles its sources: (src, {source: the file compiled}, flags, tool), tool(cmd)
    the command as it runs (on the host, or in the toolchain's container with the source read-only
    and the build's directory writable). The patched copies go to work (build: BUILD/work)."""
    b = the_build(recipe, name)
    project = recipe["projects"][b["project"]]
    src = fetch(recipe, bench, out)
    where = build_dir(recipe, name, bench)
    files, first = patched_sources(project, src, work, b.get("patches", ()))
    flags = home(b["cflags"]) + ["-D" + d for d in project.get("defines", ())] + ["-I" + d for d in first] + \
        ["-I" + os.path.join(src, i) for i in project.get("includes", ())]
    image = ensure_toolchain(b["toolchain"], out=out) if b.get("toolchain") else None

    def tool(cmd):
        return in_container(cmd, image, [(src, True), (where, False)], src) if image else cmd
    return src, files, flags, tool


def build(recipe, name, bench=None, jobs=None, out=print):
    """Compile and link one build, make its DREAD target and import the ELF; returns the target
    directory."""
    b = the_build(recipe, name)
    project = recipe["projects"][b["project"]]
    where = build_dir(recipe, name, bench)
    work = os.path.join(where, "work")
    objdir = os.path.join(work, "obj")
    os.makedirs(objdir, exist_ok=True)
    src, files, flags, tool = compiling(recipe, name, work, bench, out)
    cc, ld = b["cc"], b.get("ld", b["cc"])
    version = run(tool([cc, "--version"]), cwd=src).strip().splitlines()[0]
    started = time.time()
    objects = []

    def compile_one(item):
        source, path = item
        obj = os.path.join(objdir, source.replace("/", "__") + ".o")
        run(tool([cc] + flags + ["-c", path, "-o", obj]), cwd=src)
        return obj
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs or os.cpu_count() or 4) as pool:
        objects = list(pool.map(compile_one, sorted(files.items())))
    elf = os.path.join(where, "firmware.elf")
    script = ["-T", os.path.join(src, project["ldscript"])] if project.get("ldscript") else []
    link = b["link"] if "link" in b else project.get("link", ())      # (a build of another C library)
    run(tool([ld] + home(b.get("lflags", b["cflags"])) + script + ["-Wl,-Map=" + os.path.join(work, "firmware.map"),
                                                                   "-o", elf] + objects + home(link)),
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
                   "toolchain": b.get("toolchain"), "version": version,
                   "cflags": b["cflags"], "sources": len(files), "seconds": round(seconds, 1),
                   "made_from": made_from(recipe, name)}, f, indent=1)
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
    try:
        with open(os.path.join(where, "build.json")) as f:
            known = json.load(f).get("made_from")
    except (OSError, ValueError):
        known = None
    if known != made_from(recipe, name):        # (five builds were once measured without the models
        out("%s: the recipe changed since it was built; building it again" % name)  # they lacked)
        build(recipe, name, bench, out=out)
    target = Target.load(where)
    image = target.image()
    program = Program(target, image)
    metrics = {}
    previous = os.path.join(where, "metrics.json")
    if os.path.exists(previous):                # the steps not run now keep their figures
        with open(previous) as f:
            metrics = {k: v for k, v in json.load(f).items() if k in STEPS}
    earlier = metrics.get("lift", {}).get("accepted")
    earlier_readable = metrics.get("readable", {}).get("accepted")
    metrics.update({"build": name, "functions": len(program.functions),
                    "instruction_bytes": sum(e - s for s, e, _ in program.ranges),
                    "image_bytes": len(image.payload())})
    with open(os.path.join(where, "build.json")) as f:
        info = json.load(f)
    metrics.update(cc=info["cc"], cflags=" ".join(info["cflags"]), toolchain=info.get("toolchain"),
                   version=info.get("version"))
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
    if "readable" in steps:             # (after the lift check and the recorded cases: the gate builds on them)
        metrics["readable"] = readable_step(recipe, name, target, image, where, bench, out)
        if earlier_readable is not None and metrics["readable"].get("ran"):
            now = set(metrics["readable"]["accepted"])
            metrics["readable"]["against_last"] = {"gained": sorted(now - set(earlier_readable)),
                                                   "lost": sorted(set(earlier_readable) - now)}
    if "noreturn" in steps:
        seconds, code, text = dread("noreturn", "--target", where, log=os.path.join(where, "work", "noreturn.log"))
        metrics["noreturn"] = {"functions": len([l for l in text.splitlines() if l.strip()]) if code == 0 else None,
                               "seconds": round(seconds, 1)}
    if "callconv" in steps:                # (after the lift check: its cases are what it measures with)
        found = os.path.join(where, "work", "callconv.json")
        seconds, code, text = dread("callconv", "--target", where, "--json", found,
                                    log=os.path.join(where, "work", "callconv.log"))
        if code == 0 and os.path.exists(found):
            with open(found) as f:
                data = json.load(f)
            s = data["summary"]
            metrics["callconv"] = {"ran": True, "measured": s["measured"], "agree": s["agree"],
                                   "functions": s["functions"], "seconds": round(seconds, 1),
                                   "disagree": {n: r["found"] for n, r in data["functions"].items() if r["found"]}}
        else:
            metrics["callconv"] = {"ran": False, "seconds": round(seconds, 1)}
    if "whatcc" in steps:
        found = os.path.join(where, "work", "whatcc.json")
        seconds, code, text = dread("whatcc", "--target", where, "--json", found,
                                    log=os.path.join(where, "work", "whatcc.log"))
        try:
            with open(os.path.join(where, "build.json")) as f:
                truth = json.load(f).get("toolchain") or "host"
        except (OSError, ValueError):
            truth = None
        if code == 0 and os.path.exists(found):
            with open(found) as f:
                s = json.load(f)
            metrics["whatcc"] = {"ran": True, "truth": truth, "best": s["best"], "right": truth in s["best"],
                                 "matched": s["matched"], "sure": s["sure"], "agree": s["agree"],
                                 "seconds": round(seconds, 1)}
        else:
            metrics["whatcc"] = {"ran": False, "seconds": round(seconds, 1)}
    if "groups" in steps:
        metrics["groups"] = groups_step(target, image, program, where)
    if "lift" in steps and earlier is not None and "accepted" in metrics.get("lift", {}):
        now = set(metrics["lift"]["accepted"])
        metrics["lift"]["against_last"] = {"gained": sorted(now - set(earlier)), "lost": sorted(set(earlier) - now)}
    if os.path.exists(previous):
        os.makedirs(os.path.join(where, "history"), exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(os.path.getmtime(previous)))
        shutil.copy2(previous, os.path.join(where, "history", "metrics-%s.json" % stamp))
    with open(os.path.join(where, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=1, sort_keys=True)
    out("%s: %s" % (name, summary_line(metrics)))
    against = metrics.get("lift", {}).get("against_last") if "lift" in steps else None
    if against and (against["gained"] or against["lost"]):
        out("%s: against the last measurement, gained %s; lost %s" % (
            name, ", ".join(against["gained"]) or "none", ", ".join(against["lost"]) or "none"))
    return metrics


def groups_step(target, image, program, where):
    """dread groups on the build, scored against the source files of its ELF's debug information
    (dread.groups score_groups); {"ran": False, "why"} when it fails."""
    from dread import groups
    started = time.time()
    try:
        found = groups.find(target, image, program)
        totals, _ = groups.score_groups(program, found, os.path.join(where, "firmware.elf"))
    except Exception as e:                  # (a build whose analyses fail: said, not fatal to the rest)
        return {"ran": False, "why": str(e), "seconds": round(time.time() - started, 1)}
    return dict(totals, ran=True, seconds=round(time.time() - started, 1))


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
    verdicts, reasons, accepted = {}, {}, []
    for fn, verdict, reason in verdicts_of(target, image):
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        if verdict != "accepted":
            key = verdict + ": " + reason_kind(reason)
            reasons[key] = reasons.get(key, 0) + 1
        else:
            accepted.append(fn)
    return {"ran": code == 0, "verdicts": verdicts, "reasons": reasons, "accepted": sorted(accepted),
            "seconds": round(seconds, 1)}


def record_step(target, image, where):
    """dread sysrecord in every scenario of the target for the functions the last lift check left
    uncovered, got no inputs for, or dropped as task loops whose loop cases left instructions unrun
    (RECORDABLE): {functions, kept, seconds} (kept: the cases kept, which add instructions; ran: every
    scenario's recording ran, whatever it kept), or {"ran": False} without scenarios or verdicts. Until
    2026-10-26 the step took no task loop, counted no loop's kept cases (their lines said "N cases
    kept;") and took sysrecord's exit status 1 for "nothing kept" as a failure: the measurements
    recorded nothing for the tasks, and the report showed "-" at every build."""
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


# ---- the readable step

NOT_TURNED = ("accepted by the system run", "no longer lifts", "its lifted C is not the dialect")
WORST = 10                  # functions in each of the readable step's lists of the worst
WORST_LEAST = 5             # ... of those with at least this many statements in the source (smaller ones
                            # make large ratios of a few statements)
CODE = (".py", ".c", ".h", ".java")     # DREAD's files the gate's digest reads,
MEASURING = ("dread/bench.py", "dread/cmetrics.py")     # but those that only measure what the gate made


def readable_reason(reason):
    """Why the readable gate left a function lifted (a reason of its verdicts.tsv), as a category
    across builds; the drops by what stopped them as the lift check's (reason_category)."""
    for start, category in (("accepted by the system run; the whole image differs",
                             "the whole image differs with its readable C"),
                            ("accepted by the system run: the target has no scenarios",
                             "accepted by the system run (no scenarios to try it in)"),
                            ("accepted by the system run: the whole image differs without",
                             "accepted by the system run (the scenarios decide nothing)"),
                            ("accepted by the system run", "accepted by the system run (not checked there yet)"),
                            ("no longer lifts", "no longer lifts"),
                            ("its lifted C is not the dialect", "its lifted C is not the dialect dread.cparse reads"),
                            ("the overlay: no room", "no room for its readable C"),
                            ("the overlay", "the overlay refused its readable C"),
                            ("its readable C does not compile", "its readable C does not compile"),
                            ("the whole image differs", "the whole image differs with its readable C")):
        if reason.startswith(start):
            return category
    if "which the original does not call" in reason:
        return "its readable C calls what the original does not"
    if reason.startswith(("FAIL", "UNCOVERED", "never returns")):
        return reason_category("dropped: " + reason_kind(reason))
    return reason_kind(reason)


def gate_digest(target, image):
    """A digest of what dread readable --check decides from: DREAD's code, the build's description
    (dread.toml), its image, its tables (but the gate's own: the readable and readable_types tables)
    and the cases it keeps. The readable step runs the gate again when this changed (a new lifted
    table, a change of DREAD's) and takes its last results otherwise. The modules that only measure
    (MEASURING: this one and dread.cmetrics, which the gate does not import) are left out, so that a
    change of the figures does not run every gate again."""
    h = hashlib.sha1()
    skip = {os.path.abspath(image.table("readable")), os.path.abspath(image.table("readable_types"))} | \
        {os.path.abspath(os.path.join(ROOT, m)) for m in MEASURING}

    def tree(root, only=None):
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for f in sorted(files):
                path = os.path.join(dirpath, f)
                if os.path.abspath(path) in skip or (only and not f.endswith(only)):
                    continue
                h.update(os.path.relpath(path, root).encode() + b"\0")
                with open(path, "rb") as fh:
                    h.update(hashlib.sha1(fh.read()).digest())
    tree(os.path.join(ROOT, "dread"), CODE)
    for part in (image.config, image.extra_cases, image.kept_cases):
        tree(part)
    h.update(image.payload())
    with open(target.file, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def source_target(b):
    """clang's --target for reading a build's preprocessed C: a clang build's own, else the GNU
    prefix of its compiler (arm-none-eabi-gcc: arm-none-eabi), 32-bit where a riscv64 compiler builds
    for RV32 (-march=rv32..., -mabi=ilp32...). [] for a compiler without a prefix (the host's)."""
    for f in b["cflags"]:
        if f.startswith("--target="):
            return [f]
    cc = os.path.basename(b["cc"])
    prefix = cc[:-len("gcc")].rstrip("-") if cc.endswith("gcc") else ""
    if prefix.startswith("riscv64") and any(f.startswith(("-march=rv32", "-mabi=ilp32")) for f in b["cflags"]):
        prefix = "riscv32" + prefix[len("riscv64"):]
    return ["--target=" + prefix] if prefix else []


def c_standard(macros):
    """clang's -std for the C a compiler preprocessed, from its predefined macros (cc -dM -E): GCC 15
    on default to C23, whose headers clang's default (C17) does not read (stddef.h's nullptr)."""
    import re
    m = re.search(r"#define __STDC_VERSION__ (\d+)L", macros)
    version = int(m.group(1)) if m else 0
    return "-std=" + ("gnu2x" if version > 201710 else "gnu17" if version == 201710 else
                      "gnu11" if version >= 201112 else "gnu99" if version >= 199901 else "gnu89")


def source_side(recipe, name, bench=None, out=print, jobs=None):
    """The figures (dread.cmetrics.count) of the functions the build's C sources define, as compiled:
    each preprocessed by the build's compiler with its flags (in its container, for a toolchain's
    build), then read by the host's clang (-Xclang -ast-dump=json) for the build's target and C
    standard. Why clang: it reads C with every GNU extension the sources and their libraries' headers
    use (extended asm with operands, attributes, statement expressions), which pycparser does not, and
    the preprocessed text is the C the compiler compiled, macros expanded. {"ran": True, "functions":
    {source: {name: figures}}, "errors": {source: [clang's errors]}, "skipped": [sources not C],
    "files": n, "seconds"}, or {"ran": False, "why"}."""
    from dread import cmetrics
    from dread.util.polite import workers
    if shutil.which("clang") is None:
        return {"ran": False, "why": "no clang on this machine"}
    started = time.time()
    b = the_build(recipe, name)
    where = build_dir(recipe, name, bench)
    work = os.path.join(where, "work", "readable-src")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    src, files, flags, tool = compiling(recipe, name, work, bench, out)
    try:
        macros = run(tool([b["cc"]] + flags + ["-dM", "-E", "-x", "c", "/dev/null"]), cwd=src)
    except BenchError as e:
        return {"ran": False, "why": "the compiler's macros: %s" % str(e)[:200]}
    clang_flags = source_target(b) + [c_standard(macros)]
    sources = sorted(s for s in files if s.endswith(".c"))

    def one(source):
        i_file = os.path.join(work, source.replace("/", "__") + ".i")
        try:
            run(tool([b["cc"]] + flags + ["-E", files[source], "-o", i_file]), cwd=src)
        except BenchError as e:
            return source, {}, ["preprocessing failed: %s" % str(e)[-200:]]
        ast, errors = cmetrics.clang_ast(i_file, clang_flags)
        os.remove(i_file)
        return source, cmetrics.source_functions(ast), errors
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs or workers()) as pool:
        got = list(pool.map(one, sources))
    shutil.rmtree(work, ignore_errors=True)
    return {"ran": True, "functions": {s: fns for s, fns, _ in got}, "errors": {s: e for s, _, e in got if e},
            "skipped": sorted(s for s in files if s not in sources), "files": len(sources), "flags": clang_flags,
            "seconds": round(time.time() - started, 1)}


def translation_unit(path, sources):
    """The source (a path relative to the source's root) whose translation unit DWARF names path, or
    None: the path itself or a copy of it (a patched copy, a build moved: matched by the end)."""
    if not path:
        return None
    path = path.replace(os.sep, "/")
    found = [s for s in sources if path == s or path.endswith("/" + s)]
    return max(found, key=len) if found else None


def readable_step(recipe, name, target, image, where, bench=None, out=print):
    """The readable gate and what its readable C looks like, against the source (see the module's
    docstring); {"ran": False, "why"} without a lifted table."""
    from dread import build as dbuild, cmetrics
    from dread.program import Program
    started = time.time()
    lifted = dbuild.read_names(image.table("lifted")) if os.path.exists(image.table("lifted")) else []
    if not lifted:
        return {"ran": False, "why": "no lifted table (the lift step first)"}
    work = os.path.join(where, "work")
    os.makedirs(work, exist_ok=True)
    stamp = os.path.join(work, "readable-gate.json")
    verdicts_path = os.path.join(target.build, "readable-check", image.name, "verdicts.tsv")
    digest = gate_digest(target, image)
    try:
        with open(stamp) as f:
            last = json.load(f)
    except (OSError, ValueError):
        last = {}
    reused = last.get("digest") == digest and all(os.path.exists(p) for p in (
        image.table("readable"), image.table("readable_types"), verdicts_path))     # (the gate's outputs)
    if reused:
        gate_seconds = last.get("seconds")
    else:
        if os.path.exists(stamp):
            os.remove(stamp)
        gate_seconds, code, _ = dread("readable", "--target", where, "--check", log=os.path.join(work, "readable.log"))
        if code or not os.path.exists(verdicts_path):
            return {"ran": False, "why": "dread readable --check failed (work/readable.log)",
                    "seconds": round(time.time() - started, 1)}
        gate_seconds = round(gate_seconds, 1)
        with open(stamp, "w") as f:
            json.dump({"digest": digest, "seconds": gate_seconds}, f)
    gate = {}
    with open(verdicts_path) as f:
        next(f)
        for line in f:
            fn, verdict, reason = (line.rstrip("\n").split("\t") + ["", ""])[:3]
            gate[fn] = (verdict, reason)
    in_table = set(dbuild.read_names(image.table("readable")))
    accepted = sorted(n for n in lifted if n in in_table)
    stay, turned = {}, 0
    for fn in lifted:
        verdict, reason = gate.get(fn, ("lifted", "not checked by the gate"))
        if fn in in_table:
            turned += 1
            continue
        stay[readable_reason(reason)] = stay.get(readable_reason(reason), 0) + 1
        if fn in gate and not reason.startswith(NOT_TURNED):
            turned += 1                     # (its readable C was built and tried: the gate refused it)
    # the figures of the accepted readable C (and of the same functions' lifted C)
    figures_file = os.path.join(work, "readable-figures.json")
    fig_seconds, code, _ = dread("readable", "--target", where, "--figures", figures_file,
                                 log=os.path.join(work, "readable-figures.log"))
    figs = {}
    if code == 0:
        with open(figures_file) as f:
            figs = json.load(f)
    rows = {n: dict(figs[n]["readable"], lifted_statements=figs[n]["lifted"]["statements"],
                    lifted_gotos=figs[n]["lifted"]["gotos"], kept_lifted=figs[n]["kept_lifted"])
            for n in accepted if n in figs}
    result = {"ran": True, "lifted": len(lifted), "turned": turned, "accepted": accepted,
              "stay_lifted": dict(sorted(stay.items(), key=lambda kv: (-kv[1], kv[0]))),
              "gate": "reused" if reused else "ran", "measured": len(rows),
              "kept_lifted": sum(1 for r in rows.values() if r["kept_lifted"])}
    if code:
        result["figures_failed"] = "dread readable --figures failed (work/readable-figures.log)"
    for key in cmetrics.READABLE + ("lifted_statements", "lifted_gotos"):
        values = [r[key] for r in rows.values()]
        result[key] = {"sum": sum(values), "median": cmetrics.median(values), "max": max(values, default=0),
                       "functions": sum(1 for v in values if v)}
    # against the source: the function DWARF says each one is, its figures from clang's tree
    elf = os.path.join(where, "firmware.elf")
    result["source"] = {"ran": False, "why": "no readable function measured"}
    if rows:
        try:
            dwarf = cmetrics.out_of_line(elf)
        except ImportError:
            dwarf = None
            result["source"] = {"ran": False, "why": "pyelftools is missing (pip install -e '.[dwarf]')"}
        if dwarf == {}:
            result["source"] = {"ran": False, "why": "the ELF has no debug information"}
        elif dwarf:
            source = source_side(recipe, name, bench, out)
            result["source"] = source
            if source.get("ran"):
                result["source"] = against_source(rows, source, dwarf, Program(target, image), elf)
                result["source"].update(ran=True, files=source["files"], seconds=source["seconds"],
                                        flags=source["flags"], skipped=source["skipped"],
                                        errors={s: e[:3] for s, e in source["errors"].items()})
    write_readable_table(os.path.join(work, "readable.tsv"), rows)
    result["seconds"] = round(time.time() - started, 1)
    result["seconds_gate"] = gate_seconds
    result["seconds_figures"] = round(fig_seconds, 1)
    return result


MATCHES = ("matched", "part", "no debug information at its entry", "not the project's sources",
           "not in the source's tree")


def against_source(rows, source, dwarf, program, elf_path):
    """rows (name: figures) joined to their source functions; each row gains match (MATCHES), source,
    unit, src_statements, src_depth, src_gotos, src_labels, src_loops, src_inlined (the source's
    statements with those of the functions inlined into it, each inlined instance counted: a function
    inlined from another unit, which only link-time optimisation does, counts nothing), inlined
    (their names); the matched ones (not .part) ratio, ratio_inlined and ratio_depth. Returns the
    summary for metrics.json: counts by match, quartiles of the ratios, sums and medians of the matched
    ones' figures on both sides, and the worst lists (WORST functions of WORST_LEAST source statements
    or more by statements against the source with its inlined functions; WORST deeper than the
    source, most levels deeper first)."""
    from dread import cmetrics
    from dread.elf import Elf, STT_FUNC
    entries = {fn.name: fn.entry for fn in program.functions}
    symbols = {}
    for s in Elf(elf_path).symbols:
        if s.kind == STT_FUNC and s.name and s.shndx:
            symbols.setdefault(s.value & ~1, s.name)
    units = source["functions"]
    unmatched = {}
    for name, row in sorted(rows.items()):
        d = dwarf.get(entries.get(name, -1) & ~1)
        unit = translation_unit(d["cu"], units) if d else None
        defined = units.get(unit, {})
        if d is None:
            row["match"] = MATCHES[2]
        elif unit is None:
            row["match"] = MATCHES[3]
        elif d["name"] not in defined:
            row["match"] = MATCHES[4]
        else:
            symbol = symbols.get(entries[name] & ~1, "")
            row["match"] = MATCHES[1] if ".part." in symbol else MATCHES[0]
            mine = defined[d["name"]]
            row.update(source=d["name"], unit=unit, src_statements=mine["statements"], src_depth=mine["depth"],
                       src_gotos=mine["gotos"], src_labels=mine["labels"], src_loops=mine["loops"],
                       inlined=d["inlined"],
                       src_inlined=mine["statements"] + sum(defined.get(i, {}).get("statements", 0)
                                                            for i in d["inlined"]))
        if row["match"] not in MATCHES[:2]:
            unmatched.setdefault(row["match"], []).append(name)
    whole = [(n, r) for n, r in sorted(rows.items()) if r["match"] == MATCHES[0]]

    def ratio(a, b):
        return round(a / b, 3) if b else None
    for n, r in whole:
        r["ratio"] = ratio(r["statements"], r["src_statements"])
        r["ratio_inlined"] = ratio(r["statements"], r["src_inlined"])
        r["ratio_depth"] = ratio(r["depth"], r["src_depth"])
    out = {"matched": len(whole), "part": sum(1 for r in rows.values() if r["match"] == MATCHES[1]),
           "unmatched": {k: {"count": len(v), "names": v[:WORST]} for k, v in sorted(unmatched.items())}}
    for key in ("ratio", "ratio_inlined", "ratio_depth"):
        q = cmetrics.quartiles([r[key] for _, r in whole if r[key] is not None])
        out[key] = [round(x, 2) for x in q] if q else None
    for key in ("statements", "src_statements", "src_inlined", "depth", "src_depth", "gotos", "src_gotos", "loops",
                "src_loops"):
        out["sum_" + key] = sum(r[key] for _, r in whole)
        out["median_" + key] = cmetrics.median([r[key] for _, r in whole])
    out["deeper"] = sum(1 for _, r in whole if r["depth"] > r["src_depth"])
    out["shallower"] = sum(1 for _, r in whole if r["depth"] < r["src_depth"])
    out["inlining"] = sum(1 for _, r in whole if r["inlined"])

    def entry(n, r):
        return {"name": n, "source": r["source"], "unit": r["unit"], "statements": r["statements"],
                "src_statements": r["src_statements"], "src_inlined": r["src_inlined"], "depth": r["depth"],
                "src_depth": r["src_depth"], "gotos": r["gotos"], "loops": r["loops"], "src_loops": r["src_loops"],
                "inlined": sorted(set(r["inlined"])), "inlined_instances": len(r["inlined"])}
    big = [(n, r) for n, r in whole if r["src_statements"] >= WORST_LEAST]
    out["worst_larger"] = [entry(n, r) for n, r in sorted(
        big, key=lambda nr: (-(nr[1]["ratio_inlined"] or 0), -nr[1]["statements"], nr[0]))[:WORST]]
    out["worst_deeper"] = [entry(n, r) for n, r in sorted(
        (nr for nr in whole if nr[1]["depth"] > nr[1]["src_depth"]),
        key=lambda nr: (-(nr[1]["depth"] - nr[1]["src_depth"]), -nr[1]["statements"], nr[0]))[:WORST]]
    return out


TABLE_COLUMNS = ("statements", "depth", "gotos", "labels", "loops", "casts", "raw", "frame_raw", "members",
                 "frame_members", "fixed", "fixed_members", "variables", "typed", "lifted_statements", "lifted_gotos",
                 "kept_lifted",
                 "match", "source", "unit", "src_statements", "src_depth", "src_gotos", "src_loops", "src_inlined",
                 "ratio", "ratio_inlined", "ratio_depth", "inlined", "fixed_indexed", "fixed_addresses", "pointers",
                 "signed")


def write_readable_table(path, rows):
    """BUILD/work/readable.tsv: one line per readable function, its figures (TABLE_COLUMNS; - where
    there is none)."""
    def cell(v):
        if v is None:
            return "-"
        if isinstance(v, bool):
            return "yes" if v else "no"
        if isinstance(v, list):             # (the inlined functions: each once)
            return ",".join(sorted(set(v))) or "-"
        return str(v)
    with open(path, "w") as f:
        f.write("function\t" + "\t".join(TABLE_COLUMNS) + "\n")
        for name, row in sorted(rows.items()):
            f.write(name + "\t" + "\t".join(cell(row.get(c)) for c in TABLE_COLUMNS) + "\n")


def reason_kind(reason):
    """A refusal's reason without its particulars (registers, addresses, numbers), for counting."""
    import re
    text = re.sub(r"0x[0-9a-fA-F]+|\b\d+\b", "N", reason)
    text = re.sub(r"\br\d+\b|\b[as]\d+\b|\bt\d\b", "REG", text)
    return text[:90]


CATEGORIES = ("cases leave instructions unrun", "never returns, not all of it run", "a case differs",
              "no generated input ends")


def reason_category(key):
    """A reasons key ("verdict: kind", lift_step) as a category across builds: the drops by what
    stopped them (CATEGORIES), a refusal by its kind with the functions' names gone."""
    import re
    verdict, _, text = key.partition(": ")
    if text.startswith("UNCOVERED"):
        return CATEGORIES[0]
    if text.startswith("never returns"):
        return CATEGORIES[1]
    if "[case generation]" in text:
        return CATEGORIES[3]
    if text.startswith("FAIL"):
        return CATEGORIES[2]
    text = re.sub(r"\b(call of|calls|into|of|after|behind) (?!REG\b)([A-Za-z_][A-Za-z0-9_]*[A-Z_0-9][A-Za-z0-9_]*)",
                  lambda m: m.group(1) + " F", text)
    return verdict + ": " + re.sub(r"\s*\(.*$", "", text).strip()[:80]


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
        against = m["lift"].get("against_last")
        if against:
            parts[-1] += " (+%d -%d against the last measurement)" % (len(against["gained"]), len(against["lost"]))
    r = m.get("record", {})
    if r.get("kept"):
        parts.append("%d recorded cases (%d before them)" % (r["kept"], r.get("accepted_before") or 0))
    rd = m.get("readable", {})
    if rd.get("ran"):
        parts.append("readable %d of %d lifted (gotos %d, statements against the source's: %s)" % (
            len(rd["accepted"]), rd["lifted"], rd["gotos"]["sum"],
            "median %.2f" % rd["source"]["ratio"][1] if (rd.get("source") or {}).get("ratio") else "not measured"))
    c = m.get("callconv", {})
    if c.get("ran"):
        parts.append("registers measured: %d of %d disagree with regflow" % (c["measured"] - c["agree"], c["measured"]))
    return ", ".join(parts)


def readable_report(rows):
    """The report's lines on the readable step: the tables across builds, then each build's worst
    functions and why the rest stays lifted."""
    measured = [m for m in rows if m.get("readable", {}).get("ran")]
    if not measured:
        return []

    def fig(rd, key, median=False):
        f = rd.get(key) or {}
        return ("%d" % f.get("sum", 0)) + (" (%g)" % f["median"] if median and f.get("median") is not None else "")

    def quart(q):
        return "%.2f (%.2f-%.2f)" % (q[1], q[0], q[2]) if q else "-"
    lines = ["", "## Readable C (dread readable --check, figures by dread.cmetrics)", "",
             "Sums over the readable functions, medians per function in parentheses; raw: accesses through a "
             "pointer cast (of them through the frame's base); members: p->f (the frame's object's); fixed-address "
             "names: dread.naming's macros (members of structures and elements of arrays of static data, g_20000030.f_4, "
             "g_20000c74[1]: of them with an index, g_200299f8[r3].f_4; addresses written as such, &g_20029988); typed: "
             "variables of a structure or a pointer to one.", "",
             "| build | lifted | turned readable | readable | stay lifted | gotos left (functions) | labels | "
             "statements | depth | casts | raw (frame) | members (frame) | fixed-address names (static members: "
             "indexed; addresses) | "
             "typed of variables | "
             "lifted C: statements, gotos | seconds (gate, figures, source) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in measured:
        rd = m["readable"]
        stay = rd.get("stay_lifted", {})
        lines.append("| %s | %d | %d | %d | %s | %d (%d) | %s | %s | %s (max %d) | %s | %d (%d) | %d (%d) | %d (%d: %d; %d) "
                     "| %d of %d "
                     "| %d, %d | %s, %s, %s |" % (
                         m["build"], rd["lifted"], rd["turned"], len(rd["accepted"]),
                         "; ".join("%d %s" % (n, k) for k, n in stay.items()) or "-",
                         rd["gotos"]["sum"], rd["gotos"]["functions"], fig(rd, "labels"), fig(rd, "statements", True),
                         "%g" % (rd["depth"]["median"] or 0), rd["depth"]["max"], fig(rd, "casts"),
                         rd["raw"]["sum"], rd["frame_raw"]["sum"], rd["members"]["sum"], rd["frame_members"]["sum"],
                         rd["fixed"]["sum"], (rd.get("fixed_members") or {}).get("sum", 0),
                         (rd.get("fixed_indexed") or {}).get("sum", 0), (rd.get("fixed_addresses") or {}).get("sum", 0),
                         rd["typed"]["sum"],
                         rd["variables"]["sum"],
                         rd["lifted_statements"]["sum"], rd["lifted_gotos"]["sum"],
                         "reused" if rd.get("gate") == "reused" else rd.get("seconds_gate", "-"),
                         rd.get("seconds_figures", "-"),
                         (rd.get("source") or {}).get("seconds", "-")))
    lines += ["", "### Readable C against the source", "",
              "Each readable function matched by its entry to the function DWARF names (a clone's source "
              "function), counted on clang's tree of the preprocessed source; ratios readable / source per "
              "function, median (quartiles); with inlined: the source's statements and those of every function "
              "DWARF says was inlined into it; depth only where the source nests.", "",
              "| build | readable | matched | part (.part) | unmatched | statements readable / source | with inlined "
              "| depth readable / source | deeper, shallower | statements: readable, source, with inlined | "
              "gotos: readable, source | loops: readable, source |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in measured:
        rd, s = m["readable"], m["readable"].get("source") or {}
        if "matched" not in s:
            lines.append("| %s | %d | %s | | | | | | | | | |" % (m["build"], len(rd["accepted"]), s.get("why", "-")))
            continue
        lines.append("| %s | %d | %d | %d | %s | %s | %s | %s | %d, %d | %d, %d, %d | %d, %d | %d, %d |" % (
            m["build"], rd["measured"], s["matched"], s["part"],
            "; ".join("%d %s" % (u["count"], k) for k, u in s["unmatched"].items()) or "-",
            quart(s["ratio"]), quart(s["ratio_inlined"]), quart(s["ratio_depth"]), s["deeper"], s["shallower"],
            s["sum_statements"], s["sum_src_statements"], s["sum_src_inlined"], s["sum_gotos"], s["sum_src_gotos"],
            s.get("sum_loops", 0), s.get("sum_src_loops", 0)))
    for m in measured:
        s = m["readable"].get("source") or {}
        for key, title in (("worst_larger", "much larger than the source (against the source with its inlined "
                            "functions)"), ("worst_deeper", "deeper than the source")):
            if s.get(key):
                lines += ["", "#### %s: readable C %s" % (m["build"], title), ""]
                lines += ["- %s%s (%s): %d statements against %d%s, depth %d against %d, loops %d against %d%s" % (
                    w["name"], "" if w["name"] == w["source"] else " = " + w["source"], w["unit"], w["statements"],
                    w["src_statements"], " (%d with %d inlined: %s)" % (
                        w["src_inlined"], w["inlined_instances"], ", ".join(w["inlined"][:4]) +
                        (" ..." if len(w["inlined"]) > 4 else "")) if w["inlined"] else "",
                    w["depth"], w["src_depth"], w.get("loops", 0), w.get("src_loops", 0),
                    ", %d goto%s" % (w["gotos"], "s" if w["gotos"] > 1 else "") if w["gotos"] else "")
                    for w in s[key]]
    return lines


def compiler_cell(m):
    """The report's compiler column: the version the compiler gave, and the toolchain's image."""
    text = m.get("version") or os.path.basename(m.get("cc", ""))
    return text + (" (%s)" % m["toolchain"] if m.get("toolchain") else "")


def stops(m):
    """What stops the rest of a build's functions (the report's table "What stops the rest"): ({category:
    count} by reason_category, {refusal category: count}, the refusal category most functions have as
    (category, count), or None)."""
    counts = {}
    for key, n in (m.get("lift") or {}).get("reasons", {}).items():
        counts[reason_category(key)] = counts.get(reason_category(key), 0) + n
    refused = {k: n for k, n in counts.items() if k.startswith("refused: ")}
    top = max(refused.items(), key=lambda kv: kv[1]) if refused else None
    return counts, refused, top


# ---- the report as data (report --json) ----

JSON_VERSION = 1
FIGURES = (     # (key, title, unit): a measurement's figures, one number (or yes/no) each (figures())
    ("functions", "functions", "count"),
    ("instruction_bytes", "code bytes", "bytes"),
    ("listing_identical", "the listing rebuilds the image byte for byte", "flag"),
    ("raw_words", "raw words in the listing", "count"),
    ("ghidra_found", "functions Ghidra found without the symbols", "count"),
    ("ghidra_missed", "functions Ghidra missed", "count"),
    ("ghidra_extra", "starts Ghidra found that are no function", "count"),
    ("lifted", "functions lifted (dread lift --check)", "count"),
    ("checked", "functions the lift check decided", "count"),
    ("lifted_share", "share lifted of those decided", "percent"),
    ("dropped", "dropped by the lift check", "count"),
    ("refused", "refused by the lifter", "count"),
    ("gained", "lifted, not lifted by the measurement before", "count"),
    ("lost", "lifted by the measurement before, not now", "count"),
    ("recorded", "recorded cases kept (dread sysrecord)", "count"),
    ("readable", "readable (dread readable --check)", "count"),
    ("readable_turned", "turned readable: built from readable C", "count"),
    ("readable_gotos", "gotos left in readable C", "count"),
    ("readable_statements", "statements of readable C", "count"),
    ("readable_ratio", "statements of readable C against the source's, median", "ratio"),
    ("noreturn", "functions that never return", "count"),
    ("registers_measured", "functions whose registers were measured (dread callconv)", "count"),
    ("registers_disagree", "of those, against regflow", "count"),
    ("whatcc_right", "the libraries' toolchain told right (dread whatcc)", "flag"),
    ("whatcc_matched", "library functions matched by their bytes", "count"),
    ("whatcc_sure", "library functions named as certain", "count"),
    ("whatcc_agree", "of those, the functions' own names", "count"),
    ("groups_ari", "groups against the source files: adjusted Rand index", "ratio"),
    ("groups_purity", "groups against the source files: purity", "ratio"),
    ("groups_completeness", "groups against the source files: completeness", "ratio"),
    ("seconds", "seconds, all steps", "seconds"),
) + tuple(("seconds_" + s, "seconds of the %s step" % s, "seconds") for s in STEPS)


def figures(m):
    """{key: value} of a measurement (a metrics.json, of any age) for FIGURES: what the report shows of it,
    a number or a yes/no each; a key is left out where its step did not run or the file predates it."""
    out = {}

    def put(key, value):
        if value is not None:
            out[key] = value
    put("functions", m.get("functions"))
    put("instruction_bytes", m.get("instruction_bytes"))
    a = m.get("asm") or {}
    if a:
        put("listing_identical", bool(a.get("identical")))
        put("raw_words", a.get("raw_words"))
    g = m.get("ghidra") or {}
    if g.get("ran"):
        for k in ("found", "missed", "extra"):
            put("ghidra_" + k, g.get(k))
    lf = m.get("lift") or {}
    v = lf.get("verdicts") or {}
    if v:
        total = sum(v.values())
        put("lifted", v.get("accepted", 0))
        put("checked", total)
        put("lifted_share", round(100.0 * v.get("accepted", 0) / max(1, total), 1))
        put("dropped", v.get("dropped", 0))
        put("refused", v.get("refused", 0))
        against = lf.get("against_last")
        if against:
            put("gained", len(against.get("gained", ())))
            put("lost", len(against.get("lost", ())))
    r = m.get("record") or {}
    if "kept" in r:
        put("recorded", r["kept"])
    rd = m.get("readable") or {}
    if rd.get("ran"):
        put("readable", len(rd.get("accepted") or ()))
        put("readable_turned", rd.get("turned"))
        put("readable_gotos", (rd.get("gotos") or {}).get("sum"))
        put("readable_statements", (rd.get("statements") or {}).get("sum"))
        ratio = (rd.get("source") or {}).get("ratio")
        if ratio:
            put("readable_ratio", ratio[1])
    put("noreturn", (m.get("noreturn") or {}).get("functions"))
    c = m.get("callconv") or {}
    if c.get("ran"):
        put("registers_measured", c.get("measured"))
        if c.get("measured") is not None and c.get("agree") is not None:
            put("registers_disagree", c["measured"] - c["agree"])
    w = m.get("whatcc") or {}
    if w.get("ran"):
        put("whatcc_right", bool(w.get("right")))
        for k in ("matched", "sure", "agree"):
            put("whatcc_" + k, w.get(k))
    gr = m.get("groups") or {}
    if gr.get("ran"):
        for k in ("ari", "purity", "completeness"):
            put("groups_" + k, gr.get(k))
    total = None
    for s in STEPS:
        seconds = (m.get(s) or {}).get("seconds") if isinstance(m.get(s), dict) else None
        if isinstance(seconds, (int, float)):
            put("seconds_" + s, seconds)
            total = (total or 0) + seconds
    if total is not None:
        put("seconds", round(total, 1))
    return out


def compiler_kind(cc):
    """gcc, clang, or the compiler's own name (a build's cc)."""
    name = os.path.basename(cc or "")
    return "clang" if "clang" in name else "gcc" if name.endswith("gcc") else name


def compiler_release(version):
    """The release in a compiler's version line (GCC's after its package's parentheses, clang's after
    "version": 4.9.3, 13.2.1, 16.0.6), or None: the first number with dots after a space, once what
    stands in parentheses is taken out."""
    text = re.sub(r"\([^)]*\)", " ", version or "")
    m = re.search(r"(?:^|\s)(\d+\.\d+(?:\.\d+)?)(?=$|[\s-])", text)
    return m.group(1) if m else None


def flag_value(flags, prefixes):
    """The value of the first flag with one of the prefixes (-mcpu=cortex-m3: cortex-m3), or None."""
    for f in flags:
        for p in prefixes:
            if f.startswith(p) and len(f) > len(p):
                return f[len(p):]
    return None


def history_of(where):
    """[(when, file relative to the build, metrics)] of a build's earlier measurements (BUILD/history,
    named by the time the measurement was written, in local time as measure() names them), oldest
    first; files that cannot be read are left out."""
    import glob
    out = []
    for path in sorted(glob.glob(os.path.join(where, "history", "metrics-*.json"))):
        stamp = os.path.basename(path)[len("metrics-"):-len(".json")]
        try:
            when = time.mktime(time.strptime(stamp, "%Y%m%d-%H%M%S"))
        except ValueError:
            when = os.path.getmtime(path)
        try:
            with open(path) as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(m, dict):
            out.append((when, os.path.relpath(path, where), m))
    return sorted(out, key=lambda t: (t[0], t[1]))


def report_data(recipe, bench=None):
    """The report as data (report --json, docs/formats.md): every measured build of the recipe in its
    order with what the report shows of it, and its earlier measurements' figures."""
    builds, missing = [], []
    for b in recipe["builds"]:
        name = b["name"]
        where = build_dir(recipe, name, bench)
        path = os.path.join(where, "metrics.json")
        try:
            with open(path) as f:
                m = json.load(f)
        except (OSError, ValueError):
            missing.append(name)
            continue
        try:
            with open(os.path.join(where, "build.json")) as f:
                info = json.load(f)
        except (OSError, ValueError):
            info = {}
        cflags = info.get("cflags") or (m.get("cflags") or "").split()
        counts, refused, top = stops(m)
        kept = json.loads(json.dumps(m))
        for step in ("lift", "readable"):         # (every function's name: the page shows counts)
            if isinstance(kept.get(step), dict):
                kept[step].pop("accepted", None)
        when = os.path.getmtime(path)
        history = [{"when": t, "file": f, "figures": figures(old)} for t, f, old in history_of(where)]
        history.append({"when": when, "file": "metrics.json", "figures": figures(m)})
        builds.append({
            "build": name, "project": b.get("project"), "directory": where, "target": os.path.exists(
                os.path.join(where, "dread.toml")), "when": when,
            "current": info.get("made_from") == made_from(recipe, name) if info.get("made_from") else None,
            "cc": m.get("cc") or b.get("cc"), "kind": compiler_kind(m.get("cc") or b.get("cc")),
            "version": m.get("version"), "release": compiler_release(m.get("version")),
            "toolchain": m.get("toolchain"), "compiler": compiler_cell(m), "cflags": " ".join(cflags),
            "optimisation": next((f for f in reversed(cflags) if re.fullmatch(r"-O\w*", f)), None),
            "cpu": flag_value(cflags, ("-mcpu=", "-march=", "-mmcu=")), "summary": summary_line(m),
            "figures": figures(m), "not_lifted": sum(n for v, n in ((m.get("lift") or {}).get("verdicts") or {}).items()
                                                     if v != "accepted"),
            "stops": {c: counts.get(c, 0) for c in CATEGORIES}, "refused": sum(refused.values()),
            "refused_most": {"category": top[0][len("refused: "):], "count": top[1]} if top else None,
            "metrics": kept, "history": history})
    return {"version": JSON_VERSION, "recipe": recipe["name"], "file": recipe["_file"],
            "bench": os.path.join(bench or bench_dir(), recipe["name"]),
            "figures": [{"key": k, "title": t, "unit": u} for k, t, u in FIGURES], "categories": list(CATEGORIES),
            "builds": builds, "missing": missing}


def write_json(data, path):
    """A JSON report written whole and renamed into place (a reader never sees half of it)."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(path + ".tmp", path)
    return path


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
             "recorded cases (lifted before them) | never return | registers measured, against regflow | "
             "seconds (asm, ghidra, lift) |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in rows:
        g, lf = m.get("ghidra", {}), m.get("lift", {})
        v = lf.get("verdicts", {})
        r = m.get("record", {})
        c = m.get("callconv", {})
        lines.append("| %s | %s | %d | %d | %s | %s | %s | %s | %s | %s | %s |" % (
            m["build"], compiler_cell(m), m["functions"], m["instruction_bytes"],
            ("identical" if m["asm"]["identical"] else "differs") + (" (%d raw)" % m["asm"]["raw_words"]
                                                                     if m["asm"]["raw_words"] else "")
            if "asm" in m else "-",
            "%d / %d / %d" % (g["found"], g["missed"], g["extra"]) if g.get("ran") else "-",
            "%d of %d (%.0f %%)" % (v.get("accepted", 0), sum(v.values()), 100.0 * v.get("accepted", 0) /
                                    max(1, sum(v.values()))) if v else "-",
            "%d (%s)" % (r["kept"], r.get("accepted_before", "-")) if r.get("kept") else "-",
            m.get("noreturn", {}).get("functions", "-"),
            "%d of %d disagree" % (c["measured"] - c["agree"], c["measured"]) if c.get("ran") else "-",
            ", ".join("%.0f" % m[s]["seconds"] if s in m else "-" for s in ("asm", "ghidra", "lift"))))
    # The same across builds: what stops the rest by category, so that a category only some
    # compilers or versions show stands out (docs/todo.md, "The benchmark across compiler versions")
    lines += ["", "## What stops the rest, by build", "",
              "| build | compiler | not lifted | %s | refused | refused most for |" % " | ".join(CATEGORIES),
              "|---|---|---|%s---|---|" % ("---|" * len(CATEGORIES))]
    for m in rows:
        lf = m.get("lift", {})
        if not lf.get("verdicts"):
            continue
        counts, refused, top = stops(m)
        lines.append("| %s | %s | %d | %s | %d | %s |" % (
            m["build"], compiler_cell(m), sum(n for v, n in lf["verdicts"].items() if v != "accepted"),
            " | ".join(str(counts.get(c, 0)) for c in CATEGORIES), sum(refused.values()),
            "%d x %s" % (top[1], top[0][len("refused: "):]) if top else "-"))
    lines += readable_report(rows)
    if any(m.get("groups", {}).get("ran") for m in rows):
        lines += ["", "## Groups by code against the source files (dread groups)", "",
                  "The functions grouped by their code alone (shared memory, texts and devices, addresses taken, "
                  "neighbours in memory), against the source file of each in the build's debug information.", "",
                  "| build | functions with a file | files | groups | adjusted Rand index | purity | completeness | "
                  "seconds |", "|---|---|---|---|---|---|---|---|"]
        for m in rows:
            g = m.get("groups", {})
            if g.get("ran"):
                lines.append("| %s | %d | %d | %d | %.3f | %.3f | %.3f | %.0f |" % (
                    m["build"], g["functions"], g["files"], g["groups"], g["ari"], g["purity"], g["completeness"],
                    g["seconds"]))
    if any(m.get("whatcc", {}).get("ran") for m in rows):
        lines += ["", "## Library functions by their bytes (dread whatcc)", "",
                  "| build | built with | best (ties) | right | functions matched | named as certain | "
                  "of those right |", "|---|---|---|---|---|---|---|"]
        for m in rows:
            w = m.get("whatcc", {})
            if w.get("ran"):
                lines.append("| %s | %s | %s | %s | %d | %d | %d |" % (
                    m["build"], w.get("truth") or "-", ", ".join(w["best"]) or "-", "yes" if w["right"] else "no",
                    w["matched"], w["sure"], w["agree"]))
    for m in rows:
        reasons = m.get("lift", {}).get("reasons")
        if reasons:
            lines += ["", "## %s: why the rest is not lifted" % m["build"], ""]
            lines += ["- %d x %s" % (n, r) for r, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:15]]
        disagree = m.get("callconv", {}).get("disagree")
        if disagree:
            lines += ["", "## %s: registers measured against regflow (dread callconv)" % m["build"], ""]
            lines += ["- %s: %s" % (n, "; ".join("%s %s" % (k, ", ".join(v)) for k, v in found.items()))
                      for n, found in sorted(disagree.items())[:20]]
    path = os.path.join(bench or bench_dir(), recipe["name"], "report.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    out("report: %s" % path)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread bench", description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=("fetch", "build", "measure", "report", "all", "toolchain"))
    parser.add_argument("recipe", help="a recipe file, or the name of one in DREAD's bench/ (toolchain: a toolchain)")
    parser.add_argument("builds", nargs="*", help="builds of the recipe (default: all; toolchain: more toolchains)")
    parser.add_argument("--rebuild", action="store_true", help="toolchain: build the image again")
    parser.add_argument("--prune", action="store_true",
                        help="toolchain: remove the images of older versions of the toolchains' files")
    parser.add_argument("--steps", default=",".join(STEPS), help="measuring steps (default: %s)" % ",".join(STEPS))
    parser.add_argument("--jobs", type=int, help="compilers at once")
    parser.add_argument("--json", metavar="FILE",
                        help="report (and measure, all): also write the report as JSON to FILE, with each build's "
                             "earlier measurements (docs/formats.md)")
    args = parser.parse_args(argv)
    try:
        if args.action == "toolchain":
            for name in [args.recipe] + args.builds:
                print("%s: %s" % (name, ensure_toolchain(name, rebuild=args.rebuild)))
                if args.prune:
                    prune_toolchain(name)
            return 0
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
            if args.json:
                print("report as JSON: %s" % write_json(report_data(recipe), args.json))
    except BenchError as e:
        print("dread bench: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
