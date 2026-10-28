"""dread sysrun on gizmo: both builds boot to READY from reset with the same device log in the
original and the rebuilt image (and the same shared clock, although six functions run as C), a bug
planted in a rewritten function shows as a difference in the device log, and the stop rules end a
waiting and a polling firmware."""
import json
import os

import pytest

from dread import build, sysrun
from conftest import needs_riscv_gcc, RISCV_CROSS, RISCV_MARCH, riscv_toml
from test_runner import src_copy

pytestmark = pytest.mark.compiles


def both(target, payload=None, scenario=None):
    image = target.image()
    recon = payload or os.path.join(build.recon_dir(target, image), "payload.bin")
    if payload is None:
        build.build(target, image, quiet=True)
    with open(os.path.join(os.path.dirname(recon), "placement.json")) as f:
        placed = json.load(f)["functions"]
    built = [n for n, p in placed.items() if p["placement"] != "helper"]
    scenario = scenario if scenario is not None else target.scenarios["boot"]
    with open(recon, "rb") as f:
        rebuilt = f.read()
    a = sysrun.run_image(target, image, image.payload(), scenario, built=built)
    b = sysrun.run_image(target, image, rebuilt, scenario, placement=placed)
    return a, b


def test_boot_agrees(target):
    a, b = both(target)
    assert a.how.startswith("until") and b.how.startswith("until"), (a.how, b.how)
    assert a.reports["uart"]["sent"].endswith("READY") and "GIZMO 1000" in a.reports["uart"]["sent"]
    assert sysrun.compare(a, b) == []
    assert a.clock == b.clock and a.log == b.log


def test_a_planted_bug_shows(target, variant, tmp_path):
    """fmt_hex writes the checksum line; a digit one too high changes what the UART sends."""
    src = src_copy(tmp_path, variant, ("out[i] = HEX_DIGITS[v & 15];", "out[i] = HEX_DIGITS[(v + 1) & 15];"))
    payload = build.build(target, target.image(), src=str(src), out=str(tmp_path / "recon"), quiet=True)
    a, b = both(target, payload)
    diff = sysrun.compare(a, b)
    assert diff and diff[0].startswith("device logs differ"), diff
    assert "crc" in a.reports["uart"]["sent"] and a.reports != b.reports


def test_stop_rules(target):
    image = target.image()
    waits = sysrun.run_image(target, image, image.payload(), {"models": ["uart"], "limit": 3_000_000})
    assert waits.how.startswith("waits"), waits.how          # after READY without a timer: nothing comes
    # main outside shared code (as rebuilt): its idle loop is found a loop, and with nothing scheduled
    # the run ends there, without waiting QUIET units of shared clock
    idle = sysrun.run_image(target, image, image.payload(), {"models": ["uart"], "limit": 3_000_000}, built=["main"])
    assert idle.how.startswith("waits") and idle.clock < waits.clock - sysrun.QUIET, (idle.how, idle.clock)
    polls = sysrun.run_image(target, image, image.payload(), {"models": [], "limit": 3_000_000})
    assert polls.how.startswith("polls"), polls.how          # no UART: uart_putc waits for "can send"


def test_keys_go_through_the_timer_interrupt(target):
    """'s' arrives in the receive queue; timer_irq (an interrupt: a32 IRQ, m3 exception 16) moves it
    into the ring buffer and the main loop prints the status again."""
    a, b = both(target, scenario=target.scenarios["keys"])
    assert a.how.startswith("until") and sysrun.compare(a, b) == [], (a.how, sysrun.compare(a, b))
    assert a.reports["uart"]["sent"].endswith("READY\r\nmode run volume 7")
    assert a.reports["interrupts"] and a.reports["interrupts"] == b.reports["interrupts"]


def test_ticks_agree_for_a_long_run(target):
    """No end condition: the timer keeps interrupting until the limit; both images take the same
    interrupts at the same places, and the LED key reaches the pins."""
    scenario = {"input": [[40_000, "uart", "l"]], "limit": 600_000}
    a, b = both(target, scenario=scenario)
    assert a.how.startswith("limit") and sysrun.compare(a, b) == []
    assert sum(a.reports["interrupts"].values()) > 10
    assert a.reports["gpio"]["pins"] & 2                     # led_on: GPIO_OUT bit 1


def test_a_bug_in_the_interrupt_path_shows(target, variant, tmp_path):
    src = src_copy(tmp_path, variant, ("r->buf[head] = c;", "r->buf[head] = c + 1;"))
    payload = build.build(target, target.image(), src=str(src), out=str(tmp_path / "recon"), quiet=True)
    a, b = both(target, payload, scenario=target.scenarios["keys"])
    assert sysrun.compare(a, b), (a.how, b.how)


class Clock:
    """What a model needs of the system, for unit tests."""
    def __init__(self):
        self.clock, self.lines, self.noted, self.changed = 0, {}, [], 0

    def interrupt_level(self, number, level):
        self.lines[number] = level

    def interrupts_changed(self):
        self.changed += 1

    def note(self, *what):
        self.noted.append(what)


def test_timer_counts_and_expires():
    from dread.models.timer import Timer
    system = Clock()
    t = Timer(system, "timer", {"base": 0x1000, "rate": 10, "irq": 3})
    t.write(0x1004, 4, 100)                  # load
    t.write(0x1008, 4, 3)                    # run, interrupt
    assert t.next_event() == 1000
    system.clock = 400
    assert t.read(0x1000, 4) == 60 and t.read(0x100C, 4) == 0
    system.clock = 1000
    t.event(1000)
    assert t.read(0x100C, 4) == 1 and system.lines[3] and t.next_event() == 2000
    t.write(0x100C, 4, 1)                    # clear
    assert not system.lines[3]


def test_systick():
    from dread.models.nvic import Nvic, SYST
    system = Clock()
    n = Nvic(system, "nvic", {"systick_rate": 2})
    n.write(SYST + 4, 4, 99)                 # reload
    n.write(SYST, 4, 3)                      # enable, interrupt
    assert n.next_event() == 200
    n.event(200)
    assert n.pending_interrupt() == 15 and n.read(SYST, 4) & (1 << 16)
    assert not n.read(SYST, 4) & (1 << 16)   # countflag clears on read
    n.taken(15)
    assert n.pending_interrupt() is None
    n.write(0xE000E100, 4, 1 << 4)           # enable line 4
    n.line(4, True)
    assert n.pending_interrupt() == 20


def test_a_stop_at_a_device_access_does_not_repeat_it(target, monkeypatch):
    """With a peripheral access costing much of the shared clock, events come due inside the access
    hooks. A stop there left pc on the accessing instruction (Unicorn's way), and going on repeated
    the access: the UART sent characters twice ("modee run") and input went missing. The run stops
    at the next block instead (System.due)."""
    monkeypatch.setattr(sysrun, "ACCESS", 333)
    image = target.image()
    out = sysrun.run_image(target, image, image.payload(), target.scenarios["keys"])
    assert out.how.startswith("until"), out.how
    assert out.reports["uart"]["sent"].startswith("GIZMO 1000\r\nmode run volume 7\r\ncrc c9c999c5\r\nREADY\r\nmode run")


def test_nvic_priorities_masks_and_nesting():
    """The Cortex-M controller: implemented priority bits, PRIGROUP, BASEPRI and PRIMASK, nesting by
    priority (equal priorities never preempt), ICSR's pending bits and VECTACTIVE, AIRCR's key."""
    from dread.models.nvic import AIRCR, ICSR, IPR, NVIC, SHPR, Nvic
    system = Clock()
    n = Nvic(system, "nvic", {"priority_bits": 3})
    n.write(IPR + 2, 1, 0xFF)
    assert n.read(IPR + 2, 1) == 0xE0 and n.read(IPR, 4) == 0xE00000      # three bits implemented
    n.write(SHPR + 8, 4, 0xFF << 16)                                        # SHPR3: PendSV lowest
    assert n.priority(14) == 0xE0 and n.priority(15) == 0
    n.write(ICSR, 4, 1 << 28)
    assert n.pending_interrupt() == 14 and n.read(ICSR, 4) >> 28 & 1
    assert n.may_take(14, {}) and not n.may_take(14, {"basepri": 0x80}) and not n.may_take(14, {"primask": 1})
    n.taken(14)
    assert n.read(ICSR, 4) & 0x1FF == 14 and n.read(ICSR, 4) >> 11 & 1   # VECTACTIVE, RETTOBASE
    n.write(NVIC, 4, 1 << 2)                                                # enable line 2 (priority 0xE0)
    n.line(2, True)
    assert n.pending_interrupt() == 18 and not n.may_take(18, {})          # equal priority: no preemption
    n.write(IPR + 2, 1, 0x40)
    assert n.may_take(18, {}) and not n.may_take(18, {"basepri": 0x40})
    n.taken(18)
    assert n.active == [14, 18] and not n.read(ICSR, 4) >> 11 & 1
    n.returned()
    n.write(AIRCR, 4, 0x3 << 8)                                            # no key: ignored
    assert n.read(AIRCR, 4) == 0xFA050000
    n.write(AIRCR, 4, 0x05FA0000 | 0x6 << 8)                               # group priority: bit 7 only
    assert n.group(0xE0) == 0x80 and n.group(0x40) == 0


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_cortex_m_exceptions_from_reset(tmp_path):
    """A program on the process stack: svc enters SVCall (its frame on the process stack, EXC_RETURN
    for thread mode on it); PendSV pended inside SVCall waits for it (equal priority) and comes
    behind it; with BASEPRI above PendSV's priority it waits until BASEPRI is 0 again; inside
    PendSV a line of higher priority set pending through ISPR preempts it (a nested return)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.target import Target
    (tmp_path / "t.c").write_text(r'''
#define UART (*(volatile unsigned *)0x40004000)
#define ICSR (*(volatile unsigned *)0xE000ED04)
#define SHPR3 (*(volatile unsigned *)0xE000ED20)
#define ISER0 (*(volatile unsigned *)0xE000E100)
#define ISPR0 (*(volatile unsigned *)0xE000E200)
__attribute__((noinline)) void put(int c) { UART = c; }
__attribute__((noinline)) void barrier(void) { __asm volatile ("" ::: "memory"); }
static volatile int calls;
unsigned process_stack[64];
void svc_handler(void) { put('S'); ICSR = 1u << 28; put('s'); }
void pendsv_handler(void) {
    if (calls++ == 0) { put('P'); return; }
    put('Q'); ISPR0 = 1; barrier(); put('q');
}
void line0_handler(void) { put('I'); }
__attribute__((noreturn)) void body(void) {
    put('a');
    __asm volatile ("svc 0" ::: "memory");
    put('b');
    SHPR3 = 0xffu << 16;
    ISER0 = 1;
    __asm volatile ("msr basepri, %0" :: "r"(0x80) : "memory");
    ICSR = 1u << 28;
    put('c');
    __asm volatile ("msr basepri, %0" :: "r"(0) : "memory");
    barrier();
    put('d');
    for (;;) barrier();
}
__attribute__((naked, noreturn)) void reset(void) {
    __asm volatile ("ldr r0, =process_stack + 256\n msr psp, r0\n movs r0, #2\n msr control, r0\n isb\n b body\n");
}
__attribute__((section(".vectors"), used)) void (*const vectors[17])(void) = {
    (void (*)(void))0x20005000, reset, 0, 0, 0, 0, 0, 0, 0, 0, 0, svc_handler, 0, 0, pendsv_handler, 0, line0_handler };
''')
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

[scenarios.exceptions]
until = {uart = "d"}
limit = 200_000
''')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    image = t.image()
    run = sysrun.run_image(t, image, image.payload(), t.scenarios["exceptions"])
    assert run.how.startswith("until"), run.how
    assert run.reports["uart"]["sent"] == "aSsPbcQIqd", run.reports["uart"]["sent"]
    assert run.reports["interrupts"] == {11: 1, 14: 2, 16: 1}, run.reports["interrupts"]


def test_clint_timer_and_software_interrupts():
    """mtime counts the shared clock; the timer interrupt is a level while mtime >= mtimecmp (two
    words, a half written alone keeps the other); msip comes first; MIE and the mie bit decide."""
    from dread.models.clint import Clint
    system = Clock()
    c = Clint(system, "clint", {"base": 0x02000000, "rate": 10})
    system.clock = 1000
    assert c.read(0x0200BFF8, 4) == 100 and c.read(0x0200BFFC, 4) == 0
    c.write(0x02004004, 4, 0)
    c.write(0x02004000, 4, 150)
    assert c.pending_interrupt() is None and c.next_event() == 1500
    system.clock = 1500
    assert c.pending_interrupt() == 7
    assert not c.may_take(7, {"mstatus": 8, "mie": 0}) and c.may_take(7, {"mstatus": 8, "mie": 0x80})
    c.write(0x02004004, 4, 1)                                  # the high word: far away again
    assert c.read(0x02004000, 4) == 150 and c.pending_interrupt() is None
    c.write(0x02000000, 4, 1)
    assert c.pending_interrupt() == 3 and not c.may_take(3, {"mstatus": 0, "mie": 8})
    c.write(0x0200BFF8, 4, 5)                                  # mtime set: counts on from there
    system.clock = 1600
    assert c.read(0x0200BFF8, 4) == 15


@needs_riscv_gcc
def test_riscv_traps_from_reset(tmp_path):
    """A program with a vectored mtvec: ecall traps to the base with mepc on the ecall (the handler
    steps over it), the timer interrupt comes at base + 4 * 7 once mtime reaches mtimecmp and MTIE
    and MIE are set, and ends when the handler moves mtimecmp away."""
    import subprocess
    from dread import elfimport
    from dread.target import Target
    (tmp_path / "t.c").write_text(r'''
#define THR (*(volatile unsigned char *)0x10000000)
#define LSR (*(volatile unsigned char *)0x10000005)
#define MTIMECMP_LO (*(volatile unsigned *)0x02004000)
#define MTIMECMP_HI (*(volatile unsigned *)0x02004004)
#define MTIME_LO (*(volatile unsigned *)0x0200bff8)
__attribute__((noinline)) void put(int c) { while (!(LSR & 0x20)) ; THR = c; }
volatile int done;
__attribute__((interrupt("machine"))) void sync_trap(void) {
    unsigned epc;
    __asm volatile ("csrr %0, mepc" : "=r"(epc));
    put('E');
    __asm volatile ("csrw mepc, %0" :: "r"(epc + 4));
}
__attribute__((interrupt("machine"))) void timer_trap(void) { put('T'); MTIMECMP_HI = 0xffffffff; done = 1; }
__attribute__((interrupt("machine"))) void bad_trap(void) { put('?'); for (;;) ; }
extern char vectors[];
__asm__(".section .text.vectors\n.balign 64\n.option push\n.option norvc\n.global vectors\nvectors:\n"
        " j sync_trap\n j bad_trap\n j bad_trap\n j bad_trap\n j bad_trap\n j bad_trap\n j bad_trap\n j timer_trap\n"
        ".option pop\n");
__attribute__((noreturn)) void cmain(void) {
    __asm volatile ("csrw mtvec, %0" :: "r"((unsigned)vectors | 1));
    put('a');
    __asm volatile ("ecall" ::: "memory");
    put('b');
    MTIMECMP_HI = 0;
    MTIMECMP_LO = MTIME_LO + 50;
    __asm volatile ("csrs mie, %0" :: "r"(0x80));
    __asm volatile ("csrs mstatus, %0" :: "r"(8));
    while (!done) ;
    put('c');
    for (;;) ;
}
__asm__(".section .text.start\n.global _start\n.type _start, @function\n_start:\n li sp, 0x80090000\n j cmain\n"
        ".size _start, . - _start\n");
''')
    (tmp_path / "t.ld").write_text("SECTIONS { . = 0x80000000; .text : { *(.text.start) *(.text*) }\n"
                                   " . = 0x80080000; .bss : { *(.sbss*) *(.bss*) } }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run([RISCV_CROSS + "gcc", "-march=" + RISCV_MARCH, "-mabi=ilp32", "-O2", "-nostdlib",
                    "-ffunction-sections", "-T", str(tmp_path / "t.ld"), "-e", "_start", "-o", elf, str(tmp_path / "t.c")],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml('''[target]
name = "traps"
[arch]
name = "riscv"
isa = "rv32"
extensions = "imac"
[memory]
regions = [{name = "rom", base = 0x80000000, size = 0x10000, kind = "rom"},
           {name = "ram", base = 0x80080000, size = 0x10000}]
io = [[0x02000000, 0x02010000], [0x10000000, 0x10001000]]
stack = {top = 0x80090000, size = 0x1000}
sentinel = 0x7fff0000
[image.main]
original = "original/t.bin"
base = 0x80000000
entry = "_start"
[compiler]
cross = "riscv64-elf-"
cflags = ["-march=rv32imac_zicsr", "-mabi=ilp32", "-O2"]
[models.uart]
kind = "uart"
base = 0x10000000
data = 0x0
status = 0x5
tx_ready = 0x20
rx_ready = 0x1
size = 0x8
[models.clint]
kind = "clint"
base = 0x02000000
rate = 1
[scenarios.traps]
until = {uart = "c"}
limit = 200_000
'''))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    image = t.image()
    run = sysrun.run_image(t, image, image.payload(), t.scenarios["traps"])
    assert run.how.startswith("until"), run.how
    assert run.reports["uart"]["sent"] == "aEbTc", run.reports["uart"]["sent"]
    assert run.reports["interrupts"] == {7: 1}, run.reports["interrupts"]
    # the vector's `j` (no function's entry) hands the trap's mark on to the handler it jumps to
    from dread.program import Program
    rows, unexplained = sysrun.transfers(Program(t, image), run.calls)
    got = {(r["how"], r["to"], r.get("exception")) for r in rows if r["how"] in ("exception", "syscall", "reset")}
    assert got == {("reset", "_start", None), ("syscall", "sync_trap", None), ("exception", "timer_trap", 7)}, got
    assert unexplained == 0


@needs_riscv_gcc
def test_a_vector_table_the_tables_call_a_function(tmp_path, monkeypatch):
    """mtvec's vectored mode with the table under a label typed as a function, as an import makes it
    of a toolchain without RISC-V's mapping symbols (binutils 2.35: CI's Ubuntu 22.04 job): ecall
    enters the table's first slot, the entry of `vectors`, which only branches; the system call's mark
    goes on to sync_trap all the same (System._vector_at), not to the table, in the C hook and in the
    Python hooks alike."""
    import subprocess
    from dread import elfimport, fasthooks
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.c").write_text(r'''
#define THR (*(volatile unsigned char *)0x10000000)
#define LSR (*(volatile unsigned char *)0x10000005)
__attribute__((noinline)) void put(int c) { while (!(LSR & 0x20)) ; THR = c; }
__attribute__((interrupt("machine"))) void sync_trap(void) {
    unsigned epc;
    __asm volatile ("csrr %0, mepc" : "=r"(epc));
    put('E');
    __asm volatile ("csrw mepc, %0" :: "r"(epc + 4));
}
__attribute__((interrupt("machine"))) void other_trap(void) { put('?'); for (;;) ; }
extern char vectors[];
__asm__(".section .text.vectors\n.balign 64\n.option push\n.option norvc\n.global vectors\n.type vectors, @function\n"
        "vectors:\n j sync_trap\n j other_trap\n j other_trap\n j other_trap\n j other_trap\n j other_trap\n j other_trap\n"
        " j other_trap\n.size vectors, . - vectors\n.option pop\n");
__attribute__((noreturn)) void cmain(void) {
    __asm volatile ("csrw mtvec, %0" :: "r"((unsigned)vectors | 1));
    put('a');
    __asm volatile ("ecall" ::: "memory");
    put('b');
    for (;;) ;
}
__asm__(".section .text.start\n.global _start\n.type _start, @function\n_start:\n li sp, 0x80090000\n j cmain\n"
        ".size _start, . - _start\n");
''')
    (tmp_path / "t.ld").write_text("SECTIONS { . = 0x80000000; .text : { *(.text.start) *(.text*) }\n"
                                   " . = 0x80080000; .bss : { *(.sbss*) *(.bss*) } }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run([RISCV_CROSS + "gcc", "-march=" + RISCV_MARCH, "-mabi=ilp32", "-O2", "-nostdlib",
                    "-ffunction-sections", "-T", str(tmp_path / "t.ld"), "-e", "_start", "-o", elf, str(tmp_path / "t.c")],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml('''[target]
name = "vectors"
[arch]
name = "riscv"
isa = "rv32"
extensions = "imac"
[memory]
regions = [{name = "rom", base = 0x80000000, size = 0x10000, kind = "rom"},
           {name = "ram", base = 0x80080000, size = 0x10000}]
io = [[0x10000000, 0x10001000]]
stack = {top = 0x80090000, size = 0x1000}
sentinel = 0x7fff0000
[image.main]
original = "original/t.bin"
base = 0x80000000
entry = "_start"
[compiler]
cross = "riscv64-elf-"
cflags = ["-march=rv32imac_zicsr", "-mabi=ilp32", "-O2"]
[models.uart]
kind = "uart"
base = 0x10000000
data = 0x0
status = 0x5
tx_ready = 0x20
rx_ready = 0x1
size = 0x8
[scenarios.ecall]
until = {uart = "b"}
limit = 200_000
'''))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    image = t.image()
    program = Program(t, image)
    assert "vectors" in program.by_name and "sync_trap" in program.by_name     # (the table is a function here)
    run = sysrun.run_image(t, image, image.payload(), t.scenarios["ecall"])
    assert run.how.startswith("until") and run.reports["uart"]["sent"] == "aEb", (run.how, run.reports)
    rows, unexplained = sysrun.transfers(program, run.calls)
    got = {(r["how"], r["to"]) for r in rows if r["how"] in ("exception", "syscall", "reset")}
    assert got == {("reset", "_start"), ("syscall", "sync_trap")}, got
    assert unexplained == 0
    if fasthooks.library() is not None:
        monkeypatch.setattr(fasthooks, "_LIB", [None])              # as without a C compiler
        python = sysrun.run_image(t, image, image.payload(), t.scenarios["ecall"])
        assert (python.calls, python.log) == (run.calls, run.log)


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_run_that_spins_with_its_interrupt_masked_ends(tmp_path):
    """After three ticks the program masks interrupts and spins (as an assertion's loop does): the
    tick keeps coming due and is never taken, nothing new runs. The run ends as "spins" a million
    clock units after its last progress instead of going on to its limit (a FreeRTOS image with every
    function rebuilt from C spent 95 % of a run so)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.target import Target
    (tmp_path / "t.c").write_text(r'''
#define UART (*(volatile unsigned *)0x40004000)
#define SYST_CSR (*(volatile unsigned *)0xE000E010)
#define SYST_RVR (*(volatile unsigned *)0xE000E014)
#define SYST_CVR (*(volatile unsigned *)0xE000E018)
volatile unsigned ticks;
void systick_handler(void) { ticks++; }
__attribute__((noreturn)) void reset(void) {
    SYST_RVR = 1000; SYST_CVR = 0; SYST_CSR = 7;
    while (ticks < 3) ;
    UART = 'x';
    __asm volatile ("cpsid i" ::: "memory");
    for (;;) ;
}
__attribute__((section(".vectors"), used)) void (*const vectors[16])(void) = {
    (void (*)(void))0x20004000, reset, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, systick_handler };
''')
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
systick_rate = 1

[scenarios.spin]
limit = 50_000_000
''')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    image = t.image()
    run = sysrun.run_image(t, image, image.payload(), t.scenarios["spin"])
    assert run.reports["uart"]["sent"] == "x", run.reports
    assert run.how.startswith("spins"), run.how
    assert run.clock < 3_000_000, run.clock


WAITS = r'''
#define UART (*(volatile unsigned *)0x40004000)
#define T_LOAD (*(volatile unsigned *)0x40000004)
#define T_CTRL (*(volatile unsigned *)0x40000008)
#define T_STAT (*(volatile unsigned *)0x4000000c)
#define NVIC_ISER (*(volatile unsigned *)0xE000E100)
volatile unsigned ticks;
volatile unsigned char buf[6000];
void timer_handler(void) { T_STAT = 1; ticks++; }
__attribute__((noinline)) void fill(volatile unsigned char *p, unsigned n) { while (n--) *p++ = 0x5a; }
__attribute__((noinline)) void wait(void) {
    for (;;) {
        __asm volatile ("cpsid i" ::: "memory");
        unsigned t = ticks;
        for (volatile int i = 0; i < 40; i++) ;
        __asm volatile ("cpsie i" ::: "memory");
        if (t)
            return;
    }
}
__attribute__((noreturn)) void reset(void) {
    NVIC_ISER = 1;
    T_LOAD = 100;
    T_CTRL = 3;
    fill(buf, sizeof buf);
    UART = '0' + ticks;
    wait();
    UART = '0' + ticks;
    for (;;) ;
}
__attribute__((section(".vectors"), used)) void (*const vectors[17])(void) = {
    (void (*)(void))0x20004000, reset, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, timer_handler };
'''

WAITS_C = r'''#include "dread.h"
#define ticks (*(volatile unsigned *)TICKS)
void fill(volatile unsigned char *p, unsigned n)
{
    volatile unsigned *w = (volatile unsigned *)p;
    for (n /= 4; n; n--)
        *w++ = 0x5a5a5a5a;
}
void wait(void)
{
    for (;;) {
        __asm volatile ("cpsid i" ::: "memory");
        unsigned t = ticks;
        for (volatile int i = 40; i > 0; i--)
            ;
        __asm volatile ("cpsie i" ::: "memory");
        if (t)
            return;
    }
}
'''


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_wait_outside_shared_code_is_a_loop_a_computation_is_not(tmp_path):
    """fill and wait are rebuilt from C, so their code is outside the shared clock in both images. A
    one-shot timer comes due 100 units after it starts, which the shared code before wait does not
    reach: only a wait may move the clock on to it. fill (a computation: 6000 blocks of the
    original's byte loop, 1500 of the C's word loop) is none; wait (polling with interrupts masked
    for most of each round) is one, and its interrupt is taken behind the cpsie. Until 2026-10-22
    FROZEN blocks in a row outside shared code were a wait: the original took the tick inside fill
    ("11"), the rebuilt image moved the clock on in wait at a block where it masked the interrupt,
    and never asked again ("0", then "waits"); FreeRTOS on RISC-V GCC -Os and Cortex-M clang -O2
    differed so with lifted C alone."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.target import Target
    (tmp_path / "t.c").write_text(WAITS)
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
limit = 0x08000400

[compiler]
cross = "arm-none-eabi-"
cflags = ["-mcpu=cortex-m3", "-mthumb", "-O2", "-ffunction-sections"]

[models.uart]
kind = "uart"
base = 0x40004000

[models.timer]
kind = "timer"
base = 0x40000000
count = 0x0
load = 0x4
ctrl = 0x8
stat = 0xc
rate = 1
irq = 0
periodic = false

[models.nvic]
kind = "nvic"

[scenarios.run]
until = {uart = "01"}
limit = 2_000_000
''')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    ticks = next(line.split()[1] for line in (tmp_path / "config" / "symbols.txt").read_text().splitlines()
                 if line.endswith(" ticks"))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.c").write_text(WAITS_C.replace("TICKS", ticks))
    t = Target.load(str(tmp_path))
    payload = build.build(t, t.image(), src=str(tmp_path / "src"), out=str(tmp_path / "recon"), quiet=True)
    a, b = both(t, payload, t.scenarios["run"])
    assert a.how.startswith("until") and b.how.startswith("until"), (a.how, b.how)
    assert a.reports["uart"]["sent"] == "01" and a.reports["interrupts"] == {16: 1}, a.reports
    assert sysrun.compare(a, b) == [] and a.log == b.log


PROGRESS = r'''
#define UART (*(volatile unsigned *)0x40004000)
#define T_LOAD (*(volatile unsigned *)0x40000004)
#define T_CTRL (*(volatile unsigned *)0x40000008)
#define T_STAT (*(volatile unsigned *)0x4000000c)
#define NVIC_ISER (*(volatile unsigned *)0xE000E100)
volatile unsigned ticks;
__attribute__((used)) volatile unsigned tally, nest, spins;
void timer_handler(void) { T_STAT = 1; ticks++; }
/* while (tally < 6000) tally++: one block a round, entered with every register as in the round
   before (the count is cleared out of r0 before the branch), the progress in memory alone */
__attribute__((naked, noinline)) void tally_up(void)
{
    __asm volatile ("    ldr r1, =tally\n"
                    "1:  ldr r0, [r1]\n"
                    "    adds r0, #1\n"
                    "    str r0, [r1]\n"
                    "    movw r2, #6000\n"
                    "    cmp r0, r2\n"
                    "    movw r0, #0\n"
                    "    movw r2, #0\n"
                    "    blo 1b\n"
                    "    bx lr\n"
                    "    .ltorg\n");
}
/* until ticks: a poll whose stores come round, two words pushed and popped and nest raised in one
   block and lowered in the next. The raising store ends an IT block, and the flags change behind
   it: a write hook that left Unicorn's stale IT state (dread.arch.arm.settle_it) would make the
   next block's load of ticks conditional, skipped, and the poll endless */
__attribute__((naked, noinline)) void await_tick(void)
{
    __asm volatile ("    ldr r1, =nest\n"
                    "    ldr r3, =ticks\n"
                    "1:  push {r1, r3}\n"
                    "    ldr r2, [r1]\n"
                    "    adds r2, #1\n"
                    "    cmp r2, #0\n"
                    "    it ne\n"
                    "    strne r2, [r1]\n"
                    "    cmp r2, r2\n"
                    "    b 2f\n"
                    "2:  ldr r0, [r3]\n"
                    "    subs r2, #1\n"
                    "    str r2, [r1]\n"
                    "    pop {r1, r3}\n"
                    "    cmp r0, #0\n"
                    "    beq 1b\n"
                    "    bx lr\n"
                    "    .ltorg\n");
}
/* spins++ until the second tick: a busy loop, its registers repeating, its counter in memory */
__attribute__((naked, noinline)) void spin_until_two(void)
{
    __asm volatile ("    ldr r1, =spins\n"
                    "    ldr r2, =ticks\n"
                    "1:  ldr r0, [r1]\n"
                    "    adds r0, #1\n"
                    "    str r0, [r1]\n"
                    "    ldr r3, [r2]\n"
                    "    cmp r3, #2\n"
                    "    movw r0, #0\n"
                    "    movw r3, #0\n"
                    "    blo 1b\n"
                    "    bx lr\n"
                    "    .ltorg\n");
}
__attribute__((noreturn)) void reset(void) {
    NVIC_ISER = 1;
    T_LOAD = 1000;
    T_CTRL = 3;
    tally_up();
    UART = '0' + ticks;
    await_tick();
    UART = '0' + ticks;
    spin_until_two();
    UART = '0' + ticks;
    for (;;) ;
}
__attribute__((section(".vectors"), used)) void (*const vectors[17])(void) = {
    (void (*)(void))0x20004000, reset, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, timer_handler };
'''

PROGRESS_C = r'''#include "dread.h"
#define ticks (*(volatile unsigned *)TICKS)
#define tally (*(volatile unsigned *)TALLY)
#define nest (*(volatile unsigned *)NEST)
#define spins (*(volatile unsigned *)SPINS)
void tally_up(void)
{
    unsigned n = tally;
    while (n < 6000)
        tally = ++n;
}
void await_tick(void)
{
    unsigned t;
    do {
        nest++;
        t = ticks;
        nest--;
    } while (!t);
}
void spin_until_two(void)
{
    while (ticks < 2)
        spins++;
}
'''


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_computation_whose_progress_lies_in_memory_is_no_wait(tmp_path, monkeypatch):
    """A loop comes round when its registers and memory are both as at the anchor (System._loops).
    tally_up, await_tick and spin_until_two are rebuilt from C, their code outside shared code in
    both images, and a periodic timer comes due every 1,000 units, which only a wait may move the
    clock on to (the shared code and the three entries, ENTRY units each, take less). tally_up, the
    original's in assembly, is a computation whose registers repeat at
    its loop's head every round while its count goes up in memory: no wait (the C keeps the count in
    a register); until 2026-10-23 its registers made it one, the original took the tick inside it,
    printed "1" first and differed from the rebuilt image ("012"). await_tick, a poll on what the
    interrupt sets whose stores come round within a round, is a loop found within a round or two of
    the anchor. spin_until_two, a busy loop whose counter lies in memory while its registers repeat,
    is a wait after LOOP_MAX blocks. The C hook and the Python hooks (with their write hooks) decide
    alike."""
    from dread import fasthooks
    from dread.program import Program
    t = _program_target(tmp_path, PROGRESS, '''
[models.uart]
kind = "uart"
base = 0x40004000

[models.timer]
kind = "timer"
base = 0x40000000
count = 0x0
load = 0x4
ctrl = 0x8
stat = 0xc
rate = 1
irq = 0
periodic = true

[models.nvic]
kind = "nvic"

[scenarios.run]
until = {uart = "012"}
limit = 2_000_000
''', image="limit = 0x08000400", compiler='cflags = ["-mcpu=cortex-m3", "-mthumb", "-O2", "-ffunction-sections"]')
    symbols = {line.split()[2]: line.split()[1] for line in (tmp_path / "config" / "symbols.txt").read_text().splitlines()
               if len(line.split()) == 3}
    source = PROGRESS_C
    for name in ("ticks", "tally", "nest", "spins"):
        source = source.replace(name.upper(), symbols[name])
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.c").write_text(source)
    payload = build.build(t, t.image(), src=str(tmp_path / "src"), out=str(tmp_path / "recon"), quiet=True)
    with open(tmp_path / "recon" / "placement.json") as f:
        placed = [(p["address"], p["address"] + p["c_bytes"], n) for n, p in json.load(f)["functions"].items()]
    program = Program(t, t.image())
    waits = []
    init, real = sysrun.System.__init__, sysrun.System._loops

    def tagged(self, *args, **kwargs):
        init(self, *args, **kwargs)
        self.test_rebuilt = kwargs.get("placement") is not None

    def spy(self, address, n):
        found = real(self, address, n)
        if found:
            name = next((c for lo, hi, c in placed if lo <= address < hi), None) if self.test_rebuilt else None
            waits.append(("rebuilt" if self.test_rebuilt else "original", name or program.function_at(address).name,
                          "LOOP_MAX" if n >= sysrun.LOOP_MAX else n))
        return found
    monkeypatch.setattr(sysrun.System, "__init__", tagged)
    monkeypatch.setattr(sysrun.System, "_loops", spy)

    def run():
        del waits[:]
        a, b = both(t, payload, t.scenarios["run"])
        return (a.how, a.clock, a.log, a.reports), (b.how, b.clock, b.log, b.reports), list(waits)
    original, rebuilt, found = run()
    assert original[0].startswith("until") and rebuilt[0].startswith("until"), (original[0], rebuilt[0])
    assert original[3]["uart"]["sent"] == "012" and original[3]["interrupts"] == {16: 2}, original[3]
    assert original[2] == rebuilt[2] and original[3] == rebuilt[3]
    for run_of in ("original", "rebuilt"):
        mine = [w[1:] for w in found if w[0] == run_of]
        assert [w[0] for w in mine] == ["await_tick", "spin_until_two"], found
        assert mine[0][1] <= sysrun.FROZEN + 4 and mine[1][1] == "LOOP_MAX", found
    if fasthooks.library() is not None:
        monkeypatch.setattr(fasthooks, "_LIB", [None])      # as without a C compiler
        assert run() == (original, rebuilt, found)


ALTERNATE = r'''
#define UART (*(volatile unsigned *)0x40004000)
#define T_LOAD (*(volatile unsigned *)0x40000004)
#define T_CTRL (*(volatile unsigned *)0x40000008)
#define T_STAT (*(volatile unsigned *)0x4000000c)
#define NVIC_ISER (*(volatile unsigned *)0xE000E100)
volatile unsigned ticks, phase;
void timer_handler(void) { T_STAT = 1; UART = '0' + phase; ticks++; }
__attribute__((noinline)) void mark_one(void) { phase = 1; }
__attribute__((noinline)) void mark_two(void) { phase = 2; }
/* while (ticks < 6) { mark_one(); mark_two(); }: seven blocks a round (a branch to the next
   instruction splits the head), where the C's loop takes five */
__attribute__((naked, noinline)) void alternate(void)
{
    __asm volatile ("    push {r4, lr}\n"
                    "    ldr r4, =ticks\n"
                    "1:  ldr r0, [r4]\n"
                    "    cmp r0, #6\n"
                    "    bhs 2f\n"
                    "    b 3f\n"
                    "3:  bl mark_one\n"
                    "    bl mark_two\n"
                    "    b 1b\n"
                    "2:  pop {r4, pc}\n"
                    "    .ltorg\n");
}
__attribute__((noreturn)) void reset(void) {
    NVIC_ISER = 1;
    T_LOAD = 700;
    T_CTRL = 3;
    alternate();
    UART = '.';
    for (;;) ;
}
__attribute__((section(".vectors"), used)) void (*const vectors[17])(void) = {
    (void (*)(void))0x20004000, reset, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, timer_handler };
'''

ALTERNATE_C = r'''#include "dread.h"
#define ticks (*(volatile unsigned *)TICKS)
#define phase (*(volatile unsigned *)PHASE)
__attribute__((noinline)) void mark_one(void) { phase = 1; }
__attribute__((noinline)) void mark_two(void) { phase = 2; }
void alternate(void)
{
    while (ticks < 6) {
        mark_one();
        mark_two();
    }
}
'''


def _waits_seen(monkeypatch):
    """A list that gets (image, the wait's block count or "LOOP_MAX") for every wait the loop search
    finds (System._loops), "original" or "rebuilt" by the System's placement."""
    seen = []
    init, real = sysrun.System.__init__, sysrun.System._loops

    def tagged(self, *args, **kwargs):
        init(self, *args, **kwargs)
        self.test_rebuilt = kwargs.get("placement") is not None

    def spy(self, address, n):
        found = real(self, address, n)
        if found:
            seen.append(("rebuilt" if self.test_rebuilt else "original", "LOOP_MAX" if n >= sysrun.LOOP_MAX else n))
        return found
    monkeypatch.setattr(sysrun.System, "__init__", tagged)
    monkeypatch.setattr(sysrun.System, "_loops", spy)
    return seen


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_wait_made_only_of_c_takes_its_interrupt_where_both_images_enter_a_function(tmp_path, monkeypatch):
    """alternate, mark_one and mark_two are rebuilt from C: alternate's loop, which calls the other
    two until the timer has ticked six times, is made only of code outside shared code, its state
    repeating every round, and the handler prints where in the round the tick came (phase). Until
    2026-10-23 the clock stood still there, the loop search took the loop for a wait, and the tick
    came at the block where the search came round to its anchor: a place each image's code puts
    elsewhere (the original's round takes seven blocks, the C's five), and the images printed other
    phases. Now a function entered outside shared code moves the clock (System._entry_units, ENTRY
    units), so the tick comes due at an entry, the same in both images, and the loop search finds no
    wait at all. The C hook and the Python hooks decide alike. The C must make the original's calls:
    compiled so that it inlines mark_one and mark_two, its loop enters neither, and the images
    differ."""
    from dread import fasthooks
    t = _program_target(tmp_path, ALTERNATE, '''
[models.uart]
kind = "uart"
base = 0x40004000

[models.timer]
kind = "timer"
base = 0x40000000
count = 0x0
load = 0x4
ctrl = 0x8
stat = 0xc
rate = 1
irq = 0
periodic = true

[models.nvic]
kind = "nvic"

[scenarios.run]
until = {uart = "."}
limit = 2_000_000
''', image="limit = 0x08000400", compiler='cflags = ["-mcpu=cortex-m3", "-mthumb", "-O2", "-ffunction-sections"]')
    symbols = {line.split()[2]: line.split()[1] for line in (tmp_path / "config" / "symbols.txt").read_text().splitlines()
               if len(line.split()) == 3}
    (tmp_path / "src").mkdir()
    source = ALTERNATE_C.replace("TICKS", symbols["ticks"]).replace("PHASE", symbols["phase"])
    (tmp_path / "src" / "c.c").write_text(source)
    payload = build.build(t, t.image(), src=str(tmp_path / "src"), out=str(tmp_path / "recon"), quiet=True)
    waits = _waits_seen(monkeypatch)

    def run():
        del waits[:]
        a, b = both(t, payload, t.scenarios["run"])
        return (a.how, a.clock, a.log, a.reports), (b.how, b.clock, b.log, b.reports), list(waits)
    original, rebuilt, found = run()
    assert original[0].startswith("until") and rebuilt[0].startswith("until"), (original[0], rebuilt[0])
    assert original[3]["uart"]["sent"] == "112222." and original[3]["interrupts"] == {16: 6}, original[3]
    assert original[2] == rebuilt[2] and original[3] == rebuilt[3] and original[1] == rebuilt[1]
    assert found == [], found
    if fasthooks.library() is not None:
        monkeypatch.setattr(fasthooks, "_LIB", [None])      # as without a C compiler
        assert run() == (original, rebuilt, found)
        monkeypatch.undo()
    # C that calls otherwise than the original (here the compiler inlined both callees into the loop)
    # enters other functions: its clock is not the original's, and the whole image notices
    (tmp_path / "src" / "c.c").write_text(source.replace("__attribute__((noinline)) ", ""))
    payload = build.build(t, t.image(), src=str(tmp_path / "src"), out=str(tmp_path / "inlined"), quiet=True)
    a, b = both(t, payload, t.scenarios["run"])
    assert sysrun.compare(a, b), (a.reports, b.reports)


CRITICAL_FUNCTIONS = r"""
#define PENDSVSET (1u << 28)
static inline void basepri(unsigned v) { __asm volatile ("msr basepri, %0" :: "r" (v) : "memory"); }
__attribute__((noinline)) void touch(void) { later = pendsvs; }
/* a yield in a critical section, as FreeRTOS's vTaskResume makes it: PendSV pended while BASEPRI
   holds it off, taken on the core as soon as the msr lowers BASEPRI */
__attribute__((noinline)) void resume_task(void) {
    basepri(0x80);
    ICSR = PENDSVSET;
    inside = pendsvs;
    basepri(0);
    behind = pendsvs;
}
/* portYIELD with nothing holding PendSV off: taken behind the isb on the core */
__attribute__((noinline)) void yield_now(void) {
    ICSR = PENDSVSET;
    __asm volatile ("dsb\n    isb" ::: "memory");
    inside += pendsvs;
    touch();
}
"""

CRITICAL = r"""
#define UART (*(volatile unsigned *)0x40004000)
#define ICSR (*(volatile unsigned *)0xE000ED04)
#define SHPR3 (*(volatile unsigned *)0xE000ED20)
volatile unsigned pendsvs, inside, behind, later;
void pendsv(void) { pendsvs++; }
""" + CRITICAL_FUNCTIONS + r"""
__attribute__((noreturn)) void reset(void) {
    SHPR3 = 0xFFu << 16;
    resume_task();
    UART = '0' + inside;
    UART = '0' + behind;
    yield_now();
    UART = '0' + inside;
    UART = '0' + later;
    UART = '.';
    for (;;) ;
}
__attribute__((section(".vectors"), used)) void (*const vectors[16])(void) = {
    (void (*)(void))0x20004000, reset, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, pendsv, 0 };
"""


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_an_interrupt_held_off_by_a_mask_that_rebuilt_code_lifts_comes_behind_the_unmask(tmp_path, monkeypatch):
    """resume_task pends PendSV inside a critical section and lowers BASEPRI: on the core PendSV runs
    right behind the msr, so behind sees it ("01" printed, as the image prints with all of it shared).
    With resume_task counted as rebuilt (its code outside shared code) PendSV waited for the next
    block of shared code until 2026-10-23, behind the return ("00", in both images alike: FreeRTOS's
    GenQTest asserted so with the kernel lifted). yield_now pends it with nothing holding it off and
    then, as portYIELD does, runs dsb and isb: the core takes it behind the isb ("22" with all of it
    shared); until 2026-10-23 at touch's entry, the next shared block ("1"). Now both come at the
    block behind the instruction, a place both images share: the emulator ends its translation block
    behind an msr and an isb (Arm.ends_block), the C keeps both instructions, and the pend lies in
    the same block as the msr, so the question first comes up behind it (System._behind_block_end).
    The same with resume_task, yield_now and touch outside shared code, with touch shared, with the
    C hook and without, and with the functions rebuilt from C (their own source compiled again)."""
    from dread import fasthooks
    t = _program_target(tmp_path, CRITICAL, """
[models.uart]
kind = "uart"
base = 0x40004000

[models.nvic]
kind = "nvic"

[scenarios.run]
until = {uart = "."}
limit = 200_000
""", image="limit = 0x08000400", compiler='cflags = ["-mcpu=cortex-m3", "-mthumb", "-O2", "-ffunction-sections"]')
    image = t.image()
    scenario = t.scenarios["run"]

    def sent(built):
        system = sysrun.System(t, image, image.payload(), scenario=scenario, built=built)
        o = system.run(scenario["limit"])
        return o.how.split(":")[0], o.reports["uart"]["sent"], o.reports["interrupts"]

    def outcomes():
        return [sent(built) for built in ((), ("resume_task", "yield_now", "touch"), ("resume_task", "yield_now"))]
    got = outcomes()
    assert got == [("until", "0122.", {14: 2})] * 3, got
    if fasthooks.library() is not None:
        monkeypatch.setattr(fasthooks, "_LIB", [None])
        assert outcomes() == got
        monkeypatch.undo()
    symbols = {line.split()[2]: line.split()[1] for line in (tmp_path / "config" / "symbols.txt").read_text().splitlines()
               if len(line.split()) == 3}
    source = '#include "dread.h"\n#define ICSR (*(volatile unsigned *)0xE000ED04)\n' + "".join(
        "#define %s (*(volatile unsigned *)%s)\n" % (n, symbols[n]) for n in ("pendsvs", "inside", "behind", "later"))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.c").write_text(source + CRITICAL_FUNCTIONS)
    payload = build.build(t, image, src=str(tmp_path / "src"), out=str(tmp_path / "recon"), quiet=True)
    a, b = both(t, payload, scenario)
    assert a.reports == b.reports and a.log == b.log and a.reports["uart"]["sent"] == "0122.", (a.reports, b.reports)


COMPARES = r"""
#define UART (*(volatile unsigned *)0x40004000)
#define ICSR (*(volatile unsigned *)0xE000ED04)
#define SHPR3 (*(volatile unsigned *)0xE000ED20)
volatile unsigned pendsvs, inside, later;
void pendsv(void) { pendsvs++; }
__attribute__((noinline)) void touch(void) { later = pendsvs; }
/* PendSV pended with nothing holding it off and no barrier, then compares whose branches are not
   taken: each ends a block, and the next starts where it ended, behind no instruction at which the
   emulator ends a block */
__attribute__((naked, noinline)) void pend_then_compare(void)
{
    __asm volatile ("    push {r4, lr}\n"
                    "    ldr r0, =0xE000ED04\n"
                    "    mov r1, #0x10000000\n"
                    "    str r1, [r0]\n"
                    "    movs r2, #0\n"
                    "    cmp r2, #1\n    beq 9f\n"
                    "    cmp r2, #2\n    beq 9f\n"
                    "    cmp r2, #3\n    beq 9f\n"
                    "    cmp r2, #4\n    beq 9f\n"
                    "    ldr r0, =pendsvs\n"
                    "    ldr r0, [r0]\n"
                    "    ldr r1, =inside\n"
                    "    str r0, [r1]\n"
                    "    bl touch\n"
                    "9:  pop {r4, pc}\n"
                    "    .ltorg\n");
}
__attribute__((noreturn)) void reset(void) {
    unsigned n;
    SHPR3 = 0xFFu << 16;
    for (n = 0; n < 3; n++)
        pend_then_compare();
    UART = '0' + inside;
    UART = '0' + later;
    UART = '.';
    for (;;) ;
}
__attribute__((section(".vectors"), used)) void (*const vectors[16])(void) = {
    (void (*)(void))0x20004000, reset, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, pendsv, 0 };
"""


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_deferred_interrupt_passes_blocks_that_a_branch_not_taken_ends(tmp_path, monkeypatch):
    """pend_then_compare, outside shared code (counted as rebuilt), pends PendSV with nothing holding
    it off and runs compares whose branches are not taken: each block starts where the one before
    ended, so the run asks whether an instruction there ended it (System._behind_block_end): no, and
    the C hook keeps that answer by address and goes on alone, the interrupt waiting for touch's entry
    ("23": inside read before each PendSV, later behind it). With all of it shared PendSV comes at the
    block behind the store ("33"). The C hook and the Python hooks decide alike, and the C hook asked
    Python about each such block once, the one behind the pend at every pend, which raises a new
    question (a loop of compares and branches handed every block over while an interrupt waited, until
    the C hook kept the answer: FreeRTOS's register-test task, 47 million times a run)."""
    from dread import fasthooks
    t = _program_target(tmp_path, COMPARES, """
[models.uart]
kind = "uart"
base = 0x40004000

[models.nvic]
kind = "nvic"

[scenarios.run]
until = {uart = "."}
limit = 200_000
""", image="limit = 0x08000400", compiler='cflags = ["-mcpu=cortex-m3", "-mthumb", "-O2", "-ffunction-sections"]')
    image = t.image()
    scenario = t.scenarios["run"]
    asked = []
    real = sysrun.System._check_interrupts

    def count(self, uc, common=True, address=None):
        if not common:
            asked.append(address)
        return real(self, uc, common=common, address=address)
    monkeypatch.setattr(sysrun.System, "_check_interrupts", count)

    def outcomes():
        out = []
        for built in ((), ("pend_then_compare",)):
            o = sysrun.System(t, image, image.payload(), scenario=scenario, built=built).run(scenario["limit"])
            out.append((o.how.split(":")[0], o.reports["uart"]["sent"], o.reports["interrupts"], o.clock, o.log))
        return out
    got = outcomes()
    assert [g[:3] for g in got] == [("until", "33.", {14: 3}), ("until", "23.", {14: 3})], [g[:3] for g in got]
    if fasthooks.library() is not None:
        in_c = len(asked)
        first = asked[0]                # the block behind the pend: a new question at every pend
        assert asked.count(first) == 3 and all(asked.count(a) == 1 for a in set(asked) - {first}), asked
        monkeypatch.setattr(fasthooks, "_LIB", [None])
        del asked[:]
        assert outcomes() == got
        assert len(asked) > in_c, (len(asked), in_c)


RISCV_PLACES_FUNCTIONS = r"""
__attribute__((noinline)) void mark_one(void) { phase = 1; }
__attribute__((noinline)) void mark_two(void) { phase = 2; }
/* the software interrupt pended while MIE holds it off, taken on the core as soon as csrs sets MIE */
__attribute__((noinline)) void raise_and_lift(void) {
    __asm volatile ("csrc mstatus, %0" :: "r"(8) : "memory");
    MSIP = 1;
    inside = swis;
    __asm volatile ("csrs mstatus, %0" :: "r"(8) : "memory");
    behind = swis;
}
"""

RISCV_PLACES = r"""
#define THR (*(volatile unsigned char *)0x10000000)
#define LSR (*(volatile unsigned char *)0x10000005)
#define MSIP (*(volatile unsigned *)0x02000000)
#define MTIMECMP_LO (*(volatile unsigned *)0x02004000)
#define MTIMECMP_HI (*(volatile unsigned *)0x02004004)
#define MTIME_LO (*(volatile unsigned *)0x0200bff8)
volatile unsigned ticks, phase, swis, inside, behind;
__attribute__((noinline)) void put(int c) { while (!(LSR & 0x20)) ; THR = c; }
__attribute__((interrupt("machine"), aligned(4))) void trap(void) {     /* mtvec, direct mode */
    unsigned cause;
    __asm volatile ("csrr %0, mcause" : "=r"(cause));
    if (cause == 0x80000003u) {
        MSIP = 0;
        swis++;
    } else {
        MTIMECMP_LO = MTIME_LO + 700;
        put('0' + phase);
        ticks++;
    }
}
""" + RISCV_PLACES_FUNCTIONS + r"""
/* while (ticks < 6) { mark_one(); mark_two(); }: seven blocks a round (a jump to the next
   instruction splits the head), where the C's loop takes five */
__asm__(".section .text.alternate\n.global alternate\n.type alternate, @function\nalternate:\n"
        " addi sp, sp, -16\n sw ra, 12(sp)\n"
        "1: lui a5, %hi(ticks)\n lw a5, %lo(ticks)(a5)\n li a4, 6\n bgeu a5, a4, 2f\n j 3f\n"
        "3: call mark_one\n call mark_two\n j 1b\n"
        "2: lw ra, 12(sp)\n addi sp, sp, 16\n ret\n.size alternate, . - alternate\n");
void alternate(void);
__attribute__((noreturn)) void cmain(void) {
    __asm volatile ("csrw mtvec, %0" :: "r"((unsigned)trap));
    MTIMECMP_HI = 0xffffffff;
    __asm volatile ("csrs mie, %0" :: "r"(8));
    __asm volatile ("csrs mstatus, %0" :: "r"(8));
    raise_and_lift();
    put('0' + inside);
    put('0' + behind);
    MTIMECMP_LO = MTIME_LO + 700;
    MTIMECMP_HI = 0;
    __asm volatile ("csrs mie, %0" :: "r"(0x80));
    alternate();
    put('.');
    for (;;) ;
}
__asm__(".section .text.start\n.global _start\n.type _start, @function\n_start:\n li sp, 0x80090000\n j cmain\n"
        ".size _start, . - _start\n");
"""

RISCV_PLACES_C = r"""#include "dread.h"
#define MSIP (*(volatile unsigned *)0x02000000)
#define ticks (*(volatile unsigned *)TICKS)
#define phase (*(volatile unsigned *)PHASE)
#define swis (*(volatile unsigned *)SWIS)
#define inside (*(volatile unsigned *)INSIDE)
#define behind (*(volatile unsigned *)BEHIND)
""" + RISCV_PLACES_FUNCTIONS + r"""
void alternate(void)
{
    while (ticks < 6) {
        mark_one();
        mark_two();
    }
}
"""


@needs_riscv_gcc
def test_riscv_interrupts_come_where_both_images_share_the_place(tmp_path, monkeypatch):
    """The two cases of the Cortex-M tests above on RISC-V, raise_and_lift, mark_one, mark_two and
    alternate rebuilt from C. raise_and_lift sets msip while mstatus.MIE holds the software interrupt
    off and sets MIE again: on the core the trap comes right behind the csrs, so behind sees it ("01",
    as with all of it shared; until 2026-10-23 "00" with raise_and_lift outside shared code, the trap
    waiting for the next shared block); the emulator ends its block behind every CSR access
    (Riscv.ends_block), so the next block starts behind the csrs in both images. alternate's loop
    calls mark_one and mark_two until the timer has ticked six times, and the trap handler prints the
    phase it interrupted: the original's round (assembly) takes seven blocks, the C's five; the tick
    comes due at a function's entry, the same in both images, and no wait is found. The C hook and
    the Python hooks decide alike."""
    import subprocess
    from dread import elfimport, fasthooks
    from dread.target import Target
    (tmp_path / "t.c").write_text(RISCV_PLACES)
    (tmp_path / "t.ld").write_text("SECTIONS { . = 0x80000000; .text : { *(.text.start) *(.text*) }\n"
                                   " . = 0x80080000; .bss : { *(.sbss*) *(.bss*) } }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run([RISCV_CROSS + "gcc", "-march=" + RISCV_MARCH, "-mabi=ilp32", "-O2", "-nostdlib",
                    "-ffunction-sections", "-T", str(tmp_path / "t.ld"), "-e", "_start", "-o", elf, str(tmp_path / "t.c")],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml('''[target]
name = "places"
[arch]
name = "riscv"
isa = "rv32"
extensions = "imac"
[memory]
regions = [{name = "rom", base = 0x80000000, size = 0x10000, kind = "rom"},
           {name = "ram", base = 0x80080000, size = 0x10000}]
io = [[0x02000000, 0x02010000], [0x10000000, 0x10001000]]
stack = {top = 0x80090000, size = 0x1000}
sentinel = 0x7fff0000
[image.main]
original = "original/t.bin"
base = 0x80000000
entry = "_start"
limit = 0x80001000
[compiler]
cross = "riscv64-elf-"
cflags = ["-march=rv32imac_zicsr", "-mabi=ilp32", "-O2", "-ffunction-sections"]
[models.uart]
kind = "uart"
base = 0x10000000
data = 0x0
status = 0x5
tx_ready = 0x20
rx_ready = 0x1
size = 0x8
[models.clint]
kind = "clint"
base = 0x02000000
rate = 1
[scenarios.run]
until = {uart = "."}
limit = 200_000
'''))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    symbols = {line.split()[2]: line.split()[1] for line in (tmp_path / "config" / "symbols.txt").read_text().splitlines()
               if len(line.split()) == 3}
    source = RISCV_PLACES_C
    for name in ("ticks", "phase", "swis", "inside", "behind"):
        source = source.replace(name.upper(), symbols[name])
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.c").write_text(source)
    t = Target.load(str(tmp_path))
    payload = build.build(t, t.image(), src=str(tmp_path / "src"), out=str(tmp_path / "recon"), quiet=True)
    image = t.image()
    scenario = t.scenarios["run"]
    alone = sysrun.run_image(t, image, image.payload(), scenario)
    waits = _waits_seen(monkeypatch)

    def run():
        del waits[:]
        a, b = both(t, payload, scenario)
        return (a.how, a.clock, a.log, a.reports), (b.how, b.clock, b.log, b.reports), list(waits)
    original, rebuilt, found = run()
    assert alone.reports["uart"]["sent"].startswith("01"), (alone.how, alone.reports)
    assert original[0].startswith("until") and rebuilt[0].startswith("until"), (original[0], rebuilt[0])
    assert original[3]["uart"]["sent"] == "01111111." and original[3]["interrupts"] == {3: 1, 7: 6}, original[3]
    assert original[2] == rebuilt[2] and original[3] == rebuilt[3] and original[1] == rebuilt[1]
    assert found == [], found
    if fasthooks.library() is not None:
        monkeypatch.setattr(fasthooks, "_LIB", [None])      # as without a C compiler
        assert run() == (original, rebuilt, found)


def test_the_c_block_hook_gives_the_python_hooks_outcome(monkeypatch):
    """On Unicorn the blocks that only move the clock are done in C (dread.fasthooks, dread_sysblock),
    and the blocks of a stretch outside shared code that decide nothing: every outcome is the Python
    hook's (how, clock, device log, reports, functions entered and their order), and the C hook did
    most of the blocks. The second round counts all of gizmo but the timer's interrupt path and
    uart_putc as rebuilt (outside shared code), so that most blocks lie in stretches and every key
    waits in main's idle loop outside it."""
    from conftest import gizmo_dir
    from dread import fasthooks
    from dread.program import Program
    from dread.target import Target
    if fasthooks.library() is None:
        pytest.skip("the C hooks did not build (or DREAD_FASTHOOKS=0)")
    t = Target.load(gizmo_dir("m4"))
    image = t.image()
    keep = ("Reset_Handler", "TIMER_IRQHandler", "timer_irq", "uart_putc")
    rebuilt = [fn.name for fn in Program(t, image).functions if fn.name not in keep]

    def outcomes():
        out, fast = {}, 0
        for built in ((), rebuilt):
            for name, scenario in sorted(t.scenarios.items()):
                scenario = dict(scenario, _name=name)
                system = sysrun.System(t, image, image.payload(), scenario=scenario, built=built)
                o = system.run(int(scenario.get("limit", sysrun.LIMIT)))
                out[name, len(built)] = (o.how, o.clock, o.log, o.reports, o.entered, o.order, o.calls, o.where)
                fast += system.st.fast if system.fast is not None else 0
        return out, fast
    with_c, done_in_c = outcomes()
    monkeypatch.setattr(fasthooks, "_LIB", [None])          # as without a C compiler
    without, none = outcomes()
    assert with_c == without
    assert none == 0 and done_in_c > 1000, done_in_c
    assert all(o[0].startswith("until") for o in with_c.values()), [o[0] for o in with_c.values()]


def test_the_c_hooks_device_reads_give_the_python_hooks_outcome(target, monkeypatch):
    """On Unicorn a read of a plain register (a model's `plain` ranges: no side effect, a value only
    a call into the models changes; or a register no model owns) is done in C from the value the
    model gave last, which the run asks for again after every such call (dread_sysio,
    System._refresh_plain); every other access goes to System._io. Every scenario's outcome is the
    Python hooks' (how, clock, device log with its counted repeats, reports, functions entered), with
    all of gizmo shared and with all of it outside shared code (every block in a stretch, which a C
    read ends as a Python one does), and the C hook did most of the reads. The UART's data register
    (a read takes a byte) is not plain: the scenarios' input arrives alike."""
    from dread import fasthooks
    from dread.program import Program
    if fasthooks.library() is None:
        pytest.skip("the C hooks did not build (or DREAD_FASTHOOKS=0)")
    image = target.image()
    every = [fn.name for fn in Program(target, image).functions]

    def outcomes():
        out, in_c, reads = {}, 0, 0
        for built in ((), every):
            for name, scenario in sorted(target.scenarios.items()):
                scenario = dict(scenario, _name=name)
                system = sysrun.System(target, image, image.payload(), scenario=scenario, built=built)
                o = system.run(int(scenario.get("limit", sysrun.LIMIT)))
                out[name, len(built)] = (o.how, o.clock, o.log, o.reports, o.entered, o.order, o.calls, o.where)
                in_c += system.st.io_fast if system.fast is not None else 0
                reads += sum(e[5] for e in o.log if e[0] == "read")
        return out, in_c, reads
    with_c, in_c, reads = outcomes()
    monkeypatch.setattr(fasthooks, "_LIB", [None])          # as without a C compiler
    without, none, _ = outcomes()
    assert with_c == without
    assert none == 0 and in_c > reads // 2, (in_c, reads)
    assert all(o[0].startswith("until") for o in with_c.values()), [o[0] for o in with_c.values()]


def test_a_model_without_plain_registers_is_asked_at_every_read(monkeypatch):
    """Only the registers a model names plain (dread.models.Model.plain) are read in C, and only
    those it names idempotent are written in C (Model.idempotent); the default is none: a model of
    the target's own is asked at every access, as before. gizmo with its models' tables emptied: no
    access in C, the same outcome."""
    from conftest import gizmo_dir
    from dread import fasthooks, models
    from dread.target import Target
    if fasthooks.library() is None:
        pytest.skip("the C hooks did not build (or DREAD_FASTHOOKS=0)")
    t = Target.load(gizmo_dir("m3"))
    image = t.image()

    def run():
        system = sysrun.System(t, image, image.payload(), scenario=t.scenarios["keys"])
        o = system.run(int(t.scenarios["keys"]["limit"]))
        return (o.how, o.clock, o.log, o.reports), system.st.io_fast if system.fast is not None else 0
    a, in_c = run()
    create = models.create

    def no_plain(*args):
        m = create(*args)
        m.plain, m.idempotent = [], []
        return m
    monkeypatch.setattr(models, "create", no_plain)
    b, none = run()
    assert a == b and none == 0 and in_c > 0, (none, in_c)


READS = r'''
#define UART_DATA (*(volatile unsigned *)0x40004000)
#define UART_STAT (*(volatile unsigned *)0x40004004)
#define T_COUNT (*(volatile unsigned *)0x40000000)
#define T_LOAD (*(volatile unsigned *)0x40000004)
#define T_CTRL (*(volatile unsigned *)0x40000008)
#define T_STAT (*(volatile unsigned *)0x4000000c)
#define SYST_CSR (*(volatile unsigned *)0xE000E010)
#define SYST_RVR (*(volatile unsigned *)0xE000E014)
#define SYST_CVR (*(volatile unsigned *)0xE000E018)
#define ICSR (*(volatile unsigned *)0xE000ED04)
__attribute__((noinline)) void poll(void) { while (!(UART_STAT & 2)) ; }
__attribute__((noreturn)) void reset(void) {
    unsigned n;
    volatile unsigned i;
    while (!(UART_STAT & 2)) ;                  /* 'a' arrives between chunks */
    UART_DATA = UART_DATA;                      /* a read takes it */
    __asm volatile ("cpsid i" ::: "memory");
    ICSR = 1u << 28;                            /* PendSV pending, then not: ICSR reads both */
    UART_DATA = '0' + (ICSR >> 28 & 1);
    ICSR = 1u << 27;
    UART_DATA = '0' + (ICSR >> 28 & 1);
    __asm volatile ("cpsie i" ::: "memory");
    SYST_RVR = 3000; SYST_CVR = 0; SYST_CSR = 1;
    while (SYST_CVR > 1000) ;                   /* the current value moves with the clock */
    while (!(SYST_CSR & (1u << 16))) ;          /* COUNTFLAG: a read clears it */
    UART_DATA = '0' + (SYST_CSR >> 16 & 1);
    SYST_CSR = 0;
    T_LOAD = 300; T_CTRL = 1;
    while (T_COUNT > 100) ;                     /* the count moves with the clock */
    T_CTRL = 0;
    UART_DATA = 'T';
    poll();                                     /* 'b' arrives while poll reads */
    UART_DATA = UART_DATA;
    for (n = 0; n < 200; n++) {                 /* a read of the timer's count (not plain) between */
        (void)UART_STAT;                        /* two of the C hook's */
        (void)T_COUNT;
    }
    for (n = 0; n < 20000; n++) {               /* nothing scheduled, and every read a new entry */
        (void)T_STAT;
        (void)UART_STAT;
    }
    for (i = 0; i < 1000; i++)                  /* no access, nothing new: not yet quiet */
        ;
    for (;;)                                    /* the last read again and again, no new entry: */
        (void)UART_STAT;                        /* quiet from this loop's first block on, "polls" */
}
__attribute__((section(".vectors"), used)) void (*const vectors[16])(void) = {
    (void (*)(void))0x20004000, reset };
'''


A32_TARGET = """
[target]
name = "program-a32"

[arch]
name = "arm"
isa = "a32"
profile = "a"

[memory]
regions = [
    {name = "flash", base = 0x0, size = 0x100000, kind = "rom"},
    {name = "ram", base = 0x40000000, size = 0x10000},
    {name = "scratch", base = 0x40100000, size = 0x20000},
]
io = [[0x80000000, 0x80010000]]
stack = {top = 0x40010000, size = 0x2000}
sentinel = 0x0fff0000
scratch = {base = 0x40100000, size = 0x20000}
"""


def _program_target(tmp_path, source, tables, profile="m", image="", compiler=""):
    """A target in tmp_path of a program of the test's own, `source` built for a Cortex-M3 (profile
    "m": flash at 0x08000000, its vector table first, its entry `reset`) or an ARM7-style core in ARM
    state (profile "a": flash at 0x20000, its exception vectors first and its entry there, RAM at
    0x40000000, peripherals from 0x80000000); `tables`: its models and scenarios; `image` and
    `compiler`: more lines for [image.main] and [compiler] (a limit, the C's cflags)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.target import Target
    flash, ram, entry = (0x08000000, 0x20000000, "reset") if profile == "m" else (0x20000, 0x40000000, "0x20000")
    flags = ["-mcpu=cortex-m3", "-mthumb"] if profile == "m" else ["-march=armv4t", "-marm"]
    (tmp_path / "t.c").write_text(source)
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x%x, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x%x, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { KEEP(*(.vectors)) *(.text*) *(.rodata*) } > FLASH\n"
                                   " .bss : { *(.bss*) *(COMMON) } > RAM }\n" % (flash, ram))
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc"] + flags + ["-O2", "-nostdlib", "-ffunction-sections", "-T",
                                                    str(tmp_path / "t.ld"), "-e", entry, "-o", elf,
                                                    str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text((TARGET if profile == "m" else A32_TARGET) + '''
[image.main]
original = "original/t.bin"
base = 0x%x
entry = %s
vectors = 0x%x
%s
[compiler]
cross = "arm-none-eabi-"
%s
''' % (flash, '"reset"' if profile == "m" else entry, flash, image, compiler) + tables)
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    return Target.load(str(tmp_path))


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_reads_in_c_follow_the_models(tmp_path, monkeypatch):
    """The C hook's reads (dread_sysio) against the Python hooks on a program that tries them: a poll
    of the UART's status while input arrives between chunks, ICSR read behind the writes that change
    it, SysTick's current value and the timer's count, which move with the clock, COUNTFLAG, which a
    read clears (none of the three plain), a poll in a function outside shared code (built: a stretch
    that each read ends, and a read that makes an event due), reads of the count, done in Python,
    between reads done in C (the log keeps their order and counts), alternating reads with nothing
    scheduled (a new log entry each: not quiet, though longer than QUIET), a computation, and a poll
    of the last register read, which ends the run as "polls" QUIET units after the loop's first
    block (the clock of a new entry made in C, then of a new block, kept in that order). The same
    outcome both ways, with poll shared and rebuilt, with the run's chunks and QUIET as they are and
    with chunks of 13 blocks (the quiet rule is asked at every chunk's end: there it sees a clock of
    the alternating reads or of the computation's blocks gone wrong); most reads done in C."""
    from dread import fasthooks
    if fasthooks.library() is None:
        pytest.skip("the C hooks did not build (or DREAD_FASTHOOKS=0)")
    t = _program_target(tmp_path, READS, '''
[models.uart]
kind = "uart"
base = 0x40004000

[models.timer]
kind = "timer"
base = 0x40000000
count = 0x0
load = 0x4
ctrl = 0x8
stat = 0xc
rate = 1

[models.nvic]
kind = "nvic"
systick_rate = 1

[scenarios.reads]
input = [[1_000, "uart", "a"], [40_000, "uart", "b"]]
limit = 2_000_000
''')
    image = t.image()
    scenario = t.scenarios["reads"]

    settings = ((sysrun.CHUNK, sysrun.QUIET), (13, 50_000))

    def outcomes():
        out, in_c = {}, 0
        for chunk, quiet in settings:
            monkeypatch.setattr(sysrun, "CHUNK", chunk)
            monkeypatch.setattr(sysrun, "QUIET", quiet)
            for built in ((), ("poll",)):
                system = sysrun.System(t, image, image.payload(), scenario=scenario, built=built)
                o = system.run(scenario["limit"])
                out[chunk, built] = (o.how, o.clock, o.log, o.reports, o.entered, o.order)
                in_c += system.st.io_fast if system.fast is not None else 0
        return out, in_c
    with_c, in_c = outcomes()
    monkeypatch.setattr(fasthooks, "_LIB", [None])
    without, _ = outcomes()
    assert with_c == without
    for how, clock, log, reports, _, _ in with_c.values():
        assert how.startswith("polls: register 0x40004004") and reports["uart"]["sent"] == "a100Tb", (how, reports)
        assert clock > 360_000, (how, clock)            # (not quiet in the alternating reads)
    reads = sum(e[5] for o in with_c.values() for e in o[2] if e[0] == "read")
    assert in_c > reads // 2, (in_c, reads)


WRITES = r"""
#define ICSR (*(volatile unsigned *)0xE000ED04)
#define SHPR3 (*(volatile unsigned *)0xE000ED20)
#define ISER0 (*(volatile unsigned *)0xE000E100)
#define SYST_CSR (*(volatile unsigned *)0xE000E010)
#define SYST_RVR (*(volatile unsigned *)0xE000E014)
#define SYST_CVR (*(volatile unsigned *)0xE000E018)
#define UART_DATA (*(volatile unsigned *)0x40004000)
#define UART_STAT (*(volatile unsigned *)0x40004004)
#define T_LOAD (*(volatile unsigned *)0x40000004)
#define T_CTRL (*(volatile unsigned *)0x40000008)
#define T_STAT (*(volatile unsigned *)0x4000000c)
#define SPARE (*(volatile unsigned *)0x40002000)
#define PENDSVSET (1u << 28)
#define PENDSVCLR (1u << 27)
volatile unsigned pendsvs, ticks, timers;
__attribute__((used)) volatile unsigned stretched;
static inline void basepri(unsigned v) { __asm volatile ("msr basepri, %0" :: "r" (v) : "memory"); }
void pendsv(void) { pendsvs++; }
void systick(void) { ticks++; }
void timer_irq(void) { timers++; T_STAT = 1; }
__attribute__((noinline)) void yields(void) {
    unsigned n;
    for (n = 0; n < 200; n++) {                 /* portYIELD in critical sections: PendSV set again */
        basepri(0x80);                          /* and again while it is pending, masked, with ICSR */
        ICSR = PENDSVSET;                       /* read between */
        (void)ICSR; (void)ICSR; (void)ICSR;
        if (n % 50 == 49)
            basepri(0);                         /* taken: the next write pends it anew */
    }
    basepri(0x80);
    for (n = 0; n < 3000; n++)                  /* writes alone while it waits, masked: no spin */
        ICSR = PENDSVSET;
    basepri(0);
}
__attribute__((noinline)) unsigned flips(void) {
    unsigned n, seen = 0;
    __asm volatile ("cpsid i" ::: "memory");
    for (n = 0; n < 100; n++) {                 /* writes that change what ICSR reads, each repeated */
        ICSR = PENDSVSET;
        ICSR = PENDSVSET;
        seen += ICSR >> 28 & 1;
        ICSR = PENDSVCLR;
        ICSR = PENDSVCLR;
        seen += ICSR >> 28 & 1;
    }
    __asm volatile ("cpsie i" ::: "memory");
    return seen;
}
__attribute__((noinline)) void restarts(void) {
    unsigned n;
    for (n = 0; n < 50; n++)
        SYST_RVR = 1000;
    SYST_CVR = 0;
    SYST_CSR = 7;                               /* counts from now, its interrupt at the reload */
    for (n = 0; n < 300; n++)
        SYST_CVR = 0;                           /* each write starts the count anew: not idempotent */
    for (n = 0; n < 300; n++)
        SYST_CSR = 7;                           /* so does each of these */
    UART_DATA = '0' + ticks;                    /* no tick yet */
    for (n = 0; n < 300; n++)
        SYST_RVR = 1000;                        /* ticks come due among repeated writes */
    SYST_CSR = 0;
    UART_DATA = '0' + ticks;
}
__attribute__((noinline)) void lines(void) {
    unsigned n;
    for (n = 0; n < 50; n++)
        T_LOAD = 400;
    __asm volatile ("cpsid i" ::: "memory");
    T_CTRL = 3;                                 /* run, interrupt: the line rises in 400 units */
    for (n = 0; n < 100; n++)
        ISER0 = 1u << 3;                        /* the first enables it; the line rises meanwhile */
    __asm volatile ("cpsie i" ::: "memory");    /* taken */
    T_CTRL = 0;
}
__attribute__((noinline)) unsigned spare(void) {
    unsigned n, sum = 0;
    for (n = 0; n < 100; n++) {                 /* a register no model owns: memory (bit 31 set */
        SPARE = n << 25;                        /* from 64 on) */
        SPARE = n << 25;
        sum += SPARE >> 25;
    }
    for (n = 0; n < 2; n++)                     /* narrow stores of a wider register */
        __asm volatile ("strb %1, [%0]\n    strh %1, [%0]" :: "r" (&SPARE), "r" (0x87654321u) : "memory");
    return sum + SPARE;
}
__attribute__((noinline)) void stretch(void) {  /* a loop that only writes, its registers alike at */
    __asm volatile (                            /* every round: a stretch, which each write ends */
        "    ldr r0, =0xE000ED04\n"
        "    mov r1, #0x08000000\n"
        "    ldr r2, =stretched\n"
        "    movw r12, #3000\n"
        "    mov r3, #0\n"
        "1:  str r1, [r0]\n"
        "    ldr r3, [r2]\n"
        "    adds r3, r3, #1\n"
        "    str r3, [r2]\n"
        "    cmp r3, r12\n"
        "    mov r3, #0\n"
        "    bne 1b\n"
        ::: "r0", "r1", "r2", "r3", "r12", "cc", "memory");
}
__attribute__((noreturn)) void reset(void) {
    unsigned n;
    SHPR3 = 0xFFu << 16;                        /* PendSV the lowest priority */
    yields();
    UART_DATA = '0' + pendsvs;
    UART_DATA = '0' + flips() / 25;
    restarts();
    lines();
    UART_DATA = '0' + timers;
    UART_DATA = 'a' + spare() % 26;
    UART_DATA = 'x';                            /* the same byte twice: sent twice */
    UART_DATA = 'x';
    for (n = 0; n < 20000; n++)                 /* writes, nothing scheduled, longer than QUIET: */
        UART_STAT = 0;                          /* each a new log entry, not quiet */
    stretch();
    for (n = 0; n < 50; n++)
        UART_STAT = 0;
    for (;;)                                    /* no access: waits */
        ;
}
__attribute__((section(".vectors"), used)) void (*const vectors[20])(void) = {
    (void (*)(void))0x20004000, reset, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, pendsv, systick, 0, 0, 0, timer_irq };
"""


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_writes_in_c_follow_the_models(tmp_path, monkeypatch):
    """The C hook's writes (dread_sysio: a write repeated with nothing called in the models since, to
    a register its model calls idempotent, and a write no model owns) against the Python hooks on a
    program that tries them: PendSV set again and again while it is pending, masked by BASEPRI, with
    ICSR read between (FreeRTOS's portYIELD; a read behind a write in C is a new entry), taken when
    the mask drops (with yields outside shared code too, behind the msr: since 2026-10-23 five times
    either way, before only once, at the first shared block), after which the same write pends it
    anew; writes alone while it waits masked,
    longer than SPIN (the spin rule sees each one); set and clear in turn, each repeated, ICSR read
    between; SysTick's CVR written again and again (not idempotent: each write starts the count
    anew, so no tick comes), then RVR (idempotent) while ticks come due; a timer's line enabled with
    ISER again and again while it rises, masked; a register no model owns, written and read back; the
    same byte sent twice; writes longer than QUIET with nothing scheduled (each a new entry: not
    quiet); a loop outside shared code that only writes, its registers alike each round (each write
    ends the stretch: no wait); the run ends as "waits", its last access a write in C. The same
    outcome both ways, with yields and stretch shared and rebuilt, with the run's chunks and QUIET as
    they are, with chunks of 13 blocks, and with chunks of 5,000 and a QUIET of 20,000 (the quiet
    rule then looks at a chunk's end inside the long writes, thousands of writes in C after the
    chunk's first, which is done in Python); most writes done in C. With the UART's data register declared idempotent, wrongly: the run repeats no
    write whose model noted something (the first 'x' was sent through note), so the second 'x' is
    sent as well. With no model declaring anything: only the writes no model owns are done in C."""
    from dread import fasthooks, models
    if fasthooks.library() is None:
        pytest.skip("the C hooks did not build (or DREAD_FASTHOOKS=0)")
    t = _program_target(tmp_path, WRITES, """
[models.uart]
kind = "uart"
base = 0x40004000

[models.timer]
kind = "timer"
base = 0x40000000
count = 0x0
load = 0x4
ctrl = 0x8
stat = 0xc
rate = 1
irq = 3

[models.nvic]
kind = "nvic"
systick_rate = 1

[scenarios.writes]
limit = 3_000_000
""")
    image = t.image()
    scenario = t.scenarios["writes"]
    monkeypatch.setattr(sysrun, "SPIN", 20_000)
    settings = ((sysrun.CHUNK, sysrun.QUIET), (13, 50_000), (5_000, 20_000))

    def outcomes():
        out, in_c = {}, 0
        for chunk, quiet in settings:
            monkeypatch.setattr(sysrun, "CHUNK", chunk)
            monkeypatch.setattr(sysrun, "QUIET", quiet)
            for built in ((), ("yields", "stretch")):
                system = sysrun.System(t, image, image.payload(), scenario=scenario, built=built)
                o = system.run(scenario["limit"])
                out[chunk, built] = (o.how, o.clock, o.log, o.reports, o.entered, o.order)
                in_c += system.st.writes_fast if system.fast is not None else 0
        return out, in_c
    with_c, in_c = outcomes()
    create = models.create

    def data_idempotent(*args):
        m = create(*args)
        if m.kind == "uart":
            m.idempotent = list(m.ranges)
        return m
    monkeypatch.setattr(models, "create", data_idempotent)
    wrong, _ = outcomes()

    def nothing_declared(*args):
        m = create(*args)
        m.plain, m.idempotent = [], []
        return m
    monkeypatch.setattr(models, "create", nothing_declared)
    undeclared, memory_writes = outcomes()
    monkeypatch.setattr(models, "create", create)
    monkeypatch.setattr(fasthooks, "_LIB", [None])
    without, _ = outcomes()
    assert with_c == without and wrong == without and undeclared == without
    for (chunk, built), (how, clock, log, reports, _, _) in with_c.items():
        assert how.startswith("waits: no device access for"), (how, reports)
        assert reports["uart"]["sent"] == "54041zxx", reports
    writes = sum(1 for o in with_c.values() for e in o[2] if e[0] == "write")
    unowned = sum(1 for o in with_c.values() for e in o[2] if e[0] == "write" and e[1] == "-")
    assert in_c > writes * 3 // 4 and 0 < memory_writes < unowned, (in_c, writes, memory_writes, unowned)


WRITES_A32 = r"""
#define UART_DATA (*(volatile unsigned *)0x80000000)
#define UART_SPARE (*(volatile unsigned *)0x80000008)
#define T_LOAD (*(volatile unsigned *)0x80001004)
#define T_CTRL (*(volatile unsigned *)0x80001008)
#define T_STAT (*(volatile unsigned *)0x8000100c)
#define INTC_ENABLE (*(volatile unsigned *)0x80002004)
#define INTC_ACK (*(volatile unsigned *)0x80002008)
#define GPIO_IN (*(volatile unsigned *)0x80003000)
#define GPIO_OUT (*(volatile unsigned *)0x80003004)
__asm__(
"    .section .vectors, \"ax\"\n"
"    .global _start\n"
"_start:\n"
"    b reset\n"
"    b .\n"
"    b .\n"
"    b .\n"
"    b .\n"
"    b .\n"
"    b irq_entry\n"
"    b .\n"
"    .text\n"
"reset:\n"
"    msr cpsr_c, #0xD2\n"                       /* IRQ mode, masked: its stack */
"    ldr sp, =0x4000f800\n"
"    msr cpsr_c, #0xD3\n"                       /* supervisor mode, masked */
"    ldr sp, =0x40010000\n"
"    bl main\n"
"irq_entry:\n"
"    sub lr, lr, #4\n"
"    stmfd sp!, {r0-r3, r12, lr}\n"
"    bl irq\n"
"    ldmfd sp!, {r0-r3, r12, pc}^\n");
volatile unsigned irqs;
void irq(void) {
    irqs++;
    T_STAT = 1;                                 /* the line falls */
    INTC_ACK = 1;                               /* acknowledged, and again */
    INTC_ACK = 1;
}
__attribute__((noinline)) void enables(void) {
    unsigned n;
    T_LOAD = 300;
    T_CTRL = 3;                                 /* run, interrupt: the line rises every 300 units */
    for (n = 0; n < 200; n++)
        INTC_ENABLE = 1;                        /* the first enables it; the line rises, masked */
    __asm volatile ("msr cpsr_c, #0x53" ::: "memory");     /* unmasked: taken (rebuilt too: */
    for (n = 0; n < 100; n++)                   /* behind the msr, which ends its block) */
        INTC_ENABLE = 1;                        /* and it comes again among these (rebuilt: at the */
                                                /* return, the next place both images share) */
    T_CTRL = 0;
}
__attribute__((noinline)) unsigned pins(void) {
    unsigned n, sum = 0;
    for (n = 0; n < 100; n++)
        GPIO_IN = 5;                            /* the input register leaves a write alone */
    for (n = 0; n < 100; n++) {
        UART_SPARE = n;                         /* a register the UART leaves to memory */
        UART_SPARE = n;
        sum += UART_SPARE + GPIO_IN;
    }
    GPIO_OUT = 1;                               /* noted each time */
    GPIO_OUT = 1;
    return sum;
}
int main(void) {
    enables();
    UART_DATA = '0' + irqs;
    UART_DATA = 'a' + pins() % 26;
    for (;;)
        ;
}
"""


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_writes_in_c_follow_the_models_on_the_a_profile(tmp_path, monkeypatch):
    """The C hook's writes against the Python hooks on an ARM-state program with the A profile's
    models: the interrupt controller's enable written again and again while the timer's line rises,
    the core's IRQ masked (the C hook raises no interrupt question behind a repeat; the block hook
    asks again when CPSR's I bit drops), then unmasked, the line rising again among the repeats; its
    acknowledge written twice in the handler; gpio's input register, which leaves a write alone, and
    its output, noted at every write; a register the UART leaves to memory, written and read back.
    The same outcome both ways, with enables shared and rebuilt, with the run's chunks and with
    chunks of 13 blocks; most writes done in C."""
    from dread import fasthooks
    if fasthooks.library() is None:
        pytest.skip("the C hooks did not build (or DREAD_FASTHOOKS=0)")
    t = _program_target(tmp_path, WRITES_A32, """
[models.uart]
kind = "uart"
base = 0x80000000

[models.timer]
kind = "timer"
base = 0x80001000
rate = 1
irq = 0

[models.intc]
kind = "intc"
base = 0x80002000

[models.gpio]
kind = "gpio"
base = 0x80003000

[scenarios.writes]
limit = 1_000_000
""", profile="a")
    image = t.image()
    scenario = t.scenarios["writes"]
    chunks = (sysrun.CHUNK, 13)

    def outcomes():
        out, in_c = {}, 0
        for chunk in chunks:
            monkeypatch.setattr(sysrun, "CHUNK", chunk)
            for built in ((), ("enables",)):
                system = sysrun.System(t, image, image.payload(), scenario=scenario, built=built)
                o = system.run(scenario["limit"])
                out[chunk, built] = (o.how, o.clock, o.log, o.reports, o.entered, o.order)
                in_c += system.st.writes_fast if system.fast is not None else 0
        return out, in_c
    with_c, in_c = outcomes()
    monkeypatch.setattr(fasthooks, "_LIB", [None])
    without, _ = outcomes()
    assert with_c == without
    for (chunk, built), (how, clock, log, reports, _, _) in with_c.items():
        assert how.startswith("waits") and reports["uart"]["sent"][1:] == "k", (how, reports)
        assert reports["interrupts"][0] == (2 if built else 11), reports
        assert [e for e in log if e[0] == "gpio"] == [("gpio", "pins", 1)] * 2, log[-8:]
    writes = sum(1 for o in with_c.values() for e in o[2] if e[0] == "write")
    assert in_c > writes * 3 // 4, (in_c, writes)


def test_a_new_block_behind_reads_in_c_keeps_its_clock():
    """The clock of the last new log entry or block (System.last_new, for the quiet rule) comes from
    the C hook's reads when they made an entry, and from a new block or an access done here, in the
    order they happened: a new block behind reads in C whose entries are not taken yet takes them
    first and keeps its own clock (else the next take put the reads' older clock back, and a run
    could end as quiet early, at a chunk's end that happened to fall in between)."""
    from conftest import gizmo_dir
    from dread.target import Target
    t = Target.load(gizmo_dir("m3"))
    image = t.image()
    system = sysrun.System(t, image, image.payload(), scenario=t.scenarios["boot"])
    if system.fast is None:
        pytest.skip("the C hooks did not build")
    st = system.st
    system.last_new, st.clock, st.chunk = 100, 500, 10
    st.io_done, st.last_new = 1, 300            # a read in C made an entry at clock 300, not taken yet
    system._on_block(system.uc, image.base, 2, None)    # a block not seen before, of shared code
    system._take_log()
    assert system.last_new == 500


def test_a_system_run_is_collected():
    """A System goes when nothing refers to it any more, and with it what its C state allocated
    (dread.fasthooks frees that with the state). Once the state held the callbacks, through them
    the System, and the finalizer that frees the C side held the state: every run stayed in memory
    (3.5 MB each on gizmo's tour, 1.6 GB for 450 runs)."""
    import gc
    import weakref
    from conftest import gizmo_dir
    from dread.target import Target
    t = Target.load(gizmo_dir("m3"))
    image = t.image()
    system = sysrun.System(t, image, image.payload(), scenario=t.scenarios["keys"])
    system.run(int(t.scenarios["keys"]["limit"]))
    gone = weakref.ref(system)
    del system
    gc.collect()
    assert gone() is None


def test_without_cuts_holes_out_of_ranges():
    from dread.models import without
    assert without([(0, 0x10)], [(4, 5)]) == [(0, 4), (5, 0x10)]
    assert without([(0, 0x10), (0x20, 0x30)], [(0, 4), (8, 0xC), (0x2C, 0x40)]) == [(4, 8), (0xC, 0x10), (0x20, 0x2C)]
    assert without([(0, 8)], [(0, 8)]) == [] and without([(0, 8)], [(9, 12)]) == [(0, 8)]


def test_the_chunk_size_changes_nothing(monkeypatch):
    """A chunk ends where a block starts, before the block counts: every outcome with chunks of a
    few blocks is that with one chunk. (Chunks ended by the engine's count of instructions once,
    inside blocks, whose rest the next chunk counted again, at points the two images do not share:
    FreeRTOS's main_full rebuilt ran 106 clock units behind the original at the limit.) Also with
    most of gizmo outside shared code, where the waits are decided block by block (System._stretch)."""
    from conftest import gizmo_dir
    from dread.program import Program
    from dread.target import Target
    t = Target.load(gizmo_dir("m4"))
    image = t.image()
    keep = ("Reset_Handler", "TIMER_IRQHandler", "timer_irq", "uart_putc")
    rebuilt = [fn.name for fn in Program(t, image).functions if fn.name not in keep]

    def outcomes():
        out = {}
        for built in ((), rebuilt):
            for name, scenario in sorted(t.scenarios.items()):
                scenario = dict(scenario, _name=name)
                o = sysrun.System(t, image, image.payload(), scenario=scenario, built=built).run(
                    int(scenario.get("limit", sysrun.LIMIT)))
                out[name, len(built)] = (o.how, o.clock, o.log, o.reports, o.entered, o.order, o.calls, o.where)
        return out
    monkeypatch.setattr(sysrun, "CHUNK", 13)
    small = outcomes()
    monkeypatch.setattr(sysrun, "CHUNK", 10 ** 9)
    assert small == outcomes()


@pytest.mark.compiles
def test_the_run_keeps_who_entered_whom(target):
    """The transfers into functions (Outcome.calls, sysrun.transfers): the reset into the image's entry,
    the timer's interrupt into its handler (through the A profile's vector, which only branches on),
    and every indirect transfer the listing has in dispatch seen in the run with its target: GCC jumps
    to cmd_volume through gizmo's table of commands; clang folds the table into a switch (rv32: no
    pointer there). Each log entry's pc lies in the function that made it, and every transfer is
    explained by the instruction in front of it."""
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    scenario = {"input": [[40_000, "uart", "3"], [120_000, "uart", "s"]], "until": {"uart": "volume 3"},
                "limit": 5_000_000}
    o = sysrun.System(target, image, image.payload(), program=program, scenario=scenario).run(5_000_000)
    assert o.how.startswith("until"), o.how
    rows, unexplained = sysrun.transfers(program, o.calls)
    assert unexplained == 0
    first = program.by_entry[o.order[0]].name          # (a32: the reset vector only branches to it)
    assert [(r["how"], r["to"]) for r in rows if r["how"] == "reset"] == [("reset", first)]
    handlers = {r["to"] for r in rows if r["how"] == "exception"}
    assert handlers and handlers <= {"TIMER_IRQHandler", "irq_entry", "trap_entry"}, handlers
    fn = program.by_name["dispatch"]
    indirect = [a for a in fn.insns if program.arch.flow(program.insn(a))[0] in ("icall", "ijump")]
    pointers = {(r["from"], r["to"]) for r in rows if r["how"] in ("pointer", "pointer-jump") and r["from"] == "dispatch"}
    assert pointers == ({("dispatch", "cmd_volume")} if indirect else set()), (pointers, indirect)
    assert len(o.where) == len(o.log)
    names, at = sysrun.running(program, o.where)
    sent = {names[k] for e, k in zip(o.log, at) if e[:2] == ("uart", "sent")}
    assert sent and sent <= {"uart_putc", "uart_puts"}, sent


ENTERED = r"""
#define UART (*(volatile unsigned *)0x40004000)
#define ICSR (*(volatile unsigned *)0xE000ED04)
__attribute__((noinline)) void emit(int c) { UART = c; }
static volatile int switches;
unsigned worker_frame[8];
__attribute__((noinline)) void after_pend(void) { emit('t'); }
__attribute__((noinline)) void pend_then_call(void) { ICSR = 1u << 28; after_pend(); emit('u'); }
__attribute__((noinline)) void relay(int c) { emit(c); }
static void (*volatile hook)(int);
__attribute__((noinline)) void run_hooks(void) { hook('h'); emit('i'); }
__attribute__((noreturn)) void worker(void) { emit('w'); for (;;) __asm volatile ("" ::: "memory"); }
__attribute__((naked)) void pendsv_handler(void) {
    __asm volatile ("ldr r0, =switches\n ldr r1, [r0]\n adds r2, r1, #1\n str r2, [r0]\n cmp r1, #0\n bne 1f\n bx lr\n"
                    "1: ldr r0, =worker_frame\n msr psp, r0\n ldr lr, =0xFFFFFFFD\n bx lr\n");
}
__attribute__((noreturn)) void body(void) {
    emit('a');
    pend_then_call();
    hook = relay;
    run_hooks();
    worker_frame[6] = (unsigned)worker;
    worker_frame[7] = 0x01000000;
    ICSR = 1u << 28;
    for (;;) __asm volatile ("" ::: "memory");
}
__attribute__((naked, noreturn)) void reset(void) { __asm volatile ("b body\n"); }
__attribute__((section(".vectors"), used)) void (*const vectors[16])(void) = {
    (void (*)(void))0x20004000, reset, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, pendsv_handler, 0 };
"""
ENTERED_TABLES = """
[models.uart]
kind = "uart"
base = 0x40004000

[models.nvic]
kind = "nvic"

[scenarios.tasks]
until = {uart = "atuhiw"}
limit = 200_000
"""


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_callback_a_started_task_and_an_interrupt_in_front_of_an_entry(tmp_path, monkeypatch):
    """A program of the test's own: run_hooks calls relay through a pointer ("pointer"); PendSV, pended
    just before pend_then_call calls after_pend, comes in front of after_pend's first block, which then
    runs when the exception returned: the transfer into it is pend_then_call's call (the entry keeps
    what came before the exception, by its stack pointer), not a return from pendsv_handler; PendSV's
    second run returns into worker through a frame it made ("return", a task's first run), and the
    core entered pendsv_handler twice (exception 14). The C hooks and the Python hooks agree; the
    record keeps the transfers by address and the next reader names them as the tables do."""
    from dread import fasthooks
    from dread.program import Program
    t = _program_target(tmp_path, ENTERED, ENTERED_TABLES)
    image = t.image()
    program = Program(t, image)

    def run():
        o = sysrun.System(t, image, image.payload(), program=program, scenario=t.scenarios["tasks"]).run(200_000)
        assert o.how.startswith("until") and o.reports["uart"]["sent"] == "atuhiw", (o.how, o.reports)
        return o
    o = run()
    rows, unexplained = sysrun.transfers(program, o.calls)
    got = {(r["how"], r["from"], r["to"]) for r in rows}
    assert ("pointer", "run_hooks", "relay") in got
    assert ("call", "pend_then_call", "after_pend") in got and ("return", "pendsv_handler", "after_pend") not in got
    assert ("return", "pendsv_handler", "worker") in got
    assert [(r["how"], r["exception"], r["times"]) for r in rows if r["to"] == "pendsv_handler"] == [("exception", 14, 2)]
    assert unexplained == 0
    names, at = sysrun.running(program, o.where)
    assert {names[k] for e, k in zip(o.log, at) if e[0] == "write"} <= {"emit", "pend_then_call", "body"}
    assert [names[k] for e, k in zip(o.log, at) if e[0] == "interrupt"] == ["after_pend", "body"]
    if fasthooks.library() is not None:
        monkeypatch.setattr(fasthooks, "_LIB", [None])              # as without a C compiler
        python = run()
        assert (python.calls, python.where, python.log) == (o.calls, o.where, o.log)
    record = sysrun.make_record(program, "tasks", t.scenarios["tasks"], [("original", o)], "dread sysrun",
                                {"original": image.payload()})
    path = sysrun.write_record(t, image, record)
    assert path.endswith(os.path.join("sysrun", "main", "tasks.json"))
    rows = sysrun.ran(program, sysrun.records(t, image))
    assert {(r["how"], r["from"], r["to"], tuple(r["scenarios"])) for r in rows if r["how"] in ("pointer", "return")} == {
        ("pointer", "run_hooks", "relay", ("tasks",)), ("return", "pendsv_handler", "worker", ("tasks",))}
    assert sysrun.records(t, image, payload=image.payload() + b"\0") == {}    # (another image's record: left out)


@pytest.mark.compiles
def test_the_record_of_a_scenario(target):
    """make_record: both runs with how they ended, their logs, the pc of each entry and the function
    there, where the logs first differ with compare's lines; a window of `keep` entries around the
    first difference when the logs are longer; written and read back, of this image's bytes only.
    dread sysrun writes it."""
    import copy
    import json
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    a, b = both(target)
    record = sysrun.make_record(program, "boot", target.scenarios["boot"], [("original", a), ("rebuilt", b)],
                                "dread sysrun", {"original": image.payload(), "rebuilt": image.payload()},
                                built=["clamp"])
    assert record["differ"] is None and record["first"] == 0 and record["built"] == 1
    original = record["runs"][0]
    assert original["log"] == [list(e) for e in a.log] and len(original["at"]) == len(original["in"]) == len(a.log)
    assert {record["functions"][k] for e, k in zip(original["log"], original["in"]) if e[:2] == ["uart", "sent"]} <= {
        "uart_putc", "uart_puts"}
    c = copy.copy(b)
    c.log = list(b.log)
    c.log[7] = ("write", "uart", 0, 4, 99)
    record = sysrun.make_record(program, "boot", target.scenarios["boot"], [("original", a), ("rebuilt", c)],
                                "dread sysrun", {"original": image.payload(), "rebuilt": b"x"}, keep=6)
    assert record["differ"]["at"] == 7 and record["differ"]["lines"][0].startswith("device logs differ at entry 7")
    assert record["first"] == 4 and len(record["runs"][0]["log"]) == 6
    assert record["runs"][1]["log"][3] == ["write", "uart", 0, 4, 99]
    path = sysrun.write_record(target, image, record)
    try:
        # (a lift check of the same build leaves its own, SCENARIO.lift-check: not this test's)
        found = [k for k in sysrun.records(target, image) if not k.endswith(".lift-check")]
        assert json.load(open(path))["scenario"] == "boot" and found == ["boot"]
        assert "log" not in sysrun.records(target, image, logs=False)["boot"]["runs"][0]
    finally:
        os.remove(path)
    assert sysrun.main(["--target", target.root, "--scenario", "boot"]) == 0
    try:
        kept = sysrun.records(target, image)["boot"]
        assert kept["by"] == "dread sysrun" and [r["label"] for r in kept["runs"]] == ["original", "rebuilt"]
        assert kept["differ"] is None and kept["built"] > 0 and kept["runs"][1]["digest"] != kept["runs"][0]["digest"]
    finally:
        os.remove(os.path.join(sysrun.record_dir(target, image), "boot.json"))


def test_registers_that_answer_configured_words():
    """dread.models.regs: a range of registers answering configured 32-bit words (a PCI configuration
    space: FreeRTOS's IA32 demo reads its UART's base address at 0xe00a5010), every other word the
    default (0xffffffff: no device), reads of one, two or four bytes taking those bytes, stores kept
    and read back, a narrow one keeping the word's other bytes; all of it plain and idempotent."""
    import pytest
    from dread.models import create
    pci = create(None, "pci", {"kind": "regs", "base": 0xe0000000, "size": 0x100000, "default": 0xffffffff,
                               "words": {"0xa5010": 0x9000b000, "0xaa000": 0x09348086}}, ".")
    assert pci.ranges == pci.plain == pci.idempotent == [(0xe0000000, 0xe0100000)]
    assert pci.read(0xe00a5010, 4) == 0x9000b000
    assert pci.read(0xe00aa000, 2) == 0x8086 and pci.read(0xe00aa002, 2) == 0x0934 and pci.read(0xe00aa003, 1) == 0x09
    assert pci.read(0xe0000000, 4) == 0xffffffff and pci.read(0xe0000002, 2) == 0xffff
    pci.write(0xe0000004, 4, 0x00000006)
    pci.write(0xe0000005, 1, 0x01)
    assert pci.read(0xe0000004, 4) == 0x00000106
    assert pci.read(0xe00a5012, 4) == 0xffff9000         # (across two words: the next one's default)
    with pytest.raises(ValueError, match="4-aligned"):
        create(None, "bad", {"kind": "regs", "base": 0, "size": 0x10, "words": {"0x2": 1}}, ".")


def test_a_16550s_divisor_latch_sends_nothing():
    """dread.models.uart with lcr: while the line control register's dlab bit is set, a store to the
    data register writes the divisor (FreeRTOS's IA32 demo sets its UART to 115200 baud so: lcr 0x83,
    0x18 into the data register, lcr 3), which the model counted as a byte sent until 2026-10-28."""
    from dread.models import create

    class System:
        def __init__(self):
            self.notes = []

        def note(self, *what):
            self.notes.append(what)

        def interrupt_level(self, line, level):
            pass
    uart = create(System(), "uart", {"kind": "uart", "base": 0x9000b000, "data": 0, "status": 0x14,
                                     "tx_ready": 0x60, "rx_ready": 1, "size": 0x20, "lcr": 0xc}, ".")
    uart.write(0x9000b00c, 1, 0x83)
    uart.write(0x9000b000, 1, 0x18)
    assert uart.read(0x9000b000, 1) is None         # (the divisor: plain memory)
    uart.write(0x9000b00c, 1, 0x03)
    for c in b"ok":
        uart.write(0x9000b000, 1, c)
    assert uart.report() == {"sent": "ok"} and uart.read(0x9000b014, 1) == 0x60
