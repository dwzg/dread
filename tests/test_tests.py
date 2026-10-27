"""Checks on the test suite itself."""
import ast
import collections
import glob
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def test_no_test_module_assigns_a_name_twice():
    """A module-level name assigned twice in one test module leaves the first value to nothing: on
    2026-10-27 tests/test_x86.py got a second COUNTER_C (the time-stamp counter's program) below the
    first (the device counter of the volatile-order test), and that test then built the wrong program
    and failed for it; a test function defined twice is worse, since pytest runs only the second and
    says nothing. Every top-level assignment, def and class of tests/*.py names something once."""
    twice = []
    for path in sorted(glob.glob(os.path.join(HERE, "*.py"))):
        with open(path) as f:
            tree = ast.parse(f.read(), path)
        names = collections.Counter()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names[node.target.id] += 1
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names[node.name] += 1
        twice += ["%s: %s" % (os.path.basename(path), name) for name, n in sorted(names.items()) if n > 1]
    assert not twice, twice
