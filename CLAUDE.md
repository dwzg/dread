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
8. **Public, Apache-2.0** (github.com/dwzg/dread). Before every push, run `dread leakcheck` with the
   tables and words of each private project code was taken from (the exact command is kept with
   that project, not here); a hit that is not a known coincidence stops the push. A leak, once
   pushed, cannot be taken back.

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
