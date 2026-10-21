"""Data flow over a lifted function's tree (dread.cparse): what each statement reads and writes, the
control-flow graph of the body's statements, liveness and reaching definitions.

The lifters' C is flat: the body is a list of statements, labels and gotos, and a nested statement
is a block of an instruction's own (the flags of a compare, a conditional instruction, a call bound
to registers) or a loop that never ends. So the graph's nodes are the body's statements: a nested
one is a node with the reads and writes of all it contains, its writes counted as certain only when
every path through it makes them (Node.kills). Memory is not a variable: what a statement does to
it, it does in order (Node.effects: a volatile access, a call, an asm statement), and what reads
memory reads it at its place.

Variables are the function's own locals (the registers, the flags, the frame's base) and its
parameters, by name: what a nested block declares is its own, and the flow does not look inside it
beyond what it reads and writes of the outer variables.
"""
from dread import cparse as C


def walk_expr(e):
    """Every sub-expression of e, e first."""
    stack = [e]
    while stack:
        x = stack.pop()
        if x is None:
            continue
        yield x
        if isinstance(x, (C.Unary, C.Post, C.Cast)):
            stack.append(x.e)
        elif isinstance(x, C.Binary):
            stack += [x.r, x.l]
        elif isinstance(x, C.Cond):
            stack += [x.f, x.t, x.c]
        elif isinstance(x, C.Assign):
            stack += [x.value, x.target]
        elif isinstance(x, C.Call):
            stack += list(reversed(x.args)) + [x.f]
        elif isinstance(x, C.Index):
            stack += [x.index, x.base]
        elif isinstance(x, C.Member):
            stack.append(x.base)
        elif isinstance(x, C.Comma):
            stack += list(reversed(x.items))
        elif isinstance(x, C.Sizeof):
            stack.append(x.e)
        elif isinstance(x, C.InitList):
            stack += list(reversed(x.items))
        elif isinstance(x, C.Designated):
            stack.append(x.value)
        elif isinstance(x, C.Compound):
            stack.append(x.init)


PURE_CALLS = {"__builtin_return_address", "__builtin_frame_address", "__builtin_clz", "__builtin_ctz",
              "__builtin_popcount", "__builtin_bswap16", "__builtin_bswap32", "__builtin_bswap64",
              "__builtin_expect"}      # (no effect: what they give depends on their arguments and the frame)


def is_pure_call(e):
    return isinstance(e, C.Call) and isinstance(e.f, C.Name) and e.f.id in PURE_CALLS


def is_volatile_access(e):
    """A load or store through a volatile pointer: *(volatile T *)(...)."""
    return isinstance(e, C.Unary) and e.op == "*" and isinstance(e.e, C.Cast) and "volatile" in e.e.type.split()


def has_effects(e):
    """Whether evaluating e does more than compute a value: a call, an assignment, an increment, a
    volatile access (a peripheral's register may change when read)."""
    for x in walk_expr(e):
        if isinstance(x, C.Call) and is_pure_call(x):
            continue
        if isinstance(x, (C.Call, C.Assign, C.Post)) or (isinstance(x, C.Unary) and x.op in ("++", "--")):
            return True
        if is_volatile_access(x):
            return True
    return False


def reads_memory(e):
    for x in walk_expr(e):
        if isinstance(x, C.Unary) and x.op == "*":
            return True
        if isinstance(x, (C.Index, C.Member, C.Call)):
            return True
    return False


def expr_reads(e, out):
    """Adds to out the names e reads (an assignment's target is written, not read, unless compound
    or a dereference: *(p) = v reads p)."""
    for x in walk_expr(e):
        if isinstance(x, C.Name):
            out.add(x.id)
    # (an assignment's plain target counts as read above; callers that need writes apart use
    # expr_writes and remove what is only written)
    return out


def expr_writes(e, out):
    """Adds to out the names e assigns (plain variables only)."""
    for x in walk_expr(e):
        if isinstance(x, C.Assign) and isinstance(x.target, C.Name):
            out.add(x.target.id)
        elif isinstance(x, (C.Post,)) and isinstance(x.e, C.Name):
            out.add(x.e.id)
        elif isinstance(x, C.Unary) and x.op in ("++", "--") and isinstance(x.e, C.Name):
            out.add(x.e.id)
    return out


def _reads_of(e):
    """The names an expression reads, the plain targets of its plain assignments left out."""
    reads = set()

    def visit(x):
        if x is None:
            return
        if isinstance(x, C.Name):
            reads.add(x.id)
        elif isinstance(x, C.Assign):
            if not isinstance(x.target, C.Name) or x.op != "=":
                visit(x.target)
            visit(x.value)
        elif isinstance(x, (C.Unary, C.Post, C.Cast)):
            visit(x.e)
        elif isinstance(x, C.Binary):
            visit(x.l)
            visit(x.r)
        elif isinstance(x, C.Cond):
            visit(x.c)
            visit(x.t)
            visit(x.f)
        elif isinstance(x, C.Call):
            visit(x.f)
            for a in x.args:
                visit(a)
        elif isinstance(x, C.Index):
            visit(x.base)
            visit(x.index)
        elif isinstance(x, C.Member):
            visit(x.base)
        elif isinstance(x, C.Comma):
            for i in x.items:
                visit(i)
        elif isinstance(x, C.Sizeof):
            visit(x.e)
        elif isinstance(x, C.InitList):
            for i in x.items:
                visit(i)
        elif isinstance(x, C.Designated):
            visit(x.value)
        elif isinstance(x, C.Compound):
            visit(x.init)
    visit(e)
    return reads


class Node:
    """One statement of the body: what it reads before it writes (reads), what it may write (writes),
    what it writes on every path through it (kills), whether it has effects beyond its writes
    (effects), where control goes next (succ, filled by Graph)."""
    __slots__ = ("index", "stmt", "reads", "writes", "kills", "effects", "succ", "pred")

    def __init__(self, index, stmt):
        self.index, self.stmt = index, stmt
        self.reads, self.writes, self.kills = set(), set(), set()
        self.effects = False
        self.succ, self.pred = [], []


def summarize(stmt, node, scope):
    """Fill a node's reads, writes, kills and effects from a statement (nested ones included). scope:
    the names declared outside it (what a nested block declares is not a variable of the function)."""
    def stmt_flow(s, local, certain):
        """(reads, writes, kills, effects) of s; local: names declared inside the statement so far;
        certain: whether s runs on every path through the node."""
        r, w, k, eff = set(), set(), set(), False
        if isinstance(s, C.ExprStmt):
            r = _reads_of(s.e) - local
            w = expr_writes(s.e, set()) - local
            k = set(w) if certain else set()
            eff = not _only_assigns(s.e)        # a call, a volatile access, a store through a pointer
        elif isinstance(s, C.Decl):
            for d in s.items:
                if d.init is not None:
                    r |= _reads_of(d.init) - local
                    eff = eff or has_effects(d.init)
                for dim in d.dims:
                    if dim is not None:
                        r |= _reads_of(dim) - local
                local.add(d.name)
        elif isinstance(s, C.Block):
            inner = set(local)
            for x in s.body:
                rr, ww, kk, ee = stmt_flow(x, inner, certain)
                r |= rr - (k - local)       # (read after a certain write inside: not a read of the node's input)
                w |= ww
                k |= kk
                eff = eff or ee
        elif isinstance(s, C.If):
            r = _reads_of(s.cond) - local
            eff = has_effects(s.cond)
            for branch in (s.then, s.orelse):
                if branch is not None:
                    rr, ww, kk, ee = stmt_flow(branch, set(local), False)
                    r |= rr
                    w |= ww
                    eff = eff or ee
        elif isinstance(s, (C.While, C.DoWhile, C.For)):
            parts = []
            if isinstance(s, C.For):
                if isinstance(s.init, C.Decl):
                    rr, _, _, ee = stmt_flow(s.init, local, certain)
                    r |= rr
                    eff = eff or ee
                elif s.init is not None:
                    parts.append(s.init)
                parts += [s.cond, s.step]
            else:
                parts.append(s.cond)
            for p in parts:
                if p is not None:
                    r |= _reads_of(p) - local
                    w |= expr_writes(p, set()) - local
                    eff = eff or has_effects(p)
            rr, ww, kk, ee = stmt_flow(s.body, set(local), False)
            r |= rr
            w |= ww
            eff = eff or ee
        elif isinstance(s, C.Switch):
            r = _reads_of(s.e) - local
            rr, ww, kk, ee = stmt_flow(s.body, set(local), False)
            r |= rr
            w |= ww
            eff = eff or ee
        elif isinstance(s, C.Return):
            if s.e is not None:
                r = _reads_of(s.e) - local
                eff = has_effects(s.e)
        elif isinstance(s, C.Asm):
            for o in s.outputs:
                target = o.e
                if isinstance(target, C.Name) and target.id not in local:
                    w.add(target.id)
                    if certain:
                        k.add(target.id)
                    if "+" in "".join(o.constraint.parts):
                        r.add(target.id)
                else:
                    r |= _reads_of(target) - local
            for o in s.inputs:
                r |= _reads_of(o.e) - local
            eff = True
        return r, w, k, eff

    if isinstance(stmt, C.Decl):           # a declaration of the body's own: it writes its variables
        r, eff = set(), False
        for d in stmt.items:
            if d.init is not None:
                r |= _reads_of(d.init)
                eff = eff or has_effects(d.init)
        w = k = {d.name for d in stmt.items}
    else:
        r, w, k, eff = stmt_flow(stmt, set(), True)
    node.reads = r & scope
    node.writes = w & scope
    node.kills = k & scope
    node.effects = eff


def _only_assigns(e):
    """Whether e's only effects are plain assignments to variables (no call, no volatile access)."""
    for x in walk_expr(e):
        if (isinstance(x, C.Call) and not is_pure_call(x)) or is_volatile_access(x):
            return False
        if isinstance(x, C.Assign) and not isinstance(x.target, C.Name):
            return False
    return True


def declared(fn):
    """The function's own variables: its parameters and what its body declares at the top level."""
    names = {p[1] for p in fn.params}
    for s in fn.body.body:
        if isinstance(s, C.Decl):
            names |= {d.name for d in s.items}
    return names


def falls_through(s):
    """Whether control can go from s to the statement after it."""
    if isinstance(s, (C.Goto, C.Return)):
        return False
    if isinstance(s, C.For) and s.cond is None and not _breaks(s.body):
        return False
    if isinstance(s, C.While) and isinstance(s.cond, C.Num) and s.cond.value and not _breaks(s.body):
        return False
    return True


def _breaks(s):
    if isinstance(s, C.Break):
        return True
    if isinstance(s, C.Block):
        return any(_breaks(x) for x in s.body)
    if isinstance(s, C.If):
        return _breaks(s.then) or (s.orelse is not None and _breaks(s.orelse))
    return False


def jumps(s):
    """The labels a statement can go to (a goto, an if's goto, a switch's cases, nested ones too)."""
    out = []
    if isinstance(s, C.Goto):
        out.append(s.label)
    elif isinstance(s, C.If):
        out += jumps(s.then) + (jumps(s.orelse) if s.orelse is not None else [])
    elif isinstance(s, C.Block):
        for x in s.body:
            out += jumps(x)
    elif isinstance(s, C.Switch):
        out += jumps(s.body)
    elif isinstance(s, (C.While, C.DoWhile, C.For)):
        out += jumps(s.body)
    return out


def returns(s):
    """Whether a statement can return (itself or a statement nested in it)."""
    if isinstance(s, C.Return):
        return True
    if isinstance(s, C.If):
        return returns(s.then) or (s.orelse is not None and returns(s.orelse))
    if isinstance(s, C.Block):
        return any(returns(x) for x in s.body)
    if isinstance(s, (C.Switch, C.While, C.DoWhile, C.For)):
        return returns(s.body)
    return False


class Graph:
    """The control-flow graph of a function's body statements, with liveness and reaching
    definitions computed on demand. EXIT is the index one past the last statement."""

    def __init__(self, fn):
        self.fn = fn
        self.body = fn.body.body
        self.scope = declared(fn)
        self.nodes = [Node(i, s) for i, s in enumerate(self.body)]
        for n in self.nodes:
            summarize(n.stmt, n, self.scope)
        self.exit = len(self.body)
        labels = {s.name: i for i, s in enumerate(self.body) if isinstance(s, C.Label)}
        self.labels = labels
        self.returning = set()
        for n in self.nodes:
            s = n.stmt
            targets = []
            for label in jumps(s):
                if label not in labels:
                    raise C.ParseError("a goto to %s, a label not in the body" % label)
                targets.append(labels[label])
            if falls_through(s):
                targets.append(n.index + 1)        # (past the last statement: EXIT)
            if returns(s):
                targets.append(self.exit)
                self.returning.add(n.index)
            n.succ = sorted(set(targets))
        for n in self.nodes:
            for t in n.succ:
                if t < len(self.nodes):
                    self.nodes[t].pred.append(n.index)

    def live(self):
        """{index: the variables live after node index} (a backward analysis; nothing is live at the
        exit but what a return reads, which its node reads itself)."""
        live_in = {n.index: set() for n in self.nodes}
        live_out = {n.index: set() for n in self.nodes}
        changed = True
        while changed:
            changed = False
            for n in reversed(self.nodes):
                out = set()
                for t in n.succ:
                    if t < len(self.nodes):
                        out |= live_in[t]
                inn = n.reads | (out - n.kills)
                if out != live_out[n.index] or inn != live_in[n.index]:
                    live_out[n.index], live_in[n.index] = out, inn
                    changed = True
        return live_out

    def reaching(self):
        """[{variable: the definitions reaching the start of node i}] for every node, as mappings that
        answer get(variable, default). A definition is the index of the node that writes the variable,
        or -1: the function's entry (a parameter). Bit sets: a definition is a bit."""
        nodes = self.nodes
        defs = [(-1, v) for v in sorted(self.scope)]
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
        ins, outs = [0] * len(nodes), [None] * len(nodes)
        changed = True
        while changed:
            changed = False
            for n in nodes:
                inn = entry if n.index == 0 else 0
                for p in n.pred:
                    if outs[p] is not None:
                        inn |= outs[p]
                out = gen[n.index] | (inn & ~kill[n.index])
                if inn != ins[n.index] or out != outs[n.index]:
                    ins[n.index], outs[n.index] = inn, out
                    changed = True
        return [Reaching(bits, defs, by_var) for bits in ins]


class Reaching:
    """The definitions reaching one node: get(variable) gives the set of node indices (-1: entry)."""
    __slots__ = ("bits", "defs", "by_var", "cache")

    def __init__(self, bits, defs, by_var):
        self.bits, self.defs, self.by_var, self.cache = bits, defs, by_var, {}

    def get(self, v, default=None):
        if v in self.cache:
            return self.cache[v]
        mask = self.bits & self.by_var.get(v, 0)
        if not mask:
            self.cache[v] = default
            return default
        out = set()
        k = 0
        while mask:
            if mask & 1:
                out.add(self.defs[k][0])
            mask >>= 1
            k += 1
        self.cache[v] = out
        return out
