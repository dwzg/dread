# Using DREAD

A target is any directory with a `dread.toml` ([target.md](target.md)). The tools find it from the
working directory, from `--target`, or from `$DREAD_TARGET`.

## The commands on gizmo

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest               # DREAD's tests on gizmo
bin/dread doctor                         # what DREAD needs on this machine, what it finds, what to install
bin/dread info --target examples/gizmo/m3
bin/dread recon --target examples/gizmo/m3      # gizmo's rewritten functions over the original
bin/dread test --target examples/gizmo/m3       # each one against the original, in the emulator
bin/dread trace --target examples/gizmo/m3 clamp "generated 3"    # one case in both images, to where they part
bin/dread mutate --target examples/gizmo/m3 --kill checksum   # would the cases notice a wrong one?
bin/dread integration --target examples/gizmo/m3 --callers    # the same with real callees, and through callers
bin/dread sysrun --target examples/gizmo/m3                   # the whole firmware from reset, both images
bin/dread sysrecord --target examples/gizmo/m3 --scenario config read_line   # cases from what the run saw
bin/dread lift --target examples/gizmo/m3 --check  # lift every function, keep those whose cases agree
bin/dread uncovered --target examples/gizmo/m3     # why the cases it dropped leave code unrun: each region's way in, what decides it
bin/dread readable --target examples/gizmo/m3 --check  # build the lifted functions from readable C where the cases agree
bin/dread readable --target examples/gizmo/m3 --plain print_status   # to read: no instruction comments, addresses in a margin
bin/dread readable --target examples/gizmo/m3 --out rc   # every function's readable C, the structures once in rc/readable_types.h
bin/dread liftfuzz --isa t32                        # the lifter on random instructions, against the emulator
bin/dread test --target examples/gizmo/rv32         # the same tools on the RISC-V build
bin/dread bench all freertos                        # a real project built many ways, measured (in ~/dread-bench)
bin/dread cclab run --adapter gcc-arm               # how a compiler compiles: probes run in the emulator
bin/dread cclab compare ti-arm gcc-arm              # where two compilers differ, and the options that close it
bin/dread whatcc --target ~/dread-bench/freertos/mps2-gcc7-Os   # whose C library it links, and names for its functions
bin/dread flat --target examples/gizmo/m3 print_status        # one function to read: names, literals, arguments
bin/dread callsites --target examples/gizmo/m3 --by 0 gpio_set # who calls it, with which constants
bin/dread relations --target examples/gizmo/m3 print_status   # what it calls, reads, writes and prints
bin/dread groups --target examples/gizmo/m3                   # its functions in groups: the subsystems a reader would see
bin/dread callpair --target OTHER --other examples/gizmo/a32 --seed FUN_08000738=main   # names by call order
bin/dread rename --target examples/gizmo/m3 beep_on=buzzer_on  # a new name everywhere, or nowhere
bin/dread pilot --target examples/gizmo/m3 --limit 4          # a language model writes C, the tools decide
bin/dread gui --target examples/gizmo/m3 --open               # all of it in the browser
```

## Setting up a target

Everything below is a command first, usable over SSH and in scripts; `dread gui`'s Setup page is a form
over the same commands.

```sh
bin/dread doctor                                   # Python, unicorn, capstone, compilers, podman, Ghidra, providers
bin/dread new ~/targets/fw build/fw.elf            # a new target from an ELF, with or without symbols
bin/dread new ~/targets/raw fw.bin --arch cortex-m3 --base 0x08000000    # or from a raw image
bin/dread regflow --target ~/targets/fw            # its first analyses, as dread new prints them
bin/dread lift --target ~/targets/fw --check
bin/dread readable --target ~/targets/fw --check
bin/dread llm set claude --kind anthropic --model claude-sonnet-5-5 --price-in 3 --price-out 15 --default
bin/dread llm key claude                           # reads the key from standard input into a key file (mode 600)
bin/dread llm providers                            # each provider and whether its key is there
bin/dread llm ask --provider claude hello          # one question, to try it
```

- **`dread doctor`** looks and changes nothing: the essentials (Python 3.11 or newer, unicorn 2.1.4,
  capstone 5, and with a target its compiler, which has to take the target's cflags) decide its exit
  status; the rest (z3, pyelftools, Playwright, the cross compilers of each architecture with the
  `-march` a RISC-V GCC takes, clang's targets, podman, git, Ghidra and a java, each provider's key and
  whether a local model server answers) says what needs it. For everything missing it prints the
  command for this system's package manager (apt, pacman, dnf or brew; all four with `--all`) or pip,
  and runs none of them. `--json` for scripts.
- **`dread new DIR IMAGE`** makes the target directory: `dread.toml`, the image in `original/` and, from
  an ELF with symbols, its tables (`dread import-elf`). From an ELF it takes the architecture (ARM's build
  attributes, RISC-V's extensions), the image and its entry, the startup copies, a memory map (the image,
  the RAM its data uses up to the stack's top, the architecture's peripheral ranges where it has a fixed
  map, a scratch region and a sentinel at unused addresses, room for C behind the image) and the compiler
  with the original's flags where the debug information records them; from a raw image it needs
  `--arch` (`dread new --help` lists them) and `--base`, and reads an M-profile vector table for the
  entry and the RAM. Every guess carries a comment in `dread.toml` saying what it rests on
  ([target.md](target.md), "Making a target"). It prints what to run next: the first analyses (the functions,
  by `dread ghidra` where the image has none, then `dread regflow`, `dread lift --check`, `dread
  readable --check`).
- **`dread llm set`** adds a provider to `llm.toml` or changes its fields (kind, model, url, prices,
  cache minimum, batches, default, key_env, key_file), editing its lines: comments and everything else
  stay. **`dread llm key`** writes a provider's key into a key file readable by you alone (mode 600,
  outside any git work tree) and points the provider's key_file at it; a key is read from that file when
  a request goes out and never printed, logged or shown back ([formats.md](formats.md), `llm.toml`).

The same in the browser: [gui.md](gui.md).
