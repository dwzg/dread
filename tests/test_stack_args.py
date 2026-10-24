"""dread lift: stack arguments through a bound call, and a callee whose stack pointer is not static.

Two refusals the lifter used to make, each shown refused before (the old gate restored by a
monkeypatch) and accepted after through the lift check, with a mutation the cases catch:

1. A call of a function that reads a register its caller leaves (r4-r11), or whose result the caller
   reads behind the call, and that also takes stack arguments, was refused: a bound call passed no
   stack arguments. Now the bound call reserves the stack-argument words below the stack pointer,
   stores them there before the call and restores the stack pointer after (ARM state, Thumb-2; not
   Thumb-1; RISC-V too).
2. A call of a function whose stack pointer is not static (a context switch, an auipc sp, a
   variable-length array's sub sp, sp, rN) was refused for every caller: its stack arguments were not
   known. Now scan_stack_args infers them from the words it reads above its entry (before the stack
   pointer moves, and through a frame pointer set from it after), the caller must provide them, and
   the caller is accepted while the callee stays refused. A callee whose path reaches an instruction
   that does not decode stays not known (the code behind it is not seen), and so does its caller.

Programs of our own, in assembly and in C compiled in the test; neutral, specific names (the push's
leak check refuses the generic names a private target uses)."""
import re
import shutil
import subprocess

import pytest

from dread import elfimport, lift, regflow
from dread.program import Program
from dread.target import Target
from conftest import NOIPA_DEF, RISCV_CROSS, RISCV_MARCH, needs_riscv_gcc, riscv_toml
from test_lift_results import A_TOML, M_TOML, M_GCC, M_CLANG, RV_TOML, RV_GCC, RV_CLANG, arm_program

needs_arm = pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                               reason="needs arm-none-eabi-as and -gcc")
needs_arm_gcc = pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
needs_clang = pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")), reason="needs clang and ld.lld")


# ---- refusal 1: a bound call that passes stack arguments ----

# tally_five_args reads r5 its caller leaves (a shared helper's input) and takes a fifth argument on
# the stack: a call of it is bound (r5 handed in) and must pass the stack argument. tally_user sets r5
# and passes the fifth argument.
TALLY_A32_S = """
    .syntax unified
    .arch armv4t
    .arm
    .text
    .global tally_five_args, tally_user
    .type tally_five_args, %function
tally_five_args:
    push    {r4, lr}
    ldr     r4, [sp, #8]        @ the fifth argument (above the entry's stack pointer)
    add     r0, r0, r1
    add     r0, r0, r2
    add     r0, r0, r3
    add     r0, r0, r4
    add     r0, r0, r5          @ what the caller left in r5
    pop     {r4, lr}
    bx      lr
    .size tally_five_args, . - tally_five_args
    .type tally_user, %function
tally_user:
    push    {r4, r5, lr}
    mov     r5, r1              @ the value tally_five_args reads from r5
    add     r4, r0, #9          @ the fifth argument, from the first (a case varies it)
    sub     sp, sp, #8
    str     r4, [sp]            @ the fifth argument
    bl      tally_five_args
    add     sp, sp, #8
    add     r0, r0, #1
    pop     {r4, r5, lr}
    bx      lr
    .size tally_user, . - tally_user
"""

TALLY_T2_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global tally_five_args, tally_user
    .type tally_five_args, %function
    .thumb_func
tally_five_args:
    push    {r4, lr}
    ldr     r4, [sp, #8]        @ the fifth argument
    add     r0, r0, r1
    add     r0, r0, r2
    add     r0, r0, r3
    add     r0, r0, r4
    add     r0, r0, r5          @ what the caller left in r5
    pop     {r4, pc}
    .size tally_five_args, . - tally_five_args
    .type tally_user, %function
    .thumb_func
tally_user:
    push    {r4, r5, lr}
    mov     r5, r1
    adds    r4, r0, #9          @ the fifth argument, from the first (a case varies it)
    sub     sp, #8
    str     r4, [sp]            @ the fifth argument
    bl      tally_five_args
    add     sp, #8
    adds    r0, r0, #1
    pop     {r4, r5, pc}
    .size tally_user, . - tally_user
"""


@needs_arm
@pytest.mark.parametrize("kind", ["a32", "t2", pytest.param("t2-clang", marks=needs_clang)])
def test_a_bound_call_passes_stack_arguments(tmp_path, kind, monkeypatch):
    """tally_five_args reads r5 its caller leaves and takes a fifth argument on the stack: tally_user's
    call of it is bound (r5 handed in, from tally_user's variable) and reserves the stack argument
    below the stack pointer, stores it there before the bl and restores the stack pointer after. Before,
    a bound call passed no stack arguments and tally_user was refused; after, it is accepted, every case
    agrees and runs every instruction."""
    source = {"a32": TALLY_A32_S, "t2": TALLY_T2_S, "t2-clang": TALLY_T2_S}[kind]
    t = arm_program(tmp_path, kind, source=source, entry="tally_user")
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))

    # the callee reads r5 its caller leaves and takes one stack argument
    assert lifter.leaves(p.by_name["tally_five_args"]) == ["r5"]
    assert lifter.stack_args(p.by_name["tally_five_args"]) == 1

    # before: a bound call that would pass stack arguments was refused
    orig = lift.Lifter._bound_call

    def old_limit(self, callee, behind, inputs=(), nstack=0, insn=None):
        if nstack:
            raise lift.Refused("0x%x calls %s, which reads a register its caller leaves and takes stack arguments"
                               % (insn.address if insn is not None else 0, callee.name))
        return orig(self, callee, behind, inputs, nstack, insn)
    monkeypatch.setattr(lift.Lifter, "_bound_call", old_limit)
    with pytest.raises(lift.Refused, match="takes stack arguments"):
        lift.make_lifter(p, regflow.analyse(p)).lift(p.by_name["tally_user"])
    monkeypatch.undo()

    # after: the bound call reserves, stores and restores the stack argument
    text = lift.make_lifter(p, regflow.analyse(p)).lift(p.by_name["tally_user"]).text
    assert 'x5 __asm__("r5") = r5' in text, text                     # r5 handed in
    assert "sub sp, sp, #8\\n\\tstr %[sa0], [sp, #0]\\n\\tbl tally_five_args\\n\\tadd sp, sp, #8" in text, text
    lines = []
    accepted = lift.check(t, t.image(), ["tally_five_args", "tally_user"], out=lines.append, jobs=2)
    assert "tally_user" in accepted, "\n".join(lines)


@needs_arm
def test_a_stack_argument_at_the_wrong_offset_is_caught(tmp_path, monkeypatch):
    """A bound call that stores its stack argument at the wrong offset (the callee then reads another
    word as its fifth argument) is caught by the cases: tally_user is dropped."""
    t = arm_program(tmp_path, "t2", source=TALLY_T2_S, entry="tally_user")
    orig = lift.Lifter._bound_call

    def wrong_offset(self, callee, behind, inputs=(), nstack=0, insn=None):
        s = orig(self, callee, behind, inputs, nstack, insn)
        return s.replace("str %[sa0], [sp, #0]", "str %[sa0], [sp, #4]") if nstack else s
    monkeypatch.setattr(lift.Lifter, "_bound_call", wrong_offset)
    lines = []
    accepted = lift.check(t, t.image(), ["tally_five_args", "tally_user"], out=lines.append, jobs=2)
    assert "tally_user" not in accepted, "\n".join(lines)


# ---- refusal 2: a callee whose stack pointer is not static ----

# relocate_stack sets its stack pointer from an auipc (a fresh base), then restores it and returns: its
# stack pointer is not static, so its stack arguments were not known, and prep_scheduler, which calls
# it, was refused. It reads nothing above its entry (0 stack arguments).
RELOCATE_RV_S = """
    .option norelax
    .text
    .global relocate_stack, prep_scheduler
    .type relocate_stack, @function
relocate_stack:
    mv      t0, sp
    auipc   sp, 0               # the stack pointer is no longer static
    mv      sp, t0              # (restored at once: the run returns cleanly)
    addi    a0, a0, 1
    ret
    .size relocate_stack, . - relocate_stack
    .type prep_scheduler, @function
prep_scheduler:
    addi    sp, sp, -16
    sw      ra, 12(sp)
    call    relocate_stack
    addi    a0, a0, 5
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size prep_scheduler, . - prep_scheduler
"""


@needs_riscv_gcc
@pytest.mark.parametrize("compiler", ["gcc", pytest.param("clang", marks=needs_clang)])
def test_a_callee_with_a_non_static_stack_pointer_riscv(tmp_path, compiler, monkeypatch):
    """relocate_stack's stack pointer is not static (auipc sp): its stack arguments were not known
    (stack_args None), and prep_scheduler, which calls it, was refused. scan_stack_args finds it reads
    nothing above its entry (0 stack arguments); prep_scheduler provides none, so it is accepted, while
    relocate_stack itself stays refused."""
    (tmp_path / "t.s").write_text(RELOCATE_RV_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run([RISCV_CROSS + "gcc", "-march=" + RISCV_MARCH, "-mabi=ilp32", "-c", "-o", obj, str(tmp_path / "t.s")],
                   check=True)
    subprocess.run([RISCV_CROSS + "ld", "-m", "elf32lriscv", "-Ttext=0x20000000", "-e", "prep_scheduler", "-o", elf, obj],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml(RV_TOML + (RV_GCC if compiler == "gcc" else RV_CLANG)))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))

    assert lifter.stack_args(p.by_name["relocate_stack"]) is None     # its stack pointer is not static
    assert lifter.scan_stack_args(p.by_name["relocate_stack"]) == 0   # it reads nothing above its entry

    # before: a callee whose stack arguments are not known was refused for every caller
    def no_infer(self, callee, insn):
        raise lift.Refused("0x%x calls %s, whose stack arguments are not known (its stack pointer is not static)"
                           % (insn.address, callee.name))
    monkeypatch.setattr(lift.Lifter, "inferred_stack_args", no_infer)
    with pytest.raises(lift.Refused, match="stack arguments are not known"):
        lift.make_lifter(p, regflow.analyse(p)).lift(p.by_name["prep_scheduler"])
    monkeypatch.undo()

    # after: prep_scheduler is accepted, relocate_stack stays refused
    with pytest.raises(lift.Refused):
        lifter.lift(p.by_name["relocate_stack"])
    lines = []
    accepted = lift.check(t, t.image(), ["relocate_stack", "prep_scheduler"], out=lines.append, jobs=2)
    assert accepted == ["prep_scheduler"], "\n".join(lines)


# opaque_marker reaches a reserved compressed encoding inside its code (inline data, as a register-test
# task keeps its markers): states() refuses at it, so its stack arguments were not known, and they still
# are, since the code behind it is not seen. Its caller stays refused, with that reason. The encoding is
# patched into the original after the import: assembled as data (.2byte) it would get a data mapping
# symbol from newer binutils and lie outside the code ranges, so the path would end there instead (and
# older binutils would differ); a c.nop placeholder at a fixed offset keeps it inside, as on the bench.
OPAQUE_PATCH = 2                        # bytes into opaque_marker: behind the c.addi
OPAQUE_RV_S = """
    .option norelax
    .text
    .global opaque_marker, marker_user
    .type opaque_marker, @function
opaque_marker:
    c.addi  a0, 1
    c.nop                               # patched to 0x8008, a reserved encoding that does not decode
    c.jr    ra
    .size opaque_marker, . - opaque_marker
    .type marker_user, @function
marker_user:
    addi    sp, sp, -16
    sw      ra, 12(sp)
    call    opaque_marker
    lw      ra, 12(sp)
    addi    sp, sp, 16
    ret
    .size marker_user, . - marker_user
"""


@needs_riscv_gcc
def test_a_callee_whose_code_does_not_decode_stays_not_known(tmp_path):
    """A callee whose path reaches an instruction that does not decode: the scan does not see the code
    behind it, so its stack arguments are not known (scan_stack_args None) and its caller stays
    refused, now with that reason rather than a stack pointer that is not static."""
    (tmp_path / "t.s").write_text(OPAQUE_RV_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run([RISCV_CROSS + "gcc", "-march=" + RISCV_MARCH, "-mabi=ilp32", "-c", "-o", obj, str(tmp_path / "t.s")],
                   check=True)
    subprocess.run([RISCV_CROSS + "ld", "-m", "elf32lriscv", "-Ttext=0x20000000", "-e", "marker_user", "-o", elf, obj],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml(RV_TOML + RV_GCC))
    payload = tmp_path / "original" / "t.bin"
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(payload), elf]) == 0
    t = Target.load(str(tmp_path))
    at = Program(t, t.image()).by_name["opaque_marker"].entry + OPAQUE_PATCH - t.image().base
    data = bytearray(payload.read_bytes())
    assert data[at:at + 2] == b"\x01\x00"                      # the c.nop placeholder
    data[at:at + 2] = (0x8008).to_bytes(2, "little")
    payload.write_bytes(bytes(data))
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    assert p.insn(p.by_name["opaque_marker"].entry + OPAQUE_PATCH) is None     # on its path, inside its code
    lifter = lift.make_lifter(p, regflow.analyse(p))
    assert lifter.stack_args(p.by_name["opaque_marker"]) is None
    assert lifter.scan_stack_args(p.by_name["opaque_marker"]) is None
    with pytest.raises(lift.Refused, match=r"calls opaque_marker, whose stack arguments are not known \(its code at "
                                           r"0x[0-9a-f]+ does not decode\)"):
        lifter.lift(p.by_name["marker_user"])


# pair_strd_caller stores both of its callee's stack arguments with one strd (GCC 7 at -O2 does so);
# vla_pair_reader moves its stack pointer by a variable amount and only then reads them through r7.
PAIR_T2_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global vla_pair_reader, pair_strd_caller
    .type vla_pair_reader, %function
    .thumb_func
vla_pair_reader:
    push    {r4, r7, lr}
    add     r7, sp, #0          @ r7: twelve bytes below the entry's stack pointer
    and     r4, r0, #7
    lsls    r4, r4, #3
    sub     sp, sp, r4          @ the stack pointer is not static from here
    ldr     r2, [r7, #12]       @ the fifth argument
    ldr     r3, [r7, #16]       @ the sixth argument
    lsls    r2, r2, #1
    eors    r3, r3, r1
    adds    r0, r2, r3
    mov     sp, r7
    pop     {r4, r7, pc}
    .size vla_pair_reader, . - vla_pair_reader
    .type pair_strd_caller, %function
    .thumb_func
pair_strd_caller:
    push    {r4, r5, lr}
    sub     sp, #12
    adds    r4, r0, #3
    subs    r5, r1, #5
    strd    r4, r5, [sp]        @ both stack arguments in one store
    bl      vla_pair_reader
    add     sp, #12
    pop     {r4, r5, pc}
    .size pair_strd_caller, . - pair_strd_caller
"""


@needs_arm
def test_a_caller_that_stores_its_stack_arguments_with_strd(tmp_path):
    """The words a caller provides are those its stores cover, an strd's two among them: vla_pair_reader
    reads two stack arguments through r7 after its stack pointer stops being static, pair_strd_caller
    stores both with one strd, and is accepted through the lift check (a version that counted single
    stores only refused it as not storing them)."""
    t = arm_program(tmp_path, "t2", source=PAIR_T2_S, entry="pair_strd_caller")
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))
    assert lifter.stack_args(p.by_name["vla_pair_reader"]) is None
    assert lifter.scan_stack_args(p.by_name["vla_pair_reader"]) == 2
    text = lifter.lift(p.by_name["pair_strd_caller"]).text
    assert "vla_pair_reader(r0, r1, r2, r3, *(volatile u32 *)(sp0 - 24), *(volatile u32 *)(sp0 - 20))" in text, text
    lines = []
    accepted = lift.check(t, t.image(), ["vla_pair_reader", "pair_strd_caller"], out=lines.append, jobs=2)
    assert accepted == ["pair_strd_caller"], "\n".join(lines)


# A variable-length array with six arguments (GCC -Os and -O2): its stack pointer moves by a variable
# amount, so its two stack arguments (e and f) are read through a frame pointer. The caller stores them
# and is accepted; the callee itself stays refused. The callee returns 2e - f and the caller passes
# f = e + 1, so that e and f swapped change the result by 3 (e - f) for every input: a mutation that
# swaps them is caught by any case (with e + f, or with inputs that make e equal f, it was not).
VLA6_C = NOIPA_DEF + """
typedef unsigned int u32;
NOIPA u32 vla_sum6(u32 a, u32 b, u32 c, u32 d, u32 e, u32 f)
{
    volatile u32 arr[(a & 7) + 1];
    for (u32 i = 0; i <= (a & 7); i++) arr[i] = b + i;
    u32 s = 0;
    for (u32 i = 0; i <= (a & 7); i++) s += arr[i];
    return s + c + d + (e << 1) - f;
}
u32 drive_vla6(u32 a, u32 b)
{
    return vla_sum6(a, b, a ^ 7u, b ^ 9u, a + b, a + b + 1u) + 3u;
}
"""
CALL6 = re.compile(r"vla_sum6\(r0, r1, r2, r3, \*\(volatile u32 \*\)\(sp0 - (\d+)\), \*\(volatile u32 \*\)\(sp0 - (\d+)\)\)")


@needs_arm_gcc
@pytest.mark.parametrize("opt", ["-Os", "-O2"])
def test_a_vla_callee_with_six_arguments(tmp_path, opt, monkeypatch):
    """vla_sum6 has a variable-length array, so its stack pointer is not static and its stack arguments
    (the fifth and sixth) are read through a frame pointer: stack_args is None, scan_stack_args finds
    two. drive_vla6 stores them in its outgoing area; before, it was refused (its callee's stack
    arguments not known), after, it is accepted passing two stack arguments, every case agreeing. Both
    compiled by GCC at -Os and -O2."""
    (tmp_path / "t.c").write_text(VLA6_C)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mthumb", "-mcpu=cortex-m3", "-mfloat-abi=soft", "-ffreestanding", opt,
                    "-ffunction-sections", "-c", "-o", obj, str(tmp_path / "t.c")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "drive_vla6", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(M_TOML % {"cpu": "cortex-m3", "more": "", "compiler": M_GCC % "cortex-m3"})
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))

    assert lifter.stack_args(p.by_name["vla_sum6"]) is None           # the variable-length array moves sp
    assert lifter.scan_stack_args(p.by_name["vla_sum6"]) == 2         # the fifth and sixth arguments

    # before: the callee's stack arguments were not known, so the caller was refused
    def no_infer(self, callee, insn):
        raise lift.Refused("0x%x calls %s, whose stack arguments are not known" % (insn.address, callee.name))
    monkeypatch.setattr(lift.Lifter, "inferred_stack_args", no_infer)
    with pytest.raises(lift.Refused, match="stack arguments are not known"):
        lift.make_lifter(p, regflow.analyse(p)).lift(p.by_name["drive_vla6"])
    monkeypatch.undo()

    # after: the caller passes two stack arguments, the two lowest words of its outgoing area (where they
    # lie depends on the compiler's frame: GCC 7 keeps eight bytes more than GCC 10 and later)
    text = lifter.lift(p.by_name["drive_vla6"]).text
    call = CALL6.search(text)
    assert call and int(call.group(1)) == int(call.group(2)) + 4, text
    lines = []
    accepted = lift.check(t, t.image(), ["vla_sum6", "drive_vla6"], out=lines.append, jobs=2)
    assert accepted == ["drive_vla6"], "\n".join(lines)

    # stack arguments read from the wrong outgoing words (the fifth from the sixth's and the sixth from
    # the fifth's, both inside the frame) are caught by the cases
    monkeypatch.undo()
    orig = lift.Lifter._stack_arg_values

    def swapped(self, insn, nstack):
        return orig(self, insn, nstack)[::-1]
    monkeypatch.setattr(lift.Lifter, "_stack_arg_values", swapped)
    lines = []
    accepted = lift.check(t, t.image(), ["vla_sum6", "drive_vla6"], out=lines.append, jobs=2)
    assert "drive_vla6" not in accepted, "\n".join(lines)
