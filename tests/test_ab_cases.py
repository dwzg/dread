"""bin/ab-cases: generated cases of two checkouts, timed and compared."""
import os
import subprocess
import sys

import pytest

from conftest import gizmo_dir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.slow
def test_one_checkout_against_itself_and_a_function_that_does_not_exist():
    """The same code on both sides makes the same cases; a side that makes none is a failure, never
    a comparison of nothing (two comparisons by hand once compared nothing, unnoticed)."""
    target = gizmo_dir("m3")
    payload = os.path.join(target, "original", "gizmo.bin")
    script = os.path.join(ROOT, "bin", "ab-cases")
    run = subprocess.run([sys.executable, script, "--target", target, "--payload", payload, ROOT, ROOT, "strlen"],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "strlen" in run.stdout and "identical" in run.stdout
    run = subprocess.run([sys.executable, script, "--target", target, "--payload", payload, ROOT, ROOT, "no_such_fn"],
                         capture_output=True, text=True)
    assert run.returncode == 1 and "FAILED" in run.stdout, run.stdout + run.stderr
