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
    handlers inlined), GCC's dispatch a tail call through the pointer it loads. Every function's C takes eax as it
    comes (compiled with -mregparm=1) and its arguments through their addresses; a void function that does not write eax
    hands it back (uart_init: one store), GCC's pop eax that takes an argument off again is no read of it
    (print_status), and uart_puts reads edx behind uart_putc, whose code leaves it (GCC's interprocedural
    register allocation): uart_putc's C keeps edx (-fcall-saved-edx), and the call does not clear it."""
    from dread import lift, regflow
    from dread.program import Program
    from dread.target import Target
    lifted, refused = lift_gizmo("i386")
    assert set(refused) == {"_start", "main"}, refused
    assert "switch (index) {" in lifted["script_run"].text
    assert "return ((u32 (*)(u32, u32, u32))(s0))(eax," in lifted["dispatch"].text
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
    os.remove(copy / "i386" / "src" / "rewritten.c")
    return Target.load(str(copy / "i386"))


# (not mul64: its written cases compare its result's upper half, which no caller in the image reads, so
# the register flow cannot tell; nor script_run, whose generated cases miss one arm of its switch, nor
# dispatch, whose tail-called handlers write the stack arguments they came with, which the C passes as
# copies: docs/todo.md. uart_putc and uart_puts: the edx that GCC keeps across the call)
I386_NAMES = ["clamp", "checksum", "parse_uint", "fmt_hex", "sum6", "udiv_var", "uart_init", "print_status",
              "strcmp", "parse_kv", "uart_putc", "uart_puts"]


def test_gizmo_i386_lift_check_accepts_what_agrees_and_drops_a_mistake(tmp_path, monkeypatch):
    """The lift check on GCC's i386 build: the lifted C of these functions agrees with the original on
    generated cases that run every instruction; with je taken on zf clear (a planted mistake) the
    functions that test equality are dropped."""
    if not _compiles("i386", tmp_path):
        pytest.skip("needs the host's gcc with -m32")
    from dread import build, lift, lift_x86
    target = i386_copy(tmp_path)
    image = target.image()
    lines = []
    accepted = lift.check(target, image, I386_NAMES, out=lines.append, jobs=4)
    assert sorted(accepted) == sorted(I386_NAMES), lines
    assert build.read_names(image.table("lifted")) == sorted(I386_NAMES)
    monkeypatch.setitem(lift_x86.CONDITIONS, "e", "!zf")
    lines = []
    accepted = lift.check(target, image, I386_NAMES, out=lines.append, jobs=4)
    dropped = [line.split()[2] for line in lines if line.startswith("dropped  FAIL")]
    assert dropped and set(accepted) | set(dropped) == set(I386_NAMES), lines


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
    """dread readable on gizmo-i386: the types pass writes read_line's head anew (its eax a signed value:
    uart_getc returns -1 for nothing), eax still first. The C takes eax in eax because dread.build compiles
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
    assert head and head.group(1) == "s32" and head.group(2).startswith("eax"), head
