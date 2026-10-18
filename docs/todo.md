# What to do next

The queue of work, top first. Each item says how to tell it is done. Take the first open item,
finish it with tests on both gizmo builds, commit, run the leak check, push, and tick it off here in
the same commit. What was done and learned goes into the commit message; this file only keeps the
queue. The order was agreed with the user: the ARM phases, then `dread pilot`, then RISC-V. How the
queue may change while the user is away: CLAUDE.md, "Working unattended".

## Questions for the user

Decisions that are the user's, with options and a recommendation. Work goes on around them.

- **CI on GitHub Actions?** The tests on every push (both gizmo builds; arm-none-eabi-gcc and clang
  from the runner's packages). Free for public repositories; it would show a status badge.
  Recommendation: yes, after the performance pass, so a run stays short.

## Phase 4: cases, mutation, coverage (rest)

- [x] **Dictionary inputs for parsers.** Buffers behind learned pointers also get text made from the
  strings a function and its callees compare against (string constants in their literal pools,
  characters in compares with an immediate), as fuzzers use dictionaries. Done when gizmo's
  `apply_config` gets clearly more of its instructions from generated inputs than the 38 of 65 of
  today, without written cases, on both builds.
- [ ] **Recorded cases (`dread record`).** Callees modelled in Python by a spec (models, an input
  generator, a stream helper for read-style callees); the original runs once with the models and
  the run becomes a case the generic stubs replay exactly (returns, effects, fills). Done when a
  gizmo function that reads a line through `uart_getc` is covered by recorded cases on both builds.

## Performance, first pass

The long runs set the pace of everything after them. Measure first, then cut.

- [ ] **Profile** the test suite, case generation, `dread mutate` and `dread integration` on gizmo
  (and, read-only, on a larger image): seconds per step and per function, kept in a table the tools
  write (as `build/times/`). Done when the slowest steps are known with numbers.
- [ ] **Coverage without a Python call per executed block**: the block hook runs for every loop
  iteration. Try what costs less: a hook at translation time (the cache is flushed per run, so a
  block is translated once per run), counting instructions in C, or removing the hook once the
  function's blocks are all seen. Done when generation and tests are measurably faster with the same
  coverage results.
- [ ] **Poll loops**: a run that spins on a peripheral register until the instruction limit (gizmo's
  `uart_putc` takes 8 s and 19 s to generate) gets the register's answers staged automatically
  (`poll_read`) once the loop is recognised. Done when uart_putc generates in about a second.
- [ ] **Adaptive limits**: during the search, a run that takes far longer than the function's runs
  so far is cut early (the case keeps the full limit when it is kept). Done with numbers.

## Phase 5: system run

- [ ] **Framework.** The image from reset with device models, original against rebuilt: device
  accesses in order, what the models see (bytes sent, pins), instruction budget, events at fixed
  points (a key at instruction N). Named scenarios in a table of the target. Done when gizmo boots
  to `READY` in both images with identical device logs, and a planted bug in a rewritten function
  shows as a difference.
- [ ] **Generic models.** UART (transmit log, receive queue), timer with interrupt, GPIO pins, an
  interrupt controller and exception entry for the A profile (ARM7 IRQ vector) and the M profile
  (NVIC, SysTick, exception frames). Each a plugin-shaped class configured from `dread.toml`.
  Done when both gizmo builds run their timer interrupt and echo keys from the receive queue.
- [ ] **Recording inputs from a system run** for the functions it enters, as written cases. Done when gizmo's system run adds cases that cover
  instructions the generator does not reach.

## Phase 6: lifter

- [ ] **Lifter for ARM and Thumb** (instruction by instruction to C, volatile memory accesses in
  order), with the acceptance rule: lifted C is built only when its cases agree and cover every
  instruction. Done when most gizmo functions lift and pass on both builds.
- [ ] **Compiler profiles** (`[compiler] idioms = ["gcc"]`): what the lifter and the template
  families take from the compiler (prologue shapes, switch shapes, helper calls). gcc first.

## Phase 7: reading aids, plugin, documentation

- [ ] Reading aids: flat listing of a function, call sites with constant arguments, same code in
  two images (names carried over), the boot sector style call-order pairing.
- [ ] Claude Code plugin: thin skills that call the tools (rule 8: process in code), a TARGET.md
  template for a new target.
- [ ] Method documentation and a porting guide for a new architecture.

## After the ARM phases

- [ ] `dread pilot`: the reconstruction loop run by code, a language model only for judgement
  (write C from a listing, propose an input, argue a survivor, propose a name), typed questions,
  answers checked by tools; providers OpenAI-compatible and Anthropic.
- [ ] RISC-V (RV32IMC): a third gizmo build (clang 23 and ld.lld are installed: no system packages
  needed) and the architecture layer behind `dread.arch`.

## MSP430 (of special interest to the user; right after RISC-V)

Unicorn does not run it and capstone does not decode it, so it needs more than a new arch layer.

- [ ] **gizmo-msp430**: clang `--target=msp430`, freestanding, with gizmo's own multiply and divide
  helpers (the base ISA has no multiplier) and a linker script for a typical part (flash, RAM,
  vectors at the top of the first 64 KiB).
- [ ] **Decoder** in Python for MSP430 and MSP430X (three formats, seven addressing modes, the
  20-bit extension words), checked instruction by instruction against `llvm-objdump` on gizmo.
- [ ] **Byte-identical rebuild**: the listing generator emits assembly that clang's integrated
  assembler (llvm-mc) turns back into the same bytes.
- [ ] **Emulator interface beyond Unicorn** (what `dread.emu` needs: memory map, registers, code,
  block and memory hooks, unmapped accesses, instruction limits) and an MSP430 interpreter behind it,
  checked against what the compiled C must compute; speed in numbers.
- [ ] **Every tool on gizmo-msp430** (tests, generator, mutation, integration, regflow).

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

## More compilers

- [ ] Clang builds of gizmo for ARM state and Cortex-M3 (libgcc from arm-none-eabi-gcc for the
  division helpers): every tool on clang's code shapes (switch tables, prologues, no -fipa-ra).
- [ ] gcc at -O0, -O2 and -O3 for both ARM builds: frame pointers and spills, unrolled loops,
  inlining. The tests run on all of them (marked slow where needed).
- [ ] `dread whatcc`: guess the compiler and options of an unknown image from its code (prologue
  shapes, helper names and calls, switch shapes, literal pool placement, register use across calls)
  and suggest a compiler profile.

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

- `dread mutate`: the exhaustive equivalence proof for tiny functions, `--macro`.
- `dread regflow`: switch shapes bounded other than by cmp/and with an immediate fall back to coarse
  rules; callee-saved registers a function reads that its caller left are not analysed.
- The generated-case cache is keyed by the function's own bytes: a stale entry costs coverage
  (reported), never a wrong result.
