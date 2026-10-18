"""dread recon: compile the image's C and link it over the original (dread.overlay).

Compiles every .c file under the image's src directory (dread.toml, [image] src, default src) with
the target's [compiler] flags plus what the overlay needs (-ffunction-sections, -fno-common),
with DREAD's include directory (dread.h), the src directory and [compiler] include on the include
path. Objects go to BUILD/recon/IMAGE/obj; a source is compiled again when it, or any header in
those directories, is newer than its object. Then links them over the image:
BUILD/recon/IMAGE/payload.bin, with report.tsv, placement.json, recon.ld and recon.elf beside it.

    dread recon [--target DIR] [--image NAME] [--src DIR] [--out DIR] [--no-extent]
"""
import argparse
import glob
import os
import subprocess
import sys

from dread import overlay
from dread import target as tgt

INCLUDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "include")
REQUIRED = ["-ffunction-sections", "-fno-common"]


def recon_dir(target, image):
    return os.path.join(target.build, "recon", image.name)


def compile_all(target, image, src, objdir, jobs=None):
    """Compile the sources under `src` into `objdir`; returns the object paths."""
    sources = sorted(glob.glob(os.path.join(src, "**", "*.c"), recursive=True))
    include = [INCLUDE, src] + target.compiler.include
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
        if out:
            sys.stderr.write(out)
        if p.returncode:
            failed.append(c)
    if failed:
        raise overlay.OverlayError("compiling failed: %s" % ", ".join(os.path.relpath(c) for c in failed))
    stale = set(glob.glob(os.path.join(objdir, "**", "*.o"), recursive=True)) - set(objects)
    for obj in stale:                   # a removed source must not stay linked
        os.remove(obj)
    return objects


def build(target, image, src=None, out=None, extent=True, quiet=False):
    """Compile and link; returns the path of the rebuilt image."""
    workdir = out or recon_dir(target, image)
    objects = compile_all(target, image, src or image.src, os.path.join(workdir, "obj"))
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
