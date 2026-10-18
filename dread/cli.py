"""dread COMMAND [OPTIONS]: the commands of the toolkit.

Every command is a module with main(argv); `dread COMMAND --help` shows its options, and
`python -m dread.MODULE` runs the same.
"""
import importlib
import sys

COMMANDS = {
    "info": ("dread.target", "show the target as DREAD reads it"),
    "import-elf": ("dread.elfimport", "an image's tables from an ELF with symbols"),
    "functions": ("dread.listfunctions", "list the functions of an image with their extents"),
    "asm": ("dread.mkasm", "a listing that GNU as rebuilds byte for byte (--verify)"),
    "ghidra": ("dread.ghidra", "analyse an image with Ghidra and write its function and code tables"),
    "recon": ("dread.build", "compile the image's C and link it over the original"),
    "overlay": ("dread.overlay", "link compiled C over the original image"),
    "test": ("dread.runner", "compare every rewritten function with the original in the emulator"),
    "mutate": ("dread.mutate", "break rewritten C on purpose and list the changes no case notices"),
    "integration": ("dread.integration", "run generated inputs with real callees, original against rebuilt"),
    "regflow": ("dread.regflow", "registers callers read after a call, and those a function reads at entry"),
    "record": ("dread.record", "written cases from Python models of a function's callees"),
    "sysrun": ("dread.sysrun", "the image from reset with device models, original against rebuilt"),
    "sysrecord": ("dread.sysrecord", "written cases recorded from a system run, for the functions it enters"),
    "noreturn": ("dread.noreturn", "the functions that never return"),
    "lift": ("dread.lift", "translate functions to C instruction by instruction"),
    "liftfuzz": ("dread.liftfuzz", "check the lifter on random instructions against the emulator"),
    "leakcheck": ("dread.leakcheck", "look for a private project's names and addresses before publishing"),
}


def usage():
    width = max(len(name) for name in COMMANDS)
    lines = ["usage: dread COMMAND [OPTIONS]   (dread COMMAND --help for its options)", "", "commands:"]
    lines += ["  %-*s  %s" % (width, name, text) for name, (_, text) in COMMANDS.items()]
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    if argv[0] in ("-V", "--version"):
        from dread import __version__
        print("dread", __version__)
        return 0
    entry = COMMANDS.get(argv[0])
    if entry is None:
        print("dread: unknown command %r\n\n%s" % (argv[0], usage()), file=sys.stderr)
        return 2
    module = importlib.import_module(entry[0])
    return module.main(argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main())
