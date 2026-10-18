"""Constant register values at an instruction, found by running the code in front of it.

The straight run of instructions in front of an address (back to the previous call, return or
unconditional branch, at most RUN_BACK instructions, within the function) is executed twice in a bare
emulator with the image mapped (read-only): once with every register and every page outside the
image filled with one pattern, once with another. A register that holds the same value after both
runs got it from the code and the image alone: a constant (a literal, an address the code computes,
a value from one of the image's tables). A value loaded from RAM differs between the runs and is not
one. A run that faults or leaves the stretch before reaching the address gives nothing.

Pages outside the image are mapped on demand and filled again before every run, never unmapped
(Unicorn keeps stale references to unmapped pages; dread.switches learned that the hard way).

Used by dread callsites (the arguments at a call) and dread flat.
"""
from unicorn import (UC_HOOK_MEM_FETCH_UNMAPPED, UC_HOOK_MEM_READ_UNMAPPED, UC_HOOK_MEM_WRITE,
                     UC_HOOK_MEM_WRITE_UNMAPPED, UC_PROT_EXEC, UC_PROT_READ, UcError)

RUN_BACK = 24           # instructions in front of the address that are run at most
PAGE = 0x1000
FILLS = (0x5A, 0xA5)    # the two runs' fill bytes (registers get the byte four times)
MAX_PAGES = 64

_engines = {}


def _engine(program, isa):
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
    state = {"mapped": set(), "new": 0, "fill": 0}

    def on_unmapped(uc, access, address, size, value, st):
        if st["new"] >= MAX_PAGES:
            return False
        for page in {address & ~(PAGE - 1), (address + size - 1) & ~(PAGE - 1)}:
            if page in st["mapped"]:
                continue
            try:
                uc.mem_map(page, PAGE)
                uc.mem_write(page, bytes([st["fill"]]) * PAGE)
            except UcError:
                return False
            st["mapped"].add(page)
            st["new"] += 1
        return True

    uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED | UC_HOOK_MEM_WRITE_UNMAPPED, on_unmapped, user_data=state)
    uc.hook_add(UC_HOOK_MEM_FETCH_UNMAPPED, lambda *a: False)
    _engines[key] = (uc, state, program)
    return uc, state


def stretch(program, fn, address):
    """The first instruction of the straight run in fn that ends in front of `address`."""
    arch = program.arch
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
    return fn.insns[start]


def at(program, fn, address, regs=None):
    """{register name: value} of the registers (default: the general ones) that hold the same value
    in front of `address` whatever the registers and RAM held before the stretch. When the runs do not
    reach the address (a conditional branch in the stretch goes elsewhere under the fill values), they
    start again behind each conditional branch of the stretch, from the earliest on."""
    arch = program.arch
    if address not in fn.insns:
        return {}
    first = stretch(program, fn, address)
    if first == address:
        return {}
    starts, i = [first], fn.insns.index(first)
    while fn.insns[i] != address:
        insn = program.insn(fn.insns[i])
        if insn is not None and arch.flow(insn)[0] == "jump" and arch.predicate(insn) is not None:
            starts.append(fn.insns[i + 1])
        i += 1
    names = list(regs or arch.general_regs())
    for start in starts:
        if start == address:
            break
        got = _run(program, start, address, names)
        if got is not None:
            return got
    return {}


def _run(program, first, address, names):
    arch = program.arch
    isa = program.isa_at(address)
    uc, state = _engine(program, isa)
    results = []
    for fill in FILLS:
        state["fill"], state["new"] = fill, 0
        for page in state["mapped"]:
            uc.mem_write(page, bytes([fill]) * PAGE)
        word = int.from_bytes(bytes([fill]) * 4, "little")
        for r in arch.general_regs():
            uc.reg_write(arch.reg_id(r), word)
        arch.initial_status(uc)             # (before sp: the A profile banks sp per mode)
        uc.reg_write(arch.reg_id("sp"), word & ~0xFFF)     # a stack address that differs between the runs
        uc.reg_write(arch.reg_id("lr"), word | 1)
        try:
            uc.emu_start(arch.start_address(first, isa), address, count=RUN_BACK * 2)
        except UcError:
            return None
        if uc.reg_read(arch.pc_reg()) & ~1 != address:
            return None
        results.append({r: uc.reg_read(arch.reg_id(r)) for r in names})
    a, b = results
    return {r: a[r] for r in names if a[r] == b[r]}


def string_at(program, address, limit=48):
    """The printable NUL-terminated text at an address in the image, or None."""
    if not program.in_image(address):
        return None
    out = bytearray()
    a = address
    while program.in_image(a) and len(out) < limit:
        b = program.read(a, 1)[0]
        if b == 0:
            break
        if not (0x20 <= b < 0x7F or b in (9, 10, 13)):
            return None
        out.append(b)
        a += 1
    if len(out) < 2:
        return None
    return out.decode("ascii").encode("unicode_escape").decode("ascii") + ("..." if len(out) >= limit else "")
