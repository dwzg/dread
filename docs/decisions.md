# Decisions taken by recommendation

The user, 2026-10-20: "Generally I leave decision to your recommendation. Just note it down so I
can review your decisions later." A question that used to wait in `docs/todo.md` ("Questions for
the user") is decided by the recommended option and written down here: the question, the options,
the choice, why, and how to undo it. What only the user can do (provide files, credentials, buy or
install system software) and what cannot be undone (a force-push, making something public) still
goes to the user.

Newest first. "Status" says whether the choice is in the code yet.

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
  unchanged.

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
