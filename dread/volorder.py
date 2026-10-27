"""dread.volorder: compiler barriers in readable C where a compiler may reorder two volatile loads.

GCC 16 folds two volatile loads whose values one x86 instruction combines into that instruction (combine
takes the later load into it, then the earlier one, as a compare or an add of two memory operands
matches before reload; reload then loads the second first), so the loads change order. The lifter
puts a barrier in front of such an instruction (dread.lift_x86 insn: _needs_barrier), and needs none
where a volatile store lies between the loads and their use: the frame's stores are volatile in lifted
C. The readable passes take the frame's round trips away, and the loads meet again: FreeRTOS's
vHPETIRQHandler1 reads the HPET counter's high and low words into frame slots and adds a global to the
pair; its readable C combined the three in one statement, and GCC 16 read the low word first, which the
device log showed.

guard(tree, fn) puts the same barrier (an asm statement with a memory clobber, which combine moves no
load across) in front of a statement that reads two values of volatile loads: its own volatile reads
(*(volatile T *)..., the names the file defines as volatile memory, a member or an element through
them or through a pointer to volatile) and the variables assigned from one since the statements' run
began. A run ends at a label, a case, a branch or a loop (GCC combines within a block), a call, an asm
statement and a volatile store (combine moves no load across those). An access's address does not
count: x86 folds no load into an address. readable.py runs it last, where the target's layer says an
instruction may read memory beside a register (folds_memory_operands: x86); elsewhere no instruction
takes two values from memory, and readable C stays without barriers.
"""

import re

from dread import cparse as C
from dread.cflow import is_pure_call, is_volatile_access

DEFINE = re.compile(r"^#define (\w+) \(\(?\*?\(volatile ", re.M)    # (*(volatile T *)0x..u), ((volatile T *)0x..u)
BARRIER_TEXT = '__asm__ volatile ("" ::: "memory");'


def barrier():
    """A fresh asm statement with a memory clobber (parsed, so that it is the parser's own node)."""
    tree = C.parse("void f_(void) { %s }" % BARRIER_TEXT)
    fn = next(item for item in tree.items if isinstance(item, C.Function))
    return fn.body.body[0]


def volatile_names(tree, fn):
    """(names of volatile memory, names of pointers to volatile): the file's #define lines that name a
    volatile object or a pointer to one (dread.naming apply_globals), and the function's parameters and
    variables declared as a pointer to volatile."""
    names = set()
    for item in tree.items:
        if isinstance(item, C.Raw):
            names.update(DEFINE.findall(item.text))
    pointers = {name for ptype, name in (fn.params or ()) if "volatile" in ptype and "*" in ptype}
    for s in _statements(fn.body.body):
        if isinstance(s, C.Decl) and "volatile" in s.specs.split():
            pointers.update(d.name for d in s.items if d.ptr and "*" in d.ptr)
    return names, pointers


def _statements(body):
    for s in body:
        yield s
        for inner in _children(s):
            yield from _statements(inner)


def _children(s):
    """The statement lists inside a statement (an if's arms, a loop's body, a block's)."""
    out = []
    for part in (getattr(s, "then", None), getattr(s, "orelse", None), getattr(s, "body", None)):
        if isinstance(part, C.Block):
            out.append(part.body)
        elif isinstance(part, list):
            out.append(part)
        elif isinstance(part, C.Stmt):
            out.append([part])
    return out


class Scan:
    """What one statement's expressions do: volatile reads, the variables they read as values (not in
    an access's address), the variables they assign (and whether from a volatile read), and whether
    they write volatile memory or call."""

    def __init__(self, names, pointers):
        self.names, self.pointers = names, pointers
        self.reads, self.values, self.assigned, self.ends = 0, set(), {}, False

    def access(self, x):
        if is_volatile_access(x):
            return True
        if isinstance(x, C.Name):
            return x.id in self.names
        if isinstance(x, C.Member) and isinstance(x.base, C.Name):
            return x.base.id in (self.pointers if x.arrow else self.names) or (x.arrow and x.base.id in self.names)
        if isinstance(x, C.Index) and isinstance(x.base, C.Name):
            return x.base.id in self.names or x.base.id in self.pointers
        if isinstance(x, C.Unary) and x.op == "*" and isinstance(x.e, C.Name):
            return x.e.id in self.pointers
        return False

    def expr(self, e, address=False):
        """Count e's volatile reads and the names it reads as values; returns whether e reads volatile
        memory (for an assignment's value)."""
        if e is None:
            return False
        if isinstance(e, C.Assign):
            got = self.expr(e.value)
            target = e.target
            if self.access(target):
                self.ends = True            # (a volatile store: combine moves no load across it)
                if e.op != "=":
                    self.reads += 1
                self.inner(target)
            elif isinstance(target, C.Name):
                self.assigned[target.id] = got
                if e.op != "=":
                    self.values.add(target.id)
            else:
                self.inner(target)
            return got
        if isinstance(e, C.Call):
            if not is_pure_call(e):
                self.ends = True
            got = False
            for a in e.args:
                got = self.expr(a) or got
            return got
        if self.access(e):
            self.reads += 1
            self.inner(e)
            return True
        if isinstance(e, C.Name):
            if not address:
                self.values.add(e.id)
            return False
        if isinstance(e, (C.Post,)) or (isinstance(e, C.Unary) and e.op in ("++", "--")):
            inner = e.e
            if self.access(inner):
                self.ends = True
                self.reads += 1
                self.inner(inner)
                return True
            if isinstance(inner, C.Name):
                self.values.add(inner.id)
                self.assigned[inner.id] = False
            return False
        got = False
        for sub in _subexpressions(e):
            got = self.expr(sub, address) or got
        return got

    def inner(self, access):
        """The names an access's address reads: no values (x86 folds no load into an address)."""
        for sub in _subexpressions(access):
            self.expr(sub, address=True)


def _subexpressions(e):
    if isinstance(e, (C.Unary, C.Post, C.Cast, C.Sizeof)):
        return [e.e]
    if isinstance(e, C.Binary):
        return [e.l, e.r]
    if isinstance(e, C.Cond):
        return [e.c, e.t, e.f]
    if isinstance(e, C.Index):
        return [e.base, e.index]
    if isinstance(e, C.Member):
        return [e.base]
    if isinstance(e, C.Comma):
        return list(e.items)
    if isinstance(e, C.Call):
        return list(e.args) + [e.f]
    if isinstance(e, C.Assign):
        return [e.target, e.value]
    return []


def _exprs(s):
    """The expressions a simple statement evaluates (a declaration's initial values, a return's value)."""
    if isinstance(s, C.ExprStmt):
        return [s.e]
    if isinstance(s, C.Return):
        return [s.e]
    if isinstance(s, C.Decl):
        return [C.Assign("=", C.Name(d.name), d.init) for d in s.items if d.init is not None]
    return []


def guard_list(body, names, pointers, made):
    """Barriers into one statement list (and the lists inside its statements); made counts them."""
    out, loaded = [], set()
    for s in body:
        if isinstance(s, (C.Label, C.Case)):
            loaded = set()
        if isinstance(s, (C.If, C.While, C.DoWhile, C.For, C.Switch)):
            cond = getattr(s, "cond", None) if not isinstance(s, C.Switch) else s.e
            scan = Scan(names, pointers)
            for e in ([s.init] if isinstance(s, C.For) and isinstance(s.init, C.Expr) else []) + [cond]:
                scan.expr(e)
            if scan.reads + len(scan.values & loaded) >= 2:
                out.append(barrier())
                made[0] += 1
            for part in ("then", "orelse", "body"):
                inner = getattr(s, part, None)
                if isinstance(inner, C.Block):
                    inner.body = guard_list(inner.body, names, pointers, made)
                elif isinstance(inner, C.Stmt):     # (an arm of one statement, an else-if)
                    got = guard_list([inner], names, pointers, made)
                    setattr(s, part, got[0] if len(got) == 1 else C.Block(got))
            out.append(s)
            loaded = set()
            continue
        if isinstance(s, C.Block):
            s.body = guard_list(s.body, names, pointers, made)
            out.append(s)
            loaded = set()
            continue
        if isinstance(s, C.Asm):
            out.append(s)
            loaded = set()
            continue
        exprs = _exprs(s)
        if not exprs:
            out.append(s)
            continue
        scan = Scan(names, pointers)
        for e in exprs:
            scan.expr(e)
        if scan.reads + len(scan.values & loaded) >= 2:
            out.append(barrier())
            made[0] += 1
            loaded = set()
        out.append(s)
        if scan.ends:
            loaded = set()
            continue
        for name, from_load in scan.assigned.items():
            loaded.discard(name)
            if from_load:
                loaded.add(name)
    return out


def guard(tree, fn):
    """Put the barriers into fn (the module's docstring); returns how many."""
    names, pointers = volatile_names(tree, fn)
    made = [0]
    fn.body.body = guard_list(fn.body.body, names, pointers, made)
    return made[0]
