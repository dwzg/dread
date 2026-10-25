"""dread readable's structure stage: a cleaned-up lifted function's labels and gotos as if/else and
loops (dread.cparse trees, after dread.tidy).

The body is cut into basic blocks (a label starts one; a goto, a conditional goto or return, a return,
a switch or a loop that never ends closes one), their graph gets dominators and post-dominators,
and the blocks are emitted again from the entry. Where code never reaches a return (a task's loop
that never ends), the jumps back to the head of each cycle that nothing leaves count as ends for the
post-dominators (endless_ends): without them no branch there had a join.

- a loop (a header that dominates a block jumping back to it) is `for (;;) { ... }`, with `continue`
  for the jumps back and `break` for the jumps to the block after it (the target most of its exits
  go to), and refined afterwards (refine_loops): the run of tests at its top that leave it makes it a
  `while` with their negations joined by && (strncpy's `while (n != 0)`, a break further in staying),
  a test at its top that leaves by a return, or by a break behind statements, where the loop has no
  other way out that falls on, makes it a `while` with those statements behind it (`while (p != end)
  ...; return d;`), a test at its end that goes back makes it a `do ... while`; then a do-while whose
  condition is proved to hold whenever control reaches it is a `while` (entered: the condition
  written back through the statements before it and the tests around it, given to z3: GCC's loops
  rotated behind a test of their bound), and a while loop whose body ends with a step of a variable
  its condition reads, behind an init of it, is a `for` loop (counted: `for (p = s; p != end; p++)`);
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

Then the ifs are arranged (arrange_if), innermost first: an if's arms are the way its branch jumps
and the way it falls through, and compilers put an if's then arm where the test falls through (if
(!c) goto else; then...; goto end; else: ...). So the arm that falls through comes first, under the
condition turned round; an arm that never falls through (a return, a break, a goto) ends the if as an
early exit and the other arm follows it; an else that is one if is an else if, and an arm of tests
that exit (if (a) X; if (b) Y; rest) is folded into one where it stands as an else. Of these the one
least deeply nested is taken (nesting: the deepest level, then the levels summed; the fall-through
arm first on a tie). Without it an else-if chain came out as a staircase, each test in the then arm
of the one before (if (x != 1) { if (x != 2) { ... } else B } else A: FreeRTOS GCC -Os's
vTimerPeriodicISRTests 18 levels deep against its source's 3, tiny_print 9 against 3 at -O0). A goto
that ends an arm and goes where the if falls off to anyway is dropped (drop_fall_gotos).

Where early exits that repeat a short way (TAIL) take GUARDS levels or more off a function's deepest
nesting, its checks become such exits: a block several checks leave for that returns or never ends
(a stack check's hook) is repeated where they jump instead of following the outermost if, and the
short way after an if (vAssertCalled(...); r4 = 0; return r4;) is repeated at the end of an arm that
falls into it, where that takes the if a level less deep (short_way). The function is made both ways
and the exits are taken for all of it or not at all; taken for fewer levels, they mostly made
functions flatter than their sources, with code repeated (docs/decisions.md).

After the loops are refined, what the repeats and the clean-up wrote into several arms is written once
(arms_pass): neighbouring arms of an else-if chain with the same code are one arm, their tests joined
with || (FreeRTOS's eTaskGetState: else if (list == delayed || list == overflow) r = 2;, where one block
of the compiler's was written into each), an arm whose code is the last else's drops its test where the
test has no effect, a run of ifs without else whose arms are the same code and never fall through is
one if, and what every arm of an if / else (or a whole chain with a last else) begins with goes in front
of it where the tests have no effect and read nothing it writes, what every arm ends with behind it.

Correct whatever is decided: every block is emitted once, under its label (a short way to a return
also repeated, without one, where jumps go to it), and every transfer of control is a fall-through
to the block that really follows, a break or continue of the loop it leaves, or a goto (C lets a goto
enter a block). What could not be structured stays a goto; statements nothing reaches (after a jump,
before the next label) and the labels no goto names are dropped at the end. The gate (dread readable
--check) checks the result as it checks everything else.

Measured on the 39 FreeRTOS builds of the bench (docs/todo.md, Structure): what gotos are left are
mostly blocks several ways share whose code branches before it returns (conditions a compiler
threaded into a lattice), then loops with exits to several places; irreducible flow is rare. On four
of them (GCC -Os, clang -O2, RISC-V GCC -Os, GCC -O0) against their sources (docs/todo.md,
Staircases): what still nests deeper than the source is mostly code the compiler inlined or unrolled
(clang -O2's prvInitialiseNewTask, a name copied in an unrolled loop whose exits all go to one block,
12 levels against 3); a run of tests with statements between them that all leave for one longer
block stays nested, as the conditions a source joined with && were.
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
    becomes one branch with both conditions: if (a) goto T; if (b) goto T; U:  ->  if (a || b) goto T
    (U the way on); and if (a) goto T; if (b) goto U; T:  ->  if (!a && b) goto U (T the way on). The
    second condition is evaluated only when the first fails, as || and && do. The way on stays the
    block that comes next in the code, where compilers put an if's then arm (arrange_if writes that
    arm first: if (a || !b) T else U)."""
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
                cond, goto, succ = C.Binary("||", t[1], ft[1]), t[2], [target, f.succ[1]]
            elif f.succ[1] == target:
                cond, goto, succ = C.Binary("&&", negate(t[1]), ft[1]), ft[2], [f.succ[0], target]
            else:
                continue
            b.term = ("cond", cond, goto, C.If(cond, goto, None, t[3].addr))
            for s in set(b.succ) | set(f.succ):             # (one entry per edge, as blocks_of makes them)
                blocks[s].pred = [p for p in blocks[s].pred if p not in (b.index, f.index)]
            b.succ = succ
            for s in succ:
                blocks[s].pred.append(b.index)
            f.pred, f.succ = [], []
            changed = True
    return blocks


class Emitter:
    """Emits the blocks again as statements (see the module's docstring). merges: whether the blocks a
    branch dominates that several ways reach are emitted after its if, in reverse postorder, instead
    of in the first arm that reaches them (Ramsey's merge nodes, "Beyond Relooper", ICFP 2022):
    "nojoin" where the branch's ways never meet (the default), True after every branch, False never.
    After every branch cost more gotos than it saved on the bench (docs/decisions.md). guards: such a
    block that is a short way to a return or a statement that never ends (free) is no merge but is
    repeated where the ways jump, so that each branch to it is an early exit (structure.GUARDS)."""

    def __init__(self, blocks, labels, merges="nojoin", guards=False):
        self.blocks, self.labels, self.guards = blocks, labels, guards
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
        """The blocks b immediately dominates that several ways reach (but its join, the follow, what a
        region around emits and, with guards, the short ways), in reverse postorder: emitted after b's
        if, where their ways meet."""
        if not self.merges or (self.merges == "nojoin" and j is not None):
            return []
        ends = {f[0] for f in loops} | {f[1] for f in loops}
        idom = self.shape.idom
        out = []
        for m in self.children.get(b, ()):
            if m in (j, follow) or m in stops or m in ends or m in self.emitted:
                continue
            if self.guards and self.free(m):
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


GUARDS = 3          # levels a function's deepest nesting must fall by for the early exits that repeat short
                    # ways (Emitter's guards, arrange's copies) to be taken (structure; None: never;
                    # docs/decisions.md)
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
    """!cond, written simply: a comparison turned round, !!x as x, (s, c) as (s, !c)."""
    flip = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}
    if isinstance(cond, C.Comma) and cond.items:
        return C.Comma(cond.items[:-1] + [negate(cond.items[-1])])
    if isinstance(cond, C.Binary) and cond.op in flip:
        return C.Binary(flip[cond.op], cond.l, cond.r)
    if isinstance(cond, C.Unary) and cond.op == "!":
        return cond.e
    if isinstance(cond, C.Binary) and cond.op in ("&&", "||"):
        return C.Binary("||" if cond.op == "&&" else "&&", negate(cond.l), negate(cond.r))
    return C.Unary("!", cond)


def ends(stmts):
    """Whether control never falls off the end of the statements (comments and empty statements after
    the last one do not count)."""
    k = len(stmts)
    while k and isinstance(stmts[k - 1], (C.Note, C.Empty)):
        k -= 1
    if not k:
        return False
    last = stmts[k - 1]
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


def _real(stmts):
    """The statements but comments and empty statements."""
    return [s for s in stmts if not isinstance(s, (C.Note, C.Empty))]


def nesting(stmts, level=0):
    """(the deepest nesting of control statements, the sum of every statement's level) of a statement
    list at level: an if, loop or switch is one level deeper than the statement around it; an else
    that holds one if alone (else if) is no deeper, as dread.cparse prints it and dread.cmetrics
    counts it; labels, case labels, comments and empty statements are no statements."""
    deepest, total = 0, 0
    for s in stmts:
        if isinstance(s, (C.Note, C.Empty, C.Label, C.Case)):
            continue
        if isinstance(s, C.Block):
            d, t = nesting(s.body, level)
            deepest, total = max(deepest, d), total + t
            continue
        total += level
        if isinstance(s, C.If):
            deepest = max(deepest, level + 1)
            arms = [(_list(s.then), level + 1)]
            if s.orelse is not None:
                inner = _real(_list(s.orelse))
                arms.append((inner, level) if len(inner) == 1 and isinstance(inner[0], C.If)
                            else (_list(s.orelse), level + 1))
            for arm, at in arms:
                d, t = nesting(arm, at)
                deepest, total = max(deepest, d), total + t
        elif isinstance(s, (C.For, C.While, C.DoWhile, C.Switch)):
            d, t = nesting(_list(s.body), level + 1)
            deepest, total = max(deepest, level + 1, d), total + t
    return deepest, total


def fold(stmts):
    """A statement list as an else arm can hold it: if (c) X; rest, X never falling through, is
    if (c) X else rest, and the rest folded in turn, so that an arm of such tests becomes an else-if
    chain (each X one level less deep, the rest as deep as before). Comments before the test stay
    before it."""
    k = 0
    while k < len(stmts) and isinstance(stmts[k], C.Note):
        k += 1
    if k < len(stmts) and isinstance(stmts[k], C.If) and stmts[k].orelse is None and _real(stmts[k + 1:]) and \
            ends(_list(stmts[k].then)):
        s = stmts[k]
        return stmts[:k] + [C.If(s.cond, s.then, C.Block(fold(stmts[k + 1:])), s.addr)]
    return stmts


def arrange(stmts, way=None, copies=False):
    """The ifs of a statement list arranged (arrange_if), from its end (each if knows what follows
    it), inside loops and switches too. way: what control runs when it falls off the list's end, when
    that is a short way that ends (short_way), else None."""
    out = []
    for s in reversed(stmts):
        after = short_way(out if ends(out) or way is None else out + way)
        if isinstance(s, C.If):
            out = arrange_if(s, after, copies) + out
            continue
        if isinstance(s, (C.For, C.While, C.DoWhile, C.Switch)):
            s.body = C.Block(arrange(_list(s.body), None, copies))
        elif isinstance(s, C.Block):
            s.body = arrange(s.body, after, copies)
        out = [s] + out
    return out


def short_way(stmts):
    """The statements as a copy can repeat them: when control never falls off their end and they are
    at most TAIL statements (nested ones counted), without a declaration, a loop, a switch or a case,
    and labels only in front of a statement (the copy leaves them out); else None."""
    if not ends(stmts):
        return None
    out, count = [], 0
    for s in stmts:
        if isinstance(s, C.Label):
            continue
        n = _plain_count(s)
        if n is None or count + n > TAIL:
            return None
        count += n
        out.append(s)
    return out


def _plain_count(s):
    """The statements in s (s counted), or None when it holds what short_way does not copy."""
    if isinstance(s, (C.Note, C.Empty)):
        return 0
    if isinstance(s, C.If):
        n = 1
        for arm in [s.then] + ([s.orelse] if s.orelse is not None else []):
            for x in _list(arm):
                m = _plain_count(x)
                if m is None:
                    return None
                n += m
        return n
    if isinstance(s, (C.Decl, C.Label, C.Case, C.Block, C.For, C.While, C.DoWhile, C.Switch)):
        return None
    return 1


def arrange_if(s, way=None, copies=False):
    """The statements for if (c) T else F (T the way the branch jumps, F the way it falls through, as
    Emitter.block makes it), its arms arranged first: of
      if (!c) F; T      when F never falls through (the arm that falls through first, as compilers
      if (c) T; F       when T never falls through   lay out an if's then arm, and an early exit),
      if (!c) F else T  and  if (c) T else F  (T and F folded into an else-if chain where they can be),
    and, where a short way follows the if (way), the first two with that way repeated at the end of an
    arm that falls through (an else arm made an early exit), the one least deeply nested (nesting:
    the deepest level, then the levels summed), the earlier on a tie. An arm of comments only is an
    empty arm. Every one runs what the if ran."""
    then = arrange(_list(s.then), way, copies)
    orelse = arrange(_list(s.orelse), way, copies) if s.orelse is not None else []
    if not _real(then) and not _real(orelse):
        return [make_if(s.cond, [], [], s.addr)]
    if not _real(orelse):
        return [C.If(s.cond, C.Block(then), None, s.addr)]
    if not _real(then):
        return [C.If(negate(s.cond), C.Block(orelse), None, s.addr)]
    cond, other = s.cond, negate(s.cond)
    choices = []
    if ends(orelse):
        choices.append([C.If(other, C.Block(orelse), None, s.addr)] + then)
    if ends(then):
        choices.append([C.If(cond, C.Block(then), None, s.addr)] + orelse)
    choices.append([C.If(other, C.Block(orelse), C.Block(fold(then)), s.addr)])
    choices.append([C.If(cond, C.Block(then), C.Block(fold(orelse)), s.addr)])
    best = min(enumerate(choices), key=lambda kc: (nesting(kc[1]), kc[0]))[1]
    if way is not None and copies:
        extended = []
        if not ends(orelse):
            extended.append([C.If(other, C.Block(orelse + copy.deepcopy(way)), None, s.addr)] + then)
        if not ends(then):
            extended.append([C.If(cond, C.Block(then + copy.deepcopy(way)), None, s.addr)] + orelse)
        if extended:
            bc = min(enumerate(extended), key=lambda kc: (nesting(kc[1]), kc[0]))[1]
            if nesting(bc)[0] < nesting(best)[0]:
                return bc
    return best


def _break_if(s):
    """Whether s is if (c) break; (the break alone in its arm, no else)."""
    return isinstance(s, C.If) and s.orelse is None and len(_list(s.then)) == 1 and \
        isinstance(_list(s.then)[0], C.Break)


def leading_breaks(body):
    """(tests, k) of a loop's body: the run of if (c) break; it begins with (comments and empty
    statements among them), and the index of the first statement after the run."""
    tests, k, j = [], 0, 0
    while j < len(body):
        s = body[j]
        if isinstance(s, (C.Note, C.Empty)):
            j += 1
            continue
        if not _break_if(s):
            break
        tests.append(s)
        j += 1
        k = j
    return tests, k


def leading_exit(body):
    """(k, test, after) of a loop's body that begins (after comments) with if (c) A, A never falling
    through, where the body has no other break and A no break or continue of this loop but a last
    break: the loop's only way out that falls on is that test, so the loop is while (!c) { the rest }
    and A, its last break left out, follows it. None for any other body."""
    k = 0
    while k < len(body) and isinstance(body[k], (C.Note, C.Empty)):
        k += 1
    if k >= len(body):
        return None
    s = body[k]
    if not isinstance(s, C.If) or s.orelse is not None:
        return None
    arm = list(_list(s.then))
    if not ends(arm) or _breaks_inside(body[k + 1:]):
        return None
    j = len(arm)
    while j and isinstance(arm[j - 1], (C.Note, C.Empty)):
        j -= 1
    after = arm[:j - 1] + arm[j:] if isinstance(arm[j - 1], C.Break) else arm
    if _breaks_inside(after) or _continues_inside(after):
        return None
    return k, s, after


def refine_loops(stmts):
    """for (;;) { if (a) break; if (b) break; body }  ->  while (!a && !b) { body } (other breaks may
    follow in the body: they leave the while as they left the for);
    for (;;) { if (c) { A } body }, A never falling through and the body without a break -> while (!c)
    { body } A (A's last break left out: the loop's only way out that falls on is the test);
    for (;;) { body; if (c) continue; break; } and for (;;) { body; if (!c) break; } -> do { body } while (c).
    The run of tests at the head becomes the condition in its order, each tested only where those
    before it failed, as && evaluates them; comments among them go in front of the loop."""
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
            last = body[-1] if body else None
            tests, k = leading_breaks(body)
            exit_ = leading_exit(body) if not tests else None
            if tests:
                cond = negate(tests[0].cond)
                for t in tests[1:]:
                    cond = C.Binary("&&", cond, negate(t.cond))
                out.extend(x for x in body[:k] if isinstance(x, C.Note))
                s = C.While(cond, C.Block(body[k:]), tests[0].addr)
            elif exit_ is not None:
                k, test, after = exit_
                out.extend(x for x in body[:k] if isinstance(x, C.Note))
                out.append(C.While(negate(test.cond), C.Block(body[k + 1:]), test.addr))
                out.extend(after)
                continue
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


# --- do-while loops entered with their condition true, counted loops ---

ENTRY_NODES = 240       # the most nodes of what an entry proof asks (the loop's condition written back through
                        # the statements before it): beyond it the loop stays a do-while


class _Refused(Exception):
    pass


class _Stop(Exception):
    """A case label met on the way back: what must hold there must hold whatever the switch saw (g)."""

    def __init__(self, g):
        Exception.__init__(self)
        self.g = g


class Entry:
    """Whether a do-while's condition holds whenever control reaches the loop, proved: the condition
    written back through the statements before it (a weakest precondition: an assignment's value put
    for its variable, an if's two ways each with its test, what a loop or an asm writes, or an
    assignment from memory or a call, any value at all), through the if arms around it (the arm's test
    holds there) up to the function's start, the head of a loop around it (its condition holds there,
    nothing more) or a case label, then given to z3 (dread.cexpr: true for every value of what is left,
    and defined). Only conditions of variables and constants without an effect (the while loop
    evaluates it once more: at its entry), only variables whose address the function never takes (a
    call could write them), and only in a function without labels (a goto would bring other values
    to the loop). What cannot be followed refuses the loop (it stays a do-while)."""

    def __init__(self, fn):
        from dread import tidy
        self.fn = fn
        self.ctx = tidy.context(fn)
        self.decls = tidy.declarations(fn)
        self.fresh = 0
        self.taken = address_taken(fn.body.body)

    def condition(self, cond):
        """Whether a loop's condition may be followed: variables and constants, no effect."""
        from dread.cflow import has_effects, reads_memory
        return not has_effects(cond) and not reads_memory(cond) and not (_names(cond) & self.taken)

    def havoc(self, g, v):
        """g with v any value: a fresh variable of v's type for it."""
        from dread import tidy
        if v not in _names(g):
            return g
        if v in self.taken or v not in self.ctx.types:
            raise _Refused(v)
        self.fresh += 1
        name = "__entry%d" % self.fresh
        self.ctx.types[name] = self.ctx.types[v]
        return tidy.substitute(g, v, C.Name(name))

    def assigned(self, g, v, value):
        """g with v given value (converted to v's declared type, as the assignment converts it)."""
        from dread import tidy
        from dread.cflow import has_effects, reads_memory
        if v not in _names(g):
            return g
        t = tidy.type_of(self.fn, v, self.decls)
        if t is None or v in self.taken or has_effects(value) or reads_memory(value) or \
                self.ctx.ctype(t) is None:
            return self.havoc(g, v)
        return self._small(tidy.substitute(g, v, C.Cast(" ".join(t.split()), value)))

    def _small(self, g):
        from dread.cflow import walk_expr
        if sum(1 for _ in walk_expr(g)) > ENTRY_NODES:
            raise _Refused("too large")
        return g

    def back(self, stmts, g):
        """What must hold at the start of stmts for g to hold at their end (control falling off it);
        None where control never falls off a statement on the way (the end is not reached)."""
        from dread import tidy
        from dread.cflow import expr_writes, has_effects, reads_memory
        if ends(stmts):
            return None
        for s in reversed(stmts):
            if isinstance(s, (C.Note, C.Empty)):
                continue
            if isinstance(s, C.Case):
                raise _Stop(g)
            if isinstance(s, (C.Label, C.Goto, C.Return, C.Break, C.Continue)) or \
                    (isinstance(s, C.For) and s.cond is None and not _has_break(s.body)):
                raise _Refused("control")       # (no labels in the function: a statement nothing reaches)
            if isinstance(s, C.ExprStmt):
                e = s.e
                if tidy.is_assign(s) and not expr_writes(e.value, set()):
                    g = self.assigned(g, e.target.id, e.value)
                    continue
                for v in sorted(expr_writes(e, set())):
                    g = self.havoc(g, v)
                continue
            if isinstance(s, C.Decl):
                for d in reversed(s.items):
                    g = self.assigned(g, d.name, d.init) if d.init is not None and not d.dims else \
                        self.havoc(g, d.name)
                continue
            if isinstance(s, C.Block):
                if any(isinstance(x, C.Decl) for x in s.body):     # (its own variables: their names may
                    for v in sorted(_written_in(s)):                # hide the function's)
                        g = self.havoc(g, v)
                else:
                    g = self.back(s.body, g)
                if g is None:
                    return None
                continue
            if isinstance(s, C.If):
                c = s.cond
                written = expr_writes(c, set())
                then = self.back(_list(s.then), g)
                orelse = self.back(_list(s.orelse), g) if s.orelse is not None else g
                if then is None and orelse is None:
                    return None
                pure = not has_effects(c) and not reads_memory(c) and not written and \
                    not (_names(c) & self.taken)
                if then is g and orelse is g:
                    pass                        # (neither way writes what g reads)
                elif not pure:
                    g = then if orelse is None else orelse if then is None else \
                        C.Binary("&&", then, orelse)
                elif then is None:
                    g = C.Binary("||", c, orelse)
                elif orelse is None:
                    g = C.Binary("||", negate(c), then)
                else:
                    g = C.Binary("&&", C.Binary("||", negate(c), then), C.Binary("||", c, orelse))
                for v in sorted(written):
                    g = self.havoc(g, v)
                g = self._small(g)
                continue
            if isinstance(s, (C.While, C.DoWhile, C.For, C.Switch, C.Asm)):
                for v in sorted(_written_in(s)):
                    g = self.havoc(g, v)
                continue
            raise _Refused(type(s).__name__)
        return g

    def valid(self, g):
        """Whether g holds for every value of its variables (and is defined there): tested, then proved."""
        from dread import cexpr
        return cexpr.equivalent(C.Num("1"), g, self.ctx, as_bool=True)


def entered(fn, stmts):
    """The do-while loops of the statements (nested ones too) whose condition holds whenever control
    reaches them (Entry) as while loops: do { B } while (c) is while (c) { B } where the first test
    holds, and c is evaluated once more without an effect. Nothing in a function with a label."""
    if _has_label(stmts):
        return stmts
    entry = Entry(fn)

    def obligation(prefix, g, up):
        try:
            got = entry.back(prefix, g)
        except _Refused:
            return None
        except _Stop as stop:
            return stop.g
        return None if got is None else up(got)

    def walk(stmts, up):
        for k, s in enumerate(stmts):
            def here(g, k=k):
                return obligation(stmts[:k], g, up)
            if isinstance(s, C.DoWhile) and entry.condition(s.cond):
                g = here(copy.deepcopy(s.cond))
                try:
                    if g is not None and entry.valid(g):
                        stmts[k] = s = C.While(s.cond, s.body, s.addr)
                except _Refused:
                    pass
            if isinstance(s, C.If):
                c = s.cond
                if entry.condition(c):
                    walk(_list(s.then), lambda g, c=c, here=here: here(C.Binary("||", negate(c), g)))
                    if s.orelse is not None:
                        walk(_list(s.orelse), lambda g, c=c, here=here: here(C.Binary("||", c, g)))
                else:
                    from dread.cflow import expr_writes
                    inner = here if not expr_writes(c, set()) else (lambda g: None)
                    walk(_list(s.then), inner)
                    if s.orelse is not None:
                        walk(_list(s.orelse), inner)
            elif isinstance(s, (C.While, C.For)) and s.cond is not None and entry.condition(s.cond):
                walk(_list(s.body), lambda g, c=s.cond: C.Binary("||", negate(c), g))
            elif isinstance(s, (C.While, C.DoWhile, C.For, C.Switch)):
                walk(_list(s.body), lambda g: g)
            elif isinstance(s, C.Block):
                walk(s.body, here)
    walk(stmts, lambda g: g)
    return stmts


def counted(stmts, taken=frozenset()):
    """init; while (c) { body; step; } as for (init; c; step) { body }, in every statement list: the
    step the body's last statement, an assignment to a variable v whose value reads v without a call (v
    = v + 1, p = *(volatile u32 *)(p + 4)), c reads v, the body has no continue of the loop (a continue
    of the for loop would run the step), and the init the last statement before the loop that writes
    v, an assignment of a value that does not read v; statements between them are moved over only where
    they are expressions that neither read nor write v nor write what the init reads, and have no
    effect where the init's value has one; v is no variable whose address the function takes (taken: a
    call between could read it). The for statement keeps the loop's instruction and absorbs
    the init's and the step's (the reading form's margin shows the init's), and remembers both
    (init_addr, step_addr: what dread.naming identities says their variable is defined at). Every
    path runs what it ran, in its order."""
    from dread import tidy
    from dread.cflow import has_effects, walk_expr
    out = list(stmts)
    for s in out:
        if isinstance(s, C.If):
            s.then = C.Block(counted(_list(s.then), taken))
            if s.orelse is not None:
                s.orelse = C.Block(counted(_list(s.orelse), taken))
        elif isinstance(s, (C.While, C.DoWhile, C.For, C.Switch)):
            s.body = C.Block(counted(_list(s.body), taken))
        elif isinstance(s, C.Block):
            s.body = counted(s.body, taken)
    k = 0
    while k < len(out):
        s = out[k]
        k += 1
        if not isinstance(s, C.While):
            continue
        body = _list(s.body)
        j = len(body)
        while j and isinstance(body[j - 1], (C.Note, C.Empty)):
            j -= 1
        if not j:
            continue
        step = body[j - 1]
        if not tidy.is_assign(step):
            continue
        v = step.e.target.id
        if v in taken or not tidy.reads_count(step.e.value, v) or any(isinstance(x, (C.Call, C.Assign, C.Post)) or (
                isinstance(x, C.Unary) and x.op in ("++", "--")) for x in walk_expr(step.e.value)):
            continue
        if not tidy.reads_count(s.cond, v) or _continues_inside(body[:j - 1]):
            continue
        i, between = k - 2, []
        while i >= 0:
            x = out[i]
            if isinstance(x, (C.Note, C.Empty)):
                i -= 1
                continue
            if tidy.is_assign(x) and x.e.target.id == v:
                break
            if not isinstance(x, C.ExprStmt) or tidy.reads_count(x.e, v) or v in _writes(x.e):
                i = -1
                break
            between.append(x)
            i -= 1
        if i < 0:
            continue
        init = out[i]
        value = init.e.value
        if tidy.reads_count(value, v) or _writes(value):
            continue
        reads = _names(value)
        if any(_writes(x.e) & reads or (has_effects(value) and has_effects(x.e)) for x in between):
            continue
        f = C.For(init.e, s.cond, step.e, C.Block(body[:j - 1] + body[j:]), s.addr,
                  init_addr=init.addr, step_addr=step.addr)
        C.absorb(f, init)
        C.absorb(f, step)
        out[k - 1] = f
        del out[i]
        k -= 1
    return out


# --- merged arms and hoisted statements ---

def same_code(a, b):
    """Whether two statement lists are the same code: the same text without the instructions' comments
    (comments and empty statements left out), wherever their instructions are."""
    return _code_text(a) == _code_text(b)


def _code_text(stmts):
    return C.unparse(C.Block(_real(stmts)), notes=False, style="readable")


def _pure(e):
    from dread.cflow import has_effects, reads_memory
    return not has_effects(e) and not reads_memory(e)


class Arm:
    """One way of an if / else if chain: its test (None for the last else), its statements, and the if
    it was (its instruction: the branch's; None for the last else)."""
    __slots__ = ("cond", "body", "node")

    def __init__(self, cond, body, node):
        self.cond, self.body, self.node = cond, body, node


def chain_of(s):
    """([Arm] of an if and of the ifs its else arms hold alone (else if), the last else an Arm without a
    test where there is one; the comments in front of each else if)."""
    arms, notes = [Arm(s.cond, list(_list(s.then)), s)], []
    o = s.orelse
    while o is not None:
        body = _list(o)
        real = _real(body)
        if len(real) == 1 and isinstance(real[0], C.If):
            notes += [x for x in body if isinstance(x, C.Note)]
            arms.append(Arm(real[0].cond, list(_list(real[0].then)), real[0]))
            o = real[0].orelse
            continue
        arms.append(Arm(None, list(body), None))
        break
    return arms, notes


def chain_if(arms):
    """The if / else if chain of arms (the last one without a test: the last else)."""
    if arms[-1].cond is None:
        out, rest = C.Block(arms[-1].body), arms[:-1]
    else:
        out, rest = None, arms
    for k in range(len(rest) - 1, -1, -1):
        a = rest[k]
        out = C.If(a.cond, C.Block(a.body), out, a.node.addr if a.node is not None else None)
        if k:
            out = C.Block([out])
    return out


def merged_arms(arms):
    """Neighbouring arms of the same code as one, their tests joined with ||: if (a) X else if (b) X is if
    (a || b) X (b evaluated only where a fails, as before); an arm that is the last else's code drops
    its test where the test has no effect (else if (c) X else X: else X). The statements kept absorb
    the instructions of the copies they stand for, the if those of the test it took in
    (cparse.absorb). Returns (arms, whether any merged)."""
    arms = list(arms)
    merged = False
    k = 0
    while k + 1 < len(arms):
        a, b = arms[k], arms[k + 1]
        if b.cond is not None and same_code(a.body, b.body):
            _absorb_copies(a.body, b.body)
            C.absorb(a.node, b.node)
            arms[k] = Arm(C.Binary("||", a.cond, b.cond), a.body, a.node)
            del arms[k + 1]
            merged = True
            continue
        k += 1
    while len(arms) >= 2 and arms[-1].cond is None and same_code(arms[-2].body, arms[-1].body) and \
            _pure(arms[-2].cond):
        _absorb_copies(arms[-1].body, arms[-2].body)
        del arms[-2]
        merged = True
    return arms, merged


def _absorb_copies(kept, other):
    """The statements of kept stand for the same statements of other (cparse.copy_of: dread.naming
    identities counts their instructions as the definitions they were), nested ones too, where those
    are other instructions."""
    for x, y in zip(_real(kept), _real(other)):
        C.copy_of(x, y)
        for xi, yi in zip(_nested_lists(x), _nested_lists(y)):
            _absorb_copies(xi, yi)


def _nested_lists(s):
    if isinstance(s, C.If):
        return [_list(s.then)] + ([_list(s.orelse)] if s.orelse is not None else [])
    if isinstance(s, (C.While, C.DoWhile, C.For, C.Switch)):
        return [_list(s.body)]
    if isinstance(s, C.Block):
        return [s.body]
    return []


def hoisted(arms, plain=False, taken=frozenset()):
    """(before, arms, after) of an if / else if chain with a last else: the statements every arm begins
    with, moved in front of the if, and those every arm ends with, moved behind it. In front only an
    expression statement behind no label (a goto there would skip it) that writes nothing a test reads,
    where no test has an effect (no call, no access to memory: the stages' accesses are volatile) or the
    statement is an assignment of a value without one, of variables no test writes and whose address
    the function never takes (taken: a test's call cannot reach them): each test has its value, and
    the statement does what it did in any arm. Behind the if any
    statement but a label, a case or a declaration: it ran last on every way that fell off an arm, and
    does so behind the if. In a chain of more than two ways no arm but the last else is left empty (the
    last else then goes); an if / else with an arm left empty is turned round (make_if). Nothing leaves
    arms that declare variables of their own (it would leave their scope). plain: arms of plain
    statements only (no jump, label, loop, switch or declaration: dread.tidy's conditional blocks). The
    statement kept absorbs the instructions of the others."""
    if len(arms) < 2 or arms[-1].cond is not None:
        return [], arms, []
    lists = [list(a.body) for a in arms]
    if any(isinstance(y, C.Decl) for x in lists for y in x) or (plain and any(not _plain_arm(x) for x in lists)):
        return [], arms, []                 # (an arm that declares: what leaves it would leave its scope)
    conds = [a.cond for a in arms[:-1]]
    reads, written = set(), set()
    for c in conds:
        reads |= _names(c)
        written |= _writes(c)
    pure = all(_pure(c) for c in conds)

    def keeps(new):                         # (a chain's arms but its last stay non-empty)
        return len(new) == 2 or all(_real(x) for x in new[:-1])

    def plain_value(st):                    # (an assignment of a value without an effect, of variables
        from dread import tidy              # no test writes and no call can reach)
        return tidy.is_assign(st) and _pure(st.e.value) and \
            not (({st.e.target.id} | _names(st.e.value)) & (written | set(taken)))
    before, after = [], []
    while True:
        firsts = [_first_real(x) for x in lists]
        if None in firsts:
            break
        heads = [x[i] for x, i in zip(lists, firsts)]
        if not isinstance(heads[0], C.ExprStmt) or any(isinstance(y, C.Label) for x, i in zip(lists, firsts)
                                                          for y in x[:i]):
            break
        if not all(same_code([heads[0]], [h]) for h in heads[1:]) or _writes(heads[0].e) & reads or \
                not (pure or plain_value(heads[0])):
            break
        new = [[y for y in x[:i] if not isinstance(y, C.Note)] + x[i + 1:] for x, i in zip(lists, firsts)]
        if not keeps(new):
            break
        for h in heads[1:]:
            _absorb_copies([heads[0]], [h])
        before += [y for y in lists[0][:firsts[0]] if isinstance(y, C.Note)] + [heads[0]]
        lists = new
    while True:
        lasts = [_last_real(x) for x in lists]
        if None in lasts:
            break
        tails = [x[i] for x, i in zip(lists, lasts)]
        if isinstance(tails[0], (C.Label, C.Case, C.Decl)) or not all(same_code([tails[0]], [t]) for t in tails[1:]):
            break
        new = [x[:i] + x[i + 1:] for x, i in zip(lists, lasts)]
        if not keeps(new):
            break
        for t in tails[1:]:
            _absorb_copies([tails[0]], [t])
        after.insert(0, tails[0])
        lists = new
    if not before and not after:
        return [], arms, []
    out = [Arm(a.cond, x, a.node) for a, x in zip(arms, lists)]
    if len(out) > 2 and not _real(out[-1].body):
        out = out[:-1]                      # (a last else left empty: none)
    return before, out, after


def _first_real(stmts):
    for k, s in enumerate(stmts):
        if not isinstance(s, (C.Note, C.Empty, C.Label)):
            return k
    return None


def _last_real(stmts):
    for k in range(len(stmts) - 1, -1, -1):
        if not isinstance(stmts[k], (C.Note, C.Empty)):
            return k if not isinstance(stmts[k], C.Label) else None
    return None


def _plain_arm(stmts):
    return not any(isinstance(s, (C.Goto, C.Label, C.Return, C.Break, C.Continue, C.Case, C.While, C.DoWhile,
                                  C.For, C.Switch, C.Decl, C.Asm)) for s in _all_stmts(stmts))


def arms_pass(stmts, plain=False, chained=False, taken=None):
    """Merged arms and hoisted statements in every statement list: an if / else if chain's neighbouring
    arms of the same code as one (merged_arms), what both arms of an if / else begin or end with in
    front of or behind it (hoisted), and a run of ifs without else whose arms are the same code that
    never falls through as one if (if (a) return x; if (b) return x; is if (a || b) return x;: the
    second test ran only where the first failed). Returns (statements, whether anything changed).
    plain: only hoisting, only for ifs whose arms are plain statements (dread.tidy's conditional
    blocks); blocks that bind registers are left alone."""
    from dread import tidy
    if taken is None:
        taken = address_taken(stmts)
    changed = False
    out = []
    for s in stmts:
        if isinstance(s, C.If):
            if isinstance(s.then, (C.Goto, C.Return)) or isinstance(s.orelse, (C.Goto, C.Return)):
                out.append(s)                   # (a branch stays one: if (c) goto L;)
                continue
            for arm in ("then", "orelse"):
                x = getattr(s, arm)
                if isinstance(x, C.Block):      # (an else of one if is an else if: its chain is this one)
                    real = _real(x.body)
                    x.body, c = arms_pass(x.body, plain, arm == "orelse" and len(real) == 1 and
                                          isinstance(real[0], C.If), taken)
                elif x is not None:             # (else if, as the parser reads it: its chain is this one)
                    got, c = arms_pass([x], plain, True, taken)
                    setattr(s, arm, got[0] if len(got) == 1 else C.Block(got))
                else:
                    c = False
                changed = changed or c
            arms, notes = chain_of(s)
            merged = False
            if not plain:
                arms, merged = merged_arms(arms)
            before, arms, after = hoisted(arms, plain, taken) if not chained else ([], arms, [])
            if merged or before or after:
                changed = True
                out += notes + before + _rebuilt_if(arms, s.addr) + after
                continue
            out.append(s)
            continue
        if isinstance(s, (C.While, C.DoWhile, C.For, C.Switch)):
            body, c = arms_pass(_list(s.body), plain, False, taken)
            s.body = C.Block(body)
            changed = changed or c
        elif isinstance(s, C.Block) and not tidy.binds_registers(s):
            s.body, c = arms_pass(s.body, plain, False, taken)
            changed = changed or c
        out.append(s)
    if not plain:
        joined = _joined_exits(out)
        changed = changed or len(joined) != len(out)
        out = joined
    return out, changed


def _rebuilt_if(arms, addr):
    """The statements for a chain after merging and hoisting: an if / else with an arm left empty turned
    round (make_if), both empty its test alone where it has an effect."""
    if len(arms) == 2 and arms[1].cond is None:
        if not _real(arms[0].body) and not _real(arms[1].body) and _pure(arms[0].cond):
            return [x for x in arms[0].body + arms[1].body if isinstance(x, C.Note)]
        return [make_if(arms[0].cond, arms[0].body, arms[1].body, addr)]
    if len(arms) == 1 and arms[0].cond is None:
        return arms[0].body
    return [chain_if(arms)]


def _joined_exits(stmts):
    """if (a) X; if (b) X; (X the same code, never falling through; comments between) as if (a || b) X."""
    out = []
    for s in stmts:
        k = len(out) - 1
        while k >= 0 and isinstance(out[k], (C.Note, C.Empty)):
            k -= 1
        prev = out[k] if k >= 0 else None
        if isinstance(s, C.If) and s.orelse is None and isinstance(prev, C.If) and prev.orelse is None and \
                ends(_list(prev.then)) and same_code(_list(prev.then), _list(s.then)):
            _absorb_copies(_list(prev.then), _list(s.then))
            C.absorb(prev, s)
            prev.cond = C.Binary("||", prev.cond, s.cond)
            continue
        out.append(s)
    return out


def _names(e):
    from dread.cflow import walk_expr
    return {x.id for x in walk_expr(e) if isinstance(x, C.Name)}


def address_taken(stmts):
    """The variables whose address the statements take (&v, &v.f, &v[k]: a call may reach them)."""
    from dread.cflow import walk_expr
    out = set()
    for s in _all_stmts(stmts):
        for e in _stmt_exprs(s):
            for x in walk_expr(e):
                if isinstance(x, C.Unary) and x.op == "&":
                    out |= _names(x.e)
    return out


def _writes(e):
    from dread.cflow import expr_writes
    return expr_writes(e, set())


def _stmt_exprs(s):
    """The expressions a statement holds itself (not those of the statements inside it)."""
    if isinstance(s, (C.ExprStmt, C.Return)):
        return [s.e] if s.e is not None else []
    if isinstance(s, (C.If, C.While, C.DoWhile)):
        return [s.cond]
    if isinstance(s, C.For):
        return [x for x in (s.init if not isinstance(s.init, C.Decl) else None, s.cond, s.step) if x is not None]
    if isinstance(s, C.Switch):
        return [s.e]
    if isinstance(s, C.Decl):
        return [d.init for d in s.items if d.init is not None]
    if isinstance(s, C.Asm):
        return [o.e for o in s.outputs + s.inputs]
    return []


def _all_stmts(stmts):
    """Every statement of the list and of those inside them."""
    for s in stmts:
        yield s
        if isinstance(s, C.If):
            yield from _all_stmts(_list(s.then))
            if s.orelse is not None:
                yield from _all_stmts(_list(s.orelse))
        elif isinstance(s, (C.While, C.DoWhile, C.For, C.Switch)):
            yield from _all_stmts(_list(s.body))
            if isinstance(s, C.For) and isinstance(s.init, C.Decl):
                yield s.init
        elif isinstance(s, C.Block):
            yield from _all_stmts(s.body)


def _written_in(s):
    """The variables a statement (and those inside it) may write."""
    out = set()
    for x in _all_stmts([s]):
        if isinstance(x, C.Asm):
            out |= {o.e.id for o in x.outputs if isinstance(o.e, C.Name)}
            continue
        if isinstance(x, C.Decl):
            out |= {d.name for d in x.items}
        for e in _stmt_exprs(x):
            out |= _writes(e)
    return out


def _has_label(stmts):
    return any(isinstance(s, C.Label) for s in _all_stmts(stmts))


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
        if dead and not isinstance(s, (C.Label, C.Case)) and not _labels_in(s):
            continue                        # (a label inside is looked for only in dead code: it walks s)
        if isinstance(s, C.If):
            s.then = C.Block(drop_dead(_list(s.then)))
            if s.orelse is not None:
                s.orelse = C.Block(drop_dead(_list(s.orelse)))
        elif isinstance(s, (C.For, C.While, C.DoWhile, C.Switch)):
            s.body = C.Block(drop_dead(_list(s.body)))
        out.append(s)
        dead = ends([s])
    return out


def drop_fall_gotos(stmts, after=frozenset()):
    """A goto that ends an arm of an if and goes to a label control reaches anyway when it falls off
    the if (one right after it, or after the statement around it when the if ends that) goes:
    if (c) { ...; goto L; } else { ... } L:  ->  if (c) { ... } else { ... } L:. after: the labels
    falling off the end of stmts reaches (none in a loop's or a switch's body)."""
    for k, s in enumerate(stmts):
        nxt = _labels_after(stmts, k + 1, after)
        if isinstance(s, C.If):
            s.then = C.Block(_drop_last_goto(drop_fall_gotos(_list(s.then), nxt), nxt))
            if s.orelse is not None:
                s.orelse = C.Block(_drop_last_goto(drop_fall_gotos(_list(s.orelse), nxt), nxt))
        elif isinstance(s, (C.For, C.While, C.DoWhile, C.Switch)):
            s.body = C.Block(drop_fall_gotos(_list(s.body)))
        elif isinstance(s, C.Block):
            s.body = drop_fall_gotos(s.body, nxt)
    return stmts


def _labels_after(stmts, k, after):
    """The labels control reaches without a statement on the way from position k of stmts on (after:
    those after the list's end)."""
    names = set()
    while k < len(stmts):
        s = stmts[k]
        if isinstance(s, C.Label):
            names.add(s.name)
        elif not isinstance(s, (C.Note, C.Empty)):
            return names
        k += 1
    return names | set(after)


def _drop_last_goto(stmts, labels):
    k = len(stmts)
    while k and isinstance(stmts[k - 1], (C.Note, C.Empty)):
        k -= 1
    if k and isinstance(stmts[k - 1], C.Goto) and stmts[k - 1].label in labels:
        return stmts[:k - 1] + stmts[k:]
    return stmts


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
    when the body holds what the cut does not handle (a switch other than a jump table). Made once
    plain and, where that nests more than GUARDS levels, once more with the early exits that repeat
    short ways, taken when they take GUARDS levels or more off. A jump to the start of an arm of an if
    that is left is then part of the if's condition where it can be (arm_jumps). Last, a variable set to
    0 or 1 and then by ifs to 0 or 1 is set once to the truth value they make (dread.tidy truths, proved:
    the ifs of branches, which the clean-up before saw as gotos)."""
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
    plain = _emit(blocks, labels, False)
    out = plain
    if GUARDS is not None and (plain is None or nesting(plain)[0] > GUARDS):
        guarded = _emit(blocks, labels, True)   # (from the same blocks: what the emitter repeats it copies,
                                                # and the passes after it only rewrap the arms they keep)
        if plain is None or guarded is not None and nesting(guarded)[0] <= nesting(plain)[0] - GUARDS:
            out = guarded
    if out is None:                         # (reachable but not emitted: leave the function as it is)
        return fn
    out = refine_loops(out)
    out = arms_pass(out)[0]
    if arm_jumps(out):                      # (a jump to the start of the other arm as part of the condition;
        out = prune_labels(out, gotos_in(out))  # the label it named goes when nothing else names it)
    fn.body.body[:] = head + out
    entered(fn, fn.body.body)
    fn.body.body[:] = counted(fn.body.body, address_taken(fn.body.body))
    from dread import tidy                  # (v = 0; if (c) v = 1; of branches as v = c: dread.tidy truths)
    tidy.truths(fn)
    return fn


# arm_jumps: how many expression statements may go into a condition before the check that joins it (a
# longer comma expression reads worse than the goto it takes away)
ARM_JUMP_EFFECTS = 2


def arm_jumps(stmts):
    """A jump to the start of an arm of an if as part of the if's condition, in place; returns whether
    any changed. A compiler threads conditions so (the code of one arm shared with a later check), and
    the emitter, which places each block once, leaves the goto. From inside the other arm:

        if (A) { S...; if (B) goto L; R... } else { L: T... }   is   if (A && (S..., !B)) { R... } else { T... }
        (without R:                                              if (!A || (S..., B)) { T... })
        if (A) { L: T... } else { S...; if (B) goto L; R... }   is   if (A || (S..., B)) { T... } else { R... }

    and from just before the if:

        if (B) goto L; S...; if (A) { L: T... } else { R... }   is   if (B || (S..., A)) { T... } else { R... }
        if (B) goto L; S...; if (A) { R... } else { L: T... }   is   if (!B && (S..., A)) { R... } else { T... }

    S are expression statements only (they run where they ran, after what decided before them and before
    the test after them, in a comma expression), at most ARM_JUMP_EFFECTS of them and none a call made for
    its effects alone (a call statement in a condition reads worse than the goto: a source's call and the
    test after it, which a compiler threaded), the jump is the whole of an if without else, and L the
    first statement of its arm (a label other jumps still name stays where it is; the caller drops one
    nothing names). Each way through does what it did: S and the tests are evaluated exactly where and
    as often as before, T runs where it ran and so does R."""
    changed = []

    def real(xs):
        return [x for x in xs if not isinstance(x, (C.Note, C.Empty))]

    def jump_of(x):
        """L when x is if (B) goto L; without else, else None."""
        if isinstance(x, C.If) and x.orelse is None:
            only = real(_list(x.then))
            if len(only) == 1 and isinstance(only[0], C.Goto):
                return only[0].label
        return None

    def effects_of(xs):
        """The expressions of the expression statements xs (notes and empty statements apart) where they
        may go into a condition (few, no call made for its effects alone), else None."""
        es = [y.e for y in xs if isinstance(y, C.ExprStmt)]
        bare = [e.e if isinstance(e, C.Cast) and e.type == "void" else e for e in es]
        if len(es) > ARM_JUMP_EFFECTS or any(isinstance(e, C.Call) for e in bare):
            return None
        return es

    def plain_prefix(xs, label):
        """(S, B, R) where xs is [S..., if (B) goto label;, R...] as the docstring says, else None."""
        for k, x in enumerate(xs):
            if isinstance(x, (C.Note, C.Empty)):
                continue
            if jump_of(x) == label:
                effects = effects_of(xs[:k])
                return None if effects is None else (effects, x.cond, xs[k + 1:])
            if not isinstance(x, C.ExprStmt):
                return None
        return None

    def first_label(xs):
        first = next(iter(real(xs)), None)
        return first.name if isinstance(first, C.Label) else None

    def joined(effects, cond):
        return C.Comma(effects + [cond]) if effects else cond

    def both(op, l, r):
        """l op r for && or ||, a right operand of the same operator taken apart: a && (b && c) as
        a && b && c, a && (s, b && c) as a && (s, b) && c (the same order of evaluation, flat)."""
        if isinstance(r, C.Comma) and isinstance(r.items[-1], C.Binary) and r.items[-1].op == op:
            last = r.items[-1]
            return both(op, both(op, l, C.Comma(r.items[:-1] + [last.l])), last.r)
        if isinstance(r, C.Binary) and r.op == op:
            return both(op, both(op, l, r.l), r.r)
        return C.Binary(op, l, r)

    def arms(x):
        """The jumps from inside one arm of the if x to the other arm's start."""
        while x.orelse is not None:
            then, other = _list(x.then), _list(x.orelse)
            label = first_label(other)
            got = plain_prefix(then, label) if label else None
            if got is not None:
                effects, cond, rest = got
                if real(rest):
                    x.cond = both("&&", x.cond, joined(effects, negate(cond)))
                    x.then = C.Block(rest)
                else:
                    x.cond = both("||", negate(x.cond), joined(effects, cond))
                    x.then, x.orelse = C.Block(other), None
                changed.append(x)
                continue
            label = first_label(then)
            got = plain_prefix(other, label) if label else None
            if got is None:
                return
            effects, cond, rest = got
            x.cond = both("||", x.cond, joined(effects, cond))
            x.orelse = C.Block(rest) if real(rest) else None
            changed.append(x)

    def before(xs):
        """The jumps from just before an if to one of its arms' start (from the start again after each:
        a jump further up may name the same arm)."""
        i = 0
        while i < len(xs):
            label = jump_of(xs[i])
            j = i + 1
            while label is not None and j < len(xs) and isinstance(xs[j], (C.ExprStmt, C.Note, C.Empty)):
                j += 1
            y = xs[j] if label is not None and j < len(xs) else None
            effects = effects_of(xs[i + 1:j]) if isinstance(y, C.If) and jump_of(y) is None else None
            if effects is not None:
                if first_label(_list(y.then)) == label:
                    y.cond = both("||", xs[i].cond, joined(effects, y.cond))
                elif y.orelse is not None and first_label(_list(y.orelse)) == label:
                    y.cond = both("&&", negate(xs[i].cond), joined(effects, y.cond))
                else:
                    y = None
                if y is not None:
                    del xs[i:j]
                    changed.append(y)
                    arms(y)
                    i = 0
                    continue
            i += 1

    def visit(xs):
        for x in xs:
            if isinstance(x, C.If):
                arms(x)
                visit(_list(x.then))
                if x.orelse is not None:
                    visit(_list(x.orelse))
            elif isinstance(x, (C.For, C.While, C.DoWhile, C.Switch)):
                visit(_list(x.body))
            elif isinstance(x, C.Block):
                visit(x.body)
        before(xs)
    visit(stmts)
    return bool(changed)


def _emit(blocks, labels, guards):
    """The statements for the blocks (Emitter, then prune, arrange and prune again), or None when a
    block that can be reached was not emitted. guards: shared short ways repeated where they make
    early exits (Emitter's guards, arrange's copies)."""
    em = Emitter(blocks, labels, guards=guards)
    out = em.region(0, None, [])
    if any(b not in em.emitted for b in em.shape.idom):
        return None
    out = prune(out)                        # (the labels nobody names out first: one in an arm hides an if)
    return prune(arrange(out, copies=guards))


def prune(out):
    """Statements nothing reaches, gotos to where control falls anyway and labels no goto names out,
    until none is left (code a pruned label led to can be dead in turn; before the loops are refined: a
    label first in a loop body hides its test)."""
    wanted = None
    while True:
        out = drop_dead(drop_fall_gotos(out))
        named = gotos_in(out)
        out = prune_labels(out, named)
        if named == wanted:
            return out
        wanted = named
