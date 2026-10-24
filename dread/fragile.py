"""Inputs whose outcome belongs to the original's frames: where its stack lies, or a saved register
an input made code touch. C lays out its frames otherwise and cannot be held to either, so dread test
refuses a case with one of them when its images differ (dread.runner), dread callconv measures
without such cases, and the case generator does not let them stand for the instructions they reach
when an input without them reaches those too (dread.autocase.Generator.fragile: a case the lift
check refuses covers nothing).

Both checks run the original alone: make() returns a fresh machine with the original image,
setup(m) gives it a case's inputs.

Such a reason accounts for some differences between the images and not for others (dread.runner
refuses a case only when it accounts for every one): stack_moved names the outputs the stack's
position moves in the original, and a Timeline finds the moment of an event in a run (a saved
register touched, a call into the middle of a function) and the same moment in the other image's
run, so that what comes before it can be told from what comes after.
"""

from dread.emu import ARGS_ROOM, Result

STACK_SHIFT = 0x40      # bytes the stack moves for the check that a case's outcome depends on it
# The bytes it moves by besides, for what that check explains while a difference is left (dread.runner):
# a stack address may pick one of a few outputs (a random generator it seeds chooses the counter it adds
# to: FreeRTOS's prvQueueSetSendingTask), and one shift can leave alone the output the C's frame picked.
# 0x8 changes the low bits a shift by 0x40 keeps, 0x100 higher ones, 0x2a8 several at once; each keeps
# the stack's 8-byte alignment.
MORE_SHIFTS = (0x8, 0x100, 0x2a8)


def parting(a, b):
    """The index of the first entry where two logs part (where the shorter one ends, if it is the
    other's beginning), or None when they are equal."""
    if a == b:
        return None
    return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))


def items(first, other):
    """The outputs in which two results differ: dread.emu.Result.differences, the log not as one
    item "calls" but as ("log", the index of the first entry where the logs part)."""
    keys = first.differences(other)
    if "calls" in keys:
        keys.discard("calls")
        keys.add(("log", parting(first.calls, other.calls)))
    return keys


class Timeline:
    """One run up to an event, and the same moment in the other image's run.

    The clock ticks at every move between the function under test's own code and the rest (a call out
    of it, a return into it; inside(address) says whether a block's address is its code, counted from
    its first block) and at every entry of the run's log (stub calls, device accesses: Machine.log).
    C that does what the original does makes the same calls and the same log in the same order, so
    the n-th tick is the same moment in both images; between two ticks they may differ in the order
    of their instructions and stores. In the original (at=None), mark(m) at the event records the
    log's length then (calls), the ticks before it (event) and the bytes outside the stack as they were
    at the last tick (snapshot: {address: value} of what was written until then); what was written
    since that tick and after the event is late (ticks goes on counting to the run's end). In the
    other image's run (at=n), the snapshot is taken at its n-th tick, what is written after it is
    late, and reached says whether the run got there. A byte written outside the stack whose final
    value differs between the images and that neither run wrote late had that value already at the
    tick before the event: it differs before the event.

    attach(m) adds the hooks (after the case's setup; through Machine.hook_add, so the machine's reset
    takes them away) and wraps m.log. Every write hook runs before the store (Unicorn and DREAD's
    engines), so the old bytes are read from memory there."""

    def __init__(self, layout, inside, at=None):
        self.in_stack, self.inside, self.at = layout.in_stack, inside, at
        self.ticks, self.was = 0, None      # was: whether the last block was the function's (None: before its first)
        self.written = set()                # bytes outside the stack written so far
        self.undo = {}                      # (the original) byte -> its value before the first write since the
                                            # last tick, None when it had not been written before
        self.snapshot = {} if at == 0 else None
        self.late = set()
        self.calls = None                   # the log's length at the event (the original)
        self.event = None                   # the ticks before the event (the original)
        self.reached = at == 0              # (the other image) the run got to tick at

    def attach(self, m):
        from unicorn import UC_HOOK_BLOCK, UC_HOOK_MEM_WRITE
        self.m = m
        log = m.log

        def logged(*event):
            log(*event)
            self.tick()
        m.log = logged
        m.hook_add(UC_HOOK_BLOCK, self.on_block)
        m.hook_add(UC_HOOK_MEM_WRITE, self.on_write)

    def on_block(self, uc, address, size, user):
        inside = bool(self.inside(address & ~1))
        if self.was is None:
            if inside:                      # (a moved function is entered through the branch at its old entry)
                self.was = True
            return
        if inside != self.was:
            self.was = inside
            self.tick()

    def tick(self):
        self.ticks += 1
        if self.at is None:
            if self.calls is None:
                self.undo = {}
        elif self.ticks == self.at and self.snapshot is None:
            self.snapshot = self.values(self.written)
            self.reached = True

    def on_write(self, uc, access, address, size, value, user):
        old = None
        for i, a in enumerate(range(address, address + size)):
            if self.in_stack(a):
                continue
            if self.snapshot is not None:
                self.late.add(a)
            elif self.at is None and a not in self.undo:
                if old is None:
                    old = bytes(uc.mem_read(address, size))
                self.undo[a] = old[i] if a in self.written else None
            self.written.add(a)

    def values(self, addresses):
        """{address: the byte memory holds there now}, read a run at a time."""
        out, run = {}, []
        for a in sorted(addresses) + [None]:
            if run and (a is None or a != run[-1] + 1):
                out.update(zip(run, self.m.read(run[0], len(run))))
                run = []
            if a is not None:
                run.append(a)
        return out

    def mark(self, m):
        """The event, in the original's run: once."""
        if self.calls is not None or self.at is not None:
            return
        self.calls, self.event = len(m.calls), self.ticks
        snapshot = self.values(self.written)
        for a, old in self.undo.items():
            if old is None:
                snapshot.pop(a, None)
            else:
                snapshot[a] = old
        self.snapshot, self.late = snapshot, set(self.undo)


def stack_moves(make, layout, setup, addr, args, limit, first, name=None, kept=(), why=None):
    """Whether the original's outcome changes when its stack starts STACK_SHIFT bytes lower: then
    the case made it take a stack address for a value (a seed of a random generator, a pointer
    past a local array by an index the input made too large), which C, whose frames lie otherwise,
    cannot reproduce. first: the outcome of the run in place. Writes into the room for stack
    arguments are left out (that room moves into the stack, whose writes are not compared)."""
    m = make()
    m.plan, m.replay, m.function = [], False, name
    m.kept, m.kept_why = kept, why or {}
    m.stack_shift = STACK_SHIFT
    try:
        setup(m)
        again = m.call(addr, *args, max_instructions=limit)
    except Exception:                       # it fails or hangs only there
        return True
    lo, hi = layout.stack_top, layout.stack_top + ARGS_ROOM

    def outside(r):
        return Result(r.ret, {a: v for a, v in r.writes.items() if not lo <= a < hi}, r.calls, r.saved,
                      r.layout, r.kept, r.why)
    return bool(outside(first).diff(outside(again)))


def stack_moved(make, layout, setup, addr, args, limit, first, name=None, kept=(), why=None, shift=STACK_SHIFT,
                partial=True):
    """What of the original's outcome changes when its stack starts shift bytes lower (as
    stack_moves): (keys, log_from), the outputs that change as items() names them (the log apart:
    log_from, the index of its first entry that changes, or None), or None when nothing changes.
    When the run with the stack lower faults or does not return, what it did until it stopped stands
    for it (partial; else None: a shift that runs a deep case off the stack's end says nothing about
    the code): the log from the entry where it parts, every byte it left otherwise than the run in
    place or did not write, and every register at the return. Writes into the room for stack
    arguments are left out (that room moves with the stack). dread test refuses a case for where the
    stack lies only when every difference between its images is among these outputs (of STACK_SHIFT,
    and of MORE_SHIFTS while a difference is left)."""
    m = make()
    m.plan, m.replay, m.function = [], False, name
    m.kept, m.kept_why = kept, why or {}
    m.stack_shift = shift
    lo, hi = layout.stack_top, layout.stack_top + ARGS_ROOM
    mine = {a: v for a, v in first.writes.items() if not lo <= a < hi}
    try:
        setup(m)
        again = m.call(addr, *args, max_instructions=limit)
    except Exception:                       # it fails or hangs only there: what it did until then
        if not partial:
            return None
        try:
            theirs = {a: v for start, data in m._read_written() for a, v in zip(range(start, start + len(data)), data)
                      if not lo <= a < hi and not layout.in_stack(a)}
        except Exception:
            theirs = {}
        keys = {("memory", a) for a in set(mine) | set(theirs) if mine.get(a) != theirs.get(a)}
        keys |= {"return"} | {("saved", n) for n in layout.saved_names} | {("kept", n) for n, _ in first.kept}
        return keys, parting(first.calls, list(m.calls))
    keys = items(Result(first.ret, mine, first.calls, first.saved, first.layout, first.kept, first.why),
                 Result(again.ret, {a: v for a, v in again.writes.items() if not lo <= a < hi}, again.calls,
                        again.saved, again.layout, again.kept, again.why))
    log_from = next((k[1] for k in keys if isinstance(k, tuple) and k[0] == "log"), None)
    keys = {k for k in keys if not (isinstance(k, tuple) and k[0] == "log")}
    return (keys, log_from) if keys or log_from is not None else None


def saves_touched(make, program, layout, setup, addr, args, limit, name=None, kept=(), why=None, timeline=None):
    """Where the original's run touches a saved register's slot (a callee-saved register or the
    return address that a push or a store through sp put into a frame still on the stack, of the
    function or of any callee) with anything but a push, a pop, or a save or restore of that register:
    then the input made code read or write saved registers through a pointer, mostly a length past
    a local (a queue whose item size the input made larger than the item: memcpy wrote over the saved
    r4 of FreeRTOS's xFirstTimerHandler, which its pop then restored, and over xSecondTimerHandler's
    beneath TIMER0_Handler; prvNotifyQueueSetContainer copied its own saved r7 into a queue). C lays
    out its frames otherwise and cannot be held to it. Returns "write of the saved REG at 0xADDR by
    FUNCTION" or "read ...", or None. Pops of any function count as pops (an MSP430 epilogue is a
    function of its own); the scratch registers GCC pushes only to make room (push {r0, r1, r4, lr},
    push {r3, lr}) are no saves. It watches every instruction: dread test runs it only for a case
    whose images differ. timeline (a Timeline): attached to the run and marked at the first touch, so
    that the runner can tell the differences before it, which the touch does not explain, from those
    after it."""
    from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE
    prog, lay = program, layout
    arch = prog.arch
    if not hasattr(arch, "pushed_registers"):
        return None
    keep = set(lay.target.abi.callee_saved) | ({arch.link_reg} if getattr(arch, "link_reg", None) else set())
    has_slot = hasattr(arch, "slot_access")     # (sp-relative ones: the run reads sp itself)
    m = make()
    m.plan, m.replay, m.function = [], False, name
    m.kept, m.kept_why = kept, why or {}
    saved, found = {}, []                   # byte address -> the register saved there
    low = [None]                            # sp before the last instruction

    def own(insn, register):
        if arch.stack_kind(insn) in ("push", "pop"):
            return True
        slot = arch.slot_access(insn) if has_slot else None
        return slot is not None and slot[2] == "sp" and slot[1] == register

    def on_insn(uc, address, size, user):
        sp = uc.reg_read(lay.sp)
        if low[0] is not None and sp > low[0] and saved:
            # sp rose (a pop, an epilogue's add): the frames below it are gone, and their saved slots
            # with them. A later frame lies there again, and its locals are no saves: FreeRTOS's
            # prvQueueAccessLogError pushed r7 at a slot xQueueIsQueueFullFromISR's local took next,
            # and every store to that local counted as a write of a saved r7 (a difference such a
            # case showed would have been refused, not compared)
            for a in [a for a in saved if a < sp]:
                del saved[a]
        low[0] = sp
        insn = prog.insn(address & ~1)
        if insn is None:
            return
        regs = arch.pushed_registers(insn)
        if regs:
            base = sp - arch.stack_bytes(insn)
            for k, r in enumerate(regs):
                if r in keep:
                    for b in range(4):
                        saved[base + 4 * k + b] = r
            return
        slot = arch.slot_access(insn) if has_slot else None
        if slot and slot[0] == "store" and slot[2] == "sp" and slot[1] in keep:
            for b in range(4):
                saved[sp + slot[3] + b] = slot[1]

    def touched(uc, access, address, size, value, user):
        if found or not saved:
            return
        regs = {saved[a] for a in range(address, address + size) if a in saved}
        if not regs:
            return
        pc = uc.reg_read(lay.pc) & ~1
        insn = prog.insn(pc)
        if insn is not None and all(own(insn, r) for r in regs):
            return                          # (first: a push stores before it moves sp)
        sp = uc.reg_read(lay.sp)
        hit = set()
        for a in range(address, address + size):
            r = saved.get(a)
            if r is None:
                continue
            if a < sp:                      # its frame is gone: the stack there is free again
                del saved[a]
            else:
                hit.add(r)
        if not hit:
            return
        owner = prog.function_at(pc)
        found.append("%s of the saved %s at 0x%x by %s" % ("write" if access == UC_MEM_WRITE else "read",
                                                         "/".join(sorted(hit)), address,
                                                         owner.name if owner else "0x%x" % pc))
        if timeline is not None:
            timeline.mark(m)
    try:
        setup(m)
        if timeline is not None:
            timeline.attach(m)
        m.hook_add(UC_HOOK_CODE, on_insn)
        lo = lay.stack_top - lay.stack_size
        m.hook_add(UC_HOOK_MEM_READ, touched, begin=lo, end=lay.stack_top - 1)
        m.hook_add(UC_HOOK_MEM_WRITE, touched, begin=lo, end=lay.stack_top - 1)
        m.call(addr, *args, max_instructions=limit)
    except Exception:
        pass
    return found[0] if found else None
