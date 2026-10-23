"""A run's log of device accesses: the first IO_LOGGED one by one, later ones folded into a digest
where their stretch ends, so that a long run's memory stays bounded and every access is still
compared (dread.emu's docstring)."""
from dread import emu
from dread.emu import Layout, Machine
from dread.program import Program
from dread.target import Target
from conftest import gizmo_dir

UART_DATA, UART_STATUS = 0x40011000, 0x40011004        # gizmo m3 (fw/gizmo.h)


def puts_runs(texts, logged, chunk=None):
    """The results of gizmo m3's uart_puts on each text, with IO_LOGGED (and FOLD_CHUNK) set."""
    saved = emu.IO_LOGGED, emu.FOLD_CHUNK
    emu.IO_LOGGED = logged
    if chunk:
        emu.FOLD_CHUNK = chunk
    try:
        target = Target.load(gizmo_dir("m3"))
        image = target.image()
        layout, program = Layout(target, image), Program(target, image)
        scratch = target.memory.scratch_base
        fn = program.by_name["uart_puts"]
        out = []
        for text in texts:
            m = Machine(layout, image.payload(), isa_of=program.isa_at)
            m.write(scratch, text + b"\0")
            m.write(UART_STATUS, (1).to_bytes(4, "little"))     # always ready
            out.append(m.call(fn.entry, scratch))
        return out
    finally:
        emu.IO_LOGGED, emu.FOLD_CHUNK = saved


def test_accesses_past_the_first_are_folded_and_still_compared():
    text = b"the uart writes a long line"
    same, late, early = text, text[:-1] + b"!", b"X" + text[1:]
    whole = puts_runs([text, late], logged=10**9)
    a, b, c, d = puts_runs([text, same, late, early], logged=6, chunk=4)
    assert len(whole[0].calls) == 2 * len(text)                 # status read and data write per byte
    assert whole[0].calls[:6] == a.calls[:6]                    # the first ones as they were
    assert len(a.calls) == 7 and a.calls[6][:2] == ("io folded", 2 * len(text) - 6)
    assert a.calls == b.calls                                   # the same accesses: the same log
    assert a.calls != c.calls and whole[0].calls != whole[1].calls     # a late byte still differs
    assert a.calls[:6] == c.calls[:6] and a.calls[6][1] == c.calls[6][1]   # only the digest tells
    assert a.diff(c)[0] == "stub calls differ: entry 7 of 7 against 7 after ('io write', %d, 4, %d): %s vs %s" \
        % (UART_DATA, text[2], a.calls[6], c.calls[6])
    assert a.calls != d.calls and a.diff(d)[0].startswith("stub calls differ: entry 2 of 7 against 7")


def test_a_stub_call_ends_a_folded_stretch_where_it_comes():
    m = Machine.__new__(Machine)
    m.calls, m._io_logged, m._fold = [], 0, None
    saved = emu.IO_LOGGED
    emu.IO_LOGGED = 1
    try:
        for i in range(3):
            m.log("io read", 0x100, 4, i)
        m.log("call 0x200", 7)
        m.log("io write", 0x104, 4, 9)
        m._end_fold()
    finally:
        emu.IO_LOGGED = saved
    kinds = [e[0] for e in m.calls]
    assert kinds == ["io read", "io folded", "call 0x200", "io folded"]
    assert m.calls[1][1] == 2 and m.calls[3][1] == 1


def test_the_difference_of_two_logs_names_the_first_entry_apart():
    assert emu.log_difference([("a",), ("b",)], [("a",), ("c",)]) == \
        "stub calls differ: entry 2 of 2 against 2 after ('a',): ('b',) vs ('c',)"
    assert emu.log_difference([("a",)], [("a",), ("b",)]) == \
        "stub calls differ: entry 2 of 1 against 2 after ('a',): the end vs ('b',)"
