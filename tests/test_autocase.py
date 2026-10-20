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


def test_cached_cases_follow_what_regflow_finds_not_its_version(target, monkeypatch):
    """Cases depend on what dread.regflow finds in the image (a stub logs the registers a callee
    reads), so the key holds its result, not its version: a version that finds the same keeps the
    cached cases (version 11 changed only what ARM's FPU pushes mean and regenerated every image's
    cases), a different finding does not."""
    from dread import regflow
    image = target.image()
    g = Generator(target, image)
    flow, before = g.flow(), g.environment()
    monkeypatch.setattr(regflow, "VERSION", regflow.VERSION + 100)
    monkeypatch.setattr(regflow, "load", lambda *a, **k: flow)
    assert Generator(target, image).environment() == before
    changed = regflow.RegFlow.from_json(flow.to_json())
    changed.reads[sorted(changed.reads)[0]] ^= 1
    monkeypatch.setattr(regflow, "load", lambda *a, **k: changed)
    assert Generator(target, image).environment() != before


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


def test_loads_inside_instructions_and_stores_behind_the_image():
    """strcmp was handed an address inside its own code (the second half of a 32-bit instruction)
    as a string: the original read its own instruction bytes, the rebuilt image others. Every
    halfword an instruction covers counts, and so does a store into the room behind the image."""
    from dread.emu import EmuError
    t = Target.load(gizmo_dir("m3"))
    g = Generator(t, t.image())
    p = g.program
    wide = next(a for a, (size, _) in sorted(p.code.items()) if size == 4)
    assert wide + 2 in g.code and wide + 2 not in p.code
    assert p.writable_end() == t.image().limit
    fn = p.by_name["strncpy"]
    s = t.memory.scratch_base
    for args, what in (([s, wide + 2, 4, 0], "load"), ([t.image().end + 0x40, s, 4, 0], "store")):
        case = {"args": args, "mem": [[s, 1, 65], [s + 1, 1, 0]], "returns": [], "calls": [], "frame": 0}
        m = g.machine(); m.plan = []; g.apply(m, case)
        m.trace_reads(t.image().base, t.image().end, g.code, g.owners)
        try:
            m.call(fn.entry, *case["args"], max_instructions=100_000)
        except EmuError:
            pass
        assert g.touches_image(m), what


def test_a_slow_failed_generation_is_cached(target, tmp_path, monkeypatch):
    """The search is deterministic, so a function whose generation failed fails again under the same
    key: a slow failure is cached like cases are, and the next run raises at once (a FreeRTOS build's
    first lift round spent most of its minute generating the eight functions that always fail). A
    quick one is not: it costs little to repeat, and one that was not the function's own (an emulator
    left broken, dread.emu.Core.retire) must not stay."""
    from dread import autocase
    from dread.cases import Suite
    from dread.emu import Layout
    from dread.program import Program
    target.build = str(tmp_path / "build")          # not the example's own cache
    image = target.image()
    image.extra_cases, image.kept_cases = str(tmp_path / "extra"), str(tmp_path / "kept")   # none written
    program, layout = Program(target, image), Layout(target, image)
    suite = Suite(target, image, program, layout)
    suite.auto["clamp"] = {}
    run = {"target": target, "image": image, "program": program, "layout": layout, "original": image.payload(),
           "suite": suite}
    calls, clock = [], [1000.0]

    def failing(self, name, *args, **kwargs):
        calls.append(name)
        clock[0] += took
        raise autocase.GenerationError("%s: only 0 of 400 generated inputs ran to the end" % name)
    monkeypatch.setattr(autocase.Generator, "generate", failing)
    monkeypatch.setattr(autocase, "_GENERATORS", {})
    monkeypatch.setattr(autocase.time, "time", lambda: clock[0])
    fn = program.by_name["clamp"]
    took = 1.0                                      # quick: generated again every time
    for _ in range(2):
        with pytest.raises(autocase.GenerationError, match="only 0 of 400"):
            autocase.cases_for(run, "clamp", fn)
    assert calls == ["clamp", "clamp"]
    took = autocase.FAILURE_SECONDS + 1             # slow: cached
    for _ in range(2):
        with pytest.raises(autocase.GenerationError, match="only 0 of 400"):
            autocase.cases_for(run, "clamp", fn)
    assert calls == ["clamp", "clamp", "clamp"]


def test_a_round_of_small_arguments(monkeypatch):
    """When fewer than four inputs ran to the end, a second round draws the arguments small (half of
    them 0): a count split over two registers (MSP430's 32-bit length in r14:r15) is small only when
    both halves are, which random values almost never are. Here a run only finishes when every
    argument is 0 or 1 (the emulator's call is wrapped)."""
    from dread import autocase, emu
    from dread.target import Target
    from conftest import gizmo_dir
    target = Target.load(gizmo_dir("m3"))
    gen = autocase.Generator(target, target.image())
    fn = gen.program.by_name["strncpy"]
    call = emu.Machine.call

    def picky(self, addr, *args, **kwargs):
        if any(a > 1 for a in args):
            raise emu.EmuError("emulation fault at pc=0x%x: Invalid memory write (UC_ERR_WRITE_UNMAPPED)" % addr)
        return call(self, addr, *args, **kwargs)
    monkeypatch.setattr(emu.Machine, "call", picky)
    cases = gen.generate("strncpy", fn.entry, fn.insns, fallbacks=False, budget=100)
    assert len(cases) >= 4 and all(a <= 1 for c in cases for a in c["args"])


def test_a_near_miss_is_repaired():
    """gizmo-m4's script_run dispatches on keys_read() & 7 and loops over its length argument in two
    of the cases. The generator took that length for a pointer (the address a loop faulted at lay
    just behind it) and aimed it at a buffer from then on, so the loops ran off memory or to the
    instruction limit, and those cases stayed unrun: 60 cases in 22 s, 8 instructions missed. An
    input that runs new code and then faults (or loops to the limit in the function itself) is now
    repaired, each argument in turn set small: every instruction runs."""
    from dread import autocase
    from dread.emu import Machine
    from dread.target import Target
    from conftest import gizmo_dir
    target = Target.load(gizmo_dir("m4"))
    gen = autocase.Generator(target, target.image())
    fn = gen.program.by_name["script_run"]
    cases = gen.generate("script_run", fn.entry, fn.insns)
    covered = set()
    for case in cases:
        m = gen.machine()
        m.plan = []
        gen.apply(m, case)
        m.cover(fn.insns[0], gen.program.end(fn))
        m.call(fn.entry, *case["args"], max_instructions=200000)
        covered |= m.covered_insns(fn.insns)
    assert covered == set(fn.insns)


def test_msp430_calls_through_a_register_are_callees():
    """GCC for the MSP430 loads a function it calls more than once into a register (mov #uart_puts,
    r10; call r10): the layer names such callees, the generator stubs them in loop cases (gizmo's
    main calls only so in its loop), and dread.lift's direct callees include them (the lift check's
    call trees: a function whose callee's C failed it is tried again once that C is dropped)."""
    from dread import autocase, lift
    from dread.target import Target
    from conftest import gizmo_dir
    target = Target.load(gizmo_dir("msp430"))
    gen = autocase.Generator(target, target.image())
    program = gen.program
    main = program.by_name["main"]
    found = gen.arch.register_call_targets(program, main)
    names = {program.by_entry[e].name for entries in found.values() for e in entries}
    assert {"uart_puts", "handle_key"} <= names
    assert {"uart_puts", "handle_key"} <= {f.name for f in gen.callees(main.insns, depth=1)}
    assert {"uart_puts", "handle_key"} <= lift.direct_callees(program, main)


def test_float_arguments_get_values_of_their_own():
    """A function that reads FPU registers at its entry under hard float gets float arguments
    (fregs, fargs): to_millivolts' s0 takes both signs, so both of its paths run."""
    from dread import autocase
    from dread.target import Target
    from conftest import gizmo_dir
    target = Target.load(gizmo_dir("m4"))
    gen = autocase.Generator(target, target.image())
    fn = gen.program.by_name["to_millivolts"]
    cases = gen.generate("to_millivolts", fn.entry, fn.insns)
    assert all(c.get("fregs") == ["s0"] for c in cases)
    signs = {c["fargs"][0] >> 31 for c in cases if c["fargs"][0] & 0x7FFFFFFF}
    assert signs == {0, 1}


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_case_with_a_callback_into_a_function_is_repaired(tmp_path):
    """drop_wild: a case whose callback points into the middle of a function (the original runs
    that code with its registers, the lifted C with others) comes back with the pointer stub there
    (Thumb's bit kept, the stub added), and the input is pinned for the next pass; a fair case stays."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    (tmp_path / "t.c").write_text(r'''
__attribute__((noinline)) int twice(int x) { return x + x + 3; }
__attribute__((noinline)) int apply(int (*f)(int), int x) { if (!f) return -1; return f(x) * 5; }
__attribute__((naked, noreturn)) void reset(void) { for (;;) ; }
__attribute__((section(".vectors"), used)) void (*const vectors[2])(void) = {(void (*)(void))0x20005000, reset};
''')
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { KEEP(*(.vectors)) *(.text*) *(.rodata*) } > FLASH }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "reset", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '''
[image.main]
original = "original/t.bin"
base = 0x08000000
entry = "reset"

[compiler]
cross = "arm-none-eabi-"
''')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    gen = Generator(t, t.image())
    twice, apply = gen.program.by_name["twice"], gen.program.by_name["apply"]
    wild = {"args": [twice.insns[1] | 1, 4, 0, 0], "mem": [], "returns": [], "calls": [], "frame": 0}
    fair = {"args": [twice.entry | 1, 4, 0, 0], "mem": [], "returns": [], "calls": [], "frame": 0}
    kept, pins = gen.drop_wild(apply.entry, apply.insns, [wild, fair], 10_000)
    assert pins == ({0}, set())
    assert kept[1] == fair and len(kept) == 2
    assert kept[0]["args"][0] == gen.fake_pointer | 1 == gen.fake_code(apply.entry)
    assert [gen.fake_pointer, 0, 0] in kept[0]["calls"]


def test_pointer_buffers_stay_in_the_scratch():
    """Learned data pointers' buffers lie inside the scratch and off the pointer stub: sixteen below
    the stub where the scratch is large (as always, so those images keep their cases), fewer where it
    is small (gizmo-msp430's 16 KiB, where the sixteen ran over the stub and past the scratch's end)."""
    from conftest import gizmo_dir
    from dread import autocase
    from dread.target import Target
    for variant, count in (("m3", 16), ("msp430", 11)):
        t = Target.load(gizmo_dir(variant))
        g = autocase.Generator(t, t.image())
        lo, hi = t.memory.scratch_base, t.memory.scratch_base + t.memory.scratch_size
        assert len(g.pointer_slots) == count
        for a in g.pointer_slots:
            assert lo <= a and a + autocase.POINTER_STEP <= hi
            assert not a <= g.fake_pointer < a + autocase.POINTER_STEP


def test_memory_cells_do_not_overlap():
    """A memory cell that overlaps a known one is left out, unless it holds the known ones whole, which
    it then replaces: every input writes its cells one after the other, and overlapping cells (a
    learned pointer one byte off read the same buffer) wrote over each other's bytes, so that the
    value the search gave the first never reached the function (FreeRTOS's vListInsert)."""
    from dread.autocase import Cells
    cells = Cells()
    cells.learn({0x1000: 4, 0x1001: 4, 0x1002: 4, 0x1003: 4})      # a pointer one, two, three bytes off
    assert dict(cells) == {0x1000: 4}
    cells.learn({0x2001: 1, 0x2000: 4, 0x2006: 2})                  # a byte first, then its word
    assert dict(cells) == {0x1000: 4, 0x2000: 4, 0x2006: 2}
    cells.add(0x2004, 4)                                            # holds 0x2006 whole, not 0x2000
    assert dict(cells) == {0x1000: 4, 0x2000: 4, 0x2004: 4}
    cells.add(0x1000, 2)                                            # known larger: as it was
    assert dict(cells) == {0x1000: 4, 0x2000: 4, 0x2004: 4}
    spans = sorted((a, a + n) for a, n in cells.items())
    assert all(e <= s for (_, e), (s, _) in zip(spans, spans[1:]))


SELF_LOOP_C = """
volatile unsigned flag;
__attribute__((noinline, noreturn)) void halt(unsigned code) { flag = code; for (;;) { } }
__attribute__((noinline)) unsigned check(unsigned x) { if (x > 100u) { flag = x; for (;;) { } } return x + 1; }
"""


def test_a_run_that_ends_on_a_branch_to_itself_is_a_case(tmp_path):
    """A run that runs into its limit on an unconditional branch to itself (for (;;);) has done all it
    does, in the original and in the C alike: it is a "hang" case. A function that ends so (FreeRTOS's
    malloc-failed hook) got no loop case before, nor did the error path of one that returns otherwise."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    (tmp_path / "t.c").write_text(SELF_LOOP_C)
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "check", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    target = Target.load(str(tmp_path))
    gen = Generator(target, target.image())
    halt, check = gen.program.by_name["halt"], gen.program.by_name["check"]
    m = gen.machine()
    m.plan = []
    m.may_hang = True
    m.call(check.entry, 200, max_instructions=1000)
    spin = [a for a in check.insns if gen.program.arch.flow(gen.program.insn(a))[1] == a]
    assert m.timed_out and [m.self_loop] == spin
    m = gen.machine()
    m.plan = []
    m.call(check.entry, 2, max_instructions=1000)
    assert m.self_loop is None
    for fn, loop in ((halt, True), (check, False)):
        cases = gen.generate(fn.name, fn.entry, fn.insns, loop=loop)
        got = set()
        for c in cases:
            got |= gen.coverage(fn.entry, fn.insns, c, c.get("limit", 200_000))
        assert got == set(fn.insns), fn.name
        assert loop or any(c.get("hang") for c in cases)
