"""The hot emulator hooks in C (fasthooks.c): coverage, load tracing, store tracking and the Thumb IT
repair without a Python call per event.

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

SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fasthooks.c")
BLOCKS, TREE, READS, WRITES, PAGES = range(5)

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
                ("code_reads", ctypes.c_int32)]


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
        if code is not None:
            self.set_code(code, image.base, image.end)
        s.settle = 1 if layout.settle_it else 0
        s.status_reg = layout.arch.status_reg()
        s.it_mask = layout.arch.IT_MASK
        s.reg_read = ctypes.cast(self.uclib.uc_reg_read, ctypes.c_void_p)
        s.reg_write = ctypes.cast(self.uclib.uc_reg_write, ctypes.c_void_p)
        self.fn = {name: ctypes.cast(getattr(self.lib, name), ctypes.c_void_p) for name in
                   ("dread_block", "dread_read", "dread_write")}

    def set_code(self, code, lo, hi):
        """The instruction starts in [lo, hi), for telling loads of code as data (built once per
        set of addresses)."""
        s = self.state
        s.image_lo, s.image_hi = lo, hi
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

    def clear(self, which):
        self.lib.dread_clear(ctypes.byref(self.state), which)
