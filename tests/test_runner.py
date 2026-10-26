"""The overlay and the differential runner on gizmo: the rewritten functions agree with the original
on both builds, and the runner catches a planted bug, missing coverage and a wrong unreachable()."""
import os
import shutil

import pytest

from dread import build, runner
from conftest import GIZMO, NOIPA_DEF

pytestmark = pytest.mark.compiles


def run_all(target, payload, cases_dir=None, names=None):
    image = target.image()
    if cases_dir:
        image.cases = str(cases_dir)
    suite = runner.prepare(target, image, payload)
    lines = []
    names = names or list(suite.cases)
    failed, uncovered, results = runner.run(names, out=lines.append, jobs=2)
    return failed, uncovered, "\n".join(lines)


def src_copy(tmp_path, variant, replace=None):
    """The target's src with rewritten.c inlined from examples/gizmo/rewritten, optionally edited."""
    src = tmp_path / "src"
    src.mkdir(parents=True)
    shutil.copy(os.path.join(GIZMO, variant, "src", "addresses.h"), src)
    with open(os.path.join(GIZMO, "rewritten", "rewritten.c")) as f:
        text = f.read()
    if replace:
        assert replace[0] in text
        text = text.replace(*replace)
    (src / "rewritten.c").write_text('#include "addresses.h"\n' + text)
    return src


def test_rewritten_functions_agree(target, variant, tmp_path):
    payload = build.build(target, target.image(), src=str(src_copy(tmp_path, variant)), out=str(tmp_path / "recon"),
                          quiet=True)
    failed, uncovered, text = run_all(target, payload)
    assert failed == 0 and not uncovered, text
    assert "ok   checksum" in text and "ok   dispatch" in text


def test_planted_bug_is_caught(target, variant, tmp_path):
    """An unsigned comparison where the original compares signed: -1 is clamped to 9, not 0.
    (x >= hi ? hi would not do: it compiles to the same code. x <= lo ? lo differs only for
    x == lo > hi, an empty range, which the written cases do not try; dread mutate --kill found it.)"""
    src = src_copy(tmp_path, variant, ("return x < lo ? lo : x > hi ? hi : x;",
                                       "return (u32)x < (u32)lo ? lo : (u32)x > (u32)hi ? hi : x;"))
    payload = build.build(target, target.image(), src=str(src), out=str(tmp_path / "recon"), quiet=True)
    failed, uncovered, text = run_all(target, payload, names=["clamp", "mode_name"])
    assert "FAIL clamp [-1 in [0, 9]]" in text and "ok   mode_name" in text


def test_missing_coverage_and_wrong_unreachable(target, variant, tmp_path):
    payload = build.build(target, target.image(), src=str(src_copy(tmp_path, variant)), out=str(tmp_path / "recon"),
                          quiet=True)
    cases = tmp_path / "cases"
    cases.mkdir()
    # clamp would not do: it compiles to conditional instructions without branches, and coverage
    # counts a conditional instruction as run whether its condition held or not
    (cases / "few.py").write_text(
        'def text(m):\n'
        '    m.write(SCRATCH, b"7\\0")\n'
        'case("parse_uint", "decimal only", text, SCRATCH, SCRATCH + 0x100)\n'
        'fn = program.by_name["mode_name"]\n'
        'case("mode_name", "mode 1", lambda m: None, 1)\n'
        'unreachable("mode_name", "a wrong claim, for the test", fn.insns[0])\n')
    failed, uncovered, text = run_all(target, payload, cases_dir=cases)
    assert "UNCOVERED parse_uint" in text and "parse_uint" in uncovered
    assert "FAIL mode_name [unreachable]" in text


def test_a_function_whose_case_generation_fails_is_reported_not_fatal(target, variant, tmp_path, monkeypatch):
    """The generator failing for a function with no written cases: FAIL for it, and the run goes on to
    the summary file (its missed list was False once, and writing the summary crashed: found on
    FreeRTOS by dread bench)."""
    payload = build.build(target, target.image(), src=str(src_copy(tmp_path, variant)), out=str(tmp_path / "recon"),
                          quiet=True)
    suite = runner.prepare(target, target.image(), payload)
    name = next(n for n in suite.auto if not suite.cases.get(n))

    def broken(*args, **kw):
        raise RuntimeError("the generator gave up")
    monkeypatch.setattr(runner, "generated_cases", broken)
    lines = []
    failed, uncovered, _ = runner.run([name], out=lines.append, jobs=1)
    assert failed == 1 and "FAIL %s [case generation]" % name in "\n".join(lines)
    assert runner.RUN["missed"][name] == []


def stack_seed_target(tmp_path, cases, extra=""):
    """A target whose seed_from_stack seeds a random number with the address of a local (FreeRTOS's
    queue set test does so), with C that is the same but holds its local elsewhere in the frame, and
    plain beside it; cases: the text of a case file; extra: C the rebuilt seed_from_stack does besides
    (a difference the stack does not explain). Returns (target, image, rebuilt payload)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    if not shutil.which("arm-none-eabi-gcc"):
        pytest.skip("needs arm-none-eabi-gcc")
    seed = "seed = (unsigned)&local * 1103515245u + 12345u;"
    (tmp_path / "t.c").write_text(
        "unsigned seed, other;\n"
        "__attribute__((noinline)) void touch(volatile unsigned *p) { *p += 1; }\n"
        "void seed_from_stack(void) { volatile unsigned local = 0; touch(&local); %s }\n"
        "void plain(unsigned x) { other = x + 1; }\n" % seed)
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "plain", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mcpu=cortex-m3", "-mthumb", "-O2", "-ffunction-sections"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    target = Target.load(str(tmp_path))
    image = target.image()
    src = tmp_path / "src"
    src.mkdir()
    data = {name: address for kind, address, name in Program(target, image).symbols() if kind == "d"}
    (src / "rewritten.c").write_text(       # the same, with its local elsewhere in the frame
        "#define seed (*(volatile unsigned *)0x%x)\n#define other (*(volatile unsigned *)0x%x)\n"
        "void touch(volatile unsigned *p);\n" % (data["seed"], data["other"]) +
        "void seed_from_stack(void) { volatile unsigned pad[4], local = 0; pad[0] = 0; touch(&local); %s %s }\n"
        "void plain(unsigned x) { other = x + 1; }\n" % (seed, extra))
    os.makedirs(image.cases)
    with open(os.path.join(image.cases, "few.py"), "w") as f:
        f.write(cases)
    payload = build.build(target, image, src=str(src), out=str(tmp_path / "recon"), quiet=True)
    return target, image, payload


def test_a_case_whose_outcome_depends_on_where_the_stack_lies_is_refused(tmp_path):
    """C that lays out its frame otherwise seeds another random number. That is no disagreement:
    the original's own outcome changes when its stack starts lower, so the case is refused, not
    failed, and runs nothing for coverage; plain agrees as before."""
    target, image, payload = stack_seed_target(tmp_path, 'case("seed_from_stack", "once", lambda m: None)\n'
                                                         'case("plain", "x = 1", lambda m: None, 1)\n')
    failed, uncovered, text = run_all(target, payload)
    assert "REFUSED seed_from_stack [once]: the original's outcome depends on where the stack lies" in text, text
    assert "FAIL seed_from_stack" not in text and "seed_from_stack" in uncovered, text
    assert "ok   plain" in text, text


def stale_target(tmp_path, cases):
    """A target whose send copies a message with two members nobody set into a queue (FreeRTOS's
    timer commands do so), behind leave, which leaves data in the stack; start and start2 call them.
    The C of start leaves other data there (leave(x + 1): leave writes only its own frame), start2's
    sends 3 where the original sends 2; plain is plain. Returns (target, image, rebuilt payload)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    if not shutil.which("arm-none-eabi-gcc"):
        pytest.skip("needs arm-none-eabi-gcc")
    (tmp_path / "t.c").write_text(
        "struct msg { unsigned id, a, b, c; };\n"
        "unsigned queue[4], other; volatile unsigned done;\n"
        "__attribute__((noinline)) void put(const struct msg *m)\n"
        "{ queue[0] = m->id; queue[1] = m->a; queue[2] = m->b; queue[3] = m->c; }\n"
        "__attribute__((noinline)) void send(unsigned id, unsigned a) { struct msg m; m.id = id; m.a = a; put(&m); }\n"
        "__attribute__((noinline)) void leave(unsigned x)\n"
        "{ volatile unsigned junk[16]; int i; for (i = 0; i < 16; i++) junk[i] = x * 3 + i; }\n"
        "void start(unsigned x) { leave(x); send(1, x); done = 1; }\n"     # (no tail call: send's frame
        "void start2(unsigned x) { leave(x); send(2, x); done = 2; }\n"    # lies where leave's did)
        "void plain(unsigned x) { other = x + 1; }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-Wno-uninitialized", "-Wno-maybe-uninitialized", "-T", str(tmp_path / "t.ld"), "-e", "plain",
                    "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mcpu=cortex-m3", "-mthumb", "-O2", "-ffunction-sections"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    target = Target.load(str(tmp_path))
    image = target.image()
    src = tmp_path / "src"
    src.mkdir()
    data = {name: address for kind, address, name in Program(target, image).symbols() if kind == "d"}
    (src / "rewritten.c").write_text(
        "#define other (*(volatile unsigned *)0x%x)\n#define done (*(volatile unsigned *)0x%x)\n"
        % (data["other"], data["done"]) +
        "void leave(unsigned x); void send(unsigned id, unsigned a);\n"
        "void start(unsigned x) { leave(x + 1); send(1, x); done = 1; }\n"
        "void start2(unsigned x) { leave(x + 1); send(3, x); done = 2; }\n"
        "void plain(unsigned x) { other = x + 1; }\n")
    os.makedirs(image.cases)
    with open(os.path.join(image.cases, "few.py"), "w") as f:
        f.write(cases)
    payload = build.build(target, image, src=str(src), out=str(tmp_path / "recon"), quiet=True)
    return target, image, payload


def test_a_case_whose_differences_are_stale_stack_data_is_compared_except_those(tmp_path):
    """start's C leaves other data where send's unset members lie, so the queue's last two words
    differ; with the stack below the stack pointer filled at every function entry they change in
    the original too: outputs the original itself does not define. The case is compared except those
    (named in the report) and counts for coverage (decided 2026-10-20, docs/decisions.md; it was
    refused before). start2's C sends a 3 for a 2 besides: a difference the fills do not move, so it
    fails, the stale words named beside it."""
    target, image, payload = stale_target(tmp_path, 'case("start", "x = 5", lambda m: None, 5)\n'
                                                    'case("start2", "x = 5", lambda m: None, 5)\n'
                                                    'case("plain", "x = 1", lambda m: None, 1)\n')
    failed, uncovered, text = run_all(target, payload)
    assert "start [x = 5]: compared except what the original's stale stack data" in text, text
    assert "FAIL start " not in text and "REFUSED start " not in text and "start" not in uncovered, text
    assert "ok   start " in text, text
    assert "FAIL start2 [x = 5]" in text and "the original's stale stack data decides" in text, text
    assert "ok   plain" in text, text


def stale_branch_target(tmp_path, cases):
    """A target whose check reads a local that maybe writes only when asked (n), and branches on it;
    leave, called first, leaves data where check's frame lies. The C of start leaves other data there
    (leave(1): leave writes only its own frame), so check's n differs and so does the branch it takes;
    start2's C asks maybe to write n besides (check(1)): a difference of its own. Returns (target,
    image, rebuilt payload)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    if not shutil.which("arm-none-eabi-gcc"):
        pytest.skip("needs arm-none-eabi-gcc")
    (tmp_path / "t.c").write_text(
        "unsigned table[64], other; volatile unsigned done;\n"
        "__attribute__((noinline)) void maybe(unsigned *p, unsigned flag) { if (flag) *p = 1; }\n"
        "__attribute__((noinline)) void check(unsigned flag)\n"
        "{ unsigned n; maybe(&n, flag); if (n >= 64) other = 1; else other = table[n] + 2; }\n"
        "__attribute__((noinline)) void leave(unsigned x)\n"
        "{ volatile unsigned junk[16]; int i; for (i = 0; i < 16; i++) junk[i] = x * 3 + i; }\n"
        "void start(unsigned x) { leave(x); check(0); done = 1; }\n"
        "void start2(unsigned x) { leave(x); check(0); done = 2; }\n"
        "void plain(unsigned x) { other = x + 1; }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-Wno-uninitialized", "-Wno-maybe-uninitialized", "-T", str(tmp_path / "t.ld"), "-e", "plain",
                    "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mcpu=cortex-m3", "-mthumb", "-O2", "-ffunction-sections"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    target = Target.load(str(tmp_path))
    image = target.image()
    src = tmp_path / "src"
    src.mkdir()
    data = {name: address for kind, address, name in Program(target, image).symbols() if kind == "d"}
    (src / "rewritten.c").write_text(
        "#define other (*(volatile unsigned *)0x%x)\n#define done (*(volatile unsigned *)0x%x)\n"
        % (data["other"], data["done"]) +
        "void leave(unsigned x); void check(unsigned flag);\n"
        "void start(unsigned x) { leave(1); check(0); done = 1; }\n"
        "void start2(unsigned x) { leave(1); check(1); done = 2; }\n"
        "void plain(unsigned x) { other = x + 1; }\n")
    os.makedirs(image.cases)
    with open(os.path.join(image.cases, "few.py"), "w") as f:
        f.write(cases)
    payload = build.build(target, image, src=str(src), out=str(tmp_path / "recon"), quiet=True)
    return target, image, payload


def test_a_case_that_agrees_with_the_stack_filled_alike_agrees(tmp_path):
    """start's C leaves other data where check's n lies: the original reads 300 and more (x = 100) and
    stores 1, the C reads under 64 and stores table[n] + 2. The stale-data check fills the original
    alone, and its fills send it the way it went; with the stack below every function's entry filled
    alike in both images, both read the fill and store 1, whole: the case agrees (filled_alike).
    start2's C also has maybe write n, a difference the fills keep: it fails. plain agrees as before."""
    target, image, payload = stale_branch_target(tmp_path, 'case("start", "x = 100", lambda m: None, 100)\n'
                                                           'case("start2", "x = 100", lambda m: None, 100)\n'
                                                           'case("plain", "x = 1", lambda m: None, 1)\n')
    failed, uncovered, text = run_all(target, payload)
    assert "start [x = 100]: agrees with the stack below every function's entry filled alike" in text, text
    assert "ok   start " in text and "FAIL start " not in text and "start" not in uncovered, text
    assert "FAIL start2 [x = 100]" in text, text
    assert "ok   plain" in text, text


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_call_into_the_middle_of_a_function_is_seen(tmp_path):
    """Machine.watch_calls: a call through a register that lands inside a function but not at its
    entry is recorded with its site (the runner refuses such a case, the pilot such an input); the
    entry is not; the scratch area, where generated inputs' buffers lie, is unless a stub is there
    (the harness's pointer stub); another address outside every function only where anywhere is
    asked for."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.emu import Machine, Layout
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.c").write_text(r'''
__attribute__((noinline)) int twice(int x) { return x + x + 3; }
__attribute__((noinline)) int apply(int (*f)(int), int x) { if (!f) return -1; return f(x) * 5; }
__attribute__((naked, noreturn)) void reset(void) { for (;;) ; }
__attribute__((section(".vectors"), used)) void (*const vectors[2])(void) = {(void (*)(void))0x20005000, reset};
''')
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { KEEP(*(.vectors)) *(.text*) *(.rodata*) } > FLASH }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "reset", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '''
[image.main]
original = "original/t.bin"
base = 0x08000000
entry = "reset"

[compiler]
cross = "arm-none-eabi-"
''')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    image = t.image()
    program = Program(t, image)
    twice, apply = program.by_name["twice"], program.by_name["apply"]
    sites = program.icall_sites()
    assert sites and all(apply.entry <= a < program.end(apply) for a in sites)

    def watch(pointer, anywhere=False, stubbed=False):
        m = Machine(Layout(t, image), image.payload())
        if stubbed:
            m.stub(pointer & ~1, lambda m, *a: 0)
        m.watch_calls(program, sites, anywhere)
        try:
            m.call(apply.entry, pointer, 4, max_instructions=10_000)
        except Exception:
            pass
        return [target for _, target in m.wild_calls]
    assert watch(twice.insns[1] | 1) == [twice.insns[1]]
    assert watch(twice.entry | 1) == []
    stub = 0x20100001                       # (the scratch area, where a generated input's buffers lie)
    assert watch(stub) == [stub & ~1]       # a callback an input aimed at a buffer (generator 28)
    assert watch(stub, stubbed=True) == []  # a stub there (the harness's pointer stub) is fine
    assert watch(0x20000001) == [] and watch(0x20000001, anywhere=True) == [0x20000000]   # RAM: only anywhere


SMASH_C = NOIPA_DEF + """
volatile unsigned sink;
NOIPA void copy(char *dst, const char *src, unsigned n) { while (n--) *dst++ = *src++; }
unsigned user(const char *src, unsigned n, unsigned k) { char buf[4]; copy(buf, src, n); sink = k; return k * 5 + buf[0]; }
"""


def test_a_case_whose_original_touches_its_saved_registers_is_refused(tmp_path):
    """user keeps k in r4, which its prologue saves below its return address; copy, handed a length
    past user's 4-byte buffer (an input no caller would make: FreeRTOS's queues with an item size
    larger than the item), writes over that saved r4, and user's pop hands the smashed value back. The
    C keeps a 16-byte buffer, so its r4 comes back whole: the images differ, but the difference is
    the original's frame, which C cannot be held to. Refused, with what touched which register; a
    length within the buffer is compared and agrees."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    if not shutil.which("arm-none-eabi-gcc"):
        pytest.skip("needs arm-none-eabi-gcc")
    (tmp_path / "t.c").write_text(SMASH_C)
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "user", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mcpu=cortex-m3", "-mthumb", "-O2", "-ffunction-sections"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    target = Target.load(str(tmp_path))
    image = target.image()
    program = Program(target, image)
    # (the layout tested: user pushes r4 in its prologue; GCC 13 moves the arguments first)
    head = program.instructions(program.by_name["user"])[:4]
    assert any(i.mnemonic == "push" and "r4" in i.op_str for i in head), [(i.mnemonic, i.op_str) for i in head]
    data = {name: address for kind, address, name in program.symbols() if kind == "d"}
    src = tmp_path / "src"
    src.mkdir()
    (src / "rewritten.c").write_text(
        "#define sink (*(volatile unsigned *)0x%x)\n" % data["sink"] +
        "void copy(char *dst, const char *src, unsigned n);\n"
        "unsigned user(const char *src, unsigned n, unsigned k)\n"
        "{ char buf[16]; copy(buf, src, n); sink = k; return k * 5 + buf[0]; }\n")
    os.makedirs(image.cases)
    with open(os.path.join(image.cases, "few.py"), "w") as f:
        f.write('def text(m):\n    m.write(0x20100000, b"ABCDEFGHIJKLMNOP")\n'
                'case("user", "n = 2", text, 0x20100000, 2, 9)\n'
                'case("user", "n = 8", text, 0x20100000, 8, 9)\n')
    payload = build.build(target, image, src=str(src), out=str(tmp_path / "recon"), quiet=True)
    failed, uncovered, text = run_all(target, payload)
    assert "REFUSED user [n = 8]: the input makes the original touch its own saved registers" in text, text
    assert "write of the saved r4" in text and "by copy" in text, text
    assert "FAIL user" not in text and "ok   user" in text, text


REUSE_C = NOIPA_DEF + """
volatile unsigned sink;
NOIPA unsigned helper(unsigned a) { return a * 3 + sink; }
NOIPA unsigned pusher(unsigned a) { unsigned b = helper(a); return helper(b) + a; }
NOIPA unsigned local_user(unsigned a)
{ volatile unsigned buf[4]; buf[0] = a; buf[1] = a + 1; buf[2] = a + 2; buf[3] = a + 3;
  return buf[0] + buf[1] + buf[2] + buf[3]; }
unsigned outer(unsigned a) { return local_user(pusher(a)) + 1; }
"""


def test_a_frame_that_returned_leaves_no_saved_registers_behind(tmp_path):
    """pusher saves r4 and lr and returns; local_user, called next from the same frame, lays its
    locals where pusher's saves were and stores to them. Those slots are free again once sp rose above
    them: a case of outer whose images differ (the C adds 2, not 1) is a FAIL, compared, not refused
    as one whose input made code write a saved register (the check kept the slots of frames that had
    returned until an access below sp: FreeRTOS's xQueueIsQueueFullFromISR's local, where
    prvQueueAccessLogError had pushed r7, counted as a write of a saved r7)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    if not shutil.which("arm-none-eabi-gcc"):
        pytest.skip("needs arm-none-eabi-gcc")
    (tmp_path / "t.c").write_text(REUSE_C)
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "outer", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mcpu=cortex-m3", "-mthumb", "-O2", "-ffunction-sections"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    target = Target.load(str(tmp_path))
    image = target.image()
    program = Program(target, image)
    # (the layout tested: pusher saves r4, local_user's locals lie on the stack without a save of their own)
    ops = lambda name: [(i.mnemonic, i.op_str) for i in program.instructions(program.by_name[name])]
    assert any(m == "push" and "r4" in o for m, o in ops("pusher")), ops("pusher")
    assert not any(m == "push" for m, o in ops("local_user")) and \
        any(m.startswith("sub") and "sp" in o for m, o in ops("local_user")), ops("local_user")
    src = tmp_path / "src"
    src.mkdir()
    (src / "rewritten.c").write_text(
        "unsigned pusher(unsigned a);\nunsigned local_user(unsigned a);\n"
        "unsigned outer(unsigned a) { return local_user(pusher(a)) + 2; }\n")
    os.makedirs(image.cases)
    with open(os.path.join(image.cases, "few.py"), "w") as f:
        f.write('def none(m):\n    pass\n'
                'case("outer", "a = 5", none, 5)\n')
    payload = build.build(target, image, src=str(src), out=str(tmp_path / "recon"), quiet=True)
    failed, uncovered, text = run_all(target, payload)
    assert "FAIL outer [a = 5]" in text, text
    assert "REFUSED outer" not in text, text


def small_target(tmp_path, source, rewritten, cases, entry, data=()):
    """A target of our own: source (C, NOIPA_DEF in front) built by arm-none-eabi-gcc -O2 for a
    Cortex-M3, the rebuilt image from rewritten (C; each name in data is defined as the unsigned at
    the original's address of that global), cases a case file's text. Returns (target, image,
    payload, program)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    if not shutil.which("arm-none-eabi-gcc"):
        pytest.skip("needs arm-none-eabi-gcc")
    (tmp_path / "t.c").write_text(NOIPA_DEF + source)
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-Wno-uninitialized", "-Wno-maybe-uninitialized", "-T", str(tmp_path / "t.ld"), "-e", entry,
                    "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mcpu=cortex-m3", "-mthumb", "-O2", "-ffunction-sections"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    target = Target.load(str(tmp_path))
    image = target.image()
    program = Program(target, image)
    symbols = {name: address for kind, address, name in program.symbols() if kind == "d"}
    src = tmp_path / "src"
    src.mkdir()
    defines = "".join("#define %s (*(volatile unsigned *)0x%x)\n" % (n, symbols[n]) for n in data)
    (src / "rewritten.c").write_text(defines + rewritten)
    os.makedirs(image.cases)
    with open(os.path.join(image.cases, "few.py"), "w") as f:
        f.write(cases)
    payload = build.build(target, image, src=str(src), out=str(tmp_path / "recon"), quiet=True)
    return target, image, payload, program


TOUCH_BEFORE_C = """
volatile unsigned sink, mark;
NOIPA void copy(char *dst, const char *src, unsigned n) { while (n--) *dst++ = *src++; }
NOIPA void report(unsigned n) { sink = n; }
unsigned user(const char *src, unsigned n, unsigned k)
{ char buf[4]; report(n); copy(buf, src, n); sink = k; return k * 5 + buf[0]; }
unsigned marker(const char *src, unsigned n, unsigned k)
{ char buf[4]; mark = n; copy(buf, src, n); sink = k; return k * 5 + buf[0]; }
unsigned later(const char *src, unsigned n, unsigned k)
{ char buf[4]; report(n); copy(buf, src, n); report(k); sink = k; return k * 5 + buf[0]; }
unsigned shortcut(const char *src, unsigned n, unsigned k)
{ char buf[4]; copy(buf, src, n); sink = k; return k * 5 + buf[0]; }
"""

TOUCH_BEFORE_CASES = """
def text(m):
    m.write(0x20100000, b"ABCDEFGHIJKLMNOP")
    logging_stubs(m, ("report", 0, 1))
for f, n in (("user", 12), ("marker", 12), ("later", 12), ("shortcut", 8)):     # (as far as the saved r4, not lr)
    case(f, "n = 2", text, 0x20100000, 2, 9)
    case(f, "n = %d" % n, text, 0x20100000, n, 9)
"""


@pytest.mark.parametrize("wrong", [False, True])
def test_a_saved_register_touched_explains_only_what_comes_after_it(tmp_path, wrong):
    """user hands n to a stubbed report, then copy writes past its 4-byte buffer and the padding behind
    it over the saved r4 for n = 12 (as in the test above); marker stores n into a global first. C with
    a 16-byte buffer gets its r4 back whole: for n = 12 the images differ in r4 after the return only,
    which the touch explains, and the case is refused. C that passes n & 3 and stores n & 3 (wrong)
    differs for n = 12 in the stub's argument and in the global as well, both made before the touch:
    the touch explains neither, and the case fails with a line that says so (until 2026-10-24 it was
    refused, and the function, its n = 2 case agreeing and running every instruction, accepted).
    later reports k after the copy too, and its C leaves that out for n > 8: a difference after the
    touch (the log from its second entry, one move out and back fewer), which the touch explains.
    shortcut (its frame smaller: n = 8 reaches its saved r4) has wrong C that returns for n > 4 before it
    calls copy (the same value, but sink not stored): it never gets to the touch, so the touch explains
    none of its differences, not even those that come after the touch in the original (sink, r4)."""
    value = "n & 3" if wrong else "n"
    rewritten = ("void copy(char *dst, const char *src, unsigned n);\nvoid report(unsigned n);\n"
                 "unsigned user(const char *src, unsigned n, unsigned k)\n"
                 "{ char buf[16]; report(%s); copy(buf, src, n); sink = k; return k * 5 + buf[0]; }\n"
                 "unsigned marker(const char *src, unsigned n, unsigned k)\n"
                 "{ char buf[16]; mark = %s; copy(buf, src, n); sink = k; return k * 5 + buf[0]; }\n"
                 "unsigned later(const char *src, unsigned n, unsigned k)\n"
                 "{ char buf[16]; report(n); copy(buf, src, n); if (n <= 8) report(k); sink = k;\n"
                 "  return k * 5 + buf[0]; }\n"
                 "unsigned shortcut(const char *src, unsigned n, unsigned k)\n"
                 "{ char buf[16]; %scopy(buf, src, n); sink = k; return k * 5 + buf[0]; }\n"
                 % (value, value, "if (n > 4) return k * 5 + 'A'; " if wrong else ""))
    target, image, payload, program = small_target(tmp_path, TOUCH_BEFORE_C, rewritten, TOUCH_BEFORE_CASES, "user",
                                                   data=("sink", "mark"))
    head = program.instructions(program.by_name["user"])[:4]
    assert any(i.mnemonic == "push" and "r4" in i.op_str for i in head), [(i.mnemonic, i.op_str) for i in head]
    failed, uncovered, text = run_all(target, payload)

    def line(start):                        # (which saved registers the copy reaches depends on the compiler)
        return next((x for x in text.splitlines() if x.startswith(start)), "")
    refused = line("REFUSED later [n = 12]: the input makes the original touch its own saved registers")
    assert "it differs only after the touch: r4 after the return; " in refused and refused.endswith(
        "the log from entry 2)"), text
    assert "ok   later" in text, text
    if not wrong:
        for f in ("user [n = 12]", "marker [n = 12]", "shortcut [n = 8]"):
            refused = line("REFUSED %s: the input makes the original touch its own saved registers" % f)
            assert "it differs only after the touch: r4 after the return" in refused, text
            assert "ok   %s" % f.split()[0] in text, text
        return
    assert "FAIL user [n = 12]: refused for the saved registers the input makes the original touch (write of the " \
           "saved r4" in text and "but it differs before the touch: the log from entry 1" in text, text
    mark = next(address for kind, address, name in program.symbols() if kind == "d" and name == "mark")
    assert "FAIL marker [n = 12]: refused for the saved registers" in text and \
           "but it differs before the touch: memory 0x%x" % mark in text, text
    sink = next(address for kind, address, name in program.symbols() if kind == "d" and name == "sink")
    failing = line("FAIL shortcut [n = 8]: refused for the saved registers")
    assert "but it differs before the touch: memory 0x%x-0x%x; r4 after the return" % (sink, sink + 3) in failing \
        and failing.endswith("(the rebuilt image's run never gets that far: 0 moves out of the function and back "
                             "and log entries, where the original makes 1 before it)"), text
    for f in ("user", "marker", "shortcut"):
        assert "REFUSED %s" % f not in text and "ok   %s" % f not in text, text


STACK_BEFORE_C = """
volatile unsigned sink;
NOIPA void touch(volatile unsigned *p) { *p += 1; }
NOIPA void report(unsigned x) { sink = x; }
unsigned scaled(unsigned x, unsigned y)
{ volatile unsigned local = 0; touch(&local); report(x); return y ? ((unsigned)&local * 3u) >> y : x; }
"""


@pytest.mark.parametrize("wrong", [False, True])
def test_where_the_stack_lies_explains_only_what_it_moves(tmp_path, wrong):
    """scaled returns a number made from the address of a local for y = 2 (for y = 31 that number is 0
    wherever the stack lies, and y = 0 returns x): C that holds its local elsewhere in the frame returns
    another, and the original's own return value changes with its stack lower, so the case is refused.
    C that passes x & 3 to the stubbed report besides (wrong) differs for x = 8 in the log too, which
    the lower stack does not move: the case fails, the refusal named (until 2026-10-24 it was refused,
    and the function, its other cases agreeing and running every instruction, accepted)."""
    rewritten = ("void touch(volatile unsigned *p);\nvoid report(unsigned x);\n"
                 "unsigned scaled(unsigned x, unsigned y)\n"
                 "{ volatile unsigned pad[4], local = 0; pad[0] = 0; touch(&local); report(%s);\n"
                 "  return y ? ((unsigned)&local * 3u) >> y : x; }\n" % ("x & 3" if wrong else "x"))
    cases = ('def stubbed(m):\n    logging_stubs(m, ("report", 0, 1))\n'
             'case("scaled", "x = 2, y = 0", stubbed, 2, 0)\n'
             'case("scaled", "x = 2, y = 31", stubbed, 2, 31)\n'
             'case("scaled", "x = 8, y = 2", stubbed, 8, 2)\n')
    target, image, payload, program = small_target(tmp_path, STACK_BEFORE_C, rewritten, cases, "scaled",
                                                   data=("sink",))
    failed, uncovered, text = run_all(target, payload)
    if not wrong:
        assert "REFUSED scaled [x = 8, y = 2]: the original's outcome depends on where the stack lies" in text, text
        assert "it differs only where the lower stack moves the original too: the return value" in text, text
        assert "ok   scaled" in text and not uncovered, text
        return
    assert "FAIL scaled [x = 8, y = 2]: refused for where the stack lies (with it lower the original changes the " \
           "return value), but it differs where the lower stack moves nothing: the log from entry 1" in text, text
    assert "REFUSED scaled" not in text and "ok   scaled" not in text, text


WILD_BEFORE_C = """
volatile unsigned sink;
NOIPA int twice(int x) { return x + x + 3; }
NOIPA void report(unsigned x) { sink = x; }
int apply(int (*f)(int), int x) { report(x); if (!f) return -1; return f(x) * 5; }
"""


def test_a_call_into_the_middle_of_a_function_explains_only_what_comes_after_it(tmp_path):
    """apply hands x to a stubbed report, then calls the code pointer it was given. C that passes x & 3
    agrees where f is twice or null (x = 2 and 3); with f the middle of twice and x = 8 the original
    calls into the middle of a function (refused before: the rebuilt image may have other code there),
    but the images differ before that call already, in report's argument: the case fails, the refusal
    named (until 2026-10-24 it was refused, and the function accepted)."""
    rewritten = ("void report(unsigned x);\n"
                 "int apply(int (*f)(int), int x) { report(x & 3); if (!f) return -1; return f(x) * 5; }\n")
    cases = ('def stubbed(m):\n    logging_stubs(m, ("report", 0, 1))\n'
             'twice = program.by_name["twice"]\n'
             'case("apply", "twice, x = 2", stubbed, twice.entry | 1, 2)\n'
             'case("apply", "null, x = 3", stubbed, 0, 3)\n'
             'case("apply", "into twice, x = 8", stubbed, twice.insns[1] | 1, 8)\n')
    target, image, payload, program = small_target(tmp_path, WILD_BEFORE_C, rewritten, cases, "apply", data=("sink",))
    failed, uncovered, text = run_all(target, payload)
    assert "FAIL apply [into twice, x = 8]: refused for the call of 0x%x from " % program.by_name["twice"].insns[1] \
           in text and "inside twice but not its entry (a code pointer the input gave), but it differs before that " \
           "call: the log from entry 1" in text, text
    assert "REFUSED apply" not in text and "ok   apply" not in text, text


STALE_BEFORE_C = """
struct msg { unsigned id, a, b; };
volatile unsigned done;
NOIPA void report(unsigned a, unsigned b) { done = a + b; }
NOIPA void put(const struct msg *m) { report(m->id, m->b); }
NOIPA void send(unsigned id, unsigned a) { struct msg m; m.id = id; m.a = a; put(&m); }
NOIPA void leave(unsigned x) { volatile unsigned junk[16]; int i; for (i = 0; i < 16; i++) junk[i] = x * 3 + i; }
void start(unsigned x) { leave(x); report(x, 1); send(x, x); done = 1; }
"""


@pytest.mark.parametrize("wrong", [False, True])
def test_stale_stack_data_leaves_out_only_the_log_entries_it_moves(tmp_path, wrong):
    """start reports x, then send has put report a member nobody set (stale stack data: what leave left).
    C that leaves other data there (leave(x + 1)) differs in the second report only, which the fills
    move: compared except the log from that entry. C that reports x & 3 first (wrong) differs for x = 8
    in the first entry too, which the fills do not move: the case fails (until 2026-10-24 the whole
    log was one output, left out when any entry moved, and the case was compared except it)."""
    rewritten = ("void leave(unsigned x); void send(unsigned id, unsigned a); void report(unsigned a, unsigned b);\n"
                 "void start(unsigned x) { leave(x + 1); report(%s, 1); send(x, x); done = 1; }\n"
                 % ("x & 3" if wrong else "x"))
    cases = ('def stubbed(m):\n    logging_stubs(m, ("report", 0, 2))\n'
             'case("start", "x = 2", stubbed, 2)\n'
             'case("start", "x = 8", stubbed, 8)\n')
    target, image, payload, program = small_target(tmp_path, STALE_BEFORE_C, rewritten, cases, "start",
                                                   data=("done",))
    failed, uncovered, text = run_all(target, payload)
    assert "start [x = 2]: compared except what the original's stale stack data (an uninitialised local) " \
           "decides: the log from entry 2" in text, text
    if not wrong:
        assert "start [x = 8]: compared except what the original's stale stack data" in text, text
        assert "ok   start" in text, text
        return
    assert "FAIL start [x = 8]" in text and "(of these, the original's stale stack data decides the log from " \
           "entry 1)" not in text, text
    assert "ok   start" not in text, text


CODE_BEFORE_C = """
volatile unsigned sink;
NOIPA void report(unsigned x) { sink = x; }
unsigned peek(const unsigned *p, unsigned x) { report(x); return *p + x; }
"""


@pytest.mark.parametrize("wrong", [False, True])
def test_an_instruction_loaded_as_data_explains_only_what_comes_after_it(tmp_path, wrong):
    """A written case aims peek's pointer at peek's own first instruction, which the rebuilt image holds
    otherwise (its C starts with a nop): refused as before (remove it), the images differing in what
    the load returns only. C that reports x & 3 (wrong) differs before the load, in the log: the case
    fails, the refusal named (until 2026-10-24 such a case was refused whatever differed; its rebuilt
    image was not even run)."""
    rewritten = ("void report(unsigned x);\n"
                 "unsigned peek(const unsigned *p, unsigned x) { __asm__ volatile (\"nop\"); report(%s); return *p + x; }\n"
                 % ("x & 3" if wrong else "x"))
    cases = ('def stubbed(m):\n    logging_stubs(m, ("report", 0, 1))\n'
             'case("peek", "x = 2", lambda m: (stubbed(m), m.write(SCRATCH, bytes(4))), SCRATCH, 2)\n'
             'case("peek", "written: own code, x = 8", stubbed, entry("peek"), 8)\n')
    target, image, payload, program = small_target(tmp_path, CODE_BEFORE_C, rewritten, cases, "peek", data=("sink",))
    failed, uncovered, text = run_all(target, payload)
    if not wrong:
        assert "REFUSED peek [written: own code, x = 8]: the input loads instructions as data or stores into the " \
               "image; remove it (it differs only after the first such load or store: the return value)" in text, text
        assert "ok   peek" in text, text
        return
    assert "FAIL peek [written: own code, x = 8]: refused for the instructions the input loads as data or the image " \
           "it stores into, but it differs before the first such load or store: the log from entry 1" in text, text
    assert "REFUSED peek" not in text, text


SHIFTED_C = """
volatile unsigned scaled3, bits;
NOIPA void touch(volatile unsigned *p) { *p += 1; }
void place(void)
{ volatile unsigned local = 0; touch(&local); scaled3 = (unsigned)&local * 3u; bits = ((unsigned)&local >> 3) & 7; }
"""


@pytest.mark.parametrize("more", [True, False])
def test_more_shifts_of_the_stack_find_what_one_leaves_alone(tmp_path, monkeypatch, more):
    """place stores two numbers made from the address of a local: the stack 64 bytes lower changes the
    first (so the reason holds) but not the second, whose bits a shift by a multiple of 64 keeps. C that
    holds the local elsewhere changes both. The further shifts (fragile.MORE_SHIFTS: 8 bytes changes
    those bits) find that the second moves too, and the case is refused; with the first shift alone
    the second is a difference it does not explain, and the case fails."""
    from dread import fragile
    if not more:
        monkeypatch.setattr(fragile, "MORE_SHIFTS", ())
    rewritten = ("void touch(volatile unsigned *p);\n"
                 "void place(void) { volatile unsigned pad[4], local = 0; pad[0] = 0; touch(&local);\n"
                 "  scaled3 = (unsigned)&local * 3u; bits = ((unsigned)&local >> 3) & 7; }\n")
    target, image, payload, program = small_target(tmp_path, SHIFTED_C, rewritten, 'case("place", "once", lambda m: '
                                                   'None)\n', "place", data=("scaled3", "bits"))
    failed, uncovered, text = run_all(target, payload)
    bits = next(address for kind, address, name in program.symbols() if kind == "d" and name == "bits")
    if more:
        assert "REFUSED place [once]: the original's outcome depends on where the stack lies" in text, text
        assert "0x%x" % bits in text.split("moves the original too: ")[1], text   # (bits differs: the C's local
                                                                                # lies elsewhere modulo 64)
    else:
        assert "FAIL place [once]: refused for where the stack lies" in text and \
            "but it differs where the lower stack moves nothing: memory 0x%x" % bits in text, text


def test_the_labels_of_the_failing_cases():
    """tests.tsv's failing column (runner.failed_labels): the labels of the FAIL lines of one function, with
    or without the refusal's words after them, each once and in order; not another function's (whose name
    starts the same), nor the summary line, [case generation] or [unreachable]."""
    lines = ["FAIL clamp [generated 3]\n     result: 0x1 vs 0x2", "FAIL clamp [kept 0]: refused for a stack address, "
             "but it differs in r0: 0x1 vs 0x2", "FAIL clamp_all [generated 1]", "FAIL clamp [generated 3]",
             "FAIL clamp [a case [of mine]]", "FAIL clamp [unreachable]: 2 instructions declared unreachable did run",
             "FAIL clamp [case generation]: no inputs", "FAIL clamp                      2/9 cases agree"]
    assert runner.failed_labels("clamp", lines) == ["generated 3", "kept 0", "a case [of mine]"]
    assert runner.failed_labels("clamp_all", lines) == ["generated 1"] and runner.failed_labels("other", lines) == []
