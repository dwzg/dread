# Porting DREAD to another architecture

Everything DREAD knows about an instruction set sits, or should sit, in one module of `dread/arch`.
`dread.arch.get()` returns the layer for a target's `[arch]` section: `dread.arch.arm.Arm` documents
every method in its docstrings, `dread.arch.riscv.RiscV` is the second layer (RV32IMC; every tool runs
on it, and its port is the worked example of the order below), `dread.arch.msp430.Msp430` the third
(under way: neither capstone nor Unicorn knows the MSP430, so its layer decodes the instructions
itself, into objects with capstone's attributes, and runs them on an engine of DREAD's own,
`dread.engines.msp430`), `dread.arch.mips.Mips` the fourth (paused after step 1: MIPS32r2 little
endian, the first with delay slots; it decodes the bits itself too; the branch `mips` goes on with
Unicorn's virtual TLB, so that kseg0 and kseg1 addresses are themselves).
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
| `max_insn`, `isa_bit` | optional: the bytes an instruction may take (the program database reads that many to tell a length; 2 without it), and the bit of a code address that marks an instruction set (ARM's Thumb bit: 1; 0 where none does: RISC-V, MSP430, MIPS, x86; 1 without it). `dread.arch.code_address` takes that bit off where the tools key code, so a layer whose code starts at odd bytes (x86) must say 0 |

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
| `value_step(insn, isa, known, program)` | optional, for dread.relations flow_references (`dread groups`' evidence from the listing) and dread.regflow made_pointers (the function entries a function's code makes from another value it knows: a lui and its addi apart): given the constants registers hold in front of an instruction ({register: (value, indexed)}), the registers whose values follow from it, its loads and stores at a known base plus an offset, and the registers it stores (`dread.arch.sum_value` adds two values); without it flow_references gives the addresses `materialized` finds (ARM and RISC-V have one; MIPS's delay slots and MSP430's addressing modes are not written yet) |
| `bound_check(insn)`, `switch_targets(insn, program, inside)` | switch shapes (dread.switches runs them) |
| `mark_call(program, insn)`, `link_pair(prev, insn, isa)` | optional: calls that are not plain calls (stubs, switch helpers, two-instruction calls: RISC-V's auipc + jalr, and its auipc/lui + addi addresses) |
| `link_reg`, `veneer_reg` | the register a call leaves the return address in (None where the call pushes it: MSP430), and the one veneers go through |
| `delay_slot(insn)`, `likely(insn)` | optional: whether the instruction behind a branch runs before it goes (MIPS), and whether only when it is taken (a branch-likely) |
| `discard_sections` | optional: allocated sections the toolchain adds to every object that no image wants (MIPS: .MIPS.abiflags, .reginfo, a .got); dread asm and dread overlay discard them |
| `word`, `reg_bits` | the size of a stack argument (bytes) and of a register (bits) |
| `store_slots(insn)`, `slot_access(insn)`, `load_address(insn)` | optional, for dread.regflow: [(register, base, displacement)] of what a store writes, ("store" or "load", register, base, displacement) of a single-register access, and (base, displacement) of a load; whether a base addresses the frame (sp, a frame pointer, any copy) says regflow's Frames from the lifter's states (a variadic save area at the entry, and saves around a call, are no reads) |
| `register_only(insn)`, `bit_demand(insn, out)`, `store_demand(insn)` | optional, for dread.regflow's bits: whether an instruction only computes registers (no memory, no branch, no system instruction: one whose results nobody uses reads nothing); {register: bits} of the operands a computing instruction's used result bits (`out`: {register: bits}) depend on, None where the layer has no rule for it (every bit then); {register: bits} of what a byte's or halfword's store takes of the register it stores. A wrong rule drops a read that matters: tests/test_regflow_bits.py checks every rule against the emulator (`dread.arch` low_bits, unshift and extend_bits help writing them; ARM and RISC-V have them) |
| `merge_demand(insn)`, `flag_demand(insn)`, `refine_saved_writes` | optional, for dread.regflow's bits too, where registers have parts (x86): {register: (kept, operand)} of an instruction outside register_only that writes part of a register and keeps the rest (mov dl, byte ptr [eax]: the kept bits count as read only where they are used behind it); {register: bits} of what the flags a register_only instruction sets depend on (cmp dl, 0x20: the low byte of edx), without which every bit counts where a flag is used; and whether regflow refines an instruction that writes a register it does not follow (callee-saved; taken as all used: x86 moves bytes through ebx and esi). tests/test_regflow_bits.py checks them against the emulator |

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

**Emulation** (Unicorn, or an engine of DREAD's own: below)

| Method | What it gives |
|---|---|
| `uc_mode(isa)`, `new_uc(isa)`, `pc_reg()`, `status_reg()` | the engine |
| `initial_status(engine)` | the state a called function expects |
| `push_return(engine, address, write)`, `pop_return(engine, read)` | only without a link register: a call's return address put where the call instruction puts it (MSP430: pushed), and a stub's return |
| `entry_stack_args` | where a call pushes its return address: the bytes between sp and the first stack argument at a function's entry (x86: 4). A stub gets its first four arguments from there where the convention has fewer argument registers (cdecl: none), the generator's and dread record's stubs log their stack words from there (0 without it; MSP430's two bytes are not said yet) |
| `settle_it(engine)` | optional: repair after a hook (ARM: Thumb IT state) |
| `syscall_numbers` | the engine's exception numbers of a system call (ARM: svc 2; RISC-V: ecall 8, 9, 11). dread.emu runs one as a no-op logged as `("svc",)` (`log_svc()` adds the argument registers); any other exception number is a fault. Unicorn never enters a handler through the vector table, with or without a hook, so a system run that needs the handler models the entry itself |
| `reset`, `interrupts_enabled`, `enter_interrupt`, `exception_return`, `resume_address`, `flags_value` | the system run's core: reset, interrupt entry and return |
| `cpu_masks(engine)`, `system_exception(intno)`, `syscall_exception`, `enter_syscall(engine, resume, read_word, write_word)` | optional, for a controller that decides itself and a core that raises exceptions itself: the masks the controller weighs (M profile: PRIMASK, FAULTMASK, BASEPRI; RISC-V: mstatus, mie), what an exception Unicorn raised means in a system run ("syscall", "return" or a fault), and a system call either as an exception a controller takes (M profile: SVCall, 11) or as a trap the layer takes alone (RISC-V: ecall to mtvec, mepc on the ecall). A controller whose numbers are the core's causes says so with `core_cause(number)`, passed to `enter_interrupt` as `cause` |

## ARM knowledge outside the layer

To move behind the layer when the second architecture arrives (one at a time, as each tool is
needed on it):

- `dread.lift` and `dread.liftfuzz`: the lifter is a translator per instruction set. A new
  architecture subclasses `dread.lift.Lifter` in a module of its own (`dread.lift_riscv`): the
  control flow, the stack pointer's depth (states, with its step, new_depth, made_value and
  stored_slot), the registers read before written (entry_states, with saving_store for saves by
  single stores), the words at the frame's top the C leaves out (saves_out, with frame_uses and
  restore_dead; the layer's lifter says what each instruction does to memory in `frame_effects` and
  which registers its C returns in `results`, and its translation skips the stores and loads of
  those words), the acceptance, the system run and the switches are shared; the translation of
  each instruction, the rendering and the kinds of handlers are its own. `make_lifter` picks it.
  `dread.liftfuzz` takes a generator per ISA (`RiscVGenerator` beside the ARM `Generator`).
  `dread.lift_msp430` needed one more hook, `entry_values` (what lies in the frame at the entry: the
  MSP430's return address), and uses `ret_jump` (a branch that returns: GCC's shared epilogues) and
  `src_calls` (the functions the hand-written C calls, which `dread.build` sets before it lifts: the
  MSP430 translator returns all four result registers from them, since such a caller may read more
  of a result than the original's callers, from whom the width is taken). Where the compiler has no
  instruction for what the C says (the MSP430 has no barrel shifter, and GCC shifts in libgcc), the
  translation writes the original's instruction in inline assembly: a generated case may stub such
  a routine (one a run hung in, deeper in the tree), and the stub would answer the C's own calls;
  `dread.lift.foreign_calls` drops, in a lift check, any function whose C calls what its original
  does not.
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
layer. DREAD drives every engine through a part of Unicorn's Python interface, with the semantics
its tools rely on (a read hook supplies the value by writing memory, a code hook redirects by
writing pc, write hooks run before the store, a stop from a code or block hook leaves the
instruction unrun): the engine contract in the docstring of `dread.engines`, checked on Unicorn and
on DREAD's engines alike by `tests/test_engines.py`. An architecture Unicorn lacks gets an engine
there: a subclass of `dread.engines.Engine`, which keeps the memory, the hooks and the run loop,
with the registers, the decoding and `step()` of its own (`dread.engines.msp430`, a Python
interpreter of the 16-bit MSP430: about a microsecond an instruction). Check it against an
independent simulator with random instruction blocks, as `dread.engines.msp430_check` does against
GDB's (it found seven places where that simulator departs from TI's guide, and none in the
engine); what the reference gets wrong is stepped around in the check and tested against the
manual in `tests/test_engines.py`.

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
