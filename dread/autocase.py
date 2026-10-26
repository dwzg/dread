"""Generate test cases for a function from the original itself, guided by coverage.

For a function whose inputs are registers and plain memory (globals, or structures behind pointers
that the generator aims at scratch memory), cases need not be written by hand. The generator runs
the original with random register values and random contents of every location it is seen to read,
keeps the inputs that reach instructions no earlier input reached, and mutates those further.
Values come from boundary values and from the constants in the function's own instructions, so
that comparisons are hit on both sides.

Stages, each only for what the one before left uncovered:

1. random inputs and mutations of those that gained coverage; when fewer than four ran to the end,
   a second round with small arguments (half of them 0: a count split over two registers is small
   only when both halves are); an input that ran code no input ran before and then faulted, or ran
   to the instruction limit in a loop of the function's own, is a near miss and is repaired: each
   argument in turn set to 0 and the small values, the first that finishes with new code kept
   (REPAIR_RUNS runs per function). A loop over a length that the generator took for a pointer
   (the address that faulted lay just behind it) is reached so: gizmo-m4's script_run;
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
null-pointer check stayed uncovered). A real callee answers one way (newlib's _read stub always
fails): when code is still left after every other stage, the callee whose call stands nearest in
front of it is stubbed in a last stage, its result an input (decider): the compare-guided stage runs
once more with the stub in every input (its result and what it leaves are inputs there), then random
inputs; only inputs that run new code are kept, so the earlier stages are as they were. A generic stub returns a generated value (the first eight calls of a
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

The frontier answers (Generator.answers), after the search and from more than its cases: the
function's generated, kept and written cases together (what the lift check runs; a case dread
sysrecord recorded in the system run gets past checks no generated input does). A conditional branch
their runs took one way only is traced back through the function as dread uncovered traces it
(dread.uncovered.decide), and where what decides it is an input a case can give, a case made from
one that got there gives it what the other way needs:
  - a real callee's result: a stub that answers only that call (the callee's last call before the
    branch; the fourth element of a case's calls numbers the calls a stub answers, and the callee
    runs at the others, so the run is the same up to there): FreeRTOS's demo checks what its own
    stream-buffer calls return, every check a branch to an assertion no input reached;
  - a stubbed callee's answer at that call (the answers past the eighth call are 0, and the
    compare-guided stage varies the first SOLVE_RETURNS only);
  - the memory a load reads, wherever its address comes from (a global, a field of an argument's
    object or of what a global points to, a device register): where the load reads is taken from
    the run, at the visit of the branch; where the run read that word before, also from that read on
    only (a case's later: the word becomes the value just before it), so that two reads of one word
    can differ (FreeRTOS's stream buffers read their tail twice and go round while an interrupt moved
    it; clang -O2 unrolls that loop, and its copies ran in no case);
  - what a stubbed callee stores in a local it was handed the address of (a fill: the case gets the
    function's frame, so that the stub writes there, and a tight fill, so that it writes no further
    than the function's own stores): FreeRTOS's demo checks the value a notification delivers.
The values: the compare's other operand and its neighbours, the operand itself and its neighbours (for
a test its bits), solved through a constant offset when two runs show one, the word's edges, single
bits. A run that gains code and then hangs in a callee is tried with that callee stubbed, then ending
at its call; one that faults or stores into the image is chased (words that point just in front of
the failing access to buffers of their own, one at a time where given). The code behind a call at
which runs ended, at a stop there or deeper in (a callee that may return: configASSERT's handler waits
for a debugger, the RISC-V demo's prints first and waits in the UART's output), from a case whose run
ended so and from an answer that ends so: the callee answered at every call (where that call is its
first); where that run fails, as one that goes on into the null pointer the check guarded does, the
call answered and the callee's next call ending the run instead (a case's stop_from); where that fails
too, the first call the run makes after the answered one ending it (behind; until 2026-10-26 only the
first, for the answers' own runs that ended at the callee itself, and FreeRTOS's clang -O2 queue
functions kept the instruction behind their first configASSERT unrun). Each answer is a
case like any other (marked "answer"), compared under its stubs in both images; nothing is answered
that the run would not take as an input (a local, the image). Bounded by ANSWER_RUNS runs a function.

Generated cases are cached per function (BUILD/autocases/IMAGE/NAME.json; [paths] cases moves the
directory, which copies of one target may share: Target.cases_dir), keyed by the function's
bytes, the generator VERSION, the options and the environment (Generator.environment): what does not
belong to one function (the image's bytes, the startup copies, the memory map, the code's layout, the
convention) whole, and what does (what dread.regflow found of it, its stub's answers beyond a C result,
its domains) for the functions its runs can reach only: its call tree (reaches), or every function where
the tree calls through a pointer. A change of one function's facts makes the cases of the functions whose
trees hold it anew, not the image's (since 2026-10-23). A change that alters what the generator produces
needs a VERSION bump; SEED stays, so that a bump does not reshuffle every function's search. What
is added to a generation afterwards has a version of its own instead and is added to cached cases
without making them anew: the firm-up (FIRM, Generator.firm_up), and the frontier answers (ANSWERS:
under "answers" in the entry, keyed by the generation's key, ANSWERS and the kept and written cases,
so that a new recorded case makes them anew without a new search). The
search ends after a budget of runs and, in its last-resort stage, after LONG_SECONDS without any
input that finished: on a much slower machine that stage can find less.

Case format (also of the image's kept_cases and extra_cases files): docs/formats.md.
"""
import hashlib
import json
import re
import os
import random
import struct
import time

import capstone as cs
from capstone import arm as csarm
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE

from dread.cases import confine_args
from dread.emu import RUN_CALLEE, EmuError, Layout, Machine
from dread.program import Program, read_functions

VERSION = 34       # 2: stale Thumb IT state cleared in hooks; 3: data pointers are learned from faults;
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
                  # 16: so in the boundary sweep, for a run that hung in one;
                  # 17: a run's limit is a byte budget (dread.emu.Machine.call), STUB_CALLS 256;
                  # 18: STUB_CALLS 2,000 again (at 256 FreeRTOS's -O0 build lost coverage);
                  # 19: an emulator a CPU exception may have broken is replaced (dread.emu.Core.retire:
                  # cases made in a broken worker were cached), a quick failure is not cached;
                  # 20: a round of small arguments when fewer than four inputs ran to the end, and
                  # the callees a layer names for calls through a register (MSP430)
                  # 21: the Cortex-M a target names is the core cases run on (dread.arch.arm.uc_mode:
                  # with UC_MODE_MCLASS Unicorn built a Cortex-M33 whatever the target named);
                  # 22: near misses repaired (an input that ran new code, then faulted or looped to the
                  # limit in the function itself: each argument in turn set small);
                  # 23: float arguments (hard float): the FPU registers a function reads at its entry
                  # (dread.regflow) get values of their own (FLOATS, its float constants, any bits)
                  # 24: a case whose run calls through a register into the image at no function's
                  # entry is dropped and its pointer inputs get the pointer stub in the second pass
                  # (drop_wild); the stub's place compared without the Thumb bit where a run died
                  # 25: memory cells do not overlap (Cells.add): a pointer one byte off read the same
                  # buffer as new cells, written over most of the first one's bytes in every input;
                  # a run that ends on a branch to itself (for (;;);) is a case (Machine.self_loop_at)
                  # 26: the compare-guided stage chases a near miss: a run that reached new code and
                  # then failed is tried again with the words no input set as pointers (Generator.chase)
                  # 27: a branch's flag setter is found behind instructions that do not touch the flags
                  # (Arm.branch_deciders); a loop case's frame is the task's (Generator.loop_frame)
                  # 28: a call into the scratch (a callback the chase set to a buffer) is wild too:
                  # drop_wild repairs it with the pointer stub (dread.emu.Machine.watch_calls);
                  # 29: code pointers are learned in the first pass of every function that reaches a
                  # call through a register (the input a run jumped to becomes the pointer stub), not
                  # only in the pass drop_wild seeds: C++'s virtual calls (object, vtable, method)
                  # never ended before, so nothing seeded it; 30: an aligned memory cell takes the place
                  # of misaligned ones it overlaps (Cells.add: the first run to reach an object read it
                  # through a pointer put two bytes off, and the aligned runs never got its vtable
                  # pointer as a whole word; C++ on RISC-V); 31: a last stage stubs the callee whose call
                  # stands in front of the code still left, its result an input (decider: newlib's _read
                  # stub always fails, and _read_r's code for a read that succeeded went uncovered);
                  # 32: that stage first runs the compare-guided stage with the stub in every input
                  # (newlib's __sfp, _sbrk_r: the stub's result, what it leaves, and the chase), and the
                  # memory map is in the cache key (environment); 33: a run of the compare-guided stage
                  # that hangs in a callee, or fails behind the stub of a callee runs hung in, is tried
                  # once more, ending at that call (the sweep's did so for hangs); and a null stage
                  # gives each argument and learned pointer 0 once, from the first inputs, ending at
                  # the callees runs hung in (NULL_BASES); 34: when the cases that survive drop_bad_ends
                  # cover less than the search counted (a run that should have ended at a call and
                  # spun on a register counted, and no other input was sought), the compare-guided
                  # stage runs once more from what they cover, counting only runs whose case survives
                  # (repair_lost)
SEED = 17
DEBUG = bool(os.environ.get("DREAD_AUTOCASE_DEBUG"))
FAILURE_SECONDS = 10        # a generation that fails is cached only when it took this long (cases_for)
STUB_CALLS = 2000           # stub calls per run; beyond that the run is stopped and counts as not returning
                            # (256 sped one function's generation up ten times, runs a generated loop
                            # count kept going; but a -O0 build's functions lost coverage: a loop that
                            # empties a list a generated count long, calling a hung callee each time)
LOOP_CALLS = 48             # a loop case (a function that never returns) ends at this many stub calls
LOOP_CALLS_MAX = 512        # ... or at twice its call sites, up to this (Generator.loop_calls_for): FreeRTOS's
                            # test tasks call 64 to 175 times a round, and what lay behind the 48th call
                            # (the checks that set xErrorDetected) no loop case reached
SOLVE_RUNS = 3000           # emulator runs per function in the compare-guided stage ...
SOLVE_COST = 1_500_000      # ... and memory values written for them
SOLVE_EFFECTS = 64          # memory changes by callees examined per starting input
SOLVE_CLIMB = 10            # steps towards a comparison that no single change satisfies
SOLVE_RETURNS = 64          # results of stub calls that are inputs there (a case is generated with 8)
SOLVE_SLOTS = 400           # input values examined per starting input
SOLVE_LIMIT = 20_000        # instructions per run there
STALL = 2                   # on DREAD's own engines a search run stops after limit // STALL instructions that
                            # enter no block it had not entered (Generator.stall_part)
SWEEP_RUNS = 600            # emulator runs per base input in the boundary sweep
SWEEP_STEPS = SWEEP_RUNS // 4   # values a slot steps through at most (Generator.ladder)
BOUNDS = frozenset(range(17)) | {0xFF, 0x100, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF}  # always in the ladder
LONG_SECONDS = 120          # the last-resort search gives up after this long without one input that finished
KEPT_MAX = 6
HUNG_STUBS = 4              # callees at most that get stubbed because runs hung in them
SURVIVE_CHECKS = 64         # runs the repair checks against drop_bad_ends at most (Generator.repair_lost)
MAX_STOPS = 4               # calls a case ends at at most (the repair adds to those it has)
DECIDER_STUBS = 2           # callees at most that get stubbed because a stalled search left code behind their call
DECIDER_RUNS = 200          # inputs per such callee (the last stage)
ANSWER_RUNS = 1000          # runs of the frontier answers a function at most (Generator.answers)
ANSWER_TRIES = 20           # values one input is given for one branch (one way in, one visit) at most
ANSWER_FLIPS = 2            # ... the repairs of at most this many that went the other way and could not be kept
                            # (the next value mostly fails alike: later ones are tried without repairs)
ANSWER_VISITS = 2           # visits of a branch whose decision is answered (the first ones)
ANSWER_BASES = 2            # cases a branch is answered from at most (that give something to answer: Generator.answers)
ANSWER_SCAN = 8             # ... among the first this many cases that get there
ANSWER_EVENTS = 512         # calls and visits a run records at most
ANSWERS = 4                 # the frontier answers' version (Generator.answers): cached cases get them anew when
                            # it changes, or when the cases they are made from change (_answered); 2: the code
                            # behind a stop (behind, stop_from); 3: a word read again (reread, later); 4: behind a
                            # call whose run ended at a stop deeper in
READING_MARKS = ("carried", "firm", "kept", "why", "by", "answer")     # marks a case made from another drops
SMALL_ARGS = (1, 2, 3, 4, 8, 16)    # the round of small arguments: the values beside 0
# Float arguments (hard float), as bits: zeros, ones, halves, decimal steps, the integer conversions'
# bounds, the smallest and largest normal numbers, denormals, infinities, quiet and signalling NaNs
FLOATS = (0x00000000, 0x80000000, 0x3F800000, 0xBF800000, 0x3F000000, 0xBF000000, 0x40000000, 0x3E800000,
          0x41200000, 0x42C80000, 0x447A0000, 0x3A83126F, 0x3DCCCCCD, 0x40533333, 0xC0533333, 0x49742400,
          0xC9742400, 0x7149F2CA, 0x4F000000, 0xCF000000, 0x4F800000, 0x477FFF00, 0x00800000, 0x7F7FFFFF,
          0x00000001, 0x007FFFFF, 0x7F800000, 0xFF800000, 0x7FC00000, 0x7F800001)
HUNG_STOP = 0.5             # share of fresh inputs in which such a callee ends the run instead ("stops")
POINTER_REACH = 0x1000      # a fault this far behind an input's value makes the input a learned pointer
CHASE_STEPS = 4             # a near miss of the compare-guided stage is tried again so often, one pointer deeper each
CHASE_RUNS = 64             # near misses chased per function at most
CHASE_WORDS = 32            # words one step sets at most
CHASE_BUFFER = 0x100        # bytes a chased word's buffer has
CHASE_TRIES = 4             # given words the frontier answers' chase points elsewhere one at a time at most
POINTER_BASE = 0x1000       # learned pointers get buffers from scratch + POINTER_BASE ...
POINTER_STEP = 0x400        # ... this far apart ...
POINTER_SLOTS = 16          # ... at most this many different ones (then they are reused)
POINTER_KEEP = 0.9          # share of inputs that use the buffer (the rest keep their value: null checks)
NULL_BASES = 3              # inputs the null stage starts from (_generate_once)
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
REPAIR_RUNS = 256           # runs per function to repair near misses (an input that ran new code, then faulted)
FIRM = 1                    # the firm-up's version (Generator.firm_up): cached cases without it get it once
FIRM_RUNS = 300             # runs per function for variants of fragile cases (Generator.firm_up)
FIRM_VALUES = (4, 1, 0)     # the values one input of a fragile case is set to, one at a time (a length
                            # that fills a word-sized local first: 0 copies nothing and tells least)
SPIN_READS = 256            # one register read this often in a row with one value: the run spins
SPIN_POLL = 0.5             # share of inputs that get a poll stage for a register runs spun on
# String and memory routines that generated cases do not stub: small, pure, and what they write is
# behaviour of their caller (a stubbed strcpy copies nothing, and a caller that copies one byte too
# many into its frame then agrees with any C).
REAL_LIBRARY = ("memcpy", "memmove", "memset", "memcmp", "strcat", "strcmp", "strcpy", "strlen", "strncmp",
                "strncpy", "strstr", "strchr")
# Those of them that write: the frontier answers never answer a call of one either (the others, pure,
# may answer one call: a check of what memcmp found)
WRITING_LIBRARY = ("memcpy", "memmove", "memset", "strcat", "strcpy", "strncpy")



def cover_span(program, words):
    """[lo, hi) to record a function's coverage over: from its lowest instruction to behind its
    highest (a lifted Body's shared code may lie in front of the entry or behind the function)."""
    last = max(words)
    return min(words), last + program.insn_size(last)


class GenerationError(Exception):
    """No input of the search ran to the end: the function needs written cases."""


class Generator:
    """What generating and applying cases needs about one image; one per image and process."""

    def __init__(self, target, image, program=None, layout=None, payload=None):
        self.target, self.image = target, image
        self.program = program or Program(target, image)
        self.layout = layout or Layout(target, image)
        self._flow = None                   # dread.regflow, when a stub's mask needs it
        self._formats = None                # {entry: format argument's index}, (spans a format may not lie in)
        self._endless = None                # dread.noreturn, when a run hangs in a callee
        self._switches = None               # {name: entry} of the functions that switch stacks
        self._reaching = None               # {name: the stack switches its calls reach}
        self._whole = None                  # see whole()
        self._facts = None                  # see facts()
        self._scopes = None                 # see scopes()
        self._everything = None             # see everything()
        self._stall_part = None             # see stall_part
        self.wild_pins = None               # see drop_wild
        self.loop = False                   # generating loop cases (generate(loop=True))
        self.loop_calls = LOOP_CALLS        # their stub calls (loop_calls_for)
        self.payload = payload if payload is not None else image.payload()
        self.arch = image.arch
        mem = target.memory
        if mem.scratch_base is None:
            raise EmuError("[memory] scratch is not set: generated inputs need scratch memory for their buffers")
        self.scratch = mem.scratch_base
        self.fake_pointer = mem.scratch_base + mem.scratch_size // 2 & ~0xFFF      # stub for calls through pointers
        self.pointer_slots = self._pointer_slots(mem.scratch_size)
        # buffers of chased words (chase): the scratch above the pointer stub's page and the learned
        # pointers' buffers, where there is room (none on a 16 KiB scratch)
        low = max(self.fake_pointer + 0x1000, max(self.pointer_slots) + POINTER_STEP)
        self.chase_buffers = list(range(low, mem.scratch_base + mem.scratch_size - CHASE_BUFFER + 1, CHASE_BUFFER))
        self.boundary = tuple(range(17)) + (0x7F, 0x80, 0xFF, 0x100, 0x7FFF, 0x8000, 0xFFFF, 0x10000, 0x7FFFFFFF,
                                            0x80000000, 0xFFFFFFFE, 0xFFFFFFFF, self.scratch, self.scratch + 0x40,
                                            self.scratch + 0x104)
        self.code = self.program.instruction_halves()
        self.owners = self.program.extent_owners()  # a load from another function's extent is one of code
        self.entries = {fn.name: fn.entry for fn in self.program.functions}
        self.real = {self.entries[n] for n in REAL_LIBRARY if n in self.entries}
        self.writing = {self.entries[n] for n in WRITING_LIBRARY if n in self.entries}
        self.wide = self._wide_results()
        self._more = None                   # see more()
        self._tracer = None                 # dread.uncovered's Context, when a branch's sources are traced
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

    def more(self):
        """{entry: registers} of the functions that hand back registers beyond a C result (dread.lift's
        more_results: their callers read r2 or r3, which they set). A generic stub of one answers those
        too, derived from its answer as a wide_results callee's second word is (since 2026-10-23): a
        lifted caller's C has 0 in r2 behind an earlier C call where the original has what that callee
        left, and a stub that left r2 alone handed that difference on to the caller's reads of it. Found
        once; {} without regflow or a lifter for the architecture."""
        if self._more is None:
            self._more = {}
            flow = self.flow()
            if flow:
                from dread import lift
                try:
                    lifter = lift.make_lifter(self.program, flow)
                except lift.NoLifter:
                    lifter = None
                for fn in self.program.functions if lifter is not None else ():
                    regs = lifter.more_results(fn.name)
                    if regs:
                        self._more[fn.entry] = regs
        return self._more

    def _domains(self):
        """The image's domains table: {address: (bytes, values)}, rows NAME:rN+OFF (pointer
        domains), NAME:rN (argument domains) and NAME:ret (stub answers); docs/formats.md. The rows as
        written stay in domain_rows ({NAME: rows}, the address rows under ""), for the cache key."""
        self.domains, self.pointer_domains, self.argument_domains, self.result_domains = {}, {}, {}, {}
        self.domain_rows = {}
        path = self.image.table("domains")
        if not os.path.exists(path):
            return
        with open(path) as f:
            for line in f:
                fields = line.rstrip("\n").split("\t")
                if line.startswith("#") or len(fields) < 4:
                    continue
                self.domain_rows.setdefault(fields[0].split(":")[0] if ":" in fields[0] else "", []).append(
                    line.rstrip("\n"))
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

    def ladder(self, words, rng=None):
        """The values the boundary sweep steps a slot through: the function's constants and their
        neighbours, 0 to 16 and the word's edges (BOUNDS). Longer than SWEEP_STEPS, it keeps BOUNDS
        and a sample of the rest (with rng; without one, the full ladder): the sweep keeps at least
        four slots, so a long ladder broke its SWEEP_RUNS a base input (FreeRTOS's busiest test task
        has 579 values: 23,000 sweep runs, a third of its generation; twelve functions a build have
        more than 150). The cache key names the cap for those alone (cases_for)."""
        exact, near = self.constants(words)
        ladder = sorted(exact | near | BOUNDS)
        if rng is not None and len(ladder) > SWEEP_STEPS:
            rest = [v for v in ladder if v not in BOUNDS]
            ladder = sorted(set(rng.sample(rest, SWEEP_STEPS - len(BOUNDS))) | BOUNDS)
        return ladder

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
            if lit and lit[1] in (0, 4) and self.program.in_image(lit[0], 4):     # (0: vldr, a float constant)
                found.add(self.program.word(lit[0]))
        near = set()
        for v in found:
            near.update(x & 0xFFFFFFFF for x in (v + 1, v - 1, ~v, -v))
        return found, near

    def callees(self, words, depth=CALLEE_DEPTH):
        """The functions the instructions call directly, and theirs, `depth` levels deep; with a
        layer that names them (register_call_targets: MSP430), also those called through a register
        that holds their entry."""
        seen, level, out = set(), list(words), []
        through = getattr(self.arch, "register_call_targets", None)
        for _ in range(depth):
            nxt = []
            for ins in self.insns(level):
                if not self.arch.is_call(ins):
                    continue
                target = self.arch.target(ins)
                if target is not None:
                    targets = [target & ~1]
                elif through is not None:
                    targets = self._register_calls(ins.address).get(ins.address, ())
                else:
                    targets = ()
                for t in targets:
                    fn = self.program.by_entry.get(t)
                    if fn is not None and fn.entry not in seen:
                        seen.add(fn.entry)
                        out.append(fn)
                        nxt += fn.insns
            level = nxt
        return out

    def _register_calls(self, address):
        """The layer's register_call_targets of the function holding address, once per function."""
        fn = self.program.function_at(address)
        if fn is None:
            return {}
        cache = self.__dict__.setdefault("_register_call_cache", {})
        if fn.entry not in cache:
            cache[fn.entry] = self.arch.register_call_targets(self.program, fn)
        return cache[fn.entry]

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
        for reg, v in zip(case.get("fregs", ()), case.get("fargs", ())):     # float arguments (hard float)
            m.uc.reg_write(self.program.arch.reg_id(reg), v & 0xFFFFFFFF)
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
        counted, passing = {}, {}           # a callee's calls so far (answered or run); a call let run
        marks = getattr(m, "call_marks", None)  # (callee, its call number, stub calls before it, answered)
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
        taking = self.formats()[0]

        def stub(m, *args, target=0, nargs=0, nstack=0, only=None):
            if only is not None:            # it answers the calls numbered in only; the callee runs at the others
                sp = m.reg("sp")
                if passing.pop(target, None) == sp and m.unalias.get(m.reg("pc") & ~1) == target:
                    return RUN_CALLEE       # (the same call, through the branch at a moved callee's old entry)
                n = counted[target] = counted.get(target, 0) + 1
                if marks is not None:
                    marks.append(("call", target, n, made[0], n in only))
                if n not in only:
                    if target in m.aliases and m.reg("pc") & ~1 == target:
                        passing[target] = sp
                    return RUN_CALLEE
            elif marks is not None:
                n = counted[target] = counted.get(target, 0) + 1
                marks.append(("call", target, n, made[0], True))
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
            words = [int.from_bytes(m.read(sp + 4 * i, 4), "little") for i in range(nstack)]
            seen = [args[i] for i in range(len(args)) if nargs >> i & 1] + words
            # The registers a printf format takes are logged besides (format_mask), and only logged:
            # the fills behind pointers below go by the case's own mask, so a case made before
            # (2026-10-26) runs as it was made (counted among them, they changed the fills' seeds, and
            # a cached case of FreeRTOS's GCC -O0 prvCoreATask overwrote its loop counter otherwise and
            # faulted in the original)
            shown = seen
            more = self.format_mask(m, args, taking[target]) & ~nargs if target in taking else 0
            if more:
                shown = [args[i] for i in range(len(args)) if (nargs | more) >> i & 1] + words
            m.log("call 0x%x" % target, *("stack" if in_stack(a) else a for a in shown))
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
            for k, reg in enumerate(self.more().get(target, ())):     # (more: what its callers read beyond)
                m.reg(reg, (answer * 0x9E3779B1 + 0x7F4A7C15 * (k + 2)) & 0xFFFFFFFF)
            if target in self.wide and len(lay.result_regs) > 1:
                return answer, (answer * 0x9E3779B1 + 0x7F4A7C15) & 0xFFFFFFFF
            return answer

        answering = {}                      # callee -> its stub (what answers a stop's calls before stop_from's)
        for target, nargs, *rest in case.get("calls", ()):
            nstack = rest[0] if rest else 0
            only = frozenset(rest[1]) if len(rest) > 1 and rest[1] is not None else None
            answering[target] = lambda m, *a, target=target, nargs=nargs, nstack=nstack, only=only: \
                stub(m, *a, target=target, nargs=nargs, nstack=nstack, only=only)
            m.stub(target, answering[target])
        stop_from = {target: n for target, n in case.get("stop_from", ())}
        stop_calls = {}                     # a stop_from callee's calls so far

        def stop(m, *args, target=0):       # a callee that never returns: the run ends at its call
            first = stop_from.get(target)
            if first is not None:           # (stop_from: the calls before that one are as without the stop)
                sp, pc = m.reg("sp"), m.reg("pc") & ~1
                if passing.pop(target, None) == sp and m.unalias.get(pc) == target:
                    return RUN_CALLEE       # (the same call, through the branch at a moved callee's old entry)
                n = stop_calls[target] = stop_calls.get(target, 0) + 1
                if n < first:
                    got = answering[target](m, *args) if target in answering else RUN_CALLEE
                    if got is RUN_CALLEE and target in m.aliases and pc == target:
                        passing[target] = sp
                    return got
            # What it reads of the call's registers, as a stub logs it (stop_mask; until 2026-10-26 the
            # call alone, and the arguments of the call that ends the run were compared by nothing)
            mask = self.stop_mask(target) | (self.format_mask(m, args, taking[target]) if target in taking else 0)
            seen = [args[i] for i in range(len(args)) if mask >> i & 1]
            m.log("call 0x%x, never returns" % target, *("stack" if in_stack(a) else a for a in seen))
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

    def _pointer_slots(self, size):
        """Where learned data pointers get their buffers: POINTER_STEP apart from scratch +
        POINTER_BASE, POINTER_SLOTS of them, each inside the scratch and none on the pointer stub's
        address. A scratch of 64 KiB or more holds them all below the stub, as they always were; a
        small one fewer (16 KiB on the MSP430: ten), where the sixteen ran over the stub and past
        the scratch's end."""
        out, a, end = [], self.scratch + POINTER_BASE, self.scratch + size
        while len(out) < POINTER_SLOTS and a + POINTER_STEP <= end:
            if not a <= self.fake_pointer < a + POINTER_STEP:
                out.append(a)
            a += POINTER_STEP
        return out or [self.scratch]

    def environment(self, name=None):
        """A digest of what a function's cases depend on besides its own instructions (case_key): the part
        no call tree scopes (whole()) and, for the function `name`, the facts of every function its runs
        can reach (scopes(): its call tree), so that a change of one function's facts (what dread.regflow
        finds of it, its stub's answers) gives new keys to the functions whose trees hold it and keeps the
        others' (since 2026-10-23: until then one digest of the whole image's regflow and more, and a
        change anywhere made every case of the image anew, on a private firmware too, for a change that
        concerned the callers of some functions). With no name, or one the function
        table lacks, every function's facts (everything()): what a key held before."""
        part = self.scopes().get(name) if name is not None else None
        h = hashlib.sha1(b"environment 2\0" + self.whole().encode() + b"\0")
        h.update((part or self.everything()).encode())
        return h.hexdigest()

    def whole(self):
        """The part of the environment no call tree scopes, the same for every function of the image: the
        whole image (a literal pool holds the addresses of globals and callees: a rebuild that moved the
        data left a function's instructions alike and its cases stale, setting cells nobody reads; a run
        may load from anywhere in it), what the loader puts into RAM, the memory map (what an input may
        reach: the regions, and since 2026-10-23 the devices, the stack, the sentinel, the scratch and the
        image's limit), the stall cut and the learned pointers' slots where they apply, what dread.regflow
        found that belongs to no function (its register names; entries under names the function table
        lacks), and since 2026-10-23 the layout of the code (the code ranges and every function's entry,
        size and instruction set, without names: whether a load is one of code depends on every
        function's extent, dread.emu trace_reads; until then a table change rode on regflow's image-wide
        digest, which held every name, and a size change that left regflow's findings alike was in no
        key), the calling convention, the registers fixed in the whole program, the core, and the
        domains table's rows for addresses (a run may read any). None of it can be scoped to a call tree:
        a run reads memory, not functions."""
        if self._whole is None:
            h = hashlib.sha1(bytes(self.payload))
            for c in self.image.startup():
                h.update(repr((c.kind, c.run, c.size, c.stored)).encode())
                h.update(bytes(getattr(c, "data", None) or b""))
            h.update(b"regflow " + self._flow_parts()[0].encode())
            if self.stall_part:             # (only where it applies)
                h.update(b"stall %d" % self.stall_part)
            if self.pointer_slots != [self.scratch + POINTER_BASE + POINTER_STEP * k for k in range(POINTER_SLOTS)]:
                h.update(b"pointer slots " + repr(self.pointer_slots).encode())     # (a small scratch only)
            # the memory map: what an input may reach (the lab's page at address 0 let a probe's null
            # path end, which cases cached before it kept unseen)
            mem = self.target.memory
            h.update(b"memory " + repr([(r.name, r.base, r.size, r.kind) for r in mem.regions]).encode())
            h.update(repr((sorted(tuple(r) for r in mem.io), mem.stack_top, mem.stack_size, mem.sentinel,
                           mem.scratch_base, mem.scratch_size, self.image.base, self.image.limit)).encode())
            prog = self.program
            h.update(b"code " + repr([tuple(r) for r in prog.ranges]).encode())
            h.update(b"functions " + repr([(f.entry, f.size, f.isa) for f in prog.functions]).encode())
            abi = self.target.abi
            h.update(b"abi " + repr((list(abi.arg_regs), list(abi.result), list(abi.callee_saved),
                                     list(abi.fp_arg_regs), sorted(abi.stack_args.items()),
                                     sorted(self.image.fixed.items()), self.arch.describe())).encode())
            h.update(b"domains " + repr(self.domain_rows.get("", [])).encode())
            self._whole = h.hexdigest()
        return self._whole

    def _flow_parts(self):
        """(image-wide, {name: {field: its entry}}) of what dread.regflow found that cases read: its case
        facts (RegFlow.case_facts: the result's JSON without what no case reads, such as the registers a
        function writes on every path, overwritten, since regflow 25). A field that is a table by function
        name gives each function its entry (kept, reads, sites, preserved); any other field, and the entries
        under names the function table lacks, are image-wide (a JSON text; "none" without regflow). So a
        field regflow adds to its case facts is in the key without a change here: by function where it is a
        table by function name, image-wide otherwise; one it leaves out is in no key."""
        got = self.__dict__.get("_flow_split")
        if got is None:
            flow = self.flow()
            if not flow:
                got = ("none", {})
            else:
                names, whole, per = self.program.by_name, {}, {}
                for field, value in flow.case_facts().items():
                    if not isinstance(value, dict):
                        whole[field] = value
                        continue
                    rest = {k: v for k, v in value.items() if k not in names}
                    for k, v in value.items():
                        if k in names:
                            per.setdefault(k, {})[field] = v
                    if rest or not value:
                        whole[field] = rest
                got = (json.dumps(whole, sort_keys=True), per)
            self.__dict__["_flow_split"] = got
        return got

    def facts(self):
        """{name: digest} of what each function adds to the key of every function whose runs reach it
        (scopes()): its name and entry; its part of what dread.regflow found (_flow_parts: the registers its
        callers read after it and those it reads, the call sites behind them, those it leaves alone, and
        whatever table by function name the analysis adds); the registers beyond a C result its stub
        answers (more); whether its stub answers two words (the wide_results table, in no key before
        2026-10-23); its rows of the domains table (its stub's answers, NAME:ret, and its arguments; in no
        key before either). A stub logs what its callee reads, answers what its callers read, a function's
        float arguments get values: each of these is a fact of the function a run runs or stubs, and the
        lifter's and dread.uncovered's analyses the generator asks (frames, stack arguments, what decides a
        branch) read those of the function and its callees. Found once."""
        if self._facts is None:
            _, per = self._flow_parts()
            more = self.more()
            self._facts = {}
            for fn in self.program.functions:
                data = {"name": fn.name, "entry": fn.entry, "regflow": per.get(fn.name, {}),
                        "more": list(more.get(fn.entry, ())), "wide": fn.entry in self.wide,
                        "domains": self.domain_rows.get(fn.name, [])}
                self._facts[fn.name] = hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return self._facts

    def everything(self):
        """A digest of every function's facts (facts()): the scope of a run that may reach any function."""
        if self._everything is None:
            h = hashlib.sha1(b"every function\0")
            for name, digest in sorted(self.facts().items()):
                h.update(digest.encode())
            self._everything = h.hexdigest()
        return self._everything

    def scopes(self):
        """{name: digest}: each function's part of the environment, the facts (facts()) of every function
        its runs can reach (reaches(): its call tree, transitively, through tail calls and the dispatches
        whose targets dread.switches finds), digested bottom up over the call graph's strongly connected
        components: one digest a component, of its members' facts and the digests of the components they
        transfer to, so that the image costs one pass over its functions however deep the trees are. A
        component with an open transfer (a call through a function pointer: reaches) adds every function's
        facts (everything()): such a call may reach any function whose address a run can make, which the
        image's words alone do not tell (RISC-V forms an address with lui and addi), and on a Cortex-M a
        small pointer reads the vector table, whose reset handler reaches main and all it calls: on
        FreeRTOS's GCC builds the functions whose address the image holds reach every function. So a
        change of one function's facts gives new keys to the functions whose trees hold it and to the open
        ones: on FreeRTOS's builds 94 to 95 per cent of the functions have no open transfer in their tree
        on the ARM, 96 to 99 on RISC-V, 81 on the MSP430 (calls through a register the layer does not
        resolve). Found once (dread.runner and dread.uncovered find them before their workers fork)."""
        if self._scopes is None:
            try:
                graph = reaches(self.program)
            except Exception:               # (a layer the analysis cannot read: every scope is everything)
                graph = {fn.name: (set(), True) for fn in self.program.functions}
            facts, every = self.facts(), self.everything().encode()
            edges = {name: callees for name, (callees, _) in graph.items()}
            at, done = {}, []
            for k, members in enumerate(components(edges)):
                h = hashlib.sha1(b"scope\0")
                for name in members:
                    at[name] = k
                    h.update(facts[name].encode())
                for digest in sorted({done[at[c]] for name in members for c in edges[name] if at[c] != k}):
                    h.update(digest)
                if any(graph[name][1] for name in members):
                    h.update(b"open\0" + every)
                done.append(h.digest())
            self._scopes = {name: done[at[name]].hex() for name in graph}
        return self._scopes

    @property
    def stall_part(self):
        """STALL on DREAD's own engines, which interpret every instruction in Python (about 25 times
        Unicorn's cost an instruction), else 0. A search run there stops after limit // STALL
        instructions that enter no block it had not entered (dread.emu.Machine.call stall), as at the
        limit: circling in code it knows (a count of billions, a list that points at itself), it would
        mostly run into it. Measured from scratch on 2026-10-20 at half the limit: FreeRTOS's MSP430
        build generated in 760 s of CPU instead of 1,000 (the lift check 242 s instead of 370), the
        same 143 functions accepted; gizmo-msp430's checksum 244 s to 3 s, its lifted table as before.
        At a quarter (509 s) gizmo's script_run lost a summing loop's body (its cases ran 149 of 158
        instructions): a loop over a length entered no new code for longer than that and ended within
        the limit. Not on Unicorn: GCC -Os saved a fifth of its generation but lost two functions, one
        of them whole: FreeRTOS's blocking calls time out only after many rounds of a loop that enters
        no new code (the pended ticks the kernel counts in memory), and such runs end within it."""
        if self._stall_part is None:
            # (a new engine of its own: a Machine would reset the shared core, and the first answer
            # came while a run was being set up, which made the first generation differ)
            from unicorn import Uc
            self._stall_part = 0 if isinstance(self.layout.arch.new_uc(), Uc) else STALL
        return self._stall_part

    def stall_for(self, max_instructions):
        return max_instructions // self.stall_part if self.stall_part else 0

    def flow(self):
        """dread.regflow for the image, loaded once; False when it cannot be made."""
        if self._flow is None:
            from dread import regflow
            try:
                self._flow = regflow.load(self.target, self.image, self.program)
            except Exception:
                self._flow = False
        return self._flow

    def fp_inputs(self, name):
        """The FPU registers a function reads at its entry where floats travel in them (hard float:
        its float arguments, s0-s15 on ARM), as dread.regflow finds them; [] otherwise."""
        regs = list(self.target.abi.fp_arg_regs)
        if not regs or not name or not self.flow():
            return []
        reads = set(self._flow.read_regs(name))
        return [r for r in regs if r in reads]

    def stack_switches(self):
        """{name: entry} of the functions that load their stack pointer from memory not on the stack
        (dread.lift.Lifter.switches_stacks): context switches. Only functions with an instruction that
        writes the stack pointer without reading it are looked at."""
        if self._switches is None:
            from dread import lift
            arch, lifter, found = self.program.arch, None, {}
            for fn in self.program.functions:
                for a in fn.insns:
                    insn = self.program.insn(a)
                    if insn is None:
                        continue
                    reads, writes = arch.reg_effects(insn)
                    if "sp" in writes and "sp" not in reads:
                        break
                else:
                    continue
                if lifter is None:
                    try:
                        lifter = lift.make_lifter(self.program, self.flow() or None)
                    except lift.NoLifter:
                        break
                if lifter.switches_stacks(fn):
                    found[fn.name] = fn.entry
            self._switches = found
        return self._switches

    def switch_stubs(self, name):
        """[[entry, mask, 0]]: stubs for the stack switches that name's calls reach, however deep. A
        context switch run for real in a function's case loads the next task's stack pointer from
        a generated TCB and the run goes nowhere (FreeRTOS on the MSP430: every input of a function
        that yields died so); stubbed, it returns, as a yield does in the cases on the ARM (a store
        into the interrupt controller) and on RISC-V (ecall, a logged no-op)."""
        switches = self.stack_switches()
        if not switches:
            return []
        if self._reaching is None:
            from dread import lift
            callers = {}
            for fn in self.program.functions:       # calls, tail calls, and calls through a register
                for c in lift.direct_callees(self.program, fn):     # that holds an entry (call r8)
                    callers.setdefault(c, set()).add(fn.name)
            reaching = {}
            for s in switches:
                seen, work = set(), [s]
                while work:
                    for c in callers.get(work.pop(), ()):
                        if c not in seen:
                            seen.add(c)
                            work.append(c)
                for c in seen:
                    reaching.setdefault(c, set()).add(s)
            self._reaching = reaching
        return [[switches[s], self.reads_mask(s), 0] for s in sorted(self._reaching.get(name, ())) if s != name]

    def loop_frame(self, name):
        """A loop case's frame: the bytes the function's stack holds at its deepest (dread.lift's
        stack analysis), so that its stubbed callees write into the locals it hands them (a receive
        buffer) and the compare-guided stage gets those bytes as inputs (fills). With none, a task
        that checks what it received could not get past its first check: FreeRTOS's
        prvReceivingTask, whose stream buffer delivers a string byte by byte, had 31 of its 64
        instructions unrun by its loop cases, none with its frame. 0 where the analysis fails."""
        cache = self.__dict__.setdefault("_loop_frames", {})
        if name not in cache:
            from dread import lift
            try:
                lifter = self.__dict__.get("_frame_lifter") or lift.make_lifter(self.program, self.flow() or None)
                self.__dict__["_frame_lifter"] = lifter
                depths = lifter.depths(self.program.by_name[name], strict=False)
                cache[name] = (max(depths.values()) + 7) & ~7 if depths else 0
            except Exception:
                cache[name] = 0
        return cache[name]

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

    def stop_mask(self, entry):
        """The argument registers a stop logs at the call of the function at entry (a case's stops: one
        that never returns, or one a run hung in): what it reads (reads_mask), as a stub of it logs; 0
        for an address that is no function's entry."""
        fn = self.program.by_entry.get(entry & ~1)
        return self.reads_mask(fn.name) if fn is not None else 0

    def formats(self):
        """({entry: the index of its format argument} of the functions that take a printf format, the
        spans a format read at a call may not lie in): dread.varargs family (the printf family by name and
        the target's [abi] formats), whose stubs log the registers the format takes (format_mask); the
        image's code, its free ranges and the room behind it up to its limit (where a rebuilt image has
        code of its own, so a format read there would differ between the images), and the devices'
        registers (a read may do something). Found once."""
        if self._formats is None:
            from dread import varargs
            taking = varargs.family(self.target)
            image = self.image
            spans = [(lo, hi) for lo, hi, _ in self.program.ranges] + list(image.free)
            if image.limit > image.end:
                spans.append((image.end, image.limit))
            spans += list(self.target.memory.io)
            self._formats = ({fn.entry: taking[fn.name] for fn in self.program.functions if fn.name in taking},
                             sorted(spans))
        return self._formats

    def format_mask(self, m, args, index):
        """The argument registers a printf-family call's format takes after it, as a stub's logging mask:
        the format read from the machine's memory at the call (dread.varargs: one register for each word
        of an argument, as far as the argument registers go); 0 where it cannot be read, is not plain or
        lies where the images differ (formats). Logged, they compare what a rebuilt caller passes with
        what the original passes as far as the format takes it (dread.readable trims a call of such a
        function to them; until 2026-10-26 a stub logged what dread.regflow says the callee reads, the
        format alone, and printf's other arguments were compared by no case)."""
        if index >= len(args):
            return 0
        from dread import varargs
        k = varargs.consumed(varargs.text_at(varargs.clipped(m.read, self.formats()[1]), args[index]))
        if k is None:
            return 0
        return sum(1 << i for i in range(index + 1, min(index + 1 + k, len(args))))

    def machine(self, payload=None, aliases=None):
        return Machine(self.layout, self.payload if payload is None else payload, aliases, isa_of=self.program.isa_at)

    def touches_image(self, m):
        return m.code_reads or m.wrote_into(self.image.base, self.program.writable_end())

    def image_stores(self, m, most=4):
        """The first addresses the last run stored to inside the image (a pointer an input gave as a
        small number: on a Cortex-M the flash lies at 0, readable, so it faults nowhere)."""
        lo, hi = self.image.base, self.program.writable_end()
        ranges = getattr(m, "written_ranges", None)
        if ranges is None:
            return sorted(a for a in m.written if lo <= a < hi)[:most]
        return [max(a, lo) for a, b in sorted(ranges) if a < hi and lo < b][:most]

    @staticmethod
    def ends_badly(m):
        """Whether a run of a case that ends at a call (stops, a loop case) ended neither by a return
        nor at that call (at the instruction limit, or spinning on a register): no common place to
        compare it, so it is no case; it may still teach the compare-guided stage. A run that came to a
        branch to itself (dread.emu.Machine.self_loop_at: for (;;);) ended there in both images alike:
        FreeRTOS's malloc and stack-overflow hooks got no loop case before."""
        return getattr(m, "ends_at_call", False) and not m.stopped_at and bool(m.timed_out or m.spinning) \
            and m.self_loop is None

    def coverage(self, entry, words, case, limit=200_000, check_image=False):
        """The function's instructions this input executes; empty if it does not finish (or, with
        check_image, when it loads instructions as data or stores into the image)."""
        ran, why = self.attempt(entry, words, case, limit, check_image)
        return set() if why else ran

    def attempt(self, entry, words, case, limit=200_000, check_image=False):
        """(the function's instructions the input executes, None) when it finishes, else (what it ran
        before it stopped, why: the fault or the limit with the function and instruction where the
        run was then and the general registers, or that it loaded instructions as data or stored into
        the image). dread pilot hands the why to the model: told only that an input did not finish, it
        could not tell which callee its input failed in."""
        m = self.machine()
        m.plan = []
        try:
            self.apply(m, case)
            m.cover(*cover_span(self.program, words))
            if check_image:
                m.trace_reads(self.image.base, self.image.end, self.code, self.owners)
            m.call(entry, *case["args"], max_instructions=limit)
        except Exception as e:
            return m.covered_insns(words), self.stopped_where(m, e)
        if check_image and self.touches_image(m):
            return m.covered_insns(words), "it loads instructions as data or stores into the image"
        if check_image and self.ends_badly(m):
            return m.covered_insns(words), "it ends neither by a return nor at a call that ends the run"
        return m.covered_insns(words), None

    def stopped_where(self, m, error):
        """A line on where and why a run stopped: the error, the function and instruction at pc, the
        general registers."""
        prog = self.program
        try:
            pc = m.reg("pc") & ~1
        except Exception:
            return str(error)
        fn, insn = prog.function_at(pc), prog.insn(pc)
        regs = []
        for name in prog.arch.general_regs():
            try:
                regs.append("%s=0x%x" % (name, m.reg(name)))
            except Exception:
                pass
        return "%s; at 0x%x in %s (%s); registers: %s" % (
            error, pc, fn.name if fn else "no function", ("%s %s" % (insn.mnemonic, insn.op_str)).strip()
            if insn is not None else "no instruction", " ".join(regs))

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
        calls_before, self.loop_calls = self.loop_calls, self.loop_calls_for(words) if loop else LOOP_CALLS
        try:
            if not fallbacks:
                return self._generate_once(name, entry, words, calls, frame, count, budget, limit, sweep, learn=True)
            return self._generate_all(name, entry, words, calls, frame, count, budget, limit, sweep)
        finally:
            self.current = before
            self.deadline = None
            self.loop = loop_before
            self.loop_calls = calls_before

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
        pins = self.wild_pins               # inputs a dropped case pointed a call into the image with
        covered = set()
        for c in cases:
            covered |= self.coverage(entry, words, c, c.get("limit", limit))
        if pins and not set(words) <= covered:
            # the same pass again with those inputs the pointer stub (the search is seeded: it learns
            # the same data pointers, so that a callback cell behind one is the same address)
            try:
                again = self._generate_once(*args, learn=True, pins=pins)
            except GenerationError:
                again = []
            for c in again:
                got = self.coverage(entry, words, c, c.get("limit", limit))
                if got - covered:
                    covered |= got
                    cases.append(c)
        if set(words) <= covered:
            return cases
        try:
            other = self._generate_once(*args, learn=False, pins=pins)
        except GenerationError:
            return cases
        for c in other:
            got = self.coverage(entry, words, c, c.get("limit", limit))
            if got - covered:
                covered |= got
                cases.append(c)
        return cases

    def _generate_once(self, name, entry, words, calls, frame, count, budget, limit, sweep, learn, pins=None):
        """One search pass (see generate). pins: (argument indices, memory addresses) whose inputs are
        the pointer stub from the start (drop_wild found code pointers there in an earlier pass)."""
        if pins and all(c[0] != self.fake_pointer for c in calls):
            calls = list(calls) + [[self.fake_pointer, 0, 0]]
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
        start, end = cover_span(self.program, words)
        known, covered, corpus, extras, faults, last_gain = Cells(), set(), [], [], 0, 0
        calls = [list(c) for c in calls]    # grows by the callees runs hang in (hung, below)
        hung = set()
        deciders = set()                    # callees stubbed because the search stalled behind their call
        stops = []                          # loop cases: callees that never return end the run
        if self.loop:
            calls, stops = self.loop_stubs(words, calls)
        # Inputs a run jumped to are learned as code pointers (the pointer stub in later inputs) in
        # every function that reaches a call through a register: a pointer two loads deep (C++'s
        # object, its vtable, the method) faults in every input until it is the stub, so no input
        # ends and drop_wild never seeds the pass that would learn it
        sites = self.program.icall_sites()
        pointers = any(c[0] == self.fake_pointer for c in calls) or bool(sites and set(sites) & set(words).union(
            *(f.insns for f in self.callees(words))))
        pin_args, pin_mem = set(pins[0]) if pins else set(), set(pins[1]) if pins else set()
        data_args, data_mem = {}, {}        # inputs learned to be data pointers -> their buffer
        words_set = set(words)
        nregs = len(self.layout.arg_regs)
        fregs = self.fp_inputs(name)        # float arguments in the FPU's registers (hard float)

        def buffer_for(slot):
            """A scratch buffer of its own for each learned data pointer (pointer_slots)."""
            used = len(data_args) + len(data_mem)
            return self.pointer_slots[used % len(self.pointer_slots)]

        fake_code = self.fake_code(entry)

        def pinned(case):
            for i in pin_args:
                case["args"][i] = fake_code
            for m_ in case["mem"]:
                if m_[0] in pin_mem:
                    m_[2] = fake_code
            if pins:                        # a callback cell an earlier pass found: there even where
                given = {m_[0] for m_ in case["mem"]}       # this input sets nothing (it reads 0)
                case["mem"] += [[a, 4, fake_code] for a in sorted(pins[1]) if a not in given]
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
            """After a fault on unmapped memory, or a store into the image: the inputs whose value lies
            just in front of that address were pointers (on a Cortex-M the flash lies at 0, so a pointer
            an input gave as a small number faults nowhere, and what is stored through it goes into the
            image: newlib's __sfp stored errno through its reent pointer 0x8)."""
            addresses = ([m.unmapped[1]] if m.unmapped is not None else []) + self.image_stores(m)
            for address in addresses:
                for i, v in enumerate(case["args"][:nregs]):
                    if 0 <= address - (v & 0xFFFFFFFF) < POINTER_REACH and i not in data_args and i not in pin_args:
                        data_args[i] = buffer_for(("arg", i))
                for a, n, v in case["mem"]:
                    if n == 4 and 0 <= address - (v & 0xFFFFFFFF) < POINTER_REACH and a not in data_mem \
                            and a not in pin_mem:
                        data_mem[a] = buffer_for(("mem", a))

        def value():
            return rng.choice(hot) if rng.random() < 0.5 else rng.choice(pool)

        floats = [w for w in sorted(exact) if 0x00800000 <= w & 0x7FFFFFFF < 0x7F800000]   # (its float constants)

        def fvalue():
            """A float argument: the boundaries and special values (FLOATS), the function's own float
            constants, or any bits."""
            r = rng.random()
            if r < 0.6:
                return rng.choice(FLOATS)
            if r < 0.85 and floats:
                return rng.choice(floats) ^ (0x80000000 if rng.random() < 0.3 else 0)
            return rng.getrandbits(32)

        def any_value(kind):
            return fvalue() if kind == "fargs" else value()

        small = [False]

        def small_value():
            """An argument of the round of small values, which starts when fewer than four inputs ran
            to the end: half of them 0. A count split over two registers (MSP430's 32-bit length in
            r14:r15) is small only when both halves are, which random values almost never are; every
            input of gizmo's strncpy ran off memory so."""
            return 0 if rng.random() < 0.5 else rng.choice(SMALL_ARGS)

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
            case = {"args": [small_value() if small[0] else value() for _ in range(nregs)],
                    "mem": [[a, s, value()] for a, s in sorted(known.items())],
                    "returns": [value() for _ in range(8)] if calls else [], "calls": [list(c) for c in calls],
                    **({"fregs": list(fregs), "fargs": [fvalue() for _ in fregs]} if fregs else {}),
                    "frame": frame, **({"io": "fixed"} if self.io_fixed else {}), **({"limit": self.long} if self.long else {}),
                    **({"hang": True, "limit": limit, "stub_calls": self.loop_calls, "stops": list(stops)} if self.loop else {})}
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
            m.cover(start, end)
            m.trace_reads(image.base, image.end, self.code, self.owners)
            m.call(entry, *case["args"], max_instructions=max_instructions, stall=self.stall_for(max_instructions))
            if self.loop and self.ends_badly(m):
                raise EmuError("a loop case ended neither at a stub call nor by a return")
            return m

        stepping, tree_seen = [], set()     # inputs that only reached new code in callees
        repairs = [REPAIR_RUNS, set()]     # runs left; the new code of near misses already tried

        def repair(case, new):
            """A near miss: the input ran code no input ran before and then faulted, or ran to the
            instruction limit in a loop of the function's own (a loop over a length the generator
            took for a pointer: the address that faulted lay just behind it, and every later input
            aimed it at a buffer). Each argument in turn is set to 0 and the
            small values; the first that finishes with new code is kept. None if none does. A near
            miss to code an earlier one was repaired for (or tried for) is not tried again."""
            if new in repairs[1]:
                return None
            repairs[1].add(new)
            for i in range(nregs):
                for v in (0,) + SMALL_ARGS:
                    if case["args"][i] == v or repairs[0] <= 0:
                        continue
                    repairs[0] -= 1
                    trial = json.loads(json.dumps(case))
                    trial["args"][i] = v
                    try:
                        m = run(trial)
                    except EmuError:
                        continue
                    if self.touches_image(m):
                        continue
                    got = m.covered_insns(words)
                    if got - covered:
                        return trial, got
            return None
        started = time.time()
        for attempt in range(budget * 5):
            patience = budget * 2 if (data_args or data_mem) and (strings or chars) else budget
            if attempt >= budget and (words_set <= covered or attempt - last_gain > patience):
                if small[0] or len(corpus) + len(extras) >= 4:
                    break
                small[0], last_gain = True, attempt     # once more with small arguments (small_value)
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
                            + [("returns", i) for i in range(len(case["returns"]))] \
                            + [("fargs", i) for i in range(len(case.get("fargs", ())))]
                        kind, i = rng.choice(slots)
                        if kind == "mem":
                            case["mem"][i][2] = value()
                        else:
                            case[kind][i] = any_value(kind)
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
            m.cover(start, end, tree=True)
            m.trace_reads(image.base, image.end, self.code, self.owners)
            try:
                m.call(entry, *case["args"], max_instructions=limit, stall=self.stall_for(limit))
            except EmuError:
                if m.self_loop is not None and self.program.function_at(m.self_loop) is self.program.by_entry.get(entry):
                    # It came to a loop of its own that nothing leaves (for (;;); behind an error): there
                    # it has done all it does, in both images alike, so the run is a "hang" case,
                    # compared up to the limit (the runner gives both images the same budget)
                    got = m.covered_insns(words)
                    if got - covered and not self.touches_image(m):
                        case["hang"], case["limit"] = True, limit
                        covered |= got
                        corpus.append(case)
                        last_gain = attempt
                    known.learn(m.reads)
                    continue
                faults += 1
                if m.spinning:
                    spins.setdefault(m.spinning[0], m.spinning[1:])
                new = frozenset(m.covered_insns(words) - covered)
                stuck = self.program.function_at(m.reg("pc") & ~1) if m.timed_out else None
                if new and repairs[0] > 0 and (not m.timed_out or (stuck is not None and stuck.entry == entry)):
                    fixed = repair(case, new)       # a fault, or a loop of its own that ran to the limit
                    if fixed is not None:
                        covered |= fixed[1]
                        corpus.append(fixed[0])
                        last_gain = attempt
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
                    pin_args.update(i for i, v in enumerate(case["args"][:nregs]) if v & ~1 in went)
                    pin_mem.update(a for a, n, v in case["mem"] if n == 4 and v & ~1 in went)
                    if (pin_args or pin_mem) and all(c[0] != self.fake_pointer for c in calls):
                        calls.append([self.fake_pointer, 0, 0])     # (the stub those inputs go to)
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
                        known.add(a, n)
                continue
            if m.spinning:                      # (a case that ends at a call came back spinning: the
                spins.setdefault(m.spinning[0], m.spinning[1:])     # next inputs get a poll stage)
            if self.touches_image(m) or (self.loop and self.ends_badly(m)):
                faults += 1
                learn_pointers(m, case)         # (a store into the image through an input's small number)
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
                    + [("returns", i) for i in range(len(case["returns"]))] \
                    + [("fargs", i) for i in range(len(case.get("fargs", ())))]
                for _ in range(rng.choice((1, 1, 1, 2))):
                    kind, i = rng.choice(slots)
                    if kind == "mem":
                        case["mem"][i][2] = value()
                    else:
                        case[kind][i] = any_value(kind)
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


        # Null stage: each argument and each learned pointer cell at 0 once, from the first inputs,
        # ending at the callees runs hung in (an assertion handler that waits for a debugger; a run
        # that hangs in another callee is tried once more ending there). A null check's assertion
        # path (configASSERT) needs exactly that input, which the random stages make rarely (a
        # learned pointer takes its buffer) and lose or find anew with every change of the
        # generator: measure-35 (generator 32) lost such paths on every build and found others.
        def null_run(case):
            m = self.machine()
            m.plan = []
            m.spin_limit = SPIN_READS
            self.apply(m, case)
            m.cover(start, end)
            m.trace_reads(image.base, image.end, self.code, self.owners)
            try:
                m.call(entry, *case["args"], max_instructions=limit, stall=self.stall_for(limit))
                return m, True
            except EmuError:
                return m, False
        if corpus and not words_set <= covered and not self.loop:    # (cheap: before the long stages)
            for base in corpus[:NULL_BASES]:
                slots = [("args", i) for i in range(nregs) if base["args"][i] != 0] + \
                    [("mem", k) for k, cell in enumerate(base["mem"]) if cell[0] in data_mem and cell[1] == 4 and cell[2]]
                for kind, i in slots:
                    if words_set <= covered:
                        break
                    case = json.loads(json.dumps(base))
                    if kind == "args":
                        case["args"][i] = 0
                    else:
                        case["mem"][i][2] = 0
                    if hung and not case.get("stops"):
                        case.update({"hang": True, "limit": limit, "stops": sorted(hung)})
                    m, ok = null_run(case)
                    inside = self.program.function_at(m.reg("pc") & ~1) if not ok and m.timed_out else None
                    if inside is not None and inside.entry != entry and not case.get("stops"):
                        case.update({"hang": True, "limit": limit, "stops": [inside.entry]})
                        m, ok = null_run(case)
                    if not ok or self.touches_image(m) or self.ends_badly(m):
                        continue
                    got = m.covered_insns(words)
                    if got - covered:
                        covered |= got
                        corpus.append(case)

        first_stages = len(corpus)
        if not words_set <= covered and corpus and not self.late():
            self._solve(name, entry, words, end, calls, limit, rng, pool, known, covered, corpus, reach, edge, hung)

        # Boundary sweep: step each value of a few inputs through the constants and their
        # neighbours; where the observable result changes from one value to the next, both sides
        # are kept. A value whose every step changes the result is data and gets a sample only.
        ladder = self.ladder(words, rng)
        swept = []

        def sweep_run(case):
            """(machine, result) of one run of the sweep; result None when it did not return (the
            machine says why), machine None when it failed otherwise."""
            m = self.machine()
            m.plan = []
            try:
                self.apply(m, case)
                m.cover(start, end)
                m.trace_reads(image.base, image.end, self.code, self.owners)
                return m, m.call(entry, *case["args"], max_instructions=limit, stall=self.stall_for(limit))
            except EmuError:
                return (m, None) if m.timed_out else (None, None)
        for base in (corpus[:first_stages][-sweep:] + corpus[first_stages:][-sweep:] if corpus else extras[:1]):
            slots = [("args", i) for i in range(nregs)] + [("mem", i) for i in range(len(base["mem"]))] \
                + [("returns", i) for i in range(len(base["returns"]))] \
                + [("fargs", i) for i in range(len(base.get("fargs", ())))]
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
        # Last: a real callee answers one way (newlib's _read stub always fails, and _read_r's code for
        # a read that succeeded went uncovered). Where code is still left, the callee whose call stands
        # nearest in front of it is stubbed in some more inputs, its result an input; an input is kept
        # only when it runs new code. After every other stage, so that it only adds (in the first stage
        # it took gizmo-msp430's script_run 14 instructions: the later stages mutated stubbed inputs).
        for _ in range(DECIDER_STUBS):
            if words_set <= covered or self.loop or self.late():
                break
            stub = self.decider(words, covered, calls, deciders)
            if stub is None:
                break
            deciders.add(stub.entry)
            extra = [stub.entry, self.reads_mask(stub.name), 0]
            # First the compare-guided stage with the stub in every input: the stub's result and what
            # it leaves in memory are inputs there, and the chase points an input that a new path
            # stores through at scratch (newlib's __sfp stores errno through its reent pointer when
            # __sfmoreglue fails, and the inputs had that pointer in the flash, readable at address 8 on
            # a Cortex-M; _sbrk_r stores errno only when _sbrk fails and left errno set). On a copy of
            # the corpus, so that what it adds keeps the stub and nothing else changes.
            if corpus and not self.late():
                stubbed = []
                for case in corpus:
                    c = json.loads(json.dumps(case))
                    c["calls"] = [list(x) for x in c.get("calls", ()) if x[0] != stub.entry] + [list(extra)]
                    c["returns"] = c.get("returns") or [0] * 8
                    stubbed.append(c)
                before = len(stubbed)
                self._solve(name, entry, words, end, [list(x) for x in calls if x[0] != stub.entry] + [list(extra)],
                            limit, rng, pool, known, covered, stubbed, dict(reach), edge, hung)
                corpus.extend(stubbed[before:])
            for _ in range(DECIDER_RUNS):
                if words_set <= covered or self.late():
                    break
                case = json.loads(json.dumps(rng.choice(corpus))) if corpus and rng.random() < 0.8 else fresh()
                case["calls"] = [list(c) for c in case.get("calls", ()) if c[0] != stub.entry] + [list(extra)]
                case["returns"] = [value() for _ in range(8)]
                if rng.random() < 0.5:
                    case["args"] = [value() if rng.random() < 0.3 else a for a in case["args"]]
                try:
                    m = run(case)
                except EmuError:
                    continue
                if m.timed_out or self.touches_image(m) or self.ends_badly(m):
                    continue
                got = m.covered_insns(words)
                if got - covered:
                    covered |= got
                    corpus.append(case)
        if corpus and not self.late():
            self.repair_lost(name, entry, words, end, calls, limit, rng, pool, known, covered, corpus, extras, reach,
                             edge, hung)
        if len(corpus) + len(extras) < 4:
            raise GenerationError("%s: only %d of %d generated inputs ran to the end (%d faulted); write cases"
                                  % (name, len(corpus) + len(extras), budget, faults))
        cases = self.settle_hangs(entry, self.drop_bad_ends(entry, corpus + extras))
        cases, self.wild_pins = self.drop_wild(entry, words, cases, limit)
        return cases

    def decider(self, words, covered, calls, taken):
        """The callee called directly from words whose call stands nearest in front of an instruction no
        input ran, and that no input stubs yet: stubbed in later inputs, its result becomes an input.
        A real callee answers one way: newlib's _read stub always fails, so _read_r's code for a read
        that succeeded stayed uncovered. Not a function that never returns. None when there is none."""
        missed = sorted(set(words) - covered)
        stubbed = {c[0] for c in calls} | set(taken)
        best = None
        for a in words:
            insn = self.program.insn(a)
            if insn is None:
                continue
            kind, target, _ = self.program.arch.flow(insn)
            if kind != "call" or not isinstance(target, int):
                continue
            callee = self.program.by_entry.get(target & ~1)
            if callee is None or callee.entry in stubbed or callee.name in self.endless():
                continue
            later = [x for x in missed if x > a]
            if later and (best is None or later[0] - a < best[0]):
                best = (later[0] - a, callee)
        return best[1] if best else None

    def left_through(self, m, words):
        """The function a run that ended elsewhere left the tested one for: the destination of the
        call or jump that ends the last block it ran inside it (m.last_block); None when that is
        not a direct call or jump."""
        import bisect
        last = getattr(m, "last_block", None)
        if not last:
            return None
        words = sorted(words)               # (a lifted Body lists its own instructions first)
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

    def pointer_tails_reached(self, words):
        """The tail calls through a pointer (dread.switches.pointer_tail) among the sites drop_wild
        looks at for these instructions (theirs and their callees', CALLEE_DEPTH), sorted."""
        sites = self.program.icall_sites()
        if not sites:
            return []
        reach = set(words).union(*(f.insns for f in self.callees(words)))
        arch = self.program.arch
        return sorted(a for a in reach & set(sites)
                      if self.program.insn(a) is not None and arch.flow(self.program.insn(a))[0] == "ijump")

    def loop_stubs(self, words, calls=()):
        """(calls, stops) of a loop case of these instructions: every callee they call directly
        stubbed (with the argument registers its callers set up, and its stack arguments: what a
        callee stores behind a pointer passed on the stack is a fill of its stub too, FreeRTOS's
        xTaskGenericNotify's previous value), after the given calls; those that never return as stops
        (their call ends the run). dread.sysrecord makes its loop cases so."""
        calls = [list(c) for c in calls]
        have, stops = {c[0] for c in calls}, []
        for fn in self.callees(words, depth=1):
            if fn.entry in have:
                continue
            if fn.name in self.endless():
                stops.append(fn.entry)
            else:
                calls.append([fn.entry, self.reads_mask(fn.name), self.stack_words(fn)])
        return calls, stops

    def stack_words(self, fn):
        """How many stack arguments a function reads (dread.lift's stack analysis), 0 where unknown."""
        from dread import lift
        try:
            lifter = self.__dict__.get("_frame_lifter") or lift.make_lifter(self.program, self.flow() or None)
            self.__dict__["_frame_lifter"] = lifter
            return min(lifter.stack_args(fn) or 0, 8)
        except Exception:
            return 0

    def loop_calls_for(self, words):
        """The stub calls a loop case of these instructions ends at: twice their call sites, at least
        LOOP_CALLS and at most LOOP_CALLS_MAX (a round of the loop makes each call once, mostly)."""
        arch = self.program.arch
        sites = 0
        for a in words:
            insn = self.program.insn(a)
            if insn is not None and arch.flow(insn)[0] in ("call", "icall"):
                sites += 1
        return max(LOOP_CALLS, min(LOOP_CALLS_MAX, 2 * sites))

    def fragile(self, entry, case, limit=200_000):
        """Why a case's outcome belongs to the original's frames, or None (dread.fragile: it changes
        with the stack 64 bytes lower, or the input made code touch a saved register). The lift check
        refuses such a case when its images differ, and then it covers nothing: FreeRTOS's
        prvNotifyQueueSetContainer copies an item of the queue set's size from its own local, and
        every case of generator 33 that reached three of its instructions had a size past the local."""
        from dread import fragile

        def setup(m):
            self.apply(m, case)
        m = self.machine()
        try:
            setup(m)
            first = m.call(entry, *case["args"], max_instructions=limit)
        except Exception:                   # (no outcome to compare: the lift check checks none)
            return None
        if fragile.stack_moves(self.machine, self.layout, setup, entry, case["args"], limit, first):
            return "its outcome changes with the stack %d bytes lower" % fragile.STACK_SHIFT
        return fragile.saves_touched(self.machine, self.program, self.layout, setup, entry, case["args"], limit)

    def sturdy(self, entry, words, cases, limit=200_000, want=None, reach=None):
        """(the instructions of want (default: all) that cases whose outcome is not the original's
        frames' reach, {index: why} of the fragile cases checked). A case is checked only while it
        reaches an instruction of want that no sturdy case checked so far reaches, the widest first:
        most functions need a check of the cases that stand for something, and none has frames
        enough to need more. reach: each case's coverage, when the caller has it."""
        if reach is None:
            reach = [self.coverage(entry, words, c, c.get("limit", limit)) for c in cases]
        want = set(words) if want is None else set(want)
        firm, frail = set(), {}
        for i in sorted(range(len(cases)), key=lambda i: -len(reach[i] & want)):
            if not (reach[i] & want) - firm:
                continue
            why = self.fragile(entry, cases[i], cases[i].get("limit", limit))
            if why:
                frail[i] = why
            else:
                firm |= reach[i] & want
        return firm, frail

    def firm_up(self, entry, words, cases, limit=200_000):
        """Cases to add for the instructions only fragile cases run (Generator.fragile; the lift check
        refuses those when the images differ, and then nothing runs the instructions): each such
        case, the one that runs most of them first, with one input at a time (an argument, a memory
        cell, a callee's answer) set to each of FIRM_VALUES in turn; a variant that runs some of them as a case
        must and is not fragile is added, marked "firm". A fragile case mostly has one input past the
        frame it is meant for (a queue's item size larger than the item copied from a local:
        FreeRTOS's prvNotifyQueueSetContainer), and that input set small gives the path without the
        frame. At most FIRM_RUNS runs; loop cases are left alone (their runs end at a stub call, with
        no outcome the checks compare)."""
        if any(c.get("stub_calls") for c in cases):
            return []
        reach = [self.coverage(entry, words, c, c.get("limit", limit)) for c in cases]
        firm, frail = self.sturdy(entry, words, cases, limit, reach=reach)
        need = (set().union(*reach) if reach else set()) - firm
        out, runs = [], [FIRM_RUNS]

        def variants(base):
            for kind in ("args", "mem", "returns"):
                for k in range(len(base.get(kind, ()))):
                    for v in FIRM_VALUES:
                        now = base[kind][k][2] if kind == "mem" else base[kind][k]
                        if now != v:
                            trial = json.loads(json.dumps(base))
                            if kind == "mem":
                                trial[kind][k][2] = v
                            else:
                                trial[kind][k] = v
                            yield trial
        for i in sorted(frail, key=lambda i: -len(reach[i] & need)):
            for trial in variants(cases[i]):
                if not need or not reach[i] & need or runs[0] <= 0:
                    break
                runs[0] -= 1
                got, why = self.attempt(entry, words, trial, trial.get("limit", limit), check_image=True)
                if why is None and got & need and not self.fragile(entry, trial, trial.get("limit", limit)):
                    need -= got
                    out.append(dict(trial, firm=True))
        return out

    def carried(self, entry, words, cases, old, limit=200_000):
        """The cases of an earlier generation (old: what the cache held under another key) that still
        run as a case must and run instructions none of cases runs, the most new ones first, each
        marked "carried". A change of the generator or of the image around a function sends its
        search other ways, and a rare path one search found the next may miss: every measurement
        that made the cases anew lost functions it had and gained others (measure-35 and -36, 2 to 7
        lost a build). Those earlier inputs are inputs all the same; the original decides what they
        do. Not carried: a case that stubs an address no function starts at (the image changed
        around it), that does not end as a case must (attempt with the image check), or that calls
        through a register into the middle of a function (drop_wild repairs or drops it).

        An instruction that only fragile new cases run (Generator.fragile: the lift check refuses
        them when the images differ, and then they cover nothing) counts as not run: an old case
        that runs it and is not fragile itself is carried too (measure-38: generator 33's cases of
        prvNotifyQueueSetContainer reached three instructions with a queue item past a local only,
        and generator 31's case with an item that fits stayed behind, so the function was lost)."""
        entries = self.program.by_entry
        reach = [self.coverage(entry, words, c, c.get("limit", limit)) for c in cases]
        covered = set().union(*reach) if reach else set()
        pool = []
        for c in old:
            if any(x[0] not in entries and x[0] != self.fake_pointer for x in c.get("calls", ())) or \
                    any(s not in entries for s in c.get("stops", ())):
                continue
            got, why = self.attempt(entry, words, c, c.get("limit", limit), check_image=True)
            if why is None and got:
                pool.append((set(got), {k: v for k, v in c.items() if k != "carried"}))
        out = []
        while pool:
            pool.sort(key=lambda p: -len(p[0] - covered))
            if not pool[0][0] - covered:
                break
            got, c = pool.pop(0)
            covered |= got
            out.append(dict(c, carried=True))
        offered = set().union(*(got for got, _ in pool)) & covered if pool else set()
        if offered:                         # what an old case could stand for, if no new one stands firm
            firm, _ = self.sturdy(entry, words, cases, limit, want=offered, reach=reach)
            need = offered - firm
            for got, c in sorted(pool, key=lambda p: -len(p[0] & need)):
                if not need or not got & need:
                    break
                if self.fragile(entry, c, c.get("limit", limit)):
                    continue
                need -= got
                out.append(dict(c, carried=True))
        if out:
            out, _ = self.drop_wild(entry, words, out, limit)
        return out

    def drop_wild(self, entry, words, cases, limit):
        """(the cases, each whose run calls through a register into the image at no function's entry
        repaired or dropped, (argument indices, memory addresses) of the inputs that gave such an
        address): a generated callback (127, a Thumb pointer into __aeabi_dmul) ran code there that
        the lifted C calls with other values in the other registers (dread.runner refuses such a
        case; dread.emu.Machine.watch_calls). Repaired: those inputs become the pointer stub (a stub
        that logs the call, not arguments it cannot know), kept when the run then calls nothing so
        and ends; the path it reached stays covered. The pins go to the next pass (_generate_all).
        Only where the function or a callee (CALLEE_DEPTH) calls through a register."""
        sites = self.program.icall_sites()
        reach = set(words).union(*(f.insns for f in self.callees(words))) if sites else set()
        if not cases or not reach & set(sites):
            return cases, None
        kept, pin_args, pin_mem = [], set(), set()
        nregs, fake, code = len(self.layout.arg_regs), self.fake_pointer, self.fake_code(entry)
        for case in cases:
            ended, targets = self._wild_run(entry, case, limit, sites)
            if not targets:
                kept.append(case)
                continue
            slots_args = {i for i, v in enumerate(case["args"][:nregs]) if v & ~1 in targets}
            slots_mem = {a for a, n, v in case["mem"] if n == 4 and v & ~1 in targets}
            pin_args |= slots_args
            pin_mem |= slots_mem
            if not slots_args and not slots_mem:
                continue                    # (an address the run computed: dropped)
            repaired = json.loads(json.dumps(case))
            for i in slots_args:
                repaired["args"][i] = code
            for m_ in repaired["mem"]:
                if m_[0] in slots_mem:
                    m_[2] = code
            if all(c[0] != fake for c in repaired.get("calls", ())):
                repaired["calls"] = list(repaired.get("calls", ())) + [[fake, 0, 0]]
            ended, targets = self._wild_run(entry, repaired, limit, sites)
            if ended and not targets:
                kept.append(repaired)
        return kept, ((pin_args, pin_mem) if pin_args or pin_mem else None)

    def fake_code(self, entry):
        """The pointer stub as a value a call through a register goes to: with the instruction set's
        mode bit (Thumb's bit 0: a Cortex-M faults at an even target before a stub runs)."""
        return self.program.arch.start_address(self.fake_pointer, self.program.isa_at(entry))

    def _wild_run(self, entry, case, limit, sites):
        """(whether the case's run ended as a case's must, {targets of its calls into the image at no
        entry}) (drop_wild)."""
        m = self.machine()
        m.plan = []
        m.may_hang = bool(case.get("hang"))
        try:
            self.apply(m, case)
            m.trace_reads(self.image.base, self.image.end, self.code, self.owners)
            m.watch_calls(self.program, sites)
            m.call(entry, *case["args"], max_instructions=case.get("limit", limit))
            ended = not self.touches_image(m) and not self.ends_badly(m)
        except EmuError:
            ended = False
        return ended, {t & ~1 for _, t in getattr(m, "wild_calls", ())}

    def drop_bad_ends(self, entry, cases):
        """The cases without those that should end at a call (stops, loop cases) and end otherwise
        (ends_badly: no common place to compare them). The search keeps them while it runs (one that
        spins in a wait it cannot leave is where the compare-guided stage finds the callee effect
        that ends the wait), the result does not (what only they reached: repair_lost)."""
        return [case for case in cases if self.survives(entry, case)]

    @staticmethod
    def checked_at_end(case):
        """Whether drop_bad_ends runs the case again: one that should end at a call."""
        return bool(case.get("stops") or (case.get("hang") and case.get("stub_calls")))

    def survives(self, entry, case):
        """Whether drop_bad_ends keeps the case: its own run, under its own limit and the spin cut and
        without the search's stall cut and lower limits, ends at its call or by a return."""
        if not self.checked_at_end(case):
            return True
        m = self.machine()
        m.plan = []
        m.spin_limit = SPIN_READS
        self.apply(m, case)
        try:
            m.call(entry, *case["args"], max_instructions=case.get("limit", 200_000))
        except EmuError:
            return False
        return not self.ends_badly(m)

    def repair_lost(self, name, entry, words, end, calls, limit, rng, pool, known, covered, corpus, extras,
                    reach, edge, hung):
        """What only runs of cases that drop_bad_ends drops reached, sought once more. Such runs count
        while the search runs: they move the compare-guided stage's frontier on (where a run waits on
        memory, that stage finds the callee effect that ends the wait: gizmo-rv32cpp's main). But
        when the cases that survive cover less than that, the code only the dropped ones reached was
        left uncovered and no other input sought for it (found on a private firmware: a call behind a
        byte compared with 1, whose one run the search kept should have ended at a call deeper in and
        spun on a device register before it). Then covered becomes what
        the surviving cases cover and the compare-guided stage runs once more, every case of the
        corpus a source, counting only runs whose case survives; a run there that hangs in a callee is
        tried again ending at that call too, also when its case ends at other calls already (that
        run should have ended at the call deeper in and hung before it)."""
        if not any(self.checked_at_end(c) for c in corpus):
            return
        good = set()
        for case in corpus + extras:
            if self.survives(entry, case):
                good |= self.coverage(entry, words, case, case.get("limit", limit))
        if covered <= good:
            return
        covered.clear()
        covered |= good
        for i, case in enumerate(corpus):
            if i not in reach:
                reach[i] = set(self.attempt(entry, words, case, case.get("limit", limit))[0])
        self._solve(name, entry, words, end, calls, limit, rng, pool, known, covered, corpus, reach, edge, hung,
                    strict=True)

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

    def _solve(self, name, entry, words, end, calls, limit, rng, pool, known, covered, corpus, reach, edge,
               hung=(), strict=False):
        """The compare-guided stage (see the module's docstring); adds to corpus and covered. strict
        (repair_lost): a run whose case drop_bad_ends would drop covers nothing, a source only."""
        image, lay = self.image, self.layout
        words_set = set(words)
        solver = random.Random("%s/%d/solve" % (seed_name(entry), SEED))
        width_bytes = self.arch.reg_bits // 8          # a pointer's bytes
        chase_set = set(self.chase_buffers)
        from dread.emu import ARGS_ROOM                 # the room above the stack top where a call's stack
        args_room = (lay.stack_top, lay.stack_top + ARGS_ROOM)  # arguments lie (the case's own words, not chased:
        # clang's __sflush_r popped r8-r11 from words there the chase had set, which its C reads otherwise)
        setters = self.flag_setters(words)
        readers = {at: read for at, read in setters.values()}
        runs, cost, total = [0], [0], [0, 0]
        nregs = len(lay.arg_regs)

        def spent():
            return runs[0] >= SOLVE_RUNS or cost[0] >= SOLVE_COST or \
                total[0] >= 4 * SOLVE_RUNS or total[1] >= 4 * SOLVE_COST or self.late()

        chases = [CHASE_RUNS]

        def attempt(case, watch=(), record=False, steps=CHASE_STEPS):
            runs[0] += 1
            cost[0] += len(case["mem"]) + 50
            total[0] += 1
            total[1] += len(case["mem"]) + 50
            m = self.machine()
            m.plan = []
            self.apply(m, case)
            m.cover(min(words), end)        # (end: cover_span's)
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
                m.call(entry, *case["args"], max_instructions=min(limit, SOLVE_LIMIT),
                       stall=self.stall_for(min(limit, SOLVE_LIMIT)))
            except EmuError:
                if stop_retry(case, m):
                    return attempt(case, watch, record, steps)
                return chase(case, m, watch, record, steps)
            if self.touches_image(m):
                if stop_retry(case, m):
                    return attempt(case, watch, record, steps)
                return chase(case, m, watch, record, steps)
            if strict and self.ends_badly(m) and stop_retry(case, m):     # (a case that ends at a call
                return attempt(case, watch, record, steps)               # comes back cut or spinning)
            known.learn(m.reads)
            m.covered = m.covered_insns(words)
            return m, seen

        def stop_retry(case, m):
            """Whether a run that failed is to be tried once more, ending at a call (the case gets the
            stop): the run hung in a callee, or it called the stub of a callee runs hung in (hung)
            and failed behind it. This stage is where a null check is flipped (cbnz r0 solved to 0),
            and the run then waits in the assertion handler for a debugger, or with the handler
            stubbed goes on into the null pointer: FreeRTOS's vTaskSetTimeOutState had 44 such runs
            and xQueueReceive 19, every one dropped, and their configASSERT paths stayed unrun (since
            generator 32 learns such a pointer at once, the random stage gives it null too rarely)."""
            if (case.get("stops") and not strict) or self.loop or len(case.get("stops", ())) >= MAX_STOPS:
                return False                # (the repair adds a stop to those it has: repair_lost)
            inside = self.program.function_at(m.reg("pc") & ~1) if m.timed_out or (strict and m.spinning) else None
            if inside is not None and inside.entry != entry:
                stop = inside.entry
            else:
                called = [int(x.group(1), 16) for x in (re.match(r"call 0x([0-9a-f]+)", c[0]) for c in m.calls
                                                        if c and isinstance(c[0], str)) if x]
                stop = next((t for t in called if t in hung), None)
            if stop is None or stop in case.get("stops", ()):
                return False
            case.update({"hang": True, "limit": limit, "stops": sorted(set(case.get("stops", ())) | {stop})})
            return True

        def chase(case, m, watch, record, steps):
            """A near miss: a run that reached code no input had reached and then failed, at a fault or
            at a load from or a store into the image, mostly through a pointer no input set (read as 0:
            vListInsert, its item's value solved, came to a list pointer nobody had set). Every word it
            read that no input gave (in neither the image, the stack, the room above it for stack
            arguments nor a peripheral) points to a
            zeroed buffer of its own (Generator.chase_buffers), and the run is tried again: one pointer
            of a chain deeper each step. The words join the case, so that a run that ends is a case
            like any other (the original decides what it does with them)."""
            if steps <= 0 or chases[0] <= 0 or not self.chase_buffers or not m.covered_insns(words) - covered:
                return None, None
            given = {a for a, _, _ in case["mem"]}
            fresh = [a for a, n in sorted(m.reads.items()) if n == width_bytes and a not in given
                     and not image.base <= a < image.end and not self.target.memory.is_io(a)
                     and not args_room[0] <= a < args_room[1]][:CHASE_WORDS]
            # an argument the failing access went through (the access lies just behind its value):
            # a pointer given as a small number, readable flash on a Cortex-M, faulted nowhere and the
            # store through it went into the image (newlib's __sfp, its reent pointer 0x8)
            failed = ([m.unmapped[1]] if getattr(m, "unmapped", None) is not None else []) + self.image_stores(m)
            via = [i for i, v in enumerate(case["args"][:nregs]) if v not in chase_set and
                   any(0 <= a - (v & 0xFFFFFFFF) < POINTER_REACH for a in failed)]
            used = sum(1 for a, n, v in case["mem"] if v in chase_set) + sum(1 for v in case["args"] if v in chase_set)
            if not fresh and not via or used + len(fresh) + len(via) > len(self.chase_buffers):
                return None, None
            chases[0] -= 1
            for k, a in enumerate(fresh):
                case["mem"].append([a, width_bytes, self.chase_buffers[used + k]])
            for k, i in enumerate(via):
                case["args"][i] = self.chase_buffers[used + len(fresh) + k]
            return attempt(case, watch, record, steps - 1)

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

        checks, sourced = [SURVIVE_CHECKS], set()

        def keep(m, case):
            known.learn(m.reads)
            if strict and m.covered - covered and self.checked_at_end(case) and self.ends_badly(m):
                checks[0] -= 1
                if checks[0] < 0 or not self.survives(entry, case):
                    if m.covered - covered - sourced:   # (a source for the attempts after, not a case)
                        sourced.update(m.covered)
                        corpus.append(case)
                        reach[len(corpus) - 1] = set(m.covered)
                    return False
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
                short = min(m.stub_calls[0], max(SOLVE_RETURNS, self.loop_calls if self.loop else 0)) \
                    - len(base["returns"])
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
                    + [("returns", i) for i in range(len(base["returns"]))] \
                    + [("fargs", i) for i in range(len(base.get("fargs", ())))]
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

    # ---- the frontier answers ----

    def decision_sources(self, name, branch):
        """What decides the conditional branch at `branch` of function name, as dread.uncovered traces it
        back through the function, as far as a case can give it, in the trace's order (inputs from outside
        the function first): ("callee", entry) for the result of a direct call, ("load", address) for the
        load at that address of a global, a device register or a field (memory a case can set: where the
        load reads is learned from a run), ("fill", (address, entry)) for the load at that address of a
        local the callee at entry was handed the address of (what a stub stores there is the case's);
        then ("reread", address) for each load: the same memory where the run read it before, changed from
        that read on (a case's later), so that two reads of one word can differ (a loop that reads a
        stream buffer's tail twice and goes round while an interrupt moved it in between).
        [] when there is none or the trace fails (a layer without value_step follows fixed addresses and
        calls only)."""
        cache = self.__dict__.setdefault("_decisions", {})
        if (name, branch) in cache:
            return cache[name, branch]
        out = []
        try:
            from dread import uncovered
            if self._tracer is None:
                self._tracer = uncovered.Context(self.target, self.image, self.program, self.flow() or None)
            flows = self.__dict__.setdefault("_flows", {})
            if name not in flows:
                flows.clear()               # (one function at a time)
                flows[name] = uncovered.Flow(self._tracer, self.program.by_name[name])
            flow = flows[name]
            if branch in flow.insn:
                for source in uncovered.ordered(uncovered.decide(flow, branch)[2]):
                    cls, key, text = source
                    if cls == "callee" and text.startswith("the result of ") and key in self.entries:
                        found = [("callee", self.entries[key])]
                    elif cls == "callee" and " stores in the local " in text and key in self.entries:
                        found = [("fill", (d, self.entries[key])) for d in sorted(flow.loads.get(source, ()))]
                    elif cls in ("global", "device", "field"):
                        found = [("load", d) for d in sorted(flow.loads.get(source, ()))]
                    else:
                        continue
                    out += [s for s in found if s not in out]
            out += [("reread", d) for kind, d in list(out) if kind == "load"]
        except Exception:
            out = []
        cache[name, branch] = out
        return out

    def answers(self, name, entry, words, cases, limit=200_000, loop=False):
        """The frontier answers (see the module's docstring): the cases to add to `cases` (a function's
        generated, kept and written cases: what the lift check runs), made from them. A conditional branch
        that their runs took one way only (from the first ANSWER_BASES cases that got there and give
        something to answer, among the first ANSWER_SCAN: a run that read the deciding word in the image
        gives nothing; at its first ANSWER_VISITS visits) gets what the other way needs, where what decides it is an input a case can
        give (decision_sources): a real callee's result by a stub that answers only the call whose result
        decides (the last call of that callee before the visit; the callee runs at its other calls, so the
        run is the same up to there: the fourth element of a case's calls), a stubbed callee's answer at
        that call, the memory the load that decides read at that visit (not a local, not the image), what
        a stubbed callee stores in a local it was handed (a fill; the case gets the function's frame,
        loop_frame, and a tight fill). The values: the other operand of the compare, one more and one less,
        the operand itself and its neighbours (for a test its bits), solved through a constant offset when
        the first two runs show one, the word's edges, single bits: ANSWER_TRIES of them. A run that gains
        code and ends as a case must is kept; one that hangs in a callee is tried with it stubbed, then
        ending at its call; one that faults or stores into the image is chased (chase); those repairs only
        for the first ANSWER_FLIPS values that went the other way and failed (the next mostly fails alike;
        a single bit can still give a pointer that names another object). The code behind a call at
        which the runs of the first ANSWER_BASES cases that called it ended (at a stop there or deeper in,
        or hung; the callee one that may return), and behind the call an answer's run last left the
        function by where it ends at a stop: the callee answered at every call, else that call answered
        and the callee's next call ending the run, else the first call the run makes after it (behind).
        Over again from the cases it made while one gains, at most
        ANSWER_RUNS runs in all. loop: the function never returns (its cases are loop cases). What is not
        done: a callee that returns its argument untouched (dread.regflow: the branch's source is then the
        argument, not the stub's answer), a real callee's store into a local, a switch's index, a value two
        conditions decide together (a global the function overwrites unless another one equals an
        argument)."""
        before, self.current = self.current, name
        loop_before, self.loop = self.loop, loop
        calls_before, self.loop_calls = self.loop_calls, self.loop_calls_for(words) if loop else LOOP_CALLS
        try:
            return self._answer(name, entry, words, cases, limit)
        finally:
            self.current, self.loop, self.loop_calls = before, loop_before, calls_before

    def _answer(self, name, entry, words, cases, limit):
        words_set = set(words)
        setters = self.flag_setters(words)
        image = self.image
        start, end = cover_span(self.program, words)
        runs = [ANSWER_RUNS]
        names = {fn.entry: fn.name for fn in self.program.functions}
        corpus = [json.loads(json.dumps(c)) for c in cases]
        reach = {}
        for i, case in enumerate(corpus):
            runs[0] -= 1
            got, why = self.attempt(entry, words, case, case.get("limit", limit))
            if why is None:
                reach[i] = set(got)
        covered = set().union(*reach.values()) if reach else set()
        branches = self.conditional_branches(words)

        def edge():
            return {w for w, (target, fall) in branches.items() if w in covered and (
                (target is not None and target in words_set and target not in covered) or
                (fall in words_set and fall not in covered))}

        def run(case, watch=None, loads=()):
            """(machine, events, error) of one run; events: the stub marks ("call", callee, its call
            number, stub calls before it, answered), with watch (branch, decider, reader) the visits of
            the branch's decider ("decide", operands or None, stub calls so far), and the reads of the
            loads at the addresses in loads ("load", its address, address read, size, value, the number of
            that read among the reads that started at that address), in order."""
            runs[0] -= 1
            m = self.machine()
            m.plan = []
            m.spin_limit = SPIN_READS
            m.call_marks = events = []
            m.regions = []                  # (stub call, which pointer, address, bytes): what stubs filled
            self.apply(m, case)
            m.cover(start, end)
            m.trace_reads(image.base, image.end, self.code, self.owners)
            if watch is not None:
                branch, at, read = watch

                def visit(uc, address, size, user):
                    if len(events) < ANSWER_EVENTS:
                        events.append(("decide", read(uc) if read is not None else None, m.stub_calls[0]))
                m.hook_add(UC_HOOK_CODE, visit, begin=at if at is not None else branch,
                           end=at if at is not None else branch)
            if loads:
                armed, pc = [None], self.layout.pc

                def arm(uc, address, size, user):
                    armed[0] = address

                counts = {}                 # address -> the reads that started there so far (as later counts them)

                def on_read(uc, access, address, size, value, user):
                    counts[address] = counts.get(address, 0) + 1
                    if armed[0] is not None and uc.reg_read(pc) & ~1 == armed[0]:
                        if len(events) < ANSWER_EVENTS:
                            events.append(("load", armed[0], address, size,
                                           int.from_bytes(uc.mem_read(address, size), "little"), counts[address]))
                        armed[0] = None
                for a in sorted(loads):
                    m.hook_add(UC_HOOK_CODE, arm, begin=a, end=a)
                m.hook_add(UC_HOOK_MEM_READ, on_read)
            try:
                m.call(entry, *case["args"], max_instructions=case.get("limit", limit),
                       stall=self.stall_for(case.get("limit", limit)))
            except EmuError as e:
                return m, events, e
            return m, events, None

        width, chase_set = self.arch.reg_bits // 8, set(self.chase_buffers)
        from dread.emu import ARGS_ROOM
        args_room = (self.layout.stack_top, self.layout.stack_top + ARGS_ROOM)
        nregs = len(self.layout.arg_regs)

        def first_reads(case):
            """{address: the word first read there} of a run of case, outside the stack and the image."""
            runs[0] -= 1
            m = self.machine()
            m.plan = []
            m.spin_limit = SPIN_READS
            self.apply(m, case)
            out = {}

            def on_read(uc, access, address, size, value, user):
                if size == width and address not in out and not self.layout.in_stack(address) and \
                        not image.base <= address < image.end:
                    out[address] = int.from_bytes(uc.mem_read(address, size), "little")
            m.hook_add(UC_HOOK_MEM_READ, on_read)
            try:
                m.call(entry, *case["args"], max_instructions=case.get("limit", limit),
                       stall=self.stall_for(case.get("limit", limit)))
            except EmuError:
                pass
            return out

        def chase(case, m, keep=None):
            """A run that gained code and then faulted or stored into the image, through a pointer no
            input set or one an input gave as a small number: the cases to try, in order, in which such
            words point to zeroed buffers of their own (as the compare-guided stage's chase). First one with
            the words the run read that no input gave whose value lies just in front of a failing access;
            without those, one for each argument and given word (not keep, the answered one) whose value
            does, the nearest first (CHASE_TRIES; a zero argument lies in front of a store at 8 too, and
            pointed elsewhere it changes the path); else one with the words no input gave, as that chase
            takes them."""
            given = {a for a, _, _ in case["mem"]}
            failed = ([m.unmapped[1]] if getattr(m, "unmapped", None) is not None else []) + self.image_stores(m)

            def distance(v):
                d = [(a - (v & 0xFFFFFFFF)) for a in failed if 0 <= a - (v & 0xFFFFFFFF) < POINTER_REACH]
                return min(d) if d and v not in chase_set else None

            seen = first_reads(case)        # (the values as read: a word may be written after)

            def now(a):
                return seen.get(a)
            fresh = [a for a, n in sorted(m.reads.items()) if n == width and a not in given
                     and not image.base <= a < image.end and not self.target.memory.is_io(a)
                     and not args_room[0] <= a < args_room[1]]
            used = sum(1 for _, _, v in case["mem"] if v in chase_set) + sum(1 for v in case["args"] if v in chase_set)
            free = iter(self.chase_buffers[used:])

            def pointed(words=(), arg=None, cell=None):
                out = json.loads(json.dumps(case))
                try:
                    for a in words:
                        out["mem"].append([a, width, next(free)])
                    if arg is not None:
                        out["args"][arg] = next(free)
                    if cell is not None:
                        out["mem"][cell][2] = next(free)
                except StopIteration:
                    return None
                return out
            pointing = [a for a in fresh if distance(now(a)) is not None][:CHASE_WORDS]
            if DEBUG:
                print("  chase: failed %s pointing %s near %s" % ([hex(a) for a in failed], [hex(a) for a in pointing],
                      sorted((distance(v), i) for i, v in enumerate(case["args"][:nregs]) if distance(v) is not None)
                      + sorted((distance(v), hex(a)) for a, n, v in case["mem"] if n == width and a != keep
                               and a in m.reads and distance(v) is not None)[:6]))
            if pointing:
                return [c for c in [pointed(pointing)] if c is not None]
            near = [(distance(v), "arg", i) for i, v in enumerate(case["args"][:nregs])] + \
                [(distance(v), "cell", k) for k, (a, n, v) in enumerate(case["mem"])
                 if n == width and a != keep and a in m.reads]
            near = sorted((d, kind, i) for d, kind, i in near if d is not None)[:CHASE_TRIES]
            if near:
                return [c for c in (pointed(arg=i) if kind == "arg" else pointed(cell=i) for _, kind, i in near)
                        if c is not None]
            return [c for c in [pointed(fresh[:CHASE_WORDS])] if fresh and c is not None]

        def stopped(m):
            """The callee whose stop ended the run, or None."""
            if getattr(m, "stopped_at", None) != "never returns":
                return None
            found = re.match(r"call 0x([0-9a-f]+), never returns", (m.calls[-1] or ("",))[0]) if m.calls else None
            return int(found.group(1), 16) if found else None

        def gained(case, m, error, depth=0, keep=None, spare=None):
            """Whether the run gained code and ended as a case must; the case is kept then. A run that hung
            in a callee is run again with that callee stubbed, then ending at its call; one that faulted
            or stored into the image is chased (CHASE_STEPS); one that ended at a stop whose callee may
            return is run once more with that callee stubbed, for the code behind the call."""
            if not m.covered_insns(words) - covered or runs[0] <= 0 or depth > CHASE_STEPS:
                return False
            spare = [CHASE_TRIES * 2] if spare is None else spare     # (runs the repairs of one trial take at most)
            if error is not None and m.timed_out:
                inside = self.program.function_at(m.reg("pc") & ~1)
                stops = case.get("stops", ())
                if inside is None or inside.entry == entry or self.loop:
                    return False
                tries = []
                if inside.name not in self.endless() and all(c[0] != inside.entry for c in case.get("calls", ())):
                    tries.append(dict(case, calls=[list(c) for c in case.get("calls", ())] +
                                      [[inside.entry, self.reads_mask(inside.name), 0]]))
                if inside.entry not in stops and len(stops) < MAX_STOPS:
                    tries.append(dict(case, hang=True, limit=limit, stops=sorted(set(stops) | {inside.entry})))
                for trial in tries:
                    if spare[0] <= 0:
                        break
                    spare[0] -= 1
                    m2, _, error2 = run(trial)
                    if gained(trial, m2, error2, depth + 1, keep, spare):
                        return True
                return False
            if error is not None or self.touches_image(m):
                for chased in chase(case, m, keep):
                    if runs[0] <= 0 or spare[0] <= 0:
                        break
                    spare[0] -= 1
                    m2, _, error2 = run(chased)
                    if DEBUG:
                        print("   chased: %s, touches %s, new %d" % (error2 or "returns", self.touches_image(m2),
                                                                  len(m2.covered_insns(words) - covered)))
                    if gained(chased, m2, error2, depth + 1, keep, spare):
                        return True
                return False
            if self.ends_badly(m) or (self.checked_at_end(case) and not self.survives(entry, case)):
                return False
            got = m.covered_insns(words)
            covered.update(got)
            case = {k: v for k, v in case.items() if k not in READING_MARKS}
            case["answer"] = True
            if case.get("hang") and not self.loop:
                case = self.settle_hangs(entry, [case])[0]
            corpus.append(case)
            reach[len(corpus) - 1] = set(got)
            stop = stopped(m)
            left = self.left_through(m, words) if stop is not None else None      # (the call in words it ended
            if left is not None and may_return(left.entry):                       # behind: the stop may lie deeper)
                behind(case, left.entry, depth + 1)
            return True

        def may_return(stop):
            """Whether a stop's callee may return (not one of dread.noreturn's), so code behind its call can run."""
            callee = self.program.function_at(stop)
            return not self.loop and callee is not None and callee.entry != entry and callee.name not in self.endless()

        def answered_stop(base, stop, k):
            """base with the k-th call of stop (the one its run did not come back from) answered by a stub
            and the callee's next call ending the run (stop_from; stop joins the stops). Its earlier calls
            stay as they were: answered where its stub answered every call, run where the callee ran (the
            stub then answers the k-th alone)."""
            case = json.loads(json.dumps(base))
            case["stops"] = sorted(set(case.get("stops", ())) | {stop})
            case["stop_from"] = [[a, c] for a, c in case.get("stop_from", ()) if a != stop] + [[stop, k + 1]]
            calls = [list(c) for c in case.get("calls", ())]
            mine = next((c for c in calls if c[0] == stop), None)
            if mine is None:
                calls.append([stop, self.reads_mask(names.get(stop, "")), 0] + ([[k]] if k > 1 else []))
            elif len(mine) > 3 and mine[3] is not None:
                mine[3] = sorted(set(mine[3]) | {k})
            case["calls"] = calls
            return case

        def ending_call(base, callee):
            """Which call of callee base's run did not come back from (its last; 1 for a stop of every call,
            stop_from's number for a later one), or None where the run did not call it: counted in a run
            with a stub that answers none of its calls where base has no stub of it."""
            if callee in base.get("stops", ()):
                return dict((a, k) for a, k in base.get("stop_from", ())).get(callee, 1)
            counted = json.loads(json.dumps(base))
            if not any(c[0] == callee for c in counted.get("calls", ())):
                counted["calls"] = counted.get("calls", []) + [[callee, self.reads_mask(names.get(callee, "")), 0, []]]
            _, events, _ = run(counted)
            seen = [e[2] for e in events if e[0] == "call" and e[1] == callee]
            return max(seen) if seen else None

        def behind(base, stop, depth):
            """The code behind the call of stop (a callee that may return) at which base's run ended, there
            or at a stop deeper in (an assertion handler that waits for a debugger, or one that prints first
            and waits in the UART's output: FreeRTOS's RISC-V demo), in three tries, the first that gains
            kept: the callee answered at every call, where that call is its first (code only a run that
            gets past each of its calls reaches: GCC -O0's prvBlockOnNotificationsComingFromInterrupts
            returns after nine rounds with two checks each); that call answered and the callee's next call
            ending the run instead (answered_stop); where that fails too (the run goes on into what the
            check in front of the call guards, a null pointer), the first call it makes after the answered
            one ending it, so that the code up to there is compared. Whether one gained. (Until 2026-10-26
            only the first, for the answers' own runs that ended at the callee itself, and runs that went on
            faulted: FreeRTOS's clang -O2 queue functions kept the instruction behind their first
            configASSERT unrun.)"""
            k = ending_call(base, stop)
            if not k or runs[0] <= 0:
                return False
            if k == 1:
                every = json.loads(json.dumps(base))
                every["stops"] = [s for s in every.get("stops", ()) if s != stop]
                every["stop_from"] = [[a, c] for a, c in every.get("stop_from", ()) if a != stop]
                if not every["stop_from"]:
                    del every["stop_from"]
                every["calls"] = [list(c) for c in every.get("calls", ()) if c[0] != stop] + \
                    [[stop, self.reads_mask(names.get(stop, "")), 0]]
                m1, _, error1 = run(every)
                if gained(every, m1, error1, depth):
                    return True
                if runs[0] <= 0:
                    return False
            trial = answered_stop(base, stop, k)
            have = {c[0] for c in trial["calls"]}
            watched = dict(trial, calls=trial["calls"] + [[c, self.reads_mask(names.get(c, "")), 0, []]
                                                          for c in sorted(direct - have)])   # (counted only)
            m2, events, error2 = run(watched)
            if gained(trial, m2, error2, depth):
                return True
            if runs[0] <= 0:
                return False
            at = next((i for i, e in enumerate(events) if e[0] == "call" and e[1] == stop and e[2] == k), None)
            nxt = next((e for e in events[at + 1:] if e[0] == "call"), None) if at is not None else None
            if nxt is None or nxt[1] in (entry, self.fake_pointer):
                return False
            _, x, kx = nxt[:3]
            ended = json.loads(json.dumps(trial))
            ended["stops"] = sorted(set(ended.get("stops", ())) | {x})
            ended["stop_from"] = [[a, c] for a, c in ended["stop_from"] if a != x] + ([[x, kx]] if kx > 1 else [])
            m3, _, error3 = run(ended)
            return gained(ended, m3, error3, depth)

        def renumber(case, after):
            """A stub call inserted as number after + 1: the effects and fills of later calls move on."""
            for key, at in (("effects", 0), ("fills", 0)):
                for item in case.get(key, ()):
                    if item[at] > after:
                        item[at] += 1

        def maker(base, kind, key, events, k, m=None):
            """(make(value) -> case, the input's value in base or None, the address of the memory it sets
            or None) for the source (kind, key) at the decider's visit events[k], or (None, None, None)."""
            if kind == "reread":            # the memory a load read again: from that read on (later)
                last = next((e for e in reversed(events[:k]) if e[0] == "load" and e[1] == key), None)
                if last is None or last[5] < 2:
                    return None, None, None
                _, _, address, size, old, nth = last
                if size not in (1, 2, 4) or self.layout.in_stack(address) or \
                        image.base <= address < self.program.writable_end():
                    return None, None, None

                def make(v):
                    case = json.loads(json.dumps(base))
                    case["later"] = [c for c in case.get("later", ()) if c[:3] != [address, size, nth]] + \
                        [[address, size, nth, v & ((1 << 8 * size) - 1)]]
                    return case
                return make, old, address
            if kind == "load":              # the memory the load read last before the visit
                last = next((e for e in reversed(events[:k]) if e[0] == "load" and e[1] == key), None)
                if last is None:
                    return None, None, None
                _, _, address, size, old = last[:5]
                if size not in (1, 2, 4) or self.layout.in_stack(address) or \
                        image.base <= address < self.program.writable_end():
                    return None, None, None     # (a local, or the image: no input there)

                def make(v):
                    case = json.loads(json.dumps(base))
                    case["mem"] = [c for c in case["mem"] if c[:2] != [address, size]] + \
                        [[address, size, v & ((1 << 8 * size) - 1)]]
                    return case
                return make, old, address
            if kind == "fill":              # a local a stub was handed: what it stores there
                last = next((e for e in reversed(events[:k]) if e[0] == "load" and e[1] == key[0]), None)
                if last is None or m is None:
                    return None, None, None
                _, _, address, size, old = last[:5]
                region = next((r for r in reversed(m.regions) if r[2] <= address and address + size <= r[2] + r[3]),
                              None)
                if size not in (1, 2, 4) or region is None:
                    return None, None, None
                number, which, at = region[0], region[1], address - region[2]

                def make(v):
                    case = json.loads(json.dumps(base))
                    case["fills"] = [f for f in case.get("fills", ()) if f[:3] != [number, which, at]] + \
                        [[number, which, at, size, v & ((1 << 8 * size) - 1)]]
                    return case
                return make, old, None
            last = next((e for e in reversed(events[:k]) if e[0] == "call" and e[1] == key), None)
            if last is None:
                return None, None, None
            _, _, n, before, answered = last
            stub = next((c for c in base.get("calls", ()) if c[0] == key), None)
            if answered:                    # a stubbed callee: its answer at that call
                returns = list(base.get("returns", ())) + [0] * max(0, before + 1 - len(base.get("returns", ())))
                old = returns[before]

                def make(v):
                    case = json.loads(json.dumps(base))
                    case["returns"] = returns[:before] + [v & 0xFFFFFFFF] + returns[before + 1:]
                    return case
                return make, old, None

            def make(v):                    # a callee that runs: answered at that call only
                case = json.loads(json.dumps(base))
                for c in case["calls"]:
                    if c[0] == key:
                        while len(c) < 4:
                            c.append(0 if len(c) == 2 else [])
                        c[3] = sorted(set(c[3] or ()) | {n})
                if stub is None:
                    case["calls"].append([key, self.reads_mask(names.get(key, "")), 0, [n]])
                returns = list(case.get("returns", ())) + [0] * max(0, before - len(case.get("returns", ())))
                case["returns"] = returns[:before] + [v & 0xFFFFFFFF] + returns[before:]
                renumber(case, before)
                return case
            return make, None, None

        def values(operands, old, probes):
            """The values to try, in order, given the decider's operands in the base run (a, b, kind or
            None), the input's old value (or None) and the runs so far [(value, operands)]."""
            out = []
            if operands is not None:
                a, b, kind = operands
                if kind == "test":
                    out += [b, 0, a ^ b, ~a, a | b, a & ~b]
                else:
                    out += [b + 1, b - 1, b, a + 1, a - 1, a]
            out += [0, 1, 0xFFFFFFFF, 2, 0x80000000, 0x7FFFFFFF]
            if len(probes) >= 2 and probes[0][1] is not None and probes[1][1] is not None:
                (v1, (a1, b1, kind)), (v2, (a2, b2, _)) = probes[:2]
                step = (v2 - v1) & 0xFFFFFFFF
                if kind == "compare" and step:
                    for side, other, d in ((a1, b1, (a2 - a1) & 0xFFFFFFFF), (b1, a1, (b2 - b1) & 0xFFFFFFFF)):
                        if d == step:       # operand = value + offset: solved for the other operand's
                            out = [other - side + v1 + e for e in (0, 1, -1)] + out
                        elif d == (-step) & 0xFFFFFFFF:     # operand = offset - value
                            out = [side + v1 - other + e for e in (0, -1, 1)] + out
            bits = [1 << i for i in range(32)]
            out += [old ^ x for x in bits] if old is not None else bits
            seen, ordered = {old} if old is not None else set(), []
            for v in out:
                v &= 0xFFFFFFFF
                if v not in seen:
                    seen.add(v)
                    ordered.append(v)
            return ordered

        def answer(w, base, kind, key):
            """Answers for the branch w from corpus case base through one source: whether one gained, None
            where base gives nothing to answer (its run does not get there, or reads the deciding word in the
            image: a pointer given as a small number on a memory map whose flash starts at 0)."""
            at, read = setters.get(w, (None, None))
            case = json.loads(json.dumps(base))
            loads = (key,) if kind in ("load", "reread") else (key[0],) if kind == "fill" else ()
            if kind == "fill":              # (a stubbed callee: with the function's frame its stub fills the local,
                if all(c[0] != key[1] or len(c) > 3 for c in case.get("calls", ())):    # up to the first word the
                    return None                                                        # function stored: tight)
                if case.get("frame", 0) < self.loop_frame(name):
                    case.update(frame=self.loop_frame(name), tight=True)
            if kind == "callee":
                if key == entry or key in self.endless() or key in self.writing or key == self.fake_pointer:
                    return None
                if not any(c[0] == key for c in case.get("calls", ())):
                    case["calls"] = [list(c) for c in case.get("calls", ())] + \
                        [[key, self.reads_mask(names.get(key, "")), 0, []]]       # (counted, never answered)
            m, events, error = run(case, (w, at, read), loads)
            if error is not None and not case.get("stops") and not case.get("hang"):
                return None
            visits = [k for k, e in enumerate(events) if e[0] == "decide"][:ANSWER_VISITS]
            usable = False
            for k in visits:
                make, old, keep = maker(case, kind, key, events, k, m)
                if make is None:
                    continue
                usable = True
                nth = sum(1 for e in events[:k + 1] if e[0] == "decide")
                probes, tried, flips = [], 0, 0
                while tried < ANSWER_TRIES and runs[0] > 0 and not self.late():
                    order = [v for v in values(events[k][1], old, probes) if v not in {p[0] for p in probes}]
                    if not order:
                        break
                    v = order[0]
                    trial = make(v)
                    tried += 1
                    m2, events2, error2 = run(trial, (w, at, read))
                    seen = [e[1] for e in events2 if e[0] == "decide"]
                    probes.append((v, seen[nth - 1] if len(seen) >= nth else None))
                    if DEBUG:
                        print("  %s %s visit %d: 0x%x -> %s, %s, new %d" % (
                            kind, key, nth, v, probes[-1][1], error2 or "returns",
                            len(m2.covered_insns(words) - covered)))
                    if gained(trial, m2, error2, keep=keep, spare=[0] if flips >= ANSWER_FLIPS else None):
                        return True
                    flips += bool(m2.covered_insns(words) - covered)    # (it went the other way, and failed)
            return False if usable else None

        direct = {fn.entry for fn in self.callees(words, depth=1)} - {entry, self.fake_pointer}
        sites = {}                          # a call in words -> (its callee, the instruction behind it)
        for a in words:
            insn = self.program.insn(a)
            if insn is None:
                continue
            kind, target, _ = self.program.arch.flow(insn)
            callee = self.program.by_entry.get(target & ~1) if kind == "call" and isinstance(target, int) else None
            if callee is not None and a + self.program.insn_size(a) in words_set:
                sites[a] = (callee.entry, a + self.program.insn_size(a))
        tried, progress = set(), True
        while progress and runs[0] > 0 and not words_set <= covered and not self.late():
            progress = False
            # The code behind a call at which every run that got there ended (at a stop there or deeper
            # in, or hung: configASSERT's handler waits for a debugger, the RISC-V demo's in the UART's
            # output first), from the first ANSWER_BASES such cases whose runs called it (behind)
            for a, (stop, back) in sorted(sites.items()):
                if back in covered or runs[0] <= 0 or self.late() or not may_return(stop):
                    continue
                for i in [i for i in sorted(reach) if a in reach[i] and
                          (corpus[i].get("stops") or corpus[i].get("hang"))][:ANSWER_BASES]:
                    if ("behind", a, i) in tried or runs[0] <= 0:
                        continue
                    tried.add(("behind", a, i))
                    if DEBUG:
                        print("behind 0x%x: base %d" % (a, i))
                    if behind(corpus[i], stop, 1):
                        progress = True
                        break
            for w in sorted(edge()):
                if runs[0] <= 0 or self.late() or words_set <= covered:
                    break
                if w not in edge():
                    continue
                sources = self.decision_sources(name, w)
                bases = [i for i in sorted(reach) if w in reach[i]][:ANSWER_SCAN]
                if DEBUG:
                    print("answer 0x%x: sources %s, bases %s" % (w, sources, bases))
                if not sources:
                    continue
                done, used = False, 0
                for i in bases:             # (ANSWER_BASES of them that give something to answer)
                    if used >= ANSWER_BASES:
                        break
                    usable = False
                    for kind, key in sources:
                        if (w, i, kind, key) in tried or runs[0] <= 0:
                            continue
                        tried.add((w, i, kind, key))
                        got = answer(w, corpus[i], kind, key)
                        if got:
                            progress = done = True
                            break
                        usable |= got is not None
                    if done:
                        break
                    used += usable
        return corpus[len(cases):]


class Cells(dict):
    """The memory locations the generator gives values to: {address: size}, at most MAX_CELLS, none
    overlapping another. Every input writes its cells one after the other, so overlapping ones wrote
    over most of each other's bytes, and a value the search gave the first did not survive: learned
    pointers are put one to three bytes off now and then, a run then read the same buffer at new
    addresses, and FreeRTOS's vListInsert had cells at r1, r1+1, r1+2 and r1+3, the item's value
    reading 0x08000000 whatever the compare-guided stage set (its portMAX_DELAY path stayed unrun;
    half of a build's functions had such inputs)."""

    def __init__(self):
        super().__init__()
        self._owner = {}                    # byte address -> the address of the cell holding it

    def full(self):
        return len(self) >= MAX_CELLS

    def add(self, a, n):
        """Know the n bytes at a as a cell: left out where it overlaps a known cell it does not hold
        whole (a word one byte off: its bytes are that cell's), unless it is aligned (its address a
        multiple of its size) and those cells are not: then, and for the known cells it holds whole
        (a byte read first, then the word around it), in their place. A learned pointer is put one
        to three bytes off now and then; when the first run to reach a buffer read it so, its cells
        were misaligned for good, and the aligned runs read half a cell and half nothing (an
        object's vtable pointer never became a whole input word: C++ on RISC-V)."""
        if self.get(a, 0) >= n:
            return
        over = {self._owner[b] for b in range(a, a + n) if b in self._owner}
        partial = [o for o in over if o < a or o + self[o] > a + n]
        if partial and (a % n or any(o % self[o] == 0 for o in partial)):
            return
        if len(self) - len(over) >= MAX_CELLS:
            return
        for o in over:
            for b in range(o, o + self.pop(o)):
                del self._owner[b]
        self[a] = n
        for b in range(a, a + n):
            self._owner[b] = a

    def learn(self, reads):
        for a, n in reads.items():
            if len(self) >= MAX_CELLS:
                return
            self.add(a, n)


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


def reaches(program):
    """{name: (callees, open)} for every function of a program: what a run of the function can enter
    besides itself, as far as the cache key of its generated cases goes (Generator.scopes).

    callees: the functions its instructions transfer to directly: calls, jumps to another function's
    instructions (a tail call, a branch into shared code), the last instruction's fall to the code behind
    it (a call there whose stub returns, a hung callee's, falls through), a call the layer marks as one
    through a stub or of a switch helper (Arm.mark_call), the callees the layer finds held in a register
    (register_call_targets: MSP430), and the targets outside the function of an indirect jump whose
    dispatch dread.switches runs (a table of functions in the image, a veneer).

    open: a transfer may go where none of that tells: a call through a register (a function pointer, a
    virtual method), an indirect jump whose targets no run of its dispatch finds, a transfer to code no
    function holds (also the fall behind the last instruction), an indirect jump in code that runs outside
    the image or whose targets are words the startup copies put outside it (a table a case may set: a
    run's reads outside the image are inputs), a target at address 0 (what a dispatch through memory
    that holds nothing finds). A system call is no transfer (the layer's is_syscall: dread.emu runs it as
    a logged no-op), nor is an indirect jump through a register a call leaves its return address in (the
    layer's link_regs) when the function writes that register nowhere and every direct transfer into it
    is a call that leaves its return address there: GCC's millicode, entered with jal t0, __riscv_save_N,
    returns with jr t0 behind its call (on four of FreeRTOS's RISC-V builds, open, it opened a seventh of
    the trees).

    Not seen: a return address a run overwrote (a stub's generated fill reaching over the frame), which
    may send it anywhere; a dispatch that adds a base to a word it read outside the image."""
    from dread import switches
    arch = program.arch
    links = set(getattr(arch, "link_regs", ()) or ())
    if getattr(arch, "link_reg", None):
        links.add(arch.link_reg)
    syscall = getattr(arch, "is_syscall", None)
    through = getattr(arch, "register_call_targets", None)
    lo, hi = program.base, program.base + len(program.data)
    entered = {}                            # name -> [(how, address, the link registers the transfer writes)]
    out, jumps = {}, []
    copied = []                             # (the words the startup copies put outside the image, once)

    def holder(address):
        return program.by_entry.get(address) or program.function_at(address)

    def enter(fn, callees, g, how, address, written=frozenset()):
        if g is not fn:
            callees.add(g.name)
            entered.setdefault(g.name, []).append((how, address, written))

    def dispatched(fn, address, callees):
        """Whether the indirect jump at address goes only where runs of its dispatch land (dread.switches),
        its targets outside fn entered; False when it may go elsewhere."""
        if not lo <= address < hi:
            return False
        try:
            found = switches.resolve(program, fn, address)
        except Exception:
            return False
        if not found or not (found[0] or found[1]):
            return False
        if not copied:
            copied.append(_copied_words(program))
        if any(t in copied[0] or t | 1 in copied[0] for t in list(found[0]) + list(found[1])):
            return False
        for t in found[1]:
            g = holder(t & ~1)
            if g is None or not t & ~1:
                return False
            enter(fn, callees, g, "jump", t & ~1)
        return True

    for fn in program.functions:
        own, callees, opened, last = set(fn.insns), set(), False, None
        held = {}
        if through is not None:
            try:
                held = through(program, fn) or {}
            except Exception:
                held = {}
        for a in fn.insns:
            insn = program.insn(a)
            if insn is None:
                continue
            last = insn
            kind, target, falls = arch.flow(insn)
            direct = arch.target(insn)      # (also a bl the layer marks as a call through a stub)
            if direct is not None and direct & ~1 not in own:
                g = holder(direct & ~1)
                if g is None:
                    opened = True
                else:
                    written = frozenset(arch.reg_effects(insn)[1]) & links if links else frozenset()
                    enter(fn, callees, g, "call" if written else "jump", direct & ~1, written)
            if kind == "icall":
                if held.get(a):
                    for t in held[a]:
                        g = program.by_entry.get(t)
                        if g is None:
                            opened = True
                        else:
                            enter(fn, callees, g, "call", t)
                elif syscall is None or not syscall(insn):
                    opened = True
            elif kind == "ijump":
                jumps.append((fn, a, target, callees))
        if last is not None:
            kind, _, falls = arch.flow(last)
            before = program.insn(fn.insns[-2]) if len(fn.insns) > 1 else None
            if before is not None and getattr(arch, "delay_slot", None) and arch.delay_slot(before) and \
                    not arch.flow(before)[2]:
                kind, falls = "slot", False  # (the delay slot of a jump or return that does not fall through)
            if kind == "next" or falls:
                g = holder(last.address + last.size)
                if g is None:
                    opened = True
                else:
                    enter(fn, callees, g, "fall", last.address + last.size)
        out[fn.name] = [callees, opened]
    returning = []
    for fn, a, target, callees in jumps:
        if isinstance(target, str) and target in links:
            returning.append((fn, target))  # (decided below, when every transfer into a function is known)
        elif not out[fn.name][1] and not dispatched(fn, a, callees):
            out[fn.name][1] = True
    for fn, reg in returning:
        if out[fn.name][1]:
            continue
        writes = any(insn is not None and reg in arch.reg_effects(insn)[1] for insn in program.instructions(fn))
        if writes or any(how != "call" or address != fn.entry or reg not in written
                         for how, address, written in entered.get(fn.name, ())):
            out[fn.name][1] = True
    return {name: (callees, opened) for name, (callees, opened) in out.items()}


def _copied_words(program):
    """The values of the aligned words the startup's copies put outside the image (reaches: memory a run
    reads there is an input a case may set)."""
    size = program.arch.word
    lo, hi = program.base, program.base + len(program.data)
    out = set()
    for c in program.image.startup():
        if c.kind == "zero" or lo <= c.run < hi:
            continue
        data = bytes(c.data or b"") if c.kind == "load" else bytes(program.data[c.stored - lo:c.stored - lo + c.size])
        skip = -c.run % size
        n = (len(data) - skip) // size if len(data) > skip else 0
        if n:
            out.update(struct.unpack_from("<%d%s" % (n, "I" if size == 4 else "H"), data, skip))
    return out


def components(graph):
    """The strongly connected components of a graph ({node: successors}), each a sorted list, in the order
    Tarjan's algorithm closes them: every component after all the components it reaches. Iterative (a
    call tree may be deep) and deterministic (nodes and successors taken in sorted order)."""
    index, low, on, stack, out = {}, {}, set(), [], []
    for root in sorted(graph):
        if root in index:
            continue
        index[root] = low[root] = len(index)
        stack.append(root)
        on.add(root)
        work = [(root, iter(sorted(graph[root])))]
        while work:
            node, successors = work[-1]
            for nxt in successors:
                if nxt not in graph:
                    continue
                if nxt not in index:
                    index[nxt] = low[nxt] = len(index)
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter(sorted(graph[nxt]))))
                    break
                if nxt in on:
                    low[node] = min(low[node], index[nxt])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    members = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        members.append(w)
                        if w == node:
                            break
                    out.append(sorted(members))
    return out


def cache_key(payload, base, words, sizes, entry, options, arch=None, environment=""):
    """The key of a function's cached cases: its bytes, VERSION, the options, the architecture
    layer's generator_version when it is not 0 (what the generator learns from that architecture's
    code: changing it renews only that architecture's caches) and the environment (the generator's
    environment(NAME): the image as a whole, and the facts of the functions the function's runs can
    reach)."""
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


def cases_for(run, name, fn, make=True):
    """The generated, kept and written cases of a function with auto() for the runner:
    [(label, address, setup, args, limit, case)]. make=False: no generation, only cached cases made
    for the image as it is (dread.callconv measures with what a lift check left)."""
    gen = generator_for(run)
    image = run["image"]
    options = dict(run["suite"].auto[name])
    generate = options.pop("generate", True)    # False: kept and written cases only (dread.liftfuzz's
    calls = resolve_calls(gen, options.pop("calls", ()))        # reduction tries those first)
    frame = options.pop("frame", 0)
    if options.get("loop") and not frame:
        frame = gen.loop_frame(name)        # (in the cache key: only loop functions get new cases)
    prog = gen.program
    if generate is False:
        cached = {"cases": []}
    else:
        cached = _generated(run, name, fn, gen, image, prog, options, calls, frame, make)
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


def case_key(gen, name, fn, options, calls, frame):
    """(key, calls): the cache key of a function's generated cases (cases_for: its auto() options
    without calls and frame, the calls resolved, the frame), and the calls they are made with (the
    stack switches its calls reach added). One place for both: dread.sysrecord looked the cases up
    with a key of its own, which had fallen behind (no switch stubs, sweep cap, pointer tails or loop
    length), found no loop function's cases and judged their coverage by plain cases it made instead."""
    prog = gen.program
    sizes = {a: prog.insn_size(a) for a in fn.insns}
    have = {c[0] for c in calls}
    calls = calls + [c for c in gen.switch_stubs(name) if c[0] not in have]   # (in the key: the cases of a
    keyed = dict(options, calls=calls, frame=frame)                         # function that reaches none stay)
    if len(gen.ladder(fn.insns)) > SWEEP_STEPS:     # (only those: the other functions keep their cases)
        keyed["sweep_steps"] = SWEEP_STEPS
    if options.get("loop") and gen.loop_calls_for(fn.insns) != LOOP_CALLS:      # (only those: the other
        keyed["loop_calls"] = gen.loop_calls_for(fn.insns)                       # loop functions keep theirs)
    if options.get("loop"):                 # (their stubs see their callees' stack arguments since 2026-10-21)
        keyed["loop_stubs"] = 2
    tails = gen.pointer_tails_reached(fn.insns)
    if tails:                               # (only those: tail calls through a pointer count with the
        keyed["pointer_tails"] = tails      # calls through a register since 2026-10-21, drop_wild)
    return cache_key(gen.payload, gen.image.base, fn.insns, sizes, fn.entry, keyed, prog.arch,
                     gen.environment(name)), calls


def _generated(run, name, fn, gen, image, prog, options, calls, frame, make=True):
    """The generated cases of cases_for, from the cache or made and cached (make=False: none when
    the cache holds none for this key)."""
    key, calls = case_key(gen, name, fn, options, calls, frame)
    path = os.path.join(run["target"].cases_dir(image), name + ".json")
    cached = cache_load(path)
    if (not cached or cached.get("key") != key) and not make:
        return {"cases": []}
    if not cached or cached.get("key") != key:
        previous = (cached or {}).get("cases") or []
        started = time.time()
        failed = None
        try:
            made = gen.generate(name, fn.entry, fn.insns, calls=calls, frame=frame, **options)
        except GenerationError as e:
            made, failed = [], str(e)
        if failed is None and previous:     # what the last generation reached stays reached (carried)
            made = made + gen.carried(fn.entry, fn.insns, made, previous)
        if failed is None:                  # what only fragile cases reach, by sturdy ones too (firm_up)
            made = made + gen.firm_up(fn.entry, fn.insns, made)
        cached = {"key": key, "seed": seed_name(fn.entry), "cases": made, "firm": FIRM,
                  "seconds": round(time.time() - started, 2)}      # for reading only; not in the key
        if failed is not None:
            # the search is deterministic: under the same key it fails again (it took the eight
            # slowest functions of a FreeRTOS build's first round, main's loop 80 s, every time).
            # Only a slow failure is kept: a quick one costs little to repeat, and a failure that
            # was not the function's own (an emulator a CPU exception had broken failed every run
            # at once, Core.retire) must not stay for good.
            cached["failed"] = failed
        if failed is None or cached["seconds"] >= FAILURE_SECONDS:
            cache_store(path, cached)
    elif cached.get("firm") != FIRM and not cached.get("failed") and make:
        # cases made before the firm-up (or an earlier one): it is added once, the cases stay
        cached = dict(cached, cases=cached["cases"] + gen.firm_up(fn.entry, fn.insns, cached["cases"]), firm=FIRM)
        cache_store(path, cached)
    if cached.get("failed") and not extra_load(image, name):
        raise GenerationError(cached["failed"])
    return _answered(name, fn, gen, image, cached, path, key, options, calls, frame, make)


def _answered(name, fn, gen, image, cached, path, key, options, calls, frame, make=True):
    """cached with the frontier answers (Generator.answers) behind its cases: made from those and the
    image's kept and written cases of the function (what the lift check runs; a recorded case reaches
    branches no generated one does), and kept in the cache entry under "answers" with a key of their
    own: the generation's key, its firm-up, ANSWERS and those kept and written cases. Made anew when
    that key changes (make=False: left out then). A generation that failed has the written cases to
    answer from (the entry keeps the answers only where it keeps the failure: a quick one is not cached)."""
    failed = cached.get("failed")
    generated = [] if failed else list(cached.get("cases") or [])
    kept, written = kept_load(image, name, gen.real), extra_load(image, name, calls, frame)
    if not generated and not kept and not written:
        return cached
    akey = hashlib.sha1(json.dumps([key, cached.get("firm"), ANSWERS, kept, written], sort_keys=True).encode()
                        ).hexdigest()
    got = cached.get("answers")
    if not got or got.get("key") != akey:
        if not make:
            return cached
        started = time.time()
        made = gen.answers(name, fn.entry, fn.insns, generated + kept + written, loop=bool(options.get("loop")))
        got = {"key": akey, "cases": made, "seconds": round(time.time() - started, 2)}  # (seconds: for reading)
        cached = dict(cached, answers=got)
        if not failed or cached.get("seconds", 0) >= FAILURE_SECONDS:
            cache_store(path, cached)
    return dict(cached, cases=generated + got["cases"])
