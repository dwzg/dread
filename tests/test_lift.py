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
    if variant == "m3":
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
    for variant in ("a32", "m3", "rv32"):
        for cache in ("autocases", "regflow"):
            here = os.path.join(gizmo_dir(variant), "build", cache)
            if os.path.isdir(here):
                shutil.copytree(here, copy / variant / "build" / cache)
    return copy


def copy_of(copies, variant):
    target = Target.load(str(copies / variant))
    return target, target.image()


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
    accepted = lift.check(target, image, ["main", "fmt_dec"], out=lines.append, jobs=4)
    assert sorted(accepted) == ["fmt_dec", "main"], lines
    assert any(line.startswith("accepted main") and "every scenario agrees" in line for line in lines)


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
