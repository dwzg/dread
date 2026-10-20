"""gizmo-rv32cpp: the shared cases of examples/gizmo/shared_cases.py with this build's peripherals."""
import os

HW = {"uart": 0x10000000}
with open(os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared_cases.py")) as f:
    exec(compile(f.read(), f.name, "exec"))

# The C++ part's functions rewritten in src/shapes.cpp (Tri::area, both instances of the template
# bound) and src/rect.c (Rect::area), with cases generated from the original
for name in ("_ZNK3Tri4areaEv", "_ZNK4Rect4areaEv", "_Z5boundIjET_S0_S0_S0_", "_Z5boundIiET_S0_S0_S0_"):
    auto(name)
