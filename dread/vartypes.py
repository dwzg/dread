"""dread.vartypes: readable C's variables, parameters and prototypes typed by their use: a pointer to the
width a variable is dereferenced at, a structure pointer where a callee takes one, a signed integer
where its comparisons are signed; a file's externs with the types the callees' own readable C has
where the file's calls fit them.

The lifters declare every register u32, and readable C kept that: a parameter that is only
dereferenced and advanced read `*(volatile u8 *)(r1_1 + 1)` at every use, an argument for a callee
the extern declares all-u32 `(u32)&g_x[0]`, a counter compared signed `(s32)r2_1 > 4`. Here:

    u32 strncpy(volatile u8 *r0, const volatile u8 *r1, u32 r2)
    {
        const volatile u8 *r1_1;
        volatile u8 *r2_1, *r3;
        ...
        r12 = r1_1[1]; r1_1++;
        ...
        *r3 = r12; r3++;

What decides (plan, over every function the check reads, before any is checked; the readable_types
table's `variables` and `prototypes`):

- pointer groups: variables that are copies of one another or one plus or minus a constant or an
  index (a = b, a = b + 4, a = b + r2) hold addresses into one object and get one type. A variable is
  a pointer where the address of a memory access is it plus constants (*(volatile u8 *)(r1 + 1)), or
  plus a variable that is no pointer (an index: one given a small constant, or scaled; between two
  candidates a parameter dread.shapes says the function uses as a pointer); the type is the pointer to
  the width the group's accesses have (all at one width: `volatile u16 *`; a byte read with both signs
  is u8, a signed one cast), `const` where nothing is stored through the group, s8 or s16 where every
  access is a signed load. A variable the function does not dereference but hands to callees that take
  one pointer type (their own readable C's parameter, a structure pointer of dread.shapes among them)
  gets that type with those parameters in the file's externs;
- signed integers: a variable compared in order only by signed comparisons ((s32)v > 4) is s32, a
  function's result s32 where a caller compares it signed (uart_getc's int: (s32)f() compared, or a
  variable every definition of which that reaches the comparison is the call), also a callee the plan
  does not analyse (not lifted: its extern, and its own C printed on request, gizmo a32's ring_get
  beside main);
- negative values: a variable given a constant dread.polish prints as a negative number (ret = -1)
  and read only by == or !=, a truth test or return is s32 (negative_variables; one it returns only in a
  function whose result is s32, which a u32 function's variable would contradict), and a function's
  result s32 where its own returns give negative values (return -1, return c ? 0 : -1, or such a
  variable) and a caller reads it, every caller only so (by ==, return, a signed comparison, or a
  variable read only so: the reads the call's definition reaches, dread.cflow); without a caller that
  reads it a negative return is no evidence (gizmo's udiv_var returns 0xFFFFFFFFu beside an unsigned
  quotient). A function the plan does not analyse gets the variables on its own C (local_types);
- a file's externs: a callee's parameter has the type of the callee's own readable C where the file's
  calls pass what C takes for it without a cast (a pointer of that type, the address of an object of
  it, 0), and a byte pointer (u8) is a void pointer where they pass pointers of other types (the
  address of a u32 local for a queue's item: void_param, since 2026-10-26); elsewhere it stays u32 as
  the lifter declared it (an integer, a queue handle a u32 global
  holds, the address of a text: a cast at every call, which measured more casts than the types took
  away: docs/decisions.md). A callee's result type is its own everywhere, and a function that calls
  itself declares itself as its head (C takes no other);
- pointer results: a function every return of which gives one pointer type (a variable typed or
  declared so, a call of a callee whose result is one, 0) returns that type where its returns and its
  callers' C hold no more casts so (`return (u32)r0;` goes; a caller that uses the result as a number
  casts it); memcpy's and strncpy's destination, FreeRTOS's queue and task handles (decide_pointer_result,
  since 2026-10-26; the head's result may be several words: head_match, head_text);
- what to take is decided by trying it: each candidate is kept where the function's C, rewritten with
  it, has no more casts than without (a pointer loaded from memory and dereferenced once moves its cast
  to where it is loaded: kept; one handed to five callees that take integers: not). The trials run on
  the C as the build will have it (finished: the static data's objects and dread.shapes' structures
  applied), callees first and in rounds (ROUNDS: a parameter typed in one round lets its callers type
  what they hand it in the next).

How the C keeps its behaviour (Rewriter, the same in the plan's trials and in apply): every expression
of the lifted C has its integer type and value; the rewritten one has the same value where it is an
integer, the same address where it is a pointer:

- pointer arithmetic scales by the pointee: a byte offset k on a `volatile u16 *` is k / 2 elements
  (r5 + 4 on a u32 pointer is r5 + 1, an index shifted by 2 is the index), and a structure pointer's
  advance by its size is + 1; an offset that is not a whole number of elements keeps the integer
  arithmetic, cast ((volatile u16 *)((u32)p + 1));
- an access through a typed pointer is p[k] or *p, of its width (a load of the other sign cast);
- a pointer used as a number (stored as a word, passed for a u32 parameter, returned as the u32
  result, in arithmetic, compared with a number) is (u32)p; two pointers of one type, or a pointer and
  0, compare as pointers (unsigned, as the lifted C's u32 did); a byte pointer's difference is p - q
  where only its low 32 bits count;
- an integer becomes a pointer by a cast where it is assigned to one or passed for a pointer parameter
  (0 as it is); a pointer of another type by a pointer cast;
- an s32 variable reads the same 32 bits wherever only they count (assigned, stored, passed, returned,
  compared for equality, in a sum or a mask whose result is used so, a cast to 32 bits or narrower);
  elsewhere (an ordered comparison the lifted C made unsigned, a right shift, a division, a 64-bit
  conversion) it is (u32)v, and (s32)v is v;
- a chain of assignments that assigns a typed variable (r1 = r2 = r3 = r12 = 0 where dread.tidy's split
  left it) is one assignment per variable, innermost first (dread.tidy split_typed: r12 = 0; r3 = 0;
  r2 = 0; r1 = 0): the value of an assignment to a pointer is a pointer, and the chain would hand it
  on to an integer, or need (u32)(r2 = ...) at every link.

The C stays volatile: a typed pointer is `volatile T *`, so every access through it is the one access
it was (the open Memory item: whether accesses to RAM need volatile is not decided here). Files with
typed pointers or signed variables are compiled with -fno-delete-null-pointer-checks (dread.build
NULL_CHECKS, as dread.shapes' structures) and -fno-strict-overflow (WRAPS: signed and pointer
arithmetic wrap as the lifted u32 arithmetic did; -fwrapv and -fwrapv-pointer for GCC 8 and later and
for clang 23; an older clang's -fwrapv-pointer is not confirmed).
The gate (dread readable --check) checks every function's behaviour as for every other stage.

Not done: an access past a
structure through its pointer (raw, as dread.shapes leaves it: r0[2].f_0 would need the field's
name), pointers into the stack frame (dread.frames' object), variables the lifter declares other than
u32 (MSP430's u16 registers: its pointers are 16 bits), globals that hold pointers (a queue handle in
a u32 global stays a number, so its calls keep the extern's u32), names (the pilot's). Not
established: that a negative value read only by == is the source's int (a u32 sentinel compared with
0xFFFFFFFFu reads the same), and the result of a function only called through pointers (no caller
reads it: it keeps u32).
"""
import re

from dread import cparse as C

SIZES = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "u64": 8, "s64": 8}
INTS = {"u8": (8, False), "s8": (8, True), "u16": (16, False), "s16": (16, True), "u32": (32, False),
        "s32": (32, True), "u64": (64, False), "s64": (64, True)}
ACCESS = re.compile(r"^volatile ([us](?:8|16|32|64)) \*$")
VOID = re.compile(r"^(const )?volatile void \*$")     # an extern's void pointer parameter (void_param)
ROUNDS = 6              # rounds of the plan over the functions (callees' prototypes spread up to callers)
SMALL = 0x100           # a variable given a constant below this is an index, not a pointer
FRAME = ("sp0", "frame")


# --- types as text ---

def pointer_text(pointee, const=False):
    """The C type of a pointer to pointee ("u8", "struct s_3"): volatile, const where asked."""
    return "%svolatile %s *" % ("const " if const else "", pointee)


def parse_pointer(text):
    """(pointee, const) of a pointer type written by pointer_text, or None."""
    m = re.match(r"^(const )?volatile (\w+|struct \w+) \*$", " ".join((text or "").split()))
    return (m.group(2), bool(m.group(1))) if m else None


def int_text(t):
    """u32 for (32, False) and so on."""
    for name, ty in INTS.items():
        if ty == t:
            return name
    return None


def signed32(k):
    k &= 0xffffffff
    return k - 0x100000000 if k & 0x80000000 else k


def _num(k):
    return C.Num("%d" % k) if k >= 0 else C.Unary("-", C.Num("%d" % -k))


def _copy(e):
    """A copy of an expression tree (renaming later changes names in place: no node is shared)."""
    if isinstance(e, C.Name):
        return C.Name(e.id)
    if isinstance(e, C.Num):
        return C.Num(e.text, value=e.value, note=e.note)
    if isinstance(e, C.Str):
        return C.Str(list(e.parts))
    if isinstance(e, (C.Unary, C.Post)):
        return type(e)(e.op, _copy(e.e))
    if isinstance(e, C.Cast):
        return C.Cast(e.type, _copy(e.e))
    if isinstance(e, C.Binary):
        return C.Binary(e.op, _copy(e.l), _copy(e.r))
    if isinstance(e, C.Cond):
        return C.Cond(_copy(e.c), _copy(e.t), _copy(e.f))
    if isinstance(e, C.Assign):
        return C.Assign(e.op, _copy(e.target), _copy(e.value))
    if isinstance(e, C.Call):
        return C.Call(_copy(e.f), [_copy(a) for a in e.args])
    if isinstance(e, C.Index):
        return C.Index(_copy(e.base), _copy(e.index))
    if isinstance(e, C.Member):
        return C.Member(_copy(e.base), e.name, e.arrow)
    if isinstance(e, C.Comma):
        return C.Comma([_copy(x) for x in e.items])
    import copy
    return copy.deepcopy(e)


def constant(e):
    """The value of a constant expression of the lifted C (a literal, its negation or complement, a cast
    of one, a sum of them) as an integer, or None."""
    if isinstance(e, C.Num) and isinstance(e.value, int):
        return e.value
    if isinstance(e, C.Unary) and e.op in ("-", "~", "+"):
        v = constant(e.e)
        if v is None:
            return None
        return -v if e.op == "-" else ~v if e.op == "~" else v
    if isinstance(e, C.Cast) and e.type in INTS:
        v = constant(e.e)
        if v is None:
            return None
        bits, signed = INTS[e.type]
        v &= (1 << bits) - 1
        return v - (1 << bits) if signed and v >> (bits - 1) else v
    if isinstance(e, C.Binary) and e.op in ("+", "-"):
        a, b = constant(e.l), constant(e.r)
        if a is None or b is None:
            return None
        return a + b if e.op == "+" else a - b
    return None


def is_zero(e):
    v = constant(e)
    return v is not None and v & 0xffffffff == 0 and not isinstance(e, C.Cast)


# --- the rewriter ---

class Rewriter:
    """Rewrites one function's statements for a typing ({variable: type text}: pointer_text's or
    "s32"), keeping what every expression computes (the module's docstring). types: a dread.polish
    Types of the file as it is (its declarations, the structures dread.shapes gave and their pointer
    variables, the macros of memory at fixed addresses, the externs); protos: {callee: (result text or
    None, [parameter text or None])} of the callees' types (None: as the extern has it); result: the
    function's own result type text, None as declared; sizes: {"struct NAME": its size} for structure
    pointers' arithmetic. The Types is updated in place to the new declarations (new); old keeps a copy
    of what the lifted C declared."""

    def __init__(self, fn, typing, types, protos=None, result=None, sizes=None):
        import copy
        from dread.naming import variables
        self.fn = fn
        self.own = set(variables(fn))      # (the function's own variables: their addresses stay integers)
        self.typing = dict(typing)
        self.old = types
        self.new = copy.copy(types)
        self.new.vars = dict(types.vars)
        self.new.functions = dict(types.functions)
        self.const = set()                  # variables declared pointers to const
        for v, t in self.typing.items():
            self.new.vars[v] = self.new.parse(t)
            p = parse_pointer(t)
            if p and p[1]:
                self.const.add(v)
        for f, (res, params) in (protos or {}).items():
            old = self.new.functions.get(f)
            if old is None:
                continue
            old_res, old_params = old
            res_d = self.new.parse(res) if res else old_res
            if old_params is not None and params:
                ps = [self.new.parse(p) if p else o for p, o in zip(params, old_params)] + \
                    list(old_params[len(params):])
            else:
                ps = old_params
            self.new.functions[f] = (res_d, ps)
        self.param_texts = {f: list(params or []) for f, (_, params) in (protos or {}).items()}
        self.result = self.new.parse(result) if result else self.new.result
        self.new.result = self.result
        # (a structure's size counts only where the file defines it: a pointer to one it only declares,
        # struct s_24;, cannot be advanced in C)
        self.sizes = {k: z for k, z in (sizes or {}).items() if k in types.structs}
        self.arithmetic = False             # (whether it wrote pointer arithmetic: the file's WRAPS line)

    # -- types --

    def local(self, x):
        """Whether an lvalue is (a member or an element of) a variable of the function: its address is
        no pointer readable C may take (a pointer to a local tells the compiler what it may reach)."""
        while isinstance(x, (C.Member, C.Index)) and not (isinstance(x, C.Member) and x.arrow):
            x = x.base
        return isinstance(x, C.Name) and (x.id in self.own or x.id in FRAME)

    def ptr(self, e):
        """(pointee descriptor, const) of a rewritten expression of pointer type, else None."""
        d = self.new.of(e)
        if not d or d[0] not in ("ptr", "array") or d[1] is None:
            return None
        return d[1], self._const(e)

    def _const(self, e):
        while isinstance(e, C.Binary) and e.op in ("+", "-"):
            e = e.l if self.new.of(e.l) and self.new.of(e.l)[0] == "ptr" else e.r
        return isinstance(e, C.Name) and e.id in self.const

    def elem(self, d):
        """The size of a pointee descriptor, or None."""
        if d is None:
            return None
        if d[0] == "int":
            return d[1][0] // 8
        if d[0] == "struct":
            return self.sizes.get(d[1])
        return None

    def text(self, d, const=False):
        """The pointer type text for pointee descriptor d."""
        if d[0] == "int":
            return pointer_text(int_text(d[1]), const)
        return pointer_text(d[1], const)

    # -- pointers --

    def index(self, e, w):
        """The element count of a byte offset e (an integer of the lifted C) for elements of w bytes, as
        a rewritten expression, or None where it is not a whole number of elements by its form."""
        k = constant(e)
        if k is not None:
            k = signed32(k)
            return _num(k // w) if k % w == 0 else None
        if w == 1:
            return self.value(e, "bits")
        if isinstance(e, C.Binary) and e.op == "<<":
            s = constant(e.r)
            if s is not None and 0 <= s < 32 and (1 << s) % w == 0:
                left = self.value(e.l, "exact")
                shift = s - (w.bit_length() - 1)
                return left if shift == 0 else C.Binary("<<", left, C.Num("%d" % shift))
            return None
        if isinstance(e, C.Binary) and e.op == "*":
            for x, m in ((e.l, e.r), (e.r, e.l)):
                k = constant(m)
                if k is not None and k > 0 and k % w == 0:
                    x2 = self.value(x, "bits")
                    return x2 if k == w else C.Binary("*", x2, C.Num("%d" % (k // w)))
            return None
        if isinstance(e, C.Binary) and e.op in ("+", "-"):
            a, b = self.index(e.l, w), self.index(e.r, w)
            if a is None or b is None:
                return None
            return C.Binary(e.op, a, b)
        return None

    def natural(self, e):
        """e (an integer of the lifted C that is an address) written as a pointer the way C writes one:
        a variable of pointer type plus or minus elements, the address of an object; None where it is
        no such thing."""
        if isinstance(e, C.Name):
            d = self.new.of(e)
            if d and d[0] == "ptr" and d[1] is not None:
                return C.Name(e.id)
            return None
        if isinstance(e, (C.Member, C.Index)) or (isinstance(e, C.Unary) and e.op == "*" and
                                                  not isinstance(e.e, C.Cast)):
            d = self.new.of(e)              # (a pointer field of a structure: r3->f_28)
            if d and d[0] == "ptr" and d[1] is not None:
                return self.lvalue(e)
            return None
        if isinstance(e, C.Call):
            d = self.new.of(e)              # (a callee whose result is a pointer)
            if d and d[0] == "ptr" and d[1] is not None:
                return self.call(e)
            return None
        if isinstance(e, C.Cast):
            t = INTS.get(" ".join(e.type.split()))
            if t is not None and t[0] == 32:            # ((u32)p of a structure pointer: p)
                inner = self.natural(e.e)
                if inner is not None:
                    return inner
            p = parse_pointer(e.type)
            if p is not None:                           # ((volatile struct s *)((u32)p + 12): p + 1)
                inner = self.natural(e.e)
                if inner is not None and self.ptr(inner) and self.ptr(inner)[0] == self.new.parse(e.type)[1]:
                    return inner
            return None
        if isinstance(e, C.Unary) and e.op == "&":
            if self.local(e.e):
                return None                 # (the lifter's stack arguments: (u32)&s0, read and written
            d = self.old.of(e.e)            # volatile at its slot: never a pointer C may follow)
            if d is not None and d[0] in ("int", "struct"):
                return C.Unary("&", self.lvalue(e.e))
            return None
        if isinstance(e, C.Binary) and e.op in ("+", "-"):
            sides = [(e.l, e.r)] + ([(e.r, e.l)] if e.op == "+" else [])
            for base, off in sides:
                if constant(base) is not None:
                    continue
                b = self.natural(base)
                if b is None:
                    continue
                p = self.ptr(b)
                w = self.elem(p[0]) if p else None
                if not w:
                    return None
                idx = self.index(off, w)
                if idx is None:
                    return None
                return self.advance(b, e.op, idx)
            return None
        if isinstance(e, C.Cond):
            t, f = self.natural(e.t), self.natural(e.f)
            if t is not None and f is not None and self.ptr(t) and self.ptr(f) and self.ptr(t)[0] == self.ptr(f)[0]:
                return C.Cond(self.truth(e.c), t, f)
            return None
        return None

    def advance(self, b, op, idx):
        """b + idx or b - idx, a constant index folded into one b already has, 0 as nothing."""
        self.arithmetic = True
        k = constant(idx) if not isinstance(idx, C.Binary) else None
        if k is not None:
            if op == "-":
                k = -k
            if isinstance(b, C.Binary) and b.op in ("+", "-") and constant(b.r) is not None:
                k += constant(b.r) if b.op == "+" else -constant(b.r)
                b = b.l
            if k == 0:
                return b
            return C.Binary("+" if k > 0 else "-", b, C.Num("%d" % abs(k)))
        return C.Binary(op, b, idx)

    def to_pointer(self, e, d, const=False):
        """e (an integer of the lifted C) as a pointer to pointee descriptor d: as it is where it is one
        (const where the target is), 0 as it is, another pointer by a pointer cast, an integer by a
        cast."""
        n = self.natural(e)
        if n is None and isinstance(e, C.Cast) and "*" in e.type:
            n = self._cast(e, "bits")       # (a pointer cast the C has: dread.shapes')
        if n is not None:
            p = self.ptr(n)
            if p and p[0] == d and (const or not p[1]):
                return n
            if isinstance(n, C.Cast) and "*" in n.type:
                n = n.e                     # ((T *)(U *)x is (T *)x)
            return C.Cast(self.text(d, const), n)
        if is_zero(e):
            return C.Num("0")
        return C.Cast(self.text(d, const), self.value(e, "bits"))

    def to_void(self, e, const=False):
        """e (an integer of the lifted C) as the argument for a void pointer parameter (an extern's, where
        the callee's own C takes bytes: void_param): a pointer as it is, of any type (const only where the
        parameter is const), 0 as it is, anything else cast."""
        n = self.natural(e)
        if n is None and isinstance(e, C.Cast) and "*" in e.type:
            n = self._cast(e, "bits")
        if n is not None:
            p = self.ptr(n)
            if p and (const or not p[1]):
                return n
            if isinstance(n, C.Cast) and "*" in n.type:
                n = n.e
            return C.Cast(pointer_text("void", const), n)
        if is_zero(e):
            return C.Num("0")
        return C.Cast(pointer_text("void", const), self.value(e, "bits"))

    # -- accesses --

    def access(self, e, store=False):
        """*(volatile U *)A through a typed pointer as p[k] or *p (a load of the pointee's other sign cast
        to U), else as it was with its address rewritten."""
        m = ACCESS.match(" ".join(e.e.type.split()))
        a = e.e.e
        n = self.natural(a) if m else None
        if n is not None:
            p = self.ptr(n)
            if p and p[0][0] == "int" and p[0][1][0] // 8 == SIZES[m.group(1)] and not (store and p[1]):
                if isinstance(n, C.Binary) and n.op in ("+", "-"):
                    idx = n.r if n.op == "+" else _neg(n.r)
                    got = C.Index(n.l, idx)
                else:
                    got = _deref(n)
                if not store and p[0][1] != INTS[m.group(1)]:
                    return C.Cast(m.group(1), got)
                return got
        return C.Unary("*", C.Cast(e.e.type, self.value(a, "bits")))

    def lvalue(self, e):
        """An lvalue of the lifted C (a memory access, a member, an element, a variable) rewritten."""
        if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast):
            return self.access(e, store=True)
        if isinstance(e, C.Unary) and e.op == "*":
            return _deref(self.pointer_or_value(e.e))
        if isinstance(e, C.Member):
            return C.Member(self.pointer_or_value(e.base) if e.arrow else self.lvalue(e.base), e.name, e.arrow)
        if isinstance(e, C.Index):
            return C.Index(self.pointer_or_value(e.base), self.value(e.index, "bits"))
        return _copy(e)

    def pointer_or_value(self, e):
        """An expression of pointer type (a structure pointer, an array's macro) rewritten as it is."""
        if isinstance(e, C.Name):
            return C.Name(e.id)
        if isinstance(e, C.Cast) and "*" in e.type:
            return self._cast(e, "bits")
        if isinstance(e, (C.Member, C.Index)) or (isinstance(e, C.Unary) and e.op == "*"):
            return self.lvalue(e)
        if isinstance(e, C.Unary) and e.op == "&":
            return C.Unary("&", self.lvalue(e.e))
        if isinstance(e, C.Binary) and e.op in ("+", "-"):
            return C.Binary(e.op, self.pointer_or_value(e.l), self.value(e.r, "bits"))
        return self.value(e, "bits")

    # -- integers --

    def value(self, e, need="bits"):
        """e as an integer with the lifted C's value: of its type ("exact"), or where only the low 32
        bits count ("bits") of either 32-bit type."""
        new = self._value(e, need)
        old_d, new_d = self.old.of(e), self.new.of(new)
        if new_d is not None and new_d[0] in ("ptr", "array") and (old_d is None or old_d[0] == "int"):
            t = old_d[1] if old_d else (32, False)
            return C.Cast(int_text(t) or "u32", new)
        if old_d and new_d and old_d[0] == "int" and new_d[0] == "int" and old_d[1] != new_d[1]:
            same32 = old_d[1][0] == 32 and new_d[1][0] == 32
            if need == "exact" or not same32:
                return C.Cast(int_text(old_d[1]), new)
        return new

    def truth(self, e):
        """e as a condition: a typed pointer as it is (its truth is the address's), else its value."""
        n = self.natural(e) if isinstance(e, C.Name) else None
        if n is not None:
            return n
        d = self.old.of(e)
        return self.value(e, "bits" if not (d and d[0] == "int" and d[1][0] > 32) else "exact")

    def _value(self, e, need):
        if isinstance(e, (C.Name, C.Num, C.Str)):
            return _copy(e)
        if isinstance(e, C.Cast):
            return self._cast(e, need)
        if isinstance(e, C.Unary):
            if e.op == "*" and isinstance(e.e, C.Cast):
                return self.access(e)
            if e.op == "*":
                return _deref(self.pointer_or_value(e.e))
            if e.op == "&":
                return C.Unary("&", self.lvalue(e.e))
            if e.op == "!":
                return C.Unary("!", self.truth(e.e))
            if e.op in ("++", "--"):
                return self.step(e)
            return C.Unary(e.op, self.value(e.e, need))
        if isinstance(e, C.Post):
            return self.step(e)
        if isinstance(e, C.Binary):
            return self._binary(e, need)
        if isinstance(e, C.Cond):
            return C.Cond(self.truth(e.c), self.value(e.t, need), self.value(e.f, need))
        if isinstance(e, C.Assign):
            return self.assign(e)
        if isinstance(e, C.Call):
            return self.call(e)
        if isinstance(e, (C.Member, C.Index)):
            return self.lvalue(e)
        if isinstance(e, C.Comma):
            return C.Comma([self.top(x) for x in e.items[:-1]] + [self.value(e.items[-1], need)] if e.items else [])
        return _copy(e)

    def _cast(self, e, need):
        p = parse_pointer(e.type)
        if p is not None or "*" in e.type:
            n = self.natural(e)                 # ((volatile struct s *)((u32)p + 12): p + 1)
            if n is not None:
                return n
            inner = self.natural(e.e)
            if inner is not None:               # (a pointer of another type: a pointer cast)
                return C.Cast(e.type, inner)
            return C.Cast(e.type, self.value(e.e, "bits"))
        t = INTS.get(" ".join(e.type.split()))
        if t is None:
            return C.Cast(e.type, self.value(e.e, "exact"))
        inner = self.value(e.e, "bits" if t[0] <= 32 else "exact")
        d = self.new.of(inner)
        if d is not None and d[0] == "int" and d[1] == t:
            return inner                        # ((s32)v of an s32 v)
        if isinstance(inner, C.Cast) and inner.type == e.type:
            return inner
        return C.Cast(e.type, inner)

    def _binary(self, e, need):
        op = e.op
        if op in ("&&", "||"):
            return C.Binary(op, self.truth(e.l), self.truth(e.r))
        if op in ("==", "!=", "<", "<=", ">", ">="):
            return self.compare(e)
        if op == "<<":
            return C.Binary(op, self.value(e.l, "exact"), self.value(e.r, "bits"))
        if op in (">>", "/", "%"):
            return C.Binary(op, self.value(e.l, "exact"), self.value(e.r, "bits" if op == ">>" else "exact"))
        if op == "-" and need == "bits":        # (a byte pointer's difference: p - q)
            a, b = self.natural(e.l), self.natural(e.r)
            if a is not None and b is not None:
                pa, pb = self.ptr(a), self.ptr(b)
                if pa and pb and pa[0] == pb[0] and self.elem(pa[0]) == 1:
                    self.arithmetic = True
                    return C.Binary("-", a, b)
        sub = need if op in ("+", "-", "*", "&", "|", "^") else "exact"
        return C.Binary(op, self.value(e.l, sub), self.value(e.r, sub))

    def compare(self, e):
        op = e.op
        a, b = self.natural(e.l), self.natural(e.r)
        if a is not None and b is not None:
            pa, pb = self.ptr(a), self.ptr(b)
            if pa and pb and pa[0] == pb[0]:
                return C.Binary(op, a, b)
        if op in ("==", "!="):
            if a is not None and is_zero(e.r) and isinstance(e.l, C.Name):
                return C.Binary(op, a, C.Num("0"))
            if b is not None and is_zero(e.l) and isinstance(e.r, C.Name):
                return C.Binary(op, C.Num("0"), b)
            dl, dr = self.old.of(e.l), self.old.of(e.r)
            narrow = all(d and d[0] == "int" and d[1][0] == 32 for d in (dl, dr))
            need = "bits" if narrow else "exact"
            return C.Binary(op, self.value(e.l, need), self.value(e.r, need))
        return C.Binary(op, self.value(e.l, "exact"), self.value(e.r, "exact"))

    def assign(self, e):
        t = e.target
        if isinstance(t, C.Name) and t.id in self.typing:
            p = parse_pointer(self.typing[t.id])
            if p is not None:
                d = self.new.vars[t.id][1]
                if e.op == "=":
                    return C.Assign("=", C.Name(t.id), self.to_pointer(e.value, d, p[1]))
                w = self.elem(d)
                if e.op in ("+=", "-=") and w:
                    idx = self.index(e.value, w)
                    if idx is not None:
                        self.arithmetic = True
                        return C.Assign(e.op, C.Name(t.id), idx)
                inner = C.Binary(e.op[:-1], C.Cast("u32", C.Name(t.id)), self.value(e.value, "bits"))
                return C.Assign("=", C.Name(t.id), C.Cast(self.text(d, p[1]), inner))
        target = self.lvalue(t)
        d = self.new.of(target)
        if e.op != "=":
            old = self.old.of(t)
            if e.op in ("<<=", ">>=", "/=", "%=") and d != old and d and d[0] == "int":
                # (an s32 variable that the lifted C shifted or divided as u32: x = (u32)x >> k)
                inner = C.Binary(e.op[:-1], C.Cast(int_text(old[1]), _copy(target)), self.value(e.value, "exact"))
                return C.Assign("=", target, inner)
            if e.op in ("<<=", ">>=", "/=", "%=") or not (d and d[0] == "int" and d[1][0] <= 32):
                return C.Assign(e.op, target, self.value(e.value, "exact"))
            return C.Assign(e.op, target, self.value(e.value, "bits"))
        if d is not None and d[0] == "ptr" and d[1] is not None:
            return C.Assign("=", target, self.to_pointer(e.value, d[1]))
        if d is not None and d[0] == "int" and d[1][0] <= 32:
            return C.Assign("=", target, self.value(e.value, "bits"))
        return C.Assign("=", target, self.value(e.value, "exact"))

    def call(self, e):
        params = None
        if isinstance(e.f, C.Name) and e.f.id in self.new.functions:
            params = self.new.functions[e.f.id][1]
        texts = self.param_texts.get(e.f.id, []) if isinstance(e.f, C.Name) else []
        args = []
        for i, a in enumerate(e.args):
            d = params[i] if params is not None and i < len(params) else None
            void = VOID.match(" ".join(texts[i].split())) if i < len(texts) and texts[i] else None
            if void:
                args.append(self.to_void(a, bool(void.group(1))))
            elif d is not None and d[0] == "ptr" and d[1] is not None:
                args.append(self.to_pointer(a, d[1], self._param_const(e.f, i)))
            elif d is not None and d[0] == "int" and d[1][0] <= 32:
                args.append(self.value(a, "bits"))
            else:
                args.append(self.value(a, "exact"))
        if isinstance(e.f, C.Cast):            # (a call through a pointer: its function pointer cast stays)
            f = C.Cast(e.f.type, self.value(e.f.e, "bits"))
        else:
            f = _copy(e.f)
        return C.Call(f, args)

    def _param_const(self, f, i):
        if not isinstance(f, C.Name):
            return False
        texts = self.param_texts.get(f.id)
        if texts and i < len(texts) and texts[i]:
            p = parse_pointer(texts[i])
            return bool(p and p[1])
        return False

    def top(self, e):
        """An expression whose value is not used (a statement's, a for loop's step). A chain of
        assignments that assigns a typed variable is one assignment per variable (dread.tidy
        chain_parts, written as a comma here: apply has split such statements before, split_typed;
        the plan's trials count it so)."""
        if isinstance(e, C.Assign):
            from dread.tidy import chain_parts
            parts = chain_parts(e, self.typing)
            if parts is not None:
                return C.Comma([self.assign(x) for x in parts])
            return self.assign(e)
        if isinstance(e, C.Call):
            return self.call(e)
        if isinstance(e, (C.Post, C.Unary)) and e.op in ("++", "--"):
            return self.step(e, top=True)
        if isinstance(e, C.Comma):
            return C.Comma([self.top(x) for x in e.items])
        return self.value(e, "bits")

    def step(self, e, top=False):
        """x++, x--, ++x, --x of the lifted C (one added to an integer): as they are, but for a typed
        variable whose step C would make another (a pointer to wider elements: one byte is no element;
        an s32's is the same 32 bits), written as the assignment x = x + 1 is (as a statement: assign's
        rules), or as such a value cast back ((u32)(x = ...), only where the value is used)."""
        x = e.e
        if isinstance(x, C.Name) and x.id in self.typing:
            p = parse_pointer(self.typing[x.id])
            if p is not None and self.elem(self.new.vars[x.id][1]) != 1:
                if not top and isinstance(e, C.Post):
                    return self._post_value(e)
                return self.assign(C.Assign("+=" if e.op == "++" else "-=", C.Name(x.id), C.Num("1")))
        if isinstance(x, C.Name) and x.id in self.typing and parse_pointer(self.typing[x.id]) is not None:
            self.arithmetic = True
        return type(e)(e.op, self.lvalue(x))

    def _post_value(self, e):
        """The value of p++ for a pointer to wider elements (its old address) with the step of one byte:
        written through a comma, ((u32)p, p = ...): never in the stages' C, kept correct all the same."""
        x = e.e
        new = self.assign(C.Assign("+=" if e.op == "++" else "-=", C.Name(x.id), C.Num("1")))
        return C.Binary("-" if e.op == "++" else "+", C.Cast("u32", new), C.Num("1"))

    # -- statements --

    def slot(self, s, name):
        """The rewritten expression of one of a statement's expression slots (slots)."""
        if isinstance(s, C.ExprStmt):
            return self.top(s.e)
        if isinstance(s, C.Return):
            r = self.result
            if r is not None and r[0] == "ptr":
                return self.to_pointer(s.e, r[1])
            return self.value(s.e, "bits" if r is not None and r[0] == "int" and r[1][0] <= 32 else "exact")
        if isinstance(s, (C.If, C.While, C.DoWhile)):
            return self.truth(s.cond)
        if isinstance(s, C.For):
            x = getattr(s, name)
            return self.truth(x) if name == "cond" else self.top(x)
        if isinstance(s, C.Switch):
            d = self.old.of(s.e)
            return self.value(s.e, "bits" if d and d[0] == "int" and d[1][0] == 32 else "exact")
        if isinstance(s, C.Decl):
            d = s.items[int(name)]
            t = self.new.vars.get(d.name)
            if t is not None and t[0] == "ptr" and t[1] is not None:
                return self.to_pointer(d.init, t[1])
            return self.value(d.init, "bits" if t and t[0] == "int" and t[1][0] <= 32 else "exact")
        raise ValueError(name)

    def stmt(self, s):
        """Rewrites a statement and those it holds, in place."""
        for st, name in slots(s, inner=False):
            new = self.slot(st, name)
            if isinstance(st, C.Decl):
                st.items[int(name)].init = new
            else:
                setattr(st, name, new)
        for inner in _inner(s):
            self.stmt(inner)

    def count(self, atoms):
        """The casts the rewritten expressions of atoms ([(statement, slot)]) hold."""
        return sum(casts(self.slot(st, name)) for st, name in atoms)


def slots(s, inner=True):
    """[(statement, slot name)] of the expressions a statement holds (and with inner, those of the
    statements it holds): an expression statement's e, a return's e, a condition, a for loop's init,
    cond and step, a switch's e, a declaration's initialisers (the item's index). Asm operands are
    left out (no variable named in one is typed)."""
    out = []
    if isinstance(s, C.ExprStmt):
        out.append((s, "e"))
    elif isinstance(s, C.Return) and s.e is not None:
        out.append((s, "e"))
    elif isinstance(s, (C.If, C.While, C.DoWhile)):
        out.append((s, "cond"))
    elif isinstance(s, C.For):
        if s.init is not None and not isinstance(s.init, C.Decl):
            out.append((s, "init"))
        out += [(s, n) for n in ("cond", "step") if getattr(s, n) is not None]
    elif isinstance(s, C.Switch):
        out.append((s, "e"))
    elif isinstance(s, C.Decl):
        out += [(s, str(i)) for i, d in enumerate(s.items) if d.init is not None and not d.dims]
    if inner:
        for x in _inner(s):
            out += slots(x)
    return out


def casts(e):
    """The casts an expression holds."""
    from dread.cflow import walk_expr
    return sum(1 for x in walk_expr(e) if isinstance(x, C.Cast))


def _deref(n):
    """*n, or the object itself where n is its address (&r0->f_4, not *&r0->f_4: an access at a field's
    place through a pointer that steps to it, r0 + 4 + 0 where the lifter wrote [r1] after r1 = r0 + 4;
    *&x is x, of x's type)."""
    if isinstance(n, C.Unary) and n.op == "&":
        return n.e
    return C.Unary("*", n)


def _neg(e):
    k = constant(e)
    if k is not None and not isinstance(e, C.Binary):
        return _num(-k)
    return C.Unary("-", e)


def _inner(s):
    if isinstance(s, C.Block):
        return list(s.body)
    if isinstance(s, C.If):
        return [s.then] + ([s.orelse] if s.orelse is not None else [])
    if isinstance(s, (C.While, C.DoWhile, C.Switch)):
        return [s.body]
    if isinstance(s, C.For):
        return [s.body] + ([s.init] if isinstance(s.init, C.Decl) else [])
    return []
# --- what a function's C says of its variables ---

def eligible(fn, why=None):
    """The variables a typing may change: the function's own u32 parameters and plain u32 locals of its
    body's top level (not initialised, no pointer or array declarator), less those an asm names, whose
    address is taken, those given the address of a variable of the function (the lifter's stack
    arguments: a_1 = (u32)&s1, read at a_1 + 4 too), the frame's (sp0) and those given a frame address
    (dread.frames' domain). why, a dict: filled with the reason each variable left out is left out
    (dread readable --types)."""
    from dread.cflow import walk_expr
    from dread.naming import _exprs, _inner as n_inner
    why = {} if why is None else why
    out = set()
    for t, n in fn.params:
        if " ".join(t.split()) == "u32":
            out.add(n)
        else:
            why[n] = "a parameter declared %s" % " ".join(t.split())
    for st in fn.body.body:
        if isinstance(st, C.Decl):
            u32 = " ".join(st.specs.split()) == "u32"
            for d in st.items:
                if not u32:
                    why[d.name] = "declared %s" % " ".join(st.specs.split())
                elif d.ptr or d.dims:
                    why[d.name] = "a pointer or array declarator"
                elif d.init is not None:
                    why[d.name] = "initialised in its declaration"
                elif getattr(d, "asm", ""):
                    why[d.name] = "bound to a register (asm)"
                else:
                    out.add(d.name)
    from dread.naming import variables
    own = set(variables(fn)) | set(FRAME)
    bad = set()
    given = {}                              # variable -> the variables its values add up (address arithmetic)

    def local(x):
        """Whether an address-of's operand is a variable of the function, or a member or element of one."""
        while isinstance(x, (C.Member, C.Index)) and not (isinstance(x, C.Member) and x.arrow):
            x = x.base
        return isinstance(x, C.Name) and x.id in own

    def visit(st):
        if isinstance(st, C.Asm):
            for o in st.outputs + st.inputs:
                for x in walk_expr(o.e):
                    if isinstance(x, C.Name):
                        bad.add(x.id)
                        why.setdefault(x.id, "an asm operand")
        if isinstance(st, C.Decl):
            bad.update(d.name for d in st.items if d.init is not None or d.ptr or d.dims)
        for e in _exprs(st):
            for x in walk_expr(e):
                if isinstance(x, C.Unary) and x.op == "&" and isinstance(x.e, C.Name):
                    bad.add(x.e.id)
                    why.setdefault(x.e.id, "its address is taken")
                if isinstance(x, C.Assign) and isinstance(x.target, C.Name):
                    given.setdefault(x.target.id, set()).update(
                        t.id for _, t in terms(x.value) if isinstance(t, C.Name))
                    if any(isinstance(y, C.Unary) and y.op == "&" and local(y.e) for y in walk_expr(x.value)):
                        bad.add(x.target.id)        # (a_1 = (u32)&s1: the lifter reads the stack arguments
                                                    # beyond s1 through it; typed, clang -O2 read past s1)
                        why.setdefault(x.target.id, "given the address of a variable of the function "
                                                    "(the lifter's stack arguments)")
        for inner in n_inner(st):
            visit(inner)
    for st in fn.body.body:
        visit(st)
    framed = set(FRAME)
    changed = True
    while changed:                          # (given a frame address, or one made from one)
        changed = False
        for v, names in given.items():
            if v not in framed and names & framed:
                framed.add(v)
                changed = True
    for v in out & framed:
        if v in FRAME:
            why[v] = "the frame's own"
        else:
            why.setdefault(v, "given a frame address (dread.frames)")
    return out - bad - framed


def terms(e, sign=1):
    """[(sign, term)] of a sum of the lifted C, + and - flattened (casts to 32 bits looked through)."""
    while isinstance(e, C.Cast) and INTS.get(" ".join(e.type.split()), (0,))[0] == 32:
        e = e.e
    if isinstance(e, C.Binary) and e.op in ("+", "-"):
        return terms(e.l, sign) + terms(e.r, sign if e.op == "+" else -sign)
    return [(sign, e)]


NEGATIVE_READS = ("eq", "ret", "scmp")      # what reads a variable the negative rule types: ==, return, (s32) <


class Facts:
    """One function's variables as the plan reads them: definitions (defs: {variable: [value, None
    where it is no plain value]}), memory accesses ((type, address, store)), the arguments of calls
    ((callee, index, expression)), signed and unsigned ordered comparisons of each variable, the
    expression slots that name each variable (atoms), what reads each variable (reads: {variable: {kind}}:
    "eq" an operand of == or != or a truth test, "ret" a return's whole value, "scmp" a (s32) cast of it
    compared in order, "other" anything else) and what each call's result goes to (results: {callee:
    [kind]}: those kinds, "unused", ("var", v) a variable assigned the call, "other")."""

    def __init__(self, fn, exclude=(), pointer_params=()):
        from dread.cflow import walk_expr
        self.fn = fn
        self._groups = None
        self._signed_results = None
        self.pointer_params = [fn.params[i][1] for i in pointer_params if i < len(fn.params)]
        self.plain = eligible(fn) - set(exclude)
        self.defs, self.accesses, self.calls = {}, [], []
        self.signed, self.unsigned = {}, set()
        self.reads, self.results = {}, {}
        self.atoms = []                     # [(statement, slot)]
        self.named = {}                     # variable -> [indices into atoms]
        self.call_atoms = {}                # callee -> [indices into atoms]
        for st, name in slots_of(fn):
            e = st.items[int(name)].init if isinstance(st, C.Decl) else getattr(st, name)
            k = len(self.atoms)
            self.atoms.append((st, name))
            self._uses(e, _slot_context(st, name))
            for x in walk_expr(e):
                if isinstance(x, C.Name):
                    self.named.setdefault(x.id, []).append(k)
                elif isinstance(x, C.Call) and isinstance(x.f, C.Name):
                    self.call_atoms.setdefault(x.f.id, []).append(k)
                    for i, a in enumerate(x.args):
                        self.calls.append((x.f.id, i, a))
                elif isinstance(x, C.Assign) and isinstance(x.target, C.Name):
                    v = x.target.id
                    if x.op == "=":
                        self.defs.setdefault(v, []).append(x.value)
                    elif x.op in ("+=", "-="):
                        self.defs.setdefault(v, []).append(C.Binary(x.op[0], C.Name(v), x.value))
                    else:
                        self.defs.setdefault(v, []).append(None)
                elif isinstance(x, (C.Post, C.Unary)) and x.op in ("++", "--") and isinstance(x.e, C.Name):
                    self.defs.setdefault(x.e.id, []).append(C.Binary(x.op[0], C.Name(x.e.id), C.Num("1")))
                elif isinstance(x, C.Binary) and x.op in ("<", "<=", ">", ">="):
                    for side in (x.l, x.r):
                        if isinstance(side, C.Cast) and side.type == "s32" and isinstance(side.e, C.Name):
                            self.signed[side.e.id] = self.signed.get(side.e.id, 0) + 1
                        elif isinstance(side, C.Name):
                            self.unsigned.add(side.id)
                if isinstance(x, C.Assign) and isinstance(x.target, C.Unary):
                    self._access(x.target, True)
                elif isinstance(x, C.Unary) and x.op == "*":
                    self._access(x, False)
        for k in self.named:
            self.named[k] = sorted(set(self.named[k]))
        for k in self.call_atoms:
            self.call_atoms[k] = sorted(set(self.call_atoms[k]))

    def _uses(self, x, ctx):
        """Records what reads each variable and what takes each call's result in expression x, whose
        value goes to ctx: "eq" (compared for equality, or a truth), "ret" (returned), "unused", "order"
        (an operand of an ordered comparison), ("assign", v) (assigned to the variable v), "other"."""
        if x is None:
            return
        if isinstance(x, C.Name):
            kind = ctx if ctx in ("eq", "ret") else "other"
            self.reads.setdefault(x.id, set()).add(kind)
            return
        if isinstance(x, C.Call):
            if isinstance(x.f, C.Name):
                kind = ("var", ctx[1]) if isinstance(ctx, tuple) else ctx if ctx in ("eq", "ret", "unused", "scmp") \
                    else "other"
                self.results.setdefault(x.f.id, []).append(kind)
            else:
                self._uses(x.f, "other")
            for a in x.args:
                self._uses(a, "other")
            return
        if isinstance(x, C.Cast):
            if ctx == "order" and " ".join(x.type.split()) == "s32" and isinstance(x.e, C.Name):
                self.reads.setdefault(x.e.id, set()).add("scmp")
                return
            if ctx == "order" and " ".join(x.type.split()) == "s32" and isinstance(x.e, C.Call):
                self._uses(x.e, "scmp")
                return
            self._uses(x.e, "other")
            return
        if isinstance(x, C.Binary):
            sub = {"==": "eq", "!=": "eq", "&&": "eq", "||": "eq", "<": "order", "<=": "order", ">": "order",
                   ">=": "order"}.get(x.op, "other")
            self._uses(x.l, sub)
            self._uses(x.r, sub)
            return
        if isinstance(x, C.Unary) and x.op == "!":
            self._uses(x.e, "eq")
            return
        if isinstance(x, C.Cond):
            self._uses(x.c, "eq")
            self._uses(x.t, ctx)
            self._uses(x.f, ctx)
            return
        if isinstance(x, C.Assign):
            if isinstance(x.target, C.Name):
                if x.op != "=":
                    self.reads.setdefault(x.target.id, set()).add("other")
                self._uses(x.value, ("assign", x.target.id) if x.op == "=" else "other")
            else:
                self._uses(x.target, "other")
                self._uses(x.value, "other")
            return
        if isinstance(x, C.Comma):
            for y in x.items[:-1]:
                self._uses(y, "unused")
            if x.items:
                self._uses(x.items[-1], ctx)
            return
        if isinstance(x, (C.Unary, C.Post)):
            self._uses(x.e, "other")
            return
        if isinstance(x, C.Index):
            self._uses(x.base, "other")
            self._uses(x.index, "other")
            return
        if isinstance(x, C.Member):
            self._uses(x.base, "other")
            return
        from dread.cflow import walk_expr
        for y in list(walk_expr(x))[1:]:
            if isinstance(y, C.Name):
                self.reads.setdefault(y.id, set()).add("other")

    def _flow(self):
        """(dread.cflow Graph, its reaching definitions) of the function, made once; (None, None) where
        the flow cannot be made."""
        if getattr(self, "_graph", None) is None:
            from dread.cflow import Graph
            try:
                g = Graph(self.fn)
                self._graph = (g, g.reaching())
            except Exception:
                self._graph = (None, None)
        return self._graph

    def result_kinds(self, callee, skip=()):
        """{kind} of what reads callee's result in this function (Facts.results): where a variable is
        assigned the call, the kinds of its reads that this definition reaches (dread.cflow's reaching
        definitions: a variable that holds the result and then other values, read otherwise only after
        another definition, reads the result only as these do), else every read of the variable; the
        variables of skip left out."""
        out = set()
        wanted = {}
        for kind in self.results.get(callee, ()):
            if isinstance(kind, tuple):
                if kind[1] not in skip:
                    wanted[kind[1]] = wanted.get(kind[1], 0) + 1
            else:
                out.add(kind)
        if not wanted:
            return out
        g, reach = self._flow()
        defs = {}                           # variable -> {node index of v = callee(...)}
        if g is not None:
            for n in g.nodes:
                st = n.stmt
                if isinstance(st, C.ExprStmt) and isinstance(st.e, C.Assign) and st.e.op == "=" and \
                        isinstance(st.e.target, C.Name) and isinstance(st.e.value, C.Call) and \
                        isinstance(st.e.value.f, C.Name) and st.e.value.f.id == callee:
                    defs.setdefault(st.e.target.id, set()).add(n.index)
        for v, count in wanted.items():
            if g is None or len(defs.get(v, ())) != count:
                out |= self.reads.get(v, {"other"})     # (a definition the flow does not see: every read)
                continue
            for n in g.nodes:
                if not (reach[n.index].get(v) or set()) & defs[v]:
                    continue
                one = Facts.__new__(Facts)
                one.reads, one.results = {}, {}
                for st, name in slots(n.stmt):
                    e = st.items[int(name)].init if isinstance(st, C.Decl) else getattr(st, name)
                    one._uses(e, _slot_context(st, name))
                out |= one.reads.get(v, set())
        return out

    def negative(self, v):
        """Whether a variable is assigned a constant within NEGATIVE of the top of 32 bits (one dread.polish
        prints as a negative number where it is a value: ~0x0u, 0xfffffffeu), also as an arm of a select."""
        from dread.polish import NEGATIVE

        def near_top(x):
            if isinstance(x, C.Cond):
                return near_top(x.t) or near_top(x.f)
            k = constant(x) if x is not None else None
            return k is not None and (1 << 32) - NEGATIVE <= k & 0xffffffff
        return any(near_top(x) for x in self.defs.get(v, ()))

    def signed_results(self):
        """The callees whose result this function compares signed: (s32)f(...) in an ordered
        comparison, or (s32)v where every definition of v that reaches the comparison is v = f(...)
        (dread.cflow's reaching definitions; a register variable that holds a parameter and then a
        callee's result, handle_key's r0, is evidence only where the call's result reaches)."""
        if self._signed_results is not None:
            return self._signed_results
        from dread.cflow import Graph, walk_expr
        out = set()
        try:
            g = Graph(self.fn)
            reach = g.reaching()
        except Exception:
            self._signed_results = out
            return out

        def callee_of(k, v):
            st = g.nodes[k].stmt if 0 <= k < len(g.nodes) else None
            if isinstance(st, C.ExprStmt) and isinstance(st.e, C.Assign) and st.e.op == "=" and \
                    isinstance(st.e.target, C.Name) and st.e.target.id == v and isinstance(st.e.value, C.Call) \
                    and isinstance(st.e.value.f, C.Name):
                return st.e.value.f.id
            return None
        for n in g.nodes:
            for st, name in slots(n.stmt):
                e = st.items[int(name)].init if isinstance(st, C.Decl) else getattr(st, name)
                for x in walk_expr(e):
                    if not (isinstance(x, C.Binary) and x.op in ("<", "<=", ">", ">=")):
                        continue
                    for side in (x.l, x.r):
                        if not (isinstance(side, C.Cast) and side.type == "s32"):
                            continue
                        if isinstance(side.e, C.Call) and isinstance(side.e.f, C.Name):
                            out.add(side.e.f.id)
                        elif isinstance(side.e, C.Name):
                            ds = reach[n.index].get(side.e.id) or set()
                            fs = {callee_of(d, side.e.id) for d in ds}
                            if len(fs) == 1 and None not in fs:
                                out |= fs
        self._signed_results = out
        return out

    def _access(self, x, store):
        if x.op != "*" or not isinstance(x.e, C.Cast):
            return
        m = ACCESS.match(" ".join(x.e.type.split()))
        if m:
            if store:                       # (the target was also met as an access: a store wins)
                self.accesses = [a for a in self.accesses if a[1] is not x.e.e]
            if not any(a[1] is x.e.e for a in self.accesses):
                self.accesses.append((m.group(1), x.e.e, store))

    def groups(self):
        if self._groups is None:
            self._groups = self._make_groups()
        return self._groups

    def _make_groups(self):
        """{variable: its group's root} of the eligible variables: copies of one another, one plus or
        minus a constant of another, one plus an index of a pointer; and the roots of the groups that
        hold pointers (the address of a memory access is one of them plus constants or indices)."""
        parent = {v: v for v in self.plain}

        def find(v):
            while parent[v] != v:
                parent[v] = parent[parent[v]]
                v = parent[v]
            return v

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
        sums = []                           # (variable, [(sign, term)]) of definitions with two or more variables
        for v, values in self.defs.items():
            if v not in parent:
                continue
            for x in values:
                if x is None:
                    continue
                ts = [(s, t) for s, t in terms(x) if constant(t) is None]
                if len(ts) == 1 and ts[0][0] == 1 and isinstance(ts[0][1], C.Name) and ts[0][1].id in parent:
                    union(v, ts[0][1].id)
                elif len(ts) > 1:
                    sums.append((v, ts))
        index_like = set()                  # (variables given a small constant, 0 too: counters, indices)
        for v, values in self.defs.items():
            if v in parent and any(x is not None and constant(x) is not None and constant(x) & 0xffffffff < SMALL
                                   for x in values):
                index_like.add(v)
        pointers = set()
        given = set(self.pointer_params)    # (dread.shapes: parameters the function uses as pointers)
        ambiguous = []
        for _, a, _ in self.accesses:
            ts = [(s, t) for s, t in terms(a) if constant(t) is None]
            names = [t.id for s, t in ts if s == 1 and isinstance(t, C.Name)]
            if any(n in FRAME for n in names) or not names:
                continue
            plain = [n for n in names if n in parent]
            if len(names) == 1 and plain:
                pointers.add(plain[0])
            elif len(plain) > 1 or (len(plain) == 1 and len(names) > 1):
                ambiguous.append((names, plain))
        changed = True
        while changed:
            changed = False
            roots = {find(p) for p in pointers}
            for names, plain in ambiguous:
                if any(n not in parent for n in names):
                    continue                # (another variable is the pointer: a structure's, a u64's)
                known = [n for n in plain if find(n) in roots]
                if not known:
                    rest = [n for n in plain if not any(find(i) == find(n) for i in index_like)]
                    if len(rest) > 1:
                        rest = [n for n in rest if any(find(g) == find(n) for g in given)]
                    known = rest if len(rest) == 1 and len(plain) == len(names) else []
                if len(known) == 1 and known[0] not in pointers:
                    pointers.add(known[0])
                    changed = True
            roots = {find(p) for p in pointers}
            for v, ts in sums:
                names = [t.id for s, t in ts if s == 1 and isinstance(t, C.Name) and t.id in parent]
                bases = [n for n in names if find(n) in roots]
                if not bases and find(v) in roots:  # (a pointer made as base + index: a4 = a0 + a5, the
                    rest = [n for n in names        # base the term no index evidence names)
                            if not any(find(i) == find(n) for i in index_like)]
                    bases = rest if len(rest) == 1 and len(names) == len([t for s, t in ts if s == 1]) else []
                if len(bases) == 1 and find(v) != find(bases[0]) and all(
                        not (isinstance(t, C.Name) and t.id in parent and find(t.id) in roots) or t.id == bases[0]
                        for _, t in ts):
                    union(v, bases[0])
                    changed = True
        return {v: find(v) for v in parent}, {find(p) for p in pointers}

    def base(self, a, group, roots):
        """The eligible variable whose group holds pointers that the address a is made from, added (the
        address's other variables eligible ones that hold none: indices), or None."""
        ts = terms(a)
        names = [t.id for _, t in ts if isinstance(t, C.Name)]
        found = [t.id for sign, t in ts if sign == 1 and isinstance(t, C.Name) and t.id in group
                 and group[t.id] in roots]
        if len(found) != 1 or any(n not in group for n in names) or names.count(found[0]) != 1:
            return None
        return found[0]


def slots_of(fn):
    out = []
    for st in fn.body.body:
        out += slots(st)
    return out


def _slot_context(st, name):
    """Where the value of a statement's expression slot goes (Facts._uses): a return's is returned, an
    expression statement's and a for loop's init and step are unused, a condition is a truth (compared
    with 0), a switch's is compared for equality with its cases, a declaration's initialiser is assigned."""
    if isinstance(st, C.Return):
        return "ret"
    if isinstance(st, C.ExprStmt) or (isinstance(st, C.For) and name != "cond"):
        return "unused"
    if isinstance(st, (C.If, C.While, C.DoWhile, C.For, C.Switch)):
        return "eq"
    if isinstance(st, C.Decl):
        return ("assign", st.items[int(name)].name)
    return "other"


def _framed(e):
    from dread.cflow import walk_expr
    return any(isinstance(x, C.Name) and x.id in FRAME for x in walk_expr(e))


class Candidate:
    """Variables to type together (members) and the types to try for them, best first; params: the
    callees' parameters ((callee, index)) whose extern declarations take the type with them (a variable
    handed to them); kind: "pointer", "handed", "signed" or "extern" (a callee's parameter alone)."""
    __slots__ = ("members", "types", "kind", "params")

    def __init__(self, members, types, kind, params=()):
        self.members, self.types, self.kind, self.params = sorted(members), types, kind, sorted(params)


def candidates(facts, protos, signed_result=False):
    """The candidates of one function, in the order the plan tries them: pointer groups by their
    accesses (most accesses first), groups handed to callees' pointer parameters (protos: {callee:
    (result, [parameter types])} of the callees' own readable C) with those parameters, variables
    compared signed, variables given negative values (negative_variables: those the function returns
    only where its result is s32, signed_result), then each callee's pointer parameter alone (its extern
    declares it so where the calls pass what C takes for it without a cast)."""
    group, roots = facts.groups()
    members = {}
    for v, r in group.items():
        members.setdefault(r, set()).add(v)
    out = []
    by_root = {}
    for ctype, a, store in facts.accesses:
        b = facts.base(a, group, roots)
        if b is not None:
            by_root.setdefault(group[b], []).append((ctype, store))
    framed = {r for r, ms in members.items()
              if any(_framed(x) for v in ms for x in facts.defs.get(v, ()) if x is not None)}
    for r, acc in sorted(by_root.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if r in framed:
            continue
        widths = {SIZES[t] for t, _ in acc}
        if len(widths) != 1:
            continue                        # (a structure, or a buffer walked at several widths)
        types_ = {t for t, _ in acc}
        w = widths.pop()
        pointee = next(iter(types_)) if len(types_) == 1 and all(t.startswith("s") for t in types_) \
            else "u%d" % (8 * w)
        stores = any(s for _, s in acc)
        tries = ([pointer_text(pointee, True)] if not stores else []) + [pointer_text(pointee)]
        out.append(Candidate(members[r], tries, "pointer"))
    taken = {v for c in out for v in c.members}
    # groups the function hands to callees' pointer parameters and does not dereference
    handed = {}
    own = facts.fn.name                     # (a call of itself declares itself as its head: never apart)
    for callee, i, a in facts.calls:
        if callee == own:
            continue
        if isinstance(a, C.Name) and a.id in group and a.id not in taken:
            params = (protos.get(callee) or (None, []))[1]
            t = params[i] if i < len(params) else None
            handed.setdefault(group[a.id], []).append((callee, i, t))
    for r, uses in sorted(handed.items()):
        if r in by_root or r in framed or any(t is None for _, _, t in uses):
            continue
        ps = {parse_pointer(t) for _, _, t in uses}
        if None in ps or len({p[0] for p in ps}) != 1:
            continue
        pointee = next(iter(ps))[0]
        const = all(p[1] for p in ps)
        out.append(Candidate(members[r], [pointer_text(pointee, const)], "handed",
                             {(c, i) for c, i, _ in uses}))
    taken = {v for c in out for v in c.members}
    for v in sorted(facts.signed):
        if v in facts.plain and v not in taken and v not in facts.unsigned:
            out.append(Candidate([v], ["s32"], "signed"))
    for v in negative_variables(facts, taken, returning=signed_result):
        out.append(Candidate([v], ["s32"], "negative"))
    for callee in sorted(facts.call_atoms):
        if callee == own:
            continue
        params = (protos.get(callee) or (None, []))[1]
        for i, t in enumerate(params):
            if t is not None and parse_pointer(t) is not None:
                out.append(Candidate([], [t] + void_param(t), "extern", {(callee, i)}))
    return out


def void_param(t):
    """[the void pointer type] for a callee's byte pointer parameter t (u8: the bytes of what it is given,
    memcpy's source and the queue item it is handed on to), with t's const; [] for another. A file's
    extern may declare it so where its calls pass pointers of other types: C takes any object's pointer
    for a void pointer without a cast, and the callee's own definition keeps its bytes (one pointer at
    the machine's level: each function is a file of its own)."""
    p = parse_pointer(t)
    return [pointer_text("void", p[1])] if p and p[0] == "u8" else []


def negative_variables(facts, taken=(), returning=True):
    """The variables the negative rule types s32 (if that adds no casts: the plan's trial): eligible, given
    a constant dread.polish prints as a negative number (Facts.negative: ret = -1), read only by == or !=
    (a truth test too) and by return (Facts.reads; one compared in order signed is the signed rule's, one
    compared unsigned no candidate), none of a pointer group (taken). One the function returns only with
    returning (where the function's result is s32: a u32 function's s32 variable would contradict it, as
    a saturated sum's -1 beside r0 + r1 would)."""
    out = []
    for v in sorted(facts.plain):
        if v in taken or v in facts.signed or v in facts.unsigned:
            continue
        kinds = facts.reads.get(v)
        if kinds and kinds <= set(NEGATIVE_READS) and facts.negative(v) and (returning or "ret" not in kinds):
            out.append(v)
    return out


def head_match(fn):
    """(result text, parameter text) of a function's head, whose result may be several words (a pointer
    result: volatile struct s_13 *f(u32 r0)), anchored on its name; None where it does not read so."""
    return re.match(r"^(.*?)\s*\b%s\((.*)\)$" % re.escape(fn.name), fn.head.strip())


def head_text(result, name, params):
    """A head of these parts: a pointer result's star beside the name (volatile u8 *f(...))."""
    result = result.strip()
    return "%s%s%s(%s)" % (result, "" if result.endswith("*") else " ", name, params)


def returns_negative(facts, typing):
    """Whether a function's own C makes its result a negative value's: a return of a constant dread.polish
    prints as a negative number (also an arm of a select returned: return c ? 0 : -1), or of a variable
    given one that the negative rule would type (negative_variables) or the plan typed s32."""
    from dread.polish import NEGATIVE
    named = set(negative_variables(facts)) | {v for v, t in typing.items() if t == "s32" and facts.negative(v)}

    def negative(e):
        if isinstance(e, C.Cond):
            return negative(e.t) or negative(e.f)
        k = constant(e)
        if k is not None and (1 << 32) - NEGATIVE <= k & 0xffffffff:
            return True
        return isinstance(e, C.Name) and e.id in named
    return any(isinstance(st, C.Return) and st.e is not None and negative(st.e) for st, _ in facts.atoms)


def read_as_negative(facts, typing, callee):
    """Whether this function reads callee's result only as the negative rule reads a variable: unused,
    returned, compared for equality or signed in order, also through a variable assigned the call
    (Facts.result_kinds), or assigned to a variable typed s32."""
    typed = {v for v, t in typing.items() if t == "s32"}     # (an s32 variable reads it as an int)
    return facts.result_kinds(callee, skip=typed) <= {"unused", "ret", "eq", "scmp"}


def local_types(fn, types, protos=None, result=None, sizes=None):
    """The typing of a function the plan did not analyse (outside the lifted table: printed on request,
    as gizmo a32's ring_get): the negative rule alone, decided on its own C as the plan decides (a
    variable kept s32 where its statements hold no more casts so; result: its result type as the plan
    gave it, s32 where an analysed caller compares it signed). Its own result is not decided here: no
    caller of it is known."""
    facts = Facts(fn)
    typing = {}
    for v in negative_variables(facts, returning=result == "s32"):
        atoms = [facts.atoms[k] for k in facts.named.get(v, ())]
        before = Rewriter(fn, typing, types, protos, result, sizes).count(atoms)
        trial = dict(typing, **{v: "s32"})
        if Rewriter(fn, trial, types, protos, result, sizes).count(atoms) <= before:
            typing = trial
    return typing


# --- the plan ---

def _call_order(facts):
    """The functions callees first (a cycle broken by name)."""
    calls = {f: {c for c in fx.call_atoms if c in facts and c != f} for f, fx in facts.items()}
    order, state = [], {}

    def visit(f):
        state[f] = 1
        for c in sorted(calls[f]):
            if c not in state:
                visit(c)
        state[f] = 2
        order.append(f)
    for f in sorted(facts):
        if f not in state:
            visit(f)
    return order


class Planner:
    """The plan's decisions over the analysed functions (plan): per function the types of its variables
    (typing) and of its callees' parameters as its file declares them (calls: {callee: {index: type}});
    per function its own prototype (its parameters' types from its typing and dread.shapes' structure
    pointers, its result: s32 where its callers compare it signed)."""

    def __init__(self, trees, shapes_plan=None, int_bits=None, names=None):
        from dread import polish, tidy
        self.trees = {fn.name: (tree, fn) for tree, fn, _ in trees}
        p = shapes_plan
        names = names or {}

        def cname(k):
            return names.get(k, (k, {}))[0]
        self.sizes = {"struct " + cname(k): p.size(k) for k in p.structs} if p is not None else {}
        self.facts, self.types = {}, {}
        for name, (tree, fn) in self.trees.items():
            if tidy.fixed_registers(tree) or tidy.context(fn).int_bits != 32:
                continue                    # (kept as the lifter wrote it, or 16-bit registers)
            exclude = set((p.typed.get(name) or {}) if p is not None else ())
            used = ((p.params or {}).get(name) or ()) if p is not None else ()
            self.facts[name] = Facts(fn, exclude, used)
            self.types[name] = polish.Types(tree, fn, int_bits)
        self.typing = {name: {} for name in self.facts}
        self.calls = {name: {} for name in self.facts}
        self.results = {}                   # function -> its result type text where not as declared
        self.structs = {}                   # function -> {parameter: structure pointer type} of dread.shapes
        if p is not None:
            for name in self.facts:
                fn = self.trees[name][1]
                own = p.typed.get(name) or {}
                self.structs[name] = {n: pointer_text("struct " + cname(own[n])) for _, n in fn.params
                                      if n in own and own[n] in p.structs}

    def prototype(self, name):
        """(result type or None, [parameter types or None]) of the function's own readable C."""
        fn = self.trees[name][1]
        typed = dict(self.structs.get(name, {}))
        typed.update(self.typing.get(name, {}))
        return (self.results.get(name), [typed.get(n) for _, n in fn.params])

    def protos(self):
        return {name: self.prototype(name) for name in self.facts}

    def file_protos(self, protos, calls):
        """{callee: (result, [parameter types])} as one function's file declares its callees: the
        callees' results, and the parameters calls types ({callee: {index: type}})."""
        out = {}
        for f, (res, params) in protos.items():
            mine = calls.get(f, {})
            if res or mine:
                out[f] = (res, [mine.get(i) for i in range(len(params))])
        return out

    def cost(self, name, typing, protos, atoms):
        fn = self.trees[name][1]
        r = Rewriter(fn, typing, self.types[name], protos, self.results.get(name), self.sizes)
        return r.count(atoms)

    def decide(self, name, protos, log=None):
        """(typing, calls) of one function: its candidates tried in order, each kept where the
        expressions it touches (those naming its variables, the calls of its callees) hold no more casts
        with it than without. log, a list: each trial appended as (kind, members, callee parameters,
        type, casts without, casts with, kept)."""
        fx = self.facts[name]
        typing, calls = {}, {}
        for cand in candidates(fx, protos, signed_result=self.results.get(name) == "s32"):
            ks = {k for v in cand.members for k in fx.named.get(v, ())}
            ks |= {k for f, _ in cand.params for k in fx.call_atoms.get(f, ())}
            atoms = [fx.atoms[k] for k in sorted(ks)]
            before = self.cost(name, typing, self.file_protos(protos, calls), atoms)
            best = None                     # (an extern candidate: the type with the fewest casts, the
            for t in cand.types:            # first of those; the others: the first that adds none)
                trial, trial_calls = dict(typing), {f: dict(ps) for f, ps in calls.items()}
                trial.update({v: t for v in cand.members})
                for f, i in cand.params:        # (an extern candidate's own type: the void pointer too)
                    trial_calls.setdefault(f, {})[i] = t if cand.kind == "extern" else protos[f][1][i]
                after = self.cost(name, trial, self.file_protos(protos, trial_calls), atoms)
                if log is not None:
                    log.append((cand.kind, sorted(cand.members), sorted(cand.params), t, before, after,
                                after <= before and (best is None or after < best[0])))
                if after <= before and (best is None or after < best[0]):
                    best = (after, trial, trial_calls)
                    if cand.kind != "extern":
                        break
            if best is not None:
                typing, calls = best[1], best[2]
        return typing, calls

    def decide_result(self, name, protos, callers):
        """Whether the function's result is s32: a caller compares it signed (Facts.signed_results), or
        its own returns give negative values (returns_negative: return -1, or a variable the negative
        rule typed) where a caller reads it and every caller reads it only as that rule reads a variable
        (read_as_negative), and its own returns hold no more casts so; and its callers' C holds no more
        casts so. Without a caller that reads it (gizmo's udiv_var, whose source returns 0xFFFFFFFFu
        beside an unsigned quotient) a negative return is no evidence."""
        if self.results.get(name):
            return False
        if not any(name in self.facts[c].signed_results() for c in callers):
            fx = self.facts[name]
            typing = self.typing.get(name, {})
            read = any(k != "unused" for c in callers for k in self.facts[c].results.get(name, ()))
            if not returns_negative(fx, typing) or not read or \
                    not all(read_as_negative(self.facts[c], self.typing[c], name) for c in callers):
                return False                # (a caller that reads it, every caller as the rule reads)
            atoms = [(st, slot) for st, slot in fx.atoms if isinstance(st, C.Return)]
            fn = self.trees[name][1]
            own = self.file_protos(protos, self.calls[name])
            if Rewriter(fn, typing, self.types[name], own, "s32", self.sizes).count(atoms) > \
                    Rewriter(fn, typing, self.types[name], own, None, self.sizes).count(atoms):
                return False
        before = after = 0
        trial = dict(protos)
        trial[name] = ("s32", protos[name][1])
        for c in callers:
            fx = self.facts[c]
            atoms = [fx.atoms[k] for k in fx.call_atoms.get(name, ())]
            before += self.cost(c, self.typing[c], self.file_protos(protos, self.calls[c]), atoms)
            after += self.cost(c, self.typing[c], self.file_protos(trial, self.calls[c]), atoms)
        return after <= before

    def returned_pointer(self, name, protos):
        """The pointer type every return of the function gives: a variable the plan typed or the C declares
        a pointer of that type (dread.shapes' structure pointers), a call of a callee whose result is
        one, or 0; None where a return gives anything else or two types meet."""
        fx, typing, types = self.facts[name], self.typing.get(name, {}), self.types[name]
        seen = set()
        for st, _ in fx.atoms:
            if not isinstance(st, C.Return) or st.e is None or is_zero(st.e):
                continue
            e, t = st.e, None
            if isinstance(e, C.Cast) and " ".join(e.type.split()) == "u32" and isinstance(e.e, C.Name):
                e = e.e                     # (dread.shapes' structure pointer as it returns one: (u32)r0)
            if isinstance(e, C.Name) and e.id in typing:
                t = typing[e.id] if parse_pointer(typing[e.id]) is not None else None
            elif isinstance(e, C.Name):
                d = types.vars.get(e.id)
                if d and d[0] == "ptr" and d[1] is not None and d[1][0] in ("int", "struct"):
                    t = pointer_text(int_text(d[1][1]) if d[1][0] == "int" else d[1][1])
            elif isinstance(e, C.Call) and isinstance(e.f, C.Name):
                res = (protos.get(e.f.id) or (None,))[0]
                t = res if res and parse_pointer(res) is not None else None
            if t is None:
                return None
            seen.add(" ".join(t.split()))
        return next(iter(seen)) if len(seen) == 1 else None

    def decide_pointer_result(self, name, protos, callers):
        """The pointer type the function's result takes (returned_pointer), where its returns and its
        callers' C hold no more casts with it than with the u32 the lifter declared (`return (u32)r0;`
        goes, a caller that uses the result as a number gets a cast); else None."""
        t = self.returned_pointer(name, protos)
        if t is None:
            return None
        fx, typing = self.facts[name], self.typing.get(name, {})
        atoms = [(st, slot) for st, slot in fx.atoms if isinstance(st, C.Return)]
        fn = self.trees[name][1]
        own = self.file_protos(protos, self.calls[name])
        before = Rewriter(fn, typing, self.types[name], own, None, self.sizes).count(atoms)
        after = Rewriter(fn, typing, self.types[name], own, t, self.sizes).count(atoms)
        trial = dict(protos)
        trial[name] = (t, protos[name][1])
        for c in callers:
            cx = self.facts[c]
            catoms = [cx.atoms[k] for k in cx.call_atoms.get(name, ())]
            before += self.cost(c, self.typing[c], self.file_protos(protos, self.calls[c]), catoms)
            after += self.cost(c, self.typing[c], self.file_protos(trial, self.calls[c]), catoms)
        return t if after <= before else None

    def decide_outside(self, name, callers):
        """Whether a callee the plan did not analyse (not lifted: its own C printed on request) returns
        s32 as its externs declare it: an analysed caller compares its result signed (Facts.signed_results,
        as for an analysed callee), and every caller's C holds no more casts so."""
        before = after = 0
        protos = self.protos()
        for c in callers:
            ext = self.types[c].functions.get(name)
            if ext is None or ext[0] != ("int", (32, False)):
                return False
            fx = self.facts[c]
            atoms = [fx.atoms[k] for k in fx.call_atoms.get(name, ())]
            base = self.file_protos(protos, self.calls[c])
            trial = dict(base)
            trial[name] = ("s32", [None] * len(ext[1] or ()))
            before += self.cost(c, self.typing[c], base, atoms)
            after += self.cost(c, self.typing[c], trial, atoms)
        return after <= before

    def run(self, rounds=ROUNDS):
        order = _call_order(self.facts)
        callers = {}
        for f, fx in self.facts.items():
            for c in fx.call_atoms:
                if c in self.facts and c != f:
                    callers.setdefault(c, set()).add(f)
        dirty = set(self.facts)
        for _ in range(rounds):
            changed = set()
            for name in order:
                if name not in dirty:
                    continue
                old = self.prototype(name)
                self.typing[name], self.calls[name] = self.decide(name, self.protos())
                if self.prototype(name) != old:
                    changed.add(name)
            again = set()                   # (a result now s32: the variables it returns may be too)
            for name in order:
                if self.trees[name][1].head.split()[0] == "u32" and \
                        self.decide_result(name, self.protos(), sorted(callers.get(name, ()))):
                    self.results[name] = "s32"
                    changed.add(name)
                    again.add(name)
            for name in order:              # (a pointer result: its callers' variables may follow it)
                if self.trees[name][1].head.split()[0] != "u32" or self.results.get(name):
                    continue
                t = self.decide_pointer_result(name, self.protos(), sorted(callers.get(name, ())))
                if t is not None:
                    self.results[name] = t
                    changed.add(name)
            dirty = {c for f in changed for c in callers.get(f, ())} | again
            if not dirty:
                break
        # The pointer results once more with the callers' final types, callees first (a result handed
        # through): a caller typed in a later round may use one as a number where it held a pointer
        # before (RISC-V GCC -O2's memset, compared with u32 variables by four callers: a cast each)
        for name in order:
            t = self.results.get(name)
            if t and t != "s32":
                del self.results[name]
                if self.decide_pointer_result(name, self.protos(), sorted(callers.get(name, ()))) == t:
                    self.results[name] = t
        # callees the plan does not analyse (outside the lifted table) whose result an analysed caller
        # compares signed: s32 in their externs, and in their own C printed on request (prototypes)
        outside = {}
        for f, fx in self.facts.items():
            for c in fx.call_atoms:
                if c not in self.facts and c != f:
                    outside.setdefault(c, set()).add(f)
        for c in sorted(outside):
            if any(c in self.facts[f].signed_results() for f in outside[c]) and \
                    self.decide_outside(c, sorted(outside[c])):
                self.results[c] = "s32"
        return self


def explain(p, trees, digests=None, context=None):
    """Why each function of trees ([(tree, fn, macros)] of readable.analysis_trees) has the variable types
    it has, as lines (dread readable --types): the table's types and whether they were made for this C,
    the variables the plan may type and why each other one may not (eligible), the pointer groups with
    their accesses, and every candidate the plan tries in order, with the casts of the expressions it
    touches without and with it and whether it is kept; then the types those trials give. The trials
    run as the plan's last round does: on the C as it is built (finished, with context), the callees'
    prototypes and the functions' results as the table has them (p.prototypes)."""
    from dread import tidy
    names = context.struct_names() if context is not None else {}

    def shown(t):                           # (the table keeps its own structure names: as printed)
        return re.sub(r"\bstruct (\w+)", lambda m: "struct " + names.get(m.group(1), (m.group(1), {}))[0],
                      t) if t else t
    protos = {f: (shown(e[0]), [shown(t) for t in e[1:]]) for f, e in (getattr(p, "prototypes", None) or {}).items()}
    if context is not None:
        trees = [finished(tree, fn, p, context, (digests or {}).get(fn.name)) + (macros,)
                 for tree, fn, macros in trees]
    planner = Planner(trees, p, names=names)
    out = []
    for tree, fn, _ in trees:
        name = fn.name
        entry = (getattr(p, "variables", None) or {}).get(name) or {}
        table = ", ".join("%s %s" % (v, shown(t)) for v, t in sorted((entry.get("types") or {}).items())) or "none"
        made = entry.get("digest")
        mine = (digests or {}).get(name)
        note = "" if not entry or made is None or mine is None else (
            " (made for this C)" if made == mine else " (made for other C: not applied)")
        out.append("%s: the table's types %s%s" % (name, table, note))
        if name not in planner.facts:
            out.append("  not analysed: %s" % ("its registers are fixed (an asm statement binds them)"
                                                if tidy.fixed_registers(tree) else "not 32-bit registers"))
            continue
        why = {}
        plain = eligible(fn, why)
        fx = planner.facts[name]
        excluded = sorted(plain - fx.plain)
        out.append("  may type: %s" % (", ".join(sorted(fx.plain)) or "nothing"))
        for v in excluded:
            out.append("  not %s: a structure pointer of dread.shapes" % v)
        for v, reason in sorted(why.items()):
            out.append("  not %s: %s" % (v, reason))
        group, roots = fx.groups()
        by_root = {}
        for ctype, a, store in fx.accesses:
            b = fx.base(a, group, roots)
            if b is not None:
                by_root.setdefault(group[b], []).append("%s %s" % ("store" if store else "load", ctype))
        members = {}
        for v, r in group.items():
            members.setdefault(r, []).append(v)
        for r in sorted(by_root):
            acc = by_root[r]
            out.append("  group %s: %s" % (", ".join(sorted(members.get(r, [r]))), ", ".join(
                "%s x%d" % (k, acc.count(k)) for k in sorted(set(acc)))))
        planner.results[name] = protos.get(name, (None, []))[0]
        log = []
        typing, calls = planner.decide(name, protos, log)
        for kind, ms, params, t, before, after, kept in log:
            what = ", ".join(ms) or ", ".join("%s parameter %d" % (f, i) for f, i in params)
            out.append("  %s %s: %s, casts %d -> %d, %s" % (kind, what, t, before, after,
                                                         "kept" if kept else "not kept"))
        out.append("  typed: %s" % (", ".join("%s %s" % (v, t) for v, t in sorted(typing.items())) or "nothing"))
    return out


def finished(tree, fn, p, context, digest=None):
    """A copy of an analysis tree (readable.analysis_trees: the C before the structure stage, its
    memory at fixed addresses named) with what the plan's trials should see of the C as it is built:
    the static data's structures and arrays (a constant that is the address of one as (u32)&g_x[0]) and
    dread.shapes' structure pointers applied (dread.naming apply_globals, dread.shapes apply_types)."""
    import copy
    from dread import naming, shapes
    tree = copy.deepcopy(tree)
    fn = tree.function(fn.name)
    naming.apply_globals(tree, fn, context.globals, context.memory, statics=p.statics, names=context.static_names(),
                         arrays=p.arrays, pointers=p.params, image=context.image_spans, signed=p.signed)
    shapes.apply_types(tree, fn, p, digest, names=context.struct_names())
    return tree, fn


def plan(p, trees, digests=None, context=None):
    """Fills the readable_types plan p (dread.shapes Plan, its structures and typed variables made) with
    the variables' types (p.variables: {function: {"digest": what it was made for, "types": {variable:
    type}, "calls": {callee: {parameter index: type}}}}: the callees' parameters its externs declare
    with a type) and the prototypes ({function: [result or None, parameter types or None ...]}: its own
    readable C's) of the analysed functions (trees: [(tree, fn, macros)] of readable.analysis_trees;
    digests: theirs). context: a readable.Context whose tables the trials apply first (finished); the
    structures are named as its readable_names table names them there, by their plan's names in p.
    Returns p."""
    names = context.struct_names() if context is not None else {}
    if context is not None:
        trees = [finished(tree, fn, p, context, (digests or {}).get(fn.name)) + (macros,)
                 for tree, fn, macros in trees]
    planner = Planner(trees, p, names=names).run()
    back = {"struct " + names.get(k, (k, {}))[0]: "struct " + k for k in p.structs}

    def key(t):                             # (the plan keeps its own names: the table may change)
        return re.sub(r"\bstruct \w+", lambda m: back.get(m.group(0), m.group(0)), t) if t else t
    p.variables, p.prototypes = {}, {}
    for name in sorted(planner.facts):
        typing, calls = planner.typing[name], planner.calls[name]
        if typing or calls:
            entry = {"digest": (digests or {}).get(name), "types": {v: key(t) for v, t in sorted(typing.items())}}
            if calls:
                entry["calls"] = {f: {str(i): key(t) for i, t in sorted(ps.items())} for f, ps in sorted(calls.items())}
            p.variables[name] = entry
        res, params = planner.prototype(name)
        if res or any(params):
            p.prototypes[name] = [key(res)] + [key(t) for t in params]
    for name, res in sorted(planner.results.items()):
        if name not in planner.facts:       # (a callee not analysed: its result alone)
            p.prototypes[name] = [res]
    return p


# --- applying the plan to a function's readable C ---

EXTERN = re.compile(r"^extern (.+?)\b(\w+)\((.*)\);$", re.S)


def _named_structs(text, names):
    """A type text with the plan's structure names (struct s_3) as the readable_names table names them."""
    if not names:
        return text
    return re.sub(r"\bstruct (s_\d+)\b", lambda m: "struct " + names.get(m.group(1), (m.group(1), {}))[0], text)


def _decl_text(t, name):
    return "%s%s" % (t, name) if t.endswith("*") else "%s %s" % (t, name)


def object_arguments(tree, fn, p, names=None, int_bits=None):
    """After dread.frames made the function's objects: a callee's parameter that its extern in the file
    still declares u32 takes the callee's own pointer type (the readable_types table's prototypes) where
    every call of the function passes for it what C takes for that type without a cast: an object's
    address that dread.frames wrote as an integer ((u32)&obj, (u32)arr for an array, (u32)&obj.f), of
    the type the parameter points to, or 0; those casts go (FreeRTOS's xTaskDelayUntil(&stack36, 0x1388u)
    for xTaskDelayUntil((u32)&stack36, 0x1388u)). The rule of a file's externs (plan) for what only exists
    after the plan: the frame's objects, which the plan's trials never saw. Every cast it takes away is
    one less, none comes in. A parameter some call passes anything else (an integer, a sum) stays u32.
    Where the callee takes bytes (u8) and the objects are of other types, the extern declares a void
    pointer (void_param: a queue's item, xQueueGenericSend(q, &stack4, ...) for a u32 local, since
    2026-10-26). Returns whether anything changed."""
    from dread import polish, tidy
    prototypes = getattr(p, "prototypes", None) or {}
    if not prototypes or tidy.fixed_registers(tree):
        return False
    types = polish.Types(tree, fn, int_bits)
    externs = {}
    for k, item in enumerate(tree.items):
        m = EXTERN.match(item.text.strip()) if isinstance(item, C.Raw) else None
        if m and "..." not in m.group(3) and m.group(2) != fn.name and prototypes.get(m.group(2)):
            externs[m.group(2)] = k
    if not externs:
        return False
    calls = {}
    for st in polish._all_statements(fn.body.body):
        for e in polish._stmt_exprs(st):
            for x in polish._walk(e):
                if isinstance(x, C.Call) and isinstance(x.f, C.Name) and x.f.id in externs:
                    calls.setdefault(x.f.id, []).append(x)
    changed = False
    for f, found in sorted(calls.items()):
        k = externs[f]
        m = EXTERN.match(tree.items[k].text.strip())
        old = [x.strip() for x in m.group(3).split(",")] if m.group(3).strip() not in ("", "void") else []
        proto = prototypes[f]
        new, strip = list(old), []
        for i, t in enumerate(old):
            want = proto[i + 1] if i + 1 < len(proto) else None
            if " ".join(t.split()) != "u32" or not want or not want.rstrip().endswith("*"):
                continue
            want = _named_structs(want, names)
            target = types.parse(want)
            if not target or target[0] != "ptr" or target[1] is None:
                continue
            void = void_param(proto[i + 1])    # (a byte pointer: any object's address as void *)
            for typed in (True, False) if void else (True,):
                fits = []
                for call in found:
                    a = call.args[i] if i < len(call.args) else None
                    if isinstance(a, C.Num) and a.value == 0:
                        continue
                    if isinstance(a, C.Cast) and " ".join(a.type.split()) == "u32" and _object_address(a.e) and \
                            (not typed or _points_to(types.of(a.e), target[1])):
                        fits.append((call, i))
                        continue
                    fits = None
                    break
                if fits:
                    new[i] = want if typed else void[0]
                    strip += fits
                    break
        if strip:
            for call, i in strip:
                call.args[i] = call.args[i].e
            tree.items[k].text = "extern %s;" % head_text(m.group(1), f, ", ".join(new))
            changed = True
    return changed


def _object_address(e):
    """Whether e is an object's address as dread.frames writes one: &x, &x.f, &x[k], or an array's name."""
    if isinstance(e, C.Unary) and e.op == "&":
        x = e.e
        while isinstance(x, (C.Member, C.Index)) and not getattr(x, "arrow", False):
            x = x.base
        return isinstance(x, C.Name)
    return isinstance(e, C.Name)


def _points_to(d, pointee):
    """Whether a value of descriptor d (a pointer, or an array that decays to one) points to pointee."""
    return bool(d) and d[0] in ("ptr", "array") and d[1] is not None and d[1] == pointee


def apply(tree, fn, p, made_for=None, names=None, int_bits=None):
    """The plan's types written into one function's readable C (after dread.shapes apply_types, before
    the variables' names): its variables (only for the C the plan was made for: made_for, its digest
    before names; only those still declared u32 as the plan saw them), its parameters and result, its
    callees' externs (their results as their own readable C has them, their parameters where the plan
    found this file's calls fit them: the entry's calls; a call of itself as its head), every expression
    rewritten so that it computes what it did (Rewriter); a function with dread.shapes' structure
    pointers only gets their advances by a structure's size as p + 1. names: {s_N: (name, fields)} of
    the structures (dread.naming struct_names). Returns whether anything changed."""
    from dread import polish, tidy
    from dread.build import NULL_CHECKS, WRAPS
    variables = getattr(p, "variables", None) or {}
    prototypes = getattr(p, "prototypes", None) or {}
    if tidy.fixed_registers(tree):
        return False
    if (int_bits or tidy.context(fn).int_bits) != 32:
        return False
    entry = variables.get(fn.name) or {}
    typing, calls = {}, {}
    if entry and (made_for is None or entry.get("digest") in (None, made_for)):
        allowed = eligible(fn)
        typing = {v: _named_structs(t, names) for v, t in entry.get("types", {}).items() if v in allowed}
        calls = {f: {int(i): _named_structs(t, names) for i, t in ps.items()}
                 for f, ps in (entry.get("calls") or {}).items()}
    # a function the plan did not analyse (p.params lists every one it did; outside the lifted table:
    # printed on request) gets the negative rule on its own C (local_types)
    alone = getattr(p, "params", None) is not None and fn.name not in p.params
    protos = {}                             # callee -> (result, [parameters]) where its extern is in the file
    externs = {}
    for k, item in enumerate(tree.items):
        m = EXTERN.match(item.text.strip()) if isinstance(item, C.Raw) else None
        if not m or "..." in m.group(3) or m.group(2) == fn.name:
            continue
        f = m.group(2)
        res = (prototypes.get(f) or [None])[0]
        mine = calls.get(f, {})
        if res or mine:
            n = len([x for x in m.group(3).split(",") if x.strip() not in ("", "void")])
            protos[f] = (_named_structs(res, names) if res else None, [mine.get(i) for i in range(n)])
            externs[f] = k
    own = prototypes.get(fn.name)
    result = own[0] if own else None
    sizes = {"struct " + names.get(k, (k, {}))[0] if names else "struct " + k: p.size(k) for k in p.structs}
    if alone and not typing:
        typing = local_types(fn, polish.Types(tree, fn, int_bits), protos, result, sizes)
    if result or any(n in typing for _, n in fn.params):
        for k, item in enumerate(tree.items):   # (a function that calls itself declares itself: its head's types)
            m = EXTERN.match(item.text.strip()) if isinstance(item, C.Raw) else None
            if m and m.group(2) == fn.name:
                protos[fn.name] = (result, [typing.get(n) for _, n in fn.params])
                externs[fn.name] = k
    shaped = any("*" in t for t, _ in fn.params) or any(    # (dread.shapes' pointers: their arithmetic)
        isinstance(st, C.Decl) and any(d.ptr for d in st.items) for st in fn.body.body)
    if not typing and not protos and not result and not shaped:
        return False
    if tidy.fixed_head(fn):                 # (a wrapped function's body: its parameters as they are declared)
        typing = {v: t for v, t in typing.items() if v not in {n for _, n in fn.params}}
    tidy.split_typed(fn, set(typing))       # (a chain that assigns a typed variable: one statement each)
    types = polish.Types(tree, fn, int_bits)
    r = Rewriter(fn, typing, types, protos, result, sizes)
    for st in fn.body.body:
        r.stmt(st)
    # the externs: the callees' types (as many parameters as the extern has)
    for f, k in externs.items():
        m = EXTERN.match(tree.items[k].text.strip())
        res, params = protos[f]
        old = [x.strip() for x in m.group(3).split(",")] if m.group(3).strip() not in ("", "void") else []
        new = [params[i] if i < len(params) and params[i] else t for i, t in enumerate(old)]
        tree.items[k].text = "extern %s;" % head_text(res or m.group(1), f, ", ".join(new) or "void")
    # the head: its parameters' types and its result
    params = [(typing[n] if n in typing else t, n) for t, n in fn.params]
    m = head_match(fn)
    if m and (params != fn.params or (result and result != m.group(1))):
        fn.params = params
        fn.head = head_text(result or m.group(1), fn.name, ", ".join(_decl_text(t, n) for t, n in params) or "void")
    # the locals out of their u32 lines into lines of their types
    moved = {}
    for st in fn.body.body:
        if isinstance(st, C.Decl) and " ".join(st.specs.split()) == "u32":
            keep = []
            for d in st.items:
                if d.name in typing:
                    moved.setdefault(typing[d.name], []).append(d.name)
                else:
                    keep.append(d)
            st.items[:] = keep
    fn.body.body[:] = [st for st in fn.body.body if not (isinstance(st, C.Decl) and not st.items)]
    for t in sorted(moved, reverse=True):
        if t.endswith("*"):
            decl = C.Decl(t[:-1].strip(), [C.Declarator(n, ptr="*") for n in sorted(moved[t])])
        else:
            decl = C.Decl(t, [C.Declarator(n) for n in sorted(moved[t])])
        fn.body.body.insert(0, decl)
    # structures named by a type here that the file does not define: declared
    used = set()
    for t in list(typing.values()) + [x for _, ps in protos.values() for x in ps if x] + \
            [res for res, _ in protos.values() if res]:
        used.update(re.findall(r"\bstruct (\w+)", t))
    present = "\n".join(item.text for item in tree.items if isinstance(item, C.Raw))
    at = next((i + 1 for i, item in enumerate(tree.items)
               if isinstance(item, C.Raw) and item.text.startswith("#include")), tree.items.index(fn))
    for tag in sorted(used, reverse=True):
        if not re.search(r"^struct %s\b" % re.escape(tag), present, re.M):
            tree.items.insert(at, C.Raw("struct %s;" % tag))
    texts = {item.text for item in tree.items if isinstance(item, C.Raw)}
    if any(t.endswith("*") for t in typing.values()) and NULL_CHECKS not in texts:
        tree.items.insert(0, C.Raw(NULL_CHECKS))
    if (r.arithmetic or "s32" in typing.values()) and WRAPS not in texts:
        tree.items.insert(0, C.Raw(WRAPS))
    return True

