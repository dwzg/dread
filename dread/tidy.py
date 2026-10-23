"""dread readable's clean-up: the lifted function's tree (dread.cparse) without what only the machine
needed. Each transformation keeps the function's behaviour by itself (a variable nobody reads,
a copy, a constant, a temporary used once); the gate (dread readable --check) checks the result all
the same.

- split: a chain of assignments of one value (r1 = r2 = r3 = r12 = 0, after a call) into one each;
- unvoid: the (void)x statements that kept the compiler quiet about unused variables;
- flatten: an instruction's block ({ u32 x = r1, y = r3; ... } for a compare's flags) into the body,
  its own variables renamed to fresh ones of the function;
- dead: an assignment to a variable nobody reads before it is written again (the flags a branch does
  not test, the zeroed registers after a call); one whose value has an effect (a call, a volatile
  load) keeps the effect;
- propagate: a variable read where a constant or a copy of another variable is the only value that
  reaches it (and that variable is not written in between);
- fold: a temporary written once and read once, right behind its write, into its reader; one whose
  value has an effect only where the reader evaluates it before any effect of its own, and on every
  path (not into the right of && or ||, nor an arm of ?:);
- declarations nobody uses, and initial values nobody reads, go.

Conditional execution (Thumb-2's IT blocks, ARM's conditional instructions) comes from the lifters as
one conditional block per instruction on the flags (if (z) { r0 = 0; } if (!z) { r0 = *(r1); }),
with the IT instruction's note between. What makes such code one if/else again:

- merge (merge_ifs): a run of conditional blocks on one pure condition or its negation is one
  if/else, as long as nothing in the arms writes what the condition reads; the notes go in front of
  it, and up to BETWEEN statements without a call between two such blocks (a load GCC scheduled
  between two IT blocks) go into both arms, where they ran on either path;
- dead and fold see inside the arms (flat_view: the blocks spread out), so a register an arm sets
  for its store alone goes, and the store takes the constant; an arm left empty goes (empty_arms);
- flatten also spreads an instruction's block inside an arm (a conditional compare, cmpne), and
  chains makes z = A; if (!z) z = B; the z = A || B it computes;
- conditions rewrites every truth value, not only a branch's condition: a flag several statements
  read keeps its variable but is a comparison (z = r0 == r3), the operands of && || ! where the whole
  has no smaller form, a select's condition in a return; candidates include the lifters' compare
  operands themselves (cmp r0, r5 after adds r5, r2, #3: r0 == r2 + 3); values also rewrites a
  return's value and statements inside arms (strcmp's r2 - r3).

Flags read through variables, and the lifters' carry arithmetic around them (docs/decisions.md, "Flags
as comparisons"):

- flags: a truth value that reads flags, 64-bit temporaries or a chain of subtractions, each variable
  with one definition that reaches it unchanged (FlagFlow), as the comparison z3 proves it to be in
  terms of what those definitions read: a compare read by several statements (cmd_mode's ls, an if's
  and a select's), an add's carry away from its add (the sum below an operand), a chain of
  subtractions (x == 8 for x - 1 - 2 == 5), a conditional compare's two flags (FlagFlow.chosen: one
  condition of two comparisons), a borrow added back (r1 == 0); a condition whose operands change
  before it is read is computed where its flags are set (cc_1 = r1 > 9);
- sums: a sum through 64 bits whose carry nobody reads, anywhere in a statement, as the 32-bit sum it
  is (r0 - 8, uxListRemove(r6 + 4));
- pairs: a two-word addition (adds; adc) as one 64-bit sum, its words read as the halves of it; a
  two-word compare (cmp; sbcs) is one 64-bit comparison among conditions' candidates (chained).

Statements keep the instruction they came from (their addr); a statement folded into another takes
its reader's, but an expression statement or a return that takes a call's result in names the call
in its comment (return f(); names the bl, not the bx lr after it: cparse.take, which keeps the
statement's own instruction, so that the lines the printer groups and the digests stay). What a
statement took in from others (fold, propagate, spread, addresses) it keeps as absorbed instructions
(cparse.Addr), where the reading form finds its first instruction (dread readable --plain). Functions
whose lifted file asks for registers fixed or kept (dread.build's header lines) and blocks that bind
variables to registers (__asm__("r0")) are left alone: their registers are behaviour.
"""
import re
import threading

from dread import cparse as C
from dread import cexpr
from dread.cflow import Graph, has_effects, walk_expr, _reads_of, is_volatile_access, is_pure_call


# --- small helpers ---

def stmt_lists(fn):
    """The statement lists a pass may edit: the body's (nested blocks are the instructions' own)."""
    return [fn.body.body]


def is_assign(s, op="="):
    return isinstance(s, C.ExprStmt) and isinstance(s.e, C.Assign) and s.e.op == op and isinstance(s.e.target, C.Name)


def substitute(e, name, value):
    """e with every read of variable name replaced by (a copy of) value; an assignment's plain target
    stays (it is written, not read)."""
    import copy
    if isinstance(e, C.Name):
        return copy.deepcopy(value) if e.id == name else e
    if isinstance(e, C.Assign):
        target = e.target if (isinstance(e.target, C.Name) and e.op == "=") else substitute(e.target, name, value)
        return C.Assign(e.op, target, substitute(e.value, name, value))
    if isinstance(e, C.Unary):
        return C.Unary(e.op, substitute(e.e, name, value))
    if isinstance(e, C.Post):
        return C.Post(e.op, substitute(e.e, name, value))
    if isinstance(e, C.Cast):
        return C.Cast(e.type, substitute(e.e, name, value))
    if isinstance(e, C.Binary):
        return C.Binary(e.op, substitute(e.l, name, value), substitute(e.r, name, value))
    if isinstance(e, C.Cond):
        return C.Cond(substitute(e.c, name, value), substitute(e.t, name, value), substitute(e.f, name, value))
    if isinstance(e, C.Call):
        return C.Call(substitute(e.f, name, value), [substitute(a, name, value) for a in e.args])
    if isinstance(e, C.Index):
        return C.Index(substitute(e.base, name, value), substitute(e.index, name, value))
    if isinstance(e, C.Member):
        return C.Member(substitute(e.base, name, value), e.name, e.arrow)
    if isinstance(e, C.Comma):
        return C.Comma([substitute(x, name, value) for x in e.items])
    if isinstance(e, C.Sizeof):
        return C.Sizeof(e.type, substitute(e.e, name, value) if e.e is not None else None)
    if isinstance(e, C.InitList):
        return C.InitList([substitute(x, name, value) for x in e.items])
    if isinstance(e, C.Designated):
        return C.Designated(e.designator, substitute(e.value, name, value))
    if isinstance(e, C.Compound):
        return C.Compound(e.type, substitute(e.init, name, value))
    return e


def reads_count(e, name):
    """How often e reads variable name (a plain assignment's target is no read)."""
    n = 0
    if isinstance(e, C.Name):
        return 1 if e.id == name else 0
    if isinstance(e, C.Assign):
        if not (isinstance(e.target, C.Name) and e.op == "="):
            n += reads_count(e.target, name)
        return n + reads_count(e.value, name)
    for child in _children(e):
        n += reads_count(child, name)
    return n


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
    if isinstance(e, C.Sizeof):
        return [e.e] if e.e is not None else []
    if isinstance(e, C.InitList):
        return e.items
    if isinstance(e, C.Designated):
        return [e.value]
    if isinstance(e, C.Compound):
        return [e.init]
    return []


def stmt_exprs(s):
    """The expressions of a simple statement a read may be replaced in: (getter, setter) pairs."""
    if isinstance(s, C.ExprStmt):
        return [(lambda s=s: s.e, lambda v, s=s: setattr(s, "e", v))]
    if isinstance(s, C.Return) and s.e is not None:
        return [(lambda s=s: s.e, lambda v, s=s: setattr(s, "e", v))]
    if isinstance(s, C.If) and isinstance(s.then, (C.Goto, C.Return)) and s.orelse is None:
        out = [(lambda s=s: s.cond, lambda v, s=s: setattr(s, "cond", v))]
        if isinstance(s.then, C.Return) and s.then.e is not None:
            out.append((lambda t=s.then: t.e, lambda v, t=s.then: setattr(t, "e", v)))
        return out
    if isinstance(s, C.If):                 # (a conditional block: its condition; the arms are its own)
        return [(lambda s=s: s.cond, lambda v, s=s: setattr(s, "cond", v))]
    if isinstance(s, C.Switch):
        return [(lambda s=s: s.e, lambda v, s=s: setattr(s, "e", v))]
    return []


def simple(s):
    """A statement whose every read is in stmt_exprs (nothing nested that runs on its own)."""
    return isinstance(s, (C.ExprStmt, C.Return, C.Switch)) or (
        isinstance(s, C.If) and isinstance(s.then, (C.Goto, C.Return)) and s.orelse is None)


def foldable_into(s, v):
    """Whether v may be folded into statement s: a simple one, or an if whose condition alone reads v
    (its arms run after the condition and read other things)."""
    if simple(s):
        return True
    return isinstance(s, C.If) and _reads_in_cond_only(s, v)


def fixed_registers(tree):
    """Whether the lifted file reads registers as the caller left them (dread.build's FIXED line: the
    C binds them with asm, and GCC's -ffixed-REG keeps them so): such C stays as the lifter wrote it.
    A CALL_SAVED line (-fcall-saved-REG: registers the C must keep for its callers) constrains only
    GCC's register allocation, whatever the C says; the stages run on such a file and keep the line."""
    from dread import build
    return any(isinstance(i, C.Raw) and i.text.startswith(build.FIXED.rstrip()) for i in tree.items)


def declarations(fn):
    """{name: (Decl, Declarator)} of the body's top-level declarations."""
    out = {}
    for s in fn.body.body:
        if isinstance(s, C.Decl):
            for d in s.items:
                out[d.name] = (s, d)
    return out


def type_of(fn, name, decls=None):
    found = (declarations(fn) if decls is None else decls).get(name)
    if found:
        decl, d = found
        if d.ptr or d.dims:
            return None
        return " ".join(w for w in decl.specs.split() if w not in ("register", "static", "volatile", "const"))
    for ptype, pname in fn.params:
        if pname == name:
            return ptype
    return None


# --- the passes; each returns whether it changed something ---

def split(fn):
    changed = False
    for body in stmt_lists(fn):
        out = []
        for s in body:
            chain, e = [], s.e if isinstance(s, C.ExprStmt) else None
            while isinstance(e, C.Assign) and e.op == "=" and isinstance(e.target, C.Name):
                chain.append(e.target.id)
                e = e.value
            if len(chain) > 1 and isinstance(e, (C.Num, C.Name)) and len({type_of(fn, n) for n in chain}) == 1 \
                    and type_of(fn, chain[0]) is not None:
                for name in chain:
                    x = C.ExprStmt(C.Assign("=", C.Name(name), e), s.addr)
                    out.append(x)
                changed = True
            else:
                out.append(s)
        body[:] = out
    return changed


def unvoid(fn):
    before = len(fn.body.body)
    fn.body.body[:] = [s for s in fn.body.body if not (
        isinstance(s, C.ExprStmt) and isinstance(s.e, C.Cast) and s.e.type == "void" and isinstance(s.e.e, C.Name))]
    return len(fn.body.body) != before


def fresh(fn, base, taken):
    k = 1
    while "%s_%d" % (base, k) in taken:
        k += 1
    name = "%s_%d" % (base, k)
    taken.add(name)
    return name


def all_names(fn):
    names = {p[1] for p in fn.params}

    def visit(s):
        if isinstance(s, C.Decl):
            names.update(d.name for d in s.items)
        for e in _stmt_all_exprs(s):
            for x in walk_expr(e):
                if isinstance(x, C.Name):
                    names.add(x.id)
        for inner in _inner(s):
            visit(inner)
    for s in fn.body.body:
        visit(s)
    return names


def _inner(s):
    if isinstance(s, C.Block):
        return list(s.body)
    if isinstance(s, C.If):
        return [s.then] + ([s.orelse] if s.orelse is not None else [])
    if isinstance(s, (C.While, C.DoWhile, C.For, C.Switch)):
        return [s.body]
    return []


def _stmt_all_exprs(s):
    if isinstance(s, (C.ExprStmt,)):
        return [s.e]
    if isinstance(s, C.Return):
        return [s.e] if s.e is not None else []
    if isinstance(s, C.If):
        return [s.cond]
    if isinstance(s, (C.While, C.DoWhile)):
        return [s.cond]
    if isinstance(s, C.For):
        return [x for x in (s.init if not isinstance(s.init, C.Decl) else None, s.cond, s.step) if x is not None]
    if isinstance(s, C.Switch):
        return [s.e]
    if isinstance(s, C.Decl):
        return [d.init for d in s.items if d.init is not None]
    if isinstance(s, C.Asm):
        return [o.e for o in s.outputs + s.inputs]
    if isinstance(s, C.Case):
        return [s.value] if s.value is not None else []
    return []


def rename_in(s, old, new):
    """Every use of variable old in statement s (and what it contains) renamed to new, in place."""
    def ren(e):
        if e is None:
            return None
        for x in walk_expr(e):
            if isinstance(x, C.Name) and x.id == old:
                x.id = new
        return e
    if isinstance(s, C.Decl):
        for d in s.items:
            ren(d.init)
            if d.name == old:
                d.name = new
    elif isinstance(s, C.Asm):
        for o in s.outputs + s.inputs:
            ren(o.e)
    else:
        for e in _stmt_all_exprs(s):
            ren(e)
        if isinstance(s, C.For) and isinstance(s.init, C.Decl):
            rename_in(s.init, old, new)
    for inner in _inner(s):
        rename_in(inner, old, new)


def flatten(fn):
    """An instruction's block into the body: its variables renamed to fresh ones declared at the top,
    their initial values assignments where the declarations stood. Also such a block inside a
    conditional block's arm (an IT block's conditional compare, cmpne r4, r1: if (!z) { { u32 x = r4,
    y = r1; ... z = r == 0; ... } }), into that arm."""
    taken = all_names(fn)
    hoisted = []
    changed = False

    def spread(stmts):
        nonlocal changed
        out = []
        for s in stmts:
            if isinstance(s, C.If):
                for arm in (s.then, s.orelse):
                    if isinstance(arm, C.Block):
                        arm.body[:] = spread(arm.body)
            if not isinstance(s, C.Block) or any(isinstance(x, C.Decl) and any(d.asm or d.dims or d.ptr for d in x.items)
                                                 for x in s.body) or any(isinstance(x, (C.Label, C.Case)) for x in s.body):
                out.append(s)
                continue
            inner = [C.Block(list(s.body))]
            renames = []
            for x in s.body:
                if isinstance(x, C.Decl):
                    for d in x.items:
                        renames.append((d.name, fresh(fn, d.name, taken), x.specs))
            block = inner[0]
            for old, new, specs in renames:
                for x in block.body:
                    rename_in(x, old, new)
            for x in block.body:
                if isinstance(x, C.Decl):
                    for d in x.items:
                        hoisted.append(C.Decl(" ".join(w for w in x.specs.split() if w != "register"),
                                              [C.Declarator(d.name)]))
                        if d.init is not None:
                            out.append(C.ExprStmt(C.Assign("=", C.Name(d.name), d.init), s.addr))
                else:
                    if x.addr is None:
                        x.addr = s.addr
                    out.append(x)
            changed = True
        return out
    body = fn.body.body
    out = spread(body)
    if hoisted:
        last = max((i for i, x in enumerate(out) if isinstance(x, C.Decl)), default=-1)
        out[last + 1:last + 1] = hoisted
    body[:] = out
    return changed


def dead(fn):
    """Assignments nobody reads (their effects kept), until none is left; those inside a conditional
    block too (flat_view: an IT block's register nobody reads after it); and x = x, what a reload
    of a spilled register or a value minus 0 simplified to."""
    changed = False
    while True:
        view = flat_view(fn)
        g = Graph(view)
        live = g.live()
        edits = []                          # (statement, what takes its place: None for nothing)
        for n in g.nodes:
            s = n.stmt
            if id(s) not in view.homes:     # (the view's own gotos and labels)
                continue
            if is_assign(s) and isinstance(s.e.value, C.Name) and s.e.value.id == s.e.target.id:
                edits.append((s, None))     # (x = x: what a value simplified to, r0 - 0)
                continue
            if is_assign(s) and s.e.target.id not in live[n.index] and s.e.target.id in g.scope:
                value = s.e.value
                if not has_effects(value):
                    edits.append((s, None))
                    continue
                if isinstance(value, C.Call):
                    edits.append((s, C.ExprStmt(value, s.addr)))
                    continue
                if is_volatile_access(value) or (isinstance(value, C.Cast) and is_volatile_access(value.e)):
                    load = value.e if isinstance(value, C.Cast) else value
                    # (a load of the function's own frame: no effect)
                    edits.append((s, None if frame_address(load.e.e) else C.ExprStmt(C.Cast("void", load), s.addr)))
                    continue
            if isinstance(s, C.ExprStmt) and isinstance(s.e, C.Cast) and s.e.type == "void" and \
                    is_volatile_access(s.e.e) and frame_address(s.e.e.e.e):
                edits.append((s, None))
        if not edits:
            return changed
        for s, new in edits:
            replace(view, s, new)
        changed = True


def propagate(fn):
    """Constants and copies into the simple statements that read them, those inside a conditional
    block too (flat_view: a constant address in a register reaches an IT block's load)."""
    view = flat_view(fn)
    g = Graph(view)
    reach = g.reaching()
    changed = False
    for n in g.nodes:
        s = n.stmt
        if not simple(s) or not n.reads:
            continue
        for v in sorted(n.reads):
            defs = reach[n.index].get(v, set())
            if not defs or -1 in defs:
                continue
            stmts = [g.nodes[d].stmt for d in defs]
            if not all(is_assign(x) and x.e.target.id == v for x in stmts):
                continue
            values = [x.e.value for x in stmts]
            value = values[0]
            if isinstance(value, C.Num) and all(isinstance(x, C.Num) and x.value == value.value and x.text == value.text
                                                 for x in values):
                pass
            elif len(defs) == 1 and isinstance(value, C.Name) and value.id in g.scope and value.id != v:
                d = next(iter(defs))
                if reach[n.index].get(value.id) != reach[d].get(value.id) or _writes_name(s, value.id):
                    continue
            else:
                continue
            if _writes_name(s, v) and reads_count_stmt(s, v) == 0:
                continue
            for get, put in stmt_exprs(s):
                if reads_count(get(), v):
                    put(substitute(get(), v, value))
                    changed = True
                    for d in sorted(defs):
                        C.absorb(s, g.nodes[d].stmt)
    write_back(view)
    return changed


def _writes_name(s, name):
    for e in _stmt_all_exprs(s):
        for x in walk_expr(e):
            if isinstance(x, C.Assign) and isinstance(x.target, C.Name) and x.target.id == name:
                return True
    return False


def reads_count_stmt(s, name):
    return sum(reads_count(get(), name) for get, _ in stmt_exprs(s))


def selects(fn):
    """if (X) { v = A; } if (!X) { v = B; }  ->  v = X ? A : B;  (an IT block's pair of conditional
    moves: X, A and B pure, v not one of X's variables); if (X) { v = A; } else { v = B; } alike (the
    pair merge_ifs made one if). Not a pair behind a conditional block on X or !X that writes v when A
    or B reads v (ittte mi; ldrmi r0, [r3]; uxtbmi r0, r0; movpl r0, #-1): merge_ifs first makes the
    three one if, whose arms write v on either path; the select would read v where the data flow sees
    v's value from the function's entry reach it as well (only through the path on which the condition
    was false, where the select does not read it), and the function would take v as a parameter."""
    from dread.structure import negate
    body = fn.body.body
    out, changed, k = [], False, 0

    def one(x, arm):
        if isinstance(x, C.If) and isinstance(arm, C.Block) and len(arm.body) == 1 and is_assign(arm.body[0]):
            return x.cond, arm.body[0].e.target.id, arm.body[0].e.value
        return None

    def pure(a, b):
        return a[1] == b[1] and not any(has_effects(e) for e in (a[0], a[2], b[2])) and a[1] not in _reads_of(a[0])
    while k < len(body):
        s = body[k]
        t = body[k + 1] if k + 1 < len(body) else None
        if isinstance(s, C.If) and s.orelse is not None:
            a, b = one(s, s.then), one(s, s.orelse)
            if a and b and pure(a, b):
                out.append(C.ExprStmt(C.Assign("=", C.Name(a[1]), C.Cond(a[0], a[2], b[2])), s.addr))
                k += 1
                changed = True
                continue
        a = one(s, s.then) if isinstance(s, C.If) and s.orelse is None else None
        b = one(t, t.then) if isinstance(t, C.If) and t.orelse is None else None
        if a and b and C.expr_text(negate(a[0])) == C.expr_text(b[0]) and pure(a, b) and \
                not _merges_first(out, a[0], a[1], (a[2], b[2])):
            out.append(C.ExprStmt(C.Assign("=", C.Name(a[1]), C.Cond(a[0], a[2], b[2])), s.addr))
            k += 2
            changed = True
            continue
        out.append(s)
        k += 1
    body[:] = out
    return changed


def _merges_first(out, cond, v, values):
    """Whether the last statement of out (notes aside) is a conditional block on cond or its negation
    that writes v while one of values reads v (selects)."""
    from dread.structure import negate
    prev = next((x for x in reversed(out) if not isinstance(x, (C.Note, C.Empty))), None)
    if not (isinstance(prev, C.If) and prev.orelse is None and isinstance(prev.then, C.Block)) or \
            has_effects(prev.cond) or _reads_memory(prev.cond):
        return False
    text = C.expr_text(prev.cond)
    if text not in (C.expr_text(cond), C.expr_text(negate(cond))):
        return False
    return v in _written([prev.then]) and any(v in _reads_of(x) for x in values)


def empty_arms(fn):
    """An if whose arms dead and fold emptied: one empty arm goes (an empty then makes the else the
    then, its condition negated), and an if with neither goes when its condition is pure."""
    from dread.structure import negate
    changed = False

    def visit(stmts):
        nonlocal changed
        out = []
        for s in stmts:
            if isinstance(s, C.If) and isinstance(s.then, C.Block) and (s.orelse is None or isinstance(s.orelse, C.Block)):
                visit(s.then.body)
                if s.orelse is not None:
                    visit(s.orelse.body)
                    if not s.orelse.body:
                        s.orelse = None
                        changed = True
                if not s.then.body:
                    if s.orelse is not None:
                        s.cond, s.then, s.orelse = negate(s.cond), s.orelse, None
                        changed = True
                    elif not has_effects(s.cond):
                        changed = True
                        continue
            out.append(s)
        stmts[:] = out
    visit(fn.body.body)
    return changed


def _arm(s):
    """An if's arm as a list of statements (a block's own list; [] for no else)."""
    if s is None:
        return []
    return s.body if isinstance(s, C.Block) else [s]


def _written(stmts):
    """The variables statements may write, those of statements nested in them too (an IT block's
    conditional compare is a block of its own inside the if, and writes z), asm outputs included."""
    written = set()
    for x in stmts:
        for st in _flat(x):
            for e in _stmt_all_exprs(st):
                written |= {y.target.id for y in walk_expr(e) if isinstance(y, C.Assign) and isinstance(y.target, C.Name)}
                written |= {y.e.id for y in walk_expr(e) if (isinstance(y, C.Post) or (
                    isinstance(y, C.Unary) and y.op in ("++", "--"))) and isinstance(y.e, C.Name)}
            if isinstance(st, C.Asm):
                written |= {o.e.id for o in st.outputs if isinstance(o.e, C.Name)}
    return written


def _has(stmts, kinds):
    return any(isinstance(x, kinds) for a in stmts for x in _flat(a))


# Statements between two conditional blocks on one condition that merge_ifs writes into both arms of
# the merged if (GCC's itt ne; movne; movne; ldr r0, [r5]; it ne; strne: a load between two IT blocks).
# One: two gained no statements on FreeRTOS GCC -Os and wrote more twice (docs/decisions.md).
BETWEEN = 1


def _between(x):
    """Whether merge_ifs may write statement x into both arms: an expression statement without a call
    (a load, a store, arithmetic)."""
    return isinstance(x, C.ExprStmt) and not any(isinstance(y, C.Call) and not is_pure_call(y) for y in walk_expr(x.e))


def merge_ifs(fn):
    """A run of ifs on one condition or its negation as one if/else: an IT block's conditional
    instructions, one if each (if (z) a; if (!z) b; if (!z) c; if (z) d;), become if (z) { a; d; }
    else { b; c; }. On each path the statements that ran before still run, in their order: only where
    the condition is pure (no effect, no memory read) and nothing in the arms merged so far writes
    what it reads, so that it has its truth at every if of the run. Only conditional blocks (if (c)
    { ... }): a branch (if (c) goto L; if (c) return x;) stays one for the structure stage; no arm has
    a label (nothing jumps into the middle of one), a goto, break or continue (the structure stage
    leaves a function alone where a statement hides a goto), and an arm that takes more statements
    has no return (what is appended would run where it did not). Up to BETWEEN statements between two
    such ifs (_between: no call) are written into both arms, where they ran on either path; they too
    must not write what the condition reads. The instructions' own notes between them (/* 716c: ite
    ne */) go in front of the merged if."""
    import copy
    from dread.structure import negate
    body = fn.body.body
    out, changed = [], False
    for s in body:
        j, between = -1, []
        if isinstance(s, C.If) and isinstance(s.then, C.Block) and not has_effects(s.cond) and \
                not _reads_memory(s.cond):
            j = len(out) - 1
            while j >= 0 and not isinstance(out[j], C.If):
                if isinstance(out[j], (C.Note, C.Empty)):
                    j -= 1
                elif _between(out[j]) and len(between) < BETWEEN:
                    between.insert(0, out[j])
                    j -= 1
                else:
                    j = -1
        prev = out[j] if j >= 0 else None
        if isinstance(prev, C.If) and isinstance(prev.then, C.Block) and not has_effects(prev.cond):
            text = C.expr_text(s.cond)
            same = text == C.expr_text(prev.cond)
            opposite = not same and (text == C.expr_text(negate(prev.cond)) or
                                     C.expr_text(negate(s.cond)) == C.expr_text(prev.cond))
            pt, pe, st, se = _arm(prev.then), _arm(prev.orelse), _arm(s.then), _arm(s.orelse)
            if opposite:
                st, se = se, st
            if (same or opposite) and not _has(pt + pe + st + se, (C.Label, C.Case, C.Goto, C.Break, C.Continue)) \
                    and not ((st or between) and _has(pt, C.Return)) and not ((se or between) and _has(pe, C.Return)) \
                    and not _written(pt + pe + between) & _reads_of(s.cond):
                prev.then = C.Block(pt + between + st, prev.then.addr if isinstance(prev.then, C.Block) else None)
                orelse = pe + [copy.deepcopy(x) for x in between] + se
                prev.orelse = C.Block(orelse) if orelse else None
                notes = [x for x in out[j + 1:] if isinstance(x, (C.Note, C.Empty))]
                del out[j + 1:]
                out[j:j] = notes
                changed = True
                continue
        out.append(s)
    body[:] = out
    return changed


def chains(fn):
    """v = A; if (!v) { v = B; }  ->  v = A || B;  and  v = A; if (v) { v = B; }  ->  v = A && B;  with A
    and B truth values (0 or 1) and B not reading v: B is evaluated exactly where it was. An IT block's
    conditional compare (cmp r0, r5; it ne; cmpne r4, r1 leaves z = r0 == r5 || r4 == r1)."""
    body = fn.body.body
    out, changed = [], False
    for s in body:
        k = len(out)
        while k and isinstance(out[k - 1], (C.Note, C.Empty)):
            k -= 1
        prev = out[k - 1] if k else None
        if is_assign(prev) and truth_value(prev.e.value) and isinstance(s, C.If) and s.orelse is None and \
                isinstance(s.then, C.Block) and len(s.then.body) == 1 and is_assign(s.then.body[0]) and \
                s.then.body[0].e.target.id == prev.e.target.id and truth_value(s.then.body[0].e.value):
            v, b = prev.e.target.id, s.then.body[0].e.value
            negated = isinstance(s.cond, C.Unary) and s.cond.op == "!" and isinstance(s.cond.e, C.Name) and \
                s.cond.e.id == v
            plain = isinstance(s.cond, C.Name) and s.cond.id == v
            if (negated or plain) and v not in _reads_of(b):
                prev.e.value = C.Binary("||" if negated else "&&", prev.e.value, b)
                notes = out[k:]
                del out[k:]
                out[k - 1:k - 1] = notes
                changed = True
                continue
        out.append(s)
    body[:] = out
    return changed


def frame_address(e):
    """Whether e is an address in the function's own frame: sp0 plus or minus constants (the lifters'
    frame base, the address of a local array whose address nobody else is given through it)."""
    if isinstance(e, C.Name):
        return e.id == "sp0"
    if isinstance(e, C.Binary) and e.op in ("+", "-") and isinstance(e.r, C.Num):
        return frame_address(e.l)
    if isinstance(e, C.Cast) and e.type in ("u32", "u16"):
        return frame_address(e.e)
    return False


def frame_stores(fn):
    """Stores into the function's own frame go when nothing can read them: no frame address leaves the
    function (sp0 appears only as the address of its own loads and stores: none is passed to a call,
    kept in a variable or stored) and nothing loads from the frame any more (dead removed the loads
    behind a pop). The values pushed only to be popped again, mostly."""
    loads, escapes = [False], [False]

    def visit(e, store=False):
        if e is None:
            return
        if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast) and frame_address(e.e.e):
            if not store:
                loads[0] = True
            return
        if isinstance(e, C.Name):
            if e.id == "sp0":
                escapes[0] = True
            return
        if isinstance(e, C.Assign):
            visit(e.target, store=(e.op == "="))
            visit(e.value)
            return
        for c in _children(e):
            visit(c)

    def visit_stmt(st):
        if isinstance(st, C.Decl):
            for d in st.items:
                if d.name != "sp0":
                    visit(d.init)
        elif isinstance(st, C.Asm):
            for o in st.outputs + st.inputs:
                visit(o.e)
        else:
            for e in _stmt_all_exprs(st):
                visit(e)
        for inner in _inner(st):
            visit_stmt(inner)
    for st in fn.body.body:
        visit_stmt(st)
    if loads[0] or escapes[0]:
        return False
    out, changed = [], False
    for st in fn.body.body:
        if isinstance(st, C.ExprStmt) and isinstance(st.e, C.Assign) and st.e.op == "=" and \
                isinstance(st.e.target, C.Unary) and st.e.target.op == "*" and \
                isinstance(st.e.target.e, C.Cast) and frame_address(st.e.target.e.e):
            changed = True
            if has_effects(st.e.value):
                out.append(C.ExprStmt(st.e.value, st.addr))
            continue
        out.append(st)
    fn.body.body[:] = out
    return changed


def frame_offset(e):
    """The constant offset of a frame address (sp0 plus or minus constants), or None."""
    if isinstance(e, C.Name) and e.id == "sp0":
        return 0
    if isinstance(e, C.Binary) and e.op in ("+", "-") and isinstance(e.r, C.Num) and isinstance(e.r.value, int):
        base = frame_offset(e.l)
        if base is None:
            return None
        return base + e.r.value if e.op == "+" else base - e.r.value
    if isinstance(e, C.Cast) and e.type in ("u32", "u16"):
        return frame_offset(e.e)
    return None


def frame_index(e):
    """(offset, terms) of a sum that adds terms other than constants to the frame's base sp0 (once, added):
    its constants gathered into the offset, a frame address the terms ([(sign, term)]) are added to.
    sp0 - 64 + 0x20u + s0_1 + 0xffffffecu (RISC-V's addi a5, sp, 32; add a5, a5, s0; lbu a5, -20(a5))
    is (-52, [(1, s0_1)]): an index added to sp0 - 52, which the code reached by way of sp0 - 32. The
    offset is taken modulo 2**32, as a signed value. None when e is no such sum."""
    if not (isinstance(e, C.Binary) and e.op in ("+", "-")):
        return None
    terms = []

    def flat(x, sign):
        if isinstance(x, C.Binary) and x.op in ("+", "-"):
            flat(x.l, sign)
            flat(x.r, sign if x.op == "+" else -sign)
        else:
            terms.append((sign, x))
    flat(e, 1)
    bases = [s for s, x in terms if isinstance(x, C.Name) and x.id == FRAME_BASE]
    others = [(s, x) for s, x in terms if not (isinstance(x, C.Num) and isinstance(x.value, int))
              and not (isinstance(x, C.Name) and x.id == FRAME_BASE)]
    if bases != [1] or not others or any(FRAME_BASE in _reads_of(x) for _, x in others):
        return None
    k = sum(s * x.value for s, x in terms if isinstance(x, C.Num) and isinstance(x.value, int)) % (1 << 32)
    return (k - (1 << 32) if k >= 1 << 31 else k), others


def index_sum(e):
    """(offset, made, terms) of a sum that adds an index to a frame address below sp0 (frame_index):
    made, the frame addresses written inside it (sp0 - 48 in sp0 - 48 + r3 - 1: what the code computed
    before it added the index; sp0 alone, as clang's sp0 + r5 - 0x14u has it, is none); terms, the
    index's terms as [(sign, expression)], as written (casts kept). None for anything else, and for a
    sum at or above sp0."""
    got = frame_index(e) if isinstance(e, C.Binary) else None
    if got is None or got[0] >= 0:
        return None
    made = set()

    def visit(x):
        if frame_address(x):
            off = frame_offset(x)
            if off is not None and off < 0:
                made.add(off)
        elif isinstance(x, C.Binary) and x.op in ("+", "-"):
            visit(x.l)
            visit(x.r)
    visit(e)
    return got[0], tuple(sorted(made)), list(got[1])


def frame_sums(e, found, inside=False):
    """Into found, the offsets below sp0 of the frame addresses that sums in e add an index to
    (frame_index, each sum whole: not the parts of a longer one). The frame passes count them as
    addresses that leave, beside the frame addresses written inside the sums, which they counted before
    (sp0 - 32 of the sum above): so a region starts no higher than it did, and also where the compiler
    reached a lower address by way of a higher one."""
    if e is None:
        return found
    plus = isinstance(e, C.Binary) and e.op in ("+", "-")
    if plus and not inside:
        got = frame_index(e)
        if got is not None and got[0] < 0:
            found.add(got[0])
    for c in _children(e) if not isinstance(e, C.Assign) else [e.target, e.value]:
        frame_sums(c, found, plus)
    return found


# The prefixes of frame slots made variables (frame_locals), tried in order: s36 for sp0 - 36 unless a
# name of the function has it already, as RISC-V's saved registers s4 and s8 and the VFP's single
# registers do; then every slot of that function takes the next prefix (sl36).
SLOT_PREFIXES = ("s", "sl", "slot")
SLOT_SIZES = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "u64": 8, "s64": 8}


def region_start(spans, low):
    """Where the part of the frame that addresses handed out may reach starts: at the lowest of them
    (low), moved down over every access that reaches into it from below, until none does. spans: the
    (offset, size) of the frame's accesses at constant offsets. GCC -Os makes room for a 16-bit local
    by pushing a word, and hands the local's address on (FreeRTOS's vPolledQueueProducer: the push
    stores at sp0 - 28, the local is the word's upper half at sp0 - 26): the region then starts at the
    push, which writes part of what the address reaches. frame_locals makes variables of the slots
    wholly below it, dread.frames one object of the rest."""
    moved = True
    while moved:
        moved = False
        for off, size in spans:
            if off < low < off + size:
                low, moved = off, True
    return low


def _types_used(fn):
    """The dialect's integer types the function's C names (casts, declarations): a slot named s16 would
    hide the type s16 from them, and its signed reads become casts to such a type."""
    import re
    return set(re.findall(r"\b[su](?:8|16|32|64)\b", C.unparse(fn, notes=False)))


def frame_locals(fn):
    """The frame's words as variables of their own: *(volatile u32 *)(sp0 - 36) becomes s36, where
    every slot is accessed with one type and no two overlap. When frame addresses leave the function
    (sp0 - 64 passed to a call, a local's address, one an index is added to: frame_sums), only the
    slots wholly below the lowest of them (outgoing stack arguments, locals nobody else is given)
    become variables, and below a word that reaches into what lies above it (region_start); from there
    up the frame stays as it is, since what the address is handed to may reach anything above it
    (dread.frames). Nothing changes
    when an address that leaves is not a constant offset below sp0; an index added to sp0 before the
    constant (clang's sp0 + r5 - 0x14u, index_sum) is an index added to sp0 - 20, not an address at
    sp0 that leaves (until 2026-10-23 such a frame stayed raw, every slot of it). A slot accessed with one size but
    both signs (strb, then ldrsb) becomes a variable of the unsigned type, its signed reads cast:
    (s8)s9. A slot that may be read before it is written (what earlier frames left there, a word a
    call through a pointer is handed: the lift check compares such reads apart) stays as it is, the
    others become variables all the same."""
    import copy
    accesses, escapes, escaped = {}, [False], set()

    def visit(e):
        if e is None:
            return
        if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast) and frame_address(e.e.e):
            off = frame_offset(e.e.e)
            pointee = e.e.type.replace("volatile", " ").replace("*", " ").split()
            if off is None or len(pointee) != 1:
                escapes[0] = True
                return
            accesses.setdefault(off, set()).add(pointee[0])
            return
        if frame_address(e):                # a frame address as a value: it leaves (a call's argument)
            off = frame_offset(e)
            if off is None or off >= 0:
                escapes[0] = True
            else:
                escaped.add(off)
            return
        got = index_sum(e)
        if got is not None:                 # (an index added to a frame address, frame_sums: what the code
            escaped.update(got[1])          # computed it from leaves too; clang's sp0 + r5 - 0x14u names
            for _, x in got[2]:             # sp0 alone, which is no address at sp0 that leaves)
                visit(x)
            return
        if isinstance(e, C.Name) and e.id == "sp0":
            escapes[0] = True
            return
        for c in _children(e) if not isinstance(e, C.Assign) else [e.target, e.value]:
            visit(c)

    def visit_stmt(st):
        if isinstance(st, C.Decl):
            exprs = [d.init for d in st.items if d.name != "sp0"]
        elif isinstance(st, C.Asm):
            exprs = [o.e for o in st.outputs + st.inputs]
        else:
            exprs = _stmt_all_exprs(st)
        for e in exprs:
            visit(e)
            frame_sums(e, escaped)          # (an index added to a frame address: it leaves too)
        for inner in _inner(st):
            visit_stmt(inner)
    for st in fn.body.body:
        visit_stmt(st)
    if escapes[0] or not accesses:
        return False
    sizes = SLOT_SIZES
    if escaped:                             # (only the slots wholly below what leaves: region_start)
        low = region_start([(off, max(sizes[t] for t in types)) for off, types in accesses.items()
                            if all(t in sizes for t in types)], min(escaped))
        accesses = {off: types for off, types in accesses.items()
                    if all(t in sizes for t in types) and off + max(sizes[t] for t in types) <= low}
        if not accesses:
            return False
    spans, vtypes = [], {}
    for off, types in accesses.items():
        if not all(t in sizes for t in types) or len({sizes[t] for t in types}) != 1:
            return False
        size = sizes[next(iter(types))]     # (one size, both signs: the unsigned type, signed reads cast)
        vtypes[off] = next(iter(types)) if len(types) == 1 else "u%d" % (8 * size)
        spans.append((off, off + size))
    spans.sort()
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        return False
    before = copy.deepcopy(fn.body.body)
    taken = all_names(fn) | _types_used(fn)
    for prefix in SLOT_PREFIXES:            # (the first under which no slot's name is taken)
        slot_names = {off: ("%s%d" % (prefix, -off) if off < 0 else "%s_%d" % (prefix, off)) for off in accesses}
        if not set(slot_names.values()) & taken:
            break
    else:
        return False

    def slot_of(e):
        if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast) and frame_address(e.e.e):
            off = frame_offset(e.e.e)
            return off if off in names else None
        return None

    def access_type(e):
        return e.e.type.replace("volatile", " ").replace("*", " ").split()[0]

    def rewrite(e):
        if e is None:
            return None
        off = slot_of(e)
        if off is not None:                 # (read with the other sign: the variable cast to it)
            t = access_type(e)
            return C.Name(names[off]) if t == vtypes[off] else C.Cast(t, C.Name(names[off]))
        if isinstance(e, C.Assign):         # (a store: the same bits whatever its sign)
            off = slot_of(e.target)
            return C.Assign(e.op, C.Name(names[off]) if off is not None else rewrite(e.target), rewrite(e.value))
        if isinstance(e, C.Unary):
            return C.Unary(e.op, rewrite(e.e))
        if isinstance(e, C.Cast):
            return C.Cast(e.type, rewrite(e.e))
        if isinstance(e, C.Binary):
            return C.Binary(e.op, rewrite(e.l), rewrite(e.r))
        if isinstance(e, C.Cond):
            return C.Cond(rewrite(e.c), rewrite(e.t), rewrite(e.f))
        if isinstance(e, C.Call):
            return C.Call(rewrite(e.f), [rewrite(a) for a in e.args])
        if isinstance(e, C.Post):
            return C.Post(e.op, rewrite(e.e))
        return e

    def rewrite_stmt(st):
        if isinstance(st, C.ExprStmt):
            st.e = rewrite(st.e)
        elif isinstance(st, C.Return) and st.e is not None:
            st.e = rewrite(st.e)
        elif isinstance(st, C.If):
            st.cond = rewrite(st.cond)
        elif isinstance(st, (C.While, C.DoWhile)):
            st.cond = rewrite(st.cond)
        elif isinstance(st, C.Switch):
            st.e = rewrite(st.e)
        elif isinstance(st, C.Asm):
            for o in st.outputs + st.inputs:
                o.e = rewrite(o.e)
        for inner in _inner(st):
            rewrite_stmt(inner)

    # Slots that stay raw: one changed in place with the other sign (x++, x >>= 1: the value or the
    # bits would differ), and, found below, one that may be read before it is written (what earlier
    # frames left there: the lift check compares such reads apart).
    raw = set()
    for st in fn.body.body:
        for e in _stmt_all_exprs(st) if not isinstance(st, C.Decl) else ():
            for x in walk_expr(e):
                inner = x.e if isinstance(x, C.Post) or (isinstance(x, C.Unary) and x.op in ("++", "--")) else \
                    x.target if isinstance(x, C.Assign) and x.op != "=" else None
                if inner is not None and isinstance(inner, C.Unary) and inner.op == "*" and \
                        isinstance(inner.e, C.Cast) and frame_address(inner.e.e) and \
                        frame_offset(inner.e.e) in vtypes and access_type(inner) != vtypes[frame_offset(inner.e.e)]:
                    raw.add(frame_offset(inner.e.e))
    while True:
        names = {off: n for off, n in slot_names.items() if off not in raw}
        if not names:
            fn.body.body[:] = before
            return False
        for st in fn.body.body:
            if not isinstance(st, C.Decl):
                rewrite_stmt(st)
        decls = [C.Decl(vtypes[off], [C.Declarator(names[off])]) for off in sorted(names)]
        last = max((i for i, x in enumerate(fn.body.body) if isinstance(x, C.Decl)), default=-1)
        fn.body.body[last + 1:last + 1] = decls
        g = Graph(fn)                       # (a slot read where nothing stored it yet: it stays raw)
        reach = g.reaching()
        by_name = {n: off for off, n in names.items()}
        unset = set()
        for n in g.nodes:
            for v in n.reads & set(by_name):
                if -1 in (reach[n.index].get(v) or ()) or any(isinstance(g.nodes[d].stmt, C.Decl)
                                                              for d in (reach[n.index].get(v) or ()) if d >= 0):
                    unset.add(by_name[v])
        if not unset:
            return True
        raw |= unset
        fn.body.body[:] = copy.deepcopy(before)


def _effects_outside(e, name):
    """Whether e has an effect other than through the read of name (calls, volatile accesses,
    assignments to memory), counted in e itself."""
    if isinstance(e, C.Name):
        return False
    if isinstance(e, C.Assign):
        if isinstance(e.target, C.Name):
            return _effects_outside(e.value, name)
        return True
    if (isinstance(e, C.Call) and not is_pure_call(e)) or is_volatile_access(e):
        return True
    if isinstance(e, (C.Post,)) or (isinstance(e, C.Unary) and e.op in ("++", "--")):
        return True
    return any(_effects_outside(c, name) for c in _children(e))


def _maybe_evaluated(e, name, always=True):
    """Whether expression e reads name where it may not be evaluated: in the right operand of && or ||,
    in an arm of ?: (short-circuit evaluation)."""
    if isinstance(e, C.Name):
        return e.id == name and not always
    if isinstance(e, C.Binary) and e.op in ("&&", "||"):
        return _maybe_evaluated(e.l, name, always) or _maybe_evaluated(e.r, name, False)
    if isinstance(e, C.Cond):
        return _maybe_evaluated(e.c, name, always) or _maybe_evaluated(e.t, name, False) or \
            _maybe_evaluated(e.f, name, False)
    if isinstance(e, C.Assign):
        return (not isinstance(e.target, C.Name) and _maybe_evaluated(e.target, name, always)) or \
            _maybe_evaluated(e.value, name, always)
    return any(_maybe_evaluated(c, name, always) for c in _children(e))


def _read_maybe(s, name):
    """Whether statement s reads name where it may not be evaluated (_maybe_evaluated): a value with an
    effect folded there would happen on fewer paths (a load the original made before a conditional
    compare, z = r0 == r5 || r4 == r1)."""
    return any(_maybe_evaluated(get(), name) for get, _ in stmt_exprs(s))


def _sequenced_after(s, name):
    """Whether, in statement s, every effect of s happens after its read of name is evaluated, so that
    an effect folded into that read keeps its place: a store whose value reads it (the store comes
    after its value), a call whose arguments read it, the others pure; a condition, a return, an
    assignment to a variable."""
    e = None
    if isinstance(s, C.ExprStmt):
        e = s.e
    elif isinstance(s, (C.Return, C.Switch)):
        e = s.e
    elif isinstance(s, C.If):
        e = s.cond
    if e is None:
        return False
    if isinstance(e, C.Assign) and e.op == "=":
        value, target = e.value, e.target
        if isinstance(target, C.Name):
            if isinstance(value, C.Call):
                return not any(_effects_outside(a, name) for a in value.args) and isinstance(value.f, C.Name)
            return not _effects_outside(value, name)
        if is_volatile_access(target) or isinstance(target, C.Unary):
            return not _effects_outside(target.e, name) and not _effects_outside(value, name) and \
                reads_count(target, name) == 0
        return False
    if isinstance(e, C.Call):
        return not any(_effects_outside(a, name) for a in e.args) and isinstance(e.f, C.Name)
    return not _effects_outside(e, name)


def plain_view(fn):
    """fn as flat_view's stand-in with nothing spread: the body's statements, each at home there."""
    import types
    return types.SimpleNamespace(name=fn.name, params=fn.params, head=fn.head, body=fn.body, pairs=[],
                                 homes={id(s): fn.body.body for s in fn.body.body})


def fold(fn):
    """A temporary written once and read once (or a pure one read several times by one condition),
    on one straight path behind its write, into its reader: first on the body's statements (a path
    passes over a conditional block as over one statement), then with the blocks spread out
    (flat_view: an IT block's load and the store of what it loaded, in one arm of the merged if; a
    condition whose arms read the variable only after writing it). Folds that touch disjoint
    stretches of the body are made together; the next round sees what they changed."""
    changed = False
    for view_of in (plain_view, flat_view):
        changed = _fold(fn, view_of) or changed
    return changed


def _fold(fn, view_of):
    changed = False
    while True:
        view = view_of(fn)
        g = Graph(view)
        reach = g.reaching()
        readers = {}
        for m in g.nodes:
            for v in m.reads:
                readers.setdefault(v, []).append(m)
        taken, plans = set(), []
        for n in g.nodes:
            s = n.stmt
            if not is_assign(s) or n.index + 1 >= len(g.nodes) or id(s) not in view.homes:
                continue
            v = s.e.target.id
            if v not in g.scope:
                continue
            uses = [m for m in readers.get(v, ()) if n.index in (reach[m.index].get(v) or ())]
            if len(uses) != 1 or reach[uses[0].index].get(v) != {n.index}:
                continue
            nxt = uses[0]
            if nxt.index <= n.index or not foldable_into(nxt.stmt, v) or \
                    not _straight(g, n.index, nxt.index, s.e.value):
                continue
            value = s.e.value
            if isinstance(value, C.Call) and not is_pure_call(value) and _in_call_argument(nxt.stmt, v):
                continue                        # (a call into another call's arguments: kept a statement)
            many = reads_count_stmt(nxt.stmt, v)
            if many != 1 and not (many > 1 and isinstance(nxt.stmt, C.If) and not has_effects(value)
                                  and not _reads_memory(value) and size(value) <= 24):
                continue                        # (a condition may read a compare's result twice: bls)
            if reads_count(value, v):                   # (v = v + 4: the old v is gone with it)
                continue
            if has_effects(value) and (not _sequenced_after(nxt.stmt, v) or _read_maybe(nxt.stmt, v)):
                continue                        # (an effect keeps its place, and happens on every path)
            if any(_writes_name(nxt.stmt, w) for w in _reads_of(value)) and not _reads_before_writes(nxt.stmt, v):
                continue
            stretch = set(range(n.index, nxt.index + 1))
            if stretch & taken:
                continue
            taken |= stretch
            plans.append((n.index, nxt.index, v, value))
        if not plans:
            return changed
        for d, u, v, value in plans:
            reader = g.nodes[u].stmt
            calls = isinstance(reader, (C.ExprStmt, C.Return)) and _calls(value) and not any(
                _calls(get()) for get, _ in stmt_exprs(reader))
            for get, put in stmt_exprs(reader):
                if reads_count(get(), v):
                    put(substitute(get(), v, value))
            if reader.addr is None:
                reader.addr = g.nodes[d].stmt.addr
            else:
                (C.take if calls else C.absorb)(reader, g.nodes[d].stmt)
        write_back(view)                    # (a reader that is a stand-in's condition)
        for d, _, _, _ in plans:
            replace(view, g.nodes[d].stmt)
        changed = True


def _calls(e):
    """Whether expression e makes a call that is not pure (its value's instruction is the call's)."""
    return e is not None and any(isinstance(x, C.Call) and not is_pure_call(x) for x in walk_expr(e))


def spread(fn):
    """A pure value (a compare's flag) read only by the conditions of a few statements on one straight
    path behind it, nothing between writing what it reads: written into each of those conditions
    (conditions then makes comparisons of them). The carry an IT block's two conditional moves test."""
    g = Graph(fn)
    reach = g.reaching()
    readers = {}
    for m in g.nodes:
        for v in m.reads:
            readers.setdefault(v, []).append(m)
    for n in g.nodes:
        s = n.stmt
        if not is_assign(s) or s.e.target.id not in g.scope:
            continue
        v, value = s.e.target.id, s.e.value
        if has_effects(value) or _reads_memory(value) or size(value) > 24 or reads_count(value, v):
            continue
        uses = [m for m in readers.get(v, ()) if n.index in (reach[m.index].get(v) or ())]
        if len(uses) < 2 or len(uses) > 3 or any(reach[m.index].get(v) != {n.index} for m in uses):
            continue
        if not all(isinstance(m.stmt, C.If) and reads_count(m.stmt.cond, v) and
                   _reads_in_cond_only(m.stmt, v) for m in uses):
            continue
        last = max(m.index for m in uses)
        if last <= n.index or not _path_clear(g, n.index, last, _reads_of(value) | {v}, uses):
            continue
        for m in uses:
            m.stmt.cond = substitute(m.stmt.cond, v, value)
            C.absorb(m.stmt, s)
        del fn.body.body[n.index]
        return True
    return False


def _offset_base(e):
    """The variable of e when e is a variable plus or minus constants ((sp0 - 16) + 0x0u, as the
    lifters write add r7, sp, #0; the frame's sp0 too: its accesses then are frame slots the frame
    passes see), else None."""
    if isinstance(e, C.Cast) and e.type == "u32":
        e = e.e
    if isinstance(e, C.Binary) and e.op in ("+", "-") and isinstance(e.r, C.Num) and isinstance(e.r.value, int):
        e = e.l
        if isinstance(e, C.Cast) and e.type == "u32":
            e = e.e
        return e.id if isinstance(e, C.Name) else _offset_base(e)
    return None


def _offset_of_variable(e):
    """Whether e is a variable plus or minus a constant (_offset_base)."""
    return _offset_base(e) is not None


def _fold_offsets(e):
    """e with (x + c1) + c2 written x + (c1 + c2) (minus as a negative constant) throughout, so that
    an access through a substituted address has one constant offset from its variable."""
    if isinstance(e, C.Binary):
        left, right = _fold_offsets(e.l), _fold_offsets(e.r)
        if e.op in ("+", "-") and isinstance(right, C.Num) and isinstance(right.value, int) and \
                isinstance(left, C.Binary) and left.op in ("+", "-") and isinstance(left.r, C.Num) and \
                isinstance(left.r.value, int):
            k = (left.r.value if left.op == "+" else -left.r.value) + (right.value if e.op == "+" else -right.value)
            if k == 0:
                return left.l
            return C.Binary("+" if k > 0 else "-", left.l, C.Num("%d" % abs(k)))
        e.l, e.r = left, right
        return e
    for slot in type(e).__slots__:
        v = getattr(e, slot, None)
        if isinstance(v, C.Expr):
            setattr(e, slot, _fold_offsets(v))
        elif isinstance(v, list) and any(isinstance(x, C.Expr) for x in v):
            setattr(e, slot, [_fold_offsets(x) if isinstance(x, C.Expr) else x for x in v])
    return e


def _reads_everywhere(s, name):
    """The reads of name in statement s and every statement inside it."""
    n = sum(reads_count(e, name) for e in _stmt_all_exprs(s) if e is not None)
    return n + sum(_reads_everywhere(inner, name) for inner in _inner(s))


FRAME_BASE = "sp0"          # the lifters' address of the frame's end, declared once and never written


def addresses(fn):
    """An address computed once from a variable (the variable plus or minus a constant: the base the
    lifters give an ldrd, ldm or stm, a_1 = r0 + 4) and read by a few statements on one straight path
    behind it, nothing on the way writing that variable: written into each of them, so that an access
    through it reads as one through the variable (r0 + 8 once the constants are folded), which the
    types write as a member (r0->f_8). A frame base (sp0 plus or minus a constant: GCC -O0's frame
    pointer, r7 = sp0 - 16) is written into every use it alone reaches, on any path and however many:
    sp0 is never written, so the value is the same wherever it arrives, and each use becomes a frame
    slot's address (r7 + 4 is sp0 - 12), which frame_locals and dread.frames see."""
    g = Graph(fn)
    reach = g.reaching()
    readers = {}
    for m in g.nodes:
        for v in m.reads:
            readers.setdefault(v, []).append(m)
    writes = [m for m in g.nodes if FRAME_BASE in m.writes]  # (its declaration alone: then it never changes)
    sp0_fixed = len(writes) == 1 and isinstance(writes[0].stmt, C.Decl)
    for n in g.nodes:
        s = n.stmt
        if not is_assign(s) or s.e.target.id not in g.scope:
            continue
        v, value = s.e.target.id, s.e.value
        base = _offset_base(value)
        if base is None or reads_count(value, v):
            continue
        frame = base == FRAME_BASE and sp0_fixed
        uses = [m for m in readers.get(v, ()) if n.index in (reach[m.index].get(v) or ())]
        if not uses or (len(uses) > 8 and not frame) or any(reach[m.index].get(v) != {n.index} or
                                                             m.index <= n.index for m in uses):
            continue
        if any(_reads_everywhere(m.stmt, v) != reads_count_stmt(m.stmt, v) for m in uses):
            continue                        # (a read the substitution would not reach: an arm's, an asm's)
        last = max(m.index for m in uses)
        if not frame and not _path_clear(g, n.index, last, _reads_of(value) | {v}, uses):
            continue
        for m in uses:
            for get, put in stmt_exprs(m.stmt):
                if reads_count(get(), v):
                    put(_fold_offsets(substitute(get(), v, value)))
            C.absorb(m.stmt, s)
        del fn.body.body[n.index]
        return True
    return False


def _in_call_argument(s, v):
    """Whether statement s reads v as (part of) an argument of a call."""
    for get, _ in stmt_exprs(s):
        for x in walk_expr(get()):
            if isinstance(x, C.Call) and any(reads_count(a, v) for a in x.args):
                return True
    return False


def _reads_in_cond_only(s, v):
    """Whether an if reads v in its condition only (its arms do not)."""
    from dread.cflow import _reads_of as reads
    inner = set()
    for arm in (s.then, s.orelse):
        if arm is not None:
            for st in _flat(arm):           # (nested blocks too)
                for e in _stmt_all_exprs(st):
                    inner |= reads(e)
                if isinstance(st, C.Asm):
                    for o in st.outputs + st.inputs:
                        inner |= reads(o.e)
    return v not in inner


def _path_clear(g, start, end, names, uses):
    """Control goes from start to end along one path, and nothing on it (the uses included: their
    arms) writes names."""
    k = start
    while k < end:
        node = g.nodes[k]
        if node.succ != [k + 1] and not (isinstance(node.stmt, C.If) and not isinstance(node.stmt.then, (C.Goto, C.Return))
                                        and set(node.succ) == {k + 1}):
            return False
        k += 1
        between = g.nodes[k]
        if between.pred != [k - 1] or between.writes & names or isinstance(between.stmt, (C.Label, C.Case)):
            return False
    return True


def _straight(g, start, end, value):
    """Whether control goes from node start to node end along one path with no other way in, and the
    nodes between write nothing value reads and, when value has an effect or reads memory, have none."""
    reads = _reads_of(value)
    effect = has_effects(value) or _reads_memory(value)
    k = start
    while k != end:
        node = g.nodes[k]
        if node.succ != [k + 1] or k + 1 >= len(g.nodes) or g.nodes[k + 1].pred != [k]:
            return False
        k += 1
        if k == end:
            return True
        between = g.nodes[k]
        if between.writes & reads or (effect and between.effects) or isinstance(between.stmt, (C.Label, C.Case)):
            return False
    return True


def _reads_memory(e):
    for x in walk_expr(e):
        if isinstance(x, C.Unary) and x.op == "*":
            return True
        if isinstance(x, (C.Index, C.Member)) or (isinstance(x, C.Call) and not is_pure_call(x)):
            return True
    return False


def _reads_before_writes(s, name):
    """For x = f(name): the read is evaluated before the write of x."""
    return isinstance(s, C.ExprStmt) and isinstance(s.e, C.Assign) and isinstance(s.e.target, C.Name)


def tidy_declarations(fn):
    """Declarations of variables nobody reads or writes go; an initial value nobody reads goes too."""
    g = Graph(fn)
    live = g.live()
    used = set()
    for n in g.nodes:
        if not isinstance(n.stmt, C.Decl):
            used |= n.reads | n.writes
    for n in g.nodes:
        if isinstance(n.stmt, C.Decl):
            for d in n.stmt.items:
                if d.init is not None:
                    used |= _reads_of(d.init)
    changed = False
    out = []
    for n in g.nodes:
        s = n.stmt
        if isinstance(s, C.Decl):
            keep = []
            for d in s.items:
                if d.name not in used and d.name in g.scope and (d.init is None or not has_effects(d.init)):
                    changed = True
                    continue
                if d.init is not None and d.name not in live[n.index] and not has_effects(d.init) and not d.dims:
                    d.init = None
                    changed = True
                keep.append(d)
            if not keep:
                continue
            s.items = keep
        out.append(s)
    fn.body.body[:] = out
    return changed


LIFTER_REGISTER = re.compile(r"^(r\d+|p\d+|sp0)(_\d+)?$")   # MSP430's registers, parameters, frame address


def context(fn):
    """cexpr's context for a function: its variables' types, int's width (16 bits where the registers
    are: MSP430's lifted C has u16 registers and int is 16 bits there). Only the lifter's own
    variables say so (a parameter, a register r4 or its webs' r4_1, sp0): a u16 variable of the
    readable C's (a 16-bit stack slot of an ARM function) made a whole function 16-bit, and the
    conditions pass then proved a wrong comparison ((u32)(s16)x != 12 as x >= 0, ~12 being 0xfff3 in
    16 bits)."""
    ctx = cexpr.Ctx()
    narrow = any(p[0] == "u16" for p in fn.params) or any(
        isinstance(x, C.Decl) and x.specs == "u16" and any(LIFTER_REGISTER.match(d.name) for d in x.items)
        for x in fn.body.body)
    if narrow:
        ctx.int_bits, ctx.long_bits = 16, 32
    decls = declarations(fn)                # (once: per name it made context quadratic in the body)
    for name in all_names(fn):
        t = type_of(fn, name, decls)
        ty = ctx.ctype(t) if t else None
        if ty is not None:
            ctx.types[name] = ty
    return ctx


def size(e):
    return sum(1 for _ in walk_expr(e))


def operands(e):
    """The distinct operands of an expression, as the instruction it came from had them: variables,
    memory accesses, calls and constants (the arithmetic's own 0 and 1 among them: cmp r3, #1)."""
    out, seen = [], set()

    def visit(x):
        if isinstance(x, (C.Name, C.Call, C.Index, C.Member)) or (isinstance(x, C.Unary) and x.op in ("*", "&")) or \
                (isinstance(x, C.Num) and not isinstance(x.value, float)):
            key = C.expr_text(x)
            if key not in seen:
                seen.add(key)
                out.append(x)
            return
        for c in _children(x):
            visit(c)
    visit(e)
    return out


def signed_cast(e, ctx):
    bits = ctx.types.get(e.id, (ctx.int_bits, False))[0] if isinstance(e, C.Name) else 32
    return "s%d" % bits


def as_signed(e, ctx, bits):
    if isinstance(e, C.Num):
        v = e.value & ((1 << bits) - 1)
        if v >> (bits - 1):
            return C.Unary("-", C.Num(str((1 << bits) - v)))
        return C.Num(str(v))
    return C.Cast("s%d" % bits, e)


def subexpressions(e):
    """The distinct proper subexpressions of e that may be an operand of the comparison e stands for:
    no comparison or logical operation, nothing that goes through 64 bits (the lifters' carry and
    overflow arithmetic), smallest first."""
    out, seen = [], set()
    for x in walk_expr(e):
        if x is e or isinstance(x, (C.Cond, C.Assign)):
            continue
        if isinstance(x, C.Binary) and x.op in ("==", "!=", "<", "<=", ">", ">=", "&&", "||"):
            continue
        if isinstance(x, C.Unary) and x.op == "!":
            continue
        if any(isinstance(y, C.Cast) and y.type in ("u64", "s64") for y in walk_expr(x)):
            continue
        key = C.expr_text(x)
        if key not in seen:
            seen.add(key)
            out.append(x)
    out.sort(key=size)
    return out


def compared(e):
    """[(x, y)] of the lifters' compares in e, (u64)x + (u32)~y + 1 (cmp x, y: x - y with its carry),
    whatever x and y are (cmp r0, r5 after adds r5, r2, #3: r0 against r2 + 3, no leaf of e)."""
    out = []
    for x in walk_expr(e):
        if isinstance(x, C.Binary) and x.op == "+" and isinstance(x.r, C.Num) and x.r.value == 1 and \
                isinstance(x.l, C.Binary) and x.l.op == "+" and isinstance(x.l.l, C.Cast) and x.l.l.type == "u64" and \
                isinstance(x.l.r, C.Cast) and x.l.r.type == "u32" and isinstance(x.l.r.e, C.Unary) and \
                x.l.r.e.op == "~" and not any(isinstance(y, C.Cast) and y.type in ("u64", "s64")
                                              for y in walk_expr(x.l.l.e)):
            out.append((x.l.l.e, x.l.r.e.e))
    return out


def _sum_terms(e):
    """The terms of a sum (a + b + c as [a, b, c]), or [e]."""
    if isinstance(e, C.Binary) and e.op == "+":
        return _sum_terms(e.l) + _sum_terms(e.r)
    return [e]


def _is_carry(x):
    """Whether x is a carry the lifters add in: (u32)(t >> 32), or a comparison (a carry the flags
    pass has written as one, r3 >= 5)."""
    return (isinstance(x, C.Cast) and x.type == "u32" and isinstance(x.e, C.Binary) and x.e.op == ">>" and
            isinstance(x.e.r, C.Num) and x.e.r.value == 32) or (isinstance(x, C.Binary) and x.op in COMPARISONS)


def _word_sum(e):
    """(a, b, kind, k) of the lifters' flag-setting addition or subtraction through 64 bits: (u64)a + b
    + k is ("add"), (u64)a + (u32)~b + k ("sub"); b None where it is a constant (then summed into k for
    an addition) or left out; k the constants' sum (a Num) or a carry term (_is_carry: the carry of
    another such sum). None for anything else."""
    terms = _sum_terms(e)
    if not terms or not (isinstance(terms[0], C.Cast) and terms[0].type == "u64"):
        return None
    a, rest = terms[0].e, terms[1:]
    carries = [x for x in rest if _is_carry(x)]
    nums = [x for x in rest if isinstance(x, C.Num) and isinstance(x.value, int)]
    others = [x for x in rest if not any(x is y for y in carries + nums)]
    if len(others) > 1 or len(carries) > 1 or (carries and nums and sum(x.value for x in nums)):
        return None
    k = carries[0] if carries else C.Num(str(sum(x.value for x in nums)))
    if not others:
        return a, None, "add", k
    b = others[0]
    if isinstance(b, C.Cast) and b.type == "u32" and isinstance(b.e, C.Unary) and b.e.op == "~":
        return a, b.e.e, "sub", k
    return a, b, "add", k


def wide_pair(hi, lo):
    """(u64)hi << 32 | lo, a constant where both are."""
    if isinstance(hi, C.Num) and isinstance(lo, C.Num) and isinstance(hi.value, int) and isinstance(lo.value, int):
        v = ((hi.value & 0xffffffff) << 32) | (lo.value & 0xffffffff)
        return C.Num("%d" % v if v < 10 else "0x%xull" % v)
    return C.Binary("|", C.Binary("<<", C.Cast("u64", hi), C.Num("32")), lo)


def chained(e):
    """[(x, y)] of the lifters' two-word compares in e (cmp a0, b0; sbcs a1, b1: the high words'
    subtraction takes the low words' carry, (u64)a1 + (u32)~b1 + (u32)(((u64)a0 + (u32)~b0 + 1) >> 32)),
    as the 64-bit values compared ((u64)a1 << 32 | a0 against (u64)b1 << 32 | b0); additions alike."""
    out = []
    for x in walk_expr(e):
        if not (isinstance(x, C.Binary) and x.op == "+"):
            continue
        high = _word_sum(x)
        if high is None or isinstance(high[3], C.Num):
            continue
        if isinstance(high[3], C.Binary) and high[3].op == ">=" and high[2] == "sub":
            low = (high[3].l, high[3].r, "sub", C.Num("1"))      # (the low words' carry already a comparison)
        else:
            low = _word_sum(high[3].e.l) if isinstance(high[3], C.Cast) and isinstance(high[3].e, C.Binary) else None
        if low is None or low[2] != high[2] or not isinstance(low[3], C.Num) or \
                low[3].value != (1 if low[2] == "sub" else 0):
            continue
        zero = C.Num("0")
        out.append((wide_pair(high[0], low[0]), wide_pair(high[1] if high[1] is not None else zero,
                                                           low[1] if low[1] is not None else zero)))
    return out


def candidates(e, ctx, most=10):
    """Simple conditions to try for e, smallest first: two of its operands compared (equality, then
    unsigned, then signed order), an operand of it against one of its constants or 0, a mask test.
    Operands: its subexpressions (subexpressions), the most smallest ones. Each made of copies of e's
    subexpressions (made_candidate)."""
    return [made_candidate(recipe, ctx) for _, _, recipe in shared_candidates(e, ctx, most)]


def made_candidate(recipe, ctx):
    """The candidate of a recipe of shared_candidates, made of copies of e's subexpressions."""
    import copy
    kind, op, a, b, bits = recipe
    if kind == "signed":
        return C.Binary(op, as_signed(copy.deepcopy(a), ctx, bits), as_signed(copy.deepcopy(b), ctx, bits))
    if kind == "mask":
        return C.Binary(op, C.Binary("&", copy.deepcopy(a), copy.deepcopy(b)), C.Num("0"))
    return C.Binary(op, copy.deepcopy(a), copy.deepcopy(b))


def shared_candidates(e, ctx, most=10, below=None, terms=(), numbers=()):
    """[(size, candidate, recipe)] of candidates(), in its order; each candidate shares e's
    subexpressions (to try, not to keep: made_candidate makes the one to keep of its recipe), its
    size counted, not walked. below: only those of fewer nodes. Until 2026-10-23 every candidate was
    made of copies and walked for its size, most of them larger than e and never tried: the copies
    alone were a fifth of the readable C's time on FreeRTOS's GCC -Os demo. terms: operands beyond
    e's own (the flags pass: a variable that holds the 32 bits of a sum whose carry e tests), compared
    with e's leaves and constants, after e's own; numbers: constants beyond e's (the flags pass: what a
    chain of subtractions comes to, x - 1 - 2 == 5 as x == 8)."""
    subs = subexpressions(e)
    # An address a memory access is made at is no operand to compare with (gizmo's uart_putc waited
    # while io_40011004 << 31 < 0x40011004u: true, but the bound was the register's own address).
    access = [x for x in walk_expr(e) if isinstance(x, C.Unary) and x.op == "*" and isinstance(x.e, C.Cast)
              and isinstance(x.e.e, C.Num)]
    at = {id(x.e.e) for x in access}
    addresses = {C.expr_text(x.e.e) for x in access} - {
        C.expr_text(x) for x in walk_expr(e) if isinstance(x, C.Num) and id(x) not in at}
    consts = [x for x in subs if isinstance(x, C.Num) and C.expr_text(x) not in addresses]
    if not any(isinstance(x, C.Num) and x.value == 0 for x in consts):
        consts.append(C.Num("0"))
    seen = {x.value for x in consts if isinstance(x.value, int)}
    for k in sorted(seen):                  # (x + 1 == 0 is x == 0xffffffff: the negated constant, and
        for d in ((-k) & 0xffffffff, (~k) & 0xffffffff):     # the inverted one)
            if d not in seen:
                seen.add(d)
                consts.append(C.Num("0x%xu" % d if d > 9 else "%du" % d))
    for x in walk_expr(e):                  # (x << 31's sign is x's bit 0: the bit a shift moves there)
        if isinstance(x, C.Binary) and x.op == "<<" and isinstance(x.r, C.Num) and isinstance(x.r.value, int):
            t = type_of_expr(x.l, ctx)
            bits = t[0] if t else ctx.int_bits
            if 0 <= x.r.value < bits:
                d = 1 << (bits - 1 - x.r.value)
                if d not in seen:
                    seen.add(d)
                    consts.append(C.Num("0x%xu" % d if d > 9 else "%du" % d))
    for d in numbers:
        if d not in seen:
            seen.add(d)
            consts.append(C.Num("0x%xu" % d if d > 9 else "%du" % d))
    own = [x for x in subs if not isinstance(x, C.Num)][:most]
    leaves = [x for x in own if isinstance(x, C.Name) or (isinstance(x, C.Unary) and x.op == "*")] + list(terms)
    pairs = compared(e) + chained(e) + [(a, b) for a in own + list(terms) for b in consts] + [
        (a, b) for a in leaves for b in leaves if a is not b]
    sizes = {}                              # id -> size, of e's subexpressions and consts (alive meanwhile)

    def sz(x):
        if id(x) not in sizes:
            sizes[id(x)] = size(x)
        return sizes[id(x)]

    def signed_size(x, y):                  # (y = as_signed(x): a cast of x, or a constant of its own)
        return 1 + sz(x) if isinstance(y, C.Cast) and y.e is x else size(y)

    def bits_of(x):
        t = type_of_expr(x, ctx)
        return t[0] if t else (width(x, ctx) or ctx.int_bits)
    zero = C.Num("0")

    def unsigned(x):
        t = type_of_expr(x, ctx) or c_type(x, ctx)
        return t is not None and not t[1]
    out = []
    for a, b in pairs:
        n = 1 + sz(a) + sz(b)
        if isinstance(b, C.Num) and b.value == 1 and unsigned(a):
            for op in ("==", "!="):         # (x < 1 of an unsigned x: x == 0, tried first; a borrow of x - 1)
                out.append((n - sz(b) + 1, C.Binary(op, a, zero), ("plain", op, a, zero, None)))
        for op in ("==", "!=", "<", ">=", ">", "<="):
            out.append((n, C.Binary(op, a, b), ("plain", op, a, b, None)))
        bits = bits_of(a)
        sa, sb = as_signed(a, ctx, bits), as_signed(b, ctx, bits)
        n_signed = 1 + signed_size(a, sa) + signed_size(b, sb)
        for op in ("<", ">=", ">", "<="):
            out.append((n_signed, C.Binary(op, sa, sb), ("signed", op, a, b, bits)))
        if isinstance(b, C.Num) and b.value != 0:
            for op in ("==", "!="):
                out.append((n + 2, C.Binary(op, C.Binary("&", a, b), C.Num("0")), ("mask", op, a, b, None)))
    if below is not None:
        out = [c for c in out if c[0] < below]
    out.sort(key=lambda c: c[0])            # (stable: candidates of one size in the order made)
    return out


def with_arms(stmts):
    """The statements, each followed by those in its arms where it is a conditional block (if (c) {
    ... } else { ... }), at any depth."""
    for s in stmts:
        yield s
        if isinstance(s, C.If):
            for arm in (s.then, s.orelse):
                if isinstance(arm, C.Block):
                    yield from with_arms(arm.body)


COMPARISONS = ("==", "!=", "<", "<=", ">", ">=", "&&", "||")


def truth_value(e):
    """Whether e is a comparison or a logical operation at its top: its value is 0 or 1, so a
    comparison of the same truth has the same value."""
    return (isinstance(e, C.Binary) and e.op in COMPARISONS) or (isinstance(e, C.Unary) and e.op == "!")


_RUN = threading.local()    # the tidy run under way in this thread: {condition's key: its answer} (conditions)


def conditions(fn):
    """A branch's condition that the lifter computed from flags (a compare's subtraction with its
    carry and overflow) as the comparison it is, when the evaluator (dread.cexpr) finds one that
    gives the same truth on boundary and random values of its operands. Also every truth value in a
    statement: a flag's own value (z = (u32)((u64)r0 + (u32)~r3 + 1) == 0 as z = r0 == r3: a flag an
    IT block's ifs read in turn, or one read across other statements, stays a variable), the operands
    of && || ! where the whole has no smaller form (z = r0 == r5 || r4 == r1 from a conditional
    compare), a select's condition wherever the select is (in a return's value, a call's argument, a
    value stored; until 2026-10-23 not in calls and stores: prvCheckExpectedState(!(u32)(((u64)r7 +
    (u32)~r0_1 + 1) >> 32) ? 0x0u : 0x1u) on FreeRTOS GCC -Os). Within one tidy run a
    condition asked again (every round of the passes asks them all) gets the answer it got, by what the
    search reads of it: its text, its variables' types, the widths of int and long, whether z3 is there
    (until 2026-10-23 each round searched again: two thirds of the searches for FreeRTOS's GCC -Os
    demo were of a condition the same run had searched before). A proof that ran out of time
    (cexpr.PROOF_MS) so stays a no for the rest of the run."""
    import copy
    ctx = context(fn)
    changed = False

    def simpler(cond):
        limit = size(cond)
        if limit <= 3:
            return None
        names = sorted({x.id for x in walk_expr(cond) if isinstance(x, C.Name)})
        key = (C.expr_text(cond), tuple((n, ctx.types.get(n)) for n in names), ctx.int_bits, ctx.long_bits,
               cexpr.z3 is not None)
        memo = getattr(_RUN, "memo", None)
        if memo is not None and key in memo:
            got = memo[key]
            return copy.deepcopy(got) if got is not None else None
        got = _simpler(cond, limit)
        if memo is not None:
            memo[key] = copy.deepcopy(got) if got is not None else None
        return got

    def _simpler(cond, limit):
        loads = loads_once(cond)
        if loads is None or cexpr.z3 is None:
            return None
        check = None                        # (made for the first candidate that reads cond's loads)
        for _, cand, recipe in shared_candidates(cond, ctx, below=limit):
            if loads_once(cand) != loads:   # (same_loads)
                continue
            if check is None:
                check = cexpr.Checker(cond, ctx, as_bool=True)
            if check.stands_for(cand):
                return made_candidate(recipe, ctx)
        return None

    def truths(e, cond=False):
        """e with its truth values as small as proved: e itself where it is a condition (cond) or a
        truth value; else, or when nothing smaller stands for it, its parts."""
        if cond or truth_value(e):
            cand = simpler(e)
            if cand is not None:
                return cand
        if isinstance(e, C.Binary) and e.op in ("&&", "||"):
            return C.Binary(e.op, truths(e.l, True), truths(e.r, True))
        if isinstance(e, C.Unary) and e.op == "!":
            return C.Unary("!", truths(e.e, True))
        if isinstance(e, C.Cond):
            return C.Cond(truths(e.c, True), truths(e.t), truths(e.f))
        if isinstance(e, C.Binary):
            return C.Binary(e.op, truths(e.l), truths(e.r))
        if isinstance(e, C.Cast):
            return C.Cast(e.type, truths(e.e))
        if isinstance(e, C.Unary) and e.op not in ("*", "&"):
            return C.Unary(e.op, truths(e.e))
        if isinstance(e, C.Call):                   # (a select or a comparison passed on)
            return C.Call(e.f, [truths(a) for a in e.args])
        if isinstance(e, C.Assign):                 # (one stored)
            return C.Assign(e.op, e.target, truths(e.value))
        return e
    for s in with_arms(fn.body.body):
        if isinstance(s, C.If):                     # (a branch's, a conditional block's)
            parts = [(lambda s=s: s.cond, lambda v, s=s: setattr(s, "cond", v), True)]
        elif is_assign(s):                          # (a select's, a flag's: 0 or 1 either way)
            parts = [(lambda s=s: s.e.value, lambda v, s=s: setattr(s.e, "value", v), False)]
        elif isinstance(s, C.ExprStmt):             # (a call's arguments, a store's value)
            parts = [(lambda s=s: s.e, lambda v, s=s: setattr(s, "e", v), False)]
        elif isinstance(s, C.Return) and s.e is not None:
            parts = [(lambda s=s: s.e, lambda v, s=s: setattr(s, "e", v), False)]
        else:
            continue
        for get, put, cond in parts:
            before = C.expr_text(get())
            after = truths(get(), cond)
            if C.expr_text(after) != before:
                put(after)
                changed = True
    return changed


# --- flags read through variables: a compare's flags read by several statements, an add's carry ---

FLAG = re.compile(r"^[nzcv](?:_\d+)?$")    # the lifters' flags (and the webs' parts of them, z_1)
EXPAND_NODES = 160      # the most nodes of a truth value with its variables written as their values
EXPAND_VARIABLES = 12   # the most variables written into one truth value (a compare's flags and its
                        # temporary: three; a chain of subtractions: two a step)
EXTRA_NUMBERS = 8       # the most constants a chain of subtractions adds to a search (flag_numbers)


def _wide(ctx, name):
    t = ctx.types.get(name)
    return t is not None and t[0] > 32


def _pure_but_loads(e):
    """Whether e's only effects are loads (no call, no assignment, no increment): its value may stand
    where it is known to be the same in a proof, the loads as what they loaded there; it is never
    evaluated anew there (a candidate that reads such a load is not taken)."""
    for x in walk_expr(e):
        if isinstance(x, C.Call) and not is_pure_call(x):
            return False
        if isinstance(x, (C.Assign, C.Post)) or (isinstance(x, C.Unary) and x.op in ("++", "--")):
            return False
    return True


def _loads(e):
    """The texts of e's memory reads."""
    return [C.expr_text(x) for x in walk_expr(e) if (isinstance(x, C.Unary) and x.op == "*") or
            isinstance(x, (C.Index, C.Member))]


def offset_form(e):
    """(variable, k) when e's 32 bits are one variable plus the constant k (mod 2**32): r0 - 1, the
    lifters' (u32)((u64)r0 + (u32)~0x2u + 1), a chain of them; else None."""
    def lin(x):
        if isinstance(x, C.Num):
            return None if isinstance(x.value, float) else (None, x.value)
        if isinstance(x, C.Name):
            return (x.id, 0)
        if isinstance(x, C.Cast):
            t = cexpr.Ctx().ctype(x.type)
            return lin(x.e) if t is not None and t[0] >= 32 else False
        if isinstance(x, C.Unary) and x.op in ("~", "-"):
            inner = lin(x.e)
            if not inner or inner[0] is not None:
                return False
            return (None, ~inner[1] if x.op == "~" else -inner[1])
        if isinstance(x, C.Binary) and x.op in ("+", "-"):
            a, b = lin(x.l), lin(x.r)
            if not a or not b or b[0] is not None and (x.op == "-" or a[0] is not None):
                return False
            if x.op == "-":
                return (a[0], a[1] - b[1])
            return (a[0] or b[0], a[1] + b[1])
        return False
    got = lin(e)
    if not got or got[0] is None:
        return None
    return got[0], got[1] & 0xffffffff


def flag_numbers(e):
    """Constants beyond e's own that a comparison in e may hold for a variable it reads plus a constant:
    m - k for each constant m of e (and 0) and each k a subexpression adds to one variable
    (offset_form), so that a chain of subtractions compares its first variable (x - 1 - 2 == 5 as
    x == 8; subs r0, #1; beq; subs r0, #2; beq as r0 == 1, r0 == 3). At most EXTRA_NUMBERS, those of
    the outermost sums (a whole chain's) first."""
    ks = sorted({x.value & 0xffffffff for x in walk_expr(e) if isinstance(x, C.Num) and isinstance(x.value, int)} | {0})
    offs = []
    for x in walk_expr(e):
        got = offset_form(x) if not isinstance(x, (C.Name, C.Num)) else None
        if got and got[1] and got[1] not in offs:
            offs.append(got[1])
    out = []
    for k in offs:
        for m in ks:
            d = (m - k) & 0xffffffff
            if d not in out and d not in ks:
                out.append(d)
    return out[:EXTRA_NUMBERS]


class FlagFlow:
    """What the flags pass knows of a flat_view's flow: its graph, the reaching definitions, and for a
    variable read at a place the one definition of it that reaches there, its value still what it was
    (definition). A place is a node, where its statement reads, or just after it (after: what the
    node writes counts). A variable's definition reaches a place alone when every way to it passes the
    definition and nothing on the way writes the variable; its value is the same there when nothing on
    a way from the definition to the place (not through the definition again) writes what it reads
    (region)."""

    def __init__(self, view, ctx):
        self.g = Graph(view)
        self.reach = self.g.reaching()
        self.ctx = ctx
        self.regions = {}
        self.casts = {}                     # wide variable -> [(node, variable)] of variable = (u32)wide
        for node in self.g.nodes:
            s = node.stmt
            if is_assign(s) and isinstance(s.e.value, C.Cast) and s.e.value.type == "u32" and \
                    isinstance(s.e.value.e, C.Name) and _wide(ctx, s.e.value.e.id):
                self.casts.setdefault(s.e.value.e.id, []).append((node.index, s.e.target.id))
        self.arms = {}                      # node -> (its conditional block's stand-in, "then" or "else")
        index = {id(x): i for i, x in enumerate(self.g.body)}
        for st, _ in getattr(view, "pairs", ()):            # (outer blocks first: an inner one overwrites)
            m = index.get(id(st))
            number = st.then.label.rsplit("_", 1)[1] if m is not None else None
            if number is None or "flat_end_%s" % number not in self.g.labels:
                continue
            then_i, end_i = self.g.labels[st.then.label], self.g.labels["flat_end_%s" % number]
            else_i = self.g.labels.get("flat_else_%s" % number)
            for k in range(then_i + 1, else_i if else_i is not None else end_i):
                self.arms[k] = (m, "then")
            if else_i is not None:
                for k in range(else_i + 1, end_i):
                    self.arms[k] = (m, "else")

    def reaches_avoiding(self, a, n, m):
        """Whether a way leads from node a to node n that does not pass node m."""
        seen, stack = set(), [k for k in self.g.nodes[a].succ if k < len(self.g.nodes)]
        while stack:
            k = stack.pop()
            if k == m or k in seen:
                continue
            if k == n:
                return True
            seen.add(k)
            stack.extend(x for x in self.g.nodes[k].succ if x < len(self.g.nodes))
        return False

    def select(self, n, v, after=False):
        """(m, then, else) where v has two definitions reaching place n that a conditional block m
        chooses between (a conditional compare: z = r5 == 0x5f; if (!z) { z = lr == 0x19; }): one in
        its then arm, the other in its else arm or the one that reaches m, and every way from that one
        to n passes m; else None. v's value at n is m's condition (as at m) ? then's : else's."""
        defs = self.reaching(n, v, after)
        if not defs or len(defs) != 2 or min(defs) < 0:
            return None
        a, b = sorted(defs)
        for d_then, d_else in ((a, b), (b, a)):
            arm = self.arms.get(d_then)
            if arm is None or arm[1] != "then":
                continue
            m = arm[0]
            if self.arms.get(n, (None,))[0] == m:
                continue
            other = self.arms.get(d_else)
            if other == (m, "else") or (self.reach[m].get(v) == {d_else} and
                                         not self.reaches_avoiding(d_else, n, m)):
                return m, d_then, d_else
        return None

    def region(self, d, n, barriers=()):
        """The nodes on a way from node d to node n that does not pass d again, nor a node of
        barriers (n among them only where a way leaves n and comes back to it)."""
        key = (d, n, tuple(sorted(barriers)))
        if key not in self.regions:
            nodes = self.g.nodes
            stop = {d} | set(barriers)
            fwd, stack = set(), [k for k in nodes[d].succ if k < len(nodes)]
            while stack:
                k = stack.pop()
                if k not in stop and k not in fwd:
                    fwd.add(k)
                    stack.extend(x for x in nodes[k].succ if x < len(nodes))
            bwd, stack = set(), list(nodes[n].pred)
            while stack:
                k = stack.pop()
                if k not in stop and k not in bwd:
                    bwd.add(k)
                    stack.extend(nodes[k].pred)
            self.regions[key] = fwd & bwd
        return self.regions[key]

    def unchanged(self, d, n, names, after=False, barriers=()):
        """Whether nothing on a way from node d to place n (not through barriers: the other
        definitions of a variable whose value d gives on the ways it arrives) writes names (after:
        node n's writes too, unless n is d)."""
        if not names or (after and d == n):
            return True
        if after and self.g.nodes[n].writes & names:
            return False
        return not any(self.g.nodes[k].writes & names for k in self.region(d, n, barriers))

    def reaching(self, n, v, after=False):
        """The definitions of v that reach place n (after: node n's own write of v is the one)."""
        if after and v in self.g.nodes[n].writes:
            return {n} if v in self.g.nodes[n].kills else None
        return self.reach[n].get(v)

    def expandable(self, v, value, deep=True):
        """Whether the flags pass writes v's value into a reader: a flag (n, z, c, v), a 64-bit
        temporary (the lifters' carry arithmetic); deep, also a 32-bit result of one (r0_1 = (u32)t_1)
        or a variable plus a constant (a chain of subtractions)."""
        if FLAG.match(v) or _wide(self.ctx, v):
            return True
        if not deep:
            return False
        t = self.ctx.types.get(v)
        if t is None or t[0] != 32:
            return False
        if isinstance(value, C.Cast) and isinstance(value.e, C.Name) and _wide(self.ctx, value.e.id):
            return True
        return offset_form(value) is not None

    def definition(self, n, v, deep=True, after=False):
        """(node, value) of the one definition of v that reaches place n, when it is v = value with no
        effect but loads, not reading v, of a kind expandable takes (deep), and nothing it reads is
        written on a way from it to n; else None."""
        defs = self.reaching(n, v, after)
        if not defs or len(defs) != 1:
            return None
        d = next(iter(defs))
        if d < 0:
            return None
        s = self.g.nodes[d].stmt
        if not is_assign(s) or s.e.target.id != v:
            return None
        value = s.e.value
        if not self.expandable(v, value, deep) or not _pure_but_loads(value) or reads_count(value, v) or \
                not self.unchanged(d, n, _reads_of(value), after):
            return None
        return d, value

    def expand(self, n, e, deep=True, after=False):
        """(e with the variables it reads written as their values at place n, recursively, where
        definition allows (deep: expandable's), or chosen; {variable: (its definitions, its written
        value)}), or None when none was written or the result has more than EXPAND_NODES nodes. A load in
        a value stands for what it loaded at its definition: a value with a load whose text e itself or
        another value written reads too stays its variable, so that in the proof one text is one
        load."""
        owner = {t: -2 for t in _loads(e)}
        done = {}

        def value_of(v):
            if v in done:
                return done[v]
            done[v] = None                  # (a variable whose value reads it, through others)
            if sum(1 for x in done.values() if x is not None) >= EXPAND_VARIABLES:
                return None
            got = self.definition(n, v, deep, after)
            if got is None:
                done[v] = self.chosen(n, v, deep, after)
                return done[v]
            d, value = got
            texts = _loads(value)
            if any(owner.get(t, d) != d for t in texts):
                return None
            for t in texts:
                owner[t] = d
            done[v] = ((d,), sub(value))
            return done[v]

        def sub(x):
            for v in sorted({y.id for y in walk_expr(x) if isinstance(y, C.Name)}):
                got = value_of(v)
                if got is not None:
                    x = substitute(x, v, got[1])
            return x
        out = sub(e)
        used = {v: got for v, got in done.items() if got is not None}
        if not used or size(out) > EXPAND_NODES:
            return None
        return out, used

    def chosen(self, n, v, deep=True, after=False):
        """((then, else), Cond) for a variable a conditional block chooses the value of (select): its
        condition and both values written as at their own places (expand), none of them reading memory
        or changed on the way to place n; None otherwise."""
        got = self.select(n, v, after)
        if got is None:
            return None
        m, d_then, d_else = got
        parts = [(m, self.g.nodes[m].stmt.cond)]
        for d in (d_then, d_else):
            st = self.g.nodes[d].stmt
            if not (is_assign(st) and st.e.target.id == v and self.expandable(v, st.e.value, deep)) or \
                    reads_count(st.e.value, v):
                return None
            parts.append((d, st.e.value))
        out = []
        for place, e in parts:
            if not _pure_but_loads(e) or _reads_memory(e):
                return None
            full = self.expand(place, e, deep)
            full = full[0] if full is not None else e
            if _reads_memory(full) or not self.unchanged(place, n, _reads_of(full), after,
                                                         () if place == m else (d_then, d_else)):
                return None
            out.append(full)
        return (d_then, d_else), C.Cond(*out)

    def results(self, n, used, after=False):
        """{R: (u32) of the value used writes for t} for the variables R = (u32)t of used's 64-bit
        temporaries t whose definition reaches place n alone, t's definition reaching R's and not on a
        way from R's to n: R holds the 32 bits of the sum whose carry a truth value tests (a term for
        its candidates, sum < operand)."""
        import copy
        out = {}
        for t, (defs, value) in sorted(used.items()):
            if not _wide(self.ctx, t) or len(defs) != 1:
                continue
            d = defs[0]
            for k, r in self.casts.get(t, ()):
                if r in used or r in out or self.reaching(n, r, after) != {k} or self.reach[k].get(t) != {d} or \
                        (k != n and d in self.region(k, n)) or (after and k != n and d == n):
                    continue
                out[r] = C.Cast("u32", copy.deepcopy(value))
        return out


SPLIT_NODES = 12        # the most nodes of a truth value made of two comparisons (_split_search)


def flag_search(e, ctx, kind, loads, below, terms=None, cast="u32", split_below=None):
    """A smaller expression for e (a truth value or a value with its variables written as their values,
    FlagFlow.expand) made of its own operands, of terms ({name: what it stands for}: FlagFlow.results)
    and of the constants flag_numbers adds, that reads the loads loads (each once: the reader's own)
    and no other, proved to stand for e. kind "truth": a comparison (shared_candidates) of e's truth;
    "flag": a comparison of e's value as type cast has it (a flag, 0 or 1); "value": a simple value
    (value_candidates) of e's value as cast has it. None when there is none of fewer than below
    nodes; for a truth value whose selects choose by one condition (FlagFlow.chosen), then one of
    two comparisons of fewer than split_below nodes (_split_search)."""
    terms = terms or {}
    names = [C.Name(r) for r in sorted(terms)]
    if kind == "value":
        found = sorted(((size(c), c, None) for c in value_candidates(e, ctx, names)), key=lambda x: x[0])
    else:
        found = shared_candidates(e, ctx, below=below, terms=names, numbers=flag_numbers(e))
    as_bool = kind == "truth"
    check = None
    for n, cand, recipe in found:
        if n >= below:
            break
        if loads_once(cand) != loads:
            continue
        if check is None:
            check = cexpr.Checker(e if as_bool else C.Cast(cast, e), ctx, as_bool=as_bool)
            if not check.ok:
                return None
        made = made_candidate(recipe, ctx) if recipe is not None else cand
        full = made
        for r, value in terms.items():
            full = substitute(full, r, value)
        if check.stands_for(full if as_bool else C.Cast(cast, full)):
            return made
    if kind == "truth" and split_below is not None:
        return _split_search(e, ctx, loads, split_below, terms)
    return None


def _choose(e, arm):
    """e with each select (c ? a : b) written as its arm a (arm "then") or b."""
    import copy
    if isinstance(e, C.Cond):
        return _choose(copy.deepcopy(e.t if arm == "then" else e.f), arm)
    out = copy.copy(e)
    for slot in type(e).__slots__:
        v = getattr(e, slot, None)
        if isinstance(v, C.Expr):
            setattr(out, slot, _choose(v, arm))
        elif isinstance(v, list):
            setattr(out, slot, [_choose(y, arm) if isinstance(y, C.Expr) else y for y in v])
    return out


def _split_search(e, ctx, loads, below, terms):
    """A truth value for e whose selects all choose by one condition p (a conditional compare's flags:
    FlagFlow.chosen) made of p's comparison and those of e on either way: p && t, !p || t, p || f,
    !p && f, smallest first, proved to stand for e. None where none is."""
    from dread.structure import negate
    conds = [x for x in walk_expr(e) if isinstance(x, C.Cond)]
    if not conds or len({C.expr_text(x.c) for x in conds}) != 1:
        return None
    p = _remembered(conds[0].c, ctx, [], size(conds[0].c), None)
    if p is None:
        p = conds[0].c if truth_value(conds[0].c) and size(conds[0].c) <= 5 else None
    if p is None:
        return None
    sides = {}
    for arm in ("then", "else"):
        way = _choose(e, arm)
        sides[arm] = way if size(way) <= 5 and truth_value(way) else _remembered(way, ctx, loads, size(way), terms)
    forms = []
    if sides["then"] is not None:
        forms += [C.Binary("&&", p, sides["then"]), C.Binary("||", negate(p), sides["then"])]
    if sides["else"] is not None:
        forms += [C.Binary("||", p, sides["else"]), C.Binary("&&", negate(p), sides["else"])]
    check = cexpr.Checker(e, ctx, as_bool=True)
    for f in sorted(forms, key=size):
        if size(f) >= below or loads_once(f) != loads:
            continue
        full = f
        for r, value in (terms or {}).items():
            full = substitute(full, r, value)
        if check.stands_for(full):
            import copy
            return copy.deepcopy(f)
    return None


def _remembered(e, ctx, loads, below, terms):
    """flag_search of a truth value, its answer kept for the tidy run (_RUN, as conditions keeps its)."""
    import copy
    memo = getattr(_RUN, "memo", None)
    key = _flag_key(e, "truth", loads, below, terms or {}, ctx, "u32") + (None,)
    if memo is not None and key in memo:
        got = memo[key]
    else:
        got = flag_search(e, ctx, "truth", loads, below, terms, split_below=None)
        if memo is not None:
            memo[key] = got
    return copy.deepcopy(got) if got is not None else None


def _flag_key(full, kind, loads, below, terms, ctx, cast):
    names = {x.id for x in walk_expr(full) if isinstance(x, C.Name)} | set(terms)
    return ("flags", C.expr_text(full), kind, cast, tuple(loads), below,
            tuple(sorted((r, C.expr_text(x)) for r, x in terms.items())),
            tuple((n, ctx.types.get(n)) for n in sorted(names)), ctx.int_bits, ctx.long_bits)


def c_type(e, ctx):
    """(bits, signed) of e's C type (the usual conversions, cexpr's), or None where it cannot be told."""
    try:
        (a,), ctx2 = cexpr.abstract([e], ctx)
        names = {x.id for x in walk_expr(a) if isinstance(x, C.Name)}
        return cexpr.compile_expr(a, ctx2)({n: 1 for n in names})[1]
    except (cexpr.Undefined, KeyError):
        return None


TRANSPARENT = ("u32", "s32", "u64", "s64")      # casts a sum taken mod 2**32 passes through


def wide_sum(e):
    """The 32-bit value of e, a cast to u32 of a sum through 64 bits (a carry nobody reads: the lifters'
    adds without its flags, (u32)((u64)r6 + 4 + 0); a subtraction, (u64)a + (u32)~b + 1), written as
    the 32-bit sum it is: its terms with their signs, ~b + 1 as -b, the constants summed (a - 8, not
    + 0xfffffff8u), its 64-bit casts and casts to 32 bits gone; None when e is no such sum (another
    operation inside: a product's high word, a carry taken with >> 32). Exact mod 2**32; flags_values
    proves it all the same."""
    if not (isinstance(e, C.Cast) and e.type == "u32" and
            any(isinstance(x, C.Cast) and x.type in ("u64", "s64") for x in walk_expr(e.e))):
        return None
    terms, k = [], [0]

    def visit(x, sign):
        if isinstance(x, C.Binary) and x.op in ("+", "-"):
            visit(x.l, sign)
            visit(x.r, sign if x.op == "+" else -sign)
            return True
        if isinstance(x, C.Cast) and x.type in TRANSPARENT:
            return visit(x.e, sign)
        if isinstance(x, C.Num) and isinstance(x.value, int):
            k[0] += sign * x.value
            return True
        if isinstance(x, C.Unary) and x.op == "~":     # (~b is -b - 1)
            k[0] -= sign
            return visit(x.e, -sign)
        if isinstance(x, C.Unary) and x.op == "-":
            return visit(x.e, -sign)
        if isinstance(x, C.Name) or (isinstance(x, C.Unary) and x.op == "*") or isinstance(x, (C.Index, C.Member, C.Call)):
            terms.append((sign, x))
            return True
        terms.append(None)
        return False
    visit(e.e, 1)
    if None in terms:
        return None
    k = k[0] & 0xffffffff
    if not terms:
        return C.Num("0x%xu" % k if k > 9 else "%du" % k)
    import copy
    terms.sort(key=lambda t: -t[0])                 # (what is added first)
    out = None
    for sign, x in terms:
        x = copy.deepcopy(x)
        if out is None:
            out = x if sign > 0 else C.Unary("-", x)
        else:
            out = C.Binary("+" if sign > 0 else "-", out, x)
    if k:
        out = C.Binary("+", out, C.Num("%d" % k if k < 10 else "0x%xu" % k)) if k < 0x80000000 else \
            C.Binary("-", out, C.Num("%d" % (0x100000000 - k) if 0x100000000 - k < 10 else "0x%xu" % (0x100000000 - k)))
    return out


def _wide_anywhere(fn):
    """Whether any statement of fn has a 64-bit cast in it, or a 64-bit variable is declared (what
    sums, pairs and flags look for first, before they make the types or a flow)."""
    for st in fn.body.body:
        if isinstance(st, C.Decl) and st.specs.split()[-1:] in (["u64"], ["s64"]):
            return True
        for x in _flat(st):
            for e in _stmt_all_exprs(x):
                if any(isinstance(y, C.Cast) and y.type in ("u64", "s64") for y in walk_expr(e)):
                    return True
    return False


def sums(fn):
    """Sums through 64 bits whose carry nobody reads, anywhere in a statement (a call's argument, a
    value stored, a condition's operand: uxListRemove((u32)((u64)r6 + 4 + 0)) as uxListRemove(r6 +
    4); vPortFree's (u32)((u64)0x0u + (u32)~0x8u + 1) + r0 as r0 - 8), as the 32-bit sums they are
    (wide_sum), proved by z3 to have the same value as the cast to u32 has it and kept as a u32 (a cast
    where the sum's own type is not: a byte loaded is promoted to int). values rewrites whole values
    the same way, by a search; until 2026-10-23 the sums folded into an argument before it ran
    stayed. After conditions in each round: a compare's own sum (cmp r0, r5: (u32)((u64)r0 +
    (u32)~r5 + 1) == 0) is its comparison there (r0 == r5), as r0 - r5 == 0 it would not be."""
    if cexpr.z3 is None or not _wide_anywhere(fn):
        return False
    ctx = context(fn)
    memo = getattr(_RUN, "memo", None)
    changed = False

    def fix(e):
        nonlocal changed
        got = wide_sum(e)
        if got is not None and loads_once(e) is not None and loads_once(got) == loads_once(e):
            key = ("sums", C.expr_text(e), tuple((n, ctx.types.get(n)) for n in sorted(
                {x.id for x in walk_expr(e) if isinstance(x, C.Name)})), ctx.int_bits)
            if memo is not None and key in memo:
                good = memo[key]
            else:
                if c_type(got, ctx) != (32, False):
                    got = C.Cast("u32", got)
                good = got if cexpr.Checker(e, ctx).stands_for(got) else None
                if memo is not None:
                    memo[key] = good
            if good is not None:
                import copy
                changed = True
                return copy.deepcopy(good)
        if isinstance(e, C.Binary):
            return C.Binary(e.op, fix(e.l), fix(e.r))
        if isinstance(e, C.Cast):
            return C.Cast(e.type, fix(e.e))
        if isinstance(e, C.Unary):
            return C.Unary(e.op, fix(e.e))
        if isinstance(e, C.Cond):
            return C.Cond(fix(e.c), fix(e.t), fix(e.f))
        if isinstance(e, C.Call):
            return C.Call(e.f, [fix(a) for a in e.args])
        if isinstance(e, C.Assign):
            return C.Assign(e.op, e.target if isinstance(e.target, C.Name) else fix(e.target), fix(e.value))
        return e

    def visit(st):
        for get, put in stmt_exprs(st):
            before = C.expr_text(get())
            after = fix(get())
            if C.expr_text(after) != before:
                put(after)
        for inner in _inner(st):
            visit(inner)
    for st in fn.body.body:
        if not isinstance(st, C.Decl):
            visit(st)
    return changed


def pairs(fn):
    """A two-word addition or subtraction (adds lo; adc hi: t = (u64)a + b + 0; lo = (u32)t; hi =
    (u32)((u64)c + d + (u32)(t >> 32))) as the one 64-bit sum it is: t = ((u64)c << 32 | a) + ((u64)d <<
    32 | b); lo = (u32)t; hi = (u32)(t >> 32) (subs and sbc alike, as a difference). Only where t is read
    but as its low word and by the high word's carry (directly, or through a flag the high word alone
    reads), the high words' operands read no memory and keep their values from t's definition to the
    high word, and z3 proves both words the same. The high word's carry read through the flag is left
    to dead."""
    if cexpr.z3 is None or not any(isinstance(st, C.Decl) and st.specs.split()[-1:] in (["u64"], ["s64"])
                                   for st in fn.body.body):
        return False
    import copy
    ctx = context(fn)
    view = flat_view(fn)
    flow = None
    changed = False
    for d, s in enumerate(view.body.body):
        if not (is_assign(s) and _wide(ctx, s.e.target.id)):
            continue
        low = _word_sum(s.e.value)
        if low is None or not isinstance(low[3], C.Num) or (low[2] == "sub" and low[3].value != 1):
            continue
        t = s.e.target.id
        if flow is None:
            flow = FlagFlow(view, ctx)
        carry = C.Cast("u32", C.Binary(">>", C.Name(t), C.Num("32")))
        readers = [m for m in flow.g.nodes if t in m.reads and d in (flow.reach[m.index].get(t) or ())]
        flags = {}                          # flag -> its node, where it is t's carry and read by one node
        high = None
        for m in readers:
            st = m.stmt
            texts = [C.expr_text(get()) for get, _ in stmt_exprs(st)]
            if is_assign(st) and FLAG.match(st.e.target.id) and C.expr_text(st.e.value) == C.expr_text(carry):
                flags[st.e.target.id] = m.index
                continue
            plain = sum(len(re.findall(r"\(u32\)%s\b" % re.escape(t), x)) for x in texts)
            if sum(reads_count(get(), t) for get, _ in stmt_exprs(st)) == plain and plain:
                continue                    # (lo = (u32)t)
            if high is not None:
                high = False
            else:
                high = m.index
        names = [carry] + [C.Name(f) for f in flags]
        if not high:
            if high is False or not flags:
                continue
        for f, k in flags.items():
            users = [m for m in flow.g.nodes if f in m.reads and k in (flow.reach[m.index].get(f) or ())]
            if len(users) != 1 or (high and users[0].index != high):
                break
            high = users[0].index
        else:
            if not high:
                continue
            h = flow.g.nodes[high].stmt
            found = []
            for get, put in stmt_exprs(h):
                for x in walk_expr(get()):
                    if isinstance(x, C.Cast) and x.type == "u32":
                        hs = _word_sum(x.e)
                        if hs is not None and hs[2] == low[2] and any(C.expr_text(hs[3]) == C.expr_text(n) for n in names):
                            found.append((get, put, x, hs))
            if len(found) != 1:
                continue
            get, put, x, hs = found[0]
            a, b, kind = low[0], low[1], low[2]
            c, dd = hs[0], hs[1]
            zero = C.Num("0")
            if any(not _pure_but_loads(y) for y in (c, dd) if y is not None) or \
                    not flow.unchanged(d, high, _reads_of(c) | (_reads_of(dd) if dd is not None else set())) or \
                    (any(_reads_memory(y) for y in (c, dd) if y is not None) and
                     not _straight(flow.g, d, high, C.Binary("+", c, dd if dd is not None else zero))):
                continue                    # (a load of the high word made where the low word's is: nothing between)
            left = wide_pair(copy.deepcopy(c), copy.deepcopy(a))
            right = wide_pair(copy.deepcopy(dd) if dd is not None else zero, copy.deepcopy(b) if b is not None else zero)
            value = C.Binary("-" if kind == "sub" else "+", left, right)
            if isinstance(right, C.Num) and right.value == 0:
                value = left
            if kind == "add" and low[3].value:
                value = C.Binary("+", value, C.Num(str(low[3].value)))
            old_high = copy.deepcopy(x)
            for f in flags:
                old_high = substitute(old_high, f, carry)
            old_high = substitute(old_high, t, s.e.value)
            new_high = C.Cast("u32", C.Binary(">>", copy.deepcopy(value), C.Num("32")))
            if not (cexpr.prove(C.Cast("u32", s.e.value), C.Cast("u32", value), ctx) and
                    cexpr.prove(old_high, new_high, ctx)):
                continue
            s.e.value = value
            new = copy.deepcopy(carry)
            put(_replace_node(get(), x, new))
            changed = True
    write_back(view)
    return changed


def _replace_node(e, old, new):
    """e with its subexpression old (that very node) replaced by new."""
    if e is old:
        return new
    for slot in type(e).__slots__:
        v = getattr(e, slot, None)
        if isinstance(v, C.Expr):
            setattr(e, slot, _replace_node(v, old, new))
        elif isinstance(v, list):
            setattr(e, slot, [_replace_node(y, old, new) if isinstance(y, C.Expr) else y for y in v])
    return e


SINK = "cc"         # the name (with a number) of a condition the flags pass computes where its flags are set


def flags(fn):
    """Flags read through variables as the comparisons they stand for. fold writes a compare's flags
    into the one condition that reads them, and conditions makes a comparison of that; a flag that two
    statements read (cmd_mode's ls: an if's and a select's), a carry tested away from its add (adds;
    str; blo), a chain of subtractions (subs r0, #1; beq; subs r0, #2; beq), a borrow added back
    (rsbs r0, r1, #0; adcs r0, r1: r1 == 0) stayed carry arithmetic in variables. Here a truth value
    whose variables have one definition each that reaches it with its value unchanged
    (FlagFlow.definition) is searched for in terms of what those definitions read (FlagFlow.expand):
    first the flags and the 64-bit temporaries alone, then their 32-bit results and the variables
    that are another plus a constant too (a chain of subtractions compares its first variable with
    what the constants come to, x == 8 for x - 1 - 2 == 5); its operands, and the variables that hold
    the 32 bits of a sum it tests the carry of (sum < operand: adds r5, r5, r0; blo as r5_1 >= r5). A
    candidate is taken when z3 proves it (as conditions does), it reads no load of a definition (a
    load is not made anew where it was not; a variable that holds what it loaded may stand), and it
    is no larger than the truth value or, where that read a flag or a 64-bit temporary, of at most
    five nodes (an atomic comparison: r0 == r1 for z). Truth values: conditions, the conditions of
    selects anywhere in a statement, comparisons and logical operations, the value of an assignment to
    a flag (c = r1 >= 0x2710u: a flag that a reader past a write of what it compares needs as a
    variable); values through a 64-bit temporary (value_candidates). A condition whose flags are read
    where what they compare has changed (cmp r1, #9; mov r1, r5; bhi) is computed where they are set,
    into a variable of its own (SINK: cc_1 = r1 > 9u; ... while (cc_1)), when it has a comparison
    there and reads a flag that is not one already. The conditions of conditional blocks (an IT
    block's ifs, before merge_ifs has made them one) that read one flag's definition are rewritten
    all or none, as is every reader of a flag that is a comparison already: merge_ifs joins ifs by
    the text of their conditions, and a flag that stays for one reader would only be written twice.
    The definitions no reader needs any more go in dead."""
    if cexpr.z3 is None:
        return False
    import copy
    wide = {d.name for st in fn.body.body if isinstance(st, C.Decl) and st.specs.split()[-1:] in (["u64"], ["s64"])
            for d in st.items}
    offsets = set()                         # (variables some assignment makes another plus a constant)
    for st in fn.body.body:
        for x in _flat(st):
            if is_assign(x) and (offset_form(x.e.value) is not None or (
                    isinstance(x.e.value, C.Cast) and isinstance(x.e.value.e, C.Name) and x.e.value.e.id in wide)):
                offsets.add(x.e.target.id)
    look = {v for v in all_names(fn) if FLAG.match(v)} | wide | offsets
    if not look:
        return False
    view = flat_view(fn)
    if not any(_reads_of(get()) & look for st in view.body.body if isinstance(st, (C.ExprStmt, C.Return, C.If, C.Switch))
               for get, _ in stmt_exprs(st)):
        return False
    ctx = context(fn)
    flow = None
    memo = getattr(_RUN, "memo", None)
    stand_ins = {id(st): orig for st, orig in view.pairs}
    proposals = {}                  # node -> ([(put, new expression)], {(variable, definition) gone}, {sunk})
    sunk = {}                       # (node, text of the comparison) -> (the variable that holds it, comparison)
    taken = None

    def interesting(v):
        return bool(FLAG.match(v)) or _wide(ctx, v)

    def value_type(e):
        t = type_of_expr(e, ctx)
        return "%s%d" % ("s" if t[1] else "u", t[0]) if t is not None and t[0] <= 32 else None

    def wide_arithmetic(e):
        return any(isinstance(x, C.Cast) and x.type in ("u64", "s64") for x in walk_expr(e))

    def search(full, kind, loads, limit, terms, cast, split=False):
        split_below = max(limit + 1, SPLIT_NODES) if split else None
        key = _flag_key(full, kind, loads, limit + 1, terms, ctx, cast) + (split_below,)
        if memo is not None and key in memo:
            return memo[key]
        found = flag_search(full, ctx, kind, loads, limit + 1, terms, cast, split_below)
        if memo is not None:
            memo[key] = found
        return found
    for k, s in enumerate(view.body.body):
        if not isinstance(s, (C.ExprStmt, C.Return, C.If, C.Switch)) or not stmt_exprs(s) or \
                not any(_reads_of(get()) & look for get, _ in stmt_exprs(s)):
            continue
        orig = stand_ins.get(id(s))
        if orig is not None and _written([orig.then] + ([orig.orelse] if orig.orelse is not None else [])) & \
                _reads_of(s.cond):
            continue                        # (if (!z) { z = ...; }: chains makes it z = A || B)
        if flow is None:
            flow = FlagFlow(view, ctx)
        gone, sinks, choosers = set(), set(), set()

        def attempt(e, kind, cast="u32", top=False):
            """e as flag_search finds it at this node: as it is (a flag's value or a value with carry
            arithmetic of its own), with the flags and 64-bit temporaries written as their values,
            then with their 32-bit results and the variables plus constants too (deep); a condition
            (top) sunk where its flags are set; None where nothing is found."""
            reads = _reads_of(e)
            loads = loads_once(e)
            if loads is None:
                return None
            if kind != "truth" and wide_arithmetic(e):
                found = search(e, kind, loads, size(e) - 1, {}, cast)
                if found is not None:
                    return copy.deepcopy(found)
            for deep in (False, True):
                if not deep and not any(interesting(v) for v in reads):
                    continue
                if deep and not any(v in offsets and flow.definition(k, v) is not None for v in reads):
                    continue
                got = flow.expand(k, e, deep)
                if got is None:
                    continue
                full, used = got
                mine = {v for v in used if v in reads}
                limit = size(e)
                if any(interesting(v) for v in mine):
                    limit = max(limit, 5)
                terms = flow.results(k, used)
                chose = any(len(used[v][0]) > 1 for v in used)
                found = search(full, kind, loads, limit, terms, cast, split=chose and kind == "truth")
                if found is not None:
                    gone.update((v, d) for v in mine if v not in _reads_of(found) for d in used[v][0])
                    choosers.update(flow.arms[used[v][0][0]][0] for v in used if len(used[v][0]) > 1)
                    return copy.deepcopy(found)
            if top and kind == "truth" and not loads:
                return sink(e, reads)
            return None

        def sink(e, reads):
            """The condition e of this node computed into a variable where its flags are set (the
            last definition of what it reads, every other reaching there too and nothing written on
            the way to this node), when it is a comparison there; the variable, or None."""
            nonlocal taken
            defs = {}
            for v in reads:
                r = flow.reach[k].get(v)
                if not r or len(r) != 1 or min(r) < 0:
                    return None
                defs[v] = min(r)
            plain = [v for v in reads if FLAG.match(v) and is_assign(flow.g.nodes[defs[v]].stmt) and
                     truth_value(flow.g.nodes[defs[v]].stmt.e.value)]
            if not any(interesting(v) for v in reads) or len(plain) == len(reads) == 1:
                return None                 # (z = r0 == r1 read alone: the flag is its comparison)
            p = max(defs.values())
            if not is_assign(flow.g.nodes[p].stmt) or \
                    any(d != p and flow.reaching(p, v, True) != {d} for v, d in defs.items()) or \
                    not flow.unchanged(p, k, reads):
                return None
            got = flow.expand(p, e, True, after=True)
            if got is None:
                return None
            full, used = got
            found = search(full, "truth", [], max(size(e), 5), flow.results(p, used, True), "u32")
            if found is None or reads & _reads_of(found):
                return None
            key = (p, C.expr_text(found))
            if key not in sunk:
                if taken is None:
                    taken = all_names(fn)
                sunk[key] = (fresh(fn, SINK, taken), copy.deepcopy(found))
            sinks.add(key)
            gone.update((v, d) for v, d in defs.items())
            return C.Name(sunk[key][0])

        def truths(e, cond=False):
            if cond or truth_value(e):
                got = attempt(e, "truth", top=cond)
                if got is not None:
                    return got
            elif size(e) > 3 and any(_wide(ctx, v) for v in _reads_of(e)) and value_type(e):
                got = attempt(e, "value", value_type(e))    # (rsbs; adcs: a value through a carry)
                if got is not None:
                    return got
            if isinstance(e, C.Binary) and e.op in ("&&", "||"):
                return C.Binary(e.op, truths(e.l, True), truths(e.r, True))
            if isinstance(e, C.Unary) and e.op == "!":
                return C.Unary("!", truths(e.e, True))
            if isinstance(e, C.Cond):
                return C.Cond(truths(e.c, True), truths(e.t), truths(e.f))
            if isinstance(e, C.Binary):
                return C.Binary(e.op, truths(e.l), truths(e.r))
            if isinstance(e, C.Cast):
                return C.Cast(e.type, truths(e.e))
            if isinstance(e, C.Unary) and e.op not in ("*", "&"):
                return C.Unary(e.op, truths(e.e))
            if isinstance(e, C.Call):
                return C.Call(e.f, [truths(a) for a in e.args])
            if isinstance(e, C.Assign):
                target = e.target if isinstance(e.target, C.Name) else truths(e.target)
                if isinstance(e.target, C.Name) and FLAG.match(e.target.id) and e.op == "=" and \
                        not truth_value(e.value):
                    got = attempt(e.value, "flag")  # (c = (u32)(t_1 >> 32): a flag's value, 0 or 1)
                    if got is not None:
                        return C.Assign(e.op, target, got)
                return C.Assign(e.op, target, truths(e.value))
            return e
        puts = []
        for get, put in stmt_exprs(s):
            before = C.expr_text(get())
            after = truths(get(), isinstance(s, C.If) and get() is s.cond)
            if C.expr_text(after) != before:
                puts.append((put, after))
        if puts:
            proposals[k] = (puts, gone, sinks, choosers)
    if not proposals:
        return False
    # All or none: the conditional blocks that read one flag's definition (an IT block's, before
    # merge_ifs has made them one), and every reader of a flag whose value is a comparison already
    # (z = r0 == r1: rewriting some of its readers would only write the comparison twice); a
    # conditional compare's own condition aside where its flag was chosen through it (its block and
    # the read go once nothing needs what it chooses).
    readers = {}                            # (variable, definition) -> nodes that read it

    def readers_of(v, d):
        if (v, d) not in readers:
            readers[(v, d)] = [m.index for m in flow.g.nodes if v in m.reads and d in (flow.reach[m.index].get(v) or ())]
        return readers[(v, d)]
    blocked, more = set(), True
    while more:                             # (a reader blocked blocks the others of its flag)
        more = False
        for k, (_, gone, _, choosers) in proposals.items():
            if k in blocked:
                continue
            block = id(view.body.body[k]) in stand_ins
            for v, d in gone:
                plain = FLAG.match(v) and is_assign(flow.g.nodes[d].stmt) and truth_value(flow.g.nodes[d].stmt.e.value)
                if any(m != k and m not in choosers and (plain or (block and id(view.body.body[m]) in stand_ins)) and
                       (m in blocked or (v, d) not in proposals.get(m, ((), ()))[1]) for m in readers_of(v, d)):
                    blocked.add(k)
                    more = True
                    break
    changed, used = False, set()
    for k, (puts, _, sinks, _) in proposals.items():
        if k in blocked:
            continue
        for put, after in puts:
            put(after)
            changed = True
        used |= sinks
    declare = []
    for key in sorted(used, key=lambda key: sunk[key][0]):
        p, (name, value) = key[0], sunk[key]
        at = view.body.body[p]
        home = view.homes[id(at)]
        home.insert(next(i for i, x in enumerate(home) if x is at) + 1,
                    C.ExprStmt(C.Assign("=", C.Name(name), value), at.addr))
        declare.append(name)
    write_back(view)
    if declare:
        body = fn.body.body
        last = max((i for i, x in enumerate(body) if isinstance(x, C.Decl)), default=-1)
        body[last + 1:last + 1] = [C.Decl("u32", [C.Declarator(name)]) for name in sorted(set(declare))]
    return changed


def type_of_expr(e, ctx):
    """(bits, signed) of an expression where it is plain from the tree: a variable's declared type,
    a cast's, a load's pointer type; None otherwise."""
    if isinstance(e, C.Name):
        return ctx.types.get(e.id)
    if isinstance(e, C.Num):
        return None if isinstance(e.value, float) else cexpr.num_type(e.text, e.value, ctx)
    if isinstance(e, C.Cast):
        return ctx.ctype(e.type)
    if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast):
        t = e.e.type.replace("*", " ").split()
        return ctx.ctype(" ".join(w for w in t if w not in ("volatile", "const")))
    return None


def _mask_keeps(cond, mask, ctx):
    """Whether (cond) & mask is cond: a select between constants of mask's type that the mask keeps
    whole ((c ? 0x1u : 0x0u) & 0x1u, an IT block's moveq r0, #0; movne r0, #1 then and r0, #1)."""
    if not (isinstance(cond, C.Cond) and isinstance(mask, C.Num) and isinstance(mask.value, int)):
        return False
    arms = (cond.t, cond.f)
    if not all(isinstance(a, C.Num) and isinstance(a.value, int) and a.value >= 0 for a in arms):
        return False
    t = cexpr.num_type(mask.text, mask.value, ctx)
    return t[0] >= ctx.int_bits and all(cexpr.num_type(a.text, a.value, ctx) == t and a.value & mask.value == a.value
                                        for a in arms)


def width(e, ctx):
    """The bits of e's type where the tree shows them (type_of_expr, promoted to int's width; an
    operator's from its operands' as C converts them), else None."""
    t = type_of_expr(e, ctx)
    if t is not None:
        return max(t[0], ctx.int_bits)
    if isinstance(e, C.Unary) and e.op in ("-", "~"):
        return width(e.e, ctx)
    if isinstance(e, C.Binary) and e.op in ("<<", ">>"):
        return width(e.l, ctx)
    if isinstance(e, C.Binary) and e.op in ("+", "-", "*", "&", "|", "^"):
        a, b = width(e.l, ctx), width(e.r, ctx)
        return None if a is None or b is None else max(a, b)
    return None


def peephole(e, ctx):
    """e with what changes no value taken out: x + 0, x - 0, x | 0, x ^ 0 (x of int's width or wider), a
    cast of a value to the type it has, a mask that keeps a select of constants whole. Bottom up;
    returns a new tree."""
    if isinstance(e, C.Binary):
        l, r = peephole(e.l, ctx), peephole(e.r, ctx)
        if e.op == "&" and (_mask_keeps(l, r, ctx) or _mask_keeps(r, l, ctx)):
            return l if isinstance(l, C.Cond) else r
        if isinstance(l, C.Num) and isinstance(r, C.Num) and e.op in ("+", "-", "|", "&", "^", "<<", ">>", "*"):
            folded = fold_constants(e.op, l, r, ctx)
            if folded is not None:
                return folded
        t = type_of_expr(l, ctx)
        if isinstance(r, C.Num) and r.value == 0 and e.op in ("+", "-", "|", "^", "<<", ">>") and t is not None \
                and t[0] >= ctx.int_bits:
            return l
        if (e.op == ">>" and isinstance(r, C.Num) and isinstance(l, C.Binary) and l.op == "<<"
                and isinstance(l.r, C.Num) and isinstance(r.value, int) and isinstance(l.r.value, int)
                and 0 < l.r.value <= r.value < 32 and type_of_expr(l.l, ctx) == (32, False)):
            # x << a >> b of a 32-bit unsigned x is a field of it: (x >> (b - a)) & mask
            shift, mask = r.value - l.r.value, (1 << (32 - r.value)) - 1
            inner = l.l if shift == 0 else C.Binary(">>", l.l, C.Num("%d" % shift))
            return C.Binary("&", inner, C.Num("0x%xu" % mask if mask > 9 else "%du" % mask))
        return C.Binary(e.op, l, r)
    if isinstance(e, C.Cast):
        inner = peephole(e.e, ctx)
        t = ctx.ctype(e.type)
        if t is not None and type_of_expr(inner, ctx) == t:
            return inner
        return C.Cast(e.type, inner)
    if isinstance(e, C.Unary):
        if e.op == "*":
            return C.Unary("*", peephole(e.e, ctx))
        return C.Unary(e.op, peephole(e.e, ctx))
    if isinstance(e, C.Assign):
        target = e.target if isinstance(e.target, C.Name) else peephole(e.target, ctx)
        return C.Assign(e.op, target, peephole(e.value, ctx))
    if isinstance(e, C.Call):
        return C.Call(e.f, [peephole(a, ctx) for a in e.args])
    if isinstance(e, C.Cond):
        return C.Cond(peephole(e.c, ctx), peephole(e.t, ctx), peephole(e.f, ctx))
    return e


def fold_constants(op, l, r, ctx):
    """A Num for l op r with C's types (the type of the result written as the literal's suffix), or
    None when it is not plain (undefined, negative, wider than 32 bits)."""
    try:
        value, ty = cexpr.compile_expr(C.Binary(op, l, r), ctx)({})
    except cexpr.Undefined:
        return None
    if value < 0 or ty[0] > 32:
        return None
    hexa = l.text.lower().startswith("0x") or r.text.lower().startswith("0x")
    text = ("0x%x" % value if hexa else "%d" % value) + ("" if ty[1] else "u")
    folded = C.Num(text)
    if cexpr.num_type(text, value, ctx) != ty:
        return None
    return folded


def tidy_expressions(fn):
    """peephole in every simple statement, those inside a conditional block too (flat_view)."""
    ctx = context(fn)
    changed = False
    view = flat_view(fn)
    for s in view.body.body:
        for get, put in stmt_exprs(s):
            before = get()
            after = peephole(before, ctx)
            if C.expr_text(after) != C.expr_text(before):
                put(after)
                changed = True
    write_back(view)
    return changed


VALUE_OPS = ("+", "-", "&", "|", "^", "*")


def value_candidates(e, ctx, terms=()):
    """Simple values to try for e: an operand alone or negated, two of them combined by one operator or
    compared (a carry or a borrow; rsbs r0, r1, #0; adcs r0, r1 is r1 == 0). terms: operands beyond
    e's own (the flags pass: a variable that holds the 32 bits of a sum)."""
    import copy
    ops = operands(e) + [t for t in terms if C.expr_text(t) not in {C.expr_text(x) for x in operands(e)}]
    out = [copy.deepcopy(a) for a in ops if not isinstance(a, C.Num)]
    out += [C.Unary("-", copy.deepcopy(a)) for a in ops if not isinstance(a, C.Num)]
    for a in ops:
        for b in ops:
            if a is b or isinstance(a, C.Num):
                continue
            for op in VALUE_OPS:
                out.append(C.Binary(op, copy.deepcopy(a), copy.deepcopy(b)))
            for op in ("==", "!=", "<", ">=", ">", "<="):     # (equality; a carry or a borrow)
                out.append(C.Binary(op, copy.deepcopy(a), copy.deepcopy(b)))
    return out


def loads_once(e):
    """The texts of e's volatile loads when e's only effects are such loads, each read once: a
    rewrite that reads each of them once again keeps them (None when e has other effects). A call is
    such an effect too where it is e's only one (no load, no other call, none in its arguments): a
    rewrite that makes it once keeps it, and with nothing else to happen there is no order to keep
    (f() - 0x1eu == 0 as f() == 0x1eu; until 2026-10-23 nothing with a call was rewritten)."""
    texts, calls = [], []
    for x in walk_expr(e):
        if isinstance(x, C.Call) and not is_pure_call(x):
            calls.append(x)
        elif isinstance(x, (C.Assign, C.Post)) or (isinstance(x, C.Unary) and x.op in ("++", "--")):
            return None
        elif is_volatile_access(x):
            texts.append(C.expr_text(x))
    if calls:
        if len(calls) > 1 or texts or any(has_effects(a) for a in calls[0].args):
            return None
        texts.append(C.expr_text(calls[0]))
    if len(set(texts)) != len(texts):
        return None
    return sorted(texts)


def same_loads(a, b):
    """Whether b reads the volatile loads a reads, each once (and nothing else with an effect)."""
    la, lb = loads_once(a), loads_once(b)
    return la is not None and la == lb


def values(fn):
    """An assignment's value that the lifter computed the long way (an addition through 64 bits for its
    carry, a subtraction as an addition of the complement) as the operation it is, when the evaluator
    finds one that gives the same value on boundary and random values of its operands; a return's
    value alike, as the function's result type has it (strcmp's return (u32)((u64)r2 + (u32)~r3 + 1)
    is r2 - r3)."""
    ctx = context(fn)
    changed = False
    head = re.match(r"^\s*(\w+)\s+\w+\s*\(", fn.head or "")
    result = ctx.ctype(head.group(1)) if head and head.group(1) != "void" else None
    for s in with_arms(fn.body.body):       # (those inside a conditional block too)
        if is_assign(s):
            value, target = s.e.value, ctx.types.get(s.e.target.id)
        elif isinstance(s, C.Return) and s.e is not None:
            value, target = s.e, result
        else:
            continue
        limit, loads = size(value), loads_once(value)
        if limit < 6 or loads is None or len(operands(value)) > 3:
            continue
        if target is None or cexpr.z3 is None:
            continue
        cast = "%s%d" % ("s" if target[1] else "u", target[0])
        check = None                        # (made for the first candidate that reads value's loads)
        for cand in sorted(value_candidates(value, ctx), key=size):
            if size(cand) >= limit:
                break
            if loads_once(cand) != loads:   # (same_loads)
                continue
            if check is None:
                check = cexpr.Checker(C.Cast(cast, value), ctx)
            if check.stands_for(C.Cast(cast, cand)):
                if isinstance(s, C.Return):
                    s.e = cand
                else:
                    s.e.value = cand
                changed = True
                break
    return changed


def plain_literal(n, other_type, ctx):
    """A small unsigned literal (0x5u) written as 5 where that is the same: the value fits int, and the
    other operand is unsigned and at least int's width (the int converts to it anyway)."""
    if not isinstance(n, C.Num) or isinstance(n.value, float) or not 0 <= n.value < 10:
        return n
    t = cexpr.num_type(n.text, n.value, ctx)
    if not t[1] and other_type is not None and not other_type[1] and other_type[0] >= ctx.int_bits:
        return C.Num(str(n.value))
    return n


def literals(fn):
    """Small unsigned literals as plain numbers where the C means the same (plain_literal): beside an
    unsigned operand, assigned to an unsigned variable, as a call's argument (the prototype converts),
    as a shift's count."""
    ctx = context(fn)
    unsigned = (ctx.int_bits, False)

    def fix(e):
        if isinstance(e, C.Binary):
            l, r = fix(e.l), fix(e.r)
            if e.op in ("<<", ">>"):
                r = plain_literal(r, unsigned, ctx)
            elif e.op not in ("&&", "||"):
                l = plain_literal(l, type_of_expr(r, ctx), ctx)
                r = plain_literal(r, type_of_expr(l, ctx), ctx)
            return C.Binary(e.op, l, r)
        if isinstance(e, C.Assign):
            value = fix(e.value)
            if isinstance(e.target, C.Name):
                value = plain_literal(value, ctx.types.get(e.target.id), ctx)
            return C.Assign(e.op, e.target if isinstance(e.target, C.Name) else fix(e.target), value)
        if isinstance(e, C.Call):
            return C.Call(e.f, [plain_literal(fix(a), unsigned, ctx) for a in e.args])
        if isinstance(e, C.Unary):
            return C.Unary(e.op, fix(e.e))
        if isinstance(e, C.Cast):
            return C.Cast(e.type, fix(e.e))
        if isinstance(e, C.Cond):
            return C.Cond(fix(e.c), fix(e.t), fix(e.f))
        return e
    changed = False

    def visit(st):
        nonlocal changed
        for get, put in stmt_exprs(st):
            before = C.expr_text(get())
            after = fix(get())
            if C.expr_text(after) != before:
                put(after)
                changed = True
        for inner in _inner(st):
            visit(inner)
    for st in fn.body.body:
        if not isinstance(st, C.Decl):
            visit(st)
    return changed


PASSES = [("split", split), ("unvoid", unvoid), ("flatten", flatten), ("dead", dead), ("frame", frame_stores),
          ("locals", frame_locals),
          ("propagate", propagate),
          ("selects", selects), ("merge", merge_ifs), ("chains", chains), ("fold", fold), ("arms", empty_arms), ("spread", spread),
          ("addresses", addresses),
          ("expressions", tidy_expressions),
          ("pairs", pairs), ("values", values), ("flags", flags),
          ("conditions", conditions), ("sums", sums), ("literals", literals),
          ("declarations", tidy_declarations)]


def all_calls(fn):
    """[(call, whether its value is used)] of every call in the function's statements, nested ones
    included (a call that is a statement of its own gives nothing anyone reads)."""
    out = []

    def visit(s):
        for e in _stmt_all_exprs(s):
            alone = isinstance(s, C.ExprStmt) and e is s.e
            out.extend((x, not (alone and x is e)) for x in walk_expr(e) if isinstance(x, C.Call))
        for inner in _inner(s):
            visit(inner)
    for s in fn.body.body:
        visit(s)
    return out


def trim_calls(fn, run):
    """A call passes only the argument registers its callee reads (dread.regflow, the run's context):
    the lifters pass them all. Every call of a callee or none, and only calls of the lifters' form (as
    many arguments as the target has argument registers: one with stack arguments stays); the
    callee's extern declaration follows (readable's transform). A callee ignores registers it does
    not read, so the trimmed call is the same call; the gate checks it all the same.

    Where some call's value is read, an argument in a result register stays as far as the callee may
    hand it back (Context.handed_back): a callee that does not write its result register on some path
    returns there what its caller passed (newlib's empty lock functions). One that writes it on every
    path to its return without reading first what its caller left there (regflow's overwritten, on the
    machine code: its callees and tail callees included, a pop or another restore no write, a callee
    that may hand a value back unwritten no end of it) returns a value of its own there, which the
    caller's value cannot reach: the argument would be a phantom (xTaskGetTickCount(r0), whose
    readable C takes void), and the caller's own parameter would look read (params keeps what an
    argument reads), so it goes. Safe as far as regflow's facts are (its docstring: a push is no use
    there, as for every argument trimmed by reads); the gate checks the result all the same. Called again after the clean-up has run (_tidy): an argument it kept
    because the call's value was read may go once nothing reads that value any more (the lifters'
    vPortExitCritical(r0, ...) behind vPortEnterCritical read its r0 until it was trimmed itself)."""
    ctx = run.context if run is not None else None
    if ctx is None or not ctx.arg_regs:
        return False
    k = len(ctx.arg_regs)
    by_callee, used = {}, set()
    for call, value_used in all_calls(fn):
        if isinstance(call.f, C.Name):
            by_callee.setdefault(call.f.id, []).append(call)
            if value_used:
                used.add(call.f.id)
    changed = False
    for name, calls in by_callee.items():
        n = ctx.arity(name)
        if n is None:
            continue
        if name in used:
            # The call's value is read: what the callee leaves in its result registers is what the
            # caller passed there when it does not write them (newlib's empty lock functions: the lock's
            # address comes back, and __malloc_lock returns it), and the lift check compares a result
            # whether callers read it or not. So the arguments in the result registers stay, but for
            # those the callee overwrites (regflow: its own value there on every path).
            for reg in ctx.handed_back(name, run.returns.get(name) in ("u64", "s64")):
                n = max(n, ctx.arg_regs.index(reg) + 1)
        have = run.trimmed.get(name, k)     # (trimmed before: a value read then may be read no longer)
        if n >= have:
            continue
        if any(len(c.args) != have or any(has_effects(a) for a in c.args[n:]) for c in calls):
            continue
        for c in calls:
            del c.args[n:]
        run.trimmed[name] = n
        changed = True
    return changed


def push_room(fn, run=None):
    """A push's stores into the function's own frame go where no load of the function reads a byte
    of their words: the words a push makes room for locals with (GCC -Os pushes r0-r3 for an 8- or
    12-byte local whose address goes to a callee: gizmo's print_status pushes {r0, r1, r2, r3, r4, lr}
    for its 12-byte buffer), which hold whatever the pushed registers held. A correct program reads a
    local only after something wrote it, and GCC writes a local's first value with a store of its
    own, never through such a push (an address-taken parameter: push {r0, r1, r2, lr}; str r0, [sp,
    #4]), so nothing reads those words before they are written; kept, the stores read the caller's
    registers, which made phantom parameters (print_status(void) took four, regflow says it reads
    none: a push is no use there) and the first fields of the frame's objects (dread.frames). Only
    with the program in the run's context (the arch layer's stack_kind says the instruction is a
    push), for the body's own statements, not for a variadic function or one that calls one (a save
    area of arguments the callee reads through a va_list), and only for a word the function loads no
    byte of directly (a pop loads what it restores; regflow's argument pushed and loaded back stays).
    A word the function does load goes too where the statements behind the push store every byte of
    it that is loaded before anything may read one (xQueueGenericSend pushes r1 and stores its fourth
    argument over it at once). The words may be read by a callee given their address before
    anything writes them only in a program that reads an uninitialised local; the gate checks every
    function on its cases."""
    ctx = run.context if run is not None else None
    if ctx is None or getattr(ctx, "_program", None) is None or ctx.unknown(fn.name) or \
            fn.name in getattr(ctx, "save_areas", ()):
        return False
    loaded, stores = set(), []

    def access(e):
        if isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast) and frame_address(e.e.e):
            off = frame_offset(e.e.e)
            t = e.e.type.replace("volatile", " ").replace("*", " ").split()
            if off is not None and len(t) == 1 and t[0] in SLOT_SIZES:
                return off, SLOT_SIZES[t[0]]
            return None, None
        return None

    def visit(e, target=False):
        if e is None:
            return
        got = access(e)
        if got is not None:
            off, size = got
            if off is None:
                loaded.add(None)                # (an access at no constant offset: it may load any word)
            elif not target:
                loaded.update(range(off, off + size))
            return
        if isinstance(e, C.Assign):
            visit(e.target, target=e.op == "=")
            visit(e.value)
            return
        for c in _children(e):
            visit(c)

    def visit_stmt(st):
        if isinstance(st, C.Decl):
            for d in st.items:
                if d.name != FRAME_BASE:
                    visit(d.init)
        elif isinstance(st, C.Asm):
            for o in st.outputs + st.inputs:
                visit(o.e)
        else:
            for e in _stmt_all_exprs(st):
                visit(e)
        for inner in _inner(st):
            visit_stmt(inner)
    body = fn.body.body
    for i, st in enumerate(body):
        visit_stmt(st)
        if isinstance(st, C.ExprStmt) and isinstance(st.e, C.Assign) and st.e.op == "=" and st.addr is not None:
            got = access(st.e.target)
            if got is not None and got[0] is not None and not has_effects(st.e.value) and ctx.is_push(st.addr[0]):
                stores.append((i, st, got))
    if None in loaded:
        return False

    def overwritten(i, live):
        """Whether the statements behind body[i] store every byte of live before anything may read
        one: plain statements without calls, their accesses at constant frame offsets."""
        for st in body[i + 1:]:
            if not (isinstance(st, C.ExprStmt) and isinstance(st.e, C.Assign)) or \
                    any(isinstance(x, C.Call) for x in walk_expr(st.e)):
                return False
            reads, writes, other = set(), set(), [False]

            def look(e, target=False):
                got = access(e)
                if got is not None:
                    if got[0] is None:
                        other[0] = True
                    else:
                        (writes if target else reads).update(range(got[0], got[0] + got[1]))
                    return
                if isinstance(e, C.Unary) and e.op == "*" or isinstance(e, (C.Index, C.Member)):
                    other[0] = True         # (memory through another address)
                if isinstance(e, C.Assign):
                    look(e.target, target=e.op == "=")
                    look(e.value)
                    return
                for c in _children(e):
                    look(c)
            look(st.e)
            if other[0] or reads & live:
                return False
            live = live - writes
            if not live:
                return True
        return False
    gone = set()
    for i, st, (off, size) in stores:
        live = loaded & set(range(off, off + size))
        if not live or overwritten(i, live):
            gone.add(id(st))
    if not gone:
        return False
    fn.body.body[:] = [st for st in body if id(st) not in gone]
    return True


def params(fn, run=None):
    """A parameter the body only copies into its register's variable (u32 r0 = a0) is that variable;
    a parameter whose value at the entry nothing reads is none: trailing ones go (the callers'
    registers are ignored, as they were), one before a parameter that stays is renamed, and its
    register's variable, when the body uses it, becomes a local of its own (prvAddCurrentTaskTo-
    DelayedList's r3, written before every read, was a fourth parameter the model named). The value
    at the entry is read where it reaches a read in the flow of flat_view (an if/else whose arms both
    write the register's variable ends its value there; in a graph of the body's statements the if
    was one node that wrote it on some path only, and gizmo's uart_getc took the r0 it writes on
    either way of its IT block for a parameter). Only for a plain head (type name(parameters)), which
    is written anew. A push's stores that only make room for locals go first (push_room): they read
    the caller's registers and made parameters of registers the function does not read."""
    import re
    m = re.match(r"^(\w+) (\w+)\((.*)\)$", fn.head.strip())
    if not m or m.group(2) != fn.name:
        return False
    pushed = push_room(fn, run)
    names = all_names(fn)
    reads = {}
    for s in fn.body.body:
        for e in _stmt_all_exprs(s):
            for x in walk_expr(e):
                if isinstance(x, C.Name):
                    reads[x.id] = reads.get(x.id, 0) + 1
        for inner in _inner(s):
            for st in _flat(inner):
                for e in _stmt_all_exprs(st):
                    for x in walk_expr(e):
                        if isinstance(x, C.Name):
                            reads[x.id] = reads.get(x.id, 0) + 1
    changed = pushed
    new = list(fn.params)
    for i, (ptype, pname) in enumerate(fn.params):
        if reads.get(pname, 0) != 1:
            continue
        for s in fn.body.body:
            if not isinstance(s, C.Decl) or " ".join(s.specs.split()) != ptype:
                continue
            for d in s.items:
                if isinstance(d.init, C.Name) and d.init.id == pname and not d.dims and not d.ptr and not d.asm:
                    new[i] = (ptype, d.name)
                    s.items.remove(d)
                    changed = True
                    break
            else:
                continue
            break
    fn.body.body[:] = [s for s in fn.body.body if not (isinstance(s, C.Decl) and not s.items)]
    used = set()
    for s in fn.body.body:
        for st in _flat(s):
            for e in _stmt_all_exprs(st):
                used |= {x.id for x in walk_expr(e) if isinstance(x, C.Name)}
            if isinstance(st, C.Decl):
                used |= {d.name for d in st.items}
    fn.params = new                         # (the graph and the names see the parameters as they are now)
    try:
        g = Graph(flat_view(fn))            # (an if whose arms both write a variable ends its value)
        reach = g.reaching()
        entry_read = set()
        for n in g.nodes:
            for v in n.reads:
                if -1 in (reach[n.index].get(v) or ()):
                    entry_read.add(v)
    except C.ParseError:                    # (no graph: only parameters nobody names go, as before)
        entry_read = used
    locals_ = []                            # (type, name): a register's variable no parameter now
    while new and new[-1][1] not in entry_read:
        ptype, pname = new.pop()
        if pname in used:
            locals_.append((ptype, pname))
        changed = True
    taken = all_names(fn)
    for i, (ptype, pname) in enumerate(new):
        if pname not in entry_read and pname in used:
            name = "a%d" % i if "a%d" % i not in taken else fresh(fn, "a%d" % i, taken)
            taken.add(name)
            new[i] = (ptype, name)
            locals_.append((ptype, pname))
            changed = True
    for ptype, pname in reversed(locals_):
        fn.body.body.insert(0, C.Decl(ptype, [C.Declarator(pname)]))
    if changed:
        fn.params = new
        fn.head = "%s %s(%s)" % (m.group(1), fn.name, ", ".join("%s %s" % p for p in new) or "void")
    return changed


def _flat(s):
    out = [s]
    for inner in _inner(s):
        out += _flat(inner)
    return out


def flat_view(fn):
    """A stand-in for fn whose body has its conditional blocks spread out (if (c) goto then; goto
    else; then: ...; goto end; else: ...; end:) for dread.cflow.Graph: a graph of it tells the reads
    inside a block (an IT block, merged ifs) apart from the definitions before it. The statements and
    expressions are fn's own, so what is renamed through the stand-in is renamed in fn; a condition
    replaced in a stand-in's if (pairs: (stand-in, fn's if)) is copied back by write_back. A block
    that declares, labels, loops or switches stays one statement, as does an if that writes nothing.
    homes: {id(statement): the list in fn that holds it} for fn's own statements (the body's, a
    block's), so that a pass may take one out or put another in its place (replace)."""
    import types
    out, count, pairs, homes = [], [0], [], {}

    def plain(s):
        if isinstance(s, C.Block):
            return all(plain(x) for x in s.body)
        if isinstance(s, C.If):
            return plain(s.then) and (s.orelse is None or plain(s.orelse))
        return not isinstance(s, (C.Decl, C.Label, C.While, C.DoWhile, C.For, C.Switch, C.Case, C.Asm))

    def writes(s):
        return any(isinstance(x, C.ExprStmt) for x in _flat(s))

    def spread(s, home):
        if isinstance(s, C.Block) and plain(s):
            for x in s.body:
                spread(x, s.body)
        elif isinstance(s, C.If) and plain(s) and writes(s):
            count[0] += 1
            then_l, else_l, end_l = ("flat_%s_%d" % (w, count[0]) for w in ("then", "else", "end"))
            stand_in = C.If(s.cond, C.Goto(then_l), None, s.addr)
            pairs.append((stand_in, s))
            out.extend([stand_in, C.Goto(else_l if s.orelse is not None else end_l), C.Label(then_l)])
            spread(s.then, None)
            if s.orelse is not None:
                out.extend([C.Goto(end_l), C.Label(else_l)])
                spread(s.orelse, None)
            out.append(C.Label(end_l))
        else:
            out.append(s)
            if home is not None:
                homes[id(s)] = home
    for s in fn.body.body:
        spread(s, fn.body.body)
    return types.SimpleNamespace(name=fn.name, params=fn.params, head=fn.head,
                                 body=types.SimpleNamespace(body=out), pairs=pairs, homes=homes)


def write_back(view):
    """The conditions a pass replaced in a flat_view's stand-in ifs, into the function's own ifs, with
    the instructions a stand-in absorbed with them (cparse.absorb)."""
    for stand_in, original in view.pairs:
        original.cond = stand_in.cond
        if original.addr is not None and stand_in.addr is not original.addr:
            original.addr = stand_in.addr


def replace(view, s, new=None):
    """Statement s of a flat_view's function (one with a home) taken out of the list that holds it,
    or new put in its place."""
    home = view.homes[id(s)]
    for k, x in enumerate(home):
        if x is s:
            if new is None:
                del home[k]
            else:
                home[k] = new
            return
    raise ValueError("a statement no longer where the view found it")


def webs(fn):
    """A variable that holds unrelated values in turn (a register: the first argument, later a
    callee's result) split into one variable per web: definitions that reach a common use are one
    web, and each web but the one the entry's value belongs to (or the first) gets a name of its own
    (r0_2, r0_3); a parameter keeps its name for its value at the entry. The flow is that of the
    body with its conditional blocks spread out (flat_view), so a variable written inside one is
    split too. Not for a variable written inside a statement that stays whole (a loop, a block that
    declares) or written by asm, a chain or an increment."""
    g = Graph(flat_view(fn))
    reach = g.reaching()
    top_writes, nested = {}, set()
    for n in g.nodes:
        s = n.stmt
        if is_assign(s):
            top_writes.setdefault(s.e.target.id, []).append(n.index)
        elif isinstance(s, C.ExprStmt):
            nested |= n.writes             # (a chain, an increment: left alone)
        elif not isinstance(s, C.Decl):
            nested |= n.writes
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    candidates = {v for v in top_writes if v in g.scope and v not in nested}
    uses = {}                               # (v, node) -> the definitions reaching it
    for n in g.nodes:
        for v in n.reads & candidates:
            defs = reach[n.index].get(v) or set()
            defs = sorted(defs)
            for d in defs[1:]:
                union((v, defs[0]), (v, d))
            uses[(v, n.index)] = defs
    changed = False
    taken = all_names(fn)
    decls = declarations(fn)
    initialised = {}                        # variable -> the declaration that gives it a value (u32 r0 = a0)
    for n in g.nodes:
        if isinstance(n.stmt, C.Decl):
            for d in n.stmt.items:
                if d.init is not None:
                    initialised[d.name] = n.index
    for v in sorted(candidates):
        classes = {}
        for d in top_writes[v]:
            classes.setdefault(find((v, d)), []).append(d)
        entry = find((v, -1))
        if v in initialised and find((v, initialised[v])) in classes:
            # (its declaration's value is the entry's: the web it belongs to keeps the name the
            # declaration has; until 2026-10-23 a web with a smaller first definition did, and the
            # declaration's value was lost to the reads renamed away from it, at a loop's head)
            entry = find((v, initialised[v]))
        for (u, node), defs in uses.items():
            if u == v and defs:
                classes.setdefault(find((v, defs[0])), [])
        ptype = {n: t for t, n in fn.params}.get(v)
        if len(classes) < 2:
            continue
        if v in decls:
            if decls[v][1].dims or decls[v][1].ptr:
                continue
            specs = decls[v][0].specs
        elif ptype is not None:             # a parameter: its value at the entry keeps its name
            specs = ptype
        else:
            continue
        keep = entry if entry in classes else min(classes, key=lambda c: min(classes[c]) if classes[c] else 0)
        for cls in sorted(classes, key=lambda c: min(classes[c]) if classes[c] else -1):
            if cls == keep:
                continue
            name = fresh(fn, v, taken)
            members = set(classes[cls])
            for d in members:                # the definitions: the assignment's target
                g.nodes[d].stmt.e.target.id = name
            for (u, node), defs in uses.items():
                if u == v and defs and find((v, defs[0])) == cls:
                    _rename_reads(g.nodes[node].stmt, v, name)
            decl = C.Decl(specs, [C.Declarator(name)])
            last = max((i for i, x in enumerate(fn.body.body) if isinstance(x, C.Decl)), default=-1)
            fn.body.body.insert(last + 1, decl)
            changed = True
        if changed:
            return True                     # (indices moved: the next round sees the rest)
    return changed


def _rename_reads(s, old, new):
    """The reads of old in statement s renamed, in what it contains too (its plain assignment targets
    stay): webs splits only variables no nested statement writes, so every read in s sees the
    definitions that reach s (a conditional block's store of r5 was left with the old name, and
    gizmo's parse_kv stored the wrong character)."""
    def ren(e):
        if e is None:
            return
        if isinstance(e, C.Name):
            if e.id == old:
                e.id = new
            return
        if isinstance(e, C.Assign):
            if not (isinstance(e.target, C.Name) and e.op == "="):
                ren(e.target)
            ren(e.value)
            return
        for c in _children(e):
            ren(c)
    for st in _flat(s):
        if isinstance(st, C.Asm):
            for o in st.inputs:
                ren(o.e)
            continue
        if isinstance(st, C.Decl):
            for d in st.items:
                ren(d.init)
            continue
        for e in _stmt_all_exprs(st):
            ren(e)


def group_declarations(fn):
    """The body's plain declarations (no initial value, array, pointer or register binding) of one
    type in one line, in the order of their names (r0, r0_1, r2, r10), at the place of the first
    declaration of that type: the splits add one declaration each."""
    import re

    def key(name):
        return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]

    def plain(d):
        return d.init is None and not d.dims and not d.ptr and not d.asm
    first, merged = {}, False
    out = []
    for s in fn.body.body:
        if isinstance(s, C.Decl) and s.items and all(plain(d) for d in s.items):
            spec = " ".join(s.specs.split())
            if spec in first:
                first[spec].items += s.items
                merged = True
                continue
            first[spec] = s
        out.append(s)
    for s in first.values():
        order = sorted(s.items, key=lambda d: key(d.name))
        if [d.name for d in order] != [d.name for d in s.items]:
            s.items[:] = order
            merged = True
    fn.body.body[:] = out
    return merged


def tidy(fn, run=None, passes=None, rounds=8):
    """All passes, again until none changes anything (at most rounds times); then the calls' and the
    function's own arguments (trim_calls, params) and the passes once more, the calls' again while
    that leaves values nobody reads. The conditions' answers are kept for the run (_RUN: conditions)."""
    outer = getattr(_RUN, "memo", None)
    _RUN.memo = {}
    try:
        return _tidy(fn, run, passes, rounds)
    finally:
        _RUN.memo = outer


def _tidy(fn, run, passes, rounds):
    def settle():
        for _ in range(rounds):
            changed = False
            for _, p in (PASSES if passes is None else passes):
                changed = p(fn) or changed
            if not changed:
                break
    settle()
    if passes is None and (trim_calls(fn, run) | params(fn, run)):
        settle()
        while trim_calls(fn, run):          # (values nobody reads now: only ever fewer arguments)
            settle()
        params(fn, run)
    if passes is None:
        split_any = False
        for _ in range(16):                 # (one variable a round: webs renumbers)
            if not webs(fn):
                break
            split_any = True
        if split_any:
            settle()
        group_declarations(fn)
    return fn
