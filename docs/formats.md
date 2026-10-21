# File formats

The tables, case files and reports that DREAD's tools exchange, and that target repositories keep.
They are interfaces: a change needs a note here on what older files mean, and readers stay
tolerant of older files.

Common to the text tables:

- one entry per line;
- `#` starts a comment;
- addresses are hexadecimal with `0x`;
- unknown extra columns are ignored, so a table may carry notes.

## An image's tables (in its `config` directory)

Each table's key, as `[image.NAME] tables` names it, is in parentheses.

### `functions.tsv` (`functions`)

The functions of the image: tab-separated `address`, `name`, `bytes`, then anything (Ghidra's
export adds the file and the prototype). A first line whose first field is not a number is a
header.

`bytes` counts the function's **instruction bytes** only. Literal pools and other data inside the
function are not counted. The extent of a function is found by walking the code ranges from its
entry until that many instruction bytes are collected. The walk stops at the next function's entry
and gives up 64 KiB behind the entry. `dread.program.Program` is the one implementation of that
walk. Entry plus size is **not** the end of a function with pools inside.

Code the startup copies to RAM is listed at its **run** address.

Written by the Ghidra pipeline (`dread ghidra`) or by `dread import-elf`. Not edited by hand. Two
details of the writers:

- `dread ghidra` names a thunk Ghidra shows under its target's name `thunk_NAME`, so that names stay
  unique.
- `dread import-elf` leaves out an alignment `nop` at the end of a function's last instruction run.
  GCC counts that nop in the symbol's size, but it never executes, and a test that has to execute
  every instruction would never be complete. Ghidra's analysis does not count it either.
- For the same reason `dread import-elf` cuts out of the code ranges the `nop` that GCC puts right
  behind an unconditional `add pc, pc, ...` in ARM state (a switch: pc reads as the instruction + 8,
  so no target lies at + 4) when no branch goes there. Ghidra's analysis leaves it out too.

### `code_ranges.tsv` (`code_ranges`)

The instruction runs of the image: `start`, `end` (exclusive), and optionally the instruction set
(`a32` or `t32`; default: the image's `[arch] isa`). Literal pools lie between runs. Runs use run
addresses, like the function table.

In Thumb code, instruction boundaries are found by walking a run from its start, so a run must
start at an instruction. Written by the Ghidra pipeline (always with the instruction set) or by
`dread import-elf`. Ghidra's heuristics sometimes take data for code, for instance a string behind a
pointer that a register carries. The listing still rebuilds, but such ranges are worth a look before
functions are written there.

### `symbols.txt` (`symbols`)

Names given by people or by generators: `f ADDRESS NAME` (a function), `d ADDRESS NAME` (data), or
`- ADDRESS` (a function start the analysis should take, without a name). Generated blocks inside
the file are marked by their generator. The analysis applies the names; the function table then
carries them.

### `annotations.txt` (`annotations`)

What is known about an address: `ADDRESS | text`, as many lines per address as needed. The listing
shows them as comments. `dread gui` writes it: a note replaces the lines of its address in place
(or goes to the end) and leaves every other line, comments included, as it was.

### `data_ranges.txt`, `asm_only.txt`, `left_out.txt`

Range tables, `START END reason` or `START-END reason` (end exclusive). `asm_only.txt` may also give
a single `ADDRESS reason`.

| Table | What it holds |
|---|---|
| `data_ranges` | ranges of the image that are data, with the evidence; the analysis creates no function there |
| `asm_only` | functions that stay assembly, with the reason (they use something C cannot express, for instance) |
| `left_out` | ranges the bulk tools leave alone, with the reason |

### `asm_raw_words.txt` (`asm_raw_words`)

Instructions that `dread asm` writes as raw `.inst` because GNU as encodes them differently from the
original bytes: one stored address per line. Maintained by `dread asm --verify`.

### `lifted.txt` (`lifted`)

The functions `dread recon` builds from lifted C (`dread lift`): one name per line, `#` comments.
Written by `dread lift --check`, which lists a function only when its generated cases (and the
kept and written ones) agree with the original and run every instruction of it; not edited by hand.
The lifted C itself is made again at every build (`BUILD/recon/IMAGE/lifted/`) and not kept; a name
that the src directory defines is left to src. `BUILD/recon/IMAGE/lifted.txt` says which functions
the last build lifted, and `dread test` gives each of them its generated cases.

### `readable.txt` (`readable`)

The functions of the lifted table that `dread recon` builds from readable C (`dread readable`): one
name per line, `#` comments. Written by `dread readable --check`, which lists a function only when its
readable C, built over the original with the other lifted functions, passes the cases its lifted C
passed (they agree and run every instruction) and the whole image's scenarios agree; not edited by
hand. The readable C is made from the lifted C at every build, as the lifted C is from the original:
after `dread lift --check` or a change of the transformations, `dread test` tests what the build now
makes, and `dread readable --check` decides again. A name not in the lifted table is ignored.

### `readable_names.json` (`readable_names`)

Names for the variables of readable C (dread.naming): `{"version": 1, "functions": {NAME: {"digest",
"names", "what", "by"}}}`. `names` maps the readable C's variable names (r0, r4, r2_1, s36) to the
proposed ones, `what` is a sentence on what the function does (a comment above it), `by` the
provider that proposed them, `digest` a digest of the readable C without names the proposal was made
for: when the transformations change that C, the entry is not applied (its names would land on
other variables) until it is asked again. Written by `dread pilot --task readable`, which keeps an
entry only when every name is a C identifier nothing else in the file uses and the C with the names
compiles to the same code; not edited by hand. `dread readable --score ELF` compares the names with
the source's (the ELF's debug information).

Its `globals` part names memory at fixed addresses: `{"0xADDRESS TYPE": {"name", "by"}}` (TYPE the
access's: u32, u8, ...). dread readable writes such an access as the name, a macro above the
function that is the access itself; without an entry, or where the function's C uses the name for
something else, the default name says where it is: `io_40004004` (a device register: `[memory]
io`), `rom_c6e4` (a rom region), `g_20029aac` (anything else), with the type for an access other
than a u32 one (`g_20029aac_u8`). The pilot asks for these before the functions' variables; they
need no digest, since an address does not change with the transformations.

### `readable_types.json` (`readable_types`)

The structures readable C declares (dread.shapes): `{"version": 1, "structs": {NAME: [[offset,
type], ...]}, "functions": {FUNCTION: {"digest", "vars": {VARIABLE: NAME}}}}`. A structure's fields
are those a C structure holds at their offsets without packing (naturally aligned, not overlapping,
the gaps bytes); a function's variables listed there are declared `volatile struct NAME *` and their
accesses written as members (`p->f_2c`), every other use cast to u32. `digest` is the readable C's
before names (dread.naming): a function whose C changed since gets no types until the next check.
Written by `dread readable --check` from every lifted function's readable C before it checks any;
the builds read it. Not edited by hand.

### `domains.tsv`, `stale_readers.tsv`, `wide_results.txt`, `function_starts.txt`

Described with the tools that use them, as those tools arrive in DREAD.

## The rebuilt image (`BUILD/recon/IMAGE/`)

Written by `dread recon` (and `dread overlay`): `payload.bin` (the image with the C linked in),
`recon.ld` and `recon.elf` (the link), and two reports of where each C function went.

### `report.tsv`

`function`, `address` (the original entry), `original_bytes`, `c_bytes`, `placement` (text for
people), then a `#` summary line. Not for programs; they read placement.json.

### `placement.json`

```json
{"functions": {"NAME": {"entry": 131596, "address": 134300, "original_bytes": 180, "c_bytes": 184,
                        "placement": "behind the image", "branch": true, "isa": "a32"}},
 "summary": {"replaced": 13, "functions": 50, "image_bytes": 3440, "room_left": 127632,
             "freed": 216, "reused": 148}}
```

`placement` is one of `in place`, `over its pools`, `behind a pool`, `freed space`, `free space`
(one of the image's free ranges, `[image] free`), `behind the image`, or `helper` (a C function the
table does not have; no `entry`). The summary's `free_left` is what the free ranges still hold. `branch` says
that the old entry holds a branch to `address`; the test runner enters such a function through it.

## Generated, kept and written cases (JSON)

Generated cases (cached in `BUILD/autocases/IMAGE/NAME.json` as `{"key", "seed", "cases"}`), the
image's `kept_cases/NAME.json` (cases that once showed a difference; `--keep-failing` adds to it) and
`extra_cases/NAME.json` (cases written to reach what the generator does not) share one format,
`{"cases": [CASE, ...]}`, with these fields:

| Field | Meaning |
|---|---|
| `args` | the argument registers |
| `mem` | `[[address, size, value], ...]` written before the call (peripheral registers too) |
| `returns` | the answers of the stubbed callees, in call order (later calls answer 0) |
| `calls` | `[[callee, register mask, stack words], ...]`: the generic stubs; written cases without it get auto()'s; recorded cases (`dread record`) carry the ones they were recorded with, and those are kept |
| `frame` | the bytes of the function's frame a stub may fill behind a pointer to a local (as `calls`: from auto() unless the case carries it) |
| `effects` | `[[call number, address, size, value], ...]` written by that stub call before it returns |
| `fills` | `[[call number, which pointer, offset, size, value], ...]` set inside what a stub stored behind a pointer |
| `io` | `"fixed"`: peripheral reads return the `mem` values whatever was written |
| `poll` | `[[address, n, value], ...]`: from the n-th read of that register on it returns value |
| `later` | `[[address, size, n, value], ...]`: just before the n-th read that starts at address, memory there becomes value, once (the code's own writes stand after it): what other tasks or handlers wrote while a recorded call waited (`dread sysrecord`) |
| `stops` | `[address, ...]`: callees whose call ends the run (one that never returns, one a run hung in): the case is compared up to that call |
| `limit`, `stub_calls` | instructions (default 200,000: a budget of that many of the instruction set's shortest instructions, counted in bytes of code entered) and stub calls (default 256) one run may take |
| `hang` | true: not returning is the result (compared up to the limit) |
| `tight` | true: a stub's fill stops at the first stack word the function stored itself |
| `carried` | true: an input of an earlier generation, kept when the cases were made anew because it runs instructions the new ones do not, or that only new ones run whose outcome belongs to the original's frames (dread.autocase's carried; for reading only, the runner treats it like any case) |
| `firm` | true: a fragile case (its outcome changes with the stack, or its input makes code touch a saved register: dread.fragile) with one input set small, added because it runs instructions only fragile cases ran (dread.autocase's firm_up; for reading only) |
| `why`, `by` | where a written case comes from and why (`dread mutate --kill`, `dread record`); nothing reads them but people |
| `kept` | in kept cases: `"stubbed"` or `"real callees"` |

## Test cases (the image's `cases` directory)

Python files that call the case API of `dread.cases` (`case`, `auto`, `unreachable`, `entry`,
`logging_stubs`, `words`), run in name order with those names defined. The module's docstring is the
reference: what a case is and what is compared.

### `BUILD/times/`

Written by every parallel run (`dread test`, `dread integration`): `NAME.json` keeps the seconds
per item of the last full run, for handing the slowest out first; `NAME.tsv` is the report of the
last run, `item` and `seconds`, slowest first, ending with a comment line with the total seconds of
work and the wall-clock time of the run. A generated-case cache entry
(`BUILD/autocases/IMAGE/NAME.json`) also says how many `seconds` its generation took, and with
`firm` the version of the firm-up its cases had (dread.autocase.FIRM: an entry without it, or with an
older one, gets the firm-up once, its cases kept; a `failed` entry holds the generation's error).

### `argued.tsv` (beside the cases directory)

Survivors of `dread mutate` that cannot be caught, with the argument why the change alters nothing:
tab-separated `function`, the source line as written (stripped), the change as `dread mutate` names
it (`>= -> >`, `4 -> 5`, `removed`, `a <-> b`), and the argument. A survivor that matches is listed
with its argument and not counted. The key includes the line's text, so an argument stops applying
when the line changes.

### `kept.tsv` (beside the cases directory)

Registers that `dread regflow` finds read by callers after a call, but whose difference at the
return cannot matter, with the argument why: tab-separated `function`, `register` (`r1`, `r12`,
`flags`, ...), and the argument. `dread test` and `dread integration` do not compare that register
at that function's return and list the argument under the function's result. Typical arguments: the
caller reads it only on a path that cannot happen, or only bits the next callee ignores.

### `tests.tsv`

Written beside the rebuilt image by a complete `dread test`: `function`, `cases`, `failed`, `missed`
(the instructions of the original no case ran, as hex addresses separated by spaces; empty when
the cases run all of it).

### `BUILD/lift-check/IMAGE/verdicts.tsv`

Written by `dread lift --check`: one line per function it looked at, `function`, `verdict`
(`accepted`, `refused`: the lifter cannot translate it, `dropped`: lifted, but the cases or the
system run disagree or do not run all of it, or the overlay cannot place it; `by hand`: src defines
it), `reason` (the lifter's or the test run's words), `missed` (for a dropped function, the
instructions no case ran). `dread gui` reads it; `dread rename` renames in it.
