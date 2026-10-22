"""dread readable: the transformations from lifted C to readable C and the gate that decides which
functions are built from it. The gate on a copy of gizmo-m3: with the transformations as they are,
every lifted function is accepted and the build is the one lifted C gives (so far they change no
code); a stage that breaks one function leaves that one lifted and the others readable."""
import os
import shutil

import pytest

from dread import build, cparse, readable
from conftest import gizmo_dir

needs_gcc = pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")


def lifted_text(target, image, name, tmp_path):
    build.lift_sources(target, image, [name], str(tmp_path / "one"), quiet=True)
    return (tmp_path / "one" / (name + ".c")).read_text()


@needs_gcc
def test_a_lifted_file_comes_back_readable_or_not_at_all(tmp_path):
    from dread.target import Target
    target = Target.load(gizmo_dir("m3"))
    image = target.image()
    text = lifted_text(target, image, "fmt_dec", tmp_path)
    out = readable.transform("fmt_dec", text, stages=[])         # (no stage: the same tree)
    assert out is not None and cparse.parse(out) == cparse.parse(text)
    out = readable.transform("fmt_dec", text)                    # (the stages: shorter, still C of the dialect)
    assert out is not None and cparse.parse(out).function("fmt_dec") is not None and len(out) < len(text)
    assert readable.transform("fmt_dec", "u32 fmt_dec(u32 a0) { return ({ a0; }); }") is None   # not the dialect
    assert readable.transform("other", text) is None                                         # no such function
    seen = []
    out = readable.transform("fmt_dec", text, stages=[("look", lambda fn, run: seen.append(fn.name))])
    assert seen == ["fmt_dec"]
    change = readable.chooser(["fmt_dec"])
    assert change("fmt_dec", text) is not None and change("parse_uint", text) is None


@needs_gcc
def test_device_registers_by_name_and_their_bits_as_bit_tests(tmp_path):
    """gizmo-m3's UART: the status register's bit tests as such (not the register's own address as a
    bound: io_40011004 << 31 < 0x40011004u), x << 30 >> 31 as a field of x, and the data register's
    address in r3 carried into the IT block's load (flat_view), which then names it."""
    from dread import lift, regflow
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir("m3"))
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    context = readable.Context(target, image, program)

    def text(name):                         # (what dread.build hands the transform: no header lines)
        return readable.transform(name, lifter.lift(program.by_name[name]).text, context=context)
    putc = text("uart_putc")
    assert "while (!(io_40011004 & 1));" in putc, putc
    getc = text("uart_getc")
    assert "(io_40011004 >> 1) & 1u" in getc and "r0 = io_40011000;" in getc, getc
    assert "#define io_40011000 (*(volatile u32 *)0x40011000u)" in getc, getc


def gizmo_copy(tmp_path):
    """gizmo in tmp_path (its builds share sources), gizmo-m3's target with the cases the lift check
    accepted its functions with when they are cached."""
    from conftest import GIZMO
    from dread.target import Target
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    cases = os.path.join(gizmo_dir("m3"), "build", "autocases")
    if os.path.isdir(cases):
        shutil.copytree(cases, copy / "m3" / "build" / "autocases")
    return Target.load(str(copy / "m3"))


@pytest.mark.slow
@needs_gcc
def test_the_gate_takes_every_lifted_function_and_builds_the_same_image(tmp_path, monkeypatch):
    """With no stage (the identity): every lifted function, and the image lifted C gives."""
    monkeypatch.setattr(readable, "STAGES", [])
    target = gizmo_copy(tmp_path)
    image = target.image()
    lifted = build.read_names(image.table("lifted"))
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert accepted == sorted(lifted), lines
    assert build.read_names(image.table("readable")) == sorted(lifted)
    a = open(build.build(target, image, out=str(tmp_path / "with"), quiet=True), "rb").read()
    os.remove(image.table("readable"))
    b = open(build.build(target, image, out=str(tmp_path / "without"), quiet=True), "rb").read()
    assert a == b


@pytest.mark.slow
@needs_gcc
def test_a_stage_that_breaks_a_function_leaves_it_lifted(tmp_path, monkeypatch):
    """A stage that makes fmt_dec's every return one more: its cases fail, so fmt_dec is built from
    its lifted C again and is not in the readable table; the others are."""
    target = gizmo_copy(tmp_path)
    image = target.image()

    def off_by_one(fn, run=None):
        if fn.name != "fmt_dec":
            return

        def walk(stmts):
            for s in stmts:
                if isinstance(s, cparse.Return) and s.e is not None:
                    s.e = cparse.Binary("+", s.e, cparse.Num("1"))
                for child in ("body", "then", "orelse"):
                    inner = getattr(s, child, None)
                    if isinstance(inner, cparse.Block):
                        walk(inner.body)
                    elif isinstance(inner, cparse.Stmt):
                        walk([inner])
        walk(fn.body.body)
    monkeypatch.setattr(readable, "STAGES", [("off by one", off_by_one)])
    lifted = build.read_names(image.table("lifted"))
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert "fmt_dec" in lifted and "fmt_dec" not in accepted, lines
    assert accepted == sorted(n for n in lifted if n != "fmt_dec"), lines
    assert any(line.startswith("lifted   fmt_dec") and "FAIL fmt_dec" in line for line in lines), lines
    verdicts = open(os.path.join(target.build, "readable-check", image.name, "verdicts.tsv")).read()
    assert "fmt_dec\tlifted\tFAIL fmt_dec" in verdicts


@pytest.mark.slow
@needs_gcc
def test_the_clean_up_passes_the_gate_and_is_shorter(tmp_path):
    """With the clean-up (dread.tidy): every lifted function of gizmo-m3 is accepted, the whole image
    agrees, and the readable C has fewer lines than the lifted (the flags, the (void)s, the zeroed
    registers after calls are gone)."""
    target = gizmo_copy(tmp_path)
    image = target.image()
    lifted = build.read_names(image.table("lifted"))
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert accepted == sorted(lifted), lines
    before = after = 0
    for name in lifted:
        text = lifted_text(target, image, name, tmp_path)
        before += text.count("\n")
        after += readable.transform(name, text).count("\n")
    assert after < before * 0.8, (before, after)


@needs_gcc
def test_the_command_prints_readable_c_and_the_shapes(capsys):
    """dread readable NAME and --shapes run (a commit took the --shapes option out and kept its use:
    every call but --check failed, and no test called the command); --shapes --score counts the kinds
    the parameters point to per source structure (gizmo's ring_put and ring_get analysed alone: no
    caller hands one ring to both, so one source structure is two kinds)."""
    assert readable.main(["--target", gizmo_dir("m3"), "fmt_dec"]) in (0, None)
    assert "fmt_dec" in capsys.readouterr().out
    assert readable.main(["--target", gizmo_dir("m3"), "--shapes", "fmt_dec"]) in (0, None)
    assert "dread readable --shapes:" in capsys.readouterr().out
    pytest.importorskip("elftools")             # (--score: gizmo's ring, our kinds per source structure)
    elf = os.path.join(gizmo_dir("m3"), "original", "gizmo.elf")
    assert readable.main(["--target", gizmo_dir("m3"), "--shapes", "--score", elf, "ring_put", "ring_get"]) in (0, None)
    out = capsys.readouterr().out
    assert ("dread readable --shapes --score: 1 source structures in 2 kinds that parameters point to (1 beyond "
            "one each); 0 kinds paired with several source structures") in out, out


def test_the_trees_made_in_a_pool_are_those_made_in_one_process(monkeypatch):
    """readable_trees, analysis_trees, views and question_trees give the same in worker processes as in
    this one."""
    from dread import lift, regflow, readable
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir("m3"))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = readable.Context(target, image, program, flow)
    names = [fn.name for fn in program.functions]
    made = {}
    for jobs in (1, 3):
        monkeypatch.setattr(readable, "POOL_MIN", 1 if jobs > 1 else 10 ** 6)
        d1, d2 = {}, {}
        made[jobs] = ([readable.print_file(t, f) for t, f, _ in readable.readable_trees(program, context, lifter,
                                                                                       names, digests=d1, jobs=jobs)],
                      [readable.print_file(t, f, stages=[]) for t, f, _ in readable.analysis_trees(
                          program, context, lifter, names, digests=d2, jobs=jobs)],
                      d1, d2, readable.views(program, context, lifter, names, jobs=jobs),
                      {n: got if isinstance(got, str) else got[2:]     # (digest, file, code)
                       for n, got in readable.question_trees(program, context, lifter, names, jobs=jobs).items()})
    assert made[1] == made[3] and len(made[1][0]) > 40 and len(made[1][4]) > 40
    assert sum(not isinstance(got, str) for got in made[1][5].values()) > 40


def test_a_pool_takes_no_more_workers_than_the_memory_holds(monkeypatch):
    from dread.util import polite
    monkeypatch.delenv("DREAD_JOBS", raising=False)
    monkeypatch.setattr(polite, "available_memory", lambda: 10 << 30)
    many = polite.workers()
    assert polite.workers(memory_each=4 << 30) == min(many, 2)
    assert polite.workers(memory_each=64 << 30) == 1
    monkeypatch.setattr(polite, "available_memory", lambda: None)      # (not known: threads decide)
    assert polite.workers(memory_each=4 << 30) == many
    assert polite.resident() is None or polite.resident() > 1 << 20


WRAPPED = """/* f at 0x0, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 g(u32, u32, u32, u32);
__attribute__((noinline, used)) u32 f_dread_body(u32 a0, u32 a1, u32 a2, u32 a3, u32 sp0)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r12 = 0, lr = (u32)__builtin_return_address(0);
    (void)r12; (void)lr; (void)sp0;
    r0 = g(r0, r1, r2, r3); r1 = r2 = r3 = r12 = 0;  /* 4: bl g */
    return r0;  /* 8: pop {pc} */
}

__attribute__((naked)) void f(void)
{
    __asm__ volatile ("mov r12, sp\\n\\tsub sp, sp, #16\\n\\tpush {r12, lr}\\n\\tbl f_dread_body\\n\\tpop {r12, lr}\\n\\tadd sp, sp, #16\\n\\tbx lr");
}
"""


def test_the_view_of_a_function_with_a_body_shows_the_body():
    """A variadic function lifted as a naked entry and its body: the view a question shows has both
    (the entry alone named FreeRTOS's printf a trampoline)."""
    text = readable.view("f", WRAPPED)
    assert text is not None and "f_dread_body(u32 a0" in text and "g(r0, r1, r2, r3)" in text, text
    assert text.index("f_dread_body(u32") < text.index("void f(void)")


def test_a_call_saved_line_lets_the_stages_run_and_a_fixed_line_does_not():
    """A CALL_SAVED line (-fcall-saved-REG) constrains only GCC's register allocation: the stages run
    on such a file and keep the line, so that dread.build still compiles it so. A FIXED line (the C
    reads registers as the caller left them, through asm) keeps the file as the lifter wrote it."""
    from dread import build
    body = ('/* h at 0x0, lifted by dread lift: one statement per instruction. */\n#include "dread.h"\n'
            'u32 h(u32 a0, u32 a1, u32 a2, u32 a3)\n{\n'
            '    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r12 = 0;\n    u32 n = 0, z = 0, c = 0, v = 0;\n'
            '    (void)n; (void)z; (void)c; (void)v; (void)r12;\n'
            '    r0 = r0 + r1;  /* 0: adds r0, r1 */\n    return r0;  /* 2: bx lr */\n}\n')
    saved = readable.transform("h", build.CALL_SAVED + "r12 */\n" + body)
    assert saved is not None and saved.startswith(build.CALL_SAVED + "r12 */"), saved
    assert "u32 h(u32 r0, u32 r1)" in saved and "return r0 + r1;" in saved and "(void)" not in saved, saved
    fixed = readable.transform("h", build.FIXED + "r4 */\n" + body)
    assert fixed is not None and "(void)n" in fixed, fixed
