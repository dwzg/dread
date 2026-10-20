"""Indirect jump targets, found by running the code in front of the jump.

A switch compiled into a table ends in an indirect jump, and its targets lie in a table of one of
many shapes: addresses, byte or halfword offsets, a run of branches; ARM alone has tbb/tbh,
ldr pc, [pc, rN, lsl #2], add pc, pc, rN, lsl #2 after a byte load, ldr rN, [table, rM]; mov pc, rN.
A call of a switch helper that returns behind its call site (GCC's Thumb-1 __gnu_thumb1_case_*, the
table right after the bl; the arch layer marks such calls) is such a jump too: the run goes through
the helper and lands where it returns. Instead of recognising each shape, this runs the dispatch: the straight run of instructions in
front of the jump (back to the previous call, unconditional branch or return, at most RUN_BACK
instructions) is executed in a bare emulator with the image mapped, once for every value of the
index the bound check in that run lets through (arch.bound_check: cmp rN, #K or and rN, rM, #2^n-1),
with the index register set to that value at the check. Where pc goes after the jump is a target.
A value for which the run faults, takes the bound check's way out or leaves the run elsewhere gives
no target. Registers other than the index start at zero, and memory outside the image reads as zero
(pages are mapped on demand and zeroed again after each run; never unmapped: Unicorn keeps stale
references to an unmapped page and a later run that writes there crashes the process); the image is
read-only, so a run that writes into it gives no target. The dispatch is expected to depend only on
the index and on constant memory, which is what compiled switches do.

A jump without a bound check (a veneer ldr ip, =f; bx ip, a far jump ldr pc, =f) is run twice with
different register contents; when both land on the same address, that is its one target.

resolve() returns (inside, outside): the targets in the function (a switch's cases) and those
elsewhere (tail calls, through a table or a constant), or None when nothing reaches the jump; the
caller then falls back to decoding a table it recognises (arch.switch_targets) or to coarser rules
(dread.regflow).
"""
from unicorn import (UC_HOOK_CODE, UC_HOOK_MEM_FETCH_UNMAPPED, UC_HOOK_MEM_READ_UNMAPPED, UC_HOOK_MEM_WRITE,
                     UC_HOOK_MEM_WRITE_UNMAPPED, UC_PROT_EXEC, UC_PROT_READ, UcError)

RUN_BACK = 16           # instructions in front of the jump searched for the bound check
MAX_CASES = 1024        # index values tried at most
STEPS = 64              # instructions run per value at most
PAGE = 0x1000
MAX_PAGES = 64          # zero pages mapped on demand per run at most

_engines = {}           # (id(program), isa) -> (engine, state)


def _engine(program, isa):
    """A Unicorn engine with the image and the startup's copies mapped, cached per program and isa."""
    key = (id(program), isa)
    if key in _engines and _engines[key][2] is program:
        return _engines[key][:2]
    arch = program.arch
    uc = arch.new_uc(isa)
    spans = [(program.base, program.base + len(program.data))]
    spans += [(c.run, c.run + c.size) for c in program.copies]
    pages = sorted({(lo & ~(PAGE - 1), (hi + PAGE - 1) & ~(PAGE - 1)) for lo, hi in spans})
    merged = []
    for lo, hi in pages:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    for lo, hi in merged:
        uc.mem_map(lo, hi - lo)
    uc.mem_write(program.base, bytes(program.data))
    for c in program.copies:
        lo = c.stored - program.base
        uc.mem_write(c.run, bytes(program.data[lo:lo + c.size]))
    for lo, hi in merged:
        uc.mem_protect(lo, hi - lo, UC_PROT_READ | UC_PROT_EXEC)
    state = {"mapped": set(), "new": 0, "dirty": set()}
    uc.hook_add(UC_HOOK_CODE, _on_code, user_data=state)
    uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED | UC_HOOK_MEM_WRITE_UNMAPPED, _on_unmapped, user_data=state)
    uc.hook_add(UC_HOOK_MEM_WRITE, _on_write, user_data=state)
    uc.hook_add(UC_HOOK_MEM_FETCH_UNMAPPED, lambda *a: False)
    _engines[key] = (uc, state, program)
    return uc, state


def _on_unmapped(uc, access, address, size, value, state):
    """A zero page where the dispatch reads or writes outside the image (RAM, peripherals); at most
    MAX_PAGES new ones per run. Recorded here, and the run faults: _run maps them between runs and
    runs again from the start, which comes to the same (the runs are short and start alike). Mapped
    here, inside the access, Unicorn's memory map changed under a store that it then finished: the
    suite crashed once in that store's lookup (2026-10-20)."""
    if state["new"] >= MAX_PAGES:
        return False
    state["want"] |= {address & ~(PAGE - 1), (address + size - 1) & ~(PAGE - 1)} - state["mapped"]
    return False


def _on_write(uc, access, address, size, value, state):
    for page in {address & ~(PAGE - 1), (address + size - 1) & ~(PAGE - 1)}:
        if page in state["mapped"]:
            state["dirty"].add(page)


def _on_code(uc, address, size, state):
    if address == state["set_at"]:
        uc.reg_write(state["reg"], state["value"])
    through = state.get("through")
    if through is not None and through[0] <= address < through[1]:
        state["called"] = True              # inside the switch helper the jump called
        return
    if state.get("called") or not state["lo"] <= address <= state["jump"]:
        state["landed"] = address
        uc.emu_stop()
        return
    state["last"] = address


def targets(program, fn, address):
    """Sorted switch targets (inside fn) of the indirect jump at `address`, or None."""
    found = resolve(program, fn, address)
    return found[0] or None if found else None


FILLS = (0, 0x01010101)     # register contents of the two runs of a jump without a bound check


def _helper(program, address):
    """(start, end) of the switch helper the jump at `address` calls (a bl the arch marked as a
    dispatch: GCC's __gnu_thumb1_case_*), which the run goes through; None otherwise."""
    insn = program.insn(address)
    if insn is None or not getattr(insn, "dispatch_call", False):
        return None
    callee = program.function_at(program.arch.target(insn) & ~1)
    return (callee.entry, program.end(callee)) if callee is not None else None


def _run(program, uc, state, isa, runs, first, address):
    """Run from `first` once per entry of `runs`; where each run lands after the jump (None: it
    faulted or left the run before the jump)."""
    arch = program.arch
    landings = []
    try:
        for fill, set_at, reg, value in runs:
            state["new"] = 0
            while True:
                for r in arch.general_regs():
                    uc.reg_write(arch.reg_id(r), fill)
                arch.initial_status(uc)
                state.update(set_at=set_at, reg=reg, value=value, lo=first, jump=address, landed=None, last=None,
                             called=False, through=_helper(program, address), want=set())
                try:
                    uc.emu_start(arch.start_address(first, isa), 0xFFFFFFFF, count=STEPS)
                    landing = state["landed"] if state["last"] == address else None
                except UcError:
                    landing = None
                for page in state["dirty"]:     # zero again what the run wrote, for the next one
                    uc.mem_write(page, bytes(PAGE))
                state["dirty"] = set()
                want = sorted(state["want"]) if landing is None else []
                try:
                    for page in want:           # it ran into memory it wants: mapped, and run again
                        uc.mem_map(page, PAGE)
                        state["mapped"].add(page)
                        state["new"] += 1
                except UcError:
                    want = []
                if not want:
                    landings.append(landing)
                    break
    finally:
        for page in state["dirty"]:
            uc.mem_write(page, bytes(PAGE))
        state["dirty"] = set()
    return landings


def resolve(program, fn, address):
    """(inside, outside) targets of the indirect jump at `address` (see the module), or None."""
    arch = program.arch
    if address not in fn.insns:
        return None
    i = fn.insns.index(address)
    start, check = i, None
    while start > 0 and i - start < RUN_BACK:
        prev = fn.insns[start - 1]
        insn = program.insn(prev)
        if insn is None or prev + insn.size != fn.insns[start]:
            break
        kind, _, falls = arch.flow(insn)
        if kind not in ("next", "jump") or (kind == "jump" and not falls):
            break
        start -= 1
    for k in range(i - 1, start - 1, -1):
        insn = program.insn(fn.insns[k])
        found = arch.bound_check(insn) if insn is not None else None
        if found:
            check = (k, found)
            break
    isa = program.isa_at(address)
    uc, state = _engine(program, isa)
    if check is not None:
        k, (reg, limit, before) = check
        set_at = fn.insns[k] if before else (fn.insns[k + 1] if k + 1 <= i else None)
        if set_at is None:
            return None
        runs = [(0, set_at, arch.reg_id(reg), value) for value in range(0, min(limit, MAX_CASES) + 2)]
    else:
        runs = [(fill, None, None, None) for fill in FILLS]
    # From the start of the run first (a table base may be loaded in front of the check); when
    # nothing reaches the jump from there (a branch in front of the check leaves the run with the
    # registers at zero), from the check itself.
    starts = [start] + ([check[0]] if check is not None and check[0] != start else [])
    for first in starts:
        landings = _run(program, uc, state, isa, runs, fn.insns[first], address)
        if any(a is not None for a in landings):
            break
    if check is not None and not any(a is not None for a in landings):
        # With every register 0 a store through one went into the image where it starts at 0 (FreeRTOS's
        # MPS2 builds: xTaskGenericNotify's strb r1, [r0, #0x54] in front of its tbb), which the
        # run may not write: again with the registers filled, so that such an access lands in a
        # page mapped for it.
        runs = [(FILLS[-1], set_at, reg_id, value) for _, set_at, reg_id, value in runs]
        here = set(fn.insns)
        for first in starts:
            landings = _run(program, uc, state, isa, runs, fn.insns[first], address)
            # (where the fill itself is the target, the jump went nowhere: only code counts)
            landings = [a if a is not None and (a in here or a in program.by_entry) else None for a in landings]
            if any(a is not None for a in landings):
                break
    if check is None:
        if None in landings or len(set(landings)) != 1:
            return None
    found = {a for a in landings if a is not None}
    if not found:
        return None
    inside = set(fn.insns)
    return sorted(a for a in found if a in inside), sorted(a for a in found if a not in inside)


def mapping(program, fn, address):
    """For a switch with a bound check: (address where the index is taken, the register it is in,
    {index value: target in fn} for the values that reach the jump, the largest value the check lets
    through); None otherwise. What dread.lift needs to switch over the index instead of reading the
    table at run time."""
    arch = program.arch
    if address not in fn.insns:
        return None
    i = fn.insns.index(address)
    start = i
    while start > 0 and i - start < RUN_BACK:
        prev = fn.insns[start - 1]
        insn = program.insn(prev)
        if insn is None or prev + insn.size != fn.insns[start]:
            break
        kind, _, falls = arch.flow(insn)
        if kind not in ("next", "jump") or (kind == "jump" and not falls):
            break
        start -= 1
    check = None
    for k in range(i - 1, start - 1, -1):
        insn = program.insn(fn.insns[k])
        found = arch.bound_check(insn) if insn is not None else None
        if found:
            check = (k, found)
            break
    if check is None:
        return None
    k, (reg, limit, before) = check
    set_at = fn.insns[k] if before else (fn.insns[k + 1] if k + 1 <= i else None)
    if set_at is None:
        return None
    isa = program.isa_at(address)
    uc, state = _engine(program, isa)
    runs = [(0, set_at, arch.reg_id(reg), value) for value in range(0, min(limit, MAX_CASES) + 2)]
    inside, values = set(fn.insns), {}
    for fill in FILLS[:1] + FILLS[-1:]:     # (registers filled when zero ones store into the image: resolve)
        runs = [(fill, set_at, reg_id, value) for _, set_at, reg_id, value in runs]
        for first in [fn.insns[start]] + ([fn.insns[k]] if k != start else []):
            landings = _run(program, uc, state, isa, runs, first, address)
            values = {run[3]: at for run, at in zip(runs, landings) if at is not None and at in inside}
            if values:
                break
        if values:
            break
    return (set_at, reg, values, limit) if values else None
