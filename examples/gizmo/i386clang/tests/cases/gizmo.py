"""gizmo-i386clang: the shared cases of examples/gizmo/shared_cases.py with this build's peripherals."""
import os

HW = {"uart": 0x10000000}
with open(os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared_cases.py")) as f:
    exec(compile(f.read(), f.name, "exec"))
