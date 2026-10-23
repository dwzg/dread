"""dread uncovered: why the cases a lift check ran leave instructions of the original unrun, by code.

`dread lift --check` drops a function whose cases (for one that never returns: its loop cases and the
system run) leave instructions of the original unrun, and lists those instructions in its verdicts
(BUILD/lift-check/IMAGE/verdicts.tsv, the `missed` column; a function whose case generation failed
has none, and all of it counts as missed). This tells, for each such function (or those named), why
each piece of that code went unrun, as far as the code and the cases say. A named function the
verdicts do not drop so is looked at with what its cached cases leave unrun, run on the original
(kind "cached cases": cases the lift check refused count there).

The missed instructions are grouped into regions, connected in the function's control flow, the
lifter's where there is one (switch cases resolved, branches that constants decide followed their one
way, no fall-through behind a call of a function that never returns, the shared code a lifted body
enters). A region's frontier is the edges into it from instructions that ran; each gets a cause:

  branch       a conditional branch (or a conditional return, jump or call) ran and never went
               this way
  switch       a jump through a table ran and never went to this case
  call         a call ran and never came back
  straight     the instruction in front ran and is no branch: every run that got there ended between
               the two (at the instruction limit, or in a block the emulator ran only in part)
  not entered  no counted case ran the function at all
  unreached    no edge from code that ran leads in and the region holds no entry: only ways the
               control flow does not know (an indirect jump whose targets are not found)

For a branch and a switch, what decides it is traced back through the function: the registers its
condition reads (on ARM the flags, then the registers of the instruction that set them; a RISC-V branch
compares its two registers itself), each to the instructions whose write may reach it on some path,
through moves, arithmetic, conditional writes, loads, and stores to the frame loaded back, down to where
each value comes from, its source:

  callee       a callee's result, a register a call leaves, or a local a callee was handed the address
               of (in a register or one of the first STACK_ARGS stack arguments)
  device       a load from a fixed address in the target's [memory] io (the device model's name and
               offset where one covers it)
  field        a load through a pointer, by where the pointer comes from: a field of an argument's
               object, of a global's, of a callee's result, of a pointer a loop walks, ...
  global       a load from a fixed address outside io and the image's read-only memory (the nearest
               data symbol's name), "indexed by" another source for an array
  local        a stack slot no store of the function fills on the way (nor a callee handed its address)
  argument     an argument register, or a stack argument, as the caller passed it
  caller       another register the caller left (a callee-saved one, a scratch one past the arguments)
  loop         a register a loop carries (a counter, a pointer walking a list)
  system       a system register (mrs, csrr): what an instruction writes from no register
  unknown      not traced: more than MAX_DEPTH steps back, or what a layer without value_step (MSP430,
               MIPS) cannot take apart: there only fixed addresses (the layer's literal), compares and
               what the layer calls register-only are followed
  constant     constants only: immediates, literals, the image's read-only memory, a fixed register

A load from a table (a constant address plus an index, in the instruction or made in front of it) in
read-only memory is classed by its index; a value that is one of several constants by the path taken (a
flag the function sets) also by the conditions of the branches in front of the constants' writes. A
condition with several sources is classed by the first of them in SOURCES (inputs from outside the
function before inner ones) and all are listed. A region with several ways in is counted under its
main one, the edge from which most of its instructions are reached; the others are listed too.

The table's source column is the source for a branch and a switch, with a callee "stubbed" when the
cases stub it (its answer was an input the search set) or "real" (it ran as its code does) and a device
register "polled" when the cases' poll stages answered it (a wait the search cut); for a call how the
runs that got there ended (the cases stop there: a callee runs hung in; loop cases end there: their stub
calls ran out; runs hang in the callee), or that only the system run got there (a function that never
returns) or no case did; for a function not entered, generation failed, cases refused, cases failed or
no cases cached.

With runs (the default; --no-runs leaves them out), each function's cached cases are read: generated,
kept and written, as the lift check left them (nothing is generated; where the cache holds cases under
another key than the generator computes now, an older generator's, those are taken and the summary says
so: the lift check that wrote the verdicts ran them), with what they say of the search (the callees it
stubbed, the calls its runs stop at, the registers it polled, its hang cases, a failed generation's
message, the lift check's REFUSED and FAIL lines from its round-N.txt files). They also run on the
original, at most RUN_CASES a function: the values each frontier's deciding registers held where the
branch was decided and how many runs got there, how the runs that reached a frontier call ended, and
whether a case runs instructions the lift check counted as missed (one it refused or did not count).
Each frontier branch and switch also gets a forced run: the first case that got there runs again and is
sent the missed way the first time it gets there (from the IT instruction in front where the branch is
predicated inside a Thumb IT block: Unicorn takes no pc written in a code hook there), and how it ends
(by a return, a fault and where, the instruction limit and in which function, a stub that ends it, or
touching the image, which the generator drops) tells whether the condition is all that stands in the
way or the region needs more of its inputs; the second table counts the regions by that. A function's
work runs in a worker process.

Output: a line per region (function, its first address and size, cause and source, the frontier
instruction, what decides it and the main source), indented under it the other sources, the runs'
evidence and the search's notes; then the table of causes over all functions (regions, missed
instructions and functions per cause and source, with an example) and the forced runs' table. --kind
takes only the functions dropped one way (cases, loop, none, cached); --json FILE writes all of it
(docs/formats.md). Functions and regions come in address order, the tables by instructions, regions,
then name: the same inputs give the same bytes.

What it does not establish: whether a region can run at all (a check no caller can fail, a branch the
compiler kept); a source is what the code makes the value of, not why the search missed the value (the
values the runs saw, the forced run and the notes are the evidence for that); a value read from memory
another function wrote is classed by the read, not by the writer; a forced run keeps the registers of a
run that went the other way, so its fault may be one a fitting input avoids; the system run's share of
a function that never returns is not run again (its frontier may be one no loop case reaches).

    dread uncovered [--target DIR] [--image NAME] [--verdicts FILE] [--json FILE] [--no-runs] [--quiet]
                    [--kind cases|loop|none|cached] [--examples N] [-j N] [NAME...]
"""
import argparse
import bisect
import collections
import json
import multiprocessing
import os
import re
import sys

from dread import target as tgt
from dread.program import Program
from dread.util.polite import die_with_parent, polite, resident, workers

MAX_DEPTH = 16              # definitions a value is traced back through at most
RUN_CASES = 64              # cases of a function its runs take at most
VALUES_SHOWN = 6            # distinct values the report lists per register
NEAR_SYMBOL = 0x40          # a global this far behind a data symbol is named symbol+offset
FILL_REACH = 64             # a local this far above a pointer a callee is handed may be filled by it (a
                            # generated stub fills at most 64 bytes, dread.autocase)
STACK_ARGS = 4              # outgoing stack words looked at for such a pointer
# The sources in the order a condition with several is classed by: inputs from outside the function
# first (what the case generator has to set or stub), inner ones last
SOURCES = ("callee", "device", "field", "global", "local", "argument", "caller", "loop", "system", "unknown",
           "constant")
KINDS = {"cases": "cases", "loop": "loop cases and the system run", "none": "no cases",       # --kind
         "cached": "cached cases"}
SENTINEL_SHIFT = 24         # the distinct values registers hold when value_step is asked for an access
BASES = {"argument": "an argument", "callee": "a callee's result", "global": "a global", "device": "a device",
         "local": "a local", "loop": "a loop's pointer", "field": "a field", "caller": "a caller's register",
         "constant": "a constant", "unknown": "an unknown pointer", "system": "a system register"}

RUN = {}                    # what the workers need; filled before they are forked


# ---- the lift check's verdicts ----

def verdicts_path(target, image):
    return os.path.join(target.build, "lift-check", image.name, "verdicts.tsv")


def read_verdicts(path):
    """{function: (verdict, reason, [missed address, ...])} of a lift check's verdicts.tsv."""
    out = {}
    with open(path) as f:
        for line in f:
            if line.startswith("#") or line.startswith("function\t"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 2 or not fields[0]:
                continue
            missed = fields[3].split() if len(fields) > 3 else []
            out[fields[0]] = (fields[1], fields[2] if len(fields) > 2 else "",
                              [int(a, 16) for a in missed if a.startswith("0x")])
    return out


def drop_kind(reason):
    """How the lift check came to the missed instructions: "cases" (UNCOVERED by the function's cases),
    "loop cases and the system run" (a function that never returns), "no cases" (its case generation
    failed and nothing else ran: every instruction missed), "other"."""
    if reason.startswith("UNCOVERED"):
        return "cases"
    if reason.startswith("never returns"):
        return "loop cases and the system run"
    if "[case generation]" in reason:
        return "no cases"
    return "other"


def selected(verdicts, program, names=()):
    """{name: (kind, sorted missed addresses, reason)} of the functions to look at: every function the
    verdicts drop with missed instructions, or without any case (all of it missed); with names, those,
    and a named function they do not drop so as ("cached cases", None, reason): what its cached cases
    leave unrun is found by running them (measure)."""
    out = {}
    for name in names or sorted(verdicts):
        fn = program.by_name.get(name)
        if fn is None:
            continue
        verdict, reason, missed = verdicts.get(name, (None, "", []))
        kind = drop_kind(reason)
        if verdict == "dropped" and not missed and kind == "no cases":
            missed = list(fn.insns)
        if verdict == "dropped" and missed:
            out[name] = (kind, sorted(set(missed)), reason)
        elif names:
            out[name] = ("cached cases", None, reason)
    return out


# ---- what the code says ----

def norm(reg):
    """A register name as the trace keeps it: every flag ("flags:Z" on MSP430) as "flags"."""
    return "flags" if reg.startswith("flags") else reg


def insn_text(insn):
    return ("%s %s" % (insn.mnemonic, insn.op_str)).strip() if insn is not None else "?"


class Context:
    """What the trace needs about one image: the program, the convention, the memory map, names."""

    def __init__(self, target, image, program=None, regflow=None):
        self.target, self.image = target, image
        self.program = program or Program(target, image)
        self.arch = self.program.arch
        abi = target.abi
        self.arg_regs = list(abi.arg_regs)
        self.results = set(abi.result)
        self.general = list(self.arch.general_regs())
        self.link = getattr(self.arch, "link_reg", None)
        self.call_writes = {r for r in self.general if r not in set(abi.callee_saved)} | self.results | {"flags"}
        if self.link:
            self.call_writes.add(self.link)
        self.fixed = {norm(k): v & 0xFFFFFFFF for k, v in image.fixed.items()}
        self.regflow = regflow
        self.models = sorted((m["base"], m.get("size", 0x1000), name) for name, m in target.models.items()
                             if isinstance(m, dict) and isinstance(m.get("base"), int))
        self.data_symbols = sorted((a, n) for kind, a, n in self.program.symbols() if kind == "d" and n)
        self.data_addresses = [a for a, _ in self.data_symbols]
        regs = list(self.general) + [r for r in self.fixed if r not in self.general]
        if self.link and self.link not in regs:
            regs.append(self.link)
        self.sentinel_regs = regs
        self.sentinels = {r: ((k + 1) << SENTINEL_SHIFT, False) for k, r in enumerate(regs)}
        self._lifter = False

    # names

    def callee_name(self, address):
        if address is None:
            return None
        fn = self.program.by_entry.get(address & ~1)
        return fn.name if fn is not None else "0x%x" % address

    def device_name(self, address):
        for base, size, name in self.models:
            if base <= address < base + size:
                return "%s+0x%x" % (name, address - base)
        return None

    def data_name(self, address):
        i = bisect.bisect_right(self.data_addresses, address) - 1
        if i < 0:
            return None
        a, name = self.data_symbols[i]
        if a == address:
            return name
        return "%s+0x%x" % (name, address - a) if address - a < NEAR_SYMBOL else None

    # the lifter: control flow as the lift check sees it, and the frame

    @property
    def lifter(self):
        if self._lifter is False:
            try:
                from dread import lift
                self._lifter = lift.make_lifter(self.program, self.regflow)
            except Exception:
                self._lifter = None
        return self._lifter

    def preserved(self, callee):
        """The registers dread.regflow found a callee never to write (its callers rely on that)."""
        if self.regflow is None or callee is None:
            return set()
        return set(self.regflow.preserved_regs(callee))

    # memory accesses

    def accesses(self, insn, isa):
        """([(base register, displacement, indexed, load, store)], moved): the instruction's memory
        accesses at a register plus a constant, found by asking the layer's value_step with a distinct
        value in every register (an access's base value then names its register), and the registers
        it computes besides (a base it writes back); ([], set()) for none, for an access at sp
        (value_step leaves those out: frame_load has them) and for a layer without value_step."""
        step = getattr(self.arch, "value_step", None)
        if step is None:
            return [], set()
        try:
            values, accesses, _ = step(insn, isa, self.sentinels, self.program)
        except Exception:
            return [], set()
        out = []
        for base, offset, _, indexed, load, store in accesses:
            k = (base >> SENTINEL_SHIFT) - 1
            if base & ((1 << SENTINEL_SHIFT) - 1) or not 0 <= k < len(self.sentinel_regs):
                continue
            out.append((self.sentinel_regs[k], offset, indexed, load, store))
        return out, {r for r, v in values.items() if v is not None}

    def read_only(self, address):
        """Whether an address lies in the image's read-only memory: a "rom" region, or the image where
        no region is known."""
        region = self.target.memory.region(address)
        if region is not None:
            return region.kind == "rom"
        return self.program.in_image(address, 1)

    def address_source(self, address):
        """The source a load from a fixed address is: a device register, the image's read-only memory
        (a constant) or a global."""
        address &= 0xFFFFFFFF
        if self.target.memory.is_io(address):
            name = self.device_name(address)
            return ("device", address, "device register 0x%x%s" % (address, " (%s)" % name if name else ""))
        if self.read_only(address):
            return ("constant", address, "read-only data at 0x%x" % address)
        name = self.data_name(address)
        return ("global", address, "global 0x%x%s" % (address, " (%s)" % name if name else ""))


class Flow:
    """One function's control flow and the trace of values through it."""

    def __init__(self, ctx, fn, extra=()):
        """extra: instructions outside fn to take in (missed shared code of its lifted body: the
        lifter's Body then stands for fn, with all of its shared code)."""
        prog, arch = ctx.program, ctx.arch
        self.ctx, self.prog, self.arch = ctx, prog, arch
        lifter = ctx.lifter
        if extra and lifter is not None:
            try:
                fn = lifter.shared_code(fn) or fn   # (the lift check covers the same body: dread.runner)
            except Exception:
                pass
        self.fn = fn
        code = set(fn.insns)
        code.update(a for a in extra if prog.insn(a) is not None)
        self.code = sorted(code)
        self.insn = {a: prog.insn(a) for a in self.code}
        self.kind = {a: arch.flow(i) if i is not None else ("stop", None, False) for a, i in self.insn.items()}
        self.frames, self.states, self.reached = None, None, None
        if lifter is not None:
            try:
                self.states = lifter.states(fn, strict=False, switch=True)
            except Exception:
                self.states = None
        index = {a: i for i, a in enumerate(fn.insns)}
        shared = code - set(index)                  # code outside the function (and its body)
        if self.states is not None:
            from dread import regflow
            self.frames = regflow.Frames(prog, fn)
            self.frames._states = self.states       # (the same states it would make: frame_states)
            self.reached = set(self.states) | shared    # what a path from the entry reaches
        self.succ = {}
        self.dead = {}                      # the plain successors of what no path reaches (regions_of)
        for a in self.code:
            insn = self.insn[a]
            out = []
            if insn is None:
                pass
            elif self.states is not None and a in index and a not in self.states:
                self.dead[a] = [b for b in self._plain_successors(a, insn, code) if b in code]
            elif self.states is not None and a in self.states:
                try:
                    out = lifter.successors(fn, index, a, insn, strict=False, values=self.states[a][1])
                    if shared:              # (the lifter's leave the function: into the shared code too)
                        out = list(out) + [b for b in self._plain_successors(a, insn, code) if b in shared]
                except Exception:
                    out = self._plain_successors(a, insn, code)
            elif self.states is None or a not in index:
                out = self._plain_successors(a, insn, code)
            self.succ[a] = sorted({b for b in out if b in code})
        self.pred = {a: [] for a in self.code}
        for a in self.code:
            for b in self.succ[a]:
                self.pred[b].append(a)
        self._effects, self._defs, self._memo = {}, {}, {}
        self.loads = {}                     # a source that is a load from memory -> the loads it came from
                                            # (dread.autocase's frontier answers set the memory they read)

    def _plain_successors(self, a, insn, code):
        kind, target, falls = self.kind[a]
        out = []
        if kind in ("next", "call", "icall") or falls:
            out.append(a + insn.size)
        if kind == "jump" and isinstance(target, int):
            out.append(target & ~1)
        if kind == "ijump":
            from dread import switches
            try:
                got = switches.resolve(self.prog, self.fn, a) if a in self.fn.insns else None
            except Exception:
                got = None
            if got and got[0]:
                out += list(got[0])
            else:
                try:
                    out += list(self.arch.switch_targets(insn, self.prog, lambda t: (t & ~1) in code) or ())
                except Exception:
                    pass
        return out

    # what an instruction does with registers

    def callee(self, a):
        kind, target, _ = self.kind[a]
        return self.ctx.callee_name(target) if kind == "call" else None

    def effects(self, a):
        """(reads, writes, certain) of the instruction at a: register names (the flags as "flags"),
        the writes with what a call leaves; certain False under a condition (its writes may not
        happen)."""
        got = self._effects.get(a)
        if got is None:
            insn = self.insn[a]
            if insn is None:
                got = (frozenset(), frozenset(), True)
            else:
                reads, writes = self.arch.reg_effects(insn)
                reads = {norm(r) for r in reads if r} - {"pc"}
                writes = {norm(r) for r in writes if r} - {"pc"}
                if self.kind[a][0] in ("call", "icall"):
                    writes |= self.ctx.call_writes - self.ctx.preserved(self.callee(a))
                got = (frozenset(reads), frozenset(writes), self.arch.predicate(insn) is None)
            self._effects[a] = got
        return got

    def defs(self, at, reg):
        """(sites, entry): the instructions whose write of reg may be what the instruction at `at`
        reads, and whether the caller's value may be (a path from the entry that writes none)."""
        key = (at, reg)
        got = self._defs.get(key)
        if got is not None:
            return got
        sites, entry, seen = set(), at == self.fn.entry, set()
        work = list(self.pred.get(at, ()))
        while work:
            q = work.pop()
            if q in seen:
                continue
            seen.add(q)
            _, writes, certain = self.effects(q)
            if reg in writes:
                sites.add(q)
                if certain:
                    continue
            if q == self.fn.entry:
                entry = True
            work.extend(self.pred[q])
        got = self._defs[key] = (sorted(sites), entry)
        return got

    # where values come from

    def sources(self, at, reg, depth=0, stack=()):
        """The sources of the value reg holds in front of the instruction at `at`: a frozenset of
        (class, key, text) (the module's docstring lists the classes)."""
        sites, entry = self.defs(at, reg)
        out = set()
        if entry:
            out.add(self.entry_source(reg))
        for d in sites:
            out |= self.def_sources(d, reg, depth + 1, stack)
        if not out:
            out.add(("unknown", reg, "%s: no write of it reaches 0x%x" % (reg, at)))
        elif len(sites) > 1 and all(s[0] == "constant" for s in out) and len({s[1] for s in out}) > 1:
            # one of several constants by the path taken (a flag): what picks the path decides it
            for d in sites:
                out |= self.control_sources(d, depth + 1, stack)
        return frozenset(out)

    def control_sources(self, s, depth, stack):
        """The sources of the conditions of the nearest branches in front of the instruction at s on
        each path (the straight code back to an instruction with more than one successor)."""
        key = ("control", s)
        if key in stack or depth > MAX_DEPTH:
            return set()
        stack = stack + (key,)
        out, seen, work = set(), set(), [s]
        while work:
            q = work.pop()
            for p in self.pred.get(q, ()):
                if p in seen:
                    continue
                seen.add(p)
                if len(self.succ.get(p, ())) > 1:
                    for r in sorted(self.effects(p)[0] - {"sp", "pc"}):
                        out |= self.sources(p, r, depth, stack)
                else:
                    work.append(p)
        return out

    def entry_source(self, reg):
        ctx = self.ctx
        if reg in ctx.fixed:
            return ("constant", ctx.fixed[reg], "%s, fixed at 0x%x" % (reg, ctx.fixed[reg]))
        if reg in ctx.arg_regs:
            return ("argument", reg, "argument %s" % reg)
        if reg == "sp":
            return ("local", "sp", "the stack pointer")
        if reg == "flags":
            return ("caller", reg, "the flags the caller left")
        return ("caller", reg, "%s as the caller left it" % reg)

    def def_sources(self, d, reg, depth, stack):
        key = (d, reg)
        if key in stack:
            return {("loop", d, "a register the loop carries (%s at 0x%x)" % (insn_text(self.insn[d]), d))}
        got = self._memo.get(key)
        if got is not None:
            return got
        if depth > MAX_DEPTH:
            return {("unknown", "depth", "more than %d steps back" % MAX_DEPTH)}
        stack = stack + (key,)
        kind = self.kind[d][0]
        if kind == "icall":
            got = {("callee", "(indirect)", "what a call through a register at 0x%x leaves in %s" % (d, reg))}
        elif kind == "call":
            name = self.callee(d)
            if reg in self.ctx.results:
                got = {("callee", name, "the result of %s" % name)}
            else:
                got = {("callee", name, "what %s leaves in %s" % (name, reg))}
        else:
            got = frozenset(self.value_sources(d, reg, depth, stack))
        self._memo[key] = got
        return got

    def constant(self, at, reg, depth, stack):
        """The one constant reg holds in front of the instruction at `at`, or None."""
        got = self.sources(at, reg, depth, stack)
        if got and all(s[0] == "constant" and isinstance(s[1], int) for s in got):
            values = {self.constant_value(s) for s in got}
            if len(values) == 1:
                return values.pop()
        return None

    def constant_value(self, source):
        """The value of a constant source: its key, or the word at a read-only address."""
        if source[2].startswith("read-only data at"):
            try:
                return self.prog.word(source[1] & ~3)
            except Exception:
                return None
        return source[1]

    def frame_load(self, d, insn):
        """The slot (bytes below the entry's stack pointer) a single load in the frame reads, else
        None: the layer's load_address (any width) or slot_access (a word), at a base the lifter's
        states know to point into the frame."""
        if self.frames is None:
            return None
        arch, got = self.arch, None
        if hasattr(arch, "load_address"):
            got = arch.load_address(insn)
        if got is None and hasattr(arch, "slot_access"):
            access = arch.slot_access(insn)
            if access is not None and access[0] == "load":
                got = access[2], access[3]
        if got is None:
            return None
        k = self.frames.depth(d, got[0])
        return k - got[1] if k is not None else None

    def value_sources(self, d, reg, depth, stack):
        """The sources of what the instruction at d (no call) writes into reg."""
        insn, prog, arch, ctx = self.insn[d], self.prog, self.arch, self.ctx
        isa = prog.isa_at(d)
        reads, writes, certain = self.effects(d)
        condition = self.condition_sources(d, depth, stack) if not certain else set()
        step = getattr(arch, "value_step", None)
        if step is None:
            # A layer without value_step cannot say what a load reads through a register (MSP430's
            # @rN, X(rN) read rN like an operand): only a fixed address (its literal), compares (the
            # flags: their registers, though one may be an address) and what it calls register-only
            # are followed
            lit = arch.literal(insn, isa) if hasattr(arch, "literal") else None
            through = {self.load(d, ctx.address_source(lit[0]))} if lit is not None else set()
            if reg != "flags" and through:
                return through | condition
            only = getattr(arch, "register_only", None)
            if reg != "flags" and not (only is not None and only(insn)):
                return {("unknown", "layer", "what %s at 0x%x gives (the layer takes no value apart)" % (
                    insn_text(insn), d))} | condition
            condition |= through
        # a constant: an immediate, a literal, an address the code computes, or one made of constants
        if step is not None and reg != "flags":
            values = {}
            try:
                values, _, _ = step(insn, isa, {}, prog)
                if values.get(reg, ()) is None and reads - {"flags"}:
                    known = {}
                    for r in sorted(reads - {"flags"}):
                        c = self.constant(d, r, depth, stack)
                        if c is not None:
                            known[r] = (c, False)
                    if known:
                        values, _, _ = step(insn, isa, known, prog)
            except Exception:
                values = {}
            v = values.get(reg)
            if v is not None and not v[1]:
                return {("constant", v[0], "constant 0x%x" % v[0])} | condition
        # a load from the frame
        slot = self.frame_load(d, insn) if reg in writes and reg != "flags" else None
        if slot is not None:
            return self.slot_sources(d, slot, depth, stack) | condition
        # a load from a fixed address, or through a pointer
        accesses, moved = ctx.accesses(insn, isa)
        if accesses and reg in moved:               # a base written back: its old value and a step
            return set(self.sources(d, reg, depth, stack)) | condition
        for base, offset, indexed, load, _ in accesses:
            if not load or reg not in writes:
                continue
            k = self.frames.depth(d, base) if self.frames is not None else None
            if k is not None and not indexed:
                return self.slot_sources(d, k - offset, depth, stack) | condition
            index_regs = sorted(reads - {base, "flags", "sp", "pc"}) if indexed else []
            index = set()
            for r in index_regs:
                index |= self.sources(d, r, depth, stack)
            c = self.constant(d, base, depth, stack)
            if c is None:                           # a table's address plus an index made in front of it
                parts = self.base_parts(d, base, depth, stack)
                if parts is not None:
                    c, more = parts
                    index |= more
            if c is not None:
                at = ctx.address_source(c + offset)
                if not index:
                    return {self.load(d, at)} | condition
                if at[0] == "constant":             # a table in read-only memory: what indexes it decides
                    return index | condition
                text = "%s indexed by %s" % (at[2], describe(primary(index)))
                return {self.load(d, (at[0], at[1], text))} | condition
            main = primary(self.sources(d, base, depth, stack))
            cls = main[0] if main else "unknown"
            text = "field %s0x%x of %s%s" % ("-" if offset < 0 else "+", abs(offset), describe(main),
                                            " (indexed)" if indexed else "")
            return {self.load(d, ("field", (cls, offset), text))} | condition
        if arch.stack_kind(insn) == "pop":
            return {("local", "restored", "a value restored from the stack (%s at 0x%x)" % (insn_text(insn), d))}
        # a register computed from registers
        inputs = set(reads) - {"pc", "sp"}
        if certain and reg != "flags" and not self.reads_carry(insn):
            inputs.discard("flags")
        out = set()
        for r in sorted(inputs):
            if r in ctx.general or r == "flags" or r in ctx.fixed or r == ctx.link:
                out |= self.sources(d, r, depth, stack)
            else:
                out.add(("system", r, "the system register %s (%s at 0x%x)" % (r, insn_text(insn), d)))
        if not out:
            if step is None:                # (a constant or memory: the layer cannot tell, no value_step)
                return {("unknown", "layer", "what %s at 0x%x gives (the layer takes no value apart)" % (
                    insn_text(insn), d))} | condition
            return {("system", insn_text(insn), "what %s at 0x%x gives" % (insn_text(insn), d))} | condition
        return out | condition

    def load(self, d, source):
        """source, a load's from memory, remembered as coming from the load at d (self.loads)."""
        self.loads.setdefault(source, set()).add(d)
        return source

    @staticmethod
    def reads_carry(insn):
        """Whether an instruction takes the carry flag as a value (adc, sbc, rsc, rrx)."""
        return insn.mnemonic.split(".")[0].rstrip("s")[:3] in ("adc", "sbc", "rsc", "rrx")

    def base_parts(self, d, base, depth, stack):
        """(constant, index sources) when the one write of base that reaches d makes it a constant plus
        registers that are no constants (the layer's value_step calls it indexed): a table's address
        and an index; else None."""
        step = getattr(self.arch, "value_step", None)
        sites, entry = self.defs(d, base)
        if step is None or entry or len(sites) != 1 or self.kind[sites[0]][0] in ("call", "icall"):
            return None
        s = sites[0]
        reads = sorted(self.effects(s)[0] - {"flags", "pc", "sp"})
        known = {}
        for r in reads:
            c = self.constant(s, r, depth + 1, stack)
            if c is not None:
                known[r] = (c, False)
        if not known:
            return None
        try:
            values, _, _ = step(self.insn[s], self.prog.isa_at(s), known, self.prog)
        except Exception:
            return None
        v = values.get(base)
        if v is None or not v[1]:
            return None
        index = set()
        for r in reads:
            if r not in known:
                index |= self.sources(s, r, depth + 1, stack)
        return v[0], index

    def condition_sources(self, d, depth, stack):
        """The sources of the condition a predicated instruction runs under (which value it leaves)."""
        return set(self.sources(d, "flags", depth, stack)) if "flags" in self.effects(d)[0] else set()

    def slot_sources(self, d, slot, depth, stack):
        """The sources of the frame slot `slot` bytes below the entry's stack pointer as the load at d
        reads it: what the stores that may reach d put there, or what a callee handed its address may
        have; where a path from the entry has neither, the caller's (a stack argument: at or above the
        entry's stack pointer) or nothing known (a local)."""
        frames = self.frames
        out, seen, entry = set(), set(), d == self.fn.entry
        work = list(self.pred.get(d, ()))
        while work:
            q = work.pop()
            if q in seen:
                continue
            seen.add(q)
            insn = self.insn[q]
            hit = [r for r, k in frames.stores(q, insn) if k == slot] if insn is not None else []
            if hit:
                for r in hit:
                    out |= self.sources(q, norm(r), depth + 1, stack)
                if self.arch.predicate(insn) is None:
                    continue
            if self.kind[q][0] in ("call", "icall"):
                handed = self.handed(q, slot)
                if handed:
                    name = self.callee(q) or "a call through a register"
                    out.add(self.load(d, ("callee", name, "what %s stores in the local at entry sp-0x%x (handed its "
                                                          "address in %s at 0x%x)" % (name, slot, handed, q))))
                    continue
            if q == self.fn.entry:
                entry = True
            work.extend(self.pred[q])
        if entry:
            if slot <= 0:
                out.add(("argument", "stack%+d" % -slot, "the stack argument at entry sp+0x%x" % -slot))
            else:
                out.add(("local", slot, "a local at entry sp-0x%x that no store on the way fills" % slot))
        return out

    def handed(self, q, slot):
        """The argument (a register, or one of the first STACK_ARGS words of the outgoing stack) that
        hands the call at q an address in the frame at most FILL_REACH bytes below the slot (the callee
        may store there), or None."""
        state = self.states.get(q) if self.states is not None else None
        if state is None:
            return None
        depth, values = state
        word = getattr(self.arch, "word", 4)
        where = [(r, values.get(r)) for r in self.ctx.arg_regs] + \
            [("the stack argument at sp+0x%x" % (word * i), values.get(depth - word * i)) for i in range(STACK_ARGS)]
        for name, v in where:
            if isinstance(v, tuple) and len(v) == 2 and v[0] == "sp" and 0 <= v[1] - slot < FILL_REACH:
                return name
        return None


def primary(sources):
    """The source a set is classed by: the first class of SOURCES it has, then the smallest key."""
    if not sources:
        return None
    rank = {c: i for i, c in enumerate(SOURCES)}
    return min(sources, key=lambda s: (rank.get(s[0], len(SOURCES)), repr(s[1]), s[2]))


def describe(source):
    return source[2] if source else "nothing known"


def ordered(sources):
    rank = {c: i for i, c in enumerate(SOURCES)}
    return sorted(sources, key=lambda s: (rank.get(s[0], len(SOURCES)), s[2]))


# ---- regions and frontiers ----

def regions_of(flow, missed):
    """[[address, ...]] of the missed instructions, connected in the control flow, in address order."""
    parent = {a: a for a in missed}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a in missed:
        for b in list(flow.succ.get(a, ())) + list(flow.dead.get(a, ())):
            if b in parent:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    groups = collections.defaultdict(list)
    for a in sorted(missed):
        groups[find(a)].append(a)
    return [groups[k] for k in sorted(groups)]


def reach_within(flow, start, region):
    seen, work = set(), [start]
    while work:
        a = work.pop()
        if a in seen or a not in region:
            continue
        seen.add(a)
        work.extend(flow.succ.get(a, ()))
    return seen


def frontier_cause(flow, p, r):
    """The cause of the edge p -> r (p ran, r did not): branch, switch, call or straight."""
    insn = flow.insn[p]
    kind, target, falls = flow.kind[p]
    behind = r == p + insn.size
    if kind in ("call", "icall") and behind:
        return "call"
    if kind == "ijump":
        return "branch" if behind and (falls or flow.arch.predicate(insn) is not None) else "switch"
    if kind == "jump" and falls or flow.arch.predicate(insn) is not None and kind in ("jump", "return"):
        return "branch"
    return "straight"


def decide(flow, p):
    """(deciders, inputs, sources) of a branch or switch at p: the instructions that decide it (the
    flags' setters, or the instruction itself where it reads its registers), [(decider, register)]
    the registers they read there, and the sources of all of them."""
    reads = flow.effects(p)[0]
    deciders, inputs, sources = set(), [], set()
    for reg in sorted(reads - {"sp"}):
        if reg == "flags":
            for s in flow.defs(p, "flags")[0]:
                sreads, _, certain = flow.effects(s)
                deciders.add(s)
                inputs += [(s, r) for r in sorted(sreads - {"pc", "sp"} - ({"flags"} if certain else set()))]
        else:
            deciders.add(p)
            inputs.append((p, reg))
        sources |= flow.sources(p, reg)
    return sorted(deciders), inputs, frozenset(sources)


def way(flow, p, r):
    """One frontier edge p -> r as a dict: where, the cause, what decides it and its sources."""
    insn = flow.insn[p]
    cause = frontier_cause(flow, p, r)
    kind, target, _ = flow.kind[p]
    out = {"at": "0x%x" % p, "to": "0x%x" % r, "cause": cause, "insn": insn_text(insn)}
    if cause in ("branch", "switch"):
        deciders, inputs, sources = decide(flow, p)
        main = primary(sources)
        out.update(deciders=["0x%x" % d for d in deciders], inputs=[["0x%x" % s, reg] for s, reg in inputs],
                   sources=[[s[0], s[2]] for s in ordered(sources)], source=main[0] if main else None,
                   base=main[1][0] if main and main[0] == "field" else None, primary=describe(main))
        if main and main[0] == "callee":
            out["callee"] = main[1]
        if main and main[0] == "device":
            out["device"] = "0x%x" % main[1]
        side = ""
        if cause == "branch" and kind == "jump" and isinstance(target, int):
            side = " (taken)" if r == target & ~1 else " (not taken)"
        by = "; ".join(insn_text(flow.insn[d]) for d in deciders if d != p)
        out["text"] = "%s%s at 0x%x%s: %s" % (insn_text(insn), side, p, " (%s)" % by if by else "", describe(main))
    elif cause == "call":
        out["callee"] = flow.callee(p) if kind == "call" else None
        out["text"] = "%s at 0x%x never came back" % (insn_text(insn), p) if kind == "call" else \
            "the call through a register at 0x%x never came back" % p
    else:
        out["text"] = "every run that reached %s at 0x%x ended before 0x%x" % (insn_text(insn), p, r)
    return out


def analyse(ctx, name, missed):
    """(flow, regions) of one function's missed instructions, each region a dict for the JSON
    (docs/formats.md) with its ways in, without what the cases say."""
    fn = ctx.program.by_name[name]
    own = set(fn.insns)
    flow = Flow(ctx, fn, extra=[a for a in missed if a not in own])
    missed = {a for a in missed if a in flow.insn}
    entered = fn.entry not in missed
    regions = []
    for group in regions_of(flow, missed) if entered else [sorted(missed)]:
        region = set(group)
        out = {"start": "0x%x" % group[0], "instructions": len(group), "missed": ["0x%x" % a for a in group],
               "ways": []}
        regions.append(out)
        if not entered:
            out.update(cause="not entered", source=None, text="no counted case ran the function")
            continue
        ways = sorted((p, r) for r in group for p in flow.pred.get(r, ())
                      if p not in missed and (flow.reached is None or p in flow.reached))
        if not ways:
            out.update(cause="unreached", source=None,
                       text="no edge from code that ran leads in (only ways the control flow does not know)")
            continue
        main = max(ways, key=lambda w: (len(reach_within(flow, w[1], region)), -w[0], -w[1]))
        out["ways"] = [way(flow, p, r) for p, r in [main] + [w for w in ways if w != main]]
        first = out["ways"][0]
        out.update(cause=first["cause"], source=first.get("source"), text=first["text"])
    return flow, regions


# ---- what the cases say ----

def prepare(target, image, verdicts_file=None, runs=True):
    """Fill RUN for one image; returns the Context."""
    from dread import regflow
    prog = Program(target, image)
    try:
        flow = regflow.load(target, image, prog)
    except Exception:
        flow = None
    ctx = Context(target, image, prog, flow)
    RUN.clear()
    RUN.update(target=target, image=image, program=prog, ctx=ctx, regflow=flow, runs=runs,
               work=os.path.dirname(os.path.abspath(verdicts_file)) if verdicts_file else None)
    lifter = ctx.lifter                     # made once here, so that the workers share what it learns
    if lifter is not None:
        try:
            lifter.never_returns("")        # (dread.noreturn over the whole program)
        except Exception:
            pass
    if runs:
        from dread import autocase
        from dread.cases import Suite
        from dread.emu import Layout, Machine
        layout = Layout(target, image)
        suite = Suite(target, image, prog, layout).load()
        RUN.update(layout=layout, suite=suite, original=image.payload(), refusals=refusals(RUN["work"]))
        Machine.entries[(target.file, image.name)] = {fn.entry for fn in prog.functions}
        gen = autocase.generator_for(RUN)   # (its whole-image digests too, for the cases' keys)
        try:
            gen.environment()
            gen.stack_switches()
        except Exception:
            pass
    return ctx


ROUND_LINE = re.compile(r"(REFUSED|FAIL|UNCOVERED|ok) +([^ :\[]+)")    # the runner's lines (dread.runner)


def refusals(work):
    """{function: its REFUSED and FAIL lines} from the last round of the lift check that ran it (the
    round-N.txt files beside the verdicts: the runner's words), read once."""
    out = {}
    if not work or not os.path.isdir(work):
        return out
    rounds = sorted((int(m.group(1)), f) for f in os.listdir(work) for m in [re.match(r"round-(\d+)\.txt$", f)] if m)
    for _, f in rounds:                     # (a later round's lines replace an earlier one's)
        this = {}
        try:
            with open(os.path.join(work, f)) as fh:
                for line in fh:
                    m = ROUND_LINE.match(line)
                    if m:
                        lines = this.setdefault(m.group(2), [])
                        if m.group(1) in ("REFUSED", "FAIL") and not line.rstrip().endswith("cases agree"):
                            lines.append(line.rstrip("\n"))
        except OSError:
            continue
        out.update(this)
    return out


def case_notes(name, fn, loop):
    """(cases, notes): the function's cached cases as dread.autocase.cases_for gives them (nothing is
    generated), and what they say of the search: the callees stubbed and stopped at (by name, with the
    number of cases), the polled registers, the hang cases, a failed generation's message. Where the
    cache holds cases under another key than the generator computes now (an older generator, other
    options), those are taken, with a note: the lift check that wrote the verdicts ran the cases the
    cache held then."""
    from dread import autocase
    RUN["suite"].auto.setdefault(name, {"loop": True} if loop else {})
    notes = {"cases": 0, "stubbed": {}, "stops": {}, "polled": [], "hang": 0, "loop": bool(loop), "failed": None,
             "stale": False}
    gen = autocase.generator_for(RUN)
    try:
        cases = autocase.cases_for(RUN, name, fn, make=False)
    except autocase.GenerationError as e:
        notes["failed"] = str(e)
        return [], notes
    except Exception as e:                  # (a case file that does not load)
        notes["failed"] = "%s: %s" % (type(e).__name__, e)
        return [], notes
    path = os.path.join(RUN["target"].build, "autocases", RUN["image"].name, name + ".json")
    if not any(c[0].startswith("generated") for c in cases) and os.path.exists(path):
        cached = autocase.cache_load(path) or {}
        if cached.get("cases"):
            notes["stale"] = True
            cases = [("generated %d" % i, fn.entry, (lambda m, c=c: gen.apply(m, c)), c["args"],
                      c.get("limit", 200_000), c) for i, c in enumerate(cached["cases"])] + cases
        elif cached.get("failed"):
            notes["stale"] = True
            notes["failed"] = cached["failed"]
    dicts = [c[5] for c in cases if c[5] is not None]
    try:
        dicts += autocase.kept_load(RUN["image"], name, gen.real) + autocase.extra_load(RUN["image"], name)
    except Exception:
        pass
    notes["cases"] = len(cases)
    if not cases and not notes["failed"]:
        notes["failed"] = "no cases cached"
    ctx = RUN["ctx"]
    stubbed, stops, polled = collections.Counter(), collections.Counter(), set()
    for c in dicts:
        for callee in {ctx.callee_name(call[0]) for call in c.get("calls", ())}:
            stubbed[callee] += 1
        for callee in {ctx.callee_name(s) for s in c.get("stops", ())}:
            stops[callee] += 1
        polled.update(p[0] for p in c.get("poll", ()))
        notes["hang"] += bool(c.get("hang"))
    notes.update(stubbed=dict(sorted(stubbed.items())), stops=dict(sorted(stops.items())),
                 polled=["0x%x" % a for a in sorted(polled)])
    return cases, notes


def machine_for(name, setup, words):
    from dread.autocase import cover_span, generator_for
    from dread.emu import Machine
    prog = RUN["program"]
    m = Machine(RUN["layout"], RUN["original"], None, isa_of=prog.isa_at)
    m.plan, m.replay, m.function = [], False, name
    setup(m)
    m.cover(*cover_span(prog, words))
    gen = generator_for(RUN)
    m.trace_reads(RUN["image"].base, RUN["image"].end, gen.code, gen.owners)
    return m


def end_of(m, error, gen):
    """How a run ended, in words: by a return, at a stub that ends it, at the instruction limit,
    spinning on a register, a fault (where), or touching the image (which the generator drops)."""
    def where():
        fn = RUN["program"].function_at(m.reg("pc") & ~1)
        return " in %s" % fn.name if fn is not None else ""
    if error is not None:
        if "did not return" in str(error):
            return "at the instruction limit%s" % where()
        return "a fault: %s" % gen.stopped_where(m, error).split("; registers:")[0]
    if gen.touches_image(m):
        return "it loads code as data or stores into the image (the generator drops such inputs)"
    if getattr(m, "stopped_at", None):
        return "at a stub that ends the run (%s)" % m.stopped_at
    if m.spinning:
        return "spinning on a register%s" % where()
    if m.timed_out:
        return "at the instruction limit%s" % where()
    return "by a return"


def run_cases(name, fn, cases, flow, regions):
    """The cases run on the original: {"runs", "ends": {how: runs}, "covered": {address}, "values":
    {frontier: {"reached", "registers": {register: [[value, runs]]}}}, "calls": {call: {how: runs}},
    "forced": {frontier: how the forced run ended}}."""
    from unicorn import UC_HOOK_CODE
    from dread.autocase import generator_for
    from dread.emu import EmuError
    gen = generator_for(RUN)
    watch, branches, calls, targets = {}, set(), set(), {}
    for region in regions:
        for w in region["ways"]:
            p = int(w["at"], 16)
            if w["cause"] in ("branch", "switch"):
                branches.add(p)
                targets.setdefault(p, int(w["to"], 16))
                for s, r in w["inputs"]:
                    watch.setdefault(int(s, 16), set()).add((p, r))
            elif w["cause"] == "call":
                calls.add(p)
    words = sorted(set(fn.insns) | set(flow.code))
    seen = {p: collections.defaultdict(collections.Counter) for p in branches}
    reached, first = collections.Counter(), {}
    out = {"runs": 0, "ends": collections.Counter(), "covered": set(), "calls": {}, "forced": {}}
    for k, (label, addr, setup, args, limit, case) in enumerate(cases[:RUN_CASES]):
        pending, hits = {}, collections.Counter()
        try:
            m = machine_for(name, setup, words)
        except Exception:
            continue

        def at_decider(uc, address, size, user, m=m, pending=pending):
            for p, r in watch.get(address, ()):
                if r != "flags":
                    try:
                        pending.setdefault(p, {})[r] = m.reg(r)
                    except Exception:
                        pass

        def at_frontier(uc, address, size, user, pending=pending, hits=hits, k=k):
            if address in branches:
                reached[address] += 1
                first.setdefault(address, k)
                for r, v in pending.pop(address, {}).items():
                    seen[address][r][v] += 1
            if address in calls:
                hits[address] += 1
        try:
            for a in sorted(watch):
                m.hook_add(UC_HOOK_CODE, at_decider, begin=a, end=a)
            for a in sorted(branches | calls):
                m.hook_add(UC_HOOK_CODE, at_frontier, begin=a, end=a)
        except Exception:
            continue
        error = None
        try:
            m.call(addr, *args, max_instructions=limit)
        except EmuError as e:
            error = e
        except Exception as e:
            error = EmuError("%s: %s" % (type(e).__name__, e))
        end = end_of(m, error, gen)
        out["runs"] += 1
        out["ends"][end] += 1
        out["covered"] |= m.covered_insns(words)
        for a in hits:
            out["calls"].setdefault("0x%x" % a, collections.Counter())[end] += 1
    out["values"] = {"0x%x" % p: {"reached": reached[p], "registers": {
        r: [["0x%x" % v, n] for v, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:VALUES_SHOWN]] +
        ([["...", len(c) - VALUES_SHOWN]] if len(c) > VALUES_SHOWN else []) for r, c in sorted(regs.items())}}
        for p, regs in sorted(seen.items())}
    for p in sorted(first):                 # the forced runs: the missed way, the first time there
        _, addr, setup, args, limit, _ = cases[first[p]]
        out["forced"]["0x%x" % p] = forced_run(name, setup, addr, args, limit, words, p, targets[p], gen)
    out["ends"] = dict(sorted(out["ends"].items()))
    out["calls"] = {a: dict(sorted(c.items())) for a, c in sorted(out["calls"].items())}
    return out


IT_BACK = 4                 # instructions an IT block's IT stands in front of its last one at most


def send_from(p):
    """Where a forced run is sent from for the frontier instruction at p: p itself, or the IT
    instruction in front when p is predicated inside a Thumb IT block (Unicorn takes no pc written in
    a code hook there: the instruction runs all the same). Leaving at the IT skips the block, which is
    what the block does when every instruction in it has p's condition and the condition fails."""
    prog = RUN["program"]
    insn = prog.insn(p)
    if insn is None or prog.arch.predicate(insn) is None:
        return p
    a = p
    for _ in range(IT_BACK):
        a -= 2
        prev = prog.insn(a)
        if prev is not None and prev.address + prev.size <= p and prev.mnemonic.split(".")[0].startswith("it") \
                and prog.arch.predicate(prev) is None and len(prev.mnemonic.split(".")[0]) <= 4:
            return a
    return p


def forced_run(name, setup, addr, args, limit, words, p, r, gen):
    """How a run of the case ends when it is sent to r the first time it reaches p (send_from), and
    which instructions it then runs: {"end", "covered"}; "end" says so when the run did not get to r
    after all."""
    from unicorn import UC_HOOK_CODE
    from dread.emu import EmuError
    arch, prog = RUN["program"].arch, RUN["program"]
    try:
        m = machine_for(name, setup, words)
    except Exception as e:
        return {"end": "not run: %s" % e, "covered": []}
    count, at = [0], send_from(p)

    def at_branch(uc, address, size, user):
        count[0] += 1
        if count[0] == 1:
            m.reg("pc", arch.start_address(r, prog.isa_at(r)))
            settle = getattr(arch, "settle_it", None)
            if settle is not None and getattr(m.layout, "settle_it", False):
                settle(uc)
    error = None
    try:
        m.hook_add(UC_HOOK_CODE, at_branch, begin=at, end=at)
        m.call(addr, *args, max_instructions=limit)
    except EmuError as e:
        error = e
    except Exception as e:
        error = EmuError("%s: %s" % (type(e).__name__, e))
    covered = sorted(m.covered_insns(words))
    if r not in covered:
        return {"end": "not sent: the run did not get to 0x%x (%s)" % (r, end_of(m, error, gen)), "covered": covered}
    return {"end": end_of(m, error, gen), "covered": covered}


def search_notes(region, notes, runs):
    """The search's words on one region: what the cases did at its main way in."""
    said = []
    if region["cause"] == "not entered":
        if notes.get("kind") == "no cases" and notes.get("reason"):
            said.append("the verdict: %s" % notes["reason"][:200])
        if notes.get("failed") and not (notes["failed"].startswith("no cases") and notes.get("kind") == "no cases"):
            said.append("generation: %s" % notes["failed"])
        said += [line[:200] for line in notes.get("refused", ())[:2]]
        return said
    w = region["ways"][0] if region["ways"] else None
    if w is None or not notes.get("cases"):
        return said
    callee = w.get("callee")
    if callee and callee in notes.get("stops", {}):
        said.append("the cases end at each call of %s (runs hung in it: stops, in %d of %d cases)" % (
            callee, notes["stops"][callee], notes["cases"]))
    elif callee and callee in notes.get("stubbed", {}):
        said.append("%s is stubbed in %d of %d cases (its answer an input)" % (callee, notes["stubbed"][callee],
                                                                              notes["cases"]))
    elif callee and w["cause"] != "call":
        said.append("%s runs for real in every case" % callee)
    if w.get("device") and w["device"] in notes.get("polled", ()):
        said.append("the cases cut a wait on %s (poll stages)" % w["device"])
    if runs:
        ran = sorted(set(int(a, 16) for a in region["missed"]) & runs["covered"])
        if ran:
            said.append("the cached cases run %d of its instructions: a case the lift check did not count" % len(ran))
    return said


def key_of(region, notes):
    """The table's source column for a region: the main way's source (a field by its base, a callee by
    whether the cases stub it, a device register by whether they poll it), or what is known of a call,
    a function not entered."""
    cause = region["cause"]
    w = region["ways"][0] if region["ways"] else {}
    known = bool(notes and notes.get("cases"))
    if cause in ("branch", "switch"):
        cls = w.get("source") or "-"
        if cls == "field":
            return "field of %s" % BASES.get(w.get("base"), w.get("base"))
        if cls == "callee" and known and w.get("callee") != "(indirect)":
            return "callee, stubbed" if w.get("callee") in notes.get("stubbed", {}) else "callee, real"
        if cls == "device" and known and w["device"] in notes.get("polled", ()):
            return "device, polled"
        return cls
    if cause == "call":
        ends = w.get("ends")
        if ends:                            # how the runs that got to the call ended
            if all("(never returns)" in e for e in ends):
                return "the cases stop there"
            if all("(calls)" in e for e in ends):
                return "loop cases end there"
            if all("instruction limit" in e or "spinning" in e for e in ends):
                return "runs hang in the callee"
            return "runs end otherwise"
        if known and w.get("reached") == 0:
            return "only the system run got there" if notes.get("loop") else "no case got there"
        if w.get("callee") is None:
            return "through a register"
        if known and w["callee"] in notes.get("stops", {}):
            return "the cases stop there"
        return "a callee"
    if cause == "not entered" and notes:
        if notes.get("kind") == "no cases" or notes.get("failed") and not notes["failed"].startswith("no cases"):
            return "generation failed"
        if any(line.startswith("REFUSED") for line in notes.get("refused", ())):
            return "cases refused"
        if notes.get("refused"):
            return "cases failed"
        if notes.get("failed"):
            return "no cases cached"
    return "-"


def forced_key(way):
    """The forced run's column in the second table: how the main way's forced run ended (returns,
    faults, limit, spins, stops, touches the image, other), or that no case got there."""
    if way is None:
        return "-"
    if way.get("reached") == 0:
        return "no case got there"
    if not way.get("forced"):
        return "-"
    end = way["forced"]["end"]
    if end.startswith(("not sent", "not run")):
        return "not sent"
    for word, key in (("by a return", "returns"), ("a fault", "faults"), ("instruction limit", "limit"),
                      ("spinning", "spins"), ("stub that ends", "stops"), ("image", "touches the image")):
        if word in end:
            return key
    return "other"


def measure(name):
    """(name, the function's record for the JSON)."""
    ctx = RUN["ctx"]
    kind, missed, reason = RUN["selected"][name]
    fn = ctx.program.by_name[name]
    lifter = ctx.lifter
    loop = kind == "loop cases and the system run" or kind == "cached cases" and lifter is not None and \
        lifter.never_returns(name) and lifter.handler_kind(fn) is None
    if missed is None:                      # (named, not dropped for missed instructions: its cached cases)
        missed = cached_missed(name, fn, loop)
    flow, regions = analyse(ctx, name, missed)
    record = {"kind": kind, "missed": len(missed), "instructions": len(fn.insns), "entry": "0x%x" % fn.entry,
              "reason": reason, "regions": regions}
    notes, runs = {"kind": kind}, None
    if RUN.get("runs"):
        try:
            cases, notes = case_notes(name, fn, loop)
        except Exception as e:
            cases, notes = [], {"failed": "%s: %s" % (type(e).__name__, e)}
        notes["kind"], notes["reason"] = kind, reason
        notes["refused"] = RUN.get("refusals", {}).get(name, [])
        if cases:
            try:
                runs = run_cases(name, fn, cases, flow, regions)
            except Exception as e:
                notes["runs failed"] = "%s: %s" % (type(e).__name__, e)
        record["cases"] = notes
        if runs:
            record["runs"] = {"runs": runs["runs"], "ends": runs["ends"]}
    for region in regions:
        for w in region["ways"]:
            if runs and w["at"] in runs["values"]:
                w["values"] = runs["values"][w["at"]]
                w["reached"] = w["values"]["reached"]
            if runs and w["cause"] == "call":
                w["ends"] = runs["calls"].get(w["at"], {})
                w["reached"] = sum(w["ends"].values())
            if runs and w["at"] in runs["forced"]:
                forced = runs["forced"][w["at"]]
                inside = set(int(a, 16) for a in region["missed"])
                w["forced"] = {"end": forced["end"], "ran": len(inside & set(forced["covered"]))}
        if RUN.get("runs"):
            region["notes"] = search_notes(region, notes, runs)
        region["key"] = [region["cause"], key_of(region, notes)]
        region["forced"] = forced_key(region["ways"][0] if region["ways"] else None)
    return name, record


def cached_missed(name, fn, loop):
    """The instructions of fn its cached cases leave unrun, the cases run on the original (all of them
    without a case; none without runs, --no-runs)."""
    if not RUN.get("runs"):
        return []
    cases, _ = case_notes(name, fn, loop)
    if not cases:
        return list(fn.insns)
    return sorted(set(fn.insns) - run_cases(name, fn, cases, Flow(RUN["ctx"], fn), [])["covered"])


def measure_all(names, jobs=None):
    """{name: record} (RUN prepared, RUN["selected"] set), the functions spread over worker processes."""
    results = {}
    if jobs == 1 or len(names) <= 1:
        for name in names:
            results[name] = measure(name)[1]
        return results
    with multiprocessing.get_context("fork").Pool(jobs or workers(memory_each=resident()),
                                                  initializer=die_with_parent) as pool:
        for name, record in pool.imap_unordered(measure, names):
            results[name] = record
    return results


# ---- the table ----

def table(results, program, examples=1, key=lambda region: tuple(region["key"])):
    """[{cause, source, regions, instructions, functions, examples}] over all functions, most
    instructions first (key: what a row groups by)."""
    rows = {}
    for name in sorted(results, key=lambda n: (program.by_name[n].entry, n)):
        for region in results[name]["regions"]:
            k = key(region)
            row = rows.setdefault(k, {"cause": k[0], "source": k[1], "regions": 0, "instructions": 0,
                                      "functions": set(), "examples": []})
            row["regions"] += 1
            row["instructions"] += region["instructions"]
            row["functions"].add(name)
            if len(row["examples"]) < examples:
                row["examples"].append("%s %s: %s" % (name, region["start"], region["text"]))
    out = []
    for row in rows.values():
        row["functions"] = len(row["functions"])
        out.append(row)
    out.sort(key=lambda r: (-r["instructions"], -r["regions"], r["cause"], r["source"]))
    return out


def totals(results):
    return {"functions": len(results), "regions": sum(len(r["regions"]) for r in results.values()),
            "instructions": sum(r["missed"] for r in results.values()),
            "kinds": dict(sorted(collections.Counter(r["kind"] for r in results.values()).items())),
            "stale": sum(1 for r in results.values() if r.get("cases", {}).get("stale"))}


def region_lines(name, region):
    lines = ["%-28s %s %4d  %-11s %-22s %s" % (name, region["start"], region["instructions"], region["cause"],
                                               region["key"][1], region["text"])]
    for w in region["ways"][:1]:
        if w.get("sources") and len(w["sources"]) > 1:
            lines.append("also: " + "; ".join(t for _, t in w["sources"][1:4]) + ("; ..." if len(w["sources"]) > 4
                                                                                   else ""))
        values = w.get("values")
        if values is not None:
            regs = "; ".join("%s %s" % (r, ", ".join(v if n == 1 or v == "..." else "%s x%d" % (v, n)
                                                      for v, n in vals)) for r, vals in values["registers"].items())
            lines.append("runs there: %d%s" % (values["reached"], " (%s)" % regs if regs else ""))
        if w.get("ends"):
            lines.append("runs at the call ended: %s" % ", ".join("%s %d" % kv for kv in w["ends"].items()))
        if w.get("forced"):
            lines.append("sent that way: ends %s, %d of its instructions run" % (w["forced"]["end"],
                                                                                  w["forced"]["ran"]))
    if len(region["ways"]) > 1:
        lines.append("%d more way%s in: %s" % (len(region["ways"]) - 1, "" if len(region["ways"]) == 2 else "s",
                                              "; ".join(w["text"] for w in region["ways"][1:3])))
    lines += region.get("notes", [])
    return lines[0] + "".join("\n" + " " * 8 + line for line in lines[1:])


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread uncovered", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--verdicts", help="a lift check's verdicts.tsv (default: BUILD/lift-check/IMAGE/verdicts.tsv)")
    parser.add_argument("--json", help="write the regions, their ways in and the table to this file")
    parser.add_argument("--no-runs", action="store_true", help="the code only: no cases read or run")
    parser.add_argument("--quiet", action="store_true", help="the table only, no lines per region")
    parser.add_argument("--examples", type=int, default=1, help="examples per row of the table in the JSON")
    parser.add_argument("--kind", choices=sorted(KINDS), help="only the functions dropped so: cases (their cases), "
                        "loop (a function that never returns: its loop cases and the system run), none (no case), "
                        "cached (named, not dropped so: what its cached cases miss)")
    parser.add_argument("-j", "--jobs", type=int)
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)
    polite()
    try:
        target, image = tgt.from_args(args)
        path = args.verdicts or verdicts_path(target, image)
        if not os.path.exists(path):
            raise tgt.TargetError("no verdicts at %s: run `dread lift --check` first" % path)
        ctx = prepare(target, image, path, runs=not args.no_runs)
    except tgt.TargetError as e:
        print("dread uncovered: %s" % e, file=sys.stderr)
        return 1
    prog = ctx.program
    missing = [n for n in args.names if n not in prog.by_name]
    if missing:
        print("dread uncovered: no function %s" % ", ".join(missing), file=sys.stderr)
        return 2
    chosen = selected(read_verdicts(path), prog, args.names)
    if args.kind:
        chosen = {n: v for n, v in chosen.items() if v[0] == KINDS[args.kind]}
    RUN["selected"] = chosen
    names = sorted(chosen, key=lambda n: (prog.by_name[n].entry, n))
    results = measure_all(names, args.jobs)
    rows = table(results, prog, max(1, args.examples))
    if not args.quiet:
        for name in names:
            for region in results[name]["regions"]:
                print(region_lines(name, region))
    t = totals(results)
    print("\ndread uncovered: %d functions, %d regions, %d missed instructions (%s)" % (
        t["functions"], t["regions"], t["instructions"], ", ".join("%s %d" % kv for kv in t["kinds"].items())))
    if t["stale"]:
        print("the cases of %d of them were cached under another key than the generator makes now (an older "
              "generator or other options): those were taken, as the lift check that wrote the verdicts ran "
              "them" % t["stale"])
    print("%-11s %-24s %7s %12s %9s  %s" % ("cause", "source", "regions", "instructions", "functions", "example"))
    for row in rows:
        print("%-11s %-24s %7d %12d %9d  %s" % (row["cause"], row["source"], row["regions"], row["instructions"],
                                                row["functions"], row["examples"][0] if row["examples"] else ""))
    forced = None
    if not args.no_runs:
        forced = table(results, prog, max(1, args.examples), key=lambda region: (region["cause"], region["forced"]))
        forced = [row for row in forced if row["source"] != "-"]
        if forced:
            print("\nsent the missed way (the forced runs of the main ways in):")
            print("%-11s %-24s %7s %12s %9s  %s" % ("cause", "the run", "regions", "instructions", "functions",
                                                    "example"))
            for row in forced:
                print("%-11s %-24s %7d %12d %9d  %s" % (row["cause"], row["source"], row["regions"],
                                                        row["instructions"], row["functions"], row["examples"][0]))
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"totals": t, "table": rows, "forced": forced or [], "functions": results}, f, indent=1,
                      sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
