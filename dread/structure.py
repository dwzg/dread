"""dread readable's structure stage: a cleaned-up lifted function's labels and gotos as if/else and
loops (dread.cparse trees, after dread.tidy).

The body is cut into basic blocks (a label starts one; a goto, a conditional goto or return, a return,
a switch or a loop that never ends closes one), their graph gets dominators and post-dominators,
and the blocks are emitted again from the entry. Where code never reaches a return (a task's loop
that never ends), the jumps back to the head of each cycle that nothing leaves count as ends for the
post-dominators (endless_ends): without them no branch there had a join.

- a loop (a header that dominates a block jumping back to it) is `for (;;) { ... }`, with `continue`
  for the jumps back and `break` for the jumps to the block after it (the target most of its exits
  go to), and refined afterwards: a test at its top that leaves it makes it a `while`, one at its end
  that goes back makes it a `do ... while`;
- a conditional jump is `if (...) { ... } else { ... }`, both arms emitted up to the branch's
  immediate post-dominator, where they meet; where they never meet (each way ends in a return or
  goes round a loop), the blocks the branch dominates that several ways reach follow the if, in
  reverse postorder (Ramsey's merge nodes), instead of going into the first arm that reaches them;
- a region never emits a block that a region around it emits later (its follow, the blocks after an
  if): a jump to one is a goto, unless falling off the region's end gets there;
- a block that consists of a return only, or a few statements and a return without a branch on the
  way (TAIL, the return counted), is emitted again wherever a jump goes to it: a goto is not worth it;
- a switch whose cases only jump or return (the lifters' jump tables: switch (index) { case 0: goto
  L_x; ... default: return r0; }) is a branch of many ways: its cases grouped by target, in the order
  of their blocks, each group's statements up to where the ways meet (the switch's immediate
  post-dominator, left with break) or falling into the next group's where the code goes there.
  Inside it, break leaves the switch and continue still goes to the loop around it.

Correct whatever is decided: every block is emitted once, under its label (a short way to a return
also repeated, without one, where jumps go to it), and every transfer of control is a fall-through
to the block that really follows, a break or continue of the loop it leaves, or a goto (C lets a goto
enter a block). What could not be structured stays a goto; statements nothing reaches (after a jump,
before the next label) and the labels no goto names are dropped at the end. The gate (dread readable
--check) checks the result as it checks everything else.

Measured on the 39 FreeRTOS builds of the bench (docs/todo.md, Structure): what gotos are left are
mostly blocks several ways share whose code branches before it returns (conditions a compiler
threaded into a lattice), then loops with exits to several places; irreducible flow is rare.
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


def jump_switch(s):
    """[(values, goto or return)] of a switch whose every case only jumps or returns (a lifter's jump
    table), values its case values in order (None for default); None for any other switch."""
    if not isinstance(s, C.Switch) or not isinstance(s.body, C.Block):
        return None
    groups, values = [], []
    for x in s.body.body:
        if isinstance(x, C.Case):
            values.append(x.value)
        elif isinstance(x, (C.Goto, C.Return)) and values:
            groups.append((values, x))
            values = []
        elif not isinstance(x, C.Note):
            return None
    return groups if groups and not values else None


def blocks_of(body):
    """(blocks, labels {name: block index}) of a statement list (declarations excluded), or None when it
    holds something the cut does not handle (a switch other than a jump table, a goto nested in another
    statement)."""
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
        groups = jump_switch(s) if isinstance(s, C.Switch) else None
        if isinstance(s, C.Case) or (isinstance(s, C.Switch) and groups is None) or (
                not isinstance(s, C.Switch) and not _is_branch(s) and not isinstance(s, (C.Goto, C.Return))
                and _has_jump(s)):
            return None
        if groups is not None:
            cur.term = ("switch", s, groups)
        elif isinstance(s, C.Goto):
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
        elif t[0] == "switch":
            cases = []                      # (values, target block or None, return or None)
            for values, x in t[2]:
                if isinstance(x, C.Goto):
                    if x.label not in labels:
                        return None
                    cases.append((values, labels[x.label], None))
                else:
                    cases.append((values, None, x))
            if not any(None in values for values, _, _ in cases):
                if b.index + 1 >= n:        # (no default, and nothing after it: not handled)
                    return None
                cases.append(([None], b.index + 1, None))  # (no case taken: on after the switch)
            b.term = ("switch", t[1], cases)
            b.succ = []
            for _, target, _ in cases:
                if target is not None and target not in b.succ:
                    b.succ.append(target)
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


def components(nodes, succ):
    """{node: component number} of the strongly connected components of the nodes (Tarjan's, without
    recursion), edges to nodes outside them ignored."""
    nodes = list(nodes)
    inside = set(nodes)
    index, low, comp, on, stack, count = {}, {}, {}, set(), [], 0
    for root in nodes:
        if root in index:
            continue
        index[root] = low[root] = len(index)
        stack.append(root)
        on.add(root)
        work = [(root, iter([s for s in succ(root) if s in inside]))]
        while work:
            v, it = work[-1]
            for w in it:
                if w not in index:
                    index[w] = low[w] = len(index)
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter([s for s in succ(w) if s in inside])))
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp[w] = count
                        if w == v:
                            break
                    count += 1
    return comp


def endless_ends(blocks, order):
    """The blocks that get an edge to the virtual exit for the post-dominators where code never reaches
    one (a task's loop that never ends: without it every block there lacks a post-dominator, and no
    branch in it has a join): in each cycle that nothing leaves, the blocks that jump back to its
    first block in reverse postorder (a natural loop's header) from inside it. An iteration then ends
    where it goes round, and the branches of the body meet as they would in a loop body that is run
    once (with the header itself as the end, a branch in the header had no join)."""
    reach, stack = set(), [b.index for b in blocks if not b.succ]
    reach.update(stack)
    while stack:
        for p in blocks[stack.pop()].pred:
            if p not in reach:
                reach.add(p)
                stack.append(p)
    stuck = [b for b in order if b not in reach]    # (their successors are stuck too)
    if not stuck:
        return set()
    comp = components(stuck, lambda b: blocks[b].succ)
    first, leaves = {}, set()
    for b in stuck:
        first.setdefault(comp[b], b)
        if any(comp[s] != comp[b] for s in blocks[b].succ):
            leaves.add(comp[b])
    return {p for c, h in first.items() if c not in leaves for p in blocks[h].pred if comp.get(p) == c}


class Shape:
    """The facts the emitter needs: dominators, immediate post-dominators, loops."""

    def __init__(self, blocks):
        self.blocks = blocks
        n = len(blocks)
        self.idom = dominators(range(n), 0, lambda b: blocks[b].succ, lambda b: blocks[b].pred)
        exit_ = n                           # a virtual exit after every block that leaves the function
        ends = endless_ends(blocks, reverse_postorder(blocks))     # (and after cycles nothing leaves)

        def rsucc(b):
            if b == exit_:
                return [x.index for x in blocks if not x.succ] + sorted(ends)
            return blocks[b].pred

        def rpred(b):
            if b == exit_:
                return []
            if b in ends:
                return blocks[b].succ + [exit_]
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

    def exits(self, header):
        """{block outside the loop at header: how many of its exits go there}."""
        body = self.loops[header]
        counts = {}
        for x in body:
            for s in self.blocks[x].succ:
                if s not in body:
                    counts[s] = counts.get(s, 0) + 1
        return counts

    def common_post_dominator(self, a, b):
        """The nearest block every way from a and from b to the end passes, or None."""
        chain = set()
        while True:
            chain.add(a)
            if self.ipdom.get(a, a) == a:
                break
            a = self.ipdom[a]
        while b not in chain:
            if self.ipdom.get(b, b) == b:
                return None
            b = self.ipdom[b]
        return b


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
    """Emits the blocks again as statements (see the module's docstring). merges: whether the blocks a
    branch dominates that several ways reach are emitted after its if, in reverse postorder, instead
    of in the first arm that reaches them (Ramsey's merge nodes, "Beyond Relooper", ICFP 2022):
    "nojoin" where the branch's ways never meet (the default), True after every branch, False never.
    After every branch cost more gotos than it saved on the bench (docs/decisions.md)."""

    def __init__(self, blocks, labels, merges="nojoin"):
        self.blocks, self.labels = blocks, labels
        self.shape = Shape(blocks)
        self.emitted = set()
        self.wanted = set()                 # labels a goto names
        self.merges = merges
        self.children = {}                  # the dominator tree: block -> the blocks it immediately dominates
        for b, d in sorted(self.shape.idom.items()):
            if b != d:
                self.children.setdefault(d, []).append(b)
        self.rpo = {b: k for k, b in enumerate(reverse_postorder(blocks))}
        for blk in blocks:                  # every block a label from the start: a jump decided after
            if not blk.labels:              # its target was emitted must find it there (pruned at the end)
                blk.labels.append("block_%d" % blk.index)

    def label_of(self, b):
        return self.blocks[b].labels[0]

    def returns_only(self, b):
        blk = self.blocks[b]
        return blk.term[0] == "return" and all(isinstance(s, C.Note) for s in blk.stmts)

    def tail(self, b):
        """The statements from block b to the return (or the statement that never ends) its blocks fall
        or jump to without a branch (a conditional return is a statement), when they are at most TAIL
        (the return counted) and plain; else None. Repeated where a jump goes to b, they do what the
        jump and the code there do."""
        out, seen, count = [], set(), 0
        while b not in seen:
            seen.add(b)
            blk = self.blocks[b]
            for s in blk.stmts:
                if isinstance(s, (C.Decl, C.Label, C.Case)):
                    return None
                if not isinstance(s, C.Note):
                    count += 1
            out += blk.stmts
            t = blk.term
            if t[0] == "return":
                return out + [t[1]] if count + 1 <= TAIL else None
            if t[0] == "stop":
                return out if count <= TAIL else None
            if t[0] == "cond" and isinstance(t[2], C.Return) and blk.succ:
                out.append(t[3])
                count += 1
            elif t[0] not in ("fall", "goto") or not blk.succ:
                return None                 # (a branch, a switch, the function's end)
            if count >= TAIL:
                return None
            b = blk.succ[0]
        return None                         # (a cycle)

    def repeat(self, b, loops, follow):
        """The statements of the blocks from b on, when a few (TAIL, a return, break or continue counted)
        without a branch lead to a return or a statement that never ends, to follow (where the code at
        the jump falls off to), to the innermost loop's header or to the block after the innermost loop
        or switch; else None. Repeated where a jump goes to b, they do what the jump and the code there
        do."""
        out, seen, count = [], set(), 0
        loop = innermost_loop(loops)
        while True:
            if b == follow:
                return out
            if loop is not None and b == loop[0]:
                return out + [C.Continue()] if count + 1 <= TAIL else None
            if loops and b == loops[-1][1]:
                return out + [C.Break()] if count + 1 <= TAIL else None
            if b in seen or (seen and b in self.shape.loops):
                return None                 # (a cycle, or into a loop on the way)
            seen.add(b)
            blk = self.blocks[b]
            for s in blk.stmts:
                if isinstance(s, (C.Decl, C.Label, C.Case)):
                    return None
                if not isinstance(s, C.Note):
                    count += 1
            out += blk.stmts
            t = blk.term
            if t[0] == "return":
                return out + [t[1]] if count + 1 <= TAIL else None
            if t[0] == "stop":
                return out if count <= TAIL else None
            if t[0] == "cond" and isinstance(t[2], C.Return) and blk.succ:
                out.append(t[3])
                count += 1
            elif t[0] not in ("fall", "goto") or not blk.succ:
                return None                 # (a branch, a switch, the function's end)
            if count > TAIL:
                return None
            b = blk.succ[0]

    def jump(self, target, loops, addr=None, follow=None):
        """The statements that send control to block target from inside the given loops and switches
        (innermost last: (header, exit), a switch's header None: break leaves the innermost of either,
        continue goes to the innermost loop's header), at the end of code that falls off to follow; a
        short way on (repeat) repeated."""
        if target is None:
            return []
        loop = innermost_loop(loops)
        if loop is not None and target == loop[0]:
            return [C.Continue(addr)]
        if loops and target == loops[-1][1]:
            return [C.Break(addr)]
        if self.returns_only(target):
            return [copy.deepcopy(self.blocks[target].term[1])]
        again = self.repeat(target, loops, follow)
        if again is not None:
            return copy.deepcopy(again)
        self.wanted.add(self.label_of(target))
        return [C.Goto(self.label_of(target), addr)]

    def region(self, b, follow, loops, stops=frozenset()):
        """Statements for the blocks from b on until control reaches follow (which the caller emits
        next: falling off the end of the list goes there). stops: blocks a region around this one emits
        later (its follow, the blocks after an if); a jump to one is a goto, a break or a continue."""
        out = []
        while True:
            if b is None or b == follow:
                return out
            loop = innermost_loop(loops)
            if loop is not None and b == loop[0] and b in self.emitted:
                out.append(C.Continue())
                return out
            if loops and b == loops[-1][1]:
                out.append(C.Break())
                return out
            if b in self.emitted or b in stops:
                out += self.jump(b, loops, follow=follow)
                return out
            shape = self.shape
            if b in shape.loops and not any(h == b for h, _ in loops):
                exit_ = self.loop_exit(b)
                body = self.region_loop(b, exit_, loops, _with(stops, follow))
                out.append(C.For(None, None, None, C.Block(body)))
                if exit_ is None:
                    return out
                b = exit_
                continue
            self.emitted.add(b)
            code, b = self.block(b, follow, loops, stops)
            out += code
            if b is None:
                return out              # (the loop's top decides the next: emitted, follow, loop's ends)

    def loop_exit(self, header):
        """The block after the loop at header (left with break): of the targets its exits go to (those
        that only return aside: a jump there is a return repeated), the nearest block the ways from all
        of them pass (their code, emitted where they leave, ends in a break), unless that only returns
        too; else the one most of its exits go to."""
        counts = self.shape.exits(header)
        if not counts:
            return None
        real = [t for t in sorted(counts) if not self.free(t)] or sorted(counts)
        meet = real[0]
        for t in real[1:]:
            meet = self.shape.common_post_dominator(meet, t)
            if meet is None:
                break
        if meet is not None and meet != self.shape.exit and meet not in self.shape.loops[header] and \
                not self.free(meet):
            return meet
        return min(real, key=lambda s: (-counts[s], s))

    def free(self, b):
        """Whether a jump to block b costs no goto: it only returns, or is a short way to a return."""
        return self.returns_only(b) or self.tail(b) is not None

    def join(self, b, loops):
        """Where the ways out of block b meet (its immediate post-dominator), None when that is outside
        the innermost loop (each way then leaves it on its own)."""
        j = self.shape.join(b)
        loop = innermost_loop(loops)
        if j is not None and loop is not None and j not in self.shape.loops.get(loop[0], ()):
            return None
        return j

    def merge_blocks(self, b, j, follow, loops, stops):
        """The blocks b immediately dominates that several ways reach (but its join, the follow and what
        a region around emits), in reverse postorder: emitted after b's if, where their ways meet."""
        if not self.merges or (self.merges == "nojoin" and j is not None):
            return []
        ends = {f[0] for f in loops} | {f[1] for f in loops}
        idom = self.shape.idom
        out = []
        for m in self.children.get(b, ()):
            if m in (j, follow) or m in stops or m in ends or m in self.emitted:
                continue
            ways = {p for p in self.blocks[m].pred if p in idom and not dominates(idom, m, p)}
            if len(ways) >= 2:
                out.append(m)
        return sorted(out, key=lambda m: self.rpo[m])

    def block(self, b, follow, loops, stops):
        """(statements, the block control goes to after them or None) for block b emitted here: its
        labels and statements, then its way out: a branch as if/else (the arms up to where they meet,
        the blocks after it that several ways reach following it), a switch, a jump or a return. None:
        the statements end every way, or fall off to the follow."""
        blk = self.blocks[b]
        out = [C.Label(name) for name in blk.labels] + list(blk.stmts)
        t = blk.term
        kind = t[0]
        if kind in ("return", "stop"):
            if kind == "return":
                out.append(t[1])
            return out, None
        if kind in ("fall", "goto"):
            return out, (blk.succ[0] if blk.succ else None)
        if kind == "switch":
            stmt, j = self.switch(b, follow, loops, stops)
            out.append(stmt)
            return out, j
        cond, then, stmt = t[1], t[2], t[3]
        if isinstance(then, C.Return):
            out.append(stmt)
            return out, (blk.succ[0] if blk.succ else None)
        taken, fall = blk.succ[0], (blk.succ[1] if len(blk.succ) > 1 else None)
        j = self.join(b, loops)
        merges = self.merge_blocks(b, j, follow, loops, stops)
        seq = merges + [j if j is not None else follow]     # (what comes after the if, in order)
        later = _with(_with(stops, follow), j)
        arms = [self.region(x, seq[0], loops, later | set(merges)) for x in (taken, fall)]
        out.append(make_if(cond, arms[0], arms[1], stmt.addr))
        for k, m in enumerate(merges):
            out += self.region(m, seq[k + 1], loops, later | set(merges[k + 1:]))
        return out, j

    def switch(self, b, follow, loops, stops):
        """(the switch that ends block b, the block after it or None): its cases grouped by target in
        the order of the targets' blocks, each group's statements up to the next group's target (into
        which it falls) or up to where all ways meet (left with break); the cases that return last,
        after a break where the group before them would fall into them."""
        blk = self.blocks[b]
        sw, cases = blk.term[1], blk.term[2]
        j = self.join(b, loops)
        after = j if j is not None else follow
        frames = loops + [(None, after)]
        groups, returns = {}, []
        for values, target, ret in cases:
            if target is None:
                returns.append((values, ret))
            else:
                groups.setdefault(target, []).extend(values)
        order = sorted(groups)
        later = _with(_with(stops, follow), j)
        body = []
        for k, target in enumerate(order):
            body += [C.Case(v) for v in groups[target]]
            nxt = order[k + 1] if k + 1 < len(order) else after
            stmts = self.region(target, nxt, frames, later)
            if k + 1 == len(order) and returns and not ends(stmts):
                stmts.append(C.Break())     # (not into the cases that return)
            body += stmts
        for values, ret in returns:
            body += [C.Case(v) for v in values] + [copy.deepcopy(ret)]
        return C.Switch(sw.e, C.Block(body), sw.addr), j

    def region_loop(self, header, exit_, loops, stops=frozenset()):
        """The body of the loop at header (its exit_ the block after it, left with break)."""
        inner = loops + [(header, exit_)]
        self.emitted.add(header)
        code, nxt = self.block(header, None, inner, stops)
        return code + (self.region(nxt, None, inner, stops) if nxt is not None else [])


def _with(stops, b):
    return stops | {b} if b is not None else stops


TAIL = 4            # statements (a return, break or continue counted) a jump repeats instead of a goto
                    # (Emitter.repeat; at 1 only ways without a statement, as a lone return was before
                    # 2026-10-22; docs/decisions.md)


def innermost_loop(frames):
    """The innermost loop (header, exit) of the frames, or None: a switch's frame has no header."""
    for frame in reversed(frames):
        if frame[0] is not None:
            return frame
    return None


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
        elif isinstance(s, (C.For, C.While, C.DoWhile, C.Switch)):
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
        elif isinstance(s, (C.While, C.DoWhile, C.Switch)):
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
        if isinstance(s, C.DoWhile) and not _list(s.body):     # (do {} while (c) is while (c);: both
            s = C.While(s.cond, C.Block([]), s.addr)            # test c first)
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
    """A continue in the statements that goes to the loop they are in (one inside a switch does)."""
    for s in stmts:
        if isinstance(s, C.Continue):
            return True
        if isinstance(s, C.If):
            if _continues_inside(_list(s.then)) or (s.orelse is not None and _continues_inside(_list(s.orelse))):
                return True
        if isinstance(s, (C.Block, C.Switch)) and _continues_inside(_list(s.body) if isinstance(s, C.Switch)
                                                                    else s.body):
            return True
    return False


def drop_dead(stmts):
    """Statements after one that control never falls out of go, up to the next label or case: nothing
    reaches them (an if whose arms both jump, then the jump to where the if would have gone on). One
    that holds a label stays: a goto may enter it."""
    out, dead = [], False
    for s in stmts:
        if isinstance(s, (C.Label, C.Case)) or _labels_in(s):
            dead = False
        if dead:
            continue
        if isinstance(s, C.If):
            s.then = C.Block(drop_dead(_list(s.then)))
            if s.orelse is not None:
                s.orelse = C.Block(drop_dead(_list(s.orelse)))
        elif isinstance(s, (C.For, C.While, C.DoWhile, C.Switch)):
            s.body = C.Block(drop_dead(_list(s.body)))
        out.append(s)
        dead = ends([s])
    return out


def _labels_in(s):
    """Whether a statement holds a label (a goto's target) inside it."""
    if isinstance(s, C.Label):
        return True
    if isinstance(s, C.If):
        return _labels_in(s.then) or (s.orelse is not None and _labels_in(s.orelse))
    if isinstance(s, (C.For, C.While, C.DoWhile, C.Switch)):
        return _labels_in(s.body)
    if isinstance(s, C.Block):
        return any(_labels_in(x) for x in s.body)
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
            elif isinstance(s, (C.For, C.While, C.DoWhile, C.Switch)):
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
        elif isinstance(s, (C.For, C.While, C.DoWhile, C.Switch)):
            names |= gotos_in(_list(s.body))
        elif isinstance(s, C.Block):
            names |= gotos_in(s.body)
    return names


def structure(fn, run=None):
    """The function's body with its gotos as if/else, loops and switches where they can be; unchanged
    when the body holds what the cut does not handle (a switch other than a jump table)."""
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
    out, wanted = flatten_else(out), None
    while True:                             # (code a pruned label led to can be dead in turn; before the
        out = drop_dead(out)                # loops are refined: a label first in a loop body hides its test)
        named = gotos_in(out)
        out = prune_labels(out, named)
        if named == wanted:
            break
        wanted = named
    out = refine_loops(out)
    fn.body.body[:] = head + out
    return fn
