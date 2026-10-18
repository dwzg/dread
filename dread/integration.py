"""dread integration: run functions together with their real callees, original against rebuilt.

The per-function tests stub callees: they check a function in isolation, not that a C caller and
its callees agree about arguments, results and memory, nor behaviour that depends on what a callee
leaves in scratch registers. This run takes each function's generated inputs (the generator's
cache, BUILD/autocases/IMAGE/) and runs them again with nothing stubbed, in the original image and
in the rebuilt one: the whole call tree executes.

The scratch registers callers read after the call (dread.regflow) are compared at the return too.

An input is used when the original runs it to the end within LIMIT instructions and does not read
a stack location nobody wrote (a local set only on some paths: what it holds depends on earlier
calls and differs between builds), nor reads as data a place of the image where the two builds
differ (a generated pointer into code; loads within 4 KiB of the loading instruction are taken for
literal pools and allowed). The rebuilt image may take RECON_LIMIT instructions. Then the return
value, the memory written outside the stack and the log of peripheral accesses must agree; values
that are addresses inside the stack are not compared (frames lie differently in the two builds).

Runs the functions built from C that have generated cases (auto() in the case files) and, with
--callers, the original functions that call them directly, with inputs generated for them on the
fly (callees real; at most GENERATE_SECONDS each, without the generator's fallback searches, and
cached). The image's kept cases run first; --keep-failing adds differing inputs there.

Output: `DIFF NAME: case N: ...` per function that differs, then a summary; exit status 1 on a
difference.

    dread integration [--target DIR] [--image NAME] [--cases N] [--callers] [--keep-failing] [--recon PAYLOAD]
                      [NAME...]
"""
import argparse
import json
import multiprocessing
import os
import sys

from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE

from dread import autocase, build, runner
from dread import target as tgt
from dread.emu import EmuError, Machine
from dread.overlay import load_placement
from dread.util.polite import die_with_parent, polite, workers
from dread.util.poolorder import longest_first

LIMIT = 300_000
RECON_LIMIT = 10 * LIMIT
LITERAL_REACH = 0x1000
G = {}


def run(payload, aliases, case, addr, name, watch=False, limit=None):
    """Result of one input with real callees, or None if it does not return. With watch, also
    whether it read stale stack or differing image bytes as data (result.unusable)."""
    gen, lay = G["gen"], G["gen"].layout
    m = Machine(lay, payload, aliases, isa_of=gen.program.isa_at)
    m.function = name
    m.arg_domains = gen.argument_domains.get(name, [])
    m.kept, m.kept_why, _ = runner.kept_for(name)
    for a, size, value in case["mem"]:
        try:
            m.write(a, (value & ((1 << 8 * size) - 1)).to_bytes(size, "little"))
        except EmuError:
            return None
    # the peripheral answers are inputs too (stubs are not: the callees run for real)
    for at, nth, value in case.get("poll", ()):
        m.poll_read(at, nth, value)
    if case.get("io") == "fixed":
        m.io_fixed = {a + i: b for a, size, value in case["mem"] if lay.target.memory.is_io(a)
                      for i, b in enumerate((value & ((1 << 8 * size) - 1)).to_bytes(size, "little"))}
    stale, foreign = [], []
    if watch:
        written, last = set(), [lay.stack_top]
        base, top = lay.stack_top - lay.stack_size, lay.stack_top

        def on_stack(uc, access, a, size, value, user):
            sp = m.reg("sp")
            if sp > last[0]:
                written.difference_update(range(last[0], sp))      # a released frame is dead
            last[0] = sp
            if access == UC_MEM_WRITE:
                written.update(range(a, a + size))
            elif not stale and any(x not in written for x in range(a, a + size)):
                stale.append(a)
        m.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, on_stack, begin=base, end=top - 1)
        differs, image_base = G["differs"], G["image"].base

        def on_image(uc, access, a, size, value, user):
            if not foreign and any(differs[a - image_base:a - image_base + size]) \
                    and abs(a - m.reg("pc")) > LITERAL_REACH:
                foreign.append(a)
        if differs:
            m.hook_add(UC_HOOK_MEM_READ, on_image, begin=image_base, end=image_base + len(differs) - 1)
    try:
        result = m.call(addr, *case["args"], max_instructions=limit or LIMIT)
    except EmuError:
        return None
    result.unusable = bool(stale) or bool(foreign)
    return result


def stackish(writes, addr, lay):
    """Whether the byte at addr belongs to a written word that is an address inside the stack."""
    for base in (addr & ~3, addr, addr - 1, addr - 2, addr - 3):
        if all(base + i in writes for i in range(4)):
            if lay.in_stack(sum(writes[base + i] << 8 * i for i in range(4))):
                return True
    return False


GENERATE_SECONDS = 20       # inputs made on the fly for a caller: at most this long (cached, also when empty)


def inputs_for(name):
    """Generated inputs for a function without auto(): cached; made quickly, without the
    fallback searches (a function that never returns, such as a main loop, would otherwise take
    minutes to give up)."""
    gen, image = G["gen"], G["image"]
    prog = gen.program
    fn = prog.by_name[name]
    path = os.path.join(G["target"].build, "autocases", image.name, "integration-" + name + ".json")
    key = autocase.cache_key(gen.payload, image.base, fn.insns, {a: prog.insn_size(a) for a in fn.insns}, fn.entry,
                             {"integration": True, "seconds": GENERATE_SECONDS}, prog.arch)
    cached = autocase.cache_load(path)
    if not cached or cached.get("key") != key:
        try:
            made = gen.generate(name, fn.entry, fn.insns, budget=200, fallbacks=False, seconds=GENERATE_SECONDS)
        except autocase.GenerationError:
            made = []
        cached = {"key": key, "cases": made}
        autocase.cache_store(path, cached)
    return cached["cases"]


def check(item):
    name, addr, cases = item
    if cases is None:
        cases = inputs_for(name)
    lay = G["gen"].layout
    ran, problems, differing = 0, [], []
    kept = autocase.kept_load(G["image"], name, G["gen"].real)
    for i, case in enumerate(kept + cases[:G["count"]]):
        before = len(problems)
        a = run(G["original"], None, case, addr, name, watch=True)
        if a is None or a.unusable:
            continue
        b = run(G["recon"], G["aliases"], case, addr, name, limit=RECON_LIMIT)
        ran += 1
        if b is None:
            problems.append("case %d: the original returns, the rebuilt image faults or hangs" % i)
        else:
            if a.ret != b.ret and not lay.in_stack(a.ret):
                problems.append("case %d: return value 0x%x vs 0x%x" % (i, a.ret, b.ret))
            if a.calls != b.calls:
                problems.append("case %d: peripheral accesses differ" % i)
            theirs = dict(b.kept)
            for reg, value in a.kept:
                if theirs.get(reg) != value:
                    problems.append("case %d: %s after the return 0x%x vs %s (%s)" % (
                        i, reg, value, "-" if theirs.get(reg) is None else "0x%x" % theirs[reg], a.why.get(reg, "")))
            diff = [x for x in sorted(set(a.writes) | set(b.writes))
                    if a.writes.get(x) != b.writes.get(x) and not stackish(a.writes, x, lay)]
            if diff:
                problems.append("case %d: %d bytes of memory differ, first at 0x%x" % (i, len(diff), diff[0]))
        if len(problems) > before and i >= len(kept):
            differing.append(case)
    if differing and G["keep_failing"]:
        autocase.kept_add(G["image"], name, differing, "real callees")
    return name, ran, problems


def callers_of(program, names):
    """The functions that call one of `names` directly and are not among them."""
    entries = {program.by_name[n].entry for n in names if n in program.by_name}
    out = []
    for fn in program.functions:
        if fn.name in names:
            continue
        for insn in program.instructions(fn):
            if insn is not None and program.arch.is_call(insn) and program.arch.target(insn) in entries:
                out.append(fn.name)
                break
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread integration", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--cases", type=int, default=6, help="generated inputs per function (default 6)")
    parser.add_argument("--callers", action="store_true", help="also the original functions that call rewritten ones")
    parser.add_argument("--keep-failing", action="store_true", help="add differing inputs to the kept cases")
    parser.add_argument("--recon", help="the rebuilt image (default: BUILD/recon/IMAGE/payload.bin)")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)
    polite()
    try:
        target, image = tgt.from_args(args)
        recon_path = args.recon or os.path.join(build.recon_dir(target, image), "payload.bin")
        if not os.path.exists(recon_path):
            raise tgt.TargetError("no rebuilt image: run `dread recon` first")
        suite = runner.prepare(target, image, recon_path)
    except (tgt.TargetError, EmuError) as e:
        print("dread integration: %s" % e, file=sys.stderr)
        return 1
    run_state = runner.RUN
    gen = autocase.generator_for(run_state)
    prog = gen.program
    aliases, built = load_placement(os.path.join(os.path.dirname(recon_path), "placement.json"))
    names = [n for n in built if n in suite.auto]
    if args.callers:                        # (not those that never return: the system run tests them)
        names += [n for n in callers_of(prog, set(built)) if n not in run_state.get("system", ())]
    if args.names:
        names = [n for n in names if n in args.names] + [n for n in args.names if n not in names]
    items = []
    for name in names:
        fn = prog.by_name.get(name)
        if fn is None:
            continue
        if name in suite.auto:
            try:
                cases = [c for _, _, _, _, _, c in autocase.cases_for(run_state, name, fn) if c is not None]
            except autocase.GenerationError as e:
                print("dread integration: %s: no inputs (%s)" % (name, e))
                continue
        else:
            cases = None                    # made in the worker (inputs_for)
        items.append((name, fn.entry, cases))
    original, recon = image.payload(), run_state["recon"]
    n = min(len(original), len(recon))
    G.update(gen=gen, target=target, image=image, original=original, recon=recon, aliases=aliases, count=args.cases,
             keep_failing=args.keep_failing, differs=bytes(a != b for a, b in zip(original[:n], recon[:n])))
    times = os.path.join(target.build, "times", "integration-%s.json" % image.name)
    ran = clean = cases = 0
    bad = []
    with multiprocessing.get_context("fork").Pool(workers(), initializer=die_with_parent) as pool:
        for name, count, problems in longest_first(pool, check, items, lambda item: item[0], times):
            cases += count
            if count:
                ran += 1
                if problems:
                    bad.append((name, problems))
                else:
                    clean += 1
    for name, problems in bad:
        print("DIFF %s: %s" % (name, "; ".join(problems[:3])))
    print("dread integration: %d functions with inputs; %d ran whole call trees (%d inputs); %d agree, %d differ"
          % (len(items), ran, cases, clean, len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
