"""dread sysrecord on gizmo: the config scenario types "c" and a configuration line; the recorded
calls of read_line (whose input arrives through uart_getc) and handle_key cover what generated
cases do not, replay in the function-level harness with the result the system run saw, carry the
UART's reads as poll stages, and move the caller's line buffer into scratch memory."""
import os

import pytest

from dread import autocase, sysrecord
from dread.program import Program

pytestmark = pytest.mark.rv32


def test_recorded_calls_reach_the_config_path(target, tmp_path):
    image = target.image()
    image.extra_cases = str(tmp_path / "extra")
    lines = []
    kept = sysrecord.record(target, image, ["read_line", "handle_key"], dict(target.scenarios["config"], _name="config"),
                            keep_all=True, out=lines.append)
    assert "read_line" in kept and "handle_key" in kept, lines
    case = kept["read_line"][0]
    scratch = target.memory.scratch_base
    assert scratch <= case["args"][0] < scratch + 0x20000          # the caller's buffer, moved
    uart = target.models["uart"]["base"]
    assert any(p[0] == uart + 4 for p in case["poll"]) and any(p[0] == uart for p in case["poll"])
    program = Program(target, image)
    gen = autocase.Generator(target, image, program=program)

    def covered(name):
        fn = program.by_name[name]
        got = set()
        for c in autocase.extra_load(image, name):
            got |= gen.coverage(fn.entry, fn.insns, c, c["limit"])
        return fn, got
    fn, got = covered("handle_key")                 # the 'c' path: a line read, then applied
    apply = program.by_name["apply_config"].entry
    calls = [a for a in fn.insns if program.arch.is_call(program.insn(a)) and program.arch.target(program.insn(a)) == apply]
    assert calls and set(calls) <= got
    fn, got = covered("read_line")                  # the line loop, up to the line feed
    assert len(got) >= len(fn.insns) - 3, (len(got), len(fn.insns))


def test_dry_run_writes_nothing(target, tmp_path):
    image = target.image()
    image.extra_cases = str(tmp_path / "extra")
    sysrecord.record(target, image, ["read_line"], dict(target.scenarios["config"], _name="config"),
                     keep_all=True, dry_run=True, out=lambda line: None)
    assert not os.path.exists(os.path.join(image.extra_cases, "read_line.json"))
