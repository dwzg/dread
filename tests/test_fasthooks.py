"""The emulator's hot hooks in C (dread.fasthooks) record exactly what the Python hooks record, and
the reset restores every page a store touched. Skipped when the helper does not build (no C
compiler)."""
import pytest

from dread import fasthooks
from dread.emu import Core, Layout, Machine
from dread.program import Program

pytestmark = [pytest.mark.skipif(fasthooks.library() is None, reason="needs a C compiler for dread.fasthooks")]


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


UNDEFINED = {"a32": b"\xf0\x00\xf0\xe7", "m3": b"\x00\xde", "rv32": b"\x00\x00\x00\x00"}     # udf, udf, unimp
LOAD = {"a32": b"\x00\x00\x90\xe5\x1e\xff\x2f\xe1", "m3": b"\x00\x68\x70\x47",          # a load through r0/a0
        "rv32": b"\x03\x25\x05\x00\x67\x80\x00\x00"}        # and a return (the block stays short)


def test_a_run_ending_in_a_cpu_exception_gets_a_new_core(target, variant):
    """After a run that ends in a CPU exception or an invalid instruction the next Machine gets a
    new core: Unicorn kept such an exception for every later call on one core of a FreeRTOS build
    (dread.emu.Core.retire). After an unmapped access the core is reset and used again."""
    from dread.emu import EmuError
    image = target.image()
    layout, program = Layout(target, image), Program(target, image)
    code = target.memory.scratch_base + 0x800
    m = Machine(layout, image.payload(), isa_of=program.isa_at)
    m.write(code, UNDEFINED[variant])
    with pytest.raises(EmuError, match="CPU exception|Invalid instruction"):
        m.call(code, max_instructions=100)
    again = Machine(layout, image.payload(), isa_of=program.isa_at)
    assert again.core is not m.core
    nowhere = next(a for a in (0x70000000, 0x30000000, 0x0F000000)
                   if not any(lo <= a < hi for lo, hi in layout.maps))
    again.write(code, LOAD[variant])
    with pytest.raises(EmuError, match="READ_UNMAPPED"):
        again.call(code, nowhere, max_instructions=100)
    assert Machine(layout, image.payload(), isa_of=program.isa_at).core is again.core


def test_the_instruction_limit_is_a_budget_alike_in_c_and_python(target, monkeypatch):
    """A run's limit is a budget of bytes of code entered (the instruction set's shortest
    instructions), kept by a C block hook (Unicorn's own count calls a hook before every
    instruction: long runs went three to six times slower) and by a Python one without the C helper:
    both stop the same runaway run at the same block, and a run that returns is not cut."""
    from dread import autocase, emu, fasthooks
    gen = autocase.Generator(target, target.image())
    fn = gen.program.by_name["checksum"]
    scratch = target.memory.scratch_base

    def run(limit):
        m = gen.machine()
        m.plan = []
        m.write(scratch, bytes(range(256)) * 16)
        try:
            r = m.call(fn.entry, scratch, 4096, max_instructions=limit)
            return "returned", r.ret, m.uc.reg_read(m.layout.pc)
        except emu.EmuError:
            return "stopped", None, m.uc.reg_read(m.layout.pc)
    assert run(200_000)[0] == "returned"
    short = run(50)
    assert short[0] == "stopped"
    monkeypatch.setattr(fasthooks, "_LIB", [None])          # as without a C compiler
    monkeypatch.setattr(emu.Core, "cores", {})
    assert run(200_000)[0] == "returned"
    assert run(50) == short
