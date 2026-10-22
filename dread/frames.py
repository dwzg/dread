"""dread.frames: the part of a function's stack frame that a frame address handed out may reach, as one
local structure in readable C. Applied to the tree after dread.tidy and dread.structure (dread.readable
_named, last), since its member expressions are nothing the clean-up passes know.

dread.tidy's frame_locals turns the frame's slots into variables (*(volatile u32 *)(sp0 - 36) becomes
s36). When frame addresses leave the function (sp0 - 40 + 0x8u passed to a call: FreeRTOS's TimeOut_t
on xQueueGenericSend's stack), only the slots wholly below the lowest of them become variables; from
there up to the frame's top (sp0) the frame stays raw, since what the address is handed to may reach
anything above it. Here that region becomes one object of a type defined above the function:

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
and an address with an index added as the object's ((u32)&stack36 + r3: a buffer walked). One object
for the whole region: split at each address that leaves (a local each) it would read better, but
trust every callee to stay inside its piece. Volatile, as the raw accesses were: every access stays
one, in order, also against what a callee or a pointer of the function writes there (the targets
compile with strict aliasing, and the function's own pointers into the region access it with their
own types). A typedef, as the MSP430 lifter's dread_w32: what dread.cparse reads (a structure in a
declaration it does not), so readable C stays C of the lifters' dialect.

Only where the frame is the lifters' local array (u32 frame[N] with sp0 = (u32)&frame[N]; not a
wrapped function's real stack, whose callees may reach the stack arguments above sp0, nor an array
whose top the lifter left out, sp0 = (u32)&frame[N] + S: it does so only where no frame address
leaves, dread.lift's saves_out), every use of
sp0 is an access at a constant offset below it or such an address, an address leaves, and the
region's accesses lie inside it with one type per offset and no two overlapping (nor one below it
reaching in). Otherwise the function stays as it was. What lies below the region stays as dread.tidy
left it (variables, or raw on the frame array when frame_locals left them); when nothing uses sp0 any
more, the frame's declarations go.

Not established: that a callee stays inside the region (the lifted C's frame ended at sp0 too, so it
never reached above), nor that this C behaves as the lifted C did; dread readable --check decides that
for each function, as for every other transformation.
"""
import re

from dread import cparse as C
from dread.cflow import walk_expr
from dread.tidy import frame_address, frame_offset

SIZES = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "u64": 8, "s64": 8}
FRAME, BASE = "frame", "sp0"            # the lifters' frame array and the address of its end
ALIGNED = re.compile(r"aligned\s*\(\s*(\d+)\s*\)")
DEFINE = re.compile(r"^\s*#\s*define\s+(\w+)", re.M)


class Region:
    """What frame_struct writes: the region's lowest offset (low, negative: sp0 + low is its start), its
    fields ({offset from the start: type}), its alignment and the frame's declarations."""

    def __init__(self, low, fields, align, base_type, frame_decl, base_decl):
        self.low, self.fields, self.align, self.base_type = low, fields, align, base_type
        self.frame_decl, self.base_decl = frame_decl, base_decl

    def members(self):
        """The structure's members as C declarations: a field at each offset accessed, bytes between."""
        parts, pos = [], 0
        for rel in sorted(self.fields):
            if rel > pos:
                parts.append("u8 pad_%x[%d];" % (pos, rel - pos))
            parts.append("%s %s;" % (self.fields[rel], field_name(rel)))
            pos = rel + SIZES[self.fields[rel]]
        if pos < -self.low:
            parts.append("u8 pad_%x[%d];" % (pos, -self.low - pos))
        return parts

    def typedef(self, name):
        """The C definition of the region's type: typedef struct { ... } __attribute__((...)) name;"""
        return "typedef struct\n{\n%s\n} __attribute__((packed, aligned(%d))) %s;" % (
            "\n".join("    " + m for m in self.members()), self.align, name)


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
    if bad:
        return bad[0]
    if not escaped:
        return "no frame address leaves"
    if max(escaped) >= 0:
        return "a frame address at or above sp0 leaves"
    low = min(escaped)
    if low < -size:
        return "a frame address below the frame leaves"
    fields = {}
    for off, types in accesses.items():
        if off >= 0:
            return "an access at or above sp0"
        width = max(SIZES[t] for t in types)
        if off < low:
            if off + width > low:
                return "an access below the region reaches into it"
            continue
        if len(types) != 1:
            return "two types at one offset in the region"
        if off + width > 0:
            return "an access in the region reaches above sp0"
        fields[off - low] = next(iter(types))
    spans = sorted((rel, rel + SIZES[t]) for rel, t in fields.items())
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        return "overlapping accesses in the region"
    return Region(low, fields, _pow2_alignment(-low, base_align), btype, (fdecl, fd), (bdecl, bd))


def frame_struct(tree, fn, avoid=()):
    """Write the region of the function's frame that addresses handed out may reach as one local
    structure object (see the module's docstring); returns its name, or None when the function stays
    as it was. avoid: names the object must not take (those the function's variables will get)."""
    from dread.naming import _rewrite, _rewrite_stmt, other_names, variables
    region = plan(fn)
    if not isinstance(region, Region):
        return None
    macros = {m for item in tree.items if isinstance(item, C.Raw) for m in DEFINE.findall(item.text)}
    if macros & set(re.findall(r"\b(?:f|pad)_[0-9a-f]+\b", " ".join(region.members()))):
        return None                         # (a macro of the file would take a field's name)
    taken = other_names(tree, fn) | set(variables(fn)) | set(avoid)
    name, k = "stack%d" % -region.low, 0
    while name in taken or name + "_t" in taken:
        k += 1
        name = "stack%d_%d" % (-region.low, k)
    low, fields = region.low, region.fields

    def address(off, field=True):
        rel = off - low
        if rel in fields and field:
            return C.Cast(region.base_type, C.Unary("&", C.Member(C.Name(name), field_name(rel), False)))
        whole = C.Cast(region.base_type, C.Unary("&", C.Name(name)))
        return C.Binary("+", whole, C.Num("%d" % rel)) if rel else whole

    def f(e):
        acc = _access(e)
        if acc is not None:
            off = acc[0]
            if off - low in fields:
                return C.Member(C.Name(name), field_name(off - low), False)
            return e                        # (below the region: as it was)
        if frame_address(e):
            return address(frame_offset(e))
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
    fn.body.body.insert(at + 1, C.Decl("volatile %s_t" % name, [C.Declarator(name)]))
    fn.body.body[:] = [st for st in fn.body.body if not (isinstance(st, C.Decl) and not st.items)]
    at = next((i + 1 for i, item in enumerate(tree.items)   # (after the #include, as dread.shapes' structures)
               if isinstance(item, C.Raw) and item.text.startswith("#include")), tree.items.index(fn))
    tree.items.insert(at, C.Raw(region.typedef(name + "_t")))
    return name
