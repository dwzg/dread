# Working on DREAD

DREAD is a toolkit for rewriting firmware in C and proving the result in an emulator
(`README.md`). It serves other repositories, the targets, which hold the facts of one firmware each.
Read `docs/target.md` before changing how a tool reads its target, and `docs/formats.md` before
changing a table, case or report format.

## Rules

1. **No vendor material.** Nothing derived from a real firmware goes into this repository: no bytes,
   no disassembly, no addresses, function or variable names, register maps or other facts of a real
   target. That also holds for comments, docstrings, tests, examples and commit messages. Examples
   use gizmo (`examples/gizmo`), which is our own code. A real target's facts live in that target's
   repository, in its `dread.toml`, its tables and its plugins.
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
  open item. Finish it with tests on both gizmo
  builds, tick it off in the same commit, push. Change the plan when the work shows a better one (an
  item is bigger than thought, a bug comes first, an approach turns out wrong): split, add, reorder or
  drop items, and say why in the commit message. Every few items, check with numbers (test times,
  coverage, what an item gained) whether the order still makes sense.
- **Never block.** A decision that is the user's (a matter of taste, anything public or
  irreversible beyond the agreed pushes, credentials, buying or installing system software) goes
  into "Questions for the user" in `docs/todo.md` with the options and a recommendation; then go on
  with what does not depend on it.
- **When stuck**, after two serious attempts or a few hours on one item: write down what was tried
  and why it failed in the item, put the tree back to the last commit, and take the next item.
- **Commit only green**: `.venv/bin/pytest` passes, both gizmo builds. Push only through the leak
  check (rule 9) with a script that stops on its exit status; never force-push without the user.
- **Stay polite on the machine**: long runs go to the background (polite, as the tools are);
  nothing that fills memory or runs without a bound.
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
