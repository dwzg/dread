"""Fixtures shared by DREAD's tests: the gizmo targets.

A test that takes `variant` (or `target`, `program`) runs on the two ARM builds; marked with
@pytest.mark.rv32 it runs on the RV32 build as well. The marker goes once every test runs there.
Marked with @pytest.mark.compiles, a test is skipped when its build's compiler is missing (GCC for
the ARM builds, clang for rv32)."""
import os
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIZMO = os.path.join(ROOT, "examples", "gizmo")
VARIANTS = ("a32", "m3")
RV32 = ("rv32",)
COMPILERS = {"a32": "arm-none-eabi-gcc", "m3": "arm-none-eabi-gcc", "rv32": "clang"}


def gizmo_dir(variant):
    return os.path.join(GIZMO, variant)


def pytest_generate_tests(metafunc):
    if "variant" in metafunc.fixturenames:
        ready = metafunc.definition.get_closest_marker("rv32") is not None
        metafunc.parametrize("variant", VARIANTS + (RV32 if ready else ()))


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
