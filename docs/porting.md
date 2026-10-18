# Porting DREAD to another architecture

Everything DREAD knows about an instruction set sits, or should sit, in one module of `dread/arch`.
`dread.arch.get()` returns the layer for a target's `[arch]` section: `dread.arch.arm.Arm` documents
every method in its docstrings, `dread.arch.riscv.RiscV` is the second layer (RV32IMC, under way).
This page lists what a new layer has to provide, what is still ARM-specific outside the layer, and
the order of the work.

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
| `disassembler(isa)`, `decode(code, address, isa)` | instruction objects with `address`, `size`, `bytes`, `mnemonic`, `op_str` (capstone's; RISC-V wraps capstone and adds its own decoded fields, because capstone 5 reports no registers for RISC-V) |
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
| `mark_call(program, insn)`, `link_pair(prev, insn, isa)` | optional: calls that are not plain calls (stubs, switch helpers, two-instruction calls: RISC-V's auipc + jalr, and its auipc/lui + addi addresses) |
| `link_reg`, `veneer_reg` | the register a call leaves the return address in, and the one veneers go through |

**Placing C** (the overlay)

| Method | What it gives |
|---|---|
| `branch(src, dst, isa, link)`, `branch_length(isa, src, span)`, `entry_branch(src, dst, isa, room)` | the branch left at a moved function's entry (it must exist on the target core); the room it needs from the entry and the span its destination may lie in |
| `isa_of_symbol(value)` | the instruction set of a compiled function from its symbol (ARM: bit 0 for Thumb) |
| `start_address(address, isa)` | the address to call code of an isa at (ARM: bit 0 for Thumb) |
| `symbol_asm(name, address, isa)` | optional: an absolute symbol with its type (else PROVIDE) |

**The listing** (`dread asm`)

| Method | What it gives |
|---|---|
| `asm()` | the assembler's dialect: comment character, data directives, prelude, instruction-set switches, raw bytes, function labels, and each instruction's operands with labels for places (`ArmAsm`: GNU as; `RiscVAsm`: llvm-mc with `.option exact`) |

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
- `dread.elfimport`: ARM dispatch slots and padding between functions (other architectures skip
  them).

Done for RISC-V: the listing's syntax moved behind `arch.asm()`, the ELF import reads `$x` mapping
symbols as the layer's default instruction set, the reading aids take the argument registers from
`arch.arg_regs()` and the link register from `arch.link_reg`, the compiler settings know clang
(`[compiler] kind`), and the constant tracker stops at join points and retries behind any branch
that falls through (it went by ARM's predicates before). The case generator's compare-guided stage
asks the layer (`immediates`, `switch_hints`, `compare_chars`, `branch_deciders`), `dread record`
too (`prologue_frame`); the overlay takes the C's instruction set (`isa_of_symbol`) and the entry
branch (`branch_length` with the image's span, `entry_branch`) from it; `dread.fasthooks` clears an
IT state only where the layer has one.
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
