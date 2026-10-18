"""dread record on gizmo: read_line through a modelled uart_getc (stub answers), a model that also
stores into memory (effects), and apply_config with a modelled parse_kv that writes the key and the
value into apply_config's frame (fills). A case is only kept when the generic stubs replay it with
the same result and the same memory written, so every kept case checks the replay too."""
import json
import os

import pytest

from dread import autocase, record

from conftest import GIZMO

pytestmark = pytest.mark.rv32

SPEC = os.path.join(GIZMO, "specs", "read_line.py")


def run(target, tmp_path, name, spec, **options):
    image = target.image()
    image.extra_cases = str(tmp_path / "extra")
    lines = []
    kept = record.run(target, image, name, spec, out=lines.append, **options)
    return image, kept, lines


def covered(target, image, name):
    gen = autocase.Generator(target, image)
    fn = gen.program.by_name[name]
    got = set()
    for case in autocase.extra_load(image, name):
        again = record.replay(gen, name, fn, case)
        assert again is not None
        got |= again[1]
    return got, set(fn.insns)


def test_read_line_is_covered_by_recorded_cases(target, tmp_path):
    image, kept, lines = run(target, tmp_path, "read_line", SPEC, tries=40)
    assert len(kept) == 40 and "40 replayed exactly" in lines[0], lines
    got, words = covered(target, image, "read_line")
    assert got == words


def test_recording_is_deterministic(target, tmp_path):
    _, one, _ = run(target, tmp_path / "one", "read_line", SPEC, tries=10, dry_run=True)
    _, two, _ = run(target, tmp_path / "two", "read_line", SPEC, tries=10, dry_run=True)
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)


def test_search_keeps_only_what_adds_coverage(target, tmp_path):
    image, kept, lines = run(target, tmp_path, "read_line", SPEC, seconds=2)
    assert 1 <= len(kept) < 20, lines
    got, words = covered(target, image, "read_line")
    assert got == words


EFFECTS = '''
from dread.record import Stream, write
CALLS = (("uart_getc", 0),)
SCRATCH = None
def getc(m, regs, state):
    state["n"] += 1
    write(m, SCRATCH + 0x800, state["n"].to_bytes(4, "little"))     # a counter outside the stack
    return Stream.getc(m, regs, state)
MODELS = {"uart_getc": getc}
def gen(rng):
    return [SCRATCH + 0x100, 16], [], {"stream": Stream(b"ab\\n"), "n": 0}, {}
'''


def test_effects_are_recorded_and_replayed(target, tmp_path):
    spec = tmp_path / "effects.py"
    spec.write_text(EFFECTS)
    _, kept, lines = run(target, tmp_path, "read_line", str(spec), tries=1)
    assert len(kept) == 1, lines
    assert kept[0]["returns"] == [ord("a"), ord("b"), 10]
    assert [e[0] for e in kept[0]["effects"]] == [1, 2, 3]       # one store per call


FILLS = '''
from dread.record import write
CALLS = (("parse_kv", 0xF),)             # parse_kv(line, key, keymax, &value), all four logged
SCRATCH = None
LINES = [b"volume=7", b"mode=2", b"baud=9600", b"name=x", b"junk"]
def parse_kv(m, regs, state):
    line, key, keymax, value = regs[:4]
    text = bytearray()
    while m.read(line + len(text), 1)[0]:
        text.append(m.read(line + len(text), 1)[0])
    if b"=" not in text:
        return 0xFFFFFFFF
    k, v = bytes(text).split(b"=", 1)
    write(m, key, k[:keymax - 1] + b"\\0")
    write(m, value, (int(v) if v.isdigit() else 0).to_bytes(4, "little"))
    return 0
MODELS = {"parse_kv": parse_kv}
def gen(rng):
    text = b"\\n".join(rng.choice(LINES) for _ in range(rng.randint(1, 3))) + b"\\0"
    return [SCRATCH + 0x100], [[SCRATCH + 0x100 + i, 1, b] for i, b in enumerate(text)], {}, {}
'''


def test_fills_are_recorded_and_replayed(target, tmp_path):
    """The key and the value land in apply_config's frame (its prologue reserves 56 bytes)."""
    spec = tmp_path / "fills.py"
    spec.write_text(FILLS)
    image, kept, lines = run(target, tmp_path, "apply_config", str(spec), tries=20)
    assert len(kept) == 20 and all(c["fills"] for c in kept), lines
    got, words = covered(target, image, "apply_config")
    assert len(got) > len(words) * 3 // 4, (len(got), len(words))
