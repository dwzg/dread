"""dread lift --check where the link refuses one function's C. On a core without Thumb-2, GCC makes a
switch at -Os a call of libgcc's __gnu_thumb1_case_* (the table behind the call), which the overlay
does not link and an image built for speed does not have: until 2026-10-25 the link failed and with
it the check of the whole build (the compiler lab's GCC 16 Cortex-M0 builds at -O2 and -O3), while
at -Os, where the image has the helper, the function alone was dropped. Now the check asks before
each link which C calls what the original does not (dread.lift foreign_calls through dread.build's
vet), drops the functions whose objects or sections the linker's errors name where that misses
(dread.overlay blame), fails only on an error that names no function it may drop, and lifted C on
such a core compares instead of calling the helper (the ARM layer's lifted_cflags: -fno-jump-tables
for GCC). A program of our own, in assembly: a switch through a table of addresses as GCC -O2 makes
it on Thumb-1, a leaf, a caller of the leaf and an entry."""
import shutil
import subprocess

import pytest

from dread import build, elfimport, lift, overlay
from dread.elf import Elf
from dread.gui import read_tsv
from dread.target import Target

needs_arm = pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                               reason="needs arm-none-eabi-as and -gcc")

SWITCH_T1_S = """
    .syntax unified
    .cpu cortex-m0
    .thumb
    .text
    .global pick, twice, sum3, use
    .type pick, %function
    .thumb_func
pick:                           @ out[] by the index, through a table of addresses (GCC -O2 on Thumb-1)
    cmp     r0, #5
    bhi     .Lpick_out
    ldr     r2, =pick_table
    lsls    r3, r0, #2
    ldr     r3, [r2, r3]
    mov     pc, r3
.Lpick_0:
    movs    r3, #10
    str     r3, [r1]
    bx      lr
.Lpick_1:
    ldr     r3, [r1]
    adds    r3, #20
    str     r3, [r1, #4]
    bx      lr
.Lpick_2:
    ldr     r3, [r1, #4]
    lsls    r3, r3, #1
    str     r3, [r1, #8]
    bx      lr
.Lpick_3:
    movs    r3, #3
    str     r3, [r1, #12]
    bx      lr
.Lpick_4:
    ldr     r3, [r1, #8]
    subs    r3, #7
    str     r3, [r1]
    bx      lr
.Lpick_5:
    movs    r0, #0
    bx      lr
.Lpick_out:
    movs    r0, #1
    rsbs    r0, r0, #0
    bx      lr
    .ltorg
    .size pick, . - pick
    .type twice, %function
    .thumb_func
twice:
    adds    r0, r0, r0
    bx      lr
    .size twice, . - twice
    .type sum3, %function
    .thumb_func
sum3:                           @ twice(a) + b + c
    push    {r4, lr}
    adds    r4, r1, r2
    bl      twice
    adds    r0, r0, r4
    pop     {r4, pc}
    .size sum3, . - sum3
    .type use, %function
    .thumb_func
use:
    push    {r4, lr}
    ldr     r1, =0x20000100
    movs    r0, #2
    bl      pick
    movs    r0, #1
    movs    r1, #2
    movs    r2, #3
    bl      sum3
    pop     {r4, pc}
    .ltorg
    .size use, . - use
    .org    0x200
    .space  0x400
    .section .rodata
    .p2align 2
pick_table:
    .word   .Lpick_0, .Lpick_1, .Lpick_2, .Lpick_3, .Lpick_4, .Lpick_5
"""

NAMES = ["pick", "twice", "sum3", "use"]

TOML = '''
[target]
name = "switch-m0"

[arch]
name = "arm"
isa = "t32"
profile = "m"
cpu = "cortex-m0"

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

[image.main]
original = "original/t.bin"
base = 0x08000000
limit = 0x08010000
free = [[0x08000200, 0x08000600]]

[compiler]
cross = "arm-none-eabi-"
cflags = ["-mthumb", "-mcpu=cortex-m0", "-Os"]
'''


def switch_program(tmp_path, lifted_cflags=None):
    """The program built and its tables imported: a target in tmp_path. lifted_cflags: the target's
    [compiler] lifted_cflags (None: the default, -Os and the ARM layer's flags)."""
    (tmp_path / "t.s").write_text(SWITCH_T1_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "use", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    toml = TOML
    if lifted_cflags is not None:
        toml += "lifted_cflags = [%s]\n" % ", ".join('"%s"' % f for f in lifted_cflags)
    (tmp_path / "dread.toml").write_text(toml)
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    return Target.load(str(tmp_path))


def verdicts(tmp_path):
    return {r["function"]: r for r in read_tsv(str(tmp_path / "build" / "lift-check" / "main" / "verdicts.tsv"))}


@needs_arm
def test_a_helper_the_image_lacks_drops_its_function_before_the_link(tmp_path):
    """With lifted_cflags = ["-Os"] alone, GCC calls a switch helper from pick's C, which the image
    does not have (the program was built without it): pick is dropped before the link, with the
    reason the check gave at -Os where the image has the helper, and every other function gets its
    verdict. Before, the link failed and the check with it ("the build failed: the link failed")."""
    t = switch_program(tmp_path, ["-Os"])
    lines = []
    accepted = lift.check(t, t.image(), NAMES, out=lines.append, jobs=2)
    text = "\n".join(lines)
    assert "the build failed" not in text, text
    assert sorted(accepted) == ["sum3", "twice", "use"], text
    got = verdicts(tmp_path)
    assert sorted(got) == sorted(NAMES), got
    assert got["pick"]["verdict"] == "dropped", got["pick"]
    assert "its C calls __gnu_thumb1_case_" in got["pick"]["reason"], got["pick"]
    assert "which the original does not call" in got["pick"]["reason"], got["pick"]
    assert all(got[n]["verdict"] == "accepted" for n in ("sum3", "twice", "use")), got


@needs_arm
def test_a_link_error_drops_the_function_whose_object_it_names(tmp_path, monkeypatch):
    """Where the check before the link misses (here: told nothing), the link fails on pick's call of
    the helper; the check drops pick with the linker's words, which name its section (newer binutils
    its object too: dread.overlay blame), links again and gives every other function its verdict."""
    t = switch_program(tmp_path, ["-Os"])
    monkeypatch.setattr(lift, "foreign_calls", lambda *args, **kwargs: {})
    lines = []
    accepted = lift.check(t, t.image(), NAMES, out=lines.append, jobs=2)
    text = "\n".join(lines)
    assert "the build failed" not in text, text
    assert sorted(accepted) == ["sum3", "twice", "use"], text
    reason = verdicts(tmp_path)["pick"]["reason"]
    assert reason.startswith("the link of its C failed ("), reason
    assert "undefined reference to `__gnu_thumb1_case_" in reason, reason


@needs_arm
def test_a_link_error_that_names_no_lifted_function_fails_the_check(tmp_path):
    """twice written by hand calls a function nobody defines: the linker names twice's object, but
    twice is not the check's to drop (src defines it), so the check fails with the linker's message
    and writes no lifted table, as for a source that does not compile."""
    t = switch_program(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "by_hand.c").write_text("unsigned missing_helper(unsigned);\n"
                                                "unsigned twice(unsigned x) { return missing_helper(x); }\n")
    lines = []
    with pytest.raises(build.BuildFailed) as failed:
        lift.check(t, t.image(), NAMES, out=lines.append, jobs=2)
    text = "\n".join(lines)
    assert "the build failed" in text and "missing_helper" in text, text
    assert "missing_helper" in str(failed.value)
    assert not (tmp_path / "config" / "lifted.txt").exists()


@needs_arm
def test_lifted_c_on_thumb_1_compares_where_gcc_would_call_a_switch_helper(tmp_path):
    """By default the lifted C on a core without Thumb-2 is compiled with -fno-jump-tables (the ARM
    layer's lifted_cflags for GCC): pick's C compares the index instead of calling the helper, and
    pick is accepted with the others."""
    t = switch_program(tmp_path)
    assert t.compiler.lifted_cflags == ["-Os", "-fno-jump-tables"]
    lines = []
    accepted = lift.check(t, t.image(), NAMES, out=lines.append, jobs=2)
    assert sorted(accepted) == sorted(NAMES), "\n".join(lines)
    obj = tmp_path / "build" / "lift-check" / "main" / "obj-lifted" / "pick.o"
    undefined = {s.name for s in Elf(str(obj)).symbols if s.shndx == 0 and s.name}
    assert not any(n.startswith("__gnu_thumb1_case_") for n in undefined), undefined


needs_clang = pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")),
                                 reason="needs clang and ld.lld")


@needs_arm
@pytest.mark.parametrize("compiler", ["gcc", "gcc -g", pytest.param("clang", marks=needs_clang)])
def test_the_link_names_the_function_whose_c_it_refuses(tmp_path, compiler):
    """dread.overlay link with GNU ld and with ld.lld: twice's C calls a function nobody defines, sum3's
    is sound; the link fails, and its error names twice alone, with the linker's words, paths by their
    file names (GNU ld by its section, behind a line naming its object; with debug information
    binutils 2.38 names only the source line, and the line in front names the function; lld
    by its object, in a line of its own behind its message; binutils 2.47 adds a line naming the
    object, 2.38 and 2.42 do not)."""
    t = switch_program(tmp_path)
    if compiler == "gcc -g":
        toml = tmp_path / "dread.toml"
        toml.write_text(toml.read_text().replace('"-Os"]', '"-Os", "-g"]'))
        t = Target.load(str(tmp_path))
    if compiler == "clang":
        toml = tmp_path / "dread.toml"
        text = toml.read_text().replace('cross = "arm-none-eabi-"', 'kind = "clang"\ntriple = "thumbv6m-none-eabi"')
        toml.write_text(text.replace('"-mthumb", "-mcpu=cortex-m0", "-Os"',
                                     '"-mcpu=cortex-m0", "-Os", "-ffreestanding"'))
        t = Target.load(str(tmp_path))
    src = tmp_path / "c"
    src.mkdir()
    (src / "twice.c").write_text("unsigned nowhere_defined(unsigned);\n"
                                 "unsigned twice(unsigned x) { return nowhere_defined(x) + x; }\n")
    (src / "sum3.c").write_text("unsigned twice(unsigned);\n"
                                "unsigned sum3(unsigned a, unsigned b, unsigned c) { return twice(a) + b + c; }\n")
    objects = build.compile_all(t, t.image(), str(src), str(tmp_path / "obj"), quiet=True)
    with pytest.raises(overlay.OverlayError) as failed:
        overlay.link(t, t.image(), objects, str(tmp_path / "out" / "payload.bin"), str(tmp_path / "out"))
    blamed = failed.value.blamed
    assert sorted(blamed) == ["twice"], (blamed, str(failed.value))
    assert blamed["twice"].startswith("the link of its C failed (") and "nowhere_defined" in blamed["twice"], blamed
    assert str(tmp_path) not in blamed["twice"], blamed      # (objects by their file names, where named)


def flags_of(tmp_path, arch, compiler):
    (tmp_path / "dread.toml").write_text(
        '[target]\nname = "f"\n[arch]\nname = "arm"\n%s\n[image.main]\noriginal = "t.bin"\nbase = 0\n[compiler]\n%s\n'
        % (arch, compiler))
    return Target.load(str(tmp_path)).compiler.lifted_cflags


def test_the_switch_flag_is_for_gcc_on_thumb_without_thumb_2(tmp_path):
    """-fno-jump-tables only where GCC would call a switch helper: Thumb code of a core without Thumb-2,
    whether the cpu or the flags say so; not on Thumb-2, not in ARM state (ARMv4T's -marm: its tables
    are inline), not for clang (an inline table on Thumb-1 too), and not when the target names its
    lifted_cflags itself."""
    m = 'isa = "t32"\nprofile = "m"'
    assert flags_of(tmp_path, m + '\ncpu = "cortex-m0"', 'cflags = ["-mthumb"]') == ["-Os", "-fno-jump-tables"]
    assert flags_of(tmp_path, m, 'cflags = ["-mthumb", "-mcpu=cortex-m0"]') == ["-Os", "-fno-jump-tables"]
    assert flags_of(tmp_path, m, 'cflags = ["-mthumb", "-march=armv8-m.base"]') == ["-Os", "-fno-jump-tables"]
    assert flags_of(tmp_path, 'isa = "t32"\nprofile = "a"', 'cflags = ["-march=armv4t", "-mthumb"]') == \
        ["-Os", "-fno-jump-tables"]
    assert flags_of(tmp_path, m + '\ncpu = "cortex-m3"', 'cflags = ["-mthumb"]') == ["-Os"]
    assert flags_of(tmp_path, m, 'cflags = ["-mthumb", "-mcpu=cortex-m4"]') == ["-Os"]
    assert flags_of(tmp_path, 'isa = "a32"\nprofile = "a"', 'cflags = ["-march=armv4t", "-marm"]') == ["-Os"]
    assert flags_of(tmp_path, m + '\ncpu = "cortex-m0"', 'kind = "clang"\ntriple = "thumbv6m-none-eabi"') == ["-Os"]
    assert flags_of(tmp_path, m + '\ncpu = "cortex-m0"', 'cflags = ["-mthumb"]\nlifted_cflags = ["-Os"]') == ["-Os"]


GNU_LD = """\
/usr/bin/arm-none-eabi-ld: /w/obj-lifted/pick.o: in function `pick':
pick.c:(.text.pick+0x26): undefined reference to `__gnu_thumb1_case_uqi'
/usr/bin/arm-none-eabi-ld: (__gnu_thumb1_case_uqi): Unknown destination type (ARM/Thumb) in /w/obj-lifted/pick.o
pick.c:(.text.pick+0x26): dangerous relocation: unsupported relocation
/usr/bin/arm-none-eabi-ld: /w/obj-lifted/sum3.o: in function `sum3':
/w/lifted/sum3.c:12: relocation truncated to fit: R_ARM_THM_CALL against symbol `twice' defined in .text.twice \
section in /w/obj-lifted/twice.o
/usr/bin/arm-none-eabi-ld: /w/obj/by_hand.o: in function `many':
by_hand.c:33: undefined reference to `missing'
/usr/bin/arm-none-eabi-ld: /w/obj-lifted/use.o: in function `use':
use.c:(.text.use+0x2): warning: an odd thing
/usr/bin/arm-none-eabi-ld: warning: recon.elf has a LOAD segment with RWX permissions
/usr/bin/arm-none-eabi-ld: the C may not add zero-initialised data
collect2: error: ld returned 1 exit status
"""

LLD = """\
ld.lld: error: undefined symbol: __gnu_thumb1_case_uqi
>>> referenced by pick.c
>>>               /w/obj-lifted/pick.o:(pick)
ld.lld: error: /w/obj-lifted/sum3.o:(.text.sum3+0x4): relocation R_ARM_THM_CALL out of range: 5000000 is not in \
[-4194304, 4194303]; references 'twice'
>>> defined in /w/obj-lifted/twice.o
ld.lld: error: section .recon.use file range overlaps with .recon.tail
ld.lld: error: too many errors emitted, stopping now (use --error-limit=0 to see all errors)
"""


def test_blame_holds_each_message_against_the_function_it_names():
    """dread.overlay blame on what GNU ld and lld write: an undefined reference and the relocation
    behind it count for the function whose object or section they name (lld's by the object in its
    ">>>" lines), a relocation that does not fit for the caller whose place it names, not for the
    callee named after it, a source line behind GNU ld's "in function" line for that line's
    function; a warning for nobody; the linker's own messages, and those of an object of several
    functions written by hand, name none."""
    by_object = {"/w/obj-lifted/%s.o" % n: n for n in ("pick", "sum3", "twice", "use")}
    by_section = {".text." + n: n for n in ("pick", "sum3", "twice", "use", "many")}
    by_section.update({".recon.use": "use", ".text.pick_dread_body": "pick"})
    found, unnamed = overlay.blame(GNU_LD, by_object, by_section)
    assert found == {
        "pick": ["pick.c:(.text.pick+0x26): undefined reference to `__gnu_thumb1_case_uqi'",
                 "(__gnu_thumb1_case_uqi): Unknown destination type (ARM/Thumb) in pick.o",
                 "pick.c:(.text.pick+0x26): dangerous relocation: unsupported relocation"],
        "sum3": ["sum3.c:12: relocation truncated to fit: R_ARM_THM_CALL against symbol `twice' defined in "
                 ".text.twice section in twice.o"]}, found
    assert unnamed == ["by_hand.c:33: undefined reference to `missing'", "the C may not add zero-initialised data",
                       "error: ld returned 1 exit status"]
    old = "/w/obj-lifted/sum3.o: In function `sum3':\n/w/lifted/sum3.c:12: undefined reference to `x'\n"
    assert overlay.blame(old, by_object, by_section) == ({"sum3": ["sum3.c:12: undefined reference to `x'"]},
                                                         [])            # (binutils 2.27, 2.28)
    found, unnamed = overlay.blame(LLD, by_object, by_section)
    assert sorted(found) == ["pick", "sum3", "use"], found
    assert found["pick"] == ["error: undefined symbol: __gnu_thumb1_case_uqi referenced by pick.c pick.o:(pick)"]
    assert "out of range" in found["sum3"][0] and "use" in found and len(unnamed) == 1, (found, unnamed)
