"""ARCompact instruction decoding (ARC600/ARC700, little-endian): fields and text.

Neither capstone nor Unicorn knows ARCompact, so DREAD decodes it itself (as it does the MSP430). This
module turns the bytes of one instruction into a Fields object, with what an instruction does in
structured form (for the engine, the flow and the register effects) and the text binutils'
arc-elf32-objdump (-m ARC600) prints for it (for dread asm and the reading aids).
tests/test_arch_arc.py checks both against objdump: every 16-bit word, and the 32-bit forms.

The bits (ARCompact programmer's reference; checked against objdump, never the other way round):

  A 16-bit instruction has its major opcode in bits [15:11], 0x0c-0x1f; a 32-bit one in bits [31:27]
  of the word made of its first halfword (the high half) and its second, 0x00-0x0b (0x06-0x0b are
  extension space: decoded as unknown, 4 bytes). A 32-bit long immediate (limm) follows an
  instruction where a source register field holds 0x3e; it is stored as two halfwords, high first.
  In a destination 0x3e means "no destination" (objdump prints 0).

  32-bit general operations (majors 0x04, 0x05): b = bits[14:12]:[26:24], P = bits[23:22] the
  operand format (0: a <- b op c; 1: a <- b op u6; 2: b <- b op s12; 3: b <- b op c/u6 under a
  condition, bit 5 choosing u6), sub-opcode bits[21:16], F (set flags) bit 15, c bits[11:6],
  a bits[5:0] (P = 2: s12 = bits[5:0]:[11:6]; P = 3: the condition in bits[4:0]).

Branch and pc-relative targets count from pcl, the instruction's address with its low two bits
cleared.

What is not decoded: the extension instructions of majors 0x06-0x0b, and of major 0x05 all but the
barrel shifter, the saturating and normalising operations and the multiply; those decode as "ext"
(major 0x05: with objdump's mnemonic) and the engine refuses them. Extension conditions 0x10-0x1f
decode with their number (objdump names three of them: nj, nm, nt).
"""

REG3 = (0, 1, 2, 3, 12, 13, 14, 15)         # the 3-bit register fields of 16-bit instructions
LIMM = 0x3E
PCL = 0x3F
BLINK = 31
SP = 28
GP = 26

REG_NAMES = ["r%d" % i for i in range(64)]
for _n, _a in {26: "gp", 27: "fp", 28: "sp", 29: "ilink1", 30: "ilink2", 31: "blink", 60: "lp_count",
               61: "reserved", 63: "pcl"}.items():
    REG_NAMES[_n] = _a

# Condition codes (the Q field). 0 is "always"; 0x10-0x1f are extension conditions, of which objdump
# names three.
CONDS = {0: "", 1: "eq", 2: "ne", 3: "p", 4: "n", 5: "c", 6: "nc", 7: "v", 8: "nv", 9: "gt", 10: "ge",
         11: "lt", 12: "le", 13: "hi", 14: "ls", 15: "pnz", 0x15: "nj", 0x17: "nm", 0x18: "nt"}

# The general operations of major 0x04 by sub-opcode (three operands, or two for the compares).
GEN = {0x00: "add", 0x01: "adc", 0x02: "sub", 0x03: "sbc", 0x04: "and", 0x05: "or", 0x06: "bic",
       0x07: "xor", 0x08: "max", 0x09: "min", 0x0E: "rsub", 0x0F: "bset", 0x10: "bclr", 0x12: "bxor",
       0x13: "bmsk", 0x14: "add1", 0x15: "add2", 0x16: "add3", 0x17: "sub1", 0x18: "sub2", 0x19: "sub3",
       0x3E: "mpyw", 0x3F: "mpyuw"}
COMPARES = {0x0B: "tst", 0x0C: "cmp", 0x0D: "rcmp", 0x11: "btst"}     # F always set, no destination
# The single-operand group (major 0x04, sub-opcode 0x2f, its own sub-opcode in the a field).
SOP = {0x00: "asl", 0x01: "asr", 0x02: "lsr", 0x03: "ror", 0x04: "rrc", 0x05: "sexb", 0x06: "sexw",
       0x07: "extb", 0x08: "extw", 0x09: "abs", 0x0A: "not", 0x0B: "rlc"}
# Major 0x05, the extension operations of ARC600's options. Those with semantics in the engine:
EXT_GEN = {0x00: "asl", 0x01: "lsr", 0x02: "asr", 0x03: "ror", 0x06: "adds", 0x07: "subs",
           0x08: "divaw", 0x0A: "asls", 0x0B: "asrs", 0x28: "addsdw", 0x29: "subsdw"}
EXT_SOP = {0x00: "swap", 0x01: "norm", 0x02: "sat16", 0x03: "rnd16", 0x04: "abssw", 0x05: "abss",
           0x06: "negsw", 0x07: "negs", 0x08: "normw"}
# Major 0x05 operations objdump knows (ARC600's XMAC and DSP options) that the engine does not run:
# their names, so that the text is objdump's.
EXT_OTHER = {0x09: "minidl", 0x0C: "muldw", 0x0D: "muludw", 0x0E: "mulrdw", 0x0F: "maxidl",
             0x10: "macdw", 0x11: "macudw", 0x12: "macrdw", 0x14: "msubdw", 0x18: "mult", 0x19: "mulut",
             0x1A: "mulrt", 0x1C: "mact", 0x1E: "macrt", 0x20: "msubt", 0x21: "asldw", 0x22: "asrdw",
             0x23: "lsrdw", 0x24: "aslsdw", 0x25: "asrsdw", 0x26: "adcs", 0x27: "sbcs", 0x2B: "maxabssdw",
             0x2C: "crc", 0x30: "mululw", 0x31: "mullw", 0x32: "mulflw", 0x33: "maclw", 0x34: "macflw",
             0x35: "machulw", 0x36: "machlw", 0x37: "machflw", 0x38: "mulhlw", 0x39: "mulhflw",
             0x3A: "dmulpf", 0x3B: "dmacpf"}
EXT_SOP_OTHER = {0x0A: "vbfdw", 0x0B: "fbfdw"}
BRCC = {0: "breq", 1: "brne", 2: "brlt", 3: "brge", 4: "brlo", 5: "brhs", 0xE: "bbit0", 0xF: "bbit1"}
LD_SIZE = {0: 4, 1: 1, 2: 2}
LD_LETTER = {4: "", 1: "b", 2: "h"}
AA = {0: "", 1: ".aw", 2: ".ab", 3: ".as"}

# Auxiliary register names objdump (-m ARC600) prints for lr and sr. Not every one exists on an
# ARC600 (objdump's table is shared with later cores); the number is what counts.
AUX_NAMES = {
    0x0: "status", 0x1: "semaphore", 0x2: "lp_start", 0x3: "lp_end", 0x4: "identity", 0x5: "debug",
    0x6: "pc", 0x7: "adcr", 0x8: "apcr", 0x9: "acr", 0xa: "status32", 0xb: "status32_l1",
    0xc: "status32_l2", 0x10: "ivic", 0x11: "che_mode", 0x12: "mulhi", 0x13: "lockline",
    0x14: "dmc_code_ram", 0x15: "tag_addr_mask", 0x16: "tag_data_mask", 0x17: "line_length_mask",
    0x18: "aux_ldst_ram", 0x19: "unlockline", 0x1a: "ic_ram_address", 0x1b: "ic_tag", 0x1c: "ic_wp",
    0x1d: "ic_data", 0x1e: "ic_ptag", 0x20: "sram_seq", 0x21: "count0", 0x22: "control0",
    0x23: "limit0", 0x24: "pcport", 0x25: "int_vector_base", 0x26: "aux_vbfdw_mode",
    0x27: "aux_vbfdw_bm0", 0x28: "aux_vbfdw_bm1", 0x29: "aux_vbfdw_accu", 0x2a: "aux_vbfdw_ofst",
    0x2b: "aux_vbfdw_intstat", 0x2c: "aux_xmac0_24", 0x2d: "aux_xmac1_24", 0x2e: "aux_xmac2_24",
    0x2f: "aux_fbf_store_16", 0x40: "xtp_newval", 0x41: "aux_macmode", 0x42: "lsp_newval",
    0x43: "aux_irq_lv12", 0x44: "aux_xmac0", 0x45: "aux_xmac1", 0x46: "aux_xmac2", 0x47: "dc_ivdc",
    0x48: "dc_ctrl", 0x49: "dc_ldl", 0x4a: "dc_ivdl", 0x4b: "dc_flsh", 0x4c: "dc_fldl", 0x57: "swstat",
    0x58: "dc_ram_addr", 0x59: "dc_tag", 0x5a: "dc_wp", 0x5b: "dc_data", 0x61: "dccm_base_build",
    0x62: "crc_build", 0x63: "bta_link_build", 0x64: "vbfdw_build", 0x65: "ea_build", 0x66: "dataspace",
    0x67: "memsubsys", 0x68: "vecbase_ac_build", 0x69: "p_base_addr", 0x6a: "data_uncached_build",
    0x6b: "fp_build", 0x6c: "dpfp_build", 0x6d: "mpu_build", 0x6e: "rf_build", 0x6f: "mmu_build",
    0x71: "vecbase_build", 0x72: "d_cache_build", 0x73: "madi_build", 0x74: "dccm_build",
    0x75: "timer_build", 0x76: "ap_build", 0x77: "i_cache_build", 0x78: "iccm_build",
    0x79: "dspram_build", 0x7a: "mac_build", 0x7b: "multiply_build", 0x7c: "swap_build",
    0x7d: "norm_build", 0x7e: "minmax_build", 0x7f: "barrel_build", 0x80: "ax0", 0x81: "ax1",
    0x82: "ax2", 0x83: "ax3", 0x84: "ay0", 0x85: "ay1", 0x86: "ay2", 0x87: "ay3", 0x88: "mx00",
    0x89: "mx01", 0x8a: "mx10", 0x8b: "mx11", 0x8c: "mx20", 0x8d: "mx21", 0x8e: "mx30", 0x8f: "mx31",
    0x90: "my00", 0x91: "my01", 0x92: "my10", 0x93: "my11", 0x94: "my20", 0x95: "my21", 0x96: "my30",
    0x97: "my31", 0x98: "xyconfig", 0x99: "burstsys", 0x9a: "burstxym", 0x9b: "burstsz",
    0x9c: "burstval", 0x9d: "xylsbasex", 0x9e: "xylsbasey", 0x9f: "aux_xmaclw_h", 0xa0: "aux_xmaclw_l",
    0xa1: "se_ctrl", 0xa2: "se_stat", 0xa3: "se_err", 0xa4: "se_eadr", 0xa5: "se_spc", 0xa6: "sdm_base",
    0xa7: "scm_base", 0xa8: "se_dbg_ctrl", 0xa9: "se_dbg_data0", 0xaa: "se_dbg_data1",
    0xab: "se_dbg_data2", 0xac: "se_dbg_data3", 0xad: "se_watch", 0xc0: "bpu_build",
    0xc1: "arc600_build_config", 0xf4: "hwp_build", 0xf5: "pct_build", 0xf6: "cc_build",
    0xf7: "pm_bcr", 0xf8: "scq_switch_build", 0xf9: "vraptor_build", 0xfa: "dma_config",
    0xfb: "simd_config", 0xfc: "vlc_build", 0xfd: "simd_dma_build", 0xfe: "ifetch_queue_build",
    0xff: "smart_build", 0x100: "count1", 0x101: "control1", 0x102: "limit1", 0x103: "timer_xx",
    0x200: "aux_irq_lev", 0x201: "aux_irq_hint", 0x202: "aux_inter_core_interrupt",
    0x220: "ap_amv0", 0x221: "ap_amm0", 0x222: "ap_ac0", 0x223: "ap_amv1", 0x224: "ap_amm1",
    0x225: "ap_ac1", 0x226: "ap_amv2", 0x227: "ap_amm2", 0x228: "ap_ac2", 0x229: "ap_amv3",
    0x22a: "ap_amm3", 0x22b: "ap_ac3", 0x22c: "ap_amv4", 0x22d: "ap_amm4", 0x22e: "ap_ac4",
    0x22f: "ap_amv5", 0x230: "ap_amm5", 0x231: "ap_ac5", 0x232: "ap_amv6", 0x233: "ap_amm6",
    0x234: "ap_ac6", 0x235: "ap_amv7", 0x236: "ap_amm7", 0x237: "ap_ac7", 0x400: "eret",
    0x401: "erbta", 0x402: "erstatus", 0x403: "ecr", 0x404: "efa", 0x409: "mpuen", 0x40a: "icause1",
    0x40b: "icause2", 0x40c: "aux_ienable", 0x40d: "aux_itrigger", 0x410: "xpu", 0x412: "bta",
    0x415: "aux_irq_pulse_cancel", 0x416: "aux_irq_pending", 0x420: "mpuic", 0x421: "mpufa",
    0x450: "pm_status", 0x451: "wake", 0x452: "dvfs_performance", 0x453: "pwr_ctrl",
    0x700: "smart_control", 0x701: "smart_data"}
for _i in range(16):
    AUX_NAMES[0x422 + 2 * _i] = "mpurdb%d" % _i
    AUX_NAMES[0x423 + 2 * _i] = "mpurdp%d" % _i
AUX_NUMBERS = {v: k for k, v in AUX_NAMES.items()}


def sign(value, bits):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def hex0(v):
    """An unsigned immediate as objdump prints one: 0, else in hex."""
    return "0" if v == 0 else "0x%x" % v


class Fields:
    """One instruction. op is what it does ("add", "ld", "b", "j", ...; "ext" and "unknown" for what
    the engine does not run), size its bytes, short whether it is 16-bit. Operands: dst (a register
    number, or None: none or discarded), srcs (a tuple of operands, each ("r", n) or ("i", value)),
    cond (0-31, 0 always), setf (sets the flags), delay (.d: the next instruction runs before a taken
    branch goes), target (a branch's or lp's absolute address), limm (the long immediate or None).
    Memory (ld, st, push, pop): width (1, 2, 4), sext (.x), aa (0 none, 1 .aw, 2 .ab, 3 .as), di
    (.di: past the cache). ld: dst <- [srcs[0] + srcs[1]]; st: [srcs[0] + srcs[1]] <- srcs[2].
    aux (lr, sr): the auxiliary register operand. mnemonic and op_str: objdump's text."""

    __slots__ = ("op", "size", "short", "dst", "srcs", "cond", "setf", "delay", "target", "limm",
                 "width", "sext", "aa", "di", "aux", "mnemonic", "op_str", "brcond")

    def __init__(self, op, size, short=False):
        self.op, self.size, self.short = op, size, short
        self.dst, self.srcs, self.cond, self.setf, self.delay = None, (), 0, False, False
        self.target = self.limm = self.aux = self.brcond = None
        self.width, self.sext, self.aa, self.di = 4, False, 0, False
        self.mnemonic, self.op_str = op, ""

    def __repr__(self):
        return "Fields(%s %s %s)" % (self.op, self.mnemonic, self.op_str)


def unknown(size, word, short=False):
    f = Fields("unknown", size, short)
    f.mnemonic, f.op_str = (".short", "0x%04x" % word) if short else (".word", "0x%08x" % word)
    return f


class _Ops:
    """Operand text and values of one 32-bit instruction, with its long immediate."""

    def __init__(self, f, limm):
        self.f, self.limm_value = f, limm

    def reg(self, n):
        """A source register field: ("r", n), or the long immediate."""
        if n == LIMM:
            if self.limm_value is None:
                raise _Short()
            self.f.limm = self.limm_value
            self.f.size = 8
            return ("i", self.limm_value)
        return ("r", n)

    @staticmethod
    def text(op, kind="hex"):
        if op[0] == "r":
            return REG_NAMES[op[1]]
        if kind == "dec":
            return "%d" % op[1]
        return hex0(op[1] & 0xFFFFFFFF)

    @staticmethod
    def dst_text(n):
        return "0" if n == LIMM else REG_NAMES[n]


class _Short(Exception):
    """The long immediate lies past the bytes given."""


def _limm_of(code):
    if len(code) < 8:
        return None
    return (code[4] | code[5] << 8) << 16 | (code[6] | code[7] << 8)


def _cond_suffix(q, dot=True):
    """The condition as objdump attaches it: ".eq" to most instructions, "eq" to branches and jumps
    (dot False), whose extension conditions take a dot all the same."""
    name = CONDS.get(q, "")
    if not name:
        return ""
    if dot or q >= 0x10:
        return "." + name
    return name


def decode(code, address):
    """Fields of the instruction at the start of `code` (bytes) at `address`; None where the bytes
    end inside it."""
    if len(code) < 2:
        return None
    hw0 = code[0] | code[1] << 8
    major = hw0 >> 11
    if major >= 0x0C:
        if major == 0x0E and ((hw0 >> 3) & 3) != 3 and (((hw0 & 7) << 3) | ((hw0 >> 5) & 7)) == LIMM:
            if len(code) < 6:
                return None
            limm = (code[2] | code[3] << 8) << 16 | (code[4] | code[5] << 8)
            return _decode16(hw0, address, limm)
        return _decode16(hw0, address, None)
    if len(code) < 4:
        return None
    w = hw0 << 16 | code[2] | code[3] << 8
    try:
        return _decode32(w, major, address, _limm_of(code))
    except _Short:
        return None


def length(code):
    """The instruction's length in bytes from its first bytes (2, 4, 6 or 8), as decode gives it; 4
    for a 32-bit instruction whose bytes end before its long immediate would."""
    hw0 = code[0] | code[1] << 8
    major = hw0 >> 11
    if major >= 0x0C:
        if major == 0x0E and ((hw0 >> 3) & 3) != 3 and (((hw0 & 7) << 3) | ((hw0 >> 5) & 7)) == LIMM:
            return 6
        return 2
    if len(code) < 4:
        return 4
    f = decode(bytes(code[:4]) + b"\0\0\0\0", 0)
    return f.size


# ---- 32-bit ----

def _decode32(w, major, address, limm):
    pcl = address & ~3
    if major == 0x00:
        return _branch(w, pcl)
    if major == 0x01:
        if w & 0x10000:
            return _brcc(w, pcl, limm)
        return _bl(w, pcl)
    if major == 0x02:
        return _ld(w, limm)
    if major == 0x03:
        return _st(w, limm)
    if major in (0x04, 0x05):
        return _general(w, major, pcl, limm)
    return unknown(4, w)


def _branch(w, pcl):
    disp = ((w >> 17) & 0x3FF) << 1 | ((w >> 6) & 0x3FF) << 11
    f = Fields("b", 4)
    f.delay = bool(w & 0x20)
    if w & 0x10000:                             # B s25 (bit 4 reserved; objdump ignores it)
        disp = sign(disp | (w & 0xF) << 21, 25)
    else:                                       # Bcc s21
        disp = sign(disp, 21)
        f.cond = w & 0x1F
    f.target = (pcl + disp) & 0xFFFFFFFF
    f.mnemonic = "b" + CONDS.get(f.cond, "") + (".d" if f.delay else "")
    f.op_str = "%d" % disp
    return f


def _bl(w, pcl):
    disp = ((w >> 18) & 0x1FF) << 2 | ((w >> 6) & 0x3FF) << 11
    f = Fields("bl", 4)
    f.delay = bool(w & 0x20)
    if w & 0x20000:                             # BL s25 (bit 4 reserved)
        disp = sign(disp | (w & 0xF) << 21, 25)
    else:                                       # BLcc s21
        disp = sign(disp, 21)
        f.cond = w & 0x1F
    f.target = (pcl + disp) & 0xFFFFFFFF
    f.mnemonic = "bl" + _cond_suffix(f.cond, False) + (".d" if f.delay else "")
    f.op_str = "%d" % disp
    return f


def _b6(w):
    return ((w >> 12) & 7) << 3 | (w >> 24) & 7


def _brcc(w, pcl, limm):
    sub = w & 0xF
    if sub not in BRCC:
        return unknown(4, w)
    name = BRCC[sub]
    f = Fields(name if name.startswith("bbit") else "br", 4)
    f.brcond = name[2:] if f.op == "br" else None
    disp = sign(((w >> 17) & 0x7F) << 1 | ((w >> 15) & 1) << 8, 9)
    f.delay = bool(w & 0x20)
    f.target = (pcl + disp) & 0xFFFFFFFF
    o = _Ops(f, limm)
    b = _b6(w)
    cf = (w >> 6) & 0x3F
    if (b == LIMM or (cf == LIMM and not w & 0x10)) and f.delay or (b == LIMM and cf == LIMM and not w & 0x10
                                                                        and f.op == "br"):
        return unknown(4, w)
    second = ("i", cf) if w & 0x10 else o.reg(cf)
    first = o.reg(b)
    f.srcs = (first, second)
    f.mnemonic = name + (".d" if f.delay else "")
    f.op_str = "%s,%s,%d" % (o.text(first), o.text(second), disp)
    return f


def _mem_text(base, offset, offset_dec=True, keep_zero=False):
    b = _Ops.text(base)
    if offset[0] == "r":
        return "[%s,%s]" % (b, REG_NAMES[offset[1]])
    if offset[1] == 0 and not keep_zero:
        return "[%s]" % b
    return "[%s,%s]" % (b, "%d" % offset[1] if offset_dec else hex0(offset[1]))


def _ld(w, limm):
    zz, a = (w >> 7) & 3, w & 0x3F
    if zz == 3 or a == LIMM:
        return unknown(4, w)
    f = Fields("ld", 4)
    f.width, f.sext, f.aa, f.di = LD_SIZE[zz], bool(w & 0x40), (w >> 9) & 3, bool(w & 0x800)
    o = _Ops(f, limm)
    base = o.reg(_b6(w))
    offset = ("i", sign(((w >> 16) & 0xFF) | ((w >> 15) & 1) << 8, 9))
    if base[0] == "i" and not offset[1]:        # [limm]: objdump prints no write-back (none is
        f.aa = 0                                # possible; the engine ignores aa on a limm base)
    f.dst, f.srcs = a, (base, offset)
    f.mnemonic = "ld" + LD_LETTER[f.width] + (".di" if f.di else "") + AA[f.aa] + (".x" if f.sext else "")
    f.op_str = "%s,%s" % (REG_NAMES[a], _mem_text(base, offset))
    return f


def _st(w, limm):
    zz = (w >> 1) & 3
    if zz == 3:
        return unknown(4, w)
    f = Fields("st", 4)
    f.width, f.aa, f.di = LD_SIZE[zz], (w >> 3) & 3, bool(w & 0x20)
    o = _Ops(f, limm)
    base = o.reg(_b6(w))
    value = o.reg((w >> 6) & 0x3F)
    offset = ("i", sign(((w >> 16) & 0xFF) | ((w >> 15) & 1) << 8, 9))
    if base[0] == "i" and value[0] == "r":      # [limm]: no offset, no write-back
        if offset[1]:
            return unknown(4, w)
        f.aa = 0
    f.srcs = (base, offset, value)
    f.mnemonic = "st" + LD_LETTER[f.width] + (".di" if f.di else "") + AA[f.aa]
    f.op_str = "%s,%s" % (o.text(value), _mem_text(base, offset, keep_zero=value[0] == "i"))
    return f


def _general(w, major, pcl, limm):
    b, p, sub, setf = _b6(w), (w >> 22) & 3, (w >> 16) & 0x3F, bool(w & 0x8000)
    c, a = (w >> 6) & 0x3F, w & 0x3F
    if major == 0x04:
        if 0x30 <= sub <= 0x37:
            return _ld_rr(w, b, c, a, limm)
        if sub in GEN:
            return _alu(w, GEN[sub], b, p, setf, c, a, limm)
        if sub in COMPARES:
            return _compare(w, COMPARES[sub], b, p, setf, c, a, limm)
        if sub == 0x0A:
            return _mov(w, b, p, setf, c, a, limm)
        if 0x20 <= sub <= 0x23:
            return _jump(w, sub, b, p, setf, c, a, limm)
        if sub == 0x28:
            return _lp(w, p, setf, c, a, pcl)
        if sub == 0x29:
            return _flag(w, p, setf, c, a, limm)
        if sub in (0x2A, 0x2B):
            return _aux(w, sub, b, p, setf, c, a, limm)
        if sub == 0x2F:
            if a == 0x3F:
                return _zop(w, b, p, setf, c, limm)
            if a in SOP:
                return _sop(w, SOP[a], b, p, setf, c, limm)
        return unknown(4, w)
    if sub in EXT_GEN:
        return _alu(w, EXT_GEN[sub], b, p, setf, c, a, limm)
    if sub in (0x04, 0x05):
        return _mul64(w, "mul64" if sub == 4 else "mulu64", b, p, setf, c, a, limm)
    if sub in EXT_OTHER and (p == 0 or sub not in (0x3A, 0x3B)):  # dmulpf, dmacpf: registers only
        f = _alu(w, EXT_OTHER[sub], b, p, setf, c, a, limm)
        if f.op != "unknown":
            f.op = "ext"
        return f
    if sub == 0x2F:
        if a in EXT_SOP:
            return _sop(w, EXT_SOP[a], b, p, setf, c, limm)
        if a in EXT_SOP_OTHER:
            f = _sop(w, EXT_SOP_OTHER[a], b, p, setf, c, limm)
            if f.op != "unknown":
                f.op = "ext"
            return f
    return unknown(4, w)


def _operands(w, b, p, c, a, o):
    """(dst, src1, src2, cond, kind of src2's text) of the general format."""
    if p == 0:
        return a, o.reg(b), o.reg(c), 0, "hex"
    if p == 1:
        return a, o.reg(b), ("i", c), 0, "hex"
    if p == 2:
        return b, o.reg(b), ("i", sign(c | a << 6, 12)), 0, "dec"
    if w & 0x20:
        return b, o.reg(b), ("i", c), w & 0x1F, "hex"
    return b, o.reg(b), o.reg(c), w & 0x1F, "hex"


def _alu(w, name, b, p, setf, c, a, limm):
    f = Fields(name, 4)
    o = _Ops(f, limm)
    dst, s1, s2, f.cond, kind = _operands(w, b, p, c, a, o)
    if p >= 2 and b == LIMM:                    # b <- b op ...: a long immediate as the destination
        dst = LIMM
    f.dst, f.srcs, f.setf = (None if dst == LIMM else dst), (s1, s2), setf
    if name == "rsub" and (p == 1 or (p == 3 and w & 0x20)) and c == 0:   # objdump's alias: neg a,b
        f.mnemonic = "neg" + (".f" if setf else "") + _cond_suffix(f.cond)
        f.op_str = "%s,%s" % (o.dst_text(dst), o.text(s1))
        return f
    f.mnemonic = name + (".f" if setf else "") + _cond_suffix(f.cond)
    f.op_str = "%s,%s,%s" % (o.dst_text(dst), o.text(s1), o.text(s2, kind))
    return f


def _compare(w, name, b, p, setf, c, a, limm):
    if not setf or (p < 2 and a):
        return unknown(4, w)
    f = Fields(name, 4)
    o = _Ops(f, limm)
    _, s1, s2, f.cond, kind = _operands(w, b, p, c, a, o)
    f.srcs, f.setf = (s1, s2), True
    f.mnemonic = name + _cond_suffix(f.cond)
    f.op_str = "%s,%s" % (o.text(s1), o.text(s2, kind))
    return f


def _mul64(w, name, b, p, setf, c, a, limm):
    """mul64 / mulu64 b,c: the 64-bit product into mlo, mhi (r57, r59) and mulhi; no destination."""
    if setf or (p < 2 and a != LIMM):
        return unknown(4, w)
    f = Fields(name, 4)
    o = _Ops(f, limm)
    _, s1, s2, f.cond, kind = _operands(w, b, p, c, a, o)
    f.srcs = (s1, s2)
    f.mnemonic = name + _cond_suffix(f.cond)
    f.op_str = "0,%s,%s" % (o.text(s1), o.text(s2, kind))
    return f


def _mov(w, b, p, setf, c, a, limm):
    f = Fields("mov", 4)
    o = _Ops(f, limm)
    if p == 0:
        src, kind = o.reg(c), "hex"
    elif p == 1:
        src, kind = ("i", c), "hex"
    elif p == 2:
        src, kind = ("i", sign(c | a << 6, 12)), "dec"
    else:
        src, kind = (("i", c) if w & 0x20 else o.reg(c)), "hex"
        f.cond = w & 0x1F
    f.dst, f.srcs, f.setf = (None if b == LIMM else b), (src,), setf
    if b == LIMM and p == 1 and not setf and src[1] == 0:          # mov 0,0
        f.op = "nop"
        f.mnemonic, f.op_str = "nop", ""
        return f
    f.mnemonic = "mov" + (".f" if setf else "") + _cond_suffix(f.cond)
    f.op_str = "%s,%s" % (o.dst_text(b), o.text(src, kind))
    return f


def _jump(w, sub, b, p, setf, c, a, limm):
    link, delay = sub >= 0x22, bool(sub & 1)
    f = Fields("jl" if link else "j", 4)
    o = _Ops(f, limm)
    f.delay = delay
    if p == 0:
        if c == LIMM and delay:
            return unknown(4, w)
        src, text = o.reg(c), None
    elif p == 1:
        src, text = ("i", c), hex0(c)
    elif p == 2:
        src = ("i", sign(c | a << 6, 12))
        text = "%d" % src[1]
    else:
        f.cond = w & 0x1F
        if w & 0x20:
            src, text = ("i", c), hex0(c)
        else:
            if c == LIMM and delay:
                return unknown(4, w)
            src, text = o.reg(c), None
    if text is None:
        text = hex0(src[1]) if src[0] == "i" else "[%s]" % REG_NAMES[src[1]]
    if setf and (link or delay or src not in (("r", 29), ("r", 30))):
        return unknown(4, w)
    f.setf, f.srcs = setf, (src,)
    f.mnemonic = f.op + (".f" if setf else "") + _cond_suffix(f.cond, False) + (".d" if delay else "")
    f.op_str = text
    return f


def _lp(w, p, setf, c, a, pcl):
    f = Fields("lp", 4)
    if setf:
        return unknown(4, w)
    if p == 2:
        disp = sign(c | a << 6, 12) * 2
        text = "%d" % disp
    elif p == 1:
        disp = c * 2
        text = hex0(disp)
    elif p == 3 and w & 0x20:
        disp = c * 2
        text = hex0(disp)
        f.cond = w & 0x1F
    else:
        return unknown(4, w)
    f.target = (pcl + disp) & 0xFFFFFFFF
    f.mnemonic = "lp" + _cond_suffix(f.cond, False)
    f.op_str = text
    return f


def _flag(w, p, setf, c, a, limm):
    if setf:
        return unknown(4, w)
    f = Fields("flag", 4)
    o = _Ops(f, limm)
    if p == 0:
        src, kind = o.reg(c), "hex"
    elif p == 1:
        src, kind = ("i", c), "hex"
    elif p == 2:
        src, kind = ("i", sign(c | a << 6, 12)), "dec"
    else:
        f.cond = w & 0x1F
        src, kind = (("i", c) if w & 0x20 else o.reg(c)), "hex"
    f.srcs = (src,)
    f.mnemonic = "flag" + _cond_suffix(f.cond)
    f.op_str = o.text(src, kind)
    return f


def _aux_text(op, kind):
    if op[0] == "r":
        return "[%s]" % REG_NAMES[op[1]]
    name = AUX_NAMES.get(op[1] & 0xFFFFFFFF)
    if name:
        return "[%s]" % name
    return "[%s]" % (("%d" % op[1]) if kind == "dec" else hex0(op[1] & 0xFFFFFFFF))


def _aux(w, sub, b, p, setf, c, a, limm):
    if setf or p == 3 or (p == 1 and a):
        return unknown(4, w)
    f = Fields("lr" if sub == 0x2A else "sr", 4)
    o = _Ops(f, limm)
    if p == 0:
        aux, kind = o.reg(c), "hex"
    elif p == 1:
        aux, kind = ("i", c), "hex"
    else:
        aux, kind = ("i", sign(c | a << 6, 12)), "dec"
    f.aux = aux
    if f.op == "lr":
        f.dst = None if b == LIMM else b
        f.op_str = "%s,%s" % (o.dst_text(b), _aux_text(aux, kind))
    else:
        value = o.reg(b)
        f.srcs = (value,)
        f.op_str = "%s,%s" % (o.text(value), _aux_text(aux, kind))
    f.mnemonic = f.op
    return f


def _sop(w, name, b, p, setf, c, limm):
    if p >= 2:
        return unknown(4, w)
    f = Fields(name + "1" if name in ("asl", "asr", "lsr", "ror") else name, 4)
    o = _Ops(f, limm)
    src = o.reg(c) if p == 0 else ("i", c)
    f.dst, f.srcs, f.setf = (None if b == LIMM else b), (src,), setf
    f.mnemonic = name + (".f" if setf else "")
    f.op_str = "%s,%s" % (o.dst_text(b), o.text(src))
    return f


def _zop(w, b, p, setf, c, limm):
    if b != 1 or p >= 2 or setf:                # only sleep is an ARC600 instruction here
        return unknown(4, w)
    f = Fields("sleep", 4)
    o = _Ops(f, limm)
    src = o.reg(c) if p == 0 else ("i", c)
    f.srcs = (src,)
    f.mnemonic, f.op_str = "sleep", o.text(src)
    return f


def _ld_rr(w, b, c, a, limm):
    sub = (w >> 16) & 0x3F
    zz = (sub >> 1) & 3
    if zz == 3 or a == LIMM:
        return unknown(4, w)
    f = Fields("ld", 4)
    f.width, f.sext, f.aa, f.di = LD_SIZE[zz], bool(sub & 1), (w >> 22) & 3, bool(w & 0x8000)
    o = _Ops(f, limm)
    base = o.reg(b)
    index = o.reg(c)
    f.dst, f.srcs = a, (base, index)
    f.mnemonic = "ld" + LD_LETTER[f.width] + (".di" if f.di else "") + AA[f.aa] + (".x" if f.sext else "")
    ib = REG_NAMES[index[1]] if index[0] == "r" else hex0(index[1])
    f.op_str = "%s,[%s,%s]" % (REG_NAMES[a], o.text(base), ib)
    return f


# ---- 16-bit ----

def _r3(n):
    return REG3[n & 7]


def _set(f, mnemonic, op_str):
    f.mnemonic, f.op_str = mnemonic, op_str
    return f


def _decode16(h, address, limm):
    major = h >> 11
    b, c = _r3(h >> 8), _r3(h >> 5)
    pcl = address & ~3
    N = REG_NAMES
    if major == 0x0C:
        i, a = (h >> 3) & 3, _r3(h)
        if i == 3:
            f = Fields("add", 2, True)
            f.dst, f.srcs = a, (("r", b), ("r", c))
            return _set(f, "add_s", "%s,%s,%s" % (N[a], N[b], N[c]))
        f = Fields("ld", 2, True)
        f.width = (4, 1, 2)[i]
        f.dst, f.srcs = a, (("r", b), ("r", c))
        return _set(f, ("ld_s", "ldb_s", "ldw_s")[i], "%s,[%s,%s]" % (N[a], N[b], N[c]))
    if major == 0x0D:
        i, u3 = (h >> 3) & 3, h & 7
        f = Fields(("add", "sub", "asl", "asr")[i], 2, True)
        f.dst, f.srcs = c, (("r", b), ("i", u3))
        return _set(f, f.op + "_s", "%s,%s,%s" % (N[c], N[b], hex0(u3)))
    if major == 0x0E:
        i, hr = (h >> 3) & 3, (h & 7) << 3 | (h >> 5) & 7
        f = Fields(("add", "mov", "cmp", "mov")[i], 2, True)
        if i == 3:                              # mov_s h,b
            f.dst, f.srcs = (None if hr == LIMM else hr), (("r", b),)
            return _set(f, "mov_s", "%s,%s" % ("0" if hr == LIMM else N[hr], N[b]))
        if hr == LIMM:
            src, text = ("i", limm), hex0(limm)
            f.size, f.limm = 6, limm
        else:
            src, text = ("r", hr), N[hr]
        if i == 0:
            f.dst, f.srcs = b, (("r", b), src)
            return _set(f, "add_s", "%s,%s,%s" % (N[b], N[b], text))
        if i == 1:
            f.dst, f.srcs = b, (src,)
            return _set(f, "mov_s", "%s,%s" % (N[b], text))
        f.srcs, f.setf = (("r", b), src), True
        return _set(f, "cmp_s", "%s,%s" % (N[b], text))
    if major == 0x0F:
        return _general16(h, b, c)
    if 0x10 <= major <= 0x16:
        k = major - 0x10
        width, scale, store, sext = ((4, 4, False, False), (1, 1, False, False), (2, 2, False, False),
                                     (2, 2, False, True), (4, 4, True, False), (1, 1, True, False),
                                     (2, 2, True, False))[k]
        off = (h & 0x1F) * scale
        f = Fields("st" if store else "ld", 2, True)
        f.width, f.sext = width, sext
        name = ("ld_s", "ldb_s", "ldw_s", "ldw_s.x", "st_s", "stb_s", "stw_s")[k]
        if store:
            f.srcs = (("r", b), ("i", off), ("r", c))
        else:
            f.dst, f.srcs = c, (("r", b), ("i", off))
        return _set(f, name, "%s,[%s,%s]" % (N[c], N[b], hex0(off)))
    if major == 0x17:
        i, u5 = (h >> 5) & 7, h & 0x1F
        if i == 7:
            f = Fields("btst", 2, True)
            f.srcs, f.setf = (("r", b), ("i", u5)), True
            return _set(f, "btst_s", "%s,%s" % (N[b], hex0(u5)))
        f = Fields(("asl", "lsr", "asr", "sub", "bset", "bclr", "bmsk")[i], 2, True)
        f.dst, f.srcs = b, (("r", b), ("i", u5))
        return _set(f, f.op + "_s", "%s,%s,%s" % (N[b], N[b], hex0(u5)))
    if major == 0x18:
        return _sp16(h, b)
    if major == 0x19:
        i, s9 = (h >> 9) & 3, sign(h & 0x1FF, 9)
        off = s9 * (4, 1, 2, 4)[i]
        if i == 3:
            f = Fields("add", 2, True)
            f.dst, f.srcs = 0, (("r", GP), ("i", off))
            return _set(f, "add_s", "r0,gp,%d" % off)
        f = Fields("ld", 2, True)
        f.width = (4, 1, 2)[i]
        f.dst, f.srcs = 0, (("r", GP), ("i", off))
        return _set(f, ("ld_s", "ldb_s", "ldw_s")[i], "r0,[gp,%d]" % off)
    if major == 0x1A:
        off = (h & 0xFF) * 4
        f = Fields("ld", 2, True)
        f.dst, f.srcs = b, (("r", PCL), ("i", off))
        return _set(f, "ld_s", "%s,[pcl,%s]" % (N[b], hex0(off)))
    if major == 0x1B:
        f = Fields("mov", 2, True)
        f.dst, f.srcs = b, (("i", h & 0xFF),)
        return _set(f, "mov_s", "%s,%s" % (N[b], hex0(h & 0xFF)))
    if major == 0x1C:
        u7 = h & 0x7F
        if h & 0x80:
            f = Fields("cmp", 2, True)
            f.srcs, f.setf = (("r", b), ("i", u7)), True
            return _set(f, "cmp_s", "%s,%s" % (N[b], hex0(u7)))
        f = Fields("add", 2, True)
        f.dst, f.srcs = b, (("r", b), ("i", u7))
        return _set(f, "add_s", "%s,%s,%s" % (N[b], N[b], hex0(u7)))
    if major == 0x1D:
        disp = sign(h & 0x7F, 7) * 2
        f = Fields("br", 2, True)
        f.brcond = "ne" if h & 0x80 else "eq"
        f.srcs, f.target = (("r", b), ("i", 0)), (pcl + disp) & 0xFFFFFFFF
        return _set(f, "brne_s" if h & 0x80 else "breq_s", "%s,0,%d" % (N[b], disp))
    if major == 0x1E:
        i = (h >> 9) & 3
        f = Fields("b", 2, True)
        if i < 3:
            disp = sign(h & 0x1FF, 9) * 2
            f.cond = (0, 1, 2)[i]
            name = ("b_s", "beq_s", "bne_s")[i]
        else:
            k = (h >> 6) & 7
            disp = sign(h & 0x3F, 6) * 2
            f.cond = (9, 10, 11, 12, 13, 6, 5, 14)[k]
            name = ("bgt_s", "bge_s", "blt_s", "ble_s", "bhi_s", "bhs_s", "blo_s", "bls_s")[k]
        f.target = (pcl + disp) & 0xFFFFFFFF
        return _set(f, name, "%d" % disp)
    # major 0x1f: bl_s
    disp = sign(h & 0x7FF, 11) * 4
    f = Fields("bl", 2, True)
    f.target = (pcl + disp) & 0xFFFFFFFF
    return _set(f, "bl_s", "%d" % disp)


GEN16 = {0x02: "sub", 0x04: "and", 0x05: "or", 0x06: "bic", 0x07: "xor", 0x14: "add1", 0x15: "add2",
         0x16: "add3", 0x18: "asl", 0x19: "lsr", 0x1A: "asr"}
UNARY16 = {0x0D: "sexb", 0x0E: "sexw", 0x0F: "extb", 0x10: "extw", 0x11: "abs", 0x12: "not",
           0x13: "neg", 0x1B: "asl1", 0x1C: "asr1", 0x1D: "lsr1"}


def _general16(h, b, c):
    i = h & 0x1F
    N = REG_NAMES
    if i == 0:
        sub = (h >> 5) & 7
        if sub < 4:
            f = Fields("jl" if sub >= 2 else "j", 2, True)
            f.delay, f.srcs = bool(sub & 1), (("r", b),)
            return _set(f, ("j_s", "j_s.d", "jl_s", "jl_s.d")[sub], "[%s]" % N[b])
        if sub == 6:
            f = Fields("sub", 2, True)
            f.dst, f.srcs, f.cond = b, (("r", b), ("r", b)), 2
            return _set(f, "sub_s.ne", "%s,%s,%s" % (N[b], N[b], N[b]))
        if sub == 7:
            z = (h >> 8) & 7
            if z == 0:
                return _set(Fields("nop", 2, True), "nop_s", "")
            if z in (4, 5, 6, 7):
                f = Fields("j", 2, True)
                f.srcs = (("r", BLINK),)
                f.cond = {4: 1, 5: 2}.get(z, 0)
                f.delay = z == 7
                return _set(f, ("jeq_s", "jne_s", "j_s", "j_s.d")[z - 4], "[blink]")
        return unknown(2, h, True)
    if i in GEN16:
        f = Fields(GEN16[i], 2, True)
        f.dst, f.srcs = b, (("r", b), ("r", c))
        return _set(f, f.op + "_s", "%s,%s,%s" % (N[b], N[b], N[c]))
    if i in UNARY16:
        f = Fields(UNARY16[i], 2, True)
        f.dst, f.srcs = b, (("r", c),)
        return _set(f, UNARY16[i].rstrip("1") + "_s", "%s,%s" % (N[b], N[c]))
    if i == 0x0B:
        f = Fields("tst", 2, True)
        f.srcs, f.setf = (("r", b), ("r", c)), True
        return _set(f, "tst_s", "%s,%s" % (N[b], N[c]))
    if i == 0x0C:
        f = Fields("mul64", 2, True)
        f.srcs = (("r", b), ("r", c))
        return _set(f, "mul64_s", "0,%s,%s" % (N[b], N[c]))
    if i == 0x1F and h == 0x7FFF:
        return _set(Fields("brk", 2, True), "brk_s", "")
    return unknown(2, h, True)


def _sp16(h, b):
    i, u5 = (h >> 5) & 7, h & 0x1F
    N = REG_NAMES
    off = u5 * 4
    if i < 4:
        f = Fields("st" if i in (2, 3) else "ld", 2, True)
        f.width = 1 if i in (1, 3) else 4
        if f.op == "st":
            f.srcs = (("r", SP), ("i", off), ("r", b))
        else:
            f.dst, f.srcs = b, (("r", SP), ("i", off))
        return _set(f, ("ld_s", "ldb_s", "st_s", "stb_s")[i], "%s,[sp,%s]" % (N[b], hex0(off)))
    if i == 4:
        f = Fields("add", 2, True)
        f.dst, f.srcs = b, (("r", SP), ("i", off))
        return _set(f, "add_s", "%s,sp,%s" % (N[b], hex0(off)))
    if i == 5:
        k = (h >> 8) & 7
        if k > 1:
            return unknown(2, h, True)
        f = Fields("sub" if k else "add", 2, True)
        f.dst, f.srcs = SP, (("r", SP), ("i", off))
        return _set(f, ("add_s", "sub_s")[k], "sp,sp,%s" % hex0(off))
    if u5 not in (0x01, 0x11):
        return unknown(2, h, True)
    reg = BLINK if u5 == 0x11 else b
    f = Fields("pop" if i == 6 else "push", 2, True)
    if i == 6:
        f.dst = reg
    else:
        f.srcs = (("r", reg),)
    return _set(f, f.op + "_s", N[reg])
