"""gizmo-arc: the shared cases of examples/gizmo/shared_cases.py with this build's peripherals."""
import os

HW = {"uart": 0x80000000}
with open(os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared_cases.py")) as f:
    exec(compile(f.read(), f.name, "exec"))

# clamp at the low end of an empty range: x == lo > hi tells x < lo from x <= lo, which ARC's GCC
# computes with min, a compare no branch shows the generator's compare-guided answers (dread mutate's
# survivor here; gizmo-a32's mutation test kills that survivor with its own search, so the case is
# this build's alone)
case("clamp", "7 in [7, 2]", memory(), 7, 7, 2)
