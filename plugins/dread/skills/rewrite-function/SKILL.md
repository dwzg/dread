---
name: rewrite-function
description: Rewrite one function of a firmware in C with DREAD and prove it against the original: read it, write the C, build it over the original, compare in the emulator, break it on purpose, check its callers. Use for every function you reconstruct in a DREAD target, when `dread test` reports FAIL, UNCOVERED or UNTESTED, or when `dread mutate` leaves survivors.
---

# Rewrite one function

The tools decide whether the C is right; your part is reading the code, naming things and writing C
a person would write. One function (or a tightly coupled group) at a time, committed when it passes.

## 1. Read

- `dread flat NAME`: the function with targets, literals and texts by name, the constant arguments of
  each call, what dread.regflow says it reads at entry and what its callers read after it.
- `dread callsites NAME`: who calls it with which constants (`--by 0` groups by the first argument).
- `dread regflow NAME`: registers its callers rely on beyond the result (a second result in r1, a
  scratch register kept across the call). The C has to keep them, or the function cannot be
  written in C as it is.
- Name what you understand in `config/symbols.txt` and describe it in `config/annotations.txt`;
  keep `FUN_...` until the purpose is certain.

## 2. Write

- The C goes into the target's `src/` (any `.c` file), same name as in the function table.
- Reproduce the behaviour of the machine code exactly, including its faults: note an oddity in a
  comment, do not fix it.
- No new global variables (the overlay rejects them); globals of the original are reached by
  address (`VAR(type, address)` in `dread.h`).
- Do not assign to a parameter that arrives on the stack (the fifth and later): the compiler then
  writes the caller's slot.

## 3. Build and compare

- `dread recon` builds the C over the original (`build/recon/IMAGE/report.tsv` says whether it went in
  place, into freed space or behind the image).
- Give it cases: `auto("NAME")` in a case file under `tests/cases/` generates them from the original.
  Written cases (`case(...)`, `logging_stubs(...)`) for what the generator cannot reach; `dread
  record` for parsers fed by callees (Python models of the callees), `dread sysrecord` for calls a
  system-run scenario makes.
- `dread test NAME`: every case must agree, and the cases must run every instruction of the original
  (UNCOVERED otherwise; `unreachable(...)` with a reason for code no input can reach).

## 4. Break it

- `dread mutate NAME --kill`: changes the C in small ways and searches inputs that notice each change.
  A survivor that cannot change behaviour (an equivalent mutant) is argued in `tests/argued.tsv`;
  any other survivor means the cases are too weak: add one.

## 5. Check the callers

- `dread integration --callers NAME`: generated inputs with real callees, through the original
  callers. A DIFF there is a difference the function's own cases missed (a register a caller keeps,
  an access count).

## 6. Commit

When test, mutate and integration pass: commit the C, the cases, the names and annotations, with
what was learned and what is still not confirmed.

## When it fails

- FAIL: read the first difference (return value, a memory byte, the device log); compare with
  `dread flat`; for a lifted version of the same function, `dread lift NAME` shows the C the lifter
  makes, statement by statement next to each instruction.
- A function that never returns gets no case that ends: `dread lift --check` decides it with the
  system run, and so does a hand-written one through `dread sysrun` (see the system-run skill).
