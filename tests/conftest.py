"""Fixtures shared by DREAD's tests: the two gizmo targets."""
import os
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIZMO = os.path.join(ROOT, "examples", "gizmo")
VARIANTS = ("a32", "m3")


def gizmo_dir(variant):
    return os.path.join(GIZMO, variant)


@pytest.fixture(params=VARIANTS)
def variant(request):
    return request.param


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
