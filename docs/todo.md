# What to do next

The queue of work, top first. Each item says how to tell it is done. Take the first open item,
finish it with tests on both gizmo builds, commit, run the leak check, push, and tick it off here in
the same commit. What was done and learned goes into the commit message; this file only keeps the
queue. The order was agreed with the user: the ARM phases, then `dread pilot`, then RISC-V. How the
queue may change while the user is away: CLAUDE.md, "Working unattended".

## Questions for the user

Decisions that are the user's, with options and a recommendation. Work goes on around them.

- **IAR-built gizmo?** For the IAR profile: gizmo built with IAR EWARM (Cortex-M3) and, for MSP430,
  EW430, with the ELF (debug info on) and the map file, put somewhere outside the repository (say
  `~/dread-private/iar/`). Tests would use them when present and skip otherwise. Which IAR versions
  and optimisation levels matter to you?

## Phase 4: cases, mutation, coverage (rest)

- [x] **Dictionary inputs for parsers.** Buffers behind learned pointers also get text made from the
  strings a function and its callees compare against (string constants in their literal pools,
  characters in compares with an immediate), as fuzzers use dictionaries. Done when gizmo's
  `apply_config` gets clearly more of its instructions from generated inputs than the 38 of 65 of
  today, without written cases, on both builds.
- [x] **Recorded cases (`dread record`).** Callees modelled in Python by a spec (models, an input
  generator, a stream helper for read-style callees); the original runs once with the models and
  the run becomes a case the generic stubs replay exactly (returns, effects, fills). Done when a
  gizmo function that reads a line through `uart_getc` is covered by recorded cases on both builds.

## CI

- [x] **GitHub Actions** (the user agreed, 2026-10-18): the suite on every push, Python 3.11 and 3.13,
  the distribution's arm-none-eabi-gcc. A failure there that does not happen locally is looked at
  first (another compiler version decides placements and identical-code mutants).

## Performance, first pass

The long runs set the pace of everything after them. Measure first, then cut.

- [x] **Profile** the test suite, case generation, `dread mutate` and `dread integration` on gizmo
  (and, read-only, on a larger image): seconds per step and per function, kept in a table the tools
  write (as `build/times/`). Done when the slowest steps are known with numbers.
- [x] **Coverage without a Python call per executed block**: the block hook runs for every loop
  iteration. Try what costs less: a hook at translation time (the cache is flushed per run, so a
  block is translated once per run), counting instructions in C, or removing the hook once the
  function's blocks are all seen. Done when generation and tests are measurably faster with the same
  coverage results.
- [x] **Poll loops**: a run that spins on a peripheral register until the instruction limit (gizmo's
  `uart_putc` takes 8 s and 19 s to generate) gets the register's answers staged automatically
  (`poll_read`) once the loop is recognised. Done when uart_putc generates in about a second.
- [x] ~~Adaptive limits~~: dropped after measuring. With the hooks in C a run to the instruction
  limit costs about 5 ms; in the heaviest gizmo generation (memcpy, 3.5 s) emulation is about 15 %
  of the time. What is left is Python work per memory location a function reads (one value pick
  and one entry per buffer byte); bulk buffer contents would be the next step if it matters on a
  large target ("Known gaps").

## Phase 5: system run

- [x] **Framework.** The image from reset with device models, original against rebuilt: device
  accesses in order, what the models see (bytes sent, pins), instruction budget, events at fixed
  points (a key at instruction N). Named scenarios in a table of the target. Done when gizmo boots
  to `READY` in both images with identical device logs, and a planted bug in a rewritten function
  shows as a difference.
- [x] **Generic models.** UART (transmit log, receive queue), timer with interrupt, GPIO pins, an
  interrupt controller and exception entry for the A profile (ARM7 IRQ vector) and the M profile
  (NVIC, SysTick, exception frames). Each a plugin-shaped class configured from `dread.toml`.
  Done when both gizmo builds run their timer interrupt and echo keys from the receive queue.
- [x] **Recording inputs from a system run** for the functions it enters, as written cases. Done when gizmo's system run adds cases that cover
  instructions the generator does not reach.

## Phase 6: lifter

- [x] **Lifter for ARM and Thumb** (instruction by instruction to C, volatile memory accesses in
  order), with the acceptance rule: lifted C is built only when its cases agree and cover every
  instruction. Done when most gizmo functions lift and pass on both builds. (`dread lift`, 2026-10-18:
  a32 30 accepted, m3 31; the rest is written by hand, never returns, runs from RAM, or is refused
  with its reason: a stack pointer loaded from memory, an exception entry reading lr, uart_puts
  reading r1 behind a call, __aeabi_uidivmod branching into __udivsi3. read_line and handle_key
  needed the cases dread sysrecord recorded; the generator alone misses a few of their instructions.)
- [x] **Instruction-level check of the lifter** (`dread liftfuzz`): random instruction sequences
  assembled into a small image, original against lifted in the emulator over many register values,
  flags read back through conditional instructions, failures reduced to their shortest form. Needed:
  a planted mistake (eor translated as orr) passed every generated case of script_run on the m3
  build, because the cases that cover the xor loop run it once from zero. Its first runs found two
  lifter bugs the gizmo functions had not shown: capstone reports adc, sbc and rsc without s as
  setting the flags (arch.sets_flags now decides from the mnemonic), and the shifter's carry of
  movs r1, r1, lsl #7 was taken after r1 was written. 2026-10-18: 8 x 120 functions of 40
  instructions on each ISA agree.
- [x] Thumb-1 cores, from gizmo built for Cortex-M0 and for ARMv4T Thumb (scratch builds): 44 and
  43 functions accepted. Found and fixed: GCC's switch helpers (bl __gnu_thumb1_case_uqi, the table
  behind the call) are marked as dispatches by the decoder and resolved by running through the helper;
  calls through a bx rN stub (bl __call_via_r3, or a bx r3 behind the function) are indirect calls;
  bx pc is a jump into ARM state; mov r8, r8 (Thumb-1's nop) and mov r3, r8 before push {r3} pass on
  what the caller left instead of reading it; pop {r0}; bx r0 leaves the return address in r0, which
  the C now has exactly (__builtin_return_address(0), also for lr read as data), and a return to a
  computed address is refused. Outside the lifter: the overlay's symbols for the original's functions
  carry their instruction set (an untyped PROVIDE made ld put ARM interworking stubs into the callers'
  slots on ARMv4T), a moved function's old entry gets a veneer through ip on cores without Thumb-2
  (b.w is invalid on ARMv4T and Cortex-M0; Unicorn's M0 model runs it anyway), cores say whether
  they have Thumb-2 ([arch] thumb2, else from -mcpu/-march), the rebuilt image gets ten times a case's
  instruction limit (lifted loops are slower; dread integration already did this), and the lifter's
  statically unreachable instructions (padding behind bx pc) are declared to the runner.
- [x] dread.autocase finds the instruction that set a branch's flags with arch.sets_flags instead of
  capstone's update_flags (generator VERSION 6, together with the next item).
- [x] Functions that never return (`dread noreturn`, a fixpoint over calls and tail calls): a call
  of one does not fall through in the lifter, and dread lift --check refuses them with that reason
  instead of failing their case generation (gizmo: main, hang and the reset handlers).
- [x] Frame pointers (mov r7, sp ... mov sp, r7; accesses and stack arguments through them) and
  Thumb-1 returns (pop {r3}; bx r3): the lifter's stack analysis follows frame addresses through
  registers and the return address through pushes and pops. liftfuzz generates both shapes. Its
  first run of them crashed the process in dread.switches: on-demand pages unmapped after a run,
  then written by a later one inside Unicorn (now kept mapped and zeroed between runs).
- [x] gizmo built by gcc at -O0, -O2 and -O3 (scratch builds, not committed): the lifter accepts
  41-45 functions per build. Found and fixed on the way: Thumb's two-operand adds r7, #12 on the frame
  pointer, Thumb's nop (capstone: hint), ARMv4T's call through a register (mov lr, pc; bx r3: the
  decoder marks it, arch.flow calls it an indirect call, for every tool), and conditional writes
  under complementary conditions (addeq r7 / addne r7) that the undefined-register check took for
  reads of what the caller left. What is left over is coverage, not translation (next item).
- [x] Learned pointers point 1 to 3 bytes into their buffer in a quarter of the inputs (generator
  VERSION 6): memcpy at -O3 left 31 instructions unrun, now 13 (its unrolled tail for 3 to 6 bytes
  left over, which needs particular lengths). handle_key at -O2 and -O3 inlines read_line and needs
  the recorded cases of the -Os build, which do not carry over to other builds.
- [x] System instructions in the lifter, as inline assembly (they act on the core): cps, barriers,
  wfi/wfe/sev, clrex, bkpt, coprocessor moves, ldrex/strex (byte, halfword, word), svc with r0-r3
  bound, mrs/msr of PRIMASK, BASEPRI, CONTROL, SPSR and the like; APSR/CPSR reads and writes go
  through the C flag variables, and a function that reads the flags before writing them (mrs r0,
  cpsr opening a critical section) starts from the core's flags instead of being refused. Stack
  pointers (msp, psp) are refused. liftfuzz generates them (about 180 per ISA and run; all agree).
- [x] APCS frames (GCC -mapcs-frame, older ARM compilers): an ldm that loads sp and pc is a return
  for every tool; the stack analysis takes sp, the registers and the return address from the slots
  an ldm through a frame address reads (ldm sp, {fp, sp, lr}; bx lr on ARMv4T). gizmo with APCS
  frames for ARMv4T and ARMv5TE (scratch builds): 42 functions accepted each.
- [x] ~~The case generator could start from the coverage of written cases~~: dropped after the
  second performance pass: read_line now generates in 0.8-1.8 s and its generated cases cover all of
  it, which was the reason for the item.
- [x] **Compiler profiles** for gcc: not needed. gcc at -Os, -O0, -O2, -O3, with APCS frames, for
  ARM state, Thumb-2, Cortex-M0 and ARMv4T Thumb lifted without anything compiler-specific: the
  stack analysis follows prologues and frame pointers, switches are resolved by running them, helper
  conventions come from dread.regflow and the decoder's call marks. A profile (`[compiler] idioms`)
  becomes necessary where a convention cannot be read from the code: IAR's cross-call fragments
  (More compilers).
- [x] The system run as acceptance for lifted functions that never return (a main loop, a reset
  handler): each is built on its own and every scenario runs on both images; accepted when they agree
  and the original's runs execute all its reachable instructions. gizmo: main on both builds and
  Reset_Handler on m3 accepted (hang and Default_Handler never run: dropped). dread test lists such
  functions as tested by the system run; dread integration leaves them out. Found on the way: the
  system run took interrupts only behind shared code, so with main's idle loop rebuilt the timer never
  fired in either image and both agreed on doing nothing; a due interrupt is now taken after a wait in
  code that is not shared (FROZEN blocks with an event due). And gcc puts main into .text.startup:
  the overlay's flags now include -fno-reorder-functions.
- [x] A-profile exception handlers in the lifter: a function whose returns are exception returns
  (subs pc, lr, #4; ldm .. {.., pc}^ or movs pc, lr, after its own sub lr, lr, #4 for IRQ, without
  for SWI) becomes `void __attribute__((interrupt("IRQ" or "SWI"))) f(void)`, gcc writing the entry
  and the return; it gets no register arguments, and the system run decides. gizmo-a32's irq_entry
  is accepted (a32 now 33). Refused: a data abort's return (subs pc, lr, #8), mixed forms.
- [x] ldrexd/strexd as inline assembly with a 64-bit operand (gcc picks the register pair); liftfuzz
  generates them on ARMv7-A (Cortex-M3 has none). Its first run of them showed the generator's own
  mistake: their address needs doubleword alignment, the buffer offsets were word aligned, and both
  images faulted alike at their own addresses.
- [ ] (Later) liftfuzz: a Thumb-1 ISA (Cortex-M0: the 16-bit forms, high-register moves, the switch helper,
  calls through bx stubs), switches on the other ISAs (tbb, tbh, ldr pc tables), conditional returns
  and tail calls.

## Performance, second pass (2026-10-18, asked for by the user)

- [x] Case generation per gizmo build 142 s -> 27 s (a32) and 130 s -> 24 s (m3), the slowest
  function 67 s -> 6 s, coverage the same or better (generator VERSION 7). Measured with cProfile:
  emulation was a few percent; the time went into Python work per byte of case memory (every
  location any run read became a value in every input: 14,000 for script_run given long lengths;
  now at most MAX_CELLS = 512) and into runs that polled the UART to the instruction limit because
  status and data were read in turn (the spin cut now recognises cycles of up to four register
  reads, and a poll stage may answer with a value the function compares, so a data register can
  deliver the line feed). read_line: 51 s -> 1.8 s, and its generated cases now cover all of it
  (m3: 17 -> 25 of 25 instructions). dread integration now applies a case's poll stages and fixed
  peripheral values (it applied only memory: inputs that waited on the UART never returned there).
- [ ] C for the peripheral hook: after the cuts, Python peripheral callbacks no longer dominate on
  gizmo; measure again on a target with more device traffic before porting `Machine._on_io` (poll
  stages, the replay plan, spin detection, the log) to dread.fasthooks.

## Phase 7: reading aids, plugin, documentation

- [x] Reading aids: `dread flat` (a function's listing with targets and literals by name, texts,
  the constant arguments of each call, switch targets, annotations, callers and regflow's
  registers), `dread callsites` (calls with constant arguments, grouped by an argument with --by,
  call chains with --reaches; dread.constants runs the code in front of a call twice with
  different fills of registers and RAM), `dread samecode` (the same code in two images,
  position-independent: a stripped copy of gizmo-m3 gets 46 of 50 names back) and `dread callpair`
  (names by the order of calls: from one seed, main, 19 functions of the stripped Thumb-2 build
  named from the ARM build, all right).
- [x] Claude Code plugin (`plugins/dread`, the repository a marketplace): skills new-target,
  read-firmware, rewrite-function and system-run that only point at the tools, agents dread-writer
  and dread-reviewer, and TARGET.md, a README template for a target with the evidence behind each
  value. `claude plugin validate` passes; a test checks the manifests, the front matter and that
  every `dread COMMAND` the skills name exists.
- [x] Method documentation (`docs/method.md`: the parts, what each shows and does not, the order of
  work, the lessons the tools carry) and a porting guide (`docs/porting.md`: the arch layer's methods,
  the ARM knowledge still outside it, the emulator, a gizmo build, the order of work).

## After the ARM phases

- [x] `dread pilot`, first version: the reconstruction loop run by code. Candidates are the lifted
  functions src does not define (their cases cover them), smallest first. The question: rules, the
  flat listing, callers' constants, prototypes, the lifted C as the exact reference; the answer's C
  goes to src/pilot/NAME.c with auto() in tests/cases/pilot.py; then recon, test, integration
  --callers and mutate --kill, the tools' own words back to the model on a failure. A survivor no
  case and no search notices gets a second typed question: the model argues it in a sentence
  (argued.tsv, marked as the pilot's and not reviewed) or corrects the C. Conversations and outcomes
  in BUILD/pilot/IMAGE. Providers (dread.llm): OpenAI-compatible servers and the Anthropic API over
  plain HTTP, keys from the environment. Tested with a scripted provider on gizmo.
- [x] `dread pilot --task input` (one JSON input at a time for the instructions a function's cases
  leave unrun; the emulator decides; a useful input becomes a written case "by" the pilot) and
  `--task name` (a name and a sentence for FUN_ functions, collected in names.tsv, never applied).
- [x] A measured run of the pilot with the local model (a 27B reasoning model on llama-server) on
  four gizmo functions, 3 answers each at most: led_on, keys_read and timer_start accepted on the
  first answer (about 4 minutes each); fmt_dec rejected: two answers came back empty (the reasoning
  used all 32000 tokens; the server returns it in `reasoning_content` and an empty `content` with
  finish_reason "length"), and the third left an equivalent survivor (`j < i` -> `j <= i`: the extra
  byte is overwritten by the terminator) with no answer left to argue it.
- [x] Pilot: an answer cut off by the token limit gets feedback of its own (the code first, reason
  less), the providers report the finish reason, the tokens and the reasoning's length and the log
  keeps them; the survivors question gets an answer of its own when it comes with the last try (it
  only asks for arguments). The pilot computes the never-returning functions once for its lifters.
- [ ] A measured run with a cloud model (waits for "Questions for the user").
- [x] RISC-V (RV32IMC), in the porting guide's order (docs/porting.md); clang 23 and ld.lld are
  installed, Unicorn runs RV32 with the machine-mode registers, capstone 5 gives the text but not the
  registers an instruction uses (`regs_access` is unsupported for RISC-V, system registers are no
  operands), so the layer decodes the instruction bits itself for everything but the text:
  - [x] gizmo-rv32 (clang and ld.lld with linker relaxation: calls inside the flash are `jal`, the
    far call into RAM stays auipc + jalr; `start_rv32.S`, `rv32.ld`, the A32 build's interrupt
    controller at the machine external interrupt) and `dread.arch.riscv`: its own decoder for
    RV32IMC, Zicsr and the machine-mode instructions, checked against llvm-mc on random operands of
    every form; flow, register effects, the auipc/lui pairs. `dread import-elf` ($x mapping symbols),
    `dread functions`, `dread flat` and `dread callsites` work on it, and `dread asm --verify` rebuilds
    it byte for byte on the first pass with no raw instruction (capstone's text as it is, labels for
    jumps and branches, `.option exact`). Shared code that named ARM registers takes them from the
    layer; `[compiler] kind = "clang"` (triple, lld, llvm-objcopy, no `-fno-reorder-functions`). The
    constant tracker no longer reaches back past a join point (on rv32 it claimed `a0 = 2` for a call
    reached with 1 and 2) and retries behind any branch that falls through. Clang inlined dispatch's
    handlers (no table), so rv32's rewritten dispatch is a variant of its own. Tests that run on rv32
    carry `@pytest.mark.rv32`; CI installs clang, lld and llvm.
  - [x] the emulator: `dread test` on gizmo-rv32's 13 rewritten functions, 630 cases, all agree and
    cover every instruction; `dread integration`, `dread record` and the C hooks work there. The case
    generator's compare-guided stage asks the layer (immediates, switch_hints, compare_chars,
    branch_deciders: on RISC-V a branch compares its two registers itself), `dread record` the
    prologue's frame (prologue_frame); the generated-case cache key carries the layer's
    generator_version when it is not 0. Clang inlined uart_putc into uart_puts on rv32, so the
    integration test's buggy callee shows through handle_key there;
  - [x] the overlay's branch: jal within 1 MiB; the room is reserved before linking (branch_length
    from the entry and the image's span) and 8 bytes give auipc t1 + jalr when jal does not reach
    (entry_branch, which dread.emu checks too); the C's instruction set from the layer
    (isa_of_symbol); `.srodata`, `.sdata`, `.sbss`, `.riscv.attributes` in the linker script;
  - [x] regflow (ilp32: a0-a7, ra, s0-s11): the entry reads of every gizmo function match its C
    signature (sum6 takes all six arguments in registers); switches: the layer's bound_check (andi
    with 2^n - 1; a branch against a constant loaded right in front of it, joined by link_pair)
    lets dread.switches run script_run's table (8 targets); noreturn (_start, main); the reading
    aids; dread ghidra (Ghidra's RV32 language `RISCV:LE:32:default` with its `gcc` compiler spec,
    the exporter names the plain instruction set by the layer) agrees with the ELF;
  - [x] the system run: reset (machine mode, interrupts off), the interrupt controller's line as
    the machine external interrupt (mstatus.MIE and mie.MEIE; mepc, mcause 11, MPIE, at mtvec,
    vectored mode too), mret in the emulator itself. gizmo-rv32 boots to READY on both images, the
    keys go through the timer interrupt, a planted bug and a bug in the interrupt path show, and
    dread sysrecord records from it;
  - [x] a lifter for RV32IMC and its fuzzer: dread.lift_riscv (a subclass of dread.lift's Lifter: the
    control flow, the stack pointer's depth, the acceptance and the switches are shared; the
    translation, the frame slots, saves by single stores and the trap handler are its own; make_lifter
    picks it). On gizmo-rv32: 46 of 47 functions lift (_start sets sp from a constant), 31 are
    accepted (the trap entry through clang's interrupt("machine") and main by the system run), the
    image with 13 hand-written and 31 lifted functions agrees on 1510 cases and in the system run.
    dread liftfuzz --isa rv32 (RiscVGenerator: the base, M and Zicsr instructions, compressed forms by
    the assembler, frame pointer and register-return styles): 450 random functions of 40 instructions,
    none differ, none refused; a planted division mistake is found and reduced. On its first run it
    found that the machine CSRs (mscratch) kept their values from one call to the next, differently
    in each image's emulator: every call now finds them at zero. All tests run on rv32 (the marker
    is gone); 269 pass.

## Browser view (`dread gui`; asked for by the user on 2026-10-18)

A view over what the tools know and decided, never a second implementation: whatever computes is a
tool, the page shows its records; the only changes are the command line's own.

- [x] v1: a local server (standard library, 127.0.0.1, host-name check against DNS rebinding, a
  per-page token for changes) and one page without framework or build step. Overview (origins in
  counts and bytes, a map of the image, refusal reasons grouped, the last test run, when the tools
  ran), a virtual function list with filters, one function (the flat listing with calls, branches
  and literals linked, coverage marks from the last `dread test` or lift check, the C: its source or
  the pilot's, the lifted C built or lifted on the spot; verdict, cases, placement, notes), search
  by name, address and text, keys. Changes: rename (new `dread rename`: every file that names the
  function, refused outside the target's directory, compiled and loaded after the change and undone
  when anything still uses the old name) and notes (annotations.txt). The tools now keep what the
  page reads: `dread lift --check` writes verdicts.tsv, `dread test` the missed instructions.
  Measured on a large private image (4586 functions): loading 0.14 s, the background analyses 19 s,
  a median function 0.024 s (2.6 s before the GUI reused one lifter fed with the never-returning
  functions it already has), the largest (5854 instructions) 1.5 s.
- [ ] (Later: after MSP430) v2: a failing case's differences (the test run keeps them per function),
  pilot review (conversations, argued survivors and name proposals, accepted or rejected from the
  page through `dread rename` and argued.tsv).
- [ ] (Later) v3: the call graph around a function, the system run's timeline (device logs of both
  images side by side).

## A realistic benchmark: FreeRTOS (asked for by the user, 2026-10-19; before the rest of MSP430)

Decompile a real project built by ourselves in many ways, with the ELF as ground truth. The
repository holds the recipe only (CLAUDE.md, rule 1); builds and results live in `~/dread-bench`.

- [x] **Recipe and harness**: `dread bench` and `bench/freertos.toml`: fetch, build with our flags,
  import (ground truth), measure (listing, Ghidra against the ELF, the lifter's acceptance, recorded
  inputs, noreturn, seconds), report. Faults it found in DREAD: a listing file that began with the
  last one's tail, an extent over a free range, a crash after a failed generation, the RISC-V
  listing and link with GNU binutils, gp read as an argument, stubs logging dead registers, a hook
  that never returns stubbed, hang marks inherited by mutants, clang's variadic spill taken for
  reads, ecall taken for a system call that reads a0-a7, every -O0 function reading nothing (the
  spill rule), saves around a call of an unset register taken for reads, a hang deep in a callee
  stubbing nothing. It brought the "load" startup kind (QEMU's ELF loader filling RAM), fixed
  registers ([image] fixed), header patches, and functions declared without a size.
- [x] **The matrix, first part**: Cortex-M3 GCC -O0/-Os/-O2, clang -Os/-O2; RISC-V GCC -Os/-O2. Every
  listing rebuilds byte for byte; Ghidra finds 235-419 of 295-437 function starts without symbols.
  The lifter, first measurement against the latest one before the system run (2026-10-19): GCC ARM
  30-31 % to 46-51 %, clang ARM 21-22 % to 46-47 %, RISC-V -Os 5 % to 42 %, -O2 6 % to 35 %.
- [x] **System runs of both boards**: the Cortex-M exception model (dread.models.nvic: priorities,
  nesting, BASEPRI, PendSV, SVCall, the process stack; Unicorn enters no handler itself) with QEMU's
  MPS2 board (CMSDK UART, two timers), and RISC-V's traps (dread.models.clint: mtime/mtimecmp, ecall
  and timer traps through mtvec) with the virt board (NS16550, CLINT); a boot scenario of about 90
  ticks each (3 s on ARM, 0.4 s on RISC-V). Over 5000 ticks of the full ARM demo its own check task
  reports that every test passes (at four clock units per count; at one, one test that its source
  says may fail when the idle task starves). The recipe doubles the ARM demo's stacks: GCC 16 gives
  one task a frame of 192 of its 320 bytes. `dread bench measure` has a record step: the functions
  left uncovered get inputs recorded in the scenarios (dread sysrecord), and the lift check runs again.
- [x] **Loop cases** for functions that never return (a task's loop, a main loop: 63-78 per build):
  their callees stubbed, each case compared at the same stub call in both images (or at the call of
  a callee that never returns, which ends the run); dread lift --check tries them first and leaves
  the rest to the system run. On the GCC -Os build: 40 accepted so, against 12 by the system run,
  and 226 of 366 functions lifted (62 %; 187 before loop cases and the two fixes below). The
  benchmark also brought: generated cases cached against the whole image (a configuration change
  kept stale cases of byte-identical functions whose globals had moved), and loads of code told
  better: from another function's extent, or from the room the overlay fills (behind the image, its
  free ranges): cases had pointed a queue into a pool and behind the image, where the rebuilt image
  has C (disagreements on that build from about 22 to 8).
- [ ] **What keeps the lifter below two thirds**, from the measurement after loop cases: (1) coverage
  misses (about 60 per build) and failed generations (about 20): recorded inputs where a scenario
  reaches the code (a yielding function records nothing: every call takes PendSV), longer or more
  varied scenarios for the rest; (2) functions that never return whose loop cases miss instructions
  (about 30): mostly a path that needs every check of the loop to pass in one case (a stubbed receive
  that must write exactly the value compared), beyond the compare-guided stage's budget on runs that
  long, and their system check needs every instruction run; (3) a few disagreements per build,
  each to be traced: one kind is an inconsistent input that makes a real callee read beyond a
  local of the caller (a queue's item size of 24 for a 4-byte item: memcpy copies the frame behind
  it, laid out otherwise in the C), which the harness cannot tell yet (it would need to know which
  stack bytes the call wrote); (4) scratch
  registers across calls (a few per build; GCC's -fipa-ra): a build with -fno-ipa-ra to measure what
  is left. The RISC-V port's untyped handlers (labels in a section with mapping symbols) are missing
  from the ground truth.
- [ ] **The rest of the matrix**: clang for RISC-V; MSP430 (16-bit port) with TI's GCC -Os.
- [ ] **The pilot on what the lifter leaves**: local model, deepseek-flash, Sonnet: success rate, time
  and cost per function (the usage ledger).
- [ ] **New architectures through it**: MSP430X with TI's cl430 (FreeRTOS's MSP430X port), AVR (its
  ATmega ports), each as its own item below.
- [ ] (Later) a compute-heavy library (littlefs) for the case generator on parsers and data structures.

## MSP430 (of special interest to the user; right after RISC-V)

Unicorn does not run it and capstone does not decode it, so it needs more than a new arch layer.

- [x] **gizmo-msp430**: built with TI's MSP430 GCC (msp430-elf-, release 8.2.0.52, unpacked in
  ~/.local/opt with wrappers in ~/.local/bin; `-mcpu=msp430 -mhwmult=none`, libgcc's and libmul_none's
  helpers), flash at 0x8000, the vectors at 0xffe0, RAM at 0x0200, gizmo's peripherals at 0x0100
  with 16-bit registers (gizmo.h's hwreg), the A32 build's interrupt controller on every vector but
  reset (start_msp430.S). The clang build stays a Makefile rule (build/msp430clang, its own helpers
  in msp430_rt.c): clang's MSP430 driver links with GNU's ld only, so ld.lld links it. u32 and s32
  are long where int is 16 bits (gizmo.h, dread.h). GCC shows idioms the lifter will meet: shared
  epilogues (br #__mspabi_func_epilog_N) and calls through a register that holds a constant
  (mov #uart_puts, r10; call r10).
- [x] **Decoder** in Python for the 16-bit MSP430 (three formats, seven addressing modes, the
  constant generators, llvm-objdump's aliases): every gizmo-msp430 instruction's text and length
  equal llvm-objdump's; on 3000 random encodings the text assembles back to the same bytes with
  llvm-mc (whose MSP430 backend lacks @rN+ into memory, push.b, push X(rN) and @pc sources; such
  instructions become raw words). The length comes from the first word alone.
- [ ] **MSP430X**: the 20-bit extension words, the address instructions (mova, calla, pushm, popm,
  rrcm...), a gizmo build with -mcpu=msp430x.
- [x] **Byte-identical rebuild**: dread asm rebuilds gizmo-msp430 with GNU as on the first pass, no
  raw word (the listing links with a script of its own now: a target's default script may impose
  memory regions, MSP430's ends below the vectors).
- [x] **Emulator interface beyond Unicorn**: Unicorn's interface is DREAD's engine contract
  (`dread.engines`: the part DREAD uses, with the semantics it relies on, tested on Unicorn and the
  new engine alike); `dread.engines.Engine` keeps memory, hooks and the run loop, and
  `dread.engines.msp430` is a Python interpreter of the 16-bit MSP430 behind it (about 1 us an
  instruction, gizmo's fmt_dec 3500 instructions in 3.5 ms). Checked against GDB's MSP430 simulator
  (`dread.engines.msp430_check`, msp430-elf-run from TI's GCC): 10000 random blocks agree once seven
  faults of the simulator are stepped around (V in sr bit 7, sub without V, sxt without C, rrc's V,
  dadd beyond BCD and its V, the destination read before the source, unaligned words); those are
  tested against TI's guide. The call without a link register: the layer's push_return and
  pop_return. Found on the way: a stop from a memory hook leaves Unicorn's pc on the access, so the
  system run repeated the access it stopped in (a UART character sent twice when an event came due
  there); it stops at the next block now.
- [x] **The overlay into the image's free flash**: `[image] free` ranges (written by import-elf from
  the gaps between sections) take moved functions and the C's own code when there is no room behind
  the image (gizmo-msp430: 26 KiB between the code and the vectors). Import-elf reads global labels
  without a type in unmarked code sections as entries (libgcc's MSP430 shift routines and shared
  epilogues), and noreturn takes a function that falls into the next one's entry as its tail call.
- [x] **Tests, generator, mutation on gizmo-msp430**: 13 functions, 652 cases, every instruction
  covered; the mutation check catches every change but two argued ones.
- [x] **Integration on gizmo-msp430**: every rewritten function runs with its real callees and agrees
  (a call that pushes its return address leaves a stack slot the run did not see written: Machine
  keeps it in stack_preset; the engine's memory hooks see the registers of the instruction's start).
- [ ] **The other tools on gizmo-msp430**: regflow, recorded cases, switches,
  callsites, Ghidra, the GUI, the system run with its models (16-bit registers), CI (TI's GCC from
  its download with a checksum, or the MSP430 tests skip), the test suite's variants.
- [ ] **The lifter and its fuzzer for MSP430.**
- [ ] **Runaway mutants on the slow engine**: a mutant that loops forever runs each case to the
  5-million-instruction limit (about 5 s per case on the MSP430 engine; two of gizmo's took over 300
  s). Bound a mutant's case by a multiple of what the original took on it.

## More architectures (each: a gizmo build with clang, the arch layer, every tool on it)

- [ ] AArch64 (A64): literal loads, adrp pairs, the AAPCS64 registers.
- [ ] MIPS32, little endian: delay slots (branch encoding in the overlay, the lifter, regflow), gp.
- [ ] PowerPC 32, big endian: brings big-endian support to the emulator, the program database and
  the listing generator; lr/ctr calls.
- [ ] Big-endian ARM (BE-8) on the existing ARM layer.
- [ ] x86, 32-bit freestanding: variable-length instructions in the program database and the
  listing generator.
- [ ] AVR on the emulator interface the MSP430 work brings (clang builds it, Unicorn does not run
  it): an interpreter behind it, Harvard memory (program and data spaces) in the program database.

## Floating point

- [ ] A gizmo build with floating point (Cortex-M4F, hard float; and VFP in ARM state): the lifter's
  VFP translation (C float and double with -ffp-contract=off, or inline assembly per instruction;
  FPSCR flags through vmrs), the case generator's float inputs (NaNs, infinities, denormals,
  boundaries), the s0-s15/d0-d7 argument registers in the harness and regflow.

## More compilers

- [ ] Clang builds of gizmo for ARM state and Cortex-M3 (libgcc from arm-none-eabi-gcc for the
  division helpers): every tool on clang's code shapes (switch tables, prologues, no -fipa-ra).
- [ ] MSP430 with a newer GCC (the AUR's msp430-elf-gcc, GCC 15, needs a root install) and with
  clang (gizmo's Makefile builds build/msp430clang already): every tool on their code shapes.
- [ ] A generic engine on Ghidra's SLEIGH (pypcode: p-code for every processor Ghidra knows) behind
  the engine contract, for architectures without an engine of their own; speed against the MSP430
  interpreter, faithfulness against it and against Unicorn.
- [ ] gcc at -O0, -O2 and -O3 for both ARM builds: frame pointers and spills, unrolled loops,
  inlining. The tests run on all of them (marked slow where needed).
- [ ] **IAR profile** (EWARM, EW430; of interest to the user): IAR's runtime helper names and
  calling details, its switch shapes, and its cross-call optimisation (repeated sequences outlined
  into fragments that are called but follow no convention): fragments recognised, their register
  contract taken from regflow, which for that also has to follow callee-saved registers a function
  reads that its caller left. Validation needs IAR-built gizmo binaries from the user (question
  below); they stay out of the public repository (IAR's runtime is in them).
- [ ] `dread whatcc`: guess the compiler and options of an unknown image from its code (prologue
  shapes, helper names and calls, switch shapes, literal pool placement, register use across calls)
  and suggest a compiler profile.

## C++

- [ ] **C++ originals and rewrites**: a C++ part in gizmo (a class with virtual methods, a template
  used twice, a static object with a constructor), built with g++ and clang++ (-fno-exceptions
  -fno-rtti). The tools on it: mangled names kept for linking and shown demangled, vtables in data
  that stay valid when a method moves, static constructors in the startup (system run), template
  instances as families; `.cpp` sources in the build, a method rewritten in C++ and one in C.

## Scale and robustness

- [ ] An open-source firmware built locally as a large test target (a FreeRTOS or Zephyr demo for
  Cortex-M3, our own build, not committed: a script fetches and builds it; tests skip without it).
  Every tool on it; numbers on speed and coverage.
- [ ] Relocatable layout: symbols instead of absolute addresses in literal pools and tables, proven
  by building with a function moved, then a normal link instead of fixed slots.
- [ ] Typed data in the listing: structures, strings and tables described in the target's config and
  emitted structured by `dread asm`.
- [ ] Ghidra round trip: names, annotations and types from the tables back into the Ghidra project.
- [ ] Property tests: random instruction streams through the listing generator (rebuild identical),
  random placements through the overlay.

## Documentation and packaging

- [ ] A tutorial that takes gizmo from ELF to a rewritten, tested function, step by step.
- [ ] A reference page per tool (from the docstrings, checked by a test that every command has one).
- [ ] `pip install` from the repository works in a clean environment; version and changelog.

## Known gaps (not scheduled)

- Case generation does Python work per buffer byte a function reads (a value pick, a memory entry):
  fine on gizmo (seconds), to be measured on a large target before anything is changed.
- `dread mutate`: the exhaustive equivalence proof for tiny functions, `--macro`.
- `dread regflow`: switch shapes bounded other than by cmp/and with an immediate fall back to coarse
  rules; callee-saved registers a function reads that its caller left are not analysed.
- The generated-case cache is keyed by the function's own bytes: a stale entry costs coverage
  (reported), never a wrong result.
