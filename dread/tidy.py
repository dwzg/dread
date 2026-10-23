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


def type_of(fn, name):
    found = declarations(fn).get(name)
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
    when an address that leaves is not a constant offset below sp0. A slot accessed with one size but
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
    for name in all_names(fn):
        t = type_of(fn, name)
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


def shared_candidates(e, ctx, most=10, below=None):
    """[(size, candidate, recipe)] of candidates(), in its order; each candidate shares e's
    subexpressions (to try, not to keep: made_candidate makes the one to keep of its recipe), its
    size counted, not walked. below: only those of fewer nodes. Until 2026-10-23 every candidate was
    made of copies and walked for its size, most of them larger than e and never tried: the copies
    alone were a fifth of the readable C's time on FreeRTOS's GCC -Os demo."""
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
    terms = [x for x in subs if not isinstance(x, C.Num)][:most]
    leaves = [x for x in terms if isinstance(x, C.Name) or (isinstance(x, C.Unary) and x.op == "*")]
    pairs = compared(e) + [(a, b) for a in terms for b in consts] + [(a, b) for a in leaves for b in leaves
                                                                     if a is not b]
    sizes = {}                              # id -> size, of e's subexpressions and consts (alive meanwhile)

    def sz(x):
        if id(x) not in sizes:
            sizes[id(x)] = size(x)
        return sizes[id(x)]

    def signed_size(x, y):                  # (y = as_signed(x): a cast of x, or a constant of its own)
        return 1 + sz(x) if isinstance(y, C.Cast) and y.e is x else size(y)

    def bits_of(x):
        t = type_of_expr(x, ctx)
        return t[0] if t else ctx.int_bits
    out = []
    for a, b in pairs:
        n = 1 + sz(a) + sz(b)
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
    compare), a select's condition wherever the select is (in a return's value). Within one tidy run a
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
        return e
    for s in with_arms(fn.body.body):
        if isinstance(s, C.If):                     # (a branch's, a conditional block's)
            parts = [(lambda s=s: s.cond, lambda v, s=s: setattr(s, "cond", v), True)]
        elif is_assign(s):                          # (a select's, a flag's: 0 or 1 either way)
            parts = [(lambda s=s: s.e.value, lambda v, s=s: setattr(s.e, "value", v), False)]
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


def value_candidates(e, ctx):
    """Simple values to try for e: an operand alone, two of them combined by one operator."""
    import copy
    ops = operands(e)
    out = [copy.deepcopy(a) for a in ops if not isinstance(a, C.Num)]
    for a in ops:
        for b in ops:
            if a is b or isinstance(a, C.Num):
                continue
            for op in VALUE_OPS:
                out.append(C.Binary(op, copy.deepcopy(a), copy.deepcopy(b)))
            for op in ("<", ">=", ">", "<="):       # (a carry or a borrow)
                out.append(C.Binary(op, copy.deepcopy(a), copy.deepcopy(b)))
    return out


def loads_once(e):
    """The texts of e's volatile loads when e's only effects are such loads, each read once: a
    rewrite that reads each of them once again keeps them (None when e has other effects)."""
    texts = []
    for x in walk_expr(e):
        if isinstance(x, C.Call) and not is_pure_call(x):
            return None
        if isinstance(x, (C.Assign, C.Post)) or (isinstance(x, C.Unary) and x.op in ("++", "--")):
            return None
        if is_volatile_access(x):
            texts.append(C.expr_text(x))
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
          ("values", values),
          ("conditions", conditions), ("literals", literals),
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
    is written anew."""
    import re
    m = re.match(r"^(\w+) (\w+)\((.*)\)$", fn.head.strip())
    if not m or m.group(2) != fn.name:
        return False
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
    changed = False
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
    for v in sorted(candidates):
        classes = {}
        for d in top_writes[v]:
            classes.setdefault(find((v, d)), []).append(d)
        entry = find((v, -1))
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
