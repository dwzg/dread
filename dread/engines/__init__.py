"""Emulators of DREAD's own, for architectures Unicorn does not have, behind Unicorn's interface.

DREAD drives every emulator through a part of Unicorn's Python interface: the engine contract
below. An architecture's layer returns its engine from new_uc(): a unicorn.Uc for ARM and RISC-V,
an Msp430Engine (dread.engines.msp430) for the MSP430. The constants (UC_HOOK_*, UC_PROT_*,
UC_MEM_*, UC_ERR_*) and the exception (UcError) are Unicorn's own.

The contract, with the semantics DREAD relies on (Unicorn 2.1 behaves so; tests/test_engines.py
checks the engines against it):

memory      mem_map(address, size, perms=UC_PROT_ALL), mem_unmap(address, size) and
            mem_protect(address, size, perms) take whole 4 KiB pages; mapping a mapped page is
            UcError(UC_ERR_MAP). mem_read(address, size) -> bytearray and mem_write(address, data)
            raise UcError(UC_ERR_READ_UNMAPPED, UC_ERR_WRITE_UNMAPPED) on unmapped bytes and run
            no hooks.
registers   reg_read(id), reg_write(id, value), with the ids of the layer's reg_id().
running     emu_start(begin, until, timeout=0, count=0) runs from begin until pc reaches until
            (that instruction neither runs nor gets a code hook) or count instructions have run;
            a fault ends it with UcError, pc on the faulting instruction. emu_stop() from a code or
            block hook: that instruction or block does not run, pc stays on it, and its remaining
            hooks are skipped.
hooks       hook_add(kind, callback, user_data=None, begin=1, end=0) -> handle, hook_del(handle);
            begin and end bound the address, both included; begin > end means everywhere. Hooks
            run in the order they were added.
            UC_HOOK_CODE   callback(engine, address, size, user) before each instruction; writing pc
                           redirects (the instruction does not run).
            UC_HOOK_BLOCK  callback(engine, address, size, user) where a block starts: the
                           instructions up to and including the next branch, call or return, or
                           up to `until`.
            UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE  callback(engine, access, address, size, value, user)
                           before a data access (instruction fetches are not reported): a read hook
                           may write the memory and the load returns what it wrote; a write hook
                           sees the old contents and the store follows it. A write hook runs even
                           when the store then faults; a read hook does not run for unmapped memory.
            UC_HOOK_MEM_READ_UNMAPPED, _WRITE_UNMAPPED, _FETCH_UNMAPPED, _READ_PROT, _WRITE_PROT,
                           _FETCH_PROT (and their unions UC_HOOK_MEM_UNMAPPED, _PROT, _INVALID):
                           callback(engine, access, address, size, value, user) -> True when it
                           mapped or opened the memory (the access is tried again), else the run
                           ends with UcError.
            UC_HOOK_INTR   callback(engine, number, user) for a software interrupt.
controls    ctl_flush_tb() and ctl_set_cpu_model(model) exist; DREAD's own engines need neither.

Where the engines differ: emu_stop() from a memory hook. Unicorn finishes a store but not a load
and leaves pc on the instruction either way, so that resuming at pc repeats the access; DREAD's
engines finish the instruction and leave pc behind it. DREAD therefore stops from a memory hook
only where the run ends there; where it goes on, it stops at the next block (dread.sysrun).

Not part of the contract: the C hooks of dread.fasthooks, which reach into Unicorn's C library.
"""
from unicorn import UcError
import unicorn.unicorn_const as C

PAGE_BITS = 12
PAGE = 1 << PAGE_BITS
MAPPED = 0x80               # in Engine.pages, beside the UC_PROT_* bits of a mapped page

KINDS = {
    C.UC_HOOK_CODE: "code", C.UC_HOOK_BLOCK: "block", C.UC_HOOK_MEM_READ: "read",
    C.UC_HOOK_MEM_WRITE: "write", C.UC_HOOK_INTR: "intr",
    C.UC_HOOK_MEM_READ_UNMAPPED: C.UC_MEM_READ_UNMAPPED, C.UC_HOOK_MEM_WRITE_UNMAPPED: C.UC_MEM_WRITE_UNMAPPED,
    C.UC_HOOK_MEM_FETCH_UNMAPPED: C.UC_MEM_FETCH_UNMAPPED, C.UC_HOOK_MEM_READ_PROT: C.UC_MEM_READ_PROT,
    C.UC_HOOK_MEM_WRITE_PROT: C.UC_MEM_WRITE_PROT, C.UC_HOOK_MEM_FETCH_PROT: C.UC_MEM_FETCH_PROT,
}
ERRORS = {C.UC_MEM_READ_UNMAPPED: C.UC_ERR_READ_UNMAPPED, C.UC_MEM_WRITE_UNMAPPED: C.UC_ERR_WRITE_UNMAPPED,
          C.UC_MEM_FETCH_UNMAPPED: C.UC_ERR_FETCH_UNMAPPED, C.UC_MEM_READ_PROT: C.UC_ERR_READ_PROT,
          C.UC_MEM_WRITE_PROT: C.UC_ERR_WRITE_PROT, C.UC_MEM_FETCH_PROT: C.UC_ERR_FETCH_PROT}


class Fault(Exception):
    """Ends a run: the UC_ERR_* code. Raised inside an instruction, turned into UcError by
    emu_start with pc back on the instruction."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Engine:
    """Memory, hooks and the bookkeeping of a run, shared by DREAD's own engines. The address space
    is flat (1 << address_bits bytes, the bytes of unmapped pages unused). A subclass supplies the
    registers (reg_read, reg_write, pc and its setter) and the instructions: step() runs one at the
    current pc, block_size() measures a block, insn_size() the instruction at an address, and
    flush() forgets what it decoded."""

    address_bits = 16

    def __init__(self):
        size = 1 << self.address_bits
        self.mem = bytearray(size)
        self.pages = bytearray(size >> PAGE_BITS)   # MAPPED | perms, or 0
        self.hooks = {}                             # handle -> (kinds, callback, user, begin, end)
        self.next_handle = 1
        self.stop_request = False
        self.until = None
        self.stall, self.stalled = 0, False         # see emu_start and dread.emu.Machine.call
        self.rebuild_hooks()

    def flush(self):
        """Forget decoded instructions (after the memory map or code changed)."""

    # ---- memory ----

    def _pages_of(self, address, size):
        if address % PAGE or size % PAGE or size <= 0:
            raise UcError(C.UC_ERR_ARG)
        if address + size > len(self.mem):
            raise UcError(C.UC_ERR_ARG)
        return range(address >> PAGE_BITS, (address + size) >> PAGE_BITS)

    def mem_map(self, address, size, perms=C.UC_PROT_ALL):
        pages = self._pages_of(address, size)
        if any(self.pages[p] for p in pages):
            raise UcError(C.UC_ERR_MAP)
        for p in pages:
            self.pages[p] = MAPPED | perms
            self.mem[p << PAGE_BITS:(p + 1) << PAGE_BITS] = bytes(PAGE)
        self.flush()

    def mem_unmap(self, address, size):
        pages = self._pages_of(address, size)
        if not all(self.pages[p] for p in pages):
            raise UcError(C.UC_ERR_NOMEM)
        for p in pages:
            self.pages[p] = 0
        self.flush()

    def mem_protect(self, address, size, perms):
        pages = self._pages_of(address, size)
        if not all(self.pages[p] for p in pages):
            raise UcError(C.UC_ERR_NOMEM)
        for p in pages:
            self.pages[p] = MAPPED | perms
        self.flush()

    def _check(self, address, size, error):
        if address < 0 or address + size > len(self.mem):
            raise UcError(error)
        for p in range(address >> PAGE_BITS, ((address + size - 1) >> PAGE_BITS) + 1):
            if not self.pages[p]:
                raise UcError(error)

    def mem_read(self, address, size):
        if size <= 0:
            return bytearray()
        self._check(address, size, C.UC_ERR_READ_UNMAPPED)
        return bytearray(self.mem[address:address + size])

    def mem_write(self, address, data):
        data = bytes(data)
        if not data:
            return
        self._check(address, len(data), C.UC_ERR_WRITE_UNMAPPED)
        self.mem[address:address + len(data)] = data
        self.wrote(address, len(data))

    def wrote(self, address, size):
        """Code may have changed in [address, address + size): a subclass that caches decoded
        instructions forgets the ones there."""

    def regions(self):
        """(begin, end, perms) of the mapped runs, end included (Unicorn's mem_regions)."""
        out, p = [], 0
        while p < len(self.pages):
            if self.pages[p]:
                q = p
                while q + 1 < len(self.pages) and self.pages[q + 1] == self.pages[p]:
                    q += 1
                out.append((p << PAGE_BITS, ((q + 1) << PAGE_BITS) - 1, self.pages[p] & 7))
                p = q + 1
            else:
                p += 1
        return out

    mem_regions = regions

    # ---- hooks ----

    def hook_add(self, htype, callback, user_data=None, begin=1, end=0, arg1=0, arg2=0):
        kinds, rest = [], htype
        for bit, kind in KINDS.items():
            if rest & bit:
                kinds.append(kind)
                rest &= ~bit
        if rest or not kinds:
            raise UcError(C.UC_ERR_HOOK)        # a kind this engine does not report
        handle = self.next_handle
        self.next_handle += 1
        self.hooks[handle] = (tuple(kinds), callback, user_data, begin, end)
        self.hooks_changed()
        return handle

    def hook_del(self, handle):
        if self.hooks.pop(handle, None) is not None:
            self.hooks_changed()

    def hooks_changed(self):
        """Hooks were added or taken away: during a run the tables are rebuilt at once (a hook may
        add a hook, which counts from the next instruction), else when the next run starts. A
        Machine adds and removes several hooks between two runs, and rebuilding the tables (two
        flag arrays the size of memory) at each took a tenth of a generation on MSP430."""
        if self.until is not None:
            self.rebuild_hooks()
        else:
            self.hooks_stale = True

    def rebuild_hooks(self):
        """Per kind, the hooks in the order they were added, and for code and memory hooks a
        filter by address: a set of addresses (narrow hooks) and a flag for hooks that cover
        everything or a wide range (checked at every event)."""
        size = len(self.mem)
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
            flags = bytearray(size)
            for b, e, _, _ in hooks:
                if b > e:
                    flags = bytearray(b"\x01") * size
                    break
                lo, hi = max(b, 0), min(e + 1, size)
                if lo < hi:
                    flags[lo:hi] = b"\x01" * (hi - lo)
            setattr(self, kind + "_hooks", hooks)
            setattr(self, kind + "_flags", flags)

    def run_code_hooks(self, address, size):
        for b, e, callback, user in self.code_hooks:
            if b > e or b <= address <= e:
                callback(self, address, size, user)
                if self.stop_request:
                    return

    def run_block_hooks(self, address, size):
        for b, e, callback, user in self.block_hooks:
            if b > e or b <= address <= e:
                callback(self, address, size, user)
                if self.stop_request:
                    return

    def run_mem_hooks(self, hooks, access, address, size, value):
        for b, e, callback, user in hooks:
            if b > e or b <= address <= e:
                callback(self, access, address, size, value, user)
                if self.stop_request:
                    return

    def invalid(self, access, address, size, value=0):
        """An access the memory refuses (unmapped, or without the permission): its hooks may map
        or open the memory, and the access is tried again; otherwise the run ends."""
        handled = False
        for b, e, callback, user in self.by_kind.get(access, ()):
            if b > e or b <= address <= e:
                if callback(self, access, address, size, value, user):
                    handled = True
                    break
        if not handled:
            raise Fault(ERRORS[access])

    def load(self, address, size):
        """A data load of 1 or 2 bytes (little-endian) with its hooks and checks."""
        page = self.pages[address >> PAGE_BITS]
        if not page:
            self.invalid(C.UC_MEM_READ_UNMAPPED, address, size)
            page = self.pages[address >> PAGE_BITS]
            if not page:
                raise Fault(C.UC_ERR_READ_UNMAPPED)
        if self.read_flags[address]:
            self.run_mem_hooks(self.read_hooks, C.UC_MEM_READ, address, size, 0)
        if not page & C.UC_PROT_READ:
            self.invalid(C.UC_MEM_READ_PROT, address, size)
        m = self.mem
        if size == 1:
            return m[address]
        return m[address] | m[address + 1] << 8

    def store(self, address, size, value):
        """A data store of 1 or 2 bytes (little-endian) with its hooks and checks."""
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
        m = self.mem
        m[address] = value & 0xFF
        if size == 2:
            m[address + 1] = (value >> 8) & 0xFF
        self.wrote(address, size)

    def fetch_check(self, address, size):
        """Whether code may be fetched from [address, address + size): unmapped or not executable
        memory goes to its hooks first."""
        for a in sorted({address, address + size - 1}):
            page = self.pages[a >> PAGE_BITS]
            if not page:
                self.invalid(C.UC_MEM_FETCH_UNMAPPED, a, size)
                page = self.pages[a >> PAGE_BITS]
                if not page:
                    raise Fault(C.UC_ERR_FETCH_UNMAPPED)
            if not page & C.UC_PROT_EXEC:
                self.invalid(C.UC_MEM_FETCH_PROT, a, size)

    # ---- running ----

    def emu_stop(self):
        self.stop_request = True

    def ctl_flush_tb(self):
        pass                                # decoded instructions follow writes by themselves

    def ctl_set_cpu_model(self, model):
        pass

    def emu_start(self, begin, until, timeout=0, count=0):
        """Run from begin (see the contract above). timeout is not supported (DREAD bounds runs
        by count)."""
        if self.hooks_stale:
            self.rebuild_hooks()
        self.pc = begin
        self.stop_request = False
        self.until = until
        n, block_start = 0, True
        stall, seen, left = self.stall, set(), self.stall      # (stall: see dread.emu.Machine.call)
        self.stalled = False
        try:
            while True:
                pc = self.pc
                if pc == until or (count and n >= count):
                    break
                if block_start and stall and pc not in seen:
                    seen.add(pc)
                    left = stall
                if block_start and self.block_hooks:
                    self.run_block_hooks(pc, self.block_size(pc, until))
                    if self.stop_request:
                        break
                    if self.pc != pc:
                        continue
                block_start = False
                if self.code_everywhere or pc in self.code_at:
                    self.run_code_hooks(pc, self.insn_size(pc))
                    if self.stop_request:
                        break
                    if self.pc != pc:       # redirected: a new block where it goes on
                        block_start = True
                        continue
                block_start = self.step(pc)
                n += 1
                if stall:
                    left -= 1
                    if left <= 0:
                        self.stalled = True
                        break
                if self.stop_request:
                    break
        except Fault as e:
            self.pc = pc
            raise UcError(e.code)
        finally:
            self.until = None
