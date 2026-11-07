"""The ARC layer (ARCompact, ARC600/ARC700; little-endian), under way.

Neither capstone nor Unicorn knows ARCompact, so this layer decodes the instructions itself (as the
MSP430's does) and will run them on an engine of DREAD's own. This first step is the decoding and
`flow` that `dread functions` needs: instruction lengths (16- or 32-bit, with a 32-bit long
immediate where an operand field is the limm code 0x3e) and the control flow (branches, the
register jumps j/jl, returns through blink).

What a 32-bit instruction's bits mean here (ARCompact PRM):
  major  = bits[31:27]   0x00-0x07 are 32-bit instructions, 0x08-0x1f are 16-bit ones
  the register b is split: b[2:0] = bits[26:24], b[5:3] = bits[14:12]
  P      = bits[23:22]   operand format of the general operations (0 reg, 1 u6, 2 s12, 3 cond)
  c      = bits[11:6]    a source register where P = 0 (the limm code 0x3e means a long immediate)
Addresses are kept as read; a code address has no instruction-set bit (isa_bit = 0), and a branch
is relative to the instruction's address with its low two bits cleared.

Not implemented yet: the operand text (`dread asm`), the calling convention, the engine, the
lifter. Those methods raise NotImplementedError until their step (docs/porting.md).
"""

import struct

# ---- registers ----
# r0-r28 general; r26 gp, r27 fp, r28 sp, r29 ilink1, r30 ilink2, r31 blink (the return address);
# r60 lp_count, r62 the long-immediate code, r63 pcl (the aligned pc). Names are rN with these
# aliases; the tools use the rN form.
_ALIAS = {26: "gp", 27: "fp", 28: "sp", 29: "ilink1", 30: "ilink2", 31: "blink",
          60: "lp_count", 62: "limm", 63: "pcl"}
NAMES = {i: "r%d" % i for i in range(64)}
NUMBER = {"r%d" % i: i for i in range(64)}
for _n, _a in _ALIAS.items():
    NUMBER[_a] = _n

LIMM = 0x3E                 # the operand code that means "a 32-bit long immediate follows"
BLINK = 31                  # a jump through blink is a return

# The single-operand general operations (major 0x04, sub-opcode bits[21:16]) that are jumps.
SUBOP_J = 0x20              # j / jcc / j.d  (jump; through blink it returns)
SUBOP_JL = 0x22             # jl / jlcc      (jump and link: a call)


class Insn:
    """One decoded instruction: address, size, bytes, mnemonic, op_str (empty until dread asm),
    and the raw instruction word (`word`, a 16- or 32-bit value) with its `major` opcode."""

    __slots__ = ("address", "size", "bytes", "mnemonic", "op_str", "word", "major", "__dict__")

    def __init__(self, address, code, size, word, major, mnemonic):
        self.address, self.size, self.bytes = address, size, bytes(code[:size])
        self.word, self.major, self.mnemonic, self.op_str = word, major, mnemonic, ""


def _b_reg(insn):
    """The 6-bit b register of a 32-bit instruction (b[5:3] = bits[14:12], b[2:0] = bits[26:24])."""
    return (((insn >> 12) & 7) << 3) | ((insn >> 24) & 7)


def _sign(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


class Arc:
    """The ARC layer. Constructed from [arch]: isa ("arc600"/"arc700"), endian, ghidra."""

    name = "arc"
    isa_bit = 0                 # no bit of a code address marks an instruction set
    max_insn = 8                # a 32-bit instruction with a 32-bit long immediate
    word = 4
    reg_bits = 32
    link_reg = "blink"          # jl/bl leave the return address in blink
    veneer_reg = "r12"
    default_args = 8
    default_result = ("r0", "r1")
    # Not confirmed against the ABI document; overridable by [abi]. ARCompact: r0-r12 caller-saved,
    # r13-r25 callee-saved, with gp/fp.
    default_callee_saved = tuple("r%d" % i for i in range(13, 26)) + ("fp",)

    def __init__(self, isa="arc600", endian="little", ghidra=None, cpu=None, extensions=None):
        if endian != "little":
            raise ValueError("only endian = \"little\" is supported")
        if isa not in ("arc600", "arc700", "arc"):
            raise ValueError("isa is arc600/arc700, not %r" % (isa,))
        self.isa, self.endian, self.cpu = isa, endian, cpu
        self.ghidra = ghidra or "ARC:LE:32:default"

    # ---- identity and registers ----

    def configure(self, cflags):
        pass

    def describe(self):
        return "arc (ARCompact, %s), 32-bit, little-endian" % self.isa

    def isas(self):
        return (self.isa,)

    def reg_name(self, name):
        name = name.lower()
        if name not in NUMBER:
            raise ValueError("unknown ARC register %r" % name)
        return NAMES[NUMBER[name]]

    def reg_id(self, name):
        return NUMBER[self.reg_name(name)]

    @staticmethod
    def _name(name):
        return NAMES[NUMBER[name]] if name in NUMBER else name

    def arg_regs(self, n):
        return ["r%d" % i for i in range(min(n, 8))]

    def general_regs(self):
        return ["r%d" % i for i in range(29)]

    # ---- decoding ----

    def min_insn(self, isa=None):
        return 2

    def _has_limm(self, insn, major):
        """Whether a 32-bit instruction carries a long immediate (a following 32-bit word). A limm
        is fetched for a source operand that is the code 0x3e, so a field that holds a destination
        (mov's and the single-operand group's b) does not count: nop is `mov` with b = 0x3e and no
        immediate. Not confirmed for every sub-opcode's operand roles; refined as a gizmo ARC build
        and differential disassembly turn up more (the next step)."""
        if major == 0x00:                       # B/Bcc: pc-relative, no operand register
            return False
        if major == 0x01:                       # BLcc (bit16=0) or BRcc (bit16=1)
            if (insn >> 16) & 1:                # BRcc compares b with c: a register or limm when
                return _b_reg(insn) == LIMM or (not ((insn >> 4) & 1)   # bit4 = 0, a u6 when bit4 = 1
                                                and ((insn >> 6) & 0x3F) == LIMM)
            return False                        # BLcc: pc-relative
        if major == 0x02:                       # LD: the base b may be a long immediate (a reg+reg
            return _b_reg(insn) == LIMM         # index that is a limm is a follow-up, with LD forms)
        if major == 0x03:                       # ST: the base b, or the stored value c (bits[11:6])
            return _b_reg(insn) == LIMM or ((insn >> 6) & 0x3F) == LIMM
        sub = (insn >> 16) & 0x3F               # general operations (0x04-0x07)
        b_is_source = sub < 0x20 and sub != 0x0A   # not mov, not the single-operand group (>= 0x20)
        if b_is_source and _b_reg(insn) == LIMM:
            return True
        p = (insn >> 22) & 3
        if p == 0:                              # register c (a source)
            return ((insn >> 6) & 0x3F) == LIMM
        if p == 3 and not ((insn >> 5) & 1):    # conditional with a register c (M bit clear)
            return ((insn >> 6) & 0x3F) == LIMM
        return False                            # u6 or s12: the source is in the instruction

    def insn_length(self, code, isa=None):
        """The length in bytes of the instruction at the start of `code`. A 16-bit h-register
        operation (major 0x0e: mov_s, cmp_s, add_s b,h) whose h is the long-immediate code 0x3e
        carries a 32-bit immediate, so it is 6 bytes."""
        hw0 = code[0] | (code[1] << 8)
        if (hw0 >> 11) >= 0x08:                 # a 16-bit instruction
            # major 0x0e sub-op bits[4:3]: 0 add_s, 1 mov_s b,h, 2 cmp_s (h a source) -- 3 is mov_s
            # h,b, where h is the destination, so its 0x3e is a discard, not a long immediate.
            if (hw0 >> 11) == 0x0E and ((hw0 >> 3) & 3) != 3 \
                    and (((hw0 & 7) << 3) | ((hw0 >> 5) & 7)) == LIMM:
                return 6
            return 2
        if len(code) < 4:
            return 4
        insn = (hw0 << 16) | (code[2] | (code[3] << 8))
        return 8 if self._has_limm(insn, hw0 >> 11) else 4

    def decode(self, code, address, isa=None):
        """The instruction at the start of `code`, or None where it does not fit."""
        code = bytes(code)
        if len(code) < 2:
            return None
        size = self.insn_length(code, isa)
        if size > len(code):
            return None
        hw0 = code[0] | (code[1] << 8)
        if (hw0 >> 11) >= 0x08:                 # a 16-bit instruction (2 bytes, or 6 with a limm)
            return Insn(address, code, size, hw0, hw0 >> 11, self._mnem16(hw0))
        insn = (hw0 << 16) | (code[2] | (code[3] << 8))
        major = hw0 >> 11
        return Insn(address, code, size, insn, major, self._mnem32(insn, major))

    def disassembler(self, isa=None):
        arch = self

        class _D:
            def disasm(self, code, address, count=0):
                code, pos, n = bytes(code), 0, 0
                while pos + 2 <= len(code):
                    insn = arch.decode(code[pos:pos + 8], address + pos)
                    if insn is None:
                        return
                    yield insn
                    pos += insn.size
                    n += 1
                    if count and n >= count:
                        return
        return _D()

    def pc_value(self, address, isa=None):
        return address

    # coarse mnemonics, enough to tell control flow apart; the full text waits for dread asm
    def _mnem16(self, hw0):
        return {0x0C: "add_s", 0x0E: "mov_s", 0x0F: "op_s", 0x10: "ld_s"}.get(hw0 >> 11, "s16")

    def _mnem32(self, insn, major):
        if major == 0x00:
            return "b"
        if major == 0x01:
            return "br" if (insn >> 16) & 1 else "bl"
        if major == 0x02:
            return "ld"
        if major == 0x03:
            return "st"
        sub = (insn >> 16) & 0x3F
        if sub == SUBOP_J:
            return "j"
        if sub == SUBOP_JL:
            return "jl"
        return "op"

    # ---- what an instruction does ----

    def flow(self, insn):
        """(kind, target, falls): next, jump, call, icall, return, ijump or stop. Branch targets
        are relative to the instruction's address with the low two bits cleared."""
        w, major = insn.word, insn.major
        base = (insn.address & ~3)
        if major == 0x00:                       # B / Bcc
            cond = not ((w >> 16) & 1)          # the conditional form falls through
            return ("jump", self._branch_target(w, base, aligned4=False), cond)
        if major == 0x01:
            if (w >> 16) & 1:                   # BRcc: compare and branch, always conditional
                return ("jump", None, True)
            return ("call", self._branch_target(w, base, aligned4=True), True)   # BLcc
        if major >= 0x08:                       # a 16-bit instruction
            return self._flow16(insn)
        if major == 0x04:
            sub = (w >> 16) & 0x3F
            if sub in (SUBOP_J, SUBOP_JL):
                p = (w >> 22) & 3
                cond = (p == 3)                 # a conditional jump falls through
                c = (w >> 6) & 0x3F
                if sub == SUBOP_JL:
                    return ("icall", None, True)
                if c == BLINK:
                    return ("return", None, cond)
                return ("ijump", None, cond)
        return ("next", None, True)

    def _branch_target(self, w, base, aligned4):
        """A B/BL target. BLcc's offset is 4-aligned (its low bits are two zero), Bcc's 2-aligned."""
        hi = (w >> 6) & 0x3FF                    # target[20:11]
        if aligned4:
            lo = (w >> 18) & 0x1FF               # target[10:2]
            if (w >> 16) & 1:                    # unconditional (s25)
                disp = ((w & 0xF) << 21) | (hi << 11) | (lo << 2)
                return (base + _sign(disp, 25)) & 0xFFFFFFFF
            return (base + _sign((hi << 11) | (lo << 2), 21)) & 0xFFFFFFFF
        lo = (w >> 17) & 0x3FF                   # target[10:1]
        if (w >> 16) & 1:                        # unconditional (s25)
            disp = ((w & 0xF) << 21) | (hi << 11) | (lo << 1)
            return (base + _sign(disp, 25)) & 0xFFFFFFFF
        return (base + _sign((hi << 11) | (lo << 1), 21)) & 0xFFFFFFFF

    def _flow16(self, insn):
        """16-bit control flow is not decoded yet (the 16-bit jumps j_s/jl_s and branches b_s/bcc_s
        read as "next"): their encodings need their own ground truth, the next step after the 32-bit
        flow here. A 16-bit instruction never carries a long immediate, so its length is already
        right; only its flow is missing."""
        return ("next", None, True)

    def target(self, insn):
        kind, target, _ = self.flow(insn)
        return target if kind in ("jump", "call") else None

    def is_call(self, insn):
        return self.flow(insn)[0] in ("call", "icall")

    # ---- not implemented yet (their steps in docs/porting.md) ----

    def _todo(self, what):
        raise NotImplementedError("ARC: %s is not implemented yet (docs/porting.md)" % what)

    def reg_effects(self, insn):
        self._todo("reg_effects")

    def asm(self):
        self._todo("asm")

    def new_uc(self, isa=None):
        self._todo("the engine")
