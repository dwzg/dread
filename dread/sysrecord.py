"""dread sysrecord: written cases recorded from a system run, for the functions it enters.

The case generator invents inputs; a system run has real ones: a line typed at the serial port, a
configuration the startup parses, an interrupt's data. This runs a scenario on the original image
(dread.sysrun) and records the calls of chosen functions: at the entry the argument registers, then
everything the call reads until it returns:

  memory      every location outside the image and outside the call's own frames, at its first
              value (callees included: they run for real when the case is replayed);
  frames      what the call reads of its callers' frames (above its stack pointer at entry, below
              the highest one its context had: a task's stack is small, and what lies above it, its
              TCB or other data, is memory): when an argument register points there (a local buffer
              of the caller), those frames move into scratch memory (FRAME_RELOC) with the argument,
              since the harness's own stack lies where the firmware's did; otherwise the words just
              above the stack pointer are stack arguments;
  devices     every peripheral register as the sequence of values it read, as poll stages (the case
              field "poll": from the n-th read on, this value).

An exception suspends a call: the handler's reads are not the call's, nor those of the tasks that
run before its own runs again (Recorder: calls belong to contexts). What they write meanwhile into
memory the call has seen becomes a later stage of it ("later": just before the n-th read of that
address, the value it had), so a call that waited for a queue, a semaphore or a tick replays as it
ran. Interrupt handlers are recorded up to their exception return.

A call that runs only blocks of the function that its earlier recorded calls ran is not recorded
(unless --all), so the PER * 8 recordings a function gets at most are different paths.

Each recording becomes a case with real callees ("calls": []); it is replayed on the original in the
function-level harness and kept only when it finishes there with the result register the system run
saw (a handler's r0 is not compared: its return restores the interrupted code's), and, unless --all,
when it runs an instruction of the function that no case of it runs yet (its generated, kept and
written cases). Kept cases are appended to the image's extra_cases/NAME.json
with "by" and "why". The run is deterministic, so the recordings are too.

    dread sysrecord [--target DIR] [--image NAME] [--scenario NAME] [--per N] [--all] [--dry-run] NAME...
"""
import argparse
import json
import os
import sys

from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE

from dread import autocase
from dread import target as tgt
from dread import sysrun
from dread.program import Program

PER = 8                  # recorded calls kept per function at most
STACK_ARGS = 4           # stack words above the entry's sp that count as arguments
FRAME_SPAN = 0x800       # bytes above the entry's sp recorded as the caller's frames
FRAME_RELOC = 0x10000    # where in scratch memory the caller's frames go when an argument points there


class Recorder:
    """Watches a System for calls of `functions` ({entry: name}) and records what each one reads.

    Calls belong to the context they run in: what runs between exceptions (a task's thread, a
    handler). An exception suspends the context it interrupts until the code is back where it was
    interrupted with the stack pointer it had (after a task switch: when that task runs again); a
    handler's context ends with its exception return (the System's returned(), on the M profile),
    or where the interrupted code goes on (where the run does not see the return: RISC-V's mret, the
    A profile's). Calls of one context nest, and each records only while its context runs; a call
    the exception entered (a handler) ends with its context. What other contexts write meanwhile
    into memory a call has read or written (a queue another task filled, a tick count the timer's
    handler moved on) becomes a later stage of that location ("later"), so the replay reads what
    the call read; a call's own writes are followed for that (`seen`: its view of memory)."""

    def __init__(self, system, functions, per=PER * 8, extents=None, distinct=True):
        self.system, self.functions, self.per = system, functions, per
        self.extents = extents or {}    # entry -> (low, high): blocks there are the function's own
        self.distinct = distinct        # keep a call only when it runs a block of the function that
        self.paths = {}                 # the recorded ones did not (name -> the blocks they ran)
        self.uc, self.arch, self.layout = system.uc, system.arch, system.layout
        self.image, self.memory = system.image, system.layout.target.memory
        self.active, self.done, self.running = [], [], []
        self.counts = {}
        self.context, self.next_context = 0, 1
        self.suspended = {}             # (address, sp) where a suspended context goes on -> its id
        self.handler_entry = False      # the next block is the first of a handler
        self.returns_seen = False       # the run reports exception returns (returned())
        self.tops = {}                  # context id -> the highest stack pointer it had (at a block)
        self.uc.hook_add(UC_HOOK_MEM_READ, self._on_read)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._on_write)
        system.observers.append(self)

    def _switch(self, context):
        self.context = context
        self.running = [c for c in self.active if c["context"] == context]

    def _leave(self):
        """The running context ends (its handler returned): the call the exception entered is
        finished, any other one still running is dropped (it never returned)."""
        for call in self.running:
            self.active.remove(call)
            if call["handler"]:
                self._finish(call)
        self.running = []

    def entered(self, resume, sp):
        """An exception is about to be taken: the running context waits for the code to be back
        at `resume` with stack pointer `sp`; the handler runs in a context of its own."""
        self.suspended[(resume & ~1, sp)] = self.context
        self._switch(self.next_context)
        self.next_context += 1
        self.handler_entry = True

    def returned(self, resume):
        """The running context's exception returned, to `resume`."""
        self.returns_seen = True
        self._leave()
        back = self.suspended.pop((resume & ~1, self.uc.reg_read(self.layout.sp)), None)
        if back is None:                    # code that starts here (a task's first run)
            back, self.next_context = self.next_context, self.next_context + 1
        self._switch(back)

    def block(self, addr, size):
        uc = self.uc
        sp = uc.reg_read(self.layout.sp)
        if self.suspended and not self.returns_seen:
            back = self.suspended.pop((addr, sp), None)
            if back is not None:            # a handler returned here without the run seeing it
                self._leave()
                self._switch(back)
        handler, self.handler_entry = self.handler_entry, False
        if sp > self.tops.get(self.context, 0):
            self.tops[self.context] = sp
        running = self.running
        for call in running:
            call["low"] = min(call["low"], sp)
            call["steps"] += 1
            if call["lo"] <= addr < call["hi"]:
                call["blocks"].add(addr)
        while running and addr == running[-1]["ret"] and sp >= running[-1]["sp"]:
            call = running.pop()
            self.active.remove(call)
            self._finish(call)
        name = self.functions.get(addr)
        if name is None or self.counts.get(name, 0) >= self.per or any(c["name"] == name for c in running):
            return
        lo, hi = self.extents.get(addr, (addr, addr + 1))
        call = {"name": name, "entry": addr, "sp": sp, "low": sp, "steps": 0, "context": self.context,
                "lo": lo, "hi": hi, "blocks": {addr},
                "handler": handler, "ret": None if handler else uc.reg_read(self.layout.lr) & ~1,
                "args": [uc.reg_read(r) for r in self.layout.arg_regs],
                "mem": {}, "frame": {}, "io": {}, "seen": {}, "reads": {}, "later": []}
        self.active.append(call)
        running.append(call)

    def _where(self, call, addr):
        """"frame" (its callers' frames), "mem", or None for the call's own frames."""
        if call["sp"] <= addr < min(call["sp"] + FRAME_SPAN, self.tops.get(call["context"], 0)):
            return "frame"
        if call["low"] - 0x100 <= addr < call["sp"]:
            return None
        return "mem"

    def _on_read(self, uc, access, addr, size, value, user):
        self.system.unsettled = True        # the next block repairs the IT state this hook leaves
        try:
            if self.running and not self.image.base <= addr < self.image.end and not self.memory.is_io(addr):
                data = bytes(uc.mem_read(addr, size))
                for call in self.running:
                    where = self._where(call, addr)
                    if where is None:
                        continue
                    n = call["reads"][addr] = call["reads"].get(addr, 0) + 1
                    seen = call["seen"]
                    known = [seen.get(addr + i) for i in range(size)]
                    if any(k is not None and k != b for k, b in zip(known, data)):
                        # another context changed it since the call saw it last
                        call["later"].append([addr, size, n, int.from_bytes(data, "little")])
                    elif addr not in call[where] and None in known:
                        call[where][addr] = (size, int.from_bytes(data, "little"))
                    for i, b in enumerate(data):
                        seen[addr + i] = b
        finally:
            if self.layout.settle_it:         # on every path: a hook that skips it leaves a stale IT state
                self.arch.settle_it(uc)

    def _on_write(self, uc, access, addr, size, value, user):
        self.system.unsettled = True
        try:
            if self.running and not self.image.base <= addr < self.image.end and not self.memory.is_io(addr):
                data = (value & ((1 << 8 * size) - 1)).to_bytes(size, "little")
                for call in self.running:
                    if self._where(call, addr) is not None:
                        seen = call["seen"]
                        for i, b in enumerate(data):
                            seen[addr + i] = b
        finally:
            if self.layout.settle_it:
                self.arch.settle_it(uc)

    def io(self, addr, size, value):
        for call in self.running:
            call["io"].setdefault(addr, (size, []))[1].append(value)

    def _finish(self, call):
        path = self.paths.setdefault(call["name"], set())
        if self.distinct and call["blocks"] <= path:
            return                          # a path an earlier recording ran already
        path |= call["blocks"]
        uc, lay, sp = self.uc, self.layout, call["sp"]
        args, mem, later = list(call["args"]), dict(call["mem"]), [list(x) for x in call["later"]]
        top = min(sp + FRAME_SPAN, self.tops.get(call["context"], 0))
        pointers = [i for i, v in enumerate(args) if sp <= v < top]
        scratch = lay.target.memory.scratch_base
        in_frames = [x for x in later if sp <= x[0] < top]
        if pointers and scratch is not None:
            base = scratch + FRAME_RELOC
            for a, sv in call["frame"].items():
                mem[base + (a - sp)] = sv
            for i in pointers:
                args[i] = base + (args[i] - sp)
            for x in in_frames:
                x[0] = base + (x[0] - sp)
        else:
            if in_frames:
                return                      # a stage of a stack argument: the harness's stack is elsewhere
            for a, sv in call["frame"].items():
                k = (a - sp) // 4
                if k < STACK_ARGS:
                    while len(args) < len(lay.arg_regs) + k + 1:
                        args.append(int.from_bytes(uc.mem_read(sp + 4 * (len(args) - len(lay.arg_regs)), 4), "little"))
                else:
                    mem[a] = sv
        if any(lay.stack_top - lay.stack_size <= a < lay.stack_top + 0x100 for a in list(mem) + [x[0] for x in later]):
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
                # a handler's r0 is no result: its exception return restores the interrupted code's
                "_ret": None if call["handler"] else uc.reg_read(self.layout.result_regs[0])}
        if later:
            case["later"] = sorted(later)
        self.counts[call["name"]] = self.counts.get(call["name"], 0) + 1
        self.done.append((call["name"], case))

def cached_cases(gen, name, fn, options=None):
    """The generated cases a lift check or a test run cached for a function (with auto()'s
    options, by default none: lifted C), or None when there are none or the cache's key no longer
    fits (the function's bytes, the generator, the image changed)."""
    options = dict(options or {})
    calls = autocase.resolve_calls(gen, options.pop("calls", ()))
    frame = options.pop("frame", 0)
    sizes = {a: gen.program.insn_size(a) for a in fn.insns}
    key = autocase.cache_key(gen.payload, gen.image.base, fn.insns, sizes, fn.entry,
                             dict(options, calls=calls, frame=frame), gen.program.arch, gen.environment())
    cached = autocase.cache_load(os.path.join(gen.target.build, "autocases", gen.image.name, name + ".json"))
    return cached["cases"] if cached and cached.get("key") == key else None


def existing_coverage(gen, name, fn, suite, image):
    """What the function's present cases run (generated, kept, written): the generated ones from
    the cache when it fits (the lift check's: a bench target has no case files), else made anew in
    20 seconds."""
    got = set()
    if suite is not None and name in suite.auto:
        from dread import runner
        cases = [c for _, _, _, _, _, c in autocase.cases_for(runner.RUN, name, fn) if c is not None]
    else:
        cases = cached_cases(gen, name, fn)
        if cases is None:
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
    extents = {program.by_name[n].entry: (program.by_name[n].entry, program.end(program.by_name[n])) for n in names}
    recorder = Recorder(system, functions, extents=extents, distinct=not keep_all)
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
            m.trace_reads(image.base, image.end, gen.code, gen.owners)
            try:
                result = m.call(fn.entry, *case["args"], max_instructions=case["limit"])
            except Exception:
                continue
            if gen.touches_image(m) or (expected is not None and (result.ret & 0xFFFFFFFF) != expected):
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
