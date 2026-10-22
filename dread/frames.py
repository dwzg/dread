"""dread.frames: the part of a function's stack frame that a frame address handed out may reach, as one
local object in readable C. Applied to the tree after dread.tidy and dread.structure (dread.readable
_named, last), since its member expressions are nothing the clean-up passes know.

dread.tidy's frame_locals turns the frame's slots into variables (*(volatile u32 *)(sp0 - 36) becomes
s36). When frame addresses leave the function (sp0 - 40 + 0x8u passed to a call: FreeRTOS's TimeOut_t
on xQueueGenericSend's stack), only the slots wholly below the region they may reach become variables
(dread.tidy region_start: from the lowest of them, moved down over an access that reaches into it from
below); from there up to the frame's top (sp0) the frame stays raw, since what the address is handed
to may reach anything above it. Here that region becomes one object of a type defined above the
function:

    typedef struct
    {
        u32 f_0;
        u32 f_4;
        u32 f_8;
        u8 pad_c[24];
    } __attribute__((packed, aligned(4))) stack36_t;
    ...
        volatile stack36_t stack36;

a field at each offset the function accesses (offsets from the region's start, with the type of the
accesses there), bytes between, as large as the region and aligned as the region's start is in the
lifted C (the frame array's alignment and the offset). Its accesses are written as members
(stack36.f_0), the addresses handed out as a field's or its own: (u32)&stack36.f_4, (u32)&stack36 + 2,
and an address with an index added as the object's ((u32)&stack36 + r3: a buffer walked), the sum's
constants gathered at the address they come to where the code reached it by way of another (RISC-V's
addi a5, sp, 32; add a5, a5, s0; lbu a5, -20(a5): sp0 - 64 + 0x20u + s0_1 + 0xffffffecu is
(u32)&stack52 + s0_1; dread.tidy frame_index, only where every term is 32 bits wide or narrower). Such
an address counts as one that leaves, beside the frame addresses written inside its sum (frame_sums), so
the region starts no higher than it did.

Accesses that overlap share a union, each with a member of its own type, one that starts later behind
bytes of its own, all anonymous (C11), so that every member keeps its plain name:

        union
        {
            u32 f_0;
            struct { u8 pad_f_2[2]; u16 f_2; } __attribute__((packed));
        } __attribute__((packed));

a word stored and its upper half read: GCC -Os makes room for a 16-bit local by pushing a word and
hands the local's address on (FreeRTOS's vPolledQueueProducer and Consumer), and the region then
starts at the push. Where several types share an offset, their members say which they are (f_0_u32
and f_0_u8: RISC-V's prvReceivingTask clears a buffer by words and reads its first byte).

A region that the function accesses at no constant offset, only through addresses it hands on or
indexes (a string sprintf writes, a stream buffer's storage), is an array of bytes:

        volatile u8 stack40[40] __attribute__((aligned(8)));

its addresses (u32)stack40 and (u32)&stack40[15], an index added to them as arithmetic on the address
((u32)stack40 + r5, not &stack40[r5]: an index past the array is undefined in C, and GCC derives
loop bounds from that, where the lifted C's arithmetic had no such case).

One object for the whole region: split at each address that leaves (a local each) it would read
better, but trust every callee to stay inside its piece. Volatile, as the raw accesses were: every
access stays one, of its width and at its place, in order, also against what a callee or a pointer
of the function writes there (the targets compile with strict aliasing, and the function's own
pointers into the region access it with their own types). Packed throughout, so that the layout is
the frame's and no member moves for its alignment; aligned as a whole, so that the compiler knows each
member's alignment from its offset. A typedef, as the MSP430 lifter's dread_w32: what dread.cparse
reads (a structure in a declaration it does not), so readable C stays C of the lifters' dialect.

Only where the frame is the lifters' local array (u32 frame[N] with sp0 = (u32)&frame[N]; not a
wrapped function's real stack, whose callees may reach the stack arguments above sp0, nor an array
whose top the lifter left out, sp0 = (u32)&frame[N] + S: it does so only where no frame address
leaves, dread.lift's saves_out), every use of sp0 is an access at a constant offset below it or such an
address, an address leaves, and the region's accesses lie inside it with plain types. Otherwise the
function stays as it was. What lies below the region stays as dread.tidy left it (variables, or raw on
the frame array when frame_locals left them); when nothing uses sp0 any more, the frame's declarations
go.

Not established: that a callee stays inside the region (the lifted C's frame ended at sp0 too, so it
never reached above), nor that this C behaves as the lifted C did; dread readable --check decides that
for each function, as for every other transformation.
"""
import re

from dread import cparse as C
from dread import tidy
from dread.cflow import walk_expr
from dread.tidy import SLOT_SIZES as SIZES, frame_address, frame_index, frame_offset, frame_sums, region_start

FRAME, BASE = "frame", "sp0"            # the lifters' frame array and the address of its end
ALIGNED = re.compile(r"aligned\s*\(\s*(\d+)\s*\)")
DEFINE = re.compile(r"^\s*#\s*define\s+(\w+)", re.M)
PACKED = "__attribute__((packed))"


class Region:
    """What frame_struct writes: the region's lowest offset (low, negative: sp0 + low is its start), the
    types accessed at each offset in it ({offset from the start: {type, ...}}), its alignment, the type
    of sp0 and the frame's declarations."""

    def __init__(self, low, fields, align, base_type, frame_decl, base_decl):
        self.low, self.fields, self.align, self.base_type = low, fields, align, base_type
        self.frame_decl, self.base_decl = frame_decl, base_decl

    def array(self):
        """Whether the region is an array of bytes: the function accesses none of it at a constant
        offset."""
        return not self.fields

    def groups(self):
        """[(start, end, [(offset, type), ...])]: the accesses whose bytes overlap, together, in order
        of their offsets (one access alone where nothing overlaps it)."""
        out = []
        for rel, t in sorted(((rel, t) for rel, types in self.fields.items() for t in types),
                             key=lambda a: (a[0], -SIZES[a[1]], a[1])):
            end = rel + SIZES[t]
            if out and rel < out[-1][1]:
                start, last, items = out[-1]
                out[-1] = (start, max(last, end), items + [(rel, t)])
            else:
                out.append((rel, end, [(rel, t)]))
        return out

    def member(self, rel, t):
        """The member an access of type t at offset rel is: f_<rel>, f_<rel>_<type> where several
        types share the offset."""
        return field_name(rel) if len(self.fields[rel]) == 1 else "%s_%s" % (field_name(rel), t)

    def at(self, rel):
        """The member that stands for offset rel in an address: the widest type's there."""
        return self.member(rel, min(self.fields[rel], key=lambda t: (-SIZES[t], t)))

    def members(self):
        """The structure's members as lines of C: a field at each offset accessed, bytes between, a union
        where accesses overlap (indented by four for each level inside it)."""
        lines, pos = [], 0
        for start, end, items in self.groups():
            if start > pos:
                lines.append("u8 pad_%x[%d];" % (pos, start - pos))
            if len(items) == 1:
                rel, t = items[0]
                lines.append("%s %s;" % (t, self.member(rel, t)))
            else:
                lines += ["union", "{"]
                for rel, t in items:
                    name = self.member(rel, t)
                    lines.append("    %s %s;" % (t, name) if rel == start else
                                 "    struct { u8 pad_%s[%d]; %s %s; } %s;" % (name, rel - start, t, name, PACKED))
                lines.append("} %s;" % PACKED)
            pos = end
        if pos < -self.low:
            lines.append("u8 pad_%x[%d];" % (pos, -self.low - pos))
        return lines

    def names(self):
        """The names the structure's members take (its pads' too)."""
        return set(re.findall(r"\b(?:f|pad)_\w+", " ".join(self.members())))

    def unions(self):
        """How many unions the structure has."""
        return sum(1 for g in self.groups() if len(g[2]) > 1)

    def typedef(self, name):
        """The C definition of the region's type: typedef struct { ... } __attribute__((...)) name;"""
        return "typedef struct\n{\n%s\n} __attribute__((packed, aligned(%d))) %s;" % (
            "\n".join("    " + m for m in self.members()), self.align, name)

    def declaration(self, name):
        """The object's declaration in the function."""
        if self.array():
            attrs = "__attribute__((aligned(%d)))" % self.align if self.align > 1 else ""
            return C.Decl("volatile u8", [C.Declarator(name, dims=[C.Num("%d" % -self.low)], attrs=attrs)])
        return C.Decl("volatile %s_t" % name, [C.Declarator(name)])


def field_name(rel):
    return "f_%x" % rel


def _pow2_alignment(n, cap):
    """The largest power of two up to cap that divides n (cap itself for 0)."""
    a = 1
    while a < cap and n % (a * 2) == 0:
        a *= 2
    return a


def frame_declarations(fn):
    """(frame's Decl, its declarator, sp0's Decl, its declarator, sp0's type, the bytes of the array
    below sp0, sp0's alignment) when the function's frame is the lifters' local array and sp0 the
    address of its end (or of an element: the lifters give small frames a few words more), else None."""
    frame = base = None
    for st in fn.body.body:
        if isinstance(st, C.Decl):
            for d in st.items:
                if d.name == FRAME:
                    frame = (st, d)
                elif d.name == BASE:
                    base = (st, d)
    if frame is None or base is None:
        return None
    (fdecl, fd), (bdecl, bd) = frame, base
    elem = " ".join(fdecl.specs.split())
    if elem not in SIZES or fd.ptr or len(fd.dims) != 1 or not isinstance(fd.dims[0], C.Num) or \
            not isinstance(fd.dims[0].value, int) or fd.init is not None:
        return None
    btype = " ".join(bdecl.specs.split())
    init = bd.init
    if bd.ptr or bd.dims or not (isinstance(init, C.Cast) and init.type == btype and btype in ("u32", "u16")):
        return None
    e = init.e
    if not (isinstance(e, C.Unary) and e.op == "&" and isinstance(e.e, C.Index) and isinstance(e.e.base, C.Name)
            and e.e.base.id == FRAME and isinstance(e.e.index, C.Num) and isinstance(e.e.index.value, int)
            and 0 <= e.e.index.value <= fd.dims[0].value):
        return None
    below = e.e.index.value * SIZES[elem]
    m = ALIGNED.search(fd.attrs or "")
    array_align = int(m.group(1)) if m else SIZES[elem]
    return fdecl, fd, bdecl, bd, btype, below, _pow2_alignment(below, array_align)


def _children(e):
    for slot in type(e).__slots__:
        v = getattr(e, slot, None)
        if isinstance(v, C.Expr):
            yield v
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, C.Expr):
                    yield x


def _access(e):
    """(offset, type) of a frame access *(volatile T *)(sp0 - k), (None, None) for one whose offset or
    type is not plain, or None when e is no frame access."""
    if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast) and frame_address(e.e.e):
        pointee = e.e.type.replace("volatile", " ").replace("*", " ").split()
        off = frame_offset(e.e.e)
        if off is None or len(pointee) != 1 or pointee[0] not in SIZES or e.e.type.count("*") != 1:
            return None, None
        return off, pointee[0]
    return None


def _expressions(fn, skip=()):
    """Every expression of the body's statements, nested statements' too, but the initial values and
    dimensions of the declarators in skip."""
    from dread.naming import _exprs, _inner
    out = []

    def visit(st):
        if isinstance(st, C.Decl):
            out.extend(x for d in st.items if all(d is not k for k in skip)
                       for x in [d.init] + list(d.dims) if x is not None)
        else:
            out.extend(_exprs(st))
        for inner in _inner(st):
            visit(inner)
    for st in fn.body.body:
        visit(st)
    return out


def plan(fn):
    """The Region frame_struct would write for the function, or a string saying why there is none."""
    found = frame_declarations(fn)
    if found is None:
        return "no frame array with sp0 at its end"
    fdecl, fd, bdecl, bd, btype, size, base_align = found
    accesses, escaped, bad = {}, set(), []

    def visit(e):
        if e is None or bad:
            return
        acc = _access(e)
        if acc is not None:
            if acc[0] is None:
                bad.append("a frame access at no constant offset or of no plain type")
            else:
                accesses.setdefault(acc[0], set()).add(acc[1])
            return
        if frame_address(e):                # a frame address as a value: it leaves
            off = frame_offset(e)
            if off is None:
                bad.append("a frame address at no constant offset")
            escaped.add(off)
            return
        if isinstance(e, C.Name) and e.id in (BASE, FRAME):
            bad.append("%s used otherwise" % e.id)
            return
        for c in _children(e):
            visit(c)
    for e in _expressions(fn, skip=(fd, bd)):
        visit(e)
        frame_sums(e, escaped)              # (an index added to a frame address: dread.tidy frame_locals)
    if bad:
        return bad[0]
    if not escaped:
        return "no frame address leaves"
    if max(escaped) >= 0:
        return "a frame address at or above sp0 leaves"
    low = region_start([(off, max(SIZES[t] for t in types)) for off, types in accesses.items()], min(escaped))
    if low < -size:
        return "a frame address below the frame leaves"
    fields = {}
    for off, types in accesses.items():
        if off >= low:
            if off + max(SIZES[t] for t in types) > 0:
                return "an access in the region reaches above sp0"
            fields[off - low] = set(types)
    return Region(low, fields, _pow2_alignment(-low, base_align), btype, (fdecl, fd), (bdecl, bd))


def frame_struct(tree, fn, avoid=()):
    """Write the region of the function's frame that addresses handed out may reach as one local object
    (see the module's docstring); returns its name, or None when the function stays as it was. avoid:
    names the object must not take (those the function's variables will get)."""
    from dread.naming import _rewrite, _rewrite_stmt, other_names, variables
    region = plan(fn)
    if not isinstance(region, Region):
        return None
    macros = {m for item in tree.items if isinstance(item, C.Raw) for m in DEFINE.findall(item.text)}
    if macros & region.names():
        return None                         # (a macro of the file would take a member's name)
    taken = other_names(tree, fn) | set(variables(fn)) | set(avoid)
    name, k = "stack%d" % -region.low, 0
    while name in taken or name + "_t" in taken:
        k += 1
        name = "stack%d_%d" % (-region.low, k)
    low, fields = region.low, region.fields

    def address(off, field=True):
        rel = off - low
        if region.array():                  # ((u32)stack40, (u32)&stack40[15])
            if not rel:
                return C.Cast(region.base_type, C.Name(name))
            return C.Cast(region.base_type, C.Unary("&", C.Index(C.Name(name), C.Num("%d" % rel))))
        if rel in fields and field:
            return C.Cast(region.base_type, C.Unary("&", C.Member(C.Name(name), region.at(rel), False)))
        whole = C.Cast(region.base_type, C.Unary("&", C.Name(name)))
        return C.Binary("+", whole, C.Num("%d" % rel)) if rel else whole

    ctx = tidy.context(fn)                  # (the variables' types, before the rewrite)
    plain = ctx.types.get(BASE) == (32, False) and ctx.int_bits == 32

    def f(e):
        acc = _access(e)
        if acc is not None:
            off, t = acc
            if off >= low:
                return C.Member(C.Name(name), region.member(off - low, t), False)
            return e                        # (below the region: as it was)
        if frame_address(e):
            return address(frame_offset(e))
        got = frame_index(e) if plain else None
        if got is not None and low <= got[0] < 0 and all(tidy.width(x, ctx) == 32 for _, x in got[1]):
            # (an index added to a frame address, its constants gathered there: a sum of 32-bit
            # unsigned values, whatever its order)
            out = address(got[0], field=False)
            for sign, term in got[1]:
                out = C.Binary("+" if sign > 0 else "-", out, _rewrite(term, f))
            return out
        if isinstance(e, C.Binary) and e.op in ("+", "-") and frame_address(e.l):
            # (an address with an index added: into the object, not a field of it)
            return C.Binary(e.op, address(frame_offset(e.l), field=False), _rewrite(e.r, f))
        if isinstance(e, C.Binary) and e.op == "+" and frame_address(e.r):
            return C.Binary(e.op, _rewrite(e.l, f), address(frame_offset(e.r), field=False))
        return None
    for st in fn.body.body:
        _rewrite_stmt(st, f)
    (fdecl, fd), (bdecl, bd) = region.frame_decl, region.base_decl
    if not any(isinstance(x, C.Name) and x.id == BASE for e in _expressions(fn) for x in walk_expr(e)):
        # (nothing uses the frame array any more)
        fdecl.items[:] = [d for d in fdecl.items if d is not fd]
        bdecl.items[:] = [d for d in bdecl.items if d is not bd]
    at = next(i for i, st in enumerate(fn.body.body) if st is bdecl)     # (before every use of sp0)
    fn.body.body.insert(at + 1, region.declaration(name))
    fn.body.body[:] = [st for st in fn.body.body if not (isinstance(st, C.Decl) and not st.items)]
    if not region.array():
        at = next((i + 1 for i, item in enumerate(tree.items)   # (after the #include, as dread.shapes' structures)
                   if isinstance(item, C.Raw) and item.text.startswith("#include")), tree.items.index(fn))
        tree.items.insert(at, C.Raw(region.typedef(name + "_t")))
    return name
