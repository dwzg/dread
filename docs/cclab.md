# dread cclab: a compiler laboratory

Put a compiler in and learn how it compiles. A corpus of small probe programs (plain C99, each
function asking one question) is compiled and linked for every combination of a compiler's option
matrix, and each build is run in DREAD's emulator, on any architecture DREAD emulates (ARM, RISC-V,
MSP430):

- where each argument arrives and where results come back (registers, stack words, memory through
  a pointer: a struct passed or returned by reference), for ints, 64-bit values, structs of every
  size and varargs, and floats and doubles with soft and hard float (on ARM: s0-s15, back-filling,
  structs of floats, variadic doubles);
- the basic types: sizes, alignments, plain char's sign, sign extension of narrow results, bit
  field order;
- which runtime helper each operation calls (division, 64-bit arithmetic, soft float, copies), with
  every name it has, what it reads and returns, and the registers it changes;
- what a function keeps across a call: which callee-saved registers it stores in its frame, the
  return address, the frame at its calls, and from that the stack alignment the compiler keeps;
- the shape of every probe's code: size, calls and tail calls, first and last instructions, the
  instructions that mark an idiom (a division instruction, block transfers), and how a switch
  dispatches: a table branch (tbb), a computed pc (mov pc, add pc, c.jr), a switch helper (GCC's
  `__gnu_thumb1_case_uqi` on the Cortex-M0 at -Os), or a chain of compares;
- C++ (probes by their mangled names): where `this` and a member function's arguments arrive,
  whether a constructor returns `this` (the ARM C++ ABI says it does; a constructor that calls a
  function first tells it from a register left alone), how a class with a destructor comes back
  (through memory, however small), how a virtual call is made (GCC at -O2 devirtualises it on a
  guess and keeps the indirect call for other types: "a call through a register in its code, not
  taken"), what new and delete call;
- where the functions lie: the alignment of their entries, literal pools between them (a pool counts
  when any function loads from it or takes its address pc-relatively: TI's armcl shares one per
  file), the padding in front of a pool or the next function (none, zeros, no-ops), the
  instructions functions start and end with: what tells a function's start in a stripped image.

The answers come from running the code, not from matching patterns, so they hold for a compiler
DREAD has never seen. `report` compares the builds of one compiler; a work directory can hold
several compilers side by side.

## The compilers it was tried on

Here: GCC 16.2, 14.2 and 10.3 for ARM (`gcc-arm`, the latter two by their paths), clang 23.1 linked
with GCC's libgcc (`clang-arm`), Arm Toolchain for Embedded 23.1 with its own runtime and C library
(`atfe-arm`), TI's Arm compiler 20.2.7 with its own linker and library (`ti-arm`), GCC 16.2 for
RV32 (`gcc-riscv`: riscv64-elf-gcc's rv32imc and rv32ic multilibs) and TI's MSP430 GCC 8.2
(`gcc-msp430`): every build of every matrix measured. Each architecture's procedure call standard
came out as written, including its fine print: on RV32 a 64-bit argument takes any free register
pair but a variadic one an even pair; on the MSP430 a 64-bit argument takes r12-r15 or the stack and
a later int fills the register left free; the stack is kept 8-byte aligned at calls on ARM,
16-byte on RISC-V, 2-byte on the MSP430.

Hard float (Cortex-M4 with FPv4-SP, each ARM adapter's `cortex-m4f`, run on the emulator's own
Cortex-M4): GCC, clang and Arm's LLVM toolchain follow AAPCS's VFP variant to the letter. TI's armcl
20.2.7 with `--float_support=FPv4SPD16` does not back-fill: the float after a double, a float and a
double arrives in s6, where the others put it in s3; `compare` lists it, with no option to close
it. (TI also leaves a returned double in r0:r1 beside d0; the lab reads d0, the VFP registers first.)

IAR (`iar-arm`) could not be tried here: its adapter follows IAR's documented command line (EWARM
9.70: `--cpu=`, `--fpu=none`, `-O[n|l|m|h|hs|hz]`; ILINK's `--config`, `--entry`, `--keep`) and was
run against a stand-in that takes exactly those option forms and links with GCC below
(tests/test_cclab.py). An image whose sections carry ILINK's kind of names (P1, P2 instead of
.text) imports the same (the importer goes by section flags and segments). The doctor is there for
what that leaves open.

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
adapter (commands, options, tools, and if ILINK rejects the lab's configuration, a `script`
template of its own, see `dread/cclab/adapters.py`):

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

A build's directory holds our probes (`probes/`), their objects (`obj/`: our code as the compiler
made it), the linked image (`image.elf`, `original/`) and the tables made from it (`config/`). The
image and its tables also hold the toolchain's runtime library: for IAR, IAR's code. (`src/` is
left to DREAD's tools: a build is a DREAD target, and src/ is where its rewritten C would go.)

For a proprietary toolchain the lab guards what an LLM sees (the user, 2026-10-19: an LLM sees our
probes, their compiled code and the compilers' own messages; never the company's code and never a
compiler's runtime library code). An adapter asks for it with `guard = true` (`iar-arm` and `ti-arm`
do), and `--guard` asks for it with any adapter:

- `run` and `doctor` write a `CLAUDE.md` and Claude Code deny rules (`.claude/settings.json`: the
  images, binaries and tables) into the work directory; start Claude Code there, so that both apply.
- Each build's `dread.toml` names the functions an LLM may be shown, the probes and the lab's entry
  (`[llm] only`). `dread pilot` asks about no other function, whatever its provider: a question
  shows one function's code, its callees by name only.
- `bundle` packs only what may leave: results, build logs, reports, the compiler's version text,
  the probe sources and objects. No image, binary or table. (It does so with or without the guard.)
- What the lab learns about IAR stays out of DREAD's public repository (CLAUDE.md, rule 1).

## Two compilers side by side

DREAD rebuilds a function with GCC or clang and links it over an image another compiler made: the
rebuilt C calls the original's code and is called by it, so the two compilers must agree on the
calling convention, the types and what a call keeps. `compare` says where they do not:

```sh
bin/dread cclab compare --work /mnt/c/cclab-work iar-arm gcc-arm     # the original's, the rebuilding one
```

`WORK/compare-iar-arm-gcc-arm.md` lists the probes whose results differ (each compiler's results
with the builds that gave them), the options of the rebuilding compiler that close a difference
(`-fshort-wchar`, `-fno-short-enums`, `-fsigned-char`, `-fpcc-struct-return`, ...: they go into the
target's `[compiler] cflags`), and apart, the code's differences (helpers, inline code, saves and
frames), which matter to a reader, not to linking. TI's armcl against GCC, measured here: one
difference that matters, `wchar_t` of 2 bytes against 4 (`-fshort-wchar`); 42 probes agree; 31
differences in the code (TI's tail-calls its helpers at every level but -Ooff, a `tbb` table for a
dense switch). Arm GCC's own defaults (arm-none-eabi): short enums (the objects say
`Tag_ABI_enum_size: small`), unsigned plain char. The bundle carries the comparisons.

## A language model at work

`dread pilot` (and so the lab's builds) asks its questions through a provider of
`~/.config/dread/llm.toml` (`dread/llm.py` has every key). Three fit a workplace that already has an
approved service:

- GitHub Copilot's CLI in its headless mode (`kind = "command"`, the `copilot` preset): the
  question goes to Copilot under the company's plan; DREAD runs the CLI in an empty directory with
  its shell, write and url tools denied. On a Business or Enterprise plan the CLI is switched on by
  an administrator's policy.
- GitHub Models (`kind = "openai"` with `path = "/chat/completions"`, the `github-models` preset):
  a plain chat endpoint, no agent, a token with `models:read`; the organisation must allow it.
- Claude Code's headless mode (`command = ["claude", "-p", "{prompt}"]`): tried here through the
  local model's wrapper; it reads the user's own CLAUDE.md along with the question.

Both GitHub presets follow GitHub's documentation and were not tried here. `dread llm ask
"question"` tries a provider with one question and prints the answer and the time it took;
`dread llm providers` lists them. Whatever the provider, a guarded build's `[llm] only` decides
which functions reach it.

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

Each build is a DREAD target of its own, so the other tools run on it unchanged (`dread lift
--target BUILD` lifts the probes and the runtime library alike): a way to try DREAD's pipeline on a
compiler's code generation with nothing but the probes.

## Adapters

An adapter is a TOML file: the tools (a name on PATH, a path, a glob, or a list of those), the
command templates for compiling and linking, the option matrix, the architecture (`arch`, `isa`,
as in a `dread.toml`), and the linker family the lab writes the memory map for (`gnu`: a GNU ld
script, `iar`: an ILINK configuration, `ti`: a TI linker command file). The fields are described in
`dread/cclab/adapters.py`. `windows = true` makes every file name in a command go through
`wslpath -w`; `guard = true` is described above.

The memory map comes from the architecture (`MAPS` in `dread/cclab/lab.py`: flash, RAM and the
lab's scratch memory, the stack at the top of RAM, the sentinel return address in unmapped memory);
an adapter's `[memory]` table, with the same keys, replaces any of them (a core whose memory lies
elsewhere). The scratch memory may not overlap the stack arguments above the stack's top: the lab
refuses such a map.

## Probes

A probe file is a C file in `dread/cclab/probes/` with a TOML file of the same name that says what
each function shows (`arg`, `result`, `value`, `op` or `keeps`, see `dread/cclab/lab.py`). Probes
are plain C99, every function external (nothing may inline it away), with no initialised writable
data (the lab runs functions without the toolchain's startup code).

A probe file may be C++ (`.cpp`): an adapter compiles it with its `compile_cxx` command (without
one, its builds leave the C++ probes out and say so in build.json), without exceptions and RTTI, and
links the lab's C++ pieces (`cxx_support.c`: operator new and delete by their mangled names,
`__cxa_pure_virtual`) where the toolchain's library is not linked (`cxx_support`, by default as
`support`). Every compiler tried here mangles as the Itanium C++ ABI does, and on ARM every one
returns `this` from a constructor.

Probes of one's own live wherever one likes, in a directory of C files with a TOML of specs each,
and join DREAD's with `--probe-dir DIR` (`--probes NAME` picks files by name). In a guarded work
directory, its CLAUDE.md asks Claude Code to put a probe it writes for an open question into
`own-probes/`: plain C written for the question, never code copied from the company.

A spec's values are C-typed, so that one spec serves every architecture: a plain number is an int
(two bytes on the MSP430, four elsewhere); `"u64:..."`, `"i64:..."`, `"float:BITS"`,
`"double:BITS"` and `"ptr:scratch+N"` (an address in the lab's scratch memory) say their type; the lab
splits each into register-wide words, lowest first. An `arg` spec names the argument's C `type`; a
`result` spec its `value` and `type`, or the `fields` of a struct and their `field` type.

How `arg` finds a place: every argument register holds a marker (one byte repeated across the
register, naming it) and so do 16 stack words; the marker that comes back names where the argument
arrived. When none does (the ABI passes the value by reference), a second run points every argument
register at a buffer of its own whose bytes name register and offset, and the answer is
"memory at a0+8".

## What it does not establish

Anything about code the probes do not contain; a register a helper did not change in the runs that
were made may still be changed by other inputs; a probe's C may mean different things to compilers
where C leaves it open (that is why the probes keep to plain C99). A helper's result registers in the
profile are those some probe read: a probe that uses a result is what adds it. The stack alignment
is the largest power of two that divided every frame at a call: a measurement, from an aligned
entry. Not yet measured: prologue and epilogue variants as such; C++ exceptions, RTTI and static
construction. Not yet possible: big-endian targets, and the 8-bit cores of
compilers such as SDCC, which need an engine of DREAD's own first (docs/todo.md: Z80, then 8051).
