"""dread new: a target from every gizmo build's ELF agrees with the build's hand-written target (the
architecture, the image's bytes, base and entry, the startup copies, the functions, the stack); a target
from a raw image takes its entry and RAM from an M-profile vector table, or says what it guessed; what
dread new refuses, and that it takes back what it made; the first analyses it names; and a lift check
on a target it made, which shows that the memory map it wrote works in the emulator."""
import os
import shutil
import struct

import pytest

from conftest import GIZMO, have
from dread import newtarget
from dread.program import read_functions
from dread.target import Target

BUILDS = ("a32", "m3", "m4", "rv32", "msp430", "mips", "m3cpp", "rv32cpp")
ARCH_KEYS = ("name", "isa", "profile", "cpu", "extensions")


def elf_of(variant):
    return os.path.join(GIZMO, variant, "original", "gizmo.elf")


def quiet(*a, **k):
    pass


@pytest.mark.parametrize("variant", BUILDS)
def test_a_target_from_each_builds_elf(variant, tmp_path):
    example = Target.load(os.path.join(GIZMO, variant))
    made = newtarget.make(str(tmp_path / "fw"), elf_of(variant), out=quiet)
    assert {k: made.arch_data.get(k) for k in ARCH_KEYS} == {k: example.arch_data.get(k) for k in ARCH_KEYS}
    new, old = made.image(), example.image()
    assert new.payload() == old.payload() and new.base == old.base and new.entry == old.entry
    assert [(c.kind, c.run, c.size, c.stored) for c in new.startup()] == \
        [(c.kind, c.run, c.size, c.stored) for c in old.startup()]
    assert read_functions(new.table("functions")) == read_functions(old.table("functions"))
    assert new.fixed == old.fixed
    if variant != "mips":               # (the mips example's free range is an older build's: 8 bytes now)
        assert new.free == old.free
    assert made.memory.stack_top == example.memory.stack_top
    m = made.memory
    used = [(r.base, r.end) for r in m.regions] + m.io
    assert not any(lo <= m.sentinel < hi for lo, hi in used)
    assert m.region(m.stack_top - 4) is not None and m.region(m.scratch_base) is not None
    for c in old.startup():                 # (the RAM the image uses lies in a region)
        assert m.region(c.run) is not None and m.region(c.run + c.size - 1) is not None, c
    assert os.path.isfile(os.path.join(made.root, "original", "gizmo.elf"))
    text = open(made.file).read()
    assert "made by dread new from gizmo.elf" in text and "# " in text
    steps = newtarget.first_analyses(made)
    assert [s["step"] for s in steps] == ["functions", "regflow", "lift", "readable"] and steps[0]["command"] is None


def test_the_same_inputs_give_the_same_target(tmp_path):
    """Two targets of one name from one ELF are the same files (the compilers here decide [compiler]; they do
    not change in between)."""
    a = newtarget.make(str(tmp_path / "a" / "fw"), elf_of("m3"), out=quiet)
    b = newtarget.make(str(tmp_path / "b" / "fw"), elf_of("m3"), out=quiet)
    for name in ("dread.toml", "config/functions.tsv", "config/code_ranges.tsv", "config/symbols.txt",
                 "original/gizmo.bin"):
        with open(os.path.join(a.root, name), "rb") as f, open(os.path.join(b.root, name), "rb") as g:
            assert f.read() == g.read(), name


def test_a_target_from_a_raw_image(tmp_path, monkeypatch):
    """An M-profile raw image: the entry is the vector table's reset handler, the RAM reaches from the SRAM
    region's start up to its initial stack pointer; no tables yet, so the first analyses begin with dread
    ghidra where Ghidra is found and are refused where it is not."""
    raw = os.path.join(GIZMO, "m3", "original", "gizmo.bin")
    made = newtarget.make(str(tmp_path / "raw"), raw, arch="cortex-m3", base=0x08000000, out=quiet)
    example = Target.load(os.path.join(GIZMO, "m3"))
    assert made.image().entry == example.image().entry and made.image().payload() == example.image().payload()
    ram = made.memory.region(0x20000000)
    assert ram is not None and (ram.base, ram.end) == (0x20000000, 0x20005000) and made.memory.stack_top == 0x20005000
    assert made.arch_data == {"name": "arm", "isa": "t32", "profile": "m", "cpu": "cortex-m3"}
    assert not os.path.exists(made.image().table("functions"))
    assert "guessed" in open(made.file).read()
    import dread.ghidra
    monkeypatch.setattr(dread.ghidra, "find_ghidra", lambda target=None: "/somewhere/ghidra")
    steps = newtarget.first_analyses(made)
    assert steps[0]["command"] == "ghidra" and steps[0]["values"] == {"--no-decompile": True}
    assert newtarget.chain_line(steps) == "dread ghidra --no-decompile && dread regflow && dread lift --check && " \
                                          "dread readable --check"
    monkeypatch.setattr(dread.ghidra, "find_ghidra", lambda target=None: None)
    with pytest.raises(newtarget.NewTargetError, match="Ghidra is not installed"):
        newtarget.first_analyses(made)


def test_a_raw_image_without_a_vector_table_says_what_it_guessed(tmp_path):
    raw = os.path.join(GIZMO, "rv32", "original", "gizmo.bin")
    lines = []
    made = newtarget.make(str(tmp_path / "rv"), raw, arch="rv32imc", base=0x20000000, out=lines.append)
    text = open(made.file).read()
    assert "a placeholder" in text and "the base (guessed" in text and "none known" in text
    assert made.image().entry == 0x20000000 and made.memory.io == []
    assert any("guessed" in line for line in lines)
    given = newtarget.make(str(tmp_path / "rv2"), raw, arch="rv32imc", base=0x20000000, entry=0x20000004,
                           ram=[(0x80000000, 0x10000)], io=[(0x10000000, 0x10001000)], out=quiet)
    assert given.memory.io == [(0x10000000, 0x10001000)] and given.image().entry == 0x20000004
    assert given.memory.region(0x80000000).end == 0x80010000 and given.memory.stack_top == 0x80010000


def test_what_dread_new_refuses_and_takes_back(tmp_path):
    elf = elf_of("m3")
    with pytest.raises(newtarget.NewTargetError, match="needs --arch"):
        newtarget.make(str(tmp_path / "a"), os.path.join(GIZMO, "m3", "original", "gizmo.bin"), out=quiet)
    with pytest.raises(newtarget.NewTargetError, match="needs --base"):
        newtarget.make(str(tmp_path / "a"), os.path.join(GIZMO, "m3", "original", "gizmo.bin"), arch="cortex-m3",
                       out=quiet)
    with pytest.raises(newtarget.NewTargetError, match="--base is for a raw image"):
        newtarget.make(str(tmp_path / "a"), elf, base=0x1000, out=quiet)
    with pytest.raises(newtarget.NewTargetError, match="--arch rv32imc is riscv"):
        newtarget.make(str(tmp_path / "a"), elf, arch="rv32imc", out=quiet)
    assert not os.path.exists(tmp_path / "a")
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "notes.txt").write_text("mine")
    with pytest.raises(newtarget.NewTargetError, match="is not empty"):
        newtarget.make(str(tmp_path / "busy"), elf, out=quiet)
    newtarget.make(str(tmp_path / "done"), elf, out=quiet)
    with pytest.raises(newtarget.NewTargetError, match="is a target already"):
        newtarget.make(str(tmp_path / "done"), elf, out=quiet)
    odd = tmp_path / "odd.elf"                          # (an ELF of a machine DREAD does not run: x86)
    data = bytearray(open(elf, "rb").read())
    struct.pack_into("<H", data, 18, 3)
    odd.write_bytes(bytes(data))
    with pytest.raises(newtarget.NewTargetError, match="machine 3 is none of"):
        newtarget.make(str(tmp_path / "b"), str(odd), out=quiet)
    assert not os.path.exists(tmp_path / "b")
    assert newtarget.main([str(tmp_path / "c"), elf, "--ram", "0x2000"]) == 1
    assert newtarget.main([str(tmp_path / "c"), str(tmp_path / "nothing.elf")]) == 1
    assert not os.path.exists(tmp_path / "c")


def test_a_failure_after_the_start_takes_back_what_was_made(tmp_path, monkeypatch):
    """dread import-elf failing: a directory dread new made is gone; one that held the image (an upload) keeps
    the image and nothing else."""
    from dread import elfimport
    monkeypatch.setattr(elfimport, "main", lambda argv: 1)
    with pytest.raises(newtarget.NewTargetError, match="import-elf failed"):
        newtarget.make(str(tmp_path / "gone"), elf_of("m3"), out=quiet)
    assert not os.path.exists(tmp_path / "gone")
    held = tmp_path / "held" / "original"
    held.mkdir(parents=True)
    shutil.copy(elf_of("m3"), held / "fw.elf")
    with pytest.raises(newtarget.NewTargetError):
        newtarget.make(str(tmp_path / "held"), str(held / "fw.elf"), out=quiet)
    assert sorted(os.listdir(tmp_path / "held")) == ["original"] and os.listdir(held) == ["fw.elf"]


def test_an_image_already_in_original_is_taken_as_it_lies(tmp_path, capsys):
    """Where dread gui puts an uploaded file: DIR/original/IMAGE; dread new copies nothing, names the
    payload beside it, and prints what to run next."""
    held = tmp_path / "up" / "original"
    held.mkdir(parents=True)
    shutil.copy(elf_of("rv32"), held / "fw.elf")
    assert newtarget.main([str(tmp_path / "up"), str(held / "fw.elf"), "--unnamed", "--name", "fw-rv"]) == 0
    out = capsys.readouterr().out
    assert "next, in" in out and "dread regflow && dread lift --check && dread readable --check" in out
    assert sorted(os.listdir(held)) == ["fw.bin", "fw.elf"]
    t = Target.load(str(tmp_path / "up"))
    assert t.name == "fw-rv" and all(n.startswith("FUN_") for _, n, _ in read_functions(t.image().table("functions")))
    assert isinstance(t.image()._entry, int)


def test_the_build_attributes_and_the_producer():
    from dread.elf import Elf
    m4 = Elf(elf_of("m4"))
    keys, flags, triple, words = newtarget.arm_arch(m4)
    assert keys["cpu"] == "cortex-m4" and "-mfloat-abi=hard" in flags and triple == "thumbv7em-none-eabihf"
    kind, flags, said, how = newtarget.producer(m4)
    assert kind == "gcc" and "-mcpu=cortex-m4" in flags and not any(f.startswith(("-g", "-march")) for f in flags)
    assert how == "the only producer"
    assert newtarget.producer(Elf(elf_of("rv32")))[0] == "clang"
    keys, flags, _, words = newtarget.riscv_arch(Elf(elf_of("rv32")))
    assert keys["extensions"] == "imc" and "not confirmed" in words
    attrs = newtarget.attributes(b"A" + struct.pack("<I", 4 + 6 + 5 + 3) + b"riscv\0" + b"\x01" + struct.pack("<I", 8) +
                                 b"\x05rv\0", "riscv")
    assert attrs == {5: "rv"}


@pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_lift_check_on_a_target_it_made(tmp_path):
    """The memory map dread new wrote holds in the emulator: the lift check accepts gizmo's functions of the
    target made from the Cortex-M3 build's ELF (their cases agree and run every instruction), with room for
    their C behind the image (the guessed limit)."""
    from dread import lift
    made = newtarget.make(str(tmp_path / "fw"), elf_of("m3"), out=quiet)
    assert lift.main(["--target", made.root, "--check", "clamp", "checksum", "uart_putc"]) == 0
    with open(made.image().table("lifted")) as f:
        names = {line.strip() for line in f if line.strip() and not line.startswith("#")}
    assert {"clamp", "checksum", "uart_putc"} <= names


class Strings:
    """An ELF's stand-in for producer(): the strings of .debug_str."""

    machine = newtarget.EM_ARM

    def __init__(self, *texts):
        self.blob = b"\0".join(t.encode() for t in texts) + b"\0"

    def section(self, name):
        return type("S", (), {"nobits": False})() if name == ".debug_str" else None

    def data(self, section):
        return self.blob


def test_the_images_own_compiler_among_several(monkeypatch):
    """A C library brings a producer of its own (each string is kept once, so counting says nothing): the
    one whose compile unit holds main wins, else the one with the most code; without pyelftools none."""
    app = "clang version 23.1.3 (app)"
    lib = "GNU C23 16.1.0 -mthumb -march=armv7-m -mfloat-abi=soft -g -O2"
    elf = Strings(app, lib, "GNU AS 2.46")
    monkeypatch.setattr(newtarget, "unit_producers", lambda path, addresses: {app: [100, True], lib: [900, False]})
    kind, flags, said, how = newtarget.producer(elf, "x.elf", [0x100])
    assert kind == "clang" and said == app and "holds main" in how
    monkeypatch.setattr(newtarget, "unit_producers", lambda path, addresses: {app: [100, False], lib: [900, False]})
    kind, flags, said, how = newtarget.producer(elf, "x.elf", [0x100])
    assert kind == "gcc" and flags == ["-mthumb", "-march=armv7-m", "-mfloat-abi=soft", "-O2"] and "most code" in how

    def missing(path, addresses):
        raise ImportError("no pyelftools")
    monkeypatch.setattr(newtarget, "unit_producers", missing)
    kind, flags, said, how = newtarget.producer(elf, "x.elf", [0x100])
    assert kind is None and "several compilers" in how


def test_a_16_bit_space_without_room_below_the_flash():
    """An MSP430 whose flash begins right above its RAM (TI's F449: code from 0x1100, the vectors at the top):
    the scratch takes the erased flash between the code and the vectors, the room for C ends below it, the
    rest of the flash holds the vectors (a loader's bytes beside the image), and nothing overlaps."""
    p = newtarget.Plan()
    p.arch, p.bits, p.base, p.end, p.vectors = {"name": "msp430", "isa": "msp430"}, 16, 0x1100, 0x46c8, 0xffe0
    p.split, p.limit = (0x8300, 0x4000, 0x10000), 0x8300
    newtarget.plan_memory(p, [(0x200, 0x284), (0x284, 0x9bc)], 0xa00, "the symbol __stack")
    spans = sorted((r["base"], r["base"] + r["size"], r["name"]) for r in p.regions)
    assert spans == [(0x200, 0xa00, "ram"), (0x1100, 0x8300, "flash"), (0x8300, 0xc300, "scratch"),
                     (0xc300, 0x10000, "flash2")]
    assert p.io == [(0, 0x200)] and p.stack == (0xa00, 0x200) and p.scratch == (0x8300, 0x4000)
    assert not any(lo <= p.sentinel < hi for lo, hi, _ in spans) and not 0 <= p.sentinel < 0x200


def test_an_elf_without_symbols(tmp_path, monkeypatch):
    """An ELF stripped of its symbols (its symbol table blanked here): the image, architecture and entry come
    from the ELF all the same, the tables hold no function, and the first analyses begin with dread ghidra."""
    from dread.elf import Elf
    data = bytearray(open(elf_of("m3"), "rb").read())
    elf = Elf(elf_of("m3"))
    shoff, shentsize = struct.unpack_from("<I", data, 32)[0], struct.unpack_from("<H", data, 46)[0]
    for k, s in enumerate(elf.sections):
        if s.kind == 2:                                  # SHT_SYMTAB: made SHT_NULL
            struct.pack_into("<I", data, shoff + k * shentsize + 4, 0)
    stripped = tmp_path / "stripped.elf"
    stripped.write_bytes(bytes(data))
    made = newtarget.make(str(tmp_path / "fw"), str(stripped), out=quiet)
    example = Target.load(os.path.join(GIZMO, "m3"))
    assert made.image().payload() == example.image().payload() and made.image().entry == example.image().entry
    assert made.arch_data["cpu"] == "cortex-m3" and newtarget.functions_in(made) == 0
    import dread.ghidra
    monkeypatch.setattr(dread.ghidra, "find_ghidra", lambda target=None: "/somewhere/ghidra")
    assert newtarget.first_analyses(made)[0]["command"] == "ghidra"
