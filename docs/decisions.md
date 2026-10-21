# Decisions taken by recommendation

The user, 2026-10-20: "Generally I leave decision to your recommendation. Just note it down so I
can review your decisions later." A question that used to wait in `docs/todo.md` ("Questions for
the user") is decided by the recommended option and written down here: the question, the options,
the choice, why, and how to undo it. What only the user can do (provide files, credentials, buy or
install system software) and what cannot be undone (a force-push, making something public) still
goes to the user.

Newest first. "Status" says whether the choice is in the code yet.

## Memory at fixed addresses as names: macros that are the access (2026-10-21)

**Decided by me**, on the user's standing rule (decisions by recommendation; review here).

- **Question.** Readable C wrote every global and device register as `*(volatile u32 *)0x20029aacu`:
  the same variable is hard to recognise from function to function, and the model naming a
  function's variables had to guess what the address holds. How should such memory be named?
- **Options.** (a) An extern variable per address, defined by the linker script or an assembler
  file at that address. (b) A macro per address and type above each function that is the access
  itself: `#define g_20029aac (*(volatile u32 *)0x20029aacu)`. (c) Leave the addresses.
- **Choice: (b).** The macro expands to the very expression it replaces, so the code cannot change
  (the gate checks it anyway) and nothing changes in the build: no symbols, no linker script, no
  question of what a symbol's type does to the code. Default names say where the memory is (io_,
  rom_, g_ from the target's [memory]); the model names them for the whole image from their uses
  in every readable function, before the functions' variables, which then see the names.
- **Undo.** Take apply_globals out of dread.readable's transform; the readable_names table's
  globals part is then unused.
- **Status.** Built (dread.naming apply_globals, dread pilot --task readable); measured in
  docs/todo.md (the pilot on the draft).

## Names for readable C: the model proposes a map, code applies and checks it (2026-10-21)

**Decided by the user** ("go ahead with the pilot that way"), on a recommendation.

- **Question.** Readable C has the structure of the source but registers and offsets for names.
  What a variable means takes judgement: who gives the names, and how are they kept safe?
- **Options.** (a) The pilot writes the whole function again from the readable C (its write task: a
  model's C, checked by cases and the mutation check). (b) The model proposes only a map of names
  and a sentence; code applies it.
- **Choice: (b).** A rename applied by code cannot change behaviour; it is checked anyway (the C with
  the names must compile to the same code, a stronger check than cases). The answer is short (a
  JSON object), so a question costs a fraction of a rewrite's tokens. Each map keeps a digest of the
  C it was made for, so a later change of the transformations cannot put names on other variables.
  Scored against the source's names (the FreeRTOS bench is built with -g: debug information, read
  with pyelftools, the optional extra "dwarf"). Types (structures behind pointers, their fields)
  follow as a second step of the same kind: derived by code, named by the model.
- **Undo.** Delete the readable_names table; dread readable prints the registers' names again.
- **Status.** Built (dread/naming.py, dread pilot --task readable, dread readable --score); measured
  in docs/todo.md.

## z3 proves dread readable's rewrites of conditions and values (2026-10-21)

**Decided by recommendation**; the user, the same day: "If additional python packages help us, feel
free to install and use them."

- **Question.** The clean-up (dread.tidy) turns the lifters' flag arithmetic into comparisons and
  values (a compare and its branch as `r1 == r3`) by proposing simple expressions and keeping one that
  computes the same. How to know it does?
- **Options.** (a) Test on boundary and random values (dread.cexpr evaluates C with its types). (b)
  Prove with an SMT solver (z3: bit vectors, decidable for these expressions, milliseconds).
- **Choice: (b), with (a) as the filter in front.** Testing passed `r5 == 0x41` for the range check
  `(r5 & ~0x20) - 0x41 <= 0x19` (gizmo's parse_kv): none of the values tried was a letter. The gate
  caught it (the cases failed), but a rewrite must not depend on the cases' luck. Now a proposal is
  kept only when z3 proves that wherever the lifted expression is defined, the proposal is defined
  and has its value; testing (with each constant's neighbours and sums among the values since)
  discards the others cheaply. z3-solver is an optional extra (`pip install -e ".[prove]"`, CI
  installs it); without it, conditions and values stay as the lifter wrote them (the other clean-ups
  are sound by construction and stay).
- **Undo.** Remove the extra; dread.tidy then skips both passes.
- **Status.** Done (dread/cexpr.py prove, dread/tidy.py conditions and values).

## A transformer from lifted C to readable C, next after the fragile-case fix (2026-10-21)

**Decided by recommendation**, on the user's question (2026-10-21: "Could we also build a tool that
transforms lifted C into readable C?").

- **Question.** Lifted C is exact and accepted only with cases that run every instruction and agree,
  but it has no structure: every register a variable, every branch a goto, every load and store
  volatile. `dread pilot` has a language model write readable C from the listing and the lifted C,
  through the same checks; nothing turns lifted C into readable C by itself. Build that, how, and
  before or after what is left of compiler diversity?
- **Options.** (a) Leave readability to the pilot: a model per function, with its cost, its time
  and its refusals. (b) An LLVM route: clang turns the lifted C into optimised IR, and a structurer
  such as Rellic (pattern-independent structuring after "No More Gotos", NDSS 2015) prints goto-free
  C from it. (c) A C-to-C transformer of DREAD's own, in Python, on the lifted C: a clean-up
  (copies and constants propagated, flags nobody reads dropped, a compare and the branch that tests
  it as one comparison), structuring (gotos to if/else, loops and switch; a goto stays where the
  flow is irreducible), variables (stack slots as locals from regflow's frames, registers as
  parameters and a result from regflow and callconv) and memory (volatile only where order is
  behaviour: peripherals); names and types left to the pilot, which then starts from a draft that
  already passes.
- **Choice: (c), next after the fragile-case fix**, ahead of the long tail of compiler diversity
  (measurement 38 gains 4 to 13 functions a build; the bench measurements go on meanwhile). It reads
  the lifters' output, so one transformer serves ARM, RISC-V and MSP430; its input is a narrow
  dialect DREAD writes itself; there is no LLVM build to carry (whether Rellic is still maintained
  was not checked). Every result goes through the lift check's gate (cases that run every
  instruction, agreement, the whole image), so the transformer need not be proven correct: what it
  gets wrong is refused, and the lifted C stays. Measured on the FreeRTOS bench against its sources
  (gotos left, statements and nesting against the source's) and by what the gate accepts per build.
- **Undo.** Drop the section "Readable C from lifted C" from docs/todo.md; the pilot stays as it is.
- **Status.** Not started (the plan: docs/todo.md, "Readable C from lifted C").

## Compiler diversity before more architectures; MIPS paused after step 1 (2026-10-20)

**Decided by the user** ("we need to focus on the most important architectures which are already
implemented (ARM/Cortex-M and RISC-V) and focus on compiler diversity. I follow all your
recommendations here."), on a recommendation.

- **Question.** CI's older compilers (GCC 13, binutils 2.42, clang 18) showed two gaps of DREAD's
  own that the newer ones here never did. Real firmware comes from many compilers, versions and
  options: does DREAD need a profile per compiler?
- **Recommendation taken.** No catalogue per compiler. The comparison with the original keeps
  results right whatever the compiler; what a compiler can cost is coverage and acceptance where the
  tools recognise code shapes. So: (1) measure across compilers (toolchains in podman containers as
  an axis of the benchmark, an older distribution in CI, Compiler Explorer's compilers for the
  lab's probes); (2) replace shape catalogues by analyses (one analysis of what registers hold as
  sp, argument or constant plus an offset); (3) the emulator as oracle where it can answer
  (calling conventions measured with poisoned registers); (4) language models as writers of rules
  for what is left, judged by the comparison; (5) compilers identified by matching their output.
  docs/todo.md, "Compiler diversity", holds the items.
- **MIPS.** Paused after step 1 of docs/porting.md (decoding, flow, the listing: on main); step 2
  (the emulator and what hangs on it) on the branch `mips`, with its open Unicorn segfault.
- **Compiler Explorer and private code.** Its public instance gets DREAD's own probes and test code
  only. Matching a private image's functions would send C written from the image: that needs an
  instance of our own (open source, in podman), as language models need DREAD's guard.
- **Undo.** Reorder docs/todo.md; the branch `mips` resumes MIPS where it stopped.
- **Status.** Plan in docs/todo.md; MIPS step 1 on main (2026-10-20).

## atexit counts as a generic name in the leak check (2026-10-20)

**Decided by recommendation** (it narrows a safety check, so it is written down).

- **Question.** The push of gizmo's C++ builds was stopped by the leak check: `atexit`, a name in
  a private project's tables, appears in DREAD through GCC's option `-fno-use-cxa-atexit` (in the
  documentation, the compiler defaults, the Makefile, and the flags an ELF's debug information
  records).
- **Options.** (a) `atexit` on the generic list of dread.leakcheck, beside `exit`, `abort` and
  `malloc`; (b) on the allow list in the user's configuration (for coincidences of one private
  project); (c) avoid the option (it keeps static destructors from registering with a C++ runtime
  the overlay cannot place: needed).
- **Choice.** (a): it is a function of the C standard library, as generic as the names already on
  that list; that a private image has one tells nothing about it, for any user of the check.
- **Undo.** Take `atexit` off GENERIC in dread/leakcheck.py.
- **Status.** Done (2026-10-20).

## How C++ rewrites describe their classes, and where the C++ builds are tested (2026-10-20)

**Decided by recommendation** (the todo item "C++ originals and rewrites").

- **Question.** A method rewritten in C++ needs its class. Declared as in the original (virtual
  methods, the hierarchy), a virtual method defined outside its class becomes the class's key
  function, and the compiler emits the class's vtable (and with RTTI its typeinfo) in the rewrite's
  file: data the overlay would place beside the image's own vtable, under the same symbol. And the
  two C++ builds of gizmo could join every per-build test (conftest's VARIANTS) or have their own.
- **Options.** (a) The class described by its layout: the vtable pointer as a first member, the
  methods without `virtual`; the mangled name does not encode virtual, so the definition links
  against the original's entry. (b) The real hierarchy with every virtual method inline in the
  class and the rewritten one forced out (`__attribute__((used))` on an inline method): it emits a
  weak copy whose section name depends on the compiler, and nothing guards against the vtable
  coming back. (c) The real hierarchy and a linker script that drops the rewrite's vtables. For the
  tests: (1) tests/test_cxx.py with what C++ adds; (2) m3cpp and rv32cpp in VARIANTS.
- **Choice.** (a), with a test that the rewrite's object defines no vtable, typeinfo or guard
  symbol; a function template is rewritten once with an explicit instantiation per instance (weak
  symbols, which the build now takes as defined). (1): the per-build tests carry build-specific
  expectations (refusals, counts) and would run on six builds instead of four, two of which differ
  from m3 and rv32 only in shapes.cpp; test_cxx.py runs the listing, the build, the cases,
  the system run and the generator on both.
- **Undo.** (b) or (c) need only the rewrite's source to change; for (2), add the two names to
  VARIANTS in tests/conftest.py and give the per-build tests their expectations.
- **Status.** Done (2026-10-20): gizmo-m3cpp (g++) and gizmo-rv32cpp (clang++).

## Calls into the scratch count as wild calls (2026-10-20)

**Decided by recommendation** (an extension of an existing refusal).

- **Question.** A case whose original calls through a register into the middle of a function or
  into the image's data is refused (the rebuilt image has other code there); the generator repairs
  such a callback with its pointer stub. A callback a generated input aimed at a buffer in the
  scratch (the compare-guided stage's chase set FreeRTOS's FILE callbacks to buffers) ran zeros and
  data as code and went unseen.
- **Choice.** A call into the scratch is wild too, unless a stub is there (the harness's pointer stub
  lies in the scratch): the generator repairs it, the runner refuses a case that still makes one.
  The scratch holds nothing but generated inputs' buffers, so no real code is called there.
- **Undo.** Revert generator 28's change to dread.emu.Machine.watch_calls.
- **Status.** Done (generator 28, 2026-10-20).

## Refusing a case whose input makes code touch saved registers (2026-10-20)

**Decided by recommendation.**

- **Question.** A generated input can make a callee copy past a local of its caller (a queue whose
  item size the input made larger than the item, a global pointer aimed into the stack): memcpy
  then writes over a saved register (xFirstTimerHandler's r4, which its pop restores) or reads saved
  registers into memory (prvNotifyQueueSetContainer copies its own saved r7 into a queue). C lays
  out its frames otherwise, so the images differ: most of the disagreements left on the FreeRTOS
  builds were of this kind (r4 or r7 "after the return", a byte "68 vs 0").
- **Options.** (a) Keep failing such cases (a function with one is dropped). (b) Refuse a case whose
  images differ when, run again on the original, anything but a push, a pop, or a save or restore
  of that register touches a saved register's slot of a frame still on the stack; name what touched
  which register; check the stale stack data first (a case whose every difference that explains
  is compared except those outputs and keeps its coverage).
- **Choice: (b).** The case's outcome is the original's frame layout, which C cannot be held to,
  as with a stack address taken for a value (refused since before). A case without a difference is
  never refused, and one whose differences the saved registers do not explain still fails.
- **Undo.** Revert the runner commit "a case whose input makes code touch saved registers is
  refused" (such cases fail again).
- **Status.** Done (runner, saves_touched, 2026-10-20). Against measurement 26 on seven builds: +8
  (xFirstTimerHandler, TIMER0_Handler, xQueueSelectFromSetFromISR, prvNotifyQueueSetContainer on three
  builds, prvUnlockQueue, vQueueOverwritePeriodicISRDemo), none lost. 2026-10-21: the check kept the
  slots of frames that had returned, so a later frame's local there counted as a saved register
  touched, and a case whose images differed for another reason could be refused instead of
  failing; fixed (sp rising frees the slots below it), together with the check that moves the
  stack, which left a case's stack arguments behind (dread.fragile). Some of those +8 may have
  rested on such a refusal: a function the next measurement loses that way was wrongly accepted
  before, and the loss is the correction.

## A variadic callee's argument registers the caller did not set (2026-10-20)

**Decided by the user**, following the recommendation.

- **Question.** A variadic function stores every argument register from its first unnamed one on
  into its save area (FreeRTOS's demo sprintf: r2 and r3, the area's address handed to
  tiny_print), so regflow counts them read. A caller that passes fewer (`sprintf(buf, "%d", n)` sets
  r0 to r2) then seems to read what the previous callee left in r3: 12 functions of the clang builds
  are refused for reading r3 behind memset.
- **Options.** (a) Keep refusing. (b) At a call site, a register the variadic callee only stores into
  its save area counts as read only where the caller set it before the call: those callers lift, and
  a loop case's stub of the variadic callee logs the registers the caller set, not one it left.
- **Choice: (b).** The registers left out hold a leftover the original passes by accident; the
  format reads it only if the original reads an argument it did not pass, which is undefined in C.
  It takes from what a stub logs at such call sites, nothing else.
- **What came of it.** Tracing it to implement (b) showed the refusals were a regflow bug, not a
  question: clang's sprintf opens with sub sp, #8 before it stores r2 and r3, and the move took sp
  out of the bases of the frame stores regflow looks for, so the save area went unseen (GCC's, a
  push, was seen). Fixed in regflow 14: clang's save areas are no read either, as GCC's have been
  since regflow 4, at every call site, and the 18 refusals are gone (clang -Os 6, clang -O2 6, the
  M4F clang build 6 of 13). Nothing more is left out of a comparison than regflow 4 already left out.
  The per-site part of (b), counting a save-area register where the caller set it and logging it at
  a stubbed call, is not built: it would compare more than now, and a stub cannot tell a lifted
  caller's call sites (the C's) from one another.
- **Undo.** Revert regflow 14 (the refusals come back with it).
- **Status.** Done as the bug fix (regflow 14, 2026-10-20).

## Comparing a stale-data case except its stale bytes (2026-10-20)

**Decided by recommendation** (the user's standing delegation of the same day).

- **Question.** A case whose every difference is bytes the original wrote from stale stack data (an
  uninitialised local: FreeRTOS's timer commands carry a union member nobody set into the timer
  queue) is refused, as a case whose original took a stack address for a value is (rule 6, nothing
  compared is taken away). Where every case carries such bytes, the function is left uncovered
  (vStartTimerDemoTask, and xTaskIncrementTick by its recorded case: about two functions on each
  MPS2 build).
- **Options.** (a) Keep refusing. (b) Compare such a case except the bytes the fills moved in the
  original (the bytes whose value the original itself does not define), every other output as
  before, the bytes left out named in the report.
- **Choice: (b).** The bytes left out are exactly those the original does not define (they change
  with what the stack held before the call); every byte the original defines is still compared, and
  the report names what was left out, so a reader sees it.
- **Undo.** Revert the runner commit "a case whose every difference is the original's stale stack
  data is compared except those outputs" (such cases are refused again).
- **Status.** Done (runner, 2026-10-20): the report line reads "compared except what the original's
  stale stack data (an uninitialised local) decides: ...".

## Refusing a va_list into the saved argument registers (2026-10-21)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** A variadic function saves its argument registers next to its stack arguments, and a
  va_list walks from the one into the other. The lifted C keeps its frame in a local array, not next
  to its incoming arguments, so a lifted copy reads words of its own past the saved registers. The
  benchmark accepted FreeRTOS's sprintf and printf so (GCC and clang, ARM and RISC-V): the rebuilt
  sprintf printed a stack word of its own for its third argument, because no generated case passed
  an argument on the stack. A false acceptance: what to do until such functions can be lifted
  properly?
- **Options.** (a) Leave it (the comparison decides; it did not). (b) Refuse a function that takes
  an address in its save area: sound, but it also refuses functions that store such an address
  and never use it (GCC's pick_va_5 of the compiler lab, sound before). (c) Refuse only where the
  address escapes (into a call, a walk): precise, a dataflow of addresses through registers and
  frame slots that the lifter does not have yet. (d) Lift them properly: a C signature with which
  GCC lays out the same save area next to the stack arguments.
- **Choice: (b) now, (d) or (c) as the next steps.** An accepted function must be right for every
  input its callers can give; a refusal only costs a function that stays original. The losses:
  printf and sprintf on every FreeRTOS build (they were wrong), GCC's variadic probes of the lab.
- **Undo.** Revert the lifter commit "a va_list into the saved argument registers is refused".
- **Status.** Done (lift.py, lift_riscv.py, 2026-10-21). Then (d) the same night, another way than
  a C signature: a wrapped function's C gets the original's frame on the real stack (an assembly
  entry reserves it and hands the body its stack pointer), so ARM state and Thumb-2 lift such
  functions again, rightly; RISC-V too since (the entry hands the stack pointer as the ninth
  argument); Thumb-1 keeps the refusal.
