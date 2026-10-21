# The method

DREAD turns a firmware image into C, function by function, and proves each step by running the
original and the rebuilt image side by side in an emulator. This page explains the parts, what each
one shows and what it does not, and the order in which to use them. The commands are described in
their own `--help` and docstrings; the file formats in `formats.md`, the target description in
`target.md`.

What it shows is agreement on the inputs tried, in an emulator. It is not a proof that an image is
safe to run on hardware, and nothing here says otherwise.

## 1. The baseline: a listing that rebuilds byte for byte

`dread asm` writes the image as assembly that GNU as rebuilds exactly (`--verify` compares and moves
the instructions as encodes differently into raw words). Every later comparison starts from an image
known to be the original, and every name and annotation lands in the listing.

## 2. The overlay: C linked over the original

`dread recon` compiles the target's C and `dread overlay` links it over the original: each function
where the original was, when it fits (with its literal pools, when nothing else uses them), else in
space other rewritten functions freed, else behind the image with a branch left at the old entry.
Functions not rewritten stay where they are and become symbols that carry their instruction set, so
C calls them by name and the original keeps calling the same addresses. After linking, every byte
that differs from the original must lie in a slot that was handed out; anything else aborts.

C++ works the same way (gizmo-m3cpp with g++, gizmo-rv32cpp with clang++; `docs/target.md`
`cxxflags`). The tables keep the mangled
names, because the linker matches them; `dread functions` and the listing show them demangled
(`dread.cxx`), and `dread functions --families` lists the instances of each template. A method is
rewritten in C++ outside its class, the class described by its layout (the vtable pointer first)
without virtual methods: a virtual method defined in the file would make the compiler emit the
class's vtable there, and the image keeps its own. A function template is rewritten once, with an
explicit instantiation for each instance the image has (weak symbols, taken as defined like any
other). In C, a method is a function named by its mangled name with the object as its first
argument. The vtables stay in the image's data: a method that moves keeps a branch at its entry,
where they point. Static constructors are the startup's business (`.init_array`), which the system
run executes like the rest of the startup.

## 3. The comparison: `dread test`

Each case calls one function in both images with the same registers and memory. They must agree on
the return value, the callee-saved registers, the scratch registers callers read after the call
(section 7), every byte written outside the stack and the device log (every peripheral access in
order). And the cases together must run every instruction of the original function, or the function
is UNCOVERED; code no input can reach is declared with the reason. A case whose images differ is run
once more on the original with the stack starting lower: if the original's own outcome changes, it
took a stack address for a value (a random generator seeded with the address of a local, a pointer
an index pushed past a local array), which C, laying out its frames otherwise, cannot reproduce, and
the case is refused rather than failed; a refused case is not compared and runs nothing for
coverage. So is a case whose images differ where, run again on the original, anything but a push, a
pop, or a save or restore of that register touches a saved register's slot of a frame still on the
stack: the input made code reach the saved registers through a pointer, mostly a length past a local
(a queue whose item size it made larger than the item), and the outcome is the original's frame
layout (decided 2026-10-20, `docs/decisions.md`; checked after the stale data below). When the original read stale stack data, an uninitialised local whose bytes are what
earlier frames left, the original runs again with the stack below the stack pointer filled at every
function entry (twice, with two bytes): an output that changes there too is one the original itself
does not define, and C cannot be held to it (it lays out its frames otherwise and saves other values
in them). When every difference between the images is such an output, the case is compared except
those outputs, which the report names, and counts for coverage (decided 2026-10-20,
`docs/decisions.md`; refused before). FreeRTOS's timer commands carry a union member nobody set into
the timer queue so. A difference the fills do not move keeps the case failed.

## 4. Where inputs come from

- **Generated** (`auto()` in a case file, `dread.autocase`): random inputs, kept when they reach new
  code and mutated further; then inputs at the edge of uncovered code; then a compare-guided search
  that sets the values a branch compares to what its other side needs; then a sweep of boundaries.
  It learns pointers from faults (and points them at buffers, sometimes misaligned), fills buffers
  with text made of the strings the function compares, answers a peripheral that would be polled
  forever with poll stages, and gives at most a few hundred memory locations values per input.
- **Written** cases for what the generator cannot reach.
- **Recorded**: `dread record` from Python models of a function's callees (parsers fed by a reader),
  `dread sysrecord` from a system run (real arguments, real device answers; a call that waits for
  another task or an interrupt replays with what they wrote meanwhile, and handlers are recorded up
  to their exception return).

## 5. Are the cases strong enough? `dread mutate`

The C is changed in small ways (an operator, a constant, a comparison) and every change must make
some case fail. With `--kill` it searches inputs that tell a surviving change apart and adds them as
cases. A survivor that cannot change behaviour is argued in `tests/argued.tsv`; any other means the
cases are too weak.

## 6. Real callees: `dread integration`

Generated inputs run with the real callees in both images, and with `--callers` through the original
functions that call rewritten ones: a difference that the function's own cases could not show (a
register a caller keeps, an access count) shows there.

## 7. What callers rely on: `dread regflow`

A compiler that sees the callee may keep a value in a scratch register across a call when the callee
does not touch it, and hand-written assembly returns values anywhere. `dread regflow` finds, for
every function, the registers its callers read after it returns; the tests compare them, and C that
cannot keep them is refused before anyone writes it.

What regflow reads from the code is checked by measuring: `dread callconv` runs each function's
cases on the original once more for every register with another value in it at the entry, and lists
where what the function reads and changes disagrees with regflow (an input regflow missed, a
register of its caller it reads, one it does not restore). The benchmark runs it on every build.

## 8. The whole firmware: `dread sysrun`

Both images start from reset with models of the devices and run a scenario (input at points of time,
an end condition). Time is a clock over the code both images share, so a device acts at the same
place in both however many instructions the C takes; interrupts are taken behind shared code. The
device logs must agree. This is the test for what no function-level case can call or end: main
loops, reset and interrupt handlers.

## 9. Lifting: everything that can be C, built from C

`dread lift` translates a function to C one instruction at a time: registers as variables, flags
computed exactly, memory accesses volatile and in order, the stack as a local array whose layout
mirrors the original's. `dread lift --check` builds every function that lifts and keeps it only
when its cases agree and run all of it (the system run decides for the functions that never return
and for exception handlers). `dread liftfuzz` checks the translation itself on random instruction
sequences, because cases that cover a function need not tell a wrong translation apart.

Where the C cannot say what the original's code relies on, the translation says it otherwise:

- a frame that must be the real one (a va_list walks from the saved argument registers into the
  stack arguments; an address of a stack argument): an assembly entry reserves the original's frame
  on the real stack and hands the C its stack pointer (ARM state, Thumb-2 and RISC-V, where it also
  lifts functions with arguments beyond the eighth);
- code shared between functions (a compiler's cross call or outliner): it becomes part of every
  function that enters it with its frame open;
- what a caller reads behind a call that a C call cannot return (a 64-bit remainder in r2:r3, the
  flags of a compare helper, a register the callee leaves) and what a callee reads of its caller's
  registers: a call in an asm statement that binds them (bound calls);
- what the caller left in r4-r11 that the function hands back or reads (a frame's pad popped into
  r0): the C gets it as the caller left it, the file compiled with GCC's -ffixed-REG.

Lifted C has no names, no structure, and nobody read it: it is a stepping stone (the code is C, so it
no longer pins the layout, and it comes with cases), never a reconstruction.

## 10. Reading: names first

`dread flat` (a function with everything resolved), `dread callsites` (who calls what with which
constants, and call chains), `dread samecode` and `dread callpair` (names carried over from another
image, by identical code or by call order). Names and descriptions go into the target's tables.

## The order of work

1. Set up the target and make `dread asm --verify` pass (the new-target skill of the plugin).
2. Name the subsystems: strings, peripherals, the vector table, other images (reading aids).
3. `dread lift --check`: everything that lifts is built from C and tested; the layout becomes free.
4. Rewrite functions by hand, one at a time, each through test, mutate and integration (the
   rewrite-function skill); a lifted function's cases are the safety net for writing it properly.
5. Scenarios for the system run, for start-up, the main loop, interrupts and devices.

## Lessons the tools carry

Each of these cost time once and is now a check or a rule in a tool:

- Cases that run every instruction can still miss a wrong translation (an xor loop run once from
  zero agrees with an or): the lifter has its own fuzzer.
- A disassembler's flags can be wrong (capstone marks adc without s as setting the flags): decisions
  that matter are made from the mnemonic and tested against the emulator.
- Unicorn keeps stale references to unmapped pages: pages mapped on demand are zeroed, not unmapped.
- A linker script symbol has no type: the ARM linker took Thumb functions for ARM code and put stubs
  into the callers' slots. Symbols for the original are defined with their instruction set.
- A rebuilt idle loop is not shared code: the system run must still take its interrupts.
- Most of the generator's time went into Python bookkeeping per byte, not into the emulator: profile
  before moving code into C.
- Cases that agree on every generated input can still accept a wrong translation when no input
  reaches the difference: FreeRTOS's sprintf was accepted on every build and printed a stack word of
  its own for its third argument (no case passed one on the stack; the C's frame was not next to the
  stack arguments). Frames that must be real are now on the real stack; a check that cannot be
  exercised by the inputs is a refusal or a construction that makes it hold, not a hope.
- A real callee answers one way (newlib's _read stub always fails): when code is left behind a
  call, the generator stubs that callee, its result an input.
- A script that counts verdicts is code too: one read "dropped  FAIL fn" as a function named FAIL,
  and the numbers it reported were wrong for an evening.
- A static analysis checked against a measurement: `dread callconv` found four gaps of regflow's in
  one night on FreeRTOS (a save and restore around a call that writes the register, a named argument
  in a variadic save area, a spill taken for a save area, GCC 10's dead copies into callee-saved
  registers), each invisible to the tests because the analysis erred where nothing compared. Its own
  tests plant each kind of disagreement: a check that only ever agrees proves nothing.
- A simplification that trusts the control flow needs all of it: regflow 23's first version dropped a
  real read in a function whose switch had one case of five resolved (the others unreachable to the
  analysis). Functions with code no edge reaches are left as they are, and the switch resolution was
  fixed after (its bound check had to feed the jump).
- A measurement that runs from the checkout holds the checkout: a CI fix waited three hours behind
  one. Measurements run from a worktree at the commit they measure.
