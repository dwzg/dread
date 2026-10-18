"""dread recon: compile the image's C and link it over the original (dread.overlay).

Compiles every .c file under the image's src directory (dread.toml, [image] src, default src) with
the target's [compiler] flags plus what the overlay needs (-ffunction-sections, -fno-common,
-fno-reorder-functions),
with DREAD's include directory (dread.h), the src directory and [compiler] include on the include
path. Objects go to BUILD/recon/IMAGE/obj; a source is compiled again when it, or any header in
those directories, is newer than its object. Then links them over the image:
BUILD/recon/IMAGE/payload.bin, with report.tsv, placement.json, recon.ld and recon.elf beside it.

Functions in the image's lifted table (config/lifted.txt, written by `dread lift --check`) are
built from C that dread.lift makes from the original at every build (BUILD/recon/IMAGE/lifted, not
kept); a name the src directory defines is left to src. lifted.txt beside the image says which.

    dread recon [--target DIR] [--image NAME] [--src DIR] [--out DIR] [--no-extent]
"""
import argparse
import glob
import json
import os
import subprocess
import sys

from dread import overlay
from dread import target as tgt

INCLUDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "include")
REQUIRED = ["-ffunction-sections", "-fno-common", "-fno-reorder-functions"]
# -fno-reorder-functions: at -O2 and -Os gcc puts main into .text.startup.main (and cold or hot
# functions into .text.unlikely or .text.hot), where the overlay does not find a function's section


def recon_dir(target, image):
    return os.path.join(target.build, "recon", image.name)


def compile_all(target, image, src, objdir, jobs=None, extra_include=(), quiet=False):
    """Compile the sources under `src` into `objdir`; returns the object paths. extra_include:
    further include directories (dread mutate compiles a copy elsewhere and still needs the
    headers beside the original); quiet: no compiler messages (variants that do not compile)."""
    sources = sorted(glob.glob(os.path.join(src, "**", "*.c"), recursive=True))
    include = [INCLUDE, src] + list(extra_include) + target.compiler.include
    headers = [h for d in include for h in glob.glob(os.path.join(d, "**", "*.h"), recursive=True)]
    newest_header = max((os.path.getmtime(h) for h in headers), default=0)
    objects, todo = [], []
    for c in sources:
        rel = os.path.relpath(c, src)
        obj = os.path.join(objdir, rel[:-2] + ".o")
        objects.append(obj)
        if not os.path.exists(obj) or os.path.getmtime(obj) < max(os.path.getmtime(c), newest_header):
            todo.append((c, obj))
    flags = target.compiler.cflags + REQUIRED + ["-I" + d for d in include]
    procs = []
    for c, obj in todo:
        os.makedirs(os.path.dirname(obj), exist_ok=True)
        procs.append((c, subprocess.Popen([target.compiler.tool("gcc")] + flags + ["-c", "-o", obj, c],
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)))
    failed = []
    for c, p in procs:
        out = p.communicate()[0]
        if out and not quiet:
            sys.stderr.write(out)
        if p.returncode:
            failed.append(c)
    if failed:
        raise overlay.OverlayError("compiling failed: %s" % ", ".join(os.path.relpath(c) for c in failed))
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
    """The global functions the objects define."""
    from dread.elf import STT_FUNC, Elf
    names = set()
    for obj in objects:
        for sym in Elf(obj).symbols:
            if sym.kind == STT_FUNC and sym.shndx and sym.bind == 1:
                names.add(sym.name)
    return names


def lift_sources(target, image, names, outdir, quiet=False):
    """Lifted C for `names` (dread.lift) in outdir, a file each, written only when it changed;
    returns ({name: the instructions no path from its entry reaches}, [the names that never return:
    the system run tests them]). A name that no longer lifts is reported and left out."""
    from dread import lift, regflow
    from dread.program import Program
    program = Program(target, image)
    lifter = lift.Lifter(program, regflow.load(target, image, program))
    os.makedirs(outdir, exist_ok=True)
    made, endless = {}, []
    for name in names:
        fn = program.by_name.get(name)
        if fn is None:
            continue
        try:
            result = lifter.lift(fn)
            text = result.text
        except lift.Refused as e:
            if not quiet:
                print("dread recon: %s no longer lifts: %s" % (name, e), file=sys.stderr)
            continue
        path = os.path.join(outdir, name + ".c")
        if not os.path.exists(path) or open(path).read() != text:
            with open(path, "w") as f:
                f.write(text)
        made[name] = result.unreached
        if lifter.system_only(fn):         # never returns, or an exception handler
            endless.append(name)
    for old in glob.glob(os.path.join(outdir, "*.c")):
        if os.path.basename(old)[:-2] not in made:
            os.remove(old)
    return made, endless


def build(target, image, src=None, out=None, extent=True, quiet=False, lifted=None):
    """Compile and link; returns the path of the rebuilt image. lifted: names to build from lifted C
    (default: the image's lifted table); a name src defines is left to src."""
    workdir = out or recon_dir(target, image)
    objects = compile_all(target, image, src or image.src, os.path.join(workdir, "obj"))
    names = read_names(image.table("lifted")) if lifted is None else list(lifted)
    record = os.path.join(workdir, "lifted.txt")
    if names:
        defined = defined_functions(objects)
        made, endless = lift_sources(target, image, [n for n in names if n not in defined], os.path.join(workdir, "lifted"),
                            quiet=quiet)
        objects += compile_all(target, image, os.path.join(workdir, "lifted"), os.path.join(workdir, "obj-lifted"),
                               quiet=quiet)
        with open(record, "w") as f:
            f.write("".join(n + "\n" for n in made))
        with open(os.path.join(workdir, "lifted.json"), "w") as f:
            json.dump({"unreached": {n: a for n, a in sorted(made.items()) if a}, "system": sorted(endless)}, f,
                      indent=1)
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
