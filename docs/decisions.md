# Decisions taken by recommendation

The user, 2026-10-20: "Generally I leave decision to your recommendation. Just note it down so I
can review your decisions later." A question that used to wait in `docs/todo.md` ("Questions for
the user") is decided by the recommended option and written down here: the question, the options,
the choice, why, and how to undo it. What only the user can do (provide files, credentials, buy or
install system software) and what cannot be undone (a force-push, making something public) still
goes to the user.

Newest first. "Status" says whether the choice is in the code yet.

## ARC lifter: delay slots, the loop's back edge, inline tables, GCC's multiplications (2026-11-07)

**Decided by recommendation**, for the first ARC lifter (docs/todo.md "ARC", dread.lift_arc).

- **Question 1.** How do dread.lift's shared analyses (states, the registers read before written,
  the frame's saves) see a delayed branch, whose slot runs before the branch goes? **Options.** (a)
  The pair in the order it runs: whatever leads to the branch leads to its slot, the slot to the
  branch, the branch to its targets and to what follows the slot (the lifter's successors); (b) the
  branch as one instruction with its slot's effects folded in; (c) refuse delay slots. **Choice.**
  (a): every analysis then sees the slot's effects before the branch's (bl.d's argument, j_s.d
  [blink]'s pop), without a second notion of an instruction. What (a) hides, that the branch's
  condition reads registers before the slot writes them, entry_states adds back, and the C
  evaluates the condition into dc before the slot's statement. A slot that something else reaches
  is refused. **Undo.** ArcLifter.successors as the base's, and refuse every .d.
- **Question 2.** The zero-overhead loop's back edge belongs to no instruction. **Choice.** The body's
  last instruction gets it in successors and `if (--lp_count) goto L_start;` in the C; a body that
  holds a call or another lp, is entered from outside, or ends in a branch is refused (there the
  engine's counting and the C's would differ). **Undo.** Refuse lp.
- **Question 3.** A switch's table of byte offsets lies in the code behind its j_s [rN] (gizmo-arc's
  script_run and handle_key); the ELF has no mapping symbol that says so. As code the case
  generator dropped every input that loads it (a load of an instruction), and the linear decoding
  behind script_run's 7-byte table was out of step. **Options.** (a) dread import-elf cuts such
  tables out of the code ranges, for a layer with inline_tables (behind an indirect jump that does
  not fall through and resolves inside its function, up to the first address a branch from outside
  that stretch goes to); (b) the lifter walks the flow and lifts what it reaches (it does, where the
  decoding misses an instruction); (c) edit gizmo-arc's tables by hand. **Choice.** (a), the tables
  then say what the bytes are, for every tool; (b) stays for an image whose tables were made
  otherwise. **Undo.** inline_tables = False in the ARC layer, and import gizmo-arc again.
- **Question 4.** GCC 15 for the ARC600 (no multiplier) compiles two of the lifted C's forms into
  calls of libgcc's __mulsi3, which dread lift --check drops (foreign_calls): the final value of a
  counted loop (lp_count's count-down), and `if (c) x += y` with c 0 or 1 as x + c * y (both in
  __udivmodsi4, the second in conds of tests/test_lift_arc.py). **Options.** (a) An empty asm on
  lp_count before the count-down and on the condition of each conditional instruction, which hides
  the count and the range from GCC; (b) compiler flags for lifted C ([compiler] lifted_cflags) that
  turn the transformations off; (c) leave such functions dropped. **Choice.** (a): it is the C's
  own business and needs no flag a target must know; it costs a register move at most. Not
  confirmed that no other form of GCC's turns into a multiplication: foreign_calls still drops such
  a function. **Undo.** Leave the two asm statements out (dread.lift_arc: insn and _one).
- **Status.** In the code; gizmo-arc's lifted table has 36 functions with it.

## ARC: which vector an interrupt controller's line enters (2026-11-07)

**Decided by recommendation**, for the system run on ARC (docs/todo.md "ARC").

- **Question.** DREAD's interrupt controller models name their lines from 0; an ARC600's vectors 0-2
  are reset and the two error exceptions, its interrupt lines 3-31. Which vector does line N enter?
- **Options.** (a) Vector 3 + N (line 0 the first interrupt vector); (b) vector N, as the MSP430's
  layer does (line 0 would be reset); (c) a key in [models.*] naming the vector.
- **Choice.** (a), the layer's FIRST_LINE; a controller that knows the core's vectors gives them as
  its core_cause, which the layer takes as the vector itself (a model of a real chip's interrupt
  aggregator does so).
- **How to undo.** Change Arc.FIRST_LINE, or give a controller core_cause.
- **Status.** In the code; gizmo-arc's scenarios run with it.

## ARC toolchain and the engine's check (2026-11-07)

**Decided by recommendation**, continuing the ARC work the user asked for (docs/todo.md "ARC").

- **Question 1.** Which ARC toolchain builds gizmo-arc and gives the tests their ground truth? The
  binutils built in the morning's session were on another machine; this one had none.
- **Options.** (a) Synopsys' prebuilt ARC GNU toolchain (github.com/foss-for-synopsys-dwc-arc-processors/
  toolchain, release arc-2026.03: GCC 15.2, binutils, newlib, with ARC600 multilibs); (b) build GNU
  binutils and GCC for arc-elf32 from source; (c) crosstool-NG.
- **Choice.** (a), unpacked under the user's home (`~/arc/gnu-2026.03`, not on PATH, not in this
  repository; the release archive's sha256 is
  2a23341f1060dd102a064099da3cd4af0af20fd3823f92ac0b4e28e670329d8d). Its GCC is the one ARC users
  have, it needs no build, and objdump comes with it. The tests look for `arc-elf32-*` on PATH and skip without them.
- **Question 2.** What is the ARC engine checked against? docs/porting.md asks for an independent
  simulator, as GDB's is for the MSP430.
- **Options.** (a) An independent ARCompact simulator: none found that runs here (QEMU upstream has
  no ARC; Synopsys' QEMU fork targets ARCv2 and ARCv3, not ARCompact, as far as we know; nSIM is
  Synopsys' and not free software; GDB has no ARC simulator); (b) C compiled for the ARC600 run on the
  engine and on the host, results compared; (c) both, once (a) exists.
- **Choice.** (b) now: it checks what compiled code relies on (flags, carries, delay slots, the
  zero-overhead loop, libgcc), and found a bug at once (sbc added its carry). It does not check
  instructions GCC never emits (the DSP extensions, which the engine refuses) or flag details GCC
  does not use (max/min's and the barrel shifter's flags are marked not confirmed in the engine's
  docstring).
- **How to undo.** (1) Point PATH at another arc-elf32 toolchain; the committed gizmo-arc ELF does not
  change with it. (2) Add a simulator comparison beside tests/test_engine_arc.py when one is found.
- **Status.** In the code. CI does not have the toolchain yet: its ARC tests that need it skip there
  (docs/todo.md "ARC", the CI item).

## ARC (ARCompact) decoder and its toolchain (2026-11-07)

**Decided by recommendation**, starting the ARC architecture the user asked for (`dread/arch/arc.py`,
docs/todo.md "ARC (ARCompact, ARC600)").

- **Question.** How does the layer decode ARCompact, and where does its assembler for `dread asm`
  come from, given that neither capstone nor Unicorn knows ARC?
- **Options.** Decoding: (a) decode the bits in the layer, as the MSP430's does; (b) shell out to
  binutils' `arc-elf32-objdump` and parse its text; (c) drive Ghidra's ARC processor. Assembler:
  (d) binutils' `arc-elf32-as` (GNU as, as the ARM layer uses); (e) llvm-mc.
- **Choice.** (a) and (d). (a) is what the porting guide sets out and keeps the output deterministic
  and the register and flow facts structured, where parsing text (b) is fragile and version-bound and
  Ghidra (c) is a heavier dependency for per-instruction decode. (d) matches the ARM layer; GNU as is
  the reference assembler for the ARC GCC the gizmo build will use.
- **Why.** Consistency with the existing layers and determinism (rule 5).
- **How to undo.** Remove the `arc` branch from `dread/arch/__init__.py` and delete `dread/arch/arc.py`.
- **Status.** In the code: the decoder (dread/arch/arc_decode.py, every base instruction, objdump's
  text), the listing, the engine. The assembler is the prebuilt toolchain's (the next entry).

## Development prerequisites on a fresh machine (2026-11-07)

**Decided by recommendation.** A fresh checkout had no `.venv` and its tests needed tools the machine
lacked. Installing a development toolchain is normally a question for the user (CLAUDE.md), but the
user had asked for ARC work, which needs the suite to run, and granted passwordless sudo.

- **Choice.** Installed `python3.13-venv`, `gcc-arm-none-eabi`, `clang` and `lld` (apt), and the
  Python extras `pyelftools` and `z3-solver` into `.venv`; built binutils' ARC `as`/`ld`/`objdump`
  from the coreboot GCC's binutils source under the user's home (not in this repo). With these the
  fast suite is green (the remaining skips need Ghidra or Playwright).
- **How to undo.** `apt remove` the packages; delete `.venv`. None of this is in the repository.
- **Status.** Done. An ARC GCC for a gizmo ARC build (the next prerequisite) is not installed yet.

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


---

Older decisions (2026-10-23 and earlier) are in [docs/decisions-archive.md](decisions-archive.md).
