# What to do next

The queue of work, top first. Each item says how to tell it is done. Take the first open item,
finish it with tests on both gizmo builds, commit, run the leak check, push, and tick it off here in
the same commit. What was done and learned goes into the commit message; this file only keeps the
queue. The order was agreed with the user: the ARM phases, then `dread pilot`, then RISC-V; since
2026-10-20 compiler diversity on the architectures DREAD has, ARM/Cortex-M and RISC-V, before more
architectures, and since 2026-10-21 readable C from lifted C before the rest of that, on the user's
question (both in docs/decisions.md; the bench measurements go on meanwhile). How the queue may change while the user is away: CLAUDE.md,
"Working unattended".

## Readable C from lifted C (the user, 2026-10-21; first since then: docs/decisions.md)

`dread readable`: lifted C turned into C a person can read, by transformations in code. Every result
goes through the lift check's gate and is accepted only as lifted C is (cases that run every
instruction agree, the whole image agrees); what fails stays lifted. Names and types stay with the
pilot, which gets the readable draft instead of the lifted C. Measured on the FreeRTOS bench against
its sources.

- [x] **The tree.** Parse the lifted C of all three lifters into a syntax tree and print it back
  (pycparser if it reads the lifted dialect with a header of typedefs, else a parser of the dialect
  of DREAD's own). Done when every lifted function of an ARM, a RISC-V and an MSP430 bench build
  parses, and the printed C, built in its place, passes the gate as the lifted C does (a round trip).
  Done 2026-10-21 with a parser of DREAD's own (dread/cparse.py): the dialect has GNU extended asm
  with operands, register variables (__asm__("r0") on a declarator), compound literals with
  designators (MSP430's 32-bit results) and an attribute with a string in a function's head, which
  pycparser reads none of, and every statement keeps the instruction of its line's comment (address
  and text), which a preprocessor would have dropped. The round trip is checked harder than by the
  gate: all 10,633 lifted functions of the 38 bench builds that have them (ARM, Cortex-M4F, RISC-V,
  MSP430; GCC 4.9 to 16, clang 12 to 19) parse, and the printed C compiles to the same code and
  relocations as the lifter's (tests/test_cparse.py: every gizmo build, and the dialect's pieces).
- [x] **The gate.** `dread readable --check`: like `dread lift --check`, with each lifted function's C
  replaced by its transformed C: the rounds, the whole image, a table of what was accepted and why
  the rest was not; the overlay builds the accepted ones from it. Done with the identity
  transformation on gizmo and one bench build: the same functions accepted as by the lift check.
  Done 2026-10-21 (dread/readable.py): the function-level runs build what the lift check's built
  (the lifted functions but those the system run accepted, which come in for the whole image), a
  function that fails goes back to its lifted C, the accepted ones go into the readable table
  (config/readable.txt) and dread recon builds them from readable C. With the identity: gizmo-m3 all
  36 and the same image byte for byte; FreeRTOS GCC -Os 316 of 328, the whole image agreeing, 19 s
  with the cases cached; the 12 the system run accepted stay lifted until readable C is checked
  there too. (The first try built those 12 from lifted C in the function-level runs too, and a case
  of prvRegTestTaskEntry1 the lift check had accepted failed: the configurations differed.)
- [x] **Readable C for the functions the system run accepted** (2026-10-26, docs/decisions.md, "Readable C
  for the functions the system run accepted"). The gate left them lifted ("readable C is not checked
  there yet"): on the FreeRTOS bench's measure-40, 345 of the 381 functions that stayed lifted, the demo's
  tasks and its start. Their readable C is tried in the whole image now, with the readable functions',
  as the lift check tried their lifted C; the smallest set that makes it differ goes back to lifted C
  (halving); where nothing can decide (no scenarios, an image that differs with lifted C alone) they
  stay lifted with that reason. Main -> this: gizmo a32 35 -> 36 of 36 (irq_entry), rv32 31 -> 32 of 32
  (trap_entry), m3 and m4 have none; FreeRTOS GCC -Os 328 -> 341 of 341, clang -O2 288 -> 300 of 300,
  RISC-V GCC -Os 265 -> 272 of 272, GCC -O0 378 -> 382 of 382, the whole image agreeing on each. dread
  bench's categories name the new reasons.
- [x] **Clean-up.** A control-flow graph from labels and gotos; liveness and reaching definitions over
  registers, flags and frame words; flags and registers nobody reads dropped; copies and constants
  propagated; a temporary used once folded into its use; a flag-setting compare and the branch that
  tests it as one comparison (signed or unsigned from the condition). Done when the gate accepts as
  many functions per build as the lift check, with statements per function against the lifted C's
  and the source's in the bench report.
  Done 2026-10-21 (dread/tidy.py on dread/cflow.py's graph, liveness and reaching definitions as bit
  sets; dread/cexpr.py): chains of assignments split, the (void)s gone, the instructions' blocks
  flattened into fresh variables, dead assignments out (a call or a volatile load keeps its effect; a
  dead load of the function's own frame goes), frame stores nothing can read out, constants and
  copies propagated, temporaries folded along straight paths without moving an effect, x + 0 and
  casts to the same type out, constants folded, and conditions and values proposed from the
  expression's own parts, filtered by testing and kept only when z3 proves them (docs/decisions.md).
  Over the 10,633 lifted functions of the 38 bench builds: no failure, the lines to 48-65 % on ARM
  and RISC-V (MSP430 96 %: its flags go through helpers with asm, not cleaned yet), 4 to 39 s a
  build on one core, the slowest function 5 s. The gate on gizmo a32, m3, m4, rv32 and on FreeRTOS
  GCC -Os, GCC -O0, clang -O2, RISC-V GCC -Os and MSP430 GCC -Os accepts every function it checks
  but those the system run accepted and one task loop whose cached cases were older than the code.
  The statements against the source's come with the scoring item. Found on the way: testing alone
  took `r5 == 0x41` for a letter test (z3 since); a memory load abstracted as unsigned int instead of
  its own type (a byte is promoted to int: fixed); the whole image of two builds differs with lifted
  C alone, so the check now asks the lifted baseline first and lets the function-level runs decide
  where the scenarios cannot (and finds guilty functions by halves where they can). Later the same
  day: an address a memory access is made at is no constant to compare with (gizmo's uart_putc
  waited while io_40011004 << 31 < 0x40011004u, true but the bound was the register's address); the
  bit a left shift moves into the sign is a mask to test (now (io_40011004 & 1)); x << a >> b of a
  32-bit unsigned x is (x >> (b - a)) & mask; propagation and the expressions' clean-up reach the
  statements inside a conditional block (flat_view: uart_getc's IT block loads io_40011000, not
  *(r3 + 0)); the printer adds the parentheses GCC's -Wparentheses asks for ((x >> 1) & 1u).
  2026-10-22, on a private firmware: int's width was 16 bits in every function with a u16 variable
  (meant for MSP430's registers), and a 16-bit stack slot made a variable turned an ARM function's
  arithmetic 16-bit: the evaluator and z3 share the model and proved (u32)(s16)x != 12 to be x >= 0
  (~0xcu is 0xfff3 in 16 bits); the gate turned such functions down. Only the lifter's registers,
  parameters and sp0 decide now (tidy.context). Still read from the lifted C's dialect: int's width
  is the target's (rule 2) and could come from it.
- [x] **Structure.** Gotos to if/else, while, do-while and switch (pattern-independent structuring,
  "No More Gotos", NDSS 2015); a goto left only where the flow is irreducible. Done when the gotos
  left per build are in the bench report and the gate accepts as many as before.
  Done 2026-10-21 (dread/structure.py): basic blocks, dominators and post-dominators; a natural loop
  as for (;;) with break and continue, refined into while and do-while (a test at the top, at the
  end, or trailing continues: do { } while (a || b)); a branch as if/else up to its post-dominator,
  an else after an arm that returns written after the if; two branches to one target merged into
  || (and && by De Morgan); a block that only returns repeated instead of a goto; the labels nobody
  names dropped. Correct whatever it decides: each block once, under its label, every transfer a
  fall-through to the block that really follows, a break or continue of the loop it leaves, or a
  goto (tests/test_structure.py: 400 random functions with irreducible flow, compiled with the
  host's cc before and after, the same results on random arguments; 10 % of their gotos remain).
  The readable C is printed in a style of its own (dread.cparse: an arm of one statement without
  braces, else if; a loop with nothing in it as while (c);, and do {} while (c) as that, since both
  test c first). On the bench, the 11,061 lifted functions of the 39 builds: gotos 49,093 ->
  12,512 (25 %), lines to 67 % of the lifted C's (the braces count); the gate on the nine builds as
  for the clean-up (every function it checks). A switch since 2026-10-22, where its cases only jump
  or return (the lifters' jump tables; before, a function with one kept all its gotos): a branch of
  many ways, its cases grouped by target in the order of their blocks, each group's statements up to
  where the ways meet (left with break) or falling into the next group's where the code goes there,
  the cases that return last; inside it break leaves the switch and continue still goes to the loop
  (tests/test_structure.py: 400 random functions with switches, compiled before and after, the same
  results on random arguments, every switch structured). On the 39 builds' 11,359 lifted functions
  gotos 12,950 -> 12,228 (FreeRTOS has few switches); a private firmware's readable C lost gotos too. The
  gate accepts the same functions as before on
  nine builds (gizmo a32, m3, m4, rv32; FreeRTOS GCC -Os, clang -O2, RISC-V GCC -Os, GCC -O0; the
  private firmware).
  The gotos left, by cause (2026-10-22; each goto by the block it leaves and the one it goes to, on
  the emitter's own facts: dominators, post-dominators, natural loops, strongly connected components;
  12,228 over the 39 builds): 8,267 (68 %) in code that never reaches a return, the tasks' endless
  loops, where no block had a post-dominator and so no branch a join; 2,978 (24 %) to a block several
  ways share that the first arm reaching it had taken (1,043 of them to at most three statements and
  a return); 495 (4 %) to such a block placed where its ways meet; 438 (4 %) in loops (exits to more
  places than the one taken for break, the block after a loop placed elsewhere, a break or continue
  of an outer loop); 50 (0.4 %) irreducible flow. Changed the same day: the post-dominators count the
  jumps back to the head of each cycle that nothing leaves as its ends (structure.endless_ends); a
  jump repeats a way without a branch of at most four statements (structure.TAIL, a return, break or
  continue counted) that ends in a return or a statement that never ends, where the code at the jump
  falls off to, at the loop's head or after it (Emitter.repeat; before, only a lone return); where a
  branch's ways never meet, the blocks it dominates that several ways reach follow the if (Ramsey's
  merge nodes; after every if they cost more gotos than they saved, docs/decisions.md); a region
  never emits a block that a region around it emits later (stops), and an arm without a join ends at
  the region's follow; a loop's follow is the nearest block the ways from all its exits pass (exits
  that only return aside), else the target most exits go to; the statements nothing reaches go. On
  the 39 builds gotos 12,226 -> 1,401 (a change of the clean-up had taken 2 of the 12,228), fewer in
  every build (GCC -O2's keep the most, 50-80; GCC -Os 25-51, clang 22-37, GCC -O0 11, MSP430 13),
  the readable lines 368,347 -> 340,305; the stage takes 0.07 s instead of 0.03 s for GCC -Os's 329
  functions. What is left, by cause: 1,183 (84 %) to blocks several ways share whose code branches
  before it returns (conditions a compiler threaded into a lattice), 64 (5 %) to longer ways without
  a branch, 115 (8 %) in loops, 31 (2 %) irreducible flow, 8 to loops entered from several places.
  tests/test_structure.py: besides the 800 random functions that return, 400 whose flow mostly never
  returns (their fuel runs out into bail(), which leaves by longjmp, also from loops that never end;
  called through setjmp), compiled before and after, the same results, every way of repeating used; a
  shape for each change. The gate accepts the same functions as before on the eight builds (gizmo a32
  35, m3 36, m4 44, rv32 31; FreeRTOS GCC -Os 316, clang -O2 275, RISC-V GCC -Os 251, GCC -O0 353).
  Not yet: a small shared region that branches repeated (an error block with its assert), conditions
  a compiler threaded into a lattice of shared blocks merged back (xTaskDelayUntil's; "No More
  Gotos"' condition-based refinement).
- [x] **Calls and parameters.** Parameters and a result as dread.regflow and dread callconv measured
  them. Done when the signatures are the measured conventions and the gate accepts as many as
  before. Done 2026-10-21 (dread.tidy trim_calls and params, readable.Context): a call passes the
  argument registers its callee reads at its entry (regflow), every call of a callee or none, the
  file's extern declaration following; a call whose value is read keeps the arguments in its
  result registers (newlib's empty lock functions leave the lock's address as their result, which
  __malloc_lock returns, and the lift check compares every function's result: trimmed to nothing,
  ten FreeRTOS functions failed); a parameter the body only copies into its register's variable is
  that variable, and trailing parameters nobody reads go. Calls of a variadic function or of one
  that calls one directly keep all their arguments: regflow counts the unnamed argument registers a
  variadic function keeps in its save area as no use (right for the lifter, which passes them all),
  and vAssertCalled, which hands its line number on to printf, trimmed to one argument printed
  garbage on clang -O2 (readable.variadic_reach; one level: nearly every FreeRTOS function calls
  vAssertCalled with constants of its own). The gate as before on the builds tried.
- [x] **Variables.** Frame words as locals (regflow's frames: a word a function only stores and loads
  itself, its address given to nobody), a register split into the variables it holds (each web of
  definitions and uses a variable of its own). Done when the gate accepts as many as before.
  So far (2026-10-21, dread.tidy frame_locals): the frame's words as variables (s36 for sp0 - 36)
  where no frame address leaves the function, every slot has one type and none overlaps, and no slot
  is read before something stores it; then the other passes treat them as any variable (a register
  spilled and reloaded becomes a copy). And the register webs (dread.tidy webs): definitions that
  reach a common use are one web, each web but the entry's a variable of its own (xQueueGenericSend's
  r6 the flag that the time-out was set, r6_1 the queue's message count). The flow is that of the
  body with its conditional blocks spread out (dread.tidy flat_view: if (c) goto then; ... end:,
  the statements shared), so a variable written inside an IT block or merged ifs is split too; a
  parameter keeps its name for its value at the entry only. The first version split neither (the
  flow by statements could not tell a block's reads apart, and it left parameters alone): the model
  had one name for prvAddCurrentTaskToDelayedList's r0, the ticks to wait, then the current TCB,
  then uxListRemove's result. (Before that, a version renamed only a conditional block's condition,
  not its store, and gizmo's parse_kv stored the wrong character: the gate failed it.) A parameter
  whose value at the entry nothing reads is none: trailing ones go, one before a parameter that
  stays is renamed aN (the same function had r2 and r3, written before every read, as parameters);
  plain declarations of one type share a line. Small unsigned literals are written plainly where C
  means the same (r3 == 2, not 0x2u: beside an unsigned operand, assigned, as an argument or a
  shift's count). Frames whose address goes to a callee (FreeRTOS's TimeOut_t on
  xQueueGenericSend's stack), since 2026-10-22: the slots wholly below the lowest address that
  leaves become variables too (outgoing stack arguments, locals nobody is given); from there up the
  frame stays as it is, since what the address is handed to may reach anything above it. Raw frame
  accesses left in readable C: FreeRTOS GCC -Os 771 -> 642, fewer on a private firmware too; the
  gate as before on the eight builds (gizmo a32, m3, m4, rv32; FreeRTOS GCC -Os, clang -O2, RISC-V
  GCC -Os, GCC -O0; the private firmware's waits for its cases made anew by generator 34). That
  region as one local object since 2026-10-22 (dread.frames, applied after the structure stage and
  the tables, as dread.shapes' structures are), on xQueueGenericSend `volatile stack36_t stack36;` of
  `typedef struct { u32 f_0; u32 f_4; u32 f_8; u8 pad_c[24]; } __attribute__((packed, aligned(4))) stack36_t;`
  above the function (a typedef: dread.cparse reads no structure in a declaration, and readable C
  stays the dialect): a field at each offset the function accesses (offsets from the lowest address
  that leaves), bytes between, up to sp0, aligned as the region's start is in the lifted C; the
  accesses are members (stack36.f_0), the addresses handed out a field's or the object's
  ((u32)&stack36.f_4, (u32)&stack36 + 4; with an index added, the object's). Only where every access
  in the region has a constant offset and one type and none overlaps, and the frame is the lifters'
  array (a wrapped function's is the real stack: printf, sprintf); else the function stays as it was
  (GCC -Os vPolledQueueConsumer and Producer: a push's word below the region reaches into it). One
  object for the whole region: split at each address that leaves it would read better but trust each
  callee to stay inside its piece. Volatile, as the raw accesses were (docs/decisions.md). Then the
  frames that still stayed raw (2026-10-22): a frame base computed once (GCC -O0's frame pointer,
  r7 = (sp0 - 16) + 0x0u; the ldrd, strd, ldm and stm blocks' a_2 = sp0 - 40) is written into every
  use it alone reaches, on any path (dread.tidy's address pass: sp0 is never written), so that its
  accesses are frame slots; a slot stored as a byte and read signed (strb, then ldrsb) is a variable
  of the unsigned type with casts ((s8)s9); a slot that may be read before it is written (a word
  nothing stored, handed to a call through a pointer) stays as it is instead of the whole frame; and
  a slot takes neither a name the function has (RISC-V's saved registers s4 and s8) nor a type it
  uses (s8, s16: such C would not compile; no function of the eight builds had one), but the next
  prefix (sl8). Regions as structures: FreeRTOS GCC -Os 55 functions, clang -O2 53, RISC-V GCC -Os 43,
  GCC -O0 56 (264 while the frame pointer hid the accesses: all but two without a field). Raw frame
  accesses left in readable C (`\(sp0 [-+] \d+` in the views of the lifted functions), main before
  -> after: GCC -Os 334 -> 40, clang -O2 282 -> 17, RISC-V GCC -Os 510 -> 41, GCC -O0 23 (35 counted
  before, the frame pointer's accesses not among them); the gate accepts the same functions as before
  on the eight builds. What is left: the wrapped printf and sprintf (the real stack), slots nothing
  wrote that a call through a pointer is handed (newlib's __sflush_r and _fwalk_sglue, prvReloadTimer:
  as it must be), and regions with two types at one offset or an access below reaching in
  (vPolledQueueConsumer and Producer store a word and read its upper half; RISC-V prvReceivingTask),
  which a union would express. A region nothing accesses but through its address (9 on GCC -Os, 11 on
  clang -O2) is a structure of padding; an array would say the same more plainly. MSP430 (paused):
  ulTaskGenericNotifyTake's readable C makes every case stack-dependent (refused, so it stays
  lifted), not traced; its critical sections are asm on the real stack beside the lifted frame.
  Done 2026-10-22: the gate has accepted as many functions as before on every build since the first
  version, and the frames now read as variables and structures on all four FreeRTOS builds; what is
  left is the next item.
- [x] **Frames: the rest.** A region with two types at one offset or an access below reaching in
  (vPolledQueueConsumer and Producer store a word and read its upper half; RISC-V prvReceivingTask)
  as a union; a region nothing accesses but through its address (9 on GCC -Os, 11 on clang -O2: a
  structure of padding) as an array of bytes; the wrapped printf and sprintf, whose frame is the real
  stack, stay as they are.
  Done 2026-10-23 (dread.frames; dread.tidy region_start, frame_index, frame_sums; docs/decisions.md):
  the region starts at the lowest address that leaves, moved down over a word that reaches into it
  from below (GCC -Os makes room for vPolledQueueProducer's 16-bit local by pushing a word and hands
  the local's address on: the region starts at the push, and frame_locals makes variables only below
  it); accesses whose bytes overlap share an anonymous union, a member each, one that starts later
  behind bytes of its own, so that each keeps its plain name: `union { u32 f_0; struct { u8
  pad_f_2[2]; u16 f_2; } __attribute__((packed)); } __attribute__((packed));` in vPolledQueueProducer's
  stack28_t, its accesses stack28.f_0 = r1 (the push) and stack28.f_2 = (u16)(stack28.f_2 + 1), the
  address (u32)&stack28.f_2; where several types share an offset their members say which they are
  (RISC-V prvReceivingTask clears its buffer by words and reads its first byte: f_0_u32 and f_0_u8). A
  region the function accesses at no constant offset is an array of bytes: on GCC -Os prvCoreATask
  `volatile u8 stack40[40] __attribute__((aligned(8)));`, sprintf((u32)stack40, 0x891u), and clang
  -O2 prvCoreBTasks's two strings sprintf((u32)&stack30[15], ...) and memset((u32)stack30, 0, 0xfu); an
  index is added to the array's address ((u32)stack20 + r5), not written stack20[r5] (past the array
  that is undefined in C, and GCC takes loop bounds from it). An index added to a frame address that
  the code reached by way of another (RISC-V's addi a5, sp, 32; add a5, a5, s0; lbu a5, -20(a5): sp0 -
  64 + 0x20u + s0_1 + 0xffffffecu) is written from the address its constants come to ((u32)&stack52 +
  s0_1; gizmo's apply_config (u32)&stack64 + 16 + r3, its line buffer), and that address counts as one
  that leaves, beside those written inside the sum, so a region starts no higher than before. Two
  faults of frame_locals found on the way, shown by small C in the tests, neither on the benchmark
  builds (no function's variables changed): a byte below the address that leaves but inside the word
  that reaches in became a variable (its read found what the byte's store wrote, not the word's), and
  so did a slot that an index may reach where the sum names a higher frame address than the one the
  index starts from. Raw frame accesses left in readable C (`\(sp0 [-+] \d+` in the views of the
  lifted functions, main at 01af32f -> after): GCC -Os 40 -> 33, clang -O2 17 -> 17, RISC-V GCC -Os
  41 -> 20, GCC -O0 23 -> 23. Regions as unions: GCC -Os 2, RISC-V GCC -Os 1; as arrays of bytes: GCC
  -Os 9, clang -O2 11, GCC -O0 1, gizmo a32, m3 and m4 2 each, rv32 1 (before: the unions' functions
  raw, the arrays structures of padding). What stays raw: the wrapped printf and sprintf (the real
  stack) and the slots nothing wrote that a call through a pointer is handed (newlib's __sflush_r and
  _fwalk_sglue, prvReloadTimer, GCC -O0's prvProcessExpiredTimer), as it must be. Every access stays
  one of its width at its place (the compiled union and array checked with GCC for ARMv4t, clang for
  Cortex-M3 and GCC for RV32: str, strh, ldrh, ldrb where the members lie). The gate accepts the same
  functions as before on the eight builds (gizmo a32, m3, m4, rv32; FreeRTOS GCC -Os, clang -O2,
  RISC-V GCC -Os, GCC -O0). Not yet: sums through 64 bits for a carry nobody reads, left where the
  clean-up folded them into a call's argument before the values pass, which rewrites assignments and
  returns only, saw them (clang -O2 prvReceivingTask's (u32)((u64)(u32)stack20 + r5 + 0)); lines with
  `(u32)((u64)` in the readable files of the four builds: 73, 50, 2 and 38, not looked at one by one.
- [ ] **Types.** The structures behind pointers, as C structures the readable C uses (p->f_58). Done
  when the gate accepts as many functions as before with them, and their fields against the
  source's (debug information) are measured on the bench.
  Analysis done 2026-10-21 (dread/shapes.py, dread readable --shapes [--score ELF]): unification of
  values and memory objects with offsets over every readable function; functions polymorphic (callees
  first by strongly connected components, a parameter's memory a template the call copies onto the
  argument's memory, a recursive link a pointer back up the template); objects that meet at
  different offsets kept apart; templates copies even of memory reachable from fixed addresses
  (docs/decisions.md). Fields found that are the source's (offset and size against the members of
  each pointer parameter's structure): FreeRTOS GCC -Os 618 of 622 (99 %), clang -O2 432 of 474
  (91 %), RISC-V GCC -Os 439 of 462 (95 %), GCC -O0 634 of 638 (99 %); 25-32 % of the source's
  members found (what the code reaches through those pointers). Weak: clang's lists (48 found, 20
  right) and RISC-V's event groups (35, 16), not traced.
  Rewrite done 2026-10-21 (dread.shapes plan and apply_types, the readable_types table written by
  the check): a structure per kind of memory, kinds linked only by calls between the argument and
  the parameter at their bases (and items at one offset of one kind), its fields at their offsets
  with byte gaps; a variable pointing to a base declared volatile struct s_N *, its accesses p->f_2c,
  other uses cast to u32, the structures above the function. Only a variable declared u32 that the
  function itself dereferences, given no C variable's address, not pointing into its stack frame
  (clang -O2 failed the gate on a u64 carry cut to a pointer and on a stack argument's address read
  past; GCC -O0's 253 frames became structures). Each rule measured (docs/decisions.md). The gate
  accepts the same functions as without types on all eight builds (gizmo a32, m3, m4, rv32; FreeRTOS
  GCC -Os 316/328 with 56 structures in 99 functions, clang -O2 275/286 with 54 in 80, RISC-V GCC -Os
  251/258 with 24 in 82, GCC -O0 353/357 with 38 in 92). The fields the plan declares for typed
  parameters against their source structures: GCC -Os 519 of 537 (97 %), GCC -O0 52 of 53, but
  clang -O2 470 of 687 (68 %) and RISC-V GCC -Os 521 of 848 (61 %) at first. Traced the same day:
  RISC-V keeps a register an asm block writes whole (a0 the queue, then a0 + 16 its first list), so
  the analysis now follows definitions, not names (a value per web, on the C before structuring, a
  variable typed only when all its webs point to one structure's base); a structure's base is the
  lowest offset a variable points to; kinds join only where their layouts fit (the same size where
  both have a field, none across another), the links with most identical fields first; and calls'
  results link no kinds (a function the source declares void hands back its last call's leftover,
  vListInsert's list). Now GCC -Os 526 of 528 (100 %), clang -O2 439 of 480 (91 %), RISC-V GCC -Os
  485 of 502 (97 %), GCC -O0 52 of 53, with more structures than kinds in the source (one source
  structure can be two types, each right; every access is at its offset either way). Typed
  C compiles with -fno-delete-null-pointer-checks (GCC dropped null checks of dereferenced pointers).
  A function whose readable C does not compile now stays lifted instead of ending the check (printi's
  structure with a pad of 4 GB: + 0xffffffffu is - 1, offsets are signed now). Open: a list reached
  through an item's pointer field is a kind of its own (joining kinds through pointer fields would
  join what a void * field holds, an item's owner: tasks and timers). (A pointer to a structure
  inside another, a task's xStateListItem at 4, and such structures as members: done, below.) An address
  computed once from a variable (an ldrd's, ldm's or stm's base, a_1 = r0 + 4, the frame's a_1 = sp0
  - 16 too) and read by a few statements on one straight path behind it, the variable unchanged on
  the way, is written into each of them with its offsets folded since 2026-10-22 (dread.tidy
  addresses): the accesses through it are members (r1 = r0->f_4; r2 = r0->f_8 where uxListRemove's
  ldrd read through a_1), and frame accesses through it frame slots the frame passes make variables.
  Such temporaries in readable C: FreeRTOS GCC -Os 173 -> 4, clang -O2 229 -> 4; raw frame accesses
  on GCC -Os 642 -> 308 (functions with any 84 -> 57). The gate as before on the eight builds. Left:
  the addresses of the lifter's stack-argument variables (a_1 = (u32)&s0). Fields
  typed as pointers since 2026-10-22 (the plan's pointers part): a word field that every object of
  its kind that points somewhere from it points to the base of one structure is declared volatile
  struct s_N * (forward declarations first); a load of it into a variable of that structure needs
  no cast, nor a comparison of two such pointers or a store of one; an integer stored into it is
  cast, its use as a number cast to u32. FreeRTOS GCC -Os 48 pointer fields in 33 of 65 structures,
  clang -O2 57 in 35 of 66, RISC-V GCC -Os 14 in 14 of 34, GCC -O0 31 in 23 of 49; the gate as
  before on the eight builds. Against the source (dread readable --score: the structures of typed
  parameters with a source type) 20 of 42 declared pointer fields are the source's pointers to a
  structure (GCC -Os 7 of 9, clang -O2 8 of 24, RISC-V 2 of 5, GCC -O0 3 of 4): the wrong ones come
  from kinds made one, a heap block (its free-list link at 0 a pointer to a block) and the queue
  allocated inside it (pcHead at 0); newlib's _cookie, a void * that holds its FILE, counts as wrong.
  Traced 2026-10-22 on clang -O2 with the analysis's objects: the heap code (pvPortMalloc, vPortFree)
  reaches below the pointer it hands out or frees (a header in front, a free list through it, sizes
  added to pointers), and its templates carried that onto every structure allocated or freed through
  it: queues, event groups, stream buffers and task blocks got pointers to themselves at +0 and +4. A
  callee whose template has fields below the pointer lends its caller nothing now and links no kinds
  (reaches_below); a value that met its own object at two offsets (a queue's pcHead: the queue itself
  for a semaphore, its storage behind it for the others) makes no pointer field (Shapes.interior);
  and the returns of a function whose result no caller reads (regflow's kept registers) stay apart.
  The same pairing, main before against after: GCC -Os 6 of 8 -> 6 of 7, clang -O2 8 of 24 -> 8 of 12,
  RISC-V 2 of 5, GCC -O0 5 of 7 (the counts of this morning's line came from older plans); no right
  one lost. Fields against the source: clang -O2 490 of 532 (92 %) -> 459 of 462 (99 %), the members
  found 28 % -> 26 %. Left, not traced: newlib's _cookie (three builds; right in fact, it holds its
  own FILE), a static queue's pcHead and pcWriteTo, a task block's field at 0x2c on clang, and on
  RISC-V a task block's fields at 0x10 and 0x24 and a list's count, typed as pointers (all but the
  _cookie traced and taken out 2026-10-22: kinds through pointer fields, below).
  A field that points to an item or to a list's end marker (a list's links) points to no one base and
  stays u32: 32 of GCC -Os's source pointers to a structure. A structure needs a field wider than a byte
  (gizmo's string walks made structures of two u8 fields). Names: the pilot asks for the structures'
  and their fields' names after the globals (dread pilot --task readable: each structure's definition
  with its fields' uses in up to four functions, eight a question), kept in readable_names' structs
  part by anchors (docs/formats.md); dread readable --score compares them with the source's type
  names (typedefs and tag) and member paths. Measured 2026-10-21 on the same 30 kernel functions of
  GCC -Os with Haiku 5.5 (globals, then structures, then variables, every answer checked): the 8
  structures they point to named TCB_t, Queue_t, ListItem_t, List_t, StreamBuffer_t (5 as the
  source's typedefs, all 8 sharing a word); 42 fields, 25 as the source (pxTopOfStack, uxPriority,
  pcHead, uxMessagesWaiting, cRxLock, pxContainer, uxNumberOfItems, pxIndex ...), 32 sharing a word;
  misses where the structure is flattened (a task's xStateListItem.xItemValue named xStateItemValue)
  or the source has dummies (StaticQueue_t's pvDummy1). Parameters 35 of 49 as the source (71 %), 46
  sharing a word (94 %); 7 of 14 globals. 39 answers, 41,180 tokens in and 41,378 out, about $0.025.
  The model knows FreeRTOS, and the functions' names tell it: a stripped firmware is the harder test.
  Structures held inside others as members since 2026-10-22 (dread.shapes kinds, plan, embed,
  apply_types; docs/decisions.md): where one kind's memory holds another's at an offset (a call hands
  outer + 4 to a function that takes the inner one; or v = w + 4 met after a call gave v an object of
  its own, and w - 4 from a member, its holder), the holder declares a member of the held structure
  (struct s_24 f_4;) where C can hold it: at an offset of its alignment, ending where the holder's
  fields reach, overlapping no other member, no field across its ends, the holder's fields inside it
  fitting the held structure's, which takes them over. The holder's accesses there are p->f_4.f_8,
  its address &p->f_4, and a variable that holds only such addresses is a pointer to the held
  structure (r5 = &r0->f_4); a held structure's pointer fields are its own kind's (what the holders'
  objects hold there came from callees' templates, kinds never joined, and made the item's previous
  link no pointer). Members: FreeRTOS GCC -Os 7 (the task block's items at 4 and 0x18, the queue's
  lists at 0x10 and 0x24, the event groups' list at 4), clang -O2 13, RISC-V GCC -Os 11, GCC -O0 10;
  in the structures of typed parameters 8, 8, 7 and 8, every one where the source holds a structure.
  Against the source (dread readable --score; the plan's fields of typed parameters, members'
  flattened): GCC -Os 151 of 154 -> 152 of 155, clang -O2 209 of 212 -> 210 of 213, RISC-V 113 of 116
  -> 118 of 121, GCC -O0 141 of 144 -> 146 of 149; pointer fields 6 of 7 -> 9 of 10, 7 of 11 -> 9 of
  13, 2 of 5 -> 4 of 8, 5 of 7 -> 6 of 7 (the task block's items' previous links are pointers now; the
  items' owners, void * in the source, are not; RISC-V's list count, a pointer to the list since
  before and not traced, now counts once more in each list a queue holds); the source's pointers to a
  structure left integers 33 -> 30, 54 -> 52, 40 -> 39, 40 -> 39. The analysis's fields (dread
  readable --shapes --score) as before: 658 of 662, 449 of 452, 430 of 451, 679 of 683. Readable C
  (dread.cmetrics over the readable functions): members 656 -> 690, 629 -> 661, 405 -> 428, 672 ->
  705 (inside held structures 64, 54, 54 and 50;
  members' addresses 34, 29, 23 and 33, (u32)r0 + 0x10u before), raw accesses 327, 372 -> 369, 196,
  327, typed variables 222, 205 -> 208, 157, 244, casts 1293 -> 1304, 1103 -> 1116, 689 -> 700, 1062 ->
  1060 (an integer stored into or read from the items' previous link, now a pointer). The gate
  accepts the same functions as before on the eight builds (gizmo a32 35, m3 36, m4 44, rv32 31;
  FreeRTOS GCC -Os 316, clang -O2 275, RISC-V GCC -Os 251, GCC -O0 353).
  On the list functions themselves little changes (the item takes its owner field at 12 from the task
  block's accesses through its held item): the readable C that changes is that of the functions
  reaching lists and items inside queues, task blocks and event groups. Left: kinds stay fragmented
  (a task block reached through an item's owner, a queue whose lists go only to functions that touch
  one field of them, holds nothing: taken up below); a list's end marker at 8, a 12-byte mini item
  used as a 20-byte item, is no member (the pun still types a pointer to it as an item pointer, as
  the source's pxIndex is); the pilot's names for held members (xStateListItem) not measured yet.
  Kinds joined through what pointer fields hold since 2026-10-22 (dread.shapes kinds: pointer_slots,
  same_slots, several; docs/decisions.md). A kind's word fields, read through the structures it holds
  (a task block's field at 0x14 is its item's container at 0x10), are slots, and what one slot points
  to at structures' bases is one kind: the lists an item's container points to are the list kind,
  wherever they were reached. A slot holds several kinds, joins none and is no pointer field
  (void_slots) where the evidence says so: the layouts there do not fit together; it points back to
  the structure that holds it in a structure two kinds hold (an item's owner: task blocks and timers
  hold items; on GCC -Os only the task blocks' stores lift); or two of its targets are kinds one
  function takes as different parameters (a list and an item, five words each). There what it holds
  joins the one kind it may hold whose layout holds all its fields, where exactly one does (an owner
  a function uses as a task block). Memory with fields in front of its base is inside a larger
  structure and says nothing (a task block reached at its event item had made the items' kind the
  task block's). Templates carry what their memory holds (the calls that handed it at an
  offset), so memory copied from a callee holds its items too. Before the joins, the values that made
  fields point where the source does not, each traced: a use the callee ignores joined the
  definitions that reach it (clang -O2's shared tail return vPortExitCritical(r0): the task block's
  priority at 0x2c a pointer to its list container), and so did RISC-V's ecall, whose asm reads a0 to
  a7 (a list's count a pointer to the list, also as a queue's 0x10 and 0x24); now only uses whose
  value the analysis may take join definitions, an asm's registers are values of their own. GCC
  stores NULL with a register it has just found 0 (known_zero; the mutex holder at 8 had become one
  value with pcHead, which made the queue's pcHead and pcWriteTo hold tasks), and a register a
  branch found 0 reaches no use past it (reaching_past_zero: GCC 9 -Os's prvSearchForNameWithinSingleList
  returns the list's count where it is 0 and a found task block elsewhere, from one return, and every
  list's count was a pointer to a task block). A stack argument read
  through its address is the parameter (prvInitialiseNewTask's task block on ARM: its template was
  empty). Parameters that end as one value (pcHead: the queue itself or the storage given beside it)
  pass on the templates only of those the function uses as a pointer, and the value points to no one
  structure (the static queue's pcHead and pcWriteTo were pointers to the queue, its storage took the
  queue's fields, and at GCC -O0 a dynamic queue's storage behind it took them again).
  Against the source (main before -> after; dread readable --score's pairing and dread readable
  --shapes --score): pointer fields of typed parameters' structures that are the source's pointers to
  a structure GCC -Os 9 of 10 -> 18 of 21, clang -O2 9 of 13 -> 39 of 46, RISC-V GCC -Os 4 of 8 -> 27
  of 27, GCC -O0 6 of 7 -> 12 of 16; the source's pointers to a structure left integers 30 -> 21, 52
  -> 16, 39 -> 13, 39 -> 30. Wrong: newlib's _cookie; StaticQueue_t's dummies, void * in the source,
  holding the queue's lists' items and the mutex holder in fact (clang 6, GCC -Os and -O0 1); a second
  family of items, which no slot joins to the first, whose owner points to task blocks only (GCC -Os,
  -O0); GCC -O0's pxStack, an array its overflow check reads at four constant offsets. The plan's
  fields of typed parameters 152 of 155 -> 159 of 161, 210 of 213 -> 219 of 221, 118 of 121 -> 113 of
  116 (one structure fewer to pair: two parameters' lists are one now), 146 of 149 -> 163 of 167;
  members declared 7 -> 12, 13 -> 26, 11 -> 18, 10 -> 14 (every one where the source holds a
  structure); structures 55 -> 52, 62 -> 51, 34 -> 28, 49 -> 46. The analysis's fields (dread
  readable --shapes --score) 658 of 662 -> 668 of 673, 449 of 452 -> 479 of 483, 430 of 451, 679 of
  683 -> 780 of 787. Structures per source structure (typed parameters' structures, and those their
  pointer fields and members reach, paired with the source's): 13 source structures in 18 -> 19 (the
  pointer fields reach more of them), 11 in 24 -> 22, 7 in 13 -> 12, 11 in 21 -> 12 in 20; kinds
  per source structure (the kinds the parameters point to in the plan's analysis, before the
  structure stage) 46 for 13 -> 47 for 15, 55 for 13 -> 55 for 15, 31 for 9 -> 30 for 9, 56 for 14 ->
  54 for 17; one of ours with two source structures only for the
  source's mirror types (xSTATIC_QUEUE and QueueDefinition, of one size). Readable C (dread.cmetrics):
  members 690 -> 730, 661 -> 701, 428 -> 446, 705 -> 723; raw accesses 327 -> 287, 369 -> 332, 196 ->
  181, 327 -> 312; typed variables 222 -> 250, 208 -> 232, 157 -> 166, 244 -> 255; casts 1304 ->
  1345, 1116 -> 1102, 700 -> 721, 1060 -> 1082 (a pointer field met as a number: an argument, which
  the lifted dialect declares u32, or a variable that stays an integer; a pointer field copied into
  another of its structure is written without casts since, which took 8, 2, 0 and 18 off). The gate
  accepts the same functions on the eight builds (gizmo a32 35, m3 36, m4 44, rv32 31; FreeRTOS GCC
  -Os 316, clang -O2 275, RISC-V GCC -Os 251, GCC -O0 353), name by name. The analysis and the plan
  take 0.6 s on GCC -Os as before. Six builds besides the gate's, the plan of all their lifted
  functions: pointer fields that are the source's pointers to a structure, GCC -O2 10 of 19 -> 31 of
  42, clang -Os 11 of 19 -> 16 of 24, RISC-V clang -Os 5 of 8 -> 35 of 35, Cortex-M4F GCC -Os 10 of 12
  -> 18 of 21, RISC-V GCC -O2 5 of 7 -> 13 of 14, GCC 9 -Os 6 of 6 -> 20 of 24; left integers 33 -> 9,
  37 -> 19, 54 -> 16, 35 -> 21, 20 -> 12, 34 -> 20; members 8 -> 19, 8 -> 10, 7 -> 18, 8 -> 12, 7 -> 11, 8
  -> 15; structures 44 -> 42, 60 -> 50, 47 -> 44, 60 -> 52, 24 -> 22, 62 -> 55 (and clang 14 -Os 8 of 15
  -> 15 of 26, RISC-V clang 19 -Os 10 of 14 -> 19 of 22, GCC 14 -O2 8 of 13 -> 26 of 40, left integers 57
  -> 43, 47 -> 32, 28 -> 7). Wrong there besides the kinds above: fields that point into arrays (a
  queue's storage through pcHead, pcWriteTo and pcReadFrom, a task block's pxStack, a stream buffer's
  buffer): the joins make one field's arrays one kind, which becomes a structure (offsets read at
  constants in an array look like fields) and the field a pointer to it; and _reent's
  __sglue._next, a struct _glue * that points to the FILE structure (__sfp keeps a FILE and a glue in
  one register: the pairing counts that FILE with _glue, score_pointers counts the field right);
  most of main's others are gone (GCC -O2's task block fields at 0x2c, 0x40 and 0x44 and its queue's
  pcHead and pcWriteTo, RISC-V GCC -O2's list count). Left: the parameters' kinds stay fragmented
  where no analysed code hands one object to two of the functions (clang's six stream buffer kinds:
  their callers do not lift; timers, whose functions touch one field each, link nothing: memset's
  rule); lists and items
  in two families on GCC -Os and -O0 (vListInsert walks a list from its end marker, and unification
  reads every item reached so as the end marker itself, so the code that inserts links nothing; the
  puns that would join them joined lists with items); the measurement counts StaticQueue_t's dummies
  and _cookie wrong. The counts of structures per source structure are dread readable --score's
  since (shapes.score_kinds: typed parameters' structures, and those their pointer fields point to
  and they hold; --shapes --score: the kinds the parameters point to). On the C after the structure
  stage, which only dread readable --shapes reads, RISC-V GCC -Os pairs the item's kind with
  vTaskPlaceOnUnorderedEventList's list, both 20 bytes, besides the list's with the queue's, which
  main pairs too (the plan, made before the structure stage, pairs neither): there an if that holds
  an asm block is one node of the flow graph, every value of a5 in it one (vTaskPrioritySet), which
  made a task block's item value a pointer to a task block, so uxTaskResetEventItemValue's result one
  that holds items, and the event group that met it held an item and its list at 4; not taken further.
- [x] **Structures of globals.** Static data reached at fixed addresses, one macro a field
  (g_2000004c_u32, *(volatile u8 *)0x2000a0f1), grouped into the structures it was declared as:
  `g_state.mode` for the fields one base and its offsets reach in several functions. Found on the private
  real-life test (2026-10-23): the structures behind pointers (Types) left its accesses to static data at
  fixed addresses as they were. Code decides it: the accesses through one base plus
  constant offsets in each function, the bases several functions share, the extent up to the next base
  or a table's symbol. The hard part: a section anchor (GCC reaches unrelated globals from one base:
  gizmo's dispatch table and FreeRTOS's kernel variables) is not a structure; measured against
  FreeRTOS's debug information (which globals are one structure, which are separate variables near
  one another), and the gate keeps every access one volatile access of the same width and address.
  Done 2026-10-23 (dread/statics.py; the readable_types table's statics, dread.naming apply_globals and
  static_names; docs/decisions.md). The evidence is the machine code's, of every function: a forward
  pass follows the constants registers hold and the address each was made from (a literal, movw and
  movt, lui with addi, then moves and adds); a load or store at such a register plus a constant is an
  access from that base. A base's structure reaches as far as its accesses over every function and
  ends where something else starts (another base, an address handed out, an indexed array's start,
  a data symbol, a startup range, the region's end); its fields are every access inside it, through
  any base, laid out naturally aligned as dread.shapes lays out a kind; two fields, one wider than a
  byte. The anchor: a base counts only where one of its functions reaches a neighbour within 64 bytes
  apart from it (statics.separate_bases): a compiler that makes a base per object reaches neighbours
  through bases of their own, an anchor is the one base its file reaches everything through. Data
  symbols only end structures (one at the first variable of an anchor's run would make the run one);
  RISC-V's gp and upper halves made by lui alone are no bases; device registers are left out. Readable
  C: `r3_1 = g_200299b4.f_c;`, `#define g_200299b4 (*(volatile struct g_200299b4_t *)0x200299b4u)` and
  the structure above the function; an access of another type than its field stays the macro it was
  (a word at a structure's address it holds no word for: g_20000030_u32).
  Against FreeRTOS's debug information (`dread readable --statics --score ELF [--no-symbols]
  [--every-base]`, the 39 builds of the benchmark and GCC -Os and -O2 built without -fdata-sections,
  where GCC's section anchors reach every file's variables): no structure lies over several source
  variables on any build; each lies inside one (but two of Cortex-M4F GCC -Os, where no variable has
  debug information), each field that can be checked is a leaf of its variable at its offset with its
  size. Per build (code alone): GCC -Os 15 (11 arrays of scalars such as ulCycleCounters, an array of
  structures, xParameters; 3 structures, xStart, xSuspendedTaskList, xPendingReadyList), 33 fields;
  GCC -O2, GCC 6 to 14 -Os and GCC 10 to 14 -O2 15, GCC 4, 5 -Os and 9 -O2 14, GCC -O0 5, clang -O2 13,
  clang -Os 4, clang 12 to 19 -Os 5, Cortex-M4F GCC -Os 17, clang -Os 4, RISC-V clang 14 to 18 -Os 3,
  19 2; RISC-V GCC (all six) and RISC-V clang -Os none, MSP430 none (no value_step). Of the source's
  structures and arrays that the code reaches at fixed addresses at two places or more, GCC -Os 14 of
  16 grouped whole, 1 in part; clang -O2 11 of 14, GCC -O0 4 of 6, RISC-V GCC -Os 0 of 16. Counting
  every base instead (--every-base): 23 and 21 structures on the two builds without -fdata-sections
  (each a source file's variables), 20 on RISC-V clang -Os (clang merges a file's globals), one on
  thirteen ARM builds (newlib's malloc state, __malloc_free_list and __malloc_sbrk_start), every one
  over several source variables; the evidence keeps all of them out, and with them 56 of the 385 that
  lie inside one variable over the 39 builds (ARM 31 of 349, RISC-V 25 of 36: newlib's FILE array
  __sf, xQueueRegistry, xCoreBMessageBuffers, RISC-V's lists and xParameters). With the data symbols (the
  default) the same structures on every build. Readable C of the gate's readable functions (main ->
  after; dread.cmetrics' new figure fixed_members): GCC -Os 15 structures in 12 functions, uses of
  fixed-address macros 1421 -> 1366 and 55 members, macro definitions 524 -> 507 (19 of them
  structures); clang -O2 14 structures (the gate's plan adds the readable C's accesses as fields) in 16
  functions, 1610 -> 1525 and 85 members, 564 -> 531 (17); GCC -O0 5 in 7 functions, 1284 -> 1269 and
  15, 517 -> 514 (7); RISC-V GCC -Os none. FreeRTOS reaches its structures mostly through pointers,
  so the change is small there; the private firmware's code is the case it was made for. Example,
  GCC -Os prvAddCurrentTaskToDelayedList (listINSERT_END into xSuspendedTaskList inlined), main:
  `r2_1 = (volatile struct s_9 *)g_2002998c;` ... `g_20029988 = g_20029988 + 1;`, now `r2_1 =
  (volatile struct s_9 *)g_20029988.f_4;` ... `g_20029988.f_0 = g_20029988.f_0 + 1;` (pxIndex and
  uxNumberOfItems of one list). The gate accepts the same functions as before on the eight builds
  (gizmo a32 35, m3 36, m4 44, rv32 31, none of them with a structure: gizmo is built without
  -fdata-sections and its code alone does not tell g_config from an anchor; FreeRTOS GCC -Os 316,
  clang -O2 275, RISC-V GCC -Os 251, GCC -O0 353); the members compile to the same loads and stores as
  the macros on every gizmo build (tests/test_statics.py). The pass takes 0.4-1.6 s a FreeRTOS build.
  Names: `dread pilot --task globals` (and readable) shows the readable C with the structures; each
  field is asked as its own global (`g_20000030.f_4`, keyed by its address and type, which
  static_names takes for the field) and the structure as one more (`"0x20000030 struct"`), so the
  readable C reads `config.volume` (tests/test_statics.py, the model played). Not measured with a model
  yet. Left: a structure whose functions use nothing next to it is missed (RISC-V's lists: their
  callees reach them through a parameter, vListInsertEnd(&xSuspendedTaskList, ...), which would show
  the object's extent, from an analysis of what a function reaches through its parameters, the base
  registers item's "argument plus a constant"); the structures' addresses where the code hands them out
  (`r2_2->f_4.f_10 = (volatile struct s_25 *)0x20029988u;` could be `&g_20029988`: a variable given
  one then counts as given a C variable's address and loses its type, dread.shapes _uses); arrays of
  scalars written as arrays (`g_20000c9c[1]`, not `.f_4`) and indexed accesses into arrays of
  structures (pxReadyTasksLists[uxPriority].pxIndex, `0x200299f8u + 0x14u * r9_2 + 4` now); a
  structure reached from several bases comes out as pieces; dread.shapes still makes an object per
  address, not per structure of static data; a target whose data symbols are complete (an ELF's) could
  let them make structures too (gizmo's g_config), which needs the target to say so.
  On the private real-life test (2026-10-23) the gate accepted the same functions with the structures
  of static data as without; accesses at fixed addresses became members of them (on FreeRTOS a twentieth).
  With the arrays (the same day) indexed members and addresses read as & came in, and the gate still
  accepted the same functions.
  The four left above, done later on 2026-10-23 (docs/decisions.md, "Arrays of static data, addresses
  as &, and what callees reach"). Arrays (statics.find_arrays): a load or store of the lifted functions'
  readable C at a constant plus an index times a scale (indexed_accesses; dread.tidy folded the
  arithmetic: 0x200299f8u + 0x14u * r9 + 4, (((r2 << 2) + r2) << 2) + 0x20029988u, also through a
  variable that holds the index times the scale) says that elements of that size lie there; accesses
  of one scale less than an element apart are one array, starting at a data symbol within an element
  below, else where the code made the address, else at the lowest constant, counting the elements its
  accesses reach and, through a base that counts (separate_bases), the constant places; its elements'
  fields are every access inside it. Without readable C (a function that does not lift) a structure
  whose fields are one type one after another and which the machine code indexes is an array of that
  type (item 1's rule as written). Readable C: `g_200299f8[r9].f_4`, `g_20000c74[1]`, through a macro
  that is a pointer to the first element (no bound GCC could derive loop bounds from), the index
  through a variable that held it times the scale written as the index where straight statements
  lead from one to the other with nothing writing either (statics.straight_facts; the variable's
  assignment goes when nothing reads it any more), and so through a variable that holds an element's
  address. Addresses: where readable C uses the address of an object, a field or an element whole (a
  call's argument, an assignment's value, an operand of == or !=, a result) it reads as such:
  `(u32)&g_20029988`, `(volatile struct s_24 *)&g_20029988`, `(u32)&g_200299f8[r7]`; dread.shapes'
  _uses keeps such variables typed (only the function's own variables' addresses count). What callees
  reach (statics.handed): the address of static data handed to a callee is a base of what the callee's
  template of that parameter reaches (dread.shapes), counted without the neighbour test; memory a walk
  reached (memcpy's, also through the functions that hand it on: Shapes.walked) is none. On the way:
  dread.shapes took an index into static data for a pointer (r3_3 + 0x200299fcu, r3_3 twenty times a
  priority: a structure with a pad of 512 MB in vTaskPrioritySet); the plan of static data is made
  first now and such an offset is no field (Shapes.static_offset); a read of a field's width and the
  other signedness is a cast ((s16)g_200018de[r6]), structures' too.
  Against the debug information (`dread readable --statics --score`, which now makes the lifted
  functions' readable C and shapes too, 2-22 s a build) on the 39 builds and GCC -Os and -O2 without
  -fdata-sections: no structure and no array over several source variables; structures 331 -> 585,
  arrays 594; every array that lies in a source variable has its element size (583; 11 are tables in
  ROM at no variable); 5,414 of 5,526 fields the source's leaves (98 %; most of the rest in GCC 4.9's
  and 5's function-local static buffers, which the score does not place, and newlib's FILE, whose
  _flags and _file one store writes as a word); source structures and arrays reached at two places or
  more: grouped whole 279 -> 335, in part 30 -> 101. Per build, structures before -> after and arrays:
  GCC -Os 15 -> 21, 13 (the lists and static buffers from what callees reach; pxReadyTasksLists,
  xQueueRegistry, ulCycleCounters and ten more arrays), GCC -O2 15 -> 21, 13, GCC 4.9 to 14 -Os and 9 to
  14 -O2 14-15 -> 16-22, 12-18, clang -O2 13 -> 21, 13, clang -Os, 12 to 19 -Os and Cortex-M4F clang -Os
  4-5 -> 12-15, 23-24,
  Cortex-M4F GCC -Os 17 -> 23, 13, GCC -O0 5 -> 14, 22, RISC-V GCC (all six) 0 -> 7 (its lists), 8-10,
  RISC-V clang 0-3 -> 7-10, 13-15, the two without -fdata-sections 0 -> 13 and 14, 12 each, MSP430 none.
  The readable C of the gate's readable functions (main -> now; dread.cmetrics' new fixed_indexed and
  fixed_addresses): GCC -Os 12 -> 42 functions with members or addresses of static data, raw accesses
  280 -> 232, members of static data 55 -> 112 (56 of them indexed), addresses as & 61, fixed-address
  macro uses 1366 -> 1365, macro definitions 507 -> 554 (19 -> 35 structures, 32 arrays), statements
  6040 -> 6029; clang -O2 16 -> 41, 332 -> 279, 85 -> 148 (62), 75, 1525 -> 1524, 531 -> 580; GCC -O0 7 ->
  43, 312 -> 237, 15 -> 97 (81), 52, 1269 -> 1268, 514 -> 572; RISC-V GCC -Os 0 -> 22, 162 -> 152, 0 -> 26
  (20), 36, 1011 -> 1005, 401 -> 431. Examples, GCC -Os: vTaskSwitchContext `if (*(volatile u32
  *)(0x200299f8u + r4 * 0x14u) == 0) vAssertCalled(0x10e9u);` ... `r2_2 = r0->f_4;` ... `*(volatile u32
  *)(0x14u * r4 + 0x200299f8u + 4) = r2_3;` now `if (g_200299f8[r4].f_0 == 0) vAssertCalled(0x10e9u);`
  ... `r2_2 = g_200299f8[r4].f_4;` ... `g_200299f8[r4].f_4 = r2_3;` (listGET_OWNER_OF_NEXT_ENTRY on
  pxReadyTasksLists[uxTopPriority]); xTaskResumeAll `r8_3 = 0x14u * r8_2; r10_1 = (volatile struct s_48
  *)(0x200299f8u + r8_3);` ... `*(volatile u32 *)(0x200299f8u + r8_3) = r1_7 + 1;` now `r1_6 = (volatile
  struct s_47 *)g_200299f8[r8_2].f_4;` ... `r3->f_14 = (volatile struct s_46 *)&g_200299f8[r8_2];` ...
  `g_200299f8[r8_2].f_0 = r1_7 + 1;`, and `prvResetNextTaskUnblockTime((u32)&g_200299b4)`;
  prvAddCurrentTaskToDelayedList `r2_2->f_4.f_10 = (volatile struct s_25 *)0x20029988u;` now `...
  (volatile struct s_24 *)&g_20029988;`; GCC -O0 `if (g_20029988[s24].f_4 == (u32)&g_20029988[s24].f_8)
  g_20029988[s24].f_4 = g_20029988[s24].f_c;` (pxIndex against &xListEnd); RISC-V GCC -Os
  `g_800958b0.f_0 = g_800958b0.f_0 + 1;` (xSuspendedTaskList's count, a list no function reaches next to).
  The gate accepts the same functions as before on the eight builds (gizmo a32 35, m3 36, m4 44, rv32
  31, gizmo with arrays on a32 and rv32 now; FreeRTOS GCC -Os 316, clang -O2 275, GCC -O0 353, RISC-V GCC
  -Os 251); elements and addresses compile to the same loads and stores as the raw accesses on every
  gizmo build (tests/test_statics.py). The new evidence costs about 0.2 s a FreeRTOS build in the gate.
  Left: an array's count is what the code reaches, not the source's; accesses at a further element
  through a base of their own (arr[i + 1]) can make an array of their own; arrays inside structures
  (an index into a member) are arrays of their own; an index the readable C computes far from the
  access (a variable assigned on several paths) stays raw; what callees reach is taken only from the
  lifted functions' calls; a structure reached from several bases comes out as pieces; dread.shapes
  still makes an object per address, not per structure of static data; the pilot asks for an array's
  and its elements' fields' names (g_x, g_x[].f_4: the model played in tests/test_statics.py) but no
  model has answered yet.
- [ ] **Memory.** Plain accesses for RAM and the stack, volatile kept where order is behaviour (the
  peripheral ranges of the target's memory map). Done when the gate and the system runs agree.
  Thought 2026-10-21: plain accesses let GCC merge and hoist loads, and a loop that waits for a flag
  an interrupt sets in RAM would no longer see it change; the original's accesses all happen. The
  types item gets readable accesses without that: volatile in the pointer's declaration, p->f at the
  use. This item waits until that is done and it is clear what is left.
  Where it stands (2026-10-23, the structures behind pointers, of static data and its arrays done):
  volatile now sits in declarations and macros (a struct pointer's type, g_x's definition), where it
  does not get in a reader's way; what still shows it is the raw accesses, *(volatile u32 *)(r3 + 8),
  some hundreds on a FreeRTOS build, which plain accesses
  would shorten by a word each. Against that, plain accesses let the compiler merge and hoist loads (a
  wait for a flag an interrupt sets would spin forever) and the gate's per-access identity no longer
  holds. Not now: the raw accesses are better served by more of them becoming members (structures,
  arrays) than by losing their volatile.
- [x] **Results in r2 and r3, and registers a caller leaves.** A function whose callers read r2 or r3
  after the call (an old ARM compiler's __value_in_regs: a structure of up to four words returned in
  r0-r3) is refused while any original caller reads them, since C under the AAPCS returns at most
  r0:r1; so is one that reads registers its caller left (the stale readers). The private real-life test
  has such functions. A
  way: lift such a function with all
  of its callers as one group, the extra results handed between the group's C functions through a
  structure (their ABI among themselves is free), refused while a caller stays machine code; or a
  thin asm wrapper that loads r2 and r3 from that structure for callers that stay machine code.
  Measured on what a public build has of it first (gizmo or a test program with __value_in_regs-like
  returns in asm), then on the private firmware's refusals.
  Done 2026-10-23 with the wrapper, not groups (docs/decisions.md): a function whose callers read
  registers beyond r0:r1 that it sets (dread.lift more_results: r2, r3 and r12 on ARM, a2-a7 and t0-t6
  on RISC-V) gets a naked entry at its place that reserves an area, calls the C body with the area's
  address and loads the registers from it before it returns; only lr (Thumb-1: a saved register of
  r4-r7 not handed in; RISC-V: ra) changes before the body runs. A caller that reads them behind the
  call makes a bound call (RISC-V got bound calls for it), and so do the function's own tail calls and
  calls whose registers it hands back; with stack arguments its frame is the wrapped one (not on
  Thumb-1). A bound call hands r12 to a callee that reads it as its caller left it. The generic stubs
  answer the extra registers too (docs/formats.md, wide_results.txt). The cases compared r2 and r3
  already (dread.runner compares every register the callers read after the call). Measured first on
  programs of our own in assembly (tests/test_lift_results.py: ARM state on ARMv4T, Thumb-2 on a
  Cortex-M3, Thumb-1 on a Cortex-M0, RISC-V): the four-word function, the two that hand its words on
  and those that hand back more were refused ("its callers read r2, which it sets ...; C returns r0
  and r1 only"), on RISC-V their callers too ("reads a2 behind the call of ..."), a caller of a helper
  that reads r12 ("calls ip_shift_tail, which reads r12 its caller leaves"); now all of them are
  accepted except what the decision leaves out, a wrong r3 is caught, callers in machine code read the
  registers from the entry, and the system run agrees on the whole image. On the 39 FreeRTOS builds
  23 of 13,684 functions go from refused to lifting, on six builds (GCC 8, 9 and 14 -Os: 3, 3 and 4
  kernel functions around vAssertCalled; the hard-float builds: libgcc's __adddf3, __aeabi_dcmpgt,
  __aeabi_d2f and pvPortMalloc with GCC, two of them with clang, and on both the two tasks that call
  __aeabi_d2f, through r12; RISC-V with GCC 10: prvInitialiseNewTask_constprop_0 and its two callers);
  every other function's C is byte for byte as before, and gizmo's lifted tables are unchanged.
  Accepted by the lift check (the system run and the whole image agree on all six): 11 of the 23
  (341 -> 343 on GCC 14, 337 -> 338 on GCC 9, 336 -> 337 on GCC 8, 356 -> 358 and 344 -> 347 on the
  hard-float builds with GCC and clang, 272 -> 274 on RISC-V), 2 refused by the check for another
  reason (other code branches into __adddf3), 10 dropped: vAssertCalled for its case generation (as
  where it always lifted), four for coverage, three whose r2 is on one path what an earlier callee left
  (xTaskResumeAll on GCC 14, xTimerIsTimerActive on GCC 8 and 9; see the over-reading below).
  Left: the private firmware's refusals (not measured here); a bound call that passes stack
  arguments (done 2026-10-24, the item below; still refused on Thumb-1); the stale readers on clang
  targets and on RISC-V, where the C gets no register as
  its caller left it (the entry's area could carry them; no public function would gain: those there
  are refused for other reasons); groups as an optimisation (the entry's bytes where every caller is
  C). On the public builds none of the extra results is one a programmer meant: dread.regflow's
  over-reading, a read under a condition behind a write under the same condition in another block
  taken for a read of what the call left (prvStaticallyAllocatedCreator's it ne; movwne r2 ... it ne;
  strne r2 on GCC 8, 9 and 14; libgcc's __extendsfdf2 on the hard-float builds), and on RISC-V with
  GCC 10 a callee that copies a register nothing set (prvAddNewTaskToReadyList's mv s4, a4). The
  over-read functions now carry an entry they would not need, and where the register is on some path
  what an earlier callee left, the C cannot have it (the three dropped above); a fix in dread.regflow
  would spare both. The readable C of such a function shows the naked entry and
  leaves the body as lifted C (as for a wrapped function), and lr read as a value in the body is the
  entry's return address (likewise). The body is a helper of its function's object and goes into the
  room for the C wherever the entry went: on the private real-life test, short of room, the room
  filled with bodies alone, the overlay named nothing to drop, and the lift check's build failed
  (2026-10-23). The room's report now counts each helper with the one function of the original its
  object defines (dread.overlay.helper_owners), so the check drops that function.
- [x] **Stack arguments through a bound call.** A call of a function that reads a register its caller
  leaves (r4-r11), or whose result the caller reads behind the call, and that also takes stack
  arguments was refused ("calls X, which reads r5 its caller leaves"; "reads r2 behind the call of
  X"): Lifter._bound_call passed none. Found on the private real-life target (not measured
  here). Done 2026-10-24 (docs/decisions.md): the asm statement reserves the
  stack-argument words below the stack pointer (8-byte alignment kept, 16 on RISC-V), stores them as
  the caller's C has them (_stack_arg_values, shared with _call) before the bl or call, and restores
  the stack pointer after; frame_effects counts the words, so the caller's frame keeps its outgoing
  area. Programs of our own (tests/test_stack_args.py: a callee that reads r5 and takes a fifth
  argument, ARM state and Thumb-2 by GCC and clang; tests/test_lift_results.py: quad_fifth_user,
  which reads r2 behind a wrapped callee with a stack argument, and use on RISC-V, which reads a3
  behind one): refused before (the old gate restored by a monkeypatch), accepted after through the
  lift check, every case agreeing and every instruction run, also with Ubuntu 22.04's and Debian 10's
  compilers; a stack argument stored at the wrong offset is dropped. The 39 FreeRTOS builds have no such call (no function there both takes stack
  arguments and is bound): nothing changes there. Left: Thumb-1 (its str needs a low register the
  compiler may not give the value: refused with its own reason); a normal call with stack arguments
  on RISC-V (only a bound one carries them, as before).
- [x] **A callee whose stack pointer is not static.** A call of a function whose stack pointer is not
  static (sp set from an auipc or from a register the analysis does not know, a variable-length
  array's sub sp, sp, rN) was refused for every caller ("calls X, whose stack arguments are not
  known"). Found on the private real-life target (not measured here). Done 2026-10-24
  (docs/decisions.md): scan_stack_args counts the words such a callee reads above its entry, walking
  past the point where its stack pointer is lost and following the registers that still hold a frame
  address (a frame pointer set from sp at the entry); a caller that stores those words into its
  outgoing area is accepted (inferred_stack_args, _caller_provides), the callee stays refused. A
  callee a path of which reaches an instruction that does not decode stays not known (the code behind
  it is not seen), and so do its callers, now with that reason. Programs of our own
  (tests/test_stack_args.py: a RISC-V function that sets sp from auipc, GCC and clang; a
  variable-length array with six arguments, ARM GCC -Os and -O2, read through r7; a caller that stores
  both with one strd, as GCC 7 at -O2 does; a callee with an undecodable halfword on its path): refused
  before, accepted after through the lift check (the last stays refused), also with Ubuntu 22.04's and
  Debian 10's compilers; stack arguments read swapped are dropped. The 39 FreeRTOS
  builds: 6 of 13,684 functions go from refused to lifting (xPortStartScheduler on the five clang
  RISC-V builds, freertos_vector_table on clang 14), none from lifting to refused, every other
  function's C byte for byte as before; the register-test entries on GCC 10 and clang 14 and 16 stay
  refused (their callee keeps an inline marker that does not decode); gizmo unchanged. Lift checks
  with the system run and the whole image, before -> after: GCC -Os 341 -> 341, clang -O2 300 -> 300,
  RISC-V GCC -Os 272 -> 272, RISC-V clang 14 259 -> 260 (xPortStartScheduler; freertos_vector_table
  lifts now and the check refuses it as code only the core enters), RISC-V clang -Os 257 -> 257
  (xPortStartScheduler lifts and is dropped: its generated cases fault, its callee goes on on a task's
  stack), nothing lost and the whole image agreeing on all five. Left: the callee itself (its stack
  pointer is still not static); a callee whose code does not decode; a tail call through a register to
  such a callee (still refused); the generic stubs (dread.autocase's stack_words) still count 0 stack
  words for such a callee.
- [ ] **The pilot on the draft.** Names for the readable C: the model proposes a map of names and a
  sentence, code applies it (docs/decisions.md). Built 2026-10-21: dread/naming.py (the
  readable_names table, applied by digest, a map kept only when its names are C identifiers nothing
  else uses and the C compiles to the same code), dread pilot --task readable, dread readable
  --score ELF (parameters by position against the debug information's, local names among the
  source's). Measured 2026-10-21 on the GCC -Os build: 30 kernel functions drawn at random (lists,
  queues, tasks, timers, event groups, stream buffers; seed 3), each model on its own copy, every
  map compiling to the same code. Haiku 5.5: all 30 named, 27 of 49 parameters as the source names
  them (55 %), 34 sharing a word (69 %), 20 of 78 local names among the source's; 26,106 tokens in
  and 26,125 out, about $0.02. Sonnet 5.5: all 30, 23 of 48 (48 %), 35 (73 %), 21 of 89; 26,106 in
  and 10,362 out, about $0.16. Haiku is the one to use. The first 30 by name, mostly newlib's (terse
  names: ptr, fp, g), got 12 of 37 exact with Haiku. Most misses were a register holding several
  values one name had to cover: parameters were not split (xQueueGenericSend's r0, the queue and
  later the result, named xResult). They are now (Variables). Memory at fixed addresses is written
  as a name, a macro above the function that is the access itself (#define g_20029aac (*(volatile
  u32 *)0x20029aacu)): by default where it is (io_ a device register of [memory] io, rom_ in a rom
  region, g_ anything else), or the readable_names table's globals, which the pilot asks for first
  (40 a question, each with its uses in up to four functions; a name no function or other global
  has) and shows in the functions' C. Measured again 2026-10-21 on the same 30 functions with
  Haiku 5.5, both changes in: parameters 32 of 46 as the source (70 %, 55 % before), 45 sharing a
  word (98 %, 69 %), 23 of 128 local names among the source's (more variables now, fewer exact);
  the 14 globals they use: 12 at a data symbol's address, 8 named as the source (pxCurrentTCB,
  uxSchedulerSuspended, xNextTaskUnblockTime, uxTopReadyPriority, the timer task's four), 11
  sharing a word; one is a member (xSuspendedTaskList's pxIndex, named xTickCount), the device
  register ICSR got portNVIC_INT_CTRL_REG, FreeRTOS's own name for it. 32 answers, 30,324 tokens
  in and 30,632 out, about $0.02. Collecting the globals' uses runs the stages over every readable
  function (49 s for GCC -Os's 316 on one core; in a pool of workers since 2026-10-22, a large
  private firmware's in a fraction of the time, dread.readable in_pool). The variables' questions went
  one after another, each made in its turn (the stages, and its callers found by a walk over the
  whole image's code, once per function), slow on a large private firmware; since 2026-10-22
  `--task readable` asks them --jobs at a time (default 4, as --task name; name_variables): all of
  them made first, before any thread starts (dread.readable question_trees in the pool, the callers
  in one walk), then a thread a question, each with a provider and a directory for the same-code
  check of its own, the main thread the table's only writer, so the table ends as one question after
  another leaves it (a test plays it with answers out of order). Measured on the GCC -Os build with
  Haiku 5.5, each run on a copy of its own: the variables of the 316 functions took 1,412 s one after
  another with the code before (4.5 s a function), 1,325 s with --jobs 1, 149 s with --jobs 16 (8 s of
  it making the questions; about three answers a second, then one function the tools sent back once
  alone for half a minute). The names as good: parameters 211 of 374 as the source (56 %) before,
  216 of 378 (57 %) with --jobs 1, 215 of 377 (57 %) with --jobs 16; sharing a word 74 %, 75 %, 76 %;
  local names among the source's 115 of 1,064, 106 of 1,075, 121 of 1,071; $0.16-0.17 a run, the
  globals' and structures' questions included. Those went first, one batch after another (176-193 s
  here, more than the variables with --jobs 16), struct_uses making every function's typed C in this
  process (23 s here). Since 2026-10-23 they go --jobs at a time as well (name_shared,
  docs/decisions.md), in two rounds: the globals' batches (the structures of static data and their
  fields among them), then the structures', whose questions show the globals' names. In each round the
  uses come from a pool of worker processes (global_uses, struct_uses: only their lines come back),
  every batch's question is made, and the batches are asked by threads, a provider each while it asks;
  the main thread checks and writes the answers in the order of the batches, an answer that ends early
  waiting for those before it, so a name two batches propose goes to the earlier one and the table
  ends as one batch after another leaves it (a test plays answers that end out of order and fails
  when they are settled as they end). After a failed or declined batch nothing more is asked, as
  before; what was asked by then is kept. Measured 2026-10-23 on a copy of the gate's GCC -Os build,
  its readable_types plan made again with dread readable --check (the same 316 readable functions and
  52 structures, now also 15 structures of static data: 289 globals, structures and fields to ask, in
  8 batches; 51 structures in 7), Haiku 5.5, each run on a copy of its own:
      code                   shared names  globals (uses + asking)  structures (uses + asking)  variables  whole run
      main's, --jobs 16      260 s         137 s (9 + 128)          124 s (21 + 103)            103 s      364 s
      this, --jobs 16         53 s          31 s (9 + 21)            23 s (4 + 19)               94 s      148 s
      this, --jobs 1         259 s         146 s (9 + 136)          114 s (4 + 110)           1,303 s    1,563 s
  (361-372 answers, $0.18-0.19 a run; the uses with 8 workers, DREAD_JOBS=8; the --jobs 1 run had the
  full suite beside it for 11 of its 26 minutes.) Asked without a model, the uses are byte for byte
  main's. The names as good (dread readable --score; main's code, --jobs 16, --jobs 1): parameters
  199 of 374 as the source (53 %), 206 of 376 (55 %), 207 of 373 (55 %), sharing a word 73 %, 76 %,
  75 %; local names among the source's 109 of 1,123, 108 of 1,101, 105 of 1,104; structures against a
  parameter's source type 7 of 15, 6 of 15, 7 of 16 as the source, fields 54 of 83, 50 of 79, 58 of 84;
  globals named 250, 241, 285, as the source 4, 19, 22, sharing a word 128, 144, 152. The globals'
  differences are which batch came back empty: in main's run and in the --jobs 16 run one of the eight
  globals' answers named (almost) nothing, Haiku taking the system prompt's example names (g_20029aac,
  io_40004004, g_20000030) for the ones asked ("None of the four names you asked for appear in the
  listing" and an empty block; or a first block with the example names and a corrected second one,
  which the pilot does not read); in main's run that was the batch of the kernel's globals
  (xYieldPending, pxCurrentTimerList: names the model knows from FreeRTOS), in the --jobs 16 run one of
  the demo's; the --jobs 1 run had none. Done the same day: GLOBALS_SYSTEM and
  STRUCTS_SYSTEM show the answer's form with placeholders ("<a name shown>"); three runs each with
  --jobs 16: with the example names 242, 310 and 316 globals named, 3 of 27 batches answering the
  examples (4, 4 and 0 names: "None of the four names in the template appears in your listing"), with
  placeholders 313, 323 and 315, none.
  Found on the way: readable.transform kept its results by the context's id, and a context made where
  one had died got the dead one's results, made with its tables (38 of 50 new contexts in a row on
  gizmo; struct_uses's context of its own could get another's lines); now kept per context, weakly.
  Builds from readable C in one process lost the reuse that luck gave them (three builds of the GCC -Os
  build in a row with main's code: 55, 55 and 7 s): a build made every readable function's C in one
  process (48 s of the 55). Since 2026-10-23 (docs/decisions.md) a build makes them all at once in a
  pool of worker processes, the longest lifted C first, and keeps them in the process by what they are
  made from: the context's tables, the stages, each function's lifted C (dread.readable readable_texts;
  dread.build lift_sources lifts every function first and hands the chooser's prepare their lifted C).
  The gate's first pass makes them so, its rounds and its whole image take them again (they took them
  from the context's memo before too: the GCC -Os gate made its 316 once, 48.7 s of its 71.6 s; now
  7.0 s, the rounds nothing), and so does the next build with the same tables (dread pilot builds
  again and again); a process with another thread running makes them in one process (in_pool).
  Where the 48 s went (cProfile of one GCC -Os build): the clean-up 97 %, its conditions pass 57 % of
  it all (every candidate made of copies 20 %, the expressions' sizes walked again and again 9 %, the
  loads compared for every candidate 8.5 %, the test values 8.5 %, z3's proofs 2.5 %), fold 8 %, values
  7.5 % (the data-flow graphs of all the passes 15 %); parsing 2 %; the structure, the tables and the
  frames 1.3 %. Measured with 8 workers (DREAD_JOBS=8), main's code against this, each on a fresh copy
  of the gate's builds (seconds; the gate's rounds in parentheses):
      build (readable functions)       dread recon    two builds in one process    dread readable --check
      FreeRTOS GCC -Os (316)           57.4 -> 14.5   58.7 + 57.3 -> 14.1 + 7.2     74.7 -> 29.7 (1)
      FreeRTOS clang -O2 (275)         64.1 -> 20.0   63.0 + 61.7 -> 19.6 + 8.6     82.5 -> 38.6 (1)
      FreeRTOS RISC-V GCC -Os (251)    24.6 ->  5.6   23.7 + 24.0 ->  5.2 + 2.0     36.3 -> 17.1 (1)
      FreeRTOS GCC -O0 (353)           83.8 -> 22.9   81.5 + 81.4 -> 21.9 + 11.7   121.4 -> 59.5 (2)
      gizmo a32, m3, m4, rv32 (31-44)  1.5-2.7 -> 1.4-2.6  1.4-2.6 + 1.3-2.4 -> 1.3-2.6 + 0.1-0.3  3.1-5.5 -> 2.9-5.5
  (gizmo's builds stay below POOL_MIN: made in the process as before, the second build takes them.)
  The generated files and the images byte for byte main's on all eight builds, the gate the same
  lists and readable_types tables. Left: lifting is the largest part a build does in one process now
  (5 of 14 s on GCC -Os, every function one after another), a candidate for the same pool (the
  workers would send the Lifted results back; the lift check builds through it too); the gate's
  analysis trees run the clean-up on every function a second time (9.5 of its 28 s on GCC -Os).
  The same day the clean-up itself got cheaper, with the same output (dread.tidy): the conditions
  pass tries candidates that share the condition's subexpressions, their sizes counted, only those
  smaller than the condition (shared_candidates; made_candidate makes the one taken of copies, as
  before), makes its test values for the first candidate that reads the condition's loads (the
  values pass too), and keeps the answers for the tidy run: every round of the passes asks every
  condition again, and two thirds of the searches on the GCC -Os build were of a condition the run
  had searched before. Every function's readable C in one process (all that lift; CPU seconds):
  GCC -Os 77.3 -> 44.4, clang -O2 108.8 -> 58.8, RISC-V GCC -Os 44.9 -> 31.8, GCC -O0 127.0 -> 58.1,
  gizmo's four 2.1-3.8 -> 1.3-2.2, byte for byte the same on all eight builds. With the pool, 8
  workers (seconds, the pool alone -> with this):
      build (readable functions)       dread recon     first build in one process   dread readable --check
      FreeRTOS GCC -Os (316)           16.2 -> 11.5    14.8 -> 11.2                  29.5 -> 25.1
      FreeRTOS clang -O2 (275)         22.2 -> 14.1    20.5 -> 14.1                  41.8 -> 28.0
      FreeRTOS RISC-V GCC -Os (251)     6.7 ->  5.3     6.2 ->  5.0                  19.7 -> 17.6
      FreeRTOS GCC -O0 (353)           26.5 -> 18.9    25.7 -> 18.5                  69.7 -> 52.3
      gizmo a32, m3, m4, rv32 (31-44)  1.4-2.7 -> 1.2-2.0   1.3-2.7 -> 1.1-1.9       2.9-5.7 -> 2.4-4.1
  (The second build in one process takes the readable C as it was, the same time either way; the
  machine was busier than for the table above, the pool's own figures up to 10 s higher.) The
  generated files and the images of all eight builds byte for byte the same again, the gate the same
  lists and readable_types tables.
  Next: types: the
  structures behind pointers derived by code (accesses at constant offsets through the same
  pointer, unified across functions), with volatile in the pointer's declaration so that a field
  reads p->f, their names and fields' names proposed the same way.
- [x] **Names that survive changes of the C** (asked for 2026-10-23: on a private firmware the day's
  six readable-C changes had made every variable name wait for a new proposal). Done 2026-10-23
  (dread.naming identities, carry_map, carry, carry_table; dread readable --carry and the end of
  --check; dread pilot --task readable; docs/decisions.md, docs/formats.md): each entry keeps the
  identity of every variable of its C (a parameter by its position; a local by its register or slot
  and the instructions that write it), and when the digest differs each name goes to the variable of
  the new C with the same identity (a local: the same base, more than half the definitions shared,
  each the other's only best match), checked again (identifiers, the same code) and kept as an entry
  for the new C marked carried; a name without its variable is dropped; an entry from before waits
  by its digest. The pilot asks a carried function again only where variables are left without a
  name, showing the carried names. Measured on FreeRTOS GCC -Os, every readable function named with
  the code from before the six changes (315 functions, 1,788 names, $0.18), then main's C: by digest
  204 of 315 entries applied (813 names); carried, 312 (1,627 names): 814 names of 108 functions
  came over in 10 s without a model (177 parameters, 637 locals, every local with identical
  definitions, 111 on a variable of another name now), 161 dropped (154 with their variables, values
  no longer kept; 7 whose variable kept half its definitions or fewer); none judged wrong against
  the debug information (389 judged right, 425 unknown), and as close to the source's names as a
  fresh run (parameters 57 % as the source against 47 %, 80 % and 75 % with a word of it; the runs'
  own spread about five points). The pilot afterwards asked 18 functions for $0.01 (a fresh run: 315
  for $0.17); the 15 asked again kept all 158 carried names. Left: the structures' names (the
  structs part) follow anchors whose variables are renumbered too; a frame slot whose prefix changes
  loses its names; a partial match (rare: 4 of about 1,250 locals from the oldest C tried) is a
  measure, not a proof.
- [ ] **Function names.** `dread pilot --task name` (2026-10-22, docs/decisions.md): a question
  shows a function's readable C (dread.readable view: the tables applied, each constant followed by
  what it points to: text, a function's entry, or a table's first words, such as a localized
  message's text pointers; a variadic function's body behind its naked entry) or its listing where it
  does not lift; a function is asked once its callees are answered (call_components: the call graph's
  strongly connected components; rounds with a barrier each slowed to a fifth once they got small),
  --jobs at a time, each question showing the names proposed so far for its callees and callers.
  `--refine` asks again callers first, showing a function its callers' names and its first one.
  names.tsv keeps every proposal with the form its question showed; a run started again goes on where
  the last one stopped. `--score TRUTH` compares the proposals with true names (an ELF's function
  symbols, or the functions table of a copy that has them): exact, the same words, shared words, the
  same prefix; `--judge PROVIDER` adds a model's verdict per pair (same, close, different). The
  providers ask again after a rate limit or an overload (HTTP 429, 529, 5xx).
  Measured 2026-10-22 with Haiku 5.5, Sonnet 5.5 judging. FreeRTOS GCC -Os (mps2), every function
  renamed FUN_address (366): first pass exact 6, the same words 40 (11 %), a word shared 230 (63 %),
  half the words 81 (22 %), mean overlap 0.26; judged the same 87 (24 %), close 144 (39 %), different
  135 (37 %); $0.12, 3 minutes. With --refine: the same words 45, a word shared 245 (67 %), half the
  words 102 (28 %), overlap 0.31; judged the same 113 (31 %), close 142 (39 %), different 111 (30 %);
  $0.12. The judge's own noise: the first pass judged again gave 95, 155, 116, the same verdict for
  331 of 366 (90 %): totals move by up to 5 points. The model knows FreeRTOS, and callers' names help
  it recognise the code. A stripped private firmware (DREAD's real-life test, its true names those that
  the people who reconstruct it gave; its figures stay in its own notes): judged the same far less
  often than FreeRTOS; what a constant points to helped most where texts sit behind tables of
  pointers; --refine made the names more consistent, not more right, and fewer of them built of an
  address. A driver whose first leaf was misread carried the misreading to most of its functions on
  both passes, where another run had named it right: a proposal shown to the callers spreads its
  errors too, and runs differ by subsystem.
  What misses: a project's conventions (its subsystem prefixes, its RTOS's service names), drivers
  whose device registers have no names, leaves that touch only unnamed globals.
  Globals, measured 2026-10-22 on the same stripped GCC -Os build with Haiku 5.5, each score judged
  twice by Sonnet 5.5, against a control run of the same code without globals (the run above ran on
  older code). `dread pilot --task globals` is the readable task's globals step alone, for every
  function's readable C (362 of 366 have one, unchecked; their C uses 312 globals); after a naming
  run its questions show the functions by their proposals. Named first, from uses shown under FUN_
  names, the globals are guesses: 272 named ($0.01), none of the 231 at a data symbol's address as
  the source, 35 of the 251 at or inside one sharing a word with it; demo task handles became
  g_uart_handle, g_adc_handle and g_i2c_handle, an event group g_spi_dev, the UART's control
  register io_dma_enable, and the functions' names echoed them: with newlib's errno named
  g_irq_pending_count, _read_r, _write_r, _lseek_r and _close_r went from syscall_errno_wrapper and
  the like (judged close) to irq_pending_reset_report and the like (different), prvSyncTask from
  sync_barrier_wait to spi_handshake_task. The core's registers came out right (io_systick_csr,
  io_nvic_iser, io_scb_icsr), but the functions using them were named as well without (the model
  reads the addresses). First pass: the same words 41 against the control's 52 (the earlier run
  40), overlap 0.26 against 0.28 (0.26); judged the same 93/94, close 128/127, different 145/145
  (40 %) against 93/96, 143/150, 130/120 (33-36 %). After --refine: the same words 46 against 57,
  overlap 0.29 against 0.31; judged the same 108/110, close 121/115, different 137/141 (37-39 %)
  against 102/107, 166/151, 98/108 (27-30 %). The loss sits in the 212 functions whose C shows a
  named global (different 44-46 % against 29-31 %, overlap 0.22 against 0.26; paired with the
  control's names, 24 judged better and 51-56 worse); the other 154 did as the control's. Named
  between the passes instead (the control's first pass copied; --task globals, then --refine), from
  uses shown under the proposals, the globals are better: 292 named, 92 of the 271 at or inside a
  data symbol sharing a word with it (34 % against 14 %). The refine then: the same words 55 against
  57, a word shared 252 against 246, half the words 104 against 99, overlap 0.32 against 0.31;
  judged the same 121/109 against 102/107, different 104/118 against 98/108, totals within the
  judge's noise; paired, 282 of 366 proposals as the control's refine, of the 84 others 21-25 judged
  better and 9-10 worse (17-20 against 6 where the C shows a named global). A small gain at best:
  the globals take their names from the first pass's reading of their functions, and the refine
  confirms that reading (a function first named watchdog_poll, its globals named watchdog_*, kept;
  the source's vPeriodicEventGroupsProcessing). The judge's noise, measured: one file judged twice
  changed 25-29 of 366 verdicts (7-8 %); a pair judged in other company (other pairs in its
  question) changed 39-41 of 282 (14 %). Spent: Haiku 1,846 answers $0.62, Sonnet 80 $0.40. Not a
  default of --task name (docs/decisions.md): first they hurt, between the passes they cost $0.01
  and gain little. Not measured: a partly named image, whose named functions would tell the
  globals' questions more.
  Runs of the cases (2026-10-22, `--behaviour`, dread.behaviour; docs/decisions.md): a question can
  also show what the function did in up to three runs of its cases on the original image: the
  arguments (a text one points to where the run reads it as text), the result, the functions it calls
  itself with their arguments, texts and results, its own device accesses and writes, named as the
  readable C names memory (*(r0 + 36) behind an input's pointer), the bytes it reads from an input's
  buffer, how it ended and its loop's rounds; patterns that repeat are folded ((read io_40011004 = 1;
  io_40011000 = x) (3 times), x = "hi!"). The cases are the lift check's cached ones whatever their key
  (inputs only, run anew), the kept and the written ones; a function without any gets a search of 5 s,
  kept in BUILD/behaviour. The runs shown differ: most coverage first, then new coverage or another
  ending, other callees, other device registers or another kind of result; 1,500 characters at most
  (mean 820). FreeRTOS GCC -Os made nameless: 358 of 366 functions with runs, 50-60 s on 8 workers
  from the lift check's cases, 134 s from nothing. Measured with Haiku naming and Sonnet judging each
  pass twice, as the mean verdict rank per function (same 2, close 1, different 0): two controls 0.908
  and 0.891 (judged the same 25-27 %), three passes with the runs 0.970, 1.031, 0.981 (28-32 %, the
  same words 51-60 against 36-38): +0.098 +- 0.028, all of it in the 132 functions whose runs showed an
  assertion's source path (+0.276 +- 0.056; the others -0.002 +- 0.028). The notes cut a text after
  48 characters, so a path ended in ".../FreeRTOS/S..." before its file name; the runs showed its
  end. Notes that show a long text's first third and its end (dread.constants string_at, now the
  default: "...FreeRTOS/Source/tasks.c") gave more without running anything: 1.071 and 1.063 (the same
  34-36 %, half the words or more 32-33 % against 20-25 %), +0.165 +- 0.028. The runs on top of them:
  1.045 and 1.038, -0.009 +- 0.020, for 37 % more input tokens (522,000 against 383,000 a pass, $0.14
  against $0.12); newlib's functions and the demo's application lost a little, device registers and
  read bytes gained a little, both within the noise. Two passes of one setup differ by up to 0.05
  (-0.021, +0.004, -0.051): one pass against another cannot show less than that. A pairwise judge of
  that day, `--versus` (for each function two passes named otherwise, which name comes closer, each
  pair shown once, in an order the address decides; replaced since by `--against`, which asks both
  orders: below), preferred the second place (about 55 to 45), and two identical controls came out
  151 to 117 with it (the notes with text ends against the old control 171 to 99; the runs on top
  against the notes alone 118 to 130). So the runs stay an option.
  Votes, a confidence gate, passes and ablation (2026-10-22, docs/decisions.md; measured on the code
  before the notes showed a text's end, every variant alike): `--samples K` asks each function K
  times (Haiku 5.5 and Sonnet 5.5 refuse any temperature but their default 1.0, which samples; the
  later questions list the callers in an order of their own, the runs of `--behaviour` as they are)
  and keeps the name most answers agree with (dread.naming.vote: words paired, init with initialise),
  the agreement its confidence; `--gate C` shows later questions only names of confidence C or more;
  `--passes N` asks again, callers first and callees first in turn, what its question changed for,
  until no shown name changes; `--ablate` asks each function also with no other name shown and flags
  a name that differs then; names.tsv got columns for all of it (docs/formats.md). `--score` with
  `--judge` gives the verdicts per confidence and per flag; `--against OTHER` has the judge say
  instead, where two runs' names differ, which is closer to the true name, asked in both orders, with
  a sign test (in place of `--versus`, above). Measured on the FreeRTOS build, each variant on a copy
  of its own, pairwise against one question in one pass (wins, losses, ties): a second such run 120,
  95, 72 (p 0.10: the measure's own spread; the two runs' names alike for 55 % of the functions, the
  same string for 22 %); votes of five 130, 60, 84 (p 4e-7; against the second run 109, 64, 94);
  votes of five with gate 0.8 and four passes 158, 53, 82 (p 3e-13); votes of five and one more pass
  callers first, ungated, 163, 47, 78 (p 3e-16). The gate gains nothing: its first pass against
  ungated votes 80, 81, 79; ungated against gated after two passes 95, 69, 80; the gain is the second
  pass (66, 14, 23 gated; 81, 26, 23 ungated), passes 3 and 4 none to speak of (25, 25, 24; 22, 12,
  9), and four gated passes tie with two ungated (84, 85, 73), without a fixed point (pass 4 still
  changed 46 shown names). The confidence is calibrated: of votes of five, judged different 88 % at
  0.2 (8 functions), 58 % at 0.4 (43), 42 % at 0.6 (67), 26 % at 0.8 (80), 15 % at 1.0 (168). Votes
  are steadier: where no name is shown, two answers agree for 47 %, two votes for 68 % (50 of 74).
  Ablation: hiding the names shown changed the vote for 186 of 292 functions (64 %), the same
  question asked twice for 24 of 74 (32 %); but the flagged names are not the wrong ones (different
  27 % against 19 %, and within a confidence no worse: at 1.0 10 % against 16 %): the names shown
  make them better, the vote with them beating the vote without 164, 26, 55. The judge's absolute
  verdicts move with what else is in a batch: 13 of 126 functions that two runs named alike got other
  verdicts in the two runs' judgings, 11 of them harsher in one, so absolute totals of two runs
  differ by several points for nothing (gated pass 1 against the votes: different 38 % against 29 %,
  pairwise even); runs are compared pairwise. Defaults now `--samples 5 --passes 2`: 3,195 answers
  against 366 on this build ($1.05 against $0.12); measured on one build the model knows, not
  confirmed on firmware it does not (the private real-life test). Spent on the measurement: about
  $3.50 of Haiku, $1.30 of Sonnet.
  On the private firmware (2026-10-22, every function nameless, its people's names as the truth):
  the defaults were clearly closer to the true names than the earlier run with --refine in the
  pairwise judging (both orders, a sign test far below 0.001), the absolute verdicts a little better,
  and the driver the single-question runs had misread was not misread again: most of its functions
  got names of the driver, none the misreading's: the votes fixed it instead of flagging it. The
  confidence was calibrated there too, less steeply than on FreeRTOS.
  Subsystems (2026-10-22, dread/groups.py, docs/decisions.md): `dread groups` groups the functions by
  their code alone, no name used: Louvain's modularity (resolution 2) on a graph of what two functions
  share (a global at one address, a device's 4 KiB block, a text, a table: each pair of the k that
  share one 1/(k-1)), a function's address the other makes (1) and neighbours in memory (0.5); calls
  are left out (7-17 % of them stay inside a source file: they join layers, not files). Scored against
  the source file of each function (DWARF) on four FreeRTOS builds (GCC -Os made nameless, clang -O2,
  GCC -O0, RISC-V GCC -Os; a bench step now, `groups`): adjusted Rand index 0.805, 0.785, 0.844,
  0.778, purity 0.755, 0.735, 0.798, 0.845, completeness 0.969, 0.973, 0.977, 0.894, 27-29 groups for
  41-53 files; what mixes files is small neighbouring files of the demo and two files whose functions
  of the same names the linker put side by side. What a function touches comes from its machine code
  since later that day (dread.relations flow_references: the constants its registers hold followed
  over its control flow, the arch layer's value_step for ARM and RISC-V; `--evidence readable` for the
  readable C's facts): `dread groups` took 0.3-0.5 s for a FreeRTOS build instead of 7-23 s (cold) or
  1-5 s (facts kept); on 38 FreeRTOS builds the groups were as good on ARM (index +0.004, 20 builds
  higher, 5 lower, the two groupings 0.98 alike) and on RISC-V with clang, 0.06-0.11 lower on RISC-V
  with GCC -Os (one array field the readable C hides: The groups' sensitivity, below;
  docs/decisions.md).
  `dread pilot --task subsystems` asks for each group's
  name, prefix and sentence from all of its evidence at once (its functions' code cut to 24,000
  characters, the texts they point to, the devices they use, its callers and callees outside it; votes
  of five over the prefix), kept in subsystems.tsv beside names.tsv (docs/formats.md); `--task name
  --subsystems` does that first and shows each function's question its group's line. On the GCC -Os
  build Sonnet judged all 27 groups' names to name the file most of their functions come from (24 the
  same, 3 close: the port with the lists, the queue with heap_4, newlib's system calls): task_ for
  tasks.c, queue_ for queue.c, timer_ for timers.c, abort_delay_ for AbortDelay.c and so on. Measured
  pairwise against the defaults until then (votes of five, a second pass), each on a copy of its own,
  Haiku naming, Sonnet judging in both orders: 153, 51, 81 (p 5e-13), after the first pass 171, 46, 74
  (p 4e-18). The names follow the prefix: 97 % begin with their group's (without the groups 37 %),
  98 % share their source file's most common first word (68 %); judged the same 52 % against 38 %,
  different 10 % against 20 %. Where a group mixes files, its prefix goes to the few that do not belong
  (demo_strcmp, demo_put_char for the demo's printf beside main.c), and three exact names of the run
  without groups (vTaskSwitchContext) lost to the prefix's snake_case, a fault of the judge (without
  those 150, 54). The groups named from the first pass's names instead (shown in the second pass
  only) gained less: against the defaults until then 102, 44, 32 (p 2e-6), against the groups named
  from code 57, 127, 75; a first pass's misreading went into a group's name (newlib's stdio became a
  stream layer over UART ports, fs_). Costs: without the groups $0.95 (3,195 answers), with them $1.14
  (3,331: 136 answers for the 27 groups, about $0.10, the rest the longer questions); judging $0.41 of
  Sonnet. The default since
  2026-10-22 (NAME_SUBSYSTEMS; `--no-subsystems`); measured on one build the model knows, whose
  assertion texts name its files, not confirmed on firmware it does not know.
  Scored against the source (2026-10-22, dread/namejudge.py, docs/decisions.md; the user: "Is it good
  to judge the names by equality? There are probably many ways to call the same things with different
  styles of naming that are all valid in their own way."): `--score TRUTH --grounded PROVIDER` has a
  judge read each function's source and say of each proposal right, partly or wrong, with a reason,
  whatever its style; the sentence gets a verdict of its own (`--names-only`: the names alone). The
  source comes from the truth ELF's debug information (the definition's file and line, cut to its
  closing brace; `--source DIR` where the files moved), the names of the functions a question shows
  masked as FUNCTION_k wherever they stand (traceENTER_vTaskDelay too), a source file's functions to a
  question; `--against OTHER` with it judges the proposals of two runs that differ, the better verdict
  closer, both runs' questions of a file reading its sources from the provider's prompt cache
  (dread.llm's cached parts; Anthropic's: read for a tenth, written for a quarter more). Every score
  also gives words whatever the naming style (dread.naming.style_words: Hungarian heads, runs of
  letters cut into known words, abbreviations, synonyms, word forms). Measured on the names stored from
  eight FreeRTOS runs of that day (332 of 366 functions with a source; newlib's files are not on this
  machine): the judge reading the source ranked the runs as the pairwise judge had (Kendall's tau 1.0;
  word scores 0.86, a forced choice among five functions of the file 0.64-0.78), its own noise a
  hundredth on a run's mean (the same verdict for 88-92 % of the functions judged twice), the groups
  from code against the defaults before them +0.047 +- 0.019 a function (p 0.01) where the judges
  against the true name saw +0.122 (p 2e-11): mostly the names taking the files' words as prefixes.
  Right though sharing no word with the true name: 187 of 1,371 (xPortPendSVHandler as
  sched_context_switch, pvPortMalloc as heap_alloc). Judged with its sentence as one, a wrong detail of
  the sentence made right names wrong; apart, the names' means rose 0.06-0.07. Style words tell right
  from wrong better than the overlap (area under the ROC curve 0.88-0.90 against 0.84-0.86; the judge
  against the true name 0.93). The forced choice was dropped: two draws of one run 0.055 apart, the
  identical controls 0.057 apart with sentences (p 0.006), names judged wrong picking their function
  half of the time. Costs: about $0.80 a run alone with Sonnet, $0.35 for each run judged with it;
  Haiku as the judge ranks alike for about $0.05 (not the default: it judged its own family's names).
  Through the tool: the groups from code right 65 %, wrong 8 % ($0.88); against the defaults before
  them 68, 34, 149 (p 0.001; the pairwise judge against the true names had 153, 51, 81), $1.07 for
  both. Spent on the measurement: about $13 of Sonnet, $0.40 of Haiku.
  Next: `--subsystems` on the private real-life test (its misread driver, groups whose texts say
  less); a group's name asked again after the first pass with its functions' names (asked once from
  code now: subsystems.tsv keeps it), not tried; the runs of the cases (`--behaviour`) where the code
  says less (its drivers, and leaves that touch only unnamed globals: its misses) against its notes
  with text ends, and the globals between the passes. For the scores: a person's marks on a sample of
  50 functions (made outside the repository: it holds FreeRTOS's code) against the judge's verdicts;
  the judge on the bench's other builds and on a firmware it knows less; style words in the votes
  (dread.naming.vote pairs similarity()'s words), not tried.
  What the image is, in front of every question (2026-10-22, docs/decisions.md): the questions said
  only "a binary image". `--subsystems` on the private real-life test named groups after a kind of device
  the image is not for, with high confidence, and asked again without anything else
  changed it guessed another one; the texts its code points to say what it is at once (every answer
  agreed). `dread pilot --task device` reads it from them (device_texts: the texts of the functions an
  LLM may be shown, from the listing alone, the most used first; five answers, the device most agree
  with, in device.tsv) and every naming question then begins with it; a target's `[llm] device`
  states it instead; `--no-device` leaves it out. On the private firmware the groups in question were
  then named after parts of the right kind of device and the others stayed as they were; a sentence
  on what the program does as well made the model name groups after the parts that sentence mentioned,
  so the line says the device alone: the product or board, not what the program does or its operating
  system (asked more openly, FreeRTOS GCC -Os read as "a Cortex-M3 board running a FreeRTOS demo/test
  application" and "demo" went into kernel functions' names). FreeRTOS now reads as unknown (its texts
  name no product or board) and its questions stay as they were. On the private firmware (2026-10-23)
  the run with the line named every misread group for the right kind of device, and its function names
  were judged closer to the true ones than the run's before it, beyond chance by groups.
  Measuring between runs (2026-10-22): two FreeRTOS runs with the same settings differed by 24:40 and
  37:21 in the grounded pairwise judge (sign test over functions p 0.06 and 0.05), as much as the open
  reading's run against one without it (25:45, p 0.02; and 45:32 against another). A run's names hang
  together (a group's prefix and reading go into all of its functions), so the sign test over
  functions is too sure of itself: counted by groups, none of the four comparisons went beyond chance.
  Done the same day: `--against` also counts by groups (the run's groups.tsv, else the truth's source
  files: pilot.pair_groups, group_sign), the test to trust between two runs. The stored comparisons
  recounted that way: the votes of five against one question 35:7 by source files (p 2e-5; 180:33 by
  functions), the gate's second pass 27:8 (p 0.002), the groups named first 22:3 and 20:3 by groups
  (p 2e-4, 5e-4) and its variant 19:4 (p 0.003) all hold; two runs with the same settings 25:13 (p
  0.07); the grounded judge's re-measure of the groups 15:6 by groups (p 0.08; 68:34 by functions, p
  0.001), so their gain in what the names say was not established then.
  Replicate runs (2026-10-23, docs/decisions.md): `--against RUN... --vs RUN...` with `--grounded`
  (pilot.compare_runs) compares two settings over runs made anew: each run judged once against the
  source (the same batches for every run; the judge's answers kept by question in
  grounded-answers.jsonl, so that a run judged before costs nothing again: the comparison asked a second
  time asked nothing and wrote the same bytes), every pairing counted from those verdicts. The runs'
  means with a permutation test over runs decide (the runs are the independent draws; its least p is
  2/C(2R, R), 0.10 for three runs a side, 0.008 for five); every run of one setting against every run of
  the other by groups over all the pairings, and the pairs within a setting (the noise floor), are
  printed beside them. The groups named first, five runs a side on FreeRTOS GCC -Os (Haiku 5.5 naming,
  `--no-device`, two runs of each setting at a time; Sonnet 5.5 judging, sentences apart): means 0.795
  +- 0.012 against 0.735 +- 0.022, +0.060, p 0.008 (the least possible: no other split of the ten runs
  but its mirror comes as far apart, the next +0.053); right 66 % against 58 %, wrong 7 % against 11 %.
  By groups over the 25 pairings 22:6 (sign test p 0.004), by source files 28:9 (p 0.003), by functions
  1,729:820 (ties 3,590). Every one of the 25 pairings across favoured the groups by groups (+4 to +20),
  14 of them beyond chance; of the 20 within a setting (-9 to +9) one: a single run against one shows
  this gain about half of the time, which is what the re-measure of 2026-10-22 was. The group count
  saturates as a statistic over runs (splits of four runs of one setting and one of the other reach as
  many groups: permutation p 0.095), so the means decide and the groups say where: 22 of 28 groups gain,
  most in the demo's test code (its main group 0.713 against 0.537, the queue-set test 0.825 against
  0.583), the kernel's own files little or a little less (tasks.c 0.833 against 0.823, queue.c 0.778
  against 0.837, stream_buffer.c 0.804 against 0.854; not traced). Two runs of one setting name 43 % of
  the functions alike, with the groups as without. The default stays. Spent $14.80: naming $10.34 (ten
  runs, four at a time, 26 minutes a round), judging $4.46 (541 answers, 6 minutes; the 45 pairings judged
  one by one would have cost about $40). Next: the other open settings of this item over replicates (the
  runs of the cases where the code says less, the globals between the passes), and on the private
  real-life test; the first pass judged alone, not done.
- [ ] **The groups' sensitivity to one dense thing** (found 2026-10-22 when the evidence moved to the
  listing, docs/decisions.md): on FreeRTOS RISC-V GCC -Os the listing sees one array field (the ready
  lists' pxIndex, pxReadyTasksLists[i] + 4) in 11-13 of tasks.c's functions, which the readable C keeps
  behind a variable it reuses; with it, resolution 2 splits tasks.c differently and the index falls by
  0.06-0.11 (given to the readable C's evidence alone it falls as much). The evidence is right; the
  grouping is what reacts. Counting things with the same users once was worse for both sources (the
  mean index on ARM 0.797 and 0.796 against 0.801 and 0.804, on RISC-V 0.807 and 0.772 against 0.829
  and 0.793); the index also moves by 0.02-0.05 (its standard deviation) when 2 % of the evidence is
  dropped at random on these builds, by 0.003-0.012 on ARM. Next: a grouping that holds a large file
  together (resolution per size, a refinement pass that merges a split file's parts by their
  neighbours), measured on both sources and all 38 builds; on the private firmware, the groups from
  the listing against those from the readable C, and the time.
- [x] **A no-confidence vote on existing names** (the user's idea, 2026-10-22: reverse engineering as
  a puzzle, where a sub-picture can look right in one context and wrong in another). Not a periodic
  re-vote by the same model: consistency is not correctness (a misread driver was named after the
  misreading almost throughout, and a vote by fit with the neighbours would have voted out its few
  right names), the judge flips 7-14 % of its own verdicts, and names that depend on each other can
  oscillate. Instead: (1) triggers computed by code: the function's own evidence (texts, constants,
  device registers, behaviour) against its name, low agreement of the votes (calibrated on FreeRTOS: a
  name one vote of five gave judged different 88 %, a unanimous one 15 %; the ablation flag, the name
  changing with the neighbours hidden, was measured and is no error signal), callers' summaries that
  describe its role differently; (2) sub-pictures tested at their edges: a group of functions (calls,
  addresses, one prefix; `dread groups` and the names in subsystems.tsv are such groups since
  2026-10-22) against what lies outside it (callers outside the group, the texts and registers it
  uses), since a wrong sub-picture is consistent inside; (3) re-decided with hysteresis:
  a challenged name changes only when a new one wins a pairwise comparison by a margin, "more general"
  one of the outcomes (a helper two subsystems share), every change logged with its reason, rounds
  capped; (4) names people gave are flagged for review with the reasons, never replaced. Measured on
  the stripped FreeRTOS build and on a run where a whole driver was misread: does it find that group
  by itself, and fix it without breaking right names? Builds on the votes' confidence (Function names,
  2026-10-22). The misread driver that motivated it was not misread by the votes on the private
  firmware (Function names, 2026-10-22); what is left for the vote: names people gave, and proposals
  of low agreement.
  Done 2026-10-23: `dread pilot --task review` (dread/namereview.py, docs/decisions.md, review.tsv in
  docs/formats.md). (1) Triggers by code, each a reason with a weight: for a function the vote's
  agreement and a split of its answers, another function of the same name, a vague name, a name
  without its group's prefix, a shared helper named after one of the parts that call it, a source
  file's path among its texts that neither its name nor its group's matches; for a group, at its edges:
  its vote, the files its texts name, the described devices ([models]) it holds without naming them or
  names without touching them, its functions not taking its prefix. Measured on three runs judged
  against the source (996 names; wrong 9 %, wrong or partly 33 %): marked names wrong 17-33 %, wrong
  or partly 44-65 %; the score's area under the ROC curve 0.76; challenged from 1.0, 20 % of the
  names, 48 % of the wrong ones, 12 % of the right. No direct caller, a small function, a name changed
  between the passes and neighbours renamed after it (the puzzle's pieces around it moved) were
  measured and left out. (2) A challenge asks the naming question again with the name, its sentence
  and the reasons (five questions, voted); a candidate with the old name's words confirms it, else a
  pairwise question decides, both orders, neither name marked as the old one, "same" and "general" (a
  more general name) among the answers, the candidate needing a margin of 2 (both orders); groups
  first, a renamed group's functions after it in the same round. (3) Every challenge a line of
  review.tsv, a change a row of names.tsv (marked `, review`) or subsystems.tsv; at most two rounds; a
  name changes once a review and is challenged again only for a new reason, also across reviews;
  people's names flagged, never changed. Planted errors in a copy of one run (three groups misread and
  their functions named again by the namer under the wrong reading, three swapped pairs, five names
  made vague, five shared helpers named after one caller's part; 25 of them judged worse than the
  original names): 21 challenged, 20 changed, 16 judged better, none worse, 15 right afterwards; the
  two misread groups whose texts name their files renamed back, the third (no text, no device) not
  found; the swaps found only where a vote or a twin gave a reason (2 of 6); 19 names not planted
  changed, 6 better, 1 worse; 24:1 by functions, 11:0 by groups; $0.36 of Haiku, 6.5 minutes. Without
  planting (the three runs as stored): 58 names changed with a source, 23 better, 6 worse; by groups
  3:1, 7:1, 5:3, pooled 15:5 (p 0.04; two runs of the same settings differ by 8:12 and 14:5); $0.21-
  0.27 a review. Sonnet deciding: 22 better, 2 worse on the three runs (pooled by groups 16:2), no
  better on the planted copy, two to three times the cost, and the judge is Sonnet too: an option
  (--decider), not the default. Left: what the tables do not show stays unchallenged (half of the
  wrong names had no reason against them: a misreading of the code, swapped siblings, a misread group
  without texts or described devices); a trigger from the code itself that tells siblings apart (a
  random audit of names without a reason would at least count the misses) not tried; the review's
  spread between two runs of itself not measured (one decision went both ways in two trials).
  On the private real-life test (2026-10-23, after a naming run with the device line): it challenged
  more of the functions than on FreeRTOS and changed some of them; the changed names
  were judged closer to the true ones somewhat more often than the old ones, by functions and by
  groups, but not beyond chance; it changed only one group's name, the device line having fixed the
  misread groups already. On the way it crashed the process in Unicorn (eight threads in one engine of
  dread.constants: engines are per thread since). Its people's names not tried there.
- [x] **What the naming runs cost** (asked for 2026-10-24: this month Haiku 5.5 took 243,329 answers,
  $83.91, about $50 of it output; Sonnet 5.5 $43.66). Done when the pilot's questions can go as
  Anthropic's Message Batches, what several questions share is marked for the prompt cache where it
  reaches the model's minimum, the answers lose what nothing reads, each measured on FreeRTOS GCC -Os
  (cost, time, the names' quality) and decided (docs/decisions.md, three entries of 2026-10-24).
  Done 2026-10-24. Every question is a conversation (a generator: dread.llm converse for one question
  after another, converse_all for many in rounds, a round of `--batch N` questions or more as a Message
  Batch, default 100), so an answer of a batch is handled as one asked alone; `--task name`'s passes go
  in waves when a level is that large. The usage ledger got a mode column (`batch`, priced at half) and
  the cache's hour-long writes (docs/formats.md); `dread llm batches` lists and cancels batches.
  Measured (Haiku 5.5, defaults, `--no-device --jobs 16`): `--task name` $1.07 and 648 s one at a time
  (main's code), $0.69 and 1,858 s with batches, $0.57-0.61 with the cache too (A1, the closest to a run
  alone: 1.67 M of 4.25 M tokens in read from the cache), the names as good by their words; `--task
  readable` $0.16 and 167 s, $0.10-0.11 and 235-299 s with batches. The answers' hidden reasoning (375 of
  a name answer's 474 tokens out; Haiku 5.5 thinks by default and returns none of it) is the only text
  produced that nothing reads; without it (`--no-thinking`, llm.toml's quick fields) a name run cost
  $0.25-0.29 and a readable run $0.06-0.07, but the names got worse: by their words in every one of five
  runs a side (the same words 21-25 against 70-81 of 366, overlap 0.24-0.26 against 0.40-0.42), the
  readable names in every one of three (parameters as the source 45-49 % against 52-53 %); judged against
  the source by Sonnet 5.5, five runs a side: 0.645 +- 0.009 against 0.784 +- 0.018 (p 0.008, by groups
  25:3). So the reasoning stays (THINKING; `--no-thinking` an option); the run one at a time (0.785) and
  the one with batches alone (0.783) judged as the runs with batches and the cache.
  The sentences are read (names.tsv, the GUI, the review, the judge; readable C's comment) and stay.
  Spent: $9.42 of API (Haiku $6.77 for 19 naming runs and the probes, Sonnet $2.66 judging 12 runs, its
  questions in batches: $2.28 for ten runs, 2.48 M of 3.33 M tokens in read from the cache). Open: batches and the cache on a large image (time, the share batched); `--thinking`
  per task with effort levels between (Haiku 5.5's low effort wrote 406 tokens where medium wrote 474 on
  one question: not tried on runs); asking the losing samples' sentences no more (about half of a
  quick answer's tokens; the kept sentence would then be another sample's).
- [x] **Scored against the sources.** A bench step: per build, the functions turned readable and
  accepted, gotos left, statements and nesting against the source's functions.
  Done 2026-10-22: `dread bench measure RECIPE [BUILD...] --steps readable`, one of the default steps,
  after the lift check and the recorded cases (dread/bench.py readable_step, dread/cmetrics.py,
  docs/decisions.md). It runs `dread readable --check` again only when a digest of what the gate
  decides from changed (DREAD's code but the two measuring modules, the build's tables, image and
  kept cases; otherwise its last verdicts are taken) and counts the functions lifted, turned readable
  (built from readable C and tried) and accepted, and why the rest stays lifted, by category.
  `dread readable --figures FILE` measures the accepted functions' readable C as built, on
  dread.cparse's tree: statements, the deepest nesting of control statements (an else if no deeper,
  a macro's do { } while (0) a block), gotos, labels, loops, casts (not those of raw accesses), raw
  accesses through pointer casts and of them those through the frame's base, members p->f_.. and of
  the frame's object, fixed addresses by name, variables and the typed ones (a structure or a pointer
  to one): sums, medians and maxima, beside the same functions' lifted C. Against the source: a
  readable function's entry gives the subprogram DWARF has there (a clone's source function through
  its abstract origin, a .part kept out of the ratios), its translation unit is found among the
  project's sources by the end of its path (patched copies, moved builds), and the source function
  is counted with the same skeleton and count on clang's JSON tree of the source as the build's own
  compiler preprocessed it (macros expanded: configASSERT is an if and a call on both sides; in its
  container for a toolchain's build; clang told the compiler's C standard, C23 from GCC 15 on). Per
  function readable / source, also against the source with the functions DWARF says were inlined into
  it; median and quartiles; the ten worst by statements and by depth, named, with what was inlined.
  Written to metrics.json (readable), the report (two tables across builds, the lists per build) and
  BUILD/work/readable.tsv (every readable function's figures). Tests: tests/test_cmetrics.py (a
  function written by hand whose counts are known, counted alike by dread.cparse and by clang; the
  readable-only figures; gizmo's DWARF with its clone), tests/test_bench.py (the step on gizmo-m3:
  36 of 36 readable, 35 matched, statements against the source median 1.50; a second measurement
  reuses the gate and gives the same figures). Measured on copies of the four builds with readable
  gates (sums; readable functions matched to their source; ratios per function, median (quartiles)):

  | build | readable of lifted | gotos left (functions; lifted C) | statements, median | depth median, max | raw (frame) | members (frame's object) | typed of variables | matched | statements readable / source | with inlined | depth readable / source | deeper, shallower |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|
  | GCC -Os | 317 of 329 | 259 (84; 1339) | 6775, 11 | 1, 22 | 367 (23) | 661 (354) | 232 of 2401 | 284 | 1.29 (1.00-1.65) | 1.22 (1.00-1.50) | 1.00 (1.00-1.25) | 66, 26 |
  | clang -O2 | 275 of 286 | 270 (65; 1172) | 6286, 12 | 1, 28 | 360 (12) | 623 (398) | 215 of 1968 | 241 | 1.33 (1.10-1.91) | 1.25 (1.00-1.49) | 1.00 (1.00-1.00) | 51, 39 |
  | GCC -O0 | 354 of 357 | 188 (49; 1597) | 5428, 10 | 1, 24 | 352 (38) | 675 (943) | 250 of 1699 | 320 | 1.20 (1.00-1.40) | 1.18 (1.00-1.40) | 1.00 (1.00-1.00) | 55, 16 |
  | RISC-V GCC -Os | 252 of 259 | 289 (68; 1189) | 5537, 12 | 1, 46 | 191 (20) | 410 (435) | 159 of 2124 | 245 | 1.28 (1.00-1.76) | 1.23 (0.92-1.67) | 1.00 (1.00-1.25) | 55, 26 |

  The rest stays lifted only because the system run accepted it (12, 11, 3, 7). Not matched: newlib's
  and libgcc's functions (26, 27, 27, 6: their sources are not here) and those without debug
  information at their entry (7, 7, 7, 1: newlib's stubs and lock functions; on RISC-V
  xTaskGetTickCountFromISR, whose DWARF describes it without a code range though GCC emitted its
  code, the same as xTaskGetTickCount's: not traced further). The source has one goto in all (each build's 177
  to 289 are the structure stage's); readable C has as many loops as the source in sum (136 against
  129 on GCC -Os), unrolled ones offset by inlined ones. Time per build, the step alone on a shared
  machine: 101 s (GCC -Os) and 158 s (clang -O2) with the cases cached, nearly all of it the gate;
  18 s when the gate is reused (figures 12-17 s in a pool, the source side 1-2 s, 8 s in a toolchain's
  container); GCC -O0 708 s and RISC-V 300 s, whose gates first made every case anew (generator 34
  came after their lift check). What the lists point at, by name:
  - Staircases: a source's run of checks (`if (x != expected) xErrorDetected = pdTRUE;`) whose error
    arms the compiler put after the loop with a jump back comes out as each check's success path
    nested in the one before, a label inside each level where the error arm returns: GCC -O0's
    prvSendFrontAndBackTest at depth 24 against 3 without any inlining, RISC-V's
    prvLowPriorityMutexTask at 46 against 1 (124 statements with its three inlined functions). (Flat
    after the structure rework of the same day; what nested after it: Staircases below.)
  - Cortex-M IT blocks as one if per instruction (`if (z) r2 = 0; if (!z) r1 = g_..;`) and the flag
    as a carry expression (`z = (u32)((u64)r0 + (u32)~g_.. + 1) == 0`, for r0 == g_..): GCC -Os's
    xAreStreamBufferTasksStillRunning, 65 statements against 15 (its two loops unrolled too). One
    if/else since 2026-10-22 (the next item).
  - Loops the compiler unrolled (clang -O2's vQueueAddToRegistry, 96 statements against 13) and
    callees it inlined (vInterruptMutexMasterTask, 169 against 8, 66 with its two inlined ones).
  - RISC-V's yield (ecall) with all eight argument registers bound in and out, 17 statements each:
    vTaskDelay 52 against 9.
  This closes the clean-up's and the structure's "in the bench report" (statements against the
  lifted C's and the source's, gotos left per build). Not established: that the readable C reads
  well; only how it compares in size and nesting with what was compiled.
- [x] **IT blocks as if/else.** Readable C of what a compiler made with IT blocks (Thumb-2's
  conditional execution) as the if/else it was, its flags as comparisons. Done 2026-10-22 (dread.tidy,
  docs/decisions.md). What was left on main with the structure rework in: the lifters write one
  conditional block per instruction on the flags, and (1) merge_ifs joined two only when adjacent and
  on the same condition: the IT instruction's own note between them (`/* 7164: ite eq */`) or a load
  GCC scheduled between two IT blocks kept them apart, and a condition's negation (the block's else
  part) never joined; (2) a flag several ifs read stayed a variable in its carry arithmetic, since the
  conditions pass looked at branches' and selects' conditions only and spread takes no value that
  reads memory; (3) dead and fold saw a conditional block as one statement: a register an arm set for
  its store stayed, the store's address no constant, the registers a call zeroed live; (4) a
  conditional compare (`cmp r0, r5; it ne; cmpne r4, r1`) stayed an instruction's block inside the
  if, its unread flags with it; (5) no candidate paired a compare's operand that is neither a
  variable nor a load (`adds r5, r2, #3; cmp r0, r5`: r0 against r2 + 3). Now: a run of conditional
  blocks on one pure condition or its negation is one if/else while the arms write nothing the
  condition reads (the notes in front; no label, goto, break or continue in the arms, no return in an
  arm that takes more), and BETWEEN (1) statement without a call between two of them goes into both
  arms (docs/decisions.md: 5840 statements on GCC -Os with none, 5756 with one, 5759 with two); dead
  and fold work on the blocks spread out (flat_view, which now knows the list that holds each
  statement), fold on the body first as before (on the spread-out blocks alone it no longer folded a
  load over a conditional block: three clang -O2 functions grew); an arm left empty goes; flatten
  spreads an instruction's block inside an arm and chains makes `z = A; if (!z) z = B;` the
  `z = A || B` it computes; conditions rewrites every truth value (a flag's value, the operands of
  && || !, a select's condition in a return, statements inside arms), with the compare's own operands
  among the candidates (tidy.compared); values rewrites returns and statements in arms too (strcmp's
  `return r2 - r3;`); `x = x` goes (also RISC-V's reloads of spilled registers); a mask that keeps a
  select of constants whole goes. Found on the way: fold folded a value with an effect into the right
  operand of && or || or an arm of ?:, where it runs on some paths only (chains made `z = r0 == r5 ||
  r4 == r1`, and the load of r1, made before the compare on every path, went into the ||); a select
  could take a folded load the same way before. It folds no effect there now (tidy._read_maybe). The
  cases compare peripheral accesses in order but not loads of RAM, so the gate would not have seen
  such a load skipped; it was found by reading the output.
  Statements of the readable functions matched to their source (the same functions as on main;
  sums, and per function readable / source, median (quartiles)):

  | build | statements, main -> now (source; with inlined) | ratio main -> now | with inlined, main -> now |
  |---|---|---|---|
  | GCC -Os | 6315 -> 5756 (4049; 4971) | 1.29 (1.00-1.60) -> 1.22 (1.00-1.46) | 1.20 (1.00-1.45) -> 1.14 (1.00-1.33) |
  | clang -O2 | 5852 -> 5611 (3087; 4445) | 1.33 (1.07-1.84) -> 1.30 (1.00-1.79) | 1.22 (1.00-1.50) -> 1.15 (1.00-1.43) |
  | GCC -O0 | 4837 -> 4827 (3972; 4011) | 1.17 (1.00-1.36) -> 1.17 (1.00-1.36) | 1.14 (1.00-1.33) -> 1.14 (1.00-1.33) |
  | RISC-V GCC -Os | 5204 -> 5191 (3294; 3793) | 1.25 (1.00-1.69) -> 1.25 (1.00-1.67) | 1.20 (0.91-1.55) -> 1.20 (0.91-1.55) |

  On GCC -Os xAreStreamBufferTasksStillRunning 65 -> 28 (source 15), xAreMessageBufferAMPTasks-
  StillRunning 19 -> 12 (5), xAreRecursiveMutexTasksStillRunning 37 -> 16 (13), vInterruptMutex-
  MasterTask 157 -> 81 (8; 66 with what was inlined), prvHighestPriorityPeekTask 70 -> 30 (25); on
  clang -O2, which branches more, the same three 31 -> 29, 17 -> 13, 14 -> 14, xQueueGiveFromISR
  74 -> 54 (31). No function grew on any of the four builds; gotos as before, five GCC -Os
  functions a level shallower (four of them straight-line now). The types stage finds fewer
  structures (GCC -Os 55 in 96 functions
  against 58 in 98): a constant address that sat in a register typed as a pointer is now the access's
  own. The gate accepts the same functions as on main on the eight builds (gizmo a32 35, m3 36, m4
  44, rv32 31; FreeRTOS GCC -Os 316, clang -O2 275, RISC-V GCC -Os 251, GCC -O0 353), in 78, 86, 36
  and 270 s against main's 77, 72, 33 and 269 s (the cases cached; clang -O2's beside the test
  suite). tests/test_tidy.py: each shape on lifted C (an IT block's if/else with its loads per
  path, merging stopped by a write of the condition or a branch, the load between two IT blocks, a
  conditional compare whose loads stay unconditional, a flag read twice, the mask), and 300 random
  functions of IT-block-like code (compares, conditional blocks on every condition an IT block tests,
  conditional compares, statements and calls between them, the calls logging their order and
  arguments), cleaned up, compiled with the host's cc beside the originals and called with random
  arguments: the same results and calls.
  Left: a flag read across a call, or by a select and an if, stays a variable (z = r0 == r1; 13 such
  assignments in 11 functions on GCC -Os, 9 in 8 on clang -O2); a select the selects pass made from
  an IT block's two moves before merging sits beside the if (`r0_1 = z ? 0x0u : 0x1u;`); ternaries of
  truth values are not yet the truth values they are (`r4_1 == r1_1 ? 0x0u : (!(r0 == r5 || r4 == r1)
  ? 0x1u : 0x0u)`), and a negated || of equalities is written with De Morgan only where the structure
  stage negates it (`if (!(r1 == 0 || r1_1 == 0))` beside `if (r1 != 0 && r1_2 != 0)`).
- [x] **Staircases.** Readable C nested much deeper than its source in some functions (the readable
  step's lists above): find why, and make dread.structure write such runs of checks flat where the
  flow allows it. Done 2026-10-22 (dread/structure.py arrange; docs/decisions.md). Measured again
  first: after the structure rework of the same day the runs the lists named were flat already
  (prvSendFrontAndBackTest 3 levels against its source's 3 at -O0, prvLowPriorityMutexTask 2 against 1
  on RISC-V: the joins where code never returns). What still nested was of two kinds. Else-if chains:
  a compiler puts an if's then arm where its test falls through and jumps to the next test, and the
  stage wrote the arm the branch jumps to first, so each test landed in the then arm of the one before
  (GCC -Os's vTimerPeriodicISRTests, a chain of a dozen tests, 18 levels against 3; tiny_print's
  format letters 9 against 3 at -O0). And runs of checks with statements between them that leave for
  one short block that returns or never ends (vTaskSwitchContext's stack check, 6 against 2), or whose
  failure arms each fall to one return (xQueueGenericCreate's, 5 against 2): each check's way on
  nested in the one before, as the post-dominators have it. Now the ifs are arranged once the blocks
  are emitted: the arm that falls through first, under the condition turned round; an arm that never
  falls through as an early exit (either arm); an else that is one if as else if, tests that exit
  folded into such a chain where they stand as an else; of these the least nested. Collapsed
  conditions keep the block that comes next as the arm that falls through (if (a || b) T else U, as
  the source has it), and a goto that ends an arm and goes where the if falls off to anyway goes.
  Where that still leaves a function three levels (structure.GUARDS) or more deeper than early exits
  that repeat the short way (TAIL) would, those exits are taken: rarely, since where a function is a
  level or two deeper they mostly turned a source's single-exit nesting into code flatter than the
  source with statements repeated (docs/decisions.md has the variants). Measured on copies of the four
  builds with readable gates, the same functions before and after (each build's accepted ones; depth
  against the source over those matched to it, summed):

  | build | deepest | depth (source) | deeper, shallower than the source | levels deeper, shallower | gotos | statements |
  |---|---|---|---|---|---|---|
  | GCC -Os | 18 -> 5 | 521 -> 466 (460) | 52, 22 -> 36, 33 | 85, 24 -> 45, 39 | 27 -> 27 | 6,600 -> 6,617 |
  | clang -O2 | 12 -> 12 | 415 -> 385 (373) | 47, 31 -> 38, 35 | 78, 36 -> 56, 44 | 32 -> 32 | 6,183 -> 6,192 |
  | RISC-V GCC -Os | 6 -> 5 | 408 -> 374 (393) | 33, 27 -> 20, 37 | 46, 31 -> 25, 44 | 21 -> 20 | 5,249 -> 5,245 |
  | GCC -O0 | 9 -> 6 | 511 -> 482 (490) | 29, 18 -> 17, 23 | 39, 18 -> 18, 26 | 5 -> 4 | 5,168 -> 5,169 |

  Deepest against their sources now: functions with others inlined (vStartTimerDemoTask 4 against 1,
  prvBlockingTask 4 against 2 with nine inlined), a loop the compiler unrolled into tests that all
  leave for one block (clang -O2's prvInitialiseNewTask copying a task's name, 12 against 3), and
  tests with statements between them in an else-if chain (RISC-V's xTaskCheckForTimeOut 3 against 1:
  `a5_2 = *(volatile u32 *)a1;` before the next test, which no else if can hold). The gate accepts the
  same functions as before on the eight builds (gizmo a32 35, m3 36, m4 44, rv32 31; FreeRTOS GCC -Os
  316, clang -O2 275, RISC-V GCC -Os 251, GCC -O0 353). The stage takes 0.18 s instead of 0.06 s for
  GCC -Os's 316 functions. tests/test_structure.py: an else-if chain, early exits, the two staircases
  with GUARDS and without, an else if printed with its comments; property tests of runs of checks laid
  out as compilers lay them out (an arm after the run that jumps back, one in place, a block several
  checks share, else-if chains; half of them in loops that never end), compiled before and after, and
  with the early exits wherever they are no deeper (GUARDS 0), here and for the flow that mostly never
  returns. Not yet: an unrolled loop's exits written as do { } while (0) with break; a test's
  statements folded into its condition.

Limits known up front: readable C compiles to other code, often larger than the original's slot, so
more functions need freed space (the overlay's placement report says where); a function whose lifted
C keeps registers exact for its callers (bound calls, -ffixed-REG, registers a callee reads stale)
may stay lifted.

### From a review against hand-written C (2026-10-23)

A review on the private real-life test put DREAD's readable C (names from the pilot) beside a
human's hand-written C of the same functions, a sample drawn over subsystems and sizes (the
pairs and the counts are in that target's notes). Every function was structured (no goto), a few
read as well as the human's, and in many of them DREAD printed something a reader would take for
a fact that is not one. The items below are what DREAD can fix by a rule, ranked by that first
(what is wrong to a reader), then by what must be decoded by hand, then by what only lengthens;
each names a public function that shows it, or a test to write where none was found. Not DREAD's
to fix in code: wrong or vague names, the names of error codes and macros, and descriptions with
the oddities and evidence a person writes (two of the items take away noise that misled the naming
model).

- [x] **Array bases where the code indexes them.** An array of structures indexed as `table[i - 1]`
  prints with its base folded one element down (`table[i]` at `base - size`), and a structure whose
  lowest accessed field is not its first starts at that field, so every printed offset is shifted
  and the base matches no address in the listing. Keep the base the code makes (the literal it
  loads) as the array's start, print the index as the code computes it (`i - 1`), and start the
  structure at that base, not at its lowest accessed offset.
  Done 2026-10-23 (dread.statics base_of, find_arrays and _made_above; dread.naming apply_globals;
  docs/decisions.md, "Array bases where the code indexes them, and no address for a plain value"). GCC
  16 (-Os, -O2; ARM state, Thumb-2, RISC-V), GCC 4.9 (ARM) and clang 23 (Thumb-2, RISC-V) load the
  array's own address for `table[id - 1]` and fold the -1 into the index's arithmetic or into the
  access's offset; readable C's sum then folded the literal into a constant an element below the
  array, where find_arrays started it (with the data symbols the array's own cut it to less than an
  element: no array at all). The literal readable C's sum adds whole (base_of, also through the
  variables straight_facts and scales_of know, whose facts carry it) is the base now: where every base
  a run of indexed accesses was made from lies above them all (by at most BELOW = 256 elements), the
  array starts at the lowest, or at a data symbol within an element below it (FreeRTOS GCC -Os loads
  pxReadyTasksLists + 4 and xQueueRegistry + 4 in some functions: a first version moved both arrays
  four bytes up), else as before. An access below an array that starts at its sum's base is that
  array's element `index - n` (`g_20000020[r0 - 1].f_4`, as an address `(u32)&g_20029954[lr -
  1].f_8`), the element's unread first field a gap (`u8 pad_0[4]`). An indexed access below the
  address the machine code made it from starts that address's array, not a place in front (plan's
  starts), and makes no run of equal fields in front an array of words (clang -O2's
  `pxReadyTasksLists[i - 2]` had cut xQueueRegistry's structure at 44 bytes). Public examples after
  all: FreeRTOS clang -O2 xTaskGetHandle (`uxQueue--` before `pxReadyTasksLists[uxQueue]`, unrolled:
  it read `r9 = (u32)&g_20029954[lr]; if (*(volatile u32 *)(r9 - 20) != 0)`, now `if (g_20029954[lr -
  1].f_0 != 0)` and the list end's address `(u32)&g_20029954[lr - 1].f_8`; the function does not lift
  there, so the gate does not see it); gizmo a32's handle_key switch table, indexed by `c - 'b'`,
  starts at its literal 0x20dac, not 98 bytes in front (`(void)rom_20dac[r3];`). Tests
  (tests/test_statics.py): the slot ledger program (an array of 20-byte structures behind another
  array, indexed by `id - 1`, its first field read nowhere) built with GCC -Os for ARM state, Thumb-2
  and RISC-V, with and without the data symbols, through the gate; the start rule (a run below its
  base, a field's literal with and without the array's start made elsewhere, a switch table, an
  anchor); the element counted from the base's array, not the one in front; on every gizmo build, each
  array starts at a data symbol or an address its code makes. Measured on the eight builds (main ->
  now): the gate accepts the same functions (gizmo a32 35, m3 36, m4 44, rv32 31; FreeRTOS GCC -Os
  315, clang -O2 275, RISC-V GCC -Os 252, GCC -O0 354, name by name); every structure and array of
  static data and every structure behind pointers is the same, but clang -O2's xQueueRegistry (a
  structure of 11 words -> 18; static data's fields that are the source's 136 of 136 -> 143 of 143)
  and gizmo a32's switch table (0x20d4a -> 0x20dac, at no variable either way); the shapes' scores the
  same on every build; on all 39 FreeRTOS builds of the benchmark (dread readable --statics) every
  structure and array of static data the same but that one, with the data symbols and without. Left: a
  compiler that loads a field's address (`table + 4`) in every function that indexes the array starts
  it there where no symbol says otherwise; `table[i - 1]` through a section anchor (GCC without
  -fdata-sections) or clang's merged globals (RISC-V) is made from the anchor, and the array still
  starts at the folded constant (or, with the data symbols, is not found); `arr[i + 1]` through a base
  of its own still makes an array of its own.
- [x] **No argument from a result register the callee always writes.** trim_calls keeps a used
  call's result registers as arguments, because a callee that does not write them hands back what
  the caller passed (newlib's lock functions). A callee that writes them on every path gets a
  phantom argument, and the caller's own parameter then looks read, so functions that take nothing
  get a parameter. Give regflow a must-write set (registers written on every path to a return,
  beside preserved) and keep the argument only for a result register outside it. Public examples:
  FreeRTOS mps2-gcc-Os prvCheckExpectedTimeIsWithinAnAcceptableMargin, `r0_2 = xTaskGetTickCount(r0)
  - r0;`, while xTaskGetTickCount's own readable C is `u32 xTaskGetTickCount(void)`; gizmo a32
  read_line, `r0 = uart_getc(r0);` (source `int c = uart_getc();`), and uart_getc itself printed as
  `u32 uart_getc(u32 r0)`. A second way to a phantom parameter, not this rule: gizmo a32
  print_status(void) prints with four parameters because the push that makes room for its buffer
  stores r0-r3 into it.
  Done 2026-10-23 (dread.regflow overwritten; dread.tidy trim_calls, params, selects; dread.callconv;
  docs/decisions.md). regflow finds for every function the registers it overwrites: written on every
  path to a return (its callees' and tail callees' such writes included; a pop, and a reload regflow
  pairs with its save around a call, put the caller's value back; a call's clobber list, MSP430's,
  writes nothing; a write under a condition counts where the condition holds) less those whose value
  from the caller may reach what the function reads, through a callee that hands it back unwritten on
  some path. The must-write set alone was not enough: a function that adds 1 to what such a callee
  hands back writes r0 on every path from its caller's r0, which regflow's reads, ending r0 at the
  call, do not see (tests/test_regflow.py). A call whose value is read keeps an argument in a result
  register only where the callee does not overwrite it, and trim_calls runs again after the clean-up,
  so that an argument kept for a value that then only fed another call's dropped argument goes too
  (GCC -Os: vPortEnterCritical(r0) in front of vPortExitCritical). Two more ways to the same symptom
  on the way: params took an if/else whose arms both write a register for one statement that writes it
  on some path (it reads the entry's value in flat_view's flow now), and selects made an IT block's
  last two instructions a select before merge_ifs could merge the three, reading r0 where the flow saw
  the entry's r0 reach it: uart_getc is `u32 uart_getc(void)` on all four gizmo builds. dread callconv
  checks the set by measuring ("returns from, regflow: own": a function whose result follows its
  caller's value of the register): none of the 330 functions with cases that regflow says overwrite r0
  or a0 on the four FreeRTOS builds, none on gizmo. Counted by a script (not in the repository) on the
  C after the stages of the lifted table's functions: a phantom argument is one past what the callee
  reads in a register it overwrites, a phantom parameter one of the readable C's parameters named
  after its register (placeholders aside) that regflow says the function does not read, unless a
  result it may hand back. Main at 3aaf4db -> this, calls with a phantom argument: gizmo a32 8 -> 0,
  m3 8 -> 0, m4 9 -> 0, rv32 7 -> 0; FreeRTOS GCC -Os 133 -> 4, clang -O2 144 -> 0, RISC-V GCC -Os 73
  -> 0, GCC -O0 134 -> 6 (the rest are calls not trimmed at all: one with an argument that loads,
  vApplicationStackOverflowHook(pxCurrentTCB, ...), and sprintf, one of whose calls passes stack
  arguments). Functions with a phantom parameter: gizmo a32 5 -> 3, m3 6 -> 3, m4 6 -> 3, rv32 0; GCC
  -Os 42 -> 33, clang -O2 63 -> 58, RISC-V GCC -Os 83 -> 79, GCC -O0 17 -> 2: what is left comes from
  elsewhere (the next item). Arguments kept in a result register for a callee that may hand it back:
  GCC -Os 125 -> 61, GCC -O0 116 -> 52, RISC-V 24 -> 7, clang -O2 14 -> 14 (prvUnlockQueue's r0
  through vPortEnterCritical and vPortExitCritical to its own result, which the lift check compares).
  The gate accepts the same functions as before on the eight builds (gizmo a32 35, m3 36, m4 44, rv32
  31; FreeRTOS GCC -Os 315, clang -O2 275, RISC-V GCC -Os 252, GCC -O0 354; main on the same copies
  the same); the lifted C is byte for byte as before (gizmo a32, m3, rv32, GCC -Os compared). Time:
  making every lifted function's readable C in one process on GCC -Os 37.5 s before, 37.8 s after; the
  check's wall time with cached cases before -> after 307 -> 337 s, 75 -> 54 s, 25 -> 20 s, 120 -> 85
  s on the four FreeRTOS builds (the machine shared: noise), regflow's analysis 0.1 to 0.2 s more a
  build (its result is cached).
- [ ] **No parameters from pushes, system calls and calls that keep every argument.** What still makes
  a parameter of a register the function does not read (counted as for the item above, 2026-10-23):
  (1) a push that makes room for locals stores the caller's r0-r3 into the region whose address goes
  to callees, `stack20.f_0 = r1; ...` (gizmo a32 print_status(void) and main with four parameters;
  FreeRTOS GCC -Os 54 of the 60 phantom parameters, the tasks that push {r0, r1, r2, ...}); (2) calls
  of a variadic function or of one that calls one keep all their arguments (readable.variadic_reach:
  vAssertCalled hands its line on to printf), and each caller passes on its own entry registers in the
  arguments past its setup: clang -O2 104 of 106, RISC-V GCC -Os 389 of 405 (a2-a7 to vAssertCalled);
  (3) RISC-V's ecall binds a0-a7 in its asm (vTaskDelay: a1-a7; 14 on that build); (4) calls through a
  pointer pass all argument registers (newlib's __sflush_r and _fwalk_sglue: r3); (5) a value a callee
  hands back that goes round a loop into the same call (prvIdleTask's
  prvCheckTasksWaitingTermination(r0)). For (1): print a push's store of a register the function does
  not read (regflow: no use) so that it reads no parameter (the slots hold what the caller left, which
  no correct program reads before writing them; the gate decides), for (2) and (3) pass a variable of
  no value where the caller's register only flows through (the callee's save area reads it, but
  nothing the function computed), each keeping what the comparisons see.
  (1) done 2026-10-23 with "Local objects whose address goes to a callee" (dread.tidy push_room: a
  push's stores into words no load of the function reads, or that it stores over before anything may
  read them, go): gizmo a32, m3 and m4's print_status and main 4 parameters -> 0 each, FreeRTOS GCC -Os
  32 fewer in 21 functions. (2) grew on the GCC builds by the same work: newlib's printf and sprintf
  push their arguments, which variadic_reach did not find, so their calls were trimmed (sprintf(buf,
  fmt) without its value); now such a call keeps them all (readable.entry_save_areas), and five
  functions that call one (three on GCC -O0) pass their entry registers on.
  (2) done 2026-10-26 (docs/decisions.md, "The arguments of a printf call from its format"):
  dread.varargs counts the words a constant printf format takes; dread.regflow 28 counts them as the
  call's uses, so a wrapper that hands its line on reads it; readable trims each such call to them, the
  callee declared variadic, and keeps every argument only where a variadic function's call has a format
  it does not know; a stub of a printf-family function logs the registers its format takes (only logs:
  the fills behind pointers go by the case's own mask). Main -> this on the eight builds: the gate
  accepts the same functions (gizmo a32 35, m3 36, m4 44, rv32 31; FreeRTOS GCC -Os 328, clang -O2 288,
  RISC-V GCC -Os 252, GCC -O0 378); gizmo's C is unchanged; the readable C's parameters GCC -Os 487 ->
  473, clang -O2 579 -> 380, RISC-V GCC -Os 1009 -> 442, GCC -O0 491 -> 484; vAssertCalled's calls pass
  the file and the line (GCC -Os: the line had been lost, its arguments 677 -> 1376; clang -O2 3616 ->
  1800; RISC-V GCC -Os 5448 -> 1354). The first measurement lost three GCC -O0 functions: counted among a
  stub's arguments, the format's register moved the seeds of the fills behind pointers, and cached cases
  ran otherwise than they were made (prvCoreATask's overwrote its loop counter and faulted in the
  original). Left: a format kept in a saved register across a loop (the next item), formats with 64-bit
  or floating-point words, a format argument on the stack.
  (3) done 2026-10-26 (docs/decisions.md, "What a system call hands its handler"): `[abi] syscall_reads`
  says what a system call's handler reads; with it readable C binds those and the registers read after
  the call (dread.tidy syscall_binds), dread.regflow counts them read there and the cases log them at
  each system call; without it, all stays as it was. The FreeRTOS recipe says `[]` for its three ports.
  Main -> this on the eight builds: the gate accepts the same functions; gizmo and the ARM builds are
  unchanged (their svc lies in assembly that is not lifted); RISC-V GCC -Os's readable C 442 -> 412
  parameters, its bound registers 200 -> 3 (vTaskDelay(a0): `if (xTaskResumeAll() == 0) __asm__
  volatile ("ecall" ...)`), and the readable_types table found more there (globals as structure
  pointers in 88 files).
  (5) done 2026-10-26 (dread.tidy trim_calls, _fed_back): an argument a callee may hand back in a result
  register goes where every read of a variable its calls assign is that argument of its own calls: the
  value goes nowhere but back into the same call (FreeRTOS's prvIdleTask and its
  prvCheckTasksWaitingTermination(r0) loop; chained vTaskSuspendAll calls). Main -> this on the eight
  builds: the gate accepts the same functions; gizmo and clang -O2 unchanged; GCC -Os 473 -> 472
  parameters, RISC-V GCC -Os 442 -> 440 (prvIdleTask(void), vQueueReceiveWhenSuspendedTask(void)), GCC -O0
  484 -> 482. (4), calls through a pointer, stays open: what such a call may reach is not known.
- [x] **A format kept in a saved register across a loop.** dread.varargs finds a call's format where
  the code puts its address into the register on the straight run in front of the call
  (dread.constants at). RISC-V GCC -Os computes a format once into a callee-saved register in front of
  a loop and copies it into a1 at each call of sprintf in the loop, so the run back from the call stops
  at the loop's head: on virt-gcc-Os 6 of the 13 calls of printf and sprintf are not known (the
  tasks' sprintf(buf, "...", n) in their loops), and those calls keep all eight arguments. A register
  written once in the function, with a constant, on the way into the loop (its one write on every path
  from the entry to the call) holds that constant at the call; regflow's graphs give the paths.
  Done 2026-10-26 (dread.varargs given_at, dread.constants after and at's given): the registers the
  straight run reads that hold one constant where it starts (their one definition reaching it, the
  caller's value not; a call ends the scratch registers, not the callee-saved ones), its value taken
  behind the definition, also through a definition that computes from such a register (depth 3). On
  virt-gcc-Os all 13 calls are known now (were 7). Main -> this on the eight builds: the gate accepts
  the same functions; gizmo, GCC -Os and GCC -O0 unchanged; clang -O2 380 -> 373 parameters, RISC-V GCC
  -Os 440 -> 425 (prvCoreATask(void)). Found on the way: dread.program extent_owners began the room
  behind the image at the halfword holding the image's last byte where the image ends at an odd
  address; a format's NUL there made every case of its reader refused for loading code.
- [x] **Flags as comparisons, the rest.** After the IT-block work, flags still print as u64 sums with
  carry and zero variables where one compare or subtraction is read by two conditions (borrow, then
  zero), where an add's carry is tested, where a compiler dispatches by successive subtractions
  (`subs #1; blo; beq; subs #2; beq; ...`) and in carry chains of multi-word arithmetic. Fold a
  borrow/zero pair into `x <= k`/`x == k`, a chain of subtractions into comparisons with the
  accumulated constant (`x - 1 - 2 == 5` is `x == 8`), an add's carry into `sum < operand`, and an
  adds/adc pair into a 64-bit sum. Public examples: gizmo a32 cmd_mode (`if (mode > 7) return -1;`
  printed as `t_1 = (u64)r0 + (u32)~0x7u + 1; ... return !c || z ? 0x0u : ~0x0u;`), parse_kv;
  FreeRTOS mps2-gcc-Os prvAddCurrentTaskToDelayedList (`t_7 = (u64)r5 + r0 + 0; ... if (t_7 >
  0xffffffffu)` for the wake time's overflow test); mps2-gcc-O0 vPortFree (`(u32)((u64)0x0u +
  (u32)~0x8u + 1) + r0` for `r0 - 8`). The successive-subtraction dispatch: none found in GCC builds;
  test with hand-written ARM-state code (gizmo assembly) or a unit test of the conditions pass.
  Done 2026-10-23 (dread.tidy flags, sums, pairs, FlagFlow; conditions in calls and stores; cexpr.Checker;
  docs/decisions.md): a truth value whose variables have one definition each that reaches it with its
  value unchanged (flags, 64-bit temporaries, their 32-bit results, a variable plus a constant) is
  searched for in terms of what those definitions read and proved by z3, at each reader: cmd_mode's ls
  is r0 <= 7 in its store and in its result's select; prvAddCurrentTaskToDelayedList's wake-time test
  is r5_1 = r5 + r0; if (r5 > r5_1), as its source writes it; a dispatch by successive subtractions
  compares its first variable with what the constants come to (tests/test_flags.py: subs r0, #1; blo;
  beq; subs r0, #2; beq; cmp r0, #5; beq, ARM-state assembly of our own, as r0 == 0, 1, 3, 8, through
  the lift check and the gate); a conditional compare's two flags are one condition of two comparisons
  at each reader (parse_kv: r5 == 0x5fu || lr_1 <= 0x19u, r5 != 0x5fu && lr_1 > 0x19u; strcmp's while
  (r2 != 0 && r2 == r3)); a condition read where what it compared has changed is computed where its
  flags are set (gizmo-m3 fmt_dec: cc_1 = r1 > 9; ... while (cc_1)); rsbs/adcs is x == 0
  (prvCheckExpectedState(xStreamBufferSpacesAvailable(r0) == 0x1eu)); a sum through 64 bits whose carry
  nobody reads is the 32-bit sum anywhere in a statement (vPortFree's s12 = r0 - 8; uxListRemove(r6 +
  4)); adds/adc is one 64-bit sum (t_7 = ((u64)r2_2 << 32 | r3_2) + 1; r3 = (u32)t_7; r2 = (u32)(t_7 >>
  32)) and cmp/sbcs one 64-bit comparison (((u64)r2 << 32 | r3) >= 5). Found on the way: webs lost a
  declaration's initial value read at a loop's head (fixed; the eight builds accept the same functions
  with the fix); cexpr.Checker evaluates lazily and tries first the value that refuted the last proposal
  (the same verdicts).
  Measured on fresh copies of the eight builds (no readable tables in them), the lifted table's
  functions as dread readable prints them, main at b98f0d1 -> this (gizmo a32, m3, m4, rv32; FreeRTOS GCC
  -Os, clang -O2, RISC-V GCC -Os, GCC -O0): conditions with a 64-bit sum (the lifters' carry arithmetic:
  a + or - of a (u64) cast, or a 64-bit temporary read) 0, 1 -> 0, 1 -> 0, 0; 23 -> 0, 16 -> 4, 0, 15 ->
  0; statements with one 9 -> 0, 5 -> 0, 5 -> 0, 0; 170 -> 6, 104 -> 22, 0, 56 -> 6; flag variables
  (n, z, c, v and their webs' parts) 7 -> 1, 2 -> 0, 2 -> 0, 0; 18 -> 6, 18 -> 5, 0, 4 -> 0; 64-bit
  temporaries 3 -> 0, 2 -> 0, 2 -> 0, 0; 70 -> 2, 21 -> 6, 0, 7 -> 2; functions with any of these 3 ->
  1, 2 -> 0, 2 -> 0, 0; 29 -> 6, 24 -> 5, 0, 10 -> 1; lines 610 -> 597, 603 -> 599, 717 -> 713, 709 ->
  709; 9543 -> 9405, 9119 -> 9102, 8461 -> 8458, 8695 -> 8682. The 64-bit sums left are
  prvSanityCheckCreatedQueue's 64-bit counter, now written as one (its temporaries, and clang -O2's
  four loop conditions that read them); the flag variables left are comparisons kept as variables
  (below). The gate accepts the same functions as main on the same copies, the same
  sets (gizmo a32 35, m3 36, m4 44, rv32 31; FreeRTOS GCC -Os 315, clang -O2 275, RISC-V GCC -Os 252,
  GCC -O0 354). Time, on a machine shared with other work (load 10-20): the check's wall time on warm
  copies, two runs each alternating main and this, GCC -Os 441 and 312 -> 396 and 357 s, clang -O2 46
  and 45 -> 66 and 66 s, RISC-V 19 and 21 -> 24 and 19 s, GCC -O0 77 and 83 -> 82 and 76 s, gizmo 3 to
  10 s either way; making every lifted function's readable C in a pool of eight, CPU summed over the
  functions, three rounds interleaved: clang -O2 45.3 -> 55.1 s (wall 8.9 -> 10.7 s), RISC-V 27.8 ->
  30.7 s; GCC -Os, two rounds, 42-45 -> 46-50 s. The flags pass costs about a tenth of the clean-up's
  time per function (its flow is made anew each round); the Checker's change pays part of it back
  (30 of the random functions: 16.5-19.9 s of CPU with main's Checker, 8.6-10.4 s with this one, the
  same output).
  Left: truth variables that hold a load or a call read by two statements (z = *(volatile u8 *)(r4_1 +
  84) == 2; they must stay variables: the load is made once); a comparison whose operand a conditional
  block between its readers writes (gizmo a32 parse_kv: z_1 = r0_1 == 0; if (z_1) r0_1 = ~0x0u; if
  (!z_1) ...: the second reader cannot read r0_1 == 0, and such a flag goes into all its readers or
  none); clang -O2 prvInterruptTriggerLevelTest's c && !z_1 (not traced); a branch two paths with different
  compares share (vPeriodicEventGroupsProcessing: z = r0 == 1 on one, z = r0 == 0x12 on the other);
  a conditional compare whose flags read memory (vStartTimerDemoTask: z = g_20001818 == 0; if (!z) z =
  r0_10 == 0) or whose condition has no comparison of the variables left (clang -O2
  xAreTaskNotificationTasksStillRunning: subs r1, r1, r2; cmphi r1, #5, the first compare's hi needs
  the load r2 held); products' high words and the 64-bit counter's words stay 64-bit, as they are.
- [x] **Local objects whose address goes to a callee.** A frame region whose address is passed on
  prints as raw accesses through the lifted frame array, or as one packed structure named after its
  offset with word fields over a byte buffer or over separate scalars; a frame array indexed by a loop
  variable stays raw too. Split such a region into locals by its runs of accesses (scalars, `u8
  x[n]` for byte runs, a structure for mixed runs, typed by the callee parameter's shape where there
  is one), without volatile, and make an indexed region an array. Public examples: gizmo a32
  print_status (`char buf[12]` as `volatile stack20_t stack20` with u32 fields, `(u32)&stack20.f_0`
  to fmt_dec and uart_puts); FreeRTOS mps2-gcc-Os xTimerGenericCommandFromTask (the daemon message as
  a packed `stack40_t`). The raw form (no structure at all): none found in the public builds.
  Done 2026-10-23 (dread.frames plan, Scan, kept_params, save_words, frame_objects, whole_frame;
  dread.tidy push_room and index_sum; dread.readable Context.saves, entry_save_areas, make_plan and the
  check's whole; dread.shapes define and the readable_types table's keeps, returns and whole;
  docs/decisions.md, "Readable C's frames: an object at each address handed out, volatile only where
  an address may escape"; docs/formats.md). An object starts at every address handed out (to a callee,
  into memory, as the result, to assembly; through a variable that holds it; a sum's with an index)
  and reaches up to the next one, below the words the entry saves its caller's registers into (the
  callee-saved registers and the return address, through dread.regflow's Frames: no local's bytes,
  their stores go: RISC-V's lifted C kept `stack20.f_10 = ra;`); objects are joined where an access
  spans two, where a variable walks downward from an address inside one (printi's print_buf + 11),
  where an index is added to an address no object starts at (clang's tmp - 1 for tmp[--n]), and over
  the size of a callee's structure. An object is a scalar where one access fills it (or a callee's
  parameter types it so), bytes where only bytes or nothing is accessed, an array of the width an index
  or a callee's parameter says, a callee's structure of dread.shapes where its parameter points to one
  (each access past it an object of its own: GCC -O0's spilled locals beside the TimeOut_t), else a
  structure of its own (plain where natural; packed, with unions, where not); an access through an
  index is an element (stack48[r3]). Volatile only where an address may escape into memory: stored,
  returned, handed to assembly, through a pointer, or to a callee that may keep it (kept_params over
  every lifted function, the readable_types table's keeps and returns: a callee not lifted, variadic or
  naked keeps everything), or accessed by the function through a variable with another type than
  bytes. A function whose objects its cases reject (a generated case's queue copies another item size
  than the local holds: clang -O2's prvSendFrontAndBackTest and vPrimaryBlockTimeTestTask) is tried
  once more with its region as one object, as before (the table's whole). Part (1) of "No parameters
  from pushes" with it (dread.tidy push_room): a push's stores into words no load of the function
  reads, or that it stores over before anything may read them, go. The forms: print_status
  `u32 print_status(void)`, `u8 stack20[12];` (not volatile: fmt_dec and uart_puts keep nothing),
  `fmt_dec((u32)stack20, ...)`; main `u32 main(void)` with `volatile u8 stack28[12];` (fmt_hex is not
  lifted: it may keep it); apply_config `u32 stack64; u8 stack60[12]; u8 stack48[40];` (its source's
  value, key and line; volatile: parse_kv hands value on to parse_uint, which is not lifted) and
  `stack48[r3] = r2;`; xTimerGenericCommandFromTask `stack40_t stack40;` of `u32 f_0, f_4, f_8; u8
  pad_c[8];` (plain, not volatile: xQueueGenericSend keeps its queue and its ticks, not the item);
  xQueueGenericSend `u32 stack36;` and `struct s_29 stack32;` (xTicksToWait, and the TimeOut_t the
  callees' prototypes type), the push's stores of r1-r3 into them gone. The raw form: clang -O2 adds an
  index to sp0 before the constant (sp0 + r5 - 0x14u), which frame_locals and dread.frames took for an
  address at sp0 and left the whole frame raw (prvReceivingTask, prvNonBlockingReceiverTask_53c8,
  xStreamBufferNextMessageLengthBytes; GCC -Os prvReceivingTask): now prvReceivingTask's cRxBuffer is
  `u8 stack20[20];` read as `stack20[r5]`, and xStreamBufferNextMessageLengthBytes's length `u32
  stack24;` that memcpy fills at `(u32)&stack24 + r7`.
  Found on the way: on the GCC builds newlib's printf and sprintf push their argument registers, which
  readable.variadic_reach did not find, so their calls were trimmed to what regflow says they read:
  FreeRTOS GCC -Os's prvNonBlockingSenderTask_4138 and prvReceiverTask_4088 called sprintf(buf, fmt)
  without the value they print, and the base run's whole image dropped both (it agreed before only by
  what the C compiler left in r2); entry_save_areas keeps a call of such a function whole (not
  propagated to their callers' callers: vAssertCalled's calls stay trimmed).
  Measured on copies of the eight builds (gizmo a32, m3, m4, rv32; FreeRTOS GCC -Os, clang -O2,
  RISC-V GCC -Os, GCC -O0), main at c145c3d -> this, every lifted function's readable C: the gate
  accepts every function it accepted (35, 36, 44, 31; 315 -> 317, 275, 252, 354, name by name; GCC
  -Os gains the two sprintf tasks; clang -O2's prvSendFrontAndBackTest and vPrimaryBlockTimeTestTask
  with their region whole). Frame objects (functions with any): 5 (5), 5, 5, 5; 56 (56), 50 (50), 45
  (45), 56 (56) -> 7 (5), 7, 7, 7; 81 (57), 74 (53), 57 (45), 85 (56); of them structures 3, 3, 3, 4;
  47, 41, 45, 55, all packed -> none on gizmo; 26, 12, 28, 49, of them packed 1, 3, 1, 5 (the whole ones
  and where accesses overlap), arrays 2, 2, 2, 1; 9, 9, 0, 1 -> 6, 6, 6, 6; 18, 15, 7, 4, scalars 0 ->
  1, 1, 1, 1; 34, 47, 19, 29, callees' structures 0 -> 0 on gizmo; 3, 0, 3, 3, volatile all -> 5 of 7
  on each gizmo build; 28 of 81, 56 of 74, 21 of 57, 51 of 85 (where a callee is not lifted, so it may
  keep the address: gizmo's fmt_hex, strcmp and parse_uint; clang -O2's xQueueGenericSend and
  xQueueReceive; sprintf, opaque, everywhere), elements through an index 0 -> 2, 2, 2, 0; 1, 1, 0, 0.
  Raw frame accesses (functions; accesses): GCC -Os 6; 41 -> 5; 36, clang -O2 7; 30 -> 4; 22, RISC-V
  2; 23 and GCC -O0 6; 25 unchanged, gizmo none: what is left is the wrapped
  printf and sprintf (the real stack) and the slots a call through a pointer is handed that nothing
  wrote (_fwalk_sglue, __sflush_r, prvReloadTimer, GCC -O0's prvProcessExpiredTimer), as it must be.
  Parameters: main and print_status 4 -> 0 on the gizmo ARM builds; GCC -Os 32 fewer in 21 functions
  (the tasks' pushes), while 5 functions that call printf or sprintf (prvCoreATask, prvCoreBTasks,
  prvGetRegistersFromStack, vApplicationMallocFailedHook, vApplicationStackOverflowHook; three of them
  on GCC -O0) now hand their entry registers on to it (the open item's (2), as on clang and RISC-V).
  Readable C changed in 5, 5, 5, 5; 70, 50, 45, 59 functions (the objects, and the externs where
  dread.vartypes types a parameter anew); digests (what the names of the readable_names table are
  made for) in 2, 2, 2, 0; 33, 0, 0, 3 (push_room and the printf and sprintf calls: their names wait
  for a new proposal). The check's wall time on the warm copies, two runs each alternating main and
  this, the machine shared with a test run: gizmo 3 to 6 s either way; GCC -Os 277 and 271 -> 44 and 53 s
  (main halves its whole image to send the two sprintf tasks back; this accepts them), clang -O2 43
  and 41 -> 51 and 51 s (a second round for the two whole functions, every readable C made anew for
  the changed table), RISC-V GCC -Os 16 and 20 -> 17 and 22 s, GCC -O0 65 and 69 -> 83 and 72 s;
  kept_params takes 0.2 s on GCC -Os.
  Left: the externs keep u32 for a parameter given a frame object (`fmt_dec((u32)stack20, ...)`:
  dread.vartypes types the externs before the objects exist); an object reaches up to the next
  address handed out, so slots no address reaches stay fields of the object below them (GCC -O0's
  spilled locals beside a buffer) unless a callee's structure bounds it: a reach analysis of the
  callees' parameters (how far their accesses go) would split those and decide volatile where a callee
  is not lifted from its machine code; an index past an array is undefined in C (as it was past the
  lifted frame array): the cases decide, no -fno-aggressive-loop-optimizations; the objects' names are
  the defaults (stackN: the pilot's questions show the frame before dread.frames); printf's and
  sprintf's callers' callers (vAssertCalled's calls) stay trimmed on the GCC builds; save words only on
  the entry's straight run (a shrink-wrapped save later is local bytes). Not established: that a callee
  stays inside the object it is given, that an index stays inside its array (the cases and the whole
  image decide, as for every transformation; where the cases see otherwise, whole).
- [x] **A refused case must not hide a difference.** On the private real-life test the gate accepted
  readable C that had lost an argument of a formatting call: cases of the function differed, every one
  refused as "the input makes the original touch its own saved registers", and those that
  agreed still ran every instruction. A refusal says the case's input is one the C cannot reproduce;
  it should stand only where every difference of the case is one that refusal explains (the touched
  saved registers, what is computed from them, the stack words), and a difference beyond that (a stub
  call's logged arguments before the first touch, a store elsewhere) fails the function as any
  difference does (rule 6). The same question for the other refusals (stale stack data, stack-moved
  outcomes, wild calls): each with the differences it may explain, said in its line. Public
  reproduction: a function whose input makes it write past a local into its saves and that also
  passes a value to a stubbed callee, with C that passes another.
  Done 2026-10-24 (docs/decisions.md): a case is refused only where the reasons that hold explain every
  difference, and fails otherwise with `refused for X, but it differs Y: Z`. An event (a saved register
  touched, a call into the middle of a function, a written case's load of code) explains what comes
  after it, found by a clock both images keep alike (moves in and out of the function, log entries:
  dread.fragile Timeline); the stack's position the outputs that change with the stack lower (64 bytes,
  then 8, 256 and 680 while a difference is left); stale data's log only from the first entry the
  fills move. Each with wrong C in tests/test_runner.py that the old rules accepted. On the eight
  builds (gizmo a32, m3, m4, rv32; four FreeRTOS builds) the lift check and the readable gate accept the
  same functions as before and refuse the same cases; the measurement found a fault of the first
  version (fflush on mps2-clang-O2 failed) and one shift too few (prvQueueSetSendingTask's loop cases
  on RISC-V), both mended.
- [x] **dread integration's stack check, as the runner's.** An input whose builds differ is left out of
  dread integration when the original's outcome moves at all with the stack 64 bytes lower, whatever
  else differs: the hole the runner had until 2026-10-24. Left out only where every difference is an
  output the shifted runs move (fragile.stack_moved, the further shifts while one is left), else
  reported as a DIFF with the reason named. Public test: an input whose return value follows a stack
  address while a store elsewhere differs.
  Done 2026-10-26 (integration.stack_moves, problem_keys, explained): the outputs the shifted runs of
  the original move (STACK_SHIFT, then dread.fragile.MORE_SHIFTS while a difference is left), and the
  input left out only where every difference is among them; a rebuilt image that faults or hangs only
  where the shifted original fails too; else a DIFF naming what the stack moves.
- [ ] **A finer clock for an event's refusal.** What comes after a saved register touched or a wild
  call is placed by the last move in or out of the function or log entry before it (runner.After): a
  store the function's own code makes between that tick and the event counts as after it, and a
  function whose own code touches its saves before any call compares nothing but its log before the
  touch. Stores outside the stack as ticks too, where both images make them in the same order (lifted C
  keeps the original's order of volatile stores; readable C may merge or move stores between calls:
  measure how often they part on the bench before relying on it). Public test: a store, then the
  function's own loop writing past a local, with C that stores another value.
- [x] **One case in both images, to the first divergence.** Tracing a FAIL took one-off scripts: run a
  case in the original and the rebuilt image side by side and stop at the first point where they part
  (a callee entered with other arguments, a store of another value or to another place, a return
  value), naming the instruction and, for the rebuilt image, the C line. A tool (`dread trace NAME
  LABEL`, the label as the runner prints it: "generated 3", "written 0"), on the runner's machinery,
  for lifted and readable C alike, with the stack frame's words shown by their objects.
  Done 2026-10-24 (dread/trace.py; what it takes for the point where the runs part: docs/decisions.md).
  The image is `dread test`'s, or built as a lift check's round (`--lifted`) or a readable check's
  (`--readable`, `--whole`); the case is set up by the runner's own code. The original runs first with
  hooks that keep its shared points (calls with the registers the callee reads and its stack
  arguments, and the bytes behind an argument that points into a frame object; the log in order;
  stores outside the stack, by stretch between calls), the rebuilt image after it, compared event by
  event and stopped at the first difference `dread test` compares; a call with other arguments is
  reported as where the runs part first and the trace goes on. The report: the instruction in both
  images, the rebuilt image's C line (a twin build with -g, used only where its image is byte for
  byte the traced one, read with pyelftools), the statement of the lifted or readable C for the
  original's instruction, both call stacks, where one run's path left the other's (its first return
  from a function the other had not left), the last shared events, the instruction that set a
  register that differs at the end, and the frame, both runs made again to that point: the original's
  words by the C's objects (lifted C's frame array, readable C's stackN objects, the save words) beside
  the rebuilt image's (the twin's variable locations), "(not set)" where nothing was stored yet.
  Tests (tests/test_trace.py, every gizmo build): a changed constant, a stored value, a call argument
  and a loop that never ends in apply_config's lifted C (moved behind a branch on every build) are each
  named at the original's instruction and the changed C line; readable C that writes a frame buffer
  wrong parts at the call the buffer goes to; an agreeing case; the event cap. All 6,907 gizmo cases
  with lifted C and 4,978 with every function readable (a32, m3, rv32) trace as `dread test` judges
  them, without a call with other arguments in a case that agrees. On FreeRTOS's GCC -Os demo (332
  lifted functions) a readable trace of xQueueGenericSend with a planted fault (a frame object stored
  one more) took 15 to 20 s, 12 to 15 of them the two builds (lifting every function); `dread test`'s
  image without C lines 6 s; the run of `dread test` on that copy showed one real FAIL
  (prvRegTestTaskEntry1, a case that may hang: within the same limit the lifted register test made
  more rounds of its loop than the original, its counter 0x4e1e against 0x148e), which the trace named
  at the store of that counter, with both runs' block counts.
  Not established: the runner's refusals and excuses are not checked again; a fall-through into a
  function is no call; a register or byte the callee does not read on this path parts the runs
  without consequence (the report then shows the first difference `dread test` compares, or none).
- [ ] **A frame object over the saves.** On the private real-life test one function's readable C with
  today's frame objects (1404c72) returns into a string written over its saved return address; its
  split objects and the whole-region fallback both failed its cases, so it stays lifted (nothing wrong
  accepted). Save words are found only before the first branch or call (frames' docstring): a save
  behind a branch may be one. Not traced; a public reproduction first (a prologue that saves behind a
  branch, a buffer below).
- [ ] **Calls read narrower than declared, elsewhere.** The two-word fix typed calls by their
  declarations in the conditions' proofs and in fold (2026-10-24); whether any other pass still takes
  a call for a u32 (a u64 result, a signed result since the pointer and signed types) is not checked.
- [x] **A chain of assignments that hands a pointer to an integer; the compiler's reason with the
  verdict.** On a private firmware one function's readable C did not compile: the lifter's line after a
  call stood whole in it with two of its registers structure pointers, `r1_3 = r2 = (volatile struct
  s_N *)(r3 = (volatile struct s_N *)(r12 = 0));`, the assignment to r2 a pointer's value assigned to
  a u32 (int-conversion, an error of GCC 14 and later); and the gate's line said only "its readable C
  does not compile", so a person rebuilt the file by hand to see why.
  Done 2026-10-24 (docs/decisions.md, "A chain of assignments whose variables get different types: one
  assignment each"). Who wrote it: dread.shapes apply_types, which casts every other use of a typed
  variable to u32 but rewrote the chain whole; not dread.vartypes (its Rewriter casts an assignment to
  its own pointers where an integer is needed, (u32)(r2 = ...)) nor dread.polish (it keeps that cast).
  How such a chain gets there: dread.tidy's split, which takes such chains apart, looked only at the
  body's own list, and ARM's conditional call (blne) puts the chain into an instruction's block, `if
  (!z) { r0 = f(...); r1 = r2 = r3 = r12 = 0; }`, where dead keeps it (its value has effects) and webs
  leaves r2 under the parameter's name (that the private function's chain stood in such a block is
  not confirmed: its C was not looked at here; dread.shapes' own split covers it either way). Now
  split works on every statement list but a block that binds registers and dead takes the zeroed
  registers out (gizmo a32's timer_irq: `if (r0 != -1) r0 = ring_put(0x4000007cu, r0 & 0xffu);`, the
  four registers' declarations gone); dread.shapes and
  dread.vartypes split a chain that assigns a variable they type before they type it (dread.tidy
  split_typed: innermost first, each variable 0 where that is the chain's value, else the variable
  inside it), and the plan's trials count it so (Rewriter.top: r2 and r3 read as bytes beside such a
  chain stayed u32 for its casts). The gate keeps the compiler's first error with the verdict, in the
  round's line and in verdicts.tsv: `its readable C does not compile: NAME.c:LINE:COLUMN: error: MESSAGE
  | CODE`, cut at 200 and 300 characters (dread.build compiler_error, dread.overlay OverlayError's
  errors; docs/formats.md). Public reproduction: pair_touch, an ARM-state program of our own (its
  structure pointer in r2, a conditional call), stayed lifted on the old code with `error: assignment
  to 'u32' {aka 'unsigned int'} from 'volatile struct s_1 *' makes integer from pointer without a cast
  [-Wint-conversion] | r1_1 = r2 = (volatile struct s_1 *)(r3 = r12 = 0);` and is readable now, also
  with dread.shapes' split alone. Tests: tests/test_tidy.py (the split in a conditional call's block,
  split_typed), tests/test_vartypes.py (a chain with dread.shapes' structure pointers and one with
  dread.vartypes' pointers compiled with -Werror=int-conversion and -Werror=incompatible-pointer-types,
  the chain whole failing so; pair_touch through the gate, with and without the clean-up's split),
  tests/test_readable.py (an undeclared variable planted: the verdict with the compiler's error;
  compiler_error on GCC's and clang's output). Measured on the eight builds (base -> now): the gate
  accepts the same functions name by name, its verdicts.tsv the same (gizmo a32 35, m3 36, m4 44, rv32
  31; FreeRTOS GCC -Os 317, clang -O2 275, RISC-V GCC -Os 252, GCC -O0 354); the readable C of three
  functions changed, each without the zeroed registers (gizmo a32's timer_irq, clang -O2's
  prvUnlockQueue with two conditional calls and vStreamBufferDelete with a call behind a branch), every
  other file byte for byte the same; every lifted function's readable C, those the system run accepted
  too (1,379 files), compiles with -Werror=int-conversion and -Werror=incompatible-pointer-types.
  Not established: a chain that assigns memory inside (a = b = *p = 0) is left as it is (none known);
  the split registers of a block flat_view does not spread (one that declares, labels or loops) stay,
  one statement each on the call's line.

- [x] **Regressions of 2026-10-23 on a private firmware: two-word results, callers of a function that
  fails, save areas behind a frame copy.** After the day's readable C changes the readable gate kept
  functions of the private real-life test lifted that had been readable the day before; traced to three
  causes (docs/decisions.md). (1) A callee declared u64 (two words back: a
  division's quotient and remainder) whose first word a function compares or shifts: since f6757bd
  loads_once lets a call be a value's only effect, the values pass writes r0 = f(...) for
  r0 = (u32)f(...), and fold wrote that into its one reader bare (64 bits, the remainder as the high
  word), the proofs taking every call for unsigned int. Now the proofs type a call by its declaration
  (cexpr.Ctx.result_type, the file's declarations through readable.Run.returns and the tidy run) and
  fold writes a value as its assignment converted it, the cast left out where the reader cannot see
  it or z3 proves it reads the same (tidy.as_assigned, same_reads). Where no case gave a
  remainder the gate had accepted such C, and a caller's cases found it. (2) The gate sent back every
  caller whose cases ran a failing callee for real; a failing function whose code reaches another
  that fails in the same round now gets the next round with that one from lifted C ("again" in the
  check's output). (3) A variadic function of an APCS frame (mov ip, sp; push {r1, r2, r3}; ...) was no
  save area to entry_save_areas, which looked at the first instruction only: its callers' calls were
  trimmed and push_room took its pushes out; up to SAVE_AREA_LEAD instructions that write neither sp
  nor an argument register may come first now. Public tests: tests/test_tidy.py (a call abstracted
  with its declared type; a two-word result compared and shifted, compiled beside the lifted C) and
  tests/test_readable.py (ARM assembly of our own through both gates: the comparison, a store, a
  caller tried again, the APCS save area). Not mended: written cases whose stub masks log a register
  the callee does not read (the private target's carried cases; its decision). Figures in the commit.
- [x] **Two-word results that are leftovers.** A function whose callers, or their callers through
  returns, read r1 after it returns is lifted with a u64 result; its readable C reassigns both words
  in every arm, and its callers show `(u32)(f(...) >> 32)`. When the second word is only what a callee
  left, the reader sees a function that returns a pair. Keep the behaviour but print it as a one-word
  result with the leftover handed back through a named macro of dread.h (`RETURN2(r0, r1)` or the
  like) and the reader regflow found in a comment (its `why`); in regflow, follow which bits of a
  register flow (a bic/orr that keeps the upper half of a register the caller left) so that a read of
  bits nobody uses stops counting. Public example: none found (GCC code does not read r1 after a call
  of a one-word function); test with gizmo assembly: a callee that leaves r1, a caller that passes it
  on, a reader at the top.
  Done 2026-10-24 (dread.regflow 26: Analysis.refinement, _handed, pointer_entries and made_pointers,
  solve's two views, leftovers, computed; the layers' bit_demand and store_demand; dread.tidy
  void_results, leftover_calls, leftover_returns, narrowed_pairs; dread.readable results, void_key, the
  check's results retry; dread.h RETURN2 and CALL2; docs/decisions.md, "Results as the source has them").
  regflow follows a register bit for bit, and a read counts only where its bits matter: an instruction
  that only computes registers reads what its used results need (rules per instruction, checked against
  the emulator: tests/test_regflow_bits.py, which found capstone's missing second source of uxtab and its
  kin, a read that mattered), a byte's store the low byte; what a function computes for a callee the
  analysis cannot see (a call through a pointer, a variadic function's unnamed arguments) stays read. The
  comparison is the same: r0 at every return, and beyond it what some caller's compared outputs depend on
  (the comparison's view, which passes every function's result on at its returns); the result registers
  as the program reads them (callers through a pointer counted: a function whose entry a word of the
  image holds or its code makes, also by a lui and an addi apart). A second word that holds only a
  constant or what a leftover callee left (leftover) prints one-word through RETURN2, its readers through
  CALL2; a division's remainder stays u64. A function nobody reads the result of prints void, unless its
  own code computes the result for the return alone (computed: a strlen nobody reads keeps it, as its
  source); one whose void C the cases reject (it leaves another value in r0, which they compare) is tried
  once more with its result (the readable_types table's results; the table made anew then, so that such a
  function keeps its parameters' types). Public form fixed: gizmo's timer_start and delay print `void
  timer_start(u32 r0)` and `void delay(u32 r0) { timer_start(r0); ... }`. The program of our own with a
  leaf that leaves 0x55 in r1 printed `u64 leaf(u32 r0)` with `return ((u64)0x55u << 32) | (r0 + 1);`,
  `u64 mid`, `u32 top(u32 r0)` with the high word in the byte it stores and `return sink((u32)t_1,
  (u32)(t_1 >> 32));` in relay; where no caller reads the whole word, `u32 leaf(u32 r0)` with `return r0
  + 1;`, `void top(u32 r0)` storing `mid(r0) & 0xffu`, relay handing mid's one word to sink, and where
  one does, `return RETURN2(r0 + 1, 0x55u);` with the reader named. Figures (eight builds; before the
  parent commit, after this change): the lift check and the readable gate accept the same functions on
  every build (gizmo a32 36 lifted and 35 readable, m3 36 and 36, m4 44 and 44, rv32 32 and 31; FreeRTOS
  mps2 GCC -Os 341 and 328, clang -O2 300 and 288, virt RISC-V GCC -Os 272 and 265, mps2 GCC -O0 382 and
  378), the whole image agreeing in both. Printed void, none before: gizmo a32 18 of its 51 functions (15
  of the 35 readable), m3 21 of 50 (18 of 36), m4 22 of 58 (19 of 44), rv32 18 of 46 (15 of 31); GCC -Os
  102 of 362 (95 of 328), clang -O2 83 of 339 (74 of 288), RISC-V 90 of 312 (83 of 265), GCC -O0 150 of
  433 (125 of 378). Against FreeRTOS's naming convention (v: void in the source; x, ux, pv and their kin:
  a value): of the readable functions with a value's prefix, 4 print void on GCC -Os, 2 on clang -O2, 3
  on RISC-V and 3 on -O0, all void in their source (xPortSysTickHandler, xNotifyTaskFromISR,
  xNotifyArrayTaskFromISR) but xTaskCatchUpTicks, whose result nobody reads and whose value is
  xTaskResumeAll's; of those prefixed v, 59 of 83 print void on GCC -Os, 54 of 81 on clang -O2, 56 of 73
  on RISC-V, 55 of 82 on -O0, the others with a result: tasks and callbacks (callers through a pointer
  count as reading), what they hand on through tail calls, and the gate's retries. u64 results and `>>
  32`: the same on every build (gizmo a32's __aeabi_uidivmod stays u64, its remainder real; what `>> 32`
  remains is 64-bit arithmetic, umull and adc); leftovers: none on these builds (the false pairs were a
  private firmware's). regflow: kept beyond the result registers the same on all eight; kept r0 grew
  (callers through a pointer: +4 on each gizmo ARM build, +1 on rv32, +104 GCC -Os, +101 clang -O2, +88
  RISC-V, +105 GCC -O0) and fell once (GCC -O0's tiny_print, which printf and sprintf hand on as theirs,
  read by nobody); reads dropped: newlib's _read_r, _write_r, _lseek_r, _close_r, __sread and __sseek
  (r1-r3, 14 a build, on each mps2 build: stubs that read no argument) and xQueueCreateMutex's r0 on GCC
  -Os, every one measured by dread callconv: all agree (8 of 8 functions on GCC -Os, 6 of 6 on clang -O2,
  7 of 7 on -O0 with tiny_print). Case keys regenerated (the case facts hold kept): gizmo a32 8 of 56
  functions, m3 9 of 54, m4 9 of 61, rv32 1 of 50; GCC -Os 277 of 362, clang -O2 264 of 339, RISC-V 246
  of 308, GCC -O0 342 of 439. Gate rounds: 1 to 3 (gizmo a32, m3, GCC -Os), 1 to 2 (m4, rv32, RISC-V), 2
  to 4 (clang -O2), 1 to 4 (GCC -O0). Times (warm, the builds one after another on a machine shared with
  other work): the readable gate takes two to three times as long (gizmo a32 5 to 25 s, m3 5 to 12, m4 5
  to 8, rv32 3 to 5; GCC -Os 38 to 80 s, clang -O2 58 to 144, RISC-V 26 to 38, GCC -O0 49 to 166): more
  rounds, and a round that gives results back makes the readable_types table and every function's
  readable C anew (14 s and 8 s on clang -O2: the open item below); the lift check and the printing take
  as long as before (GCC -Os 72 and 69 s, 61 and 62 s; clang -O2 79 and 78 s, 79 and 90 s; the first lift
  check after a change of the code is slower, caches are keyed by it); regflow's analysis about a quarter
  longer, its two solutions (GCC -Os 2.9 to 3.6 s, clang -O2 3.8 to 4.9, RISC-V 1.1 to 1.5, -O0 5.7 to
  7.0). Tests: tests/test_regflow_bits.py (every bit rule and the narrow stores against the emulator on
  ARM state, Thumb-2 and RISC-V; an assembly program of our own: a callee that leaves a constant in r1, a
  caller that hands it on, callers that keep its upper bits and store a byte, a reader at the top that
  stores it whole, a division whose remainder is a result, a callback and its caller; computed on
  gizmo-m3), tests/test_results.py (that program's readable C and both gates on ARM and RISC-V; a wrong
  RETURN2 caught by the cases; gizmo's timer_start and delay void on every build; a void function the
  cases reject kept with its result and its types, a computed result kept; a pointer made by a lui and an
  addi apart; a void callee of a body behind a naked entry), tests/test_regflow.py (the cache key holds
  the code a process imported: a suite that had imported the old code stored its analysis under the key
  of an edit made meanwhile). Not established: a real 64-bit result that is a constant on every path
  counts as a leftover; an accumulator in r0 is no computed result (the gate's retry decides); a pointer
  made by arithmetic the walk does not follow; a function whose entry the image holds keeps its result
  whether its callers through the pointer read it or not; MSP430 and MIPS keep every read (no
  register_only) and print no void (their heads are not the lifters' u32 form).
- [x] **A cheaper round for results given back.** dread readable --check makes the readable_types table
  anew after a round that gave functions their results back (it keeps each function's types for the
  digest of its C, and the first table was made for the void forms), and with it every function's
  readable C, which readable_texts keys by the whole context: 14 s and 8 s a round on FreeRTOS's clang
  -O2 demo, whose gate went from 58 s to 144 s with that and two more rounds. Make only the functions
  whose void status changed and their callers anew (their analysis trees, their entries of the table), or
  key each function's readable C by the entries it reads. Public example: FreeRTOS mps2-clang-O2.
  Done 2026-10-24 (dread.readable scoped, scope_key, plan_key, tables_key, stage_view, kept_trees,
  cleaned_tree, from_cleaned, readable_texts' kept and verify, the check's verify and summary line,
  `dread readable --check --verify`; docs/decisions.md, "Readable C kept by what it is made of").
  Profiled first, timers around the gate's parts (main 3186afb, eight workers): a round that gave results
  back made the table from trees made anew (the clean-up of every lifted function, and of the callees
  outside the lifted table whose constants the masks look at) and then every function's readable C
  (its clean-up again): on clang -O2 16.7-17.3 s and 7.5-8.0 s a round (the trees 8.8-9.4 s, the
  masks' trees 3.5-4.5 s, the analyses 4.5 s: vartypes 1.6-2.0, static data 1.6-1.9 with the machine
  code's evidence gathered again, shapes, pointer parameters, kept parameters 0.7), of a round's 30 s;
  of the gate's 148 s, 71 s tables and 31 s readable C. Now a function's readable C is made from a view
  of the context (scoped) that holds of the table's parts kept per function only the entries of the
  names its lifted C uses and which of them are void, the rest whole, and kept by exactly what the view
  holds: a round makes anew only the C of the functions given back and of those that name them. The
  check keeps the analysis trees, their cleaned trees (the stages run with a view that holds of what
  changes only whether the function is void) and the evidence, and makes readable C from a kept cleaned
  tree without the clean-up. A later round on clang -O2: the table 3.7-5.0 s (the analyses 3.5 s:
  vartypes 1.6-1.9, static data 0.75, the rest 0.7; the given functions' trees up to 1.5 s), the readable
  C 0.1-0.7 s. Seconds (rounds; main -> this, every gate on a fresh copy of the same lifted table and
  cases, main and this one after the other, eight workers, the machine shared with other work):
      build (rounds)                gate             tables          readable C    the later tables
      gizmo a32 (3)                  27.5 ->  16.1     7.5 ->  3.3    6.8 -> 0.2   2.6/2.4 -> 0.2/0.3
      gizmo m3 (3)                   14.3 ->   3.9     6.1 ->  2.4    6.3 -> 0.1   1.9/1.9 -> 0.2/0.3
      gizmo m4 (2)                    9.3 ->   3.7     4.2 ->  2.4    3.7 -> 0.1   2.0 -> 0.3
      gizmo rv32 (2)                  6.4 ->   2.7     2.8 ->  1.7    2.5 -> 0.2   1.4 -> 0.3
      FreeRTOS GCC -Os (3)           92.7 ->  55.9    38.1 -> 26.8   20.5 -> 1.2   12.2/12.5 -> 4.5/5.7
      FreeRTOS clang -O2 (4)        148.4 ->  72.4    70.8 -> 33.8   30.8 -> 1.6   17.3/16.9/16.7 -> 5.0/3.7/3.8
      FreeRTOS RISC-V GCC -Os (2)    48.1 ->  30.1    22.2 -> 13.8    9.0 -> 0.5   10.3 -> 3.9
      FreeRTOS GCC -O0 (4)          172.4 -> 109.5    63.3 -> 39.8   33.2 -> 1.6   15.2/14.9/14.7 -> 6.6/4.2/5.1
  (The table is of the code before its last step, which runs the stages with a view of their own,
  stage_view: the same work. With the final code, the test suite running beside: gizmo 27.4 -> 15.9,
  12.9 -> 4.2, 9.7 -> 3.9, 6.8 -> 2.9; FreeRTOS GCC -Os 101.9 -> 58.7, clang -O2 157.2 -> 83.0, RISC-V
  GCC -Os 38.8 -> 26.1, GCC -O0 149.1 -> 94.3; the later tables on clang -O2 17.2-18.5 -> 4.1-5.4 s.)
  The first table takes longer than main's (GCC -Os 13.5 -> 16.6 s, clang -O2 19.9 -> 21.4, -O0 18.5 ->
  23.8): it lifts every function in the check's process, which main did right after it (outside the
  timers there), and makes the cleaned trees the first pass then takes (6.4 -> 0.5 s on GCC -Os). Byte
  for byte: on all eight builds the gate's readable and readable_types tables, its verdicts, every
  round's runner output, the last build's files and image, and every function's printed readable C
  (`dread readable --out`: 51, 50, 58, 46 and 363, 340, 313, 434 files) are main's; `dread readable
  --check --verify` (every round's table against one made anew from nothing, every readable C the
  builds take against the one made anew from the whole context, the frame's objects of the functions
  it asks about; the carry's question trees where the readable_names table has entries made for other
  C, none on these builds: tests/test_carry.py) passes on all eight. The builds asked for readable C 175, 180, 176, 124 and 1640,
  1728, 1060, 2268 times and made it 46, 47, 55, 38 and 372, 326, 267, 452 times, every one from a
  cleaned tree; 40, 40, 49, 37 and 348, 316, 276, 399 analysis trees for 3, 3, 2, 2 and 3, 4, 2, 4
  tables. Tests (tests/test_readable.py, tests/test_carry.py): readable_texts makes anew only the
  functions whose entries changed (a function given its result back and the function that names it, a
  callee's entry, a structure every function) and each time what remade makes from the whole context;
  the gate on gizmo-m3 with --verify (trees made anew only for the functions given back, the later
  builds' readable C fewer than the functions); --verify finds a view cut to the function's own name;
  the carry's question trees as the whole context gives them. Left: the analyses over all trees in every
  such round (vartypes.plan about half: its copies of every tree with the static data and structures
  applied, and its planner, which rebuilds the prototypes of every function for each decision, quadratic
  in the functions: not measured on a large target); the first table's lifting in one process (7.9 s for
  GCC -Os's 341 functions).
- [x] **Bound calls in one line.** A call whose callee reads registers its caller left, or behind
  which the caller reads scratch registers, prints as an inline-asm block that binds every register,
  plus asm reads of those registers at the function's entry: many lines for one call. Print it as one
  statement through a dread.h macro that names the registers it hands over, and let a target table
  mark a register read as unreachable (a report on a path that cannot run), so that the call is a
  plain call there. Public example: none found (GCC code does not read registers its caller left);
  test: gizmo assembly.
  The statement done 2026-10-25 (dread/bound.py, dread.h's BOUND, BOUND_R12, Rn, Rn_IN, Rn_IN_OUT): the
  lifter's block (register variables, the asm's bl, the write-backs) as `BOUND(f, R0(a), R1(b), R2_IN(5),
  R5_IN_OUT(c, d));` behind dread.polish, only where the block is the lifter's as it writes it
  (no flags read behind the call, no stack arguments, no FPU registers, plain write-backs: else the block
  stays); what followed the write-backs (r12 = 0) follows. The macros make the same instructions as the
  block (tests/test_bound.py compiles both with gizmo a32's compiler). ARM only: RISC-V's bound calls
  clobber what their callee writes, a list no one macro holds. The public builds have none; on the
  private real-life test they are frequent, nine lines each before. Left: a target table that marks a
  register read as unreachable, so that such a call is a plain call; the entry's reads of registers as
  left (`__asm__ volatile ("mov %0, r7" : "=r"(saved))`), one line each already.
- [x] **No address for a plain value.** The pass that prints `(u32)&object` for constants that are
  the address of static data also replaces a constant compared with a masked word, a bit-field value,
  by the address of an object that happens to lie at that number. Substitute an address only where
  the constant is used as one (dereferenced, passed where a callee dereferences it, stored into a
  pointer, compared with a pointer), never where it meets the result of a mask or a shift, and not for
  objects outside the image.
  Done 2026-10-23 (dread.naming apply_globals' as_address with _bit_variables, _address_variables,
  _loaded_variables and _straight_values; dread.shapes pointer_params and the readable_types table's
  `params`; dread.statics image_spans; docs/decisions.md, as above). A constant is an object's address
  only as an argument for a parameter the callee uses as a pointer (the shapes' param_used and
  templates, and readable C that adds the parameter whole to an access's address, which the shapes do
  not follow through a sum of two variables: memcmp's `a0 + a4`; a callee not analysed as before), as
  a value assigned to a variable the function uses as an address or stored into memory, as a result,
  and as an operand of == or != whose other operand is a variable used as an address or holding a word
  read from memory (all its values, or the one it holds there), a word read from memory or a call's
  result; never where the other operand is a mask's, a shift's or a narrowing cast's result or a
  variable given one, and never for an object outside the image's bytes and its startup's ranges. An
  element's address the code computes (a constant plus a scaled index) is one wherever it is used.
  Tests (tests/test_statics.py): a unit test with a static object at 0x400 (masked, shifted and narrow
  compares, a parameter used as a number, arithmetic, a count for a callee that takes none, a variable
  holding a word and then a halfword, the image rule); the gauge program on a Cortex-M3 with its RAM
  at 0x400 through the gate: main wrote `if ((r0 & 0xf00u) == (u32)&g_400[0])`, `(u32)&g_400[0].f_4 ==
  r0 >> 4` and `gauge_spin((u32)&g_400[0].f_4)` for a count; they are numbers now, while gauge_link's,
  gauge_same's (bytes at it plus an index) and the cursor's compare keep their addresses. Measured
  (main -> now, every function's readable C): GCC -Os 80 -> 78 addresses, clang -O2 96 -> 104, RISC-V
  GCC -Os 63 -> 63, GCC -O0 74 -> 71, gizmo none; in the readable functions (dread.cmetrics
  fixed_addresses) 61 -> 59, 75 -> 80, 36 -> 36, 52 -> 49. Gone: xTaskCreate's pvParameters (GCC -Os,
  clang -O2: xTaskCreate only stores it), values passed only to vPortExitCritical and xTaskResumeAll,
  which take none (GCC -O0 three, GCC -Os one: the phantom arguments' item); come: xQueueRegistry's
  fields (its structure grew, above) and xTaskGetHandle's list ends. pointer_params takes 0.2 s on GCC
  -Os. Left: whether a store or a result holds an address (both as before: the field's or result's
  type is not known); a word read from memory and compared with an object's address may be a number (a
  state word); an index that readable C adds to an address whole makes its parameter a pointer
  (memset's count); a parameter only stored is none (pvParameters).
- [x] **Pointer types for parameters and prototypes.** A parameter that is only dereferenced (and
  advanced) stays u32 with a `*(volatile T *)p` cast at every use, and arguments to callees whose
  extern DREAD writes as all-u32 get `(u32)&x`. Type a parameter or variable dereferenced at one width
  as a pointer to that width (`const s8 *` for signed byte loads only), from a callee parameter's
  shape where there is one, and write extern prototypes with the types the callee's own readable C
  has. Public examples: gizmo a32 strncpy, read_line, parse_kv (char pointers as u32); FreeRTOS
  mps2-gcc-Os vStartTimerDemoTask (`memset((u32)&g_200017c5[0], 0, 0x15u)` under `extern u32
  memset(u32, u32, u32)`).
  Done 2026-10-23 with the item "Signed types from signed use" (dread.vartypes; the readable_types
  table's `variables` and `prototypes`; dread.readable check, _named and _question_one; dread.build
  WRAPS; dread.polish's pointer casts; dread.cmetrics `pointers` and `signed`; docs/decisions.md,
  "Pointer and signed types for variables and parameters; externs typed where a file's calls fit
  them"). Variables that copy one another or add a constant or an index to one another are one group
  and get one type: a pointer to the width their accesses have (`volatile u8 *`, `const` where nothing
  is stored through them, s8/s16 where every access is a signed load), a structure pointer where they
  only go to a callee parameter dread.shapes types so; each kept where the function's C holds no more
  casts with it (a trial). Accesses are `p[k]` and `*p`, pointer arithmetic counts elements (+ 4 on a
  u32 pointer is + 1, a shifted index the index; dread.shapes' advance of a structure pointer by its
  size `(volatile struct s *)((u32)p + 12)` is p + 1); a pointer used as a number is (u32)p. Volatile
  stays on every typed pointer (the Memory item below stays open). Externs: a callee's parameter has
  the callee's type in a file where the calls pass what C takes for it without a cast (`&g_x[0]`,
  `&r0->f_4`, a typed pointer, 0), else u32 as the lifter wrote it: the callee's type in every extern
  added a cast at every call that passes a number (FreeRTOS GCC -Os: casts with raw accesses 1,250 ->
  1,588 that way, a queue handle a u32 global holds, a text's address). strncpy: `u32 strncpy(volatile
  u8 *r0, const volatile u8 *r1, u32 r2)`, `r12 = r1_1[1]; r1_1++;`, `*r3 = r12; r3++;`; read_line:
  `s32 read_line(volatile u8 *r0, u32 r1)`, `r0[r4] = r0_1;`; parse_kv: `u32 parse_kv(const volatile
  u8 *r0, volatile u8 *r1, u32 r2, u32 r3)`; vStartTimerDemoTask: `memset(&g_200017c5[0], 0, 0x15u);`
  under `extern u32 memset(volatile u8 *, u32, u32);`, its timer array walked as `volatile u32 *r5,
  *w_1` (`w_1 = r5 + 1; *r5 = r0_1;`, `w_1[-1]`). Measured on copies of the eight builds (gizmo a32,
  m3, m4, rv32; FreeRTOS GCC -Os, clang -O2, RISC-V GCC -Os, GCC -O0; main -> this): the gate accepts
  the same functions, name by name (35, 36, 44, 31, 315, 275, 252, 354), in the same time (warm
  caches, main run again on the same copies: 299 -> 308 s on GCC -Os, 49 -> 48, 20 -> 21, 80 -> 72;
  noise); over every lifted function's readable C, parameters typed as pointers to scalars 0 -> 14,
  14, 15, 15, 63, 57, 47, 58, such variables 0 -> 22, 17, 17, 19, 61, 35, 25, 32, structure pointer
  parameters 70 -> 86, 53 -> 57, 57 -> 66, 63 -> 71 on FreeRTOS (handed on to callees that take one),
  raw accesses through variables 33, 34, 35, 35, 206, 248, 147, 212 -> 6, 13, 13, 3, 64, 94, 72, 100,
  casts with raw accesses 75, 67, 71, 69, 1,250, 1,088, 714, 999 -> 46, 43, 47, 40, 1,055, 891, 596,
  783, casts at uses (raw accesses through variables, (u32) and (s32) of a variable, pointer casts)
  48, 48, 52, 43, 681, 642, 443, 573 -> 19, 24, 28, 13, 539, 501, 345, 453, externs with a type 1, 0,
  0, 0, 0, 0, 0, 0 -> 4, 3, 3, 2, 115, 58, 94, 68; lines 0.5-2 % more (a declaration line per type).
  The structures' scores against the source (dread readable --shapes --score, --statics --score,
  --score) the same, line for line. The plan takes 1.4-1.7 s a FreeRTOS build; every GCC -Os
  function's readable C in one process 43.8 -> 48.8 s of CPU.
  Pointer results done 2026-10-26 (vartypes decide_pointer_result): a function every return of which
  gives one pointer type (a variable typed or declared so, dread.shapes' structure pointers among them
  as their (u32)r0 returns, a call of a callee whose result is one, 0) returns that type where its
  returns and its callers' C hold no more casts so, checked once more with the callers' final types
  after the rounds (a caller typed later may use it as a number: RISC-V GCC -O2's memset, compared with
  u32 variables); a head's result may be several words (head_match, head_text: naming renamed
  parameters by a one-word head's pattern). On the nine builds (copies of the fault branch's results,
  against the void branch) the gate accepts the same functions; in the lifted functions `return (u32)`
  20 -> 16 (GCC -Os), 36 -> 32 (clang -O2), 17 -> 14 (RISC-V GCC -Os), 15 -> 10 (GCC -O0), 16 -> 11
  (RISC-V GCC -O2), gizmo 2-4 -> 0, lines with a (u32) never more (636 -> 634, 661 -> 658, 535 -> 534,
  750 -> 748, 638 -> 635). The functions outside the lifted table, printed on request, are not in the
  plan: RISC-V GCC -O2's three more lines there are calls of memset's pointer result. Left: a pointer
  plus an offset returned (pvPortMalloc's block + its header) stays u32; globals that hold pointers
  (a queue handle in a u32 global: its calls keep the externs' u32, and the pointer casts it would
  need are why); an access past a structure through its pointer stays raw (`r0[2].f_0` would need the
  field's name); arrays of structures dread.shapes makes no structure of are walked as words (spool's
  cells on ARM: `w_1 = r3 + 3`, `w_1[-1]`); the const of a parameter only handed on follows the
  callees' types, not the source.
- [ ] **Memory (the open item), more evidence.** On a private firmware volatile on RAM showed in the
  readable C beyond the raw accesses: dead loads printed as `(void)g;`, loads kept in temporaries to
  keep their order, constants that do not fold through a volatile local array, and the naming model
  reading plain pointers and buffers as device registers. Public examples: FreeRTOS mps2-gcc-Os
  vTaskSwitchContext (`(void)g_20029aac;`, a dead RAM load); the frame structures of gizmo a32
  print_status and FreeRTOS xTimerGenericCommandFromTask are declared volatile. A first step that
  keeps the item's concern (a wait for a flag an interrupt sets): no volatile on frame objects, and
  none in what the naming question shows. The pointers dread.vartypes types (2026-10-23: `volatile u8
  *p`, `const volatile u16 *q`) are volatile-qualified as dread.shapes' structure pointers are, every
  access through them the one volatile access it was: this item decides for them too. The frame's
  objects: done 2026-10-23 with "Local objects whose address goes to a callee" (volatile only where an
  address may escape into memory: dread.frames kept_params; print_status's and
  xTimerGenericCommandFromTask's are plain now).
- [x] **Arrays and members for uniform runs and interior pointers.** An object accessed at constant
  offsets of one element type prints as a structure of numbered fields and padding, walked by integer
  pointer arithmetic; an access through `p + k` stays raw where the structure has a member at k; the
  address of an embedded structure is `(u32)((u64)(u32)p + 4 + 0)`. Make a run of same-type fields at
  a fixed stride that code walks with a pointer an array, print `p->member` and `&p->member` for
  derived pointers, and an embedded structure as a member of its type. Public examples: FreeRTOS
  mps2-gcc-Os vStartTimerDemoTask (xAutoReloadTimers, an array of 21 handles, as `struct
  g_200017dc_t { u32 f_0; u8 pad_4[72]; u32 f_4c; u32 f_50; }`, filled by `*(volatile u32 *)r5 =
  r0_1; r5 = w_1;`); xTimerCreate and prvProcessExpiredTimer
  (`vListInitialiseItem((u32)((u64)(u32)r0_1 + 4 + 0))` for `&pxNewTimer->xTimerListItem`).
  Done 2026-10-24 (docs/decisions.md, "Arrays a pointer walks, members for derived pointers, a callee
  that reaches one field past its pointer"). Walks (dread.statics walks): a variable of a lifted
  function's readable C given one address of static data and otherwise only stepped by one constant of
  two bytes or more is a pointer that walks an array; its accesses count as indexed ones at its start
  with its stride as the scale (indexed_accesses), its address handed to a callee as an element's
  (handed: pxReadyTasksLists' elements take vListInitialise's five fields), only where the start is a
  base that counts, tried with the end its structure would have (the arrays' trust used the base's
  whole reach, which on RISC-V GCC covered the variables behind the array). vStartTimerDemoTask now
  reads `r5 = &g_200017dc[0];` ... `*r5++ = r0_1;` ... `g_200017dc[20] = r0_2;`, `#define g_200017dc
  ((volatile u32 *)0x200017dcu)`. Members for derived pointers (dread.shapes apply_types): a typed
  variable plus a field's offset is the field's address (`r0->f_c = (u32)&r0->f_8;` in vListInitialise,
  `(u32)&r0->f_4.f_8` inside a held structure), against a constant the sum as before; an access of
  another width at a field's place goes through the field's address (`*(volatile u8 *)&p->f_8`). A
  callee that reaches one field past its pointer and none in front of it takes one kind of structure,
  as one that reaches two did (shapes.structure): vListInitialiseItem takes items, the timer xTimerCreate
  hands it at 4 holds one, `vListInitialiseItem(&r0_1->f_4);` under `void vListInitialiseItem(volatile
  struct s_9 *r0) { r0->f_10 = 0; }`; getters of one field take their structure (`uxQueueMessagesWaiting`:
  `r0->f_38` for `r0[14]`), and the queue's set container at 0x48 is a pointer to a queue, as the
  source's. A value read through a stack argument's address (`r4 = *(volatile u32 *)&s2`) may be typed
  (shapes._uses): prvInitialiseNewTask's and xTimerCreateStatic's task block and timer print as members.
  dread.polish takes an element's index for a number (vTaskSwitchContext's `31 - clz`).
  Measured on the eight builds (main -> now; dread.cmetrics over the gate's readable functions, and their
  printed C): the gate accepts the same functions, name by name (gizmo a32 35, m3 36, m4 44, rv32 31;
  FreeRTOS GCC -Os 317, clang -O2 275, RISC-V GCC -Os 252, GCC -O0 354); raw accesses GCC -Os 78 -> 61,
  clang -O2 105 -> 71, RISC-V GCC -Os 68 -> 67, GCC -O0 107 -> 90 (with a constant offset, `*(volatile
  T *)(x + k)`: 62 -> 47, 66 -> 35, 58 -> 57, 54 -> 40); members 711 -> 747, 689 -> 742, 439 -> 452, 700 ->
  737; members' addresses `&p->` 35 -> 46, 32 -> 44, 27 -> 35, 36 -> 47, a typed pointer plus a constant
  as an integer `(u32)p + k` 26 -> 19, 22 -> 13, 11 -> 3, 18 -> 8; typed variables 242 -> 253, 201 -> 214,
  165 -> 173, 266 -> 290 (none lost); casts 810 -> 800, 790 -> 798, 521 -> 513, 731 -> 747 (up where a
  variable typed now is read as an integer: clang -O2's vQueueDelete compares its queue with the
  registry's handles, `(u32)r0` ten times; GCC -O0 rereads its stack arguments, a cast each time). The
  structures with numbered runs that became arrays: xAutoReloadTimers on GCC -Os; on the 39 builds
  (dread readable --statics --score) structures 558 -> 547, arrays 571 -> 583, every new one inside one
  source variable with its element size, their fields 1602 -> 1859, 1591 -> 1848 the source's. Functions
  whose readable C changed: 35, 58, 41 and 85, of them 6, 37, 17 and 59 only by the structures' numbers
  (s_N): structures 50 -> 49, 54 -> 50, 28 -> 26, 46 -> 47, kinds the weak holds joined; names by anchor
  and offset (readable_names' structs part) keep every field (293, 450, 235, 269); xAutoReloadTimers'
  field names (keyed by offset) no longer apply, its object's does. Against the source (dread readable
  --score): pointer fields the source's pointers to a structure 20 of 23 -> 21 of 25, 45 of 55 -> 31 of
  41, 33 of 33 -> 22 of 22, 13 of 17 -> 14 of 19 (fewer counted where duplicate structures became one:
  source structures in 23 -> 21 and 14 -> 12 of ours on clang -O2 and RISC-V, 12 -> 10 and 7 -> 5 beyond
  one each), the typed parameters' fields still 97-99 % the source's, every held member where the source
  holds one. The plan 10.9 -> 11.4 s, 14.0 -> 14.6, 8.7 -> 8.7, 12.2 -> 12.1; the readable C of every
  lifted function 5.7 -> 5.7, 6.0 -> 6.4, 4.4 -> 4.2, 5.7 -> 5.6 s (eight workers, best of two, a shared
  machine). Left: arrays inside structures behind pointers (a task block's ulNotifiedValue[] indexed,
  `*(volatile u32 *)((u32)r0 + ((r1 + 18) << 2))`, its pcTaskName walked from `(u32)r4 + 0x33u`);
  links that point to items or to a list's end marker stay integers, and the raw accesses through them
  (`*(volatile u32 *)(r2_1 + 4)`); walks downwards or from two addresses; static data that is one of
  dread.shapes' structures keeps a type of its own (FreeRTOS's lists: 24-43 casts a build of their
  addresses to the list's structure pointer, `(volatile struct s_24 *)&g_20029988`).
- [ ] **Structures that share a header stay apart.** Structures that begin with the same link (a list
  node at offset 0) and pass through the same list functions merge into one type with each one's
  fields at their offsets; an access to one's field that falls inside another's member prints raw.
  Unify through a callee's parameter only over what the callee reaches (the header), and keep the
  containers apart. Public example: none found (FreeRTOS's lists are members at nonzero offsets);
  test: gizmo with two structures that start with the same link and a list function taking either.
- [x] **One name per static object.** A static block's structure can end early and its later fields
  print as loose macros, and an address read and written at two signednesses gets two names. Extend
  a structure over the fields the code reaches from the same base (a data symbol inside such a run a
  member, not an end), and give a field one name with a cast for the other width or signedness.
  Public example: FreeRTOS mps2-gcc-Os prvSemaphoreTest (`g_200018dc_u16 = (u16)(g_200018dc_s16 +
  1);` for `sNextCheckVariable++`). The early end: none found.
  Done 2026-10-24 but for the data symbols (docs/decisions.md, "One name per static object"). The early
  end was found: an address handed out inside a structure ended it (countsem.c's xParameters, whose
  second block xTaskCreate gets, on 31 of the 39 benchmark builds; newlib's __sf on six, clang -O2's
  xQueueRegistry). A structure now goes on over an address handed out and another base where its base
  reaches past them (dread.statics REACH_PAST), whether the base counts still asked of its run up to the
  next start; one whose fields recur at the stride of the addresses handed out inside it is an array:
  vStartCountingSemaphoreTasks reads `g_20001858[1].f_0 = r0; ...` and `vQueueAddToRegistry(
  g_20001858[1].f_0, ...)` where it read `g_20001864 = r0; ...` with three names of their own. Data
  symbols still end a structure: as members, on the 39 builds 43 structures lay over several source
  variables (clang's merged globals on RISC-V clang -Os, newlib's malloc state); right only for a
  person's partial symbols, which would need the target to say so (left). One name per address and
  width: a place is signed where an access of its size is (dread.statics signed_type, in both layouts:
  the lifters write stores unsigned), the readable_types table lists the scalars read signed (`signed`),
  and memory at a fixed address has one name at a width, a read of the other signedness cast to it, a
  store as it is: prvSemaphoreTest reads `g_200018dc_s16++;`, prvUnlockQueue `r5 = r0->f_45; ...
  r0->f_45 = -1;` (the queue's cTxLock, int8_t, was `(s8)r0->f_45`, `0xffu`); a store of the other
  signedness into a field of static data goes into it, an access of another width at a field's place
  through its address (`*(volatile u8 *)&g_x.f_4`, before a name of its own).
  Measured on the eight builds (before -> now, after the arrays and members above; dread.cmetrics over
  the gate's readable functions, and their printed C): the gate accepts the same functions, name by name
  (gizmo a32 35, m3 36, m4 44, rv32 31; FreeRTOS GCC -Os 317, clang -O2 275, RISC-V GCC -Os 252, GCC -O0
  354); addresses with names of two signednesses 1 -> 0 on GCC -Os, clang -O2 and RISC-V GCC -Os
  (sNextCheckVariable), none on GCC -O0 (only in a function the gate leaves lifted); second names of a
  static object's places 0 -> 0 (none on these builds, but a structure's word read through a halfword
  field's address in the tests); addresses with a macro of their own 242 -> 239, 272 -> 267, 214 -> 214,
  252 -> 249; uses of such macros 1260 -> 1252, 1406 -> 1395, 950 -> 949, 1157 -> 1150; members of
  static data 108 -> 115, 160 -> 171, 22 -> 22, 95 -> 102; casts 800 -> 785, 798 -> 796, 513 -> 500, 747
  -> 739 (the signed fields); raw accesses as before (61, 71, 67, 90). Structures of static data that
  became arrays: xParameters (GCC -Os, clang -O2, GCC -O0) and clang -O2's xQueueRegistry (10 entries of
  8 bytes, the source's); the source's structures and arrays reached at two places or more now all
  grouped whole on GCC -Os (15 -> 16 of 16), clang -O2 (11 -> 13 of 14, none in part) and GCC -O0 (5 ->
  6 of 6). On the 39 builds (dread readable --statics --score) structures 547 -> 510, arrays 583 -> 621,
  none over several source variables, every array inside one with its element size (572 -> 610), the
  source's structures and arrays grouped whole 329 -> 368 and in part 89 -> 51, fields that are the
  source's 3507 + 1848 -> 3333 + 2270 (the 12 more wrong are __sf's FILE word, _flags and _file stored
  as one, in two more elements). Functions whose readable C changed: 16, 13, 10 and 14; no structure
  renumbered, every field kept (names by anchor and offset); the structures that became arrays keep
  their object's name, their first element's field names apply by offset, the others' not. The plan and
  the readable C take as long as before (10.1- 12.7 s and 3.0-5.5 s a build, within the noise of a
  shared machine). Left: data symbols inside a base's run, as members where the target's symbols are
  partial (not built: no target says so); a structure reached from several bases that do not reach over
  one another still comes out as pieces.
- [x] **Negative constants by use.** An mvn immediate prints as `~0xNu` also where the value is a
  negative number. Print `-(N+1)` where it is returned, compared, stored or passed as a value, and
  `~N` where it is a mask (and, bic, eor). Public examples: FreeRTOS mps2-gcc-Os
  xTimerPendFunctionCallFromISR (`stack24.f_0 = ~0x1u;` for tmrCOMMAND_EXECUTE_CALLBACK_FROM_ISR,
  -2); gizmo a32 parse_kv (`~0x0u`, `~0x1u`, `~0x2u` for -1, -2, -3), read_line, cmd_mode.
  Done 2026-10-23 with the three items below in one pass (dread.polish, the last of readable C:
  readable._named after dread.frames, and the naming questions after their digest; docs/decisions.md):
  what must stay the same follows the use (a conversion: the converted value; an operation's operand or
  a select's arm: the common type and the value in it; a comparison's operand: the set of the other
  operand's values for which it holds, computed exactly as intervals; a shift's count: the value).
  Negatives where a value is one: assigned, stored, returned, passed to a prototyped parameter, a
  converted select's arm, compared for equality, compared in order only where the comparison is signed,
  added (x + 0xfffffff8u is x - 8); ~N stays in masks (an operand of &, |, ^, ~, a variable only those
  read), an unsigned byte (0xff), unsigned orders (x < -2 would read wrong) and the compare idiom. The
  forms named: `stack24.f_0 = -2;`, parse_kv's `= -1;`, `= -2;`, `parse_uint(r0_2, r3) == -1 ? -3 : 0;`,
  read_line's `= -1;`, cmd_mode's `return !c || z ? 0 : -1;`. Measured on copies of the eight builds
  (gizmo a32, m3, m4, rv32; FreeRTOS mps2-gcc-Os, mps2-clang-O2, virt-gcc-Os, mps2-gcc-O0; their
  readable functions, main's code -> this): the gate accepts the same functions (35, 36, 44, 31, 315,
  275, 252, 354; the same readable tables and readable_types plans); its time 5.2, 5.0, 5.4, 5.2, 776,
  355, 210, 596 s -> 3.8, 3.5, 4.0, 2.4, 573, 247, 129, 586 s, the whole image's halving most of it and
  the machine shared (the pass itself: 0.39 s of the 44 s every GCC -Os function's readable C takes in
  one process); lines the same, characters 1.1-1.9 % fewer (27,603 -> 27,106 on a32, 546,299 -> 539,258
  on GCC -Os). `~0x` 12, 5, 5, 0, 29, 28, 0, 18 -> 4, 3, 3, 0, 25, 24, 0, 15 (masks and the compare
  idiom's), negative literals 0 -> 10, 9, 9, 7, 66, 51, 43, 67. Left: the compare idiom's complements
  ((u64)x + ~0x5fu + 1) go with "Flags as comparisons".
- [x] **Extensions and bit fields.** `(s32)(x << 16) >> 16` for a halfword's sign extension, `(x &
  0xffff) << 16 >> 16`, `(u32)((s32)(w & m) >> n)` for a field whose mask clears the sign bit, `(x <<
  2) & 4` for `(x & 1) << 2`, a final mask the bits already satisfy. Fold to `(s16)x`, `(u16)x`, `(w
  >> n) & (m >> n)`, `(x & 1) << 2`, and drop masks a known-bits pass proves redundant. Public
  example: none found (Thumb-2 has sxth and ubfx; gizmo's ARMv4T build has no signed halfwords).
  Test: a gizmo function on a32 that returns a signed short and extracts a field with a signed shift.
  Done 2026-10-24 (dread.bitfields, dread.tidy's pass "bits" after "expressions"; docs/decisions.md,
  "Extensions and bit fields: casts, fields and selects, each rewrite proved where it is made"). Rules
  propose, guided by known bits (zeros, ones, copies of the top bit, from constants, types, casts,
  loads, masks, shifts, sums, comparisons, selects), and z3 proves each rewrite for the node it
  replaces with C's types (cexpr.equivalent), the node keeping its promoted type; a sign extension of 8
  or 16 bits by shifts is a cast ((u32)(s16)x, a signed field (u32)(s8)(x >> 4)), a logical pair from
  bit 0 (u32)(u16)x, a field whose top bit is known 0 the unsigned field, other pairs and masks the
  field they select ((x >> k) & m, (x & 1) << 2), two shifts or masks one, masks and narrowing casts
  the known bits make redundant, and a smeared sign or a negated truth value under a mask the select
  it computes (where the masked value reads no memory and calls nothing). Public examples after all,
  on RV32 (no sext.h, sext.b, zext.h without Zbb), in Thumb-2's sbfx (lifted as the same pair) and in
  GCC's branchless selects: FreeRTOS virt-gcc-Os prvUnlockQueue (int8_t cTxLock--: `s2 = (s32)(((u32)s2
  - 1) << 24) >> 24;` now `s2 = (s8)(s2 - 1);`, as mps2-gcc-Os prints its sxtb), vPolledQueueConsumer
  and vCounterControlTask (uint16_t counters: `(a5 + 1) & 0xffffu`, `(s3 - 1) << 16 >> 16` now `(u16)(a5
  + 1)`, `(u16)(s3 - 1)`), prints (`(((s32)(a3 << 30) >> 31) & 0x10u) + 0x20u` now `(a3 & 2) != 0 ? 0x30u
  : 0x20u`, its clamp `((s32)(s0 ^ 0xffffffffu) >> 31) & s0` now `s0 >= 0 ? s0 : 0`) and three FromISR
  functions' wrap-arounds (`a5_1 & (0 - (u32)(a5_1 < 24))` now `a5_1 < 24 ? a5_1 : 0`); gizmo's
  cmd_volume (clamp(level, 0, 9): a32, m3, m4 `r0 & ~(u32)(r0 >> 31)`, rv32 `a0 & (0 - (u32)(a0 > 0))`,
  now `r0 >= 0 ? r0 : 0`) and rv32's parse_kv (`((s32)parse_uint(...) >> 31) & 0xfffffffdu` now
  `(s32)parse_uint(...) < 0 ? -3 : 0`). A test program (tests/test_bitfields.py BITS_PROGRAM: a short, a
  signed char, unsigned shorts, sums of them, a signed byte of a word, bit fields of 8 and 4 bits, a bit
  moved, a field of a mask, a select by a sign, a pad character, a clamp) with GCC for ARMv4T at -Os and
  -O0, a Cortex-M3 and RV32IMAC: the casts, fields and selects read the same on all four, and the gate
  takes every function, also with Ubuntu 22.04's compilers (whose RV32 GCC 10 branches where GCC 16
  smears a sign). Its patterns (16 functions, main -> this): signed shift pairs 6 -> 1 on ARM state at
  -Os and on RV32, 8 -> 1 at -O0, 3 -> 1 on the Cortex-M3 (its sbfx; the one left is the 4-bit field),
  smeared signs 2, 1, 2, 2 -> 0, logical pairs and masks of a shift gone; 11, 11, 5 and 12 of its
  functions read differently. Measured on copies of the eight builds (main -> this; the gate, then
  every lifted function's printed C): the gate accepts the same functions on all eight (gizmo a32 35,
  m3 36, m4 44, rv32 31; FreeRTOS GCC -Os 317, clang -O2 275, RISC-V GCC -Os 252, GCC -O0 354; the
  readable tables byte for byte); the printed C of 24 functions changed (gizmo 1, 1, 1, 3; FreeRTOS 2,
  0, 7, 9). The patterns (statements of the printed C, counted alike on both): signed shift pairs 3 ->
  0 and logical pairs 1 -> 0 on RISC-V GCC -Os (casts to s8 12 -> 14, to u16 0 -> 2 there); smeared
  signs and negated truth values under a mask 1, 1, 1, 2 -> 0 on gizmo and 1, 0, 6, 0 -> 0 on FreeRTOS,
  selects now; two masks 1 -> 0 on gizmo rv32; on GCC -O0 shifts by 0 4 -> 0 (prvRand's lsrs; ubfx #0)
  and casts to u8 the known bits make redundant 11 -> 5 (`prvCheckExpectedState((u8)(stack32.f_8 == 0 ?
  1 : 0))`, `(u8)(r3_2->f_24 & ~0x1u)` of a byte field); on GCC -Os only prints' clamp and a redundant
  `& 1` of a select of 1 and 0 (xAreDynamicPriorityTasksStillRunning), on clang -O2 nothing (Thumb-2
  has sxth, ubfx and IT blocks). The check took 10, 5, 7, 3, 608, 303, 152, 447 s -> 6, 4, 5, 3, 469,
  334, 152, 459 s and printing every lifted function in one process 3.3, 2.7, 2.4, 1.4, 54, 46, 24, 49
  s -> 2.4, 1.9, 2.0, 1.4, 45, 48, 27, 46 s, on a machine shared with other runs: no change beyond its
  noise; the pass itself is 2.0 % of making the readable C (2.4 s of 122 s on RISC-V GCC -Os, 3.4 s of
  173 s on GCC -O0, every lifted function in one process, profiled; half of a first version's time
  went to the types of functions without a shift or a mask, which it now skips). Names made for the C
  before carry to it (dread readable --carry, which the check runs): tables naming every variable of
  changed functions with main's code (gizmo a32's and rv32's, RISC-V GCC -Os's seven, GCC -O0's
  nine), 113 names in the 15 that have variables, carried whole. Found on the way: three of the
  lifters' idioms that later passes read by their shape must stay whole (the compare's (u64)x +
  (u32)~y + 1 lost its (u32) to a first version and two gizmo conditions changed form; a byte store of
  an add with its carry, (u8)(u32)((u64)r6 + 1 + 0), lost the (u32) sums reads and printed (u8)(r6 + 1)
  on clang -O2; GCC -O0's flag select of 1 and 0 through a 64-bit temporary, (u32)(u8)(!(u32)(t_3 >>
  32) || ... ? 0x1u : 0x0u), lost its (u8), and flags left `r3_1 = r0_1 <= s24 ? 0x1u : 0x0u` for the
  comparison): no rewrite of a node that goes through 64 bits (a cast or a variable), and casts of a
  value to its own type only between two arithmetic shifts. Left: a variable's bits from its
  definitions (three masks each in gizmo a32, m3, m4 script_run and GCC -O0's __NVIC_EnableIRQ, all a
  register shift's amount: the next item); 16-bit int (MSP430: left alone).
- [x] **Register shifts with an amount known below 32.** ARM's shift by a register (lsl r0, r1, r2,
  Thumb-2's lsl.w) is lifted as `(s & 0xffu) == 0 ? x : (s & 0xffu) < 32 ? x << (s & 0xffu) : 0` (the
  amount's low byte, 32 and more giving 0), and readable C prints it so. Its first arm is always
  redundant (x << 0 is x); where the amount is known below 32 the whole is `x << s`: from its
  expression's known bits (dread.bitfields known), and for a variable from all its definitions (known
  bits over a function's assignments, a greatest fixpoint, given to the proof as an assumption on the
  variable, which cexpr has no way to state yet). Follow-up of "Extensions and bit fields": the only
  masks its known bits left on variables are these amounts. Public examples: gizmo a32, m3, m4
  script_run (`acc ^= p[i] << (i & 7)`: `r2 = r3_2 & 7; r0_1 ^= (r2 & 0xffu) == 0 ? r1_1 : (r2 & 0xffu) <
  32 ? r1_1 << (r2 & 0xffu) : 0;`), FreeRTOS mps2-gcc-O0 __NVIC_EnableIRQ (`1UL << (IRQn & 0x1FUL)`);
  the ready-priority bitmaps of the Cortex-M3 port (`uxReadyPriorities |= 1UL << uxPriority`, an amount
  nothing bounds: there only the first arm goes). Lines with the idiom on the eight builds (main,
  2026-10-24): gizmo a32, m3, m4 2 each (script_run, gpio_set), rv32 none (RISC-V's sll takes the
  amount's low five bits: `x << (s & 31)`), FreeRTOS GCC -Os 18 in 14 functions, clang -O2 21 in 13,
  RISC-V GCC -Os none, GCC -O0 14 in 11.
  Done 2026-10-25 (dread.bitfields: the select rules in proposals, decided, variable_facts with
  definitions and read_unset; dread.cexpr Ctx.facts; docs/decisions.md, "Facts about variables: known bits
  from all their definitions, assumed by the proofs"). The first arm of a register shift goes wherever the
  rest shifts by its amount (proved: x shifted by 0 is x; asr's alike; a rotation keeps it, x << 32 being
  undefined), and a select whose condition the known bits decide is the arm it takes, the guard of an
  amount below 32 among them. A variable's known bits come from all its definitions in its function (a
  greatest fixpoint over its assignments in the order of what they read, a counter's taken as unknown
  after three weakenings; nothing for a parameter, an asm's output, an address taken or a variable that
  may be read before it is written), and cexpr's proofs assume them (the test values keep them, z3 assumes
  them). gizmo a32, m3, m4 script_run: `r0_1 ^= *(volatile u8 *)(r0 + r3_2) << (r3_2 & 7);` (the source's
  `acc ^= p[i] << (i & 7)`); gpio_set keeps `(r0 & 0xffu) < 32 ? 1u << (r0 & 0xffu) : 0` (its amount a
  parameter); GCC -O0's __NVIC_EnableIRQ reads `1u << (s9 & 0x1fu)`. On fresh copies of the eight builds
  (gizmo a32, m3, m4, rv32; FreeRTOS GCC -Os, clang -O2, RISC-V GCC -Os, GCC -O0; main 6f015bc -> this;
  the lifted tables' functions as dread readable --out prints them): first arms 2, 2, 2, 0; 18, 21, 0, 14
  -> 0 on all; guards 2, 2, 2, 0; 18, 21, 0, 14 -> 1, 1, 1, 0; 18, 21, 0, 13 (FreeRTOS's amounts are
  priorities loaded from a task's structure, which nothing bounds, and loop counters only a comparison
  bounds). Of the kinds, every function that lifts has lsl by a register (2, 2, 2, 0; 22, 28, 0, 24 times)
  and lsr once (gizmo a32's __udivsi3, newlib's __sflush_r on the three Cortex-M3 builds), no asr or ror
  (their forms are tested on snippets). The gate accepts the same functions (35, 36, 44, 31; 317, 275,
  252, 354) and writes the same readable tables. The printed C of 77 functions changed (every function
  that lifts: 3, 2, 2, 0; 24, 21, 5, 20): the register shifts, and ten where a variable's facts made a
  mask or a narrowing cast redundant or decided a select (`r4 & 1` of a value 0 or 1 in three of GCC
  -Os's, `(u8)(r3_1 | g_200017ac_u8)` of a byte, a division's guard `r4 > 0xffffffffu ? r4 : ...` that is
  never true; on RISC-V `a0 >> (a5 & 31)` as `a0 >> a5` in __clzsi2, `(s0 & 1) == 0` as `s0 == 0` twice,
  `s3_24 & (0 - a0_13)` as `a0_13 != 0 ? s3_24 : 0`). The bits pass took 1.4 -> 2.2 s of every lifted
  function's readable tree on FreeRTOS GCC -Os and 0.8 -> 1.2 s on GCC -O0 (one process, CPU time, on main
  bc223f9 with the same code; on main 1c241ac 0.8 -> 1.5 and 1.2 -> 1.8 s): the facts and the new rules
  cost about 0.6 s a build, less than the totals (89 -> 72 s, 42 -> 37 s) move from run to run on a
  machine shared with other runs, and the check's times stayed (15, 4, 4, 5; 609, 439, 279, 728 s -> 15,
  4, 4, 4; 617, 417, 279, 728 s). Names made for main's C carry (dread readable --carry, tables naming
  every variable of the 77 changed functions with main's code): 1349 of 1357, the 8 dropped those of
  variables the change folded away (script_run's loaded byte and amount, __NVIC_EnableIRQ's amount, a
  temporary of RISC-V's prvQueueSetSendingTask). Tests (tests/test_bitfields.py): the shifts' forms with
  and without facts (lsl, lsr, asr, a rotation), cexpr's proof and test values under facts, a small
  function's facts (a mask, a counter, a mask of the counter, a byte or 1, a value that reads itself; a
  parameter, a read before a write, an address taken, an asm's output), random functions of shifts by
  registers cleaned up and compiled beside the originals, gizmo's script_run and gpio_set on the ARM
  builds, and a program of our own through the gate (ARMv4T at -Os and -O0, Cortex-M3, RV32IMAC: every
  function accepted, the masked amounts plain shifts on all four); with Ubuntu 24.04's and 22.04's
  compilers too. Left: a bound only a comparison on the way gives (a loop's counter, `r1_2 <= 29 && ...`);
  a variable's bits through steps of itself in sequence (gizmo's __udivsi3, `r2 += 8; r2 += 4` before the
  shift by r2 + 3: the flow-insensitive fixpoint takes them for a cycle).
- [x] **One variable per value.** A register that holds unrelated values in turn (a pointer, then
  loaded bytes, then a callee's second word; a parameter, then a callee's result) stays one variable,
  and the pilot names it after one of its uses. Split variables where their live ranges do not meet
  (the webs pass, extended to the parameters' registers and to values that pass through a call).
  Public examples: FreeRTOS mps2-gcc-Os xTimerGenericCommandFromTask (`if (r0 == 0) r0 =
  vAssertCalled(0x1131u);`: the parameter variable takes vAssertCalled's result, then is passed on);
  gizmo a32 read_line (`r0 = uart_getc(r0);`).
  Done 2026-10-24 (dread.tidy webs with split_parameter and hands_on, propagate's _moved_back;
  docs/decisions.md, "One variable per value: a parameter kept apart from what its register hands
  back"). Both examples were gone with the phantom arguments (2026-10-23): read_line's parameter is its
  buffer throughout, uart_getc's result r0_1, its own result r0_2; xTimerGenericCommandFromTask reads
  `if (r0 == 0) vAssertCalled(0x1131u);` and stores its parameter. webs already split a parameter's
  register where the live ranges do not meet (after the clean-up no variable with two webs is left on
  the eight builds but gizmo m4's FPU registers, which asm writes). What stayed one variable were
  registers whose values meet in a use, among them parameters that meet a callee's result where the
  value is only handed on: in the return of a function whose result no caller reads (a source that
  returns nothing hands back what the register held last: gizmo's handle_key returns c on one path,
  led_on's leftover on another; FreeRTOS's vQueueAddToRegistry, vTaskDelay, vTaskResume) or in an
  argument its callee does not read but may hand back (vTimerSetTimerID's xTimer through
  vPortEnterCritical(r0)). There the parameter keeps the uses only its own value reaches, and the
  register's later values are a variable of their own declared with the parameter as its initial value
  (`u32 r0_1 = r0;`), where a value on either side is read; nothing writes the parameter then, so the
  copies the compiler made of it propagate into it (vTaskResume's `r4 = (volatile struct s_8 *)r0;` is
  gone, the parameter is `volatile struct s_8 *r0` throughout, `u32 r0_3 = (u32)r0;` the value handed
  back), and a part gets a type the whole could not: gizmo rv32's apply_config typed the text pointer
  and the line buffer's index as one `volatile u8 *a0` (`a0[(u32)stack84] = 0;`, `a1 = a0 + 1`), now
  `const volatile u8 *a0` and `u32 a0_2` (`stack84[a0_2] = 0;`). Where the values meet in a use that
  reads them (a clamp, a pointer a loop advances), one variable as before; so for a register nothing
  reads but such uses (irq_dispatch(void)). And a parameter moved back after a call (r4 = r0; f(); r0 =
  r4;, the C's f writing no r0) is no new value: prvDeleteTCB's `r4 = (u32)r0;` and, in one arm after
  `vPortFree(r0->f_30);`, `r0 = (volatile struct s_13 *)r4;` are gone.
  Tests (tests/test_tidy.py): a parameter reused for a leftover result (split where no caller reads the
  result, one variable where one does), a parameter handed on through a call (split where the callee
  does not read the argument, not where it does), a clamp and a register only handed on (one variable),
  a parameter that only reaches the return beside a compared result (split), a parameter moved back in
  one arm; with Ubuntu 22.04's compilers too, as gizmo-m3's gate test (handle_key and apply_config split
  there).
  Measured on fresh copies of the eight builds (gizmo a32, m3, m4, rv32; FreeRTOS GCC -Os, clang -O2,
  RISC-V GCC -Os, GCC -O0), main at fa47c42 -> this, every lifted function's readable C as dread
  readable prints it with the copy's tables: parameters kept apart 3, 2, 2, 1; 19, 3, 10, 25; parameters
  written in their function 8 -> 5, 10 -> 8, 11 -> 9, 8 -> 7; 38 -> 18, 50 -> 47, 212 -> 201 (RISC-V's
  others are the variadic calls' and the ecall's), 42 -> 17; variables (parameters and locals) 118 ->
  121, 116 -> 118, 193 -> 195, 97 -> 98; 1,848 -> 1,856, 1,692 -> 1,692, 1,757 -> 1,760, 1,458 -> 1,468;
  typed parameters (pointers, signed) 16 -> 17, 16 -> 17, 17 -> 18, 17 -> 17; 156 -> 161, 117 -> 117,
  117 -> 120, 133 -> 139, typed locals 24 -> 26, 19 -> 21, 84 -> 86, 24 -> 23; 215 -> 209, 195 -> 193,
  109 -> 106, 220 -> 216 (a parameter's typed copies go into it; rv32's index a1 is no pointer now);
  functions whose readable C changed 3, 2, 2, 1; 20, 3, 11, 25 (the splits, prvDeleteTCB and RISC-V's
  prints by the move back); lines 773 -> 779, 773 -> 778, 910 -> 915, 860 -> 861; 14,711 -> 14,711,
  13,484 -> 13,482, 12,058 -> 12,055, 13,847 -> 13,852. The gate accepts the same functions name by name
  with the same verdicts (35, 36, 44, 31; 317, 275, 252, 354); its wall time, main and this alternating,
  the machine shared, 5 -> 5, 4 -> 4, 4 -> 5, 3 -> 3; 356 -> 369, 268 -> 259, 130 -> 141, 452 -> 433 s;
  every GCC -Os function's readable C in one process 38.6 and 36.8 -> 37.2 and 39.3 s of CPU. Names,
  from a table made for the measurement by main for its C (every variable named, with identities), on
  GCC -Os 298 functions and 1,968 names: of the 20 functions whose C changed all carry, 139 names, 11
  dropped (a parameter's copy that propagated into it: r4 in ten functions, vPortFree's r6; the
  parameter keeps its own name), 19 with one variable without a name (the new one, for the pilot's next
  run); gizmo a32 3 functions, 18 names, none dropped, m3 2, 14, none (dread readable --carry, and the
  same through --check).
  Left: other registers whose values with uses of their own meet only where the value is handed on
  (vTaskResume's r0_1: prvTaskIsTaskSuspended's result, then the ready list's address, both handed to
  vPortExitCritical; gizmo apply_config's parse_kv status and clamp's result, returned;
  vPeriodicEventGroupsProcessing's results): apply_config on gizmo a32, m3 and m4, none on rv32; 13 in
  12 functions, 10 in 10, 18 in 12, 11 in 10 on FreeRTOS GCC -Os, clang -O2, RISC-V, GCC -O0 (counted by
  a script, not in the repository; registers asm writes among them): keeping them apart needs a copy
  behind each value (docs/decisions.md: not done); registers asm writes are not split at all (gizmo
  m4's FPU registers, 20 in three functions; GCC -O0's mrs of basepri into r2, a parameter in four
  functions; RISC-V's ecall, which binds a0-a7: with the next kind 74 parameters in thirteen
  functions), nor the argument registers a variadic call passes whole (vAssertCalled's a2-a7 on RISC-V:
  the item "No parameters from pushes"); not established that a return regflow finds unread is
  a leftover in the source (a function without callers, or called through pointers only, counts as
  unread), and what the pilot names the new variables was not asked.
- [x] **Merged arms and hoisted statements.** Arms with identical bodies stay apart, and a statement
  common to every arm is printed in each. Merge arms with one body into one condition with ||, hoist
  what all arms begin or end with. Public example: FreeRTOS mps2-gcc-Os
  prvCheckExpectedTimeIsWithinAnAcceptableMargin (`r4 = r1 + 7;` in both arms; the source computes
  the bound once).
  Done 2026-10-24 (dread.structure arms_pass, merged_arms, hoisted, chain_of; dread.tidy's hoisting at
  the end of the clean-up; cparse copy_of and copies, read by dread.naming identities; docs/decisions.md,
  "Merged arms and hoisted statements"). Neighbouring arms of an else-if chain with the same code (as
  text, wherever their instructions are) are one arm with their tests joined by ||, an arm that is the
  last else's code drops its test where it has no effect, a run of ifs without else whose arms are the
  same code and never fall through is one if; what every arm of an if / else (or of a whole chain with
  a last else) begins with goes in front of it where no test has an effect and none reads what it
  writes (or it assigns a value without an effect of variables no test writes and no call reaches),
  what every arm ends with behind it. In dread.tidy for its conditional blocks' ifs once the clean-up
  has settled, then the clean-up again: prvCheckExpectedTimeIsWithinAnAcceptableMargin is `r0_1 =
  xTaskGetTickCount() - r0; if (r0_1 < r1) g_200009d0 = 759; if (r1 + 7 < r0_1) g_200009d0 = 767;`, the
  source's two tests; eTaskGetState's `else if (r5 == r7 || r5 == r6) r0_1 = 2;`; xEventGroupWaitBits's
  `if (r1 >= 0x1000000u || r1 == 0) vAssertCalled(...)`; vPrimaryBlockTimeTestTask's four IT-block if /
  elses one line each. Measured as "Loops in their source shape" (the same copies of the eight builds),
  that item -> this: the gate accepts the same functions, set for set (35, 36, 44, 31, 317, 275, 252,
  354, as main); the readable C of 0, 0, 1, 2, 18, 11, 5, 11 functions changed; neighbouring arms of one
  code 0, 0, 0, 0, 15, 1, 0, 3 -> 0 everywhere; runs of exits with one body 0, 0, 0, 0, 1, 1, 1, 0 -> 0;
  ifs whose every arm begins with one statement 0, 0, 1, 3, 50, 3, 6, 3 -> 0, 0, 0, 0, 30, 1, 0, 2 (what
  is left: a load or a call in front of a test that calls, the interrupt tests' handles), whose every
  arm ends with one 1, 1, 1, 1, 2, 12, 0, 7 -> 1, 1, 1, 0, 0, 1, 0, 1 (gizmo ARM's: an else if's own two
  arms, read_line's, which its chain's first arm does not share); arms of one code apart in a chain 9,
  1, 8, 11 on FreeRTOS -> 10, 1, 8, 11; every GCC -Os function's readable C 65.5 and 67.7 s of CPU before,
  66.8 and 67.6 s with this. Names a script made for main's C carry to this C: of 6, 5, 6, 6, 39, 16, 19,
  34 functions whose C changed under them, all (44, 36, 45, 42, 468, 217, 265, 267 names), 1, 1, 1, 4,
  12, 4, 0, 3 dropped: the copies "Loops in their source shape" took out and variables the clean-up
  folded away once their statement was written once (prvCheckExpectedTimeIsWithinAnAcceptableMargin's
  r4, vPrimaryBlockTimeTestTask's r2_2 and r2_4, __sseek's r3_1); no variable left without a name (without
  copy_of GCC -O0's two prvSuspendedTaskTimerTestCallback lost r0's: a call written once for its copies
  at two instructions). Tests (tests/test_arms.py): each rewrite and what refuses it, the clean-up's
  hoisting of an IT block's statement, the identities of a statement written once, gizmo's rv32
  script_run, read_line and m4 apply_config, random functions of shared arms rewritten and compiled by the
  host beside their originals; also with Ubuntu 24.04's and 22.04's compilers.
  Left: arms of one code apart in a chain (a test would move before another); a statement every arm
  begins with that loads or calls, where a test calls; a statement first in one arm and second in the
  other behind one it does not depend on (gizmo rv32's script_run, case 1); an else if's own arms.
- [x] **Structure definitions once per image.** Every function's readable C repeats the full
  definition of each structure it touches. Write them to one header per image (`--out` writes it
  beside the files; the printed form includes it), and print only the members a function uses, as a
  comment, if anything. Public example: FreeRTOS mps2-gcc-O0 prvInitialiseTaskLists (59 lines of
  declarations, six structures, for a 20-line body); 60 of 357 functions of that build and 84 of 329
  of mps2-gcc-Os have more declaration lines than body lines.
  Done 2026-10-23 (dread.readable Types, printed, header_text; docs/decisions.md, docs/formats.md).
  What dread readable prints (NAME, `--out DIR`) leaves the image's structures, those behind pointers
  and of static data, to one header, readable_types.h (`--out` writes it beside the files, `--header`
  prints it), which the function's file includes, naming in a comment a structure a line the members
  it uses with their types and offsets (`/* struct s_9: u32 f_0 at 0x0, struct s_24 *f_10 at 0x10
  */`). Its frame's structure, its extern declarations (trimmed per caller: one prototype for the image
  would change what compiles) and its macros (named per function, and not scoped: in a header they
  would expand inside other files) stay with it. What the gate and the builds compile is the compiled
  form as before, each file defining what it uses; the printed form with the header beside it compiles
  to the same bytes and relocations, for all 1,679 lifted functions of the eight builds. Measured on
  copies of the eight builds, every lifted function, main at b98f0d1 -> this (declaration lines: those
  outside the function's definition, comments and includes among them; body: the definition from its
  head to its closing brace; the review counted otherwise, its 60 and 84 are not reproduced): functions
  with more declaration than body lines GCC -Os 55 -> 5 of 329, clang -O2 43 -> 4 of 286, RISC-V GCC
  -Os 26 -> 0 of 259, GCC -O0 47 -> 2 of 357 (what is left: small functions whose own externs and
  macros outnumber their lines, fflush 7/5); declaration lines 5,092 -> 2,867, 4,288 -> 2,459, 3,984
  -> 2,410, 4,982 -> 3,064 (median 9 or 10 -> 7), body lines unchanged; prvInitialiseTaskLists 59 ->
  12 lines for its 22; the header 694 lines (9,035 bytes), 770 (9,981), 362 (4,676), 593 (7,670).
  Gizmo's builds have no structure in the gate's plan: unchanged (35 to 44 functions, a median of 3
  declaration lines).
- [x] **Loops in their source shape.** Several exits at a loop's head print as `for (;;) { if (c)
  break; ... }`, a counted loop whose first test holds as a do-while after its initialisation, and
  post-increments as a copy and an add. Make leading breaks the while condition, a do-while whose
  condition holds on entry a for loop, and a use of the copy made before an increment `*p++`. Public
  example: gizmo a32 strncpy (`for (;;) { if (r2 == 0) break; ... if (r12 == 0) break; ...}` for
  `while (n && *src)`; `*(volatile u8 *)r3 = (u8)r12; r3 = r3 + 1;` for `*d++ = *src++`).
  Done 2026-10-24 (dread.structure refine_loops, leading_breaks, leading_exit, Entry, entered, counted;
  dread.tidy steps; dread.polish fold_steps; cparse For's init_addr and step_addr, read by dread.naming
  identities; docs/decisions.md, "Loops in their source shape"). The run of tests at a loop's head that
  leave it is its while condition (`!a && !b`, a break further in staying), a head test that leaves by
  a return (or a break behind statements) where the loop has no other way out is the condition with
  those statements behind the loop; a do-while whose condition is proved to hold wherever control
  reaches it (written back through the statements before it and the tests around it, z3) is a while
  loop, and a while loop whose body ends with a step of a variable its condition reads, behind an init
  of it, a for loop; a step made through a copy (`w = r0 + 1; r2 = *r0; r0 = w;`) is a step of the
  variable, and an access with its pointer's step behind it is one, `*p++` or `*++p` (after the types:
  a word pointer's step of one element too). gizmo a32 strncpy, as the check types it: `while (r2 !=
  0) { r12 = *++r1_1; if (r12 == 0) break; *r3++ = r12; r2--; }` and `while (r3 != r2_1) *r3++ = 0;`;
  RISC-V's memcpy and checksum `for (a3 = a0; a3 != a2_1; a3++)`; FreeRTOS GCC -Os's prvSingleTaskTests
  (MessageBufferDemo.c) `for (r8 = 48; r8 != 53; r8++)` for `for (xItem = 0; xItem < xMax6ByteMessages;
  xItem++)` with `'0' + xItem`, newlib's memcpy on RISC-V `*a3++ = *a1++;`. Measured on fresh copies of
  the eight builds (gizmo a32, m3, m4, rv32; FreeRTOS GCC -Os, clang -O2, RISC-V GCC -Os, GCC -O0), main
  at 95511db -> this, every function's readable C as `dread readable --out` prints it (counted by a
  script on the trees, not in the repository): loops with tests at their head that break 2, 2, 2, 1,
  2, 1, 1, 6 -> 0 everywhere, gizmo a32's four with a return at their head -> 0 (clang -O2's one and GCC
  -O0's two have another way out); do-whiles behind an assignment their condition reads 2, 2, 2, 6, 59,
  24, 67, 0 -> 1, 1, 1, 3, 8, 14, 21, 0 (do-whiles 13, 8, 8, 18, 100, 90, 95, 14 -> 10, 7, 7, 9, 24, 71,
  37, 11; for loops with an init and a step 0 -> 4, 5, 5, 4, 54, 10, 41, 47); an access and its
  pointer's step apart (the step behind a statement whose one read of the pointer is the access's
  whole address) 18, 22, 22, 7, 23, 6, 1, 6 -> 0 everywhere (any read and a step of one 19, 23, 24, 14,
  34, 30, 14, 16 -> 0, 0, 1, 7, 11, 23, 12, 4: counters compared, indices); `for (;;)` never left 2, 3,
  3, 1, 78, 74, 69, 81, unchanged. The gate accepts the same functions, set for set (35, 36, 44, 31,
  317, 275, 252, 354); the readable C of 13, 15, 15, 11, 42, 19, 31, 43 functions changed; every GCC -Os
  function's readable C in one process 66.4 and 67.7 s of CPU on main, 69.2 and 69.8 s with this (the
  entry proofs, the folds; a shared machine). Names a script made for main's C (every variable of every
  lifted function) carry to this C: of 6, 5, 5, 5, 27, 9, 17, 26 functions whose C changed under them,
  all (44, 36, 35, 36, 365, 125, 225, 189 names), 1, 1, 1, 4, 5, 3, 0, 2 dropped, each of a copy the
  steps took out (w_1, RISC-V's a5), no variable left without a name. Tests (tests/test_loops.py): each
  shape and what refuses it, the identities of a for loop's init and step, gizmo's strncpy, memcpy and
  checksum on every build, random loops structured and random pointer walks through every stage, each
  compiled by the host beside its original and run (the walks on a memory of their own); also with
  Ubuntu 24.04's and 22.04's compilers.
  Left: a loop whose exit is a return at its end stays `for (;;)` (none without another break here);
  one entered only by what a loop before it proved stays a do-while (gizmo's fmt_dec); an index's step
  (`x = a[i]; i++;`) is not folded; an if that only guards a while loop on its own test (`if (n != 0) {
  while (n != 0) ... }`: RISC-V's newlib memcpy and memset, gizmo rv32's memcmp) keeps its test; two
  steps of one pointer in a round are an offset and one step (webs splits the pointer between them).
- [x] **A reading form without the instruction comments.** Every statement carries its instruction,
  pushes, pops and stack adjustments are comment lines, and a statement made of several instructions
  carries one of them (a call can carry the mov after it). An option (`--plain`, and the GUI's
  reading view) that leaves them out, with the address of each statement's first instruction in a
  margin. Public example: every function (FreeRTOS mps2-gcc-Os xTaskGetTickCount).
  Done 2026-10-23 (dread.readable reading, margin_text; dread.cparse Addr, first_addresses,
  unparse_lines; dread.tidy fold, propagate, spread, addresses; dread.gui /api/reading;
  docs/decisions.md). `dread readable --plain` prints the printed form without the instructions'
  comments, their notes and dread.build's lines for the compiler, each statement's line behind a
  margin with the address of its first instruction: the lowest of its own and of those whose
  statements the clean-up folded or propagated into it alone (recorded in its addr, a cparse.Addr; a
  literal that two statements' loads share belongs to neither, so the margin does not jump back);
  a do-while's on its while line. dread gui's function page has a readable tab, first for a function
  without source: the same lines from /api/reading (made on the first request outside the server's
  lock: 1.3 to 1.7 s for xQueueGenericSend with the context, then kept), the margin's addresses
  selecting their instruction in the listing; checked in headless Chrome on gizmo m3 and FreeRTOS GCC
  -Os (light, dark, phone width: no script errors, no horizontal scroll). The comment a statement
  carries: one that takes a call's result in (return f(), r4 = f(), a store of it) named the
  instruction after the call (the mov, the pop, the bx lr); now it shows the call's, its own staying
  for the printer's lines and the digests (taking the call's for its own changed five functions'
  digests, and their names in a target's tables would have stopped applying). Such statements naming
  another instruction, main -> this: gizmo a32 9 -> 0 of 53, m3 4 -> 0 of 54, m4 4 -> 0 of 59, rv32
  5 -> 0 of 45; FreeRTOS GCC -Os 232 -> 0 of 1,609, clang -O2 113 -> 0 of 1,232, RISC-V GCC -Os 108
  -> 0 of 1,057, GCC -O0 173 -> 0 of 1,205. Characters of every lifted function's printed form
  against its reading form (with its margin, without): GCC -Os 554,297 -> 456,046, 386,210 (median a
  function 0.85 and 0.71), clang -O2 528,128 -> 425,753, 359,519 (0.84, 0.71), RISC-V 511,645 ->
  409,280, 316,790 (0.82, 0.62), GCC -O0 504,256 -> 416,422, 349,161 (0.82, 0.68); gizmo 0.89 to 0.96
  and 0.66 to 0.71. The gate accepts the same functions on the eight builds (35, 36, 44, 31; 315, 275,
  252, 354), compiles the same C but for the comments of 9, 4, 4, 5; 45, 38, 31 and 93 of their
  files (line for line), and builds the same payloads; every digest is as before. Not established: a
  statement in an IT block's arm has no instruction of its own (its if carries it), so its line has no
  address; an instruction the clean-up dropped as dead is no statement's.
- [x] **Signed types from signed use.** A variable compared only with signed conditions stays u32
  with `(s32)` at every compare, and -1 compares as 0xffffffffu. Type it s32 when its compares are
  signed (its constants then print signed), and a result signed when its callers compare it signed.
  Public examples: FreeRTOS mps2-gcc-Os vPrimaryBlockTimeTestTask (`if ((s32)r2_1 > 4) break;`);
  gizmo a32 read_line (`if ((s32)r0 < 0)` on uart_getc's int result).
  Done 2026-10-23 with the item "Pointer types for parameters and prototypes" (dread.vartypes): a
  variable compared in order only signed is s32 where its function's C holds no more casts so ((s32)v
  is v; (u32)v where the lifted C read it unsigned: an unsigned order, a right shift, a division, a
  64-bit conversion; x >>= k of it is x = (u32)x >> k); a function's result is s32 where a caller
  compares it signed, `(s32)f()` or a variable every definition of which that reaches the comparison
  is the call (dread.cflow's reaching definitions: handle_key's r0 holds its parameter and then
  led_on's leftover, no evidence for led_on). Signed arithmetic wraps (-fno-strict-overflow, the
  WRAPS line). vPrimaryBlockTimeTestTask: `s32 r2_1, r2_2, r3_1, ...`, `if (r2_1 > 4) break;`;
  read_line: `s32 r0_1;`, `if (r0_1 < 0)`, `extern s32 uart_getc(void);` and uart_getc's own `s32
  uart_getc(void)`. Signed parameters 0 -> 2, 2, 3, 2, 5, 3, 4, 3 and variables
  0 -> 2, 2, 3, 5, 21, 23, 10, 24 on the eight builds, (s32) of a variable 7, 7, 8, 7, 28, 32, 16, 32
  -> 0, 0, 0, 0, 1, 5, 1, 1 (what is left: a variable also compared unsigned, or read so where the
  casts back would be more). Left: a compare the compiler made on an expression (GCC's strength
  reduction: `(s32)(r0 - (r3 + (r3 << 1))) > 4`) types nothing; a result its callers only pass on to
  a callee that compares it signed is no evidence; whether a counter's s32 is the source's int (a
  value compared signed by the compiler's choice, a loop's bound) is not established.
- [x] **Casts and constants.** `(u8)0x1u` for a byte store of 1, cast chains `(u16)(u32)(s16)x`, `+
  0`, `0x0u |`, `((u64)0x0u << 32) | 0x0u`, `x = (u32)(x + 1)`, and 0 and 1 as `0x0u`/`0x1u`. Drop
  the casts a store's width implies, collapse chains, fold `+ 0` and `| 0`, print small values in
  decimal where the type is clear, `x++`/`x += k`. Public examples: FreeRTOS mps2-gcc-O0
  eTaskGetState (`sl9 = (u8)0x0u;` ... `(u8)0x2u`), mps2-gcc-Os xTimerCreate (`+ 4 + 0`), gizmo a32
  strncpy (`(u8)0x0u`).
  Done 2026-10-23 (dread.polish, with "Negative constants by use"; figures there for the gate): a cast
  a store or a parameter implies goes (also a variable's declared width; not the cast that takes a
  64-bit value's low word, which says what it does), so does a cast to its operand's type; a chain
  (T)(U)x is (T)x where U is as wide as T or holds x's values; where only a sum's low bits count, the
  casts that widen its operands go ((u32)((u64)(u32)r0_1 + 4 + 0) is `(u32)r0_1 + 4`: xTimerCreate's
  vListInitialiseItem, uxListRemove(r6 + 4)); constant expressions that are numbers fold ((u8)0x0u,
  ((u64)0x0u << 32) | 0x0u, vPortFree's `(volatile struct s_20 *)(r0 - 8)`), not those that show a mask
  or a comparison; x + 0, 0 | x go where no type changes; x + ~y + 1 is x - y (memcpy(..., r2 - r7));
  a select on a constant condition is its arm (`r5 = r1 / 10;` for 0xau ? r1 / 0xau : 0); 0-9 in
  decimal; x = x op y is x op= y and x = x + 1 x++ (also a field or an element: g_200017c5[r0_1]++).
  eTaskGetState: `sl9 = 0;` ... `sl9 = 2;`; strncpy: `*(volatile u8 *)r3 = 0; r3++;`. On the eight
  builds (a32, m3, m4, rv32, GCC -Os, clang -O2, RISC-V, GCC -O0): `0x0u` 20, 13, 15, 0, 256, 271, 0,
  250 -> 0, 0, 0, 0, 4, 7, 0, 7 and `0x1u` 13, 9, 9, 5, 180, 241, 115, 185 -> 0, 0, 0, 0, 4, 2, 0, 3 (what
  is left is ~0x0u and ~0x1u, masks and the compare idiom's, and the lifter's text of variadic functions'
  bodies, printed as it was written); casts of a literal 3, 5, 5, 1, 90, 113, 6, 48 -> 0, 0, 0, 0, 0, 0, 0, 1; `+ 0` 15, 33, 7 -> 0,
  1, 3 on GCC -Os, clang -O2, GCC -O0 (variadic bodies); cast chains 0, 3, 3, 1, 63, 22, 7, 62 -> 0, 0,
  0, 0, 0, 0, 1, 2; `x = x ± k` 26, 29, 32, 26, 155, 169, 132, 153 -> 0, 0, 0, 0, 2, 2, 2, 6 (x++ and
  x-- 25, 25, 27, 23, 137, 135, 100, 186, other compound assignments 3, 5, 7, 5, 52, 65, 54, 44); value
  casts 73, 67, 69, 74, 1,475, 1,136, 780, 1,151 -> 46, 39, 42, 47, 1,110, 780, 553, 803. Left: `(u64)0`
  in the negs idiom (t = (u64)0 + ~x + 1, 54 on GCC -Os) goes with "Flags as comparisons"; x = x + f()
  with a call stays (the call in plain sight); (u32)(u8)r1 << 5 stored into a byte keeps its inner cast
  (a shift is no low-bits operation for the pass).
- [x] **Names without their type.** DREAD's default names for narrow accesses carry the type
  (g_20001830_u8), and the naming model keeps the suffix in what it proposes. Strip a trailing type
  suffix from a proposed name that has other words (the answer's check), or show the question's
  defaults without it. Public example: the defaults of FreeRTOS mps2-gcc-Os (g_20001830_u8); whether
  the model keeps them on FreeRTOS was not checked (no pilot names in the bench copies).
  Done 2026-10-24 (dread.naming untyped, in dread pilot's name_globals; docs/decisions.md, "Functions'
  and texts' addresses in readable C", which records this one too). A proposal that ends in a type of
  the default names (_u8, _s16, ... _s64) and has other words is kept without it (uxTimerFlags_u8 is
  uxTimerFlags), unless the name without it is taken or another proposal of the same question (a word
  and its byte at one address: state and state_u8 stay apart); the questions are as they were (two views
  of one address would show one name). Measured with dread pilot --task globals, Haiku, on two copies of
  FreeRTOS GCC -Os ($0.03 and $0.02): with the check's readable_types plan 16 default names with a type
  were shown and no proposal kept it; without a plan (no structures of static data: every narrow access
  a name of its own) 19 were shown and 3 proposals kept it (the NVIC's priority bytes, io_e000e400_u8 as
  nvic_priority_u8, io_e000e408_u8 and io_e000e409_u8), stored as nvic_priority, nvic_ip_irq2 and
  nvic_ip_irq3; neither table holds a name that ends in a type. Tests: tests/test_naming.py (untyped;
  name_globals with a played model: a suffix stripped, kept where it tells two views apart or the name
  without it is a function's). Left: the variables' and structures' questions show no such defaults
  and get no such check; how often other models keep the type, and on the review's private target,
  is not measured here.
- [x] **Radix by use.** Counts, delays, sizes and line numbers print in hex, and a value compared in
  decimal can be stored in hex in the same function. Print a constant in decimal where it is a count,
  a multiplier, a size compared with decimal constants or stored beside them, unless it looks like a
  mask (a run of ones, one bit) or an address. Public example: FreeRTOS mps2-gcc-Os
  prvCheckExpectedTimeIsWithinAnAcceptableMargin (`g_200009d0 = 0x2f7u;` for `xErrorOccurred =
  __LINE__`, line 759).
  Done 2026-10-23 (dread.polish, with "Negative constants by use"; figures there for the gate):
  decimal where the use is a number: a divisor, a modulus, a multiplier (not a division's reciprocal,
  x * 0x5556u >> 16), a shift's count, an ordered bound, and the places those make numbers, places
  counted, places given or compared with two or more distinct plain numbers in the function; never a
  pointer (a counted address: r6 = 0x908u; *r6; r6++), a device register, a constant of 0x10000 or more
  or a run of eight ones; evidence from bit patterns (0x55) or from printable characters only (a pad
  character's 0x20 and 0x30) counts for nothing. prvCheckExpectedTimeIsWithinAnAcceptableMargin:
  `g_200009d0 = 759;` and `767`, the demos' other __LINE__ stores likewise, errno values 22 and 29,
  bounds such as `r0_1 <= 21`, `while (r6 != 200)`, `r4_1 - 150 * (r4_1 / 150)`. On the eight builds
  hex constants of 10 or more 62, 72, 79, 74, 1,739, 1,948, 1,629, 1,679 -> 46, 47, 54, 40, 1,481,
  1,693, 1,330, 1,482, decimal ones 19, 13, 13, 14, 378, 289, 126, 217 -> 25, 22, 22, 24, 527, 464, 282,
  324; lines where a hex constant became decimal or a character 14, 15, 15, 14, 165, 200, 143, 110, read
  against the source on gizmo and FreeRTOS GCC -Os. Left: a place stored once in its function stays
  hex though other functions store it in decimal (prvControllingTask's line number on GCC -Os: an
  image-wide pre-pass, like the readable_types plan, would carry the evidence); call arguments (memset's
  sizes, vAssertCalled's lines) stay hex: a size and an address cannot be told apart by the call alone.
- [x] **Addresses of functions and texts by name.** A function's entry (with the Thumb bit) or the
  address of a string in the image passed or stored as a value prints as a number. Print the
  function's name (`&f`) and, for text, a comment with the string or a named constant. Public
  examples: FreeRTOS mps2-gcc-Os vStartTimerDemoTask (`xTimerCreate(0xd9cu, ..., 0x913du)`: the timer
  name "FR Timer" and prvAutoReloadTimerCallback; `vAssertCalled(0xd3fu)` passes the source file's
  name); gizmo a32 print_status (`uart_puts(0x20d35u)` for "mode ").
  Done 2026-10-24 (dread.constants Pointed; dread.naming apply_globals' pointee, function_use and
  text_use, taken_kinds; the readable_types table's `takes`; dread.h FUNCTION_AT; cparse.Num's note,
  which dread.polish keeps; dread pilot's name questions note a text once; docs/decisions.md,
  "Functions' and texts' addresses in readable C"; docs/formats.md). A function's entry as code takes
  its address (bit 0 set for Thumb code; its even entry, and an address inside a function, are none) is
  the function's address through a macro of dread.h that is the same number, `(u32)&prvTimerCallback`
  with `#define prvTimerCallback FUNCTION_AT(0x6ed5u)` above the function: `&f` of f's declaration would
  be the place of f's own C, which the overlay moves (prvAutoReloadTimerCallback's lifted C is 112 bytes
  for 80 on GCC -Os); where the file calls the function too, or is it, the number keeps `/* &name */`
  (newlib's fflush hands _fflush_r to _fwalk_sglue and calls it). A text's start keeps its number with
  the text in a note, `0xd9cu /* "FR Timer" */` (a C string literal, cut to its start and its end beyond
  48 characters, "initially" in front where the startup fills that memory): a named constant would be a
  name made from the text, and a string literal another copy. Where: a text only as an argument, for a
  parameter that takes texts (taken_kinds: two distinct constants or more from the calls, every one a
  text, more than half of them beginning behind a NUL) or that the callee dereferences (dread.shapes
  pointer_params) where the text begins there; a function's for a parameter that takes functions (every
  constant a function's entry, or one the callee calls through) or that the callee dereferences, what a
  call through a constant goes to (gizmo's veneer: `((u32 (*) ( u32 , u32 , u32 , u32 ))&ram_crc)(r0,
  r1, r2, r3)`), and a value stored into memory unless it looks like a bit pattern. Static data's rules
  were tried first and annotated numbers on FreeRTOS: its texts lie at 0x100 to 0x1300, where the demos'
  line numbers do, and GNU ld merges the ends of strings, so a line number stored into an error variable
  mostly pointed inside a text (GCC -Os: 109 of 234 texts annotated so lay inside another, all but six
  of them numbers). The forms: vStartTimerDemoTask `r0_1 = xTimerCreate(0xd9cu /* "FR Timer" */, r4 *
  g_200017a8, 1, r3, (u32)&prvAutoReloadTimerCallback);`, `vAssertCalled(0xd3fu /*
  "...Minimal/TimerDemo.c" */)` (the source file's path, its start and its end),
  `xTaskCreate((u32)&prvTimerTestTask, 0xdb3u /* "Tmr Tst" */, 0xa0u, 0, 4, 0)`; newlib's std `r0->f_24
  = (u32)&__sread;`; print_status `uart_puts(0x20d35u /* "mode " */);`, main `apply_config(0x20db7u /*
  "volume = 7\nmode=2\n  baud = 0x2580\nname=3\n" */);`. Measured on copies of the eight builds (gizmo
  a32, m3, m4, rv32; FreeRTOS GCC -Os, clang -O2, RISC-V GCC -Os, GCC -O0; main at fa47c42 -> this): the
  gate accepts the same functions, name by name (35, 36, 44, 31; 317, 275, 252, 354), the readable_types
  tables the same but the new `takes` (17, 13, 14, 11 parameters on FreeRTOS, of them 5, 5, 4, 5
  functions'; 2 texts' on each gizmo build: uart_puts and strcmp); every function's digest the same (the
  names of the readable_names table apply as they did); the readable C of 4, 4, 5, 3; 163, 138, 130, 155
  functions changed, every difference a note or a function's name (taken out again, the files are main's
  line for line). Over every lifted function, texts noted 11, 11, 12, 11; 413, 365, 306, 335 (in the
  readable functions 399, 355, 295, 335 on FreeRTOS), functions named 1, 1, 1, 0 (the veneer); 99, 97,
  79, 99 (90, 88, 77, 90 as arguments, 9, 9, 2, 9 stored), the number kept with `/* &name */` 0; 1, 1,
  0, 1; each name is a cast more in dread.cmetrics' count (`(u32)&name`: the externs take a u32 there),
  the notes change no figure. The check's wall time on the shared machine 7, 4, 5, 3; 389, 271, 139, 448
  s -> 5, 4, 4, 3; 347, 224, 120, 399 s (noise; Pointed takes 0.005 s and taken_kinds 0.05 s on GCC -Os
  and -O0; on GCC -Os, two rounds alternating main and this, making the plan 12.1 and 11.7 s -> 12.2 and
  11.8 s, every lifted function's readable C in a pool of eight 5.7 and 5.7 s -> 5.7 and 5.8 s). Tests:
  tests/test_addresses.py (the rules on synthetic C with what lies at each constant given; taken_kinds;
  the note printed and kept by dread.polish; Pointed on an image with Thumb functions, texts in a rom
  region, a merged string's end, the loader's and the startup's bytes, code; a program of our own for
  ARM state, Thumb-2 and RISC-V through the gate, with a timer-like function that only stores a name and
  a callback, a stored callback, and a function's and a text's address handed to a count, which stay
  numbers; gizmo's print_status and main on every build; the name questions); also with Ubuntu 22.04's
  compilers. Left: a text given to a parameter that gets the same text at every call and does not
  dereference it stays a number (xTimerCreateStatic's "T1"); a text plus an index (`0x908u + r4`) and a
  text or a function assigned to a variable stay numbers; a text in memory an object of static data
  covers is written as the object's address, without the text; a stored number that is a function's
  entry by chance is named where an image's code lies where such numbers do (none on the bench).
- [ ] **Initialised local arrays.** A byte-copy loop from constant data in the image into the frame
  (how some compilers initialise a local array) prints as a do-while over integer pointers. Make it a
  local array with its initialiser read from the image (or a copy helper's call). Public example: none
  found (GCC calls memcpy or copies words); test: gizmo assembly.
- [x] **A count of leading zeros shifted by five is a test for zero** (2026-10-26, dread.polish _zero_test).
  GCC -Os on Cortex-M tests a value for zero as clz r0, r0; lsr r0, r0, #5, which the lifter writes
  (x ? (u32)__builtin_clz(x) : 32) >> 5: 1 where x is 0, 0 elsewhere. It prints x == 0 now
  (prvIsQueueEmpty's `return r4 == 0;`). Main -> this on the eight builds: the gate accepts the same
  functions; FreeRTOS GCC -Os 50 such expressions -> 0 (100 lines in 13 files), clang -O2 15 -> 0;
  gizmo, RISC-V (no clz) and GCC -O0 unchanged. Left: clz's other uses (31 - clz, the highest set bit)
  keep their form.
- [x] **Characters as characters.** Bytes from char loads compared with printable codes print as hex
  (`0x20u`, `0x3du`, `0xdu`). Print a printable or common-escape constant compared with or stored
  from a char-typed value as a character literal. Public examples: gizmo a32 parse_kv (`0x20u`,
  `0x3du`), read_line (`0xdu`, `0xau`); FreeRTOS mps2-gcc-O0 tiny_print (`0x25u`, `0x2du`, `0x30u`).
  Done 2026-10-23 (dread.polish, with "Negative constants by use"; figures there for the gate): a
  printable or common-escape constant is a character literal where the function compares values that
  hold characters (bytes loaded from memory, variables given only those, callees' results stored as
  bytes, copies of them) with two or more distinct such codes: compared with such a value, added to it
  or subtracted from it (`c - '0'`, `(r5 & ~0x20u) - 'A'`), stored into a byte, a digit's character
  stored (`d + '0'`), given to a variable that gets only character codes (tiny_print's letter base:
  `r0_2 = 'A';`, `'a'`); 0 stays 0, as gizmo's source writes `buf[n] = 0`. parse_kv: `' '`, `'\t'`,
  `'='`; read_line: `'\r'`, `'\n'` (on RISC-V through a copy of uart_getc's result); tiny_print:
  `'%'`, `'-'`, `'0'`, `'c'`, `'d'`, `'u'`, `'x'`, `'X'`. Character literals on the eight builds 0 -> 11,
  11, 11, 12, 12, 23, 18, 13. Left: a character that arrives as a parameter is a number in its function
  (gizmo's handle_key: `r0 != 115`, `r3 = r0 - 98`, consistent with the decimal bounds the conditions
  pass prints there, where the source switches on 's' and 'b': evidence from the callers, an image-wide
  pass); prints()'s pad character on GCC -O0 (`r8 = 0x20u`, `0x30u`) stays hex: that function compares
  no characters.

### From a second review (2026-10-24)

The same sample of the private real-life test, reviewed again after the day's fixes: what DREAD itself
prints false to a reader is down to the two-word results (the open item "Two-word results that are
leftovers", now the first to take), the false layouts, phantom arguments and addresses for plain
values are gone, constants and types read as a person writes them, and the bodies are as long as
before (the fixes changed what lines say, not how many there are). Extensions and bit fields are now
the most frequent thing a reader decodes by hand. Two of the day's printing rules made a few lines
worse (masks as negative numbers, one object in two radices), and the frame objects were blocked by
the pilot's names, which the first review had taken for raw frames. New items:

- [x] **Frame objects under the table's names.** dread.frames finds the frame array and its end by their
  names (`frame`, `sp0`) and runs after the readable_names table is applied. Wherever a naming run named
  either, no objects are made: not in the printed C, not in the compiled C the gate builds, not in the
  next naming question (which then names the raw array again). On a private firmware this caused raw
  frames in reviews, including those the 2026-10-23 review reported, and turned a frame that was one
  object under the earlier names into raw offsets. Make the objects before applying the names (drop the
  table's names for `frame`/`sp0`, or map them onto the object when the region is one), or find the
  array by identity rather than by name; and show the pilot the objects so stackN gets a name. Public
  reproduction: gizmo a32 print_status with a readable_names entry, made for its C, naming only `sp0`
  (`buf_end`). `u8 stack20[12]; fmt_dec((u32)stack20, ...)` becomes `u32 frame[6]; u32 buf_end =
  (u32)&frame[6]; fmt_dec(buf_end - 20, ...)`. Test: that entry through the printer and the gate on
  every gizmo build.
  Done 2026-10-24 (docs/decisions.md, "The frame's objects before the variables' names, and in the naming
  question"). dread.readable makes the objects before it applies the names (_framed, then _named), on
  the lifters' names as before, taking no name the entry gives; a name of the frame array or sp0 is
  dropped where the objects took them away, as is one of an object the C has not (dread.frames
  table_names); the naming question shows the objects (question_tree) and keeps their identities, the
  offset (object_identities), so their names carry; dread pilot asks an entry made from a question that
  showed the frame raw again for its objects, its other names shown (unasked); the check asks
  dread.frames whether a failing function has objects before it tries its region whole (it looked for
  stackN in the C it built). The digest an entry is made for is taken before the objects as always, so
  every entry applies to the same C as before. Measured on the eight builds with a table a script made
  for main's C, naming sp0 or the frame array in two thirds of the functions whose question showed
  them: main lost the objects of every such function (4 on each gizmo build; 37, 35, 30 and 36 on
  FreeRTOS GCC -Os, clang -O2, RISC-V GCC -Os, GCC -O0), the fix makes as many as without the table (5;
  57, 53, 45, 56); the compiled C without names is main's byte for byte; the gate accepts the same
  functions with the table on main and with the fix as without it, set for set. With Haiku on GCC -Os
  ($0.14) the model named sp0 or the frame array in 35 of the 55 functions whose question showed the
  frame raw; under that table the fix asked the 52 with objects again ($0.03) and named 68 of their 74
  objects, 7 of them as the source does (1 of the 49 names of frame and sp0). Names the fix's question
  made on an older commit's C carried to main's: the objects' 21 in 12 functions, none dropped. Tests in
  tests/test_frames.py (the entry through the printer and the gate on every gizmo build, the question,
  the carry, the pilot asking again, the whole region under a name), also with Ubuntu 22.04's compilers.
  Left: dread trace's frame view finds objects by their default names (stackN at sp0 - N), as it finds
  slot variables (sN): one the table names is shown without its label there.
- [x] **Masks stay masks across calls.** "Negative constants by use" prints an all-ones or `~N` value as
  a negative number when it is passed to a prototyped parameter or held in a variable that a shift also
  reads, even where the value is a mask. Keep `~N` (or hex) where the callee uses the parameter only as
  an operand of &, |, ^, ~ or bic (from its readable C, or from its machine code when it is not lifted),
  and for a variable read only by bit operations and shifts. Public examples: FreeRTOS mps2-gcc-Os
  prvNotifiedTask_73ac, `xTaskGenericNotifyWait(0, -1, 0, (u32)&stack40, 0x64u)` for
  `xTaskNotifyWait(notifyUINT32_MAX, ...)`, whose callee clears `&= ~ulBitsToClearOnEntry` (printed on
  request; not in that build's lifted table). gizmo a32 ram_crc, `r3 = -1;` for `crc = 0xFFFFFFFFu`.
  Done 2026-10-24 (dread.polish mask_params and the variable rule; the readable_types table's `masks`,
  dread.readable mask_plan; docs/decisions.md, "Masks across calls, one radix per object, signed
  variables from negative values"; docs/formats.md). A parameter is a mask where every read of its value,
  also through copies, is an operand of &, |, ^ or ~ (also &=, |=, ^=), the value returned as it is (a
  void function's leftover register), or an argument for a callee's mask parameter; a store, a
  comparison, a shift (FreeRTOS's stack depth `<< 2`) and a shift's count the lifters mask (`1 << (r0 &
  0xffu)`) are none. Read from every lifted function's C and from the C of the callees outside them that
  a lifted function passes a constant that would read negative (lifted on their own: on GCC -Os
  xEventGroupSync, xQueuePeek and xQueueSemaphoreTake, whose -1 is a tick count; the plan's masks take
  1.6 s there). A variable read only by bit operations and shifts gets no negative number either
  (compound assignments now count as reads: x ^= y a mask's, x++ a number's); all ones as a mask prints
  0xffffffffu whatever the lifter wrote, another complement keeps ~N (RISC-V's li writes 0xffffffdfu).
  prvNotifiedTask_73ac (printed on request): `xTaskGenericNotifyWait(0, 0xffffffffu, 0, (u32)&stack40,
  0x64u)`, `xTaskGenericNotifyWait(0, 0, 0xffffffffu, ...)` for ulBitsToClearOnExit,
  `ulTaskGenericNotifyValueClear(g_20001760, 0, 0xffffffffu)`, while xTaskGenericNotify's ulValue
  (stored) and every portMAX_DELAY stay -1; gizmo's ram_crc `r3 = 0xffffffffu;` on every build (a32's
  ~0x0u and the others' -1 before). Measured with the other two items on fresh copies of the eight builds
  (gizmo a32, m3, m4, rv32; FreeRTOS GCC -Os, clang -O2, RISC-V GCC -Os, GCC -O0), main at c5cc30e ->
  this, every function's readable C as `dread readable --out` prints it: the gate accepts the same
  functions, set for set (35, 36, 44, 31, 317, 275, 252, 354); the readable C of 8, 9, 10, 6, 52, 46, 41,
  47 functions changed, every digest as before (the readable_types entries are made for the same C: names
  apply and carry as they did); every GCC -Os function's readable C in one process 36.1 and 37.2 s of CPU
  on main, 37.9 and 35.7 s with this (noise; the plan's masks 1.6 s there); the check's times moved
  within the noise of a shared machine (GCC -Os 370 -> 337 s, GCC -O0 471 -> 420 s). Negative call
  arguments 0, 0, 0, 0, 96 -> 65, 112 -> 88, 100, 95 -> 65; 0xffffffffu 0 -> 1, 0 -> 1, 0 -> 1, 2 -> 3, 1
  -> 32, 0 -> 24, 24, 2 -> 32 (the rest of the -1 arguments are tick counts, portMAX_DELAY). Tests
  (tests/test_polish.py): the variable rule and a callee's mask parameters, mask_params on small
  functions (copies, &=, a callee's mask handed on, a shift, a count, a return), random functions
  compiled both ways with a mask parameter, gizmo's ram_crc on every build, a program of our own through
  the gate (ARM state, Thumb-2, RISC-V; also with Ubuntu 22.04's compilers). Left: RISC-V's
  xTaskGenericNotifyWait keeps its registers around the ecall of a yield and writes a1 again, and a
  parameter written in its function is no mask by this rule (its callers keep -1: the reads its entry
  value reaches would tell, dread.cflow); a callee that neither lifts nor gets a negative-looking
  constant from a lifted function says nothing (a function printed on request passes -1 to it); what a
  callee does with a value it stores is not followed.
- [x] **Truth values as one comparison.** A dispatch by successive subtraction that sets a result per
  case (`mov r0, #0; subs r1, r1, #1; blo a; beq b; ... a: mov r0, #0 ... b: mov r0, #1`) prints `r0 =
  0; if (x == 0) r0 = 0; else if (x == 1) r0 = 1;` for `return x == 1`. Drop an arm's store of a value
  the variable already holds on every path into it, then turn `v = 0; if (c) v = 1;` into `v = c`.
  Public example: none (GCC uses no branches here). Test: that ARM-state sequence through the conditions
  pass and the gate, as tests/test_flags.py does.
  Done 2026-10-25 (dread.tidy held, truths with _truth_tree, _formula and _truth_of, call_addresses;
  conditions' search shared as simpler_truth and smallest_truths, which now also takes a truth value
  compared with 0 for itself; dread.structure calls truths last; docs/decisions.md, "Truth values of ifs
  that store 0 and 1: one comparison, proved; a store of the value held dropped"). held drops an arm's v =
  K (behind a label of the clean-up's flat view) where every definition of v that reaches it stores K and
  the ways back pass nothing with an effect (not a declaration's initial value, nor the zeros the lifters
  give the registers a call leaves). truths makes v = K; and an if-tree behind it that stores only 0 and 1
  into v (conditions that do not read v; the first may load or call once) v = the truth value they make:
  written in the conditions' order (c ? 1 : 0 as c, c ? x : 0 as c && x, ...), as small as the conditions'
  search proves it, without operands the others imply, the whole proved by dread.cexpr; in the clean-up
  for conditional moves, as dread.structure's last step for the ifs of branches; the new statement keeps
  the first store's instruction, the ifs' absorbed, the arms' stores as copies (dread.naming identities:
  names carry). The item's sequence in ARM-state assembly of our own (tests/test_flags.py TRUTHS_S) prints
  `r0_1 = r1 == 1;` (before `r0_1 = 0; if (r1 == 0) { r0_1 = 0; } else if (r1 == 1) r0_1 = 1;`), with a
  case more `r0_1 = r1 == 1 || r1 == 2;`, a value cleared on the other way `r2 = r1 == 3;`, a conditional
  move of 1 after 0 `return r0 + (r1 > 5);`; a select of 7 stays an if; the gate accepts all. On fresh
  copies of the eight builds (gizmo a32, m3, m4, rv32; FreeRTOS GCC -Os, clang -O2, RISC-V GCC -Os, GCC
  -O0; main 6f015bc -> this; the gate run with --verify, which passes on all eight): the gate accepts the
  same functions (35, 36, 44, 31; 317, 275, 252, 354) and writes the same readable tables; the shape (v =
  0 or 1 and ifs behind it that store 0 and 1) in the lifted tables' functions 0, 0, 0, 0; 0, 3, 0, 8 ->
  0, 0, 0, 0; 0, 0, 0, 4 (the four left on GCC -O0 load between the store and its if, and clear the flag
  in a run of such ifs: `s12 = 1; r2 = g_20000928; if (r2 == g_20000a00) s12 = 0; ...` in
  xAreAbortDelayTestTasksStillRunning and three alike). The printed C of 9 functions changed (every
  function that lifts: 0, 0, 0, 0; 0, 5, 0, 4): on clang -O2 printi (`r4 = 0; if ((s32)r1 >= 0) r4 = 1;`
  folded into its reader, `lr_1 = ((s32)r1 >= 0) | ...`), prvInterruptTriggerLevelTest (`if (c && !z_1) r8
  = 1;` for `r0_3 = 0; if (c && !z_1) r0_3 = 1; if (r0_3 != 0) r8 = 1;`), vTaskPrioritySet (`r6 = r5 <=
  *(volatile u32 *)(r1_1 + 44);`), xStreamBufferIsFull and xTaskPriorityInherit (`return r1_1 <= (r12 & 1)
  << 2;`, `return r0_1 < *(volatile u32 *)(r1_5 + 44);`); on GCC -O0 prvTestWaitCondition (`s12 = r2 == 0
  ? (r1 & r0) != 0 : r1 == (r1 & r0);`), xAreCountingSemaphoreTasksStillRunning (`s12 = g_20000a44 ==
  0;`), xQueueGenericCreate and xQueueGenericReset (`r1_1 = (u64)r0 * r1 > 0xffffffffu;`, the size's
  overflow test). held drops a store in three functions (GCC -Os's prvTestSlaveTask, RISC-V's
  eTaskGetState and prvSanityCheckCreatedQueue) whose printed C stays the same (the clean-up took out
  later what it dropped). Names made for the C before carry (tables naming every variable of the 9
  functions): 109 of 113, the 4 dropped those of the variables whose truth value went into its one reader.
  held and truths took 0.85 and 0.3 s of every lifted function's readable tree on GCC -Os and 0.44 and 0.2
  s on GCC -O0 (one process, CPU time, on main bc223f9 with the same code), less than the totals move from
  run to run on a machine shared with other runs. Tests (tests/test_flags.py): that program through the
  gate, held on a small function (the arm's store and one reached by 5 alone go; one reached by 6 too, one
  reached by a parameter stay; the store's instruction kept as a copy, the identity unchanged), truths on
  snippets (else-if chains, ifs inside, an operand implied, a statement between, a call or a load in the
  first condition; kept: other constants, an arm that does more, a call in an inner condition, two loads,
  a condition reading v, a call or a read of v between), the identity kept, random functions of stores and
  if-trees through truths and of stores and branches through held compiled beside the originals (host cc,
  random and boundary arguments), a truth value compared with 0; with Ubuntu 24.04's and 22.04's compilers
  too. Left: ifs that store other constants (`v = 0; if (c) v = 7;`: a select of constants would be
  another rule); a store and its if with a load or a call between (GCC -O0's
  xAreBlockTimeTestTasksStillRunning: `s12 = 1; r2 = g_20001860; if (r2 == g_2000186c) s12 = 0;`), a run
  of ifs that each clear a flag; `v = x; if (v != 0) v = 1;` (clang's subs; movne: `if (r6 != 0) r6 = 1;`
  in printi, vTaskPrioritySet); a truth value of branches' ifs folded into its one reader (nothing cleans
  up behind the structure: `r0_1 = r1 == 1; return r0_1 + r0;`); held where the value is kept through
  other work (left alone on purpose).
- [x] **Signed variables from negative values.** Since negative constants print as such, `u32 ret = -10;
  ... return ret;` is common. Type a variable s32 when it is assigned negative constants and read only
  by ==, by return or by signed comparisons (and the result likewise), where that adds no casts. Public
  example: gizmo a32 ring_get, with the readable_types table made as the check makes it. It prints `u32
  ring_get(u32 r0)` with `u32 r0_1; ... r0_1 = -1;` for `int ring_get(...)`, while main's variable for
  its result is s32 (`if (r0 >= 0)`) and uart_getc, which has the same shape, prints `s32
  uart_getc(void)`.
  Done 2026-10-24 (dread.vartypes negative_variables, returns_negative, read_as_negative, Facts.reads and
  result_kinds, local_types, Planner.decide_outside; docs/decisions.md, as above; docs/formats.md). A
  variable given a constant readable C prints negative and read only by == or !=, a truth test or return
  is s32 where that adds no casts (the plan's trial); one the function returns only where the function's
  result is s32 (a u32 function's s32 variable would contradict it). A result is s32 where its own
  returns give negative values and a caller reads it, every caller only so (==, return, a signed
  comparison, or a variable read only so: the reads the call's definition reaches, dread.cflow); without
  a caller that reads it a negative return is no evidence (gizmo's udiv_var returns 0xFFFFFFFFu beside an
  unsigned quotient: u32 as its source). A callee the plan does not analyse (not lifted) whose result an
  analysed caller compares signed is s32 in the table (`prototypes`, its result alone), and such a
  function gets the negative variables on its own C. ring_get on every gizmo build: `s32 ring_get(u32
  r0)` with `s32 r0_1; ... r0_1 = -1;` (RISC-V returns -1 directly), main's `extern s32 ring_get(u32);`;
  uart_getc's `s32 r0;`, read_line's `s32 r0_1, r0_2;`, cmd_mode s32 on the ARM builds (apply_config
  reads it by return), parse_kv on Thumb-2 (-1, -2, -3). FreeRTOS: newlib's _close, _lseek, _read and
  _write (they return -1: `extern s32` in their _r wrappers), xTaskCreate (`s32 r0_2` for
  errCOULD_NOT_ALLOCATE_REQUIRED_MEMORY). On the eight builds (figures for the gate with "Masks stay
  masks across calls"): u32 variables given negative constants 10 -> 6, 7 -> 2, 7 -> 2, 6 -> 4, 5 -> 4,
  12, 5 -> 4, 6 -> 5 (what is left is read otherwise: an index, a sum, a store), s32 variables 4 -> 7, 4
  -> 8, 5 -> 9, 7 -> 8, 26 -> 27, 27, 15 -> 16, 27 -> 28, s32 results 2 -> 4, 2 -> 5, 2 -> 5, 2 -> 5, 1
  -> 6, 1 -> 6, 0 -> 1, 1 -> 6; rv32's parse_uint, compared signed by parse_kv and not lifted, is s32 as
  its source (`parse_uint(...) < 0 ? -3 : 0`, no cast). Tests (tests/test_vartypes.py): the rule's
  variables and results on small functions (a saturated sum stays u32), a callee outside the plan, random
  callees and callers typed and compiled in 32 bits each in a file of its own, gizmo's ring_get,
  uart_getc, udiv_var and cmd_mode on every build; with Ubuntu 22.04's compilers too. Left: a negative
  value read only by == may be a u32 sentinel (0xFFFFFFFFu compared reads the same); a function only
  called through pointers keeps u32 (no caller reads it: RISC-V's cmd_mode).
- [x] **Old tables' globals without the type suffix.** Done 2026-10-25 (dread.naming plain_globals,
  load_globals): the pilot takes the default name's type off its answers since 2026-10-24, but a table
  named before keeps names like g_play_state_u8, which the private real-life test's readable C showed
  widely. Where names are shown, such a name is used without the suffix when the
  name without it is unique in the table, no default name and no function's; two views of one address
  named apart only by it keep it. The file is not rewritten (the pilot works on it as it is).
- [x] **Why a variable has its type.** Done 2026-10-25 (dread readable --types NAME..., vartypes.explain):
  per function the table's types and whether they were made for this C, the variables the plan may type
  and why each other one may not (eligible's reasons: declared otherwise, initialised, an asm operand,
  its address taken, the lifter's stack arguments, the frame's), the pointer groups with their accesses,
  and every candidate in the plan's order with the casts of what it touches without and with it, kept
  or not; the trials run as the plan's last round does (the C as built, the table's prototypes). For
  finding why a variable on a real firmware stays an integer without reading the plan's code; the private
  real-life test has such variables used directly as addresses.
- [ ] **Pointers held in globals; frame objects to all-u32 externs.** These are "Left" of two done items
  and have no item of their own. A global holding a structure pointer stays a u32 macro, cast through a
  fresh variable at each use, or read through a byte pointer and an index. A frame object passed to an
  all-u32 extern goes as `(u32)&stackN`. Type the global as a pointer to the structure its readers use
  (`g_x->f` at each use); give an extern's parameter the frame object's pointer type when every call in
  the file passes such an object or 0. Public examples: FreeRTOS mps2-gcc-Os
  prvAddCurrentTaskToDelayedList (pxCurrentTCB cast at six uses; `uxListRemove(r0_1 + 4)` for
  `&pxCurrentTCB->xStateListItem`); gizmo a32 print_status (`fmt_dec((u32)stack20, ...)`).
  Globals: done 2026-10-25 (dread.shapes pointer_globals, the readable_types table's global_pointers,
  apply_types; docs/decisions.md, "Words that hold a structure's pointer"): a word at a fixed address that
  every typed variable loading or storing it takes as a pointer to one structure is declared as that
  pointer (`#define g_20029aac (*(volatile struct s_8 *volatile *)0x20029aacu)`), a typed variable takes it
  as it is, members and member addresses go through it (`&g_20029aac->f_4`), other uses are `(u32)`. On
  the eight builds the gate accepts the same functions; casts of such words to a structure pointer 53 ->
  13 (GCC -Os), 50 -> 15 (clang -O2), 52 -> 47 (GCC -O0), 31 -> 31 (RISC-V GCC -Os, whose globals are
  reached through a base register), while (u32) casts of them 0 -> 31, 40, 1, 0: nearly all of those are
  pxCurrentTCB read into a variable that a call takes as a u32 (`uxListRemove(r0_1 + 4)`), whose
  parameter is not typed. Left: frame objects to all-u32 externs (gizmo a32 print_status's
  `fmt_dec((u32)stack20, ...)`; uart_puts there also takes texts, so "every call passes such an object"
  holds for fmt_dec only), and typed parameters of the list functions, which would take the casts above.
  Frame objects: done 2026-10-25 (dread.vartypes object_arguments, called behind dread.frames in
  readable._framed and question_tree): the plan typed a file's externs before the frame's objects existed,
  so their addresses went as `(u32)&stack36`; now a parameter the extern still declares u32 takes the
  callee's own pointer type where every call passes an object's address of the type it points to (an
  array for its elements, a structure's for the structure) or 0, and those casts go: gizmo's
  `fmt_dec(stack20, ...)` (uart_puts, whose own C takes an integer, keeps `(u32)stack20`), FreeRTOS's
  `xTaskDelayUntil(&stack36, 0x1388u)`, `strcmp(stack40, stack56)`, `memset(stack40, 0, 0xfu)`. On the eight
  builds the gate accepts the same functions; `(u32)&` 1, 2, 2, 1 (gizmo, as before), 400 -> 315 (GCC -Os),
  498 -> 416 (clang -O2), 385 -> 363 (RISC-V GCC -Os), 465 -> 445 (GCC -O0); lines with a `(u32)` 37 ->
  33, 31 -> 27, 34 -> 30, 33 -> 29, 1,115 -> 1,007, 1,013 -> 897, 746 -> 725, 948 -> 927. Tests:
  tests/test_vartypes.py test_objects_go_to_callees_as_the_pointers_their_own_c_takes (an array, a
  halfword, a structure; 0 beside them; a parameter a call passes an integer, a pointee of the other
  sign), tests/test_frames.py (print_status's fmt_dec, the callee's structure).
- [x] **An access through a field's address, as the field.** A lifter's `[r1]` after `r1 = p + k` is
  `*(volatile T *)(p + k + 0)` once the stages substitute r1; where k is the place of a field of the
  structure p points to, dread.shapes writes the address as the field's (`&p->f_4`, the members for derived
  pointers) and dread.vartypes wrote the access as the dereference of that address: `*&p->f_4 = v;`. Seen
  on the private real-life test (a run of halfword stores through stepped pointers); no public build printed
  it (their compilers put the offset into the store). Done 2026-10-25 (dread.vartypes _deref: the
  dereference of an address is the object, `*&x` is x, in stores and loads alike; tests/test_vartypes.py
  test_an_access_through_a_field_s_address_is_the_field). On the eight builds the readable C is the same,
  line for line (none printed it); on the private real-life test the stores print as members.
- [x] **Masks moved into place by a shift.** dread.polish mask_params takes any shift of a parameter as no
  mask use (`x << 2` scales a count as often as it moves bits), so a callee that moves its mask before one
  of its uses (`(keep >> 8) & REG` in one arm, `keep & REG` in another) has no mask parameter, and its
  callers' constants print as negative numbers (`-16` for `~0xfu`, `-1` for all ones). A shift whose value
  is only an operand of &, |, ^ or ~ (or of &=, |=, ^=) keeps the mask use. Seen on the private real-life
  test (a port's read-modify-write and its callers); public example: none checked. Test: such a callee and
  callers passing ~0xfu and 0xffffffffu, through the gate on the three architectures.
  Done 2026-10-25 (dread.polish _masks_of: a shift in a mask's place reads its operand "moved"): a
  parameter shifted where the shift's value is an operand of &, |, ^ or ~ is allowed beside its mask uses,
  but is none itself, so a count scaled and masked ((n << 2) & 0x3cu) stays a number; the port function's
  keep and set values are masks, its callers write `~0xfu` and `0xffffffffu`
  (tests/test_polish.py test_a_mask_moved_by_a_shift_stays_a_mask, which fails on the previous rule). The
  gate test was not needed: the change writes the same values. On the eight builds the gate accepts the
  same functions and no line changes (none moves a mask before using it).
- [x] **A byte pointer parameter as a void pointer in the externs** (2026-10-26; docs/decisions.md "A
  callee's byte pointer as void * in its callers"). A callee that copies what it is handed byte by byte
  (FreeRTOS's xQueueGenericSend: its item goes to prvCopyDataToQueue and memcpy) takes `const volatile
  u8 *` in its own C, and a caller that hands it the address of a u32 local or of a structure got
  `xQueueGenericSend(q, (u32)&stack4, 0x8cu, 0)` with the extern all-u32: the plan's extern rule takes
  the callee's type only where the calls pass it without a cast, and a word's address is no byte
  pointer. Now a byte pointer (u8) may be declared void in a file's externs (void_param): in the
  plan, where the trial counts fewer casts with it (an extern candidate keeps the type with the fewest
  casts, the first of those; the other candidates the first that adds none, as before), and in
  object_arguments (after dread.frames made the objects the plan never saw), where every call passes
  an object's address of any type or 0; the callee's own definition keeps its bytes (each function is
  a file of its own: one pointer at the machine's level). `xQueueGenericSend(q, &stack4, 0x8cu, 0)`,
  `memset(&g_200017ac[0], 0, 0x54u)`. On the nine builds (copies of the fault branch's lift results)
  the gate accepts the same functions; `(u32)&` 316 -> 257 (GCC -Os), 368 -> 299 (clang -O2), 340 ->
  309 (RISC-V GCC -Os), 445 -> 410 (GCC -O0), 284 -> 261 (RISC-V GCC -O2), lines with a (u32) 778 ->
  718, 833 -> 762, 731 -> 698, 955 -> 911, 760 -> 737; gizmo unchanged. Left: the callee's own
  parameter stays a byte pointer where it only hands it on (a void parameter there would need no cast
  either; not tried), and an integer passed beside objects keeps the extern u32. Tests:
  tests/test_vartypes.py test_a_byte_parameter_is_a_void_pointer_where_the_calls_pass_other_pointers,
  test_objects_of_other_types_go_to_a_byte_parameter_as_void_pointers.
- [ ] **What a variable loaded from a pointer global reads past its structure.** The structure of a
  pointer global is the one its typed loaders and storers vote for; a variable loaded from it that reads
  past that structure's members (a task block's flag byte past the list item the block starts with, where
  only list functions typed the word) prints `*(volatile u8 *)((u32)p + k)`, longer than the untyped `p[k]`
  it replaced. Take the accesses through variables loaded from the global into its structure, as for a
  parameter, or write such an access `((volatile u8 *)p)[k]`. Seen on the private real-life test; public
  example: none checked (FreeRTOS's readers type pxCurrentTCB as the task block itself). Part of the
  pointer globals' Left.
- [x] **A frame object walked by an index is an array.** dread.frames makes a structure of a frame region
  with accesses of two widths at one place even where a loop walks the whole region with a variable index
  of one element size (a buffer zeroed word by word, one of its words read as a halfword later): a packed
  structure with a union, and the loop's stores stay `*(volatile u32 *)((u32)&buf + (i << 2)) = 0;`. Such
  a region is an array of the walk's element (`u32 buf[8]`), its constant places elements, a narrower read
  at one through the element. An access through a variable that holds the object's address (`v =
  (u32)&buf; *(volatile u32 *)v |= 0x80u;`) is the object's. Seen on the private real-life test; public
  example: none checked. Test: a function that zeroes a local array of words in a loop, sets a bit in its
  first word, passes words of it to a callee and reads one as a halfword.
  Done 2026-10-25 (dread.frames _decide and member): a region accessed through an index of one width,
  every access at a constant offset inside one element and aligned to its own size, is an array of the
  index's element even where some of those accesses are narrower; such an access is an element of a
  view of its width (((volatile u16 *)stack32)[3] for bytes 6 and 7), the loop's stores elements
  (stack32[r3] = 0). One that is not aligned, or spans two elements, keeps the structure. Not done: an
  access through a variable that holds the object's address. Test: tests/test_frames.py (the item's
  function; the two that stay structures).
- [ ] **An element read through an array's address plus an index.** `v = (u32)tab + i; v = *(volatile s8
  *)v;` (the address and the index added in a register, the load through it) is `tab[i]`, also where one
  variable holds the address and then the value (One variable per value). Seen on the private real-life
  test once its local arrays became frame objects; public example: none checked.
- [ ] (low) **A member's address held for one access.** `q = &p->size; ... *q = v;` (the address computed
  early), with nothing between that writes p or q, is `p->size = v;`.
- [ ] (low) **A narrower access at a word member of static data.** One name per static object takes a
  halfword that had a name of its own into the structure whose word member is at its place: `*(volatile
  s16 *)&g.member`, the halfword's name gone. Compare the alternatives (a union member, the narrower member
  where every access there has that width, the separate name kept) before changing anything.
- [x] **One radix per object, and stores through pointers.** Follow-up of "Radix by use". The bytes of
  one small local can come out in two radices (`0x96u, 0x35u, 15`), and constants stored through pointer
  parameters stay hex while the bounds that select them print in decimal. Decide the radix for an
  object, or for a pointer's target, as a whole; count a store through a pointer parameter as a place.
  Public example: none checked. Test: a function that stores sizes through two pointers after comparing
  its argument with the same numbers.
  Done 2026-10-24 (dread.polish _objects, placed, masklike, low_run, address; docs/decisions.md, as
  above). One object: a variable; an array's elements (a local's, static data's, a typed pointer's: x[k],
  *p); a structure's field; joined by copies (x = y, *p = v, v = p[k], a declaration's initialiser), not
  through an access at an address the code computes. Its places share the evidence (a use; two or more
  plain numbers given or compared; a number the function writes in decimal elsewhere: a bound or its
  neighbour on the side the compiler moved it to, x <= 63 for x < 64, a divisor, a multiplier, a constant
  of an object that holds numbers), and one that holds numbers prints all its constants below WIDE in
  decimal, a run of ones from bit 0 too (255 beside 63), unless all look like masks (x <= 0xffu alone
  stays); a sum's constant is a number also where the sum goes into such an object (GCC -O0's 12345 in
  two adds folds: `0x41c64e6du * g_20000e3c + 12345`). Never an object holding a pointer (RISC-V's walk
  over the ready lists, `s4 += 0x14u`, hex as the pointer it is copied into) or only texts' starts and
  functions' entries (dread.constants Pointed: FreeRTOS's prvCheckTask stores its status message's texts,
  at small addresses on an image at 0, which main printed as numbers, clang -O2 `g_20000000 = 379;`, GCC
  -Os `r3 = 394;`, now `0x17bu`, `0x18au`; line numbers that fall on such a text beside others stay
  numbers), nor one taken to numbers by another object's value where one of its own looks like a bit
  pattern (clang -O2's prvSanityCheckCreatedEventGroup keeps an event group's 0x55 and a line number in
  one register). A complement stored into a narrower place is the value it leaves there (~0x69u into a
  byte: 150, or 0x96u). The item's test, tally on ARM state, Thumb-2 and RISC-V through the gate:
  tally_pick's sizes through two pointers `= 64; = 256; = 1024;` with the bounds `<= 63`, `> 255` (main:
  `0x40u`, `0x100u`, `0x400u`, `0xffu`), tally_colour's bytes `150, 53, 15/20` (main: `~0x69u, 0x35u`
  beside `20 : 15`). FreeRTOS: prvRecursiveMutexBlockingTask's `r2 = 255; g_200009dc = r2;` (__LINE__
  through a register: was 0xffu), QueueOverwrite's ulTx1 and ulTx2 `stack28 = 10; stack24 = 20;`, the
  printf base `10`/`16`, prvTimerTestTask's counts `while (r4 != 20)`, MessageBufferDemo's tick bound
  `a5_2 = 249;` beside `249 >= ...`; gizmo m4's handle_key in one radix with its bounds (`r0 == 76`,
  `r0 - 98`, as a32 printed already; the characters they are stay the Left of "Characters as
  characters"). On the eight builds (figures for the gate with "Masks stay masks across calls") lines
  whose constant changed its radix 1, 1, 7, 0, 42, 8, 17, 29 in 1, 1, 2, 0, 10, 5, 13, 3 functions (most
  of them the texts of FreeRTOS's status message going back to hex: 21 on GCC -Os, 20 on GCC -O0), hex
  constants of 10 or more 98, 84, 100 -> 94, 87 -> 88, 2,387 -> 2,427, 3,611 -> 3,633, 3,133 -> 3,118,
  3,003 -> 3,054, decimal ones 44 -> 45, 32 -> 33, 34 -> 41, 31, 572 -> 563, 1,011 -> 1,013, 362 -> 377,
  580 -> 558. Tests (tests/test_polish.py): one radix per object (a colour triple both ways, sizes and
  bounds, a sum in a sum, a lone bound, a shifted run, a pad character), texts' addresses (a line number
  on a text by chance) and bit patterns, random functions with an array and copies compiled both ways,
  the gate program. Left: a structure's fields are decided each on its own (a frame message's command
  beside its pointers); a place stored once and in decimal company in another function stays hex
  (image-wide evidence); call arguments stay hex; a digit's character added to a number (printi's
  `r8 + 48`) is a number where the function compares too few characters to be text.
- [x] **Readable C the gate has not accepted, marked.** `dread readable NAME` prints any function that
  lifts, under the same first line ("readable C from its lifted C"), whether the gate accepted it,
  rejected it, or never saw it. Say so in that line, and in the GUI's reading view. Public example:
  FreeRTOS mps2-gcc-Os prvNotifiedTask_73ac (not in that build's lifted table).
  Done 2026-10-25 (dread.readable gate_states, gate_note, marked; dread.gui reading's "gate"): the first
  line of `dread readable NAME` (printed, --plain and --out alike) ends "; accepted by dread readable
  --check", "; not accepted by dread readable --check: REASON" (the check's verdict, e.g. FAIL NAME
  [generated 1]), "; never checked by dread readable --check: not in the lifted table" or "; not checked: no
  dread readable --check has seen it"; the GUI's reading view says the same, with the rejection's reason.
  The forms the gate builds and the tests compare (printed, reading) keep their line; only what a person
  reads carries the standing (tests/test_gate_mark.py).
- [ ] (low) **A margin address of the statement's own.** The margin shows the lowest folded address,
  which can be the entry's copy of a parameter. gizmo a32 read_line shows `2097c  r0[r4] = 0;` for the
  store at 0x209c8; 0x2097c is `mov r5, r0`. Prefer the statement's own instruction when the folded ones
  are earlier register copies.
- [x] **Readable C of a wrapped function's body.** A function that reaches its caller's stack (a
  variadic one, or one whose callees read its stack arguments) is lifted as a naked trampoline and a body
  that takes the real stack's address, and dread readable leaves both as the lifter wrote them: registers,
  the flags as compare instructions set them, gotos. Run the clean-up and the structure on the body (its
  frame accesses staying raw, as they must), and print the trampoline in one line. Public examples:
  FreeRTOS printf and sprintf on every build. Frequent on the private real-life test, where the naming
  pilot then describes the trampoline instead of the function.
  Done 2026-10-25 (dread.readable picked): the stages ran on the function the file is named after, which
  for a wrapped one is its entry, an asm block; now the body is the function readable C is made of, and
  carries the function's name while the stages run (what the tables, the plan and the names know it by),
  the entry NAME_dread_entry; the heads keep their own text (the body's starts with its attribute, which
  no stage's head pattern matches, so its parameters and its declaration stay as the lifter wrote them).
  FreeRTOS GCC -Os's printf: `return tiny_print(0, r0, sp0 - 12, 0);` behind the stores of its variadic
  registers, where the lifted form set flags and passed registers. On the eight builds the gate accepts
  the same functions; printf and sprintf changed on the FreeRTOS builds, and with them their callers
  prvCheckTask and vAssertCalled, whose formats now show (`printf(0x108u /* "ASSERT! Line %d, file
  %s\r\n" */, ...)`: printf's body tells that its first parameter is a text), and one structure of GCC
  -O0's gained the byte the body reads; gizmo, which wraps nothing, unchanged. Not done: the trampoline
  stays as the lifter wrote it (one asm statement). Test: tests/test_readable.py
  test_a_wrapped_function_s_body_is_made_readable.
  Fixed the same day (dread.tidy fixed_head): dread.vartypes, dread.shapes and dread.naming rewrote a
  body's statements but not its head, so a parameter typed or renamed in the statements alone no longer
  compiled (`*more = r3;` for more declared u32, in the bodies that hand back results through an area);
  the private real-life test's gate refused such bodies, the public builds have none (their printf and
  sprintf hand back nothing beyond r0). Such a body's parameters now stay as declared, its locals are
  typed and named as before (tests/test_readable.py test_a_wrapped_body_keeps_its_parameters_as_declared).
- [x] **Division helpers as operators.** A runtime helper that returns a quotient and a remainder prints
  as a two-word call with its halves cut out: `(u32)(__aeabi_uidivmod(r4, 0xau) >> 32) + 0x30u`. Print
  `r4 % 10 + '0'` where the helper is known to divide (by name for the EABI's; by its behaviour in the
  emulator for a library's own routine, checked on test values, the order of its arguments included),
  and give the readable check's link the helper the compiler's own code calls for `/` and `%`. Public
  example: gizmo a32 fmt_dec. Frequent on the private real-life test (its library's own routines).
  Done 2026-10-25 (dread/divisions.py; the readable_types table's `divisions`, docs/formats.md;
  docs/decisions.md, "Division routines as / and %"): the routines that divide by name (the RTABI's,
  libgcc's) and by what they do in the emulator (a function that reads two argument registers, run on
  pairs in both orders and signs; not one that uses a divide instruction, nor a wrapper that hands back
  nothing its callee does not, gizmo's udiv_var); their calls as `/` and `%` in the routine's order and
  sign (dread.readable polish_tree, before the constants), not inside a routine that divides (written
  as an operator its own call would become a helper made of itself: gizmo a32's __aeabi_uidivmod fell
  out of the readable table so on the first try); the helpers GCC's own `/` and `%` call made of the
  image's routines where it lacks them (build/readable-check/IMAGE/support/dread_divisions.c, linked
  beside the readable C), and taken as what the original calls by the check's test of foreign calls.
  A function whose / and % fail its cases is tried once more with its divisions as calls (the
  readable_types table's divcalls, tried first among the check's second tries): a case that stubs the
  routine sees each call, which the compiler's / does not make where it knows the divisor or multiplies
  by a constant; and last, where it would be dropped otherwise, with the divisions of the functions its
  code reaches as calls (a case's refusal of a callee's difference takes its coverage too), given back to
  / and % if it is dropped all the same. On the private real-life test the gate accepts as many functions
  as before, with / and % where their cases allow it; without the second tries it lost functions to
  cases that log the routine.
  gizmo a32: fmt_dec's `r4 % 10 + 0x30u` (its quotient by 10 stays the compiler's multiplication),
  udiv_var's `return r0 / r1;` when printed (its C is the program's own); its __aeabi_uidiv made of
  __aeabi_uidivmod. On the eight builds the gate accepts the same functions; only gizmo a32 changed (6
  lines), the others have divide instructions. On the private real-life test both of its library's
  routines are found by what they do, the divisor first. Not done: a two-word value kept whole (u64 t =
  f(a, b), both halves read later) stays a call; 64-bit division; a compiler's division by a constant
  as a multiplication (`(u32)((u64)r4 * 0xcccccccdu >> 32) >> 3` for r4 / 10). Tests:
  tests/test_divisions.py.
- [x] **Division by a constant, from its multiplication.** A compiler divides by a constant with a
  multiplication by its reciprocal and a shift: `(u32)((u64)r4 * 0xcccccccdu >> 32) >> 3` is r4 / 10
  (gizmo a32's fmt_dec), clang -O2's `(u32)((u64)r1_1 * 0x1b4e81cu >> 32) * 150` the quotient by 150
  taken times 150 (a remainder in the making). Recognise the multiplier and the shift by checking that
  they give x / d for every x of the width (not by the pattern alone; signed ones add a correction of the
  sign) and write x / d, x % d where the code subtracts the product; the gate decides as for any rewrite.
  Public examples: gizmo a32 fmt_dec, FreeRTOS clang -O2 (ten such products).
  Done 2026-10-25 (dread.divisions constants, magic_divisor; polish_tree after the routines' rewrite):
  proved by the numbers for every value the dividend can have, a 32-bit one or fewer bits where its form
  or its one definition says so (clang's / 150 multiplier is exact for the 15-bit value it was made for,
  not for every 32-bit one: written as % 150 there only); two values decide (the error of a quotient
  made so grows with the dividend: the last of the last whole run of d values and the largest value).
  A function whose constant division fails its cases is tried again with its divisions as the machine
  code has them (divcalls, as for the routines' calls: GCC -Os for ARMv4T calls __aeabi_uidivmod for a C
  % 150). Not done: the signed form, and the one with an add-back step (a 33-bit multiplier: GCC's / 7).
  Test: tests/test_divisions.py (the proof on known multipliers, and sound on every 12-bit value of
  random ones; the rewrite compiled before and after; gizmo a32 through the gate; a wrong rewrite taken
  back). On the eight builds the gate accepts the same functions: gizmo a32's fmt_dec writes `r4 /= 10`,
  FreeRTOS clang -O2's printf (tiny_print) / 10, / 100, / 1000 and /= 10000, its
  prvStaticallyAllocatedCreator % 150 three times (a 15-bit value); the products left (11 -> 4 on clang
  -O2) are no divisions (the high word of a product of two variables, an overflow check), or RISC-V's
  whose shift is a statement of its own.
- [x] **A two-word value read only as its low word.** Done 2026-10-25 (dread.polish narrow_wide,
  readable.polish_tree before the divisions): a u64 variable every read of which is (u32)v is u32, its
  values computed in 32 bits where their low word depends on their operands' low words alone (+, -,
  *, &, |, ^, ~, negation, a left shift under 32, a call that the 32-bit place cuts to its low word):
  a carry the lifter kept (t = (u64)r1 + 1; the borrow form (u64)r0 + ~0x2u + 1, which polish then
  writes r0 - 2) and a division routine's two words whose quotient alone is read (then written as /
  by dread.divisions). Not a parameter, nor a variable whose address is taken or that is read any
  other way. Seen on the private real-life test and on FreeRTOS clang -O2 (xTaskIncrementTick's
  tick count plus one). Test: tests/test_polish.py (compiled before and after; what stays; the
  chain into /). On the eight builds the gate accepts the same functions; only clang -O2 changes
  (xTaskIncrementTick reads t_2 = r1 + 1 ... if (t_2 < ...), as the source's xConstTickCount; its u64
  declarations 5 -> 3, the others' high words read: overflow checks of a product).
- [x] **A jump to the start of the other arm, as a condition.** Done 2026-10-25 (dread.structure
  arm_jumps, after the arms pass): a check in one arm of an if that jumps to the start of the other arm
  joins the if's condition, the expression statements before it in a comma expression: `if (A) { S; if
  (B) goto L; R } else { L: T }` is `if (A && (S, !B)) { R } else { T }` (`if (!A || (S, B)) { T }`
  without R), and the other way round `if (A || (S, B)) { T } else { R }`; a jump just before an if, only
  expression statements between, to the start of one of its arms likewise (`if (B) goto L; S; if (A) {
  L: T }` is `if (B || (S, A)) { T }`, `if (!B && (S, A))` where L begins the else arm). The operands of
  one && or || print flat; the label goes where nothing else names it. Not joined: a statement other
  than an expression before the check, more than two of them (ARM_JUMP_EFFECTS) or a call made for its
  effects alone among them (both read worse in a condition than the goto: docs/decisions.md), a label
  further into the arm. Test: random threaded checks (both layouts, a jump from before, and checks that
  must stay jumps) and the shapes one by one, compiled before and after; ten wrong variants of the rule
  each fail a test. The jump from an arm of an else-if chain to an earlier arm's start stays (the next
  item).
- [x] **Quantities beside a private target, found by the leak check.** Rule 1 keeps a private target's
  counts, scores, times and costs out of DREAD's texts; dread leakcheck looked for its names, addresses
  and listed words, not for quantities, and on 2026-10-25 twenty passages written over three days still
  gave some (reworded). Done the same day without a change to the tool: its words file takes regular
  expressions, and the private project's now holds two that find a number with a unit, a fraction or a
  count in words within a sentence of a mention of the private target; a public build's figure beside
  such a mention goes into the allow file. Old commits keep such passages: they are allowed by their
  text, and a rewrite of the public history is the user's decision.
- [x] **A jump from an else-if arm to an earlier arm's start.** `if (A) { L: T } else if (C) { X } else {
  S; if (B) goto L; R }`: joined as `if (A || (!C && (S, B))) { T } else if (C) ...` it evaluates C twice,
  so only where C has no effects and reads only variables that S and B do not write (a volatile read
  twice is another access). Seen on the private real-life test; the random threaded checks' chain layout
  (tests/test_structure.py) is its test bed. Done 2026-10-25 (dread.structure arm_jumps' chain, given the
  function's own variables: parameters and locals not volatile, whose address nothing takes): from any
  arm of the chain (`... else if (Cj) { S; if (B) goto L; R }` joins `!C1 && ... && Cj && (S, B)`) and
  from its last else; every test on the way reads only those variables, has no effects (no call, store,
  memory read), and S and B write none of what the tests read; at most two such tests (ARM_JUMP_TESTS:
  FreeRTOS GCC -O0's xTaskGenericNotify, a switch compiled as a chain, would have given its default arm
  six in one condition). Test: the shapes compiled before and
  after, the cases that stay (a test that calls or reads memory, statements that write a tested
  variable), and the threaded checks' chain layout through the property test; five wrong variants of
  the rule each fail a test. On the eight builds the gate accepts the same functions, and with the limit
  no public build's C changes: the shape is the private real-life test's.
- [ ] (low) **Members comments over several lines**, wrapped past a few members.
- [x] (low) **Declarations nothing uses**, dropped. Done 2026-10-25 (dread.polish drop_unused_declarations,
  the last step of polish): a variable that a pass after the clean-up's own tidy_declarations folded away
  (the structure stage's loops, merged arms and truth values, polish's steps) is no longer declared, where
  its initial value, if any, has no effects; a name an asm operand or a raw statement holds counts. On the
  FreeRTOS GCC -Os and clang -O2 builds 18 and 28 such declarations before, none after, the gate
  accepting the same functions (317, 275); it runs after the digest, so names are untouched.
- [x] **The readable gate's refusals at measure-41** (2026-10-26). The gate kept 27 of the 12,148 lifted
  functions of the 38 ARM and RISC-V builds lifted: 26 because a case differed, one whose readable C did
  not compile. Traced with dread trace --readable:
  - 20 on GCC 4.9, 5, 6 and 7 -Os (five each of xQueueSelectFromSet, xQueueSelectFromSetFromISR,
    xNotifyTaskFromISR, xStreamBufferSend, prv1stHigherPriorityNormallyFullTask and an echo server
    task): the entry pushes r0-r2 to make room, a register holds sp0 - 8, the local is stored through
    it minus 4 (str r2, [r3, #-4]!) and that address goes to a callee. frame_locals saw only sp0 - 8
    leave (the frame base is written into its uses by a later pass) and made the slot below it a
    variable: the read became the register the push stored there. Fixed (dread.tidy
    _held_frame_addresses): frame_locals follows the variables that hold a frame address, on the
    reaching definitions, with constants held in variables (the lifter's flag-setting adds), an index
    added counted from the address it is added to (as frame_index), and a walk downwards in a loop taken
    as before (at or above where its base leaves). The measurement found two first forms too strict:
    newlib's printi (its digit buffer walked down) and three GCC -O0 functions (an index loaded from the
    frame added to a local array's address) lost stack variables; both as on main now;
  - RISC-V GCC -O2's vTaskResume: its task pointer's register holds the priority where it indexes the
    ready lists, and C takes no pointer as an index (dread.polish casts one);
  - FreeRTOS pvPortMalloc on clang 12 -Os: propagate took a copy past a reload of its source on a
    loop's exit (below).
  On copies of measure-41's builds, against main: GCC 4.9, 5, 6 and 7 -Os 5 -> 0 refusals each, RISC-V
  GCC -O2 1 -> 0, the other builds as before; on the nine builds the readable C is main's but for
  RISC-V GCC -O2's vTaskResume and vTaskPrioritySet (the cast; the second did not compile there either).
  Tests: tests/test_tidy.py test_a_slot_reached_through_a_variable_that_holds_a_frame_address_stays,
  tests/test_polish.py test_a_pointer_used_as_an_index_is_cast_to_an_integer.
- [x] **A copy past a reload of its source on a loop's exit** (pvPortMalloc on clang 12 -Os, from the
  item above): its free-list walk copies r1 into r6 at the loop's top and reloads r1 before the second
  exit, behind which it reads r6 + 4; propagate wrote r1 + 4 there (the same definitions of r1 reach the
  copy and the read, the reload through the back edge), and the readable C stored through address 4.
  Done 2026-10-26 (dread.tidy _written_between): a copy goes only where no path from it to the read,
  without running it again, writes its source. On copies of measure-41's builds, against main:
  pvPortMalloc readable on clang 12 -Os (1 -> 0 refusals); on the nine builds one line differs, clang
  -O2's prvNonBlockingReceiverTask_5300 reading r5 | strcmp(...) where it read r8 (r8 is written between
  the copy and the read on paths that leave before it: the same value either way). Test:
  tests/test_tidy.py test_a_copy_does_not_go_past_a_write_of_its_source_on_a_loop_exit.
- [ ] **Stale stack content in the readable gate** (xStreamBufferReceive and its FromISR form on the GCC
  -O2 builds, five refusals, from the item above). In those cases memcpy is stubbed (its source, a
  generated pointer, faulted) and writes nothing: the generic stub fills only the tested function's
  frame, and generated cases have none, while the local here is the caller's caller's. The length read
  from it is what earlier frames left there. The stale-data check fills the original only, and its fills
  (0xA5, 0x5A) send it the way the stale data did; the rebuilt image reads its own frame's garbage, takes
  the other way and faults. Tried 2026-10-26:
  - Both images filled alike (dread.runner filled_alike, docs/decisions.md "A case that agrees with the
    stack filled alike"): where a case differs, both run again with the same fill below every
    function's entry and the case agrees where they agree whole under each fill. Kept: the lift check
    accepts GCC -O0's prvCreateAndDeleteStaticallyAllocatedQueues (395 -> 396 on a copy), the other
    builds as before. It does not reach these five: their readable C reserves the local with push {r0,
    r1, r2, ...} and reads the pushed r0, not the fill.
  - A refusal for the original's read of a stack byte no live frame wrote (a write counted until a
    function is entered with its stack pointer above the byte), what follows it not compared: it would
    take the five, but it also refused a C that passes another flag to the callee that writes the local
    (its only visible effect comes after the original's undefined read), so it hides differences: not
    done (rule 6), the code not kept.
  Next: the case itself. memcpy is stubbed because its source, a generated pointer, faulted; with the
  page mapped instead (as the answers' fault trial maps one, a case's map) memcpy runs and writes the
  local, and nothing reads stale data.

## Compiler diversity (the user, 2026-10-20: first, on ARM/Cortex-M and RISC-V)

- [x] **The whole image of FreeRTOS RISC-V GCC -Os and Cortex-M clang -O2 differs with lifted C
  alone** (BUILD/lift-check/main/whole.txt of both, measure-38): the rebuilt run gets further before
  the limit than the original by the clock of code both share (RISC-V: 412 log entries against 158,
  the original printing nothing, the rebuilt "FreeRTOS Demo Start"), and whether a run agrees
  depends on how the clock falls (each half of the 258 readable functions agreed, all together not).
  Find where the clocks part (dread sysrun --log on both, the first block counted in one and not the
  other), so that the scenarios decide there for lifted and readable C alike.
  Done 2026-10-22. Not the clock of shared code (it was the same in both runs at every entry up to
  the first difference) but the run's rule for a wait outside it: FROZEN (2,000) blocks in a row
  outside shared code with an event due were a wait, and the run moved the clock on to the event.
  That counts blocks of the code the two images do not share. clang -O2: xTaskCreate's memset of a
  new task's stack (lifted; the original's byte loop, two blocks a byte) crossed 2,000 blocks, the
  run moved the clock 6,032 units on to timer1's event inside it and took the interrupt there; the
  rebuilt memset (fewer blocks) went on (entry 254: vPortEnterCritical's ICSR read 4 times before
  it in the original, 39 in the rebuilt image). RISC-V: prvQueueSetReceivingTask polls
  xQueueSelectFromSet, all of it lifted; the rule fell at 0x8000ac2a, inside xQueueReceive's critical
  section (mstatus.MIE clear), in the original and outside one in the rebuilt image, and the tick
  was never asked for again (the CLINT schedules nothing more, and no shared block came): the
  original ended "waits" at clock 861,822 having printed nothing, the rebuilt image 33 ticks later
  at its own masked point. Now (dread.sysrun, System._stretch): code outside shared code runs in
  stretches in which the clock stands still (a shared block, a device access or an exception ends
  one); a stretch that ends by itself was a computation, however long; a wait is a loop, a block
  entered again with the core's registers (general, sp, link, status, masks) as at an anchor
  (Brent's search from FROZEN blocks on; LOOP_MAX = 200,000 blocks count as one: a busy task that
  counts in a register); in a wait an interrupt is taken behind the first block the core takes it in
  (a masked one behind the loop's unmask; one the loop keeps masked a whole round: on to the next
  event, or the end). The C hook does the stretch's blocks that only count (sys_stretch). Measured on
  the measure-38 images (the original against every lifted function rebuilt): RISC-V GCC -Os agrees
  to the limit, 8,172 log entries each (before: entry 158, both runs ending early); clang -O2 agrees,
  533 entries (before: entry 254); GCC -Os and -O0 on the M3 still agree (2,466 and 906,650 entries,
  as before). The C hook's outcomes are the Python hook's byte for byte on all four. Two runs took:
  RISC-V 1.3 s ending early, now 2.4 s to the limit (40 s with the stretches in Python), GCC -Os 11.2
  -> 0.8 s, clang -O2 0.6 -> 0.2 s, -O0 273 -> 107 s. Of the waits of the RISC-V runs, 346 and 360
  were loops, 14 of the original's LOOP_MAX (the coherence tester's busy loop, whose counter stays in
  a5 in the original's code; the C's loop repeats its registers): both took the tick somewhere in it.
  Gizmo's lift checks accept what they did, the whole image agreeing on every build (gizmo-mips's
  check stops at 'Mips' object has no attribute 'new_uc' before and after: MIPS is paused). All 38
  benchmark builds with a whole image of measure-38 or later, the same comparison with HEAD's rule
  and with this one: 18 differed with lifted C alone, now 3, which differed before at the same entry
  and for a reason of their own (the item after the next). Most that differed had ended early in
  both images, in an assertion's spin; now the M3's clang -Os builds (12 to 19 and the host's, M4F
  too) and GCC -O2 builds (12, 14 and the host's; 9, 10 and 13 still spin, alike) run to the limit
  with 1.4 to 2.5 million log entries, and the RISC-V builds with about 8,150. dread readable
  --check on copies of RISC-V GCC -Os and clang -O2: 252 of 259 and 275 of 286 readable, the whole
  image agreeing on both (HEAD: the same functions, "the whole image differs with lifted C alone:
  not decisive"). Left: clang -O2's runs still end early (next item); the M3 runs that now reach
  the limit take 95 to 141 s a pair (about a second when they spun early), 80 % of it in the Python
  peripheral hook (vPortEnterCritical reads ICSR on every critical section: with the kernel outside
  the clock, 125,000 accesses a million units, nearly all of the clock). Done for the reads
  (2026-10-22, "Performance, third pass": the reads of plain registers in C, 82 s -> 30 s a pair);
  the writes are the next item there.
- [x] **An interrupt held off by a mask that code outside shared code lifts waits for the next
  shared block.** FreeRTOS clang -O2 with every lifted function rebuilt: both runs assert in
  GenQTest.c line 683 at 93,560 units ("ASSERT!", then "spins"), where the original alone runs to
  the limit. vTaskResume pends PendSV in its critical section (BASEPRI masks it), vPortExitCritical
  (lifted) lowers BASEPRI, and the run takes PendSV only behind vAssertCalled's first block (the
  first shared one): eTaskGetState already saw the high-priority task ready. The original's code
  of the rewritten functions is outside the clock as the C is, so both images do it alike, and the
  scenario decides only over its first 94,000 units. A rule tried on a copy (2026-10-22): an
  interrupt asked for while the core masks it (noted where it is asked for, a device access, an
  event, an exception's entry or return: the masks are the same in both images there) is taken
  behind the first block in which the core takes it, outside shared code too. The instruction that
  unmasks ends its translation block on Unicorn 2.1.4 (seen for msr basepri, cpsie, cpsid and isb on
  the M3, cpsie and msr cpsr_c in ARM state, CSR writes on RISC-V; MSP430's own engine not checked),
  so that is right behind it in both images. Both images agreed on all four builds, but the runs
  changed: clang -O2 passed GenQTest and asserted in EventGroupsDemo.c line 304 at 1.6 million units
  (2,371 log entries against 533), GCC -Os and -O0 on the M3 asserted in TimerDemo.c line 929 at 2.4
  and 2.5 million (they ran to the limit before, -Os with 2,466 entries: after the 67th only the
  1,200 ticks, PendSV taken three times in all; -O0 with 906,650), RISC-V unchanged. More
  faithful interrupts expose the larger distortion: the kernel outside the clock takes no time, and
  the demo's timing tests assert (lift.system_check's docstring). Not kept: which runs serve the
  comparison better is a question of its own (a run that asserts early compares little; one that
  idles to the limit compares little more).
  Done 2026-10-23, together with "Where an interrupt comes in a wait made only of C" (Phase 4;
  docs/decisions.md): both came from one model, outside shared code the clock stood still and no place
  was common to both images. Now a function entered outside shared code from outside it moves the
  clock by ENTRY (56) units (System._entry_units; lifted C makes the original's calls, so both images
  enter the same functions in the same order), the compiler's runtime helpers take none, and outside
  shared code an interrupt is taken only where both images share the place: a function's entry, a
  block that starts right behind an instruction at which Unicorn ends its translation block without
  branching (isb, msr, cps; RISC-V's CSR accesses and fence.i, measured; the C keeps them itself),
  shared code, and in a wait inside one function as before; one the core would take at once
  elsewhere waits for the next such place. clang -O2 with every lifted function rebuilt: GenQTest
  passes, both images run to the limit with 88,216 log entries (the original alone: 88,555; before:
  the assertion at 94,000 units, 533 entries). The whole images of all 38 benchmark builds with
  scenarios (each build's lifted table): all run to the limit (main: 29), the Cortex-M ones with 0.93
  to 1.23 times the original alone's log entries (main: from 0.03 to 28 times: nine asserted, six
  idled with 2,466 entries, twelve made 1.5 to 2.5 million), the RISC-V ones with 1.03 to 1.07; 36
  agree (main: 37). virt-gcc10-Os differs as with main (its table is from before the culprit search),
  and mps2-gcc-O0 now differs at entry 20,356: its table (measure-38) lists prvQueueSetSendingTask,
  whose lifted C alone makes the image differ at entry 16,085 with main's code as with this one, and
  which today's lift check drops by its system check; main's run never got that far (906,650 entries
  of another course). The lift check on copies of the six builds with the frontier answers: the same
  tables (RISC-V GCC -Os one more, vTaskSuspendAll: next item), the whole image agreeing on all six,
  in 22 to 104 s (main: 62 to 367). The pairs of runs take 400 s on Cortex-M where main took 199 s
  spinning or idling (the register-test tasks' busy loops: "LOOP_MAX can come down" below), 11 s on
  RISC-V where main took 60 s. The 2026-10-22 rule's timing
  assertions do not come: the kernel takes time again. Taking an interrupt at the next block outside
  shared code made the images differ on 19 of the 27 Cortex-M builds; deferring every one to the next
  entry made 20 assert in xEventGroupWaitBits (a task that suspended itself switched only at its next
  call, the argument already loaded).
- [x] **Three RISC-V builds' whole images differ with lifted C alone for reasons of their own**
  (the only ones left of the benchmark's 38 once the wait rule was fixed, 2026-10-22; they differed
  at the same entries before): the rebuilt clang -Os and clang 14 images print "Stack overflow in
  SetB" (entries 355 and 359; a task's stack too small for the C's frames, which is what the whole
  image is there to find), and GCC 10's asserts (line 1367) at entry 32 and then reads unmapped
  memory (0xfffffb85) while it prints the file name.
  Done 2026-10-22: two causes, in neither the C of one function alone (docs/decisions.md, both
  entries of 2026-10-22). GCC 10: its function table lists freertos_vector_table, the vectored trap
  table (mtvec = table | 1; the core enters at table + 4 x cause), as a function of 128 bytes (GCC
  10's binutils write no RISC-V mapping symbols, and the untyped global label makes a function;
  LLVM 14's table too, refused there for another reason). Its first entry, a jump, lifted as a tail
  call and its cases accepted it; the overlay freed the other entries as unreached code, and
  xStreamBufferIsFull's C (120 bytes) took exactly that space. The timer interrupt (cause 7, table
  + 0x1c) entered that C in the middle of its lui and ran its configASSERT path (stream_buffer.c line
  1367) with a0 = 1 - 0x47c = 0xfffffb85 for the file name, which printf's prints then read (dread
  sysrun with a code hook at vAssertCalled: ra in the C, a0 without its upper part). Fixed in the lift
  check: code of a function that no path reaches, that nothing leads to (no instruction in front goes
  on to it, no direct jump or call of any function goes there, no switch case) and that is not padding
  is entered from outside, and the function is refused (Lifter.entered_unseen; the layers' padding;
  tests/test_lift.py test_code_that_only_the_core_enters_is_not_freed, which failed before). In the
  lifter itself first, it refused dread liftfuzz's random MSP430 functions (three tests failed): the
  translation is right, the space is not free, so it is a refusal of the acceptance, as "other code
  branches into its middle" is. On the 39 benchmark builds it refuses only GCC 10's table among the
  lifted functions (LLVM 14's among all), on gizmo nothing. clang -Os and clang 14: the C's frames are
  larger than the original's: prvTestMasterTask 176 -> 256 (clang 14: 128 -> 224) and
  xEventGroupSetBits 32 -> 64 (32 -> 80) bytes. On SetB's deepest path (prvTestMasterTask,
  xEventGroupSetBits, xTaskResumeAll, the trap's context save: a block hook that keeps a call stack
  per task) the original leaves 100 (148) of the 480 bytes free, the rebuilt image goes 12 bytes below
  the stack (to 4 bytes above it, inside the 16 bytes that configCHECK_FOR_STACK_OVERFLOW's method 2
  reads).
  Either function alone leaves the image agreeing. The lift check's whole-image stage saw it and only
  reported it (whole.txt); now it finds a smallest set of functions that still differs (lift.culprits:
  a binary search for the shortest prefix, repeated; halves, as dread readable used, would have
  returned 121 and 122 functions: the two lie in different halves) and drops it, until the whole image
  agrees (tests/test_lift.py: test_culprits_finds_what_differs_only_together;
  test_functions_whose_c_makes_the_whole_image_differ_are_dropped, a program that prints how deep its
  stack went, which failed before). dread readable --check uses the same search. Measured on copies of
  the measure-38 builds with this code: GCC 10 257 functions (258, the table out), the whole image
  agreeing to the limit with 8,147 log entries each (before: 8,150 against 104); clang -Os 241 (242:
  the two out, prvRegTestTaskEntry1 in; this change only takes functions out, so that one came with
  the code since measure-38), 8,218 entries
  each (8,227 against 439), the search 17 whole-image runs and 28 s, SetB's stack again 100 bytes
  from its end; clang 14 241 (243, the same two out), 8,165 entries each (8,166 against 443), 19 runs
  and 28 s, SetB's stack 132 bytes from its end (148 in the original: other lifted functions' frames,
  within it). Frame growth itself: the next item.
- [x] **Lifted C's frames grow by the C compiler's own register saves.** The lifted C keeps the
  original's frame as an array, its save slots included, and the C compiler saves the registers the
  C uses beside it: on FreeRTOS RISC-V clang -Os 172 of the 242 lifted functions have a larger frame
  in C (median 32 bytes), and on clang 14 131 of the 171 that grew grew by exactly the original's
  register saves rounded up to 16 bytes (4,528 of their 4,784 bytes). A task with a small stack
  overflows (the item before), and the lift check keeps such functions out now, so lifting loses
  them. Leave the save slots out of the frame array where only the prologue's saves and the
  epilogue's restores touch them (no frame address handed out reaches them, not a wrapped function's
  real frame): then the C's frame is the original's locals plus the compiler's saves and spills.
  Changes the frame model of every lifter and what readable C's frame stages (dread.tidy's
  frame_locals, dread.frames) read; measure the frames before and after on the benchmark.
  Done 2026-10-22 (docs/decisions.md): the frame array leaves out the run of words at its top that
  only whole-word stores and whole-word loads touch whose register nothing reads behind them
  (dread.lift's saves_out: the prologue's saves, the epilogue's restores, a pad pushed to keep the
  stack aligned; the return address, which the C takes from the builtin, counts as read by
  nothing). Their stores and loads translate to nothing, and sp0 lies that far above the array's
  end (`u32 frame[3] __attribute__((aligned(8))); u32 sp0 = (u32)&frame[3] + 12;`), so every word
  kept lies where it lay and as aligned. A load's register is dead when no path behind it reads it
  before writing it (restore_dead: a return reads the result, a call its arguments and what its
  callee reads of its caller's registers; a pop that returns loads only the result). Where an
  address of the frame may reach the words, every word stays (FrameReach): the registers that may
  hold one are followed forward on every path, an access through one must lie where the states know
  (no index, one address on every path), and none may be stored, handed to a call, returned or read
  by inline assembly; the frame words a call is handed (stack arguments, a call through a pointer's
  lowest words) are reads of their own. Each lifter says what an instruction does to memory
  (frame_effects: ARM, RISC-V and the MSP430). The C of a function that leaves nothing out is the
  same as before. Measured on copies of six benchmark builds, each lift check run before and after
  (the before-run's accepted functions; the C's frames by -fstack-usage of the lifted C compiled as
  the lift check compiles it, the original's by its deepest stack pointer below the entry's):
  RISC-V clang -Os 173 of 241 frames larger in C (4,768 bytes more; the C's frames 9,632 bytes
  against the original's 4,864), now 56 (1,728; 6,560), 2,880 bytes of saves left out in 128
  functions; clang 14 171 -> 57 (4,704 -> 1,760 bytes; 9,696 -> 6,592 against 4,992); RISC-V GCC
  -Os 191 -> 51 (4,432 -> 1,376; 10,512 -> 7,216 against 6,080); M3 GCC -Os 225 of 330 -> 163
  (4,828 -> 2,612; 11,000 -> 8,672 against 6,252); M3 clang -Os 127 of 320 -> 98 (2,968 -> 1,696;
  7,792 -> 6,512 against 4,952); M3 GCC -O0 (frame pointers) 275 of 358 -> 185 (4,184 -> 2,328;
  12,928 -> 10,592 against 8,788). The lift check: clang -Os and clang 14 accept 243 (241 before:
  prvTestMasterTask and xEventGroupSetBits, which the stack search dropped for SetB's overflow, stay,
  their C's frames 256 -> 192 and 64 -> 32 bytes on clang -Os, 224 -> 160 and 80 -> 48 on clang
  14), the other four the same functions as before (259, 330, 320, 358); every whole image agrees,
  none with a function dropped for it. Gizmo's builds accept what they did. The readable gate
  accepts the same functions on its eight builds; raw frame accesses in the readable C of the
  lifted functions stay as they were (FreeRTOS GCC -Os 43, clang -O2 22, RISC-V GCC -Os 44, GCC -O0
  25 occurrences of `(sp0 - k`): its passes had removed the dead saves already. What changed there:
  a frame array that stays is smaller (`u32 frame[4]; u32 sp0 = (u32)&frame[4] + 16;`), RISC-V's
  slots need the second prefix less often (sl36: a save's slot at sp0 - 8 would have been s8, a
  register's name), and the saves and restores are comment lines. Lifting every function takes as long
  as before (RISC-V clang -Os 3.1 -> 3.2 s, M3 GCC -O0 15.3 -> 13.0 s). Left: the frames whose
  address goes to a callee keep their saves (on clang -Os 45 of the 56 functions still larger,
  1,552 of their 1,728 bytes; M3 GCC -Os 63 of 163, 1,540 of 2,612 bytes): a callee reaches past
  what it was given only through a fault of the original, but that is not shown, so they stay
  (docs/decisions.md, option (c)). The rest is the C compiler's own register pressure (lifted C
  keeps more values live than the original: M3 GCC -Os 100 functions, 1,072 bytes).

Real firmware comes from many compilers, versions and options; DREAD was tested on one version of
each. CI on Ubuntu 24.04's GCC 13, binutils 2.42 and clang 18 found two gaps of DREAD's own that
GCC 16, binutils 2.47 and clang 23 never showed (a frame pointer's copies, the listing's FPU). The
comparison with the original keeps the results right whatever the compiler; what a compiler can
cost is coverage and acceptance, where the tools recognise code shapes. The way out is measuring
across compilers and replacing shape catalogues with analyses, not a profile per compiler.

- [x] **Toolchains in containers** (podman, rootless, installed by the user 2026-10-20): an image per
  toolchain, built from a distribution's packages (Ubuntu 20.04: arm-none-eabi-gcc 9.2; 22.04: 10.3;
  24.04: 13.2; Debian 12: 12.2; Debian 13: 14) and LLVM's apt repository (clang 14 to 21), with the
  RISC-V GCC of the same distributions; `dread bench` builds a recipe with `toolchain = IMAGE`
  inside the container (the compile and link steps only; DREAD's analysis runs here), the image's
  compiler versions in the report. Done when a FreeRTOS build of each image goes through the bench.
  Done 2026-10-20: five images (bench/toolchains/: Ubuntu 20.04, 22.04, 24.04, Debian 12, 13: GCC 9.2,
  10.3, 13.2, 12.2, 14.2, binutils 2.34 to 2.44, clang 12, 14, 18, 16, 19), a build's `toolchain =
  NAME`, the version each compiler gives in build.json and the report; a FreeRTOS build in an image
  takes 6 to 10 s. `bin/with-toolchain NAME COMMAND...` runs the tests with an image's compilers.
- [ ] **The benchmark across compiler versions** (first round done, 2026-10-20, below): the M3 GCC -Os and -O2 and clang -Os builds and
  the RISC-V GCC -Os build for every image; accepted per version, and the refusals and drops by
  category across versions. Each category that only some versions show is a work item (an analysis
  that does not know a shape). Done when the table is in the report and the categories are items.
  First round, -Os on the M3 (bench/freertos.toml's mps2-gccN-Os and mps2-clangN-Os), accepted:
  GCC 9 293/373, 10 299/374, 12 296/374, 13 298/371, 14 293/371, the host's 16 311/366; clang 12
  289/366, 14 285/365, 16 285/365, 18 277/362, 19 280/362, the host's 23 296/356; every listing
  rebuilt byte for byte. By origin (the link map): FreeRTOS's own functions (about 300, the same
  source) GCC 80.4 to 83.1 % against 84.7 % on GCC 16, clang 80.7 to 81.4 % against 82.3 %; newlib's
  65 to 72.5 % against 97 to 100 %: the host's newlib (Arch's) is built for size and the
  distributions' is not. Categories:
  - [x] Distribution newlib's strcmp (and kin) loads r3 under a condition and branches on the same
    condition: regflow took r3 for read, so about seven FreeRTOS functions a build were refused for
    a register nothing reads ("its callers read r3, which it sets"). regflow 16 carries the writes
    made under a branch's condition along its edges. Measured 2026-10-20 (measure-30, all 21
    builds): every version build gained 10 to 13 and lost none (GCC 9 303/373, 10 310/374, 12
    308/374, 13 310/371, 14 303/371; clang 12 302/366, 14 298/365, 16 298/365, 18 290/362, 19
    293/362), the r3 refusals are gone; most of it is regflow 16 (GCC 9's first lift check, before
    the recorded cases, 301 against 293), the rest a second round of recorded cases. The host's
    builds unchanged (GCC 16 -Os 311/366).
  - [ ] newlib's reentrant wrappers (_read_r, _write_r, _close_r, _lseek_r, _sbrk_r, also _free_r,
    _malloc_r): UNCOVERED by one to a few instructions. Not the errno store, as it seemed: newlib's
    _read stub always fails, so the code for a read that succeeded never ran. Generator 31
    (2026-10-21): a last stage stubs the callee whose call stands in front of the code still left
    (decider): the GCC 9 build's _read_r, _write_r, _close_r, _lseek_r and __sread lift; gizmo's rv32
    and msp430 gain handle_key. Left: _sbrk_r (its errno store needs a stub that writes errno: stub
    effects), _free_r, _fflush_r, __sfp, strcmp (other causes, not traced). Generator 32
    (2026-10-21): that stage runs the compare-guided stage once more with the stub in every input
    (the stub's result and what it leaves are inputs there: _sbrk_r's errno), and a store into the
    image just behind an input's value makes that input a learned pointer, also in the chase (on a
    Cortex-M a pointer given as a small number is readable flash and faults nowhere: __sfp stored
    errno through its reent pointer 0x8 when __sfmoreglue failed). On a copy of the GCC 9 build
    __sfp and _sbrk_r lift, _free_r misses 4 instructions instead of 11. Left: _fflush_r (its paths
    for newlib's fake stdio objects read the real FILE pointer from the reent, which lay in flash),
    strcmp (2), _free_r (4).
  - [ ] The -O2 builds and the RISC-V GCC builds (RISC-V newlib-nano is not packaged by the
    distributions: xPack's riscv-none-elf-gcc in an image, or picolibc). RISC-V done 2026-10-21:
    GCC 10.2, 12.2, 13.2 and 14.2 with the distributions' picolibc (bench/freertos.toml's
    virt-gccN-Os; a build's own `link` replaces the project's newlib-nano); Ubuntu 20.04's GCC 9.3
    has no C library. Flags by version: GCC 10 has no _zicsr, GCC 12 needs -misa-spec=2.2 for its
    multilib (bench/toolchains/README.md). Toolchain images are named by their file's digest now, so a
    worktree's changed image does not replace one a measurement uses. Then (the same night) the
    -O2 builds on the M3 for GCC 9 to 14 (mps2-gccN-O2) and clang's versions on RISC-V
    (virt-clang14/16/18/19-Os: the images' clang with picolibc's headers, their GCC linking; clang 14
    and 16 know no _zicsr in -march): all nine build (354 to 371 functions at -O2, 304 to 316 on
    RISC-V). Left: measuring them, and the categories they show.
    Measured 2026-10-21 (measure-33: generator 31, the ARM frame wrapper, the RISC-V GCC builds;
    2 h 15 min, every case made anew after the generator's VERSION): no build lost a function, every
    one gained. GCC -Os 9 to 16: 315, 321, 319, 318, 310, 317 (+8 to +14); clang 12 to 19: 311, 308,
    308, 304, 308 (+11 to +17); GCC -O0 354/437 (+17), -O2 266/349 (+12); clang -Os 304 (+10), -O2 278
    (+9); M4F GCC 332/394 (+6), clang 322/384 (+8); RISC-V GCC -Os 244/316 (+6), -O2 185/300 (+8),
    clang 226/308 (+9); MSP430 156/250 (+5). printf and sprintf came back on every ARM build (the
    wrapper), newlib's _read_r, _write_r, _close_r, _lseek_r and __sread on the distributions' builds
    and FreeRTOS functions behind callees that answer one way on every architecture (the decider:
    xQueueGenericReset, xTimerCreateTimerTask, the sanity checks). The new RISC-V builds: GCC 10
    234/323 (72 %); GCC 12, 13 and 14 lifted nothing: the lift check's link failed (linker
    relaxation under the overlay's fixed placement; the overlay's C is compiled with -mno-relax since,
    and a copy of the GCC 12 build lifts 199 of 321), and on GCC 12 and 13 regflow took t0 for read on
    every path to picolibc's _init_tls (its millicode call jal t0; regflow 20).
    Measured 2026-10-21 (measure-34, from a worktree at af62f26: regflow 19 and 20, the RISC-V frame
    wrapper, -mno-relax, callconv, the nine new builds; 2 h 34 min): no build lost a function. ARM
    unchanged; RISC-V +1 to +2 each (the wrapper: printf and sprintf); GCC 12, 13 and 14 on RISC-V
    lift 244/325, 246/325, 249/315 (75 to 79 %, nothing before). The new builds: GCC -O2 on the M3
    for 9, 10, 12, 13, 14: 278/371, 271/360, 266/358, 272/355, 270/354 (73 to 77 %); clang on RISC-V
    14, 18, 19: 230/316, 227/318, 226/308; clang 16 lifted nothing: it writes no RISC-V mapping
    symbols, picolibc's objects put a few into the same .text, and the import took everything of
    clang's in front of the first for data (299 of 315 functions without code bytes; the check then
    stopped on the first with an IndexError). Fixed the same morning: the import reads what lies in
    front of a section's first mapping symbol like a section without them, and a sized function
    that holds none is code where the marks say data; the check refuses a function without
    instructions. The 33 other builds import exactly as before.
    Older GCC (2026-10-21): 4.9.3, 5.4.1, 6.3.1, 7.3.1 and 8.3.1 from Ubuntu 16.04, Debian 9, Ubuntu
    18.04, Debian 10 and Debian 11 (bench/toolchains/, archive mirrors for the archived releases):
    the versions of ARM's GNU Arm Embedded toolchains that vendor SDKs shipped from 2015 to 2019.
    mps2-gcc4-Os to mps2-gcc8-Os build as they are (GCC 4.9 compiles the demo in its default C90
    mode): 362 to 373 functions. Measured the same morning (from a worktree with regflow 24): GCC
    4.9.3 309 of 362, 5.4.1 311 of 372, 6.3.1 319 of 372, 7.3.1 318 of 373, 8.3.1 316 of 373, on par
    with GCC 9 to 14 (315 to 323 of 371 to 374 in measure-34): nothing in DREAD depends on a newer
    GCC's code. What stops the rest has the same shape (25 to 35 functions whose cases leave
    instructions unrun, 18 to 20 that never return and are not run whole, 5 to 8 refused); callconv
    finds 0 to 2 disagreements a build. The whole suite under Debian 10's GCC 7.3 found test
    assumptions and one fault of the lab's (-mfpu=auto), not of DREAD's results (below).
    Measured 2026-10-21 (measure-35, from a worktree at aee092e: generator 32, regflow 21 to 23, the
    RISC-V switches with the bound first, the import of code without mapping symbols; every case made
    anew): stopped after eleven builds, the M3's GCC -Os builds and clang's. Each gained 4 to 12
    functions and lost 2 to 7 (net -3 to +6): most of the losses were configASSERT null checks
    (vTaskSetTimeOutState, xQueueReceive, xQueueGenericSend: three instructions each), which
    generator 32 had made unreachable (a store into the image at address 0 behind a null pointer
    teaches that the input is a pointer, which then takes its buffer; the compare-guided stage
    flipped the check to 0 but dropped the run that waited in the handler). Generator 33 (a null
    stage, and the compare-guided stage's retry with a stop) brings them back; which other rare
    paths a build covers changes with every change of the generator, in both directions (of eleven
    functions measure-35 gained or lost on GCC -Os, eight are accepted with generator 33; with
    generator 32 six). Generator 33's whole effect: measure-36. Since then a regeneration keeps the
    last generation's cases that still run as a case must and reach instructions the new ones do not
    (carried, autocase.Generator.carried): coverage no longer goes down when the generator changes,
    except by a case the current rules refuse. In effect from the next regeneration on (no VERSION
    bump: caches still valid stay as they are).
    Measure-38 still lost seven on GCC -O0 (the first build it regenerated from generator 31's cases:
    +10 -7). Traced on a copy: generator 31's cases of all seven pass carried's filters, but for
    prvNotifyQueueSetContainer and xSecondTimerHandler a fragile new case (dread.fragile: its outcome
    changes with the stack, or its input makes code touch a saved register; the lift check refuses it
    when the images differ) already ran the instructions, so the old case was not carried and the
    lift check then ran them in no case it compared. And the check that a saved register was touched
    kept the slots of frames that had returned: a later frame's local there counted as a saved
    register written (FreeRTOS's xQueueIsQueueFullFromISR over prvQueueAccessLogError's pushed r7),
    so a case whose images differ could be refused instead of failing: a comparison weakened by
    accident (fixed: sp rising frees the slots below it; test: a wrong outer over a reused frame
    FAILs). Since: carried counts only cases that are not fragile (Generator.sturdy), and a firm-up
    (Generator.firm_up, version FIRM, added to cached cases once without regenerating) varies a
    fragile case one input at a time to 4, 1 or 0 until a sturdy variant runs the same instructions.
    On the copy: the firm-up alone over generator 33's cached cases gets prvNotifyQueueSetContainer
    accepted (27 s for the seven); regenerated over generator 31's cases all seven are accepted. The
    other five of the bench's seven were plain search luck: their generator 31 cases are gone from
    the bench's caches (overwritten by measure-38's regeneration), so only a later search finds them
    again. Checking every case of a build for both properties costs 97 s of one core (12,116 cases of
    GCC -O0, 362 fragile); the firm-up checks only the cases that stand for something.
    The suite found what the firm-up must not do: MSP430's udivmodsi4 with a stack argument read
    from the wrong place on purpose was accepted, its cases varied to the argument 0, where the wrong
    place reads the same. Behind it a fault of the stack check: the stack moved, a stack argument a
    case gives as a memory cell at the stack top stayed behind, so every case of a function with
    stack arguments looked stack-dependent and those that differed were refused, not failed. The
    room for stack arguments moves with the stack since (Machine.call, also for dread integration's
    check): the planted mistake now FAILs in udivmodsi4's own cases. The firm-up tries 4 and 1
    before 0 (0 copies nothing and tells least).
    Measured 2026-10-21 (measure-38, from a worktree at b2b26fb: generator 33 with the carried cases
    before their fix for fragile ones, the loop cases' length, the switches seeded, pointer tail
    calls, regflow 24; the GCC 4.9 to 8 builds; 5 h 20 min, 238,500 s of CPU): all 39 builds, 11,330
    of 13,684 functions lifted (82.8 %), +605 and -21 against each build's last measurement. The
    losses are the ones traced above: seven on GCC -O0 (search luck and fragile cases), one or two on
    the -O2 builds (the same, their caches made anew from generator 31's). MSP430 (paused): dread
    callconv disagrees with regflow on all 162 functions it measured, not looked at.
    callconv against regflow: 0 to 3 functions a build disagree on ARM and RISC-V: printf's r0 and
    sprintf's r1 on ARM GCC (named arguments loaded back from the save area: regflow 21, after this
    measurement), sprintf's a2 on RISC-V GCC 10 (an unnamed argument: listed apart since). On MSP430
    every function seems to change r4 to r10 (162 of 162), a fault of the measurement there, not
    traced (MSP430 is paused).
    Measured again 2026-10-21 (measure-39, from a worktree at a35c4d2: the fragile cases' fix, the
    readable C's first stages beside it; 4 h 2 min, 122,100 s of CPU): 11,359 of 13,684 lifted (83.0 %),
    +34 and -5 against measure-38. Gained: prvNotifyQueueSetContainer on most builds, a few ISR demo
    functions. Lost: xFirstTimerHandler (GCC 10 -Os, mps2f GCC -Os), vCreateSuicidalTasks (clang 18
    -Os), vTaskStartScheduler (clang 14 -Os), prvSenderTask_7abc (GCC -O0: its loop cases' stale cache,
    known); not traced yet.
- [x] **CI on an older distribution too**: a second job on Ubuntu 22.04 (GCC 10, binutils 2.38,
  clang 14), and `pytest -rs` so that the log says what is skipped and why (CI skipped 35 tests on
  2026-10-20, here 6). Done 2026-10-20: the suite under `bin/with-toolchain ubuntu-22.04` first found
  96 failures and 2 errors, all from the default `--no-warn-rwx-segments` that binutils 2.38's ld
  does not know (now given only where the linker's --help lists it), then 10 from LLVM tools the
  images lacked (llvm-N added); then 570 passed, 6 skipped (each for a build's own reason). The CI
  matrix: Ubuntu 24.04 with Python 3.11 and 3.13, Ubuntu 22.04 with 3.11.
- [x] **What a second compiler's code needs** (found 2026-10-20 on a commercial compiler's lab
  builds: the probes' objects from a bundle, linked here with GNU ld, newlib and libgcc, so none of
  its library is involved; what the lab learned about that compiler stays in `~/dread-private/`,
  not here; below only what DREAD lacks, in general terms). `dread lift --check`
  on each build, and on GCC's lab build of the same probes for comparison; what stops functions,
  generic first:
  - [x] **regflow 17**: an instruction under a condition that sets the flags ran when the condition
    holds afterwards, so what was written under that condition stays written for later reads under
    it. Before, libgcc's `__aeabi_fmul` (`ittt ne; andsne r3, ...; teqne r2, ip; teqne r3, ip`)
    seemed to read r3, and soft-float code that keeps a value in r3 across it was refused
    (keeps_fp and op_fmul on GCC's build and the other compiler's). No gizmo table changed.
  - [x] **The run-time ABI's helpers by the standard**: `__aeabi_uldivmod` and the other helpers of
    Arm's RTABI take their arguments in r0-r3, none on the stack (except the C++ array helper
    `__aeabi_vec_cctor_nocookie_nodtor`), and return in r0, r0:r1 or r0-r3; libgcc's moves sp, so
    its stack arguments were "not known" and every caller was refused (four of the lab's probes on
    every build, GCC's too). A table in the ARM layer (stack arguments, `abi_results` as MSP430 has).
    Done 2026-10-20: `RTABI_RESULTS` (83 helpers), the lifter asks the layer after the target's
    `[abi] stack_args`, regflow 18 takes the results as read. Measured together with the next item
    (below). Gizmo's tables unchanged; on a32 `__aeabi_ldiv0` now returns r0:r1 (the standard's).
  - [x] **Calls whose results C cannot return**: the remainder of `__aeabi_uldivmod` in r2:r3, the
    flags of `__aeabi_cfcmple` and its kin (the RTABI's compares that answer in the flags; libgcc's
    own `__aeabi_fcmplt` calls them), a register the callee leaves that the caller reads: an inline-asm call that
    binds r0-r3 and reads the flags after the `bl`, for callees without stack arguments.
    Done 2026-10-20 (`_bound_call`; not for tail calls, whose callers C cannot hand r2:r3 either).
    With regflow 17 and the RTABI table, probes accepted: the lab's GCC -Os build 84 to 90 of 94
    (libgcc's float compares lifted too), the other compiler's -On 79 to 86 of 95, -Ohz 72 to 78,
    its older version's -Ohz 66 to 71. (Corrected the same evening: a script of mine had read
    "dropped  FAIL fn" as a function named FAIL, dropping failed functions from count and total; it
    had reported 85 to 91 and "every probe" at -On.) Broken on purpose (r2 cleared behind the bl),
    the 64-bit remainder failed 41 of 46 cases.
  - [x] **A pool another function loads is no part of an extent**: a function's extent ran to the
    next function, pools included, and a load from another function's extent counted as a load of
    code, so every generated input was thrown away when a function loaded a constant from a table
    behind another (the other compiler shares its constant tables between functions: a probe that
    only loads a constant got no case). `Program.extent_owners` ends an extent in front of a
    pool another function loads pc-relatively (the overlay already kept such pools). The other
    compiler's -On build 86 to 92 of 95. Elsewhere: gizmo a32's `reset` gets cases (its constants lie
    behind `irq_entry`), and the RISC-V benchmark builds' trap vector table is nobody's (cases of
    main and vOutNS16550 byte for byte the same, bin/ab-cases); no other build's extents changed.
  - [x] **Pools a rebuilt function still reads**: a function that takes its pool's address with
    `adr` (or `add rX, pc`) and loads through it reads the pool at run time, but the overlay counts
    only original functions' loads, so it builds the C over that pool: GCC's ret_ll (`adr r1, pool;
    ldrd r0, r1, [r1]`) and, at -Ohz, the other compiler's ret_s1, ret_s4, ret_s8, ret_s12 fail.
    The lifter could fold such loads into constants as it does `ldr rX, [pc, #d]`, or the overlay
    keep pools a replaced function's adr points into. Done 2026-10-20, the second: the overlay
    counts a replaced function's adr targets as loaded (its pc-relative loads stay excluded: the
    lifter makes them constants). GCC's -Os build 91 of 94 probe functions, the other compiler's
    -Ohz 79 to 83 of 95. Folding the loads instead (the pool then free for other C) is left for
    when the room matters.
  - Measured 2026-10-21 (measure-31: regflow 17, the RTABI table, bound calls, the extent and pool
    fixes, before shared code): the FreeRTOS builds unchanged but the hard-float ones, +6 each
    (mps2f-gcc-Os 322 to 328, mps2f-clang-Os 310 to 316: libgcc's __aeabi_dcmpeq/ge/le/lt, which read
    the flags of __aeabi_cdcmple, and vCompetingMathTask1/2 that call them); no build lost one.
  - [x] **A va_list that walks from the saved argument registers into the stack arguments**: a
    variadic function saves its argument registers next to its stack arguments, and a va_list walks
    from the one into the other; the lifted C's frame is a local array, not next to its incoming
    arguments. Found 2026-10-21 to be a false acceptance on the benchmark: FreeRTOS's sprintf and
    printf (GCC and clang, ARM and RISC-V) were accepted, and the rebuilt sprintf printed a stack word
    of its own for its third argument (sprintf(buf, "%d %d %d", 11, 22, 33): "11 22 -1513889519"):
    no generated case passed an argument on the stack. Now refused: an address taken in the save
    area (the first push of argument registers ending with r3, or dread.regflow's
    variadic_save_area; RISC-V too, where addi also learnt the refusal of a stack argument's
    address). That also refuses functions that only store such an address and never use it (GCC's
    pick_va_5 of the compiler lab: sound before, a loss now).
    Done 2026-10-21, the wrapper: where the translation is refused only for an address in the save
    area or of a stack argument, the function is lifted again wrapped (Lifter.WRAP, can_wrap): a
    naked entry (mov r12, sp; sub sp, #frame; push {r12, lr}; bl NAME_dread_body; pop; add sp; bx
    lr) reserves the original's frame on the real stack and hands the body that stack pointer as
    sp0, its fifth argument; frame and stack arguments are real memory, as in the original. The
    rebuilt sprintf of the benchmark prints what the original prints with 2, 3, 5 and 8 arguments.
    The lab's probes: GCC -Os every one (94; the three that take a stack argument's address too),
    the other compiler's -On 94 of 95, -Ohz 95 of 95 in both versions. ARM state and Thumb-2 only:
    RISC-V and Thumb-1 (which cannot push r12) keep the refusal. RISC-V done 2026-10-21: its entry
    (mv t0, sp; reserve the frame and 16 bytes; sw ra; sw t0 as the body's ninth argument; call;
    restore; ret) wraps the va_list walks and also the functions that take arguments beyond the
    eighth, refused before; the rebuilt sprintf of the RISC-V GCC build prints what the original
    prints with 2, 6, 7 and 10 values (six arguments on the stack). Thumb-1 keeps the refusal.
    Measured 2026-10-21 (measure-32, the refusal, before the wrapper): every ARM build lost printf
    and sprintf and nothing else. The RISC-V builds lost them too and gained functions that had
    failed in every measurement since 2026-10-20 (virt-gcc-Os prvInitialiseNewTask_constprop_0 and
    xPortStartScheduler, 238 of 316; virt-gcc-O2 those and prvRegTestTaskEntry1 but not xTaskCreate,
    177 of 300; virt-clang-Os prvInitialiseNewTask and prvRegTestTaskEntry2, 217 of 308). Not from
    the refusal: the measurement made most of the image's cases anew (their key holds the whole image
    and dread.regflow's results), and measure-31's code accepts the two of virt-gcc-Os and printf
    with the new cases (a full check on a copy). Why the cache was renewed, and why the old cases
    failed them, is not traced.
  - [x] **The caller's r4-r11 at the entry, for the C**: the other compiler keeps the stack aligned
    with `push {r7, lr}` and drops the pad on the way out into a scratch register, `pop {r0, pc}` in
    a function without a result (its -On make_nt): r0 then holds the caller's r7. The lifter does not
    store a saved callee-saved register (only the matching pop reads it back, it assumed), so the
    lifted r0 is an unwritten frame word, and the comparison, which always compares r0, refutes it.
    Not comparing r0 where no caller reads it would weaken the comparison (rule 6). Done 2026-10-21,
    not with an entry stub but with GCC's -ffixed-REG: entry_states follows what the caller left
    through frame slots too (a pop into another register hands it on) and takes the result registers
    as read at a return; registers among r4-r11 the function reads so are handed in: the file is
    compiled with -ffixed-REG (a "/* dread: fixed */" line), so GCC never touches them, the C reads
    them at the entry, and a caller's call binds them (bound calls). This also lifts functions that
    were refused for reading what their caller left in r4-r11 (GCC only; clang has no such option
    for these registers). The other compiler's -On build 92 to 93 of 95.
  - [x] **Code shared between functions** (a compiler's cross call or outliner, at high
    optimisation for size): functions that branch or fall into common code with their frame still
    open, and common code called by `bl` whose callers read what it leaves; with a symbol of its own
    or only a local label, so to DREAD it may belong to no function. Lift it as part of each function
    that enters it (as IDA's tail chunks); a two-byte function (one `b.n` into it) stays original
    until functions can move. Done 2026-10-20, the second: `Lifter.shared_code` makes a function a
    `Body` (its own instructions, then the pieces of shared code it falls or branches into with its
    frame open, or that belong to no function), the build records the bodies in lifted.json, and the
    runner and the generator cover them (coverage spans the whole body). A branch into another
    function with the frame open is refused instead of being taken for a tail call (the C's frame
    would be popped by the original's tail), and the overlay keeps an extent closed where original
    code branches into it (code no function owns behind a function that took it in: three callers
    of a two-byte `b.n` into it failed before). The other compiler's -Ohz builds: 83 to 85 of 95
    probe functions, its older version's 77 to 85 (its shared code has no symbol); -On and GCC's
    unchanged. What stays at -Ohz is mostly two-byte functions (below).
    Also 2026-10-21: behind a conditional return the shared piece goes on (libgcc's double helpers
    share __adddf3's tail, which returns with popge {r4, r5, pc}): the hard-float builds refused six
    for falling through there; on a copy of the GCC one two lift now (__gtdf2, __ltdf2), three stop
    at r12 read on the path where a predicated write did not run (its result used only under the
    same condition), one at coverage. Then r12 is handed in like r4-r11 (the C gets it as the caller
    left it, the file compiled with -ffixed-r12): the three get past it and stop at coverage too
    (__adddf3's tail for subnormal results, which a conversion from an integer never makes: not
    reachable from those entries, not a gap of the generator's).
  - [x] **Two-byte functions**: a function that is one `b.n` into shared code (or a `nop` that falls
    into it) leaves no room for the four-byte branch to its C. A `b.n` reaches 2 KB: a four-byte
    trampoline (or the C itself) in freed room within reach would do (eight of the other compiler's
    -Ohz probe functions, its older version's too). Done 2026-10-20, the second: the overlay places
    such a function first, its C into room within reach if it fits there, else a trampoline
    (`placement.json`'s `via`); the entry gets a narrow b (only its two bytes may change), and the
    emulator enters through it so that the cases test it. Both of the other compiler's -Ohz builds
    94 of 95 probe functions (85 before); what stays is keeps_fp.
  - [x] **A callee that reads a register its caller keeps**: the other compiler's shared code called
    with `bl` may start `mov r1, r6; b.w __addsf3`, so it reads its caller's r6. A copy counts as no
    use, and a call or tail call counted no use of its arguments either, so the callee seemed to read
    nothing its caller leaves, and the lifted caller called it with whatever the C held in r6 (its
    keeps_fp failed its comparison). Done 2026-10-21: entry_states takes the arguments a callee reads
    (regflow's, r0-r3 without it) as read at a call, a tail call and a fall into another function,
    and names the caller's register a copy came from; a bound call binds such registers (r4-r11) from
    the C's variables. The other compiler's -Ohz build: every probe function (95 of 95).
  - [x] **A call into code no function owns** (`bl` into the other compiler's unnamed shared code, its
    older version: keeps_fp): the callee needs a name (a function of its own at the target, as
    import does for calls it finds), then bound calls take what it leaves. Done 2026-10-21:
    import-elf makes the target of a direct call into code no function holds a function, FUN_address,
    up to the next function (as Ghidra would in a stripped image). Its older version's -Ohz build:
    every probe function too (95 of 95).
  - [x] **A lift step in the lab**: `dread cclab run --lift` gives each build a `[compiler]` (the
    rebuilding GCC with the build's cpu) and runs `dread lift --check`; the probes' verdicts go into
    results.json, `report` and `compare` ("lifted N of M"). Then a run at work checks the real images
    with the compiler's own library. Done 2026-10-20 (docs/cclab.md, "What DREAD makes of a
    compiler's code"): the float ABI as measured (a float argument in s0), an adapter's `[rebuild]`
    for others; the probe functions only, one build after the other.
  - [x] **A bundle's objects relinked at home by an option**: the probes' objects a compiler made
    elsewhere, linked here with the GNU toolchain (newlib, libgcc, the two C++ type_info vtables), so
    that its code is measured and lifted without its library (2026-10-20: a hand-made adapter in
    `~/dread-private/`, whose compile step copies the bundle's object). Done 2026-10-21:
    `--adapter relink:DIR` (docs/cclab.md, "A compiler that runs elsewhere").
  - [x] **The probe left at -On**: the other compiler checks what operator new returns, and its path
    for a null result reads through the pointer; the real allocator never fails, and nothing was
    mapped at address 0. Done 2026-10-21: generator 31's last stage stubs operator new (its second
    pick; the constructor's call stands nearer), and the lab maps a page at 0 on ARM (a Cortex-M
    part's boot alias is readable): that probe function is accepted (checked on its own; the whole
    -On build not measured again). What is left of the item needs the user's machine: the lift check
    at work on the real images, with the compiler's own library (`dread cclab run --lift`).
- [x] **A cclab adapter for Compiler Explorer** (its public API: 52 ARM GCC versions, 21 ARM clang,
  48 RISC-V GCC, 42 RISC-V clang, TI Arm clang, 17 MSP430 GCC, SDCC on 2026-10-20): the probes
  compiled there to assembly, assembled and linked here, measured in the emulator as now; answers
  cached by compiler, options and probe; at most a few requests a second. Only DREAD's own probes
  go there (rule 1; as for language models).
  Done 2026-10-20 (docs/cclab.md, "Compiler Explorer's compilers"): `--adapter ce:ID`, `dread cclab
  ce-list`; GCC 13.2 against the local GCC 16: the same on all 62 probes that matter for linking,
  four differences in the code. Not yet: RISC-V clang (no built-in clang-riscv adapter to start
  from), SDCC and TI's Arm clang (their own adapters first).
- [x] **One analysis of base registers instead of shapes**: what each register holds as sp plus a
  constant, an argument plus a constant, or a constant, through every move and add, for regflow's
  save areas and frame stores, the lifter's frames and the generator's frames (regflow 15 followed
  one more shape; the next compiler brings another). Done 2026-10-21 for the frame (regflow 24): the
  lifter's states were that analysis already (sp minus a constant and constants through moves, adds,
  subs and frame slots), and the generator took its loop frames from them; regflow had shapes of its
  own (the layers' sp_copy, base_copy, frame_stores, and frame_slot for sp-relative slots only). Now
  regflow's Frames reads the states: the save area, the spills on the entry run, saves and restores
  around a call (paired by depth, through any base) and whether a frame address is taken; the
  layers give slot_access (a single-register store or load with its base) instead. What the states
  follow and the shapes did not: a sub from a copy of sp, a frame address kept in a slot and loaded
  back, an address taken through a frame pointer (a save and restore around a call whose callee got
  the slot's address was taken for a copy through the slot). On the benchmark's 34 builds and gizmo
  no answer changed; regflow takes about a sixth longer (the states are made only where a cheap
  check says they can matter). Argument plus a constant: left until something needs it.
- [x] **Calling conventions measured, not only read**: a function run with poisoned registers says
  which it reads before it writes them and which it changes; regflow's answer is checked against
  that on every build of the benchmark, disagreements listed. Done 2026-10-21: `dread callconv`
  (each function's cases on the original, once more per register with another value at the entry:
  a different outcome is a read, a register not as at the entry a change; a case whose finding comes
  from code touching a saved slot is left out, as dread test refuses it) and the bench step
  `callconv`. Gizmo agrees everywhere; FreeRTOS's GCC builds show regflow's gaps, below. A few
  seconds a build.
- [x] **regflow follows values through stack slots** (found by `dread callconv`, 2026-10-21): what a
  function stores in its frame and loads back is the same value, and regflow does not follow it.
  (1) A named argument kept in a variadic save area and read back from it: printf pushes r0-r3 and
  loads its format from r0's slot (variadic_save_area takes a push of all four for -O0 keeping its
  arguments, and pushes count as no use), sprintf pushes r1-r3 with its format in r1 (the rule
  expects one named argument): regflow says they do not read r0 and r1, so a stub of printf would not
  log the format a caller passes. (2) A register saved around a call and restored, then used:
  newlib's _free_r spills r0 (the reent pointer) around __malloc_lock and stores errno through it
  (regflow 4: "a save around a call of a register never set before is neither a read nor a write").
  The way: liveness of frame slots as of registers (a store to a slot reads its register only when
  a later load from that slot feeds a live register), which would also replace the special cases of
  regflow 4 and 13. On GCC 16 -Os: printf, sprintf, _free_r; GCC 9 and 10: printf, sprintf.
  (2) done 2026-10-21, regflow 19: the cause was narrower. regflow 4's pair (a save and restore
  around a call of a register that still holds the entry's value) did read the register through the
  call, but __malloc_lock writes r0 (newlib's retargetable lock: the lock's address for the lock
  routine), so the call ended r0 in front of the restore. The pair is now a copy through the slot:
  the save reads the register when it is live behind the restore, which writes it within the
  function. On three FreeRTOS builds (GCC 16 and 9 -Os, RISC-V GCC) one answer changed, _free_r's
  (reads r0 r1); gizmo none. (1) done the same night, regflow 21: an argument register an entry
  push of the argument registers saved is read where a load at a known offset reads its slot back
  (the lifter's states() say what each register holds as the entry's stack pointer minus a constant;
  the ARM layer's load_address): printf reads r0 and sprintf r1 on every GCC build (no other answer
  changed on eleven FreeRTOS builds and every gizmo build; clang keeps those arguments in registers).
  The unnamed registers a va_list walks over stay no use, as before. regflow uses the lifter's frame
  analysis here for the first time, a first step of "one analysis of base registers" (below).
  (3) found by callconv on GCC -O2 the same night, regflow 22: the variadic rule took a lone r3
  stored at the entry for a save area of one register (three named arguments before it): FreeRTOS's
  xStreamBufferSend spills its fourth argument far below the entry, and its r3 counted as no use on
  every GCC build. A save area now has to lie right below the stack arguments (the last argument
  register a word below the entry's stack pointer: the lifter's states and the layers' store_slots);
  xStreamBufferSend reads r0-r3 since, nothing else changed (thirteen FreeRTOS builds, gizmo).
  (4) regflow 23 (the same night): GCC 10 on RISC-V copies registers nothing set into callee-saved
  ones and never reads them again before restoring them (xTaskCheckForTimeOut: c.mv s0, a4;
  xStreamBufferReset, prvCheckForValidListAndQueue: c.mv s1/s0, a5): a4 and a5 seemed inputs, and
  through vAssertCalled and the list functions 13 functions of that build were refused for registers
  their callers seemed to read. An instruction that only computes callee-saved registers nobody reads
  afterwards reads nothing now: the callee-saved registers are followed inside each function, a call
  using what its callee reads of its caller's (a summary for all functions: none for ordinary C, its
  r6 for a compiler's shared code). Functions with code no edge reaches are left as they are (GCC 12's
  RISC-V xTaskGenericNotifyFromISR: its switch's cases were not all found, below). 16 answers of the
  GCC 10 RISC-V build changed, none elsewhere (fifteen FreeRTOS builds, gizmo).
- [x] **Switches whose table or bound lies in a register, and tail calls through a pointer**
  (2026-10-21, from the categories of measure-34: every RISC-V build refused prvBlockingTask,
  prvTimerTask and prvProcessExpiredTimer, every ARM build prvProcessExpiredTimer). RISC-V code
  loads a switch's table address in front of the loop into a callee-saved register (GCC: s1, s2),
  clang the bound as well (s5), and GCC adjusts the index between the bound's li and the branch.
  dread.switches seeds its runs with the constants the lifter's states know at the run's start (a
  plain lifter: its own switches unseeded, no dread.noreturn, so nothing recurses; the RISC-V states
  learned lui, auipc, li and addi of a constant), takes a register known to hold a constant as the
  bound, and lets the bound's li stand behind a write of the index. prvProcessExpiredTimer ends with
  the timer's callback, a jump through a register behind the epilogue: with the frame gone and the
  return address back in its register (switches.pointer_tail, strict: a frame elsewhere in the
  function, no run landing inside) it is lifted as a call through the register whose result is
  returned, and it counts with the calls through a register, so generated inputs give it the pointer
  stub (one ARM case pointed it into the middle of a function, where the real jump and the C's call
  ran on other stacks: r4 differed). Every indirect jump of the benchmark's builds and gizmo, main
  against this: only the intended ones changed (21 jumps on the RISC-V builds); regflow's answers
  unchanged. On copies of four builds prvProcessExpiredTimer and prvBlockingTask lift; prvTimerTask
  is a matter of coverage now (a task that never returns).
- [x] **RISC-V switches with the bound first** (found 2026-10-21 by regflow 23's check): GCC 12's
  RISC-V xTaskGenericNotifyFromISR checks its switch index with bltu a5, s3 (the bound 4 in a5, the
  index in s3) and jumps through a table in .rodata; the switch resolution found one of the five
  cases, so the others have no edge in regflow's graph (and the flat listing says "goes to L12").
  Probably the bound check is read with the operands the other way round. RISC-V's most frequent
  refusal on the benchmark ("jumps through a register to targets not known") may be the same.
  Done the same morning, the cause another: the li of the bound was joined to the branch only right
  in front of it (an andi of another register stood between), and that andi was taken for the bound
  check, so the run went the same way for every value. The RISC-V layer takes the li a few
  instructions in front as long as nothing else writes its register, and dread.switches takes a
  bound check only when its register feeds the jump. Every indirect jump of thirteen FreeRTOS builds
  and seven gizmo builds compared: xTaskGenericNotify and xTaskGenericNotifyFromISR on the RISC-V
  builds of GCC 10 to 14 had one case of five (now five), on clang's RISC-V build none (now five);
  nothing else changed.
- [ ] **Refusals to a language model as rule writer**: the categories the benchmark leaves (a code
  shape the tools do not know) go with their listings to a model that proposes the rule (a
  recognizer, an input, the C); nothing is taken that the comparison does not accept.
- [ ] **`dread whatcc` by matching**: a function's C compiled with many compilers and options until
  the bytes match the original's, as matching decompilation does (decomp.me); on Compiler Explorer
  for open targets only, with a Compiler Explorer of our own in podman for private ones (C written
  from a private image must not leave the machine). First step done 2026-10-21, without C: the
  libraries. dread whatcc takes the C library, libgcc and libm of every toolchain image (and the
  host's, and archives given with --archive: a vendor's at work, kept local) for the image's
  multilib, makes each library function a signature (its bytes, the relocated ones masked) and
  compares them at every function entry of the image. On the benchmark's ARM builds 26 to 49
  functions match; the toolchain with the most is the one that linked the build wherever their
  libraries differ, and a tie otherwise (Debian 11's and Ubuntu 20.04's newlib give FreeRTOS the same
  35 functions; said as a tie); every name given as certain is right (23 of 23 on GCC 7's build, 39
  of 39 on the M4F's). Names that land on several functions are given as ambiguous (newlib's __errno
  has the code of every getter: FreeRTOS's xTaskGetTickCount; the nosys stubs), and a match whose
  calls go to functions matched under other names is dropped. Left: RISC-V (its linker relaxes calls
  and shortens linked code: 3 to 12 functions match there), and compiled C (the item itself).
- [x] **Tests that state properties, not one compiler's output**: the tests that compile snippets
  checked for what they assume of the code (three CI failures on 2026-10-20 were such assumptions);
  the snippet tests run in the CI job of the older distribution too. 2026-10-21: the whole suite
  under Debian 10's ARM GCC 7.3 (bin/with-toolchain debian-10; the image came with the older GCC
  builds): 603 passed, 6 failed, none of them DREAD's results. Four snippets relied on noipa, which
  GCC 8 introduced: GCC 7 ignores it and inlined the helper whose call the test is about (tests
  now use NOIPA from conftest: noipa, or noinline and noclone before GCC 8, noinline for clang).
  The lab's rebuilding compiler asked for -mfpu=auto, also GCC 8's, and every hard-float lab build
  gave no verdicts (a fault of DREAD's: cclab now asks the compiler and names the core's FPU
  otherwise). The full-room test assumed 256 bytes hold gizmo's hand-written C (GCC 7's did not fit,
  and the check had nothing left to drop): the room is measured now. Then under Ubuntu 16.04's GCC
  4.9.3: 607 passed, 6 failed, again all assumptions of the tests: two snippets declared in for loops
  (C99; GCC 4.9 defaults to C90), the C++ probe expected the sized delete (_ZdlPvj, C++14: GCC 5 on),
  GCC 4.9's libgcc __aeabi_uldivmod moves sp in a way the walk counts (the test asserted the refusal
  newer ones get), and GCC 4.9's code of the chase test is covered without the chase (it skips now,
  saying so). Then under Ubuntu 20.04's clang 12 and GCC 9.2: 619 passed, none failed. With CI's
  Ubuntu 22.04 and 24.04 (GCC 10 and 13, clang 14 and 18, their RISC-V GCC) and the Debian 12 and 13
  runs (GCC 12 and 14), the suite has run on GCC 4.9 to 16 and clang 12 to 23.
- [x] **The pilot without names**: Haiku's inputs on a copy of the GCC -Os build with every name made
  FUN_address and the calls' names gone too: how much of what it finds comes from knowing FreeRTOS
  (open source, its names in the listing) and how much from reading the code. Private firmware has
  no names. Done 2026-10-21, on copies of measure-33's GCC -Os build (26 functions with 142 missed
  instructions), one with every function renamed FUN_address (dread rename) and the data symbols
  dropped (strings in the image stay, as in a private firmware), Haiku 5.5's --task input three
  times each:
  - The question as it was: named 7, 9, 10 functions covered whole and 37, 44, 47 instructions;
    nameless 6, 6, 7 and 27, 27, 30 (about $0.09 a run: 240,000 tokens in, 130,000 out). Names won every time on vTaskDelete,
    xTaskGetHandle and vTaskSuspend: there Haiku set up FreeRTOS's lists by what the names say.
    Without them it wrote that it could not see the helpers' code, and its inputs faulted inside
    them, which the tools reported only as "does not finish".
  - With the callees' listings in the question and a stopped input told where and why (pilot
    --task input since): named 11, 10, 9 and 44, 50, 36; nameless 9, 11, 10 and 47, 52, 47. The gap
    is gone: nameless vTaskSuspend went from 0 of 13 in all three runs to 8, 10, 8. A run costs about
    one and a half times as much ($0.14: 440,000 tokens in, the listings; 65 answers as before).
    (Each run's own summary had counted every run beside it: the input task took the difference of
    the shared usage ledger's totals. Providers count their own answers since, Provider.spent; the
    figures here are the ledger's per batch, divided by the runs.)
    Not separated: what the listings and what the stop reasons each bring (a named run with stop
    reasons only would say whether named firmware needs the listings, which cost most).


## Compiler lab (`dread cclab`; the user, 2026-10-19: before anything else)

Put a compiler in and learn how it compiles: a corpus of small probe programs, compiled across a
matrix of options, each answering one question by running the code in DREAD's emulator (where
arguments arrive, what survives a call, what a runtime helper computes and clobbers) and by reading
the object (the helpers each construct calls, prologue and switch shapes, outlined code). First for
IAR EWARM (ARM). It runs on the user's work computer, which has IAR, GCC, clang, WSL and Claude
Code but no access from here: DREAD runs in WSL there, calls IAR's Windows executables through WSL's
interop (paths converted with wslpath), and must work without being tried against IAR here. What it
learns about IAR stays out of this repository (rule 1); the harness is public. An LLM (Claude Code at
work, or one here) sees only our probes, their compiled code and the compilers' own messages:
nothing of the user's company, and not IAR's runtime code (the user, 2026-10-19). The lab keeps to
that by construction: what it hands an LLM comes from its probe corpus and its work directory, with
IAR's runtime code marked and left out.

- [x] **Core** (`dread cclab`, `docs/cclab.md`): compiler adapters in a TOML file (executables, command templates for
  compiling, linking and listings, the option matrix); adapters for arm-none-eabi-gcc and clang,
  tested here; the probe format (C, plus a TOML of what each probe measures); the matrix runner
  (parallel, cached); a target of its own per build for the emulator; results per probe as JSON.
  First probes: where arguments and results go (ints, 64-bit, structs, varargs), what a call keeps.
  Done with GCC 16.2, 14.2 and 10.3, clang 23.1, Arm's ATfE 23.1 and TI's armcl 20.2.7 (installed
  for it in ~/.local/opt; TI's needs its own linker family, a TI linker command file): 70 builds,
  the AAPCS facts as the standard says. Not yet: caching builds (a run of twelve takes a second).
  Since then for any architecture DREAD emulates (the user, 2026-10-19: "You can make it also
  generic, but it should also run under my work constraints"): registers, widths and memory maps
  from the architecture layer, C-typed values in the specs, arguments passed by reference, the
  guard per adapter (`guard = true`, `--guard`) with `[llm] only` in each build for dread pilot;
  GCC for RV32 and TI's MSP430 GCC measured, each standard's fine print as written.
- [x] **The IAR adapter**: iccarm and ilinkarm through WSL interop (wslpath, the work directory on
  the Windows side), an ILINK configuration (.icf) written for the emulator's memory map, the
  probes kept from ILINK's removal of unreferenced code; written from IAR's documented command
  line and tested here against a stand-in that speaks it over GCC. Its options checked against IAR's
  EWARM 9.70 documentation (`--cpu=`, `--fpu=none`, ILINK's `--config`, `--entry`, `--keep`);
  EWARM has no cross-call optimisation (that is IAR's MSP430 and 8051 compilers').
- [x] **`dread cclab doctor`**: finds EWARM's compilers (Program Files\IAR Systems\...\arm\bin)
  and the other compilers, prints their versions, compiles, links and runs a trivial probe, and says
  which step failed and what to change in the adapter file; with an LLM attached it proposes the
  change from the error and help texts, and the doctor tries it before keeping it. (The LLM is
  Claude Code at work, reading the doctor's output and editing a copy of the adapter: no client of
  the lab's own. TI's compiler showed the use: no include path by default, the doctor printed it.)
- [x] **The probe set**: calling convention (ints, 64-bit, structs by value and returned, varargs,
  soft and hard float: s0-s15, back-filling, structs of floats, variadic doubles), callee-saved
  registers (s16-s31 too) and the stack alignment kept at calls, the runtime helper each operation
  calls and its register contract, entry and exit instructions, how a switch dispatches (on a switch
  whose cases do different work), division by constants, memcpy, memset and struct copies, tail
  calls, literal pools, function alignment and padding. Measured on six compilers and three
  architectures, every standard's fine print as written; where one compiler departs (TI's armcl
  does not back-fill), compare names it. Cross-call fragments are IAR's MSP430 and 8051 compilers'
  (not EWARM's): under More compilers, "IAR profile".
- [x] **Profile, report and bundle** (the profile so far carries the runtime helpers: names, result
  registers from the register flow on every build, registers changed; regflow takes the results
  from `[compiler] profile`; switch and epilogue shapes and prologue signatures for the other tools
  are open, below): a profile TOML the tools read (`[compiler] profile`: helper
  contracts for regflow, switch and epilogue shapes for dread.switches and the lifter, prologue
  signatures for finding functions), a self-contained report, and a bundle the user brings home,
  with IAR's runtime code marked as such.
- [x] **With an LLM** (Claude Code at work, or here on a brought-back bundle): the report's narrative
  and probes for what the corpus does not answer, each one compiled and run before it counts.
  `--probe-dir DIR` adds a directory of one's own probes (C files with their TOML specs); a guarded
  work directory's CLAUDE.md asks Claude Code to write such a probe into own-probes/ (plain C for
  the question, never the company's code), says how to read the reports and the comparisons, and
  asks for every claim of an explanation to cite the probe and the measured values, or to become a
  new probe. No client of the lab's own: Claude Code is the one at work. Its deny rules keep it to
  the probes and their results: not IAR's runtime code, nothing outside the lab.
- [x] **`dread cclab compare`** (from the user's question how the lab fits DREAD's workflow, 2026-10-19):
  two compilers side by side, where the calling convention, the types and what a call keeps differ
  (what linking rebuilt C into another compiler's image depends on), the options of the rebuilding
  compiler that close a difference, and apart the code's differences. TI's armcl against GCC: one
  difference that matters (wchar_t, `-fshort-wchar`), 42 probes agree. Open: feeding the options
  into a target's `[compiler] cflags` by itself, and the pilot's question naming the original
  compiler's facts where they differ.
- [x] **LLM providers for work** (the user, 2026-10-19: "We use GitHub Copilot at work. Could we
  attach that to dread?"): `kind = "command"` runs a command line with the question (in an argument,
  a file or on standard input) and reads the answer, in an empty directory without the environment's
  keys: GitHub Copilot CLI's `copilot -p`, Claude Code's `claude -p`, the local model's wrapper
  (tried with that: 39 s for one question). GitHub Models through `kind = "openai"` with `path` and
  `headers`. `dread llm ask` and `dread llm providers`. Copilot and GitHub Models cannot be tried
  here: their presets come from GitHub's documentation (2026-10). Open: the Copilot SDK (GA since
  2026-06) if the CLI's headless mode turns out too coarse at work.
- [x] **C++ probes** (2026-10-19): `.cpp` probe files compiled by an adapter's `compile_cxx`, the
  lab's C++ pieces (operator new and delete, __cxa_pure_virtual) where no library is linked, probe
  functions by mangled name (aliases at one entry found: GCC's complete and base constructors).
  Measured on six ARM toolchains, RISC-V and MSP430 GCC: `this` in the first argument register; a
  constructor returns `this` on ARM (the ARM C++ ABI), not on RISC-V (plain Itanium); a class with a
  destructor returned through memory; a virtual call (GCC -O2 devirtualises it on a guess); new and
  delete. Open: exceptions (an unwinder in the build), RTTI, static construction (the startup's
  init_array, which the lab does not run).
- [ ] Later: `dread whatcc` from stored profiles (More compilers), and a search for the options that
  reproduce a function's bytes.

## Questions for the user

What only the user can do (provide files, credentials, system software) or decide (what cannot be
undone). Other decisions are taken by the recommended option and written down in
`docs/decisions.md` for the user to review (the user, 2026-10-20). Work goes on around them.

- **IAR-built gizmo?** (2026-10-20: the lab ran at work on EWARM 9.70.2 and 8.42.1, every probe
  through in 24 builds after four changes, now in the adapter and docs/cclab.md; the bundle is in
  `~/dread-private/iar/`, what DREAD lacks for that code are items under Compiler diversity.) For the IAR profile: gizmo built with IAR EWARM (Cortex-M3) and, for MSP430,
  EW430, with the ELF (debug info on) and the map file, put somewhere outside the repository (say
  `~/dread-private/iar/`). Tests would use them when present and skip otherwise. Which IAR versions
  and optimisation levels matter to you?

## Phase 4: cases, mutation, coverage (rest)

- [x] **A call of a callee that never returns compares no arguments.** dread.autocase's stop stub (a
  case's `stops`: a callee that never returns, or one a run hung in) logs only that the call came: the
  arguments of the call that ends the run are compared by nothing (found 2026-10-26 with the printf
  formats: on FreeRTOS GCC -Os, prvAutoReloadTimerCallback's cases stop at vAssertCalled in 11 of the
  69 that reach it, and its file and line went unseen there). Log what the callee reads (reads_mask,
  and the registers a format takes: format_mask), as the call stubs do: it adds to what is compared,
  and the cases stay as they are (the stub reads the mask at the run).
  Done 2026-10-26 (autocase stop_mask): a stop logs the registers the callee reads and those its format
  takes; it fills nothing, so the cases run as before. The gate accepts the same functions on the eight
  builds (gizmo a32 35, m3 36, m4 44, rv32 31; FreeRTOS GCC -Os 328, clang -O2 288, RISC-V GCC -Os 252,
  GCC -O0 378): no readable C passed a stopped callee other arguments than the original.
- [x] **Why the cases leave code unrun, by code** (`dread uncovered`, 2026-10-23; asked for before the
  generator gets new work: on a private firmware, DREAD's real-life test, the lift check drops functions
  as UNCOVERED, some without a single instruction run). For each
  function a lift check dropped for missed instructions (verdicts.tsv): the missed code in regions, each
  region's way in (branch, switch, call, straight, not entered, unreached), and for a branch or switch
  what decides it traced back to its source (argument, callee's result, stubbed or real, global, device
  register, field by its base, local, loop counter, system register, constant); with the cached cases:
  the values seen where the branch was decided, the search's stubs, stops and polls, and a forced run
  sent the missed way (returns, faults, the limit and where, touches the image). On the 39 FreeRTOS
  builds' last lift checks (1,910 dropped functions, 7,432 regions, 64,616 missed instructions; 151 s for
  all of them): of what the ordinary functions' cases miss (22,057 instructions) 54 % lies behind a
  branch on a real callee's result (most of it the demo's own checks that its queue and stream-buffer
  calls answered as expected; forced that way, 57 % of everything ends in vAssertCalled's wait for a
  debugger), 13 % behind a stubbed callee's answer the search never gave, 9 % behind a global, 8 %
  behind calls the cases stop at (vAssertCalled) and 6 % behind a field of an argument; of what the
  functions that never return miss (41,450) 56 % lies behind a loop that needs more rounds than a loop
  case runs and 23 % behind a stubbed callee's answer, and no loop case gets to the frontier of 51 % of it
  (only the system run does). The bench's caches were made by an older generator: its cases are taken
  as they are, and the measurement is of the cases those lift checks ran. Not done: MSP430 and MIPS have
  no value_step, so memory through a register is "unknown" there; the system run's share of a function
  that never returns is not run again.

- [x] **The frontier answers** (2026-10-23; the next step after `dread uncovered`: give the generator the
  values it lacks). Generator.answers, a step after the search (its cases stay byte for byte, so VERSION
  stays; ANSWERS keys the answers with the generation's key and the kept and written cases, in the cache
  entry's "answers"): for each conditional branch that the function's generated, kept and written cases
  together (what the lift check runs; a recorded case gets past checks no generated input does) take one
  way only, dread.uncovered's trace names what decides it, and a case made from one that got there gives
  that the value the other way needs: a real callee's result (a stub that answers only that call, the
  fourth element of a case's calls, the callee running at its other calls), a stubbed callee's answer at
  that call, the memory the deciding load read (a global, a field of an argument's or a global's object, a
  device register; the address from the run), what a stubbed callee stores in a local it was handed (a
  fill, with the function's frame and a tight fill). Values: the compare's other operand and its
  neighbours, the operand and its neighbours, a constant offset solved from two runs, the edges, single
  bits; repairs: a hang (the callee stubbed, then a stop), a fault or a store into the image (chased), a
  stop whose callee may return (stubbed). Measured on copies of six FreeRTOS builds, generator 34 from
  scratch and then the answers on its cached cases (accepted before and after; what ordinary functions
  miss, instructions; the answers' CPU seconds): GCC -Os 329 -> 340, 232 -> 56, 21 s; clang -O2 281 ->
  294, 1,046 -> 361, 99 s; GCC -O0 358 -> 381, 2,202 -> 1,186, 32 s; RISC-V GCC -Os 256 -> 269 by the
  cases, 227 once the whole-image check dropped 42 (the open item below), 378 -> 132, 19 s; M4F GCC -Os
  337 -> 349, 478 -> 275, 18 s; GCC 4 -Os 314 -> 323, 202 -> 87, 16 s. Nothing lost but by that check.
  Over the six builds ordinary functions miss behind a callee's result 2,707 -> 690 instructions, behind
  a field 588 -> 278, behind a global 375 -> 251 (fewer branches, but code reached newly shows new ones:
  -O0 80 -> 171); functions that never return 6,328 -> 6,000 (most of it behind loops that need more
  rounds than a loop case runs). Of the answer cases on five builds 219 start from a recorded case, 119
  from a generated one. The lift check on cached cases with the answers took 64-151 s instead of 537-703
  s from scratch (RISC-V 709 s: its culprit search). gizmo: every lifted table as it was;
  gizmo-msp430's apply_config misses 18 of its 93 instructions instead of 37. Checked by hand:
  xEventGroupSync's answer gives uxTaskResetEventItemValue an item value without bit 25 (the task timed
  out) and the timeout path runs; xTaskGenericNotify's sets the TCB's event list item's container to 1 and
  its configASSERT runs; xQueuePeek's tells it the queue is not empty after the timeout check (data came
  meanwhile); xTaskGetHandle's has prvSearchForNameWithinSingleList miss at its twelfth call and the next
  list is searched. Not done (what is left behind the answers): a loop that needs more rounds than a loop
  case runs (the largest share of what functions that never return miss), a callee that hands its argument
  back untouched (dread.regflow: the branch's source is the argument, not the stub's answer), a real
  callee's store into a local, a call through a register (gizmo-msp430's apply_config), a switch's index,
  a value two conditions decide together (xAreTimerDemoTasksStillRunning: a counter the function resets
  unless a global equals its argument), two reads of one field compared (clang -O2's stream-buffer
  functions read xTail again to see whether an interrupt moved it: a case's `later`, memory that changes
  before the n-th read, could answer that; done 2026-10-26, below), system registers (done 2026-10-26:
  a case's sysregs, below), a run that faults in a call through a pointer a stub's fill gave
  (prvTimerTask's pending function calls).
  On the private real-life test (2026-10-23; no whole-image run there): more functions accepted,
  fewer dropped for unrun instructions, and no more disagreements than before.

- [x] **The code behind a stop** (2026-10-26; docs/decisions.md "The code behind a stop"). A callee in
  which runs hang (configASSERT's handler waits for a debugger) ends the cases at its call; the code behind the
  call ran only where the answers' retry, the callee stubbed at every call, got through, and a run that
  went on with what the assertion refused (a null queue) faulted. On FreeRTOS's clang -O2 build sixteen
  functions kept one to four instructions behind a configASSERT call unrun (clang copies the next check
  behind the call), and 51 functions outside the lifted table held 340 of the build's 384 raw pointer
  accesses in readable C: functions the lift check drops are never typed or checked. Now (dread.autocase
  behind, ANSWERS 2) a case whose run ended at such a call, and an answer that ends so, is retried with
  the callee answered at every call (as before); where that fails, with the call answered and the
  callee's next call ending the run (a case's `stop_from`: the calls before that number are as without
  the stop); where that fails too, ending at the first call the run makes after the answered one.
  dread uncovered says when cases end at a later call. Main -> this on the eight builds (main's full
  lift check, then this on a copy of its result): gizmo unchanged (36, 36, 44, 32); FreeRTOS GCC -Os 341
  -> 343 (vTaskStartScheduler, xStreamBufferGenericCreate), clang -O2 300 -> 311 (xQueueGenericSend,
  xQueueReceive, xQueueGenericSendFromISR, xQueueReceiveFromISR, xQueuePeekFromISR, xEventGroupSetBits,
  xEventGroupClearBits, xTaskDelayUntil, xTaskCheckForTimeOut, pcTimerGetName,
  prvCheckTasksWaitingTermination), RISC-V GCC -Os 272 -> 275 (prvSwitchTimerLists,
  vTimerPeriodicISRTests, a timer callback), GCC -O0 382 -> 386 (printi, vTimerPeriodicISRTests,
  xStreamBufferGenericCreate, prvSingleTaskTests); none lost; the readable gate accepts every one of
  them. (With the new tries alone, without the every-call stub first, GCC -O0 lost
  prvBlockOnNotificationsComingFromInterrupts, whose return only a run past all eighteen checks
  reaches.) Tests: tests/test_autocase.py test_a_stop_from_a_later_call,
  test_answers_run_the_code_behind_a_stop.
- [x] **A value read twice that changes between the reads** (found 2026-10-26 behind "The code behind a
  stop", done the same day; docs/decisions.md "A word read twice that changes in between"). FreeRTOS's
  stream buffers read xTail twice and loop while the two reads differ (an interrupt may move it in
  between); clang -O2 unrolls that loop four times, and the unrolled copies never ran: in the emulator
  nothing writes memory between two reads, and an answer that set the word set both
  (xStreamBufferSpacesAvailable, xStreamBufferIsFull and xStreamBufferSendFromISR kept 18 instructions
  each unrun on the clang -O2 build). The frontier answers have a source more for every load that
  decides a branch (reread, ANSWERS 3): where the run read that word before, it becomes the value just
  before this read (a case's `later`, dread sysrecord's field), so the two reads differ; each unrolled
  copy is a branch of its own and gets its answer in turn (xStreamBufferSpacesAvailable: the tail
  changed before its 3rd, 6th and 9th read). On the way: a branch was answered from the first two cases
  that got there only, and both of xStreamBufferSpacesAvailable's read the buffer through a pointer
  into the image (a small number on a map whose flash starts at 0), where no answer sets memory; now
  the first two that give something to answer, among the first eight (ANSWER_SCAN). Behind-stops ->
  this on the eight builds (copies of that branch's lift results, only the answers made anew): gizmo
  unchanged; FreeRTOS GCC -Os 343 -> 345 (_free_r, xPortStartScheduler), clang -O2 311 -> 317
  (xQueuePeek, xQueueSemaphoreTake, xStreamBufferIsFull, xStreamBufferSend,
  xStreamBufferSpacesAvailable, xPortStartScheduler), RISC-V GCC -Os 275 -> 277 (xEventGroupSync,
  vPrimaryBlockTimeTestTask), GCC -O0 386 -> 393 (xEventGroupSync, xPortStartScheduler,
  vPrimaryBlockTimeTestTask, prvSemaphoreTest and three timer test helpers); none lost, the readable
  gate accepts every one; 39 of the new functions' 42 answer cases use later. xPortStartScheduler
  writes a priority register and reads it back (the bits it holds), a device's answer later gives.
  A case with later also tells readable C that merges two reads of one word into one: readable C keeps
  memory accesses volatile now; the Memory item (plain accesses for RAM) will meet it. Test:
  tests/test_autocase.py test_answers_change_a_word_between_two_reads (a retry behind a second read of
  a buffer's tail, with a call so that GCC branches instead of an IT block).
- [x] **Behind a call whose run ended deeper in** (2026-10-26, behind "A value read twice"). The code
  behind a stop looked at cases whose stop was the call's own callee. FreeRTOS's RISC-V demo prints in
  its assertion handler before it waits, and the emulated UART reports itself busy, so its output
  routine (vOutNS16550, `j .` while busy) spins: the cases end there, two calls below the
  configASSERT call whose code behind stays unrun (RISC-V GCC -O2: 52 functions so after behind-stops
  and reread). Now (ANSWERS 4) the call in the function is the one answered, whatever stop below it
  ended the run: a base is any case with stops (or a hang case) whose run called it, the call's number
  counted in a run where the callee has no stub (its earlier calls keep running, the stub answers that
  call alone), and an answer that ends at a stop is retried behind the call the run last left the
  function by (left_through). Reread -> this (copies of that branch's lift results; RISC-V GCC -O2 from
  main's lift check of the bench build, 203 there, 214 with reread): RISC-V GCC -O2 214 -> 226
  (vTaskPlaceOnEventList, xTaskIncrementTick, vTaskStartScheduler, xTimerCreate, xTimerCreateTimerTask,
  xTaskAbortDelay, xTaskGenericNotifyFromISR, xEventGroupSetBits, vEventGroupDelete,
  prvCheckTasksWaitingTermination, vQueueOverwritePeriodicISRDemo, a test helper), RISC-V GCC -Os 277 ->
  278 (xTimerCreateTimerTask), GCC -O0 393 -> 394 (prvPerformTaskSyncTests), GCC -Os and clang -O2
  unchanged, none lost; the readable gate accepts every new one (RISC-V GCC -O2 224 of 226: the two
  that stayed lifted before still do).
- [x] **A case that ends at a fault** (found 2026-10-26 behind the item above, done the same day;
  docs/decisions.md "A case that ends at a fault"). On RISC-V GCC -O2, 40 functions kept the instruction
  behind a configASSERT call unrun (GCC puts the call out of line and jumps back): with the null queue
  the assertion refused, the run faults at once (0x38 or 0x40 on the virt board, where nothing is
  mapped below RAM), before any call that could end it. A case may say now that its run ends at a fault
  (`fault: true`, dread.emu Machine.may_fault): a load or store to unmapped memory ends the run as its
  result, logged ("fault", kind, address) after what it logged before and compared like the rest; the
  instructions behind the faulting one in its block do not count as run (covered_insns: short, never
  long); a fetch fault stays an error. The frontier answers' code behind a stop makes such a case where
  the answered run faults before any call (behind's last try, ANSWERS 5). Deep-stops -> this (copies of
  that branch's results): RISC-V GCC -O2 226 -> 244 (xQueueGenericSendFromISR, xQueueGiveFromISR,
  xQueueReceiveFromISR, xQueuePeekFromISR, xQueueGiveMutexRecursive, xQueueTakeMutexRecursive,
  xStreamBufferSend, xStreamBufferReceive and their FromISR forms, xStreamBufferNextMessageLengthBytes,
  xTaskGenericNotify, xTaskGenericNotifyWait, xTaskRemoveFromEventList, vTaskPlaceOnEventListRestricted,
  vTaskPlaceOnUnorderedEventList, prvNotifyQueueSetContainer, vEventGroupClearBitsCallback), RISC-V GCC
  -Os 278 -> 279 (vTaskPlaceOnEventListRestricted), GCC -Os 345, clang -O2 317, GCC -O0 394 and gizmo
  a32, m3, m4, rv32 unchanged, none lost;
  the readable gate accepts every new one (RISC-V GCC -O2 242 of 244, the two of before). Tests:
  tests/test_autocase.py test_a_case_that_ends_at_a_fault, test_answers_end_the_code_behind_a_stop_at_a_fault.
- [x] **A system register a case sets** (2026-10-26; docs/decisions.md "A system register a case
  sets"). FreeRTOS's vPortValidateInterruptPriority reads the exception number (mrs r0, ipsr) and checks
  the interrupt's priority only inside a handler (16 and up); every case ran in thread mode, and the
  function was dropped on every Cortex-M build of the bench (27 at measure-40). A case's `sysregs` sets
  system registers before the call, after the core's status is reset (dread.emu; the layer's sysreg_id
  says which: IPSR on the M profile), and the frontier answers give one where an mrs decides a branch
  (dread.uncovered's "system" source, ANSWERS 6). On the nine builds (copies of main's lift results)
  vPortValidateInterruptPriority is accepted on FreeRTOS GCC -Os (345 -> 346), clang -O2 (317 -> 318)
  and GCC -O0 (394 -> 395), and readable; RISC-V and gizmo unchanged (a RISC-V CSR is not settable
  yet; RISC-V GCC -O2's dropped functions have no system register among their causes). Test: tests/test_autocase.py
  test_answers_give_a_system_register_a_value.
- [x] **A page a case maps for its run** (2026-10-26; docs/decisions.md "A page a case maps for its
  run"). On a target that maps nothing at address 0 (QEMU's RISC-V virt board) the code behind a null
  pointer's load ran in no case: GCC -O2 gives configASSERT's path its own copy of the function's end,
  and the answers' fault case ended the run at the load. FreeRTOS's RISC-V GCC -O2 build dropped
  seventeen queue, stream-buffer and timer accessors for it (dread uncovered's "straight": every run that
  reached the load ended there). A case's `map` maps pages for its run alone (dread.emu Machine.map_page,
  Layout.free_page), and the answers' code behind a stop, after the fault case, tries the run with the
  faulting page mapped and MAPPED_WORD at the address (ANSWERS 7). On copies of measure-41's builds:
  RISC-V GCC -O2 244 -> 262 (the same code without it: 244, measure-41's verdicts) (pcTimerGetName, pvTimerGetTimerID,
  uxQueueMessagesWaiting, uxQueueSpacesAvailable, uxTimerGetReloadMode, vQueueDelete,
  vStreamBufferDelete, vTaskRemoveFromUnorderedEventList, vTaskSetTimeOutState, vTimerSetReloadMode,
  vTimerSetTimerID, xQueueGetMutexHolder, xQueueGetMutexHolderFromISR, xStreamBufferBytesAvailable,
  xStreamBufferIsEmpty, xStreamBufferIsFull, xStreamBufferSpacesAvailable, xTimerIsTimerActive); RISC-V
  GCC -Os 279 and clang -Os 274 as at measure-41; ARM GCC -O2 and clang -O2 317 -> 318, both
  vPortValidateInterruptPriority, which the system registers' answers took (their flash lies at 0: no
  page to map); none lost. Left: vTaskGenericNotifyGiveFromISR there (two instructions behind a priority comparison), and
  the -Os builds' "faults" (a run sent to a branch's other side faults on the way: vTaskDelete,
  tiny_print), which no answer behind a stop reaches. Tests: tests/test_autocase.py
  test_a_case_maps_a_page_of_its_own, test_answers_map_the_page_a_run_faulted_at.
- [x] **A computation the system run takes for a wait, and the culprits the whole-image check drops**
  (found 2026-10-23 with the frontier answers, FreeRTOS RISC-V GCC -Os). With xTaskResumeAll accepted
  (its answers covered it) on top of what was accepted before, the whole image differs: the stream-buffer
  test task runs 8,124 blocks of C in a row (memcpy, memcmp, xStreamBufferSend, xStreamBufferSpacesAvailable,
  xTaskResumeAll ...), no shared code and no device access between, and at a block its registers repeat
  while its progress lies in memory, so System._loops takes the computation for a wait and moves the clock
  to the timer: the rebuilt image takes the timer interrupt in front of a yield's ecall that the original
  makes first (device log entry 507). sysrun's docstring names such a computation as possible but not
  seen. xTaskResumeAll's C alone, and with the other new kernel functions, agrees in the whole image;
  everything accepted except xTaskResumeAll agrees too (268 functions). The lift check's culprit search
  (lift.culprits) looks for a smallest set that differs on its own, and for an effect of the combination
  that set was 42 functions, all dropped: 227 accepted instead of 268. On GCC -O0 a first version of the
  answers accepted a slightly different set, and the same rule ended the rebuilt run before the scheduler
  starts: the demo creates its timers (pvPortMalloc, strcmp, memcpy, xQueueGenericSend, xTaskResumeAll
  ..., 2,156 blocks, all C) and the run took that for a wait with nothing scheduled ("waits", after two
  device log entries); the original's code for the same work takes other block counts and the rule did
  not fire. Reproduced with the 381 functions another version accepted (its image agreed) plus
  prvHighPriorityTimeout: the rule fires with that function's C in the image and not without, though the
  function runs only in a task;
  its C moves other functions' C (xTaskResumeAll into its old place, xQueueGenericSend behind the image),
  and with that the registers or block counts at the anchor. The final version's -O0 image agrees. The
  culprit search on -O0 runs the whole 30-million-unit scenario on the original for each probe and was
  still going after half an hour. Two things to look at: the loop test (a memory digest at the anchor? a
  second round?), and leaving out one culprit at a time where that makes the rest agree.
  Done 2026-10-23 (docs/decisions.md). A loop comes round now when a block is entered with the
  anchor's registers and memory as at the anchor (System._loops, _came_round; in C sys_at_anchor): the
  whole state repeats, so the code repeats for ever unless an interrupt comes, in either image's code,
  and a computation is never taken for a wait. Memory through the stores: from the anchor on, a write
  hook gives each 4-byte word stored to its value at the anchor and now and counts those whose two
  differ (dread_syswrite; System._on_write without the C hook), in only while a stretch is tracked (a
  hook on every store that did nothing made the run 42 % slower: Unicorn syncs pc before every hooked
  store). On the 269-function image the old rule had taken 73 of the original's 511 waits and 95 of
  the rebuilt image's 514 with memory not come round: computations (after 2,142 to 12,363 blocks) and
  the coherence tester's busy loop, whose counter lies in memory. Such a busy task now runs LOOP_MAX
  blocks before it counts as a wait, as one counting in a register did. The memory test alone left
  that image differing at entry 1195 (next item), and the lift check accepted 255; the culprit search
  is still needed, and of a smallest set of three or more it now drops only the smallest function
  whose leaving out alone makes the whole image agree (lift.enough; dread readable --check the same).
  The lift check on copies of the six builds with the frontier answers (their cases cached): RISC-V
  GCC -Os 268 accepted instead of 227, the whole image agreeing once vTaskSuspendAll alone was dropped
  (of the coherence actor's 14), in 367 s instead of 709; GCC -Os 340, clang -O2 294, the M4F's GCC
  -Os 349, GCC 4 -Os 323 and GCC -O0 381 as before, their tables the same. The whole images of all 37
  benchmark builds with scenarios (each build's lifted table) end as with main, log for log; virt-gcc10
  -Os differs at entry 32 with both (a table from before the culprit search). Gizmo: every scenario's
  outcome the same (original, rebuilt, and with nearly every function counted as rebuilt), the lift
  checks the same. The C hook and the Python hooks agree (the tests; RISC-V GCC -Os with 227
  functions rebuilt, 1.6 s a run against 16 s). The cost is in the RISC-V runs, whose waits between
  ticks are busy loops that count in memory (72 to 219 LOOP_MAX waits a run where there were 0 to 20):
  a run of the whole image on an idle machine, main against now, 0.56 -> 1.50 s on clang 18 -Os, 0.95
  -> 5.71 s on GCC 12 -Os, 1.02 -> 1.89 s on GCC -Os; the Cortex-M runs as before (their waits come
  round with memory). A variant that tracks nothing (every wait then LOOP_MAX) puts three quarters of
  that on the busy loops' blocks and a quarter on the tracking, so tracking only from the first
  register match would gain little. Nor can LOOP_MAX come down much: the longest stretch that ended by
  itself in those runs took 59,280 blocks (Cortex-M GCC -Os), the next 26,487. A first version's write
  hooks did not clear Unicorn's stale IT state, as every memory hook must (dread.arch.arm.settle_it):
  Cortex-M GCC -Os ran off with a broken stack pointer once a tracked stretch stored at the end of an
  IT block; the test program's poll stores so now.

- [x] **Where an interrupt comes in a wait made only of C** (found 2026-10-23 behind the item above). In
  a wait outside shared code the run takes a due interrupt behind the first block in which the core
  takes it, which is at once where the loop search came round to its anchor (System._stretch): a place
  each image's code puts elsewhere. On FreeRTOS RISC-V GCC -Os with xTaskResumeAll accepted,
  prvSpaceAvailableCoherenceActor's loop (it sends to a stream buffer and receives back, its state
  repeating) is a true wait once it and its 13 callees are C; the original came round in
  prvWriteBytesToBuffer, where the tick switches tasks at once, the rebuilt image in
  xStreamBufferReceive with the scheduler suspended, where the tick only pends and xTaskResumeAll
  yields first (the whole image differs at entry 1195, a syscall against the interrupt). The lift
  check drops one of the 14 for it now (lift.enough: with any of them the original's code, the loop
  runs shared code and the tick comes behind a shared block). A place both images share: a function's
  entry (lifted C keeps the original's calls, so a round enters the same functions in the same order
  in both images), chosen from the round's sequence of entries whatever block the search came round
  at (the sequence's least rotation, say, with the entries an old entry's branch repeats collapsed),
  the interrupt taken there or behind the first unmask after it. Open: a round whose entries repeat
  inside it (two halves alike, a toggle in memory between them) picks a half by phase again; a wait
  found by LOOP_MAX has no round; one whose round enters no function (a poll inside one function)
  would keep today's rule.
  Done 2026-10-23 (docs/decisions.md; with "An interrupt held off by a mask ..." under Compiler
  diversity): not by a place chosen in the round but by a clock for such a loop. A function entered
  outside shared code moves the clock by ENTRY units, so a round that enters functions is no wait,
  and the tick comes due at an entry, the same in both images
  (test_a_wait_made_only_of_c_takes_its_interrupt_where_both_images_enter_a_function, and the RISC-V
  program of test_riscv_interrupts_come_where_both_images_share_the_place: the original's round seven
  blocks, the C's five; both failed before). The lift check of RISC-V GCC -Os with the frontier
  answers keeps vTaskSuspendAll: 269 accepted, the whole image agreeing with nothing dropped for it,
  in 22 s instead of 367 (the culprit search no longer runs). The coherence testers' busy loops call
  functions too and are no LOOP_MAX waits any more: RISC-V whole images run in 0.5 to 1.2 s instead of
  up to 7. Left: a loop inside one rebuilt function (no entry in its round) keeps the old rule, its
  interrupt where the search came round or behind the unmask (not established that this never shows;
  only what the handler reads that such a loop writes can tell the places apart); one that counts in
  memory there waits LOOP_MAX blocks (next item).

- [ ] **LOOP_MAX can come down** (measured 2026-10-23 behind the item above). With function entries
  moving the clock, the longest stretch outside shared code that ended by itself, over the whole
  images of all 38 benchmark builds with scenarios, is 8,202 blocks (59,280 before, when LOOP_MAX =
  200,000 was set with a margin of 3.4). On Cortex-M the register-test tasks now get the CPU between
  ticks as in the original alone, and their busy loops count in memory inside one function: 91 to
  180 LOOP_MAX waits a run, 18 of the 21.5 million blocks of clang -Os's runs, with the write hook in
  for the loop search (the rebuilt image's lifted C stores its frame on every round: 54 million
  stores tracked against the original's 3.9 million), so the rebuilt image's runs take twice the
  original's. A LOOP_MAX of 30,000 keeps a margin of 3.7. Measure the waits' places and the whole
  images' outcomes and times before and after.

- [ ] **prvQueueSetSendingTask on Cortex-M GCC -O0** (found 2026-10-23 behind "Where an interrupt
  comes in a wait made only of C"). Its lifted C alone makes the whole image differ at log entry
  16,085 (an ICSR read three times against seven: one image enters more critical sections), with
  main's system run as with the one that times function entries; the lift check drops it by its
  system check (entry 18,201), but the benchmark's table of measure-38 still lists it, and that
  table's whole image differs since the runs go that far. Not traced to its instruction.

- [x] **Retries beyond eight passes** (2026-10-24, found on a private firmware: a dropped function
  agreed in a trace against the final list; the bottom-up retries had stopped at their eight passes
  before its turn, silently). Each function is tried again once and the passes end by themselves, a
  bound of 256 for safety, what it leaves printed as "untried" (dread.lift Retries; docs/decisions.md,
  "The lift check's retries"). The public builds are unchanged: none had needed more than two passes.
- [ ] **The culprit search's empty set** (2026-10-23). lift.check and dread readable --check take the
  image without lifted C for one that agrees (differs([]) is None, never run). Since function entries
  move the system run's clock, hand-written C that calls otherwise than the original (inlining one
  rewritten function into another, a helper the layer does not call a runtime helper) makes that
  image differ on its own, and the search would then drop correct lifted functions one by one. Run
  the image with the hand-written C alone once where the whole image differs, and report "not
  decisive" when it differs too, as readable does for lifted C alone.

- [x] **Code only a dropped case reached** (found 2026-10-22 on a private firmware, DREAD's real-life
  test: its uncovered functions took longer to generate than those accepted, and met callees stubbed for
  hanging). A case that should end at a call (stops, a loop case)
  and ends otherwise (spinning on a register, at the limit) counts while the search runs, and must:
  it moves the compare-guided stage's frontier (gizmo-rv32cpp's main: where a run waits on memory,
  that stage finds the callee effect that ends the wait; not counting such runs lost four of its
  instructions). But drop_bad_ends drops it from the result, and what only it had reached was left
  uncovered: on the private firmware, a call whose one kept run was meant to end at a call deeper in
  and spun on a device register before it. Generator 34: when the surviving
  cases cover less than the search counted, the compare-guided stage runs once more from what they
  cover, every case a source, counting only runs whose case survives, and a run there that comes
  back cut or spinning in a callee is tried again ending at that call too (repair_lost). On a sample of
  the private firmware's uncovered functions some were accepted and some miss fewer instructions, none
  more; the gizmo builds lose nothing (msp430's main is accepted by loop cases again instead of
  by the system run). The rest: other causes, not traced (a dead
  branch the compiler kept, such as a negative test of a value loaded unsigned; code behind an RTOS
  call that switches tasks and never comes back in the emulator).

- [x] **The memory map in the cases' cache key** (found 2026-10-21): the key holds the image, its
  startup copies and dread.regflow's results, not the target's [memory]: a target whose memory
  changes keeps the cases made under the old map (they still run alike; the generator would find
  others: the lab's page at address 0 let a probe's null path end, but only after its cached cases
  were removed). Add the regions to Generator.environment with the next generator VERSION bump,
  which renews every cache anyway. Done with generator 32 (2026-10-21).

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
- [x] **The C standard of generated C** (found 2026-10-22): DREAD compiled lifted and readable C with
  the toolchain's default standard, and GCC 15 and later default to C23 while CI's GCC 10 and 13 (and
  a user's older toolchain) default to C17: a second identical definition of a structure, which C23
  allows, passed here and failed in CI. Pinned to gnu11 ([compiler] generated_std, in front of
  lifted_cflags; docs/decisions.md): GCC 4.7 and clang 3.1 on know it, C17 adds nothing it needs.

- [x] **GitHub Actions** (the user agreed, 2026-10-18): the suite on every push, Python 3.11 and 3.13,
  the distribution's arm-none-eabi-gcc. A failure there that does not happen locally is looked at
  first (another compiler version decides placements and identical-code mutants).
  Red from 2026-10-19 02:22 to 2026-10-20, 74 pushes, and nobody looked (the user asked): the push
  script ran the fast tests here, on GCC 16, binutils 2.47 and clang 23; CI runs everything on
  Ubuntu 24.04's GCC 13, binutils 2.42 and clang 18. Five tests, three of them faults of DREAD's:
  - regflow followed a variadic save area through a copy of sp, not through a copy of that copy:
    clang 18 keeps a frame pointer (add r7, sp, #8; add.w r12, r7, #8; stm.w r12, {r1-r3}), and r1-r3
    counted as read (regflow 15: the layers' base_copy; the test runs with a frame pointer too);
  - the ARM listing stated the core but not the FPU: binutils before 2.47 take none from
    `.cpu cortex-m4` and rejected gizmo-m4's 30 float instructions (written as words; the rebuild
    stayed byte for byte). `.fpu` from [compiler] cflags' -mfpu now; checked with GCC 13.2's
    assembler on Compiler Explorer: none rejected;
  - the fake wslpath of a cclab test used echo, which dash (Ubuntu's /bin/sh) stops at the \c of
    \mnt\c\w (printf now);
  - two tests asked for one compiler's code: the C++ probe's devirtualising guess (GCC 16 guesses,
    GCC 13 calls through the register; the test now ties the count of taken indirect calls to
    whether W::f ran) and a prologue's first instruction (GCC 13 moves the arguments before the
    push; the test looks at the first four).
  Since: ~/.config/dread/checked-push.sh refuses to push while CI's last finished run on main is red
  (`--ci-red-ok` for the push that fixes it).
- [x] **Red again, 2026-10-21, and what CI did not run**: the push of `cclab relink:` failed one
  test on all three jobs: the relink adapter links newlib (-lc), and Ubuntu's gcc-arm-none-eabi only
  recommends libnewlib-arm-none-eabi, which `--no-install-recommends` leaves out. Every machine here
  has newlib, so the suite passed here and in each toolchain image (`bin/with-toolchain ubuntu-24.04`
  ran the test green, which told the missing library apart from the compiler version). CI installs
  newlib now, and the test skips where arm-none-eabi-gcc finds no libc.a. The lesson: a test's
  marker names the compiler, but the library it links is a dependency too.
  The same look found that CI ran none of the nine tests that compile RV32 with GCC: they asked for
  `riscv64-elf-gcc` (Arch's name; Ubuntu's is riscv64-unknown-elf-gcc) and CI installed neither.
  conftest's `riscv_gcc` finds either and the -march spelling the version takes (GCC 10 knows no
  _zicsr), `riscv_toml` writes both into a test's target; CI installs Ubuntu's RISC-V GCC (13.2 on
  24.04, 10.2 on 22.04). Checked with the images: the nine pass with GCC 10.2, 12.2, 13.2 and the
  host's 16 (six failed with GCC 10 before, all on the -march), the compiler lab's RISC-V test with
  10.2 and 13.2. CI's first run with both (2026-10-21, run 39534455108): green on all three jobs,
  578 passed and 27 skipped each (35 skipped the day before), none of the skips a RISC-V or newlib
  test. The distributions CI does not cover, by bin/with-toolchain: Debian 12 (GCC 12.2, clang 16)
  596 passed; Debian 13 (GCC 14.2, clang 19) one test that assumed GCC stores sp itself at -O2
  (fixed the same night: its C cases skip with the reason, an asm case has the shape).

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

## The GUI for everything (the user, 2026-10-24)

The user, 2026-10-24: "Can the GUI be extended to support all of DREADs features and also provides an
easy way to set everything up and run and monitor the pilot?" Four steps: the first alone, then the
other three in parallel, each one or more pages of its own (a module of dread/guipages with its routes,
a script in dread/web registered with the page's router: dread/guipages/__init__.py says how), so that
they add pages without touching the same lines. As before, the tools compute and the page shows and
starts them: a page runs DREAD's commands as jobs (dread.guipages.run start_command), it does not
reimplement them.

- [x] 1. Jobs and every command: a job runner in dread gui and a Run page with a form for every
  command generated from its parser (2026-10-24; docs/decisions.md, "The GUI runs every command"):
  - Every command's parser, read without running it: dread.cli parser calls the command's main with
    argparse's parse methods stopped at their first call (this thread only) and takes the parser main
    built. dread.cliforms makes a form of it (positionals, options with their choices, defaults and
    help, flags as checkboxes, --x/--no-x pairs, repeated options, an option's optional value,
    subcommands as nested forms) and a command line of a form's values, and reads a command line back
    into the form through the parser; the options a help text ties to a choice of another field
    ("--task name: ...", bench's "toolchain: ...") are shown for that choice only (a switch shows them
    all). tests/test_cliforms.py: every command yields a parser in an empty directory without writing
    or printing anything; for every command and subcommand, generated values for every field (each
    choice, lists with spaces, values starting with -) make a command line its parser takes, the values
    read back make one it reads the same, a second round changes nothing; the README's command lines
    too.
  - dread.jobs runs them: `sys.executable -m dread.cli COMMAND ARGS...` in the target's directory with
    `--target .` (and --image for an image other than the first) where the form leaves them empty, a
    session (process group) of its own, polite as the tools are, at most `dread gui --jobs N` at a time
    (default 1) and the rest queued, stdout and stderr into BUILD/gui/jobs/N.log, a record N.json
    beside it (docs/formats.md: command line, directory, by, queued, started, ended, state, exit status;
    never the environment). A job may wait for another (`after`: a chain stops at the first job that
    fails, for step 2's first analyses). Stop: SIGTERM to the group, SIGKILL after 30 s (a job may ask
    for more); stopping the server stops its jobs; a server that ended without closing leaves records
    the next one marks lost, or follows again while their process lives (Linux: pid, start time, boot
    id). One server per target runs jobs (a lock), and only one that listens on this machine alone (the
    page carries its token: on another address anyone who reaches it could run commands); a second
    one, and `dread gui --read-only` (no change, no job), show them. The values of secret-looking
    variables (KEY, TOKEN, SECRET, PASSWORD) are replaced by their names in the logs served and in a
    record's command line.
  - The page: the Run page (#/run, key x, a button in the bar with the jobs running and waiting): the
    commands, a command's form, the command line the server makes of it (as you type, with the
    command's own refusal and the field it names), Copy, Run; the jobs (#/jobs) and a job (#/jobs/N):
    its command line, state, times, exit status, its log followed while it runs (polled from a byte
    offset), Stop, Run again, Change and run (its form filled from its command line). A command that
    needs what the server cannot give (dread gui itself) says so instead of running. The tables a job
    changes reload the other views as a run in a terminal does: after a lift check from the page the
    overview and the list show its verdicts (tests/test_gui_run.py).
  - The server: page modules (dread/guipages: discovered at start, routes by decorator, /api/gui),
    their share of /api/status, their scripts and stylesheets served from dread/web; app.js exports its
    helpers, imports the pages' scripts and routes to them (register: address, show, leave, status,
    keys and their help, a button in the bar). The pages of browsing stay where they were.
  - Checked: tests/test_cliforms.py, test_jobs.py, test_gui_run.py (also on Python 3.11 and 3.13 in
    containers), test_gui_browser.py (headless Chrome through Playwright, the `browser` extra: a job
    run from the form, a task's options, a subcommand, a refusal, a stop, a read-only page; skipped
    without Playwright, as on CI); by eye in headless Chrome, light and dark, 1360 and 390 px wide: no
    script error, no horizontal scroll.
  - Not established: a job that outlived a crashed server is recognised on Linux only (elsewhere it is
    marked lost without looking); politeness is applied right after the process starts, so its first
    milliseconds run at the server's priority; the options tied to a choice come from a convention of
    the help texts (an option whose help does not follow it is always shown); the page keeps a log's
    last 20,000 lines (the file keeps all), tried with short logs only; not tried on macOS; a long form
    (dread pilot's three dozen options) is checked by its round trips, not yet for how well it reads.
- [x] 2. Setup: a new target from an ELF (dread import-elf) or a raw image (architecture, load address,
  entry); the prerequisites checked (Python packages, cross compilers, podman, Ghidra optional) with
  what to install; the first analyses as one chain of jobs (functions, regflow, the lift check, the
  readable check) with their progress; the language-model providers as a form (llm.toml: models,
  prices, cache_min, batch size; keys from a key file per provider, written with mode 600 and never
  shown back). Done 2026-10-24 (docs/decisions.md, "Setting up"); every action a command first:
  - `dread doctor` (dread/doctor.py): Python, unicorn 2.1.4, capstone 5 and a target's compiler (which must
    take its cflags) are the essentials and decide the exit status; z3, pyelftools, Playwright and a
    browser, GCC for ARM, RISC-V (with the -march it takes: _zicsr or not) and the MSP430, clang with
    ld.lld and its targets, podman, git, Ghidra with a java, each provider of llm.toml (its key there or
    not, never shown; a local server asked for /v1/models within 2 s; a command on PATH) are optional and
    say what needs them. For what is missing: the command for this system's package manager (apt, pacman,
    dnf, brew from os-release; all four with --all) or pip, printed, never run; --json.
  - `dread new DIR IMAGE` (dread/newtarget.py): the directory, dread.toml and original/; from an ELF the
    architecture (build attributes), the image (sections near the entry; farther ones a loader's), the
    entry, the startup copies, a memory map (the RAM up to the stack's top, the architecture's peripheral
    ranges where it fixes them, scratch and sentinel at unused addresses, as much room again as the image
    behind it), the compiler of the image's own C (the producer of the compile unit holding main) with its
    flags, and the tables through dread import-elf; from a raw image with --arch and --base, an M-profile
    vector table read for the entry and the RAM. Each guess has a comment in dread.toml. On all eight
    gizmo builds it gives gizmo's own architecture, image, entry, startup copies, functions and stack top
    (tests/test_newtarget.py); on the FreeRTOS builds, the bench's compilers and flags. It prints the first
    analyses to run (newtarget.first_analyses: the functions by dread ghidra where an image has none).
  - `dread llm set` and `dread llm key` (dread/llmconfig.py): a provider added or changed in llm.toml line
    by line (comments and unknown fields kept; inline tables, dotted keys and new command providers by
    hand); a key from standard input into a key file (keys/NAME beside llm.toml, mode 600, never in a git
    work tree or a directory others may write to). dread.llm reads key_file when a request goes out
    (env first where key_env is set), refuses a file others may read, and hides a key in HTTP errors;
    dread gui hides the key files' keys in its jobs' logs. `dread llm providers` says whether each key is
    there.
  - The Setup page (dread/guipages/setup.py, dread/web/setup.js and setup.css; #/setup, key u): the doctor's
    findings (the problems, the rest on request) with each command to copy; the new-target form (a path, or
    a file uploaded into DIR/original/, at most --upload-limit MiB) with the command line as typed, run as
    a job, the target opened when it is made; the first analyses as chained jobs with each step's state,
    time and its log's last line; the providers with a form (a password field for the key, shown back as
    set or not set) and a Test button (dread llm ask as a job). dread gui started where there is no target
    comes up with this page alone; the server switches to the target made or opened (Server.open_target),
    refused while a job of it runs, on a read-only server and on one listening beyond this machine.
  - Checked: tests/test_doctor.py (a fake PATH of stand-in compilers, providers of every kind),
    test_newtarget.py (every gizmo build against its own target, raw images, refusals, taking back, a lift
    check on a target it made), test_llmconfig.py, test_gui_setup.py, test_gui_setup_browser.py; tried in
    headless Chrome, light and dark, 1360 and 390 px wide (no script error, no horizontal scroll): targets
    made from gizmo-m3's ELF by path (first analyses 40-85 s: 49 of 50 functions lifted, all readable),
    from its raw image uploaded from the browser (dread ghidra first: 56 s, 30 of Ghidra's 32 functions
    lifted, all readable) and from a copy of FreeRTOS's mps2-gcc-Os ELF (19 min on a machine busy with
    other runs: 298 of 366 functions lifted, all 298 readable; the bench's own target of that build lifts
    329, and its 55 files of extra cases, recorded before, cover 29 of the 64 dropped here; the rest of the
    difference is not traced), a provider added with a key and tried against a stand-in server.
  - Found on the way: dread import-elf --write-startup walked a new target's calls with the description
    read before it wrote the startup list (gizmo-a32 failed: "0x40000000 is outside image main"); fixed.
  - Not established: a guessed memory map is the device's (devices without a fixed peripheral map fault
    until [memory] io names them; the room behind the image is a guess); the producer of a clang-built
    image's own code is told from a C library's only with pyelftools; the page's upload is tried with
    files of a few dozen KiB only; Fedora's bare-metal RISC-V GCC and the MSP430 GCC are not known in the
    distributions (said so, not guessed); not tried on macOS. Left: gizmo-mips's dread.toml keeps a free
    range an older import wrote (import-elf does not take out a `free` line when no gap is left: below).
- [ ] dread import-elf --write-startup leaves a `free = [...]` line in place when the image has no free
  range any more: gizmo-mips's names [0xbfc014f0, 0xbfc01500], of which .ramfunc's last 8 bytes now lie
  in the first half (the overlay could put C over them). Take the line out when the list is empty, and
  regenerate gizmo-mips (found 2026-10-24 by dread new, which writes the target from scratch). Tried
  2026-10-26 and dropped: taking the line out where no gap is found removed ranges written by hand
  (tests/test_lift.py's narrow-branch tests and test_lift_results.py write room inside .text, padding
  the ELF's gaps do not show; four tests failed). A rule must tell a stale range the tool wrote from one
  written by hand: its bytes erased in the image, or a mark on the tool's own line. MIPS is paused.
- [x] 3. The pilot: start a run (task, provider, budget, batches), follow it live (stage, questions asked
  and answered, batches waiting and their age, spend so far against the budget, the names as they
  come), stop it (its batches cancelled: a job's stop is SIGTERM to its process group, which makes the
  pilot cancel its batches; start_command's grace gives it longer than 30 s where that takes longer),
  its history and the cost per month (the usage log), two runs compared (--score, the judge). The
  pilot's side (structured progress events, the budget) is done: 3a; the page reads dread.pilotlog.
  Done 2026-10-24 (dread/guipages/pilot.py, dread/web/pilot.js and pilot.css; docs/decisions.md, "The
  pilot page"):
  - The pilot page (#/pilot, key `l`, a dial in the bar with the runs going on): a short form of the
    pilot's own options (its parser through dread.cliforms: the task, the provider of llm.toml with its
    prices, Message Batches and prompt cache and whether its key is there, never the key; the budget in
    dollars; all functions, the first N or some; Message Batches on or off with their round size; the
    two to four options each task needs), the command line (the Run page's route), Copy, Start (a job of
    `dread pilot`, grace 60 s), and "Every option": the Run page's form of dread pilot filled in with
    these (run.js now takes `#/run/NAME@["--x", ...]`). Providers and keys are set up on the Setup page
    (a link; each provider's key said there or not, and from where: dread.llm key_status). A server
    without a target shows the cost per month here and starts nothing (the routes about runs: 409).
  - The estimate: the dry run's count (dread.pilot planned, from which `dread pilot --dry-run` now prints
    too; for --task readable those without an entry in readable_names, a guess the readable C confirms
    as the run begins) times the tokens a function of the newest earlier run of the task on this target
    at the chosen provider's prices, its share of Message Batches at the batch rate where this run may
    batch, a run with the same settings preferred and differing ones named (dread.pilotlog estimate);
    the dry run prints the same line. None without an earlier run, and the page says why.
  - A run (#/pilot/ID; #/pilot/job/N until the job's run begins): the stage and the stages with their
    questions, the questions answered of those planned (a bar), the answers and those from Message
    Batches, the spend against the budget (a bar that turns amber at 75 % and red at 90 %), the rate and
    the time left, the batches out with their age and answers, the budget reached, the end with its
    totals, the warnings and errors; the names as they come, newest first (each function settled with
    its name and vote or its variables' names and sentence, each refusal with its reason, the stages and
    batches; every answer too where a function is asked once), each with a link to its function's page.
    The server folds each followed run from the offset it read (Follow) and the page polls from its own:
    a poll reads what was written since. Stop is the run's job's stop (the job whose process is the
    run's), "stopping" until the end event "stopped" comes; a run started elsewhere is followed, not
    stopped; a run gone without an end offers to cancel its batches (`dread llm batches --cancel`, a
    job).
  - The runs before (task, image, provider, how it ended, answers, cost of the budget, functions named,
    duration, its job), from pilotlog.runs and the end's new counts (`settled`, `settled_other`:
    docs/formats.md; an older run's events counted once); the cost per month from the usage ledger
    (usage_summary per month: per provider its answers, share from Message Batches, input tokens and the
    shares read from and written to the prompt cache, output tokens, cost, and what batches and cache
    saved against the standard prices; every month with its total).
  - Comparisons as the pilot makes them, started as jobs and shown by what the pilot prints: with the
    true names (--score TRUTH, the target's ELF files offered; free without a judge), with another
    copy's names.tsv (--against with --grounded or --judge, the copies beside the target offered), two
    settings over replicate copies (--against ... --vs ... --grounded); the judge defaults to the run's
    provider, else the cheapest. Two runs picked in the history are explained: other tasks' names have
    no comparison in the pilot, and two naming runs of one target are one set of names (one names.tsv).
  - Found on the way: a function page's readable C did not show names a run wrote to readable_names
    while the page was open (the reading was kept by the load's generation only): now kept by the
    names' too (app.js), so the function pages follow a readable run as the list and overview follow a
    naming run.
  - Checked: tests/test_gui_pilot.py with a fake provider (Anthropic's Messages API and Message Batches
    played on 127.0.0.1, its key made up; the jobs reach it through an llm.toml of the test's own and
    write a usage ledger of their own): a run started from the page, its run found by its job's process,
    followed from offsets while its batch is out (the stage, the batch and its age), stopped (the batch
    cancelled at the provider, the end "stopped", the job "stopped"); a run to its end with its names on
    the list and in the function pages, the history, the month's costs with the batches' share, the
    estimate from it (and its settings differing), a --score comparison as a job; two runs explained; no
    answer of the page and no job record or log holding the key; a read-only server that starts and
    stops nothing. tests/test_pilotlog.py: the end's counts, an older run's counted from its events,
    estimate() with and without batches and settings, the dry run's plan and estimate line.
    tests/test_gui_pilot_browser.py: the same page in headless Chrome (skipped without Playwright).
  - The trial, on a stripped copy of FreeRTOS GCC -Os (mps2) with Haiku 5.5, followed in headless Chrome,
    light and dark, 1360 and 390 px wide (no horizontal scroll, no script error; docs/decisions.md has the
    figures): a readable run of 80 functions to its end ($0.0203, two Message Batches); its estimate for
    the next 12 ($0.0053 one at a time, $0.0028 with batches); those 12 stopped by a budget of $0.003 (7
    named, $0.0032), a function page open meanwhile showing the names as they were written; a naming run
    stopped from the page while its batch was out (cancelled, none of it billed), run again ($0.0009),
    and compared with the true names and with an older copy's names ($0.0013); after the rebase onto
    step 2, a naming run of three functions ($0.0005) with the history and the costs. Spent: $0.0263.
  - Not established: a run's job is found by its pid on this host; the estimate is rough (pilotlog
    estimate: the shared stages are spread over the earlier run's functions, and a readable run's count
    of functions named already is a guess before their C is made); not tried on macOS or in Firefox; most
    of the trial ran before step 2 came onto main (after the rebase only the short naming run above).
- [x] 3a. The pilot's side of 3: a run followed live and kept within a budget. Done when every run
  writes structured events as they happen, `dread pilot --budget DOLLARS` stops a run where its
  estimates say the budget ends with the tables as for a normal end, `dread pilot --status [--follow]`
  and `--list` show a run from its events, a stopped run says so, each measured on FreeRTOS GCC -Os.
  Done 2026-10-24 (dread/pilotlog.py, dread.llm Meter; docs/formats.md, three entries of
  docs/decisions.md). Every run that may ask a provider has an id and BUILD/pilot/runs/ID/events.jsonl
  (start, stages with the questions planned, rounds, Message Batches submitted, looked at and ended,
  each answer with its tokens, cost and the spend so far, what the pilot made of it, each function
  settled, the budget reached, warnings, the end: done, stopped, failed or budget reached), and two
  lines in BUILD/pilot/runs/index.jsonl; the GUI reads them through pilotlog runs, state and events (by
  byte offset). Every question goes through the run's meter (Provider.ask, ask_batch): estimated from
  its text and its kind's answers (the larger of their mean and their tokens out a token in times its
  input; else earlier runs'; else 4,000 tokens out), admitted only while it fits; the pilot weighs a
  function's or group's samples before it begins them, a batch's rest is weighed again after its first
  part. A vote cut short keeps the answers it got. SIGTERM cancels the batches and the questions not
  begun and waits 20 s at most for those being asked (llm.thread_pool: dread gui kills a job 30 s after
  its SIGTERM), so that the end event "stopped" is written. Measured (Haiku 5.5): events 230-275 bytes
  each (a readable run 294 KB, a 40-function name run 181 KB), no measurable cost (a played run of 1,830
  answers, five pairs, within the machine's spread); `--task readable` $0.106 whole, with `--budget
  0.05` $0.0498 (104 of 317 functions; a first version passed it, $0.0577, the estimate's mean taken
  from the smallest functions' answers); a 40-function `--task name` $0.0585 whole, with `--budget
  0.029` $0.0264 (14 of 29 groups), $0.0277 with an earlier run's estimates (20 groups); a run stopped
  by SIGTERM while its batch was out: the batch cancelled (none of its 145 questions billed), the end
  event "stopped". Spent: $0.35 of Haiku 5.5. Open: `--budget` with `--task review` (refused:
  dread.namereview keeps a challenge whose questions were held back as one without a candidate); the
  estimate on other models and tasks; a first run's first batch of a kind (no answers yet) is weighed at
  4,000 tokens out, so a small budget takes several rounds.
- [x] 4a. The views a reader of one function needs, on its page (2026-10-24; docs/decisions.md, "A
  function's views"): under the function's header a strip with what each view knows, and the one that is
  open beside the listing and the C (above them below 1600 px), its instructions marked in the listing
  and its lines in the C, each read from its tool's --json record in BUILD/gui/views and run as a job
  from the page where the record is missing or stale (a newer file the tool reads, by the files' times):
  - Trace: the failing cases of dread test's tests.tsv (a new column, `failing`: the cases' labels), of
    the lift check's and the readable check's verdicts (with the last round file's labels), each traced
    in its mode (dread trace, --lifted, --readable) with `dread trace --json` (new: the report as data,
    the printed report made from it): where the runs part, both images' instructions with the C line or
    the statement, where a register was set last, the call stacks, the frame as both images have it,
    the last shared events, the stores that differ, the first difference dread test compares where it
    is another; a case chosen on purpose traced at once; any case by its label in either mode; a failing
    function of the overview opens here.
  - Uncovered: dread uncovered NAME --json, its regions marked in the listing (the region, its way in,
    what decides it), each with its way in, the sources, the values its runs saw, how a run sent the
    missed way ended and what that means in the tool's words; the search's notes.
  - Mutation: `dread mutate --json` (new), for C of src, with or without --kill: each survivor as its line
    before and after, marked in the C, what the search found; argued ones with their argument; counts.
  - Registers: dread.regflow's facts (RegFlow.facts, also `dread regflow --json`, new) with the bits a
    read uses where fewer than all count (regflow 27 keeps them), per call site of its callers and of its
    own calls, leftover and computed; dread callconv NAME --json's measurement against them.
  - Checked: tests/test_gui_views.py (the API on a copy of gizmo with parse_uint written wrong: the
    failing cases of both records, traces run as jobs in both modes and read back, a record made stale,
    the mutation check's refusal, failure and argued survivors, uncovered, regflow and callconv, the
    token, a read-only server, wrong requests), test_gui_browser.py (the views in headless Chrome: a trace
    at once, another by its chip, the listing marked, the registers and mutation views, phone width), the
    tools' JSON in test_trace.py, test_mutate.py, test_regflow_bits.py, test_runner.py; by eye in headless
    Chrome on that copy and on a copy of FreeRTOS's GCC -Os build (uncovered regions with forced runs,
    a register read 16 bits wide), light and dark, 1360, 1920 and 390 px wide.
  - Not established: staleness by the files' times misses a change of DREAD's own code; the bits per
    call site are not kept (sites are part of the cases' key); which case comes closest to noticing a
    survivor is not known to dread mutate and not shown; a trace's C line needs the twin build to be byte
    for byte the traced image (a rebuilt image older than the tables has none: the view shows the note);
    tried with a few hundred functions, not a large target's thousands of failing cases (the view lists
    eight before "all").
- [x] 4. Result views for the tools the GUI does not show yet: bench results, cclab profiles, leakcheck's
  hits. Done 2026-10-24 (docs/decisions.md, "Pages of the tools' results"): three pages, each reading its
  tool's own `--json` record and running the tool as a job (the report by itself where its record is
  missing or older than what it reads and no job of the tool runs on it; anything longer on a button):
  - The tools' JSON (docs/formats.md): `dread bench report --json FILE` (also measure and all), dread.bench
    report_data: each build's figures as the report shows them (FIGURES, one number each: figures()), what
    stops the rest, its compiler (kind, release, the flags' optimisation and cpu), whether the recipe changed
    since it was built, its metrics without the lists of every function's name, and the same figures of
    every earlier measurement (BUILD/history) for a trend; report.md byte for byte as before on the 39
    FreeRTOS builds. `dread cclab report --json FILE`: the report is data first now (report.data,
    report.markdown), with the probes whose measurement failed apart and the profile; report.md and
    profile.toml byte for byte as before. `dread leakcheck --json FILE`: each hit with its file, its lines
    and whether it is allowed, the history's with their commits, the authors, what was looked for; of the
    private files only their paths and the strings that matched; mode 600; the printed lines unchanged.
  - The bench (#/bench, key m; dread/guipages/bench.py, bench.js, bench.css): a recipe's builds as a table
    that sorts by any column and filters by compiler, release, -O, core, project and text, each row with a
    small line of the chosen figure over the build's measurements; a build's page (#/bench/RECIPE/BUILD) with
    tiles, the trend as a chart (crosshair and readout) and as a table, every figure against the measurement
    before, what stops the rest, why the rest is not lifted, what it gained and lost, registers against
    regflow, the readable C's figures; "Measure again" (the steps chosen, by default those it had; the
    command line and an estimate from the build's last metrics.json; at least one build), "Every option"
    (the Run page's form of dread bench); "Open as the target" (Server.open_target by the build's name under
    BENCH, as the Setup page opens a target).
  - The compiler lab (#/cclab, key i; cclab.py, cclab.js, cclab.css): a work directory typed as a path, the
    compilers measured there; a compiler's profile (#/cclab/ADAPTER): what failed first (builds, probes with
    each error and its builds), then where arguments arrive and results come back, the types, what each
    operation calls or does inline, what a call keeps with the stack alignment, where the functions lie, the
    runtime helpers with the registers they change and those the compiler's code reads after them (the
    profile), what DREAD lifts; rows whose builds differ marked, a filter and "only where the builds differ".
    A run from a form (an adapter: built-in, ce:ID, relink:DIR or a file; its matrix as a grid; one's own
    probes; --lift, --guard), "Check the adapter" (dread cclab doctor), the report.
  - The leak check (#/leakcheck, key w; leakcheck.py, leakcheck.js, leakcheck.css): the form (a tree,
    default the DREAD the server runs; tables, words, allow list by their paths; --history; --min), the
    command line, the check as a job of the page's own (Server.jobs_of_page: its records and logs in
    $XDG_STATE_HOME/dread/gui/leakcheck, never in the target's directory), its log while it runs; the hits
    with file, lines and string, allowed ones on request, the history's with their commits, the authors; the
    checks made, the form starting from the newest. The server never opens the private files.
  - The bar at a phone's width (app.css): the brand's mark alone, the search at least 96 px, the pages'
    buttons scrolling sideways beside it (three more of them had left the search a few pixels wide).
  - Checked: tests/test_gui_bench.py, test_gui_cclab.py, test_gui_leakcheck.py (the APIs: jobs started and
    read back, records made stale, refusals, the token, read-only servers; with GCC a build measured from the
    page and opened as the target, a compiler's probes run with a probe of one's own that never returns; the
    leak check's canaries, strings of the private files in no tree file, in no answer, page or log, and its
    jobs nowhere in the target's directory), test_gui_results_browser.py (the three pages in headless Chrome:
    sorting, filters, the trend's chart and readout, the profile's filters, a check from the form; no script
    error, no sideways scroll at 390 px), the tools' JSON in test_bench.py, test_cclab.py, test_leakcheck.py.
    Tried in headless Chrome, light and dark, 1360 and 390 px wide: a copy of the 39 FreeRTOS builds' results
    (metrics, history, build.json; times kept) and of mps2-gcc-Os whole, which was measured from its page
    (asm, readable, noreturn, callconv: 463 s on a busy machine, the readable step 458 s; 317 of its 329
    lifted functions readable, 24 gotos, statements against the source's median 1.10; callconv measured 275
    functions where the measurement of 2026-10-21 measured 283, not traced) and opened as the target; GCC
    16's 16 builds run from the compiler lab page with --lift and a probe of one's own that never returns
    (497 s on a busy machine, the lift checks 30-50 s a build, the report started by the page after it; the
    probe's error in every build, each with its address; the lift check of cortex-m0-O2 and -O3 gave no
    verdicts: below); a leak check of a made-up project with a history.
  - Not established: staleness by the files' times misses a change of DREAD's own code; a measurement's
    estimate is its last one's seconds (the machine's load then); a recipe outside DREAD's bench/ has its
    results shown and no job (the Run page's form takes a recipe's path); a build directory of BENCH whose
    report names a build the recipe no longer has is not listed; `dread cclab compare` (two compilers side by
    side) has no JSON and no view yet; the pages tried in Chrome only, not on macOS.
  Left of "The GUI for everything": adding a hit to the allow list from the leak check page (a change of the
  user's private file: asked, not assumed).
  Done 2026-10-25: two compilers side by side (#/cclab/compare/ORIGINAL/REBUILD, from the compiler lab's
  "Compare two compilers"; dread.guipages.cclab compare_view, compare_run): dread cclab compare --json
  (dread.cclab.compare data, markdown: the Markdown as before, byte for byte) made as a job where its record is
  missing or older than a result of either compiler, as the page makes the report; the differences in what
  linking depends on, the options that close them, where the code differs, what DREAD lifts of each; the
  work page lists the comparisons made. tests/test_gui_cclab.py (GCC with int-sized enums and signed chars
  against GCC as it comes: the job, the record, the list, staleness, names that are no compiler refused);
  looked at in Chrome, light, dark and at a phone's width.
  Done 2026-10-25: names from elsewhere (#/names, key e; dread/guipages/names.py, dread/web/names.js): dread
  whatcc, samecode and callpair run as jobs from the page (samecode and callpair with `--json` since then),
  their proposals listed (only those that give a function without a name one), the chosen ones applied by
  dread rename as a job; tests/test_gui_names.py (on gizmo-m3 stripped of its names: samecode against the
  named build proposes 46, three applied; callpair from the ARM-state build with one seed; whatcc's table;
  the token, a read-only server, words that are no names; the page in Chrome).
- [x] dread cclab run --lift with gcc-arm (GCC 16.2): the lift check of cortex-m0-O2 and cortex-m0-O3 failed as
  a whole ("the build failed: the link failed": the lifted C of op_switch_jump, built for the Cortex-M0, calls
  `__gnu_thumb1_case_uqi`, which the link does not have), so those builds got no verdicts at all; at -Os the
  same function was dropped alone ("its C calls __gnu_thumb1_case_uqi, which the original does not call").
  A link error of one function's C should drop that function, not the check (found 2026-10-24 by the
  compiler lab page's trial). Done 2026-10-25 (docs/decisions.md):
  - The cause was the order, not another section, relocation or symbol form: the -O2 object holds the same
    R_ARM_THM_CALL against the same undefined symbol in .text.op_switch_jump, and foreign_calls finds it
    there; it was only asked after a link that went through. Lifted C is compiled at -Os at every level, and
    GCC makes a Thumb-1 switch a call of `__gnu_thumb1_case_*` only when it optimises for size, so only the
    -Os image has the helper (libgcc's, for the original's own switches): there the link went through and the
    check after it dropped the function; at -O2 and -O3 the link failed first.
  - The check reads the compiled C before every link now (dread.build build's vet: foreign_calls), and a
    link the linker refuses drops the functions whose objects or sections its errors name (dread.overlay
    blame, GNU ld's and lld's forms; the verdict "the link of its C failed (...)" with the linker's words)
    and links again; an error that names no function the check may drop (a failed assertion of the linker
    script, a missing library, an object written by hand) still fails it with the linker's message. The
    linker runs with LC_ALL=C.
  - Lifted C for Thumb code of a core without Thumb-2 is compiled by GCC with -fno-jump-tables (the ARM
    layer's lifted_cflags, after dread.target configured the layer from cflags): its switches compare
    instead of calling the helper, whether the image has one or not.
  - Measured (main against this, on copies with the same cached cases): the compiler lab's gcc-arm matrix with
    --lift, cortex-m0-O2 and -O3 89 of 94 probe functions each (no verdicts before), op_switch_jump accepted
    there; cortex-m0-Os 89 as before (op_switch_jump dropped as before, now because no case runs its dispatch:
    the open item on the switch helper below); the other thirteen builds the same verdicts with the same
    reasons. gizmo built for the Cortex-M0 in a scratch directory: 49 either way. The eight builds accept the
    same functions, tables and verdicts with reasons byte for byte the same: gizmo a32 36, m3 36, m4 44, rv32
    32; FreeRTOS mps2-gcc-Os 341, mps2-clang-O2 300, the RISC-V GCC -Os build 272, mps2-gcc-O0 382.
  - Checked: tests/test_lift_link.py (a Thumb-1 program of our own whose switch GCC compiles into a call of
    the helper, which its image lacks: with lifted_cflags = ["-Os"] the check drops that function before the
    link and gives the other three their verdicts, and with the check before the link told nothing, the
    linker's error drops it with its words; a function written by hand that calls what nobody defines fails
    the check with the linker's message and no table; by default all four are accepted and the switch's C
    calls no helper; the link with GNU ld, with debug information too, and with ld.lld names the one function
    whose C it refuses; where the flag applies; blame on GNU ld's and lld's messages) and
    tests/test_cclab.py (run --lift on the Cortex-M0 build at -O2 of the probes' ops file: verdicts, the
    switch probe accepted; main gives "the lift check gave no verdicts"), on the host's GCC 16 and on CI's
    GCC 13 and 10.
  - Not established: other linkers' message forms (they fail the check as before); readable C on Thumb-1
    gets the flag too, and the readable gate was not run on a Thumb-1 build; an image whose [arch] differs
    from the target's gets the target's flags (docs/decisions.md lists the rest).
- [x] dread readable --check takes a link that its readable C makes the linker refuse for the end of the gate
  (dread.build BuildFailed), as the lift check did until 2026-10-25: readable C that calls what the original
  does not (a loop GCC turns into a memcpy, a helper) is dropped only where the image has the callee, and
  checked only after the link. The lift check's way fits it: foreign_calls as build's vet before each link,
  the functions OverlayError.blamed names dropped (dread.lift check). Left with the lift check's item, which
  kept out of dread.readable.
  Done 2026-10-25 (dread.readable check: foreign as build's vet, OverlayError.blamed's readable functions
  dropped with the linker's words, the after-link check gone): a function whose readable C calls what
  nothing defines is kept from the link, or dropped through the linker's message where the vet misses, and
  the others are checked as before (tests/test_readable_link.py: both fail on the code before, with the
  whole check ended by "the link failed").
- [x] A function whose original dispatches through GCC's Thumb-1 switch helper (bl __gnu_thumb1_case_uqi, the
  table behind the call) gets no case that runs the dispatch: the helper, another function, loads a byte of
  the caller's extent, which dread.emu trace_reads takes for code loaded as data (the extent owners' rule), so
  every input that reaches the call is refused, the generator's and written ones alike ("the input loads
  instructions as data or stores into the image"). Seen on GCC 16's Cortex-M0 -Os build of the compiler lab
  (op_switch_jump: 37 of its 44 instructions never ran, ten written cases with every index refused) and on
  gizmo built for the Cortex-M0 in a scratch directory (handle_key, script_run): since their C compiles
  without the helper (2026-10-25), this is what drops them. The helper's loads of the table behind a call the
  layer marks as a dispatch (insn.dispatch_call) are the caller's constants.
  Done 2026-10-25 (dread.program extent_owners): the table behind a call the layer marks as a dispatch, from
  behind the call to the function's next instruction (the first case: the call does not fall through), is
  nobody's, like a pool another function loads from, so the helper's loads of it are the caller's constants.
  tests/test_thumb1_dispatch.py: a switch of our own in C, GCC for the Cortex-M0 at -Os with libgcc (the image
  has __gnu_thumb1_case_uqi): on the code before, the lift check dropped it with 27 of its 34 instructions
  never run; now it is accepted. Not measured again: the compiler lab's Cortex-M0 -Os op_switch_jump and
  gizmo built for the Cortex-M0.

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
  Measured on a private image: a function's page comes up at once (it did not before the GUI reused one
  lifter fed with the never-returning functions it already has).
- [ ] (Later: after MSP430) v2: a failing case's differences (the test run keeps them per function),
  pilot review (conversations, argued survivors and name proposals, accepted or rejected from the
  page through `dread rename` and argued.tsv).
- [x] v3, first part: the relations around a function as a graph (asked for by the user on
  2026-10-22: reverse engineering is a puzzle; show how the pieces join). A tool computes it, `dread
  relations` (text or `--json`, docs/formats.md), and the page draws its record. Here is what it holds:
  - its callers up and callees down to a depth of 1 to 3 (nearest first, at most 150 functions and
    400 nodes, what is left out counted);
  - the memory each function's readable C reads and writes (`g_`/`io_`/`rom_` as readable C names it;
    an indexed access at the sum of its constants, which finds GCC's section anchors: gizmo's dispatch
    reads `commands`, not mode_name's table at the anchor);
  - the texts, functions and tables of pointers its constants point to (dread.constants pointed, now
    structured; pointed_note writes it);
  - the readable_types plan's structures;
  - the name status of each piece (named, proposed by the pilot: names.tsv or readable_names; none yet).

  A constant counts as an address only where the machine code makes it as compilers make addresses
  (literal pools, pc-relative, a RAM address shortly after one). Without that, an image at 0 took
  delays for addresses: vTaskDelay "referred" to 89 texts, mostly tails of strings, 23 with it. A
  refused function is read from its listing ("uses" only).

  The page: a Relations tab (`#/r/NAME`, key `r`) with a force layout in plain JS and SVG. Callers are
  held above their line and callees below theirs; the function is fixed in the middle; each layout is
  seeded from the last, so a click that walks to another function keeps the picture. Shapes by kind,
  colours by name status, filters per kind, depth 1-3, hover lighting a piece's neighbours, drag, zoom.
  The functions come at once; a worker reads each function's C in the background, nearest first, and
  the page polls. The facts are kept per function in BUILD/relations under a content key: making
  readable C costs 0.1-0.5 s a function (tidy's condition simplification is 78 % of it on the slowest
  FreeRTOS functions), a kept one a lift.

  Measured:
  - gizmo: neighbourhoods of 8-40 nodes, 3 s cold, 0.5 s kept;
  - FreeRTOS GCC -Os: xQueueGenericSend has 153 nodes at depth 1 (25 s cold) and 255 at depth 2;
    vTaskDelay at depth 2 takes 18 s cold and 3 s kept;
  - layout and drawing: 15-19 ms in Chrome at 150-260 nodes.

  Every label shows up to 60 nodes; beyond that, the 28 best-joined near pieces and the rest on hover
  or zoom. A hub with 40 callers gives an overview where the clusters show, not the names.
- [x] v3, second part (asked for by the user on 2026-10-22: "make the nodes of the graph position
  themselves automatically in a nice wobbly way? Also is there a way to show the graph of the whole
  application?"):
  - Both graphs lay themselves out in front of the reader. dread/web/sim.js, a force simulation without
    the DOM: velocities that friction damps (a node overshoots a little and settles), an energy that
    decays to rest, every pair's repulsion up to 600 nodes and Barnes-Hut's quadtree beyond. It starts
    from the last positions and velocities when the graph changes (depth, filters, the C read meanwhile,
    a walk), so the picture morphs. A dragged node keeps the simulation running and its neighbours
    follow; released, it settles again (not pinned: the picture stays the forces' balance;
    docs/decisions.md). The function in the middle glides back to it. The camera follows the motion
    until the reader zooms or pans. At rest no frame is asked for. With prefers-reduced-motion the layout
    goes to its rest at once and a dragged node stays where it is dropped.
  - The whole program (`#/p`, key `p`, the overview's Whole program, a function's Program button, which
    lights it in its group). Each group of `dread groups` is a node, sized by its functions, its ring the
    share named and proposed. It is labelled by the pilot's subsystem name where subsystems.tsv names
    exactly its functions, else by the name prefix a third of its functions share (unless another group's
    is the same: ten FreeRTOS demo groups all said `prv`), the name of its only function, or `group N`.
    Groups are joined by the calls (arrows at the callee's end), taken addresses and shared memory and
    texts between their functions. A click opens a group into its functions in place; its label closes
    it. A program of at most 500 functions opens with all of them; a larger one with its groups. Drawn on
    a canvas: the groups are kept apart as discs, an open group's functions pulled together, calls across
    groups pulling 6 % as much as calls inside one. Beyond the 400 heaviest, a group's links are
    hairlines. Labels are placed largest first, none over another.
  - The tool computes, the page draws: `dread groups` keeps its groups and their evidence in
    BUILD/groups/IMAGE.json under a key over DREAD's code and the image's tables (a second look 0.09 s
    instead of 2.8 s on a FreeRTOS build; since the evidence comes from the listing, found anew in
    0.3-0.5 s, without waiting for the register analysis), and `--json` prints the whole-program graph
    (docs/formats.md).
    The GUI's `/api/program` runs `dread groups` on the first request in a process of its own, because
    its pool forks and the server has threads, and reads the kept record after that.
  - Measured in headless Chrome, which here has no GPU and rasterizes in software (a lower bound):
    - relations: gizmo's print_status (24 nodes) moves for 3.6 s, 8 nodes overshoot by up to 38 px, 60 fps
      at 0.3 ms of JS a frame; FreeRTOS's xQueueGenericSend at 153, 255 and 261 nodes 60 fps, 1.8-3.2 ms;
    - the whole program of FreeRTOS GCC -Os (366 functions in 27 groups): all functions 60 fps at 0.8 ms,
      at rest after 4 s; the groups found cold through the GUI in 22 s (clang -O2, 343 functions);
    - a synthetic program of 5,000 functions in 221 groups with 14,027 calls: its groups 56 fps at 1.8 ms;
      all 5,000 functions 21-23 fps at 14 ms of JS a frame, 9 of them the simulation, the rest of a frame
      the software raster. sim.js alone at 5,000 nodes: Barnes-Hut 6.2 ms a tick against 64 ms for every
      pair, 0.55 % from the exact forces; at 10,000 nodes 16 ms.
    Not measured: a browser with its GPU, and an image of thousands of real functions; the layout is
    the same however long the frames take, but how it reads at that size is not established.
- [x] v3, next, first part (2026-10-23): how sure each name is and the name review's suspects, wherever a
  function shows; in the whole program, a search that lights functions and the groups that hold them,
  and the shared memory and texts down to an open group's functions.
  - The tools compute, the page draws. dread.relations certainty (CERTAINTY: none, unvoted, unsure,
    likely, sure, named): a proposal's from names.tsv's confidence, sure where every answer agreed,
    unsure at or below the agreement whose vote alone makes the review challenge it (0.5 by its
    weights). dread.namereview standing: the mark of the last review.tsv row about the name a function
    or group goes by now (flagged; suspect after kept or no candidate; changed; confirmed), rows about
    an older name stale. Both reach `dread relations --json`, `dread groups --json` and the GUI through
    relations.Names; the whole-program record gains each group's certainties and marks, its own name's
    certainty and review, and `shared` (each thing functions of two groups touch, with them). Kept until
    a names table changes; /api/status counts those changes and the page reloads its list without
    loading the image again (docs/formats.md, dread/gui.py; docs/decisions.md, 2026-10-23).
  - The page: one hue from unsure to sure (an ordinal ramp checked in both themes: monotone lightness,
    its light end 2.05:1 on white and 2.66:1 on the dark panel), a neutral grey for no vote, an empty
    disc for none; each function's disc filled as far as its vote agreed (whole for a name in the
    tables), dashed without a vote, so the scale reads without the colour. Marks: `?` suspect, `!`
    flagged, a turn for changed (a quiet check for confirmed, in the list and the pages only). In the
    list (chips filter by level and mark), the overview (a bar of the levels, the map by names, a review
    card), the function's page (its proposal, vote and every challenge with its reasons), the relations
    and the whole program (a group's ring by level, the suspects a closed group holds counted).
    Functions without a name in the tables go by their proposal, in italics; the search finds proposals.
  - Whole program: a Light box; /api/program-search finds by name, proposed name, address or text, each
    function with its group, and the groups that hold them; the finds are ringed and lit, a closed group
    counts its finds, Enter opens those groups. An open group's functions are joined to the memory and
    texts they share with another group (its node, or its functions where it is open too): at most 4,000
    such pairs, things fewer functions touch first, what is left out counted in the note.
  - Checked in headless Chrome on gizmo (names and a review planted) and on a FreeRTOS GCC -Os copy after
    a naming run and a review (366 functions: 285 sure, 77 likely, 4 unsure; 17 changed, 10 suspects):
    light and dark, 400 px wide, no script errors, no horizontal scroll; at rest the relations of
    queue_peek (57 nodes) after 3.6 s (11.7 s the first time, while the worker read their C) and the
    whole program after 3-4 s, no frames after; "queue" lights
    61 functions in 13 groups, Enter opens them (166 functions) and they settle in 2.7 s; reduced motion
    draws no frames. The synthetic 5,000 functions (software raster): groups 57 fps at 2.0 ms of JS a
    frame (1.7 before), all functions 18 fps at 14.8 ms (20 fps at 14.2 before; drawing a disc filled
    whole as one path took it from 16.5).
  - Not established: whether the levels fit other naming runs (one FreeRTOS run's votes: 285 at 1.0, 43
    at 0.8, 34 at 0.6, 4 at 0.4); memory and structures have no vote, so their names are never more than
    unvoted; the shared things between groups are few on FreeRTOS (two), most sharing stays inside a
    group by construction.
- [x] v3, next, second part (2026-10-23): calls through pointers from the system run's record (who called
  whom), so that a task's or a callback's caller shows; the system run's timeline (device logs of both
  images side by side).
  - What kept calls: nothing. dread sysrun kept the functions entered (counts, the order of first
    entries), the lift check's whole image only whether the logs agree, dread.sysrecord the calls of
    chosen functions with their inputs, not their callers. Now the system run keeps who entered whom,
    in its C block hook (sys_ran) as in Python (System._ran): every block that runs ends where the next
    transfer into a function comes from, and at an entry the pair (where the block before ended, the
    entry) is counted; the reset, an exception the run takes and a system call leave a mark instead,
    which a vector that only branches on (the A profile's, RISC-V's vectored mode) hands on. An
    exception taken in front of an entry keeps what came before it, by the entry and its stack pointer,
    until that entry runs with that stack pointer again (the exception returned, or the task it
    interrupted runs again): the transfer then counts from the call, not from the exception's return (a
    test program shows it; without the rule the call was a return from PendSV). dread.sysrun transfers
    names each pair by the instruction in front (the layer's flow): call, jump, pointer, pointer-jump,
    return (a task's first run), falls, exception, syscall, reset; one no instruction explains is
    counted apart (none on gizmo's builds and two FreeRTOS builds). Each log entry keeps the pc of its
    access too (Outcome.where; the C hook's entries and every access it hands over carry it). Neither
    is compared.
  - The record (docs/formats.md, BUILD/sysrun/IMAGE/NAME.json): dread sysrun writes one a scenario it
    runs, dread lift --check one a scenario of its last whole image (SCENARIO.lift-check, beside it):
    each image's ending, clock and reports, both device logs (100,000 entries at most: from the start,
    or around the first difference) with each entry's pc and function (the rebuilt image's C by its
    placement), the first difference with compare's lines, and the original's transfers. Readers use
    it only for the image's bytes now, and name by address (renames keep the rows); dread relations
    and dread groups --json read it.
  - The graphs: what no call shows (a call or jump through a pointer, a return that went to an entry,
    code running into it) joins a function's relations as callers and callees, as edges of their own
    (calls-pointer dotted, enters dash-dot, with how, how often, where and the records), and the whole
    program as `ran` (dashed); how the core entered a function (exception N, the reset) is on its node.
    The function's page says who called it through a pointer, who started it, which exception entered
    it.
  - The timeline (`#/s`, key `s`; the overview's System run): a record at a time (a choice where there
    are several, the one that differs first), how each image's run ended, the serial port's text (once
    where both are the same), the first difference with compare's lines; the two logs side by side in a
    virtual list, each entry with the function whose instruction made it (a link to it there); the
    first difference marked and the rows after it that differ tinted, `d` goes there. At phone width
    each row holds both images' entries one under the other.
  - Measured: FreeRTOS GCC -Os, its original's run to its limit (90,114 log entries): 505,631 transfers
    in 1,932 rows, none unexplained; 68 tasks started by a return (67 from xPortPendSVHandler, the first
    from vPortSVCHandler), the timers' callbacks through pointers from prvProcessExpiredTimer (six
    callbacks, 94 calls) and the pended functions from prvTimerTask (seven), PendSV, SysTick, SVCall and
    both timers' handlers by their exceptions; on clang -O2, which inlines prvProcessExpiredTimer, the
    callbacks are plain calls through a pointer from prvTimerTask (99), the same 68 tasks, none
    unexplained. The GCC -Os run took about 5 % longer than without keeping them (3.99 s against
    3.78 s, the best of six alternating runs on the shared machine), FreeRTOS's MSP430 build 7 % on
    DREAD's engine, which does every block in Python; the record 4.1 MB, 0.2 s to read, 28 MB in the
    server. Its whole image (329 functions from lifted C):
    6.3 s for the build and both runs, as dread lift --check runs it, the records kept in 0.02 s (249
    KB, 2,466 entries each). FreeRTOS GCC -Os on RISC-V: the same callbacks, 63 tasks started by mret
    from the port's restore (its trap handler is no function of the tables, so its exceptions name no
    handler), none unexplained. gizmo: dispatch's jump through its table of commands on a32, m3 and m4
    (pointer-jump) and msp430 (a call through a register, as MSP430 GCC makes most of its calls: they
    show as calls through pointers); clang folds the table into a switch on rv32. The page: a window of
    400 entries in 1 ms (30-57 KB); headless Chrome on the FreeRTOS copy and gizmo (one with a planted
    bug, so that the logs differ) light and dark, 400 px wide, no script errors, no horizontal scroll.
  - Not established: the rebuilt image's transfers are kept but not named (its C is not the listing's);
    a call through a stub that only branches on (ARMv4T's call_via) counts from the stub; an exception
    whose handler is no function's entry of the tables names none; the KEEP window shows at most
    100,000 entries of a longer run.
  - CI's Ubuntu 22.04 job (RISC-V GCC 10.2, binutils 2.35, which has no RISC-V mapping symbols) made
    the vector table's label a function of the tables, and the system call that entered its first
    slot counted as entering the table. The mark now goes on through a slot of a vector made of
    instructions (the layer's enters_code_vector: the A profile's, RISC-V's vectored mode) whose
    instruction only branches, whatever the tables call it; a handler a table of addresses names (the
    M profile's) keeps it, also where it is only a branch. A test with the label typed as a function
    shows it on every toolchain.

## A realistic benchmark: FreeRTOS (asked for by the user, 2026-10-19; before the rest of MSP430)

Decompile a real project built by ourselves in many ways, with the ELF as ground truth. The
repository holds the recipe; the sources, builds and results live in `~/dread-bench`.

- [x] **Recipe and harness**: `dread bench` and `bench/freertos.toml`: fetch, build with our flags,
  import (ground truth), measure (listing, Ghidra against the ELF, the lifter's acceptance, recorded
  inputs, noreturn, seconds), report. Faults it found in DREAD: a listing file that began with the
  last one's tail, an extent over a free range, a crash after a failed generation, the RISC-V
  listing and link with GNU binutils, gp read as an argument, stubs logging dead registers, a hook
  that never returns stubbed, hang marks inherited by mutants, clang's variadic spill taken for
  reads, ecall taken for a system call that reads a0-a7, every -O0 function reading nothing (the
  spill rule), saves around a call of an unset register taken for reads, a hang deep in a callee
  stubbing nothing, five builds measured as they were made from an older recipe (without the
  device models it had gained: measure builds again what the recipe changed since). It brought the "load" startup kind (QEMU's ELF loader filling RAM), fixed
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
- [x] **What the lifter leaves** (closed 2026-10-20, round two below). All builds rebuilt from the recipe and measured on 2026-10-19
  (afternoon; the scenario 900 ticks): Cortex-M3 GCC -Os 289 of 366, -O0 314 of 437, -O2 227 of 349,
  clang -Os 266 of 356, -O2 240 of 343; RISC-V GCC -Os 214 of 312, -O2 166 of 296, clang -Os 208 of 304:
  1,924 of 2,763 functions (70 %); recorded inputs add 7 to 33 a build; the nine builds took 1 h 43 min.
  Measured again in the evening (generator 20, regflow 6; measure-20): 1,926 of 2,763 on the eight
  ARM and RISC-V builds (+3 against measure-19: clang -Os, clang -O2 and RISC-V GCC -Os one each,
  from the round of small arguments), and the MSP430 build lifted for the first time: 74 of 250
  (30 %; Ghidra finds 43 of its functions, the ELF all). 57 min, beside other work and with the
  MSP430 lift new (627 s): not comparable with measure-19's 47.
  After the near-miss repair (generator 22; measure-22, 2026-10-20): 1,930 of 2,763 on the eight ARM
  and RISC-V builds, +4 against measure-21 (GCC -Os +1, -O2 +2, clang -O2 +3, RISC-V GCC -O2 and clang
  -Os +1 each; clang -Os -3, RISC-V GCC -Os -1: which ones, no measurement kept; they name them since),
  MSP430 74 as before; 63 min beside the full suite and the MSP430 tests.
  Measure-23 (2026-10-20: the DSP and FPU instructions, vpush and libgcc's eight-byte push, a
  register a callee never writes kept across calls, branches decided never taken, stale-data cases
  refused, the IT state kept at interrupts; generator 23, regflow 11): 1,952 of 2,763 on the eight
  builds, +22 against measure-22 and none lower (GCC -Os +5, -O0 +3, -O2 +5, clang -Os +3, -O2 +3,
  RISC-V GCC -Os +2, -O2 +1, clang -Os 0); the Cortex-M4F builds 297 of 394 and 278 of 384 (+4 and
  +3 against their first measurement that day, by name: printi, prints, vRegTest1Implementation,
  xStreamBufferReceiveFromISR; TIMER0_Handler, vRegTest1Implementation, xSecondTimerHandler; none
  lost); MSP430 74. 73 min for eleven builds, every case generated anew (the cache's key changed).
  Measure-24 (2026-10-20: generator 24 with the pointer stub for wild callbacks, the register a
  caller keeps compiled call-saved, wild calls refused, the MSP430 stall rule, the system run's C
  hook, the sweep cap): 1,957 of 2,763 on the eight builds, +5 against measure-23 and none lower
  (GCC -Os 298, -O0 316, -O2 233, clang -Os 270, -O2 247, RISC-V GCC -Os 217, -O2 167, clang -Os 209);
  the Cortex-M4F builds 299 of 394 and 280 of 384 (+2 each); MSP430 143 of 250 (74 at measure-23).
  Gained by name: __sflush_r (GCC -Os, -O0, clang -Os), prvBytesInBuffer (GCC -Os, M4F GCC),
  __aeabi_dsub (both M4F), cleanup_stdio (M4F clang), printchar (RISC-V GCC -Os). 62 min for the
  eleven builds with every case generated anew (73 for measure-23).
  Disagreements traced that day and left: a queue whose generated item size is larger than the
  item makes a real callee copy the frame behind a local, laid out otherwise in the C
  (xFirstTimerHandler's saved r4 on GCC -Os, TIMER1_Handler on the M4F clang build, most likely
  xEventGroupClearBitsFromISR on RISC-V GCC -Os); telling them needs where a passed object ends.
  memset on GCC -O0 runs more than ten times the original's instructions in its C (compiled at -O0
  like the build). MSP430's three indirect jumps are libgcc's __muldisi2, __umuldisi2 and __muldi3:
  br X(pc) through words that hold 0x120a, no function (never called): rightly refused.
  Lifted C at -Os (2026-10-20; [compiler] lifted_cflags, default -Os, after the target's cflags):
  on fresh copies with the objects rebuilt (a first try kept most objects at the old level: the
  build compared only times, fixed), GCC -O0 316 to 322 (the three empty lock functions, memset,
  which ran more than ten times the original's instructions at -O0, prvQueueOverwriteTask and
  vRegTest1Implementation), GCC -O2 233 to 237 (the lock functions, prvRegTestTaskEntry2), clang
  -O2 and RISC-V GCC -O2 unchanged, none lost; the -Os builds compiled it at -Os already. Of the 17
  task loops of GCC -O0 whose system runs differed (their -O0 frames overflowed the task stacks:
  FreeRTOS's own check called vApplicationStackOverflowHook in prvBlockingTask's run), 2 are
  accepted, 11 agree and miss 2 to 121 instructions, 4 still differed. One of them, main_full, was
  the system run's own doing: its chunks ended by the engine's count of instructions, inside blocks,
  whose rest the next chunk counted again, at points the two images do not share (the rebuilt
  main_full ran 106 clock units behind at the limit: two device accesses more). Chunks end where a
  block starts since, before the block counts; main_full agrees and misses one instruction (the loop
  behind vTaskStartScheduler, which runs only if the scheduler cannot start). With both changes on
  fresh copies of all eleven builds: GCC -O0 322, -O2 237, RISC-V GCC -Os 218, -O2 168 and clang -Os
  210 (main on each RISC-V build, by the chunk fix), the rest as at measure-24, none lost: 1,970 of
  2,763 on the eight builds (+13). Still differing on GCC -O0: prvQueueSetSendingTask (also on GCC
  -Os), prvSemaphoreTest, vRegTest2Implementation. Clang -Os's xTaskGenericNotify and
  xTaskGenericNotifyFromISR are lifted since the switch resolver's filled registers (refused
  before) and UNCOVERED: the generator never set the switch index to 3 or 4 (tbb behind cmp #4), nor
  the task's notify state to "waiting".
  Generator 25 (2026-10-20): (a) memory cells do not overlap. A learned pointer is put one to three
  bytes off now and then, a run then read the same buffer at new addresses, and every input wrote
  those overlapping cells one after the other, over most of each other's bytes: half of a build's
  functions had such inputs, and vListInsert's item value read 0x08000000 whatever the compare-
  guided stage set. Measured from scratch on three builds: GCC -Os 298 to 297, clang -Os 270 to 274,
  RISC-V GCC -Os 218 to 220, the instructions the uncovered functions miss 447 to 313, 1,204 to
  1,180 and 557 to 551 (a few functions each way, the search's path). vListInsert still misses its
  one: behind the compare it loads a list pointer from a word no input sets, and the solver drops an
  attempt that reached new code and then touched the image (a pointer chain, which the pilot's Haiku
  wrote). (b) A run that runs into its limit on a branch to itself (for (;;);) has done all it does
  in both images: a case (emu.Machine.self_loop_at; a loop case, or a "hang" case of a function's own
  error path). GCC -Os 298 to 303 with the cases of the five affected functions made anew: FreeRTOS's
  malloc-failed and stack-overflow hooks, prvGetRegistersFromStack, Default_Handler and main_full
  (whose last instruction is the loop behind vTaskStartScheduler); gizmo's hang and Default_Handler.
  Measure-25 (2026-10-20: lifted C at -Os, chunks that end at block starts, generator 25, register
  calls in the call trees, stack switches stubbed, -fno-ssa-phiopt on the MSP430): the eight builds
  2,016 of 2,763 (+59: GCC -Os 303, -O0 328, -O2 243, clang -Os 279, -O2 253, RISC-V GCC -Os 227,
  -O2 168, clang -Os 215), the M4F builds 309 and 290 (+10 each), MSP430 150 (+7); every build gained,
  each lost two to seven (the search's path, regenerated). 63 minutes. Merged after it, measured
  next: regflow 13 and 14 (38 refusals fewer), generator 26 (the chase), stale-data cases compared.
  Measure-26 (regflow 13 and 14, generator 26, stale-data cases compared): the eight builds 2,065
  (+49: GCC -Os 307, -O0 332, -O2 250, clang -Os 289, -O2 264, RISC-V GCC -Os 234, -O2 174, clang -Os
  215), the M4F builds 314 and 305 (+5, +15), MSP430 150; one lost (clang -O2). 2,834 of 3,791 on the
  eleven builds against 2,679 at measure-24. Merged after it, measured next: generator 27 (loop
  frames, flag setters behind other instructions) and saved registers touched refused.
  Measure-27 (generator 27, saved registers touched refused): the eight builds 2,086 (+21: GCC -Os
  310, -O0 337, -O2 252, clang -Os 294, -O2 266, RISC-V GCC -Os 235, -O2 175, clang -Os 217), the M4F
  builds 318 and 309 (+4 each), MSP430 151; two lost (GCC -O0). 2,864 of 3,791 on the eleven builds.
  Merged after it: generator 28 (calls into the scratch wild, the chase off the stack-argument room).
  Measure-28 (generator 28): every build as in measure-27 (+0 -0 on all eleven), 54.5 minutes.
  Measure-29 (generators 29 and 30: code pointers learned in the first pass, aligned cells over
  misaligned ones): the eight builds 2,105 (+19: GCC -Os 311, -O0 339, -O2 256, clang -Os 296, -O2
  271, RISC-V GCC -Os 238, -O2 177, clang -Os 217), the M4F builds 322 and 310 (+5), MSP430 151:
  2,888 of 3,791 (+24), 56 minutes. 44 gained, most behind calls through pointers (_fwalk_sglue on
  every build, __sflush_r on five, timer callbacks); 20 lost, every one UNCOVERED by one to eleven
  instructions (the search's path moved: prvSanityCheckCreatedSemaphore on four builds misses the
  store behind a take that fails at one iteration), none FAIL.
  Left there, traced: clang -O2's __sflush_r, whose FILE callback a generated input points at a real
  function's entry that loads r8-r11 from generated memory (a longjmp-like target; no wild call,
  since it is an entry); clang -Os's prvSampleTimeNow, whose rebuilt run ends at 0x3c (not traced).
  From the measurement after loop cases: (1) coverage
  misses (about 60 per build) and failed generations (about 20): recorded inputs where a scenario
  reaches the code (the recorder follows calls across task switches and into handlers since, with
  what other tasks write meanwhile as later stages: on GCC -Os 254 to 265 accepted, the record step
  868 s to 25 s), longer or more varied scenarios for the rest; (2) functions that never return whose loop cases miss instructions
  (about 30): mostly a path that needs every check of the loop to pass in one case (a stubbed receive
  that must write exactly the value compared), beyond the compare-guided stage's budget on runs that
  long; the system check builds each one alone since (with every accepted function rebuilt the
  kernel took no clock time, the demo's tick-count test asserted, and the scenario ran a third of its
  code) and counts what its loop cases ran, and the scenario runs ten times as long (at 90 ticks the
  not-accepted functions ran 3,018 of 10,568 instructions, at 900 7,268); the next step would be loop
  cases recorded in the system run (a task's callees' answers and what they wrote, as stubs); (3) a few disagreements per build,
  each to be traced: a stack address taken for a value (a seed, a pointer an index pushed past a
  local) is refused since (the original's own outcome changes with its stack lower; dread
  integration does not check that yet), and so is a case whose differences are all stale stack
  data the original read (2026-10-20: FreeRTOS's timer commands copy an unset union member into the
  timer queue, which was most of the disagreements of the MPS2 builds: vStartTimerDemoTask,
  xTaskIncrementTick, the tick hooks; a question for the user whether to compare the rest);
  2026-10-20: five of the disagreements (GCC -Os, Cortex-M4F GCC -Os, RISC-V GCC -Os) were a scratch
  register the callers read after the call because the original never writes it (GCC's -fipa-ra
  compiled the callers knowing: prints keeps a value in r2 across printchar), which the C used;
  such a register is now compiled call-saved in the function's file (Lifter.call_saved, GCC's
  -fcall-saved-REG; not for clang): +1 on each of the three builds, none lost (printchar on ARM is
  UNCOVERED behind it: its store path needs a pointer to a writable pointer);
  also 2026-10-20: a generated code pointer that lands in the image but at no function's entry
  (a timer's callback 127, a Thumb pointer into __aeabi_dmul; a FILE's _write into prvCheckTask;
  0x20a, between two functions): the original ran that code with its registers and came back, the
  C called it with other values in the others (prvReloadTimer came to address 0 on both M4F builds,
  clang's __sflush_r and cleanup_stdio to an even address); such a case is refused now
  (runner.wild_call, Machine.watch_calls, which the pilot uses for its inputs too): cleanup_stdio
  accepted on the M4F clang build, none lost on three builds; prvReloadTimer and clang's __sflush_r
  are UNCOVERED behind it (every case of their callback paths had such a pointer: the item below); another kind is an inconsistent input that makes a real
  callee read beyond a local of the caller (a queue's item size of 24 for a 4-byte item: memcpy copies the frame behind
  it, laid out otherwise in the C), which the harness cannot tell yet (it would need to know which
  stack bytes the call wrote); (4) scratch
  registers across calls (a few per build; GCC's -fipa-ra): a register the callee never writes, its
  callees included (regflow's preserved), keeps the caller's value in the C since 2026-10-20 (13 of
  the 52 refusals for reading a register behind a call on the ARM and RISC-V builds; on copies of
  GCC -Os, 291 to 295 on the MPS2 build, printf's printi and prints among them, and 214 to 215 on
  RISC-V, none lost); 2026-10-20: 20 more (FreeRTOS's RISC-V builds and GCC -O2: a2 around vAssertCalled)
  were a scratch register the caller saved and restored around the call itself, which regflow (a
  save and restore of a register that holds the entry's value is neither a read nor a write) let
  look live behind the call: not read behind it since (regflow 13); the other 39 read what the callee left, where regflow takes a variadic
  callee's save area as read (sprintf behind memset reads r3 only as far as its format goes) or
  follows libgcc's conditional code without its conditions (__aeabi_f2d). The RISC-V port's untyped handlers (labels in a section with mapping symbols) are missing
  from the ground truth. (Three instructions of each static-creation function cannot run, a volatile
  size asserted equal to itself: the lifter decides such branches since, and they are unreached.)
- [x] **The rest of the matrix**: clang for RISC-V (virt-clang-Os: built with -mno-relax, since clang's
  assembler and GNU ld turned the vector table's jumps into two-byte ones, which the system run showed)
  and the MSP430F449 demo built by TI's msp430-elf-gcc -Os with TI's support files (installed in
  ~/.local/opt): 250 functions, the listing rebuilds byte for byte, Ghidra finds 43 without symbols;
  no lifter for MSP430 yet, and no system-run models of its board.
- [x] **Stores through small pointers into an image at address 0** (the MPS2 builds have their flash
  at 0): a store through a null or small pointer lands in the image without a fault, the run is
  rejected as touching the image, and the generator learns nothing from it (pointer learning looks
  at faults); every input of one -O0 list function failed so, in a fresh process too. Learn from such
  stores (and loads of code as data) as from faults; measure on every build.
  Tried (2026-10-20): stores into the image learned from as faults, at the lowest address written.
  No -O0 list function fails generation any more (only vAssertCalled, which waits for a debugger, and
  cleanup_stdio), and on six uncovered functions of GCC -Os the cases covered as much or less
  (xQueuePeek 84 instructions against 89, vTaskGenericNotifyGiveFromISR 31 against 36): the learned
  pointers mostly moved the random search. Not kept; loads of code as data give no address (the C
  hooks keep a flag).
- [x] **Generated code pointers to the pointer stub**: an input whose run calls through a register
  into the image at no function's entry (Machine.watch_calls) gets that value pinned to the
  generator's pointer stub, as where a run dies at the jump, so that a callback path is covered by
  a fair case (prvReloadTimer on the M4F builds, clang's __sflush_r: UNCOVERED now that such cases
  are refused). Changes cases (VERSION); measure from scratch and on every gizmo build.
  Done (generator 24): a pass ends with drop_wild, which runs each case watching its calls; a case
  that calls into the image at no entry is repaired (the inputs that gave the address become the
  pointer stub, with its stub added and the instruction set's mode bit: a Cortex-M faults at an even
  target before a stub runs) and kept when it then ends fairly, else dropped; the inputs are pinned
  in a second pass with the first pass's settings (pinned memory cells set even where an input left
  them out). Found on the way: the generator's pointer stub was never in DREAD's stub lists (only case
  files that named it put it there), so nothing pinned before; and the old pin on a run that died at the
  jump compared values with their Thumb bit. Clang's __sflush_r: 49 of 119 instructions covered by
  fair cases to 119. prvReloadTimer stays UNCOVERED: with the stub as its callback the reload loop
  does not end (the wild call had broken it), and the search finds no period that ends it.
  Lift checks with every case generated anew, main against this: GCC -Os 297 to 298, clang -Os 269
  to 270 (__sflush_r both), the M4F clang build and RISC-V GCC -Os as before (280, 217), none lost;
  the gizmo builds' tables as before.
- [x] **A decision to implement** (`docs/decisions.md`, 2026-10-20): a case whose differences are all
  stale bytes of the original is compared except those bytes, which the report names. Done (the
  runner). The other one, a variadic callee's registers, was a regflow bug: clang's save areas behind
  sub sp went unseen; regflow 14, 18 refusals gone.
- [x] **The pilot on what the lifter leaves** (costs at Anthropic's list prices the user gave on 2026-10-20:
  Haiku 5.5 $0.10/$0.50, Sonnet 5.5 $2/$10, Opus 5.5 $4/$20, Fable 5.1 $10/$50 per million tokens in/out;
  the figures recorded before were at older prices assumed, ten times Haiku's): local model, deepseek-flash, Sonnet: success rate, time
  and cost per function (the usage ledger). --task input now also asks for the functions the lift
  check left uncovered. First run, deepseek-flash on 20 of GCC -Os: 3 covered whole, 5 of 53 missed
  instructions run, 74,000 tokens in and 352,000 out, 28 minutes. Most misses were failed
  assertions, which the generator covers itself since (stops): the next runs, with Sonnet and the
  local model too, go on what the measurement after that leaves.
  2026-10-20, --task input on the same 20 functions of GCC -Os (fewest missed first, 74 missed
  instructions), each on a fresh copy of the build, then dread lift --check on it (296 before):
  - Sonnet 5.5 declined all 20 (stop reason "refusal", no text), also with the purpose stated in
    the question; Opus 5.5 too; Haiku 5.5 answers the same question. What is declined: a listing
    together with the instructions an input should reach. $0.11 (at the list prices of 2026-10-20). A run stops asking a provider after
    three declined in a row since.
  - Haiku 5.5: 5 functions covered whole, 17 of 74 instructions; 53 answers, 181,000 tokens in,
    114,000 out, $0.08 at the list prices ($0.75 at the older ones assumed then), 9.5 minutes. The lift check
    accepts all five (301).
  - deepseek-flash, answers up to 32,000 tokens (at 8,000, 37 of 56 had been cut off reasoning): 7
    whole, 22 of 74; 43 answers, 122,000 in, 818,000 out, at most $1.49 (the balance went from 11.87
    to 10.38), 60 minutes. The lift check accepts six (302). The seventh, _fwalk_sglue, fails on its
    input: r1 = 0xe815, the callback, points into _fwalk_sglue's own body; the original runs its own
    instructions there, the rebuilt image has other bytes (an unfair input, not a lifter fault).
  - The local model (qwen3.8-27b, answers up to 32,000 tokens): cut off by the run's timeout after 3 h
    53 min with 18 of the 20 asked (about 13 minutes a function); 6 covered whole, 19 of their 59
    missed instructions; 43 answers, 150,000 tokens in, 1,018,000 out; the lift check accepts five
    more (301). Its summary line was lost with the cut (stdout buffered): the counts come from its
    conversations.
  So far: deepseek-flash finds the most (six accepted), Haiku nearly as much at a sixth of the time,
  the local model as much as Haiku at no cost but twenty-five times the time; Sonnet and Opus decline.
  Haiku 5.5 over every function with missed instructions of GCC -Os after measure-24 (34 functions,
  447 instructions): 8 covered whole, 154 instructions run; 91 answers, 370,000 tokens in, 223,000
  out, $0.15 at the list prices ($1.48 at the older ones assumed then), 19 minutes; the lift check accepts
  all eight more and loses none (298 to 306 of 366, 81 to 84 %): about $0.02 a function accepted. Open: where such inputs should
  live. They are a build's written cases (tests/extra_cases), which the benchmark's next build keeps
  or not; the measurement counts DREAD's own tools, so a second figure "with the pilot's inputs",
  their files kept beside the recipe (FreeRTOS is open source), would show both.
  Refused since: an input whose run makes the function call through a register to an address that
  is no function's entry (pilot.wild_call: the first block behind each of its indirect calls; a
  call through a veneer is not followed), with the tools' words back to the model; the question
  says that a pointer to code must be a function's entry.
  Haiku 5.5 again after the day's generator work (measure-28's GCC -Os, 310 accepted): 33 functions
  with 230 missed instructions left (447 that morning), 10 covered whole, 77 instructions run; 87
  answers, 327,000 tokens in, 188,000 out, $0.13 at the list prices, 17 minutes; the lift check accepts
  all ten and loses none (310 to 320): about $0.013 a function. Six of the ten Haiku found that morning too; two of that
  morning's eight the generator covers itself now (vListInsert, prvInsertTimerInActiveList). Done:
  measured with every provider; what the benchmark does with such inputs is a later item (below).
- [ ] (Later) **The pilot's inputs in the benchmark**: a `dread bench pilot` step that keeps the input
  pilot's written cases of a build beside the recipe (FreeRTOS is open source), keyed by the image's
  hash, and a second figure per build, "with the pilot's inputs", from a lift check on a copy, so that
  the measurement still counts DREAD's own tools first. About $0.13 a build with Haiku 5.5.
- [ ] (Later) **What the lifter leaves, round two**: (1) loop cases seeded with what the system run
  saw each callee of a task return and write (dread.sysrecord for loop functions: their stub answers
  as returns, fills and effects; about twenty task loops a build miss instructions behind checks in a
  row). Found 2026-10-21 that most of it was the loop cases' length: they ended at their 48th stub
  call, and FreeRTOS's test tasks call 64 to 175 times a round, so the checks behind the 48th call
  (the stores of xErrorDetected) were reached by no case. A loop case now ends at twice the
  function's call sites (at least 48, at most 512), and the compare-guided stage solves that many
  stub answers; on a copy of the GCC -Os build four of its seventeen task loops are accepted that
  were not (prvLowPriorityMutexTask, prvStaticallyAllocatedCreator, prvTestMasterTask,
  vPrimaryBlockTimeTestTask), prvTimerTestTask misses 228 instructions instead of 436,
  prvNotifiedTask 654 instead of 720, none worse. What was left was mostly inner waits: a stub answers
  0 past a case's list, which a wait takes for "not yet" (prvNotifiedTask's cases made all 230 stub
  calls and reached 253 of 671 instructions; an answer stage turning single answers changed nothing).
  Then the item's plan, the same day: dread sysrecord records a task's loop as a loop case, the answers
  its callees gave it in the system run (r0 back in its own context), what its own instructions read,
  and what callees stored behind pointers it passed (in registers and on the stack: the stubs of loop
  cases see their callees' stack arguments since); replayed, the round goes the run's way (all 161
  recorded calls of prvNotifiedTask), and each answer turned once (a site's first two rounds), with a
  stop where the turned run leaves the recorded calls (the assertion's), reaches the paths the run
  never took. On a copy of the GCC -Os build three more task loops are accepted (prvEchoClient twice,
  prvQueueSetReceivingTask), prvNotifiedTask misses 17 instead of 90 and 521 instead of 654, prvTimerTask
  10 instead of 20. (Found on the way: sysrecord looked cases up with a key of its own that had fallen
  behind and took a loop function's coverage from plain cases it made: now one key, autocase.case_key.)
  Left of the task loops: prvCheckTask (its run reaches no round), prvTimerTestTask, prvSenderTask and
  xPortStartScheduler (not traced). The bench recorded none of it until 2026-10-26: its record step
  took only the UNCOVERED and case-generation refusals, read no loop's kept cases (their line said "N
  cases kept;") and took sysrecord's exit status 1 for "nothing kept" for a failure, so the report's
  recorded column was "-" at every build of measure-41. Now it records the task loops whose loop cases
  left instructions unrun too (bench.RECORDABLE), sysrecord exits 0 whenever it ran, and a loop's line
  says "N kept;" as the others do. On a copy of measure-41's GCC -Os build (main at 1f54018) the
  recording kept cases for 7 of its 9 dropped task loops, and the lift check accepts 4 more
  (prvEchoClient twice, prvNotifiedTask_73ac, prvQueueSetReceivingTask_5ea8: 345 -> 350 with
  vPortValidateInterruptPriority, which the system registers' answers took, not in measure-41);
  (2) a cycle that changes nothing (registers alike at a pc, no store, no peripheral) as a
  defined end, like a branch to itself (vAssertCalled's wait for a debugger; it stays uncovered for
  its exit path, so mostly time: the failing generations of vAssertCalled, Reset_Handler, main and
  vRegTest2Implementation take 400 to 500 s a build); (3) the M4F builds' libgcc compare helpers that
  return flags (__aeabi_cdcmple and kin: about six refusals a build); (4) the disagreements left
  (clang's __sflush_r, prvSampleTimeNow).
- [x] **Measured 2026-10-26** (measure-40 of the 40 builds, DREAD a84de17, `dread bench measure --jobs 4`:
  5.9 h; the first attempt, one build at a time, was on course for about 11 h): every build lifts more
  than at measure-39 (2026-10-21), 11,203 -> 11,684 functions over the 38 ARM and RISC-V builds: ARM GCC
  -Os +9 to +19, -O2 +5 to +10, -O0 +25, ARM clang -Os +11 to +14, -O2 +14, RISC-V GCC -Os +13 to +18,
  -O2 +9, RISC-V clang -Os +15 to +17. The readable gate accepts 11,304 of the 11,684 (96.7 %). RISC-V
  GCC -O2 stays the lowest (203 of 300 lifted). The builds' targets predate the recipe's syscall_reads:
  the next measurement rebuilds them.
- [x] **Measured 2026-10-26** (measure-41 of the 40 builds, DREAD b957032: the day's coverage work up to
  the fault cases (the code behind a stop, a word read twice, deeper stops, faults), not yet the void
  parameters, pointer results or system registers; `dread bench measure --jobs 4`: 6.2 h, the targets
  rebuilt with syscall_reads): every ARM and RISC-V build lifts more than at measure-40 and none loses
  a function, 11,684 -> 12,148 over the 38 builds: ARM GCC -Os +3 to +9, -O2 +27 to +34, -O0 +12, ARM
  clang -Os +5 to +7, -O2 +17, the M4F builds +3 and +4, RISC-V GCC -Os +6 and +7, -O2 +41, RISC-V clang
  -Os +13 to +17. The readable gate accepts 12,121 of the 12,148 (99.8 %). RISC-V GCC -O2 stays the
  lowest (244 of 300). What stops the 1,286 functions left, summed over the 38 builds: cases leave
  instructions unrun 537, never returns and not all of it run 438, refused 226, no generated input ends
  42, a case differs 33. The register measurement (dread callconv) disagreed with regflow 18 times
  against measure-40's 7: the eleven new ones were the answers' fault cases, whose runs end in the
  middle of the function and were measured as returns; fixed (callconv leaves out runs that do not
  end at the return, from the run's log), on copies of the builds the count is back to 7. One of the
  7 is the original's own: RISC-V GCC -O2's vStartTimerDemoTask "reads its caller's s3" because
  xTimerGenericCommand queues a message whose union's last word no command sets, holding an s3 saved
  there before.
- [ ] **New architectures through it**: Cortex-M4F first (FreeRTOS's ARM_CM4F port, below), then MSP430X
  with TI's cl430 (FreeRTOS's MSP430X port), AVR (its ATmega ports), each as its own item below.
- [ ] (Later) a compute-heavy library (littlefs) for the case generator on parsers and data structures.

## Cortex-M4 (ARMv7E-M with FPv4-SP; the user, 2026-10-19: first among the new architectures)

Integer M4 code is the M3's instruction set and goes through every tool today. Missing: the DSP
extension, the FPU, the hard-float calling convention and their place in every tool, and a real M4
in the emulator. The MSP430 work in flight (its lifter, stack arguments) is finished first; the rest
of the MSP430 section comes after this one.

- [x] **The configured core** (with "The configured Cortex-M model" above): Unicorn's cortex-m4 (DSP,
  FPv4-SP) for a target that names it, which runs VFP code with nothing to enable (an M3 faults on
  it, as the hardware does).
- [x] **gizmo-m4** (2026-10-19): gizmo built with GCC for the Cortex-M4 with hard float
  (`-mcpu=cortex-m4 -mfpu=fpv4-sp-d16 -mfloat-abi=hard`) and C of its own in fw/dsp.c (a low-pass
  filter, an average and a conversion with rounding on floats; saturating and packed arithmetic
  through ACLE intrinsics: qadd, smlad, ssat, usat). Its reset handler enables the FPU (CPACR); key
  'f' runs one step of the signal path; scenarios press it (signal) and every key that returns
  (tour: 'b' waits for a timer nothing starts). It runs on Unicorn's own Cortex-M4; every test that
  takes a gizmo build runs on it (71 pass), the rewritten C agrees (563 cases), and the lift check
  accepts what gizmo-m3's does (34); the float and DSP functions are refused (the items below).
  Found on the way: case generation missed script_run's loops over a length it took for a pointer
  (fixed: near misses repaired, generator 22) and `dread lift --check NAME` replaced the lifted table
  unnoticed (now said). Not yet: a clang build of it.
- [x] **The DSP instructions in the lifter** (2026-10-19): saturating arithmetic (qadd, qsub, ssat,
  usat and their 16-bit forms), packed adds and subtracts with the GE flags and sel, the packed and
  halfword multiplies with and without accumulation, packing and extending, sums of absolute
  differences: 88 instructions, each as itself in inline assembly (Q and GE stay the core's own;
  the C is compiled for the same core). The fuzzer has a Cortex-M4 target (`--isa m4`) that draws
  them: 300 functions agree. gizmo-m4's four DSP functions lift and pass the check (38 accepted).
  Found on the way: capstone 5 lists umlal's and smlal's RdLo and RdHi as written only and ssat16's
  source not at all; the ARM layer has its own roles for those (operand_roles), so the register
  flow sees the reads now (regflow 8) on every ARM target, the M3's and ARM state's included.
- [x] **The FPU in the lifter** (2026-10-19): s0-s31 are the C variables f0-f31 (bit patterns; d<n>
  the pair f<2n>, f<2n+1>); moves (every vmov form and float constants), loads and stores (literals
  too, vldm and vstm) copy bits; arithmetic, the multiply-accumulates, conversions (integer, fixed
  point, half precision, both roundings) and compares are the instruction itself in inline assembly
  on scratch registers, so FPSCR (rounding, flush to zero, the cumulative flags) is the core's own
  and exact by construction; vmrs APSR_nzcv moves a compare's flags into n, z, c, v. The fuzzer's
  m4f target draws all of it and compares s0-s15 and FPSCR: 300 functions agree. Refused until the
  next item: a float argument (an FPU register read before written), a float result (a value left
  in s0-s15, unless the caller compares it as the fuzzer does), a call of a function that uses the
  FPU; and vpush, vpop and double precision. Found on the way: capstone names vmov.f32 sN, #const
  FCONSTS and vmrs APSR_nzcv FMSTAT, lists vldm's registers as not written, vstm's as not read and
  vpush's and vpop's as both (the ARM layer replaces those lists); and the lifter's entry analysis
  refused a register read under the condition it was written under in the same IT block (vmovgt
  s15, r1; vcvtgt s15, s15), core registers alike.
- [x] **The hard-float calling convention** (2026-10-19): `[abi] float` (hard where the cflags say
  -mfloat-abi=hard); regflow follows s0-s15 like the scratch registers (float arguments in
  read_regs, float results in kept_regs, which the runner compares); the case generator gives the
  FPU registers a function reads at its entry values of their own (zeros, ones, boundaries of the
  integer conversions, normal limits, denormals, infinities, NaNs, the function's float constants,
  any bits); the lifted C mirrors s0-s15 where it meets other code (at its entry, at every return,
  around every call), so float arguments, results and what a callee changes are exact. gizmo-m4: all
  four float functions pass the lift check (42 accepted, what gizmo-m3's does and more). Not yet: a
  generic stub's log of a callee's float arguments; a float result that nothing in the image reads
  is not compared (as an integer register beyond r0); double precision stays refused (vpush and
  vpop lift since: FreeRTOS on it, below).
- [x] **The system run with an FPU** (2026-10-19): where the FPU's state is active (CONTROL.FPCA,
  which Unicorn's Cortex-M4 sets when it runs an FPU instruction, as the hardware does) an exception
  pushes the extended frame, s0-s15, FPSCR and a reserved word behind the eight (0x68 bytes),
  returns through EXC_RETURN 0xFFFFFFE1, E9 or ED and runs its handler with FPCA clear; the return
  restores them and FPCA. Lazy stacking is modelled eagerly (the registers come out the same; the
  reserved words' memory differs only when a handler never uses the FPU). Tested on the core
  directly; gizmo's scenarios interrupt no float code: FreeRTOS's ARM_CM4F port (next) will.
- [x] **An interrupt inside an IT block** (2026-10-20, found by FreeRTOS's Cortex-M4F build): since
  the first exception model (2026-10-18) the M profile's entry stacked xPSR with the IT state
  cleared, so an interrupt taken between the instructions of an IT block came back with the rest of
  the block unconditional. The system run takes interrupts where translation blocks start, and
  QEMU's 1 KiB pages end one inside newlib's strcmp (`it cs` at 0x7fe, `cmpcs` at 0x800): three
  interrupts a run landed there, and one at the strings' terminator made strcmp compare past the
  end of equal strings (MessageBufferDemo.c line 868 asserted). The extended frames had only moved
  the timing onto it. The IT state goes into the stacked xPSR now, and the A profile's IRQ entry
  clears it for the handler as the core does; a test splits an IT block at 0x800 and takes an
  interrupt there (it fails on the old entry). Gone as well: the repair of a stale IT state at the
  next block's start (2026-10-19, for the recorder's memory hooks, which repair it themselves): it
  cleared the state of a block that starts inside an IT block, so an interrupt taken there while
  recording stacked it cleared again. Any Thumb-2 system run could hit this where a block boundary
  splits an IT block; the measurements up to 22 ran with it.
- [x] **FreeRTOS on it** (2026-10-20): the full demo with the ARM_CM4F port for the AN386 board,
  built by GCC and clang -Os with hard float (bench/freertos.toml, project mps2f; flop.c's math tasks
  run too, their arithmetic in single precision). Both rebuild byte for byte; the lift check accepts
  293 of 394 functions (GCC) and 275 of 384 (clang), recorded inputs adding 36 each; 13 minutes for
  both. fabs, the FPU's enable and the math tasks' start and check lift; the port's PendSV and SVC
  handlers are context switches (refused, as on the M3). Found and fixed on the way: a bl to a
  helper that saves lr and calls another (libgcc's __aeabi_dcmpgt) was taken for a switch helper's
  dispatch, an indirect jump, in every caller; libgcc's eight-byte push and pop of one register
  (str lr, [sp, #-8]!; ldr pc, [sp], #8), vpush and vpop were refused: they are pushes and pops now
  (the size from the ARM layer, stack_bytes; the fuzzer draws all three, 0 differ on a32, t32, m4
  and m4f, and a size planted wrong shows). That gains nothing in these builds yet: libgcc's
  __aeabi_dcmp* read the flags __aeabi_cdcmple returns (a result in the flags, which a C call cannot
  take), and regflow follows libgcc's conditional code without its conditions (__aeabi_f2d reads r3
  only where it wrote it under the same flags, __aeabi_dmul r5 likewise), so the math tasks, which
  call them in loops, are refused for reading what a callee leaves. Besides, the disagreements the
  M3 builds have (What the lifter leaves).

## MSP430 (of special interest to the user; right after RISC-V)

**Paused by the user on 2026-10-20** ("Can we pause MSP430 for now and continue with the rest?"):
no MSP430 item (MSP430X, the other tools, mul64, case generation, the FreeRTOS build, runaway mutants
on the slow engine) is taken until the user resumes it. Where it stood: FreeRTOS's MSP430 build 148
of 250 accepted (143 at measure-24) with stack switches stubbed in cases, the generator's pointer
buffers inside the scratch, -fno-ssa-phiopt for its lifted C, and calls through a register in the
lift check's call trees (its retry brought vStartLEDFlashTasks back). Next, when resumed: (1) the lift
check's retry also for a function left UNCOVERED while a callee's C failed (vStartPolledQueueTasks:
its queue-creating cases were refused while xTaskCreate's C was in; only failing functions are
retried now); (2) the recipe's scratch at the top of the flash (0xc000 up to the vectors, the room for
C up to 0xc000; the patch was measured, not committed): +1 (xSerialPutChar), the three queue functions
get room and are UNCOVERED (xQueueGenericSend 15 instructions missed, xQueuePeek 71, xQueueReceive
72), two functions' coverage better and two worse (the search's variance); (3) those queue
functions' and xTaskCreate's coverage; (4) the 28 two-byte libgcc shift entries (relocatable layout).

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
- [x] **The system run on gizmo-msp430** (2026-10-20): the layer's reset (sr clear, pc from the
  reset vector), interrupts (GIE; pc and then sr pushed, sr cleared, so a low-power mode ends, on
  through the vector of the controller's line: gizmo's one line is vector 0) and returns (reti runs
  in the engine, a low-power mode again unless the handler cleared CPUOFF in the stacked sr); the
  run already took an interrupt for a core asleep (no block runs: the clock goes on to the next
  event and the interrupt comes at the loop). gizmo's models with their 16-bit registers needed
  nothing. Every scenario agrees, original against rebuilt; the keys scenario takes the timer's
  interrupt twice. The lift check's system check runs on the MSP430 too and accepts irq_entry and
  main (gizmo-msp430: 36 accepted); the recorder takes a call's return address from the stack
  where there is no link register. A test wakes the core from LPM0 on the bare engine.
- [ ] **The other tools on gizmo-msp430**: switches, callsites, Ghidra, the GUI, CI (TI's GCC from
  its download with a checksum, or the MSP430 tests skip), the test suite's variants.
- [x] **The pilot's prototypes on MSP430 and RISC-V** (found 2026-10-19 while checking how the
  compiler lab feeds the pilot): the declarations the model is told to use had ARM's types for
  every architecture (u32 arguments, u64 when r1 was read). Now register-wide arguments (u16 on the
  MSP430) and a result as wide as the callers read, sized like the MSP430 lifter's (r12:r13 u32,
  r12-r15 u64): gizmo's `checksum(const u8 *p, u32 n)` is `u32 checksum(u16, u16, u16)` there,
  where the old text asked for six registers.
- [x] **The lifter and its fuzzer for MSP430** (`dread.lift_msp430`, `dread liftfuzz --isa msp430`):
  u16 registers, a frame array addressed in bytes, the flags as 0 or 1; GCC's shared epilogues (br
  #__mspabi_func_epilog_N) are returns, a call through a register that holds an entry is a call by
  name, a function that returns with reti is an interrupt handler (GCC's interrupt attribute), and a
  call's result is one, two or four words, as wide as the callers read. What GCC would compute with
  libgcc's shift routines (a sign bit, x != 0, swpb, two rra in a row) is the original's instruction
  in inline assembly: a generated case had stubbed __mspabi_srli (a run had hung in it) and the stub
  answered the C's own flag computations (gizmo's script_run looped); a lift check drops a function
  whose C calls what its original does not (none on the other gizmo builds or four FreeRTOS builds,
  1,313 lifted functions). The fuzzer found TI's GCC 8.2 deleting a volatile load and a store of the
  same value to the same address as a move to itself (an empty asm before every store now). Nine
  seeds of 16 functions of 60 instructions with 16 inputs each: no difference, nothing refused.
  gizmo-msp430: 48 of 130 entries lift (62 of the refused are libgcc's shift routines, labels that
  fall into one another); the lift check accepts 26, 13 more are written by hand; irq_entry lifts and
  is accepted by the system run since (with main).
- [x] **Stack arguments on MSP430**: the lifter refused functions with arguments beyond r12-r15
  (gizmo's parse_kv and sum6, libgcc's udivmodsi4) and their callers (apply_config); FreeRTOS's task
  creation takes two on the stack. Now they are parameters sa0, sa1, ... of the lifted C behind
  p12-p15, read and written through their addresses, which TI's GCC 8.2 keeps in the slots the
  caller passed them in (the original's places; it makes no sibling calls that could write them). A
  call passes the frame words that lie where its callee reads them (a tail call its own stack
  arguments), a call through an unknown register the lowest four frame words, as on ARM. Refused:
  the address of a stack argument in a register (va_start), a frame word read again behind the call
  it was passed to (the callee may change it, the C passed a copy; GCC's callers store anew), a tail
  call with a frame, the return address (GCC's prologue moves it when a 32-bit argument is split
  between r15 and the stack), what an interrupt saved. The fuzzer's functions take two stack words
  of their own (read and written, words and bytes, through sp and the frame pointer) and call a
  helper that takes two: nine seeds of 16 functions of 60 instructions with 16 inputs each, no
  difference, nothing refused. gizmo-msp430: 56 of 130 entries lift (48 before); the lift check
  accepts 32 (26 before; 34 with the generator's round of small arguments): parse_kv, udivmodsi4 and libgcc's four 32-bit division entries; sum6 lifts
  but src writes it by hand; apply_config lifts and agrees on its 70 cases, which never run the 37
  instructions behind a parse_kv that succeeds (the case-generation item below). Found on the way:
  the test of gizmo-msp430's rewritten functions had failed since the lifter came (two parse_uint
  cases, a wrong upper word): the lifted __mspabi_mpyl returned r12 alone, the width the original's
  callers read (it has none), while the rewritten parse_uint reads its 32-bit product. dread.build
  now tells the lifter what the hand-written C calls (Lifter.src_calls), and the MSP430 lifter
  returns r12-r15 from those (ARM and RISC-V do not use it yet); dread.regflow takes a runtime
  helper's result by the ABI as read besides, so its cases compare it. A bench test still expected no
  MSP430 lifter (failing since the lifter came too); it checks that the lifter takes the image now.
  Not done: the stubs of generated cases log no stack words on any architecture (a wrong word passed
  to a stubbed callee goes unseen), and written cases' stubs read them as ARM's 4-byte words at sp
  (on MSP430 they lie above the return address); generated cases pass stack arguments in cells at
  the stack top, which dread.runner.stack_moves_it does not move with the stack, so a case that
  differs through one is refused, not failed (a planted mistake in udivmodsi4's flag shows as
  UNCOVERED there, as FAIL in __mspabi_remul, which passes the flag from its frame).
- [ ] **mul64 on MSP430** (from the item above): mul64 calls __mspabi_mpyll, which takes its second
  operand in r8-r11 (the ABI's 64-bit helper convention): refused as reading what its caller leaves.
- [ ] **Case generation on MSP430**: every generated input of gizmo's strncpy and main failed ("only 0
  of 100 generated inputs ran to the end"). strncpy's count is a u32 in r14:r15, small only when both
  halves are: a round of small arguments when fewer than four inputs finished (strncpy: 69 cases, all
  31 instructions). main's loop calls only through registers (mov #f, r10; call r10), which the
  generator's callee list missed, so no loop case could end at a stub: the layer names such callees
  now (register_call_targets; main: 6 loop cases, 38 of 51 instructions). Generator VERSION 20.
  Left: script_run, gpio_set and handle_key keep instructions uncovered; so does apply_config, 55 of
  whose 70 cases pass its text at 0x11, in the I/O region, and none in a buffer of the generator's
  (its pointer argument not learned as one?); why, before the FreeRTOS build goes through. Generation on the MSP430 engine is slow (strncpy 193 s, main 322 s, with a
  benchmark running beside it).
- [ ] **FreeRTOS's MSP430 build through the lift check** (msp430-gcc-Os: 250 functions). 93 of its 158
  refusals were functions that fall into the next one: libgcc's shift routines (each entry shifts
  once and falls into the next one's), the shared epilogues and the startup. A plain instruction
  that falls into another function's entry is a tail call of it since 2026-10-20 (as regflow already
  took it): gizmo-msp430 lifts 84 of its 130 functions against 56, and its lift check accepts 50
  against 36 (the 32-bit left shifts; the 16-bit routines are an instruction of two bytes each, too
  short for the four-byte branch the overlay leaves at a moved function's entry: roadmap's
  relocatable layout). FreeRTOS's build (measured alone, 2026-10-20): 102 of 250 against 74, the
  32-bit shift chains (__mspabi_slll_N, __mspabi_sral_N), none lost; 7 s with the cached cases and
  the compiled engine (the lift took 627 s in measure-21). Left of those chains: the logical right
  shifts (next item), the epilogues (they pop their caller's registers), and system-run models of
  the MSP430F449 board for the tasks.
- [x] **The MSP430's flags bit by bit in regflow** (2026-10-20): clrc (bic #1, sr) writes C and passes
  Z, N and V through, so with the flags as one unit it read them; the logical right shifts
  (__mspabi_srli_N, __mspabi_srll_N) begin with it, and their callers were refused for leaving the
  flags, although the rrc behind it writes all four. The layer has flag_effects (each flag read,
  written, or passed through), and regflow (version 12) and the lifter's entry analysis follow the
  flags so where it is there, folding them into "flags" in what they report. gizmo-msp430: 112 of
  130 lift (84), the lift check accepts 78 (50); the MSP430 fuzzer agrees on three seeds. FreeRTOS's
  build: 143 of 250 (102), none lost: the two chains and 13 more that read flags a callee seemed to
  leave or need (pvPortMalloc, xQueueGenericCreate, vTaskSuspendAll, prvUnlockQueue, the LED and
  serial tasks, __mspabi_divul); 74 that morning.
  Next on that build: 14 callers of vPortYield (the port's context switch, its stack pointer loaded
  from the TCB) are refused for its stack arguments. `[abi] stack_args = {vPortYield = 0}` (since
  2026-10-20) lets them lift, but nothing more is accepted: their C needs room the image does not
  have (the lift check failed whole on a full room; it drops the largest without room now and goes
  on), and their callers' cases run the real context switch, which goes nowhere in a function's
  case (xTaskCreate's callers failed). Left out of the recipe until a context switch can be stubbed
  in cases.
  Done generically (2026-10-20): a load of the stack pointer from memory not on the stack (mov
  @r12, sp; ldr sp, [rN]; lw sp, k(rN); not a literal, not the frame) is a stack switch
  (lift.StackSwitch): the function is refused itself, takes as stack arguments the words it reads
  before the switch (vPortYield, xPortStartScheduler and the tick handler: none), so its callers lift
  without [abi] stack_args; and the generated cases of every function whose calls reach one (calls
  through a register too: GCC's -Os keeps vPortYield in r8 in prvIdleTask) stub it, the stub in their
  cache key (38 functions of that build). The build: 143 as before, ulTaskGenericNotifyTake and
  xTaskDelayUntil gained (no instruction covered before), vStartLEDFlashTasks and
  vStartPolledQueueTasks lost (not to room, as that commit said: in the round that judged them the
  C of xTaskCreate, which they call through a register, broke their cases, and the lift check's retry
  did not see such calls; see the pause note above); xQueueSemaphoreTake misses 21 instructions (72), and
  xQueueGenericSend, xQueuePeek and xQueueReceive have no room (2,094 bytes short). Room is the limit
  now: behind the image up to 0x8000, then the generated inputs' 16 KiB scratch, then 16 KiB of
  erased flash up to the vectors. Next: the generator's pointer buffers (scratch + 4 KiB, sixteen of
  1 KiB) bounded by the scratch's size (on this target they run past it and over the pointer stub in
  its middle), then the scratch to the top of the flash and the room up to it (30 KiB).
- [ ] **Stack arguments behind the pushed return address**: the layer does not say entry_stack_args (2:
  the return address a call pushes), so the generator's and dread record's stubs read a callee's stack
  words from sp itself, the return address first, four bytes at a time (x86 says 4 since 2026-10-27).
- [ ] **Runaway mutants on the slow engine**: a mutant that loops forever runs each case to the
  5-million-instruction limit (about 5 s per case on the MSP430 engine; two of gizmo's took over 300
  s). Bound a mutant's case by a multiple of what the original took on it.

## More architectures (each: a gizmo build with clang, the arch layer, every tool on it)

- [ ] MIPS32, little endian: delay slots (branch encoding in the overlay, the lifter, regflow), gp.
  Paused after step 1 (the user, 2026-10-20: compiler diversity first). Step 1 done: dread.arch.mips
  decodes the bits itself (capstone has no register access for MIPS and names coprocessor 0's
  registers as general ones), checked against llvm-mc on random operands of every integer form;
  gizmo-mips (clang, the image in kseg1 at 0xbfc00000, RAM in kseg0; clang has no bare-metal
  driver for MIPS: compiled with clang, linked with ld.lld); the listing rebuilds byte for byte
  with every instruction as text. Found: ld.lld puts .MIPS.abiflags and .reginfo in front of .text
  (every j and jal of the listing 0x30 off; the layer's discard_sections), and gizmo's own call of
  ram_crc (RAM, kseg0) by jal from the flash (kseg1) went to 0xb0000000: jal reaches only its own
  256 MiB region and ld.lld said nothing (a long call now; a test checks that every jump lands on
  code). Step 2 in progress on the branch `mips`: Unicorn's virtual TLB (kseg0 and kseg1 addresses
  as themselves, else nothing there fetches), o32's 16-byte argument home in front of the stack
  arguments, the generator's hooks, the entry branch, _gp for the overlay, regflow's delay slots
  (the branch's row takes its reads and the slot's effects, the slot's row the transfer: arguments
  set in a jal's slot). Open there: Unicorn segfaults translating a block during fmt_hex's case
  generation (tcg_const in tb_gen_code), which also hangs a pool whose worker dies so.
- [ ] AArch64 (A64): literal loads, adrp pairs, the AAPCS64 registers. Bigger than it looks: the first
  64-bit architecture, and the tools are 32-bit throughout (the emulator's arguments and results,
  the comparisons, the generator's values and pointer cells, regflow's masks, the lifter's u32
  registers): 64-bit registers and pointers through every tool first, from the layer's reg_bits.
- [ ] PowerPC 32, big endian: brings big-endian support to the emulator, the program database and
  the listing generator; lr/ctr calls.
- [ ] Big-endian ARM (BE-8) on the existing ARM layer.
- [ ] **x86 and x86-64 (amd64)** (the user, 2026-10-24), freestanding ELF images first. Variable-length
  instructions in the program database and the listing generator (no fixed alignment: decoding by
  flow from known entries); the arch layer on capstone and Unicorn (both have x86 and x86-64); the
  six flags (CF, ZF, SF, OF, PF, AF) in the lifter and regflow; the System V calling conventions
  (i386: arguments on the stack; AMD64: rdi, rsi, rdx, rcx, r8, r9, the 128-byte red zone) and a
  target's own convention where it states one; RIP-relative addressing. For x86-64, 64-bit registers
  and pointers through every tool first, as for AArch64 (shared work). Gizmo builds for both with GCC
  and clang, then an open-source program as a larger test target.
  Order (the user, 2026-10-26: "What about x86/AMD64 support?"; decided by recommendation): i386 first,
  since DREAD is 32-bit throughout (registers, pointers, the lifted C's u32) and the MSP430 work brought
  return addresses a call pushes and instructions of several lengths; AMD64 after the 64-bit work that
  AArch64 and RV64 share. Steps, each a branch with its tests:
  - [x] gizmo-i386: gizmo built with the host's GCC (-m32) and clang (i686-unknown-elf, ld.lld), flat
    32-bit protected mode, integer only (no x87, MMX, SSE), the RV32 build's memory-mapped peripherals,
    no interrupts yet; imported (dread import-elf) and listed identically. Done 2026-10-26 with the
    layer's first part (dread.arch.x86: decoding, flow, register effects, the stack, the listing in
    AT&T syntax, the assembler's and linker's flags by toolchain; the program database reads up to
    max_insn bytes to tell a length): both builds rebuild byte for byte at the first pass (GCC 955
    instructions with one raw, clang 1,048 with two), 46 functions each (tests/test_x86.py). Their code, measured
    2026-10-26: about 45 mnemonics (moves with zero and sign extension, push and pop, call, ret, jmp and
    the conditional jumps, add, sub, and, or, xor, not, neg, inc, dec, cmp, test, lea, leave, imul, mul,
    div, the shifts, rol, setcc, cmovcc, xchg, one movsb, hlt; clang pads with int3), and the read-only
    data inside .text: decoding goes by flow from known entries.
  - [ ] The arch layer (dread.arch.x86): capstone and Unicorn in 32-bit mode, the registers (eax ... edi,
    esp, ebp), flow and targets, register effects, the stack (push, pop, enter, leave), cdecl (arguments
    on the stack, eax and edx:eax results, ebx, esi, edi, ebp kept), a call's pushed return address in
    the emulator (as MSP430's push_return), dread.regflow.
    - [x] Code addresses keep bit 0 outside ARM. DREAD took bit 0 (ARM's Thumb bit) off code addresses
      in about 190 places, which made each of x86's odd entries the byte in front of it: gizmo-i386's
      import put clamp on the ret of the function before (a call returned at once) and broke the code
      ranges at every odd entry. Since 2026-10-27 each layer says which bit marks an instruction set
      (isa_bit: ARM 1, the others 0), and dread.arch.code_address, Program.code_address, Elf.code_value
      and dread.elf.dwarf_code_value take it off only there (docs/decisions.md, "Code addresses keep
      bit 0 outside ARM"). The other builds of gizmo import to the same tables; both i386 builds now
      run their functions at odd entries (tests/test_x86.py).
    - [x] dread test on gizmo-i386's rewritten C (2026-10-27): both builds agree on every case (GCC's 13
      functions with 515 cases, clang's 13 with 484; tests/test_x86.py). The layer has the switch shapes
      (cmp r, imm and and r, 2^n - 1 as bound checks; jmp [r*4 + table] read as the fallback), the
      generator's hints (immediates, switch_hints, compare_chars, branch_deciders, prologue_frame), the
      overlay's 5-byte jmp at a moved function's entry, resume_address, and pop_return setting eip (it
      did not: a stubbed callee would have run on); esp is "sp" as on every layer, the reading aids take
      the argument registers from the target's ABI (cdecl: none), and a stub gets its first four
      arguments from the stack behind the pushed return address (entry_stack_args). dread.regflow on x86
      (VERSION 30): merge_demand, flag_demand, refine_saved_writes and the layer's bit rules, checked
      against the emulator: no gizmo-i386 function reads a register at its entry any more (parse_kv's mov
      dl, byte ptr [eax]; cmp dl, 0x20 read edx; mov bl, cl all of ecx), and the one register kept besides
      eax is real (uart_putc leaves edx to uart_puts: GCC's interprocedural register allocation). Found on
      the way: dread recon did not see a change to a file a source includes from elsewhere (now the
      compiler's -MMD list), capstone's cmov reads its destination and cdq writes edx alone, and the
      shared rewritten fmt_hex reads the original's digit table (HEX_DIGITS) so that each build leaves
      the registers its original leaves (docs/decisions.md, "The rewritten fmt_hex reads the original's
      table").
    - [x] Generated cases log a stubbed callee's stack arguments (2026-10-27, generator VERSION 35):
      only a loop case's stubs logged them (Generator.stack_words, from the lifter's stack analysis);
      the stubs the other stages make (a callee a run hung in, the decider stage's, the chase's, a
      stop's, those that reach a context switch) logged the argument registers alone, which on i386
      is nothing: GCC's handle_key handed read_line any length unseen. Now every stub of a callee logs
      its stack words (Generator.stub_words), on every architecture (ARM's fifth argument and up);
      with read_line handed 41 for its 40 bytes, handle_key is dropped (tests/test_x86.py).
    - [ ] refine_saved_writes on ARM and RISC-V: whether refining an instruction that writes a callee-saved
      register (uxtb r4, r1 reads the low byte of r1) changes a lift check or a readable gate there;
      only x86 says it so far (dread.regflow refinement).
    - [ ] The system run on x86: sysrecord's stack arguments (entry_stack_args), interrupts (the IDT),
      a device model of the i386 build's peripherals (the RV32 build's).
    - [ ] The extents' owner table (Program.owners) counts halfwords: on x86 the halfword in which one
      function ends and the next begins at an odd byte counts as the next one's, so a load from the
      first one's last byte would count as a read of another function's code (not confirmed to happen:
      compilers put no data there). Bytes where the layer's min_insn is 1, when a case shows it.
  - [x] The lifter (dread.lift_x86) and the lift check on gizmo-i386 (2026-10-27): every function of
    both builds lifts but the startup and GCC's main (it aligns esp): 44 of 46 on GCC's build, 45 on
    clang's. A jump through a table is a switch over the index its bound check takes (script_run;
    clang's dispatch, its handlers inlined), GCC's dispatch a tail call through the pointer it loads
    (switches.pointer_tail now also where the call pushed the return address). The lift check accepts every
    function that lifts but ram_crc, which runs from RAM: 43 of GCC's and 44 of clang's since generator
    VERSION 36 (41 and 43 at first: dispatch and script_run, below).
    The fuzzer's x86 ISA (dread liftfuzz --isa x86: every width of the arithmetic with the flags, the
    shifts, mul, imul, div, setcc, cmovcc, lea, xchg, frame slots, push and pop, branches, cdecl calls)
    agrees on 800 random functions of 40 instructions (seeds 21 to 28, the last three on the final code).
    What it took beyond the MSP430 lifter's plan: eax handed in and on with GCC's regparm(1) (cdecl
    passes nothing in a register, but a function that does not write eax hands its caller's eax back, and
    the cases compare a void function's result register: uart_init; with regparm(1) the stack arguments
    stay where cdecl has them), the entry analysis without the reads of a write of a register's part
    (merge_only), a call reading eax only where the callee takes it (GCC's pop eax that takes an argument
    off again), the registers GCC keeps across a call (uart_putc's edx: -fcall-saved-edx, the call not
    clearing it), the flags a core leaves undefined as the emulator (QEMU) leaves them (div keeps them;
    sf and zf of mul's low half; a shift by cl writes memory back with a count of 0), shift by cl and
    parts read outside register_only in dread.regflow's bits, generic stubs that answer edx for a callee
    whose callers read it (edx is no argument register: the C's edx at a call is its compiler's), and a
    case generator that has nothing to vary (no argument, load or call: uart_init).
  - [x] Readable C on gizmo-i386 (2026-10-27): every lifted function of both builds passes dread readable
    --check (GCC's 43, clang's 44 since generator VERSION 36; 41 and 43 before). What it took: eax handed in with -mregparm=1 from dread.build's REGPARM
    line instead of an attribute in the C's head: the readable passes write heads anew by pattern, read_line's
    lost its regparm(1) when the types pass gave it another type, and every argument was read a word off.
  - [x] GCC's dispatch on i386 (2026-10-27): its handlers, tail-called through the pointer it loads, pass
    their argument on to gpio_set by rewriting the stack arguments they came with, in dispatch's caller's
    frame, and a C call passed copies. The C's tail call is now one the compiler must make a jump
    (__attribute__((musttail)) return), with eax alone for GCC, which then writes no argument word: the
    handlers find their arguments in the caller's words, as the original's do, and the lift check
    accepts dispatch. clang's musttail takes the caller's signature, so there the arguments go along,
    each written back into its own slot (where a callee would not write them, the cases see it). GCC
    takes musttail since 15: with an older one (CI's GCC 13 failed the lift check's whole build on it)
    the tail call is refused, as dread.target's Compiler.accepts finds.
    dread.cparse reads a statement attribute in front of a return, and void_results leaves such a
    return whole.
  - [x] script_run on i386 (both builds, 2026-10-27, generator VERSION 36): the generated cases missed an
    arm of its switch (index 1 on GCC's build), where every ARM and RISC-V build's cases reach every arm.
    The cause was not the switch: the generator learned the function's arguments, stack words on i386,
    as memory cells, which the chase leaves alone, and the length kept the scratch pointer the search had
    once given it, so every run of the xor arm went on for half a billion bytes and was dropped (dread
    uncovered: forced into the arm, a run returned after four instructions). A case's args now hold the
    stack arguments behind the argument registers (Generator.arg_count; Machine.call writes them at the
    stack top), varied, chased and learned as pointers as registers are: every arm is reached, and both
    builds accept every function that lifts but ram_crc (tests/test_x86.py, tests/test_autocase.py).
  - [ ] mul64's width: its written cases compare edx, which no caller in the image reads, so the lift
    check takes it as one word (the dread.regflow width of a function nobody calls).
  - [ ] The parity and auxiliary flags (jp, setnp, lahf, pushfd: refused), string instructions with a
    compare (repe cmpsb), the stack's alignment in main (and esp, -16).
  - [ ] A larger target: FreeRTOS's IA32 port with its demo for Intel's Galileo Gen 2
    (FreeRTOS/Demo/IA32_flat_GCC_Galileo_Gen_2: the kernel, the port's assembly, fifteen of the common
    test task files, the demo's own support code), integer only (no flop.c, configSUPPORT_FPU 0).
    First look on 2026-10-27, a trial build with the host's GCC -Os (not yet a bench recipe):
    - [x] dread import-elf and dread asm on it: the import took a global label without a type in front
      of _start for a function (the multiboot header: data in .text), whose bytes read as a call far out
      of the image, and called_without_function then decoded outside the image and stopped. A label
      without a type that nothing points at and whose bytes do not hold together as code is data now
      (elfimport.data_labels); the program database decodes a code range again from every function entry
      in it and takes bytes that do not decode for no code (the header's bytes changed with the layout,
      and the walk across them stopped the whole program); and dread asm --verify writes raw from the
      start what the assembler encodes otherwise (x86's padding nops: 66 90, lea esi, [esi + 0]) and,
      while a region has a length change, takes nothing but its first one in a pass: the listing went
      from 808 raw instructions after sixteen passes to 13 after two (a far jump capstone writes as GNU
      as does not read, eleven padding nops, a near jmp with a short distance: GNU as keeps a jump to a
      global symbol at 32 bits).
    - [x] A bench recipe (bench/freertos.toml, project ia32; 2026-10-27): three builds, Debian 12's GCC
      for i686 at -Os and -O2 and clang 16 (its assembly through GNU as: the demo's .S has .func and
      suffix-less memory operands, which clang's own assembler takes neither of), the demo's
      -march=pentium; DREAD compiles C with the host's gcc -m32 and clang (dread_cc). Each imports
      (the multiboot header data: with Debian's GCC its bytes read as code that ran on into _start,
      elfimport.data_labels now takes that for data too) and its listing rebuilds byte for byte
      (GCC -Os 14 raw instructions after two passes, clang 18 after six, GCC -O2 431 after seven: the
      padding nops between functions and in loops). What it needed, below. The host's 32-bit libgcc does not do,
      because CachyOS builds its packages for x86-64-v3 and its __udivdi3 uses lzcnt, shlx and shrx,
      which the Galileo's Quark (a Pentium) never runs: Debian's gcc-i686-linux-gnu in a toolchain
      image. The demo needs patches: the full demo (mainCREATE_SIMPLE_BLINKY_DEMO_ONLY 0), stats
      formatting off (kernel V11 asks for trace facility or run-time stats with it; the demo has
      neither), SIZE_MAX (its own stdint.h has none), the port's ISR_Support.h as before a formatter
      broke its assembly in 2023 (% eax, ".\n if": kernel commit 01820d3ed has it intact, the same code
      once comments and spaces are left out), no -fdata-sections (the demo's linker script collects
      .bss alone, so its startup would clear nothing), and room for C that moves: its linker script
      puts .data right behind .text, and the first lift check dropped 138 of 293 functions for room.
      The bench took a patch that takes a file as of another commit ({file, commit}: file_at), a build
      key for the compiler DREAD compiles C with on the host (dread_cc), a patched linker script
      (the link took the source's), a fetch that applies sparse paths added since, and a digest of a
      build without the sparse paths (docs/decisions.md). Not measured yet (lift, readable): next.
    - [ ] The lift check with room (.data moved 256 KiB away): 102 of 293 accepted, 109 refused, 82
      dropped at first; with the steps below 189 accepted, 36 refused, 68 dropped (2026-10-27: 54 held
      back by coverage, 14 without cases, none whose cases disagree), and 206 with the PC's
      conventional memory mapped (docs/decisions.md). What it showed, largest first:
      - [x] GCC's local calling convention (2026-10-27): a static function whose address is not taken
        gets its first three arguments in eax, edx and ecx (regparm(3)), prvAddCurrentTaskToDelayedList(eax,
        edx) say; the lifter took eax alone and refused those that read edx or ecx (15) and their callers
        (about 25). A function that reads edx or ecx at its entry takes all three now (X86Lifter.reg_args;
        LLVM's fastcc, ecx and edx, lands in its registers the same way): its file's REGPARM line says 3,
        its head takes eax_in, edx_in and ecx_in, and a declaration of a callee whose convention is not
        its file's says its own (__attribute__((regparm(N)))), which the readable passes keep (the types
        pass and polish leave such a declaration as it is, dread.shapes carries the attribute along).
        tests/test_x86.py: a static mix(a, b, c) and its caller pass the lift check and the readable gate,
        which reads return mix(esi, 7, edi) + s0.
      - [x] Generated cases give a function its register arguments (2026-10-27, x86 generator_version 2):
        x86's ABI has no argument registers, so a case's arguments were stack words and its eax, edx and
        ecx what the harness left; on the trial build 21 functions got no case at all (every input of
        prints faulted on one string pointer) and 8 more missed the arms their registers decide. A case
        carries the registers its first arguments go to (its regs: Generator.case_regs, the layer's
        case_regs over dread.regflow's reads at the entry: eax, edx and ecx where the function reads edx
        or ecx, eax where it reads eax alone), Generator.apply hands them to the machine, and Machine.call
        puts them there, once (written cases keep their C arguments in the ABI's places).
        tests/test_x86.py: pick(p, n, k) reaches both arms.
        A stub logs what such a callee reads of eax, edx and ecx (the layer's stub_regs, which reads_mask
        goes by on x86: by the ABI's argument registers, which cdecl has none of, a stub logged no
        register argument there). A stop logs them as a stub does (2026-10-27: it took the mask's
        places from the stub's arguments, cdecl's stack words on x86, which differ with the frame's
        layout; FreeRTOS's vTaskDelay disagreed on the stack words where its callee's edx is), and the
        words a printf format takes beyond the named ones are stack words there.
      - [x] Calls of a variadic function (2026-10-27): the words a call passes came from the callee's
        stack analysis, which for printf sees the format alone; the original pushes the format, the file
        and the line, the C passed one word, printf read a file name from elsewhere and prints faulted
        (323 of the first round's failing cases, every one through vAssertCalled). A callee that walks
        its stack arguments through a pointer (va_start: X86Lifter.walks_stack_args) gets the words the
        call site stored one after the other from the stack pointer up since the call before it, a
        branch target or the entry (set_up_words), and is declared variadic (u32 f(u32, ...)), which
        GCC passes on the stack whatever the regparm, so eax is no argument of it; a tail call to one is
        refused. vAssertCalled, xQueueGenericCreate and vQueueDelete then pass, and readable C reads
        printf(0x10d1b4u, ebx, esi) (tests/test_x86.py: a variadic total and its caller).
      - [ ] A stub of a printf-family callee on i386 logs its named words alone (stub_words: the
        callee's own stack analysis); its format's words are stack words there, which format_mask
        (registers) does not see. Only where printf is stubbed (a run hung in it): it mostly runs for
        real, and what it prints is compared.
      - [x] int N (2026-10-27; 16 functions at first, 23 once regparm(3) let more of them lift): FreeRTOS's
        yield (int 0x50, portYIELD). As RISC-V's ecall and ARM's svc, a logged system call in the emulator:
        Unicorn reports int N as it reports a fault, with its number, but resumes behind it, so the layer
        tells it by the two bytes cd NN in front of eip (software_interrupt, which dread.emu asks); and in
        the C inline assembly that hands eax, ecx and edx to the handler and back (it may change them, the
        emulator's no-op does not), the flags and memory clobbered (tests/test_x86.py: tick yields).
      - [x] cli and sti (7: interrupts masked around the critical sections) and bt reg (4: the event
        groups' bt eax, 0x19) since 2026-10-27: cli and sti inline assembly that runs in the emulator as
        in the original, bt the bit into cf (zf as it was; the other flags undefined, and compiled code
        reads cf behind it), and rep movsd (2), which the lifter's string loop took all along: capstone
        names it with its prefix (rep movsd), which the prefix check refused. tests/test_x86.py: crit,
        flag and copy.
      - [x] The address of a stack argument (2026-10-27; 7 on the trial build: printf, g_printf, ...):
        va_start's lea eax, [ebp + 0xc] is (u32)&sa0 + 4 in the C, and an index into the stack arguments
        (GCC 13 made the same va_arg loop mov ecx, [ebp + eax*4 + 0xc]) goes from sa0's address too: the
        C's stack parameters lie in the caller's words one after another, as cdecl puts them, and GCC and
        clang keep a parameter whose address is taken in its slot, so a walk on from it reads what the
        caller pushed. printf, g_printf and g_printf_rcc pass the lift check (tests/test_x86.py: total).
      - [x] in and out (2026-10-27): a port space beside memory. dread.emu hooks them (the layer's
        port_instructions) and logs every access in order as a device access (port read, port write);
        a case's ports answer in (0 at any other port; formats.md), and the generator takes the ports a
        run read for inputs of later cases; the lifter writes dread.h's dread_inN and dread_outN, inline
        assembly of the instruction itself, which the emulator runs and logs as it does the original's.
        Unicorn had run in as 0 and out as nothing, unlogged. On the trial build the demo's inb ... outl
        and pciIOread32 pass; an x86 string instruction no longer reads the flags (the direction flag
        alone, which the ABI keeps clear: rep movsl two calls up made inl hand its flags back; regflow
        VERSION 32). tests/test_x86.py: kbd polls a status port and reads the data port.
      - [ ] Pushes of every register, iret, rdtsc (the port's assembly and the support code: refused,
        rightly for now).
      - [x] Three of the trial build's disagreements traced to their cause (2026-10-27): (1) GCC 16
        reorders two volatile loads: mov eax, [ebx + 0x38]; cmp [ebx + 0x3c], eax lifted, combine folds
        the later load into the compare and then the earlier one too (cmp mem, mem matches i386's pattern
        before reload), and reload loads 0x3c first, so a null queue faulted at 0x3c, not 0x38
        (xQueueIsQueueFullFromISR; GCC 11 to 13 and clang keep the order, -fdisable-rtl-combine too,
        but made the trial build's lifted C 6.5 % larger): a compiler barrier in front of an instruction
        that reads two values of memory outside the frame (insn: _needs_barrier, _track_loads); (2) a
        tail jump passed its stack arguments as volatile reads, which GCC stored back into the
        incoming slots before its jmp (uxTimerGetReloadMode and vEventGroupClearBitsCallback differed
        in every case): the parameters themselves now, which GCC and clang jump with in place; (3)
        frame_effects skipped push's and pop's memory operand, so saves_out took a word that only push
        dword ptr [ebp - 0x1c] reads for a save, and the C lost its store
        (prvAddCurrentTaskToDelayedList passed vListInsert a word it never stored); pop into memory
        through esp, whose address three places formed three ways, is refused (no compiler writes it).
        tests/test_x86.py: isfull, wrap, spill (each fails without its fix).
      - [x] A push of a register that a call takes as a stack argument reads that register (2026-10-27,
        regflow VERSION 31, x86 generator_version 3): FreeRTOS's prvCopyDataFromQueue takes its buffer
        in edx (regparm(3)) and only pushes it, as memcpy's first argument; dread.regflow counted no
        push as a read (made for ARM's push {r3, lr}, which only keeps the stack aligned), so its reads
        were eax alone and the C of its callers passed eax alone (xQueueReceive and xQueuePeek, which
        lift since the address of a stack argument does, failed so). Where the layer says pushes pass
        arguments (pushes_arguments), a push into a word a call takes (its callee's named stack
        arguments: the lifter's stack_args; call_words, which passes every stored word to a callee
        that takes the address of one of its own, made a padding push ecx an argument and refused
        xQueueReceive for an ecx its caller seemed to read) reads its register (Analysis.
        argument_pushes); the lifter's reg_args takes regflow's reads besides its own entry analysis.
        GCC's padding pushes in front of the arguments and its push of a dead register that only
        reserves a word stay no reads. xQueueReceive and xQueuePeek agree on every case now (their
        coverage keeps them out); tests/test_x86.py: fill and user3, which fail without it.
      - [x] A string operation through a frame address keeps the frame (2026-10-27): frame_effects
        skipped memory operands with a segment register, and capstone writes movsd's destination as
        es:[edi]; vGalileoInitializeLegacyGPIO copies a table onto its frame with rep movsd and off it
        with another, saves_out took the words for untouched, and the C's frame array held 32 of its
        128 bytes (the copy overwrote the C's return address: every case failed). A string operation's
        accesses through esi and edi are now unsized ones, which keep the whole frame where either
        holds a frame address (frame_uses' FrameReach). tests/test_x86.py: init.
      - [x] A call with stack arguments stays a call (2026-10-27): in front of the C's return, GCC 11
        turned spill's call of take into a jump and stored take's arguments into spill's own incoming
        slots first, a write the original's call never makes (GCC 16 did not; tests/test_x86.py's spill
        failed in CI's Ubuntu 22.04 image). An empty asm statement behind such a call (KEEP_CALL) keeps
        it a call; a tail jump passes the function's own parameters, which the compilers jump with in
        place.
      - [x] Readable C on the trial build (2026-10-27): 209 of its 212 lifted functions readable, 17
        structures and 19 arrays of static data found. Two of the three that stayed lifted were the HPET's
        interrupt handlers: they read the counter's high and low words into frame slots and add a global
        to the pair; the readable passes take the frame's volatile stores away, the statement that
        combines the three meets GCC 16's reorder again, and the low word came first. dread.volorder puts
        the lifter's barrier into readable C in front of a statement that reads two values of volatile
        loads, where the layer says instructions fold memory operands (folds_memory_operands: x86).
        tests/test_x86.py: counter (refused without it).
      - [x] A register a function hands back unchanged through a call (2026-10-27): EnableI2CController
        never writes ecx and calls GetI2CIoPortBaseAddress, which leaves it alone; its callers keep a
        value in ecx across it (GCC's -fipa-ra). At its call ecx held what its own caller left, and the
        x86 lifter refused ("reads ecx behind the call"); since the function hands it back unchanged
        (call_saved: its file is compiled with -fcall-saved-ecx), the C keeps the caller's value through
        the call. tests/test_x86.py: outer2.
      - [ ] An input that has code read stack words nothing wrote (a length past a local:
        prvNotifyQueueSetContainer copies an item of the input's size from its 4-byte local) reads the
        original's frame layout, which the C's differs from; dread.fragile refuses it only where a saved
        register is touched (saves_touched). A check that the run reads no stack byte below the entry's
        stack pointer that it did not write first would refuse it as the others are (with a Timeline
        mark at the first such read).
      - [x] PCI configuration space as a device range (2026-10-27): the Quark keeps bus 0's
        memory-mapped at 0xe0000000, and GetI2CIoPortBaseAddress reads the I2C controller's BAR0 at
        0xe00aa010; unmapped, every input of the I2C and GPIO drivers faulted there. With the window
        among the bench target's io ranges, six of them pass on the trial build (two more lack
        coverage alone: WaitForStopDet, Pcal9555SetPortRegBit); with port I/O and the conventional
        memory, the trial build passes 212 functions before it (from 189).
      - [ ] The case generation errors of the trial build (14): every input faulted, mostly where a
        callee polls a device through a base address the startup sets (vAssertCalled's
        ucGalileoGetchar reads the UART through a pointer that is 0 without the startup), and low memory
        is unmapped on x86, where Cortex-M reads the vector table at 0 (a null pointer there faults
        nowhere): the tail-call wrappers of FreeRTOS's event groups run their callee's list walk into
        address 4.
- [ ] AVR on the emulator interface the MSP430 work brings (clang builds it, Unicorn does not run
  it): an interpreter behind it, Harvard memory (program and data spaces) in the program database.
- [ ] Z80 (low priority; the user, 2026-10-19: "Z80 and later 8051, but both low in prio"): an engine
  of DREAD's own (Unicorn has no 8-bit core), SDCC as the compiler (packaged; its own assembler and
  linker, Intel HEX with a map, `__sdcccall` conventions), and SDCC as a cclab adapter: the hardest
  test of the lab's generality (no GNU tools, its own linker).
- [ ] 8051, after Z80 (low priority): its four address spaces (code, internal data, external data,
  bits) in the emulator and the program database, and a cclab measure for arguments in a static
  parameter area (SDCC's default for functions that are not reentrant). Keil C51 and IAR's 8051
  compiler are the proprietary compilers it would be compared with.

## Floating point

- [ ] VFP in ARM state and double precision (VFPv3/v4-D16 and wider): what the Cortex-M4 section
  leaves, on the A profile (a gizmo build with hard float; d0-d7 arguments).

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

## Scale and robustness

- [x] **A suite that hung on a forked child** (2026-10-26, under a load of about 20: a bench
  measurement, a private gate and another suite beside it). The full suite stopped at 53 % in
  tests/test_pilot.py, most likely test_the_input_question_shows_the_callees_and_a_stopped_input_where_it_stopped
  (the test after the newest temporary directory): pytest waited for a child process (do_wait) that
  waited on a lock (futex_wait) for 23 minutes, beside defunct workers: a process forked while another
  thread held a lock. The next suite on the same code passed it. Find what forks with threads alive
  there (the pilot asks its questions in threads, and readable's question trees come from a pool) and
  start such pools before any thread, or with the spawn or forkserver method; a suite should also stop a
  test that waits too long instead of hanging (a timeout per test). Found the same day: no thread
  outlives a test in tests/test_gui*.py (without a browser), test_jobs, test_llm*, test_pilot (a probe
  that printed the threads alive before each test found none), and the suite already takes Python's
  warning of a fork beside other threads for an error; so the child was likely a worker a pool forked
  from its own handler thread to replace one that ended (the defunct workers beside it), while another of
  its threads held a lock. A test that hangs fails after half an hour now (pytest-timeout in the test
  extra, [tool.pytest.ini_options] timeout = 1800; docs/decisions.md). It happened again the same
  evening in a push's tests, and the timeout gave the traceback: test_a_dry_run_of_the_input_task_asks_nothing
  -> dread pilot's main -> missed_instructions -> dread.runner run, its `with Pool(...)` ending in
  terminate, which sends SIGTERM to the workers and waits for each. dread pilot's main runs inside
  llm.ending_on_signals, whose SIGTERM handler the workers inherit; it is Python code, and a worker
  blocked in a wait in C never runs it (the fix of 2026-10-24, the handler resetting itself in a worker,
  does not reach such a worker). Done: the pools' initializer (dread.util.polite die_with_parent) gives
  SIGTERM and SIGHUP their default, so terminate ends a worker wherever it waits; test
  tests/test_llm.py test_a_pool_ends_a_worker_that_waits_in_c (a worker locking a mutex twice under
  ending_on_signals: the old initializer held the pool until the test's timeout, the new one ends it in
  a second). Not found: why a worker of that pool waited on a lock at all (idle, before any task).
- [x] **Copies of one target share their generated cases** (2026-10-25, from the private real-life
  test: a naming experiment's copy of a target made every case anew after the target's own lift check
  had just made them, hours of work). `[paths] cases` puts the case cache where a target says (default
  `BUILD/autocases`; Target.cases_dir, used by every reader of the cache); copies that point it at one
  directory share it, each entry still checked by its key (docs/target.md; tests/test_shared_cases.py).
  Not shared: the regflow cache (minutes, not hours), and nothing across images whose functions share
  names (they would keep replacing each other's entries).
- [x] **Case keys scoped to a function's call tree** (2026-10-23; docs/decisions.md). The generated
  cases' key held one environment for the whole image (dread.autocase Generator.environment): a change
  of what dread.regflow found anywhere, or of the stubs' answers beyond a C result, made every case of
  the image anew (on the private real-life test too, for a change that concerned the callers of some
  functions). Now it has two parts (docs/formats.md, "the key"): whole,
  what no call tree scopes (the image's bytes, the startup copies, the memory map, and, in no key
  before, the code's layout, the devices, stack, sentinel and scratch, the calling convention, the
  fixed registers, the core, the domains' address rows); and the facts of every function the
  function's runs can reach (dread.autocase reaches: calls, tail calls, the fall behind the last
  instruction, the targets a run of an indirect jump's dispatch finds; every function's where the tree
  calls through a pointer): its name, its entries in regflow's case facts, its stub's answers beyond a
  C result, and, in no key before, its wide_results row and domains rows, digested once per strongly
  connected component. A change of one function's facts on FreeRTOS GCC -Os (MPS2, 366 functions)
  keeps 321 keys for vListInsertEnd, 304 for xQueueGenericSend, 98 for vPortEnterCritical, 347 for
  main (before: none); the lift check after the change of vListInsertEnd's made 43 of 362 entries anew
  in 135 s (68 s cached; main's code made 359 anew in 678 s). 5 to 6 per cent of the functions of
  FreeRTOS's ARM builds have a transfer that no table tells (a call through a pointer, mostly) in
  their tree, 1.2 to 3.5 on RISC-V, 19 on the MSP430. The keys of a FreeRTOS build take 0.2 to 2.7 s
  (the scoped part 0.03 to 0.6). Every image's cases were made anew once, byte for byte as before
  (MPS2 GCC -Os and RISC-V GCC -Os: the same cases, the same 341 and 272 functions accepted; gizmo's
  tables unchanged).

- [x] **A bulk run's memory, item by item** (2026-10-23, found on the private real-life test: one
  worker of a lift check grew by gigabytes on one function until the run was killed, which the
  machine's other work felt). `DREAD_ITEMS_LOG=FILE` has every worker of a pool that hands the
  slowest out first (dread.util.poolorder: the tests', the integration run's and the lift check's
  case runs) write a line when it starts an item and when it ends one, with its pid and its memory,
  so the pid that grows names its item; with it a worker writes its Python stack to its stderr on
  SIGUSR1 (faulthandler), which needs no debugger (ptrace's scope may forbid one). The cause it
  found: a case run kept every device access of its log as a tuple, and a long written case that read
  a device register over and over made the log gigabytes. A run now logs its first IO_LOGGED
  (100,000) accesses one by one and folds the later ones of each stretch between two other entries
  into one ("io folded", their number, a 128-bit digest of them) where the stretch ends: two logs are
  equal exactly when the accesses are, so nothing compares less (rule 6), and Result.diff() names the
  first entry where two logs part instead of printing both whole. There the function's worker stayed
  within a few hundred MB of where it started, its time a few per cent longer (the digests).

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

- [x] **README's status section generated** (the user, 2026-10-27): `dread status` makes it from
  docs/todo.md's checkboxes, the gizmo builds' tables, the bench recipe, the commands and the tests'
  count, between two markers right below the introduction; tests/test_status.py fails on a stale one.
  The README cleaned up with it: the old status table's descriptions in docs/features.md, the commands
  and setting up in docs/usage.md, the browser view in docs/gui.md (docs/decisions.md).
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
