"""Fixtures shared by DREAD's tests: the gizmo targets.

A test that takes `variant` (or `target`, `program`) runs on every build: a32, m3 and m4 (GCC; m4 with
hard float and the DSP extension), rv32 (clang). Marked with @pytest.mark.compiles, a test is skipped when its build's compiler is missing."""
import os
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIZMO = os.path.join(ROOT, "examples", "gizmo")
VARIANTS = ("a32", "m3", "m4", "rv32")
COMPILERS = {"a32": "arm-none-eabi-gcc", "m3": "arm-none-eabi-gcc", "m4": "arm-none-eabi-gcc", "rv32": "clang",
             "msp430": "msp430-elf-gcc"}


def gizmo_dir(variant):
    return os.path.join(GIZMO, variant)


@pytest.fixture(autouse=True)
def _no_threads_left():
    """A test leaves no thread of its own running: DREAD's pools fork, and a thread left running (the
    GUI's analyses, its facts worker) may hold a lock the forked child then waits for forever (the
    suite hung on CI so after the GUI's tests, 2026-10-22). Threads get some time to end."""
    import threading
    import time
    before = set(threading.enumerate())
    yield
    left = [t for t in threading.enumerate() if t not in before and t.is_alive()]
    deadline = time.time() + 60
    for t in left:
        t.join(max(0.0, deadline - time.time()))
    left = [t.name for t in left if t.is_alive()]
    assert not left, "threads left running after the test (a later fork may deadlock): %s" % left


@pytest.fixture(autouse=True)
def _usage_ledger(tmp_path, monkeypatch):
    """Tests never write to the real usage ledger of language-model answers (dread.llm.USAGE)."""
    from dread import llm
    monkeypatch.setattr(llm, "USAGE", str(tmp_path / "llm-usage.tsv"))


@pytest.fixture(params=VARIANTS)
def variant(request):
    return request.param


@pytest.fixture(autouse=True)
def _build_compiler(request):
    if request.node.get_closest_marker("compiles") and "variant" in request.fixturenames:
        tool = COMPILERS[request.getfixturevalue("variant")]
        if not have(tool):
            pytest.skip("needs " + tool)


@pytest.fixture
def target(variant):
    from dread.target import Target
    return Target.load(gizmo_dir(variant))


@pytest.fixture
def program(target):
    from dread.program import Program
    return Program(target)


def have(tool):
    return shutil.which(tool) is not None


def _riscv_gcc():
    """The RISC-V GCC here, as (cross prefix, -march for RV32IMAC with the CSR instructions), or
    (None, None): Arch names it riscv64-elf-gcc, Debian and Ubuntu riscv64-unknown-elf-gcc (CI's);
    GCC 10 knows no _zicsr (its ISA spec has the CSR instructions in the base), GCC 12 and later need
    it for them."""
    import subprocess
    for cross in ("riscv64-elf-", "riscv64-unknown-elf-"):
        if have(cross + "gcc"):
            r = subprocess.run([cross + "gcc", "-march=rv32imac_zicsr", "-mabi=ilp32", "-E", "-x", "c", "-"],
                               input="", capture_output=True, text=True)
            return cross, "rv32imac_zicsr" if r.returncode == 0 else "rv32imac"
    return None, None


# What keeps a test's small function a call of its own whose body its callers know nothing of:
# noipa from GCC 8 on. GCC 7 and older ignore it and inline (Debian 10's GCC 7.3 did so in four
# tests: the calls they are about were gone), so there noinline and noclone; clang knows neither
# noipa nor noclone. A snippet that uses NOIPA starts with NOIPA_DEF.
NOIPA_DEF = ("#if defined(__clang__)\n#define NOIPA __attribute__((noinline))\n"
             "#elif __GNUC__ >= 8\n#define NOIPA __attribute__((noipa))\n"
             "#else\n#define NOIPA __attribute__((noinline, noclone))\n#endif\n")

RISCV_CROSS, RISCV_MARCH = _riscv_gcc()
needs_riscv_gcc = pytest.mark.skipif(RISCV_CROSS is None, reason="needs a RISC-V GCC (riscv64-elf-gcc or "
                                     "riscv64-unknown-elf-gcc)")


def riscv_toml(text):
    """A target's text for the RISC-V GCC here: its cross prefix and the -march it takes for the CSR
    instructions in place of riscv64-elf- and rv32imac_zicsr."""
    return text.replace('cross = "riscv64-elf-"', 'cross = "%s"' % RISCV_CROSS).replace(
        '"-march=rv32imac_zicsr"', '"-march=%s"' % RISCV_MARCH)
