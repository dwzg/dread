"""dread readable: the transformations from lifted C to readable C and the gate that decides which
functions are built from it. The gate on a copy of gizmo-m3: with the transformations as they are,
every lifted function is accepted and the build is the one lifted C gives (so far they change no
code); a stage that breaks one function leaves that one lifted and the others readable. How it is
shown: the image's structures once in a header the printed form includes (the compiled form defines
them in each file: the same code), a call's comment naming the call, and the reading form without
the instructions' comments, each statement's first instruction in its margin."""
import os
import re
import shutil

import pytest

from dread import build, cexpr, cparse, readable
from conftest import gizmo_dir

needs_gcc = pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
needs_z3 = pytest.mark.skipif(cexpr.z3 is None, reason="needs z3-solver (the extra 'prove')")


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
    address in r3 carried into the IT block's load (flat_view), which then names it; the IT block's
    three conditional instructions one if/else (selects waits for merge_ifs), whose arms both write r0,
    so that uart_getc takes no parameter (it took r0, read by a select on the path that wrote it)."""
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
    assert "(io_40011004 >> 1) & 1" in getc and "r0 = (u8)io_40011000;" in getc, getc
    assert "u32 uart_getc(void)" in getc, getc
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
def test_a_function_the_system_run_accepted_is_tried_in_the_whole_image(tmp_path, monkeypatch):
    """gizmo-a32's irq_entry, which the lift check accepted by the system run (an interrupt handler: its
    cases cannot test it alone), stayed lifted in the readable check until 2026-10-26 ("readable C is not
    checked there yet"); now its readable C is tried in the whole image with the readable functions', as
    the lift check tried its lifted C, and it is readable. A stage that drops its call of irq_dispatch makes
    the scenarios differ: it stays lifted, found by halving, and every other function stays readable."""
    from conftest import GIZMO
    from dread.target import Target
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    cases = os.path.join(gizmo_dir("a32"), "build", "autocases")
    if os.path.isdir(cases):
        shutil.copytree(cases, copy / "a32" / "build" / "autocases")
    target = Target.load(str(copy / "a32"))
    image = target.image()
    lifted = build.read_names(image.table("lifted"))
    assert "irq_entry" in build.system_names(image.table("lifted"))
    lines = []
    accepted = readable.check(target, image, out=lines.append, jobs=2)
    assert "irq_entry" in accepted and accepted == sorted(lifted), lines

    def drop_call(fn, run=None):
        if fn.name == "irq_entry":
            fn.body.body[:] = [s for s in fn.body.body
                               if not (isinstance(s, cparse.ExprStmt) and isinstance(s.e, cparse.Call))]
    monkeypatch.setattr(readable, "STAGES", readable.STAGES + [("break", drop_call)])
    lines = []
    accepted = readable.check(target, image, out=lines.append, jobs=1)     # (in this process: the stage)
    assert accepted == sorted(n for n in lifted if n != "irq_entry"), lines
    verdicts = open(os.path.join(target.build, "readable-check", image.name, "verdicts.tsv")).read()
    assert re.search(r"^irq_entry\tlifted\taccepted by the system run; the whole image differs", verdicts, re.M), verdicts


@pytest.mark.slow
@needs_gcc
def test_readable_c_that_does_not_compile_stays_lifted_with_the_compilers_first_error(tmp_path, monkeypatch):
    """A stage that writes an undeclared variable into fmt_dec: its readable C does not compile, so it
    stays lifted, and the gate's line and verdicts.tsv give the compiler's first error with the code
    it quotes (dread.build compiler_error); until 2026-10-24 they said only that it did not compile,
    and a person rebuilt the file by hand to see why. The others are readable."""
    target = gizmo_copy(tmp_path)
    image = target.image()

    def plant(fn, run=None):
        if fn.name == "fmt_dec":
            fn.body.body.append(cparse.ExprStmt(cparse.Assign("=", cparse.Name("planted_undeclared"), cparse.Num("1"))))
    monkeypatch.setattr(readable, "STAGES", readable.STAGES + [("plant", plant)])
    lifted = build.read_names(image.table("lifted"))
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert "fmt_dec" in lifted and accepted == sorted(n for n in lifted if n != "fmt_dec"), lines
    why = re.compile(r"its readable C does not compile: fmt_dec\.c:\d+:\d+: error: [^|]*planted_undeclared[^|]* \| "
                     r"planted_undeclared = 1;")
    assert any(line.startswith("lifted   fmt_dec") and why.search(line) for line in lines), lines
    verdicts = open(os.path.join(target.build, "readable-check", image.name, "verdicts.tsv")).read()
    assert re.search(r"^fmt_dec\tlifted\t" + why.pattern, verdicts, re.M), verdicts


def test_the_compilers_first_error_is_one_line():
    """compiler_error: GCC's and clang's first error, with and without the code's line numbers (GCC 9 and
    clang 15 on, and older or -fno-diagnostics-show-line-numbers), and without the code
    (-fno-diagnostics-show-caret); nothing where the output names no error."""
    gcc = ("/w/lifted/f.c: In function 'f':\n"
           "/w/lifted/f.c:7:10: error: assignment to 'u32' {aka 'unsigned int'} from 'volatile struct s_1 *' makes "
           "integer from pointer without a cast [-Wint-conversion]\n"
           "    7 |     r1 = r2 = (volatile struct s_1 *)(r3 = 0);\n      |        ^\n"
           "/w/lifted/f.c:9:5: error: 'x' undeclared (first use in this function)\n")
    assert build.compiler_error(gcc) == (
        "f.c:7:10: error: assignment to 'u32' {aka 'unsigned int'} from 'volatile struct s_1 *' makes integer from "
        "pointer without a cast [-Wint-conversion] | r1 = r2 = (volatile struct s_1 *)(r3 = 0);")
    clang = ("/w/lifted/f.c:7:10: error: incompatible pointer to integer conversion assigning to 'u32' (aka 'unsigned "
             "int') from 'volatile struct s_1 *' [-Wint-conversion]\n"
             "    r1 = r2 = (volatile struct s_1 *)(r3 = 0);\n       ^ ~~~~~~~~~~~~~~~~~~\n1 error generated.\n")
    assert build.compiler_error(clang).endswith("[-Wint-conversion] | r1 = r2 = (volatile struct s_1 *)(r3 = 0);")
    assert build.compiler_error(clang).startswith("f.c:7:10: error: incompatible pointer to integer conversion")
    assert build.compiler_error("/w/f.c:2:10: fatal error: x.h: No such file or directory\ncompilation terminated.\n") \
        == "f.c:2:10: error: x.h: No such file or directory"
    assert build.compiler_error("/w/f.c: In function 'f':\n/w/f.c:3:1: warning: unused [-Wunused]\n") == ""
    assert build.compiler_error("") == ""


@pytest.mark.slow
@needs_gcc
def test_the_clean_up_passes_the_gate_and_is_shorter(tmp_path, monkeypatch):
    """With the clean-up (dread.tidy): every lifted function of gizmo-m3 is accepted, the whole image
    agrees, and the readable C has fewer lines than the lifted (the flags, the (void)s, the zeroed
    registers after calls are gone). No function's readable C is made twice from the same lifted C with
    the same tables in the whole check: the rounds' builds and the whole image's take what its first
    pass made (readable_texts). A round that gives functions their results back (void the first time:
    dread.tidy void_results) makes the readable_types table anew (its types are kept for the digest of
    each function's C); a function's readable C is made anew where what its view of the context holds
    changed (scope_key: its entries and its callees', which of them are void; plan_key)."""
    target = gizmo_copy(tmp_path)
    image = target.image()
    lifted = build.read_names(image.table("lifted"))
    made, real = [], readable._transform

    def counted(name, text, stages, context=None, cleaned=None):
        made.append((name, text, readable.tables_key(context), readable.plan_key(context),
                     readable.scope_key(context, name, text)))
        return real(name, text, stages, context, cleaned)
    monkeypatch.setattr(readable, "_transform", counted)
    monkeypatch.setattr(readable, "POOL_MIN", 10 ** 6)          # (made in this process, where they are counted)
    lines = []
    accepted = readable.check(target, image, out=lines.append)
    assert accepted == sorted(lifted), lines
    twice = sorted({m[0] for m in made if made.count(m) > 1})
    assert not twice, twice
    assert {m[0] for m in made} == set(lifted), sorted(m[0] for m in made)
    before = after = 0
    for name in lifted:
        text = lifted_text(target, image, name, tmp_path)
        before += text.count("\n")
        after += readable.transform(name, text).count("\n")
    assert after < before * 0.8, (before, after)


@pytest.mark.slow
@needs_gcc
def test_the_rounds_make_anew_only_what_changed_and_what_a_full_remake_makes(tmp_path, monkeypatch):
    """dread readable --check --verify on gizmo-m3, whose rounds give functions their results back (void,
    their C leaves another r0 than the original; with GCC 16: memcpy, memset, strncpy, gpio_set,
    handle_key): every round's readable_types table and every readable C its builds take are the same
    as made anew from nothing (verify: VerifyFailed else), byte for byte; the tables after the first are
    made from the analysis trees kept but those of the functions given their results back (made anew
    once each), and the builds after the first pass make anew only the readable C whose entries changed:
    those functions' among it. Until 2026-10-24 every such round made every tree and every function's
    readable C anew."""
    target = gizmo_copy(tmp_path)
    image = target.image()
    lifted = build.read_names(image.table("lifted"))
    trees, texts, plans = [], [], []
    real_tree, real_text, real_plan = readable._analysis_kept, readable._text_one, readable.make_plan
    monkeypatch.setattr(readable, "_analysis_kept", lambda name: trees.append(name) or real_tree(name))
    monkeypatch.setattr(readable, "_text_one", lambda name: (readable._SCOPE and texts.append(name)) or
                        real_text(name))       # (not those --verify makes anew from the whole context)

    def plan(*args, **kwargs):
        got = real_plan(*args, **kwargs)
        if kwargs.get("kept") is not None:
            plans.append(len(trees))
        return got
    monkeypatch.setattr(readable, "make_plan", plan)
    monkeypatch.setattr(readable, "_STORE", {})     # (not the readable C an earlier test made of this program)
    monkeypatch.setattr(readable, "POOL_MIN", 10 ** 6)          # (made in this process, where they are counted)
    lines = []
    accepted = readable.check(target, image, out=lines.append, verify=True)
    assert accepted == sorted(lifted), lines
    given = sorted({line.split()[1] for line in lines if line.startswith("result ")})
    assert given and len(plans) >= 2, (given, plans, lines)
    assert sorted(trees[plans[0]:]) == given, (trees[plans[0]:], given)
    later = texts[len(lifted):]
    assert set(given) <= set(later) and len(later) < len(lifted), (sorted(later), given)
    assert sorted(texts[:len(lifted)]) == sorted(lifted)
    assert any("as made anew from nothing (--verify)" in line for line in lines), lines


@pytest.mark.slow
@needs_gcc
def test_verify_finds_a_view_that_leaves_out_what_a_function_reads(tmp_path, monkeypatch):
    """--verify is what shows a view of the context too narrow: with each function's view cut to its own
    name (its callees' void status and their entries of the readable_types table left out, so that a
    caller of a void function declares it u32), the first pass's readable C differs from the C the whole
    context gives, and the check stops with VerifyFailed (dread readable --check --verify exits 1)."""
    target = gizmo_copy(tmp_path)
    monkeypatch.setattr(readable, "scope_names", lambda name, text: {name})
    monkeypatch.setattr(readable, "_STORE", {})
    monkeypatch.setattr(readable, "POOL_MIN", 10 ** 6)
    with pytest.raises(readable.VerifyFailed) as e:
        readable.check(target, target.image(), out=[].append, verify=True)
    assert "differs from the one made anew from nothing" in str(e.value), e.value


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


@needs_gcc
def test_the_command_explains_the_variables_types(capsys):
    """dread readable --types NAME: per function the table's types, the trials and what they give (gizmo
    m3 carries no table: the trials alone); a name without readable C says so."""
    assert readable.main(["--target", gizmo_dir("m3"), "--types", "strncpy", "no_such_function"]) in (0, None)
    out = capsys.readouterr().out
    assert out.startswith("strncpy: the table's types none\n"), out
    assert "  pointer r0" in out and ", kept\n" in out and "  typed: r0 volatile u8 *" in out, out
    assert "no_such_function: no readable C (it does not lift)" in out, out


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


@pytest.mark.slow
@needs_gcc
def test_a_build_makes_its_readable_c_in_a_pool_as_in_one_process(tmp_path, monkeypatch):
    """dread.build makes the readable C of the functions of the readable table in worker processes
    (POOL_MIN lowered, three workers): byte for byte the files and the image one process makes, and no
    thread left running. Beside a thread of its own the process does not fork (a lock the thread holds
    would stay held in the child; the suite takes Python's warning of such a fork for an error): the
    same files again, made here."""
    import multiprocessing
    import threading
    target = gizmo_copy(tmp_path)
    image = target.image()
    lifted = build.read_names(image.table("lifted"))
    with open(image.table("readable"), "w") as f:
        f.write("".join(n + "\n" for n in lifted))
    forks, real = [], multiprocessing.get_context
    monkeypatch.setattr(multiprocessing, "get_context", lambda *a: forks.append(a) or real(*a))
    monkeypatch.setenv("DREAD_JOBS", "3")
    got = {}
    for way in ("one process", "pool", "beside a thread"):
        monkeypatch.setattr(readable, "POOL_MIN", 10 ** 6 if way == "one process" else 1)
        monkeypatch.setattr(readable, "_STORE", {})            # (nothing kept from the builds before)
        del forks[:]
        stop = threading.Event()
        thread = threading.Thread(target=stop.wait, daemon=True) if way == "beside a thread" else None
        if thread:
            thread.start()
        threads = threading.active_count()
        try:
            out = tmp_path / way.replace(" ", "-")
            payload = build.build(target, image, out=str(out), quiet=True)
            assert threading.active_count() == threads      # (the pool's threads end with it)
        finally:
            stop.set()
            if thread:
                thread.join()
        assert bool(forks) == (way == "pool"), (way, forks)
        got[way] = {p.name: p.read_text() for p in (out / "lifted").glob("*.c")}, open(payload, "rb").read()
    assert got["pool"] == got["one process"] == got["beside a thread"]
    assert len(got["pool"][0]) == len(lifted)


def test_readable_c_is_made_again_only_where_its_inputs_changed(monkeypatch):
    """readable_texts keeps what it made by the context's tables (context_key) and each function's
    lifted C: a new context with the same tables (the next build's, a check's next round) makes
    nothing again; another lifted C makes that function again; another table all of them, with the
    new name."""
    from dread import lift, regflow
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir("m3"))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    texts = {}
    for fn in program.functions:
        try:
            texts[fn.name] = lifter.lift(fn).text
        except lift.Refused:
            pass
    made, real = [], readable._transform
    monkeypatch.setattr(readable, "_transform", lambda name, *rest: made.append(name) or real(name, *rest))
    monkeypatch.setattr(readable, "_STORE", {})
    monkeypatch.setattr(readable, "POOL_MIN", 10 ** 6)          # (made in this process, where they are counted)
    first = readable.readable_texts(texts, readable.Context(target, image, program, flow))
    assert sorted(made) == sorted(texts) and "while (!(io_40011004 & 1));" in first["uart_putc"]
    del made[:]
    assert readable.readable_texts(texts, readable.Context(target, image, program, flow)) == first and not made
    other = dict(texts, uart_putc=texts["uart_putc"].replace("lifted by dread lift", "lifted by hand"))
    assert other["uart_putc"] != texts["uart_putc"]
    again = readable.readable_texts(other, readable.Context(target, image, program, flow))
    assert made == ["uart_putc"] and "lifted by hand" in again["uart_putc"]
    assert {n: t for n, t in again.items() if n != "uart_putc"} == {n: t for n, t in first.items() if n != "uart_putc"}
    del made[:]
    context = readable.Context(target, image, program, flow)
    context.globals = {"0x40011000 u32": {"name": "UART_DATA"}}
    named = readable.readable_texts(texts, context)
    assert sorted(made) == sorted(texts) and "UART_DATA = " in named["uart_putc"], named["uart_putc"]
    assert readable.context_key(context) != readable.context_key(readable.Context(target, image, program, flow))


def test_a_function_s_readable_c_is_made_again_only_where_its_entries_changed(monkeypatch):
    """readable_texts keeps each function's readable C by what its view of the context holds (scoped,
    scope_key: the readable_types table's entries of the names its lifted C uses and which of them are
    void; plan_key: the table's parts that are no function's): a function given its result back (the
    check's keep_results) makes it and the functions whose C names it anew, another function's entry the
    same, a structure added to the table every function; and every time the C kept is the C made anew
    from the whole context (remade)."""
    from dread import lift, regflow
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir("m3"))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    texts = {n: lifter.lift(program.by_name[n]).text
             for n in ("timer_start", "delay", "uart_putc", "uart_puts", "keys_read", "strlen", "checksum")}
    made, real = [], readable._transform
    monkeypatch.setattr(readable, "_transform", lambda name, *rest: made.append(name) or real(name, *rest))
    monkeypatch.setattr(readable, "_STORE", {})
    monkeypatch.setattr(readable, "POOL_MIN", 10 ** 6)          # (made in this process, where they are counted)
    context = readable.Context(target, image, program, flow)
    assert "timer_start" in context.void

    def naming(name):
        return {n for n, t in texts.items() if re.search(r"\b%s\b" % name, t)}

    def round_(change):
        change()
        del made[:]
        kept = readable.readable_texts(texts, context)
        got = sorted(made)
        assert readable.remade(texts, context) == kept      # (as made anew from nothing, from the whole context)
        return got, kept
    got, first = round_(lambda: setattr(context.plan, "masks", {}))    # (None: "the table does not say")
    assert got == sorted(texts) and "void timer_start(" in first["timer_start"], first["timer_start"]
    assert naming("timer_start") == {"timer_start", "delay"} and "timer_start(r0);" in first["delay"], first["delay"]
    got, again = round_(lambda: context.keep_results(["timer_start"]))
    assert got == ["delay", "timer_start"] and "u32 timer_start(" in again["timer_start"], again["timer_start"]
    assert {n: t for n, t in again.items() if n not in got} == {n: t for n, t in first.items() if n not in got}
    got, _ = round_(lambda: setattr(context.plan, "masks", dict(context.plan.masks, uart_putc=[0])))
    assert got == sorted(naming("uart_putc")) and "uart_puts" in got, got
    got, _ = round_(lambda: setattr(context.plan, "structs", dict(context.plan.structs, s_99=[(0, "u32")])))
    assert got == sorted(texts), got
    got, _ = round_(lambda: None)
    assert got == [], got


def test_a_new_context_gets_readable_c_made_with_its_own_tables():
    """readable.transform keeps its results for a context while the context lives: one made after
    another died gets C made with its own tables. Until 2026-10-23 the results were kept by the
    context's id, which a new context often takes over from a dead one (38 of 50 in a row got the
    dead one's names); a copy of a context (dread pilot struct_uses) has results of its own too."""
    import copy
    from dread import lift, readable, regflow
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir("m3"))
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    text = lift.make_lifter(program, flow).lift(program.by_name["uart_putc"]).text
    for i in range(20):
        context = readable.Context(target, image, program, flow)
        context.globals = {"0x40011000 u32": {"name": "DATA_%d" % i}}
        assert "DATA_%d = " % i in readable.transform("uart_putc", text, context=context)
        other = copy.copy(context)
        other.globals = {"0x40011000 u32": {"name": "OTHER_%d" % i}}
        assert "OTHER_%d = " % i in readable.transform("uart_putc", text, context=other)
        assert "DATA_%d = " % i in readable.transform("uart_putc", text, context=context)
        del context, other


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
    (the entry alone named FreeRTOS's printf a trampoline). Since 2026-10-25 the body is the function
    readable C is made of (readable.picked): readable, last, as a function is shown behind the file's
    others; the entry in front of it as the lifter wrote it (before, the body came first, as lifted)."""
    text = readable.view("f", WRAPPED)
    assert text is not None and "f_dread_body(u32 a0" in text and "return g(r0, r1, r2, r3);" in text, text
    assert text.index("void f(void)") < text.index("f_dread_body(u32") and "(void)r12" not in text, text


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


@needs_gcc
def test_a_check_whose_build_fails_exits_1_and_keeps_the_tables(tmp_path):
    """dread readable --check and dread lift --check on a build that fails (gizmo without the
    rewritten directory its src/rewritten.c includes): exit 1 with the reason, the readable and
    lifted tables as they were (until 2026-10-23 both exited 0 and a comparison of the tables read
    "the same")."""
    from dread import lift
    target = gizmo_copy(tmp_path)
    image = target.image()
    shutil.rmtree(tmp_path / "gizmo" / "rewritten")
    lifted_before = open(image.table("lifted")).read()
    assert not os.path.exists(image.table("readable"))
    assert readable.main(["--target", target.root, "--check"]) == 1
    assert not os.path.exists(image.table("readable"))
    some = build.read_names(image.table("lifted"))[:2]
    assert lift.main(["--target", target.root, "--check"] + some) == 1
    assert open(image.table("lifted")).read() == lifted_before


WIDE_S = """
    .syntax unified
    .arch armv4t
    .arm
    .text
    .global udivmod, fits, remainder_of, store_mean, use
    .type udivmod, %function
udivmod:                        @ r1 / r0 in r0, r1 % r0 in r1 (unsigned), as a runtime library divides; r0 = 0: 0 and r1
    mov     r2, r0
    mov     r0, #0
    cmp     r2, #0
    bxeq    lr
    mov     r3, #32
    mov     r12, #0
.Lbit:
    movs    r1, r1, lsl #1
    adc     r12, r12, r12
    cmp     r12, r2
    subhs   r12, r12, r2
    adc     r0, r0, r0
    subs    r3, r3, #1
    bne     .Lbit
    mov     r1, r12
    bx      lr
    .size udivmod, . - udivmod
    .type fits, %function
fits:                           @ whether a header and n records of x bytes fit in 64 KiB: x <= 0xffff / (n + 1)
    push    {r4, lr}
    mov     r4, r0
    add     r0, r1, #1
    ldr     r1, =0xffff
    bl      udivmod             @ the quotient alone: the remainder in r1 is not read
    cmp     r4, r0
    movls   r0, #1
    movhi   r0, #0
    pop     {r4, lr}
    bx      lr
    .size fits, . - fits
    .type remainder_of, %function
remainder_of:                   @ the remainder: a caller that reads udivmod's second word
    push    {r4, lr}
    bl      udivmod
    mov     r0, r1
    pop     {r4, lr}
    bx      lr
    .size remainder_of, . - remainder_of
    .type store_mean, %function
store_mean:                     @ the mean of a sum over n + 1 samples, halved, plus 3, stored to a word
    push    {r4, lr}
    mov     r2, r0
    add     r0, r1, #1
    mov     r1, r2
    bl      udivmod
    mov     r1, r0
    mov     r1, r1, lsr #1
    add     r1, r1, #3
    ldr     r2, =0x40000100
    str     r1, [r2]
    mov     r0, #0
    pop     {r4, lr}
    bx      lr
    .size store_mean, . - store_mean
    .ltorg
    .type use, %function
use:
    push    {r4, lr}
    mov     r4, r0
    bl      fits
    add     r0, r0, r4
    ldr     r1, =0x40000110     @ (what it computes is read: nothing is void, dread.tidy void_results)
    str     r0, [r1]
    pop     {r4, lr}
    bx      lr
    .ltorg
    .size use, . - use
"""
WIDE_NAMES = ["fits", "remainder_of", "store_mean", "udivmod", "use"]
needs_arm = pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                               reason="needs arm-none-eabi-as and -gcc")


def wide_program(tmp_path):
    """WIDE_S as a target, its functions lifted (dread lift --check)."""
    from dread import lift
    from test_lift_results import arm_program
    t = arm_program(tmp_path, "a32", WIDE_S)
    lines = []
    assert sorted(lift.check(t, t.image(), out=lines.append, jobs=2)) == WIDE_NAMES, lines
    return t


@needs_z3
@needs_arm
def test_a_two_word_results_first_word_compared_is_32_bits_and_the_gate_takes_it(tmp_path):
    """fits compares its argument with the quotient of udivmod, which hands back two words (its
    remainder in r1, which remainder_of reads: the lifter declares udivmod u64), and store_mean stores
    a sum of the quotient halved. Their readable C takes the first word, (u32)udivmod(...), and the gate
    takes them and use, whose cases run fits for real. Until 2026-10-24 the values pass wrote
    r0 = udivmod(...) for r0 = (u32)udivmod(...), fold wrote that into its reader bare, and the
    conditions search, which took every call for unsigned int, kept r0 <= udivmod(r1 + 1, 0xffffu):
    64 bits compared, the remainder as the high word; the store shifted the remainder's bit in. The
    gate turned fits and store_mean down, and use with them (on a private firmware the same sent back
    functions that compare or shift a division's quotient, and their callers with them)."""
    from dread import lift, regflow
    from dread.program import Program
    t = wide_program(tmp_path)
    p = Program(t, t.image())
    flow = regflow.analyse(p)

    def text(name):
        return readable.transform(name, lift.make_lifter(p, flow).lift(p.by_name[name]).text,
                                  context=readable.Context(t, t.image(), p, flow))
    out = text("fits")
    assert "extern u64 udivmod(u32, u32);" in out and "return r0 <= (u32)udivmod(r1 + 1, 0xffffu) ?" in out, out
    out = text("store_mean")
    assert "((u32)udivmod(r1 + 1, r0) >> 1) + 3;" in out, out
    lines = []
    assert readable.check(t, t.image(), out=lines.append, jobs=2) == WIDE_NAMES, lines


def off_by_one(name):
    """A stage that makes every return of function name one more."""
    def stage(fn, run=None):
        if fn.name != name:
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
    return stage


@needs_arm
def test_a_caller_of_a_function_that_fails_is_tried_again_with_it_lifted(tmp_path, monkeypatch):
    """A stage that breaks fits (one more on every return): its cases fail, and so do use's, whose
    cases run fits for real. use is tried once more with fits from lifted C, passes and is readable;
    fits stays lifted. Until 2026-10-24 use went back with fits."""
    t = wide_program(tmp_path)
    monkeypatch.setattr(readable, "STAGES", readable.STAGES + [("off by one", off_by_one("fits"))])
    lines = []
    accepted = readable.check(t, t.image(), out=lines.append, jobs=2)
    assert accepted == [n for n in WIDE_NAMES if n != "fits"], lines
    assert any(line.startswith("lifted   fits") and "FAIL fits" in line for line in lines), lines
    assert any(line.startswith("again    use") and "fits" in line for line in lines), lines


VARARG_S = """
    .syntax unified
    .arch armv4t
    .arm
    .text
    .global vsum, sum3, use
    .type vsum, %function
vsum:                           @ the sum of the n words that follow n (n at most 3), its frame kept as APCS keeps it
    mov     ip, sp
    push    {r1, r2, r3}        @ the variadic arguments where a va_list finds them
    push    {r4, ip, lr}
    add     r1, sp, #12         @ va_start: the first word after n
    mov     r2, #0
    cmp     r0, #3
    movhi   r0, #3
.Lloop:
    subs    r0, r0, #1
    bmi     .Ldone
    ldr     r3, [r1], #4
    add     r2, r2, r3
    b       .Lloop
.Ldone:
    mov     r0, r2
    pop     {r4, ip, lr}
    add     sp, sp, #12
    bx      lr
    .size vsum, . - vsum
    .type sum3, %function
sum3:                           @ a + b + c through vsum
    push    {r4, lr}
    mov     r3, r2
    mov     r2, r1
    mov     r1, r0
    mov     r0, #3
    bl      vsum
    pop     {r4, lr}
    bx      lr
    .size sum3, . - sum3
    .type use, %function
use:
    push    {r4, lr}
    bl      sum3
    add     r0, r0, #1
    ldr     r1, =0x40000110     @ (what it computes is read: nothing is void, dread.tidy void_results)
    str     r0, [r1]
    pop     {r4, lr}
    bx      lr
    .ltorg
    .size use, . - use
"""


@needs_arm
def test_a_save_area_behind_an_apcs_frame_copy_keeps_the_variadic_arguments(tmp_path):
    """vsum keeps the entry's stack pointer first (mov ip, sp, as an APCS frame does), then pushes its
    variadic arguments r1-r3 where its va_list (add r1, sp, #12) reads them: a save area
    (entry_save_areas), though not the first instruction. Its readable C keeps the pushes and takes r1-r3
    (push_room leaves a save area alone), and sum3's call passes them (regflow sees no use of a pushed
    register: vsum reads r0 alone). Until 2026-10-24 only a push at the entry counted: sum3 called
    vsum(3), and vsum read words nothing had written."""
    from dread import lift, regflow
    from dread.program import Program
    from test_lift_results import arm_program
    t = arm_program(tmp_path, "a32", VARARG_S)
    p = Program(t, t.image())
    flow = regflow.analyse(p)
    assert flow.read_regs("vsum") == ["r0"]
    context = readable.Context(t, t.image(), p, flow)
    assert context.save_areas == {"vsum"} and context.arity("vsum") is None
    lifter = lift.make_lifter(p, flow)
    vsum = readable.transform("vsum", lifter.lift(p.by_name["vsum"]).text, context=context)
    assert "u32 vsum(u32 r0, u32 r1, u32 r2, u32 r3)" in vsum and "= r1;" in vsum and "= r3;" in vsum, vsum
    sum3 = readable.transform("sum3", lifter.lift(p.by_name["sum3"]).text, context=context)
    assert "return vsum(3, r0, r1, r2);" in sum3, sum3
    lines = []
    assert lift.check(t, t.image(), out=lines.append, jobs=2) == ["vsum", "sum3", "use"], lines
    assert readable.check(t, t.image(), out=lines.append, jobs=2) == ["sum3", "use", "vsum"], lines


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-ld")),
                    reason="needs arm-none-eabi binutils")
def test_no_argument_in_a_result_register_the_callee_overwrites(tmp_path):
    """A call whose value is read passes no argument in a result register its callee overwrites
    (regflow's overwritten), and a function whose parameter only such an argument read takes none:
    gauge_poll stores a getter's value and took the r0 it passed it (FreeRTOS's xTaskGetTickCount(r0)
    in prvCheckExpectedTimeIsWithinAnAcceptableMargin; gizmo's uart_getc(r0) in read_line), so did a
    tail call of the getter (gauge_relay). The argument stays where the callee may hand it back: an
    empty callee (newlib's lock functions), one that writes r0 on one path only, one that pops r0 back,
    one that adds to what such a callee hands back. An argument kept because the call's value was read
    goes once the clean-up leaves the value unread (gauge_pair: the empty function's r0 went only into
    the getter's call, which lost it)."""
    from dread import lift, regflow
    from dread.program import Program
    from test_regflow import gauge_target
    target = gauge_target(tmp_path)
    image = target.image()
    program = Program(target, image)
    flow = regflow.analyse(program)
    lifter = lift.make_lifter(program, flow)
    context = readable.Context(target, image, program, flow)

    def text(name):
        return readable.transform(name, lifter.lift(program.by_name[name]).text, context=context)
    poll = text("gauge_poll")
    assert "u32 gauge_poll(void)" in poll and "= gauge_now();" in poll, poll
    assert "extern u32 gauge_now(void);" in poll, poll
    since = text("gauge_since")
    assert "u32 gauge_since(u32 r0)" in since and "return gauge_now() - r0;" in since, since
    # (gauge_main reads none of these callers' results: they are void, dread.tidy void_results)
    assert "void gauge_relay(void)" in text("gauge_relay") and "= gauge_via();" in text("gauge_relay")
    for name, callee in (("gauge_keep", "gauge_hold"), ("gauge_try", "gauge_maybe"), ("gauge_reback", "gauge_back"),
                         ("gauge_rebump", "gauge_bump")):
        c = text(name)
        assert "void %s(u32 r0)" % name in c and "%s(r0);" % callee in c, c
        assert "extern u32 %s(u32);" % callee in c, c
    pair = text("gauge_pair")
    assert "void gauge_pair(void)" in pair and "    gauge_hold();" in pair and "= gauge_now();" in pair, pair


def test_read_line_calls_uart_getc_without_an_argument(target):
    """gizmo's read_line (int c = uart_getc();) printed r0 = uart_getc(r0): uart_getc writes its result
    register on every path, so the call passes nothing, as uart_getc's prototype says; uart_getc's own
    readable C takes nothing either (on the ARM builds its IT block wrote r0 on either path, but a select
    read it where the data flow saw the entry's r0 reach it: tidy.selects waits for merge_ifs, and
    params reads the entry's value in flat_view's flow)."""
    from dread import lift, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = readable.Context(target, image, program, flow)

    def text(name):
        return readable.transform(name, lifter.lift(program.by_name[name]).text, context=context)
    line = text("read_line")
    assert "extern u32 uart_getc(void);" in line and "uart_getc(r" not in line, line
    assert "= uart_getc();" in line, line
    getc = text("uart_getc")
    assert "u32 uart_getc(void)" in getc, getc


# A lifted file of the lifters' dialect: a literal two loads use, a call with a constant argument and
# one with a loaded one, and the call's result moved into r4 before the return (GCC's mov r4, r0)
SHARED = """/* f at 0x100, lifted by dread lift: one statement per instruction. */
#include "dread.h"
extern u32 g(u32, u32, u32, u32);
u32 f(u32 a0, u32 a1, u32 a2, u32 a3)
{
    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r12 = 0, lr = 0;
    (void)r12; (void)lr;
    r4 = 0x20000000u;  /* 100: ldr r4, [pc, #0x20] */
    r0 = *(volatile u32 *)(r4 + 4);  /* 102: ldr r0, [r4, #4] */
    r0 = g(r0, r1, r2, r3); r1 = r2 = r3 = r12 = 0;  /* 104: bl g */
    r0 = 0x7u;  /* 108: movs r0, #7 */
    r0 = g(r0, r1, r2, r3); r1 = r2 = r3 = r12 = 0;  /* 10a: bl g */
    r0 = *(volatile u32 *)(r4 + 8);  /* 10e: ldr r0, [r4, #8] */
    r0 = g(r0, r1, r2, r3); r1 = r2 = r3 = r12 = 0;  /* 110: bl g */
    r4 = r0;  /* 114: mov r4, r0 */
    return r4;  /* 116: pop {r4, pc} */
}
"""


def test_a_call_names_its_call_and_the_reading_form_each_statement_s_first_instruction():
    """The comment of a statement that takes a call's result in names the call (return g(...) the bl at
    0x110, not the mov r4, r0 or the pop after it). The reading form (reading) has no instructions'
    comments and gives each statement the address of its first instruction: the call of 7 its movs
    (0x108), each load's call its load (0x102, 0x10e), not the literal both loads use (0x100), which
    belongs to neither."""
    text = readable.transform("f", SHARED)
    assert "    return g(g_20000008, 0, 0, 0);  /* 110: bl g */" in text, text
    lines = readable.reading("f", SHARED)
    assert not any("/*" in line and ": " in line and "readable C" not in line for _, line in lines), lines
    at = {line.strip(): a for a, line in lines if a is not None}
    assert at == {"g(g_20000004, r1, r2, r3);": 0x102, "g(7, 0, 0, 0);": 0x108,
                  "return g(g_20000008, 0, 0, 0);": 0x10e}, lines
    assert lines[0] == (None, "/* f at 0x100: readable C from its lifted C (dread readable) */"), lines
    out = readable.margin_text(lines)
    assert "\n102  " in "\n" + out and "\n     u32 f(" in "\n" + out, out


def test_every_call_names_a_call_and_every_margin_lies_in_its_function(target):
    """Every function of the build that lifts: in its readable C, a statement holding a call that has an
    instruction comment names a call or a jump (a tail call); its reading form has no instructions'
    comments or notes, and every address in its margin is an instruction of its lifted C (the code it
    branches into outside its range too: a32's __aeabi_uidivmod), at or before the statement's own
    instruction and the one its comment names. gizmo's print_status starts its last call where the
    call's argument is loaded, before the call."""
    from dread import lift, regflow
    from dread.cflow import is_pure_call, walk_expr
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = readable.Context(target, image, program, flow)
    checked = 0
    for fn_p in program.functions:
        try:
            text = lifter.lift(fn_p).text
        except lift.Refused:
            continue
        made = readable._named(fn_p.name, text, None, context)
        if made is None:
            continue
        _, fn, _ = made
        own = {}

        def visit(s):
            if s.addr is not None and not isinstance(s, cparse.Note):
                own[id(s)] = s.addr[0]
            if isinstance(s, (cparse.ExprStmt, cparse.Return)) and s.addr is not None and s.e is not None and any(
                    isinstance(x, cparse.Call) and isinstance(x.f, cparse.Name) and not is_pure_call(x)
                    for x in walk_expr(s.e)):
                kind = program.arch.flow(program.insn(cparse.shown(s.addr)[0]))[0]
                assert kind in ("call", "icall", "jump", "ijump"), (fn.name, cparse.unparse(s), cparse.shown(s.addr))
            for inner in cparse._nested(s):
                visit(inner)
        visit(fn.body)
        first = cparse.first_addresses(fn)
        insns = {int(a, 16) for a in re.findall(r"/\* ([0-9a-f]+): ", text)}

        def check(s):
            if s.addr is not None and not isinstance(s, cparse.Note):
                assert first(s.addr) in insns and first(s.addr) <= min(s.addr[0], cparse.shown(s.addr)[0]), (
                    fn.name, cparse.unparse(s), s.addr)
            for inner in cparse._nested(s):
                check(inner)
        check(fn.body)
        lines = readable.reading(fn_p.name, text, context)
        assert not any(re.search(r"/\* [0-9a-f]+: ", line) for _, line in lines), (fn_p.name, lines)
        checked += 1
    assert checked > 30
    text = lifter.lift(program.by_name["print_status"]).text
    lines = readable.reading("print_status", text, context)
    last = [(a, line) for a, line in lines if "uart_puts(" in line][-1]
    call = [s for s in readable._named("print_status", text, None, context)[1].body.body
            if "uart_puts(" in cparse.unparse(s)][-1]
    assert last[0] is not None and last[0] < cparse.shown(call.addr)[0], (last, cparse.shown(call.addr))


@needs_gcc
def test_the_image_s_structures_once_in_a_header_the_printed_form_includes(tmp_path, capsys):
    """gizmo-m3 with a readable_types plan made over all its functions (the ring behind ring_put's and
    ring_get's pointer, s_1, and the static ring timer_irq and main hand them): the header defines each
    structure once; a function's printed form (printed: what dread readable prints and --out writes)
    includes it instead of defining them, names the members it uses in a comment, and keeps its macros,
    externs and its frame's objects; the compiled form (transform: what the gate builds) still
    defines them, and both compile to the same code. dread readable --out writes the header beside the
    files, --header prints it."""
    from dread import lift, regflow, shapes
    from dread.elf import Elf
    from dread.program import Program
    target = gizmo_copy(tmp_path)
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    lifter = lift.make_lifter(program, flow)
    context = readable.Context(target, image, program, flow)
    names = [fn.name for fn in program.functions]
    digests = {}
    trees = readable.analysis_trees(program, context, lifter, names, digests=digests)
    found, arrays, s = readable.static_plan(program, context, trees, flow)
    p = shapes.plan(s, trees, digests)
    p.statics, p.arrays = found, arrays
    shapes.store_plan(image, p)
    assert p.typed.get("ring_put") and p.statics, (p.typed, p.statics)
    context = readable.Context(target, image, program, flow)
    header = readable.header_text(context, image.name)
    defined = re.findall(r"^struct (\w+)\n\{", header, re.M)
    assert len(defined) == len(set(defined)) >= 2 and "s_1" in defined, header
    compiled, shown = tmp_path / "compiled", tmp_path / "printed"
    compiled.mkdir()
    shown.mkdir()
    (shown / readable.HEADER).write_text(header)
    included = []
    for name in names:
        try:
            text = lifter.lift(program.by_name[name]).text
        except lift.Refused:
            continue
        c, out = readable.transform(name, text, context=context), readable.printed(name, text, context)
        if c is None:
            continue
        assert not re.search(r"^struct \w+\n\{", out, re.M) and not re.search(r"^struct \w+;$", out, re.M), out
        if re.search(r"^struct \w+\n\{", c, re.M):
            included.append(name)
            assert '#include "%s"' % readable.HEADER in out, out
        (compiled / (name + ".c")).write_text(c)
        (shown / (name + ".c")).write_text(out)
    assert {"ring_put", "ring_get", "timer_irq"} <= set(included), included
    ring = readable.printed("ring_put", lifter.lift(program.by_name["ring_put"]).text, context)
    assert "/* struct s_1: u32 f_20 at 0x20, u32 f_24 at 0x24 */" in ring, ring
    irq = readable.printed("timer_irq", lifter.lift(program.by_name["timer_irq"]).text, context)
    assert "#define g_%x (*(volatile struct g_%x_t *)0x%xu)" % ((min(p.statics),) * 3) in irq, irq
    assert "extern void ring_put(" in irq, irq        # (nobody reads its result: dread.tidy void_results)
    main = readable.printed("main", lifter.lift(program.by_name["main"]).text, context)
    assert re.search(r"^    volatile u8 stack\d+\[12\];$", main, re.M), main     # (its frame's buffer: its own)
    flags = target.compiler.generated_cflags()
    a = build.compile_all(target, image, str(compiled), str(tmp_path / "obj-a"), quiet=True, flags=flags)
    b = build.compile_all(target, image, str(shown), str(tmp_path / "obj-b"), quiet=True, flags=flags)

    def code(obj):
        e = Elf(obj)
        return {s.name: (e.data(s), [(o, t, e.symbols[k].name, x) for o, t, k, x in e.relocations(i)])
                for i, s in enumerate(e.sections) if s.flags & 4 and s.size}
    assert len(a) == len(b) > 30 and all(code(x) == code(y) for x, y in zip(sorted(a), sorted(b)))
    out = tmp_path / "out"
    assert readable.main(["--target", target.root, "--out", str(out), "ring_put", "print_status"]) == 0
    assert (out / readable.HEADER).read_text() == header
    states = readable.gate_states(target, image)    # (the file's first line says where the check stands)
    assert (out / "ring_put.c").read_text() == readable.marked(ring, readable.gate_note("ring_put", states))
    capsys.readouterr()
    assert readable.main(["--target", target.root, "--header"]) == 0
    assert capsys.readouterr().out == header
    assert readable.main(["--target", target.root, "--plain", "ring_put"]) == 0
    plain = capsys.readouterr().out
    assert "/* struct s_1: u32 f_20 at 0x20, u32 f_24 at 0x24 */" in plain and ": ldr" not in plain, plain


# ---- a wrapped function's body made readable (2026-10-25)

BODY_PREAMBLE = ("    u32 r0 = a0, r1 = a1, r2 = a2, r3 = a3, r4 = 0, r5 = 0, r6 = 0, r7 = 0, r8 = 0, r9 = 0, r10 = 0, r11 = 0, "
            "r12 = 0, lr = (u32)__builtin_return_address(0);\n    u32 n = 0, z = 0, c = 0, v = 0;\n"
            "    (void)n; (void)z; (void)c; (void)v; (void)r12; (void)lr; (void)sp0;\n")
BODY_ENTRY = ('__attribute__((naked)) void h(void)\n{\n    __asm__ volatile ("mov r12, sp\\n\\tsub sp, sp, #8\\n\\t'
         'push {r12, lr}\\n\\tbl h_dread_body\\n\\tpop {r12, lr}\\n\\tadd sp, sp, #8\\n\\tbx lr");\n}\n')
BODY_WRAPPED = ("/* h at 0x100, lifted by dread lift: one statement per instruction. */\n#include \"dread.h\"\n"
           "extern u32 g(u32, u32);\nu32 h_dread_body(u32 a0, u32 a1, u32 a2, u32 a3, u32 sp0);\n" + BODY_ENTRY +
           "__attribute__((noinline, used)) u32 h_dread_body(u32 a0, u32 a1, u32 a2, u32 a3, u32 sp0)\n{\n" + BODY_PREAMBLE +
           "    r12 = (u32)*(volatile u32 *)(sp0 + 0);  /* 100: ldr r12, [sp] */\n"
           "    { u32 x = r0, y = 0x0u; u64 t = (u64)x + (u32)~y + 1; u32 r = (u32)t; n = r >> 31; z = r == 0; "
           "c = (u32)(t >> 32); v = ((x ^ r) & (~y ^ r)) >> 31; }  /* 104: cmp r0, #0 */\n"
           "    if (z) goto L_110;  /* 108: beq #0x110 */\n"
           "    r0 = g(r12, r1); r1 = r2 = r3 = r12 = 0;  /* 10c: bl g */\n"
           "    return r0;  /* 10e: bx lr */\n"
           "L_110: ;\n"
           "    r0 = ~0x0u;  /* 110: mvn r0, #0 */\n"
           "    return r0;  /* 114: bx lr */\n}\n")


def test_a_wrapped_function_s_body_is_made_readable():
    """A wrapped function (dread.lift: a naked entry that reserves the frame and calls NAME_dread_body
    with the real stack's address, as for FreeRTOS's printf): the stages run on its body, which carries
    the function's name meanwhile (readable.picked), not on the entry; the entry, the body's declaration
    and its head stay as the lifter wrote them (the file links as it did), the body's flags and goto are
    gone, its stack argument read where it lies. Until 2026-10-25 both were printed as lifted."""
    out = readable.transform("h", BODY_WRAPPED, context=readable.Context())
    assert BODY_ENTRY in out, out
    assert "u32 h_dread_body(u32 a0, u32 a1, u32 a2, u32 a3, u32 sp0);" in out, out
    assert "__attribute__((noinline, used)) u32 h_dread_body(u32 a0, u32 a1, u32 a2, u32 a3, u32 sp0)" in out, out
    body = out[out.index("__attribute__((noinline, used))"):]
    assert "goto" not in body and "n = r >> 31" not in body and "u64" not in body, body
    assert re.search(r"if \(\w+ == 0\)", body) or re.search(r"if \(\w+ != 0\)", body), body
    assert "*(volatile u32 *)sp0" in body and "g(" in body, body
    tree = cparse.parse(BODY_WRAPPED)
    fn = readable.picked(tree, "h")
    assert fn.head.startswith("__attribute__((noinline, used)) u32 h_dread_body(") and fn.name == "h"
    assert tree.function("h") is fn and tree.function("h" + readable.ENTRY).head.startswith("__attribute__((naked))")
    plain = cparse.parse(BODY_WRAPPED.replace("bl h_dread_body", "bl other_body"))      # (an entry that calls
    assert readable.picked(plain, "h").head.startswith("__attribute__((naked))")    # something else: itself)


def test_a_wrapped_body_keeps_its_parameters_as_declared():
    """A wrapped function's body (its head starts with its attribute: dread.tidy fixed_head) keeps its
    parameters as its head and the file's declaration have them: dread.vartypes does not type one in the
    statements alone (the "more results" body's `*(volatile u32 *)more = r3;` stays; typed, it read `*more
    = r3` with more declared u32, which does not compile: found by the private real-life test's gate),
    dread.shapes does not make one a structure pointer (`more->f_0`), dread.naming renames none; its
    locals are typed and named as before."""
    from dread import naming, shapes, tidy, vartypes
    text = ('#include "dread.h"\nu32 h_dread_body(u32 a0, u32 a1, u32 a2, u32 a3, u32 more);\n'
            '__attribute__((noinline, used)) u32 h_dread_body(u32 a0, u32 a1, u32 a2, u32 a3, u32 more)\n{\n'
            '    u32 r3, p;\n    p = a1;\n    r3 = *(volatile u8 *)(p + 1);\n'
            '    *(volatile u32 *)more = r3;\n    return a0;\n}\n')
    tree = cparse.parse(text)
    fn = tree.function("h_dread_body")
    assert tidy.fixed_head(fn) and not tidy.fixed_head(cparse.parse("u32 f(u32 a0)\n{\n    return a0;\n}\n").function("f"))
    p = shapes.Plan()
    p.variables = {"h_dread_body": {"digest": None, "types": {"more": "volatile u32 *", "p": "const volatile u8 *"}}}
    p.typed = {"h_dread_body": {"more": "s_1"}}
    p.structs = {"s_1": [(0, "u32"), (4, "u32")]}
    shapes.apply_types(tree, fn, p)
    vartypes.apply(tree, fn, p)
    naming.rename(fn, {"more": "extra", "p": "src", "r3": "byte"})
    out = cparse.unparse(tree, notes=False)
    assert "*(volatile u32 *)more = byte;" in out and "more->" not in out and "*more" not in out, out
    assert "u32 a3, u32 more)\n{" in out and "u32 h_dread_body(u32 a0, u32 a1, u32 a2, u32 a3, u32 more);" in out, out
    assert "src[1]" in out or "*(src + 1)" in out or "src = (const volatile u8 *)a1" in out, out
