"""Instruction-set layers: what the tools need to know about an architecture.

`get(spec)` returns the layer for a target's [arch] section: ARM (ARM state and Thumb, A and M
profiles, `dread.arch.arm`), RISC-V (RV32IMC, `dread.arch.riscv`), MSP430 (`dread.arch.msp430`,
under way) and MIPS (MIPS32r2 little endian, `dread.arch.mips`, under way). Another architecture is a module whose class offers the same methods;
`dread.arch.arm.Arm` documents them.
"""


def sum_value(a, b, add=True):
    """a + b, or a - b, of two register values of a layer's value_step ((value, indexed), indexed True
    for a constant plus something not known), either of them None when not known: a known value plus
    one not known is that value indexed (the readable C's constant + something); None when a is not
    known, or b is not known in a difference."""
    if a is None:
        return None
    if b is None:
        return (a[0], True) if add else None
    return ((a[0] + b[0] if add else a[0] - b[0]) & 0xFFFFFFFF, a[1] or b[1])


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
    if name == "msp430":
        from dread.arch.msp430 import Msp430
        return Msp430(**options)
    if name == "mips":
        from dread.arch.mips import Mips
        return Mips(**options)
    raise ValueError("unknown architecture %r (known: arm, riscv, msp430, mips)" % (name,))
