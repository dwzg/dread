"""Run single functions of an image in Unicorn, to compare a rewritten function with the original.

Both images (the original and the one with C linked in) are loaded into identical machines, the
same function is called with the same inputs, and what it does must agree: the return value, the
callee-saved registers it hands back, every byte it writes outside its stack, the calls it makes to
stubbed functions, and every access to the peripheral ranges, in order.

Only memory is modelled: the target's regions ([memory] in dread.toml), the image at its base with
what the startup copies (its `startup` list), the stack, a page for the return sentinel. There are
no devices: a peripheral register behaves like memory, and every read and write of one is logged
in order with its value, so the sequence of register accesses is part of what is compared.

One emulator per image and process (Core), reset between uses: creating an emulator and loading
an image costs more than most functions take to run. Every hook goes through Machine.hook_add, so
that the reset removes it, and the translation cache is flushed at every reset, so that results do
not depend on the order in which cases run.

Architecture: ARM (A and M profile, ARM state and Thumb) through dread.arch.
"""
import struct

from unicorn import (UC_HOOK_BLOCK, UC_HOOK_CODE, UC_HOOK_INTR, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE,
                     UC_MEM_WRITE, UcError)

PAGE = 0x1000
POLL_LOGGED = 2     # reads after a Machine.poll_read stage's start that are still logged one by one
POISON = 0xA5C3E100 # callee-saved registers start as POISON + 0x11 * (i + 1): no zero byte, so that
                    # a byte cleared in one of them shows
ARGS_ROOM = 0x100   # mapped above the stack top: arguments beyond the registers lie there, as the
                    # caller's frame would hold them (writes there are compared like memory)


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
        self.saved_regs = [self.arch.reg_id(r) for r in abi.callee_saved]
        self.saved_names = list(abi.callee_saved)
        self.result_regs = [self.arch.reg_id(r) for r in abi.result]
        self.all_regs = [self.arch.reg_id("r%d" % i) for i in range(13)]
        self.sp, self.lr, self.pc = (self.arch.reg_id(r) for r in ("sp", "lr", "pc"))

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

    def initial(self, payload):
        """[(address, bytes)]: what memory holds before any code runs."""
        out = [(self.base, payload)]
        for c in self.image.startup():
            if c.kind != "zero":
                lo = c.stored - self.base
                out.append((c.run, payload[lo:lo + c.size]))
        return out


class Result:
    """What one call did: return value, bytes written outside the stack (address -> value), the
    log of stub calls and peripheral accesses, the callee-saved registers at the return."""

    def __init__(self, ret, writes, calls, saved=(), layout=None):
        self.ret, self.writes, self.calls, self.saved, self.layout = ret, writes, calls, saved, layout

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
            out.append("stub calls differ: %s vs %s" % (self.calls, other.calls))
        if self.saved != other.saved:
            names = self.layout.saved_names if self.layout else ["r%d" % (i + 4) for i in range(len(self.saved))]
            for name, a, b in zip(names, self.saved, other.saved):
                if a != b:
                    out.append("%s after the return: 0x%x vs 0x%x" % (name, a, b))
        for addr in sorted(set(self.writes) | set(other.writes)):
            a, b = self.writes.get(addr), other.writes.get(addr)
            if a != b:
                out.append("memory 0x%x: %s vs %s" % (addr, a, b))
                if len(out) > 8:
                    out.append("...")
                    break
        return out


class Core:
    """One emulator per image, reused for every call. A Machine borrows the Core of its image; the
    Core undoes what the previous Machine left (written pages, hooks, registers)."""

    cores = {}          # (id(layout), id(payload)) -> Core, least recently used first
    KEEP = 4            # emulators kept (the tests use two); more grow memory without bound

    def __init__(self, layout, payload):
        self.layout, self.payload = layout, payload
        self.uc = uc = layout.arch.new_uc()
        for lo, hi in layout.maps:
            uc.mem_map(lo, hi - lo)
        self.initial = layout.initial(payload)
        for start, data in self.initial:
            uc.mem_write(start, data)
        self.pages, self.dirty, self.hooks, self.machine = {}, set(), [], None
        uc.hook_add(UC_HOOK_MEM_WRITE, self._on_write)
        for lo, hi in layout.io:
            uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self._on_io, begin=lo, end=hi - 1)

    def _on_write(self, uc, access, addr, size, value, user):
        self.dirty.add(addr >> 12)
        self.dirty.add((addr + size - 1) >> 12)
        self.machine._on_write(uc, access, addr, size, value, user)

    def _on_io(self, uc, access, addr, size, value, user):
        self.machine._on_io(uc, access, addr, size, value, user)

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

    def reset(self):
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
        for reg in self.layout.all_regs:
            self.uc.reg_write(reg, 0)

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

    entries / image_end: set by a runner that wants stub addresses checked against the function
    table (a stub at an address that is not a function's entry never runs, and the case then
    compares less than it seems to)."""

    entries = None
    image_end = 0

    def __init__(self, layout, payload, aliases=None, isa_of=None):
        self.layout = layout
        self.arch = layout.arch
        # original entry -> address the C version was linked at, for functions that moved
        self.aliases = aliases or {}
        self.isa_of = isa_of or (lambda address: layout.arch.isa)
        self.core = Core.get(layout, payload)
        self.core.machine = self
        self.uc = self.core.uc
        self.stubs, self.calls, self.written = {}, [], set()
        self.unalias = {}           # address a moved function is stubbed at -> its original entry
        self.wide_result = False    # also compare the second result register
        self.may_hang = False       # running into the instruction limit is a result, not an error
        self.io_fixed = None        # {byte address: value}: peripheral reads return these, see _on_io
        self.polls = {}             # see poll_read()
        self.arg_domains = []       # [(register number, values)] applied by call()
        self.function = ""          # the name of the function under test, for stubs that look it up

    # ---- hooks ----

    def hook_add(self, kind, callback, **where):
        """Add an emulator hook that lasts as long as this Machine."""
        self.core.hooks.append(self.uc.hook_add(kind, callback, **where))

    def _on_write(self, uc, access, addr, size, value, user):
        if not self.layout.in_stack(addr):
            self.written.update(range(addr, addr + size))

    def _on_io(self, uc, access, addr, size, value, user):
        """Peripheral registers are plain memory, but every access is logged in order."""
        if access == UC_MEM_WRITE:
            self.log("io write", addr, size, value & ((1 << 8 * size) - 1))
            return
        if self.io_fixed is not None:
            uc.mem_write(addr, bytes(self.io_fixed.get(addr + i, 0) for i in range(size)))
        poll = self.polls.get(addr)
        if poll:
            poll["count"] += 1
            n = poll["count"]
            value = None
            for at, v in poll["stages"]:
                if at <= n:
                    value = v
            if value is not None:
                uc.mem_write(addr, (value & ((1 << 8 * size) - 1)).to_bytes(size, "little"))
            if n > poll["stages"][-1][0] + POLL_LOGGED:
                return      # counted, not logged one by one; call() logs the total
        self.log("io read", addr, size, int.from_bytes(uc.mem_read(addr, size), "little"))

    def poll_read(self, addr, n, value):
        """From the n-th read (1 is the first) of the register at addr on, until a later stage,
        reads return value, whatever the code wrote there. For a poll loop that calls nothing, so
        that no stub can end it. Reads beyond POLL_LOGGED after the last stage's start are counted,
        not logged one by one; call() ends its log with ("io poll reads", addr, total), so the
        number of reads must still agree. Opt-in: it changes the input, it compares no less."""
        poll = self.polls.setdefault(addr, {"stages": [], "count": 0})
        poll["stages"] = sorted(poll["stages"] + [(n, value)])

    def _on_stub(self, uc, addr, size, user):
        args = tuple(uc.reg_read(r) for r in self.layout.arg_regs)
        ret = self.stubs[addr](self, *args)
        if isinstance(ret, tuple):          # (r0, r1): a stub of a function with a wide result
            for reg, v in zip(self.layout.result_regs, ret):
                uc.reg_write(reg, (v or 0) & 0xFFFFFFFF)
        else:
            uc.reg_write(self.layout.result_regs[0], (ret or 0) & 0xFFFFFFFF)
        uc.reg_write(self.layout.pc, uc.reg_read(self.layout.lr))  # bit 0 of lr keeps Thumb state

    def stub(self, addr, fn=lambda m, *a: 0):
        """Replace the function at addr with a callable(machine, r0, r1, r2, r3) -> result.

        The callable records what matters about the call with machine.log(); raw pointers into the
        stack differ between builds, so it should log what they point to."""
        addr &= ~1
        if Machine.entries is not None and self.layout.base <= addr < Machine.image_end and addr not in Machine.entries:
            raise EmuError("stub at 0x%x, which is not the entry of a function" % addr)
        again = addr in self.stubs
        self.stubs[addr] = fn
        if not again:
            self.hook_add(UC_HOOK_CODE, self._on_stub, begin=addr, end=addr)
        moved = self.aliases.get(addr)
        if moved:       # C code calls the moved function directly, not through its old entry
            self.unalias[moved] = addr
            again = moved in self.stubs
            self.stubs[moved] = fn
            if not again:
                self.hook_add(UC_HOOK_CODE, self._on_stub, begin=moved, end=moved)

    def cover(self, begin, end):
        """Record the blocks executed in [begin, end): covered_insns() turns them into
        instruction addresses (block granularity: a conditional instruction that does not
        execute counts as executed)."""
        self.blocks = set()
        self.hook_add(UC_HOOK_BLOCK, lambda uc, addr, size, user: self.blocks.add((addr, size)),
                      begin=begin, end=end - 1)

    def covered_insns(self, insns):
        """The addresses of `insns` (sorted instruction addresses) that lie in executed blocks."""
        import bisect
        out = set()
        for addr, size in getattr(self, "blocks", ()):
            i = bisect.bisect_left(insns, addr)
            while i < len(insns) and insns[i] < addr + size:
                out.add(insns[i])
                i += 1
        return out

    def trace_reads(self, image_lo, image_hi, code=frozenset()):
        """Record {address: size} of every load outside the stack and the image, and in
        self.code_reads whether an instruction (an address in `code`) was loaded as data."""
        self.reads = {}
        self.code_reads = False
        in_stack = self.layout.in_stack

        def on_read(uc, access, addr, size, value, user):
            if image_lo <= addr < image_hi:
                if any(a in code for a in range(addr & ~1, addr + size, 2)):
                    self.code_reads = True
            elif not in_stack(addr):
                self.reads.setdefault(addr, size)
        self.hook_add(UC_HOOK_MEM_READ, on_read)

    def log_svc(self):
        """Let svc instructions run as no-ops and log the argument registers at each. Without
        this an svc is an emulation fault; the firmware's handler is not modelled."""
        self.hook_add(UC_HOOK_INTR, lambda uc, intno, user:
                      self.log("svc", *(uc.reg_read(r) for r in self.layout.arg_regs)))

    def log(self, *event):
        self.calls.append(event)

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
            return self.uc.reg_read(rid)
        self.uc.reg_write(rid, value & 0xFFFFFFFF)

    # ---- the call ----

    def call(self, addr, *args, max_instructions=5_000_000):
        """Call the function at addr (its original entry) with args in the argument registers;
        further arguments go into the stack words at the stack top, as the calling convention
        passes them. Returns a Result; raises EmuError on a fault or when the function does not
        return within max_instructions (unless may_hang)."""
        uc, lay = self.uc, self.layout
        isa = self.isa_of(addr)
        # A moved function that left a branch at its old entry is entered through it, so that the
        # branch is tested with every case; one without is called where it lies now.
        moved = self.aliases.get(addr)
        if moved:
            try:
                expected = self.arch.branch(addr, moved, isa)
            except ValueError:
                expected = None
            if self.read(addr, len(expected or b"\0" * 4)) != expected:
                addr = moved
        if self.arg_domains:
            from dread.cases import confine_args
            args = confine_args(self.arg_domains, args)
        regs = list(args[:len(lay.arg_regs)])
        stacked = list(args[len(lay.arg_regs):])
        self.arch.initial_status(uc)            # before sp: on the A profile sp is banked per mode
        for reg, value in zip(lay.arg_regs, regs):
            uc.reg_write(reg, value & 0xFFFFFFFF)
        uc.reg_write(lay.sp, lay.stack_top)
        if 4 * len(stacked) > ARGS_ROOM:
            raise EmuError("more stack arguments than the %d bytes above the stack top hold" % ARGS_ROOM)
        for i, value in enumerate(stacked):
            self.write(lay.stack_top + 4 * i, struct.pack("<I", value & 0xFFFFFFFF))
        uc.reg_write(lay.lr, self.arch.start_address(lay.sentinel, isa))
        for i, reg in enumerate(lay.saved_regs):
            if uc.reg_read(reg) == 0:
                uc.reg_write(reg, POISON + 0x11 * (i + 1))
        self.calls, self.written = [], set()
        for poll in self.polls.values():
            poll["count"] = 0
        try:
            uc.emu_start(self.arch.start_address(addr, isa), lay.sentinel, count=max_instructions)
        except UcError as e:
            raise EmuError("emulation fault at pc=0x%x: %s" % (uc.reg_read(lay.pc), e))
        except Exception as e:              # an exception in a stub or a hook of the case
            raise EmuError("%s in a stub or hook at pc=0x%x: %s" % (type(e).__name__, uc.reg_read(lay.pc), e))
        writes = {a: self.read(a, 1)[0] for a in self.written}
        for register, poll in sorted(self.polls.items()):
            if poll["count"] > poll["stages"][-1][0] + POLL_LOGGED:
                self.calls.append(("io poll reads", register, poll["count"]))
        if uc.reg_read(lay.pc) != lay.sentinel:
            if self.may_hang:
                return Result(0, writes, list(self.calls) + [("did not return",)], layout=lay)
            raise EmuError("function at 0x%x did not return within %d instructions" % (addr, max_instructions))
        ret = uc.reg_read(lay.result_regs[0])
        if self.wide_result and len(lay.result_regs) > 1:
            ret |= uc.reg_read(lay.result_regs[1]) << 32
        return Result(ret, writes, list(self.calls), tuple(uc.reg_read(r) for r in lay.saved_regs), layout=lay)
