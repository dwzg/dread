# DREAD

[![tests](https://github.com/dwzg/dread/actions/workflows/test.yml/badge.svg)](https://github.com/dwzg/dread/actions/workflows/test.yml)

**Dennis' Reverse Engineering And Decompilation**: *disassemble, rewrite, emulate and diff.*

A toolkit for rewriting embedded firmware in C, one function at a time, when the original compiler
is not available. Matching-decompilation tools assume you can recompile the original byte for
byte. DREAD instead proves the rewritten function behaves like the original:

1. **Disassemble.** Analysis tables (from Ghidra, or from an ELF when there are symbols) become a
   listing that reassembles to the original image, byte for byte. That rebuild is the baseline
   every change is measured against.
2. **Rewrite.** C functions are linked over the original image at their own addresses: in place
   when they fit, otherwise into space freed by other rewritten functions. Everything not rewritten
   stays the original machine code, so every intermediate state is a complete image. The C comes
   from three sources: written by hand, generated for families of identical functions, or lifted
   mechanically, instruction by instruction, as a first step.
3. **Emulate.** Every rewritten function runs in the original image and in the rebuilt one, in
   Unicorn, with the same inputs. Callees are stubbed and logged, and peripheral accesses are
   recorded in order. Inputs come from hand-written cases and from a generator that is guided by
   coverage and by the values each branch compares. The whole image can also run from reset with
   device models.
4. **Diff.** Return values, memory written, calls made and peripheral accesses must all agree, and
   so must every register a caller reads after the call: a compiler that saw the callee (GCC's
   `-fipa-ra`) may keep a value in a scratch register across it, and a rewrite has to leave that
   register alone. The cases must run every instruction of the original function. A mutation check
   confirms that the cases would notice a wrong function.

What this proves is agreement on the inputs tried, in an emulator. It is not a proof that an image
is safe to run on hardware.

## Status

Early: the tools are being moved over and generalised phase by phase, and the table below says
what works. The method and the first versions of the tools come from the reconstruction of
a 2004 digital camera's firmware (ARM7, ARM state). Here they are being made independent of that
firmware: everything a tool needs to know about a target comes from the target's `dread.toml`.
DREAD tests itself on **gizmo** (`examples/gizmo`), a small firmware written for the purpose and
built three times: for an ARM7-style core in ARM state and for a Cortex-M3 in Thumb-2 with GCC, and
for an RV32IMC core with clang (that build is new; the tools are being ported to it step by step).

| Part | State |
|---|---|
| Target description, function tables, ARM and Thumb layer, ELF import | done |
| Listing generator and byte-identical rebuild, Ghidra pipeline | done |
| Emulator, differential test runner, overlay linker | done |
| Case generator (coverage- and compare-guided, pointer learning, a dictionary for parsers) | done |
| Mutation check with survivor search and argued survivors | done |
| Integration run: generated inputs with real callees, also through original callers | done |
| Register flow across calls: what callers read after a call, compared at the return | done |
| Recorded cases from Python models of callees (`dread record`) | done |
| System run from reset, original against rebuilt, on a shared clock; models for serial port, timer, pins, interrupt controllers (A profile, Cortex-M NVIC with SysTick) | done |
| Lifter for ARM and Thumb-2: instruction by instruction to C, built only when the generated cases agree and run every instruction of the original; its instruction semantics fuzzed against the emulator (`dread liftfuzz`) | done |
| Reading aids: flat listing, call sites with constant arguments, the same code in two images, names by call order; renaming everywhere at once (`dread rename`) | done |
| Browser view (`dread gui`): overview and map of the image, listings with coverage, the C, verdicts, search, rename and notes | done |
| Claude Code plugin (skills, writer and reviewer agents, a target template) | done |
| Method documentation and a porting guide | done |
| Compiler profiles for the lifter and the template families (gcc, older ARM compilers) | next |
| `dread pilot`: the reconstruction loop run by code, an LLM (local or cloud) only for the judgement: writing C, arguing survivors, proposing inputs and names; every answer checked by the tools | first version |
| RISC-V (RV32IMC): gizmo-rv32 built with clang; decoding, listing with byte-identical rebuild, reading aids, the overlay, differential tests with generated cases, integration and recorded cases, regflow, switches, Ghidra | under way (next: the system run, then the lifter) |

## Layout

| Path | What it is |
|---|---|
| `dread/` | the Python package; `bin/dread` runs it from a checkout |
| `docs/` | the method (`method.md`), porting to another architecture (`porting.md`), the target description, the file formats |
| `examples/gizmo/` | the test firmware: sources, builds and three target directories (a32, m3, rv32) |
| `tests/` | DREAD's own tests (`.venv/bin/pytest`) |

## Using it

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest                         # DREAD's tests on gizmo
bin/dread info --target examples/gizmo/m3
bin/dread recon --target examples/gizmo/m3      # gizmo's rewritten functions over the original
bin/dread test --target examples/gizmo/m3       # each one against the original, in the emulator
bin/dread mutate --target examples/gizmo/m3 --kill checksum   # would the cases notice a wrong one?
bin/dread integration --target examples/gizmo/m3 --callers    # the same with real callees, and through callers
bin/dread sysrun --target examples/gizmo/m3                   # the whole firmware from reset, both images
bin/dread sysrecord --target examples/gizmo/m3 --scenario config read_line   # cases from what the run saw
bin/dread lift --target examples/gizmo/m3 --check  # lift every function, keep those whose cases agree
bin/dread liftfuzz --isa t32                        # the lifter on random instructions, against the emulator
bin/dread flat --target examples/gizmo/m3 print_status        # one function to read: names, literals, arguments
bin/dread callsites --target examples/gizmo/m3 --by 0 gpio_set # who calls it, with which constants
bin/dread callpair --target OTHER --other examples/gizmo/a32 --seed FUN_08000738=main   # names by call order
bin/dread rename --target examples/gizmo/m3 beep_on=buzzer_on  # a new name everywhere, or nowhere
bin/dread pilot --target examples/gizmo/m3 --limit 4          # a language model writes C, the tools decide
bin/dread gui --target examples/gizmo/m3 --open               # all of it in the browser
```

### In the browser

`dread gui` serves a page on this machine (127.0.0.1:8350) that shows what the tools know and
decided: where each function stands (written by hand, by the pilot, lifted, refused, original), a
map of the image, the listing with the instructions the cases ran or missed, the C beside it,
verdicts and cases, search by name, address and text. It changes nothing but names (through `dread
rename`) and notes (annotations.txt); everything else is the tools' work. Keys: `/` search, `j` `k`
lines, `Enter` follow, `n` rename, `;` note, `?` all of them.

![The overview of gizmo in dread gui](docs/images/gui-overview.webp)
![A function with its listing, coverage and C](docs/images/gui-function.webp)

### With Claude Code

The repository is also a plugin marketplace. The `dread` plugin has skills that drive the tools
(setting up a target, reading a firmware, rewriting a function, the system run), a writer and a
reviewer agent, and a template for a target's README:

```
/plugin install dread --marketplace dwzg/dread
```

The skills only point at the tools: whatever a program can decide, the tools decide.

A target is any directory with a `dread.toml` (`docs/target.md`). The tools find it from the
working directory, from `--target`, or from `$DREAD_TARGET`.

## License

Apache License 2.0, see `LICENSE`.
