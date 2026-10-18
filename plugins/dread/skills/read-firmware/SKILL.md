---
name: read-firmware
description: Find your way around a firmware with DREAD's reading aids and carry names over from other images: listings by name, call sites with constant arguments, call chains, the same code in another image, names by call order. Use when exploring an unknown firmware, naming functions or subsystems, looking for the code behind a message or a device, or when a second image (another version, a boot loader) has names you could reuse.
---

# Read a firmware

Reading comes before writing C, and names come before both: a function named after what it does is
half reconstructed. The tools here only read, except two: a name goes in with `dread rename` (the
symbols and every file that names the function), a description into `config/annotations.txt` (by
hand or as a note in `dread gui`).

## Listings and calls

- `dread flat NAME` (or an address inside it): one function, with call and branch targets, literals
  and texts by name, the constant arguments of each call, switch targets, its callers.
- `dread callsites PATTERN`: every call of matching functions (shell patterns: `uart_*`) with the
  constant arguments. `--by N` groups by argument N: a message printer called with string literals, a
  dispatcher with command codes, a register writer with addresses.
- `dread callsites --reaches TARGET PATTERN`: the shortest call chains from matching functions to
  TARGET (how does the main loop get to the flash writer?).
- `dread functions`: every function with its extent; `dread noreturn`: those that never return.
- `dread gui`: the same reading in the browser (overview, map, listing with coverage, the C, search);
  rename (`n`) and notes (`;`) from there go through `dread rename` and annotations.txt.
- `dread rename OLD=NEW`: a name, once known, everywhere it is written (tables, cases, C), or
  nowhere: it is refused outside the target's directory and undone when the tree contradicts it.

## Names from other images

- `dread samecode --other DIR`: functions that are the same code in another target (another version
  of the firmware, a boot loader built from the same sources, a library you have built yourself),
  independent of where they lie. `--symbols` prints the names this side lacks as symbols.txt lines.
- `dread callpair --other DIR --seed HERE=THERE`: when the code was compiled again (other compiler,
  options or instruction set) the bytes differ but the calls mostly do not. From pairs you know
  (same names, samecode pairs, seeds) it pairs the callees by call order. Read each proposal before
  taking it: sizes that differ a lot are suspect.

## Habits that pay

- Start from what the firmware says: strings (texts in `dread flat` and `dread callsites --by 0`),
  peripheral addresses (`dread callsites` on the functions that touch them), the vector table.
- Name a subsystem's entry points first, then walk their callees.
- Write down what is not certain as "not confirmed"; a wrong name costs more than a missing one.
