"""dread rename: give functions new names, everywhere a name is written, or nowhere.

A function's name is in the image's function table and in symbols.txt (where a regenerated table
takes its names from), and other files refer to it by name:
- the target file: an image's entry (entry = "name") and [abi] stack_args' keys;
- the lifted table, the argued survivors and kept registers (argued.tsv, kept.tsv);
- the case files (auto("name"), case("name", ...), entry("name") and any other quoted name), and
  the files they read (a file of cases shared by several targets, say);
- the C under src and every file it includes with #include "..." (identifiers);
- the written, kept and recorded cases and the cache of generated ones (one file per function,
  named after it, moved along);
- the records of the last runs (the lift check's verdicts, the test run's results), so that readers
  such as dread gui keep showing them.
Annotations are kept by address and need nothing. Images of the target that share the src
directory are renamed along: their builds read the same C.

Refused, with nothing changed: an unknown old name; a new name that is not a C identifier or that a
function of one of those images already has; a change to a file outside the target's directory
(another target may read it too: --outside allows it). After the change the C is compiled and the
case files are loaded: when a file the rename does not know still defines or names an old name, the
change is undone and the command says where.

    dread rename [--target DIR] [--image NAME] [--dry-run] [--outside] OLD=NEW...
"""
import argparse
import glob
import os
import re
import sys

from dread import target as tgt

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)


class Refused(tgt.TargetError):
    pass


def c_files(image):
    """The C under the image's src and every file reachable from it through #include "..."."""
    seen, todo = [], sorted(glob.glob(os.path.join(image.src, "**", "*.[ch]"), recursive=True))
    while todo:
        path = os.path.abspath(todo.pop(0))
        if path in seen or not os.path.isfile(path):
            continue
        seen.append(path)
        with open(path, errors="replace") as f:
            text = f.read()
        for name in INCLUDE.findall(text):
            for base in (os.path.dirname(path), image.src):
                candidate = os.path.normpath(os.path.join(base, name))
                if os.path.isfile(candidate):
                    todo.append(candidate)
                    break
    return seen


def case_files(target, image):
    """The case files of the image and the files they read while loading."""
    from dread.cases import Suite
    from dread.emu import Layout
    from dread.program import Program
    files = sorted(os.path.abspath(p) for p in glob.glob(os.path.join(image.cases, "**", "*.py"), recursive=True))
    try:
        suite = Suite(target, image, Program(target, image), Layout(target, image)).load()
        files += sorted(p for p in suite.read if p.endswith((".py", ".tsv", ".txt", ".json")) and p not in files)
    except Exception:
        pass                                # the check after the change reports a suite that does not load
    return files


def images_sharing(target, image):
    return [image] + [other for other in target.images.values()
                      if other is not image and os.path.abspath(other.src) == os.path.abspath(image.src)]


def plan(target, image, pairs, outside=False):
    """[(path, new text or None, new path or None)] for renaming {old: new}; raises Refused."""
    from dread.program import Program
    images = images_sharing(target, image)
    programs = {img.name: Program(target, img) for img in images}
    if not pairs:
        raise Refused("nothing to rename")
    for old, new in pairs.items():
        if old not in programs[image.name].by_name:
            raise Refused("no function %s in %s" % (old, image.table("functions")))
        if not IDENT.match(new):
            raise Refused("%s is not a C identifier" % new)
        for img in images:
            if new in programs[img.name].by_name and new not in pairs:
                raise Refused("%s is already the name of a function of image %s" % (new, img.name))
    words = re.compile(r"\b(%s)\b" % "|".join(re.escape(o) for o in sorted(pairs, key=len, reverse=True)))
    quoted = re.compile(r"""(["'])(%s)\1""" % "|".join(re.escape(o) for o in sorted(pairs, key=len, reverse=True)))
    changes = {}                            # path -> [new text or None, new path or None]

    def edit(path, transform):
        path = os.path.abspath(path)
        if path in changes or not os.path.isfile(path):
            return
        with open(path) as f:
            text = f.read()
        changed = transform(text)
        if changed != text:
            changes[path] = [changed, None]

    def column(n):
        def transform(text):
            out = []
            for line in text.splitlines(True):
                parts = line.rstrip("\n").split("\t")
                if len(parts) > n and parts[n] in pairs and not line.startswith("#"):
                    parts[n] = pairs[parts[n]]
                    line = "\t".join(parts) + ("\n" if line.endswith("\n") else "")
                out.append(line)
            return "".join(out)
        return transform

    def symbols(text):
        out = []
        for line in text.splitlines(True):
            parts = line.split()
            if len(parts) >= 3 and parts[0] in ("f", "d") and parts[2] in pairs and not line.startswith("#"):
                line = " ".join([parts[0], parts[1], pairs[parts[2]]] + parts[3:]) + "\n"
            out.append(line)
        return "".join(out)

    def names(text):
        return "".join(pairs[l.strip()] + "\n" if l.strip() in pairs and not l.startswith("#") else l
                       for l in text.splitlines(True))

    keys = re.compile(r"\b(%s)(\s*=)" % "|".join(re.escape(o) for o in sorted(pairs, key=len, reverse=True)))

    def target_file(text):
        """An image's entry and [abi] stack_args' keys (an inline table: one line)."""
        out = []
        for line in text.splitlines(True):
            if re.match(r'\s*entry\s*=\s*"', line):
                line = quoted.sub(lambda m: m.group(1) + pairs[m.group(2)] + m.group(1), line)
            elif re.match(r"\s*stack_args\s*=", line):
                line = keys.sub(lambda m: pairs[m.group(1)] + m.group(2), line)
            out.append(line)
        return "".join(out)

    edit(target.file, target_file)
    for img in images:
        mine = {o: n for o, n in pairs.items() if o in programs[img.name].by_name}
        if not mine:
            continue
        edit(img.table("functions"), column(1))
        edit(img.table("symbols"), symbols)
        edit(img.table("lifted"), names)
        for path in case_files(target, img):
            edit(path, lambda t: quoted.sub(lambda m: m.group(1) + pairs[m.group(2)] + m.group(1), t))
        here = os.path.dirname(img.cases)
        edit(os.path.join(here, "argued.tsv"), column(0))
        edit(os.path.join(here, "kept.tsv"), column(0))
        for record in (os.path.join(target.build, "lift-check", img.name, "verdicts.tsv"),
                       os.path.join(target.build, "recon", img.name, "tests.tsv")):
            edit(record, column(0))
        for folder, suffix in ((img.extra_cases, ".json"), (img.kept_cases, ".json"), (img.record_specs, ".py"),
                               (os.path.join(img.src, "pilot"), ".c"),
                               (os.path.join(target.build, "autocases", img.name), ".json")):
            for old, new in mine.items():
                path = os.path.abspath(os.path.join(folder, old + suffix))
                if os.path.isfile(path):
                    changes.setdefault(path, [None, None])[1] = os.path.join(folder, new + suffix)
    for path in c_files(image):
        edit(path, lambda t: words.sub(lambda m: pairs[m.group(1)], t))
    root = os.path.abspath(target.root)
    away = sorted(p for p in changes if os.path.commonpath([root, p]) != root)
    if away and not outside:
        raise Refused("the names are also written outside the target's directory, where other targets may read "
                      "them: %s (dread rename --outside changes them too)" % ", ".join(os.path.relpath(p, root)
                                                                                  for p in away))
    return [(p, text, move) for p, (text, move) in changes.items()]


def apply(changes):
    """Make the changes; returns what undo() needs."""
    done = []
    for path, text, move in changes:
        with open(path) as f:
            before = f.read()
        if text is not None:
            with open(path, "w") as f:
                f.write(text)
        if move is not None:
            os.replace(path, move)
        done.append((path, before, move))
    return done


def undo(done):
    for path, before, move in reversed(done):
        if move is not None and os.path.exists(move):
            os.replace(move, path)
        with open(path, "w") as f:
            f.write(before)


def verify(target, image, pairs):
    """None, or why the renamed tree is not consistent: src still defines an old name, or the case
    files do not load (they name a function the table no longer has)."""
    from dread import build, overlay
    from dread.cases import Suite
    from dread.emu import Layout
    from dread.program import Program
    for img in images_sharing(target, image):
        try:
            objects = build.compile_all(target, img, img.src, os.path.join(build.recon_dir(target, img), "obj"),
                                        quiet=True)
        except (overlay.OverlayError, tgt.TargetError, OSError) as e:
            return "the C of image %s does not compile: %s" % (img.name, e)
        left = sorted(set(pairs) & build.defined_functions(objects))
        if left:
            return "the C still defines %s in a file the rename does not reach" % ", ".join(left)
        try:
            Layout(target, img)
        except Exception:
            continue                        # an image without the emulator's settings has no cases to load
        try:
            Suite(target, img, Program(target, img), Layout(target, img)).load()
        except Exception as e:
            return "the case files of image %s do not load: %s" % (img.name, e)
    return None


def rename(target, image, pairs, dry_run=False, outside=False, check=True):
    """Rename {old: new} everywhere, or nowhere; returns the changes (see plan); raises Refused."""
    changes = plan(target, image, pairs, outside)
    if dry_run:
        return changes
    done = apply(changes)
    why = verify(target, image, pairs) if check else None
    if why:
        undo(done)
        raise Refused("%s; nothing was changed" % why)
    return changes


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread rename", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--dry-run", action="store_true", help="list what would change, change nothing")
    parser.add_argument("--outside", action="store_true", help="also change files outside the target's directory")
    parser.add_argument("pairs", nargs="+", metavar="OLD=NEW")
    args = parser.parse_args(argv)
    pairs = {}
    for item in args.pairs:
        old, sep, new = item.partition("=")
        if not sep or not old or not new:
            print("dread rename: %s is not OLD=NEW" % item, file=sys.stderr)
            return 1
        pairs[old] = new
    try:
        target, image = tgt.from_args(args)
        changes = rename(target, image, pairs, args.dry_run, args.outside)
    except tgt.TargetError as e:
        print("dread rename: %s" % e, file=sys.stderr)
        return 1
    for path, text, move in changes:
        print("%s %s%s" % ("would change" if args.dry_run else "changed", os.path.relpath(path),
                           " -> %s" % os.path.relpath(move) if move else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
