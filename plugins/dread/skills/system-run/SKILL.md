---
name: system-run
description: Run a whole firmware from reset in DREAD's emulator with device models, the original against the image with your C, and use the runs to test what function-level cases cannot (main loops, interrupt handlers, start-up) and to record real inputs. Use when setting up device models or scenarios, when `dread sysrun` reports a difference, or when a function needs inputs only the running firmware gives it.
---

# The system run

`dread sysrun` starts both images from reset with the target's device models (`[models.*]` in
dread.toml: uart, timer, gpio, interrupt controllers, or a class of your own) and compares what they
do to the devices: every peripheral access in order, what the models note (bytes sent) and how the
run ends. Time is a shared clock over the code both images have in common, plus a fixed amount for
every entry into a rewritten function, so a model acts at the same place in both however many
instructions the C takes. The C must make the original's calls: C that inlines one rewritten
function into another (several in one file, compiled without `-fno-inline`) enters fewer functions,
and the whole image differs.

## Scenarios

A scenario (`[scenarios.NAME]`) gives input at points of the clock and an end condition:

    [scenarios.config]
    input = [[40_000, "uart", "cvolume=3\r\n"], [200_000, "uart", "s"]]
    until = {uart = "mode run volume 3"}
    limit = 5_000_000

Write one per behaviour you want covered: boot, each command, each error path a device can cause.

## Steps

1. `dread sysrun --original-only --scenario NAME`: the original alone first. It must reach its end
   condition; "waits" or "polls" at a pc means a model does not answer what the firmware expects.
2. `dread sysrun --scenario NAME` after `dread recon`: both images; a difference shows the device log
   around the first entry that differs.
3. `dread sysrecord --scenario NAME FUNCTION...`: record the calls of functions during the run as
   written cases (their arguments, what they read, how the devices answered), kept when they reach
   code no case reached before.
4. `dread lift --check` uses every scenario for the functions no case can call or end (main loops,
   reset and interrupt handlers): accepted when all scenarios agree and run all of their code.
5. `dread gui`, key `s`: each run is kept (BUILD/sysrun/IMAGE: `SCENARIO.json` from dread sysrun,
   `SCENARIO.lift-check.json` from the lift check's whole image) with both device logs, the function
   whose instruction made each entry, and who entered whom. Read a difference there entry by entry
   (`d` goes to the first); the relations of a callback or a task show who called it through a
   pointer or started it, which no listing shows.

## What goes wrong

- A difference in the device log is traced to its instruction, never removed by changing a model:
  the models are the same for both images.
- A scenario that never reaches some code leaves the functions there unproven: add input that gets
  there rather than lowering the bar.
- Interrupts are taken at places both images share: shared code, a rewritten function's entry, the
  instruction behind an unmask or a barrier (which the C keeps itself), or the place a wait inside
  one rewritten function comes round. An interrupt the C would let in elsewhere waits for the next
  such place; when a difference appears only with interrupts, look at what the handler reads that
  the interrupted C writes before that place.
