"""dread.divisions: the runtime routines that divide, written as / and % in readable C.

A compiler for a processor without a divide instruction (ARMv4 to ARMv6, Cortex-M0, RISC-V without its
M extension) calls a routine of its runtime library for x / y and x % y: the RTABI's __aeabi_uidivmod
hands back the quotient in r0 and the remainder in r1, libgcc's __udivsi3 the quotient alone, an older
compiler's library may take the divisor first. Readable C printed the call as it is, its two words cut
apart: `(u32)(__aeabi_uidivmod(r4, 0xau) >> 32) + 0x30u` for r4 % 10 + '0' (gizmo a32's fmt_dec).

Which functions divide (find; the readable_types table's `divisions`: {function: [sign, results,
order]}, sign "u" or "s", results "q" (the quotient in the first result register), "r" (the remainder
there) or "qr" (the quotient, and the remainder in the second), order "nd" where the dividend comes
first, "dn" where the divisor does):

- by name, the RTABI's and libgcc's (NAMED);
- by what it does, for a routine without one of those names (a library of the compiler's own, a
  stripped image): a function of at most MAX_SIZE bytes that reads two argument registers is run in the
  emulator on PAIRS (none with a divisor of 0, either way round; negative ones tell the signed from
  the unsigned), and where its first result register holds the quotient or the remainder of its two
  arguments on every pair, in one order and one sign, and it writes nothing outside the stack, it
  divides (classify); the second result register holding the remainder too makes it "qr". A pair
  that faults or runs past PROBE_LIMIT instructions rules a function out.

How readable C writes a call of one (rewrite, before dread.polish): its quotient as n / d and its
remainder as n % d, the operands in the routine's order and of its sign (`(s32)n / (s32)d` where they
are not signed already): `(u32)f(a, b)` and a 32-bit place given f(a, b) are the quotient of a routine
that hands back both, `(u32)(f(a, b) >> 32)` the remainder, a routine of one result its one. A two-word
value the code keeps whole (u64 t = f(a, b), both halves read later) stays a call, as does a call with
other than two arguments. The extern of a routine no call names any more goes.

What the readable check links for them (support_source): GCC compiles / and % for such a processor into
calls of its own helpers (HELPERS: the RTABI's __aeabi_uidiv and kin, libgcc's __udivsi3 and kin; a
constant divisor it mostly turns into a multiplication instead), which the image may have under other
names or not at all; each one the image lacks is generated from the image's own routine of that sign
(one that hands back both results, else one of the result asked for), its arguments in the routine's
order. A function whose original calls a division routine may so call these helpers (readable's check:
foreign_calls takes them as what the original calls).

Where a function's / and % fail its cases, dread.readable's check tries it once more with its calls kept
(the readable_types table's divcalls; polish_tree leaves such a function's calls alone): a case that
stubs the routine and logs each call (a generated case whose run hung in it at a divisor of 0, a written
case that logs it) sees calls the compiler's / does not make where it knows the divisor or multiplies by
a constant. Its last try before it is dropped keeps the calls of the dividing functions its code reaches
too (their / runs in its cases), given back to / and % where it is dropped all the same.

A division by a constant, which a compiler makes a multiplication by the divisor's reciprocal and a
shift (constants, after rewrite): `(u32)((u64)r4 * 0xcccccccdu >> 32) >> 3` is r4 / 10 where
magic_divisor proves it for every value r4 can have (the error of such a quotient grows with the
dividend, so two values decide: the last of the last whole run of d values and the largest value; a
dividend whose form or one definition holds fewer bits, a mask or a cast, is proved for those: clang -O2
takes a smaller multiplier for a 15-bit value), and x minus that quotient times d is x % d. Such a
function goes into divcalls too where it fails (GCC -Os for ARMv4T makes a C % 150 a call of
__aeabi_uidivmod, which the original does not make).

Not done: a two-word value kept whole; 64-bit division (__aeabi_uldivmod); a routine that divides by
a constant of its own (a compiler's x / 10 as a call); a constant divisor's signed form and the one with
an add-back step (a 33-bit multiplier).
"""
import re

MASK = 0xFFFFFFFF
MAX_SIZE = 4096                 # bytes: libgcc's unrolled ARM __udivsi3 is well below
PROBE_LIMIT = 50000             # instructions a probe runs
# (dividend, divisor) as the registers get them: none 0 in either order; 0xfffffc18 is -1000, 0xfffffff9 -7
PAIRS = ((1000, 7), (12345, 10), (7, 1000), (0xFFFFFFF0, 3), (0xFFFFFC18, 7), (100, 0xFFFFFFF9),
         (0x80000000, 3), (0x7FFFFFFF, 0x10000))

NAMED = {
    "__aeabi_uidiv": ("u", "q", "nd"), "__aeabi_idiv": ("s", "q", "nd"),
    "__aeabi_uidivmod": ("u", "qr", "nd"), "__aeabi_idivmod": ("s", "qr", "nd"),
    "__udivsi3": ("u", "q", "nd"), "__divsi3": ("s", "q", "nd"),
    "__umodsi3": ("u", "r", "nd"), "__modsi3": ("s", "r", "nd"),
}

# the helpers a compiler's own code calls for a 32-bit / and % on an architecture without a divide
# instruction: {architecture: {(sign, what): (name, the results it hands back)}}
HELPERS = {
    "arm": {("u", "q"): ("__aeabi_uidiv", "q"), ("u", "r"): ("__aeabi_uidivmod", "qr"),
            ("s", "q"): ("__aeabi_idiv", "q"), ("s", "r"): ("__aeabi_idivmod", "qr")},
    "riscv": {("u", "q"): ("__udivsi3", "q"), ("u", "r"): ("__umodsi3", "r"),
              ("s", "q"): ("__divsi3", "q"), ("s", "r"): ("__modsi3", "r")},
}


def _s32(x):
    return x - (1 << 32) if x & 0x80000000 else x


def expected(a, b, sign, order):
    """(quotient, remainder) as 32-bit words of the division the arguments a, b (as the registers have
    them) make with that sign and order: C's, the quotient truncated towards 0."""
    n, d = (a, b) if order == "nd" else (b, a)
    if sign == "s":
        n, d = _s32(n), _s32(d)
        q = abs(n) // abs(d) * (1 if (n < 0) == (d < 0) else -1)
        return q & MASK, (n - q * d) & MASK
    return (n // d) & MASK, (n % d) & MASK


def classify(runs):
    """[sign, results, order] of what runs ([((a, b), (first, second result register))] on PAIRS) show,
    or None: the one division whose quotient or remainder the first register holds on every pair."""
    found = []
    for sign in ("u", "s"):
        for order in ("nd", "dn"):
            want = [expected(a, b, sign, order) for (a, b), _ in runs]
            if all(got[0] == q for (_, got), (q, _) in zip(runs, want)):
                both = all(got[1] == r for (_, got), (_, r) in zip(runs, want))
                found.append([sign, "qr" if both else "q", order])
            elif all(got[0] == r for (_, got), (_, r) in zip(runs, want)):
                found.append([sign, "r", order])
    return found[0] if len(found) == 1 else None


def probe(call):
    """classify's answer for a function call(a, b) runs (it returns (first, second result register), or
    None where the run faulted, wrote outside the stack or did not return), or None; it stops at the
    first pair no division of either sign and order explains."""
    runs = []
    for a, b in PAIRS:
        got = call(a, b)
        if got is None:
            return None
        runs.append(((a, b), got))
        if classify(runs) is None and not _still_possible(runs):
            return None
    return classify(runs)


def _still_possible(runs):
    """Whether some division explains every run so far (several may, early on)."""
    for sign in ("u", "s"):
        for order in ("nd", "dn"):
            want = [expected(a, b, sign, order) for (a, b), _ in runs]
            if all(got[0] == q for (_, got), (q, _) in zip(runs, want)) or \
                    all(got[0] == r for (_, got), (_, r) in zip(runs, want)):
                return True
    return False


DIVIDE_INSNS = {"udiv", "sdiv", "div", "divu", "rem", "remu"}    # an instruction that divides (ARM, RISC-V)


def find(program, context=None, target=None, image=None):
    """{function: [sign, results, order]} of the image's functions that divide: NAMED by name, and by
    behaviour (probe) every other function of at most MAX_SIZE bytes that reads two argument
    registers (context.arity, dread.regflow's reads; without a context none is probed). Not a function
    that uses a divide instruction (code of the program's own, which a runtime routine exists to stand
    in for: gizmo m3's udiv_var), nor one that calls a function that divides and hands back nothing that
    one does not (a wrapper of the program's own: gizmo a32's udiv_var, a quotient through __udivsi3's;
    __aeabi_uidivmod, which calls __udivsi3 too, adds the remainder): a call of either stays a call."""
    out = {fn.name: list(NAMED[fn.name]) for fn in program.functions if fn.name in NAMED}
    arity = getattr(context, "arity", None)
    if arity is None or target is None or image is None:
        return out
    candidates = [fn for fn in program.functions
                  if fn.name not in out and 0 < fn.size <= MAX_SIZE and _arity(arity, fn.name) == 2
                  and not _divides_itself(program, fn)]
    if not candidates:
        return out
    from dread.emu import EmuError, Layout, Machine
    layout = Layout(target, image)
    payload = image.payload()
    for fn in candidates:
        def call(a, b, fn=fn):
            m = Machine(layout, payload, isa_of=program.isa_at)
            m.wide_result = True
            try:
                r = m.call(fn.entry, a, b, max_instructions=PROBE_LIMIT)
            except (EmuError, Exception):       # (a fault, an unmapped access, no return)
                return None
            if r.writes:
                return None
            return r.ret & MASK, (r.ret >> 32) & MASK
        got = probe(call)
        if got is not None:
            out[fn.name] = got
    from dread import lift
    wrappers = set()
    for name, (sign, results, _) in out.items():
        if name in NAMED:
            continue
        for callee in lift.direct_callees(program, program.by_name[name]) & (set(out) - {name}):
            if out[callee][0] == sign and set(results) <= set(out[callee][1]):
                wrappers.add(name)          # (it hands back nothing its callee does not: udiv_var)
    return {name: k for name, k in out.items() if name not in wrappers}


def _divides_itself(program, fn):
    """Whether fn uses one of the architecture's divide instructions."""
    for insn in program.instructions(fn):
        if insn is not None and getattr(insn, "mnemonic", "").split(".")[0] in DIVIDE_INSNS:
            return True
    return False


def _arity(arity, name):
    try:
        return arity(name)
    except Exception:
        return None


# ---- readable C

def rewrite(tree, fn, divisions, int_bits=None):
    """The calls of the functions that divide (divisions, as find gives them) in fn's readable C as / and %
    (the module's docstring), in place; then the externs of those no call names any more go. Not in a
    routine that divides itself: its / would be compiled into a helper that may be made of it
    (support_source), which then calls itself (gizmo a32's __aeabi_uidivmod calls __udivsi3; written
    r0 / r1, GCC calls __aeabi_uidiv, made of __aeabi_uidivmod). Returns whether anything changed."""
    if not divisions or fn.name in divisions:
        return False
    from dread import cparse as C
    from dread import polish
    from dread.naming import _rewrite, _rewrite_stmt
    types = polish.Types(tree, fn, int_bits)
    changed = []

    def kind(e):
        if isinstance(e, C.Call) and isinstance(e.f, C.Name) and e.f.id in divisions and len(e.args) == 2:
            return divisions[e.f.id]
        return None

    def operand(x, sign):
        """x as an operand of that sign: a constant as the number it is there, else cast."""
        if isinstance(x, C.Num) and isinstance(x.value, int):
            v = x.value & MASK
            if sign == "s" and v & 0x80000000:
                return C.Unary("-", C.Num("%d" % (-_s32(v))))
            return C.Num("%d" % v) if sign == "s" else C.Num("%du" % v)
        return C.Cast("s32" if sign == "s" else "u32", x)

    def op(call, what):
        sign, _, order = kind(call)
        a, b = (_rewrite(x, f) for x in call.args)
        n, d = (a, b) if order == "nd" else (b, a)
        want = ("int", (32, sign == "s"))
        n, d = (operand(x, sign) if types.of(x) != want else x for x in (n, d))
        changed.append(call.f.id)
        return C.Binary("/" if what == "q" else "%", n, d)

    def f(e):
        if isinstance(e, C.Cast) and " ".join(e.type.split()) == "u32":
            x = e.e
            k = kind(x)
            if k and k[1] == "qr":
                return op(x, "q")
            if isinstance(x, C.Binary) and x.op == ">>" and isinstance(x.r, C.Num) and x.r.value == 32:
                k = kind(x.l)
                if k and k[1] == "qr":
                    return op(x.l, "r")
        if isinstance(e, C.Assign) and e.op == "=":
            k = kind(e.value)
            t = types.of(e.target)
            if k and k[1] == "qr" and t and t[0] == "int" and t[1][0] == 32:
                e.target = _rewrite(e.target, f)
                e.value = op(e.value, "q")
                return e
        k = kind(e)
        if k and k[1] in ("q", "r"):
            return op(e, k[1])
        return None
    for st in fn.body.body:
        _rewrite_stmt(st, f)
    if not changed:
        return False
    left = set()
    for st in polish._all_statements(fn.body.body):
        for e in polish._stmt_exprs(st):
            left |= {x.f.id for x in polish._walk(e) if isinstance(x, C.Call) and isinstance(x.f, C.Name)}
    gone = set(changed) - left
    tree.items[:] = [item for item in tree.items if not (
        isinstance(item, C.Raw) and any(re.match(r"^extern .*\b%s\(" % re.escape(g), item.text.strip()) for g in gone))]
    return True


# ---- division by a constant, as the compiler multiplied it

def magic_divisor(m, k, bits=32):
    """d where floor(x * m / 2**k) is floor(x / d) for every unsigned x of bits bits, else None: m the
    multiplier, k how far the product is shifted right. d is the nearest 2**k / m; with e = m * d - 2**k
    not negative (else x = d gives 0 already), the error x * e / (d * 2**k) grows with x, so for each
    remainder r of d the hardest x is the largest below 2**bits that has it, and two of them decide for
    all: the last x of the last whole run of d values (remainder d - 1, the least room) and 2**bits - 1
    (the largest error, in the part run after it, whose remainders have more room)."""
    if not (0 < m < (1 << 33)) or not (32 <= k < 66) or not 0 < bits <= 32:
        return None
    d = ((1 << k) + m // 2) // m
    top = (1 << bits) - 1
    if d < 2 or d > top or m * d < (1 << k):
        return None
    for x in (((top + 1) // d) * d - 1, top):
        if (x * m) >> k != x // d:
            return None
    return d


def _bits(e, defs, depth=0):
    """How many low bits an unsigned 32-bit expression's values can have set, from its form: a constant's
    own, a cast to u8 or u16, a mask, a right shift, a variable by its one definition (defs); 32 otherwise
    (dread readable's C names a value once where it can: webs)."""
    from dread import cparse as C
    if depth > 8:
        return 32
    if isinstance(e, C.Num) and isinstance(e.value, int):
        return max(1, (e.value & MASK).bit_length())
    if isinstance(e, C.Cast):
        t = " ".join(e.type.split())
        inner = _bits(e.e, defs, depth + 1)
        return min(inner, {"u8": 8, "u16": 16}.get(t, 32)) if t in ("u8", "u16", "u32") else 32
    if isinstance(e, C.Binary) and e.op == "&":
        return min(_bits(e.l, defs, depth + 1), _bits(e.r, defs, depth + 1))
    if isinstance(e, C.Binary) and e.op == ">>" and isinstance(e.r, C.Num) and isinstance(e.r.value, int):
        return max(1, _bits(e.l, defs, depth + 1) - e.r.value)
    if isinstance(e, C.Name) and e.id in defs:
        return _bits(defs[e.id], defs, depth + 1)
    return 32


def _definitions(fn):
    """{variable: its value} for the function's locals given a value by one plain assignment and changed
    nowhere else (not a parameter, its address never taken)."""
    from dread import cparse as C
    from dread import polish
    count, value, bad = {}, {}, {n for _, n in fn.params}
    for st in polish._all_statements(fn.body.body):
        for e in polish._stmt_exprs(st):
            for x in polish._walk(e):
                if isinstance(x, C.Assign) and isinstance(x.target, C.Name):
                    count[x.target.id] = count.get(x.target.id, 0) + 1
                    if x.op == "=":
                        value[x.target.id] = x.value
                    else:
                        bad.add(x.target.id)
                elif isinstance(x, (C.Post, C.Unary)) and x.op in ("++", "--") and isinstance(x.e, C.Name):
                    bad.add(x.e.id)
                elif isinstance(x, C.Unary) and x.op == "&" and isinstance(x.e, C.Name):
                    bad.add(x.e.id)
        if isinstance(st, C.Decl):
            for d in st.items:
                if d.init is not None:
                    count[d.name] = count.get(d.name, 0) + 1
                    value[d.name] = d.init
    return {v: value[v] for v, n in count.items() if n == 1 and v in value and v not in bad}


def _high_product(e, types):
    """(x, m, k) where e is the high word of an unsigned 32-bit x times a constant m shifted right by k in
    all ((u32)((u64)x * m >> k), k >= 32, and that shifted right again: k the sum), else None."""
    from dread import cparse as C
    extra = 0
    if isinstance(e, C.Binary) and e.op == ">>" and isinstance(e.r, C.Num) and isinstance(e.r.value, int):
        extra, e = e.r.value, e.l
    if not (isinstance(e, C.Cast) and " ".join(e.type.split()) == "u32"):
        return None
    e = e.e
    if not (isinstance(e, C.Binary) and e.op == ">>" and isinstance(e.r, C.Num) and isinstance(e.r.value, int)
            and e.r.value >= 32):
        return None
    k = e.r.value + extra
    prod = e.l
    if not (isinstance(prod, C.Binary) and prod.op == "*"):
        return None
    for wide, m in ((prod.l, prod.r), (prod.r, prod.l)):
        if isinstance(m, C.Cast) and " ".join(m.type.split()) == "u64":
            m = m.e
        if isinstance(wide, C.Cast) and " ".join(wide.type.split()) == "u64" and isinstance(m, C.Num) \
                and isinstance(m.value, int):
            x = wide.e
            if types.of(x) == ("int", (32, False)):
                return x, m.value, k
    return None


def constants(tree, fn, int_bits=None):
    """Division by a constant, which a compiler makes a multiplication by a reciprocal and a shift, as /
    and % in fn's readable C, in place: (u32)((u64)x * m >> 32) >> s is x / d where magic_divisor proves it
    for every value x can have (by the numbers, not by the pattern: an unsigned 32-bit x, fewer bits where
    its form says so, _bits; clang -O2 takes a smaller multiplier for a value it knows is short); x minus
    that quotient times d is then x % d. Returns whether anything changed. Not done: the signed form (a
    correction of the sign) and the one with an add-back step (a 33-bit multiplier)."""
    from dread import cparse as C
    from dread import polish
    from dread.naming import _rewrite_stmt
    from dread.structure import _has_effects
    types = polish.Types(tree, fn, int_bits)
    defs = _definitions(fn)
    changed = []

    def number(d, x):
        return C.Num("%d" % d) if types.of(x) == ("int", (32, False)) else C.Num("%du" % d)

    def f(e):
        got = _high_product(e, types)
        if got is not None:
            x, m, k = got
            d = magic_divisor(m, k, _bits(x, defs))
            if d is not None:
                changed.append(d)
                return C.Binary("/", x, number(d, x))
        if isinstance(e, C.Binary) and e.op == "-":
            y, prod = e.l, e.r
            if isinstance(prod, C.Binary) and prod.op == "*":
                for q, n in ((prod.l, prod.r), (prod.r, prod.l)):
                    got = _high_product(q, types)
                    if got is None or not isinstance(n, C.Num) or not isinstance(n.value, int):
                        continue
                    x, m, k = got
                    d = magic_divisor(m, k, _bits(x, defs))
                    if d is not None and d == n.value and not _has_effects(x) and \
                            C.expr_text(x) == C.expr_text(y):
                        changed.append(d)
                        return C.Binary("%", x, number(d, x))
        return None
    for st in fn.body.body:
        _rewrite_stmt(st, f)
    return bool(changed)


def has_constants(tree, fn, int_bits=None):
    """Whether constants would write a division by a constant in fn (the readable check tries such a
    function once more with them as multiplications, as it does a routine's calls)."""
    import copy
    tree = copy.deepcopy(tree)
    return constants(tree, tree.function(fn.name), int_bits)


# ---- what the readable check links

SUPPORT = "dread_divisions.c"   # the generated file's name (dread.build build: its own object)


def helper_names(arch_name):
    """The names of the helpers a compiler's own code calls for / and % on the architecture."""
    return {name for name, _ in HELPERS.get(arch_name, {}).values()}


def support_source(arch_name, divisions, defined):
    """C of the helpers HELPERS names for the architecture (arch_name: dread.arch's name) that the image
    lacks (defined: its functions' names), each from one of its routines that divide (divisions, as find
    gives them) of that sign: one that hands back both results, else one of the result the helper hands
    back; "" where none is needed or none can be made."""
    helpers = HELPERS.get(arch_name)
    if not helpers or not divisions:
        return ""
    externs, lines = {}, []
    for (sign, what), (name, hands) in sorted(helpers.items()):
        if name in defined:
            continue
        made = _helper(name, hands, what, sign, divisions)
        if made is None:
            continue
        routine, rtype, line = made
        externs[routine] = "extern %s %s(u32, u32);" % (rtype, routine)
        lines.append(line)
    if not lines:
        return ""
    return ("/* dread: the division helpers the readable C's own / and % call, from the image's routines "
            "(dread.divisions) */\n#include \"dread.h\"\n" + "\n".join(sorted(externs.values())) + "\n" +
            "\n".join(lines) + "\n")


def _helper(name, hands, what, sign, divisions):
    """(routine, its result type, the helper's definition) of the helper name (what it hands back: hands,
    "q", "r" or "qr"; for what: "q" or "r"), from a routine of divisions of the same sign, or None."""
    for routine, (rsign, rres, rorder) in sorted(divisions.items(), key=lambda kv: (kv[1][1] != "qr", kv[0])):
        if rsign != sign or routine == name:
            continue
        args = "n, d" if rorder == "nd" else "d, n"
        rtype = "u64" if rres == "qr" else "u32"
        if hands == "qr" and rres == "qr":
            return routine, rtype, "u64 %s(u32 n, u32 d) { return %s(%s); }" % (name, routine, args)
        if hands == "q" and rres in ("q", "qr"):
            return routine, rtype, "u32 %s(u32 n, u32 d) { return (u32)%s(%s); }" % (name, routine, args)
        if hands == "r" and rres == "qr":
            return routine, rtype, "u32 %s(u32 n, u32 d) { return (u32)(%s(%s) >> 32); }" % (name, routine, args)
        if hands == "r" and rres == "r":
            return routine, rtype, "u32 %s(u32 n, u32 d) { return %s(%s); }" % (name, routine, args)
    return None

