"""Generate test cases for a function from the original itself, guided by coverage.

For a function whose inputs are registers and plain memory (globals, or structures behind pointers
that the generator aims at scratch memory), cases need not be written by hand. The generator runs
the original with random register values and random contents of every location it is seen to read,
keeps the inputs that reach instructions no earlier input reached, and mutates those further.
Values come from boundary values and from the constants in the function's own instructions, so
that comparisons are hit on both sides.

Stages, each only for what the one before left uncovered:

1. random inputs and mutations of those that gained coverage;
2. mutations of the inputs that reach a conditional branch whose other way was never taken;
3. the compare-guided stage: for such a branch it finds the instruction that set the flags (or the
   compare-and-branch itself), changes every input once to learn which ones the compared values
   depend on, and sets those to what the other side needs (the other operand and its neighbours,
   corrected for a constant offset; bit masks for tests). Among the inputs are what a callee
   leaves in memory ("effects") and what it stores through a pointer to a local ("fills");
4. a boundary sweep that steps each input through the constants and keeps the pairs of values
   between which the result changes.

Inputs that fault, write into the image or load instructions as data are dropped. The test runner
checks afterwards that every instruction ran, as for written cases; a function the generator cannot
cover fails there and needs written cases (the image's extra_cases).

Pointers are learned: a run that faults on unmapped memory just behind the value of an argument or
of a memory word marks that input as a data pointer, and later inputs aim it at a scratch buffer of
its own (most of the time: a null pointer stays possible). On a memory map where small numbers are
no valid addresses (a Cortex-M's) random pointers fault almost always. A run that dies in a jump to
where an input pointed pins that input to the stub for calls through pointers instead.

Callees run for real unless auto() names stubs: calls=((callee, register mask[, stack words]), ...),
callee a name or an address. A callee a run hangs in until the instruction limit (an assertion that
waits for a debugger, a wait for a flag nothing sets) is stubbed in later inputs: each case lists
its stubs, so both images get the same (FreeRTOS's demo: configASSERT's handler spins, and every
null-pointer check stayed uncovered). A generic stub returns a generated value (the first eight calls of a
run, later ones 0) and logs the argument registers in the mask and the stack words, with addresses
inside the stack replaced by the word "stack". Peripheral registers are inputs like memory; with
"io": "fixed" they read what the case says whatever was written.

Waits: a run that reads one peripheral register with one value SPIN_READS times in a row is cut (it
would spin to the instruction limit), and later inputs give that register a poll stage: from the
n-th read on it answers with some bits changed, so the wait ends after a few rounds and its loop is
covered. dread test does not cut runs.

Parsers: the constants of the function's callees (CALLEE_DEPTH levels) join its own, and the strings
and characters they compare against form a dictionary. When a function reads a learned buffer and
the dictionary is not empty, inputs fill the buffer with text made of its tokens (often in the shape
identifier, separator, value), and mutations insert, overwrite or exchange tokens. An input that
runs code in a callee that no input ran before is kept for further mutation (not as a case): a
parser's progress inside the callee that checks a line shows nowhere else.

Generated cases are cached per function (BUILD/autocases/IMAGE/NAME.json), keyed by the function's
bytes, the generator VERSION and the options. A change that alters what the generator produces
needs a VERSION bump; SEED stays, so that a bump does not reshuffle every function's search. The
search ends after a budget of runs and, in its last-resort stage, after LONG_SECONDS without any
input that finished: on a much slower machine that stage can find less.

Case format (also of the image's kept_cases and extra_cases files): docs/formats.md.
"""
import hashlib
import json
import os
import random
import struct
import time

import capstone as cs
from capstone import arm as csarm
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE

from dread.cases import confine_args
from dread.emu import EmuError, Layout, Machine
from dread.program import Program, read_functions

VERSION = 16       # 2: stale Thumb IT state cleared in hooks; 3: data pointers are learned from faults;
                  # 4: callee constants, the dictionary, text in buffers, call-tree coverage;
                  # 5: spins on a peripheral register are cut and answered with poll stages;
                  # 6: learned pointers sometimes misaligned; flag setters known by their mnemonic;
                  # 7: at most MAX_CELLS memory locations per function get values; spins of a cycle
                  # of peripheral reads cut; poll stages may answer with compared constants
                  # 8: a callee a run hung in until the instruction limit is stubbed in later inputs
                  # 9: those stubs log only the argument registers the callee reads (dread.regflow)
                  # 10: a run that hangs in a callee that never returns makes a "hang" case instead
                  # 11: system calls run as logged no-ops (dread.emu), no longer faults;
                  # 12: a run caught in a hook that never returns stubs the callee it went in by;
                  # 13: a load from a function's extent by another function's code is a load of code;
                  # 14: so is one of any halfword of an instruction, and a store behind the image counts;
                  # 15: a callee a run hung in also ends inputs at its call ("stops");
                  # 16: so in the boundary sweep, for a run that hung in one
SEED = 17
DEBUG = bool(os.environ.get("DREAD_AUTOCASE_DEBUG"))
STUB_CALLS = 2000           # stub calls per run; beyond that the run is stopped and counts as not returning
LOOP_CALLS = 48             # a loop case (a function that never returns) ends at this many stub calls
SOLVE_RUNS = 3000           # emulator runs per function in the compare-guided stage ...
SOLVE_COST = 1_500_000      # ... and memory values written for them
SOLVE_EFFECTS = 64          # memory changes by callees examined per starting input
SOLVE_CLIMB = 10            # steps towards a comparison that no single change satisfies
SOLVE_RETURNS = 64          # results of stub calls that are inputs there (a case is generated with 8)
SOLVE_SLOTS = 400           # input values examined per starting input
SOLVE_LIMIT = 20_000        # instructions per run there
SWEEP_RUNS = 600            # emulator runs per base input in the boundary sweep
LONG_SECONDS = 120          # the last-resort search gives up after this long without one input that finished
KEPT_MAX = 6
HUNG_STUBS = 4              # callees at most that get stubbed because runs hung in them
HUNG_STOP = 0.5             # share of fresh inputs in which such a callee ends the run instead ("stops")
POINTER_REACH = 0x1000      # a fault this far behind an input's value makes the input a learned pointer
POINTER_BASE = 0x1000       # learned pointers get buffers from scratch + POINTER_BASE ...
POINTER_STEP = 0x400        # ... this far apart ...
POINTER_SLOTS = 16          # ... at most this many different ones (then they are reused)
POINTER_KEEP = 0.9          # share of inputs that use the buffer (the rest keep their value: null checks)
MAX_CELLS = 512             # memory locations an input gives values to at most (in first-access order):
                            # a run over a long buffer would otherwise make every input carry one value
                            # per byte read (14,000 for a script interpreter given a long length), and
                            # the generator spent its time on those, not in the emulator
POINTER_MISALIGN = 0.25     # share of those that point 1 to 3 bytes into it (alignment-dependent paths:
                            # a memcpy's word loop for equally misaligned pointers)
CALLEE_DEPTH = 2            # callees whose constants and strings join the function's own
TEXT_SHARE = 0.5            # share of fresh inputs whose data buffers hold text from the dictionary
TEXT_MUTATE = 0.3           # share of mutations that change such a text by a token
TEXT_MAX = 96               # bytes of text at most
TEXT_TOKENS = 8             # tokens per text at most
STRING_MAX = 64             # longest string constant taken into the dictionary
STEPPING_MAX = 48           # inputs kept for new code in callees only (mutated further, not cases)
SPIN_READS = 256            # one register read this often in a row with one value: the run spins
SPIN_POLL = 0.5             # share of inputs that get a poll stage for a register runs spun on
# String and memory routines that generated cases do not stub: small, pure, and what they write is
# behaviour of their caller (a stubbed strcpy copies nothing, and a caller that copies one byte too
# many into its frame then agrees with any C).
REAL_LIBRARY = ("memcpy", "memmove", "memset", "memcmp", "strcat", "strcmp", "strcpy", "strlen", "strncmp",
                "strncpy", "strstr", "strchr")



class GenerationError(Exception):
    """No input of the search ran to the end: the function needs written cases."""


class Generator:
    """What generating and applying cases needs about one image; one per image and process."""

    def __init__(self, target, image, program=None, layout=None, payload=None):
        self.target, self.image = target, image
        self.program = program or Program(target, image)
        self.layout = layout or Layout(target, image)
        self._flow = None                   # dread.regflow, when a stub's mask needs it
        self._endless = None                # dread.noreturn, when a run hangs in a callee
        self._environment = None            # see environment()
        self.loop = False                   # generating loop cases (generate(loop=True))
        self.payload = payload if payload is not None else image.payload()
        self.arch = image.arch
        mem = target.memory
        if mem.scratch_base is None:
            raise EmuError("[memory] scratch is not set: generated inputs need scratch memory for their buffers")
        self.scratch = mem.scratch_base
        self.fake_pointer = mem.scratch_base + mem.scratch_size // 2 & ~0xFFF      # stub for calls through pointers
        self.boundary = tuple(range(17)) + (0x7F, 0x80, 0xFF, 0x100, 0x7FFF, 0x8000, 0xFFFF, 0x10000, 0x7FFFFFFF,
                                            0x80000000, 0xFFFFFFFE, 0xFFFFFFFF, self.scratch, self.scratch + 0x40,
                                            self.scratch + 0x104)
        self.code = self.program.instruction_halves()
        self.owners = self.program.extent_owners()  # a load from another function's extent is one of code
        self.entries = {fn.name: fn.entry for fn in self.program.functions}
        self.real = {self.entries[n] for n in REAL_LIBRARY if n in self.entries}
        self.wide = self._wide_results()
        self._domains()
        self.io_fixed, self.long = False, 0
        self.current = None                 # the function whose cases are made or run (domains)
        self.deadline = None

    # ---- tables ----

    def _wide_results(self):
        path = self.image.table("wide_results")
        names = set()
        if os.path.exists(path):
            with open(path) as f:
                names = {l.split()[0] for l in f if l.strip() and not l.startswith("#")}
        return {self.entries[n] for n in names if n in self.entries}

    def _domains(self):
        """The image's domains table: {address: (bytes, values)}, rows NAME:rN+OFF (pointer
        domains), NAME:rN (argument domains) and NAME:ret (stub answers); docs/formats.md."""
        self.domains, self.pointer_domains, self.argument_domains, self.result_domains = {}, {}, {}, {}
        path = self.image.table("domains")
        if not os.path.exists(path):
            return
        with open(path) as f:
            for line in f:
                fields = line.rstrip("\n").split("\t")
                if line.startswith("#") or len(fields) < 4:
                    continue
                values = (tuple(int(x, 0) for x in fields[2].split(",")) if "," in fields[2] or fields[3].strip() == "-"
                          else range(int(fields[2], 0), int(fields[3], 0) + 1))
                where, size = fields[0], int(fields[1])
                if ":" not in where:
                    self.domains[int(where, 0)] = (size, values)
                    continue
                name, place = where.split(":")
                reg, plus, offset = place.partition("+")
                if reg == "ret":
                    if name not in self.entries:
                        raise GenerationError("%s: %s: no function %s" % (path, where, name))
                    self.result_domains[self.entries[name]] = values
                elif reg not in self.layout.target.abi.arg_regs:
                    raise GenerationError("%s: %s: only argument registers can be named" % (path, where))
                elif plus:
                    self.pointer_domains.setdefault(name, []).append((int(reg[1:]), int(offset or "0", 0), size, values))
                else:
                    self.argument_domains.setdefault(name, []).append((int(reg[1:]), values))

    def pointed(self, name, args):
        out = {}
        for reg, offset, size, values in self.pointer_domains.get(name, ()) if name else ():
            if reg < len(args):
                out[(args[reg] + offset) & 0xFFFFFFFF] = (size, values)
        return out

    def confined(self, addr, size, value, extra=None):
        """The value a case may write to a location: itself, one inside a declared domain, or None
        when the write would touch only part of a confined location."""
        table = self.domains
        if extra:
            table = dict(table, **extra)
        if addr in table and table[addr][0] == size:
            values = table[addr][1]
            return values[value % len(values)]
        for a, (n, _) in table.items():
            if a < addr + size and addr < a + n:
                return None
        return value

    # ---- what the code says ----

    def insns(self, words):
        return [i for i in (self.program.insn(a) for a in words) if i is not None]

    def constants(self, words):
        """(exact, near): immediates and literal values of the instructions, and their neighbours.
        Branch targets are left out: as data pointers they would aim writes at code."""
        found = set()
        insns = self.insns(words)
        limits, table = self.arch.switch_hints(insns)
        if table:                       # a jump table: one arm per value up to the compare's bound
            for limit in limits:
                found.update(range(limit + 2))
        for ins in insns:
            isa = self.program.isa_at(ins.address)
            found.update(self.arch.immediates(ins))
            lit = self.arch.literal(ins, isa)
            if lit and lit[1] == 4 and self.program.in_image(lit[0], 4):
                found.add(self.program.word(lit[0]))
        near = set()
        for v in found:
            near.update(x & 0xFFFFFFFF for x in (v + 1, v - 1, ~v, -v))
        return found, near

    def callees(self, words, depth=CALLEE_DEPTH):
        """The functions the instructions call directly, and theirs, `depth` levels deep."""
        seen, level, out = set(), list(words), []
        for _ in range(depth):
            nxt = []
            for ins in self.insns(level):
                if not self.arch.is_call(ins):
                    continue
                target = self.arch.target(ins)
                fn = self.program.by_entry.get(target & ~1) if target is not None else None
                if fn is not None and fn.entry not in seen:
                    seen.add(fn.entry)
                    out.append(fn)
                    nxt += fn.insns
            level = nxt
        return out

    def dictionary(self, words):
        """(strings, characters) the instructions and their callees compare against: the
        NUL-terminated printable strings their literals and pc-relative addresses point at, and the
        printable characters (and tab, line feed, carriage return) in compares with an immediate.
        What fuzzers call a dictionary: parsers check their input against these."""
        strings, chars = [], []
        everything = list(words) + [a for fn in self.callees(words) for a in fn.insns]
        for ins in self.insns(everything):
            isa = self.program.isa_at(ins.address)
            for c in self.arch.compare_chars(ins):
                if (0x20 <= c < 0x7F or c in (9, 10, 13)) and c not in chars:
                    chars.append(c)
            lit = self.arch.literal(ins, isa)
            pointers = [self.program.word(lit[0])] if lit and lit[1] == 4 and self.program.in_image(lit[0], 4) else []
            at = self.arch.pc_address(ins, isa)
            if at is not None:
                pointers.append(at)
            for p in pointers:
                text = self._string_at(p)
                if text and text not in strings:
                    strings.append(text)
        return strings, chars

    def _string_at(self, address):
        if not self.program.in_image(address, 2):
            return None
        out = bytearray()
        for i in range(STRING_MAX + 1):
            if not self.program.in_image(address + i, 1):
                return None
            b = self.program.read(address + i, 1)[0]
            if b == 0:
                return bytes(out) if out else None
            if not (0x20 <= b < 0x7F or b in (9, 10, 13)):
                return None
            out.append(b)
        return None

    def flag_setters(self, words):
        """{conditional branch: (address of the instruction that decides it, reader)}. The reader
        takes the emulator stopped at that instruction and returns (first operand, second operand,
        "test" or "compare"). Which instruction decides a branch, and with which operands, is the
        architecture's (arch.branch_deciders: ARM's flag setters, RISC-V's branch itself)."""
        out = {}
        for branch, (at, first, second, imm, kind, sign) in self.arch.branch_deciders(self.insns(words)).items():
            def reader(uc, first=first, second=second, imm=imm, kind=kind, sign=sign):
                a = uc.reg_read(first) if first is not None else imm
                b = uc.reg_read(second) if second is not None else imm
                return a, (sign * b) & 0xFFFFFFFF, kind
            out[branch] = (at, reader)
        return out

    def conditional_branches(self, words):
        """{address: (target, fall-through)} of the function's conditional direct branches."""
        out = {}
        for ins in self.insns(words):
            kind, target, falls = self.arch.flow(ins)
            if kind == "jump" and falls and target is not None:
                out[ins.address] = (target, ins.address + ins.size)
        return out

    # ---- applying a case ----

    def apply(self, m, case):
        """Put a case into a machine: memory, peripheral answers, generic stubs."""
        lay = self.layout
        fixed = {} if case.get("io") == "fixed" else None
        if case.get("hang"):
            m.may_hang = True
        function = getattr(m, "function", None) or self.current
        m.arg_domains = self.argument_domains.get(function, []) if function else []
        extra = self.pointed(function, confine_args(m.arg_domains, case["args"]))
        confine = bool(self.domains or extra)
        stores = []
        for addr, size, value in case["mem"]:
            value &= (1 << 8 * size) - 1
            if confine:
                value = self.confined(addr, size, value, extra)
                if value is None:
                    continue
            data = value.to_bytes(size, "little")
            stores.append((addr, data))
            if fixed is not None and lay.target.memory.is_io(addr):
                fixed.update((addr + i, b) for i, b in enumerate(data))
        for addr, data in coalesce(stores):
            m.write(addr, data)
        if fixed is not None:
            m.io_fixed = fixed
        for at, nth, value in case.get("poll", ()):
            m.poll_read(at, nth, value)
        for at, size, nth, value in case.get("later", ()):
            m.later_read(at, size, nth, value)
        returns = list(case.get("returns", ()))
        frame = case.get("frame", 0)
        count = [0]
        made = m.stub_calls = [0]
        m.stopped_at = None                 # "calls" or "never returns" when a stub ended the run
        m.ends_at_call = bool(case.get("stops") or (case.get("hang") and case.get("stub_calls")))
        effects = case.get("effects", ())
        fills = case.get("fills", ())
        own = set() if case.get("tight") else None
        if own is not None:
            def on_store(uc, access, addr, size, value, user):
                own.update(range(addr & ~3, addr + size, 4))
            m.hook_add(UC_HOOK_MEM_WRITE, on_store, begin=lay.stack_top - lay.stack_size, end=lay.stack_top - 1)
        in_stack = lay.in_stack

        def stub(m, *args, target=0, nargs=0, nstack=0):
            made[0] += 1
            if made[0] > case.get("stub_calls", STUB_CALLS):
                m.stopped_at = "calls"
                m.uc.emu_stop()
                return 0
            for number, addr, size, v in effects:
                v = self.confined(addr, size, v, extra) if number == made[0] else None
                if v is not None:
                    m.write(addr, v.to_bytes(size, "little"))
            sp = m.reg("sp")
            seen = [args[i] for i in range(len(args)) if nargs >> i & 1] + \
                [int.from_bytes(m.read(sp + 4 * i, 4), "little") for i in range(nstack)]
            m.log("call 0x%x" % target, *("stack" if in_stack(a) else a for a in seen))
            # A callee handed the address of a local usually stores a result there; the stub does
            # too, with generated bytes up to the end of the caller's frame. How far the frame
            # reaches is known only in the original's layout: the original run records the lengths
            # and the run of the rebuilt image replays them.
            for i, a in enumerate(seen):
                count[0] += 1
                if not in_stack(a):
                    continue
                if getattr(m, "replay", False):
                    length = m.plan.pop(0) if m.plan else 0
                else:
                    length = min(frame - (a - sp), 64) if 0 <= a - sp < frame else 0
                    if own is not None:
                        length = next((o for o in range(4 - (a & 3), length, 4) if a + o in own), length)
                    m.plan.append(length)
                if length:
                    m.write(a, random.Random("%s/%d" % (case["args"], count[0])).randbytes(length))
                    if hasattr(m, "stack_written"):
                        m.stack_written.update(range(a, a + length))
                    for number, which, offset, size, v in fills:
                        if number == made[0] and which == i and 0 <= offset and offset + size <= length:
                            m.write(a + offset, (v & ((1 << 8 * size) - 1)).to_bytes(size, "little"))
                    if hasattr(m, "regions"):
                        m.regions.append((made[0], i, a, length))
            answer = returns.pop(0) if returns else 0
            if target in self.result_domains:
                values = self.result_domains[target]
                answer = values[answer % len(values)]
            if target in self.wide and len(lay.result_regs) > 1:
                return answer, (answer * 0x9E3779B1 + 0x7F4A7C15) & 0xFFFFFFFF
            return answer

        for target, nargs, *rest in case.get("calls", ()):
            nstack = rest[0] if rest else 0
            m.stub(target, lambda m, *a, target=target, nargs=nargs, nstack=nstack:
                   stub(m, *a, target=target, nargs=nargs, nstack=nstack))

        def stop(m, *args, target=0):       # a callee that never returns: the run ends at its call
            m.log("call 0x%x, never returns" % target)
            m.stopped_at = "never returns"
            m.uc.emu_stop()
            return 0
        for target in case.get("stops", ()):
            m.stub(target, lambda m, *a, target=target: stop(m, *a, target=target))

    def endless(self):
        """The functions that never return (dread.noreturn), found once."""
        if self._endless is None:
            from dread import noreturn
            self._endless = set(noreturn.find(self.program))
        return self._endless

    def environment(self):
        """A digest of what a function's cases depend on besides its own instructions: the whole
        image (a literal pool holds the addresses of globals and callees: a rebuild that moved the
        data left a function's instructions alike and its cases stale, setting cells nobody reads),
        what the loader puts into RAM, and dread.regflow's version (a hung callee's stub logs what
        it reads)."""
        if self._environment is None:
            from dread import regflow
            h = hashlib.sha1(bytes(self.payload))
            for c in self.image.startup():
                h.update(repr((c.kind, c.run, c.size, c.stored)).encode())
                h.update(bytes(getattr(c, "data", None) or b""))
            h.update(b"regflow %d" % regflow.VERSION)
            self._environment = h.hexdigest()
        return self._environment

    def reads_mask(self, name):
        """The argument registers a function reads at its entry (dread.regflow), as a stub's logging
        mask: the others hold what earlier code left, which a rebuilt caller need not reproduce
        (lifted C clears the scratch registers behind a call; logged, they made every stubbed
        assertion call differ)."""
        if self._flow is None:
            from dread import regflow
            try:
                self._flow = regflow.load(self.target, self.image, self.program)
            except Exception:
                self._flow = False
        if not self._flow:
            return 1
        reads = set(self._flow.read_regs(name))
        return sum(1 << i for i, r in enumerate(self.target.abi.arg_regs) if r in reads)

    def machine(self, payload=None, aliases=None):
        return Machine(self.layout, self.payload if payload is None else payload, aliases, isa_of=self.program.isa_at)

    def touches_image(self, m):
        return m.code_reads or m.wrote_into(self.image.base, self.program.writable_end())

    @staticmethod
    def ends_badly(m):
        """Whether a run of a case that ends at a call (stops, a loop case) ended neither by a return
        nor at that call (at the instruction limit, or spinning on a register): no common place to
        compare it, so it is no case; it may still teach the compare-guided stage."""
        return getattr(m, "ends_at_call", False) and not m.stopped_at and bool(m.timed_out or m.spinning)

    def coverage(self, entry, words, case, limit=200_000, check_image=False):
        """The function's instructions this input executes; empty if it does not finish (or, with
        check_image, when it loads instructions as data or stores into the image)."""
        m = self.machine()
        m.plan = []
        try:
            self.apply(m, case)
            m.cover(words[0], self.program.end(self.program.by_entry[entry]) if entry in self.program.by_entry
                    else words[-1] + 4)
            if check_image:
                m.trace_reads(self.image.base, self.image.end, self.code, self.owners)
            m.call(entry, *case["args"], max_instructions=limit)
        except Exception:
            return set()
        if check_image and (self.touches_image(m) or self.ends_badly(m)):
            return set()
        return m.covered_insns(words)

    # ---- the search ----

    def generate(self, name, entry, words, calls=(), frame=0, count=40, budget=400, limit=200_000, sweep=3,
                 fallbacks=True, seconds=None, loop=False):
        """Cases for one function (see the module's docstring). Raises GenerationError when no
        input runs to the end. fallbacks=False skips the io "fixed" and long-run searches;
        seconds bounds the time (a deadline after which every stage stops; the result then
        depends on the machine's speed, so it is for quick uses such as the integration run).

        loop: the function never returns (a task's loop, a main loop): every callee it calls is
        stubbed, and a run ends at its LOOP_CALLS-th stub call or at a call of a callee that never
        returns ("stops": its stub ends the run), the same place in both images however many
        instructions the C takes; the cases are "hang" cases compared there. A run that ends at the
        instruction limit instead (a path that calls nothing) is no case."""
        before, self.current = self.current, name
        self.deadline = time.time() + seconds if seconds else None
        loop_before, self.loop = self.loop, loop
        try:
            if not fallbacks:
                return self._generate_once(name, entry, words, calls, frame, count, budget, limit, sweep, learn=True)
            return self._generate_all(name, entry, words, calls, frame, count, budget, limit, sweep)
        finally:
            self.current = before
            self.deadline = None
            self.loop = loop_before

    def late(self):
        return self.deadline is not None and time.time() > self.deadline

    def _generate_all(self, name, entry, words, calls, frame, count, budget, limit, sweep):
        args = (name, entry, words, calls, frame, count, budget, limit, sweep)
        try:
            cases = self._generate_once(*args, learn=True)
        except GenerationError:
            try:
                return self._generate_once(*args, learn=False)
            except GenerationError:
                # Nothing ran to the end. Most such functions write a peripheral register and wait
                # for it to change, and memory does not change: registers that read what the case
                # says, whatever was written.
                self.io_fixed = True
                try:
                    return self._generate_once(*args, learn=True)
                except GenerationError:
                    # Still nothing: ten times the instructions a run may take (a loop over a
                    # frame of pixels, a wait of thousands of rounds). Its cases carry the limit.
                    self.long = limit * 10
                    try:
                        return self._generate_once(name, entry, words, calls, frame, count, budget // 4, limit * 10,
                                                   sweep, learn=True)
                    finally:
                        self.long = 0
                finally:
                    self.io_fixed = False
        covered = set()
        for c in cases:
            covered |= self.coverage(entry, words, c, c.get("limit", limit))
        if set(words) <= covered:
            return cases
        try:
            other = self._generate_once(*args, learn=False)
        except GenerationError:
            return cases
        for c in other:
            got = self.coverage(entry, words, c, c.get("limit", limit))
            if got - covered:
                covered |= got
                cases.append(c)
        return cases

    def _generate_once(self, name, entry, words, calls, frame, count, budget, limit, sweep, learn):
        rng = random.Random("%s/%d" % (seed_name(entry), SEED))
        exact, near = self.constants(words)
        deeper = set()
        for fn in self.callees(words):
            deeper |= self.constants(fn.insns)[0]
        strings, chars = self.dictionary(words)
        noise = [rng.getrandbits(32) for _ in range(8)]
        hot = sorted(exact | set(chars) | {0, 1, 2, self.scratch} | set(noise[:3])
                     | {c[0] for c in calls if c[0] == self.fake_pointer})
        pool = sorted(set(self.boundary) | exact | near | deeper | set(chars) | set(noise))
        image = self.image
        end = self.program.end(self.program.by_entry[entry]) if entry in self.program.by_entry else words[-1] + 4
        known, covered, corpus, extras, faults, last_gain = Cells(), set(), [], [], 0, 0
        calls = [list(c) for c in calls]    # grows by the callees runs hang in (hung, below)
        hung = set()
        stops = []                          # loop cases: callees that never return end the run
        if self.loop:
            have = {c[0] for c in calls}
            for fn in self.callees(words, depth=1):
                if fn.entry in have:
                    continue
                if fn.name in self.endless():
                    stops.append(fn.entry)
                else:
                    calls.append([fn.entry, self.reads_mask(fn.name), 0])
        pointers = any(c[0] == self.fake_pointer for c in calls)
        pin_args, pin_mem = set(), set()
        data_args, data_mem = {}, {}        # inputs learned to be data pointers -> their buffer
        words_set = set(words)
        nregs = len(self.layout.arg_regs)

        def buffer_for(slot):
            """A scratch buffer of its own for each learned data pointer, below the stub address."""
            used = len(data_args) + len(data_mem)
            return self.scratch + POINTER_BASE + POINTER_STEP * (used % POINTER_SLOTS)

        def pinned(case):
            for i in pin_args:
                case["args"][i] = self.fake_pointer
            for m_ in case["mem"]:
                if m_[0] in pin_mem:
                    m_[2] = self.fake_pointer
            for i, address in data_args.items():
                if i < len(case["args"]) and rng.random() < POINTER_KEEP:
                    case["args"][i] = address + misalign()
            for m_ in case["mem"]:
                if m_[0] in data_mem and m_[1] == 4 and rng.random() < POINTER_KEEP:
                    m_[2] = data_mem[m_[0]] + misalign()
            return case

        def misalign():
            return rng.randrange(1, 4) if rng.random() < POINTER_MISALIGN else 0

        def learn_pointers(m, case):
            """After a fault on unmapped memory: the inputs whose value lies just in front of the
            address that faulted were pointers."""
            if m.unmapped is None:
                return
            address = m.unmapped[1]
            for i, v in enumerate(case["args"][:nregs]):
                if 0 <= address - v < POINTER_REACH and i not in data_args and i not in pin_args:
                    data_args[i] = buffer_for(("arg", i))
            for a, n, v in case["mem"]:
                if n == 4 and 0 <= address - (v & 0xFFFFFFFF) < POINTER_REACH and a not in data_mem and a not in pin_mem:
                    data_mem[a] = buffer_for(("mem", a))

        def value():
            return rng.choice(hot) if rng.random() < 0.5 else rng.choice(pool)

        def token():
            r = rng.random()
            if strings and r < 0.35:
                return rng.choice(strings)
            if chars and r < 0.7:
                return bytes([rng.choice(chars)])
            if r < 0.9:
                return str(rng.choice((0, 1, 2, 7, 9, 10, 42, 100, 255, 1000, 4096, rng.randrange(100000)))).encode()
            return bytes([rng.randrange(0x20, 0x7F)])

        def buffers(case):
            """Where the learned data buffers this input uses begin for it (a misaligned pointer: a
            few bytes into its buffer)."""
            used = {case["args"][i] for i, address in data_args.items()
                    if i < len(case["args"]) and 0 <= case["args"][i] - address < 4}
            used |= {v for a, n, v in case["mem"] if a in data_mem and n == 4 and 0 <= v - data_mem[a] < 4}
            return sorted(used)

        def text_of(case, buffer):
            mem = {a: v for a, n, v in case["mem"] if n == 1 and buffer <= a < buffer + TEXT_MAX + 1}
            out = bytearray()
            while buffer + len(out) in mem and mem[buffer + len(out)] & 0xFF:
                out.append(mem[buffer + len(out)] & 0xFF)
            return bytes(out)

        def put_text(case, buffer, data):
            data = data[:TEXT_MAX] + b"\0"
            case["mem"] = [m_ for m_ in case["mem"] if not (m_[0] < buffer + len(data) and buffer < m_[0] + m_[1])]
            case["mem"] += [[buffer + i, 1, b] for i, b in enumerate(data)]

        def fresh_text():
            if strings and chars and rng.random() < 0.5:
                # identifier, separator, value: the shape of config lines, commands, queries
                pairs = []
                for _ in range(rng.randint(1, 3)):
                    value_part = token() if rng.random() < 0.3 else str(rng.choice(
                        (0, 1, 2, 3, 7, 9, 10, 100, 255, rng.randrange(100000)))).encode()
                    pairs.append(rng.choice(strings) + bytes([rng.choice(chars)]) + value_part)
                end = bytes([rng.choice(chars)]) if rng.random() < 0.5 else b""
                return end.join(pairs) + (end if rng.random() < 0.5 else b"")
            return b"".join(token() for _ in range(rng.randint(1, TEXT_TOKENS)))

        def mutate_text(old):
            found = [(i, w) for w in strings for i in range(len(old)) if old.startswith(w, i)]
            if found and len(strings) > 1 and rng.random() < 0.3:     # one dictionary string for another
                i, w = rng.choice(found)
                return old[:i] + rng.choice([x for x in strings if x != w]) + old[i + len(w):]
            r, at = rng.random(), rng.randint(0, len(old))
            if r < 0.4:
                return old[:at] + token() + old[at:]                    # insert a token
            if r < 0.7:
                piece = token()
                return old[:at] + piece + old[at + len(piece):]        # overwrite with a token
            if r < 0.85 and old:
                return old[:at] + old[at + 1:]                         # delete a byte
            return fresh_text()

        def fresh():
            case = {"args": [value() for _ in range(nregs)],
                    "mem": [[a, s, value()] for a, s in sorted(known.items())],
                    "returns": [value() for _ in range(8)] if calls else [], "calls": [list(c) for c in calls],
                    "frame": frame, **({"io": "fixed"} if self.io_fixed else {}), **({"limit": self.long} if self.long else {}),
                    **({"hang": True, "limit": limit, "stub_calls": LOOP_CALLS, "stops": list(stops)} if self.loop else {})}
            # A callee a run hung in either returns from its stub (the code behind the call runs) or
            # ends the run at its call: an assertion handler that waits for a debugger, stubbed, let
            # the run go on into the null pointer it was called for, and the run faulted, so the path
            # into the assertion stayed unrun (FreeRTOS: most functions the lifter left uncovered)
            if hung and not self.loop and rng.random() < HUNG_STOP:
                case.update({"hang": True, "limit": limit, "stops": sorted(hung)})
            return case

        spins = {}                          # register -> (size, value) a run spun on

        def add_poll(case):
            """A poll stage for a register a run spun on: from the n-th read on it answers with some
            bits changed (all, one, or a mask the function tests), so the wait can end."""
            address = rng.choice(sorted(spins))
            size, stuck = spins[address]
            full = (1 << 8 * size) - 1
            masks = [full, 1 << rng.randrange(8 * size)] + [c & full for c in sorted(exact) if 0 < c & full < full]
            if exact and rng.random() < 0.5:    # a data register: one of the values the function compares
                answer = rng.choice(sorted(exact)) & full
            else:
                answer = stuck ^ rng.choice(masks)
            stage = [address, rng.choice((1, 2, 3)), answer]
            case["poll"] = [p for p in case.get("poll", ()) if p[0] != address] + [stage]

        def run(case, max_instructions=limit):
            m = self.machine()
            m.plan = []
            m.spin_limit = SPIN_READS
            self.apply(m, case)
            m.cover(words[0], end)
            m.trace_reads(image.base, image.end, self.code, self.owners)
            m.call(entry, *case["args"], max_instructions=max_instructions)
            if self.loop and self.ends_badly(m):
                raise EmuError("a loop case ended neither at a stub call nor by a return")
            return m

        stepping, tree_seen = [], set()     # inputs that only reached new code in callees
        started = time.time()
        for attempt in range(budget * 5):
            patience = budget * 2 if (data_args or data_mem) and (strings or chars) else budget
            if attempt >= budget and (words_set <= covered or attempt - last_gain > patience):
                break
            if self.late():
                break
            if self.long and not corpus and time.time() - started > LONG_SECONDS:
                break
            if (corpus or stepping) and rng.random() < 0.75:
                case = json.loads(json.dumps(rng.choice(corpus + stepping)))
                if not self.loop and not case.get("stops") and case.pop("hang", None):  # (by its own run)
                    if self.long:
                        case["limit"] = self.long
                    else:
                        case.pop("limit", None)
                if hung and len(case["calls"]) < len(calls):        # stubs found since it was made
                    case["calls"] = [list(c) for c in calls]
                    case["returns"] = case["returns"] or [value() for _ in range(8)]
                if hung and not self.loop and not case.get("stops") and rng.random() < HUNG_STOP:
                    case.update({"hang": True, "limit": limit, "stops": sorted(hung)})     # (see fresh)
                present = {a for a, _, _ in case["mem"]}
                case["mem"] += [[a, s, value()] for a, s in sorted(known.items()) if a not in present]
                texts = buffers(case) if strings or chars else ()
                if texts and rng.random() < TEXT_MUTATE:
                    buffer = rng.choice(texts)
                    put_text(case, buffer, mutate_text(text_of(case, buffer)))
                else:
                    for _ in range(rng.choice((1, 1, 2, 3))):
                        slots = [("args", i) for i in range(nregs)] + [("mem", i) for i in range(len(case["mem"]))] \
                            + [("returns", i) for i in range(len(case["returns"]))]
                        kind, i = rng.choice(slots)
                        if kind == "mem":
                            case["mem"][i][2] = value()
                        else:
                            case[kind][i] = value()
                if pin_args or pin_mem or data_args or data_mem:
                    pinned(case)
                if spins and rng.random() < SPIN_POLL / 2:
                    add_poll(case)
            else:
                case = fresh()
                if spins and rng.random() < SPIN_POLL:
                    add_poll(case)
                if pin_args or pin_mem or data_args or data_mem:
                    pinned(case)
                    for buffer in (buffers(case) if strings or chars else ()):
                        if rng.random() < TEXT_SHARE:
                            put_text(case, buffer, fresh_text())
            m = self.machine()
            m.plan = []
            m.spin_limit = SPIN_READS
            self.apply(m, case)
            m.cover(words[0], end, tree=True)
            m.trace_reads(image.base, image.end, self.code, self.owners)
            try:
                m.call(entry, *case["args"], max_instructions=limit)
            except EmuError:
                faults += 1
                if m.spinning:
                    spins.setdefault(m.spinning[0], m.spinning[1:])
                # A run that never came back from a callee. One caught in a function that never
                # returns at all (dread.noreturn: a hook that spins with interrupts off) makes a case of
                # its own, "hang": both images run into it and are compared up to the limit (stubbed,
                # the original would go on behind the call where the lifted C knows better). When the
                # run got there through a callee that does return (an allocator whose failure hook it
                # is), that callee is stubbed in later inputs, so the code behind its call is covered
                # too: stubbing a function that returns cannot make one that never returns return.
                # One that waits for something (an assertion that waits for a debugger, a flag nothing
                # sets) is stubbed itself in later inputs. Each case says which stubs it has, for both
                # images.
                inside = self.program.function_at(m.reg("pc") & ~1) if m.timed_out else None
                if inside is not None and inside.entry != entry:
                    stub = inside
                    if inside.name in self.endless():
                        got = m.covered_insns(words)
                        if got - covered and not self.touches_image(m):
                            case["hang"], case["limit"] = True, limit
                            covered |= got
                            corpus.append(case)
                            last_gain = attempt
                        via = self.left_through(m, words)
                        stub = via if via is not None and via.entry not in (inside.entry, entry) and \
                            via.name not in self.endless() else None
                    if stub is not None and len(hung) < HUNG_STUBS and stub.entry not in hung and \
                            all(c[0] != stub.entry for c in calls):
                        hung.add(stub.entry)
                        calls.append([stub.entry, self.reads_mask(stub.name), 0])
                learn_pointers(m, case)
                if pointers:
                    went = {m.reg("pc") & ~1, m.reg(self.program.arch.veneer_reg)} - {self.fake_pointer, 0}
                    pin_args.update(i for i, v in enumerate(case["args"][:nregs]) if v in went)
                    pin_mem.update(a for a, n, v in case["mem"] if n == 4 and v in went)
                # What the run loaded before it failed is an input all the same: a function whose
                # first inputs all fail (a division by a value from memory, a wait for a flag)
                # never gets any otherwise.
                if learn and len(m.reads) < 4096:
                    for a, n in m.reads.items():
                        if known.full():
                            break
                        try:
                            m.read(a, n)
                        except Exception:
                            continue
                        known.setdefault(a, n)
                continue
            if m.spinning:                      # (a case that ends at a call came back spinning: the
                spins.setdefault(m.spinning[0], m.spinning[1:])     # next inputs get a poll stage)
            if self.touches_image(m) or (self.loop and self.ends_badly(m)):
                faults += 1
                continue
            known.learn(m.reads)
            got = m.covered_insns(words)
            if got - covered:
                covered |= got
                corpus.append(case)
                last_gain = attempt
            elif m.tree - tree_seen and len(stepping) < STEPPING_MAX:
                stepping.append(case)           # further into a callee: worth mutating, not a case
                last_gain = attempt
            elif len(extras) < count:
                extras.append(case)
            tree_seen |= m.tree
            if words_set <= covered and len(extras) >= count:
                break

        # Second stage: inputs that reach the edge of the uncovered code (an executed conditional
        # branch whose other way was never taken), varied one or two values at a time.
        branches = self.conditional_branches(words)
        reach = {}

        def edge():
            return {w for w, (target, fall) in branches.items() if w in covered and (
                (target is not None and target in words_set and target not in covered) or
                (fall in words_set and fall not in covered))}

        if not words_set <= covered and corpus:
            for i, case in enumerate(corpus):
                try:
                    reach[i] = run(case).covered_insns(words)
                except EmuError:
                    continue
            frontier = edge()
            gained = 0
            for attempt in range(budget * 4):
                if words_set <= covered or not frontier or attempt - gained > budget or self.late():
                    break
                bases = [i for i, c in reach.items() if c & frontier]
                if not bases:
                    break
                case = json.loads(json.dumps(corpus[rng.choice(bases)]))
                present = {a for a, _, _ in case["mem"]}
                case["mem"] += [[a, s, value()] for a, s in sorted(known.items()) if a not in present]
                slots = [("args", i) for i in range(nregs)] + [("mem", i) for i in range(len(case["mem"]))] \
                    + [("returns", i) for i in range(len(case["returns"]))]
                for _ in range(rng.choice((1, 1, 1, 2))):
                    kind, i = rng.choice(slots)
                    if kind == "mem":
                        case["mem"][i][2] = value()
                    else:
                        case[kind][i] = value()
                try:
                    m = run(case)
                except EmuError:
                    continue
                if self.touches_image(m):
                    continue
                known.learn(m.reads)
                got = m.covered_insns(words)
                if got - covered:
                    covered |= got
                    corpus.append(case)
                    reach[len(corpus) - 1] = got
                    frontier = edge()
                    gained = attempt

        first_stages = len(corpus)
        if not words_set <= covered and corpus and not self.late():
            self._solve(name, entry, words, end, calls, limit, rng, pool, known, covered, corpus, reach, edge)

        # Boundary sweep: step each value of a few inputs through the constants and their
        # neighbours; where the observable result changes from one value to the next, both sides
        # are kept. A value whose every step changes the result is data and gets a sample only.
        ladder = sorted(exact | near | set(range(17)) | {0xFF, 0x100, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF})
        swept = []

        def sweep_run(case):
            """(machine, result) of one run of the sweep; result None when it did not return (the
            machine says why), machine None when it failed otherwise."""
            m = self.machine()
            m.plan = []
            try:
                self.apply(m, case)
                m.cover(words[0], end)
                m.trace_reads(image.base, image.end, self.code, self.owners)
                return m, m.call(entry, *case["args"], max_instructions=limit)
            except EmuError:
                return (m, None) if m.timed_out else (None, None)
        for base in (corpus[:first_stages][-sweep:] + corpus[first_stages:][-sweep:] if corpus else extras[:1]):
            slots = [("args", i) for i in range(nregs)] + [("mem", i) for i in range(len(base["mem"]))] \
                + [("returns", i) for i in range(len(base["returns"]))]
            if len(slots) * len(ladder) > SWEEP_RUNS:
                slots = rng.sample(slots, max(4, SWEEP_RUNS // len(ladder)))
            for kind, i in slots:
                steps = []
                for v in ladder:
                    case = json.loads(json.dumps(base))
                    if kind == "mem":
                        case["mem"][i][2] = v
                    else:
                        case[kind][i] = v
                    m, r = sweep_run(case)
                    if m is not None and m.timed_out and not case.get("stops") and not self.loop:
                        # It hung in a callee (a null pointer into an assertion that waits for a
                        # debugger: the sweep is where a pointer becomes 0): once more, ending there
                        inside = self.program.function_at(m.reg("pc") & ~1)
                        if inside is not None and inside.entry != entry:
                            case.update({"hang": True, "limit": limit, "stops": [inside.entry]})
                            m, r = sweep_run(case)
                            if m is not None and not m.stopped_at:
                                m = None
                    if m is None or r is None or self.touches_image(m) or self.ends_badly(m):
                        continue
                    got = m.covered_insns(words)
                    if got - covered:
                        covered |= got
                        swept.append(case)
                    steps.append((case, repr((r.ret, r.ranges, r.calls))))
                edges = [j for j in range(1, len(steps)) if steps[j][1] != steps[j - 1][1]]
                if len(edges) > 6:
                    edges = rng.sample(edges, 3)
                for j in edges:
                    swept += [steps[j - 1][0], steps[j][0]]
        seen, unique = set(), []
        for case in swept:
            key = json.dumps(case, sort_keys=True)
            if key not in seen:
                seen.add(key)
                unique.append(case)
        extras = extras[:count // 2] + unique
        if len(corpus) + len(extras) < 4:
            raise GenerationError("%s: only %d of %d generated inputs ran to the end (%d faulted); write cases"
                                  % (name, len(corpus) + len(extras), budget, faults))
        return self.settle_hangs(entry, self.drop_bad_ends(entry, corpus + extras))

    def left_through(self, m, words):
        """The function a run that ended elsewhere left the tested one for: the destination of the
        call or jump that ends the last block it ran inside it (m.last_block); None when that is
        not a direct call or jump."""
        import bisect
        last = getattr(m, "last_block", None)
        if not last:
            return None
        i = bisect.bisect_left(words, last[0] + last[1]) - 1
        if i < 0 or words[i] < last[0]:
            return None
        insn = self.program.insn(words[i])
        if insn is None:
            return None
        kind, target, _ = self.program.arch.flow(insn)
        if kind not in ("call", "jump") or target is None:
            return None
        return self.program.function_at(target)

    def drop_bad_ends(self, entry, cases):
        """The cases without those that should end at a call (stops, loop cases) and end otherwise
        (ends_badly: no common place to compare them). The search keeps them while it runs (one that
        spins in a wait it cannot leave is where the compare-guided stage finds the callee effect
        that ends the wait), the result does not."""
        out = []
        for case in cases:
            if case.get("stops") or (case.get("hang") and case.get("stub_calls")):
                m = self.machine()
                m.plan = []
                m.spin_limit = SPIN_READS
                self.apply(m, case)
                try:
                    m.call(entry, *case["args"], max_instructions=case.get("limit", 200_000))
                except EmuError:
                    continue
                if self.ends_badly(m):
                    continue
            out.append(case)
        return out

    def settle_hangs(self, entry, cases):
        """A case is a "hang" case by its own run, not by the case it was made from: one that returns
        loses the mark (and the limit that came with it)."""
        for case in cases:
            if not case.get("hang"):
                continue
            m = self.machine()
            m.plan = []
            self.apply(m, case)
            m.may_hang = False
            try:
                m.call(entry, *case["args"], max_instructions=case.get("limit", 200_000))
            except EmuError:
                continue                    # it hangs (or faults the same way it did): it stays
            case.pop("hang", None)
            if self.long:
                case["limit"] = self.long
            else:
                case.pop("limit", None)
        return cases

    def _solve(self, name, entry, words, end, calls, limit, rng, pool, known, covered, corpus, reach, edge):
        """The compare-guided stage (see the module's docstring); adds to corpus and covered."""
        image, lay = self.image, self.layout
        words_set = set(words)
        solver = random.Random("%s/%d/solve" % (seed_name(entry), SEED))
        setters = self.flag_setters(words)
        readers = {at: read for at, read in setters.values()}
        runs, cost, total = [0], [0], [0, 0]
        nregs = len(lay.arg_regs)

        def spent():
            return runs[0] >= SOLVE_RUNS or cost[0] >= SOLVE_COST or \
                total[0] >= 4 * SOLVE_RUNS or total[1] >= 4 * SOLVE_COST or self.late()

        def attempt(case, watch=(), record=False):
            runs[0] += 1
            cost[0] += len(case["mem"]) + 50
            total[0] += 1
            total[1] += len(case["mem"]) + 50
            m = self.machine()
            m.plan = []
            self.apply(m, case)
            m.cover(words[0], end)
            m.trace_reads(image.base, image.end, self.code, self.owners)
            seen = {}
            m.after, m.filled = {}, {}
            if record and calls:
                m.regions = []

                def on_read(uc, access, addr, size, value, user):
                    n = m.stub_calls[0]
                    if n and size <= 4 and lay.in_stack(addr):
                        for number, which, a, length in m.regions:
                            if a <= addr and addr + size <= a + length and (number, which, addr - a) not in m.filled:
                                m.filled[number, which, addr - a] = (size, int.from_bytes(uc.mem_read(addr, size), "little"))
                    if n and size <= 4 and (n, addr) not in m.after and not image.base <= addr < image.end \
                            and not lay.in_stack(addr):
                        m.after[n, addr] = (size, int.from_bytes(uc.mem_read(addr, size), "little"))
                m.hook_add(UC_HOOK_MEM_READ, on_read)
            for at in watch:
                m.hook_add(UC_HOOK_CODE, lambda uc, addr, size, user, read=readers[at]:
                           len(seen.setdefault(addr, [])) < 32 and seen[addr].append(read(uc)), begin=at, end=at)
            try:
                m.call(entry, *case["args"], max_instructions=min(limit, SOLVE_LIMIT))
            except EmuError:
                return None, None
            if self.touches_image(m):
                return None, None
            known.learn(m.reads)
            m.covered = m.covered_insns(words)
            return m, seen

        effects, fillable = [], []

        def get(case, slot):
            if slot[0] == "effect":
                return effects[slot[1]][3]
            if slot[0] == "fill":
                return fillable[slot[1]][4]
            return case["mem"][slot[1]][2] if slot[0] == "mem" else case[slot[0]][slot[1]]

        def width(case, slot):
            size = effects[slot[1]][2] if slot[0] == "effect" else fillable[slot[1]][3] if slot[0] == "fill" \
                else case["mem"][slot[1]][1] if slot[0] == "mem" else 4
            return (1 << 8 * size) - 1

        def put(case, slot, v):
            case = json.loads(json.dumps(case))
            if slot[0] == "effect":
                number, addr, size, _ = effects[slot[1]]
                case["effects"] = case.get("effects", []) + [[number, addr, size, v & width(case, slot)]]
            elif slot[0] == "fill":
                number, which, offset, size, _ = fillable[slot[1]]
                case["fills"] = case.get("fills", []) + [[number, which, offset, size, v & width(case, slot)]]
            elif slot[0] == "mem":
                item = case["mem"][slot[1]]
                item[2] = v & 0xFFFFFFFF
                if any(a < item[0] + item[1] and item[0] < a + n for a, n, _ in case["mem"][slot[1] + 1:]):
                    case["mem"].append(list(item))
            else:
                case[slot[0]][slot[1]] = v & 0xFFFFFFFF
            return case

        def keep(m, case):
            known.learn(m.reads)
            if m.covered - covered:
                covered.update(m.covered)
                corpus.append(case)
                reach[len(corpus) - 1] = set(m.covered)
                return True
            return False

        def score(seen, at):
            d = [min((a - b) & 0xFFFFFFFF, (b - a) & 0xFFFFFFFF) for a, b, kind in seen.get(at, ()) if kind == "compare"]
            return len(seen.get(at, ())), -min(d) if d else -(1 << 32)

        climbers = {}
        state = {"progress": True}

        def solve(work, ats):
            base, seen, targets = work["base"], work["seen"], work["targets"]
            effects[:] = work["effects"]
            fillable[:] = work["fillable"]
            for at in ats:
                wanted = set()
                for a, b, kind in seen.get(at, ()):
                    if kind == "test":
                        wanted.update((a ^ b, a | b, a & ~b, b, 0))
                    else:
                        wanted.update((b, b + 1, b - 1, a, a + 1, a - 1, 0))
                if DEBUG:
                    print("%s 0x%x input %s: seen %s" % ("edge" if at in targets else "path", at, work["key"],
                                                         [(hex(a), hex(b), k) for a, b, k in seen.get(at, ())[:6]]))
                for slot, new, changed in work["depends"][at]:
                    old = get(base, slot)
                    tries = set()
                    for (a, b, kind), (a2, b2, _) in zip(seen[at], changed):
                        for was, now, other in ((a, a2, b), (b, b2, a)):
                            if was != now and (now - was) & 0xFFFFFFFF == (new - old) & 0xFFFFFFFF:
                                tries.update(old + ((other + d - was) & 0xFFFFFFFF) for d in (0, 1, -1))
                    tries.update(wanted)
                    flipped = (new ^ old) & 0xFFFFFFFF
                    for k in range(4):
                        if flipped and flipped & ~(0xFF << 8 * k) == 0:
                            tries.update((old & ~(0xFF << 8 * k)) | ((w & 0xFF) << 8 * k) for w in wanted)
                    if at in targets:
                        tries.update(solver.sample(pool, min(len(pool), 24)))
                    for v in sorted(x & 0xFFFFFFFF for x in tries):
                        if spent():
                            break
                        case = put(base, slot, v)
                        m2, seen2 = attempt(case, targets)
                        if m2 is not None:
                            work["consider"](case, m2, seen2)
            if work["steps"] < SOLVE_CLIMB:
                for at, (got, case, cov) in sorted(work["furthest"].items()):
                    climbers["%s/%x" % (work["key"], at)] = (case, cov, work["steps"] + 1)
                    state["progress"] = True

        tried = set()
        while state["progress"] and not words_set <= covered and not spent():
            state["progress"] = False
            later = []
            sources = [(i, corpus[i], reach[i], 0) for i in sorted(reach)] + \
                [(k, c, cov, steps) for k, (c, cov, steps) in sorted(climbers.items())]
            for i, source, reached, steps in sources:
                if not reached & edge():
                    continue
                targets = sorted({setters[w][0] for w in reached & edge() if w in setters} - {s for j, s in tried if j == i})
                if not targets or spent():
                    continue
                tried.update((i, at) for at in targets)
                watch = targets + sorted({setters[w][0] for w in reached if w in setters} - set(targets))
                base = json.loads(json.dumps(source))
                present = {a for a, _, _ in base["mem"]}
                probe = self.machine()
                probe.plan = []
                self.apply(probe, base)
                base["mem"] += [[a, n, int.from_bytes(probe.read(a, n), "little")]
                                for a, n in sorted(known.items()) if a not in present]
                m, seen = attempt(base, watch, record=True)
                if m is None:
                    continue
                present = {a for a, _, _ in base["mem"]}
                for a, n in sorted(m.reads.items()):
                    if a not in present:
                        try:
                            base["mem"].append([a, n, int.from_bytes(probe.read(a, n), "little")])
                        except Exception:
                            pass
                short = min(m.stub_calls[0], SOLVE_RETURNS) - len(base["returns"])
                if calls and short > 0:
                    base["returns"] += [0] * short
                effects[:] = [[n, a, size, v] for (n, a), (size, v) in sorted(m.after.items())][:SOLVE_EFFECTS]
                fillable[:] = [[n, which, off, size, v] for (n, which, off), (size, v) in sorted(m.filled.items())][:SOLVE_EFFECTS]
                furthest = {}

                def consider(case, m2, seen2, seen=seen, targets=targets, furthest=furthest):
                    if keep(m2, case):
                        state["progress"] = True
                    for at in targets:
                        got = score(seen2, at)
                        if got > max(score(seen, at), furthest.get(at, ((0, 0),))[0]):
                            furthest[at] = (got, case, set(m2.covered))

                depends = {at: [] for at in watch}
                slots = [("args", i) for i in range(nregs)] + [("mem", i) for i in range(len(base["mem"]))] \
                    + [("returns", i) for i in range(len(base["returns"]))]
                mem = [s for s in slots if s[0] == "mem"]
                if len(mem) > SOLVE_SLOTS:
                    mem = sorted(solver.sample(mem, SOLVE_SLOTS))
                slots = [s for s in slots if s[0] != "mem"] + [("effect", j) for j in range(len(effects))] \
                    + [("fill", j) for j in range(len(fillable))] + mem
                for slot in slots:
                    if spent():
                        break
                    old = get(base, slot)
                    single = [old ^ (0x80 << 8 * k) for k in range(1, 4) if width(base, slot) >> 8 * k & 0xFF]
                    for new in [(old ^ 0x55) & width(base, slot), (old + 0x01010101) & width(base, slot),
                                ~old & width(base, slot)] + single:
                        case = put(base, slot, new)
                        m2, seen2 = attempt(case, watch)
                        if m2 is None:
                            continue
                        consider(case, m2, seen2)
                        hit = [at for at in watch if at in seen and at in seen2 and seen2[at] != seen[at]
                               and slot not in [d[0] for d in depends[at]]]
                        for at in hit:
                            depends[at].append((slot, new, seen2[at]))
                work = dict(key=i, steps=steps, base=base, seen=seen, depends=depends, targets=targets,
                            effects=[list(e) for e in effects], fillable=[list(e) for e in fillable],
                            consider=consider, furthest=furthest)
                solve(work, targets)
                later.append((work, [at for at in watch if at not in targets]))
            runs[0], cost[0] = 0, 0
            for work, ats in later:
                if spent() or words_set <= covered:
                    break
                solve(work, ats)
            runs[0], cost[0] = SOLVE_RUNS, SOLVE_COST
            if state["progress"] and not words_set <= covered:
                runs[0], cost[0] = 0, 0


class Cells(dict):
    """The memory locations the generator gives values to: {address: size}, at most MAX_CELLS."""

    def full(self):
        return len(self) >= MAX_CELLS

    def learn(self, reads):
        for a, n in reads.items():
            if len(self) >= MAX_CELLS:
                return
            self.setdefault(a, n)


def coalesce(stores):
    """[(address, bytes), ...] in the order given -> the same as few contiguous runs, for one emulator
    write each (a case's memory is mostly bytes of buffers). With overlapping entries the later one
    must win, as when they are written one by one: then they are returned as they are."""
    order = sorted(range(len(stores)), key=lambda i: stores[i][0])
    end = None
    for i in order:
        address, data = stores[i]
        if end is not None and address < end:
            return stores
        end = address + len(data)
    runs = []
    for i in order:
        address, data = stores[i]
        if runs and runs[-1][0] + len(runs[-1][1]) == address:
            runs[-1][1].extend(data)
        else:
            runs.append([address, bytearray(data)])
    return [(address, bytes(data)) for address, data in runs]


def seed_name(entry):
    """What a function's random numbers derive from: its address, in the form of the name it had
    before anybody named it, so that naming a function does not change its cases."""
    return "FUN_%08x" % entry


def resolve_calls(generator, calls):
    """calls of auto() with callee names resolved to entries: [[address, mask, stack words]]."""
    out = []
    for c in calls:
        target = generator.entries[c[0]] if isinstance(c[0], str) else c[0]
        out.append([target, c[1], c[2] if len(c) > 2 else 0])
    return out


def cache_key(payload, base, words, sizes, entry, options, arch=None, environment=""):
    """The key of a function's cached cases: its bytes, VERSION, the options, the architecture
    layer's generator_version when it is not 0 (what the generator learns from that architecture's
    code: changing it renews only that architecture's caches) and the environment (the generator's
    environment(): the image as a whole)."""
    blob = b"".join(payload[a - base:a - base + sizes[a]] for a in words if 0 <= a - base < len(payload))
    version = VERSION
    if arch is not None and getattr(arch, "generator_version", 0):
        version = (VERSION, arch.name, arch.generator_version)
    return hashlib.sha1(blob + repr((version, entry, sorted((k, repr(v)) for k, v in options.items()),
                                     environment)).encode()).hexdigest()


def cache_load(path):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except ValueError:
            return None
    return None


def cache_store(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = "%s.%d.tmp" % (path, os.getpid())
    with open(temp, "w") as f:
        json.dump(data, f)
    os.replace(temp, path)


def kept_load(image, name, real=()):
    path = os.path.join(image.kept_cases, name + ".json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        cases = json.load(f)["cases"]
    for c in cases:
        c["calls"] = [x for x in c.get("calls", ()) if x[0] not in real]
    return cases


def kept_add(image, name, cases, how):
    """Remember cases that showed a difference (how: "stubbed" or "real callees")."""
    kept = kept_load(image, name)
    seen = {json.dumps({k: v for k, v in c.items() if k != "kept"}, sort_keys=True) for c in kept}
    for case in cases:
        key = json.dumps({k: v for k, v in case.items() if k != "kept"}, sort_keys=True)
        if key not in seen and len([c for c in kept if c.get("kept") == how]) < KEPT_MAX // 2:
            seen.add(key)
            kept.append(dict(case, kept=how))
    os.makedirs(image.kept_cases, exist_ok=True)
    temp = os.path.join(image.kept_cases, "%s.%d.tmp" % (name, os.getpid()))
    with open(temp, "w") as f:
        json.dump({"cases": kept}, f, sort_keys=True)
    os.replace(temp, os.path.join(image.kept_cases, name + ".json"))


def extra_load(image, name, calls=(), frame=0):
    """Cases written for one function (the image's extra_cases/NAME.json); they get the stubs and
    frame of the generated cases, unless they carry their own (recorded cases: dread record)."""
    path = os.path.join(image.extra_cases, name + ".json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [dict({"calls": [list(x) for x in calls], "frame": frame}, **c) for c in json.load(f)["cases"]]


_GENERATORS = {}


def generator_for(run):
    key = (id(run["target"]), run["image"].name)
    gen = _GENERATORS.get(key)
    if gen is None:
        gen = _GENERATORS[key] = Generator(run["target"], run["image"], run["program"], run["layout"], run["original"])
    return gen


def cases_for(run, name, fn):
    """The generated, kept and written cases of a function with auto() for the runner:
    [(label, address, setup, args, limit, case)]."""
    gen = generator_for(run)
    image = run["image"]
    options = dict(run["suite"].auto[name])
    calls = resolve_calls(gen, options.pop("calls", ()))
    frame = options.pop("frame", 0)
    prog = gen.program
    sizes = {a: prog.insn_size(a) for a in fn.insns}
    key = cache_key(gen.payload, image.base, fn.insns, sizes, fn.entry, dict(options, calls=calls, frame=frame),
                    prog.arch, gen.environment())
    path = os.path.join(run["target"].build, "autocases", image.name, name + ".json")
    cached = cache_load(path)
    if not cached or cached.get("key") != key:
        started = time.time()
        try:
            made = gen.generate(name, fn.entry, fn.insns, calls=calls, frame=frame, **options)
        except GenerationError:
            if not extra_load(image, name):
                raise
            made = []
        cached = {"key": key, "seed": seed_name(fn.entry), "cases": made,
                  "seconds": round(time.time() - started, 2)}      # for reading only; not in the key
        cache_store(path, cached)
    out = []

    def setup(case):
        return lambda m: gen.apply(m, case)

    for i, c in enumerate(cached["cases"]):
        out.append(("generated %d" % i, fn.entry, setup(c), c["args"], c.get("limit", 200_000), c))
    for i, c in enumerate(kept_load(image, name, gen.real)):
        out.append(("kept %d" % i, fn.entry, setup(c), c["args"], c.get("limit", 200_000), None))
    for i, c in enumerate(extra_load(image, name, calls, frame)):
        out.append(("written %d" % i, fn.entry, setup(c), c["args"], c.get("limit", 200_000), None))
    return out
