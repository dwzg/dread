"""dread callsites: the calls of functions, with the constant arguments at each call.

For every direct call or tail call of a function whose name matches one of the patterns (shell
style: uart_*, *_init), the caller, the address and the argument registers whose value at the call
is a constant (dread.constants: literals, computed addresses, values from the image's tables), shown
with the name of what they point to or the text there. Only the registers the callee reads at its
entry are shown when dread.regflow knows them.

    --by N           group the calls by the value of argument N instead (r0 is 0): value, count, callers
                     (a message number, a command code: what a dispatcher is called with)
    --reaches NAME   for each matching function, the shortest chains of calls that lead to NAME

    dread callsites [--target DIR] [--image NAME] [--by N] [--reaches NAME] PATTERN...
"""
import argparse
import collections
import fnmatch
import sys

from dread import target as tgt
from dread.program import Program



def sites(program, patterns):
    """[(callee, caller, address, kind)] of the direct calls and tail calls of matching functions."""
    out = []
    for fn in program.functions:
        for insn in program.instructions(fn):
            if insn is None:
                continue
            kind, target, _ = program.arch.flow(insn)
            if kind not in ("call", "jump") or not isinstance(target, int):
                continue
            callee = program.by_entry.get(program.code_address(target))
            if callee is None or callee is fn or (kind == "jump" and (program.code_address(target)) in fn.insns):
                continue
            if any(fnmatch.fnmatchcase(callee.name, p) for p in patterns):
                out.append((callee, fn, insn.address, "call" if kind == "call" else "tail call"))
    return out


def graph(program):
    """{caller name: {callee names}} of direct calls and tail calls."""
    out = collections.defaultdict(set)
    for callee, caller, _, _ in sites(program, ["*"]):
        out[caller.name].add(callee.name)
    return out


def chains(program, start, goal, limit=5):
    """Up to `limit` shortest call chains from start to goal (lists of names), nearest first."""
    calls = graph(program)
    found, queue, seen = [], collections.deque([[start]]), {start}
    while queue and len(found) < limit:
        path = queue.popleft()
        for callee in sorted(calls.get(path[-1], ())):
            if callee == goal:
                found.append(path + [callee])
            elif callee not in seen:
                seen.add(callee)
                queue.append(path + [callee])
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread callsites", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--by", type=int, help="group by the value of argument N")
    parser.add_argument("--reaches", help="the shortest call chains from the matching functions to NAME")
    parser.add_argument("patterns", nargs="+")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
    except tgt.TargetError as e:
        print("dread callsites: %s" % e, file=sys.stderr)
        return 1
    from dread import constants, regflow
    from dread.flat import describe_value
    program = Program(target, image)
    if args.reaches:
        if args.reaches not in program.by_name:
            print("dread callsites: no function %s" % args.reaches, file=sys.stderr)
            return 1
        for fn in program.functions:
            if any(fnmatch.fnmatchcase(fn.name, p) for p in args.patterns) and fn.name != args.reaches:
                for path in chains(program, fn.name, args.reaches):
                    print(" -> ".join(path))
        return 0
    try:
        flow = regflow.load(target, image, program)
    except Exception:
        flow = None
    found = sites(program, args.patterns)
    if not found:
        print("dread callsites: no calls of %s" % " ".join(args.patterns), file=sys.stderr)
        return 1
    ARGS = program.arch.arg_regs(4)
    if args.by is not None:
        reg = ARGS[args.by]
        groups = collections.defaultdict(list)
        for callee, caller, address, kind in found:
            value = constants.at(program, caller, address, [reg]).get(reg)
            groups[(callee.name, value)].append("%s 0x%x" % (caller.name, address))
        for (name, value), where in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] is None,
                                                                           kv[0][1] or 0)):
            shown = describe_value(program, value) if value is not None else "(not constant)"
            print("%-20s %s=%-28s %3d  %s" % (name, reg, shown, len(where), ", ".join(where[:6]) +
                                               (" ..." if len(where) > 6 else "")))
        return 0
    for callee in sorted({c.name for c, _, _, _ in found}):
        reads = [r for r in (flow.read_regs(callee) if flow else ARGS) if r in ARGS] if flow else list(ARGS)
        print("%s (reads %s):" % (callee, ", ".join(reads) or "no argument register"))
        for c, caller, address, kind in found:
            if c.name != callee:
                continue
            values = constants.at(program, caller, address, reads) if reads else {}
            shown = ", ".join("%s=%s" % (r, describe_value(program, values[r]) if r in values else "?") for r in reads)
            print("    %s+0x%x (0x%x) %s%s" % (caller.name, address - caller.entry, address,
                                             kind + " " if kind != "call" else "", shown))
    return 0


if __name__ == "__main__":
    sys.exit(main())
