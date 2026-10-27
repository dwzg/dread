"""dread record: written cases from Python models of a function's callees, recorded on the original.

The generic stubs of generated cases answer every call from a list and fill every stack pointer a
callee is handed with random bytes. For a function whose behaviour depends on what its callees do
(a parser reading input through getc or read, a walk over a list a callee hands out, a callee that
sets a global), a generated case rarely gets far. Here the callees are modelled in Python by a spec,
the original runs once with the models, and the run becomes a case the generic stubs replay
exactly:

  returns   the answer of every stubbed call, in call order (every stubbed callee counts; one
            without a model answers 0 and writes nothing);
  effects   what a model stored outside the stack, written at the start of that call (words where
            four aligned bytes allow, else bytes);
  fills     for every stack pointer a stubbed callee is handed, the whole region a generic stub
            fills with random bytes (up to the end of the caller's frame, at most 64 bytes), with
            the bytes found there after the model ran: what the model stored, and the caller's own
            locals where it stored nothing. Without them the random bytes would run over the
            caller's frame and hide its branches.

The case also carries its stubs (calls) and frame, so it replays with exactly the stubs it was
recorded with. Each case is replayed with the generic stubs and kept only when the replay finishes
with the same result and the same memory written outside the stack; with --search, only when it
also runs an instruction of the function that no kept case runs. Kept cases are appended to the
image's extra_cases/NAME.json with "by" and "why".

The spec is a Python file:

  MODELS = {callee name: f(m, regs, state) -> answer}
           regs are the argument registers (as many as the architecture has: 4 on ARM, 8 on
           RISC-V; take the first ones by index); a model stores with write(m, address, data) so that the
           case gets it, and reads with m.read(address, n); state is what gen() returned.
  gen(rng) -> (args, mem, state, extra)
           one input: the argument registers, [[address, size, value], ...] set before the run, the
           models' state, and keys merged into the case ("io", "limit", ...).
  CALLS, FRAME (optional)
           the stubs, as auto() takes them, and the size of the function's locals (how far a stub
           may fill behind a pointer into them); default: the function's auto() declaration, and
           for FRAME the stack the prologue reserves (sub sp, sp, #N).
  WHY      (optional) the case's "why".
  SCRATCH  set by dread record before gen() runs: the start of the target's scratch memory, for
           buffers the input needs.

It may import write and Stream from dread.record. A Stream is input that arrives through calls;
its model functions take (m, regs, state) with state["stream"] the Stream: getc (the next byte or
-1), read (buffer in the first argument register, count in the second: stores the bytes, zeros past
the end, answers the count), skip, seek, tell.

Cases are deterministic for a given spec and --tries (the random generator is seeded by the
function's name); with --search they depend on the machine's speed.

    dread record [--target DIR] [--image NAME] NAME SPEC [--tries N] [--search SECONDS] [--dry-run]
"""
import argparse
import importlib.util
import json
import os
import random
import sys
import time

from dread import autocase
from dread import target as tgt
from dread.emu import EmuError

SEED = 23
LIMIT = 2_000_000           # instructions per run


def write(m, address, data):
    """For models: store data and remember it for the case."""
    m.write(address, bytes(data))
    m.model_writes.append((address, bytes(data)))


class Stream:
    """Input that arrives through calls (see the module's docstring)."""

    def __init__(self, data):
        self.data, self.pos = bytes(data), 0

    @staticmethod
    def getc(m, regs, state):
        s = state["stream"]
        if s.pos >= len(s.data):
            return 0xFFFFFFFF
        s.pos += 1
        return s.data[s.pos - 1]

    @staticmethod
    def read(m, regs, state):
        s, buf, n = state["stream"], regs[0], regs[1]
        chunk = s.data[s.pos:s.pos + n]
        s.pos += len(chunk)
        write(m, buf, chunk + bytes(n - len(chunk)))
        return n

    @staticmethod
    def skip(m, regs, state):
        state["stream"].pos += regs[0]
        return 0

    @staticmethod
    def seek(m, regs, state):
        state["stream"].pos = regs[0]
        return 0

    @staticmethod
    def tell(m, regs, state):
        return state["stream"].pos


def pieces(address, data):
    """(address, size, value) of data as aligned words where four bytes allow, else bytes."""
    out, i = [], 0
    while i < len(data):
        a = address + i
        if a % 4 == 0 and i + 4 <= len(data):
            out.append((a, 4, int.from_bytes(data[i:i + 4], "little")))
            i += 4
        else:
            out.append((a, 1, data[i]))
            i += 1
    return out


def load_spec(path):
    spec = importlib.util.spec_from_file_location("dread_record_spec", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for need in ("MODELS", "gen"):
        if not hasattr(module, need):
            raise tgt.TargetError("%s: the spec has no %s" % (path, need))
    return module


def record(gen, name, fn, calls, frame, spec, rng, limit=LIMIT):
    """One input of the spec run on the original with the models: (case, result), or None when the
    run does not finish."""
    lay = gen.layout
    args, mem, state, extra = spec.gen(rng)
    args = list(args) + [0] * (len(lay.arg_regs) - len(args))
    models = {}
    for callee, model in spec.MODELS.items():
        if callee not in gen.program.by_name:
            raise tgt.TargetError("the spec models %s, which is no function of the image" % callee)
        models[gen.program.by_name[callee].entry] = model
    m = gen.machine()
    m.plan, m.function, m.model_writes = [], name, []
    for address, size, value in mem:
        m.write(address, (value & ((1 << 8 * size) - 1)).to_bytes(size, "little"))
    answers, effects, fills, number = [], [], [], [0]

    def stub(m, *regs, target, nargs, nstack):
        number[0] += 1
        sp = m.reg("sp")
        first = sp + m.layout.entry_stack_args      # (x86: behind the return address the call pushed)
        seen = [regs[i] for i in range(len(regs)) if nargs >> i & 1] + \
            [int.from_bytes(m.read(first + 4 * i, 4), "little") for i in range(nstack)]
        m.model_writes = []
        model = models.get(target)
        answer = (model(m, list(regs), state) or 0) & 0xFFFFFFFF if model else 0
        for address, data in m.model_writes:
            if not lay.in_stack(address):
                effects.extend([number[0], a, size, value] for a, size, value in pieces(address, data))
        for i, a in enumerate(seen):
            if lay.in_stack(a) and 0 <= a - sp < frame:
                length = min(frame - (a - sp), 64)
                fills.extend([number[0], i, offset - a, size, value]
                             for offset, size, value in pieces(a, m.read(a, length)))
        answers.append(answer)
        return answer

    for target, nargs, *rest in calls:
        nstack = rest[0] if rest else 0
        m.stub(target, lambda m, *r, target=target, nargs=nargs, nstack=nstack:
               stub(m, *r, target=target, nargs=nargs, nstack=nstack))
    try:
        result = m.call(fn.entry, *args, max_instructions=limit)
    except EmuError:
        return None
    case = {"args": args, "mem": [list(x) for x in mem], "returns": answers, "effects": effects, "fills": fills,
            "calls": [list(c) for c in calls], "frame": frame, **extra}
    return case, result


def replay(gen, name, fn, case, limit=LIMIT):
    """The case run with the generic stubs: (result, covered instructions), or None."""
    m = gen.machine()
    m.plan, m.function = [], name
    gen.apply(m, case)
    m.cover(fn.insns[0], gen.program.end(fn))
    try:
        result = m.call(fn.entry, *case["args"], max_instructions=case.get("limit", limit))
    except EmuError:
        return None
    return result, m.covered_insns(fn.insns)


def prologue_frame(program, fn):
    """The stack a function's prologue reserves for its locals (the architecture's reading:
    arch.prologue_frame)."""
    return program.arch.prologue_frame(program, fn)


def stubs_for(gen, suite, name, spec):
    """(calls, frame): the spec's CALLS and FRAME, else the function's auto() declaration; the frame
    from the prologue when neither gives one."""
    fn = gen.program.by_name[name]
    if hasattr(spec, "CALLS"):
        frame = getattr(spec, "FRAME", None)
        return autocase.resolve_calls(gen, spec.CALLS), prologue_frame(gen.program, fn) if frame is None else frame
    if suite is not None and name in suite.auto:
        options = suite.auto[name]
        return autocase.resolve_calls(gen, options.get("calls", ())), options.get("frame", 0)
    raise tgt.TargetError("%s has no auto() declaration and the spec no CALLS: which callees are stubbed?" % name)


def run(target, image, name, spec_path, tries=1, seconds=None, dry_run=False, out=print, suite=None):
    """Record cases; returns the kept cases."""
    gen = autocase.Generator(target, image)
    fn = gen.program.by_name.get(name)
    if fn is None:
        raise tgt.TargetError("no function %s in image %s" % (name, image.name))
    spec = load_spec(spec_path)
    spec.SCRATCH = gen.scratch
    calls, frame = stubs_for(gen, suite, name, spec)
    rng = random.Random("%s/%d" % (name, SEED))
    kept, covered, made, finished = [], set(), 0, 0
    deadline = time.time() + seconds if seconds else None
    while (made < tries) if deadline is None else (time.time() < deadline):
        made += 1
        got = record(gen, name, fn, calls, frame, spec, rng)
        if got is None:
            continue
        case, result = got
        again = replay(gen, name, fn, case)
        if again is None or (again[0].ret, again[0].ranges) != (result.ret, result.ranges):
            continue
        finished += 1
        if deadline is not None and not again[1] - covered:
            continue
        covered |= again[1]
        case["by"] = "dread record"
        case["why"] = getattr(spec, "WHY", "recorded with the models of %s" % os.path.basename(spec_path))
        kept.append(case)
    out("%s: %d inputs, %d replayed exactly, %d kept, %d of %d instructions covered"
        % (name, made, finished, len(kept), len(covered & set(fn.insns)), len(fn.insns)))
    if kept and not dry_run:
        path = os.path.join(image.extra_cases, name + ".json")
        old = []
        if os.path.exists(path):
            with open(path) as f:
                old = json.load(f)["cases"]
        os.makedirs(image.extra_cases, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"cases": old + kept}, f, indent=1, sort_keys=True)
        out("appended to %s" % os.path.relpath(path, target.root))
    return kept


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread record", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("name")
    parser.add_argument("spec")
    parser.add_argument("--tries", type=int, default=1, help="inputs of the spec to record (default 1)")
    parser.add_argument("--search", type=float, metavar="SECONDS",
                        help="record for this long and keep only cases that cover something new")
    parser.add_argument("--dry-run", action="store_true", help="print what would be kept, write nothing")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        from dread.cases import Suite
        from dread.emu import Layout
        from dread.program import Program
        program = Program(target, image)
        suite = Suite(target, image, program, Layout(target, image)).load()
        kept = run(target, image, args.name, args.spec, args.tries, args.search, args.dry_run, suite=suite)
    except (tgt.TargetError, EmuError) as e:
        print("dread record: %s" % e, file=sys.stderr)
        return 1
    return 0 if kept else 1


if __name__ == "__main__":
    sys.exit(main())
