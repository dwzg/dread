"""dread lift: what the gizmo builds lift and why the rest is refused, that the lifted C compiles,
and that the acceptance keeps what agrees with the original and drops a planted mistake."""
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
    lifter = lift.Lifter(program, regflow.load(target, image, program))
    lifted, refused = {}, {}
    for fn in program.functions:
        try:
            lifted[fn.name] = lifter.lift(fn)
        except lift.Refused as e:
            refused[fn.name] = str(e)
    return target, image, lifted, refused


def test_refusals_name_their_reason(variant):
    target, image, lifted, refused = lift_all(variant)
    assert "reads r1 behind the call of uart_putc" in refused.pop("uart_puts")     # dread.regflow's finding
    if variant == "a32":
        assert "changes the stack pointer" in refused.pop("reset")
        assert "returns from an exception" in refused.pop("irq_entry")              # subs pc, lr, #4
        assert "branches into other code" in refused.pop("__aeabi_uidivmod")
    assert refused == {}


def test_translation_shapes(variant):
    target, image, lifted, refused = lift_all(variant)
    text = lifted["script_run"].text
    assert "switch (index) {" in text and text.count("case ") == 7          # the switch over 0..6
    assert "u32 frame[" in text and "volatile" in text
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
    expected = {"a32": {"hang", "main", "reset"}, "m3": {"Default_Handler", "Reset_Handler", "main"}}[variant]
    assert noreturn.find(Program(target, target.image())) == expected


def test_a_call_that_never_returns_ends_the_path():
    target, image, lifted, refused = lift_all("m3")
    text = lifted["Reset_Handler"].text                 # bl main is its last instruction; a pool follows
    assert "r0 = main(r0, r1, r2, r3); r1 = r2 = r3 = r12 = 0; for (;;) { }" in text


@needs_gcc
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
    for variant in ("a32", "m3"):
        for cache in ("autocases", "regflow"):
            here = os.path.join(gizmo_dir(variant), "build", cache)
            if os.path.isdir(here):
                shutil.copytree(here, copy / variant / "build" / cache)
    return copy


def copy_of(copies, variant):
    target = Target.load(str(copies / variant))
    return target, target.image()


@pytest.mark.slow
@needs_gcc
def test_check_accepts_what_agrees(variant, copies):
    target, image = copy_of(copies, variant)
    lines = []
    accepted = lift.check(target, image, NAMES, out=lines.append, jobs=4)
    assert sorted(accepted) == sorted(NAMES), lines
    assert build.read_names(image.table("lifted")) == sorted(NAMES)
    payload = build.build(target, image, quiet=True)                    # dread recon builds them from now on
    assert build.read_names(os.path.join(os.path.dirname(payload), "lifted.txt")) == sorted(NAMES)


@pytest.mark.slow
@needs_gcc
def test_check_drops_a_planted_mistake(variant, copies, monkeypatch):
    target, image = copy_of(copies, variant)
    compare = lift.Lifter._compare

    def wrong_carry(self, insn, ops, kind):                             # cmp leaves the carry inverted
        text = compare(self, insn, ops, kind)
        return text.replace("c = (u32)(t >> 32);", "c = !(u32)(t >> 32);") if kind == "cmp" else text
    monkeypatch.setattr(lift.Lifter, "_compare", wrong_carry)
    lines = []
    accepted = lift.check(target, image, ["script_run", "keys_read"], out=lines.append, jobs=4)
    assert accepted == ["keys_read"], lines                             # keys_read compares nothing
    assert any(line.startswith("dropped  FAIL script_run") for line in lines)
