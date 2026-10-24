"""dread readable's last pass: constants and casts written as their use reads, on the finished tree.

The lifters write a constant as the instruction had it: hex with a u suffix (0x2f7u), a move of the
complement as the complement (mvn r0, #1: ~0x1u), a byte store's value with its cast ((u8)0x0u), a
compare's subtraction through 64 bits ((u32)((u64)(u32)p + 4 + 0)). After the stages and the tables
(dread.readable _named: the names, the structures, the frame's object) this pass writes each constant
by where its value goes, its use, and only where C gives the printed form the same meaning:

- the use decides what must stay the same (same_in): a value assigned, stored, returned or passed to a
  prototyped parameter is converted to that type, so any literal with the same converted value will
  do (-1 for 0xffffffffu stored into a u32; 0 for (u8)0x0u stored into a byte); an operand of an
  arithmetic or bitwise operation, or a select's arm, must keep the operation's type and its value in
  it (x + 7 for x + 0x7u with an unsigned x); an operand of a comparison must leave the set of values
  of the other operand for which the comparison holds as it was (c == ' ' for c == 0x20u, whether c is
  a byte or a word; never x == -1 for a 64-bit x: cmp_set computes the sets exactly, as intervals); a
  shift's count only its value. Where the other operand's type is not known (a call without a
  prototype, a member of a structure nothing declares), the literal keeps its type and value;
- negative numbers: a constant within NEGATIVE of the top of an unsigned type of int's width or wider
  (or a negative value of a signed type, a byte's too) is -k where it is a value: assigned, stored,
  returned, passed, a converted select's arm, compared for equality, compared in order where the
  comparison is signed (x < -2 with an unsigned x would read wrong), added (x + 0xfffffff8u is x - 8);
  it stays where it is a mask (an operand of &, |, ^, ~, also of &=, |=, ^=, a variable whose every
  read is one; a variable whose every read is one or a shift's left operand, ram_crc's crc; an argument
  for a callee's parameter that it uses only as a mask: mask_params, the readable_types table's masks,
  FreeRTOS's xTaskGenericNotifyWait(0, 0xffffffffu, ...)), in an unsigned byte (0xff), and in the
  lifters' compare idiom ((u64)x + (u32)~y + 1: no other literal keeps its type, and a complement inside
  a cast that stays is never made negative); all ones as such a mask is 0xffffffffu whatever the lifter
  wrote (~0x0u), another complement keeps its form (~0x20u);
- decimal: a constant below DECIMAL wherever the check lets a plain int stand (0 and 1 for 0x0u and
  0x1u; 1u where only an unsigned literal keeps the type); a larger one where its use is a number: a
  divisor, a modulus, a multiplier (multiplier: not a division's reciprocal, x * 0x5556u >> 16), a
  shift's count, a bound in an ordered comparison, or a constant assigned to, stored into, compared
  with or added to a place (a variable, a field, memory at a fixed address; a sum's constant also by
  the place the sum goes into) whose object holds numbers. One radix per object (_objects): the places
  of one object (a variable; an array's elements, a local's, static data's or a typed pointer's, x[k]
  and *p; a structure's field) and those a copy joins (x = y, *p = v) are decided together: an object
  holds numbers where a use makes one of its places a number (one of those uses, x += k), where its
  places are given (a select's constant arms each) or compared with two or more distinct such
  constants (line numbers stored into one error variable, a colour triple's 150 and 53), or where it
  takes a number the function writes in decimal elsewhere (a bound or its neighbour on the side it
  stands for, x <= 63 for x < 64; a divisor; a constant of an object that holds numbers: sizes stored
  through a pointer after the argument was compared with them); its constants below WIDE are all decimal
  then, a run of ones from bit 0 too (255 beside 63), unless all of them look like masks (x <= 0xffu
  alone stays). Never for a place that is a pointer (a counted address: r6 = 0x908u; *r6; r6++), an
  object given only texts' starts or functions' entries (pointed: a status message's texts on an image
  at 0), a device register (io: the target's device regions), constants that look like a mask or an
  address elsewhere (hexy: WIDE or more, a run of eight ones or more), and evidence from values that look
  like bit patterns or are all printable characters (a pad character's ' ' and '0') counts for nothing; a
  complement stored into a narrower place is the value it leaves there (~0x69u into a byte: 0x96u, or
  150);
- characters: a printable or common-escape constant as a character literal where a function handles
  text (it compares values that hold characters with two or more distinct such constants): compared
  with such a value, added to it or subtracted from it (c - '0', (c & ~0x20u) - 'A'), stored into a
  byte, a digit's character stored (d + '0'), or given to a variable that only ever gets such
  constants (a letter base: 'A', 'a'). A value holds characters when it is a byte loaded from memory,
  a variable given only such bytes (or small constants), or a variable given only callees' results
  (copies of them too) and stored as a byte (read_line's uart_getc result). 0 stays 0, as people write
  buf[n] = 0;
- casts: a cast to the type its operand has goes; a cast a conversion implies goes (the store's width:
  *(volatile u8 *)p = (u8)r12; not the cast that takes a 64-bit value's low word); a chain (T)(U)x is
  (T)x where U is at least as wide as T or holds every value of x's type; where only a value's low
  bits count (a sum converted to 32 bits), the casts that widen its operands go with the sum's width
  ((u32)((u64)(u32)p + 4) is p + 4), the operation keeping a type of that width that cannot overflow;
  a pointer cast of a pointer taken through an integer of 32 bits or more is a cast of the pointer
  ((const volatile u8 *)(u32)&stack28.f_0, where dread.frames wrote the address inside a cast
  dread.vartypes made: (const volatile u8 *)&stack28.f_0; the lifter's stack argument
  *(volatile u32 *)(u32)&s0: *(volatile u32 *)&s0, the same volatile access); an integer cast of a
  pointer stays ((u8)(u32)p: never (u8)p, and (u64)p would extend the pointer by its sign);
- constant expressions fold where the result is a number ((u8)0x0u, ((u64)0x0u << 32) | 0x0u, (u32)
  ((u64)0x0u + (u32)~0x8u + 1)), not where they show a mask or a comparison (0x20001913u & ~0x7u, if
  (0x80000000u == 0)); a select on a constant condition is its arm (the lifters' guard of a division by
  a constant); x + 0, x | 0, x ^ 0 and 0 + x go where they change no type; x + ~y + 1 is x - y and -y
  + x is x - y in unsigned arithmetic of int's width, (x - 4) + 1 is x - 3;
- increments: x = x op y is x op= y, x = x + 1 is x++ (statements and a for loop's step; x a
  variable, or a field, an element, a memory access whose address reads no memory); x = x of a local
  variable goes.

What the pass decides from is the function alone, but for its callees' mask parameters (the
readable_types table's masks, made by the check from every lifted function's C and from the C of the
callees outside them that one passes a constant that would read negative): a character that arrives as
a parameter (gizmo's handle_key compares its key with letters) is a number there; a place stored once
in this function and in decimal company elsewhere stays hex here; what a callee does with a value it
stores is not followed (a mask kept for later is no mask). A function's address and a text's are
dread.naming apply_globals's, before this pass ((u32)&name, and a text's number with the text in a
note, which keeps its literal as it is). Not done: a constant's name (an enumeration, a macro the
source had).
"""
import re

from dread import cexpr
from dread import cparse as C

NEGATIVE = 0x100        # a constant within this of the top of its unsigned type is -k where it is a value
DECIMAL = 10            # constants below this are decimal wherever C reads them the same
WIDE = 0x10000          # constants this large or larger stay hex (addresses, bit patterns)
COMPARE = ("==", "!=", "<", "<=", ">", ">=")
ORDER = ("<", "<=", ">", ">=")
FLIP = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!="}
COMPOUND = ("+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>")
LOW_BITS = ("+", "-", "*", "&", "|", "^")       # operations whose low n bits only the operands' low n bits make
ESCAPES = {9: "\\t", 10: "\\n", 13: "\\r", 39: "\\'", 92: "\\\\"}
INTS = {"u8": (8, False), "s8": (8, True), "u16": (16, False), "s16": (16, True), "u32": (32, False),
        "s32": (32, True), "u64": (64, False), "s64": (64, True)}
QUALIFIERS = ("volatile", "const", "register", "static", "extern")


# --- types: what the file declares ---

def _type_words(text):
    return [w for w in text.replace("*", " * ").split() if w not in QUALIFIERS]


class Types:
    """The types one readable file declares, for its function: the variables (declarations and
    parameters, pointers and arrays among them), the function's result, the externs' prototypes, the
    macros of memory at fixed addresses, the structures' fields (shapes, statics, the frame's
    typedef). Descriptors: ("int", (bits, signed)), ("ptr", descriptor or None), ("struct", key),
    ("array", descriptor); None where it is not known."""

    def __init__(self, tree, fn, int_bits=None):
        from dread import tidy
        self.ctx = tidy.context(fn)
        if int_bits is not None and int_bits != self.ctx.int_bits:
            self.ctx.int_bits = int_bits    # (after the names: the lifter's register names tell no more)
            self.ctx.long_bits = 32
        self.structs, self.macros, self.functions = {}, {}, {}
        for item in tree.items:
            if isinstance(item, C.Raw):
                self._raw(item.text)
        self.vars = {}
        for ptype, pname in fn.params:
            self.vars[pname] = self.parse(ptype)
        self._decls(fn.body.body)
        m = re.match(r"^\s*(.+?)\s*\b%s\s*\(" % re.escape(fn.name), fn.head or "")
        self.result = self.parse(m.group(1)) if m else None

    def _decls(self, stmts):
        for s in stmts:
            if isinstance(s, C.Decl):
                for d in s.items:
                    self.vars[d.name] = self.parse(s.specs, d.ptr, d.dims)
            elif isinstance(s, C.For) and isinstance(s.init, C.Decl):
                self._decls([s.init])
            for inner in _inner(s):
                self._decls([inner])

    def _raw(self, text):
        for line in text.split("\n"):
            m = re.match(r"^#define (\w+) \(\*\((.+?)\)(0x[0-9a-fA-F]+)[uUlL]*\)$", line)
            if m:
                self.macros[m.group(1)] = (self.parse(m.group(2) + ""), int(m.group(3), 16))
                continue
            m = re.match(r"^#define (\w+) \(\((.+?)\)(0x[0-9a-fA-F]+)[uUlL]*\)$", line)
            if m:                           # (an array of static data: its macro is the pointer)
                self.macros[m.group(1)] = (self.parse(m.group(2)), None)
        m = re.match(r"^extern (.+?)\b(\w+)\((.*)\);$", text.strip(), re.S)
        if m:
            params = [p.strip() for p in m.group(3).split(",")] if m.group(3).strip() not in ("", "void") else []
            self.functions[m.group(2)] = (self.parse(m.group(1)), None if "..." in params else
                                          [self.parse(p) for p in params])
            return
        m = re.match(r"^(typedef\s+)?struct\s*(\w*)\s*\{(.*)\}\s*(?:__attribute__\(\(.*?\)\)\s*)?(\w*)\s*;$",
                     text.strip(), re.S)
        if m:
            key = m.group(4) if m.group(1) else "struct " + m.group(2)
            fields = {}
            for f in re.finditer(r"^\s*((?:\w+\s+)*?\w+)\s*(\**)\s*(\w+)\s*((?:\[\d*\])*)\s*;", m.group(3), re.M):
                dims = re.findall(r"\[(\d*)\]", f.group(4))
                fields[f.group(3)] = (f.group(1), f.group(2), dims)
            self.structs[key] = fields

    def parse(self, text, ptr="", dims=()):
        """The descriptor of a type written as text (with a declarator's pointer stars and dims)."""
        words = _type_words(text)
        stars = words.count("*") + ptr.count("*")
        words = [w for w in words if w != "*"]
        if not words:
            return None
        base = " ".join(words)
        if base in INTS:
            d = ("int", INTS[base])
        elif words[0] == "struct" and len(words) == 2:
            d = ("struct", base)
        elif len(words) == 1 and words[0] in self.structs:
            d = ("struct", words[0])
        else:
            t = self.ctx.ctype(base)
            d = ("int", t) if t is not None else None
        for _ in range(stars):
            d = ("ptr", d)
        for _ in dims:
            d = ("array", d)
        return d

    def field(self, struct, name):
        f = self.structs.get(struct[1]) if struct and struct[0] == "struct" else None
        if f is None or name not in f:
            return None
        return self.parse(*f[name])

    def of(self, e):
        """The descriptor of an expression's type, or None."""
        if isinstance(e, C.Num):
            if isinstance(e.value, float):
                return None
            return ("int", cexpr.num_type(e.text, e.value, self.ctx))
        if isinstance(e, C.Name):
            if e.id in self.vars:
                return self.vars[e.id]
            if e.id in self.macros:
                return _pointee(self.macros[e.id][0]) if self.macros[e.id][1] is not None else self.macros[e.id][0]
            return None
        if isinstance(e, C.Cast):
            return self.parse(e.type)
        if isinstance(e, C.Unary):
            if e.op == "*":
                return _pointee(self.of(e.e))
            if e.op == "&":
                return ("ptr", self.of(e.e))
            if e.op == "!":
                return ("int", (self.ctx.int_bits, True))
            if e.op in ("-", "+", "~"):
                t = self.int(e.e)
                return ("int", cexpr.promote(t, self.ctx)) if t else None
            return self.of(e.e)             # (++ --)
        if isinstance(e, C.Post):
            return self.of(e.e)
        if isinstance(e, C.Binary):
            if e.op in COMPARE or e.op in ("&&", "||"):
                return ("int", (self.ctx.int_bits, True))
            if e.op in ("<<", ">>"):
                t = self.int(e.l)
                return ("int", cexpr.promote(t, self.ctx)) if t else None
            a, b = self.of(e.l), self.of(e.r)
            if a and b and a[0] == "int" and b[0] == "int":
                return ("int", cexpr.common(a[1], b[1], self.ctx))
            if e.op in ("+", "-") and a and a[0] in ("ptr", "array") and b and b[0] == "int":
                return a if a[0] == "ptr" else ("ptr", a[1])
            return None
        if isinstance(e, C.Cond):
            a, b = self.of(e.t), self.of(e.f)
            if a and b and a[0] == "int" and b[0] == "int":
                return ("int", cexpr.common(a[1], b[1], self.ctx))
            return a if a == b else None
        if isinstance(e, C.Assign):
            return self.of(e.target)
        if isinstance(e, C.Call):
            if isinstance(e.f, C.Name) and e.f.id in self.functions:
                return self.functions[e.f.id][0]
            return None
        if isinstance(e, C.Member):
            base = self.of(e.base)
            return self.field(_pointee(base) if e.arrow else base, e.name)
        if isinstance(e, C.Index):
            return _pointee(self.of(e.base))
        if isinstance(e, C.Comma):
            return self.of(e.items[-1]) if e.items else None
        return None

    def int(self, e):
        """(bits, signed) of an expression of integer type, or None."""
        d = self.of(e)
        return d[1] if d and d[0] == "int" else None

    def params(self, call):
        """The parameters' types of a call's callee where a prototype declares them (None for each
        argument otherwise, and for a variadic callee's)."""
        if isinstance(call.f, C.Name) and call.f.id in self.functions:
            ps = self.functions[call.f.id][1]
            if ps is not None and len(ps) == len(call.args):
                return [p[1] if p and p[0] == "int" else None for p in ps]
        if isinstance(call.f, C.Cast):
            m = re.match(r"^(.+?)\(\s*\*\s*\)\s*\((.*)\)$", call.f.type)
            if m and "..." not in m.group(2):
                ps = [p.strip() for p in m.group(2).split(",")] if m.group(2).strip() not in ("", "void") else []
                if len(ps) == len(call.args):
                    return [(lambda d: d[1] if d and d[0] == "int" else None)(self.parse(p)) for p in ps]
        return [None] * len(call.args)


def _pointee(d):
    return d[1] if d and d[0] in ("ptr", "array") else None


def _inner(s):
    if isinstance(s, C.Block):
        return list(s.body)
    if isinstance(s, C.If):
        return [s.then] + ([s.orelse] if s.orelse is not None else [])
    if isinstance(s, (C.While, C.DoWhile, C.For, C.Switch)):
        return [s.body]
    return []


# --- values and literals ---

def wrap(v, t):
    return cexpr.wrap(v, t)


def int_range(t):
    bits, signed = t
    return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)


def holds(t, u):
    """Whether type t holds every value of type u."""
    lo, hi = int_range(u)
    a, b = int_range(t)
    return a <= lo and hi <= b


def is_constant(e):
    if isinstance(e, C.Num):
        return not isinstance(e.value, float)
    if isinstance(e, C.Unary):
        return e.op in ("-", "+", "~", "!") and is_constant(e.e)
    if isinstance(e, C.Cast):
        return e.type in INTS and is_constant(e.e)
    if isinstance(e, C.Binary):
        return is_constant(e.l) and is_constant(e.r)
    if isinstance(e, C.Cond):
        return is_constant(e.c) and is_constant(e.t) and is_constant(e.f)
    return False


def value_of(e, ctx):
    """(value, (bits, signed)) of a constant expression with C's types, or None (not constant,
    undefined)."""
    if not is_constant(e):
        return None
    try:
        return cexpr.compile_expr(e, ctx)({})
    except (cexpr.Undefined, ValueError, TypeError):
        return None


def decimal(v, unsigned=False):
    """A literal for v in decimal: -k as a negation of k."""
    if v < 0:
        return C.Unary("-", C.Num("%d" % -v))
    return C.Num("%d%s" % (v, "u" if unsigned else ""))


def hexa(v):
    return C.Num("0x%xu" % v)


def char(v):
    text = ESCAPES.get(v) or chr(v)
    return C.Num("'%s'" % text, value=v)


def is_char_code(v):
    return 0x20 <= v <= 0x7e or v in (9, 10, 13)


def patterned(v):
    """Whether v looks like a bit pattern more than a number: one bit (16 or more), a run of four ones or
    more, one hex digit repeated (0x55, 0xaa, 0x3333). Such a value is no evidence that a place holds
    numbers (an event group's bits compared with 0x55)."""
    if v < 16:
        return False
    low = v & -v
    if v & (v - 1) == 0 or ((v + low) & (v + low - 1) == 0 and bin(v).count("1") >= 4):
        return True
    digits = "%x" % v
    return len(set(digits)) == 1


def masklike(v):
    """Whether v looks like a bit pattern and like no number (patterned, but not one bit: 64, 1024 are
    sizes as often, nor a run of ones from bit 0: 255 is a bound as often): 0x55, 0xaa, 0xf0."""
    return patterned(v) and v & (v - 1) != 0 and not low_run(v)


def low_run(v):
    """Whether v is a run of ones from bit 0 (255, 0x3ff, 0xffff: 2^k - 1, the bound x < 2^k becomes)."""
    return v > 0 and v & (v + 1) == 0


def hexy(v):
    """Whether v looks like a mask or an address where a number is expected, and stays hex: WIDE or
    more, or a run of eight ones or more (0xff, 0x3ff, 0xff00). One bit is a number there (256, 4096: a
    size, a count); so is a short run (0x1f0: line 496)."""
    if v >= WIDE:
        return True
    low = v & -v if v else 0
    return v >= 0xff and (v + low) & (v + low - 1) == 0 and bin(v).count("1") >= 8


# --- the sets of a comparison ---

def cmp_set(op, ta, const, left, ctx):
    """The values of the other operand (of type ta) for which `other op const` (const on the left:
    `const op other`) holds, as sorted disjoint intervals, with C's conversions: const = (value, type)."""
    c, tc = const
    t = cexpr.common(ta, tc, ctx)
    w = wrap(c, t)
    if left:
        op = FLIP[op]
    lo, hi = int_range(ta)
    if t[1] or not ta[1]:
        pieces = [(lo, hi, 0)]              # (every value of ta is itself in t)
    else:
        pieces = [(lo, -1, 1 << t[0]), (0, hi, 0)]     # (a negative value wraps round in unsigned t)
    out = []
    for a0, a1, off in pieces:
        if a0 > a1:
            continue
        k = w - off                         # a + off OP w, where a + off rises with a
        seg = {"==": [(k, k)], "!=": [(a0, k - 1), (k + 1, a1)], "<": [(a0, k - 1)], "<=": [(a0, k)],
               ">": [(k + 1, a1)], ">=": [(k, a1)]}[op]
        out += [(max(a0, x), min(a1, y)) for x, y in seg if max(a0, x) <= min(a1, y)]
    out.sort()
    merged = []
    for x, y in out:
        if merged and x <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], y))
        else:
            merged.append((x, y))
    return merged


# --- the pass ---

class Use:
    """Where a value goes. kind: "conv" (converted to type t, None: unknown), "same" (its type and value
    stay), "arith" (an operand of op beside a value of type t), "cmp" (compared by op with other, of
    type t; left: the constant is the left operand), "count" (a shift's count), "shifted" (a shift's
    left operand), "truth" (a condition). place: the text of the place a converted value goes into or
    a compared one is compared with (radix and characters); byte: a conversion into a byte in memory;
    top: the conversion of an assignment, a return or an argument (where a cast the target's width
    implies goes); neg: whether a negative number may stand for the value (not inside a cast that
    stays: (u32)-96 in the compare idiom would read worse than (u32)~0x5fu); mask: an argument for a
    callee's parameter that it uses only as a mask (mask_params)."""
    __slots__ = ("kind", "t", "op", "other", "left", "place", "byte", "top", "neg", "mask")

    def __init__(self, kind, t=None, op=None, other=None, left=False, place=None, byte=False, top=False, neg=True,
                 mask=False):
        self.kind, self.t, self.op, self.other, self.left = kind, t, op, other, left
        self.place, self.byte, self.top, self.neg, self.mask = place, byte, top, neg, mask


SAME = Use("same")


class Polisher:
    def __init__(self, tree, fn, int_bits=None, io=(), masks=None, pointed=None):
        self.fn = fn
        self.types = Types(tree, fn, int_bits)
        self.ctx = self.types.ctx
        self.io = list(io)
        self.masks_of = masks or {}         # callee -> the indices of its parameters it uses only as masks
        self.pointed = pointed              # (dread.constants Pointed: texts' starts and functions' entries)
        self.changed = False
        self.switch_t = None                # (the controlling expression's type of the switch around)
        self._facts()

    # -- what the function says of its places: masks, numbers, characters --

    def _facts(self):
        """Per variable: whether every read is a mask's operand (an operand of &, |, ^ or ~, also of &=, |=
        and ^=: masks), or a mask's operand or a shift's left operand (bitwise, ram_crc's crc: no negative
        number for it); per place: whether a use makes it a number; the variables that hold characters,
        and whether the function handles text."""
        reads, bit_reads, shift_reads = {}, {}, {}
        pointers = set()                    # variables a memory access reads through
        valued = {}                         # place -> {constants stored into it or compared with it for equality}
        assigned = {}                       # variable -> [values assigned]
        numbers = set()                     # places (texts) a use makes numbers
        byte_stored = set()                 # variables stored into a byte in memory
        compared = []                       # (other, constant value) of the comparisons with a constant
        objects = {}                        # place -> the object it belongs to (_object_key)
        links = []                          # (place, place) of copies: x = y, *p = v, v = p[k]
        bounds = {}                         # place -> {constants it is compared with in order}
        direct = set()                      # constants a use makes numbers: bounds and their neighbours,
                                            # divisors, multipliers

        def place_of(x):
            x = _strip_int_casts(x)
            if isinstance(x, (C.Name, C.Member, C.Index)) or (isinstance(x, C.Unary) and x.op == "*"):
                text = C.expr_text(x)
                if text not in objects:
                    objects[text] = _object_key(x) or text
                return text
            return None

        def address(x):
            """x is the address of a memory access: the variable it is (or that a constant offset is
            added to) is a pointer."""
            x = _strip_casts(x)
            if isinstance(x, C.Binary) and x.op in ("+", "-") and value_of(x.r, self.ctx) is not None:
                x = _strip_casts(x.l)
            if isinstance(x, C.Name):
                pointers.add(x.id)

        def visit(e, bits=False):
            if e is None:
                return
            if isinstance(e, C.Unary) and e.op == "*":
                address(e.e)
            elif isinstance(e, C.Member) and e.arrow:
                address(e.base)
            elif isinstance(e, C.Index):
                address(e.base)
            if isinstance(e, C.Name):
                reads[e.id] = reads.get(e.id, 0) + 1
                if bits == "shift":
                    shift_reads[e.id] = shift_reads.get(e.id, 0) + 1
                elif bits:
                    bit_reads[e.id] = bit_reads.get(e.id, 0) + 1
                return
            if isinstance(e, C.Assign):
                t = e.target
                k = value_of(e.value, self.ctx) if e.op == "=" else None
                p = place_of(t)
                if p is not None and e.op == "=":
                    pt = self.types.int(t)      # (the value the place gets: ~0x69u into a byte is 0x96; a
                    for arm in _arms(e.value):  # select's constant arms, c ? 0x40u : 0x100u, each one)
                        ka = value_of(arm, self.ctx)
                        if ka is not None:
                            valued.setdefault(("stored", p), set()).add(wrap(ka[0], pt) if pt else ka[0])
                if e.op == "=" and p is not None and k is None:
                    q = place_of(e.value)   # (a copy: one value in both places; not through an access at an
                    if q is not None and not _raw(t) and not _raw(e.value):    # address the code computes:
                        links.append((p, q))                                  # *(u32 *)a5 now and later)
                if isinstance(t, C.Name):
                    if e.op == "=":
                        assigned.setdefault(t.id, []).append(e.value)
                    else:
                        assigned.setdefault(t.id, []).append(None)
                        if e.op in ("+=", "-=") and value_of(e.value, self.ctx) is not None:
                            numbers.add(t.id)
                        # (x op= y reads x: a mask's operand for &=, |=, ^=, a shift's for <<=, >>=)
                        visit(t, True if e.op in ("&=", "|=", "^=") else "shift" if e.op in ("<<=", ">>=")
                              else False)
                else:
                    visit(t)
                d = self.types.of(t)
                if d and d[0] == "int" and d[1][0] == 8 and not isinstance(t, C.Name):
                    v = _strip_int_casts(e.value)
                    if isinstance(v, C.Name):
                        byte_stored.add(v.id)
                visit(e.value, e.op in ("&=", "|=", "^="))
                return
            if isinstance(e, (C.Post, C.Unary)) and e.op in ("++", "--"):
                p = place_of(e.e)
                if p:
                    numbers.add(p)
                if isinstance(e.e, C.Name):
                    assigned.setdefault(e.e.id, []).append(None)
                    reads[e.e.id] = reads.get(e.e.id, 0) + 1      # (a counter: no mask)
                else:
                    visit(e.e)
                return
            if isinstance(e, C.Binary):
                if e.op in ("&", "|", "^"):
                    visit(e.l, True)
                    visit(e.r, True)
                    return
                if e.op in ("<<", ">>"):
                    visit(e.l, "shift")
                    visit(e.r)
                    return
                if e.op in COMPARE:
                    for a, b in ((e.l, e.r), (e.r, e.l)):
                        k = value_of(b, self.ctx)
                        if k is not None and value_of(a, self.ctx) is None:
                            compared.append((a, k[0]))
                            p = place_of(a)
                            if p is not None and e.op in ("==", "!="):
                                valued.setdefault(("compared", p), set()).add(k[0])
                            if e.op in ORDER:
                                p = place_of(a)
                                if p:
                                    numbers.add(p)
                                    bounds.setdefault(p, set()).add(k[0])
                                op = e.op if a is e.l else FLIP[e.op]
                                # (x <= 63 for x < 64: a bound and its neighbour on the side it stands for)
                                direct.update((k[0], k[0] + 1) if op in ("<=", ">") else (k[0], k[0] - 1))
                if e.op in ("*", "/", "%"):
                    for a, b in ((e.l, e.r), (e.r, e.l)):
                        k = value_of(b, self.ctx)
                        if k is not None:
                            p = place_of(a)
                            if p:
                                numbers.add(p)
                            if e.op != "*" or self.multiplier(k[0]):
                                direct.add(k[0])
                visit(e.l)
                visit(e.r)
                return
            if isinstance(e, C.Unary) and e.op == "~":
                visit(e.e, True)
                return
            if isinstance(e, C.Cast) and e.type in INTS:
                visit(e.e, bits)            # ((u32)x & m: x a mask's operand all the same)
                return
            for c in _children(e):
                visit(c)

        def visit_stmt(s):
            if isinstance(s, C.Decl):
                for d in s.items:
                    if d.init is not None:
                        assigned.setdefault(d.name, []).append(d.init)
                        q = place_of(d.init) if not d.dims and not _raw(d.init) else None
                        if q is not None:   # (u32 r0_1 = r0;: a copy as x = y is)
                            objects.setdefault(d.name, d.name)
                            links.append((d.name, q))
                        visit(d.init)
            elif isinstance(s, C.Asm):
                for o in s.outputs:
                    if isinstance(o.e, C.Name):
                        assigned.setdefault(o.e.id, []).append(None)
                for o in s.inputs:
                    visit(o.e)
            else:
                for e in _stmt_exprs(s):
                    visit(e)
            for inner in _inner(s):
                visit_stmt(inner)
            if isinstance(s, C.For) and isinstance(s.init, C.Decl):
                visit_stmt(s.init)
        for s in self.fn.body.body:
            visit_stmt(s)
        self.masks = {v for v, n in reads.items() if n and bit_reads.get(v, 0) == n}
        self.bitwise = {v for v, n in reads.items() if n and bit_reads.get(v, 0) + shift_reads.get(v, 0) == n}
        # a variable counted up or down (x = x + k, x++) is a number
        for v, values in assigned.items():
            for x in values:
                if isinstance(x, C.Binary) and x.op in ("+", "-") and isinstance(x.l, C.Name) and x.l.id == v \
                        and value_of(x.r, self.ctx) is not None:
                    numbers.add(v)
        # a place given or compared with two or more numbers (10 or more, none a mask or an address):
        # line numbers stored into one error variable (the negative ones print as such anyway)
        for (_, p), values in valued.items():
            big = {v & 0xffffffffffffffff for v in values if abs(v) >= DECIMAL and not _near_top(v)}
            if len(big) >= 2 and not any(hexy(v) or patterned(v) for v in big) and \
                    not all(0x20 <= v <= 0x7e for v in big):
                numbers.add(p)              # (not ' ' and '0' for a pad character)
        self.pointers = pointers
        self.numbers = numbers - pointers   # (a pointer counted up is no number: r6 = 0x908u; *r6; r6++)
        self._objects(objects, links, valued, bounds, direct)
        # characters: bytes loaded from memory, variables that hold only those (or small constants), or
        # that hold a callee's result and are stored as a byte
        callish = set()                     # variables that hold only callees' results (copies of them too)
        while True:
            more = {v for v, values in assigned.items() if v not in callish and values and all(
                x is not None and (isinstance(_strip_int_casts(x), C.Call) or (
                    isinstance(_strip_int_casts(x), C.Name) and _strip_int_casts(x).id in callish))
                for x in values)}
            if not more:
                break
            callish |= more
        self._callish = callish
        chars = set()
        while True:
            more = set()
            for v, values in assigned.items():
                if v in chars or not values or any(x is None for x in values):
                    continue
                kinds = [self._char_source(x, chars) for x in values]
                if all(kinds) and ("byte" in kinds or (v in byte_stored and "call" in kinds)):
                    more.add(v)
            if not more:
                break
            chars |= more
        self.chars = chars
        seen = {k for other, k in compared if is_char_code(k) and self.char_typed(other)}
        self.text = len(seen) >= 2
        if self.text:                       # (tiny_print's letter base: r0_2 = 0x41u; ... r0_2 = 0x61u)
            for v, values in assigned.items():
                ks = [value_of(x, self.ctx) if x is not None else None for x in values]
                if v not in pointers and all(k is not None and is_char_code(k[0]) for k in ks) and \
                        len({k[0] for k in ks}) >= 2:
                    self.chars.add(v)

    def _objects(self, objects, links, valued, bounds, direct):
        """One radix per object: the places of one object (_object_key: a variable; an array's elements,
        a local's, static data's or a typed pointer's, x[k] and *p; a structure's field), and those a copy
        joins (x = y, *p = v, v = p[k]: one value in both), are decided together. An object holds numbers
        where a use makes one of its places a number (self.numbers), where its places together are given
        or compared with two or more distinct plain numbers (as one place's are), or where it takes a
        number the function writes in decimal elsewhere: a bound of an ordered comparison or its
        neighbour on the side it stands for (x <= 63 for x < 64: 64), a divisor, a multiplier, a constant
        of an object that holds numbers (sizes stored through a pointer after the argument was compared
        with them). Never one that holds a pointer, or only texts' starts and functions' entries (address:
        a status message's texts on an image at 0; a line number that falls on a text's start beside
        others is a number all the same); not by two or more values that are all printable characters (a
        pad character's ' ' and '0'), nor by a number taken where one of its own values looks like a bit
        pattern (masklike: an event group's 0x55 in a register a line number reuses). Such an object's
        constants are all decimal below WIDE, a run of ones from bit 0 (255 beside 63: the bound of x <
        256) too, unless all its constants of DECIMAL or more look like masks (hexy: x <= 0xffu alone stays
        as it is); a shifted run (0xff00) stays hex."""
        parent = {}

        def find(k):
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k
        for k in objects.values():
            parent.setdefault(k, k)
        for a, b in links:
            ra, rb = find(objects[a]), find(objects[b])
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
        groups = {}
        for p, k in objects.items():
            groups.setdefault(find(k), set()).add(p)
        values, evidence, pointer = {}, {}, {}
        for root, ps in groups.items():
            vs = set()
            for p in ps:
                vs |= valued.get(("stored", p), set()) | valued.get(("compared", p), set()) | bounds.get(p, set())
            big = {v & 0xffffffffffffffff for v in vs if abs(v) >= DECIMAL and not _near_top(v)}
            values[root] = big
            # (an object given only texts' starts or functions' entries holds addresses: a status message's
            # texts on an image at 0 are small numbers, 0x1e2u, never 482; a line number that falls on a
            # text's start among others is a number all the same)
            pointer[root] = any(p in self.pointers for p in ps) or (bool(big) and all(self.address(v) for v in big))
            pooled = len(big) >= 2 and not any(hexy(v) or patterned(v) for v in big) and \
                not all(0x20 <= v <= 0x7e for v in big)
            evidence[root] = not pointer[root] and (pooled or any(p in self.numbers for p in ps))

        def decimal(root):
            big = {v for v in values[root] if v < WIDE}
            return evidence[root] and (not big or any(not hexy(v) for v in big))
        chosen = {r for r in groups if decimal(r)}
        known = {v for v in direct if DECIMAL <= v < WIDE}
        for r in chosen:
            known |= {v for v in values[r] if v < WIDE}
        more = True
        while more:                         # (a number taken in one object makes it one too)
            more = False
            for r in groups:
                if r in chosen or pointer[r] or not values[r] & known or any(masklike(v) for v in values[r]) or \
                        (len(values[r]) >= 2 and all(0x20 <= v <= 0x7e for v in values[r])):
                    continue                # (an event group's 0x55 beside a line number: a register reused;
                                            # a pad character's ' ' and '0')
                evidence[r] = True
                if decimal(r):
                    chosen.add(r)
                    known |= {v for v in values[r] if v < WIDE}
                    more = True
        self.decimal_objects = chosen
        self.place_root = {p: find(k) for p, k in objects.items()}

    def address(self, v):
        """Whether v is a text's start or a function's entry in the image (dread.constants Pointed; never
        below its SMALLEST_POINTER): what an object given it holds is addresses."""
        p = self.pointed
        if p is None or v >= 1 << 32:
            return False
        return p.function(v) is not None or (p.text(v) is not None and p.begins(v))

    def placed(self, place):
        """Whether a place holds numbers: its object does (one radix per object, _objects)."""
        if place is None:
            return False
        root = self.place_root.get(place)
        return root in self.decimal_objects if root is not None else place in self.numbers

    def _char_source(self, x, chars):
        """What makes x a character's value: "byte" (a byte loaded from memory, a variable of chars),
        "call" (a callee's result), "small" (a constant below 256), or None."""
        x = _strip_int_casts(x)
        if self._byte_load(x) or (isinstance(x, C.Name) and x.id in chars):
            return "byte"
        if isinstance(x, C.Call) or (isinstance(x, C.Name) and x.id in self._callish):
            return "call"
        k = value_of(x, self.ctx)
        if k is not None and 0 <= k[0] < 256:
            return "small"
        return None

    def _byte_load(self, x):
        if isinstance(x, (C.Unary, C.Member, C.Index)) and not (isinstance(x, C.Unary) and x.op != "*"):
            d = self.types.of(x)
            return bool(d and d[0] == "int" and d[1][0] == 8)
        return False

    def char_typed(self, e, masked=False):
        """Whether e holds a character: a byte loaded from memory, or a variable of chars (masked: also
        such a value under a mask, c & ~0x20u)."""
        x = _strip_int_casts(e)
        if masked and isinstance(x, C.Binary) and x.op == "&":
            return any(self.char_typed(y) for y in (x.l, x.r))
        return self._byte_load(x) or (isinstance(x, C.Name) and x.id in self.chars)

    def is_io(self, place_expr):
        """Whether a place is memory at a fixed address in a device region (a register)."""
        x = place_expr
        while isinstance(x, (C.Member, C.Index)):
            x = x.base
        if isinstance(x, C.Name) and x.id in self.types.macros:
            address = self.types.macros[x.id][1]
            return address is not None and any(lo <= address < hi for lo, hi in self.io)
        if isinstance(x, C.Unary) and x.op == "*":
            k = value_of(_strip_casts(x.e), self.ctx)
            return k is not None and any(lo <= k[0] < hi for lo, hi in self.io)
        return False

    # -- choosing a literal --

    def same_in(self, use, old, new):
        kind = use.kind
        if kind == "conv":
            if use.t is None:
                return old[0] == new[0]
            return wrap(old[0], use.t) == wrap(new[0], use.t)
        if kind in ("arith", "arm"):
            if use.t is None:
                return old == new
            a, b = cexpr.common(use.t, old[1], self.ctx), cexpr.common(use.t, new[1], self.ctx)
            return a == b and wrap(old[0], a) == wrap(new[0], b)
        if kind == "shifted":
            p = lambda t: cexpr.promote(t, self.ctx)     # noqa: E731
            return p(old[1]) == p(new[1]) and old[0] == new[0]
        if kind == "count":
            return old[0] == new[0]
        if kind == "truth":
            return bool(old[0]) == bool(new[0])
        if kind == "cmp":
            if use.t is None:
                return old == new
            return cmp_set(use.op, use.t, old, use.left, self.ctx) == cmp_set(use.op, use.t, new, use.left, self.ctx)
        return old == new

    def negative(self, v, t, use):
        """The negative number -k a value v of type t stands for in use, or None: v within NEGATIVE of
        the top of an unsigned type of int's width or wider, or a signed type's negative value (the converted
        value where the use converts), and the use a value's."""
        if not use.neg:
            return None
        if use.kind == "conv" and use.t is not None:
            t = use.t
            v = wrap(v, t)
        elif use.kind == "arm":
            t = cexpr.common(use.t, t, self.ctx)
            v = wrap(v, t)
        elif use.kind not in ("cmp", "arith"):
            return None
        bits, signed = t
        if signed:                          # (a signed target: its negative values, a byte's too)
            return v if -NEGATIVE <= v < 0 else None
        if bits < self.ctx.int_bits:        # (an unsigned byte's 0xff stays)
            return None
        top = 1 << bits
        return v - top if top - NEGATIVE <= v < top else None

    def number(self, use, v):
        """Whether the use makes a constant v a number (decimal unless it looks like a mask)."""
        if use.kind == "arith":
            return use.op in ("/", "%") or (use.op == "*" and self.multiplier(v)) or \
                (use.op in ("+", "-") and self.placed(use.place))
        if use.kind == "cmp":
            return use.op in ORDER or self.placed(use.place)
        if use.kind == "conv":
            return self.placed(use.place)
        return False

    def multiplier(self, v):
        """Whether a multiplier is a number (decimal): below 0x1000 or a power of ten; a larger one is a
        reciprocal of a division (x * 0x5556u >> 16) or a mask."""
        return v < 0x1000 or (v >= 10 and str(v).rstrip("0") == "1")

    def candidates(self, e, k, use):
        """Literals to try for the constant e (value k) in use, best first."""
        v, t = k
        out = []
        if use.kind in ("cmp", "conv") and self.text and is_char_code(wrap(v, use.t or t) if use.kind == "conv"
                                                                     else v):
            w = wrap(v, use.t or t) if use.kind == "conv" else v
            if (use.kind == "cmp" and use.other is not None and self.char_typed(use.other)) or \
                    (use.kind == "conv" and (use.byte or use.place in self.chars)):
                out.append(char(w))
        masky = use.kind == "arith" and use.op in ("&", "|", "^") or use.kind == "shifted" or \
            (use.kind == "conv" and use.place in self.masks) or use.mask
        bitwise = masky or (use.kind == "conv" and use.place in self.bitwise)
        if bitwise and use.kind == "conv":  # (all ones as a mask: 0xffffffffu for ~0x0u, never -1)
            ct = use.t or t
            if ct[0] >= self.ctx.int_bits and wrap(v, ct) == (1 << ct[0]) - 1 and _complement(e):
                out.append(hexa((1 << ct[0]) - 1))
        n = None if bitwise else self.negative(v, t, use)
        if n is not None and not (use.kind == "cmp" and use.op in ORDER) and \
                not (use.kind == "arith" and use.op not in ("+", "-")) and not (use.kind == "arm" and not use.neg):
            out.append(decimal(n))
        elif n is not None and use.kind == "cmp":
            out.append(("signed", decimal(n)))
        w = wrap(v, use.t) if use.kind == "conv" and use.t is not None else v
        if 0 <= w < DECIMAL or use.kind == "count" or (not masky and 0 <= w < WIDE and
                                                       (not hexy(w) or (low_run(w) and self.placed(use.place)))
                                                       and
                                                       self.number(use, w)):
            out += [decimal(w), decimal(w, unsigned=True)]
        # (a complement stored into a narrower place is the value it leaves there: ~0x69u into a byte, 0x96u)
        narrowed = _complement(e) and use.kind == "conv" and use.t is not None and use.t[0] < self.ctx.int_bits \
            and not bitwise
        if w >= 0 and (narrowed or not _complement(e)) and not isinstance(e, C.Num):
            out.append(hexa(w))             # (a cast of a literal, a sum of literals: folded)
        return out

    def literal(self, e, k, use):
        """The literal e (a constant expression of value k) is written as in use, or None (as it is)."""
        for cand in self.candidates(e, k, use):
            signed_only = isinstance(cand, tuple)
            if signed_only:
                cand = cand[1]
            got = value_of(cand, self.ctx)
            if got is None or not self.same_in(use, k, got):
                continue
            if signed_only and not cexpr.common(use.t, got[1], self.ctx)[1]:
                continue                    # (x < -2 with an unsigned x reads wrong)
            return cand
        return None

    # -- rewriting --

    def expr(self, e, use=SAME):
        out = self._expr(e, use)
        if C.expr_text(out) != C.expr_text(e):
            self.changed = True
        return out

    def _expr(self, e, use):
        if e is None:
            return None
        k = value_of(e, self.ctx)
        if k is not None and not _keeps_form(e):
            lit = self.literal(e, k, use)
            if lit is not None:
                return lit
        if isinstance(e, (C.Name, C.Num, C.Str)):
            return e
        if isinstance(e, C.Cast):
            return self._cast(e, use)
        if isinstance(e, C.Binary):
            return self._binary(e, use)
        if isinstance(e, C.Unary):
            if e.op in ("-", "~") and use.kind == "conv" and use.t is not None:
                inner = self._narrowed(e.e, use.t)
                if inner is not None:
                    new = C.Unary(e.op, inner)
                    if self._width_ok(e, new, use.t):
                        return new
            if e.op in ("!",):
                return C.Unary(e.op, self._expr(e.e, Use("truth")))
            if e.op == "~" and value_of(e.e, self.ctx) is not None:
                return e                    # (a mask's complement keeps its form: ~0x7u)
            return C.Unary(e.op, self._expr(e.e, SAME))
        if isinstance(e, C.Post):
            return C.Post(e.op, self._expr(e.e, SAME))
        if isinstance(e, C.Cond):
            return self._cond(e, use)
        if isinstance(e, C.Assign):
            return self._assign(e)
        if isinstance(e, C.Call):
            ts = self.types.params(e)
            ms = self.masks_of.get(e.f.id, ()) if isinstance(e.f, C.Name) else ()
            return C.Call(self._expr(e.f, SAME), [self._expr(a, Use("conv", t, top=True, mask=i in ms) if t else SAME)
                                                  for i, (a, t) in enumerate(zip(e.args, ts))])
        if isinstance(e, C.Index):
            return C.Index(self._expr(e.base, SAME), self._expr(e.index, SAME))
        if isinstance(e, C.Member):
            return C.Member(self._expr(e.base, SAME), e.name, e.arrow)
        if isinstance(e, C.Comma):
            return C.Comma([self._expr(x, SAME) for x in e.items])
        return e

    def _cast(self, e, use):
        t = self.types.parse(e.type)
        if t and t[0] == "ptr" and isinstance(e.e, C.Cast) and INTS.get(e.e.type, (0,))[0] >= 32:
            inner = self.types.of(e.e.e)    # ((T *)(u32)&stack28.f_0, a pointer through a word: (T *)&stack28.f_0)
            if inner is not None and inner[0] in ("ptr", "array"):
                return C.Cast(e.type, self._expr(e.e.e, SAME))
        if not t or t[0] != "int":
            return C.Cast(e.type, self._expr(e.e, SAME))
        t = t[1]
        inner_t = self.types.int(e.e)
        # a cast the conversion implies (the store's width, the parameter's type)
        if use.kind == "conv" and use.t is not None and inner_t is not None and (
                (use.top and t[0] >= use.t[0] and inner_t[0] <= max(32, self.ctx.int_bits)) or holds(t, inner_t)):
            # ((u8)r12 stored into a byte, (u32)x of a u8 x; not (u32)t of a 64-bit t: its low word is
            # the point there)
            return self._expr(e.e, Use("conv", use.t, place=use.place, byte=use.byte, top=use.top, neg=use.neg))
        # a chain (t)(u)x: (t)x where u is as wide as t, or holds every value of x
        inner = e.e
        while isinstance(inner, C.Cast) and inner.type in INTS:
            u = INTS[inner.type]
            xt = self.types.int(inner.e)
            if xt is not None and (u[0] >= t[0] or holds(u, xt)):
                inner = inner.e
                continue
            break
        new_inner = self._narrowed(inner, t)
        if new_inner is None or not self._width_ok(inner, new_inner, t):
            new_inner = self._expr(inner, Use("conv", t, neg=False))
        if self.types.int(new_inner) == t and not isinstance(new_inner, C.Num):
            return new_inner                # (a cast to the type its operand has)
        k = value_of(new_inner, self.ctx)
        if k is not None and k[1] == t:
            return new_inner
        return C.Cast(e.type, new_inner)

    def _narrowed(self, e, t, place=None):
        """e rewritten where only its value converted to t matters, when it is a sum or the like of
        low bits (LOW_BITS) whose operands may then lose casts that widen beyond t (the lifters' flags
        arithmetic through 64 bits); its constants are then written for the operation as it is
        (_arith); None when it is no such operation."""
        if isinstance(e, C.Binary) and e.op in LOW_BITS:
            l, r = self._narrow_operand(e.l, t), self._narrow_operand(e.r, t)
            if l is e.l and r is e.r:
                return None
            return self._arith(C.Binary(e.op, l, r), Use("conv", t, place=place), narrow=t)
        return None

    def _narrow_operand(self, x, t):
        """x without the casts that widen it beyond t (a sum of such operands inside it too), or x
        itself when there are none."""
        y = x
        while isinstance(y, C.Cast) and y.type in INTS and INTS[y.type][0] >= t[0]:
            yt = self.types.int(y.e)
            if yt is None or yt[0] > max(t[0], self.ctx.int_bits, INTS[y.type][0]) or \
                    (yt[0] > 32 and t[0] <= 32):
                break                       # (not the low word of a 64-bit value)
            y = y.e
        if isinstance(y, C.Binary) and y.op in LOW_BITS:
            got = self._narrowed(y, t)
            if got is not None and self._width_ok(y, got, t):
                return got
        return y

    def _width_ok(self, old, new, t):
        """Whether new computes old's value converted to t: its type is old's, or unsigned and at least
        t's width (a sum's low bits), or a bitwise operation's of that width."""
        a, b = self.types.int(old), self.types.int(new)
        if b is None:
            return False
        if a == b:
            return True
        if b[0] < t[0]:
            return False
        if not b[1] or (isinstance(new, (C.Binary, C.Unary)) and new.op in ("&", "|", "^", "~")):
            return True
        # a signed int sum of operands narrower than int (or small constants) does not overflow
        return isinstance(new, C.Binary) and new.op in ("+", "-") and all(self._small(x) for x in (new.l, new.r))

    def _small(self, x):
        """Whether x's values lie well inside int (a type narrower than int, a small constant)."""
        k = value_of(x, self.ctx)
        if k is not None:
            return abs(k[0]) < 1 << (self.ctx.int_bits - 2)
        t = self.types.int(x)
        return t is not None and t[0] < self.ctx.int_bits

    def _binary(self, e, use):
        if e.op in ("&&", "||"):
            return C.Binary(e.op, self._expr(e.l, Use("truth")), self._expr(e.r, Use("truth")))
        if e.op in COMPARE:
            lt, rt = self.types.int(e.l), self.types.int(e.r)
            lk, rk = value_of(e.l, self.ctx), value_of(e.r, self.ctx)
            l = self._expr(e.l, Use("cmp", rt, e.op, e.r, False, self._place(e.r)) if lk is not None and rk is None
                           else SAME)
            r = self._expr(e.r, Use("cmp", lt, e.op, e.l, True, self._place(e.l)) if rk is not None and lk is None
                           else SAME)
            # (the other operand's type is read before it is rewritten: what is not constant keeps its type)
            return C.Binary(e.op, l, r)
        if e.op in ("<<", ">>"):
            l = self._expr(e.l, Use("shifted") if value_of(e.l, self.ctx) is not None else SAME)
            return C.Binary(e.op, l, self._expr(e.r, Use("count")))
        if use.kind == "conv" and use.t is not None and e.op in LOW_BITS:
            got = self._narrowed(e, use.t, use.place)
            if got is not None and self._width_ok(e, got, use.t):
                return got
        return self._arith(e, use)

    def _arith(self, e, use, done=False, narrow=None):
        """An arithmetic or bitwise operation with its constant operands written for it, x + 0 and the
        like gone, x + (-k) as x - k, (x - k1) + k2 as one constant (unsigned). narrow: the type t
        whose width alone counts (_narrowed): a constant of a sum or a difference then needs only its
        value in t's width (c - '0' where c is a byte and the sum lands in a u32); the caller checks
        the operation's type (_width_ok)."""
        lk, rk = value_of(e.l, self.ctx), value_of(e.r, self.ctx)
        lt, rt = self.types.int(e.l), self.types.int(e.r)

        def const_use(other_t, other):
            # (a sum's constant is a number where the other operand is, or the place the sum goes into)
            place = self._place(other) or (use.place if use.kind == "conv" and e.op in ("+", "-") else None)
            if narrow is not None and e.op in ("+", "-"):
                return Use("conv", narrow, place=place, op=e.op, other=other)
            return Use("arith", other_t, e.op, place=place)
        def operand_use(x):
            """A sum inside a sum that goes into a place goes there too ((x + 0x3000u) + 0x39u stored into a
            number: both constants numbers, then one, x + 12345)."""
            if e.op in ("+", "-") and use.kind == "conv" and use.place is not None and \
                    isinstance(x, C.Binary) and x.op in ("+", "-"):
                return Use("conv", None, place=use.place)
            return SAME
        if done:
            l, r = e.l, e.r
        else:
            l = self._expr(e.l, const_use(rt, e.r) if lk is not None and rk is None else operand_use(e.l))
            r = self._expr(e.r, const_use(lt, e.l) if rk is not None and lk is None else operand_use(e.r))
        new = C.Binary(e.op, l, r)
        # x + 0, x - 0, x | 0, x ^ 0, 0 + x, 0 | x, 0 ^ x: where no type changes (or only the converted value counts)
        for keep, zero, ok_ops in ((l, r, ("+", "-", "|", "^")), (r, l, ("+", "|", "^"))):
            z = value_of(zero, self.ctx)
            if z is not None and z[0] == 0 and e.op in ok_ops and value_of(keep, self.ctx) is None:
                kt, nt = self.types.int(keep), self.types.int(new)
                if kt is not None and nt is not None and (kt == nt or (use.kind == "conv" and use.t is not None
                                                                       and use.t[0] <= min(kt[0], nt[0]))):
                    return keep
        # x + (-k) is x - k, x - (-k) is x + k, (-k) + x is x - k
        if e.op in ("+", "-"):
            new = _flip_negative(new)
            new = self._negation(new)
            new = self._reassociate(new)
            new = self._char_operand(new, stored=use.kind == "conv" and use.byte)
        return new

    def _negation(self, e):
        """x + ~y + 1 as x - y and ~y + 1 as -y where the sum is unsigned of int's width (~y + 1 is -y
        there; not in a 64-bit sum, where ~y of a 32-bit y is no negation: the lifters' carry)."""
        t = self.types.int(e)
        if e.op == "+" and t == (self.ctx.int_bits, False):
            for a, b in ((e.l, e.r), (e.r, e.l)):     # (-x + y, y + -x: y - x)
                if isinstance(a, C.Unary) and a.op == "-" and value_of(a, self.ctx) is None and \
                        self.types.int(a) == t:
                    return C.Binary("-", b, a.e)
        if not (e.op == "+" and _plain_number(e.r) == 1 and t == (self.ctx.int_bits, False)):
            return e
        inner = e.l
        if isinstance(inner, C.Unary) and inner.op == "~" and self.types.int(inner) == t:
            return C.Unary("-", inner.e)
        if isinstance(inner, C.Binary) and inner.op == "+" and isinstance(inner.r, C.Unary) and inner.r.op == "~" \
                and self.types.int(inner) == t and self.types.int(inner.r) == t:
            if value_of(inner.l, self.ctx) is not None and value_of(inner.l, self.ctx)[0] == 0:
                return C.Unary("-", inner.r.e)
            return C.Binary("-", inner.l, inner.r.e)
        return e

    def _char_operand(self, e, stored=False):
        """c - 0x61u, c + 0x30u as c - 'a', c + '0' where c holds characters (char_typed, also through a
        mask: (c & ~0x20u) - 'A') in a function that handles text, or (stored) where the sum is stored
        into a byte there (a digit's character: d + '0'); the same type and value."""
        if not (self.text and isinstance(e, C.Binary) and e.op in ("+", "-")):
            return e
        k = value_of(e.r, self.ctx)
        if k is None or not is_char_code(k[0]) or not (
                self.char_typed(e.l, masked=True) or (stored and e.op == "+" and k[0] >= 0x20)):
            return e
        lit = char(k[0])
        if not self.same_in(Use("arith", self.types.int(e.l), e.op), k, value_of(lit, self.ctx)):
            return e
        return C.Binary(e.op, e.l, lit)

    def _reassociate(self, e):
        """(x ± k1) ± k2 as x ± k in unsigned arithmetic of int's width or wider (the same value), x when
        k is 0; others as they are."""
        if not (isinstance(e, C.Binary) and e.op in ("+", "-") and isinstance(e.l, C.Binary) and e.l.op in ("+", "-")):
            return e
        k2, k1 = _plain_number(e.r), _plain_number(e.l.r)
        t = self.types.int(e)
        if k1 is None or k2 is None or t is None or t[1] or t[0] < self.ctx.int_bits or \
                self.types.int(e.l) != t or value_of(e.l.l, self.ctx) is not None:
            return e
        total = (k1 if e.l.op == "+" else -k1) + (k2 if e.op == "+" else -k2)
        x = e.l.l
        if total == 0:
            return x
        new = C.Binary("+" if total > 0 else "-", x, C.Num("%d" % abs(total)))
        return new if self.types.int(new) == t else e

    def _cond(self, e, use):
        ck = value_of(e.c, self.ctx)
        if ck is not None and value_of(e, self.ctx) is None:
            arm = e.t if ck[0] else e.f     # (the lifters' guard of a division by a constant: 0xau ? r1 / 10 : 0)
            s, a = self.types.int(e), self.types.int(arm)
            if s is not None and (a == s or (use.kind == "conv" and use.t is not None and a is not None
                                             and use.t[0] <= min(a[0], s[0]))):
                return self._expr(arm, use)
        c = self._expr(e.c, Use("truth"))
        tk, fk = value_of(e.t, self.ctx), value_of(e.f, self.ctx)
        s = self.types.int(e)
        if use.kind == "conv" and use.t is not None and s is not None:
            # converted to use.t: both arms written for that conversion, where each converts as before
            # through the select's type, old and new (return r6 != 0 ? 0 : !z ? 1 : 0 for 0u, 1u, 0u)
            sub = Use("conv", use.t, place=use.place, byte=use.byte, neg=use.neg)
            t2, f2 = self._expr(e.t, sub), self._expr(e.f, sub)
            s2 = self.types.int(C.Cond(c, t2, f2))
            if s2 is not None and self._arm_same(e.t, t2, s, s2, use.t) and self._arm_same(e.f, f2, s, s2, use.t):
                return C.Cond(c, t2, f2)
        if use.kind == "conv" and use.t is not None and tk is not None and fk is not None and s is not None:
            # both arms constants: each arm's literal by itself under the conversion to use.t (the arms
            # take their common type first)
            sub = Use("conv", use.t, place=use.place, byte=use.byte)
            for t_lit, f_lit in self._arm_pairs(e, tk, fk, sub):
                got_t, got_f = value_of(t_lit, self.ctx), value_of(f_lit, self.ctx)
                s2 = cexpr.common(got_t[1], got_f[1], self.ctx)
                if wrap(wrap(got_t[0], s2), use.t) == wrap(wrap(tk[0], s), use.t) and \
                        wrap(wrap(got_f[0], s2), use.t) == wrap(wrap(fk[0], s), use.t):
                    return C.Cond(c, t_lit, f_lit)
        # one arm a constant: converted to use.t, any literal will do whose select converts the other arm
        # as before (both select types hold its values, or are as wide as use.t) and gives the constant's
        # value converted (x = c ? h : -1 for ~0x0u with a u16 h); else written so that the select keeps
        # its type and the arm its value in it (return n ? (u32)(u8)r0 : -1)
        t_use = f_use = SAME
        if s is not None and (tk is None) != (fk is None):
            other = e.f if tk is not None else e.t
            other_t, k = self.types.int(other), tk if tk is not None else fk
            const = e.t if tk is not None else e.f
            if other_t is not None and use.kind == "conv" and use.t is not None and not _keeps_form(const):
                sub = Use("conv", use.t, place=use.place, byte=use.byte)
                for lit in [x for x in self.candidates(const, k, sub) if not isinstance(x, tuple)]:
                    got = value_of(lit, self.ctx)
                    s2 = cexpr.common(other_t, got[1], self.ctx) if got is not None else None
                    if s2 is None or wrap(wrap(got[0], s2), use.t) != wrap(wrap(k[0], s), use.t):
                        continue
                    if (holds(s, other_t) and holds(s2, other_t)) or use.t[0] <= min(s[0], s2[0]):
                        arms = (lit, self._expr(other, SAME)) if tk is not None else (self._expr(other, SAME), lit)
                        return C.Cond(c, *arms)
            if other_t is not None:
                arm = Use("arm", other_t, op="?:", place=use.place if use.kind == "conv" else None,
                          neg=use.kind == "conv")
                if tk is not None:
                    t_use = arm
                else:
                    f_use = arm
        t = self._expr(e.t, t_use)
        f = self._expr(e.f, f_use)
        return C.Cond(c, t, f)

    def _arm_same(self, old, new, s, s2, t):
        """Whether an arm new (written for the conversion to t) gives what old gave, the select's type
        s before and s2 after: a constant by its value through both; another arm by its type, each
        select type holding its values or being as wide as t (its conversion to t is then the arm's)."""
        ko, kn = value_of(old, self.ctx), value_of(new, self.ctx)
        if ko is not None and kn is not None:
            return wrap(wrap(kn[0], s2), t) == wrap(wrap(ko[0], s), t)
        a, b = self.types.int(old), self.types.int(new)
        return a is not None and b is not None and (holds(s, a) or t[0] <= s[0]) and (holds(s2, b) or t[0] <= s2[0])

    def _arm_pairs(self, e, tk, fk, sub):
        ts = [x for x in self.candidates(e.t, tk, sub) if not isinstance(x, tuple)] + [e.t]
        fs = [x for x in self.candidates(e.f, fk, sub) if not isinstance(x, tuple)] + [e.f]
        for a in ts:
            for b in fs:
                if value_of(a, self.ctx) is not None and value_of(b, self.ctx) is not None:
                    yield a, b

    def _place(self, x):
        x = _strip_int_casts(x)
        if isinstance(x, (C.Name, C.Member, C.Index)) or (isinstance(x, C.Unary) and x.op == "*"):
            return C.expr_text(x)
        return None

    def _assign(self, e):
        target = e.target if isinstance(e.target, C.Name) else self._lvalue(e.target)
        d = self.types.of(e.target)
        t = d[1] if d and d[0] == "int" else None
        place = self._place(e.target)
        if place is not None and self.is_io(e.target):
            place = None                    # (a device register: its constants stay as they are)
        byte = t is not None and t[0] == 8 and not isinstance(e.target, C.Name)
        if e.op == "=":
            value = self._expr(e.value, Use("conv", t, place=place, byte=byte, top=True))
        elif e.op[:-1] in ("<<", ">>"):
            value = self._expr(e.value, Use("count"))
        else:
            k = value_of(e.value, self.ctx)
            value = self._expr(e.value, Use("arith", t, e.op[:-1]) if k is not None else SAME)
        return C.Assign(e.op, target, value)

    def _lvalue(self, x):
        if isinstance(x, C.Unary) and x.op == "*":
            return C.Unary("*", self._expr(x.e, SAME))
        if isinstance(x, C.Member):
            return C.Member(self._expr(x.base, SAME), x.name, x.arrow)
        if isinstance(x, C.Index):
            return C.Index(self._expr(x.base, SAME), self._expr(x.index, SAME))
        return x

    # -- statements --

    def stmt(self, s):
        if isinstance(s, C.ExprStmt):
            s.e = self.expr(s.e)
            inc = _increment(s.e, self.types)
            if inc is not None:
                s.e = inc
                self.changed = True
        elif isinstance(s, C.Return):
            if s.e is not None:
                r = self.types.result
                s.e = self.expr(s.e, Use("conv", r[1], top=True) if r and r[0] == "int" else SAME)
        elif isinstance(s, (C.If, C.While, C.DoWhile)):
            s.cond = self.expr(s.cond, Use("truth"))
        elif isinstance(s, C.For):
            if s.init is not None and not isinstance(s.init, C.Decl):
                s.init = self.expr(s.init)
            elif isinstance(s.init, C.Decl):
                self.stmt(s.init)
            if s.cond is not None:
                s.cond = self.expr(s.cond, Use("truth"))
            if s.step is not None:
                s.step = self.expr(s.step)
                inc = _increment(s.step, self.types)
                if inc is not None:
                    s.step = inc
                    self.changed = True
        elif isinstance(s, C.Switch):
            outer, self.switch_t = self.switch_t, self.types.int(s.e)
            s.e = self.expr(s.e, SAME)
            self.stmt(s.body)
            self.switch_t = outer
            return
        elif isinstance(s, C.Case) and s.value is not None:
            t = self.switch_t
            s.value = self.expr(s.value, Use("conv", cexpr.promote(t, self.ctx)) if t else SAME)
        elif isinstance(s, C.Decl):
            for d in s.items:
                if d.init is not None and not d.dims:
                    t = self.types.vars.get(d.name)
                    d.init = self.expr(d.init, Use("conv", t[1], place=d.name, top=True)
                                       if t and t[0] == "int" else SAME)
        for inner in _inner(s):
            self.stmt(inner)


def _children(e):
    if isinstance(e, (C.Unary, C.Post, C.Cast)):
        return [e.e]
    if isinstance(e, C.Binary):
        return [e.l, e.r]
    if isinstance(e, C.Cond):
        return [e.c, e.t, e.f]
    if isinstance(e, C.Call):
        return [e.f] + e.args
    if isinstance(e, C.Index):
        return [e.base, e.index]
    if isinstance(e, C.Member):
        return [e.base]
    if isinstance(e, C.Comma):
        return e.items
    if isinstance(e, C.Assign):
        return [e.target, e.value]
    return []


def _stmt_exprs(s):
    if isinstance(s, (C.ExprStmt, C.Return)):
        return [s.e] if s.e is not None else []
    if isinstance(s, (C.If, C.While, C.DoWhile)):
        return [s.cond]
    if isinstance(s, C.For):
        return [x for x in (s.init if not isinstance(s.init, C.Decl) else None, s.cond, s.step) if x is not None]
    if isinstance(s, C.Switch):
        return [s.e]
    if isinstance(s, C.Case):
        return [s.value] if s.value is not None else []
    return []


def _arms(x):
    """The values a select gives (its arms, a select inside an arm's too), or x itself."""
    y = _strip_int_casts(x)
    if isinstance(y, C.Cond) and value_of(y.c, cexpr.Ctx()) is None:
        return _arms(y.t) + _arms(y.f)
    return [x]


def _raw(x):
    """Whether a place is an access through an address the code computes (*(volatile u32 *)(r0 + 4)):
    the same text may stand for other memory at another time, so it joins no object by a copy."""
    x = _strip_int_casts(x)
    return isinstance(x, C.Unary) and x.op == "*" and not isinstance(x.e, C.Name)


def _object_key(x):
    """The object a place belongs to (one radix per object): a variable or a macro its own; an array's
    elements one object (a local's, static data's, a typed pointer's: x[k], and *p as p[0]); a
    structure's fields each their own (x.f, p->f, x[].f); a raw access through an address expression
    its own place (None: the place's text)."""
    x = _strip_int_casts(x)
    if isinstance(x, C.Name):
        return x.id
    if isinstance(x, C.Index):
        base = _object_key(x.base) if isinstance(x.base, (C.Name, C.Member, C.Index)) else C.expr_text(x.base)
        return None if base is None else base + "[]"
    if isinstance(x, C.Member):
        if x.arrow:
            return C.expr_text(x.base) + "->" + x.name
        base = _object_key(x.base)
        return None if base is None else base + "." + x.name
    if isinstance(x, C.Unary) and x.op == "*" and isinstance(x.e, C.Name):
        return x.e.id + "[]"
    return None


def _strip_int_casts(x):
    while isinstance(x, C.Cast) and x.type in INTS:
        x = x.e
    return x


def _strip_casts(x):
    while isinstance(x, C.Cast):
        x = x.e
    return x


def _keeps_form(e):
    """Whether a constant expression keeps its form: one with a bitwise operation or a shift besides
    a single complement (0x20001913u & ~0x7u shows the alignment it computes; the compare idiom's
    (u32)~0x5fu its constant), or a literal with a note (a text's address: dread.naming apply_globals
    wrote 0xd9cu /* "FR Timer" */, the number the listing shows)."""
    if isinstance(e, C.Num) and e.note:
        return True
    for x in _walk(e):
        if isinstance(x, C.Binary) and (x.op in COMPARE or x.op in ("&&", "||")):
            return True                     # (if (0x80000000u == 0) says what it compares)
        if isinstance(x, C.Binary) and x.op in ("&", "|", "^", "<<", ">>"):
            z = [y for y in (x.l, x.r) if isinstance(_strip_int_casts(y), C.Num) and _strip_int_casts(y).value == 0]
            if not z:
                return True
    return False


def _near_top(v):
    """Whether v (as a 32- or 64-bit unsigned value) lies within NEGATIVE of the top: a negative number."""
    return any((1 << n) - NEGATIVE <= v & ((1 << n) - 1) < (1 << n) and v >= (1 << (n - 1)) or -NEGATIVE <= v < 0
               for n in (32, 64))


def near_top(e, bits=32):
    """Whether e is a constant whose value in bits bits lies within NEGATIVE of the top: one this pass
    writes as a negative number where it is a value (~0x0u, 0xfffffffeu as -1, -2)."""
    k = value_of(e, cexpr.Ctx())
    return k is not None and (1 << bits) - NEGATIVE <= k[0] & ((1 << bits) - 1)


def _complement(e):
    """Whether a constant expression is a complement of a literal (~0x5fu, (u32)~0x5fu): the lifters'
    form of a negative number or a mask, which stays where no negative number stands for it (folded to
    hex it would hide the compared constant of the compare idiom). A sum that holds one folds."""
    x = _strip_int_casts(e)
    return isinstance(x, C.Unary) and x.op == "~" and isinstance(_strip_int_casts(x.e), C.Num)


def _walk(e):
    yield e
    for c in _children(e):
        yield from _walk(c)


def _plain_number(x):
    if isinstance(x, C.Num) and not isinstance(x.value, float) and re.fullmatch(r"\d+u?", x.text):
        return x.value
    return None


def _flip_negative(e):
    """x + (-k) as x - k, x - (-k) as x + k, (-k) + x as x - k (the same type and value: -k is an int
    as k is)."""
    def neg(x):
        if isinstance(x, C.Unary) and x.op == "-" and isinstance(x.e, C.Num) and _plain_number(x.e) is not None:
            return x.e
        return None
    if e.op in ("+", "-") and neg(e.r) is not None:
        return C.Binary("-" if e.op == "+" else "+", e.l, neg(e.r))
    if e.op == "+" and neg(e.l) is not None and neg(e.r) is None:
        return C.Binary("-", e.r, neg(e.l))
    return e


def _increment(e, types):
    """x = x op y as x op= y, x = x + 1 as x++ and x = x - 1 as x-- (an expression statement or a
    for loop's step: its value unused), x a plain variable; None when e is no such assignment."""
    if not (isinstance(e, C.Assign) and e.op == "=" and _plain_lvalue(e.target)):
        return None
    x, v = C.expr_text(e.target), e.value
    tx = types.of(e.target)
    if isinstance(v, C.Cast) and tx and types.parse(v.type) == tx:
        v = v.e                             # (x = (T)(x + 1), T x's type: the assignment converts so)
    if not isinstance(v, C.Binary) or v.op not in COMPOUND:
        return None
    if C.expr_text(_strip_int_casts(v.l)) == x and _same_or_implied(v.l, e.target, v.op, types):
        y = v.r
    elif v.op in ("+", "*", "&", "|", "^") and C.expr_text(_strip_int_casts(v.r)) == x and \
            _same_or_implied(v.r, e.target, v.op, types):
        y = v.l
    else:
        return None
    if any(C.expr_text(n) == x for n in _walk(y)) or any(
            isinstance(n, (C.Assign, C.Post, C.Call)) or (isinstance(n, C.Unary) and n.op in ("++", "--"))
            for n in _walk(y)):
        return None
    if v.op in ("+", "-") and isinstance(y, C.Num) and y.value == 1 and _plain_number(y) is not None:
        return C.Post("++" if v.op == "+" else "--", e.target)
    return C.Assign(v.op + "=", e.target, y)


def _plain_lvalue(x):
    """A variable, or a field, an element or a memory access whose address reads no memory and has no
    effect: x op= y evaluates it once where x = x op y did twice, the same accesses of x itself."""
    if isinstance(x, C.Name):
        return True
    if isinstance(x, C.Member):
        return _plain_address(x.base) if x.arrow else _plain_lvalue(x.base)
    if isinstance(x, C.Index):
        return _plain_address(x.base) and _plain_address(x.index)
    if isinstance(x, C.Unary) and x.op == "*":
        return _plain_address(x.e)
    return False


def _plain_address(e):
    for n in _walk(e):
        if isinstance(n, (C.Call, C.Assign, C.Post, C.Member, C.Index)) or \
                (isinstance(n, C.Unary) and n.op in ("*", "++", "--")):
            return False
    return True


def _same_or_implied(x, target, op, types):
    """Whether operand x is the target itself, or the target under a cast the assignment back into it
    makes no difference to ((u32)x + 1 stored into a u8 x: the low byte is the same; only for an
    operation whose low bits only the operands' low bits make)."""
    if not isinstance(x, C.Cast):
        return True
    if op not in LOW_BITS + ("<<",):
        return False
    tt, ct = types.int(target), types.int(x)
    return tt is not None and ct is not None and ct[0] >= tt[0] and ct[0] <= 32


# --- parameters used only as masks (the readable_types table's masks) ---

MASK_ROUNDS = 6         # rounds of mask_params over the functions (a mask handed on to a callee's mask)


def mask_params(trees, rounds=MASK_ROUNDS):
    """{function: [indices]} of the parameters each function ([(tree, fn, macros)]: its C) uses only as a
    mask, every function listed: the parameter is never assigned, and every read of its value (the
    parameter, or a variable every definition of which is a copy of one that holds it) is an operand of
    &, |, ^ or ~ (also of &=, |=, ^=: bic is & ~), a copy into such a variable, the value returned as it
    is (a void function's leftover register: RISC-V's a0), or an argument for a callee's parameter that
    is a mask itself (over the functions until nothing changes, at most rounds rounds); one read as a
    mask at least. A shift reads no mask (x << 2 scales a count as often as it moves bits), nor does a
    shift's count masked by the lifters (1 << (r0 & 0xffu), ARM's register shifts). What a constant argument for such a parameter is
    written as: a mask (0xffffffffu, ~0x20u), never a negative number (FreeRTOS's
    xTaskGenericNotifyWait(0, 0xffffffffu, ...): its callee clears &= ~ulBitsToClearOnEntry). Not
    established: what the callee does with a value it stores (a mask kept for later is no mask here)."""
    found = {fn.name: set() for _, fn, _ in trees}
    for _ in range(rounds):
        changed = False
        for _, fn, _ in trees:
            got = _masks_of(fn, found)
            if got != found[fn.name]:
                found[fn.name] = got
                changed = True
        if not changed:
            break
    return {name: sorted(ix) for name, ix in found.items()}


def _masks_of(fn, found):
    """The indices of fn's parameters it uses only as masks (mask_params), its callees' as found says."""
    defs, reads = {}, {}                    # variable -> [value or None]; variable -> [context of each read]

    def visit(e, ctx="other"):
        if e is None:
            return
        if isinstance(e, C.Name):
            reads.setdefault(e.id, []).append(ctx)
            return
        if isinstance(e, C.Cast) and e.type in INTS:
            visit(e.e, ctx if ctx in ("bits", "ret", "count") or isinstance(ctx, tuple) else "other")
            return
        if isinstance(e, C.Binary) and e.op in ("&", "|", "^"):
            inner = "count" if ctx == "count" else "bits"     # (1 << (r0 & 0xffu): ARM's count, no mask)
            visit(e.l, inner)
            visit(e.r, inner)
            return
        if isinstance(e, C.Binary) and e.op in ("<<", ">>"):
            visit(e.l)                      # (x << 2 scales a count as often as it moves bits)
            visit(e.r, "count")
            return
        if isinstance(e, C.Unary) and e.op == "~":
            visit(e.e, "bits")
            return
        if isinstance(e, C.Assign):
            if isinstance(e.target, C.Name):
                v = e.target.id
                defs.setdefault(v, []).append(e.value if e.op == "=" else None)
                if e.op != "=":
                    reads.setdefault(v, []).append("bits" if e.op in ("&=", "|=", "^=") else "other")
                visit(e.value, ("copy", v) if e.op == "=" else "bits" if e.op in ("&=", "|=", "^=") else
                      "count" if e.op in ("<<=", ">>=") else "other")
            else:
                visit(e.target)
                visit(e.value, "bits" if e.op in ("&=", "|=", "^=") else "count" if e.op in ("<<=", ">>=") else
                      "other")
            return
        if isinstance(e, (C.Post, C.Unary)) and e.op in ("++", "--") and isinstance(e.e, C.Name):
            defs.setdefault(e.e.id, []).append(None)
            reads.setdefault(e.e.id, []).append("other")
            return
        if isinstance(e, C.Call) and isinstance(e.f, C.Name):
            for i, a in enumerate(e.args):
                visit(a, ("arg", e.f.id, i))
            return
        for c in _children(e):
            visit(c)

    def visit_stmt(s):
        if isinstance(s, C.Decl):
            for d in s.items:
                if d.init is not None:
                    defs.setdefault(d.name, []).append(d.init)
                    visit(d.init, ("copy", d.name))
        elif isinstance(s, C.Asm):
            for o in s.outputs:
                if isinstance(o.e, C.Name):
                    defs.setdefault(o.e.id, []).append(None)
            for o in s.inputs:
                visit(o.e)
        elif isinstance(s, C.Return) and s.e is not None:
            visit(s.e, "ret")               # (handed back as it is: a void function's leftover register)
        else:
            for e in _stmt_exprs(s):
                visit(e)
        if isinstance(s, C.For) and isinstance(s.init, C.Decl):
            visit_stmt(s.init)
        for inner in _inner(s):
            visit_stmt(inner)
    for s in fn.body.body:
        visit_stmt(s)

    def copy_of(x, held):
        x = _strip_int_casts(x)
        return isinstance(x, C.Name) and x.id in held
    out = set()
    for i, (ptype, pname) in enumerate(fn.params):
        if " ".join(ptype.split()) not in INTS or defs.get(pname):
            continue
        held = {pname}
        grown = True
        while grown:                        # (the variables that only ever copy it)
            grown = False
            for v, values in defs.items():
                if v not in held and values and all(x is not None and copy_of(x, held) for x in values):
                    held.add(v)
                    grown = True
        uses = [ctx for v in held for ctx in reads.get(v, ())]
        ok = [ctx == "bits" or (isinstance(ctx, tuple) and ctx[0] == "arg" and ctx[2] in found.get(ctx[1], ()))
              for ctx in uses]
        neutral = [ctx == "ret" or (isinstance(ctx, tuple) and ctx[0] == "copy" and ctx[1] in held) for ctx in uses]
        if any(ok) and all(a or b for a, b in zip(ok, neutral)):
            out.add(i)
    return out


def polish(tree, fn, int_bits=None, io=(), masks=None, pointed=None):
    """Write the function's constants and casts by their use (the module's docstring), in place;
    returns whether anything changed. int_bits: int's width where the function's names no longer tell
    it (dread.tidy context reads the lifter's register names); io: [(start, end)] of device memory,
    whose registers keep their constants' radix; masks: {callee: [indices of the parameters it uses only
    as masks]} (mask_params, the readable_types table's masks); pointed: the image's texts and functions
    (dread.constants Pointed): an object given one's address holds no numbers."""
    p = Polisher(tree, fn, int_bits, io, masks, pointed)
    for s in fn.body.body:
        p.stmt(s)
    _drop_self_assignments(fn.body, p)
    return p.changed


def _drop_self_assignments(block, p):
    """x = x; of a local variable (what 0x0u | x leaves) goes; one of memory stays (an access)."""
    if isinstance(block, C.Block):
        keep = []
        for s in block.body:
            if isinstance(s, C.ExprStmt) and isinstance(s.e, C.Assign) and s.e.op == "=" and \
                    isinstance(s.e.target, C.Name) and isinstance(s.e.value, C.Name) and \
                    s.e.target.id == s.e.value.id and s.e.target.id in p.types.vars and \
                    not _volatile_variable(p.fn, s.e.target.id):
                p.changed = True
                continue
            keep.append(s)
        block.body[:] = keep
    for s in (block.body if isinstance(block, C.Block) else [block]):
        for inner in _inner(s):
            _drop_self_assignments(inner if isinstance(inner, C.Block) else C.Block([inner]), p)


def _volatile_variable(fn, name):
    def find(stmts):
        for s in stmts:
            if isinstance(s, C.Decl) and any(d.name == name for d in s.items):
                return "volatile" in s.specs.split()
            for inner in _inner(s):
                got = find([inner])
                if got is not None:
                    return got
        return None
    return bool(find(fn.body.body))
