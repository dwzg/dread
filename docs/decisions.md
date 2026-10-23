# Decisions taken by recommendation

The user, 2026-10-20: "Generally I leave decision to your recommendation. Just note it down so I
can review your decisions later." A question that used to wait in `docs/todo.md` ("Questions for
the user") is decided by the recommended option and written down here: the question, the options,
the choice, why, and how to undo it. What only the user can do (provide files, credentials, buy or
install system software) and what cannot be undone (a force-push, making something public) still
goes to the user.

Newest first. "Status" says whether the choice is in the code yet.

## Arguments in result registers: what a callee overwrites, not what it writes on every path (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** dread.tidy trim_calls keeps an argument in a result register wherever some call's
  value is read, since a callee that does not write the register hands back what its caller passed
  (newlib's lock functions); a callee that writes it on every path then gets a phantom argument
  (xTaskGetTickCount(r0), uart_getc(r0)), and the caller's own parameter looks read. The item asked
  for a must-write set (the registers written on every path to a return) and the argument only for a
  result register outside it. What exactly may the trimming trust, and how are pushes, pops and
  indirect calls taken?
- **Options and choices.**
  1. The set: (a) written on every path to a return, as asked; (b) that, less the registers whose
     value from the caller may reach what the function reads (regflow overwritten: _must_write less
     _reads_first); (c) a taint analysis of every value. **(b)**: (a) alone takes a function that
     adds 1 to what its callee hands back (int g(int x) { if (c) return 7; return x; }; f calls g and
     returns its result + 1) for one whose r0 is its own, although it is the caller's r0 + 1 on one
     path: regflow's reads, which end at a call whatever the callee may write, see no read of r0 in
     f, and the trimmed call would pass garbage (tests/test_regflow.py, gauge_bump). _reads_first is
     reads with a call ending only what its callee writes on every path. (c) would follow values
     through memory too; not needed for what the trimming asks.
  2. Pops and pushes: a pop takes what it pops out of the set (push {r0, lr}; bl g; pop {r0, pc}
     hands back its caller's r0), and so does a restore that regflow pairs with its save around a
     call. A push: (a) a read of what it saves, so that a load of its slot back into the result is
     seen; (b) no use, as for reads. **(b)**: (a) made every function that pushes r0-r3 to make room
     for locals (GCC -Os push {r0, r1, r4, lr}) read them, and that ran up through every caller that
     calls such a function before it writes r0: on FreeRTOS GCC -Os vApplicationTickHook, and through
     it xTaskIncrementTick, kept r0 for vQueueSetAccessQueueSetFromISR's push. Compiled code reads no
     pushed slot before it writes it (a push of r0-r3 makes room or keeps the stack aligned; a variadic
     function's save area is kept from the trimming by readable.Context.unknown); hand-written code that
     does is not seen, as it is not for the other trimmed arguments, and the gate is there for it.
  3. A call through a register: (a) reads every argument register; (b) reads nothing, as for reads,
     and ends only its link register. **(b)**: what the result can take from the caller through such a
     call still flows (nothing is ended); what a callback reads of an argument is the same unknown
     that every argument trimmed by reads has (a callee that passes its r0 on to a function pointer
     reads nothing to regflow), and (a) ran up like the pushes.
  4. A value read only by arguments that the trimming then drops: trim_calls runs again after the
     clean-up until it changes nothing (only ever fewer arguments). vPortEnterCritical writes no r0,
     and its value was read by vPortExitCritical's call behind it until that call lost its arguments
     in the same round: on FreeRTOS GCC -Os the functions that pass it r0 went from 39 to 10, those
     that pass vTaskSuspendAll r0 from 14 to 4 (what is left goes on to a result the gate compares).
  5. Two more ways to the same symptom, found on the way: params read the entry's value in a graph
     of the body's statements, where an if/else whose arms both write a register is one node that
     writes it on some path (now flat_view's flow); and selects made an IT block's last two
     conditional instructions a select before merge_ifs could make the three one if/else, the select
     reading r0 where the flow saw the entry's r0 reach it (gizmo's uart_getc took r0): selects waits
     where the block before writes the select's variable and the select reads it.
  6. The cases' environment (dread.autocase) leaves the new set out (RegFlow.case_facts): it is no
     input of any case, and with it every image's cases would have been made anew.
  7. A check by measurement: dread callconv lists a function whose result follows its caller's value
     of a register regflow says it overwrites ("returns from, regflow: own").
- **Figures.** (docs/todo.md, the item, has them all.) Calls with a phantom argument, main against
  this on copies of the eight builds: gizmo a32 8 -> 0, m3 8 -> 0, m4 9 -> 0, rv32 7 -> 0, FreeRTOS
  GCC -Os 133 -> 4, clang -O2 144 -> 0, RISC-V GCC -Os 73 -> 0, GCC -O0 134 -> 6 (calls not trimmed at
  all); functions with a phantom parameter 5 -> 3, 6 -> 3, 6 -> 3, 0, 42 -> 33, 63 -> 58, 83 -> 79,
  17 -> 2, the rest from pushes that make room, calls that keep every argument and RISC-V's ecall (a
  new item). With options 2(a) and 3(a) together 77 functions of GCC -Os overwrite r0, with (b) 131. The
  gate accepts the same functions as before on all eight; the lifted C is byte for byte the same;
  dread callconv finds no result that follows a register regflow says the function overwrites (330
  functions with cases on the four FreeRTOS builds, every gizmo build). The cases of every image
  stayed cached.
- **Undo.** Context.handed_back returning the first one or two argument registers again brings the
  old trimming back (overwritten left unused); the second trim_calls round is the while loop in
  tidy._tidy; params' Graph(flat_view(fn)) back to Graph(fn); selects' _merges_first check.
- **Status.** Done (dread/regflow.py, dread/tidy.py, dread/readable.py, dread/autocase.py,
  dread/callconv.py; tests/test_regflow.py, test_readable.py, test_tidy.py, test_callconv.py).

## Where the system run takes an interrupt outside shared code: function entries move the clock, places both images share (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** Outside shared code the clock stood still and no place was common to both images,
  which showed twice. (1) A wait made only of C took its interrupt where each image's code came round
  to the loop search's anchor: on FreeRTOS RISC-V GCC -Os the stream buffers' coherence actor, all of
  it C, came round in prvWriteBytesToBuffer in the original and in xStreamBufferReceive with the
  scheduler suspended in the rebuilt image, and the lift check dropped vTaskSuspendAll for it
  (lift.enough). (2) An interrupt held off by a mask that code outside shared code lifts waited for
  the next shared block: FreeRTOS clang -O2 with every lifted function rebuilt asserted in GenQTest at
  94,000 units in both images alike, PendSV taken only behind vAssertCalled. A rule tried on
  2026-10-22 (taken behind the unmask) left the images agreeing but made the demos' timing tests
  assert elsewhere: the kernel outside the clock takes no time. Measured on the benchmark's original
  images run alone, the lifted functions' own code is 49 % (RISC-V clang -Os) to 91 % (Cortex-M GCC
  -Os) of the clock. Main's whole images were far from the original alone accordingly: of the 27
  Cortex-M builds nine ended in an assertion's spin, six idled to the limit with 2,466 log entries and
  twelve made 1.5 to 2.5 million log entries, where the original alone makes 85,000 to 97,000.
- **Options.**
  1. Where the clock moves outside shared code: (a) nowhere, as before; (b) by a fixed amount at every
     entry into a function outside shared code from outside that function; (c) by that function's
     size at every such entry.
  2. Where an interrupt the core takes is taken outside shared code: (A) only at places both images
     share, the entries of (b) and the block right behind the instruction that unmasks it (the
     2026-10-22 rule); (B) at the next block; (A+) as (A), and at any block that starts right behind an
     instruction at which the emulator ends its translation block without branching.
  3. For a wait made only of C: (i) the interrupt at the least rotation of the round's sequence of
     entries (the todo's sketch); (ii) nothing of its own: with (b) a round that enters a function
     moves the clock and is no wait.
- **Choice: (b) with 56 units (sysrun.ENTRY; a scenario's `entry` sets it), (A+), (ii), and the
  compiler's runtime helpers taking no time.**
  - (b) gives both a common place and time. Lifted C makes the original's calls (one function a file:
    the compiler inlines none into another; readable C the same), so both images enter the same
    functions in the same order and their clocks agree at every entry, and a kernel rebuilt in C takes
    about its time again. 56 is the average of the bytes the lifted functions' own code ran per entry
    in the original-alone runs (54 to 63 at -Os and -O2, 87 at -O0). (c) needs no constant but counts
    big functions high (1.2 to 1.5 times on average): its whole images made 58,000 to 85,000 log
    entries on Cortex-M, and one asserted (TaskNotifyArray.c); (b)'s make 86,000 to 97,000. An entry
    counts only from outside the function (the block before ended outside its code, or an exception
    came): a loop back to the entry, or a call of itself, is none, since the original's code may loop
    through its entry where the C loops behind its prologue. A moved function counts at the branch at
    its old entry when entered there, at its C only when the C's own calls enter it directly: an
    interrupt in front of it then comes behind the same units in both images (the first measured
    version counted only the C, and four RISC-V whole images differed by those 56 units where mret
    started a task at its old entry).
  - (A+): taking an interrupt at the next block (B) made the images differ on 19 of the 27 Cortex-M
    builds (PendSV after a PENDSVSET write: the C's blocks end elsewhere than the original's code's).
    (A) agreed everywhere on Cortex-M but put context switches late: vTaskSuspend(NULL), lifted,
    yields with nothing masking PendSV, and the task ran on to its next call with the argument (an
    event group not yet created, NULL) loaded before it switched; resumed later, it asserted in
    xEventGroupWaitBits on 20 builds. On the core PendSV comes behind portYIELD's isb. Unicorn 2.1.4
    ends its translation block behind isb, msr and cps (M profile and ARM state) and behind every CSR
    access and fence.i (RISC-V), measured; not behind dsb, dmb, mrs, mcr, nop or RISC-V's fence and
    wfi. Lifted C keeps those instructions themselves (inline assembly with a memory clobber, in their
    order among the memory accesses), so the block behind one is the same place in both images
    (System._behind_block_end: the block before ended where this one starts and its last instruction
    is one of the layer's ends_block). That covers the unmask too, also where the pend and the msr lie
    in one block, so that the question first comes up behind the msr. An interrupt the core would take
    at once elsewhere outside shared code waits for the next such place, an entry or shared code:
    late where a loop inside one rebuilt function makes device accesses and nothing else (gizmo's
    A-profile write test: 2 interrupts taken where the shared image takes 11).
  - (ii): with (b) the coherence actor's loop enters a dozen functions a round and moves the clock;
    the tick comes due at an entry, the same in both images, and the loop search finds no wait there.
    (i) would have left the stretches as they were and added the round's sequence of entries to the
    search, with a case it cannot decide (a round whose entries repeat inside it, two halves alike,
    picks a half by phase again). A loop inside one function (no entry in its round) keeps the old
    rule: its interrupt comes where the search came round, or behind the unmask (not established that
    this never shows; it can matter only to what the handler reads that the loop writes).
  - Runtime helpers: with entries timed, a lift check of gizmo-msp430 with a planted lifter mistake
    dropped three correct functions for the whole image. The cause was older: gizmo's rewritten
    parse_uint multiplies through __mspabi_mpyl, which the original's never calls, and compiled C
    jumps to other shared epilogues than the original's code; their bytes moved only one image's clock
    (with main's code that configuration ended 1,950 units apart, its device log agreeing by chance).
    Compiled code calls a runtime helper where its compiler chose, so no rule of the program decides
    it: the layer's runtime_helper (Arm: the RTABI's, Msp430: the EABI's and GCC's shared epilogues)
    names them, and they take no time in either image, shared or rebuilt. RISC-V's layer names none
    (libgcc's helpers there are called alike by both images in every benchmark run; not established
    beyond).
- **What it does.** A stretch is now the code between two clock movements, which entries end too;
  the loop search sees only loops inside one function. The masked-interrupt rule holds outside shared
  code as everywhere: behind the unmasking instruction. The comparison gains a dependence: C that
  calls otherwise than the original (hand-written C that inlines one rewritten function into another
  in the same file) enters other functions, its clock parts from the original's, and the whole image
  differs (a test shows it); targets compile such C without inlining (docs/target.md). Nothing is
  compared less (rule 6): the device logs, reports and endings, as before.
- **Undo.** dread/sysrun.py: ENTRY, System._entry_units and the costs and runtime helpers of _shared,
  _behind_block_end, the common argument and the deferred answers of _check_interrupts and its call in
  _stretch; dread/fasthooks.c: sys_entry_units, dread_sys_cost, sys_masks_hold's kinds, last_block,
  block_ends (dread_sys_block_end) and the conditions in sys_stretch and dread_sysblock;
  dread/fasthooks.py: set_costs, block_end, set_memo's kind; the layers' ends_block, runtime_helper and
  Arm.current_isa. tests/test_sysrun.py: the three tests of
  2026-10-23 (four) and the changed expectations of the write tests and the memory-progress test;
  tests/test_msp430.py: the system run's clocks.
- **Status.** Done. FreeRTOS clang -O2 with every lifted function rebuilt: GenQTest passes, both
  images run to the limit with 88,216 log entries (the original alone 88,555). The whole images of all
  38 benchmark builds with scenarios (each build's lifted table): all run to the limit (main: 29),
  with 0.93 to 1.23 times the original alone's log entries on Cortex-M and 1.03 to 1.07 on RISC-V; 36
  agree (main: 37): virt-gcc10-Os differs as with main (a table from before the culprit search), and
  mps2-gcc-O0's older table lists prvQueueSetSendingTask, whose C alone makes the image differ with
  main's run as with this one (the lift check drops it), which main's run never reached. The lift
  check on copies of the six builds with the frontier answers: RISC-V GCC -Os 269 (vTaskSuspendAll
  kept, nothing dropped for the whole image; 22 s instead of 367), Cortex-M GCC -Os 340, clang -O2
  294, M4F GCC -Os 349, GCC 4 -Os 323 and GCC -O0 381, as before, the whole image agreeing on all.
  Gizmo: every scenario agrees; with nearly every function counted as rebuilt the device logs are as
  before; ten scenarios of m3, m3cpp, m4 and rv32cpp end three to five entries shorter in both images
  alike (the boot takes longer, so the main loop's poll sees the scenario's input a tick earlier);
  gizmo-msp430's images now end at the same clock (before, the rebuilt one ran ahead); the lifted
  tables are the same. The C hook and the Python hooks agree (the tests; RISC-V GCC -Os and Cortex-M
  clang -O2 whole images). Times, on a lightly loaded machine, one run of each image: RISC-V clang 18
  -Os 1.6 + 1.6 s -> 0.5 + 0.5 s, GCC 12 -Os 4.4 + 5.6 -> 0.5 + 0.5, GCC -Os 1.9 + 1.9 -> 0.6 + 0.6
  (the coherence testers' busy loops call functions and are no LOOP_MAX waits any more); Cortex-M GCC
  -Os 0.3 + 0.3 s (idling with 2,466 entries) -> 5.0 + 10.2 (95,023), clang -O2 0.06 + 0.06
  (asserting) -> 4.5 + 8.8, clang -Os 5.8 + 6.1 (1.9 million entries) -> 4.8 + 10.0. The rebuilt
  image's Cortex-M runs take twice the original's: the register-test tasks' busy loops count in
  memory inside one function, LOOP_MAX waits with the loop search's write hook in (docs/todo.md,
  "LOOP_MAX can come down"). The lift checks of the six builds: 22, 74, 88, 75, 101 and 104 s (main:
  367, 62, 86, 83, 128, 108). A first version handed every block a branch not taken ends to Python
  while an interrupt was deferred (System._behind_block_end, which decides there): with lift-v3's
  clang -O2 table the original image's run took 198 s against 5 s now; the C hook keeps the answer
  by address (test_a_deferred_interrupt_passes_blocks_that_a_branch_not_taken_ends).

## Results beyond r0:r1: a naked entry hands them back, no groups (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** A function whose callers read r2 or r3 after the call was refused ("its callers read
  r2, which it sets ...; C returns r0 and r1 only"): an old ARM compiler's __value_in_regs returns a
  structure of up to four words in r0-r3, and C under the AAPCS returns r0:r1 at most (RISC-V: a0:a1;
  a function that leaves a value in a2 or a3 for its callers was refused the same way, and so were
  those callers, which ARM lifts as bound calls). On a private real-life target many functions are
  refused so (docs/todo.md). How is such a function lifted, and its callers; and the registers a
  callee reads of its caller (the stale readers)?
- **Options and choices.**
  1. How the extra results leave the C: (a) a group: the function lifted with all of its callers,
     the extra results handed among their C functions through a structure (an out-parameter, or a
     structure returned through a hidden pointer: their convention among themselves is free),
     refused while a caller stays machine code; (b) a naked entry at the function's place that
     reserves an area for the extra registers, calls the C body with the area's address as one
     argument more, and loads them from it before it returns; (c) both, (b) only where a caller
     stays machine code. **(b)** (Lifter.more_results, render, more_entry). The entry keeps the
     original's convention at the original's place, so every caller finds the registers where the
     original leaves them: machine code, lifted C (its bound call, unchanged), hand-written C, a
     case's stub of the function, the overlay's branch at the old entry. The function is accepted
     alone, by its own cases, and the lift check's loop (drop what fails, try again, the halving for
     the whole image) stays as it is. (a) changes the convention between C functions where much
     expects the original's: a stub at the function's entry answers by the original's convention,
     which a group's C caller would not read; one caller that fails its cases, an exception handler
     or an asm_only caller keeps the whole group out (a function many others call is the common
     case), and the loop and the halving would have to take groups as units. What (a) saves is the entry's code
     (for two registers: ARM state 40 bytes, Thumb-2 30, Thumb-1 28, RISC-V 28 with compressed
     instructions; the body is a function of its own beside it) and a few cycles a call. It can come
     later as an optimisation (callers' C calling the body directly where every caller is C) without
     changing what is compared.
  2. What the entry may change before the body runs: only the link register (Thumb-1, which cannot
     store lr: one of r4-r7 that is not handed in, saved and restored), so registers the C gets as
     the caller left them (-ffixed-REG) arrive untouched. The area lies above two words of the
     entry's own (the body's fifth argument, the saved return address), the stack's alignment kept
     (8 bytes, 16 on RISC-V); the entry loads the registers before it gives the area back (an
     interrupt takes the stack below sp). Thumb-1 returns with bx lr (ARMv4T's pop {pc} does not
     return to ARM code). The forms are those GNU as takes in both of GCC's syntaxes.
  3. Such a function that takes stack arguments: (a) the wrapped frame (frame and stack arguments on
     the real stack, as for a va_list), the area below the frame; (b) the entry copies the stack
     arguments. **(a)** where the wrapped frame exists (ARM state, Thumb-2, RISC-V); on Thumb-1 such
     a function stays refused. Its callers that read the extra results stay refused too: a bound call
     passes no stack arguments (none on the public builds needs it; not measured on the private one).
  4. A tail call, and a call whose registers the function hands back: bound calls (the bl or the call
     in an asm statement with the argument registers bound in and out), so that the C has what the
     callee leaves. A tail call through a register in such a function is refused (not bound).
  5. RISC-V had no bound calls (a caller that read a2 behind a call was refused): now it has them,
     a0-a7 bound, ra and the other scratch registers clobbered, a scratch register the caller reads
     behind the call bound too.
  6. The stale readers: on ARM with GCC the C gets r4-r12 as the caller left them (-ffixed-REG,
     2026-10-22) and a bound call hands r4-r11 on. It hands on r12 now too: the callers of a function
     that reads r12 where its predicated write did not run were refused (FreeRTOS's hard-float builds:
     the tasks that call __aeabi_d2f). Left out, with the reason: other compilers (clang has no
     -ffixed-REG for these registers; the entry could carry them in its area, but the only such
     functions on the public builds are refused for other reasons: prvPortStartFirstTask falls
     through, xPortPendSVHandler reads the process stack pointer), RISC-V (the C reads no register as
     its caller left it there, Lifter.can_hand; on the public builds only the millicode's t0, its
     return address: another convention), and a callee with stack arguments. A group would not have
     made them liftable either without a convention of its own between the C functions, which is
     what (1) rejects.
  7. The generic stubs (dread.autocase): (a) as they were, r2 and r3 left as the caller had them;
     (b) a stub of a function that hands back more registers answers them too, derived from its
     answer, as a wide_results callee's answers r1. **(b)**: a lifted caller's C has 0 in r2 behind an
     earlier C call where the original has what that callee left, and a stub that leaves r2 alone
     hands that difference to the caller's reads (a false difference, the caller dropped). Images
     with such functions get new case keys (environment: the functions and their registers), the
     others keep theirs.
  8. Comparisons (rule 6): nothing taken away. The cases compared every register the callers read
     after the call already (dread.runner's kept: r1, r2, r3, r12 and the flags, beyond the result in
     r0): r2 and r3 of such a function are compared, as r1 of a wide one was (m.wide_result is for
     written cases). A planted wrong r3 is dropped (tests/test_lift_results.py).
- **Figures.** Programs of our own (tests/test_lift_results.py): every function the item is about
  lifts and is accepted on ARM state, Thumb-2 (its C by GCC and by clang), Thumb-1 and RISC-V (GCC,
  clang), except what (3) and (6) leave out. The 39 FreeRTOS builds: 23 of 13,684 functions go from
  refused to lifting, on six builds; every other function's C is byte for byte as before. Lift checks
  with the system run and the whole image on those six, the code before against this: accepted
  341 -> 343 (GCC 14 -Os), 337 -> 338 (GCC 9), 336 -> 337 (GCC 8), 356 -> 358 and 344 -> 347 (hard
  float, GCC and clang), 272 -> 274 (RISC-V, GCC 10), no other verdict changed, the whole image agreeing
  on every one. Of the 23, 11 are accepted (vPortExitCritical, vTaskDelay, __aeabi_dcmpgt, pvPortMalloc,
  prvInitialiseNewTask_constprop_0 with their entries; xTaskCreate's bound call; the tasks that call
  __aeabi_d2f, on clang by the system run), 2 stay refused by the check for another reason (other code
  branches into __adddf3), 10 are dropped: vAssertCalled three times for its case generation (as on the
  builds where it always lifted), four for coverage (instructions no case or system run reached), and
  three because the r2 they hand back is on one path what an earlier callee left (xTaskResumeAll on GCC
  14, xTimerIsTimerActive on GCC 8 and 9: dread.regflow takes a read under a condition behind a write
  under the same condition in another block for a read, here prvStaticallyAllocatedCreator's it ne;
  movwne r2 ... it ne; strne r2; no C reproduces a callee's leftover, and the comparison says so).
  gizmo has no such function: its lifted tables stay as they are on all seven builds.
- **Undo.** MORE = () in dread/lift.py and dread/lift_riscv.py brings the refusals back; HANDED in
  _call's check back to BOUND_IN the refusal of r12; RISC-V's calls without bind its refusal of a
  caller that reads a2; the stubs' extra answers are AutoCase.more.
- **Status.** Done (dread/lift.py, dread/lift_riscv.py, dread/lift_msp430.py: no extra results there,
  dread/autocase.py, tests/test_lift_results.py).

## Two settings of the naming compared over replicate runs; the groups named first kept (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** One naming run against one cannot tell two settings apart: two FreeRTOS runs of one
  setting differed by 24:40 and 37:21 functions in the grounded pairwise judge and by 8:12 and 14:5
  groups (p 0.50 and 0.06), as much as runs of two settings did. The gain of the groups named first
  (`--subsystems`, the default since 2026-10-22) in what the names say was left at 15:6 by groups (p
  0.08): not established. How are two settings compared from now on, and do the groups named first give
  better function names?
- **Options.** (a) One run a side, counted by groups, as since 2026-10-22. (b) Replicate runs, every
  pairing judged with `--against` (R x R pairings across the settings and R(R-1) within, a judging
  each). (c) Replicate runs, each judged once against the source and every pairing counted from those
  verdicts, decided at the level of runs. The plan said three runs a side.
- **Choice.** (c) with five runs a side (`dread pilot --score TRUTH --grounded P --against RUN...
  --vs RUN...`, pilot.compare_runs), the test fixed before the runs were made: a setting is better when
  the permutation test over runs of the difference of the runs' means (the grounded verdicts, right 1,
  partly 0.5, wrong 0, over the functions with a source) gives p < 0.05; beside it, every run of one
  setting against every run of the other counted by groups over all the pairings (a sign test over
  groups, and a permutation test over runs of the same count), and the pairs within a setting as the
  noise floor. From now on settings are compared so: five runs a side (four at least), made side by
  side from one commit, each on a fresh copy of the build; one run against one (`--against OTHER`)
  shows where two runs differ, not which setting is better. The groups named first stay the default:
  their gain is established.
- **Why.** The runs are the independent draws of a setting: a run's names hang together within its
  groups (a group's prefix and its reading go into all of its functions) and across them (the names its
  questions show), so neither a run's functions nor its groups are independent draws; only another run
  is. A test over runs needs enough of them: with R runs a side the permutation test's least p is
  2/C(2R, R), 0.10 for three (the plan could not have reached 0.05 whatever the runs said), 0.029 for
  four, 0.008 for five. Each run judged once instead of once per pairing: the same 54 batches of
  sources for every run (the company each verdict sees is the same functions), 10 judgings instead of
  45 (25 pairings across, 20 within: about $40 at a pairing's price), a run's verdicts the same in all of
  its pairings (the judge's own noise, 8-12 % of the verdicts on a second judging, enters each run
  once), and the answers kept by question (grounded-answers.jsonl): another comparison of the same runs,
  a split of them or a run added later costs nothing for the runs judged before. The difference of the
  means decides because it is the grounded judge's own measure and the permutation over runs assumes
  nothing about how functions hang together; it also proved the sharper statistic (below). The count by
  groups says whether a difference is broad or carried by a few groups and is comparable with the single
  pairings before; its sign test still takes a run's groups for independent draws, so it describes.
- **Measured** on FreeRTOS GCC -Os (mps2, 366 functions made FUN_address, 332 with a source), Haiku 5.5
  naming with the defaults and `--no-device` (the build reads as unknown), against `--no-subsystems`,
  five runs each, made side by side at 7ea8fa2 (two of each setting at a time, the last pair alone);
  Sonnet 5.5 judging, sentences apart. The runs'
  means 0.795 +- 0.012 (0.780-0.810) against 0.735 +- 0.022 (0.712-0.764), +0.060, permutation p 0.008:
  the real split above every other but its mirror (the next, four runs of one setting and one of the
  other, +0.053); right 66 % against 58 %, wrong 7 % against 11 %; the sentences 0.650 against 0.627 (not
  tested). By groups over the 25 pairings 22:6 (sign test p 0.004), by source files 28:9 (p 0.003), by
  functions 1,729:820, ties 3,590. The group count over all pairings saturates as a statistic over runs:
  splits of four runs of one setting and one of the other reach +6 to +18 groups against the real
  split's +16 (permutation p 0.095), so it cannot decide with five runs a side where the means can. The
  noise floor: all 25 pairings across favoured the groups by groups (+4 to +20, mean +10.3), 14 of them
  beyond chance; the 20 within a setting ran from -9 to +9 (mean size 4.0), one beyond chance; so one run
  against one establishes this gain about half of the time, and the re-measure of 2026-10-22 (15:6, p
  0.08) was such a draw. Two runs against three of one setting, counted by groups over their pairings:
  none of the 20 splits beyond chance (smallest p 0.06); across the settings 160 of 200 (median p 0.013).
  Two runs of one setting name 43 % of the functions alike, with the groups as without; runs of the two
  settings 26 %. The gain lies in the demo's test code (22 of 28 groups gain: the demo's main group 0.713
  against 0.537, the queue-set test 0.825 against 0.583, the message and stream buffer demos 0.778
  against 0.661); the kernel's own files gain little or lose a little (tasks.c 0.833 against 0.823,
  queue.c 0.778 against 0.837, stream_buffer.c 0.804 against 0.854), as does the timer demo (0.637
  against 0.713), not traced. Spent: naming $10.34 (with the groups $1.10-1.13 a run, without $0.94-0.96), judging $4.46
  (541 answers; 2.5 of their 3.3 million tokens in read from the prompt cache); the comparison asked again
  asked nothing and wrote the same bytes.
- **Not established.** The gain on firmware the model does not know (FreeRTOS's assertion texts name its
  files, and the gain lies where the groups' names say which demo a function belongs to), or with another
  model naming; the judge against a person (dread.namejudge); after the first pass alone (not judged);
  why queue.c and stream_buffer.c lose a little. The permutation test takes the runs for exchangeable
  draws: they were made side by side from one commit within 75 minutes, alternating the settings.
- **Undo.** Settings: compare one run against one with `--against OTHER`, as before (not recommended:
  the noise floor above). The groups: `--no-subsystems`, or NAME_SUBSYSTEMS = False.
- **Status.** In the code (dread/pilot.py compare_runs, run_splits, pairing, group_nets, net_sign,
  judge_cache_path; dread/namejudge.py judge's cache; dread/llm.py AnswerCache; docs/formats.md). The
  ten runs and the judge's answers are not kept in the repository.

## What the system run takes for a wait: registers and memory as at the anchor; one of many culprits (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** The wait rule of 2026-10-22 (below: a block entered again with the core's registers as
  at an anchor) takes a computation for a wait as soon as its registers repeat at a block while its
  progress lies in memory. Seen on FreeRTOS RISC-V GCC -Os with the frontier answers: with
  xTaskResumeAll accepted, the rebuilt image took xStreamBufferIsFull's loop in the stream-buffer
  tests' C (8,263 blocks) for one and moved the clock on to the tick inside it; the whole image
  differed at entry 511, and the culprit search dropped 42 functions (227 accepted of 269).
  Instrumented, the runs of that image had 73 of 511 and 95 of 514 waits whose memory had not come
  round: computations (taken after 2,142 to 12,363 blocks) and the coherence tester's busy loop,
  whose counter lies in memory; most fell alike in both images by chance. How is a wait told from a
  computation, at what cost, and what does the culprit search do with a large set?
- **Options and choices.**
  1. The test: (a) registers alone, as before; (b) registers and memory as at the anchor: the whole
     state repeats, so the code repeats for ever unless an interrupt comes, whichever image's code it
     is; (c) as (b), memory through a digest of the stores (it depends on their order, and a
     collision is a false wait); (d) a count of the stores since the anchor, a wait only without any
     (a poll that pushes a return address, or raises and lowers a nesting count, never comes round).
     **(b)**, exactly: per 4-byte word stored to since the anchor, its value then (read before the
     first store) and now, and the number of words whose two differ; memory has come round when it is
     0. A busy task that counts in memory is then no loop and becomes a wait after LOOP_MAX blocks,
     as one counting in a register was already; on 2026-10-22 that cost was the reason against
     comparing memory, and it is measured below.
  2. How the stores are seen: (a) a write hook for the whole run; (b) one only while the search looks
     at a stretch, from its first anchor on; (c) RAM snapshots at the anchor, compared whenever the
     registers come round (a busy loop's come round every round: all of RAM compared a round);
     (d) RAM write-protected at the anchor, the first store to a page caught (Unicorn's regions split
     page by page). **(b)**: Unicorn 2.1.4 syncs pc before every hooked store (cpu_restore_state,
     about 48 ns), and (a) with a hook that did nothing made the run of that image 42 % slower. A hook
     put in during a run sees the stores of code translated while some memory hook was in (the device
     hooks; a hook over the sentinel where a target has no devices), and it takes itself out at the
     first store after the stretch. In C beside the C block hook (dread_syswrite), in Python on
     DREAD's own engines and without a C compiler (System._on_write); both clear Unicorn's stale IT
     state, as every memory hook must (a first version did not, and the Cortex-M GCC -Os run went off
     with a broken stack pointer once a tracked stretch stored at the end of an IT block).
  3. Tracking only from the first register match, a few rounds an anchor: a variant that tracks
     nothing (every wait then a LOOP_MAX wait) puts about three quarters of what the rule costs on the
     busy loops' blocks and a quarter on the tracking. Not done. Nor a smaller LOOP_MAX: the longest
     stretch that ended by itself in the benchmark's whole-image runs took 59,280 blocks (Cortex-M
     GCC -Os), the next 26,487; 200,000 keeps a margin of 3.4.
  4. The culprit search: (a) as before, the whole smallest set that differs alone; (b) of a set of
     LEAVE_ONE_FROM (3) or more, the smallest function whose leaving out alone makes the whole image
     agree, alone (lift.enough; dread readable --check the same); (c) (b) for every size. **(b)**: a
     difference that needs every one of many together goes with one of them, at the price of up to
     that many whole images more. A pair still goes whole, as decided on 2026-10-22 for frames that
     grew on one task's stack (keeping either would leave the smaller margin).
- **Why the culprit search is still needed.** With the memory test that image still differs, at
  entry 1195: prvSpaceAvailableCoherenceActor's loop (it sends to a stream buffer and receives back,
  its state repeating) is a true wait once it and its 13 callees are C, and the tick is taken where
  each image's code comes round to the anchor: in the original in prvWriteBytesToBuffer, where the
  tick switches tasks at once, in the rebuilt image in xStreamBufferReceive with the scheduler
  suspended, where xTaskResumeAll then yields first. With any one of the 14 the original's, the loop
  runs shared code and the tick comes behind a shared block. Where an interrupt is to come in a wait
  made only of C is an open item (docs/todo.md).
- **Undo.** dread/sysrun.py: _came_round's memory test, _track, _on_write and _track_store;
  dread/fasthooks.c: sys_at_anchor's, dread_syswrite and dread_sys_track (fasthooks.SysHooks.track).
  dread/lift.py: enough, function_bytes and LEAVE_ONE_FROM (their callers in check and
  dread/readable.py). tests/test_sysrun.py: test_a_computation_whose_progress_lies_in_memory_is_no_wait;
  tests/test_lift.py: test_enough_leaves_out_one_of_many_that_differ_only_all_together.
- **Status.** Done. The lift check on copies of the six builds with the frontier answers (cases
  cached): RISC-V GCC -Os 268 accepted instead of 227 (vTaskSuspendAll alone dropped for the whole
  image; 255 with the memory test alone), 367 s instead of 709; the five Cortex-M builds keep their
  tables (340, 294, 349, 323, 381). The whole images of all 37 benchmark builds with scenarios end
  as with main, log for log, and on every RISC-V build that runs to its limit the original's and the
  rebuilt image's runs now find the same waits of each kind (with registers alone they differed on
  all nine that have waits). Gizmo's scenarios and lift checks give the same outcomes. The cost is
  in the RISC-V runs, whose time between ticks is spent in busy loops that count in memory (72 to
  219 LOOP_MAX waits a run, 0 to 20 before): a run of a whole image on an idle machine 0.56 -> 1.50
  s on clang 18 -Os, 0.95 -> 5.71 s on GCC 12 -Os, 1.02 -> 1.89 s on GCC -Os; the Cortex-M runs as
  before.

## What the system run keeps of who entered whom, and of its device logs (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** dread gui is to show a task's and a callback's caller and the device logs of both
  images side by side (docs/todo.md, Browser view, v3). Nothing kept calls with their caller: dread
  sysrun kept the functions entered, the lift check's whole image whether the logs agree. What does
  the run keep, how, and where does it go?
- **Options and choices.**
  1. How a transfer is seen: (a) the link register at each entry (a call's return address), (b) where
     the block that ran before an entry ended, the instruction there naming how (call, through a
     pointer, return), (c) an observer of every block in Python (dread.sysrecord's way). **(b)**, in the
     C block hook and in Python alike: exact for tail calls and returns, where (a) names the caller's
     caller or a frame's made-up return address, and as cheap as a store a block, where (c) takes every
     block out of C. An exception taken in front of an entry keeps what came before it, by entry and
     stack pointer, until that entry runs again with that stack pointer: without it every interrupt
     that came there turned the call into a return from the handler. A vector made of instructions
     (the layer says where: enters_code_vector) passes the mark on from a slot that only branches,
     also where the tables call the slot a function's entry (binutils 2.35 for RISC-V, which has no
     mapping symbols, makes the table's label one: CI's Ubuntu 22.04 job); the M profile's handlers,
     named by a table of addresses, keep it.
  2. When: (a) always, (b) behind an option. **(a)**: on FreeRTOS's GCC -Os demo (its original's run
     to the limit) the run took about 5 % longer (3.99 s against 3.78 s, the best of six alternating
     runs on a shared machine), 7 % on DREAD's MSP430 engine, which does every block in Python (the
     transfers counted inline there except at entries). An option would leave the graphs without the
     run's callers wherever it was not given. Each log entry's pc is kept always too (the C hook reads
     it where it makes an entry or hands one over). Undo: an option in System, off for the lift check's
     system checks, which keep nothing.
  3. Where: (a) in the outcome only, (b) a record a tool writes, BUILD/sysrun/IMAGE/NAME.json, one a
     scenario. **(b)**, as the item asks (the page shows the tools' records): dread sysrun writes
     SCENARIO.json, the lift check its last whole image's SCENARIO.lift-check.json beside it, so that
     neither overwrites the other (the whole image's original clock leaves the rebuilt functions' code
     out and runs a different stretch than the original alone).
  4. How much of the logs: (a) all, (b) a window of KEEP (100,000) entries, from the start or around the
     first difference. **(b)**: FreeRTOS's original run to its limit has 90,114 entries (4.1 MB, 28 MB in
     the server); a longer run is not unbounded on disk or in the page's server.
- **Undo.** The transfers: System._ran, sys_ran and the CAME_* marks (dread/sysrun.py,
  dread/fasthooks.c); the records: make_record's callers in dread sysrun's main and lift.keep_whole;
  KEEP is a named constant.
- **Status.** Done (dread/sysrun.py, dread/fasthooks.c, dread/lift.py, dread/gui.py, the page).

## The frontier answers: a step after the search, from the cases the lift check runs (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** `dread uncovered` showed what the cases lack: on the FreeRTOS builds most of what
  ordinary functions miss lies behind a branch on a real callee's result, a stubbed callee's answer the
  search never gave, or memory (a global); forced the missed way, much of it runs and returns. How is
  the generator to give those values, where does it look for the branches, and how is it kept from
  costing every cache its cases?
- **Options and choices.**
  1. Where: (a) a stage inside the search (`_generate_once`, after the decider), from the generated
     corpus; (b) a step after the generation, like the firm-up, from the function's generated, kept and
     written cases together (what the lift check runs). **(b)**: on FreeRTOS much of the frontier is
     reached only by the cases dread sysrecord recorded in the system run (xEventGroupSync's test of
     uxTaskResetEventItemValue's bit 25, vTaskPriorityDisinheritAfterTimeout's of uxListRemove's
     result): no generated input gets there, so (a) could not answer them. Measured: of the answer
     cases on five FreeRTOS builds 219 start from a recorded case, 119 from a generated one. The
     search's cases stay as they were, so VERSION stays (the docstring's rule for what is added
     afterwards: a version of its own, ANSWERS); the answers are cached in the entry under "answers",
     keyed by the generation's key, its firm-up, ANSWERS and the kept and written cases, so that a newly
     recorded case makes them anew without a new search. A generation that failed is answered from its
     written cases (kept only where the failure is cached: a quick failure is not).
  2. What decides a branch: (a) the compare-guided stage's dependence probes (change each input, see
     what moves), (b) dread.uncovered's trace (decide: the flags' setter, each register back to its
     sources). **(b)**: a real callee's result is no input, so no probe finds it; the trace names it,
     and the load a global or a field comes from (uncovered.Flow.loads, a side table; its output is as
     it was).
  3. A real callee's result: (a) stub the callee at every call (the decider stage's way), (b) a stub
     that answers only the call whose result decides, the callee running at its other calls (the fourth
     element of a case's calls, the call numbers it answers; docs/formats.md). **(b)**: stubbed at every
     call, xStreamBufferSend's earlier calls write nothing, the buffer stays empty and the run takes
     another way long before the branch (prvSingleTaskTests checks each call's result); with (b) the run
     is the same up to the answered call. The answer is inserted into `returns` where that call stands
     among the stub calls, and later effects and fills are renumbered. A moved callee is hit twice per
     call in a rebuilt image (the branch at its old entry, then its C): a call let run at the old entry
     is not counted again at the C (same stack pointer, the next hit, at the alias).
  4. Memory: (a) fixed addresses only (the item's wording), (b) the memory the deciding load read at
     that visit, wherever its address came from (a field of an argument's object, of what a global
     points to, a device register), not a local and not the image. **(b)**: the address is taken from
     the run; FreeRTOS's xTaskGenericNotify tests a field of the TCB its argument points to.
  5. A local a stubbed callee was handed: **the case gets the function's frame (loop_frame) and a tight
     fill**, and a fill sets the value: an ordinary function's cases have no frame, so the stub wrote
     nothing there (FreeRTOS -O0's prvSingleTaskTests checks the value xTaskGenericNotifyWait delivers:
     1,087 of its 1,190 instructions behind it); without tight the fill wrote 64 bytes over other locals
     and the run went elsewhere. A real callee's store into a local is not answered (rare on the bench).
  6. The values: the compare's other operand and its neighbours, the operand and its neighbours (for a
     test its bits), a constant offset solved from the first two runs, the word's edges, single bits:
     at most ANSWER_TRIES (20) a branch and visit. The repairs (7.) only for the first ANSWER_FLIPS (2)
     values that went the other way and could not be kept; later ones are tried once each. (a) No cap:
     each failing value took its repairs along, up to nine runs, and on clang -O2's prvSingleTaskTests
     400 runs left 252 of its 1,083 instructions missed (2,000 runs: 111); (b) no more values after two
     such failures: 400 runs reached 111 and 1,000 runs 33, but FreeRTOS -O0's queue-set test lost what a
     single flipped bit gives (a queue handle with one bit flipped is the set's other queue: 12 missed
     instead of 0); **(c) values on, repairs off**: both (33 missed with 1,000 runs, in 70 s instead of
     52 s; the queue-set test covered). ANSWER_RUNS 1,000 a function.
  7. Repairs of a run that went the other way and failed: one that hangs in a callee is tried with the
     callee stubbed, then ending at its call; one that faults or stores into the image is chased: first
     the words no input gave that point just in front of the failing access (their values as read, from
     a run of their own: a word read as 0 and written afterwards looked harmless after the run), then
     arguments and given words that do, one at a time, nearest first (a zero argument lies in front of a
     store at 8 too, and the answered word must not be taken: the first version pointed it elsewhere and
     undid the answer), then the plain unread words; CHASE_TRIES × 2 runs a trial. A run that ends at a
     stop whose callee may return is run once more with it stubbed, for the code behind the call.
- **Undo.** Take `_answered` out of `_generated` (cases_for): the cached answers stay in the entries,
  unread. Older readers of case files take three elements of calls and stub every call.
- **Status.** In the code (branch frontier-answers): docs/todo.md, "The frontier answers".

## A build's readable C in a pool, kept in the process by what it is made from (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** A build from readable C made every chosen function's readable C in its own process,
  one after another (48 s of a 55 s build of FreeRTOS's GCC -Os demo), kept per context object only:
  the gate paid it once in one process, every dread recon and every build of dread pilot paid it
  again. Where is it made, and what is kept for whom?
- **Options and choices.**
  1. Where: (a) in the building process, (b) in a pool of forked worker processes (dread.readable
     in_pool, as the analysis trees and the questions already are), (c) spawned workers that load the
     target again. **(b)**: the workers share the context and the lifted C copy-on-write; nothing but
     the readable C's text comes back. With another thread running the process does not fork (a lock
     the thread holds would stay held in the child) and makes them as (a): in_pool checks.
  2. What is kept: (a) per context object, as before, (b) in the process by the content (a digest of
     the context's tables, the stages, whether z3 is there, each function's lifted C), the four latest
     contexts, (c) on disk under BUILD by the same key and DREAD's code. **(b)**: a new context made from
     the same tables (each build's, the next build of dread pilot) finds them, a table changed in place
     changes the key; (c) would make repeated dread recon runs fast too, but its key needs every module
     the stages run (a stale entry builds wrong C without a word), and it is not needed for the rounds.
  3. Order in the pool: the longest lifted C first, one at a time (the table's order four at a time
     ended on a few large functions).
- **To undo.** POOL_MIN at 10 ** 6 makes them in the process again; STORE_KEEP 0 keeps nothing.
- **Status.** In the code (dread.readable readable_texts, chooser; dread.build lift_sources).

## Arrays of static data, addresses as &, and what callees reach (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** docs/todo.md's "Structures of globals" left four things: arrays of scalars written as
  arrays, indexed accesses into arrays of structures, the addresses of structures and elements as
  &-expressions, and structures whose functions touch nothing next to them (gizmo's g_config, RISC-V's
  lists). What says that an array lies somewhere and how large its elements are, where it starts and
  how far it goes; how are its elements written so that every access stays one of its width at its
  address and the C behaves as the lifted C; where is an address written as &; what decides a
  structure no function reaches next to?
- **Options and choices.**
  1. The element size: (a) the machine code's pass follows scaled indices (each arch layer's value_step
     learns shifts, multiplications and register offsets with a scale), (b) the arithmetic of the lifted
     functions' readable C, which dread.tidy has folded (0x200299f8u + 0x14u * r9 + 4, (r1 << 3) +
     0x2002990cu), also through a variable that holds the index times the scale, (c) without a scale:
     the machine code's indexed access at a structure whose fields are one type one after another (the
     item's own rule). **(b) and (c)**: no arch layer changes; (c) covers functions that do not lift
     (FreeRTOS GCC -Os's ulEchoLoopCounters). To undo: plan(indexed=None).
  2. Where an array starts: **a data symbol within one element below the lowest constant of its
     accesses, else the address the code made them from or handed out there, else the lowest constant**
     (never further below than an element: an anchor below is not taken).
  3. How many elements: (a) up to the next start, as a structure; (b) what its accesses' constants
     reach, and the constant places the code reaches from its start where the start is a base that
     counts (separate_bases). **(b)**: a first version extended by the run of constant places through
     any base and made one array over two variables on five clang -Os builds (clang's global merge on
     ARM: the base is the merged object's, the next element's places the next variable's); a second
     wanted the places in one run and cut xAutoReloadTimers ([0], [19], [20]) to one element, now 21,
     as declared.
  4. Writing: (a) an array lvalue (`(*(volatile u32 (*)[N])0x...)`) or members of array type, (b) a
     pointer to the first element (`((volatile u32 *)0x...)`), indexed. **(b)**: C knows no bound, so
     GCC derives no loop bounds from an index past the end (dread.frames' rule for local buffers); the
     count stays in the plan. The elements' structure has bytes up to the element size.
  5. The index through a variable (r8_3 = 0x14u * r8_2 ... 0x200299f8u + r8_3): (a) reaching
     definitions on the structured tree, (b) a straight-line rule (statics.straight_facts): the
     assignment earlier in the same list of statements or one around it, nothing between them writes
     either variable, no label or case between. **(b)**, small and safe; the variable's assignment goes
     when nothing reads it any more (its declaration stays: a names proposal names it). A first version
     took the fact after the statement had been rewritten and took a macro (memory) for the index
     variable, so the index read memory again; the gate turned the function down (prvSemaphoreTest, on
     three builds), and the index must now be a variable of the function. A variable that holds an
     element's address (a3 = 0x80095914u + s1 * 20) carries the index to its accesses too.
  6. Addresses as &: (a) every constant inside an object, (b) a value used whole: a call's argument, an
     assignment's value, an operand of == or !=, a result; at an object's start &g_x (not &g_x.f_0), at
     an element's &g_x[3], at a field &g_x.f_8, else the number. **(b)**: arithmetic on addresses (a
     heap's bounds, end - start) stays numbers. dread.shapes' _uses counted a variable given any
     address as given a C variable's and left it untyped (the earlier entry's worry); it counts only
     the function's own variables' addresses now, and apply_types writes (T *)&g_x without a u32 cast
     between.
  7. What callees reach: (a) a pass over the machine code that follows each argument register plus a
     constant (the base registers item's "argument plus a constant"), (b) dread.shapes' templates of
     each callee's parameters, which already copy what the callees of callees reach. **(b)**, for the
     lifted functions' calls. A handed address counts without the neighbour test (a parameter points
     into one object), ends only where a data symbol, a startup range, an array or another handed base
     starts, and is no evidence for other bases' neighbours (the earlier entry's (d): GCC makes an
     anchor's variable's address as a literal of its own). A parameter whose memory a walk reached is
     none: memcpy's own object is marked (dread.shapes' conflicts), and the mark now goes along with the
     templates copied onto callers (Shapes.walked, which nothing else in the shapes reads); without it a
     string handed through a stream buffer's functions to an unrolled memcpy made a structure of words
     0 to 60 in ROM on four clang -Os builds. An element's address handed so gives its array's elements
     fields.
  8. An index into static data in the shapes: shapes took r3 + 0x200299fcu (r3 twenty times a
     priority) for a field 512 MB into what r3 points to. **The plan of static data is made before the
     shapes, and an address inside one of its objects added to a value is no offset from a pointer**
     (Shapes.static_offset).
  9. A read of a field's width and the other signedness: (a) the macro, (b) a cast of the member
     ((s16)g_200018de[r6]; never for a store or an operand of ++ or --, which a cast cannot be).
     **(b)**, for structures too.
- **Undo.** plan(indexed=None, handed=None) gives main's structures; an empty `arrays` in the
  readable_types table (or none: older files) gives readable C without arrays.
- **Status.** In the code (branch statics-arrays): docs/todo.md, "Structures of globals".

## The shared names asked --jobs batches at a time: answers settled in the batches' order (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** `dread pilot --task readable` (and `--task globals`) asks the names of the globals
  (40 a question) and of the structures (8 a question) before the variables; one batch after another,
  each answer's names were checked against those the batches before it had taken, so a name two
  batches proposed went to the earlier one. Asked side by side, how do two batches avoid taking the
  same name, and what happens to the others when one fails?
- **Options.** (a) Settle in the batches' order: every question made first, the answers checked and
  written by the main thread in the order of the batches, an answer that ends early waiting for those
  before it. (b) Settle as the answers end, then a final pass that settles conflicts in batch order.
  (c) Show each question the names taken so far (each would wait for the ones before it: no longer side
  by side). (d) Give each batch its own part of the names (a prefix): other names.
- **Choice.** (a). No question depends on another's answer (a batch shows its globals' uses or its
  structures' definitions and uses, made before any batch is asked), and the model is told nothing of
  the refused names either way, so checking each answer against the names of the batches before it is
  what one batch after another did: the table ends byte for byte the same (tests/test_naming.py, with
  answers ending out of order). (b) ends the same after writing twice, (c) is one after another again,
  (d) changes the names.
- **Two rounds.** The structures' questions show the globals' names (struct_uses reads the C with them),
  so they are made once the globals' answers are settled. Made with the rest at the start, they would
  show the default names (g_2000...): one answer's time faster, but other questions than before.
- **A failure.** After a batch the provider failed or declined, no batch is asked that was not asked by
  then (one after another stopped there); the answers of those asked are kept (they are paid for, and a
  run started again asks what has no name yet). With a failure, a run with --jobs N may thus keep more
  names than one with --jobs 1.
- **How to undo.** --jobs 1 asks one batch after another, as before.
- **Status.** In the code (dread/pilot.py: ask_batches, name_globals, name_structs, name_shared).

## How sure names are, in dread gui: levels from the vote, a disc filled by it, the review's marks (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** docs/todo.md's v3-next asked for colours from name confidence rather than name status,
  and the review's suspects marked, readable in light and dark and not by colour alone. Which levels,
  from which numbers; what says it besides the colour; which review rows mark a name; what a function
  without a name in the tables shows.
- **Options and choices.**
  1. Levels: (a) the vote's agreement as a continuous colour, (b) a few levels. **(b)** for the colour
     (an ordinal ramp needs steps a reader tells apart), with the agreement itself as the disc's fill,
     so 0.6 and 0.8 still differ. The thresholds come from the review's own model: sure where every
     answer agreed (any agreement below 1 is a reason the review weighs, and such names were judged
     wrong 22 % of the time against 9 % of all, "A no-confidence vote on existing names" below);
     unsure at or below the agreement whose vote alone reaches the review's threshold (1 - THRESHOLD /
     WEIGHTS["vote"], 0.5); likely between; unvoted for a proposal without a vote (one question; memory
     and structures, which the pilot asks once; a review's more general name). To undo or tune:
     dread.relations SURE and unsure_at().
  2. Besides the colour: (a) an outline style a level, (b) a disc filled as far as the vote agreed, (c)
     a mark a level. **(b)**: how full says how sure in grey too, and carries the agreement itself; a
     dashed outline for no vote, an empty disc for none. In the page's text the disc is an inline SVG:
     the page's Content-Security-Policy (style-src 'self') keeps style attributes out, which first
     dropped the bar's widths and the discs' fill silently.
  3. Colours: one hue (the page's steel blue) from light to dark, checked as an ordinal ramp in both
     themes with the dataviz skill's validator (monotone lightness, steps apart, the light end above
     2:1 on the panel). A diverging amber-to-blue scale, to make unsure stand out, was left: the
     review's marks do that with a glyph, and amber and red stay for states (refused, dropped).
  4. Which rows mark a name: only those whose name after the challenge is the name the function or
     group goes by now (a later naming pass, or a person renaming it, makes them history, "stale"); the
     last of them gives the mark: flagged; suspect after kept or no candidate; changed after changed or
     general, also when a later review confirmed that name; else confirmed. Confirmed shows quietly in
     the list and the pages, not in the graphs.
  5. A function without a name in the tables goes by its proposal in the list, the graphs' labels and
     the function's links, in italics; its FUN_ name stays in titles, the page's heading and the
     address bar. Before, the list and the whole program showed FUN_address only, which tells nothing
     on a stripped image. To undo: shownName() and labelOf() in app.js.
  6. The memory and texts an open group shares: (a) a link for each pair of nodes whose functions touch
     a shared thing, as the closed groups have, (b) each shared thing a node of its own. **(a)**, the
     picture's vocabulary unchanged, bounded by 4,000 pairs (a thing many functions of two open groups
     touch makes a pair for each; things fewer functions touch go first, the rest is counted).
- **Status.** In the code (branch gui-confidence).

## Structures of static data: a base counts where the code reaches its neighbours apart from it (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** Readable C writes memory at fixed addresses as a macro per address and type. Where the
  source keeps its data in static structures (the private real-life test), the code reaches their
  fields from one base plus constant offsets, and readable C should say so (`g_20000030.f_4`, one
  object). But a section anchor looks the same: GCC without -fdata-sections reaches every variable of
  a source file from one base, clang merges a file's globals on RISC-V, newlib's malloc keeps its
  state so. What decides that a base is a structure's, where does a structure end, and how is it
  written?
- **Options.** Evidence: (a) every base whose accesses reach several offsets is a structure, ending
  at the next base, address handed out, array start or data symbol; (b) as (a), with the places one
  base reaches split where no function's view of them overlaps another's (a file's functions seldom
  use all of its variables together); (c) as (a), but a base counts only where one of its functions
  reaches a place next to it (within 64 bytes before it or after its structure) apart from it, through
  another base or none (gp): a compiler that makes a base per object does so for neighbours a
  function uses together, an anchor is the one base its file reaches everything through; (d) as (c),
  counting addresses handed out next to the base, made apart from it, too; (e) a data symbol at the
  base also makes it count. Writing: (i) a macro per structure, `#define g_20000030 (*(volatile struct
  g_20000030_t *)0x20000030u)`, with the structure, fields naturally aligned with byte gaps (as
  dread.shapes' structures), an access of another type than its field left the macro it was; (ii)
  packed structures with casts for the other types.
- **Choice: (c) and (i), with data symbols only ending structures.** Measured against debug information
  on the 39 FreeRTOS builds and two built without -fdata-sections (dread readable --statics --score):
  (a) made 23 and 21 structures on the two anchor builds, 20 on RISC-V clang -Os and one on thirteen ARM
  builds (newlib's malloc state), each over several source variables; (b) left 20 of the 23 and cost
  true structures (one of RISC-V's two lists, GCC -O0's xAutoReloadTimers); (c) keeps all of them out
  and 329 of the 385 structures (a) finds inside one source variable on the 39 builds (ARM 318 of 349,
  RISC-V 11 of 36; lost: newlib's FILE array __sf, xQueueRegistry, xCoreBMessageBuffers, RISC-V's lists
  and xParameters, whose functions use nothing next to them); its window, on the gate's three ARM
  builds: two structures fewer at 32 bytes, four at 16, none at 64, and one anchor run passes at 256;
  (d) lets five runs of each anchor build pass (GCC makes a static's address as a literal of its own,
  .LANCHOR0+20) for one structure gained on RISC-V. (e) would be right with an ELF's complete symbols (gizmo's g_config) and wrong with a person's partial ones, where a
  symbol at the first variable of an anchor's run makes the run one. (i) keeps every access one of its
  width at its address by its declaration (a packed word GCC reads in bytes on ARMv4t where it cannot
  tell the address's alignment, through a pointer; at a constant address it infers it, which is the
  compiler's choice, not the C's); the compiled members are checked against the macros on every gizmo
  build. The cost: what
  the code alone cannot tell stays as it was (gizmo's g_config; RISC-V's lists, whose functions reach
  no neighbour apart from their base), and accesses of another type than the field stay macros.
- **Undo.** In dread.statics plan(), trust="all" counts every base, symbols=False leaves out the data
  symbols; an empty `statics` in the readable_types table (or none: older files) gives readable C a
  macro per address and type again.
- **Status.** Done (dread/statics.py, dread.naming apply_globals and static_names, dread readable
  --check and --statics, dread pilot's globals question): docs/todo.md, "Structures of globals". The
  arrays, the addresses as & and what callees reach came later the same day ("Arrays of static data,
  addresses as &, and what callees reach", above).

## A no-confidence vote on existing names: triggers by code, a candidate, a blind pairwise decision with a margin (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** The user, 2026-10-22: "Could we also have a mechanism for a no-confidence vote on
  existing names? Reverse engineering is like putting together a puzzle. With more and more pieces put
  in the right place, the whole picture emerges. But smaller sub pictures could look right in one
  context but different in the other." The item's design (docs/todo.md) asked for triggers computed
  by code, sub-pictures tested at their edges, a re-decision with hysteresis (a pairwise comparison
  with a margin, "more general" one of its outcomes), every change logged, rounds capped, and names
  people gave flagged, never replaced. Which triggers, what does a challenge ask, which margin, which
  decider, how many rounds?
- **Options, measured** on FreeRTOS GCC -Os (mps2, 366 functions made FUN_address) with three stored
  naming runs of the defaults (Haiku 5.5: votes of five, a second pass, the groups named first), every
  name judged against the source by Sonnet 5.5 (dread.namejudge; 996 names with a source: wrong 93, 9
  %, partly 237, right 666):
  - Triggers: the rate of names judged wrong, and wrong or partly, among those a signal marks: a vote
    below 1.0 22 % and 56 % (207 names), its answers in three readings or more 33 % and 65 % (75),
    another function of the same name 17 % and 58 % (130), a name without its group's prefix 24 % and
    44 % (25), a vague name 0 % and 60 % (15: vague names are partly right). Left out: no direct
    caller (8 % and 51 %) and a small function (17 % and 48 %), which make a name harder, not wrong;
    a name that changed between the passes, and one whose neighbours' names changed after it was given
    (the puzzle's pieces around it moved: 8 % and 29 %), no better than all names; helpers that several
    parts call were wrong less often than all names (12 %, 17 %), so they are a trigger only when the
    name puts them into one of those parts. A source file's path among a function's texts never
    contradicted its name or its group's name in the three runs (no false alarm). The score of the
    reasons ranks wrong names above right ones with an area under the ROC curve of 0.76 (0.68 for
    wrong or partly); challenged from 1.0, 204 of the 996 names (20 %): 48 % of the wrong ones, 32 %
    of the partly right, 12 % of the right.
  - What a challenge asks: (a) the candidate replaces the old name when they differ, no decision; (b)
    a pairwise decision in one order; (c) both orders, neither name marked as the old one, the
    candidate needing a margin. In the first trial a candidate confirmed the old name when alike by the
    vote's measure (half the words): it confirmed all five planted too-specific names whose candidates
    moved them back to their part (task_list_insert against port_list_insert); a confirmation needs the
    same words since.
  - The decider: Haiku 5.5 (the namer) or Sonnet 5.5.
- **Choice.** dread pilot --task review (dread/namereview.py): the triggers above with weights
  (WEIGHTS: the vote 2.0 times 1 - its confidence, a split 0.5, the same name 1.0, vague 1.0, a file
  that does not match 1.0, the prefix 0.6, a shared helper 0.6; for groups the vote, files, a described
  device most of whose users the group holds without naming it 1.0, a described device it names but
  does not touch 0.6, its functions not taking its prefix 0.6; a renamed group's functions 1.0),
  challenged from a score of 1.0; a candidate from the naming question with the old name and the
  reasons, five questions, voted; (c) with a margin of 2 (one question in each order: both must prefer
  the candidate, or both "general"); groups first, a renamed group's functions after it in the same
  round; at most two rounds; a name changes once a review and is challenged again only for a reason
  its last challenge lacked, also across reviews; people's names flagged; the naming provider decides
  unless --decider names another.
- **Why.** Planted errors, on a copy of one run (68 names): three groups misread (the stream buffer
  as a UART driver, the event groups as binary semaphores, the C library's stream layer as a USB CDC
  port), their functions named again by the namer with the wrong group line, votes of five in two
  passes, as a real misreading spreads (it spread to part of them: the code's assertion paths held many
  names to the stream buffer); three pairs of a group's functions with their whole proposals swapped;
  five names made vague, five shared list helpers named after one part that calls them; all rewritten
  in place, so no trigger sees when they were made. The judge found 25 of the 68 worse than the
  original names (with a source; the misread C library has none). The review (Haiku deciding, 391 s,
  749 answers, $0.36) challenged 21 of the 25, changed 20, 16 judged better than the planted name, none
  worse, 15 right afterwards: the misread groups' 9 (9 challenged, 8 changed, 6 better; both groups
  whose texts name their source files renamed back in both orders, the C library's not challenged:
  no text, no described device), the swaps 2 of 6 (the heap's pair, through its vote and in round 2
  the same name; the others gave no reason), the vague 5 of 5 (4 better), the too specific 5 of 5 (4
  better); 4 planted names that were not worse were changed (2 better), and 19 names not planted (6
  better, 1 worse, 12 the same verdict). Against the planted names 24:1 by functions (p 2e-6), 11:0
  by groups (p 0.001). Without planting (the three runs as stored, 4.5-6 minutes and $0.21-0.27 of
  Haiku each): 231 challenges of functions and 14 of groups, of the names judged wrong 48 % challenged and
  18 % changed, of the partly right 32 % and 9 %, of the right 12 % and 2 %; 58 names changed with a
  source, 23 judged better, 6 worse, 29 the same verdict; by functions 6:2, 11:1, 6:3 (pooled 23:6, p
  0.002), by groups 3:1, 7:1, 5:3 (pooled 15:5, p 0.04). Two runs of the same settings differ by
  24:40 and 37:21 by functions and 8:12 and 14:5 by groups ("Two naming runs compared by groups",
  below), so one run's gain alone is within that noise and the three together are not. The
  decisions: 56 of 84 went to the candidate in both orders, 14 to the old name, 8 split by position
  (the model leans towards a side: both orders cancel it). Sonnet deciding (Haiku proposing): on the
  planted copy 19 errors changed, 15 better, none worse, 5 better and 3 worse among the names not
  planted (23:3, by groups 10:2); on the three runs 22 better, 2 worse (by groups 6:0, 6:0, 4:2;
  pooled 16:2, p 0.001); for $0.48-0.92 a review against $0.21-0.36, and the judge is Sonnet too (a
  shared taste would count as a gain). Over all four copies 45 better and 5 worse with Sonnet, 47 and
  7 with Haiku: alike within the noise, so the default stays the naming provider and --decider is
  there to choose. Spent on the measurement: $4.20 of Sonnet judging, $1.83 of Sonnet deciding, $2.64
  of Haiku.
- **Not established.** A misreading the tables do not show (receive for peek, two siblings' names
  swapped, a group that points to no source file and uses no described device) is not challenged:
  the triggers rank, they do not find every wrong name, and half of the wrong names had no reason
  against them. The planted kinds were chosen as real runs go wrong, but the triggers were made for
  them; on firmware without assertion paths the file trigger does not fire. Names people gave: the
  flagging is tested on gizmo only (FreeRTOS's stripped build has none); not tried on the private
  firmware, whose names are mostly people's. One measurement each: the review's own spread between two
  runs is not measured (one decision, task_is_suspended against task_is_valid_for_resume, went both
  ways in two trials).
- **Undo.** Leave --task review unused; a review's changes are the names.tsv rows marked `, review`
  and the subsystems.tsv rows it appended (review.tsv lists them), and `--upto-pass` with the last
  naming pass scores the names before it.
- **Status.** In the code (dread/namereview.py; dread/pilot.py --task review, review_task, ask_name's
  own; docs/formats.md review.tsv).

## Readable C's frames: overlapping accesses in anonymous unions, a region without fields as bytes (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20; the item in
docs/todo.md, Frames: the rest).

- **Question.** dread.frames left a function raw where accesses in the region that frame addresses
  reach overlap (two types at one offset; a word and its upper half, where GCC -Os pushes a word to
  make room for a 16-bit local whose address it hands on, so that the push reaches into the region
  from below), and wrote a region the function accesses at no constant offset as a structure of
  padding. How are these written, and where does such a region start?
- **Options.** For overlapping accesses: (a) an anonymous union (C11) for each run of overlapping
  accesses, a member each, one that starts later wrapped in a packed structure behind bytes of its
  own, members named f_<offset> as the plain fields are, f_<offset>_<type> where several types share
  an offset; (b) named unions (stack28.u_0.w, stack28.u_0.h.f_2): every access longer, and the name
  of a member changes when another access comes to overlap it; (c) one type per offset, the others
  reached by casts ((s8)stack12.f_3, as frame_locals does for a slot read with both signs): a cast of
  a wider member reads all of it, not the narrower access the original made, and reaches no part of
  it at another offset; (d)
  leave out the push's store as dead: in vPolledQueueConsumer the upper half it writes is read where
  the receive did not overwrite it, so it is not dead. Where the region starts: (e) at the push that
  reaches in, moved down until no access reaches in, frame_locals making variables only below that
  (region_start); (f) at the lowest address that leaves, the reaching word split: no C access can
  write a part of a word of the original's in one store. For a region without fields: (g) `volatile
  u8 stackN[N]` aligned as the region's start, addresses (u32)stackN and (u32)&stackN[k], an index
  added to the address; (h) as (g) with an index as stackN[i]: C makes an index past the array
  undefined, and GCC derives loop bounds from such accesses (-faggressive-loop-optimizations), where
  the lifted C's arithmetic on the address had no such case (clang -O2 prvReceivingTask's index into
  its 20 bytes reaches 20 when its assertion returns); (i) an array of the element the accesses
  through an index suggest: only bytes are indexed in the eight builds, and a wider element's index
  would need a shift the original's address arithmetic does not have.
- **Choice: (a), (e) and (g).** Each access stays one access of its width at its offset: packed
  throughout, the object aligned as a whole, so the compiler knows each member's alignment from its
  offset (checked on the compiled union and array with GCC for ARMv4t, which has no unaligned access,
  clang for Cortex-M3 and GCC for RV32: str, strh, ldrh, ldrb where the members lie). (a) keeps every
  access as short as a plain field's and its name as it would be without the overlap. (e) is what the
  region means (what the addresses handed out may reach, and the push writes part of it) and fixes a
  fault of frame_locals beside it: a slot below the address that leaves but inside the reaching word
  became a variable, and its read found what its own store wrote, not the word's. With it, an index
  added to a frame address counts from the address its constants come to (frame_index: RISC-V's addi,
  add and a load with a negative offset reach sp0 - 52 by way of sp0 - 32) as well as from the frame
  addresses written in the sum, which were counted before, so a region starts no higher than before;
  the second fault of frame_locals found, a slot such an index may reach made a variable, goes with it.
  Neither fault changed a function of the eight builds. dread.frames writes such a sum from the address
  its constants come to ((u32)&stack52 + s0_1), only where every term is 32 bits wide or narrower (a sum
  of unsigned 32-bit values, whatever its order).
- **Undo.** dread/frames.py: Region.groups returning each access alone, and plan refusing two types
  at one offset or an overlap, as before; region_start returning low unchanged (in dread.tidy, which
  both use); Region.array returning False writes the structure of padding; frame_sums finding nothing
  and the frame_index branch of frame_struct's rewrite out take the sums back.
- **Status.** Done; the gate accepts the same functions as before on the eight builds (docs/todo.md,
  Frames: the rest).

## Readable C's structures: kinds joined through what pointer fields hold, a void * by evidence (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** dread.shapes' kinds stayed fragmented: a list reached through an item's container
  was a kind of its own, and a task block reached through an item's owner held nothing. Joining kinds
  through pointer fields would also join what a void * holds: FreeRTOS's pvOwner holds task blocks
  for some lists and timers for others. Which rule, decided by code, joins what one pointer field
  holds and only that?
- **Options, measured** on four FreeRTOS builds (pointer fields against the source's pointers to a
  structure, the plan's fields of typed parameters, members, structures; docs/todo.md, Types):
  (1) every field's targets one kind, unification of kinds: joins task blocks and timers through the
  owner (on RISC-V GCC -Os the timer's six fields are its held item's, all within the task block's
  layout). (2) A field's targets one kind where their layouts fit together: fit alone tells neither a
  timer from a task block there nor a list from an item (five words each). (3) As (2), and a field
  holds several kinds where it points back to the structure that holds it in a structure two kinds
  hold, or where two of its targets are kinds one function takes as different parameters; there a
  target joins the one kind it may hold whose layout holds all its fields, where exactly one does,
  and the field is no pointer. (4) As (3), fields also read through puns (a list's end marker at 8,
  walked as an item by one pointer): it joined lists with items until (3)'s parameter rule kept them
  apart, and then found fewer right pointer fields (clang -O2 39 -> 35, RISC-V 27 -> 23); puns used
  only to type a field that points to a list's end marker as an item pointer changed nothing on the
  four builds (the unification never meets an item there with the end marker: vListInsert's walk
  reads every item as the end marker itself), so a field that also points to a list's end marker
  stays an integer (an item's next; on GCC -Os and -O0 the list's links too). (5) As (3),
  two kinds parameters make in one field, neither's layout within the other's, held as several: no
  difference on the four builds.
- **Choice: (3).** A field's targets are read at structures' bases, through the structures their
  kinds hold (a task block's field at 0x14 is its item's container at 0x10); memory with fields in
  front of its base lies inside a larger structure and says nothing (a task block reached at its
  event item, which had made the items' kind the task block's). The owner is several
  on GCC -Os though only the task blocks' own stores lift there: items are held by task blocks and by
  timers, and a field that points back to its holder holds whichever holds it. Pointer fields that
  are the source's pointers to a structure: GCC -Os 9 of 10 -> 18 of 21, clang -O2 9 of 13 -> 39 of
  46, RISC-V 4 of 8 -> 27 of 27, GCC -O0 6 of 7 -> 12 of 16 (the wrong ones: newlib's _cookie,
  StaticQueue_t's dummies, void * in the source, that hold the queue's lists' items and the mutex
  holder in fact; on GCC -Os and -O0 the owner of a second family of items, which no slot joins to
  the first; GCC -O0's pxStack, an array read at four constant offsets); the source's pointers to a
  structure left integers 30 -> 21, 52 -> 16, 39 -> 13, 39 -> 30. (5) is left out (nothing measured
  it), the parameter rule kept: it is what keeps lists and items apart when a field holds both, and
  it also keeps one place two kinds are held at from joining them (no difference on the four builds).
- **Values first, each traced.** What made fields point where the source does not: a use the
  callee does nothing with joined the definitions that reached it (clang -O2's shared tail return
  vPortExitCritical(r0): a task block's priority took its list container's pointer); RISC-V's ecall,
  whose asm reads a0 to a7, did the same (a list's count took its container's); GCC stores NULL with
  a register it has just found 0 (the mutex holder took pcHead's value), and returns a register a
  branch found 0 from the return that hands back a pointer elsewhere (GCC 9 -Os: a list's count took
  a task block's pointer; definitions now do not reach past an edge that says their register is 0);
  a stack argument read through its address was nobody's value (prvInitialiseNewTask's task block on
  ARM); and parameters that end as one value, the static queue's storage and the queue through
  pcHead, passed one template
  to both (the storage took the queue's fields; at GCC -O0 a dynamic queue's storage behind it took
  them again). Keeping such parameters two values would take points-to sets instead of unification;
  the rule taken passes on the templates of the parameters the function uses as a pointer itself, and
  the value points to no one structure. Templates also carry what their memory holds (the calls that
  handed it at an offset), so memory copied from a callee holds the callee's items too.
- **Undo.** dread/shapes.py: same_slots() not called in kinds() joins nothing through fields (and
  makes no void_slots); Collector.taken returning None merges at every use; known_zero returning {},
  g.reaching() for reaching_past_zero in Collector.webs and address_taken returning set() take the
  others back; the groups in analyse() pass every template again; templates' fourth part, what their
  memory holds, empty takes that back.
- **Status.** Done (dread/shapes.py; tests/test_shapes.py; docs/todo.md, Types).

## Lifted C's frames: the words only the saves and dead restores touch are left out (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20; the item in
docs/todo.md, Compiler diversity).

- **Question.** The lifted C kept the original's frame as a local array, the words the prologue
  saves the caller's registers into among them, and the C compiler saves the registers its own code
  uses beside it: on FreeRTOS's RISC-V builds most lifted frames grew by exactly those saves (clang
  -Os: 173 of the 241 lifted functions, 4,768 bytes), a task's stack overflowed with two of them in
  C, and the lift check keeps such functions out since the whole image is searched (lift.culprits).
  Which words may the C leave out, and how is that kept from changing what the C does?
- **Options.** (a) Keep every word, as before. (b) Leave out the words at the frame's top (right
  below the entry's stack pointer) that only whole-word stores and whole-word loads touch, each
  load's register read by nothing behind it (or made without memory: the return address as the
  builtin), where no address of the frame may reach them; the translation skips those stores and
  loads, and the array ends that far below sp0. (c) As (b), also where a frame address goes to a
  callee: a callee reaches past what it was given only through a fault of the original (a length
  past a local), and the cases already refuse an input that makes code touch saved registers
  (saves_touched; most of what still grows after (b) would shrink). (d) Have the C compiler reuse
  the original's slots: no compiler offers it.
- **Choice: (b).** It takes away the cause where the analysis can show that nothing needs the
  words, and stays where it cannot. Why it is safe:
  1. Nothing the C computes needs a word left out: every load of it is a restore into a register
     that no path reads before writing it again (restore_dead: a return reads the result registers,
     a call its arguments and what its callee reads of its caller's registers, a jump through a
     register the arguments, a tail call the arguments; a pop that returns loads only the result),
     or the return address the C takes from the builtin. So its stores are dead as well, and the C
     without them and without the loads computes the same.
  2. Nothing else reaches it: every register that may hold a frame address is followed forward on
     every path (sp, copies, arithmetic, writeback; a load writes what memory holds, and a frame
     address would have been stored first, which is refused); an access through one must lie where
     the states know (one frame address on every path, no index), and none may be stored, handed
     to a call, returned or read by an instruction that runs as itself in inline assembly
     (frame_uses: else FrameReach, and every word stays). A call's stack arguments and the lowest
     frame words a call through a pointer is handed are reads of their own: such words stay.
  3. The words left out are a run from the top: the array stays one block, its start as aligned as
     before (sp0 = the array's start + the original's frame rounded as before), so every word kept
     lies where it lay relative to sp0 and to the alignment. A wrapped function's frame is the real
     stack and keeps everything.
  4. No comparison changes (rule 6): the cases compare the result, the registers handed back, the
     kept scratch registers, every write outside the stack (and the room for stack arguments above
     the stack top), stub calls and device accesses; the stack below the caller's stack pointer is
     not compared anywhere (C lays out its frames otherwise), and the checks that look at it run the
     original alone (stale stack data read, a stack address taken for a value, saved registers
     touched by code other than a push, pop, save or restore: a case where that happens is refused
     with the words kept or left out alike). The system run compares device logs and model reports,
     as before.
  5. Not covered: code that reaches the frame through an address it makes itself (a callee writing
     past its own frame, a pointer computed from the stack's top) writes there into what is now the
     C compiler's own data instead of a word nobody read. Not seen on the benchmark; the cases and
     the whole image decide, as for anything else C does otherwise.
- **Undo.** Make Lifter.saves_out return 0: every lifter then writes the same C as before for every
  function (the frame declarations, pushes, pops, saves and restores are as they were whenever
  nothing is left out).
- **Status.** Done (dread.lift: saves_out, frame_uses, restore_dead, c_uses; each lifter's
  frame_effects and results; tests on ARM, RISC-V and MSP430). On copies of six benchmark builds
  the frames larger in C fell from 173 to 56 of 241 (RISC-V clang -Os), 171 to 57 (clang 14), 191
  to 51 of 259 (RISC-V GCC -Os), 225 to 163 of 330 (M3 GCC -Os), 127 to 98 of 320 (M3 clang -Os) and
  275 to 185 of 358 (M3 GCC -O0); the lift check accepts the same functions on four and two more on
  the clang RISC-V builds (the two the stack search dropped), every whole image agreeing; the
  readable gate accepts the same functions on its eight builds (docs/todo.md, the item). Option (c)
  would shrink most of what still grows (on clang -Os 45 of the 56 frames keep their saves for an
  address handed on); it is not taken without an argument that a callee cannot reach them.

## The groups' evidence from the listing, not the readable C (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** `dread groups` read what each function touches from its readable C (dread.relations
  Facts): 0.1-0.5 s a function, 6-32 s for a FreeRTOS build on 8 workers with no facts kept and 1-5 s
  with them (each function lifted to check its facts' key), more in proportion on a larger firmware;
  paid again after any change to DREAD's code (the kept groups' key holds every module) before `dread
  gui`'s first whole-program view and before every `dread pilot --task name --subsystems` (the
  default). Can the facts come from something much cheaper with the same groups?
- **Options, measured** on 38 FreeRTOS builds (27 ARM: Cortex-M3 and M4F, GCC 4 to 16 at -Os, -O2 and
  -O0, clang 12 to 23 at -Os and -O2; 11 RISC-V: GCC 10 to 16 at -Os and -O2, clang 14 to 23 at -Os),
  against the source file of each function (DWARF) and against the readable C's groups (the adjusted
  Rand index of one grouping against the other: agreement), each from a cold cache on 8 workers:
  - (a) the readable C, as before: the index 0.801 on ARM, 0.829 on RISC-V (means over the builds);
  - (b) the addresses the code makes alone (dread.relations materialized: literal pools, addresses
    from the pc, movw + movt; what the pilot's device texts read): ARM 0.005 lower (5 builds higher, 21
    lower), RISC-V 0.18 lower (GCC's lui pairs apart, its stores, gp-relative accesses unseen);
  - (c) a forward pass of the constants registers hold over each function's control flow
    (dread.relations flow_references with the arch layer's value_step: literals, adr, mov, movw, movt,
    add, sub, orr, lui, auipc, addi, the target's fixed gp; a load or store at a known base plus an
    offset; a constant plus an unknown index; the scratch registers ended at calls), naming the
    addresses the code makes and the values it stores, passes or returns: ARM 0.004 higher (20 higher,
    5 lower, a sign test p 0.004; with 2 % of the evidence dropped at random, 23 higher, 4 lower),
    agreement 0.98 (at least 0.93); RISC-V 0.036 lower: with clang as high (-0.01 to +0.05), with GCC
    -Os 0.06-0.11 lower, -O2 0.015, agreement 0.91 (at least 0.79). 0.06-0.5 s on 8 workers (in one
    process 0.5-2.3 ms a function, 8-30 us an instruction);
  - (c) without the values that are a constant plus an index: ARM +0.005, RISC-V -0.031; naming only
    the values a function stores, passes or returns, not every address it makes: ARM +0.002, RISC-V
    -0.028 to -0.047. The variants are within the scores' noise of each other: 2 % of the evidence
    dropped at random moves the index by a standard deviation of 0.002-0.014 on ARM, 0.005-0.043 on
    RISC-V, and the readable C's own index on the RISC-V GCC 16 -Os build spans 0.66-0.81 between
    resolutions 1.8 and 2.2;
  - (d) the second lever, a key over only the modules the facts depend on: the facts kept per function
    have one already (dread.relations PIPELINE, eight modules, regflow's key, the function's lifted
    C); a change elsewhere costs each function's lift, a change of the readable C's own modules (often)
    or of regflow's every function's readable C again. The kept groups' key holds every module because
    the import closure of dread.relations is 61 of DREAD's 84 modules: a narrower key would be a guess.
  - MSP430 (one build of 98 functions; no value_step yet: the addresses the code makes): 0.403 against
    0.386 from the readable C.
  What (c) and (a) find, as function-thing pairs over the 38 builds: texts the same (10,081), device
  blocks 1,115 alike (3 and 2 apart), globals 27,425 alike, 368 from the readable C alone (mostly
  addresses that differ by path, which the readable C names where each path sets them and the pass
  loses where the paths join: clang's unrolled loops, newlib's stdio set-up) and 1,113 from the
  listing alone (constants only compared, accesses the readable C keeps behind a variable,
  gp-relative ones in assembly the lifter refuses); the readable C's 46 tables are literal pools'
  slots of functions it could not read, whose words (functions' entries) the listing counts as taken
  (56 more).
- **Choice:** (c) with the indexed values and every address the code makes named, the variant closest
  to what c_references reads from the C, as the default (dread.groups EVIDENCE "listing"); the
  readable C stays an option (`dread groups --evidence readable`), and the kept record says which and
  holds it in its key. A layer without value_step (MIPS's delay slots, MSP430's addressing modes: not
  written yet) gives the addresses its code makes. dread gui starts `dread groups` without waiting for
  the register analysis, which the listing does not need.
- **Why.** As good on ARM and on RISC-V with clang, and 20-50 times faster: 0.3-0.5 s for a FreeRTOS
  build, the whole command, against 7-23 s cold (four builds); the time is what kept the groups from
  the GUI's first view and from every pilot run. The loss on RISC-V GCC -Os is traced: the listing
  sees one array field, the ready lists' pxIndex (pxReadyTasksLists[i] + 4), in 11-13 of tasks.c's
  functions, where the readable C keeps the element's address in a variable it reuses and so reads no
  access at a constant plus an index. With that one thing taken from the listing and everything else
  from the readable C, the readable C's index falls as far (GCC 10 0.786 to 0.672, the listing's
  0.677; GCC 12 and 13 0.761 to 0.691; GCC 14 0.770 to 0.699; GCC 16 0.778 to 0.738). The evidence is
  right and the grouping at resolution 2 splits tasks.c around a dense clique: of the weight the
  shared things put between two functions with a source file, the listing's joins one file more often
  in every family over all the builds (ARM 91.9 % of 11,262 against 91.6 % of 10,943; RISC-V clang
  92.4 % against 92.1 %; RISC-V GCC 90.5 % of 1,536 against 89.9 % of 1,476). That is a matter of the
  method (docs/todo.md, The groups' sensitivity); counting things with the same users once was worse
  for both.
- **Undo.** EVIDENCE = "readable" in dread/groups.py, or `--evidence readable` for one run.
- **Status.** Done (dread/relations.py flow_references, the value_step of dread/arch/arm.py and
  riscv.py with dread.arch.sum_value, dread/groups.py evidence and the record's version 2, dread gui's
  groups thread). Not measured on the private real-life firmware (its groups from both sources and the
  times), nor beyond FreeRTOS's 38 builds and one MSP430 build.

## Two naming runs compared by groups (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** The pairwise comparison of two naming runs (`dread pilot --score --against`) gave a
  sign test over functions. Two FreeRTOS runs with the same settings came out 24:40 and 37:21 (p 0.06
  and 0.05), as uneven as the comparisons that decided settings. Which test should decide?
- **Options.** (a) The sign test over functions, as before. (b) The same over groups: a group counts
  for the run whose names it judged closer more often. (c) Replicate runs of each setting, compared
  by their means.
- **Choice.** (b) in every `--against` (the run's groups.tsv, the grouping its groups were named for,
  else the truth's source files), printed beside (a) and named the one to trust; (c) for a setting
  whose effect is small, as the next step.
- **Why.** A run's names hang together within a group: the group's prefix and the model's reading of
  it go into all of its functions, so a run that reads one group differently wins or loses many
  functions at once (one group made 11 of the 20 that one comparison lost). By groups the two
  same-setting comparisons were 8:12 and 14:5 (p 0.50 and 0.06). Recounted from the stored judgments,
  the earlier decisions hold (votes 35:7, the gate 27:8, the groups 22:3 and 20:3), and one does not:
  the grounded judge's gain from the groups, 15:6 by groups (p 0.08).
- **Undo.** Read the line over functions, which is still printed.
- **Status.** In the code (dread/pilot.py: pair_groups, group_sign, compare_names). Between two settings
  replaced on 2026-10-23 by replicate runs, decided over runs (the entry "Two settings of the naming
  compared over replicate runs"): one run against one shows where two runs differ, not which setting is
  better.

## What the image is, in front of every naming question (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** The pilot's naming questions said only "a binary image". On a private firmware
  (DREAD's real-life test) the subsystem names called several large groups after a kind of device the
  image is not for, with high confidence, and the same questions asked again guessed another kind for
  one of them. A reverse engineer always knows what device a firmware is for. Should the questions
  say it, where does it come from, and how much do they say?
- **Options.** (a) Nothing, as before. (b) A person states it in the target. (c) A model reads it
  once from the texts the image's code points to, and every question begins with that reading.
  (d) As (c), with a sentence on what the program does as well.
- **Choice.** (b) where a person stated it (`[llm] device`), else (c); not (d). `dread pilot --task
  device` makes the reading (five answers, the device most of them agree with, in device.tsv) and the
  naming tasks (name, readable, globals, subsystems) begin each question with it unless `--no-device`.
  The reading is a task of its own, asked once, not before every naming run: the naming tasks' tests
  script every answer, and a run that does not know the device says so on stderr.
- **Why.** The private firmware's texts named its device at once (every answer agreed). With the line,
  the groups in question were named after parts of the right kind of device, and the others stayed
  as they were. With the program sentence as well, the model named groups after the parts that
  sentence mentioned where they were other parts: the details primed it, the device alone did not.
  The question asks for the device alone, the product or board, not what the program does or its
  operating system: asked more openly, FreeRTOS GCC -Os read as "Cortex-M3 microcontroller board
  running a FreeRTOS demo/test application", and that run's names put "demo" into kernel functions'
  names (egdemo_set_bits for vEventGroupSetBitsCallback). Asked for the device alone, FreeRTOS reads
  as unknown (five of five: its texts name no product or board), so its questions stay as they were;
  the private firmware's reading stayed the same. The reading costs a few thousand tokens an image.
- **Measured on FreeRTOS GCC -Os** (the defaults, Haiku 5.5; Sonnet's grounded pairwise judge, where
  two runs' names differ): the run with the open reading against one without the line 25:45 by
  functions (sign test p 0.02); but two runs without the line differed as much (24:40 and 37:21, p 0.06
  and 0.05), and the same run won against another one without the line (45:32). Counted by groups
  (dread groups: a group's names hang together through its prefix and reading), 6:11, 8:12, 14:5 and
  15:8, none beyond chance (p 0.06 to 0.50); one group made 11 of the 20 that the first comparison
  lost. So no effect on FreeRTOS's function names is measured either way.
- **Measured on the private firmware** (2026-10-23, the defaults with the line against the run before
  it without): the groups named after the wrong kind of device were all named for the right one, and
  the function names were judged closer to the true ones in more groups than not, beyond chance by
  groups (the test to trust between runs); the judge also called fewer of them different, and the
  names it was surest of most so.
- **Not established.** A reading can be wrong (the texts of a library, a vendor's sample code), so the
  line says "likely, not certain", and a person's statement wins; a firmware whose texts name no
  device gets no line (FreeRTOS).
- **Undo.** `--no-device`, or remove device.tsv.
- **Status.** In the code (dread/pilot.py: device_task, known_device, about).

## Function names scored against their source: a judge that reads it, the bench's main measure (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** The user, 2026-10-22: "Is it good to judge the names by equality? There are probably
  many ways to call the same things with different styles of naming that are all valid in their own
  way. Is there a better way to judge the results of the pilot naming?" Every measure until then
  compared a proposal with the one true name: word scores (exact, the same words, overlap), a judge's
  verdict per pair (`--judge`: same, close, different) and the pairwise judge between two runs
  (`--against`: which proposal is closer to the true name). Which measure says best whether a name is
  right whatever its style, and which becomes the bench's main one?
- **Options, measured** on the names stored from eight runs on FreeRTOS GCC -Os (mps2, 366 functions
  made FUN_address, Haiku 5.5 naming: one question in one pass, twice; votes of five; votes with a gate
  and four passes; votes and a second pass; the same on newer code, the defaults before the groups; the
  groups named from the first pass's names; the groups named from code, the default since that day),
  every measure on every run: (a) word scores: the overlap as before, and words whatever the style
  (dread.naming.style_words: a camelCase name's Hungarian head dropped, runs of letters cut into known
  words, abbreviations and a small table of synonyms made one word, a word's forms one stem); (b) the
  judge against the true name (`--judge`, its stored verdicts); (c) a judge that reads the source:
  each function's definition (DWARF's file and line, cut to its closing brace, the names of the
  functions shown masked), a source file's functions to a question, says of each proposal right,
  partly or wrong with a reason: first the name and its sentence judged as one, then the sentence
  judged apart, then the names alone; Sonnet 5.5 judging, and Haiku 5.5; (d) a forced choice: the
  proposal (alone, or with its sentence) and five functions of its source file by their true names,
  shuffled, the judge picks the one it describes; accuracy against the 20 % of chance, two draws.
  - Rankings: (c) ranked the eight runs alike in each form judged on all of them (the sentence with
    its name and apart, Sonnet and Haiku; Kendall's tau 1.0), in the order the pairwise judge had
    found (the two controls, votes, the passes gated and not, the defaults before the groups, the
    groups from the first pass, the groups from code); (b) the same on the seven it has verdicts for;
    (a) 0.86 (overlap and style words both put the groups from the first pass above those from code);
    (d) 0.64-0.78.
  - The judge's own noise: (c) judged twice, the same verdict for 88-92 % of the functions, the run's
    mean within 0.012 (paired p 0.34 or more); two runs of one setup +0.011 +- 0.019 (p 0.86). (d):
    two draws of one run differ by up to 0.055 (p 0.03), and with the sentences the two identical
    controls by 0.057 (p 0.006): a difference that is not there.
  - Resolution, paired by function with a sign test, (c) with the sentence apart: the groups from code
    against the defaults before them +0.047 +- 0.019 (p 0.01), against one question in one pass +0.167
    +- 0.023 (p 3e-12). (b) for the first: +0.122 (p 2e-11), the pairwise judge 153, 51, 81 (p 5e-13):
    most of what the judges against the true name saw was the names taking the source files' words
    as their prefixes (task_ for tasks.c, as vTask...), not names more right. (b) also put the gated
    passes above the ungated (+0.046, p 0.002), (c) did not (+0.000).
  - What a name gets: of 1,371 proposals (c) judged right, 187 share no word with the true name:
    xPortPendSVHandler as sched_context_switch, pvPortMalloc as heap_alloc,
    prvCheckTasksWaitingTermination as task_reap_deleted_tcbs, printi as fmt_write_integer; (b) called
    73 of them different. Words alike can be wrong: uxTaskGetNumberOfTasks as task_get_tick_count,
    xQueuePeekFromISR as queue_receive_from_isr (it leaves the item).
  - The sentence: judged as one with its name, a wrong detail of the sentence made right names wrong
    (vStartQueueSetTasks as queue_set_test_start: "suspends the sending task rather than deleting it";
    Reset_Handler as an entry named after the reset). Judged apart, 12 wrong names became right and 160 partly, 285
    partly ones right; names' means 0.59-0.77 against 0.53-0.70; the sentences 0.50-0.62, their gain
    from the groups smaller (+0.015 against the defaults before them, p 0.41). The names alone (two
    runs): 0.53 and 0.72, the same verdict as beside their sentences for 74 %.
  - Per function, how well a measure tells a name (c) calls right from one it calls wrong (the area
    under the ROC curve): style words 0.88-0.90, overlap 0.84-0.86, the same words 0.67-0.70, (b) 0.93,
    (d) 0.65-0.71, Haiku as the judge of (c) 0.97-0.98 (style words leave 95 right names at 0, overlap
    187).
  - Costs at Sonnet's $2 and $10 a million tokens, 332 functions with a source: (c) about $0.80 for a
    run alone (320,000 tokens in, of them 270,000 of source, 18,000 out with the sentences apart),
    about $0.35 for each run judged with it (the sources read from Anthropic's prompt cache: twelve
    judgings $4.67); Haiku as the judge about $0.05; (b) about $0.05 a run, the pairwise judge about
    $0.13 a pair; (d) $0.11-0.18 a run and draw; (a) nothing.
- **Choice.** (c), the sentence judged apart, Sonnet judging: the bench's main measure of names,
  `dread pilot --score TRUTH --grounded PROVIDER` (`--names-only` for the names alone, `--source DIR`
  where the files moved), and with `--against OTHER` function by function between two runs (the
  proposals that differ judged, the better verdict closer, a sign test). (a)'s style words in every
  score beside the old word scores, which stay for comparisons with earlier runs; (b) and the pairwise
  judge against the true name stay for a target without its source (a private firmware, its people's
  names the truth). (d) is not built.
- **Why.** It is the only measure that asks what the user asked: whether a name says what the
  function does, not how near it comes to one valid name; it ranks the runs as the pairwise judge did,
  its noise is small against the differences between runs (a paired difference of 0.047 at p 0.01),
  and it does not reward taking the source's conventions. The sentence apart because judged together a
  wrong detail made right names wrong; its own verdict comes for a few cents more (a second measure,
  noisier: two judgings of a control 0.024 apart, p 0.04). Sonnet rather than Haiku: Haiku ranks alike
  and agrees closely for a fifteenth of the price, but it judged the names of its own family (the namer
  is Haiku) and writes six times the reasons. The forced choice is noisy, saturates (names judged
  wrong still pick their function half of the time) and found a difference between two identical
  setups.
- **Undo.** Nothing changes without `--grounded`: `--score` scores as before (style words added
  beside), `--judge` and `--against` with `--judge` as they were.
- **Status.** Done (dread/namejudge.py, dread/pilot.py score_names and compare_names, dread/naming.py
  style_words, dread/llm.py cached parts; docs/formats.md). Not confirmed: the judge against a person
  (a sample of 50 functions to mark was made outside the repository, it holds FreeRTOS's code; no
  marks yet); on firmware the judge knows less than FreeRTOS (its reasons name FreeRTOS's functions);
  the true name left in the source (not masked) was not tried. In the measurement each function's own
  name was masked; the tool masks every function's of the question, so that a call does not show it.
  Through the tool on the groups from code (the same day): names right 65 %, partly 27 %, wrong 8 %,
  mean 0.783, sentences 0.623 (the measured form's two judgings 0.770 and 0.764, 0.619 and 0.617; the
  same verdict as those for 84-86 % of the names, as each other for 92 %), $0.88; against the
  defaults before the groups (`--against`, 251 names that differ, four questions at a time): closer
  68, 34, ties 149 (p 0.001), $1.07 for both runs, 195,000 of their 477,000 tokens in read from the
  cache. The vote's confidence is calibrated against it too: wrong 4 % of the names of 1.0, 21 % of
  0.8, 47 % of 0.6.

## The graphs' motion and the whole program in dread gui (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** The user asked for graphs that lay themselves out "in a nice wobbly way" and for a
  graph of the whole program. That left several choices open:
  - what a dragged node does when released;
  - where the groups are made, and how they are kept;
  - what a group is called where the pilot has not named it;
  - what draws thousands of nodes.
- **Options and choices.**
  1. A released node: (a) settles again with the others, or (b) stays pinned where it was dropped.
     **(a)**. The picture stays the balance of its forces, so where a node lies means something. A
     pinned node would need a way to unpin it, and would bend the layout round a place chosen for no
     reason the tools know. The function in the middle of the relations glides back to it. With
     prefers-reduced-motion a dropped node stays where it is, since nothing else moves then. To undo:
     in app.js's pointerup handlers, leave the node fixed (`sim.fix`) instead of freeing it.
  2. The groups for the GUI: (a) made one function at a time in the server's facts thread, (b) by
     `dread groups` in a process of its own, or (c) by a pool forked from the server. **(b)**. The tool
     computes and the page shows, and its pool uses every core: FreeRTOS cold took 16 s, against an
     estimated minute or more on one thread (0.1-0.5 s a function). (c) forks a process that has threads;
     a thread's lock can hang the child, and the suite takes the warning for an error. The groups are
     kept in BUILD/groups under a key over all of DREAD's code and the image's tables, so a second look
     costs 0.09 s instead of 2.8 s. The key is conservative: any change of DREAD makes them anew, because
     what the facts depend on is most of the package.
  3. A group the pilot has not named: its most common name prefix, unless another group has the same
     one, in which case `group N` (on FreeRTOS ten demo groups would all have said `prv`); a group of one
     function, that function's name. Words inside names, weighed by how rare they are elsewhere, would
     name more groups; that is not tried.
  4. The relations stay SVG: at most 400 nodes, styled by CSS, with markers. The whole program is a
     canvas, for thousands of nodes. One simulation serves both (dread/web/sim.js): every pair below
     600 nodes, Barnes-Hut above. A group's links beyond the 400 heaviest are hairlines: in software
     raster the thick links between 221 groups had taken the frame rate from 56 to 15 fps.
- **Status.** In the code (branch gui-wobble).

## Readable C's structures: what one holds inside another as members (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** dread.shapes knew where one kind of memory holds another (a call hands outer + 4 to
  a function that takes an item: FreeRTOS's task block with its two list items at 4 and 24, a queue
  with its lists at 16 and 36), but readable C declared every structure flat (the block's f_8 for
  its first item's next) and wrote the item's address as (u32)r0 + 4. How should readable C use it,
  and what decides the held structure's fields and pointer fields?
- **Options.** (1) The held structure a member of its holder (struct s_7 f_4;), the holder's
  accesses inside it p->f_4.f_8, its address &p->f_4; or (2) flat fields named after the member
  (f_4_8): no nesting, but no C type for a pointer to the item either. For the held structure's
  fields: (a) its own kind's fields only; (b) also what the holder accesses inside it. For its
  pointer fields: (i) the objects of every kind that holds it count, as the objects of its own kind
  do; (ii) only its own kind's objects decide, as before.
- **Choice: (1), with (b) and (ii).** (1) is how the source declares it, and a pointer computed from
  the holder (r5 = r0 + 4, then r5->f_c) can be typed as a pointer to the held structure, written
  r5 = &r0->f_4. (b): the memory at the block's offset 4 is an item, so the block's accesses there
  are an item's fields; a field that does not fit (another size, across a field, wider than the
  item's alignment) leaves the member out instead. (ii): the holders' fields inside a held
  structure point to objects that callees' templates copied onto them, kinds never joined; counted,
  they made GCC -Os's item link at 8 no pointer (pointer fields against the source 5 of 6 instead of
  9 of 10). A member is declared only where C can hold it: at an offset of its alignment, ending
  where the holder's fields reach, not overlapping another member, no field across its ends (on
  clang -O2 and RISC-V GCC -Os the list's end marker, a 12-byte mini item at 8 that FreeRTOS uses as
  a 20-byte item, had made the list hold an item and grow to 28 bytes; then a queue could hold only
  one of its lists). The pun still types a pointer to the end marker as an item pointer, as the
  source declares pxIndex. A late offset (v = w + 4 met after a call gave v an object of its own)
  is read as one memory inside the other too, in both directions (w - 4 from a member is its
  holder). In comparisons a member's address meets only a pointer to its structure, never 0 (a
  compiler may take the address of a member for never null). In dread.cmetrics a member of a held
  structure (p->f_4.f_8) is one member, and a member's address counts as a member, as the frame
  object's always did.
- **Undo.** dread/shapes.py: embed() not called from plan() leaves every structure flat; the late
  offsets are Shapes.offsets, read in kinds(); held structures' pointer fields from every holder:
  the found loop in plan() through Plan.place.
- **Status.** Done (dread/shapes.py kinds, plan, embed, apply_types; docs/todo.md, Types).

## Function names: each question shows its group's name and prefix, the default (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** With the functions grouped by their code (the entry below), should the questions for
  function names show the subsystem their group was named, and from what should the group's name come?
- **Options, measured** on FreeRTOS GCC -Os (mps2, 366 functions made FUN_address), Haiku 5.5 naming,
  Sonnet 5.5 judging pairwise in both orders, each run on a copy of its own from the same code:
  (a) the defaults until then (votes of five, a second pass callers first); (b) `--subsystems`: each
  group named first from its code (one question a group, votes of five over the prefix; its
  functions' code cut to 24,000 characters, its texts, devices, callers and callees outside it), every
  function's question in both passes showing its group's name, prefix and sentence; (c) the groups named from
  (a)'s first pass (its names in the groups' questions), shown in the second pass only. (b) against
  (a): 153, 51, 81 (p 5e-13), after the first pass 171, 46, 74 (p 4e-18); (c) against (a) 102, 44, 32
  (p 2e-6); (b) against (c) 127, 57, 75 (p 3e-7). Absolute verdicts: (b) the same 52 %, close 37 %,
  different 10 %; (a) 38 %, 42 %, 20 %. The groups' names of (b) against the file most of their
  functions come from: 24 the same, 3 close, none different.
- **Choice: (b) the default** (NAME_SUBSYSTEMS in dread/pilot.py; `--no-subsystems` turns it off).
- **Why.** The largest gain measured on this build since the votes, for 20 % more ($1.14 against
  $0.95 a run): a function sees what its whole group shows (the assertion paths of the others, the
  tasks its group creates), and the names of a subsystem agree (97 % begin with its prefix, against
  37 % without; 98 % share their file's most common first word, against 68 %). (c) is cheaper but
  carries the first pass's misreadings into the groups' names (newlib's stdio became a stream layer
  over UART ports). The cost of consistency: a group that mixes files gives its prefix to the few
  that do not belong (the demo's printf helpers and strcmp became demo_), and a wrong group name would
  spread the same way; consistency is not correctness (docs/todo.md, the no-confidence vote).
- **Undo.** NAME_SUBSYSTEMS = False, or `--no-subsystems` for one run; subsystems.tsv and groups.tsv
  can be removed (a run with groups names them again).
- **Status.** Done (dread/pilot.py name_subsystems, subsystem_question, Namer's SUBSYSTEM_LINE,
  score_subsystems; docs/formats.md). Measured on one build the model knows, whose assertion texts
  name the source files: not confirmed on firmware it does not know (next: the private real-life
  test, docs/todo.md Function names). Its gain in what the names say, left open by the grounded judge
  on 2026-10-22 (15:6 by groups, p 0.08), was established over five replicate runs a side on 2026-10-23
  (the entry "Two settings of the naming compared over replicate runs"): the runs' mean verdict 0.795
  against 0.735, permutation test over runs p 0.008.

## Subsystems: functions grouped by what they share and where they lie, not by calls (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** Function names need a step that sees a subsystem whole (docs/todo.md, Function
  names): functions grouped by their code, the group named from all of its evidence at once. Which
  evidence groups functions as their source files do, and by which method, without names (a
  stripped image must group as its named build)?
- **Options, measured** on four FreeRTOS builds (Cortex-M3 GCC -Os made nameless, clang -O2, GCC
  -O0; RISC-V GCC -Os) against the compile unit of each function in the ELF's debug information.
  Evidence per kind, the share of its weight that joins two functions of one file: calls 7-17 % (a
  demo calls the kernel, the kernel the port; also the calls to a callee with a single caller, 12-45
  %); neighbours in address order 79-87 %; globals both touch, at exactly one address, 90-97 %
  (coarser, 16-256 byte blocks, joined files: the adjusted Rand index fell by 0.03-0.4); texts 79-100
  % (an assertion's __FILE__ marks a file); a function's address the other makes (a task it creates)
  92-100 % (clang's 4 of 9 before its movw + movt were joined, below); device blocks 23-27 % (the
  core's NVIC, SysTick and SCB share one 4 KiB block). Methods:
  Louvain's modularity (a small implementation, deterministic: address order, ties to the lower
  community) on a graph of those edges, a thing k functions share giving each pair 1/(k-1); a final
  pass of single moves after it (moved 1-3 functions, +0.00-0.004: left out). The adjusted Rand index
  (1 the files exactly, 0 chance) per build (GCC -Os, clang -O2, GCC -O0, RISC-V): neighbours only
  0.45, 0.38, 0.45, 0.43; shared things and addresses only 0.79, 0.83, 0.76, 0.38 (71-134 groups,
  many single functions); both 0.81, 0.78, 0.84, 0.78; calls added with weight 0.3 0.70, 0.58, 0.57,
  0.48, with 1 0.33, 0.30, 0.36, 0.24. Resolution 1 against 2: 0.77, 0.78, 0.87, 0.80 against 0.81,
  0.78, 0.84, 0.78 (the same on average), purity 0.70-0.79 against 0.74-0.85, completeness 0.95-0.98
  against 0.89-0.98; 3 split RISC-V's files (0.55). A grid over the weights of texts, globals, addresses
  taken and neighbours and the resolution (486 settings) gave a mean over the builds of 0.82 at best
  and 0.75 for the median setting: flat, so the weights stay 1, with neighbours 0.5. The evidence from the listing alone (literal pools and
  pc-relative addresses) did as well on ARM but found few of RISC-V's globals (0.44 against 0.78);
  from the readable C's accesses (dread.relations Facts) it finds them; clang's movw + movt made texts
  and callbacks invisible to both until the ARM layer joined the pair (wide_constants: clang -O2 0.76
  to 0.78).
- **Choice:** Louvain at resolution 2 on shared globals, texts, tables and device blocks, addresses
  taken (weight 1) and neighbours (0.5), no calls, things more than 64 functions touch left out, the
  evidence from dread.relations Facts (dread/groups.py); movw + movt pairs among the addresses a
  function makes (dread.relations materialized, the facts' version 2). No package installed: the
  graphs are small (networkx would have added a dependency for one function of 80 lines).
- **Why.** The combination is the best measured on all four builds (0.78-0.84, purity 0.74-0.85,
  completeness 0.89-0.98: 27-29 groups for 41-53 files); resolution 2 gives the same index as 1 with
  purer groups, and a group's name needs purity more than completeness (a file split in two gets two
  names of the same part; two files in one group get a name that fits one of them). Calls are what
  layers do to each other; they join subsystems, not files. On gizmo (five source files of 50
  functions) resolution 1 is better (0.40-0.48 against 0.24-0.42: its files are few and large), so
  the choice holds for builds of FreeRTOS's size, not confirmed elsewhere.
- **Undo.** The constants of dread/groups.py (SHARED, TAKEN, ADJACENT, CALLS, RESOLUTION) or
  `dread groups --resolution R`; FACTS_VERSION 1 and wide_constants out of materialized for the facts
  as before.
- **Status.** Done (dread/groups.py, `dread groups [--score ELF]`, the bench step `groups`, dread.arch
  arm wide_constants, dread.relations materialized). Not measured on firmware whose files are not
  laid out one after the other (link-time optimisation, sections sorted by name).

## Readable C's ifs: the arm that falls through first, early exits, code repeated only for staircases (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** Readable C nested much deeper than its source in some functions (the bench's readable
  step; docs/todo.md, Staircases). The ones named there first (GCC -O0's prvSendFrontAndBackTest,
  RISC-V's prvLowPriorityMutexTask) were flat already after the structure rework of the same day
  (joins where code never returns); what was left: else-if chains with each test in the then arm of
  the one before (the stage wrote the arm the branch jumps to first), and runs of checks that leave
  for one short block or whose failure arms fall to one return, nested one level per check. How
  should an if's arms be arranged, and should code be repeated to make early exits?
- **Options.** (a) As before: the arm the branch jumps to first, an else after a first arm that never
  falls through written after the if. (b) Each if arranged by its nesting: the arm the test falls
  through to first (where compilers put an if's then arm), an arm that never falls through as an early
  exit (either arm), an else that is one if as else if, tests that exit folded into an else-if chain
  where they stand as an else; of these the least deeply nested (the deepest level, then the levels
  summed), the fall-through arm first on a tie. (c) (b), and early exits that repeat short ways
  (TAIL): a block several checks leave for that returns or never ends repeated at each jump instead of
  following the outermost if, and the short way after an if repeated into an arm that falls into it
  where that takes the if a level less deep; for every function (0), or where the function's deepest
  nesting falls by at least 1, 2 or 3 levels (GUARDS).
- **Choice: (b), with (c) where it takes 3 levels or more off (structure.GUARDS = 3).** Measured on
  the four FreeRTOS builds with readable gates (GCC -Os, clang -O2, RISC-V GCC -Os, GCC -O0; their
  1,195 accepted functions, 1,087 matched to their source functions), summing per function how many
  levels the readable C is deeper or shallower than the source: (a) 248 deeper and 109 shallower (357
  in all), (b) 169 and 138 (307; 41 statements fewer than (a)), (c) at 0 or 1 level 92 and 256 (348;
  about 330 statements more than (b)), at 2 levels 130 and 183 (313; 135 more), at 3 levels 144 and
  153 (297; 64 more).
  The repeats flatten what is a staircase (vTaskSwitchContext's stack check 6 levels to 2, its
  source's 2; xQueueGenericCreate's failure arms 5 to 2, its source's 2), but where a function is
  only a level or two deeper they mostly turn a source's single-exit nesting into early exits, flatter
  than the source with code repeated (xQueueGenericReset 4 to 1 against 4). Of the functions whose
  depth the choice changed against (a), 58 came closer to their source's, 30 went further; the gotos
  went from 85 to 83.
  Collapsed conditions keep the block that comes next as the way the test falls through
  (if (a) goto T; if (b) goto U; T: is if (!a && b) goto U, so that (b) writes if (a || !b) T else U,
  as the source has it).
- **What it costs.** The structure stage takes 0.18 s instead of 0.06 s for GCC -Os's 316 functions
  (the arms' nesting compared, the clean-up run before and after the arrangement, a second emission
  for a function more than GUARDS levels deep), about 0.4 s instead of 0.15 s for the tests' 400
  random functions that mostly never return (on a shared machine; tidy takes far longer). Where the
  readable C of a function changes (the order of its if/else arms; 420 of the four builds' 1,229
  lifted functions), the readable_names entry made for the old C waits for a new proposal (its
  digest).
- **Undo.** structure.GUARDS = None takes the repeats out; GUARDS = 0 makes early exits wherever they
  are no deeper. Writing the jumped-to arm first again: arrange_if's tie order (or flatten_else, in
  git history before this change).
- **Status.** In the code (dread/structure.py: arrange, arrange_if, fold, short_way, nesting,
  drop_fall_gotos, Emitter's guards, GUARDS). Not tried: a test's statements folded into its
  condition (r3_1 = *r1; if (r3_1 == ...) as an else if), which would need assignments in
  conditions; an unrolled loop's exits as a do { } while (0) with break (clang -O2's
  prvInitialiseNewTask, 12 levels against 3).

## A whole image that differs with lifted C: the lift check keeps out a smallest set that makes it (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** The lift check's last stage runs every scenario on the image with all accepted
  functions built from lifted C (whole_image). A difference there was reported (whole.txt) and every
  function stayed in the lifted table. FreeRTOS's RISC-V builds with clang -Os and clang 14 differ
  there: SetB, EventGroupsDemo's test master (configMINIMAL_STACK_SIZE, 480 bytes), overflows
  ("Stack overflow in SetB"), because the C's frames of prvTestMasterTask and xEventGroupSetBits are
  larger than the original's: clang -Os 176 -> 256 and 32 -> 64 bytes, 112 more on SetB's deepest
  path (prvTestMasterTask, xEventGroupSetBits, xTaskResumeAll, the trap's context save), which had
  100 bytes to spare in the original; clang 14 128 -> 224 and 32 -> 80, 144 more of 148 to spare (the
  overflow check reads the stack's last 16 bytes). Each function's cases agree: they never see a
  task's stack. Either function alone leaves the image agreeing. The growth is general: 172 of the
  242 lifted functions of the clang -Os build have a larger frame in C (median 32 bytes), and on
  clang 14 131 of the 171 that grew grew by exactly the original's register saves rounded up to 16
  bytes (4,528 of their 4,784 bytes): the lifted C keeps the original's frame as an array, its save
  slots included, and the C compiler saves the registers it uses beside it. What should the check do?
- **Options.** (a) Report only, as before: the lifted table then holds functions whose C makes the
  firmware fail. (b) Find the functions by halves and keep them assembly, as dread readable did for
  readable C: two that differ only together and lie in different halves leave neither half
  differing, and that search returned both halves. On the lift check's order of these builds the two
  split at the second level: 121 (clang -Os) and 122 (clang 14) functions would have gone. (c) Search
  for a smallest set that still differs (a binary search for the shortest prefix that makes the
  difference with what was found, repeated until what was found differs alone: about |set| x log2(n)
  whole-image runs), drop it, run again until the whole image agrees. (d) Compile lifted C so its
  frames do not grow: leave the original's save slots out of the frame array (the C compiler saves the
  registers itself), so that the C's frame is the original's locals plus the compiler's saves and
  spills.
- **Choice: (c) now, (d) as an item** (Compiler diversity, docs/todo.md). (c) is a check: whatever
  makes the whole image differ, frames or anything else C does only together with other C, is found
  and kept out, with the difference in the verdicts; a run of the search costs a build and two system
  runs (3 s on FreeRTOS RISC-V; the M3 builds that run to the limit take about 100 s a pair), and it
  runs only where the whole image differs. It drops the whole set it finds: one of the two would do
  here, but which one cannot be told from outside, and the one kept would leave the smaller margin.
  dread readable --check uses the same search (lift.culprits) instead of its halves. (d) goes to the
  cause, but changes the frame model of every lifter (ARM, RISC-V, MSP430) and what readable C's
  frame stages read; it would not make (c) unnecessary (spills and alignment still grow frames).
- **Undo.** dread/lift.py: check()'s loop behind whole_image (history, differs) and culprits;
  dread/readable.py's call of lift.culprits (its bisect is in git). tests/test_lift.py:
  test_culprits_finds_what_differs_only_together and
  test_functions_whose_c_makes_the_whole_image_differ_are_dropped (a program that prints how deep its
  stack went).
- **Status.** Done. On copies of the measure-38 builds the lift check drops prvTestMasterTask and
  xEventGroupSetBits on both clang builds (17 and 19 whole-image runs, 28 s each) and their whole
  images agree to the limit (8,218 and 8,165 log entries each; before, they differed at entries 355
  and 359); gizmo's lift checks keep every function, their whole images agreeing as before. Revised
  2026-10-23 (above): of a set of three or more only one goes where leaving it out alone makes the
  image agree.

## Code that only the core enters: the lift check refuses a function holding it (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** FreeRTOS's RISC-V build with GCC 10 asserted at stream_buffer.c line 1367 with lifted
  C alone and then read unmapped memory (0xfffffb85) while printing the file name. Its function table
  lists freertos_vector_table, the vectored trap table (mtvec = table | 1: the core enters at table +
  4 x cause), as a function of 128 bytes (GCC 10's binutils write no mapping symbols, and an untyped
  global label then makes a function; LLVM 14's table is listed so too, refused for another reason).
  Its first entry, a jump, is a tail call: that lifted, and its cases accepted it. The other entries
  are code no path from the entry reaches, and the overlay freed them under the C: the 120 bytes went
  to xStreamBufferIsFull's C, and the timer interrupt (cause 7, table + 0x1c) entered that C in the
  middle of its lui, ran its assertion path with a0 = 1 - 0x47c, the file name's address without its
  upper part. How should the lift check treat code inside a function that no path reaches?
- **Options.** (a) Refuse every function with unreached code that is not padding: but such code is
  common and harmless, behind a call that never returns or a branch decided never taken (357 lifted
  functions of 36 benchmark builds). (b) Refuse code that nothing leads to: no instruction in front of
  it goes on to it, no direct jump or call of any function goes there, no switch case of the function,
  and it is not padding (zero bytes, the layer's nops). (c) Lift it and keep the unreached bytes (the
  overlay frees nothing there): the C of the first entry (auipc and jr, 8 bytes) already covers the
  second. (d) Take the system run's trap entries as facts and refuse a function the core enters
  other than at its entry: only the vectors a scenario takes would be seen.
- **Choice: (b),** in the acceptance (check, beside "other code branches into its middle"), not in
  the translation: the C of such a function is right, the space it would free is not free, and dread
  liftfuzz's random functions hold such code (behind their jumps) and still have to lift; refused in
  the lifter first, three MSP430 fuzz tests failed. It needs no fact the code does not show, and on
  the benchmark it hits nothing but the table: of the lifted functions of the 39 builds only GCC 10's
  freertos_vector_table; of all functions, LLVM 14's table too (not lifted anyway) and none else once
  branches from other functions count (MSP430's shift helpers are entered so). Gizmo's lift checks
  keep every function. Not covered: hand-written C (src) of such a function, and a lifted table
  written before (dread recon builds what the table names); the overlay does not look.
- **Undo.** dread/lift.py: Lifter.entered_unseen and check()'s lifts; the layers' padding attributes.
  tests/test_lift.py: test_code_that_only_the_core_enters_is_not_freed.
- **Status.** Done; GCC 10's lift check refuses the table and its whole image agrees (257 functions,
  the same as before but the table).

## IT blocks in readable C: one if/else, statements between written into both arms (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** The lifters write each conditional instruction of a Thumb-2 IT block as an if of its
  own on the flags. Ifs on one condition or its negation can be one if/else where the condition
  keeps its truth. GCC often schedules an unconditional instruction between two IT blocks on one
  condition (`itt ne; movne r2, #0xc1; movne r3, #addr; ldr r0, [r5]; it ne; strne r2, [r3]`): the
  load keeps the two ifs apart, the registers the first block sets stay live on both paths (a call's
  zeroed r2 and r3 too), and the store's address is no constant. What to do with such a statement?
- **Options.** (a) Leave the ifs apart. (b) Move the statement before or after the ifs: not where it
  is an effect (a volatile load or store moves past others). (c) Write it into both arms of the one
  if/else (if (c) { A; S; C } else { B; S; D }): on each path the same statements run in the same
  order, at the price of the statement twice.
- **Choice: (c)**, for at most BETWEEN (1) statement without a call (dread.tidy merge_ifs), which
  must not write what the condition reads. One statement written twice is as many statements as the
  if it saves; what it enables (the zeroed registers dead, the address a constant, the flag read once
  and folded into the if) is what pays: vInterruptMutexMasterTask on FreeRTOS GCC -Os has eight such
  pairs (121 statements without, 81 with). Measured on FreeRTOS GCC -Os, the readable functions
  matched to their source: 5840 statements with BETWEEN 0, 5756 with 1, 5759 with 2 (two statements
  written twice gained in one function and lost in three); clang -O2 the same with each.
- **How to undo.** BETWEEN = 0 in dread/tidy.py.
- **Status.** In the code (dread.tidy merge_ifs).

## What the system run takes for a wait outside shared code: a loop found by its registers (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** Code outside shared code (the C, and the original's code of what the C replaces)
  does not move the shared clock, so a firmware that waits there for an interrupt would wait for
  ever: the run has to move the clock on to the next event somewhere. Until now: after FROZEN
  (2,000) blocks in a row there with an event due. That count is of code the two images do not
  share, and it decided at different places in them (FreeRTOS RISC-V GCC -Os and Cortex-M clang -O2
  with lifted C alone: docs/todo.md, Compiler diversity). What is a wait?
- **Options.** (a) A larger FROZEN: the same rule, failing on longer computations. (b) A loop: a
  block entered again with the core's registers (general, sp, link, status, the interrupt masks) as
  at an anchor, searched from FROZEN blocks on (Brent: the anchor moves at FROZEN, 2 FROZEN, ...),
  memory not compared, and LOOP_MAX (200,000) blocks counting as a wait all the same. (c) As (b) with
  memory compared (a snapshot of RAM at a register match): a proof that the code repeats itself.
- **Choice: (b).** A computation, however long, makes progress in some register (the copy's pointer,
  the count), so it ends by itself in both images and nothing in it depends on how many blocks each
  image's code takes; a wait polls, and its registers repeat within a round. (c) would take a busy
  task that counts in memory (FreeRTOS's coherence testers: their counter is a global) for a
  computation and run it to LOOP_MAX on every tick, and costs a RAM snapshot per candidate; such a
  loop is a wait as far as the devices can see, which is all the run compares. What (b) gets wrong:
  a computation whose progress lies in memory alone while every register repeats at some block would
  count as a wait (not seen; not confirmed that none exists), and one of more than LOOP_MAX blocks
  without a device access would too (then as FROZEN did, at a hundred times the length). The rule
  decides when time passes and when a run ends, not what is compared: a mistake of it shows as a
  difference between the images or as runs that end early (less compared), not as a comparison that
  lets a difference pass.
- **Undo.** dread/sysrun.py: System._loops (the search) and FROZEN, LOOP_MAX; the C hook's copy is
  sys_stretch and sys_at_anchor in dread/fasthooks.c. tests/test_sysrun.py's
  test_a_wait_outside_shared_code_is_a_loop_a_computation_is_not shows what the old rule did.
- **Status.** Done; the four FreeRTOS builds measured agree with lifted C alone, gizmo's lift checks
  unchanged. Revised 2026-10-23 (above): memory is compared too, since a computation whose progress
  lay in memory was seen taken for a wait.

## Readable C against its source: clang's tree of what the compiler preprocessed (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** The bench's readable step compares each readable function with its source function
  (statements, the deepest nesting of control statements, gotos). How are the source's figures
  counted, for C with GNU extensions (FreeRTOS's ports: extended asm with operands, attributes;
  newlib's and the toolchains' headers)?
- **Options.** (a) pycparser on preprocessed source: pure Python, but it reads no GNU extension;
  every asm statement, attribute and statement expression would have to be rewritten away first,
  with fake headers for the C library. (b) clang's JSON tree (`-Xclang -ast-dump=json
  -fsyntax-only`) on the sources with the build's include paths and defines: clang's own predefined
  macros then pick the #if branches (__clang__, its __GNUC__ of 4), not those GCC compiled, and the
  headers' search path of the build's compiler has to be given. (c) The build's compiler
  preprocesses each source with its exact flags (in its container for a toolchain's build), clang
  reads the preprocessed text for the build's target and the C standard the compiler preprocessed
  (GCC 15 on: C23, whose stddef.h has nullptr, which clang's default C17 refused in every kernel
  file). (d) dread.cparse: it reads only the lifters' dialect.
- **Choice: (c).** The preprocessed text is the C that was compiled, macros expanded (configASSERT
  is an if and a call in the source as in the readable C; a macro's `do { } while (0)` is counted
  as the block it is); clang reads every extension the FreeRTOS builds' files use (41 C files on
  the Cortex-M builds, 39 on RISC-V), a source file in about 0.1 s, a build's in 1 to 2 s in threads
  (8 s where a toolchain's container preprocesses). Both sides go through one
  skeleton and one count (dread.cmetrics), so a statement means the same on both. A source function
  is found by the out-of-line function's entry in DWARF (the subprogram there, through its abstract
  origin for a clone), its translation unit by the end of its path (a patched copy, a moved build),
  and its name in that unit's tree, never by DREAD's name (prvEchoServer_544c).
- **What it costs.** clang on the measuring machine (the bench's clang builds need it anyway; CI has
  it); without it the source side is skipped and says so. A construct GCC accepts and clang refuses
  is an error the report lists per file (startup_gcc.c: a C statement in a naked function, a
  function no readable C has). Functions the compiler inlined count in the readable C, not in the
  source function: the comparison is also made against the source with its inlined functions
  (DWARF's inlined subroutines), and the lists of the worst say what was inlined.
- **How to undo.** dread.cmetrics' clang_skeleton and dread.bench's source_side are the only users;
  another reader of the source only has to give the same skeleton.
- **Status.** In the code (dread.cmetrics, dread.bench readable_step and source_side).

## Readable C's structure: a jump repeats a few statements; shared blocks after an if without a join (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** Readable C may repeat code where that saves a goto: a copy does what the jump and
  the code it went to did. How much should a jump repeat, and where should a block go that several
  ways reach: into the first arm that reaches it (the other ways jumping in), or after the if that
  dominates it (Ramsey's merge nodes, "Beyond Relooper", ICFP 2022)?
- **Options.** Repeat: (a) a lone return only, as before; (b) up to TAIL statements of a way without
  a branch that ends in a return or a statement that never ends, at the follow, at the loop's head
  or at the block after it. Shared blocks: (c) in the first arm, as before; (d) after every if that
  dominates them; (e) after an if only where its ways never meet (each ends in a return or goes
  round a loop).
- **Choice: (b) with TAIL = 4, and (e).** Measured with the rest of the change on four FreeRTOS builds
  (GCC -Os, clang -O2, RISC-V GCC -Os, GCC -O0; 1,231 lifted functions): TAIL 1, 3, 4, 5, 6 left 195,
  116, 107, 105 and 104 gotos, the readable lines 36,180, 36,320, 36,334, 36,340 and 36,344; (c),
  (e), (d) left 111, 107 and 226. After every if, the shared blocks stand in a row behind it, and
  every way that skips one jumps over it; where the ways never meet, nothing has to skip them. A
  branch whose ways meet at a block that only returns, taken as one that never meets, was worse
  (measured before the repeats were general: 291 against 271).
- **Undo.** structure.TAIL = 1 repeats only ways without a statement (a lone return);
  Emitter(merges=False) leaves shared blocks in the first arm.
- **Status.** Done (dread/structure.py). Not tried: repeating a small region that branches (the
  assert of a shared error block), or conditions merged across a lattice of shared blocks.

## Function names: five questions a function and a second pass, callers first; no gate (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** `dread pilot --task name` asked each function once, in one pass, callees first. Its
  names vary from run to run (two runs on FreeRTOS name 55 % of the functions alike), and a wrong
  early name spreads, shown to later questions as if it were certain. Three ideas that use randomness
  and iteration: votes over several questions (self-consistency), later questions showing only names
  a vote is sure of with repeated passes, and ablation (asking again without the neighbours' names
  to flag names that depend on them). Which help, and which become the default? And how are two runs
  compared, when the judge's absolute verdicts move by several points?
- **Options, measured** on FreeRTOS GCC -Os (mps2, 366 functions made FUN_address), Haiku 5.5
  naming, Sonnet 5.5 judging pairwise (`--against`: where two runs' names differ, which is closer to
  the true name, asked in both orders; wins, losses, ties against one question in one pass; a sign
  test): (a) as before: a second run 120, 95, 72 (p 0.10: the measure's spread); (b) votes of five
  (`--samples 5`): 130, 60, 84 (p 4e-7); (c) votes of five, shown to later questions only at
  confidence 0.8 or more, four passes (`--gate 0.8 --passes 4`): 158, 53, 82 (p 3e-13); (d) votes of
  five and a second pass callers first, every name shown (`--samples 5 --passes 2`): 163, 47, 78 (p
  3e-16). The gate alone gains nothing (its first pass against (b): 80, 81, 79; (d) against (c) after
  two passes: 95, 69, 80); the second pass gains (66, 14, 23 gated, 81, 26, 23 ungated); passes 3 and
  4 do not (25, 25, 24 and 22, 12, 9); (c) after four passes ties (d) (84, 85, 73) for half again
  the answers. (e) Ablation (`--ablate`): the flag marks names that depend on the names shown (64 %
  of them against 32 % for the same question asked twice), not wrong ones: the vote with the names
  beats the vote without 164, 26, 55.
- **Choice: (d) the default** (NAME_SAMPLES 5, NAME_PASSES 2 in dread/pilot.py); `--gate` and
  `--ablate` stay options, off; runs are compared with the pairwise judge, the absolute verdicts kept
  beside it. `--against` replaces `--versus` (merged the same day), which showed each pair once, the
  order chosen by the address: that judge preferred the second place (about 55 to 45; two identical
  controls came out 151 to 117), which asking both orders cancels (two identical controls 120 to 95,
  p 0.10).
- **Why.** (d) is the best measured and the cheapest of the best: 3,195 answers on this build ($1.05
  against $0.12 for one question in one pass), (c) 4,721 ($1.59) for no more. The vote's confidence is
  calibrated (judged different 88 % at 0.2, 58 % at 0.4, 42 % at 0.6, 26 % at 0.8, 15 % at 1.0), so a
  reader of names.tsv knows which names to trust, and votes are steadier than single answers (two
  votes alike for 68 % of the functions whose question shows no name, two answers for 47 %). The gate
  hides names that help more than they mislead: a name of 0.6 is wrong 42 % of the time but carries
  the subsystem. The absolute judge drifts with its batch (13 of 126 functions two runs named alike
  were judged otherwise in the two judgings, 11 harsher in one), so it cannot tell runs apart by a few
  points; the pairwise judge puts both names on one line.
- **Undo.** NAME_SAMPLES = 1 and NAME_PASSES = 1 in dread/pilot.py, or `--samples 1 --passes 1` for
  one run (a K of 1 writes no confidence, so `--gate` then hides every name: refused).
- **Status.** Done (dread/pilot.py, dread/naming.py similarity and vote, docs/formats.md names.tsv).
  Measured on one build the model knows (it recognises FreeRTOS once it has context), and on the
  code before the notes showed a long text's end (the entry below; every variant alike); not
  confirmed on firmware it does not know, where a misread driver spread on 2026-10-22: next on the
  private real-life test (docs/todo.md, Function names), and with `--behaviour`.

## Function names: a long text's end in the notes; the runs of the cases an option (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** Should a naming question (`dread pilot --task name`) show, beside the function's
  code, what the function did when it ran: dread.behaviour's runs of its cases on the original
  image (its calls with their arguments and results, its device accesses and writes, the bytes it
  read, how it ended)? By default?
- **Options, measured** on FreeRTOS GCC -Os with every function renamed FUN_address, Haiku 5.5
  naming, Sonnet 5.5 judging each pass twice; the mean verdict rank per function (same 2, close 1,
  different 0) over all passes and judgings of an option: (a) as before, 0.900 (two passes); (b) with
  the runs, 0.994 (three passes, +0.098 +- 0.028); (c) the notes after constants show a long text's
  first third and its end instead of its first 48 characters, 1.067 (two passes, +0.165 +- 0.028);
  (d) (c) with the runs, 1.042 (two passes; -0.009 +- 0.020 against (c)). All of (b)'s gain was in
  the functions whose runs showed an assertion's source path, whose file name the notes had cut off;
  two passes of one setup differ by up to 0.05.
- **Choice: (c) by default; the runs stay an option (`--behaviour`).** (c) gives more than (b) at
  no cost; on top of it the runs add nothing measurable on this build, for 37 % more input tokens
  ($0.14 against $0.12 a pass) and a minute of emulation (two more without cached cases).
- **Undo.** The notes: dread.constants string_at back to a text's first 48 characters and "..."
  (its start-and-end form and TEXT_SCAN out). The runs are shown only with `--behaviour`; making
  them the default is name_functions' and Namer's behaviour argument.
- **Status.** Done (docs/todo.md, Function names). Not measured where the code says less than
  FreeRTOS's (drivers whose device registers have no names: the private firmware's misses); the
  runs are to be tried there next.

## The frame that addresses handed out reach: one volatile object of a typedef'd structure (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** dread.frames writes the part of a frame from the lowest address that leaves up to
  sp0 as one local structure (one object for the whole region, not one per address that leaves:
  that would trust each callee to stay inside its piece; docs/todo.md, Variables). How is it
  declared: a plain object or a volatile one; its type inside the function or above it?
- **Options.** (a) plain: reads like C written by hand, and a compiler may keep a member in a
  register between calls; (b) volatile, as the raw accesses were. And (1) an anonymous structure in
  the declaration, its fields on one line (what a view, the text dread pilot's questions show, would
  have beside the object); (2) a typedef above the function, a field per line, the object declared
  with its name (a view shows the object only, as for dread.shapes' structures).
- **Choice: (b) and (2).** The builds compile with strict aliasing (-Os and -O2 without
  -fno-strict-aliasing), and the function's own pointers into the region access it with their own
  types (the lifter's ldrd and strd blocks: a_2 = (u32)&stack40.f_0; *(volatile u32 *)(a_2 + 4) = 0u;
  a buffer walked with byte accesses): a compiler may carry a plain member across such an access of
  another type. Volatile keeps every access one and in order, as the raw frame accesses were, at no
  cost the lifted C did not have. (1) was tried first: dread.cparse reads no structure in a
  declaration, and readable C is to stay C of the lifters' dialect (tests/test_readable.py parses it
  again); it reads typedefs (the MSP430 lifter's dread_w32), so (2) keeps that. A field per line also
  suits the largest region so far, 35 fields (FreeRTOS's prvTestMasterTask, GCC -Os).
- **Undo.** dread/frames.py: without "volatile " in the object's declaration for (a); the typedef is
  Region.typedef. The hook is one call in dread.readable._named.
- **Status.** Done; the gate accepts the same functions as before on the eight builds (docs/todo.md,
  Variables).

## The C standard of DREAD's own C: gnu11 (2026-10-22)

**Asked by the user** ("What pinned C standard would you recommend?"), **decided by recommendation**
(the user's standing delegation of 2026-10-20).

- **Question.** DREAD compiled lifted and readable C with the toolchain's default standard: gnu90 for
  GCC before 5, gnu17 from GCC 5 to 14 and for clang, gnu23 from GCC 15 on (the host's toolchain
  compiles it: the benchmark's builds of old compilers, GCC 4.9.3 among them, are the originals'; their
  lifted C is compiled with the host's GCC). A second identical definition of a structure, which C23 allows, passed the suite here (GCC 16)
  and failed in CI (GCC 10 and 13). Which standard should the C DREAD generates be compiled to?
- **Options.** (a) gnu11: GCC 4.7 and clang 3.1 on; (b) gnu17: the same language as C11 with defect
  fixes, but GCC 8 and clang 6 on (a user's older host toolchain may lack it); (c) gnu23: only the newest
  toolchains, and it changes meaning (bool, true and nullptr keywords, () as (void), tag
  redefinition); (d) gnu99: older still, without C11's identical typedef redefinition and anonymous
  members; (e) strict c11: turns off GNU extensions the lifters write (asm, attributes, builtins).
- **Choice: (a) gnu11**, for the C DREAD generates only (lifted and readable C; the C a names check
  compiles), with gcc and clang alike: [compiler] generated_std, default "gnu11", in front of
  lifted_cflags, which may override it with a later -std; "" pins nothing. A target's own sources
  keep its flags.
- **Undo.** generated_std = "" in a target, or the default in dread.target.Compiler.
- **Status.** Done; checked with the suite, gizmo's lift checks and the eight readable gates (see the
  commit).

## Globals for function names: a step of their own, not a default (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** Do names for the globals (memory at fixed addresses, g_ADDRESS in readable C) make
  `dread pilot --task name`'s proposals better, and should naming them become part of that task?
- **Options.** (a) The globals named before the first pass, by default; (b) named between the first
  pass and --refine, their uses shown under the proposed function names; (c) a task of its own
  (`--task globals`), showing the proposals when names.tsv has them, the order left to whoever runs
  it.
- **Choice: (c).** Measured on FreeRTOS GCC -Os with every function FUN_ (docs/todo.md, Function
  names): (a) made the names worse (judged different 37-39 % after --refine against 27-30 % without
  globals, the loss in the functions whose C shows a named global); (b) gained a little (paired with
  the same first pass refined without globals, 21-25 changed names judged better and 9-10 worse;
  the totals within the judge's noise) for $0.01.
- **Undo.** `--task globals` is separate: nothing else asks for globals but `--task readable`; a
  globals part removed from config/readable_names.json takes the names away again.
- **Status.** Done (`dread pilot --task globals`). Not measured: a partly named image.

## Readable C's structures: kinds from calls between bases, null checks kept (2026-10-21)

**Decided by me**, on the user's standing rule (decisions by recommendation; review here).

- **Question.** dread.shapes finds each pointer's fields (99 % the source's), but readable C needs
  types: which pointers, across functions, are the same structure? And may typed C be compiled as
  the integer C was?
- **Options, measured** as the fields the plan declares for typed parameters against their source
  structures (FreeRTOS GCC -Os): kinds linked whenever objects merge or a template is copied (58 %:
  one kind of task blocks, queues, lists and items: FreeRTOS's rings of items, list ends and tasks);
  only through calls, between the argument and the parameter, on the final objects (63 %); only for
  callees that reach two fields or more (79 %: memset, memcpy and strcmp touch offset 0 of anything);
  no constant below 0x100 as an address (93 %: NULL for the current task went to a dozen functions);
  no stack frame, offsets from a known pointer resolved at once (97 %: GCC gave a static queue and a
  static event group one slot); items at one offset of one kind one kind (97 %, one item type for
  all list functions). Then, for the gate on clang -O2 and GCC -O0: only u32 variables the function
  dereferences, no C variable's address, no stack frame (GCC -Os 97 %, -O0 98 %, clang -O2 68 %,
  RISC-V GCC -Os 61 %). Then for clang and RISC-V: values per web of definitions on the C before
  structuring (RISC-V 84 %), the lowest pointer target as a structure's base, joins only where
  layouts fit, strongest first (clang 90 %, GCC -Os 100 %), no kinds through calls' results (RISC-V
  97 %, clang 91 %). Considered and left: joining kinds through pointer fields (an item's owner, a
  void *, holds tasks and timers).
- **Choice:** the last. Typed C is compiled with -fno-delete-null-pointer-checks (a file's marker
  line, dread.build): GCC dropped a pointer's comparison with 0 once the pointer was dereferenced,
  and address 0 is memory on these targets. Typed C compiles to other code than the integer C (GCC
  addresses [r0, #4] where it computed r0 + 4 first, a signed read of an unsigned field adds a sxth):
  the gate decides on behaviour, as for every transformation.
- **Undo.** Delete readable_types.json and take apply_types out of dread.readable's transform.
- **Status.** Built; the gate accepts as many functions as before on the bench's builds
  (docs/todo.md, Types).

## Structures behind pointers: polymorphic unification, measured against debug information (2026-10-21)

**Decided by me**, on the user's standing rule (decisions by recommendation; review here).

- **Question.** How to find the structures readable C should declare: which offsets through which
  pointers belong together, across functions?
- **Options, each measured** on FreeRTOS GCC -Os (fields found that are the source's, by offset and
  size, against the debug information's members of each pointer parameter's structure): (a) plain
  unification with offsets (Steensgaard's points-to analysis with fields): 43 %, one object of 67
  fields that 356 parameters pointed to; (b) functions polymorphic, a callee's parameter memory a
  template the call copies: 77 %; (c) as (b), objects that meet at different offsets kept apart
  (FreeRTOS walks a list from its end marker as if it were an item, so lists and items had merged):
  80 %; (d) as (c), templates copies even of memory reachable from fixed addresses (pvPortMalloc
  returns a pointer into its heap's free list, and sharing that made every allocated structure one):
  99 %, 32 % of the source's members found.
- **Choice: (d).** The fields it finds are almost all the source's on GCC -Os and -O0, 91 % on clang
  -O2 and 95 % on RISC-V; what it misses is what the code never reaches through those pointers. The
  analysis decides nothing about behaviour: the C written with these structures goes through the
  gate like any other.
- **Undo.** dread.shapes is used by nothing else yet; Shapes(share=True) and shifted=True bring back
  (c) and (b) for a comparison.
- **Status.** Analysis built and measured (dread readable --shapes --score); the rewrite is the next
  step (docs/todo.md, Types).

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

## Function names: readable C, callees first, and a judge beside the word scores (2026-10-22)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** `dread pilot --task name` asked for one FUN_ function's name at a time, smallest
  first, from its listing, and kept the proposals unscored. On a stripped firmware of thousands of
  functions (DREAD's real-life test, private), what should a question show, in which order, and how
  is a name scored when the words of two good names differ (buf_len against buffer_length share
  no word)?
- **Options.** Input: (a) the listing, as before; (b) the readable C where the function lifts, the
  listing where it does not; (c) readable C only where dread readable's gate accepted it. Order:
  (1) smallest first, each alone; (2) callees first, each question showing the names proposed so far
  for the functions it calls and its callers. Score: (i) word scores against true names only (exact,
  the same words, shared words, prefix); (ii) a language model's verdict per pair beside them (same,
  close, different), from another model than the one that named.
- **Choice: (b), (2), (ii).** Readable C is what a person reads; the gate's verdict is about the
  compiled code's behaviour, and for a name the reading counts, so unchecked readable C is shown too
  and names.tsv's shown column says which form a question had. Callees first is how a person names a
  program: a leaf's name then helps its callers; the system prompt says other names may be proposals.
  The word scores stay the measure code decides; the judge is a second one, labelled as a model's
  judgement, cheap (names only), and optional (`--judge`).
- **Undo.** `--task name` reads the listing again where `Namer.code` returns it; the order is
  `callees_first` in main(); `--judge` is not given.
- **Status.** Done. Measured on the private firmware (docs/todo.md, the pilot on the draft). The
  readable C of every function is made first in a pool of workers (dread.readable views: in a
  sixth of the time on one core), each worker costing up to the parent's size in memory, so pools are now capped by the memory available.
