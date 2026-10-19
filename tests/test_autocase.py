"""The case generator on gizmo: coverage from generated cases alone (apply_config only with the
dictionary: its lines must read like volume=7), determinism, pointer learning, and the Thumb
IT-state repair in the emulator (a regression test with the input that hung)."""
import json

import pytest

from dread.autocase import Generator
from dread.target import Target
from conftest import gizmo_dir


@pytest.fixture
def gen(target):
    return Generator(target, target.image())


def covered(gen, name, cases):
    fn = gen.program.by_name[name]
    got = set()
    for c in cases:
        got |= gen.coverage(fn.entry, fn.insns, c, c.get("limit", 200_000))
    return got, set(fn.insns)


@pytest.mark.slow
@pytest.mark.parametrize("name", ["parse_uint", "memcmp", "strncpy", "fmt_dec", "apply_config"])
def test_generated_cases_cover(gen, name):
    fn = gen.program.by_name[name]
    cases = gen.generate(name, fn.entry, fn.insns)
    got, words = covered(gen, name, cases)
    assert got == words, sorted(hex(a) for a in words - got)


def test_dictionary_of_a_parser(gen):
    """apply_config compares keys with three strings; parse_kv, two levels down, splits at '='."""
    fn = gen.program.by_name["apply_config"]
    strings, chars = gen.dictionary(fn.insns)
    assert {b"volume", b"mode", b"baud"} <= set(strings) and ord("=") in chars
    assert "parse_kv" in [f.name for f in gen.callees(fn.insns)]
    assert gen.dictionary(gen.program.by_name["checksum"].insns) == ([], [])


@pytest.mark.slow
def test_generation_is_deterministic(gen):
    fn = gen.program.by_name["parse_kv"]
    one = gen.generate("parse_kv", fn.entry, fn.insns)
    two = gen.generate("parse_kv", fn.entry, fn.insns)
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)


def test_pointers_are_learned_on_cortex_m():
    """On the M3 build small numbers are no addresses: without pointer learning the generator
    never found two valid pointers at once for parse_uint (12 of 46 instructions stayed missed)."""
    t = Target.load(gizmo_dir("m3"))
    g = Generator(t, t.image())
    fn = g.program.by_name["parse_uint"]
    cases = g.generate("parse_uint", fn.entry, fn.insns)
    scratch = t.memory.scratch_base
    pointers = [c for c in cases if scratch <= c["args"][0] < scratch + t.memory.scratch_size]
    assert len(pointers) > len(cases) // 2


def test_thumb_it_state_after_hooked_store():
    """parse_kv on the M3 build with this input looped forever before the repair: a conditional
    store inside an IT block, hooked for the written-bytes record, left a stale IT state that made
    the first instruction behind the next branch conditional (Arm.settle_it)."""
    t = Target.load(gizmo_dir("m3"))
    g = Generator(t, t.image())
    s = t.memory.scratch_base
    case = {"args": [s + 0x104, s, 2, 0xFFFFFFFF], "mem": [[s, 1, 61], [s + 0x40, 1, 61], [s + 0x104, 1, 65],
            [s + 0x105, 1, 0x56], [s + 0x106, 1, 61]], "returns": [], "calls": [], "frame": 0}
    m = g.machine()
    m.plan = []
    g.apply(m, case)
    result = m.call(g.program.by_name["parse_kv"].entry, *case["args"], max_instructions=10_000)
    assert result.ret == 0xFFFFFFFD          # -3: no number behind the '='
    assert m.read(s, 2) == b"A\0"            # one character of the key (keymax 2) and its end


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_callee_that_hangs_is_stubbed(tmp_path):
    """A null check that calls an assertion handler which waits for a debugger: every input that
    took it ran into the instruction limit inside the handler and was dropped, so the check stayed
    uncovered. The generator stubs a callee a run hung in, and the cases say so."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    (tmp_path / "t.c").write_text("volatile int go;\nvoid trap(int line) { while (!go) ; }\n"
                                  "int get(int *p) { if (!p) trap(42); return 5; }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH\n .bss : { *(.bss*) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "get", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    g = Generator(t, t.image())
    fn = g.program.by_name["get"]
    cases = g.generate("get", fn.entry, fn.insns)
    got, words = covered(g, "get", cases)
    assert got == words, sorted(hex(a) for a in words - got)
    trap = g.program.by_name["trap"].entry
    assert any(c[0] == trap for case in cases for c in case.get("calls", ()))


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_callee_that_never_returns_makes_hang_cases(tmp_path):
    """A failure hook that spins with interrupts off never returns (dread.noreturn), and the lifter
    puts an endless loop behind a call of it. Stubbed, the original went on behind the call where
    the lifted C stopped (FreeRTOS's allocator: every lifted caller hung). Such a callee is not
    stubbed: the input becomes a "hang" case, compared up to the limit."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    (tmp_path / "t.c").write_text("__attribute__((noipa)) void hook(void) { for (;;) ; }\n"
                                  "int alloc(int n) { if (n < 0) { hook(); return 0; } return n * 2; }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "alloc", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    g = Generator(t, t.image())
    assert "hook" in g.endless()
    fn = g.program.by_name["alloc"]
    cases = g.generate("alloc", fn.entry, fn.insns)
    hook = g.program.by_name["hook"].entry
    assert not any(c[0] == hook for case in cases for c in case.get("calls", ()))
    hangs = [c for c in cases if c.get("hang")]
    assert hangs and all(c["args"][0] & 0x80000000 for c in hangs)


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_hook_deeper_down_stubs_the_callee_it_was_reached_by(tmp_path):
    """FreeRTOS's queue set-up calls the allocator, which calls a failure hook that never returns
    when the heap's state (an input) says no. Every such run became a hang case and nothing was
    stubbed, so the code behind the allocator's call stayed uncovered (26 of 57 instructions). The
    callee the run went in by is stubbed in later inputs; the hook itself stays unstubbed."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    (tmp_path / "t.c").write_text("__attribute__((noipa)) void hook(void) { for (;;) ; }\n"
                                  "__attribute__((noipa)) int alloc(int n) { if (n != 12345) hook(); return n; }\n"
                                  "__attribute__((noipa)) int side(int r) { return r + 3; }\n"
                                  "int make(int n) { int r = alloc(n); if (r == 12345) return 1; return side(r); }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "make", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    g = Generator(t, t.image())
    assert "hook" in g.endless() and "alloc" not in g.endless()
    fn = g.program.by_name["make"]
    cases = g.generate("make", fn.entry, fn.insns)
    got, words = covered(g, "make", cases)
    assert got == words, sorted(hex(a) for a in words - got)
    stubs = {c[0] for case in cases for c in case.get("calls", ())}
    assert g.program.by_name["alloc"].entry in stubs and g.program.by_name["hook"].entry not in stubs


def test_cached_cases_follow_the_whole_image(target):
    """A rebuild that moves data leaves a function's instructions alike while its literal pool (or
    any global it reads) changes: its cached cases set cells nobody reads any more (FreeRTOS's ARM
    builds failed on the original after a configuration change). The key covers the whole image."""
    from dread.autocase import cache_key
    image = target.image()
    g1 = Generator(target, image)
    fn = g1.program.by_name["parse_kv"]
    sizes = {a: g1.program.insn_size(a) for a in fn.insns}
    payload = bytearray(g1.payload)
    at = len(payload) - 1                       # a byte outside the function: its instructions stay alike
    assert not any(a <= image.base + at < a + sizes[a] for a in fn.insns)
    payload[at] ^= 0xFF
    g2 = Generator(target, image, payload=bytes(payload))
    keys = [cache_key(g.payload, image.base, fn.insns, sizes, fn.entry, {}, g.program.arch, g.environment())
            for g in (g1, g2)]
    assert keys[0] != keys[1]
    assert cache_key(g1.payload, image.base, fn.insns, sizes, fn.entry, {}, g1.program.arch, g1.environment()) == keys[0]


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_loop_cases_for_a_function_that_never_returns(tmp_path):
    """A task's loop gets no case that returns. Loop cases stub its callees and end at the same stub
    call in both images, or at the call of a callee that never returns (its stub ends the run, so
    the C's endless loop behind that call is never compared); together they run all of it."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.emu import EmuError
    (tmp_path / "t.c").write_text("volatile int flag;\n"
                                  "__attribute__((noipa)) void fail(void) { for (;;) ; }\n"
                                  "__attribute__((noipa)) int poll(int x) { return x + flag; }\n"
                                  "void task(int p) { for (;;) { int v = poll(p); if (v == 77) fail();"
                                  " if (v & 1) flag++; } }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH\n .bss : { *(.bss*) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "task", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    g = Generator(t, t.image())
    assert {"task", "fail"} <= g.endless()
    fn = g.program.by_name["task"]
    cases = g.generate("task", fn.entry, fn.insns, loop=True)
    got, words = covered(g, "task", cases)
    assert got == words, sorted(hex(a) for a in words - got)
    fail, poll = g.program.by_name["fail"].entry, g.program.by_name["poll"].entry
    assert all(c.get("hang") and c.get("stub_calls") and c.get("stops") == [fail] for c in cases)
    assert all(any(x[0] == poll for x in c["calls"]) for c in cases)
    ends = set()
    for c in cases:                         # each replays to the same end: a stub call, or the hook's call
        m = g.machine()
        m.plan = []
        g.apply(m, c)
        r = m.call(fn.entry, *c["args"], max_instructions=c["limit"])
        ends.add(m.stopped_at)
        assert r.calls[-1] == ("did not return",)
    assert ends == {"calls", "never returns"}


def test_loads_from_another_functions_pool_are_code_reads():
    """A case that pointed into another function's literal pool read other bytes in the rebuilt
    image, where the overlay had put C over that pool or behind the image. A load from a function's
    extent by an instruction of another function, or from the room the overlay fills, is a load of
    code; a function's own literal loads are not (its constants), and loads of instructions are."""
    from dread.emu import EmuError
    t = Target.load(gizmo_dir("m3"))
    g = Generator(t, t.image())
    p, owners = g.program, g.owners
    pools = [(k, a) for k, f in enumerate(p.functions) if f.insns
             for a in range(p.end(f), p.end(f) + 64, 2) if a not in p.code and 0 <= (a - p.base) >> 1 < len(owners)
             and owners[(a - p.base) >> 1] == k + 1]
    assert pools, "gizmo-m3 has functions with literal pools behind them"
    k, pool = pools[0]
    assert owners[(p.functions[k].entry - p.base) >> 1] == k + 1
    # uart_puts calls uart_putc, which loads the UART's address from its own pool: no load of code
    fn = g.program.by_name["uart_puts"]
    s = t.memory.scratch_base
    case = {"args": [s, 0, 0, 0], "mem": [[s, 1, 65], [s + 1, 1, 0]], "returns": [], "calls": [], "frame": 0}
    m = g.machine(); m.plan = []; g.apply(m, case)
    m.trace_reads(t.image().base, t.image().end, g.code, owners)
    try:
        m.call(fn.entry, *case["args"], max_instructions=100_000)
    except EmuError:
        pass
    assert not m.code_reads
    # the same function handed a pointer into another function's pool, or behind the image where
    # the overlay puts C (FreeRTOS: a queue at 0x10000, zeros in the original), loads code
    for pointer in (pool, t.image().end + 0x100):
        case["args"][0] = pointer
        m = g.machine(); m.plan = []; g.apply(m, case)
        m.trace_reads(t.image().base, t.image().end, g.code, owners)
        try:
            m.call(fn.entry, *case["args"], max_instructions=100_000)
        except EmuError:
            pass
        assert m.code_reads, hex(pointer)
