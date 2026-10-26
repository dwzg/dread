"""Functions that never return, found from the code.

A function returns when some path from its entry reaches a return instruction. Paths follow the
function's own branches (a switch's resolved cases included) and its calls: a call falls through
only when its callee returns (an indirect call, an svc and a call of something without an entry in
the function table are taken to return), and a jump to another function's entry (a tail call)
returns when that function does. An indirect jump whose targets are not found counts as a return:
not known, so the function is not called non-returning on a guess. Falling out of the function's
instructions into the next function's entry is a tail call of it (MSP430's shared epilogues: pop r4
falls into the function that pops r5); falling into data is not a return.

The fixpoint starts with no function known to return and adds every function that reaches a return
with what is known, until nothing changes. What is left never returns: an endless loop, a reset
handler that calls one, a panic routine and everything that ends in a call of one.

Used by dread.lift (a call of such a function does not fall through; the function itself is not a
candidate, since no case of it can end).

    dread noreturn [--target DIR] [--image NAME]
"""
import argparse
import sys

from dread import target as tgt


def _nodes(program, fn):
    """{address: (successors, (callee name or None, address after the call) or None, tail callee
    names, returns)} for the instructions of fn."""
    from dread import switches
    arch = program.arch
    index = {a: i for i, a in enumerate(fn.insns)}
    nodes = {}
    for i, a in enumerate(fn.insns):
        insn = program.insn(a)
        if insn is None:
            continue
        kind, target, falls = arch.flow(insn)
        nxt = fn.insns[i + 1] if i + 1 < len(fn.insns) and fn.insns[i + 1] == a + insn.size else None
        succ, after, tails, ret = [], None, [], False
        t = program.code_address(target) if isinstance(target, int) else None
        if kind == "next" or kind == "icall":
            succ.append(nxt)
            after_end = a + insn.size
            if nxt is None and after_end not in index and after_end in program.by_entry:
                tails.append(program.by_entry[after_end].name)     # falls into the next function
        elif kind == "return":
            ret = True
            if falls:
                succ.append(nxt)
        elif kind == "jump":
            if t in index:
                succ.append(t)
            elif t is not None and t in program.by_entry:
                tails.append(program.by_entry[t].name)
            else:
                ret = True                  # somewhere unknown
            if falls:
                succ.append(nxt)
        elif kind == "call":
            callee = program.by_entry.get(t) if t is not None else None
            if callee is None:
                succ.append(nxt)
            elif arch.predicate(insn) is not None:
                succ.append(nxt)            # not taken: goes on whatever the callee does
                after = (callee.name, None)
            else:
                after = (callee.name, nxt)
        elif kind == "ijump":
            got = switches.resolve(program, fn, a)
            if not got:
                ret = True
            else:
                succ += [x for x in got[0] if x in index]
                for x in got[1]:
                    callee = program.by_entry.get(program.code_address(x))
                    if callee is None:
                        ret = True
                    else:
                        tails.append(callee.name)
            if falls:
                succ.append(nxt)
        nodes[a] = ([s for s in succ if s is not None], after, tails, ret)
    return nodes


def find(program):
    """The names of the functions that never return."""
    nodes = {fn.name: _nodes(program, fn) for fn in program.functions if fn.insns}
    entries = {fn.name: fn.insns[0] for fn in program.functions if fn.insns}
    returns = set()

    def reaches_return(name):
        graph, seen, work = nodes[name], set(), [entries[name]]
        while work:
            a = work.pop()
            if a in seen or a not in graph:
                continue
            seen.add(a)
            succ, after, tails, ret = graph[a]
            if ret or any(t in returns for t in tails):
                return True
            work += succ
            if after is not None and after[1] is not None and after[0] in returns:
                work.append(after[1])
        return False

    changed = True
    while changed:
        changed = False
        for name in nodes:
            if name not in returns and reaches_return(name):
                returns.add(name)
                changed = True
    return set(nodes) - returns


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread noreturn", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
    except tgt.TargetError as e:
        print("dread noreturn: %s" % e, file=sys.stderr)
        return 1
    from dread.program import Program
    for name in sorted(find(Program(target, image))):
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
