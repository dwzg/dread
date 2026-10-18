# The target description: `dread.toml`

A target is a directory with a `dread.toml`, usually the root of the repository that reconstructs
one firmware. Everything a DREAD tool needs to know about that firmware comes from this file and
the tables it points to. Tools find it through `--target DIR`, `$DREAD_TARGET`, or by walking up
from the working directory.

Unknown sections and keys are errors, so a typo does not go unnoticed. Numbers may be written in
hex (`0x20000`). Paths are relative to the target directory. `dread info` prints the target as
DREAD reads it.

## Example

The Cortex-M3 build of gizmo (`examples/gizmo/m3/dread.toml`):

```toml
[target]
name = "gizmo-m3"
title = "Gizmo 1000 (Cortex-M3)"

[arch]
name = "arm"
isa = "t32"
profile = "m"
cpu = "cortex-m3"

[image.main]
original = "original/gizmo.bin"
base = 0x08000000
limit = 0x08010000
entry = "Reset_Handler"
startup = [
    {kind = "code", run = 0x20000000, size = 0x30, stored = 0x8000758},
    {kind = "data", run = 0x20000030, size = 0x1c, stored = 0x8000788},
    {kind = "zero", run = 0x2000004c, size = 0x2c},
]

[memory]
regions = [
    {name = "flash", base = 0x08000000, size = 0x100000, kind = "rom"},
    {name = "sram", base = 0x20000000, size = 0x5000},
    {name = "scratch", base = 0x20100000, size = 0x20000},
]
io = [[0x40000000, 0x40020000], [0xe000e000, 0xe000f000]]
stack = {top = 0x20005000, size = 0x1000}
sentinel = 0x1fff0000
scratch = {base = 0x20100000, size = 0x20000}

[compiler]
cross = "arm-none-eabi-"
cflags = ["-mcpu=cortex-m3", "-mthumb", "-Os", "-ffreestanding", "-fno-builtin", "-ffunction-sections"]
```

## Sections

### `[target]`

| Key | Meaning |
|---|---|
| `name` | short name, required |
| `title` | a line for reports (default: the name) |

### `[arch]`

| Key | Meaning |
|---|---|
| `name` | `"arm"`, required (the only architecture so far) |
| `isa` | the instruction set of code the code ranges do not mark: `"a32"` (ARM state, default) or `"t32"` (Thumb, 16- and 32-bit) |
| `profile` | `"a"` (classic cores with CPSR: ARM7, ARM9, ARM11; default) or `"m"` (Cortex-M, Thumb only) |
| `cpu` | Unicorn's CPU model (`cortex-m3`, `arm926`, `arm1176`, ...); default: Unicorn's choice for the mode |
| `endian` | `"little"` (the only one supported) |
| `ghidra` | Ghidra's language id (default `ARM:LE:32:Cortex` for M profile, `ARM:LE:32:v4t` otherwise) |

Unicorn has no ARM7TDMI model. An ARM7 target runs on the default model or on `arm926`. The
differences that can matter, such as unaligned loads, are not modelled.

### `[image.NAME]`

A target has one or more images: a firmware and its boot loader, for instance. The first is the
default. `--image NAME` or `$DREAD_IMAGE` selects another.

| Key | Meaning |
|---|---|
| `original` | the raw image every comparison is made against, loaded at `base`; required |
| `base` | its load address; required |
| `vendor` | the vendor's file the image was taken from, when it has a container |
| `container` | plugin that unpacks and packs `vendor` |
| `limit` | the end of the room the overlay may use behind the image (default: the image's end) |
| `erased` | the value of unprogrammed flash (default `0xff`) |
| `entry` | the reset entry, an address or a symbol name from the image's symbols (default: `base`) |
| `config` | the directory of the image's tables (default `config`) |
| `cases`, `extra_cases`, `kept_cases`, `record_specs` | where its test cases live (defaults under `tests/`) |
| `src` | the directory of its C (default `[paths] src`) |
| `startup` | what the startup copies before the code runs (below) |
| `tables` | `{key = "file name"}` for tables with other names (`docs/formats.md`) |
| `arch` | keys of `[arch]` that differ for this image |

`startup` is a list of copies, `{kind, run, size, stored}`. `kind` is one of:

- `"code"`: bytes stored at `stored` that run at `run`;
- `"data"`: data stored at `stored` that is used at `run`;
- `"zero"`: a range the startup clears, with no `stored`.

Code that runs from RAM is analysed at its run address. The program database reads its bytes at
the stored address. Instead of a list, `startup = {plugin = "file.py:function"}` names a function
`function(image)` that returns `dread.target.Copy` objects, read from the image itself (from the
startup's own copy table, for instance).

### `[memory]`

| Key | Meaning |
|---|---|
| `regions` | `[{name, base, size, kind}]`; `kind` is `"rom"`, `"ram"` (default) or `"alias"` (separate memory standing for a mirror; not a real mirror) |
| `io` | `[[low, high], ...]`: peripheral ranges; every access there is logged in order and compared |
| `stack` | `{top, size}`: the stack a called function gets |
| `sentinel` | the return address a called function gets; the run stops when it arrives there |
| `scratch` | `{base, size}`: memory the firmware does not use, for the buffers of generated inputs |

Regions and peripheral ranges may not overlap. On an M-profile core the sentinel has to lie in an
executable region: from `0xe0000000` up everything is execute-never there, and a return to such an
address faults before the run can stop.

### `[abi]`

| Key | Meaning |
|---|---|
| `args` | how many argument registers (default 4: r0-r3) |
| `callee_saved` | the registers a function preserves (default r4-r11) |
| `result` | the registers a result comes back in (default r0, r1) |

### `[compiler]`

| Key | Meaning |
|---|---|
| `cross` | the prefix of the cross tools (default `arm-none-eabi-`) |
| `cflags` | the flags C for this target is compiled with |
| `include` | include directories |
| `idioms` | the family of compiler idioms the lifter recognises |

### `[paths]`, `[host]`, `[models]`, `[plugins]`

- `[paths]`: `build` (default `build`) and `src` (default `src`).
- `[host]`: settings of the machine the tools run on (`jobs`, `free_cores`, `nice`). The environment
  variables of `dread.util.polite` override them.
- `[models]` and `[plugins]`: free-form sections owned by the tools that read them, such as the
  device models of the system run.

## Plugins

A plugin is given as `"module:attribute"`, a module importable from the target directory, or as
`"file.py:attribute"`, a file in the target. A plugin holds what is specific to one target and needs
code rather than a value: unpacking a vendor's container, reading a startup's copy table, a device
model.
