"""dread.bitfields: extensions and bit fields in readable C. Where the machine has no sign- or
zero-extending instruction and no bit-field extract (ARM state before ARMv6, RV32 without Zbb), a cast
to short is two shifts (lsl #16; asr #16), a signed bit field two more, a select a smeared sign under a
mask; the pass writes them as the casts, fields and selects they are ((s16)x, (x >> 2) & 0xffu, x < 0 ?
-3 : 0), each rewrite proved by z3 for C's types. Each fold on snippets, the same functions compiled by
the host's C compiler beside the lifted C, the known bits against the values, and a program of our own
through the gate: ARMv4T in ARM state at -Os and -O0, a Cortex-M3 (sxth, ubfx) and RV32IMAC, all four
read alike."""
import ctypes
import random
import shutil
import subprocess

import pytest

from dread import bitfields, cexpr, cparse, readable, tidy

needs_z3 = pytest.mark.skipif(cexpr.z3 is None, reason="needs z3-solver (the extra 'prove')")
needs_gcc = pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")

TYPES = {"x": (32, False), "y": (32, False), "w": (32, False), "q": (32, False), "z": (32, False),
         "h": (16, False), "b": (8, False), "sh": (16, True)}


def expr(text):
    return cparse.parse("void f(void)\n{\n    v = %s;\n}\n" % text).function("f").body.body[0].e.value


def ctx():
    return cexpr.Ctx(types=TYPES)


def folded(text):
    c = ctx()
    return cparse.expr_text(bitfields.simplified(expr(text), c, bitfields.Prover(c)))


# (lifted C, readable C): what each fold makes of a form the lifters write
FOLDS = [
    ("(u32)((s32)(x << 16) >> 16)", "(u32)(s16)x"),                     # lsl #16; asr #16: a short
    ("(u32)((s32)(x << 24) >> 24)", "(u32)(s8)x"),                      # a signed char
    ("x << 16 >> 16", "(u32)(u16)x"),                                   # lsl #16; lsr #16: unsigned
    ("(x + y) << 24 >> 24", "(u32)(u8)(x + y)"),
    ("(u32)((s32)((x & 0xffffu) << 16) >> 16)", "(u32)(s16)x"),         # the low half, then its sign
    ("(u32)((s32)((x << 16 >> 16) << 16) >> 16)", "(u32)(s16)x"),       # -O0's two pairs
    ("(u32)((s32)(x << 20) >> 24)", "(u32)(s8)(x >> 4)"),               # a signed field of 8 bits
    ("(u32)((s32)((u32)((s32)x >> 4) << 24) >> 24)", "(u32)(s8)(x >> 4)"),
    ("(u32)((s32)(((w >> 2) & 0xffu) << 16) >> 16)", "(w >> 2) & 0xffu"),   # its sign bit is 0
    ("(u32)((s32)(w & 0x3fcu) >> 2)", "(w >> 2) & 0xffu"),              # a mask clears the sign
    ("(u32)((s32)w >> 2) & 0xffu", "(w >> 2) & 0xffu"),                 # a mask clears what asr copied
    ("(w & 0x3fcu) >> 2", "(w >> 2) & 0xffu"),
    ("(x << 2) & 4u", "(x & 1u) << 2"),
    ("(x << 8) & 0xff00u", "(x & 0xffu) << 8"),
    ("(x >> 24) & 0xffu", "x >> 24"),                                   # masks the bits satisfy
    ("(u32)*(volatile u8 *)x & 0xffu", "(u32)*(volatile u8 *)x"),
    ("(u32)h & 0xffffu", "(u32)h"),
    ("(x & 7u) & 31u", "x & 7u"),
    ("x << 28 >> 26", "(x & 0xfu) << 2"),
    ("x << 4 >> 8", "(x >> 4) & 0xffffffu"),
    ("(x >> 3) << 3", "x & ~7u"),
    ("x >> 2 >> 3", "x >> 5"),
    ("(u32)((s32)((s32)(x << 28) >> 24) >> 4)", "(u32)((s32)(x << 28) >> 28)"),     # two asr
    ("(u32)(s16)(u32)(u16)x", "(u32)(s16)x"),                           # what a narrow cast drops
    ("(u32)(s8)(x & 0x3ffu)", "(u32)(s8)x"),
    ("(u32)(s16)((x >> 2) & 0xffu)", "(x >> 2) & 0xffu"),               # a value that fits
    ("(u32)((s32)x >> 31) & 0xfffffffdu", "(s32)x < 0 ? 0xfffffffdu : 0"),     # a smeared sign
    ("x & ~(u32)((s32)x >> 31)", "(s32)x >= 0 ? x : 0"),               # bic r0, r0, r0, asr #31
    ("x & (0 - (u32)(x < 24u))", "x < 24u ? x : 0"),                   # RISC-V's sltu; neg; and
    ("((0 - (u32)((x & 2u) < 1)) & 0xfffffff0u) + 0x30u", "(x & 2u) == 0 ? 0x20u : 0x30u"),
    ("(((u32)((s32)(x << 30) >> 31)) & 0x10u) + 0x20u", "(x & 2u) != 0 ? 0x30u : 0x20u"),
]

KEPT = [
    "(u32)((s32)(x << 28) >> 28)",          # a signed field of 4 bits: C has no type for it
    "(u32)((s32)(x << 16) >> 15)",          # no field: more bits than a short's
    "(x << 2) & 0xfffffff0u",               # a mask that keeps what the shift made
    "x & 0xffu",                            # a mask the bits do not satisfy
    "(u32)((s32)x >> 4)",                   # a sign that may be set
    "(u32)((s32)x >> 31) & *(volatile u32 *)y",     # a select would load on one way only
]


@needs_z3
@pytest.mark.parametrize("lifted,wanted", FOLDS)
def test_each_fold(lifted, wanted):
    assert folded(lifted) == wanted, (lifted, folded(lifted))
    c = ctx()
    assert cexpr.prove(expr(lifted), expr(wanted), c)


@needs_z3
@pytest.mark.parametrize("lifted", KEPT)
def test_what_has_no_simpler_form_stays(lifted):
    assert folded(lifted) == cparse.expr_text(expr(lifted)), folded(lifted)


@needs_z3
def test_a_proposal_that_does_not_hold_is_refused(monkeypatch):
    """The rules only propose: one that would not compute the same is refused by the proof (a sign
    extension proposed for an arithmetic shift by one bit fewer), as is one that would drop a load."""
    c = ctx()
    wrong = expr("(u32)(s16)x")
    monkeypatch.setattr(bitfields, "proposals", lambda e, ctx: [wrong] if isinstance(e, cparse.Cast) else [])
    lifted = expr("(u32)((s32)(x << 16) >> 15)")
    assert cparse.expr_text(bitfields.simplified(lifted, c, bitfields.Prover(c))) == cparse.expr_text(lifted)
    prove = bitfields.Prover(c)
    assert not prove(expr("(u32)*(volatile u8 *)x & 0u"), expr("0u"))      # (the load would go)


def test_without_z3_nothing_changes(monkeypatch):
    monkeypatch.setattr(cexpr, "z3", None)
    fn = cparse.parse("u32 f(u32 r0)\n{\n    return (u32)((s32)(r0 << 16) >> 16);\n}\n").function("f")
    assert not bitfields.bitfields(fn)


@needs_z3
def test_the_pass_runs_in_the_clean_up_and_leaves_the_compare_idiom():
    """tidy runs the pass among its others: the lifted ARM-state statements of a function that returns
    a short (lsl; asr) read as one cast; a compare's carry idiom keeps the shape its readers know
    ((u64)x + (u32)~y + 1: the pass drops no cast of a value to its own type but an asr's)."""
    text = """u32 f(u32 a0, u32 a1)
{
    u32 r0 = a0, r1 = a1;
    u32 n = 0, z = 0, c = 0, v = 0;
    (void)n; (void)z; (void)c; (void)v;
    r0 = (r0 << 16);  /* 0: lsl r0, r0, #16 */
    r0 = (u32)((s32)r0 >> 16);  /* 4: asr r0, r0, #16 */
    { u32 x = r0, y = r1; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 8: cmp r0, r1 */
    if (!c) goto L_14;  /* c: blo */
    r0 = r1;  /* 10: mov r0, r1 */
L_14: ;
    return r0;  /* 14: bx lr */
}
"""
    fn = cparse.parse(text).function("f")
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False)
    assert "(u32)(s16)r0" in out and "<< 16" not in out, out
    assert "r1 <=" in out or ">= r1" in out or "< r1" in out or "r1 >" in out, out      # (the comparison)


@needs_z3
def test_a_sum_through_64_bits_under_a_byte_store_is_left_to_the_sums_pass():
    """A byte stored from the lifters' add with its carry, (u8)(u32)((u64)r6 + 1 + 0) (clang -O2's
    adds; strb): the pass leaves what goes through 64 bits to dread.tidy sums, which reads the (u32) of
    it by its shape; a first version dropped that cast (the store reads only the low byte) and the store
    printed (u8)(r6 + 1) instead of r6 + 1."""
    text = """u32 f(u32 a0, u32 a1)
{
    u32 r0 = a0, r1 = a1, r6 = 0;
    u32 n = 0, z = 0, c = 0, v = 0;
    (void)n; (void)z; (void)c; (void)v;
    r6 = (u32)(u8)(r1);  /* 0: uxtb r6, r1 */
    r1 = (u32)((u64)r6 + 1 + 0);  /* 4: adds r1, r6, #1 */
    *(volatile u8 *)((r0 + 69)) = (u8)r1;  /* 8: strb r1, [r0, #0x45] */
    r0 = r6;  /* c: mov r0, r6 */
    return r0;  /* 10: bx lr */
}
"""
    fn = cparse.parse(text).function("f")
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False)
    assert "= (u8)(r6 + 1);" in out and "u64" not in out, out


@needs_z3
def test_a_flags_select_of_one_and_zero_is_left_to_the_flags_pass():
    """An IT block that sets a register to 1 or 0 by a compare's flags, then a byte's extension of it
    (movls r3, #1; movhi r3, #0; uxtb r3, r3: GCC -O0): (u32)(u8)(!c || z ? 0x1u : 0x0u), whose flags
    read a 64-bit temporary. dread.tidy flags makes the whole value the comparison r0 <= r1; a first
    version of this pass dropped the (u8) first (a select of 0 and 1 fits a byte), and flags then only
    rewrote the select's condition: r3 = r0 <= r1 ? 0x1u : 0x0u."""
    text = """extern u32 g(u32, u32, u32, u32);
u32 f(u32 a0, u32 a1)
{
    u32 r0 = a0, r1 = a1, r2 = 0, r3 = 0, r12 = 0;
    u32 n = 0, z = 0, c = 0, v = 0;
    (void)n; (void)z; (void)c; (void)v; (void)r12;
    { u32 x = r0, y = r1; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 0: cmp r0, r1 */
    if ((!c || z)) { r3 = 0x1u; }  /* 4: movls r3, #1 */
    if ((c && !z)) { r3 = 0x0u; }  /* 6: movhi r3, #0 */
    r3 = (u32)(u8)(r3);  /* 8: uxtb r3, r3 */
    r0 = r3;  /* a: mov r0, r3 */
    r0 = g(r0, r1, r2, r3); r1 = r2 = r3 = r12 = 0;  /* c: bl g */
    return r0;  /* 10: pop {pc} */
}
"""
    fn = cparse.parse(text).function("f")
    tidy.tidy(fn)
    out = cparse.unparse(fn, notes=False)
    assert "r3 = r0 <= r1;" in out, out


# --- known bits against the values C computes

def random_expr(rng, depth=3):
    leaves = ["x", "y", "h", "b", "sh", "(u32)(u8)x", "(u32)(s8)y", "(u32)(s16)x", "(u32)h", "0x%xu" % rng.getrandbits(
        rng.choice([4, 8, 16, 32])), "%d" % rng.randint(0, 40)]
    if depth == 0 or rng.random() < 0.25:
        return rng.choice(leaves)
    a, b = random_expr(rng, depth - 1), random_expr(rng, depth - 1)
    r = rng.random()
    if r < 0.45:
        return "(%s %s %s)" % (a, rng.choice(["&", "|", "^", "+", "-", "*"]), b)
    if r < 0.65:
        return "(%s %s %d)" % (a, rng.choice(["<<", ">>"]), rng.randint(0, 31))
    if r < 0.75:
        return "((s32)%s >> %d)" % (a, rng.randint(0, 31))
    if r < 0.85:
        return "(%s)%s" % (rng.choice(["u8", "s8", "u16", "s16", "u32", "s32"]), a)
    if r < 0.9:
        return "(%s %s %s)" % (a, rng.choice(["<", "==", "!="]), b)
    if r < 0.95:
        return "(%s ? %s : %s)" % (a, b, random_expr(rng, depth - 1))
    return "(~%s)" % a


def test_known_bits_agree_with_the_values():
    """known on random expressions of variables of several widths: on random values, every bit it
    calls 0 or 1 is so in the value C computes (dread.cexpr), and the value has at least as many copies
    of its top bit as it says."""
    rng = random.Random(5)
    c = ctx()
    checked = 0
    for _ in range(400):
        e = expr(random_expr(rng))
        k = bitfields.known(e, c)
        try:
            f = cexpr.compile_expr(e, c)
        except cexpr.Undefined:
            continue
        if k is None:
            continue
        for _ in range(40):
            env = {n: rng.choice([0, 1, 0x7f, 0x80, 0xff, 0x7fff, 0x8000, 0xffff, 0x7fffffff, 0x80000000,
                                  0xffffffff, rng.getrandbits(32)]) & ((1 << TYPES[n][0]) - 1) for n in TYPES}
            try:
                value, ty = f(env)
            except cexpr.Undefined:
                continue
            bits = value & ((1 << k.bits) - 1)      # (in the promoted type: an s16's -5 is 0xfffffffb)
            assert (k.bits, k.signed) == cexpr.promote(ty, c), (cparse.expr_text(e), ty, k.type)
            assert bits & k.zeros == 0 and bits & k.ones == k.ones, (cparse.expr_text(e), hex(bits), hex(k.zeros),
                                                                       hex(k.ones))
            top = bits >> (k.bits - 1)
            run = 0
            while run < k.bits and (bits >> (k.bits - 1 - run)) & 1 == top:
                run += 1
            assert run >= k.signs, (cparse.expr_text(e), hex(bits), k.signs)
            checked += 1
    assert checked > 5000, checked


# --- the folds compiled beside the lifted C

def random_fold(rng):
    """A lifted expression of x, y, z (u32) of one of the forms the pass rewrites, with random shifts,
    masks and operands."""
    def operand():
        return rng.choice(["x", "(x + y)", "(u32)(u16)y", "(u32)(u8)y", "(u32)(s16)y", "(x >> %d)" % rng.randint(1, 20),
                           "((x >> %d) & 0x%xu)" % (rng.randint(0, 8), (1 << rng.randint(1, 20)) - 1), "(y ^ z)"])
    y, a, b = operand(), rng.randint(1, 31), rng.randint(1, 31)
    lo, hi = min(a, b), max(a, b)
    m = rng.choice([(1 << rng.randint(1, 31)) - 1, rng.getrandbits(32), 0xff << rng.randint(0, 24),
                    ((1 << rng.randint(1, 12)) - 1) << rng.randint(0, 16)]) or 1
    forms = [
        "(u32)((s32)(%s << %d) >> %d)" % (y, lo, hi),
        "(u32)((s32)(%s << %d) >> %d)" % (y, rng.choice([16, 24]), rng.choice([16, 24])),
        "((%s << %d) >> %d)" % (y, a, b),
        "((%s << %d) >> %d)" % (y, rng.choice([16, 24]), rng.choice([16, 24])),
        "(u32)((s32)(%s & 0x%xu) >> %d)" % (y, m, a),
        "((%s << %d) & 0x%xu)" % (y, a, m),
        "((%s & 0x%xu) >> %d)" % (y, m, a),
        "(%s & 0x%xu)" % (y, m),
        "((u32)((s32)%s >> %d) & 0x%xu)" % (y, a, m),
        "((u32)((s32)%s >> 31) & 0x%xu)" % (y, m),
        "(z & ~(u32)((s32)%s >> 31))" % y,
        "(z & (0 - (u32)(%s < z)))" % y,
        "(((u32)((s32)(%s << %d) >> 31) & 0x%xu) + 0x%xu)" % (y, a, m, rng.getrandbits(16)),
        "(u32)((s32)((u32)((s32)(%s << %d) >> %d)) >> %d)" % (y, lo, hi, rng.randint(1, 31)),
        "(u32)(%s)(%s & 0x%xu)" % (rng.choice(["s8", "s16", "u8", "u16"]), y, m),
        "(u32)(s16)(u32)(u16)%s" % y,
        "((%s >> %d) << %d)" % (y, a, a),
    ]
    return rng.choice(forms)


PRELUDE = ("typedef unsigned int u32;\ntypedef signed int s32;\ntypedef unsigned short u16;\ntypedef short s16;\n"
           "typedef unsigned char u8;\ntypedef signed char s8;\n")


@needs_z3
@pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")
def test_random_folds_compiled_beside_the_lifted_c(tmp_path):
    """Random expressions of the forms the pass rewrites (random_fold), folded, compiled with the host's
    C compiler beside the lifted ones (GCC's semantics where C leaves them to the implementation: a
    conversion to a signed type modulo 2**n, an arithmetic right shift of a negative value, as on the
    targets) and called with random and boundary arguments: the same value every time, and most of them
    rewritten."""
    rng = random.Random(23)
    c = ctx()
    prove = bitfields.Prover(c)
    count, lifted, made = 300, [], []
    for i in range(count):
        text = random_fold(rng)
        lifted.append(text)
        made.append(cparse.expr_text(bitfields.simplified(expr(text), c, prove)))
    source = PRELUDE + "".join("u32 lifted_%d(u32 x, u32 y, u32 z) { return %s; }\nu32 folded_%d(u32 x, u32 y, u32 z) "
                               "{ return %s; }\n" % (i, a, i, b) for i, (a, b) in enumerate(zip(lifted, made)))
    (tmp_path / "folds.c").write_text(source)
    lib = tmp_path / "folds.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-fwrapv", "-o", str(lib), str(tmp_path / "folds.c")],
                   check=True)
    so = ctypes.CDLL(str(lib))
    edges = [0, 1, 2, 0x7f, 0x80, 0xff, 0x100, 0x7fff, 0x8000, 0xffff, 0x10000, 0x7fffffff, 0x80000000, 0xffffffff]
    for i in range(count):
        f, g = getattr(so, "lifted_%d" % i), getattr(so, "folded_%d" % i)
        for h in (f, g):
            h.restype, h.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 3
        for _ in range(60):
            args = [rng.choice(edges + [rng.getrandbits(32)] * 4) for _ in range(3)]
            assert f(*args) == g(*args), (lifted[i], made[i], args)
    changed = sum(1 for a, b in zip(lifted, made) if cparse.expr_text(expr(a)) != b)
    assert changed > count * 0.6, changed


# --- a program of our own through the gate

BITS_PROGRAM = """typedef unsigned int u32;
struct bits_reg { int low:4; int mid:8; unsigned high:4; };
short bits_short(int x) { return (short)x; }
signed char bits_schar(int x) { return (signed char)x; }
unsigned short bits_ushort(int x) { return (unsigned short)x; }
short bits_short_sum(short a, short b) { return a + b; }
unsigned short bits_ushort_sum(unsigned short a, unsigned short b) { return a + b; }
int bits_signed_byte(int w) { return (signed char)(w >> 4); }
int bits_mid(struct bits_reg *r) { return r->mid; }
int bits_low(struct bits_reg *r) { return r->low; }
u32 bits_moved(u32 x) { return (x & 1) << 2; }
u32 bits_field(u32 w) { return (w & 0x3fc) >> 2; }
int bits_signed_field(int w) { return (w & 0x3fc) >> 2; }
__attribute__((noinline)) int bits_check(const char *s) { return *s - 'a'; }
int bits_sign_select(const char *s) { if (bits_check(s) < 0) return -3; return 0; }
u32 bits_pad(u32 flags) { return (flags & 2) ? '0' : ' '; }
int bits_clamp(int x) { return x < 0 ? 0 : x; }
int main(void) { return 0; }
"""

# What each function returns in readable C (R the first argument's register, S the second's), alike on
# every build; before, ARM state and RV32 printed (s32)(r0 << 16) >> 16, r0 & 0xffffu, (s32)((r0 + r1)
# << 16) >> 16, (r0 + r1) << 16 >> 16, (s32)((u32)((s32)r0 >> 4) << 24) >> 24 (RV32: (s32)(a0 << 20) >>
# 24), (r0 << 2) & 4, ((s32)r0 >> 2) & 0xffu, r0 & ~(u32)((s32)r0 >> 31) (RV32: a0 & ((s32)(a0 ^
# 0xffffffffu) >> 31)), (bits_check(r0) >> 31) & ~0x2u and RV32's ((0 - (u32)((a0 & 2) < 1)) &
# 0xfffffff0u) + 0x30u
BITS_READ = {
    "bits_short": "return (s16)R;", "bits_schar": "return (s8)R;", "bits_ushort": "return (u16)R;",
    "bits_short_sum": "return (s16)(R + S);", "bits_ushort_sum": "return (u16)(R + S);",
    "bits_signed_byte": "return (s8)(R >> 4);", "bits_moved": "return (R & 1) << 2;",
    "bits_field": "return (R >> 2) & 0xffu;", "bits_signed_field": "return (R >> 2) & 0xffu;",
}
BUILDS = [("a32", "-Os"), ("a32", "-O0"), ("m3", "-Os"), ("rv32", "-Os")]


def needs_build(kind):
    from conftest import needs_riscv_gcc
    return needs_riscv_gcc if kind == "rv32" else needs_gcc


@pytest.mark.slow
@needs_z3
@pytest.mark.parametrize("kind,opt", [pytest.param(k, o, marks=needs_build(k)) for k, o in BUILDS])
def test_extensions_and_bit_fields_read_alike_on_every_build_and_pass_the_gate(kind, opt, tmp_path):
    """BITS_PROGRAM with GCC for ARMv4T in ARM state (-Os and -O0: no sxth, uxth or ubfx; shift pairs,
    at -O0 two of them, and bic r0, r0, r0, asr #31 for the clamp), a Cortex-M3 (sxth, uxth, ubfx, sbfx)
    and RV32IMAC (slli; srai, sltu; neg; and): every function lifts and the gate takes every one of them
    in its readable C; the casts, the fields and the selects read the same on all four (BITS_READ); a
    signed bit field of 8 bits is a cast of the field ((s8)(... >> 4)), one of 4 keeps its shifts, and
    the smeared signs are selects."""
    from dread import build, lift, regflow
    from dread.program import Program
    from test_statics import kind_target
    target = kind_target(tmp_path, kind, BITS_PROGRAM, opt=opt)
    image = target.image()
    lifted = build.read_names(image.table("lifted"))
    assert set(BITS_READ) | {"bits_mid", "bits_low", "bits_sign_select", "bits_pad", "bits_clamp"} <= set(lifted)
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert accepted == sorted(lifted), lines
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    context = readable.Context(target, image, program, flow)
    lifter = lift.make_lifter(program, flow)
    regs = ("a0", "a1") if kind == "rv32" else ("r0", "r1")

    def text(name):
        return readable.transform(name, lifter.lift(program.by_name[name]).text, context=context)
    for name, wanted in BITS_READ.items():
        out = text(name)
        assert wanted.replace("R", regs[0]).replace("S", regs[1]) in out, (name, out)
    mid, low = text("bits_mid"), text("bits_low")
    assert "return (s8)(" in mid and ">> 4);" in mid and "<< 20" not in mid, mid
    assert "<< 28) >> 28;" in low, low
    select = text("bits_sign_select")       # (a select, or a branch where the compiler made one: GCC 10 and
    # GCC 13 for RV32, whose bltz prints as if (bits_check(a0) >= 0) with the arms the other way round)
    assert ("bits_check(%s) < 0" % regs[0] in select or "bits_check(%s) >= 0" % regs[0] in select) and \
        "-3" in select and ">> 31" not in select, select
    clamp, pad = text("bits_clamp"), text("bits_pad")     # (selects, or branches: GCC 10 for RV32)
    assert ("%s >= 0 ? %s : 0;" % (regs[0], regs[0]) in clamp or "if (%s < 0)" % regs[0] in clamp) and \
        ">> 31" not in clamp and "0 - " not in clamp, clamp
    assert "(%s & 2) == 0" % regs[0] in pad or "(%s & 2) != 0" % regs[0] in pad, pad
    assert "0 - " not in pad and ">> 31" not in pad, pad


# --- gizmo

@needs_z3
def test_gizmo_clamps_its_volume_with_a_select(variant, target, program):
    """gizmo's cmd_volume clamps its level to 0..9 (clamp(level, 0, 9)): GCC's ARM-state and Thumb-2
    code takes the lower bound with bic r0, r0, r0, asr #31, clang's RV32 code with sgtz; neg; and. Both
    read as selects (before: r0 & ~(u32)((s32)r0 >> 31), a0 & (0 - (u32)((s32)a0 > 0))); and parse_kv's
    return of parse_uint(...) < 0 ? -3 : 0, which RV32's code computes with srai a0, a0, 31; andi a0,
    a0, -3, reads so (before: ((s32)parse_uint(...) >> 31) & 0xfffffffdu)."""
    from dread import lift, regflow
    image = target.image()
    context = readable.Context(target, image, program)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))

    def text(name):
        return readable.transform(name, lifter.lift(program.by_name[name]).text, context=context)
    volume = text("cmd_volume")
    assert ">> 31" not in volume and "0 - " not in volume, volume
    assert (" >= 0 ? " in volume or " > 0 ? " in volume) and " : 0;" in volume, volume
    if variant == "rv32":
        kv = text("parse_kv")
        assert "parse_uint(" in kv and " < 0 ? -3 : 0;" in kv and ">> 31" not in kv, kv
