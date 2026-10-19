"""dread cclab: adapters fill in their commands (Windows tools get their file names through wslpath),
and the lab, run on the compilers installed here, measures what the ARM procedure call standard says
(arguments, results, which runtime helper an operation calls and what that helper changes); the
doctor names the step that fails. Each compiler's test is skipped where it is not installed."""
import json
import os
import shutil
import stat

import pytest

from dread.cclab import adapters as ad
from dread.cclab import lab
from conftest import have

needs_gcc = pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")


def adapter_ready(name):
    """Whether a built-in adapter finds its compiler here."""
    try:
        path = ad.load(name).tool("cc")
    except ad.AdapterError:
        return False
    return os.path.exists(path) or bool(shutil.which(path))


needs_riscv = pytest.mark.skipif(not adapter_ready("gcc-riscv"), reason="needs a RISC-V GCC")
needs_msp430 = pytest.mark.skipif(not adapter_ready("gcc-msp430"), reason="needs msp430-elf-gcc")


def test_builds_are_every_combination():
    a = ad.load("gcc-arm")
    builds = a.builds()
    assert len(builds) == len(a.matrix["cpu"]) * len(a.matrix["opt"])
    key, picked, flags = builds[0]
    assert key == "cortex-m0-O0" and picked == {"cpu": "cortex-m0", "opt": "O0"}
    assert flags == ["-mcpu=cortex-m0", "-mthumb", "-O0"]
    assert a.cpu_model(picked) == "cortex-m0"


def test_commands_expand_lists_and_keep(tmp_path):
    a = ad.Adapter({"name": "t", "family": "iar", "tools": {"cc": "/bin/true", "ld": "/bin/true"},
                    "commands": {"compile": ["{cc}", "{src}", "-o", "{obj}", "{flags}"],
                                 "link": ["{ld}", "{objs}", "-o", "{out}", "--config", "{script}", "{keep}"],
                                 "keep": ["--keep", "{symbol}"]},
                    "matrix": {"cpu": {"m3": ["--cpu=Cortex-M3"]}}}, "test")
    argv = a.command("link", objs=[str(tmp_path / "a.o"), str(tmp_path / "b.o")], out=str(tmp_path / "x.out"),
                     script=str(tmp_path / "l.icf"), keep=["f", "g"])
    assert argv == ["/bin/true", str(tmp_path / "a.o"), str(tmp_path / "b.o"), "-o", str(tmp_path / "x.out"),
                    "--config", str(tmp_path / "l.icf"), "--keep", "f", "--keep", "g"]
    assert a.command("compile", src="s.c", obj="s.o", flags=["-Oh"])[-1] == "-Oh"


def test_windows_tools_get_windows_file_names(tmp_path, monkeypatch):
    """windows = true: every file name goes through `wslpath -w` (a stand-in here), the tool
    itself does not (WSL runs /mnt/c/... executables directly)."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "wslpath").write_text('#!/bin/sh\n[ "$1" = "-w" ] && echo "C:\\\\work$(echo "$2" | tr / \\\\\\\\)"\n')
    (fake / "wslpath").chmod(0o755)
    monkeypatch.setenv("PATH", str(fake) + os.pathsep + os.environ["PATH"])
    a = ad.Adapter({"name": "t", "windows": True, "tools": {"cc": "/bin/true"},
                    "commands": {"compile": ["{cc}", "{src}", "-o", "{obj}"], "link": ["{cc}"]},
                    "matrix": {"opt": {"On": ["-On"]}}}, "test")
    argv = a.command("compile", src="/mnt/c/w/p.c", obj="/mnt/c/w/p.o")
    assert argv[0] == "/bin/true"
    assert argv[1] == "C:\\work\\mnt\\c\\w\\p.c" and argv[3] == "C:\\work\\mnt\\c\\w\\p.o"


def test_a_tool_from_a_list_of_places(tmp_path):
    here = tmp_path / "opt" / "tool-2.0" / "bin"
    here.mkdir(parents=True)
    (here / "cc").write_text("")
    a = ad.Adapter({"name": "t", "tools": {"cc": [str(tmp_path / "nowhere" / "cc"), str(tmp_path / "opt" / "tool-*" / "bin" / "cc")]},
                    "commands": {"compile": ["{cc}"], "link": ["{cc}"]}, "matrix": {}}, "test")
    assert a.tool("cc") == str(here / "cc")


def measured(work, adapter, builds, guard=None):
    a = ad.load(adapter)
    from dread.cclab import run
    results = run(a, str(work), only=builds, jobs=2, out=lambda *x: None, guard=guard)
    for key, r in results.items():
        assert "error" not in r, (key, r.get("error"), [s["output"][-500:] for s in r.get("log", [])])
    return {key: r["results"] for key, r in results.items()}


@needs_gcc
def test_gcc_follows_the_procedure_call_standard(tmp_path):
    """What the ARM procedure call standard says, measured on GCC's code: the lab is right where
    the answer is known."""
    got = measured(tmp_path, "gcc-arm", ["cortex-m0-O2", "cortex-m3-O2"])
    for r in got.values():
        assert r["pick_i5_5"]["where"] == "stack+0"
        assert r["pick_i_ll"]["where"] == "r2:r3"                       # 64-bit: an even register pair
        assert r["pick_i_ll_i"]["where"] == "stack+0"                   # r1 is not filled in afterwards
        assert r["pick_iii_ll"]["where"] == "stack+0:stack+4"
        assert r["pick_ii_s12_c"]["where"] == "stack+0"                 # a struct split: r2, r3, stack
        assert r["pick_va_ll"]["where"] == "r2:r3"
        assert r["ret_ll"]["where"] == "r0:r1"
        assert r["ret_s4"]["where"] == "r0"
        assert r["ret_s8"]["where"].startswith("memory at the address in r0")
        assert r["size_enum_small"]["value"] == 1 and r["char_is_signed"]["value"] == 0   # arm-none-eabi:
        # short enums by default (the objects say Tag_ABI_enum_size: small), plain char unsigned
        assert r["align_ll"]["value"] == 8
    m0, m3 = got["cortex-m0-O2"], got["cortex-m3-O2"]
    udiv = [h for h in m0["op_udiv"]["helpers"] if h["depth"] == 1]
    assert len(udiv) == 1 and "__aeabi_uidiv" in udiv[0]["function"]   # libgcc's alias of __udivsi3
    assert udiv[0]["args"][:2] == ["0x%08x" % 1000, "0x%08x" % 7] and udiv[0]["results"][0] == "0x%08x" % 142
    assert m3["op_udiv"]["helpers"] == [] and "udiv" in m3["op_udiv"]["shape"]["mnemonics"]
    for r in got.values():                                              # what a call keeps
        assert r["keeps_pressure"]["kept"] == "all" and all(f % 8 == 0 for f in r["keeps_pressure"]["frames_at_calls"])
        assert {"r4", "return address"} <= set(r["keeps_pressure"]["saved"])
    shl = m0["op_shl64"]                                               # what a 64-bit shift does there
    assert shl["result"] == ["0x%08x" % (0x0123456789ABCDEF << 5 & 0xFFFFFFFF), "0x%08x" % (0x0123456789ABCDEF << 5 >> 32 & 0xFFFFFFFF)]


@needs_gcc
def test_where_the_functions_lie(tmp_path):
    """Each build's layout: the probes' entries are halfword-aligned at least (Thumb), each function
    starts and ends with an instruction the report names, and literal pools behind a function are
    told from padding."""
    from dread.cclab import run
    results = run(ad.load("gcc-arm"), str(tmp_path), only=["cortex-m3-O0", "cortex-m3-O2"], jobs=2,
                  out=lambda *x: None)
    for key, r in results.items():
        layout = r["layout"]
        assert layout["functions"] > 60 and layout["aligned"] in (2, 4, 8, 16)
        assert sum(layout["padding"].values()) == layout["functions"] and 0 < layout["pools"] < layout["functions"]
        assert layout["first"] and layout["last"]
    o0 = dict(results["cortex-m3-O0"]["layout"]["first"])
    assert o0.get("push", 0) > 30                    # -O0: a frame for nearly every function
    from dread.cclab import make_report
    make_report(str(tmp_path), "gcc-arm", out=lambda *x: None)
    text = (tmp_path / "gcc-arm" / "report.md").read_text()
    assert "## Where the functions lie" in text and "| where the probes' functions start | multiples of" in text


@needs_gcc
def test_the_report_compares_builds(tmp_path):
    measured(tmp_path, "gcc-arm", ["cortex-m0-Os", "cortex-m3-Os"])
    from dread.cclab import make_report
    assert make_report(str(tmp_path), "gcc-arm", out=lambda *x: None) == 0
    text = (tmp_path / "gcc-arm" / "report.md").read_text()
    assert "| `pick_i5_5` | the 5th of five ints | stack+0 (all 2) |" in text
    assert "__aeabi_uidiv" in text and "inline (udiv)" in text


@needs_gcc
def test_the_doctor_names_the_failing_step(tmp_path, capsys):
    from dread.cclab import doctor
    assert doctor(ad.load("gcc-arm"), str(tmp_path)) == 0
    assert "the 5th of five ints arrives in stack+0" in capsys.readouterr().out
    data = ad.load("gcc-arm").data
    broken = ad.Adapter(dict(data, commands=dict(data["commands"], compile=["{cc}", "-c", "{src}", "-o", "{obj}",
                                                                              "--no-such-option", "{flags}"])), "broken")
    assert doctor(broken, str(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "compile entry: exit 1" in out and "--no-such-option" in out


ICCARM = r"""#!/usr/bin/env python3
# A stand-in for IAR's iccarm: IAR's option forms (as IAR's EWARM documentation gives them), GCC below.
import subprocess, sys
OPT = {"-On": "-O0", "-Ol": "-O1", "-Om": "-O2", "-Oh": "-O2", "-Ohs": "-O3", "-Ohz": "-Os"}
CPU = {"Cortex-M3": "cortex-m3", "Cortex-M4": "cortex-m4"}
args, src, out, flags = sys.argv[1:], None, None, ["-mthumb"]
i = 0
while i < len(args):
    a = args[i]
    if a == "-o":
        out, i = args[i + 1], i + 2
        continue
    if a == "--silent":
        pass
    elif a.startswith("--cpu=") and a[6:] in CPU:
        flags.append("-mcpu=" + CPU[a[6:]])
    elif a == "--fpu=none":
        flags.append("-mfloat-abi=soft")
    elif a in OPT:
        flags.append(OPT[a])
    elif a.endswith(".c") and not a.startswith("-"):
        src = a
    else:
        print("Error[Pe999]: iccarm stand-in: unknown option %s" % a)
        sys.exit(1)
    i += 1
if not src or not out:
    print("Error: iccarm stand-in: a source and -o are needed")
    sys.exit(1)
sys.exit(subprocess.call(["arm-none-eabi-gcc", "-c", src, "-o", out] + flags))
"""

ILINKARM = r"""#!/usr/bin/env python3
# A stand-in for IAR's ilinkarm: IAR's options, the regions read from the ILINK configuration, GNU ld
# below with libgcc and the lab's support file as "IAR's library".
import os, re, subprocess, sys
args, objs, out, config, entry, keep = sys.argv[1:], [], None, None, None, []
i = 0
while i < len(args):
    a = args[i]
    if a in ("-o", "--config", "--entry", "--keep"):
        value, i = args[i + 1], i + 2
        if a == "-o": out = value
        elif a == "--config": config = value
        elif a == "--entry": entry = value
        else: keep.append(value)
        continue
    if a == "--silent":
        pass
    elif a.endswith(".o") and not a.startswith("-"):
        objs.append(a)
    else:
        print("Error[Li999]: ilinkarm stand-in: unknown option %s" % a)
        sys.exit(1)
    i += 1
text = open(config).read()
regions = dict((n, (int(b, 16), int(s, 16))) for n, b, s in
               re.findall(r"define region (\w+) = mem:\[from 0x([0-9a-fA-F]+) size 0x([0-9a-fA-F]+)\];", text))
assert "ROM_region" in regions and "RAM_region" in regions, regions
assert "initialize by copy { readwrite };" in text and "place in ROM_region { readonly };" in text
here = os.path.dirname(out)
script = os.path.join(here, "standin.ld")
open(script, "w").write("MEMORY { ROM (rx) : ORIGIN = 0x%x, LENGTH = 0x%x\n RAM (rwx) : ORIGIN = 0x%x, LENGTH = 0x%x }\n"
                        "SECTIONS { .text : { KEEP(*(.text*)) *(.rodata*) } > ROM .data : { *(.data*) } > RAM AT > ROM"
                        " .bss : { *(.bss*) *(COMMON) } > RAM }\n" % (regions["ROM_region"] + regions["RAM_region"]))
support = os.path.join(here, "obj", "standin_support.c")
open(support, "w").write(open(os.environ["STANDIN_SUPPORT"]).read())
flags = ["-mthumb", "-mcpu=cortex-m3"]
sys.exit(subprocess.call(["arm-none-eabi-gcc"] + flags + ["-nostdlib", "-T", script, "-e", entry, "-o", out] + objs
                         + [support, "-lgcc"] + ["-Wl,--undefined=" + k for k in keep]))
"""


@needs_gcc
def test_the_iar_adapter_against_a_stand_in(tmp_path, monkeypatch):
    """The built-in IAR adapter's commands, matrix and ILINK configuration, run through stand-ins
    for iccarm and ilinkarm that take IAR's documented option forms and refuse others (GCC below):
    what can be checked of IAR here without IAR."""
    import tomllib
    bins = tmp_path / "bin"
    bins.mkdir()
    for name, text in (("iccarm", ICCARM), ("ilinkarm", ILINKARM)):
        (bins / name).write_text(text)
        (bins / name).chmod(0o755)
    monkeypatch.setenv("STANDIN_SUPPORT", os.path.join(lab.PROBES, "support.c"))
    with open(ad.builtin()["iar-arm"], "rb") as f:
        data = tomllib.load(f)
    data.update(windows=False, tools={"cc": str(bins / "iccarm"), "ld": str(bins / "ilinkarm")})
    data["matrix"] = {"cpu": {"cortex-m3": data["matrix"]["cpu"]["cortex-m3"]},
                      "opt": {k: data["matrix"]["opt"][k] for k in ("On", "Ohz")}}
    path = tmp_path / "iar-standin.toml"
    lines = []
    for key, value in data.items():
        if not isinstance(value, dict):
            lines.append("%s = %s" % (key, json.dumps(value)))
    for table in ("tools", "commands"):
        lines.append("[%s]" % table)
        lines += ["%s = %s" % (k, json.dumps(v)) for k, v in data[table].items()]
    for dim, choices in data["matrix"].items():
        lines.append("[matrix.%s]" % dim)
        lines += ["%s = %s" % (k, json.dumps(v)) for k, v in choices.items()]
    path.write_text("\n".join(lines) + "\n")
    got = measured(tmp_path / "work", str(path), None)
    assert sorted(got) == ["cortex-m3-Ohz", "cortex-m3-On"]
    for r in got.values():
        assert r["pick_i5_5"]["where"] == "stack+0" and r["ret_s8"]["where"].startswith("memory")
    assert (tmp_path / "work" / "iar-arm" / "cortex-m3-On" / "link.icf").exists()


@pytest.mark.slow
@pytest.mark.parametrize("adapter", ["clang-arm", "atfe-arm", "ti-arm"])
def test_other_compilers(adapter, tmp_path):
    """The lab with clang (linked against GCC's libgcc), Arm's LLVM toolchain with its own runtime,
    and TI's own compiler, linker and linker command file: the probes build, and the facts every
    AAPCS compiler shares come out."""
    a = ad.load(adapter)
    try:
        for name in a.tools:
            path = a.tool(name)
            if not (os.path.exists(path) or shutil.which(path)):
                pytest.skip("%s is not installed" % adapter)
    except ad.AdapterError:
        pytest.skip("%s is not installed" % adapter)
    if adapter == "clang-arm" and not have("arm-none-eabi-gcc"):
        pytest.skip("clang-arm links with arm-none-eabi-gcc")
    key = [k for k, _, _ in a.builds() if "cortex-m3" in k][1]
    got = measured(tmp_path, adapter, [key])[key]
    assert got["pick_i5_5"]["where"] == "stack+0" and got["pick_i_ll"]["where"] == "r2:r3"
    assert got["ret_ll"]["where"] == "r0:r1"
    calls = {h["function"] for h in got["op_udiv64"]["helpers"] if h["depth"] == 1}
    assert any("__aeabi_uldivmod" in c for c in calls), got["op_udiv64"]


@needs_gcc
def test_the_bundle_leaves_images_and_tables_behind(tmp_path):
    """What may leave the work directory: results, logs, reports, probe sources and objects; not the
    linked image, its binary or the tables made from it (a vendor's runtime code is in them). A
    guarded work directory gets a CLAUDE.md and deny rules for Claude Code; an open compiler's
    does not need them."""
    import zipfile
    from dread.cclab import workdir
    measured(tmp_path / "plain", "gcc-arm", ["cortex-m3-O2"])
    assert not (tmp_path / "plain" / "CLAUDE.md").exists()
    assert "[llm]" not in (tmp_path / "plain" / "gcc-arm" / "cortex-m3-O2" / "dread.toml").read_text()
    measured(tmp_path / "work", "gcc-arm", ["cortex-m3-O2"], guard=True)
    assert (tmp_path / "work" / "CLAUDE.md").exists()
    assert "Read(./**/image.elf)" in (tmp_path / "work" / ".claude" / "settings.json").read_text()
    packed = workdir.bundle(str(tmp_path / "work"), str(tmp_path / "b.zip"))
    names = zipfile.ZipFile(tmp_path / "b.zip").namelist()
    assert sorted(names) == sorted(packed)
    assert "gcc-arm/cortex-m3-O2/results.json" in names and "gcc-arm/cortex-m3-O2/probes/abi.c" in names
    assert "gcc-arm/cortex-m3-O2/obj/abi.o" in names
    assert not any(n.endswith((".elf", ".bin")) or "/config/" in n or "/original/" in n for n in names)


@needs_gcc
def test_an_adapter_brings_its_own_linker_script(tmp_path):
    """An adapter's script replaces the family's: a copy of an adapter can fix what a linker
    rejects in the lab's own (for IAR's ILINK, untried here) without touching DREAD. A template
    that names a placeholder the lab does not have is the build's error, with the ones it has."""
    from dread.cclab import run
    text = open(ad.builtin()["gcc-arm"]).read().replace('name = "gcc-arm"', 'name = "scripted"')
    script = lab.GNU_SCRIPT.replace("/* dread cclab: the probes' memory; the emulator maps the same */",
                                    "/* the adapter's own */")
    path = tmp_path / "scripted.toml"
    path.write_text(text.replace('support_flags = ["-fno-builtin"]',
                                 'support_flags = ["-fno-builtin"]\nscript = """%s"""' % script))
    results = run(ad.load(str(path)), str(tmp_path / "work"), only=["cortex-m3-O2"], names=["abi"], jobs=1,
                  out=lambda *x: None)
    assert "error" not in results["cortex-m3-O2"]
    assert (tmp_path / "work" / "scripted" / "cortex-m3-O2" / "link.ld").read_text().startswith("/* the adapter's own */")
    path.write_text(text.replace('support_flags = ["-fno-builtin"]',
                                 'support_flags = ["-fno-builtin"]\nscript = "ORIGIN = {nowhere:x}"'))
    results = run(ad.load(str(path)), str(tmp_path / "work"), only=["cortex-m3-O2"], names=["abi"], jobs=1,
                  out=lambda *x: None)
    assert "placeholders: flash, flash_end, flash_size" in results["cortex-m3-O2"]["error"]


@needs_gcc
def test_an_image_with_iar_like_section_names_imports_the_same(tmp_path):
    """IAR's linker names its output sections after placement blocks (P1, P2, ...), not .text: the
    import goes by section flags and segments, so the same image under other names gives the same
    tables and payload (a stand-in for IAR's output, which cannot be tried here)."""
    import subprocess
    from dread import elfimport
    from dread.cclab import run
    run(ad.load("gcc-arm"), str(tmp_path / "work"), only=["cortex-m3-O2"], names=["abi"], jobs=1, out=lambda *x: None)
    build, copy = tmp_path / "work" / "gcc-arm" / "cortex-m3-O2", tmp_path / "iar-like"
    (copy / "original").mkdir(parents=True)
    shutil.copy(build / "dread.toml", copy / "dread.toml")
    subprocess.run(["arm-none-eabi-objcopy", "--rename-section", ".text=P1", "--rename-section", ".ARM.exidx=P2",
                    str(build / "image.elf"), str(copy / "image.elf")], check=True)
    from dread.elf import Elf
    names = [sec.name for sec in Elf(str(copy / "image.elf")).sections]
    assert "P1" in names and ".text" not in names
    assert elfimport.main(["--target", str(copy), "--payload", str(copy / "original" / "image.bin"),
                           "--write-startup", str(copy / "image.elf")]) == 0
    for name in ("config/functions.tsv", "config/code_ranges.tsv", "original/image.bin"):
        assert (copy / name).read_bytes() == (build / name).read_bytes(), name


@needs_gcc
def test_probes_of_ones_own_join_the_corpus(tmp_path):
    """A directory of one's own probes (C files with a TOML of specs) joins DREAD's: --probe-dir. A
    name used twice, a name the lab keeps for itself and a name that is not there are refused."""
    from dread.cclab import run
    own = tmp_path / "own"
    own.mkdir()
    (own / "mine.c").write_text("int mine_third(int a, int b, int c) { return c; }\n")
    (own / "mine.toml").write_text('[[probes]]\nfunction = "mine_third"\nmeasure = "arg"\nnote = "the third int"\n')
    results = run(ad.load("gcc-arm"), str(tmp_path / "work"), only=["cortex-m3-O2"], names=["mine"], jobs=1,
                  out=lambda *x: None, probe_dirs=[str(own)])
    r = results["cortex-m3-O2"]
    assert r["results"]["mine_third"]["where"] == "r2" and r["probes"] == {"mine_third": "mine"}
    with pytest.raises(ad.AdapterError, match="no probe file named nothing"):
        run(ad.load("gcc-arm"), str(tmp_path / "work"), names=["nothing"], out=lambda *x: None, probe_dirs=[str(own)])
    for clash in ("abi", "entry"):
        (own / (clash + ".c")).write_text("int x;\n")
        (own / (clash + ".toml")).write_text("")
        with pytest.raises(ValueError, match="a probe file named %s" % clash):
            lab.probes(directories=[str(own)])
        (own / (clash + ".c")).unlink()


@needs_gcc
def test_hard_float_follows_the_vfp_variant(tmp_path):
    """Floats and doubles measured on GCC's Cortex-M4 with hard float (on the emulator's own M4,
    which has the FPU) and with soft float: s0-s15, doubles in even pairs, back-filling, a struct of
    floats in consecutive registers and a variadic double in the core registers, returned there
    too (AAPCS: a variadic function uses the base standard throughout); soft float as integers."""
    got = measured(tmp_path, "gcc-arm", ["cortex-m4f-O2", "cortex-m4-O2"])
    hard, soft = got["cortex-m4f-O2"], got["cortex-m4-O2"]
    expect = {"ret_f": ("s0", "r0"), "ret_d": ("s0:s1", "r0:r1"), "pick_f_3": ("s2", "r2"),
              "pick_f_17": ("stack+0", "stack+48"), "pick_if_f": ("s0", "r1"), "pick_i5_f": ("s0", "stack+4"),
              "pick_d_2": ("s2:s3", "r2:r3"), "pick_fd_d": ("s2:s3", "r2:r3"), "pick_dfdf_f": ("s3", "stack+8"),
              "pick_hfa_c": ("s2", "r2"), "pick_va_d": ("r2:r3", "r2:r3")}
    for fn, (h, s) in expect.items():
        assert (hard[fn].get("where"), soft[fn].get("where")) == (h, s), (fn, hard[fn], soft[fn])
    assert hard["pick_va_d"]["returned"] == "r0:r1" and "returned" not in soft["pick_va_d"]
    assert hard["keeps_fp"]["kept"] == "all" and any(r.startswith("s1") for r in hard["keeps_fp"]["saved"])
    assert soft["keeps_fp"]["kept"] == "all" and not any(r.startswith("s") for r in soft["keeps_fp"]["saved"])
    from dread.cclab import compare
    assert compare.hint("pick_dfdf_f", "s6", "s3").startswith("no option")


@needs_gcc
def test_compare_names_the_options_that_close_a_difference(tmp_path):
    """Two compilers side by side: GCC with int-sized enums and signed plain char as the original's
    compiler, GCC as it comes (arm-none-eabi: short enums, unsigned char) as the rebuilding one. The
    comparison lists those differences and names the options that close them; the rest agrees."""
    from dread.cclab import compare
    text = open(ad.builtin()["gcc-arm"]).read().replace('name = "gcc-arm"', 'name = "variant"')
    text = text.replace('support_flags = ["-fno-builtin"]',
                        'support_flags = ["-fno-builtin"]\ncflags = ["-fno-short-enums", "-fsigned-char"]')
    (tmp_path / "variant.toml").write_text(text)
    measured(tmp_path, str(tmp_path / "variant.toml"), ["cortex-m3-O2"])
    measured(tmp_path, "gcc-arm", ["cortex-m3-O2"])
    said = []
    assert compare.write(str(tmp_path), "variant", "gcc-arm", out=said.append) == 0
    md = (tmp_path / "compare-variant-gcc-arm.md").read_text()
    assert "- -fno-short-enums: `size_enum_small`" in md and "- -fsigned-char: `char_is_signed`" in md
    assert "| `size_enum_small` | sizeof an enum of 0 and 1 | 4 (all 1) | 1 (all 1) |" in md
    assert "`pick_i5_5`" not in md and "probes agree on what linking depends on." in md
    assert compare.write(str(tmp_path), "variant", "nothing", out=said.append) == 1


@needs_gcc
def test_the_pilot_keeps_to_the_probes_of_a_guarded_build(tmp_path):
    """A guarded build (a proprietary toolchain's) names the functions an LLM may be shown, the
    probes and the lab's entry, in its dread.toml; dread pilot asks about those only, never about
    the toolchain's runtime library."""
    from dread import pilot
    from dread.program import Program
    from dread.target import Target
    measured(tmp_path, "gcc-arm", ["cortex-m3-O2"], guard=True)
    target = Target.load(str(tmp_path / "gcc-arm" / "cortex-m3-O2"))
    image = target.image()
    program = Program(target, image)
    assert {"pick_i5_5", "op_udiv", "cclab_entry"} <= target.llm_only
    helper = next(n for n in sorted(program.by_name) if n.startswith("__") and n not in target.llm_only)
    said = []
    assert pilot.shown(target, ["pick_i5_5", helper], out=said.append) == ["pick_i5_5"] and said
    with open(image.table("lifted"), "w") as f:
        f.write("pick_i5_5\n%s\n" % helper)
    assert pilot.candidates(target, image, program) == ["pick_i5_5"]


@needs_riscv
def test_riscv_gcc_follows_its_psabi(tmp_path):
    """The RISC-V psABI's rules, measured on GCC's RV32 code: eight argument registers a0-a7; a
    64-bit scalar in any free pair, a variadic one in an even pair; a struct of more than two words
    by reference; results of up to two words in a0:a1, larger ones through a pointer in a0; narrow
    results extended; the stack 16-byte aligned at calls; no M extension, division by a helper."""
    got = measured(tmp_path, "gcc-riscv", ["rv32imc-O2", "rv32ic-Os"])
    for r in got.values():
        assert r["pick_i5_5"]["where"] == "a4" and r["pick_i8_8"]["where"] == "a7"
        assert r["pick_i_ll"]["where"] == "a1:a2"
        assert r["pick_va_ll"]["where"] == "a2:a3"
        assert r["pick_s12_c"]["where"] == "memory at a0+8"
        assert r["ret_s8"]["where"] == "a0:a1" and r["ret_s12"]["where"].startswith("memory at the address in a0")
        assert r["ret_schar"]["value"] == 0xffffff88
    with_m, without = got["rv32imc-O2"], got["rv32ic-Os"]
    assert with_m["op_udiv"]["helpers"] == [] and "divu" in with_m["op_udiv"]["shape"]["mnemonics"]
    assert any("__udivsi3" in h["function"] for h in without["op_udiv"]["helpers"])
    from dread.cclab import make_report
    make_report(str(tmp_path), "gcc-riscv", out=lambda *x: None)
    assert "a multiple of 16 bytes" in (tmp_path / "gcc-riscv" / "report.md").read_text()


@needs_msp430
def test_msp430_gcc_follows_its_eabi(tmp_path):
    """The MSP430 EABI's rules, measured on GCC's code (16-bit registers): arguments in r12-r15, a
    64-bit value in all four or on the stack, a later argument in a register left free; structs by
    reference both ways; 32-bit long, int-sized enums; division by a helper; the stack word-aligned."""
    r = measured(tmp_path, "gcc-msp430", ["msp430-O2"])["msp430-O2"]
    assert r["pick_i5_1"]["where"] == "r12" and r["pick_i5_5"]["where"] == "stack+0"
    assert r["pick_ll_1"]["where"] == "r12:r13:r14:r15"
    assert r["pick_i_ll"]["where"] == "stack+0:stack+2:stack+4:stack+6"
    assert r["pick_i_ll_i"]["where"] == "r13"
    assert r["pick_s8_b"]["where"] == "memory at r12+2"
    assert r["ret_s4"]["where"].startswith("memory at the address in r12")
    assert r["size_long"]["value"] == 4 and r["size_enum_small"]["value"] == 2
    assert any("__mspabi_divu" in h["function"] for h in r["op_udiv"]["helpers"])
    from dread.cclab import make_report
    make_report(str(tmp_path), "gcc-msp430", out=lambda *x: None)
    assert "a multiple of 2 bytes" in (tmp_path / "gcc-msp430" / "report.md").read_text()


def test_a_target_takes_helper_results_from_a_profile(tmp_path):
    """[compiler] profile names a dread cclab profile: dread.regflow takes each helper's results as
    read after its calls (what the compiler's own code expects), whatever the image's callers do."""
    from conftest import GIZMO
    from dread import regflow
    from dread.program import Program
    from dread.target import Target
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    (copy / "m3" / "profile.toml").write_text('[compiler]\nadapter = "test"\n\n[helpers."checksum"]\n'
                                              'names = ["checksum"]\nresults = ["r0", "r1"]\n')
    toml = (copy / "m3" / "dread.toml").read_text()
    assert "[compiler]\n" in toml
    (copy / "m3" / "dread.toml").write_text(toml.replace("[compiler]\n", '[compiler]\nprofile = "profile.toml"\n', 1))
    target = Target.load(str(copy / "m3"))
    image = target.image()
    flow = regflow.load(target, image, Program(target, image))
    assert "r1" in flow.kept_regs("checksum")
    assert "the compiler's profile (its result)" in flow.why("checksum", "r1")
