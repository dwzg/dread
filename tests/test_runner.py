"""The overlay and the differential runner on gizmo: the rewritten functions agree with the original
on both builds, and the runner catches a planted bug, missing coverage and a wrong unreachable()."""
import os
import shutil

import pytest

from dread import build, runner
from conftest import GIZMO

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


def test_a_case_whose_outcome_depends_on_where_the_stack_lies_is_refused(tmp_path):
    """seed_from_stack stores the address of a local into a global (FreeRTOS's queue set test seeds
    its random numbers so); C that lays out its frame otherwise stores another address. That is no
    disagreement: the original's own outcome changes when its stack starts lower, so the case is
    refused, not failed, and runs nothing for coverage; plain agrees as before."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.target import Target
    if not shutil.which("arm-none-eabi-gcc"):
        pytest.skip("needs arm-none-eabi-gcc")
    (tmp_path / "t.c").write_text(
        "unsigned seed, other;\n"
        "__attribute__((noinline)) void touch(volatile unsigned *p) { *p += 1; }\n"
        "void seed_from_stack(void) { volatile unsigned local = 0; touch(&local); seed = (unsigned)&local; }\n"
        "void plain(unsigned x) { other = x + 1; }\n")
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
    from dread.program import Program
    data = {name: address for kind, address, name in Program(target, image).symbols() if kind == "d"}
    (src / "rewritten.c").write_text(       # the same, with its local elsewhere in the frame
        "#define seed (*(volatile unsigned *)0x%x)\n#define other (*(volatile unsigned *)0x%x)\n"
        "void touch(volatile unsigned *p);\n" % (data["seed"], data["other"]) +
        "void seed_from_stack(void) { volatile unsigned pad[4], local = 0; pad[0] = 0; touch(&local);"
        " seed = (unsigned)&local; }\n"
        "void plain(unsigned x) { other = x + 1; }\n")
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "few.py").write_text('case("seed_from_stack", "once", lambda m: None)\n'
                                  'case("plain", "x = 1", lambda m: None, 1)\n')
    payload = build.build(target, image, src=str(src), out=str(tmp_path / "recon"), quiet=True)
    failed, uncovered, text = run_all(target, payload, cases_dir=cases)
    assert "REFUSED seed_from_stack [once]: the original's outcome depends on where the stack lies" in text, text
    assert "FAIL seed_from_stack" not in text and "seed_from_stack" in uncovered, text
    assert "ok   plain" in text, text
