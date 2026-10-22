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
        self.kind_parent = {}           # object -> (object, shift): what the memory is, as obj_parent
        self.embeds = set()             # (kind, offset, kind, offset): one kind's memory inside another's
        self.calls = []                 # (the argument's location, the callee's parameter's): kinds (kinds())
        self.frames = set()             # objects that are a function's stack frame (reached from sp0)
        self.var_webs = {}              # (function, variable) -> its values' keys, one per web
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
            if not self.shifted and ra in self.points and rb in self.points:
                (xa, oa), (xb, ob) = self.loc(self.points[ra]), self.loc(self.points[rb])
                if oa != ob:                # (pointers that cannot be aligned: the two values stay apart,
                    if xa != xb:            # or the one left would point to one of the two objects only
                        self.embeds.add((xa, oa, xb, ob))   # and take the other's accesses there: FreeRTOS's
                    else:                   # pxIndex, a list's own end marker and its items, made the
                        self.conflicts.add(xa)      # items' kind the task block's)
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
        self.kind_parent[o] = (o, 0)
        self.fields[o] = {}
        return o

    # --- kinds: which objects hold the same kind of structure (a template copied from a callee's
    # parameter makes the caller's memory there the callee's kind); objects stay apart
    def kind_find(self, o):
        shift = 0
        while self.kind_parent[o][0] != o:
            p, d = self.kind_parent[o]
            shift += d
            o = p
        return o, shift

    def kind_of(self, location):
        root, off = self.loc(location)
        k, shift = self.kind_find(root)
        return k, off + shift

    def link_kinds(self, a, b):
        """The memory at locations a and b is of one kind: kinds that meet at one offset become one,
        at different offsets one is recorded inside the other (embeds)."""
        (ka, oa), (kb, ob) = self.kind_of(a), self.kind_of(b)
        if ka == kb:
            return
        if oa != ob:
            self.embeds.add((ka, oa, kb, ob))
            return
        self.kind_parent[kb] = (ka, 0)

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
        return ("copy", out, (root, off0))

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
SMALLEST_ADDRESS = 0x100            # a constant below is a number (NULL, a count), not an address
FRAME = "sp0"                       # the lifters' variable for the stack frame's address


def _access(e):
    """(base expression, offset, type) of *(volatile T *)(base + k) or *(volatile T *)base."""
    if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast):
        m = ACCESS.match(" ".join(e.e.type.split()))
        if not m:
            return None
        a = e.e.e
        if isinstance(a, C.Binary) and a.op in ("+", "-") and isinstance(a.r, C.Num) and isinstance(a.r.value, int):
            return a.l, signed32(a.r.value if a.op == "+" else -a.r.value), m.group(1)
        return a, 0, m.group(1)
    return None


def signed32(k):
    """An offset as the 32-bit arithmetic means it: + 0xffffffffu is - 1 (printi's buffer walk made a
    structure with a pad of 4 GB of it)."""
    k &= 0xffffffff
    return k - 0x100000000 if k & 0x80000000 else k


class Collector:
    """Walks one function's readable tree and feeds the unification. key(v): the type variable of a
    variable of the function. summaries: {callee: {"params": {i: template}, "ret": template}} of the
    callees already done (their templates are copied); together: the functions of this one's
    component (their parameters are unified, as one function's)."""

    def __init__(self, shapes, fn, macros, summaries=None, together=()):
        self.s, self.fn, self.macros = shapes, fn, macros   # macros: {name: (address, type)}
        self.summaries, self.together = summaries or {}, set(together)

    def key(self, name, write=False, entry=False):
        """The value of variable name here: one per web of its definitions (webs), so that a variable
        the C keeps whole (one asm writes: RISC-V's a0, the queue and later its list at 16) is as
        many values as it holds."""
        if entry:
            w = self.entry_web.get(name)
        elif self.node is None:
            w = None
        elif write:
            w = self.def_web.get((name, self.node))
        else:
            w = self.use_web.get((name, self.node))
        k = ("var", self.fn.name, name, w)
        self.s.var_webs.setdefault((self.fn.name, name), set()).add(k)
        return k

    def webs(self):
        """Each definition's web and each use's (definitions that reach a common use are one web, as
        dread.tidy webs), over the body's flow graph with its conditional blocks spread out; the
        statements' nodes (a statement inside one without a node of its own takes its parent's)."""
        from dread.cflow import Graph
        from dread.tidy import flat_view
        self.node, self.node_of = None, {}
        self.def_web, self.use_web, self.entry_web = {}, {}, {}
        try:
            view = flat_view(self.fn)
            g = Graph(view)
            reach = g.reaching()
        except C.ParseError:
            return
        for n in g.nodes:
            self.node_of[id(n.stmt)] = n.index
        for stand_in, original in view.pairs:
            self.node_of[id(original)] = self.node_of.get(id(stand_in))
        parent = {}

        def find(x):
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x
        from dread.tidy import simple
        for n in g.nodes:
            for v in n.reads:
                defs = sorted(reach[n.index].get(v) or ())
                for d in defs[1:]:
                    a, b = find((v, defs[0])), find((v, d))
                    if a != b:
                        parent[b] = a
                # (a statement with others in it, a loop or a block, that reads and writes v: its node
                # cannot say which read sees its own write, so they are one web; a simple one reads first)
                if defs and v in n.writes and not simple(n.stmt):
                    a, b = find((v, defs[0])), find((v, n.index))
                    if a != b:
                        parent[b] = a
        for n in g.nodes:
            for v in n.writes:
                self.def_web[(v, n.index)] = find((v, n.index))[1]
            for v in n.reads:
                defs = sorted(reach[n.index].get(v) or ())
                if defs:
                    self.use_web[(v, n.index)] = find((v, defs[0]))[1]
        self.entry_web = {v: find((v, -1))[1] for v in g.scope}

    def run(self):
        self.webs()
        for i, (_, pname) in enumerate(self.fn.params):
            self.s.unify(self.key(pname, entry=True), ("param", self.fn.name, i))
        for st in self.fn.body.body:
            self.stmt(st)

    def stmt(self, st):
        outer = self.node
        self.node = self.node_of.get(id(st), self.node)
        self._stmt(st)
        self.node = outer

    def _stmt(self, st):
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
                        self.s.unify(self.key(d.name, write=True), v)
        from dread.naming import _inner
        for inner in _inner(st):
            self.stmt(inner)

    def address(self, e):
        """The location an address expression points to, or None (an integer we know nothing of; a
        small constant: NULL is no memory at address 0, and FreeRTOS hands NULL for the current task
        to a dozen functions, which made every structure given NULL one kind)."""
        if isinstance(e, C.Num) and isinstance(e.value, int):
            return (self.s.global_object(e.value), 0) if e.value >= SMALLEST_ADDRESS else None
        if isinstance(e, C.Binary) and e.op in ("+", "-") and isinstance(e.r, C.Num) and isinstance(e.r.value, int):
            base = self.address(e.l)
            if base is None:
                return None
            k = signed32(e.r.value if e.op == "+" else -e.r.value)
            return (base[0], base[1] + k)
        v = self.value(e)
        if v is None:
            return None
        location = self.s.pointer(v)
        if isinstance(e, C.Name) and e.id == FRAME:
            self.s.frames.add(location[0])
        return location

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
                t = self.key(e.target.id, write=True)
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
                        if structure(tmpl):
                            s.calls.append((location, tmpl[2]))
                t = ("result", self.fn.name, id(e))
                if summary.get("ret") is not None:
                    # (its fields, but no kind: a function the source declares void hands back what
                    # its last call left in the result register, FreeRTOS's vListInsert's list, and
                    # every caller that passes that on linked lists with tasks)
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
                    k = signed32(e.r.value if e.op == "+" else -e.r.value)
                    frame = isinstance(e.l, C.Name) and e.l.id == FRAME
                    if frame or s.has_pointer(base):    # (known now: the variable it goes to points there
                        o, off = s.pointer(base)        # before a call makes it a fresh object of its own)
                        if frame:
                            s.frames.add(o)
                        s.points[s.find(t)] = (o, off + k)
                    else:
                        s.pending.append(("offset", t, base, k))
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


def structure(tmpl):
    """Whether a template says its memory is a structure: two fields or more at or after the pointer.
    A header in front alone (vPortFree's) says nothing, nor does one field: memset, memcpy and strcmp
    touch offset 0 of whatever they get, and made task blocks, queues and timer messages one kind."""
    return tmpl is not None and tmpl[0] == "copy" and sum(1 for rel in tmpl[1] if rel >= 0) >= 2


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
    return {entry: {i: (names[-1], set(leaves)) for i, (names, leaves) in params.items()}
            for entry, params in source_params(elf_path).items()}


def source_params(elf_path, pointers=None):
    """{function entry: {parameter index: ([the type's names, its typedefs first and the structure's
    tag last], {(offset, size): [the member names on the way to that leaf]})}} of the parameters that
    are pointers to a structure, from the ELF's debug information (pyelftools). pointers, a dict:
    gets {function entry: {parameter index: {offsets of the leaves that point to a structure}}}."""
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

    def leaves(die, base, into, depth=0, path=(), ptrs=None):
        die = strip(die)
        if die is None or depth > 6:
            return
        if die.tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
            for m in die.iter_children():
                if m.tag != "DW_TAG_member" or "DW_AT_type" not in m.attributes:
                    continue
                loc = m.attributes.get("DW_AT_data_member_location")
                off = loc.value if loc is not None and isinstance(loc.value, int) else 0
                mname = m.attributes.get("DW_AT_name")
                leaves(m.get_DIE_from_attribute("DW_AT_type"), base + off, into, depth + 1,
                       path + ((mname.value.decode(errors="replace"),) if mname else ()), ptrs)
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
                    leaves(elem, base + k * esize, into, depth + 1, path, ptrs)
        else:
            size = size_of(die)
            if size:
                into.setdefault((base, size), list(path))
                if ptrs is not None and die.tag == "DW_TAG_pointer_type" and "DW_AT_type" in die.attributes:
                    pointee = strip(die.get_DIE_from_attribute("DW_AT_type"))
                    if pointee is not None and pointee.tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
                        ptrs.add(base)

    def type_names(die):
        """The names on the way from a parameter's pointer target to its structure."""
        names = []
        while die is not None and die.tag in ("DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type"):
            if die.tag == "DW_TAG_typedef" and "DW_AT_name" in die.attributes:
                names.append(die.attributes["DW_AT_name"].value.decode(errors="replace"))
            if "DW_AT_type" not in die.attributes:
                return names, None
            die = die.get_DIE_from_attribute("DW_AT_type")
        return names, die

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
                    handle, t = type_names(p.get_DIE_from_attribute("DW_AT_type")) if "DW_AT_type" in p.attributes \
                        else ([], None)
                    t = strip(t)
                    if t is not None and t.tag == "DW_TAG_pointer_type" and "DW_AT_type" in t.attributes:
                        names, target = type_names(t.get_DIE_from_attribute("DW_AT_type"))
                        target = strip(target)
                        if target is not None and target.tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
                            into, ptrs = {}, set()
                            leaves(target, 0, into, ptrs=ptrs)
                            if pointers is not None:
                                pointers.setdefault(entry, {})[k] = ptrs
                            name = target.attributes.get("DW_AT_name")
                            out.setdefault(entry, {})[k] = (handle + names + [name.value.decode() if name else "?"],
                                                            into)
                    k += 1
    return out


def score_pointers(program, p, elf_path):
    """The plan's pointer fields against the source (debug information), for the structures of
    typed parameters whose source type points to a structure, each pair of our structure and the
    source's once: {"fields": pointer fields, "right": of them pointers to a structure in the
    source, "missed": the source's pointers to a structure at word fields the plan left integers}."""
    ptrs = {}
    src = source_params(elf_path, pointers=ptrs)
    regs = list(program.target.abi.arg_regs)
    seen, out = set(), {"fields": 0, "right": 0, "missed": 0}
    for f, variables_ in sorted(p.typed.items()):
        fn = program.by_name.get(f)
        params = src.get(fn.entry, {}) if fn is not None else {}
        for v, k in sorted(variables_.items()):
            if v not in regs or regs.index(v) not in params or k not in p.structs:
                continue
            i = regs.index(v)
            if (k, params[i][0][-1]) in seen:
                continue
            seen.add((k, params[i][0][-1]))
            mine, theirs = p.pointers.get(k, {}), ptrs.get(fn.entry, {}).get(i, set())
            out["fields"] += len(mine)
            out["right"] += sum(1 for off in mine if off in theirs)
            out["missed"] += sum(1 for off, t in p.structs[k] if t == "u32" and off in theirs and off not in mine)
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


# --- C structures for readable C ----------------------------------------------------------------

class Kind:
    """A kind of memory: its fields ({offset: {type: count}}) and where values point into it
    ({offset: count}); its base is where most of them point (a structure's start)."""
    __slots__ = ("root", "fields", "targets", "base")

    def __init__(self, root):
        self.root, self.fields, self.targets, self.base = root, {}, {}, None


def function_variables(fn):
    """The function's parameters and the variables its body declares at the top level."""
    out = [p[1] for p in fn.params]
    for st in fn.body.body:
        if isinstance(st, C.Decl):
            out += [d.name for d in st.items]
    return out


def pointer_candidates(fn):
    """The variables readable C may declare as pointers: declared u32 (a parameter or a plain local:
    a u64 that the lifters' carry arithmetic holds, t_7 = (u64)r5 + r0, cut to a pointer lost its
    carry and clang -O2's prvAddCurrentTaskToDelayedList woke its task at the wrong time) and
    dereferenced by the function itself (*(T *)(v + k)): a value it only copies, stores, compares or
    hands on stays an integer (a tick count stored in a list item's value field took a pointer type
    from what met it elsewhere)."""
    from dread.cflow import walk_expr
    from dread.naming import _exprs, _inner
    u32 = {n for t, n in fn.params if " ".join(t.split()) == "u32"}
    for st in fn.body.body:
        if isinstance(st, C.Decl) and " ".join(st.specs.split()) == "u32":
            u32.update(d.name for d in st.items if not d.ptr and not d.dims)
    bases = set()

    def visit(st):
        for e in _exprs(st):
            for x in walk_expr(e):
                acc = _access(x)
                if acc is not None and isinstance(acc[0], C.Name):
                    bases.add(acc[0].id)
        for inner in _inner(st):
            visit(inner)
    for st in fn.body.body:
        visit(st)
    return u32 & bases


def kinds(s, trees):
    """{kind root: Kind} of the analysed functions' memory, worked out on the final objects: an
    object's base is the lowest offset the functions' variables point to in it; a call's argument and the
    callee's parameter (s.calls) make their objects one kind when both are at their bases, so the
    bases line up (a list item a task block holds at 4 is inside it: s.embeds, not the task's kind).
    Merging objects or copying a template's inner parts says nothing about kinds: through FreeRTOS's
    rings of list items, list ends and task blocks that made everything one kind."""
    targets = {}
    for _, fn, _ in trees:
        for v in function_variables(fn):
            for key in s.var_webs.get((fn.name, v), ()):
                r = s.find(key)
                if r in s.points:
                    o, off = s.loc(s.points[r])
                    slot = targets.setdefault(o, {})
                    slot[off] = slot.get(off, 0) + 1
    # A structure's base is the lowest offset a variable points to: members it holds (a queue's two
    # lists at 16 and 36) and what is handed out past a header lie above it. (The most frequent
    # pointer made RISC-V's queues start at their first list, and queues and lists one kind.)
    base = {o: min(t) for o, t in targets.items()}
    parent = {}
    shape = {}                              # kind root -> {offset from its base: size}

    def find(o):
        while parent.get(o, o) != o:
            parent[o] = parent.get(parent[o], parent[o])
            o = parent[o]
        return o

    def layout_of(o):
        b = base.get(o, 0)
        out = {}
        for off, f in s.fields[o].items():
            if off >= b:
                out[off - b] = max(SIZES.get(t, 4) for t in (f.types or {"u32"}))
        return out

    def fits(x, y):
        """Whether two layouts can be one structure: the same size where both have a field, and no
        field of one across a field of the other (a queue's byte cRxLock at 68 is not a task block's
        word uxMutexesHeld there)."""
        a, b = shape.get(x, {}), shape.get(y, {})
        for off, size in a.items():
            if off in b:
                if b[off] != size:
                    return False
            elif any(o < off < o + z for o, z in b.items()) or any(off < o < off + size for o in b):
                return False
        return True

    def union(x, y):
        x, y = find(x), find(y)
        if x == y or not fits(x, y):
            return False
        parent[y] = x
        merged = dict(shape.get(x, {}))
        merged.update(shape.get(y, {}))
        shape[x] = merged
        return True
    for o, (p, _) in s.obj_parent.items():
        if p == o:
            shape[o] = layout_of(o)
    s.embeds = set()
    frames = {s.loc((o, 0))[0] for o in s.frames}
    links = []
    for a, b in s.calls:
        (ra, oa), (rb, ob) = s.loc(a), s.loc(b)
        if ra == rb:
            continue
        # (a stack frame says nothing either: GCC gives locals whose lives do not overlap one slot, and
        # FreeRTOS's prvStaticallyAllocatedCreator handed one slot as a static queue and later as a
        # static event group, which made the two one kind)
        if ra not in frames and oa == base.get(ra, 0) and ob == base.get(rb, 0):
            links.append((ra, rb))
        elif ra not in frames:
            s.embeds.add((ra, oa, rb, ob))
    # The strongest links first: the more identical fields two sides share, the surer they are one
    # structure; a link whose layouts do not fit is left (one structure handed to a helper that takes
    # several, clang's vPortFree whose free list walk reaches past its header, comes last and fails).
    def strength(link):
        a, b = shape.get(link[0], {}), shape.get(link[1], {})
        return -sum(1 for off, size in a.items() if b.get(off) == size)
    for ra, rb in sorted(links, key=lambda l: (strength(l), l)):
        union(ra, rb)
    # What sits at one offset of one kind is one kind: every list function's item, given as a task
    # block's xStateListItem at 4, is the same structure.
    changed = True
    while changed:
        changed = False
        place = {}
        for ra, oa, rb, ob in sorted(s.embeds):
            if ob != base.get(rb, 0):
                continue
            key = (find(ra), oa - base.get(ra, 0))
            inner = find(rb)
            if key not in place:
                place[key] = inner
            elif find(place[key]) != inner and union(place[key], inner):
                changed = True
    out = {}
    for o, (p, _) in s.obj_parent.items():
        if p != o:
            continue
        k = find(o)
        kind = out.setdefault(k, Kind(k))
        kind.base = 0
        b = base.get(o, 0)
        for off, f in s.fields[o].items():
            slot = kind.fields.setdefault(off - b, {})
            for t in f.types or {"u32"}:
                slot[t] = slot.get(t, 0) + 1
        for off, n in targets.get(o, {}).items():
            kind.targets[off - b] = kind.targets.get(off - b, 0) + n
    s.kind_root = {o: find(o) for o, (p, _) in s.obj_parent.items() if p == o}
    s.kind_base = base
    return out


def layout(kind):
    """[(offset from the base, type)] of the fields a C structure holds at their offsets without
    packing: at or after the base, naturally aligned, not overlapping a field before them; an offset
    read with several types takes its most frequent size, unsigned unless only signed reads it."""
    rows, end = [], 0
    for off in sorted(o for o in kind.fields if kind.base is not None and o >= kind.base):
        types = kind.fields[off]
        by_size = {}
        for t, c in types.items():
            by_size[SIZES.get(t, 4)] = by_size.get(SIZES.get(t, 4), 0) + c
        size = max(by_size, key=lambda z: (by_size[z], z))
        signed = [t for t in types if SIZES.get(t, 4) == size and t.startswith("s")]
        unsigned = [t for t in types if SIZES.get(t, 4) == size and not t.startswith("s")]
        ctype = ("s%d" if signed and not unsigned else "u%d") % (size * 8)
        rel = off - kind.base
        if rel % size or rel < end:
            continue
        rows.append((rel, ctype))
        end = rel + size
    return rows


def field_name(fields, rel):
    """A field's name: fields' ({offset: name}, from the readable_names table) or f_<offset>."""
    return (fields or {}).get(rel) or "f_%x" % rel


def struct_text(name, rows, fields=None, pointers=None):
    """The C definition of a structure with these fields, gaps filled with bytes; fields: their names
    by offset where they have one; pointers: {offset: the name of the structure the field points to},
    such a field declared volatile struct NAME * (a word, as its u32)."""
    lines, pos = ["struct %s" % name, "{"], 0
    for rel, ctype in rows:
        if rel > pos:
            lines.append("    u8 pad_%x[%d];" % (pos, rel - pos))
        if (pointers or {}).get(rel):
            lines.append("    volatile struct %s *%s;" % (pointers[rel], field_name(fields, rel)))
        else:
            lines.append("    %s %s;" % (ctype, field_name(fields, rel)))
        pos = rel + SIZES[ctype]
    lines.append("};")
    return "\n".join(lines)


TABLE = "readable_types"


class Plan:
    """What readable C declares (the image's readable_types table): structures ({name: [(offset,
    type)]}) and, per function, which variables point to one ({function: {variable: name}}) with the
    digest of the readable C it was made for ({function: digest}: dread.naming.digest)."""

    def __init__(self):
        self.structs, self.typed, self.digests = {}, {}, {}
        self.pointers = {}              # structure -> {offset: the structure the field points to}

    def to_json(self):
        out = {"version": 1, "structs": {n: [list(r) for r in rows] for n, rows in sorted(self.structs.items())},
               "functions": {f: {"digest": self.digests.get(f), "vars": dict(sorted(v.items()))}
                             for f, v in sorted(self.typed.items())}}
        if self.pointers:
            out["pointers"] = {n: {str(off): t for off, t in sorted(ps.items())}
                               for n, ps in sorted(self.pointers.items()) if ps}
        return out

    @classmethod
    def from_json(cls, data):
        p = cls()
        p.structs = {n: [tuple(r) for r in rows] for n, rows in data.get("structs", {}).items()}
        for f, entry in data.get("functions", {}).items():
            p.typed[f] = dict(entry.get("vars", {}))
            p.digests[f] = entry.get("digest")
        p.pointers = {n: {int(off): t for off, t in ps.items()} for n, ps in data.get("pointers", {}).items()}
        return p


def load_plan(image):
    import json
    import os
    path = image.table(TABLE)
    if not os.path.exists(path):
        return Plan()
    with open(path) as f:
        return Plan.from_json(json.load(f))


def store_plan(image, p):
    import json
    import os
    path = image.table(TABLE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(p.to_json(), f, indent=1)
        f.write("\n")
    os.replace(path + ".tmp", path)


def plan(s, trees, digests=None, least=2):
    """The plan for the analysed functions: a structure for every kind with a base and at least
    least fields, named s_1, s_2 ... in the order of the first (function, variable) pointing to it;
    a variable typed as a pointer to it when it points to the base. digests: {function: the digest
    of its readable C before names} (readable_trees)."""
    ks = kinds(s, trees)
    pointing = {}
    frames = {s.loc((o, 0))[0] for o in s.frames}
    for _, fn, _ in trees:
        for v in sorted(pointer_candidates(fn) - _uses(fn) - {FRAME}):
            seen = set()                    # (what each of its webs points to: one structure's base, or
            for key in s.var_webs.get((fn.name, v), ()):        # it stays an integer)
                r = s.find(key)
                if r in s.points:
                    o, off = s.loc(s.points[r])
                    k = s.kind_root.get(o)
                    # (a function's own stack frame is its locals, not a structure: GCC -O0's 253
                    # functions had one each)
                    seen.add(k if o not in frames and k in ks and off == s.kind_base.get(o, 0) else None)
            if len(seen) == 1 and None not in seen:
                pointing.setdefault(next(iter(seen)), []).append((fn.name, v))
    out = Plan()
    # (a structure has a field wider than a byte: bytes alone are a buffer walked, gizmo's strings, and
    # read better as the raw accesses than as r0->f_0 and r0->f_1)
    eligible = [k for k in ks if ks[k].base is not None and len(layout(ks[k])) >= least and k in pointing
                and any(SIZES[t] > 1 for _, t in layout(ks[k]))]
    names_of = {}
    for n, k in enumerate(sorted(eligible, key=lambda k: min(pointing[k])), 1):
        name = names_of[k] = "s_%d" % n
        out.structs[name] = layout(ks[k])
        for fname, v in pointing[k]:
            out.typed.setdefault(fname, {})[v] = name
    # A word field is a pointer to a structure when every object of the kind that points somewhere
    # from it points to the base of one structure (a list item's next and previous, its container).
    # (Also requiring a variable of that structure loaded from or stored into the field halved what
    # was found and kept the share the source declares so, 48 %: the wrong ones come from kinds made
    # one, a heap block and the queue allocated in it.)
    for k, name in names_of.items():
        rows, found = dict(out.structs[name]), {}
        for o, root in s.kind_root.items():
            if root != k:
                continue
            b = s.kind_base.get(o, 0)
            for off, f in s.fields.get(o, {}).items():
                t = s.find(f.content)
                if rows.get(off - b) != "u32" or t not in s.points:
                    continue
                o2, off2 = s.loc(s.points[t])
                k2 = s.kind_root.get(o2)
                to = names_of.get(k2) if o2 not in frames and off2 == s.kind_base.get(o2, 0) else None
                found.setdefault(off - b, set()).add(to)
        ptrs = {rel: next(iter(ts)) for rel, ts in found.items() if len(ts) == 1 and None not in ts}
        if ptrs:
            out.pointers[name] = ptrs
    out.digests = {f: (digests or {}).get(f) for f in out.typed}
    return out


def _uses(fn):
    """Variables the rewrite must leave alone: written other than by a plain assignment (+=, ++),
    their address taken, named in asm, declared with an initial value, or given the address of a C
    variable (a_1 = (u32)&s1: the lifter reads a stack argument and the one after it through it;
    typed, clang -O2 took the second read as outside s1, and xTaskCreateStatic read unmapped memory)."""
    from dread.cflow import walk_expr
    from dread.naming import _exprs, _inner
    out = set()

    def visit(st):
        if isinstance(st, C.Decl):
            out.update(d.name for d in st.items if d.init is not None or d.ptr or d.dims)
        if isinstance(st, C.Asm):
            for o in st.outputs + st.inputs:
                out.update(x.id for x in walk_expr(o.e) if isinstance(x, C.Name))
        for e in _exprs(st):
            for x in walk_expr(e):
                if isinstance(x, C.Assign) and x.op != "=" and isinstance(x.target, C.Name):
                    out.add(x.target.id)
                if isinstance(x, C.Assign) and isinstance(x.target, C.Name) and any(
                        isinstance(y, C.Unary) and y.op == "&" for y in walk_expr(x.value)):
                    out.add(x.target.id)
                if isinstance(x, (C.Post, C.Unary)) and x.op in ("++", "--", "&") and isinstance(x.e, C.Name):
                    out.add(x.e.id)
        for inner in _inner(st):
            visit(inner)
    for st in fn.body.body:
        visit(st)
    return out


def apply_types(tree, fn, p, made_for=None, names=None):
    """The function's pointer variables declared as pointers to the plan's structures (volatile, so
    that every access stays one), their accesses written as members (p->f_2c; with a cast where the
    access reads another type of the field's size, as raw access where the structure has no such
    field), every other use cast to u32; the structures defined above the function. Only when the
    plan was made for this C (made_for: its digest before names). names: {s_N: (name, {offset: field
    name})} from the readable_names table (dread.naming struct_names). Returns the names of the
    structures it used."""
    names = names or {}

    def cname(k):
        return names.get(k, (k, {}))[0]

    def fname(k, rel):
        return field_name(names.get(k, (k, {}))[1], rel)
    from dread.naming import _rewrite, _rewrite_stmt
    if made_for is not None and p.digests.get(fn.name) not in (None, made_for):
        return []
    typed = {v: k for v, k in p.typed.get(fn.name, {}).items() if k in p.structs}
    allowed = pointer_candidates(fn) - _uses(fn)
    typed = {v: k for v, k in typed.items() if v in allowed}
    if not typed:
        return []
    fields = {k: dict(p.structs[k]) for k in set(typed.values())}
    ptrs = {k: dict(p.pointers.get(k, {})) for k in fields}
    ptype = {k: "volatile struct %s *" % cname(k) for k in set(fields) | {t for ps in ptrs.values()
                                                                         for t in ps.values()}}

    def member(e):
        """(member expression, its type, the access's type) for an access through a typed variable,
        or the raw access through its integer value; None when e is no such access. A field that
        points to a structure has the type ("ptr", that structure)."""
        acc = _access(e)
        if acc is None or not isinstance(acc[0], C.Name) or acc[0].id not in typed:
            return None
        base, k, ctype = acc
        rows = fields[typed[base.id]]
        if rows.get(k) is not None and SIZES[rows[k]] == SIZES.get(ctype):
            target = ptrs[typed[base.id]].get(k)
            return C.Member(C.Name(base.id), fname(typed[base.id], k), True), \
                ("ptr", target) if target else rows[k], ctype
        address = C.Cast("u32", C.Name(base.id))
        if k:
            address = C.Binary("+" if k > 0 else "-", address, C.Num("%d" % abs(k)))
        return C.Unary("*", C.Cast("volatile %s *" % ctype, address)), ctype, ctype

    def value(e):
        return _rewrite(e, f)

    def pointer(e):
        """(structure, expression) of what points to a structure as it is: a typed variable, a field
        that points to one; None for anything else."""
        if isinstance(e, C.Name) and e.id in typed:
            return typed[e.id], C.Name(e.id)
        m = member(e)
        if m is not None and isinstance(m[1], tuple):
            return m[1][1], m[0]
        return None

    own = {i: typed[n] for i, (_, n) in enumerate(fn.params) if n in typed}     # (its own typed parameters)

    def f(e):
        if isinstance(e, C.Call) and isinstance(e.f, C.Name) and e.f.id == fn.name and own:
            args = []                       # (a call of itself passes pointers where its parameters are)
            for i, a in enumerate(e.args):
                if i in own:
                    args.append(C.Name(a.id) if isinstance(a, C.Name) and typed.get(a.id) == own[i]
                                else C.Cast(ptype[own[i]], value(a)))
                else:
                    args.append(value(a))
            return C.Call(C.Name(fn.name), args)
        if isinstance(e, C.Assign):
            if isinstance(e.target, C.Name) and e.target.id in typed and e.op == "=":
                k = typed[e.target.id]
                v = e.value
                if isinstance(v, C.Name) and typed.get(v.id) == k:
                    return C.Assign("=", C.Name(e.target.id), C.Name(v.id))
                mv = member(v)              # (a field that points to its structure: no cast)
                if mv is not None and mv[1] == ("ptr", k):
                    return C.Assign("=", C.Name(e.target.id), mv[0])
                return C.Assign("=", C.Name(e.target.id), C.Cast(ptype[k], value(v)))
            m = member(e.target)
            if m is not None:
                if isinstance(m[1], tuple) and e.op == "=":     # (a field that points to a structure)
                    v, t = e.value, m[1][1]
                    if (isinstance(v, C.Name) and typed.get(v.id) == t) or (isinstance(v, C.Num) and v.value == 0):
                        return C.Assign("=", m[0], C.Name(v.id) if isinstance(v, C.Name) else v)
                    return C.Assign("=", m[0], C.Cast(ptype[t], value(v)))
                return C.Assign(e.op, m[0], value(e.value))
            return None
        m = member(e)
        if m is not None:
            expr, ftype, ctype = m
            return expr if ftype == ctype else C.Cast(ctype, expr)
        if isinstance(e, C.Binary) and e.op in ("==", "!="):
            pa, pb = pointer(e.l), pointer(e.r)
            if pa is not None and (pb is not None and pb[0] == pa[0] or isinstance(e.r, C.Num) and e.r.value == 0):
                return C.Binary(e.op, pa[1], pb[1] if pb is not None else e.r)
            return None
        if isinstance(e, C.Name) and e.id in typed:
            return C.Cast("u32", C.Name(e.id))
        return None
    for st in fn.body.body:
        _rewrite_stmt(st, f)
    # the declarations: parameters' types, locals out of their u32 lines into pointer lines
    params = [(ptype[typed[n]] if n in typed else t, n) for t, n in fn.params]
    if params != fn.params:
        m = re.match(r"^(\w+) (\w+)\((.*)\)$", fn.head.strip())
        if m:
            fn.params = params
            fn.head = "%s %s(%s)" % (m.group(1), m.group(2), ", ".join(
                "%s%s" % (t, n) if t.endswith("*") else "%s %s" % (t, n) for t, n in params) or "void")
            for item in tree.items:         # (a function that calls itself declares itself: the same types)
                if isinstance(item, C.Raw) and re.match(r"^extern \w+ %s\(" % re.escape(fn.name), item.text):
                    item.text = "extern %s %s(%s);" % (m.group(1), fn.name, ", ".join(t for t, _ in params) or "void")
    moved = {}
    for st in fn.body.body:
        if isinstance(st, C.Decl):
            keep = []
            for d in st.items:
                if d.name in typed:
                    moved.setdefault(typed[d.name], []).append(d.name)
                else:
                    keep.append(d)
            st.items[:] = keep
    fn.body.body[:] = [st for st in fn.body.body if not (isinstance(st, C.Decl) and not st.items)]
    for k in sorted(moved, reverse=True):
        fn.body.body.insert(0, C.Decl("volatile struct %s" % cname(k), [C.Declarator(n, ptr="*")
                                                                          for n in sorted(moved[k])]))
    at = next((i + 1 for i, item in enumerate(tree.items)       # (after the #include: before the file's
               if isinstance(item, C.Raw) and item.text.startswith("#include")), tree.items.index(fn))    # declarations)
    present = {item.text for item in tree.items if isinstance(item, C.Raw)}     # (a file whose other function
    for k in sorted(fields, reverse=True):                                      # has them already: C17 forbids
        text = struct_text(cname(k), p.structs[k], names.get(k, (k, {}))[1],    # a second definition)
                           {off: cname(t) for off, t in ptrs[k].items()})
        if text not in present:
            tree.items.insert(at, C.Raw(text))
    for t in sorted({t for ps in ptrs.values() for t in ps.values()}, reverse=True):
        if "struct %s;" % cname(t) not in present:      # (a field may point to a structure defined after it)
            tree.items.insert(at, C.Raw("struct %s;" % cname(t)))
    from dread.build import NULL_CHECKS     # (a pointer it dereferences may be 0 and still be compared with 0:
    tree.items.insert(0, C.Raw(NULL_CHECKS))    # dread.build compiles it so)
    return sorted(fields)
