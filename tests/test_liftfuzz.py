"""dread liftfuzz: random instruction sequences, lifted and compared with the original in the
emulator, agree on every ISA (a32, t32, rv32); a planted lifter mistake shows and is reduced."""
import pytest

from dread import lift, liftfuzz
from conftest import have

needs_gcc = pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")


@pytest.mark.slow
@needs_gcc
@pytest.mark.parametrize("isa", ["a32", "t32", "m4"])
def test_random_functions_agree(isa, tmp_path):
    lines = []
    failures, refused, _ = liftfuzz.run(isa, functions=40, length=30, inputs=12, seed=7, keep=str(tmp_path / isa),
                                        out=lines.append, jobs=4, reduce=0)
    assert not failures and not refused, "\n".join(lines)


@pytest.mark.slow
@needs_gcc
def test_a_planted_mistake_shows_and_is_reduced(tmp_path, monkeypatch):
    compare = lift.Lifter._compare

    def wrong_overflow(self, insn, ops, kind):  # cmp's overflow as if it added
        text = compare(self, insn, ops, kind)
        return text.replace("v = ((x ^ r) & (~y ^ r)) >> 31;", "v = ((x ^ r) & (y ^ r)) >> 31;") if kind == "cmp" else text
    monkeypatch.setattr(lift.Lifter, "_compare", wrong_overflow)
    lines = []
    failures, refused, _ = liftfuzz.run("a32", functions=40, length=30, inputs=12, seed=7, keep=str(tmp_path / "a32"),
                                        out=lines.append, jobs=4, reduce=1)
    assert failures and not refused
    reduced = [line for line in lines if line.startswith("reduced")]
    assert len(reduced) == 1 and len(reduced[0].split(" to: ")[1].split(" | ")) <= 3, reduced


needs_llvm = pytest.mark.skipif(not (have("clang") and have("ld.lld")), reason="needs clang and ld.lld")


@pytest.mark.slow
@needs_llvm
def test_random_rv32_functions_agree(tmp_path):
    """dread.lift_riscv on random RV32IMC code (the assembler compresses what it can)."""
    lines = []
    failures, refused, _ = liftfuzz.run("rv32", functions=40, length=30, inputs=12, seed=7,
                                        keep=str(tmp_path / "rv32"), out=lines.append, jobs=4, reduce=0)
    assert not failures and not refused, "\n".join(lines)


@pytest.mark.slow
@needs_llvm
def test_a_planted_rv32_mistake_shows_and_is_reduced(tmp_path, monkeypatch):
    """divu by zero as if C's division were defined there (it gives all ones on RISC-V)."""
    from dread import lift_riscv
    monkeypatch.setitem(lift_riscv.DIVS, "divu", "({b} == 0 ? 0u : {a} / {b})")
    lines = []
    failures, refused, _ = liftfuzz.run("rv32", functions=60, length=30, inputs=12, seed=7,
                                        keep=str(tmp_path / "rv32"), out=lines.append, jobs=4, reduce=1)
    assert failures and not refused, "\n".join(lines)
    reduced = [line for line in lines if line.startswith("reduced")]       # (a zero divisor needs the units
    assert len(reduced) == 1 and "divu" in reduced[0] and len(reduced[0].split(" to: ")[1].split(" | ")) <= 12, reduced
    # in front of it that make one: fewer units are left out than for ARM's flags)
