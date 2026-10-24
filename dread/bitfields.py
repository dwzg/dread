"""dread readable's extensions and bit fields (a pass of dread.tidy): what a compiler makes of a cast to
a narrower type, of a bit field and of a select without a branch where the machine has no instruction
for them, written as C writes it. ARM state before ARMv6 has no sxth, sxtb, uxth or ubfx, RV32 without
Zbb no sext.h, sext.b or zext.h: GCC and clang shift left and back (lsl #16; asr #16 for a short, slli;
srai for a signed char, lsl #20; asr #24 for a signed bit field), combine masks with shifts ((x << 2) &
4), and select with a smeared sign (srai a0, a0, 31; andi a0, a0, -3 for f() < 0 ? -3 : 0; bic r0, r0,
r0, asr #31 for a clamp at 0). The lifters write asr as (u32)((s32)x >> n), so readable C showed
(s32)(x << 16) >> 16 where the source has (short)x, and Thumb-2's sxth of the same source (s16)x.

Rewrites, at every node of every statement's expressions, its children first (bottom up):

- a sign extension by shifts, (u32)((s32)(y << a) >> b) with a <= b: a field of b - a .. 31 - a of y,
  sign-extended. Of 8 or 16 bits it is (u32)(s8)(y >> (b - a)) or (u32)(s16)..., (u32)(s16)y where it
  starts at bit 0 (polish drops the (u32) where the use converts anyway: r0 = (s16)r1;); where the
  field's top bit is known to be 0 (known bits, below) it is the unsigned field (y >> (b - a)) & mask; a
  field of another width keeps its shifts (C has no type for it); two arithmetic shifts are one (-O0's
  asr #24; asr #4);
- an arithmetic shift of a value whose top bit is known to be 0, (u32)((s32)(w & 0x3fcu) >> 2), is the
  logical shift (w & 0x3fcu) >> 2, and a mask that clears the bits it shifts in makes it logical too:
  (u32)((s32)w >> 2) & 0xffu is (w >> 2) & 0xffu;
- a pair of logical shifts, (y << a) >> b: the low bits of y as (u32)(u16)y or (u32)(u8)y (a == b, 16 or
  24: as uxth and uxtb read on Thumb-2), a field (y >> (b - a)) & mask for b > a, (y & mask) << (a - b)
  for b < a; (y >> a) << a is y & ~mask; two shifts the same way are one, a shift by 0 none (dread.tidy
  peephole folds the other pairs of a plain variable, without proof, and leaves these to this pass);
- a mask moved to the field it selects: (w & m) >> n is (w >> n) & (m >> n), (x << n) & m is (x & (m >>
  n)) << n where m >> n is one run of ones below the top ((x << 2) & 4 as (x & 1) << 2), two masks one
  ((x & m1) & m2);
- what the known bits make redundant: a mask that clears only bits known to be 0 ((r0 >> 24) & 0xffu,
  (u32)*(volatile u8 *)p & 0xffu, a select of 0 and 1 & 1), a cast to a narrower type of a value that
  fits it under a cast back ((u32)(s16)((x >> 2) & 0xffu) is (x >> 2) & 0xffu: a field sign-extended
  from a bit it never sets, (s32)(((x >> 2) & 0xffu) << 16) >> 16), and under a cast to 8 or 16 bits
  what changes only higher bits ((s16)(u32)(u16)x is (s16)x, (s8)(x & 0x3ffu) is (s8)x,
  (s8)((u32)((s32)x >> 4)) is (s8)(x >> 4), and a constant added within NEGATIVE of 2**32 a subtraction
  there: RV32's addi -1 under a cast, (s8)(x - 1), which polish leaves as x + 0xffffffffu where a cast
  reads it);
- a value that is all ones or 0 under a mask, the select it computes: a smeared sign ((u32)((s32)v >>
  31) & m is (s32)v < 0 ? m : 0, of v = y << a the bit 31 - a of y: (y & bit) != 0), a truth value
  negated (RISC-V's sltu; neg: x & (0 - (u32)(x < 24)) is x < 24 ? x : 0) and the complement of either
  (bic r0, r0, r0, asr #31: (s32)r0 >= 0 ? r0 : 0), where the masked value reads no memory and calls
  nothing (a select evaluates its arm on one way only); a constant operation on a select of constants
  goes into its arms ((c ? 0x10u : 0) + 0x20u as c ? 0x30u : 0x20u).

Known bits (known): what an expression's type and operations say of its value's bits in its promoted
type, as C computes it: bits known to be 0, known to be 1, and how many top bits are copies of the top
bit (a value sign-extended from 16 bits has 17); from constants, the types of variables, casts and
loads (a u16 load's top 16 bits are 0), masks, shifts, comparisons (0 or 1), selects and sums. It only
proposes: every rewrite is proved by z3 through dread.cexpr (cexpr.equivalent: the same value, with C's
types, wherever the lifted expression is defined: the integer promotions, the conversions to signed
types modulo 2**n and arithmetic shifts of signed values as GCC has them) for the node it replaces, and
a node keeps its type (a cast where the rewrite's own type would differ: a u16 promotes to int, not to
unsigned int). A rewrite keeps each load and call of the node, once. The lifters' idioms through 64
bits ((u64)x + (u32)~y + 1, (u32)((u64)x + 4 + 0)) are left whole for the passes that read them by
their shape (sums, flags, pairs: they run after this one each round, and what they leave comes back to
it), as are casts of a value to its own type but those between two arithmetic shifts.

What is not established: a variable's bits from its definitions (r2 = r3 & 7; ... r2 & 0xffu keeps its
mask: the proof sees r2 as any u32; on the eight builds such masks are three in each of gizmo's ARM
builds' script_run and GCC -O0's __NVIC_EnableIRQ, all of them the amount of a register shift, (r2 &
0xffu) == 0 ? x : (r2 & 0xffu) < 32 ? x << (r2 & 0xffu) : 0 for x << (i & 7)); the forms of 16-bit int
(MSP430: the pass leaves its functions alone); a mask the source wrote as a mask stays one (x & 0xffff,
never (u16)x).
"""
from dread import cexpr
from dread import cparse as C
from dread.cflow import is_pure_call, is_volatile_access, walk_expr

NEGATIVE = 0x100     # a constant added within this of 2**32 is a subtraction under a cast to 8 or 16 bits
ROUNDS = 8          # the most rewrites one node takes (each proved; a bound against rules that would cycle)
NARROW = {8: ("u8", "s8"), 16: ("u16", "s16")}      # the widths C has a type for below int's


def _mask(bits):
    return (1 << bits) - 1


def _top_run(bits, zeros, ones):
    """How many top bits are all known 0 or all known 1."""
    n = 0
    for kind in (zeros, ones):
        k = 0
        while k < bits and kind >> (bits - 1 - k) & 1:
            k += 1
        n = max(n, k)
    return n


class Known:
    """What is known of a value's bits in its type (bits wide, signed): zeros and ones the bits known to
    be 0 and 1, signs how many top bits are copies of the top bit (at least 1: the top bit itself)."""
    __slots__ = ("bits", "signed", "zeros", "ones", "signs")

    def __init__(self, bits, signed, zeros=0, ones=0, signs=1):
        m = _mask(bits)
        self.bits, self.signed = bits, signed
        self.zeros, self.ones = zeros & m, ones & m
        self.signs = min(bits, max(signs, _top_run(bits, self.zeros, self.ones), 1))

    @property
    def type(self):
        return (self.bits, self.signed)

    def low_zeros(self):
        """How many low bits are known to be 0."""
        n = 0
        while n < self.bits and self.zeros >> n & 1:
            n += 1
        return n

    def high_zeros(self):
        """How many top bits are known to be 0."""
        n = 0
        while n < self.bits and self.zeros >> (self.bits - 1 - n) & 1:
            n += 1
        return n

    def fits(self, bits, signed):
        """Whether the value's bits are those of its low bits (fewer than its type's) extended, with
        zeros (signed False) or with copies of the top one of them (signed): converted to a type of
        that width and back to one of its own, it is itself."""
        if signed:
            return self.signs >= self.bits - bits + 1
        return self.high_zeros() >= self.bits - bits


def _resize(k, bits):
    """k converted to bits (of the same signedness as k: the caller sets the target's): wider is
    extended by k's own signedness (zeros above an unsigned value, copies of the top bit above a
    signed one), narrower keeps the low bits."""
    if bits == k.bits:
        return Known(bits, k.signed, k.zeros, k.ones, k.signs)
    if bits < k.bits:
        return Known(bits, k.signed, k.zeros, k.ones, k.signs - (k.bits - bits))
    high = _mask(bits) ^ _mask(k.bits)
    if not k.signed:
        return Known(bits, False, k.zeros | high, k.ones)
    top = 1 << (k.bits - 1)
    zeros = k.zeros | (high if k.zeros & top else 0)
    ones = k.ones | (high if k.ones & top else 0)
    return Known(bits, True, zeros, ones, k.signs + bits - k.bits)


def convert(k, ty):
    """k converted to type ty ((bits, signed)): its bits resized, then read with ty's signedness."""
    r = _resize(k, ty[0])
    return Known(ty[0], ty[1], r.zeros, r.ones, r.signs)


def promoted(k, ctx):
    """k as the integer promotions make it: a type narrower than int is converted to int."""
    return convert(k, (ctx.int_bits, True)) if k.bits < ctx.int_bits else k


def _load_type(e, ctx):
    """The pointee type of a load *(T *)p ((bits, signed)), or None."""
    if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast):
        words = e.e.type.split()
        if words.count("*") == 1 and words[-1] == "*":
            return ctx.ctype(" ".join(w for w in words[:-1] if w not in ("volatile", "const")))
    return None


def known(e, ctx):
    """What is known of e's bits, as a Known of e's promoted type; None where e is no integer whose type
    the tree shows (a pointer, an address, an object)."""
    if isinstance(e, C.Num):
        if not isinstance(e.value, int):
            return None
        ty = cexpr.num_type(e.text, e.value, ctx)
        v = e.value & _mask(ty[0])
        return promoted(Known(ty[0], ty[1], ~v, v), ctx)
    if isinstance(e, C.Name):
        ty = ctx.types.get(e.id)
        return None if ty is None else promoted(Known(*ty), ctx)
    if isinstance(e, C.Cast):
        ty = ctx.ctype(e.type)
        if ty is None:
            return None
        inner = known(e.e, ctx)
        return promoted(Known(*ty) if inner is None else convert(inner, ty), ctx)
    if isinstance(e, C.Call):
        ty = ctx.result_type(e)
        return None if ty is None else promoted(Known(*ty), ctx)
    if isinstance(e, C.Unary):
        if e.op == "*":
            ty = _load_type(e, ctx)
            return None if ty is None else promoted(Known(*ty), ctx)
        if e.op == "!":
            return Known(ctx.int_bits, True, ~1)
        inner = known(e.e, ctx)
        if inner is None or e.op not in ("~", "-", "+"):
            return None
        if e.op == "~":
            return Known(inner.bits, inner.signed, inner.ones, inner.zeros, inner.signs)
        if e.op == "-":                     # (-x keeps x's low zeros)
            return Known(inner.bits, inner.signed, _mask(inner.low_zeros()))
        return inner
    if isinstance(e, C.Cond):
        a, b = known(e.t, ctx), known(e.f, ctx)
        if a is None or b is None:
            return None
        ty = cexpr.common(a.type, b.type, ctx)
        a, b = convert(a, ty), convert(b, ty)
        return Known(ty[0], ty[1], a.zeros & b.zeros, a.ones & b.ones, min(a.signs, b.signs))
    if isinstance(e, C.Binary):
        return _binary(e, ctx)
    return None


def _binary(e, ctx):
    op = e.op
    if op in ("==", "!=", "<", "<=", ">", ">=", "&&", "||"):
        return Known(ctx.int_bits, True, ~1)
    a, b = known(e.l, ctx), known(e.r, ctx)
    if a is None or b is None:
        return None
    if op in ("<<", ">>"):
        n = e.r.value if isinstance(e.r, C.Num) and isinstance(e.r.value, int) else None
        if n is None or not 0 <= n < a.bits:
            return Known(a.bits, a.signed)
        m = _mask(a.bits)
        if op == "<<":
            return Known(a.bits, a.signed, (a.zeros << n) | _mask(n), a.ones << n, a.signs - n)
        if not a.signed:
            return Known(a.bits, False, (a.zeros >> n) | (m ^ (m >> n)), a.ones >> n)
        top = 1 << (a.bits - 1)
        high = m ^ (m >> n)
        return Known(a.bits, True, (a.zeros >> n) | (high if a.zeros & top else 0),
                     (a.ones >> n) | (high if a.ones & top else 0), a.signs + n)
    ty = cexpr.common(a.type, b.type, ctx)
    a, b = convert(a, ty), convert(b, ty)
    bits, signed = ty
    if op == "&":
        return Known(bits, signed, a.zeros | b.zeros, a.ones & b.ones, min(a.signs, b.signs))
    if op == "|":
        return Known(bits, signed, a.zeros & b.zeros, a.ones | b.ones, min(a.signs, b.signs))
    if op == "^":
        return Known(bits, signed, (a.zeros & b.zeros) | (a.ones & b.ones), (a.zeros & b.ones) | (a.ones & b.zeros),
                     min(a.signs, b.signs))
    low = min(a.low_zeros(), b.low_zeros())
    if op in ("+", "-"):
        zeros = _mask(low)
        if op == "+":                       # (a sum of two values below 2**k is below 2**(k + 1))
            high = min(a.high_zeros(), b.high_zeros()) - 1
            if high > 0:
                zeros |= _mask(bits) ^ _mask(bits - high)
        return Known(bits, signed, zeros, 0, min(a.signs, b.signs) - 1)
    if op == "*":
        zeros = _mask(min(bits, a.low_zeros() + b.low_zeros()))
        used = (bits - a.high_zeros()) + (bits - b.high_zeros())
        if used < bits:
            zeros |= _mask(bits) ^ _mask(used)
        return Known(bits, signed, zeros)
    if op in ("/", "%") and not signed and isinstance(e.r, C.Num) and isinstance(e.r.value, int) and e.r.value > 0:
        k = e.r.value
        if op == "/":
            high = min(bits, a.high_zeros() + k.bit_length() - 1)
        else:
            high = bits - (k - 1).bit_length()
        return Known(bits, False, _mask(bits) ^ _mask(bits - high))
    return Known(bits, signed)


# --- the rewrites ---

def type_name(ty):
    bits, signed = ty
    return "%s%d" % ("s" if signed else "u", bits)


def num(value):
    """An unsigned literal as the lifters write a mask: hex with u, below 10 decimal."""
    return C.Num("0x%xu" % value if value > 9 else "%du" % value)


def count(n):
    return C.Num("%d" % n)


def _value(e):
    return e.value if isinstance(e, C.Num) and isinstance(e.value, int) and not isinstance(e.value, bool) else None


def _masked(e):
    """(x, m) of x & m with a constant m (on either side), or None."""
    if isinstance(e, C.Binary) and e.op == "&":
        if _value(e.r) is not None:
            return e.l, _value(e.r)
        if _value(e.l) is not None:
            return e.r, _value(e.l)
    return None


def _shifted(e, op):
    """(x, n) of x << n or x >> n (op) with a constant n, or None."""
    if isinstance(e, C.Binary) and e.op == op and _value(e.r) is not None:
        return e.l, _value(e.r)
    return None


def _asr(e):
    """(x, n) of the lifters' arithmetic shift (u32)((s32)x >> n) with a constant n, or None."""
    if isinstance(e, C.Cast) and e.type == "u32" and isinstance(e.e, C.Binary) and e.e.op == ">>" and \
            isinstance(e.e.l, C.Cast) and e.e.l.type == "s32" and _value(e.e.r) is not None:
        return e.e.l.e, _value(e.e.r)
    return None


def _sar(e):
    """(x, n) of an arithmetic shift with a constant n, the lifters' (u32)((s32)x >> n) or the bare
    (s32)x >> n, or None."""
    got = _asr(e)
    if got is not None:
        return got
    if isinstance(e, C.Binary) and e.op == ">>" and isinstance(e.l, C.Cast) and e.l.type == "s32" and \
            _value(e.r) is not None:
        return e.l.e, _value(e.r)
    return None


def _smear(s, ctx):
    """The condition under which s, a value of 32 bits that is all ones or 0, is all ones, or None: a
    sign smeared over the word ((u32)((s32)v >> 31): (s32)v < 0; of v = y << a, the bit 31 - a of y: (y &
    bit) != 0), a truth value negated (0 - (u32)(a < b), RISC-V's neg of an sltu: a < b), the
    complement of either (~smear: the condition negated)."""
    from dread import structure, tidy
    u32 = (32, False)
    got = _asr(s)
    if got is not None and got[1] == 31 and _kind(got[0], ctx) == u32:
        v = got[0]
        shifted = _shifted(v, "<<")
        if shifted is not None and _kind(shifted[0], ctx) == u32 and 0 < shifted[1] < 32:
            return C.Binary("!=", C.Binary("&", shifted[0], num(1 << (31 - shifted[1]))), C.Num("0"))
        return C.Binary("<", C.Cast("s32", v), C.Num("0"))
    b = None
    if isinstance(s, C.Unary) and s.op == "-":
        b = s.e
    elif isinstance(s, C.Binary) and s.op == "-" and _value(s.l) == 0:
        b = s.r
    if b is not None and _kind(s, ctx) == u32:
        kb = known(b, ctx)
        if kb is not None and not (_mask(kb.bits) & ~1 & ~kb.zeros):      # (b is 0 or 1)
            inner = b.e if isinstance(b, C.Cast) and tidy.truth_value(b.e) else b
            if isinstance(inner, C.Binary) and inner.op == "<" and _value(inner.r) == 1 and \
                    _kind(inner.l, ctx) == u32:     # (RISC-V's seqz, sltiu x, 1: x == 0)
                return C.Binary("==", inner.l, C.Num("0"))
            return inner if tidy.truth_value(inner) else C.Binary("!=", inner, C.Num("0"))
    if isinstance(s, C.Unary) and s.op == "~":
        cond = _smear(s.e, ctx)
        return structure.negate(cond) if cond is not None else None
    return None


def _kind(x, ctx):
    got = known(x, ctx)
    return got.type if got is not None else None


def _pure(e):
    """Whether e reads no memory and calls nothing (a select may evaluate it on one way only)."""
    return not any(is_volatile_access(x) or isinstance(x, (C.Call, C.Assign, C.Post)) or
                   (isinstance(x, C.Unary) and x.op in ("*", "++", "--")) for x in walk_expr(e))


def _folded(e, ctx):
    """A constant operation of two literals as one literal (dread.tidy fold_constants), or None."""
    from dread import tidy
    return tidy.fold_constants(e.op, e.l, e.r, ctx)


def _signed_shift(e):
    """Whether e is (s32)x >> n: a value of type s32."""
    return isinstance(e, C.Binary) and e.op == ">>" and isinstance(e.l, C.Cast) and e.l.type == "s32"


def _as(x, ty, ctx):
    """x with the type ty (promoted): x itself where it has it, else a cast."""
    k = known(x, ctx)
    if k is not None and k.type == ty:
        return x
    return C.Cast(type_name(ty), x)


def _run(m):
    """Whether m's ones are one run (a field's mask)."""
    return m > 0 and ((m >> ((m & -m).bit_length() - 1)) + 1) & (m >> ((m & -m).bit_length() - 1)) == 0


def _field(y, low, width):
    """The unsigned field of width bits from bit low of a 32-bit unsigned y: (y >> low) & mask, the shift
    left out for low 0."""
    inner = y if low == 0 else C.Binary(">>", y, count(low))
    return C.Binary("&", inner, num(_mask(width)))


def proposals(e, ctx):
    """Simpler forms of node e (its children as they are), the strictly smaller first; each still to be
    proved."""
    out = []
    u32 = (32, False)
    k = None

    def kind(x):
        got = known(x, ctx)
        return got.type if got is not None else None
    masked = _masked(e)
    if masked is not None:
        x, m = masked
        k = known(e, ctx)
        kx = known(x, ctx)
        if k is not None and kx is not None:
            kx = convert(kx, k.type)
            if not (_mask(k.bits) & ~m & ~kx.zeros):        # (every bit m clears is 0 in x)
                out.append(_as(x, k.type, ctx))
            inner = _masked(x)
            if inner is not None and k.type == u32 and kind(inner[0]) == u32 and m & inner[1]:
                out.append(C.Binary("&", inner[0], num(m & inner[1])))
            left = _shifted(x, "<<")
            if left is not None and k.type == u32 and kind(left[0]) == u32 and 0 < left[1] < 32:
                y, n = left             # (a field of y placed at bit n: one run of ones below the top)
                if _run(m >> n) and (m >> n).bit_length() < 32 - n:
                    out.append(C.Binary("<<", C.Binary("&", y, num(m >> n)), count(n)))
            asr = _asr(x)
            if asr is not None and k.type == u32 and kind(asr[0]) == u32 and 0 < asr[1] < 32 and \
                    m >> (32 - asr[1]) == 0:      # (the mask clears every bit the shift copied the sign into)
                out.append(C.Binary("&", C.Binary(">>", asr[0], count(asr[1])), num(m)))
    if isinstance(e, C.Binary) and e.op == "&" and kind(e) == u32:
        for s, y in ((e.l, e.r), (e.r, e.l)):
            cond = _smear(s, ctx)
            if cond is not None and _pure(y):
                out.append(C.Cond(cond, _as(y, u32, ctx), C.Num("0")))
    if isinstance(e, C.Binary) and e.op in ("+", "-", "|", "^", "&"):
        for c, k2, left in ((e.l, e.r, True), (e.r, e.l, False)):
            if isinstance(c, C.Cond) and _value(c.t) is not None and _value(c.f) is not None and \
                    _value(k2) is not None:     # ((c ? 0x10u : 0u) + 0x20u: c ? 0x30u : 0x20u)
                arms = [C.Binary(e.op, a, k2) if left else C.Binary(e.op, k2, a) for a in (c.t, c.f)]
                folded = [_folded(a, ctx) for a in arms]
                if None not in folded:
                    out.append(C.Cond(c.c, folded[0], folded[1]))
    for op in ("<<", ">>"):                 # (a shift by 0: ubfx r0, r0, #0, #15 behind lsrs r0, #16)
        zero = _shifted(e, op)
        if zero is not None and zero[1] == 0 and kind(zero[0]) == u32:
            out.append(zero[0])
    right = _shifted(e, ">>")
    if right is not None and kind(e) == u32 and 0 < right[1] < 32:
        x, b = right
        again = _shifted(x, ">>")
        if again is not None and kind(again[0]) == u32 and 0 < again[1] and again[1] + b < 32:
            out.append(C.Binary(">>", again[0], count(again[1] + b)))
        pair = _shifted(x, "<<")
        if pair is not None and kind(pair[0]) == u32 and 0 < pair[1] < 32:
            y, a = pair
            if a == b and 32 - a in NARROW:
                out.append(C.Cast("u32", C.Cast(NARROW[32 - a][0], y)))
            elif b >= a:
                out.append(_field(y, b - a, 32 - b))
            else:
                out.append(C.Binary("<<", C.Binary("&", y, num(_mask(32 - a))), count(a - b)))
        inner = _masked(x)
        if inner is not None and kind(inner[0]) == u32 and inner[1] >> b:
            out.append(C.Binary("&", C.Binary(">>", inner[0], count(b)), num(inner[1] >> b)))
    left = _shifted(e, "<<")
    if left is not None and kind(e) == u32 and 0 < left[1] < 32:
        x, b = left
        again = _shifted(x, "<<")
        if again is not None and kind(again[0]) == u32 and 0 < again[1] and again[1] + b < 32:
            out.append(C.Binary("<<", again[0], count(again[1] + b)))
        pair = _shifted(x, ">>")
        if pair is not None and pair[1] == b and kind(pair[0]) == u32:      # (the low bits cleared: ~0xffu
            keep = C.Unary("~", num(_mask(b))) if b < 16 else num(_mask(32) ^ _mask(b))     # or 0xf8000000u)
            out.append(C.Binary("&", pair[0], keep))
    if right is not None and kind(e) == (32, True) and 0 < right[1] < 32:
        inner = _sar(right[0])              # ((s32)y >> a) >> b: one shift (two asr, the casts between gone)
        if inner is not None and _asr(right[0]) is None and kind(inner[0]) == u32 and 0 < inner[1] < 32:
            out.append(C.Binary(">>", C.Cast("s32", inner[0]), count(min(inner[1] + right[1], 31))))
    asr = _asr(e)
    if asr is not None and kind(asr[0]) == u32 and 0 < asr[1] < 32:
        x, b = asr
        inner = _asr(x)
        if inner is not None and kind(inner[0]) == u32 and 0 < inner[1] < 32:
            out.append(C.Cast("u32", C.Binary(">>", C.Cast("s32", inner[0]), count(min(inner[1] + b, 31)))))
        kx = known(x, ctx)
        if kx is not None and kx.zeros >> 31 & 1:          # (its top bit 0: a logical shift)
            out.append(C.Binary(">>", x, count(b)))
        pair = _shifted(x, "<<")
        if pair is not None and kind(pair[0]) == u32 and 0 < pair[1] <= b:
            y, a = pair
            low, width = b - a, 32 - b
            ky = known(y, ctx)
            if ky is not None and ky.zeros >> (31 - a) & 1:     # (the field's top bit 0: unsigned)
                out.append(_field(y, low, width))
            elif width in NARROW:
                out.append(C.Cast("u32", C.Cast(NARROW[width][1], y if low == 0 else C.Binary(">>", y, count(low)))))
    if isinstance(e, C.Cast):
        ty = ctx.ctype(e.type)
        # The casts between two arithmetic shifts (an asr of an asr: (s32)((u32)((s32)y >> a)) >> b) go;
        # no other cast of a value to its own type (the lifters' compare idiom, (u64)x + (u32)~y + 1, is
        # read by its shape: dread.tidy compared)
        if ty == (32, True) and _signed_shift(e.e):
            out.append(e.e)
        if ty is not None and ty[0] == 32 and isinstance(e.e, C.Cast):
            inner = ctx.ctype(e.e.type)
            ki = known(e.e.e, ctx)
            if inner is not None and ki is not None and inner[0] in NARROW and ki.bits == 32 and \
                    ki.fits(inner[0], inner[1]):
                out.append(_as(e.e.e, ty, ctx) if ki.type != ty else e.e.e)
            elif inner is not None and inner[0] == 32 and _signed_shift(e.e.e):
                out.append(C.Cast(e.type, e.e.e))
        if ty is not None and ty[0] in NARROW:
            n = ty[0]
            x = e.e
            if isinstance(x, C.Cast):           # ((s16)(u32)(u16)x, (s8)(u16)x: the narrow casts between)
                inner = ctx.ctype(x.type)
                if inner is not None and inner[0] >= n:
                    deeper = x.e
                    if isinstance(deeper, C.Cast) and inner[0] == 32:
                        di = ctx.ctype(deeper.type)
                        if di is not None and n <= di[0] < 32:
                            out.append(C.Cast(e.type, deeper.e))
                    if inner[0] < 32:
                        out.append(C.Cast(e.type, x.e))
            inner = _masked(x)
            if inner is not None and inner[1] & _mask(n) == _mask(n):
                out.append(C.Cast(e.type, inner[0]))
            if isinstance(x, C.Binary) and x.op == "+" and _value(x.r) is not None and kind(x) == u32 and \
                    _mask(32) - NEGATIVE < _value(x.r) <= _mask(32):    # (RV32's addi a0, a0, -1 under a cast:
                out.append(C.Cast(e.type, C.Binary("-", x.l, num((1 << 32) - _value(x.r)))))    # (s8)(a0 - 1))
            asr = _sar(x)
            if asr is not None and kind(asr[0]) == u32 and 0 < asr[1] and asr[1] + n <= 32:
                out.append(C.Cast(e.type, C.Binary(">>", asr[0], count(asr[1]))))
    return out


def _effects(e):
    """The texts of e's loads and calls (sorted, each as often as it is there)."""
    return sorted(C.expr_text(x) for x in walk_expr(e)
                  if is_volatile_access(x) or (isinstance(x, C.Call) and not is_pure_call(x)))


class Prover:
    """cexpr.equivalent of a node and a proposal, kept by what it reads (the texts, the variables'
    types, int's width) in memo (dread.tidy's run: the passes run again each round)."""

    def __init__(self, ctx, memo=None):
        self.ctx, self.memo = ctx, memo if memo is not None else {}

    def __call__(self, e, cand):
        if _effects(e) != _effects(cand):
            return False
        names = sorted({x.id for y in (e, cand) for x in walk_expr(y) if isinstance(x, C.Name)})
        key = ("bitfields", C.expr_text(e), C.expr_text(cand), tuple((n, self.ctx.types.get(n)) for n in names),
               self.ctx.int_bits)
        if key not in self.memo:
            self.memo[key] = cexpr.equivalent(e, cand, self.ctx)
        return self.memo[key]


def simplified(e, ctx, prove):
    """e with its extensions and bit fields rewritten, children first; a node takes a proposal that
    prove proves, then its own simpler forms again (at most ROUNDS). Returns a new tree (e where nothing
    changed)."""
    e2 = _children_simplified(e, ctx, prove)
    for _ in range(ROUNDS):
        cands = proposals(e2, ctx)
        if not cands or _wide(e2, ctx):
            break
        for cand in cands:
            if prove(e2, cand):
                e2 = _children_simplified(cand, ctx, prove)
                break
        else:
            break
    return e2


def _wide(e, ctx):
    """Whether e goes through 64 bits: the lifters' carry and compare idioms ((u32)((u64)x + 4 + 0),
    (u64)x + (u32)~y + 1, a flag of a 64-bit temporary: (u32)(t_3 >> 32)), which dread.tidy's sums, flags
    and pairs read by their shape and rewrite first (until 2026-10-24 a cast this pass dropped from
    (u8)(u32)((u64)r6 + 1 + 0) kept sums from reading the add, and a byte store printed (u8)(r6 + 1) on
    clang -O2; one it dropped from (u32)(u8)(!(u32)(t_3 >> 32) || (u32)t_3 == 0 ? 0x1u : 0x0u), a
    flag's select of 1 and 0, kept flags from making it the comparison r0_1 <= s24 on GCC -O0)."""
    return any((isinstance(x, C.Cast) and x.type in ("u64", "s64")) or
               (isinstance(x, C.Name) and (ctx.types.get(x.id) or (0,))[0] > 32) for x in walk_expr(e))


def _children_simplified(e, ctx, prove):
    def s(x):
        return simplified(x, ctx, prove)
    if isinstance(e, C.Binary):
        l, r = s(e.l), s(e.r)
        return e if l is e.l and r is e.r else C.Binary(e.op, l, r)
    if isinstance(e, C.Cast):
        inner = s(e.e)
        return e if inner is e.e else C.Cast(e.type, inner)
    if isinstance(e, C.Unary):
        if e.op in ("&", "++", "--"):       # (an object, not a value)
            return e
        inner = s(e.e)
        return e if inner is e.e else C.Unary(e.op, inner)
    if isinstance(e, C.Cond):
        c, t, f = s(e.c), s(e.t), s(e.f)
        return e if (c, t, f) == (e.c, e.t, e.f) else C.Cond(c, t, f)
    if isinstance(e, C.Call):
        args = [s(a) for a in e.args]
        return e if all(a is b for a, b in zip(args, e.args)) else C.Call(e.f, args)
    if isinstance(e, C.Assign):
        target = e.target
        if isinstance(target, C.Unary) and target.op == "*":
            inner = s(target.e)
            target = target if inner is target.e else C.Unary("*", inner)
        value = s(e.value)
        return e if target is e.target and value is e.value else C.Assign(e.op, target, value)
    if isinstance(e, C.Index):
        index = s(e.index)
        return e if index is e.index else C.Index(e.base, index)
    return e


def _shaped(e):
    """Whether e holds a node of a shape some rule starts from (a shift by a constant, a mask, a select
    of constants under an operation with one, a negation, a cast), by its tree alone: the statements
    without one are left alone and a function without any needs no types (until 2026-10-24 the pass
    made them for every function each round: half its time)."""
    for x in walk_expr(e):
        if isinstance(x, C.Binary):
            if (x.op in ("<<", ">>") and _value(x.r) is not None) or (x.op == "-" and _value(x.l) == 0) or \
                    (x.op == "&" and (_value(x.r) is not None or _value(x.l) is not None)) or \
                    isinstance(x.l, C.Cond) or isinstance(x.r, C.Cond):
                return True
        elif (isinstance(x, C.Unary) and x.op == "-") or (isinstance(x, C.Cast) and isinstance(x.e, C.Cast)):
            return True
    return False


def bitfields(fn):
    """The pass (dread.tidy PASSES): every simple statement's expressions, those inside conditional
    blocks too (dread.tidy flat_view), simplified; whether anything changed. Nothing without z3 (the
    extra "prove"), nothing where int is not 32 bits wide."""
    from dread import tidy
    if cexpr.z3 is None:
        return False
    view = tidy.flat_view(fn)
    work = [(get, put) for s in view.body.body for get, put in tidy.stmt_exprs(s) if _shaped(get())]
    if not work:
        return False
    ctx = tidy.context(fn)
    if ctx.int_bits != 32:
        return False
    prove = Prover(ctx, getattr(tidy._RUN, "memo", None))
    changed = False
    for get, put in work:
        before = get()
        after = simplified(before, ctx, prove)
        if after is not before and C.expr_text(after) != C.expr_text(before):
            put(after)
            changed = True
    tidy.write_back(view)
    return changed
