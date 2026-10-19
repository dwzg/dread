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
        self.cache = {}         # address -> (Fields, size, ends a block)
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
        entry = (f, f.size, self._ends_block(f))
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
        self.sleeping = False
        super().emu_start(begin, until, timeout, count)

    def step(self, pc):
        """Run the instruction at pc; True when it ends a block."""
        entry = self.cache.get(pc)
        if entry is None:
            entry = self.decode(pc)
        f, size, ends = entry
        r = self.r
        op = f.op
        if f.target is not None:            # jumps
            sr = r[2]
            if op == "jmp":
                taken = True
            elif op == "jne":
                taken = not sr & ZF
            elif op == "jeq":
                taken = sr & ZF
            elif op == "jlo":
                taken = not sr & CF
            elif op == "jhs":
                taken = sr & CF
            elif op == "jn":
                taken = sr & NF
            elif op == "jge":
                taken = not (((sr >> 2) ^ (sr >> 8)) & 1)
            else:                           # jl
                taken = ((sr >> 2) ^ (sr >> 8)) & 1
            r[0] = f.target if taken else (pc + 2) & 0xFFFF
            return True
        if op == "reti":
            sp = r[1]
            sr = self.load(sp, 2)
            back = self.load((sp + 2) & 0xFFFF, 2)
            r[2], r[1], r[0] = sr, (sp + 4) & 0xFFFE, back & 0xFFFE
            self._sr_written()
            return True
        if f.dst is None:
            r[0] = self._single(f, op, f.byte, pc, size)
        else:
            r[0] = self._double(f, op, f.byte, pc, size)
        return ends

    def _double(self, f, op, byte, pc, size):
        """A two-operand instruction; returns where execution goes on."""
        r = self.r
        s = self._source(f.src, byte, pc)
        d = f.dst
        if byte:
            mask, msb, width = 0xFF, 0x80, 1
        else:
            mask, msb, width = 0xFFFF, 0x8000, 2
        if d.mode == "reg":
            n, address = d.reg, None
            dv = (r[n] if n else pc + size) & mask
        else:
            n, address = None, self._destination(d)
            if not byte:
                address &= 0xFFFE
            dv = self.load(address, width) if op != "mov" else 0
        flags = None
        if op == "mov":
            res = s
        elif op == "add":
            res, *flags = _add(dv, s, 0, mask, msb)
        elif op == "addc":
            res, *flags = _add(dv, s, r[2] & CF, mask, msb)
        elif op == "sub":
            res, *flags = _add(dv, ~s & mask, 1, mask, msb)
        elif op == "subc":
            res, *flags = _add(dv, ~s & mask, r[2] & CF, mask, msb)
        elif op == "cmp":
            _, *flags = _add(dv, ~s & mask, 1, mask, msb)
            res = None
        elif op == "dadd":
            res, carry = _dadd(dv, s, r[2] & CF, 2 if byte else 4)
            flags = (carry, res == 0, res & msb, r[2] & VF)
        elif op == "bit":
            t = s & dv
            flags, res = (t != 0, t == 0, t & msb, 0), None
        elif op == "bic":
            res = ~s & dv & mask
        elif op == "bis":
            res = s | dv
        elif op == "xor":
            res = s ^ dv
            flags = (res != 0, res == 0, res & msb, s & dv & msb)
        else:                               # and
            res = s & dv
            flags = (res != 0, res == 0, res & msb, 0)
        nxt = (pc + size) & 0xFFFF
        if res is not None:
            if n is None:
                self.store(address, width, res)
            elif n == 0:
                nxt = (res & mask) & 0xFFFE
            else:
                self._write_reg(n, res, byte)
        if flags is not None:
            self._flags(*flags)
        if n == 2 and (res is not None or flags is not None):
            self._sr_written()
        return nxt

    def _single(self, f, op, byte, pc, size):
        """A one-operand instruction; returns where execution goes on."""
        r = self.r
        o = f.src
        nxt = (pc + size) & 0xFFFF
        if op in ("push", "call"):
            v = self._source(o, byte and op == "push", pc)
            sp = (r[1] - 2) & 0xFFFE
            if op == "push":
                self.store(sp, 1 if byte else 2, v)
            else:
                self.store(sp, 2, nxt)      # the return address: behind the instruction
                nxt = v & 0xFFFE
            r[1] = sp
            return nxt
        if byte:
            mask, msb, width = 0xFF, 0x80, 1
        else:
            mask, msb, width = 0xFFFF, 0x8000, 2
        # rrc, swpb, rra, sxt: the result goes back where the operand came from
        mode, n, address = o.mode, None, None
        if mode == "reg":
            n = o.reg
            v = (r[n] if n else (pc + 2) & 0xFFFF) & mask
        elif mode == "imm":
            if o.reg == 0:                  # #N: the result overwrites the extension word
                address = (pc + 2) & 0xFFFF
            else:
                n = 3                       # a constant generator: the result goes nowhere
            v = o.value & mask
        else:
            if mode == "idx":
                address = (r[o.reg] + o.value) & 0xFFFF
            elif mode == "sym":
                address = o.address
            elif mode == "abs":
                address = o.value
            else:                           # ind, inc
                address = r[o.reg] if o.reg else (pc + 2) & 0xFFFF
            if not byte:
                address &= 0xFFFE
            v = self.load(address, width)
            if mode == "inc":
                r[o.reg] = (r[o.reg] + (1 if byte and o.reg > 1 else 2)) & (0xFFFF if o.reg > 1 else 0xFFFE)
        flags = None
        if op == "rrc":
            res = (v >> 1) | ((r[2] & CF) * msb)
            flags = (v & 1, res == 0, res & msb, 0)
        elif op == "rra":
            res = (v >> 1) | (v & msb)
            flags = (v & 1, res == 0, res & msb, 0)
        elif op == "swpb":
            res = ((v & 0xFF) << 8) | (v >> 8)
        else:                               # sxt
            res = v & 0xFF
            if res & 0x80:
                res |= 0xFF00
            flags = (res != 0, res == 0, res & 0x8000, 0)
        if n == 0:
            nxt = res & 0xFFFE
        elif n is not None:
            self._write_reg(n, res, byte)
        else:
            self.store(address, width, res)
        if flags is not None:
            self._flags(*flags)
        if n == 2:
            self._sr_written()
        return nxt
