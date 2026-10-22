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
place in both runs, however many instructions the C takes. Code that is not shared runs in stretches
in which the clock stands still (System._stretch). A stretch that ends by itself (at shared code, a
device access, an exception) was a computation, however many blocks it took: the two images' code
for it differs, so nothing may depend on its length. One that does not end is a wait the clock
cannot see (a rebuilt idle loop, a task polling memory, a busy task only an interrupt preempts):
from FROZEN blocks on the run looks for a loop in it, a block entered again with the core's registers
as at an earlier entry (System._loops; LOOP_MAX blocks count as one too), and then moves the clock
to the next scheduled event, in both runs alike. Until 2026-10-22 FROZEN blocks in a row were a
wait: the rule fell inside a long computation of one image's code and not the other's, and in a loop
at a block where the core masked the interrupt, which was not asked for again (FreeRTOS on RISC-V
GCC -Os and Cortex-M clang -O2 differed with lifted C alone). Interrupts are taken behind a block of
shared code, or, in such a wait, behind the first block in which the core takes them (at once, or
behind the instruction that unmasks them): the code it interrupts is not shared, so there is no
common place to wait for. A controller with priorities (dread.models.nvic) decides with the core's
masks whether one is taken (may_take), so a pending one waits while masked and comes behind the
first shared block after the mask drops, or at once behind the return of the handler that held it
off. svc raises its exception (Cortex-M: SVCall) at once. The device log, every
peripheral access with its value in order plus what the models note (bytes sent), is what is
compared; the functions entered are information (a rewritten function enters what it calls in its
own way).

A run ends at its limit (shared-clock units), when the scenario's end condition holds (text a model
sent; checked at the store that completes it, so both runs stop at the same place), when the
firmware waits with nothing scheduled (QUIET units without a new block or log entry, or a wait
outside shared code with no interrupt it could take: "polls" when it keeps reading one register,
"waits" when it accesses no device at all), or at a fault. A read repeated without
anything between is one log entry with its count. A chunk (the emulator's run between such checks)
is CHUNK blocks and ends where a block starts, before the block counts, so that its size changes
nothing the clock counts.

The block hook is the run's hot path (a FreeRTOS run of 30 million clock units enters 3.4 million
blocks). On Unicorn a hook in C (dread.fasthooks, dread_sysblock) does the blocks that only move the
clock: a known block of shared code with nothing due, a function entered again (counted there),
and one behind which the masked interrupt's answer still holds (the registers it depends on read as
when it was given: the layer's mask_registers); and the known blocks of a stretch outside shared
code that only count (sys_stretch: before FROZEN, not at the anchor or with other registers there,
in a wait while the masked answer holds). Every other block goes to System._on_block, which keeps
the rules; both read and write one state (System.st). Elsewhere (DREAD's own engines) _on_block
does every block. (FreeRTOS's RISC-V GCC -Os image with every lifted function rebuilt: a run to the
limit took 20 s with its stretches' blocks in Python, 1.2 s with them in C.)

The device accesses are the other hot path, once much of the code is outside shared code and its
clock stands still: FreeRTOS's Cortex-M port reads ICSR in every critical section, and with the
kernel rebuilt (lifted) a run's clock was nearly all accesses, 125,000 a million units. On Unicorn the C hook
(dread_sysio) does the reads of plain registers alone: a model names them (dread.models, `plain`:
no side effect, a value only a call into the models changes), and every register no model owns is
memory. It answers from the value the model gave at the last read done here (_plain) and keeps the
clock, the log with its counted repeats and the stop rules' clocks as _io does; after anything that
may change a model (a write, a read with a side effect, the events and exceptions between chunks)
the registers it read get their models' values again (_refresh_plain). Every other access goes to
_io. (FreeRTOS's Cortex-M clang -Os with every lifted function rebuilt: the pair of runs to the
limit 82 s -> 30 s, three reads of ICSR in four accesses done in C; the writes, PendSV set again
and again while it is pending, are most of what is left in Python.)

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
import ctypes
import os
import sys

from unicorn import (UC_HOOK_BLOCK, UC_HOOK_INTR, UC_HOOK_MEM_READ, UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_WRITE,
                     UC_MEM_WRITE, UcError)

from dread import models as dmodels
from dread import target as tgt
from dread.build import recon_dir
from dread.emu import Layout, core_reader
from dread.fasthooks import MEMORY, NO_MODEL, VALUE
from dread.program import Program

CHUNK = 50_000           # blocks per emu_start (stop rules are checked between chunks); a chunk ends
# where a block starts, before the block counts. It ended by the engine's count of instructions once:
# inside a block, whose rest the next chunk entered as a block of its own and counted again, at points
# the two images do not share (their C runs other instructions), so that their clocks drifted apart
# (FreeRTOS's main_full rebuilt: 106 units behind at the limit, two device accesses more in its log)
ACCESS = 8               # shared-clock units a device access takes
QUIET = 200_000          # shared-clock units without a new block or a device access: the firmware waits
FROZEN = 2_000           # blocks in a row outside shared code without a device access (a stretch in which
                         # the clock stands still) from which the run looks for a loop there (System._loops)
LOOP_MAX = 200_000       # blocks of such a stretch after which it counts as a wait without a loop found
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
        self.chunk = CHUNK                  # blocks the current chunk may still enter
        # the stretch outside shared code (System._stretch): see SysState
        self.stretch_from, self.loop_max, self.anchor_next = FROZEN, LOOP_MAX, FROZEN
        self.waited = self.anchor = self.resume = self.waiting = 0
        self.io_done = self.last_access = 0     # (the C hook's reads: none here)


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

    spin_check, limit, frozen, last_access = (_field(n) for n in ("spin_check", "limit", "frozen", "last_access"))
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
        self.model_index = {m: i for i, m in enumerate(self.models)}
        self.model_names = [m.name for m in self.models] + ["-"]   # by index; -1: no model owns it
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
        self.log, self.clock, self.stop = [], 0, None
        self.next_event = None
        self.levels = {}
        self.blocks, self.entered, self.order = {}, collections.Counter(), []    # blocks: address -> size
        self.last_new, self.last_access, self.limit = 0, 0, LIMIT
        self.progress = 0                   # the clock at the last device access, model note, interrupt taken
                                            # or new block of shared code: the same in both images
        self.spin_check = 0                 # the clock from which the spin rule looks again (_on_block)
        self.frozen = 0                     # blocks of the current stretch: in a row outside shared code,
                                            # no device access between (_on_block)
        self.jump = self.stuck = False      # what _stretch asks run() for: the next event; the end
        self.state_regs = self._state_registers()
        self.anchor_vals = [0] * len(self.state_regs)   # their values at the anchor (st.anchor + 1)
        if self.fast is not None:
            self.fast.set_state_registers(self.state_regs)
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
        else:
            # The device accesses through the C hook (dread_sysio): the reads of plain registers it
            # can do alone (_plain), every other access to _io; it repairs the IT state behind both.
            arch = self.arch
            self.fast.add_io(self.layout.io, self._io_from_c, ACCESS,
                             settle=(arch.status_reg(), arch.IT_MASK) if self.layout.settle_it else None)
        self._plain_at = {}                 # (address, size) -> the C hook's entry for its reads, -1: none
        self._plain_info, self._plain_views, self._plain_base = [], [], None   # per entry (_new_plain)
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

    # ---- waits outside shared code ----

    def _state_registers(self):
        """The core registers _loops compares: the layer's general registers, the stack pointer, the
        link register, the status register and what the interrupt masks are read from."""
        arch, layout = self.arch, self.layout
        regs = [arch.reg_id(n) for n in arch.general_regs()] + [layout.sp]
        if layout.lr is not None:
            regs.append(layout.lr)
        status = getattr(arch, "status_reg", None)
        if status is not None:
            regs.append(status())
        masks = getattr(arch, "mask_registers", None)
        if masks is not None:
            regs += [r for priorities in (True, False) for r, _ in masks(priorities)]
        return list(dict.fromkeys(regs))

    def _loops(self, address, n):
        """Whether the stretch outside shared code (n blocks so far) has come back to where it was: a
        block entered again with the core's registers (_state_registers) as at an anchor, a block
        entered earlier in the stretch. The anchor moves to the current block at FROZEN, 2 FROZEN,
        4 FROZEN ... blocks (Brent's cycle search), so that it comes to lie inside a loop the stretch
        ends in, and a loop of fewer blocks than the anchor's span is found within one round.
        Memory is not compared: a loop that counts in memory alone (a busy task's counter) is found as
        well, and so would be a computation whose progress lies in memory alone while every register
        repeats (not seen; not confirmed that none exists). A stretch of LOOP_MAX blocks counts as a
        loop all the same: a busy loop that counts in a register, or a computation too long to tell."""
        if n >= LOOP_MAX or self._came_round(address):
            return True
        st = self.st
        if n >= st.anchor_next:
            read = self.regs.reg_read
            self.anchor_vals = [read(r) for r in self.state_regs]
            if self.fast is not None and st.nregs >= 0:     # (the C hook's copy)
                st.anchor_vals[:len(self.anchor_vals)] = self.anchor_vals
            st.anchor, st.anchor_next = address + 1, 2 * n
        return False

    def _came_round(self, address):
        """Whether the block at `address` is entered with the anchor's registers (in C:
        sys_at_anchor)."""
        if self.st.anchor != address + 1:
            return False
        read = self.regs.reg_read
        return all(read(r) == v for r, v in zip(self.state_regs, self.anchor_vals))

    def _stretch(self, uc, address):
        """A block outside shared code, where the clock stands still: st.frozen counts the blocks of
        the stretch (since the clock last moved). A stretch that ends by itself (at shared code, a
        device access, an exception) was a computation, however long: the two images' code for it
        differ, so nothing in it may depend on how many blocks it takes. From FROZEN blocks on the
        run looks for a loop (_loops); found, the stretch is a wait the clock cannot see (a rebuilt
        idle loop, a task polling memory, a busy task only an interrupt preempts), and behind every
        block from then on: an interrupt a controller asks for is taken when the core takes it
        (behind the instruction that unmasks it, which ends its block); while one waits, masked, the
        loop goes on until it comes round (or LOOP_MAX blocks) without unmasking it. Then, or with
        none wanted, the run moves the clock on to the next event (run(): jump) and takes what it
        brings; with nothing scheduled the firmware waits for good (stuck: the run ends)."""
        st = self.st                        # (in C: sys_stretch does the blocks that only count)
        if st.resume == address + 1 and st.frozen:
            st.resume = 0                   # the block a stop came in front of, run now: no second
            return                          # look (it would find the anchor's registers again)
        st.frozen += 1
        n = st.frozen
        if n == 1:
            st.waiting, st.anchor, st.resume, st.anchor_next = 0, 0, 0, FROZEN
        if not st.waiting:
            if n < FROZEN or not self._loops(address, n):
                return
            st.waiting, st.waited = 1, n
        if st.irq_check:
            self._check_interrupts(uc)
            if self.deliver:
                return
            if st.irq_check and n - st.waited < LOOP_MAX and not (n > st.waited and self._came_round(address)):
                return                      # one waits, masked: the loop may still unmask it
        if st.next_event != NO_EVENT:
            self.jump = True
        else:
            self.stuck = True
        st.resume = address + 1
        uc.emu_stop()

    # ---- for models ----

    def note(self, model, what, value):
        """A model reports something (a byte sent): part of the device log."""
        self._log((model, what, value))
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
        best = self.pending_input[0][0] if self.pending_input else None     # (sorted by time)
        for m in self.models:
            t = m.next_event()
            if t is not None and (best is None or t < best):
                best = t
        self.next_event = best

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
        if st.due or st.chunk <= 0:         # a stop asked for, or the chunk's end
            st.due = 0
            uc.emu_stop()                   # this block runs (and counts) when the run goes on
            return
        st.chunk -= 1
        for o in self.observers:
            o.block(addr, size)
        known = self.blocks.get(addr)
        fresh = known is None
        if fresh or size > known:
            self.blocks[addr] = size
        if fresh:
            if st.io_done:                  # (the reads the C hook did came first)
                self._take_reads()
            self.last_new = self.clock
        entry = self.entries.get(addr)
        if entry is not None:
            if not self.entered[entry]:
                self.order.append(entry)
            self.entered[entry] += 1
        if not self.is_shared(addr):
            self._stretch(uc, addr)
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
                self._check_interrupts(uc)

    def _check_interrupts(self, uc):
        """Behind a block where an interrupt may be taken: stop the run for it (deliver) when a
        controller asks for one and the core takes it now."""
        st = self.st
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
        """A device access, where the C hook is not at hand."""
        self._io(uc, access, addr, size, value)
        if self.layout.settle_it:
            self.arch.settle_it(uc)

    def _io_from_c(self, access, addr, size, value):
        """An access the C hook hands over (dread.fasthooks, dread_sysio, which repairs the IT state
        behind it): an exception cannot pass through the C between, so it stops the run and run()
        raises it."""
        try:
            self._io(self.uc, access, addr, size, value)
        except BaseException as e:          # noqa: B036 (raised again by run())
            if self.callback_error is None:
                self.callback_error = e
            self.uc.emu_stop()

    def _io(self, uc, access, addr, size, value):
        st = self.st
        if st.io_done:                      # the reads the C hook did came first
            self._take_reads()
        model, mask = self._owner(addr), (1 << 8 * size) - 1
        who = model.name if model else "-"
        st.clock += ACCESS
        st.frozen = 0                       # the clock moved: a stretch outside shared code ends
        self.last_access = st.progress = st.clock
        if access == UC_MEM_WRITE:
            self._log(("write", who, addr, size, value & mask))
            self.last_new = self.clock
            if model is not None:
                model.write(addr, size, value & mask)
                self.schedule()             # a write may set when the model acts next (a timer's compare)
            changed = model is not None
        else:
            answer = None
            if model is not None:
                answer = model.read(addr, size)
                if answer is not None:
                    uc.mem_write(addr, (answer & mask).to_bytes(size, "little"))
            got = int.from_bytes(uc.mem_read(addr, size), "little")
            for o in self.observers:
                o.io(addr, size, got)
            last = self.log[-1] if self.log else None
            if last is not None and last[:5] == ("read", who, addr, size, got):
                self.log[-1] = last[:5] + (last[5] + 1,)        # the same read again: one entry, counted
            else:
                self._log(("read", who, addr, size, got, 1), read=(model, addr, size, got))
                self.last_new = self.clock
            changed = self.fast is not None and not self._plain(model, addr, size, answer, got)
        if st.clock >= st.next_event or st.clock >= st.limit:
            st.due = 1                      # (not emu_stop here: see self.due)
        if changed and self.fast is not None:
            self._refresh_plain()           # a write to a model, or a read that may have changed one

    # ---- the C hook's reads (dread.fasthooks, dread_sysio) ----
    #
    # A read of a plain register (a model's `plain` ranges: no side effect, a value only a call into
    # the models changes; or a register no model owns, which is memory) is done in C from the value
    # read here last, and every other access comes to _io, as above. The C hook keeps the entries it
    # makes until _take_reads puts them into the log, which comes first whenever this side adds to
    # the log or uses it; st.tail_* is the log's last entry, for counting repeats as _io does.

    def _log(self, entry, read=None):
        """Append to the device log, behind the reads the C hook did. read: (model, address, size,
        value) of a read's entry, whose repeats the C hook then counts into it."""
        st = self.st
        if st.io_done:
            self._take_reads()
        self.log.append(entry)
        if self.fast is not None:
            if read is None:
                st.tail_read = 0
            else:
                model, address, size, value = read
                st.tail_read, st.tail_model, st.tail_address, st.tail_size, st.tail_value = (
                    1, self.model_index[model] if model is not None else -1, address, size, value)

    def _take_reads(self):
        """The reads the C hook did since the last call, into the log, and the clock of the last new
        entry among them into last_new (which may hold a clock beyond the state's: see System.clock)."""
        st, log = self.st, self.log
        st.io_done = 0
        if st.tail_extra:                   # repeats of the log's last entry
            last = log[-1]
            log[-1] = last[:5] + (last[5] + st.tail_extra,)
            st.tail_extra = 0
        n = st.reads_n
        if n:
            names, reads = self.model_names, st.reads
            for i in range(n):
                r = reads[i]
                log.append(("read", names[r.model], r.address, r.size, r.value, r.count))
            st.reads_n = 0
        if st.last_new >= 0:
            self.last_new, st.last_new = st.last_new, -1

    def _plain(self, model, addr, size, answer, got):
        """After a read in _io: whether the register is plain; then the C hook does its reads from
        now on, until the next _refresh_plain (answer: what the model answered, None for memory)."""
        at = self._plain_at.get((addr, size))
        if at is None:
            at = self._new_plain(model, addr, size)
        if at < 0:
            return False
        st = self.st
        e = self._plain_views[at]
        if model is None:
            e.kind = NO_MODEL
        elif answer is None:
            e.kind = MEMORY
        else:
            e.kind, e.value = VALUE, got
        e.epoch = st.plain_epoch
        if not e.used:
            e.used = 1
            st.plain_used[st.plain_used_n] = at
            st.plain_used_n += 1
        return True

    def _new_plain(self, model, addr, size):
        """The C hook's entry for the reads of (addr, size) when they are plain, else -1."""
        at = -1
        if 1 <= size <= 8 and (model is None or any(lo <= addr and addr + size <= hi
                                                   for lo, hi in getattr(model, "plain", ()))):
            at = self.fast.plain_entry(addr, size, self.model_index[model] if model is not None else -1)
            if at >= 0:                     # (entries are made in order, each once)
                self._plain_info.append((model, addr, size, (1 << 8 * size) - 1))
                plain = self.st.plain
                base = ctypes.cast(plain, ctypes.c_void_p).value
                if base != self._plain_base:    # the C side moved its entries: new views of them
                    self._plain_base = base
                    self._plain_views = [plain[i] for i in range(len(self._plain_info))]
                else:
                    self._plain_views.append(plain[at])
        self._plain_at[addr, size] = at
        return at

    def _refresh_plain(self):
        """After a call into the models that may have changed them (a write, a read with a side
        effect, the events and exceptions between chunks): the C hook's values no longer hold. The
        registers it read since the last refresh get their models' values again; the others are
        asked here at their next read."""
        st = self.st
        epoch = st.plain_epoch + 1
        st.plain_epoch = epoch
        n = st.plain_used_n
        if not n:
            return
        used, views, info = st.plain_used, self._plain_views, self._plain_info
        for i in range(n):
            at = used[i]
            e = views[at]
            e.used = 0
            model, address, size, mask = info[at]
            if model is None:
                continue
            answer = model.read(address, size)
            if answer is None:
                e.kind = MEMORY
            else:
                e.kind, e.value = VALUE, answer & mask
            e.epoch = epoch
        st.plain_used_n = 0

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
        self.st.stretch_from, self.st.loop_max = FROZEN, LOOP_MAX
        if self.fast is not None:
            self.st.access = ACCESS
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
            self.progress, self.frozen = self.clock, 0      # (a stretch outside shared code ends)
            self._log(("interrupt", controller.name, number))
            vectors = controller.vector_base() if hasattr(controller, "vector_base") else self.image.vectors
            cause = controller.core_cause(number) if hasattr(controller, "core_cause") else None
            self._entering(pc)
            pc = arch.enter_interrupt(uc, number, pc, vectors, word, write_word, cause=cause)
            self.interrupts_changed()
            return pc
        how = None
        while True:
            error = None
            if self.fast is not None:           # (the models may have changed between chunks)
                self._refresh_plain()
            try:
                self.st.chunk = CHUNK
                uc.emu_start(pc, self.layout.sentinel)
            except UcError as e:
                error = e
            if self.st.io_done:                 # the reads the C hook did, into the log
                self._take_reads()
            if self.callback_error is not None:
                raise self.callback_error
            back = arch.exception_return(uc, word)
            if back is not None:                # the M profile's return from an exception, finished here
                self.fault, pc, self.frozen = None, back, 0
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
                self.syscall, self.frozen = False, 0
                self._log(("syscall",))         # (not its address: code that moved is elsewhere)
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
            jump, stuck, self.jump, self.stuck = self.jump, self.stuck, False, False
            if jump:
                self.clock = self.next_event        # a wait outside shared code (_stretch): on to the next event
            self.service()
            if jump:                                # and no shared block behind which to take its interrupt:
                pc = take(pc)                       # at once, or behind the first block the core takes it in
            if self.next_event is None and (stuck or self.clock - self.last_new >= QUIET):
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
