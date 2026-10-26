# Decisions taken by recommendation

The user, 2026-10-20: "Generally I leave decision to your recommendation. Just note it down so I
can review your decisions later." A question that used to wait in `docs/todo.md` ("Questions for
the user") is decided by the recommended option and written down here: the question, the options,
the choice, why, and how to undo it. What only the user can do (provide files, credentials, buy or
install system software) and what cannot be undone (a force-push, making something public) still
goes to the user.

Newest first. "Status" says whether the choice is in the code yet.

## Division routines as / and % (2026-10-25)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), from docs/todo.md's
"Division helpers as operators": a compiler without a divide instruction calls a routine for x / y and
x % y, and readable C printed the call with its two words cut apart.

- **Question.** How does readable C know a routine divides, write its calls, and still pass the readable
  check, whose link and whose test of foreign calls the C's own / and % meet?
- **Options.** Knowing the routine: (a) by name only (the RTABI's, libgcc's); (b) by name, and by what it
  does in the emulator for one without such a name. Writing a call: (c) a macro of dread.h; (d) the
  operators. The C's own / and %: (e) only where GCC needs no helper (a constant divisor it multiplies);
  (f) the helpers it calls made of the image's routines where the image lacks them, and taken as what
  the original calls where the original calls a routine that divides.
- **Choice.** (b), (d), (f). A library of a compiler's own, or a stripped image, has no such names, and a
  routine's behaviour on a few pairs (both orders, both signs, quotient and remainder) leaves no doubt,
  and the probe is cheap (most candidates are out after their first pair). Not a function that uses a
  divide instruction (the program's own code, which such a routine stands in for) nor a wrapper that
  hands back nothing its callee does not (gizmo's udiv_var): their calls stay calls, as does a routine's
  own call of another (written as an operator, GCC would call a helper made of the routine itself).
  (e) would have kept most variable divisors as calls. Where a function's / and % fail its cases (a case
  that stubs the routine sees each call, which the compiler's / does not make where it knows the
  divisor), the check tries it once more with its calls kept (divcalls), the first of its second tries,
  and, as its last before it is dropped, with the calls of the dividing functions its code reaches kept
  too, given back where it is dropped all the same (tried earlier, a caller that another change helped
  kept its callee's calls for nothing).
- **Undo.** Leave the readable_types table's `divisions` empty (dread.readable division_plan) or delete
  dread/divisions.py and its four uses (make_plan, polish_tree, chooser's support, check's foreign).
- **Status.** In the code (tests/test_divisions.py).

## Names from elsewhere, a page of the GUI (2026-10-25)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with what was left of "The GUI
for everything" (docs/todo.md): dread whatcc, samecode and callpair had no view; their results showed in a
job's log only.

- **Question.** How does the GUI show three tools that propose names from elsewhere, and how are their
  proposals applied?
- **Options.** (a) A view each. (b) One page for all three, since they answer one question (names for this
  image's functions), each source a command run as a job and its proposals a table with a box each, the
  chosen ones applied by dread rename as a job. (c) As (b), applied by writing symbols.txt from the page.
- **Choice.** (b): a page of its own (#/names, key e: n was rename's already), a proposal offered only where
  it gives a function without a name one (FUN_... here, a name there: the tools' new_name, what --symbols
  prints), applied by dread rename, which renames everywhere a name is written and refuses where the tree
  then contradicts itself (C that no longer compiles, case files that name what is gone). samecode and
  callpair write `--json` for it (docs/formats.md); whatcc's table is read as it writes it.
- **Undo.** Delete dread/guipages/names.py and dread/web/names.js (and the two `--json` options).
- **Status.** In the code (tests/test_gui_names.py).

## Words that hold a structure's pointer (2026-10-25)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the item "Pointers
held in globals; frame objects to all-u32 externs" (docs/todo.md), its first half.

- **Question.** A word at a fixed address that holds a pointer to one of the structures (FreeRTOS's
  pxCurrentTCB) stayed a u32, cast to the structure at every use (`r3 = (volatile struct s_8
  *)g_20029aac;`, six times in prvAddCurrentTaskToDelayedList). When is such a word declared as the
  pointer, and how are its uses written then?
- **Options.** (a) Leave it. (b) Declare it as the pointer when every typed variable that loads or
  stores it takes it as a pointer to one structure, such uses at least as many as the others, and it is
  only loaded and stored whole; write it as a typed variable is written (apply_types: no cast into a
  typed variable, members through it, `&g->f` for a member's address, `(u32)` elsewhere). (c) As (b),
  with a stricter share (typed uses twice the others).
- **Choice.** (b): dread.shapes pointer_globals, the readable_types table's global_pointers (by address,
  whatever the word's name), apply_types adding the words to its typed names and declaring their macros
  as the pointer, as volatile as before (`(*(volatile struct s_8 *volatile *)0x20029aacu)`).
- **Why.** The word's type is then the source's, and its uses read as the source's: members and member
  addresses (`&g_20029aac->f_4` for `&pxCurrentTCB->xStateListItem`) where there were sums and casts.
  The casts move rather than vanish where the word is read into a variable that a call takes as a u32:
  measured on the eight builds, casts of such words to a structure pointer 53 -> 13, 50 -> 15, 52 -> 47,
  31 -> 31 and `(u32)` casts of them 0 -> 31, 40, 1, 0 (GCC -Os, clang -O2, GCC -O0, RISC-V GCC -Os),
  nearly all pxCurrentTCB passed to list functions whose parameters are not typed: typing those is the
  next step, and it would take these casts too; (c) would have kept pxCurrentTCB a u32, the item's own
  example. The gate accepts the same functions on all eight builds.
- **Undo.** Leave `global_pointers` out of the plan (pointer_globals returning {}); a table without it
  reads as before.
- **Status.** In the code (tests/test_global_pointers.py).

## Truth values of ifs that store 0 and 1: one comparison, proved; a store of the value held dropped (2026-10-25)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the item "Truth values
as one comparison" (docs/todo.md).

- **Question.** A dispatch by successive subtraction that sets a result before its tests and again in
  each case (`mov r0, #0; subs r1, r1, #1; blo a; beq b; ... a: mov r0, #0 ... b: mov r0, #1`) printed `r0 = 0; if (x == 0) r0 = 0; else if (x == 1) r0 = 1;` for
  `return x == 1`; a conditional move after a 0 (`mov r2, #0; cmp r1, #5; movhi r2, #1`), a value set
  and cleared on the other way, and GCC -O0's `xReturn = pdFALSE; if (...) xReturn = pdTRUE;` read alike.
  Where do such ifs become the truth value, how is the arm's store of the value already held dropped,
  and how is each kept exact?
- **Options.** Where: (a) in dread.structure only, once the branches are ifs; (b) a pass of the clean-up
  for the conditional blocks it has (IT blocks, ARM's conditional moves) and the same function as the
  structure stage's last step for the ifs of branches; (c) dread.polish, on the finished tree. The arm's
  store: (d) by data flow in the clean-up's flat view (reaching definitions: every definition that
  reaches the store gives the same constant); (e) within the truth value itself: the ifs' value is the
  select of every store in them and of the value before, a store of that value a leaf like any other.
  The value: (f) rules per shape (v = 0; if (c) v = 1; as v = c); (g) the select of the stores written in
  the conditions' order (c ? 1 : 0 as c, c ? x : 0 as c && x, ...), made small by the conditions' search
  and by leaving out operands the others imply, each step and the whole proved by dread.cexpr.
- **Choice: (b), (d) and (e), (g)** (dread.tidy held and truths; dread.structure calls truths last).
  held drops an arm's v = K (behind a label of the clean-up's flat view: a conditional block's arm, a
  branch's target) where every definition of v that reaches it is the program's store of K (not a
  declaration's initial value, nor one of the zeros the lifters give the registers a call leaves, which
  carry the call's instruction) and the ways back to them pass only statements without an effect.
  truths takes v = K; and an if-tree behind it whose arms store only constants into v or nothing,
  conditions that do not read v (the first may load or call once: it runs first in the value; the
  others have no effect), and makes v the truth value where every constant is 0 or 1, with the same
  load or call made first on every path; the new statement keeps the first store's instruction, the
  ifs' absorbed, the arms' stores as copies (dread.naming identities: a name made for v carries). The
  conditions' search takes a truth value compared with 0 for itself (fold leaves `if ((c && !z) != 0)`
  where the variable truths made was tested).
- **Why.** (b): the clean-up sees a conditional move's if and folds the new value into its reader
  (`return r0 + (r1 > 5);`), while the ifs of branches exist only after the structure; the structure
  stage is the last stage, and what the tables do after it (types, names, frames) reads its output.
  (d) and (e): the flat view has exact reaching definitions; the structured tree has none, and the
  truth value needs none (the arm's store of the value held is a leaf of the select). (g): the
  conditions' order and evaluation are kept exactly by construction (&& and || where the ifs nest), so
  the proof only has to show the smaller forms, and a rule per shape would miss the else-if chains.
  held's restrictions come from first versions on FreeRTOS GCC -Os, which moved stores: one that dropped
  any store a store of the same constant reached took GCC's zero for a call's fourth argument, still in
  r3 behind the call (dread.regflow knows the callee keeps it), for the source's `xStartIndex = 0` in an
  else (prvNonBlockingReceiverTask), and moved a loop counter's `r4 = 0` back to an earlier zero of r4
  (prvSenderTask), in ten functions; one that took the lifters' zeros behind a call for definitions
  dropped an IT block's own `movne r2, #0` (xTimerGenericCommandFromTask); one that took any store, not
  an arm's, dropped a movt's repeat of its movw's value and moved the statement's instruction (clang
  -O2's prvCheckTask). All of them correct, and further from the source; truths would have taken the
  lifters' zeros behind a call for its first store too, and does not. A declaration's initial value is no
  definition for held (the lifters give the registers no argument holds 0 at the entry: a first version
  took clang -O2's printi's `r4 = 0` for that 0).
- **Not established.** Ifs that store other constants (`v = 0; if (c) v = 7;` stays: a select of
  constants would be another rule); a store and its if with a load or a call between
  (xAreBlockTimeTestTasksStillRunning: `s12 = 1; r2 = g; if (r2 == ...) s12 = 0;`), a run of ifs that
  each clear one flag (`if (a) s12 = 0; if (b) s12 = 0;`); `v = x; if (v != 0) v = 1;` (clang's subs;
  movne: `r6 = x != 0`); a truth value of branches' ifs folded into its one reader (no clean-up runs
  behind the structure: `r0_1 = r1 == 1; return r0_1 + r0;`); held where the value is kept through
  other work.
- **How to undo.** Take ("held", held) and ("truths", truths) out of dread.tidy PASSES, the call of
  tidy.truths at the end of dread.structure.structure and the comparison with 0 in smallest_truths.
- **Status.** In the code (dread/tidy.py, dread/structure.py; tests/test_flags.py). On the eight builds
  the gate accepts the same functions (run with --verify), the readable tables byte for byte, and the
  printed C of 9 functions changed; the figures are in docs/todo.md under the item.

## Facts about variables: known bits from all their definitions, assumed by the proofs (2026-10-25)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the item "Register
shifts with an amount known below 32" (docs/todo.md).

- **Question.** ARM's shift by a register takes the amount's low byte, and 32 and more give 0 (asr: the
  sign); the lifters write lsl r0, r1, r2 as `(s & 0xffu) == 0 ? x : (s & 0xffu) < 32 ? x << (s & 0xffu)
  : 0`, and readable C printed it so, also where the source wrote `acc ^= p[i] << (i & 7)` (gizmo's
  script_run, behind `r2 = r3_2 & 7`). The first arm is always the rest's value (x shifted by 0); the
  guard is redundant where the amount is known below 32, and for a variable that knowledge comes from
  its definitions, which dread.cexpr's proofs did not see (a variable was any value of its type). How
  is a rewrite proved under facts about variables, where do the facts come from, and how are they kept
  sound?
- **Options.** The facts: (a) none, only the expression's own known bits (the first arm goes, the
  guard stays unless the mask is written in the amount); (b) known bits of each variable over all its
  definitions in the function, flow-insensitive, a greatest fixpoint; (c) flow-sensitive known bits per
  statement (reaching definitions, conditions on the way: a counter below 5 in a loop). How the proof
  takes them: (d) substitute a variable's known bits into the expression (r2 as r2 & 7); (e) an
  assumption on the variable in cexpr (Ctx.facts: the test values keep the known bits, z3 assumes
  them). Where: (f) in dread.bitfields' known bits, so that every rule of the pass reads them; (g) a
  rule of its own for shifts alone.
- **Choice: (b), (e), (f)**, with two rules for selects in dread.bitfields: a register shift's first
  arm goes where the rest shifts by its amount (`y == 0 ? x : ...x << y...`, proved: x << 0 is x; a
  rotation keeps it, x << 32 is undefined), and a select whose condition the known bits decide is the
  arm it takes (decided: the guard of an amount below 32). variable_facts: every assignment, compound
  assignment, increment and initial value of a variable in the function (nested blocks too), the
  variables in the order of what their definitions read (strongly connected components), a component
  whose definitions read each other met with each definition until nothing changes (optimistic: a
  definition that reads a variable no definition has reached yet waits), a variable whose bits weaken
  FACT_WEAKENINGS (3) times taken as unknown (a counter); nothing known of a parameter, an asm's
  output, a variable whose address is taken, one a nested block declares without a value, and one that
  may be read before it is written (live behind its declaration: asked only of the variables that get
  facts, which keeps the liveness off most functions).
- **Why.** (b): the only masks the bits pass left on variables were these amounts (the item "Extensions
  and bit fields"), each with one definition; flow-insensitive facts hold at every use, so a proof
  under them is sound wherever the expression is, with nothing to track per statement, and webs splits
  a register into one variable per web, which makes the definitions of one variable few. (e): the
  assumption is stated once, where the variables are, and the same proof machinery checks every rule
  (d would have changed the expressions the rules match). (f): known() is where the pass reads bits, so
  masks and narrowing casts of such variables go as well, each proved (`r4 & 1` of a value 0 or 1,
  `(u8)(r3_1 | g)` of a byte). Unknown starting values: a fact must hold on every path that reaches the
  use, and a parameter's value at the entry or an uninitialised read reaches some.
- **Not established.** A bound only a comparison on the way gives (c: FreeRTOS's
  vPrimaryBlockTimeTestTask shifts by a loop's counter below 5, __sflush_r tests r1_2 <= 29 before it
  shifts); a variable's bits through steps of itself in sequence (gizmo's __udivsi3: r2 += 8; r2 += 4
  before it shifts by r2 + 3, which the flow-insensitive fixpoint takes for a cycle); 16-bit int
  (MSP430: the pass leaves it alone).
- **How to undo.** In dread.bitfields, take the facts out of bitfields() (ctx.facts) and the Cond rules
  out of proposals; cexpr's facts then go unused (an empty Ctx.facts changes nothing).
- **Status.** In the code (dread/bitfields.py, dread/cexpr.py; tests/test_bitfields.py). On the eight
  builds the gate accepts the same functions, the readable tables byte for byte, and the printed C of 77
  functions changed; the figures are in docs/todo.md under the item.

## A link that refuses one function's C drops that function; Thumb-1 switches without GCC's helper (2026-10-25)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the item "dread cclab
run --lift with gcc-arm (GCC 16.2): the lift check of cortex-m0-O2 and cortex-m0-O3 fails as a whole"
(docs/todo.md).

- **Question.** On GCC 16's Cortex-M0 builds of the compiler lab at -O2 and -O3 the lift check failed as
  a whole ("the build failed: the link failed": op_switch_jump's lifted C calls `__gnu_thumb1_case_uqi`,
  which the link does not have), so those builds got no verdicts; at -Os the same function was dropped
  alone ("its C calls __gnu_thumb1_case_uqi, which the original does not call"). The per-function check
  finds the call in the -O2 object as well (the same R_ARM_THM_CALL against the same undefined symbol in
  .text.op_switch_jump): it never ran there, since it read the objects only after a link that went
  through. Lifted C is compiled at -Os whatever the original's level; GCC makes a Thumb-1 switch a call
  of `__gnu_thumb1_case_*` only when it optimises for size (or for position-independent code); and the
  image has the helper only where the original's own code calls one (from libgcc: the -Os build). There
  the link resolved the C's call and the check after it dropped the function; at -O2 and -O3 the link
  failed first. What should (1) the check of what the C calls, (2) a link the linker refuses, and (3)
  lifted C that calls such a helper do?
- **Options.**
  1. The check of calls the original does not make: (a) after a link that went through, as before; (b)
     before every link, from the compiled objects (dread.build build's vet).
  2. A link the linker refuses: (a) fails the check, as before; (b) drops the functions whose objects or
     sections the linker's errors name, with the linker's words in their verdicts, and links again; an
     error that names none of the functions the check may drop fails it as before, with the message;
     (c) halves the functions until the link goes through, as the whole image's differences are halved
     (culprits).
  3. Lifted C that GCC compiles into a call of a switch helper (Thumb code of a core without Thumb-2):
     (a) dropped, as before (with 1b at every level alike); (b) compiled with -fno-jump-tables wherever
     GCC compiles Thumb code for such a core (the ARM layer's lifted_cflags, as the MSP430's
     -fno-ssa-phiopt); (c) the same, only where the image lacks the helper; (d) libgcc's helper linked
     into the C's room; (e) the lifter writes such a switch otherwise (labels as values in a table of
     its own).
- **Choice.** (1b), (2b), (3b).
- **Why.** (1b) What the C calls is a property of its object alone (its undefined symbols against what
  the lifted C calls by name); the link adds nothing to it, and asked before the link it holds wherever
  the link would fail on the same call, with the same reason at every level. In a round whose link would
  also overfill the room for the C, the function that calls a helper now goes first and the largest
  without room after it; the room is then short by less among the same candidates less that one, so that
  round drops no function for room that the old order kept (not shown over several rounds). (2b) The
  linker names the culprit of an undefined reference or of a relocation by its object and section, and a
  lifted function's object and its .text.NAME are its own (dread.overlay blame reads GNU ld's and lld's
  forms: the place a message begins with, GNU ld's "in function" line in front of what it says with debug
  information, lld's ">>>" lines; one function a message, the one it names first). Halving (2c) needs a
  link a step and cannot tell an error of nobody's (a missing library fails every half). An error that
  names no function the check may drop stays an error of the build (dread.build BuildFailed), as for a
  source written by hand that does not compile. (3b) The cases compare what the C does (results, memory,
  calls, the original's coverage), not how its switch dispatches: a tree of compares runs the same arm
  for the same index (a switch statement's meaning in C), and with the flag GCC 10, 13 and 16 emit no
  helper (each checked, CI's two as well). Where the image has the helper (option c's exception), calling
  it was no better: such C was dropped there too, since a case's stub of the helper would answer the C's
  call, so a flag that depends on the image keeps functions out for nothing and makes one function's
  translation depend on what else the original's compiler linked. (There the functions are not gained
  yet: an original that dispatches through the helper gets no case that runs the dispatch, since the
  runner refuses an input whose run loads a byte of a function's extent from another function, and the
  helper reads its caller's table; docs/todo.md.) (3d) brings the toolchain's library into the rebuilt
  image, needs the core's multilib, and still leaves a call the original does not make; (3e) is machinery
  in the lifter for what one flag of the compiler does. clang makes an inline table on Thumb-1 (a byte
  table read behind add pc, rN; clang 23), and ARM state and Thumb-2 have inline tables (ldr pc, tbb,
  tbh): their C stays as it was. A target that names its own [compiler] lifted_cflags replaces the
  default and the flag with it, as for the MSP430's.
- **Cost.** The size of a switch's C changes little: op_switch_jump's 144 bytes with the helper's call
  and its table, 156 with the compares (104 in the original at -O2; moved behind the image either way);
  the test's six-way switch 100 and 96. A larger sparse switch grows more; on a core whose image is
  full it may find no room ("no room left for its C": the safe side).
- **Measured** (main at 1c241ac against this, each build's lift check on copies with the same cached
  cases). The compiler lab's gcc-arm matrix (GCC 16.2, its sixteen builds with --lift): cortex-m0-O2 and
  -O3, which had no verdicts, lift 89 of 94 probe functions each, op_switch_jump among them (four probes
  that take a va_list's address are refused and _Z11cxx_virtuali's cases leave seven instructions unrun,
  at both); cortex-m0-Os lifts 89 as before, op_switch_jump dropped as before but now for its unrun
  dispatch (above), not for the helper's call; cortex-m0-O0 and the twelve Thumb-2 builds give the same
  verdicts with the same reasons. gizmo built for the Cortex-M0 in a scratch directory (its image has the
  helpers, for its own switches; no C written by hand): 49 accepted either way, handle_key and script_run
  dropped for their unrun dispatch where they were dropped for the helper's call. The eight builds accept
  the same functions, their lifted tables byte for byte and their verdicts with the reasons the same:
  gizmo a32 36, m3 36, m4 44, rv32 32; FreeRTOS mps2-gcc-Os 341, mps2-clang-O2 300, the RISC-V GCC -Os
  build 272, mps2-gcc-O0 382; and so do gizmo's m3cpp (43), rv32cpp (39) and msp430 (79).
- **Not established.** (1) dread.overlay blame knows the forms GNU ld (seen from 2.27 to 2.47; the
  tests link with 2.38, 2.42 and 2.47) and lld (14, 18 and 23) write; another linker's forms, or a
  message that names no object or section of a function, fail the check as before (the safe side). (2) A
  message whose first-named function is not its culprit (a form not seen here) would drop the wrong
  function; its verdict shows the linker's words. (3) -fno-jump-tables reaches readable C on such cores
  too (it is compiled with the same flags); dread readable --check was not run on a Thumb-1 build. (4)
  An image whose [arch] differs from the target's gets the target's flags. (5) Other helpers lifted C
  may call where the original does not (a 64-bit shift's on a core without the instruction, libgcc's
  shifts on the MSP430) are dropped as before, now at every level alike, not avoided. (6) The system
  run's single builds of a function that never returns (system_check) are not vetted (no case stubs a
  callee there) and drop the function on a refused link, now with what the linker said of its C; the
  whole image (whole_image) takes a refused link for its difference and halves, as before.
- **How to undo.** dread/arch/arm.py: lifted_cflags removed (3); dread/lift.py check: foreign_calls asked
  after the build again and the blamed branch removed (1, 2); dread.build build's vet and dread.overlay
  blame, link_blame and OverlayError.blamed are then unused.
- **Status.** Done (2026-10-25): dread.overlay blame, link_blame, OverlayError.blamed and the linker run
  with LC_ALL=C; dread.build build's vet; dread.lift check (the foreign calls before each link, blamed
  functions dropped); dread.arch.arm Arm.lifted_cflags, dread.target Compiler configuring the layer
  before it reads the layer's flags; tests/test_lift_link.py, tests/test_cclab.py.

## One name per static object: structures over what their base reaches, one name per address and width, signed where read signed (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the item "One name
per static object" (docs/todo.md).

- **Question.** One object of static data printed under several names: a structure ended where an
  address was handed out inside it, though its base reached further, so the rest printed as macros of
  their own (FreeRTOS's countsem.c xParameters, two parameter blocks, the second one's address handed
  to xTaskCreate: a structure of the first block's three words and three loose names, on 31 of the
  39 builds; newlib's __sf, stdin, stdout and stderr, handed out at 0x68 and 0xd0); a halfword read
  signed and stored had a name per signedness (prvSemaphoreTest's sNextCheckVariable++ read
  `g_200018dc_u16 = (u16)(g_200018dc_s16 + 1)`); a store of the other signedness into a field, and an
  access of another width at a field's place, kept a macro of their own. What extends a structure, which
  type is an address's one name, and how is an access of another type written?
- **Options and choices.**
  1. What a base's structure goes on over where the base reaches past it: (a) an address handed out
     inside it, (b) another base, (c) a data symbol (the item's wording: "a data symbol inside such a
     run a member, not an end"); a startup range, an array, a handed base (what callees reach) and the
     region's end still end it. **(a) and (b)**, measured on the 39 FreeRTOS builds (dread readable
     --statics --score, the structures and arrays against the debug information's variables): (a) alone
     let 38 structures go on over the rest of their source variable (xParameters on 31 builds, newlib's
     __sf on six, clang -O2's xQueueRegistry: 3608 -> 3865 fields, 3507 -> 3752 the source's, the rest
     the FILE word the score already counts wrong), none over several variables, none lost; (b) added
     one more (GCC 9 -O2's xAutoReloadTimers, reached through a base inside it); (c) made 43 structures
     over several source variables (clang's merged globals on RISC-V clang -Os, newlib's malloc state on
     17 ARM builds) and is left out: with an ELF's symbols a data symbol inside a run is another
     variable; a person's partial symbols, naming a field, would want it, which needs the target to say
     so (not built).
  2. Whether the base counts (separate_bases) is asked of its run up to the next start of any kind, as
     before; asked up to all it reaches, clang -O2's xQueueRegistry, which hands out its last entry's
     address, lost the neighbour that made it count and with it the structure (18 right fields).
  3. A structure whose fields recur at a stride, with addresses handed out inside it that recur at it,
     is an array of elements of that stride: xParameters two blocks of 12 bytes, __sf three FILEs of 104
     (one 312-byte structure otherwise). The stride: of the distances between two handed addresses (the
     base among them), smallest first, the first with a divisor that the structure is a whole number of
     and at which its fields recur, every element holding a field, its greatest such divisor: __sf hands out each FILE and its _mbstate
     (to memset), 12 bytes apart, where no element of 12 holds a field everywhere; clang -O2's
     xQueueRegistry hands out its tenth entry at 72 of 80 bytes: entries of 8, the source's. The
     greatest common divisor alone made __sf no array and xQueueRegistry an array of 72-byte entries
     over the next variables (one array over several variables on the 39 builds, now none).
  4. A place's type at a width: (a) unsigned unless only signed accesses (as before: a store, which the
     lifters write unsigned, made every stored field unsigned and each signed read a cast); (b) signed
     where an access is signed (a signed access is a load extending the sign, which a compiler makes of
     a signed place). **(b)**, for the fields of static data (dread.statics layout), of structures behind
     pointers (dread.shapes layout) and for scalars: FreeRTOS's queue locks (int8_t) read `r0->f_45`
     instead of `(s8)r0->f_45`.
  5. Scalars' one name: the plan lists, image-wide, the scalars outside structures and arrays that an
     access reads signed (the readable_types table's `signed`, from every function's machine code and the
     lifted functions' readable C); each address has one name at each width, signed where listed, a read
     of the other signedness cast to it, a store as it is. Per function it could not be one name: a
     function that only stores the counter would name it unsigned.
  6. Another type at a field's place: a store of the field's width goes into the field (the same bits:
     before, the macro it was); another width reads or writes through the field's address
     (`*(volatile u8 *)&g_20000030.f_4`), one access of its width at its address (before, a macro of its
     own, g_20000030_u8: a second name, which dread.cmetrics counts as a name, not a raw access).
- **Measured** (before -> after, behind the arrays and members of the entry below; docs/todo.md has the
  rest): the gate accepts the same functions on the eight builds, name by name; names of two
  signednesses at one address 1 -> 0 on FreeRTOS GCC -Os, clang -O2 and RISC-V GCC -Os; addresses with a
  macro of their own 242 -> 239, 272 -> 267, 214 -> 214, 252 -> 249 (GCC -O0); members of static data
  108 -> 115, 160 -> 171, 22 -> 22, 95 -> 102; casts 800 -> 785, 798 -> 796, 513 -> 500, 747 -> 739; on
  the 39 builds structures 547 -> 510, arrays 583 -> 621, none over several source variables, every
  array inside one with its source's element size, the source's structures and arrays grouped whole 329
  -> 368 and in part 89 -> 51. The plan and the readable C take as long as before.
- **Undo.** statics.REACH_PAST = () (and the evidence test with it); the periodic arrays' block in
  statics.plan; signed_type back to "unsigned unless only signed" in both layouts; an empty plan's
  `signed` (None) gives a macro per address and type; apply_globals' widths and stores back to the macro.
- **Status.** In the code (dread/statics.py, dread/naming.py, dread/shapes.py, small parts of
  dread/readable.py and dread/vartypes.py that hand the signed part on; tests/test_statics.py,
  tests/test_shapes.py).

## Arrays a pointer walks, members for derived pointers, a callee that reaches one field past its pointer (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the item "Arrays and
members for uniform runs and interior pointers" (docs/todo.md).

- **Question.** Static data that code fills through a pointer it steps printed as a structure of
  numbered fields with padding between (FreeRTOS GCC -Os's vStartTimerDemoTask: xAutoReloadTimers, 21
  handles, as `struct g_200017dc_t { u32 f_0; u8 pad_4[72]; u32 f_4c; u32 f_50; }`); a typed pointer
  plus the offset of a field printed as an integer sum (`(u32)r0 + 8` for &pxList->xListEnd), an access
  of another width at a field's place as a raw access; a structure handed at an offset to a callee
  that reaches one field of it stayed flat (xTimerCreate's `vListInitialiseItem((u32)r0_1 + 4)` for
  &pxNewTimer->xTimerListItem); and a variable that holds a stack argument read through its address
  (`r4 = *(volatile u32 *)&s2`) was never typed, so prvInitialiseNewTask wrote its task block's every
  field raw. What says that an array lies where a pointer walks, which derived pointers print as
  members, and what makes a held structure a member of its type?
- **Options and choices.**
  1. A walk's evidence: (a) the machine code's pass (each arch layer's value_step following a register
     stepped by a constant), (b) the lifted functions' readable C: a variable given one constant address
     and otherwise only stepped by one constant (dread.statics walks), its accesses taken as indexed
     ones at its start with its stride as the scale and its start as the base. **(b)**: no arch layer
     changes, as for the indexed accesses (2026-10-23). Strides of two bytes or more (a byte walk is a
     text or a buffer: texts keep their notes); a walk counts only where its start is a base that counts
     (separate_bases), tried with the end its structure would have (a walk alone, from a base the plan
     does not trust, made a one-element array that cut the structure there); the count stays what the
     code reaches at constant places. A walking variable handed to a callee gives the callee's reach in
     each element (statics.handed): pxReadyTasksLists' elements took vListInitialise's five fields.
  2. Derived pointers: (a) a typed variable plus the offset of a field is the field's address
     (`&p->f_8`, `&p->f_4.f_8` inside a held structure), in every use but against a constant (a
     member's address a compiler may take for never null: the sum stays, as a held structure's address
     does against 0); an access of another width at a field's place through the field's address
     (`*(volatile u8 *)&p->f_8`, one access of its width); (b) also variables that hold such addresses
     typed as pointers to the field's type. **(a)**: (b) is dread.vartypes' to decide by its cast trials.
  3. What makes a structure a member of its type: (a) a callee whose template reaches one field past
     its pointer and none in front of it takes one kind of structure, as one that reaches two fields
     did (shapes.structure: the call holds, links kinds, and a structure handed at an offset holds the
     callee's there); (b) a callee's parameter typed as a structure (dread.vartypes' prototypes, made
     after the plan) makes its callers' memory that structure. **(a)**: within the analysis, and one
     field at the pointer itself still says nothing (memset, memcpy, strcmp touch offset 0 of
     anything). vListInitialiseItem (one field at 16) now takes items: its prvInitialiseNewTask caller
     hands it the task block's items, so xTimerCreate's timer holds an item at 4.
  4. Typing a value read through a variable's address: `_uses` kept every variable an integer whose
     assignment's value had an address-of a variable of the function in it (for a_1 = (u32)&s1, whose
     typed reads clang -O2 took as outside s1). Now only one whose value may be that address: an
     address-of under a dereference is read through, its value the argument.
  5. dread.polish: an element's index is a number (vTaskSwitchContext's 31 - clz read 0x1fu once the
     element's address was no longer the sum 20 * r4 + ...).
- **Measured** (main -> after, the gate's readable functions; docs/todo.md has the rest): the gate
  accepts the same functions on the eight builds, name by name; raw accesses on FreeRTOS GCC -Os 78 ->
  61, clang -O2 105 -> 71, RISC-V GCC -Os 68 -> 67, GCC -O0 107 -> 90; members 711 -> 747, 689 -> 742,
  439 -> 452, 700 -> 737; casts 810 -> 800, 790 -> 798, 521 -> 513, 731 -> 747 (a variable typed now and
  read as an integer: (u32)r0); on the 39 builds' static data (--statics --score) structures 558 -> 547
  and arrays 571 -> 583, each new array with its source's element size, no structure or array over
  several variables (0 -> 0). Against the source (--score) no typed variable lost; duplicate structures
  became one on clang -O2 and RISC-V (12 -> 10 and 7 -> 5 beyond one per source structure), every held
  member where the source holds one. The plan and the readable C take as long as before (within half a
  second a build).
- **Undo.** statics.LEAST_STRIDE above every stride (walks find none); shapes.structure without its
  one-field clause; apply_types without field_address and the field's address for other widths;
  shapes._uses' handed() back to any address-of; polish without the Index clause; the arrays' trust
  back to the base's full reach (statics.plan own_end).
- **Status.** In the code (dread/statics.py, dread/shapes.py, dread/polish.py; tests/test_statics.py,
  tests/test_shapes.py, tests/test_polish.py).
## Readable C kept by what it is made of: the check's rounds make anew only what changed (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the item "A cheaper
round for results given back" (docs/todo.md).

- **Question.** Since void results, dread readable --check makes the readable_types table anew after a
  round that gave functions their results back (the table keeps each function's types for the digest of
  its C, and the first was made for the void forms), and with it every analysis tree and every
  function's readable C, which readable_texts kept by the whole context: FreeRTOS's clang -O2 gate went
  from 58 s to 144 s, GCC -O0's from 49 to 166 s, and a private firmware's grew too. How does such a round
  make only what changed, and still give, function for
  function, the C a check that keeps nothing gives?
- **Options.**
  1. The table after such a round: (a) made from trees made anew, as before; (b) made from the analysis
     trees kept by what each reads of what changes while a check runs (its lifted C, whether it is
     void), only the trees of the functions given their results back made anew, the analyses over all
     of them as before; (c) an incremental table, only the given functions' and their callers' entries
     made anew. The analyses are global (dread.shapes unifies across functions and numbers its
     structures by the first variable that points to one, dread.vartypes decides prototypes in a
     fixpoint over the call graph, pointer_params spreads over callers, naming.struct_names reads every
     function's typed variables): a change can travel past the callers, and an incremental result is the
     full one only where that was shown each time.
  2. A function's readable C: (a) kept by the whole context, as before (all made anew when the table
     changes); (b) kept by a key that names the table's entries it reads (an entry it reads that the
     key misses: C made for another table taken, silently, and the builds after the check make other C
     than the gate checked); (c) made from a view of the context that holds only what the key holds:
     the table's parts kept per function cut to the names its lifted C uses (its own entries, its
     callees'), the void functions among them, the parts that are no function's (structures, static
     data, the names made of the whole table) whole; the key is then by construction what the C is made
     of, and a view too narrow makes other C than the whole context, which a comparison shows.
  3. A caller of a function given its result back (its extern declaration changes): (a) made anew from
     its lifted C (the clean-up again: as long as the whole readable C, seconds for a large function);
     (b) made from its cleaned tree, kept from the table's making, the stages run with a view that holds
     of what changes only whether the function itself is void (what dread.tidy void_results reads).
  4. How it is shown: (a) the eight builds' gates compared with main's, outputs byte for byte; (b) and an
     option that makes every round's table and readable C anew from nothing beside what the check kept
     and stops at the first difference (dread readable --check --verify): run on the eight builds, in
     tests on gizmo, and there for a private target.
- **Choice.** (1b), (2c), (3b), (4b).
- **Why.** Before (main, eight workers, the machine shared): of a later round on FreeRTOS's clang -O2
  demo, 17 s went to the table (the clean-up of all 300 functions for their analysis trees 9 s, of the
  six callees outside the lifted table the masks look at 3.5-4.5 s, the analyses 4.5 s) and 7.5-8 s to the
  builds' readable C (every function's clean-up again), of about 30 s; now 3.7-5 s and 0.1-0.7 s (the
  analyses, the trees of the functions given back, readable C from cleaned trees). The gates (seconds,
  main -> this): clang -O2 148 -> 72, GCC -O0 172 -> 110, GCC -Os 93 -> 56, RISC-V GCC -Os 48 -> 30,
  gizmo a32 27.5 -> 16, m3 14 -> 4, m4 9.3 -> 3.7, rv32 6.4 -> 2.7 (docs/todo.md). (1c) would save the
  analyses too (now the larger part of such a round, 3.5 s on clang -O2), but its result is not the full
  one by construction; (1b) leaves the table exactly as a full remake makes it (--verify compares it,
  part by part, with the digests). (2c) cannot take C made for another table: what the view holds is
  what the key says, so a missing entry shows as a difference from the whole context (the test with a
  view cut to the function's own name finds one at once), never as stale C, and the builds after the
  check make the same C as the gate checked. (3b) made the rounds after the first nearly free: with the
  clean-up kept, a caller's C is the structure, the results and the tables (milliseconds); the first
  pass takes every function's from the trees its table was made from, which until then ran the clean-up
  a second time (FreeRTOS: 4.6-8.3 s a gate).
- **Not established.** That a function's readable C reads of the table's per-function parts only the
  entries of the identifiers of its lifted C, and that the stages read of the void functions only the
  function itself and nothing of the table: shown on the eight builds (every round, --verify, and the
  outputs byte for byte main's) and on gizmo in the tests, and by reading the code (dread.naming
  apply_globals: the pointers and takes of the callees it calls; dread.vartypes apply: its own entry and
  its externs' prototypes; dread.frames: its callees' keeps, returns and prototypes, its own whole;
  dread.polish: its callees' masks; dread.tidy void_results and readable's results: itself and its
  externs; naming.struct_names reads every function's typed variables, so the names are made of the
  whole table and shared); not proved for every target. A target where it fails gives other C than
  main's, the same in the gate and in the builds after it, and --verify there says so. The analyses over
  all trees still run in every such round; the first table now lifts every function in the check's
  process (main lifted them there after it), and keeps two pickles a function (about twice its lifted C
  each).
- **How to undo.** dread/readable.py: scoped and stage_view returning their context (or _SCOPE False),
  check calling make_plan and chooser without kept; readable_texts keyed by context_key again.
- **Status.** In the code (dread.readable scoped, scope_names, scope_key, plan_key, tables_key,
  stage_view, kept_trees, cleaned_tree, from_cleaned, kept_cleaned, readable_texts' kept and verify,
  remade, verify_plan, verify_framed, carry's verify, check's verify and its summary line; `dread
  readable --check --verify`; tests/test_readable.py, tests/test_carry.py). Figures in docs/todo.md.

## Pages of the tools' results: the bench, the compiler lab, the leak check (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), for the rest of step 4 of
"The GUI for everything" (docs/todo.md): pages for the tools that work beyond one target, each fed by its
tool's own output and running the tool as a job, never computing what the tool computes.

- **Question.** What feeds each page, where do its records live, when does a page start a job by itself,
  and how does a page about a private project's words keep them out of the browser and out of a repository?
- **Options and choices.**
  1. What feeds them: (a) the pages read metrics.json, results.json and the leak check's printed lines
     themselves, (b) a `--json FILE` for each tool, the same facts as its report as data. **(b)**, as the
     user asked: `dread bench report --json` (also on `measure` and `all`, which end with the report),
     `dread cclab report --json`, `dread leakcheck --json`. The cclab report is data first now
     (report.data, report.markdown): report.md and profile.toml came out byte for byte the same on a
     four-build run of GCC 16 before and after, and the tests' strings of report.md pass unchanged. The
     bench's markdown report stays as it was (byte for byte the same on the 39 FreeRTOS builds); the JSON is
     made beside it by report_data with the helpers the markdown uses (stops, compiler_cell, summary_line),
     and FIGURES says what one number of a measurement is (figures()), so the page needs no knowledge of
     metrics.json. Undo: drop the options; the pages then have nothing to read.
  2. The trend: (a) the page reads BUILD/history itself, (b) the report's JSON carries each build's earlier
     measurements as the same figures. **(b)**: one record, made by the tool that wrote the history; the
     time of each comes from its file's name as measure wrote it (copies that keep no times keep it). The
     table's trend column draws one figure (a choice of every numeric one) as a small line per build; a
     build's page draws it as a chart with a crosshair and its readout, and the same as a table (the chart
     never gates a value). Plain SVG, one series in the accent colour, no library.
  3. Where the records live: beside what the tool writes already: BENCH/RECIPE/report.json beside
     report.md, WORK/report.json in the cclab work directory (every adapter in one file, as report writes
     every adapter's report.md in one run); the leak check's in the user's state directory (below). The
     same command typed in a terminal shows on the page.
  4. When a job starts by itself: (a) only on a button, as the function views do for heavy tools, (b) the
     report by itself where its record is missing or stale (a file it reads is newer) and no job of the
     tool runs on it, a measurement or a compiler's run only on a button. **(b)**: a report reads files and
     takes a second or two; a measurement takes minutes a build (the lift check), so the page shows an
     estimate from the build's last measurement and takes no "all builds" (dread bench measures every build
     where none is named: hours on FreeRTOS's 39). A report that failed on the same files is not started
     again (its log says why; the button runs it). A read-only server starts nothing.
  5. Opening a build as the target: by the build's name under BENCH/RECIPE (never a path taken from the
     record), through the server's open_target as the Setup page does, with its refusals (a job running, a
     read-only server, one beyond this machine); the page names why it cannot (no dread.toml: only the
     build's figures are there).
  6. The compiler lab's work directory: a path typed into the page, taken as typed from where dread gui
     started (the Setup page's rule), remembered per viewer in the browser (a convenience: it is the
     user's own path, and nothing breaks without it). The run's form: an adapter (built-in, ce:ID,
     relink:DIR or a file), the builds of its matrix as a grid, one's own probes, --lift, --guard; "Check
     the adapter" runs dread cclab doctor. Every other option is the Run page's form ("Every option"). A
     result's builds are written by the matrix where they are exactly the builds of some values of one of
     its dimensions ("cpu cortex-m4f (4)", "all but cpu cortex-m4f (12)", whichever names fewer), the full
     list on hover: with GCC's 16 builds a list of twelve names hid the result it belonged to.
  7. The leak check's secrets: the server never opens the tables, words or allow list (it asks whether a
     typed path exists); only the check's record (the strings that matched, the paths as given) and its log
     (the tool's HIT lines) reach the page. Its jobs are the page's own (dread.gui Server.jobs_of_page, a
     Jobs in $XDG_STATE_HOME/dread/gui/leakcheck/jobs, made and closed with the server), not the target's:
     their command lines name the private files and their logs hold what matched, and a target can lie in
     a repository (gizmo's do, in DREAD's own; build/ is ignored there, but "never stored in DREAD's repo"
     should not rest on an ignore rule). The record is written with mode 600. Tested with canaries in the
     private files that are in no file of the tree: in no answer, page or log. dread leakcheck's printed
     lines are unchanged (the user's push script reads its exit status; a line number in the text would be
     a format change for nothing the JSON lacks). Its exit status 1 (a hit not allowed) shows as "hits not
     allowed", not as a failed job.
  8. The bar at a phone's width: three more buttons left the search a few pixels wide. app.css now shows
     the brand's mark alone below 560 px, keeps the search at 96 px at least and lets the buttons scroll
     sideways beside it (one row; the bar never wraps). Undo: the rule's block in app.css.
  9. Class prefixes: the first draft of the compiler lab used `.cl`, which is app.css's class of a line of
     C (its grid broke the page); the pages' prefixes (bn, lab, lk) were checked against every stylesheet.
- **Status.** In the code: dread/guipages/bench.py, cclab.py, leakcheck.py, dread/web/bench.js, cclab.js,
  leakcheck.js and their stylesheets, Server.jobs_of_page, the tools' --json (docs/formats.md). Tried in
  headless Chrome, light and dark, 1360 and 390 px wide: a copy of the 39 FreeRTOS builds' results (and one
  whole build, measured from the page), GCC 16's probes run from the compiler lab page, a leak check of a
  made-up project.

## A function's views: a trace, the code left unrun, the mutation check, the registers (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), for the first part of step 4
of "The GUI for everything" (docs/todo.md): the views a reader of one function needs, each fed by its
tool's own output and run as a job from the page, never computed by the page.

- **Question.** Where do the four views live, what feeds them, when is a record stale, and what do the
  tools have to give that they did not?
- **Options and choices.**
  1. Where: (a) pages of their own (#/trace/NAME, ...) that draw the listing and the C again, (b) part of
     the function's page through a few hooks in app.js. **(b)**: the views are about the listing and the C
     the page already draws (a trace's instruction marked there, a region's code, a survivor's line), and a
     second drawing of both would be app.js's code copied. app.js gains functionHook (show, c, leave), the
     address #/f/NAME/VIEW[/ARG], the strip's and the pane's places, and three exports; views.js and
     views.css are files of their own, and views.py a module of dread/guipages, as the other pages are.
     Undo: delete the three files and the hooks.
  2. Layout: the open view (a) above the listing, (b) beside it, (c) beside from 1600 px of window, above
     below that. **(c)**: beside, the listing and the C stay in sight while the view says where to look
     (the trace's instruction scrolled to and marked); below 1600 px a third column leaves the listing too
     narrow, so the view sits above it at most 64 % of the window high, and the listing is not scrolled to
     (the view would leave the screen). At phone width everything stacks; wide tables scroll sideways
     inside the view, the page never.
  3. What feeds them: (a) the tools write a record of their own by default (BUILD/trace, BUILD/mutate),
     (b) a `--json FILE` for each tool that lacks one (dread trace, dread mutate, dread regflow; dread
     uncovered and dread callconv had one), the page choosing the file (BUILD/gui/views/IMAGE/...). **(b)**,
     as the user asked ("add a --json to a tool where it lacks one"): no tool gains a side effect, a
     command typed in a terminal with the same file shows on the page too, and the JSON says what the
     printed report says. dread trace's printed report is made from its data now (TraceResult.data,
     report_text), so the two cannot drift; tests/test_trace.py's 33 tests passed unchanged on the text
     (one difference left on purpose: a frame of exactly 40 rows no longer ends in "...", which said
     rows were left out where none were).
  4. Which cases fail: dread test's tests.tsv had counts only. (a) parse a job's log, (b) a fifth column,
     `failing`, the labels of the failing cases as a JSON list (a written case's label may hold any
     character). **(b)**, a format change with its note in docs/formats.md; older files have four columns
     and their readers go on. The lift check's and the readable check's failing case is the FAIL line of
     the verdict, with the labels of the last round file that names the function's FAIL lines.
  5. The bits: regflow's analysis follows a register bit for bit since regflow 26 but kept only which
     registers. (a) run the analysis again in a job for the bits, (b) keep them in its result (`bits`,
     regflow 27: kept's and reads' fields where fewer than all bits count). **(b)**: the page's server
     already loads the analysis; the bits cost nothing to keep. RegFlow.case_facts leaves them out, so no
     generated case is made anew for them (any edit of regflow.py makes the cache anew anyway: its key
     holds the module's source; relations' and groups' caches follow it). Per call site the bits are not
     kept (sites' format is part of the cases' key); the call sites show which registers, always or on
     some paths.
  6. Stale: (a) digests of what a tool read, kept in its record, (b) the files' times against the
     record's. **(b)**: cheap and enough to offer the job again; the view names the newer files. Not seen:
     a change of DREAD's own code, a file's contents changed back to an older time.
  7. When a job runs: (a) only on a button, (b) a trace at once where a case was chosen on purpose (its
     chip, the trace view opened by its tab, or an address that names it) and its record is missing; the
     other views on a button. **(b)**: "one click from a failing case to its trace"; a trace is quick (a
     second on gizmo; on FreeRTOS's GCC -Os build a lifted trace took 53 s the first time, compiling the
     objects and the twin with -g, and 9 s after) where a mutation check is not, and walking from function to function with the view open starts nothing (each case at most
     once a page). A read-only server starts nothing.
  8. A job's exit status: dread trace's 1 is a case that parts and dread mutate's a survivor, which
     dread.jobs records as failed. The view shows a job's state while it runs, and after it only where it
     wrote no record (its log says why).
  9. What the tools do not say is not made up: dread mutate does not know which case comes closest to
     noticing a survivor (it runs each variant's cases to their verdict), so the view says why no case
     notices in the tool's words (no case's outcome changed; what the --kill search found); dread
     uncovered suggests no action, so the view shows its forced run's outcome with what its docstring
     says that outcome means ("the condition alone stands in the way", "the region needs more of its
     inputs").
- **Status.** In the code: dread/guipages/views.py, dread/web/views.js and views.css, app.js's hooks; the
  tools' --json (docs/formats.md), tests.tsv's failing column, regflow 27's bits. Tried in headless
  Chrome on a copy of gizmo with parse_uint written wrong on purpose and on a copy of FreeRTOS's GCC -Os
  build, light and dark, 1360, 1920 and 390 px wide.

## The lift check's retries: each function once, no fixed number of passes (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), found on the private
real-life test: a function dropped by a lift check agreed in a trace against the check's final list.
It had failed while a callee failed in the same round, so it was a suspect, but the retries go bottom
up, one layer of suspects a pass, and stopped at 8 passes (RETRY_PASSES) before its turn; the check
said nothing about it. Several of that firmware's checks had used all of them; of the 39 public builds'
last checks none had needed more than two, and none had tried a function twice.

- **Question.** How many passes may the retries take?
- **Options.** (a) A larger fixed number: the same silent cut on a deeper tree. (b) Each function tried
  again once, so the passes end by themselves (at most one a suspect), with a safety bound and the
  suspects it leaves reported. (c) Every suspect at once in a last pass: a caller tried with a callee
  that fails on its own fails through it again, which the bottom-up order exists to avoid.
- **Choice.** (b): dread.lift Retries; RETRY_PASSES is a bound of 256 now, and a check that reaches it
  prints "untried" with the suspects left. A function that fails again on its retry stays out (before,
  it could become a suspect again, which the fixed number had to stop).
- **Undo.** RETRY_PASSES back to 8, and Retries.failed without `- self.retried`.
- **Status.** In the code (tests/test_lift_retries.py).

## The pilot page: a short form of the pilot's own options, a run followed from its events, its job's stop (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), for step 3 of "The GUI for
everything" (docs/todo.md; the user, 2026-10-24: "... an easy way to set everything up and run and
monitor the pilot?").

- **Question.** How does the page start a pilot run, say beforehand what it may cost, follow it while it
  runs, stop it, and show the runs before, their cost and their comparison, without a second
  implementation of anything the pilot does?
- **Options and choices.**
  1. The form: (a) the Run page's form of dread pilot (three dozen options, the task's tied ones shown
     for it); (b) a form of the page's own, its fields written in the page; (c) a short form of the
     pilot's own fields (dread.cliforms describe of its parser): the task, provider, budget, all
     functions or the first N or some, Message Batches on or off, and the two to four options each task
     needs (SHORT). **(c)**, with the Run page's form one click away and filled in with these values: the
     Run page takes `#/run/NAME@["--x", ...]` (a JSON list of words, read back into its form through
     the parser, as `@N` reads job N's), a few lines of run.js. The help, defaults and checks are the
     parser's, and the command line is the Run page's own route, so the short form cannot drift from
     `dread pilot --help`. Undo: drop SHORT and link the Run page.
  2. The estimate: (a) build every question of the run and weigh each with the run's meter (exact input
     tokens; but a readable run's questions need every function's readable C, seconds to minutes, and a
     naming run's later questions depend on answers not given yet); (b) a fixed price a function; (c)
     the pilot's dry run's count of what it would ask (dread.pilot planned, which the dry run itself now
     prints from) times the tokens a function asked of an earlier run of the task on this target
     (dread.pilotlog estimate), priced at the chosen provider's prices, the earlier run's share of
     Message Batches at the batch rate where this one may batch, a run with the same settings that
     change a function's cost preferred (ESTIMATE_SETTINGS) and differing ones named. **(c)**, and no
     estimate where no such run exists (the page says why and points to a first run with a small
     --budget, which is what holds a run to a sum anyway). The dry run prints the same line, so the
     figure is the pilot's, not the page's. It is rough: the shared stages (groups, globals and
     structures) are spread over the functions of the earlier run, and a dry run of --task readable
     takes a function with an entry in readable_names for named (its readable C, made as the run begins,
     decides: an entry made for other C is asked again). Undo: the route and the dry run's line.
  3. Following a run: (a) the page reads the events file from its offset and folds it itself; (b) the
     server folds each followed run from the byte offset it has read (Follow: pilotlog.Run, the newest
     KEEP = 400 events worth a line, at most 16 runs kept, the least recently asked dropped) and answers
     the page with the state and the events since the page's offset. **(b)**: a poll a second reads only
     what was written since, and the answers (thousands on a long run) reach the page as counts in the
     state, not one by one; a first look gets the newest 400 events worth a line and says how many older
     ones it left out. A poll a second, as the Run page's log (decision "The GUI runs every command", 4).
  4. Stopping: the run's job (the page's job whose process is the run's: the same pid on this host, the
     run begun while the job ran; no new field in the events or the job's record), its stop (SIGTERM to
     its group), with a grace of 60 s (three times llm.STOP_WAIT: the batches' cancels, the questions
     being asked, the end event) before SIGKILL. A run started elsewhere (a terminal, another server)
     is followed and not stopped: the page says where to stop it. A run gone without an end (killed
     outright) may have left Message Batches billed at the provider: its page offers their cancel, a job
     of `dread llm batches --cancel`.
  5. The history: pilotlog.runs, with what each run settled. The end event and the index's end line now
     count the functions settled by result (`settled`, and the other subjects apart: `settled_other`;
     docs/formats.md), so that the list needs the index alone; an older run's events are counted once and
     kept by the file's size and time.
  6. The cost per month: dread.llm usage_summary for the month shown and for every month of the ledger,
     each provider with its share of answers from Message Batches, its shares of input tokens read from
     and written to the prompt cache, and what both saved against the standard prices (its tokens at
     price_in and price_out); priced with today's llm.toml, as `dread pilot --usage` prices, so a
     provider since taken out of llm.toml shows "no prices".
  7. Comparisons: what the pilot compares (function names, names.tsv of --task name: with the true names,
     --score; with another copy's names.tsv, --against with --grounded or --judge; two settings over
     replicate copies, --against ... --vs ... with --grounded), started as jobs of dread pilot, their
     result what the pilot prints and writes. Two runs picked in the history are explained instead: the
     pilot has no comparison for the other tasks' names (no truth to score variables against, no judge
     for them), and two naming runs of one target wrote one names.tsv, the later asking only what had
     no proposal, so they are one set of names; separate copies are compared with --against. The judge
     defaults to the start form's provider where it has prices, else the cheapest.
  8. Keys and providers: the page shows whether a provider's key is there and where a request would take
     it from (dread.llm key_status: $KEY_ENV in the server's environment, or a key file that passes its
     checks, else why not), never a key; setting providers and keys up is the Setup page's (a link). A
     server without a target (the Setup page alone) shows the cost per month here and starts nothing:
     the routes about a target's runs answer 409 with the way to one.
  9. The estimate is a POST with the page's token (it reads the image's tables in the server; another
     web page must not trigger it) that a read-only server answers: it changes nothing.
- **Found on the way.** A function's readable C on its page stayed as it was when a run wrote the
  readable_names table: the page kept the reading by its load's generation, which the names tables do
  not change; it is now kept by the names' too (app.js readingBody), so a readable run from the page
  shows its names on the function pages as they come (the overview and the list already reloaded).
- **Measured** (2026-10-24; a stripped copy of FreeRTOS GCC -Os, mps2, its readable table made by `dread
  readable --check`; Haiku 5.5; the page in headless Chrome, light and dark, 1360 and 390 px wide). A
  readable run of the first 80 functions, Message Batches from 20 questions, a budget of $0.03: to its end
  in 5 min 14 s, $0.0203, 85 answers, 80 of them from two batches (24 admitted first, the other 56 once
  those were answered), followed live (its stages, the batch out with its age, the spend at 28 %, the
  time left). After it the form's estimate for 12 more functions: $0.0053 one at a time, $0.0028 with
  batches. Those 12 with a budget of $0.003, one question at a time: "budget reached" after 27 s, 7
  named, $0.0032 (107 % of the budget: the questions being answered cost more than their estimates, as
  the meter allows); a function page left open showed its globals' new names 6 s after the run wrote
  them. A naming run of 10 functions stopped from the page while its batch of 9 questions was out:
  "stopped" 2 s after the click, the batch cancelled at the provider (`dread llm batches`: canceled 9, none
  billed); run again to its end, $0.0009; its names compared with the true names (--score, free) and with
  an older copy's names.tsv (--against --judge haiku, $0.0013). Spent on the trial: $0.0263 of Haiku 5.5
  (with the run after the rebase, below).
  Following a played run of 1,830 answers (4,028 events, 756 KB; Follow alone): the first look 18 ms (the
  newest 400 events worth a line, 80 KB of JSON), a poll with nothing new 0.1 ms, one with an event 0.03 ms.
- **Not established.** The run's job is found by its process on this host; the estimate's accuracy
  beyond the earlier run it takes (pilotlog.estimate's docstring); the page was tried with Haiku 5.5 on
  FreeRTOS only (above), in Chrome, most of it before the Setup page and its key files came onto main
  (rebased onto them since: its tests pass, and a naming run of three functions from the page, $0.0005,
  with the history and the costs, the copies' providers priced as haiku); not on macOS or in Firefox.
- **Status.** In the code (dread/guipages/pilot.py, dread/web/pilot.js, pilot.css; dread/pilot.py
  planned, estimate_line; dread/pilotlog.py estimate, settled, edge_event, the end's counts; run.js's
  handed-over words; app.js readingBody).

## Setting up: dread doctor, dread new, keys from a file, one server that opens the target it made (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), for step 2 of "The GUI for
everything" (docs/todo.md; the user, 2026-10-24: "... and also provides an easy way to set everything
up"). Step 1's principle holds: every action is a command first (usable over SSH and in scripts), the
page a form over it, so the page does nothing the command line cannot.

- **Question.** How does a person get from a fresh checkout to a target with its first analyses and a
  language model to ask, from the command line and from the page?
- **Options and choices.**
  1. The prerequisites: (a) a page that probes, (b) a command, `dread doctor`, whose findings the page
     shows (dread.doctor check(), the same code). **(b)**. It looks and changes nothing: each fix is the
     command for this system's package manager (apt, pacman, dnf, brew; detected from /etc/os-release, all
     four with --all) or pip, printed, never run (installing system software is the user's to do). The
     essentials, which decide the exit status: Python 3.11, unicorn 2.1.4 (pinned), capstone 5, and with
     a target its compiler, which has to take the target's cflags (a RISC-V GCC before 12 refuses
     `_zicsr`: the probe found it before a build would). A local model server is asked for /v1/models
     within two seconds; a server elsewhere is not contacted (a request to a cloud API from a check is
     not the check's business). The package names come from CI's install step and bench/toolchains
     where those name them; Fedora's bare-metal RISC-V GCC and the MSP430 GCC are said to be not known
     in the distributions rather than guessed. Undo: delete dread/doctor.py and its route.
  2. A new target: (a) an option of dread import-elf (`--new DIR`), (b) a command of its own, `dread new
     DIR IMAGE`. **(b)**: import-elf writes an existing target's tables from an ELF and stays that; a new
     target also comes from a raw image, which has no ELF, and needs a dread.toml before import-elf can
     run (its regions decide which sections are the image's and which a loader's). dread new calls
     import-elf for an ELF. DIR must be new, empty, or hold only original/ with the image (where the page
     puts an upload); an existing target is never touched, and a failure takes back what was made.
  3. The memory map of an ELF, which does not keep the linker script's MEMORY: the image's region from
     the load addresses of the sections near the entry (64 KiB apart at most; farther ones are a loader's,
     "load" copies, as QEMU's MPS2 ELF has), the RAM from the writable sections' addresses, clustered and
     reaching up to the initial stack pointer (an M-profile vector table's first word, else a stack symbol
     such as _estack or __stack_top), the peripheral ranges only where the architecture fixes them
     (Cortex-M's Peripheral and System regions, the MSP430's first 512 bytes; none for ARM's A profile,
     RISC-V and MIPS, whose devices then fault in the emulator until named), a scratch region and the
     sentinel at addresses nothing else uses (below the RAM, as gizmo's are). On all eight gizmo builds
     this gives gizmo's own architecture, image, entry, startup copies, functions and stack top. A 16-bit
     MSP430 whose flash begins right above its RAM (FreeRTOS's F449 demo: code from 0x1100, the vectors at
     0xffe0) leaves no unused addresses for the scratch: it then takes the erased flash between the code
     and the vectors, as the bench's target of that build does by hand, the vectors becoming a loader's
     bytes. Every guess carries a comment in dread.toml. Undo: the comments say what to replace.
  4. The room behind the image (limit): (a) none unless --limit, (b) the image's end rounded up to 64 KiB,
     (c) as much room again as the image takes, rounded up to 64 KiB; each marked a guess. **(c)**: without
     room, the lift check dropped two of three gizmo functions whose lifted C is larger than the original
     ("no room left for its C"), and the first analyses would report a lifter far worse than it is; (b),
     tried first, left FreeRTOS's 60 KB image 5 KB, where the bench's run (with the board's 4 MiB) had
     placed 16 KB of C behind it, and the trial's lift check dropped functions for room. The emulator
     maps the room; whether the device's flash reaches that far is not known, and nothing of DREAD claims
     an image runs on hardware. Undo: --limit, or the line.
  5. The compiler: the original's where this machine has one for the architecture, else the one it has
     (the lifted C is DREAD's own translation and compares by behaviour, so another compiler serves). The
     original's is the debug information's producer (GCC records its code-generation flags, clang none).
     Which producer: (a) the most frequent string, (b) the compile unit's that holds main (else the entry,
     else the most code), read with pyelftools. **(b)**: the linker keeps each string once, so (a) counts
     nothing, and a C library linked in has a producer of its own: on the FreeRTOS builds (a) took newlib's
     flags (no -mcpu) and, on the clang build, newlib's GCC for the application's compiler. Without
     pyelftools only a single producer is taken. A flag naming a path of the build machine (TI's
     -mdevices-csv-loc=/home/...) is left out. This part depends on the machine, which the docs say.
  6. The functions of an image without symbols: (a) dread ghidra where Ghidra is installed, (b) a finder
     of DREAD's own, (c) none. **(a), else (c)**: DREAD has no function finder of its own (its tables come
     from Ghidra or an ELF, docs/formats.md); writing one is a project of its own. Without Ghidra the
     first analyses are refused with what to do (install Ghidra, dread doctor says how; or an ELF with
     symbols). Undo: first_analyses.
  7. Keys: until now a key came from the environment alone (dread.llm: "the key comes from the
     environment, never from a file"). That rule changes: a provider may name a key_file. Why: dread gui's
     jobs inherit the server's environment, so a key only in the environment means starting the GUI with
     it set, which a page cannot do and a person forgets; and the user's own key already lives in a file
     that is read into the environment for each command, so a file is where keys are kept anyway. What
     keeps it safe, as ssh insists for its keys: the file is read when a request goes out (not cached),
     and refused unless it is a regular file of this user's, readable and writable by the user alone (mode
     600 or stricter), in a directory no other user may write to, and outside any git work tree (where a
     commit would take it along; ~/.config as a dotfiles repository is refused too). The environment comes
     first where key_env is set (a key for one run without touching the file). A key never goes into a
     page, a log, a job's record, the usage ledger or an error message: an HTTP error's body has the
     request's key replaced by [key] (a server that echoes its authorization header), and dread gui hides
     the key files' keys in its jobs' logs as it hides the environment's secrets. Undo: drop key_file from
     provider_key; the rule is back.
  8. Writing a key: `dread llm key NAME` reads it from standard input (without echo from a terminal),
     never from the command line (a command line is kept in shell histories and in a job's record). The
     page writes the key it was given in a password field with the same function in its own process, not
     as a job (a job's standard input is /dev/null, and a key handed to a job would sit in the server's
     memory until the job starts, then pass through a pipe for nothing). The page shows a key as set or
     not set, never back.
  9. Changing llm.toml: (a) parse, change and write the file anew, (b) edit its lines. **(b)**: the file is
     the user's, with comments and fields DREAD does not know; a rewrite loses them. A field's value is
     replaced where it stands, a new field goes below the provider's last one, a new provider to the end;
     the result must parse and read back as asked, with every other provider and key as before, or the
     file stays as it was. A provider written as an inline table or with dotted keys is refused (edit it by
     hand), and a new command provider too (its command line is written by hand).
  10. Opening the new target: (a) the page links to a server per target, started in a terminal; (b) one
     server switches to the target it made. **(b)**: one tab from a fresh checkout to the first analyses,
     which is what "easy" asks for; a server cannot start another server as a job (it runs until stopped).
     A server started where there is no target comes up with the Setup page alone, its jobs (dread new, a
     provider's test) in the user's state directory and run where it started. Switching is refused while
     any job of the server runs or waits (it would lose its server), on a read-only server and on one that
     listens beyond this machine; the old target's analyses are stopped and its jobs' lock given up, and
     the page reloads when /api/status names another target. Two targets at once: two servers. Undo: the
     open route and Server.open_target.
  11. Uploads: a route that takes a file's bytes (not JSON), refused beyond --upload-limit (64 MiB by
     default) before it is read, written only to DIR/original/NAME of a directory dread new would take,
     NAME reduced to a plain file name, DIR or its original/ refused where it is a symbolic link (the file
     would land elsewhere), no link followed when writing; the page then runs dread new on the file where
     it lies. Paths typed
     into the page are taken as typed, relative to the directory dread gui started in, and dread new runs
     there: no shell, no ~. (Relative to the target shown, as the Run page's jobs are, a second target made
     while the first was open landed inside the first one's directory: found in the browser trial.)
  12. The first analyses: chained jobs (step 1's `after`), the chain kept in BUILD/gui/chain.json so that a
     reload or another server shows it; each step's state from its job, its log's last line beside it.
- **Found on the way.** dread import-elf --write-startup walked the calls of a target whose dread.toml it
  had just given a startup list with the description read before: code run from RAM was not seen, and
  gizmo-a32 failed with "0x40000000 is outside image main" (gizmo's targets never showed it: their lists
  were there already). It reads the target again after writing. Not fixed: an empty free list does not
  take out a `free` line an older import wrote (gizmo-mips's dread.toml names a 16-byte gap that is 8
  bytes now; noted in docs/todo.md).
- **Status.** In the code: dread/doctor.py, dread/newtarget.py, dread/llmconfig.py, key_file in dread/llm.py,
  dread/guipages/setup.py with dread/web/setup.js and setup.css; tests/test_doctor.py, test_newtarget.py,
  test_llmconfig.py, test_gui_setup.py, test_gui_setup_browser.py.

## Merged arms and hoisted statements: arms of one code as one condition with ||, what every arm shares in front of or behind the if (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the item "Merged arms
and hoisted statements" (docs/todo.md).

- **Question.** Readable C writes the same code into several arms where the compiler had one block for
  them: dread.structure repeats a short way to a return at every jump to it (TAIL), so an else-if chain
  whose tests reached one block has that block in each arm (FreeRTOS's eTaskGetState: `else if (list
  == delayed) r = 2; else if (list == overflow) r = 2;` for the source's one test with ||), and
  dread.tidy's merge_ifs writes a statement GCC scheduled between two IT blocks into both arms of the
  one if (prvCheckExpectedTimeIsWithinAnAcceptableMargin: `r4 = r1 + 7;` in both, where the source
  computes the bound once); a compiler that duplicated a call into both arms (GCC -O0) leaves it in
  both. Which arms should become one, which statements should leave the arms, and where may that be
  done so that every way runs what it ran, in its order?
- **Options.** (a) Neighbouring arms of an else-if chain with the same code as one arm, their tests
  joined with || (the second evaluated only where the first fails, as before: any test may have an
  effect); an arm whose code is the last else's drops its test where the test has no effect; a run of
  ifs without else whose arms are the same code and never fall through as one if (`if (a) return x; if
  (b) return x;`). (b) Arms apart in a chain too (`if (a) X else if (b) Y else if (c) X`: c would be
  tested before b, which needs every test between without an effect). (c) What every arm of an if /
  else (or of a whole chain with a last else) begins with in front of it where no test has an effect
  and none reads what the statement writes, or the statement assigns a value without an effect of
  variables no test writes and no call can reach (an address taken nowhere); what every arm ends with
  behind it, always; (d) hoisting from the arms of an else if alone (the chain's first arms keep it:
  `else { if (b) X else Y; S }`, a level deeper). Where: (e) in dread.structure after the loops are
  refined, on every if; (f) also in dread.tidy, for its conditional blocks' ifs, once the clean-up
  has settled, and the clean-up again after it.
- **Choice: (a), (c), (e) and (f).** (b) changes the order of tests; (d) breaks a flat chain into a
  deeper else for one statement fewer (gizmo's read_line). (f) matters for IT blocks: merge_ifs writes
  the statement into both arms so that dead and fold can use it on each way, and once they have, it is
  moved back, where the clean-up can fold it into its one reader (`if (r1 + 7 < r0_1)`, the source's
  comparison); in dread.structure alone it would stay a variable. Code is compared as text without the
  instructions' comments (copies the compiler duplicated have other instructions); the statement kept
  stands for the others (cparse copy_of: their instructions join its absorbed, for the reading form's
  margin, and its copies, which dread.naming identities counts as the definitions they were: without
  that, a variable two copies at two instructions defined lost one definition, half of its identity,
  and its name did not carry, GCC -O0's prvSuspendedTaskTimerTestCallback). Nothing leaves arms that
  declare variables of their own.
- **Not done.** Arms apart in a chain stay apart; a statement every arm begins with that reads memory
  or calls stays where a test calls (FreeRTOS's interrupt tests load a handle in both arms of a test of
  a call's result: the load might see what the call wrote); a statement first in one arm and second in
  another (behind a statement it does not depend on) stays (gizmo rv32's script_run, case 1).
- **How to undo.** dread/structure.py: structure without the arms_pass call; dread/tidy.py: _tidy
  without the hoisting at its end; cparse copy_of back to absorb (and identities without copies).
- **Status.** In the code (tests/test_arms.py).

## Loops in their source shape: tests at the head as the condition, loops proved entered, steps folded into their accesses (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the item "Loops in
their source shape" (docs/todo.md).

- **Question.** dread.structure emits every loop as `for (;;)` and refined it into a while loop only
  where its one way out was a test at its head, into a do-while where the test was at its end. A loop
  whose source tests two things at its head (gizmo's strncpy, `while (n && *src)`: the load of `*src`
  comes between the tests) or leaves it by a return there (strncpy's second loop, memset, memcpy and
  checksum on gizmo's ARM builds: `for (;;) { if (p == end) return d; ... }`) stayed `for (;;)` with
  ifs that break or return; a loop a compiler rotated behind a test of its bound (`if (n == 0) return;
  p = s; do { ...; p++; } while (p != end);`, GCC -Os's counted loops) stayed a do-while; the steps of
  post-indexed accesses read as a copy and an add (`w = r0 + 1; r2 = *r0; r0 = w;`, `*r3 = r12; r3++;`).
  What should become a while or for loop and an access with its pointer's step, and where may each
  rewrite be made so that it keeps the C's meaning?
- **Options.** For the tests: (a) the run of `if (c) break;` at a loop's head as the while condition,
  `!a && !b` (each test evaluated only where those before it failed, as && does), other breaks staying
  in the body (they leave the while as they left the for), and a head test that leaves by a return, or
  by a break behind statements, as the condition with those statements behind the loop where the loop
  has no other way out that falls on; (b) also tests behind statements (`while (n && (c = *++s) != 0)`:
  assignments in conditions). For rotated loops: (c) a do-while is a while loop where its condition is
  proved to hold whenever control reaches it (a weakest precondition through the statements before it,
  the tests of the ifs around it and the head of a loop around it, then z3), a for loop where an init
  before it and its body's last statement, a step of a variable its condition reads, make it a counted
  one; (d) every do-while behind a test that skips it, by pattern; (e) leave do-whiles. For the steps:
  (f) in dread.tidy, a step made through a copy (`w = v + k; ...; v = w;`) as a step of v, where w's
  other reads see v's value and v's new value meets its others (a loop's pointer); (g) the step folded
  into its access in dread.tidy, before the types; (h) in dread.polish, after the types: `*p = x; p++;`
  as `*p++ = x;` and `x = p[1]; p++;` as `x = *++p;`, where nothing between reads p.
- **Choice: (a), (c), (f) and (h).** (b) would write assignments into conditions, which readable C has
  nowhere else (not tried). (c) is sound by construction (only conditions of variables without an
  effect: the while loop evaluates it once more; nothing in a function that keeps a label), and the
  gate checks it besides; (d) would convert loops whose test reads values other than those tested
  before (a pointer advanced in between). (g) hides the pointer from dread.vartypes, whose pointer
  groups are addresses that are a variable plus constants (`*(volatile u8 *)r3++` is none: the variable
  would stay u32 with casts), folds only byte steps (a u32's ++ adds one; a word pointer's step of four
  is one element only once vartypes typed it) and changes the digests and identities the readable_names
  entries are made for; (h) does neither (polish runs after both). (f) only where the copy's v meets
  another of v's values: a pre-indexed load's base used again behind it (`w_2 = r4 + 36` on clang -O2)
  became a variable of its own under webs, w under another name whose name did not carry. A for loop
  takes its init and step from statements: cparse's For remembers their instructions (init_addr,
  step_addr) and dread.naming identities counts the variable's definitions there, so names carry
  (without it the definitions moved to the loop's branch and a counter's name would not carry).
- **Not done.** A loop whose exit sits at its end behind a return (`for (;;) { ...; if (n == 0) return
  d; }`, none without another break on the eight builds) stays `for (;;)`; a loop entered only where
  something proved about a loop before it holds (gizmo's fmt_dec, whose second loop's count the first
  made at least 1) stays a do-while; an index's step (`x = a[i]; i++;`, a counter's) is not folded; an
  if that only guards a while loop on its own test (`if (n != 0) { while (n != 0) ... }`, three functions
  of the eight builds: RISC-V's newlib memcpy and memset, gizmo rv32's memcmp) keeps its test; two steps
  of one pointer in one round become an offset and one step (`*(r0 + 2)`, `r0 += 3`: webs splits the
  pointer between them).
- **How to undo.** dread/structure.py: refine_loops back to its single leading break (leading_breaks and
  leading_exit gone), structure without the entered and counted calls; dread/tidy.py: ("steps", steps)
  out of PASSES; dread/polish.py: polish without the fold_steps call; dread/naming.py: identities without
  the For case (and cparse For's init_addr, step_addr).
- **Status.** In the code (tests/test_loops.py; tests/test_polish.py's and tests/test_vartypes.py's
  expected lines of strncpy and fill).

## A pilot run's events: a JSON-lines file per run in the target's build directory, an index beside it (2026-10-24)

**Decided by recommendation**, on the user's request of 2026-10-24 ("Can the GUI be extended to support
all of DREADs features and also provides an easy way to set everything up and run and monitor the
pilot?"): the pilot's side, so that a run can be followed live, from the GUI and from the command line.

- **Question.** Where and in what form does a run say what it does, as it does it: its stages, the
  questions planned and answered, the batches out, each answer with its cost, the spend, how it ended?
- **Options.** (a) The lines it prints, parsed by whoever watches (they are for people, change with every
  wording, and are lost unless someone captured them). (b) A database per target (SQLite: concurrency
  handled, but a schema to migrate for every new field, a binary file, a dependency of every reader).
  (c) JSON lines, one file per run, appended as things happen, and a small index of the runs. (d) The
  usage ledger extended (one file for every run of every target on the machine, no run's structure).
- **Choice: (c).** `BUILD/pilot/runs/ID/events.jsonl` and `BUILD/pilot/runs/index.jsonl` (docs/formats.md;
  dread/pilotlog.py writes and reads them). A run's id is its start time, its task and four hex digits,
  its directory made with an exclusive mkdir. Each event is one line, written with one `write()` to the
  file opened for appending: a reader polling by byte offset (`pilotlog.events(build, run, offset)`, what
  the GUI's page calls) sees whole lines, the threads of the run share it through a lock, runs of two
  targets never share a file, and runs of one target share only the index, appended under `flock`. New
  fields and kinds need no migration: readers ignore what they do not know (the start event's `format`
  says when that would not do). The provider's side (each answer, round, batch, the budget) comes from
  dread.llm's run meter (Meter.emit), the pilot's side (stages, what an answer gave, each function
  settled) from pilotlog.note, both into the same file. The build directory is the place: generated,
  per target, not committed; the runs of all of a target's images in one list (the image is in each
  start event).
- **Measured** (FreeRTOS GCC -Os, Haiku 5.5; the runs below, "Runs within a budget"): 230-275 bytes an
  event; a readable run (364 answers) wrote 1,076 events, 294 KB, a name run of 40 functions (345
  answers) 782 events, 181 KB (a whole name run, about 3,300 answers, would write about 1.7 MB). The writer
  takes about 23 µs an event alone (an answer and what the pilot made of it, two events, 45 µs). A name
  run of all 366 functions with a played provider (1,830 answers, five samples, no network), five times
  with events and five without, alternately: 18.6-20.6 s against 16.0-21.7 s, the paired differences
  -1.7 to +4.6 s (mean +1.0, s.d. 2.4): nothing measurable beside the machine's own spread (load 16;
  the events of such a run, about 4,000, take 0.1-0.2 s to write).
- **Not established.** Anything after a process was killed outright (no end event: `runs()` calls the run
  `gone` once its pid is no more on this host, the pid checked against the process's start time where
  Linux's /proc tells it; a run of another host stays `running`); other systems than Linux (flock and
  /proc are used where they exist).
- **Undo.** The run wrapper (dread.pilot `logged`) is one function; without it nothing is written and
  nothing else changes.
- **Status.** In the code (dread/pilotlog.py; dread/pilot.py logged, heard, the stage and settled events;
  dread/llm.py Meter.emit, ask_batch's and converse_all's events; dread/namejudge.py heard).

## Runs within a budget: every question weighed before it is asked, groups whole, a batch's rest weighed again (2026-10-24)

**Decided by recommendation** (the same request): `dread pilot --budget DOLLARS`.

- **Question.** How does a run keep within a budget, when what an answer costs is known only after it?
  And what does it do with the rest?
- **Options for the estimate.** (a) The worst case (max_tokens out: 16,000 tokens for Haiku 5.5 where a name
  answer takes about 470): a run would stop at a small part of its budget. (b) The input from the
  question's text (CHARS_PER_TOKEN) and the output from the answers of the same kind so far in the run,
  else from earlier runs, else a stated default. (c) A fixed price a question (the ledger's mean).
- **Choice: (b)**, by dread.llm's run meter (Meter): a question's kind is its model, whether it is asked
  without reasoning and its instructions. Its output comes from the last OUT_WINDOW (50) answers of its
  kind: the larger of their mean and their tokens out a token in times the question's input (a constant
  answer's length and one that grows with the question both come out at or above the truth); else from
  the earlier runs' answers (their end events' outputs, pilotlog.prior_outputs); else OUT_TOKENS (4,000:
  about the longest answers measured, the globals' and structures' of a readable run, 3,818 and 3,948
  tokens on FreeRTOS; an answer longer than its estimate can take the spend past the budget, a shorter
  one only makes the run weigh again). Its input comes from its characters at the characters a token its
  kind's answers took (the provider's input tokens against the questions' characters), else
  CHARS_PER_TOKEN (3). It is priced as the usage ledger prices (answer_cost: the batch rate in a batch),
  no prompt cache assumed. The spend is the run's own answers at those prices (Meter.answered, from
  Provider.record): what this run spent, whatever runs beside it.
- **Where it is weighed.** Every question, in Provider.ask (the kinds implement _ask): the guarantee,
  whatever code asks; a batch's questions in ask_batch, in their order while they fit; what does not fit
  is weighed again once those that fit are answered (the estimates know the answers' lengths then), and
  only a batch of which nothing fits marks the budget reached. The pilot weighs what belongs together
  before it begins it, so that it is asked whole: a function's samples (one_pass begins a component of
  the call graph only when a thread is free, with a budget, so that it is weighed with the spend so
  far; a wave of batches goes in parts that fit), a group's samples, the device question's. Once the
  budget is reached the pilot begins nothing new, asks the questions of what it has begun, and the run
  ends with "budget reached". A question of a group begun that the meter holds back after all (its
  estimate grew while the group was asked: answers that wrote the prompt cache cost more than their
  estimates, the characters a token learned) counts as a failed question: the vote takes the answers
  there are, so that every answer paid for is kept (a first version dropped such a vote with its
  answers; on FreeRTOS that lost four paid answers of a group). A function or group none of whose
  questions was asked has no row, no conversation and no line of results.tsv: a run started again
  asks it. The tables are written as for a run that ends normally.
- **Measured** on copies of FreeRTOS GCC -Os (mps2), Haiku 5.5, the defaults (`--batch 100`, the cache,
  five samples, two passes), `--no-device --jobs 16`:

      run                                   cost     answers  how                 what was named
      name, 40 functions (whole)            $0.0585  345      done, 6.5 min       29 groups, 40 functions
        --budget 0.029, first run           $0.0262  69       budget reached      14 groups (in parts, one at a time)
        --budget 0.029, an earlier run's    $0.0277  100      budget reached      20 groups
          index copied (its estimates)
      readable, 317 functions (whole)       $0.1062  364      done, 5.3 min       317 functions
        --budget 0.05, first version        $0.0577  162      budget reached      135 functions: 15 % over
        --budget 0.05                       $0.0498  124      budget reached      104 functions (batches of 19, 90)

  The first version's readable run passed its budget: its variables' second batch (130 questions) was
  weighed at the mean answer of the first batch, the 18 smallest functions (370 tokens out, 538 in),
  where its own took 589 and 856: 1.46 times its estimate. An answer's output is now the larger of its
  kind's mean and its kind's tokens out a token in times the question's input. With the first default
  of 1,000 tokens out, the globals' and structures' questions of a first run (all asked at once) cost
  3.0 and 3.5 times their estimates; with 4,000, 1.0 and 0.87 times. Estimates against costs otherwise:
  the groups' questions 1.9-2.6 times their cost (most of their input read from the prompt cache, which
  the estimate does not assume), a first batch of a kind not seen before about twice. A name run stopped
  by SIGTERM five seconds after its groups' batch was submitted: exit 143, the batch cancelled (none of
  its 145 questions answered or billed), its end event "stopped" (ended by SIGTERM), `--status` showing
  it, the grouping written and no proposal. Spent on the measurements: $0.33 of Haiku 5.5.
- **Not established.** The estimate's accuracy on other models, compilers and tasks (measured on Haiku 5.5
  and FreeRTOS only); a run can pass its budget by what its admitted questions cost beyond their
  estimates (an answer that reasons longer, a cache write, a question asked again); `--task review`
  is refused with a budget, since dread.namereview keeps a challenge whose questions were held back as
  one without a candidate (and then does not challenge that name again for the same reasons).
- **Undo.** Leave out `--budget`: the meter still counts, nothing is held back.
- **Status.** In the code (dread/llm.py Meter, Ticket, metered, BudgetReached, OUT_TOKENS, ask_batch's
  rounds; dread/pilot.py --budget, logged, Tally, fitting, the schedulers' weighing).

## The run's progress: `dread pilot --status [ID] [--follow]` and `--list`, the reading in dread.pilotlog (2026-10-24)

**Decided by recommendation** (the same request).

- **Question.** How does one look at a run from the command line: options of `dread pilot`, a subcommand
  of it, or a command of its own?
- **Options.** (a) `dread pilot --status [ID]`, `--follow`, `--list`, as `--usage` and `--dry-run` are
  modes of dread pilot that ask nothing. (b) `dread pilot status ...`: dread pilot takes function names as
  its positional arguments, and a subcommand would be one more word a function could be named. (c) A
  command `dread runs`: one more command for one tool's files.
- **Choice: (a).** The target is the pilot's (`--target`), the run the latest without an id. `--status`
  prints the run's state (pilotlog.state: the stage, the questions answered of those planned so far,
  the batches waiting and their age and progress, the spend against the budget, the rate, an estimate
  of the time left, the last answers, the warnings); `--follow` prints it, then a line for each stage,
  round, batch, function settled and warning as the events come, a progress line every 30 s, and the
  state again when the run ends (or says so when its process is gone without an end); `--list` lists
  the runs with how each ended and what it cost. The reading is a module of its own (dread/pilotlog.py:
  runs, state, events, status_lines, follow) that the GUI calls.
- **Not established.** The time left is the questions planned so far over the rate since the first stage
  began: rough while a Message Batch is out (its answers come at once), and a later pass of `--task
  name` plans its questions only when it begins (its stage says "at most").
- **Undo.** The options are a few lines of pilot_main; the module stays for the GUI.
- **Status.** In the code (dread/pilot.py status_main; dread/pilotlog.py).

## The GUI runs every command: jobs, forms from the commands' parsers, pages as modules (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), for step 1 of "The GUI for
everything" (docs/todo.md; the user, 2026-10-24: "Can the GUI be extended to support all of DREADs
features and also provides an easy way to set everything up and run and monitor the pilot?").

- **Question.** How does dread gui run DREAD's commands: where does a command's form come from, how is
  a job run, followed and stopped, what happens to jobs when the server stops, and how is the page
  split so that three agents can add pages at once?
- **Options and choices.**
  1. A command's parser without running the command: (a) a `parser()` function in every command's
     module, main building its parser with it; (b) main called with ArgumentParser's parse methods
     stopped at their first call, the parser it built handed over (dread.cli parser); (c) a table of
     each command's options in the GUI. **(b)**: main stays the one place a command's options are
     written, so the form cannot drift from --help, and no command module changes ((a) touches 35
     modules, two of them being changed by other agents now). The price: a main must build its parser
     first and parse before any work. Every one does; tests/test_cliforms.py reads each command's
     parser in an empty directory with an empty home and fails on anything written or printed, and
     CLAUDE.md's conventions say so. The stop is for the calling thread only (other threads parse as
     usual) and argparse's methods are put back afterwards. Undo: a `parser()` per module and dread.cli
     parser calling it.
  2. The form and the command line: generic over argparse's actions (dread.cliforms); the page sends
     the form's values and the server makes the command line, for the preview as for the job, with
     the same code; options of one value first, then the positionals, then options of several values
     (which would take the positionals for theirs); a list as shell words. Subcommands (dread llm,
     dread cclab) are nested forms. A mode that argparse does not know as one (dread bench's action,
     dread pilot's --task): (a) real subparsers in those commands, (b) a table per command in the GUI,
     (c) the convention the help texts already follow ("--task name: ...", "toolchain: ..."), the
     options it ties to a choice shown for that choice only, a switch showing them all. **(c)**: (a)
     changes their command lines, (b) is a second description to drift. An option whose help does not
     follow the convention is always shown. Undo: describe() without condition().
  3. A job: `sys.executable -m dread.cli COMMAND ARGS...` (dread.jobs command) in the target's directory
     with `--target .` where the form leaves it empty (what a person types there), PYTHONPATH with this
     DREAD in front (a worktree's server runs the worktree's code, as bin/dread does), a session of its
     own (its process group), stdout and stderr into one log. Polite: applied by the server to the new
     process right after it starts, and DREAD_POLITE=1 so that the tools' own polite() does not repeat
     it; not in the child before exec (Popen's preexec_fn is unsafe in a threaded server, which this
     one is). Its first milliseconds run at the server's priority.
  4. Following a log: (a) server-sent events, (b) the page polling from a byte offset. **(b)**: the
     threading server holds a thread per open connection, and a stream left open would hold one that
     the server's close and the tests (no thread left behind) would have to cut; a poll a second costs
     a short request where nothing happened. While a job runs a piece holds whole lines only, so that a
     secret is never split between two pieces past the redaction.
  5. Stopping: SIGTERM to the group, SIGKILL after GRACE (30 s, as long as dread pilot needs to cancel
     its batches; start_command takes a longer grace). When the server stops: (a) its jobs run on, (b)
     they are stopped the same way. **(b)**: the jobs share the target's files with the next server's,
     and a job nobody follows writes tables nobody watches; a run that should outlive the page belongs
     in a terminal. A server that ends without closing (killed, crashed) leaves its jobs running in
     their sessions: the next server follows such a job again (Linux: the same pid with the same start
     time since the same boot) and can stop it, else it is marked lost. SIGTERM and SIGHUP of `dread
     gui` close it as Ctrl-C does. Undo: Server.server_close without jobs.close().
  6. One server per target runs jobs: an advisory lock on BUILD/gui/jobs/lock; a second server, and
     `dread gui --read-only` (no change at all), read the records and start nothing.
  7. Secrets: the environment reaches the jobs (keys come from it) and is never kept or shown; the
     values of secret-looking variables (KEY, TOKEN, SECRET, PASSWORD, ...) are replaced by their names
     in the log text served and in a record's command line (the words run are the real ones).
  8. Pages as modules: dread/guipages, its modules imported at start (pkgutil, in the order of their
     names), routes registered by a decorator, each page's script and stylesheet served from dread/web;
     app.js exports its helpers, imports the pages' scripts (import(): the page's Content-Security-Policy
     allows its own origin) and routes to what they register (address, show, leave, status, keys, help,
     a button in the bar). The pages of browsing stay where they are; app.js gained the router hooks.
  9. A browser test: Playwright as an optional extra (`browser`) for tests/test_gui_browser.py, which
     drives the Run page in headless Chrome where both are installed. CI installs neither, so it is
     skipped there; the API is tested without a browser (tests/test_gui_run.py).
  10. A server listening beyond this machine (`--host 0.0.0.0`): (a) jobs as on 127.0.0.1, (b) no jobs.
     **(b)**: the page carries its token, so whoever can load the page can start a command, any of
     DREAD's with any arguments, as the user who runs the server; the token keeps other web pages out,
     not other machines. Renames and notes there are as before. For another machine, ssh -L. Undo: the
     `local` test in dread.gui serve.
- **Status.** In the code (branch gui-jobs).

## The naming runs' cost (1): rounds of 100 questions or more as Message Batches, by default (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), on the request to make the
pilot's runs cheaper without making the names worse (the month to 2026-10-24 by the usage ledger: Haiku
5.5 243,329 answers, 343 M tokens in, 99 M out, $83.91, about $50 of it output; Sonnet 5.5 $43.66).

- **Question.** Anthropic's Message Batches cost half (input and output), and a batch is answered within
  minutes to an hour (24 at most) where a question takes seconds. The naming questions come in stages:
  the groups', the globals' and structures' batches, the variables of `--task readable` and the
  grounded judge's questions are independent within a stage; `--task name` asks a function only once its
  callees (callers, on even passes) are named, 23 levels deep on FreeRTOS GCC -Os. Batches where, and by
  default?
- **Options.** (a) One question at a time, as before. (b) An option only. (c) Every stage as batches.
  (d) By default, a round of N questions or more as a batch, smaller rounds one at a time as before;
  `--task name`'s passes in waves (every function whose dependencies are named, asked at once, settled
  before the next wave) when a level of the call graph has N questions or more.
- **Choice: (d) with N = 100** (`--batch N`, BATCH_MIN; `--batch 0` asks one at a time). Every question is
  a conversation (a generator) that decides what an answer means, the same code whether its question
  goes alone or in a batch (dread.llm converse, converse_all); answers come back by custom_id; what asks
  again (a name that cannot be used, names the checks refuse, verdicts missing) makes the next round. A
  batch is looked at every 10 s and cancelled after 2 hours (batch_poll, batch_wait), its unanswered
  questions failing with an error that says so; an interrupted run, SIGTERM and SIGHUP too, cancels the
  batches it waits for (`dread llm batches --cancel` for one killed outright). Each answer of a batch is
  a line of the usage ledger with mode `batch`, priced at half (docs/formats.md).
- **Measured** on FreeRTOS GCC -Os (mps2): `--task name` on a copy of the bench's stripped build (366
  functions FUN_), `--task readable` on a copy of the named build after `dread readable --check` (317
  readable functions), Haiku 5.5, the defaults (`--samples 5 --passes 2 --subsystems`), `--no-device
  --jobs 16`, one run each:

      run                          answers  tokens in  out     cost   time    word scores (same words, half the words, overlap, style)
      name, one at a time (main)   3,345    4.26 M     1.30 M  $1.07    648 s  75, 132, 0.39, 0.47
      name, --batch 100            3,340    4.25 M     1.28 M  $0.69  1,858 s  78, 155, 0.42, 0.49
      readable, one at a time      347      0.36 M     0.25 M  $0.16    167 s  parameters 198 of 374 as the source
      readable, --batch 100        363      0.37 M     0.26 M  $0.11    235 s  191 of 369

  The name run's ten batches carried 2,310 of its 3,340 answers (the groups' 145 questions, levels of
  130 to 410), each answered in 125-208 s; the rounds below 100 went one at a time (69 % of the answers
  batched: -35 %). The readable run's variables went as one batch of 317 (156 s); its globals' and
  structures' 15 questions and the 46 asked again one at a time (-31 %). The names are those of the same
  questions asked one at a time (a provider's sampling): the word scores within the noise of runs (five
  runs of one setting below: the same words 70-81), the readable scores too (three runs 52-53 %); judged
  against the source by Sonnet 5.5 (the replicate method, next entries), the run one at a time 0.785,
  the run with batches 0.783, the five with batches and the cache 0.784 +- 0.018 (0.761-0.807).
- **Why.** The answers are the model's to the same questions; only the price and the waiting change.
  Half the price of the large rounds is most of a run's cost on a large image, where rounds are wide,
  and a round below 100 questions (a handful of functions, a review's candidates) is asked as quickly as
  before. The price is time: on FreeRTOS `--task name` took 2.9 times as long (31 minutes), `--task
  readable` 1.4 times; most runs are unattended (the bench, replicate comparisons), where money is the
  limit. On a large image a batch of thousands of questions should not take longer than asking them 16
  at a time (about three answers a second here) takes; not measured.
- **Not established.** Batches on a large image (their latency with thousands of questions, the run's
  time); how long batches take at other hours (here, a Saturday afternoon with up to ten runs at a time,
  117 Haiku batches took 114-280 s, median 186 s; Sonnet's three for the judge 364-734 s); the readable
  task's globals' and structures' stages as batches (15 questions: one at a time by the threshold).
- **Undo.** `--batch 0`, or BATCH_MIN = 0; a provider's `batch = false` in llm.toml.
- **Status.** In the code (dread/llm.py ask_batch, converse, converse_all, Providers, ending_on_signals,
  the providers' batch_* calls, `dread llm batches`; dread/pilot.py the conversations name_talk,
  subsystem_talk, readable_talk, one_talk, batching, one_pass_waves, --batch; dread/namejudge.py judge_talk
  and judge's batch; docs/formats.md the ledger's columns).

## The naming runs' cost (2): the prompt cache for what several questions share, where llm.toml gives the model's minimum (2026-10-24)

**Decided by recommendation** (the same request).

- **Question.** Anthropic keeps a prompt's beginning for questions that begin alike: read for a tenth of
  the input price (Haiku 5.5; Sonnet 5.5 and Opus 5.5 0.05), written for a quarter more (for an hour:
  twice), a prompt of at least 512 tokens on the 5.5 models, available only once the answer that writes
  it has begun. What do the pilot's questions share, and where does marking it pay?
- **What is shared.** The instructions (NAME_SYSTEM and its kin, 200-340 tokens) and the device line are
  shorter than the minimum alone, and the group's line a function's question shows (about 80 tokens) adds
  too little to reach it for the questions of one group's functions. What reaches it: a function's
  samples (five by default) share the device line and its code, the bulk of the question, and differ only
  in the callers' order after it (Namer.question_parts: the code ends the shared part); a group's samples
  are one question asked five times; the device question likewise; the grounded judge's runs share a
  source batch's sources (marked since 2026-10-22). A readable question is asked once (again only when
  the checks refuse its answer), a globals' or structures' question shares only its instructions: not
  marked (a mark written and never read costs a quarter more).
- **Choice.** A provider whose configuration gives cache_min (the model's least cacheable prompt) says
  that it caches (Provider.cacheable); the pilot then marks the shared part of questions at least two of
  which begin alike, where the prompt up to its end reaches the minimum (estimated at 3 characters a
  token: the naming questions took 2.4), and orders them for it: one at a time the first sample goes
  first and the others once it has ended; in a batch the first samples come first (the cache is the
  API's best effort there); the judge in batches asks every source batch's first run first, in a round
  of its own, its sources kept for an hour. Without cache_min nothing new is marked and nothing waits.
  The parts go to the API with the blank line content_text joins them with (the API puts a message's
  blocks together as they are, measured with its token count), so a question split for the cache is the
  same text, the answer cache's keys unchanged.
- **Measured** (the first entry's setting, `--batch 100`, cache_min 512): the name run A1 read 1.67 M of its
  4.25 M tokens in from the cache and wrote 1.06 M, $0.61 against $0.69 without marks (-12 % of the run,
  -31 % of its input cost): questions asked one at a time 0.56 M read and 0.14 M written of 1.17 M (-40 %
  of their input cost), questions of batches 1.11 M read and 0.92 M written of 3.09 M (-25 %); the groups'
  batch alone (145 questions, the samples of one question) 0.45 M read and 0.39 M written of 0.84 M,
  where every sample after the first reading would have written 0.17 M. Four runs beside it (A2-A5) read
  2.2-2.3 M: concurrent runs of one build ask the same questions (no names yet in the groups' and the
  first level's) and read each other's cache, $0.57-0.59.
- **Why.** The samples are where a run's input repeats itself; ordered for the cache, a function's code is
  paid about one and a half times instead of five. The model's minimum is a fact of its price list:
  from the configuration, not a constant in the code (Haiku 4.5's is 4,096).
- **Not established.** The cache one at a time in a whole run without batches (only the rounds below 100
  of these runs, which went so); the best effort in a batch elsewhere (here about two thirds of the reads
  it could have had); the estimate of tokens on code of other compilers and architectures.
- **Undo.** Remove cache_min from llm.toml (no marks, no ordering).
- **Status.** In the code (dread/llm.py Anthropic.blocks, cacheable, CHARS_PER_TOKEN, the hour's price
  CACHE_WRITE_1H, price_cache_write_1h; dread/pilot.py cache_parts, shared_parts, Namer.question_parts,
  name_subsystems, one_pass's held questions, device_task; dread/namejudge.py). The user's llm.toml needs
  `cache_min = 512` in the Haiku 5.5, Sonnet 5.5, Opus 5.5 and Fable 5.1 providers, and `price_cache_read`
  0.10 and 0.20 for Sonnet 5.5 and Opus 5.5 (their reads are 0.05 of the input price; the code's default
  is Haiku's 0.1).

## The naming runs' cost (3): the model's hidden reasoning stays; answering without it an option (2026-10-24)

**Decided by recommendation** (the same request: shorter answers, never at the cost of what the names
say).

- **Question.** What do the answers hold beyond what the pilot uses? A name answer is two lines (about
  100 tokens: the name, a sentence), a readable answer a ```json block (the names, a sentence), a group's
  three lines. The sentences are read: names.tsv's what, the GUI, the review, the grounded judge's second
  verdict; readable C's comment above each function. But Haiku 5.5 reasons before it answers (adaptive
  thinking by default, effort medium), returns none of that text (its display is omitted) and bills it
  as output: 375 of 474 tokens out on a FreeRTOS name question; output was 60 % of a name run's cost and
  78 % of a readable run's. Ask without it?
- **Options.** (a) As before. (b) The naming questions without thinking (Haiku 5.5 accepts thinking
  disabled at effort high or below; Sonnet 5.5 needs between_tools; Opus 5.5 cannot turn it off). (c) A
  lower effort (low: 406 tokens out on the same question, hardly fewer). (d) The sentence asked of one
  sample of a vote only.
- **Measured** with the replicate method (2026-10-23): five name runs a side, side by side from one
  commit, batches and the cache on (`--batch 100`, cache_min 512), the second side with `--no-thinking`
  (llm.toml's quick fields: thinking disabled), each judged once against the source by Sonnet 5.5,
  sentences apart: the runs' means 0.784 +- 0.018 (0.761-0.807; right 212-232 of 332, wrong 27-39)
  against 0.645 +- 0.009 (0.631-0.652; right 148-159, wrong 53-64), +0.139, permutation test over runs p
  0.008 (the least possible); by groups over the 25 pairings 25:3 (sign test p 3e-5), by source files
  32:5; within a setting -15 to +11. The sentences 0.607-0.648 against 0.479-0.544. Their words: the same
  words as the source 70-81 against 21-25 of 366, overlap 0.40-0.42 against 0.24-0.26, every run without
  below every run with. Cost $0.57-0.61 against $0.25-0.29 a run (tokens out 1.28-1.33 M against 0.30 M).
  Three readable runs a side: parameters named as the source 52-53 % against 45-49 %, local names among
  the source's 89-109 against 85-87, globals named as the source 18-25 against 13-15, $0.10-0.11 against
  $0.06-0.07, 350-363 answers against 421-434 (more names the checks refused). Without thinking the names
  describe the mechanics, longer (queue_set_check_and_reset_error_flag where with it queue_set_check_progress,
  the source's xAreQueueSetPollTasksStillRunning), and a group's answer sometimes hedges inside its line
  (prefix: pvPortMalloc_ (heap_, mem_; ...)), which goes back as unusable.
- **Choice: (a).** Thinking stays the default of every naming task (THINKING, per task); `--no-thinking`
  stays an option, asking the provider for its quick fields (llm.toml), for a model where it measures
  otherwise or a local reasoning model where time counts. (c) was not tried on runs; (d) not done: the
  kept sentence would then often be another sample's than the winning name's.
- **Why.** The reasoning is what makes the names: nothing reads its text, but the names come out of it.
  Without it a run costs half, and its names are judged clearly worse by every measure.
- **Not established.** A lower effort (low, between none and the default); another model naming without
  it; a firmware the model does not know.
- **Undo.** THINKING (per task), or `--no-thinking` on a run.
- **Status.** In the code (dread/llm.py Provider.quick, quick_fields; dread/pilot.py THINKING, thinking(),
  naming_provider, --thinking). The judge's answers and the twenty runs are not kept in the repository.

## Results as the source has them: reads of bits nobody uses, results nobody reads, second words that are leftovers (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the item "Two-word
results that are leftovers" (docs/todo.md), the only source of false statements left in the readable C
of a private firmware after the second review.

- **Question.** dread.regflow counted every read. A caller that kept the upper bits of a register a
  callee left (a bic and an orr) and stored a byte of it, or handed it to a callee that did, made that
  register a second result of the callee and of every function that passed it on; the lifters return
  two words from those, and readable C printed `u64 f(...)` with `return ((u64)hi << 32) | lo;`, its
  callers `(u32)(f(...) >> 32)`, with phantom arguments and a constant made a high word behind it. And
  readable C printed every function's r0 as a result (gizmo's `u32 timer_start(u32 r0) { ...; return
  r0; }`, `u32 delay(u32 r0) { r0_1 = timer_start(r0); ...; return r0_1; }` for two void functions): the
  lifters return r0 from every function, since dread.runner compares it at every return, whether a
  caller reads it or not (checked: emu.Result compares the return value always, two stack addresses
  aside). How does regflow find the reads that matter, and how does readable C print results as the
  source has them, without taking anything from the comparison (rule 6)?
- **Options.**
  1. Reads: (a) every read, as before; (b) an instruction that only computes registers reads its
     operands only where a result it writes is used; (c) (b) bit for bit: the bits of each operand its
     used result bits depend on (the layers' rules), a byte's store the low byte.
  2. Whose results count as read: (a) a direct caller's read after the call, as before; (b) and every
     function a pointer may call (its entry in a word of the image or made by its code); (c) every
     function's (nothing is void).
  3. With (1b) a function whose result nobody reads makes the computations behind it dead, also a
     callee's second register that flows into that result, which the cases compare all the same: the
     lifted caller then returns garbage there and fails. (a) one solution, as the program reads; (b)
     two: the comparison's (every return passes on its result registers, as the runner reads r0 at
     every return) for every register but the result, the program's for the result registers.
  4. Void in readable C: (a) never; (b) where no caller reads the result, the gate deciding; (c) (b),
     with the register's value at the return handed back through a macro where the void C leaves
     another (LEAVE(x)); (d) (b), but a function that computes its result for the return alone kept
     (a strlen nobody reads returns one in its source), and one the gate rejects void tried once more
     with its result.
  5. A leftover second word: (a) u64 as before; (b) one-word, `return RETURN2(lo, hi);` (dread.h), a
     caller that reads both words through CALL2; (c) a comment only.
  6. Which second words are leftovers: (a) all; (b) none but by the ABI; (c) by data flow: only
     constants the function writes and what callees that are leftovers themselves left there, on every
     path (a greatest fixpoint); a function that computes it, loads it or passes its caller's on has a
     result.
- **Choice.** (1c), (2b) without M-profile vector tables and the image's entry, (3b), (4d), (5b), (6c).
- **Why.** (1c) is what the item asks and what the private review needs: a read of bits nobody uses is
  no read. The layers' rules (dread.arch arm and riscv bit_demand, store_demand) are checked against the
  emulator instruction by instruction (tests/test_regflow_bits.py: no operand bit outside a rule's
  changes a used result bit, no unlisted register changes a result); the check found capstone 5's
  missing second source of uxtab, uxtah, sxtab and sxtah, a read that mattered and had been missed
  (now in the layer's operand_roles). What a function computes for a callee the analysis does not see
  stays read: a call through a pointer, a variadic function's unnamed arguments, a way out it cannot
  follow (Analysis._handed: the defining instructions keep every read; its own values only, else every
  callee in front of such a call looked as if its callers read the scratch registers it leaves alone:
  a first version gave FreeRTOS RISC-V GCC -Os some 200 kept registers more, and without the rule
  vAssertCalled read no line number for printf). (2b): a pointer's callers are unseen, so their reads
  are assumed; an M-profile vector table's handlers are left out (the exception return puts r0 back),
  as is the image's entry (the reset reads nothing back; counted, test programs whose first function
  lies at the base got its result read). The entries come from the image's words, from what the layers
  join into an address (adr, auipc or lui with the addi next to it, movw with movt) and from values the
  code makes from others it knows and stores, passes or returns (made_pointers, the layer's value_step):
  GCC's RISC-V code puts a lui and its addi apart, and without them FreeRTOS's RISC-V GCC -Os demo
  printed 81 functions more void (170 of 312 against 89), with nothing to show that the callers through a
  pointer ignore their result: its 72 tasks and callbacks found so are void in their source, but
  tests/test_results.py's twice, which printed void so, is not. A bare immediate does not count (140 in
  FreeRTOS's mps2 demos is strlen's entry, a lui of 0x80000000 the RISC-V demo's _start). (3b) keeps the comparison where it was: r0 compared at every
  return as before, kept beyond it what some caller's compared outputs depend on; a register leaves
  kept only where no compared output depends on any of its bits. (4d): the plain void form passes where
  the compiler leaves what the original left (timer_start, delay); where the original's own code put a
  value in r0 for the return alone (regflow's computed: memcmp, strlen, mul64 on gizmo), the void C
  would not compute it, so it is kept, as the source has it; where the value is the register used to
  the end for something else (gizmo's gpio_set) or a parameter handed back (memcpy's destination), the
  gate tries void and turns down what leaves another value: tried once more with its result, it passes
  as before. One change a round, the likelier cause first: a function whose code reaches another that
  failed too is tried with that one from lifted C (as before) and keeps its void form where that was
  all; then a void function whose cases differ in the value it returns alone with its result; then
  the frame's region as one object; then a void function that differs otherwise with its result
  (until 2026-10-24 the frame came first, also for a function that reached a failing callee: with void
  forms failing in the first round, gizmo-m3's handle_key went whole for its callees' failures; and a
  void function whose objects failed took its result first, which made its C anew under another digest,
  without the names the readable_names table had for it, so that its objects were never tried whole:
  tests/test_frames.py's print_status with a name for its buffer). A round that gives functions their
  results back makes the readable_types table anew, and with it every function's readable C: the table
  keeps each function's types for the digest of its C, and the first was made for the void forms (the
  functions printed with their result lost their parameters' types: gizmo-m3's strncpy printed `u32
  strncpy(u32 r0, u32 r1, u32 r2)`); else only the functions whose void status changed and their
  callers are made anew between the rounds (readable_texts keys each function's readable C by what it
  reads of the void set: void_key). Since the same day such a round makes anew only what changed: the
  trees of the functions given their results back, and the readable C whose entries changed (the entry
  "Readable C kept by what it is made of" above; void_key is its scope_key now).
  (4c) would print `LEAVE(1u << r0)` in gpio_set and the like, a statement about a register in every
  such function: not done. (5b) keeps the behaviour exact (the second word left by an asm with
  register variables; the call through a two-word function type, which GCC folds into the direct bl
  with one warning), checked with GCC 16 (ARM state, Thumb-1, Thumb-2), clang 23 (Thumb-2) and GCC on
  RISC-V; (5c) would keep the false u64. (6c): a constant in a scratch register and a callee's leftover
  are no results, a division's remainder is (also where it hands the dividend back unwritten).
- **Not established.** A real two-word result that is a constant on every path (a 64-bit function that
  returns a small number: its high word 0) counts as a leftover; a constant built by movw and movt as
  computed. A function whose result an accumulator in r0 computes (read in its own loop) is no computed
  one, and goes through the gate's retry. A pointer made by arithmetic the walk does not follow (an
  offset added to a table's base the code loads) is missed, its function maybe printed void (the cases
  still compare its r0). The other way round, a function whose entry the image holds keeps its result
  whether the callers through the pointer read it or not, and so do the functions it hands its result
  on to through a tail call: FreeRTOS's tasks and timer callbacks, void in their source, print with a
  result (with the gate's retries, 26 or 27 of the 81 to 83 readable functions prefixed v on each mps2
  build); where every call through a
  register in the image ignores the result could be told (not done: newlib's stdio reads what its
  FILE's functions return, so it would gain nothing there). A function whose source returns a callee's
  value that nobody reads prints void (FreeRTOS's xTaskCatchUpTicks): its machine code is a void
  function's. A void function the gate rejects void (it leaves another r0 than the original, which the
  cases compare) prints with the result the lifters return (option 4c would keep it void).
- **How to undo.** dread/regflow.py: Analysis.refinement returning None (every read counts), _handed
  returning {}, pointer_entries returning set(), analyse taking the comparison's view alone; dread/tidy.py:
  void_results returning False; dread/readable.py: results returning at once and Context.leftover empty.
- **Status.** In the code (regflow 26; dread.tidy void_results, leftover_calls, leftover_returns,
  narrowed_pairs; dread.readable results, the check's results retry; dread.h RETURN2, CALL2;
  tests/test_regflow_bits.py, tests/test_results.py). Figures in docs/todo.md.

## An entry without names carries its sentence (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), found on the private
real-life test: after a day's printing rules changed readable C, dread readable --carry
reported a number of functions as "carried nothing", every one with the reason "none has its variable
in this C". Their entries held a sentence and no names: the C had nothing the model renamed (a
function without locals, or one whose variables the model kept as they were).

- **Question.** Is an entry without names that was made for other C carried, or left for a new
  proposal?
- **Options.** (a) Left, as before: dread pilot asks the model about the function again, for a
  sentence it already has, and the report counts a failure where nothing was lost. (b) Carried: the
  sentence goes to the new C, with the new C's identities, and a variable the new C has that the old
  did not is open, so that a new proposal names only that.
- **Choice.** (b): dread.naming carry returns None only when an entry had names and none carries;
  carry_table skips the compile check for an entry that applies no name (the code cannot change), and
  prints "the sentence carried (it had no names)".
- **Undo.** `if not got and names` in dread.naming carry back to `if not got`, and the early return in
  carry_table's check.
- **Status.** In the code (tests/test_carry.py).

## Masks across calls, one radix per object, signed variables from negative values (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with three items of
docs/todo.md, "From a second review": Masks stay masks across calls, One radix per object and stores
through pointers, Signed variables from negative values.

- **Question.** Three of 2026-10-23's printing rules read a few lines worse than the lifter's form. A
  mask handed to a callee or kept in a variable that a shift reads printed as a negative number
  (FreeRTOS's `xTaskGenericNotifyWait(0, -1, ...)` for `ulBitsToClearOnEntry = notifyUINT32_MAX`; gizmo's
  ram_crc `r3 = -1;` for `crc = 0xFFFFFFFFu`). The radix was decided per place, so one object came out
  in two radices (the bytes of a colour triple `0x96u, 0x35u, 15`; a variable compared with `63` and
  `0xffu`; sizes stored through a pointer as `0x40u` beside the bounds `r0 <= 63` that select them).
  And `u32 ret = -10; ... return ret;` stayed u32 (gizmo's ring_get, `u32 r0_1; ... r0_1 = -1;` for
  `int ring_get`). How are masks recognised across calls, what is one object for the radix, and when
  is a variable or a result s32 from its negative values?
- **Options and choices.**
  1. A callee's mask parameter: (a) from its machine code (an instruction-level flow of the argument
     register into and, orr, eor, bic, mvn: one per architecture); (b) from its C: the plan's analysis
     trees for the lifted functions, and for a callee outside them that a lifted function passes a
     constant that would read negative, its own lifted C (the lifter is the machine code's flow,
     written once for every architecture); (c) by the constant alone (all ones is always a mask): wrong
     for portMAX_DELAY's -1 (a count), FreeRTOS's most frequent all-ones argument. **(b)**, a table key of
     its own (the readable_types table's `masks`, dread.polish mask_params): a parameter never assigned
     whose every read, also through copies, is an operand of &, |, ^ or ~ (also &=, |=, ^=), the value
     returned as it is (a void function's leftover register), or an argument for a callee's mask
     parameter (over the functions until nothing changes); a value it stores is no mask's use (what
     memory does with it is not followed), nor a shift (x << 2 scales FreeRTOS's stack depth; the
     lifters' 1 << (r0 & 0xffu) masks a count). A variable read only by bit operations and
     shifts (ram_crc's crc: ^=, >>, &, ~) gets no negative number either; all ones as such a mask prints
     0xffffffffu whatever the lifter wrote (ARM state's ~0x0u, Thumb-2's and RISC-V's all ones: the
     sources write 0xFFFFFFFFu), another complement keeps its form (~0x20u: the bits it clears).
     Compound assignments now count as reads (x ^= y reads x as a mask's operand; x++ as a number), so
     a counter that & also reads is no mask any more.
  2. One object for the radix: (a) every place a structure's or an object's: a structure's fields hold
     unrelated things (a count beside flags), and static data's structures are inferred groupings of
     globals; (b) a variable; an array's elements (a local's, static data's, a typed pointer's: x[k], *p)
     as one object; a structure's field its own; joined through copies (x = y, *p = v, v = p[k], a
     declaration's initialiser: one value in both), not through an access at an address the code computes
     (`*(volatile u32 *)a5` stands for other memory later: a RISC-V function that keeps its registers had
     joined a0 with the pointer a5 so). **(b)**. The object holds numbers where a use makes one of its
     places one (as before), where its places together are given (a select's constant arms each: GCC 13
     makes selects of tally's sizes on Thumb-2) or compared with two or more distinct plain numbers (as
     one place's were), or where it takes a number the function writes in decimal elsewhere (a bound of
     an ordered comparison or its neighbour on the side the compiler moved it to, x <= 63 for x < 64; a
     divisor; a multiplier; a constant of an object that holds numbers). Never one that holds a pointer
     (a counted address: RISC-V's walk over the ready lists, `s4 += 0x14u`, now hex as the pointer it is
     copied into), or only texts' starts and functions' entries (dread.constants Pointed: FreeRTOS's
     prvCheckTask stores its status message's texts, which lie at small addresses on an image at 0; main
     printed them as numbers by the old place rule, clang -O2 `g_20000000 = 379;`, now `0x17bu`; line
     numbers stored beside them elsewhere fall on such a text now and then and stay numbers: all of an
     object's values must be addresses), nor by a number taken where one of its own values looks like a
     bit pattern (0x55, 0xf0: clang -O2's prvSanityCheckCreatedEventGroup keeps an event group's 0x55 and
     a line number in one register). Then all its constants below WIDE print decimal, a run of ones from
     bit 0 too (255 beside 63: the bound of x < 256), unless all its constants of 10 or more look like
     masks (x <= 0xffu alone stays as it was); a shifted run (0xff00) stays hex. A sum's constant is a
     number also where the sum goes into such an object (RISC-V's branchless select `(...) + 15` stored
     into the colour's byte), and a complement stored into a narrower place is the value it leaves there
     (~0x69u into a byte: 150, or 0x96u where the object stays hex).
  3. Signed variables: (a) every variable given a negative constant; (b) one read only by == or !=, a
     truth test or return (the item's rule), kept where it adds no casts (the plan's trial). **(b)**,
     and one the function returns only where the function's result is s32: gizmo's udiv_var returns
     0xFFFFFFFFu beside an unsigned quotient, a saturated sum likewise; a u32 function with an s32
     variable returned would say two things. Results: (a) s32 wherever a return gives a negative value;
     (b) where a caller reads the result too, and every caller only as the rule reads a variable (==,
     return, a signed comparison, a variable read only so: through dread.cflow's reaching definitions,
     the reads the call's definition reaches). **(b)**: (a) typed udiv_var (no caller) and dispatch-like
     functions whose other returns are full-width values. A callee the plan does not analyse (not
     lifted) whose result an analysed caller compares signed is s32 in the table too (its result alone
     in `prototypes`): main declares `extern s32 ring_get(u32)` and ring_get printed on request says
     `s32 ring_get`, as the item's example asks; such a function gets the negative variables on its own
     C (local_types: p.params lists the functions the plan analysed).
- **Why.** Each rule changes how a constant or a declaration reads, never what the C computes: the
  literals are chosen by dread.polish's exact checks (same_in) as before, the s32 typing goes through
  dread.vartypes' rewriter as the signed rule does, and the gate checks every function. Tests compile
  the printed forms beside the originals on the host (random functions with masks, an array's elements
  and copies; random callees and callers whose negative values the plan types, each in a file of its
  own, in 32 bits).
- **Measured.** See the three items in docs/todo.md (the eight builds' gate counts, the patterns before
  and after, the functions whose C changed).
- **Not established.** That a negative value read only by == is the source's int (a u32 sentinel
  compared with 0xFFFFFFFFu reads the same); a function only called through pointers keeps u32 (no
  caller reads it); a mask parameter of a callee that neither lifts nor is reached from a lifted
  function's constant argument stays unknown (a function printed on request passes -1 to it); the
  value evidence of a bound's neighbour may make a coincident flag decimal in the same function (none
  seen on the eight builds' changed lines).
- **Undo.** Masks: take `masks=` out of readable.polish_tree (and mask_plan out of make_plan); the
  variable rule: Polisher.bitwise empty. Objects: Polisher._objects making every place its own object
  and no `known` values. Negative values: candidates() without the "negative" kind, decide_result's
  second path and the outside callees in Planner.run, local_types not called from apply.
- **Status.** Done (dread/polish.py: mask_params, _objects, placed, near_top; dread/vartypes.py:
  negative_variables, returns_negative, read_as_negative, Facts.reads/results/result_kinds,
  local_types, Planner.decide_outside; dread/readable.py: mask_plan, polish_tree; dread/shapes.py:
  Plan.masks; docs/formats.md; tests/test_polish.py, tests/test_vartypes.py).

## Extensions and bit fields: casts, fields and selects, each rewrite proved where it is made (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the item "Extensions
and bit fields" (docs/todo.md), which the second review of 2026-10-24 found the most frequent thing a
reader decodes by hand.

- **Question.** Where the machine has no sign- or zero-extending instruction and no bit-field extract
  (ARM state before ARMv6, RV32 without Zbb), a cast to short is lsl #16; asr #16, a signed bit field
  lsl #20; asr #24, and GCC and clang compute a select without a branch from a smeared sign (srai a0,
  a0, 31; andi a0, a0, -3 for `return f() < 0 ? -3 : 0`, bic r0, r0, r0, asr #31 for a clamp at 0). The
  lifters write asr as (u32)((s32)x >> n), so readable C printed (s32)(x << 16) >> 16, (r0 << 2) & 4,
  ((s32)r0 >> 2) & 0xffu, r0 & ~(u32)((s32)r0 >> 31), and the same source read differently on Thumb-2
  (sxth: (s16)x) and on ARM state or RV32. Where are they rewritten, into what, and how is each
  rewrite kept exact for C's types (the promotions of u8 and u16 to int, conversions to signed types
  modulo 2**n, arithmetic shifts of signed values)?
- **Options.** Where: (a) dread.polish, on the finished tree: its types are those vartypes gave (s32
  variables, pointers), the lifters' u32 registers gone, and conditions and vartypes would not see the
  results (a select's condition, a cast that types a variable); (b) a pass of dread.tidy among the
  others, on the lifters' types, every round. How: (c) rules per pattern that print the form (fast,
  each rule argued once, a mistake wrong everywhere); (d) rules that only propose, guided by an analysis
  of known bits, each proposal proved by z3 for the node it replaces (dread.cexpr, as the flags pass
  proves its rewrites). Extensions: (e) as masks (x & 0xffffu), (f) as casts ((u16)x), as the
  extending instructions read on Thumb-2. Selects: (g) leave smears as they are, (h) the select they
  compute, where the selected value is evaluated on one way only (no load, no call).
- **Choice: (b), (d), (f), (h)** (dread.bitfields, tidy's "bits" pass after "expressions"). A sign
  extension by shifts of a field of 8 or 16 bits is a cast of the field ((u32)(s16)x, (u32)(s8)(x >>
  4)), a logical pair from bit 0 (u32)(u16)x or (u32)(u8)x; a field whose top bit the known bits show 0
  is the unsigned field; other pairs and masks move to the field they select ((y >> k) & mask, (x &
  1) << 2, (w >> 2) & 0xff); masks and narrowing casts the known bits make redundant go; under a cast
  to 8 or 16 bits what only changes higher bits goes; a smeared sign (or a negated truth value, RISC-V's
  sltu; neg) under a mask is a select of the masked value and 0 where that value reads no memory and
  calls nothing, with constants folded into a select of constants ((c ? 0x10 : 0) + 0x20 as c ? 0x30 :
  0x20). Fields of widths C has no type for keep their shifts.
- **Why.** (b): the proofs need the lifters' types (each register a u32, each load its width) and the
  later passes read the results (conditions turns a select's (s32)~x < 0 into x >= 0; vartypes types a
  clamp's parameter s32: `u32 bits_clamp(s32 r0) { return r0 >= 0 ? r0 : 0; }`). (d): with z3 checking
  each node, a rule can only fail to apply, and the analysis that guides them (known: zeros, ones and
  copies of the top bit, from constants, types, casts, loads, masks, shifts, sums, comparisons and
  selects) may be approximate; a node keeps its promoted type (a cast where a rewrite's own type would
  differ: a u16 promotes to int, not to unsigned int), and each load and call of the node stays, once.
  The proof is per node: small, and memoized for the tidy run (each round asks again). (f): the same
  source then reads the same on ARMv4T, Thumb-2 and RV32 (a test program at -Os and -O0 does), and a
  mask the source wrote (and #0xff) stays a mask: only shift pairs become casts. (h): a select is what
  the source said (gizmo's clamp(level, 0, 9), parse_kv's `parse_uint(...) < 0 ? -3 : 0`), and a
  select evaluates its arm on one way only, so a value with an effect keeps the smear. Cautions found
  on the way: a node that goes through 64 bits (a cast or a variable of 64 bits) is left to the passes
  that read the lifters' idioms there by their shape, sums, flags and pairs, which run after this one
  each round (a first version dropped the (u32) of a byte store's (u8)(u32)((u64)r6 + 1 + 0), and sums
  no longer read the add: clang -O2 printed (u8)(r6 + 1); it dropped the (u8) of GCC -O0's flag select
  (u32)(u8)(!(u32)(t_3 >> 32) || ... ? 0x1u : 0x0u), and flags left r3_1 = r0_1 <= s24 ? 0x1u : 0x0u
  for the comparison); a cast of a value to its own type is dropped only between two arithmetic shifts
  (a first version dropped the (u32) of the compare idiom, (u64)x + (u32)~y + 1, and two gizmo
  conditions changed form); a mask is moved into a left shift only where it is one run of ones below
  the top ((x << 2) & 0xfffffff0u stays: (x & 0x3ffffffcu) << 2 reads no better). dread.tidy
  peephole's unproved field rule for a plain variable (x << a >> b, b > a) stays; x << 16 >> 16 and x
  << 24 >> 24 it leaves to this pass.
- **Not established.** A variable's bits from its definitions (r2 = r3 & 7; ... r2 & 0xffu keeps its
  mask: the proof sees r2 as any u32; on the eight builds three such masks in each of gizmo's ARM
  builds' script_run and GCC -O0's __NVIC_EnableIRQ, all the amount of a register shift: the item
  "Register shifts with an amount known below 32"); 16-bit int (MSP430: the pass leaves its functions
  alone); whether a mask that GCC made of a cast (ARM state's and #0xff for a u8) should read as the
  cast too: it stays a mask, as one the source wrote.
- **How to undo.** Take ("bits", bits) out of tidy.PASSES and the narrowing of peephole's field rule
  (`and not (l.r.value == r.value and 32 - r.value in (8, 16))`); dread/bitfields.py then serves
  nothing.
- **Status.** In the code (dread/bitfields.py, dread/tidy.py; tests/test_bitfields.py). On the eight
  builds the gate accepts the same functions, the readable tables byte for byte, and the printed C of
  24 functions changed; the figures are in docs/todo.md under the item.

## The frame's objects before the variables' names, and in the naming question (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the item "Frame
objects under the table's names" (docs/todo.md, from a second review).

- **Question.** dread.frames found the frame array and its end by the lifters' names (`frame`, `sp0`)
  and ran after the readable_names table's names (dread.readable _named). Wherever a naming run had
  named either, it made no objects: not in the printed C, not in the compiled C the gate builds. The
  naming question (question_trees) never ran dread.frames: it showed the frame raw (`sp0 - 20`), so the
  model named `frame` and `sp0` when it named anything there and never saw the objects, whose default
  names (stackN) got no proposal. On a private firmware this caused raw frames in a review.
  Public reproduction: gizmo a32's print_status with an entry made for its C naming `sp0` alone
  (`buf_end`): `u8 stack20[12]; fmt_dec((u32)stack20, ...)` became `u32 frame[6]; u32 buf_end =
  (u32)&frame[6]; fmt_dec(buf_end - 20, ...)`. In which order do the objects and the names go, what
  becomes of a name of `frame` or `sp0`, and how do the objects get names of their own?
- **Options.**
  1. The order: (a) the names first, as before, dread.frames finding the frame by its shape (an array,
     and a variable initialised to the address of one of its elements) instead of its names, and a
     second naming step after it for the objects; (b) the objects first, on the lifters' names, then one
     naming step over the variables as they are then, the objects among them.
  2. A name of `frame` or `sp0` where the objects take those variables away: (a) dropped; (b) given to
     the object where the region is one; (c) kept for whatever is left of them.
  3. The question: (a) as before; (b) the C the builds make before the names, objects and all, with the
     objects' identities in the entry (what carries a name to C made anew).
  4. An entry made from a question that showed the frame raw, whose digest still matches: (a) left as it
     is (the pilot calls it named already, its objects keep stackN); (b) asked again for its objects,
     its other names shown and kept unless the answer changes one.
- **Choice.** 1(b), 2(a) where they are gone (where the C keeps them, raw slots below the region, a name
  of either applies as before), 3(b), 4(b). dread.readable _framed applies the globals, structures and
  types and makes the objects (avoiding the names the entry will give); _named then applies the names
  (dread.frames table_names drops those of the variables dread.frames took away, and those of objects
  the C has not); question_tree makes the objects as the builds do and keeps their identities
  (dread.frames object_identities: an array's or a structure's is its base, stack20, its offset below
  the frame's top; a scalar's the base and the instructions that write it, as a slot variable's); dread
  pilot's readable_jobs asks an entry made for this C again where the question shows objects it has
  neither a name nor an identity for (dread.frames unasked); the check asks dread.frames whether a
  failing function has objects before it tries its region as one object (it looked for stackN in the C
  it built, which the table may have renamed).
- **Why.** 1: a proposal's digest is taken after the stages, before every table; the objects came after
  that point and still do, so every entry applies to the same C as before (none waits, none needs
  carrying). dread.frames keeps working on names a table cannot give (KEYWORDS refuses `frame` and `sp0`
  as new names) and on the raw `sp0 - N` addresses dread.tidy's frame helpers know; (a) would make those
  helpers take the base's name wherever they look and would still name the objects in a step of their
  own. One naming step over the final variables keeps the question, the printed C and the gate's C on
  the same variables. 2: `sp0` is the address of the frame's top, and a model that named it saw the raw
  code use it as an end (`buf_end`, `frame_end`); the object is the buffer, not its end, so (b) would
  put a wrong name on it. `frame` is the whole array, the saves and the slots below the region with it;
  a name chosen for it from raw offsets is a guess at one object at best (measured below: Haiku named it
  after the object it held in 11 of 14 cases, ulFrame or ucFrame in 3, and named sp0 in all 14, so the
  function is asked again anyway). Dropped, a name is lost where it named the machinery the objects
  stand for, and the object gets one from a question that shows it (4). 3: the model names what a reader
  sees; identities let the objects' names carry as a slot's do (to the object of the same offset; a
  scalar with the same definitions). 4: with (a) the objects would stay stackN for good, the digest
  matching.
- **Measured.** On the eight builds (gizmo a32, m3, m4, rv32; FreeRTOS GCC -Os, clang -O2, RISC-V GCC
  -Os and GCC -O0, copies), with a table a script made for main's C: every variable of each function's
  question named, and of the functions whose question showed the frame array and sp0 (5 on each gizmo
  build; 60, 55, 45 and 60), every third with sp0 named, every third with the frame array named, every
  third with neither. Functions with frame objects without the table: 5 on each gizmo build, 57, 53, 45
  and 56. With it, main made 1 and 20, 18, 15, 20 (every function whose entry named either lost its
  objects: 4 and 37, 35, 30, 36) and left 4 and 40, 37, 30, 40 frame arrays raw; the fix makes as many
  objects as without the table and leaves only the frames that stay raw without it (0 and 3, 2, 0, 4,
  where no address leaves). Wherever objects were made, the frame array and sp0 went: no function kept
  either beside objects, so a name of either applies only where the frame stays raw. The compiled C
  without names is byte for byte main's in every function of the eight builds. Every entry applies with
  the fix as on main; the names in the C fell by those of the frame array and sp0 in the functions whose
  objects took them away (4 on each gizmo build, 37, 35, 30, 36), on RISC-V also by those of the saved
  registers whose saves went with the objects (17 on gizmo rv32, 183 on the RISC-V GCC -Os build: their
  variables are gone, as in every function whose objects were made before). The gate (dread readable
  --check) with the table accepts the same functions on main and with the fix as without the table, set
  for set (35, 36, 44, 31 on the gizmo builds; 317, 275, 252 and 354 of 329, 286, 259, 357); on clang
  -O2 the fix tries prvSendFrontAndBackTest and vPrimaryBlockTimeTestTask whole, as main does without
  the table (with it main had left their frames raw); on GCC -O0 the whole image differs with lifted C
  alone in all three runs, as before (not decisive there).
  With Haiku on the GCC -Os build: main's question showed the frame raw in 55 of the 317 functions it
  named ($0.14); the model named sp0 or the frame array in 35 of them (sp0 in all 35, mostly frame_end,
  frame_top, pxFrameEnd), and main's code then made objects in 22 functions of 57 (38 frames raw). With
  the fix, 57 and 3; the pilot asked the 52 functions with objects again (30 s, $0.03), named 68 of
  their 74 objects (xTimeOut in xQueueGenericSend, xQueueReceive and xStreamBufferSend, xMessage,
  xLastWakeTime, rx_msg_buf, ucRxChunk) and kept 402 of the 403 names they had besides those of frame
  and sp0. 7 of the 68 are the source's names of those locals (dread readable --score's measure), 1 of
  the 49 names the table had given frame and sp0; the score's local names among the source's went from
  101 of 1,021 to 107 of 1,076.
  Carried across a real change of the C: names that main's question and the fix's made on an older
  commit's C (fa47c42, before 15e82bf split parameters; every variable named), each carried by the same
  code to main's C. The same functions carry with the fix as without (3, 2, 2, 1 on the gizmo builds;
  20, 3, 11, 25), with the same names dropped (none on gizmo; 11, 3, 7, 15) and every other name alike,
  but where main carried names of the frame array and sp0 (on RISC-V of the saved registers too), the
  fix carries the objects' (21 in 12 functions): none dropped, no object left without a name.
- **Not established.** That a name carried by an object's offset names the same thing where the region
  is split otherwise (the readable_types table's whole makes it one object, which takes the name of the
  object that starts where it does; a callee's structure joins objects). How often a name of `frame` or
  `sp0` stays on what is left of them (none of the eight builds keeps either beside objects). dread
  trace's frame view finds objects by their default names (stackN at sp0 - N), as it finds slot
  variables (sN): one the table names is shown without its label there.
- **How to undo.** dread/readable.py: in _named apply the names before frame_objects again (and give it
  no avoid), question_tree without frame_objects (its ids the identities before the globals);
  dread/pilot.py readable_jobs without unasked. The check's question to dread.frames (framed) may stay.
- **Status.** In the code (tests/test_frames.py).

## Functions' and texts' addresses in readable C: a macro that is the number, a note beside it, by evidence of use (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20; two items of docs/todo.md,
"From a review against hand-written C": Addresses of functions and texts by name, Names without their
type).

- **Question.** A function's entry or a text's address handed to a callee prints as a number
  (FreeRTOS GCC -Os vStartTimerDemoTask: `xTimerCreate(0xd9cu, ..., 0x913du)`, the timer's name "FR
  Timer" and prvAutoReloadTimerCallback; gizmo a32 print_status `uart_puts(0x20d35u)` for "mode ").
  How is each written so that it compiles to the same code, and where is a constant such an address?
  And the naming model keeps the type of DREAD's default names in its proposals (g_20001830_u8).
- **Options for a function.** (a) `(u32)&f` with f's declaration: f is the C function's symbol, and
  where the overlay moves the function's C the number changes (prvAutoReloadTimerCallback's lifted C
  is 112 bytes for the original's 80 on GCC -Os and lands at 0x59d0): the callback a timer stores, and
  its cases compare, is another one. (b) A symbol for every function's original entry, written by
  dread.overlay, and a declaration that names it (an asm label): a change of every build path, and a
  file that calls the function too must still call the C. (c) A macro that is the function at its
  number, `#define prvAutoReloadTimerCallback FUNCTION_AT(0x913du)`, dread.h's FUNCTION_AT a cast of
  the number to a function pointer, dereferenced: `(u32)&prvAutoReloadTimerCallback` is the constant
  again, the code the same by construction, nothing for the overlay to know. **(c)**, the number as
  code takes it (bit 0 set for Thumb code: a Thumb function's even entry is none, and no address inside
  a function is one); where the file uses the name otherwise (it calls the function, or is it: the macro
  would rewrite the call) or the name is no C name, the number stays with `/* &name */` beside it
  (newlib's fflush, which calls _fflush_r and hands it to _fwalk_sglue).
- **Options for a text.** (a) A comment beside the constant, `0xd9cu /* "FR Timer" */`; (b) a named
  constant (`#define FR_Timer 0xd9cu`, or `text_d9c`): a name made from a text loses what makes it
  that text (a trailing space in "mode ", "\r\n", an 80-character path), two texts give one name, a
  default name says nothing, and the reader looks the text up either way; (c) a string literal: a new
  copy elsewhere, other code. **(a)**: the number stays, as the listing and the machine code have it,
  the exact text beside it as a C string literal (cut to its first third, "...", its end beyond 48
  characters: a path ends in its file's name; `*/` written `*\/`), "initially" in front where the
  startup fills that memory (a text in RAM the code may change); a comment changes no code, and the
  pilot's questions already annotate constants so (dread.constants pointed_note). It is a note on the
  literal (cparse.Num.note), which dread.polish keeps as it is, and which shows in the printed form,
  the reading form and the naming questions.
- **Where: measured.** The first version took static data's rules (an argument for a pointer parameter
  or for a callee not analysed, a value stored, a result, an operand of == or !=). On FreeRTOS's mps2
  builds it annotated line numbers stored into an error variable, a stack size, a device register's
  bit and event bits compared with a constant as texts: the demos keep their texts at 0x100 to 0x1300,
  where their line numbers lie, and GNU ld merges the ends of strings ("T1" is the end of "CNT1"), so
  a number that falls among the texts mostly points inside one (GCC -Os: 109 of 234 texts annotated
  lay inside another, all but six of them numbers). So a text is one only as an argument: for a
  parameter that takes texts (dread.naming taken_kinds: at least two distinct constants from the
  calls, every one a text, more than half of them beginning behind a NUL; the readable_types table's
  new `takes`: xTimerCreate and xTaskCreate only store the name, no pointer for dread.shapes), or one
  the callee uses as a pointer (pointer_params) where the text begins there (pointer_params counts an
  index the callee adds to an address: xTaskCreateStatic's stack depth of 320 pointed inside a text).
  A function's entry for such a parameter or one that takes functions (every constant a function's
  entry, two at least, or a parameter the callee calls through: newlib's _fwalk_sglue), what a call
  through a constant goes to (a veneer), and a value stored into memory, as for static data: every such
  store on the eight builds is the function's address (newlib's FILE callbacks __sread, __swrite,
  __sseek, __sclose, cleanup_stdio, stdio_exit_handler, the initial stack frame's prvTaskExitError, the
  TaskNotify demos' prvRand as a random seed), except where the value looks like a bit pattern
  (dread.polish patterned: a RISC-V image's first function at 0x80000000 is
  taskEVENT_LIST_ITEM_VALUE_IN_USE's value; gizmo's first function at 0x20000). Never a value returned,
  compared or assigned to a variable (none found on the bench; each a way to a number by chance).
- **Names without their type.** (a) Show the question's defaults without the type: two views of one
  address (a word and its byte) would show one name; (b) strip a type at the end of a proposal that
  has other words (dread.naming untyped), in the answer's check (pilot.name_globals), unless the name
  without it is taken or another proposal of the same question. **(b)**.
- **Measured.** Figures in docs/todo.md under the two items: the gate accepts the same functions on the
  eight builds, the digests are the same, the readable C of the functions with a text or a function
  changed by the notes and names alone.
- **Not established.** A store of a number that is a function's entry by chance is named where the
  image's code lies where such numbers do (code first, from 0: none on the bench, whose texts lie
  there); a parameter given the same text at every call takes nothing, and its text stays a number
  where the callee does not dereference it (xTimerCreateStatic's "T1"); a text plus an index (`0x908u
  + r4`) and a text assigned to a variable stay numbers; a text in memory an object of static data
  covers is written as that object's address, without the text; that a text that begins behind a NUL
  is the one meant (a number that falls exactly there is one).
- **How to undo.** Context.pointed returning None (or pointed=None in dread.readable _named and
  _question_one) gives the readable C as before, byte for byte; the table's `takes` is then unread.
  pilot.name_globals: drop the untyped step.
- **Status.** In the code (dread/constants.py Pointed; dread/naming.py apply_globals, taken_kinds,
  untyped; dread/include/dread.h FUNCTION_AT; tests/test_addresses.py, tests/test_naming.py).

## One variable per value: a parameter kept apart from what its register hands back (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the item "One
variable per value" (docs/todo.md).

- **Question.** dread.tidy's webs splits a register into one variable per web (the definitions that
  reach a common use), a parameter keeping its name for its value at the entry. What it left as one
  variable were registers whose values meet in a use. A parameter and a callee's result meet where a
  function whose source returns nothing hands back what the register held last (gizmo's
  handle_key(int c) returns c on one path and led_on's leftover on another; FreeRTOS's
  vQueueAddToRegistry, vTaskDelay, vTaskResume), or where a callee that does not read the register may
  hand it back (vTimerSetTimerID's xTimer, or vAssertCalled's leftover after it, through
  vPortEnterCritical(r0)). The naming model named the one variable after one of its uses (a queue
  handle assigned an assertion's result). Which meetings keep one variable, and how does readable C
  keep the others apart while its behaviour stays the same?
- **Options.** (a) Split every web at every meeting with a copy where the values meet (SSA's copies:
  `u32 v_1 = v; if (v > 9) v_1 = 9;`). (b) Split only where the values meet in uses that hand the value
  on unread (a return no caller reads, an argument the callee does not read), for a parameter's
  register, the copy being the new variable's initial value (`u32 r0_1 = r0;`): nothing added to the
  body. (c) As (b), and also other registers whose values with uses of their own meet only at such a
  use (vTaskResume's r0: prvTaskIsTaskSuspended's result, then the ready list's address, both handed to
  vPortExitCritical), with a copy behind each such value. (d) Leave the C and say in a comment at the
  return that the value is a leftover.
- **Choice.** (b) (dread.tidy split_parameter and hands_on), where a value on either side is read (a
  use as the parameter or of a later value); with it a parameter moved back after a call as no new
  value (propagate's _moved_back).
- **Why.** Where the values meet in a use that reads them (a clamp's `if (v > 9) v = 9;`, a pointer a
  loop advances, `if (!p) p = dflt();`), a source holds one variable, and (a) would write two names and
  a copy for it. Where they meet only in a leftover, the register is no variable of the program: the
  source returns nothing there or the callee does not look at it, and the cases compare it because the
  register is behaviour. (b) costs one declaration line a split and no statement, and the parameter
  is then never written, so the copies the compiler made of it to keep it across calls (r4 = r0)
  propagate into it: vQueueAddToRegistry reads its parameter as r0 throughout, r4 gone, and gizmo
  a32's apply_config's text pointer is typed `const volatile u8 *` (dread.vartypes), which the variable
  that also held parse_kv's status could not be. A split keeps every value at every use: the new
  variable holds the parameter's value wherever the register still held it. A register nothing reads
  but such uses (irq_dispatch(void): `if (...) r0 = timer_irq(); return r0;`) stays one variable: no
  value to keep apart. (c) needs a statement for every value it keeps apart, which reads worse than
  the leftover variable it removes; not done (its count is in the item). (d) is the way of the item
  "Two-word results that are leftovers", open for one-word results too. The move back: GCC keeps an
  argument in a callee-saved register across a call and moves it back for the next (r4 = r0; f(); r0 =
  r4;); where the C's call writes no r0 (its value unread), `r0 = r4` is r0 = r0, but propagate did
  not put r0 into a statement that writes r0, and inside one arm the move back left r0 two definitions
  at the join (prvDeleteTCB). Only the plain copy into a parameter is taken so: a first version let
  propagate into every statement that writes the copy's source (r4 = x << 2 behind x = r4, a flag
  block's temporary), which changed the form of unrelated functions' C (gizmo m3's script_run, a test's
  xStreamBufferIsFull); a second took every plain move back, and on GCC -O0 ten functions' reloads of
  a local from its stack slot (r3 = s16 behind s16 = r3) moved the value from the slot's variable to
  the register's, a line longer in pvPortMalloc and under another identity (the slot's name would not
  carry to the register's variable).
- **How to undo.** dread/tidy.py: webs without the split_parameter call (hands_on and split_parameter
  gone); propagate's guard back to `or _writes_name(s, value.id)` (_moved_back gone).
- **Status.** In the code (tests/test_tidy.py).

## A chain of assignments whose variables get different types: one assignment each (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the fix of a
readable C that did not compile on a private firmware (docs/todo.md).

- **Question.** The lifters clear the scratch registers a call leaves in one chain, `r1 = r2 = r3 =
  r12 = 0;`. Where that chain reaches the typing passes and one of its variables becomes a pointer
  (dread.shapes' structure pointer, dread.vartypes' pointer), the value of the assignment to it is a
  pointer, and the chain hands it to an integer: `r1_1 = r2 = (volatile struct s_1 *)(r3 = r12 =
  0);` is an error of GCC 14 and later (int-conversion), a warning before. How does readable C write
  such a chain?
- **Options.** (a) Keep the chain and cast each link where the type changes: `r1_1 = (u32)(r2 =
  (volatile struct s_1 *)(r3 = r12 = 0));`, as dread.vartypes' Rewriter already did for its own
  pointers. (b) Split it into one assignment per variable, innermost first, where it assigns a
  variable that gets another type: `r12 = 0; r3 = 0; r2 = (volatile struct s_1 *)0; r1_1 = 0;`
  (dread.shapes writes the 0 it gives a structure pointer so; dread.vartypes' pointers take a plain
  0). (c) Make the chain not reach the typing passes: the clean-up's split, which takes such chains
  apart in the function's body, also inside an instruction's block, where dead then takes out the
  registers nobody reads.
- **Choice.** (c), with (b) as the typing passes' guard. dread.tidy split works on every statement list
  but a block that binds registers (every_list); dread.shapes apply_types and dread.vartypes apply
  split a chain that assigns a variable they type before they write any type (dread.tidy split_typed:
  each variable gets 0 where that is the chain's value, else the variable inside it), and the plan's
  trials count such a chain as split (vartypes Rewriter.top).
- **Why.** On the public reproduction the chain survived because the split looked only at the body's
  own list (dread.tidy's stmt_lists: "nested blocks are the instructions' own"), and ARM's conditional
  call is one such block, `if (!z) { r0 = f(...); r1 = r2 = r3 = r12 = 0; }`: dead keeps an assignment
  whose value has an effect (the chain's inner assignments), and webs leaves the variables a chain
  writes inside it under their old names, so r2 there was the parameter that dread.shapes typed (that
  the private function's chain stood in such a block is not confirmed). With the split there, the
  line reads `if (r0 != 0) r0_4 = pair_note(r0_4 + r3);` (the public reproduction, an ARM-state
  program in tests/test_vartypes.py; gizmo a32's timer_irq lost its four zeroed registers the same
  way). (a) compiles but reads worse than both, and its casts counted against typing a variable that
  stands in a chain in the plan's trials (r2 and r3 read as bytes stayed u32 for it). A split
  assigns what the chain assigns: each variable the value converted to its type, 0 staying 0 in every
  integer and pointer type, else the next variable's value as the chain passes it on; the variables
  are the function's own, neither volatile nor read in between. Compiled with optimisation, as DREAD's
  C is (lifted_cflags, -Os by default), the line split and the chain with (a)'s casts made the same
  code in pair_touch (GCC 16 in ARM state and Thumb-2, clang 23 in Thumb-2, at -Os and -O2; GCC -O0
  orders its stack stores otherwise), and the gate checks the behaviour as for every transformation.
  The guard keeps the typing passes from writing a pointer into an integer whatever the clean-up
  leaves (a chain of variables of different types, a value that is neither a constant nor a
  variable).
- **How to undo.** dread/tidy.py: split over stmt_lists again instead of every_list; drop the calls
  of split_typed in dread/shapes.py apply_types and dread/vartypes.py apply and of chain_parts in
  Rewriter.top.
- **Status.** In the code (tests/test_tidy.py, tests/test_vartypes.py).

## What dread trace takes for the point where two runs part (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), with the tool that traces
one case to the first divergence (docs/todo.md).

- **Question.** C keeps its own frame and its own registers, so the two runs of a case cannot be
  compared instruction by instruction. At which points are they compared, and how strictly, so that
  the first divergence the tool names is where the C went wrong and not where it merely differs?
- **Options.** (a) Only what `dread test` compares (the log, the stores outside the stack, the end),
  in order. (b) Those, and every call: the callee, its arguments and what they point to. (c) Also the
  locals, mapped through the debug information.
- **Choice.** (b), with these rules: a call's arguments are the registers the callee reads at its
  entry (dread.regflow) and its stack arguments; two values that both point into the stack count as
  the same, and the bytes behind them are compared instead, within the frame object they point into
  and only those the caller's call stored; a call with other arguments is reported as where the runs
  part first, but as soft (the callee may not read the value on this path): the tool goes on to the
  first difference `dread test` compares and reports both. Stores outside the stack are compared by
  stretch between two calls (what the stretch leaves in memory), so C that stores in another order or
  width does not part; the first store in order whose bytes differ is named. Calls into the
  architecture's run-time helpers and into shared code a lifted function's C holds are no shared points.
  A branch back to the entry of the function that runs is a loop, not a call.
- **Why.** (a) names a store or a log entry long after the cause where a wrong argument or a wrong
  local reached a callee; (c) would part at every local the C keeps in a register. The final
  comparison is always `dread test`'s own (dread.emu Result.diff), so nothing it fails traces as
  agreeing (rule 6 holds: the trace only adds what it reports).
- **How to undo.** dread/trace.py: Compare (the order and strictness), argument_differences and
  pointed_differences (the soft points), Context.transparent (the calls left out).
- **Status.** In the code (dread/trace.py, tests/test_trace.py).

## A refusal stands only where it explains every difference (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20). It makes the comparison
stronger (rule 6): no case is refused or compared except now that was compared before, and some that
were refused fail.

- **Question.** dread test (and through it the lift check and the readable gate) refuses a case instead
  of failing it when its input is one the C cannot be held to: the original touches its own saved
  registers (a length past a local), takes a stack address for a value, calls through a code pointer
  the input gave into the middle of a function, or, in a written case, loads instructions as data or
  stores into the image; and it compares a case except the outputs the original's stale stack data
  decides. Until now a reason that held refused the case whatever else differed (the stale data
  apart, whose log was one output: any entry moved left the whole log out). On the private real-life
  test the readable gate accepted C that had lost an argument of a call: every case that showed it was
  refused under the saved-register rule, and the cases that agreed ran every instruction. Which
  differences may each reason explain?
- **Options.** (a) As before. (b) Each reason explains a defined part of a case's outputs; a case is
  refused only where the reasons that hold explain every difference, and fails otherwise, its line
  naming the reasons and what they leave (`FAIL NAME [label]: refused for X, but it differs Y: Z`).
  (c) No refusals: such cases fail, and their functions stay lifted or original.
- **Choice: (b)**, with these rules (dread.runner judge, Moved, After; dread.fragile Timeline,
  stack_moved):
  - *An event explains what comes after it, and nothing before it*: the first touch of a saved
    register's slot (saves_touched), the call into the middle of a function (wild_call), a written
    case's first load of an instruction as data or store into the image (code_touch). Before the event
    the C's frame layout, or the code at the call's target, has not mattered yet, so C that is right
    does what the original does there; after it the C may do anything: its overflow lands among its
    own locals and saves, which lie otherwise, or it runs other code. "Before" is found by a clock both
    runs keep alike: the moves between the function's own code and the rest (calls out of it, returns
    into it, counted from its first block; in the rebuilt image its C's place from placement.json) and
    the entries of the log (stub calls, device accesses), which right C makes in the same order;
    between two ticks the images may order their instructions and stores otherwise, so a store is
    placed by the last tick before it. The original's run is watched to the event (its log's length
    then, its ticks before it, the bytes outside the stack as they were at the last of those ticks);
    the rebuilt image's run again, to the same tick. Explained: every register at the return (it is
    made at the end); the log from the first entry the original made after the event; a byte outside
    the stack that either image wrote after the last tick before the event. Not explained: the log's
    entries before the event (a stub call's arguments); a byte whose final value both images had at
    that tick (a store elsewhere); a rebuilt run that faults (an original that faults after a written
    case's load), unless the rebuilt run got to that tick with the original's log and bytes. A rebuilt
    run that never gets to that tick (it made fewer calls out of the function or log entries before the
    event: it went another way earlier, a return taken too soon) gets nothing explained by the event,
    not even a register, and the FAIL line says how far it got.
  - *Where the stack lies explains the outputs that change in the original when its stack starts
    lower*: with it STACK_SHIFT (64) bytes lower, the run that decides whether the reason holds, and,
    while a difference is left that those do not explain, 8, 256 and 680 bytes lower one after another
    (fragile.MORE_SHIFTS), joined; the log from its first entry that changes. A run 64 bytes lower that
    faults or hangs stands for itself up to where it stopped (the log from where it parts, the bytes it
    left otherwise or not at all, every register); a further shift's run that faults adds nothing (a
    deep case run off the stack's end says nothing about the code). Why more than one shift: a stack
    address may pick one of a few outputs, and one shift can leave alone the one the C's frame picked
    (FreeRTOS's queue set test seeds a random generator with the address of a local, which picks the
    counter each send adds to: on RISC-V GCC -Os 39 of prvQueueSetSendingTask's 41 loop cases failed
    with the one shift, the third counter left alone; with the four, none).
  - *Stale stack data* (compared except, not refused): the outputs the fills move, as before, but the
    log from the first entry a fill moves; the entries before it are compared.
  - Reasons join, checked in the old order while a difference is left (code, wild call, stack, stale
    data, saved registers): a case is refused when the reasons that hold together explain every
    difference (a memcpy that reads a saved register past a local, in a function whose queue items
    carry a stack address: FreeRTOS's vTimerPeriodicISRTests), compared except the stale outputs when
    those alone do, and fails otherwise. The REFUSED line now says what its reasons explained (`(it
    differs only after the touch: r4 after the return)`).
- **Why.** A refusal says that the case's input is one the C cannot be held to; that holds for what
  its reason reaches, not for the whole case. For an event, time is the boundary that is sound without
  the C's frame: what the touched registers decide cannot be told from the original's run alone, since
  the C's overflow lands elsewhere and corrupts other things; but nothing the C does before its own
  overflow depends on its layout. For the stack's position the original's own runs tell what it
  decides, without a moment: a stack address may be taken long before it matters. Option (c) would
  lose functions whose refused cases are all honest (FreeRTOS's queue set and timer tests: a stack
  address seeds or is sent), and (a) let wrong C through.
- **Measured** on the eight builds (gizmo a32, m3, m4, rv32; FreeRTOS mps2-gcc-Os, mps2-clang-O2, the
  RISC-V GCC -Os build, mps2-gcc-O0), the lift check and then the readable gate, before (5eaeefc) and
  after, from the same cached cases: the functions accepted are the same on every build in both steps
  (gizmo 36, 36, 44, 32 lifted and 35, 36, 44, 31 readable; FreeRTOS 341, 300, 272, 382 lifted and
  328, 288, 265, 378 readable), the lifted and readable tables byte for byte the same, and so are the
  refused cases (87, 32, 60, 175 in the lift check's last rounds, 45, 25, 16, 14 in the gate's) and
  those compared except stale data (8, 8, 10, 35; 20, 14, 5, 18). Some refusals name other reasons now:
  the stack and a saved register where the stack alone had refused them (vTimerPeriodicISRTests, whose
  memcpy reads one: 25 cases on mps2-clang-O2 and on mps2-gcc-O0 in the lift check, 25 on mps2-clang-O2
  in the gate; prvSanityCheckCreatedQueue, which writes one: 1 on RISC-V in each step), and the saved
  register alone for 6 of prvSanityCheckCreatedQueue's cases in mps2-gcc-O0's gate, where the stack
  moves none of their differences. No case fails for a difference its reasons leave: the stricter rules
  found no fault in these builds' C. Two functions newly failed on the way and were traced: fflush on mps2-clang-O2 (accepted
  before) and prvSingleTaskTests on the same build (dropped either way), through a fault of the first
  version (the event's tick was counted to the run's end); and prvQueueSetSendingTask's loop cases on
  RISC-V, through the one shift (above). The lift check of mps2-gcc-O0 took 134 s against 137 s before,
  side by side on one machine: the extra runs are made only for cases a reason holds for.
- **Not established.** (1) The stretch between the last tick and the event is not compared: a store
  the function's own code makes there counts as after the event, and a function whose own code touches
  its saves before any call or log entry has nothing compared before the touch but the log (a finer
  clock, docs/todo.md). (2) Time is wider than what the event decides: a difference made after the
  touch but not by it is explained too. (3) C that makes other calls than the original (a helper of
  its own, a call the original inlined) would put the clock out of step: such a case fails (the safe
  side); none on the eight builds. (4) Four shifts can miss what depends on the stack's position in a
  way none of them changes (such a case fails: the safe side). (5) dread integration's own stack check
  leaves out an input whose builds differ when the shift moves anything at all, the same hole: not
  changed here (docs/todo.md). (6) The private test's function was not run against these rules here
  (rule 1): that its cases now fail rests on the public reproduction, a stub call's argument made before
  the touch.
- **Undo.** Revert the commit: runner.judge back to the first reason that holds refusing the case,
  stale_outputs' log one output.
- **Status.** Done (2026-10-24): tests/test_runner.py has each reason's reproduction with wrong C
  (another value passed to a stubbed callee before the touch, before the call, before the load, where
  the stack moves nothing; a store before the touch; a return before the touch; a stale case's first
  log entry) that the old rules accepted and these fail, beside right C they still refuse, and C that
  does otherwise only after the touch, which they refuse too.

## Stack arguments through a bound call; a callee whose stack pointer is not static (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** Two refusals of the lifter, found on a private real-life target (its figures stay
  with it): (1) a call of a function that reads a register its caller
  leaves (r4-r11), or whose result the caller reads behind the call, and that also takes stack
  arguments, was refused, since Lifter._bound_call passed no stack arguments; (2) a call of a function
  whose stack pointer is not static (sp set from an auipc or from a register the analysis does not
  know, a variable-length array's sub sp, sp, rN; a load of sp from memory, a context switch, was
  followed already) was refused for every caller, since its stack arguments were not known
  (stack_args None). How are such calls made?
- **Options and choices.**
  1. A bound call with stack arguments: (a) the asm statement reserves the stack-argument words below
     the stack pointer, stores them there and restores the stack pointer after the call; (b) a thunk
     per callee that takes them as C arguments and makes the bound call; (c) wrap the caller (its frame
     on the real stack) and point the stack pointer at its outgoing area for the call. **(a)**
     (_bound_call): the values are those a normal call passes (_stack_arg_values, from the caller's
     outgoing area, its own stack parameters or a wrapped frame), handed to the asm as inputs, and
     the stores go where the callee reads them whatever the C's frame looks like; the stack stays
     8-byte aligned at the call (16 on RISC-V). (b) adds an object per callee and a second call; (c)
     changes the caller's whole frame for one call, and a callee's frame would overwrite what the C
     keeps below the outgoing area. frame_effects counts the words (call_stack_words), so saves_out
     keeps the outgoing area in the caller's frame. Thumb-1 is refused with its own reason (its str
     needs a low register, which an input operand may not get); RISC-V's normal calls still carry no
     stack arguments, its bound calls now do.
  2. A callee whose stack pointer is not static: (a) infer its stack arguments from its code and accept
     a caller whose stores agree; (b) declare them per function ([abi] stack_args); (c) leave it
     refused. **(a)**, (b) staying as it was for what the code cannot tell. scan_stack_args walks the
     function as states() does but goes on where the stack pointer is lost (_tolerant_states: the
     depth becomes unknown, the registers that hold a frame address keep it, since a frame address is
     relative to the entry's stack pointer, which does not change) and counts the words read above
     the entry: through sp before it moves, through a frame pointer after. Past that point no access
     through sp is a stack argument (it is on another stack, or at an offset not known). The caller
     must store each of those words into its outgoing area on its own path (_caller_provides,
     vacuous for none; every whole word a store covers counts: a str or sw, the words of a push or an
     stm, both words of an strd, which GCC 7 at -O2 stores two stack arguments with): where the two
     agree the caller is accepted, and the callee stays refused for
     its own stack pointer. The cases decide the rest: a count that is too small or too large reads
     another word where the callee reads its argument. The walk is states() in all else (a conditional
     instruction keeps only what holds either way, a conditional return lets the code behind it run
     unpopped, a load of sp from memory ends its path); where a path reaches an instruction that does
     not decode, which also makes stack_args None, the code behind it is not seen and the stack
     arguments stay not known (rule 7): those callers stay refused, now with that reason. A first
     version took such a callee for one without stack arguments; on the public builds its six callers
     (the register-test tasks' entries, whose callee keeps an inline marker that does not decode) then
     lifted and even passed the lift check, but nothing had seen the code behind the marker.
  3. What it takes from a comparison (rule 6): nothing. The cases compared every register the callers
     read after the call already; the stack arguments are compared through what the callee does with
     them (its real code runs in the cases).
- **Figures.** Programs of our own (tests/test_stack_args.py, and tests/test_lift_results.py's
  quad_fifth_user and RISC-V use, which the old gate refused): refused before (the old gate restored
  by a monkeypatch), accepted after through the lift check (every case agreeing, every instruction
  run): (1) on ARM state and Thumb-2 with GCC and clang (a callee that reads r5 and takes a fifth
  argument) and on RISC-V with GCC and clang (quad's use); (2) on RISC-V with GCC and clang (a callee
  that sets sp from auipc) and on Thumb-2 with GCC -Os and -O2 (a variable-length array with six
  arguments, its fifth and sixth read through r7; a caller that stores both stack arguments with one
  strd). A stack argument stored at the wrong offset, or the two read swapped, is dropped; a callee
  whose code does not decode keeps its caller refused. The same tests pass with Ubuntu 22.04's and
  Debian 10's compilers (bin/with-toolchain); there GCC 7 at -O2 showed the strd, which the first
  version of the agreement did not count, and a mutation whose swap the cases did not see (the callee
  used e + f, and inputs could make them equal: it now returns 2e - f with f = e + 1). The 39 FreeRTOS builds: 6 of 13,684 functions go from refused to lifting, all through (2)
  (xPortStartScheduler on the five clang RISC-V builds, whose callee sets sp from an auipc;
  freertos_vector_table on clang 14, calling the exception handler, likewise); none from lifting to
  refused; every other function's C byte for byte as before; six refusals keep their verdict with the
  reason made exact (the register-test entries above: "its code at ... does not decode"); gizmo's
  lifting unchanged on the seven builds DREAD lifts. (1) has no public case. Lift checks with the system
  run and the whole image, the code before against this: GCC -Os 341 -> 341, clang -O2 300 -> 300,
  RISC-V GCC -Os 272 -> 272, RISC-V clang 14-Os 259 -> 260 (xPortStartScheduler; freertos_vector_table
  lifts and the check refuses it as code only the core enters, Lifter.entered_unseen), RISC-V clang -Os
  257 -> 257 (xPortStartScheduler lifts, and its generated cases fault: its callee goes on on a task's
  stack; dropped), nothing lost on any and the whole image agreeing on all five.
- **Undo.** In _bound_call refuse when nstack is set; in Lifter._call and RiscVLifter._call refuse when
  stack_args is None instead of calling inferred_stack_args, and use stack_args again in
  frame_effects for call_stack_words.
- **Status.** Done (dread/lift.py, dread/lift_riscv.py; dread/lift_msp430.py has its own calls and is
  unchanged).

## Readable C's two-word results, callers of a function that fails, save areas behind a frame copy (2026-10-24)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), from regressions on the
private real-life test: after the readable C changes of 2026-10-23 the readable gate kept functions lifted
that had been readable the day before.

- **Question.** What made those functions fail, and how is each cause mended without taking from what
  the gate compares? Three causes were found, each traced to the first instruction where the images
  part (the private target's notes have the functions):
  1. **A two-word result used as one word.** The lifters declare a callee whose callers read r1 after
     it u64 (a division routine's quotient and remainder) and take its first word with (u32). Since
     "flags as comparisons" (f6757bd) loads_once lets a call be a value's only effect, so the values
     pass writes r0 = f(...) for r0 = (u32)f(...), which is right for the assignment; fold then wrote
     that value into its one reader as it was (if (x <= f(...)), a store of f(...) >> 1): 64 bits, the
     remainder as the high word. The proofs that would have noticed took every call for unsigned int
     (cexpr.abstract), so the conditions search also turned x > (u32)f(...) into x > f(...) by itself.
     The gate failed such functions where their cases gave a remainder, and with them every caller
     whose cases ran them for real; where no case did (a comparison with 0 that no case reached with a
     quotient of 0 and a remainder), it accepted the wrong C, and a caller's cases found it.
  2. **Every caller of such a function went back with it.** The gate dropped all that failed in a
     round; the lift check retries a function that failed through a failing callee (RETRY_PASSES),
     the readable gate did not.
  3. **A save area behind a frame copy.** A variadic function of an APCS frame keeps the entry's
     stack pointer first (mov ip, sp; push {r1, r2, r3}; push {r4, ip, lr}); entry_save_areas looked at
     the first instruction only, so its callers' calls were trimmed to what regflow says it reads (its
     va_list reads more: the cases logged the third argument), and since 1404c72 push_room took its
     own pushes out.
- **Options.** (1) (a) keep the (u32) in the values pass; (b) fold writes a value as its assignment
  converted it; (c) the proofs type a call by its declaration. (2) (a) as the lift check: drop, and
  retry bottom-up after a round without failures; (b) keep a failing function whose code reaches
  another failing one in the next round, with that one from lifted C. (3) (a) regflow counts pushes
  of argument registers as uses; (b) entry_save_areas looks past a few instructions in front of the
  push.
- **Choice: (1) (b) and (c), (2) (b), (3) (b).** (1c) makes the proofs model C, so every rewrite that
  has a call in it is decided right, not only this one: Ctx.returns holds the file's declarations
  (readable.Run.returns, handed to the passes through the tidy run: tidy._RUN.returns), a call
  through a cast pointer has the cast's result type, an unknown callee unsigned int as before. (1b)
  fold writes the value cast to its variable's type unless C gives it that type already (c_type),
  every read is one the conversion cannot change (a truth value of a value no wider than the
  variable, the whole of a return or of an assignment of the variable's type), or z3 proves that the
  reader gives the same with the bare value (same_reads: an if's condition, an assignment's or a
  store's value as its target's type has it, a return's value; tidy.as_assigned). (1a) alone would
  leave fold wrong for any value of another type than its variable (a byte loaded, a comparison's
  int, a select). Without the proof, a cast went in front of loaded bytes and halfwords that never
  needed one, and a two-word comparison's carry ((u32)(r3 >= 5)) no longer paired into one 64-bit
  comparison (FreeRTOS GCC -Os prvSanityCheckCreatedQueue). With it the readable C of seven public
  builds is byte for byte what it was; on FreeRTOS GCC -O0 two functions get back a conversion fold
  had dropped, both before and after accepted by the gate (prvTest3_CheckAutoReloadExpireRates:
  (u8)(s28 - 1), a u8 local's wrap-around; __NVIC_EnableIRQ: (u32)(s8)s9 >> 5, CMSIS's unsigned shift
  of the IRQ number, where (s8)s9 >> 5 shifted arithmetically: equal only for the non-negative
  numbers its guard lets through). (2b) costs no round the gate would not run anyway (it runs again
  after any failure), where (2a) adds at least one; the last round still runs every accepted
  function's cases with everything as it ends up, so nothing is accepted that the gate did not see
  agree. Not where every function that failed reaches another (recursion): then all go, as before,
  and every round sends one back at least. (3b): pushes stay no use to regflow (right for the lifter,
  which passes every register); the frame copy writes neither sp nor an argument register.
- **Not mended.** Written cases whose stubs log an argument register their callee does not read (an
  empty function, the fourth register of a callee that reads three) fail readable C that passes only
  what the callee reads, wherever the C compiler leaves another value there (it had left the
  original's before, by chance); the comparison stays (rule 6). On the private target such cases came
  from its earlier project; their stub masks are the target's to correct.
- **Undo.** cexpr.Ctx.result_type returning (int_bits, False) takes (1c) back; tidy.as_assigned
  returning value takes (1b) back; the check's again set staying empty takes (2) back;
  SAVE_AREA_LEAD = 0 takes (3) back.
- **Status.** Done (docs/todo.md, the item, with the figures).

## Readable C's frames: an object at each address handed out, volatile only where an address may escape (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20; the item in docs/todo.md,
"From a review against hand-written C": Local objects whose address goes to a callee; with part (1) of
"No parameters from pushes").

- **Question.** dread.frames wrote the part of a frame that addresses handed out may reach, from the
  lowest of them up to sp0, as one volatile object: gizmo's print_status (`char buf[12]`) read as
  `volatile stack20_t stack20` with three u32 fields over the buffer and the saves of r4 and lr behind
  it, its four phantom parameters the push's stores into those fields. How is the region split into
  the function's locals, typed, and when may they be plain objects; what becomes of the push's
  stores and the save words?
- **Options.**
  1. Where objects start: (a) one object, as before (no callee is trusted to stay in its piece);
     (b) at every address handed out (to a callee, into memory, as the result, to assembly; through a
     variable that holds it; a sum's with an index), up to the next one and below the save words,
     joined where one access spans two, where a variable walks downward from an address inside one,
     where an index is added to an address no object starts at, and over a callee structure's size;
     (c) at every frame address the function uses as a value, also one it only keeps in a variable
     or compares: tried first, it cut gizmo rv32's fmt_dec at its tmp - 1 (clang indexes tmp[--n]
     from there), and the cases failed; (d) by the function's own runs of accesses alone: misses
     separate locals it never accesses itself (apply_config's key and line, filled by callees).
  2. What an object is: by its accesses (a scalar where one access fills it, bytes where only bytes
     or nothing is accessed, a structure for mixed ones), the width an index or a callee's parameter
     (the readable_types table's prototypes) says, a callee's structure where its parameter points to
     one of dread.shapes; or always a structure of its own.
  3. An index into an object: (a) an element (stack48[r3]: past the array undefined in C, and GCC
     derives loop bounds from such accesses); (b) arithmetic on the address, as before.
  4. Volatile: (a) always, as before; (b) never; (c) only where an address may escape into memory.
  5. A callee that reaches past the object it was given (a generated case's queue copies another
     item size than the local holds: clang -O2's prvSendFrontAndBackTest and
     vPrimaryBlockTimeTestTask, every case different and then refused for the saved registers it
     reached): (a) never split where a callee's reach is not bounded by its type (most of the demos'
     locals go to queue and string functions, untyped); (b) split, and where a function's cases
     reject its objects, try it once more with its region as one object (the readable_types table's
     whole), then lifted as before.
- **Choice: 1(b), 2 by accesses, width and callee's structure, 3(a), 4(c), 5(b).** Each object is what
  C's object model makes of a local whose address a callee gets: the callee may reach that object
  and no other. The original reached past it only by a fault of its own (a length past a local), or
  in a state a generated case made up; the cases refuse an input that makes code touch saved
  registers, and the gate checks every function on its cases (every instruction run) and the whole
  image, as for every other transformation; where the cases see a callee reach past an object, 5(b)
  keeps the function readable as it was. 3(a): the lifted C's arithmetic on the frame array is no
  safer by C's rules (GCC documents that integer arithmetic on a pointer is no way around the
  undefined behaviour of an index past its object), only on a larger object; whole functions keep the
  arithmetic. 4(c): an interrupt or another task reaches a local only through its address, so only
  where the address may escape into memory: the function stores, returns or hands it to assembly or
  through a pointer, or a callee that may keep it gets it (kept_params over every lifted function:
  a parameter is kept where a value derived from it is stored, handed to assembly, through a
  pointer or to a callee that keeps it; one returned is followed into the caller's result, memset's
  destination; a callee not lifted, variadic or naked keeps everything). Volatile also stays where
  the function accesses the object through a variable with another type than bytes (strict
  aliasing: a compiler may carry a member across such a store; bytes alias everything). What a plain
  object loses (a value kept in a register between calls, accesses no call separates merged, stores
  after the last call dropped) only another context could see, and none has the address.
  The push's stores (dread.tidy push_room): a correct program reads a local only after writing it,
  and GCC writes a local's first value with a store of its own, never through the push that made
  room (push {r0, r1, r2, lr}; str r0, [sp, #4] for an address-taken parameter); a word the function
  loads directly stays, unless its statements store over every loaded byte before anything may read
  it. The save words (the entry's stores of callee-saved registers and the return address before
  anything writes them, through dread.regflow's Frames): no local's bytes; their stores go (RISC-V's
  lifted C kept `stack20.f_10 = ra;`), the cases refuse an input that touches them.
- **Found on the way.** (1) On the GCC builds newlib's printf and sprintf push their argument
  registers (push {r1, r2, r3} first), which variadic_reach did not find: calls of them were trimmed
  to what regflow says they read, so FreeRTOS's GCC -Os tasks called sprintf(buf, fmt) without the
  value, and the whole image had agreed with two of them only by what the C compiler left in r2 (the
  base run dropped both by halving; dread.readable entry_save_areas: such a call keeps all its
  arguments, not propagated to the callers' callers: vAssertCalled's stay trimmed). (2) clang -O2 adds
  an index to sp0 before the constant (sp0 + r5 - 0x14u): frame_locals and dread.frames took the bare
  sp0 for an address at sp0 and left the whole frame raw (dread.tidy index_sum).
- **Undo.** dread/frames.py: plan starting objects at the region's start alone and _decide returning a
  structure or bytes (or whole_frame returning True) gives the one object; _volatile returning at once
  keeps every object volatile; element returning None keeps the index arithmetic; dread/tidy.py
  push_room returning False keeps the push's stores; Context.saves returning {} keeps the save words;
  readable.entry_save_areas returning set() takes (1) back; the check's whole set staying empty drops
  the fallback.
- **Status.** Done (docs/todo.md, the item, with the figures).

## Names that survive changes of the C: carried by the variables' definitions (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20; asked for on 2026-10-23
after the day's six changes of readable C had dropped every variable name of a private firmware).

- **Question.** The readable_names table kept a function's names with a digest of the C they were made
  for, and applied them only to that C: a name on r4_1 must not land on another variable when the
  transformations number the webs anew. So every improvement of readable C made every changed
  function's names wait for a new pilot run, which costs, and whose names differ (two runs on the same
  C of FreeRTOS's GCC -Os build named 36 % of 786 variables alike, 64 % with a word alike). How can a
  name follow its variable to C made anew, where that is safe?
- **Options and choices.**
  1. A variable's identity: (a) its name (r4_1): the webs are numbered anew whenever one appears or
     goes (prvBlockingTask's r0_20 is r0_10 today); (b) its uses or the text of its statements, which
     are what the transformations change; (c) a parameter by its position (the head lists the argument
     registers in order, dread.tidy params keeps them so), a local by the register or frame slot it
     lives in (its name without the web numbers: s36 is the slot sp0 - 36) and the instructions that
     write it (each assigning statement's own instruction, the one dread.tidy keeps when it folds
     others in; what it absorbed is left out: that changes with every clean-up and is not recorded by
     code from before the reading form). **(c)**: two locals with the same register and the same
     definitions hold the same values, whatever the transformations did around them. The register is
     part of it: one instruction can write two registers (a load with writeback, a chain split into one
     assignment each), and without it 38 fewer locals matched on the measurement below.
  2. The match: (a) identical definitions only; (b) the local of the same base whose definitions it
     shares most, more than a bar (Jaccard), each the other's only best match (a web split in two equal
     halves gets nothing: which half the name meant is not known). **(b) with the bar at more than half
     (CARRY_OVERLAP 0.5)**, measured on FreeRTOS's GCC -Os build, every variable of every function whose
     C changed between an older commit and main, each match judged against the debug information (the
     source variables whose location list puts them in the register after each definition, until it is
     written again): from before the six changes (d7a8230) 117 of 329 functions changed, 208 parameters
     and 825 locals matched at any bar from 0.99 down to 0.5, every local with identical definitions,
     115 of them renumbered; 240 judged right, none wrong, 585 unknown (no source variable in that
     register there: temporaries). From the day before (0d18f0b, 211 changed) and from the first named
     C (9ad5dec, 2026-10-21, 237 changed): 4 partial matches more than half (2 right, 2 unknown). No
     bar gave a match judged wrong: at least half instead adds 6 partial matches from d7a8230 (all
     right) and 21 from each of the older two (11 right, 14 unknown), a bar of 0.3 one and nine more. The
     partial matches are mostly a variable that kept one of its two definitions (the other value no
     longer kept), which the debug information calls right where it can tell; the stricter bar is
     kept all the same, since a name dropped costs a question (4) and a name on the wrong variable is
     what no check catches. Applying the old maps by the variables' names instead puts 9 of 207 judged
     names on a wrong variable (14 of 303 from 9ad5dec).
  3. Where: (a) at every application of the table (every build, every view: the compile check would
     run each time); (b) once, a step that writes the entry anew for the new C. **(b)**: `dread readable
     --check` carries at its end (the step that runs after the transformations change: its digests tell
     which entries need it), `--carry` alone, and `dread pilot --task readable` before it asks. Each
     carried name passes the identifier check again (usable: a name the new C uses otherwise is
     dropped alone), and the C with them must compile to the same code (same_code; else the entry stays
     as it was and waits). The entry says it was carried, from which digest, what was dropped and which
     variables of the new C are open (new, or their name refused); its comment says "carried from
     earlier C", which the reading form and the GUI show.
  4. Asking again: (a) never; (b) every carried function; (c) only one whose carry left variables open,
     its question showing the carried names in its C and asking for the others. **(c)**: the carried
     names were as close to the source's as a fresh run's (below), (b) costs what a fresh run costs and
     changes two names of three for nothing, and (a) leaves new variables with register names.
  5. Every variable's identity in the entry, not only the named ones', one string each ("param 0", "r4
     0xcad2 0xcadc"): it tells a new variable from one the model left unnamed. The table of the
     measurement grew from 208 to 266 KB (dictionaries would have made 361 KB and a line per field).
- **Measured** (a copy of FreeRTOS's GCC -Os build, Haiku 5.5, --jobs 16): every readable function
  named with the code of d7a8230, before the day's six readable-C changes (315 functions, 1,788 names,
  $0.18), the identities its C had added to the entries (what the pilot now records), then the table
  applied with main's code: 204 of 315 entries apply (813 names), 111 wait (975 names). With this, on
  main's C: `dread readable --carry` (10-14 s, no model) carried 108 of the 111: 814 names, 177
  parameters and 637 locals, every local with identical definitions, 111 of them now on a variable of
  another name; 161 names dropped: 154 whose variable is gone (11 parameters no longer read, 143
  locals holding values no longer kept, such as vTaskDelay's r0_1 and all of main_full's,
  vApplicationTickHook's and vFullDemoTickHookFunction's, which have no variable left), 7 whose
  variable kept half its definitions or fewer (one of two, mostly; the debug information calls all
  seven candidates right: the price of the bar); none refused by a check. 312 of 315 entries apply
  (1,627 names). Of the 975 names the old maps applied by name would have put 84 on another variable
  and 188 on none. Against the debug information every judged carry is right: 164 parameters (13
  unknown) and 225 locals (412 unknown), 62 of them renumbered. Against the source's names (dread
  readable --score, the changed functions): the old names on the old C 100 of 172 parameters as the
  source (58 %), 135 with a word of it (78 %), 82 of 711 local names among the source's; carried, 95
  of 167 (57 %), 134 (80 %), 67 of 558; a fresh run on main's C (315 functions, $0.17), 79 of 169
  (47 %), 127 (75 %), 57 of 600. Per variable (a local by the source variables in its register):
  carried 57 exact and 100 alike of 225 judged locals, fresh 50 and 107 of 258. The unchanged
  functions show the runs' own spread (51 % and 53 % of 203 parameters). The pilot after the carry
  asked 18 functions (15 with 33 variables open, the three without variables), $0.01 instead of
  $0.17, 49 s instead of 172; the 15 kept all 158 carried names and named the 33.
- **Not established.** That a partial match is the same variable (few such matches: 4 above the bar
  from the oldest C tried, none judged wrong, two unknown); most locals have no source variable to
  judge them by. Carrying depends on dread.tidy keeping each statement's own instruction: a change of
  that makes names wait, as before, not land elsewhere. A frame slot whose prefix changes (s36 to
  sl36, when a name of the function takes s36) loses its names. The structures' names (the table's
  structs part) still follow their anchors, function:variable pairs whose variables can be renumbered
  too.
- **Undo.** Take the carry out of dread readable's check and of dread pilot's readable_jobs; the entries
  then apply by their digest only, as before (their ids and carried are ignored).
- **Status.** Built (dread.naming identities, carry_map, carry, carry_table; dread readable --carry and
  the end of --check; dread pilot --task readable).

## Pointer and signed types for variables and parameters; externs typed where a file's calls fit them (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20; two items of docs/todo.md,
"From a review against hand-written C": Pointer types for parameters and prototypes, Signed types from
signed use).

- **Question.** Readable C declared every register u32: a parameter only dereferenced and advanced
  read `*(volatile u8 *)(r1_1 + 1)` at every use, an argument for an all-u32 extern `(u32)&g_x[0]`, a
  counter compared signed `(s32)r2_1 > 4`. Which variables and parameters get which types, how are
  externs written, and how is the C kept doing what it did?
- **Options and choices.**
  1. Which variables: (a) every variable dereferenced at one width; (b) groups of variables that copy
     one another or add a constant or an index to one another (one type a group), each group kept
     where the function's C with it holds no more casts than without (a trial: the rewriter on the
     statements that name the group). **(b)**: (a) types a pointer loaded from memory and used once at
     the price of a cast where it is loaded, and a group's members typed apart would meet with casts.
  2. Volatile: (a) `volatile T *`, every access through it the one volatile access it was; (b) plain
     `T *`. **(a)**: the Memory item (whether accesses to RAM need volatile) stays open, as the item
     asked; dread.shapes' structure pointers are volatile too. `const` where nothing is stored through
     the group (the review's `const s8 *`), s8/s16 where every access is a signed load.
  3. Externs: (a) every caller's extern as the callee's own readable C declares it (the item's
     wording); (b) per file: a callee's parameter typed where the file's calls pass what C takes for it
     without a cast (a pointer of that type, the address of an object of it, 0), else u32 as the lifter
     wrote it; the variables a function only hands to such parameters typed with them (a trial too).
     **(b)**: (a) measured on FreeRTOS GCC -Os (the same code otherwise): casts with raw accesses 1,250
     -> 1,588, pointer casts 147 -> 858 (queue handles held in u32 globals, texts' addresses, callees'
     results: a cast at every call), while (b) took them to 1,055 and 216. A callee's result type is its
     own everywhere; a function that calls itself declares itself as its head (C takes no other).
  4. Signed: a variable compared in order only signed is s32 (a trial: kept where no more casts), a
     result s32 where a caller compares it signed: (a) any caller variable typed s32 that the call
     assigns; (b) a variable every definition of which that reaches the signed comparison is the call
     (dread.cflow's reaching definitions). **(b)**: (a) made gizmo's led_on and beep_off s32 through
     handle_key's r0, which holds its parameter and then a callee's leftover.
  5. Where it is decided: (a) at each build on the function's final C; (b) in the readable_types plan,
     made by the check over every lifted function before any is checked, its trials on copies of the
     analysis trees with the static data's objects and dread.shapes' structures applied (finished).
     **(b)**: callers need the callees' parameter types; the trials on the plain analysis trees had seen
     `0x200017c5u` where the build has `(u32)&g_200017c5[0]` and typed no extern.
  6. Keeping the behaviour: (a) arithmetic kept in u32 with casts around every pointer and s32 use;
     (b) a rewriter that writes every expression for its value (an integer of the lifted C's type, or
     only its low 32 bits where only they count) or its address (pointer arithmetic in elements: + 4 on
     a u32 pointer is + 1, a shifted index the index; an offset that is no whole number of elements
     keeps the integer arithmetic), with -fno-strict-overflow for files with typed variables or pointer
     arithmetic (dread.build WRAPS: signed and pointer arithmetic wrap, as the lifted u32 arithmetic
     did) and -fno-delete-null-pointer-checks (NULL_CHECKS) for typed pointers. **(b)**: (a) would read
     no better. A structure pointer's advance by its size, dread.shapes' `(volatile struct s *)((u32)p
     + 12)`, is p + 1 too (where the file defines the structure: a declared one has no size). The
     address of a variable of the function stays an integer, and a variable given one stays u32: a
     first version wrote the lifter's stack arguments as pointers, and the gate turned four functions
     of clang -O2 down (xTaskCreateStatic read the argument after s1 through `a_1 = &s1; a_1[1]`, which
     the compiler may take for never: dread.shapes keeps such variables untyped for the same reason)
     and one of GCC -O0 (xEventGroupWaitBits' `*(volatile u32 *)(u32)&s0 = 0`, the store at the
     argument's slot, had become `*&s0 = 0`, which the compiler may keep in a register).
  7. dread.polish: a pointer cast of a pointer taken through a word, `(T *)(u32)&stack28.f_0` (dread.frames
     writes the frame's address inside the rewriter's cast), is `(T *)&stack28.f_0`, and the lifter's
     stack argument `*(volatile u32 *)(u32)&s0` is `*(volatile u32 *)&s0` (the same volatile access); an
     integer cast of a pointer keeps its (u32) ((u64)p would extend the address by its sign).
- **Measured** (copies of the eight builds: gizmo a32, m3, m4, rv32; FreeRTOS mps2-gcc-Os,
  mps2-clang-O2, virt-gcc-Os, mps2-gcc-O0; main against this, the same inputs). The gate accepts the
  same functions, name by name: 35, 36, 44, 31, 315, 275, 252, 354. Over every lifted function's
  readable C (a script, not in the repository: declarations, casts and raw accesses counted on the
  trees as built): parameters typed as pointers to scalars 0 -> 14, 14, 15, 15, 63, 57, 47, 58 and such
  variables 0 -> 22, 17, 17, 19, 61, 35, 25, 32; structure pointer parameters (dread.shapes' and now
  those handed on) 70 -> 86, 53 -> 57, 57 -> 66, 63 -> 71 on FreeRTOS (gizmo none), such variables
  126 -> 131, 123 -> 130, 70 -> 74, 144 -> 145; signed parameters 0 -> 2, 2, 3, 2, 5, 3, 4, 3 and
  variables 0 -> 2, 2, 3, 5, 21, 23, 10, 24; raw accesses through variables 33, 34, 35, 35, 206, 248,
  147, 212 -> 6, 13, 13, 3, 64, 94, 72, 100; casts with raw accesses 75, 67, 71, 69, 1,250, 1,088, 714,
  999 -> 46, 43, 47, 40, 1,055, 891, 596, 783; casts at uses (raw accesses through variables, (u32) and
  (s32) of a variable, pointer casts) 48, 48, 52, 43, 681, 642, 443, 573 -> 19, 24, 28, 13, 539, 501,
  345, 453; (s32) of a variable 7, 7, 8, 7, 28, 32, 16, 32 -> 0, 0, 0, 0, 1, 5, 1, 1; (u32) of a
  variable 7, 6, 8, 1, 300, 219, 158, 156 -> 12, 10, 14, 10, 258, 216, 127, 138 (a pointer returned
  as the u32 result, handed to a u32 parameter); pointer casts 1, 1, 1, 0, 147, 143, 122, 173 -> 1, 1,
  1, 0, 216, 186, 145, 214 (an integer given to a typed variable, a structure's pointer given to a
  scalar one); externs with a type 1, 0, 0, 0, 0, 0, 0, 0 -> 4, 3, 3, 2, 115, 58, 94, 68 of 46, 45,
  49, 39, 945, 773, 661, 941; lines 570, 569, 681, 620, 8,833, 8,183, 6,810, 7,582 -> 582, 580, 691,
  629, 8,891, 8,222, 6,841, 7,620 (a declaration line per type). Every function's readable C of FreeRTOS
  GCC -Os in one process 43.8 -> 48.8 s of CPU (the machine shared); the plan 1.4-1.7 s a FreeRTOS
  build; the check with warm caches (main run again on the same copies) 5, 4, 4, 3, 299, 49, 20, 80 ->
  5, 4, 4, 3, 308, 48, 21, 72 s, noise on a shared machine. The structures' scores against the source
  (dread readable --shapes --score, --statics --score, --score with each one's table) the same on the
  four FreeRTOS builds, line for line.
- **Not established.** That a group is one object (a copy into a variable that then holds something
  else is one group: the tidy's webs mostly split those); pointer results (memcpy's destination,
  returned twice, stays u32 by the count of casts); globals that hold pointers (a queue handle in a u32
  global, so its calls keep u32 externs); an older clang's -fwrapv-pointer (clang 23 sets it); an access
  past a structure through its pointer stays raw; whether const is what the source declares where a
  function only reads through a pointer it hands on (const only from the function's own stores and the
  callees' parameters as typed).
- **Undo.** Take vartypes.apply out of readable._named and _question_one and vartypes.plan out of
  readable.check (a readable_types table without `variables` and `prototypes` types nothing); per
  rule: candidates() without the "handed", "signed" or "extern" kinds; Planner.decide_result returning
  False; the WRAPS line's flag in dread.build; polish's pointer-cast rule (Polisher._cast's first
  branch).
- **Status.** Done (dread/vartypes.py; dread.shapes Plan, dread.readable, dread.build, dread.polish,
  dread.naming rename, dread.cmetrics pointers and signed; tests/test_vartypes.py, test_cmetrics.py).

## Flags as comparisons: a flag read through variables, proved where it is read (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** After the IT-block work, flags still printed as 64-bit sums with carry and zero
  variables: where one compare is read by two statements (gizmo's cmd_mode, its ls by a conditional
  store and by the result's select), where an add's carry is tested away from the add (FreeRTOS's
  wake-time overflow test), where a compiler dispatches by successive subtractions, where a
  conditional compare's two flags are read later (gizmo's parse_kv), in two-word arithmetic, and as
  sums through 64 bits whose carry nobody reads (vPortFree's r0 - 8). fold writes a temporary into
  its one reader, and conditions makes a comparison of what it finds there; neither sees a flag that
  two statements read, or one read past a write of what it compares. How to read them, keeping the
  exact semantics (unsigned and signed overflow, ARM's carry on a subtraction, RISC-V without flags,
  the 64-bit sum's top bit)?
- **Options.** (a) Rules per pattern (ls is <=, hi is >, the carry of an add is sum < operand, ...)
  written into the readers. (b) At each reader, write the flags' definitions into the truth value
  (each variable with one definition that reaches it, its value unchanged on every way there) and
  search for the comparison it is, proved by z3, as conditions does for one reader. (c) Rewrite the
  flags' definitions into comparisons (z = r0 == r3) and leave their readers reading variables.
- **Choice: (b), with (c) where a reader must keep a variable.** (a) would repeat for each pattern what
  the search with proof already does for every combination of flags, and the problem was where the
  flags are read, not what they combine to; the proof decides each rewrite, so no rule has to be
  argued case by case. (c) alone leaves z = r0 == r1 beside if (z): one name more to read, and the
  carry arithmetic of ls (two flags) has no one variable. The pieces, each argued:
  1. **Writing a definition into a reader** (dread.tidy FlagFlow): v's value at node n is its
     definition's value when that definition is the only one reaching n (every way to n passes it and
     no other writes v) and nothing on a way from it to n writes what the value reads (the region: the
     nodes on ways from the definition to n that pass neither it again nor another definition of v).
     Equal sets of reaching definitions, the test propagate uses for copies, is not enough in a loop:
     a way may pass a write of what the value reads and come round to the same set. Flags, 64-bit
     temporaries and, at a second level, their 32-bit results and variables that are another plus a
     constant are written (a chain of subtractions). A load in a definition stands for what it loaded
     there (one text, one load, in the proof); a candidate that reads it is not taken (it would load
     anew where nothing loaded), but a variable that holds what it loaded may stand: the sum of an add
     whose carry is tested (adds r5, r5, r0; str r5; blo as r5_1 >= r5) or the difference of a
     subtraction from memory (subs r8, r1, [r0, #8]; movlo r8, r1 as if (r1 < r8) r8 = r1).
  2. **The proof** is conditions': z3 proves that wherever the written truth value is defined, the
     candidate is and has its truth (a flag's value or a value: its value, as the target's type has
     it). The lifters' flags are what z3 sees, so the carry's meaning is the lifter's: ARM's subtract
     sets c when there is no borrow, and `(u32)(((u64)x + (u32)~y + 1) >> 32)` is exactly x >= y
     unsigned; n and v are the sign and overflow of the 32-bit result, and the signed comparisons z3
     proves against them are those of the two's complement values. RISC-V has no flags: its
     comparisons are sltu and kin, which the lifter writes as comparisons; the passes find nothing
     there (no flag variable or 64-bit sum on the RISC-V builds before or after).
  3. **The sum below an operand.** The carry of a + b (32 bits) is exactly (a + b) mod 2**32 < a;
     with a carry in, <=. Proved per reader all the same.
  4. **A chain of subtractions.** x - k1 - k2 == m is x == m + k1 + k2 (mod 2**32): subtraction of a
     constant is a bijection. An order comparison is not (it wraps), so the constants it would take
     are only offered to the search (flag_numbers) and kept where z3 proves them.
  5. **Two words.** adds lo; adc hi is the 64-bit sum (c:a) + (d:b) + k exactly: its low word is the
     low sum's, its high word c + d + the low sum's carry (pairs; both words proved per instance). The
     high words' operands are read where the low word's sum is, so they must keep their values on the
     way (and a load of them be made where nothing happens between). cmp; sbcs is the 64-bit
     comparison: no borrow out of the high words with the low words' borrow in is (a1:a0) >= (b1:b0);
     such pairs are offered to conditions' search (chained) and proved.
  6. **A conditional compare's two flags** (cmp r5, #0x5f; it ne; cmpne lr, #0x19; read by a select
     and a branch): each flag has two definitions, one in the conditional block's arm; its value at a
     reader is the block's condition ? the arm's : the other's, each part written where it is defined
     and unchanged up to the reader, none reading memory (FlagFlow.chosen). The search then tries
     p && t, !p || t, p || f, !p && f from the comparisons of the condition and of either way
     (_split_search): r5 == 0x5f || lr_1 <= 0x19 for ls, r5 != 0x5f && lr_1 > 0x19 for hi.
  7. **Sinking.** A condition whose operands change before it is read (cmp r1, #9; mov r1, r5; bhi,
     a loop's variable) is computed where its flags are set into a variable of its own, cc_1 = r1 > 9,
     read where the flags were: every way to the reader passes the last of the flags' definitions,
     and nothing writes them between. Only where the condition reads a flag that is not a comparison
     already (z = r0 == r1 read alone would only be copied).
  8. **Sums whose carry nobody reads** (sums): (u32)((u64)a + b + k) is a + b + k (mod 2**32) for
     32-bit operands; ~b + 1 is -b. Proved per instance with C's types, and kept a u32 (a cast where
     the sum's own type is not: a loaded byte is promoted to int). After conditions, which reads a
     compare's own sum better (r0 == r5, not r0 - r5 == 0).
  9. **Which readers.** A reader takes a candidate no larger than its truth value, or of at most five
     nodes where it read a flag or a 64-bit temporary (an atomic comparison: r0 == r1 for z). Two
     rules keep the result readable, not correct (the proof does that): the conditional blocks that
     read one flag's definition (an IT block's, before merge_ifs has made them one) are rewritten all
     or none, since merge_ifs joins ifs by the texts of their conditions; and a flag whose value is a
     comparison already is written into all its readers or none (else the comparison is written twice
     and the flag stays), a conditional compare's own condition aside.
  10. **Also.** conditions looks into calls' arguments and stored values (prvCheckExpectedState(r7 <
     r0_1 ? 0 : 1)); a call that is a condition's only effect counts as one read once (f() - 0x1e == 0
     as f() == 0x1e); value candidates include a negation and equalities (rsbs r0, r1, #0; adcs r0, r1
     is r1 == 0); an unsigned x < 1 is tried as x == 0 first.
- **Found on the way.** dread.tidy webs lost a declaration's initial value (r0 = a0, a parameter read
  elsewhere too) where the value is read at a loop's head that the loop's end reaches as well: the web
  with the smaller first definition kept the name, the declaration's did not and was renamed away from
  its reads. The gate refuses such a function (the eight builds accept the same functions with the fix
  as without it); the random functions of tests/test_flags.py found it. cexpr.Checker evaluated its
  expression on all its test values before the first proposal (thousands for two variables) and tried
  each proposal from the first value: it now evaluates lazily and tries first the value that refuted
  the last proposal (the verdicts are the same in any order): 30 of the random functions took 8.6-10.4
  s of CPU instead of 16.5-19.9 s with the old Checker, the same output. context() made the
  declarations once per name (quadratic in the body); once now.
- **Figures.** (docs/todo.md, the item, has them all.) On fresh copies of the eight builds, main ->
  this: conditions with a 64-bit sum FreeRTOS GCC -Os 23 -> 0, clang -O2 16 -> 4, GCC -O0 15 -> 0,
  gizmo m3 and m4 1 -> 0 (RISC-V and gizmo a32, rv32: none either way); flag variables gizmo a32 7 -> 1,
  m3 and m4 2 -> 0, GCC -Os 18 -> 6, clang -O2 18 -> 5, GCC -O0 4 -> 0; 64-bit temporaries 70 -> 2,
  21 -> 6, 7 -> 2 (what is left on these builds is a 64-bit counter, written as one now); lines down in
  every build with flags. The gate accepts the same functions as main on all eight builds, the same
  sets. Making the readable C costs about a tenth more CPU (clang -O2 45.3 -> 55.1 s summed over the
  functions); the check's wall time moved within the noise of a shared machine except clang -O2's (45
  -> 66 s, two runs each).
- **Undo.** Take ("pairs", pairs), ("flags", flags) and ("sums", sums) out of tidy.PASSES; conditions'
  ExprStmt branch and Call/Assign recursion; value_candidates' negation and equalities; loads_once's
  call; shared_candidates' terms, numbers, chained and the equality with 0.
- **Status.** Done (dread/tidy.py, dread/cexpr.py; tests/test_flags.py, tests/test_tidy.py).

## Readable C as printed: the structures in an image's header, a reading form without the instructions (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** Two items of the review against hand-written C (docs/todo.md): every function's
  readable C repeats the full definition of every structure it touches (FreeRTOS GCC -O0
  prvInitialiseTaskLists: 59 lines of declarations for a 22-line function), and every statement
  carries an instruction comment, one of several where it is made of several (a call often the mov or
  the bx lr after it). What goes into one header per image and what stays with the function, what the
  gate then compiles, which address a reading form without the comments shows, and where the GUI
  shows it.
- **Options and choices.**
  1. What the gate compiles: (a) every file with the header (the header among the build's sources);
     (b) the compiled form as it was, each file defining what it uses, the header only in what is
     printed. **(b)**: dread.build, the gate and dread pilot's checks compile what they compiled. The
     printed form is checked to be the same code: on the eight builds every lifted function's printed
     form, compiled with the header beside it, gives the compiled form's bytes and relocations (GCC
     -Os: 329 of 329).
  2. The header: (a) the structures of the readable_types table, behind pointers and of static data;
     (b) the extern declarations too; (c) the macros of static data too. **(a)**. Their tags are one per
     image by construction (naming.struct_names and static_names: no tag or field takes a macro's
     name) and their definitions the same text in every file. The externs stay with the function: they
     are trimmed per caller (trim_calls keeps an argument in a result register where this function
     reads the call's value), so two functions may declare one callee differently, and one prototype
     would have to fit every call or change what compiles; they are one line a callee and say what
     the function calls. The macros stay: a global's macro name gives way to its default name in a
     function that uses the name for a variable, a label or a callee, and a macro is not scoped, so in
     a header every file includes it would expand inside every identifier of its name. A frame's
     structure (stack36_t) stays: it is that function's frame.
  3. What a function keeps of the structures: (a) the include alone; (b) the include and a comment a
     structure naming the members it uses with their types and offsets; (c) definitions cut down to
     those members. **(b)**: (c) is C that contradicts the header; with (a) the reading form, which
     has no instruction comments, would hide the offsets a reader matches with the listing.
  4. A call's comment: (a) the reader's instruction, as before, where fold puts a call into the
     statement that reads its result; (b) the call's where that statement is an expression statement
     or a return (a condition keeps its branch). **(b)**, as the item asked: 232 of 1,609 such
     statements on FreeRTOS GCC -Os named the mov or the pop after their call, none now. The call is
     the instruction the statement's comment shows (cparse.Addr shown); the statement keeps its own.
     Taking the call's for its own, tried first, regrouped the printer's lines (statements of one
     instruction share a line), and the digests that the readable_types and readable_names tables are
     made for come from that text: five functions of the eight builds got another digest (gizmo a32
     fmt_dec, GCC -Os prvSanityCheckCreatedQueue, clang -O2 vStartCountingSemaphoreTasks, RISC-V
     ulTaskGenericNotifyTake and xTaskGenericNotifyWait), and a target's names for them would have
     stopped applying until asked again; with shown, none.
  5. The margin's address: (a) the statement's own instruction (its comment's); (b) the lowest of the
     instructions folded or propagated into it; (c) that, counting an absorbed instruction only where
     no statement carries it and one statement alone absorbed it. **(c)**: the item asks for the first
     instruction, and (b) gives a literal that two loads share (gizmo's print_status: ldr r4, =base) to
     both statements, the second one's margin then jumping back before the statement above it.
     Recorded through the clean-up in the statement's addr as a cparse.Addr (a tuple to everything
     else, so equality, the printed comments and the digests stay) that fold, propagate, spread and
     addresses add to.
  6. The GUI: (a) a switch on the lifted tab; (b) a tab of its own, first for a function without
     source, asked for when shown. **(b)**: the lifted tab shows what the builds compiled; the reading
     form is the tool's (dread.readable reading), made on the first request outside the server's lock.
- **Undo.** dread readable's main prints transform again (printed, reading and Types unused); fold
  back to `if reader.addr is None` alone (no cparse.take); the GUI's c_sources without the readable
  entry.
- **Status.** Done (dread.readable Types, printed, reading; dread.cparse Addr, first_addresses,
  unparse_lines; dread.tidy fold, propagate, spread, addresses, flat_view; dread.gui reading). Not
  established: a statement in an IT block's arm has no instruction of its own (its if carries it),
  so its line has no address; an instruction the clean-up dropped as dead is no statement's.

## Case keys scoped to a function's call tree (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** The key of a function's generated cases held one environment for the whole image: its
  bytes, the startup copies, the memory map, what dread.regflow found in the whole image (its case
  facts since this morning) and the table of stubs that answer registers beyond a C result (more). A
  change of regflow's findings or of that table anywhere made every case of the image anew: on the private
  real-life test too, for a change that concerned the callers of some functions. What may
  a function's key leave out, and how are calls through pointers taken?
- **Options and choices.**
  1. What is scoped: (a) nothing; (b) what belongs to one function: its entries in regflow's case
     facts, its stub's answers beyond a C result, and (new in any key) its wide_results row and its
     rows of the domains table, for the functions its runs can reach; (c) everything per function.
     **(b)**: the image's bytes, the startup copies and the memory map cannot be scoped (a run reads
     memory, not functions: a moved literal changes what a function reads), nor can the stall cut
     (the engine's) or the learned pointers' slots (the scratch's).
  2. What a run can reach (dread.autocase reaches): its calls, tail calls and branches into another
     function's code; the fall behind its last instruction (a call there whose stub returns falls
     through); the calls the layer marks (a stub that jumps through a register, a switch helper); the
     callees the layer finds held in a register (MSP430); the targets outside the function that a
     run of an indirect jump's dispatch finds (dread.switches: a table of functions, a veneer), where
     the table is in the image (a case's inputs never set it). Everything else is an open transfer:
     a call through a register, an indirect jump no run resolves, one in code that runs outside the
     image or whose targets are words the startup copies put there (memory a case may set), a target
     at 0, code no function holds.
  3. A tree with an open transfer: (a) the callees through pointers as the generator resolves them;
     (b) every function whose address the image takes, with their trees; (c) every function.
     **(c)**: the generator resolves nothing there, a run calls whatever value the pointer holds (an
     input, a word of the image, an address its code forms). (b) needs the addresses the code forms:
     on FreeRTOS's RISC-V builds no function's address is a word of the image (lui and addi make
     every one), so the image's words alone would miss every task; it would take an analysis per
     architecture (dread.relations' flow_references, a few milliseconds a function). And where the
     words do find them, their closure is most of the image: 96 to 100 per cent of the functions on
     GCC's ARM builds (the vector table's reset handler reaches main and every task creator), 45 on
     clang's. (b) could gain only within the trees that are open: 5 to 6 per cent of the functions on
     FreeRTOS's ARM builds, 1.2 to 3.5 on RISC-V, 19 on the MSP430.
  4. Transfers that are none: a system call (svc, ecall: dread.emu runs it as a logged no-op; the
     layers' is_syscall, new); an indirect jump through a register a call leaves its return address
     in (the layer's link_regs), where the function writes it nowhere and every direct transfer into
     the function is a call that leaves it there: GCC's millicode (jal t0, __riscv_save_N; jr t0).
     Without that rule the trees of four RISC-V builds were a seventh open.
  5. Names: (a) in each function's facts; (b) left out (entries only). **(a)**: names decide what the
     generator does (the string routines it never stubs, those whose calls it never answers, the
     domains rows), and a rename now renews the keys of the trees that hold the function only;
     before, regflow's image-wide digest held every name, and any rename renewed every key.
  6. Also whole, new in the key: the layout of the code (the code ranges, every function's entry, size
     and instruction set, without names: whether a load is one of code depends on every function's
     extent, dread.emu trace_reads; before, a table change reached the key only through regflow's
     findings), the devices, the stack, the sentinel, the scratch's place, the image's limit, the
     calling convention, the fixed registers and the core. None was in a key, each changes runs, and
     the key's format changes once anyway.
  7. regflow's result is read through RegFlow.case_facts (what cases read: to_json without
     overwritten): a table by function name per function, any other field image-wide. A field
     regflow adds there is covered without a change in dread.autocase; one it leaves out changes no
     key (overwritten: a test).
  8. Digests per strongly connected component of the call graph, bottom up (each of its members'
     facts and the digests of the components it calls): linear in the graph; a closure per function
     is quadratic (synthetic graphs: 5,000 functions 0.035 s against 5.2 s, 20,000 0.15 s).
  9. Made once per process, in dread.runner's and dread.uncovered's prepare, before the workers fork.
- **Figures.** A change of one function's regflow facts (a call site added behind its r0), keys kept:
  FreeRTOS GCC -Os on the MPS2 (366 functions, 19 with an open transfer in their tree) vListInsertEnd
  321, xQueueGenericSend 304, memcpy 176, vPortEnterCritical 98, prvIdleTask 346, main 347; on RISC-V
  GCC -Os (316, 8 open) vListInsertEnd 283, xQueueGenericSend 266, memcpy 142, prvIdleTask 307, main
  306; before, none. Each time the new keys were exactly the trees that hold the function and the open
  ones. The lift check after that change of vListInsertEnd's on the MPS2 build made 43 of its 362
  cache entries anew (319 kept) in 135 s, against 68 s with everything cached (main's code, the same
  change on its own copy: 359 of 362 anew in 678 s; the other three are generations that fail within a
  second, which no check caches); same table, same verdicts. The first check after this change makes
  every entry anew once, and the cases come out byte for byte main's: the MPS2 build's 362 entries
  (341 accepted by both, 792 s beside main's 763 s), RISC-V's 308 (272 accepted, 600 s beside 597 s);
  gizmo's seven tables unchanged. The keys' time on the 39 bench builds: their environment 0.03 to 0.6
  s a build (the reach, the facts, the components; main's under 0.01 s), the case keys of every
  function 0.2 to 2.7 s (main's 0.15 to 2.1), on a machine loaded by the lift checks; synthetic call
  graphs (4 calls a function, cycles): the components of 20,000 functions 0.15 s, where a closure per
  function took 5.2 s at 5,000.
- **Undo.** scopes() returning everything() for every function brings back the image-wide scope of
  regflow and more; the new whole parts are lines of Generator.whole(); a revert of the commit
  renews every key once more.
- **Status.** Done (dread/autocase.py, dread/arch/arm.py, riscv.py, mips.py, dread/runner.py,
  dread/uncovered.py, dread/integration.py; tests/test_autocase.py, test_lift_results.py).

## Array bases where the code indexes them, and no address for a plain value (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** Two items of docs/todo.md's review against hand-written C. (1) An array of structures
  indexed as `table[i - 1]` printed with its base folded down: readable C's arithmetic (`0x14u * (r0 -
  1) + 0x20000020u + 4`) folds the literal the code loads with the index's -1 and the field's offset
  into one constant (0x20000010, an element below the array); dread.statics find_arrays started the
  array there (no data symbol and no origin lay within an element below it), its structure at the
  lowest field read, so every printed offset was shifted; with the data symbols the array's own cut it
  to less than an element and it was not found at all. Where does an array start, and how is such an
  index printed? (2) dread.naming apply_globals wrote `(u32)&object` for every constant inside an
  object of static data used whole (an argument, a value assigned or compared, a result), also for a
  bit field's value compared with a masked word and for a count, where an object happened to lie at
  that number (most likely where objects lie at small addresses). Where is a constant an address?
- **What the compilers make.** A test program (an array of 20-byte structures behind another array,
  indexed by `id - 1`, its first field read nowhere) and arrays of words, halfwords, pairs and 16-byte
  structures, built with GCC 16 -Os and -O2 for ARM state, Thumb-2 and RISC-V, GCC 4.9 for ARM and
  clang 23 for Thumb-2 and RISC-V: each loads the array's own address (a literal, movw and movt, lui
  with addi) and puts the -1 into the index's arithmetic (`sub r0, #1; mla`, `addi a0, a0, -1`) or the
  access's offset (`ldr r0, [r3, #-16]` after `add r3, r3, r0, lsl #2`); none folds it into the
  literal. Through a section anchor (GCC without -fdata-sections) or clang's merged globals (RISC-V)
  the base is the anchor and the -1 goes into its offset. FreeRTOS clang -O2's xTaskGetHandle is a
  public example (`pxReadyTasksLists[uxQueue]` after `uxQueue--`, unrolled: `[lr - 1]` and `[lr - 2]`
  through `r9 - 20` and `r9 - 40`), gizmo a32's handle_key another (its switch table indexed by `c -
  'b'` from the table's literal). But FreeRTOS GCC -Os loads a field's address as a literal in some
  functions (pxReadyTasksLists + 4, xQueueRegistry + 4, its accesses below and above it), and a first
  version that took any address the code made above the accesses for the start moved both arrays four
  bytes up.
- **Options and choices.**
  1. The base: (a) the machine code's origin (dread.statics evidence) only: its pass does not follow
     `mla`, and its constant differs from readable C's where the -1 is in the index register (RISC-V);
     (b) the literal readable C's sum adds whole (base_of, also through a variable straight_facts or
     scales_of know, whose facts now carry it), else the origin. **(b).**
  2. Where an array starts: (a) at the base wherever the accesses lie below it; (b) where every base
     of a run's accesses lies above all of them (at most BELOW = 256 elements: `c - 'b'`), at the
     lowest, or at a data symbol within an element below it (the variable a field's literal lies in,
     or the array's own), else as before (a data symbol within an element below the lowest access, an
     origin there, the lowest); (b') as (b) with any data symbol between the lowest access and the
     base. **(b)**: a function that made the array's start keeps it where it was (FreeRTOS GCC -Os);
     (b') took gizmo a32's commands, in front of the anonymous switch table, for the table's start.
  3. Printing: `g_x[r0 - 1].f_4`, the index minus the elements below the start, as the code computes
     it, through the array's pointer macro, also for an address (`(u32)&g_x[lr - 1].f_8`) and through
     a variable that holds the element's address; apply_globals counts the element from the array that
     starts at the expression's base, not from an object in front the constant lies in.
  4. The machine code's evidence: an indexed access below its origin starts the origin's array, not a
     place in front (plan()'s starts), and is no index there for the rule that makes a run of equal
     fields an array of words (clang -O2's `[lr - 2]` reached into xQueueRegistry in front and cut its
     structure at 44 bytes; with the start moved, it had made the registry an array of words).
  5. Where a constant is an address: (a) everywhere used whole (main); (b) where the machine code
     makes it as compilers make addresses (dread.relations' anchoring); (c) by its use: an argument
     only for a parameter the callee uses as a pointer (dread.shapes pointer_params, a new `params`
     part of the readable_types table: param_used, the templates, and readable C that adds the
     parameter whole to an access's address, which the shapes do not follow through a sum of two
     variables: memcmp's `a0 + a4`; a callee not analysed as before), a value assigned to a variable
     the function uses as an address or stored into memory, a result, an operand of == or != whose
     other operand is a variable used as an address or holding a word read from memory (every value it
     is given, or the one it holds there: _straight_values), a word read or a call's result; never one
     that meets a mask's, a shift's or a narrowing cast's result or a variable given one; never for an
     object outside the image's bytes and its startup's ranges (dread.statics image_spans; without
     startup ranges every region but rom counts). An element's address the code computes (a constant
     plus a scaled index) is one wherever it is used. **(c)**: (b) tells no count from an address
     where ARM state loads both from a literal pool (0x404 is no ARM immediate), nor on RISC-V, where
     lui with addi makes every large constant.
- **Measured** (main -> now, copies of the eight builds, the same inputs). The gate accepts the same
  functions, name by name: gizmo a32 35, m3 36, m4 44, rv32 31; FreeRTOS GCC -Os 315, clang -O2 275,
  RISC-V GCC -Os 252, GCC -O0 354. Its time, on a shared machine whose load varied: GCC -Os 912 -> 728
  s, clang -O2 334 -> 267, RISC-V 153 -> 138, GCC -O0 493 -> 448, gizmo 3-7 s either way;
  pointer_params takes 0.2 s on GCC -Os. Static data: every structure and array the same but clang
  -O2's xQueueRegistry (11 words -> 18; static data's fields that are the source's 136 of 136 -> 143
  of 143, dread readable --statics --score) and gizmo a32's switch table (0x20d4a -> 0x20dac, at no
  variable either way); no array's base moved on a FreeRTOS build. On all 39 FreeRTOS builds of the
  benchmark (dread readable --statics, with the data symbols and without) every structure and array of
  static data is the same but clang -O2's xQueueRegistry (the command took 530 -> 450 s and 542 -> 550
  s for the 39, on the shared machine). Structures behind pointers: the same plans and scores on every
  build (dread readable --shapes --score, --score). Addresses written as & in every function's
  readable C: GCC -Os 80 -> 78, clang -O2 96 -> 104, RISC-V 63 -> 63, GCC -O0 74 -> 71, gizmo none; in
  the readable functions (dread.cmetrics fixed_addresses) 61 -> 59, 75 -> 80, 36 -> 36, 52 -> 49.
  Gone: xTaskCreate's pvParameters (GCC -Os, clang -O2: it only stores it), values passed only to
  vPortExitCritical and xTaskResumeAll, which take none (GCC -O0 three, GCC -Os one); come:
  xQueueRegistry's fields' addresses and xTaskGetHandle's list ends (clang -O2). In clang -O2's
  readable functions members of static data 148 -> 166, fixed-address macros 1524 -> 1506.
- **Not established.** That a literal a compiler loads is its array's start where every function loads
  a field's address (none did here) and no symbol says otherwise; `table[i - 1]` through a section
  anchor or merged globals still starts at the folded constant; whether a store or a result holds an
  address (written as & as before: the field's or result's type is not known); a word read from memory
  and compared with an object's address may be a number (a state word); an index that readable C adds
  to an address whole makes its parameter a pointer (memset's count); a parameter only stored
  (xTaskCreate's pvParameters) is no pointer for the call rule.
- **Undo.** statics.BELOW = 0 takes the start at a base above the accesses out (an index below the
  base then names no element: the constant's own object or the number); apply_globals(pointers=None,
  image=None) and a readable_types table without `params` give main's call rule; the comparisons' and
  assignments' rules have no switch (as_address in apply_globals); the rest is this change's revert.
- **Status.** In the code (branch array-bases): docs/todo.md, the two items of "From a review against
  hand-written C".

## Readable C's constants and casts by their use: a last pass, exact checks, evidence from the function (2026-10-23)

**Decided by recommendation** (the user's standing delegation of 2026-10-20; four items of docs/todo.md,
"From a review against hand-written C": Negative constants by use, Casts and constants, Characters as
characters, Radix by use).

- **Question.** The lifters write every constant as its instruction had it (0x2f7u, ~0x1u for mvn r0,
  #1, (u8)0x0u for a byte store, (u32)((u64)(u32)p + 4 + 0) for an add with its carry), and the stages
  keep most of it. Where does a rewrite by use run, how is its meaning kept, and where does each rule
  apply?
- **Options.** Where: (a) a stage after the structure, before the tables: it changes the digest the
  readable_names table's entries are made for (every name the pilot gave would stop applying) and the
  trees dread.shapes and dread.statics analyse; (b) tidy's literals pass: it runs before the names,
  the structures and the frame's object, so a store into a member, a macro's width, a field's type are
  unknown there; (c) a pass on the finished tree, last in readable._named (and on the naming questions'
  trees, after their digest). How the meaning is kept: (d) prove each rewrite with z3 (dread.cexpr):
  slow over every constant of an image, and z3 is optional; (e) exact rules over C's types: what must
  stay the same depends on the use (a conversion: the converted value; an operation's operand or a
  select's arm: the common type and the value in it; a comparison's operand: the set of the other
  operand's values for which it holds, computed as intervals; a shift's count: the value).
  Negative numbers: (f) every value near a type's top negative: wrong for masks (x & -33), for a
  64-bit operand (-1 there is 64 bits of ones) and misleading in unsigned orders (x < -2 holds for
  almost every x); (g) by use, within NEGATIVE (0x100) of the top. Characters: (h) every byte compared
  with a printable code: a byte field compared with 32 or 100 would read ' ' or 'd', a fact that is not
  one; (i) evidence of text in the function itself: values that hold characters (bytes loaded from
  memory, variables given only those, callees' results stored as bytes) compared with two or more
  distinct printable or escape codes; (j) evidence from the callers (gizmo's handle_key gets its key
  from uart_getc in main): an image-wide pass. Radix: (k) decimal by default for every value that is not
  a mask: on Cortex-M the image starts at 0, so addresses of texts and functions are small numbers
  (vAssertCalled(0xd3fu) passes a file name), and device registers take bit fields; (l) decimal by use
  in the function: divisors, multipliers, shift counts, ordered bounds, and the places those make
  numbers, places counted, places given or compared with two or more distinct plain numbers; (m) by
  evidence from other functions (a place stored decimal elsewhere): image-wide.
- **Choice: (c), (e), (g), (i), (l).** dread.polish, called by readable._named after dread.frames and by
  the naming questions (_question_one). Negatives where a value is one (assigned, stored, returned,
  passed, a converted select's arm, compared for equality, compared in order only where the comparison
  is signed, added: x + 0xfffffff8u is x - 8), never in a mask, a byte (0xff), or the compare idiom
  ((u64)x + (u32)~y + 1 keeps ~y: no other literal keeps its type there, and a negative inside a cast
  that stays would read worse). Characters with (i), also for a sum with such a value (c - '0'), a
  constant stored into a byte, a digit's character stored (d + '0'), a variable given only character
  codes (a letter base); 0 stays 0 ('\0' nowhere: people write buf[n] = 0). Decimal by (l), except for
  pointers (a counted address), device registers (the target's io regions), constants WIDE (0x10000)
  or larger or a run of eight ones or more; evidence from values that look like bit patterns or are all
  printable characters (a pad character's 0x20 and 0x30) counts for nothing; a reciprocal multiplier
  (x * 0x5556u >> 16) stays hex. Casts: a cast to its operand's type, a cast a conversion implies (the
  store's width, also a variable's declared one; not the cast that takes a 64-bit value's low word,
  which says what it does), a chain (T)(U)x as (T)x where U is as wide as T or holds x's values, and
  the casts that widen a sum's operands where only its low bits count ((u32)((u64)(u32)p + 4 + 0) is p
  + 4), with the operation kept at a type of that width that cannot overflow. Constant expressions fold
  where the result is a number, not where they show a mask or a comparison; a select on a constant
  condition is its arm (the lifters' guard 0xau ? r1 / 10 : 0); x + 0 and the like go where no type
  changes; x + ~y + 1 is x - y at int's width (unsigned); x = x op y is x op= y, x = x + 1 x++.
- **Why.** (c) keeps every names table and every analysis as it was (the digests are taken before the
  pass) and sees the types the tables gave. (e) is cheap (a few walks a function) and exact; the
  printed forms are tested by compiling random functions both ways on the host (tests/test_polish.py),
  and the gate checks each function's behaviour as for every stage. (g), (i) and (l) print nothing a
  reader would take for a fact that is not one, which is what the review asked to fix first; where the
  function alone gives no evidence, the constant keeps the lifter's form, which is never wrong.
  What could not be made safe and is left: characters and numbers by evidence from other functions
  ((j), (m): an image-wide pre-pass like the readable_types plan, worth it only if the review's sample
  shows such cases often); a negative 64-bit operand's constant (no literal of another type keeps its
  value); decimal call arguments (a size and an address cannot be told apart by the call alone).
- **Undo.** Take the polish_tree calls out of readable._named and _question_one: the readable C is as
  before, byte for byte. Per rule: polish.NEGATIVE = 0 (no negatives), Polisher.text False (no
  characters), Polisher.number returning False (no decimal beyond 0-9), _increment returning None (no
  compound assignments).
- **Status.** Done; the figures (patterns before and after, lines, the gate's counts and time on the
  eight builds) are in docs/todo.md under the four items.

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
  those callers, which ARM lifts as bound calls). On a private real-life target functions are refused so
  (docs/todo.md). How is such a function lifted, and its callers; and the registers a
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
  (DREAD's real-life test) the subsystem names called groups after a kind of device the image is not for,
  with high confidence, and the same questions asked again guessed another kind. A reverse engineer always
  knows what device a firmware is for. Should the questions
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
  Variables). Superseded 2026-10-23 ("an object at each address handed out, volatile only where an
  address may escape"): the region is one volatile object only for a function whose split the cases
  reject (the readable_types table's whole).

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
  first, from its listing, and kept the proposals unscored. On a large stripped firmware
  (DREAD's real-life test, private), what should a question show, in which order, and how
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
  readable C of every function is made first in a pool of workers (dread.readable views), each worker
  costing up to the parent's size in memory, so pools are now capped by the memory available.

## A jump to an arm's start as part of the condition (2026-10-25)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), from docs/todo.md's "A
jump to the start of the other arm, as a condition": a compiler that threads a condition (the code of an
arm shared with a later check) leaves a jump the emitter cannot place without a goto, since it places
each block once.

- **Question.** How is such a jump written without a goto, and without changing what runs where and how
  often?
- **Options.** (a) Leave the goto. (b) Repeat the shared arm where the jump was, as the emitter repeats
  short ways. (c) A flag set before the jump and tested after. (d) The check in the if's condition, the
  statements before it in a comma expression.
- **Choice.** (d) where the statements before the check are at most two expression statements and
  none of them a call made for its effects alone, (a) otherwise. (b) doubles code that is long by
  definition (a short one is repeated already); (c) adds a variable and a test the original does not
  have. (d) keeps every evaluation where it was: the statements run after the test that decided before
  them and before the check, in order, as often as before, and what follows the check stays in its arm.
  A comma expression in a condition is rarer in hand-written C than a goto, but with one or two
  statements it reads as one decision (`if (a && (n = f(x), n != 5))`), and the readable check compares
  its behaviour like that of any other rewrite. The limit came from the public builds: on FreeRTOS's
  timer task four statements, one of them a call through a pointer with eight arguments, went into one
  condition, the source's callback and the test after it, which the compiler had threaded; that read
  worse than the goto. The else-if chain's case evaluates the tests on the way into the chain a second
  time; done the same day where those tests read only the function's own variables (no memory, no call,
  no effect) and the statements before the jump write none of them, so the second evaluation gives
  what the first gave, and where they are at most two (a switch compiled as a chain of tests gave its
  default arm six in one condition on a public build).

## A division by a constant proved by the numbers (2026-10-25)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), from docs/todo.md's
"Division by a constant, from its multiplication".

- **Question.** When is the high word of x times a constant, shifted, x / d in readable C?
- **Options.** (a) By the pattern and a table of the compilers' multipliers. (b) By the numbers: the
  nearest d to 2^k / m, and a check that it holds for every value x can have, with x taken as 32 bits.
  (c) As (b), with fewer bits where the dividend's form says so: a mask, a cast to a narrower type, a
  right shift, or a variable's one definition that is such an expression. (d) A solver (z3) per product.
- **Choice.** (c). A table misses compilers and flags, and a pattern can match a product that is no
  division. Two values decide the check exactly (the error of such a quotient grows with the dividend),
  so no solver is needed. Taking 32 bits alone left clang -O2's products out: it chose a smaller
  multiplier for a value it knew had 15 bits, which is not x / 150 for every 32-bit x (1309892841
  differs), so written as / for 32 bits it would have been wrong. A variable's one definition stands
  for every value it holds (dread's readable C names a value once where it can); a parameter's or a
  variable whose address is taken does not.


## The arguments of a printf call from its format (2026-10-26)

**Decided by the user** on my recommendation (asked "What is your recommendation on the phantom parameters
issue? Ignoring what the rules say.", then "Yes, take it as the next item"), from docs/todo.md's "No
parameters from pushes, system calls and calls that keep every argument", part (2).

- **Question.** A variadic function reads as many of its unnamed arguments as its format says, which the
  register flow cannot see (it counts the save area's stores as no use). Readable C therefore kept every
  argument of every call of a variadic function and of a function that calls one, and each caller of
  FreeRTOS's vAssertCalled handed its own entry registers on as phantom parameters (a2-a7 on RISC-V).
  Where newlib's printf pushes its arguments, the wrapper was not recognised at all, regflow said it read
  the file alone, and its callers' readable C lost the line it prints (GCC -Os; no stub compared the
  line). How many arguments does such a call pass?
- **Options.** (a) Leave it. (b) Drop the registers a caller only hands on to a call that keeps every
  argument. (c) A table per target of how many arguments each such function takes. (d) Count the
  conversions of a constant format: the register flow counts the format, the arguments in front of it and
  one register for each word the format takes as the call's uses, so a wrapper that hands its line on
  reads it; readable C trims each such call to them, the callee declared variadic; a stub of a
  printf-family function logs the registers its format takes. Everything else (a format that is not a
  constant, a 64-bit, floating-point or positional argument, a call through a pointer, an ecall) stays as
  it was.
- **Choice.** (d), with the printf family by name and a target's own functions in `[abi] formats`. (b)
  breaks a wrapper that hands on its caller's format and arguments (`say(fmt, ...)` calling printf with
  them): what it hands on is what printf reads. (c) is the same fact typed by hand, and a function like
  printf takes a different number at every call. (d) is what the callee itself reads: the format decides
  every va_arg. It adds to what the gate compares rather than taking away: the stubs of the wrappers log
  the line regflow now finds them reading (before: the file alone), and a stub of printf logs the
  format's registers (before: the format alone), so a trimmed call is checked, not trusted. A format is
  read from the image only where the code puts its address into the register on the straight run in
  front of the call; a stub reads it from the emulator's memory, never from code, free space or the room
  behind the image, where the two images differ.

## What a system call hands its handler (2026-10-26)

**Decided by recommendation** (the user's standing delegation of 2026-10-20), from docs/todo.md's "No
parameters from pushes, system calls and calls that keep every argument", part (3).

- **Question.** The lifters bind every argument register to a system call, in and out (`svc`: r0-r3,
  `ecall`: a0-a7): their C cannot know what the handler reads or writes. A function whose entry registers
  flow into the binding took them all as parameters (FreeRTOS's RISC-V vTaskDelay(a0, ..., a7) around its
  yield). The emulator runs a system call as a no-op and logs no register there, so nothing compares what
  is bound. What should readable C hand the handler?
- **Options.** (a) Leave it. (b) Nothing but what the function reads after the call, as dread.regflow
  already assumes (an `ecall` reads nothing there). (c) A statement of the target's: the registers the
  handler reads (`[abi] syscall_reads`), every argument register where it says nothing; readable C binds
  those and the registers read after the call, regflow counts them read at the call, and the cases log
  them at each system call. (d) Work it out from the handler's own code (the trap vector and what it reads
  before it saves the registers).
- **Choice.** (c). (b) is wrong for a firmware whose system calls take arguments (a write with its file,
  buffer and length, Zephyr's user-mode calls), and nothing would notice: the emulator does not run the
  handler. (d) finds a handler that saves every register (a context switch) reading them all, and a
  handler that dispatches through a table is out of reach; it may come later for what (c) leaves unsaid.
  What the target states is checked as far as the cases go: the registers it names are logged at each
  system call, so the gate compares what readable C hands over; a register it leaves out that the handler
  does read is the statement's error, not seen. A register read after the call stays bound in and out, so
  a handler that writes it is kept; one nobody reads after the call is left alone by the C (the
  handler's write of it, if any, changes nothing the function does). The FreeRTOS recipe says `[]` for
  its three ports: the RISC-V yield saves the context and switches tasks, the Cortex-M `svc 0` starts the
  first task, and neither reads an argument register (portASM.S, port.c).

## Readable C for the functions the system run accepted (2026-10-26)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** The lift check accepts some functions by the system run alone: functions that never
  return and handlers, whose cases cannot test them on their own (dread lift --check marks them in the
  lifted table). The readable check left them lifted ("readable C is not checked there yet"): on the
  FreeRTOS bench (measure-40) 345 of the 381 functions that stayed lifted, the demo's tasks and its start.
  How is their readable C checked?
- **Options.** (a) Leave them lifted. (b) Try their readable C where the lift check tried their lifted C:
  in the whole image, with the readable functions' C, the scenarios against the original's; the smallest
  set that makes it differ back to lifted C (halving), as for the others in the whole image. (c) Give
  them cases of their own first (loop cases, recorded inputs) and check them like the others.
- **Choice.** (b): the same evidence that accepted their lifted C, and nothing the comparison sees is
  taken away. (c) is the generator's work on hard functions and stays open beside it (the cases decide
  more finely than a scenario does). Where nothing can decide (a target without scenarios, an image
  that differs with lifted C alone) they stay lifted, with that reason.

## The code behind a stop (2026-10-26)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** A callee in which runs hang (configASSERT's handler, which waits for a debugger to set a
  variable) becomes a stop: the case ends at its call. Code behind such a call runs only when the
  callee returns, and on FreeRTOS's clang -O2 build sixteen functions kept one to four instructions
  behind a configASSERT call unrun (clang copies the next check behind the call), xQueueGenericSend,
  xQueueReceive and their kin among them: dropped from the lifted table for that alone, so their readable
  C was never made, typed or checked. The frontier answers already retried a run that ended at such a
  stop with the callee stubbed at every call; the run then went on with what the assertion had refused
  (a null queue), called the handler again, went on again and faulted, and was dropped. How can a case
  run the code behind the call?
- **Options.** (a) Count the code behind an assertion's call as unreachable (the callee never returns
  in practice). (b) Stub the callee at every call (as before). (c) Answer the call the run ended at and
  end the run at the callee's next call (a case's new `stop_from`: the calls before that number are as
  without the stop), and where that run fails, at the first call the run makes after the answered one.
  Each also for the cases whose runs ended at such a call (not only for the answers' own, as before).
- **Choice.** (c) behind (b): (b) first, as before, and (c) where its run fails. (a) weakens the gate:
  the instructions are the function's own, the lifted C must reproduce them, and vAssertCalled does
  return when a debugger sets its variable. (b) alone runs on into the state the check refused, which
  faults or writes into the image; but where it does not, it reaches code that only a run past every
  call reaches (GCC -O0's prvBlockOnNotificationsComingFromInterrupts returns after nine rounds with two
  checks each, and lost its return with (c) alone). (c) compares the code behind the call up to the next
  call, a place both images reach alike, and goes on from there one call at a time (each answered in
  turn, as far as the answers' depth goes). The answers' version changes, not the generator's: cached
  searches are kept and only the answers are made anew. The same day also where the run ended at a stop
  deeper in (FreeRTOS's RISC-V demo prints in its handler and waits in the UART's output): the call in
  the function is the one answered, its earlier calls left as they ran.

## A word read twice that changes in between (2026-10-26)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** Code that reads one word twice and acts on the difference (FreeRTOS's stream buffers
  read their tail twice and go round while an interrupt moved it; a priority register written and read
  back for the bits it keeps) has paths no generated case runs: in the emulator nothing writes memory
  between two reads, and a case's memory sets the word for both. Should the generator make cases in
  which a word changes between two reads, and how?
- **Options.** (a) No: such paths need another task or a device, and stay uncovered (their functions
  dropped). (b) Model the other party (a device model, an interrupt between the reads). (c) Give the
  word a new value just before the read that decides (a case's `later`, which dread sysrecord already
  uses for what other tasks wrote while a recorded call waited), from the frontier answers, only where
  the run read that word before and the branch went one way.
- **Choice.** (c). It is an input like the others, compared under the same rules in both images: the
  lifted C makes the same reads in the same order (volatile accesses), so a case with a changed word
  checks the code's behaviour under such a change and nothing more. (b) is the system run's business,
  where a device model and the interrupts are. A consequence for readable C: a stage that merged two
  reads of a word would fail such a case; readable C keeps memory accesses volatile now, and the open
  Memory item (plain accesses for RAM) has to keep the reads a case changes.


## A case that ends at a fault (2026-10-26)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** Behind an assertion's call, the code that runs when the handler returns goes on with
  what the assertion refused. On FreeRTOS's RISC-V GCC -O2 build forty functions keep the instruction
  behind such a call unrun: GCC puts the call out of line and jumps back, and with the null queue the
  run reads at once at 0x38 or 0x40, where nothing is mapped, before any call that could end it. A run
  that faults was never a case (the runner compared "original: fault" with "rebuilt: fault" as a
  difference, and the generator dropped such inputs). Should a fault be a result?
- **Options.** (a) No: such code stays unrun and its functions dropped. (b) Map the null page so that
  the reads give 0 (a model the hardware does not have). (c) A case may say that its run ends at a fault:
  a load or store to unmapped memory then ends the run as its result, logged with its kind and address
  after what the run logged before, and compared like any log (the writes up to it too); the
  instructions behind the faulting one in its block do not count as run. Only the frontier answers'
  code behind a stop makes such cases, where the run after the answered call faults before any call.
- **Choice.** (c). The lifted and readable C make the same loads and stores in the same order (volatile),
  so the first access to unmapped memory is the same one at the same address in both images, and what
  the case compares is behaviour up to it: nothing is taken away from the comparison of other cases, and
  a C that faults elsewhere or not at all differs. A fault whose address depends on the stack (a stale
  local used as a pointer) would differ between the images and fail (conservative, and the fragile
  check's business). Fetch faults (a jump to unmapped memory) stay errors. Coverage counts short at the
  faulting block (a block that also ran whole earlier loses its tail for that case), never long.

## A callee's byte pointer as void * in its callers (2026-10-26)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** A callee that takes bytes (its own C dereferences them, or hands them to memcpy) has a
  `u8` pointer parameter, and its callers mostly hand it other things: the address of a word, a
  structure (a queue's item, a buffer memset clears). The extern rule (a callee's type where the calls
  pass it without a cast) left such externs u32 and every call cast: `(u32)&stack4`. How should callers
  declare it?
- **Options.** (a) As before: u32 and a cast per call. (b) The callee's type and a pointer cast per call
  (`(const volatile u8 *)&stack4`: as many casts). (c) `void *` with the parameter's qualifiers in the
  callers' externs, where their calls pass pointers of other types: C converts any object's pointer to
  a void pointer without a cast; the callee's own definition keeps its byte pointer. (d) Void in the
  callee's own definition too.
- **Choice.** (c), taken by the plan's trial (fewest casts) and by object_arguments for the frame's
  objects. The extern and the definition differ in type, not in what is passed (one pointer, each
  function compiled alone, as memcpy's `void *` and a byte loop's `unsigned char *` do in a C
  library). (d) would need casts inside the callee, whose body reads bytes, and changes the function's
  own C for its callers' sake; not tried.

## A system register a case sets (2026-10-26)

**Decided by recommendation** (the user's standing delegation of 2026-10-20).

- **Question.** FreeRTOS's vPortValidateInterruptPriority reads the exception number (mrs r0, ipsr) and
  checks the interrupt's priority only inside a handler (16 and up). Every case ran in thread mode
  (IPSR 0), so that path never ran and the function was dropped on every Cortex-M build of the bench
  (27 at measure-40). Should a case be able to say that it runs inside a handler?
- **Options.** (a) No: such paths stay unrun. (b) Hook the mrs instruction and give its register a value
  (the rebuilt image's mrs lies elsewhere, so the hook would need its address in both images). (c) A
  case field that sets the system register before the call, after the core's status is reset: both
  images' mrs read it alike; the frontier answers give it where an mrs decides a branch.
- **Choice.** (c), for the registers a layer can set (sysreg_id: IPSR on the M profile). What the case
  says is a state the function is called in, as its arguments are; the code reads it with its own
  instruction in both images. The function returns with bx lr to an address of the harness, which is
  no exception return (EXC_RETURN), so the handler state changes nothing else of the run.
