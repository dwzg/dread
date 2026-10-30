"""Run single functions of an image in Unicorn, to compare a rewritten function with the original.

Both images (the original and the one with C linked in) are loaded into identical machines, the
same function is called with the same inputs, and what it does must agree: the return value, the
callee-saved registers it hands back, the scratch registers its callers read after the call
(Machine.kept, from dread.regflow), every byte it writes outside its stack, the calls it makes to
stubbed functions, and every access to the peripheral ranges, in order.

Only memory is modelled: the target's regions ([memory] in dread.toml), the image at its base with
what the startup copies (its `startup` list), the stack, a page for the return sentinel. There are
no devices: a peripheral register behaves like memory, and every read and write of one is logged
in order with its value, so the sequence of register accesses is part of what is compared. A run's
first IO_LOGGED accesses are logged one by one; the later ones of each stretch between two other
entries (a stub call, an svc) are folded into one entry, ("io folded", their number, a digest of
them), where that stretch ends: two logs are equal exactly when the accesses are (a digest of 128
bits), and the memory a run takes stays bounded (a long written case that read a device register
over and over kept every access as a tuple, and one worker of a lift check grew by gigabytes,
2026-10-23). Result.diff() names the first entry where two logs part (log_difference).

One emulator per image and process (Core), reset between uses: creating an emulator and loading
an image costs more than most functions take to run. Every hook goes through Machine.hook_add, so
that the reset removes it, and the translation cache is flushed at every reset, so that results do
not depend on the order in which cases run.

Architecture: ARM (A and M profile, ARM state and Thumb) through dread.arch.
"""
import hashlib
import struct

from unicorn import (UC_ERR_FETCH_UNMAPPED, UC_ERR_READ_UNMAPPED, UC_ERR_WRITE_UNMAPPED, UC_HOOK_BLOCK,
                     UC_HOOK_CODE, UC_HOOK_INSN, UC_HOOK_INTR, UC_HOOK_MEM_INVALID, UC_HOOK_MEM_READ,
                     UC_HOOK_MEM_WRITE, UC_MEM_WRITE, Uc, UcError)

from dread.arch import code_address
from dread.util.perthread import PerThread

UNMAPPED = (UC_ERR_READ_UNMAPPED, UC_ERR_WRITE_UNMAPPED, UC_ERR_FETCH_UNMAPPED)

PAGE = 0x1000
POLL_LOGGED = 2     # reads after a Machine.poll_read stage's start that are still logged one by one
IO_LOGGED = 100_000 # device accesses of one run logged one by one; later ones folded (the docstring)
IO_EVENTS = ("io read", "io write", "port read", "port write",   # (port: x86's in and out, Machine._on_port_in)
             "counter read")                                          # (x86's rdtsc, Machine._on_counter)
COUNTER_START = 0x100000000     # the time-stamp counter's first answer is this plus a step (Machine._on_counter)
COUNTER_STEP = 1 << 24          # what it advances by between two reads: about 42 ms of a 400 MHz core, so that a
                                # delay loop ends in a few rounds (0x10000 until 2026-10-27: FreeRTOS's IA32 demo
                                # waits half a second in 64-bit microseconds, which took 3000 rounds of a division
                                # each, and its callers' cases ended at the limit); low 32 bits still change
FOLD_CHUNK = 4096   # folded accesses hashed together (the digest is the same for the same accesses)
POISON = 0xA5C3E100 # callee-saved registers start as POISON + 0x11 * (i + 1): no zero byte, so that
                    # a byte cleared in one of them shows
KEPT_POISON = 0x5AC31E00    # kept scratch registers no argument sets start as KEPT_POISON + 0x11 * (i + 1)
SPIN_PERIOD = 4         # longest cycle of peripheral reads the spin cut recognises
ARGS_ROOM = 0x100   # mapped above the stack top: arguments beyond the registers lie there, as the
                    # caller's frame would hold them (writes there are compared like memory)
RUN_CALLEE = object()   # what a stub returns to let the callee run at this call (Machine.stub)
STUB_ARGS = 4           # the arguments a stub gets at least: the argument registers, then words from the stack


class EmuError(RuntimeError):
    pass


class Layout:
    """What the emulator takes from the target for one image: memory map, image, startup copies,
    stack, sentinel, registers. Built once per image and process."""

    def __init__(self, target, image):
        self.target, self.image = target, image
        self.arch = image.arch
        self.base = image.base
        mem = target.memory
        if mem.stack_top is None:
            raise EmuError("[memory] stack is not set: the emulator needs a stack for called functions")
        if mem.sentinel is None:
            raise EmuError("[memory] sentinel is not set: the emulator needs a return address")
        self.stack_top, self.stack_size = mem.stack_top, mem.stack_size
        self.sentinel = mem.sentinel
        self.io = list(mem.io)
        spans = [(r.base, r.end) for r in mem.regions]
        spans.append((image.base, max(image.limit, image.end)))
        self.room = image.room                          # ([image] room: mapped in both images, filled in one)
        if self.room is not None:
            spans.append(self.room)
        spans += self.io
        spans.append((self.stack_top - self.stack_size, self.stack_top + ARGS_ROOM))
        spans.append((self.sentinel, self.sentinel + 4))
        if mem.scratch_base is not None:
            spans.append((mem.scratch_base, mem.scratch_base + mem.scratch_size))
        for c in image.startup():
            spans.append((c.run, c.run + c.size))
        self.maps = self._merge(spans)
        abi = target.abi
        self.arg_regs = [self.arch.reg_id(r) for r in abi.arg_regs]
        # what a system call's handler reads, where the target says ([abi] syscall_reads): logged at each one
        reads = getattr(abi, "syscall_reads", None)
        self.syscall_regs = None if reads is None else [self.arch.reg_id(r) for r in reads]
        self.saved_regs = [self.arch.reg_id(r) for r in abi.callee_saved]
        self.saved_names = list(abi.callee_saved)
        self.result_regs = [self.arch.reg_id(r) for r in abi.result]
        self.all_regs = [self.arch.reg_id(r) for r in self.arch.general_regs()]
        self.sp, self.pc = self.arch.reg_id("sp"), self.arch.reg_id("pc")
        # The register a call leaves the return address in; None where the call pushes it (MSP430:
        # the layer's push_return and pop_return then set up a call and return from a stub)
        self.lr = self.arch.reg_id(self.arch.link_reg) if self.arch.link_reg else None
        self.word = self.arch.word                      # the size of a stack argument
        # sp to the first stack argument at a function's entry: the return address a call pushes lies
        # between on x86 (MSP430's is two bytes, which its layer does not say yet: its stubs read the
        # stack from sp itself, as before)
        self.entry_stack_args = getattr(self.arch, "entry_stack_args", 0)
        self.fixed = [(self.arch.reg_id(r), v) for r, v in sorted(image.fixed.items())]     # [image] fixed
        # Cores that can have IT blocks need every hook to clear a stale IT state (Arm.settle_it)
        from dread.program import read_code_ranges
        self.settle_it = self.arch.profile == "m" or any(
            isa == "t32" for _, _, isa in read_code_ranges(image.table("code_ranges"), self.arch.isa,
                                                            self.arch.isas()))

    @staticmethod
    def _merge(spans):
        """Page-aligned, merged ranges covering every span (Unicorn maps whole pages)."""
        pages = sorted(((lo & ~(PAGE - 1)), (hi + PAGE - 1) & ~(PAGE - 1)) for lo, hi in spans if hi > lo)
        out = []
        for lo, hi in pages:
            if out and lo <= out[-1][1]:
                out[-1] = (out[-1][0], max(out[-1][1], hi))
            else:
                out.append((lo, hi))
        return out

    def in_stack(self, address):
        return self.stack_top - self.stack_size <= address < self.stack_top

    def free_page(self, address):
        """The page of address where the target maps nothing in it (no region, nor the image, IO, the
        stack, the sentinel, scratch memory, a startup copy): a case may map it for its run alone
        (Machine.map_page). None where something is mapped there."""
        page = address & ~(PAGE - 1)
        if any(lo < page + PAGE and page < hi for lo, hi in self.maps):
            return None
        return page

    def payload_spans(self, payload):
        """[(address, bytes)]: the parts of an image's payload that are loaded. A rebuilt image with C
        in the image's room ([image] room) reaches up to the room's end: only its image and the room
        are loaded, not the filler between (other memory there: RAM the firmware uses)."""
        if self.room is not None and self.base + len(payload) > self.room[0]:
            span = max(self.image.limit, self.image.end) - self.base
            return [(self.base, payload[:span]),
                    (self.room[0], payload[self.room[0] - self.base:self.room[1] - self.base])]
        return [(self.base, payload)]

    def initial(self, payload):
        """[(address, bytes)]: what memory holds before any code runs."""
        out = self.payload_spans(payload)
        for c in self.image.startup():
            if c.kind == "load":                # the loader's bytes, not in the image
                out.append((c.run, c.data))
            elif c.kind != "zero":
                lo = c.stored - self.base
                out.append((c.run, payload[lo:lo + c.size]))
        return out


def log_difference(a, b):
    """Where two logs of stub calls and device accesses part, for people: the first entry that
    differs, with the one before it (the whole logs can be long)."""
    i = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    before = " after %s" % (a[i - 1],) if i else ""
    mine = "%s" % (a[i],) if i < len(a) else "the end"
    theirs = "%s" % (b[i],) if i < len(b) else "the end"
    return "stub calls differ: entry %d of %d against %d%s: %s vs %s" % (i + 1, len(a), len(b), before, mine, theirs)


class Result:
    """What one call did: return value, bytes written outside the stack (address -> value), the
    log of stub calls and peripheral accesses, the callee-saved registers at the return, and the
    kept scratch registers at the return ((name, value) pairs; why: name -> who reads it)."""

    def __init__(self, ret, writes, calls, saved=(), layout=None, kept=(), why=None):
        # writes: {address: byte}, or [(start, bytes), ...] as Machine.call takes them (the
        # dictionary is then built when someone asks for it: a large copy writes megabytes)
        self.ret, self.calls, self.saved, self.layout = ret, calls, saved, layout
        if isinstance(writes, dict):
            self._writes, self._ranges = writes, None
        else:
            self._writes, self._ranges = None, [(lo, bytes(data)) for lo, data in writes]
        self.kept, self.why = tuple(kept), why or {}

    @property
    def writes(self):
        """{address: byte} of everything written outside the stack."""
        if self._writes is None:
            self._writes = {}
            for lo, data in self._ranges:
                self._writes.update(zip(range(lo, lo + len(data)), data))
        return self._writes

    @property
    def ranges(self):
        """The same as [(start, bytes), ...], contiguous runs in address order."""
        if self._ranges is None:
            self._ranges, run = [], None
            for a in sorted(self._writes):
                if run is not None and a == run[0] + len(run[1]):
                    run[1].append(self._writes[a])
                else:
                    run = [a, bytearray([self._writes[a]])]
                    self._ranges.append(run)
            self._ranges = [(lo, bytes(data)) for lo, data in self._ranges]
        return self._ranges

    def differences(self, other):
        """What differs from another result, every item named, as diff() decides: "return",
        "calls", ("saved", register), ("kept", register), ("memory", address); empty when they
        agree. (dread.runner compares the differences of two pairs of results.)"""
        keys = set()
        in_stack = self.layout.in_stack if self.layout else (lambda a: False)
        if self.ret != other.ret and not all(in_stack(r & 0xFFFFFFFF) for r in (self.ret, other.ret)):
            keys.add("return")
        if self.calls != other.calls:
            keys.add("calls")
        names = self.layout.saved_names if self.layout else ["r%d" % (i + 4) for i in range(len(self.saved))]
        keys |= {("saved", n) for n, a, b in zip(names, self.saved, other.saved) if a != b}
        mine, theirs = dict(self.kept), dict(other.kept)
        keys |= {("kept", n) for n in set(mine) | set(theirs) if mine.get(n) != theirs.get(n)}
        if self.ranges != other.ranges:
            keys |= {("memory", a) for a in set(self.writes) | set(other.writes)
                     if self.writes.get(a) != other.writes.get(a)}
        return keys

    def diff(self, other):
        """Readable differences to another result; empty when they agree."""
        out = []
        # Two different addresses inside the stack are not a difference: a function without a
        # result leaves in r0 whatever its last callee returned, which may be the address of a
        # local, and locals lie elsewhere in the frame of the C. What is at that address is
        # compared like all memory.
        in_stack = self.layout.in_stack if self.layout else (lambda a: False)
        both_stack = all(in_stack(r & 0xFFFFFFFF) for r in (self.ret, other.ret))
        if self.ret != other.ret and not both_stack:
            out.append("return value 0x%x vs 0x%x" % (self.ret, other.ret))
        if self.calls != other.calls:
            out.append(log_difference(self.calls, other.calls))
        if self.saved != other.saved:
            names = self.layout.saved_names if self.layout else ["r%d" % (i + 4) for i in range(len(self.saved))]
            for name, a, b in zip(names, self.saved, other.saved):
                if a != b:
                    out.append("%s after the return: 0x%x vs 0x%x" % (name, a, b))
        if self.kept != other.kept:
            mine, theirs = dict(self.kept), dict(other.kept)
            for name in sorted(set(mine) | set(theirs)):
                a, b = mine.get(name), theirs.get(name)
                if a != b:
                    out.append("%s after the return: %s vs %s (%s)" % (
                        name, "-" if a is None else "0x%x" % a, "-" if b is None else "0x%x" % b,
                        self.why.get(name) or other.why.get(name) or "read by callers after the call"))
        for addr in (() if self.ranges == other.ranges else sorted(set(self.writes) | set(other.writes))):
            a, b = self.writes.get(addr), other.writes.get(addr)
            if a != b:
                out.append("memory 0x%x: %s vs %s" % (addr, a, b))
                if len(out) > 8:
                    out.append("...")
                    break
        return out


def core_reader(uc):
    """A function that reads one 32-bit core register of `uc`: Unicorn's C function called through
    ctypes with a buffer made once (0.6 us a read against the Python binding's 1.3; a stub call
    reads several, and a loop case's search makes millions), or the engine's own reg_read where
    that is not at hand (DREAD's engines, another binding). Unicorn is pinned at 2.1.4."""
    try:
        import ctypes
        import unicorn.unicorn_py3.unicorn as binding
        library = getattr(binding, "uclib", None) or getattr(binding, "_uc")
        function, handle = library.uc_reg_read, uc._uch
    except (ImportError, AttributeError):
        return uc.reg_read
    buffer = ctypes.c_uint64(0)
    ref = ctypes.byref(buffer)

    def read(reg):
        if function(handle, reg, ref):
            return uc.reg_read(reg)         # an error: the binding raises it as it would
        return buffer.value & 0xFFFFFFFF
    return read


def core_writer(uc):
    """The writing half of core_reader: write(reg, value) of a 32-bit core register through Unicorn's
    C function (a buffer made once), or the engine's own reg_write."""
    try:
        import ctypes
        import unicorn.unicorn_py3.unicorn as binding
        library = getattr(binding, "uclib", None) or getattr(binding, "_uc")
        function, handle = library.uc_reg_write, uc._uch
    except (ImportError, AttributeError):
        return uc.reg_write
    buffer = ctypes.c_uint64(0)
    ref = ctypes.byref(buffer)

    def write(reg, value):
        buffer.value = value & 0xFFFFFFFF
        if function(handle, reg, ref):
            uc.reg_write(reg, value & 0xFFFFFFFF)   # an error: the binding raises it as it would
    return write


class Core:
    """One emulator per image, reused for every call. A Machine borrows the Core of its image; the
    Core undoes what the previous Machine left (written pages, hooks, registers)."""

    cores = PerThread()  # this thread's: (id(layout), id(payload)) -> Core, least recently used first
    KEEP = 4            # emulators kept (the tests use two); more grow memory without bound
    retired = 0         # cores taken out of use (retire), for reading

    def __init__(self, layout, payload):
        self.layout, self.payload = layout, payload
        self.uc = uc = layout.arch.new_uc()
        self.read32 = core_reader(uc)       # the core registers, fast (Machine.reg, the stubs)
        self.write32 = core_writer(uc)
        self.stub_hooks = {}                # address -> hook, kept across Machines (stub_hook)
        for lo, hi in layout.maps:
            uc.mem_map(lo, hi - lo)
        self.initial = layout.initial(payload)
        for start, data in self.initial:
            uc.mem_write(start, data)
        self.pages, self.dirty, self.hooks, self.machine = {}, set(), [], None
        self.extra = []                     # (base, size) of pages a case mapped for its run (Machine.map_page)
        self.budget_hook = None     # fasthooks.dread_budget, kept across resets (Machine.call)
        # The hot hooks run in C when dread.fasthooks builds (stores here; coverage and load tracing
        # per Machine); otherwise in Python. Both record the same.
        from dread import fasthooks
        self.fast = fasthooks.Hooks(uc, layout) if fasthooks.usable(uc) else None
        self.fast_hooks = []                # per Machine, removed at the reset
        if self.fast is not None:
            self.fast.add(UC_HOOK_MEM_WRITE, "dread_write")
        else:
            uc.hook_add(UC_HOOK_MEM_WRITE, self._on_write)
        for lo, hi in layout.io:
            uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self._on_io, begin=lo, end=hi - 1)
        ports = getattr(layout.arch, "port_instructions", None)
        if ports:                           # (x86's in and out: a port space beside memory)
            uc.hook_add(UC_HOOK_INSN, self._on_port_in, None, 1, 0, ports[0])     # (unicorn 2.1: aux1)
            uc.hook_add(UC_HOOK_INSN, self._on_port_out, None, 1, 0, ports[1])
        sites = getattr(layout.arch, "counter_sites", None)
        if sites is not None:               # (x86's rdtsc: Unicorn hooks no rdtsc as an instruction)
            for start, data in self.initial:
                for site in sites(bytes(data), start):
                    uc.hook_add(UC_HOOK_CODE, self._on_counter, begin=site, end=site)
        # Every Machine's own two hooks, added once (Unicorn's binding takes about 20 us to add a
        # hook): they hand over to the Machine that runs. The fault hook is a memory hook and clears
        # a stale IT state behind it, as Machine.hook_add does.
        on_unmapped = self._on_unmapped
        if layout.settle_it:
            settle = layout.arch.settle_it

            def on_unmapped(uc, *args, inner=on_unmapped, settle=settle):
                result = inner(uc, *args)
                settle(uc)
                return result
        uc.hook_add(UC_HOOK_MEM_INVALID, on_unmapped)
        uc.hook_add(UC_HOOK_INTR, self._on_intr)
        # The CPU as built, put back by every reset: a run can change CPU state that no register
        # write reaches. A FreeRTOS run on Unicorn's Cortex-M33 executed a bxns (code it had
        # overwritten) and left the core in Non-secure state; from then on every fetch raised a
        # SecureFault (CPU exception 3) at the first instruction of every call. The masks, stack
        # pointers and stack limits of both security states, CONTROL, the FP registers and the
        # exclusive monitor are of the same kind. A context goes back only into the engine it came
        # from: it holds that engine's heap pointers (the MPU's region arrays).
        self.clean = uc.context_save() if isinstance(uc, Uc) else None

    def _on_write(self, uc, access, addr, size, value, user):
        self.dirty.add(addr >> 12)
        self.dirty.add((addr + size - 1) >> 12)
        self.machine._on_write(uc, access, addr, size, value, user)
        if self.layout.settle_it:
            self.layout.arch.settle_it(uc)

    def _on_io(self, uc, access, addr, size, value, user):
        self.machine._on_io(uc, access, addr, size, value, user)
        if self.layout.settle_it:
            self.layout.arch.settle_it(uc)

    def _on_port_in(self, uc, port, size, user):
        return self.machine._on_port_in(uc, port, size)

    def _on_port_out(self, uc, port, size, value, user):
        self.machine._on_port_out(uc, port, size, value)

    def _on_counter(self, uc, address, size, user):
        self.machine._on_counter(uc, address)

    def page(self, number):
        """The initial contents of one page."""
        if number not in self.pages:
            start, data = number << 12, bytearray(PAGE)
            for base, content in self.initial:
                lo, hi = max(start, base), min(start + PAGE, base + len(content))
                if lo < hi:
                    data[lo - start:hi - start] = content[lo - base:hi - base]
            self.pages[number] = bytes(data)
        return self.pages[number]

    def _on_unmapped(self, uc, access, addr, size, value, user):
        return self.machine._on_unmapped(uc, access, addr, size, value, user)

    def _on_intr(self, uc, intno, user):
        return self.machine._on_intr(uc, intno, user)

    def stub_hook(self, addr):
        """The code hook of a stub at addr (Machine.stub). It stays across Machines: a loop case's
        search stubs the same callees in tens of thousands of runs, and adding a hook takes about 20 us
        in Unicorn's binding. prune_stub_hooks takes away the hooks of addresses the Machine about to
        run does not stub, so that a callee runs as it did without them."""
        if addr not in self.stub_hooks:
            self.stub_hooks[addr] = self.uc.hook_add(UC_HOOK_CODE, self._on_stub, begin=addr, end=addr)

    def prune_stub_hooks(self, stubs):
        for addr in [a for a in self.stub_hooks if a not in stubs]:
            self.uc.hook_del(self.stub_hooks.pop(addr))

    def _on_stub(self, uc, addr, size, user):
        m = self.machine
        if addr in m.stubs:
            m._on_stub(uc, addr, size, user)

    def reset(self):
        if self.fast is not None:
            from dread import fasthooks
            self.dirty.update(self.fast.items(fasthooks.PAGES))
            for which in (fasthooks.PAGES, fasthooks.WRITES, fasthooks.BLOCKS, fasthooks.TREE, fasthooks.READS):
                self.fast.clear(which)
            for h in self.fast_hooks:
                self.fast.delete(h)
            self.fast_hooks = []
        for lo, size in self.extra:         # a case's pages of its own: gone for the next Machine
            self.dirty.difference_update(range(lo >> 12, (lo + size) >> 12))
            self.uc.mem_unmap(lo, size)
        self.extra = []
        for number in self.dirty:
            try:
                self.uc.mem_write(number << 12, self.page(number))
            except UcError:
                pass        # a faulting write to unmapped memory is reported to the hook too
        self.dirty.clear()
        for h in self.hooks:
            self.uc.hook_del(h)
        self.hooks = []
        # Translated code has the hooks of its time compiled in: a block translated while an
        # address had no stub runs past a stub added later. Every Machine starts with an empty
        # translation cache.
        self.uc.ctl_flush_tb()
        if self.clean is not None:
            self.uc.context_restore(self.clean)     # every register as built (r0-r12 are 0 there)
        else:
            for reg in self.layout.all_regs:
                self.uc.reg_write(reg, 0)

    def retire(self):
        """Take this core out of use: the next Machine for its image gets a new one (about a
        millisecond to build), after a run that ends in a CPU exception or an invalid instruction.
        A backstop for CPU state outside the context the reset restores: the state that broke a
        core was inside it (a FreeRTOS run had stored over its own code and executed a bxns, which
        left Unicorn's Cortex-M33 in Non-secure state; every later fetch faulted, and the worker
        process's generations all failed, 149 of a lift check's 175 failures). The Machine that
        made the run keeps the core it ran on."""
        key = (id(self.layout), id(self.payload))
        if Core.cores.get(key) is self:
            del Core.cores[key]
            Core.retired += 1

    @classmethod
    def get(cls, layout, payload):
        key = (id(layout), id(payload))
        core = cls.cores.pop(key, None)
        if core is None or core.payload is not payload or core.layout is not layout:
            core = cls(layout, payload)
        else:
            core.reset()
        cls.cores[key] = core
        while len(cls.cores) > cls.KEEP:
            del cls.cores[next(iter(cls.cores))]
        return core


class Machine:
    """One call of one function in one image: set up memory and stubs, then call().

    entries: {(target file, image name): the function entries}, set by a runner that wants stub
    addresses checked against the function table (a stub at an address that is not a function's
    entry never runs, and the case then compares less than it seems to). Keyed by the image, so that
    a machine for another image is not checked against the wrong table."""

    entries = {}

    def __init__(self, layout, payload, aliases=None, isa_of=None):
        self.layout = layout
        self.arch = layout.arch
        # original entry -> address the C version was linked at, for functions that moved
        self.aliases = aliases or {}
        self.isa_of = isa_of or (lambda address: layout.arch.isa)
        self.core = Core.get(layout, payload)
        self.core.machine = self
        self.uc = self.core.uc
        self.written_ranges = None  # [(lo, hi), ...] written outside the stack by the last call
        self.stubs, self.calls, self.written = {}, [], set()
        self._io_logged, self._fold = 0, None
        self.unalias = {}           # address a moved function is stubbed at -> its original entry
        self.wide_result = False    # also compare the second result register
        self.may_hang = False       # running into the instruction limit is a result, not an error
        self.may_fault = False      # a data access to unmapped memory ends the run as a result (a case's fault)
        self.sysregs = {}           # {name: value} system registers set before each call (a case's sysregs)
        self.fault_at = None        # the instruction such an access ended the last call at
        self.io_fixed = None        # {byte address: value}: peripheral reads return these, see _on_io
        self.ports = {}             # {port: value}: what x86's in reads there (a case's ports), see _on_port_in
        self.port_reads = set()     # the ports the run read with in (dread.autocase takes them for inputs)
        self.counter_reads = 0      # the time-stamp counter's reads so far, see _on_counter
        self.polls = {}             # see poll_read()
        self.laters = {}            # see later_read()
        self.arg_domains = []       # [(register number, values)] applied by call()
        self.function = ""          # the name of the function under test, for stubs that look it up
        self.kept = ()              # scratch registers compared at the return (dread.regflow)
        self._fast_cover = self._fast_reads = False
        self.spin_limit = 0         # reads of one register with one value in a row that stop the run
        self.spinning = None        # (address, size, value) of the register a stopped run spun on
        self._reads, self._cycle = [], [0] * (SPIN_PERIOD + 1)
        self.kept_why = {}          # register -> who reads it after the call, for the report
        self.core_names = frozenset(self.arch.general_regs()) | {"sp", "pc", "lr"}     # 32 bits (reg)
        self.unmapped = None        # (access, address) of the access to unmapped memory a fault came from
        self.timed_out = False      # the last call ran into its instruction limit
        self.self_loop = None       # where it did so on a branch to itself (self_loop_at)
        self.stack_preset = set()   # stack bytes call() wrote for the call itself (a pushed return address)
        self.stack_shift = 0        # bytes below the stack top where call() starts the stack (dread.runner)
        self.stale_fill = None      # (byte, span, entries): at every block that starts at one of the
                                    # entries, span bytes below sp (down to the stack's bottom) become
                                    # byte: what earlier frames left there is gone (dread.runner)
        # (the hooks of _on_unmapped and _on_intr are the Core's, added once)
        # A system call (svc, ecall) runs as a no-op, logged in order: its handler is the firmware's and
        # not modelled (FreeRTOS's RISC-V port yields with ecall), logged with the registers the target
        # says it reads ([abi] syscall_reads; none where it does not say). Any other CPU exception stops
        # the run as a fault (with a hook in place, Unicorn would take an undefined instruction as handled).
        self.svc_regs, self.exception = getattr(layout, "syscall_regs", None) or False, None

    # ---- hooks ----

    def hook_add(self, kind, callback, **where):
        """Add an emulator hook that lasts as long as this Machine. On cores with IT blocks a
        memory hook ends by clearing a stale IT state (Arm.settle_it): Unicorn restores the state of
        an instruction's start for a memory hook only, not for a code, block or interrupt hook (a
        stub's at a function's entry, where no IT block is open; the repair there took an eighth of
        a loop case search)."""
        if self.layout.settle_it and kind & ~(UC_HOOK_INTR | UC_HOOK_CODE | UC_HOOK_BLOCK):
            inner, settle = callback, self.arch.settle_it

            def callback(uc, *args, inner=inner, settle=settle):
                result = inner(uc, *args)
                settle(uc)
                return result
        self.core.hooks.append(self.uc.hook_add(kind, callback, **where))

    def watch_calls(self, program, sites, anywhere=False):
        """Record in self.wild_calls [(site, target)] the calls through a register at `sites`
        ({address: size}) whose target lies in the image but is no function's entry: the middle of a
        function, or data such as the vector table; or in the scratch, where the generated inputs'
        buffers lie (a callback the input pointed at a buffer: the compare-guided stage's chase set
        FreeRTOS's _write to one, and __sflush_r's run went on through data; anywhere: any address
        that is no function's entry). A stub's address is fine (the harness's pointer stub lies in
        the scratch). The target is the first block behind the call; a call to the
        next instruction (an svc that runs on) is none. Not followed: a call through a veneer (bl to a
        bx rN stub) reaches its target a block later. An input that points a callback into the
        middle of a function runs code the rebuilt image has otherwise, or calls it with other values
        in the other registers (dread.runner refuses such a case; dread.pilot such an input)."""
        state = {"site": None}
        self.wild_calls = []

        def at_site(uc, address, size, user):
            state["site"] = address

        mem = self.layout.target.memory
        scratch = (mem.scratch_base, mem.scratch_base + mem.scratch_size) if mem.scratch_base is not None else (0, 0)

        def on_block(uc, address, size, user):
            site, state["site"] = state["site"], None
            if site is None or address == site + sites[site] or address in program.by_entry or address in self.stubs:
                return
            if anywhere or program.in_image(address) or scratch[0] <= address < scratch[1]:
                self.wild_calls.append((site, address))

        for address in sites:
            self.hook_add(UC_HOOK_CODE, at_site, begin=address, end=address)
        self.hook_add(UC_HOOK_BLOCK, on_block)

    def _on_write(self, uc, access, addr, size, value, user):
        if not self.layout.in_stack(addr):
            self.written.update(range(addr, addr + size))

    def _on_io(self, uc, access, addr, size, value, user):
        """Peripheral registers are plain memory, but every access is logged in order. With
        spin_limit, reads that repeat one cycle of up to SPIN_PERIOD (register, value) pairs that
        many times in a row (no peripheral write between) stop the run and set spinning, with the
        last register read: a wait that cannot end by itself (period 1: a status bit polled; period
        2: status and data read in turn while the data never ends the line)."""
        if access == UC_MEM_WRITE:
            self.log("io write", addr, size, value & ((1 << 8 * size) - 1))
            self._reads, self._cycle = [], [0] * (SPIN_PERIOD + 1)
            return
        if self.io_fixed is not None:
            self._poke(uc, addr, bytes(self.io_fixed.get(addr + i, 0) for i in range(size)))
        poll, logged = self.polls.get(addr), True
        if poll:
            poll["count"] += 1
            n = poll["count"]
            value = None
            for at, v in poll["stages"]:
                if at <= n:
                    value = v
            if value is not None:
                self._poke(uc, addr, (value & ((1 << 8 * size) - 1)).to_bytes(size, "little"))
            logged = n <= poll["stages"][-1][0] + POLL_LOGGED     # later ones are counted; call() logs the total
        if not (logged or self.spin_limit):
            return
        got = int.from_bytes(uc.mem_read(addr, size), "little")
        if logged:
            self.log("io read", addr, size, got)
        if self.spin_limit:
            seen, cycle = self._reads, self._cycle
            seen.append((addr, got))
            if len(seen) > 2 * SPIN_PERIOD:
                del seen[0]
            for period in range(1, SPIN_PERIOD + 1):
                if len(seen) > period and seen[-1] == seen[-1 - period]:
                    cycle[period] += 1
                    if cycle[period] >= self.spin_limit * period:
                        self.spinning = (addr, size, got)
                        uc.emu_stop()
                        break
                else:
                    cycle[period] = 0

    def _on_unmapped(self, uc, access, addr, size, value, user):
        if self.unmapped is None:
            self.unmapped = (access, addr)
        return False                        # the access faults as before

    def _on_port_in(self, uc, port, size):
        """x86's in. The port space is no memory: a read returns the case's value for the port (ports,
        cut to the access's size), 0 for any other port whatever was written there, and is logged in
        order as a device access, as a peripheral register's read is (_on_io). Unicorn ran in as 0
        and out as nothing, unlogged, until 2026-10-27."""
        value = self.ports.get(port, 0) & ((1 << 8 * size) - 1)
        self.port_reads.add(port)
        self.log("port read", port, size, value)
        return value

    def _on_port_out(self, uc, port, size, value):
        """x86's out: logged in order as a device access (_on_io's io write)."""
        self.log("port write", port, size, value & ((1 << 8 * size) - 1))

    def _on_counter(self, uc, address):
        """x86's rdtsc: the n-th read of a run answers COUNTER_START + n * COUNTER_STEP, logged as a device
        access. Unicorn answers with the host's clock, and a delay loop on it (FreeRTOS's IA32 demo: the
        I2C driver waits 10000 ticks between polls) ran as many rounds as the host's speed allowed: the
        same input ran a different number of instructions in the generator and in the runner. Both
        images read the counter in the same order, so they get the same answers."""
        self.counter_reads += 1
        value = (COUNTER_START + COUNTER_STEP * self.counter_reads) & 0xFFFFFFFFFFFFFFFF
        self.log("counter read", value)
        self.layout.arch.answer_counter(uc, address, value)

    def poll_read(self, addr, n, value):
        """From the n-th read (1 is the first) of the register at addr on, until a later stage,
        reads return value, whatever the code wrote there. For a poll loop that calls nothing, so
        that no stub can end it. Reads beyond POLL_LOGGED after the last stage's start are counted,
        not logged one by one; call() ends its log with ("io poll reads", addr, total), so the
        number of reads must still agree. Opt-in: it changes the input, it compares no less."""
        poll = self.polls.setdefault(addr, {"stages": [], "count": 0})
        poll["stages"] = sorted(poll["stages"] + [(n, value)])

    def _poke(self, uc, addr, data):
        """A write from a hook (a poll stage, a later stage, a fixed peripheral value): its pages are
        marked for the reset like the code's own writes, or the next case on this emulator started
        from what this one left."""
        self.core.dirty.update(range(addr >> 12, ((addr + len(data) - 1) >> 12) + 1))
        uc.mem_write(addr, data)

    def map_page(self, base, size=PAGE):
        """Map size bytes at base, zero-filled, for this Machine's run alone (the next reset unmaps
        them): a case's "map" (dread.autocase: the page of a data access its run faulted at, so that the
        code behind the access runs, in the original and in the rebuilt image alike). Refused (EmuError)
        where the target maps anything there."""
        base &= ~(PAGE - 1)
        size = (size + PAGE - 1) & ~(PAGE - 1)
        if (base, size) in self.core.extra:
            return
        if size <= 0 or any(lo < base + size and base < hi for lo, hi in self.layout.maps) or \
                any(lo < base + size and base < lo + n for lo, n in self.core.extra):
            raise EmuError("a case's page at 0x%x: the target or another page maps memory there" % base)
        if not hasattr(self.uc, "mem_map"):
            raise EmuError("a case's page at 0x%x: this engine maps no memory for a case" % base)
        self.uc.mem_map(base, size)
        self.uc.mem_write(base, bytes(size))
        self.core.extra.append((base, size))

    def later_read(self, addr, size, n, value):
        """Just before the n-th read that starts at addr (1 is the first), memory there becomes
        value (size bytes): what another context (a task, an interrupt handler) wrote there while a
        recorded call waited (dread sysrecord). Unlike a poll stage it is written once, so the
        code's own writes stand after it. Opt-in: it changes the input, it compares no less."""
        later = self.laters.get(addr)
        if later is None:
            later = self.laters[addr] = {"stages": {}, "count": 0}
            self.hook_add(UC_HOOK_MEM_READ, self._on_later, begin=addr, end=addr)
        later["stages"][n] = (size, value)

    def _on_later(self, uc, access, addr, size, value, user):
        later = self.laters.get(addr)
        if later is None:
            return
        later["count"] += 1
        stage = later["stages"].get(later["count"])
        if stage is not None:
            s, v = stage
            self._poke(uc, addr, (v & ((1 << 8 * s) - 1)).to_bytes(s, "little"))

    def _on_budget(self, uc, addr, size, user):
        """The run's limit and its stall rule without the C helper (fasthooks.dread_budget, alike)."""
        self._budget -= size
        if self._stall and not self._stalled:
            if addr not in self._seen:
                self._seen.add(addr)
                self._stall_left = self._stall
            else:
                self._stall_left -= size
                if self._stall_left <= 0:
                    self._stalled = True
                    uc.emu_stop()
                    return
        if self._budget <= 0 < self._budget + size:
            uc.emu_stop()

    def _on_stub(self, uc, addr, size, user):
        read = self.core.read32
        args = tuple(read(r) for r in self.layout.arg_regs)
        if len(args) < STUB_ARGS:           # (cdecl passes every argument on the stack: a stub gets its
            args += self.stack_args(STUB_ARGS - len(args))      # first ones from there)
        ret = self.stubs[addr](self, *args)
        if ret is RUN_CALLEE:               # the stub lets the callee run at this call (a case that
            return                          # answers some of its calls only: dread.autocase)
        if getattr(self, "stopped_at", None):
            # The stub ended the run at this call (a stop, a loop case's last stub call): no return
            # from it, so the run ends at the callee's entry however the caller got there (behind a
            # tail jump in the original, lr is the harness's own return and a return went there)
            return
        write = self.core.write32
        if isinstance(ret, tuple):          # (r0, r1): a stub of a function with a wide result
            for reg, v in zip(self.layout.result_regs, ret):
                write(reg, v or 0)
        else:
            write(self.layout.result_regs[0], ret or 0)
        if self.layout.lr is not None:
            write(self.layout.pc, read(self.layout.lr))     # bit 0 of lr keeps Thumb state
        else:
            self.arch.pop_return(uc, self.read)

    def stack_args(self, n, first=0):
        """The stack arguments first .. first + n - 1 of the function whose entry the run stands at, as
        words (0 for one that cannot be read)."""
        lay = self.layout
        base = self.core.read32(lay.sp) + lay.entry_stack_args + lay.word * first
        out = []
        for k in range(n):
            try:
                out.append(int.from_bytes(self.read(base + lay.word * k, lay.word), "little"))
            except Exception:
                out.append(0)
        return tuple(out)

    def stub(self, addr, fn=lambda m, *a: 0):
        """Replace the function at addr with a callable(machine, *args) -> result: args are the call's
        argument registers, and where the convention has fewer than STUB_ARGS (cdecl: none), the
        first stack arguments after them.

        The callable records what matters about the call with machine.log(); raw pointers into the
        stack differ between builds, so it should log what they point to. A callable that returns
        RUN_CALLEE lets the function run at that call, as if no stub were there (and of a moved
        function, the stub at its C is then hit too, through the branch at its old entry)."""
        addr = code_address(self.arch, addr)
        known = Machine.entries.get((self.layout.target.file, self.layout.image.name))
        if known is not None and self.layout.base <= addr < self.layout.image.end and addr not in known:
            raise EmuError("stub at 0x%x, which is not the entry of a function" % addr)
        self.stubs[addr] = fn
        self.core.stub_hook(addr)
        moved = self.aliases.get(addr)
        if moved:       # C code calls the moved function directly, not through its old entry
            self.unalias[moved] = addr
            self.stubs[moved] = fn
            self.core.stub_hook(moved)

    def _narrow_to(self, addr, moved, isa):
        """Whether the narrow b at addr (a function too small for the branch to its C, dread.overlay)
        leads to moved, directly or through a trampoline that branches there."""
        target_of = getattr(self.arch, "short_branch_target", None)
        if target_of is None:
            return False
        via = target_of(self.read(addr, 2), addr, isa)
        if via is None:
            return False
        if via == moved:
            return True
        try:
            hop = self.arch.entry_branch(via, moved, isa)
        except ValueError:
            return False
        return self.read(via, len(hop)) == hop

    def cover(self, begin, end, tree=False):
        """Record the blocks executed in [begin, end): covered_insns() turns them into
        instruction addresses (block granularity: a conditional instruction that does not
        execute counts as executed). With tree, also the start of every block executed outside
        that range, in self.tree (what the function's callees ran: the generator's signal for an
        input that gets further into a callee); one hook does both. self.last_block is the
        (address, size) of the last block run inside the range: when a run ends elsewhere, its
        last instruction is the call or jump that left for good."""
        self.blocks, self.tree, self.last_block = set(), set(), None
        fast = self.core.fast
        if fast is not None:
            fast.state.cover_lo, fast.state.cover_hi, fast.state.tree = begin, end, 1 if tree else 0
            fast.state.last_block = 0
            self.core.fast_hooks.append(fast.add(UC_HOOK_BLOCK, "dread_block", *((1, 0) if tree else (begin, end - 1))))
            self._fast_cover = True
            return
        blocks, outside = self.blocks, self.tree

        def inside(uc, addr, size, user):
            blocks.add((addr, size))
            self.last_block = (addr, size)
        if not tree:
            self.hook_add(UC_HOOK_BLOCK, inside, begin=begin, end=end - 1)
            return

        def on_block(uc, addr, size, user):
            if begin <= addr < end:
                inside(uc, addr, size, user)
            else:
                outside.add(addr)
        self.hook_add(UC_HOOK_BLOCK, on_block)

    def covered_insns(self, insns):
        """The addresses of `insns` (instruction addresses) that lie in executed blocks. (A lifted
        Body lists its own instructions before the shared code, which may lie in front of them.) After
        a fault that ended a call as its result (may_fault), the instructions behind the faulting one in
        its block are left out: they did not run (when that block also ran whole earlier in the call,
        they are left out too: counted short, never long)."""
        import bisect
        insns = sorted(insns)
        out = set()
        fault = getattr(self, "fault_at", None)
        for addr, size in getattr(self, "blocks", ()):
            i = bisect.bisect_left(insns, addr)
            while i < len(insns) and insns[i] < addr + size:
                if fault is not None and addr <= fault < addr + size and insns[i] > fault:
                    break
                out.add(insns[i])
                i += 1
        return out

    def trace_reads(self, image_lo, image_hi, code=frozenset(), owners=None):
        """Record {address: size} of every load outside the stack and the image, and in
        self.code_reads whether code was loaded as data: an instruction (an address in `code`), or,
        with owners (dread.program.Program.extent_owners), a byte of a function's extent loaded by
        an instruction of another function (a function's own literal pools are its constants)."""
        self.reads = {}
        self.code_reads = False
        if owners is not None:              # (its table reaches over the room behind the image)
            image_hi = max(image_hi, image_lo + 2 * len(owners))
        fast = self.core.fast
        if fast is not None:
            fast.set_code(code, image_lo, image_hi, owners, self.layout.pc)
            self.core.fast_hooks.append(fast.add(UC_HOOK_MEM_READ, "dread_read"))
            self._fast_reads = True
            return
        in_stack = self.layout.in_stack
        pc_reg = self.layout.pc

        def owner(a):
            h = (a - image_lo) >> 1
            return owners[h] if owners is not None and 0 <= h < len(owners) else 0

        def on_read(uc, access, addr, size, value, user):
            if image_lo <= addr < image_hi:
                if any(a in code for a in range(addr & ~1, addr + size, 2)):
                    self.code_reads = True
                elif owner(addr) and owner(uc.reg_read(pc_reg) & ~1) != owner(addr):
                    self.code_reads = True
            elif not in_stack(addr):
                self.reads.setdefault(addr, size)
        self.hook_add(UC_HOOK_MEM_READ, on_read)

    @property
    def written(self):
        """The addresses the last call wrote outside the stack (from written_ranges when the C
        hooks recorded them)."""
        if self._written is None:
            self._written = {a for lo, hi in (self.written_ranges or ()) for a in range(lo, hi)}
        return self._written

    @written.setter
    def written(self, value):
        self._written = value

    def _read_written(self):
        """[(start, bytes), ...] of what the call wrote outside the stack, one read per contiguous
        run; sets written_ranges [(lo, hi), ...] (the C hooks give them merged already)."""
        if self.written_ranges is None:
            ranges, addrs, i = [], sorted(self._written or ()), 0
            while i < len(addrs):
                j = i
                while j + 1 < len(addrs) and addrs[j + 1] == addrs[j] + 1:
                    j += 1
                ranges.append((addrs[i], addrs[j] + 1))
                i = j + 1
            self.written_ranges = ranges
        return [(lo, self.read(lo, hi - lo)) for lo, hi in self.written_ranges]

    def wrote_into(self, lo, hi):
        """Whether the last call wrote anything in [lo, hi)."""
        ranges = getattr(self, "written_ranges", None)
        if ranges is None:
            return any(lo <= a < hi for a in self.written)
        return any(a < hi and lo < b for a, b in ranges)

    def _collect(self):
        """Take what the C hooks recorded into the attributes the Python hooks fill."""
        fast = self.core.fast
        if fast is None:
            return
        from dread import fasthooks
        self.written_ranges, self._written = fast.ranges(), None
        if self._fast_cover:
            self.blocks = {(k >> 16, k & 0xFFFF) for k in fast.items(fasthooks.BLOCKS)}
            last = fast.state.last_block
            self.last_block = (last >> 16, last & 0xFFFF) if last else None
            self.tree = set(fast.items(fasthooks.TREE))
        if self._fast_reads:
            self.reads = dict(fast.items(fasthooks.READS, values=True))
            self.code_reads = bool(fast.state.code_reads)

    def _on_intr(self, uc, intno, user):
        soft = getattr(self.arch, "software_interrupt", None)    # (x86: int N, told from a fault by its bytes)
        if intno in getattr(self.arch, "syscall_numbers", ()) or (soft is not None and soft(uc, intno)):
            if self.svc_regs:
                regs = self.layout.arg_regs if self.svc_regs is True else self.svc_regs
                self.log("svc", *(uc.reg_read(r) for r in regs))
            else:
                self.log("svc")
            return
        if self.exception is None:
            self.exception = intno
        uc.emu_stop()

    def log_svc(self):
        """Log the argument registers at each system call too (svc, ecall), for a case that knows the
        handler reads them; by default a system call is logged without registers, which may hold
        what earlier code left."""
        self.svc_regs = True

    def log(self, *event):
        """An entry of the run's log (stub calls, device accesses: what is compared in order); device
        accesses beyond the first IO_LOGGED of the run are folded (the module's docstring)."""
        if event and event[0] in IO_EVENTS:
            if self._io_logged >= IO_LOGGED:
                fold = self._fold
                if fold is None:
                    fold = self._fold = [0, hashlib.blake2b(digest_size=16), []]
                fold[0] += 1
                fold[2].append(event)
                if len(fold[2]) >= FOLD_CHUNK:
                    fold[1].update(repr(fold[2]).encode())
                    fold[2] = []
                return
            self._io_logged += 1
        elif self._fold is not None:
            self._end_fold()
        self.calls.append(event)

    def _end_fold(self):
        """The folded stretch ends: its entry goes into the log where the stretch ends."""
        fold, self._fold = self._fold, None
        if fold is not None:
            if fold[2]:
                fold[1].update(repr(fold[2]).encode())
            self.calls.append(("io folded", fold[0], fold[1].hexdigest()))

    # ---- memory ----

    def write(self, addr, data):
        data = bytes(data)
        if data:
            self.core.dirty.update(range(addr >> 12, ((addr + len(data) - 1) >> 12) + 1))
            try:
                self.uc.mem_write(addr, data)
            except UcError:
                raise EmuError("write to unmapped memory at 0x%x (%d bytes)" % (addr, len(data)))

    def read(self, addr, n):
        try:
            return bytes(self.uc.mem_read(addr, n))
        except UcError:
            raise EmuError("read of unmapped memory at 0x%x (%d bytes)" % (addr, n))

    def word(self, addr):
        return struct.unpack("<I", self.read(addr, 4))[0]

    def cstring(self, addr, limit=256):
        return self.read(addr, limit).split(b"\0")[0]

    def reg(self, name, value=None):
        """Read a register, or write it when value is given (names as in dread.arch)."""
        rid = self.arch.reg_id(name)
        if value is None:
            if name in self.core_names:     # a core register: the fast reader (Core.read32)
                return self.core.read32(rid)
            return self.uc.reg_read(rid)
        if name in self.core_names:
            self.core.write32(rid, value)
        else:
            self.uc.reg_write(rid, value & 0xFFFFFFFF)

    # ---- the call ----

    def self_loop_at(self, uc):
        """The address of the instruction a stopped run stands on when it is an unconditional branch
        to itself (for (;;); at the end of a hook or a fault handler), else None. A run that ran into
        its limit there had done all it would ever do, without interrupts, in the original and in the
        C alike: a defined end, though not a return (dread.autocase makes it a "hang" case)."""
        resume = self.arch.resume_address(uc)       # (bit 0: Thumb, on the ARM)
        address = code_address(self.arch, resume)
        thumb = getattr(self.arch, "isa_bit", 1) and resume & 1
        try:
            insn = self.arch.decode(bytes(uc.mem_read(address, getattr(self.arch, "max_insn", 4))), address,
                                    "t32" if thumb else None)
        except Exception:
            return None
        if insn is None:
            return None
        kind, target, _ = self.arch.flow(insn)
        if kind == "jump" and target is not None and code_address(self.arch, target) == address and \
                self.arch.predicate(insn) is None:
            return address
        return None

    def call(self, addr, *args, max_instructions=5_000_000, stall=0):
        """Call the function at addr (its original entry) with args in the argument registers;
        further arguments go into the stack words at the stack top, as the calling convention
        passes them. Returns a Result; raises EmuError on a fault or when the function does not
        return within max_instructions (unless may_hang). With may_fault, a load or store to unmapped
        memory ends the run as a result: the Result's log ends with ("fault", "read" or "write",
        address), and fault_at is the instruction (covered_insns counts nothing behind it in its
        block). stall: a run that enters no block it had
        not entered for that many instructions stops as at the limit (self.stalled; the case
        generator's searches: a run circling in code it knows mostly runs into the limit)."""
        uc, lay = self.uc, self.layout
        isa = self.isa_of(addr)
        self.fault_at, faulted = None, None
        # A moved function that left a branch at its old entry is entered through it, so that the
        # branch is tested with every case; one without is called where it lies now.
        moved = self.aliases.get(addr)
        if moved:
            try:
                expected = self.arch.entry_branch(addr, moved, isa)
            except ValueError:
                expected = None
            if self.read(addr, len(expected or b"\0" * 4)) != expected and not self._narrow_to(addr, moved, isa):
                addr = moved
        if self.arg_domains:
            from dread.cases import confine_args
            args = confine_args(self.arg_domains, args)
        # the registers the first arguments go to: a generated case's own (Generator.apply: x86's eax,
        # edx and ecx for a function that takes them), else the ABI's; asked once, by this call
        names, self.call_regs = getattr(self, "call_regs", None), None
        arg_regs = [self.arch.reg_id(r) for r in names] if names is not None else list(lay.arg_regs)
        regs = list(args[:len(arg_regs)])
        stacked = list(args[len(arg_regs):])
        self.arch.initial_status(uc)            # before sp: on the A profile sp is banked per mode
        for name, value in sorted((self.sysregs or {}).items()):     # (a case's: sysregs, after the reset)
            rid = getattr(self.arch, "sysreg_id", lambda n: None)(name)
            if rid is None:
                raise EmuError("the case gives the system register %s, which this core's model cannot set" % name)
            uc.reg_write(rid, value & 0xFFFFFFFF)
        for reg, value in lay.fixed:            # registers that hold one value in the whole program (gp)
            uc.reg_write(reg, value)
        for reg, value in zip(arg_regs, regs):
            uc.reg_write(reg, value & 0xFFFFFFFF)
        top = lay.stack_top - self.stack_shift
        uc.reg_write(lay.sp, top)
        if self.stack_shift:
            # The room for stack arguments moves with the stack: a stack argument a case gives as a
            # memory cell at the stack top (the generator learns it from the read there) is where the
            # function looks for it. Left behind, every case of a function with stack arguments
            # changed its outcome with the stack moved, and dread test refused those whose images
            # differed instead of failing them (MSP430's udivmodsi4, its stack argument read from
            # the wrong place on purpose: refused, not failed)
            uc.mem_write(top, bytes(uc.mem_read(lay.stack_top, ARGS_ROOM)))
        word = lay.word
        if word * len(stacked) > ARGS_ROOM:
            raise EmuError("more stack arguments than the %d bytes above the stack top hold" % ARGS_ROOM)
        for i, value in enumerate(stacked):
            self.write(top + word * i, (value & ((1 << 8 * word) - 1)).to_bytes(word, "little"))
        if lay.lr is not None:
            uc.reg_write(lay.lr, self.arch.start_address(lay.sentinel, isa))
        else:                               # pushed below the stack arguments, as a call does
            preset = self.stack_preset = set()

            def write(address, data):
                preset.update(range(address, address + len(data)))
                self.write(address, data)
            self.arch.push_return(uc, self.arch.start_address(lay.sentinel, isa), write)
        for i, reg in enumerate(lay.saved_regs):
            if uc.reg_read(reg) == 0:
                uc.reg_write(reg, POISON + 0x11 * (i + 1))
        kept = [(name, None if name == "flags" else self.arch.reg_id(name)) for name in self.kept]
        for i, (name, reg) in enumerate(kept):
            if reg is not None and reg not in arg_regs[:len(regs)]:
                uc.reg_write(reg, KEPT_POISON + 0x11 * (i + 1))
        self.calls, self.written, self.written_ranges = [], set(), None
        self._io_logged, self._fold = 0, None
        self.timed_out, self.exception, self.self_loop = False, None, None
        if self.core.fast is not None:
            from dread import fasthooks
            self.core.fast.clear(fasthooks.WRITES)
        for poll in self.polls.values():
            poll["count"] = 0
        for later in self.laters.values():
            later["count"] = 0
        if self.stale_fill is not None and not getattr(self, "_stale_hooked", False):
            self._stale_hooked = True
            fill, span, entries = self.stale_fill
            bottom = lay.stack_top - lay.stack_size

            def stale(uc, address, size, user):
                if address in entries:
                    sp = uc.reg_read(lay.sp)
                    low = max(bottom, sp - span)
                    if low < sp:
                        uc.mem_write(low, bytes([fill]) * (sp - low))
            self.hook_add(UC_HOOK_BLOCK, stale)
        # The limit: Unicorn's own count calls a hook before every instruction (long runs went three
        # to six times slower), so it is a budget of bytes of code entered instead, kept by a block
        # hook in C (fasthooks.dread_budget) or, without the C helper, in Python, alike: as many
        # bytes as max_instructions of the instruction set's shortest instructions (exactly the old
        # limit for code made of them, a little less for longer ones; with four bytes for Thumb a
        # generation found less: runs that had stopped at the limit went on). DREAD's own engines
        # count instructions themselves.
        count, fast = max_instructions, self.core.fast
        budget = max_instructions * self.arch.min_insn(isa) if max_instructions else 1 << 62
        stall_bytes = stall * self.arch.min_insn(isa) if stall else 0
        self.stalled = False
        if fast is not None:
            if self.core.budget_hook is None:
                self.core.budget_hook = fast.add(UC_HOOK_BLOCK, "dread_budget")
            fast.state.budget, fast.state.budget_hit = budget, 0
            fast.state.stall, fast.state.stall_left, fast.state.stalled = stall_bytes, stall_bytes, 0
            if stall_bytes:
                from dread import fasthooks
                fast.clear(fasthooks.SEEN)
            count = 0
        elif max_instructions and isinstance(uc, Uc):
            self._budget = budget
            self._stall, self._stall_left, self._stalled, self._seen = stall_bytes, stall_bytes, False, set()
            if not getattr(self, "_budget_hooked", False):
                self._budget_hooked = True
                self.hook_add(UC_HOOK_BLOCK, self._on_budget)
            count = 0
        elif hasattr(uc, "stall"):          # DREAD's own engines count instructions themselves
            uc.stall = stall
        self.core.prune_stub_hooks(self.stubs)
        try:
            uc.emu_start(self.arch.start_address(addr, isa), lay.sentinel, count=count)
        except UcError as e:
            errno = getattr(e, "errno", None)
            if errno not in UNMAPPED:       # an invalid instruction (Core.retire)
                self.core.retire()
            if self.may_fault and errno in (UC_ERR_READ_UNMAPPED, UC_ERR_WRITE_UNMAPPED) and self.unmapped is not None:
                faulted = ("read" if errno == UC_ERR_READ_UNMAPPED else "write", self.unmapped[1],
                           code_address(self.arch, uc.reg_read(lay.pc)))
            else:
                raise EmuError("emulation fault at pc=0x%x: %s" % (uc.reg_read(lay.pc), e))
        except Exception as e:              # an exception in a stub or a hook of the case
            raise EmuError("%s in a stub or hook at pc=0x%x: %s" % (type(e).__name__, uc.reg_read(lay.pc), e))
        finally:
            self._collect()
            self._end_fold()
            if fast is not None:
                self.stalled = bool(fast.state.stalled)
            elif isinstance(uc, Uc):
                self.stalled = getattr(self, "_stalled", False)
            else:
                self.stalled = bool(getattr(uc, "stalled", False))
        if self.exception is not None:
            self.core.retire()
            raise EmuError("CPU exception %d at pc=0x%x" % (self.exception, uc.reg_read(lay.pc)))
        writes = self._read_written()
        for register, poll in sorted(self.polls.items()):
            if poll["count"] > poll["stages"][-1][0] + POLL_LOGGED:
                self.calls.append(("io poll reads", register, poll["count"]))
        if faulted is not None:             # (may_fault: the access is the result, compared like a stop)
            self.fault_at = faulted[2]
            return Result(0, writes, list(self.calls) + [("fault", faulted[0], faulted[1])], layout=lay)
        if uc.reg_read(lay.pc) != lay.sentinel:
            self.timed_out = self.spinning is None and not getattr(self, "stopped_at", None)
            self.self_loop = self.self_loop_at(uc) if self.timed_out else None
            if self.may_hang:
                return Result(0, writes, list(self.calls) + [("did not return",)], layout=lay)
            raise EmuError("function at 0x%x did not return within %d instructions" % (addr, max_instructions))
        ret = uc.reg_read(lay.result_regs[0])
        if self.wide_result and len(lay.result_regs) > 1:
            ret |= uc.reg_read(lay.result_regs[1]) << self.arch.reg_bits
        kept_values = [(name, self.arch.flags_value(uc) if reg is None else uc.reg_read(reg)) for name, reg in kept]
        return Result(ret, writes, list(self.calls), tuple(uc.reg_read(r) for r in lay.saved_regs), layout=lay,
                      kept=kept_values, why=self.kept_why)
