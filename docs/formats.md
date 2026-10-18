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
shows them as comments.

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

### `lifted.txt`, `domains.tsv`, `stale_readers.tsv`, `wide_results.txt`, `function_starts.txt`

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

`placement` is one of `in place`, `over its pools`, `behind a pool`, `freed space`,
`behind the image`, or `helper` (a C function the table does not have; no `entry`). `branch` says
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
| `calls` | `[[callee, register mask, stack words], ...]`: the generic stubs; for written cases taken from auto()'s options, not from the file |
| `frame` | the bytes of the function's frame a stub may fill behind a pointer to a local |
| `effects` | `[[call number, address, size, value], ...]` written by that stub call before it returns |
| `fills` | `[[call number, which pointer, offset, size, value], ...]` set inside what a stub stored behind a pointer |
| `io` | `"fixed"`: peripheral reads return the `mem` values whatever was written |
| `poll` | `[[address, n, value], ...]`: from the n-th read of that register on it returns value |
| `limit`, `stub_calls` | instructions (default 200,000) and stub calls (default 2,000) one run may take |
| `hang` | true: not returning is the result (compared up to the limit) |
| `tight` | true: a stub's fill stops at the first stack word the function stored itself |
| `why`, `by` | where a written case comes from and why; nothing reads them but people |
| `kept` | in kept cases: `"stubbed"` or `"real callees"` |

## Test cases (the image's `cases` directory)

Python files that call the case API of `dread.cases` (`case`, `auto`, `unreachable`, `entry`,
`logging_stubs`, `words`), run in name order with those names defined. The module's docstring is the
reference: what a case is and what is compared.

### `tests.tsv`

Written beside the rebuilt image by a complete `dread test`: `function`, `cases`, `failed`.
