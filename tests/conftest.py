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


RISCV_CROSS, RISCV_MARCH = _riscv_gcc()
needs_riscv_gcc = pytest.mark.skipif(RISCV_CROSS is None, reason="needs a RISC-V GCC (riscv64-elf-gcc or "
                                     "riscv64-unknown-elf-gcc)")


def riscv_toml(text):
    """A target's text for the RISC-V GCC here: its cross prefix and the -march it takes for the CSR
    instructions in place of riscv64-elf- and rv32imac_zicsr."""
    return text.replace('cross = "riscv64-elf-"', 'cross = "%s"' % RISCV_CROSS).replace(
        '"-march=rv32imac_zicsr"', '"-march=%s"' % RISCV_MARCH)
