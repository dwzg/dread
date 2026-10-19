# dread cclab: a compiler laboratory

Put a compiler in and learn how it compiles. A corpus of small probe programs (plain C99, each
function asking one question) is compiled and linked for every combination of a compiler's option
matrix, and each build is run in DREAD's emulator:

- where each argument arrives and where results come back (registers, stack words, memory through
  a pointer the caller passes), for ints, 64-bit values, structs of every size and varargs;
- the basic types: sizes, alignments, plain char's sign, sign extension of narrow results, bit
  field order;
- which runtime helper each operation calls (division, 64-bit arithmetic, soft float, copies), with
  every name it has, what it reads and returns, and the registers it changes;
- the shape of every probe's code: size, calls and tail calls, first and last instructions, the
  instructions that mark an idiom (a division instruction, a table branch, block transfers).

The answers come from running the code, not from matching patterns, so they hold for a compiler
DREAD has never seen. `report` compares the builds of one compiler; a work directory can hold
several compilers side by side.

## The compilers it was tried on

Here: GCC 16.2, 14.2 and 10.3 (`gcc-arm`, the latter two by their paths), clang 23.1 linked with
GCC's libgcc (`clang-arm`), Arm Toolchain for Embedded 23.1 with its own runtime and C library
(`atfe-arm`), and TI's Arm compiler 20.2.7 with its own linker and library (`ti-arm`): every build of
every matrix measured. IAR (`iar-arm`) could not be tried here: its adapter follows IAR's documented
command line (EWARM 9.70: `--cpu=`, `--fpu=none`, `-O[n|l|m|h|hs|hz]`; ILINK's `--config`,
`--entry`, `--keep`) and was run against a stand-in that takes exactly those option forms and links
with GCC below (tests/test_cclab.py). The doctor is there for what that leaves open.

## At work: IAR EWARM on Windows, DREAD in WSL

DREAD needs Python 3.11 or newer (Ubuntu 24.04's `python3` in WSL; on 22.04, 3.11 from the
deadsnakes PPA) and installs unicorn and capstone from PyPI.

```sh
# in WSL
git clone https://github.com/dwzg/dread.git && cd dread
python3 -m venv .venv && .venv/bin/pip install -e .       # unicorn and capstone come with it
bin/dread cclab adapters                                  # the built-in adapters

mkdir -p /mnt/c/cclab-work                                # IAR's tools see the Windows side
bin/dread cclab doctor --adapter iar-arm --work /mnt/c/cclab-work
```

The doctor finds `iccarm.exe` and `ilinkarm.exe` (under `C:\Program Files*\IAR Systems\*\arm\bin`,
the newest installation), prints the compiler's version, then compiles, links and runs one probe.
When a step fails it prints the command and the tool's output. The fix goes into a copy of the
adapter:

```sh
cp dread/cclab/adapters/iar-arm.toml /mnt/c/cclab-work/iar-arm.toml   # edit [tools] or [commands]
bin/dread cclab doctor --adapter /mnt/c/cclab-work/iar-arm.toml --work /mnt/c/cclab-work
```

Then the whole matrix, and the other compilers on the same machine for comparison:

```sh
bin/dread cclab run --adapter /mnt/c/cclab-work/iar-arm.toml --work /mnt/c/cclab-work
bin/dread cclab run --adapter gcc-arm --work /mnt/c/cclab-work
bin/dread cclab run --adapter clang-arm --work /mnt/c/cclab-work
bin/dread cclab report --work /mnt/c/cclab-work          # one report.md per compiler
bin/dread cclab bundle --work /mnt/c/cclab-work --out cclab-bundle.zip
```

## What may be read, shown and taken home

A build's directory holds our probes (`src/`), their objects (`obj/`: our code as the compiler made
it), the linked image (`image.elf`, `original/`) and the tables made from it (`config/`). The image
and its tables also hold the toolchain's runtime library: for IAR, IAR's code.

- An LLM (Claude Code at work, or one at home) sees our probes, their compiled code and the
  compilers' own messages: never the company's code and never a compiler's runtime library code
  (the user, 2026-10-19). `run` writes a `CLAUDE.md` and Claude Code deny rules
  (`.claude/settings.json`: the images, binaries and tables) into the work directory; start Claude
  Code there, so that both apply.
- `bundle` packs only what may leave: results, build logs, reports, the compiler's version text,
  the probe sources and objects. No image, binary or table.
- What the lab learns about IAR stays out of DREAD's public repository (CLAUDE.md, rule 1).

## The profile, for DREAD's other tools

`report` also writes `WORK/ADAPTER/profile.toml`: every runtime helper the probes called, with all
its names, its result registers (what the compiler's own code reads after a call of it: DREAD's
register flow on every build's image) and the registers the runs saw it change. A target built by
that compiler names it in its `dread.toml`:

```toml
[compiler]
profile = "iar-arm-profile.toml"
```

and DREAD's register flow takes each helper's results as read after every call of it, so that a
lifted helper returns all of its result and its cases compare it (C compiled anew calls the helper
by that contract, whatever the original's callers read of it). A profile of IAR stays with the
target it describes, outside DREAD's repository.

## Adapters

An adapter is a TOML file: the tools (a name on PATH, a path, a glob, or a list of those), the
command templates for compiling and linking, the option matrix, and the linker family the lab
writes the memory map for (`gnu`: a GNU ld script, `iar`: an ILINK configuration, `ti`: a TI linker
command file). The fields are described in `dread/cclab/adapters.py`. `windows = true` makes every
file name in a command go through `wslpath -w`.

## Probes

A probe file is a C file in `dread/cclab/probes/` with a TOML file of the same name that says what
each function shows (`arg`, `result`, `value` or `op`, see `dread/cclab/lab.py`). Probes are plain
C99, every function external (nothing may inline it away), with no initialised writable data (the
lab runs functions without the toolchain's startup code).

## What it does not establish

Anything about code the probes do not contain; a register a helper did not change in the runs that
were made may still be changed by other inputs; a probe's C may mean different things to compilers
where C leaves it open (that is why the probes keep to plain C99). A helper's result registers in the
profile are those some probe read: a probe that uses a result is what adds it. Not yet measured:
prologue and epilogue variants as such, literal pools and padding, hard-float calling conventions,
C++.
