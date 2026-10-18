"""The emulator's hot hooks in C (dread.fasthooks) record exactly what the Python hooks record, and
the reset restores every page a store touched. Skipped when the helper does not build (no C
compiler)."""
import pytest

from dread import fasthooks
from dread.emu import Core, Layout, Machine
from dread.program import Program

pytestmark = [pytest.mark.skipif(fasthooks.library() is None, reason="needs a C compiler for dread.fasthooks"),
              pytest.mark.rv32]


def runs(target, fast):
    """Results of a few calls with coverage and load tracing, with the C hooks or without."""
    saved = list(fasthooks._LIB)
    fasthooks._LIB[:] = [] if fast else [None]
    Core.cores.clear()
    try:
        image = target.image()
        layout, program = Layout(target, image), Program(target, image)
        scratch = target.memory.scratch_base
        code = frozenset(program.code)
        out = []
        for name, args, data in (("checksum", (scratch, 3000), bytes(range(256)) * 12),
                                 ("strncpy", (scratch + 0x2000, scratch, 40), b"gizmo configuration\0"),
                                 ("parse_uint", (scratch, scratch + 0x100), b"4294967295\0"),
                                 ("apply_config", (scratch,), b"volume=7\nmode=2\nbaud=9600\n\0")):
            fn = program.by_name[name]
            m = Machine(layout, image.payload(), isa_of=program.isa_at)
            m.write(scratch, data)
            m.cover(fn.insns[0], program.end(fn), tree=True)
            m.trace_reads(image.base, image.end, code)
            r = m.call(fn.entry, *args)
            out.append((name, r.ret, sorted(r.writes.items()), r.calls, r.saved, sorted(m.blocks), sorted(m.tree),
                        list(m.reads.items()), m.code_reads))
        return out
    finally:
        fasthooks._LIB[:] = saved
        Core.cores.clear()


def test_same_as_the_python_hooks(target):
    assert runs(target, fast=True) == runs(target, fast=False)


def test_reset_restores_what_stores_changed(target):
    image = target.image()
    layout, program = Layout(target, image), Program(target, image)
    ram = target.memory.regions[1].base if len(target.memory.regions) > 1 else target.memory.scratch_base
    fn = program.by_name["memset"]
    before = Machine(layout, image.payload(), isa_of=program.isa_at).read(ram, 64)
    m = Machine(layout, image.payload(), isa_of=program.isa_at)
    m.call(fn.entry, ram, 0x5A, 64)
    assert m.read(ram, 64) == b"\x5a" * 64
    again = Machine(layout, image.payload(), isa_of=program.isa_at)     # the same Core, reset
    assert again.core is m.core and again.read(ram, 64) == before
