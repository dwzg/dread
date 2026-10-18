"""The case generator on gizmo: coverage from generated cases alone (apply_config only with the
dictionary: its lines must read like volume=7), determinism, pointer learning, and the Thumb
IT-state repair in the emulator (a regression test with the input that hung)."""
import json

import pytest

from dread.autocase import Generator
from dread.target import Target
from conftest import gizmo_dir

pytestmark = pytest.mark.rv32


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
