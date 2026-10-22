"""Constant register values at an instruction, found by running the code in front of it.

The straight run of instructions in front of an address (back to the previous call, return or
unconditional branch, at most RUN_BACK instructions, within the function, and never past a join: an
instruction a jump goes to, where the values may come from another path) is executed twice in a bare
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
    state = {"mapped": set(), "new": 0, "fill": 0, "want": set()}

    def on_unmapped(uc, access, address, size, value, st):
        """Pages of the fill byte where a run reads or writes outside the image, at most MAX_PAGES
        a run: recorded, and the run faults; _run maps them and runs again from the start (mapped
        inside the access, Unicorn's memory map changed under it: see dread.switches._on_unmapped)."""
        if st["new"] >= MAX_PAGES:
            return False
        st["want"] |= {address & ~(PAGE - 1), (address + size - 1) & ~(PAGE - 1)} - st["mapped"]
        return False

    uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED | UC_HOOK_MEM_WRITE_UNMAPPED, on_unmapped, user_data=state)
    uc.hook_add(UC_HOOK_MEM_FETCH_UNMAPPED, lambda *a: False)
    _engines[key] = (uc, state, program)
    return uc, state


_joins = {}


def joins(program, fn):
    """The instructions of fn that a jump goes to: what ran in front of one need not have run."""
    key = (id(program), fn.entry)
    got = _joins.get(key)
    if got is not None and got[0] is program:
        return got[1]
    inside, out = set(fn.insns), set()
    for insn in program.instructions(fn):
        if insn is None:
            continue
        kind, target, _ = program.arch.flow(insn)
        if kind == "jump" and isinstance(target, int) and (target & ~1) in inside:
            out.add(target & ~1)
    _joins[key] = (program, out)
    return out


def stretch(program, fn, address):
    """The first instruction of the straight run in fn that ends in front of `address`; it does
    not reach back past a join (`address` itself when that is one: then nothing is known)."""
    arch = program.arch
    targets = joins(program, fn)
    i = fn.insns.index(address)
    start = i
    while start > 0 and i - start < RUN_BACK and fn.insns[start] not in targets:
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
    if address not in fn.insns or not hasattr(arch, "new_uc"):   # (an architecture without an engine yet)
        return {}
    first = stretch(program, fn, address)
    if first == address:
        return {}
    starts, i = [first], fn.insns.index(first)
    while fn.insns[i] != address:
        insn = program.insn(fn.insns[i])
        if insn is not None:
            kind, _, falls = arch.flow(insn)
            if kind == "jump" and falls:    # a conditional branch (ARM: predicated; RISC-V: beq and kin)
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
        while True:
            for page in state["mapped"]:
                uc.mem_write(page, bytes([fill]) * PAGE)
            word = int.from_bytes(bytes([fill]) * 4, "little")
            for r in arch.general_regs():
                uc.reg_write(arch.reg_id(r), word)
            arch.initial_status(uc)             # (before sp: the A profile banks sp per mode)
            for r, value in program.image.fixed.items():    # gp and kin keep their one value
                uc.reg_write(arch.reg_id(r), value)
            uc.reg_write(arch.reg_id("sp"), word & ~0xFFF)     # a stack address that differs between the runs
            if arch.link_reg:
                uc.reg_write(arch.reg_id(arch.link_reg), arch.start_address(word & ~3, isa))
            state["want"] = set()
            try:
                uc.emu_start(arch.start_address(first, isa), address, count=RUN_BACK * 2)
                break
            except UcError:
                want = sorted(state["want"])
            try:
                for page in want:               # memory it wants: mapped (filled above), and run again
                    uc.mem_map(page, PAGE)
                    state["mapped"].add(page)
                    state["new"] += 1
            except UcError:
                want = []
            if not want:
                return None
        if uc.reg_read(arch.pc_reg()) & ~1 != address:
            return None
        results.append({r: uc.reg_read(arch.reg_id(r)) for r in names})
    a, b = results
    return {r: a[r] for r in names if a[r] == b[r]}


TEXT_SCAN = 256         # characters string_at looks at for a text's end


def string_at(program, address, limit=48):
    """The printable NUL-terminated text at an address in the image, or None. A text longer than
    limit shows its first third and its end ("/home/user/sr...rtos/Source/tasks.c"): a path has its
    file name at the end (an assertion's __FILE__ names the source file); one without an end within
    TEXT_SCAN characters shows its start."""
    if not program.in_image(address):
        return None
    out = bytearray()
    a = address
    while program.in_image(a) and len(out) < TEXT_SCAN:
        b = program.read(a, 1)[0]
        if b == 0:
            break
        if not (0x20 <= b < 0x7F or b in (9, 10, 13)):
            if len(out) >= limit:           # (a long run of text ends in other bytes: its start)
                break
            return None
        out.append(b)
        a += 1
    if len(out) < 2:
        return None

    def esc(part):
        return bytes(part).decode("ascii").encode("unicode_escape").decode("ascii")
    if len(out) <= limit:
        return esc(out)
    if not program.in_image(a) or len(out) >= TEXT_SCAN or program.read(a, 1)[0] != 0:
        return esc(out[:limit]) + "..."
    head = limit // 3
    return esc(out[:head]) + "..." + esc(out[len(out) - (limit - head):])


POINTED_WORDS = 4       # words of a table a constant points to that pointed_note shows at most
SMALLEST_POINTER = 0x100    # a smaller constant is a number, even where the image begins at 0


def pointed_note(program, address):
    """What a reader wants to know about a constant: the text it points to ("CARD FULL"), the
    function whose entry it is (&name), or, when it points to a table of words (aligned, in the
    image), the texts and functions its first words point to: a localized message's table of
    pointers, one a language, or a menu's pairs of a handler and a label (-> {name, "Label", ...}).
    None when it is none of these."""
    if address < SMALLEST_POINTER:
        return None
    text = string_at(program, address)
    if text:
        return '"%s"' % text
    fn = program.by_entry.get(address & ~1) if hasattr(program, "by_entry") else None
    if fn is not None and (address & ~1) == fn.entry:
        return "&" + fn.name
    if address % 4 or not program.in_image(address, 4):
        return None
    parts, useful = [], False
    for k in range(POINTED_WORDS):
        a = address + 4 * k
        if not program.in_image(a, 4):
            break
        w = program.word(a)
        text = string_at(program, w)
        callee = program.by_entry.get(w & ~1)
        if text:
            parts.append('"%s"' % text)
            useful = True
        elif callee is not None and (w & ~1) == callee.entry:
            parts.append(callee.name)
            useful = True
        else:
            parts.append("0x%x" % w)
    return "-> {%s, ...}" % ", ".join(parts) if useful else None
