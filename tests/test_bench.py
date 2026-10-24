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
from conftest import GIZMO, have, needs_riscv_gcc, RISCV_CROSS, riscv_toml

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
    metrics = bench.measure(r, "m3-Os", steps=("asm", "noreturn", "callconv"), out=lambda *a: None)
    assert metrics["functions"] > 30 and metrics["asm"]["identical"] and metrics["asm"]["raw_words"] <= 2
    assert metrics["callconv"]["ran"] and metrics["callconv"]["measured"] == 0     # (no lift check: no cases)
    assert metrics["noreturn"]["functions"] >= 2          # main's loop, and the reset handler that calls it
    assert json.load(open(os.path.join(where, "metrics.json")))["functions"] == metrics["functions"]
    report = open(bench.report(r, out=lambda *a: None)).read()
    assert "| m3-Os | arm-none-eabi-gcc " in report and "identical" in report     # (its --version line)


@needs_gcc
def test_the_whatcc_step_names_the_toolchain_that_linked_the_build(tmp_path, monkeypatch):
    """bench's whatcc step: dread whatcc on the build, its best toolchains against the one that built
    it (the host's here), how many names it gives as certain and how many of those are the
    functions' own; a section of the report. (The host's compilers only here: a machine with podman
    would otherwise build every toolchain image first.)"""
    monkeypatch.setenv("DREAD_BENCH", str(tmp_path / "bench"))
    real = bench.dread
    monkeypatch.setattr(bench, "dread", lambda *a, **k: real(*(a + ("--no-toolchains",) if a[0] == "whatcc" else a),
                                                             **k))
    r = recipe(tmp_path)
    bench.build(r, "m3-Os", out=lambda *a: None)
    metrics = bench.measure(r, "m3-Os", steps=("whatcc",), out=lambda *a: None)
    w = metrics["whatcc"]
    assert w["ran"] and w["truth"] == "host", w
    report = open(bench.report(r, out=lambda *a: None)).read()
    assert "## Library functions by their bytes (dread whatcc)" in report
    if w["matched"]:                        # (GCC 16's build links two of libgcc's functions; Ubuntu's
        assert w["best"] == ["host"] and w["right"] and w["agree"] == w["sure"] >= 1, w    # GCC 10 and 13's none)
        assert "| m3-Os | host | host | yes |" in report
    else:                                   # nothing matched: no best toolchain, and not right
        assert w["best"] == [] and not w["right"], w
        assert "| m3-Os | host | - | no | 0 |" in report


@needs_gcc
def test_the_groups_step_scores_the_grouping_against_the_source_files(tmp_path, monkeypatch):
    """bench's groups step: dread groups on the build against the source file of each function in its
    debug information (gizmo's five files); a section of the report."""
    pytest.importorskip("elftools")
    monkeypatch.setenv("DREAD_BENCH", str(tmp_path / "bench"))
    r = recipe(tmp_path)
    bench.build(r, "m3-Os", out=lambda *a: None)
    metrics = bench.measure(r, "m3-Os", steps=("groups",), out=lambda *a: None)
    g = metrics["groups"]
    assert g["ran"] and g["files"] == 5 and g["functions"] >= 40 and g["groups"] >= 2, g    # (another
    # compiler lays the build out otherwise: tests/test_groups.py scores gizmo's checked-in builds)
    report = open(bench.report(r, out=lambda *a: None)).read()
    assert "## Groups by code against the source files (dread groups)" in report and "| m3-Os | %d | 5 |" % (
        g["functions"]) in report


def test_a_toolchain_runs_the_compiler_in_its_container(tmp_path, monkeypatch):
    """A build that names a toolchain compiles and links in that image's container, the source
    read-only and the build's directory writable at their own paths, without network; its image is
    named by its Containerfile's digest (a changed file builds an image of its own beside the old
    one, which a run may still use) and built when missing."""
    argv = bench.in_container(["arm-none-eabi-gcc", "-c", "a.c"], "localhost/dread-tc-x", [("/src", True),
                              ("/out", False)], "/src")
    assert argv[:4] == ["podman", "run", "--rm", "--network=none"]
    assert argv[4:8] == ["-v", "/src:/src:ro", "-v", "/out:/out"] and argv[8:11] == ["-w", "/src", "localhost/dread-tc-x"]
    assert argv[11:] == ["arm-none-eabi-gcc", "-c", "a.c"]
    with pytest.raises(bench.BenchError, match="no toolchain"):
        bench.toolchain_digest("no-such-toolchain")
    assert os.path.exists(bench.toolchain_file("ubuntu-22.04"))
    image = bench.toolchain_image("ubuntu-22.04")
    assert image == "localhost/dread-tc-ubuntu-22.04:" + bench.toolchain_digest("ubuntu-22.04")[:12]
    import shutil as sh
    import subprocess
    if not sh.which("podman") or subprocess.run(["podman", "image", "exists", image]).returncode:
        pytest.skip("needs podman and the image of the toolchain ubuntu-22.04 (dread bench toolchain ubuntu-22.04)")
    monkeypatch.setenv("DREAD_BENCH", str(tmp_path / "bench"))
    path = tmp_path / "gizmo.toml"
    text = open(recipe(tmp_path)["_file"]).read().replace('project = "m3"\n', 'project = "m3"\ntoolchain = "ubuntu-22.04"\n', 1)
    path.write_text(text)
    r = bench.load(str(path))
    where = bench.build(r, "m3-Os", out=lambda *a: None)
    info = json.load(open(os.path.join(where, "build.json")))
    assert info["toolchain"] == "ubuntu-22.04" and " 10.3." in info["version"], info
    assert os.path.exists(os.path.join(where, "firmware.elf"))


@needs_gcc
def test_a_build_may_link_with_options_of_its_own(tmp_path, monkeypatch):
    """A build's link replaces its project's link options: a build of the same project with another
    C library (the RISC-V version builds link picolibc, the project newlib-nano)."""
    monkeypatch.setenv("DREAD_BENCH", str(tmp_path / "bench"))
    path = recipe(tmp_path)["_file"]
    with open(path, "a") as f:
        f.write('''
[[builds]]
name = "m3-own-link"
project = "m3"
cc = "arm-none-eabi-gcc"
cflags = ["-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft", "-Os", "-ffreestanding", "-fno-builtin",
          "-ffunction-sections", "-fno-common"]
link = ["-nostdlib", "-lgcc", "-Wl,--cref"]
''')
    r = bench.load(path)
    own = bench.build(r, "m3-own-link", out=lambda *a: None)
    project = bench.build(r, "m3-Os", out=lambda *a: None)
    assert "Cross Reference Table" in open(os.path.join(own, "work", "firmware.map")).read()
    assert "Cross Reference Table" not in open(os.path.join(project, "work", "firmware.map")).read()


@needs_gcc
def test_a_measurement_names_what_it_gained_and_lost(tmp_path, monkeypatch):
    """The lift step keeps the accepted names; a later measurement says which it gained and lost
    against the last one (in metrics.json and its output), and the last metrics.json goes to
    history/."""
    monkeypatch.setenv("DREAD_BENCH", str(tmp_path / "bench"))
    r = recipe(tmp_path)
    where = bench.build(r, "m3-Os", out=lambda *a: None)
    runs = iter([["a", "b", "c"], ["b", "c", "d"]])

    def lift_step(target, image, where, log):
        names = next(runs)
        return {"ran": True, "verdicts": {"accepted": len(names), "dropped": 1}, "reasons": {},
                "accepted": names, "seconds": 0.0}
    monkeypatch.setattr(bench, "lift_step", lift_step)
    first = bench.measure(r, "m3-Os", steps=("lift",), out=lambda *a: None)
    assert "against_last" not in first["lift"] and not os.path.exists(os.path.join(where, "history"))
    lines = []
    second = bench.measure(r, "m3-Os", steps=("lift",), out=lines.append)
    assert second["lift"]["against_last"] == {"gained": ["d"], "lost": ["a"]}
    assert "lifted 3 of 4 (+1 -1 against the last measurement)" in lines[0]
    assert lines[1] == "m3-Os: against the last measurement, gained d; lost a"
    kept = os.listdir(os.path.join(where, "history"))
    assert len(kept) == 1 and json.load(open(os.path.join(where, "history", kept[0])))["lift"]["accepted"] == ["a", "b", "c"]


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


@needs_riscv_gcc
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
    subprocess.run([RISCV_CROSS + "gcc", "-march=rv32imac", "-mabi=ilp32", "-Os", "-nostdlib", "-ffunction-sections",
                    "-msmall-data-limit=8", "-T", str(tmp_path / "t.ld"), "-e", "next", "-o", elf, str(tmp_path / "t.c")],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml('''[target]
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
'''))
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
    subprocess.run([RISCV_CROSS + "gcc", "-march=rv32imac", "-mabi=ilp32", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml(RV_TARGET))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    return Target.load(str(tmp_path))


@needs_riscv_gcc
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


def test_reasons_fall_into_categories_across_builds():
    """The report's table across builds counts the drops by what stopped them and a refusal by its
    kind with the functions' names gone, so that the same category counts alike on every build."""
    c = bench.reason_category
    assert c("dropped: UNCOVERED xTaskGetHandle: N of N instructions of the original never ran: N") == \
        "cases leave instructions unrun"
    assert c("dropped: never returns; the system run and its loop cases never ran N of its N instructions") == \
        "never returns, not all of it run"
    assert c("dropped: FAIL vAssertCalled [case generation]: only N of N generated inputs ran") == \
        "no generated input ends"
    assert c("dropped: FAIL xQueuePeek [generated N]") == "a case differs"
    a = c("refused: N reads REG behind the call of vAssertCalled (what the callee leaves)")
    b = c("refused: N reads REG behind the call of xQueueGenericSendFromISR (what the callee leaves)")
    assert a == b == "refused: N reads REG behind the call of F", a


def test_readable_reasons_fall_into_categories():
    """Why the readable gate left a function lifted, as categories across builds; the drops by what
    stopped them, as the lift check's."""
    c = bench.readable_reason
    assert c("accepted by the system run: readable C is not checked there yet") == \
        "accepted by the system run (not checked there yet)"
    assert c("no longer lifts: N reads r3") == "no longer lifts"
    assert c("the overlay: no room left for its readable C (12 bytes short)") == "no room for its readable C"
    assert c("its readable C calls __aeabi_uidiv, which the original does not call") == \
        "its readable C calls what the original does not"
    assert c("FAIL prvSenderTask_7abc [case generation]") == "no generated input ends"
    assert c("FAIL xQueuePeek [generated 3]") == "a case differs"
    assert c("UNCOVERED f: 2 of 9 instructions of the original never ran: 8000a 8000c") == \
        "cases leave instructions unrun"
    assert c("the whole image differs with its readable C (found by halves): boot") == \
        "the whole image differs with its readable C"


def test_how_the_source_side_reads_a_build():
    """clang's target for a build's preprocessed C (a clang build's own, a GNU prefix, 32-bit for an
    RV32 build of a riscv64 compiler), the C standard its compiler preprocessed (GCC 15 on: C23), and
    the source a translation unit's path names (a patched copy, a moved build: by the end)."""
    assert bench.source_target({"cc": "clang", "cflags": ["--target=thumbv7m-none-eabi", "-O2"]}) == \
        ["--target=thumbv7m-none-eabi"]
    assert bench.source_target({"cc": "arm-none-eabi-gcc", "cflags": ["-mthumb"]}) == ["--target=arm-none-eabi"]
    assert bench.source_target({"cc": "/usr/bin/riscv64-unknown-elf-gcc", "cflags": ["-march=rv32imac_zicsr"]}) == \
        ["--target=riscv32-unknown-elf"]
    assert bench.source_target({"cc": "gcc", "cflags": []}) == []
    assert bench.c_standard("#define __STDC__ 1\n#define __STDC_VERSION__ 202311L\n") == "-std=gnu2x"
    assert bench.c_standard("#define __STDC_VERSION__ 201710L\n") == "-std=gnu17"
    assert bench.c_standard("#define __STDC_VERSION__ 201112L\n") == "-std=gnu11"
    assert bench.c_standard("#define __STDC__ 1\n") == "-std=gnu89"
    units = ["FreeRTOS/Source/tasks.c", "Demo/main.c", "main.c"]
    assert bench.translation_unit("/home/x/src/freertos/FreeRTOS/Source/tasks.c", units) == "FreeRTOS/Source/tasks.c"
    assert bench.translation_unit("/bench/b/work/patched/Demo/main.c", units) == "Demo/main.c"
    assert bench.translation_unit("main.c", units) == "main.c"
    assert bench.translation_unit("/build/newlib/libc/stdio/findfp.c", units) is None
    assert bench.translation_unit(None, units) is None


@pytest.mark.slow
@needs_gcc
def test_the_readable_step_measures_readable_c_against_the_source(tmp_path, monkeypatch):
    """The readable step on gizmo-m3 (its lifted table, cases and ELF with debug information, where the
    recipe's build lies): the gate accepts what it accepts on gizmo, the figures of the readable C are
    smaller than the lifted C's, each readable function is matched to its source function, and a
    second measurement takes the gate's results again (the digest of its inputs is the same) and gives
    the same figures; the report has its tables and lists."""
    from dread import build
    from dread.target import Target
    from conftest import GIZMO
    root = tmp_path / "bench"
    monkeypatch.setenv("DREAD_BENCH", str(root))
    r = recipe(tmp_path)
    shutil.copytree(GIZMO, root / "gizmo", ignore=shutil.ignore_patterns("build"))   # (its builds share files)
    where = root / "gizmo" / "m3-Os"
    os.rename(root / "gizmo" / "m3", where)
    cases = os.path.join(GIZMO, "m3", "build", "autocases")
    if os.path.isdir(cases):
        shutil.copytree(cases, where / "build" / "autocases")
    shutil.copyfile(where / "original" / "gizmo.elf", where / "firmware.elf")
    target = Target.load(str(where))
    image = target.image()
    got = bench.readable_step(r, "m3-Os", target, image, str(where), bench=str(root), out=lambda *a: None)
    lifted = build.read_names(image.table("lifted"))
    assert got["ran"] and got["gate"] == "ran" and got["lifted"] == len(lifted), got
    assert len(got["accepted"]) == got["measured"] >= len(lifted) - 2 and got["turned"] >= len(got["accepted"])
    assert got["accepted"] == sorted(build.read_names(image.table("readable")))
    assert 0 < got["statements"]["sum"] < got["lifted_statements"]["sum"] and got["statements"]["median"] > 0
    assert got["gotos"]["sum"] < got["lifted_gotos"]["sum"] and got["variables"]["sum"] > 0
    table = (where / "work" / "readable.tsv").read_text().splitlines()
    assert table[0].startswith("function\tstatements\tdepth\tgotos") and len(table) == 1 + got["measured"]
    s = got["source"]
    if shutil.which("clang") and s.get("ran"):
        assert s["matched"] + s["part"] + sum(u["count"] for u in s["unmatched"].values()) == got["measured"]
        assert s["matched"] >= got["measured"] - 4, s          # (a linker veneer: no debug information)
        assert 0.5 <= s["ratio"][1] <= 3 and s["errors"] == {}, s
        assert all(w["unit"] in ("start_m3.c", "lib.c", "util.c", "hw.c", "main.c") for w in s["worst_larger"])
        assert len(s["worst_larger"]) <= bench.WORST and s["sum_src_statements"] > 0
    again = bench.readable_step(r, "m3-Os", target, image, str(where), bench=str(root), out=lambda *a: None)
    assert again["gate"] == "reused"
    same = ("accepted", "statements", "depth", "gotos", "raw", "members", "typed", "source")
    strip = ("seconds", "flags")
    assert {k: again[k] for k in same if k != "source"} == {k: got[k] for k in same if k != "source"}
    assert {k: v for k, v in again["source"].items() if k not in strip} == \
        {k: v for k, v in got["source"].items() if k not in strip}
    lines = bench.readable_report([{"build": "m3-Os", "readable": got}])
    assert any(line.startswith("| m3-Os | %d | " % len(lifted)) for line in lines), lines


def measurement(lifted, checked, readable=None, seconds=100.0, stamp=None):
    """A metrics.json as dread bench measure writes it (the steps the report reads), made up for a test."""
    m = {"build": "m3-Os", "functions": checked, "instruction_bytes": 4000, "image_bytes": 4400, "cc": "arm-none-eabi-gcc",
         "cflags": "-mcpu=cortex-m3 -mthumb -Os", "toolchain": None, "version": "arm-none-eabi-gcc (Example 1:2.0) 13.2.1 20231009",
         "asm": {"identical": True, "raw_words": 0, "seconds": 0.5},
         "ghidra": {"ran": True, "found": checked - 2, "missed": 2, "extra": 0, "seconds": 3.0},
         "lift": {"ran": True, "verdicts": {"accepted": lifted, "dropped": checked - lifted - 1, "refused": 1},
                  "reasons": {"dropped: UNCOVERED f: N of N instructions of the original never ran: N": checked - lifted - 1,
                              "refused: N reads a stack pointer (msp): the C's stack is not the original's": 1},
                  "accepted": ["fn_%d" % i for i in range(lifted)], "seconds": seconds},
         "noreturn": {"functions": 2, "seconds": 0.2},
         "callconv": {"ran": True, "measured": lifted, "agree": lifted - 1, "functions": {}, "seconds": 1.0,
                      "disagree": {"fn_0": {"reads, regflow does not": ["r2"]}}}}
    if readable is not None:
        from dread import cmetrics
        m["readable"] = {"ran": True, "lifted": lifted, "turned": readable + 1, "accepted": ["fn_%d" % i for i in range(readable)],
                         "stay_lifted": {"its readable C does not compile": lifted - readable}, "measured": readable,
                         "gate": "ran", "seconds_gate": 7.0, "seconds_figures": 1.0, "kept_lifted": 0,
                         "source": {"ran": False, "why": "no clang on this machine", "ratio": [0.9, 1.1, 1.4]},
                         "seconds": 9.0}
        for key in cmetrics.READABLE + ("lifted_statements", "lifted_gotos"):
            m["readable"][key] = {"sum": 10, "median": 1, "max": 4, "functions": readable}
        m["readable"]["gotos"] = {"sum": 3, "median": 0, "max": 2, "functions": 2}
    return m


def write_measurements(where, recipe_, history, now):
    """BUILD/metrics.json (now) and history/ (the earlier ones, named by their times), with build.json."""
    os.makedirs(os.path.join(where, "history"), exist_ok=True)
    for stamp, m in history:
        with open(os.path.join(where, "history", "metrics-%s.json" % stamp), "w") as f:
            json.dump(m, f)
    with open(os.path.join(where, "metrics.json"), "w") as f:
        json.dump(now, f)
    with open(os.path.join(where, "build.json"), "w") as f:
        json.dump({"recipe": "gizmo", "build": "m3-Os", "project": "m3", "cc": "arm-none-eabi-gcc", "toolchain": None,
                   "version": now["version"], "cflags": now["cflags"].split(), "sources": 5, "seconds": 1.0,
                   "made_from": bench.made_from(recipe_, "m3-Os")}, f)


def test_the_report_as_data(tmp_path, monkeypatch):
    """dread bench report --json: each build's figures as the report shows them (dread.bench FIGURES), what
    stops the rest by category, its compiler (kind, release, the flags' optimisation and cpu), whether the
    recipe changed since it was built, its metrics without every function's name, and the same figures of
    every earlier measurement (history/) oldest first; report.md as before; the same bytes twice. The
    measurements are made up here (no compiler)."""
    monkeypatch.setenv("DREAD_BENCH", str(tmp_path / "bench"))
    r = recipe(tmp_path)
    where = os.path.join(str(tmp_path / "bench"), "gizmo", "m3-Os")
    write_measurements(where, r, [("20261020-101500", measurement(30, 40)), ("20261019-090000", measurement(25, 40))],
                       measurement(33, 40, readable=31, seconds=80.0))
    out = str(tmp_path / "report.json")
    assert bench.main(["report", r["_file"], "--json", out]) == 0
    first = open(out, "rb").read()
    assert bench.main(["report", r["_file"], "--json", out]) == 0
    assert open(out, "rb").read() == first                      # (deterministic)
    data = json.loads(first)
    assert data["version"] == 1 and data["recipe"] == "gizmo" and data["missing"] == []
    assert [f["key"] for f in data["figures"]][:3] == ["functions", "instruction_bytes", "listing_identical"]
    b, = data["builds"]
    assert b["build"] == "m3-Os" and b["project"] == "m3" and b["current"] is True and b["target"] is False
    assert (b["kind"], b["release"], b["optimisation"], b["cpu"]) == ("gcc", "13.2.1", "-Os", "cortex-m3")
    f = b["figures"]
    assert (f["lifted"], f["checked"], f["lifted_share"], f["dropped"], f["refused"]) == (33, 40, 82.5, 6, 1)
    assert (f["readable"], f["readable_gotos"], f["readable_ratio"]) == (31, 3, 1.1)
    assert (f["registers_measured"], f["registers_disagree"], f["noreturn"]) == (33, 1, 2)
    assert f["seconds_lift"] == 80.0 and f["seconds"] == round(0.5 + 3.0 + 80.0 + 9.0 + 0.2 + 1.0, 1)
    assert b["stops"]["cases leave instructions unrun"] == 6 and b["refused"] == 1
    assert b["refused_most"] == {"category": "N reads a stack pointer", "count": 1} and b["not_lifted"] == 7
    assert "accepted" not in b["metrics"]["lift"] and "accepted" not in b["metrics"]["readable"]
    assert b["metrics"]["lift"]["verdicts"]["accepted"] == 33
    assert [h["file"] for h in b["history"]] == ["history/metrics-20261019-090000.json",
                                                 "history/metrics-20261020-101500.json", "metrics.json"]
    assert [h["figures"]["lifted"] for h in b["history"]] == [25, 30, 33]
    assert b["history"][0]["when"] < b["history"][1]["when"] and "readable" not in b["history"][0]["figures"]
    assert "lifted 33 of 40" in b["summary"]
    report = open(os.path.join(str(tmp_path / "bench"), "gizmo", "report.md")).read()
    assert "| m3-Os | arm-none-eabi-gcc (Example 1:2.0) 13.2.1 20231009 | 40 | 4000 | identical | 38 / 2 / 0 | 33 of 40 (82 %) |" \
        in report
    with open(os.path.join(where, "build.json")) as fh:          # (a recipe changed since: not current)
        info = json.load(fh)
    info["made_from"] = "0" * 40
    with open(os.path.join(where, "build.json"), "w") as fh:
        json.dump(info, fh)
    assert json.loads(json.dumps(bench.report_data(r)))["builds"][0]["current"] is False


def test_figures_of_older_measurements_and_releases():
    """figures() leaves out what an older metrics.json lacks; compiler_release reads the release out of the
    version lines of GCC (its package's parentheses taken out) and clang."""
    old = {"functions": 10, "asm": {"identical": False, "raw_words": 3, "seconds": 1}, "lift": {"verdicts": {"accepted": 4}}}
    assert bench.figures(old) == {"functions": 10, "listing_identical": False, "raw_words": 3, "lifted": 4, "checked": 4,
                                  "lifted_share": 100.0, "dropped": 0, "refused": 0, "seconds_asm": 1, "seconds": 1}
    for line, release in (("arm-none-eabi-gcc (15:4.9.3+svn231177-1) 4.9.3 20150529 (prerelease)", "4.9.3"),
                          ("msp430-elf-gcc (Mitto Systems Limited - msp430-gcc 8.2.0.52) 8.2.0", "8.2.0"),
                          ("Ubuntu clang version 12.0.0-3ubuntu1~20.04.5", "12.0.0"), ("clang version 23.1.3", "23.1.3"),
                          ("arm-none-eabi-gcc (15:13.2.rel1-2) 13.2.1 20231009", "13.2.1"), ("", None)):
        assert bench.compiler_release(line) == release, line
    assert (bench.compiler_kind("clang"), bench.compiler_kind("/opt/x/riscv64-elf-gcc"), bench.compiler_kind("armcl")) == \
        ("clang", "gcc", "armcl")
