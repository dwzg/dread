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

The block hook is the run's hot path (a FreeRTOS run of 30 million clock units enters 3.4 million
blocks). On Unicorn a hook in C (dread.fasthooks, dread_sysblock) does the blocks that only move the
clock: a known block of shared code with nothing due, a function entered again (counted there),
and one behind which the masked interrupt's answer still holds (the registers it depends on read as
when it was given: the layer's mask_registers). Every other block goes to System._on_block, which
keeps the rules; both read and write one state (System.st). Elsewhere (DREAD's own engines)
_on_block does every block.

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
from dread.emu import Layout, core_reader
from dread.program import Program

CHUNK = 200_000          # instructions per emu_start (stop rules are checked between chunks)
ACCESS = 8               # shared-clock units a device access takes
QUIET = 200_000          # shared-clock units without a new block or a device access: the firmware waits
FROZEN = 2_000           # blocks in a row outside shared code with an event due: a wait the clock cannot see
SPIN = 1_000_000         # shared-clock units without progress while an interrupt waits, masked: the run spins
LIMIT = 20_000_000       # default shared-clock limit


class Outcome:
    """What one run did."""

    def __init__(self, how, clock, log, reports, entered, order):
        self.how, self.clock, self.log, self.reports, self.entered, self.order = how, clock, log, reports, entered, order


NO_EVENT = (1 << 63) - 1    # _State.next_event when nothing is scheduled (as in dread.fasthooks)
CLOCK_MAX = NO_EVENT - 1    # the state's clock and times stop here (64 bits in C); see System.clock


class _State:
    """The run's clock and what the block hook decides with, as plain attributes: on Unicorn the same
    fields are dread.fasthooks.SysState, which the C block hook reads and writes."""

    def __init__(self):
        self.clock = self.progress = self.spin_check = 0
        self.next_event, self.limit, self.spin = NO_EVENT, LIMIT, SPIN
        self.frozen = self.due = self.irq_check = self.observers = self.memo = 0


def _field(name, kind=int):
    """A System attribute kept in its state (System.st)."""
    if kind is bool:
        return property(lambda s: bool(getattr(s.st, name)), lambda s, v: setattr(s.st, name, 1 if v else 0))
    return property(lambda s: getattr(s.st, name), lambda s, v: setattr(s.st, name, v))


class _Registers:
    """The engine's reg_read for the layer's interrupt questions (cpu_masks, interrupts_enabled),
    through Unicorn's C function where the engine is Unicorn (dread.emu.core_reader)."""

    def __init__(self, engine):
        self.reg_read = core_reader(engine)


class System:
    """One image from reset, with models. placement: the rebuilt image's placement.json content
    ({name: {...}}) to tell shared code from C; built: the names of the functions built from C (for
    the original, whose code of them does not count)."""

    spin_check, limit, frozen = (_field(n) for n in ("spin_check", "limit", "frozen"))
    due, irq_check = _field("due", bool), _field("irq_check", bool)

    # The state holds 64-bit times (C's), Python any: a timer model may schedule beyond 2**63 (the CLINT
    # once its compare register is all ones), and the run may move the clock on to such an event when
    # it waits (run()), just before it ends at the limit. The true values stay here; the state gets
    # them clamped below NO_EVENT, which every comparison during a run takes alike.
    @property
    def clock(self):
        return self._big_clock if self._big_clock is not None else self.st.clock

    @clock.setter
    def clock(self, value):
        self._big_clock = value if value > CLOCK_MAX else None
        self.st.clock = min(value, CLOCK_MAX)

    @property
    def progress(self):
        return self.st.progress

    @progress.setter
    def progress(self, value):
        self.st.progress = min(value, CLOCK_MAX)

    @property
    def next_event(self):
        return self._next_event

    @next_event.setter
    def next_event(self, value):
        self._next_event = value
        self.st.next_event = NO_EVENT if value is None else min(value, CLOCK_MAX)

    def __init__(self, target, image, payload, program=None, placement=None, built=(), scenario=None):
        self.target, self.image, self.arch = target, image, image.arch
        self.program = program or Program(target, image)
        self.layout = Layout(target, image)
        self.uc = uc = self.arch.new_uc()
        # On Unicorn a block hook in C does the blocks that only move the clock (dread.fasthooks:
        # nearly all) and hands the others to _on_block; elsewhere _on_block does every block.
        from dread import fasthooks
        self.fast, self.callback_error = None, None
        if fasthooks.usable(uc):
            self.fast = fasthooks.SysHooks(uc, self._from_c)
        self.st = self.fast.state if self.fast is not None else _State()
        self._big_clock, self._next_event = None, None
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
        # An interrupt that is wanted but masked (a lower priority's while a handler runs, one behind
        # BASEPRI) is asked about behind every block of shared code until it is taken: half the
        # blocks of a FreeRTOS run. What the controllers answer changes only with what reaches
        # interrupts_changed (their registers, lines, timer events, exception entry and return), so
        # irq_waiting keeps (irq_epoch, controller, masks) of the last answer "masked", and a block
        # with the same epoch and the same masks reads only the masks again (the same decisions,
        # a quarter of the time). The masks are read through Unicorn's C function where it is one.
        self.irq_epoch, self.irq_waiting = 0, None
        self.regs = _Registers(uc)
        owned = sorted((lo, hi, m) for m in self.models for lo, hi in m.ranges)
        self.owned_lo, self.owned = [lo for lo, _, _ in owned], owned
        self.log, self.clock, self.new, self.stop = [], 0, 0, None
        self.next_event = None
        self.levels = {}
        self.blocks, self.entered, self.order = {}, collections.Counter(), []    # blocks: address -> size
        self.last_new, self.last_access, self.limit = 0, 0, LIMIT
        self.progress = 0                   # the clock at the last device access, model note, interrupt taken
                                            # or new block of shared code: the same in both images
        self.spin_check = 0                 # the clock from which the spin rule looks again (_on_block)
        self.frozen = 0                     # blocks in a row outside shared code
        # A stop asked for by a peripheral access, taken where the next block starts: stopped inside a
        # memory hook, Unicorn leaves pc on the accessing instruction (a store done, a load not), and
        # going on from pc would repeat the access (dread.engines)
        self.due = False
        self.observers = []                 # tools that watch the run (dread.sysrecord): block(), io(), and
                                            # if they have them entered(resume, sp) before an exception is taken
                                            # (where the interrupted code goes on, its stack pointer) and
                                            # returned(resume) after a return this run finishes (the M profile's)
        # A memory hook leaves a stale Thumb IT state behind (dread.arch.arm.settle_it): every memory
        # hook repairs it itself before it returns, _on_io and an observer's (dread.sysrecord) alike. A
        # repair at the next block's start instead cleared the state of an IT block that a translation
        # block boundary splits (QEMU's 1 KiB pages do: FreeRTOS's strcmp, `it cs` at 0x7fe), and an
        # interrupt taken there stacked it cleared (a repair at every block took 40 % of a run anyway)
        self._shared(placement, built)
        if self.fast is None:
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
        if self.fast is not None:
            self.fast.set_excluded(self.excluded)
            for address in self.entries:
                self.fast.add_entry(address)

    def is_shared(self, address):
        i = bisect.bisect_right(self.excluded_lo, address) - 1
        return not (i >= 0 and address < self.excluded[i][1])

    # ---- for models ----

    def note(self, model, what, value):
        """A model reports something (a byte sent): part of the device log."""
        self.log.append((model, what, value))
        self.new += 1
        self.last_new = self.progress = self.clock
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
        st = self.st
        st.irq_check, st.memo = 1, 0
        self.irq_epoch += 1

    def _wanted(self):
        """The interrupt a controller asks for, or None."""
        for c in self.controllers:
            number = c.pending_interrupt()
            if number is not None:
                return c, number
        return None

    def _masks(self, controller):
        """What _may_take reads of the core for `controller`: its masks (the layer's cpu_masks) when
        it has priorities, else whether the core takes interrupts at all (interrupts_enabled)."""
        if getattr(controller, "may_take", None) is not None:
            return self.arch.cpu_masks(self.regs)
        return self.arch.interrupts_enabled(self.regs)

    def _may_take(self, controller, number, masks=None):
        """Whether the core takes that interrupt now: the controller decides with the core's masks
        when it has priorities (may_take), else the layer (interrupts_enabled). masks: _masks(),
        when read already."""
        if masks is None:
            masks = self._masks(controller)
        may = getattr(controller, "may_take", None)
        if may is not None:
            return may(number, masks)
        return masks

    def schedule(self):
        times = [t for t in (m.next_event() for m in self.models) if t is not None]
        times += [t for t, _, _ in self.pending_input]
        self.next_event = min(times) if times else None

    # ---- hooks ----

    def _from_c(self, addr, size):
        """A block the C hook hands over (dread.fasthooks.SysHooks): an exception cannot pass through
        the C between, so it stops the run and run() raises it."""
        try:
            self._on_block(self.uc, addr, size, None)
        except BaseException as e:          # noqa: B036 (raised again by run())
            if self.callback_error is None:
                self.callback_error = e
            self.uc.emu_stop()

    def _on_block(self, uc, addr, size, user):
        st = self.st
        if st.due:
            st.due = 0
            uc.emu_stop()                   # this block runs (and counts) when the run goes on
            return
        for o in self.observers:
            o.block(addr, size)
        known = self.blocks.get(addr)
        fresh = known is None
        if fresh or size > known:
            self.blocks[addr] = size
        if fresh:
            self.new += 1
            self.last_new = self.clock
        entry = self.entries.get(addr)
        if entry is not None:
            if not self.entered[entry]:
                self.order.append(entry)
            self.entered[entry] += 1
        if not self.is_shared(addr):
            st.frozen += 1                  # a wait in code built from C (a rebuilt idle loop): stop the
            if st.frozen >= FROZEN and st.next_event != NO_EVENT:          # chunk, the run moves the
                uc.emu_stop()                                               # clock on to the event
        else:
            st.frozen = 0
            if fresh:
                st.progress = st.clock
            st.clock += size
            if st.clock - st.progress >= SPIN and st.clock >= st.spin_check:
                # Here, in a block of code both images share, at a clock both have: a check between
                # the emulator's chunks came at other points in the two images, which then ended with
                # logs of different lengths. Every SPIN // 16 units while the rule holds.
                st.spin_check = st.clock + SPIN // 16
                wanted = self._wanted()
                if wanted is not None and not self._may_take(*wanted):
                    # it waits for an interrupt it keeps masked (an assertion's loop with interrupts
                    # off): nothing can change any more
                    self.request_stop("spins: interrupt %d waits, masked, and nothing new for %d units of shared "
                                      "clock, at 0x%x" % (wanted[1], st.clock - st.progress, addr))
            if st.clock >= st.next_event or st.clock >= st.limit:
                uc.emu_stop()
            if st.irq_check:
                waiting = self.irq_waiting
                if waiting is None or waiting[0] != self.irq_epoch or waiting[2] != self._masks(waiting[1]):
                    wanted = self._wanted()
                    st.memo = 0
                    if wanted is None:
                        st.irq_check, self.irq_waiting = 0, None
                    else:
                        masks = self._masks(wanted[0])
                        if self._may_take(*wanted, masks=masks):
                            self.deliver, self.irq_waiting = True, None
                            uc.emu_stop()
                        else:
                            self.irq_waiting = (self.irq_epoch, wanted[0], masks)
                            registers = getattr(self.arch, "mask_registers", None)
                            if self.fast is not None and registers is not None:     # (the C hook's copy)
                                self.fast.set_memo(registers(getattr(wanted[0], "may_take", None) is not None),
                                                   self.regs.reg_read)

    def _owner(self, address):
        i = bisect.bisect_right(self.owned_lo, address) - 1
        if i >= 0 and address < self.owned[i][1]:
            return self.owned[i][2]
        return None

    def _on_io(self, uc, access, addr, size, value, user):
        model, mask = self._owner(addr), (1 << 8 * size) - 1
        who = model.name if model else "-"
        st = self.st
        st.clock += ACCESS
        self.last_access = st.progress = st.clock
        if access == UC_MEM_WRITE:
            self.log.append(("write", who, addr, size, value & mask))
            self.new += 1
            self.last_new = self.clock
            if model is not None:
                model.write(addr, size, value & mask)
                self.schedule()             # a write may set when the model acts next (a timer's compare)
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
        if st.clock >= st.next_event or st.clock >= st.limit:
            st.due = 1                      # (not emu_stop here: see self.due)
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
        self.st.observers, self.st.spin = 1 if self.observers else 0, SPIN
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
            self.progress = self.clock
            self.log.append(("interrupt", controller.name, number))
            vectors = controller.vector_base() if hasattr(controller, "vector_base") else self.image.vectors
            cause = controller.core_cause(number) if hasattr(controller, "core_cause") else None
            self._entering(pc)
            pc = arch.enter_interrupt(uc, number, pc, vectors, word, write_word, cause=cause)
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
            if self.callback_error is not None:
                raise self.callback_error
            back = arch.exception_return(uc, word)
            if back is not None:                # the M profile's return from an exception, finished here
                self.fault, pc = None, back
                for c in self.controllers:
                    c.returned()
                for o in self.observers:
                    if hasattr(o, "returned"):
                        o.returned(back)
                self.interrupts_changed()
                pc = take(pc)                   # one that waited for it comes at once (tail-chaining)
                continue
            if error is not None or self.fault is not None:
                how = self.fault or "emulator error at pc 0x%x: %s" % (uc.reg_read(self.layout.pc), error)
                break
            pc = arch.resume_address(uc)
            if self.syscall and hasattr(arch, "enter_syscall"):    # a trap the layer takes alone (ecall)
                self.syscall = False
                self.log.append(("syscall",))     # (not its address: code that moved is elsewhere)
                self._entering(pc)
                pc = arch.enter_syscall(uc, pc, word, write_word)
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
        if self.fast is not None:           # the entries the C hook counted
            for address, n in self.fast.take_entered():
                self.entered[self.entries[address]] += n
        reports = {m.name: m.report() for m in self.models}
        if self.interrupts:
            reports["interrupts"] = dict(sorted(self.interrupts.items()))
        return Outcome(how, self.clock, list(self.log), reports, dict(self.entered), list(self.order))

    def _entering(self, resume):
        """Tell the observers an exception is about to be taken (see observers)."""
        if self.observers:
            sp = self.uc.reg_read(self.layout.sp)
            for o in self.observers:
                if hasattr(o, "entered"):
                    o.entered(resume, sp)

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
