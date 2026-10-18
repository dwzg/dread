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

Written by the Ghidra pipeline or by `dread import-elf`. Not edited by hand.

### `code_ranges.tsv` (`code_ranges`)

The instruction runs of the image: `start`, `end` (exclusive), and optionally the instruction set
(`a32` or `t32`; default: the image's `[arch] isa`). Literal pools lie between runs. Runs use run
addresses, like the function table.

In Thumb code, instruction boundaries are found by walking a run from its start, so a run must
start at an instruction. Written by the Ghidra pipeline or by `dread import-elf`.

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

### `lifted.txt`, `domains.tsv`, `stale_readers.tsv`, `wide_results.txt`, `function_starts.txt`, `asm_raw_words.txt`

Described with the tools that use them, as those tools arrive in DREAD.
