---
name: system-run
description: Run a whole firmware from reset in DREAD's emulator with device models, the original against the image with your C, and use the runs to test what function-level cases cannot (main loops, interrupt handlers, start-up) and to record real inputs. Use when setting up device models or scenarios, when `dread sysrun` reports a difference, or when a function needs inputs only the running firmware gives it.
---

# The system run

`dread sysrun` starts both images from reset with the target's device models (`[models.*]` in
dread.toml: uart, timer, gpio, interrupt controllers, or a class of your own) and compares what they
do to the devices: every peripheral access in order, what the models note (bytes sent) and how the
run ends. Time is a shared clock over the code both images have in common, so a model acts at the
same place in both however many instructions the C takes.

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

## What goes wrong

- A difference in the device log is traced to its instruction, never removed by changing a model:
  the models are the same for both images.
- A scenario that never reaches some code leaves the functions there unproven: add input that gets
  there rather than lowering the bar.
- Interrupts are taken behind code both images share, or at once after a wait in code that is not
  shared (a rewritten idle loop); keep handlers and the code they interrupt on the same side when a
  difference appears only with interrupts.
