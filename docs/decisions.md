# Decisions taken by recommendation

The user, 2026-10-20: "Generally I leave decision to your recommendation. Just note it down so I
can review your decisions later." A question that used to wait in `docs/todo.md` ("Questions for
the user") is decided by the recommended option and written down here: the question, the options,
the choice, why, and how to undo it. What only the user can do (provide files, credentials, buy or
install system software) and what cannot be undone (a force-push, making something public) still
goes to the user.

Newest first. "Status" says whether the choice is in the code yet.

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
- **Status.** In the code (dread/pilot.py: pair_groups, group_sign, compare_names).

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
- **Not established.** Whether the function names gain where the device is known (a naming run of the
  private firmware with the line against its last one: running); a reading can be wrong (the texts of
  a library, a vendor's sample code), so the line says "likely, not certain", and a person's
  statement wins.
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
  test, docs/todo.md Function names).

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
  and 359); gizmo's lift checks keep every function, their whole images agreeing as before.

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
