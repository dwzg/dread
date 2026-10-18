"""Fixtures shared by DREAD's tests: the gizmo targets.

A test that takes `variant` (or `target`, `program`) runs on the two ARM builds; marked with
@pytest.mark.rv32 it runs on the RV32 build as well. The marker goes once every test runs there."""
import os
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIZMO = os.path.join(ROOT, "examples", "gizmo")
VARIANTS = ("a32", "m3")
RV32 = ("rv32",)


def gizmo_dir(variant):
    return os.path.join(GIZMO, variant)


def pytest_generate_tests(metafunc):
    if "variant" in metafunc.fixturenames:
        ready = metafunc.definition.get_closest_marker("rv32") is not None
        metafunc.parametrize("variant", VARIANTS + (RV32 if ready else ()))


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
