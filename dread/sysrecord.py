"""dread sysrecord: written cases recorded from a system run, for the functions it enters.

The case generator invents inputs; a system run has real ones: a line typed at the serial port, a
configuration the startup parses, an interrupt's data. This runs a scenario on the original image
(dread.sysrun) and records the calls of chosen functions: at the entry the argument registers, then
everything the call reads until it returns:

  memory      every location outside the image and outside the call's own frames, at its first
              value (callees included: they run for real when the case is replayed);
  frames      what the call reads of its callers' frames (above its stack pointer at entry): when an
              argument register points there (a local buffer of the caller), those frames move into
              scratch memory (FRAME_RELOC) with the argument, since the harness's own stack lies
              where the firmware's did; otherwise the words just above the stack pointer are stack
              arguments;
  devices     every peripheral register as the sequence of values it read, as poll stages (the case
              field "poll": from the n-th read on, this value).

A call during which an interrupt was taken is not recorded (the handler's reads are not the call's).
Each recording becomes a case with real callees ("calls": []); it is replayed on the original in the
function-level harness and kept only when it finishes there with the result register the system run
saw, and, unless --all, when it runs an instruction of the function that no case of it runs yet (its
generated, kept and written cases). Kept cases are appended to the image's extra_cases/NAME.json
with "by" and "why". The run is deterministic, so the recordings are too.

    dread sysrecord [--target DIR] [--image NAME] [--scenario NAME] [--per N] [--all] [--dry-run] NAME...
"""
import argparse
import json
import os
import sys

from unicorn import UC_HOOK_MEM_READ

from dread import autocase
from dread import target as tgt
from dread import sysrun
from dread.program import Program

PER = 8                  # recorded calls kept per function at most
STACK_ARGS = 4           # stack words above the entry's sp that count as arguments
FRAME_SPAN = 0x800       # bytes above the entry's sp recorded as the caller's frames
FRAME_RELOC = 0x10000    # where in scratch memory the caller's frames go when an argument points there


class Recorder:
    """Watches a System for calls of `functions` ({entry: name}). Calls may nest (a recorded
    function calling another one): each active call records what is read while it runs."""

    def __init__(self, system, functions, per=PER * 8):
        self.system, self.functions, self.per = system, functions, per
        self.uc, self.arch, self.layout = system.uc, system.arch, system.layout
        self.active, self.done = [], []
        self.counts = {}
        self.uc.hook_add(UC_HOOK_MEM_READ, self._on_read)
        system.observers.append(self)

    def block(self, addr, size):
        uc = self.uc
        sp = uc.reg_read(self.layout.sp)
        for call in self.active:
            call["low"] = min(call["low"], sp)
            call["steps"] += 1
        while self.active and addr == self.active[-1]["ret"] and sp >= self.active[-1]["sp"]:
            self._finish(self.active.pop())
        name = self.functions.get(addr)
        if name is None or self.counts.get(name, 0) >= self.per or any(c["name"] == name for c in self.active):
            return
        self.active.append({"name": name, "entry": addr, "sp": sp, "low": sp, "steps": 0,
                            "ret": uc.reg_read(self.layout.lr) & ~1,
                            "args": [uc.reg_read(r) for r in self.layout.arg_regs],
                            "mem": {}, "frame": {}, "io": {}, "interrupts": sum(self.system.interrupts.values())})

    def _on_read(self, uc, access, addr, size, value, user):
        self.system.unsettled = True        # the next block repairs the IT state this hook leaves
        try:
            if self.active and not self.system.image.base <= addr < self.system.image.end \
                    and not self.layout.target.memory.is_io(addr):
                for call in self.active:
                    where = "frame" if call["sp"] <= addr < call["sp"] + FRAME_SPAN else "mem"
                    if where == "mem" and call["low"] - 0x100 <= addr < call["sp"]:
                        continue                    # the call's own frames
                    if addr not in call[where]:
                        call[where][addr] = (size, int.from_bytes(uc.mem_read(addr, size), "little"))
        finally:
            if self.layout.settle_it:         # on every path: a hook that skips it leaves a stale IT state
                self.arch.settle_it(uc)

    def io(self, addr, size, value):
        for call in self.active:
            call["io"].setdefault(addr, (size, []))[1].append(value)

    def _finish(self, call):
        if sum(self.system.interrupts.values()) != call["interrupts"]:
            return                          # an interrupt came in between: its reads are not the call's
        uc, lay, sp = self.uc, self.layout, call["sp"]
        args, mem = list(call["args"]), dict(call["mem"])
        pointers = [i for i, v in enumerate(args) if sp <= v < sp + FRAME_SPAN]
        scratch = lay.target.memory.scratch_base
        if pointers and scratch is not None:
            base = scratch + FRAME_RELOC
            for a, sv in call["frame"].items():
                mem[base + (a - sp)] = sv
            for i in pointers:
                args[i] = base + (args[i] - sp)
        else:
            for a, sv in call["frame"].items():
                k = (a - sp) // 4
                if k < STACK_ARGS:
                    while len(args) < len(lay.arg_regs) + k + 1:
                        args.append(int.from_bytes(uc.mem_read(sp + 4 * (len(args) - len(lay.arg_regs)), 4), "little"))
                else:
                    mem[a] = sv
        if any(lay.stack_top - lay.stack_size <= a < lay.stack_top + 0x100 for a in mem):
            self.dropped = getattr(self, "dropped", 0) + 1
            return                          # it would read what the harness keeps on its own stack
        poll = []
        for addr, (size, values) in sorted(call["io"].items()):
            last = None
            for n, v in enumerate(values, 1):
                if v != last:
                    poll.append([addr, n, v])
                    last = v
        case = {"args": args, "mem": [[a, s, v] for a, (s, v) in sorted(mem.items())], "poll": poll,
                "returns": [], "calls": [], "frame": 0, "limit": max(200_000, call["steps"] * 64),
                "_ret": uc.reg_read(self.layout.result_regs[0])}
        self.counts[call["name"]] = self.counts.get(call["name"], 0) + 1
        self.done.append((call["name"], case))

def existing_coverage(gen, name, fn, suite, image):
    """What the function's present cases run (generated, kept, written)."""
    got = set()
    if suite is not None and name in suite.auto:
        from dread import runner
        cases = [c for _, _, _, _, _, c in autocase.cases_for(runner.RUN, name, fn) if c is not None]
    else:
        try:
            cases = gen.generate(name, fn.entry, fn.insns, seconds=20)
        except autocase.GenerationError:
            cases = []
    cases += autocase.extra_load(image, name)
    for c in cases:
        got |= gen.coverage(fn.entry, fn.insns, c, c.get("limit", 200_000))
    return got


def record(target, image, names, scenario, per=PER, keep_all=False, dry_run=False, out=print, suite=None):
    """Run the scenario, record calls of `names`, keep cases; returns {name: [kept cases]}."""
    program = Program(target, image)
    functions = {}
    for name in names:
        fn = program.by_name.get(name)
        if fn is None:
            raise tgt.TargetError("no function %s in image %s" % (name, image.name))
        functions[fn.entry] = name
    system = sysrun.System(target, image, image.payload(), program=program, scenario=scenario)
    recorder = Recorder(system, functions)
    outcome = system.run(int(scenario.get("limit", sysrun.LIMIT)))
    out("system run: %s; %d calls recorded" % (outcome.how, len(recorder.done)))
    gen = autocase.Generator(target, image, program=program)
    kept = {}
    for name in names:
        fn = program.by_name[name]
        recordings = [c for n, c in recorder.done if n == name]
        covered = set() if keep_all else existing_coverage(gen, name, fn, suite, image)
        before, good = len(covered & set(fn.insns)), 0
        for case in recordings:
            expected = case.pop("_ret")
            m = gen.machine()
            m.plan, m.function = [], name
            gen.apply(m, case)
            m.cover(fn.insns[0], program.end(fn))
            m.trace_reads(image.base, image.end, gen.code)
            try:
                result = m.call(fn.entry, *case["args"], max_instructions=case["limit"])
            except Exception:
                continue
            if gen.touches_image(m) or (result.ret & 0xFFFFFFFF) != expected:
                continue
            good += 1
            got = m.covered_insns(fn.insns)
            if (keep_all or got - covered) and len(kept.get(name, ())) < per:
                covered |= got
                case["by"] = "dread sysrecord"
                case["why"] = "recorded in the system run of scenario %s" % scenario.get("_name", "?")
                kept.setdefault(name, []).append(case)
        out("%-24s %d calls recorded, %d replay with the same result, %d kept; %d -> %d of %d instructions"
            % (name, len(recordings), good, len(kept.get(name, ())), before, len(covered & set(fn.insns)), len(fn.insns)))
        if kept.get(name) and not dry_run:
            path = os.path.join(image.extra_cases, name + ".json")
            old = []
            if os.path.exists(path):
                with open(path) as f:
                    old = json.load(f)["cases"]
            os.makedirs(image.extra_cases, exist_ok=True)
            with open(path, "w") as f:
                json.dump({"cases": old + kept[name]}, f, indent=1, sort_keys=True)
    return kept


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread sysrecord", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--scenario", help="a table of [scenarios] (default: the first one)")
    parser.add_argument("--per", type=int, default=PER, help="cases kept per function at most (default %d)" % PER)
    parser.add_argument("--all", action="store_true", help="keep every call that replays, not only new coverage")
    parser.add_argument("--dry-run", action="store_true", help="print what would be kept, write nothing")
    parser.add_argument("names", nargs="+")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        scenarios = target.scenarios
        name = args.scenario or (sorted(scenarios)[0] if scenarios else None)
        if name is None or name not in scenarios:
            raise tgt.TargetError("no scenario %s (there are: %s)" % (name, ", ".join(sorted(scenarios)) or "none"))
        scenario = dict(scenarios[name], _name=name)
        from dread.cases import Suite
        from dread.emu import Layout
        suite = Suite(target, image, Program(target, image), Layout(target, image)).load()
        kept = record(target, image, args.names, scenario, args.per, args.all, args.dry_run, suite=suite)
    except (tgt.TargetError, OSError, ValueError) as e:
        print("dread sysrecord: %s" % e, file=sys.stderr)
        return 1
    return 0 if kept else 1


if __name__ == "__main__":
    sys.exit(main())
