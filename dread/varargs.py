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
        except Exception:
            value = None
        n = arity(index, image_format(program, value), len(arg_regs)) if value is not None else None
        if n is not None:
            out[address] = (n, value)
    return out
