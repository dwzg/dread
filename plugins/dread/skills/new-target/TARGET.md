# TARGET: <device name and firmware version>

What this firmware is and how DREAD reads it. Every value below has its evidence next to it (an
address, a datasheet section, a string in the image); write "unknown" or "not confirmed" rather
than a guess presented as fact.

## The image

| | Value | Evidence |
|---|---|---|
| File | `original/<file>` (never modified) | where it came from: update package, flash dump |
| Load address | `0x...` | |
| Size limit | `0x...` (flash size from the load address) | |
| Entry | reset handler at `0x...` | vector table / boot code |
| Copies at startup | code or data copied to RAM, zero-filled ranges | the startup loop at `0x...` |

## The core

| | Value | Evidence |
|---|---|---|
| Architecture | arm, isa a32 / t32, profile a / m | |
| CPU | Unicorn model (cortex-m3, arm926, ...) | |
| Thumb-2 | yes / no (ARMv4T-ARMv6 and Cortex-M0 have none) | |
| Compiler | name, version, options as far as known | prologue shapes, helper names, strings |

## Memory map

| Region | Base | Size | Kind | Evidence |
|---|---|---|---|---|
| flash | | | rom | |
| RAM | | | | |
| peripherals | | | io | |
| stack | top | size | | initial sp |
| scratch | | | (for generated inputs; unused by the firmware) | |

## Devices (for the system run)

| Device | Base | Model | What is known |
|---|---|---|---|
| | | uart / timer / gpio / intc / nvic / own | |

## Scenarios

What each scenario sends and waits for, and which code it reaches.

## Status

`dread asm --verify`: passes / fails at ...; functions named: N of M; rewritten in C: ...; lifted: ...

## Rules for this target

- `original/` is never modified.
- Nothing from this firmware goes into DREAD (its rule 1): no bytes, addresses or names in DREAD's
  repository, issues or commits.
- This repository stays private unless you own the firmware.
