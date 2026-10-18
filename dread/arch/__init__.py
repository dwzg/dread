"""Instruction-set layers: what the tools need to know about an architecture.

`get(spec)` returns the layer for a target's [arch] section. Only ARM exists so far (ARM state and
Thumb, A and M profiles, `dread.arch.arm`). Another architecture is a module whose class offers the
same methods; `dread.arch.arm.Arm` documents them.
"""


def get(spec):
    """The architecture layer for an [arch] table (a dict with at least "name")."""
    name = spec.get("name")
    options = {k: v for k, v in spec.items() if k != "name"}
    if name == "arm":
        from dread.arch.arm import Arm
        return Arm(**options)
    raise ValueError("unknown architecture %r (known: arm)" % (name,))
