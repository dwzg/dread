"""Inputs whose outcome belongs to the original's frames: where its stack lies, or a saved register
an input made code touch. C lays out its frames otherwise and cannot be held to either, so dread test
refuses a case with one of them when its images differ (dread.runner), dread callconv measures
without such cases, and the case generator does not let them stand for the instructions they reach
when an input without them reaches those too (dread.autocase.Generator.fragile: a case the lift
check refuses covers nothing).

Both checks run the original alone: make() returns a fresh machine with the original image,
setup(m) gives it a case's inputs.
"""

from dread.emu import ARGS_ROOM, Result

STACK_SHIFT = 0x40      # bytes the stack moves for the check that a case's outcome depends on it


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


def saves_touched(make, program, layout, setup, addr, args, limit, name=None, kept=(), why=None):
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
    whose images differ."""
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
    try:
        setup(m)
        m.hook_add(UC_HOOK_CODE, on_insn)
        lo = lay.stack_top - lay.stack_size
        m.hook_add(UC_HOOK_MEM_READ, touched, begin=lo, end=lay.stack_top - 1)
        m.hook_add(UC_HOOK_MEM_WRITE, touched, begin=lo, end=lay.stack_top - 1)
        m.call(addr, *args, max_instructions=limit)
    except Exception:
        pass
    return found[0] if found else None
