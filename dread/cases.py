"""The test cases of a target: what a case is, and the API case files are written in.

A case calls one function with given registers and memory, in the original image and in the
rebuilt one, and the two results must agree (dread.emu.Result.diff). Case files are Python files in
the image's `cases` directory (dread.toml, default tests/cases). They run with these names already
defined, so a file needs no imports:

    case(name, label, setup, *args, limit=5_000_000, addr=None)
        One case of the function `name`: setup(m) prepares the Machine m (memory, stubs, hooks)
        the same way for both images; args go into the argument registers, further ones onto the
        stack. addr defaults to the function's entry.
    auto(name, **options)
        Have cases generated from the original (dread.autocase), with options for the generator.
    unreachable(name, reason, *addresses)
        Instructions of the original no input can reach, with the reason. The runner fails a
        function whose cases leave an instruction unexecuted unless it is declared here, and fails
        it as well when a declared one does execute.
    entry(name)
        A function's entry from the function table: stub addresses are looked up, never typed.
    logging_stubs(m, (name, result, n[, effect]), ..., seen=lambda: ())
        Stubs that log the callee's name, its first n argument registers and seen(), return
        `result` and run effect(m, *args) if given.
    words(m, (address, format, value), ...)
        struct-packed writes.
    target, image, program, layout, SCRATCH, STACK_TOP
        The target, its image, the program database, the emulator layout, the scratch area and
        the top of the stack.

What is compared: the return value (with m.wide_result also the second result register), the
callee-saved registers at the return, the scratch registers the function's callers read after the
call (dread.regflow; the runner sets m.kept), every byte written outside the stack, and the log:
stub calls (whatever the stubs log) and every peripheral access in order. A case where either image
faults, or does not return within its limit, fails (m.may_hang makes not returning a result).
"""
import os
import struct


def confine_args(rows, args):
    """The arguments of a case, each register named in rows [(number, values)] kept to its values:
    a value in the list stays as it is, so applying it twice changes nothing; another value v
    becomes values[v % len(values)]."""
    args = list(args)
    for n, values in rows:
        if n < len(args):
            v = args[n] & 0xFFFFFFFF
            if v not in values:
                args[n] = values[v % len(values)]
    return args


class Suite:
    """The cases of one image: filled by running its case files."""

    def __init__(self, target, image, program, layout):
        self.target, self.image, self.program, self.layout = target, image, program, layout
        self.cases = {}             # name -> [(label, address, setup, args, limit)]
        self.auto = {}              # name -> options of the generator
        self.unreachable = {}       # name -> {address}
        self.caller_regs = {}       # name -> [register numbers] read before written (lifter)
        self.files = []

    # ---- the API ----

    def entry(self, name):
        fn = self.program.by_name.get(name)
        if fn is None:
            raise KeyError("%s is not in the function table %s" % (name, self.image.table("functions")))
        return fn.entry

    def case(self, name, label, setup, *args, limit=5_000_000, addr=None):
        self.cases.setdefault(name, []).append((label, self.entry(name) if addr is None else addr,
                                                setup, args, limit))

    def auto_(self, name, **options):
        self.entry(name)
        self.auto[name] = options

    def unreachable_(self, name, reason, *addrs):
        if not reason:
            raise ValueError("unreachable() needs the reason")
        self.unreachable.setdefault(name, set()).update(addrs)

    def logging_stubs(self, m, *specs, seen=lambda: ()):
        for name, ret, n, *effect in specs:
            def stub(m, *a, name=name, ret=ret, n=n, effect=effect):
                m.log(name, *a[:n], *seen())
                if effect:
                    effect[0](m, *a)
                return ret
            m.stub(self.entry(name), stub)

    @staticmethod
    def words(m, *items):
        for address, fmt, value in items:
            m.write(address, struct.pack("<" + fmt, *(value if isinstance(value, tuple) else (value,))))

    def namespace(self):
        lay = self.layout
        return {
            "case": self.case, "auto": self.auto_, "unreachable": self.unreachable_,
            "entry": self.entry, "logging_stubs": self.logging_stubs, "words": self.words,
            "target": self.target, "image": self.image, "program": self.program, "layout": lay,
            "SCRATCH": self.target.memory.scratch_base, "STACK_TOP": lay.stack_top,
            "struct": struct,
        }

    def load(self, directory=None):
        """Run every case file (*.py, in name order) of the image's cases directory."""
        directory = directory or self.image.cases
        if not os.path.isdir(directory):
            return self
        for name in sorted(os.listdir(directory)):
            if name.endswith(".py") and not name.startswith("_"):
                path = os.path.join(directory, name)
                with open(path) as f:
                    code = compile(f.read(), path, "exec")
                exec(code, dict(self.namespace(), __file__=path, __name__="dread_cases_" + name[:-3]))
                self.files.append(path)
        return self
