"""C expressions of dread.cparse evaluated in Python, with C's types: the integer promotions, the
usual arithmetic conversions, wrap-around of unsigned types, truncating division, arithmetic shifts
of signed values (GCC's), comparisons that give int. dread.tidy uses it to check that a simpler
expression it proposes computes what the lifted one does (equivalent): first on boundary values (the
expressions' constants and their neighbours, sums of two of them) and random ones, which rejects most
proposals at once, then by proof with z3 (prove: for every value of the variables where the lifted
expression is defined, the proposal is defined and has its value). Testing alone is no proof: the
range check (x & ~0x20) - 0x41 <= 0x19 passed for x == 0x41 on every value tried, none of them a
letter. Without z3 (the optional extra "prove") nothing is proved and equivalent says no.

What cannot be evaluated (a call, a memory access) is abstracted: each distinct such subexpression
becomes a variable of its own (abstract), so that both expressions see the same value for it.
Undefined behaviour (a shift by the width or more, a division by zero) raises Undefined; such a value
of the inputs is left out of the comparison when the lifted expression has it.
"""
import random

from dread import cparse as C


class Undefined(Exception):
    pass


class Ctx:
    """The target's integer widths (ARM and RISC-V: int 32 bits; MSP430: 16) and the variables' types."""

    def __init__(self, int_bits=32, long_bits=32, types=None):
        self.int_bits, self.long_bits = int_bits, long_bits
        self.types = dict(types or {})

    def ctype(self, text):
        """(bits, signed) of a type name, or None (a pointer, a struct)."""
        words = [w for w in text.replace("*", " * ").split() if w not in ("const", "volatile", "register", "static")]
        if "*" in words or not words:
            return None
        t = " ".join(words)
        fixed = {"u8": (8, False), "s8": (8, True), "u16": (16, False), "s16": (16, True), "u32": (32, False),
                 "s32": (32, True), "u64": (64, False), "s64": (64, True), "_Bool": (8, False)}
        if t in fixed:
            return fixed[t]
        signed = "unsigned" not in words
        if "char" in words:
            return (8, signed if "signed" in words else False)
        if "short" in words:
            return (16, signed)
        if words.count("long") >= 2:
            return (64, signed)
        if "long" in words:
            return (self.long_bits, signed)
        if "int" in words or "signed" in words or "unsigned" in words:
            return (self.int_bits, signed)
        return None


def wrap(value, ty):
    bits, signed = ty
    value &= (1 << bits) - 1
    if signed and value >> (bits - 1):
        value -= 1 << bits
    return value


def promote(ty, ctx):
    bits, signed = ty
    if bits < ctx.int_bits:
        return (ctx.int_bits, True)
    return ty


def common(a, b, ctx):
    a, b = promote(a, ctx), promote(b, ctx)
    if a == b:
        return a
    if a[1] == b[1]:
        return a if a[0] >= b[0] else b
    u, s = (a, b) if not a[1] else (b, a)
    if u[0] >= s[0]:
        return u
    return s


def num_type(text, value, ctx):
    t = text.lower()
    suffix = t[len(t.rstrip("ul")):] if not t.startswith("0x") else t[len(t.rstrip("ul")):]
    hexa = t.startswith("0x") or (t.startswith("0") and len(t) > 1 and t[1:2].isdigit())
    unsigned = "u" in suffix
    longs = suffix.count("l")
    order = []
    if longs == 0:
        order = [(ctx.int_bits, True), (ctx.int_bits, False), (ctx.long_bits, True), (ctx.long_bits, False),
                 (64, True), (64, False)]
    elif longs == 1:
        order = [(ctx.long_bits, True), (ctx.long_bits, False), (64, True), (64, False)]
    else:
        order = [(64, True), (64, False)]
    if unsigned:
        order = [t for t in order if not t[1]]
    elif not hexa:
        order = [t for t in order if t[1]] or order
    for bits, signed in order:
        if 0 <= value < (1 << (bits - 1 if signed else bits)):
            return (bits, signed)
    return (64, False)


def compile_expr(e, ctx):
    """A function env -> (value, (bits, signed)) for expression e; env maps variable names to ints."""
    if isinstance(e, C.Name):
        ty = ctx.types.get(e.id)
        if ty is None:
            raise Undefined("no type for %s" % e.id)
        name = e.id
        return lambda env: (wrap(env[name], ty), ty)
    if isinstance(e, C.Num):
        if isinstance(e.value, float):
            raise Undefined("a float")
        ty = num_type(e.text, e.value, ctx)
        v = wrap(e.value, ty)
        return lambda env: (v, ty)
    if isinstance(e, C.Cast):
        ty = ctx.ctype(e.type)
        if ty is None:
            raise Undefined("a cast to %s" % e.type)
        inner = compile_expr(e.e, ctx)
        return lambda env: (wrap(inner(env)[0], ty), ty)
    if isinstance(e, C.Unary):
        inner = compile_expr(e.e, ctx)
        if e.op == "-":
            def neg(env):
                v, ty = inner(env)
                ty = promote(ty, ctx)
                return wrap(-v, ty), ty
            return neg
        if e.op == "+":
            def pos(env):
                v, ty = inner(env)
                ty = promote(ty, ctx)
                return wrap(v, ty), ty
            return pos
        if e.op == "~":
            def inv(env):
                v, ty = inner(env)
                ty = promote(ty, ctx)
                return wrap(~v, ty), ty
            return inv
        if e.op == "!":
            it = (ctx.int_bits, True)
            return lambda env: (0 if inner(env)[0] else 1, it)
        raise Undefined("unary %s" % e.op)
    if isinstance(e, C.Binary):
        left, right = compile_expr(e.l, ctx), compile_expr(e.r, ctx)
        op = e.op
        it = (ctx.int_bits, True)
        if op == "&&":
            return lambda env: (1 if left(env)[0] and right(env)[0] else 0, it)
        if op == "||":
            return lambda env: (1 if left(env)[0] or right(env)[0] else 0, it)
        if op in ("<<", ">>"):
            def shift(env):
                a, ta = left(env)
                b, _ = right(env)
                ta = promote(ta, ctx)
                if b < 0 or b >= ta[0]:
                    raise Undefined("a shift by %d" % b)
                a = wrap(a, ta)
                if op == "<<":
                    if ta[1] and a < 0:
                        raise Undefined("a left shift of a negative value")
                    return wrap(a << b, ta), ta
                return wrap(a >> b, ta), ta       # (Python's >> is arithmetic on negative values: GCC's)
            return shift

        def arith(env):
            a, ta = left(env)
            b, tb = right(env)
            ty = common(ta, tb, ctx)
            a, b = wrap(a, ty), wrap(b, ty)
            if op == "+":
                return wrap(a + b, ty), ty
            if op == "-":
                return wrap(a - b, ty), ty
            if op == "*":
                return wrap(a * b, ty), ty
            if op in ("/", "%"):
                if b == 0:
                    raise Undefined("a division by zero")
                q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
                return (wrap(q, ty) if op == "/" else wrap(a - q * b, ty)), ty
            if op == "&":
                return wrap(a & b, ty), ty
            if op == "|":
                return wrap(a | b, ty), ty
            if op == "^":
                return wrap(a ^ b, ty), ty
            r = {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
            return (1 if r else 0), it
        return arith
    if isinstance(e, C.Cond):
        c, t, f = compile_expr(e.c, ctx), compile_expr(e.t, ctx), compile_expr(e.f, ctx)

        def cond(env):
            a, ta = t(env)
            b, tb = f(env)
            ty = common(ta, tb, ctx)
            return (wrap(a, ty) if c(env)[0] else wrap(b, ty)), ty
        # (both arms evaluated for their types: an arm with undefined behaviour raises even when not
        # taken; such inputs are left out, which only loses test values)
        return cond
    raise Undefined("cannot evaluate %s" % type(e).__name__)


def abstract(exprs, ctx, prefix="__v", mapping=None, into=None):
    """The expressions with each distinct subexpression that cannot be evaluated (a call, a memory
    access) replaced by a fresh variable typed as its cast or as unsigned int; returns (exprs, ctx with
    those variables; ctx.abstracted: {its text: the variable}). mapping and into: those of an earlier
    call, so that a second expression gets the same variables for the same subexpressions."""
    seen = {} if mapping is None else mapping
    ctx2 = into if into is not None else Ctx(ctx.int_bits, ctx.long_bits, ctx.types)
    ctx2.abstracted = seen

    def key(x):
        return C.expr_text(x)

    def own_type(x):
        """The C type of what cannot be evaluated: a load's pointee type, a call's (the lifters declare
        their callees to return a register: unsigned int's width), None for the rest."""
        if isinstance(x, C.Unary) and x.op == "*" and isinstance(x.e, C.Cast):
            words = x.e.type.split()
            if words.count("*") == 1 and words[-1] == "*":
                return ctx.ctype(" ".join(w for w in words[:-1] if w not in ("volatile", "const")))
            return None
        if isinstance(x, C.Call):
            return (ctx.int_bits, False)
        return None

    def rewrite(x):
        if isinstance(x, (C.Call, C.Index, C.Member)) or (isinstance(x, C.Unary) and x.op in ("*", "&")):
            ty = own_type(x)
            if ty is None:              # (an address, a member: no value to stand in for)
                raise Undefined("cannot abstract %s" % key(x))
            k = key(x)
            if k not in seen:
                seen[k] = "%s%d" % (prefix, len(seen))
                ctx2.types[seen[k]] = ty
            return C.Name(seen[k])
        if isinstance(x, C.Cast):
            return C.Cast(x.type, rewrite(x.e))
        if isinstance(x, C.Unary):
            return C.Unary(x.op, rewrite(x.e))
        if isinstance(x, C.Binary):
            return C.Binary(x.op, rewrite(x.l), rewrite(x.r))
        if isinstance(x, C.Cond):
            return C.Cond(rewrite(x.c), rewrite(x.t), rewrite(x.f))
        return x
    return [rewrite(x) for x in exprs], ctx2


EDGES = (0, 1, 2, 3, 0x7f, 0x80, 0xff, 0x100, 0x7fff, 0x8000, 0xffff, 0x10000, 0x7fffffff, 0x80000000,
         0x80000001, 0xfffffffe, 0xffffffff, 0x100000000, 0x7fffffffffffffff, 0x8000000000000000,
         0xffffffffffffffff)


def interesting(exprs):
    """Values the expressions' constants make boundaries of: each constant, its neighbours, the sum and
    difference of two of them and their neighbours, a constant with another's bits set or cleared."""
    from dread.cflow import walk_expr
    ks = sorted({x.value for e in exprs for x in walk_expr(e) if isinstance(x, C.Num) and isinstance(x.value, int)})
    out = set()
    for a in ks:
        out |= {a - 1, a, a + 1, -a}
        for b in ks:
            out |= {a + b - 1, a + b, a + b + 1, a - b - 1, a - b, a - b + 1, a | b, a & ~b}
    return sorted(out)


def vectors(names, ctx, count=400, seed=7, extra=()):
    """Test values for the variables: every pair of boundary values for two variables (with equal and
    neighbouring values), boundary values one at a time for more, and random values. extra: further
    boundary values (interesting())."""
    rng = random.Random(seed)
    widths = {n: ctx.types[n][0] for n in names}
    edges = {n: sorted({e & ((1 << widths[n]) - 1) for e in tuple(EDGES) + tuple(extra)}) for n in names}
    out = []
    names = list(names)
    if len(names) == 1:
        out += [{names[0]: v} for v in edges[names[0]]]
    elif len(names) == 2:
        a, b = names
        out += [{a: x, b: y} for x in edges[a] for y in edges[b]]
        for x in edges[a]:
            m = (1 << widths[b]) - 1
            out += [{a: x, b: x & m}, {a: x, b: (x + 1) & m}, {a: x, b: (x - 1) & m}]
    else:
        base = {n: 0 for n in names}
        for n in names:
            for v in edges[n]:
                out.append(dict(base, **{n: v}))
    for _ in range(count):
        env = {}
        for n in names:
            r = rng.random()
            w = widths[n]
            if r < 0.3:
                env[n] = rng.choice(edges[n])
            elif r < 0.5:
                env[n] = rng.randrange(0, 64)
            else:
                env[n] = rng.getrandbits(w)
        out.append(env)
    if len(names) == 2:                     # equal values, the case a compare is about
        a, b = names
        m = (1 << min(widths[a], widths[b])) - 1
        for _ in range(count // 4):
            v = rng.getrandbits(widths[a]) & m
            out.append({a: v, b: v})
    return out


def tested(e1, e2, ctx, as_bool=False, count=400):
    """Whether two expressions compute the same value (as_bool: the same truth) for the test values of
    their variables; values for which e1 is undefined are left out. A filter, no proof."""
    from dread.cflow import walk_expr
    try:
        (a1, a2), ctx2 = abstract([e1, e2], ctx)
    except Undefined:
        return False
    names = sorted({x.id for e in (a1, a2) for x in walk_expr(e) if isinstance(x, C.Name)})
    if any(n not in ctx2.types for n in names):
        return False
    try:
        f1, f2 = compile_expr(a1, ctx2), compile_expr(a2, ctx2)
    except Undefined:
        return False
    tried = 0
    for env in vectors(names, ctx2, count, extra=interesting([a1, a2])):
        try:
            v1 = f1(env)[0]
        except Undefined:
            continue
        try:
            v2 = f2(env)[0]
        except Undefined:
            return False
        if (bool(v1) != bool(v2)) if as_bool else (v1 != v2):
            return False
        tried += 1
    return tried > 0


class Checker:
    """tested for many proposals against one expression: the expression evaluated once on its test
    values, each proposal dropped at its first disagreement (most at the first few values)."""

    def __init__(self, e1, ctx, as_bool=False, count=400):
        from dread.cflow import walk_expr
        self.e1, self.ctx0, self.as_bool, self.ok = e1, ctx, as_bool, False
        try:
            (a1,), self.ctx = abstract([e1], ctx)
        except Undefined:
            return
        self.mapping = self.ctx.abstracted
        self.names = sorted({x.id for x in walk_expr(a1) if isinstance(x, C.Name)})
        if any(n not in self.ctx.types for n in self.names):
            return
        try:
            f1 = compile_expr(a1, self.ctx)
        except Undefined:
            return
        self.values = []
        for env in vectors(self.names, self.ctx, count, extra=interesting([a1])):
            try:
                self.values.append((env, f1(env)[0]))
            except Undefined:
                continue
        self.ok = bool(self.values)

    def agrees(self, e2):
        """Whether e2 gives e1's values (as_bool: truths) on every test value (no proof)."""
        from dread.cflow import walk_expr
        if not self.ok:
            return False
        try:
            (a2,), _ = abstract([e2], self.ctx0, mapping=self.mapping, into=self.ctx)
        except Undefined:
            return False
        if any(isinstance(x, C.Name) and x.id not in self.ctx.types for x in walk_expr(a2)):
            return False
        try:
            f2 = compile_expr(a2, self.ctx)
        except Undefined:
            return False
        for env, v1 in self.values:
            try:
                v2 = f2(env)[0]
            except (Undefined, KeyError):
                return False
            if (bool(v1) != bool(v2)) if self.as_bool else (v1 != v2):
                return False
        return True

    def stands_for(self, e2):
        """agrees, then proved (prove)."""
        return self.agrees(e2) and prove(self.e1, e2, self.ctx0, self.as_bool)


def equivalent(e1, e2, ctx, as_bool=False):
    """Whether e2 may stand for e1: the same value (as_bool: the same truth) wherever e1 is defined,
    tested first (tested) and then proved (prove)."""
    return tested(e1, e2, ctx, as_bool) and prove(e1, e2, ctx, as_bool)


try:
    import z3
except ImportError:                         # the optional extra "prove"
    z3 = None

PROOF_MS = 3000         # z3's time for one proof; unknown when out of time counts as no


def prove(e1, e2, ctx, as_bool=False):
    """True when z3 proves that for every value of the variables where e1 is defined, e2 is defined
    and has e1's value (as_bool: its truth); False when it finds values where not, runs out of time,
    or is not installed."""
    if z3 is None:
        return False
    from dread.cflow import walk_expr
    try:
        (a1, a2), ctx2 = abstract([e1, e2], ctx)
    except Undefined:
        return False
    names = sorted({x.id for e in (a1, a2) for x in walk_expr(e) if isinstance(x, C.Name)})
    if any(n not in ctx2.types for n in names):
        return False
    variables = {n: z3.BitVec(n, ctx2.types[n][0]) for n in names}
    try:
        v1, t1, d1 = to_z3(a1, ctx2, variables)
        v2, t2, d2 = to_z3(a2, ctx2, variables)
    except Undefined:
        return False
    if as_bool:
        same = (v1 != 0) == (v2 != 0)
    else:
        bits = max(t1[0], t2[0])
        same = _resize(v1, t1, bits) == _resize(v2, t2, bits)
    solver = z3.Solver()
    solver.set("timeout", PROOF_MS)
    solver.add(d1, z3.Or(z3.Not(d2), z3.Not(same)))
    return solver.check() == z3.unsat


def _resize(v, ty, bits):
    if ty[0] == bits:
        return v
    if ty[0] > bits:
        return z3.Extract(bits - 1, 0, v)
    return z3.SignExt(bits - ty[0], v) if ty[1] else z3.ZeroExt(bits - ty[0], v)


def to_z3(e, ctx, variables):
    """(z3 bit vector, (bits, signed), z3 condition under which e is defined) for expression e."""
    T = z3.BoolVal(True)
    it = (ctx.int_bits, True)

    def conv(v, ty, to):
        return _resize(v, ty, to[0]), to

    def prom(v, ty):
        return conv(v, ty, promote(ty, ctx))

    def boolint(b):
        return z3.If(b, z3.BitVecVal(1, ctx.int_bits), z3.BitVecVal(0, ctx.int_bits)), it

    if isinstance(e, C.Name):
        return variables[e.id], ctx.types[e.id], T
    if isinstance(e, C.Num):
        if isinstance(e.value, float):
            raise Undefined("a float")
        ty = num_type(e.text, e.value, ctx)
        return z3.BitVecVal(e.value & ((1 << ty[0]) - 1), ty[0]), ty, T
    if isinstance(e, C.Cast):
        ty = ctx.ctype(e.type)
        if ty is None:
            raise Undefined("a cast to %s" % e.type)
        v, t, d = to_z3(e.e, ctx, variables)
        return _resize(v, t, ty[0]), ty, d
    if isinstance(e, C.Unary):
        v, t, d = to_z3(e.e, ctx, variables)
        if e.op == "!":
            r, ty = boolint(v == 0)
            return r, ty, d
        v, t = prom(v, t)
        if e.op == "-":
            return -v, t, d
        if e.op == "+":
            return v, t, d
        if e.op == "~":
            return ~v, t, d
        raise Undefined("unary %s" % e.op)
    if isinstance(e, C.Cond):
        c, tc, dc = to_z3(e.c, ctx, variables)
        a, ta, da = to_z3(e.t, ctx, variables)
        b, tb, db = to_z3(e.f, ctx, variables)
        ty = common(ta, tb, ctx)
        a, b = _resize(a, ta, ty[0]), _resize(b, tb, ty[0])
        return z3.If(c != 0, a, b), ty, z3.And(dc, z3.Implies(c != 0, da), z3.Implies(c == 0, db))
    if isinstance(e, C.Binary):
        a, ta, da = to_z3(e.l, ctx, variables)
        b, tb, db = to_z3(e.r, ctx, variables)
        op = e.op
        if op == "&&":
            r, ty = boolint(z3.And(a != 0, b != 0))
            return r, ty, z3.And(da, z3.Implies(a != 0, db))
        if op == "||":
            r, ty = boolint(z3.Or(a != 0, b != 0))
            return r, ty, z3.And(da, z3.Implies(a == 0, db))
        d = z3.And(da, db)
        if op in ("<<", ">>"):
            a, ta = prom(a, ta)
            width = ta[0]
            count = _resize(b, tb, max(tb[0], width))
            ok = z3.ULT(count, z3.BitVecVal(width, count.size()))
            if tb[1]:
                ok = z3.And(ok, count >= 0)
            count = _resize(count, (count.size(), False), width)
            if op == "<<":
                if ta[1]:
                    ok = z3.And(ok, a >= 0)
                return a << count, ta, z3.And(d, ok)
            return (a >> count if ta[1] else z3.LShR(a, count)), ta, z3.And(d, ok)
        ty = common(ta, tb, ctx)
        a, b = _resize(a, ta, ty[0]), _resize(b, tb, ty[0])
        signed = ty[1]
        if op == "+":
            return a + b, ty, d
        if op == "-":
            return a - b, ty, d
        if op == "*":
            return a * b, ty, d
        if op in ("/", "%"):
            nonzero = b != 0
            if signed:
                low = z3.BitVecVal(1 << (ty[0] - 1), ty[0])
                nonzero = z3.And(nonzero, z3.Not(z3.And(a == low, b == -1)))
                return (a / b if op == "/" else z3.SRem(a, b)), ty, z3.And(d, nonzero)
            return (z3.UDiv(a, b) if op == "/" else z3.URem(a, b)), ty, z3.And(d, nonzero)
        if op == "&":
            return a & b, ty, d
        if op == "|":
            return a | b, ty, d
        if op == "^":
            return a ^ b, ty, d
        cmp = {"==": lambda: a == b, "!=": lambda: a != b,
               "<": lambda: a < b if signed else z3.ULT(a, b), "<=": lambda: a <= b if signed else z3.ULE(a, b),
               ">": lambda: a > b if signed else z3.UGT(a, b), ">=": lambda: a >= b if signed else z3.UGE(a, b)}
        if op in cmp:
            r, t = boolint(cmp[op]())
            return r, t, d
        raise Undefined("binary %s" % op)
    raise Undefined("cannot translate %s" % type(e).__name__)
