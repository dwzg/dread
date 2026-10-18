"""dread liftfuzz: random instruction sequences, lifted and compared with the original in the
emulator, agree on both ISAs; a planted lifter mistake shows and is reduced to a few instructions."""
import pytest

from dread import lift, liftfuzz
from conftest import have

needs_gcc = pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")


@pytest.mark.slow
@needs_gcc
@pytest.mark.parametrize("isa", ["a32", "t32"])
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
