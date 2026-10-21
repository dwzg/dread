"""The Ghidra pipeline: the directive file from the target description, and (when Ghidra is
installed) a whole analysis of gizmo whose tables must agree with the ELF's."""
import os

import pytest

from dread import ghidra
from dread.program import read_code_ranges, read_functions


def test_directives_from_the_target(target, variant):
    image = target.image()
    lines = ghidra.directives(target, image)
    kinds = [l.split()[0] for l in lines if not l.startswith("#")]
    assert kinds[0] == "image"
    assert kinds.count("copy") == 2                         # the RAM function and .data
    assert "io" in kinds and "ram" in kinds and "entry" in kinds
    funcs = [l for l in lines if l.startswith("func ")]
    assert any(l.endswith(" main") for l in funcs)
    if variant in ("m3", "m4"):
        handlers = ghidra.vector_handlers(image)
        assert handlers[0][1] == image.entry                # the reset vector is the entry
        assert [i for i, _ in handlers] == [1, 2, 3, 4, 5, 6, 11, 12, 14, 15, 16]   # reserved slots are 0
        from dread.program import Program
        assert Program(target).name_at(handlers[-1][1]) == "TIMER_IRQHandler"


def test_subtract():
    assert ghidra.subtract((0, 100), [(10, 20), (50, 60)]) == [(0, 10), (20, 50), (60, 100)]
    assert ghidra.subtract((0, 100), [(0, 100)]) == []


def test_vector_table_behind_a_header():
    """vector_handlers reads the M-profile table at image.vectors, which may sit behind a vendor
    header (the image's base below it), not at the base. image.vectors defaults to base, so the
    other tests, whose tables are at the base, still exercise the start-at-base path."""
    import struct
    base = 0x08010000
    vectors = base + 0x200                          # a 0x200-byte header precedes the table
    table = struct.pack("<4I", 0x20001000,          # index 0: initial SP, not a handler
                        (base + 0x20) | 1,          # index 1: reset
                        (base + 0x40) | 1,          # index 2: a handler
                        0x12345678)                 # index 3: not a Thumb address, ends the table
    payload = b"\xaa" * 0x200 + table + b"\x00" * 0x100

    class FakeImage:
        def __init__(self):
            self.base, self.vectors = base, vectors

        def payload(self):
            return payload

    handlers = ghidra.vector_handlers(FakeImage())
    assert handlers == [(1, base + 0x20), (2, base + 0x40)]


def test_vectors_outside_the_payload_are_read_at_the_base():
    """image.vectors at an alias of the boot address (0, the image at 0x08000000) lies outside the
    payload: the table is read at the base, as before image.vectors was read, not from a negative
    offset (struct reads that from the payload's end)."""
    import struct
    base = 0x08000000
    table = struct.pack("<4I", 0x20001000, (base + 0x20) | 1, (base + 0x40) | 1, 0x12345678)
    payload = table + b"\x00" * 0x100 + struct.pack("<I", (base + 0x80) | 1)

    class FakeImage:
        def __init__(self):
            self.base, self.vectors = base, 0

        def payload(self):
            return payload

    assert ghidra.vector_handlers(FakeImage()) == [(1, base + 0x20), (2, base + 0x40)]


@pytest.mark.slow
@pytest.mark.ghidra
@pytest.mark.skipif(ghidra.find_ghidra() is None, reason="needs Ghidra (GHIDRA_INSTALL_DIR)")
def test_analysis_agrees_with_the_elf(target, variant, tmp_path):
    image = target.image()
    rc = ghidra.main(["--target", target.root, "--out", str(tmp_path / "config"), "--project",
                      str(tmp_path / "project"), "--no-decompile"])
    assert rc == 0
    truth = {e: (n, s) for e, n, s in read_functions(image.table("functions"))}
    got = {e: (n, s) for e, n, s in read_functions(str(tmp_path / "config" / "functions.tsv"))}
    for entry, (name, size) in truth.items():
        assert entry in got, name
        assert got[entry] == (name, size), (name, got[entry])
    extra = [got[e][0] for e in set(got) - set(truth)]
    assert all(name.startswith("thunk_") for name in extra), extra
    ranges = read_code_ranges(str(tmp_path / "config" / "code_ranges.tsv"), target.arch.isa, target.arch.isas())
    assert {isa for _, _, isa in ranges} == {target.arch.isa}
