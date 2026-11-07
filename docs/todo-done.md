# Done, archived from the queue

Completed sections moved out of [docs/todo.md](todo.md) on 2026-11-07 to keep the queue short.
Each was fully ticked when archived; kept verbatim for the record. The live queue is todo.md.


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
- [x] C for the peripheral hook: after the cuts, Python peripheral callbacks no longer dominate on
  gizmo; measure again on a target with more device traffic before porting `Machine._on_io` (poll
  stages, the replay plan, spin detection, the log) to dread.fasthooks. Measured (2026-10-20) on
  FreeRTOS GCC -Os's slowest generation (a task's loop cases, its UART and timers): the two
  peripheral hooks take 10 % of it, the stub calls 40 % (after the faster register reads): not
  ported; the stub path would come first.

## Performance, third pass (2026-10-19, asked for by the user)

- [x] A lift check of FreeRTOS's GCC -Os build with its cases cached: 290 s -> 120 s. The image with
  every accepted function rebuilt spun 95 % of its system run in an assertion's loop with interrupts
  masked (the kernel rebuilt from C takes no clock time, and the timer demo asserts): a system run
  ends as "spins" when an interrupt waits masked and nothing new happens for SPIN clock units (127 s
  -> 6 s). Failed case generations are cached under their key, as cases are: the eight that always
  fail took most of the first round every time (62 s -> 3 s). The check keeps one lifter and what it
  lifted across its builds (21 s -> 5 s); the system check takes its coverage from the run's blocks.
- [x] Every run's limit was Unicorn's instruction count, which calls a hook before every
  instruction: a block hook in C keeps a budget of bytes instead (the limit's number of the
  instruction set's shortest instructions; with four bytes for Thumb a generation found less: runs
  that had stopped went on), long runs 3.5 times faster; without the C helper a Python hook keeps the
  same budget. Three of the slowest generations of the GCC -Os build make the same cases with the same
  coverage as before in 112 s instead of 280, 59 instead of 323, 35 instead of 37 (with the stub limit
  of the next item as it is again).
- [x] **Taken back** (VERSION 18): stub calls per run went from 2,000 to 256 with the budget, measured
  on four slow functions of the GCC -Os build alone (runs that returned made at most 209 calls; one
  function spent its time in 7.1 million calls of runs a generated loop count kept going; 280 s -> 27 s,
  323 s -> 57 s with the same coverage). The next benchmark run accepted 240 of the -O0 build's 437
  functions against 314: a -O0 function that empties a list a generated count long calls stubs for
  each element (one fell from 33 of 35 instructions covered to 14). The same run showed two faults of
  this pass: the spin rule was checked between the emulator's chunks, which ended the two images'
  system runs at different points (19 "device logs differ" on -O0; it is checked in a block of shared
  code now, at the same clock in both), and writes from hooks (later stages, poll stages, fixed
  peripheral values) were not marked for the reset between cases (a later stage's write stayed for
  the next case on the emulator). Lesson: a change to what generation finds is measured on every
  build of the matrix, -O0 included, before it is kept.
- [x] **Emulators that a CPU exception broke** (VERSION 19). The next measurement (stub limit back,
  the spin rule in shared code) had every build within two functions of the one before the pass but
  -O0: 241 against 314 (the nine builds in 50 minutes against 1 h 43 min). The -O0 build's case
  generation was not deterministic: under the same cache keys a second run made other cases for 157
  of 428 functions. One input (a run that ended in a prefetch abort with its stack pointer at 0)
  broke the emulator of its worker process: from then on every call on it raised the same exception
  at its first instruction, so every later generation in that worker failed at once (149 of the
  check's 175 failures), and the failure cache kept them. A core is now replaced after a run that ends
  in a CPU exception or an invalid instruction (a millisecond each; some 6,300 such runs in that
  build's check), and a failed generation is cached only when it took ten seconds or more. The -O0
  lift check: 226 -> 312 accepted before recorded inputs (291 before the pass), 21 -> 13 rounds.
  Found afterwards (an agent read Unicorn's source; checked here): Unicorn 2.1.4 builds a Cortex-M33
  for UC_MODE_MCLASS whatever CPU model is asked for, so the cortex-m3 targets ran on a core with
  the Security Extension. The input had made the code store over its own strlen, one byte turning
  bx lr into bxns r0, which left the core in Non-secure state, where every fetch faults; no register
  write reaches that state. The reset now restores Unicorn's whole CPU context as it was built
  (14 us a reset instead of 31): that state, BASEPRI, CONTROL, PSP and the rest no longer carry
  from one call into the next (BASEPRI did, FreeRTOS's critical sections set it). retire stays as
  a backstop. Lesson: a cache of failures assumes they recur; check that the same key gives the
  same result in a second run before caching what a fault elsewhere could have caused.
- [x] **The configured Cortex-M model**: without UC_MODE_MCLASS Unicorn builds the cpu the target
  names (a real M3 for cortex-m3: no Security Extension, DSP or FPU; an M0 would be ARMv6-M). The
  suite passes on it, and measure-21 (generator 21) lifted exactly what measure-20 did on every
  build of the FreeRTOS matrix, in the same time (57 min beside other work).
- [x] The test suite in a fresh worktree regenerated gizmo's cases (21 minutes; the MSP430 test 772 s,
  0.4 s with its cache): `bin/dev-worktree` copies the caches into a new worktree.
- [x] The generation of functions that never return is the slowest left (one task's loop: 49,000 runs
  of 48 stub calls, 331 s under cProfile). About half is Python per stub call (ten register reads
  through Unicorn's binding, the IT repair), the rest the solver's and the sweep's runs (24,000 and
  23,000: the sweep steps every stub answer of a loop case). A stub's register reads and IT repair in
  one C call (fasthooks) would take about a quarter; fewer search runs for loop cases would change the
  cases and wants its own measurement.
  Profiled again (2026-10-20) on FreeRTOS GCC -Os's slowest task, prvHigherPriorityNormallyEmptyTask:
  in two minutes 30,000 runs made 1.48 million stub calls, 45 % of the time; inside them 14.6 million
  register reads through Unicorn's Python binding (33 %), the IT repair after every hook (13 %),
  adding hooks per run (6.5 %). Core registers are read through Unicorn's C function directly now
  (ctypes, a buffer made once: 0.6 us a read against the binding's 1.3; its reg_read_batch is slower
  than single reads), and the IT repair wraps memory hooks only (Unicorn restores an instruction's
  starting state for those alone): the same cases (three functions compared byte for byte), loop
  cases 18 to 21 % faster (that task 104 s to 86 s, prvQueueOverwriteTask 12.1 s to 9.5 s), a plain
  function 3 %. Then the stub hooks stay on the emulator across Machines (pruned to the Machine's
  stubs before each run; adding one took about 20 us in the binding, twelve a run), the fault and
  interrupt hooks are added once per emulator, and core registers are written through the C function
  too: the same cases again (bin/ab-cases, three functions), loop cases another 17 to 23 % faster
  (that task 86 s to 71 s, 104 s in the morning; prvQueueOverwriteTask 10.2 s to 7.9 s), a plain
  function 12 %. Fewer search runs for loop cases change the cases: the item below.
- [x] The suite's slowest tests (pytest --durations, 2026-10-20) were the lifter fuzzer's: a planted
  mistake found and reduced (MSP430 31 s, RV32 30 s, ARM 15 s) and the random functions (46 s for
  six), about 120 of the suite's 269 s. The reduction generated cases anew for each of its 40 tries
  (40 of 50 s): a try now runs the generated input that showed the failure, with the written ones,
  first and without generating; only a try that does not fail so is checked with generated cases
  too (MSP430: 37 of 40 tries end at once, the same reduction as before). DREAD's own engines rebuilt
  their hook tables (two flag arrays the size of memory) at every hook added or taken away, four
  times a run: now once when a run starts (the same cases: bin/ab-cases on five MSP430 functions).
  The fuzzer's tests take the polite worker count instead of four. Those nine tests: 121 s to 75 s.
- [x] The system check (about a minute of a lift check on that build): one wave of two runs of the
  scenario per function; all candidates rebuilt together would be two runs, but brings back some of
  the clock's distortion (their own blocks take no time): try it with a fallback to one at a time.
  Measured (2026-10-20, GCC -Os, cases cached): the system check is two thirds of the lift check
  (124 of 170 s under cProfile). Half of a FreeRTOS run's blocks asked again whether an interrupt
  that waits masked (a lower priority's while a handler runs, one behind BASEPRI) may be taken: the
  answer is kept now until interrupts_changed counts a change or the masks differ, and the masks are
  read through Unicorn's C function. All 37 scenario runs of 15 targets (original and whole-image
  build) give the same outcomes; 108 s against 92 s, the MPS2 runs that end waiting on a masked
  interrupt twice as fast. Those were whole-image runs, though (every accepted function rebuilt: the
  demo asserts early and waits masked). The system check's own runs have one function rebuilt and
  run to the clock's limit: about 20 s each beside the others, 42 s a function (its build 1.7 s), all
  24 or more in one wave; the lift check is 80 s now (89 before). Grouping not done: most candidates
  are dropped (on GCC -Os 11 of 30 or more accepted) and would need their single checks anyway, it
  brings back the clock's distortion, and only 4 candidates never run at all (fault handlers and
  hooks). What a realistic run spends now: 3.4 million blocks through the Python block hook, about
  40 % of it: the item below.
- [x] The generator's runs that only circle in known code until the instruction limit (checksum on
  the MSP430: nearly every run): ending a run after a stretch without a new block would save most of
  their time on every architecture, but it changes the cases (a VERSION change) and wants its own
  measurement of coverage.
  Done (2026-10-20) for DREAD's own engines: a search run stops after limit // 4 instructions that
  enter no block it had not entered (dread.emu.Machine.call stall: the C budget hook, its Python
  twin and the engines' run loops; Generator.stall_part; in the cache key only where it applies, so
  the other images keep their cases). Measured from scratch on two FreeRTOS builds: MSP430
  generation 1,000 s of CPU to 509 (the lift check 370 s to 128), the same 143 accepted, two
  functions closer; GCC -Os (Unicorn) saved a fifth but lost two functions, one whole
  (prvSanityCheckCreatedSemaphore: FreeRTOS's blocking calls time out only after many rounds of a
  loop that enters no new code, the pended ticks counted in memory, and such runs end within the
  limit). So not on Unicorn, where an instruction costs a twenty-fifth. bin/ab-cases --cover
  compares what such a change's cases cover.
  Corrected the same day to half the limit: at a quarter gizmo-msp430's script_run lost a summing
  loop's body (149 of 158 instructions covered, the function dropped), which neither the FreeRTOS
  measurement nor the four gizmo functions compared had shown. At half: script_run whole again
  (gizmo-msp430's table as before), FreeRTOS MSP430 from scratch 760 s of generation instead of
  1,000 (lift check 242 s instead of 370), the same 143 accepted, checksum 244 s to 3 s. A
  generator change that changes cases now gets the lift check on every gizmo build too (CLAUDE.md).
- [x] Fewer search runs for loop cases (the sweep steps every stub answer of a loop case: 23,000 of a
  task's 49,000 runs): changes the cases; measure with bin/ab-cases --cover and from scratch.
  Measured (2026-10-20): of the busiest task's 72 s, the compare-guided stage made 24,000 runs (31 s),
  the boundary sweep 23,160 (24 s), the random search 850. The sweep's cap of SWEEP_RUNS a base input
  keeps at least four slots and stepped each through the whole ladder: 579 values there, more than
  150 for twelve functions a build (all tasks). A ladder longer than SWEEP_STEPS (150) keeps BOUNDS
  and a sample of the function's constants; the cache key names the cap for those functions alone.
  bin/ab-cases --cover on the eight longest of GCC -Os: the same instructions covered, 10 to 32 %
  faster (that task 73 s to 49 s). Left: the compare-guided stage's 24,000 runs (four times
  SOLVE_RUNS in each of the two passes a function not covered whole gets, with and without learning
  from loads: _generate_all).

- [x] A C fast path for the system run's block hook (dread.fasthooks): nearly every block is a known
  block of shared code with nothing due (clock += size and three comparisons); C could do those and
  call Python only for a new block, a function's entry, code built from C, an event or the limit due,
  a spin check or an interrupt question (the masked answer's registers compared in C). The same
  outcomes as the Python hook, compared on every scenario (sysdump of 2026-10-20); estimated a third
  off a realistic run, the system check and dread sysrecord with it.
  Done (2026-10-20): dread_sysblock does a known block of shared code with nothing due, a function
  entered again (counted in C, added to System.entered at the end) and a block behind which the
  masked answer still holds (the registers and bits the layer's mask_registers names, compared in
  C); the rest goes to System._on_block, which keeps the rules; the state is System.st (SysState in
  C, a plain object on DREAD's own engines). A realistic FreeRTOS run: 97.4 % of its 3.4 million
  blocks in C. Old against new: all 53 runs alike (37 scenario runs of 15 targets on both images, 16
  system-check runs of GCC -Os and RISC-V GCC -Os on both images); the system-check runs 8.1 s to
  4.8 s on ARM, 7.2 times faster on RISC-V, the scenario runs 93 s to 61 s. Found on the way: a timer
  model may schedule beyond 2**63 (the CLINT with its compare register all ones), which C's 64-bit
  fields wrapped (test_riscv_traps_from_reset ran to its limit); the true times stay in Python, C
  gets them clamped. Left in a realistic run: the peripheral hook (126,000 calls through the
  binding) and the register reads at every chunk's end.
- [x] gizmo's MSP430 cases take ten minutes to generate (the suite's first run after a generator
  VERSION change: 603 s of its 936), on DREAD's own engine, which the byte budget does not reach;
  profile that engine's runs. Profiled (2026-10-20): checksum's search runs nearly all end at the
  instruction limit (its u32 length is two registers on the MSP430: random lengths are billions),
  so the engine's cost per instruction is the cost: 54 million instructions in two minutes, spread
  over the interpreter's dispatch on the operation and the operands at every step. The engine
  compiles each instruction once into a Python function of its own, with its operation, operand
  modes, registers and constants written in, and runs them from a loop of its own (step inlined):
  2.8 times faster beside the same load (0.73 us an instruction against 2.0), with the same results
  (msp430_check: 30,000 random blocks agree with GDB's simulator; three functions' generated cases
  identical, so cached cases stay valid).
- [x] **A C path for the system run's device reads** (2026-10-22). Profiled first: FreeRTOS's
  Cortex-M clang -Os image with every lifted function rebuilt runs to the limit (30 million units)
  in 41 s; 83 % of it in System._on_io, 1.25 million calls in 10 million units, all of them ICSR:
  portYIELD writes PENDSVSET and three critical sections read ICSR (vPortEnterCritical's assertion)
  before the next, PendSV pending all along (the open item on interrupts held off by a mask that
  code outside shared code lifts), so the clock is almost all device accesses. A model names its
  plain registers now (dread.models: `plain` ranges, no side effect, a value only a call into the
  models changes; the NVIC all but CSR, whose read clears COUNTFLAG, and CVR, which moves with the
  clock; the UART all but its data register; the timer all but its count; the CLINT all but mtime;
  gpio and intc all; a model of the target's own none until it says so), and a register no model
  owns is memory. On Unicorn the device accesses go through a C hook (dread_sysio): a read of a
  plain register comes from the value the model gave at the last read done in Python, written where
  the load finds it, with the clock, the log and its counted repeats, the clock of the last new
  entry and access, `due` and the stretch's end as System._io keeps them; the C hook's entries wait
  in a buffer until System takes them, before it adds to the log or looks at it (System._take_reads).
  After anything that may change a model (a write, a read with a side effect, the events, input and
  exceptions between chunks) the registers read since are asked again (_refresh_plain); the others
  wait for their next read in Python. Every other access goes to System._io as before. Measured
  (the pair of runs, original and rebuilt, on the bench builds' whole images of measure-39, three
  builds at a time): the clang -Os builds 86 -> 35 to
  38 s (clang -Os alone: 82 -> 30 s), GCC -O2 84 -> 41 s, GCC 14 -O2 86 -> 42 s, GCC -O0 68 -> 40 s;
  the four standard builds' short runs unchanged (alternating, five rounds: GCC -Os 0.71 -> 0.66 s,
  clang -O2 0.13 -> 0.12 s, RISC-V GCC -Os 2.34 -> 2.34 s). Every outcome the same as before (how,
  clock, device log, reports, functions entered and their order: the digests of all 18 runs of nine
  builds), and as the Python hooks' on gizmo (every build and scenario, all of it shared and all of
  it outside shared code) and on a program of the test's own that tries what a cached read could get
  wrong (input arriving while a register is polled, registers that move with the clock or whose read
  clears a flag, a read inside a stretch and one that makes an event due, the quiet rule at chunks
  of 13 blocks); 18 mutants of the path (a register wrongly plain, a refresh left out, the order of
  the clocks taken, the stretch's end, `due`, the callbacks held by the state) all fail the tests.
  The C side frees what it allocates with the state (the block hook's sets were never freed: 450
  gizmo runs in one process 79 -> 89 MB before, flat now; a state that held its callbacks through
  ctypes kept every System alive on the way, 3.5 MB a run, and a test shows a finished one
  collected). Of the reads of the clang -Os run 99.96 % are done in C. Left: the writes, now 53 % of that run (938,000 calls of 8.7 us), 66 % of GCC
  -O2's; on -O0 the writes and the interrupt question each one raises behind the next shared block
  (28 % and 37 %). Next item.
- [x] **The system run's repeated writes that change nothing, in C** (2026-10-22). Profiled first
  (the original's run of each pair, the Python callbacks timed): on FreeRTOS's Cortex-M clang -Os
  with every lifted function rebuilt the writes took 58 % of the run (936,716 in Python, 9.9 us
  each: the callback, the owner, the log, NVIC.write, schedule() and _refresh_plain's ICSR), on GCC
  -O2 70 % (1.25 million); on GCC -O0 the 453,324 writes took 31 % and the blocks handed to Python
  behind them another 36 % (455,682, nearly all for the interrupt question each write raised behind
  the next shared block). Nearly every one is portYIELD's PENDSVSET, the write before it again,
  PendSV pending all along. A model names its idempotent registers now (dread.models, `idempotent`:
  a write made again with the same size and value, nothing called in the models between, at any
  later clock, changes nothing and calls nothing of the run but interrupts_changed): the NVIC all
  but SysTick's CSR and CVR (both may start the count from the clock), the UART all but its data
  register, the timer all but control and status (they move the line), gpio all but its output, intc
  all; the CLINT none (a write arms the timer against mtime, which moves with the clock); a model of
  the target's own none. When _io did a write whose model noted nothing, moved no line and asked for
  no stop (System.effects), it notes it (_note_write), and dread_sysio does the same write again
  alone until the next call into the models (models_epoch, which _refresh_plain moves on), and every
  write to a register no model owns: the clock, the log entry, the stop rules' clocks and `due` as
  _io keeps them, nothing else (no schedule(), no refresh, no interrupts_changed). Writes that
  alternate between two registers stay in Python (each is a call into the models the other's note
  depends on). The separate question, answered: the interrupt question behind such a write is not
  asked again. It depends on what the controllers want, which the repeat leaves as it was, and on
  the core's masks, which the block hook compares with the last answer's at every block as before.
  Measured on the old code, which asked it behind every write: GCC -O0 asked it 451,278 times behind
  repeating writes alone, each time with the masks of the question before, and every answer was that
  question's; on clang -Os and GCC -O2 the 3 and 8 asked with other masks (BASEPRI lifted) were
  answered "taken", and the block hook still asks those. The C hook's log entries hold writes too
  (SysEntry); System takes eight or more at once through struct (half the time of reading them field
  by field). Measured, the pair of runs on copies of the bench builds' whole images of measure-39,
  the old and the new code in turn, one at a time (other agents' work busy on up to 11 of 32 threads
  meanwhile; the faster of four rounds): clang -Os 25.0 -> 11.5 s, clang 18 -Os 25.5 -> 11.3 s, M4F
  clang -Os 24.8 -> 11.3 s, GCC -O2 28.5 -> 8.9 s, GCC 14 -O2 28.3 -> 9.2 s, GCC -O0 31.8 -> 9.5 s;
  the short runs (alternating, seven rounds, CPU seconds, medians): GCC -Os 0.58 -> 0.58, clang -O2
  0.10 -> 0.10, RISC-V GCC -Os 2.03 -> 2.03, gizmo's 40 system runs 0.201 -> 0.205 (2 %: the note of
  each quiet write done in Python, and an entry for each register written). Of clang -Os's 936,716
  writes 934,299 are done in C (a chunk's start moves the epoch, so each chunk's first is done in
  Python). Every outcome the same as before: the digests of all 18 runs of the nine builds (how,
  clock, device log, reports, functions entered and their order) and of gizmo's 40 (every build with
  a system run and every scenario, all of it shared and all of it outside shared code). Tests:
  programs of the tests' own against the Python hooks, on a Cortex-M3 (PendSV set again and again
  while pending and masked, ICSR read between, taken when BASEPRI drops and pended anew; writes
  alone longer than SPIN; set and clear in turn; SysTick's CVR and CSR, which restart the count,
  written again and again, and RVR while ticks come due; ISER while the timer's line rises; a
  register no model owns, narrow stores too; the same byte sent twice; writes longer than QUIET with
  nothing scheduled; a loop outside shared code that only writes; the UART's data register declared
  idempotent, wrongly; no model declaring anything) and on an ARM7-style core (the intc's enable and
  acknowledge with the IRQ masked by CPSR, gpio's input and output, a register the UART leaves to
  memory). A mutation check by hand of the path, 19 mutants: 18 fail a test; the survivor, the C
  side's mask of the written value, is equivalent on Unicorn (it hands the hook the stored bytes
  only). Left in these runs: the emulator with the C hooks (about 90 % of clang -Os's run of 5.5 s)
  and taking the C hook's entries into the log (6 %).
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
- [x] A measured run with a cloud model (waits for "Questions for the user"): the keys came on
  2026-10-19; the measurements are in "The pilot on what the lifter leaves".
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

## C++

- [x] **C++ originals and rewrites**: a C++ part in gizmo (a class with virtual methods, a template
  used twice, a static object with a constructor), built with g++ and clang++ (-fno-exceptions
  -fno-rtti). The tools on it: mangled names kept for linking and shown demangled, vtables in data
  that stay valid when a method moves, static constructors in the startup (system run), template
  instances as families; `.cpp` sources in the build, a method rewritten in C++ and one in C.
  Done 2026-10-20: gizmo-m3cpp (g++) and gizmo-rv32cpp (clang++), both with fw/shapes.cpp; their
  linker scripts keep .init_array, the startups run it (tests/test_cxx.py; docs/decisions.md):
  - The listing rebuilds byte for byte; the mangled names stay in the tables (the linker matches
    them), `dread functions` and the listing's headers show them demangled (dread.cxx, c++filt or
    llvm-cxxfilt), `dread functions --families` lists the instances of each template (bound<...>:
    bound<int>, bound<unsigned int>; methods of a class template too).
  - The lift check takes every function the plain builds take: m3cpp 43 lifted and 17 by hand of
    61 (ram_crc refused, as in m3), rv32cpp 39 and 17 of 58 (_start and ram_crc refused, as in
    rv32). Three gaps showed: storing sp itself (str sp, [sp, #k]: &local_object kept for a call
    through the vtable; now the frame's address, as mov rX, sp gives it); calls through a vtable,
    where every generated input faulted (object, table, code: the generator learned code pointers
    only in a pass that cases which ended seeded; generator 29 learns them in the first pass of
    every function that reaches a call through a register); and on RISC-V the object's word, which
    the first run read through a pointer put two bytes off, so that the aligned runs never read it
    whole (generator 30: an aligned cell takes the place of misaligned ones it overlaps). Neither
    generator changed a plain gizmo build's list.
  - clang folds a constructor that only stores constants into .data (no .init_array entry): the
    Counter reads the volatile g_ticks, which is 0 when the constructors run after .bss is cleared.
  - `.cpp` sources in src (build.SOURCES; [compiler] cxx and cxxflags: no exceptions, no RTTI, no
    guards, no atexit); weak symbols count as defined (template instances are weak: the original's
    symbol would win the link). Tri::area rewritten in C++ (the class by its layout, without virtual
    methods, so that no vtable is emitted), the template bound once for both instances (explicit
    instantiations), Rect::area in C under its mangled name: all in place at the original's sizes,
    their generated cases agree, planted bugs fail; dread mutate finds C++ definitions by their
    source names (Tri::area, bound, Box<T>::get) and caught all (bound<int>'s < to <= by --kill:
    v == lo > hi).
  - Square::sides (lifted, larger) moves with a branch at its entry, which Square's vtable in the
    image's data still points at: the system run agrees ("shapes 158": the constructor ran; 116
    without it); on rv32cpp the three scenarios agree too.
