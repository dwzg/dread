"""dread callconv: the calling convention as the original's code keeps it, measured.

dread.regflow reads from the code which scratch registers a function takes at its entry (reads) and
which its callers rely on it to leave alone (preserved); the lifter and the comparison act on both.
A compiler whose code the analysis does not know (its runtime helpers, a register handed through, a
convention of its own) can make it wrong without any test failing. This runs each function's cases
on the original image alone: once as they are, and once more for each general register with another
value in it at the entry. A register whose entry value changes what the function does (its result,
what it writes outside the stack, its calls and peripheral accesses, the callee-saved registers at
its return) is one it reads, unless all it changes is the result register holding that very value
(a function without a result leaves it as its caller had it); one that holds another value at the
return than at the entry is one it changes.

Listed per function where the measurement and dread.regflow disagree:

  reads X, regflow does not      a scratch register regflow does not count as read: an input it missed
  reads its caller's X           a callee-saved register the function reads as its caller left it:
                                 regflow does not follow those (the lifter hands them in, dread.lift)
  changes X, regflow: leaves it  a scratch register regflow says the function leaves alone
  changes callee-saved X         not restored at the return: not the standard convention
  returns from X, regflow: own   the result register, whose value at the return changes with its value
                                 at the entry where regflow says the function overwrites it (its own
                                 value on every path: dread readable then passes no argument there)

regflow's reads that no case shows are counted, not listed: a path the cases do not take, or the
analysis' caution. A read of an unnamed argument kept in a variadic save area is listed apart
("variadic"), not as a disagreement: regflow takes those registers for no use by design (a stub of
printf would otherwise log what a caller's format does not ask for). A case whose findings come from code that touches a saved register's slot (a
length past a local: a queue's item size a generated input made larger than the item, so memcpy
copied the saved r4 to r6 into the queue; dread.runner.saves_touched, where dread test refuses such a
case) is left out. A stack word the code leaves unset and copies out holds what an earlier frame saved
there, and the finding is the original's: FreeRTOS's xTimerGenericCommand (RISC-V GCC -O2) queues a
message whose union's last word no command sets, an s3 saved there before ("reads its caller's s3").
Not seen: a register read only on paths no case takes, one whose value changes nothing a case compares,
and the flags.

The cases are each function's cached generated, kept and written ones, as a lift check left them
(dread lift --check); a function without cases for the image as it is now is left out, nothing is
generated. At most MAX_CASES cases a function. A case whose run does not end at the return is left
out: a hang, a stop, a fault (fault: true) end it in the middle of the function, the callee-saved
registers not yet restored (from the run's log, so also a kept or written case, whose marks the list
of cases does not carry). Functions run in parallel worker processes.

    dread callconv [--target DIR] [--image NAME] [--json FILE] [NAME...]
"""
import argparse
import json
import multiprocessing
import os
import sys

from dread import target as tgt
from dread.emu import EmuError, Layout, Machine
from dread.program import Program
from dread.util.polite import die_with_parent, polite, resident, workers

MAX_CASES = 12              # cases a function at most: a register an input reads shows in most of them
VARY = 0x5A5A5A5A           # xor'ed into a register's entry value for its second run
BASE = 0x3C000000           # entry values of the registers a case does not set: BASE + 0x101 * (index + 1)
KINDS = ("reads, regflow does not", "reads its caller's", "changes, regflow: leaves it", "changes callee-saved",
         "returns from, regflow: own")

RUN = {}                    # what the workers need; filled before they are forked


def registers(image):
    """The general registers a case's run may find anything in: not sp, pc, the link register, nor
    the image's fixed registers (gp, which the startup sets once)."""
    arch = image.arch
    skip = {"sp", "pc", arch.link_reg} | set(image.fixed)
    return [r for r in arch.general_regs() if r not in skip]


def run_once(name, case, entry_values, args):
    """One run of a case on the original: (Result, {register: value at the return}), or the EmuError."""
    _, addr, setup, _, limit, _ = case
    m = Machine(RUN["layout"], RUN["original"], None, isa_of=RUN["program"].isa_at)
    m.plan, m.replay, m.function = [], False, name
    setup(m)
    for reg, value in entry_values.items():
        m.reg(reg, value)
    try:
        result = m.call(addr, *args, max_instructions=limit)
    except EmuError as e:
        return e, {}
    return result, {r: m.reg(r) for r in RUN["regs"]}


def returned(result):
    """Whether a run ended at the function's return: one that may hang or fault ends with ("did not
    return",) or ("fault", kind, address) in its log instead (dread.emu Machine.call)."""
    last = result.calls[-1] if result.calls else ()
    return not (last and last[0] in ("did not return", "fault"))


def measure(name):
    """(name, {"cases": n, "reads": [...], "changes": [...]}) of one function, or (name, None) without
    cases."""
    from dread import autocase
    prog, flow = RUN["program"], RUN["regflow"]
    fn = prog.by_name[name]
    RUN["suite"].auto.setdefault(name, {})
    try:
        cases = autocase.cases_for(RUN, name, fn, make=False)
    except Exception:                       # a failed generation, cached: no cases
        cases = []
    cases = [c for c in cases if not (c[5] or {}).get("hang") and not (c[5] or {}).get("fault")][:MAX_CASES]
    if not cases:
        return name, None
    regs, arg_regs, saved = RUN["regs"], RUN["arg_regs"], set(RUN["saved"])
    said = set(flow.read_regs(name)) if flow else set()
    own = RUN.get("overwritten", {}).get(name, set())
    area = variadic_area(prog, fn)
    reads, changes, handed, ran, touched = set(), set(), set(), 0, 0
    for case in cases:
        args = list(case[3])
        base = {r: BASE + 0x101 * (i + 1) for i, r in enumerate(regs)
                if not (r in arg_regs and arg_regs.index(r) < len(args))}
        first, after = run_once(name, case, base, args)
        if isinstance(first, Exception) or not returned(first):
            continue                        # the case does not run on its own, or not to the return:
                                            # nothing to compare with
        entry = dict(base, **{r: args[arg_regs.index(r)] & 0xFFFFFFFF for r in regs
                              if r in arg_regs and arg_regs.index(r) < len(args)})
        case_reads, case_changes, case_handed = set(), set(), set()
        for r in regs:
            if r in saved and after.get(r) != entry[r]:
                case_changes.add(r)
            elif r in RUN["preserved"].get(name, ()) and after.get(r) != entry[r]:
                case_changes.add(r)
            if r in reads or r in case_reads:
                continue
            if r in arg_regs and arg_regs.index(r) < len(args):
                varied_args = list(args)
                varied_args[arg_regs.index(r)] = (args[arg_regs.index(r)] ^ VARY) & 0xFFFFFFFF
                second, _ = run_once(name, case, base, varied_args)
            else:
                second, _ = run_once(name, case, dict(base, **{r: base[r] ^ VARY}), args)
            if isinstance(second, Exception):
                case_reads.add(r)
                continue
            differ = {d for d in first.differences(second) if d != ("saved", r)}
            if "return" in differ and r == RUN["result"] and r in own:
                case_handed.add(r)          # (its own on every path, regflow says: the return should not follow)
            if "return" in differ and r == RUN["result"] and first.ret & 0xFFFFFFFF == entry[r] and \
                    second.ret & 0xFFFFFFFF == entry[r] ^ VARY:
                differ.discard("return")    # left as its caller had it (no result): passed through, not read
            if differ:
                case_reads.add(r)
        # a finding: unless the input made code touch a saved slot
        if case_changes or case_reads - said or case_handed:
            from dread import runner
            if runner.saves_touched(name, case[1], args, case[4], case[2], (), {}):
                touched += 1
                continue
        ran += 1
        reads |= case_reads
        changes |= case_changes
        handed |= case_handed
    if not ran:
        return name, None
    out = {"cases": ran, "touched": touched,
           "reads": sorted(reads, key=regs.index), "changes": sorted(changes, key=regs.index),
           "regflow_reads": sorted(said & set(regs), key=regs.index), "found": {}, "variadic": []}
    found = {k: [] for k in KINDS}
    for r in out["reads"]:
        if r in saved:
            found["reads its caller's"].append(r)
        elif r not in said and r in area:
            out["variadic"].append(r)       # (an unnamed argument: no use to regflow by design)
        elif r not in said:
            found["reads, regflow does not"].append(r)
    for r in out["changes"]:
        found["changes callee-saved" if r in saved else "changes, regflow: leaves it"].append(r)
    found["returns from, regflow: own"] = sorted(handed, key=regs.index)
    out["found"] = {k: v for k, v in found.items() if v}
    out["unshown"] = sorted(said & set(regs) - reads, key=regs.index)     # regflow's reads no case showed
    return name, out


def variadic_area(program, fn):
    """The argument registers fn keeps in a variadic save area: dread.regflow's rule (stores right
    below the stack arguments) and an entry push of argument registers that ends with the last one
    (GCC's push {r1, r2, r3}); the empty set for any other function."""
    from dread import regflow
    arch = program.arch
    insns = [program.insn(a) for a in fn.insns]
    joins = {fn.entry} | {program.code_address(t) for i in insns if i is not None
                          for k, t, _ in [arch.flow(i)] if k == "jump" and isinstance(t, int)}
    try:
        area = set(regflow.variadic_save_area(program, fn, insns, joins))
    except Exception:
        area = set()
    args = list(program.target.abi.arg_regs)
    if insns and insns[0] is not None and hasattr(arch, "pushed_registers"):
        pushed = [r for r in arch.pushed_registers(insns[0]) if r]
        if pushed and args and args[-1] in pushed and set(pushed) <= set(args):     # (i386: no argument registers)
            area |= set(pushed)
    return area


def prepare(target, image):
    """Fill RUN for measuring the functions of one image."""
    from dread import regflow
    from dread.cases import Suite
    prog = Program(target, image)
    layout = Layout(target, image)
    suite = Suite(target, image, prog, layout).load()
    flow = regflow.load(target, image, prog)
    regs = registers(image)
    RUN.clear()
    RUN.update(target=target, image=image, program=prog, layout=layout, suite=suite, original=image.payload(),
               regflow=flow, regs=regs, arg_regs=list(target.abi.arg_regs), saved=list(target.abi.callee_saved),
               result=target.abi.result[0],
               preserved={fn.name: set(flow.preserved_regs(fn.name)) for fn in prog.functions} if flow else {},
               overwritten={fn.name: set(flow.overwritten_regs(fn.name)) for fn in prog.functions} if flow else {})
    Machine.entries[(target.file, image.name)] = {fn.entry for fn in prog.functions}
    from dread import runner                # (saves_touched reads the runner's context)
    runner.RUN.update(program=prog, layout=layout, original=image.payload(), isa_of=prog.isa_at)


def measure_all(names, jobs=None):
    """{name: result} for the functions that have cases (RUN prepared)."""
    results = {}
    with multiprocessing.get_context("fork").Pool(jobs or workers(memory_each=resident()), initializer=die_with_parent) as pool:
        for name, out in pool.imap_unordered(measure, names):
            if out is not None:
                results[name] = out
    return results


def summary(results):
    """{kind: number of functions} and the functions with any disagreement, for reports."""
    counts = {k: sum(1 for r in results.values() if k in r["found"]) for k in KINDS}
    return {"measured": len(results), "agree": sum(1 for r in results.values() if not r["found"]),
            "functions": counts, "unshown_reads": sum(len(r["unshown"]) for r in results.values())}


def main(argv=None):
    p = argparse.ArgumentParser(prog="dread callconv", description=__doc__.split("\n\n")[0])
    p.add_argument("--target", default=".")
    p.add_argument("--image")
    p.add_argument("--json", help="write every function's measurement and the summary to this file")
    p.add_argument("-j", "--jobs", type=int)
    p.add_argument("names", nargs="*")
    args = p.parse_args(argv)
    polite()
    target = tgt.Target.load(args.target)
    image = target.image(args.image)
    prepare(target, image)
    prog = RUN["program"]
    names = args.names or [fn.name for fn in prog.functions]
    missing = [n for n in names if n not in prog.by_name]
    if missing:
        print("dread callconv: no function %s" % ", ".join(missing), file=sys.stderr)
        return 2
    results = measure_all(names, args.jobs)
    for name in sorted(results, key=lambda n: prog.by_name[n].entry):
        found = results[name]["found"]
        if found:
            print("%-28s %s" % (name, "; ".join("%s %s" % (k, ", ".join(v)) for k, v in found.items())))
    s = summary(results)
    print("dread callconv: %d functions measured, %d agree with dread.regflow; %s; %d of regflow's reads no "
          "case showed" % (s["measured"], s["agree"], ", ".join("%s: %d" % (k, n) for k, n in s["functions"].items()),
                           s["unshown_reads"]))
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"summary": s, "functions": results}, f, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
