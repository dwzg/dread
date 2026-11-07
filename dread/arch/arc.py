"""The ARC layer (ARCompact: ARC600/ARC700, little-endian), under way.

Neither capstone nor Unicorn knows ARCompact, so this layer decodes the instructions itself
(dread.arch.arc_decode, checked against binutils' objdump) and runs them on an engine of DREAD's own
(dread.engines.arc). Instructions are objects with capstone's attributes (address, size, bytes,
mnemonic, op_str) and the decoded Fields (`fields`).

Registers: r0-r25 general, r26 gp, r27 fp, r28 sp, r29 ilink1, r30 ilink2 (the return addresses of
the two interrupt levels), r31 blink (a call's return address), r60 lp_count (the zero-overhead
loop's counter), r63 pcl (the instruction's address, its low two bits clear; read only). The flags
Z, N, C, V are bits of the auxiliary register STATUS32.

The calling convention (GCC's for ARC): arguments in r0-r7, the result in r0 (r0:r1 for 64 bits),
r13-r25, gp and fp kept by the callee, r0-r12 not, blink the return address. Not confirmed against an
ABI document: taken from GCC's register usage (overridable by [abi]).

Delay slots: a branch, jump or call written .d runs the instruction behind it before it goes
(`delay_slot`), on both ways of a conditional one; a call written .d returns behind its slot.
Zero-overhead loops: lp sets LP_START (behind it) and LP_END (its target); where the core is about
to run the instruction at LP_END, it counts lp_count down and goes back to LP_START until the count
is zero. That back edge belongs to no instruction: `flow` gives lp as a conditional jump to LP_END
(the loop's exit) and `loop_body` says which instructions a loop holds.

Constructed from [arch]: isa ("arc600" or "arc700"), endian ("little"), ghidra.

Not implemented yet (docs/porting.md, docs/todo.md "ARC"): regflow's bit rules and the case
generator's hints, the overlay's long branch beyond 16 MiB, the system run and the lifter.
"""

import struct

from dread.arch import arc_decode as D
from dread.arch.arc_decode import REG_NAMES, LIMM, BLINK

NAMES = {i: "r%d" % i for i in range(64)}
NUMBER = {"r%d" % i: i for i in range(64)}
for _i, _n in enumerate(REG_NAMES):
    if not _n.startswith("r") or _n == "reserved":
        NUMBER[_n] = _i
NUMBER["limm"] = LIMM
NUMBER["lp_count"] = 60
NUMBER["mlo"], NUMBER["mmid"], NUMBER["mhi"] = 57, 58, 59
CANON = {i: (REG_NAMES[i] if i in (26, 27, 28, 29, 30, 31, 60, 63) else "r%d" % i) for i in range(64)}
NUMBER["pc"], NUMBER["status32"] = 64, 65          # the engine's ids beside the core registers
CANON[64], CANON[65] = "pc", "status32"

# Aux register numbers the engine and the layer name (dread.engines.arc keeps the rest by number).
AUX_STATUS32, AUX_STATUS32_L1, AUX_STATUS32_L2 = 0x0A, 0x0B, 0x0C
AUX_LP_START, AUX_LP_END = 0x02, 0x03
FLAG_Z, FLAG_N, FLAG_C, FLAG_V = 1 << 11, 1 << 10, 1 << 9, 1 << 8      # STATUS32's flags
STATUS32_E1, STATUS32_E2 = 1 << 1, 1 << 2                              # the interrupt enables
STATUS32_H = 1                                                          # halted


class Insn:
    """One decoded instruction: address, size, bytes, mnemonic, op_str (objdump's text) and its
    Fields (`fields`)."""

    __slots__ = ("address", "size", "bytes", "mnemonic", "op_str", "fields", "__dict__")

    def __init__(self, address, code, f):
        self.address, self.size, self.fields = address, f.size, f
        self.bytes = bytes(code[:f.size])
        self.mnemonic, self.op_str = f.mnemonic, f.op_str


def fields(insn):
    return insn.fields


class _Disassembler:
    def disasm(self, code, address, count=0):
        code, pos, n = bytes(code), 0, 0
        while pos + 2 <= len(code):
            f = D.decode(code[pos:pos + 8], address + pos)
            if f is None:
                return
            yield Insn(address + pos, code[pos:pos + f.size], f)
            pos += f.size
            n += 1
            if count and n >= count:
                return


def _reg(op):
    """The register an operand names, or None for an immediate."""
    return op[1] if op[0] == "r" else None


class Arc:
    """The ARC layer. Constructed from [arch]: isa ("arc600"/"arc700"), endian, ghidra."""

    name = "arc"
    isa_bit = 0                 # no bit of a code address marks an instruction set
    max_insn = 8                # a 32-bit instruction with a 32-bit long immediate
    word = 4
    reg_bits = 32
    link_reg = "blink"          # bl and jl leave the return address in blink
    veneer_reg = "r12"
    default_args = 8
    default_result = ("r0", "r1")
    default_callee_saved = tuple("r%d" % i for i in range(13, 26)) + ("gp", "fp")   # (gp: the small
                                    # data's base, which no function changes)
    profile = None
    # Instructions that fill a gap and do nothing (dread.lift's entered_unseen): nop_s, nop
    padding = {"arc600": (b"\xe0\x78", b"\x4a\x26\x00\x70"), "arc700": (b"\xe0\x78", b"\x4a\x26\x00\x70")}

    def __init__(self, isa="arc600", endian="little", ghidra=None, cpu=None, extensions=None):
        if endian != "little":
            raise ValueError("only endian = \"little\" is supported")
        if isa not in ("arc600", "arc700"):
            raise ValueError("isa is arc600 or arc700, not %r" % (isa,))
        self.isa, self.endian, self.cpu = isa, endian, cpu
        self.ghidra = ghidra    # None: Ghidra (11.x, as installed here) has no ARCompact processor;
                                # [arch] ghidra names a language where one is added (dread discover
                                # finds the functions meanwhile)
        self.ghidra_cspec = "default"

    # ---- identity and registers ----

    def configure(self, cflags):
        pass

    def describe(self):
        return "arc (ARCompact, %s), 32-bit, little-endian" % self.isa

    def isas(self):
        return (self.isa,)

    def reg_name(self, name):
        """The tools' name of a register: rN, with gp, fp, sp, ilink1, ilink2, blink, lp_count and
        pcl for r26-r31, r60 and r63."""
        key = name.lower()
        if key not in NUMBER:
            raise ValueError("unknown ARC register %r" % name)
        return CANON[NUMBER[key]]

    def reg_id(self, name):
        """The engine's register id: the register number (dread.engines.arc)."""
        return NUMBER[name.lower()] if name.lower() in NUMBER else NUMBER[self.reg_name(name)]

    @staticmethod
    def _name(name):
        return CANON[NUMBER[name]] if name in NUMBER else name

    def arg_regs(self, n):
        return ["r%d" % i for i in range(min(n, 8))]

    def general_regs(self):
        return ["r%d" % i for i in range(26)] + ["gp", "fp"]

    # ---- decoding ----

    def min_insn(self, isa=None):
        return 2

    def insn_length(self, code, isa=None):
        """The instruction's length from its first bytes: 2 or 4, 6 for a 16-bit instruction and 8
        for a 32-bit one with a long immediate."""
        return D.length(code)

    def disassembler(self, isa=None):
        return _Disassembler()

    def decode(self, code, address, isa=None):
        f = D.decode(bytes(code[:8]), address)
        return Insn(address, code, f) if f is not None else None

    def pc_value(self, address, isa=None):
        """pc-relative forms (branches, ld [pcl, ...], add rN, pcl, ...) count from pcl, the
        instruction's address with its low two bits clear."""
        return address & ~3

    # ---- what an instruction does ----

    def delay_slot(self, insn):
        """Whether the instruction behind this one runs before it goes (.d), on both ways of a
        conditional branch."""
        return insn.fields.delay

    def likely(self, insn):
        return False

    def flow(self, insn):
        """(kind, target, falls), as Arm.flow documents; the delay slot behind a .d is not part of
        it (delay_slot). b and the 16-bit branches are jumps (conditional ones fall through), bl a
        call, br and bbit conditional jumps, j [blink] a return (a conditional one falls through),
        j.f [ilink1] and [ilink2] returns from an interrupt, j through another register an indirect
        jump, j to an immediate a jump, jl a call (through a register: icall); lp a conditional
        jump to the loop's end (its back edge: loop_body); brk and what is not decoded stop."""
        f = insn.fields
        op = f.op
        if op == "b":
            return "jump", f.target, bool(f.cond)
        if op == "bl":
            return "call", f.target, True
        if op in ("br", "bbit0", "bbit1"):
            return "jump", f.target, True
        if op == "lp":
            return "jump", f.target, True
        if op in ("j", "jl"):
            src = f.srcs[0]
            cond = bool(f.cond)
            if op == "jl":
                return ("call", src[1] & 0xFFFFFFFF, True) if src[0] == "i" else ("icall", None, True)
            if src[0] == "i":
                return "jump", src[1] & 0xFFFFFFFF, cond
            if src[1] == BLINK or (f.setf and src[1] in (29, 30)):
                return "return", None, cond
            return "ijump", CANON[src[1]], cond
        if op in ("brk", "unknown", "ext"):
            return "stop", None, False
        if op == "sleep":
            return "next", None, True
        return "next", None, True

    def target(self, insn):
        kind, target, _ = self.flow(insn)
        return target if kind in ("jump", "call") else None

    def is_call(self, insn):
        return self.flow(insn)[0] in ("call", "icall")

    def is_syscall(self, insn):
        return False

    def is_conditional(self, insn):
        """Whether the instruction runs only under a condition (cond != 0) other than a branch's:
        mov.eq, add.ne and kin, which keep their destination where the condition fails."""
        f = insn.fields
        return bool(f.cond) and f.op not in ("b", "bl", "j", "jl", "lp")

    def predicate(self, insn):
        f = insn.fields
        return D.CONDS.get(f.cond) if self.is_conditional(insn) else None

    COMPLEMENT = {"eq": "ne", "ne": "eq", "p": "n", "n": "p", "c": "nc", "nc": "c", "v": "nv", "nv": "v",
                  "gt": "le", "le": "gt", "ge": "lt", "lt": "ge", "hi": "ls", "ls": "hi"}

    @staticmethod
    def complement(predicate):
        return Arc.COMPLEMENT.get(predicate)

    def condition(self, insn):
        """The condition of a conditional branch, jump or instruction ("eq", "lt", ...), else None."""
        f = insn.fields
        if f.op in ("br",):
            return f.brcond
        return D.CONDS.get(f.cond) or None

    def switches_isa(self, insn):
        return False

    def link_pair(self, prev, insn, isa):
        return False

    def mem_access(self, insn):
        """(kind, width, base register or None, offset, index register or None, writeback) of a load
        or store (kind "load"/"store"), else None. A long-immediate base is base None, its address
        the offset plus the immediate (`literal`)."""
        f = insn.fields
        if f.op not in ("ld", "st"):
            return None
        base, off = f.srcs[0], f.srcs[1]
        return ("load" if f.op == "ld" else "store", f.width, _reg(base), off[1] if off[0] == "i" else 0,
                _reg(off), f.aa in (1, 2) and base[0] == "r")

    def reg_effects(self, insn):
        """(reads, writes) by the tools' names, the flags as "flags" (STATUS32's Z, N, C, V). A
        conditional instruction reads the flags and its destination (kept where the condition
        fails). A call writes blink and the registers a callee need not keep (r0-r12); a load or
        store with write-back (.aw, .ab) writes its base; push_s and pop_s read and write sp; lp
        writes nothing a register holds (LP_START and LP_END are auxiliary); mul64 writes mlo, mmid
        and mhi (r57-r59)."""
        f = insn.fields
        op = f.op
        reads, writes = set(), set()
        for s in f.srcs:
            if s[0] == "r" and s[1] not in (LIMM, 63):
                reads.add(CANON[s[1]])
        if f.dst is not None and f.dst not in (LIMM, 63):
            writes.add(CANON[f.dst])
        if op in ("ld", "st") and f.aa in (1, 2) and f.srcs[0][0] == "r":
            writes.add(CANON[f.srcs[0][1]])
        if op == "pop":
            reads.add("sp")
            writes.add("sp")
        elif op == "push":
            reads.add("sp")
            writes.add("sp")
        elif op in ("bl", "jl"):
            writes |= {"blink", "flags"} | {"r%d" % i for i in range(13)}
        elif op in ("mul64", "mulu64"):
            writes |= {"r57", "r58", "r59"}
        elif op == "lr" and f.aux == ("i", D.AUX_NUMBERS["status32"]):
            reads.add("flags")
        elif op == "flag":
            writes.add("flags")
        if op in ("adc", "sbc", "rrc", "rlc"):
            reads.add("flags")
        if f.cond:
            reads.add("flags")
            if f.dst is not None and op not in ("b", "bl", "j", "jl", "lp") and f.dst not in (LIMM, 63):
                reads.add(CANON[f.dst])
        if f.setf:
            writes.add("flags")
        return reads, writes

    def sets_flags(self, insn):
        return insn.fields.setf or insn.fields.op == "flag"

    def stack_kind(self, insn):
        """"push" for push_s and st.aw rN,[sp,-4]; "pop" for pop_s and ld.ab rN,[sp,4]; else None."""
        f = insn.fields
        if f.op in ("push", "pop"):
            return f.op
        if f.op in ("st", "ld") and f.srcs[0] == ("r", 28) and f.width == 4:
            off = f.srcs[1]
            if f.op == "st" and f.aa == 1 and off == ("i", -4):
                return "push"
            if f.op == "ld" and f.aa == 2 and off == ("i", 4):
                return "pop"
        return None

    def literal(self, insn, isa=None):
        """(address, size) of a load from a fixed address: ld [pcl, off] (a literal pool) or a long
        immediate base; else None."""
        f = insn.fields
        if f.op != "ld" or f.srcs[1][0] != "i":
            return None
        base, off = f.srcs
        if base == ("r", 63):
            return ((insn.address & ~3) + off[1]) & 0xFFFFFFFF, f.width
        if base[0] == "i":
            return (base[1] + off[1]) & 0xFFFFFFFF, f.width
        return None

    def pc_address(self, insn, isa=None):
        """The address add rN, pcl, K makes (code taking its own position), else None."""
        f = insn.fields
        if f.op == "add" and len(f.srcs) == 2 and f.srcs[0] == ("r", 63) and f.srcs[1][0] == "i":
            return ((insn.address & ~3) + f.srcs[1][1]) & 0xFFFFFFFF
        return None

    def immediates(self, insn):
        """The immediates an instruction carries (its long immediate and the constants of its
        operands): candidate input values for the case generator."""
        f = insn.fields
        out = [s[1] & 0xFFFFFFFF for s in f.srcs if s[0] == "i" and f.op not in ("ld", "st")]
        if f.limm is not None and (f.limm & 0xFFFFFFFF) not in out:
            out.append(f.limm & 0xFFFFFFFF)
        return out

    def bound_check(self, insn, prior=(), known=None):
        """(register, limit, before) when the instruction bounds a switch index (dread.switches):
        brhs/brlo rN, K (GCC's: the index below K goes on to the table; set before the branch),
        cmp rN, K (set before it: a bhi or bls behind it takes the way out), bmsk rN, rM, k (rN
        takes 0 .. 2^(k+1) - 1) and and rN, rM, 2^n - 1 (set after them)."""
        f = insn.fields
        srcs = f.srcs
        if f.op == "br" and f.brcond in ("hs", "lo") and srcs[0][0] == "r" and srcs[1][0] == "i":
            k = srcs[1][1] & 0xFFFFFFFF
            if 0 < k <= 0x10000:
                return CANON[srcs[0][1]], k, True
        if f.op == "cmp" and not f.cond and srcs[0][0] == "r" and srcs[1][0] == "i":
            k = srcs[1][1] & 0xFFFFFFFF
            if 0 <= k < 0x10000:
                return CANON[srcs[0][1]], k, True
        if f.op in ("bmsk", "and") and not f.cond and f.dst is not None and f.dst < 61 and srcs[1][0] == "i":
            k = srcs[1][1] & 0xFFFFFFFF
            limit = (2 << (k & 31)) - 1 if f.op == "bmsk" else k
            if 0 < limit < 0x10000 and limit & (limit + 1) == 0:
                return CANON[f.dst], limit, False
        return None

    def switch_targets(self, insn, program, inside):
        return None

    VECTORS = 32                # ARC600 at most: reset, memory error, instruction error, lines 3-31

    def vector_entries(self, read, vectors):
        """[(index, address, "reset" or "handler")] of the interrupt vector table at `vectors` (its
        base: 0 out of reset): 8 bytes a vector, each a jump with a long immediate (j limm), vector
        0 the reset. The table ends at the first slot that holds anything else (a core has as many
        vectors as it was configured with, up to VECTORS: a private ARC600 firmware's table has
        fewer, and code follows it). read(address, n) gives the bytes loaded there
        (dread.discover)."""
        out = []
        for i in range(self.VECTORS):
            at = vectors + 8 * i
            f = D.decode(read(at, 8), at)
            if f is None or f.op != "j" or f.cond or f.limm is None or f.srcs[0][0] != "i":
                break
            out.append((i, f.srcs[0][1] & 0xFFFFFFFE, "reset" if i == 0 else "handler"))
        return out

    def loop_body(self, insn):
        """(start, end) of the zero-overhead loop an lp instruction sets up: the instructions from
        behind it up to, not including, its target. None for anything else."""
        f = insn.fields
        if f.op != "lp":
            return None
        return insn.address + insn.size, f.target

    # ---- what the case generator learns from the code (dread.autocase) ----

    generator_version = 2       # in the generated-case cache's key: bump when these change

    def switch_hints(self, insns):
        limits, table = set(), False
        for ins in insns:
            f = ins.fields
            if f.op in ("cmp", "br") and len(f.srcs) == 2 and f.srcs[1][0] == "i" and 0 < f.srcs[1][1] <= 0xFF:
                limits.add(f.srcs[1][1])
            if self.flow(ins)[0] == "ijump":
                table = True
        return limits, table

    def compare_chars(self, insn):
        f = insn.fields
        if f.op in ("cmp", "br", "sub") and len(f.srcs) == 2 and f.srcs[1][0] == "i":
            return [f.srcs[1][1] & 0xFFFFFFFF]
        return []

    def branch_deciders(self, insns, returns=False):
        """{conditional branch: (address of what decides it, first, second, imm, kind, sign)}, as
        Arm.branch_deciders documents (first and second the engine's register ids: the register
        numbers). br and brcc_s compare their two operands themselves ("compare"), bbit0 and bbit1
        test a bit of theirs ("test" with the bit as imm, where the bit is a constant); a conditional
        b (and with returns a conditional return: jeq_s [blink], j.cc [blink]) is decided by the
        flag setter in front of it, at most 16 bytes before with nothing between that sets the flags
        or leaves the straight run: cmp, cmp_s, sub.f ("compare"), rcmp (the operands the other way
        round), tst, tst_s, and.f, btst, btst_s ("test"), add.f with a constant (compared with its
        negation: sign -1)."""
        out = {}
        for k, ins in enumerate(insns):
            f = ins.fields
            if f.op == "br" and f.srcs[0][0] == "r" and f.srcs[0][1] < 61:
                a, b = f.srcs
                if b[0] == "r" and b[1] < 61:
                    out[ins.address] = (ins.address, a[1], b[1], 0, "compare", 1)
                elif b[0] == "i":
                    out[ins.address] = (ins.address, a[1], None, b[1] & 0xFFFFFFFF, "compare", 1)
                continue
            if f.op in ("bbit0", "bbit1") and f.srcs[0][0] == "r" and f.srcs[0][1] < 61 and f.srcs[1][0] == "i":
                out[ins.address] = (ins.address, f.srcs[0][1], None, 1 << (f.srcs[1][1] & 31), "test", 1)
                continue
            branch = f.op == "b" and f.cond and f.cond < 0x10
            back = returns and f.op == "j" and f.cond and f.cond < 0x10 and self.flow(ins)[0] == "return"
            if not (branch or back):
                continue
            found = self._setter_before(insns, k)
            if found is not None:
                out[ins.address] = found
        return out

    def _setter_before(self, insns, k):
        at = insns[k].address
        for j in range(k - 1, -1, -1):
            p = insns[j]
            if at - p.address > 16 or p.address + p.size != insns[j + 1].address:
                return None
            f = p.fields
            if self.flow(p)[0] != "next":
                return None
            if not (f.setf or f.op == "flag"):
                continue
            if f.cond or len(f.srcs) != 2 or f.srcs[0][0] != "r" or f.srcs[0][1] >= 61:
                return None
            a, b = f.srcs
            if f.op in ("cmp", "sub", "rcmp"):
                kind, sign = "compare", 1
            elif f.op in ("tst", "and", "btst"):
                kind, sign = "test", 1
            elif f.op == "add" and b[0] == "i":
                return p.address, a[1], None, b[1] & 0xFFFFFFFF, "compare", -1
            else:
                return None
            if b[0] == "r" and b[1] < 61:
                if f.op == "rcmp":
                    return p.address, b[1], a[1], 0, kind, sign
                return p.address, a[1], b[1], 0, kind, sign
            if b[0] == "i":
                imm = (1 << (b[1] & 31)) if f.op == "btst" else b[1] & 0xFFFFFFFF
                if f.op == "rcmp":
                    return None
                return p.address, a[1], None, imm, kind, sign
            return None
        return None

    def prologue_frame(self, program, fn, look=32):
        """The stack a function's prologue reserves for locals: sub sp,sp,N (and sub_s sp,sp,u7)
        behind the pushes of the registers it keeps."""
        total = 0
        for a in fn.insns[:look]:
            insn = program.insn(a)
            if insn is None or self.flow(insn)[0] != "next":
                break
            f = insn.fields
            if f.op == "sub" and f.dst == 28 and f.srcs[0] == ("r", 28) and f.srcs[1][0] == "i" and not f.cond:
                total += f.srcs[1][1]
        return total

    # ---- placing C ----

    def branch(self, src, dst, isa=None, link=False):
        """b s25 (bl s25 with link) at `src` to `dst`, 4 bytes: it reaches 16 MiB either way from
        src's pcl. Raises ValueError farther (far_branch: j limm, 8 bytes)."""
        disp = dst - (src & ~3)
        if dst % 2 or (link and dst % 4):
            raise ValueError("no ARC branch from 0x%x to 0x%x (alignment)" % (src, dst))
        if not -(1 << 24) <= disp < (1 << 24):
            raise ValueError("no ARC branch from 0x%x to 0x%x (b reaches 16 MiB)" % (src, dst))
        d = disp & 0x1FFFFFF
        if link:
            w = (1 << 27) | ((d >> 2) & 0x1FF) << 18 | 1 << 17 | ((d >> 11) & 0x3FF) << 6 | (d >> 21) & 0xF
        else:
            w = ((d >> 1) & 0x3FF) << 17 | 1 << 16 | ((d >> 11) & 0x3FF) << 6 | (d >> 21) & 0xF
        return struct.pack("<HH", w >> 16, w & 0xFFFF)

    def far_branch(self, src, dst, isa=None, link=False):
        """j limm (jl limm with link), 8 bytes, anywhere."""
        w = 0x20200F80 if not link else 0x20220F80
        return struct.pack("<HHHH", w >> 16, w & 0xFFFF, (dst >> 16) & 0xFFFF, dst & 0xFFFF)

    def branch_length(self, isa=None, src=None, span=None):
        if src is not None and span is not None and max(span[1] - src, src - span[0]) >= 1 << 24:
            return 8
        return 4

    def entry_branch(self, src, dst, isa=None, room=8):
        try:
            return self.branch(src, dst, isa)
        except ValueError:
            if room < 8:
                raise
            return self.far_branch(src, dst, isa)

    def start_address(self, address, isa=None):
        return address

    def isa_of_symbol(self, value):
        return self.isa

    @staticmethod
    def symbol_asm(name, address, isa):
        return "\t.global %s\n\t.type %s, @function\n\t.set %s, 0x%x\n" % (name, name, name, address)

    # libgcc's arithmetic helpers, which GCC calls for ARC600 code without a multiplier or a
    # divider (and for 64-bit operations): compiled C calls them where its compiler chose, so the C
    # and the original's code call them differently
    RUNTIME_HELPERS = frozenset("__" + n for n in (
        "mulsi3 muldi3 umulsidi3 mulsidi3 udivsi3 umodsi3 divsi3 modsi3 udivmodsi4 divdi3 udivdi3 "
        "moddi3 umoddi3 udivmoddi4 ashldi3 ashrdi3 lshrdi3 clzsi2 ctzsi2 ffssi2 cmpdi2 ucmpdi2").split())

    def runtime_helper(self, name):
        """Whether `name` is one of libgcc's arithmetic helpers (RUNTIME_HELPERS): dread.sysrun gives
        them no time, as the MSP430's."""
        return name in self.RUNTIME_HELPERS

    def fixed_from_symbols(self, symbols):
        """The registers that keep one value (dread.target.Image.fixed) from an ELF's symbols: gp is
        __SDATA_BEGIN__, where GCC's small data (-G, 4 bytes by default) begins and from which its
        code addresses it (the start-up sets gp so; not confirmed for every ARC toolchain)."""
        value = symbols.get("__SDATA_BEGIN__")
        return {"gp": value} if value is not None else {}

    def assembler_flags(self, compiler):
        return ["-mcpu=%s" % self.isa]

    def asm(self):
        return ArcAsm(self)

    # ---- the engine (dread.engines.arc: Unicorn has no ARC) ----

    def uc_mode(self, isa=None):
        return None

    def new_uc(self, isa=None):
        from dread.engines.arc import ArcEngine
        return ArcEngine(self.isa)

    def initial_status(self, engine):
        """STATUS32 clear (no flags, interrupts off, running) and no zero-overhead loop set up."""
        engine.aux_write(AUX_STATUS32, 0)
        engine.reset_loop()

    def status_reg(self):
        return 65

    def settle_it(self, engine):
        pass

    def flags_value(self, engine):
        return engine.aux_read(AUX_STATUS32) & (FLAG_Z | FLAG_N | FLAG_C | FLAG_V)

    def pc_reg(self):
        return 64

    def resume_address(self, engine):
        """Where a stopped run goes on: pc."""
        return engine.reg_read(64)

    # ---- the system run (dread.sysrun) ----
    # The ARC600's interrupts as far as they are known here (the ARCompact programmer's reference;
    # not confirmed on a core): vectors at the interrupt vector base, 8 bytes each (0 reset, 1 memory
    # error, 2 instruction error, 3-31 the interrupt lines); a line is level 1 unless its bit in
    # AUX_IRQ_LEV (0x200) is set (level 2); entry saves the return address in ilink1 (level 2:
    # ilink2) and STATUS32 in STATUS32_L1 (_L2) and clears E1 (level 2: E1 and E2); j.f [ilink1]
    # returns (in the engine).

    FIRST_LINE = 3                          # the vector of interrupt line 0 (a controller's numbers)
    AUX_IRQ_LEV = 0x200

    def _vector_target(self, engine, at):
        """The destination of the j limm in the vector slot at `at`, else the slot itself."""
        try:
            code = bytes(engine.mem_read(at, 8))
        except Exception:
            return at
        f = D.decode(code, at)
        if f is not None and f.op == "j" and not f.cond and f.srcs[0][0] == "i":
            return f.srcs[0][1] & 0xFFFFFFFE
        return at

    def reset(self, engine, read_word, entry, vectors):
        """As the core comes out of reset: STATUS32 clear (interrupts off), no zero-overhead loop,
        on at the reset vector's destination (its slot holds j _start)."""
        engine.aux_write(AUX_STATUS32, 0)
        engine.reset_loop()
        return self._vector_target(engine, vectors)

    def interrupts_enabled(self, engine):
        """STATUS32.E1: a level-1 interrupt may be taken (level 2 needs E2; gizmo-arc has none)."""
        return bool(engine.reg_read(65) & STATUS32_E1)

    def system_exception(self, intno):
        """The engine raises no exception of the core's (brk ends a run with an error)."""
        return None

    def enter_interrupt(self, engine, number, resume, vectors, read_word, write_word, cause=None):
        """Take interrupt line `number` (vector FIRST_LINE + number; a controller's core cause is
        the vector itself) as the core does (the comment above): ilink1 and STATUS32_L1, E1 off,
        on at the handler the vector's j limm names."""
        vector = cause if cause is not None else self.FIRST_LINE + number
        level2 = bool((engine.aux_read(self.AUX_IRQ_LEV) >> vector) & 1)
        status = engine.aux_read(AUX_STATUS32)
        engine.aux_write(AUX_STATUS32_L2 if level2 else AUX_STATUS32_L1, status)
        engine.reg_write(30 if level2 else 29, resume)
        engine.aux_write(AUX_STATUS32, status & ~(STATUS32_E1 | STATUS32_E2 if level2 else STATUS32_E1))
        return self._vector_target(engine, vectors + 8 * vector)

    def exception_return(self, engine, read_word):
        """Nothing to finish: j.f [ilink1] runs in the engine."""
        return None


class ArcAsm:
    """How dread asm writes ARC code: objdump's text, which GNU as (arc-elf32-as -mcpu=arc600)
    reads back; branches, br, bbit, bl and lp go to labels. What as encodes otherwise (an
    immediate it fits in another operand format, an extension condition objdump does not name) the
    verify loop writes raw: halfwords in their order in memory."""

    comment = ";"
    directives = {"word": ".word", "half": ".short", "byte": ".byte", "text": ".asciz"}

    def __init__(self, arch):
        self.arch = arch

    def prelude(self, isa):
        return []

    def switch(self, isa):
        return []

    def raw(self, b, isa):
        return ".short", ", ".join("0x%04x" % w for w in struct.unpack("<%dH" % (len(b) // 2), b))

    def function_lines(self, label, isa, code):
        return ["\t.type   %s, @function" % label]

    def operands(self, listing, insn, run, st, isa):
        f = insn.fields
        if f.op == "unknown" or f.op == "ext":
            return None
        if f.target is not None:
            tst = listing.prog.stored(f.target)
            if listing.space(f.target) == listing.space(run) and listing.has(tst, 2) and listing.labelable(tst):
                listing.labels.add(tst)
                head = insn.op_str.rsplit(",", 1)[0] + "," if "," in insn.op_str else ""
                return insn.mnemonic, ("label", head, tst), ""
            return None
        comment = ""
        lit = self.arch.literal(insn)
        if lit is not None:
            comment = "[0x%x]" % lit[0]
        return insn.mnemonic, insn.op_str, comment
