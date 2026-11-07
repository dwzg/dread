"""The ARC engine: an interpreter of ARCompact (ARC600) behind the engine contract (dread.engines),
which the layer's new_uc() returns. Register ids: 0-63 the core registers (r63 pcl reads the
instruction's address with its low two bits clear and ignores writes, r61 and r62 read 0), 64 pc,
65 STATUS32 (also aux_read(0xa)). Auxiliary registers: aux_read and aux_write, by number.

Memory is sparse (a dict of 4 KiB pages): the address space has 32 bits, which the base class's
flat array cannot hold. Accesses are little-endian and may be unaligned (not confirmed for the
ARC600, whose memory system may fault on them; GCC's code makes none).

The semantics (the ARCompact programmer's reference as far as it is known here; what GCC's code
relies on is checked by tests/test_engine_arc.py, which runs C compiled for the ARC600 here and on
the host and compares the results):
- Conditional instructions (.eq and kin) do nothing where the condition fails. Flags are written
  only with .f, by the compares (cmp, tst, rcmp, btst and their 16-bit forms) and by flag.
- add, adc, sub, sbc, rsub, cmp, rcmp and the scaled forms (add1-3, sub1-3: the second operand
  shifted by 1-3 first) set Z, N, C (the carry; a borrow for the subtractions) and V; the logical
  operations, mov, the bit operations and the extensions set Z and N; asl, asr, lsr, ror, rrc and
  rlc by one bit set Z, N and C (asl also V: the sign changed). The barrel shifter's asl, lsr, asr
  and ror by a register set Z and N, and C to the last bit shifted out where the count is not 0
  (not confirmed). max and min set the flags of b - c, C where c was chosen (not confirmed). abs
  sets Z and N of the result, C to the source's sign and V where the source was 0x80000000 (not
  confirmed); norm, swap, and the saturating operations set Z and N (not confirmed).
- Loads and stores: .aw adds the offset to the base before the access and writes it back, .ab
  after the access, .as scales the offset by the access's size (bytes unscaled); a destination that
  is also a written-back base takes the loaded value (not confirmed). A long-immediate or pcl base
  is not written back.
- A branch, jump or call written .d runs the instruction behind it first (on both ways of a
  conditional branch); bl.d and jl.d leave the address behind that slot in blink. The slot ends
  the block with its branch. A run that ends between the two (its count) keeps the branch: the next
  run that starts at the slot goes to the branch's destination behind it.
- The zero-overhead loop: lp sets LP_START (the address behind it) and LP_END (its target); an lp
  whose condition fails jumps to LP_END. Where an instruction that does not branch is followed by
  LP_END and STATUS32's L bit (12) is clear, lp_count is counted down and, unless it is then 0,
  execution goes on at LP_START. A branch to LP_END leaves the loop. initial state: no loop (LP_END
  unset), as the layer's initial_status leaves it for every call.
- j.f [ilink1] (and ilink2) returns from an interrupt: STATUS32 from STATUS32_L1 (_L2).
- flag sets STATUS32's Z, N, C, V (bits 11-8), E1, E2 (bits 2, 1) and H (bit 0): H halts the core,
  which ends the run behind the instruction with `halted` set; sleep ends it with `sleeping` set.
- mul64 and mulu64 write the 64-bit product's low word to mlo (r57), its middle (bits 47-16) to
  mmid (r58) and its high word to mhi (r59) and the aux register mulhi (0x12).
- brk ends the run with UC_ERR_EXCEPTION; what is not decoded, the extension instructions the
  engine has no semantics for (major 0x05's DSP operations, divaw, the dual 16-bit ones) and the
  extension conditions fault with UC_ERR_INSN_INVALID when they would run.
"""
from unicorn import UcError
import unicorn.unicorn_const as C

from dread.arch import arc_decode as D
from dread.engines import Engine, Fault, PAGE, PAGE_BITS

M = 0xFFFFFFFF
PC_ID, STATUS32_ID = 64, 65
AUX_STATUS32, AUX_STATUS32_L1, AUX_STATUS32_L2, AUX_LP_START, AUX_LP_END, AUX_MULHI = 0x0A, 0x0B, 0x0C, 2, 3, 0x12
FLAG_BITS = 0xF00               # Z N C V: bits 11-8
L_BIT = 1 << 12
BLOCK_LIMIT = 128
WIDE = 1 << 16                  # a hook spanning more pages than this is checked at every access

# the conditions on the flags list fl = [Z, N, C, V]
COND_EXPR = {1: "fl[0]", 2: "not fl[0]", 3: "not fl[1]", 4: "fl[1]", 5: "fl[2]", 6: "not fl[2]", 7: "fl[3]",
             8: "not fl[3]", 9: "(fl[1] == fl[3] and not fl[0])", 10: "fl[1] == fl[3]", 11: "fl[1] != fl[3]",
             12: "(fl[0] or fl[1] != fl[3])", 13: "(not fl[2] and not fl[0])", 14: "(fl[2] or fl[0])",
             15: "(not fl[1] and not fl[0])"}
SUPPORTED_EXT = {"asl", "lsr", "asr", "ror", "adds", "subs", "asls", "asrs", "swap", "norm", "normw", "sat16",
                 "rnd16", "abss", "abssw", "negs", "negsw"}


def _s32(v):
    return v - 0x100000000 if v & 0x80000000 else v


def _sat(v):
    return max(-0x80000000, min(0x7FFFFFFF, v)) & M


def _norm(v, bits=32):
    sign = (v >> (bits - 1)) & 1
    x = (~v if sign else v) & ((1 << bits) - 1)
    return (bits - 1) - x.bit_length() if x else bits - 1


class ArcEngine(Engine):
    address_bits = 32

    def __init__(self, isa="arc600"):
        self.isa = isa
        self.r = [0] * 64
        self.fl = [0, 0, 0, 0]              # Z N C V
        self.status = 0                     # STATUS32 without its flags
        self.aux = {}
        self.lp_start, self.lp_end = 0, None
        self.pcv = 0
        self.bta = None                     # a taken .d branch's destination, until its slot has run
        self.pending = None                 # (slot, destination) of a run that ended in front of a slot
        self.cache = {}                     # address -> (Fields, size, ends a block, compiled, slot size)
        self.blocks = {}
        self.marks = {}                     # page -> bytearray: bytes of cached instructions
        self.sleeping = self.halted = False
        self.pmem, self.pperm = {}, {}
        self.hooks = {}
        self.next_handle = 1
        self.stop_request = False
        self.until = None
        self.stall, self.stalled = 0, False
        self.rebuild_hooks()

    # ---- registers ----

    @property
    def pc(self):
        return self.pcv

    @pc.setter
    def pc(self, value):
        self.pcv = value & 0xFFFFFFFE

    def reg_read(self, rid):
        if 0 <= rid < 61:
            return self.r[rid]
        if rid in (61, 62):
            return 0
        if rid == 63:
            return self.pcv & ~3
        if rid == PC_ID:
            return self.pcv
        if rid == STATUS32_ID:
            return self.aux_read(AUX_STATUS32)
        raise UcError(C.UC_ERR_ARG)

    def reg_write(self, rid, value):
        value &= M
        if 0 <= rid < 61:
            self.r[rid] = value
        elif rid in (61, 62, 63):
            pass
        elif rid == PC_ID:
            self.pc = value
        elif rid == STATUS32_ID:
            self.aux_write(AUX_STATUS32, value)
        else:
            raise UcError(C.UC_ERR_ARG)

    def aux_read(self, n):
        n &= M
        if n == AUX_STATUS32:
            fl = self.fl
            return self.status | fl[0] << 11 | fl[1] << 10 | fl[2] << 9 | fl[3] << 8
        if n == AUX_LP_START:
            return self.lp_start
        if n == AUX_LP_END:
            return self.lp_end or 0
        return self.aux.get(n, 0)

    def aux_write(self, n, value):
        n, value = n & M, value & M
        if n == AUX_STATUS32:
            self.fl[:] = [(value >> 11) & 1, (value >> 10) & 1, (value >> 9) & 1, (value >> 8) & 1]
            self.status = value & ~FLAG_BITS
        elif n == AUX_LP_START:
            self.lp_start = value & ~1
        elif n == AUX_LP_END:
            self.lp_end = value & ~1
        else:
            self.aux[n] = value

    def reset_loop(self):
        """No zero-overhead loop set up (LP_END unset): the state a called function finds."""
        self.lp_start, self.lp_end = 0, None

    # ---- memory: sparse pages ----

    def _pages_of(self, address, size):
        if address % PAGE or size % PAGE or size <= 0 or address < 0 or address + size > 1 << 32:
            raise UcError(C.UC_ERR_ARG)
        return range(address >> PAGE_BITS, (address + size) >> PAGE_BITS)

    def mem_map(self, address, size, perms=C.UC_PROT_ALL):
        pages = self._pages_of(address, size)
        if any(p in self.pperm for p in pages):
            raise UcError(C.UC_ERR_MAP)
        for p in pages:
            self.pperm[p] = perms
            self.pmem[p] = bytearray(PAGE)
        self.flush()

    def mem_unmap(self, address, size):
        pages = self._pages_of(address, size)
        if not all(p in self.pperm for p in pages):
            raise UcError(C.UC_ERR_NOMEM)
        for p in pages:
            del self.pperm[p], self.pmem[p]
        self.flush()

    def mem_protect(self, address, size, perms):
        pages = self._pages_of(address, size)
        if not all(p in self.pperm for p in pages):
            raise UcError(C.UC_ERR_NOMEM)
        for p in pages:
            self.pperm[p] = perms
        self.flush()

    def _check(self, address, size, error):
        if address < 0 or address + size > 1 << 32:
            raise UcError(error)
        for p in range(address >> PAGE_BITS, ((address + size - 1) >> PAGE_BITS) + 1):
            if p not in self.pperm:
                raise UcError(error)

    def _raw_read(self, address, size):
        out = bytearray()
        while size:
            p, o = address >> PAGE_BITS, address & (PAGE - 1)
            n = min(size, PAGE - o)
            out += self.pmem[p][o:o + n]
            address, size = address + n, size - n
        return out

    def _raw_write(self, address, data):
        pos = 0
        while pos < len(data):
            p, o = address >> PAGE_BITS, address & (PAGE - 1)
            n = min(len(data) - pos, PAGE - o)
            marks = self.marks.get(p)
            if marks is not None and marks.find(1, o, o + n) >= 0:
                m = self.pmem[p]
                if any(marks[o + i] and m[o + i] != data[pos + i] for i in range(n)):
                    self.flush()
            self.pmem[p][o:o + n] = data[pos:pos + n]
            address, pos = address + n, pos + n

    def mem_read(self, address, size):
        if size <= 0:
            return bytearray()
        self._check(address, size, C.UC_ERR_READ_UNMAPPED)
        return self._raw_read(address, size)

    def mem_write(self, address, data):
        data = bytes(data)
        if not data:
            return
        self._check(address, len(data), C.UC_ERR_WRITE_UNMAPPED)
        self._raw_write(address, data)

    def regions(self):
        out = []
        for p in sorted(self.pperm):
            perm = self.pperm[p] & 7
            if out and out[-1][1] + 1 == p << PAGE_BITS and out[-1][2] == perm:
                out[-1] = (out[-1][0], ((p + 1) << PAGE_BITS) - 1, perm)
            else:
                out.append((p << PAGE_BITS, ((p + 1) << PAGE_BITS) - 1, perm))
        return out

    mem_regions = regions

    def rebuild_hooks(self):
        """As the base class's, with the memory hooks' filter a set of pages (None: every page)."""
        self.hooks_stale = False
        self.by_kind = {}
        for kinds, callback, user, begin, end in self.hooks.values():
            for kind in kinds:
                self.by_kind.setdefault(kind, []).append((begin, end, callback, user))
        code = self.by_kind.get("code", [])
        self.code_hooks = code
        self.code_everywhere = any(b > e or e - b >= 256 for b, e, _, _ in code)
        self.code_at = {a for b, e, _, _ in code if b <= e and e - b < 256 for a in range(b, e + 1)}
        self.block_hooks = self.by_kind.get("block", [])
        for kind in ("read", "write"):
            hooks = self.by_kind.get(kind, [])
            pages = set()
            for b, e, _, _ in hooks:
                if b > e or (e >> PAGE_BITS) - (b >> PAGE_BITS) > WIDE:
                    pages = None
                    break
                pages.update(range(b >> PAGE_BITS, (e >> PAGE_BITS) + 1))
            setattr(self, kind + "_hooks", hooks)
            setattr(self, kind + "_pages", pages if hooks else set())

    def load(self, address, size):
        """A data load of 1, 2 or 4 bytes (little-endian) with its hooks and checks."""
        p = address >> PAGE_BITS
        if p not in self.pperm:
            self.invalid(C.UC_MEM_READ_UNMAPPED, address, size)
            if p not in self.pperm:
                raise Fault(C.UC_ERR_READ_UNMAPPED)
        rp = self.read_pages
        if rp is None or p in rp:
            self.run_mem_hooks(self.read_hooks, C.UC_MEM_READ, address, size, 0)
        if not self.pperm[p] & C.UC_PROT_READ:
            self.invalid(C.UC_MEM_READ_PROT, address, size)
        o = address & (PAGE - 1)
        if o + size <= PAGE:
            m = self.pmem[p]
            if size == 4:
                return m[o] | m[o + 1] << 8 | m[o + 2] << 16 | m[o + 3] << 24
            if size == 1:
                return m[o]
            return m[o] | m[o + 1] << 8
        if (p + 1) & 0xFFFFF not in self.pperm:
            self.invalid(C.UC_MEM_READ_UNMAPPED, (address + size - 1) & M, size)
            if (p + 1) & 0xFFFFF not in self.pperm:
                raise Fault(C.UC_ERR_READ_UNMAPPED)
        return int.from_bytes(self._raw_read(address, size), "little")

    def store(self, address, size, value):
        """A data store of 1, 2 or 4 bytes (little-endian) with its hooks and checks."""
        p = address >> PAGE_BITS
        wp = self.write_pages
        if wp is None or p in wp:
            self.run_mem_hooks(self.write_hooks, C.UC_MEM_WRITE, address, size, value)
        if p not in self.pperm:
            self.invalid(C.UC_MEM_WRITE_UNMAPPED, address, size, value)
            if p not in self.pperm:
                raise Fault(C.UC_ERR_WRITE_UNMAPPED)
        if not self.pperm[p] & C.UC_PROT_WRITE:
            self.invalid(C.UC_MEM_WRITE_PROT, address, size, value)
        o = address & (PAGE - 1)
        data = (value & ((1 << (8 * size)) - 1)).to_bytes(size, "little")
        if o + size <= PAGE and p not in self.marks:
            self.pmem[p][o:o + size] = data
            return
        if o + size > PAGE and (p + 1) & 0xFFFFF not in self.pperm:
            self.invalid(C.UC_MEM_WRITE_UNMAPPED, (address + size - 1) & M, size, value)
            if (p + 1) & 0xFFFFF not in self.pperm:
                raise Fault(C.UC_ERR_WRITE_UNMAPPED)
        self._raw_write(address, data)

    def fetch_check(self, address, size):
        for a in sorted({address, address + size - 1}):
            p = a >> PAGE_BITS
            if p not in self.pperm:
                self.invalid(C.UC_MEM_FETCH_UNMAPPED, a, size)
                if p not in self.pperm:
                    raise Fault(C.UC_ERR_FETCH_UNMAPPED)
            if not self.pperm[p] & C.UC_PROT_EXEC:
                self.invalid(C.UC_MEM_FETCH_PROT, a, size)

    # ---- decoding ----

    def flush(self):
        self.cache.clear()
        self.blocks.clear()
        self.marks.clear()

    def _bytes_at(self, pc, n):
        """Up to n code bytes at pc from mapped executable pages, without hooks."""
        out = bytearray()
        a = pc
        while len(out) < n:
            p = a >> PAGE_BITS
            if p not in self.pperm or not self.pperm[p] & C.UC_PROT_EXEC:
                break
            o = a & (PAGE - 1)
            k = min(n - len(out), PAGE - o)
            out += self.pmem[p][o:o + k]
            a += k
        return bytes(out)

    def _mark(self, pc, size):
        for a in range(pc, pc + size):
            p = a >> PAGE_BITS
            m = self.marks.get(p)
            if m is None:
                m = self.marks[p] = bytearray(PAGE)
            m[a & (PAGE - 1)] = 1

    def _decoded(self, pc, f):
        slot = 0
        if f.delay:
            nf = D.decode(self._bytes_at(pc + f.size, 8), pc + f.size)
            slot = nf.size if nf is not None else 2
        entry = (f, f.size, self._ends_block(f), self._compile(f, pc, slot), slot)
        self.cache[pc] = entry
        self._mark(pc, f.size)
        return entry

    @staticmethod
    def _ends_block(f):
        return f.op in ("b", "bl", "br", "bbit0", "bbit1", "j", "jl", "lp", "sleep", "flag", "brk")

    def decode(self, pc):
        entry = self.cache.get(pc)
        if entry is not None:
            return entry
        self.fetch_check(pc, 2)
        code = self._bytes_at(pc, 8)
        size = D.length(code + b"\0" * 8)
        self.fetch_check(pc, size)
        f = D.decode(code, pc)
        if f is None:
            raise Fault(C.UC_ERR_FETCH_UNMAPPED)
        return self._decoded(pc, f)

    def _peek(self, pc):
        entry = self.cache.get(pc)
        if entry is not None:
            return entry
        code = self._bytes_at(pc, 8)
        if len(code) < 2:
            return None
        f = D.decode(code, pc)
        if f is None:
            return None
        return self._decoded(pc, f)

    def insn_size(self, pc):
        return self.decode(pc)[1]

    def block_size(self, pc, until):
        size = self.blocks.get(pc)
        if size is None:
            a, size = pc, 0
            for _ in range(BLOCK_LIMIT):
                entry = self._peek(a)
                if entry is None:
                    break
                size += entry[1]
                a += entry[1]
                if entry[2]:
                    size += entry[4]            # a delay slot ends the block with its branch
                    break
            self.blocks[pc] = size
        end = self.lp_end
        if end is not None and pc < end < pc + size:
            size = end - pc
        if until is not None and pc < until < pc + size:
            return until - pc
        return size

    # ---- running ----

    def emu_start(self, begin, until, timeout=0, count=0):
        """The base class's run loop (dread.engines.Engine.emu_start: keep the two alike), with the
        delay slots and the zero-overhead loop's back edge."""
        if self.hooks_stale:
            self.rebuild_hooks()
        self.sleeping = self.halted = False
        cache, r = self.cache, self.r
        self.pc = begin
        self.stop_request = False
        self.until = until
        n, block_start = 0, True
        stall, seen, left = self.stall, set(), self.stall
        self.stalled = False
        slot_target = None
        if self.pending is not None and self.pending[0] == self.pcv:    # (a run that ended between a
            slot_target, block_start = self.pending[1], False            # branch and its slot goes on)
        self.pending = None
        try:
            while True:
                pc = self.pcv
                if pc == until or (count and n >= count):
                    break
                if block_start and stall and pc not in seen:
                    seen.add(pc)
                    left = stall
                if block_start and self.block_hooks:
                    self.run_block_hooks(pc, self.block_size(pc, until))
                    if self.stop_request:
                        break
                    if self.pcv != pc:
                        continue
                block_start = False
                if self.code_everywhere or pc in self.code_at:
                    self.run_code_hooks(pc, self.insn_size(pc))
                    if self.stop_request:
                        break
                    if self.pcv != pc:
                        block_start = True
                        slot_target = None
                        continue
                entry = cache.get(pc)
                if entry is None:
                    entry = self.decode(pc)
                nxt = entry[3]()
                n += 1
                if slot_target is not None:         # this was a delay slot
                    nxt, slot_target, block_start = slot_target, None, True
                elif self.bta is not None:
                    slot_target, self.bta = self.bta, None
                else:
                    block_start = entry[2]
                    if nxt == self.lp_end and nxt == pc + entry[1] and not self.status & L_BIT:
                        r[60] = (r[60] - 1) & M
                        if r[60]:
                            nxt, block_start = self.lp_start, True
                        else:
                            block_start = True
                self.pcv = nxt
                if stall:
                    left -= 1
                    if left <= 0:
                        self.stalled = True
                        break
                if self.stop_request:
                    break
        except Fault as e:
            self.pcv = pc
            self.bta = None
            raise UcError(e.code)
        finally:
            self.until = None
            if slot_target is not None:
                self.pending = (self.pcv, slot_target)

    def step(self, pc):
        entry = self.cache.get(pc)
        if entry is None:
            entry = self.decode(pc)
        self.pcv = entry[3]()
        return entry[2]

    # ---- the instructions compiled ----
    # As the MSP430 engine does: each decoded instruction becomes a Python function written out with
    # its operation, registers and constants filled in. It returns the address execution goes on
    # at (behind it, or a branch's destination); a taken .d branch sets bta and returns the address
    # of its slot, and the run loop goes to bta behind the slot.

    def _compile(self, f, pc, slot):
        env = {"r": self.r, "fl": self.fl, "load": self.load, "store": self.store, "eng": self,
               "Fault": Fault, "C": C, "_s32": _s32, "_sat": _sat, "_norm": _norm}
        body = self._lines(f, pc, slot)
        exec("def run():\n    " + "\n    ".join(body), env)
        return env["run"]

    @staticmethod
    def _src(op, pc):
        if op[0] == "i":
            return "%d" % (op[1] & M)
        n = op[1]
        if n < 61:
            return "r[%d]" % n
        if n == 63:
            return "%d" % (pc & ~3)
        return "0"

    @staticmethod
    def _dst(n, expr):
        if n is None or n >= 61:
            return []
        return ["r[%d] = %s" % (n, expr)]

    @staticmethod
    def _zn(res="res"):
        return ["fl[0] = 0 if %s else 1" % res, "fl[1] = %s >> 31" % res]

    def _lines(self, f, pc, slot):
        op, nxt = f.op, (pc + f.size) & M
        if op in ("unknown", "ext") or f.cond >= 0x10:
            return ["raise Fault(C.UC_ERR_INSN_INVALID)"]
        lines = []
        if f.cond and op not in ("b", "bl", "j", "jl", "lp", "br"):
            lines.append("if not %s: return %d" % (COND_EXPR[f.cond], nxt))
        body = self._op_lines(f, op, pc, nxt, slot)
        if body is None:
            return ["raise Fault(C.UC_ERR_INSN_INVALID)"]
        return lines + body

    def _taken(self, f, target, nxt):
        """Lines that go to `target`: at once, or behind the delay slot."""
        if f.delay:
            return ["eng.bta = %s" % target, "return %d" % nxt]
        return ["return %s" % target]

    def _op_lines(self, f, op, pc, nxt, slot):
        S = lambda o: self._src(o, pc)
        if op == "nop":
            return ["return %d" % nxt]
        if op in ("add", "adc", "sub", "sbc", "rsub", "add1", "add2", "add3", "sub1", "sub2", "sub3",
                  "cmp", "rcmp"):
            a, b = S(f.srcs[0]), S(f.srcs[1])
            if op in ("rsub", "rcmp"):
                a, b = b, a
            k = {"add1": 1, "add2": 2, "add3": 3, "sub1": 1, "sub2": 2, "sub3": 3}.get(op, 0)
            lines = ["x = %s" % a, "y = %s" % (b if not k else "(%s << %d) & %d" % (b, k, M))]
            if op in ("add", "adc", "add1", "add2", "add3"):
                lines += ["t = x + y%s" % (" + fl[2]" if op == "adc" else ""), "res = t & %d" % M]
                flags = ["fl[2] = 1 if t > %d else 0" % M, "fl[3] = ((x ^ res) & (y ^ res)) >> 31"]
            else:
                borrow = " - fl[2]" if op == "sbc" else ""
                lines += ["res = (x - y%s) & %d" % (borrow, M)]
                flags = ["fl[2] = 1 if x < y%s else 0" % (" + fl[2]" if op == "sbc" else ""),
                         "fl[3] = ((x ^ y) & (x ^ res)) >> 31"]
            if op not in ("cmp", "rcmp"):
                lines += self._dst(f.dst, "res")
            if f.setf:
                lines += self._zn() + flags
            return lines + ["return %d" % nxt]
        if op in ("and", "or", "xor", "bic", "tst", "bset", "bclr", "bxor", "bmsk", "btst", "max", "min",
                  "mpyw", "mpyuw", "asl", "lsr", "asr", "ror", "adds", "subs", "asls", "asrs"):
            a, b = S(f.srcs[0]), S(f.srcs[1])
            expr = {"and": "x & y", "tst": "x & y", "or": "x | y", "xor": "x ^ y", "bic": "x & ~y & %d" % M,
                    "bset": "x | (1 << (y & 31))", "bclr": "x & ~(1 << (y & 31)) & %d" % M,
                    "bxor": "x ^ (1 << (y & 31))", "btst": "x & (1 << (y & 31))",
                    "bmsk": "x & ((2 << (y & 31)) - 1)",
                    "max": "x if _s32(x) >= _s32(y) else y", "min": "x if _s32(x) <= _s32(y) else y",
                    "mpyw": "((((x & 0xFFFF) ^ 0x8000) - 0x8000) * (((y & 0xFFFF) ^ 0x8000) - 0x8000)) & %d" % M,
                    "mpyuw": "((x & 0xFFFF) * (y & 0xFFFF)) & %d" % M,
                    "asl": "(x << (y & 31)) & %d" % M, "lsr": "x >> (y & 31)",
                    "asr": "(_s32(x) >> (y & 31)) & %d" % M,
                    "ror": "((x >> (y & 31)) | (x << (32 - (y & 31)))) & %d" % M,
                    "adds": "_sat(_s32(x) + _s32(y))", "subs": "_sat(_s32(x) - _s32(y))",
                    "asls": None, "asrs": None}[op]
            if expr is None:
                return None                     # asls, asrs: not run (their saturation is not known here)
            lines = ["x = %s" % a, "y = %s" % b, "res = %s" % expr]
            if op not in ("tst", "btst"):
                lines += self._dst(f.dst, "res")
            if f.setf:
                lines += self._zn()
                if op in ("max", "min"):
                    lines += ["d = (x - y) & %d" % M, "fl[0] = 0 if d else 1", "fl[1] = d >> 31",
                              "fl[3] = ((x ^ y) & (x ^ d)) >> 31", "fl[2] = 1 if res == y and x != y else 0"]
                elif op in ("asl", "lsr", "asr", "ror"):
                    out = {"asl": "(x >> (32 - (y & 31))) & 1", "lsr": "(x >> ((y & 31) - 1)) & 1",
                           "asr": "(x >> ((y & 31) - 1)) & 1", "ror": "(x >> ((y & 31) - 1)) & 1"}[op]
                    lines += ["if y & 31: fl[2] = %s" % out]
            return lines + ["return %d" % nxt]
        if op == "mov":
            lines = ["res = %s" % S(f.srcs[0])] + self._dst(f.dst, "res")
            if f.setf:
                lines += self._zn()
            return lines + ["return %d" % nxt]
        if op in ("asl1", "asr1", "lsr1", "ror1", "rrc", "rlc", "sexb", "sexw", "extb", "extw", "abs", "not",
                  "neg", "swap", "norm", "normw", "sat16", "rnd16", "abss", "abssw", "negs", "negsw"):
            lines = ["x = %s" % S(f.srcs[0])]
            expr = {"asl1": "(x << 1) & %d" % M, "asr1": "(x >> 1) | (x & 0x80000000)", "lsr1": "x >> 1",
                    "ror1": "(x >> 1) | ((x & 1) << 31)", "rrc": "(x >> 1) | (fl[2] << 31)",
                    "rlc": "((x << 1) & %d) | fl[2]" % M, "sexb": "(((x & 0xFF) ^ 0x80) - 0x80) & %d" % M,
                    "sexw": "(((x & 0xFFFF) ^ 0x8000) - 0x8000) & %d" % M, "extb": "x & 0xFF",
                    "extw": "x & 0xFFFF", "abs": "(-x) & %d if x & 0x80000000 else x" % M, "not": "~x & %d" % M,
                    "neg": "(-x) & %d" % M, "swap": "((x << 16) | (x >> 16)) & %d" % M, "norm": "_norm(x)",
                    "normw": "_norm(x & 0xFFFF, 16)", "sat16": None, "rnd16": None,
                    "abss": "_sat(abs(_s32(x)))", "abssw": None, "negs": "_sat(-_s32(x))", "negsw": None}[op]
            if expr is None:
                return None                     # not run: their exact results are not known here
            lines += ["res = %s" % expr] + self._dst(f.dst, "res")
            if f.setf:
                lines += self._zn()
                if op in ("asl1", "asr1", "lsr1", "ror1", "rrc"):
                    lines.append("fl[2] = x & 1" if op != "asl1" else "fl[2] = x >> 31")
                if op == "rlc":
                    lines.append("fl[2] = x >> 31")
                if op == "asl1":
                    lines.append("fl[3] = ((x ^ res) >> 31) & 1")
                if op == "abs":
                    lines += ["fl[2] = x >> 31", "fl[3] = 1 if x == 0x80000000 else 0"]
            return lines + ["return %d" % nxt]
        if op in ("mul64", "mulu64"):
            a, b = S(f.srcs[0]), S(f.srcs[1])
            prod = "_s32(%s) * _s32(%s)" % (a, b) if op == "mul64" else "%s * %s" % (a, b)
            return ["p = (%s) & 0xFFFFFFFFFFFFFFFF" % prod, "r[57] = p & %d" % M, "r[58] = (p >> 16) & %d" % M,
                    "r[59] = p >> 32", "eng.aux[%d] = r[59]" % AUX_MULHI, "return %d" % nxt]
        if op == "ld":
            return self._load_lines(f, pc, nxt)
        if op == "st":
            return self._store_lines(f, pc, nxt)
        if op == "push":
            return ["v = %s" % S(f.srcs[0]), "sp = (r[28] - 4) & %d" % M, "store(sp, 4, v)", "r[28] = sp",
                    "return %d" % nxt]
        if op == "pop":
            return ["v = load(r[28], 4)", "r[28] = (r[28] + 4) & %d" % M] + self._dst(f.dst, "v") + \
                ["return %d" % nxt]
        if op == "b":
            if f.cond:
                return ["if not %s: return %d" % (COND_EXPR[f.cond], nxt)] + self._taken(f, f.target, nxt)
            return self._taken(f, f.target, nxt)
        if op == "bl":
            back = (nxt + slot) & M if f.delay else nxt
            lines = ["if not %s: return %d" % (COND_EXPR[f.cond], nxt)] if f.cond else []
            return lines + ["r[31] = %d" % back] + self._taken(f, f.target, nxt)
        if op in ("br", "bbit0", "bbit1"):
            a, b = S(f.srcs[0]), S(f.srcs[1])
            test = {"eq": "x == y", "ne": "x != y", "lt": "_s32(x) < _s32(y)", "ge": "_s32(x) >= _s32(y)",
                    "lo": "x < y", "hs": "x >= y"}.get(f.brcond) if op == "br" else \
                ("not (x >> (y & 31)) & 1" if op == "bbit0" else "(x >> (y & 31)) & 1")
            return ["x = %s" % a, "y = %s" % b, "if not (%s): return %d" % (test, nxt)] + \
                self._taken(f, f.target, nxt)
        if op in ("j", "jl"):
            src = f.srcs[0]
            lines = ["if not %s: return %d" % (COND_EXPR[f.cond], nxt)] if f.cond else []
            lines.append("t = %s & %d" % (S(src), M & ~1))
            if op == "jl":
                lines.append("r[31] = %d" % ((nxt + slot) & M if f.delay else nxt))
            if f.setf:                          # j.f [ilink1/2]: STATUS32 from STATUS32_L1/_L2
                lines.append("eng.aux_write(%d, eng.aux_read(%d))" %
                             (AUX_STATUS32, AUX_STATUS32_L1 if src == ("r", 29) else AUX_STATUS32_L2))
            return lines + self._taken(f, "t", nxt)
        if op == "lp":
            lines = ["if not %s: return %d" % (COND_EXPR[f.cond], f.target)] if f.cond else []
            return lines + ["eng.lp_start = %d" % nxt, "eng.lp_end = %d" % f.target, "return %d" % nxt]
        if op == "flag":
            return ["v = %s" % S(f.srcs[0]),
                    "eng.aux_write(%d, (eng.aux_read(%d) & ~0xF07) | (v & 0xF07))" % (AUX_STATUS32, AUX_STATUS32),
                    "if v & 1:", "    eng.halted = True", "    eng.stop_request = True", "return %d" % nxt]
        if op == "lr":
            lines = ["v = eng.aux_read(%s)" % S(f.aux)] + self._dst(f.dst, "v")
            return lines + ["return %d" % nxt]
        if op == "sr":
            return ["eng.aux_write(%s, %s)" % (S(f.aux), S(f.srcs[0])), "return %d" % nxt]
        if op == "sleep":
            return ["eng.sleeping = True", "eng.stop_request = True", "return %d" % nxt]
        if op == "brk":
            return ["raise Fault(C.UC_ERR_EXCEPTION)"]
        return None

    def _address_lines(self, f, pc):
        """Lines that leave the access's address in a, and the base's write-back in wb (or None)."""
        base, off = f.srcs[0], f.srcs[1]
        b = self._src(base, pc)
        o = self._src(off, pc) if off[0] == "r" else "%d" % off[1]
        writable = base[0] == "r" and base[1] < 61
        if f.aa == 3:
            shift = {4: 2, 2: 1, 1: 0}[f.width]
            return ["a = (%s + (%s << %d)) & %d" % (b, o, shift, M)], None
        if f.aa == 1 and writable:
            return ["a = (%s + %s) & %d" % (b, o, M)], "r[%d] = a" % base[1]
        if f.aa == 2 and writable:
            return ["a = %s" % b], "r[%d] = (a + %s) & %d" % (base[1], o, M)
        return ["a = (%s + %s) & %d" % (b, o, M)], None

    def _load_lines(self, f, pc, nxt):
        lines, wb = self._address_lines(f, pc)
        lines.append("v = load(a, %d)" % f.width)
        if f.sext and f.width < 4:
            bit = 8 * f.width
            lines.append("v = ((v ^ %d) - %d) & %d" % (1 << (bit - 1), 1 << (bit - 1), M))
        if wb:
            lines.append(wb)
        return lines + self._dst(f.dst, "v") + ["return %d" % nxt]

    def _store_lines(self, f, pc, nxt):
        lines, wb = self._address_lines(f, pc)
        lines += ["v = %s" % self._src(f.srcs[2], pc), "store(a, %d, v)" % f.width]
        if wb:
            lines.append(wb)
        return lines + ["return %d" % nxt]
