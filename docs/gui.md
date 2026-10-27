# The browser view (`dread gui`)

`dread gui` serves a page on this machine (127.0.0.1:8350) that shows what the tools know and
decided: where each function stands (written by hand, by the pilot, lifted, refused, original), a
map of the image, the listing with the instructions the cases ran or missed, the C beside it (a
lifted function's readable C to read, each statement's first instruction in a margin that selects it
in the listing), verdicts and cases, search by name, proposed name, address and text. Its only changes are
names (through `dread rename`), notes (annotations.txt) and the commands its Run page starts; everything
else is the tools' work. Keys: `/` search, `j` `k` lines, `Enter` follow, `n` rename, `;` note, `r`
relations, `p` the whole program, `s` the system run, `x` run a command, `u` setup, `l` the pilot, `m` the
bench, `i` the compiler lab, `w` the leak check, `e` names from elsewhere, `?` all of them.

## Running commands

The Run page (`x`, or the button in the bar) has a form for every command of DREAD, made from the
command's own options as `--help` lists them (a subcommand's form inside its command's; dread pilot's
options for the task chosen). What you set becomes the command line below the form, the words you would
type in the target's directory, and Run runs it there as a job: polite, `--jobs N` at a time (default 1)
and the others waiting, its output kept in BUILD/gui/jobs and followed live, with Stop (SIGTERM to its
processes, SIGKILL after a grace period), Run again, and Change and run. Where a job changes the tables,
the other views reload. The jobs live with the server: stopping it stops them, and the history stays.
`dread gui --read-only` changes nothing and runs nothing.

## The pilot

The pilot page (`l`, or the dial in the bar) runs `dread pilot` and watches it:
- **Start**: a short form of what matters (the task, the provider with its prices, a budget in dollars,
  all functions or the first N or some, Message Batches on or off, the few options the task needs), the
  command line it makes, and an estimate where an earlier run of the task gives one (the dry run's count
  times that run's tokens a function at the chosen provider's prices; `dread pilot --dry-run` prints
  the same). "Every option" opens the Run page's form of dread pilot filled in with these.
- **A run**: followed live from its events (BUILD/pilot/runs): the stage, the questions answered of those
  planned, the Message Batches out and their age, the spend against the budget (the bar turns amber,
  then red, near it), the rate and the time left, and the names as they come, newest first, each with
  its function's page, accepted or refused and why. Stop sends its job SIGTERM: the run cancels its
  batches and ends "stopped", its tables as it left them; started again, it goes on where it stopped.
- **The runs before**, how each ended, what it cost and named; **the cost per month** from the usage
  ledger, per provider, with the shares of Message Batches and of the prompt cache and what they saved;
  **comparisons** of names as dread pilot makes them: with the true names (`--score`, which asks
  nothing without a judge), with another copy's names (`--against`), two settings over replicate copies
  (`--vs`).

Keys stay out of the page: it says whether a provider's key is there, never what it is.

## A function's views

A function's page has four views under its header, the one that is open beside the listing and the C
(above them on a screen narrower than 1600 px), each read from its tool's own record (its `--json`, kept
in BUILD/gui/views) and run as a job from the page where the record is missing or older than what the
tool reads:
- **Trace**: the cases the records say fail (dread test's tests.tsv, the lift check's and the readable
  check's verdicts), each traced in the image it failed in (`dread trace`, `--lifted`, `--readable`):
  where the runs part, the instruction in both images with its C line, where a register was set last,
  both call stacks, the frame as both images have it, the last shared events. The instruction is marked
  in the listing and its statement in the C. A case chosen on purpose is traced at once; a failing
  function in the overview opens here.
- **Uncovered** (`dread uncovered`): the regions its cases leave unrun, marked in the listing, each with
  its way in, what decides it and where that comes from, the values its runs saw there and how a run sent
  the missed way ended.
- **Mutation** (`dread mutate`, for C of src): each change no case notices as the line before and after,
  with what the `--kill` search found; the argued ones with their argument.
- **Registers**: what dread.regflow knows (what it reads at its entry and what its callers read after it,
  with the bits where fewer than all count, what it leaves alone, leftovers and computed results), per
  call site of its callers and of its own calls, and what `dread callconv` measured against it.

## The bench, the compiler lab, the leak check, names from elsewhere

Three pages show the results of the tools that work beyond one target, each from its tool's own `--json`
record and running the tool as a job (the report by itself where it is missing or older than what it reads,
quick; anything longer on a button):
- **The bench** (`m`): a recipe's builds (`dread bench`) as a table that sorts and filters by compiler,
  release, optimisation, core and project, each build's figures as `dread bench report` shows them (functions,
  lifted, readable, Ghidra's starts, never-returning functions, registers against regflow, the libraries'
  toolchain, seconds) with the trend of one figure over its measurements; a build's page with every figure
  against the measurement before, the trend as a chart and a table, what stops the rest and why, what it
  gained and lost, its readable C's figures; a measurement started as a job with the steps chosen and an
  estimate from the last one's seconds; the build opened as the page's target. The record is
  `BENCH/RECIPE/report.json` (`dread bench report RECIPE --json FILE`).
- **The compiler lab** (`i`): the compilers measured in a work directory (`dread cclab`) and a compiler's
  profile: where arguments arrive and results come back, the types, what each operation calls or does
  inline, what a call keeps and the stack alignment, where the functions lie, the runtime helpers with the
  registers they change and those the compiler's code reads after them, what DREAD lifts; the probes and
  builds that failed; the results that differ between builds marked. A compiler's probes run from a form
  (an adapter, the builds of its matrix, one's own probes, `--lift`), its adapter checked (`dread cclab
  doctor`). The record is `WORK/report.json` (`dread cclab report --json FILE`). Two compilers measured
  there side by side (`#/cclab/compare/ORIGINAL/REBUILD`, from the page's "Compare two compilers"): where
  they differ in what linking depends on, the options of the rebuilding compiler that close a difference,
  where their code differs, what DREAD lifts of each; the record is `WORK/compare-ORIGINAL-REBUILD.json`
  (`dread cclab compare --json FILE`), made as a job where it is missing or older than a result.
- **The leak check** (`w`): `dread leakcheck` against a tree with the private tables, words and allow list
  given by their paths, its hits with their file, lines and the string each matched, the history's with
  their commits. The page shows nothing of the private files but what matched: the server never opens them,
  and the check's jobs and records stay in your state directory (`~/.local/state/dread/gui/leakcheck`),
  never in a target's or a repository.
- **Names from elsewhere** (`e`): names for this image's functions from three sources, each a command run as a
  job: the toolchains' libraries (`dread whatcc`: functions whose code is a library function), the same
  code in another image (`dread samecode`), the order of calls (`dread callpair`, with seeds). A proposal is
  offered only where it gives a function without a name one; the chosen ones are applied by `dread rename`,
  a job too, everywhere a name is written.

## Relations and names

A function's relations are the pieces of the puzzle around it, as a graph:
- its callers above and callees below, to a depth of 1 to 3;
- the memory its readable C reads and writes;
- the texts and tables its constants point to;
- the structures its variables point to.

A click walks to another function; pointing at a piece lights what it joins.

How sure each name is shows wherever a function does (the list, the overview, both graphs): a name in
the tables, a proposal of the pilot's by how far its vote agreed (sure, likely, unsure, or no vote), or
none yet; one hue from unsure to sure, and each function's disc filled as far as its vote agreed, so
the scale reads without the colour. A name review's suspects (`dread pilot --task review`) carry a
mark: `?` a name it kept without confirming it, `!` a person's name it found against (never changed),
a turn for a name it changed; the reasons and the old name show on hover and in the function's page,
and the overview lists them.

## The whole program

The whole program (`p`) is a graph of the groups `dread groups` finds from the code: each group a
node, sized by its functions and named by the pilot where it named it, joined by the calls, taken
addresses and shared memory between them. A click opens a group into its functions (the memory and
texts it shares then joined to the functions that touch them); a program of a few hundred functions
shows all of them at once. A search there lights the functions it finds (by name, proposed name,
address or text) and the groups that hold them; Enter opens those groups.

## The system run

The system run (`s`) shows what `dread sysrun` and the lift check's whole image kept of each scenario
(BUILD/sysrun): how each image's run ended, and both device logs side by side, entry by entry, each
with the function whose instruction made it; where they first differ is marked (`d` goes there). What
the run saw beyond the calls joins both graphs and the function's page: who called a function through
a pointer (a timer's callback, a table of commands), which task the scheduler started by a return, which
exception entered a handler.

## Drawing the graphs

Both graphs lay themselves out as they are drawn: nodes overshoot a little and settle, a change of the
graph morphs from the picture before, and a dragged node pulls its neighbours along. With reduced
motion asked for, the settled picture shows at once. Thousands of nodes stay in motion: the whole
program is drawn on a canvas, its repulsion approximated by Barnes-Hut.

## Pictures

![The overview of gizmo in dread gui](images/gui-overview.webp)
![A function with its listing, coverage and C](images/gui-function.webp)
![The relations of a function: who writes the global that another one reads](images/gui-relations.webp)
![How sure the names of a FreeRTOS build are after a naming run and a review: each function's disc filled as far as its vote agreed, the review's marks, a changed name with its old name and why](images/gui-names.webp)
![The whole program of a FreeRTOS build: its functions in the groups dread groups found, one function lit with its callers and callees](images/gui-program.webp)
![The system run of a FreeRTOS build: the device logs of the original and of the image with 329 functions from lifted C side by side, each entry with the function that made it](images/gui-timeline.webp)
![The Run page: dread lift's form, made from its options, and the command line it makes](images/gui-run.webp)
![A job: the lift check run from the page, its command line, exit status and log](images/gui-job.webp)
![The pilot page's short form: a readable run with Haiku on a FreeRTOS build, its budget, the functions, batches off, and its estimate from the run before](images/gui-pilot.webp)
![A pilot run followed live: its stages, the questions answered of those planned, the spend against the budget, a Message Batch out and its age](images/gui-pilot-run.webp)

## The Setup page

`dread gui` started where there is no target comes up with the **Setup page** (`u`, or the button in the
bar): what `dread doctor` found, with each install command to copy; a form for a new target (a directory,
and an image by its path on this machine or uploaded from the browser into the new target's `original/`,
at most `--upload-limit` MiB), which runs `dread new` as a job and then shows the new target; its first
analyses as a chain of jobs, each step's state and its log's last line; and the providers, with a form
for each (the key in a password field, written to its key file, shown back only as set or not set) and a
Test button that asks one question as a job. The command line of each action is shown beside it, and
paths typed into the page are taken as typed, from the directory `dread gui` started in (no shell). One
server shows one target at a time and opens another only while none of its jobs runs; `--read-only`, or
listening beyond this machine, changes nothing.
