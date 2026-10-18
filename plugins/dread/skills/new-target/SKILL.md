---
name: new-target
description: Set up a firmware image as a DREAD target, from an ELF or a raw binary, until its listing rebuilds byte for byte and the emulator runs its functions. Use when starting work on a new firmware with DREAD, when a target's dread.toml is wrong or incomplete, or when `dread asm --verify` or `dread info` fails.
---

# Set up a new target

A target is a directory with a `dread.toml` and the tables DREAD makes from the image. Everything a
tool knows about the firmware comes from there; nothing about it goes into DREAD itself. Keep the
target in a repository of its own: it holds the firmware, which is usually not yours to publish.

[TARGET.md](TARGET.md) is the template: copy it into the new target's repository as its README and
fill it in as you go. It asks for what `dread.toml` needs and for the evidence behind each value.

## Steps

1. **Write `dread.toml`** (the reference is DREAD's `docs/target.md`): the image file, its load
   address and limit, the entry, `[arch]` (isa, profile, cpu; `thumb2 = false` for ARMv4T to ARMv6
   and Cortex-M0), the memory map with the peripheral ranges, a stack and a scratch area, and
   `[compiler]` with flags as close to the original's compiler as you can tell. Check it with
   `dread info`.
2. **Make the tables.** With an ELF that has symbols: `dread import-elf FILE --payload IMAGE.bin
   --write-startup`. Without one: `dread ghidra` (headless analysis, writes the function and code
   tables), then name what you know in `config/symbols.txt`.
3. **Prove the listing.** `dread asm --verify` must rebuild the image byte for byte. A failure names
   the first differing address; `--verify` also moves instructions GNU as encodes differently into
   `config/asm_raw_words.txt`. Do not go on until this passes: every later comparison stands on it.
4. **Run something.** `dread functions` lists what the tables give; `dread flat NAME` shows one
   function. `dread recon` with an empty `src/` must link and `dread test` must pass (there is
   nothing to compare yet, but the emulator, the memory map and the overlay are exercised).
5. **For the system run** (whole firmware from reset): describe the devices in `[models.*]` and add
   `[scenarios.*]`; then use the system-run skill.

## What goes wrong

- A memory region missing from `[memory]` shows as an emulation fault on the first access; the
  fault names the address. Add the region, do not stub the access away.
- Code that the startup copies to RAM needs its `startup` entries (import-elf writes them from the
  ELF); without them functions in RAM look like data.
- On a Cortex-M the sentinel return address must lie in an executable region (0xe0000000 and up is
  execute-never).
- Wrong compiler flags show later, as functions that do not fit their slot or differ in register
  use. Record what you know about the original compiler in TARGET.md.
