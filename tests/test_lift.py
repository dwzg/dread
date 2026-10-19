"""dread lift: what the gizmo builds lift and why the rest is refused, that the lifted C compiles,
and that the acceptance keeps what agrees with the original and drops a planted mistake. On gizmo-rv32
the translator is dread.lift_riscv (dread.lift.make_lifter)."""
import os
import shutil

import pytest

from dread import build, lift, regflow
from dread.program import Program
from dread.target import Target
from conftest import GIZMO, gizmo_dir, have

needs_gcc = pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")


def lift_all(variant):
    target = Target.load(gizmo_dir(variant))
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    lifted, refused = {}, {}
    for fn in program.functions:
        try:
            lifted[fn.name] = lifter.lift(fn)
        except lift.Refused as e:
            refused[fn.name] = str(e)
    return target, image, lifted, refused


def test_refusals_name_their_reason(variant):
    target, image, lifted, refused = lift_all(variant)
    if variant == "rv32":       # the startup sets sp from a constant (la sp, __stack_top)
        assert "changes the stack pointer in a way not modelled: auipc sp" in refused.pop("_start")
        assert refused == {}
        return
    assert "reads r1 behind the call of uart_putc" in refused.pop("uart_puts")     # dread.regflow's finding
    if variant == "a32":
        assert "changes the stack pointer" in refused.pop("reset")
        assert "branches into other code" in refused.pop("__aeabi_uidivmod")
    assert refused == {}


def test_exception_handler_and_main_lift(variant):
    target, image, lifted, refused = lift_all(variant)
    assert "main" in lifted                     # never returns: the system run decides (system_check)
    if variant == "a32":                        # an exception handler: gcc's interrupt attribute
        assert "irq_entry" in lifted and "return;" in lifted["irq_entry"].text
    if variant == "rv32":                       # the trap entry ends in mret: clang's interrupt("machine")
        text = lifted["trap_entry"].text
        assert 'void __attribute__((interrupt("machine"))) trap_entry(void)' in text and "return;" in text


def test_translation_shapes(variant):
    target, image, lifted, refused = lift_all(variant)
    text = lifted["script_run"].text
    assert "switch (index) {" in text and text.count("case ") == (8 if variant == "rv32" else 7)
    assert "u32 frame[" in text and "volatile" in text
    if variant == "rv32":       # the far call into RAM (auipc + jalr) is a call by name; the frame
        assert "a0 = ram_crc(a0, a1, a2, a3, a4, a5, a6, a7);" in lifted["main"].text
        assert "(sp0 - " in lifted["apply_config"].text
        return
    if variant in ("m3", "m4"):
        assert "u32 x = r2, a = r1;" in text                            # lsls r2, r1 shifts r2 by r1
    apply = lifted["apply_config"].text
    assert "(sp0 - " in apply                                           # its line buffer is in the frame
    veneer = lifted["__ram_crc_veneer"].text                            # ldr pc, =ram_crc: a far tail call
    assert "return ((u32 (*)(u32, u32, u32, u32))0x%xu)(r0, r1, r2, r3);" % (
        0x40000000 if variant == "a32" else 0x20000001) in veneer


def test_never_returns(variant):
    from dread import noreturn
    target = Target.load(gizmo_dir(variant))
    expected = {"a32": {"hang", "main", "reset"}, "m3": {"Default_Handler", "Reset_Handler", "main"},
                "m4": {"Default_Handler", "Reset_Handler", "main"},
                "rv32": {"_start", "main"}}[variant]
    assert noreturn.find(Program(target, target.image())) == expected


def test_a_call_that_never_returns_ends_the_path():
    target, image, lifted, refused = lift_all("m3")
    text = lifted["Reset_Handler"].text                 # bl main is its last instruction; a pool follows
    assert "r0 = main(r0, r1, r2, r3); r1 = r2 = r3 = r12 = 0; for (;;) { }" in text


@pytest.mark.compiles
def test_lifted_c_compiles(variant, tmp_path):
    target, image, lifted, refused = lift_all(variant)
    src = tmp_path / "lifted"
    src.mkdir()
    for name, result in lifted.items():
        (src / (name + ".c")).write_text(result.text)
    objects = build.compile_all(target, image, str(src), str(tmp_path / "obj"))
    assert len(objects) == len(lifted)


NAMES = ["script_run", "read_line", "apply_config", "fmt_dec", "timer_irq", "parse_kv"]


@pytest.fixture(scope="module")
def copies(tmp_path_factory):
    """One copy of gizmo for the module, with the generated cases and the register flow the
    example has already cached (they are keyed by what they depend on, so they stay valid)."""
    copy = tmp_path_factory.mktemp("lift") / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    for variant in ("a32", "m3", "m4", "rv32"):
        for cache in ("autocases", "regflow"):
            here = os.path.join(gizmo_dir(variant), "build", cache)
            if os.path.isdir(here):
                shutil.copytree(here, copy / variant / "build" / cache)
    return copy


def copy_of(copies, variant):
    target = Target.load(str(copies / variant))
    return target, target.image()


@pytest.mark.compiles
def test_float_functions_lift_with_the_hard_float_convention(copies):
    """gizmo-m4's float functions: float arguments in s0-s2 (lp_filter, to_millivolts), a float
    result (average), a call of one with a float argument (signal_step). The lifted C mirrors s0-s15
    at its entry, its returns and around its calls, the generated cases give float arguments, and
    every case agrees and runs every instruction."""
    target, image = copy_of(copies, "m4")
    names = ["lp_filter", "to_millivolts", "average", "signal_step"]
    lines = []
    assert sorted(lift.check(target, image, names, out=lines.append, jobs=4)) == sorted(names), lines
    program = Program(target, image)
    text = lift.make_lifter(program, regflow.load(target, image, program)).lift(program.by_name["to_millivolts"]).text
    assert 'vmov %0, s0' in text and 'vcvt.s32.f32 s15, s14' in text and 'vmov s0, %0' in text


@pytest.mark.compiles
def test_a_check_by_name_says_what_left_the_table(tmp_path, capsys):
    """--check with names writes the lifted table with those of them that passed; the functions it
    listed before leave it, and the command says so (checking one function once emptied gizmo-m4's
    table unnoticed)."""
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    for cache in ("autocases", "regflow"):
        here = os.path.join(gizmo_dir("m3"), "build", cache)
        if os.path.isdir(here):
            shutil.copytree(here, copy / "m3" / "build" / cache)
    before = len(build.read_names(str(copy / "m3" / "config" / "lifted.txt")))
    assert lift.main(["--target", str(copy / "m3"), "--check", "fmt_dec"]) == 0
    assert build.read_names(str(copy / "m3" / "config" / "lifted.txt")) == ["fmt_dec"]
    assert "%d it listed before are built from the original's code again" % (before - 1) in capsys.readouterr().out


@pytest.mark.slow
@pytest.mark.compiles
def test_check_accepts_what_agrees(variant, copies):
    target, image = copy_of(copies, variant)
    lines = []
    accepted = lift.check(target, image, NAMES, out=lines.append, jobs=4)
    assert sorted(accepted) == sorted(NAMES), lines
    assert build.read_names(image.table("lifted")) == sorted(NAMES)
    payload = build.build(target, image, quiet=True)                    # dread recon builds them from now on
    assert build.read_names(os.path.join(os.path.dirname(payload), "lifted.txt")) == sorted(NAMES)


@pytest.mark.slow
@pytest.mark.compiles
def test_check_drops_a_planted_mistake(variant, copies, monkeypatch):
    target, image = copy_of(copies, variant)
    if variant == "rv32":       # the unsigned branches reversed: fmt_dec's loop has one, keys_read none
        from dread import lift_riscv
        monkeypatch.setitem(lift_riscv.CONDS, "bltu", "%s > %s")
        monkeypatch.setitem(lift_riscv.CONDS, "bgeu", "%s <= %s")
        lines = []
        accepted = lift.check(target, image, ["fmt_dec", "keys_read"], out=lines.append, jobs=4)
        assert accepted == ["keys_read"], lines
        assert any(line.startswith("dropped  FAIL fmt_dec") for line in lines), lines
        return
    compare = lift.Lifter._compare

    def wrong_carry(self, insn, ops, kind):                             # cmp leaves the carry inverted
        text = compare(self, insn, ops, kind)
        return text.replace("c = (u32)(t >> 32);", "c = !(u32)(t >> 32);") if kind == "cmp" else text
    monkeypatch.setattr(lift.Lifter, "_compare", wrong_carry)
    lines = []
    accepted = lift.check(target, image, ["script_run", "keys_read"], out=lines.append, jobs=4)
    assert accepted == ["keys_read"], lines                             # keys_read compares nothing
    assert any(line.startswith("dropped  FAIL script_run") for line in lines)
    from dread.gui import read_tsv                                      # the record readers such as dread gui use
    verdicts = {r["function"]: r for r in read_tsv(os.path.join(target.build, "lift-check", image.name,
                                                                "verdicts.tsv"))}
    assert verdicts["keys_read"]["verdict"] == "accepted"
    assert verdicts["script_run"]["verdict"] == "dropped" and verdicts["script_run"]["reason"].startswith("FAIL")


@pytest.mark.slow
@pytest.mark.compiles
def test_a_main_loop_is_accepted_by_the_system_run(variant, copies):
    """main never returns, so no case of it ends: the scenarios decide. They agree on both images and
    run all of it only because a due interrupt is taken after a wait in code built from C (main's own
    idle loop): without that, the timer never fires in either image and the key path stays unrun."""
    target, image = copy_of(copies, variant)
    lines = []
    accepted = lift.check(target, image, ["main", "fmt_dec"], out=lines.append, jobs=4, loop_cases=False)
    assert sorted(accepted) == ["fmt_dec", "main"], lines
    assert any(line.startswith("accepted main") and "every scenario agrees" in line for line in lines)
    assert "main  # accepted by the system run" in open(image.table("lifted")).read()
    import json
    from dread import build
    build.build(target, image, quiet=True)          # from the table: main stays the system run's
    with open(os.path.join(build.recon_dir(target, image), "lifted.json")) as f:
        info = json.load(f)
    assert "main" in info["system"] and "main" not in info["loop"]


@pytest.mark.slow
@pytest.mark.compiles
def test_a_main_loop_is_accepted_by_loop_cases(variant, copies):
    """Loop cases: main's callees stubbed, each case compared at the same stub call in both images,
    every instruction run by some case; the system run is not needed for it."""
    target, image = copy_of(copies, variant)
    lines = []
    accepted = lift.check(target, image, ["main", "fmt_dec"], out=lines.append, jobs=4)
    assert sorted(accepted) == ["fmt_dec", "main"], lines
    assert any("(1 by loop cases, 0 by the system run" in line for line in lines), lines[-3:]
    assert "the whole image agrees" in lines[-1], lines[-1]
    marked = open(image.table("lifted")).read()
    assert "\nmain\n" in marked and "accepted by the system run" not in marked


@pytest.mark.slow
@pytest.mark.compiles
def test_loop_cases_and_the_system_run_together(variant, copies):
    """A function that never returns may be accepted by what its loop cases ran (when they all
    agreed) and what the scenarios ran, together; it is built alone for that. In the boot scenario
    alone main never handles a key: with the key path given as run by its loop cases, the two run
    all of it; with loop cases that ran nothing, the key path keeps it out."""
    from dread import sysrun
    target, image = copy_of(copies, variant)
    target.scenarios = {"boot": target.scenarios["boot"]}          # no key comes
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    fn = program.by_name["main"]
    coverage = lift.Coverage(program, ["main"])
    system = sysrun.System(target, image, image.payload(), program=program,
                           scenario=dict(target.scenarios["boot"], _name="boot"))
    system.observers.append(coverage)
    system.run(int(target.scenarios["boot"].get("limit", sysrun.LIMIT)))
    unrun = set(fn.insns) - coverage.ran - set(lifter.lift(fn).unreached)
    assert unrun                                                    # the key path
    lines, verdicts = [], {}
    kept = lift.system_check(target, image, program, lifter, ["main"], out=lines.append, verdicts=verdicts,
                             loop_missed={"main": set(fn.insns) - unrun}, jobs=2)
    assert kept == {"main": "loop cases and the system run"}, lines
    kept = lift.system_check(target, image, program, lifter, ["main"], out=lines.append, verdicts=verdicts,
                             loop_missed={"main": set(fn.insns)}, jobs=2)
    assert kept == {} and "the system run and its loop cases never ran" in verdicts["main"][1], verdicts


@pytest.mark.slow
@needs_gcc
def test_an_exception_handler_is_lifted_with_the_interrupt_attribute(copies):
    """gizmo-a32's IRQ entry (sub lr, lr, #4; push; bl irq_dispatch; ldm sp!, {.., pc}^): gcc writes
    the entry and the exception return, and the scenarios, which take timer interrupts, decide."""
    target, image = copy_of(copies, "a32")
    lifted = lift_all("a32")[2]
    assert 'void __attribute__((interrupt("IRQ"))) irq_entry(void)' in lifted["irq_entry"].text
    lines = []
    assert lift.check(target, image, ["irq_entry"], out=lines.append, jobs=4) == ["irq_entry"], lines
    assert any(line.startswith("accepted irq_entry") and "an exception handler" in line for line in lines)


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_branch_on_a_constant_the_function_stored_is_decided(tmp_path):
    """FreeRTOS's static-creation functions store a size into a volatile local, load it back and
    assert that it equals itself: no input runs the assertion's call, and the functions stayed
    UNCOVERED. The walk knows the constant through the frame slot and the compare; the other way of
    the branch is unreached (left out of the C and of the coverage it needs)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.c").write_text("__attribute__((noipa)) void fail(void) { for (;;) ; }\n"
                                  "int check(int n) { volatile int x = 92; if (x != 92) fail(); return n + 1; }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "check", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    fn = p.by_name["check"]
    fail = p.by_name["fail"].entry
    calls = [a for a in fn.insns if p.arch.flow(p.insn(a))[0] == "call" and p.arch.target(p.insn(a)) == fail]
    assert calls, "the check calls fail"
    result = lift.make_lifter(p).lift(fn)
    assert set(calls) <= set(result.unreached), [hex(a) for a in result.unreached]
    assert "fail" not in result.text
