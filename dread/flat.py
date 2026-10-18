"""dread flat: one function's code as a flat listing to read.

For each function named (or given by an address inside it): a header with its extent, instruction
set, who calls it and what it calls, what dread.regflow knows (the registers it reads at its entry,
those its callers read after it), whether it never returns (dread.noreturn), and the annotations of
its entry; then every instruction with its address and bytes, branch and call targets by name (a
target inside the function as a label), literal loads with their value (the name of what it points
to, or the text there), the constant arguments of each call (dread.constants), the targets of a
switch, and the annotations of each address; literal pools as words with what they point to.

    dread flat [--target DIR] [--image NAME] NAME|ADDRESS...
"""
import argparse
import sys

from dread import target as tgt
from dread.program import Program


def describe_value(program, value):
    """A value as the listing shows it: hex, with the name of what it points to or its text."""
    name = program.name_at(value & ~1) if value is not None else None
    if name:
        return "0x%x <%s>" % (value, name)
    fn = program.function_at(value & ~1)
    if fn is not None:
        return "0x%x <%s+0x%x>" % (value, fn.name, (value & ~1) - fn.entry)
    from dread.constants import string_at
    text = string_at(program, value)
    if text:
        return '0x%x "%s"' % (value, text)
    return "0x%x" % value


def callers_of(program):
    """{callee name: [(caller name, address)]} of the direct calls and tail calls."""
    out = {}
    for fn in program.functions:
        for insn in program.instructions(fn):
            if insn is None:
                continue
            kind, target, _ = program.arch.flow(insn)
            if kind in ("call", "jump") and isinstance(target, int):
                callee = program.by_entry.get(target & ~1)
                if callee is not None and callee is not fn:
                    out.setdefault(callee.name, []).append((fn.name, insn.address))
    return out


def value_parts(program, value):
    """(text, linked name or None) of a value, as describe_value shows it."""
    name = program.name_at(value & ~1) if value is not None else None
    if name and (value & ~1) in program.by_entry:
        return describe_value(program, value), name
    return describe_value(program, value), None


def rows(program, fn, flow=None, endless=None, callers=None):
    """The listing as data: (header, rows). header: name, entry, end, count, isa, callers [(name,
    address)], calls [names], reads, kept, endless, notes. A row: {"kind": "insn" or "word" (a literal
    pool word) or "label" or "note", "address", "bytes", "text", "comment": [{"text", "name"?,
    "label"?}]}, names and labels being what a viewer links."""
    from dread import constants, switches
    arch = program.arch
    insns = program.instructions(fn)
    calls = []
    for insn in insns:
        if insn is not None:
            kind, target, _ = arch.flow(insn)
            callee = program.by_entry.get(target & ~1) if kind in ("call", "jump") and isinstance(target, int) else None
            if callee is not None and callee is not fn and callee.name not in calls:
                calls.append(callee.name)
    notes = program.annotations()
    header = {"name": fn.name, "entry": fn.entry, "end": program.end(fn), "count": len(fn.insns), "isa": fn.isa,
              "callers": list((callers or {}).get(fn.name, [])), "calls": calls,
              "reads": flow.read_regs(fn.name) if flow is not None else None,
              "kept": flow.kept_regs(fn.name) if flow is not None else None,
              "endless": bool(endless is not None and fn.name in endless), "notes": list(notes.get(fn.entry, ()))}
    labels = {}
    for insn in insns:
        if insn is None:
            continue
        kind, target, _ = arch.flow(insn)
        if kind == "jump" and isinstance(target, int) and (target & ~1) in fn.insns and (target & ~1) != fn.entry:
            labels.setdefault(target & ~1, "L%d" % (len(labels) + 1))
    out = []
    previous_end = fn.entry
    for insn in insns:
        if insn is None:
            continue
        a = insn.address
        if a > previous_end:
            out += pool_rows(program, previous_end, a)
        previous_end = a + insn.size
        if a in labels:
            out.append({"kind": "label", "address": a, "text": labels[a]})
        for text in notes.get(a, ()) if a != fn.entry else ():
            out.append({"kind": "note", "address": a, "text": text})
        kind, target, _ = arch.flow(insn)
        comment = []
        if kind in ("call", "jump") and isinstance(target, int):
            t = target & ~1
            if t in labels:
                comment.append({"text": labels[t], "label": t})
            else:
                text, name = value_parts(program, t)
                comment.append({"text": text, "name": name} if name else {"text": text})
            if kind == "call" or (program.by_entry.get(t) is not None and t not in fn.insns):
                args = constants.at(program, fn, a, ["r0", "r1", "r2", "r3"])
                if args:
                    comment.append({"text": "with " + ", ".join("%s=%s" % (r, describe_value(program, v))
                                                                for r, v in sorted(args.items()))})
        elif kind == "ijump":
            got = switches.resolve(program, fn, a)
            if got:
                inside, outside = got
                comment.append({"text": "goes to " + ", ".join([labels.get(x, "0x%x" % x) for x in inside] +
                                                               [describe_value(program, x) for x in outside])})
        lit = arch.literal(insn, program.isa_at(a))
        if lit is not None and lit[1] == 4 and program.in_image(lit[0], 4):
            text, name = value_parts(program, program.word(lit[0]))
            comment.append({"text": "= " + text, "name": name} if name else {"text": "= " + text})
        at = arch.pc_address(insn, program.isa_at(a))
        if at is not None:
            text, name = value_parts(program, at)
            comment.append({"text": "= " + text, "name": name} if name else {"text": "= " + text})
        out.append({"kind": "insn", "address": a, "bytes": bytes(insn.bytes).hex(),
                    "text": "%s %s" % (insn.mnemonic, insn.op_str), "comment": comment})
    if program.end(fn) > previous_end:
        out += pool_rows(program, previous_end, program.end(fn))
    return header, out


def listing(program, fn, flow=None, endless=None, callers=None):
    """The lines of one function's flat listing (the text of rows())."""
    header, out = rows(program, fn, flow, endless, callers)
    lines = ["; %s  0x%x..0x%x  %d instructions, %s" % (fn.name, header["entry"], header["end"], header["count"],
                                                         header["isa"])]
    who = header["callers"]
    if who:
        lines.append("; called by: %s" % ", ".join("%s 0x%x" % w for w in who[:12]) +
                     (" and %d more" % (len(who) - 12) if len(who) > 12 else ""))
    if header["calls"]:
        lines.append("; calls: %s" % ", ".join(header["calls"]))
    if header["reads"] is not None:
        lines.append("; reads at its entry: %s; its callers read after it: %s" % (
            ", ".join(header["reads"]) or "nothing", ", ".join(header["kept"]) or "nothing"))
    if header["endless"]:
        lines.append("; never returns")
    for text in header["notes"]:
        lines.append("; %s" % text)
    for row in out:
        if row["kind"] == "label":
            lines.append("%s:" % row["text"])
        elif row["kind"] == "note":
            lines.append("    ; %s" % row["text"])
        else:
            line = "    %08x  %-10s %-34s" % (row["address"], row["bytes"], row["text"])
            if row["comment"]:
                line += " ; " + "; ".join(c["text"] for c in row["comment"])
            lines.append(line.rstrip())
    return lines


def pool_rows(program, lo, hi):
    out = []
    a = lo
    while a + 4 <= hi:
        if a % 4:
            a += 2
            continue
        text, name = value_parts(program, program.word(a))
        out.append({"kind": "word", "address": a, "bytes": program.read(a, 4).hex(), "text": ".word",
                    "comment": [{"text": text, "name": name} if name else {"text": text}]})
        a += 4
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread flat", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("names", nargs="+", help="function names or addresses")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
    except tgt.TargetError as e:
        print("dread flat: %s" % e, file=sys.stderr)
        return 1
    from dread import noreturn, regflow
    program = Program(target, image)
    try:
        flow = regflow.load(target, image, program)
    except Exception:
        flow = None
    endless = noreturn.find(program)
    callers = callers_of(program)
    status = 0
    for name in args.names:
        fn = program.by_name.get(name)
        if fn is None:
            try:
                fn = program.function_at(int(name, 0))
            except ValueError:
                fn = None
        if fn is None:
            print("dread flat: no function %s" % name, file=sys.stderr)
            status = 1
            continue
        print("\n".join(listing(program, fn, flow, endless, callers)))
        print()
    return status


if __name__ == "__main__":
    sys.exit(main())
