"""The MSP430 engine: an interpreter of the 16-bit MSP430 behind the engine contract (dread.engines),
which the layer's new_uc() returns. Register ids are the register numbers (0 pc, 1 sp, 2 sr, 3 the
constant generator, 4-15).

The semantics follow TI's MSP430x2xx family user's guide (SLAU144), chapter 3:
- pc and sp are even: bit 0 of a value written to them is dropped. r3 reads 0 and ignores writes.
- A byte operation on a register clears its high byte; a word access ignores bit 0 of the address.
- @rN+ adds 1 after a byte access and 2 after a word access; sp and pc always add 2.
- Extension words are fetched in order, the source's before the destination's: pc as an operand
  reads the address behind the words fetched so far (add r15, pc: behind the instruction; the
  source pc of mov pc, 2(r4): behind the first word). While an instruction runs, the engine's pc
  stays on it, and register write-backs come as late as the semantics allow (see the operands'
  comment): memory hooks see the registers of the instruction's start, as on Unicorn.
- mov does not read its destination; cmp and bit do not write it. push evaluates its operand before
  sp moves (push sp pushes the old sp); push.b writes one byte at the new sp. call evaluates its
  target before it pushes the return address. reti pops sr, then pc.
- Flags (sr: C bit 0, Z bit 1, N bit 2, V bit 8): add, addc, sub, subc and cmp set all four (C is
  the carry, so "no borrow" for a subtraction); bit, and and sxt set C to not Z and clear V; xor
  sets C to not Z and V when both operands are negative; rrc and rra shift into C and clear V; dadd
  sets C, Z and N and leaves V alone (the guide: undefined). mov, bic, bis, swpb, push and call
  leave the flags.
- An instruction that writes sr and sets flags: the flags win (not confirmed on hardware).
- An instruction that sets CPUOFF (sr bit 4: a low-power mode) ends the run behind it, with
  `sleeping` set; only an interrupt (the layer's enter_interrupt) goes on from there.
- dadd on digits that are not decimal: each digit sum above 9 drops 10 and carries (the guide does
  not define it).
"""
from unicorn import UcError
import unicorn.unicorn_const as C

from dread.arch.msp430 import WRITES_DST, decode_fields, length_of
from dread.engines import Engine, Fault, PAGE_BITS

CF, ZF, NF, GIE, CPUOFF, VF = 1, 2, 4, 8, 0x10, 0x100
FLAGS = CF | ZF | NF | VF
BLOCK_LIMIT = 128           # instructions in one block at most


def _add(a, b, carry, mask, msb):
    s = a + b + carry
    res = s & mask
    return res, s > mask, res == 0, res & msb, ~(a ^ b) & (a ^ res) & msb


def _dadd(a, b, carry, digits):
    res = 0
    for i in range(0, 4 * digits, 4):
        d = ((a >> i) & 15) + ((b >> i) & 15) + carry
        carry = 1 if d > 9 else 0
        if carry:
            d -= 10
        res |= (d & 15) << i
    return res, carry


class Msp430Engine(Engine):
    address_bits = 16

    def __init__(self):
        self.r = [0] * 16
        self.cache = {}         # address -> (Fields, size, ends a block, the compiled instruction)
        self.blocks = {}        # address -> the block's size up to its end
        self.marks = bytearray(1 << self.address_bits)     # bytes of instructions in the cache
        self.sleeping = False
        super().__init__()

    # ---- registers ----

    @property
    def pc(self):
        return self.r[0]

    @pc.setter
    def pc(self, value):
        self.r[0] = value & 0xFFFE

    def reg_read(self, rid):
        if not 0 <= rid < 16:
            raise UcError(C.UC_ERR_ARG)
        return self.r[rid]

    def reg_write(self, rid, value):
        if not 0 <= rid < 16:
            raise UcError(C.UC_ERR_ARG)
        if rid != 3:
            self.r[rid] = value & (0xFFFE if rid < 2 else 0xFFFF)

    # ---- decoding ----

    def flush(self):
        self.cache.clear()
        self.blocks.clear()
        self.marks[:] = bytes(len(self.marks))

    def mem_write(self, address, data):
        data = bytes(data)
        if not data:
            return
        self._check(address, len(data), C.UC_ERR_WRITE_UNMAPPED)
        end = address + len(data)
        if self.marks.find(1, address, end) >= 0:
            mem, marks = self.mem, self.marks
            if any(marks[a] and mem[a] != data[a - address] for a in range(address, end)):
                self.flush()
        self.mem[address:end] = data

    def store(self, address, size, value):
        if self.write_flags[address]:
            self.run_mem_hooks(self.write_hooks, C.UC_MEM_WRITE, address, size, value)
        page = self.pages[address >> PAGE_BITS]
        if not page:
            self.invalid(C.UC_MEM_WRITE_UNMAPPED, address, size, value)
            page = self.pages[address >> PAGE_BITS]
            if not page:
                raise Fault(C.UC_ERR_WRITE_UNMAPPED)
        if not page & C.UC_PROT_WRITE:
            self.invalid(C.UC_MEM_WRITE_PROT, address, size, value)
        m, marks = self.mem, self.marks
        low = value & 0xFF
        if marks[address] and m[address] != low:
            self.flush()
        m[address] = low
        if size == 2:
            high = (value >> 8) & 0xFF
            if marks[address + 1] and m[address + 1] != high:
                self.flush()
            m[address + 1] = high

    @staticmethod
    def _ends_block(f):
        if f.target is not None or f.op in ("reti", "call"):
            return True
        d = f.dst
        return d is not None and d.mode == "reg" and d.reg == 0 and f.op in WRITES_DST

    def _decoded(self, pc, f):
        entry = (f, f.size, self._ends_block(f), self._compile(f, pc, f.size))
        self.cache[pc] = entry
        self.marks[pc:pc + f.size] = b"\x01" * f.size
        return entry

    def decode(self, pc):
        """The cached decoding of the instruction at pc; fetches it (with the fetch checks) the first
        time."""
        entry = self.cache.get(pc)
        if entry is not None:
            return entry
        self.fetch_check(pc, 2)
        m = self.mem
        size = length_of(m[pc] | m[pc + 1] << 8)
        if pc + size > len(m):
            raise Fault(C.UC_ERR_FETCH_UNMAPPED)
        self.fetch_check(pc, size)
        f = decode_fields(bytes(m[pc:pc + size]), pc)
        if f.op == "unknown":
            raise Fault(C.UC_ERR_INSN_INVALID)
        return self._decoded(pc, f)

    def _peek(self, pc):
        """The decoding at pc without hooks or faults (for measuring blocks): None where code
        cannot be fetched or decoded."""
        entry = self.cache.get(pc)
        if entry is not None:
            return entry
        m, pages = self.mem, self.pages
        if pc + 2 > len(m) or not pages[pc >> PAGE_BITS] & C.UC_PROT_EXEC:
            return None
        size = length_of(m[pc] | m[pc + 1] << 8)
        if pc + size > len(m) or not pages[(pc + size - 1) >> PAGE_BITS] & C.UC_PROT_EXEC:
            return None
        f = decode_fields(bytes(m[pc:pc + size]), pc)
        if f.op == "unknown":
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
                    break
            self.blocks[pc] = size
        if until is not None and pc < until < pc + size:
            return until - pc
        return size

    # ---- operands ----
    # pc stays on the instruction while it runs (a hook reads the instruction's address there, as on
    # Unicorn); where the instruction reads pc as an operand, the value is the address behind the
    # words fetched up to then, known when it is decoded: pc + 2 for a source register or @pc, pc +
    # size for a destination register. Register write-backs come as late as the semantics allow, so
    # that a memory hook sees the registers of the instruction's start: @rN+ adds after its load
    # (before the destination's address is formed), push and call move sp after their store, reti
    # after both loads.

    def _source(self, o, byte, pc):
        """The source operand's value (and @rN+'s increment, after the load)."""
        r = self.r
        mode = o.mode
        if mode == "reg":
            v = r[o.reg] if o.reg else (pc + 2) & 0xFFFF
            return v & 0xFF if byte else v
        if mode == "imm":
            return o.value & 0xFF if byte else o.value
        if mode == "idx":
            a = (r[o.reg] + o.value) & 0xFFFF
        elif mode == "sym":
            a = o.address
        elif mode == "abs":
            a = o.value
        elif mode == "ind":
            a = r[o.reg] if o.reg else (pc + 2) & 0xFFFF
        else:                               # inc (@pc+ is an immediate)
            a = r[o.reg]
            v = self.load(a, 1) if byte else self.load(a & 0xFFFE, 2)
            r[o.reg] = (a + (1 if byte and o.reg > 1 else 2)) & (0xFFFF if o.reg > 1 else 0xFFFE)
            return v
        if byte:
            return self.load(a, 1)
        return self.load(a & 0xFFFE, 2)

    def _destination(self, o):
        """The memory address of an indexed, symbolic or absolute destination."""
        if o.mode == "idx":
            return (self.r[o.reg] + o.value) & 0xFFFF
        if o.mode == "sym":
            return o.address
        return o.value

    def _write_reg(self, n, value, byte):
        if n == 3:
            return
        if byte:
            value &= 0xFF
        self.r[n] = value & 0xFFFE if n == 1 else value

    def _flags(self, c, z, n, v):
        self.r[2] = (self.r[2] & ~FLAGS) | (CF if c else 0) | (ZF if z else 0) | (NF if n else 0) | (VF if v else 0)

    def _sr_written(self):
        if self.r[2] & CPUOFF:
            self.sleeping = True
            self.stop_request = True

    # ---- execution ----

    def emu_start(self, begin, until, timeout=0, count=0):
        """The base class's run loop (dread.engines.Engine.emu_start: keep the two alike) with step()
        inlined and pc as the register itself: a third less time an instruction."""
        self.sleeping = False
        r, cache = self.r, self.cache
        r[0] = begin & 0xFFFE
        self.stop_request = False
        self.until = until
        n, block_start = 0, True
        try:
            while True:
                pc = r[0]
                if pc == until or (count and n >= count):
                    break
                if block_start and self.block_hooks:
                    self.run_block_hooks(pc, self.block_size(pc, until))
                    if self.stop_request:
                        break
                    if r[0] != pc:
                        continue
                block_start = False
                if self.code_everywhere or pc in self.code_at:
                    self.run_code_hooks(pc, self.insn_size(pc))
                    if self.stop_request:
                        break
                    if r[0] != pc:          # redirected: a new block where it goes on
                        block_start = True
                        continue
                entry = cache.get(pc)
                if entry is None:
                    entry = self.decode(pc)
                r[0] = entry[3]()
                block_start = entry[2]
                n += 1
                if self.stop_request:
                    break
        except Fault as e:
            r[0] = pc
            raise UcError(e.code)
        finally:
            self.until = None

    def step(self, pc):
        """Run the instruction at pc; True when it ends a block."""
        entry = self.cache.get(pc)
        if entry is None:
            entry = self.decode(pc)
        self.r[0] = entry[3]()
        return entry[2]

    # ---- the instructions compiled ----
    # Each decoded instruction becomes a Python function of its own (_compile), written out once with
    # its operation, operand modes, registers and constants filled in, so that a run dispatches on
    # none of them: about three times faster than interpreting the fields at every step. The
    # semantics are those the module's docstring states (and the operands' comment above), in the
    # same order: the source with its @rN+ increment after its load, then the destination's address,
    # then its load, the store or register write-back, the flags, sr's low-power check. The function
    # returns where execution goes on; step() puts that into pc, so a hook during the instruction
    # still sees pc on it.

    def _compile(self, f, pc, size):
        env = {"r": self.r, "load": self.load, "store": self.store, "sr_written": self._sr_written,
               "_dadd": _dadd}
        exec("def run():\n    " + "\n    ".join(self._lines(f, pc, size)), env)
        return env["run"]

    def _lines(self, f, pc, size):
        op, nxt = f.op, (pc + size) & 0xFFFF
        if f.target is not None:            # jumps: the condition on sr's flags
            cond = {"jmp": None, "jne": "not r[2] & 2", "jeq": "r[2] & 2", "jlo": "not r[2] & 1",
                    "jhs": "r[2] & 1", "jn": "r[2] & 4", "jge": "not ((r[2] >> 2) ^ (r[2] >> 8)) & 1",
                    "jl": "((r[2] >> 2) ^ (r[2] >> 8)) & 1"}[op]
            if cond is None:
                return ["return %d" % f.target]
            return ["return %d if %s else %d" % (f.target, cond, (pc + 2) & 0xFFFF)]
        if op == "reti":
            return ["sp = r[1]", "sr = load(sp, 2)", "back = load((sp + 2) & 0xFFFF, 2)", "r[2] = sr",
                    "r[1] = (sp + 4) & 0xFFFE", "sr_written()", "return back & 0xFFFE"]
        if f.dst is None:
            return self._single_lines(f.src, op, f.byte, pc, nxt)
        return self._double_lines(f, op, f.byte, pc, size, nxt)

    @staticmethod
    def _source_lines(o, byte, pc):
        """Lines that leave the source operand's value in s (and do @rN+'s increment, after the
        load)."""
        mode, n = o.mode, o.reg
        if mode == "reg":
            if n == 0:
                v = (pc + 2) & 0xFFFF
                return ["s = %d" % (v & 0xFF if byte else v)]
            return ["s = r[%d] & 0xFF" % n] if byte else ["s = r[%d]" % n]
        if mode == "imm":
            return ["s = %d" % (o.value & 0xFF if byte else o.value)]
        if mode == "inc":                   # (@pc+ is an immediate)
            return ["a = r[%d]" % n, "s = load(a, 1)" if byte else "s = load(a & 0xFFFE, 2)",
                    "r[%d] = (a + %d) & %d" % (n, 1 if byte and n > 1 else 2, 0xFFFF if n > 1 else 0xFFFE)]
        if mode == "idx":
            a = "(r[%d] + %d) & 0xFFFF" % (n, o.value)
        elif mode == "sym":
            a = "%d" % o.address
        elif mode == "abs":
            a = "%d" % o.value
        else:                               # ind
            a = "r[%d]" % n if n else "%d" % ((pc + 2) & 0xFFFF)
        return ["s = load(%s, 1)" % a] if byte else ["s = load((%s) & 0xFFFE, 2)" % a]

    @staticmethod
    def _write_back(n, byte):
        """The register write-back of res to register n (r3 takes nothing, sp stays even)."""
        if n == 3:
            return []
        if n == 1:
            return ["r[1] = res & %d" % (0xFE if byte else 0xFFFE)]
        return ["r[%d] = res & 0xFF" % n] if byte else ["r[%d] = res" % n]

    def _double_lines(self, f, op, byte, pc, size, nxt):
        """A two-operand instruction."""
        mask, msb, width = (0xFF, 0x80, 1) if byte else (0xFFFF, 0x8000, 2)
        lines = self._source_lines(f.src, byte, pc)
        d = f.dst
        n = d.reg if d.mode == "reg" else None
        if n is None:                       # the destination's address, after the source's increment
            if d.mode == "idx":
                a = "(r[%d] + %d) & 0xFFFF" % (d.reg, d.value)
            elif d.mode == "sym":
                a = "%d" % d.address
            else:
                a = "%d" % d.value
            lines.append("d = %s" % a if byte else "d = (%s) & 0xFFFE" % a)
            if op != "mov":
                lines.append("dv = load(d, %d)" % width)
        elif op != "mov":
            lines.append("dv = %d" % ((pc + size) & mask) if n == 0 else "dv = r[%d] & %d" % (n, mask))
        res, flags = True, False
        if op == "mov":
            lines.append("res = s")
        elif op in ("add", "addc", "sub", "subc", "cmp"):
            carry = {"add": "", "addc": " + (r[2] & 1)", "sub": " + 1", "subc": " + (r[2] & 1)", "cmp": " + 1"}[op]
            lines += ["b = %s" % ("s" if op in ("add", "addc") else "~s & %d" % mask),
                      "t = dv + b%s" % carry, "res = t & %d" % mask,
                      "fl = (1 if t > %d else 0) | (0 if res else 2) | (4 if res & %d else 0)"
                      " | (256 if ~(dv ^ b) & (dv ^ res) & %d else 0)" % (mask, msb, msb)]
            res, flags = op != "cmp", True
        elif op == "dadd":
            lines += ["res, c = _dadd(dv, s, r[2] & 1, %d)" % (2 if byte else 4),
                      "fl = c | (0 if res else 2) | (4 if res & %d else 0) | (r[2] & 256)" % msb]
            flags = True
        elif op == "bit":
            lines += ["t = s & dv", "fl = (1 if t else 0) | (0 if t else 2) | (4 if t & %d else 0)" % msb]
            res, flags = False, True
        elif op == "bic":
            lines.append("res = ~s & dv & %d" % mask)
        elif op == "bis":
            lines.append("res = s | dv")
        elif op == "xor":
            lines += ["res = s ^ dv", "fl = (1 if res else 0) | (0 if res else 2) | (4 if res & %d else 0)"
                      " | (256 if s & dv & %d else 0)" % (msb, msb)]
            flags = True
        else:                               # and
            lines += ["res = s & dv", "fl = (1 if res else 0) | (0 if res else 2) | (4 if res & %d else 0)" % msb]
            flags = True
        back = "%d" % nxt
        if res:
            if n is None:
                lines.append("store(d, %d, res)" % width)
            elif n == 0:
                back = "res & %d & 0xFFFE" % mask
            else:
                lines += self._write_back(n, byte)
        if flags:
            lines.append("r[2] = (r[2] & %d) | fl" % ~FLAGS)
        if n == 2:                          # (every operation writes sr or sets flags there)
            lines.append("sr_written()")
        return lines + ["return " + back]

    def _single_lines(self, o, op, byte, pc, nxt):
        """A one-operand instruction."""
        if op in ("push", "call"):          # the operand first, then the store, then sp
            lines = self._source_lines(o, byte and op == "push", pc) + ["sp = (r[1] - 2) & 0xFFFE"]
            if op == "push":
                return lines + ["store(sp, %d, s)" % (1 if byte else 2), "r[1] = sp", "return %d" % nxt]
            return lines + ["store(sp, 2, %d)" % nxt, "r[1] = sp", "return s & 0xFFFE"]
        mask, msb, width = (0xFF, 0x80, 1) if byte else (0xFFFF, 0x8000, 2)
        # rrc, swpb, rra, sxt: the result goes back where the operand came from
        mode, n, lines = o.mode, None, []
        if mode == "reg":
            n = o.reg
            lines.append("v = %d" % ((pc + 2) & 0xFFFF & mask) if n == 0 else "v = r[%d] & %d" % (n, mask))
        elif mode == "imm":
            if o.reg == 0:                  # #N: the result overwrites the extension word
                lines.append("d = %d" % ((pc + 2) & 0xFFFF))
            else:
                n = 3                       # a constant generator: the result goes nowhere
            lines.append("v = %d" % (o.value & mask))
        else:
            if mode == "idx":
                a = "(r[%d] + %d) & 0xFFFF" % (o.reg, o.value)
            elif mode == "sym":
                a = "%d" % o.address
            elif mode == "abs":
                a = "%d" % o.value
            else:                           # ind, inc
                a = "r[%d]" % o.reg if o.reg else "%d" % ((pc + 2) & 0xFFFF)
            lines += ["d = %s" % a if byte else "d = (%s) & 0xFFFE" % a, "v = load(d, %d)" % width]
            if mode == "inc":
                lines.append("r[%d] = (r[%d] + %d) & %d" % (o.reg, o.reg, 1 if byte and o.reg > 1 else 2,
                                                          0xFFFF if o.reg > 1 else 0xFFFE))
        flags = True
        if op == "rrc":
            lines += ["res = (v >> 1) | ((r[2] & 1) * %d)" % msb,
                      "fl = (v & 1) | (0 if res else 2) | (4 if res & %d else 0)" % msb]
        elif op == "rra":
            lines += ["res = (v >> 1) | (v & %d)" % msb, "fl = (v & 1) | (0 if res else 2) | (4 if res & %d else 0)" % msb]
        elif op == "swpb":
            lines.append("res = ((v & 0xFF) << 8) | (v >> 8)")
            flags = False
        else:                               # sxt
            lines += ["res = v & 0xFF", "if res & 0x80:", "    res |= 0xFF00",
                      "fl = (1 if res else 0) | (0 if res else 2) | (4 if res & 0x8000 else 0)"]
        back = "%d" % nxt
        if n == 0:
            back = "res & 0xFFFE"
        elif n is not None:
            lines += self._write_back(n, byte)
        else:
            lines.append("store(d, %d, res)" % width)
        if flags:
            lines.append("r[2] = (r[2] & %d) | fl" % ~FLAGS)
        if n == 2:
            lines.append("sr_written()")
        return lines + ["return " + back]
