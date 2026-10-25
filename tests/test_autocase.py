"""The case generator on gizmo: coverage from generated cases alone (apply_config only with the
dictionary: its lines must read like volume=7), determinism, pointer learning, and the Thumb
IT-state repair in the emulator (a regression test with the input that hung)."""
import json

import pytest

from dread.autocase import Generator
from dread.target import Target
from conftest import NOIPA_DEF
from conftest import gizmo_dir, needs_riscv_gcc


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
    (tmp_path / "t.c").write_text(NOIPA_DEF + "NOIPA void hook(void) { for (;;) ; }\n"
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
    (tmp_path / "t.c").write_text(NOIPA_DEF + "NOIPA void hook(void) { for (;;) ; }\n"
                                  "NOIPA int alloc(int n) { if (n != 12345) hook(); return n; }\n"
                                  "NOIPA int side(int r) { return r + 3; }\n"
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
    builds failed on the original after a configuration change). The key covers the whole image, and
    the layout of its code (whether a load is one of code depends on every function's extent)."""
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
    keys = [cache_key(g.payload, image.base, fn.insns, sizes, fn.entry, {}, g.program.arch, g.environment("parse_kv"))
            for g in (g1, g2)]
    assert keys[0] != keys[1]
    assert cache_key(g1.payload, image.base, fn.insns, sizes, fn.entry, {}, g1.program.arch,
                     g1.environment("parse_kv")) == keys[0]
    g3 = Generator(target, image)
    g3.program.by_name["checksum"].size += 2    # (a function far from parse_kv's tree, its extent longer)
    assert g3.environment("parse_kv") != g1.environment("parse_kv")


def test_cached_cases_follow_what_regflow_finds_not_its_version(target, monkeypatch):
    """Cases depend on what dread.regflow finds in the image (a stub logs the registers a callee
    reads), so the key holds its result, not its version: a version that finds the same keeps the
    cached cases (version 11 changed only what ARM's FPU pushes mean and regenerated every image's
    cases), a different finding of the function does not."""
    from dread import regflow
    image = target.image()
    g = Generator(target, image)
    flow, before = g.flow(), g.environment("parse_kv")
    monkeypatch.setattr(regflow, "VERSION", regflow.VERSION + 100)
    monkeypatch.setattr(regflow, "load", lambda *a, **k: flow)
    assert Generator(target, image).environment("parse_kv") == before
    changed = regflow.RegFlow.from_json(flow.to_json())
    changed.reads["parse_kv"] = changed.reads.get("parse_kv", 0) ^ 1
    monkeypatch.setattr(regflow, "load", lambda *a, **k: changed)
    assert Generator(target, image).environment("parse_kv") != before


REGFLOW_LOAD = __import__("dread.regflow", fromlist=["load"]).load     # (before any test patches it)


def scoped_keys(target, monkeypatch, change=None, tweak=None):
    """{name: Generator.environment(name)} of every function of a target: with what dread.regflow found
    changed by change(flow) (on a copy) and the generator changed by tweak(generator) before the keys are
    made, when given."""
    from dread import regflow
    image = target.image()
    flow = REGFLOW_LOAD(target, image)
    if change is not None:
        flow = regflow.RegFlow.from_json(json.loads(json.dumps(flow.to_json())))
        change(flow)
    monkeypatch.setattr(regflow, "load", lambda *a, **k: flow)
    g = Generator(target, image)
    if tweak is not None:
        tweak(g)
    return {fn.name: g.environment(fn.name) for fn in g.program.functions}


def holding(program, name):
    """The functions whose call trees (dread.autocase.reaches) hold name or a function with an open
    transfer: those whose keys a change of name's facts renews."""
    from dread.autocase import reaches
    graph = reaches(program)
    marked = {name} | {n for n, (_, opened) in graph.items() if opened}
    out = set()
    for start in graph:
        seen, work = {start}, [start]
        while work:
            for c in graph[work.pop()][0]:
                if c not in seen:
                    seen.add(c)
                    work.append(c)
        if seen & marked:
            out.add(start)
    return out


def flip_reads(name):
    """A change of what regflow found of one function: one more (or one fewer) register it reads."""
    def change(flow):
        flow.reads[name] = flow.reads.get(name, 0) ^ 1
    return change


def changed(before, after):
    return {n for n in before if after[n] != before[n]}


def test_case_keys_follow_the_call_tree(target, monkeypatch):
    """A function's cases depend on what dread.regflow finds of the functions its runs reach (a stub logs
    the registers its callee reads and answers those its callers read beyond a C result), not of the
    others: the key holds the facts of its call tree only (Generator.scopes), so that a change of one
    function's facts makes the cases of the functions whose trees hold it anew and keeps the others'
    (until 2026-10-23 one digest of the whole image's findings: a change concerning the callers of a few
    functions made every case of a private firmware anew). gpio_set lies in the trees of led_on (a
    tail call on the M3 and RISC-V builds), handle_key and main, not in parse_kv's or checksum's; so do
    its stub's answers beyond a C result (more), its two-word answers (wide_results) and its rows of the
    domains table. The same image gives the same keys."""
    from dread.program import Program
    trees = holding(Program(target), "gpio_set")
    assert {"gpio_set", "led_on", "handle_key", "main"} <= trees and not trees & {"parse_kv", "checksum"}
    before = scoped_keys(target, monkeypatch)
    assert scoped_keys(target, monkeypatch) == before
    reg = target.abi.arg_regs[-1]
    for after in (scoped_keys(target, monkeypatch, change=flip_reads("gpio_set")),
                  scoped_keys(target, monkeypatch, tweak=lambda g: g.more().update(
                      {g.program.by_name["gpio_set"].entry: [reg]})),
                  scoped_keys(target, monkeypatch, tweak=lambda g: g.wide.add(g.program.by_name["gpio_set"].entry)),
                  scoped_keys(target, monkeypatch, tweak=lambda g: g.domain_rows.setdefault("gpio_set", []).append(
                      "gpio_set:ret\t4\t0\t1"))):
        assert changed(before, after) == trees
    after = scoped_keys(target, monkeypatch, tweak=lambda g: g.domain_rows.setdefault("", []).append(
        "0x20000000\t4\t0\t1"))                  # (a domain of an address: any run may read it)
    assert changed(before, after) == set(before)


def test_case_keys_through_a_tail_call_and_a_table_of_functions(monkeypatch):
    """On the M3 build led_on ends in a tail call of gpio_set (b), and dispatch jumps through a table of
    functions (commands[cmd](arg) behind its bound check: bx r3, the targets found by running the
    dispatch, dread.switches). Both are in the key: gpio_set's facts in led_on's, cmd_volume's in
    dispatch's and in those of its callers, not in cmd_mode's or apply_config's."""
    from dread.program import Program
    t = Target.load(gizmo_dir("m3"))
    prog = Program(t)
    gpio_set, led_on, dispatch = (prog.by_name[n] for n in ("gpio_set", "led_on", "dispatch"))
    assert ("jump", gpio_set.entry) in [prog.arch.flow(prog.insn(a))[:2] for a in led_on.insns]
    assert "ijump" in [prog.arch.flow(prog.insn(a))[0] for a in dispatch.insns]
    before = scoped_keys(t, monkeypatch)
    got = changed(before, scoped_keys(t, monkeypatch, change=flip_reads("gpio_set")))
    assert "led_on" in got and "parse_kv" not in got and got == holding(prog, "gpio_set")
    got = changed(before, scoped_keys(t, monkeypatch, change=flip_reads("cmd_volume")))
    assert {"cmd_volume", "dispatch", "handle_key", "main"} <= got and not got & {"cmd_mode", "apply_config", "led_on"}
    assert got == holding(prog, "cmd_volume")


def test_a_call_through_a_pointer_takes_every_functions_facts(monkeypatch):
    """measure calls area() and sides() through the object's vtable (gizmo's C++ build): where such a
    call goes no table and no run of the code tells, and a run may reach any function whose address it
    can make, so the key of measure, and of shapes_demo, which calls it, holds every function's facts;
    parse_kv, which calls through no pointer, keeps its key when a function outside its tree changes."""
    from dread.autocase import reaches
    from dread.program import Program
    t = Target.load(gizmo_dir("m3cpp"))
    prog = Program(t)
    measure = "_ZL7measurePK5Shape"
    graph = reaches(prog)
    assert graph[measure][1] and "checksum" not in graph[measure][0] and not graph["parse_kv"][1]
    before = scoped_keys(t, monkeypatch)
    got = changed(before, scoped_keys(t, monkeypatch, change=flip_reads("checksum")))
    assert {measure, "shapes_demo", "checksum"} <= got and "parse_kv" not in got
    assert got == holding(prog, "checksum")


def test_case_keys_cover_what_regflow_adds(target, monkeypatch):
    """The key reads regflow's findings through their case facts (RegFlow.case_facts: the result's JSON
    without what no case reads), whatever fields they have: a table by function name added later is in
    each function's facts, so gpio_set's entry renews the keys of the trees that hold it; a field that
    is no such table is image-wide and renews every key."""
    from dread.program import Program

    def adding(extra):
        def change(flow):
            base = flow.to_json
            flow.to_json = lambda: dict(base(), **extra)
        return change
    before = scoped_keys(target, monkeypatch)
    got = changed(before, scoped_keys(target, monkeypatch, change=adding({"must_write": {"gpio_set": 3}})))
    assert got == holding(Program(target), "gpio_set")
    got = changed(before, scoped_keys(target, monkeypatch, change=adding({"must_write": {"0x1234": 3}})))
    assert got == set(before)


def test_case_keys_leave_out_what_no_case_reads(target, monkeypatch):
    """regflow's overwritten (since version 25: the registers a function writes on every path, which the
    readable C reads for its arguments) is no case fact (RegFlow.case_facts), and a change of it alone
    renews no key, of gpio_set's trees or any other; the same change in reads renews those trees'."""
    from dread.program import Program

    def overwriting(name):
        def change(flow):
            flow.overwritten = dict(flow.overwritten or {})
            flow.overwritten[name] = flow.overwritten.get(name, 0) ^ 1
        return change

    def all_overwritten(flow):
        flow.overwritten = {name: 0xF for name in flow.reads}
    before = scoped_keys(target, monkeypatch)
    assert scoped_keys(target, monkeypatch, change=overwriting("gpio_set")) == before
    assert scoped_keys(target, monkeypatch, change=all_overwritten) == before
    got = changed(before, scoped_keys(target, monkeypatch, change=flip_reads("gpio_set")))
    assert got and got == holding(Program(target), "gpio_set")


def test_components_of_a_call_graph():
    """Tarjan's components, each after every component it reaches (a scope is digested from those below
    it), cycles and self-calls one component each."""
    from dread.autocase import components
    graph = {"a": {"b"}, "b": {"c", "a"}, "c": set(), "d": {"c", "e"}, "e": {"d", "x"}, "f": {"f"}}
    got = components(graph)
    assert sorted(got) == [["a", "b"], ["c"], ["d", "e"], ["f"]] and got == components(dict(graph))
    order = {n: k for k, members in enumerate(got) for n in members}
    assert order["c"] < order["a"] and order["c"] < order["d"]
    chain = {"n%05d" % i: {"n%05d" % (i + 1)} for i in range(20000)}     # (deep: no recursion)
    chain["n20000"] = set()
    assert len(components(chain)) == 20001


TREE_T2_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global leaf, other, call_leaf, tail_other, via_pointer, via_svc, use
    .type leaf, %function
    .thumb_func
leaf:
    adds    r0, r0, #1
    bx      lr
    .size leaf, . - leaf
    .type other, %function
    .thumb_func
other:
    adds    r0, r0, #2
    bx      lr
    .size other, . - other
    .type call_leaf, %function
    .thumb_func
call_leaf:
    push    {r4, lr}
    bl      leaf
    pop     {r4, pc}
    .size call_leaf, . - call_leaf
    .type tail_other, %function
    .thumb_func
tail_other:
    b.w     other
    .size tail_other, . - tail_other
    .type via_pointer, %function
    .thumb_func
via_pointer:
    push    {r4, lr}
    blx     r1
    pop     {r4, pc}
    .size via_pointer, . - via_pointer
    .type via_svc, %function
    .thumb_func
via_svc:
    svc     #0
    bx      lr
    .size via_svc, . - via_svc
    .type use, %function
    .thumb_func
use:
    push    {r4, lr}
    bl      call_leaf
    bl      tail_other
    bl      via_pointer
    bl      via_svc
    pop     {r4, pc}
    .size use, . - use
"""


@pytest.mark.skipif(not (__import__("shutil").which("arm-none-eabi-as") and __import__("shutil").which("arm-none-eabi-gcc")),
                    reason="needs arm-none-eabi-as and -gcc")
def test_case_keys_of_calls_tail_calls_pointers_and_system_calls_on_arm(tmp_path, monkeypatch):
    """A call (bl leaf) and a tail call (b.w other) bring their callee's facts into the key; a call through
    a register (blx r1) takes every function's; a system call (svc, a logged no-op in a case's run)
    reaches no function, and leaves via_svc's key alone."""
    from dread.autocase import reaches
    from dread.program import Program
    from test_lift_results import arm_program
    t = arm_program(tmp_path, "t2", source=TREE_T2_S)
    graph = reaches(Program(t))
    assert graph["call_leaf"] == ({"leaf"}, False) and graph["tail_other"] == ({"other"}, False)
    assert graph["via_pointer"][1] and graph["via_svc"] == (set(), False)
    before = scoped_keys(t, monkeypatch)
    assert changed(before, scoped_keys(t, monkeypatch, change=flip_reads("leaf"))) == \
        {"leaf", "call_leaf", "via_pointer", "use"}
    assert changed(before, scoped_keys(t, monkeypatch, change=flip_reads("other"))) == \
        {"other", "tail_other", "via_pointer", "use"}


TREE_RV_S = """
    .option norelax
    .text
    .global leaf, other, saver, via_millicode, odd_saver, via_odd, tail_other, via_pointer, via_ecall, use
    .type leaf, @function
leaf:
    addi    a0, a0, 1
    ret
    .size leaf, . - leaf
    .type other, @function
other:
    addi    a0, a0, 2
    ret
    .size other, . - other
    .type saver, @function
saver:                          # GCC's millicode: entered with jal t0, back through t0
    addi    sp, sp, -16
    sw      ra, 12(sp)
    jr      t0
    .size saver, . - saver
    .type via_millicode, @function
via_millicode:
    jal     t0, saver
    call    leaf
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size via_millicode, . - via_millicode
    .type odd_saver, @function
odd_saver:                      # back through t0, but called with ra: t0 holds what the caller left
    jr      t0
    .size odd_saver, . - odd_saver
    .type via_odd, @function
via_odd:
    addi    sp, sp, -16
    sw      ra, 12(sp)
    call    odd_saver
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size via_odd, . - via_odd
    .type tail_other, @function
tail_other:
    tail    other
    .size tail_other, . - tail_other
    .type via_pointer, @function
via_pointer:
    addi    sp, sp, -16
    sw      ra, 12(sp)
    jalr    a1
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size via_pointer, . - via_pointer
    .type via_ecall, @function
via_ecall:
    ecall
    ret
    .size via_ecall, . - via_ecall
    .type use, @function
use:
    addi    sp, sp, -16
    sw      ra, 12(sp)
    call    via_millicode
    call    via_odd
    call    tail_other
    call    via_pointer
    call    via_ecall
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size use, . - use
"""


@needs_riscv_gcc
def test_case_keys_of_millicode_and_system_calls_on_risc_v(tmp_path, monkeypatch):
    """GCC's millicode (-msave-restore) is entered with jal t0, __riscv_save_N and goes back with jr t0:
    a jump through a register, but through the one every call of it leaves its return address in, and
    that it never writes, so it returns behind the call and opens nothing (open, it opened a seventh of
    the trees of four FreeRTOS builds). The same jump in a function called with ra goes where t0 happens
    to point: open. ecall (a logged no-op in a case's run) reaches no function; a tail call (tail other:
    auipc and jr) brings its callee's facts in; jalr a1 takes every function's."""
    import subprocess
    from dread import elfimport
    from dread.autocase import reaches
    from dread.program import Program
    from conftest import RISCV_CROSS, RISCV_MARCH, riscv_toml
    from test_lift_results import RV_GCC, RV_TOML
    (tmp_path / "t.s").write_text(TREE_RV_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run([RISCV_CROSS + "gcc", "-march=" + RISCV_MARCH, "-mabi=ilp32", "-c", "-o", obj, str(tmp_path / "t.s")],
                   check=True)
    subprocess.run([RISCV_CROSS + "ld", "-m", "elf32lriscv", "-Ttext=0x20000000", "-e", "use", "-o", elf, obj],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml(RV_TOML + RV_GCC))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    graph = reaches(Program(t))
    assert graph["saver"] == (set(), False) and graph["via_millicode"] == ({"saver", "leaf"}, False)
    assert graph["odd_saver"][1] and graph["via_pointer"][1]
    assert graph["via_ecall"] == (set(), False) and graph["tail_other"] == ({"other"}, False)
    before = scoped_keys(t, monkeypatch)
    assert changed(before, scoped_keys(t, monkeypatch, change=flip_reads("leaf"))) == \
        {"leaf", "via_millicode", "odd_saver", "via_odd", "via_pointer", "use"}
    assert changed(before, scoped_keys(t, monkeypatch, change=flip_reads("other"))) == \
        {"other", "tail_other", "odd_saver", "via_odd", "via_pointer", "use"}


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_loop_cases_for_a_function_that_never_returns(tmp_path):
    """A task's loop gets no case that returns. Loop cases stub its callees and end at the same stub
    call in both images, or at the call of a callee that never returns (its stub ends the run, so
    the C's endless loop behind that call is never compared); together they run all of it."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.emu import EmuError
    (tmp_path / "t.c").write_text(NOIPA_DEF + "volatile int flag;\n"
                                  "NOIPA void fail(void) { for (;;) ; }\n"
                                  "NOIPA int poll(int x) { return x + flag; }\n"
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
    """A memory cell that overlaps a known one is left out, unless it holds the known ones whole, or
    is aligned where they are not, and then replaces them: every input writes its cells one after the
    other, and overlapping cells (a learned pointer one byte off read the same buffer) wrote over each
    other's bytes, so that the value the search gave the first never reached the function (FreeRTOS's
    vListInsert); a misaligned cell learned first kept the aligned word out (C++ on RISC-V)."""
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
    cells.learn({0x3002: 4, 0x3000: 4, 0x3004: 4})                  # a pointer two bytes off read first:
    assert dict(cells) == {0x1000: 4, 0x2000: 4, 0x2004: 4, 0x3000: 4, 0x3004: 4}     # the aligned words win
    cells.add(0x2002, 4)                                            # misaligned over aligned: left out
    cells.add(0x2000, 2)                                            # aligned, but inside an aligned cell
    assert dict(cells) == {0x1000: 4, 0x2000: 4, 0x2004: 4, 0x3000: 4, 0x3004: 4}
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


CHAIN_C = """
struct node { unsigned pad[2]; struct node *next; unsigned value; };
struct list { unsigned count; unsigned pad; struct node *end; };
__attribute__((noinline)) unsigned put_last(struct list *l, unsigned *key)
{
    if (*key == 0xFFFFFFFFu) {          /* behind a compare: the end's next node gets the key */
        l->end->next->next->value = *key;
        return 1;
    }
    l->count++;
    return 0;
}
"""


def test_a_near_miss_is_chased_through_pointers_no_input_set(tmp_path, monkeypatch):
    """The compare-guided stage solves *key == -1, and the run then follows l->end->next->next, words no
    input set (0: the image lies at 0, as on FreeRTOS's MPS2 builds, so the run reads and writes the
    image instead of faulting, and the main stage learns no pointer from it): a near miss. Chased, those words point to
    buffers of their own, one pointer deeper a step, and the path is covered; without the chase it is
    not (FreeRTOS's vListInsert, whose path behind its item's value 0xFFFFFFFF reaches a list
    pointer nobody had set)."""
    import subprocess
    from test_bench import TARGET
    from dread import autocase, elfimport
    (tmp_path / "t.c").write_text(CHAIN_C)
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0, LENGTH = 64K\n"     # (flash at 0, as on
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"    # the MPS2 boards)
                                   "SECTIONS { .text : { *(.text*) } > FLASH .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "put_last", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET.replace("base = 0x08000000, size = 0x100000", "base = 0, size = 0x100000")
                                         + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    target = Target.load(str(tmp_path))

    def missed(chase):
        gen = Generator(target, target.image())
        if not chase:
            gen.chase_buffers = []
        fn = gen.program.by_name["put_last"]
        got = set()
        for c in gen.generate("put_last", fn.entry, fn.insns):
            got |= gen.coverage(fn.entry, fn.insns, c, c.get("limit", 200_000))
        return set(fn.insns) - got
    if not missed(False):                   # (GCC 4.9's code of put_last is covered without the chase)
        pytest.skip("this compiler's code needs no chase: the test would show nothing")
    assert missed(True) == set()


READER_C = NOIPA_DEF + """
volatile int status;
NOIPA int receive(char *buf, int n) { buf[0] = 0; return n; }  /* (fills it) */
__attribute__((noreturn)) void reader(void)
{
    char buf[8];
    for (;;) {
        __builtin_memset(buf, 0, sizeof buf);   /* (not what lay on the stack: the stub's bytes) */
        receive(buf, 8);
        if (buf[0] == 'H' && buf[1] == 'i')     /* what the stub wrote into the local buffer */
            status = 1;
        else
            status = 2;
    }
}
"""


def test_a_loop_case_stub_fills_the_buffer_it_is_handed(tmp_path):
    """A task's loop cases stub its callees; with the task's frame (Generator.loop_frame: its deepest
    stack, dread.lift's analysis) a stub writes into a local the task hands it, and the compare-guided
    stage sets those bytes: the path behind 'H' and 'i' is covered. With no frame the stub writes
    nothing there and it is not (FreeRTOS's prvReceivingTask: 31 of its 64 instructions)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    (tmp_path / "t.c").write_text(READER_C)
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "reader", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    target = Target.load(str(tmp_path))
    gen = Generator(target, target.image())
    fn = gen.program.by_name["reader"]
    assert gen.loop_frame("reader") >= 8
    insns = [gen.program.insn(a) for a in fn.insns]
    branches = [i.address for i in insns if gen.arch.flow(i)[0] == "jump" and gen.arch.predicate(i) is not None]
    deciders = gen.arch.branch_deciders(insns)
    assert branches and all(b in deciders for b in branches), (branches, deciders)  # (GCC loads a literal
    # between the cmp and its bne here: the setter is found behind it)

    def missed(frame):
        got = set()
        for c in gen.generate("reader", fn.entry, fn.insns, loop=True, frame=frame):
            got |= gen.coverage(fn.entry, fn.insns, c, c.get("limit", 200_000))
        return set(fn.insns) - got
    assert missed(gen.loop_frame("reader")) == set()
    assert missed(0)


def test_the_null_stage_reaches_a_null_check_no_other_stage_offers(tmp_path, monkeypatch):
    """FreeRTOS's vTaskSetTimeOutState and xQueueReceive assert their pointer argument and store
    through it after. With it 0 and the assertion handler stubbed, the run goes on into the image at
    address 0 (the MPS2 builds map flash there), and since generator 32 that store makes the argument
    a learned pointer at once, which takes its buffer in nine of ten random inputs and a random value
    otherwise, rarely 0. The sweep tries 0 only in the few slots it samples; the compare-guided stage
    flipped the check to 0, but its runs waited in the handler (it waits for a debugger on a local:
    it may return, so it is no stop) and were dropped. measure-35 lost such functions on every build.
    Here the random routes to a null input are closed (learned pointers always take their buffer, no 0
    among the constants), as they were in effect there: generator 32 left the assertion's call unrun;
    generator 33's null stage (each argument 0 once, ending at the callees runs hung in) and the
    compare-guided stage's retry with a stop reach it."""
    import subprocess
    from test_bench import TARGET
    from dread import autocase, elfimport
    (tmp_path / "t.c").write_text(NOIPA_DEF +
                                  "NOIPA void assert_failed(int line) { volatile int go = 0; while (!go) ; }\n"
                                  "void set_state(int *p) { if (!p) assert_failed(5510); p[0] = 7; p[1] = 9; }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH\n .bss : { *(.bss*) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "set_state", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    target = TARGET.replace('{name = "flash", base = 0x08000000,', '{name = "flash", base = 0x00000000,')
    (tmp_path / "dread.toml").write_text(target + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x0\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    monkeypatch.setattr(autocase, "POINTER_KEEP", 1.0)
    monkeypatch.setattr(autocase, "SMALL_ARGS", (1, 2, 3))
    real_ladder = autocase.Generator.ladder
    monkeypatch.setattr(autocase.Generator, "ladder",
                        lambda self, words, rng=None: [v for v in real_ladder(self, words, rng) if v] or [1])
    real_constants = autocase.Generator.constants
    monkeypatch.setattr(autocase.Generator, "constants",
                        lambda self, words: tuple({v for v in part if v} for part in real_constants(self, words)))
    t = Target.load(str(tmp_path))
    g = Generator(t, t.image())
    fn = g.program.by_name["set_state"]
    cases = g.generate("set_state", fn.entry, fn.insns)
    got, words = covered(g, "set_state", cases)
    assert got == words, sorted(hex(a) for a in words - got)


def test_cases_made_anew_keep_what_the_last_ones_reached(gen, tmp_path, monkeypatch):
    """When a function's cases are made anew (another key: a new generator, a changed image), the
    inputs of the last generation that still run as a case must and reach instructions the new ones
    do not are kept, marked carried: every measurement that made the cases anew lost functions it had
    had (rare paths one search found and the next missed: measure-35 and -36, 2 to 7 a build). An old
    case that stubs an address no function starts at is not kept."""
    import os
    from dread import autocase
    fn = gen.program.by_name["parse_uint"]
    full = gen.generate("parse_uint", fn.entry, fn.insns)
    reach = [gen.coverage(fn.entry, fn.insns, c) for c in full]
    every = set().union(*reach)
    assert every == set(fn.insns)
    # a poorer generation: the cases that reach the least, until something is missed
    poor = [c for c, r in sorted(zip(full, reach), key=lambda p: len(p[1]))][:1]
    assert set().union(*(gen.coverage(fn.entry, fn.insns, c) for c in poor)) < every
    bogus = dict(full[0], calls=[[fn.entry + 2, 1, 0]])     # (a stub where no function starts)
    kept = gen.carried(fn.entry, fn.insns, poor, [bogus] + full)
    assert kept and all(c.get("carried") for c in kept)
    assert all(c["calls"] != [[fn.entry + 2, 1, 0]] for c in kept)
    got = set().union(*(gen.coverage(fn.entry, fn.insns, c) for c in poor + kept))
    assert got == every
    # and cases_for does it when the key changes: a cache file of another key, a generation that
    # makes the poor cases
    from dread.cases import Suite
    from dread.emu import Layout
    from dread.program import Program
    target = gen.target
    target.build = str(tmp_path / "build")          # not the example's own cache
    image = target.image()
    image.extra_cases, image.kept_cases = str(tmp_path / "extra"), str(tmp_path / "kept")
    program, layout = Program(target, image), Layout(target, image)
    suite = Suite(target, image, program, layout)
    suite.auto["parse_uint"] = {}
    run = {"target": target, "image": image, "program": program, "layout": layout, "original": image.payload(),
           "suite": suite}
    path = os.path.join(target.build, "autocases", image.name, "parse_uint.json")
    autocase.cache_store(path, {"key": "an older generator's", "cases": full})
    monkeypatch.setattr(autocase.Generator, "generate", lambda self, *a, **k: [dict(c) for c in poor])
    monkeypatch.setattr(autocase, "_GENERATORS", {})
    cases = [c for *_, c in autocase.cases_for(run, "parse_uint", program.by_name["parse_uint"]) if c]
    assert any(c.get("carried") for c in cases) and len(cases) > len(poor)
    assert autocase.cache_load(path)["key"] != "an older generator's"


def test_a_long_loop_round_is_run_past_the_48th_call(tmp_path):
    """FreeRTOS's test tasks call 64 to 175 times a round of their loop, and the checks that set
    xErrorDetected lie behind most of those calls; a loop case ended at its 48th stub call, so no case
    reached them (prvLowPriorityMutexTask, vPrimaryBlockTimeTestTask and others stayed uncovered on
    every build). A loop case now ends at twice the function's call sites (at least 48, at most
    512), and the compare-guided stage solves stub answers that far."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    steps = " ".join("step(%d);" % k for k in range(60))
    (tmp_path / "t.c").write_text(NOIPA_DEF + "volatile int err;\n"
                                  "NOIPA int step(int k) { return k + 1; }\n"
                                  "NOIPA int check(void) { return 1; }\n"
                                  "__attribute__((noreturn)) void task(void) { for (;;) { %s if (check() != 1) err = 1; } }\n"
                                  % steps)
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
    fn = g.program.by_name["task"]
    assert g.loop_calls_for(fn.insns) > 48
    cases = g.generate("task", fn.entry, fn.insns, loop=True)
    got, words = covered(g, "task", cases)
    assert got == words, sorted(hex(a) for a in words - got)


FRAGILE_C = NOIPA_DEF + """
volatile unsigned sink;
NOIPA void copy(char *dst, const char *src, unsigned n) { while (n--) *dst++ = *src++; }
unsigned user(const char *src, unsigned n, unsigned k)
{ char buf[4]; copy(buf, src, n); if (k == 7) sink = buf[1]; return k * 5 + buf[0]; }
"""


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_case_that_touches_saved_registers_stands_for_nothing(tmp_path):
    """user copies n bytes into a 4-byte local and stores to sink only when k is 7. An input with n 8
    reaches that store but writes over user's saved r4 (Generator.fragile: the lift check refuses such
    a case when the images differ, and then nothing runs the store). The firm-up adds the input with n
    small (marked firm), and carried keeps an earlier generation's input with n 2 that runs the store
    although a fragile new case runs it too (measure-38 lost prvNotifyQueueSetContainer so)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    (tmp_path / "t.c").write_text(FRAGILE_C)
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH\n .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "user", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    g = Generator(t, t.image())
    fn = g.program.by_name["user"]
    text = [[0x20100000 + i, 1, 0x41 + i] for i in range(16)]

    def case(n, k):
        return {"args": [0x20100000, n, k], "mem": [list(x) for x in text], "returns": [], "calls": []}
    smash, calm, earlier = case(8, 7), case(2, 1), case(2, 7)
    why = g.fragile(fn.entry, smash)
    assert why and "saved" in why, why
    assert g.fragile(fn.entry, calm) is None and g.fragile(fn.entry, earlier) is None
    store = covered(g, "user", [smash])[0] - covered(g, "user", [calm])[0]
    assert store and store <= covered(g, "user", [earlier])[0]
    firm = g.firm_up(fn.entry, fn.insns, [smash, calm])
    assert firm and all(c.get("firm") and c["args"][2] == 7 for c in firm), firm
    assert store <= covered(g, "user", firm)[0] and all(g.fragile(fn.entry, c) is None for c in firm)
    assert g.firm_up(fn.entry, fn.insns, [smash, calm, earlier]) == []     # (a sturdy case runs the store)
    kept = g.carried(fn.entry, fn.insns, [smash, calm], [earlier])
    assert [c["args"] for c in kept] == [[0x20100000, 2, 7]] and kept[0]["carried"], kept


@pytest.mark.slow
@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_code_only_a_dropped_case_reached_is_sought_again(tmp_path, monkeypatch):
    """A case that should end at a call (a callee runs hung in) and spins on a device register before
    it (a peripheral's status) is dropped from the result (drop_bad_ends), but its run counted
    while the search ran, and no other input was sought for what only it had reached, which stayed
    uncovered. The repair (repair_lost) seeks it again from what the surviving cases cover. The test
    hands the repair that situation: the good cases that reach work's call taken out, such a
    spinning case put in, the call counted as covered."""
    import subprocess
    from test_bench import TARGET
    from dread import autocase, elfimport
    (tmp_path / "t.c").write_text(NOIPA_DEF +
                                  "volatile unsigned char state;\nvolatile int go;\n"
                                  "#define READY (*(volatile unsigned *)0x40000100u)\n"
                                  "NOIPA void finish(void) { while (!go) ; }\n"
                                  "NOIPA void wait_ready(void) { while (READY != 0x5a5a1234u) ; }\n"
                                  "NOIPA int work(int n) { wait_ready(); finish(); return n + 1; }\n"
                                  "int f(int x) { if (x == 7) finish(); if (state == 0) return 1;\n"
                                  "    if (state == 1) return work(0xff); return 2; }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH\n .bss : { *(.bss*) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "f", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    g = Generator(t, t.image())
    p = g.program
    fn, work, finish = p.by_name["f"], p.by_name["work"], p.by_name["finish"]
    call = next(a for a in sorted(fn.insns) if p.arch.flow(p.insn(a))[0] in ("call", "jump")
                and p.arch.flow(p.insn(a))[1] is not None and p.arch.flow(p.insn(a))[1] & ~1 == work.entry)
    state = next(s.value for s in __import__("dread.elf", fromlist=["Elf"]).Elf(elf).symbols if s.name == "state")
    spinning = {"args": [1, 0, 0, 0], "mem": [[state, 1, 1], [0x40000100, 4, 0]], "returns": [], "calls": [],
                "frame": 0, "hang": True, "limit": 200_000, "stops": [finish.entry]}
    assert not g.survives(fn.entry, spinning)               # (it spins in wait_ready before it gets to finish)
    seen = {}
    real = autocase.Generator.repair_lost

    def handed(self, name, entry, words, end, calls, limit, rng, pool, known, covered, corpus, extras, reach,
               edge, hung):
        for cases in (corpus, extras):
            cases[:] = [c for c in cases if call not in self.coverage(entry, words, c, c.get("limit", limit))]
        reach.clear()
        corpus.append(dict(spinning))
        covered.add(call)
        seen["handed"] = True
        return real(self, name, entry, words, end, calls, limit, rng, pool, known, covered, corpus, extras, reach,
                    edge, hung)
    monkeypatch.setattr(autocase.Generator, "repair_lost", handed)
    cases = g.generate("f", fn.entry, fn.insns)
    got, words = covered(g, "f", cases)
    assert seen and call in got, sorted(hex(a) for a in words - got)


# The frontier answers (Generator.answers): behind a gate no generated input passes (the top bits of
# key * 0x9E3779B1 against a constant: no compare to solve, no constant to try), a written case that
# passes it reaches a branch on a global, one on a real callee's result and one on a stubbed callee's
# answer, and takes each one way. Only the answers take the other way.
GATE_KEY = 0x12345678
GATE_TOP = ((GATE_KEY * 0x9E3779B1) & 0xFFFFFFFF) >> 7
GATED_C = NOIPA_DEF + """
volatile unsigned gated_limit, gated_out_a, gated_out_b, gated_out_c;
NOIPA unsigned make_odd(unsigned k) { return k | 1u; }                   /* odd, whatever k is */
NOIPA unsigned spin_for_debugger(unsigned k) { volatile unsigned go = 0; while (!go) ; return k + go; }
NOIPA unsigned add_three(unsigned k) { return k + 3u; }
#define GATE(key) (((key) * 0x9E3779B1u) >> 7 != 0x%xu)
#define WORK(k) do { gated_out_a = (k); gated_out_b = (k) ^ 0x55u; gated_out_c = (k) + 9u; \\
                     return add_three(k); } while (0)
unsigned gated_by_global(unsigned key) { if (GATE(key)) return 1;
    if (gated_limit == 0xC0FFEE11u) WORK(key); return 2; }
unsigned gated_by_callee(unsigned key) { if (GATE(key)) return 1;
    if ((make_odd(key) & 1u) == 0) WORK(key); return 3; }
unsigned gated_by_stub(unsigned key) { if (GATE(key)) return 1;
    if (spin_for_debugger(key) == 0x31337u) WORK(key); return 4; }
unsigned make_odd_twice(unsigned k) { return make_odd(k) + 2 * make_odd(k + 1); }
""" % GATE_TOP


def gated_target(tmp_path):
    """The gated program's target (with what a lift check needs) and the written cases that pass the
    gate, in its extra_cases: spin_for_debugger, which waits for a debugger, stubbed in
    gated_by_stub's."""
    import os
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    (tmp_path / "t.c").write_text(GATED_C)
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH\n .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "gated_by_global", "-o", elf, str(tmp_path / "t.c")],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mcpu=cortex-m3", "-mthumb", "-Os", "-ffunction-sections"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    spin = Program(t, t.image()).by_name["spin_for_debugger"].entry
    written = {"gated_by_global": {"args": [GATE_KEY, 0, 0, 0], "mem": [], "returns": [], "calls": [], "frame": 0},
               "gated_by_callee": {"args": [GATE_KEY, 0, 0, 0], "mem": [], "returns": [], "calls": [], "frame": 0},
               "gated_by_stub": {"args": [GATE_KEY, 0, 0, 0], "mem": [], "returns": [0], "calls": [[spin, 1, 0]],
                                 "frame": 0}}
    os.makedirs(t.image().extra_cases, exist_ok=True)
    for name, case in written.items():
        with open(os.path.join(t.image().extra_cases, name + ".json"), "w") as f:
            json.dump({"cases": [case]}, f)
    return t, written


def answered(gen, name, written):
    """(what the generated and written cases cover, what the answers add to that, the answer cases, the
    function's instructions)."""
    fn = gen.program.by_name[name]
    bases = gen.generate(name, fn.entry, fn.insns) + [written]
    before = set().union(*(gen.coverage(fn.entry, fn.insns, c) for c in bases))
    new = gen.answers(name, fn.entry, fn.insns, bases)
    after = set().union(before, *(gen.coverage(fn.entry, fn.insns, c) for c in new))
    return before, after, new, set(fn.insns)


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_answers_give_a_global_what_the_branch_needs(tmp_path):
    """gated_by_global's written case reads gated_limit (0 in the image) and returns 2. dread.uncovered's
    trace names the load of gated_limit as what decides the branch; the answer sets the memory that load
    read to the compare's other operand, and the code behind the branch runs."""
    t, written = gated_target(tmp_path)
    gen = Generator(t, t.image())
    before, after, new, words = answered(gen, "gated_by_global", written["gated_by_global"])
    assert before < words and after == words, sorted(hex(a) for a in words - after)
    from dread.elf import Elf
    limit = next(s.value for s in Elf(str(tmp_path / "t.elf")).symbols if s.name == "gated_limit")
    assert [limit, 4, 0xC0FFEE11] in new[0]["mem"] and new[0].get("answer"), new


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_answers_stub_one_call_of_a_real_callee(tmp_path):
    """gated_by_callee tests make_odd's result, which is odd whatever its argument: a real callee that
    answers one way. The answer stubs make_odd at that one call (the fourth element of the case's calls:
    the callee runs at its other calls) with an even value, and the code behind the branch runs."""
    t, written = gated_target(tmp_path)
    gen = Generator(t, t.image())
    before, after, new, words = answered(gen, "gated_by_callee", written["gated_by_callee"])
    assert before < words and after == words, sorted(hex(a) for a in words - after)
    make_odd = gen.program.by_name["make_odd"].entry
    stubs = [c for c in new[0]["calls"] if c[0] == make_odd]
    assert stubs and stubs[0][3] == [1] and new[0]["returns"][0] % 2 == 0, new


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_answers_give_a_stubbed_callee_the_answer_the_branch_needs(tmp_path):
    """gated_by_stub's written case stubs spin_for_debugger (it waits for a debugger) with the answer 0.
    The answer gives the stub the compare's other operand at that call, and the code behind the branch
    runs."""
    t, written = gated_target(tmp_path)
    gen = Generator(t, t.image())
    before, after, new, words = answered(gen, "gated_by_stub", written["gated_by_stub"])
    assert before < words and after == words, sorted(hex(a) for a in words - after)
    assert new[0]["returns"][0] == 0x31337, new


@pytest.mark.slow
@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_lift_check_accepts_what_the_answers_cover(tmp_path, monkeypatch):
    """The three gated functions with their written cases: without the answers each misses the code
    behind its branch and is dropped; with them the lift check accepts all three (the lifted C agrees
    with the original under the same answers, a stub that answers one call of make_odd and lets it run
    at the others included), and the answers are cached beside the generated cases under a key of
    their own."""
    from dread import autocase, lift
    t, _ = gated_target(tmp_path)
    names = ["gated_by_callee", "gated_by_global", "gated_by_stub"]
    monkeypatch.setattr(autocase, "ANSWER_RUNS", 0)
    monkeypatch.setattr(autocase, "_GENERATORS", {})
    lines = []
    assert lift.check(t, t.image(), names, out=lines.append, jobs=1, system=False) == []
    monkeypatch.setattr(autocase, "ANSWER_RUNS", 400)
    monkeypatch.setattr(autocase, "ANSWERS", autocase.ANSWERS + 1)     # (the empty answers cached are stale)
    monkeypatch.setattr(autocase, "_GENERATORS", {})
    lines = []
    assert sorted(lift.check(t, t.image(), names, out=lines.append, jobs=1, system=False)) == names, "\n".join(lines)
    import os
    entry = autocase.cache_load(os.path.join(t.build, "autocases", "main", "gated_by_callee.json"))
    assert entry["answers"]["cases"] and all(c.get("answer") for c in entry["answers"]["cases"])


@pytest.mark.skipif(not __import__("shutil").which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_stub_that_answers_one_call_counts_a_moved_callee_once(tmp_path):
    """In a rebuilt image a callee may lie elsewhere with a branch at its old entry, and a stub is hit
    at both when the callee runs through it: a call let run at the old entry must not count twice.
    make_odd_twice calls make_odd twice; a case answers the second call only, and the original and an
    image with make_odd moved (its old entry a branch to a copy) agree on the call answered."""
    t, _ = gated_target(tmp_path)
    gen = Generator(t, t.image())
    prog = gen.program
    make_odd, twice = prog.by_name["make_odd"], prog.by_name["make_odd_twice"]
    size = max(make_odd.insns) + prog.insn_size(max(make_odd.insns)) - make_odd.entry
    moved = (t.image().base + len(gen.payload) + 0x40) & ~3
    rebuilt = bytearray(gen.payload) + bytes(moved + size - t.image().base - len(gen.payload))
    lo = make_odd.entry - t.image().base
    rebuilt[moved - t.image().base:moved - t.image().base + size] = gen.payload[lo:lo + size]
    branch = prog.arch.entry_branch(make_odd.entry, moved, prog.isa_at(make_odd.entry))
    rebuilt[lo:lo + len(branch)] = branch
    case = {"args": [10, 0, 0, 0], "mem": [], "returns": [0x100], "calls": [[make_odd.entry, 1, 0, [2]]], "frame": 0}
    results = []
    for payload, aliases in ((gen.payload, None), (bytes(rebuilt), {make_odd.entry: moved})):
        m = gen.machine(payload=payload, aliases=aliases)
        m.plan = []
        gen.apply(m, case)
        results.append((m.call(twice.entry, 10, max_instructions=10_000).ret, list(m.calls)))
    assert results[0] == results[1] and results[0][0] == 11 + 2 * 0x100, results
