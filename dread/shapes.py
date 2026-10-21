"""Structures behind pointers in readable C, found by code: the shapes of the memory a function
reaches through a pointer plus a constant offset, unified across copies, calls, results and
globals (unification with offsets, in the manner of Steensgaard's points-to analysis).

Every value of the image's readable functions is a type variable: a function's variable, its
parameters and result, the word at a fixed address. A type variable that is used as an address
points to a location: an object and an offset in it. Objects are merged by unification: two
pointers that are copied, compared or passed for the same parameter point to the same location, so
their objects become one, aligned at those offsets (an object reached at offset 4 through one
pointer and at 0 through another is one object whose second view starts at 4). An object's fields
are the offsets accessed in it, with the access's types and a type variable for what the field
holds; a field holding a pointer gives the structure a pointer to another (or the same) object.

Functions are polymorphic: they are taken callees first (by strongly connected components of the
call graph), and what a callee does with a parameter's memory is a template (fields, the types they
are accessed with, what their pointers reach, a pointer back to a structure higher up the template
for a recursive one) that a call copies onto the memory its argument points to, instead of making
the argument's object the parameter's: one object per caller, not one for all (memcpy-like helpers
and functions given frames merged everything into one object of 67 fields otherwise). A template is
a copy even of memory reachable from fixed addresses (share=False): FreeRTOS's pvPortMalloc returns
a pointer into its heap's free list, and sharing that made every allocated structure one. Two
objects that meet at different offsets (one inside the other, or a pun: FreeRTOS walks a list from
its end marker as if it were an item) are kept apart (shifted=False), not merged into one with
fields of both.

Measured against the source's structures (score: a parameter's fields against the debug
information's members, by offset and size; a structure a file only declares is looked up by name):
on FreeRTOS GCC -Os, 618 of 622 fields found are the source's (99 %), 32 % of its members found
(code reaches part of a structure through a given pointer); clang -O2 91 %, RISC-V GCC -Os 95 %,
GCC -O0 99 %. Plain unification: 43 %; with templates but shifted merges: 77 %; shared: 80 %.

What comes out is a proposal for reading, not a fact: unification merges what flows together inside
a function, and a structure whose fields are only reached through other pointers stays apart from
its twins (a list item reached as an item's next is not known to be an item unless something says
so). The gate decides nothing here; a rewrite that uses these shapes will be checked like every
other.
"""
import re

from dread import cparse as C


class Shapes:
    """The unification state: type variables (values) and objects (memory with fields)."""

    def __init__(self):
        self.tv_parent = {}
        self.points = {}                # type variable root -> (object, offset)
        self.obj_parent = {}            # object -> (parent object, shift): x in obj = x + shift in parent
        self.fields = {}                # object root -> {offset: Field}
        self.conflicts = set()          # object roots seen at two offsets of themselves (arrays, walks)
        self.count = 0
        self.globals = {}               # address -> object of the memory there
        self.pending = []               # (kind, a, b, k): relations that wait for a pointer
        self.shifted = False            # whether objects that meet at different offsets are merged
        self.share = False              # whether templates refer to memory reachable from fixed addresses
        self.embedded = set()           # (root, offset, root, offset) of those kept apart

    # --- type variables
    def tv(self, key):
        if key not in self.tv_parent:
            self.tv_parent[key] = key
        return key

    def find(self, key):
        self.tv(key)
        root = key
        while self.tv_parent[root] != root:
            root = self.tv_parent[root]
        while self.tv_parent[key] != root:
            self.tv_parent[key], key = root, self.tv_parent[key]
        return root

    def unify(self, a, b):
        work = [(a, b)]
        while work:
            a, b = work.pop()
            ra, rb = self.find(a), self.find(b)
            if ra == rb:
                continue
            self.tv_parent[rb] = ra
            pa, pb = self.points.pop(ra, None), self.points.pop(rb, None)
            if pa and pb:
                self.points[ra] = pa
                work += self.unify_loc(pa, pb)
            elif pa or pb:
                self.points[ra] = pa or pb

    def pointer(self, key):
        """The location key's value points to (made when it has none)."""
        r = self.find(key)
        if r not in self.points:
            self.points[r] = (self.new_object(), 0)
        return self.points[r]

    def has_pointer(self, key):
        return self.find(key) in self.points

    # --- objects
    def new_object(self):
        self.count += 1
        o = "o%d" % self.count
        self.obj_parent[o] = (o, 0)
        self.fields[o] = {}
        return o

    def obj_find(self, o):
        """(root, shift): offset x of o is offset x + shift of root."""
        shift = 0
        path = []
        while self.obj_parent[o][0] != o:
            p, d = self.obj_parent[o]
            path.append((o, d))
            shift += d
            o = p
        total = shift
        for node, d in path:                  # (path compression with the shifts summed)
            self.obj_parent[node] = (o, total)
            total -= d
        return o, shift

    def loc(self, location):
        o, off = location
        root, shift = self.obj_find(o)
        return root, off + shift

    def unify_loc(self, a, b):
        """Make two locations one; returns type variable pairs to unify (the fields' contents)."""
        (ra, oa), (rb, ob) = self.loc(a), self.loc(b)
        if ra == rb:
            if oa != ob:
                self.conflicts.add(ra)
            return []
        if oa != ob and not self.shifted:   # (one inside the other at another offset: kept apart)
            self.embedded.add((ra, oa, rb, ob))
            return []
        # rb's offset x is ra's offset x + (oa - ob)
        shift = oa - ob
        self.obj_parent[rb] = (ra, shift)
        out = []
        for off, f in self.fields.pop(rb).items():
            g = self.fields[ra].get(off + shift)
            if g is None:
                self.fields[ra][off + shift] = f
            else:
                g.types |= f.types
                out.append((g.content, f.content))
        if rb in self.conflicts:
            self.conflicts.add(ra)
        return out

    def field(self, location, ctype=None):
        root, off = self.loc(location)
        f = self.fields[root].get(off)
        if f is None:
            f = self.fields[root][off] = Field(("field", root, off, self.count))
        if ctype:
            f.types.add(ctype)
        return f

    def global_object(self, address):
        if address not in self.globals:
            self.globals[address] = self.new_object()
        return self.globals[address]

    def unify_locations(self, a, b):
        for x, y in self.unify_loc(a, b):
            self.unify(x, y)

    def global_roots(self):
        """The object roots reachable from memory at fixed addresses (through pointer fields)."""
        seen, work = set(), [self.loc((o, 0))[0] for o in self.globals.values()]
        while work:
            r = work.pop()
            if r in seen:
                continue
            seen.add(r)
            for f in self.fields[r].values():
                t = self.find(f.content)
                if t in self.points:
                    work.append(self.loc(self.points[t])[0])
        return seen

    def template(self, location, shared, depth=4, path=()):
        """What the memory at location looks like, to copy onto another: ("copy", {offset: (types,
        child)}) relative to location, ("shared", root, offset) for memory reachable from fixed
        addresses, ("back", k, delta) for a pointer to the k-th structure up the path."""
        root, off0 = self.loc(location)
        if root in shared and self.share:
            return ("shared", root, off0)
        for k, (r, o) in enumerate(path):
            if r == root:
                return ("back", k, off0 - o)
        if depth == 0 or not self.fields[root]:
            return None
        out = {}
        for off, f in self.fields[root].items():
            t = self.find(f.content)
            child = None
            if t in self.points:
                child = self.template(self.points[t], shared, depth - 1, path + ((root, off0),))
            out[off - off0] = (frozenset(f.types), child)
        return ("copy", out)

    def instantiate(self, tmpl, location, stack=()):
        """Copy a template onto the memory at location (an object of the caller)."""
        if tmpl is None:
            return
        if tmpl[0] == "shared":
            self.unify_locations(location, (tmpl[1], tmpl[2]))
        elif tmpl[0] == "back":
            if tmpl[1] < len(stack):
                o, off = stack[tmpl[1]]
                self.unify_locations(location, (o, off + tmpl[2]))
        else:
            stack = stack + (location,)
            for rel, (types, child) in tmpl[1].items():
                f = self.field((location[0], location[1] + rel))
                f.types |= types
                if child is not None:
                    self.instantiate(child, self.pointer(f.content), stack)


class Field:
    __slots__ = ("content", "types")

    def __init__(self, content):
        self.content, self.types = content, set()


ACCESS = re.compile(r"^volatile (\w+) \*$")


def _access(e):
    """(base expression, offset, type) of *(volatile T *)(base + k) or *(volatile T *)base."""
    if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast):
        m = ACCESS.match(" ".join(e.e.type.split()))
        if not m:
            return None
        a = e.e.e
        if isinstance(a, C.Binary) and a.op in ("+", "-") and isinstance(a.r, C.Num) and isinstance(a.r.value, int):
            return a.l, a.r.value if a.op == "+" else -a.r.value, m.group(1)
        return a, 0, m.group(1)
    return None


class Collector:
    """Walks one function's readable tree and feeds the unification. key(v): the type variable of a
    variable of the function. summaries: {callee: {"params": {i: template}, "ret": template}} of the
    callees already done (their templates are copied); together: the functions of this one's
    component (their parameters are unified, as one function's)."""

    def __init__(self, shapes, fn, macros, summaries=None, together=()):
        self.s, self.fn, self.macros = shapes, fn, macros   # macros: {name: (address, type)}
        self.summaries, self.together = summaries or {}, set(together)

    def key(self, name):
        return ("var", self.fn.name, name)

    def run(self):
        for i, (_, pname) in enumerate(self.fn.params):
            self.s.unify(self.key(pname), ("param", self.fn.name, i))
        for st in self.fn.body.body:
            self.stmt(st)

    def stmt(self, st):
        if isinstance(st, C.ExprStmt):
            self.value(st.e)
        elif isinstance(st, C.Return):
            if st.e is not None:
                v = self.value(st.e)
                if v is not None:
                    self.s.unify(v, ("ret", self.fn.name))
        elif isinstance(st, (C.If, C.While, C.DoWhile)):
            self.value(st.cond)
        elif isinstance(st, C.Switch):
            self.value(st.e)
        elif isinstance(st, C.For):
            for x in (st.init if not isinstance(st.init, C.Decl) else None, st.cond, st.step):
                if x is not None:
                    self.value(x)
        elif isinstance(st, C.Decl):
            for d in st.items:
                if d.init is not None:
                    v = self.value(d.init)
                    if v is not None:
                        self.s.unify(self.key(d.name), v)
        from dread.naming import _inner
        for inner in _inner(st):
            self.stmt(inner)

    def address(self, e):
        """The location an address expression points to, or None (an integer we know nothing of)."""
        if isinstance(e, C.Num) and isinstance(e.value, int):
            return (self.s.global_object(e.value), 0)
        if isinstance(e, C.Binary) and e.op in ("+", "-") and isinstance(e.r, C.Num) and isinstance(e.r.value, int):
            base = self.address(e.l)
            if base is None:
                return None
            k = e.r.value if e.op == "+" else -e.r.value
            return (base[0], base[1] + k)
        v = self.value(e)
        if v is None:
            return None
        return self.s.pointer(v)

    def value(self, e):
        """The type variable of e's value (None when it is no plain value: arithmetic, a comparison),
        with what e says on the way (its accesses, assignments, calls)."""
        s = self.s
        if isinstance(e, C.Name):
            if e.id in self.macros:
                address, ctype = self.macros[e.id]
                return s.field((s.global_object(address), 0), ctype).content
            return self.key(e.id)
        if isinstance(e, C.Num):
            return None
        acc = _access(e)
        if acc is not None:
            base, k, ctype = acc
            location = self.address(base)
            if location is None:
                return None
            return s.field((location[0], location[1] + k), ctype).content
        if isinstance(e, C.Assign):
            v = self.value(e.value)
            if isinstance(e.target, C.Name) and e.target.id not in self.macros:
                t = self.key(e.target.id)
            else:
                t = self.value(e.target)
            if e.op == "=" and t is not None and v is not None:
                s.unify(t, v)
            return t
        if isinstance(e, C.Cast):
            inner = self.value(e.e)
            return inner if " ".join(e.type.split()) in ("u32", "s32") else None
        if isinstance(e, C.Call):
            name = e.f.id if isinstance(e.f, C.Name) else None
            if name in self.summaries:          # (done: its templates copied onto the arguments' memory)
                summary = self.summaries[name]
                for i, a in enumerate(e.args):
                    tmpl = summary["params"].get(i)
                    if tmpl is None:
                        self.value(a)
                        continue
                    location = self.address(a)
                    if location is not None:
                        s.instantiate(tmpl, location)
                t = ("result", self.fn.name, id(e))
                if summary.get("ret") is not None:
                    s.instantiate(summary["ret"], s.pointer(t))
                return t
            for i, a in enumerate(e.args):
                v = self.value(a)
                if name not in self.together:
                    continue
                if v is not None:
                    s.unify(v, ("param", name, i))
                elif isinstance(a, C.Num) and isinstance(a.value, int) and a.value >= 0x1000:
                    s.pending.append(("const-arg", ("param", name, i), a.value, 0))
            return ("ret", name) if name in self.together else ("result", self.fn.name, id(e))
        if isinstance(e, C.Binary):
            if e.op in ("+", "-") and isinstance(e.r, C.Num) and isinstance(e.r.value, int):
                base = self.value(e.l)
                if base is not None:
                    t = ("tmp", self.fn.name, id(e))
                    s.pending.append(("offset", t, base, e.r.value if e.op == "+" else -e.r.value))
                    return t
                return None
            if e.op in ("==", "!="):
                a, b = self.value(e.l), self.value(e.r)
                if a is not None and b is not None:
                    s.pending.append(("compare", a, b, 0))
                return None
            self.value(e.l)
            self.value(e.r)
            return None
        if isinstance(e, C.Cond):
            self.value(e.c)
            a, b = self.value(e.t), self.value(e.f)
            if a is not None and b is not None:
                s.unify(a, b)
            return a or b
        if isinstance(e, C.Unary):
            self.value(e.e)
            return None
        if isinstance(e, C.Comma):
            out = None
            for x in e.items:
                out = self.value(x)
            return out
        return None


def settle(shapes):
    """The relations that wait for a pointer, again until none fires: an offset (v = w + k) when
    either side points somewhere, a comparison of two pointers, a constant passed for a parameter
    that points somewhere (the memory at that address is such an object)."""
    s = shapes
    changed = True
    while changed:
        changed = False
        rest = []
        for kind, a, b, k in s.pending:
            if kind == "offset":
                if s.has_pointer(b) or s.has_pointer(a):
                    o, off = s.pointer(b)
                    pa = s.find(a)
                    if pa in s.points:
                        for x, y in s.unify_loc(s.points[pa], (o, off + k)):
                            s.unify(x, y)
                    else:
                        s.points[pa] = (o, off + k)
                    changed = True
                    continue
            elif kind == "compare":
                if s.has_pointer(a) or s.has_pointer(b):
                    s.unify(a, b)
                    changed = True
                    continue
            elif kind == "const-arg":
                if s.has_pointer(a):
                    for x, y in s.unify_loc(s.pointer(a), (s.global_object(b), 0)):
                        s.unify(x, y)
                    changed = True
                    continue
            rest.append((kind, a, b, k))
        s.pending = rest


def callees(fn):
    from dread.cflow import walk_expr
    from dread.naming import _exprs, _inner
    out = set()

    def visit(st):
        for e in _exprs(st):
            for x in walk_expr(e):
                if isinstance(x, C.Call) and isinstance(x.f, C.Name):
                    out.add(x.f.id)
        for inner in _inner(st):
            visit(inner)
    for st in fn.body.body:
        visit(st)
    return out


def components(graph):
    """Strongly connected components of {node: {successors}}, callees before callers (Tarjan's,
    without recursion)."""
    index, low, on, stack, out = {}, {}, set(), [], []
    counter = [0]
    for start in sorted(graph):
        if start in index:
            continue
        work = [(start, iter(sorted(graph.get(start, ()))))]
        index[start] = low[start] = counter[0]
        counter[0] += 1
        stack.append(start)
        on.add(start)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in graph:
                    continue
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter(sorted(graph.get(nxt, ())))))
                    advanced = True
                    break
                if nxt in on:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    x = stack.pop()
                    on.discard(x)
                    comp.append(x)
                    if x == node:
                        break
                out.append(sorted(comp))
    return out


def analyse(trees, depth=4, share=False):
    """Shapes from [(tree, fn, macros)]: every function's readable tree with its macros (name ->
    (address, type)), callees first; returns the Shapes with .summaries."""
    s = Shapes()
    s.share = share
    by_name = {fn.name: (tree, fn, macros) for tree, fn, macros in trees}
    graph = {name: callees(fn) & set(by_name) for name, (_, fn, _) in by_name.items()}
    summaries = {}
    for comp in components(graph):
        for name in comp:
            tree, fn, macros = by_name[name]
            Collector(s, fn, macros, summaries, comp).run()
        settle(s)
        shared = s.global_roots()
        for name in comp:
            fn = by_name[name][1]
            summary = {"params": {}, "ret": None}
            for i in range(len(fn.params)):
                r = s.find(("param", name, i))
                if r in s.points:
                    summary["params"][i] = s.template(s.points[r], shared, depth)
            r = s.find(("ret", name))
            if r in s.points:
                summary["ret"] = s.template(s.points[r], shared, depth)
            summaries[name] = summary
    s.summaries = summaries
    return s


def structures(s, least=2):
    """{object root: [(offset, types, points to (root, offset) or None)]} of the objects with at
    least least fields, offsets from the smallest."""
    out = {}
    for root, fields in s.fields.items():
        if s.obj_parent[root][0] != root or len(fields) < least:
            continue
        rows = []
        for off in sorted(fields):
            f = fields[off]
            r = s.find(f.content)
            target = s.loc(s.points[r]) if r in s.points else None
            rows.append((off, sorted(f.types), target))
        out[root] = rows
    return out


def report(s, trees, top=20, least=2):
    """Lines on the largest structures: their fields (offset:types, ->object@offset for a pointer),
    the parameters pointing to them and the fixed addresses inside them."""
    st = structures(s, least)
    pp = params(s, [(fn.name, len(fn.params)) for _, fn, _ in trees])
    users, inside = {}, {}
    for (f, i), (root, off) in pp.items():
        users.setdefault(root, []).append("%s(%d)@%d" % (f, i, off))
    for a, o in s.globals.items():
        r, off = s.loc((o, 0))
        inside.setdefault(r, []).append("0x%x@%d" % (a, off))
    out = ["dread readable --shapes: %d functions, %d structures with %d fields or more" % (len(trees), len(st), least)]
    for root, rows in sorted(st.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:top]:
        u, g = sorted(users.get(root, [])), sorted(inside.get(root, []))
        out.append("%s: %d fields; parameters: %s; fixed addresses: %s" % (
            root, len(rows), ", ".join(u[:6]) + (" and %d more" % (len(u) - 6) if len(u) > 6 else "") or "none",
            ", ".join(g[:4]) + (" and %d more" % (len(g) - 4) if len(g) > 4 else "") or "none"))
        out.append("    " + " ".join("%d:%s%s" % (off, "/".join(ty) or "?", "->%s@%d" % tg if tg else "")
                                    for off, ty, tg in rows))
    return out


def params(s, names):
    """{(function, index): (object root, offset)} of the parameters that point somewhere."""
    out = {}
    for name, count in names:
        for i in range(count):
            r = s.find(("param", name, i))
            if r in s.points:
                out[(name, i)] = s.loc(s.points[r])
    return out


SIZES = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "u64": 8, "s64": 8}


def fields_at(s, location):
    """{(offset, size)} of the fields of the object at location, offsets relative to it (none before
    it: a header in front of a structure, as an allocator's, is no member of it)."""
    root, off0 = s.loc(location)
    out = set()
    for off, f in s.fields[root].items():
        if off < off0:
            continue
        for t in f.types or {"u32"}:
            out.add((off - off0, SIZES.get(t, 4)))
    return out


def source_layouts(elf_path):
    """{function entry: {parameter index: (struct name, {(offset, size)} of its members' leaves)}} of
    the parameters that are pointers to a structure, from the ELF's debug information."""
    from elftools.elf.elffile import ELFFile
    out = {}
    complete = {}                           # struct name -> its definition (a file that only declares it:
                                            # an opaque handle's struct, defined in another file)

    def strip(die):
        while die is not None and die.tag in ("DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type"):
            if "DW_AT_type" not in die.attributes:
                return None
            die = die.get_DIE_from_attribute("DW_AT_type")
        if die is not None and "DW_AT_declaration" in die.attributes and "DW_AT_name" in die.attributes:
            die = complete.get((die.tag, die.attributes["DW_AT_name"].value), die)
        return die

    def size_of(die):
        die = strip(die)
        if die is None:
            return None
        if "DW_AT_byte_size" in die.attributes:
            return die.attributes["DW_AT_byte_size"].value
        if die.tag == "DW_TAG_pointer_type":
            return 4
        return None

    def leaves(die, base, into, depth=0):
        die = strip(die)
        if die is None or depth > 6:
            return
        if die.tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
            for m in die.iter_children():
                if m.tag != "DW_TAG_member" or "DW_AT_type" not in m.attributes:
                    continue
                loc = m.attributes.get("DW_AT_data_member_location")
                off = loc.value if loc is not None and isinstance(loc.value, int) else 0
                leaves(m.get_DIE_from_attribute("DW_AT_type"), base + off, into, depth + 1)
        elif die.tag == "DW_TAG_array_type":
            elem = die.get_DIE_from_attribute("DW_AT_type") if "DW_AT_type" in die.attributes else None
            esize = size_of(elem) if elem is not None else None
            count = None
            for sub in die.iter_children():
                if sub.tag == "DW_TAG_subrange_type":
                    if "DW_AT_count" in sub.attributes:
                        count = sub.attributes["DW_AT_count"].value
                    elif "DW_AT_upper_bound" in sub.attributes:
                        count = sub.attributes["DW_AT_upper_bound"].value + 1
            if esize and count:
                for k in range(min(count, 64)):
                    leaves(elem, base + k * esize, into, depth + 1)
        else:
            size = size_of(die)
            if size:
                into.add((base, size))

    with open(elf_path, "rb") as f:
        dwarf = ELFFile(f).get_dwarf_info()
        for cu in dwarf.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag in ("DW_TAG_structure_type", "DW_TAG_union_type") and "DW_AT_name" in die.attributes \
                        and "DW_AT_declaration" not in die.attributes:
                    complete.setdefault((die.tag, die.attributes["DW_AT_name"].value), die)
        for cu in dwarf.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_subprogram" or "DW_AT_low_pc" not in die.attributes:
                    continue
                entry = die.attributes["DW_AT_low_pc"].value & ~1
                k = 0
                for p in die.iter_children():
                    if p.tag != "DW_TAG_formal_parameter":
                        continue
                    t = strip(p.get_DIE_from_attribute("DW_AT_type")) if "DW_AT_type" in p.attributes else None
                    if t is not None and t.tag == "DW_TAG_pointer_type" and "DW_AT_type" in t.attributes:
                        target = strip(t.get_DIE_from_attribute("DW_AT_type"))
                        if target is not None and target.tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
                            into = set()
                            leaves(target, 0, into)
                            name = target.attributes.get("DW_AT_name")
                            out.setdefault(entry, {})[k] = (name.value.decode() if name else "?", into)
                    k += 1
    return out


def score(s, program, functions, elf_path):
    """The parameters' recovered fields against the source's structures: ({struct: [found, right,
    in the source]}, totals). A field is right when the source has a member leaf at that offset with
    that size."""
    known = source_layouts(elf_path)
    per, total = {}, [0, 0, 0, 0]
    for name, count in functions:
        fn = program.by_name.get(name)
        src = known.get(fn.entry, {}) if fn is not None else {}
        for i, (sname, leaves) in src.items():
            if i >= count:
                continue
            r = s.find(("param", name, i))
            mine = fields_at(s, s.points[r]) if r in s.points else set()
            right = mine & leaves
            row = per.setdefault(sname, [0, 0, 0, 0])
            for acc in (row, total):
                acc[0] += len(mine)
                acc[1] += len(right)
                acc[2] += len(leaves)
                acc[3] += 1
    return per, total
