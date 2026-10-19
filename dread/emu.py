"""Run single functions of an image in Unicorn, to compare a rewritten function with the original.

Both images (the original and the one with C linked in) are loaded into identical machines, the
same function is called with the same inputs, and what it does must agree: the return value, the
callee-saved registers it hands back, the scratch registers its callers read after the call
(Machine.kept, from dread.regflow), every byte it writes outside its stack, the calls it makes to
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

from unicorn import (UC_HOOK_BLOCK, UC_HOOK_CODE, UC_HOOK_INTR, UC_HOOK_MEM_INVALID, UC_HOOK_MEM_READ,
                     UC_HOOK_MEM_WRITE, UC_MEM_WRITE, Uc, UcError)

PAGE = 0x1000
POLL_LOGGED = 2     # reads after a Machine.poll_read stage's start that are still logged one by one
POISON = 0xA5C3E100 # callee-saved registers start as POISON + 0x11 * (i + 1): no zero byte, so that
                    # a byte cleared in one of them shows
KEPT_POISON = 0x5AC31E00    # kept scratch registers no argument sets start as KEPT_POISON + 0x11 * (i + 1)
SPIN_PERIOD = 4         # longest cycle of peripheral reads the spin cut recognises
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
        self.all_regs = [self.arch.reg_id(r) for r in self.arch.general_regs()]
        self.sp, self.pc = self.arch.reg_id("sp"), self.arch.reg_id("pc")
        # The register a call leaves the return address in; None where the call pushes it (MSP430:
        # the layer's push_return and pop_return then set up a call and return from a stub)
        self.lr = self.arch.reg_id(self.arch.link_reg) if self.arch.link_reg else None
        self.word = self.arch.word                      # the size of a stack argument
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

    def initial(self, payload):
        """[(address, bytes)]: what memory holds before any code runs."""
        out = [(self.base, payload)]
        for c in self.image.startup():
            if c.kind == "load":                # the loader's bytes, not in the image
                out.append((c.run, c.data))
            elif c.kind != "zero":
                lo = c.stored - self.base
                out.append((c.run, payload[lo:lo + c.size]))
        return out


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
        if self.fast is not None:
            from dread import fasthooks
            self.dirty.update(self.fast.items(fasthooks.PAGES))
            for which in (fasthooks.PAGES, fasthooks.WRITES, fasthooks.BLOCKS, fasthooks.TREE, fasthooks.READS):
                self.fast.clear(which)
            for h in self.fast_hooks:
                self.fast.delete(h)
            self.fast_hooks = []
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
        self.unalias = {}           # address a moved function is stubbed at -> its original entry
        self.wide_result = False    # also compare the second result register
        self.may_hang = False       # running into the instruction limit is a result, not an error
        self.io_fixed = None        # {byte address: value}: peripheral reads return these, see _on_io
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
        self.unmapped = None        # (access, address) of the access to unmapped memory a fault came from
        self.timed_out = False      # the last call ran into its instruction limit
        self.stack_preset = set()   # stack bytes call() wrote for the call itself (a pushed return address)
        self.stack_shift = 0        # bytes below the stack top where call() starts the stack (dread.runner)
        self.hook_add(UC_HOOK_MEM_INVALID, self._on_unmapped)
        # A system call (svc, ecall) runs as a no-op, logged in order: its handler is the firmware's and
        # not modelled (FreeRTOS's RISC-V port yields with ecall). Any other CPU exception stops the run
        # as a fault (with a hook in place, Unicorn would take an undefined instruction as handled).
        self.svc_regs, self.exception = False, None
        self.hook_add(UC_HOOK_INTR, self._on_intr)

    # ---- hooks ----

    def hook_add(self, kind, callback, **where):
        """Add an emulator hook that lasts as long as this Machine. On cores with IT blocks the
        hook ends by clearing a stale IT state (Arm.settle_it)."""
        if self.layout.settle_it:
            inner, settle = callback, self.arch.settle_it

            def callback(uc, *args, inner=inner, settle=settle):
                result = inner(uc, *args)
                settle(uc)
                return result
        self.core.hooks.append(self.uc.hook_add(kind, callback, **where))

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
        self._budget -= size
        if self._budget <= 0 < self._budget + size:
            uc.emu_stop()

    def _on_stub(self, uc, addr, size, user):
        args = tuple(uc.reg_read(r) for r in self.layout.arg_regs)
        ret = self.stubs[addr](self, *args)
        if getattr(self, "stopped_at", None):
            # The stub ended the run at this call (a stop, a loop case's last stub call): no return
            # from it, so the run ends at the callee's entry however the caller got there (behind a
            # tail jump in the original, lr is the harness's own return and a return went there)
            return
        if isinstance(ret, tuple):          # (r0, r1): a stub of a function with a wide result
            for reg, v in zip(self.layout.result_regs, ret):
                uc.reg_write(reg, (v or 0) & 0xFFFFFFFF)
        else:
            uc.reg_write(self.layout.result_regs[0], (ret or 0) & 0xFFFFFFFF)
        if self.layout.lr is not None:
            uc.reg_write(self.layout.pc, uc.reg_read(self.layout.lr))  # bit 0 of lr keeps Thumb state
        else:
            self.arch.pop_return(uc, self.read)

    def stub(self, addr, fn=lambda m, *a: 0):
        """Replace the function at addr with a callable(machine, r0, r1, r2, r3) -> result.

        The callable records what matters about the call with machine.log(); raw pointers into the
        stack differ between builds, so it should log what they point to."""
        addr &= ~1
        known = Machine.entries.get((self.layout.target.file, self.layout.image.name))
        if known is not None and self.layout.base <= addr < self.layout.image.end and addr not in known:
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
        """The addresses of `insns` (sorted instruction addresses) that lie in executed blocks."""
        import bisect
        out = set()
        for addr, size in getattr(self, "blocks", ()):
            i = bisect.bisect_left(insns, addr)
            while i < len(insns) and insns[i] < addr + size:
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
        if intno in getattr(self.arch, "syscall_numbers", ()):
            if self.svc_regs:
                self.log("svc", *(uc.reg_read(r) for r in self.layout.arg_regs))
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
                expected = self.arch.entry_branch(addr, moved, isa)
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
        for reg, value in lay.fixed:            # registers that hold one value in the whole program (gp)
            uc.reg_write(reg, value)
        for reg, value in zip(lay.arg_regs, regs):
            uc.reg_write(reg, value & 0xFFFFFFFF)
        top = lay.stack_top - self.stack_shift
        uc.reg_write(lay.sp, top)
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
            if reg is not None and reg not in lay.arg_regs[:len(regs)]:
                uc.reg_write(reg, KEPT_POISON + 0x11 * (i + 1))
        self.calls, self.written, self.written_ranges = [], set(), None
        self.timed_out, self.exception = False, None
        if self.core.fast is not None:
            from dread import fasthooks
            self.core.fast.clear(fasthooks.WRITES)
        for poll in self.polls.values():
            poll["count"] = 0
        for later in self.laters.values():
            later["count"] = 0
        # The limit: Unicorn's own count calls a hook before every instruction (long runs went three
        # to six times slower), so it is a budget of bytes of code entered instead, kept by a block
        # hook in C (fasthooks.dread_budget) or, without the C helper, in Python, alike: as many
        # bytes as max_instructions of the instruction set's shortest instructions (exactly the old
        # limit for code made of them, a little less for longer ones; with four bytes for Thumb a
        # generation found less: runs that had stopped at the limit went on). DREAD's own engines
        # count instructions themselves.
        count, fast = max_instructions, self.core.fast
        budget = max_instructions * self.arch.min_insn(isa) if max_instructions else 1 << 62
        if fast is not None:
            if self.core.budget_hook is None:
                self.core.budget_hook = fast.add(UC_HOOK_BLOCK, "dread_budget")
            fast.state.budget, fast.state.budget_hit = budget, 0
            count = 0
        elif max_instructions and isinstance(uc, Uc):
            self._budget = budget
            if not getattr(self, "_budget_hooked", False):
                self._budget_hooked = True
                self.hook_add(UC_HOOK_BLOCK, self._on_budget)
            count = 0
        try:
            uc.emu_start(self.arch.start_address(addr, isa), lay.sentinel, count=count)
        except UcError as e:
            raise EmuError("emulation fault at pc=0x%x: %s" % (uc.reg_read(lay.pc), e))
        except Exception as e:              # an exception in a stub or a hook of the case
            raise EmuError("%s in a stub or hook at pc=0x%x: %s" % (type(e).__name__, uc.reg_read(lay.pc), e))
        finally:
            self._collect()
        if self.exception is not None:
            raise EmuError("CPU exception %d at pc=0x%x" % (self.exception, uc.reg_read(lay.pc)))
        writes = self._read_written()
        for register, poll in sorted(self.polls.items()):
            if poll["count"] > poll["stages"][-1][0] + POLL_LOGGED:
                self.calls.append(("io poll reads", register, poll["count"]))
        if uc.reg_read(lay.pc) != lay.sentinel:
            self.timed_out = self.spinning is None and not getattr(self, "stopped_at", None)
            if self.may_hang:
                return Result(0, writes, list(self.calls) + [("did not return",)], layout=lay)
            raise EmuError("function at 0x%x did not return within %d instructions" % (addr, max_instructions))
        ret = uc.reg_read(lay.result_regs[0])
        if self.wide_result and len(lay.result_regs) > 1:
            ret |= uc.reg_read(lay.result_regs[1]) << self.arch.reg_bits
        kept_values = [(name, self.arch.flags_value(uc) if reg is None else uc.reg_read(reg)) for name, reg in kept]
        return Result(ret, writes, list(self.calls), tuple(uc.reg_read(r) for r in lay.saved_regs), layout=lay,
                      kept=kept_values, why=self.kept_why)
