"""dread.frames: the part of a function's stack frame that frame addresses handed out may reach, as the
function's local objects in readable C. Applied to the tree after dread.tidy, dread.structure and the
tables of globals, structures and types, before the variables' names and dread.polish (dread.readable
_framed), since what it writes is nothing the clean-up passes know. Before the names: it finds the
frame by the lifters' names (frame, sp0), which the readable_names table could rename (until
2026-10-24 the names came first, and wherever a naming run had named either, no objects were made),
and the objects are variables the table names too: the naming question shows them (dread.readable
question_tree), an object's identity is its offset below sp0 (object_identities: what carries its name
to C made anew), a name the table gave the frame array or sp0 is dropped where the objects took them
away (table_names), and an entry made from a question that did not show them gets asked again for
them (unasked).

dread.tidy's frame_locals turns the frame's slots into variables (*(volatile u32 *)(sp0 - 36) becomes
s36). When frame addresses leave the function (sp0 - 40 + 0x8u passed to a call: FreeRTOS's TimeOut_t
on xQueueGenericSend's stack), only the slots wholly below the region they may reach become variables
(dread.tidy region_start: from the lowest of them, moved down over an access that reaches into it from
below); from there up to the frame's top (sp0) the frame stays raw. Here that region becomes the
function's locals, split where the code shows objects of their own (layout):

- an object starts at the region's start and at each frame address the function hands out: to a
  callee, into memory, as its result, to assembly, also through a variable that holds it, or with an
  index added (a sum's address: dread.tidy index_sum; where the code folded a constant into the sum,
  the address it computed the sum from when one is handed out: x[i - 1] as sp0 - 48 + r3 - 1); an
  address the function only keeps in a variable or computes with points into an object;
- it reaches up to the next start and ends below the words the function's entry saves its caller's
  registers into (save_words: the callee-saved registers and the return address, through
  dread.readable's Context.saves): those are no local's bytes, and their stores go (RISC-V keeps its
  saves of ra and s0-s11 in the frame array where an address leaves: `stack20.f_10 = ra;` read like a
  field of the buffer);
- objects are joined where one access of the function's spans them (an access's bytes are never
  split), where a variable that holds an address into one computes an address below it (it walks
  downward: an address inside an object that starts lower, printi's print_buf + 11, decremented as
  digits are written: the object below joins), where an index is added to an address no object starts
  at (it reaches the object above too: clang's tmp - 1 for tmp[--n]), and over the size of a structure
  a callee's parameter points to.

Each object is written by what the function and its callees do with it:

    u32 stack36;                    a scalar: one access of one type at its start that fills it, or
                                    one a callee's parameter types so (xTaskCheckForTimeOut's
                                    TickType_t *)
    u8 stack20[12];                 bytes: accessed at no constant offset (a buffer a callee fills), only
                                    by bytes, or indexed by bytes (stack48[r3] = r2); an array of the
                                    width a callee's parameter or the index says (one width throughout)
    struct s_29 stack32;            a structure of dread.shapes that a callee's parameter points to
                                    (vTaskInternalSetTimeOutState's TimeOut_t), where it fits and the
                                    function's own accesses inside it are its plain fields
                                    (shapes.define puts its definition in the file); each access past
                                    it is an object of its own (GCC -O0's spilled locals beside it)
    stack40_t stack40;              a structure of its own for mixed accesses (the timer daemon's
                                    message: three words at 0, 4 and 8), fields at the offsets accessed,
                                    bytes between, plain where every field lies at an offset its size
                                    divides, packed and aligned as its start in the frame where not or
                                    where accesses overlap (they share an anonymous union)

The callee's parameter types are the readable_types table's prototypes (dread.vartypes): a pointer to a
width types a scalar or the elements of an array, a structure pointer the object. The addresses handed
out are the object's ((u32)&stack36, (u32)stack20, (u32)&stack40.f_4, (u32)&stack20[4]); an index
added to an array's address is arithmetic on its address ((u32)stack20 + r5); an access through an
index is an element (stack48[r3], stack44[r1 - 1]: the index the code computes, in elements).

Volatile only where something other than the function and its callees may reach the object while the
function runs: an interrupt or another task reaches a local only through its address, so only where
the address escapes into memory: the function stores it or returns it, hands it to inline assembly or
through a pointer, or to a callee that may keep it (the readable_types table's keeps: a callee keeps a
parameter where a value derived from it is stored, returned, handed to assembly or to a callee that
may keep it; kept_params, over every lifted function; a callee the table does not know keeps every
parameter). And where the function itself accesses it through a variable that holds an address into
it with a type other than bytes: the targets compile with strict aliasing, and a compiler may carry
a plain member across a store of another type (bytes alias everything). Without the table's keeps
(older tables) every object is volatile, as before. Not volatile, a compiler may keep an object's
value in a register between calls, merge or drop accesses no call separates, and drop stores nothing
reads after the last call: what only the function and its callees see does not change by that.

Where an address leaves at no constant offset, below the frame or at or above sp0, or the frame is not
the lifters' local array (u32 frame[N] with sp0 = (u32)&frame[N]; not a wrapped function's real stack,
whose callees may reach the stack arguments above sp0, nor an array whose top the lifter left out, sp0
= (u32)&frame[N] + S: it does so only where no frame address leaves, dread.lift's saves_out), the
function stays as it was. What lies below the region stays as dread.tidy left it (variables, or raw on
the frame array when frame_locals left them); when nothing uses sp0 any more, the frame's
declarations go.

Until 2026-10-23 the whole region was one object (a packed structure named after its offset, volatile,
or one array of bytes): print_status's `char buf[12]` read as `volatile stack20_t stack20` with three
u32 fields over the buffer (the push that made room for it stored r1-r3 there: dread.tidy push_room
takes such stores out now) and the save words of its caller's r4 and lr behind them.

Not established: that a callee stays inside the object it was given (C makes it undefined to reach
past it; the original reached the next object or a saved register there, and the cases refuse an
input that touches saved registers), that an index stays inside its array (the same: the lifted C's
arithmetic on the frame array had a larger object), nor that this C behaves as the lifted C did:
dread readable --check decides that for each function, as for every other transformation.
"""
import re

from dread import cparse as C
from dread.tidy import SLOT_SIZES as SIZES, frame_address, frame_offset, index_sum, region_start

FRAME, BASE = "frame", "sp0"            # the lifters' frame array and the address of its end
OBJECT = re.compile(r"^stack\d+(?:_\d+)?$")     # the names frame_objects gives its objects (a table's may follow)
ALIGNED = re.compile(r"aligned\s*\(\s*(\d+)\s*\)")
DEFINE = re.compile(r"^\s*#\s*define\s+(\w+)", re.M)
PACKED = "__attribute__((packed))"
BYTES = ("u8", "s8")
OTHER, STORE, RETURN, ASM = ("other",), ("store",), ("return",), ("asm",)
KEEPING = ("store", "return", "asm")    # uses by which a value escapes into memory or out of sight
KEEP_ROUNDS = 8                         # rounds of kept_params over the functions (callees first)
EVERY = 16                              # the argument indices an opaque function keeps and returns (all
                                        # that any target passes: argument registers and stack arguments)


# --- what the prologue saves ---

def save_words(program, fn):
    """{depth below the entry's stack pointer: (register, bytes)} of the words fn's entry saves its caller's
    registers into: the callee-saved registers ([abi] callee_saved) and the link register, each stored
    on the entry's straight run (up to the first branch, call or return, or a place a branch reaches)
    before anything writes it, through sp or any register that holds a frame address (dread.regflow
    Frames). {} where the layer has no store_slots or the frame's states are not known."""
    from dread.regflow import Frames
    arch = program.arch
    if not hasattr(arch, "store_slots") or not fn.insns:
        return {}
    keep = set(program.target.abi.callee_saved) | {getattr(arch, "link_reg", None)}
    insns = [program.insn(a) for a in fn.insns]
    joins = set()
    for insn in insns:
        if insn is not None:
            kind, target, _ = arch.flow(insn)
            if kind == "jump" and target is not None:
                joins.add(target & ~1)
    frames = Frames(program, fn)
    if not frames.ok:
        return {}
    written, out, word = set(), {}, getattr(arch, "word", 4)
    for a, insn in zip(fn.insns, insns):
        if insn is None or (a != fn.insns[0] and a in joins):
            break
        for reg, depth in frames.stores(a, insn):
            if reg in keep and reg not in written and depth > 0:
                out.setdefault(depth, (reg, word))
        written |= set(arch.reg_effects(insn)[1])
        if arch.flow(insn)[0] != "next":
            break
    return out


# --- what a function does with its frame and with values derived from roots ---

def _pointee(ctype):
    """The integer type a pointer type of the dialect points to (volatile u8 *: u8), else None."""
    words = ctype.replace("volatile", " ").replace("const", " ").replace("*", " ").split()
    if len(words) != 1 or words[0] not in SIZES or ctype.count("*") != 1:
        return None
    return words[0]


def _children(e):
    for slot in type(e).__slots__:
        v = getattr(e, slot, None)
        if isinstance(v, C.Expr):
            yield v
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, C.Expr):
                    yield x


def _terms(e, sign=1, out=None):
    """[(sign, term)] of a sum of + and - (casts to 32 bits looked through)."""
    out = [] if out is None else out
    if isinstance(e, C.Binary) and e.op in ("+", "-"):
        _terms(e.l, sign, out)
        _terms(e.r, sign if e.op == "+" else -sign, out)
    elif isinstance(e, C.Cast) and e.type in ("u32", "s32"):
        _terms(e.e, sign, out)
    else:
        out.append((sign, e))
    return out


class Scan:
    """What a function's C does with its frame (frame=True) and with the values of roots: frame
    addresses (sp0 plus or minus constants), sums that add an index to one (dread.tidy index_sum) and
    the variables in derived (and those given a root's value: scan). direct: [(node, offset, type,
    store)] of the accesses at a constant offset in the frame; uses: [(root, use, node)], root
    ("frame", offset), ("sum", offset, made, terms) or ("var", name), use ("call", callee or None,
    argument index), ("assign", variable), ("deref", type or None, store) for an access through it,
    ("walk", +1 or -1 or 0) for a variable that holds one changed in place (0: by other than a
    constant), store (into memory or a global), return, asm or other (compared, computed with);
    bad: why the frame cannot be told (an access or address at no constant offset, sp0 used
    otherwise). returned: callee -> the indices of the arguments whose value its result may be
    (kept_params' returns: memset's destination, a function that hands its argument back): a root
    handed so is used as the call's value is too."""

    def __init__(self, fn, derived=(), frame=True, returned=None):
        from dread.naming import variables
        self.fn, self.frame, self.returned = fn, frame, returned or (lambda callee: ())
        self.locals = set(variables(fn))
        self.derived = set(derived)
        self.direct, self.uses, self.bad = [], [], []
        self.inplace = set()                # offsets of direct accesses changed in place (x++, x += k)
        for st in fn.body.body:
            self.stmt(st)

    def root(self, e):
        if self.frame and frame_address(e):
            off = frame_offset(e)
            if off is None:
                self.bad.append("a frame address at no constant offset")
                return None
            return ("frame", off)
        if self.frame:
            got = index_sum(e)
            if got is not None:
                return ("sum",) + tuple(got)
        if isinstance(e, C.Name) and e.id in self.derived:
            return ("var", e.id)
        if isinstance(e, (C.Binary, C.Cast)):
            terms = _terms(e)
            named = [(s, x) for s, x in terms if isinstance(x, C.Name) and x.id in self.derived]
            if len(terms) > 1 and len(named) == 1 and named[0][0] == 1 and \
                    not any(isinstance(x, C.Name) and x.id in (BASE, FRAME) for _, x in terms):
                return ("var", named[0][1].id)
        return None

    def _rest(self, root, e):
        """The parts of e beside its root (an index's terms, what is added to a variable): read."""
        if root[0] == "sum":
            for _, x in root[3]:
                self.value(x, OTHER)
        elif root[0] == "var" and not isinstance(e, C.Name):
            for _, x in _terms(e):
                if not (isinstance(x, C.Name) and x.id == root[1]):
                    self.value(x, OTHER)

    def value(self, e, use):
        if e is None:
            return
        if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast):
            self.deref(e, e.e.e, _pointee(e.e.type), False)
            return
        r = self.root(e)
        if r is not None:
            self.uses.append((r, use, e))
            self._rest(r, e)
            return
        if isinstance(e, C.Name):
            if e.id in (BASE, FRAME) and self.frame:
                self.bad.append("%s used otherwise" % e.id)
            return
        if isinstance(e, C.Assign):
            self.assign(e)
        elif isinstance(e, C.Call):
            callee = e.f.id if isinstance(e.f, C.Name) else None
            if callee is None:
                self.value(e.f, OTHER)
            back = self.returned(callee) if callee is not None else ()
            for i, a in enumerate(e.args):
                self.value(a, ("call", callee, i))
                r = self.root(a) if i in back and use != OTHER else None
                if r is not None:           # (what the call's value is used for, the argument may be)
                    self.uses.append((r, use, a))
        elif isinstance(e, C.Cast):
            self.value(e.e, use if e.type in ("u32", "s32") else OTHER)
        elif isinstance(e, C.Cond):
            self.value(e.c, OTHER)
            self.value(e.t, use)
            self.value(e.f, use)
        elif isinstance(e, C.Comma):
            for x in e.items[:-1]:
                self.value(x, OTHER)
            self.value(e.items[-1], use)
        elif isinstance(e, C.Post) or (isinstance(e, C.Unary) and e.op in ("++", "--")):
            self.update(e.e, 1 if e.op == "++" else -1)
        elif isinstance(e, C.Member):
            if e.arrow:
                self.deref(e, e.base, None, False)
            else:
                self.value(e.base, OTHER)
        elif isinstance(e, C.Index):
            self.deref(e, e.base, None, False)
            self.value(e.index, OTHER)
        elif isinstance(e, C.Unary) and e.op == "&":
            self.value(e.e, OTHER)
        elif isinstance(e, C.Binary) and e.op in ("+", "-"):
            self.value(e.l, use)
            self.value(e.r, use if e.op == "+" else OTHER)
        else:
            for c in _children(e):
                self.value(c, OTHER)

    def deref(self, node, address, t, store):
        if self.frame and frame_address(address):
            off = frame_offset(address)
            if off is None or t is None:
                self.bad.append("a frame access at no constant offset or of no plain type")
            else:
                self.direct.append((node, off, t, store))
            return
        r = self.root(address)
        if r is not None:
            self.uses.append((r, ("deref", t, store), address))
            self._rest(r, address)
            return
        self.value(address, OTHER)

    def update(self, target, sign):
        if isinstance(target, C.Name):
            if target.id in self.derived:
                self.uses.append((("var", target.id), ("walk", sign), target))
            return
        self._inplace(target)
        self.target(target)

    def _inplace(self, t):
        if self.frame and isinstance(t, C.Unary) and t.op == "*" and isinstance(t.e, C.Cast) and \
                frame_address(t.e.e) and frame_offset(t.e.e) is not None:
            self.inplace.add(frame_offset(t.e.e))

    def target(self, t):
        if isinstance(t, C.Unary) and t.op == "*" and isinstance(t.e, C.Cast):
            self.deref(t, t.e.e, _pointee(t.e.type), True)
        elif isinstance(t, C.Member):
            if t.arrow:
                self.deref(t, t.base, None, True)
            else:
                self.value(t.base, OTHER)
        elif isinstance(t, C.Index):
            self.deref(t, t.base, None, True)
            self.value(t.index, OTHER)
        else:
            self.value(t, OTHER)

    def assign(self, e):
        t = e.target
        if isinstance(t, C.Name):
            if t.id not in self.locals:     # (a global: memory)
                self.value(e.value, STORE if e.op == "=" else OTHER)
            elif e.op == "=":
                self.value(e.value, ("assign", t.id))
            else:
                if t.id in self.derived:
                    sign = 0
                    if e.op in ("+=", "-=") and isinstance(e.value, C.Num) and isinstance(e.value.value, int):
                        sign = (1 if e.op == "+=" else -1) * (1 if e.value.value > 0 else -1)
                    self.uses.append((("var", t.id), ("walk", sign), t))
                self.value(e.value, OTHER)
            return
        if e.op != "=":
            self._inplace(t)
        self.target(t)
        self.value(e.value, STORE if e.op == "=" else OTHER)

    def stmt(self, st):
        from dread.naming import _inner
        if isinstance(st, C.Decl):
            for d in st.items:
                if self.frame and d.name in (FRAME, BASE):
                    continue
                if d.init is not None:
                    self.value(d.init, ("assign", d.name))
                for x in d.dims:
                    self.value(x, OTHER)
        elif isinstance(st, C.ExprStmt):
            self.value(st.e, OTHER)
        elif isinstance(st, C.Return):
            self.value(st.e, RETURN)
        elif isinstance(st, (C.If, C.While, C.DoWhile)):
            self.value(st.cond, OTHER)
        elif isinstance(st, C.For):
            for x in (st.init if not isinstance(st.init, C.Decl) else None, st.cond, st.step):
                self.value(x, OTHER)
        elif isinstance(st, C.Switch):
            self.value(st.e, OTHER)
        elif isinstance(st, C.Case):
            self.value(st.value, OTHER)
        elif isinstance(st, C.Asm):
            for o in st.inputs:
                self.value(o.e, ASM)
            for o in st.outputs:
                if not isinstance(o.e, C.Name):
                    self.target(o.e)
        for inner in _inner(st):
            self.stmt(inner)

    def walks(self):
        """{variable: signs}: how the variables that hold a root's value change in place (an increment
        +1, a decrement -1, anything else 0; v = v + 4 is +1)."""
        out = {}
        for r, use, node in self.uses:
            if use[0] == "walk":
                out.setdefault(r[1], set()).add(use[1])
            elif use[0] == "assign" and r[0] == "var" and use[1] == r[1]:
                k = [s * x.value for s, x in _terms(node) if isinstance(x, C.Num) and isinstance(x.value, int)]
                rest = [x for _, x in _terms(node) if not isinstance(x, C.Num) and
                        not (isinstance(x, C.Name) and x.id == r[1])]
                out.setdefault(r[1], set()).add(0 if rest or not k else (1 if sum(k) > 0 else -1 if sum(k) < 0 else 0))
        return out


def scan(fn, roots=(), frame=True, returned=None):
    """Scan of fn with the variables that hold a root's value (given one by an assignment or a
    declaration, also of another such variable) among derived, roots' too: until nothing changes."""
    derived = set(roots)
    for _ in range(16):
        s = Scan(fn, derived, frame, returned)
        new = {use[1] for r, use, _ in s.uses if use[0] == "assign"} - derived
        if not new:
            return s
        derived |= new
    return s


# --- which parameters a function may keep ---

def kept_params(trees, context=None):
    """({function: [indices of the parameters it may keep]}, {function: [indices of the parameters
    whose value its result may be]}) for the functions of trees ([(tree, fn, macros)]: dread.readable
    analysis_trees). A parameter is kept where a value derived from it (it, a copy, it plus or minus
    anything, a variable given one, the result of a callee that may return it) is stored into memory
    or a global, handed to inline assembly, to a call through a pointer, or to a callee that may keep
    it there: one not among the trees (not lifted, not the dialect), one whose parameters regflow
    cannot tell (context.unknown: variadic, or calling one) or one that keeps that parameter; it is
    returned where such a value is the function's result (memset's destination; the lifters' C of a
    function the source declares void returns what its last register held, a pointer walked on);
    over the functions until nothing changes (KEEP_ROUNDS). An opaque function, whose C does not show
    what it does with its arguments (unknown; a naked entry, a wrapped variadic function's: the body
    behind its assembly is the file's other function), keeps and returns every argument (EVERY). What
    dread.frames reads before it declares an object whose address goes to a callee without volatile.
    An access through the value (*(p + 4)) and what it loads are no keeping; a value the function
    stores into its own frame and loads back counts as stored (a store is a store). Not established for a callee that keeps a parameter in a
    way the C does not show (a callee of the original's that is not lifted is assumed to keep all)."""
    fns = {fn.name: fn for _, fn, _ in trees}
    unknown = (lambda n: False) if context is None else context.unknown
    opaque = {name for name, fn in fns.items() if unknown(name) or "naked" in fn.head}
    keeps = {name: set(range(EVERY)) if name in opaque else set() for name in fns}
    returns = {name: set(range(EVERY)) if name in opaque else set() for name in fns}

    def returned(callee):
        return returns.get(callee, ())
    for _ in range(KEEP_ROUNDS):
        changed = False
        for name, fn in fns.items():
            if name in opaque:
                continue
            new_keeps, new_returns = set(), set()
            for i, (_, p) in enumerate(fn.params):
                uses = [use for _, use, _ in scan(fn, roots=(p,), frame=False, returned=returned).uses]
                if any(_keeps(use, fns, keeps, opaque) for use in uses):
                    new_keeps.add(i)
                if any(use == RETURN for use in uses):
                    new_returns.add(i)
            if new_keeps != keeps[name] or new_returns != returns[name]:
                keeps[name], returns[name] = new_keeps, new_returns
                changed = True
        if not changed:
            break
    return ({name: sorted(ix) for name, ix in keeps.items()}, {name: sorted(ix) for name, ix in returns.items()})


def _keeps(use, fns, keeps, opaque):
    """Whether a value used so may be kept beyond the call (kept_params)."""
    if use[0] in ("store", "asm"):
        return True
    if use[0] == "call":
        callee, i = use[1], use[2]
        if callee is None or callee not in fns or callee in opaque:
            return True
        return i in keeps[callee]
    return False


def _returned(context):
    """callee -> the indices of the arguments its result may be, by the readable_types table's returns
    (none where it does not say: an object handed to such a callee is volatile anyway, may_keep)."""
    returns = getattr(getattr(context, "plan", None), "returns", None) or {}
    return lambda callee: returns.get(callee, ())


def may_keep(plan, callee, index):
    """Whether a callee may keep the value handed to it as the argument index, by the readable_types
    table's keeps (kept_params); True where the table does not say (made before 2026-10-23's keeps,
    or a callee it does not know)."""
    keeps = getattr(plan, "keeps", None)
    if keeps is None or callee is None or callee not in keeps:
        return True
    return index in keeps[callee]


# --- the frame and its objects ---

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


def field_name(rel):
    return "f_%x" % rel


def _unsigned(t):
    return "u" + t[1:]


class Fields:
    """A structure's members over the accesses at offsets ({offset: {type, ...}}) of an object of
    size bytes: a field at each offset accessed, bytes between, a union where accesses overlap."""

    def __init__(self, fields, size):
        self.fields, self.size = fields, size

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
        """The structure's members as lines of C (indented by four for each level inside a union)."""
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
        if pos < self.size:
            lines.append("u8 pad_%x[%d];" % (pos, self.size - pos))
        return lines

    def names(self):
        return set(re.findall(r"\b(?:f|pad)_\w+", " ".join(self.members())))

    def unions(self):
        return sum(1 for g in self.groups() if len(g[2]) > 1)

    def natural(self):
        """Whether a plain structure lays its members out where they are: no union, every field at an
        offset its size divides."""
        return not self.unions() and all(rel % SIZES[t] == 0 for rel, types in self.fields.items() for t in types)


class Piece:
    """One of the region's objects: [start, start + size) below sp0 (start negative); fields, the
    function's own accesses at constant offsets ({offset in it: {type}}); kind "scalar", "array",
    "struct" (Fields) or "typed" (a structure of the readable_types table: tag) with elem, the scalar's
    or the elements' type; name; volatile; align, the start's alignment in the frame."""

    def __init__(self, start, end):
        self.start, self.end = start, end
        self.fields = {}
        self.kind, self.elem, self.struct, self.tag, self.name = None, None, None, None, None
        self.volatile, self.align, self.indexed = True, 1, False     # (indexed: accessed through an index)
        self.index_types = set()            # the types of its accesses through an index
        self.inplace = False                # (an access of it changed in place: x++, x += k)
        self.packed = False                 # (packed and aligned as its start whatever its fields: whole_frame)
        self.protos = []                    # the callees' parameter types its start is handed for

    @property
    def size(self):
        return self.end - self.start

    def contains(self, off):
        return self.start <= off < self.end


class Layout:
    """What frame_objects writes: the region's lowest offset (low), its objects (pieces, in order),
    the save words in it ({offset: register}), sp0's type and the frame's declarations; scan: the
    function's Scan."""

    def __init__(self, low, pieces, saves, base_type, frame_decl, base_decl, scan, handed=(), whole=False):
        self.low, self.pieces, self.saves, self.base_type = low, pieces, saves, base_type
        self.frame_decl, self.base_decl, self.scan, self.handed = frame_decl, base_decl, scan, set(handed)
        self.whole = whole                  # (the region as one object: whole_frame)

    def piece(self, off, end=False):
        """The object offset off lies in (end: else the one it lies just past, an address one past its
        end), or None."""
        for p in self.pieces:
            if p.contains(off):
                return p
        if end:
            for p in self.pieces:
                if off == p.end:
                    return p
        return None

    def unions(self):
        return sum(p.struct.unions() for p in self.pieces if p.kind == "struct")

    def arrays(self):
        return [p for p in self.pieces if p.kind == "array"]

    def typed(self):
        """The readable_types table's structures objects are of."""
        return sorted({p.tag for p in self.pieces if p.kind == "typed"})

    def plain(self):
        """The constant frame offsets the function hands out (handed)."""
        return self.handed


def _prototype(context, callee, index):
    """The parameter type the readable_types table's prototypes give a callee's argument (a pointer
    type), else None."""
    plan = getattr(context, "plan", None)
    protos = getattr(plan, "prototypes", None) or {}
    row = protos.get(callee) if callee is not None else None
    if not row or index + 1 >= len(row):
        return None
    return row[index + 1]


def _struct_of(ctype):
    m = re.match(r"^(?:const )?volatile struct (\w+) \*$", " ".join((ctype or "").split()))
    return m.group(1) if m else None


def plan(fn, context=None):
    """The Layout frame_objects would write for the function, or a string saying why there is none."""
    found = frame_declarations(fn)
    if found is None:
        return "no frame array with sp0 at its end"
    fdecl, fd, bdecl, bd, btype, size, base_align = found
    s = scan(fn, returned=_returned(context))
    if s.bad:
        return s.bad[0]
    direct = {}
    for _, off, t, _ in s.direct:
        direct.setdefault(off, set()).add(t)
    # the addresses that leave: frame addresses used as values, and the sums' (the address they add an
    # index to, and those the code computed it from)
    leaves, sums = set(), []
    for r, use, node in s.uses:
        if r[0] == "frame":
            leaves.add(r[1])
        elif r[0] == "sum":
            leaves.add(r[1])
            leaves.update(r[2])
            sums.append(r)
    if not leaves:
        return "no frame address leaves"
    if max(leaves) >= 0:
        return "a frame address at or above sp0 leaves"
    low = region_start([(off, max(SIZES[t] for t in types)) for off, types in direct.items()], min(leaves))
    if low < -size:
        return "a frame address below the frame leaves"
    for off, types in direct.items():
        if off >= low and off + max(SIZES[t] for t in types) > 0:
            return "an access in the region reaches above sp0"
    if whole_frame(fn, context):            # (the region as one object, as before 2026-10-23)
        p = Piece(low, 0)
        p.align = _pow2_alignment(-low, base_align)
        p.fields = {off - low: set(types) for off, types in direct.items() if off >= low}
        p.kind, p.elem, p.struct = ("struct", None, Fields(p.fields, p.size)) if p.fields else ("array", "u8", None)
        p.packed = True
        return Layout(low, [p], {}, btype, (fdecl, fd), (bdecl, bd), s, (), whole=True)
    # the save words: no object's bytes, unless the function accesses one otherwise than by a store of
    # the whole word (the save itself) or an address points into it
    saves = {}
    accs = [(off, SIZES[t], store) for _, off, t, store in s.direct]
    for off, (reg, word) in sorted((context.saves(fn.name) if hasattr(context, "saves") else {}).items()):
        if not (low <= off and off + word <= 0):
            continue
        touching = [(o, n, st) for o, n, st in accs if o < off + word and off < o + n]
        if any(not st or (o, n) != (off, word) for o, n, st in touching) or any(off <= a < off + word for a in leaves):
            continue
        saves[off] = (reg, word)
    saved = set()
    for off, (_, word) in saves.items():
        saved.update(range(off, off + word))
    # where objects start: the region's start and every address handed out (to a callee, into memory,
    # as the result, to assembly), also through a variable that holds it or as a sum's (where the code
    # folded a constant into the sum, the address it computed the sum from when one starts there); an
    # address the function only keeps in a variable or computes with points into an object
    origins = _origins(s)
    plain = handed(s, origins)
    starts = {low} | plain
    for r, use, _ in s.uses:
        if r[0] == "sum" and _hands(use):
            starts.add(_sum_base(r, plain))
    starts = sorted(x for x in starts if x not in saved and low <= x < 0)
    pieces = []
    for i, a in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else 0
        cut = min((w for w in saves if a < w < end), default=end)
        pieces.append(Piece(a, cut))
    # accesses between objects (behind a save word): objects of their own
    for off in sorted(direct):
        if off >= low and off not in saved and not any(p.contains(off) for p in pieces):
            pieces.append(Piece(off, off + max(SIZES[t] for t in direct[off])))
    pieces.sort(key=lambda p: p.start)

    def merge(i):                           # pieces[i] and pieces[i + 1] into one
        a, b = pieces[i], pieces[i + 1]
        pieces[i:i + 2] = [Piece(a.start, max(a.end, b.end))]
    # an access whose bytes lie in two objects joins them
    spans = sorted((off, off + max(SIZES[t] for t in types)) for off, types in direct.items() if off >= low)
    changed = True
    while changed:
        changed = False
        for off, end in spans:
            i = next((i for i in range(len(pieces) - 1) if pieces[i].contains(off) and pieces[i + 1].start < end), None)
            if i is not None:
                merge(i)
                changed = True
                break
    # a variable that holds an address into an object, from which an address below it is computed (it
    # walks downward, or less a constant or an index is accessed or kept): an address inside an object
    # that starts lower (printi's print_buf + 11, decremented as digits are written), the object below
    # joins
    for var in sorted(_reaching_below(s)):
        for off in origins.get(var, ()):
            for i in range(1, len(pieces)):
                if pieces[i].contains(off) and pieces[i - 1].end == pieces[i].start:
                    merge(i - 1)
                    break
    # an index added to an address no object starts at (a sum's, or a variable's that holds one): it
    # reaches the object above too (an address the code made below a buffer it indexes upward: clang's
    # tmp - 1 for tmp[--n])
    above = {_sum_base(r, plain) for r, use, _ in s.uses if r[0] == "sum"}
    above |= {o for var in _reaching_above(s) for o in origins.get(var, ())}
    for off in sorted(above - plain):
        for i in range(len(pieces) - 1):
            if pieces[i].contains(off) and pieces[i].end == pieces[i + 1].start:
                merge(i)
                break
    # a structure a callee's parameter points to that is larger than its object: what lies in it joins
    for r, use, _ in s.uses:
        if r[0] == "frame" and use[0] == "call":
            tag = _struct_of(_prototype(context, use[1], use[2]))
            if tag is not None and context is not None:
                want = r[1] + context.plan.size(tag)
                for i, p in enumerate(pieces):
                    if p.start == r[1]:
                        while i + 1 < len(pieces) and pieces[i].end < want and pieces[i + 1].start < want:
                            merge(i)
                        break
    for p in pieces:
        p.align = _pow2_alignment(-p.start, base_align)
        for off, types in direct.items():
            if p.contains(off):
                p.fields[off - p.start] = set(types)
        p.inplace = any(p.contains(off) for off in s.inplace)
    for r, use, _ in s.uses:                # (accessed through an index: an array where one width serves)
        p = _sum_piece(r, pieces, plain) if r[0] == "sum" and use[0] == "deref" else None
        if p is not None:
            p.indexed = True
            if use[1] is not None:
                p.index_types.add(use[1])
    used = set()                            # (the objects something refers to)
    for r, use, _ in s.uses:
        offs = {r[1]} if r[0] == "frame" else origins.get(r[1], set()) if r[0] == "var" else set()
        if r[0] == "sum":
            got = _sum_piece(r, pieces, plain)
            used.add(id(got))
        for p in pieces:
            if any(p.contains(o) or o == p.end for o in offs):
                used.add(id(p))
            if use[0] == "call" and p.start in offs:
                p.protos.append(_prototype(context, use[1], use[2]))
    pieces = [p for p in pieces if p.fields or id(p) in used]
    if not pieces:
        return "no object in the region"
    pieces = [x for p in pieces for x in _typed_split(p, context)]
    for p in pieces:
        _decide(p, context)
    _volatile(pieces, s, origins, context, plain)
    return Layout(low, pieces, saves, btype, (fdecl, fd), (bdecl, bd), s, plain)


def whole_frame(fn, context):
    """Whether the function's frame region is one object, volatile, as dread.frames wrote it before
    2026-10-23 (the readable_types table's whole: the check puts a function there whose objects its
    cases rejected, a callee given one reaching past it where the original reached the next: FreeRTOS's
    queue functions copy the item size a generated case's queue says)."""
    return fn.name in (getattr(getattr(context, "plan", None), "whole", None) or ())


def _reaching_below(s):
    """The variables (Scan's) from which the function computes an address below the one they hold: a
    decrement or a change by other than a constant, the variable less a constant or less an index."""
    out = set()
    for r, use, node in s.uses:
        if r[0] != "var":
            continue
        if use[0] == "walk":
            if use[1] != 1:
                out.add(r[1])
            continue
        if use[0] == "assign" and use[1] == r[1]:
            continue                        # (v = v + k: Scan.walks)
        terms = _terms(node)
        k = sum(sign * x.value for sign, x in terms if isinstance(x, C.Num) and isinstance(x.value, int))
        k = (k & 0xffffffff) - (1 << 32) if k & 0x80000000 else k
        if k < 0 or any(sign < 0 and not isinstance(x, C.Num) for sign, x in terms):
            out.add(r[1])
    for var, signs in s.walks().items():
        if signs - {1}:
            out.add(var)
    return out


def _reaching_above(s):
    """The variables (Scan's) through which the function reaches above the address they hold by an
    index (a term not a constant, added) or by walking upward."""
    out = set()
    for r, use, node in s.uses:
        if r[0] != "var":
            continue
        if use[0] == "walk":
            if use[1] != -1:
                out.add(r[1])
            continue
        if any(sign > 0 and not isinstance(x, C.Num) and not (isinstance(x, C.Name) and x.id == r[1])
               for sign, x in _terms(node)):
            out.add(r[1])
    return out


def _origins(s):
    """{variable: {offsets}}: the frame offsets the variables that hold a root's value may point at
    (the frame address or sum they were given, through other such variables)."""
    direct, links = {}, {}
    for r, use, _ in s.uses:
        if use[0] != "assign":
            continue
        if r[0] == "frame":
            direct.setdefault(use[1], set()).add(r[1])
        elif r[0] == "sum":
            direct.setdefault(use[1], set()).add(r[1])
        elif r[0] == "var" and r[1] != use[1]:
            links.setdefault(use[1], set()).add(r[1])
    out = {v: set(o) for v, o in direct.items()}
    for _ in range(16):
        changed = False
        for v, srcs in links.items():
            for u in srcs:
                add = out.get(u, set()) - out.get(v, set())
                if add:
                    out.setdefault(v, set()).update(add)
                    changed = True
        if not changed:
            break
    return out


def _hands(use):
    """Whether a value used so goes where the function does not see it: to a callee, into memory, as
    the result, to assembly."""
    return use[0] in ("call",) + KEEPING


def handed(s, origins):
    """The constant frame offsets the function hands out (_hands), directly or through a variable that
    holds them (origins: _origins)."""
    out = set()
    for r, use, _ in s.uses:
        if not _hands(use):
            continue
        if r[0] == "frame":
            out.add(r[1])
        elif r[0] == "var":
            out.update(o for o in origins.get(r[1], ()) if o < 0)
    return out


def _sum_base(r, plain):
    """The offset a sum adds its index to: its own where an address is handed out there, else the
    highest it was computed from where one is (sp0 - 48 for sp0 - 48 + r3 - 1), else its own."""
    off, made = r[1], r[2]
    if off in plain:
        return off
    near = [m for m in made if m in plain]
    return max(near) if near else off


def _sum_piece(r, pieces, plain):
    """The object a sum adds its index to (_sum_base)."""
    off = _sum_base(r, plain)
    for p in pieces:
        if p.contains(off):
            return p
    return None


def _typed(p, context):
    """The structure of the readable_types table that p's start is handed for (a callee's parameter
    points to it: Piece.protos), where it fits p and p's own accesses inside it are its plain fields;
    else None."""
    elems = {_pointee(x) for x in p.protos if x is not None and _pointee(x) is not None}
    tags = {_struct_of(x) for x in p.protos if _struct_of(x) is not None}
    plan_ = getattr(context, "plan", None)
    if len(tags) != 1 or elems or plan_ is None or p.indexed:
        return None
    tag = next(iter(tags))
    if tag not in plan_.structs or plan_.size(tag) > p.size:
        return None
    rows, size = dict(plan_.structs[tag]), plan_.size(tag)
    held = set(plan_.embeds.get(tag, {})) | set(plan_.pointers.get(tag, {}))
    for rel, ts in p.fields.items():
        if rel < size and not (rel in rows and rel not in held and all(SIZES[t] == SIZES[rows[rel]] for t in ts)
                               and rel + max(SIZES[t] for t in ts) <= size):
            return None
    return tag


def _typed_split(p, context):
    """[p], or p typed by a callee's structure (_typed) and, where the function accesses bytes past
    that structure up to p's end, an object of their own for each access there (a group where they
    overlap): what the callee's type does not reach is none of its object (GCC -O0's spilled locals
    beside the TimeOut_t whose address goes to xTaskCheckForTimeOut)."""
    tag = _typed(p, context)
    if tag is None:
        return [p]
    end = p.start + context.plan.size(tag)
    rest = sorted((rel, ts) for rel, ts in p.fields.items() if p.start + rel >= end)
    p.fields = {rel: ts for rel, ts in p.fields.items() if p.start + rel < end}
    p.kind, p.tag, p.end = "typed", tag, end
    out = [p]
    for rel, ts in rest:
        off, size = p.start + rel, max(SIZES[t] for t in ts)
        if len(out) > 1 and off < out[-1].end:
            q = out[-1]
            q.end = max(q.end, off + size)
        else:
            q = Piece(off, off + size)
            q.align = _pow2_alignment(-off, 8)
            out.append(q)
        q.fields[off - q.start] = set(ts)
    return out


def _decide(p, context):
    """The kind of object p is (Piece), from the function's accesses (at constant offsets and through
    an index), and the callees' parameter types its start is handed for (Piece.protos)."""
    if p.kind == "typed":                   # (_typed_split)
        return
    own = {t for ts in p.fields.values() for t in ts}
    types = own | p.index_types
    elems = {_pointee(x) for x in p.protos if x is not None and _pointee(x) is not None}
    hint = next(iter(elems)) if len(elems) == 1 else None
    if not types:
        e = hint or "u8"
        if p.size == SIZES[e] and not p.indexed:
            p.kind, p.elem = "scalar", e
        else:
            p.kind, p.elem = ("array", e) if p.size % SIZES[e] == 0 else ("array", "u8")
        return
    widths = {SIZES[t] for t in types}
    if len(widths) == 1 and all(rel % SIZES[t] == 0 for rel, ts in p.fields.items() for t in ts) and \
            not (p.inplace and len(types) > 1):     # (x++ or x >>= 1 of both signs: a member each)
        w = next(iter(widths))
        t = next(iter(types)) if len(types) == 1 else _unsigned(sorted(types)[0])
        if hint is not None and SIZES[hint] == w:
            t = hint
        if w == 1:                          # (bytes: the item's u8 x[n])
            p.kind, p.elem = ("scalar" if p.size == 1 and not p.indexed else "array"), t
            return
        if set(p.fields) == {0} and not p.indexed and (p.size == w or hint == t):
            p.kind, p.elem, p.end = "scalar", t, p.start + w
            return
        if p.indexed and p.size % w == 0:
            p.kind, p.elem = "array", t
            return
    p.kind, p.struct = "struct", Fields(p.fields, p.size)


def _volatile(pieces, s, origins, context, plain):
    """Which objects stay volatile (the module's docstring): where an address into one escapes, or
    the function accesses one through a variable with another type than bytes; all of them where the
    readable_types table has no keeps."""
    plan_ = getattr(context, "plan", None)
    if plan_ is None or getattr(plan_, "keeps", None) is None:
        return
    for p in pieces:
        p.volatile = False

    def mark(offsets):
        for off in offsets:
            for p in pieces:
                if p.contains(off) or off == p.end:
                    p.volatile = True
    for r, use, _ in s.uses:
        if r[0] == "frame":
            offs = {r[1]}
        elif r[0] == "sum":
            got = _sum_piece(r, pieces, plain)
            offs = {got.start} if got is not None else {r[1]}
        else:
            offs = origins.get(r[1], set())
        if use[0] in KEEPING or (use[0] == "call" and may_keep(plan_, use[1], use[2])):
            mark(offs)
        elif use[0] == "deref" and r[0] == "var" and use[1] not in BYTES:
            mark(offs)


# --- writing them ---

def frame_objects(tree, fn, context=None, avoid=()):
    """Write the region of the function's frame that addresses handed out may reach as its local
    objects (see the module's docstring); returns their names, or None when the function stays as it
    was. avoid: names the objects must not take (those the function's variables will get)."""
    from dread.cflow import walk_expr
    from dread.naming import _rewrite, _rewrite_stmt, other_names, variables
    from dread.shapes import field_name as shape_field
    layout = plan(fn, context)
    if not isinstance(layout, Layout):
        return None
    macros = {m for item in tree.items if isinstance(item, C.Raw) for m in DEFINE.findall(item.text)}
    if any(p.kind == "struct" and macros & p.struct.names() for p in layout.pieces):
        return None                         # (a macro of the file would take a member's name)
    taken = other_names(tree, fn) | set(variables(fn)) | set(avoid)
    names = context.struct_names() if layout.typed() else {}
    for p in layout.pieces:
        name, k = "stack%d" % -p.start, 0
        while name in taken or name + "_t" in taken:
            k += 1
            name = "stack%d_%d" % (-p.start, k)
        p.name = name
        taken.add(name)
    base_type = layout.base_type
    plain = layout.plain()

    def fname(p, rel):
        return shape_field(names.get(p.tag, (p.tag, {}))[1], rel)

    def member(p, rel, t):
        """The expression of an access of type t at offset rel of p."""
        if p.kind == "scalar":
            e, own = C.Name(p.name), p.elem
        elif p.kind == "array":
            e, own = C.Index(C.Name(p.name), C.Num("%d" % (rel // SIZES[p.elem]))), p.elem
        elif p.kind == "typed":
            e, own = C.Member(C.Name(p.name), fname(p, rel), False), dict(context.plan.structs[p.tag])[rel]
        else:
            e, own = C.Member(C.Name(p.name), p.struct.member(rel, t), False), t
        return e if own == t else C.Cast(t, e)

    def address(p, rel, field=True):
        """The address rel bytes into p, of sp0's type: an element's, a field's or the object's."""
        whole = C.Cast(base_type, C.Name(p.name) if p.kind == "array" else C.Unary("&", C.Name(p.name)))
        if rel == 0:
            return whole
        if field and p.kind == "array" and rel % SIZES[p.elem] == 0:
            return C.Cast(base_type, C.Unary("&", C.Index(C.Name(p.name), C.Num("%d" % (rel // SIZES[p.elem])))))
        if field and p.kind == "struct" and rel in p.struct.fields:
            return C.Cast(base_type, C.Unary("&", C.Member(C.Name(p.name), p.struct.at(rel), False)))
        if field and p.kind == "typed" and rel in dict(context.plan.structs[p.tag]):
            return C.Cast(base_type, C.Unary("&", C.Member(C.Name(p.name), fname(p, rel), False)))
        return C.Binary("+", whole, C.Num("%d" % rel))

    def element(p, rel, terms, t):
        """p[index] for an access of type t rel bytes plus terms ([(sign, term)]) into array p, or None
        where the index is not a whole number of its elements (or the region is one object: its index
        stays arithmetic on the address, as before)."""
        w = SIZES[p.elem]
        if SIZES[t] != w or rel % w or layout.whole:
            return None
        idx = None
        for sign, x in terms:
            if w > 1:
                x = _scaled(x, w)
                if x is None:
                    return None
            x = _rewrite(x, f)
            if idx is None:
                idx = x if sign > 0 else C.Unary("-", x)
            else:
                idx = C.Binary("+" if sign > 0 else "-", idx, x)
        k = rel // w
        if k:
            idx = C.Binary("+" if k > 0 else "-", idx, C.Num("%d" % abs(k)))
        e = C.Index(C.Name(p.name), idx)
        return e if t == p.elem else C.Cast(t, e)

    def target(t):
        """An access written to (an assignment's target, an increment's operand) as its object's member
        without a cast to its own type (the same bits; _decide makes a structure where an in-place change
        has another type than the object's), or None."""
        if not (isinstance(t, C.Unary) and t.op == "*" and isinstance(t.e, C.Cast) and frame_address(t.e.e)):
            return None
        off = frame_offset(t.e.e)
        p = layout.piece(off)
        if p is None:
            return None
        m = member(p, off - p.start, _pointee(t.e.type))
        return m.e if isinstance(m, C.Cast) else m

    def f(e):
        if isinstance(e, C.Assign):
            m = target(e.target)
            return C.Assign(e.op, m, _rewrite(e.value, f)) if m is not None else None
        if isinstance(e, C.Post) or (isinstance(e, C.Unary) and e.op in ("++", "--")):
            m = target(e.e)
            return (C.Post(e.op, m) if isinstance(e, C.Post) else C.Unary(e.op, m)) if m is not None else None
        if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast):
            a, t = e.e.e, _pointee(e.e.type)
            if frame_address(a):
                off = frame_offset(a)
                p = layout.piece(off)
                if p is not None:
                    return member(p, off - p.start, t)
                return e                    # (below the region, or a save word's: raw, as dread.tidy left it)
            got = index_sum(a)
            if got is not None and t is not None:
                p = _sum_piece(("sum",) + tuple(got), layout.pieces, plain)
                if p is not None and p.kind == "array":
                    return element(p, got[0] - p.start, got[2], t)
            return None
        if frame_address(e):
            off = frame_offset(e)
            p = layout.piece(off, end=True)
            return address(p, off - p.start) if p is not None else None
        got = index_sum(e)
        if got is not None:
            p = _sum_piece(("sum",) + tuple(got), layout.pieces, plain)
            if p is not None:
                out = address(p, got[0] - p.start, field=False)
                for sign, term in got[2]:
                    out = C.Binary("+" if sign > 0 else "-", out, _rewrite(term, f))
                return out
        return None
    # the save words' stores go: they keep the caller's registers for the epilogue, and the C compiler
    # keeps its own
    fn.body.body[:] = [st for st in fn.body.body if not (
        isinstance(st, C.ExprStmt) and isinstance(st.e, C.Assign) and st.e.op == "=" and
        isinstance(st.e.target, C.Unary) and st.e.target.op == "*" and isinstance(st.e.target.e, C.Cast) and
        frame_address(st.e.target.e.e) and frame_offset(st.e.target.e.e) in layout.saves)]
    for st in fn.body.body:
        _rewrite_stmt(st, f)
    (fdecl, fd), (bdecl, bd) = layout.frame_decl, layout.base_decl
    if not any(isinstance(x, C.Name) and x.id == BASE for e in _expressions(fn) for x in walk_expr(e)):
        fdecl.items[:] = [d for d in fdecl.items if d is not fd]     # (nothing uses the frame array any more)
        bdecl.items[:] = [d for d in bdecl.items if d is not bd]
    at = next(i for i, st in enumerate(fn.body.body) if st is bdecl)     # (before every use of sp0)
    fn.body.body[at + 1:at + 1] = [_declaration(p, names) for p in layout.pieces]
    fn.body.body[:] = [st for st in fn.body.body if not (isinstance(st, C.Decl) and not st.items)]
    if layout.saves:                        # (the registers those stores saved, now nobody's: RISC-V's
        _unused(fn)                         # u32 ra = (u32)__builtin_return_address(0), s0 = 0)
    at = next((i + 1 for i, item in enumerate(tree.items)       # (after the #include, as dread.shapes' structures)
               if isinstance(item, C.Raw) and item.text.startswith("#include")), tree.items.index(fn))
    for p in reversed(layout.pieces):
        if p.kind == "struct":
            tree.items.insert(at, C.Raw(_typedef(p)))
    if layout.typed():
        from dread.shapes import define
        define(tree, fn, context.plan, layout.typed(), names)
    return [p.name for p in layout.pieces]


frame_struct = frame_objects            # (its name until 2026-10-23, when the region was one object)


# --- the objects and the readable_names table ---

def table_names(names, before, fn):
    """The part of a readable_names entry's names ({variable: name}, made for the function's C before
    frame_objects ran on it, whose variables were before) that applies to its C now: without the names
    of the variables frame_objects took away (the frame array and sp0 where nothing reaches the frame
    raw any more, the registers whose save stores went), whose role the objects took over, and without
    those of objects this C does not have (OBJECT: made where the readable_types table's whole or
    prototypes split the region otherwise); every other name as it is, for dread.naming apply to
    check as before."""
    from dread.naming import variables
    now = set(variables(fn))
    gone = set(before) - now
    return {v: n for v, n in names.items() if v not in gone and (v in now or not OBJECT.match(v))}


def object_identities(fn, ids, objects):
    """The identities (dread.naming identities) of the variables of the function's C after frame_objects
    made objects (their names) in it: ids, those taken before it ran (where a proposal's identities
    are taken), for the variables it left, and the objects' own from the C now: a scalar by its base
    (stack36: its offset below sp0, as s36 is the slot's) and the instructions that write it, an array or
    a structure, whose stores go to its elements and members, by its base alone (stack20). What
    dread.naming carry_map matches an object's name by when the C is made anew: the object of the same
    offset (with the same definitions, for a scalar)."""
    from dread.naming import identities, variables
    have = set(variables(fn))
    out = {v: i for v, i in ids.items() if v in have}
    if objects:
        now = identities(fn)
        out.update((v, now[v]) for v in objects if v in now)
    return out


def unasked(entry, fn, ids):
    """The objects of the function's C (fn and ids: as dread.readable question_trees makes them, the
    frame's objects with their own names) that a readable_names entry made for that C has no name for
    and whose identity the entry's ids do not have: the question it was made from did not show them (a
    question before 2026-10-24 showed the frame raw, sp0 - 20 for stack20; an entry without ids is from
    such a question). What dread pilot --task readable asks again for, the entry's other names shown."""
    from dread.naming import variables
    names = entry.get("names") or {}
    seen = set(entry["ids"].values()) if isinstance(entry.get("ids"), dict) else set()
    return [v for v in variables(fn) if OBJECT.match(v) and v not in names and ids.get(v) not in seen]


def _unused(fn):
    """The body's declarators of variables nothing names any more (no dimension, no register binding,
    an initial value without effects: a constant, the return address's builtin) go."""
    from dread.cflow import has_effects, walk_expr
    from dread.naming import _inner
    named = set()

    def visit(st):
        if isinstance(st, C.Decl):
            for d in st.items:
                for x in [d.init] + list(d.dims):
                    if x is not None:
                        named.update(n.id for n in walk_expr(x) if isinstance(n, C.Name))
        else:
            for e in _stmt_exprs(st):
                named.update(n.id for n in walk_expr(e) if isinstance(n, C.Name))
        for inner in _inner(st):
            visit(inner)
    for st in fn.body.body:
        visit(st)

    def builtin(e):
        return isinstance(e, C.Cast) and isinstance(e.e, C.Call) and isinstance(e.e.f, C.Name) and \
            e.e.f.id == "__builtin_return_address"
    for st in fn.body.body:
        if isinstance(st, C.Decl):
            st.items[:] = [d for d in st.items if d.name in named or d.dims or d.asm or d.ptr or
                           (d.init is not None and not builtin(d.init) and has_effects(d.init))]
    fn.body.body[:] = [st for st in fn.body.body if not (isinstance(st, C.Decl) and not st.items)]


def _stmt_exprs(st):
    """The expressions a statement holds itself (not those of the statements it holds)."""
    from dread.naming import _exprs
    if isinstance(st, C.Asm):
        return [o.e for o in st.outputs + st.inputs]
    return _exprs(st)


def _scaled(x, w):
    """x as an element index for elements of w bytes: y for y << log2(w) or y * w (or w * y), else
    None."""
    shift = {2: 1, 4: 2, 8: 3}.get(w)
    if isinstance(x, C.Binary) and x.op == "<<" and isinstance(x.r, C.Num) and x.r.value == shift:
        return x.l
    if isinstance(x, C.Binary) and x.op == "*":
        if isinstance(x.r, C.Num) and x.r.value == w:
            return x.l
        if isinstance(x.l, C.Num) and x.l.value == w:
            return x.r
    return None


def _typedef(p):
    """The C definition of a structure object's type: plain where every field lies at an offset its
    size divides and no access overlaps another, else packed (the frame's layout, no member moved for
    its alignment) and aligned as the object's start is in the frame."""
    attrs = "" if p.struct.natural() and not p.packed else " __attribute__((packed, aligned(%d)))" % p.align
    return "typedef struct\n{\n%s\n}%s %s_t;" % ("\n".join("    " + m for m in p.struct.members()), attrs, p.name)


def _declaration(p, names):
    """An object's declaration in the function."""
    vol = "volatile " if p.volatile else ""
    if p.kind == "struct":
        return C.Decl("%s%s_t" % (vol, p.name), [C.Declarator(p.name)])
    if p.kind == "typed":
        return C.Decl("%sstruct %s" % (vol, names.get(p.tag, (p.tag, {}))[0]), [C.Declarator(p.name)])
    dims = [C.Num("%d" % (p.size // SIZES[p.elem]))] if p.kind == "array" else []
    attrs = "__attribute__((aligned(%d)))" % p.align if p.packed and p.align > SIZES[p.elem] else ""
    return C.Decl("%s%s" % (vol, p.elem), [C.Declarator(p.name, dims=dims, attrs=attrs)])


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
