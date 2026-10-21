"""dread sysrecord on gizmo: the config scenario types "c" and a configuration line; the recorded
calls of read_line (whose input arrives through uart_getc) and handle_key cover what generated
cases do not, replay in the function-level harness with the result the system run saw, carry the
UART's reads as poll stages, and move the caller's line buffer into scratch memory."""
import os

import pytest

from dread import autocase, sysrecord, sysrun
from dread.program import Program


def test_recorded_calls_reach_the_config_path(target, tmp_path):
    image = target.image()
    image.extra_cases = str(tmp_path / "extra")
    lines = []
    kept = sysrecord.record(target, image, ["read_line", "handle_key"], dict(target.scenarios["config"], _name="config"),
                            keep_all=True, out=lines.append)
    assert "read_line" in kept and "handle_key" in kept, lines
    case = kept["read_line"][0]
    scratch = target.memory.scratch_base
    assert scratch <= case["args"][0] < scratch + 0x20000          # the caller's buffer, moved
    uart = target.models["uart"]["base"]
    assert any(p[0] == uart + 4 for p in case["poll"]) and any(p[0] == uart for p in case["poll"])
    program = Program(target, image)
    gen = autocase.Generator(target, image, program=program)

    def covered(name):
        fn = program.by_name[name]
        got = set()
        for c in autocase.extra_load(image, name):
            got |= gen.coverage(fn.entry, fn.insns, c, c["limit"])
        return fn, got
    fn, got = covered("handle_key")                 # the 'c' path: a line read, then applied
    apply = program.by_name["apply_config"].entry
    calls = [a for a in fn.insns if program.arch.is_call(program.insn(a)) and program.arch.target(program.insn(a)) == apply]
    assert calls and set(calls) <= got
    fn, got = covered("read_line")                  # the line loop, up to the line feed
    assert len(got) >= len(fn.insns) - 3, (len(got), len(fn.insns))


def test_dry_run_writes_nothing(target, tmp_path):
    image = target.image()
    image.extra_cases = str(tmp_path / "extra")
    sysrecord.record(target, image, ["read_line"], dict(target.scenarios["config"], _name="config"),
                     keep_all=True, dry_run=True, out=lambda line: None)
    assert not os.path.exists(os.path.join(image.extra_cases, "read_line.json"))


def test_a_handler_is_recorded_up_to_its_exception_return(variant, tmp_path):
    """On the M profile a handler returns through EXC_RETURN, not to an address of its caller: the
    recorder ends its call at the exception return (its r0 is not compared), so the timer's handler
    gets cases from the keys scenario (it got none: its call never ended)."""
    if variant not in ("m3", "m4"):
        pytest.skip("the M profile's exception return")
    from dread.target import Target
    from conftest import gizmo_dir
    target = Target.load(gizmo_dir(variant))
    image = target.image()
    image.extra_cases = str(tmp_path / "extra")
    lines = []
    kept = sysrecord.record(target, image, ["TIMER_IRQHandler"], dict(target.scenarios["keys"], _name="keys"),
                            keep_all=True, out=lines.append)
    assert kept.get("TIMER_IRQHandler"), lines


TWO_TASKS = r'''
#define UART (*(volatile unsigned *)0x40004000)
#define ICSR (*(volatile unsigned *)0xE000ED04)
__attribute__((noinline)) void put(int c) { UART = c; }
volatile int flag, other;
unsigned stack_a[64], stack_b[64];
unsigned *saved[2];
int current;

/* Yields to the other task, which sets flag meanwhile. */
__attribute__((noinline)) int take_flag(void) {
    int before = flag;
    ICSR = 1u << 28;
    __asm volatile ("dsb\n isb" ::: "memory");
    return flag + 10 * before;
}
__attribute__((noinline)) int twice(volatile int *p) { int a = *p; *p = a + 1; return 100 * a + *p; }

__attribute__((noreturn)) void task_b(void) {
    other = other + 1;
    flag = 5;
    ICSR = 1u << 28;
    __asm volatile ("dsb\n isb" ::: "memory");
    for (;;) ;
}
__attribute__((noreturn)) void task_a(void) {
    put('a');
    put('0' + take_flag());
    put('0' + twice(&other) % 10);
    put('d');
    for (;;) ;
}
/* A task switch as FreeRTOS's port does it: r4-r11 below the frame on the process stack. */
__attribute__((naked)) void pendsv_handler(void) {
    __asm volatile ("mrs r0, psp\n stmdb r0!, {r4-r11}\n ldr r1, =current\n ldr r2, [r1]\n"
                    "ldr r3, =saved\n str r0, [r3, r2, lsl #2]\n eor r2, r2, #1\n str r2, [r1]\n"
                    "ldr r0, [r3, r2, lsl #2]\n ldmia r0!, {r4-r11}\n msr psp, r0\n bx lr\n");
}
void setup(void) {
    unsigned *sp = stack_b + 64 - 16;           /* r4-r11, then task_b's first exception frame */
    sp[8 + 5] = 0xffffffff;
    sp[8 + 6] = (unsigned)task_b;
    sp[8 + 7] = 0x01000000;
    saved[1] = sp;
}
__attribute__((naked, noreturn)) void reset(void) {
    __asm volatile ("ldr r0, =stack_a + 256\n msr psp, r0\n movs r0, #2\n msr control, r0\n isb\n"
                    "bl setup\n b task_a\n");
}
__attribute__((section(".vectors"), used)) void (*const vectors[16])(void) = {
    (void (*)(void))0x20005000, reset, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, pendsv_handler, 0 };
'''


def two_tasks(tmp_path):
    """The program above as a target: task A's take_flag yields to task B, which sets flag."""
    import shutil
    import subprocess
    if not shutil.which("arm-none-eabi-gcc"):
        pytest.skip("needs arm-none-eabi-gcc")
    from test_bench import TARGET
    from dread import elfimport
    from dread.target import Target
    (tmp_path / "t.c").write_text(TWO_TASKS)
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { KEEP(*(.vectors)) *(.text*) *(.rodata*) } > FLASH\n"
                                   " .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "reset", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '''
[image.main]
original = "original/t.bin"
base = 0x08000000
entry = "reset"
vectors = 0x08000000

[compiler]
cross = "arm-none-eabi-"

[models.uart]
kind = "uart"
base = 0x40004000

[models.nvic]
kind = "nvic"

[scenarios.tasks]
until = {uart = "d"}
limit = 200_000
''')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    target = Target.load(str(tmp_path))
    image = target.image()
    image.extra_cases = str(tmp_path / "extra")
    return target, image


def test_a_call_across_a_task_switch(tmp_path):
    """take_flag reads flag (0), yields: PendSV switches to task B, which reads other and sets flag
    to 5, and back to task A, which reads flag again. The call is recorded although an exception
    came in between (it was dropped), task B's reads are not its inputs, and flag's second read
    gets B's value as a later stage, so the replay returns what the system run saw (5)."""
    target, image = two_tasks(tmp_path)
    run = sysrun.run_image(target, image, image.payload(), target.scenarios["tasks"])
    assert run.reports["uart"]["sent"] == "a52d", run.reports["uart"]["sent"]
    lines = []
    kept = sysrecord.record(target, image, ["take_flag"], dict(target.scenarios["tasks"], _name="tasks"),
                            keep_all=True, out=lines.append)
    assert kept.get("take_flag"), lines
    program = Program(target, image)
    data = {name: address for kind, address, name in program.symbols() if kind == "d"}
    flag = data["flag"]
    case = kept["take_flag"][0]
    assert case["later"] == [[flag, 4, 2, 5]], case
    assert [a for a, _, _ in case["mem"]] == [flag], case["mem"]       # not task B's read of other


def test_later_stages_in_the_harness(tmp_path):
    """A later stage is written once, just before its read: twice reads *p, writes *p + 1 and reads
    it again. A stage at the first read changes what it reads first and leaves its own write to
    the second; a stage at the second read overrides that write (another task wrote after it)."""
    target, image = two_tasks(tmp_path)
    program = Program(target, image)
    gen = autocase.Generator(target, image, program=program)
    fn = program.by_name["twice"]
    cell = target.memory.scratch_base

    def run(later):
        case = {"args": [cell], "mem": [[cell, 4, 3]], "returns": [], "calls": [], "frame": 0, "later": later}
        m = gen.machine()
        m.plan, m.function = [], "twice"
        gen.apply(m, case)
        return m.call(fn.entry, cell).ret
    assert run([]) == 304
    assert run([[cell, 4, 1, 7]]) == 708
    assert run([[cell, 4, 2, 9]]) == 309


def test_a_later_stage_leaves_nothing_for_the_next_case(tmp_path):
    """The emulator is reused from case to case and reset by the pages that were written; a later
    stage's write comes from a hook, past the code's own writes, and once stayed for the next case
    (so did a poll stage's and a fixed peripheral value's): take_flag only reads flag."""
    target, image = two_tasks(tmp_path)
    program = Program(target, image)
    gen = autocase.Generator(target, image, program=program)
    fn = program.by_name["take_flag"]
    flag = {name: address for kind, address, name in program.symbols() if kind == "d"}["flag"]
    case = {"args": [], "mem": [], "returns": [], "calls": [], "frame": 0, "later": [[flag, 4, 2, 5]]}
    m = gen.machine()
    m.plan, m.function = [], "take_flag"
    gen.apply(m, case)
    assert m.call(fn.entry).ret == 5
    assert int.from_bytes(gen.machine().read(flag, 4), "little") == 0


def test_a_loop_is_recorded_as_the_answers_its_callees_gave(target):
    """A function that never returns (gizmo's main loop) is recorded as a loop case: the answers its
    callees gave it in the run become the stubs' (in the keys scenario ring_get's -1 round after round,
    then the 's' typed), what changed under its own reads later stages (the timer's ticks it waits
    for), and the replay makes the run's calls: handle_key's where the key came. With the key taken out
    of the answers (ring_get's -1 every round), an answer turned once (0: a key) takes the case into
    handle_key, which those answers never call (FreeRTOS's task loops: the error paths behind their
    checks)."""
    image = target.image()
    program = Program(target, image)
    gen = autocase.Generator(target, image, program=program)
    fn = program.by_name["main"]
    loops = {fn.entry: {a + program.insn(a).size: a for a in fn.insns if program.insn(a) is not None
                        and program.arch.flow(program.insn(a))[0] in ("call", "icall")}}
    scenario = dict(target.scenarios["keys"], _name="keys")
    system = sysrun.System(target, image, image.payload(), program=program, scenario=scenario)
    recorder = sysrecord.Recorder(system, {fn.entry: "main"}, extents={fn.entry: (fn.entry, program.end(fn))},
                                  loops=loops)
    system.run(int(scenario.get("limit", sysrun.LIMIT)))
    call = next(c for c in recorder.active if c["entry"] == fn.entry)
    ring, handle = program.by_name["ring_get"].entry, program.by_name["handle_key"].entry
    calls_of = lambda target_: [a for a in fn.insns if program.arch.is_call(program.insn(a)) and
                                program.arch.target(program.insn(a)) == target_]
    sites = calls_of(handle)
    assert sites and ring in [program.arch.target(program.insn(s)) for s in call["sites"]]

    def covered(cases):
        got = set()
        for c in cases:
            got |= gen.coverage(fn.entry, fn.insns, c, c["limit"])
        return got
    cases = sysrecord.loop_cases(gen, fn, recorder, set(), 8)
    assert cases and all(c["hang"] and c["stub_calls"] and c["returns"] for c in cases)
    assert set(sites) <= covered([c for c in cases if not c.get("_turned")])      # the run's own answers
    keyless = [0xFFFFFFFF if site in calls_of(ring) and answer != 0xFFFFFFFF else answer
               for answer, site in zip(call["answers"], call["sites"])]
    assert keyless != call["answers"]
    call["answers"] = keyless
    cases = sysrecord.loop_cases(gen, fn, recorder, set(), 8)
    assert not set(sites) & covered([c for c in cases if not c.get("_turned")])
    assert any(c.get("_turned") for c in cases) and set(sites) <= covered(cases)
