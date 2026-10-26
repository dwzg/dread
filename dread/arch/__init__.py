"""Instruction-set layers: what the tools need to know about an architecture.

`get(spec)` returns the layer for a target's [arch] section: ARM (ARM state and Thumb, A and M
profiles, `dread.arch.arm`), RISC-V (RV32IMC, `dread.arch.riscv`), MSP430 (`dread.arch.msp430`,
under way), MIPS (MIPS32r2 little endian, `dread.arch.mips`, under way) and x86 (32-bit i386,
`dread.arch.x86`, under way). Another architecture is a module whose class offers the same methods;
`dread.arch.arm.Arm` documents them.
"""


M32 = 0xFFFFFFFF


def code_address(arch, address):
    """A code address as the tools key code: without the bit that marks an instruction set in it where
    the layer has one (isa_bit: ARM's Thumb bit, bit 0), as it is elsewhere (x86's code lies at any
    byte; RISC-V's, MSP430's and MIPS's at even ones). A layer without isa_bit counts as having bit 0
    (the tools took it off everywhere until 2026-10-27, which turned x86's odd entries into the byte in
    front of them)."""
    bit = getattr(arch, "isa_bit", 1)
    return address & ~bit if bit else address


def low_bits(d):
    """The bits from bit 0 up to the highest bit of d: what bits d of a sum, a difference or a product
    depend on in each operand (a carry goes upwards only); 0 for 0. dread.regflow's bits (the layers'
    bit_demand)."""
    return (1 << d.bit_length()) - 1 if d else 0


def unshift(d, kind, k):
    """The bits of a 32-bit x that bits d of x shifted by k depend on: kind "lsl", "lsr", "asr" or
    "ror" (k of 32 and more: "lsl" and "lsr" leave nothing of x, "asr" copies of bit 31; "ror" by k
    modulo 32). dread.regflow's bits (the layers' bit_demand)."""
    d &= M32
    if kind == "lsl":
        return d >> k if k < 32 else 0
    if kind == "lsr":
        return (d << k) & M32 if k < 32 else 0
    if kind == "asr":
        k = min(k, 32)
        if not k:
            return d
        out = (d << k) & M32
        if d >> (32 - k):                   # (the result's top k bits are copies of bit 31)
            out |= 1 << 31
        return out
    if kind == "ror":
        k %= 32
        return ((d << k) | (d >> (32 - k))) & M32 if k else d
    raise ValueError("no shift %r" % (kind,))


def extend_bits(d, width, signed):
    """The bits of x that bits d of x's low `width` bits extended to 32 depend on (zero extension:
    those bits; sign extension: and the sign bit wherever a bit above is used)."""
    mask = (1 << width) - 1
    out = d & mask
    if signed and d & ~mask & M32:
        out |= 1 << (width - 1)
    return out


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
    if name == "x86":
        from dread.arch.x86 import X86
        return X86(**options)
    raise ValueError("unknown architecture %r (known: arm, riscv, msp430, mips, x86)" % (name,))
