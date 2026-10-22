# Working on DREAD

DREAD is a toolkit for rewriting firmware in C and proving the result in an emulator
(`README.md`). It serves other repositories, the targets, which hold the facts of one firmware each.
Read `docs/target.md` before changing how a tool reads its target, and `docs/formats.md` before
changing a table, case or report format.

## Rules

1. **No proprietary material.** Nothing derived from a proprietary firmware or a private project goes
   into this repository: no bytes, no disassembly, no addresses, function or variable names,
   register maps or other facts of such a target. That also holds for comments, docstrings, tests,
   examples and commit messages. Examples use gizmo (`examples/gizmo`), which is our own code. A
   proprietary target's facts live in that target's repository, in its `dread.toml`, its tables and
   its plugins. Open-source projects are not covered (the user, 2026-10-19: "Relax the rule to only
   include proprietary stuff"): their names, addresses and figures may appear in code, docs, tests
   and commit messages. Those used as benchmarks (FreeRTOS first) are still built outside, by `dread
   bench`: the repository keeps their recipes (a pinned download, the build configurations, the
   measuring scripts), and their sources, builds and reports live in `~/dread-bench`.
2. **Facts come from the target, not from the logic.** A tool takes load addresses, the memory map,
   the calling convention, the compiler and the paths of its tables from `dread.target`. A constant
   in a tool that describes a firmware is a bug. Constants of the method (limits, search budgets)
   are named, documented and overridable.
3. **Both gizmo builds pass.** `.venv/bin/pytest` runs the tests on the ARM-state build (`a32`) and
   the Cortex-M3 build (`m3`). A new tool comes with a test on gizmo. A test that needs Ghidra or
   another slow or optional program is marked and skipped when the program is missing.
4. **Formats are interfaces.** Tables, case files and reports are read by other tools and by target
   repositories. `docs/formats.md` describes each one. Change a format only with a note there on
   what older files mean, and keep readers tolerant of older files.
5. **Deterministic output.** The same inputs give the same bytes: tables, listings, generated
   cases, images and reports. Caches are keyed by content and diffs have to mean something. A
   result that depends on wall-clock time or machine speed says so in its documentation.
6. **Never weaken a comparison.** An option may add to what is compared, never take away. A
   disagreement between two images is traced to its instruction before anything is concluded.
7. **Say what is not known.** Write "not confirmed" or "unknown" rather than a guess presented as
   fact, in docs, docstrings and commit messages.
8. **Process in code, models only for judgement** (the user's principle). Whatever a tool can decide,
   a tool decides: choosing work, assembling context, building, testing, searching inputs, the
   mutation check, bookkeeping. A language model is asked only where judgement is needed (writing C
   from a listing, an input no search finds, an argument that a mutant is equivalent, a name), with
   a typed question and an answer a tool checks before it counts. A step that a skill or an agent
   does by following instructions is a candidate for a tool.
9. **Public, Apache-2.0** (github.com/dwzg/dread). Before every push, run `dread leakcheck` with the
   tables and words of each private project code was taken from (the exact command is kept with
   that project, not here); a hit that is not a known coincidence stops the push. A leak, once
   pushed, cannot be taken back.

## Working unattended

When the user is away (a `/loop`), the work runs from files, not from a conversation:

- **The plan is `docs/todo.md`.** Start each item with `git pull --rebase --autostash`: the user may steer from
  anywhere by editing `docs/todo.md` on GitHub (answering a question, reordering). Take the first
  open item. Finish it with tests on every gizmo
  build, tick it off in the same commit, push. Change the plan when the work shows a better one (an
  item is bigger than thought, a bug comes first, an approach turns out wrong): split, add, reorder or
  drop items, and say why in the commit message. Every few items, check with numbers (test times,
  coverage, what an item gained) whether the order still makes sense.
- **Never block.** The user leaves decisions to the recommendation (2026-10-20: "Just note it down
  so I can review your decisions later"): take the recommended option and write it into
  `docs/decisions.md` (question, options, choice, why, how to undo, status), also where it takes
  from a comparison (rule 6 then holds through that record). Only what the user alone can do or
  undo goes into "Questions for the user" in `docs/todo.md`: files or credentials only they have,
  buying or installing system software, anything public or irreversible beyond the agreed pushes
  (a force-push); then go on with what does not depend on it.
- **When stuck**, after two serious attempts or a few hours on one item: write down what was tried
  and why it failed in the item, put the tree back to the last commit, and take the next item.
- **Commit only green**: `.venv/bin/pytest` passes, every gizmo build. Push only through the leak
  check (rule 9) with a script that stops on its exit status; never force-push without the user.
  The push script runs the fast tests first (`pytest -m "not slow"`, about 15 s) and refuses a red
  tree: once a push went out because the command that ran the tests went on after they failed.
  The same holds for the push script itself: its exit status decides what comes after it, so it is
  not piped into `tail` before an `&&` (the pipe's status is tail's: on 2026-10-20 a push the leak
  check refused went on to remove its worktree and branch). CI runs every test on Ubuntu's older
  compilers: look at its run after a push (`gh run list --repo dwzg/dread`); the push script refuses
  to push while the last finished run on main is red, except the push that fixes it (`--ci-red-ok`).
  On 2026-10-20 CI had been red for 74 pushes nobody looked at. `bin/with-toolchain NAME COMMAND...`
  runs a command (the tests) with the compilers of a toolchain image (bench/toolchains/, podman) in
  front of the host's: a change that depends on compiler versions is tried on CI's older ones first.
- **Stay polite on the machine**: long runs go to the background (polite, as the tools are);
  nothing that fills memory or runs without a bound.
- **Beside a long run**: a bench measurement starts every step anew from the tree it runs from, so
  it runs from a worktree of its own at the commit it measures (`bin/dev-worktree measure-N`, then
  `bin/dread bench all freertos` there), and `main` can take fixes and be pushed meanwhile (until
  2026-10-21 measurements ran from the checkout, which then had to stay put: a CI fix waited three
  hours behind one). Changes go into worktrees too (`bin/dev-worktree NAME`: a branch, the `.venv`
  linked, the examples' caches copied; without them the suite regenerates gizmo's cases, fifteen
  minutes more), are tested there in full and come onto `main` when they are done.
  Before the push, `bin/take-caches WORKTREE` copies the caches the worktree's suite made back
  into the checkout; without it the push's fast tests make them again after a VERSION change.
- **Look for performance regularly** (the user, 2026-10-18): every few items, measure what is slow
  now (`pytest --durations=15`, `BUILD/times/`, the `seconds` of `BUILD/autocases/*.json`), profile
  the top item (cProfile) and fix what dominates. Prefer the algorithmic fix (work per byte or per
  run that need not be done); move a hot path into C (`dread.fasthooks`) when a Python callback per
  instruction or memory access dominates. Record before and after in the commit message. A change
  meant to be faster without changing results is checked with `bin/ab-cases [--target DIR] [--loop]
  MAIN WORKTREE NAME...`: both checkouts generate the named functions' cases, timed, compared byte
  for byte (a shell loop by hand twice compared nothing); `--cover` compares the instructions the
  cases cover instead, for a change meant to change them (then measure whole builds from scratch:
  a handful of functions moves with the search's path, 2026-10-20).
- **A generator change tried in variants**: every variant under one VERSION reuses the cases the
  first one cached (the key holds VERSION, not the code): remove the caches the variants made
  (BUILD/autocases of the worktree's examples and of any target tried) before the next variant and
  before `bin/take-caches`, or compare with `bin/ab-cases`. Never `git stash` in a worktree a
  background run uses: a lift check that imported the generator after the stash ran the old code,
  and the measurement showed nothing (2026-10-22).
- **A lifter change** (what it lifts, refuses or keeps across calls), **or a generator change that
  changes cases**: run `dread lift --check` on every gizmo build before the suite and commit the
  regenerated `config/lifted.txt` with the change; a table that loses a function is a regression to
  trace before the change goes in (2026-10-20: a generator change measured on a FreeRTOS build and
  four gizmo functions cost gizmo-msp430's script_run, which only the gizmo lift check showed).
  A test that plants a fault and shows it through a caller assumes the caller is the original's
  code; a caller that starts to lift can take the fault out of its reach (2026-10-20: uart_puts,
  lifted once it kept r1 across uart_putc, made two tests fail).
- **The loop prompt** only points here and at `docs/todo.md`. It may be rewritten when the way of
  working changes; the plan itself is changed in `docs/todo.md`.

## Conventions

- Python 3.11 or newer (`tomllib`), unicorn 2.1.4 (pinned: tools rely on its hook behaviour),
  capstone 5. The virtual environment is `.venv` in the checkout. `bin/dread COMMAND` runs a tool
  from the checkout; every command is also `python -m dread.MODULE`.
- Each tool is a module with `main(argv)`, registered in `dread/cli.py`. It takes `--target` and,
  where a target has several images, `--image`.
- Bulk runs are polite and parallel (`dread.util.polite`): lowest priority, a pool sized by
  `workers()`, slowest tasks first (`dread.util.poolorder`).
- Docstrings say what a tool does, what it assumes and what it does not establish.
- Commits go on `main`. The message says what was done and what is not confirmed.
