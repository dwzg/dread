# What to do next

The queue of work, top first. Each item says how to tell it is done. Take the first open item,
finish it with tests on both gizmo builds, commit, run the leak check, push, and tick it off here in
the same commit. What was done and learned goes into the commit message; this file only keeps the
queue. The order was agreed with the user: the ARM phases, then `dread pilot`, then RISC-V.

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
- [ ] RISC-V: a third gizmo build and the architecture layer.

## Known gaps (not scheduled)

- `dread mutate`: the exhaustive equivalence proof for tiny functions, `--macro`.
- `dread regflow`: switch shapes bounded other than by cmp/and with an immediate fall back to coarse
  rules; callee-saved registers a function reads that its caller left are not analysed.
- The generated-case cache is keyed by the function's own bytes: a stale entry costs coverage
  (reported), never a wrong result.
