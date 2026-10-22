"""The hot emulator hooks in C (fasthooks.c): coverage, load tracing, store tracking and the Thumb IT
repair without a Python call per event; and the system run's (dread.sysrun): its block hook
(SysHooks: the blocks that only move the clock or only count) and its device accesses (add_io: the
reads of plain registers, from the value the model gave last, which System asks for again after
every call into the models; every other access goes to Python).

The system run's state is SysState here and sys_t in C: a field added to one is added to the other
(the sizes are compared when the library loads). What the C side keeps for System, the log entries
of the reads it did (SysRead) and the registers it reads alone (SysPlain), it owns and frees with
the state; System takes the entries into its log before it adds to the log or looks at it.

The C file is compiled on first use with the system's C compiler (cc, or $CC) into
~/.cache/dread (keyed by the source's hash) and loaded with ctypes. When that fails, or with
DREAD_FASTHOOKS=0, library() returns None and dread.emu keeps its Python hooks; both record the same.
"""
import ctypes
import hashlib
import os
import subprocess
import sys
import tempfile
import weakref

SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fasthooks.c")
BLOCKS, TREE, READS, WRITES, PAGES, SEEN = range(6)

_LIB = []


class Set(ctypes.Structure):
    _fields_ = [("keys", ctypes.c_void_p), ("vals", ctypes.c_void_p), ("order", ctypes.c_void_p),
                ("cap", ctypes.c_size_t), ("used", ctypes.c_size_t), ("order_cap", ctypes.c_size_t),
                ("ordered", ctypes.c_int64)]


class State(ctypes.Structure):
    _fields_ = [("cover_lo", ctypes.c_uint64), ("cover_hi", ctypes.c_uint64),
                ("stack_lo", ctypes.c_uint64), ("stack_hi", ctypes.c_uint64),
                ("image_lo", ctypes.c_uint64), ("image_hi", ctypes.c_uint64),
                ("code_bits", ctypes.c_void_p),
                ("tree", ctypes.c_int32), ("settle", ctypes.c_int32), ("status_reg", ctypes.c_int32),
                ("it_mask", ctypes.c_uint32),
                ("reg_read", ctypes.c_void_p), ("reg_write", ctypes.c_void_p),
                ("blocks", Set), ("tree_set", Set), ("reads", Set), ("writes", Set), ("pages", Set),
                ("code_reads", ctypes.c_int32), ("last_block", ctypes.c_uint64),
                ("owners", ctypes.c_void_p), ("owners_n", ctypes.c_uint64), ("pc_reg", ctypes.c_int32),
                ("budget", ctypes.c_int64), ("budget_hit", ctypes.c_int32), ("emu_stop", ctypes.c_void_p),
                ("stall", ctypes.c_int64), ("stall_left", ctypes.c_int64), ("stalled", ctypes.c_int32),
                ("seen", Set)]


def library():
    """The loaded helper, or None (no compiler, a failed build, DREAD_FASTHOOKS=0)."""
    if _LIB:
        return _LIB[0]
    lib = None
    if os.environ.get("DREAD_FASTHOOKS", "1") != "0":
        try:
            lib = _load()
        except (OSError, subprocess.SubprocessError) as e:
            print("dread: the C hooks did not build (%s); using the Python hooks" % e, file=sys.stderr)
    _LIB.append(lib)
    return lib


def usable(engine):
    """Whether the C hooks can serve this engine: they reach into Unicorn's C library, so only a
    unicorn.Uc (DREAD's own engines, dread.engines, record in Python)."""
    if library() is None:
        return False
    from unicorn import Uc
    return isinstance(engine, Uc)


def _load():
    with open(SOURCE, "rb") as f:
        source = f.read()
    tag = hashlib.sha1(source + sys.platform.encode()).hexdigest()[:16]
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "dread")
    path = os.path.join(cache, "fasthooks-%s.so" % tag)
    if not os.path.exists(path):
        os.makedirs(cache, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".so", dir=cache)
        os.close(fd)
        try:
            subprocess.run([os.environ.get("CC", "cc"), "-O2", "-shared", "-fPIC", "-o", tmp, SOURCE],
                           check=True, capture_output=True, timeout=120)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    lib = ctypes.CDLL(path)
    lib.dread_state_size.restype = ctypes.c_size_t
    if lib.dread_state_size() != ctypes.sizeof(State):
        raise OSError("state layout differs (%d against %d bytes)" % (lib.dread_state_size(), ctypes.sizeof(State)))
    lib.dread_copy.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
    lib.dread_copy.restype = ctypes.c_size_t
    lib.dread_count.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.dread_count.restype = ctypes.c_size_t
    lib.dread_clear.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.dread_ranges.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    lib.dread_ranges.restype = ctypes.c_size_t
    return lib


class Hooks:
    """The C state of one emulator, and the hooks that write it."""

    def __init__(self, engine, layout, code=None):
        import unicorn.unicorn_py3.unicorn as U
        self.lib, self.uclib, self.engine = library(), U.uclib, engine
        self.state = State()
        self.lib.dread_init(ctypes.byref(self.state))
        s = self.state
        s.stack_lo, s.stack_hi = layout.stack_top - layout.stack_size, layout.stack_top
        image = layout.image
        s.image_lo, s.image_hi = image.base, image.end
        self.bits, self.code_id = None, None
        self.owners_buf, self.owners_id = None, None
        if code is not None:
            self.set_code(code, image.base, image.end)
        s.settle = 1 if layout.settle_it else 0
        s.status_reg = layout.arch.status_reg()
        s.it_mask = getattr(layout.arch, "IT_MASK", 0)     # ARM's IT state; other cores have none
        s.reg_read = ctypes.cast(self.uclib.uc_reg_read, ctypes.c_void_p)
        s.reg_write = ctypes.cast(self.uclib.uc_reg_write, ctypes.c_void_p)
        s.emu_stop = ctypes.cast(self.uclib.uc_emu_stop, ctypes.c_void_p)
        self.fn = {name: ctypes.cast(getattr(self.lib, name), ctypes.c_void_p) for name in
                   ("dread_block", "dread_read", "dread_write", "dread_budget")}

    def set_code(self, code, lo, hi, owners=None, pc_reg=0):
        """The instruction starts in [lo, hi), for telling loads of code as data (built once per
        set of addresses), and the image's extent owners (dread.program.Program.extent_owners: a
        load from a function's extent by another function's instruction is one too)."""
        s = self.state
        s.image_lo, s.image_hi = lo, hi
        if owners is not None:
            if self.owners_id != id(owners):
                self.owners_buf = (ctypes.c_uint16 * len(owners)).from_buffer(owners)
                self.owners_id = id(owners)
            s.owners, s.owners_n, s.pc_reg = ctypes.cast(self.owners_buf, ctypes.c_void_p), len(owners), pc_reg
        else:
            s.owners, s.owners_n = None, 0
        if self.code_id == (id(code), lo, hi):
            return
        nbits = (hi - lo + 1) // 2
        self.bits = (ctypes.c_uint8 * (nbits // 8 + 1))()
        for a in code:
            if lo <= a < hi:
                h = (a - lo) >> 1
                self.bits[h >> 3] |= 1 << (h & 7)
        s.code_bits = ctypes.cast(self.bits, ctypes.c_void_p)
        self.code_id = (id(code), lo, hi)

    def add(self, kind, name, begin=1, end=0):
        handle = ctypes.c_ulong()
        status = self.uclib.uc_hook_add(self.engine._uch, ctypes.byref(handle), kind, self.fn[name],
                                        ctypes.cast(ctypes.byref(self.state), ctypes.c_void_p), begin, end)
        if status:
            raise OSError("uc_hook_add failed (%d)" % status)
        return handle.value

    def delete(self, handle):
        self.uclib.uc_hook_del(self.engine._uch, handle)

    def items(self, which, values=False):
        n = self.lib.dread_count(ctypes.byref(self.state), which)
        keys = (ctypes.c_uint64 * max(n, 1))()
        vals = (ctypes.c_uint32 * max(n, 1))() if values else None
        n = self.lib.dread_copy(ctypes.byref(self.state), which, keys, vals)
        return [(keys[i], vals[i]) for i in range(n)] if values else [keys[i] for i in range(n)]

    def ranges(self):
        """The stores as merged, sorted ranges [(lo, hi), ...]."""
        n = self.lib.dread_count(ctypes.byref(self.state), WRITES)
        lo, hi = (ctypes.c_uint64 * max(n, 1))(), (ctypes.c_uint64 * max(n, 1))()
        n = self.lib.dread_ranges(ctypes.byref(self.state), lo, hi)
        return [(lo[i], hi[i]) for i in range(n)]

    def clear(self, which):
        self.lib.dread_clear(ctypes.byref(self.state), which)


NO_EVENT = (1 << 63) - 1            # SysState.next_event when nothing is scheduled
SYS_REGS = 64                       # registers the loop search of a stretch can compare (SYS_REGS in C)

BLOCK_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_void_p)
IO_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint64, ctypes.c_int, ctypes.c_int64,
                         ctypes.c_void_p)
UNKNOWN, VALUE, MEMORY, NO_MODEL = range(4)     # SysPlain.kind


class SysRead(ctypes.Structure):
    """A device log entry of reads the C hook did (sysread_t): model is an index into the run's
    models, -1 for none."""
    _fields_ = [("address", ctypes.c_uint64), ("value", ctypes.c_uint64), ("count", ctypes.c_uint64),
                ("model", ctypes.c_int32), ("size", ctypes.c_int32)]


class SysPlain(ctypes.Structure):
    """A register whose reads the C hook does (sysplain_t): VALUE and MEMORY hold while epoch is
    the state's plain_epoch, NO_MODEL always; UNKNOWN asks Python."""
    _fields_ = [("address", ctypes.c_uint64), ("value", ctypes.c_uint64), ("epoch", ctypes.c_int64),
                ("size", ctypes.c_int32), ("model", ctypes.c_int32), ("kind", ctypes.c_int32),
                ("used", ctypes.c_int32)]


class SysState(ctypes.Structure):
    """The system run's clock and what its block hook decides with (sys_t in fasthooks.c)."""
    _fields_ = [("clock", ctypes.c_int64), ("progress", ctypes.c_int64), ("spin_check", ctypes.c_int64),
                ("next_event", ctypes.c_int64), ("limit", ctypes.c_int64), ("spin", ctypes.c_int64),
                ("chunk", ctypes.c_int64), ("frozen", ctypes.c_int32), ("due", ctypes.c_int32), ("irq_check", ctypes.c_int32),
                ("observers", ctypes.c_int32), ("memo", ctypes.c_int32), ("memo_n", ctypes.c_int32),
                ("memo_regs", ctypes.c_int32 * 4), ("memo_bits", ctypes.c_uint32 * 4),
                ("memo_vals", ctypes.c_uint32 * 4),
                ("known", Set), ("entry_set", Set), ("entered", Set),
                ("excl_lo", ctypes.c_void_p), ("excl_hi", ctypes.c_void_p), ("excl_n", ctypes.c_uint64),
                ("reg_read", ctypes.c_void_p), ("python", ctypes.c_void_p), ("fast", ctypes.c_int64),
                ("stretch_from", ctypes.c_int64), ("loop_max", ctypes.c_int64), ("anchor_next", ctypes.c_int64),
                ("waited", ctypes.c_int64), ("anchor", ctypes.c_uint64), ("resume", ctypes.c_uint64),
                ("waiting", ctypes.c_int32), ("nregs", ctypes.c_int32),
                ("regs", ctypes.c_int32 * SYS_REGS), ("anchor_vals", ctypes.c_uint32 * SYS_REGS),
                # the device accesses (dread_sysio)
                ("io_python", ctypes.c_void_p), ("reg_write", ctypes.c_void_p),
                ("mem_read", ctypes.c_void_p), ("mem_write", ctypes.c_void_p),
                ("access", ctypes.c_int64), ("settle", ctypes.c_int32), ("status_reg", ctypes.c_int32),
                ("it_mask", ctypes.c_uint32), ("io_done", ctypes.c_int32),
                ("tail_read", ctypes.c_int32), ("tail_model", ctypes.c_int32), ("tail_size", ctypes.c_int32),
                ("tail_address", ctypes.c_uint64), ("tail_value", ctypes.c_uint64),
                ("tail_extra", ctypes.c_uint64),
                ("last_new", ctypes.c_int64), ("last_access", ctypes.c_int64),
                ("reads", ctypes.POINTER(SysRead)), ("reads_n", ctypes.c_uint64), ("reads_cap", ctypes.c_uint64),
                ("plain_index", Set), ("plain", ctypes.POINTER(SysPlain)), ("plain_n", ctypes.c_uint64),
                ("plain_cap", ctypes.c_uint64), ("plain_epoch", ctypes.c_int64),
                ("plain_used", ctypes.POINTER(ctypes.c_uint64)), ("plain_used_n", ctypes.c_uint64),
                ("io_fast", ctypes.c_int64)]


class SysHooks:
    """The C block hook of one system run (dread.sysrun.System): blocks it cannot do alone go to
    `python`, a callable(address, size), which System._on_block serves."""

    def __init__(self, engine, python):
        import unicorn.unicorn_py3.unicorn as U
        from unicorn import UC_HOOK_BLOCK
        self.lib, self.uclib, self.engine = library(), U.uclib, engine
        lib = self.lib
        lib.dread_sys_size.restype = ctypes.c_size_t
        if lib.dread_sys_size() != ctypes.sizeof(SysState):
            raise OSError("system state layout differs (%d against %d bytes)" % (
                lib.dread_sys_size(), ctypes.sizeof(SysState)))
        lib.dread_sys_entered.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        lib.dread_sys_entered.restype = ctypes.c_size_t
        lib.dread_sys_entry.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        lib.dread_sys_plain.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_int32, ctypes.c_int32]
        lib.dread_sys_plain.restype = ctypes.c_int64
        lib.dread_sys_free.argtypes = [ctypes.c_void_p]
        self.state = s = SysState()
        s.next_event = NO_EVENT
        s.reg_read = ctypes.cast(self.uclib.uc_reg_read, ctypes.c_void_p)
        s.last_new = -1
        # The callbacks are kept alive here and given to the state as plain addresses: through a ctypes
        # cast the state kept a reference to them, and through them to the System, which the
        # finalizer below, holding the state, then kept alive for ever (every system run leaked).
        self.callback = BLOCK_CB(lambda uc, address, size, user: python(address, size))
        s.python = ctypes.cast(self.callback, ctypes.c_void_p).value
        self.io_callback = None
        self.ranges = None
        self._hook(UC_HOOK_BLOCK, lib.dread_sysblock, 1, 0)
        # the memory the C side allocates for the state, freed with it (no hook runs after the run)
        weakref.finalize(self, lib.dread_sys_free, ctypes.byref(s))

    def _hook(self, kind, function, begin, end):
        handle = ctypes.c_ulong()
        status = self.uclib.uc_hook_add(self.engine._uch, ctypes.byref(handle), kind,
                                        ctypes.cast(function, ctypes.c_void_p),
                                        ctypes.cast(ctypes.byref(self.state), ctypes.c_void_p), begin, end)
        if status:
            raise OSError("uc_hook_add failed (%d)" % status)

    def add_io(self, ranges, python, access, settle=None):
        """The device accesses of [(lo, hi), ...] through the C hook (dread_sysio): the reads it can
        do alone (plain_entry), every other access to `python`, a callable(access type, address,
        size, value), which System._io_from_c serves. settle: (status register, IT mask) when every
        access must clear a stale IT state (dread.arch.arm.settle_it), else None."""
        from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
        s = self.state
        self.io_callback = IO_CB(lambda uc, kind, address, size, value, user: python(kind, address, size, value))
        s.io_python = ctypes.cast(self.io_callback, ctypes.c_void_p).value    # (see __init__)
        s.reg_write = ctypes.cast(self.uclib.uc_reg_write, ctypes.c_void_p)
        s.mem_read = ctypes.cast(self.uclib.uc_mem_read, ctypes.c_void_p)
        s.mem_write = ctypes.cast(self.uclib.uc_mem_write, ctypes.c_void_p)
        s.access = access
        if settle is not None:
            s.settle, s.status_reg, s.it_mask = 1, settle[0], settle[1]
        for lo, hi in ranges:
            self._hook(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.lib.dread_sysio, lo, hi - 1)

    def plain_entry(self, address, size, model):
        """The index of the C hook's entry for reads of (address, size), made (UNKNOWN) when there
        is none; model: its index in the run's models, -1 for none. -1: no memory for it."""
        return self.lib.dread_sys_plain(ctypes.byref(self.state), address, size, model)

    def set_state_registers(self, registers):
        """The registers the loop search of a stretch compares (more than SYS_REGS: Python compares)."""
        s = self.state
        if len(registers) > SYS_REGS:
            s.nregs = -1
            return
        for i, reg in enumerate(registers):
            s.regs[i] = reg
        s.nregs = len(registers)

    def set_excluded(self, ranges):
        """The code that is not shared, as sorted [(lo, hi), ...]."""
        n = len(ranges)
        lo, hi = (ctypes.c_uint64 * max(n, 1))(*[a for a, _ in ranges]), (ctypes.c_uint64 * max(n, 1))(*[b for _, b in ranges])
        self.ranges = (lo, hi)
        s = self.state
        s.excl_lo, s.excl_hi, s.excl_n = ctypes.cast(lo, ctypes.c_void_p), ctypes.cast(hi, ctypes.c_void_p), n

    def add_entry(self, address):
        self.lib.dread_sys_entry(ctypes.byref(self.state), address)

    def take_entered(self):
        """[(entry address, entries)] the hook counted since the last call."""
        n = self.state.entered.used
        keys, vals = (ctypes.c_uint64 * max(n, 1))(), (ctypes.c_uint32 * max(n, 1))()
        n = self.lib.dread_sys_entered(ctypes.byref(self.state), keys, vals)
        return [(keys[i], vals[i]) for i in range(n)]

    def set_memo(self, registers, read):
        """The masked answer holds while these (register, bits) read as now."""
        s = self.state
        for i, (reg, bits) in enumerate(registers[:4]):
            s.memo_regs[i], s.memo_bits[i], s.memo_vals[i] = reg, bits, read(reg) & bits
        s.memo_n = min(len(registers), 4)
        s.memo = 1 if len(registers) <= 4 else 0
