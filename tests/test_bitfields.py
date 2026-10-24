"""dread.bitfields: extensions and bit fields in readable C. Where the machine has no sign- or
zero-extending instruction and no bit-field extract (ARM state before ARMv6, RV32 without Zbb), a cast
to short is two shifts (lsl #16; asr #16), a signed bit field two more, a select a smeared sign under a
mask; the pass writes them as the casts, fields and selects they are ((s16)x, (x >> 2) & 0xffu, x < 0 ?
-3 : 0), each rewrite proved by z3 for C's types. Each fold on snippets, the same functions compiled by
the host's C compiler beside the lifted C, the known bits against the values, and a program of our own
through the gate: ARMv4T in ARM state at -Os and -O0, a Cortex-M3 (sxth, ubfx) and RV32IMAC, all four
read alike. ARM's shift by a register (its first arm gone, the plain shift where the amount is known
below 32, from its expression or its variable's definitions: variable_facts, the facts cexpr's proofs
assume): the forms on snippets, the facts of small functions' variables, random functions of such
shifts cleaned up and compiled beside the originals, gizmo's script_run and gpio_set, and a program of
our own through the gate on the same four builds."""
import ctypes
import random
import re
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


# --- ARM's shift by a register: its first arm, and an amount known below 32

LSL = "(s & 0xffu) == 0 ? x : (s & 0xffu) < 32 ? x << (s & 0xffu) : 0u"     # (dread.lift REG_SHIFT)
LSR = "(s & 0xffu) == 0 ? x : (s & 0xffu) < 32 ? x >> (s & 0xffu) : 0u"
ASR = "(s & 0xffu) == 0 ? x : (u32)((s32)x >> ((s & 0xffu) < 32 ? (s & 0xffu) : 31))"
ROR = "((s & 0xffu) & 31) == 0 ? x : (x >> ((s & 0xffu) & 31)) | (x << (32 - ((s & 0xffu) & 31)))"
BELOW_8 = {"s": (0xfffffff8, 0)}            # (s's facts: its bits from 3 up known to be 0)
BELOW_256 = {"s": (0xffffff00, 0)}

# (lifted C, facts, readable C)
SHIFTS = [
    (LSL, {}, "(s & 0xffu) < 32 ? x << (s & 0xffu) : 0u"),               # x << 0 is x: the first arm goes
    (LSR, {}, "(s & 0xffu) < 32 ? x >> (s & 0xffu) : 0u"),
    (ASR, {}, "(u32)((s32)x >> ((s & 0xffu) < 32 ? s & 0xffu : 31))"),
    (LSL, BELOW_8, "x << s"),                                               # an amount below 32: the shift
    (LSR, BELOW_8, "x >> s"),
    (ASR, BELOW_8, "(u32)((s32)x >> s)"),
    (LSL, BELOW_256, "s < 32 ? x << s : 0u"),                              # below 256: its low byte is itself
    (LSL.replace("(s & 0xffu)", "((y & 7u) & 0xffu)"), {}, "x << (y & 7u)"),  # the amount's own bits
    (ROR, {}, "(s & 0x1fu) == 0 ? x : (x >> (s & 0x1fu)) | (x << (32 - (s & 0x1fu)))"),   # x << 32 if 0: kept
    (ROR, {"s": (0xffffffe0, 1)}, "(x >> s) | (x << (32 - s))"),           # an odd amount below 32: never 0
]


def folded_with(text, facts):
    c = cexpr.Ctx(types=TYPES, facts=facts)
    return cparse.expr_text(bitfields.simplified(expr(text), c, bitfields.Prover(c)))


TYPES["s"] = (32, False)


@needs_z3
@pytest.mark.parametrize("lifted,facts,wanted", SHIFTS)
def test_a_shift_by_a_register(lifted, facts, wanted):
    """The lifters' shift by a register (the amount's low byte, 32 and more giving 0, or the sign for asr):
    its first arm (the amount 0) always goes, the guard where the amount's known bits (the expression's
    or its variable's facts) keep it below 32; a rotation by 0 keeps its arm (x << 32 is undefined)."""
    assert folded_with(lifted, facts) == wanted, (lifted, facts, folded_with(lifted, facts))
    assert cexpr.prove(expr(lifted), expr(wanted), cexpr.Ctx(types=TYPES, facts=facts))


@needs_z3
def test_a_proof_assumes_the_facts_and_the_test_values_keep_them():
    """cexpr's facts about a variable: x << s stands for the guarded shift where s is known below 8, not
    without; the test values (vectors) keep the known bits, so the filter before the proof does not
    refuse what the facts make true."""
    guarded, plain = expr("(s & 0xffu) < 32 ? x << (s & 0xffu) : 0u"), expr("x << s")
    assert not cexpr.prove(guarded, plain, cexpr.Ctx(types=TYPES))
    assert not cexpr.equivalent(guarded, plain, cexpr.Ctx(types=TYPES))
    with_facts = cexpr.Ctx(types=TYPES, facts={"s": (0xfffffff8, 0x1)})
    assert cexpr.prove(guarded, plain, with_facts) and cexpr.equivalent(guarded, plain, with_facts)
    values = cexpr.vectors(["s", "x"], with_facts)
    assert values and all(v["s"] & 0xfffffff8 == 0 and v["s"] & 1 for v in values)
    assert not cexpr.prove(expr("s"), expr("s & 1u"), cexpr.Ctx(types=TYPES, facts={"s": (0xfffffff8, 0)}))


FACTS_FN = """extern void g(u32);
u32 f(u32 a0, u32 a1)
{
    u32 r0 = a0, r1 = a1, r2, r3, r4, r5, r6, r7 = 3, r8, r9;
    r2 = r1 & 7;  /* 0: and r2, r1, #7 */
    r3 = 0;  /* 4: mov r3, #0 */
L_top: ;
    r3 = r3 + 1;  /* 8: add r3, r3, #1 */
    r4 = r3 & 0x1fu;  /* c: and r4, r3, #0x1f */
    if (r3 != r0) goto L_top;  /* 10: bne */
    r5 = *(volatile u8 *)r0;  /* 14: ldrb r5, [r0] */
    if (r1 == 0) { r5 = 1; }  /* 18: moveq r5, #1 */
    if (r1 == 2) { r6 = 1; }  /* 1c: moveq r6, #1 */
    g((u32)&r7);  /* 20: bl g */
    __asm__ volatile ("mov %0, #1" : "=r"(r8));  /* 24: mov */
    r9 = r2 << 2;  /* 28: lsl r9, r2, #2 */
    r9 |= 1;  /* 2c: orr r9, r9, #1 */
    return r2 + r3 + r4 + r5 + r6 + r7 + r8 + r9;  /* 30: bx lr */
}
"""


def test_a_variables_facts_come_from_all_its_definitions():
    """variable_facts: r2 = r1 & 7 is below 8; a counter (r3 = 0, r3 + 1 in a loop) has no facts, a mask
    of it (r4 = r3 & 0x1f) has the mask's; a byte loaded or 1 is below 256; r9 = r2 << 2, then r9 |= 1
    (its definitions read it: a fixpoint) keeps the bits neither sets. Nothing for a parameter, a
    variable that may be read before anything writes it (r6), one whose address is taken (r7), an asm's
    output (r8)."""
    fn = cparse.parse(FACTS_FN).function("f")
    facts = bitfields.variable_facts(fn, tidy.context(fn), ["r%d" % k for k in range(10)] + ["a0", "a1"])
    assert facts == {"r2": (0xfffffff8, 0), "r4": (0xffffffe0, 0), "r5": (0xffffff00, 0), "r9": (0xffffffe2, 0)}, \
        {k: (hex(z), hex(o)) for k, (z, o) in facts.items()}


REG_SHIFT = {"lsl": "(%(s)s == 0 ? %(x)s : %(s)s < 32 ? %(x)s << %(s)s : 0u)",       # (dread.lift REG_SHIFT)
             "lsr": "(%(s)s == 0 ? %(x)s : %(s)s < 32 ? %(x)s >> %(s)s : 0u)",
             "asr": "(%(s)s == 0 ? %(x)s : (u32)((s32)%(x)s >> (%(s)s < 32 ? %(s)s : 31)))",
             "ror": "((%(s)s & 31) == 0 ? %(x)s : (%(x)s >> (%(s)s & 31)) | (%(x)s << (32 - (%(s)s & 31))))"}


def random_shift_function(rng, name, steps=10):
    """A function in the lifters' dialect that shifts by registers (lsl, lsr, asr, ror as the lifters
    write them, in an instruction's block) in a loop that runs three times: r4's amounts are masks, top
    bits and small constants (always below 32), r5's the same or a step of its own (a counter: nothing
    known), the other registers' anything (other shifts, sums, a parameter); some statements in
    conditional blocks."""
    regs = ["r0", "r1", "r2", "r3", "r12"]
    lines = ["u32 %s(u32 a0, u32 a1, u32 a2, u32 a3)" % name, "{",
             "    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r5 = 0, r7 = 3, r12 = a0 ^ a3;",
             "L_top: ;"]

    def bounded(d):
        src = rng.choice(regs)
        r = rng.random()
        if r < 0.5:
            return "%s = %s & 0x%xu;" % (d, src, rng.choice([1, 3, 7, 15, 31]))
        if r < 0.75:
            return "%s = %s >> %d;" % (d, src, rng.choice([27, 28, 30, 31]))
        return "%s = 0x%xu;" % (d, rng.randrange(0, 32))

    def amount():
        r = rng.random()
        if r < 0.4:
            return bounded("r4")
        if r < 0.6:
            return bounded("r5") if rng.random() < 0.6 else "r5 = r5 + 1;"
        d, src = rng.choice(regs), rng.choice(regs)
        return "%s = %s;" % (d, rng.choice(["%s & 0x%xu" % (src, rng.choice([7, 0xff, 0x1f0])), "%s + 1" % d,
                                             "%s ^ %s" % (src, rng.choice(regs)), "%s >> 24" % src]))

    def shift():
        d, x, s = rng.choice(regs), rng.choice(regs + ["r4"]), rng.choice(["r4", "r4", "r5"] + regs)
        value = REG_SHIFT[rng.choice(sorted(REG_SHIFT))] % {"x": "(x)", "s": "(a & 0xffu)"}
        return "{ u32 x = %s, a = %s; %s = %s; }" % (x, s, d, value)
    for k in range(steps):
        line = amount() if rng.random() < 0.45 else shift()
        if rng.random() < 0.2:
            line = "if (%s < %s) { %s }" % (rng.choice(regs), rng.choice(regs), line)
        lines.append("    %s  /* %x: i */" % (line, 4 * k))
    lines += ["    r7 = r7 - 1;  /* fe: subs */", "    if (r7 != 0) goto L_top;  /* ff: bne */",
              "    return r0 ^ (r1 << 1) ^ (r2 << 2) ^ (r3 << 3) ^ (r12 << 4) ^ (r4 << 5) ^ (r5 << 6);", "}"]
    return "\n".join(lines) + "\n"


@needs_z3
@pytest.mark.skipif(not shutil.which("cc"), reason="needs the host's C compiler (cc)")
def test_random_shifts_by_registers_cleaned_up_compute_the_same(tmp_path):
    """Random functions of shifts by registers (random_shift_function), cleaned up (every pass, the facts
    of their amounts among them), compiled with the host's C compiler beside the originals and called
    with random and boundary arguments: the same results every time (a shift by 32 or more where the
    facts were wrong is undefined in C, and x86 takes the amount modulo 32: it would show); every first
    arm gone but a rotation's, and the guards of the amounts known below 32 (r4's, mostly)."""
    rng = random.Random(29)
    count, originals, results = 60, [], []
    for i in range(count):
        text = random_shift_function(rng, "orig_%d" % i, steps=rng.randint(6, 14))
        originals.append(text)
        fn = cparse.parse(text.replace("orig_", "tidy_")).function("tidy_%d" % i)
        tidy.tidy(fn)
        results.append(cparse.unparse(fn, notes=False))
    head = PRELUDE + "typedef unsigned long long u64;\ntypedef signed long long s64;\n"
    (tmp_path / "shifts.c").write_text(head + "".join(originals) + "\n".join(results) + "\n")
    lib = tmp_path / "shifts.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-O0", "-w", "-o", str(lib), str(tmp_path / "shifts.c")], check=True)
    so = ctypes.CDLL(str(lib))
    edges = [0, 1, 7, 8, 31, 32, 33, 0xff, 0x100, 0x7fffffff, 0x80000000, 0xffffffe0, 0xffffffff]
    for i in range(count):
        a, b = getattr(so, "orig_%d" % i), getattr(so, "tidy_%d" % i)
        for f in (a, b):
            f.restype, f.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 4
        for _ in range(60):
            args = [rng.choice(edges + [rng.getrandbits(32), rng.getrandbits(6)]) for _ in range(4)]
            assert a(*args) == b(*args), (i, args, originals[i], results[i])
    rotations = sum(t.count("& 31) == 0 ?") for t in originals)     # (a rotation by 0 keeps its first arm)
    assert sum(t.count("== 0 ?") for t in results) <= rotations < sum(t.count("== 0 ?") for t in originals)
    before, after = sum(t.count("< 32 ?") for t in originals), sum(t.count("< 32 ?") for t in results)
    assert after < before * 0.7, (before, after)


@needs_z3
@pytest.mark.parametrize("variant", ["a32", "m3", "m4"])
def test_gizmo_shifts_by_its_index_as_the_source(variant):
    """gizmo's script_run (acc ^= p[i] << (i & 7)) printed ARM's shift by a register as r0_1 ^= (r2 &
    0xffu) == 0 ? r1_1 : (r2 & 0xffu) < 32 ? r1_1 << (r2 & 0xffu) : 0, r2 = r3_2 & 7 its amount: now
    the shift itself (r2's facts: below 8), and the amount folded in as the source writes it; gpio_set's
    amount (1u << pin) is a parameter nothing bounds: only the first arm goes."""
    from test_flags import gizmo_readable
    got = gizmo_readable(variant, "script_run", "gpio_set")
    run, gpio = got["script_run"], got["gpio_set"]
    assert re.search(r"\^= \*\(volatile u8 \*\)\(r0 \+ (\w+)\) << \(\1 & 7\);", run), run
    assert "< 32 ?" not in run and "== 0 ?" not in run, run
    assert "(r0 & 0xffu) < 32 ? 1u << (r0 & 0xffu) : 0;" in gpio and "== 0 ?" not in gpio, gpio


SHIFT_PROGRAM = """typedef unsigned int u32;
typedef unsigned char u8;
u32 shifts_masked(u32 x, u32 i) { return x << (i & 7); }
u32 shifts_right(u32 x, u32 i) { return x >> (i & 15); }
int shifts_arith(int x, u32 i) { return x >> (i & 31); }
u32 shifts_bit(u32 flags, u32 k) { u32 b = k & 3; return (flags >> b) & 1; }
u32 shifts_any(u32 x, u32 n) { return x << n; }
__attribute__((noinline)) u32 shifts_mix(const u8 *p, u32 n)
{
    u32 acc = 0;
    for (u32 i = 0; i < n; i++)
        acc ^= (u32)p[i] << (i & 7);
    return acc;
}
int main(void) { return 0; }
"""

# What each function returns in readable C (R the first argument's register, S the second's) on every
# build; before, ARM state and Thumb-2 printed ARM's shift by a register whole, (r1_1 & 0xffu) == 0 ? r0 :
# (r1_1 & 0xffu) < 32 ? r0 << (r1_1 & 0xffu) : 0 for shifts_masked behind r1_1 = r1 & 7
SHIFT_READ = {
    "shifts_masked": r"return R << \(S & 7\);",
    "shifts_right": r"return R >> \(S & 0xfu\);",
    "shifts_arith": r"return \(s32\)R >> \(S & (0x1fu|31)\);",
    "shifts_bit": r"return \(R >> \(S & 3\)\) & 1;",
}


@pytest.mark.slow
@needs_z3
@pytest.mark.parametrize("kind,opt", [pytest.param(k, o, marks=needs_build(k)) for k, o in BUILDS])
def test_shifts_by_registers_read_as_shifts_and_pass_the_gate(kind, opt, tmp_path):
    """SHIFT_PROGRAM with GCC for ARMv4T in ARM state (-Os, -O0), a Cortex-M3 and RV32IMAC (whose sll
    takes the amount's low five bits: the lifted C's x << (s & 31) was a shift already): every function
    lifts and the gate takes every one in its readable C; an amount the code masks below 32 reads as the
    plain shift on every build (SHIFT_READ; shifts_mix's i & 7 in a loop, through its variable's facts
    where the mask has a register of its own), and an amount nothing bounds keeps ARM's guard, its first
    arm gone."""
    from dread import build, lift, regflow
    from dread.program import Program
    from test_statics import kind_target
    target = kind_target(tmp_path, kind, SHIFT_PROGRAM, opt=opt)
    image = target.image()
    lifted = build.read_names(image.table("lifted"))
    assert set(SHIFT_READ) | {"shifts_any", "shifts_mix"} <= set(lifted)
    lines = []
    assert readable.check(target, image, out=lines.append) == sorted(lifted), lines
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    context = readable.Context(target, image, program, flow)
    lifter = lift.make_lifter(program, flow)
    regs = ("a0", "a1") if kind == "rv32" else ("r0", "r1")

    def text(name):
        return readable.transform(name, lifter.lift(program.by_name[name]).text, context=context)
    for name, wanted in SHIFT_READ.items():
        out = text(name)
        assert re.search(wanted.replace("R", regs[0]).replace("S", regs[1]), out), (name, out)
    mix, any_ = text("shifts_mix"), text("shifts_any")
    assert re.search(r"\^= .*<< (\w+|\(\w+ & 7\));", mix) and "< 32 ?" not in mix and "== 0 ?" not in mix, mix
    assert "== 0 ?" not in any_, any_
    if kind != "rv32":
        assert "(r1 & 0xffu) < 32 ? r0 << (r1 & 0xffu) : 0;" in any_, any_
