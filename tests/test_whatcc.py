"""dread whatcc on a library of our own: its functions found in an image without names."""
import os
import shutil
import subprocess

import pytest

from dread import elfimport, whatcc
from dread.program import Program
from dread.target import Target

LIB_C = {
    "lib_sum.c": "int lib_sum(const int *p, int n) { int s = 0; while (n-- > 0) s += *p++ * 3 + 1; return s; }\n",
    "lib_scale.c": "int lib_sum(const int *p, int n);\n"
                   "int lib_scale(const int *p, int n, int k) { return lib_sum(p, n) * k + n; }\n",
    "lib_get.c": "extern int lib_state;\nint lib_get(void) { return lib_state; }\n",
}
MAIN_C = """
int lib_scale(const int *p, int n, int k);
int lib_get(void);
int lib_state, own_counter;
int own_get(void) { return own_counter; }              /* a getter: the same code as lib_get */
int own_mix(int a, int b) { return (a ^ 0x5a5a) * b - (a >> 3); }
int start(int *p) { return lib_scale(p, 4, 7) + lib_get() + own_get() + own_mix(p[0], p[1]); }
"""


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_library_of_our_own_names_its_functions(tmp_path):
    """The library's functions are found by their bytes in an image imported without names, the
    linker's fields masked (lib_scale's call of lib_sum, lib_get's address of lib_state); lib_scale
    keeps its name because its call goes to the function that matched lib_sum; lib_get's code is
    also the image's own getter's, so that name is given as ambiguous, not as a fact."""
    from test_bench import TARGET
    cc = ["arm-none-eabi-gcc", "-mthumb", "-mcpu=cortex-m3", "-Os", "-ffunction-sections", "-c"]
    objs = []
    for name, text in LIB_C.items():
        (tmp_path / name).write_text(text)
        obj = str(tmp_path / (name[:-2] + ".o"))
        subprocess.run(cc + ["-o", obj, str(tmp_path / name)], check=True)
        objs.append(obj)
    lib = str(tmp_path / "libown.a")
    subprocess.run(["arm-none-eabi-ar", "rcs", lib] + objs, check=True)
    (tmp_path / "main.c").write_text(MAIN_C)
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH\n .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mthumb", "-mcpu=cortex-m3", "-Os", "-ffunction-sections", "-nostdlib",
                    "-T", str(tmp_path / "t.ld"), "-e", "start", "-o", elf, str(tmp_path / "main.c"), lib], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mthumb", "-mcpu=cortex-m3"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--unnamed", elf]) == 0
    entries = {s.name: s.value & ~1 for s in __import__("dread.elf", fromlist=["Elf"]).Elf(elf).symbols if s.name}
    assert whatcc.main(["--target", str(tmp_path), "--archive", lib, "--no-host", "--no-toolchains"]) == 0
    rows = {}
    for line in open(os.path.join(str(tmp_path), "build", "whatcc", "main", "matches.tsv")).read().splitlines()[1:]:
        entry, name, matched, ambiguous, groups = line.split("\t")
        rows[int(entry, 16)] = (name, matched.split(","), ambiguous.split(",") if ambiguous else [])
    assert rows[entries["lib_sum"]][1] == ["lib_sum"] and not rows[entries["lib_sum"]][2]
    assert rows[entries["lib_scale"]][1] == ["lib_scale"] and not rows[entries["lib_scale"]][2]
    assert rows[entries["lib_get"]][2] == ["lib_get"] and rows[entries["own_get"]][2] == ["lib_get"]
    assert entries["own_mix"] not in rows and entries["start"] not in rows
    assert rows[entries["lib_sum"]][0].startswith("FUN_")             # (imported without names)


@pytest.mark.skipif(not shutil.which("arm-none-eabi-ar"), reason="needs arm-none-eabi-ar")
def test_archive_members_with_long_names(tmp_path):
    """GNU ar keeps names longer than 15 characters in a table of their own ("//")."""
    for name in ("short.o", "a_member_with_a_rather_long_name.o"):
        (tmp_path / name).write_bytes(b"\x7fELF" + name.encode())
    lib = str(tmp_path / "libx.a")
    subprocess.run(["arm-none-eabi-ar", "rc", lib, str(tmp_path / "short.o"),
                    str(tmp_path / "a_member_with_a_rather_long_name.o")], check=True)
    members = dict(whatcc.archive_members(lib))
    assert members == {"short.o": b"\x7fELFshort.o",
                       "a_member_with_a_rather_long_name.o": b"\x7fELFa_member_with_a_rather_long_name.o"}
