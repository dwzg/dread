"""Instruction-set layers: what the tools need to know about an architecture.

`get(spec)` returns the layer for a target's [arch] section: ARM (ARM state and Thumb, A and M
profiles, `dread.arch.arm`) and RISC-V (RV32IMC, `dread.arch.riscv`). Another architecture is a
module whose class offers the same methods; `dread.arch.arm.Arm` documents them.
"""


def get(spec):
    """The architecture layer for an [arch] table (a dict with at least "name")."""
    name = spec.get("name")
    options = {k: v for k, v in spec.items() if k != "name"}
    if name == "arm":
        from dread.arch.arm import Arm
        return Arm(**options)
    if name == "riscv":
        from dread.arch.riscv import RiscV
        return RiscV(**options)
    raise ValueError("unknown architecture %r (known: arm, riscv)" % (name,))
