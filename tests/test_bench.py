"""dread bench on a local recipe built from gizmo's own sources (no network): the harness compiles,
links, imports the ELF and measures the listing and noreturn; a substitution goes into a copy, never
into the source; and the "load" kind of startup entry: bytes a loader puts into RAM beside the image
(a QEMU-style ELF whose initialised data has no copy in flash)."""
import json
import os
import shutil
import subprocess

import pytest

from dread import bench
from conftest import GIZMO, have

needs_gcc = pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")

TARGET = '''
[target]
name = "bench-gizmo"

[arch]
name = "arm"
isa = "t32"
profile = "m"
cpu = "cortex-m3"

[memory]
regions = [
    {name = "flash", base = 0x08000000, size = 0x100000, kind = "rom"},
    {name = "sram", base = 0x20000000, size = 0x5000},
    {name = "scratch", base = 0x20100000, size = 0x20000},
]
io = [[0x40000000, 0x40020000], [0xe000e000, 0xe000f000]]
stack = {top = 0x20005000, size = 0x1000}
sentinel = 0x1fff0000
scratch = {base = 0x20100000, size = 0x20000}
'''


def recipe(tmp_path, patches=""):
    path = tmp_path / "gizmo.toml"
    path.write_text('''name = "gizmo"

[source]
path = "%s"

[projects.m3]
sources = ["start_m3.c", "lib.c", "util.c", "hw.c", "main.c"]
includes = ["."]
defines = ["GIZMO_M3"]
ldscript = "m3.ld"
link = ["-nostdlib", "-lgcc"]
base = 0x08000000
limit = 0x08010000
entry = "Reset_Handler"
target = \'\'\'%s\'\'\'
%s
[[builds]]
name = "m3-Os"
project = "m3"
cc = "arm-none-eabi-gcc"
cflags = ["-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft", "-Os", "-ffreestanding", "-fno-builtin",
          "-ffunction-sections", "-fno-common", "-g", "-Wall"]
''' % (os.path.join(GIZMO, "fw"), TARGET, patches))
    return bench.load(str(path))


@needs_gcc
def test_a_local_recipe_builds_and_measures(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAD_BENCH", str(tmp_path / "bench"))
    r = recipe(tmp_path)
    where = bench.build(r, "m3-Os", out=lambda *a: None)
    assert os.path.exists(os.path.join(where, "firmware.elf"))
    toml = open(os.path.join(where, "dread.toml")).read()
    assert 'cross = "arm-none-eabi-"' in toml and '"-g"' not in toml and "startup = [" in toml
    metrics = bench.measure(r, "m3-Os", steps=("asm", "noreturn"), out=lambda *a: None)
    assert metrics["functions"] > 30 and metrics["asm"]["identical"] and metrics["asm"]["raw_words"] <= 2
    assert metrics["noreturn"]["functions"] >= 2          # main's loop, and the reset handler that calls it
    assert json.load(open(os.path.join(where, "metrics.json")))["functions"] == metrics["functions"]
    report = open(bench.report(r, out=lambda *a: None)).read()
    assert "| m3-Os | arm-none-eabi-gcc |" in report and "identical" in report


@needs_gcc
def test_a_substitution_goes_into_a_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAD_BENCH", str(tmp_path / "bench"))
    patch = '[[projects.m3.patches]]\nfile = "main.c"\nold = "READY"\nnew = "STEADY"\n'
    r = recipe(tmp_path, patch)
    before = open(os.path.join(GIZMO, "fw", "main.c")).read()
    where = bench.build(r, "m3-Os", out=lambda *a: None)
    assert open(os.path.join(GIZMO, "fw", "main.c")).read() == before
    assert b"STEADY" in open(os.path.join(where, "original", "firmware.bin"), "rb").read()
    bad = recipe(tmp_path, '[[projects.m3.patches]]\nfile = "main.c"\nold = "no such text"\nnew = "x"\n')
    with pytest.raises(bench.BenchError):
        bench.build(bad, "m3-Os", out=lambda *a: None)


@needs_gcc
def test_a_header_substitution(tmp_path, monkeypatch):
    """A header patched (FreeRTOS's configuration: stacks too small for GCC 16) is found by the
    sources beside it, which include it by name, and by the others; the tree stays as it was."""
    monkeypatch.setenv("DREAD_BENCH", str(tmp_path / "bench"))
    patch = '[[projects.m3.patches]]\nfile = "gizmo.h"\nold = "#define UART_BASE  0x40011000u"\n' \
            'new = "#define UART_BASE  0x40012000u"\n'
    before = open(os.path.join(GIZMO, "fw", "gizmo.h")).read()
    where = bench.build(recipe(tmp_path, patch), "m3-Os", out=lambda *a: None)
    assert open(os.path.join(GIZMO, "fw", "gizmo.h")).read() == before
    image = open(os.path.join(where, "original", "firmware.bin"), "rb").read()
    assert (0x40012000).to_bytes(4, "little") in image and (0x40011000).to_bytes(4, "little") not in image
    with pytest.raises(bench.BenchError, match="not in the project"):
        bench.build(recipe(tmp_path, '[[projects.m3.patches]]\nfile = "nope.h"\nold = "a"\nnew = "b"\n'),
                    "m3-Os", out=lambda *a: None)


def test_the_record_step(monkeypatch):
    """The functions left uncovered or without inputs are recorded in every scenario, the cases
    kept are counted from sysrecord's lines, and the lift check runs again only when some were."""
    class T:
        scenarios = {"boot": {}, "long": {}}
    calls = []

    def fake(*args, **kw):
        calls.append(args)
        return 1.0, 0, "f  3 calls recorded, 2 replay with the same result, 1 kept; 4 -> 9 of 9 instructions\n" \
                       "g  0 calls recorded, 0 replay with the same result, 0 kept; 1 -> 1 of 5 instructions\n"
    monkeypatch.setattr(bench, "dread", fake)
    monkeypatch.setattr(bench, "verdicts_of", lambda t, i: [
        ("f", "dropped", "UNCOVERED 5 of 9"), ("g", "dropped", "FAIL x [case generation]: no input ran"),
        ("h", "dropped", "never returns or an exception handler"), ("k", "accepted", "")])
    image = type("I", (), {"name": "main"})()
    got = bench.record_step(T(), image, "/nowhere")
    assert got == {"ran": True, "functions": 2, "kept": 2, "seconds": 2.0}
    assert [c[:7] for c in calls] == [("sysrecord", "--target", "/nowhere", "--image", "main", "--scenario", s)
                                      for s in ("boot", "long")] and all(c[7:] == ("f", "g") for c in calls)
    T.scenarios = {}
    assert bench.record_step(T(), image, "/nowhere") == {"ran": False, "functions": 2}


@needs_gcc
def test_a_rebuild_that_changes_the_image_drops_recorded_cases(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAD_BENCH", str(tmp_path / "bench"))
    where = bench.build(recipe(tmp_path), "m3-Os", out=lambda *a: None)
    recorded = os.path.join(where, "tests", "extra_cases", "main.json")
    os.makedirs(os.path.dirname(recorded))
    open(recorded, "w").write("{}")
    bench.build(recipe(tmp_path), "m3-Os", out=lambda *a: None)                 # the same image: kept
    assert os.path.exists(recorded)
    patch = '[[projects.m3.patches]]\nfile = "main.c"\nold = "READY"\nnew = "STEADY"\n'
    bench.build(recipe(tmp_path, patch), "m3-Os", out=lambda *a: None)          # another image: dropped
    assert not os.path.exists(recorded)


def test_recipes_are_checked(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('name = "x"\n[source]\npath = "."\n[projects.p]\nsources = []\n'
                    '[[builds]]\nname = "b"\nproject = "nope"\ncc = "gcc"\ncflags = []\n')
    with pytest.raises(bench.BenchError, match="names no project"):
        bench.load(str(path))
    assert bench.load("freertos")["name"] == "freertos"           # the recipes in DREAD's bench/


def test_the_compiler_section_for_rewritten_c():
    gcc = bench.dread_compiler({"name": "b", "cc": "arm-none-eabi-gcc", "cflags": ["-mthumb", "-Os", "-g3", "-Wall",
                                                                                   "-DX=1", "-Iinc"]})
    assert gcc == '[compiler]\ncross = "arm-none-eabi-"\ncflags = ["-mthumb", "-Os"]\n'
    clang = bench.dread_compiler({"name": "b", "cc": "clang", "cflags": ["--target=thumbv7m-none-eabi", "-Os"]})
    assert clang == '[compiler]\nkind = "clang"\ntriple = "thumbv7m-none-eabi"\ncflags = ["-Os"]\n'


@needs_gcc
def test_data_a_loader_puts_into_ram(tmp_path):
    """An ELF whose initialised data lies in RAM with no copy in flash (QEMU loads it so): the image
    is the flash part, the data a startup entry of kind load with its bytes in a file, and a called
    function finds them."""
    from dread import elfimport
    from dread.emu import Layout, Machine
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.c").write_text("int counter = 0x1234567;\nint next(void) { return counter++; }\n")
    (tmp_path / "t.ld").write_text(
        "MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
        "SECTIONS { .text : { *(.text*) } > FLASH\n .data : { *(.data*) } > RAM\n .bss : { *(.bss*) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "next", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    target = Target.load(str(tmp_path))
    image = target.image()
    assert len(image.payload()) < 0x100                          # the flash part only
    (load,) = [c for c in image.startup() if c.kind == "load"]
    assert (load.run, load.size, load.data) == (0x20000000, 4, (0x1234567).to_bytes(4, "little"))
    assert 'file = "original/load-20000000.bin"' in (tmp_path / "dread.toml").read_text()
    program = Program(target, image)
    machine = Machine(Layout(target, image), image.payload())
    result = machine.call(program.by_name["next"].entry)
    assert result.ret == 0x1234567 and result.writes[0x20000000] == 0x68       # counter + 1, written back


@needs_gcc
def test_an_extent_does_not_take_a_free_range(tmp_path):
    """The gap in front of an aligned section is a free range (import-elf) and lay inside the
    function before it, whose extent the overlay hands out too: FreeRTOS's -O2 build placed a moved
    function in the gap and the extended one over it, and the link failed. An extent now stops at a
    free range, and overlapping placements are refused before the link."""
    from dread import build, elfimport
    from dread.target import Target
    (tmp_path / "t.c").write_text(
        "int first(int x) { return x * 3 + 1; }\n"
        "__attribute__((aligned(32))) int second(int x) { return x - 1; }\n"
        "int third(int x) { return second(x) + first(x); }\n")
    (tmp_path / "t.ld").write_text(
        "MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K }\n"
        "SECTIONS { .text.first : { *(.text.first) } > FLASH\n .text.second : { *(.text.second) } > FLASH\n"
        " .text.third : { *(.text.third) } > FLASH }\n")        # separate sections: the gap lies between them
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "third", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08000400\n\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mcpu=cortex-m3", "-mthumb", "-Os", "-ffunction-sections"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    target = Target.load(str(tmp_path))
    image = target.image()
    assert image.free, "the gap in front of the aligned function is free"
    src = tmp_path / "src"
    src.mkdir()
    # first grows past its own instructions (an extent would hold it); second grows and has to move
    (src / "c.c").write_text('#include "dread.h"\n'
                             "int first(int x) { volatile int k = 3; int r = x * k; return r + 1; }\n"
                             "int second(int x) { volatile int k = 1; volatile int j = 0; return x - k + j; }\n")
    payload = build.build(target, image, src=str(src), out=str(tmp_path / "recon"), quiet=True)
    placed = json.load(open(tmp_path / "recon" / "placement.json"))["functions"]
    spans = sorted((p["address"], p["address"] + p["c_bytes"]) for p in placed.values())
    assert all(a_hi <= b_lo for (_, a_hi), (b_lo, _) in zip(spans, spans[1:])), spans
    assert os.path.getsize(payload) > 0


@pytest.mark.skipif(not have("riscv64-elf-gcc"), reason="needs riscv64-elf-gcc")
def test_gp_holds_one_value(tmp_path):
    """GCC's RISC-V code addresses small data from gp, which the startup sets to __global_pointer$
    once: import-elf writes it as a fixed register, the emulator sets it before every call, and the
    lifter takes it as that constant instead of refusing a function that reads it (FreeRTOS's GCC
    builds: 209 of 311 functions refused before)."""
    from dread import elfimport
    from dread.emu import Layout, Machine
    from dread.lift import make_lifter
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.c").write_text("int counter = 7;\nint next(void) { return counter + 1; }\n")   # one load: relaxed
    (tmp_path / "t.ld").write_text(
        "MEMORY { ROM (rx) : ORIGIN = 0x80000000, LENGTH = 64K\n RAM (rw) : ORIGIN = 0x80080000, LENGTH = 64K }\n"
        "SECTIONS { .text : { *(.text*) } > ROM\n .sdata : { __global_pointer$ = . + 0x100; *(.sdata*) } > RAM AT > ROM\n"
        " .sbss : { *(.sbss*) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["riscv64-elf-gcc", "-march=rv32imac", "-mabi=ilp32", "-Os", "-nostdlib", "-ffunction-sections",
                    "-msmall-data-limit=8", "-T", str(tmp_path / "t.ld"), "-e", "next", "-o", elf, str(tmp_path / "t.c")],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text('''[target]
name = "gp"
[arch]
name = "riscv"
isa = "rv32"
extensions = "imac"
[memory]
regions = [{name = "rom", base = 0x80000000, size = 0x10000, kind = "rom"},
           {name = "ram", base = 0x80080000, size = 0x10000}]
stack = {top = 0x80090000, size = 0x1000}
sentinel = 0x7fff0000
[image.main]
original = "original/t.bin"
base = 0x80000000
[compiler]
cross = "riscv64-elf-"
cflags = ["-march=rv32imac", "-mabi=ilp32", "-Os"]
''')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    target = Target.load(str(tmp_path))
    image = target.image()
    assert set(image.fixed) == {"gp"}
    program = Program(target, image)
    fn = program.by_name["next"]
    assert any("gp" in (i.op_str or "") for i in program.instructions(fn))     # it does address from gp
    machine = Machine(Layout(target, image), image.payload())
    machine.write(0x80080000, (7).to_bytes(4, "little"))      # the startup's copy of .sdata
    assert machine.call(fn.entry).ret == 8
    text = make_lifter(program).lift(fn).text
    assert "gp = 0x%x;" % image.fixed["gp"] in text


RV_TARGET = '''[target]
name = "rv"
[arch]
name = "riscv"
isa = "rv32"
extensions = "imac"
[memory]
regions = [{name = "rom", base = 0x80000000, size = 0x10000, kind = "rom"},
           {name = "ram", base = 0x80080000, size = 0x10000}]
stack = {top = 0x80090000, size = 0x1000}
sentinel = 0x7fff0000
[image.main]
original = "original/t.bin"
base = 0x80000000
[compiler]
cross = "riscv64-elf-"
cflags = ["-march=rv32imac", "-mabi=ilp32", "-Os"]
'''


def tiny_rv(tmp_path, source):
    from dread import elfimport
    from dread.target import Target
    (tmp_path / "t.c").write_text(source)
    (tmp_path / "t.ld").write_text("SECTIONS { . = 0x80000000; .text : { *(.text*) } }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["riscv64-elf-gcc", "-march=rv32imac", "-mabi=ilp32", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(RV_TARGET)
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    return Target.load(str(tmp_path))


@pytest.mark.skipif(not have("riscv64-elf-gcc"), reason="needs riscv64-elf-gcc")
def test_a_system_call_runs_as_a_logged_no_op(tmp_path):
    """FreeRTOS's RISC-V port yields with ecall: in the emulator it was a fault (no input of such a
    function finished) and the lifter refused it. A system call now runs as a no-op logged in order,
    and lifts to inline assembly with a0-a7 bound; another CPU exception still stops the run."""
    from dread.emu import EmuError, Layout, Machine
    from dread.lift import make_lifter
    from dread.program import Program
    target = tiny_rv(tmp_path, "int yield(int x) { __asm__ volatile (\"ecall\"); return x + 1; }\n"
                               "int broken(void) { __asm__ volatile (\".word 0\"); return 0; }\n")
    image = target.image()
    program = Program(target, image)
    machine = Machine(Layout(target, image), image.payload())
    result = machine.call(program.by_name["yield"].entry, 41)
    assert result.ret == 42 and result.calls == [("svc",)]
    with pytest.raises(EmuError, match="CPU exception|INSN_INVALID|Unhandled"):
        Machine(Layout(target, image), image.payload()).call(program.by_name["broken"].entry)
    assert '__asm__ volatile ("ecall"' in make_lifter(program).lift(program.by_name["yield"]).text


def test_paths_in_the_home_and_free_ranges_beside_the_scratch():
    """A toolchain's files in the user's home (TI's MSP430 support files: -I~/..., -L~/...), and free
    ranges that leave the target's scratch memory out (on a 16-bit MSP430 the generated inputs'
    buffers lie in the erased flash between code and vectors, which the overlay must not fill)."""
    from dread import elfimport
    home = os.path.expanduser("~")
    assert bench.home(["-I~/x", "-L~/y", "-Os", "~/z", "-T~/a.ld"]) == \
        ["-I%s/x" % home, "-L%s/y" % home, "-Os", "%s/z" % home, "-T%s/a.ld" % home]

    class Elf:
        def loaded(self):
            return [(0x1100, b"\0" * 0x100, None), (0xF000, b"\0" * 0x20, None)]
    assert elfimport.free_ranges(Elf(), 0x1100) == [(0x1200, 0xF000)]
    assert elfimport.free_ranges(Elf(), 0x1100, reserved=[(0x8000, 0xC000)]) == [(0x1200, 0x8000), (0xC000, 0xF000)]


@pytest.mark.skipif(not shutil.which("msp430-elf-gcc"), reason="needs msp430-elf-gcc")
def test_msp430_vectors_in_a_region_of_their_own(tmp_path):
    """FreeRTOS's MSP430 build: the vectors lie in a flash region above the scratch, apart from the
    image. They are loader data, no code range lies outside the image (GCC emits each vector as an
    executable section without symbols), Ghidra's analysis starts at the reset vector and the
    handlers, and the MSP430 lifter (dread.lift_msp430) takes the image."""
    import subprocess
    from dread import elfimport, lift
    from dread.ghidra import directives
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.c").write_text("volatile int n;\nvoid isr(void) { n++; }\nvoid reset(void) { for (;;) n--; }\n"
                                  "__attribute__((section(\".vec\"), used)) void (* const vec[16])(void) = "
                                  "{ [3] = isr, [15] = reset };\n")
    (tmp_path / "t.ld").write_text("SECTIONS { .text 0x1100 : { *(.text*) }\n .vec 0xffe0 : { KEEP(*(.vec)) }\n"
                                   " .bss 0x200 : { *(.bss*) *(COMMON) } }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["msp430-elf-gcc", "-mcpu=msp430", "-Os", "-nostdlib", "-T", str(tmp_path / "t.ld"),
                    "-e", "reset", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text('''[target]
name = "vec"
[arch]
name = "msp430"
isa = "msp430"
[memory]
regions = [{name = "flash", base = 0x1100, size = 0x6f00, kind = "rom"},
           {name = "scratch", base = 0x8000, size = 0x4000},
           {name = "flash2", base = 0xc000, size = 0x4000, kind = "rom"},
           {name = "ram", base = 0x0200, size = 0x0800}]
io = [[0x0000, 0x0200]]
stack = {top = 0x0a00, size = 0x0200}
sentinel = 0x0ff0
scratch = {base = 0x8000, size = 0x4000}
[image.main]
original = "original/t.bin"
base = 0x1100
limit = 0x8000
entry = "reset"
vectors = 0xffe0
[compiler]
cross = "msp430-elf-"
cflags = ["-mcpu=msp430"]
ldflags = []
''')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    image = t.image()
    p = Program(t, image)                   # (it refused code ranges outside the image)
    assert any(c.kind == "load" and c.run == 0xffe0 for c in image.startup())
    lines = directives(t, image, symbols=False)
    assert "entry 0x%x" % p.by_name["reset"].entry in lines and "func 0x%x -" % p.by_name["isr"].entry in lines
    lifter = lift.make_lifter(p)
    assert type(lifter).__name__ == "Msp430Lifter"
    assert "isr(u16 p12, u16 p13, u16 p14, u16 p15)" in lifter.lift(p.by_name["isr"]).text


@needs_gcc
def test_measuring_builds_again_what_the_recipe_changed(tmp_path, monkeypatch):
    """A build made from an older recipe (other flags, patches, models or scenarios) is built again
    before it is measured: five builds were once measured without the device models their recipe
    had gained since."""
    monkeypatch.setenv("DREAD_BENCH", str(tmp_path / "bench"))
    where = bench.build(recipe(tmp_path), "m3-Os", out=lambda *a: None)
    lines = []
    bench.measure(recipe(tmp_path), "m3-Os", steps=("asm",), out=lines.append)
    assert not any("building it again" in line for line in lines), lines
    patch = '[[projects.m3.patches]]\nfile = "main.c"\nold = "READY"\nnew = "STEADY"\n'
    lines = []
    bench.measure(recipe(tmp_path, patch), "m3-Os", steps=("asm",), out=lines.append)
    assert any("building it again" in line for line in lines), lines
    assert b"STEADY" in open(os.path.join(where, "original", "firmware.bin"), "rb").read()
