"""A function whose original dispatches through GCC's Thumb-1 switch helper (bl __gnu_thumb1_case_uqi,
the table behind the call). The helper, another function, reads a byte of its caller's extent, which
the rule against loading instructions as data (dread.emu trace_reads with dread.program extent_owners)
took for code until 2026-10-25: every input that reached the dispatch was refused, no case ran the
switch, and the function was dropped for the code its cases left unrun. The table is the caller's
constants now. A switch of our own in C, built for the Cortex-M0 at -Os with libgcc, so that the image
has the helper."""
import shutil
import subprocess

import pytest

from dread import elfimport, lift
from dread.elf import Elf
from dread.target import Target
from test_lift_link import TOML, verdicts

needs_gcc = pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")

SWITCH_C = r"""
__attribute__((noinline)) int pick(unsigned k, int *out)
{
    switch (k) {
    case 0: out[0] = 10; break;
    case 1: out[1] = out[0] + 20; break;
    case 2: out[2] = out[1] * 2; break;
    case 3: out[3] = 3; break;
    case 4: out[4] = out[2] - 7; break;
    case 5: out[5] = out[3] ^ 0x55; break;
    case 6: out[6] = -out[4]; break;
    default: return -1;
    }
    return (int)k;
}

__attribute__((noreturn)) void use(void)
{
    int b[8];
    for (unsigned k = 0;; k++)
        pick(k & 7, b);
}
"""


def program(tmp_path):
    """The program built for the Cortex-M0 at -Os with libgcc and imported: (target, its ELF's symbols)."""
    (tmp_path / "t.c").write_text(SWITCH_C)
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mthumb", "-mcpu=cortex-m0", "-Os", "-ffreestanding", "-nostdlib",
                    "-Wl,-Ttext=0x08000000", "-e", "use", "-o", elf, str(tmp_path / "t.c"), "-lgcc"], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text("\n".join(l for l in TOML.splitlines() if not l.startswith("free = ")))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    return Target.load(str(tmp_path)), {s.name for s in Elf(elf).symbols if s.name}


@needs_gcc
def test_the_table_behind_a_switch_helper_call_is_its_callers_constants(tmp_path):
    from dread.program import Program
    target, symbols = program(tmp_path)
    helpers = sorted(s for s in symbols if s.startswith("__gnu_thumb1_case_"))
    assert helpers, "GCC made no call of a Thumb-1 switch helper here: the test would prove nothing"
    image = target.image()
    prog = Program(target, image)
    pick = prog.by_name["pick"]
    calls = [a for a in pick.insns if getattr(prog.insn(a), "dispatch_call", False)]
    assert len(calls) == 1, calls
    owners = prog.extent_owners()
    behind = calls[0] + prog.code[calls[0]][0]
    first_case = min(a for a in pick.insns if a > calls[0])
    assert first_case > behind
    assert all(owners[(b - image.base) >> 1] == 0 for b in range(behind, first_case, 2))     # (the table)
    k = prog.functions.index(pick) + 1
    assert owners[(pick.entry - image.base) >> 1] == k and owners[(first_case - image.base) >> 1] == k


@pytest.mark.slow
@needs_gcc
def test_a_switch_through_gccs_thumb_1_helper_gets_cases_that_run_it(tmp_path):
    target, _ = program(tmp_path)
    assert lift.main(["--target", target.root, "--check"]) == 0
    got = verdicts(tmp_path)
    assert got["pick"]["verdict"] == "accepted", got["pick"]
