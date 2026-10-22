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
    src = src_copy(tmp_path, variant, ("out[i] = d < 10 ? '0' + d : 'a' + d - 10;",
                                       "out[i] = d < 10 ? '1' + d : 'a' + d - 10;"))
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
                out[name, len(built)] = (o.how, o.clock, o.log, o.reports, o.entered, o.order)
                fast += system.st.fast if system.fast is not None else 0
        return out, fast
    with_c, done_in_c = outcomes()
    monkeypatch.setattr(fasthooks, "_LIB", [None])          # as without a C compiler
    without, none = outcomes()
    assert with_c == without
    assert none == 0 and done_in_c > 1000, done_in_c
    assert all(o[0].startswith("until") for o in with_c.values()), [o[0] for o in with_c.values()]


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
                out[name, len(built)] = (o.how, o.clock, o.log, o.reports, o.entered, o.order)
        return out
    monkeypatch.setattr(sysrun, "CHUNK", 13)
    small = outcomes()
    monkeypatch.setattr(sysrun, "CHUNK", 10 ** 9)
    assert small == outcomes()
