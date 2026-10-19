"""Fixtures shared by DREAD's tests: the gizmo targets.

A test that takes `variant` (or `target`, `program`) runs on every build: a32 and m3 (GCC), rv32
(clang). Marked with @pytest.mark.compiles, a test is skipped when its build's compiler is missing."""
import os
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIZMO = os.path.join(ROOT, "examples", "gizmo")
VARIANTS = ("a32", "m3", "rv32")
COMPILERS = {"a32": "arm-none-eabi-gcc", "m3": "arm-none-eabi-gcc", "rv32": "clang", "msp430": "msp430-elf-gcc"}


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
