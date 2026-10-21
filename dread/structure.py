"""dread readable's structure stage: a cleaned-up lifted function's labels and gotos as if/else and
loops (dread.cparse trees, after dread.tidy).

The body is cut into basic blocks (a label starts one; a goto, a conditional goto or return, a return,
a switch or a loop that never ends closes one), their graph gets dominators and post-dominators,
and the blocks are emitted again from the entry:

- a loop (a header that dominates a block jumping back to it) is `for (;;) { ... }`, with `continue`
  for the jumps back and `break` for the jumps to the block after it (the target most of its exits
  go to), and refined afterwards: a test at its top that leaves it makes it a `while`, one at its end
  that goes back makes it a `do ... while`;
- a conditional jump is `if (...) { ... } else { ... }`, both arms emitted up to the branch's
  immediate post-dominator, where they meet;
- a block that consists of a return only is emitted again wherever a jump goes to it (a return is
  not worth a goto).

Correct whatever is decided: every block is emitted once, under its label, and every transfer of
control is a fall-through to the block that really follows, a break or continue of the loop it
leaves, or a goto (C lets a goto enter a block). What could not be structured stays a goto, and the
labels no goto names are dropped at the end. The gate (dread readable --check) checks the result as
it checks everything else.
"""
import copy

from dread import cparse as C


class Block:
    __slots__ = ("index", "labels", "stmts", "term", "succ", "pred", "addr")

    def __init__(self, index):
        self.index, self.labels, self.stmts, self.term = index, [], [], None
        self.succ, self.pred, self.addr = [], [], None


def _is_branch(s):
    return isinstance(s, C.If) and s.orelse is None and isinstance(s.then, (C.Goto, C.Return))


def _stops(s):
    """A statement control never leaves (a loop without a way out)."""
    if isinstance(s, C.For) and s.cond is None:
        return not _has_break(s.body)
    if isinstance(s, C.While) and isinstance(s.cond, C.Num) and s.cond.value:
        return not _has_break(s.body)
    return False


def _has_break(s):
    if isinstance(s, C.Break):
        return True
    if isinstance(s, C.Block):
        return any(_has_break(x) for x in s.body)
    if isinstance(s, C.If):
        return _has_break(s.then) or (s.orelse is not None and _has_break(s.orelse))
    return False


def _has_jump(s):
    """A goto, return or break somewhere inside a statement that is no branch of its own: such a
    statement is not structured (its block keeps it, and the function is left as it is)."""
    if isinstance(s, (C.Goto,)):
        return True
    if isinstance(s, C.Block):
        return any(_has_jump(x) or isinstance(x, (C.Label, C.Case)) for x in s.body)
    if isinstance(s, C.If):
        return _has_jump(s.then) or (s.orelse is not None and _has_jump(s.orelse))
    return False


def blocks_of(body):
    """(blocks, labels {name: block index}) of a statement list (declarations excluded), or None when it
    holds something the cut does not handle (a switch, a goto nested in another statement)."""
    blocks = [Block(0)]
    for s in body:
        cur = blocks[-1]
        if isinstance(s, C.Label):
            if cur.stmts or cur.term is not None:
                cur = Block(len(blocks))
                blocks.append(cur)
            cur.labels.append(s.name)
            if cur.addr is None:
                cur.addr = s.addr
            continue
        if cur.term is not None:
            cur = Block(len(blocks))
            blocks.append(cur)
        if isinstance(s, (C.Switch, C.Case)) or (not _is_branch(s) and not isinstance(s, (C.Goto, C.Return)) and
                                                 _has_jump(s)):
            return None
        if isinstance(s, C.Goto):
            cur.term = ("goto", s.label, s)
        elif _is_branch(s):
            cur.term = ("cond", s.cond, s.then, s)
        elif isinstance(s, C.Return):
            cur.term = ("return", s)
        elif _stops(s):
            cur.stmts.append(s)
            cur.term = ("stop",)
        else:
            cur.stmts.append(s)
    labels = {}
    for b in blocks:
        for name in b.labels:
            labels[name] = b.index
    n = len(blocks)
    for b in blocks:
        t = b.term
        if t is None:
            b.succ = [b.index + 1] if b.index + 1 < n else []
            b.term = ("fall",)
        elif t[0] == "goto":
            if t[1] not in labels:
                return None
            b.succ = [labels[t[1]]]
        elif t[0] == "cond":
            nxt = [b.index + 1] if b.index + 1 < n else []
            if isinstance(t[2], C.Goto):
                if t[2].label not in labels:
                    return None
                b.succ = [labels[t[2].label]] + nxt
            else:
                b.succ = nxt                # (a conditional return: the other way falls through)
        else:
            b.succ = []
    for b in blocks:
        for s in b.succ:
            blocks[s].pred.append(b.index)
    return blocks, labels


def reverse_postorder(blocks, entry=0, succ=None):
    succ = succ or (lambda b: blocks[b].succ)
    seen, order = set(), []
    stack = [(entry, iter(succ(entry)))]
    seen.add(entry)
    while stack:
        node, it = stack[-1]
        for nxt in it:
            if nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, iter(succ(nxt))))
                break
        else:
            stack.pop()
            order.append(node)
    return list(reversed(order))


def dominators(nodes, entry, succ, pred):
    """{node: immediate dominator} (Cooper, Harvey and Kennedy) of the nodes reachable from entry."""
    order = reverse_postorder(None, entry, succ)
    pos = {n: i for i, n in enumerate(order)}
    idom = {entry: entry}

    def intersect(a, b):
        while a != b:
            while pos[a] > pos[b]:
                a = idom[a]
            while pos[b] > pos[a]:
                b = idom[b]
        return a
    changed = True
    while changed:
        changed = False
        for n in order[1:]:
            ps = [p for p in pred(n) if p in idom and p in pos]
            if not ps:
                continue
            new = ps[0]
            for p in ps[1:]:
                new = intersect(p, new)
            if idom.get(n) != new:
                idom[n] = new
                changed = True
    return idom


def dominates(idom, a, b):
    while True:
        if a == b:
            return True
        if idom.get(b, b) == b:
            return False
        b = idom[b]


class Shape:
    """The facts the emitter needs: dominators, immediate post-dominators, loops."""

    def __init__(self, blocks):
        self.blocks = blocks
        n = len(blocks)
        self.idom = dominators(range(n), 0, lambda b: blocks[b].succ, lambda b: blocks[b].pred)
        exit_ = n                           # a virtual exit after every block that leaves the function

        def rsucc(b):
            if b == exit_:
                return [x.index for x in blocks if not x.succ]
            return blocks[b].pred

        def rpred(b):
            if b == exit_:
                return []
            return blocks[b].succ if blocks[b].succ else [exit_]
        self.ipdom = dominators(range(n + 1), exit_, rsucc, rpred)
        self.exit = exit_
        self.loops = {}                     # header -> set of blocks
        for b in blocks:
            if b.index not in self.idom:
                continue
            for s in b.succ:
                if dominates(self.idom, s, b.index):        # a back edge b -> s
                    body = self.loops.setdefault(s, {s})
                    stack = [b.index]
                    while stack:
                        x = stack.pop()
                        if x not in body:
                            body.add(x)
                            stack += [p for p in blocks[x].pred if p in self.idom]

    def join(self, b):
        """The block both ways of a branch at b meet at (its immediate post-dominator), or None."""
        j = self.ipdom.get(b)
        return None if j is None or j == self.exit else j

    def loop_exit(self, header):
        """The block after a loop: the target outside it that most of its exits go to."""
        body = self.loops[header]
        counts = {}
        for x in body:
            for s in self.blocks[x].succ:
                if s not in body:
                    counts[s] = counts.get(s, 0) + 1
        if not counts:
            return None
        return min(counts, key=lambda s: (-counts[s], s))


def collapse(blocks):
    """A branch whose way on is a block holding nothing but another branch (reached from it alone)
    becomes one branch with both conditions: if (a) goto T; if (b) goto T;  ->  if (a || b) goto T;
    and if (a) goto T; if (b) goto U; T:  ->  if (a || !b) goto T (U then the way on). The second
    condition is evaluated only when the first fails, as || does."""
    changed = True
    while changed:
        changed = False
        for b in blocks:
            t = b.term
            if t is None or t[0] != "cond" or not isinstance(t[2], C.Goto) or len(b.succ) != 2:
                continue
            f = blocks[b.succ[1]]
            if f.index == b.index or f.pred != [b.index] or any(not isinstance(x, C.Note) for x in f.stmts):
                continue
            ft = f.term
            if ft is None or ft[0] != "cond" or not isinstance(ft[2], C.Goto) or len(f.succ) != 2:
                continue
            target = b.succ[0]
            if f.succ[0] == target:
                cond, rest = C.Binary("||", t[1], ft[1]), f.succ[1]
            elif f.succ[1] == target:
                from dread.structure import negate
                cond, rest = C.Binary("||", t[1], negate(ft[1])), f.succ[0]
            else:
                continue
            b.term = ("cond", cond, t[2], C.If(cond, t[2], None, t[3].addr))
            b.succ = [target, rest]
            for s in f.succ:
                blocks[s].pred = [b.index if p == f.index else p for p in blocks[s].pred]
                if b.index in blocks[s].pred[1:] and blocks[s].pred.count(b.index) > 1:
                    pass
            f.pred, f.succ = [], []
            changed = True
    return blocks


class Emitter:
    def __init__(self, blocks, labels):
        self.blocks, self.labels = blocks, labels
        self.shape = Shape(blocks)
        self.emitted = set()
        self.wanted = set()                 # labels a goto names
        for blk in blocks:                  # every block a label from the start: a jump decided after
            if not blk.labels:              # its target was emitted must find it there (pruned at the end)
                blk.labels.append("block_%d" % blk.index)

    def label_of(self, b):
        return self.blocks[b].labels[0]

    def returns_only(self, b):
        blk = self.blocks[b]
        return blk.term[0] == "return" and all(isinstance(s, C.Note) for s in blk.stmts)

    def jump(self, target, loops, addr=None):
        """The statements that send control to block target from inside the given loops (innermost
        last: (header, exit))."""
        if target is None:
            return []
        if loops and target == loops[-1][0]:
            return [C.Continue(addr)]
        if loops and target == loops[-1][1]:
            return [C.Break(addr)]
        if self.returns_only(target):
            return [copy.deepcopy(self.blocks[target].term[1])]
        self.wanted.add(self.label_of(target))
        return [C.Goto(self.label_of(target), addr)]

    def region(self, b, follow, loops):
        """Statements for the blocks from b on until control reaches follow (which the caller emits
        next: falling off the end of the list goes there)."""
        out = []
        while True:
            if b is None or b == follow:
                return out
            if loops and b == loops[-1][0] and b in self.emitted:
                out.append(C.Continue())
                return out
            if loops and b == loops[-1][1]:
                out.append(C.Break())
                return out
            if b in self.emitted:
                out += self.jump(b, loops)
                return out
            shape = self.shape
            if b in shape.loops and not any(h == b for h, _ in loops):
                exit_ = shape.loop_exit(b)
                body = self.region_loop(b, exit_, loops)
                out.append(C.For(None, None, None, C.Block(body)))
                if exit_ is None:
                    return out
                b = exit_
                continue
            self.emitted.add(b)
            blk = self.blocks[b]
            for name in blk.labels:
                out.append(C.Label(name))
            out += blk.stmts
            t = blk.term
            kind = t[0]
            if kind in ("return", "stop"):
                if kind == "return":
                    out.append(t[1])
                return out
            if kind in ("fall", "goto"):
                nxt = blk.succ[0] if blk.succ else None
                if nxt is None:
                    return out
                b = nxt                     # (the loop's top decides: emitted, follow, loop's ends)
                continue
            # a conditional jump or return
            cond, then, stmt = t[1], t[2], t[3]
            if isinstance(then, C.Return):
                out.append(stmt)
                b = blk.succ[0] if blk.succ else None
                continue
            taken, fall = blk.succ[0], (blk.succ[1] if len(blk.succ) > 1 else None)
            j = shape.join(b)
            if j is not None and loops and j not in shape.loops.get(loops[-1][0], ()):
                j = None                    # (they meet outside the loop: each arm leaves it on its own)
            arm_t = self.region(taken, j, loops)
            arm_f = self.region(fall, j, loops)
            out.append(make_if(cond, arm_t, arm_f, stmt.addr))
            if j is None:
                return out
            b = j

    def region_loop(self, header, exit_, loops):
        inner = loops + [(header, exit_)]
        self.emitted.add(header)
        blk = self.blocks[header]
        out = [C.Label(name) for name in blk.labels] + list(blk.stmts)
        t = blk.term
        kind = t[0]
        if kind in ("return", "stop"):
            if kind == "return":
                out.append(t[1])
            return out
        if kind in ("fall", "goto"):
            nxt = blk.succ[0] if blk.succ else None
            out += self.region(nxt, None, inner) if nxt is not None else []
            return out
        cond, then, stmt = t[1], t[2], t[3]
        if isinstance(then, C.Return):
            out.append(stmt)
            out += self.region(blk.succ[0] if blk.succ else None, None, inner)
            return out
        taken, fall = blk.succ[0], (blk.succ[1] if len(blk.succ) > 1 else None)
        j = self.shape.join(header)
        if j is not None and j not in self.shape.loops[header]:
            j = None
        arm_t = self.region(taken, j, inner)
        arm_f = self.region(fall, j, inner)
        out.append(make_if(cond, arm_t, arm_f, stmt.addr))
        if j is not None:
            out += self.region(j, None, inner)
        return out


def negate(cond):
    """!cond, written simply: a comparison turned round, !!x as x."""
    flip = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}
    if isinstance(cond, C.Binary) and cond.op in flip:
        return C.Binary(flip[cond.op], cond.l, cond.r)
    if isinstance(cond, C.Unary) and cond.op == "!":
        return cond.e
    if isinstance(cond, C.Binary) and cond.op in ("&&", "||"):
        return C.Binary("||" if cond.op == "&&" else "&&", negate(cond.l), negate(cond.r))
    return C.Unary("!", cond)


def ends(stmts):
    """Whether control never falls off the end of the statements."""
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (C.Return, C.Goto, C.Break, C.Continue)):
        return True
    if isinstance(last, C.If) and last.orelse is not None:
        return ends(_list(last.then)) and ends(_list(last.orelse))
    if isinstance(last, C.For) and last.cond is None:
        return not _has_break(last.body)
    return False


def _list(s):
    return s.body if isinstance(s, C.Block) else [s]


def make_if(cond, then, orelse, addr=None):
    """if (cond) then else orelse, the empty arm left out (the condition turned round when it is the
    then arm), an else after an arm that never falls through written after the if instead."""
    if not then and orelse:
        cond, then, orelse = negate(cond), orelse, []
    if not then and not orelse:
        return C.ExprStmt(C.Cast("void", cond), addr) if _has_effects(cond) else C.Empty(addr)
    s = C.If(cond, C.Block(then), C.Block(orelse) if orelse else None, addr)
    return s


def _has_effects(e):
    from dread.cflow import has_effects
    return has_effects(e)


def flatten_else(stmts):
    """if (c) { ... return; } else { rest }  ->  if (c) { ... return; } rest (one level less)."""
    out = []
    for s in stmts:
        if isinstance(s, C.If):
            s.then = C.Block(flatten_else(_list(s.then)))
            if s.orelse is not None:
                s.orelse = C.Block(flatten_else(_list(s.orelse)))
                if ends(s.then.body):
                    rest = s.orelse.body
                    s.orelse = None
                    out.append(s)
                    out += rest
                    continue
        elif isinstance(s, (C.For, C.While, C.DoWhile)):
            s.body = C.Block(flatten_else(_list(s.body)))
        out.append(s)
    return out


def refine_loops(stmts):
    """for (;;) { if (c) break; body }  ->  while (!c) { body };
    for (;;) { body; if (c) continue; break; } and for (;;) { body; if (!c) break; } -> do { body } while (c)."""
    out = []
    for s in stmts:
        if isinstance(s, C.If):
            s.then = C.Block(refine_loops(_list(s.then)))
            if s.orelse is not None:
                s.orelse = C.Block(refine_loops(_list(s.orelse)))
        elif isinstance(s, (C.While, C.DoWhile)):
            s.body = C.Block(refine_loops(_list(s.body)))
        elif isinstance(s, C.For) and s.init is None and s.cond is None and s.step is None:
            body = refine_loops(_list(s.body))
            while body and isinstance(body[-1], C.Continue):
                body.pop()
            first = body[0] if body else None
            last = body[-1] if body else None
            if isinstance(first, C.If) and first.orelse is None and _list(first.then) and \
                    isinstance(_list(first.then)[-1], C.Break) and len(_list(first.then)) == 1 and \
                    not _breaks_inside(body[1:]):
                s = C.While(negate(first.cond), C.Block(body[1:]), first.addr)
            elif isinstance(last, C.If) and last.orelse is None and len(_list(last.then)) == 1 and \
                    isinstance(_list(last.then)[0], C.Break) and not _breaks_inside(body[:-1]) and \
                    not _continues_inside(body[:-1]):
                s = C.DoWhile(C.Block(body[:-1]), negate(last.cond), last.addr)
            elif isinstance(last, C.Break) and len(body) >= 2 and isinstance(body[-2], C.If) and \
                    body[-2].orelse is None and _list(body[-2].then) and \
                    isinstance(_list(body[-2].then)[-1], C.Continue) and len(_list(body[-2].then)) > 1:
                # ...; if (A) { X; continue; } break;  ->  ...; if (!A) break; X;
                test = body[-2]
                rest = _list(test.then)[:-1]
                body = body[:-2] + [C.If(negate(test.cond), C.Block([C.Break()]), None, test.addr)] + rest
                s = C.For(None, None, None, C.Block(body))
                again = refine_loops([s])
                s = again[0]
            elif isinstance(last, C.Break) and len(body) >= 2:
                # body; if (a) continue; if (b) continue; break;  ->  do { body } while (a || b);
                k = len(body) - 1
                conds = []
                while k > 0 and isinstance(body[k - 1], C.If) and body[k - 1].orelse is None and \
                        len(_list(body[k - 1].then)) == 1 and isinstance(_list(body[k - 1].then)[0], C.Continue):
                    conds.insert(0, body[k - 1].cond)
                    k -= 1
                if conds and not _breaks_inside(body[:k]) and not _continues_inside(body[:k]):
                    cond = conds[0]
                    for c in conds[1:]:
                        cond = C.Binary("||", cond, c)
                    s = C.DoWhile(C.Block(body[:k]), cond, body[k].addr)
                else:
                    s.body = C.Block(body)
            else:
                s.body = C.Block(body)
        out.append(s)
    return out


def _breaks_inside(stmts):
    """A break in the statements that leaves the loop they are in (not one of a nested loop)."""
    for s in stmts:
        if isinstance(s, C.Break):
            return True
        if isinstance(s, C.If):
            if _breaks_inside(_list(s.then)) or (s.orelse is not None and _breaks_inside(_list(s.orelse))):
                return True
        if isinstance(s, C.Block) and _breaks_inside(s.body):
            return True
    return False


def _continues_inside(stmts):
    for s in stmts:
        if isinstance(s, C.Continue):
            return True
        if isinstance(s, C.If):
            if _continues_inside(_list(s.then)) or (s.orelse is not None and _continues_inside(_list(s.orelse))):
                return True
        if isinstance(s, C.Block) and _continues_inside(s.body):
            return True
    return False


def prune_labels(stmts, wanted):
    """Labels no goto names go; goto L directly before L: goes."""
    def walk(xs):
        out = []
        for s in xs:
            if isinstance(s, C.Label) and s.name not in wanted:
                continue
            if isinstance(s, C.If):
                s.then = C.Block(walk(_list(s.then)))
                if s.orelse is not None:
                    s.orelse = C.Block(walk(_list(s.orelse)))
            elif isinstance(s, (C.For, C.While, C.DoWhile)):
                s.body = C.Block(walk(_list(s.body)))
            out.append(s)
        k = 0
        while k + 1 < len(out):
            if isinstance(out[k], C.Goto) and isinstance(out[k + 1], C.Label) and out[k].label == out[k + 1].name:
                del out[k]
                continue
            k += 1
        return out
    return walk(stmts)


def gotos_in(stmts):
    names = set()
    for s in stmts:
        if isinstance(s, C.Goto):
            names.add(s.label)
        elif isinstance(s, C.If):
            names |= gotos_in(_list(s.then)) | (gotos_in(_list(s.orelse)) if s.orelse is not None else set())
        elif isinstance(s, (C.For, C.While, C.DoWhile)):
            names |= gotos_in(_list(s.body))
        elif isinstance(s, C.Block):
            names |= gotos_in(s.body)
    return names


def structure(fn, run=None):
    """The function's body with its gotos as if/else and loops where they can be; unchanged when the
    body holds what the cut does not handle (a switch: not yet)."""
    body = fn.body.body
    k = 0
    while k < len(body) and isinstance(body[k], (C.Decl, C.Note)):
        k += 1
    head, rest = body[:k], body[k:]
    cut = blocks_of(rest)
    if cut is None or not rest:
        return fn
    blocks, labels = cut
    collapse(blocks)
    em = Emitter(blocks, labels)
    out = em.region(0, None, [])
    missing = [b for b in range(len(blocks)) if b not in em.emitted and b in em.shape.idom]
    if missing:                             # (reachable but not emitted: leave the function as it is)
        return fn
    out = flatten_else(out)
    out = prune_labels(out, gotos_in(out))     # (before the loops are refined: a label first in a loop
    out = refine_loops(out)                    # body hides its test)
    fn.body.body[:] = head + out
    return fn
