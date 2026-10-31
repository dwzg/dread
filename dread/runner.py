"""dread test: every rewritten function against the original, in the emulator.

Each case calls one function in two emulated machines, one per image (the original and the image
with the C linked in, from `dread recon`), with identical inputs, and the results must agree
(dread.cases says what is compared). The rebuilt image may run REBUILT_BUDGET times a case's
instruction limit (C is slower than the original; dread integration does the same), except in a
case marked "hang". A function the overlay built from C that has no case is
reported UNTESTED.

Besides what dread.cases lists, the scratch registers the function's callers read after the call
(dread.regflow: a second result, or a register a compiler with -fipa-ra kept a value in across the
call) are compared at the return. A difference names the reader, says whether it reads on every
path or on some only, and whether the original leaves the register unchanged. A register whose
difference cannot matter is left out with an argument in kept.tsv beside the cases directory
(function, register, argument; tab-separated), and listed with it under the function's result.

Coverage: while the original runs, the instructions executed inside the function under test are
recorded. A function whose cases leave instructions of the original unexecuted is reported
UNCOVERED and fails the run, unless those addresses are declared with unreachable() and a reason;
a declared address that does execute fails the function (the declaration is wrong). Coverage is
counted per executed block: a conditional instruction counts as run whether its condition held or
not, so a function made of conditional instructions without branches is covered by one case. The
mutation check is what shows whether its cases tell both outcomes apart.

Output, one block per function, in this form (other tools read it):

    ok   NAME                       n/m cases agree
         NAME [label]: agrees with the stack below every function's entry filled alike (...)
                                    a case whose plain runs differ, but that agrees whole in both
                                    images with the stack below each function's entry filled alike,
                                    once with each fill (filled_alike; decided 2026-10-26,
                                    docs/decisions.md): the difference was what earlier frames left
                                    in a local nothing wrote; counted for coverage
         NAME [label]: compared except ...   a case whose every difference changes in the original
                                    too when what lies below the stack pointer at its function
                                    entries changes (it read stale stack data, an uninitialised
                                    local: what earlier frames left there, which C, laying out its
                                    frames and saving other values in them, leaves otherwise;
                                    stale_outputs): compared except those outputs, which the line
                                    names (the log from the first entry the fills move), and
                                    counted for coverage (decided 2026-10-20 by recommendation,
                                    docs/decisions.md; refused before)
    FAIL NAME [label]               followed by the differences, indented by five spaces; with
                                    ": refused for X, but it differs Y: Z" where a refusal below
                                    applies but does not explain every difference (Z: those it
                                    does not)
    UNCOVERED NAME: a of b instructions of the original never ran: ADDRESS ...
    REFUSED NAME [label]: ... (it differs only ...: Z)
                                    a case whose images differ only where one of these reasons, or
                                    several together, explain (judge; decided 2026-10-24,
                                    docs/decisions.md: until then a reason that applied refused
                                    the case whatever else differed), Z naming the differences:
                                    a written case that reads code as data or writes into the image
                                    (what comes after the first such load or store; refused also
                                    when the images agree: remove it),
                                    where the original's own outcome changes when its stack starts
                                    STACK_SHIFT bytes lower (it took a stack address for a value:
                                    C's frames lie otherwise; the outputs that change so),
                                    where the original's input makes code other than a push or a
                                    pop read or write the function's own saved registers (a length
                                    past a local: C keeps them elsewhere; saves_touched; decided
                                    2026-10-20; what comes after the first touch),
                                    where the original calls through a register into the middle of a
                                    function (a code pointer the input gave that is no entry: the
                                    rebuilt image has other code there, or calls it with other
                                    values in the other registers; wild_call; what comes after
                                    that call); such a case is not compared and runs nothing for
                                    coverage. "What comes after" an event (fragile.Timeline,
                                    After): every register at the return, the log from the entry
                                    made after the event, and a byte written outside the stack that
                                    either image wrote after the last call out of the function or
                                    return into it, or log entry, before the event; a rebuilt run
                                    that never gets that far gets nothing explained by the event
    UNTESTED NAME

Exit status 1 when anything failed, is uncovered or untested. Functions run in parallel worker
processes (fork; Linux and macOS), slowest of the previous run first. A complete run writes tests.tsv
beside the rebuilt image (docs/formats.md): each function's cases and failures, the instructions no
case ran and the labels of its failing cases (dread gui's trace view offers those). With names given,
only those run and no summary file is written; DREAD_ALL_MISSED=1 lists every missed address.

    dread test [--target DIR] [--image NAME] [--recon PAYLOAD] [--keep-failing] [NAME...]
"""
import argparse
import json
import multiprocessing
import os
import sys

from dread import target as tgt
from dread.arch import code_address
from dread.build import HEADERS, SOURCES, recon_dir
from dread.cases import Suite
from dread.emu import EmuError, Layout, Machine
from dread.overlay import load_placement
from dread.program import Program
from dread.util.polite import die_with_parent, polite, resident, workers
from dread.util.poolorder import longest_first

RUN = {}        # what the workers need; filled before they are forked
from dread.fragile import STACK_SHIFT      # bytes the stack moves for the check that a case's outcome depends on it
STALE_SPAN = 0x400      # bytes below the stack pointer the stale-data check fills at every function entry
STALE_FILLS = (0xA5, 0x5A)  # the bytes it fills them with, one run each
REBUILT_BUDGET = 10     # the rebuilt image may take this many times a case's limit (C runs slower than
                        # the original: lifted C's loops take more instructions); a hang case gets the same


def generated_cases(name, fn):
    """The generated, kept and written cases of a function with auto(): [(label, addr, setup,
    args, limit, case dict or None)]."""
    from dread import autocase
    return autocase.cases_for(RUN, name, fn)


def kept_for(name):
    """(registers, why, argued) for a function: the scratch registers its callers read after the
    call (dread.regflow), to compare at the return besides the result; argued: those left out by
    an argument in the image's kept.tsv, {register: argument}. An argument that names the
    instructions it covers and does not cover them all (regflow.argued_kept) leaves its register
    compared, and its why says so."""
    flow = RUN.get("regflow")
    if flow is None:
        return (), {}, {}
    result = RUN["target"].abi.result[:1]
    arguments = RUN.get("kept_argued", {})
    lapsed = RUN.get("kept_lapsed", {})
    regs = [r for r in flow.kept_regs(name) if r not in result]
    argued = {r: arguments[(name, r)] for r in regs if (name, r) in arguments}
    regs = tuple(r for r in regs if r not in argued)
    why = {}
    for r in regs:
        why[r] = flow.why(name, r)
        if (name, r) in lapsed:             # (an argument of kept.tsv that does not cover where it ends)
            why[r] += "; kept.tsv's argument does not cover %s (dread regflow --why --deep)" % \
                " ".join("0x%x" % a for a in lapsed[(name, r)][1][:6])
    return regs, why, argued


def _original():
    return Machine(RUN["layout"], RUN["original"], isa_of=RUN["isa_of"])


def inside(name, words=(), rebuilt=False):
    """A test of a block's address: whether it is the function's own code. In the original: its extent
    and its instructions with the shared code its C holds (words); in the rebuilt image: where its C was
    placed, with the body behind a naked entry (dread.overlay's placement.json), or the original's when
    it was not built from C."""
    spans = RUN.get("placed", {}).get(name) if rebuilt else None
    if spans:
        return lambda a: any(lo <= a < hi for lo, hi in spans)
    fn = RUN["program"].by_name.get(name)
    lo, hi = (fn.entry, fn.entry + fn.size) if fn is not None else (0, 0)
    words = frozenset(words)
    return lambda a: lo <= a < hi or a in words


class Moved:
    """The outputs a change of the original's own run moves (its stack lower: fragile.stack_moved; stale
    stack data filled: stale_outputs): difference items (fragile.items), the log from the entry where
    the runs part (log_from). A difference between the images among them is one the original itself
    does not define."""

    def __init__(self, keys, log_from):
        self.keys, self.log_from = set(keys), log_from

    def explains(self, item):
        if isinstance(item, tuple) and item[0] == "log":
            return self.log_from is not None and item[1] >= self.log_from
        return item in self.keys


class After:
    """What an event of the original's run explains (a saved register touched, a call into the middle of
    a function, an instruction loaded as data: fragile.Timeline marks it): what comes after it, and
    nothing before it. After it the C, whose frames lie otherwise or which has other code there, may do
    anything: every register at the return, the log from the entry made after the event on, a byte
    either run wrote late (since the last tick before the event). Before it the C does what the
    original does: the log's entries before the event, and a byte whose final value both runs had at
    that tick, are compared. A rebuilt run that never gets to that tick (fewer calls out of the
    function or log entries: it went another way before the event) gets nothing explained (note says
    so); a run that faults (an original that faults after it) counts as after it only when the
    rebuilt run got there with the original's log and bytes."""

    def __init__(self, original, rebuilt, original_calls, rebuilt_calls):
        self.k, self.ticks = original.calls, original.event
        self.late = original.late | rebuilt.late
        self.reached, self.got = rebuilt.reached, rebuilt.ticks
        self.fault_after = rebuilt.reached and rebuilt.snapshot == original.snapshot and \
            list(rebuilt_calls[:self.k]) == list(original_calls[:self.k])
        self.note = None if self.reached else (
            "the rebuilt image's run never gets that far: %d moves out of the function and back and log "
            "entries, where the original makes %d before it" % (self.got, self.ticks))

    def explains(self, item):
        if not self.reached:
            return False
        if item == "return":
            return True
        if not isinstance(item, tuple):
            return False
        if item[0] in ("saved", "kept"):
            return True
        if item[0] == "log":
            return item[1] >= self.k
        if item[0] == "memory":
            return item[1] in self.late
        if item[0] in ("rebuilt", "original"):
            return self.fault_after
        return False


def stack_moved_it(name, addr, args, limit, setup, kept, why, first, left=()):
    """fragile.stack_moved for the run's original, as a Moved; None when the stack's position moves
    nothing (with the stack STACK_SHIFT bytes lower, the check that decides whether the reason holds).
    While a difference of left is one the outputs moved so far do not explain, the stack moves by
    fragile.MORE_SHIFTS too, one after another, and what they move is joined (a run that faults
    there adds nothing)."""
    from dread import fragile
    got = fragile.stack_moved(_original, RUN["layout"], setup, addr, args, limit, first, name, kept, why)
    if not got:
        return None
    moved = Moved(*got)
    for shift in fragile.MORE_SHIFTS:
        if all(moved.explains(i) for i in left):
            break
        more = fragile.stack_moved(_original, RUN["layout"], setup, addr, args, limit, first, name, kept, why,
                                   shift=shift, partial=False)
        if more:
            moved.keys |= more[0]
            if more[1] is not None:
                moved.log_from = more[1] if moved.log_from is None else min(moved.log_from, more[1])
    return moved


def saves_touched(name, addr, args, limit, setup, kept, why, timeline=None):
    """dread.fragile.saves_touched for the run's original (timeline: marked at the first touch)."""
    from dread import fragile
    return fragile.saves_touched(_original, RUN["program"], RUN["layout"], setup, addr, args, limit, name, kept, why,
                                 timeline=timeline)


def wild_call(name, addr, args, limit, setup, kept, why, timeline=None):
    """(site, target) of the first call through a register, anywhere in the original's run, that
    lands in the image but at no function's entry, or None (dread.emu.Machine.watch_calls). A generated
    input set a timer's callback to 127, a Thumb pointer into the middle of __aeabi_dmul at 0x7e
    (FreeRTOS's Cortex-M4F builds): the original ran that code with its registers and came back, the
    lifted prvReloadTimer called it with others and came to address 0. timeline: marked at that call
    (a fragile.Timeline, attached before the watch, so that the move out of the function that the call
    may be counts before it)."""
    from unicorn import UC_HOOK_BLOCK
    lay, program = RUN["layout"], RUN["program"]
    m = Machine(lay, RUN["original"], isa_of=RUN["isa_of"])
    m.plan, m.replay, m.function = [], False, name
    m.kept, m.kept_why = kept, why
    try:
        setup(m)
        if timeline is not None:
            timeline.attach(m)
        m.watch_calls(program, program.icall_sites())
        if timeline is not None:
            def mark(uc, address, size, user):
                if m.wild_calls:
                    timeline.mark(m)
            m.hook_add(UC_HOOK_BLOCK, mark)
        m.call(addr, *args, max_instructions=limit)
    except Exception:
        pass
    return (getattr(m, "wild_calls", None) or [None])[0]


def code_touch(name, addr, args, limit, setup, kept, why, timeline):
    """A written case's original run again, timeline marked at its first load of an instruction as data
    or store into the image (the conditions of Machine.trace_reads and wrote_into, watched here in
    Python so that the moment is known)."""
    from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
    lay, prog, image = RUN["layout"], RUN["program"], RUN["image"]
    code, owners = RUN["code"], RUN["owners"]
    lo, hi = image.base, image.end
    if owners is not None:
        hi = max(hi, lo + 2 * len(owners))
    m = Machine(lay, RUN["original"], isa_of=RUN["isa_of"])
    m.plan, m.replay, m.function = [], False, name
    m.kept, m.kept_why = kept, why

    def owner(a):
        h = (a - lo) >> 1
        return owners[h] if owners is not None and 0 <= h < len(owners) else 0

    def on_read(uc, access, address, size, value, user):
        if any(a in code for a in range(address & ~1, address + size, 2)) or \
                owner(address) and owner(uc.reg_read(lay.pc) & ~1) != owner(address):
            timeline.mark(m)

    def on_write(uc, access, address, size, value, user):
        if not lay.in_stack(address):
            timeline.mark(m)
    try:
        setup(m)
        timeline.attach(m)
        m.hook_add(UC_HOOK_MEM_READ, on_read, begin=lo, end=hi - 1)
        m.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=image.base, end=prog.writable_end() - 1)
        m.call(addr, *args, max_instructions=limit)
    except Exception:
        pass


def event_after(name, addr, args, budget, setup, kept, why, plan, timeline, words=()):
    """An After for the event timeline marked in the original's run: the rebuilt image's run of the case
    again (its stubs' plan replayed, its budget), with a timeline to the same tick."""
    from dread import fragile
    lay = RUN["layout"]
    m = Machine(lay, RUN["recon"], RUN["aliases"], isa_of=RUN["isa_of"])
    m.plan, m.replay, m.function = list(plan), True, name
    m.kept, m.kept_why = kept, why
    rebuilt = fragile.Timeline(lay, inside(name, words, rebuilt=True), at=timeline.event)
    try:
        setup(m)
        rebuilt.attach(m)
        m.call(addr, *args, max_instructions=budget)
    except Exception:
        pass
    return After(timeline, rebuilt, list(timeline.m.calls), list(m.calls))


def stale_outputs(name, addr, args, limit, setup, kept, why, first, rebuilt):
    """The outputs in which the images differ that stale stack data the original read decides: the
    original runs again with STALE_SPAN bytes below the stack pointer filled at every function entry
    (once with each of STALE_FILLS), so that an uninitialised local, of the function or of a callee,
    reads the fill instead of what earlier frames left; an output that changes there too is one the
    original itself does not define, and C cannot be held to it (it lays out its frames otherwise
    and saves other values in them; FreeRTOS's timer commands carry a union member nobody set into
    the timer queue). Returns a Moved of the outputs the fills move (the log from the first entry
    either fill moves: the entries before it are compared; until 2026-10-24 the whole log was one
    output, left out when any entry moved), or None when they move none in which the images differ,
    or the original fails with a fill."""
    from dread import fragile
    images = fragile.items(first, rebuilt)
    if not images:
        return None
    lay = RUN["layout"]
    entries = frozenset(code_address(lay.arch, e) for e in Machine.entries.get((lay.target.file, lay.image.name), ()))
    keys, log_from = set(), None
    for fill in STALE_FILLS:
        m = Machine(lay, RUN["original"], isa_of=RUN["isa_of"])
        m.plan, m.replay, m.function = [], False, name
        m.kept, m.kept_why = kept, why
        m.stale_fill = (fill, STALE_SPAN, entries | {code_address(lay.arch, addr)})
        try:
            setup(m)
            again = m.call(addr, *args, max_instructions=limit)
        except Exception:
            return None
        for k in fragile.items(first, again):
            if isinstance(k, tuple) and k[0] == "log":
                log_from = k[1] if log_from is None else min(log_from, k[1])
            else:
                keys.add(k)
    stale = Moved(keys, log_from)
    return stale if any(stale.explains(i) for i in images) else None


def filled_alike(name, addr, args, limit, setup, kept, why, generated):
    """Whether both images agree with the stack below every function's entry filled alike: the case run
    again, in the original and in the rebuilt image, once with each of STALE_FILLS (STALE_SPAN bytes
    below the stack pointer at each entry of a function of the original, which the rebuilt image enters
    at the same address, its own code, a moved function's branch or its C in place, and at each entry
    of C the rebuilt image placed elsewhere, which C callers call directly: c_places), compared whole.
    A case whose plain runs differ only in what earlier frames left in a local nothing wrote agrees so:
    in the original that is what its callers' frames left, in the C what other frames, laid out
    otherwise, left, and with the fill both read the same. A translation that differs shows under the
    fills too: a value the original writes would have to equal both fills to hide it. FreeRTOS's GCC -O0
    prvCreateAndDeleteStaticallyAllocatedQueues agrees so in the lift check (a test counter written 254
    against 249 from what earlier frames left; 2026-10-26). It does not reach a local the C itself
    writes before reading it uninitialised: FreeRTOS's xStreamBufferReceive on the GCC -O2 builds reads a
    message length memcpy was stubbed to leave unwritten, and its readable C reserves that local with
    push {r0, r1, r2, ...}, so it reads the pushed r0, not the fill (still failed)."""
    lay = RUN["layout"]
    known = Machine.entries.get((lay.target.file, lay.image.name), ())
    entries = frozenset(code_address(lay.arch, e) for e in known) | {code_address(lay.arch, addr)} | \
        {start for spans in (RUN.get("placed") or {}).values() for start, _ in spans}
    loop_case = bool(generated and generated.get("hang") and (generated.get("stub_calls") or generated.get("stops")))
    for fill in STALE_FILLS:
        plan, out = [], []
        for which, payload in (("original", RUN["original"]), ("rebuilt", RUN["recon"])):
            m = Machine(lay, payload, RUN["aliases"] if which == "rebuilt" else None, isa_of=RUN["isa_of"])
            m.plan, m.replay, m.function = plan, which == "rebuilt", name
            m.kept, m.kept_why = kept, why
            m.stale_fill = (fill, STALE_SPAN, entries)
            try:
                setup(m)
                budget = limit if which == "original" or (getattr(m, "may_hang", False) and not loop_case) \
                    else limit * REBUILT_BUDGET
                out.append(m.call(addr, *args, max_instructions=budget))
            except Exception:
                return False
        if out[0].diff(out[1]):
            return False
    return True


def items_text(keys):
    """A short text of difference items (fragile.items): memory as address runs, the rest in words."""
    memory = sorted(k[1] for k in keys if isinstance(k, tuple) and k[0] == "memory")
    runs = []
    for a in memory:
        if runs and a == runs[-1][1] + 1:
            runs[-1][1] = a
        else:
            runs.append([a, a])
    shown = ["memory " + ", ".join("0x%x" % lo if lo == hi else "0x%x-0x%x" % (lo, hi) for lo, hi in runs[:4])
             + (", ..." if len(runs) > 4 else "")] if runs else []
    words = []
    for k in keys:
        if k == "return":
            words.append("the return value")
        elif isinstance(k, str):
            words.append(k)
        elif k[0] in ("saved", "kept"):
            words.append("%s after the return" % k[1])
        elif k[0] == "log":
            words.append("the log from entry %d" % (k[1] + 1))
        elif k[0] in ("rebuilt", "original"):
            words.append("the %s's run (%s)" % ("rebuilt image" if k[0] == "rebuilt" else "original", k[1]))
        elif k[0] != "memory":
            words.append(" ".join("%s" % x for x in k))
    return "; ".join(shown + sorted(words))


outputs_text = items_text   # (its name until 2026-10-24)


class Reason:
    """A refusal's reason that applies to a case (kind: code, wild, stack, stale, saves): what it explains
    (explains(item), a difference item of fragile.items; got: the case's differences it does explain),
    its words in a REFUSED line (refused) and in a FAIL line (reason; outside: where it explains
    nothing; note: why it explains nothing at all), and the scope of what it explains."""

    def __init__(self, kind, explains, refused, reason, scope, outside, got=(), note=None):
        self.kind, self.explains, self.refused, self.reason = kind, explains, refused, reason
        self.scope, self.outside, self.got, self.note = scope, outside, set(got), note


def fragile_items(out):
    """The difference items of a case's two outcomes (fragile.items); an outcome that is an error is
    one item, ("original" or "rebuilt", its text)."""
    from dread import fragile
    if not isinstance(out[0], Exception) and out[1] is not None and not isinstance(out[1], Exception):
        return fragile.items(out[0], out[1])
    return {(which, "%s" % o) for which, o in zip(("original", "rebuilt"), out) if isinstance(o, Exception)}


def judge(name, addr, args, limit, setup, kept, why, words, out, diff, depends, code_touched, recorded, budget):
    """The refusals' reasons that apply to a case whose images differ, and the differences none of them
    explains: (reasons, left, stale: the stale outputs' Moved or None). Checked in this order, each only
    while a difference is left: the instructions a written case loads as data or the image it stores
    into (code_touch), a call into the middle of a function (wild_call), where the stack lies
    (stack_moved_it), the original's stale stack data (stale_outputs), the saved registers the input
    makes the original touch (saves_touched). An event's reason (a load or store, a call, a touch)
    explains what comes after the event (After), a moved output's (the stack, stale data) the outputs
    that change in the original (Moved). run_function refuses a case only when the reasons together
    explain every difference, compares it except the stale outputs when those alone do, and fails it
    with the reasons named when a difference is left (decided 2026-10-24, docs/decisions.md: until then
    a reason that applied refused the case whatever else differed)."""
    from dread import fragile
    layout = RUN["layout"]
    orig_ran = not isinstance(out[0], Exception)
    reb_ran = len(out) == 2 and out[1] is not None and not isinstance(out[1], Exception)
    reasons, stale = [], None
    if not diff or depends or len(out) != 2 or out[1] is None or not (orig_ran or code_touched):
        return reasons, set(), stale
    items = fragile_items(out)
    left = set(items)

    def timed(kind, timeline, refused_text, reason, scope, outside):
        nonlocal left
        after = event_after(name, addr, args, budget, setup, kept, why, recorded, timeline, words) \
            if timeline.calls is not None else None     # (a moment not found again explains nothing)
        explains = after.explains if after else (lambda item: False)
        got = {i for i in items if explains(i)}
        left -= got
        reasons.append(Reason(kind, explains, refused_text, reason, scope, outside, got,
                              after.note if after else "its moment was not found again"))

    if code_touched:
        tl = fragile.Timeline(layout, inside(name, words))
        code_touch(name, addr, args, limit, setup, kept, why, tl)
        timed("code", tl, "the input loads instructions as data or stores into the image; remove it",
              "the instructions the input loads as data or the image it stores into",
              "after the first such load or store", "before the first such load or store")
    if left and orig_ran:
        wild = wild_call(name, addr, args, limit, setup, kept, why)
        if wild:
            tl = fragile.Timeline(layout, inside(name, words))
            wild_call(name, addr, args, limit, setup, kept, why, timeline=tl)
            owner = RUN["program"].function_at(wild[1])
            where = "inside %s but not its entry" % owner.name if owner else "into the image's data"
            timed("wild", tl, "the original calls 0x%x from 0x%x, %s (a code pointer the input gave): the rebuilt "
                  "image has other code there, or calls it with other values in the other registers; not compared"
                  % (wild[1], wild[0], where),
                  "the call of 0x%x from 0x%x, %s (a code pointer the input gave)" % (wild[1], wild[0], where),
                  "after that call", "before that call")
    if left and orig_ran and reb_ran:
        moved = stack_moved_it(name, addr, args, limit, setup, kept, why, out[0], left)
        if moved:
            got = {i for i in items if moved.explains(i)}
            left -= got
            reasons.append(Reason(
                "stack", moved.explains, "the original's outcome depends on where the stack lies (it differs with "
                "the stack %d bytes lower): a stack address taken for a value; not compared" % STACK_SHIFT,
                "where the stack lies (with it lower the original changes %s)"
                % (items_text(got) or "in other outputs"),
                "where the lower stack moves the original too", "where the lower stack moves nothing", got))
    if left and orig_ran and reb_ran:
        stale = stale_outputs(name, addr, args, limit, setup, kept, why, out[0], out[1])
        if stale:
            stale.shown = {i for i in items if stale.explains(i)}
            left -= stale.shown
            reasons.append(Reason("stale", stale.explains, None, None, "where the original's stale stack data "
                                  "decides", None, stale.shown))
    if left and orig_ran and reb_ran:
        tl = fragile.Timeline(layout, inside(name, words))
        smash = saves_touched(name, addr, args, limit, setup, kept, why, timeline=tl)
        if smash:
            timed("saves", tl, "the input makes the original touch its own saved registers (%s; a length past a "
                  "local): C keeps them elsewhere; not compared" % smash,
                  "the saved registers the input makes the original touch (%s)" % smash,
                  "after the touch", "before the touch")
    return reasons, left, stale


def run_function(name):
    """All cases of one function: (name, cases, failed, output lines, the instructions of the original
    no case ran)."""
    suite, prog, layout = RUN["suite"], RUN["program"], RUN["layout"]
    fn = prog.by_name.get(name)
    if fn is not None and RUN.get("bodies", {}).get(name):
        from dread.lift import Body         # its C holds shared code: cases cover that code too
        fn = Body(fn, RUN["bodies"][name])
    words = fn.insns if fn else []
    cases = [c + (None,) for c in suite.cases.get(name, [])]
    lines = []
    if name in suite.auto:
        try:
            cases += generated_cases(name, fn)
        except Exception as e:             # the generator failed: written cases still count
            if not cases:
                return name, 0, 1, ["FAIL %s [case generation]" % name, "     %s" % e], []
            lines.append("FAIL %s [case generation]: %s" % (name, e))
    image = RUN["image"]
    kept, why, argued = kept_for(name)
    bad, covered, failing, refused = 0, set(), [], 0
    for label, addr, setup, args, limit, generated in cases:
        out, plan, recorded, case_covered, budget, code_touched = [], [], [], set(), limit, False
        for which, payload in (("original", RUN["original"]), ("rebuilt", RUN["recon"])):
            m = Machine(layout, payload, RUN["aliases"] if which == "rebuilt" else None, isa_of=RUN["isa_of"])
            m.plan, m.replay = plan, which == "rebuilt"
            m.function = name
            m.kept, m.kept_why = kept, why
            try:
                setup(m)
            except Exception as e:
                out = [EmuError("the case's setup failed: %s: %s" % (type(e).__name__, e)), None]
                break
            if code_address(layout.arch, addr) in m.stubs:
                out = [EmuError("the case stubs the function it is meant to test"), None]
                break
            if which == "original" and words and addr == fn.entry:
                from dread.autocase import cover_span
                m.cover(*cover_span(prog, words))
            written = label.startswith("written")
            if which == "original" and written:
                m.trace_reads(image.base, image.end, RUN["code"], RUN["owners"])
            # a case that ends at a call (a loop case's stub call, a stop) not at the limit: the C may take
            # more instructions to get there
            loop_case = bool(generated and generated.get("hang") and (generated.get("stub_calls") or generated.get("stops")))
            budget = limit if which == "original" or (getattr(m, "may_hang", False) and not loop_case) \
                else limit * REBUILT_BUDGET
            try:
                out.append(m.call(addr, *args, max_instructions=budget))
            except Exception as e:          # EmuError, or anything a case's own code raises
                out.append(e if isinstance(e, EmuError) else EmuError("%s: %s" % (type(e).__name__, e)))
            if which == "original":
                recorded = list(plan)       # (what the rebuilt run replays: for a run of it again)
                code_touched = written and bool(m.code_reads or m.wrote_into(image.base, prog.writable_end()))
                case_covered = m.covered_insns(words)
        depends = None
        regs = RUN["suite"].caller_regs.get(name)
        if regs and out and not isinstance(out[0], Exception):
            m = Machine(layout, RUN["original"], isa_of=RUN["isa_of"])
            m.plan, m.replay, m.function = [], False, name
            setup(m)
            for r in regs:
                m.reg("r%d" % r, 0x3C5A0F00 + 0x13 * r)
            try:
                again = m.call(addr, *args, max_instructions=limit)
                if (again.ret, again.ranges, again.calls) != (out[0].ret, out[0].ranges, out[0].calls):
                    depends = "the original's result depends on what its caller left in %s" % \
                        " or ".join("r%d" % r for r in regs)
            except EmuError as e:
                depends = "the original fails with other values in %s: %s" % (" or ".join("r%d" % r for r in regs), e)
        if len(out) == 2 and out[1] is None:
            diff = [str(out[0])]
        elif isinstance(out[0], Exception) or isinstance(out[1], Exception):
            diff = ["original: %s" % out[0] if isinstance(out[0], Exception) else "original ran",
                    "rebuilt: %s" % out[1] if isinstance(out[1], Exception) else "rebuilt ran"]
        else:
            diff = out[0].diff(out[1])
        if depends:
            diff = list(diff) + [depends]
        if code_touched and not diff:
            refused += 1
            lines.append("REFUSED %s [%s]: the input loads instructions as data or stores into the image; "
                         "remove it" % (name, label))
            continue
        reasons, left, stale = judge(name, addr, args, limit, setup, kept, why, words, out, diff, depends,
                                     code_touched, recorded, budget)
        refusing = [r for r in reasons if r.kind != "stale"]
        if reasons and not left:
            # (named: the reasons that explain a difference, and a written case's load of code always)
            named = [r for r in reasons if r.got or r.kind == "code"]
            if any(r.kind != "stale" for r in named):
                refused += 1
                lines.append("REFUSED %s [%s]: %s (it differs only %s: %s)" % (
                    name, label, "; ".join(r.refused for r in named if r.kind != "stale"),
                    " or ".join(r.scope for r in named), items_text(fragile_items(out))))
                continue
            # Every difference is an output the original's stale stack data decides: compared except
            # those (decided 2026-10-20, docs/decisions.md; such a case was refused before)
            lines.append("     %s [%s]: compared except what the original's stale stack data (an uninitialised "
                         "local) decides: %s" % (name, label, items_text(stale.shown)))
            covered |= case_covered
            continue
        if diff and not refusing and not depends and not code_touched and not isinstance(out[0], Exception) and \
                len(out) == 2 and out[1] is not None and \
                filled_alike(name, addr, args, limit, setup, kept, why, generated):
            lines.append("     %s [%s]: agrees with the stack below every function's entry filled alike (%s): the "
                         "plain runs differ only in what earlier frames left in a local nothing wrote"
                         % (name, label, ", ".join("0x%02x" % f for f in STALE_FILLS)))
            covered |= case_covered
            continue
        if stale:
            diff = list(diff) + ["(of these, the original's stale stack data decides %s)"
                                 % items_text(stale.shown)]
        covered |= case_covered
        if diff:
            bad += 1
            if generated is not None:
                failing.append(generated)
            note = ""
            if refusing:
                # A refusal stands only where it explains every difference (2026-10-24, docs/decisions.md):
                # here one is left that it does not
                note = ": refused for %s, but it differs %s: %s%s" % (
                    " and for ".join(r.reason for r in refusing),
                    refusing[0].outside if len(refusing) == 1 else "where none of them explains",
                    items_text(left), "".join(" (%s)" % r.note for r in refusing if r.note and not r.got))
            lines.append("FAIL %s [%s]%s" % (name, label, note))
            lines += ["     " + d for d in diff]
    if failing and RUN["keep_failing"]:
        from dread import autocase
        autocase.kept_add(RUN["image"], name, failing, "stubbed")
    declared = suite.unreachable.get(name, set())
    missed = sorted(set(words) - covered - declared)
    wrong = sorted(covered & declared)
    if wrong:
        bad += 1
        lines.append("FAIL %s [unreachable]: %d instructions declared unreachable did run: %s"
                     % (name, len(wrong), " ".join("0x%x" % a for a in wrong[:12])))
    if missed:
        shown = missed if os.environ.get("DREAD_ALL_MISSED") else missed[:12]
        lines.append("UNCOVERED %s: %d of %d instructions of the original never ran: %s"
                     % (name, len(missed), len(words), " ".join("0x%x" % a for a in shown)))
    total = len(cases) - refused
    lines.append("%-4s %-26s %d/%d cases agree%s" % ("ok" if not bad else "FAIL", name, total - bad, total,
                                                    ", %d refused" % refused if refused else ""))
    for reg, argument in sorted(argued.items()):
        lines.append("     %s at the return not compared (kept.tsv): %s" % (reg, argument))
    return name, len(cases), bad, lines, missed


def c_places(path, arch=None):
    """{function: [(start, end)]}: where the rebuilt image holds each function's C (dread.overlay's
    placement.json), with the body behind its naked entry (NAME_dread_body, a helper of its own);
    empty without the file."""
    import json
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f).get("functions", {})
    out = {}
    for name, v in data.items():
        if v.get("placement") == "helper" or "address" not in v:
            continue
        start = code_address(arch, v["address"])
        spans = [(start, start + v.get("c_bytes", 0))]
        body = data.get(name + "_dread_body")
        if body and "address" in body:
            start = code_address(arch, body["address"])
            spans.append((start, start + body.get("c_bytes", 0)))
        out[name] = spans
    return out


def prepare(target, image, recon_path, keep_failing=False):
    """Fill RUN for a run over one image; returns the suite."""
    prog = Program(target, image)
    layout = Layout(target, image)
    suite = Suite(target, image, prog, layout).load()
    with open(recon_path, "rb") as f:
        recon = f.read()
    aliases, built = load_placement(os.path.join(os.path.dirname(os.path.abspath(recon_path)), "placement.json"))
    placed = c_places(os.path.join(os.path.dirname(os.path.abspath(recon_path)), "placement.json"), prog.arch)
    from dread.build import read_names
    here = os.path.dirname(os.path.abspath(recon_path))
    lifted_info = {}
    if os.path.exists(os.path.join(here, "lifted.json")):
        import json
        with open(os.path.join(here, "lifted.json")) as f:
            lifted_info = json.load(f)
    system = set(lifted_info.get("system", ()))  # never return: dread sysrun's scenarios test them
    loops = set(lifted_info.get("loop", ()))    # never return: loop cases test them (dread.autocase)
    system -= loops
    for name in read_names(os.path.join(here, "lifted.txt")):
        if name in loops:
            suite.auto.setdefault(name, {"loop": True})
        elif name not in system:
            suite.auto.setdefault(name, {})     # lifted C is tested with generated cases, callees real
    bodies = {n: list(a) for n, a in lifted_info.get("bodies", {}).items()}   # shared code in the C
    for name, addrs in lifted_info.get("unreached", {}).items():
        # no path from the entry reaches them (dread lift); one that runs fails the function
        suite.unreachable.setdefault(name, set()).update(addrs)
    Machine.entries[(target.file, image.name)] = {fn.entry for fn in prog.functions}
    from dread import regflow
    flow = regflow.load(target, image, prog)
    kept_argued, kept_lapsed = regflow.argued_kept(prog, flow, image)
    RUN.clear()
    RUN.update(system=system, bodies=bodies)
    RUN.update(target=target, image=image, program=prog, layout=layout, suite=suite, original=image.payload(),
               recon=recon, aliases=aliases, built=built, keep_failing=keep_failing,
               isa_of=prog.isa_at, code=prog.instruction_halves(), owners=prog.extent_owners(), regflow=flow,
               kept_argued=kept_argued, kept_lapsed=kept_lapsed, placed=placed)
    if suite.auto:                          # the generated cases' keys (dread.autocase's environment): their
        from dread import autocase          # digests found once here, before the workers fork, not in each
        try:
            gen = autocase.generator_for(RUN)
            gen.whole()
            gen.scopes()
        except Exception:                   # (the workers meet it again where it matters)
            pass
    return suite


def failed_labels(name, lines):
    """The labels of the cases a function's FAIL lines name ("FAIL NAME [label]", with or without the
    words after it), in their order; not [case generation] nor [unreachable], which are no case."""
    prefix, out = "FAIL %s [" % name, []
    for text in lines:
        for line in text.splitlines():
            if not line.startswith(prefix):
                continue
            rest = line[len(prefix):]
            end = len(rest) - 1 if rest.endswith("]") else rest.find("]: ")
            label = rest[:end] if end > 0 else None
            if label and label not in ("case generation", "unreachable") and label not in out:
                out.append(label)
    return out


def run(names, only=False, out=print, jobs=None):
    """Run the functions `names` (RUN prepared); returns (failed, uncovered, results). RUN["missed"]
    keeps, per function, the instructions of the original no case ran, RUN["failing"] the labels of
    its cases that failed (failed_labels)."""
    failed, uncovered, results = 0, [], []
    RUN["missed"], RUN["failing"] = {}, {}
    times = os.path.join(RUN["target"].build, "times", "tests-%s%s.json" % (RUN["image"].name, "-some" if only else ""))
    with multiprocessing.get_context("fork").Pool(jobs or workers(memory_each=resident()), initializer=die_with_parent) as pool:
        for name, count, bad, lines, missed in longest_first(pool, run_function, names, str, times):
            out("\n".join(lines))
            failed += bad
            results.append((name, count, bad))
            RUN["missed"][name] = missed
            if bad:
                RUN["failing"][name] = failed_labels(name, lines)
            if missed:
                uncovered.append(name)
    return failed, uncovered, results


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread test", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--recon", help="the rebuilt image (default: BUILD/recon/IMAGE/payload.bin)")
    parser.add_argument("--keep-failing", action="store_true", help="keep failing generated cases")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)
    polite()
    try:
        target, image = tgt.from_args(args)
        recon_path = args.recon or os.path.join(recon_dir(target, image), "payload.bin")
        if not os.path.exists(recon_path):
            raise tgt.TargetError("no rebuilt image at %s: run `dread recon` first" % recon_path)
        if not args.recon and os.path.isdir(image.src):
            built = os.path.getmtime(recon_path)
            newer = [os.path.join(d, f) for d, _, fs in os.walk(image.src) for f in fs
                     if f.endswith(SOURCES + HEADERS) and os.path.getmtime(os.path.join(d, f)) > built]
            if newer:
                raise tgt.TargetError("%s is older than %s: run `dread recon`" % (recon_path, ", ".join(sorted(newer))))
        suite = prepare(target, image, recon_path, args.keep_failing)
    except (tgt.TargetError, EmuError) as e:
        print("dread test: %s" % e, file=sys.stderr)
        return 1
    names = [n for n in list(suite.cases) + [n for n in suite.auto if n not in suite.cases]
             if not args.names or n in args.names]
    failed, uncovered, results = run(names, only=bool(args.names))
    if args.names:
        missing = [n for n in args.names if n not in names]
        for n in missing:
            print("UNTESTED %s" % n)
        return 1 if failed or missing or uncovered else 0
    with open(os.path.join(os.path.dirname(os.path.abspath(recon_path)), "tests.tsv"), "w") as f:
        f.write("function\tcases\tfailed\tmissed\tfailing\n")
        f.writelines("%s\t%d\t%d\t%s\t%s\n" % (n, c, b, " ".join("0x%x" % a for a in RUN["missed"].get(n, ())),
                                                 json.dumps(RUN["failing"][n]) if RUN["failing"].get(n) else "")
                     for n, c, b in results)
    system = RUN.get("system", set())
    for fn in sorted(system & set(RUN["built"])):
        print("SYSTEM %s: it never returns or is an exception handler; the system run's scenarios test it"
              " (dread sysrun)" % fn)
    untested = [fn for fn in RUN["built"] if fn not in names and fn not in system]
    for fn in untested:
        print("UNTESTED %s" % fn)
    cases = sum(c for _, c, _ in results)
    print("dread test: %d functions, %d cases, %d failed, %d uncovered, %d untested"
          % (len(results), cases, failed, len(uncovered), len(untested)))
    return 1 if failed or untested or uncovered else 0


if __name__ == "__main__":
    sys.exit(main())
