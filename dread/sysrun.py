"""dread sysrun: the image from reset with device models, and the original against the rebuilt one.

Everything else tests one function at a time. This starts where the processor starts and lets the
firmware run: its own startup, its initialisation, its main loop. Devices are models (dread.models,
configured in the target's [models] tables): a model owns a range of peripheral registers, answers
reads, takes writes, keeps time and may raise interrupts. Peripheral registers no model owns are
plain memory. Memory starts as after power-on: the image where it is stored, everything else zero;
the firmware's startup does its own copies.

Comparing two images that way (the original and the one with C linked in) is the point: they must do
the same things to the devices in the same order. Time is the shared clock: the bytes of code run
that are the same in both images (the original's code of the functions rewritten in C, and the C
code with the branches left at moved functions' old entries, do not count), plus ACCESS units for
every device access (the accesses must be the same in both anyway, and a rewritten function that
polls a register still lets time pass). So a model that acts after N units acts at the corresponding
place in both runs, however many instructions the C takes. A wait in code that is not shared (a
rebuilt idle loop waiting for an interrupt: FROZEN blocks in a row with an event due, or a chunk in
which the clock does not move at all) moves the clock to the next scheduled event, in both runs
alike. Interrupts are taken behind a block of shared code, or, after such a wait, at once: the code
it interrupts is not shared, so there is no common place to wait for. A controller with priorities
(dread.models.nvic) decides with the core's masks whether one is taken (may_take), so a pending one
waits while masked and comes behind the first shared block after the mask drops, or at once behind
the return of the handler that held it off. svc raises its exception (Cortex-M: SVCall) at once. The device log, every
peripheral access with its value in order plus what the models note (bytes sent), is what is
compared; the functions entered are information (a rewritten function enters what it calls in its
own way).

A run ends at its limit (shared-clock units), when the scenario's end condition holds (text a model
sent; checked at the store that completes it, so both runs stop at the same place), when the
firmware waits with nothing scheduled (QUIET units without a new block or log entry, or a chunk
without any clock: "polls" when it keeps reading one register, "waits" when it accesses no device at
all), or at a fault. A read repeated without
anything between is one log entry with its count.

Scenarios are tables of the target's [scenarios] section:

    [scenarios.boot]
    until = {uart = "READY"}         # end when the model's report "sent" contains the text
    limit = 2_000_000                # shared-clock units
    input = [[0, "uart", "s"]]       # at clock 0, give the model "uart" these bytes
    models = ["uart"]                # default: all models of the target

Output: per image how the run ended, the clock, the models' reports and the functions entered;
for two images the first difference of the device logs with what came before it. Exit status 1 on
a difference.

    dread sysrun [--target DIR] [--image NAME] [--scenario NAME] [--recon PAYLOAD] [--original-only]
                 [--limit N] [--log FILE]
"""
import argparse
import bisect
import collections
import os
import sys

from unicorn import (UC_HOOK_BLOCK, UC_HOOK_INTR, UC_HOOK_MEM_READ, UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_WRITE,
                     UC_MEM_WRITE, UcError)

from dread import models as dmodels
from dread import target as tgt
from dread.build import recon_dir
from dread.emu import Layout
from dread.program import Program

CHUNK = 200_000          # instructions per emu_start (stop rules are checked between chunks)
ACCESS = 8               # shared-clock units a device access takes
QUIET = 200_000          # shared-clock units without a new block or a device access: the firmware waits
FROZEN = 2_000           # blocks in a row outside shared code with an event due: a wait the clock cannot see
LIMIT = 20_000_000       # default shared-clock limit


class Outcome:
    """What one run did."""

    def __init__(self, how, clock, log, reports, entered, order):
        self.how, self.clock, self.log, self.reports, self.entered, self.order = how, clock, log, reports, entered, order


class System:
    """One image from reset, with models. placement: the rebuilt image's placement.json content
    ({name: {...}}) to tell shared code from C; built: the names of the functions built from C (for
    the original, whose code of them does not count)."""

    def __init__(self, target, image, payload, program=None, placement=None, built=(), scenario=None):
        self.target, self.image, self.arch = target, image, image.arch
        self.program = program or Program(target, image)
        self.layout = Layout(target, image)
        self.uc = uc = self.arch.new_uc()
        for lo, hi in self.layout.maps:
            uc.mem_map(lo, hi - lo)
        uc.mem_write(image.base, bytes(payload))
        for c in image.startup():               # what the loader puts into memory besides the image
            if c.kind == "load":
                uc.mem_write(c.run, c.data)
        self.scenario = dict(scenario or {})
        names = list(self.scenario["models"]) if "models" in self.scenario else sorted(target.models)
        self.models = [dmodels.create(self, n, target.models[n], target.root) for n in names]
        self.by_name = {m.name: m for m in self.models}
        self.controllers = [m for m in self.models if hasattr(m, "pending_interrupt")]
        self.irq_check, self.deliver, self.interrupts = False, False, collections.Counter()
        owned = sorted((lo, hi, m) for m in self.models for lo, hi in m.ranges)
        self.owned_lo, self.owned = [lo for lo, _, _ in owned], owned
        self.log, self.clock, self.new, self.stop = [], 0, 0, None
        self.next_event = None
        self.levels = {}
        self.blocks, self.entered, self.order = set(), collections.Counter(), []
        self.last_new, self.last_access, self.limit = 0, 0, LIMIT
        self.frozen = 0                     # blocks in a row outside shared code
        # A stop asked for by a peripheral access, taken where the next block starts: stopped inside a
        # memory hook, Unicorn leaves pc on the accessing instruction (a store done, a load not), and
        # going on from pc would repeat the access (dread.engines)
        self.due = False
        self.observers = []                 # tools that watch the run (dread.sysrecord): block(), io()
        self._shared(placement, built)
        uc.hook_add(UC_HOOK_BLOCK, self._on_block)
        for lo, hi in self.layout.io:
            uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self._on_io, begin=lo, end=hi - 1)
        self.fault = None
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._on_unmapped)
        # An exception the core raises itself: svc (taken through a controller with pend(), the M
        # profile's SVCall) and the start of an M-profile exception return (finished by the layer);
        # any other is a fault. Without the hook Unicorn stops at each with an error; with it, it
        # would go on behind the instruction, so the hook stops the run there.
        self.syscall = False
        uc.hook_add(UC_HOOK_INTR, self._on_intr)

    # ---- what counts ----

    def _shared(self, placement, built):
        """The code that is not the same in both images, and where functions are entered."""
        prog = self.program
        self.entries = {fn.entry: fn.entry for fn in prog.functions}
        excluded = []
        if placement:                       # the rebuilt image: its C code and the branches to it
            for name, p in placement.items():
                excluded.append((p["address"], p["address"] + p["c_bytes"]))
                if p.get("branch"):
                    excluded.append((p["entry"], p["entry"] + 4))
                if p["placement"] != "helper" and p["address"] != p["entry"]:
                    self.entries[p["address"]] = p["entry"]
        for name in built:                  # the original: the code the C replaces
            fn = prog.by_name.get(name)
            if fn is not None and fn.insns:
                excluded.append((fn.insns[0], prog.end(fn)))
        self.excluded = sorted(excluded)
        self.excluded_lo = [lo for lo, _ in self.excluded]

    def is_shared(self, address):
        i = bisect.bisect_right(self.excluded_lo, address) - 1
        return not (i >= 0 and address < self.excluded[i][1])

    # ---- for models ----

    def note(self, model, what, value):
        """A model reports something (a byte sent): part of the device log."""
        self.log.append((model, what, value))
        self.new += 1
        self.last_new = self.clock
        until = self.scenario.get("until") or {}
        if model in until:
            m = self.by_name[model]
            if str(until[model]) in m.report().get("sent", ""):
                self.request_stop("until: %s sent %r" % (model, str(until[model])))

    def request_stop(self, how):
        if self.stop is None:
            self.stop = how
        self.uc.emu_stop()

    def interrupt_level(self, number, level):
        """A model's interrupt line, handed to the interrupt controller models."""
        self.levels[number] = level
        for c in self.controllers:
            c.line(number, level)
        self.interrupts_changed()

    def interrupts_changed(self):
        """Something may now ask for an interrupt: checked behind the next block of shared code."""
        self.irq_check = True

    def _wanted(self):
        """The interrupt a controller asks for, or None."""
        for c in self.controllers:
            number = c.pending_interrupt()
            if number is not None:
                return c, number
        return None

    def _may_take(self, controller, number):
        """Whether the core takes that interrupt now: the controller decides with the core's masks
        when it has priorities (may_take), else the layer (interrupts_enabled)."""
        may = getattr(controller, "may_take", None)
        if may is not None:
            return may(number, self.arch.cpu_masks(self.uc))
        return self.arch.interrupts_enabled(self.uc)

    def schedule(self):
        times = [t for t in (m.next_event() for m in self.models) if t is not None]
        times += [t for t, _, _ in self.pending_input]
        self.next_event = min(times) if times else None

    # ---- hooks ----

    def _on_block(self, uc, addr, size, user):
        if self.due:
            self.due = False
            uc.emu_stop()                   # this block runs (and counts) when the run goes on
            return
        for o in self.observers:
            o.block(addr, size)
        if addr not in self.blocks:
            self.blocks.add(addr)
            self.new += 1
            self.last_new = self.clock
        entry = self.entries.get(addr)
        if entry is not None:
            if not self.entered[entry]:
                self.order.append(entry)
            self.entered[entry] += 1
        if not self.is_shared(addr):
            self.frozen += 1                # a wait in code built from C (a rebuilt idle loop): stop the
            if self.frozen >= FROZEN and self.next_event is not None:      # chunk, the run moves the
                uc.emu_stop()                                               # clock on to the event
        else:
            self.frozen = 0
            self.clock += size
            if (self.next_event is not None and self.clock >= self.next_event) or self.clock >= self.limit:
                uc.emu_stop()
            if self.irq_check:
                wanted = self._wanted()
                if wanted is None:
                    self.irq_check = False
                elif self._may_take(*wanted):
                    self.deliver = True
                    uc.emu_stop()
        if self.layout.settle_it:
            self.arch.settle_it(uc)

    def _owner(self, address):
        i = bisect.bisect_right(self.owned_lo, address) - 1
        if i >= 0 and address < self.owned[i][1]:
            return self.owned[i][2]
        return None

    def _on_io(self, uc, access, addr, size, value, user):
        model, mask = self._owner(addr), (1 << 8 * size) - 1
        who = model.name if model else "-"
        self.clock += ACCESS
        self.last_access = self.clock
        if access == UC_MEM_WRITE:
            self.log.append(("write", who, addr, size, value & mask))
            self.new += 1
            self.last_new = self.clock
            if model is not None:
                model.write(addr, size, value & mask)
        else:
            if model is not None:
                got = model.read(addr, size)
                if got is not None:
                    uc.mem_write(addr, (got & mask).to_bytes(size, "little"))
            got = int.from_bytes(uc.mem_read(addr, size), "little")
            for o in self.observers:
                o.io(addr, size, got)
            last = self.log[-1] if self.log else None
            if last is not None and last[:5] == ("read", who, addr, size, got):
                self.log[-1] = last[:5] + (last[5] + 1,)        # the same read again: one entry, counted
            else:
                self.log.append(("read", who, addr, size, got, 1))
                self.new += 1
                self.last_new = self.clock
        if (self.next_event is not None and self.clock >= self.next_event) or self.clock >= self.limit:
            self.due = True                 # (not emu_stop here: see self.due)
        if self.layout.settle_it:
            self.arch.settle_it(uc)

    def _on_intr(self, uc, intno, user):
        kind = getattr(self.arch, "system_exception", lambda n: None)(intno)
        if kind == "syscall":
            self.syscall = True
        elif kind != "return" and self.fault is None:
            self.fault = "CPU exception %d at pc 0x%x" % (intno, uc.reg_read(self.layout.pc))
        uc.emu_stop()

    def _on_unmapped(self, uc, access, addr, size, value, user):
        if self.fault is None:
            self.fault = "access to unmapped memory 0x%x (%d bytes) at pc 0x%x" % (
                addr, size, uc.reg_read(self.layout.pc))
        return False

    # ---- the run ----

    def run(self, limit=LIMIT):
        uc, arch = self.uc, self.arch
        self.limit = limit
        self.pending_input = sorted((int(t), str(m), d) for t, m, d in self.scenario.get("input", ()))

        def word(address):
            return int.from_bytes(uc.mem_read(address, 4), "little")

        def write_word(address, value):
            uc.mem_write(address, (value & 0xFFFFFFFF).to_bytes(4, "little"))
        pc = arch.reset(uc, word, self.image.entry, self.image.vectors)
        self.schedule()

        def take(pc):
            wanted = self._wanted()
            if wanted is None or not self._may_take(*wanted):
                return pc
            controller, number = wanted
            controller.taken(number)
            self.interrupts[number] += 1
            self.log.append(("interrupt", controller.name, number))
            vectors = controller.vector_base() if hasattr(controller, "vector_base") else self.image.vectors
            pc = arch.enter_interrupt(uc, number, pc, vectors, word, write_word)
            self.interrupts_changed()
            return pc
        how = None
        while True:
            self.new, clock_before = 0, self.clock
            error = None
            try:
                uc.emu_start(pc, self.layout.sentinel, count=CHUNK)
            except UcError as e:
                error = e
            back = arch.exception_return(uc, word)
            if back is not None:                # the M profile's return from an exception, finished here
                self.fault, pc = None, back
                for c in self.controllers:
                    c.returned()
                self.interrupts_changed()
                pc = take(pc)                   # one that waited for it comes at once (tail-chaining)
                continue
            if error is not None or self.fault is not None:
                how = self.fault or "emulator error at pc 0x%x: %s" % (uc.reg_read(self.layout.pc), error)
                break
            pc = arch.resume_address(uc)
            if self.syscall:                    # svc: its exception, taken at once behind it
                self.syscall = False
                number = getattr(arch, "syscall_exception", None)
                owner = next((c for c in self.controllers if hasattr(c, "pend")), None)
                if number is None or owner is None:
                    how = "svc at pc 0x%x: no exception model takes it" % (pc & ~1)
                    break
                owner.pend(number)
                if self._wanted() != (owner, number) or not self._may_take(owner, number):
                    how = "svc at pc 0x%x while its exception cannot be taken (HardFault, not modelled)" % (pc & ~1)
                    break
                pc = take(pc)
            if self.deliver:
                self.deliver = False
                pc = take(pc)
            if self.stop:
                how = self.stop
                break
            if self.clock >= limit:
                how = "limit: %d units of shared clock" % limit
                break
            frozen = self.clock == clock_before and self.next_event is not None
            if frozen:
                self.clock = self.next_event        # a wait outside shared code: on to the next event
            self.service()
            if frozen:                              # and no shared block behind which to take its interrupt
                self.frozen = 0
                pc = take(pc)
            if self.next_event is None and (self.clock == clock_before or self.clock - self.last_new >= QUIET):
                if self.log and self.log[-1][0] == "read" and self.clock - self.last_access < QUIET:
                    how = "polls: register 0x%x read %d times in a row, nothing scheduled, pc 0x%x" % (
                        self.log[-1][2], self.log[-1][5], pc & ~1)
                else:
                    how = "waits: no device access for %d units of shared clock and nothing scheduled, pc 0x%x" % (
                        self.clock - self.last_access, pc & ~1)
                break
        reports = {m.name: m.report() for m in self.models}
        if self.interrupts:
            reports["interrupts"] = dict(sorted(self.interrupts.items()))
        return Outcome(how, self.clock, list(self.log), reports, dict(self.entered), list(self.order))

    def service(self):
        """Inputs and model events that are due, then the next stop."""
        while self.pending_input and self.pending_input[0][0] <= self.clock:
            _, name, data = self.pending_input.pop(0)
            self.by_name[name].input(data)
        for m in self.models:
            t = m.next_event()
            if t is not None and t <= self.clock:
                m.event(self.clock)
        self.schedule()


def run_image(target, image, payload, scenario, placement=None, built=(), limit=None):
    system = System(target, image, payload, placement=placement, built=built, scenario=scenario)
    return system.run(limit or int(scenario.get("limit", LIMIT)))


def compare(a, b):
    """Readable differences of two outcomes (empty when they agree)."""
    out = []
    n = min(len(a.log), len(b.log))
    first = next((i for i in range(n) if a.log[i] != b.log[i]), None)
    if first is None and len(a.log) != len(b.log):
        first = n
    if first is not None:
        out.append("device logs differ at entry %d (of %d and %d):" % (first, len(a.log), len(b.log)))
        for i in range(max(0, first - 3), first):
            out.append("      %s" % (show(a.log[i]),))
        out.append("    original: %s" % (show(a.log[first]) if first < len(a.log) else "(ends)"))
        out.append("    rebuilt:  %s" % (show(b.log[first]) if first < len(b.log) else "(ends)"))
    if a.reports != b.reports:
        out.append("model reports differ: %s against %s" % (a.reports, b.reports))
    if a.how.split(":")[0] != b.how.split(":")[0]:
        out.append("the runs end differently: %s / %s" % (a.how, b.how))
    return out


def show(entry):
    return " ".join("0x%x" % x if isinstance(x, int) and x > 9 else str(x) for x in entry)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread sysrun", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--scenario", default=None, help="a table of [scenarios] (default: the first one)")
    parser.add_argument("--recon", help="the rebuilt image (default: BUILD/recon/IMAGE/payload.bin)")
    parser.add_argument("--original-only", action="store_true", help="run the original image alone")
    parser.add_argument("--limit", type=int, help="shared-clock units (default: the scenario's, else %d)" % LIMIT)
    parser.add_argument("--log", help="write the device logs to FILE (and FILE.rebuilt)")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        scenarios = target.scenarios
        name = args.scenario or (sorted(scenarios)[0] if scenarios else None)
        scenario = scenarios.get(name, {}) if name else {}
        if args.scenario and args.scenario not in scenarios:
            raise tgt.TargetError("no scenario %s (there are: %s)" % (args.scenario, ", ".join(sorted(scenarios)) or "none"))
        runs = [("original", image.payload(), None, ())]
        if not args.original_only:
            recon = args.recon or os.path.join(recon_dir(target, image), "payload.bin")
            if not os.path.exists(recon):
                raise tgt.TargetError("no rebuilt image (%s): run dread recon, or --original-only" % recon)
            import json
            placement = os.path.join(os.path.dirname(os.path.abspath(recon)), "placement.json")
            with open(placement) as f:
                placed = json.load(f)["functions"]
            built = [n for n, p in placed.items() if p["placement"] != "helper"]
            with open(recon, "rb") as f:
                runs[0] = ("original", image.payload(), None, built)
                runs.append(("rebuilt", f.read(), placed, ()))
    except (tgt.TargetError, OSError, ValueError) as e:
        print("dread sysrun: %s" % e, file=sys.stderr)
        return 1
    program = Program(target, image)
    outcomes = []
    for label, payload, placed, built in runs:
        outcome = run_image(target, image, payload, scenario, placement=placed, built=built, limit=args.limit)
        outcomes.append(outcome)
        names = [program.name_at(e) or "0x%x" % e for e in outcome.order]
        print("%-8s %s; clock %d; %d device log entries; %d functions entered" % (
            label, outcome.how, outcome.clock, len(outcome.log), len(names)))
        for model, report in sorted(outcome.reports.items()):
            print("         %s: %s" % (model, report))
        if args.log:
            with open(args.log + ("" if label == "original" else "." + label), "w") as f:
                f.write("\n".join(show(e) for e in outcome.log) + "\n")
    if len(outcomes) == 2:
        diff = compare(*outcomes)
        for line in diff:
            print(line)
        print("dread sysrun: scenario %s: %s" % (name or "(none)", "the images differ" if diff else "the images agree"))
        return 1 if diff else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
