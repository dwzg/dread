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
callee a name or an address. A generic stub returns a generated value (the first eight calls of a
run, later ones 0) and logs the argument registers in the mask and the stack words, with addresses
inside the stack replaced by the word "stack". Peripheral registers are inputs like memory; with
"io": "fixed" they read what the case says whatever was written.

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

VERSION = 4       # 2: stale Thumb IT state cleared in hooks; 3: data pointers are learned from faults;
                  # 4: callee constants, the dictionary, text in buffers, call-tree coverage
SEED = 17
DEBUG = bool(os.environ.get("DREAD_AUTOCASE_DEBUG"))
STUB_CALLS = 2000           # stub calls per run; beyond that the run is stopped and counts as not returning
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
POINTER_REACH = 0x1000      # a fault this far behind an input's value makes the input a learned pointer
POINTER_BASE = 0x1000       # learned pointers get buffers from scratch + POINTER_BASE ...
POINTER_STEP = 0x400        # ... this far apart ...
POINTER_SLOTS = 16          # ... at most this many different ones (then they are reused)
POINTER_KEEP = 0.9          # share of inputs that use the buffer (the rest keep their value: null checks)
CALLEE_DEPTH = 2            # callees whose constants and strings join the function's own
TEXT_SHARE = 0.5            # share of fresh inputs whose data buffers hold text from the dictionary
TEXT_MUTATE = 0.3           # share of mutations that change such a text by a token
TEXT_MAX = 96               # bytes of text at most
TEXT_TOKENS = 8             # tokens per text at most
STRING_MAX = 64             # longest string constant taken into the dictionary
STEPPING_MAX = 48           # inputs kept for new code in callees only (mutated further, not cases)
# String and memory routines that generated cases do not stub: small, pure, and what they write is
# behaviour of their caller (a stubbed strcpy copies nothing, and a caller that copies one byte too
# many into its frame then agrees with any C).
REAL_LIBRARY = ("memcpy", "memmove", "memset", "memcmp", "strcat", "strcmp", "strcpy", "strlen", "strncmp",
                "strncpy", "strstr", "strchr")

CONDITIONAL_BRANCHES = (csarm.ARM_INS_B, csarm.ARM_INS_CBZ, csarm.ARM_INS_CBNZ)
SETTERS = (csarm.ARM_INS_CMP, csarm.ARM_INS_CMN, csarm.ARM_INS_TST, csarm.ARM_INS_TEQ)
TESTS = (csarm.ARM_INS_TST, csarm.ARM_INS_TEQ, csarm.ARM_INS_AND, csarm.ARM_INS_BIC, csarm.ARM_INS_EOR,
         csarm.ARM_INS_ORR)


class GenerationError(Exception):
    """No input of the search ran to the end: the function needs written cases."""


class Generator:
    """What generating and applying cases needs about one image; one per image and process."""

    def __init__(self, target, image, program=None, layout=None, payload=None):
        self.target, self.image = target, image
        self.program = program or Program(target, image)
        self.layout = layout or Layout(target, image)
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
        self.code = frozenset(self.program.code)
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
        found, limits, table = set(), set(), False
        insns = self.insns(words)
        for ins in insns:
            ops = ins.operands
            if ins.id == csarm.ARM_INS_CMP and len(ops) == 2 and ops[1].type == csarm.ARM_OP_IMM and 0 < ops[1].imm <= 0xFF:
                limits.add(ops[1].imm)
            if ins.id in (csarm.ARM_INS_TBB, csarm.ARM_INS_TBH) or (
                    ins.id in (csarm.ARM_INS_MOV, csarm.ARM_INS_LDR, csarm.ARM_INS_ADD) and ops
                    and ops[0].type == csarm.ARM_OP_REG and ops[0].reg == csarm.ARM_REG_PC):
                table = True
        if table:                       # a jump table: one arm per value up to the compare's bound
            for limit in limits:
                found.update(range(limit + 2))
        for ins in insns:
            if (ins.group(cs.CS_GRP_JUMP) or ins.group(cs.CS_GRP_CALL)) and ins.id != csarm.ARM_INS_BIC:
                continue
            isa = self.program.isa_at(ins.address)
            for op in ins.operands:
                if op.type == csarm.ARM_OP_IMM:
                    found.add(op.imm & 0xFFFFFFFF)
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
            ops = ins.operands
            if ins.id in (csarm.ARM_INS_CMP, csarm.ARM_INS_CMN) and len(ops) == 2 and ops[1].type == csarm.ARM_OP_IMM:
                c = ops[1].imm & 0xFFFFFFFF
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
        "test" or "compare"). Plain forms only: a register against a register or a constant, no
        shifted operand, the setter at most four instructions before the branch with nothing but
        other conditional branches between; cbz and cbnz decide themselves (register against 0)."""
        ins_list = self.insns(words)
        index = {ins.address: k for k, ins in enumerate(ins_list)}
        out = {}

        def reg(capstone_reg, ins):
            name = ins.reg_name(capstone_reg)
            try:
                return self.arch.reg_id(name)
            except ValueError:
                return None

        for ins in ins_list:
            if ins.id in (csarm.ARM_INS_CBZ, csarm.ARM_INS_CBNZ):
                r = reg(ins.operands[0].reg, ins)
                if r is not None:
                    out[ins.address] = (ins.address, lambda uc, r=r: (uc.reg_read(r), 0, "compare"))
                continue
            if ins.id != csarm.ARM_INS_B or not self.arch.is_conditional(ins):
                continue
            k = index[ins.address] - 1
            while k >= 0 and ins.address - ins_list[k].address <= 16:
                s = ins_list[k]
                if (s.id == csarm.ARM_INS_B and self.arch.is_conditional(s)) or s.id == csarm.ARM_INS_IT:
                    k -= 1
                    continue
                sets = s.id in SETTERS or s.update_flags
                ops = s.operands[-2:]
                if sets and len(s.operands) >= 2 and ops[0].type == csarm.ARM_OP_REG \
                        and ops[1].type in (csarm.ARM_OP_REG, csarm.ARM_OP_IMM) and not any(o.shift.type for o in ops):
                    kind = "test" if s.id in TESTS else "compare"
                    first = reg(ops[0].reg, s)
                    second = reg(ops[1].reg, s) if ops[1].type == csarm.ARM_OP_REG else None
                    imm = ops[1].imm & 0xFFFFFFFF if second is None else 0
                    # adds r2, r2, #4; beq asks whether r2 was -4; cmn compares with the negation
                    sign = -1 if s.id in (csarm.ARM_INS_ADD, csarm.ARM_INS_CMN) else 1
                    if first is not None and (second is not None or ops[1].type == csarm.ARM_OP_IMM):
                        out[ins.address] = (s.address, lambda uc, first=first, second=second, imm=imm, kind=kind, sign=sign:
                                            (uc.reg_read(first), sign * (uc.reg_read(second) if second is not None else imm)
                                             & 0xFFFFFFFF, kind))
                break
        return out

    def conditional_branches(self, words):
        """{address: (target, fall-through)} of the function's conditional direct branches."""
        out = {}
        for ins in self.insns(words):
            if ins.id in (csarm.ARM_INS_CBZ, csarm.ARM_INS_CBNZ) or (ins.id == csarm.ARM_INS_B and self.arch.is_conditional(ins)):
                out[ins.address] = (self.arch.target(ins), ins.address + ins.size)
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
        for addr, size, value in case["mem"]:
            value = self.confined(addr, size, value & ((1 << 8 * size) - 1), extra)
            if value is not None:
                m.write(addr, value.to_bytes(size, "little"))
                if fixed is not None and lay.target.memory.is_io(addr):
                    fixed.update((addr + i, b) for i, b in enumerate(value.to_bytes(size, "little")))
        if fixed is not None:
            m.io_fixed = fixed
        for at, nth, value in case.get("poll", ()):
            m.poll_read(at, nth, value)
        returns = list(case.get("returns", ()))
        frame = case.get("frame", 0)
        count = [0]
        made = m.stub_calls = [0]
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

    def machine(self, payload=None, aliases=None):
        return Machine(self.layout, self.payload if payload is None else payload, aliases, isa_of=self.program.isa_at)

    def touches_image(self, m):
        image = self.image
        return m.code_reads or any(image.base <= a < image.end for a in m.written)

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
                m.trace_reads(self.image.base, self.image.end, self.code)
            m.call(entry, *case["args"], max_instructions=limit)
        except Exception:
            return set()
        if check_image and self.touches_image(m):
            return set()
        return m.covered_insns(words)

    # ---- the search ----

    def generate(self, name, entry, words, calls=(), frame=0, count=40, budget=400, limit=200_000, sweep=3,
                 fallbacks=True, seconds=None):
        """Cases for one function (see the module's docstring). Raises GenerationError when no
        input runs to the end. fallbacks=False skips the io "fixed" and long-run searches;
        seconds bounds the time (a deadline after which every stage stops; the result then
        depends on the machine's speed, so it is for quick uses such as the integration run)."""
        before, self.current = self.current, name
        self.deadline = time.time() + seconds if seconds else None
        try:
            if not fallbacks:
                return self._generate_once(name, entry, words, calls, frame, count, budget, limit, sweep, learn=True)
            return self._generate_all(name, entry, words, calls, frame, count, budget, limit, sweep)
        finally:
            self.current = before
            self.deadline = None

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
        known, covered, corpus, extras, faults, last_gain = {}, set(), [], [], 0, 0
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
                    case["args"][i] = address
            for m_ in case["mem"]:
                if m_[0] in data_mem and m_[1] == 4 and rng.random() < POINTER_KEEP:
                    m_[2] = data_mem[m_[0]]
            return case

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
            """The learned data buffers this input uses."""
            used = {address for i, address in data_args.items() if i < len(case["args"]) and case["args"][i] == address}
            used |= {v for a, n, v in case["mem"] if a in data_mem and n == 4 and v == data_mem[a]}
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
            return {"args": [value() for _ in range(nregs)],
                    "mem": [[a, s, value()] for a, s in sorted(known.items())],
                    "returns": [value() for _ in range(8)] if calls else [], "calls": [list(c) for c in calls],
                    "frame": frame, **({"io": "fixed"} if self.io_fixed else {}), **({"limit": self.long} if self.long else {})}

        def run(case, max_instructions=limit):
            m = self.machine()
            m.plan = []
            self.apply(m, case)
            m.cover(words[0], end)
            m.trace_reads(image.base, image.end, self.code)
            m.call(entry, *case["args"], max_instructions=max_instructions)
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
            else:
                case = fresh()
                if pin_args or pin_mem or data_args or data_mem:
                    pinned(case)
                    for buffer in (buffers(case) if strings or chars else ()):
                        if rng.random() < TEXT_SHARE:
                            put_text(case, buffer, fresh_text())
            m = self.machine()
            m.plan = []
            self.apply(m, case)
            m.cover(words[0], end, tree=True)
            m.trace_reads(image.base, image.end, self.code)
            try:
                m.call(entry, *case["args"], max_instructions=limit)
            except EmuError:
                faults += 1
                learn_pointers(m, case)
                if pointers:
                    went = {m.reg("pc") & ~1, m.reg("r12")} - {self.fake_pointer, 0}
                    pin_args.update(i for i, v in enumerate(case["args"][:nregs]) if v in went)
                    pin_mem.update(a for a, n, v in case["mem"] if n == 4 and v in went)
                # What the run loaded before it failed is an input all the same: a function whose
                # first inputs all fail (a division by a value from memory, a wait for a flag)
                # never gets any otherwise.
                if learn and len(m.reads) < 4096:
                    for a, n in m.reads.items():
                        try:
                            m.read(a, n)
                        except Exception:
                            continue
                        known.setdefault(a, n)
                continue
            if self.touches_image(m):
                faults += 1
                continue
            for a, s in m.reads.items():
                known.setdefault(a, s)
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
                for a, s in m.reads.items():
                    known.setdefault(a, s)
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
                    try:
                        m = self.machine()
                        m.plan = []
                        self.apply(m, case)
                        m.cover(words[0], end)
                        m.trace_reads(image.base, image.end, self.code)
                        r = m.call(entry, *case["args"], max_instructions=limit)
                    except EmuError:
                        continue
                    if self.touches_image(m):
                        continue
                    got = m.covered_insns(words)
                    if got - covered:
                        covered |= got
                        swept.append(case)
                    steps.append((case, repr((r.ret, sorted(r.writes.items()), r.calls))))
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
        return corpus + extras

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
            m.trace_reads(image.base, image.end, self.code)
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
            for a, n in m.reads.items():
                known.setdefault(a, n)
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
            for a, s in m.reads.items():
                known.setdefault(a, s)
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


def cache_key(payload, base, words, sizes, entry, options):
    blob = b"".join(payload[a - base:a - base + sizes[a]] for a in words if 0 <= a - base < len(payload))
    return hashlib.sha1(blob + repr((VERSION, entry, sorted((k, repr(v)) for k, v in options.items()))).encode()).hexdigest()


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
    key = cache_key(gen.payload, image.base, fn.insns, sizes, fn.entry, dict(options, calls=calls, frame=frame))
    path = os.path.join(run["target"].build, "autocases", image.name, name + ".json")
    cached = cache_load(path)
    if not cached or cached.get("key") != key:
        try:
            made = gen.generate(name, fn.entry, fn.insns, calls=calls, frame=frame, **options)
        except GenerationError:
            if not extra_load(image, name):
                raise
            made = []
        cached = {"key": key, "seed": seed_name(fn.entry), "cases": made}
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
