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
  gotos 12,950 -> 12,228 (FreeRTOS has few switches); on a private firmware's readable C, which has
  many switches, the gotos fell by more than half. The gate accepts the same functions as before on
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
- [ ] **Variables.** Frame words as locals (regflow's frames: a word a function only stores and loads
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
  structure with a pad of 4 GB: + 0xffffffffu is - 1, offsets are signed now). Open: a pointer to a
  field inside a structure (a task's xStateListItem at 4) stays an integer; a list reached through an
  item's pointer field is a kind of its own (joining kinds through pointer fields would join what a
  void * field holds, an item's owner: tasks and timers); embedded structures as members. An address
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
  RISC-V a task block's fields at 0x10 and 0x24 and a list's count, typed as pointers.
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
- [ ] **Memory.** Plain accesses for RAM and the stack, volatile kept where order is behaviour (the
  peripheral ranges of the target's memory map). Done when the gate and the system runs agree.
  Thought 2026-10-21: plain accesses let GCC merge and hoist loads, and a loop that waits for a flag
  an interrupt sets in RAM would no longer see it change; the original's accesses all happen. The
  types item gets readable accesses without that: volatile in the pointer's declaration, p->f at the
  use. This item waits until that is done and it is clear what is left.
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
  private firmware's in a sixth of the time, dread.readable in_pool). Next: types: the
  structures behind pointers derived by code (accesses at constant offsets through the same
  pointer, unified across functions), with volatile in the pointer's declaration so that a field
  reads p->f, their names and fields' names proposed the same way.
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
  Next: on the private firmware, the defaults (votes of five and a second pass: does the confidence
  catch the misread driver?), the runs of the cases (`--behaviour`) where the code says less (its
  drivers, and leaves that touch only unnamed globals: its misses) against its notes with text ends,
  and the globals between the passes; then subsystems as a step of their own (functions grouped by
  calls and addresses, a prefix proposed a group with its evidence: texts, devices) to catch a
  misread driver before its callers inherit it.
- [ ] **A no-confidence vote on existing names** (the user's idea, 2026-10-22: reverse engineering as
  a puzzle, where a sub-picture can look right in one context and wrong in another). Not a periodic
  re-vote by the same model: consistency is not correctness (a misread driver was named after the
  misreading almost throughout, and a vote by fit with the neighbours would have voted out its few
  right names), the judge flips 7-14 % of its own verdicts, and names that depend on each other can
  oscillate. Instead: (1) triggers computed by code: the function's own evidence (texts, constants,
  device registers, behaviour) against its name, low agreement of the votes (calibrated on FreeRTOS: a
  name one vote of five gave judged different 88 %, a unanimous one 15 %; the ablation flag, the name
  changing with the neighbours hidden, was measured and is no error signal), callers' summaries that
  describe its role differently; (2) sub-pictures tested at their edges: a group of functions (calls,
  addresses, one prefix) against what lies outside it (callers outside the group, the texts and
  registers it uses), since a wrong sub-picture is consistent inside; (3) re-decided with hysteresis:
  a challenged name changes only when a new one wins a pairwise comparison by a margin, "more general"
  one of the outcomes (a helper two subsystems share), every change logged with its reason, rounds
  capped; (4) names people gave are flagged for review with the reasons, never replaced. Measured on
  the stripped FreeRTOS build and on a run where a whole driver was misread: does it find that group
  by itself, and fix it without breaking right names? Builds on the votes' confidence (Function names,
  2026-10-22).
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
    prvLowPriorityMutexTask at 46 against 1 (124 statements with its three inlined functions).
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

Limits known up front: readable C compiles to other code, often larger than the original's slot, so
more functions need freed space (the overlay's placement report says where); a function whose lifted
C keeps registers exact for its callers (bound calls, -ffixed-REG, registers a callee reads stale)
may stay lifted.

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
  the clock, 125,000 accesses a million units, nearly all of the clock): a C path for model reads
  without side effects is the next performance item there.
- [ ] **An interrupt held off by a mask that code outside shared code lifts waits for the next
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
- [ ] **Three RISC-V builds' whole images differ with lifted C alone for reasons of their own**
  (the only ones left of the benchmark's 38 once the wait rule was fixed, 2026-10-22; they differed
  at the same entries before): the rebuilt clang -Os and clang 14 images print "Stack overflow in
  SetB" (entries 355 and 359; a task's stack too small for the C's frames, which is what the whole
  image is there to find), and GCC 10's asserts (line 1367) at entry 32 and then reads unmapped
  memory (0xfffffb85) while it prints the file name. Not traced.

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

- [x] **Code only a dropped case reached** (found 2026-10-22 on a private firmware, DREAD's real-life
  test: its uncovered functions took far longer to generate than those accepted, and most of them
  met callees stubbed for hanging). A case that should end at a call (stops, a loop case)
  and ends otherwise (spinning on a register, at the limit) counts while the search runs, and must:
  it moves the compare-guided stage's frontier (gizmo-rv32cpp's main: where a run waits on memory,
  that stage finds the callee effect that ends the wait; not counting such runs lost four of its
  instructions). But drop_bad_ends drops it from the result, and what only it had reached was left
  uncovered: on the private firmware, a call whose one kept run was meant to end at a call deeper in
  and spun on a device register before it. Generator 34: when the surviving
  cases cover less than the search counted, the compare-guided stage runs once more from what they
  cover, every case a source, counting only runs whose case survives, and a run there that comes
  back cut or spinning in a callee is tried again ending at that call too (repair_lost). On a sample of the
  private firmware's uncovered functions a few were accepted and some more miss fewer instructions,
  none more; the gizmo builds lose nothing (msp430's main is accepted by loop cases again instead of
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
  Measured on a large private image: loading 0.14 s, the background analyses 19 s, a median function
  0.024 s (2.6 s before the GUI reused one lifter fed with the never-returning functions it already
  has), the largest 1.5 s.
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
- [ ] v3, next: an overview where a cluster of functions is one node (a subsystem: by call structure,
  by the files of the ELF where a bench build has them to score it), opened by a click; colours from
  name confidence rather than name status, and the pilot's no-confidence vote's suspects marked once
  it exists; calls through pointers from the system run's record (who called whom), so that a task's
  or a callback's caller shows; the system run's timeline (device logs of both images side by side).

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
  xPortStartScheduler (not traced); (2) a cycle that changes nothing (registers alike at a pc, no store, no peripheral) as a
  defined end, like a branch to itself (vAssertCalled's wait for a debugger; it stays uncovered for
  its exit path, so mostly time: the failing generations of vAssertCalled, Reset_Handler, main and
  vRegTest2Implementation take 400 to 500 s a build); (3) the M4F builds' libgcc compare helpers that
  return flags (__aeabi_cdcmple and kin: about six refusals a build); (4) the disagreements left
  (clang's __sflush_r, prvSampleTimeNow).
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
- [ ] x86, 32-bit freestanding: variable-length instructions in the program database and the
  listing generator.
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
