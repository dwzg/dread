"""dread trace: one case of a function in the original and the rebuilt image side by side, to the
first point where the two runs part.

    dread trace NAME LABEL [--target DIR] [--image NAME] [--lifted | --readable [--whole] | --recon PAYLOAD]
                [--src DIR] [--last N] [--no-lines] [--json FILE]

LABEL is the case's label as dread test, dread lift --check and dread readable --check print it in a
FAIL line ("generated 3", "kept 0", "written 2", or a written case's own label; the brackets may stay).

The rebuilt image is the one the failing check ran:

- by default the one dread test runs, BUILD/recon/IMAGE/payload.bin (--recon PAYLOAD: another one, with
  the placement.json, lifted.txt and lifted.json its build wrote beside it), not built again;
- --lifted: built as dread lift --check builds a round, into BUILD/trace/IMAGE/lifted: the lifted
  table's functions but those the system run accepted, and NAME, from lifted C (what the target's src,
  or --src, defines from that);
- --readable: built as dread readable --check builds a round, into BUILD/trace/IMAGE/readable: those
  from lifted C, and the readable table's functions and NAME from readable C, with the readable_types
  table as the check left it (--whole: NAME's frame region as one object, the check's second try).

The case is set up exactly as the runner sets it up (dread.runner prepare and run_function: the same
case list, the same Machine settings, stubs, memory, peripheral polls, kept registers and plan, the
original run with the case's limit, the rebuilt one with REBUILT_BUDGET times it unless the case may
hang), and both runs are watched at the points both images share, which the C cannot have elsewhere:

- calls into other functions, at the callee's entry (a moved function's C, which the rebuilt image
  enters directly or through the branch at its old entry, counts as its entry; a branch back to the
  entry of the function that runs, with its frame, is a loop, not a call), with the registers the
  callee reads at its entry (dread.regflow) and its stack arguments (the lifter's count; none where
  the architecture has no lifter); two values that both point into the stack are taken as equal (the
  C keeps its own frame, as dread.emu takes a result so), and the bytes behind them are compared
  instead: from the address to the end of the frame object it lies in (lifted C's frame array, which
  mirrors the original's frame; readable C's frame object stackN, at sp0 - N; the frame up to its top
  in a function that is the original's code in both images; nothing in C written by hand), at most
  POINTED bytes, only those the caller's call stored (a byte stored before it is what earlier frames
  left there, which C lays out otherwise; a push and the words its entry saves registers into hold no
  data);
- the log: stub calls and device accesses, each entry as dread test compares it, in order;
- stores outside the stack and the device ranges (address, size, value), compared by stretch: what a
  stretch between two calls leaves in memory (the bytes written, the last value of each) must be the
  same, so C that stores in another order or width inside a stretch does not part; where it is not,
  the first store in order whose bytes differ is named;
- the return: what dread test compares at the end (dread.emu Result.diff), so that a case dread test
  fails never traces as agreeing. For a register that differs there (the result, a callee-saved or a
  kept one), both runs are made once more to name the instruction that set it last.

Calls into the architecture's run-time helpers (arch.runtime_helper: a division, a 64-bit shift,
clang's memclr; compiled C calls them where its compiler chose) and into shared code that a lifted
function's C holds (dread.lift's bodies) are no shared points: their stores count with the stretch
around them. Locals are compared only through what reaches a shared point.

The original runs first and its events are kept; the rebuilt image runs after it, compared event by
event, and stops at the first divergence in what dread test compares (the hard one). A call of the
same callee with other arguments, or with other bytes behind them, is where the runs part first, but
the callee may not read that value on this path: it is reported as the first divergence, and the
trace goes on to the first difference dread test compares, reported after it. Where one run makes a
call or a log entry the other does not (or ends), the report adds where its path left the other's:
its first return, behind the last shared event, from a function the other run had not left.

The report names, for both images, the instruction (address, disassembly, function), for the rebuilt
image the C line it came from (the line table of a twin build compiled with -g, BUILD/trace/IMAGE/
MODE-g, used only when its image is byte for byte the traced one; read with pyelftools, the dwarf
extra) and, where the function is built from lifted or readable C, the statement of that C for the
original's instruction (its comment names the instruction, or, in readable C, the statement that
absorbed it: dread.cparse Addr), the call stack of both runs, the last shared events before it
(--last), and a stack frame, both runs made again and stopped at that point: of the innermost function
built from lifted or readable C that both runs are in (else the function's), the original's words
labelled by the C's objects (as above, and dread.tidy's slot variables sN) and by the registers its
entry saved, beside each object's bytes in the rebuilt image where the twin's debug information places
it (at a fixed offset from the frame base or the stack pointer), "(not set)" where the frame's call
has stored none of its bytes yet. Ends with what dread test reports for the whole case.

--json FILE writes the same report as data besides (docs/formats.md, "dread trace --json"): what was
traced, both divergences with each image's instruction, its C line or statement, where a register was
set last, the call stacks, the last shared events and the frame's rows; or the error that stopped the
trace. The printed report is made from that data (TraceResult.data), so the two say the same; dread
gui's trace view reads the file.

Exit status: 0 when the case agrees, 1 when it parts, 2 when it cannot be traced (no such function or
case, a build that fails, a case whose setup fails).

Times: a trace builds the image twice (--no-lines: once; the default mode builds only the twin), and
a lifted or readable build lifts every function of the lifted table: on FreeRTOS's GCC -Os demo (332
lifted functions) about 20 seconds once the objects are compiled, most of it lifting; on gizmo about
a second. The runs themselves take Python hooks on every block and store, a few times the runner's
time for the case.

Not established: the runner's refusals and excuses (a stack address taken for a value, stale stack
data, saved registers touched, a call into the middle of a function, a result that depends on the
caller's registers) are not checked again; a case dread test refuses or excuses traces as it runs. A
function entered by falling into it from the code before it (no branch) is not seen as a call; a call
the C's compiler makes where the original's code has none (other than a run-time helper: a structure
copied through memcpy, say) parts the runs where nothing else may differ. A register the callee reads
on other paths only, or a byte behind a pointer it does not read, parts the runs without consequence:
the report then shows the first difference dread test compares after it, or that there is none. On
the MSP430 (DREAD's own engine) it ran on gizmo by hand only, without C lines (pyelftools does not
read those line tables). The twin build's line table maps an address to the line the compiler gave
it, which for optimised code is a close guess, not a proof; a variable's location is read only where
it is one offset from the frame base or the stack pointer for the whole function.
"""
import argparse
import bisect
import os
import re
import sys
from array import array
from collections import deque

from unicorn import UC_HOOK_BLOCK, UC_HOOK_MEM_WRITE

from dread.arch import code_address
from dread.elf import dwarf_code_value
from dread import target as tgt
from dread.emu import IO_EVENTS, EmuError, Machine

LAST = 6            # shared events shown before the divergence (--last)
RECENT = 48         # blocks a run keeps to show where it circled when it ran into its limit
RUNAWAY = 100_000   # stores past the original's in one stretch after which the rebuilt run is stopped
SOFT_SHOWN = 4      # calls with other arguments listed before the first hard divergence
POINTED = 256       # bytes behind an argument that points into the stack compared at most
MAX_EVENTS = 1_000_000  # the original's events kept (about 200 bytes each); later ones are not compared
STACK_SHOWN = 256   # bytes below the stack pointer a snapshot keeps besides the stack in use
# The DWARF number of the stack pointer, per architecture (each architecture's psABI): a frame base
# or a location relative to it (clang's frame base on RISC-V and ARM)
DWARF_SP = {"arm": 13, "riscv": 2, "msp430": 1, "mips": 29}


class TraceError(Exception):
    """The case cannot be traced: no such case, a build that fails, a setup that fails."""


# ---- the runs ----

class Frame:
    """One call on a run's stack: the original's entry of the function, the stack pointer at its
    entry, the address it returns to, the block in front of its entry (its last instruction entered
    the function), its caller's frame, whether a tail call replaced the caller's frame, whether it
    is transparent (a run-time helper or shared code: no shared point), and the run's count of stack
    stores when it was entered (born: a byte of its frame stored later is one the call defined)."""
    __slots__ = ("entry", "sp", "ret", "came", "parent", "tail", "helper", "born")

    def __init__(self, entry, sp, ret, came, parent, tail=False, helper=False, born=0):
        self.entry, self.sp, self.ret, self.came, self.parent = entry, sp, ret, came, parent
        self.tail, self.helper, self.born = tail, helper, born

    def chain(self):
        """The frames from the outermost to this one."""
        out, f = [], self
        while f is not None:
            out.append(f)
            f = f.parent
        return out[::-1]


class Event:
    """One shared point of a run: kind "entry" (data: (callee's entry, ((register, value), ...),
    (stack words, ...), the bytes behind the arguments that point into a frame: Recorder.pointed, the
    original's run only)), "log" (data: the log entry) or "store" (data: (address, size, value)); seq,
    its place among the run's events; pc, the instruction (for an entry, its first; site() names
    the instruction that entered it); frame, the top of the run's stack; stretch, the stretch of
    stores it falls into (a call starts the next)."""
    __slots__ = ("kind", "seq", "pc", "frame", "data", "stretch")

    def __init__(self, kind, seq, pc, frame, data, stretch):
        self.kind, self.seq, self.pc, self.frame, self.data, self.stretch = kind, seq, pc, frame, data, stretch


class Snapshot:
    """A run stopped at one point: its registers, its stack (from STACK_SHOWN bytes below the stack
    pointer up to the room for stack arguments above the stack's top), its frames and, where the run
    kept them, when each stack byte was stored last (set: whether a frame's call stored a byte)."""

    def __init__(self, m, frame, stored_at=None, stack_base=0):
        self.stored_at, self.stack_base = stored_at, stack_base
        lay = m.layout
        self.regs = {}
        for name in list(lay.arch.general_regs()) + ["sp", "pc"]:
            try:
                self.regs[name] = m.reg(name)
            except Exception:
                pass
        sp = self.regs.get("sp", lay.stack_top)
        self.lo = max(lay.stack_top - lay.stack_size, (sp - STACK_SHOWN) & ~3)
        try:
            from dread.emu import ARGS_ROOM
            self.data = m.read(self.lo, lay.stack_top + ARGS_ROOM - self.lo)
        except EmuError:
            self.lo, self.data = lay.stack_top, b""
        self.frames = frame.chain() if frame is not None else []

    def set(self, address, n, frame):
        """Whether frame's call (or its callees) stored any of n bytes at address; None where not kept."""
        if self.stored_at is None:
            return None
        off = address - self.stack_base
        return any(self.stored_at[i] > frame.born for i in range(max(0, off), min(off + n, len(self.stored_at))))

    def read(self, address, n):
        """n bytes at address from the stack kept, or None outside it."""
        off = address - self.lo
        if off < 0 or off + n > len(self.data):
            return None
        return self.data[off:off + n]


class Context:
    """What both runs' recorders share: the program, the layout, the entries and the facts of each
    callee (its name, the registers it reads, its stack arguments, whether it is transparent)."""

    def __init__(self, run, lifter=None, objects=None):
        prog, lay = run["program"], run["layout"]
        self.program, self.layout, self.arch = prog, lay, lay.arch
        self.objects = objects or (lambda name: None)
        self.names = {fn.entry: fn.name for fn in prog.functions}
        self.entries = set(self.names)
        helper = getattr(self.arch, "runtime_helper", None)
        self.transparent = {e for e, n in self.names.items() if helper is not None and helper(n)}
        for name, addrs in run.get("bodies", {}).items():     # shared code a lifted function's C holds
            fn = prog.by_name.get(name)
            own = set(fn.insns) if fn is not None else set()
            self.transparent |= {a for a in addrs if a not in own and a in self.entries}
        flow = run.get("regflow")
        general = set(self.arch.general_regs()) - {"sp", "pc", "lr", getattr(self.arch, "link_reg", None)}
        abi_args = list(run["target"].abi.arg_regs)
        self.reads = {}
        for e, name in self.names.items():
            regs = flow.read_regs(name) if flow is not None else abi_args
            self.reads[e] = tuple((r, self.arch.reg_id(r)) for r in regs if r in general)
        self.lifter, self._nstack, self._saves, self._push = lifter, {}, {}, {}
        self.word = lay.word
        self.io = list(lay.io)
        self.sp_id = self.arch.reg_id("sp")
        self.pc_id = lay.pc
        self.lr_id = lay.lr
        self.result_id = lay.result_regs[0]

    def nstack(self, entry):
        """The stack arguments of the function at entry (the lifter's count; 0 without a lifter or
        where it is not known)."""
        if entry not in self._nstack:
            n = 0
            fn = self.program.by_entry.get(entry)
            if self.lifter is not None and fn is not None:
                try:
                    n = self.lifter.stack_args(fn) or 0
                except Exception:
                    n = 0
            self._nstack[entry] = n
        return self._nstack[entry]

    def saved(self, entry):
        """{depth below sp0: bytes} of the words the function at entry saves its caller's registers into
        (dread.frames save_words): no data of its frame."""
        if entry not in self._saves:
            from dread.frames import save_words
            fn = self.program.by_entry.get(entry)
            try:
                self._saves[entry] = {d: n for d, (_, n) in save_words(self.program, fn).items()} if fn else {}
            except Exception:
                self._saves[entry] = {}
        return self._saves[entry]

    def is_push(self, address):
        """Whether the original's instruction at address is a push (the arch layer's stack_kind): what
        it stores are saved registers, no data."""
        if address not in self._push:
            insn = self.program.insn(address) if address in self.program.code else None
            kind = getattr(self.arch, "stack_kind", None)
            self._push[address] = insn is not None and kind is not None and kind(insn) == "push"
        return self._push[address]

    def pointed_span(self, holder, value):
        """(label, bytes) of what an argument that points into a frame (holder's: the innermost
        frame whose stack pointer at its entry lies above it) reaches there: in a function built from
        lifted or readable C its object from the address on (objects: the C's frame array or frame
        object it lies in; none where no object holds it, or where the function's C is written by
        hand), in the original's code the frame from the address up to its top; at most POINTED."""
        name = self.names.get(holder.entry, "0x%x" % holder.entry)
        objs = self.objects(name)
        sp0 = holder.sp
        if objs is None:
            return "the frame of %s at sp0-0x%x" % (name, sp0 - value), min(sp0 - value, POINTED)
        for label, depth, size in objs:
            lo = sp0 - depth
            if lo <= value < lo + size:
                where = label if value == lo else "%s+0x%x" % (label, value - lo)
                return "%s of %s" % (where, name), min(lo + size - value, POINTED)
        return None


class Recorder:
    """The hooks of one run: entries (a block hook, which also keeps the run's frames), stores outside
    the stack and the device ranges (a write hook) and the log (Machine.log wrapped). rebuilt: the
    entry of each moved function's C counts as its entry (aliases: original entry -> C's address;
    hops: the blocks behind an old entry's branch, not counted again). listener(recorder, event) is
    called with every event (the comparison); stop_at: the event at which the run stops with a
    Snapshot (taken before that event happens)."""

    def __init__(self, ctx, m, aliases=None, hops=None, listener=None, stop_at=None, capture=False,
                 max_events=None):
        self.ctx, self.m, self.capture = ctx, m, capture
        self.max_events, self.truncated = max_events, False
        lay = ctx.layout
        self.stack_base = lay.stack_top - lay.stack_size
        self.clock = 0                      # stack stores so far (the original's run: capture)
        self.stored_at = array("Q", bytes(8 * lay.stack_size)) if capture or stop_at is not None else None
        self.read = m.core.read32
        self.events, self.top, self.prev = [], None, None
        self.stretch, self.starts = 0, {}       # the current stretch; stretch -> the call that starts it
        self.blocks = 0
        self.recent = deque(maxlen=RECENT)
        self.listener, self.stop_at = listener, stop_at
        self.snapshot, self.halted, self.hops = None, False, ()
        self.returns = []                   # [(the next event's seq, frame, the block that returned, result)]
        self.entry_of = {e: e for e in ctx.entries}
        self.hop_after = {}
        for old, new in (aliases or {}).items():
            self.entry_of[new] = old
            self.hop_after[old] = frozenset((hops or {}).get(old, ())) | {new}
        self.in_stack = ctx.layout.in_stack
        m.hook_add(UC_HOOK_BLOCK, self.on_block)
        m.hook_add(UC_HOOK_MEM_WRITE, self.on_write)
        log = m.log

        def logged(*event):
            self.on_log(event)
            log(*event)
        m.log = logged

    # -- hooks --

    def on_block(self, uc, address, size, user):
        if self.halted:
            return
        self.blocks += 1
        if self.hops:
            if address in self.hops:        # (the branch at a moved function's old entry, to its C)
                self.prev = (address, size)
                return
            self.hops = ()
        top = self.top
        if top is not None and address == top.ret:
            sp = self.read(self.ctx.sp_id)
            while top is not None and address == top.ret and sp >= top.sp:
                if not top.helper:
                    self.returns.append((len(self.events), top, self.prev, self.read(self.ctx.result_id)))
                top = top.parent
            self.top = top
        e = self.entry_of.get(address)
        if e is not None:
            self.enter(e, address)
            self.hops = self.hop_after.get(address, ())
        self.prev = (address, size)
        self.recent.append(address)

    def enter(self, e, address):
        ctx = self.ctx
        sp = self.read(ctx.sp_id)
        if ctx.lr_id is not None:
            ret = code_address(ctx.arch, self.read(ctx.lr_id))
        else:                               # (pushed by the call: MSP430)
            try:
                ret = int.from_bytes(self.m.read(sp, ctx.word), "little")
            except EmuError:
                ret = None
        top = self.top
        while top is not None and top.sp < sp:      # (frames below this stack pointer have returned)
            top = top.parent
        tail = top is not None and top.sp == sp and top.ret == ret
        if tail and top.entry == e:         # (a branch back to its own entry: a loop, or a tail call of
            self.top = top                  # itself, which C may well write as a loop: no call)
            return
        frame = Frame(e, sp, ret, self.prev, top.parent if tail else top, tail, e in ctx.transparent, self.clock)
        self.top = frame
        if frame.helper:
            return
        args = tuple((r, self.read(rid)) for r, rid in ctx.reads.get(e, ()))
        stack = ()
        n = ctx.nstack(e)
        if n:
            base = sp + (ctx.word if ctx.lr_id is None else 0)
            try:
                raw = self.m.read(base, ctx.word * n)
                stack = tuple(int.from_bytes(raw[i:i + ctx.word], "little") for i in range(0, len(raw), ctx.word))
            except EmuError:
                stack = ()
        pointed = self.pointed(frame, args, stack, sp) if self.capture else ()
        self.stretch += 1
        ev = self.emit("entry", address, (e, args, stack, pointed))
        if ev is not None:
            self.starts[self.stretch] = ev

    def pointed(self, frame, args, stack, sp):
        """((argument, label, bytes, defined), ...) behind the arguments of a call that point into a
        frame of its callers (Context.pointed_span), read at the callee's entry; defined, a byte each:
        1 where that frame's call stored it, not by a push and not into a word its entry saves a
        register into (a byte stored before the call is what earlier frames left there, which C, laying
        out its frames otherwise, leaves otherwise; a saved register is no data: not compared)."""
        chain = frame.parent.chain() if frame.parent is not None else []
        out = []
        for where, v in list(args) + [("stack argument %d" % i, v) for i, v in enumerate(stack)]:
            if not self.in_stack(v) or v < sp:
                continue
            holder = next((f for f in reversed(chain) if f.sp > v), None)
            span = self.ctx.pointed_span(holder, v) if holder is not None else None
            if span is None or span[1] <= 0:
                continue
            try:
                data = self.m.read(v, span[1])
            except EmuError:
                continue
            off = v - self.stack_base
            saved = {holder.sp - d + j for d, n in self.ctx.saved(holder.entry).items() for j in range(n)}
            defined = bytes(1 if 0 <= off + i < len(self.stored_at) and self.stored_at[off + i] > holder.born
                            and v + i not in saved else 0 for i in range(len(data)))
            if any(defined):
                out.append((where, span[0], data, defined))
        return tuple(out)

    def on_write(self, uc, access, address, size, value, user):
        if self.halted:
            return
        if self.in_stack(address):
            if self.stored_at is not None:  # (when each stack byte was stored last: Recorder.pointed;
                self.clock += 1             # a push stores saved registers, no data)
                when = 0 if self.capture and self.ctx.is_push(self.read(self.ctx.pc_id)) else self.clock
                off = address - self.stack_base
                for i in range(max(0, off), min(off + size, len(self.stored_at))):
                    self.stored_at[i] = when
            return
        if any(lo <= address < hi for lo, hi in self.ctx.io):
            return
        self.emit("store", self.read(self.ctx.pc_id), (address, size, value & ((1 << 8 * size) - 1)))

    def on_log(self, event):
        if self.halted:
            return
        self.emit("log", self.read(self.ctx.pc_id), tuple(event))

    def emit(self, kind, pc, data):
        if self.max_events is not None and len(self.events) >= self.max_events:
            self.truncated = True           # (no more events kept: the comparison ends here)
            return None
        ev = Event(kind, len(self.events), pc, self.top, data, self.stretch)
        self.events.append(ev)
        if self.stop_at is not None and ev.seq == self.stop_at:
            self.snapshot = Snapshot(self.m, self.top, self.stored_at, self.stack_base)
            self.halt()
        elif self.listener is not None:
            self.listener(self, ev)
        return ev

    def halt(self):
        self.halted = True
        try:
            self.m.uc.emu_stop()
        except Exception:
            pass


# ---- the comparison ----

class Divergence:
    """Where the runs part: kind ("call", "log", "stores", "extra", "short", "limit", "fault",
    "result", "arguments"), the original's and the rebuilt image's event (either may be None), the
    differences in words, notes (stores that differ before a call or a log entry that does), and for
    a register that differs at the end the instructions that set it last in both runs (writers)."""

    def __init__(self, kind, original, rebuilt, what, notes=(), stores=None):
        self.kind, self.original, self.rebuilt, self.what = kind, original, rebuilt, what
        self.notes, self.stores = list(notes), stores
        self.soft = kind == "arguments"
        self.writers = None


def ran_out(out):
    """Whether a run ended at its limit (a case that may hang: its log ends so; else the error)."""
    if isinstance(out, Exception):
        return "did not return within" in str(out)
    return bool(out.calls) and out.calls[-1] == ("did not return",)


def register_of(line, layout):
    """The register a difference line of dread.emu Result.diff is about (the result register for the
    return value, else "REG after the return"), or None."""
    if line.startswith("return value"):
        return layout.arch.reg_name(layout.target.abi.result[0]) if hasattr(layout.arch, "reg_name") else \
            layout.target.abi.result[0]
    m = re.match(r"(\w+) after the return", line)
    return m.group(1) if m and m.group(1) != "flags" else None


def stack_pair(lay, a, b):
    """Whether two values both point into the stack (the C keeps its own frame)."""
    return lay.in_stack(a & 0xFFFFFFFF) and lay.in_stack(b & 0xFFFFFFFF)


def argument_differences(lay, want, got):
    """[(where, original value, rebuilt value)] of two entries of the same callee."""
    out = []
    for (reg, a), (_, b) in zip(want.data[1], got.data[1]):
        if a != b and not stack_pair(lay, a, b):
            out.append((reg, a, b))
    for i, (a, b) in enumerate(zip(want.data[2], got.data[2])):
        if a != b and not stack_pair(lay, a, b):
            out.append(("stack argument %d" % i, a, b))
    return out


def store_bytes(events):
    """[(address, byte, event)] of stores in order, one item a byte."""
    out = []
    for ev in events:
        addr, size, value = ev.data
        out.extend((addr + i, (value >> 8 * i) & 0xFF, ev) for i in range(size))
    return out


def compare_stores(lay, mine, theirs):
    """None when two stretches of stores leave the same bytes (the bytes written, the last value of
    each; a byte both last stored as a word that points into the stack counts as the same), else a
    dict: the first store in order that differs (its event in either run or None), and the bytes
    that differ ([(address, original byte or None, rebuilt byte or None, original event, rebuilt
    event)], in the original's order)."""
    a, b = store_bytes(mine), store_bytes(theirs)
    last_a = {addr: (v, ev) for addr, v, ev in a}
    last_b = {addr: (v, ev) for addr, v, ev in b}
    word = lay.word

    def same(addr):
        x, y = last_a.get(addr), last_b.get(addr)
        if x is None or y is None:
            return x is y
        if x[0] == y[0]:
            return True
        ex, ey = x[1], y[1]
        return ex.data[0] == ey.data[0] and ex.data[1] == ey.data[1] == word and stack_pair(lay, ex.data[2], ey.data[2])
    differ = [addr for addr in sorted(set(last_a) | set(last_b)) if not same(addr)]
    if not differ:
        return None
    bad = set(differ)
    k = next((i for i, (x, y) in enumerate(zip(a, b)) if x[:2] != y[:2]), min(len(a), len(b)))
    first = (a[k][2] if k < len(a) else None, b[k][2] if k < len(b) else None)
    if not ((first[0] is not None and any(x in bad for x in range(first[0].data[0], first[0].data[0] + first[0].data[1])))
            or (first[1] is not None and any(x in bad for x in range(first[1].data[0], first[1].data[0] + first[1].data[1])))):
        # the first store out of order is no byte that differs: the first store of one that does
        mine_first = next((ev for addr, _, ev in a if addr in bad), None)
        first = (mine_first, None if mine_first is not None else next((ev for addr, _, ev in b if addr in bad), None))
        if first[0] is not None:
            addr0 = first[0].data[0]
            first = (first[0], next((ev for addr, _, ev in reversed(b) if addr0 <= addr < addr0 + first[0].data[1]), None))
    order = {addr: i for i, (addr, _, _) in enumerate(a)}
    rows = []
    for addr in sorted(differ, key=lambda x: (order.get(x, len(a)), x)):
        x, y = last_a.get(addr), last_b.get(addr)
        rows.append((addr, x[0] if x else None, y[0] if y else None, x[1] if x else None, y[1] if y else None))
    return {"first": first, "bytes": rows}


class Compare:
    """The rebuilt run's listener: each of its events against the original's, in order (the module's
    docstring). hard: the first divergence in what dread test compares; soft: calls with other
    arguments before it; pairs: the shared events that agreed."""

    def __init__(self, ctx, original, stretches):
        self.ctx, self.lay = ctx, ctx.layout
        self.sync = [ev for ev in original.events if ev.kind != "store"]
        self.stretches = stretches              # stretch -> the original's stores in it
        self.i, self.stretch, self.cur = 0, 0, []
        self.pairs, self.soft, self.hard = [], [], None
        self.agreed_stretches = 0
        self.truncated, self.cut = original.truncated, False     # (the original's events end at MAX_EVENTS)

    def __call__(self, rec, ev):
        if self.hard is not None or self.cut:
            return
        if self.truncated and self.i >= len(self.sync):
            self.cut = True                 # (past what the original's run kept: compared no further)
            return
        if ev.kind == "store":
            self.cur.append(ev)
            if len(self.cur) > len(self.stretches.get(self.stretch, ())) + RUNAWAY:
                self.diverge(rec, Divergence("stores", None, ev, "the rebuilt image goes on storing: %d stores in this "
                                             "stretch, the original made %d" % (len(self.cur), len(self.stretches.get(
                                                 self.stretch, ())))))
            return
        want = self.sync[self.i] if self.i < len(self.sync) else None
        matches = want is not None and want.kind == ev.kind and (
            want.data[0] == ev.data[0] if ev.kind == "entry" else want.data == ev.data)
        if not matches:
            notes = self.partial_notes(want)
            if want is None:
                what = "the rebuilt image goes on to %s; the original makes no further call or log entry" % \
                    self.describe(ev)
                self.diverge(rec, Divergence("extra", None, ev, what, notes))
            elif want.kind == "entry" and ev.kind == "entry":
                self.diverge(rec, Divergence("call", want, ev, "the original calls %s, the rebuilt image %s" % (
                    self.name(want.data[0]), self.name(ev.data[0])), notes))
            else:
                self.diverge(rec, Divergence("call" if "entry" in (want.kind, ev.kind) else "log", want, ev,
                                             "the original %s, the rebuilt image %s" % (self.describe(want, True),
                                                                                         self.describe(ev, True)), notes))
            return
        if ev.kind == "entry":
            got = compare_stores(self.lay, self.stretches.get(self.stretch, ()), self.cur)
            if got is not None:
                self.diverge(rec, self.store_divergence(got))
                return
            self.agreed_stretches += 1
            diffs = argument_differences(self.lay, want, ev)
            if diffs:
                self.soft.append(Divergence("arguments", want, ev, "a call of %s with other arguments: %s" % (
                    self.name(ev.data[0]), ", ".join("%s 0x%x vs 0x%x" % d for d in diffs))))
            else:
                behind = self.pointed_differences(rec, want, ev)
                if behind:
                    self.soft.append(Divergence("arguments", want, ev, "a call of %s with other bytes behind %s" % (
                        self.name(ev.data[0]), "; ".join(behind))))
            self.stretch, self.cur = self.stretch + 1, []
        self.pairs.append((want, ev))
        self.i += 1

    def pointed_differences(self, rec, want, ev):
        """Texts for the arguments that point into a frame whose bytes there (the original's, read at
        its entry: Recorder.pointed) the rebuilt image's do not match, read now at its entry."""
        values = dict(ev.data[1])
        values.update(("stack argument %d" % i, v) for i, v in enumerate(ev.data[2]))
        out = []
        for where, label, data, defined in want.data[3]:
            v = values.get(where)
            if v is None or not self.lay.in_stack(v):
                continue
            try:
                got = rec.m.read(v, len(data))
            except EmuError:
                continue
            k = next((i for i in range(len(data)) if defined[i] and got[i] != data[i]), None)
            if k is not None:
                out.append("%s (%s, %d bytes): from +0x%x %s vs %s" % (where, label, len(data), k,
                                                                       data[k:k + 8].hex(" "), got[k:k + 8].hex(" ")))
        return out

    def name(self, entry):
        return self.ctx.names.get(entry, "0x%x" % entry)

    def describe(self, ev, verb=False):
        if ev is None:
            return "ends" if verb else "the end"
        if ev.kind == "entry":
            return ("calls %s" if verb else "a call of %s") % self.name(ev.data[0])
        return ("logs %s" if verb else "the log entry %s") % (show_event(ev.data),)

    def partial_notes(self, want):
        """The original's stores of the current stretch in front of want against the rebuilt
        image's so far, when they differ: a note (they may still come later in the C)."""
        mine = [ev for ev in self.stretches.get(self.stretch, ()) if want is None or ev.seq < want.seq]
        got = compare_stores(self.lay, mine, self.cur)
        if got is None:
            return []
        return ["before it, the stores of this stretch differ already: %s" % store_rows_text(got["bytes"], 4)]

    def store_divergence(self, got):
        first_o, first_r = got["first"]
        return Divergence("stores", first_o, first_r, "the stores of this stretch differ: %s" %
                          store_rows_text(got["bytes"], 6), stores=got)

    def diverge(self, rec, d):
        self.hard = d
        rec.halt()

    def finish(self, rec, ended):
        """The rebuilt run ended (ended: a Divergence of how it ended, or None when it returned or its
        case stopped it as the original's): what is left unmatched."""
        if self.hard is not None or self.cut or (self.truncated and self.i >= len(self.sync)):
            return
        if self.i < len(self.sync):
            want = self.sync[self.i]
            notes = self.partial_notes(want)
            if ended is not None:
                ended.original, ended.notes = want, notes + ended.notes
                ended.what += "; the original goes on to %s" % self.describe(want)
                self.hard = ended
            else:
                self.hard = Divergence("short", want, None, "the rebuilt image ends; the original goes on to %s" %
                                       self.describe(want), notes)
            return
        mine = self.stretches.get(self.stretch, ())
        got = compare_stores(self.lay, mine, self.cur)
        if got is not None:
            k = prefix_matched(self.lay, mine, self.cur) if ended is not None else None
            if k is not None and k < len(mine):     # (it stopped short of the original's stores: where it hung)
                ended.original = mine[k]
                ended.what += "; the original goes on to store at 0x%x" % mine[k].data[0]
                self.hard = ended
            else:
                self.hard = self.store_divergence(got)
            return
        self.agreed_stretches += 1
        if ended is not None:
            self.hard = ended


def prefix_matched(lay, mine, theirs, most=2000):
    """The number of the original's stores of a stretch whose bytes the rebuilt image's stores leave
    (compare_stores), when they are a beginning of the stretch, else None."""
    for k in range(min(len(mine), most) + 1):
        if compare_stores(lay, mine[:k], theirs) is None:
            return k
    return None


def show_event(entry):
    """A log entry as the runner prints it, numbers in hex."""
    return "(%s)" % ", ".join("0x%x" % x if isinstance(x, int) and not isinstance(x, bool) else repr(x) for x in entry)


def store_runs(rows):
    """The bytes that differ (compare_stores) as runs: [(first address, original bytes or None,
    rebuilt bytes or None, original event, rebuilt event)], a run each stretch of neighbouring bytes
    the same two stores left."""
    runs = []
    for addr, a, b, ea, eb in sorted(rows, key=lambda r: r[0]):
        last = runs[-1] if runs else None
        if last is not None and addr == last[0] + last[5] and last[3] is ea and last[4] is eb and \
                (a is None) == (last[1] is None) and (b is None) == (last[2] is None):
            last[1] = None if a is None else last[1] + bytes([a])
            last[2] = None if b is None else last[2] + bytes([b])
            last[5] += 1
        else:
            runs.append([addr, None if a is None else bytes([a]), None if b is None else bytes([b]), ea, eb, 1])
    order = {id(r): min((ev.seq for ev in (r[3],) if ev is not None), default=1 << 60) for r in runs}
    runs.sort(key=lambda r: (order[id(r)], r[0]))
    return [tuple(r[:5]) for r in runs]


def little(data):
    """Bytes as a little-endian number in hex (as many digits as bytes), or "-" for none."""
    return "-" if data is None else "0x%0*x" % (2 * len(data), int.from_bytes(data, "little"))


def store_rows_text(rows, limit):
    runs = store_runs(rows)
    parts = ["0x%x: %s vs %s" % (addr, little(a), little(b)) for addr, a, b, _, _ in runs[:limit]]
    return ", ".join(parts) + (", ..." if len(runs) > limit else "")


# ---- the images ----

def rebuilt_image(target, image, mode, name, recon=None, whole=False, lines=True, src=None):
    """(payload, its work directory, twin payload or None, a note on the twin, a text that says what
    was built, the lifter the build lifted with or None) for a mode ("test", "lifted", "readable"):
    the module's docstring."""
    from dread import build, lift, overlay, regflow
    from dread.program import Program
    program = Program(target, image)
    if program.by_name.get(name) is None:
        raise TraceError("%s is not in the function table" % name)
    flow = regflow.load(target, image, program)
    try:
        lifter = lift.make_lifter(program, flow)
    except lift.NoLifter as e:
        if mode != "test":
            raise TraceError(str(e))
        lifter = None
    twin, note = None, None
    if mode == "test":
        path = recon or os.path.join(build.recon_dir(target, image), "payload.bin")
        if not os.path.exists(path):
            raise TraceError("no rebuilt image at %s: run `dread recon` first, or trace with --lifted or --readable"
                             % path)
        if not recon and os.path.isdir(image.src):
            built = os.path.getmtime(path)
            newer = [os.path.join(d, f) for d, _, fs in os.walk(image.src) for f in fs
                     if f.endswith(build.SOURCES + build.HEADERS) and os.path.getmtime(os.path.join(d, f)) > built]
            if newer:
                raise TraceError("%s is older than %s: run `dread recon`" % (path, ", ".join(sorted(newer))))
        work = os.path.dirname(os.path.abspath(path))
        if lifter is not None:              # (as dread.build's lift_sources hands it what the C of src calls)
            import glob
            objects = glob.glob(os.path.join(work, "obj", "**", "*.o"), recursive=True)   # (src's objects)
            lifter.src_calls = frozenset(build.called_functions(objects))
        if lines:
            twin_dir = os.path.join(target.build, "trace", image.name, "test-g")
            try:
                twin = with_debug(target, lambda: build.build(target, image, src=src, out=twin_dir, quiet=True,
                                                              lifter=lifter))
            except (overlay.OverlayError, build.BuildFailed, tgt.TargetError) as e:
                note = "no C lines: the build with -g failed (%s)" % str(e).splitlines()[0]
        return path, work, twin, note, "the image dread test runs", lifter
    try:
        build.lifted_result(lifter, program.by_name[name])
    except lift.Refused as e:
        raise TraceError("%s does not lift: %s" % (name, e))
    system = build.system_names(image.table("lifted"))
    names = [n for n in build.read_names(image.table("lifted")) if n not in system]
    if name not in names:
        names.append(name)
    work = os.path.join(target.build, "trace", image.name, mode)

    def transform():
        if mode != "readable":
            return None
        from dread import readable
        context = readable.Context(target, image, program, flow)
        if whole:
            context.plan.whole.add(name)
        return readable.chooser(set(build.read_names(image.table("readable"))) | {name}, context)
    what = ("the lifted table's functions and %s from lifted C" % name if mode == "lifted" else
            "the lifted table's functions from lifted C, the readable table's and %s from readable C%s"
            % (name, " (its frame one object)" if whole else ""))
    try:
        path = build.build(target, image, src=src, out=work, lifted=names, quiet=True, lifter=lifter,
                           transform=transform())
    except (overlay.OverlayError, build.BuildFailed) as e:
        raise TraceError("the build failed: %s" % e)
    if lines:
        try:
            twin = with_debug(target, lambda: build.build(target, image, src=src, out=work + "-g", lifted=names,
                                                          quiet=True, lifter=lifter, transform=transform()))
        except (overlay.OverlayError, build.BuildFailed) as e:
            note = "no C lines: the build with -g failed (%s)" % str(e).splitlines()[0]
    return path, work, twin, note, what, lifter


def with_debug(target, make):
    """make() with -g among the target's C flags (the twin build)."""
    flags = target.compiler.cflags
    target.compiler.cflags = list(flags) + ["-g"]
    try:
        return make()
    finally:
        target.compiler.cflags = flags


class Placement:
    """The C of a rebuilt image (its placement.json): which function's C an address lies in, the
    moved functions (aliases: original entry -> the C's address; hops: the trampoline a narrow branch
    goes through), and how each function is built (lifted, readable, src, or the original's code)."""

    def __init__(self, work, program):
        import json
        path = os.path.join(work, "placement.json")
        data = {}
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f).get("functions", {})
        self.functions = data
        self.ranges = sorted((v["address"], v["address"] + v.get("c_bytes", 0), n) for n, v in data.items()
                             if v.get("c_bytes"))
        self.starts = [r[0] for r in self.ranges]
        self.aliases = {v["entry"]: v["address"] for v in data.values() if v.get("branch")}
        self.hops = {v["entry"]: (v["via"],) for v in data.values() if v.get("branch") and v.get("via")}
        self.work, self.program = work, program
        self.lifted_dir = os.path.join(work, "lifted")
        self._kind = {}

    def c_function(self, address):
        """(name, offset) of the C function an address of the rebuilt image lies in, or None."""
        i = bisect.bisect_right(self.starts, address) - 1
        if i >= 0 and self.ranges[i][0] <= address < self.ranges[i][1]:
            return self.ranges[i][2], address - self.ranges[i][0]
        return None

    def kind(self, name, lifter=None):
        """"lifted C", "readable C", "C" (the target's src) or None (the original's code)."""
        if name not in self._kind:
            v = self.functions.get(name)
            if v is None or v.get("placement") == "helper":
                self._kind[name] = None
            else:
                path = os.path.join(self.lifted_dir, name + ".c")
                if not os.path.exists(path):
                    self._kind[name] = "C"
                else:
                    self._kind[name] = "lifted C"
                    fn = self.program.by_name.get(name)
                    if lifter is not None and fn is not None:
                        try:
                            from dread import build
                            text = build.lifted_result(lifter, fn).text
                            with open(path) as f:
                                built = "".join(l for l in f if not l.startswith("/* dread: "))
                            if built != text:
                                self._kind[name] = "readable C"
                        except Exception:
                            pass
        return self._kind[name]


class Lines:
    """The debug information of an ELF (the twin build's), read with pyelftools as it is needed: the
    line of an address (at), the variables of a function in memory (variables). A compilation unit is
    found through .debug_aranges where the compiler wrote it, else its line program is read in turn;
    each is read once."""

    def __init__(self, elf_path):
        import io
        from elftools.elf.elffile import ELFFile
        with open(elf_path, "rb") as f:
            self.elf = ELFFile(io.BytesIO(f.read()))
        if not self.elf.has_dwarf_info():
            raise ValueError("no debug information")
        self.dwarf = self.elf.get_dwarf_info()
        self.cus = list(self.dwarf.iter_CUs())
        try:
            self.aranges = self.dwarf.get_aranges()
        except Exception:
            self.aranges = None
        self._rows, self._vars = {}, {}

    def rows(self, cu):
        """(starts, rows) of a compilation unit's line program: rows [(address, path or None at the end
        of a sequence, line)] in address order; none where pyelftools cannot read it (MSP430's)."""
        if cu.cu_offset not in self._rows:
            try:
                self._rows[cu.cu_offset] = self._read_rows(cu)
            except Exception:
                self._rows[cu.cu_offset] = ([], [])
        return self._rows[cu.cu_offset]

    def _read_rows(self, cu):
        rows = []
        top = cu.get_top_DIE()
        comp = top.attributes.get("DW_AT_comp_dir")
        comp = comp.value.decode(errors="replace") if comp is not None else ""
        try:
            program = self.dwarf.line_program_for_CU(cu)
        except Exception:           # (pyelftools cannot read every line table)
            program = None
        if program is not None:
            files, dirs = program["file_entry"], program["include_directory"]
            version = program.header["version"]

            def path_of(index):
                if version >= 5:
                    if index >= len(files):
                        return None
                    entry = files[index]
                    d = dirs[entry.dir_index].decode(errors="replace") if entry.dir_index < len(dirs) else comp
                else:
                    if not 0 < index <= len(files):
                        return None
                    entry = files[index - 1]
                    d = dirs[entry.dir_index - 1].decode(errors="replace") if entry.dir_index else comp
                name = entry.name.decode(errors="replace")
                return name if os.path.isabs(name) else os.path.join(d, name)
            paths = {}
            for entry in program.get_entries():
                s = entry.state
                if s is None:
                    continue
                if s.end_sequence:
                    rows.append((dwarf_code_value(self.elf, s.address), None, 0))
                else:
                    if s.file not in paths:
                        paths[s.file] = path_of(s.file)
                    rows.append((dwarf_code_value(self.elf, s.address), paths[s.file], s.line))
        rows.sort(key=lambda r: (r[0], r[1] is not None))
        return [r[0] for r in rows], rows

    def cu_at(self, address):
        """The compilation unit whose code holds an address, or None."""
        if self.aranges is not None:
            try:
                off = self.aranges.cu_offset_at_addr(address)
                if off is not None:
                    return self.dwarf.get_CU_at(off)
            except Exception:
                pass
        for cu in self.cus:
            starts, rows = self.rows(cu)
            i = bisect.bisect_right(starts, address) - 1
            if i >= 0 and rows[i][1] is not None:
                return cu
        return None

    def at(self, address):
        """(path, line) of an address, or None (also where the debug information cannot be read)."""
        try:
            return self._at(address)
        except Exception:
            return None

    def _at(self, address):
        address = dwarf_code_value(self.elf, address)
        cu = self.cu_at(address)
        if cu is None:
            return None
        starts, rows = self.rows(cu)
        i = bisect.bisect_right(starts, address) - 1
        if i < 0 or rows[i][1] is None:
            return None
        return rows[i][1], rows[i][2]

    def variables(self, name, address):
        """(frame base, [(variable, size, location)]) of the function name whose code is at address:
        frame base "cfa" or ("reg", number); location ("fb", offset) or ("reg", number, offset); a
        variable of another form (in a register, in a location list) left out. None when it is not
        found."""
        if name not in self._vars:
            try:
                self._vars[name] = self._variables(name, address)
            except Exception:
                self._vars[name] = None
        return self._vars[name]

    def _variables(self, name, address):
        from elftools.dwarf.dwarf_expr import DWARFExprParser
        parser = DWARFExprParser(self.dwarf.structs)

        def expr(attr):
            if attr is None or attr.form not in ("DW_FORM_exprloc", "DW_FORM_block1", "DW_FORM_block"):
                return None
            try:
                return parser.parse_expr(attr.value)
            except Exception:
                return None

        def size_of(die, depth=0):
            if die is None or depth > 16:
                return None
            if "DW_AT_byte_size" in die.attributes and die.tag != "DW_TAG_array_type":
                return die.attributes["DW_AT_byte_size"].value
            if die.tag == "DW_TAG_array_type":
                elem = size_of(die.get_DIE_from_attribute("DW_AT_type"), depth + 1) \
                    if "DW_AT_type" in die.attributes else None
                count = 1
                for sub in die.iter_children():
                    if sub.tag == "DW_TAG_subrange_type":
                        if "DW_AT_count" in sub.attributes:
                            count *= sub.attributes["DW_AT_count"].value
                        elif "DW_AT_upper_bound" in sub.attributes:
                            count *= sub.attributes["DW_AT_upper_bound"].value + 1
                        else:
                            return None
                return None if elem is None else elem * count
            if "DW_AT_type" in die.attributes:
                return size_of(die.get_DIE_from_attribute("DW_AT_type"), depth + 1)
            return None

        def collect(die, out):
            for child in die.iter_children():
                if child.tag == "DW_TAG_variable" and "DW_AT_name" in child.attributes:
                    ops = expr(child.attributes.get("DW_AT_location"))
                    loc = None
                    if ops and len(ops) == 1 and ops[0].op_name == "DW_OP_fbreg":
                        loc = ("fb", ops[0].args[0])
                    elif ops and len(ops) == 1 and ops[0].op_name.startswith("DW_OP_breg") and \
                            ops[0].op_name[len("DW_OP_breg"):].isdigit():
                        loc = ("reg", int(ops[0].op_name[len("DW_OP_breg"):]), ops[0].args[0])
                    if loc is not None:
                        try:
                            size = size_of(child.get_DIE_from_attribute("DW_AT_type")) \
                                if "DW_AT_type" in child.attributes else None
                        except Exception:
                            size = None
                        out.append((child.attributes["DW_AT_name"].value.decode(errors="replace"), size, loc))
                elif child.tag == "DW_TAG_lexical_block":
                    collect(child, out)
        got = None
        cu = self.cu_at(address)
        for die in (cu.get_top_DIE().iter_children() if cu is not None else ()):
            if die.tag != "DW_TAG_subprogram" or "DW_AT_low_pc" not in die.attributes or \
                    die.attributes.get("DW_AT_name") is None or \
                    die.attributes["DW_AT_name"].value.decode(errors="replace") != name:
                continue
            ops = expr(die.attributes.get("DW_AT_frame_base"))
            base = None
            if ops and len(ops) == 1 and ops[0].op_name == "DW_OP_call_frame_cfa":
                base = "cfa"
            elif ops and len(ops) == 1 and ops[0].op_name.startswith("DW_OP_reg") and \
                    ops[0].op_name[len("DW_OP_reg"):].isdigit():
                base = ("reg", int(ops[0].op_name[len("DW_OP_reg"):]))
            out = []
            collect(die, out)
            got = (base, out)
            break
        return got


# ---- the C behind an address ----

NOTE = "/* %x: "


def file_line(path, number):
    try:
        with open(path) as f:
            for i, line in enumerate(f, 1):
                if i == number:
                    return line.rstrip()
    except OSError:
        pass
    return None


class Sources:
    """The C lines of the rebuilt image: the twin's line table for a rebuilt address, and the
    statement of a function's lifted or readable C for an original address."""

    def __init__(self, placement, lines, lifter, target, image, program):
        self.placement, self.lines, self.lifter = placement, lines, lifter
        self.target, self.image, self.program = target, image, program
        self._context = None

    def rel(self, path):
        """A C file's path as the report shows it: relative to the build's directory or the target's."""
        path = os.path.normpath(os.path.abspath(path))
        for root in (self.placement.work, self.target.root):
            r = os.path.relpath(path, root)
            if not r.startswith(".."):
                return r
        return path

    def rebuilt_line(self, address):
        """(path, line number, text) of the C line a rebuilt address came from, or None."""
        if self.lines is None or self.placement.c_function(address) is None:
            return None
        got = self.lines.at(address)
        if got is None:
            return None
        path, number = got
        # the twin's files are copies of the traced build's: show the traced build's
        twin_lifted = self.placement.lifted_dir + "-g"
        if path.startswith(self.placement.work + "-g" + os.sep):
            path = self.placement.work + path[len(self.placement.work + "-g"):]
        elif path.startswith(twin_lifted):
            path = self.placement.lifted_dir + path[len(twin_lifted):]
        return path, number, file_line(path, number)

    def statement(self, name, address):
        """(path, line number, text, how) of the statement of name's lifted or readable C that
        carries the original's instruction at address, or None (its C is not the lifters', or no
        statement carries it)."""
        kind = self.placement.kind(name, self.lifter)
        if kind not in ("lifted C", "readable C"):
            return None
        path = os.path.join(self.placement.lifted_dir, name + ".c")
        found = self._find(path, address)
        if found:
            return found + ("its comment names the instruction",)
        if kind != "readable C":
            return None
        tree = self._tree(name)
        if tree is None:
            return None
        from dread import cparse
        hit = []

        def visit(s):
            a = getattr(s, "addr", None)
            if a is not None and address in cparse.absorbed(a):
                hit.append(cparse.shown(a)[0])
            for inner in cparse._nested(s):
                visit(inner)
        visit(tree.body)
        for shown in hit:
            found = self._find(path, shown)
            if found:
                return found + ("the statement that absorbed the instruction",)
        return None

    @staticmethod
    def _find(path, address):
        note = NOTE % address
        try:
            with open(path) as f:
                for i, line in enumerate(f, 1):
                    if note in line:
                        return path, i, line.rstrip()
        except OSError:
            pass
        return None

    def _tree(self, name):
        from dread import build, readable
        fn = self.program.by_name.get(name)
        if fn is None or self.lifter is None:
            return None
        try:
            if self._context is None:
                self._context = readable.Context(self.target, self.image, self.program)
            made = readable.readable_tree(name, build.lifted_result(self.lifter, fn).text, None, self._context)
        except Exception:
            return None
        return made[1] if made else None


# ---- tracing ----

def find_case(run, name, label):
    """(label, address, setup, args, limit, generated case or None) of the case labelled so: the
    list dread.runner run_function makes."""
    from dread import runner
    suite, prog = run["suite"], run["program"]
    fn = prog.by_name.get(name)
    if fn is None:
        raise TraceError("%s is not in the function table" % name)
    if run.get("bodies", {}).get(name):
        from dread.lift import Body
        fn = Body(fn, run["bodies"][name])
    cases = [c + (None,) for c in suite.cases.get(name, [])]
    if name in suite.auto:
        try:
            cases += runner.generated_cases(name, fn)
        except Exception as e:
            if not cases:
                raise TraceError("the case generation failed: %s" % e)
    want = label.strip()
    if want.startswith("[") and want.endswith("]"):
        want = want[1:-1]
    for c in cases:
        if c[0] == want:
            return c
    raise TraceError("%s has no case labelled %r (%d cases: %s)" % (name, want, len(cases),
                                                                     labels_text([c[0] for c in cases]) or "none"))


def labels_text(labels):
    """Case labels in short: the numbered kinds as ranges (generated 0 to 37), the first written ones."""
    numbered, own = {}, []
    for label in labels:
        m = re.match(r"^(generated|kept|written) (\d+)$", label)
        if m:
            numbered.setdefault(m.group(1), []).append(int(m.group(2)))
        else:
            own.append(label)
    parts = ["%s %d to %d" % (k, min(v), max(v)) if len(v) > 1 else "%s %d" % (k, v[0]) for k, v in numbered.items()]
    if own:
        parts.append("%s%s" % (", ".join(repr(x) for x in own[:4]), ", and %d more written" % (len(own) - 4)
                                if len(own) > 4 else ""))
    return "; ".join(parts)


class Tracer:
    """One case of one function in both images (the module's docstring)."""

    def __init__(self, name, case, placement, sources, lifter=None):
        from dread import runner
        self.run = runner.RUN
        self.name, self.case = name, case
        self.placement, self.sources = placement, sources
        self.ctx = Context(self.run, lifter, self.objects)
        self._objects = {}
        self.kept, self.why, _ = runner.kept_for(name)
        self.budget = runner.REBUILT_BUDGET

    def objects(self, name):
        """The frame objects of a function's C for Context.pointed_span: [(label, depth below sp0,
        size)] for lifted or readable C (lifted C's frame array one object), [] for C written by hand
        (its frame is its own), None for the original's code."""
        if name not in self._objects:
            kind = self.placement.kind(name, self.sources.lifter)
            if kind is None:
                got = None
            elif kind not in ("lifted C", "readable C"):
                got = []
            else:
                objs = frame_objects(os.path.join(self.placement.lifted_dir, name + ".c"))
                words = [d for label, d, _ in objs if label.startswith("frame[")]
                got = [(label, d, size) for label, d, size in objs
                       if not label.startswith("frame[") and size is not None and re.match(r"stack\d", label)]
                if words:
                    got.append(("frame", max(words), max(words) - min(words) + 4))
            self._objects[name] = got
        return self._objects[name]

    def machine(self, which, plan):
        run = self.run
        label, addr, setup, args, limit, generated = self.case
        m = Machine(run["layout"], run["original"] if which == "original" else run["recon"],
                    run["aliases"] if which == "rebuilt" else None, isa_of=run["isa_of"])
        m.plan, m.replay = plan, which == "rebuilt"
        m.function = self.name
        m.kept, m.kept_why = self.kept, self.why
        return m

    def limit(self, m, which):
        label, addr, setup, args, limit, generated = self.case
        loop_case = bool(generated and generated.get("hang") and (generated.get("stub_calls") or generated.get("stops")))
        return limit if which == "original" or (getattr(m, "may_hang", False) and not loop_case) else limit * self.budget

    def one(self, which, plan, listener=None, stop_at=None, record=True):
        """(recorder or None, the run's Result or EmuError, the Machine) of one run."""
        label, addr, setup, args, limit, generated = self.case
        m = self.machine(which, plan)
        try:
            setup(m)
        except Exception as e:
            raise TraceError("the case's setup failed: %s: %s" % (type(e).__name__, e))
        if (code_address(self.ctx.arch, addr)) in m.stubs:
            raise TraceError("the case stubs the function it is meant to test")
        rec = None
        if record:
            rec = Recorder(self.ctx, m, self.placement.aliases if which == "rebuilt" else None,
                           self.placement.hops if which == "rebuilt" else None, listener, stop_at,
                           capture=which == "original", max_events=MAX_EVENTS if which == "original" else None)
        try:
            out = m.call(addr, *args, max_instructions=self.limit(m, which))
        except Exception as e:
            out = e if isinstance(e, EmuError) else EmuError("%s: %s" % (type(e).__name__, e))
        return rec, out, m

    def trace(self):
        """A Result of the trace (TraceResult)."""
        plan = []
        orig, out_o, m_o = self.one("original", plan)
        end_o = Snapshot(m_o, orig.top, orig.stored_at, orig.stack_base)
        end_o.pc = m_o.reg("pc")
        replayed = list(plan)
        stretches = {}
        for ev in orig.events:
            if ev.kind == "store":
                stretches.setdefault(ev.stretch, []).append(ev)
        cmp = Compare(self.ctx, orig, stretches)
        reb, out_r, m_r = self.one("rebuilt", list(replayed), listener=cmp)
        end_r = Snapshot(m_r, reb.top)
        end_r.pc = m_r.reg("pc")
        stopped = cmp.hard is not None
        if not stopped:
            cmp.finish(reb, self.ending(out_o, out_r, m_r))
        hard = cmp.hard
        if hard is None or hard.kind == "result":
            final = out_r
        else:                               # (the rebuilt run stopped there: the whole case as dread test runs it)
            _, final, _ = self.one("rebuilt", list(replayed), record=False)
        verdict = self.verdict(out_o, final)
        if hard is None and verdict:
            hard = Divergence("result", None, None, "at the end: %s" % verdict[0])
            reg = register_of(verdict[0], self.run["layout"])
            if reg is not None:             # (the instruction that set it last, in both runs)
                hard.writers = (self.last_writer("original", [], reg), self.last_writer("rebuilt", list(replayed), reg))
        if hard is not None and ran_out(out_o) and ran_out(out_r):
            hard.notes.append("both runs ran into their limit (the case may hang): what each did by then depends "
                              "on how fast its code is (the original ran %d blocks, the rebuilt image %d)"
                              % (orig.blocks, reb.blocks))
        result = TraceResult(self, orig, reb, cmp, hard, verdict, out_o, out_r, end_o, end_r)
        first = cmp.soft[0] if cmp.soft else hard
        if first is not None:
            result.snapshots = self.snapshots(first, replayed, end_o, end_r)
            if hard is not None and hard is not first:
                result.hard_snapshots = self.snapshots(hard, replayed, end_o, end_r)
        return result

    def last_writer(self, which, plan, reg):
        """The address of the last instruction of a run that changed register reg (a code hook on every
        instruction: one run more), or None."""
        from unicorn import UC_HOOK_CODE
        label, addr, setup, args, limit, generated = self.case
        m = self.machine(which, plan)
        try:
            setup(m)
        except Exception:
            return None
        rid, read = m.arch.reg_id(reg), m.core.read32
        state = [None, None, None]          # the value seen last, the instruction before, the writer

        def on_code(uc, address, size, user):
            v = read(rid)
            if state[0] is not None and v != state[0]:
                state[2] = state[1]
            state[0], state[1] = v, address
        m.hook_add(UC_HOOK_CODE, on_code)
        try:
            m.call(addr, *args, max_instructions=self.limit(m, which))
        except Exception:
            pass
        if state[0] is not None and read(rid) != state[0]:
            state[2] = state[1]
        return state[2]

    def snapshots(self, d, replayed, end_o, end_r):
        """(original, rebuilt) Snapshots at a divergence: both runs again, stopped at its events (at
        their ends where it has none)."""
        out = []
        for which, ev, end, plan in (("original", d.original, end_o, []), ("rebuilt", d.rebuilt, end_r, list(replayed))):
            if ev is None or d.kind == "result":
                out.append(end)
                continue
            try:
                rec, _, _ = self.one(which, plan, stop_at=ev.seq)
                out.append(rec.snapshot or end)
            except TraceError:
                out.append(end)
        return tuple(out)

    def ending(self, out_o, out_r, m_r):
        """A Divergence for how the rebuilt run ended where the original's did not end so (its limit,
        a fault), or None."""
        if ran_out(out_r) and not ran_out(out_o):
            return Divergence("limit", None, None, "the rebuilt image runs into its limit (%d instructions)"
                              % self.limit(m_r, "rebuilt"))
        if isinstance(out_r, EmuError) and not isinstance(out_o, EmuError):
            return Divergence("fault", None, None, "the rebuilt image faults: %s" % out_r)
        return None

    @staticmethod
    def verdict(out_o, out_r):
        """What dread test reports for the case: its difference lines (dread.runner run_function's),
        [] when it agrees."""
        if isinstance(out_o, Exception) or isinstance(out_r, Exception):
            return ["original: %s" % out_o if isinstance(out_o, Exception) else "original ran",
                    "rebuilt: %s" % out_r if isinstance(out_r, Exception) else "rebuilt ran"]
        return out_o.diff(out_r)


class TraceResult:
    """What a trace found (first: where the runs part first; hard: the first difference dread test
    compares; verdict: what dread test reports for the whole case), and its report (text())."""

    def __init__(self, tracer, orig, reb, cmp, hard, verdict, out_o, out_r, end_o, end_r):
        self.tracer, self.orig, self.reb, self.cmp = tracer, orig, reb, cmp
        self.hard, self.verdict, self.out_o, self.out_r = hard, verdict, out_o, out_r
        self.end_o, self.end_r = end_o, end_r
        self.soft = list(cmp.soft)
        self.first = self.soft[0] if self.soft else hard
        self.snapshots = self.hard_snapshots = None

    @property
    def agrees(self):
        return self.first is None and not self.verdict

    # -- words --

    def fname(self, entry):
        return self.tracer.ctx.names.get(entry, "0x%x" % entry)

    def where(self, which, pc):
        """The function an image's address lies in, with the offset and, in the rebuilt image, how it
        is built."""
        prog, placement = self.tracer.ctx.program, self.tracer.placement
        if which == "rebuilt":
            got = placement.c_function(pc)
            if got is not None:
                name, off = got
                kind = placement.kind(name, self.tracer.sources.lifter) or "a helper of the C"
                return "%s%s (%s)" % (name, "+0x%x" % off if off else "", kind)
        fn = prog.function_at(pc)
        if fn is None or (which == "rebuilt" and fn.name in placement.functions):
            return "0x%x" % pc if fn is None else "inside %s's old place" % fn.name
        return ("%s+0x%x" % (fn.name, pc - fn.entry) if pc != fn.entry else fn.name) + \
            (" (the original's code)" if which == "rebuilt" else "")

    def site(self, which, ev):
        """The instruction an event happened at: for a call, and for a stub's log entry, the call or
        jump that entered the callee (the last instruction of the block in front of its entry), else
        the event's own."""
        stub = ev.kind == "log" and ev.data and ev.data[0] not in IO_EVENTS and ev.data[0] != "svc"
        if (ev.kind == "entry" or stub) and ev.frame is not None and ev.frame.came is not None:
            return last_instruction(self.tracer, ev.frame.came, which)
        return ev.pc

    def instruction_data(self, which, pc):
        """One image's instruction at pc as data: {"which", "address", "instruction" (its disassembly),
        "where" (where()'s words), "function" (the function it lies in, by the original's name; the C
        function for the rebuilt image's C, null outside every function)}, the rebuilt image's with
        "built" (how its function is built there: placement kind, null for the original's code) and
        "line" ({"path", "line", "text"}: the C line it came from, text null where the file is gone),
        the original's with "statement" ({"path", "line", "text", "how"}: its statement in its
        function's lifted or readable C); either null where there is none."""
        prog, placement, sources = self.tracer.ctx.program, self.tracer.placement, self.tracer.sources
        out = {"which": which, "address": pc, "instruction": disassemble(self.tracer, which, pc),
               "where": self.where(which, pc)}
        fn = prog.function_at(pc)
        if which == "original":
            out["function"] = fn.name if fn is not None else None
            got = sources.statement(fn.name, pc) if fn is not None else None
            out["statement"] = None if got is None else {"path": sources.rel(got[0]), "line": got[1],
                                                         "text": got[2].strip(), "how": got[3]}
            return out
        c = placement.c_function(pc)
        if c is not None:
            out["function"] = c[0]
            out["built"] = placement.kind(c[0], sources.lifter) or "a helper of the C"
        else:
            out["function"] = fn.name if fn is not None else None
            out["built"] = None
        got = sources.rebuilt_line(pc)
        out["line"] = None if got is None else {"path": sources.rel(got[0]), "line": got[1],
                                                "text": got[2].strip() if got[2] is not None else None}
        return out

    def instruction_lines(self, which, pc):
        """The lines that show one image's instruction at pc: the original's with the statement of its
        function's C, the rebuilt image's with the C line it came from (instruction_data)."""
        return instruction_text(self.instruction_data(which, pc))

    def stack_data(self, which, frame):
        """A run's call stack as data, outermost first: [{"function", "built" (the rebuilt image's: how
        it is built there, null for the original's code), "helper" (no shared point), "from" (the
        instruction that called it, null for the outermost)}]; [] without a frame."""
        if frame is None:
            return []
        out = []
        for f in frame.chain():
            name = self.fname(f.entry)
            item = {"function": name, "helper": bool(f.helper), "from": None}
            if which == "rebuilt":
                item["built"] = self.tracer.placement.kind(name, self.tracer.sources.lifter)
            if f.parent is not None and f.came is not None:
                item["from"] = last_instruction(self.tracer, f.came, which)
            out.append(item)
        return out

    def stack_text(self, which, frame):
        """A run's call stack, outermost first, each callee with the instruction that called it."""
        return stack_line(self.stack_data(which, frame))

    def event_text(self, ev):
        if ev.kind == "entry":
            e, args, stack = ev.data[:3]
            shown = ["%s=0x%x" % (r, v) for r, v in args] + ["[sp+%d]=0x%x" % (i * self.tracer.ctx.word, v)
                                                               for i, v in enumerate(stack)]
            return "call %s(%s)" % (self.fname(e), ", ".join(shown))
        if ev.kind == "log":
            return "log %s" % show_event(ev.data)
        addr, size, value = ev.data
        return "store 0x%x, %d bytes: 0x%x" % (addr, size, value)

    def stretch_text(self, ev):
        """What starts the stretch of stores an event falls into."""
        rec = self.orig if ev in self.orig.events[ev.seq:ev.seq + 1] else self.reb
        start = rec.starts.get(ev.stretch)
        return "the entry of %s" % self.tracer.name if start is None or start.frame is None or \
            start.frame.parent is None else "the call of %s" % self.fname(start.data[0])

    # -- the report --

    def data(self, last=LAST):
        """The report as data (dread trace --json; docs/formats.md): {"agrees", "truncated" (the original's
        run made more than MAX_EVENTS shared events), "agreed" ({"calls", "log", "stretches"} where it
        agrees), "first" (where the runs part first: divergence_data), "then" (the calls with other
        arguments between it and the hard one: [{"what", "original"}]), "hard" (the first difference
        dread test compares where that is another one; null where it is the first, "same", or where
        nothing dread test compares differs), "same", "verdict" (what dread test reports for the case)}.
        text() is made from it."""
        pairs = self.cmp.pairs
        calls = sum(1 for a, _ in pairs if a.kind == "entry")
        out = {"agrees": self.agrees, "truncated": bool(self.cmp.truncated), "agreed": None, "first": None,
               "then": [], "hard": None, "same": False, "verdict": list(self.verdict or [])}
        if self.agrees:
            roots = sum(1 for a, _ in pairs if a.kind == "entry" and a.frame is not None and a.frame.parent is None)
            out["agreed"] = {"calls": calls - roots, "log": len(pairs) - calls, "stretches": self.cmp.agreed_stretches}
            return out
        out["first"] = self.divergence_data(self.first, "first divergence", last, self.snapshots)
        if self.hard is self.first:
            out["same"] = True
        elif self.hard is not None:
            for d in self.soft[1:SOFT_SHOWN + 1]:
                if d.original is not None and (self.hard.original is None or d.original.seq < self.hard.original.seq):
                    out["then"].append({"what": d.what, "original": self.site("original", d.original)})
            out["hard"] = self.divergence_data(self.hard, "the first difference dread test compares", last,
                                               self.hard_snapshots)
        return out

    def text(self, last=LAST):
        return report_text(self.data(last))

    def divergence_data(self, d, title, last, snapshots):
        """One divergence as data: {"title", "kind" (Divergence's), "what", "soft", "stretch" (what starts
        the stretch of stores it falls into, for a difference in stores), "original" and "rebuilt" (the
        instruction in each image: instruction_data; null where that run has no event there),
        "original_none" and "rebuilt_none" (the words for a run that makes no further store in the
        stretch), "recent" (the blocks a rebuilt run that ran into its limit ran last), "writers" (where
        each run set a register that differs at the end last: instruction_data), "leaves" (where one
        run's path leaves the other's: [{"which", "function", "result", "instruction"}]), "stores" (what
        differs at the stretch's end: [{"address", "original", "original_site", "rebuilt",
        "rebuilt_site"}], the bytes as little-endian hex, at most six; "stores_total" runs in all),
        "notes", "stacks" ({"original", "rebuilt"}: stack_data), "events" (the last shared events before
        it: [{"index", "text", "original", "rebuilt"}]; "events_total", "events_shown"), "frame"
        (frame_data, or null), "verdict" (on the hard one: what dread test reports, else null)}."""
        ev_o, ev_r = d.original, d.rebuilt
        out = {"title": title, "kind": d.kind, "what": d.what, "soft": bool(d.soft), "stretch": None,
               "original": None, "rebuilt": None, "original_none": None, "rebuilt_none": None, "recent": None,
               "writers": [], "leaves": [], "stores": None, "stores_total": 0, "notes": list(d.notes),
               "stacks": None, "events": [], "events_total": 0, "events_shown": 0, "frame": None, "verdict": None}
        if d.stores is not None and (ev_o or ev_r) is not None:
            out["stretch"] = self.stretch_text(ev_o or ev_r)
        ended = d.kind in ("result", "limit", "fault", "short")
        if ev_o is not None:
            out["original"] = self.instruction_data("original", self.site("original", ev_o))
        elif ended:
            out["original"] = self.instruction_data("original", self.last_pc(self.orig, "original"))
        elif d.stores is not None:          # (the rebuilt image stores on where the original made no store)
            out["original_none"] = "makes no further store in this stretch%s" % self.stretch_last(self.orig, ev_r)
        if ev_r is not None:
            out["rebuilt"] = self.instruction_data("rebuilt", self.site("rebuilt", ev_r))
        elif d.stores is not None and not ended:
            out["rebuilt_none"] = "makes no further store in this stretch%s" % self.stretch_last(self.reb, ev_o)
        elif ended:
            pc = self.end_r.pc if d.kind in ("limit", "fault") else self.last_pc(self.reb, "rebuilt")
            out["rebuilt"] = self.instruction_data("rebuilt", pc)
            if d.kind == "limit":
                out["recent"] = sorted(set(self.reb.recent))
        if d.writers is not None:
            for which, pc in zip(("original", "rebuilt"), d.writers):
                if pc is not None:
                    out["writers"].append(self.instruction_data(which, pc))
        if d.kind in ("call", "log", "extra", "short", "limit", "fault"):
            for which, frame, block, value in self.leaves(d):
                out["leaves"].append({"which": which, "function": self.fname(frame.entry), "result": value,
                                      "instruction": self.instruction_data(
                                          which, last_instruction(self.tracer, block, which))})
        if d.stores is not None:
            runs = store_runs(d.stores["bytes"])
            out["stores"] = [{"address": addr, "original": None if a is None else little(a),
                              "original_site": None if a is None else self.site("original", ea),
                              "rebuilt": None if b is None else little(b),
                              "rebuilt_site": None if b is None else self.site("rebuilt", eb)}
                             for addr, a, b, ea, eb in runs[:6]]
            out["stores_total"] = len(runs)
        frames = [s.frames[-1] if s.frames else None for s in snapshots] if snapshots else [None, None]
        out["stacks"] = {"original": self.stack_data("original", ev_o.frame if ev_o else frames[0]),
                         "rebuilt": self.stack_data("rebuilt", ev_r.frame if ev_r else frames[1])}
        before = self.before(d)
        if before:
            shown = min(last, len(before))
            out["events_total"], out["events_shown"] = len(before), shown
            out["events"] = [{"index": k, "text": self.event_text(a), "original": self.site("original", a),
                              "rebuilt": self.site("rebuilt", b)}
                             for k, (a, b) in enumerate(before[-last:], len(before) - shown + 1)]
        if snapshots is not None:
            out["frame"] = frame_data(self, d, snapshots)
        if self.hard is d:
            out["verdict"] = list(self.verdict or [])
        return out

    def divergence_text(self, d, title, last, snapshots):
        return divergence_lines(self.divergence_data(d, title, last, snapshots))

    def stretch_last(self, rec, other):
        """The last store a run made in the stretch of another run's event, as words for the report."""
        mine = [e for e in rec.events if e.kind == "store" and e.stretch == other.stretch] if other is not None else []
        return "; its last there: 0x%x (%s)" % (self.site(rec is self.reb and "rebuilt" or "original", mine[-1]),
                                                self.event_text(mine[-1])) if mine else ""

    def before(self, d):
        """The shared events before a divergence."""
        pairs, ev_o, ev_r = self.cmp.pairs, d.original, d.rebuilt
        if ev_o is not None:
            return [p for p in pairs if p[0].seq < ev_o.seq]
        if ev_r is not None:
            return [p for p in pairs if p[1].seq < ev_r.seq]
        return list(pairs)

    def leaves(self, d):
        """Where one run's path leaves the other's after the last shared event before a divergence:
        the first return in each run, before its next event, from a function the other run has not
        left by its next event (both runs' frames from the outermost); [(which, frame, block, result)],
        the rebuilt run's first."""
        pairs = self.before(d)
        out = []
        for which, rec, mine, theirs, end in (("rebuilt", self.reb, d.rebuilt, d.original, self.end_o),
                                              ("original", self.orig, d.original, d.rebuilt, self.end_r)):
            after = (pairs[-1][1].seq if which == "rebuilt" else pairs[-1][0].seq) + 1 if pairs else 0
            upto = mine.seq if mine is not None else len(rec.events)
            chain = theirs.frame.chain() if theirs is not None and theirs.frame is not None else list(end.frames)
            for pos, frame, block, value in rec.returns:
                if after <= pos <= upto and block is not None:
                    depth = len(frame.chain())
                    if depth <= len(chain) and chain[depth - 1].entry == frame.entry:
                        out.append((which, frame, block, value))
                        break
        return out

    def last_pc(self, rec, which):
        """The last instruction a run ran (its return, when it returned)."""
        return last_instruction(self.tracer, rec.prev, which) if rec.prev is not None else 0


# ---- the report's words (made from its data: TraceResult.data) ----

def report_text(data):
    """The report as dread trace prints it, from TraceResult.data."""
    cut = ["(the original's run made more than %d shared events: the trace compared those)" % MAX_EVENTS] \
        if data["truncated"] else []
    if data["agrees"]:
        a = data["agreed"]
        return "\n".join(cut + ["no divergence: %s, %s and %s agree, and so does the end" % (
            plural(a["calls"], "call"), plural(a["log"], "log entry", "log entries"),
            plural(a["stretches"], "stretch of stores", "stretches of stores"))])
    out = cut + divergence_lines(data["first"])
    if data["hard"] is None and not data["same"]:
        out += ["", "nothing dread test compares differs after it; dread test: the case agrees"]
    elif data["hard"] is not None:
        out.append("")
        out += ["then: %s (0x%x)" % (t["what"], t["original"]) for t in data["then"]]
        out += divergence_lines(data["hard"])
    return "\n".join(out)


def instruction_text(i):
    """The lines that show one image's instruction (TraceResult.instruction_data)."""
    out = ["  %-9s 0x%-8x %-32s %s" % (i["which"], i["address"], i["instruction"], i["where"])]
    s = i.get("statement")
    if s is not None:
        out.append("            its statement in the C (%s), %s:%d:" % (s["how"], s["path"], s["line"]))
        out.append("              %s" % s["text"])
    line = i.get("line")
    if line is not None:
        out.append("            from the C line %s:%d:" % (line["path"], line["line"]))
        if line["text"] is not None:
            out.append("              %s" % line["text"])
    return out


def stack_line(frames):
    """A call stack (TraceResult.stack_data) in one line, outermost first."""
    if not frames:
        return "-"
    parts = []
    for f in frames:
        name = f["function"]
        if f.get("built"):
            name += " [%s]" % f["built"]
        if f["helper"]:
            name += " (no shared point)"
        if f["from"] is not None:
            name += " from 0x%x" % f["from"]
        parts.append(name)
    return " > ".join(parts)


def divergence_lines(d):
    """The lines of one divergence (TraceResult.divergence_data)."""
    out = ["%s: %s" % (d["title"], d["what"])]
    if d["stretch"] is not None:
        out.append("  in the stretch of stores after %s" % d["stretch"])
    if d["original"] is not None:
        out += instruction_text(d["original"])
    elif d["original_none"] is not None:
        out.append("  original  %s" % d["original_none"])
    if d["rebuilt"] is not None:
        out += instruction_text(d["rebuilt"])
        if d["recent"] is not None:
            out.append("            the blocks it ran last: %s" % ", ".join("0x%x" % a for a in d["recent"]))
    elif d["rebuilt_none"] is not None:
        out.append("  rebuilt   %s" % d["rebuilt_none"])
    for w in d["writers"]:
        out.append("  where the %s run set it last:" % w["which"])
        out += ["  " + line for line in instruction_text(w)]
    for leave in d["leaves"]:
        out.append("  where the %s run's path leaves the other's: it returns from %s (0x%x), which the other run has "
                   "not left" % (leave["which"], leave["function"], leave["result"]))
        out += ["  " + line for line in instruction_text(leave["instruction"])]
    if d["stores"] is not None:
        out.append("  what differs at the stretch's end (each with the store that left it):")
        for s in d["stores"]:
            out.append("    0x%x: original %s, rebuilt %s" % (
                s["address"], "-" if s["original"] is None else "%s (0x%x)" % (s["original"], s["original_site"]),
                "-" if s["rebuilt"] is None else "%s (0x%x)" % (s["rebuilt"], s["rebuilt_site"])))
        if d["stores_total"] > 6:
            out.append("    ... %d runs of bytes in all" % d["stores_total"])
    out += ["  %s" % note for note in d["notes"]]
    out.append("  call stack, original: %s" % stack_line(d["stacks"]["original"]))
    out.append("  call stack, rebuilt:  %s" % stack_line(d["stacks"]["rebuilt"]))
    if d["events_total"]:
        out.append("  the last shared events before it (%d of %d):" % (d["events_shown"], d["events_total"]))
        for e in d["events"]:
            out.append("    %4d  %-44s original 0x%x, rebuilt 0x%x" % (e["index"], clip(e["text"], 44), e["original"],
                                                                     e["rebuilt"]))
    if d["frame"] is not None:
        out += frame_lines(d["frame"])
    if d["verdict"] is not None:
        v = d["verdict"]
        out.append("  dread test: %s" % ("; ".join(v[:4]) + ("; ..." if len(v) > 4 else "") if v
                                         else "the case agrees"))
    return out


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else many or one + "s")


def clip(text, n):
    return text if len(text) <= n else text[:n - 3] + "..."


def code_bytes(tracer, which, address, n):
    """n bytes of code at a run address in one image (through the startup's copies), or b""."""
    prog = tracer.ctx.program
    payload = tracer.run["recon"] if which == "rebuilt" else tracer.run["original"]
    off = prog.stored(address) - tracer.ctx.layout.base
    return payload[off:off + n] if 0 <= off < len(payload) else b""


def isa_at(tracer, which, address):
    """The instruction set of an address in one image: a C function's (its placement), else the
    original's code ranges, else the architecture's default."""
    prog = tracer.ctx.program
    if which == "rebuilt":
        got = tracer.placement.c_function(address)
        if got is not None:
            isa = tracer.placement.functions.get(got[0], {}).get("isa")
            if isa:
                return isa
    return prog.isa_at(address) if address in prog.code else tracer.ctx.arch.isa


def disassemble(tracer, which, address):
    """The instruction at an address of one image as text, or "?"."""
    prog = tracer.ctx.program
    if which == "original" and address in prog.code:
        insn = prog.insn(address)
    else:
        insn = tracer.ctx.arch.decode(code_bytes(tracer, which, address, 8), address, isa_at(tracer, which, address))
    return ("%s %s" % (insn.mnemonic, insn.op_str)).strip() if insn is not None else "?"


def last_instruction(tracer, block, which):
    """The address of the last instruction of a block ((address, size)) of one image, decoded from
    its start."""
    address, size = block
    arch, prog = tracer.ctx.arch, tracer.ctx.program
    if which == "original" and address in prog.code:
        a = address
        while a in prog.code and a + prog.code[a][0] < address + size:
            a += prog.code[a][0]
        return a
    code, isa = code_bytes(tracer, which, address, size + 8), isa_at(tracer, which, address)
    a = address
    while a < address + size:
        insn = arch.decode(code[a - address:a - address + 8], a, isa)
        if insn is None or a + insn.size >= address + size:
            return a
        a += insn.size
    return address


# ---- the stack frame ----

FRAME_ARRAY = re.compile(r"\bu32 frame\[(\d+)\]")
FRAME_BASE = re.compile(r"\bsp0 = \(u32\)&frame\[(\d+)\](?: \+ (0x[0-9a-fA-F]+|\d+)u?)?;")
OBJECT_DECL = re.compile(r"^\s*(?:volatile\s+)?([\w ]+?)\s+((?:stack|s)(\d+)(?:_\d+)?)(?:\[(\d+)\])?"
                         r"(?:\s+__attribute__\(\(.*?\)\))?\s*;", re.M)
SCALAR = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "u64": 8, "s64": 8}


def frame_objects(path):
    """[(name, depth below sp0, size or None)] of the original's frame as a function's C names it:
    lifted C's frame array, word by word (sp0 = (u32)&frame[J] + S: frame[k] at sp0 - S - 4 (J - k)),
    readable C's frame objects (stackN at sp0 - N) and dread.tidy's slot variables (sN at sp0 - N),
    sizes from their declarations (None for a type of another name: the debug information's, or up to
    the next object)."""
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return []
    out = []
    array, base = FRAME_ARRAY.search(text), FRAME_BASE.search(text)
    if array and base:
        n, j = int(array.group(1)), int(base.group(1))
        top = int(base.group(2), 0) if base.group(2) else 0
        out += [("frame[%d]" % k, top + 4 * (j - k), 4) for k in range(n) if top + 4 * (j - k) > 0]
    for m in OBJECT_DECL.finditer(text):
        kind, name, depth, count = m.group(1).split()[-1], m.group(2), int(m.group(3)), m.group(4)
        size = SCALAR.get(kind)
        if size is not None and count:
            size *= int(count)
        out.append((name, depth, size))
    return out


FRAME_ROWS = 40     # rows of a frame the report shows at most


def frame_text(result, d, snapshots):
    """The lines that show a stack frame at a divergence in both runs (the module's docstring); never
    stops the report."""
    got = frame_data(result, d, snapshots)
    return frame_lines(got) if got is not None else []


def frame_data(result, d, snapshots):
    """The stack frame at a divergence in both runs as data (the module's docstring), or None where
    nothing of it is on the stack: {"function", "built" (how it is built in the rebuilt image, null for
    the original's code), "sp0" (the original's stack pointer at its entry), "inuse" (down to where the
    original's frame is in use), "rebuilt_sp0" (null where the function is not on the rebuilt image's
    stack), "objects" (whether its C's objects label the rows; else the words from sp0 down), "rows":
    [{"depth" (below sp0), "label", "object" (the C's object, null for a saved register or a bare word),
    "size", "original" (its bytes there: hexbytes), "original_unset" (the frame's call has stored none
    of them yet), "rebuilt" (the object's bytes in the rebuilt image where the twin's debug information
    places it, else null), "rebuilt_unset", "rebuilt_missing" (the debug information has the object in
    no memory)}], at most FRAME_ROWS, "more" (rows left out)}; {"error"} where it could not be made (the
    frame is an aid: it never stops the report)."""
    try:
        return _frame_data(result, d, snapshots)
    except Exception as e:                  # (the frame is an aid)
        return {"error": "%s: %s" % (type(e).__name__, e)}


def frame_lines(f):
    """The lines of a frame (frame_data)."""
    if "error" in f:
        return ["  frame: not shown (%s)" % f["error"]]
    head = "  frame of %s (%s) at that point: original sp0 0x%x, in use down to 0x%x" % (
        f["function"], f["built"] or "the original's code", f["sp0"], f["inuse"])
    head += "; rebuilt sp0 0x%x" % f["rebuilt_sp0"] if f["rebuilt_sp0"] is not None else \
        "; not on the rebuilt image's stack"
    out = [head + ":"]
    if not f["objects"]:
        out.append("    (no objects of its C; the words from sp0 down)")
    for row in f["rows"]:
        text_o = row["original"] + (" (not set)" if row["original_unset"] else "")
        text_r = ""
        if row["rebuilt"] is not None:
            text_r = row["rebuilt"] + (" (not set)" if row["rebuilt_unset"] else "")
        elif row["rebuilt_missing"]:
            text_r = "(not in memory)"
        out.append(("    sp0-0x%-4x %-22s %-36s %s" % (row["depth"], row["label"], text_o,
                                                      "| rebuilt " + text_r if text_r else "")).rstrip())
    if f["more"]:
        out.append("    ...")
    return out


def _frame_data(result, d, snapshots):
    t = result.tracer
    snap_o, snap_r = snapshots

    def callers(snap, ev):
        frames = list(snap.frames)
        stub = ev is not None and ev.kind == "log" and ev.data and ev.data[0] not in IO_EVENTS and ev.data[0] != "svc"
        if ev is not None and (ev.kind == "entry" or stub) and len(frames) > 1:
            frames = frames[:-1]            # (the callee was entered there: its caller made the call)
        return frames
    mine, theirs = callers(snap_o, d.original), callers(snap_r, d.rebuilt)
    if not mine:
        return None
    common = 0                              # the frames both runs have, from the outermost
    while common < min(len(mine), len(theirs)) and mine[common].entry == theirs[common].entry:
        common += 1
    placement, lifter = t.placement, t.sources.lifter
    shared = mine[:common] or mine[:1]
    pick = next((f for f in reversed(shared) if placement.kind(t.ctx.names.get(f.entry), lifter)
                 in ("lifted C", "readable C")), shared[-1])
    name = t.ctx.names.get(pick.entry, "0x%x" % pick.entry)
    k = snap_o.frames.index(pick)
    inner = snap_o.frames[k + 1].sp if k + 1 < len(snap_o.frames) else snap_o.regs.get("sp", pick.sp)
    fr = theirs[mine.index(pick)] if mine.index(pick) < common else None
    word, sp0 = t.ctx.word, pick.sp
    built = placement.kind(name, lifter)
    objects = frame_objects(os.path.join(placement.lifted_dir, name + ".c")) if built in ("lifted C", "readable C") else []
    fn = t.ctx.program.by_name.get(name)
    saves = {}
    if fn is not None:
        from dread.frames import save_words
        try:
            saves = {depth: reg for depth, (reg, _) in save_words(t.ctx.program, fn).items()}
        except Exception:
            saves = {}
    places = rebuilt_places(t, name, fr, snap_r)
    out = {"function": name, "built": built, "sp0": sp0, "inuse": inner,
           "rebuilt_sp0": fr.sp if fr is not None else None, "objects": True, "rows": [], "more": False}
    rows = {}                               # depth -> (label, size, the C's object or None)
    for obj, depth, size in objects:
        if size is None:
            size = places.get(obj, (None, None))[1] or min([depth - o for _, o, _ in objects if o < depth] or [depth])
        rows[depth] = (obj, size, obj)
    for depth, reg in saves.items():
        obj, size, _ = rows.get(depth, (None, word, None))
        rows[depth] = ("%s (saved %s)" % (obj, reg) if obj else "saved %s" % reg, size, obj)
    if not rows:
        rows = {depth: ("", word, None) for depth in range(word, min(sp0 - inner, 16 * word) + 1, word)}
        if not rows:
            return None                     # (nothing of it on the stack)
        out["objects"] = False
    for depth in sorted(rows, reverse=True):
        if len(out["rows"]) >= FRAME_ROWS:
            out["more"] = True
            break
        label, size, obj = rows[depth]
        row = {"depth": depth, "label": label, "object": obj, "size": size,
               "original": hexbytes(snap_o.read(sp0 - depth, size)), "original_unset": False, "rebuilt": None,
               "rebuilt_unset": False, "rebuilt_missing": False}
        if obj is not None and depth not in saves and snap_o.set(sp0 - depth, size, pick) is False:
            row["original_unset"] = True    # (a save word's push stored a register: no data, but set)
        if obj is None:
            pass
        elif obj in places or (obj.startswith("frame[") and "frame" in places):
            at = places[obj][0] if obj in places else places["frame"][0] + 4 * int(obj[6:-1])
            row["rebuilt"] = hexbytes(snap_r.read(at, size if obj in places else 4))
            row["rebuilt_unset"] = snap_r.set(at, size, fr) is False
        elif t.sources.lines is not None and fr is not None:
            row["rebuilt_missing"] = True
        out["rows"].append(row)
    return out


def rebuilt_places(tracer, name, frame, snap):
    """{variable: (address, size)} of a function's C variables in memory at a rebuilt frame, where
    the twin's debug information places them at a fixed offset from the frame base (the canonical
    frame address, the stack pointer at the entry) or from the stack pointer; {} without it."""
    lines = tracer.sources.lines
    v = tracer.placement.functions.get(name)
    got = lines.variables(name, v["address"]) if lines is not None and frame is not None and v else None
    if got is None:
        return {}
    base_kind, variables = got
    sp_reg = DWARF_SP.get(tracer.ctx.arch.name)
    k = snap.frames.index(frame)
    if k + 1 < len(snap.frames):            # (the stack pointer at its call of the next frame)
        sp_here = snap.frames[k + 1].sp + (tracer.ctx.word if tracer.ctx.lr_id is None else 0)
    else:
        sp_here = snap.regs.get("sp")
    out = {}
    for var, size, loc in variables:
        address = None
        if loc[0] == "fb" and base_kind == "cfa":
            address = frame.sp + loc[1]
        elif loc[0] == "fb" and isinstance(base_kind, tuple) and base_kind[1] == sp_reg and sp_here is not None:
            address = sp_here + loc[1]
        elif loc[0] == "reg" and loc[1] == sp_reg and sp_here is not None:
            address = sp_here + loc[2]
        if address is not None:
            out[var] = (address, size)
    return out


def hexbytes(data):
    """Bytes as the frame shows them: up to a double word as a number, else the bytes in order."""
    if data is None:
        return "?"
    if len(data) in (1, 2, 4, 8):
        return "0x%0*x" % (2 * len(data), int.from_bytes(data, "little"))
    text = " ".join("%02x" % b for b in data[:16]) + (" ..." if len(data) > 16 else "")
    return "%s  %s" % (text, "".join(chr(b) if 32 <= b < 127 else "." for b in data[:16]))


# ---- the command ----

def shown_path(path, target):
    """A path as the report shows it: relative to the target's directory when inside it."""
    path = os.path.normpath(os.path.abspath(path))
    rel = os.path.relpath(path, target.root)
    return path if rel.startswith("..") else rel


def trace(target, image, name, label, mode="test", recon=None, whole=False, lines=True, src=None):
    """Trace one case; returns (TraceResult, a heading). Raises TraceError when it cannot. src: the
    target's C sources (default: the image's src) for the builds, and in mode "test" for the twin."""
    from dread import runner
    payload, work, twin, note, what, lifter = rebuilt_image(target, image, mode, name, recon, whole, lines, src)
    try:
        runner.prepare(target, image, payload)
    except (tgt.TargetError, EmuError) as e:
        raise TraceError(str(e))
    run = runner.RUN
    program = run["program"]
    case = find_case(run, name, label)
    placement = Placement(work, program)
    table = None
    if twin is not None:
        with open(payload, "rb") as f1, open(twin, "rb") as f2:
            same = f1.read() == f2.read()
        if not same:
            note = "no C lines: the build with -g is not byte for byte the traced image"
        else:
            try:
                table = Lines(os.path.join(os.path.dirname(twin), "recon.elf"))
            except ImportError:
                note = "no C lines: pyelftools is missing (pip install -e '.[dwarf]')"
            except Exception as e:
                note = "no C lines: the build's debug information could not be read (%s)" % e
    elif lines and note is None:
        note = "no C lines"
    sources = Sources(placement, table, lifter, target, image, program)
    tracer = Tracer(name, case, placement, sources, lifter)
    result = tracer.trace()
    how = placement.functions.get(name, {})
    built = placement.kind(name, lifter) or "the original's code (no C replaces it)"
    where = ""
    if how.get("branch"):
        where = ", moved to 0x%x behind a branch at 0x%x" % (how["address"], how["entry"])
    elif how:
        where = ", in place"
    heading = ["dread trace: %s [%s]: %s" % (name, case[0], what),
               "  %s is built from %s%s; rebuilt image %s" % (name, built, where, shown_path(payload, target))]
    if note:
        heading.append("  (%s)" % note)
    result.about = {"function": name, "label": case[0], "mode": mode, "what": what,
                    "built": placement.kind(name, lifter), "moved": bool(how.get("branch")),
                    "address": how.get("address"), "entry": how.get("entry"), "image": shown_path(payload, target),
                    "note": note}
    return result, "\n".join(heading)


JSON_VERSION = 1    # of dread trace --json's record (docs/formats.md)


def json_record(result=None, last=LAST, error=None, **about):
    """dread trace --json's record (docs/formats.md): what was traced (TraceResult.about: the function,
    the case's label, the mode, how the function is built and where, the rebuilt image, the note on the
    C lines) and the report as data (TraceResult.data), or the error that stopped it; "exit" as the
    command's exit status. No wall-clock time: the same trace gives the same bytes."""
    out = {"version": JSON_VERSION}
    if result is not None:
        out.update(result.about)
        out.update(result.data(last))
        out.update(last=last, exit=0 if result.agrees else 1, error=None)
    else:
        out.update(about)
        out.update(error=error, exit=2)
    return out


def write_json(path, record):
    """Write a record whole, renamed into place (a reader never sees half of it)."""
    import json
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, ".%s.tmp%d" % (os.path.basename(path), os.getpid()))
    with open(tmp, "w") as f:
        json.dump(record, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread trace", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("name", help="the function")
    parser.add_argument("label", help='the case\'s label as the runner prints it ("generated 3")')
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--lifted", action="store_true", help="build the image as dread lift --check does")
    group.add_argument("--readable", action="store_true", help="build the image as dread readable --check does")
    group.add_argument("--recon", metavar="PAYLOAD", help="the rebuilt image to trace (default: dread test's)")
    parser.add_argument("--whole", action="store_true", help="with --readable: the function's frame one object")
    parser.add_argument("--last", type=int, default=LAST, help="shared events shown before the divergence")
    parser.add_argument("--no-lines", action="store_true", help="no twin build with -g for the C lines")
    parser.add_argument("--src", help="the C sources the image is built from (default: the image's src)")
    parser.add_argument("--json", metavar="FILE", help="also write the report as JSON to FILE (docs/formats.md)")
    args = parser.parse_args(argv)
    mode = "lifted" if args.lifted else "readable" if args.readable else "test"
    try:
        target, image = tgt.from_args(args)
        result, heading = trace(target, image, args.name, args.label, mode, args.recon, args.whole,
                                not args.no_lines, args.src)
    except (tgt.TargetError, TraceError) as e:
        print("dread trace: %s" % e, file=sys.stderr)
        if args.json:
            write_json(args.json, json_record(error=str(e), function=args.name, label=args.label, mode=mode))
        return 2
    print(heading)
    print(result.text(args.last))
    if args.json:
        write_json(args.json, json_record(result, args.last))
    return 0 if result.agrees else 1


if __name__ == "__main__":
    sys.exit(main())
