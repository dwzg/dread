"""dread callconv: each kind of disagreement with dread.regflow is found, and gizmo agrees."""
import subprocess

import pytest

from dread import autocase, callconv, elfimport
from dread.target import Target
from conftest import gizmo_dir, have

needs_as = pytest.mark.skipif(not have("arm-none-eabi-as"), reason="needs arm-none-eabi-as")

ODD_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global use, reads_r4, clobbers_r5, clobbers_r3, plain
    .type reads_r4, %function
    .thumb_func
reads_r4:                       @ reads what its caller left in r4
        adds    r0, r4, #1
        bx      lr
    .size reads_r4, . - reads_r4
    .type clobbers_r5, %function
    .thumb_func
clobbers_r5:                    @ does not restore r5
        movs    r5, #7
        movs    r0, #0
        bx      lr
    .size clobbers_r5, . - clobbers_r5
    .type clobbers_r3, %function
    .thumb_func
clobbers_r3:                    @ writes the scratch register r3
        movs    r3, #5
        adds    r0, r0, #2
        bx      lr
    .size clobbers_r3, . - clobbers_r3
    .type plain, %function
    .thumb_func
plain:  adds    r0, r0, #1
        bx      lr
    .size plain, . - plain
    .type use, %function
    .thumb_func
use:    push    {r4, r5, lr}
        movs    r4, #1
        bl      reads_r4
        bl      clobbers_r5
        bl      clobbers_r3
        bl      plain
        pop     {r4, r5, pc}
    .size use, . - use
"""


class Claims:
    """dread.regflow's answers, some made wrong on purpose."""

    def __init__(self, real, reads=None, preserved=None):
        self.real, self.reads, self.preserved = real, reads or {}, preserved or {}

    def read_regs(self, name):
        return self.reads.get(name, self.real.read_regs(name))

    def preserved_regs(self, name):
        return self.preserved.get(name, self.real.preserved_regs(name))


@needs_as
def test_each_kind_of_disagreement_is_found(tmp_path):
    """A function that reads its caller's r4 and one that leaves r5 changed are found against the
    real analysis; against answers made wrong (plain reads nothing; clobbers_r3 leaves r3 alone) the
    other two kinds are found; plain agrees with the real analysis."""
    from test_bench import TARGET
    (tmp_path / "t.s").write_text(ODD_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "use", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mthumb", "-mcpu=cortex-m3", "-Os"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    callconv.prepare(t, t.image())
    run, prog = callconv.RUN, callconv.RUN["program"]
    names = ["reads_r4", "clobbers_r5", "clobbers_r3", "plain"]
    for name in names:                      # cases as a lift check would leave them
        run["suite"].auto.setdefault(name, {})
        assert autocase.cases_for(run, name, prog.by_name[name])
    found = {n: callconv.measure(n)[1]["found"] for n in names}
    assert found["reads_r4"] == {"reads its caller's": ["r4"]}, found
    assert found["clobbers_r5"] == {"changes callee-saved": ["r5"]}, found
    assert found["plain"] == {} and found["clobbers_r3"] == {}, found
    run["regflow"] = Claims(run["regflow"], reads={"plain": []})
    run["preserved"] = dict(run["preserved"], clobbers_r3={"r3"})
    assert callconv.measure("plain")[1]["found"] == {"reads, regflow does not": ["r0"]}
    assert callconv.measure("clobbers_r3")[1]["found"] == {"changes, regflow: leaves it": ["r3"]}


@pytest.mark.compiles
def test_gizmo_agrees_with_regflow(variant):
    """Every gizmo build: what each function's cases show of the registers it reads and changes
    agrees with dread.regflow (functions with cached cases only: those a lift check generated)."""
    t = Target.load(gizmo_dir(variant))
    callconv.prepare(t, t.image())
    results = {n: callconv.measure(n)[1] for n in [fn.name for fn in callconv.RUN["program"].functions]}
    results = {n: r for n, r in results.items() if r is not None}
    assert {n: r["found"] for n, r in results.items() if r["found"]} == {}
