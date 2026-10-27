"""dread COMMAND [OPTIONS]: the commands of the toolkit.

Every command is a module with main(argv); `dread COMMAND --help` shows its options, and
`python -m dread.MODULE` runs the same. A main builds its argparse parser first and parses before it
does any work: parser(COMMAND) reads a command's parser that way without running it (dread gui makes
a form of it).
"""
import argparse
import importlib
import sys
import threading

COMMANDS = {
    "doctor": ("dread.doctor", "what DREAD needs on this machine, what it finds, and what to install"),
    "new": ("dread.newtarget", "a new target from an ELF or a raw image, and what to run next"),
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
    "callconv": ("dread.callconv", "the registers each function reads and changes, measured against regflow"),
    "whatcc": ("dread.whatcc", "which toolchain's libraries an image links, and which functions are theirs"),
    "record": ("dread.record", "written cases from Python models of a function's callees"),
    "sysrun": ("dread.sysrun", "the image from reset with device models, original against rebuilt"),
    "sysrecord": ("dread.sysrecord", "written cases recorded from a system run, for the functions it enters"),
    "flat": ("dread.flat", "one function's code as a flat listing to read"),
    "rename": ("dread.rename", "give functions new names, everywhere a name is written"),
    "gui": ("dread.gui", "the web browser view: browse an image (listings, C, coverage, verdicts), run every command as a job"),
    "callsites": ("dread.callsites", "the calls of functions with the constant arguments at each"),
    "relations": ("dread.relations", "a function's neighbourhood as a graph: calls, memory, texts, structures"),
    "groups": ("dread.groups", "the functions grouped by their code alone: the subsystems a reader would see"),
    "samecode": ("dread.samecode", "functions that are the same code as functions of another image"),
    "callpair": ("dread.callpair", "names for another image's functions from the order of calls"),
    "noreturn": ("dread.noreturn", "the functions that never return"),
    "lift": ("dread.lift", "translate functions to C instruction by instruction"),
    "uncovered": ("dread.uncovered", "why the lift check's cases leave code unrun: each region's way in and its source"),
    "readable": ("dread.readable", "turn lifted C into readable C, each function checked as lifted C is"),
    "status": ("dread.status", "the README's status section, from what the repository records"),
    "trace": ("dread.trace", "one case in the original and the rebuilt image, to the first point where they part"),
    "liftfuzz": ("dread.liftfuzz", "check the lifter on random instructions against the emulator"),
    "pilot": ("dread.pilot", "the reconstruction loop run by code, a language model only for the judgement"),
    "llm": ("dread.llm", "the language-model providers: list them, set them up (llm.toml, keys), or ask one a question"),
    "bench": ("dread.bench", "build a real project in many ways and measure what DREAD makes of it"),
    "cclab": ("dread.cclab", "put a compiler in and learn how it compiles: probes run in the emulator"),
    "leakcheck": ("dread.leakcheck", "look for a private project's names and addresses before publishing"),
}


class NoParser(Exception):
    """A command whose main did not parse its arguments with argparse before anything else."""


class _Parsed(BaseException):        # (not an Exception: a main's `except Exception` must not catch it)
    def __init__(self, parser):
        super().__init__()
        self.parser = parser


_reading = threading.local()
_patch = threading.Lock()


def _stop_at_parse(original):
    def parse(self, *args, **kwargs):
        if getattr(_reading, "on", False):
            raise _Parsed(self)
        return original(self, *args, **kwargs)
    parse.__wrapped__ = original
    return parse


def parser(command):
    """The argparse parser of a command as its main builds it, without running the command: main is
    called with ArgumentParser's parse methods stopped at their first call in this thread (other
    threads parse as usual), so that it hands over its parser before it does any work. That holds as
    long as every main builds its parser first and parses before anything else, which they all do
    (tests/test_cliforms.py calls this for every command in an empty directory and checks that
    nothing was written or printed). Raises NoParser for a main that returned or failed without
    parsing, KeyError for an unknown command. Each call builds a new parser."""
    module = importlib.import_module(COMMANDS[command][0])
    with _patch:
        saved = argparse.ArgumentParser.parse_args, argparse.ArgumentParser.parse_known_args
        argparse.ArgumentParser.parse_args = _stop_at_parse(saved[0])
        argparse.ArgumentParser.parse_known_args = _stop_at_parse(saved[1])
        _reading.on = True
        try:
            module.main([])
        except _Parsed as got:
            return got.parser
        except (Exception, SystemExit) as e:    # (nothing of a command may end the caller)
            raise NoParser("dread %s: main ended before it parsed its arguments (%s: %s)"
                           % (command, type(e).__name__, e)) from e
        finally:
            _reading.on = False
            argparse.ArgumentParser.parse_args, argparse.ArgumentParser.parse_known_args = saved
    raise NoParser("dread %s: main returned without parsing its arguments with argparse" % command)


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
    if hasattr(sys.stdout, "reconfigure"):  # a line as it is printed, also into a file: a long run's log
        sys.stdout.reconfigure(line_buffering=True)     # (dread bench all) showed nothing for an hour
    module = importlib.import_module(entry[0])
    return module.main(argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main())
