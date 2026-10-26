"""Calls of printf-family functions: how many argument registers each one passes, from its constant format.

A variadic function reads its unnamed arguments through a va_list, as many as its format says.
dread.regflow counts what it keeps of them (a push, a spill into its save area) as no use, so the
function reads no more than its named arguments, and a register its caller hands on to it untouched is
read by nobody. FreeRTOS's vAssertCalled(file, line) calls printf("ASSERT! Line %d, file %s\\r\\n", line,
file) with the line still in the register it came in: regflow said vAssertCalled read the file alone.
Readable C kept every argument of every call of such a wrapper (readable.variadic_reach), so each
caller handed its own entry registers on as phantom parameters (a2-a7 on RISC-V); where the wrapper was
not known for one (newlib's printf pushes its arguments, which variadic_reach did not look for), its
calls lost the line.

Where the format is a constant text in the image whose conversions take one word each, the call reads
the format, the arguments in front of it and one register for each conversion (call_arities):
dread.regflow counts those as the call's uses, so vAssertCalled reads the file and the line and its
callers pass both; readable C trims each such call of printf to them, printf declared variadic; a stub
of a printf-family function logs the registers its format takes (dread.autocase: format_regs).

Which functions take a format: the printf family by name (FAMILY) and the target's [abi] formats
(a name and the index of its format argument: a firmware's own, which a stripped image does not name).
Only formats whose arguments are one word each (consumed: a 64-bit or floating-point argument, a
positional one, %n and a conversion it does not know give None, and the call stays as it was), only a
format the code puts into its register on the straight run in front of the call (dread.constants), and
only calls whose words all go in registers.
"""
import re

# printf-family functions by name: the index of their format argument
FAMILY = {
    "printf": 0, "iprintf": 0, "printk": 0,
    "sprintf": 1, "siprintf": 1, "fprintf": 1, "fiprintf": 1, "dprintf": 1, "asprintf": 1,
    "snprintf": 2, "sniprintf": 2,
    "_printf_r": 1, "_iprintf_r": 1,
    "_sprintf_r": 2, "_siprintf_r": 2, "_fprintf_r": 2, "_fiprintf_r": 2, "_asprintf_r": 2,
    "_snprintf_r": 3, "_sniprintf_r": 3,
}
FORMAT_MAX = 512        # bytes of a format read at most (one without its end within them: not known)

_CONVERSION = re.compile(r"%(?P<flags>[-+ #0']*)(?P<width>\*|\d+)?(?:\.(?P<prec>\*|\d*))?"
                         r"(?P<length>hh|h|ll|l|j|z|t|L|q)?(?P<conv>.)", re.S)
_ONE_WORD = frozenset("diouxXcsp")
_TWO_WORDS = ("ll", "j", "q", "L")


def family(target):
    """{name: index of its format argument}: FAMILY and the target's [abi] formats (which win)."""
    abi = getattr(target, "abi", None)
    return dict(FAMILY, **(getattr(abi, "formats", None) or {}))


def consumed(fmt):
    """How many words of arguments a printf format reads after it, or None where that is not plain: one
    for each conversion of an int, a pointer, a character or a text, one more for each * width or
    precision, none for %%. A 64-bit argument (ll, j, q, L), a floating-point one (both two words, which
    may go to an even register), a positional one (%1$d), %n and a conversion not known give None, and
    so does anything that is not bytes."""
    if not isinstance(fmt, (bytes, bytearray)):
        return None
    text = bytes(fmt).decode("latin-1")
    n, i = 0, 0
    while True:
        k = text.find("%", i)
        if k < 0:
            return n
        if text.startswith("%%", k):
            i = k + 2
            continue
        m = _CONVERSION.match(text, k)
        if m is None or m.group("length") in _TWO_WORDS or m.group("conv") not in _ONE_WORD:
            return None
        n += 1 + (m.group("width") == "*") + (m.group("prec") == "*")
        i = m.end()


def text_at(read, address, limit=FORMAT_MAX):
    """The bytes from address up to the first NUL (without it), read through read(address, n) -> at most n
    bytes (fewer where a region ends; it may raise); None where a read fails or comes back empty first,
    or no NUL comes within limit bytes."""
    if not isinstance(address, int):
        return None
    out = bytearray()
    while len(out) < limit:
        try:
            chunk = bytes(read(address + len(out), min(16, limit - len(out))))
        except Exception:
            return None
        if not chunk:
            return None
        end = chunk.find(b"\0")
        if end >= 0:
            return bytes(out + chunk[:end])
        out += chunk
    return None


def image_format(program, address):
    """The format text at an address in the program's image (text_at), None outside it."""
    def read(a, n):
        while n > 1 and not program.in_image(a, n):
            n -= 1
        return program.read(a, n) if program.in_image(a, n) else b""
    return text_at(read, address)


def clipped(read, spans):
    """read(address, n) that comes back empty at an address inside one of spans ([(lo, hi)]) and stops
    short of the next one: what a reader of a format may not read (dread.autocase: code, which differs
    between two images, and a device's registers)."""
    def out(a, n):
        for lo, hi in spans:
            if lo <= a < hi:
                return b""
            if a < lo < a + n:
                n = lo - a
        return read(a, n)
    return out


def arity(index, fmt, nregs):
    """How many argument registers a call passes whose format argument (at index) holds fmt: the format,
    the arguments in front of it and one for each word it consumes; None where that is not known or
    the words do not all go in the nregs registers."""
    k = consumed(fmt)
    if k is None or index + 1 + k > nregs:
        return None
    return index + 1 + k


def sites(program, names):
    """[(function, address, callee, "call" or "tail")] of the direct calls of the named functions, and of
    the branches to their entries from other functions (tail calls), in each function's order."""
    entries = {fn.entry: fn.name for fn in program.functions if fn.name in names}
    out = []
    if not entries:
        return out
    for fn in program.functions:
        for a in fn.insns:
            insn = program.insn(a)
            if insn is None:
                continue
            kind, target, _ = program.arch.flow(insn)
            if target is None or kind not in ("call", "jump"):
                continue
            callee = entries.get(target & ~1)
            if callee is None or (kind == "jump" and target & ~1 == fn.entry):
                continue
            out.append((fn, a, callee, "call" if kind == "call" else "tail"))
    return out


def _graph(program, fn):
    """({address: index}, [successors]) of the function's instructions: the next one where an instruction
    goes on, a jump's target inside the function; None where an indirect jump (a switch) makes the
    paths unknown."""
    arch = program.arch
    index = {a: i for i, a in enumerate(fn.insns)}
    succ = []
    for i, a in enumerate(fn.insns):
        insn = program.insn(a)
        out = []
        if insn is not None:
            kind, target, falls = arch.flow(insn)
            if kind == "ijump":
                return None
            nxt = fn.insns[i + 1] if i + 1 < len(fn.insns) else None
            if (kind in ("next", "call", "icall") or falls) and nxt == a + insn.size:
                out.append(i + 1)
            if kind == "jump" and target is not None and (target & ~1) in index:
                out.append(index[target & ~1])
        succ.append(out)
    return index, succ


ENTRY = -1      # (the caller's value of a register, as a definition)


GIVEN_DEPTH = 3     # definitions through definitions given_at follows at most

def given_at(program, fn, start, end, depth=GIVEN_DEPTH):
    """{register: value} of the registers the straight run from start up to end (dread.constants stretch)
    reads before it writes them that hold one constant at start: the one definition of theirs that
    reaches start, and the caller's value does not (a call ends the scratch registers, not the
    callee-saved ones; a write under a condition may not happen), its value a constant (dread.constants
    at, behind it). RISC-V GCC -Os computes a format into a callee-saved register in front of a loop and
    copies it into the call's register at each pass (2026-10-26: virt's tasks' sprintf). A definition
    that computes from a register the same way (s2 = s2 + 1020 behind lui s2 and two calls) gets its own
    run's given registers, up to depth definitions deep. {} where nothing is known, or the function
    jumps through a register (a switch: paths not known)."""
    from dread import constants
    arch = program.arch
    graph = _graph(program, fn)
    if graph is None or start not in graph[0] or end not in graph[0] or fn.entry not in graph[0]:
        return {}
    index, succ = graph
    wanted, written = [], set()
    for a in fn.insns[index[start]:index[end]]:
        insn = program.insn(a)
        if insn is None:
            return {}
        reads, writes = arch.reg_effects(insn)
        wanted += [r for r in reads if r not in written and r not in wanted]
        written |= set(writes)
    wanted = [r for r in wanted if r not in program.image.fixed and r != "sp"]
    if not wanted:
        return {}
    general = set(arch.general_regs())
    saved = set(getattr(program.target.abi, "callee_saved", ()))
    effects = []                            # per instruction: (registers it writes, under a condition)
    for a in fn.insns:
        insn = program.insn(a)
        if insn is None:
            effects.append((set(), False))
            continue
        writes = set(arch.reg_effects(insn)[1])
        if arch.flow(insn)[0] in ("call", "icall"):
            writes |= general - saved      # (a call may change every scratch register)
        effects.append((writes, arch.predicate(insn) is not None))
    preds = [[] for _ in fn.insns]
    for k, nexts in enumerate(succ):
        for j in nexts:
            preds[j].append(k)
    first = index[fn.entry]
    out = {}
    for reg in wanted:
        reach_out = [set() for _ in fn.insns]
        changed = True
        while changed:
            changed = False
            for k in range(len(fn.insns)):
                came = set().union(*(reach_out[p] for p in preds[k])) if preds[k] else set()
                if k == first:
                    came.add(ENTRY)
                writes, conditional = effects[k]
                o = ({k} | came) if (reg in writes and conditional) else ({k} if reg in writes else came)
                if o != reach_out[k]:
                    reach_out[k], changed = o, True
        k0 = index[start]
        came = set().union(*(reach_out[p] for p in preds[k0])) if preds[k0] else set()
        if k0 == first:
            came.add(ENTRY)
        if len(came) != 1 or ENTRY in came:
            continue
        (k,) = came
        try:
            value = constants.after(program, fn, fn.insns[k], regs=[reg]).get(reg)
            if value is None and depth > 1:     # (computed from registers given the same way)
                upto = fn.insns[k + 1] if k + 1 < len(fn.insns) else None     # (the definition's own reads too)
                inner = given_at(program, fn, constants.stretch(program, fn, fn.insns[k]), upto, depth - 1) \
                    if upto is not None else {}
                if inner:
                    value = constants.after(program, fn, fn.insns[k], regs=[reg], given=inner).get(reg)
        except Exception:
            value = None
        if value is not None:
            out[reg] = value
    return out


def call_arities(program, formats, arg_regs, found=None):
    """{address: (how many argument registers the call or tail call there passes, the format's address)}
    of the calls of the functions that take a format (formats: {name: index of the format argument},
    family()) whose format argument holds a constant on the straight run in front of the call
    (dread.constants at) that points to a format in the image whose words all go in registers (arity).
    found: sites() of a set of functions that holds those (made here without). {} where there is no
    engine for the architecture."""
    from dread import constants
    out = {}
    for fn, address, callee, _ in (sites(program, formats) if found is None else found):
        if callee not in formats:
            continue
        index = formats[callee]
        if not 0 <= index < len(arg_regs):
            continue
        reg = arg_regs[index]
        try:
            value = constants.at(program, fn, address, regs=[reg]).get(reg)
            if value is None:           # (a register written once with a constant on the way in: given_at)
                given = given_at(program, fn, constants.stretch(program, fn, address), address)
                if given:
                    value = constants.at(program, fn, address, regs=[reg], given=given).get(reg)
        except Exception:
            value = None
        n = arity(index, image_format(program, value), len(arg_regs)) if value is not None else None
        if n is not None:
            out[address] = (n, value)
    return out
