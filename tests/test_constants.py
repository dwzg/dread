"""dread.constants: the constant registers at a call, the same whatever ran before and whichever thread
asks (2026-10-23: a translation block cached by an earlier run ran past a later run's end, and two
threads in one Unicorn engine crashed the process)."""
import os
import subprocess
import sys
import textwrap
import threading

from conftest import ROOT, gizmo_dir
from dread import constants
from dread.program import Program
from dread.target import Target


def call_sites(program):
    return [(fn, a) for fn in program.functions for a in fn.insns
            if program.insn(a) is not None and program.arch.flow(program.insn(a))[0] == "call"]


def test_the_answers_do_not_depend_on_what_ran_before():
    """Every call site answered in one thread in program order (one engine for all) as in a thread of
    its own (a fresh engine): before the cache was flushed at every call, gizmo-m3's apply_config lost
    r1 = "mode" at 0x80005bc behind its call at 0x80005ae, whose run went through that block with
    another end and left it translated."""
    for variant in ("m3", "a32"):
        target = Target.load(gizmo_dir(variant))
        program = Program(target, target.image())
        spots = call_sites(program)
        fresh = {}

        def alone(fn, a):
            fresh[(fn.name, a)] = constants.at(program, fn, a)
        for fn, a in spots:
            t = threading.Thread(target=alone, args=(fn, a))
            t.start()
            t.join()
        in_order = {(fn.name, a): constants.at(program, fn, a) for fn, a in spots}
        assert in_order == fresh, variant
    cfg = Program(Target.load(gizmo_dir("m3")), Target.load(gizmo_dir("m3")).image())
    fn = cfg.by_name["apply_config"]
    constants.at(cfg, fn, 0x80005ae)
    assert constants.string_at(cfg, constants.at(cfg, fn, 0x80005bc)["r1"]) == "mode"


def test_threads_ask_at_once():
    """Eight threads asking at once, in a process of its own (a crash of Unicorn's would end pytest):
    it ends normally, and every answer is the one asked alone."""
    script = textwrap.dedent("""
        import concurrent.futures, sys
        sys.path.insert(0, %r)
        from dread import constants
        from dread.program import Program
        from dread.target import Target
        program = Program(Target.load(%r), Target.load(%r).image())
        spots = [(fn, a) for fn in program.functions for a in fn.insns
                 if program.insn(a) is not None and program.arch.flow(program.insn(a))[0] == "call"]
        want = {(fn.name, a): constants.at(program, fn, a) for fn, a in spots}
        def one(k):
            order = spots[k %% len(spots):] + spots[:k %% len(spots)]
            return sum(constants.at(program, fn, a) != want[(fn.name, a)] for fn, a in order)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            print("differ", sum(pool.map(one, range(80))))
        """) % (ROOT, gizmo_dir("m3"), gizmo_dir("m3"))
    got = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=600,
                         cwd=os.path.dirname(ROOT))
    assert got.returncode == 0, (got.returncode, got.stderr[-2000:])
    assert got.stdout.strip() == "differ 0", got.stdout
