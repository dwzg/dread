"""dread uncovered: why a lift check's cases leave instructions unrun. On every gizmo build (a branch on
a callee's result, a switch on an argument, the command), and on a small program of its own: each
source of what decides a region (an argument, a global, a callee's result, a device register, a field,
a local a callee fills, a loop's counter, a switch's index), the causes without a branch (a call that
never came back, runs that ended in a block, code no edge leads into, a function no case entered), and
the whole command with cases that run, the values they saw and a forced run."""
import json
import shutil
import subprocess

import pytest

from conftest import NOIPA_DEF
from dread import uncovered
from dread.autocase import Generator, cache_store
from dread.target import Target

needs_arm_gcc = pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")

PROBES_C = NOIPA_DEF + r"""
#define DEVICE_STATUS (*(volatile unsigned *)0x40000014)
struct probe_box { unsigned pad[3]; unsigned mode; };
volatile unsigned probe_level;
NOIPA unsigned probe_source(unsigned x) { return x * 3 + 1; }
NOIPA void probe_sink(unsigned x) { probe_level = x; }
NOIPA void probe_fill(unsigned *out) { *out = probe_level + 2; }
NOIPA unsigned on_argument(unsigned a) { if (a == 0x1234) { probe_sink(a); return 7; } return 1; }
NOIPA unsigned on_global(void) { if (probe_level == 0x55) { probe_sink(9); return 9; } return 2; }
NOIPA unsigned on_callee(unsigned a) { if (probe_source(a) == 0x99) { probe_sink(a); return 11; } return 3; }
NOIPA unsigned on_device(void) { if (DEVICE_STATUS & 0x80) { probe_sink(13); return 13; } return 4; }
NOIPA unsigned on_field(struct probe_box *b) { if (b->mode == 3) { probe_sink(5); return 5; } return 0; }
NOIPA unsigned on_filled(void) { unsigned v; probe_fill(&v); if (v == 8) { probe_sink(v); return 1; } return 0; }
NOIPA unsigned after_rounds(unsigned a)
{
    unsigned i;
    for (i = 0; i < 100; i++)
        probe_sink(i);
    probe_sink(a);
    return a;
}
NOIPA unsigned probe_switch(unsigned k)
{
    switch (k) {
    case 0: probe_sink(10); break;
    case 1: probe_sink(21); break;
    case 2: probe_sink(32); break;
    case 3: probe_sink(43); break;
    case 4: probe_sink(54); break;
    case 5: probe_sink(65); break;
    case 6: probe_sink(76); break;
    }
    return k + 1;
}
NOIPA unsigned after_call(unsigned a) { unsigned x = probe_source(a); probe_sink(x); return x + 1; }
__attribute__((naked)) unsigned with_dead_code(unsigned a)
{
    __asm__ volatile("cmp r0, #0\n\tbeq 1f\n\tbx lr\n\tmovs r0, #9\n\tbx lr\n1:\tmovs r0, #1\n\tbx lr\n");
}
"""

SCRATCH = 0x20100000                        # test_bench.TARGET's scratch


@pytest.fixture(scope="module")
def probes(tmp_path_factory):
    """The probe program built for the Cortex-M3 and imported as a target."""
    if not shutil.which("arm-none-eabi-gcc"):
        pytest.skip("needs arm-none-eabi-gcc")
    from test_bench import TARGET
    from dread import elfimport
    tmp = tmp_path_factory.mktemp("probes")
    (tmp / "t.c").write_text(PROBES_C)
    (tmp / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                              " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                              "SECTIONS { .text : { *(.text*) } > FLASH .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp / "t.ld"), "-e", "on_argument", "-o", elf, str(tmp / "t.c")], check=True)
    (tmp / "original").mkdir()
    (tmp / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                    '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp), "--payload", str(tmp / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    target = Target.load(str(tmp))
    return target, Generator(target, target.image()), uncovered.prepare(target, target.image(), runs=False)


def missed_by(gen, name, case):
    """The instructions of the function one case leaves unrun (the run may end anywhere)."""
    fn = gen.program.by_name[name]
    ran, _ = gen.attempt(fn.entry, fn.insns, dict({"mem": [], "args": [0, 0, 0, 0]}, **case))
    assert ran, name
    return sorted(set(fn.insns) - ran)


def regions(ctx, name, missed):
    """The regions of the missed instructions (the context from prepare: with dread.regflow, which says
    what a callee leaves alone; GCC 7 keeps after_rounds' counter in r2 across its call)."""
    return uncovered.analyse(ctx, name, missed)[1]


@pytest.mark.parametrize("name, case, source, text", [
    ("on_argument", {"args": [0]}, "argument", "argument r0"),
    ("on_global", {}, "global", "global 0x2"),
    ("on_callee", {"args": [0]}, "callee", "the result of probe_source"),
    ("on_device", {}, "device", "device register 0x40000014"),
    ("on_field", {"args": [SCRATCH], "mem": [[SCRATCH + 12, 4, 0]]}, "field", "field +0xc of argument r0"),
    ("on_filled", {}, "callee", "what probe_fill stores in the local"),
])
def test_what_decides_a_branch(probes, name, case, source, text):
    """The one region the case leaves (the call in the branch's other way) is entered by that branch,
    and what the branch compares comes from the source the code makes it of."""
    _, gen, ctx = probes
    got = regions(ctx, name, missed_by(gen, name, case))
    assert len(got) == 1, got
    region = got[0]
    assert region["cause"] == "branch" and region["source"] == source, region
    assert text in region["text"], region["text"]


def test_a_loop_counter_and_a_switch(probes):
    """The code behind a loop that ran fewer rounds than it takes (its runs ended at their twentieth
    stub call) is entered by the loop's exit, which compares the counter the loop carries; a switch's
    cases no run took are entered by the switch, whose index is the argument."""
    target, gen, ctx = probes
    sink = gen.program.by_name["probe_sink"].entry
    missed = missed_by(gen, "after_rounds", {"args": [5], "calls": [[sink, 1, 0]], "stub_calls": 20, "hang": True})
    got = regions(ctx, "after_rounds", missed)
    assert [r["source"] for r in got] == ["loop"] and got[0]["cause"] == "branch", got
    got = regions(ctx, "probe_switch", missed_by(gen, "probe_switch", {"args": [0]}))
    assert got and all(r["source"] == "argument" for r in got), got
    assert any(r["cause"] == "switch" for r in got) or all(r["cause"] == "branch" for r in got)


def test_the_causes_without_a_branch(probes):
    """A call that never came back (the code behind it missed), a run that ended inside a block (the
    second of two instructions without a branch between), code no edge leads into (behind a return:
    the lifter's control flow has no way there) and a function no case entered."""
    _, gen, ctx = probes
    prog = gen.program
    fn = prog.by_name["after_call"]
    call = next(a for a in fn.insns if prog.arch.flow(prog.insn(a))[0] == "call")
    got = regions(ctx, "after_call", [a for a in fn.insns if a > call])
    assert [r["cause"] for r in got] == ["call"] and got[0]["ways"][0]["callee"] == "probe_source", got
    flow = uncovered.Flow(ctx, prog.by_name["on_argument"])
    tail = next(a for a in flow.code if flow.kind[a][0] == "return")     # behind a move and a plain jump
    assert all(flow.kind[p][0] in ("next", "jump") and not flow.kind[p][2] for p in flow.pred[tail])
    assert [r["cause"] for r in regions(ctx, "on_argument", [tail])] == ["straight"]
    dead = prog.by_name["with_dead_code"].insns[3:5]        # movs r0, #9; bx lr: no way leads there
    assert [r["cause"] for r in regions(ctx, "with_dead_code", dead)] == ["unreached"]
    everything = prog.by_name["on_global"].insns
    got = regions(ctx, "on_global", everything)
    assert [(r["cause"], r["instructions"]) for r in got] == [("not entered", len(everything))]


def test_the_command_with_cases_and_a_forced_run(probes, tmp_path, monkeypatch):
    """The command over a lift check's verdicts: the regions with what the cached cases saw at their
    frontier (the compared register's values), a forced run that goes the missed way and returns, a
    function whose generation failed, and the table; the JSON says the same twice alike."""
    target, gen, ctx = probes
    cases = {"on_argument": [{"args": [0, 0, 0, 0], "mem": []}, {"args": [3, 0, 0, 0], "mem": []}],
             "on_device": [{"args": [0, 0, 0, 0], "mem": [[0x40000014, 4, 1]]}]}
    lines = ["function\tverdict\treason\tmissed"]
    for name, made in cases.items():
        cache_store("%s/autocases/main/%s.json" % (target.build, name), {"key": "made elsewhere", "cases": made})
        missed = missed_by(gen, name, made[0])
        lines.append("%s\tdropped\tUNCOVERED %s: ...\t%s" % (name, name, " ".join("0x%x" % a for a in missed)))
    lines.append("on_global\tdropped\tFAIL on_global [case generation] | only 0 of 100 generated inputs ran\t")
    lines.append("on_callee\taccepted\t\t")
    verdicts = tmp_path / "verdicts.tsv"
    verdicts.write_text("\n".join(lines) + "\n")
    outputs = []
    for k in range(2):
        out = tmp_path / ("out%d.json" % k)
        args = ["--target", target.root, "--verdicts", str(verdicts), "--json", str(out), "-j", "1"]
        assert uncovered.main(args) == 0
        outputs.append(out.read_text())
    assert outputs[0] == outputs[1]
    data = json.loads(outputs[0])
    assert sorted(data["functions"]) == ["on_argument", "on_device", "on_global"]
    arg = data["functions"]["on_argument"]
    assert arg["kind"] == "cases" and arg["cases"]["stale"] and arg["runs"]["runs"] == 2
    (region,) = arg["regions"]
    way = region["ways"][0]
    assert region["key"] == ["branch", "argument"]
    assert way["values"]["reached"] == 2 and any(len(seen) == 2 for seen in way["values"]["registers"].values())
    assert way["forced"]["end"] == "by a return" and way["forced"]["ran"] == region["instructions"]
    assert region["forced"] == "returns"
    device = data["functions"]["on_device"]["regions"][0]
    assert device["key"] == ["branch", "device"] and device["ways"][0]["values"]["registers"]
    failed = data["functions"]["on_global"]
    assert failed["kind"] == "no cases" and failed["regions"][0]["key"] == ["not entered", "generation failed"]
    rows = {(r["cause"], r["source"]): r for r in data["table"]}
    assert rows[("branch", "argument")]["regions"] == 1 and rows[("not entered", "generation failed")]["functions"] == 1
    assert data["totals"]["functions"] == 3


# ---- on gizmo ----

def gizmo_missed(gen, name, case):
    fn = gen.program.by_name[name]
    ran, why = gen.attempt(fn.entry, fn.insns, dict({"mem": [], "args": [0, 0, 0, 0]}, **case))
    assert ran and why is None, why
    return sorted(set(fn.insns) - ran)


def read_line_missed(target, gen):
    """read_line with the serial port empty: uart_getc (another file's: a call on every build) answers
    -1 and the line is given up at once."""
    return gizmo_missed(gen, "read_line", {"args": [target.memory.scratch_base, 16, 0, 0]})


def test_gizmo_branch_on_a_callee_and_a_switch(target):
    """read_line's loop, unrun, is entered by the branch on uart_getc's result; handle_key with a key
    it ignores: every case no run took is entered by the switch (or the compares) on its argument."""
    image = target.image()
    gen, ctx = Generator(target, image), uncovered.prepare(target, image, runs=False)
    got = regions(ctx, "read_line", read_line_missed(target, gen))
    assert got and got[0]["cause"] == "branch" and got[0]["source"] == "callee", got
    assert "the result of uart_getc" in got[0]["text"]
    got = regions(ctx, "handle_key", gizmo_missed(gen, "handle_key", {"args": [ord("z")]}))
    assert got and all(r["source"] == "argument" for r in got), [(r["cause"], r["text"]) for r in got]


def test_gizmo_command(target, tmp_path):
    """The command on a gizmo build with verdicts of its own (the code only: no case runs)."""
    image = target.image()
    gen = Generator(target, image)
    missed = read_line_missed(target, gen)
    verdicts = tmp_path / "verdicts.tsv"
    verdicts.write_text("function\tverdict\treason\tmissed\nread_line\tdropped\tUNCOVERED read_line: ...\t%s\n"
                        % " ".join("0x%x" % a for a in missed))
    out = tmp_path / "out.json"
    assert uncovered.main(["--target", target.root, "--verdicts", str(verdicts), "--no-runs", "--json", str(out),
                           "--quiet"]) == 0
    data = json.loads(out.read_text())
    assert data["totals"] == {"functions": 1, "regions": len(data["functions"]["read_line"]["regions"]),
                              "instructions": len(missed), "kinds": {"cases": 1}, "stale": 0}
    assert data["table"][0]["cause"] == "branch" and data["table"][0]["source"] == "callee"
