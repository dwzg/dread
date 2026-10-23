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

What is one value follows the code (Collector): a variable is as many values as it has webs of
definitions, and only a use whose value the analysis may take joins the definitions that reach it.
An argument for a parameter the callee does nothing with, the result of a function no caller reads,
and an asm's register operands join nothing (clang -O2's shared tail return vPortExitCritical(r0)
made a task block's priority one value with its list container, RISC-V's ecall, whose asm reads a0
to a7, a list's count one with its container: both pointer fields that were not). A register a
branch has just found 0 holds no pointer there (known_zero: GCC stores NULL with it, and FreeRTOS's
mutex holder had become one value with the pcHead it tested), and its earlier values reach no use
past that branch (reaching_past_zero: a list's count returned where it is 0, from the return that
hands back a found task block elsewhere). A parameter the C reads through its
address (the lifters' stack arguments, *(volatile u32 *)(u32)&s2) is the parameter. Parameters that
end as one value, an either-or of the arguments stored in one place (FreeRTOS's pcHead: the queue
itself for a semaphore, else the storage given beside it), pass on only the templates of those the
function uses as a pointer itself (dereferences, or hands to a callee that takes one), and the value
points to no one structure (interior): the storage had taken the queue's fields.

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

For readable C (plan, apply_types) the objects are sorted into kinds: kinds() joins the memory a
call hands at a structure's base to what the callee takes, and records where one kind's memory holds
another's at an offset (embeds: a call handed outer + 4 to a function that takes an item, or v = w +
4 met after v had an object of its own; FreeRTOS's task block holds two list items at 4 and 24, a
queue two lists at 16 and 36, an event group one at 4). The plan declares such a held structure a
member of its holder (struct s_7 f_4;) where C can hold it: at an offset of its alignment, ending
where the holder's fields reach, not overlapping another member, no field across its ends, and what
the holder accesses inside it fitting the held structure's fields (embed). The holder's accesses
there are written through the member (p->f_4.f_8), its address as &p->f_4 (a variable that holds
only such addresses a pointer to the held structure: r5 = &r4->f_4), and every access stays the one
volatile access it was. A place two kinds claim that could not be made one, or a held structure the
plan does not declare, leaves the holder's fields flat. Measured on four FreeRTOS builds against the
source (score_plan; docs/todo.md, Types): of the members declared in typed parameters' structures,
every one lies where the source's structure holds a structure. A template carries what the memory it
describes holds (the calls that handed it at an offset, hold), so a task block reached through an
item's owner in a callee that hands its event item to uxListRemove holds an item in the caller too.

Kinds are also joined through what pointer fields hold (kinds(): pointer_slots, same_slots). The
word fields of the objects, read through the structures their kinds hold (a task block's field at
0x14 is its item's container at 0x10), are slots, and what one slot points to at structures' bases
is one kind: the list an item's container points to is the list kind wherever it was reached (a list
reached through an item's container had been a kind of its own, and a task block reached through an
item's owner held nothing). Memory with fields in front of its base lies inside a larger structure
and says nothing (a task block reached at its event item, 0x18 into it). A slot holds
several kinds, and joins none of them, where the evidence says so (several): their layouts do not
fit together; it points back to the structure that holds it in a structure two kinds hold (FreeRTOS's
pvOwner: the task block or the timer that holds the item, a void *; on GCC -Os the code that stores a
timer there does not lift, and the two holders are the evidence); or two of them are kinds one
function takes as different parameters (a list and the item vListInsert takes are five words each).
In such a slot what it holds joins the one kind it may hold, a kind a parameter makes there or a
structure that holds it, whose layout holds all of its fields, where exactly one does (an owner a
function uses as a task block: a word at 44 is a task block's, a timer has a byte there), and the plan
declares the field no pointer (void_slots, also at the holders' offsets of a held structure's slot).
Reading slots through puns too (a list's end marker at 8, walked as an item) was measured and left:
it joined lists with items, and with them kept apart found fewer pointer fields (docs/decisions.md).
Two kinds one function takes as different parameters are joined neither through a slot nor as what
one place holds. How many of ours each source structure is: score_kinds (dread readable --score for
the plan's structures, with --shapes for the kinds the parameters point to).
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
        self.walked = set()             # object roots a template of a walked object was copied onto (walked())
        self.count = 0
        self.globals = {}               # address -> object of the memory there
        self.static_spans = []          # sorted [(start, end)] of static data's objects (dread.statics): no offsets
        self.pending = []               # (kind, a, b, k): relations that wait for a pointer
        self.kind_parent = {}           # object -> (object, shift): what the memory is, as obj_parent
        self.embeds = set()             # (kind, offset, kind, offset): one kind's memory inside another's
        self.kind_embeds = {}           # (kind, offset from its base) -> the kind held there (kinds())
        self.offsets = []               # (location, location): one memory, met late (settle's v = w + k)
        self.calls = []                 # (the argument's location, the callee's parameter's): kinds (kinds())
        self.held = {}                  # object root -> [(offset, the callee's parameter's location)] (hold)
        self.frames = set()             # objects that are a function's stack frame (reached from sp0)
        self.var_webs = {}              # (function, variable) -> its values' keys, one per web
        self.shifted = False            # whether objects that meet at different offsets are merged
        self.share = False              # whether templates refer to memory reachable from fixed addresses
        self.embedded = set()           # (root, offset, root, offset) of those kept apart
        self.interior = set()           # roots of values that met their own object at another offset
        self.param_used = {}            # (function, parameter index) -> whether it uses it as a pointer
        self.void_slots = set()         # (kind, offset) of pointer slots that hold several kinds (kinds())

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

    def static_offset(self, k):
        """Whether an offset added to a value is an address inside an object of static data (the plan's
        structures and arrays, dread.statics): r3 + 0x200299fcu is an index into an array of lists at
        0x200299f8, not a field of what r3 points to (it made r3 a pointer to a structure of 512 MB)."""
        import bisect
        k &= 0xffffffff
        i = bisect.bisect_right(self.static_spans, (k, 0xffffffff)) - 1
        return i >= 0 and self.static_spans[i][0] <= k < self.static_spans[i][1]

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
                        # (one object at two offsets: a pointer into it, such as a queue's pcHead, the
                        # queue itself for a semaphore and its storage behind it for the others)
                        self.interior.update((ra, rb))
                    continue
            self.tv_parent[rb] = ra
            if rb in self.interior:
                self.interior.add(ra)
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
        for off, inner in self.held.pop(rb, ()):
            self.held.setdefault(ra, []).append((off + shift, inner))
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
        if rb in self.walked:
            self.walked.add(ra)
        return out

    def walked_template(self, tmpl):
        """Whether a template describes memory that a walk reached: its object met itself at two
        offsets (conflicts: memcpy's buffers) or such a template was copied onto it (instantiate), so
        that a function that hands its parameter on to memcpy describes a buffer too. Nothing in the
        shapes reads it; dread.statics handed keeps such parameters out (a string handed through a
        stream buffer's functions to an unrolled memcpy had made a structure of words 0 to 60)."""
        if tmpl is None or tmpl[0] != "copy":
            return False
        r = self.loc(tmpl[2])[0]
        return r in self.conflicts or r in self.walked

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
        child, interior)} relative to location, (root, offset) of location, ((offset, the location of
        a callee's parameter whose template's root a call handed the memory there), ...): what it
        holds), ("shared", root, offset) for memory reachable from fixed addresses, ("back", k,
        delta) for a pointer to the k-th structure up the path."""
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
            out[off - off0] = (frozenset(f.types), child, t in self.interior)
        held = tuple(sorted({(off - off0, inner) for off, inner in self.held.get(root, ()) if off > off0}))
        return ("copy", out, (root, off0), held)

    def hold(self, location, inner):
        """Records that a call handed the memory at location for the parameter whose template's root
        is inner (calls, and held: what an object holds at an offset, which its templates carry)."""
        self.calls.append((location, inner))
        root, off = self.loc(location)
        self.held.setdefault(root, []).append((off, inner))

    def instantiate(self, tmpl, location, stack=()):
        """Copy a template onto the memory at location (an object of the caller), with what it holds
        at its offsets (a task block reached through an item's owner in a callee that hands its
        event item to uxListRemove holds an item at 0x18 in the caller too)."""
        if tmpl is None:
            return
        if tmpl[0] == "shared":
            self.unify_locations(location, (tmpl[1], tmpl[2]))
        elif tmpl[0] == "back":
            if tmpl[1] < len(stack):
                o, off = stack[tmpl[1]]
                self.unify_locations(location, (o, off + tmpl[2]))
        else:
            if self.walked_template(tmpl):
                self.walked.add(self.loc(location)[0])
            stack = stack + (location,)
            for rel, (types, child, interior) in tmpl[1].items():
                f = self.field((location[0], location[1] + rel))
                f.types |= types
                if child is not None:
                    self.instantiate(child, self.pointer(f.content), stack)
                if interior:
                    self.interior.add(self.find(f.content))
            for rel, inner in tmpl[3]:
                self.hold((location[0], location[1] + rel), inner)



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


def reaches_below(tmpl):
    """Whether a template has fields below the location it describes: the callee takes the pointer as
    one into something larger, a header in front (FreeRTOS's vPortFree reads the heap block's size at
    -4 of what it frees, pvPortMalloc hands out its block plus 8). What it sees there is its own
    structure, not the caller's: copied, every structure allocated or freed through it took the heap
    block's links, and linked, the heap block's kind."""
    return tmpl is not None and tmpl[0] == "copy" and any(rel < 0 for rel in tmpl[1])


class Collector:
    """Walks one function's readable tree and feeds the unification. key(v): the type variable of a
    variable of the function. summaries: {callee: {"params": {i: template}, "ret": template}} of the
    callees already done (their templates are copied); together: the functions of this one's
    component (their parameters are unified, as one function's)."""

    def __init__(self, shapes, fn, macros, summaries=None, together=(), unread=()):
        self.s, self.fn, self.macros = shapes, fn, macros   # macros: {name: (address, type)}
        self.summaries, self.together = summaries or {}, set(together)
        self.unread = unread            # functions whose result no caller reads (analyse)
        self.opaque = register_variables(fn)    # (an asm's register operands: what they hold is unknown)
        self.args = None                # the object of the parameters whose address the C takes (run)
        self.arg_offset = {}            # such a parameter -> its offset in it
        self.derefs = set()             # values used as an address here (or handed to a callee for one)
        self.copies = []                # (value, the variable's value it is copied into): v = w, v = w + k
        self.zero_at = {}               # node -> the variables known to hold 0 there (webs: known_zero)

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
        statements' nodes (a statement inside one without a node of its own takes its parent's).
        Only a use whose value the analysis takes joins the definitions that reach it (taken): a
        register several values pass through on their way to a use that ignores them stays as many
        values (clang -O2's shared tail return vPortExitCritical(r0), reached with a task's list
        container in r0 on one path and its priority on another, made the priority a pointer to the
        list; RISC-V's ecall, whose asm reads a0 to a7, made a list's count one with its container)."""
        from dread.cflow import Graph
        from dread.tidy import flat_view
        self.node, self.node_of = None, {}
        self.def_web, self.use_web, self.entry_web = {}, {}, {}
        try:
            view = flat_view(self.fn)
            g = Graph(view)
            reach = reaching_past_zero(g)
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
            used = self.taken(n.stmt)
            for v in n.reads:
                defs = sorted(reach[n.index].get(v) or ())
                if used is None or v in used:
                    for d in defs[1:]:
                        a, b = find((v, defs[0])), find((v, d))
                        if a != b:
                            parent[b] = a
                # (a statement with others in it, a loop or a block, that reads and writes v: its node
                # cannot say which read sees its own write, so they are one web; a simple one reads first;
                # an asm's own registers are values of their own)
                if defs and v in n.writes and not simple(n.stmt) and (used is None or v in used):
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
        self.zero_at = known_zero(g)

    def taken(self, st):
        """The variables statement st reads (its own expressions and those of the statements in it)
        where the analysis may take their value: every read but an argument for a parameter the
        callee does nothing with (its summary has no template there), what a function returns that
        no caller reads, and an asm's registers (an asm, or a block of register variables around
        one, reads none: its registers are values of their own, also where the structure stage put
        it inside an if, RISC-V's ecall); None when st holds a statement it does not know (every
        read counts)."""
        out = set()
        return out if self._taken_stmt(st, out) else None

    def _taken_stmt(self, st, out):
        from dread.naming import _inner
        if isinstance(st, C.Asm) or (isinstance(st, C.Block) and any(
                isinstance(x, C.Decl) and any(d.asm for d in x.items) for x in st.body)):
            return True
        if isinstance(st, C.ExprStmt):
            self._taken(st.e, True, out)
        elif isinstance(st, C.Return):
            if st.e is not None:
                self._taken(st.e, self.fn.name not in self.unread, out)
        elif isinstance(st, (C.If, C.While, C.DoWhile)):
            self._taken(st.cond, True, out)
        elif isinstance(st, C.Switch):
            self._taken(st.e, True, out)
        elif isinstance(st, C.For):
            for x in (st.init if not isinstance(st.init, C.Decl) else None, st.cond, st.step):
                if x is not None:
                    self._taken(x, True, out)
        elif isinstance(st, C.Decl):
            for d in st.items:
                if d.init is not None:
                    self._taken(d.init, d.name not in self.opaque, out)
                for x in d.dims:
                    if x is not None:
                        self._taken(x, True, out)
        elif not isinstance(st, (C.Block, C.Goto, C.Label, C.Case, C.Break, C.Continue)):
            return False
        return all(self._taken_stmt(inner, out) for inner in _inner(st))

    def _taken(self, e, used, out):
        """Adds to out the variables e reads whose value the analysis may take; used: False where e's
        value goes nowhere (an argument the callee ignores, an unread result): the reads that only
        pass it on (casts, an offset added, a conditional's arms) do not count; those that compute
        something else from it (an address, an operand) do."""
        if isinstance(e, C.Name):
            if used and e.id not in self.macros and e.id not in self.opaque:
                out.add(e.id)
            return
        if isinstance(e, C.Cast):
            self._taken(e.e, used, out)
        elif isinstance(e, C.Binary) and e.op in ("+", "-") and isinstance(e.r, C.Num):
            self._taken(e.l, used, out)
        elif isinstance(e, C.Cond):
            self._taken(e.c, True, out)
            self._taken(e.t, used, out)
            self._taken(e.f, used, out)
        elif isinstance(e, C.Comma):
            for i, x in enumerate(e.items):
                self._taken(x, used or i < len(e.items) - 1, out)
        elif isinstance(e, C.Call):
            name = e.f.id if isinstance(e.f, C.Name) else None
            if name is None:
                self._taken(e.f, True, out)
            for i, a in enumerate(e.args):
                tmpl = self.summaries[name]["params"].get(i) if name in self.summaries else None
                ignored = name in self.summaries and (tmpl is None or reaches_below(tmpl))
                self._taken(a, not ignored, out)
        else:
            from dread.cflow import walk_expr      # (anything else: every read counts)
            out.update(x.id for x in walk_expr(e) if isinstance(x, C.Name))

    def run(self):
        self.webs()
        for i, (_, pname) in enumerate(self.fn.params):
            self.s.unify(self.key(pname, entry=True), ("param", self.fn.name, i))
        # A parameter the C reads through its address (the lifters' stack arguments: r4 = *(volatile
        # u32 *)(u32)&s2, or a_1 = (u32)&s0 and a_1 + 4 for the next one) is a word of an argument
        # area at 4 bytes a parameter: what is read there is the parameter (FreeRTOS's
        # prvInitialiseNewTask takes its task block on the stack on ARM, and its template was empty).
        addressed = address_taken(self.fn)
        if addressed:
            self.args = self.s.new_object()
            self.s.frames.add(self.args)
            for i, (_, pname) in enumerate(self.fn.params):
                if pname in addressed:
                    self.arg_offset[pname] = 4 * i
                    self.s.unify(self.s.field((self.args, 4 * i)).content, ("param", self.fn.name, i))
        for st in self.fn.body.body:
            self.stmt(st)
        # Which parameters the function uses as a pointer itself: their value, or one it is copied
        # into, is an address here or goes to a callee that takes a pointer (analyse: a parameter
        # that is one value with another only by being stored or chosen with it passes nothing on).
        used, changed = set(self.derefs), True
        while changed:
            changed = False
            for src, dst in self.copies:
                if dst in used and src not in used:
                    used.add(src)
                    changed = True
        self.node = None
        for i, (_, pname) in enumerate(self.fn.params):
            self.s.param_used[(self.fn.name, i)] = self.key(pname, entry=True) in used

    def source(self, e):
        """The value of the variable e is (through casts to an integer and offsets added), or of the
        parameter it reads from the argument area (*(volatile u32 *)(u32)&s0); None for another."""
        while isinstance(e, C.Cast) or (isinstance(e, C.Binary) and e.op in ("+", "-") and isinstance(e.r, C.Num)):
            e = e.e if isinstance(e, C.Cast) else e.l
        if isinstance(e, C.Name) and e.id not in self.macros and e.id not in self.opaque:
            return self.key(e.id)
        acc = _access(e)
        if acc is not None and acc[1] == 0:
            x = acc[0].e if isinstance(acc[0], C.Cast) else acc[0]
            if isinstance(x, C.Unary) and x.op == "&" and isinstance(x.e, C.Name) and x.e.id in self.arg_offset:
                return self.key(x.e.id, entry=True)
        return None

    def copied(self, e, target):
        """Records that e's value, a variable's or a parameter's (source), is copied into target's
        (run follows a use as a pointer back through it)."""
        src = self.source(e)
        if src is not None:
            self.copies.append((src, target))

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
                if v is not None and self.fn.name not in self.unread:
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
                    if v is not None and d.name not in self.opaque:
                        self.s.unify(self.key(d.name, write=True), v)
                        self.copied(d.init, self.key(d.name, write=True))
        from dread.naming import _inner
        for inner in _inner(st):
            self.stmt(inner)

    def address(self, e):
        """The location an address expression points to, or None (an integer we know nothing of; a
        small constant: NULL is no memory at address 0, and FreeRTOS hands NULL for the current task
        to a dozen functions, which made every structure given NULL one kind)."""
        if isinstance(e, C.Num) and isinstance(e.value, int):
            return (self.s.global_object(e.value), 0) if e.value >= SMALLEST_ADDRESS else None
        x = e.e if isinstance(e, C.Cast) and " ".join(e.type.split()) in ("u32", "s32") else e
        if isinstance(x, C.Unary) and x.op == "&" and isinstance(x.e, C.Name) and x.e.id in self.arg_offset:
            return (self.args, self.arg_offset[x.e.id])     # (a parameter's word in the argument area)
        if isinstance(e, C.Binary) and e.op in ("+", "-") and isinstance(e.r, C.Num) and isinstance(e.r.value, int):
            if e.op == "+" and self.s.static_offset(e.r.value):
                self.value(e.l)             # (an index into static data: the value is no pointer)
                return None
            base = self.address(e.l)
            if base is None:
                return None
            k = signed32(e.r.value if e.op == "+" else -e.r.value)
            return (base[0], base[1] + k)
        src = self.source(e)
        if src is not None:
            self.derefs.add(src)
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
            if e.id in self.opaque or e.id in self.zero_at.get(self.node, ()):
                return None                 # (a register a branch has just found 0 stores NULL)
            return self.key(e.id)
        if isinstance(e, C.Num):
            return None
        acc = _access(e)
        if acc is not None:
            base, k, ctype = acc
            if k and s.static_offset(k):
                self.value(base)            # (an index into static data: the value is no pointer)
                return None
            location = self.address(base)
            if location is None:
                return None
            return s.field((location[0], location[1] + k), ctype).content
        if isinstance(e, C.Assign):
            v = self.value(e.value)
            if isinstance(e.target, C.Name) and e.target.id not in self.macros:
                t = self.key(e.target.id, write=True)
                if e.op == "=":
                    self.copied(e.value, t)
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
                    if tmpl is None or reaches_below(tmpl):
                        self.value(a)
                        continue
                    location = self.address(a)
                    if location is not None:
                        s.instantiate(tmpl, location)
                        if structure(tmpl):
                            s.hold(location, tmpl[2])
                t = ("result", self.fn.name, id(e))
                if summary.get("ret") is not None and not reaches_below(summary["ret"]):
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
                    src = self.source(a)                    # (its own component: taken for a pointer)
                    if src is not None:
                        self.derefs.add(src)
                elif isinstance(a, C.Num) and isinstance(a.value, int) and a.value >= 0x1000:
                    s.pending.append(("const-arg", ("param", name, i), a.value, 0))
            return ("ret", name) if name in self.together else ("result", self.fn.name, id(e))
        if isinstance(e, C.Binary):
            if e.op in ("+", "-") and isinstance(e.r, C.Num) and isinstance(e.r.value, int):
                base = self.value(e.l)
                if e.op == "+" and s.static_offset(e.r.value):
                    return None             # (an index into static data plus its address: no pointer)
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


def _statements(fn):
    from dread.naming import _inner
    work = list(fn.body.body)
    while work:
        st = work.pop()
        yield st
        work += _inner(st)


def zero_tests(cond):
    """(the variables cond being true says are 0, those its being false says are 0): v == 0, !v,
    v != 0, v, through casts and && (true) or || (false)."""
    def name(e):
        while isinstance(e, C.Cast):
            e = e.e
        return e.id if isinstance(e, C.Name) else None

    def zero(e):
        return isinstance(e, C.Num) and e.value == 0
    if isinstance(cond, C.Cast):
        return zero_tests(cond.e)
    if isinstance(cond, C.Binary) and cond.op in ("==", "!="):
        v = name(cond.l) if zero(cond.r) else name(cond.r) if zero(cond.l) else None
        if v is None:
            return set(), set()
        return ({v}, set()) if cond.op == "==" else (set(), {v})
    if isinstance(cond, C.Unary) and cond.op == "!":
        t, f = zero_tests(cond.e)
        return f, t
    if isinstance(cond, C.Binary) and cond.op == "&&":
        return zero_tests(cond.l)[0] | zero_tests(cond.r)[0], set()
    if isinstance(cond, C.Binary) and cond.op == "||":
        return set(), zero_tests(cond.l)[1] | zero_tests(cond.r)[1]
    v = name(cond)
    return (set(), {v}) if v is not None else (set(), set())


def known_zero(g):
    """{node index: the variables known to hold 0 when it runs} of a dread.cflow Graph, for the
    nodes of simple statements (they read before they write): a conditional jump or return on v == 0
    or v != 0 says so on the edge it takes (if (r7 != 0) goto L; ... *(r0 + 8) = r7: GCC stores NULL
    with the register it has just found 0, which made FreeRTOS's mutex holder one value with pcHead),
    a write forgets it, and where paths meet what all of them know holds."""
    from dread.tidy import simple
    count = len(g.nodes)
    known = {0: set()} if count else {}
    work = [0] if count else []
    while work:
        i = work.pop()
        n = g.nodes[i]
        after = known[i] - n.writes
        tests = None
        if isinstance(n.stmt, C.If) and n.stmt.orelse is None and isinstance(n.stmt.then, (C.Goto, C.Return)):
            tests = zero_tests(n.stmt.cond)
            jump = g.labels.get(n.stmt.then.label) if isinstance(n.stmt.then, C.Goto) else None
        for t in n.succ:
            if t >= count:
                continue
            fact = after
            if tests is not None and jump != i + 1:
                fact = after | (tests[0] if t == jump else tests[1] if t == i + 1 else set())
            new = fact if t not in known else known[t] & fact
            if t not in known or new != known[t]:
                known[t] = new
                work.append(t)
    return {i: known[i] for i in known if known[i] and simple(g.nodes[i].stmt)}


def reaching_past_zero(g):
    """As dread.cflow Graph.reaching, but a definition does not reach along a conditional jump's
    edge that says its variable is 0 there (if (v != 0) goto L: the fall-through; if (v == 0) goto
    L: the jump): the register holds 0 past it, no value, and joins none it meets later (FreeRTOS's
    prvSearchForNameWithinSingleList returns a list's count where it is 0 and a found task block
    elsewhere from one return: the count had become a pointer to a task block)."""
    from dread.cflow import Reaching
    nodes = g.nodes
    defs = [(-1, v) for v in sorted(g.scope)]
    for n in nodes:
        defs += [(n.index, v) for v in sorted(n.writes)]
    by_var = {}
    for k, (_, v) in enumerate(defs):
        by_var[v] = by_var.get(v, 0) | (1 << k)
    entry = sum(1 << k for k, (i, _) in enumerate(defs) if i == -1)
    gen, kill = [0] * len(nodes), [0] * len(nodes)
    for k, (i, v) in enumerate(defs):
        if i >= 0:
            gen[i] |= 1 << k
    for n in nodes:
        for v in n.kills:
            kill[n.index] |= by_var.get(v, 0) & ~gen[n.index]
    cut = {}                                # (node, successor) -> the definitions an edge stops
    for n in nodes:
        st = n.stmt
        if isinstance(st, C.If) and st.orelse is None and isinstance(st.then, (C.Goto, C.Return)):
            true, false = zero_tests(st.cond)
            jump = g.labels.get(st.then.label) if isinstance(st.then, C.Goto) else None
            if jump == n.index + 1:
                continue
            if jump is not None and true:
                cut[(n.index, jump)] = sum(by_var.get(v, 0) for v in true)
            if false:
                cut[(n.index, n.index + 1)] = sum(by_var.get(v, 0) for v in false)
    ins, outs = [0] * len(nodes), [None] * len(nodes)
    changed = True
    while changed:
        changed = False
        for n in nodes:
            inn = entry if n.index == 0 else 0
            for p in n.pred:
                if outs[p] is not None:
                    inn |= outs[p] & ~cut.get((p, n.index), 0)
            out = gen[n.index] | (inn & ~kill[n.index])
            if inn != ins[n.index] or out != outs[n.index]:
                ins[n.index], outs[n.index] = inn, out
                changed = True
    return [Reaching(bits, defs, by_var) for bits in ins]


def register_variables(fn):
    """The names of the function's register variables (register u32 x0 __asm__("a0") = a0: an asm's
    operands, as the lifters write RISC-V's ecall)."""
    return {d.name for st in _statements(fn) if isinstance(st, C.Decl) for d in st.items if d.asm}


def address_taken(fn):
    """The function's parameters whose address its C takes (&s0: the lifters' stack arguments)."""
    from dread.cflow import walk_expr
    from dread.naming import _exprs
    params = {p for _, p in fn.params}
    out = set()
    for st in _statements(fn):
        for e in _exprs(st):
            for x in walk_expr(e):
                if isinstance(x, C.Unary) and x.op == "&" and isinstance(x.e, C.Name) and x.e.id in params:
                    out.add(x.e.id)
    return out


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
                        # (v points somewhere already, a call gave it a fresh object: r5 = r0 + 4 met
                        # after uxListRemove(r5); objects at other offsets stay apart, and kinds() reads
                        # the pair as one inside the other)
                        s.offsets.append((s.points[pa], (o, off + k)))
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


def analyse(trees, depth=4, share=False, unread=frozenset(), static_spans=()):
    """Shapes from [(tree, fn, macros)]: every function's readable tree with its macros (name ->
    (address, type)), callees first; returns the Shapes with .summaries. static_spans: [(start, end)]
    of the objects of static data (dread.statics), an address inside one added to a value no offset
    from a pointer (Shapes.static_offset). unread: the functions whose
    result no caller reads (dread.regflow's kept registers lack the result register): their returns
    stay values of their own instead of one result. Such a function's lifted C hands back whatever its
    result register holds on each path (FreeRTOS's vPortFree, void in the source: its parameter on one
    path, the freed block's size on another), and made one, the size pointed to the parameter's memory:
    every structure freed through it got a pointer to itself at the block header's offsets."""
    s = Shapes()
    s.share = share
    s.static_spans = sorted(static_spans)
    by_name = {fn.name: (tree, fn, macros) for tree, fn, macros in trees}
    graph = {name: callees(fn) & set(by_name) for name, (_, fn, _) in by_name.items()}
    summaries = {}
    for comp in components(graph):
        for name in comp:
            tree, fn, macros = by_name[name]
            Collector(s, fn, macros, summaries, comp, unread).run()
        settle(s)
        shared = s.global_roots()
        for name in comp:
            fn = by_name[name][1]
            summary = {"params": {}, "ret": None}
            # Parameters that end as one value: an either-or of the arguments stored in one place
            # (FreeRTOS's pcHead: the queue itself for a semaphore, else the storage given beside it).
            # Only those the function uses as a pointer pass the template on (the storage got the
            # queue's fields, and at GCC -O0 the dynamic queue's storage behind it the queue's again),
            # and the value points to no one structure (interior: no pointer field).
            groups, passed = {}, set(range(len(fn.params)))
            for i in range(len(fn.params)):
                groups.setdefault(s.find(("param", name, i)), []).append(i)
            for r, group in groups.items():
                used = [i for i in group if s.param_used.get((name, i))]
                if len(group) > 1 and used and len(used) < len(group):
                    s.interior.add(r)
                    passed -= set(group) - set(used)
            for i in sorted(passed):
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


def source_params(elf_path, pointers=None, held=None):
    """{function entry: {parameter index: ([the type's names, its typedefs first and the structure's
    tag last], {(offset, size): [the member names on the way to that leaf]})}} of the parameters that
    are pointers to a structure, from the ELF's debug information (pyelftools). pointers, a dict:
    gets {function entry: {parameter index: {offsets of the leaves that point to a structure}}};
    held, a dict: {function entry: {parameter index: {offset: size}}} of the structures (and unions)
    the parameter's structure holds as members, at any depth."""
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

    def leaves(die, base, into, depth=0, path=(), ptrs=None, inner=None):
        die = strip(die)
        if die is None or depth > 6:
            return
        if die.tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
            if depth and inner is not None:
                inner.setdefault(base, size_of(die))
            for m in die.iter_children():
                if m.tag != "DW_TAG_member" or "DW_AT_type" not in m.attributes:
                    continue
                loc = m.attributes.get("DW_AT_data_member_location")
                off = loc.value if loc is not None and isinstance(loc.value, int) else 0
                mname = m.attributes.get("DW_AT_name")
                leaves(m.get_DIE_from_attribute("DW_AT_type"), base + off, into, depth + 1,
                       path + ((mname.value.decode(errors="replace"),) if mname else ()), ptrs, inner)
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
                    leaves(elem, base + k * esize, into, depth + 1, path, ptrs, inner)
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
                            into, ptrs, inner = {}, set(), {}
                            leaves(target, 0, into, ptrs=ptrs, inner=inner)
                            if pointers is not None:
                                pointers.setdefault(entry, {})[k] = ptrs
                            if held is not None:
                                held.setdefault(entry, {})[k] = inner
                            name = target.attributes.get("DW_AT_name")
                            out.setdefault(entry, {})[k] = (handle + names + [name.value.decode() if name else "?"],
                                                            into)
                    k += 1
    return out


def _typed_params(program, p, src):
    """[(function, parameter index, our structure)] of the typed parameters whose source type is a
    pointer to a structure (src: source_params'), each pair of our structure and the source's once."""
    regs = list(program.target.abi.arg_regs)
    seen, out = set(), []
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
            out.append((fn, i, k))
    return out


def score_pointers(program, p, elf_path):
    """The plan's pointer fields against the source (debug information), for the structures of
    typed parameters whose source type points to a structure, each pair of our structure and the
    source's once: {"fields": pointer fields, "right": of them pointers to a structure in the
    source, "missed": the source's pointers to a structure at word fields the plan left integers}.
    A structure's fields include those of the structures it holds, at their offsets in it."""
    ptrs = {}
    src = source_params(elf_path, pointers=ptrs)
    out = {"fields": 0, "right": 0, "missed": 0}
    for fn, i, k in _typed_params(program, p, src):
        mine, theirs = p.leaf_pointers(k), ptrs.get(fn.entry, {}).get(i, set())
        out["fields"] += len(mine)
        out["right"] += sum(1 for off in mine if off in theirs)
        out["missed"] += sum(1 for off, t in p.leaves(k) if t == "u32" and off in theirs and off not in mine)
    return out


def held_offsets(p, name):
    """The offsets in a structure of the structures it holds, theirs included."""
    return [d for d, _ in held_structures(p, name)]


def score_plan(program, p, elf_path):
    """The plan's structures against the source (debug information), for the structures of typed
    parameters whose source type is a pointer to a structure, each pair of our structure and the
    source's once: {"structures": such pairs, "fields": the fields declared (those of the structures
    a structure holds at their offsets in it), "right": of them a leaf of the source's structure at
    that offset with that size, "held": the structures declared members of another, "held_right": of
    them at an offset where the source's structure holds a structure or a union}."""
    inner = {}
    src = source_params(elf_path, held=inner)
    out = dict.fromkeys(("structures", "fields", "right", "held", "held_right"), 0)
    for fn, i, k in _typed_params(program, p, src):
        leaves = set(src[fn.entry][i][1])
        mine = {(off, SIZES[t]) for off, t in p.leaves(k)}
        theirs = inner.get(fn.entry, {}).get(i, {})
        held = held_offsets(p, k)
        out["structures"] += 1
        out["fields"] += len(mine)
        out["right"] += len(mine & leaves)
        out["held"] += len(held)
        out["held_right"] += sum(1 for d in held if d in theirs)
    return out


def source_structures(elf_path):
    """({structure: {"size": bytes, "pointers": {offset: the structure a leaf there points to},
    "held": {offset: a structure held there}}}, {function entry: {parameter index: structure}}) from
    the ELF's debug information: the structures parameters point to and those they reach through
    pointers and members, by name (the tag, else the typedef, else ?@ the DIE's offset)."""
    from elftools.elf.elffile import ELFFile
    out, params, complete = {}, {}, {}

    def strip(die):
        while die is not None and die.tag in ("DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type"):
            if "DW_AT_type" not in die.attributes:
                return None
            die = die.get_DIE_from_attribute("DW_AT_type")
        if die is not None and "DW_AT_declaration" in die.attributes and "DW_AT_name" in die.attributes:
            die = complete.get((die.tag, die.attributes["DW_AT_name"].value), die)
        return die

    def describe(die, depth=0):
        die = strip(die)
        if die is None or die.tag not in ("DW_TAG_structure_type", "DW_TAG_union_type"):
            return None
        n = die.attributes.get("DW_AT_name")
        name = n.value.decode(errors="replace") if n else "?@%x" % die.offset
        if name in out or depth > 8:
            return name
        entry = out[name] = {"size": die.attributes["DW_AT_byte_size"].value
                             if "DW_AT_byte_size" in die.attributes else None, "pointers": {}, "held": {}}

        def walk(d, base, top):
            d = strip(d)
            if d is None:
                return
            if d.tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
                if not top:
                    entry["held"].setdefault(base, describe(d, depth + 1))
                for m in d.iter_children():
                    if m.tag == "DW_TAG_member" and "DW_AT_type" in m.attributes:
                        loc = m.attributes.get("DW_AT_data_member_location")
                        walk(m.get_DIE_from_attribute("DW_AT_type"),
                             base + (loc.value if loc is not None and isinstance(loc.value, int) else 0), False)
            elif d.tag == "DW_TAG_pointer_type" and "DW_AT_type" in d.attributes:
                t = describe(d.get_DIE_from_attribute("DW_AT_type"), depth + 1)
                if t is not None:
                    entry["pointers"][base] = t
        walk(die, 0, True)
        return name

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
                        name = describe(t.get_DIE_from_attribute("DW_AT_type"))
                        if name is not None:
                            params.setdefault(entry, {})[k] = name
                    k += 1
    return out, params


def held_structures(p, name, depth=0):
    """[(offset, structure)] of the structures a structure holds, theirs at their offsets in it."""
    out = []
    for d, inner in sorted(p.embeds.get(name, {}).items()):
        out.append((d, inner))
        if depth < 8:
            out += [(d + x, i) for x, i in held_structures(p, inner, depth + 1)]
    return out


def pair_structures(p, direct, structs):
    """{(our structure, source structure)}: the pairs direct (typed parameters' structures with their
    parameters' source structures), and, from every pair found, the structures its pointer fields
    point to with the source's structures there, and those it holds with the source's held there
    (structs: source_structures' first part)."""
    pairs, work = set(direct), list(direct)
    while work:
        k, name = work.pop()
        info = structs.get(name)
        if info is None:
            continue
        found = [(k2, info["pointers"][off]) for off, k2 in p.leaf_pointers(k).items() if off in info["pointers"]]
        found += [(inner, info["held"][d]) for d, inner in held_structures(p, k) if info["held"].get(d)]
        for pair in found:
            if pair not in pairs:
                pairs.add(pair)
                work.append(pair)
    return pairs


def count_pairs(pairs, structs):
    """{"source": source structures paired, "ours": ours paired, "extra": ours beyond one per source
    structure (one source structure is several of ours), "several": ours paired with more than one
    source structure, "sizes": of them those paired with source structures of different sizes (one of
    ours that is two of the source's)} of the pairs {(ours, source structure)}."""
    by_source, by_ours = {}, {}
    for k, name in pairs:
        by_source.setdefault(name, set()).add(k)
        by_ours.setdefault(k, set()).add(name)
    several = [names for names in by_ours.values() if len(names) > 1]
    return {"source": len(by_source), "ours": len(by_ours),
            "extra": sum(len(ks) - 1 for ks in by_source.values()), "several": len(several),
            "sizes": sum(1 for names in several if len({(structs.get(n) or {}).get("size") for n in names}) > 1)}


def score_kinds(program, p, elf_path, s=None, trees=None):
    """How many of ours each source structure is (debug information): the plan's structures paired
    with the source's through typed parameters, then through pointer fields and members
    (pair_structures), counted (count_pairs) under "structures"; with the analysis (s, its kinds()
    done, and its trees) the kinds the parameters point to at their bases, paired with the
    parameters' source structures, under "kinds". One of ours paired with two source structures of
    one size can be right: FreeRTOS's StaticQueue_t (xSTATIC_QUEUE) is the queue's memory by design."""
    structs, params = source_structures(elf_path)
    regs = list(program.target.abi.arg_regs)
    direct = set()
    for f, variables_ in p.typed.items():
        fn = program.by_name.get(f)
        sp = params.get(fn.entry, {}) if fn is not None else {}
        for v, k in variables_.items():
            if v in regs and regs.index(v) in sp and k in p.structs:
                direct.add((k, sp[regs.index(v)]))
    out = {"structures": count_pairs(pair_structures(p, direct, structs), structs)}
    if s is not None:
        pairs = set()
        for _, fn, _ in trees:
            f = program.by_name.get(fn.name)
            sp = params.get(f.entry, {}) if f is not None else {}
            for i in sorted(sp):
                r = s.find(("param", fn.name, i))
                if i < len(fn.params) and r in s.points:
                    o, off = s.loc(s.points[r])
                    if o in s.kind_root and off == s.kind_base.get(o, 0):
                        pairs.add((s.kind_root[o], sp[i]))
        out["kinds"] = count_pairs(pairs, structs)
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


def fit(a, b):
    """Whether two layouts ({offset: size}) can be one structure: the same size where both have a
    field, and no field of one across a field of the other (a queue's byte cRxLock at 68 is not a
    task block's word uxMutexesHeld there)."""
    for off, size in a.items():
        if off in b:
            if b[off] != size:
                return False
        elif any(o < off < o + z for o, z in b.items()) or any(off < o < off + size for o in b):
            return False
    return True


def within(a, b):
    """Whether layout a's every field is a field of layout b, of the same size."""
    return all(b.get(off) == size for off, size in a.items())


def pointer_slots(s, find, base, shape, roots, frames):
    """({(kind, offset from its base): (the kinds the field points to at their bases, the kinds whose
    objects point from it to themselves)}, {kind: the kinds that hold it}) of the word fields of the
    objects roots, read through the structures their kinds hold (s.embeds: a task block's field at
    0x14 is its item's at 0x10). find: an object's kind; base: objects' bases; shape: kinds' layouts
    ({offset: size})."""
    held, holding = {}, {}                  # (kind, offset) -> the kinds held there at their bases
    for ra, oa, rb, ob in s.embeds:
        if ob == base.get(rb, 0) and oa - base.get(ra, 0) > 0 and find(ra) != find(rb):
            held.setdefault(find(ra), {}).setdefault(oa - base.get(ra, 0), set()).add(find(rb))
            holding.setdefault(find(rb), set()).add(find(ra))

    def extent(k):
        return max((o + z for o, z in shape.get(k, {}).items()), default=0)

    def canonical(k, rel, depth=0):
        """The kind and offset of a place, inside the structure held there (one kind, no clash)."""
        for d, inners in sorted(held.get(k, {}).items()):
            if len(inners) == 1 and depth < EMBED_DEPTH:
                inner = next(iter(inners))
                if d <= rel < d + extent(inner):
                    return canonical(inner, rel - d, depth + 1)
        return k, rel
    out = {}
    for o in roots:
        k, b = find(o), base.get(o, 0)
        for off, f in s.fields[o].items():
            t = s.find(f.content)
            if off < b or t not in s.points or t in s.interior:
                continue
            o2, off2 = s.loc(s.points[t])
            if o2 in frames or any(x < base.get(o2, 0) for x in s.fields[o2]):
                continue                    # (memory with fields in front of its base: inside a larger
                                            # structure, as a task block reached at its event item, 0x18
                                            # into it; a place inside one with a base is read through)
            target, rel2 = canonical(find(o2), off2 - base.get(o2, 0))
            if rel2 != 0:
                continue                    # (inside one, not at a structure's base: a list's end marker)
            slot = canonical(k, off - b)
            entry = out.setdefault(slot, (set(), set()))
            entry[0].add(target)
            if o2 == o and off2 == b and slot[0] != k:
                entry[1].add(k)             # (an owner: a field of what it holds points back to it)
    return out, holding


def several(targets, holders, holding, shape):
    """Whether a pointer slot holds several kinds of structure (a void *, a union): the targets'
    layouts do not fit together; it points back to the structure that holds it (holders) in a
    structure that two kinds hold (holding: FreeRTOS's pvOwner of an item, the task block that holds
    it or the timer; on GCC -Os only the task blocks' own stores are code DREAD lifts, so the two
    holders are the evidence)."""
    if holders and len(holding | holders) > 1:
        return True
    acc = {}
    for t in targets:
        if not fit(acc, shape.get(t, {})):
            return True
        acc.update(shape.get(t, {}))
    return False


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
        """Whether two kinds' layouts can be one structure (fit)."""
        return fit(shape.get(x, {}), shape.get(y, {}))

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
    # One memory reached as two objects at offsets of their own (v = w + k met after v had an object):
    # the one at its base lies inside the other (a task block's list item at 4, or the block from its
    # item, w - 4).
    for a, b in s.offsets:
        (ra, oa), (rb, ob) = s.loc(a), s.loc(b)
        if ra == rb or ra in frames or rb in frames:
            continue
        da, db = oa - base.get(ra, 0), ob - base.get(rb, 0)
        if da == 0 and db > 0:
            s.embeds.add((rb, ob, ra, oa))
        elif db == 0 and da > 0:
            s.embeds.add((ra, oa, rb, ob))
    # The strongest links first: the more identical fields two sides share, the surer they are one
    # structure; a link whose layouts do not fit is left (one structure handed to a helper that takes
    # several, clang's vPortFree whose free list walk reaches past its header, comes last and fails).
    def strength(link):
        a, b = shape.get(link[0], {}), shape.get(link[1], {})
        return -sum(1 for off, size in a.items() if b.get(off) == size)
    for ra, rb in sorted(links, key=lambda l: (strength(l), l)):
        union(ra, rb)

    def same_places():
        """What sits at one offset of one kind is one kind: every list function's item, given as a
        task block's xStateListItem at 4, is the same structure; not two kinds one function takes
        as different parameters (a list and an item: apart)."""
        joined, place, far = False, {}, apart()
        for ra, oa, rb, ob in sorted(s.embeds):
            if ob != base.get(rb, 0):
                continue
            key = (find(ra), oa - base.get(ra, 0))
            inner = find(rb)
            if key not in place:
                place[key] = inner
            elif find(place[key]) != inner and tuple(sorted((find(place[key]), inner))) not in far \
                    and union(place[key], inner):
                joined = True
        return joined
    # The objects parameters point to at their bases: kinds with an identity of their own (anchored),
    # and per function those it takes as different parameters (a list and the item vListInsert takes
    # have the same layout, five words, yet are two structures).
    anchored, takes = set(), []
    for _, fn, _ in trees:
        objs = set()
        for i in range(len(fn.params)):
            r = s.find(("param", fn.name, i))
            if r in s.points:
                o, off = s.loc(s.points[r])
                if o not in frames and off == base.get(o, 0):
                    objs.add(o)
        anchored |= objs
        if len(objs) > 1:
            takes.append(sorted(objs))
    roots = sorted(o for o, (p, _) in s.obj_parent.items() if p == o and o not in frames)
    voids = set()                           # (kind, offset) of the slots that hold several kinds

    def apart():
        """{(kind, kind)} (sorted pairs) of the kinds one function takes as two of its parameters."""
        out = set()
        for objs in takes:
            ks_ = sorted({find(o) for o in objs})
            out.update((a, b) for i, a in enumerate(ks_) for b in ks_[i + 1:])
        return out

    def same_slots():
        """What one pointer slot holds is one kind (pointer_slots), where nothing says it holds
        several (several); else what it holds joins the one kind it may hold whose layout holds all
        its fields, where exactly one does: the kinds parameters make there, and for a field that
        points back to the structure holding its own, those that hold it (an owner the function
        uses as a task block, its own accesses a task block's, not a timer's)."""
        joined, anchor, far = False, {find(o) for o in anchored}, apart()
        slots, holding = pointer_slots(s, find, base, shape, roots, frames)
        for (k, rel), (targets, holders) in sorted(slots.items()):
            ts = sorted({find(t) for t in targets})
            own = [t for t in ts if t in anchor]
            holders = {find(h) for h in holders}
            if not several(ts, holders, holding.get(k, set()), shape) and not any(
                    (a, b) in far for i, a in enumerate(ts) for b in ts[i + 1:]):
                for t in ts[1:]:
                    joined = union(ts[0], t) or joined
                continue
            voids.add((k, rel))
            choices = sorted(set(own) | (holding.get(k, set()) if holders else set()))
            for t in ts:
                if t in choices:
                    continue
                c = [a for a in choices if within(shape.get(t, {}), shape.get(a, {}))]
                if len(c) == 1:
                    joined = union(c[0], t) or joined
        return joined
    changed = True
    while changed:
        changed = same_places()
        changed = same_slots() or changed
    s.void_slots = {(find(k), rel) for k, rel in voids}
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
    # What sits inside what, kind by kind: {(outer kind, offset from its base): the inner kind, at its
    # base} (a task block's two list items at 4 and 24, a queue's two lists at 16 and 36). A place
    # that two kinds claim which could not be made one holds neither; a kind inside itself (a walk
    # from inside, a pun) is no member.
    s.kind_embeds, clash = {}, set()
    for ra, oa, rb, ob in sorted(s.embeds):
        if ob != base.get(rb, 0):
            continue
        outer, rel, inner = find(ra), oa - base.get(ra, 0), find(rb)
        if rel <= 0 or outer == inner:
            continue
        if s.kind_embeds.setdefault((outer, rel), inner) != inner:
            clash.add((outer, rel))
    for key in clash:
        del s.kind_embeds[key]
    # (a slot of a held structure is also its holders' field there, where the plan leaves it flat)
    work = list(s.void_slots)
    while work:
        k, f = work.pop()
        for (outer, d), inner in s.kind_embeds.items():
            if inner == k and (outer, d + f) not in s.void_slots:
                s.void_slots.add((outer, d + f))
                work.append((outer, d + f))
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


def struct_text(name, rows, fields=None, pointers=None, embeds=None, size=None):
    """The C definition of a structure with these fields, gaps filled with bytes; fields: their names
    by offset where they have one; pointers: {offset: the name of the structure the field points to},
    such a field declared volatile struct NAME * (a word, as its u32); embeds: {offset: (the name of
    a structure held there, its size)}, declared struct NAME (defined before this one); size: bytes
    after the last field up to it (an array's element, as large as its stride)."""
    members = [(rel, ctype, None) for rel, ctype in rows] + \
        [(rel, None, inner) for rel, inner in (embeds or {}).items()]
    lines, pos = ["struct %s" % name, "{"], 0
    for rel, ctype, inner in sorted(members, key=lambda m: m[0]):
        if rel > pos:
            lines.append("    u8 pad_%x[%d];" % (pos, rel - pos))
        if inner is not None:
            lines.append("    struct %s %s;" % (inner[0], field_name(fields, rel)))
            pos = rel + inner[1]
            continue
        if (pointers or {}).get(rel):
            lines.append("    volatile struct %s *%s;" % (pointers[rel], field_name(fields, rel)))
        else:
            lines.append("    %s %s;" % (ctype, field_name(fields, rel)))
        pos = rel + SIZES[ctype]
    if size is not None and size > pos:
        lines.append("    u8 pad_%x[%d];" % (pos, size - pos))
    lines.append("};")
    return "\n".join(lines)


TABLE = "readable_types"


class Plan:
    """What readable C declares (the image's readable_types table): structures ({name: [(offset,
    type)]}) and, per function, which variables point to one ({function: {variable: name}}) with the
    digest of the readable C it was made for ({function: digest}: dread.naming.digest). A structure
    held inside another is a member of it (embeds: {outer: {offset: inner}}); the outer's rows leave
    out what lies inside its members (the inner's fields are its own). statics: the structures of
    static data at fixed addresses ({address: [(offset, type)]}, dread.statics), which every function
    writes its accesses there through, whatever its digest (an address does not change with the
    transformations); arrays: the arrays of static data ({address: (element size, count, [(offset in
    the element, type)])}, dread.statics find_arrays), written so too. params: {function: the indices of
    the parameters it uses as a pointer} for every analysed function (pointer_params), None where the
    table does not say (made before 2026-10-23): dread.naming apply_globals writes the address of
    static data as an argument only for such a parameter of a function listed."""

    def __init__(self):
        self.structs, self.typed, self.digests = {}, {}, {}
        self.pointers = {}              # structure -> {offset: the structure the field points to}
        self.embeds = {}                # structure -> {offset: the structure held there as a member}
        self.statics = {}               # address -> [(offset, type)]: structures of static data (dread.statics)
        self.arrays = {}                # address -> (element size, count, [(offset, type)]): arrays of static data
        self.params = None              # function -> [indices of the parameters it uses as a pointer]

    def to_json(self):
        out = {"version": 1, "structs": {n: [list(r) for r in rows] for n, rows in sorted(self.structs.items())},
               "functions": {f: {"digest": self.digests.get(f), "vars": dict(sorted(v.items()))}
                             for f, v in sorted(self.typed.items())}}
        if self.pointers:
            out["pointers"] = {n: {str(off): t for off, t in sorted(ps.items())}
                               for n, ps in sorted(self.pointers.items()) if ps}
        if any(self.embeds.values()):
            out["embeds"] = {n: {str(off): t for off, t in sorted(es.items())}
                             for n, es in sorted(self.embeds.items()) if es}
        if self.statics:
            out["statics"] = {"0x%x" % a: [list(r) for r in rows] for a, rows in sorted(self.statics.items())}
        if self.arrays:
            out["arrays"] = {"0x%x" % a: {"size": k, "count": n, "fields": [list(r) for r in rows]}
                             for a, (k, n, rows) in sorted(self.arrays.items())}
        if self.params:
            out["params"] = {f: sorted(ix) for f, ix in sorted(self.params.items())}
        return out

    @classmethod
    def from_json(cls, data):
        p = cls()
        p.structs = {n: [tuple(r) for r in rows] for n, rows in data.get("structs", {}).items()}
        for f, entry in data.get("functions", {}).items():
            p.typed[f] = dict(entry.get("vars", {}))
            p.digests[f] = entry.get("digest")
        p.pointers = {n: {int(off): t for off, t in ps.items()} for n, ps in data.get("pointers", {}).items()}
        p.embeds = {n: {int(off): t for off, t in es.items() if t in p.structs}     # (a member of a structure
                    for n, es in data.get("embeds", {}).items() if n in p.structs}  # the table lacks: none)
        p.statics = {int(a, 16): [tuple(r) for r in rows] for a, rows in data.get("statics", {}).items()}
        p.arrays = {int(a, 16): (e["size"], e["count"], [tuple(r) for r in e["fields"]])
                    for a, e in data.get("arrays", {}).items()}
        p.params = {f: list(ix) for f, ix in data["params"].items()} if "params" in data else None
        return p

    def align(self, name, depth=0):
        """A structure's alignment in C: its widest field's, its members' structures' included."""
        out = max((SIZES[t] for _, t in self.structs.get(name, [])), default=1)
        if depth < 8:
            for inner in self.embeds.get(name, {}).values():
                out = max(out, self.align(inner, depth + 1))
        return out

    def size(self, name, depth=0):
        """sizeof the structure in C: the end of its last field or member, rounded up to its alignment."""
        end = max((off + SIZES[t] for off, t in self.structs.get(name, [])), default=0)
        if depth < 8:
            for off, inner in self.embeds.get(name, {}).items():
                end = max(end, off + self.size(inner, depth + 1))
        a = self.align(name, depth)
        return (end + a - 1) // a * a

    def members(self, name):
        """[(offset, type)] of a structure's members in order: its fields' types, and "struct NAME"
        for a structure it holds."""
        rows = list(self.structs.get(name, []))
        rows += [(off, "struct " + inner) for off, inner in self.embeds.get(name, {}).items()]
        return sorted(rows)

    def leaves(self, name, depth=0):
        """[(offset, type)] of every field a structure holds, its members' structures' fields at their
        offsets in it (what the memory holds, the nesting flattened)."""
        out = list(self.structs.get(name, []))
        if depth < 8:
            for off, inner in self.embeds.get(name, {}).items():
                out += [(off + o, t) for o, t in self.leaves(inner, depth + 1)]
        return sorted(out)

    def leaf_pointers(self, name, depth=0):
        """{offset: structure} of the pointer fields a structure holds, its members' at their offsets
        in it."""
        out = dict(self.pointers.get(name, {}))
        if depth < 8:
            for off, inner in self.embeds.get(name, {}).items():
                out.update({off + o: t for o, t in self.leaf_pointers(inner, depth + 1).items()})
        return out

    def place(self, name, off, depth=0):
        """([(structure, offset) of each member on the way], structure, offset in it) of offset off of a
        structure, down through the structures it holds as members (one of them where off lies inside
        it)."""
        path = []
        while depth < 8:
            for d, inner in sorted(self.embeds.get(name, {}).items()):
                if d <= off < d + self.size(inner):
                    path.append((name, d))
                    name, off = inner, off - d
                    break
            else:
                break
            depth += 1
        return path, name, off


POINTER_ROUNDS = 8      # rounds of pointer_params' reading over the functions (callees' answers spread up)


def pointer_params(s, trees):
    """{function: [indices]} of the parameters each analysed function ([(tree, fn, macros)]) uses as a
    pointer: it dereferences the parameter's value, or one it is copied into, or hands it to a callee
    that takes a pointer there (Collector.run's param_used), or the value points to memory the
    analysis knows (its summary has a template for it: stored where something reads through it), or
    the readable C adds it whole to an access's address, also through variables it goes on into, or
    passes it on for such a parameter (dread.naming _address_variables without the uses whose end is
    not known; the analysis follows no sum of two variables, and memcmp's a0 + a4 had made its
    parameters no pointers), over the functions until nothing changes (at most POINTER_ROUNDS rounds).
    What dread.naming apply_globals takes for "passed where the callee dereferences it"; not
    established for a parameter only stored where no analysed code reads through it, and an index the
    readable C adds to an address whole counts as well (a sum does not say which term is the pointer)."""
    from dread.naming import _address_variables
    out = {}
    for _, fn, _ in trees:
        summary = (getattr(s, "summaries", None) or {}).get(fn.name, {})
        out[fn.name] = {i for i in range(len(fn.params))
                        if s.param_used.get((fn.name, i)) or i in summary.get("params", {})}
    for _ in range(POINTER_ROUNDS):
        changed = False
        for _, fn, _ in trees:
            used = _address_variables(fn, out, unknown=False)
            more = {i for i, (_, name) in enumerate(fn.params) if name in used} - out[fn.name]
            if more:
                out[fn.name] |= more
                changed = True
        if not changed:
            break
    return {name: sorted(ix) for name, ix in out.items()}


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


EMBED_DEPTH = 4                     # how deep a pointer is followed into structures held inside others


def plan(s, trees, digests=None, least=2):
    """The plan for the analysed functions: a structure for every kind with a base and at least
    least fields, named s_1, s_2 ... in the order of the first (function, variable) pointing to it;
    a variable typed as a pointer to it when it points to the base, or to where another kind's memory
    holds this one (a task block's list item at 4: kinds()' embeds); the structures one holds inside
    it declared members of it where C can hold them (embed). digests: {function: the digest of its
    readable C before names} (readable_trees)."""
    ks = kinds(s, trees)
    frames = {s.loc((o, 0))[0] for o in s.frames}
    held = {}                               # kind -> [(offset, the kind held there)]
    for (k, rel), inner in sorted(s.kind_embeds.items()):
        held.setdefault(k, []).append((rel, inner))

    def inside(k, rel, depth=0):
        """The kind whose base lies at offset rel of kind k's memory: k at 0, else one held there (or
        inside one held there); None for none."""
        if rel == 0:
            return k
        if depth >= EMBED_DEPTH:
            return None
        if (k, rel) in s.kind_embeds:
            return s.kind_embeds[(k, rel)]
        for d, inner in held.get(k, ()):
            got = inside(inner, rel - d, depth + 1) if 0 < d < rel else None
            if got is not None:
                return got
        return None

    def target(location):
        """The kind whose base a pointer to location points to, or None. (A function's own stack frame
        is its locals, not a structure: GCC -O0's 253 functions had one each.)"""
        o, off = s.loc(location)
        k = s.kind_root.get(o)
        if o in frames or k not in ks:
            return None
        return inside(k, off - s.kind_base.get(o, 0))
    pointing = {}
    for _, fn, _ in trees:
        for v in sorted(pointer_candidates(fn) - _uses(fn) - {FRAME}):
            seen = set()                    # (what each of its webs points to: one structure's base, or
            for key in s.var_webs.get((fn.name, v), ()):        # it stays an integer)
                r = s.find(key)
                if r in s.points:
                    seen.add(target(s.points[r]))
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
    embed(out, {names_of[k]: [(rel, names_of[inner]) for rel, inner in rows if inner in names_of]
                for k, rows in held.items() if k in names_of})
    # A word field is a pointer to a structure when every object of the kind that points somewhere
    # from it points to the base of one structure (a list item's next and previous, its container).
    # A structure held inside another is decided by its own kind's objects: what a task block holds
    # at its items' links came from callees' templates copied onto it, fresh objects of kinds never
    # joined, and made the item's previous no pointer (docs/decisions.md). (Also requiring a variable
    # of that structure loaded from or stored into the field halved what was found and kept the share
    # the source declares so, 48 %: the wrong ones come from kinds made one, a heap block and the
    # queue allocated in it.)
    # A slot that holds several kinds (kinds(): an item's owner, a task block or a timer) is no
    # pointer, whatever the objects of its own kind point to (only task blocks, where the code that
    # stores timers there does not lift).
    voids = s.void_slots
    found = {}
    for o, root in s.kind_root.items():
        name = names_of.get(root)
        if name is None:
            continue
        b = s.kind_base.get(o, 0)
        rows = dict(out.structs[name])
        for off, f in s.fields.get(o, {}).items():
            t = s.find(f.content)
            if rows.get(off - b) != "u32" or t not in s.points or (root, off - b) in voids:
                continue
            # (a value that also points into its own object at another offset points to no one base)
            to = None if t in s.interior else names_of.get(target(s.points[t]))
            found.setdefault(name, {}).setdefault(off - b, set()).add(to)
    for name, rels in found.items():
        ptrs = {rel: next(iter(ts)) for rel, ts in rels.items() if len(ts) == 1 and None not in ts}
        if ptrs:
            out.pointers[name] = ptrs
    out.digests = {f: (digests or {}).get(f) for f in out.typed}
    return out


def embed(p, held):
    """Declares in plan p the members held proposes ({outer: [(offset, inner)]}, the plan's
    structures), each where C can hold it: the inner structures first (in a cycle, the link that
    closes it is left out); at an offset of the inner's alignment, ending where the outer's fields
    reach, not overlapping another member, no field of the outer across its ends; the outer's fields
    inside it go into the inner where they fit (_fit), else the member is left out and the fields
    stay the outer's."""
    order, state = [], {}

    def visit(k):
        state[k] = 1
        for _, inner in held.get(k, ()):
            if inner not in state:
                visit(inner)
        state[k] = 2
        order.append(k)
    for k in sorted(p.structs, key=lambda n: (len(n), n)):
        if k not in state:
            visit(k)
    done = set()
    for k in order:
        # (a member ends where the holder's fields are known to reach: FreeRTOS's list keeps its end
        # marker, a 12-byte mini item, at 8 and uses it as a 20-byte item; held there, the list grew to
        # 28 bytes, and a queue's two lists 20 bytes apart could no longer both be members)
        reach = max((r + SIZES[t] for r, t in p.structs[k]), default=0)
        for d, inner in sorted(held.get(k, ())):
            if inner not in done or inner == k:
                continue                    # (a cycle: it would hold itself)
            z, a = p.size(inner), p.align(inner)
            if z == 0 or d % a or d + z > reach \
                    or any(e < d + z and d < e + p.size(i) for e, i in p.embeds.get(k, {}).items()):
                continue
            rows = p.structs[k]
            if any(r < d < r + SIZES[t] or r < d + z < r + SIZES[t] for r, t in rows):
                continue
            adds = _fit(p, inner, [(r - d, t) for r, t in rows if d <= r < d + z])
            if adds is None:
                continue
            for k2, r, t in adds:
                p.structs[k2] = sorted(p.structs[k2] + [(r, t)])
            p.structs[k] = [(r, t) for r, t in rows if not d <= r < d + z]
            p.embeds.setdefault(k, {})[d] = inner
        done.add(k)


def _fit(p, name, fields):
    """[(structure, offset, type)] of the fields (offset in structure name, type) that structure name
    (or a structure it holds) lacks and can take: naturally aligned, no wider than its alignment (its
    size and alignment stay what its holders were built on), inside its size, across no field or
    member; one it has needs the same size. None when one does not fit."""
    adds = []
    for r, t in fields:
        _, k, off = p.place(name, r)
        rows, z = dict(p.structs[k]), SIZES[t]
        if off in rows:
            if SIZES[rows[off]] != z:
                return None
            continue
        if off < 0 or off % z or z > p.align(k) or off + z > p.size(k) \
                or any(o < off + z and off < o + SIZES[u] for o, u in rows.items()) \
                or any(d < off + z and off < d + p.size(i) for d, i in p.embeds.get(k, {}).items()):
            return None
        adds.append((k, off, t))
    return adds


def _uses(fn):
    """Variables the rewrite must leave alone: written other than by a plain assignment (+=, ++),
    their address taken, named in asm, declared with an initial value, or given the address of a C
    variable of the function (a_1 = (u32)&s1: the lifter reads a stack argument and the one after it
    through it; typed, clang -O2 took the second read as outside s1, and xTaskCreateStatic read unmapped
    memory). The address of static data at a fixed address (dread.naming's (u32)&g_20029988, a macro's
    object, no variable of the function) is none: nothing derives an object's size from it."""
    from dread.cflow import walk_expr
    from dread.naming import _exprs, _inner, variables
    out = set()
    own = set(variables(fn)) | {"frame", FRAME}

    def local(x):
        """Whether an address-of's operand is a variable of the function (or a member or an element of one)."""
        while isinstance(x, (C.Member, C.Index)):
            x = x.base
        return not isinstance(x, C.Name) or x.id in own

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
                        isinstance(y, C.Unary) and y.op == "&" and local(y.e) for y in walk_expr(x.value)):
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
    that every access stays one), their accesses written as members (p->f_2c, p->f_4.f_8 inside a
    structure it holds; with a cast where the access reads another type of the field's size, as raw
    access where the structure has no such field), the address of a structure held inside one as
    &p->f_4, every other use cast to u32; the structures defined above the function, a held one
    before its holder. Only when the plan was made for this C (made_for: its digest before names).
    names: {s_N: (name, {offset: field name})} from the readable_names table (dread.naming
    struct_names). Returns the names of the structures it defined."""
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
    order = []                              # (the structures defined: those typed and those they hold,
                                            # a held one before its holder)
    def visit(k, depth=0):
        if k in order or depth > 8:
            return
        for inner in sorted(set(p.embeds.get(k, {}).values())):
            visit(inner, depth + 1)
        order.append(k)
    for k in sorted(set(typed.values())):
        visit(k)
    fields = {k: dict(p.structs[k]) for k in order}
    ptrs = {k: dict(p.pointers.get(k, {})) for k in order}
    ptype = {k: "volatile struct %s *" % cname(k) for k in set(fields) | {t for ps in ptrs.values()
                                                                         for t in ps.values()}}

    def chain(base, path, last=None):
        """base->m.n...: the members on path ((structure, offset) pairs) and then last, a field's name."""
        parts = [fname(k, d) for k, d in path] + ([last] if last else [])
        e = C.Member(C.Name(base), parts[0], True)
        for part in parts[1:]:
            e = C.Member(e, part, False)
        return e

    def member(e):
        """(member expression, its type, the access's type) for an access through a typed variable,
        or the raw access through its integer value; None when e is no such access. A field that
        points to a structure has the type ("ptr", that structure)."""
        acc = _access(e)
        if acc is None or not isinstance(acc[0], C.Name) or acc[0].id not in typed:
            return None
        base, k, ctype = acc
        path, k2, rel = p.place(typed[base.id], k)
        rows = fields[k2]
        if rows.get(rel) is not None and SIZES[rows[rel]] == SIZES.get(ctype):
            target = ptrs[k2].get(rel)
            return chain(base.id, path, fname(k2, rel)), ("ptr", target) if target else rows[rel], ctype
        address = C.Cast("u32", C.Name(base.id))
        if k:
            address = C.Binary("+" if k > 0 else "-", address, C.Num("%d" % abs(k)))
        return C.Unary("*", C.Cast("volatile %s *" % ctype, address)), ctype, ctype

    def held(e):
        """(structure, &member) for a typed variable plus the offset of a structure its structure holds
        (r4 + 4 for a task block's list item at 4); None for anything else."""
        if not (isinstance(e, C.Binary) and e.op == "+" and isinstance(e.l, C.Name) and e.l.id in typed
                and isinstance(e.r, C.Num) and isinstance(e.r.value, int)):
            return None
        path, k2, rel = p.place(typed[e.l.id], signed32(e.r.value))
        if not path or rel:
            return None
        return k2, C.Unary("&", chain(e.l.id, path))

    def value(e):
        return _rewrite(e, f)

    def as_pointer(k, v):
        """v as a pointer to structure k: (T *)&g_x for the address of static data ((u32)&g_x: dread.naming),
        else the cast of its value."""
        if isinstance(v, C.Cast) and v.type == "u32" and isinstance(v.e, C.Unary) and v.e.op == "&":
            return C.Cast(ptype[k], v.e)
        return C.Cast(ptype[k], value(v))

    def pointer(e):
        """(structure, expression) of what points to a structure as it is: a typed variable, a field
        that points to one, the address of one held inside another; None for anything else."""
        if isinstance(e, C.Name) and e.id in typed:
            return typed[e.id], C.Name(e.id)
        m = member(e)
        if m is not None and isinstance(m[1], tuple):
            return m[1][1], m[0]
        return held(e)

    own = {i: typed[n] for i, (_, n) in enumerate(fn.params) if n in typed}     # (its own typed parameters)

    def f(e):
        if isinstance(e, C.Call) and isinstance(e.f, C.Name) and e.f.id == fn.name and own:
            args = []                       # (a call of itself passes pointers where its parameters are)
            for i, a in enumerate(e.args):
                if i in own:
                    args.append(C.Name(a.id) if isinstance(a, C.Name) and typed.get(a.id) == own[i]
                                else as_pointer(own[i], a))
                else:
                    args.append(value(a))
            return C.Call(C.Name(fn.name), args)
        if isinstance(e, C.Assign):
            if isinstance(e.target, C.Name) and e.target.id in typed and e.op == "=":
                k = typed[e.target.id]
                v = e.value
                if isinstance(v, C.Name) and typed.get(v.id) == k:
                    return C.Assign("=", C.Name(e.target.id), C.Name(v.id))
                mv = member(v)              # (a field that points to its structure, or the address of one
                hv = held(v)                # held inside another: no cast)
                if mv is not None and mv[1] == ("ptr", k):
                    return C.Assign("=", C.Name(e.target.id), mv[0])
                if hv is not None and hv[0] == k:
                    return C.Assign("=", C.Name(e.target.id), hv[1])
                return C.Assign("=", C.Name(e.target.id), as_pointer(k, v))
            m = member(e.target)
            if m is not None:
                if isinstance(m[1], tuple) and e.op == "=":     # (a field that points to a structure)
                    v, t = e.value, m[1][1]
                    if (isinstance(v, C.Name) and typed.get(v.id) == t) or (isinstance(v, C.Num) and v.value == 0):
                        return C.Assign("=", m[0], C.Name(v.id) if isinstance(v, C.Name) else v)
                    hv, mv = held(v), member(v)
                    if hv is not None and hv[0] == t:
                        return C.Assign("=", m[0], hv[1])
                    if mv is not None and mv[1] == ("ptr", t):  # (a field that points to it too)
                        return C.Assign("=", m[0], mv[0])
                    return C.Assign("=", m[0], as_pointer(t, v))
                return C.Assign(e.op, m[0], value(e.value))
            return None
        m = member(e)
        if m is not None:
            expr, ftype, ctype = m
            return expr if ftype == ctype else C.Cast(ctype, expr)
        if isinstance(e, C.Binary) and e.op in ("==", "!="):
            pa, pb = pointer(e.l), pointer(e.r)
            if pa is not None and pb is not None and pb[0] == pa[0]:
                return C.Binary(e.op, pa[1], pb[1])
            # (with 0: a typed variable or a pointer field, not a member's address, which a compiler
            # may take for never 0)
            if pa is not None and held(e.l) is None and isinstance(e.r, C.Num) and e.r.value == 0:
                return C.Binary(e.op, pa[1], e.r)
            return None
        hv = held(e)
        if hv is not None:
            return C.Cast("u32", hv[1])
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
    for k in reversed(order):                                                   # has them already: C17 forbids
        text = struct_text(cname(k), p.structs[k], names.get(k, (k, {}))[1],    # a second definition)
                           {off: cname(t) for off, t in ptrs[k].items()},
                           {off: (cname(i), p.size(i)) for off, i in p.embeds.get(k, {}).items()})
        if text not in present:
            tree.items.insert(at, C.Raw(text))
    for t in sorted({t for ps in ptrs.values() for t in ps.values()}, reverse=True):
        if "struct %s;" % cname(t) not in present:      # (a field may point to a structure defined after it)
            tree.items.insert(at, C.Raw("struct %s;" % cname(t)))
    from dread.build import NULL_CHECKS     # (a pointer it dereferences may be 0 and still be compared with 0:
    tree.items.insert(0, C.Raw(NULL_CHECKS))    # dread.build compiles it so)
    return sorted(order)
