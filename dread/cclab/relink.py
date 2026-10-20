"""dread cclab's relink driver: the compile step of a relink adapter (adapters.relink_adapter). It
copies the object a compiler made elsewhere for this build and file instead of compiling:

    relink.py BUNDLE_ADAPTER_DIR OBJ        OBJ is WORK/ADAPTER/BUILD/obj/NAME.o (the lab's layout)
    relink.py BUNDLE_ADAPTER_DIR --version  the bundle's version.txt

Run by its file path, as dread.cclab.ce is: it needs nothing of DREAD."""
import os
import shutil
import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: relink.py BUNDLE_ADAPTER_DIR OBJ|--version")
        return 2
    bundle, what = argv
    if what == "--version":
        path = os.path.join(bundle, "version.txt")
        print(open(path).read().strip() if os.path.exists(path) else "unknown (the bundle has no version.txt)")
        return 0
    build = os.path.basename(os.path.dirname(os.path.dirname(what)))
    source = os.path.join(bundle, build, "obj", os.path.basename(what))
    if not os.path.exists(source):
        print("relink: the bundle has no object %s" % source)
        return 1
    shutil.copyfile(source, what)
    return 0


if __name__ == "__main__":
    sys.exit(main())
