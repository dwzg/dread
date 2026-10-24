"""dread recon: compile the image's C and link it over the original (dread.overlay).

Compiles every .c file under the image's src directory (dread.toml, [image] src, default src) with
the target's [compiler] flags plus what the overlay needs (-ffunction-sections, -fno-common,
-fno-reorder-functions),
with DREAD's include directory (dread.h), the src directory and [compiler] include on the include
path. Objects go to BUILD/recon/IMAGE/obj; a source is compiled again when it, or any header in
those directories, is newer than its object, and every source when the compiler or the flags are
not those the objects were made with (.flags in the object directory). Lifted C is compiled with
[compiler] generated_std (default -std=gnu11) and lifted_cflags (default -Os) after the flags. Then
links them over the image:
BUILD/recon/IMAGE/payload.bin, with report.tsv, placement.json, recon.ld and recon.elf beside it.

Functions in the image's lifted table (config/lifted.txt, written by `dread lift --check`) are
built from C that dread.lift makes from the original at every build (BUILD/recon/IMAGE/lifted, not
kept); a name the src directory defines is left to src. lifted.txt beside the image says which.
Those of its readable table (config/readable.txt, written by `dread readable --check`) are built
from readable C made from that lifted C (dread.readable chooser): every one lifted first, then all
their readable C at once, in a pool of worker processes (dread.readable readable_texts; kept in the
process for the next build with the same tables and lifted C).

    dread recon [--target DIR] [--image NAME] [--src DIR] [--out DIR] [--no-extent]
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

from dread import overlay
from dread import target as tgt

INCLUDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "include")
REQUIRED = ["-ffunction-sections", "-fno-common", "-fno-reorder-functions"]
# -fno-reorder-functions: at -O2 and -Os gcc puts main into .text.startup.main (and cold or hot
# functions into .text.unlikely or .text.hot), where the overlay does not find a function's section


class BuildFailed(Exception):
    """A build an acceptance loop cannot mend by dropping functions (dread lift --check, dread readable
    --check: a source that does not compile, a header that is missing): the loop writes no table and
    the command exits 1. Until 2026-10-23 the loops returned nothing accepted and the commands exited
    0 with the old table in place, so a comparison of the tables before and after read "the same"
    (a gate copy without a directory its sources include)."""


def recon_dir(target, image):
    return os.path.join(target.build, "recon", image.name)


CALL_SAVED = "/* dread: call-saved "     # a lifted file's first line: registers to keep (Lifter.call_saved)
FIXED = "/* dread: fixed "               # ... or registers the C gets as the caller left them (handed)
NULL_CHECKS = "/* dread: null checks kept */"   # readable C with pointers (dread.shapes): address 0 is memory
WRAPS = "/* dread: overflow wraps */"       # readable C with typed variables (dread.vartypes): arithmetic wraps


def file_flags(target, path):
    """Compiler flags a source asks for on its first lines: GCC's -fcall-saved-REG for the registers
    of a CALL_SAVED line, -ffixed-REG for those of a FIXED line (the C reads them as the caller left
    them; other compilers have no such options for these targets); -fno-delete-null-pointer-checks
    for a NULL_CHECKS line (GCC and clang: readable C that dereferences a pointer would otherwise
    lose its comparisons of that pointer with 0, and address 0 is memory on these targets);
    -fno-strict-overflow for a WRAPS line (-fwrapv and -fwrapv-pointer for GCC 8 and later and clang 23;
    an older clang's -fwrapv-pointer is not confirmed): readable C with signed variables and typed
    pointers (dread.vartypes) computes what the lifted C's u32 arithmetic did, which wraps."""
    with open(path) as f:
        lines = [f.readline() for _ in range(6)]
    flags = []
    if any(line.startswith(NULL_CHECKS) for line in lines):
        flags.append("-fno-delete-null-pointer-checks")
    if any(line.startswith(WRAPS) for line in lines):
        flags.append("-fno-strict-overflow")
    if target.compiler.kind != "gcc":
        return flags
    for line in lines:
        if line.startswith(CALL_SAVED):
            flags += ["-fcall-saved-" + r for r in line[len(CALL_SAVED):].split("*/")[0].split()]
        elif line.startswith(FIXED):
            flags += ["-ffixed-" + r for r in line[len(FIXED):].split("*/")[0].split()]
    return flags


_COMPILERS = {}

SOURCES = (".c", ".cpp", ".cc", ".cxx")             # in src: C, and C++ (dread.cxx)
HEADERS = (".h", ".hpp", ".hh")


def is_cxx(path):
    """Whether a source is C++ (compiled with [compiler]'s cxx and cxxflags)."""
    return path.endswith(SOURCES[1:])


def compile_stamp(command, flags):
    """What a directory's objects are compiled with: the compiler (its path, size and time, so that
    an upgrade counts) and the command line common to every source. Objects made otherwise are
    stale, however new: once a target's -O0 became -Os and most objects stayed at -O0."""
    exe = command[0]
    if exe not in _COMPILERS:
        path = os.path.realpath(shutil.which(exe) or exe)
        try:
            st = os.stat(path)
            _COMPILERS[exe] = "%s %d %d" % (path, st.st_size, st.st_mtime_ns)
        except OSError:
            _COMPILERS[exe] = path
    return "\n".join([_COMPILERS[exe]] + list(command) + list(flags)) + "\n"


ERROR = re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?:fatal )?error: (?P<message>.*)$")
DIAGNOSTIC = re.compile(r"^[^:\n]+:\d+(?::\d+)?:\s*(?:fatal error|error|warning|note)\b|^In file included from |"
                        r"^[^:\n]+: In function |^(?:cc1\w*|clang|gcc|collect2|compilation terminated)\b")


def compiler_error(output):
    """The first error a compiler's output names, as one line for a verdict: its file (by name, without
    the directory), line, column and message, and after " | " the line of code it quotes (GCC 9 and
    later and clang 15 and later print it behind its number and a bar, older ones as it is); "" where
    the output names no error (a compiler that crashed, a linker's message)."""
    lines = output.splitlines()
    for k, line in enumerate(lines):
        m = ERROR.match(line)
        if not m:
            continue
        where = "%s:%s%s" % (os.path.basename(m.group("file").strip()), m.group("line"),
                             ":" + m.group("col") if m.group("col") else "")
        text = "%s: error: %s" % (where, m.group("message").strip())
        quoted = lines[k + 1] if k + 1 < len(lines) else ""
        bar = re.match(r"^\s*\d*\s*\|(.*)$", quoted)
        code = bar.group(1).strip() if bar else quoted.strip()
        if code and not DIAGNOSTIC.match(quoted) and not re.fullmatch(r"[\s^~|+-]*", code):
            text += " | " + code
        return text
    return ""


def compile_all(target, image, src, objdir, jobs=None, extra_include=(), quiet=False, flags=()):
    """Compile the sources under `src` into `objdir`; returns the object paths. extra_include:
    further include directories (dread mutate compiles a copy elsewhere and still needs the
    headers beside the original); quiet: no compiler messages (variants that do not compile);
    flags: further flags after the target's (the lifted C's [compiler] lifted_cflags). A source that
    does not compile raises dread.overlay OverlayError with the sources that failed and, for each, the
    first error the compiler named (compiler_error), quiet or not."""
    sources = sorted(f for suffix in SOURCES for f in glob.glob(os.path.join(src, "**", "*" + suffix), recursive=True))
    include = [INCLUDE, src] + list(extra_include) + target.compiler.include
    headers = [h for d in include for suffix in HEADERS
               for h in glob.glob(os.path.join(d, "**", "*" + suffix), recursive=True)]
    newest_header = max((os.path.getmtime(h) for h in headers), default=0)
    extra = target.compiler.required() + list(flags) + ["-I" + d for d in include]
    flags = target.compiler.cflags + extra
    cxx = [target.compiler.command("cxx"), target.compiler.cxxflags + extra]
    stamp_path, stamp = os.path.join(objdir, ".flags"), compile_stamp(target.compiler.command("cc"), flags)
    if any(is_cxx(c) for c in sources):     # (a C-only build keeps the stamp it had)
        stamp += compile_stamp(*cxx)
    try:
        with open(stamp_path) as f:
            same = f.read() == stamp
    except OSError:
        same = False
    objects, todo = [], []
    for c in sources:
        rel = os.path.relpath(c, src)
        obj = os.path.join(objdir, rel + ".o" if is_cxx(c) else rel[:-2] + ".o")
        objects.append(obj)
        if not same or not os.path.exists(obj) or os.path.getmtime(obj) < max(os.path.getmtime(c), newest_header):
            todo.append((c, obj))
    procs = []
    for c, obj in todo:
        os.makedirs(os.path.dirname(obj), exist_ok=True)
        if os.path.exists(obj):         # a failed compile must not leave the old object behind
            os.remove(obj)
        command = cxx[0] + cxx[1] if is_cxx(c) else target.compiler.command("cc") + flags
        procs.append((c, subprocess.Popen(command + file_flags(target, c) + ["-c", "-o", obj, c],
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)))
    failed, errors = [], {}
    for c, p in procs:
        out = p.communicate()[0]
        if out and not quiet:
            sys.stderr.write(out)
        if p.returncode:
            failed.append(c)
            errors[c] = compiler_error(out or "")
    if failed:
        raise overlay.OverlayError("compiling failed: %s" % ", ".join(os.path.relpath(c) for c in failed),
                                   failed=failed, errors=errors)
    if not same:                        # written last: an interrupted run leaves the old stamp
        os.makedirs(objdir, exist_ok=True)
        with open(stamp_path, "w") as f:
            f.write(stamp)
    stale = set(glob.glob(os.path.join(objdir, "**", "*.o"), recursive=True)) - set(objects)
    for obj in stale:                   # a removed source must not stay linked
        os.remove(obj)
    return objects


def read_names(path):
    """The names of a list table (one per line, # comments), or [] when it does not exist."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [l.split("#", 1)[0].strip() for l in f if l.split("#", 1)[0].strip()]


def defined_functions(objects):
    """The global functions the objects define, weak ones too: C++ emits template instances and
    inline functions weak (in COMDAT groups), and the original's symbol of the same name would win
    the link over them."""
    from dread.elf import STT_FUNC, Elf
    names = set()
    for obj in objects:
        for sym in Elf(obj).symbols:
            if sym.kind == STT_FUNC and sym.shndx and sym.bind in (1, 2):     # (STB_GLOBAL, STB_WEAK)
                names.add(sym.name)
    return names


def called_functions(objects):
    """The symbols the objects use without defining them: the functions their C calls (GCC's libcalls
    among them) and the data it addresses."""
    from dread.elf import Elf
    return {sym.name for obj in objects for sym in Elf(obj).symbols if sym.shndx == 0 and sym.name}


def lifted_result(lifter, fn):
    """lifter.lift(fn), kept in the lifter (lifter.lifted, a refusal too: raised again) for the
    builds that use it; lift_sources forgets what it kept when the hand-written C's calls change."""
    from dread import lift
    kept = lifter.__dict__.setdefault("lifted", {})
    if fn.name not in kept:
        try:
            kept[fn.name] = lifter.lift(fn)
        except lift.Refused as e:
            kept[fn.name] = e
    if isinstance(kept[fn.name], lift.Refused):
        raise kept[fn.name]
    return kept[fn.name]


def lift_sources(target, image, names, outdir, quiet=False, lifter=None, src_calls=None, transform=None):
    """Lifted C for `names` (dread.lift) in outdir, a file each, written only when it changed;
    returns ({name: the instructions no path from its entry reaches}, [the names that never return:
    the system run tests them]). A name that no longer lifts is reported and left out. lifter: one
    already made for this image (dread lift --check builds many times with one: what it lifts is kept
    in it, lifter.lifted). src_calls: what the hand-written C calls (dread.lift.Lifter.src_calls).
    transform(name, text): the C to write instead of the lifted text, or None to keep it (dread
    readable: readable C for the functions of its table). Every name is lifted first; a transform
    with a prepare attribute (dread.readable chooser) is handed {name: lifted C} of them all before
    the first call, to make what it writes at once (readable C in a pool of worker processes, or kept
    from the round before: dread.readable readable_texts)."""
    from dread import lift, regflow
    from dread.program import Program
    if lifter is None:
        program = Program(target, image)
        lifter = lift.make_lifter(program, regflow.load(target, image, program))
    program = lifter.prog
    kept = lifter.__dict__.setdefault("lifted", {})
    if src_calls is not None and frozenset(src_calls) != lifter.src_calls:
        lifter.src_calls = frozenset(src_calls)
        kept.clear()                        # what was lifted for other callers
    os.makedirs(outdir, exist_ok=True)
    texts = {}
    for name in names:
        fn = program.by_name.get(name)
        if fn is not None:
            try:
                texts[name] = lifted_result(lifter, fn).text
            except lift.Refused:
                pass                        # (reported below, in the names' order)
    if getattr(transform, "prepare", None) is not None:
        transform.prepare(texts)
    made, endless, loops, bodies = {}, [], [], {}
    for name in names:
        fn = program.by_name.get(name)
        if fn is None:
            continue
        try:
            result = lifted_result(lifter, fn)
            text = result.text
            if transform is not None:
                text = transform(name, text) or text
            saved = lifter.call_saved(fn)
            handed = getattr(result, "handed", None)
            if handed:                      # (compile_all reads it: GCC's -ffixed-REG)
                text = "%s%s */\n%s" % (FIXED, " ".join(handed), text)
            if saved:                       # (compile_all reads it: GCC's -fcall-saved-REG)
                text = "%s%s */\n%s" % (CALL_SAVED, " ".join(saved), text)
        except lift.Refused as e:
            if not quiet:
                print("dread recon: %s no longer lifts: %s" % (name, e), file=sys.stderr)
            continue
        path = os.path.join(outdir, name + ".c")
        if not os.path.exists(path) or open(path).read() != text:
            with open(path, "w") as f:
                f.write(text)
        made[name] = result.unreached
        if getattr(result, "body", None):
            bodies[name] = result.body      # its C holds shared code too (lift.Lifter.shared_code)
        if lifter.system_only(fn):         # never returns, or an exception handler
            endless.append(name)
            if lifter.handler_kind(fn) is None:
                loops.append(name)          # it never returns: loop cases may test it (dread.autocase)
    for old in glob.glob(os.path.join(outdir, "*.c")):
        if os.path.basename(old)[:-2] not in made:
            os.remove(old)
    return made, endless, loops, bodies


def system_names(path):
    """The names of a lifted table that the system run accepted (dread lift --check marks them with
    a comment): they keep being tested by the scenarios, not by loop cases."""
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {l.split("#", 1)[0].strip() for l in f if "#" in l and "system run" in l.split("#", 1)[1]
                and l.split("#", 1)[0].strip()}


def build(target, image, src=None, out=None, extent=True, quiet=False, lifted=None, lifter=None, transform=None):
    """Compile and link; returns the path of the rebuilt image. lifted: names to build from lifted C
    (default: the image's lifted table, those of its readable table from readable C: dread
    readable); a name src defines is left to src. lifter, transform: see lift_sources."""
    workdir = out or recon_dir(target, image)
    objects = compile_all(target, image, src or image.src, os.path.join(workdir, "obj"))
    names = read_names(image.table("lifted")) if lifted is None else list(lifted)
    if lifted is None and transform is None and os.path.exists(image.table("readable")):
        from dread import readable
        names_ = read_names(image.table("readable"))
        if names_:
            transform = readable.chooser(names_, readable.Context(target, image))
    record = os.path.join(workdir, "lifted.txt")
    if names:
        defined = defined_functions(objects)
        made, endless, loops, bodies = lift_sources(target, image, [n for n in names if n not in defined],
                                            os.path.join(workdir, "lifted"), quiet=quiet, lifter=lifter,
                                            src_calls=called_functions(objects), transform=transform)
        if lifted is None:                  # the image's table: the system run's acceptances stay its own
            by_system = system_names(image.table("lifted"))
            loops = [n for n in loops if n not in by_system]
        objects += compile_all(target, image, os.path.join(workdir, "lifted"), os.path.join(workdir, "obj-lifted"),
                               quiet=quiet, flags=target.compiler.generated_cflags())
        with open(record, "w") as f:
            f.write("".join(n + "\n" for n in made))
        with open(os.path.join(workdir, "lifted.json"), "w") as f:
            json.dump({"unreached": {n: a for n, a in sorted(made.items()) if a}, "system": sorted(endless),
                       "loop": sorted(loops), "bodies": {n: bodies[n] for n in sorted(bodies)}}, f, indent=1)
    elif os.path.exists(record):
        os.remove(record)
    payload = os.path.join(workdir, "payload.bin")
    lines, _ = overlay.link(target, image, objects, payload, workdir, extent=extent)
    if not quiet:
        print(lines[-1].lstrip("# "))
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread recon", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--src", help="the C sources (default: the image's src)")
    parser.add_argument("--out", help="work directory (default: BUILD/recon/IMAGE)")
    parser.add_argument("--no-extent", action="store_true", help="never give a function its literal pools")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        path = build(target, image, args.src, args.out, extent=not args.no_extent)
    except (tgt.TargetError, overlay.OverlayError) as e:
        print("dread recon: %s" % e, file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
