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
| `name` | `"arm"`, `"riscv"` or `"msp430"` (the 16-bit MSP430, on DREAD's own engine; under way), required |
| `isa` | the instruction set of code the code ranges do not mark. ARM: `"a32"` (ARM state, default) or `"t32"` (Thumb, 16- and 32-bit); RISC-V: `"rv32"`; MSP430: `"msp430"` |
| `profile` | ARM: `"a"` (classic cores with CPSR: ARM7, ARM9, ARM11; default) or `"m"` (Cortex-M, Thumb only) |
| `extensions` | RISC-V: the standard extensions, default `"imc"` (the C extension decides whether instructions may be 2 bytes long; `-march` in `[compiler] cflags` overrides it) |
| `cpu` | Unicorn's CPU model (`cortex-m3`, `arm926`, `arm1176`, ...); default: Unicorn's choice for the mode |
| `endian` | `"little"` (the only one supported) |
| `ghidra` | Ghidra's language id (default `ARM:LE:32:Cortex` for M profile, `ARM:LE:32:v4t` otherwise, `RISCV:LE:32:default` for RISC-V, analysed with Ghidra's `gcc` compiler spec) |

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
| `fixed` | registers that hold one value in the whole program, `{gp = 0x80080800}`: RISC-V's `gp`, which GCC's code addresses small data from (the startup sets it to `__global_pointer$`; `dread import-elf --write-startup` writes it). The emulator sets them before every call, and the lifter takes them as constants and refuses a function that writes one |
| `free` | ranges `[[start, end], ...]` inside the image that hold nothing (erased flash between the code and a vector table at the top of a microcontroller's flash): the overlay puts moved functions and the C's own code there; `dread import-elf --write-startup` writes them from the gaps between the ELF's sections |
| `erased` | the value of unprogrammed flash (default `0xff`) |
| `entry` | the reset entry, an address or a symbol name from the image's symbols (default: `base`) |
| `vectors` | where the exception vectors lie (A profile) or the vector table (M profile: the system run takes the stack pointer and the reset handler from its first two words); default `base` |
| `config` | the directory of the image's tables (default `config`) |
| `cases`, `extra_cases`, `kept_cases`, `record_specs` | where its test cases live (defaults under `tests/`) |
| `src` | the directory of its C (default `[paths] src`) |
| `startup` | what the startup copies before the code runs (below) |
| `tables` | `{key = "file name"}` for tables with other names (`docs/formats.md`) |
| `arch` | keys of `[arch]` that differ for this image |

`startup` is a list of copies, `{kind, run, size, stored}`. `kind` is one of:

- `"code"`: bytes stored at `stored` that run at `run`;
- `"data"`: data stored at `stored` that is used at `run`;
- `"zero"`: a range the startup clears, with no `stored`;
- `"load"`: bytes a loader puts into memory beside the image, `{kind = "load", run, size, file}`, with
  no `stored`: an ELF whose initialised data lies in RAM with no copy in flash (QEMU's ELF loader
  fills RAM so, and the startup copies nothing). The emulator and the system run put the file's
  bytes at `run` before any code runs. `dread import-elf` takes every section with contents outside
  the memory region the image's base lies in for such bytes, writes each run to `load-RUN.bin`
  beside the payload, and leaves them out of the payload.

Code that runs from RAM is analysed at its run address. The program database reads its bytes at
the stored address. `dread import-elf` prints the list for an image with an ELF; with
`--write-startup` it puts it into the image's section of `dread.toml` itself (gizmo's Makefile does,
so the list cannot go stale when the build moves the stored copies). Instead of a list, `startup = {plugin = "file.py:function"}` names a function
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
| `args` | how many argument registers (default ARM 4: r0-r3; RISC-V 8: a0-a7) |
| `callee_saved` | the registers a function preserves (default ARM r4-r11; RISC-V s0-s11) |
| `result` | the registers a result comes back in (default ARM r0, r1; RISC-V a0, a1) |

### `[compiler]`

| Key | Meaning |
|---|---|
| `kind` | `"gcc"` (default: a GNU cross toolchain, `cross` + gcc, as, ld, objcopy) or `"clang"` (clang `--target=triple` with its integrated assembler, ld.lld, llvm-objcopy) |
| `cross` | the prefix of the GNU cross tools (default `arm-none-eabi-`) |
| `triple` | clang's `--target` (`kind = "clang"` needs it), e.g. `"riscv32-unknown-elf"` |
| `cflags` | the flags C for this target is compiled with (for clang, also given to the assembler: `-march`) |
| `ldflags` | flags for the linker (default `--no-warn-rwx-segments` for GNU ld, none for lld) |
| `tools` | `{cc, as, ld, objcopy, nm}`: a command or a list for a tool, overriding the defaults |
| `include` | include directories |
| `idioms` | the family of compiler idioms the lifter recognises |

### `[paths]`, `[host]`, `[plugins]`

- `[paths]`: `build` (default `build`) and `src` (default `src`).
- `[host]`: settings of the machine the tools run on (`jobs`, `free_cores`, `nice`). The environment
  variables of `dread.util.polite` override them.
- `[plugins]`: a free-form section owned by the tools that read it.

### `[models.NAME]`: devices for the system run

One table per device of `dread sysrun`. `kind` names a model class of `dread.models` or the
target's own (`"file.py:Class"`, relative to the target directory); the other keys are the class's
(its docstring). The kinds of `dread.models`: `uart` (serial port: data, status bits, receive queue),
`timer` (down-counter with load, control, status and an interrupt line), `gpio` (input and output
pins), `intc` (an A-profile controller with pending, enable and acknowledge registers, taken through
the IRQ vector), `nvic` (the Cortex-M NVIC's enable and pending registers and SysTick, taken through
the vector table). Example, a serial port:

```toml
[models.uart]
kind = "uart"
base = 0x80000000
data = 0x0
status = 0x4
tx_ready = 0x1
rx_ready = 0x2
```

### `[scenarios.NAME]`: system runs with a name

| Key | Meaning |
|---|---|
| `until` | `{model = "text"}`: end when that model's report `sent` contains the text |
| `limit` | the shared-clock units the run may take (`dread.sysrun`) |
| `input` | `[[clock, "model", "bytes"], ...]`: input handed to a model at that time |
| `models` | the models of the run (default: all of `[models]`; `[]` for none) |

## Plugins

A plugin is given as `"module:attribute"`, a module importable from the target directory, or as
`"file.py:attribute"`, a file in the target. A plugin holds what is specific to one target and needs
code rather than a value: unpacking a vendor's container, reading a startup's copy table, a device
model.
