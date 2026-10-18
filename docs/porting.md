# Porting DREAD to another architecture

Everything DREAD knows about an instruction set sits, or should sit, in one module of `dread/arch`.
`dread.arch.get()` returns the layer for a target's `[arch]` section; `dread.arch.arm.Arm` is the one
that exists and documents every method in its docstrings. This page lists what a new layer has to
provide, what is still ARM-specific outside the layer, and the order of the work.

## The layer

A class whose constructor takes the keys of `[arch]` (beyond `name`), with these groups of methods.

**Identity and registers**

| Method | What it gives |
|---|---|
| `describe()` | one line for `dread info` |
| `configure(cflags)` | facts the compiler flags give (ARM: whether Thumb-2 exists) |
| `isas()`, `isa` | the instruction sets of the architecture and the default one |
| `reg_name(name)`, `reg_id(name)`, `_name(capstone name)` | register names and emulator ids |
| `general_regs()`, `arg_regs(n)` | registers that hold values; the argument registers |
| `default_args`, `default_callee_saved`, `default_result` | the calling convention ([abi] overrides) |

**Decoding**

| Method | What it gives |
|---|---|
| `disassembler(isa)`, `decode(code, address, isa)` | capstone objects with details |
| `insn_length(code, isa)`, `min_insn(isa)` | lengths, for walking code |

**What an instruction does** (used by the program database, regflow, switches, noreturn, the
generator, the lifter, the reading aids)

| Method | What it gives |
|---|---|
| `flow(insn)` | (kind, target, falls): next, jump, call, icall, return, ijump, stop |
| `target(insn)`, `is_call(insn)` | direct branch targets; calls |
| `is_conditional`, `predicate`, `complement`, `condition` | predication, for analyses that follow conditional writes |
| `reg_effects(insn)` | registers read and written, the flags as "flags" |
| `sets_flags(insn)` | whether the flags change (do not trust a disassembler's flag alone) |
| `stack_kind(insn)` | push and pop forms |
| `literal(insn, isa)`, `pc_address(insn, isa)`, `pc_value(address, isa)` | pc-relative loads and addresses |
| `bound_check(insn)`, `switch_targets(insn, program, inside)` | switch shapes (dread.switches runs them) |
| `mark_call(program, insn)`, `link_pair(prev, insn, isa)` | optional: calls that are not plain calls (stubs, switch helpers, two-instruction calls) |

**Placing C** (the overlay)

| Method | What it gives |
|---|---|
| `branch(src, dst, isa, link)`, `branch_length(isa)` | the branch left at a moved function's entry (it must exist on the target core) |
| `start_address(address, isa)` | the address to call code of an isa at (ARM: bit 0 for Thumb) |
| `symbol_asm(name, address, isa)` | optional: an absolute symbol with its type (else PROVIDE) |

**Emulation** (Unicorn today)

| Method | What it gives |
|---|---|
| `uc_mode(isa)`, `new_uc(isa)`, `pc_reg()`, `status_reg()` | the engine |
| `initial_status(engine)` | the state a called function expects |
| `settle_it(engine)` | optional: repair after a hook (ARM: Thumb IT state) |
| `reset`, `interrupts_enabled`, `enter_interrupt`, `exception_return`, `resume_address`, `flags_value` | the system run's core: reset, interrupt entry and return |

## ARM knowledge outside the layer

To move behind the layer when the second architecture arrives (one at a time, as each tool is
needed on it):

- `dread.lift` and `dread.liftfuzz`: the lifter is a translator per instruction set; a new
  architecture gets its own module beside them (the acceptance in `dread lift --check` and the system
  run are generic).
- `dread.autocase`: the compare-guided stage reads capstone ARM operands (about twenty places).
- `dread.mkasm`: the listing's directives (`.syntax unified`, `.arm`, `.thumb`) and Thumb widths.
- `dread.elfimport` (ARM dispatch slots between functions) and `dread.record` (the frame of a call).
- `dread.fasthooks`: the C hooks repair the Thumb IT state when the layer asks for it.

## The emulator

Unicorn runs ARM, AArch64, MIPS, PowerPC, RISC-V, SPARC, x86, M68K and others: those need only the
layer. MSP430 and AVR are not among them; they need an emulator interface in front of what
`dread.emu` uses (map memory, read and write registers, run from an address until a sentinel with
an instruction budget, hooks per block, per memory access and per unmapped access) and an
interpreter behind it.

## A gizmo build

DREAD tests itself on gizmo: every test runs on every build. A new architecture adds a build of
`examples/gizmo/fw` (a start file and a linker script for the core, the Makefile's rules, `make
targets` to import it) and a target directory with its `dread.toml`; until a test can run on it,
the architecture is not supported.

## The order of work

1. The layer's decoding and `flow`, enough for `dread import-elf`, `dread functions` and
   `dread asm --verify` on the gizmo build.
2. The emulator: `dread test` with generated cases on functions rewritten in C.
3. The overlay's branch: functions that do not fit move and keep their entry.
4. regflow (the calling convention), switches, noreturn.
5. The system run: reset, interrupts, the device models.
6. The lifter and its fuzzer.
