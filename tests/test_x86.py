"""The x86 layer (32-bit, i386): flow and register effects of chosen encodings, lengths up to fifteen
bytes, and gizmo-i386's two builds (the host's GCC with -m32, clang for i686-unknown-elf) listed so
that GNU as and clang rebuild them byte for byte."""
import os
import shutil
import subprocess

import pytest

from dread.arch.x86 import X86
from conftest import gizmo_dir


def insn(hexbytes, address=0x08000000):
    return X86().decode(bytes.fromhex(hexbytes), address)


def test_flow_and_effects():
    """Calls, returns, jumps (direct, conditional, through a register), hlt and int; the registers an
    instruction reads and writes, a write to al keeping (so reading) the rest of eax, xor of a register
    with itself reading nothing; push and pop; the conditions and their opposites; cdecl takes no
    register argument, -mregparm=3 eax, edx and ecx."""
    a = X86()
    assert a.flow(insn("e8fb000000")) == ("call", 0x08000100, True)        # call rel32
    assert a.flow(insn("c3")) == ("return", None, False)                   # ret
    assert a.flow(insn("eb10")) == ("jump", 0x08000012, False)             # jmp rel8
    assert a.flow(insn("7410")) == ("jump", 0x08000012, True)              # je rel8
    assert a.flow(insn("ffe0")) == ("ijump", "eax", False)                 # jmp eax
    assert a.flow(insn("ffd0")) == ("icall", None, True)                   # call eax
    assert a.flow(insn("f4")) == ("stop", None, False)                     # hlt
    assert a.flow(insn("cd80")) == ("icall", None, True)                   # int 0x80
    assert a.reg_effects(insn("01c8")) == ({"eax", "ecx"}, {"eax", "flags"})        # add eax, ecx
    assert a.reg_effects(insn("31c0")) == (set(), {"eax", "flags"})                 # xor eax, eax
    assert a.reg_effects(insn("b001")) == ({"eax"}, {"eax"})                        # mov al, 1
    assert a.reg_effects(insn("0f94c0")) == ({"flags", "eax"}, {"eax"})             # sete al
    assert a.stack_kind(insn("53")) == "push" and a.pushed_registers(insn("53")) == ["ebx"]
    assert a.stack_kind(insn("5b")) == "pop" and a.stack_kind(insn("89c8")) is None
    assert a.predicate(insn("7410")) == "e" and a.complement("e") == "ne" and a.complement("a") == "be"
    assert a.arg_regs(0) == [] and a.arg_regs(3) == ["eax", "edx", "ecx"]
    with pytest.raises(ValueError):
        a.arg_regs(4)


def test_lengths_take_up_to_fifteen_bytes():
    """An instruction's length from its bytes (the program database reads max_insn of them): a
    one-byte push, a seven-byte store of an immediate, a ten-byte one with a 32-bit displacement and
    immediate, an operand-size prefix."""
    a = X86()
    for hexbytes, n in (("53", 1), ("c7450400000000", 7), ("c78578563412efbeadde", 10), ("66b80100", 4)):
        assert a.insn_length(bytes.fromhex(hexbytes) + b"\x90" * 8, "i386") == n, hexbytes
    assert a.max_insn == 15


@pytest.mark.parametrize("variant, tool", [("i386", "as"), ("i386clang", "clang")])
def test_gizmo_i386_listings_rebuild_byte_for_byte(variant, tool, tmp_path):
    """dread asm --verify on both builds: the AT&T listing (jumps and calls to labels, the read-only
    data in .text as data) that GNU as (the GCC build) and clang (the clang build) assemble back to
    the image, raw bytes only where the build's table says."""
    if not shutil.which(tool):
        pytest.skip("needs " + tool)
    from dread import mkasm
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir(variant))
    program = Program(target)
    assert len(program.functions) >= 40 and program.by_name["main"]
    raw = tmp_path / "raw.txt"
    shipped = os.path.join(gizmo_dir(variant), "config", "asm_raw_words.txt")
    if os.path.exists(shipped):
        shutil.copy(shipped, raw)
    assert mkasm.main(["--target", gizmo_dir(variant), "--out", str(tmp_path / "asm"), "--verify",
                       "--raw", str(raw)]) == 0
    shipped_raw = mkasm.load_raw_words(shipped) if os.path.exists(shipped) else set()
    assert mkasm.load_raw_words(str(raw)) == shipped_raw


@pytest.mark.parametrize("variant", ["i386", "i386clang"])
def test_gizmo_i386_functions_at_odd_addresses_run(variant):
    """x86 has no instruction-set bit in a code address: an entry at an odd address is the entry
    (GCC's build starts clamp, sum6, udiv_var and strlen at one, clang's udiv_var). The import keeps
    bit 0 (until it did not, clamp's row named the byte in front, the ret of the function before,
    and a call returned at once) and the emulator enters there, the arguments on the stack (cdecl)."""
    from dread.emu import Layout, Machine
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir(variant))
    image = target.image()
    program = Program(target, image)
    assert program.by_name["udiv_var"].entry & 1
    assert all(h % 2 == 0 for h in program.instruction_halves())    # (the halfwords' even addresses)
    scratch = target.memory.scratch_base
    m = Machine(Layout(target, image), image.payload(), isa_of=program.isa_at)
    m.write(scratch, b"gizmo\0")
    for name, args, want in (("clamp", (5, 1, 3), 3), ("clamp", (-4, 1, 3), 1), ("clamp", (2, 1, 3), 2),
                             ("sum6", (1, 2, 3, 4, 5, 6), 91), ("udiv_var", (100, 7), 14),
                             ("strlen", (scratch,), 5)):
        assert m.call(program.by_name[name].entry, *args).ret & 0xFFFFFFFF == want & 0xFFFFFFFF, (name, args)


def test_only_arm_takes_bit_0_off_a_code_address():
    """dread.arch.code_address: ARM's bit 0 marks Thumb code and is no part of the address; x86's and
    RISC-V's addresses keep it (an odd one is x86 code there, and nothing at all on RISC-V)."""
    from dread.arch import code_address, get
    assert code_address(get({"name": "arm", "isa": "t32", "profile": "m"}), 0x08000197) == 0x08000196
    assert code_address(get({"name": "x86", "isa": "i386"}), 0x08000197) == 0x08000197
    assert code_address(get({"name": "riscv"}), 0x80000003) == 0x80000003


def _compiles(variant, tmp_path):
    """Whether the build's compiler is here: the host's gcc with 32-bit libraries (-m32), or clang."""
    command = ["gcc", "-m32"] if variant == "i386" else ["clang", "--target=i686-unknown-elf"]
    if not shutil.which(command[0]):
        return False
    probe = tmp_path / "probe.c"
    probe.write_text("int f(int x) { return x + 1; }\n")
    return subprocess.run(command + ["-c", "-o", str(tmp_path / "probe.o"), str(probe)],
                          capture_output=True).returncode == 0


HEADER_S = r"""
    .text
    .globl _start
_start:
    jmp     go
    .p2align 2
    .globl hdr                  # data under a global label without a type, as a multiboot header is
hdr:
    .byte   0xe8                # (read as code: a call far out of the image)
    .long   0x40000000
    .long   0x1badb002, 3, hdr
    .globl go
go:
    call    helper
    call    first
    pushl   $isr                # (isr: known by its address alone)
    call    install
    addl    $4, %esp
1:  hlt
    jmp     1b
    .globl pad                  # data that reads as code to its end (sti, nops) and runs on into helper
pad:
    .byte   0xfb, 0x90, 0x90, 0x90
    .globl helper
helper:
    xorl    %eax, %eax
    .p2align 4                  # padding the assembler writes as a nop of several bytes
    incl    %eax
    ret
    .globl install
install:
    ret
    .globl isr
isr:
    iret
    .globl dead                 # (nothing points at it, and it reads as code that ends)
dead:
    movl    $1, %eax
    ret
    .globl first
first:
    movl    $2, %eax            # (runs on into second, which nothing else reaches)
    .globl second
second:
    ret
"""

HEADER_TOML = """[target]
name = "header-i386"
title = "Untyped labels and padding (i386)"

[arch]
name = "x86"
isa = "i386"

[image.main]
original = "original/image.bin"
base = 0x00100000
limit = 0x00110000
entry = "_start"

[memory]
regions = [
    {name = "image", base = 0x00100000, size = 0x10000, kind = "rom"},
    {name = "ram", base = 0x00200000, size = 0x10000},
    {name = "scratch", base = 0x00300000, size = 0x10000},
]
stack = {top = 0x00210000, size = 0x2000}
sentinel = 0x00f00000
scratch = {base = 0x00300000, size = 0x10000}

[compiler]
cross = ""
cflags = ["-m32", "-march=i686", "-fno-pic", "-fno-pie", "-Os", "-ffreestanding"]
"""


def test_untyped_labels_padding_and_the_listing(tmp_path, capsys):
    """Assembly marks labels as functions without a type, and x86 has no mapping symbols: dread
    import-elf makes data of a label that nothing points at and nothing runs into and that does not end
    as code does (a header: its first bytes call 0x40000000 + hdr; pad: four bytes that read as sti
    and nops and run on into helper; MSP430's libgcc shift routines run on into each other too, and are
    code), and keeps one known by its address alone (pushl $isr), one nothing points
    at whose code ends (dead) and one code runs on into (second); the program database decodes again
    from each function's entry (go follows the header); and dread asm writes the padding nop of
    several bytes raw from the start (its precheck), so that the listing rebuilds in its first pass."""
    if not _compiles("i386", tmp_path):
        pytest.skip("needs the host's gcc with -m32")
    from dread import elfimport, mkasm
    from dread.program import Program
    from dread.target import Target
    root = tmp_path / "t"
    (root / "original").mkdir(parents=True)
    (root / "dread.toml").write_text(HEADER_TOML)
    source, elf = tmp_path / "header.S", root / "original" / "image.elf"
    source.write_text(HEADER_S)
    subprocess.run(["gcc", "-m32", "-nostdlib", "-static", "-Wl,--build-id=none", "-Wl,-Ttext=0x100000",
                    "-o", str(elf), str(source)], check=True)
    assert elfimport.main([str(elf), "--target", str(root), "--payload", str(root / "original" / "image.bin")]) == 0
    out = capsys.readouterr().out
    assert "end as code, data: hdr, pad" in out, out
    target = Target.load(str(root))
    program = Program(target, target.image())
    assert sorted(fn.name for fn in program.functions) == ["_start", "dead", "first", "go", "helper", "install",
                                                         "isr", "second"]
    go = program.by_name["go"]
    assert go.insns[0] == go.entry and program.insn(go.entry).mnemonic == "call"
    assert mkasm.main(["--target", str(root), "--verify"]) == 0
    out = capsys.readouterr().out
    assert "written raw from the start" in out and "pass 2" not in out, out


SMALL_FLAGS = ["-m32", "-march=i686", "-mno-80387", "-mno-mmx", "-mno-sse", "-fno-pic", "-fno-pie", "-Os",
               "-ffreestanding", "-fno-builtin", "-ffunction-sections", "-fno-common",
               "-fno-asynchronous-unwind-tables", "-fno-stack-protector"]


def small_i386(tmp_path, source, name="small"):
    """A target of its own from one C file, built with the host's GCC (-m32) as gizmo-i386 is: the code
    at 0x08000000, the zeroed data at 0x20000000, imported (dread import-elf). None without the host's
    32-bit GCC."""
    if not _compiles("i386", tmp_path):
        return None
    from dread import elfimport
    from dread.target import Target
    root = tmp_path / name
    (root / "original").mkdir(parents=True)
    toml = HEADER_TOML.replace('name = "header-i386"', 'name = "%s"' % name) \
        .replace("0x00100000", "0x08000000").replace("0x00110000", "0x08010000") \
        .replace('{name = "ram", base = 0x00200000', '{name = "ram", base = 0x20000000') \
        .replace("top = 0x00210000", "top = 0x20010000") \
        .replace('cflags = ["-m32", "-march=i686", "-fno-pic", "-fno-pie", "-Os", "-ffreestanding"]',
                 "cflags = [%s]" % ", ".join('"%s"' % f for f in SMALL_FLAGS))
    (root / "dread.toml").write_text(toml)
    c, elf = tmp_path / ("%s.c" % name), root / "original" / "image.elf"
    c.write_text(source)
    subprocess.run(["gcc"] + SMALL_FLAGS + ["-nostdlib", "-static", "-no-pie", "-Wl,--build-id=none",
                    "-Wl,-Ttext=0x08000000", "-Wl,-Tbss=0x20000000", "-o", str(elf), str(c)], check=True)
    assert elfimport.main([str(elf), "--target", str(root), "--payload", str(root / "original" / "image.bin"),
                           "--write-startup"]) == 0
    return Target.load(str(root))


VARIADIC_C = r"""
#include <stdarg.h>
typedef unsigned int u32;
volatile u32 sink[4];

__attribute__((noinline)) u32 total(u32 n, ...)
{
    va_list ap;
    u32 s = 0, i;
    va_start(ap, n);
    for (i = 0; i < n; i++)
        s += va_arg(ap, u32) * (i + 1);
    va_end(ap);
    sink[0] = s;
    return s;
}

u32 caller(u32 a, u32 b, u32 c)
{
    return total(3, a, b, c) + total(1, a ^ b);
}

void _start(void)
{
    sink[1] = caller(1, 2, 3);
    for (;;)
        ;
}
"""


def test_a_call_of_a_variadic_function_passes_what_the_call_site_stored(tmp_path, monkeypatch, capsys):
    """total(n, ...) walks its stack arguments with a va_list (GCC 15 takes the address of n, GCC 13
    indexes into the words behind it), so the words a call of it passes are those its caller stored, not
    those total reads itself (n alone): caller's C stores and passes four words to the first call and
    two to the second, declares total variadic (which GCC passes on the stack whatever the regparm), and
    its lift check agrees. Without, each call passed eax and one word, and the frame analysis, counting
    total's own reads, took the pushes of the words behind n for nobody's and left them out; the cases
    here agreed even so, by luck of the layout (the C's frame array mirrors the original's, so total read
    on into the words the original pushed): FreeRTOS's vAssertCalled, whose frame lay otherwise, handed
    printf a file name from elsewhere, and UngatedMoveToScreenPosition printed no escape character."""
    target = small_i386(tmp_path, VARIADIC_C, "variadic")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, lift_x86, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    assert lifter.walks_stack_args(program.by_name["total"])
    assert not lifter.walks_stack_args(program.by_name["caller"])
    text = lifter.lift(program.by_name["caller"]).text
    assert "extern u32 total(u32, ...);" in text, text
    lines_ = text.split("\n")
    calls = [k for k, line in enumerate(lines_) if "= total(" in line]
    assert sorted(lines_[k].split("= total(")[1].count("volatile u32") for k in calls) == [2, 4], calls
    import re
    for k in calls:                         # every word passed is one the C stored (the frame analysis counts it)
        for slot in re.findall(r"sp0 - (\d+)", lines_[k].split("= total(")[1]):
            assert any("*(volatile u32 *)((u32)(sp0 - %s)) = " % slot in line for line in lines_[:k]), (slot, text)
    lines = []
    assert lift.check(target, image, ["caller"], out=lines.append, jobs=2) == ["caller"], lines
    # total itself: its va_list from sa0's address (GCC 15's lea), or its index into the words behind
    # sa0 (GCC 13's), and the C reads the words the caller pushed
    text = lifter.lift(program.by_name["total"]).text
    assert "&sa0" in text, text
    lines = []
    assert lift.check(target, image, ["total"], out=lines.append, jobs=2) == ["total"], lines
    monkeypatch.setattr(lift_x86.X86Lifter, "walks_stack_args", lambda self, fn: False)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    text = lifter.lift(program.by_name["caller"]).text
    calls = [line for line in text.split("\n") if "= total(" in line]
    assert all(line.split("= total(")[1].startswith("eax, ") and line.count("volatile u32") == 1
               for line in calls), calls


REGPARM_C = r"""
typedef unsigned int u32;
volatile u32 sink[4];

static __attribute__((noinline)) u32 mix(u32 a, u32 b, u32 c)
{
    sink[2] = c;
    return a * 3 + (b ^ c);
}

u32 user(u32 x, u32 y)
{
    return mix(x, y, x + y) + mix(y, 7, x);
}

void _start(void)
{
    sink[1] = user(1, 2);
    for (;;)
        ;
}
"""


def test_a_static_function_takes_its_arguments_in_registers(tmp_path):
    """GCC gives a static function whose address is not taken its first three arguments in eax, edx and
    ecx (its local regparm(3)): mix's C takes all three (the file's REGPARM line says 3), user's C,
    compiled with regparm 1 as cdecl's, declares mix with its convention and hands it eax, edx and ecx,
    and both agree with the original and pass the readable gate, whose passes keep the declaration's
    attribute. Until then mix was refused for reading edx before anything wrote it, and user for
    calling it."""
    target = small_i386(tmp_path, REGPARM_C, "regparm")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import build, lift, readable, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    mix, user = program.by_name["mix"], program.by_name["user"]
    if lifter.reg_args(mix) != ("eax", "edx", "ecx"):
        pytest.skip("this GCC did not give mix its arguments in registers")
    assert lifter.reg_args(user) == ("eax",) and lifter.regparm_of(mix) == 3
    text = lifter.lift(mix).text
    assert "u32 mix(u32 eax_in, u32 edx_in, u32 ecx_in)" in text, text
    text = lifter.lift(user).text
    assert "extern u32 mix(u32, u32, u32) __attribute__((regparm(3)));" in text, text
    assert "= mix(eax, edx, ecx);" in text, text
    lines = []
    assert sorted(lift.check(target, image, ["mix", "user"], out=lines.append, jobs=2)) == ["mix", "user"], lines
    lines = []
    assert sorted(readable.check(target, image, out=lines.append, jobs=2)) == ["mix", "user"], lines
    out = tmp_path / "readable"
    assert readable.main(["--target", str(tmp_path / "regparm"), "--out", str(out), "user"]) == 0
    assert "__attribute__((regparm(3)))" in (out / "user.c").read_text()
    assert build.REGPARM + "3 */" in "".join(open(p).read() for p in (tmp_path / "regparm" / "build").rglob("mix.c"))


YIELD_C = r"""
typedef unsigned int u32;
volatile u32 counter;

u32 tick(u32 n)
{
    counter += n;
    if (counter > 10)
        __asm__ volatile ("int $0x50" ::: "memory");
    return counter;
}

void _start(void)
{
    tick(3);
    for (;;)
        ;
}
"""


def test_int_n_is_a_system_call(tmp_path):
    """int N (FreeRTOS's yield: int 0x50) is a system call: the emulator runs it as a logged no-op (Unicorn
    reports it as it reports a fault, with its number; dread.arch.x86's software_interrupt tells it by the
    bytes in front of eip), and the lifter writes it as inline assembly that hands eax, ecx and edx to the
    handler and back. tick's lift check and readable gate pass; until then it was refused."""
    target = small_i386(tmp_path, YIELD_C, "yield")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, readable, regflow
    from dread.emu import Core, Machine, Layout
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    text = lifter.lift(program.by_name["tick"]).text
    assert '__asm__ volatile ("int $0x50" : "+a"(eax), "+c"(ecx), "+d"(edx) :: "memory", "cc");' in text, text
    m = Machine(Layout(target, image), image.payload(), isa_of=program.isa_at)
    m.write(0x20000000, (20).to_bytes(4, "little"))        # (counter: past 10, so tick yields)
    m.write(m.layout.stack_top, (1).to_bytes(4, "little"))
    result = m.call(program.by_name["tick"].entry, 1)
    assert ("svc",) in [tuple(e) for e in result.calls] and m.exception is None, result.calls
    lines = []
    assert lift.check(target, image, ["tick"], out=lines.append, jobs=2) == ["tick"], lines
    lines = []
    assert readable.check(target, image, out=lines.append, jobs=2) == ["tick"], lines


CLI_BT_C = r"""
typedef unsigned int u32;
volatile u32 counter;

void crit(void)
{
    __asm__ volatile ("cli" ::: "memory");
    counter++;
    __asm__ volatile ("sti" ::: "memory");
}

void copy(u32 *d, const u32 *s, u32 n)
{
    __asm__ volatile ("rep movsl" : "+D"(d), "+S"(s), "+c"(n) :: "memory");
}

u32 flag(u32 x)
{
    u32 r;
    __asm__ ("bt $25, %1; sbb %0, %0" : "=r"(r) : "r"(x) : "cc");
    return (r & 7u) + 1u;
}

void _start(void)
{
    crit();
    counter = flag(counter);
    copy((u32 *)&counter, (const u32 *)&counter, 1);
    for (;;)
        ;
}
"""


def test_cli_sti_and_bt_lift(tmp_path):
    """cli and sti (FreeRTOS's critical sections) are inline assembly in the C, run in the emulator as
    in the original; bt reg, imm puts the bit into cf (FreeRTOS's event groups: bt eax, 0x19), which
    sbb reads here; rep movsd (capstone's mnemonic carries the prefix) is a loop. crit's, flag's and
    copy's lift checks pass; all three were refused until then."""
    target = small_i386(tmp_path, CLI_BT_C, "clibt")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    assert '__asm__ volatile ("cli" ::: "memory");' in lifter.lift(program.by_name["crit"]).text
    assert "cf = (" in lifter.lift(program.by_name["flag"]).text
    assert "while (ecx) {" in lifter.lift(program.by_name["copy"]).text
    lines = []
    assert sorted(lift.check(target, image, ["copy", "crit", "flag"], out=lines.append, jobs=2)) == \
        ["copy", "crit", "flag"], lines


ORDER_C = r"""
typedef unsigned int u32;
volatile u32 sink[20];

/* the original's order: the load of 0x38, then the compare that reads 0x3c */
__asm__(".text\n.globl isfull\n.type isfull, @function\nisfull:\n"
        "  movl 4(%esp), %ecx\n  movl 0x38(%ecx), %eax\n  cmpl %eax, 0x3c(%ecx)\n"
        "  sete %al\n  movzbl %al, %eax\n  ret\n.size isfull, .-isfull\n");
/* one load: nothing to keep in order */
__asm__(".text\n.globl isone\n.type isone, @function\nisone:\n"
        "  movl 4(%esp), %ecx\n  movl 0x38(%ecx), %eax\n  cmpl $7, %eax\n"
        "  sete %al\n  movzbl %al, %eax\n  ret\n.size isone, .-isone\n");
u32 isfull(const u32 *q);
u32 isone(const u32 *q);

void _start(void)
{
    sink[1] = isfull((const u32 *)sink) + isone((const u32 *)sink);
    for (;;)
        ;
}
"""


def _gcc_major():
    try:
        out = subprocess.run(["gcc", "-dumpversion"], capture_output=True, text=True, check=True).stdout
        return int(out.split(".")[0])
    except (OSError, subprocess.CalledProcessError, ValueError):
        return 0


def test_two_volatile_loads_keep_their_order(tmp_path, monkeypatch):
    """isfull loads 0x38 and compares 0x3c with it; GCC 16 folds both loads of the C into one cmp and
    reload loads 0x3c first, so a null pointer faulted at 0x3c instead of 0x38 (FreeRTOS's
    xQueueIsQueueFullFromISR on the trial build). The C has a barrier in front of the compare (and none
    where one load is all: isone), and the case that faults agrees; without the barrier GCC 16's build
    differs there (GCC 11 to 13 and clang keep the order either way)."""
    target = small_i386(tmp_path, ORDER_C, "order")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    import json
    from dread import lift, lift_x86, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    text = lifter.lift(program.by_name["isfull"]).text
    barrier = text.index(lift_x86.BARRIER)
    assert barrier < text.index("cmp dword ptr [ecx + 0x3c], eax") and barrier > text.index("mov eax, dword ptr [ecx + 0x38]"), text
    assert lift_x86.BARRIER not in lifter.lift(program.by_name["isone"]).text
    extra = os.path.join(target.root, "tests", "extra_cases")
    os.makedirs(extra, exist_ok=True)
    with open(os.path.join(extra, "isfull.json"), "w") as f:
        json.dump({"cases": [{"args": [0], "mem": [], "returns": [], "calls": [], "frame": 0, "fault": True}]}, f)
    lines = []
    assert lift.check(target, image, ["isfull", "isone"], out=lines.append, jobs=2) == ["isfull", "isone"], lines
    if _gcc_major() >= 16:
        monkeypatch.setattr(lift_x86.X86Lifter, "_needs_barrier", lambda self, insn: False)
        shutil.rmtree(os.path.join(target.build, "lift-check"), ignore_errors=True)
        lines = []
        assert lift.check(target, image, ["isfull"], out=lines.append, jobs=2) == [], lines


PUSHED_C = r"""
typedef unsigned int u32;
volatile u32 sink[4];

__attribute__((noinline)) u32 take(u32 a, u32 b)
{
    sink[0] = a;
    return a + b;
}

/* a word stored near the frame's top, read again only by a push of it (a + 0x11: a word nowhere
   else, which C reading past its frame array does not find by chance) */
__asm__(".text\n.globl spill\n.type spill, @function\nspill:\n"
        "  pushl %ebp\n  movl %esp, %ebp\n  pushl %ebx\n  subl $0x14, %esp\n"
        "  movl 8(%ebp), %eax\n  addl $0x11, %eax\n  movl %eax, -8(%ebp)\n  movl 12(%ebp), %ebx\n"
        "  pushl %ebx\n  pushl -8(%ebp)\n  call take\n  addl $0x8, %esp\n"
        "  movl -4(%ebp), %ebx\n  leave\n  ret\n.size spill, .-spill\n");
u32 spill(u32 a, u32 b);

void _start(void)
{
    sink[1] = spill(3, 4);
    for (;;)
        ;
}
"""


def test_a_word_only_a_push_reads_is_kept(tmp_path):
    """spill stores its first argument into a frame word right below its saves and reads it again only
    with push dword ptr [ebp - 8], as take's argument. The frame's top that the C leaves out
    (dread.lift saves_out) took the word for a save while the push's read went unseen, and the C lost
    the store (FreeRTOS's prvAddCurrentTaskToDelayedList handed vListInsert a word it never stored):
    the store is in the C, and the lift check passes."""
    target = small_i386(tmp_path, PUSHED_C, "pushed")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    text = lifter.lift(program.by_name["spill"]).text
    assert "= (u32)(eax);  /* " in text and "mov dword ptr [ebp - 8], eax */" in text, text
    store = [line for line in text.splitlines() if "mov dword ptr [ebp - 8], eax" in line][0]
    assert store.strip().startswith("*(volatile u32 *)"), store
    lines = []
    assert lift.check(target, image, ["spill"], out=lines.append, jobs=2) == ["spill"], lines


TAIL_C = r"""
typedef unsigned int u32;
volatile u32 sink[4];

__attribute__((noinline)) u32 inner(u32 a, u32 b)
{
    sink[2] = a - b;
    return a ^ b;
}

u32 wrap(u32 a, u32 b)
{
    return inner(a, b);
}

void _start(void)
{
    sink[1] = wrap(5, 3);
    for (;;)
        ;
}
"""


def test_a_tail_call_passes_its_own_stack_arguments(tmp_path):
    """wrap is jmp inner: the C passes its own stack parameters themselves (sa0, sa1), and GCC and clang
    jump with the words where they lie. Read through a volatile, GCC stored each back into its slot
    before the jump, which the original never writes (FreeRTOS's uxTimerGetReloadMode differed in
    every case so)."""
    target = small_i386(tmp_path, TAIL_C, "tail")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    wrap = program.by_name["wrap"]
    if program.insn(wrap.insns[0]).mnemonic != "jmp":
        pytest.skip("this GCC did not make wrap a jump")
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    assert "inner(eax, sa0, sa1)" in lifter.lift(wrap).text
    lines = []
    assert lift.check(target, image, ["wrap"], out=lines.append, jobs=2) == ["wrap"], lines


HALT_C = r"""
typedef unsigned int u32;
volatile u32 sink[4];

static __attribute__((noinline, noreturn)) void halt(u32 a, u32 b)
{
    sink[0] = a;
    sink[1] = b;
    for (;;)
        ;
}

u32 guard(u32 x, u32 y)
{
    if (x > 3)
        halt(x + 1, y);
    return x + y;
}

void _start(void)
{
    sink[2] = guard(5, 6);
    for (;;)
        ;
}
"""


def test_a_stop_logs_the_registers_its_callee_takes(tmp_path):
    """halt never returns and takes its arguments in eax and edx (GCC's regparm(3) for a static
    function): a run of guard that calls it ends there, and the stop logs eax and edx, as a stub of
    halt would. It logged the stack words at those places of the mask (on x86 the stub's arguments are
    cdecl's stack words), which differ with the frame's layout: FreeRTOS's vTaskDelay disagreed so."""
    target = small_i386(tmp_path, HALT_C, "halt")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, regflow
    from dread.autocase import Generator
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    halt, guard = program.by_name["halt"], program.by_name["guard"]
    if lift.make_lifter(program, regflow.load(target, image, program)).reg_args(halt) != ("eax", "edx", "ecx"):
        pytest.skip("this GCC did not give halt its arguments in registers")
    gen = Generator(target, image)
    case = {"args": [7, 9], "mem": [], "returns": [], "calls": [], "frame": 0, "stops": [halt.entry],
            "hang": True, "limit": 20000}
    m = gen.machine()
    m.plan = []
    gen.apply(m, case)
    m.call(guard.entry, 7, 9, max_instructions=20000)
    assert list(m.calls[-1]) == ["call 0x%x, never returns" % halt.entry, 8, 9], m.calls
    lines = []
    assert lift.check(target, image, ["guard"], out=lines.append, jobs=2) == ["guard"], lines


PUSHARG_C = r"""
typedef unsigned int u32;
volatile u32 sink[8];

__attribute__((noinline)) void put(volatile u32 *dst, u32 v, u32 n)
{
    u32 i;
    for (i = 0; i < n; i++)
        dst[i] = v;
}

static __attribute__((noinline)) void fill(u32 v, volatile u32 *dst)
{
    put(dst, v, 2);
}

u32 user3(u32 v)
{
    fill(v + 1, &sink[2 + (v & 1)]);
    fill(v ^ 5, &sink[4 + (v & 1)]);
    return sink[3];
}

void _start(void)
{
    sink[1] = user3(4);
    for (;;)
        ;
}
"""


def test_a_register_pushed_as_an_argument_is_read(tmp_path):
    """fill takes its pointer in edx (regparm(3)) and only pushes it, as put's first argument: dread.regflow
    counts that push as a read of edx (a push into a word the call takes; GCC's padding pushes in front
    of the arguments stay no reads), so fill takes eax, edx and ecx in the C and user3's C passes edx.
    regflow counted no push as a read, and the C of FreeRTOS's xQueueReceive passed its buffer to
    prvCopyDataFromQueue nowhere."""
    target = small_i386(tmp_path, PUSHARG_C, "pusharg")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    fill = program.by_name["fill"]
    pushes = [program.insn(a) for a in regflow.Analysis(program).argument_pushes(fill)]
    if not any(i.op_str == "edx" for i in pushes):
        pytest.skip("this GCC did not push fill's edx as put's argument")
    flow = regflow.load(target, image, program)
    assert {"eax", "edx"} <= set(flow.read_regs("fill")), flow.read_regs("fill")
    lifter = lift.make_lifter(program, flow)
    assert lifter.reg_args(fill) == ("eax", "edx", "ecx")
    assert "fill(eax, edx, ecx)" in lifter.lift(program.by_name["user3"]).text
    lines = []
    assert sorted(lift.check(target, image, ["fill", "user3"], out=lines.append, jobs=2)) == ["fill", "user3"], lines


BLOCK_C = r"""
typedef unsigned int u32;
typedef struct { u32 w[18]; } blk;
volatile u32 sink[4];
blk keep;

void init(u32 x)
{
    blk b = {{3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8, 9, 7, 9, 3, 2, 3}};
    b.w[0] = x;
    b.w[17] = x ^ sink[1];
    keep = b;
}

void _start(void)
{
    init(7);
    for (;;)
        ;
}
"""


def test_a_string_copy_through_the_frame_keeps_it(tmp_path):
    """init fills a structure on its frame from a constant table and copies it to a global, both with
    rep movsd through esi and edi. The frame analysis did not see those accesses (es:[edi] went with
    the segment registers), took the words for untouched and left them out of the C's frame array,
    and the copy ran over the C's own saves and return address (FreeRTOS's
    vGalileoInitializeLegacyGPIO). A frame address in esi or edi now keeps the whole frame."""
    target = small_i386(tmp_path, BLOCK_C, "block")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    init = program.by_name["init"]
    if not any(program.insn(a).mnemonic.startswith("rep movs") for a in init.insns):
        pytest.skip("this GCC copied the structure without rep movs")
    lines = []
    assert lift.check(target, image, ["init"], out=lines.append, jobs=2) == ["init"], lines


PORTS_C = r"""
typedef unsigned int u32;
typedef unsigned char u8;
volatile u32 sink[4];

static inline u8 inb_(unsigned short port)
{
    u8 v;
    __asm__ volatile ("inb %w1, %0" : "=a"(v) : "Nd"(port));
    return v;
}

static inline void outb_(unsigned short port, u8 v)
{
    __asm__ volatile ("outb %0, %w1" : : "a"(v), "Nd"(port));
}

u32 kbd(u32 n)
{
    u32 got = 0, i;
    outb_(0x64, 0xad);
    for (i = 0; i < n && i < 4; i++) {
        if (inb_(0x64) & 1)
            got += inb_(0x60);
        else
            got ^= 0x5a;
    }
    sink[0] = got;
    return got;
}

void _start(void)
{
    sink[1] = kbd(3);
    for (;;)
        ;
}
"""


def test_port_io_is_logged_answered_and_lifted(tmp_path):
    """in and out: the emulator logs them in order as device accesses (port read, port write) and a
    case's ports answer in (0 elsewhere); the generator takes the ports a run read for inputs, so kbd's
    cases reach both arms of its status bit; the lifter writes dread.h's dread_in8 and dread_out8,
    which run the instructions themselves, and kbd passes its lift check. Unicorn ran in as 0 and out
    as nothing, unlogged, and the lifter refused both."""
    target = small_i386(tmp_path, PORTS_C, "ports")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    import json
    from dread import lift, regflow
    from dread.autocase import Generator
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    kbd = program.by_name["kbd"]
    gen = Generator(target, image)
    m = gen.machine()
    m.plan = []
    gen.apply(m, {"args": [2], "mem": [], "returns": [], "calls": [], "frame": 0, "ports": [[0x64, 4, 1], [0x60, 4, 7]]})
    m.call(kbd.entry, 2, max_instructions=20000)
    events = [list(e) for e in m.calls if e[0] in ("port read", "port write")]
    assert events[:3] == [["port write", 0x64, 1, 0xad], ["port read", 0x64, 1, 1], ["port read", 0x60, 1, 7]], events
    text = lift.make_lifter(program, regflow.load(target, image, program)).lift(kbd).text
    assert "dread_out8(" in text and "dread_in8(" in text, text
    lines = []
    assert lift.check(target, image, ["kbd"], out=lines.append, jobs=2) == ["kbd"], lines
    with open(os.path.join(target.build, "autocases", image.name, "kbd.json")) as f:
        cases = json.load(f)["cases"]
    status = {v & 1 for c in cases for p, _, v in c.get("ports", ()) if p == 0x64}
    assert status == {0, 1}, cases


COUNTER_C = r"""
typedef unsigned int u32;
volatile u32 sink[4];
volatile u32 offset = 5;

/* a 64-bit counter read as two device words, high first, through frame slots, and a global added */
__asm__(".text\n.globl counter\n.type counter, @function\ncounter:\n"
        "  pushl %ebp\n  movl %esp, %ebp\n  subl $8, %esp\n"
        "  movl 0xfed000f4, %eax\n  movl 0xfed000f0, %edx\n"
        "  movl %edx, -8(%ebp)\n  movl %eax, -4(%ebp)\n"
        "  movl -8(%ebp), %eax\n  movl -4(%ebp), %edx\n"
        "  addl offset, %eax\n  adcl $0, %edx\n"
        "  movl %eax, sink\n  movl %edx, sink+4\n"
        "  leave\n  ret\n.size counter, .-counter\n");
void counter(void);

void _start(void)
{
    counter();
    for (;;)
        ;
}
"""


def test_readable_c_keeps_two_device_reads_in_order(tmp_path):
    """counter reads a 64-bit device counter's high word, then its low word, through frame slots, and adds
    a global to the pair (FreeRTOS's vHPETIRQHandler1). Its lifted C keeps the frame's volatile stores
    between the reads and their use; its readable C takes them away and combines all three in one
    statement, which GCC 16 compiled reading the low word first. dread.volorder puts a barrier in front of
    that statement (x86: folds_memory_operands), and the readable gate accepts it."""
    target = small_i386(tmp_path, COUNTER_C, "counter")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, readable, volorder
    from dread.target import Target
    toml = os.path.join(target.root, "dread.toml")
    text = open(toml).read()
    assert "\nio = " not in text
    open(toml, "w").write(text.replace("\nstack = {", "\nio = [[0xfed00000, 0xfed01000]]\nstack = {", 1))
    target = Target.load(target.root)
    image = target.image()
    lines = []
    assert lift.check(target, image, ["counter"], out=lines.append, jobs=2) == ["counter"], lines
    lines = []
    assert readable.check(target, image, out=lines.append, jobs=2) == ["counter"], lines
    out = tmp_path / "readable"
    assert readable.main(["--target", str(target.root), "--out", str(out), "counter"]) == 0
    c = (out / "counter.c").read_text()
    assert volorder.BARRIER_TEXT in c or '__asm__ volatile ("" : : : "memory");' in c, c


HANDBACK_C = r"""
typedef unsigned int u32;
volatile u32 sink[4];

/* inner2 leaves ecx alone; outer2 calls it and touches no ecx itself, so it hands its caller's ecx back;
   user2 keeps a value in ecx across outer2, as code GCC compiled with -fipa-ra does */
__asm__(".text\n.globl inner2\n.type inner2, @function\ninner2:\n"
        "  movl 4(%esp), %eax\n  addl $3, %eax\n  ret\n.size inner2, .-inner2\n"
        ".globl outer2\n.type outer2, @function\nouter2:\n"
        "  pushl %ebp\n  movl %esp, %ebp\n  pushl 8(%ebp)\n  call inner2\n  addl $4, %esp\n"
        "  popl %ebp\n  ret\n.size outer2, .-outer2\n"
        ".globl user2\n.type user2, @function\nuser2:\n"
        "  pushl %ebp\n  movl %esp, %ebp\n  movl 8(%ebp), %ecx\n  pushl %ecx\n  call outer2\n"
        "  addl $4, %esp\n  addl %ecx, %eax\n  movl %eax, sink\n  popl %ebp\n  ret\n.size user2, .-user2\n");
u32 user2(u32 x);

void _start(void)
{
    sink[1] = user2(5);
    for (;;)
        ;
}
"""


def test_a_register_handed_back_through_a_call(tmp_path):
    """outer2 never writes ecx and calls inner2, which leaves it alone; user2 reads ecx after calling outer2
    (GCC's -fipa-ra keeps a value there). At outer2's call the register still holds what its own caller left,
    and the lifter refused the call ("reads ecx behind the call of inner2"). outer2 hands it back unchanged
    (dread.regflow: kept and preserved), so its file is compiled with -fcall-saved-ecx (call_saved), which
    keeps the caller's value through the C and the call: the lift check passes (FreeRTOS's
    EnableI2CController)."""
    target = small_i386(tmp_path, HANDBACK_C, "handback")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    assert "ecx" in flow.kept_regs("outer2") and "ecx" in flow.preserved_regs("outer2"), flow.kept_regs("outer2")
    lifter = lift.make_lifter(program, flow)
    assert lifter.call_saved(program.by_name["outer2"]) == ["ecx"]
    lines = []
    assert lift.check(target, image, ["outer2"], out=lines.append, jobs=2) == ["outer2"], lines


SAY_C = r"""
typedef unsigned int u32;
volatile u32 sink[4];

__attribute__((noinline)) int printf(const char *fmt, ...)
{
    sink[0] = (u32)fmt;
    return 0;
}

void say(u32 v)
{
    printf("%u %u\n", v, v + 1);
}

void _start(void)
{
    say(7);
    for (;;)
        ;
}
"""


def test_a_printf_stub_logs_what_its_format_takes(tmp_path):
    """A stub of a printf-family callee (dread.varargs family, by name) logs its named stack word, the
    format, and the words the format takes behind it (format_mask over the stub's arguments, cdecl's stack
    words on x86): say's call of printf("%u %u\\n", v, v + 1) logs the format and 7 and 8, so a rebuilt
    caller that passed other numbers would differ."""
    target = small_i386(tmp_path, SAY_C, "say")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread.autocase import Generator
    image = target.image()
    gen = Generator(target, image)
    printf, say = gen.program.by_name["printf"], gen.program.by_name["say"]
    assert gen.formats()[0] == {printf.entry: 0}
    case = {"args": [7], "mem": [], "returns": [0], "frame": 0,
            "calls": [[printf.entry, gen.reads_mask("printf"), gen.stub_words(printf.entry)]]}
    m = gen.machine()
    m.plan = []
    gen.apply(m, case)
    m.call(say.entry, 7, max_instructions=20000)
    entry = list(m.calls[0])
    assert entry[0] == "call 0x%x" % printf.entry and entry[2:] == [7, 8], m.calls
    assert bytes(m.read(entry[1], 6)) == b"%u %u\n", entry


READBACK_C = r"""
typedef unsigned int u32;
volatile u32 sink[4];

/* reader leaves its stack argument alone, writer changes it; each caller reads the word it pushed back
   after the call, as GCC 12 at -Os does */
__asm__(".text\n.globl reader\n.type reader, @function\nreader:\n"
        "  movl 4(%esp), %eax\n  addl $1, %eax\n  ret\n.size reader, .-reader\n"
        ".globl writer\n.type writer, @function\nwriter:\n"
        "  movl 4(%esp), %eax\n  movl $9, 4(%esp)\n  ret\n.size writer, .-writer\n"
        ".globl back_r\n.type back_r, @function\nback_r:\n"
        "  pushl 4(%esp)\n  call reader\n  addl (%esp), %eax\n  addl $4, %esp\n  ret\n.size back_r, .-back_r\n"
        ".globl back_w\n.type back_w, @function\nback_w:\n"
        "  pushl 4(%esp)\n  call writer\n  addl (%esp), %eax\n  addl $4, %esp\n  ret\n.size back_w, .-back_w\n");
u32 back_r(u32 x);
u32 back_w(u32 x);

void _start(void)
{
    sink[1] = back_r(5) + back_w(6);
    for (;;)
        ;
}
"""


def test_a_word_read_back_after_a_call(tmp_path):
    """back_r and back_w read the word they pushed back after the call. The C passes a copy, so the lifter
    refused both; reader writes no stack argument (writes_stack_args), and back_r's copy reads what the
    original does: its lift check passes. writer changes its argument, which back_w then reads: refused."""
    target = small_i386(tmp_path, READBACK_C, "readback")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    assert not lifter.writes_stack_args(program.by_name["reader"])
    assert lifter.writes_stack_args(program.by_name["writer"])
    lines = []
    assert lift.check(target, image, ["back_r", "back_w"], out=lines.append, jobs=2) == ["back_r"], lines
    assert any(line.startswith("refused") and "back_w" in line and "passed to writer" in line for line in lines), lines


PADDING_C = r"""
typedef unsigned int u32;
volatile u32 sink[4];

/* GCC -O2 for a core without nopl pads with lea of a register to itself: once behind a jmp, where no
   path goes, and once where the code falls into a loop's head */
__asm__(".text\n.globl padded\n.type padded, @function\npadded:\n"
        "  movl 4(%esp), %ecx\n  xorl %eax, %eax\n  testl %ecx, %ecx\n  jne 2f\n  ret\n"
        "  .byte 0x8d, 0xb4, 0x26, 0, 0, 0, 0\n"           /* lea esi, [esi + eiz*1 + 0]: no path */
        "2:\n  .byte 0x8d, 0x76, 0x00\n"                   /* lea esi, [esi + 0]: falls into the loop */
        "  .byte 0x8d, 0xbc, 0x27, 0, 0, 0, 0\n"           /* lea edi, [edi + eiz*1 + 0] */
        "3:\n  addl %ecx, %eax\n  decl %ecx\n  jne 3b\n  ret\n.size padded, .-padded\n");
u32 padded(u32 n);

void _start(void)
{
    sink[1] = padded(4);
    for (;;)
        ;
}
"""


def test_padding_reads_nothing_and_is_no_code_to_free(tmp_path):
    """padded holds an assembler's padding: lea esi, [esi] where no path goes (behind a ret) and lea esi,
    [esi] and lea edi, [edi] where the code falls into a loop's head. They are no-ops (the layer's
    is_padding): dread.regflow finds no read of esi or edi (each made the function take its caller's
    register, and its callers refused), and the lift check takes the unreached one for padding ("code
    no path from its entry reaches" refused it). The lift check passes; on FreeRTOS's IA32 demo built
    with GCC -O2 for the Pentium they kept 111 functions out."""
    target = small_i386(tmp_path, PADDING_C, "padding")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    assert not {"esi", "edi"} & set(flow.read_regs("padded")), flow.read_regs("padded")
    lines = []
    assert lift.check(target, image, ["padded"], out=lines.append, jobs=2) == ["padded"], lines


NULLREAD_C = r"""
typedef unsigned int u32;
volatile u32 sink[4];

__attribute__((noinline)) void note(u32 line)
{
    sink[0] = line;
}

/* the shape of an accessor behind an assertion as GCC -O2 makes it: where the pointer is null, the
   assertion's handler returns and the read goes to address 0, which is memory here */
__asm__(".text\n.globl getname\n.type getname, @function\ngetname:\n"
        "  pushl %ebx\n  movl 8(%esp), %ebx\n  testl %ebx, %ebx\n  je 2f\n"
        "  movl (%ebx), %eax\n  popl %ebx\n  ret\n"          /* each path its own load, as GCC -O2 makes */
        "2:\n  pushl $691\n  call note\n  addl $4, %esp\n"
        "  movl (%ebx), %eax\n  popl %ebx\n  ret\n.size getname, .-getname\n");
u32 getname(u32 *p);

void _start(void)
{
    sink[1] = getname((u32 *)sink);
    for (;;)
        ;
}
"""


def test_a_read_through_a_null_pointer_is_no_trap(tmp_path):
    """getname reads through its argument where it is null too (behind an assertion whose handler
    returns), and address 0 is memory on the target. GCC at -O2 kept such a load in the lifted C and
    put ud2 behind it, the path being undefined to it (FreeRTOS's pcTimerGetName on the IA32 demo's -O2
    build); every lifted file is compiled with -fno-delete-null-pointer-checks now (its NULL_CHECKS
    line), and the case with a null pointer agrees."""
    target = small_i386(tmp_path, NULLREAD_C, "nullread")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift
    from dread.target import Target
    toml = os.path.join(target.root, "dread.toml")
    text = open(toml).read()
    text = text.replace('regions = [\n', 'regions = [\n    {name = "low", base = 0x00000000, size = 0x1000},\n', 1)
    text = text.replace('"-Os"', '"-O2"')
    open(toml, "w").write(text)
    target = Target.load(target.root)
    image = target.image()
    lines = []
    assert lift.check(target, image, ["getname"], out=lines.append, jobs=2) == ["getname"], lines


BITSCAN_C = r"""
typedef unsigned int u32;
volatile u32 sink[4];

__attribute__((noinline)) u32 top(u32 bits)
{
    u32 r = 99;
    __asm__ ("bsr %1, %0" : "+r"(r) : "rm"(bits) : "cc");
    return r;
}

__attribute__((noinline)) u32 low(u32 bits)
{
    u32 r = 77;
    __asm__ ("bsf %1, %0" : "+r"(r) : "rm"(bits) : "cc");
    return r;
}

void _start(void)
{
    sink[1] = top(0x50) + low(0x50);
    for (;;)
        ;
}
"""


def test_bsr_and_bsf(tmp_path):
    """bsr and bsf: the highest and the lowest bit set, the destination kept where the source is 0 (99 and
    77 here: the asm's "+r"); FreeRTOS's IA32 port picks the highest ready priority with bsr, and the lifter
    refused it. top's and low's lift checks pass."""
    target = small_i386(tmp_path, BITSCAN_C, "bitscan")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    assert "__builtin_clz" in lifter.lift(program.by_name["top"]).text
    lines = []
    assert sorted(lift.check(target, image, ["top", "low"], out=lines.append, jobs=2)) == ["low", "top"], lines


CALLBACK_C = r"""
typedef unsigned int u32;
volatile u32 sink[64];

__attribute__((noinline)) u32 twice(u32 x)
{
    return 2 * x;
}

__attribute__((noinline)) u32 callit(u32 (*f)(u32), u32 x)
{
    return f(x) + 1;
}

void _start(void)
{
    sink[1] = callit(twice, 3);
    for (;;)
        ;
}
"""


def test_a_run_that_stops_outside_the_image_is_described(tmp_path):
    """callit calls through its argument; an input that aims it at 0x9 stops the run there (nothing is
    mapped). attempt describes where a run stopped (stopped_where: the function and the instruction at
    pc), and decoding at pc read outside the image and raised TargetError, which ended the function's
    case generation in firm_up (FreeRTOS's timer functions on the IA32 demo: "0x9 is outside image
    main", for the GCC and the clang builds). The run's description says "no instruction" there now."""
    target = small_i386(tmp_path, CALLBACK_C, "callback")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread.autocase import Generator
    image = target.image()
    gen = Generator(target, image)
    callit = gen.program.by_name["callit"]
    case = {"args": [9, 3], "mem": [], "returns": [], "calls": [], "frame": 0}
    covered, why = gen.attempt(callit.entry, callit.insns, case, check_image=True)
    assert why is not None and "at 0x9 in no function (no instruction)" in why, why
    assert callit.entry in covered
    twice = gen.program.by_name["twice"]
    case = {"args": [twice.entry, 5], "mem": [], "returns": [], "calls": [], "frame": 0}
    assert gen.attempt(callit.entry, callit.insns, case, check_image=True)[1] is None


REALIGN_C = r"""
typedef unsigned int u32;
volatile u32 sink[8];

__attribute__((noinline)) void fill(u32 *p)
{
    p[0] = sink[2] + 5;
    p[1] = sink[3] ^ 9;
}

u32 realigned(u32 a, u32 b);
u32 flags_kept(u32 a);
__asm__(
"    .intel_syntax noprefix\n"
"    .globl realigned\n"
"    .type realigned, @function\n"
"realigned:\n"
"    push ebp\n"
"    mov ebp, esp\n"
"    push esi\n"
"    and esp, -8\n"
"    sub esp, 16\n"
"    lea eax, [esp + 8]\n"
"    push eax\n"
"    call fill\n"
"    add esp, 4\n"
"    mov esi, dword ptr [esp + 8]\n"
"    add esi, dword ptr [ebp + 8]\n"
"    mov eax, dword ptr [esp + 12]\n"
"    sub eax, dword ptr [ebp + 12]\n"
"    mov dword ptr [esp], eax\n"
"    mov eax, esi\n"
"    xor eax, dword ptr [esp]\n"
"    lea esp, [ebp - 4]\n"
"    pop esi\n"
"    pop ebp\n"
"    ret\n"
"    .size realigned, .-realigned\n"
"    .globl flags_kept\n"
"    .type flags_kept, @function\n"
"flags_kept:\n"
"    push ebp\n"
"    mov ebp, esp\n"
"    and esp, -8\n"
"    push 0\n"
"    setz al\n"
"    movzx eax, al\n"
"    add eax, dword ptr [ebp + 8]\n"
"    mov esp, ebp\n"
"    pop ebp\n"
"    ret\n"
"    .size flags_kept, .-flags_kept\n"
"    .att_syntax prefix\n"
);

void _start(void)
{
    sink[1] = realigned(3, 4) + flags_kept(7);
    for (;;)
        ;
}
"""


def test_a_frame_aligned_at_run_time(tmp_path):
    """clang aligns a frame for 8-byte locals with and esp, -8 behind push ebp; mov ebp, esp (FreeRTOS's
    IA32 demo: 14 functions of its clang 16 build, refused as "changes the stack pointer in a way not
    modelled"). realigned addresses its locals from the aligned esp (one of them handed to fill), its
    arguments from ebp, and restores esp from ebp: lifted, its depth taken as if entered aligned, and its
    lift check passes. flags_kept reads the zero flag the and set (setz behind a push): still refused."""
    target = small_i386(tmp_path, REALIGN_C, "realign")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, regflow
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    lifter.lift(program.by_name["realigned"])
    with pytest.raises(lift.Refused, match="changes the stack pointer in a way not modelled"):
        lifter.lift(program.by_name["flags_kept"])
    lines = []
    assert lift.check(target, image, ["realigned"], out=lines.append, jobs=2) == ["realigned"], lines


TSC_C = r"""
typedef unsigned int u32;
volatile u32 sink[8];

static inline u32 tsc_low(void)
{
    u32 lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return lo;
}

__attribute__((noinline)) u32 spin(u32 ticks)
{
    u32 start = tsc_low(), n = 0;
    while (tsc_low() - start < ticks)
        n++;
    return n;
}

void _start(void)
{
    sink[1] = spin(0x50000);
    for (;;)
        ;
}
"""


def test_the_time_stamp_counter_is_the_emulators(tmp_path):
    """rdtsc: Unicorn answers with the host's clock, so spin, which counts the rounds until the counter
    moved ticks on, ran as many rounds as the emulation's speed allowed (FreeRTOS's IA32 demo: the I2C
    driver's waits, and a function reaching them that ran into the instruction limit in the runner, not
    in the generator). The emulator answers COUNTER_START + n * COUNTER_STEP now: the same rounds with a
    slow hook on every instruction and without, the reads counted, and spin's lift check passes (the
    lifted C's rdtsc answered as the original's)."""
    target = small_i386(tmp_path, TSC_C, "tsc")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from unicorn import UC_HOOK_CODE
    from dread import emu, lift
    from dread.autocase import Generator
    image = target.image()
    gen = Generator(target, image)
    spin = gen.program.by_name["spin"]
    ticks, rounds, reads = 0x50000, [], []
    for slow in (False, True):
        m = gen.machine()
        m.plan = []
        gen.apply(m, {"args": [ticks], "mem": [], "returns": [], "calls": [], "frame": 0})
        if slow:
            def on_code(uc, address, size, user):
                for _ in range(300):
                    pass
            m.hook_add(UC_HOOK_CODE, on_code)
        rounds.append(m.call(spin.entry, ticks, max_instructions=5_000_000).ret)
        reads.append(m.counter_reads)
    assert rounds == [(ticks - 1) // emu.COUNTER_STEP] * 2 and reads == [rounds[0] + 2] * 2, (rounds, reads)
    lines = []
    assert lift.check(target, image, ["spin"], out=lines.append, jobs=2) == ["spin"], lines


SHIFT64_C = r"""
typedef unsigned int u32;
typedef unsigned long long u64;
volatile u32 sink[8];

__attribute__((noinline)) u32 right(u32 lo, u32 hi, u32 n)
{
    return (u32)((((u64)hi << 32) | lo) >> (n & 31));
}

__attribute__((noinline)) u32 left(u32 lo, u32 hi, u32 n)
{
    return (u32)(((((u64)hi << 32) | lo) << (n & 31)) >> 32);
}

__attribute__((noinline)) u32 by_five(u32 lo, u32 hi)
{
    return (u32)((((u64)hi << 32) | lo) >> 5);
}

__attribute__((noinline)) void in_memory(u32 *p, u32 hi, u32 n)
{
    __asm__ ("shrdl %%cl, %1, %0" : "+m"(*p) : "r"(hi), "c"(n) : "cc");
}

__attribute__((noinline)) u32 flags_right(u32 lo, u32 hi, u32 n)
{
    u32 c = 0, o = 0;
    __asm__ ("shrdl %%cl, %3, %2; setc %b0; seto %b1" : "+&q"(c), "+&q"(o), "+r"(lo) : "r"(hi), "c"(n) : "cc");
    return (c & 1) | (o & 1) << 1 | lo << 2;
}

__attribute__((noinline)) u32 flags_left(u32 lo, u32 hi, u32 n)
{
    u32 c = 0, o = 0;
    __asm__ ("shldl %%cl, %3, %2; setc %b0; seto %b1" : "+&q"(c), "+&q"(o), "+r"(lo) : "r"(hi), "c"(n) : "cc");
    return (c & 1) | (o & 1) << 1 | lo << 2;
}

__attribute__((noinline)) u32 right_by_one(u32 lo, u32 hi)
{
    u32 c = 0, o = 0;
    __asm__ ("shrdl $1, %3, %2; setc %b0; seto %b1" : "+&q"(c), "+&q"(o), "+r"(lo) : "r"(hi) : "cc");
    return (c & 1) | (o & 1) << 1 | lo << 2;
}

__attribute__((noinline)) u32 left_by_one(u32 lo, u32 hi)
{
    u32 c = 0, o = 0;
    __asm__ ("shldl $1, %3, %2; setc %b0; seto %b1" : "+&q"(c), "+&q"(o), "+r"(lo) : "r"(hi) : "cc");
    return (c & 1) | (o & 1) << 1 | lo << 2;
}

void _start(void)
{
    u32 w = 3;
    in_memory(&w, 9, 4);
    sink[1] = right(1, 2, 3) + left(4, 5, 6) + by_five(7, 8) + w + flags_right(1, 2, 3) + flags_left(4, 5, 6)
        + right_by_one(7, 8) + left_by_one(9, 10);
    for (;;)
        ;
}
"""


def test_double_shifts(tmp_path):
    """shrd and shld: GCC shifts a 64-bit value on i386 with them (shrd eax, edx, cl), and FreeRTOS's
    IA32 demo divides time-stamp counts so (vMilliSecondDelay, refused for shrd once rdtsc lifted). By a
    count in cl and an immediate, into a register and into memory, and the carry and overflow flags read
    behind them (flags_right, flags_left: as QEMU computes them, whatever the count; and for a
    count of 1, the one Intel defines the overflow flag for: right_by_one, left_by_one): the lift checks
    pass."""
    target = small_i386(tmp_path, SHIFT64_C, "shift64")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift
    from dread.program import Program
    image = target.image()
    program = Program(target, image)
    names = ["right", "left", "by_five", "in_memory", "flags_right", "flags_left", "right_by_one", "left_by_one"]
    used = {program.insn(a).mnemonic for n in names for a in program.by_name[n].insns}
    assert {"shrd", "shld"} <= used, used
    lines = []
    assert sorted(lift.check(target, image, names, out=lines.append, jobs=2)) == sorted(names), lines


def test_the_layer_takes_values_apart():
    """The x86 layer's hooks for dread.uncovered, dread.relations and dread.statics: value_step gives an
    access at a fixed address with base 0 (absolute_accesses) and none at an unknown base, values for
    lea and add, none for a load; store_slots names a mov into the frame; load_address the slot a compare
    or a call through memory reads; address_regs the registers an address is made of, not the value
    compared beside it."""
    a = X86()

    def insn(code):
        return a.decode(bytes.fromhex(code), 0x1000, "i386")
    known = {"ebx": (0x1000, False), "ecx": (0x2000, False)}
    values, accesses, stored = a.value_step(insn("8b0584031900"), "i386", known, None)    # mov eax, [0x190384]
    assert accesses == [(0, 0x190384, "u32", False, True, False)] and not values.get("eax") and not stored
    assert a.value_step(insn("8d4308"), "i386", known, None)[0] == {"eax": (0x1008, False)}  # lea eax, [ebx + 8]
    assert a.value_step(insn("83c310"), "i386", known, None)[0] == {"ebx": (0x1010, False)}  # add ebx, 0x10
    assert a.value_step(insn("803902"), "i386", known, None)[1] == [(0x2000, 0, "u8", False, True, False)]
    assert a.value_step(insn("8945e4"), "i386", known, None)[1] == []         # mov [ebp - 0x1c], eax: ebp not known
    assert a.store_slots(insn("8945e4")) == [("eax", "ebp", -0x1c)]
    assert a.store_slots(insn("c745e405000000")) == []                        # mov dword ptr [ebp - 0x1c], 5
    assert a.load_address(insn("3955e4")) == ("ebp", -0x1c)                   # cmp [ebp - 0x1c], edx
    assert a.load_address(insn("ff55dc")) == ("ebp", -0x24)                   # call dword ptr [ebp - 0x24]
    assert a.load_address(insn("8945e4")) is None
    assert a.address_regs(insn("3955e4")) == {"ebp"}
    assert a.address_regs(insn("8b048500100000")) == {"eax"}                  # mov eax, [eax*4 + 0x1000]


def test_the_answers_reach_branches_on_memory(tmp_path):
    """The frontier answers on i386 (Generator.answers, as test_autocase's ARM test): gated_by_global
    compares a global with a constant, in memory (GCC 16: cmp dword ptr [gated_limit], 0xc0ffee11) or
    loaded into a register first (GCC 11 and 13: mov eax, [gated_limit]; cmp eax, 0xc0ffee11). The x86 layer
    had no value_step, store_slots or load_address and gave no decider for a compare with memory, so
    dread.uncovered named no source for any x86 branch, and the compare-guided stage had nothing to
    answer: FreeRTOS's IA32 demo had 30 functions without full coverage that the ARM build covers
    (xTaskIncrementTick: 52 of 126 instructions). Now the trace names the load of the global, the
    answer sets it to the compare's other operand, and the code behind the branch runs."""
    import json
    from test_autocase import GATED_C, GATE_KEY, answered
    target = small_i386(tmp_path, GATED_C + "\nvoid _start(void) { gated_by_global(1); for (;;) ; }\n", "gated")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    from dread.autocase import Generator
    from dread.elf import Elf
    image = target.image()
    written = {"args": [GATE_KEY], "mem": [], "returns": [], "calls": [], "frame": 0}
    os.makedirs(image.extra_cases, exist_ok=True)
    with open(os.path.join(image.extra_cases, "gated_by_global.json"), "w") as f:
        json.dump({"cases": [written]}, f)
    gen = Generator(target, image)
    before, after, new, words = answered(gen, "gated_by_global", written)
    assert before < words and after == words, sorted(hex(a) for a in words - after)
    limit = next(s.value for s in Elf(str(tmp_path / "gated" / "original" / "image.elf")).symbols
                 if s.name == "gated_limit")
    assert any([limit, 4, 0xC0FFEE11] in c["mem"] for c in new), new


PICK_C = r"""
typedef unsigned int u32;
volatile u32 sink[4];

static __attribute__((noinline)) u32 pick(const u32 *p, u32 n, u32 k)
{
    if (n > 5)
        return p[k & 3];
    return n * 2;
}

u32 user2(const u32 *p, u32 n)
{
    return pick(p, n, n + 1) + pick(p, 3, 0);
}

void _start(void)
{
    sink[1] = user2((const u32 *)sink, 9);
    for (;;)
        ;
}
"""


def test_a_case_gives_a_function_its_register_arguments(tmp_path):
    """pick(p, n, k), a static function GCC gives eax, edx and ecx, reads through the pointer in eax and
    branches on edx: its generated cases give it those registers (dread.autocase case_regs, from what
    dread.regflow finds it reads; the case's regs, which Generator.apply hands to the machine), vary them
    as arguments, and reach both arms, so its lift check accepts it. Cases that left them as the harness
    does gave every input one pointer and one count."""
    target = small_i386(tmp_path, PICK_C, "pick")
    if target is None:
        pytest.skip("needs the host's gcc with -m32")
    import json
    from dread import lift
    from dread.autocase import Generator
    image = target.image()
    gen = Generator(target, image)
    pick = gen.program.by_name["pick"]
    if gen.case_regs(pick.entry) != ["eax", "edx", "ecx"]:
        pytest.skip("this GCC did not give pick its arguments in registers")
    lines = []
    assert lift.check(target, image, ["pick"], out=lines.append, jobs=2) == ["pick"], lines
    with open(os.path.join(target.build, "autocases", image.name, "pick.json")) as f:
        cases = json.load(f)["cases"]
    assert cases and all(c.get("regs") == ["eax", "edx", "ecx"] for c in cases)
    assert len({c["args"][1] for c in cases}) > 1 and len({c["args"][0] for c in cases}) > 1
    # a stub of pick logs the registers it reads (stub_regs: eax, edx and ecx by reads_mask)
    assert gen.reads_mask("pick") == 0b111, gen.reads_mask("pick")


@pytest.mark.parametrize("which", ["i386", "i386clang"])
def test_gizmo_i386_rewritten_functions_agree(which, tmp_path):
    """The rewritten C over the original, every argument on the stack: functions that do not fit move
    behind a 5-byte jmp at their entry, and every function agrees with the written and generated cases,
    which run every instruction. Stubs take their arguments from the stack (behind the return address the
    call pushed), and dread.regflow takes a byte loaded into part of a register for no read of the rest
    (until it did, apply_config looked as if it read ecx behind every strcmp)."""
    if not _compiles(which, tmp_path):
        pytest.skip("needs the compiler of gizmo-" + which)
    from dread import build, runner
    from dread.target import Target
    from test_runner import src_copy
    target = Target.load(gizmo_dir(which))
    payload = build.build(target, target.image(), src=str(src_copy(tmp_path, which)), out=str(tmp_path / "recon"),
                          quiet=True)
    with open(tmp_path / "recon" / "report.tsv") as f:
        assert "branch at original entry" in f.read()
    suite = runner.prepare(target, target.image(), payload)
    lines = []
    failed, uncovered, _ = runner.run(list(suite.cases), out=lines.append, jobs=4)
    text = "\n".join(lines)
    assert failed == 0 and not uncovered, text
    assert "ok   fmt_hex" in text and "ok   dispatch" in text and "ok   strcmp" in text


def lift_gizmo(variant):
    from dread import lift, regflow
    from dread.program import Program
    from dread.target import Target
    target = Target.load(gizmo_dir(variant))
    image = target.image()
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    lifted, refused = {}, {}
    for fn in program.functions:
        try:
            lifted[fn.name] = lifter.lift(fn)
        except lift.Refused as e:
            refused[fn.name] = str(e)
    return lifted, refused


def test_gizmo_i386_lifts():
    """dread.lift_x86 on both i386 builds: every function but the startup and GCC's main (they set the
    stack pointer and align it). A jump through a table is a switch (script_run; clang's dispatch, its
    handlers inlined), GCC's dispatch a tail call through the pointer it loads, which the compiler must make a
    jump (musttail: its handlers take their arguments from dispatch's caller's words). Every function's C takes eax as it
    comes (compiled with -mregparm=1) and its arguments through their addresses; a void function that does not write eax
    hands it back (uart_init: one store), GCC's pop eax that takes an argument off again is no read of it
    (print_status), and uart_puts reads edx behind uart_putc, whose code leaves it (GCC's interprocedural
    register allocation): uart_putc's C keeps edx (-fcall-saved-edx), and the call does not clear it."""
    from dread import lift, lift_x86, regflow
    from dread.program import Program
    from dread.target import Target
    lifted, refused = lift_gizmo("i386")
    musttail = Target.load(gizmo_dir("i386")).compiler.accepts(lift_x86.MUSTTAIL)    # (GCC 15 and later)
    assert set(refused) == {"_start", "main"} | (set() if musttail else {"dispatch"}), refused
    assert "switch (index) {" in lifted["script_run"].text
    if musttail:
        assert "__attribute__((musttail)) return ((u32 (*)(u32))(s0))(eax);" in lifted["dispatch"].text
    else:
        assert "has no musttail" in refused["dispatch"], refused
    target = Target.load(gizmo_dir("i386"))
    program = Program(target)
    lifter = lift.make_lifter(program, regflow.load(target, target.image(), program))
    assert lifter.call_saved(program.by_name["uart_putc"]) == ["edx"]
    call = [line for line in lifted["uart_puts"].text.splitlines() if "uart_putc(eax" in line][0]
    assert "ecx = 0; cf = zf = sf = of = 0;" in call and "edx = 0" not in call
    text = lifted["clamp"].text
    assert "u32 clamp(u32 eax_in, u32 sa0, u32 sa1, u32 sa2)" in text
    assert "ecx = *(volatile u32 *)((u32)&sa0);  /* 800019a: mov ecx, dword ptr [ebp + 8] */" in text
    assert "{ s0 = ecx; if ((zf | (sf != of))) edx = s0; }  /* 80001a6: cmovle edx, ecx */" in text
    init = lifted["uart_init"].text
    assert "u32 eax = eax_in," in init and "return eax;  /* 8000504: ret  */" in init
    assert "print_status" in lifted
    lifted, refused = lift_gizmo("i386clang")
    assert set(refused) == {"_start"}, refused
    assert "switch (index) {" in lifted["dispatch"].text and "switch (index) {" in lifted["script_run"].text


def i386_copy(tmp_path):
    """gizmo in tmp_path (its builds share sources) with gizmo-i386's cached generated cases and register
    flow, and nothing written by hand in it: every function lifts."""
    from dread.target import Target
    from conftest import GIZMO
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    for cache in ("autocases", "regflow"):
        here = os.path.join(gizmo_dir("i386"), "build", cache)
        if os.path.isdir(here):
            shutil.copytree(here, copy / "i386" / "build" / cache)
    for name in os.listdir(copy / "i386" / "src"):         # (rewritten.c, uart_putc.c: nothing by hand)
        if name.endswith(".c"):
            os.remove(copy / "i386" / "src" / name)
    return Target.load(str(copy / "i386"))


# (not mul64: its written cases compare its result's upper half, which no caller in the image reads, so
# the register flow cannot tell: docs/todo.md. uart_putc and uart_puts: the edx that GCC keeps across
# the call; dispatch: a tail call through a pointer, made a jump; script_run: its arguments, stack words,
# varied as arguments since generator VERSION 36, reach every arm of its switch)
I386_NAMES = ["clamp", "checksum", "parse_uint", "fmt_hex", "sum6", "udiv_var", "uart_init", "print_status",
              "strcmp", "parse_kv", "uart_putc", "uart_puts", "dispatch", "script_run"]


def test_gizmo_i386_lift_check_accepts_what_agrees_and_drops_a_mistake(tmp_path, monkeypatch):
    """The lift check on GCC's i386 build: the lifted C of these functions agrees with the original on
    generated cases that run every instruction; with je taken on zf clear (a planted mistake) the
    functions that test equality are dropped."""
    if not _compiles("i386", tmp_path):
        pytest.skip("needs the host's gcc with -m32")
    from dread import build, lift, lift_x86
    target = i386_copy(tmp_path)
    image = target.image()
    names = [n for n in I386_NAMES if n != "dispatch" or target.compiler.accepts(lift_x86.MUSTTAIL)]
    lines = []
    accepted = lift.check(target, image, names, out=lines.append, jobs=4)
    assert sorted(accepted) == sorted(names), lines
    assert build.read_names(image.table("lifted")) == sorted(names)
    monkeypatch.setitem(lift_x86.CONDITIONS, "e", "!zf")
    lines = []
    accepted = lift.check(target, image, names, out=lines.append, jobs=4)
    dropped = [line.split()[2] for line in lines if line.startswith("dropped  FAIL")]
    assert dropped and set(accepted) | set(dropped) == set(names), lines


def test_a_stubbed_callee_logs_its_stack_arguments(tmp_path):
    """GCC's handle_key calls read_line(line, 40), whose run waits for a key no case gives, so its
    generated cases stub read_line, and the stub logs the two words cdecl passes on the stack (dread.autocase
    stub_words, since VERSION 35; before, a stub of a callee the generator made logged no argument on i386):
    the buffer, a frame address ("stack"), and 40, which the cases compare. The test reads the stub's log
    on the original: whether handle_key's lift check accepts it depends on how the compiler lays out the
    C's frame, as apply_config behind read_line reads a saved ebx (with CI's GCC 11 the case that stubs
    read_line was refused, and handle_key dropped for the code that case alone runs)."""
    if not _compiles("i386", tmp_path):
        pytest.skip("needs the host's gcc with -m32")
    import json
    from dread import lift
    from dread.autocase import Generator
    target = i386_copy(tmp_path)
    image = target.image()
    gen = Generator(target, image)
    read_line, handle_key = gen.program.by_name["read_line"], gen.program.by_name["handle_key"]
    assert gen.stub_words(read_line.entry) == 2
    lift.check(target, image, ["handle_key"], out=lambda *a: None, jobs=2)     # (makes its generated cases)
    with open(os.path.join(target.build, "autocases", image.name, "handle_key.json")) as f:
        cases = json.load(f)["cases"]
    stubbed = [c for c in cases if any(call[0] == read_line.entry for call in c.get("calls", ()))]
    assert stubbed and all(call[2] == 2 for c in stubbed for call in c["calls"] if call[0] == read_line.entry)
    m = gen.machine()
    m.plan = []                             # (as Generator.attempt runs a case)
    gen.apply(m, stubbed[0])
    result = m.call(handle_key.entry, *stubbed[0]["args"], max_instructions=stubbed[0].get("limit", 200_000))
    logged = [tuple(e[1:]) for e in result.calls if e[0] == "call 0x%x" % read_line.entry]
    assert logged and logged[0] == ("stack", 40), result.calls


def test_random_x86_functions_agree(tmp_path):
    """dread.lift_x86 on random 32-bit code (dread.liftfuzz): the arithmetic and logic of every width with
    the flags, the shifts by a constant, by cl and by one, the multiplications and divisions, setcc,
    cmovcc, lea, xchg, frame slots through esp and ebp, push and pop, forward branches, cdecl calls."""
    if not _compiles("i386", tmp_path):
        pytest.skip("needs the host's gcc with -m32")
    from dread import liftfuzz
    lines = []
    failures, refused, _ = liftfuzz.run("x86", functions=16, length=40, inputs=12, seed=7,
                                        keep=str(tmp_path / "x86"), out=lines.append, reduce=0)
    assert not failures and not refused, "\n".join(lines)


@pytest.mark.slow
def test_a_planted_x86_mistake_shows_and_is_reduced(tmp_path, monkeypatch):
    """sf taken as the sign of the operand instead of the result (a planted mistake in the flags of a
    subtraction): the fuzzer finds it and reduces it to a few units."""
    if not _compiles("i386", tmp_path):
        pytest.skip("needs the host's gcc with -m32")
    from dread import liftfuzz, lift_x86
    original = lift_x86.X86Lifter._zs
    monkeypatch.setattr(lift_x86.X86Lifter, "_zs", staticmethod(
        lambda res, size: original("e0", size).replace("zf = e0 == 0;", "zf = %s == 0;" % res)))
    lines = []
    failures, refused, _ = liftfuzz.run("x86", functions=16, length=40, inputs=12, seed=7,
                                        keep=str(tmp_path / "x86"), out=lines.append, reduce=1)
    assert failures and not refused, "\n".join(lines)
    reduced = [line for line in lines if line.startswith("reduced")]
    assert len(reduced) == 1 and len(reduced[0].split(" to: ")[1].split(" | ")) <= 6, reduced


def test_the_x86_c_takes_eax_in_eax(tmp_path):
    """dread.build writes a REGPARM line ahead of the x86 lifter's C (Lifter.regparm), which compile_all
    turns into -mregparm=1 for GCC and clang: eax_in in eax, the stack arguments where cdecl has them. In
    the C's text it would be an attribute, which dread.readable's new heads would not keep."""
    from dread import build
    from dread.target import Target
    target = Target.load(gizmo_dir("i386"))
    source = tmp_path / "f.c"
    source.write_text("%s1 */\nu32 f(u32 eax_in, u32 sa0) { return eax_in + sa0; }\n" % build.REGPARM)
    assert "-mregparm=1" in build.file_flags(target, str(source))
    out = tmp_path / "lifted"
    from dread import lift, regflow
    from dread.program import Program
    program = Program(target)
    lifter = lift.make_lifter(program, regflow.load(target, target.image(), program))
    build.lift_sources(target, target.image(), ["clamp"], str(out), lifter=lifter, quiet=True)
    assert open(out / "clamp.c").readline().startswith(build.REGPARM + "1 */")


def test_gizmo_i386_readable_c_passes_its_gate(tmp_path):
    """dread readable on gizmo-i386: the types pass writes read_line's head anew where the cases take it
    (its eax a signed value: uart_getc returns -1 for nothing; GCC 13 kept u32), eax still first. The C takes eax in eax because dread.build compiles
    it with -mregparm=1 (its REGPARM line); an attribute in the head's text was lost there, and every
    argument was read a word off. The readable C of these functions passes the cases their lifted C passed."""
    if not _compiles("i386", tmp_path):
        pytest.skip("needs the host's gcc with -m32")
    from dread import lift, readable
    target = i386_copy(tmp_path)
    image = target.image()
    names = ["read_line", "clamp", "uart_puts", "checksum"]
    lines = []
    assert sorted(lift.check(target, image, names, out=lines.append, jobs=4)) == sorted(names), lines
    lines = []
    assert sorted(readable.check(target, image, out=lines.append, jobs=4)) == sorted(names), lines
    out = tmp_path / "readable"
    assert readable.main(["--target", str(tmp_path / "gizmo" / "i386"), "--out", str(out), "read_line"]) == 0
    import re
    head = re.search(r"^\w+ read_line\((\w+) (\w+), u32 sa0, u32 sa1\)$", (out / "read_line.c").read_text(), re.M)
    # (the types pass gives eax another type where the cases take it: s32 with GCC 15, u32 kept with 13)
    assert head and head.group(1) in ("s32", "u32") and head.group(2).startswith("eax"), head
