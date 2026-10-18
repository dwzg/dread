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
built twice, once for an ARM7-style core in ARM state and once for a Cortex-M3 in Thumb-2.

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
| Reading aids, Claude Code plugin, method documentation | planned |
| Compiler profiles for the lifter and the template families (gcc, older ARM compilers) | next |
| `dread pilot`: the reconstruction loop run unattended, an LLM (local or cloud) only for the thinking | after the ARM phases |
| RISC-V: a third gizmo build and an architecture backend | after the pilot |

## Layout

| Path | What it is |
|---|---|
| `dread/` | the Python package; `bin/dread` runs it from a checkout |
| `docs/` | the method, the target description, the file formats |
| `examples/gizmo/` | the test firmware: sources, builds and two target directories |
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
```

A target is any directory with a `dread.toml` (`docs/target.md`). The tools find it from the
working directory, from `--target`, or from `$DREAD_TARGET`.

## License

Apache License 2.0, see `LICENSE`.
