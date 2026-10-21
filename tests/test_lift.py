"""dread lift: what the gizmo builds lift and why the rest is refused, that the lifted C compiles,
and that the acceptance keeps what agrees with the original and drops a planted mistake. On gizmo-rv32
the translator is dread.lift_riscv (dread.lift.make_lifter)."""
import os
import re
import shutil

import pytest

from dread import build, lift, regflow
from dread.program import Program
from dread.target import Target
from conftest import GIZMO, gizmo_dir, have, needs_riscv_gcc, RISCV_CROSS, RISCV_MARCH, riscv_toml

needs_gcc = pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")


def lift_all(variant):
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
    return target, image, lifted, refused


def test_refusals_name_their_reason(variant):
    target, image, lifted, refused = lift_all(variant)
    if variant == "rv32":       # the startup sets sp from a constant (la sp, __stack_top)
        assert "changes the stack pointer in a way not modelled: auipc sp" in refused.pop("_start")
        assert refused == {}
        return
    # uart_puts reads r1 behind the call of uart_putc, which never writes it (GCC's -fipa-ra kept a
    # value there; dread.regflow's preserved): the C keeps its r1 across the call (it was refused)
    assert "uart_puts" not in refused and "r0 = uart_putc(r0, r1, r2, r3); r2 = r3 = r12 = 0;" in \
        lifted["uart_puts"].text
    if variant == "a32":
        assert "changes the stack pointer" in refused.pop("reset")
        # libgcc's __aeabi_uidivmod branches into the division by zero of another function's code (it
        # was refused for it): that code is its own now (shared_code)
        assert lifted["__aeabi_uidivmod"].body
    assert refused == {}


def test_exception_handler_and_main_lift(variant):
    target, image, lifted, refused = lift_all(variant)
    assert "main" in lifted                     # never returns: the system run decides (system_check)
    if variant == "a32":                        # an exception handler: gcc's interrupt attribute
        assert "irq_entry" in lifted and "return;" in lifted["irq_entry"].text
    if variant == "rv32":                       # the trap entry ends in mret: clang's interrupt("machine")
        text = lifted["trap_entry"].text
        assert 'void __attribute__((interrupt("machine"))) trap_entry(void)' in text and "return;" in text


def test_translation_shapes(variant):
    target, image, lifted, refused = lift_all(variant)
    text = lifted["script_run"].text
    assert "switch (index) {" in text and text.count("case ") == (8 if variant == "rv32" else 7)
    assert "u32 frame[" in text and "volatile" in text
    if variant == "rv32":       # the far call into RAM (auipc + jalr) is a call by name; the frame
        assert "a0 = ram_crc(a0, a1, a2, a3, a4, a5, a6, a7);" in lifted["main"].text
        assert "(sp0 - " in lifted["apply_config"].text
        return
    if variant in ("m3", "m4"):
        assert "u32 x = r2, a = r1;" in text                            # lsls r2, r1 shifts r2 by r1
    apply = lifted["apply_config"].text
    assert "(sp0 - " in apply                                           # its line buffer is in the frame
    veneer = lifted["__ram_crc_veneer"].text                            # ldr pc, =ram_crc: a far tail call
    assert "return ((u32 (*)(u32, u32, u32, u32))0x%xu)(r0, r1, r2, r3);" % (
        0x40000000 if variant == "a32" else 0x20000001) in veneer


def test_never_returns(variant):
    from dread import noreturn
    target = Target.load(gizmo_dir(variant))
    expected = {"a32": {"hang", "main", "reset"}, "m3": {"Default_Handler", "Reset_Handler", "main"},
                "m4": {"Default_Handler", "Reset_Handler", "main"},
                "rv32": {"_start", "main"}}[variant]
    assert noreturn.find(Program(target, target.image())) == expected


def test_a_call_that_never_returns_ends_the_path():
    target, image, lifted, refused = lift_all("m3")
    text = lifted["Reset_Handler"].text                 # bl main is its last instruction; a pool follows
    assert "r0 = main(r0, r1, r2, r3); r1 = r2 = r3 = r12 = 0; for (;;) { }" in text


@pytest.mark.compiles
def test_lifted_c_compiles(variant, tmp_path):
    target, image, lifted, refused = lift_all(variant)
    src = tmp_path / "lifted"
    src.mkdir()
    for name, result in lifted.items():
        (src / (name + ".c")).write_text(result.text)
    objects = build.compile_all(target, image, str(src), str(tmp_path / "obj"))
    assert len(objects) == len(lifted)


@pytest.mark.compiles
def test_objects_follow_the_flags(tmp_path):
    """An object made with other flags is compiled again though its source is older (once a
    target's -O0 became -Os and most of its objects stayed at -O0); unchanged, it is left alone."""
    target = Target.load(gizmo_dir("m3"))
    image = target.image()
    src = tmp_path / "src"
    src.mkdir()
    (src / "f.c").write_text("int f(int a, int b) { int c = a * b; return c + a; }\n")
    obj = str(tmp_path / "obj")
    target.compiler.cflags = [f for f in target.compiler.cflags if not f.startswith("-O")] + ["-O0"]
    path = build.compile_all(target, image, str(src), obj)[0]
    at_o0 = open(path, "rb").read()
    target.compiler.cflags[-1] = "-Os"
    assert build.compile_all(target, image, str(src), obj) == [path]
    at_os = open(path, "rb").read()
    assert at_os != at_o0
    made = os.stat(path).st_mtime_ns
    build.compile_all(target, image, str(src), obj)
    assert os.stat(path).st_mtime_ns == made and open(path, "rb").read() == at_os


def last_level(stamp_file):
    with open(stamp_file) as f:
        return [l for l in f.read().split("\n") if l.startswith("-O")][-1]


@pytest.mark.compiles
def test_lifted_c_is_compiled_for_size(tmp_path):
    """Lifted C gets [compiler] lifted_cflags after cflags (default -Os), hand-written C cflags alone:
    at -O0 a lifted function keeps every register of the original in its frame (FreeRTOS's GCC -O0
    build: six functions more at -Os, none lost); lifted_cflags = [] leaves cflags alone."""
    shutil.copytree(GIZMO, tmp_path / "gizmo", ignore=shutil.ignore_patterns("build"))
    copy = tmp_path / "gizmo" / os.path.relpath(gizmo_dir("m3"), GIZMO)
    target = Target.load(str(copy))
    assert target.compiler.lifted_cflags == ["-Os"]
    target.compiler.cflags = [f for f in target.compiler.cflags if not f.startswith("-O")] + ["-O0"]
    out = tmp_path / "out"
    build.build(target, target.image(), out=str(out), quiet=True)
    assert last_level(out / "obj-lifted" / ".flags") == "-Os"
    assert last_level(out / "obj" / ".flags") == "-O0"
    toml = copy / "dread.toml"
    toml.write_text(toml.read_text().replace("[compiler]\n", "[compiler]\nlifted_cflags = []\n", 1))
    assert Target.load(str(copy)).compiler.lifted_cflags == []


NAMES = ["script_run", "read_line", "apply_config", "fmt_dec", "timer_irq", "parse_kv"]


@pytest.fixture(scope="module")
def copies(tmp_path_factory):
    """One copy of gizmo for the module, with the generated cases and the register flow the
    example has already cached (they are keyed by what they depend on, so they stay valid)."""
    copy = tmp_path_factory.mktemp("lift") / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    for variant in ("a32", "m3", "m4", "rv32"):
        for cache in ("autocases", "regflow"):
            here = os.path.join(gizmo_dir(variant), "build", cache)
            if os.path.isdir(here):
                shutil.copytree(here, copy / variant / "build" / cache)
    return copy


def copy_of(copies, variant):
    target = Target.load(str(copies / variant))
    return target, target.image()


@pytest.mark.compiles
def test_float_functions_lift_with_the_hard_float_convention(copies):
    """gizmo-m4's float functions: float arguments in s0-s2 (lp_filter, to_millivolts), a float
    result (average), a call of one with a float argument (signal_step). The lifted C mirrors s0-s15
    at its entry, its returns and around its calls, the generated cases give float arguments, and
    every case agrees and runs every instruction."""
    target, image = copy_of(copies, "m4")
    names = ["lp_filter", "to_millivolts", "average", "signal_step"]
    lines = []
    assert sorted(lift.check(target, image, names, out=lines.append, jobs=4)) == sorted(names), lines
    program = Program(target, image)
    text = lift.make_lifter(program, regflow.load(target, image, program)).lift(program.by_name["to_millivolts"]).text
    assert 'vmov %0, s0' in text and 'vcvt.s32.f32 s15, s14' in text and 'vmov s0, %0' in text


@pytest.mark.compiles
def test_a_check_by_name_says_what_left_the_table(tmp_path, capsys):
    """--check with names writes the lifted table with those of them that passed; the functions it
    listed before leave it, and the command says so (checking one function once emptied gizmo-m4's
    table unnoticed)."""
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    for cache in ("autocases", "regflow"):
        here = os.path.join(gizmo_dir("m3"), "build", cache)
        if os.path.isdir(here):
            shutil.copytree(here, copy / "m3" / "build" / cache)
    before = len(build.read_names(str(copy / "m3" / "config" / "lifted.txt")))
    assert lift.main(["--target", str(copy / "m3"), "--check", "fmt_dec"]) == 0
    assert build.read_names(str(copy / "m3" / "config" / "lifted.txt")) == ["fmt_dec"]
    assert "%d it listed before are built from the original's code again" % (before - 1) in capsys.readouterr().out


@pytest.mark.slow
@pytest.mark.compiles
def test_check_accepts_what_agrees(variant, copies):
    target, image = copy_of(copies, variant)
    lines = []
    accepted = lift.check(target, image, NAMES, out=lines.append, jobs=4)
    assert sorted(accepted) == sorted(NAMES), lines
    assert build.read_names(image.table("lifted")) == sorted(NAMES)
    payload = build.build(target, image, quiet=True)                    # dread recon builds them from now on
    assert build.read_names(os.path.join(os.path.dirname(payload), "lifted.txt")) == sorted(NAMES)


@pytest.mark.slow
@pytest.mark.compiles
def test_check_drops_a_planted_mistake(variant, copies, monkeypatch):
    target, image = copy_of(copies, variant)
    if variant == "rv32":       # the unsigned branches reversed: fmt_dec's loop has one, keys_read none
        from dread import lift_riscv
        monkeypatch.setitem(lift_riscv.CONDS, "bltu", "%s > %s")
        monkeypatch.setitem(lift_riscv.CONDS, "bgeu", "%s <= %s")
        lines = []
        accepted = lift.check(target, image, ["fmt_dec", "keys_read"], out=lines.append, jobs=4)
        assert accepted == ["keys_read"], lines
        assert any(line.startswith("dropped  FAIL fmt_dec") for line in lines), lines
        return
    compare = lift.Lifter._compare

    def wrong_carry(self, insn, ops, kind):                             # cmp leaves the carry inverted
        text = compare(self, insn, ops, kind)
        return text.replace("c = (u32)(t >> 32);", "c = !(u32)(t >> 32);") if kind == "cmp" else text
    monkeypatch.setattr(lift.Lifter, "_compare", wrong_carry)
    lines = []
    accepted = lift.check(target, image, ["script_run", "keys_read"], out=lines.append, jobs=4)
    assert accepted == ["keys_read"], lines                             # keys_read compares nothing
    assert any(line.startswith("dropped  FAIL script_run") for line in lines)
    from dread.gui import read_tsv                                      # the record readers such as dread gui use
    verdicts = {r["function"]: r for r in read_tsv(os.path.join(target.build, "lift-check", image.name,
                                                                "verdicts.tsv"))}
    assert verdicts["keys_read"]["verdict"] == "accepted"
    assert verdicts["script_run"]["verdict"] == "dropped" and verdicts["script_run"]["reason"].startswith("FAIL")


@pytest.mark.slow
@pytest.mark.compiles
def test_a_main_loop_is_accepted_by_the_system_run(variant, copies):
    """main never returns, so no case of it ends: the scenarios decide. They agree on both images and
    run all of it only because a due interrupt is taken after a wait in code built from C (main's own
    idle loop): without that, the timer never fires in either image and the key path stays unrun."""
    target, image = copy_of(copies, variant)
    lines = []
    accepted = lift.check(target, image, ["main", "fmt_dec"], out=lines.append, jobs=4, loop_cases=False)
    assert sorted(accepted) == ["fmt_dec", "main"], lines
    assert any(line.startswith("accepted main") and "every scenario agrees" in line for line in lines)
    assert "main  # accepted by the system run" in open(image.table("lifted")).read()
    import json
    from dread import build
    build.build(target, image, quiet=True)          # from the table: main stays the system run's
    with open(os.path.join(build.recon_dir(target, image), "lifted.json")) as f:
        info = json.load(f)
    assert "main" in info["system"] and "main" not in info["loop"]


@pytest.mark.slow
@pytest.mark.compiles
def test_a_main_loop_is_accepted_by_loop_cases(variant, copies):
    """Loop cases: main's callees stubbed, each case compared at the same stub call in both images,
    every instruction run by some case; the system run is not needed for it."""
    target, image = copy_of(copies, variant)
    lines = []
    accepted = lift.check(target, image, ["main", "fmt_dec"], out=lines.append, jobs=4)
    assert sorted(accepted) == ["fmt_dec", "main"], lines
    assert any("(1 by loop cases, 0 by the system run" in line for line in lines), lines[-3:]
    assert "the whole image agrees" in lines[-1], lines[-1]
    marked = open(image.table("lifted")).read()
    assert "\nmain\n" in marked and "accepted by the system run" not in marked


@pytest.mark.slow
@pytest.mark.compiles
def test_loop_cases_and_the_system_run_together(variant, copies):
    """A function that never returns may be accepted by what its loop cases ran (when they all
    agreed) and what the scenarios ran, together; it is built alone for that. In the boot scenario
    alone main never handles a key: with the key path given as run by its loop cases, the two run
    all of it; with loop cases that ran nothing, the key path keeps it out."""
    from dread import sysrun
    target, image = copy_of(copies, variant)
    target.scenarios = {"boot": target.scenarios["boot"]}          # no key comes
    program = Program(target, image)
    lifter = lift.make_lifter(program, regflow.load(target, image, program))
    fn = program.by_name["main"]
    coverage = lift.Coverage(program, ["main"])
    system = sysrun.System(target, image, image.payload(), program=program,
                           scenario=dict(target.scenarios["boot"], _name="boot"))
    system.observers.append(coverage)
    system.run(int(target.scenarios["boot"].get("limit", sysrun.LIMIT)))
    unrun = set(fn.insns) - coverage.ran - set(lifter.lift(fn).unreached)
    assert unrun                                                    # the key path
    lines, verdicts = [], {}
    kept = lift.system_check(target, image, program, lifter, ["main"], out=lines.append, verdicts=verdicts,
                             loop_missed={"main": set(fn.insns) - unrun}, jobs=2)
    assert kept == {"main": "loop cases and the system run"}, lines
    kept = lift.system_check(target, image, program, lifter, ["main"], out=lines.append, verdicts=verdicts,
                             loop_missed={"main": set(fn.insns)}, jobs=2)
    assert kept == {} and "the system run and its loop cases never ran" in verdicts["main"][1], verdicts


@pytest.mark.slow
@needs_gcc
def test_an_exception_handler_is_lifted_with_the_interrupt_attribute(copies):
    """gizmo-a32's IRQ entry (sub lr, lr, #4; push; bl irq_dispatch; ldm sp!, {.., pc}^): gcc writes
    the entry and the exception return, and the scenarios, which take timer interrupts, decide."""
    target, image = copy_of(copies, "a32")
    lifted = lift_all("a32")[2]
    assert 'void __attribute__((interrupt("IRQ"))) irq_entry(void)' in lifted["irq_entry"].text
    lines = []
    assert lift.check(target, image, ["irq_entry"], out=lines.append, jobs=4) == ["irq_entry"], lines
    assert any(line.startswith("accepted irq_entry") and "an exception handler" in line for line in lines)


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
@pytest.mark.parametrize("opt", ["-Os", "-O2"])
def test_a_branch_on_a_constant_the_function_stored_is_decided(tmp_path, opt):
    """FreeRTOS's static-creation functions store a size into a volatile local, load it back and
    assert that it equals itself: no input runs the assertion's call, and the functions stayed
    UNCOVERED. The walk knows the constant through the frame slot and the compare; the other way of
    the branch is unreached (left out of the C and of the coverage it needs). At -O2 GCC branches
    to the assertion behind the return: a branch decided never taken, which is nothing in the C (it
    was refused for branching into other code)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.c").write_text("__attribute__((noipa)) void fail(void) { for (;;) ; }\n"
                                  "int check(int n) { volatile int x = 92; if (__builtin_expect(x != 92, 0)) fail();"
                                  " return n + 1; }\n")      # (unlikely: -O2 puts the call behind the return)
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", opt, "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "check", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    fn = p.by_name["check"]
    fail = p.by_name["fail"].entry
    calls = [a for a in fn.insns if p.arch.flow(p.insn(a))[0] == "call" and p.arch.target(p.insn(a)) == fail]
    assert calls, "the check calls fail"
    result = lift.make_lifter(p).lift(fn)
    assert set(calls) <= set(result.unreached), [hex(a) for a in result.unreached]
    assert "fail" not in result.text


SP_STORE_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global use, f
    .type use, %function
    .thumb_func
use:    movs    r3, #0
        mov     r2, r3
        push    {r4, r5, lr}
1:      cmp     r3, r1
        blt     2f
        mov     r0, r2
        pop     {r4, r5, pc}
2:      ldr     r4, [r0, r3, lsl #2]
        adds    r3, r3, #1
        ldrd    r5, r4, [r4]
        mla     r2, r4, r5, r2
        b       1b
    .size use, . - use
    .type f, %function
    .thumb_func
f:      push    {r0, r1, r2, r3, r4, r5, r6, lr}
        movs    r3, #1
        movs    r1, #2
        str     r0, [sp]
        add     r0, r0, r3
        str     r3, [sp, #4]
        str     r0, [sp, #8]
        add     r3, sp, #8
        add     r0, sp, #16
        str     r1, [sp, #12]
        str     sp, [sp, #16]
        str     r3, [sp, #20]
        bl      use
        add     sp, sp, #28
        pop     {pc}
    .size f, . - f
"""


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
@pytest.mark.parametrize("opt", ["-Os", "-O2", "asm"])
def test_the_stack_pointer_stored_as_a_value(tmp_path, opt):
    """GCC keeps the address of a local at the bottom of the frame with str sp, [sp, #k] (C++ keeps
    the object a call through its vtable gets as `this` so): the lifter stores the frame's address
    there, as it gives it for mov rX, sp, instead of refusing; the lift check accepts the function.
    The C cases test what this compiler makes of the snippet and are skipped where it stores no sp
    (GCC 14.2 at -O2 builds the array otherwise); "asm" is GCC 16's -Os code of it, the shape on any
    compiler."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    if opt == "asm":
        (tmp_path / "t.s").write_text(SP_STORE_S)
    (tmp_path / "t.c").write_text(
        "struct obj { unsigned a, b; };\n"
        "__attribute__((noipa)) unsigned use(struct obj **all, int n)\n"
        "{ unsigned t = 0; for (int i = 0; i < n; i++) t += all[i]->a * all[i]->b; return t; }\n"
        "unsigned f(unsigned x) { struct obj p = {x, 1}, q = {x + 1, 2}; struct obj *all[] = {&p, &q};"
        " return use(all, 2); }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH }\n")
    elf = str(tmp_path / "t.elf")
    flags = ["-mcpu=cortex-m3", "-mthumb", "-Os" if opt == "asm" else opt, "-nostdlib", "-ffunction-sections"]
    subprocess.run(["arm-none-eabi-gcc"] + flags + ["-T", str(tmp_path / "t.ld"), "-e", "f", "-o", elf,
                    str(tmp_path / ("t.s" if opt == "asm" else "t.c"))], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mcpu=cortex-m3", "-mthumb", "%s"]\n' % flags[2])
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    fn = p.by_name["f"]
    stores = [a for a in fn.insns if p.insn(a).mnemonic.startswith("str") and p.insn(a).op_str.startswith("sp,")]
    if not stores and opt != "asm":
        pytest.skip("this compiler's %s code of the snippet stores no sp (the asm case has the shape)" % opt)
    assert stores, "the code stores sp itself"
    result = lift.make_lifter(p).lift(fn)
    assert re.search(r"= \(u32\)\(sp0 - \d+\);", result.text), result.text
    lines = []
    accepted = lift.check(t, t.image(), ["f", "use"], out=lines.append, jobs=2)
    assert "f" in accepted, "\n".join(lines)


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_register_the_callee_never_writes_keeps_its_value_across_the_call(tmp_path):
    """GCC's -fipa-ra keeps a value in a scratch register across the call of a function that never
    writes it: use keeps d in r3 across twice and reads it behind the call. The lifted C keeps its
    variable instead of clearing it after the call, and use passes the lift check (it was refused
    for reading r3 behind the call)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.target import Target
    (tmp_path / "t.c").write_text("__attribute__((noinline)) unsigned twice(unsigned x) { return x + x; }\n"
                                  "unsigned use(unsigned a, unsigned b, unsigned c, unsigned d)"
                                  " { return twice(a) + d * 3; }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "use", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mcpu=cortex-m3", "-mthumb", "-O2", "-ffunction-sections"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    image = t.image()
    lines = []
    accepted = lift.check(t, image, ["use"], out=lines.append, jobs=2)
    assert accepted == ["use"], "\n".join(lines)


@pytest.mark.compiles
def test_a_full_room_drops_the_largest_and_the_check_goes_on(tmp_path):
    """When the C does not fit into its room behind the image (an MSP430's 64 KiB: FreeRTOS's build
    filled it), the overlay says by how much and which functions it could not place; the check drops
    the largest of them until that is covered and builds again, instead of failing whole."""
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    for cache in ("autocases", "regflow"):
        here = os.path.join(gizmo_dir("m3"), "build", cache)
        if os.path.isdir(here):
            shutil.copytree(here, copy / "m3" / "build" / cache)
    image = Target.load(str(copy / "m3")).image()
    end = image.base + len(image.payload())
    toml = copy / "m3" / "dread.toml"
    text = toml.read_text()
    assert text.count("limit = 0x08010000") == 1
    toml.write_text(text.replace("limit = 0x08010000", "limit = 0x%x" % (end + 0x100)))
    target = Target.load(str(copy / "m3"))
    lines = []
    accepted = lift.check(target, target.image(), NAMES, out=lines.append, jobs=4)
    assert any("no room left for its C" in line for line in lines), "\n".join(lines)
    assert not any(line.startswith("dread lift: the build failed") for line in lines), "\n".join(lines)
    assert accepted and set(accepted) < set(NAMES), "\n".join(lines)


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_declared_stack_arguments_of_a_context_switch(tmp_path):
    """A function that loads its stack pointer from memory not on the stack (a context switch) takes
    the stack words it reads before the switch: none here, so its callers lift as they are. One whose
    stack pointer is not static otherwise (taken from a register the analysis does not know: here an
    argument) has stack arguments the lifter cannot count, so its callers are refused; [abi]
    stack_args says how many, and they lift."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    (tmp_path / "t.c").write_text(
        "unsigned saved;\n"
        "__attribute__((naked, noinline)) void switcher(void)\n"
        "{ __asm__ volatile (\"ldr r3, =saved\\n str sp, [r3]\\n ldr sp, [r3]\\n bx lr\\n .ltorg\"); }\n"
        "__attribute__((naked, noinline)) void adjuster(unsigned to)\n"
        "{ __asm__ volatile (\"mov sp, r0\\n bx lr\"); }\n"
        "unsigned caller(unsigned x) { switcher(); return x + 1; }\n"
        "unsigned other(unsigned x) { adjuster(x); return x + 2; }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "caller", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    toml = TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n' \
        '\n[compiler]\ncross = "arm-none-eabi-"\n'
    (tmp_path / "dread.toml").write_text(toml)
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    for declared in (False, True):
        if declared:
            with open(tmp_path / "dread.toml", "a") as f:
                f.write("\n[abi]\nstack_args = {adjuster = 0}\n")
        t = Target.load(str(tmp_path))
        p = Program(t, t.image())
        lifter = lift.make_lifter(p)
        assert lifter.switches_stacks(p.by_name["switcher"]) and lifter.stack_args(p.by_name["switcher"]) == 0
        assert not lifter.switches_stacks(p.by_name["adjuster"])
        assert "switcher(r0, r1, r2, r3)" in lifter.lift(p.by_name["caller"]).text
        if declared:
            assert "adjuster(r0, r1, r2, r3)" in lifter.lift(p.by_name["other"]).text
        else:
            with pytest.raises(lift.Refused, match="calls adjuster, whose stack arguments are not known"):
                lifter.lift(p.by_name["other"])


def test_registers_the_callers_keep_are_compiled_call_saved(tmp_path):
    """A scratch register the callers read after the call and the function leaves unchanged (GCC's
    -fipa-ra: FreeRTOS's prints keeps a value in r2 across printchar) is kept by the C: the lifted
    file names it on its first line and GCC compiles it call-saved. Not the result, the ABI's
    callee-saved registers or the veneers' register; no flag for another compiler."""
    t = Target.load(gizmo_dir("m3"))
    program = Program(t, t.image())

    class Flow:
        def kept_regs(self, name):
            return ["r0", "r2", "r3", "r4", "r12", "flags"]

        def preserved_regs(self, name):
            return ["r0", "r2", "r4", "r12"]
    lifter = lift.make_lifter(program, Flow())
    assert lifter.call_saved(program.by_name["parse_kv"]) == ["r2"]
    source = tmp_path / "f.c"
    source.write_text(build.CALL_SAVED + "r2 r3 */\nint f(void) { return 0; }\n")
    assert build.file_flags(t, str(source)) == ["-fcall-saved-r2", "-fcall-saved-r3"]
    t.compiler.kind = "clang"
    assert build.file_flags(t, str(source)) == []
    (tmp_path / "g.c").write_text("int g(void) { return 0; }\n")
    t.compiler.kind = "gcc"
    assert build.file_flags(t, str(tmp_path / "g.c")) == []


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_the_run_time_abi_helpers_take_no_stack_arguments(tmp_path):
    """libgcc's __aeabi_uldivmod moves sp before it calls __udivmoddi4, so its stack arguments could
    not be counted and every caller of a 64-bit division was refused. Arm's run-time ABI says its
    helpers take every argument in r0-r3 (the ARM layer's RTABI_RESULTS), and that this one returns
    the quotient in r0:r1 and the remainder in r2:r3: regflow counts those as read after every call."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport, regflow
    (tmp_path / "t.c").write_text(
        "unsigned long long q(unsigned long long a, unsigned long long b) { return a / b; }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "q", "-o", elf, str(tmp_path / "t.c"), "-lgcc"], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    lifter = lift.make_lifter(p)
    helper = p.by_name["__aeabi_uldivmod"]
    with pytest.raises(lift.Refused, match="changes the stack pointer"):    # (libgcc's: strd ip, lr, [sp, #-16]!)
        lifter.depths(helper, strict=False, switch=True)
    assert lifter.stack_args(helper) == 0
    assert "__aeabi_uldivmod(r0, r1, r2, r3)" in lifter.lift(p.by_name["q"]).text
    flow = regflow.analyse(p)
    assert {"r0", "r1", "r2", "r3"} <= set(flow.kept_regs("__aeabi_uldivmod")), flow.kept_regs("__aeabi_uldivmod")


@pytest.mark.skipif(not shutil.which("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_call_whose_caller_reads_what_c_cannot_return(tmp_path):
    """The remainder of a 64-bit division comes back in r2:r3 of __aeabi_uldivmod, and libgcc's
    __aeabi_fcmplt reads the flags __aeabi_cfcmple leaves: a C call returns neither, so both callers
    were refused. The lifter puts such a call into an asm statement that binds r0-r3 and reads the
    flags right behind the bl (_bound_call), and the lifted C agrees with the original."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    (tmp_path / "t.c").write_text(          # use reads both results, as callers of the originals do
        "__attribute__((noinline)) unsigned long long r(unsigned long long a, unsigned long long b)"
        " { return a % b; }\n"
        "__attribute__((noinline)) int lt(float a, float b) { return a < b; }\n"
        "__attribute__((noinline)) unsigned long long two(unsigned long long a, unsigned long long b,"
        " unsigned long long c, unsigned long long d) { return a / b + c % d; }\n"
        "volatile unsigned long long A, B, R, T; volatile float X, Y; volatile int L;\n"
        "void use(void) { R = r(A, B); L = lt(X, Y); T = two(A, B, B, A); }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH .bss : { *(.bss*) *(COMMON) } > RAM }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "use", "-o", elf, str(tmp_path / "t.c"), "-lgcc"], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mthumb", "-mcpu=cortex-m3", "-Os"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    from dread import regflow
    lifter = lift.make_lifter(p, regflow.analyse(p))
    assert '"bl __aeabi_uldivmod"' in lifter.lift(p.by_name["r"]).text
    assert 'bl __aeabi_cfcmple\\n\\tmrs' in lifter.lift(p.by_name["__aeabi_fcmplt"]).text
    text = lifter.lift(p.by_name["two"]).text           # one call bound, one a C call: one prototype
    assert text.count("extern u64 __aeabi_uldivmod") == 1 and '"bl __aeabi_uldivmod"' in text, text
    lines = []
    accepted = lift.check(t, t.image(), ["r", "__aeabi_fcmplt", "two"], out=lines.append, jobs=2)
    assert set(accepted) == {"r", "__aeabi_fcmplt", "two"}, "\n".join(lines)


ADR_POOL_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global ll, use
    .type ll, %function
    .thumb_func
ll:     adr     r1, 1f          @ GCC's 64-bit constant: the pool's address, then ldrd through it
        ldrd    r0, r1, [r1]
        bx      lr
        .align 2
1:      .word   0x55667788
        .word   0x11223344
    .type use, %function
    .thumb_func
use:    push    {r4, lr}        @ reads both words of the result
        bl      ll
        ldr     r4, =0x20000000
        strd    r0, r1, [r4]
        pop     {r4, pc}
        .ltorg
"""


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                    reason="needs arm-none-eabi-as and -gcc")
def test_a_pool_the_c_reads_through_adr_is_kept(tmp_path):
    """The lifter makes a pc-relative load a constant, but an adr's address stays an address in
    the C, which loads through it at run time. GCC compiled it as mov.w r3, #0x8000000 and offsets,
    so no word of the C pointed at the pool, and the overlay built the C over it: GCC's ret_ll of the
    compiler lab read its own instructions (return value 0x55667788 vs 0x31c4f8d3)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    (tmp_path / "t.s").write_text(ADR_POOL_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "use", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mthumb", "-mcpu=cortex-m3", "-Os"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    lines = []
    assert lift.check(t, t.image(), ["ll"], out=lines.append, jobs=1) == ["ll"], "\n".join(lines)


SHARED_CODE_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global f, g, use
    .type f, %%function
    .thumb_func
f:      push    {r4, r5, lr}    @ falls into the shared tail with its frame open
        movs    r4, #3
        adds    r5, r0, r1
    .size f, . - f
%s
shared: adds    r0, r4, r5      @ the tail f and g share: it ends their frame
        lsls    r0, r0, #1
        pop     {r4, r5, pc}
    .type g, %%function
    .thumb_func
g:      push    {r4, r5, lr}    @ branches into it with its frame open
        movs    r4, #2
        subs    r5, r0, r1
        b       shared
    .size g, . - g
    .type use, %%function
    .thumb_func
use:    push    {r4, lr}
        bl      f
        mov     r4, r0
        bl      g
        add     r0, r0, r4
        pop     {r4, pc}
"""


@pytest.mark.parametrize("named", [True, False], ids=["a function", "a label"])
@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                    reason="needs arm-none-eabi-as and -gcc")
def test_code_shared_between_functions(tmp_path, named):
    """A compiler's cross call keeps a tail several functions share once and enters it with their
    frame still open, by falling into it or by a branch; the tail ends the frame. As a function of its
    own (IAR's ?SubroutineN) it was refused (falls into it with 12 bytes of frame; it returns through
    its callers' frame), as a local label only (IAR's version 8) its callers branched into other code
    or fell into what follows. Each caller is lifted with the shared code as its own (Body), and its
    cases cover that code too."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport, regflow
    symbol = "    .type shared, %function\n    .thumb_func" if named else ""
    (tmp_path / "t.s").write_text(SHARED_CODE_S % symbol)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "use", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mthumb", "-mcpu=cortex-m3", "-Os"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    assert ("shared" in p.by_name) == named
    lifter = lift.make_lifter(p, regflow.analyse(p))
    for name in ("f", "g"):
        got = lifter.lift(p.by_name[name])
        assert got.body and set(p.by_name[name].insns) < set(got.body), (name, got.body)
    lines = []
    assert sorted(lift.check(t, t.image(), ["f", "g"], out=lines.append, jobs=1)) == ["f", "g"], "\n".join(lines)


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                    reason="needs arm-none-eabi-as and -gcc")
def test_shared_code_others_branch_into_stays(tmp_path):
    """f takes the shared code behind it in (a local label, no function's), g stays original and
    branches into it: the overlay must not give f's C the extent behind f, or g lands in f's C (on
    the other compiler's -Ohz build three callers of a two-byte b.n into such code failed so)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.elf import Elf
    (tmp_path / "t.s").write_text(SHARED_CODE_S % "")
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "use", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mthumb", "-mcpu=cortex-m3", "-Os"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    lines = []
    assert lift.check(t, t.image(), ["f"], out=lines.append, jobs=1) == ["f"], "\n".join(lines)
    shared = [s.value for s in Elf(elf).symbols if s.name == "shared"][0]
    original = (tmp_path / "original" / "t.bin").read_bytes()
    rebuilt = (tmp_path / "build" / "lift-check" / "main" / "payload.bin").read_bytes()
    lo, hi = shared - 0x08000000, shared - 0x08000000 + 12
    assert rebuilt[lo:hi] == original[lo:hi]


TWO_BYTES_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global h, k, use
    .type h, %%function
    .thumb_func
h:      b.n     common          @ a two-byte function: one narrow branch into shared code
    .size h, . - h
    .type k, %%function
    .thumb_func
k:      movs    r1, #1          @ another function that enters it (it stays original)
        b.n     common
    .size k, . - k
common: adds    r0, r2, r3      @ code no function owns
        eors    r0, r0, r1
        lsls    r0, r0, #3
        adds    r0, r0, #7
        bx      lr
        .align  2
gap:    .space  %d, 0xff        @ declared free: room within reach of h's narrow branch
    .type use, %%function
    .thumb_func
use:    push    {r4, lr}
        movs    r1, #3
        movs    r2, #5
        movs    r3, #9
        bl      h
        mov     r4, r0
        bl      k
        add     r0, r4
        pop     {r4, pc}
"""


@pytest.mark.parametrize("gap", [32, 4], ids=["the C within reach", "a trampoline within reach"])
@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                    reason="needs arm-none-eabi-as and -gcc")
def test_a_two_byte_function_gets_a_narrow_branch(tmp_path, gap):
    """A function that is one b.n into shared code (a compiler's cross call: eight such probe
    functions of the other compiler at -Ohz) has no room for the four-byte branch to its C. A narrow
    b reaches 2 KiB: the C goes into room within reach, or a trampoline that branches to it does,
    and the cases enter through the narrow branch."""
    import json
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.elf import Elf
    (tmp_path / "t.s").write_text(TWO_BYTES_S % gap)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "use", "-o", elf, obj], check=True)
    at = {s.name: s.value & ~1 for s in Elf(elf).symbols if s.name in ("h", "gap")}
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\nfree = [[0x%x, 0x%x]]\n' % (at["gap"], at["gap"] + gap) +
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mthumb", "-mcpu=cortex-m3", "-Os"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    lines = []
    assert lift.check(t, t.image(), ["h"], out=lines.append, jobs=1) == ["h"], "\n".join(lines)
    work = tmp_path / "build" / "lift-check" / "main"
    placed = json.loads((work / "placement.json").read_text())["functions"]["h"]
    rebuilt = (work / "payload.bin").read_bytes()
    arch = t.image().arch
    to = arch.short_branch_target(rebuilt[at["h"] - 0x08000000:], at["h"], "t32")
    if gap == 32:
        assert to == placed["address"] and "via" not in placed
    else:
        assert to == placed["via"] and at["gap"] <= to < at["gap"] + gap
        assert rebuilt[to - 0x08000000:to - 0x08000000 + 4] == arch.entry_branch(to, placed["address"], "t32")


CALLER_LEFT_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global frag, add2, caller, use
    .type frag, %function
    .thumb_func
frag:   mov     r1, r6          @ shared code that reads its caller's r6 (a compiler's cross call)
        b.w     add2
    .size frag, . - frag
    .type add2, %function
    .thumb_func
add2:   adds    r0, r0, r1
        bx      lr
    .size add2, . - add2
    .type caller, %function
    .thumb_func
caller: push    {r6, lr}
        mov     r6, r1
        bl      frag            @ r0 + r6
        pop     {r6, pc}
    .size caller, . - caller
    .type use, %function
    .thumb_func
use:    push    {r4, lr}
        bl      caller
        pop     {r4, pc}
    .size use, . - use
"""


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                    reason="needs arm-none-eabi-as and -gcc")
def test_a_callee_that_reads_a_register_its_caller_keeps(tmp_path):
    """A compiler's shared code called with bl may read a register the convention has its caller
    keep: mov r1, r6; b.w add2 passes the caller's r6 on. A copy is no use, but the tail call takes
    r1, so frag reads r6 its caller leaves (the analysis missed it: the C called frag with whatever
    its own r6 held, and the other compiler's keeps_fp failed its comparison). Now frag gets r6 as
    its caller left it (handed in), and its caller's call binds r6 from the C's variable."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport, regflow
    (tmp_path / "t.s").write_text(CALLER_LEFT_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "use", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mthumb", "-mcpu=cortex-m3", "-Os"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))
    assert lifter.leaves(p.by_name["frag"]) == ["r6"]
    assert lifter.lift(p.by_name["frag"]).handed == ["r6"]
    assert 'x6 __asm__("r6") = r6' in lifter.lift(p.by_name["caller"]).text
    lines = []
    assert lift.check(t, t.image(), ["caller"], out=lines.append, jobs=1) == ["caller"], "\n".join(lines)


HANDED_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global pad, g, h, use
    .type pad, %function
    .thumb_func
pad:    push    {r7, lr}        @ r7 only keeps the stack aligned; its pad is popped into r0
        bl      g
        pop     {r0, pc}        @ r0: the caller's r7 (a second compiler's -On, a function without a result)
    .size pad, . - pad
    .type g, %function
    .thumb_func
g:      movs    r1, #1
        bx      lr
    .size g, . - g
    .type h, %function
    .thumb_func
h:      adds    r0, r0, r4      @ reads what its caller left in r4
        bx      lr
    .size h, . - h
    .type use, %function
    .thumb_func
use:    push    {r4, r7, lr}
        movs    r7, #3
        bl      pad
        movs    r4, #9
        bl      h
        pop     {r4, r7, pc}
    .size use, . - use
"""


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                    reason="needs arm-none-eabi-as and -gcc")
def test_registers_the_caller_left_are_handed_in(tmp_path):
    """pad hands back its caller's r7 in r0 (the pad of an aligned frame, popped into a scratch
    register: the analysis took the pop for a write, the C returned an unwritten frame word, and the
    comparison refuted it); h reads its caller's r4 (it was refused). The C gets such registers as the
    caller left them: GCC compiles the file with -ffixed-REG, the C reads them at the entry, and a
    caller's call binds them."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport, regflow
    (tmp_path / "t.s").write_text(HANDED_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "use", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mthumb", "-mcpu=cortex-m3", "-Os"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))
    assert lifter.leaves(p.by_name["pad"]) == ["r7"] and lifter.leaves(p.by_name["h"]) == ["r4"]
    assert lifter.lift(p.by_name["pad"]).handed == ["r7"] and lifter.lift(p.by_name["h"]).handed == ["r4"]
    assert 'x4 __asm__("r4") = r4' in lifter.lift(p.by_name["use"]).text
    lines = []
    assert sorted(lift.check(t, t.image(), ["pad", "h", "use"], out=lines.append, jobs=1)) == ["h", "pad", "use"], \
        "\n".join(lines)
    with open(tmp_path / "build" / "lift-check" / "main" / "lifted" / "pad.c") as f:
        assert f.readline().startswith("/* dread: fixed r7")


VA_LIST_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global vsum, walk, local, use
    .type vsum, %function
    .thumb_func
vsum:   push    {r1, r2, r3}    @ a variadic function: its save area next to the stack arguments
        push    {lr}
        add     r1, sp, #4      @ va_start: the address of the saved r1
        bl      walk            @ walk reads r0 words through it, on into the stack arguments
        pop     {r3}
        add     sp, #12
        bx      r3
    .size vsum, . - vsum
    .type walk, %function
    .thumb_func
walk:   movs    r2, #0
1:      cbz     r0, 2f
        ldr     r3, [r1], #4
        add     r2, r3
        subs    r0, #1
        b       1b
2:      mov     r0, r2
        bx      lr
    .size walk, . - walk
    .type local, %function
    .thumb_func
local:  push    {r4, lr}        @ an address of its own local, no save area: lifts as before
        sub     sp, #8
        str     r0, [sp]
        mov     r1, sp
        movs    r0, #1
        bl      walk
        add     sp, #8
        pop     {r4, pc}
    .size local, . - local
    .type use, %function
    .thumb_func
use:    push    {r4, lr}
        bl      vsum
        bl      local
        pop     {r4, pc}
    .size use, . - use
"""


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                    reason="needs arm-none-eabi-as and -gcc")
def test_a_va_list_walks_on_into_the_stack_arguments(tmp_path):
    """A variadic function saves its argument registers next to its stack arguments, and a va_list
    walks from the one into the other; the C's frame (a local array) did not hold them next to each
    other, so a lifted copy read words of its own past the saved registers. FreeRTOS's sprintf (GCC,
    clang, ARM and RISC-V) was accepted so and printed a stack word of its own for its third
    argument: no generated case passed one. Such a function is now wrapped: an assembly entry reserves
    the original's frame on the real stack and hands the C body its stack pointer (sp0), so the walk
    goes on as in the original. An address of a local alone needs no wrapper."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport, regflow
    from dread.emu import Layout, Machine
    from dread.overlay import load_placement
    (tmp_path / "t.s").write_text(VA_LIST_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "use", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mthumb", "-mcpu=cortex-m3", "-Os"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))
    text = lifter.lift(p.by_name["vsum"]).text
    assert "__attribute__((naked)) void vsum(void)" in text and "u32 vsum_dread_body(" in text
    assert "naked" not in lifter.lift(p.by_name["local"]).text
    lines = []
    assert lift.check(t, t.image(), ["vsum"], out=lines.append, jobs=1) == ["vsum"], "\n".join(lines)
    work = tmp_path / "build" / "lift-check" / "main"
    aliases, _ = load_placement(str(work / "placement.json"))
    rebuilt = (work / "payload.bin").read_bytes()
    entry = p.by_name["vsum"].entry
    got = []
    for payload, al in ((t.image().payload(), None), (rebuilt, aliases)):
        m = Machine(Layout(t, t.image()), payload, al)
        got.append(m.call(entry, 5, 1, 2, 3, 4, 5, max_instructions=10_000).ret)    # 4 and 5 on the stack
    assert got == [15, 15], got


DECIDER_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global fails, f, use
    .type fails, %function
    .thumb_func
fails:  movs    r0, #0          @ a callee that always fails (newlib's _read stub)
        subs    r0, #1
        bx      lr
    .size fails, . - fails
    .type f, %function
    .thumb_func
f:      push    {r4, lr}
        bl      fails
        adds    r3, r0, #1
        beq     1f
        movs    r0, #7          @ the path for a callee that succeeds
        pop     {r4, pc}
1:      movs    r0, #9
        pop     {r4, pc}
    .size f, . - f
    .type use, %function
    .thumb_func
use:    push    {r4, lr}
        bl      f
        pop     {r4, pc}
    .size use, . - use
"""


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                    reason="needs arm-none-eabi-as and -gcc")
def test_a_callee_that_answers_one_way_is_stubbed_when_the_search_stalls(tmp_path):
    """A real callee answers one way: newlib's _read stub always fails, so _read_r's code for a read
    that succeeded went uncovered on the benchmark's distribution builds (and _write_r, _close_r,
    _lseek_r, __sread). When the search stalls short of every instruction, the callee whose call
    stands in front of what is left is stubbed in later inputs (generator 31), its result an input."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    (tmp_path / "t.s").write_text(DECIDER_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "use", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mthumb", "-mcpu=cortex-m3", "-Os"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    lines = []
    assert lift.check(t, t.image(), ["f"], out=lines.append, jobs=1) == ["f"], "\n".join(lines)


VA_LIST_RV_C = """
#include <stdarg.h>
int vsum(int n, ...)
{
    va_list ap;
    int s = 0;
    va_start(ap, n);
    while (n-- > 0)
        s += va_arg(ap, int);
    va_end(ap);
    return s;
}
int many(int a, int b, int c, int d, int e, int f, int g, int h, int i, int j)
{
    return a - b + c - d + e - f + g - h + i * 3 - j * 5;      /* i and j come on the stack */
}
int use(void)
{
    return vsum(9, 1, 2, 3, 4, 5, 6, 7, 8, 9) + many(1, 2, 3, 4, 5, 6, 7, 8, 9, 10);
}
"""

RV_TARGET = """
[target]
name = "rv-wrap"

[arch]
name = "riscv"
isa = "rv32"
extensions = "imac"

[memory]
regions = [
    {name = "flash", base = 0x20000000, size = 0x100000, kind = "rom"},
    {name = "ram", base = 0x80000000, size = 0x10000},
    {name = "scratch", base = 0x80100000, size = 0x20000},
]
io = [[0x10000000, 0x10010000]]
stack = {top = 0x80010000, size = 0x2000}
sentinel = 0x1fff0000
scratch = {base = 0x80100000, size = 0x20000}

[image.main]
original = "original/t.bin"
base = 0x20000000
limit = 0x20020000

[compiler]
cross = "riscv64-elf-"
cflags = ["-march=rv32imac_zicsr", "-mabi=ilp32", "-Os"]
"""


@needs_riscv_gcc
def test_risc_v_wraps_a_va_list_walk_and_stack_arguments(tmp_path):
    """RISC-V as ARM (test_a_va_list_walks_on_into_the_stack_arguments): a variadic function saves
    a1-a7 next to its stack arguments and its va_list walks from the one into the other (FreeRTOS's
    printf and sprintf on the RISC-V builds), and a function with ten arguments reads the last two
    on the stack. Both are wrapped: a naked entry reserves the original's frame on the real stack
    and calls the body with the entry's stack pointer as its ninth argument. Before, the first was
    refused for the address in its save area and the second for its stack arguments."""
    import subprocess
    from dread import elfimport, regflow
    from dread.emu import Layout, Machine
    from dread.overlay import load_placement
    (tmp_path / "t.c").write_text(VA_LIST_RV_C)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run([RISCV_CROSS + "gcc", "-march=" + RISCV_MARCH, "-mabi=ilp32", "-Os", "-ffreestanding",
                    "-ffunction-sections", "-c", "-o", obj, str(tmp_path / "t.c")], check=True)
    subprocess.run([RISCV_CROSS + "ld", "-m", "elf32lriscv", "-Ttext=0x20000000", "-e", "use", "-o", elf, obj],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml(RV_TARGET))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    p = Program(t, t.image())
    lifter = lift.make_lifter(p, regflow.analyse(p))
    for name in ("vsum", "many"):
        text = lifter.lift(p.by_name[name]).text
        assert "__attribute__((naked)) void %s(void)" % name in text and "u32 %s_dread_body(" % name in text, text
    lines = []
    assert sorted(lift.check(t, t.image(), ["vsum", "many"], out=lines.append, jobs=1)) == ["many", "vsum"], \
        "\n".join(lines)
    work = tmp_path / "build" / "lift-check" / "main"
    aliases, _ = load_placement(str(work / "placement.json"))
    rebuilt = (work / "payload.bin").read_bytes()
    got = []
    for payload, al in ((t.image().payload(), None), (rebuilt, aliases)):
        m = Machine(Layout(t, t.image()), payload, al)
        got.append((m.call(p.by_name["vsum"].entry, 9, 1, 2, 3, 4, 5, 6, 7, 8, 9, max_instructions=10_000).ret,
                    m.call(p.by_name["many"].entry, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, max_instructions=10_000).ret))
    assert got[0] == got[1] == (45, (1 - 2 + 3 - 4 + 5 - 6 + 7 - 8 + 27 - 50) & 0xFFFFFFFF), got


DECIDER_SOLVE_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global ones, g, useg
    .type ones, %function
    .thumb_func
ones:   movs    r0, #1          @ answers one way
        bx      lr
    .size ones, . - ones
    .type g, %function
    .thumb_func
g:      push    {r4, lr}
        bl      ones
        cmp     r0, #3
        bne     1f
        movs    r0, #7          @ only where ones returns 3: no input of g's makes it
        pop     {r4, pc}
1:      movs    r0, #9
        pop     {r4, pc}
    .size g, . - g
    .type useg, %function
    .thumb_func
useg:   push    {r4, lr}
        bl      g
        pop     {r4, pc}
    .size useg, . - useg
"""


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-gcc")),
                    reason="needs arm-none-eabi-as and -gcc")
def test_a_stubbed_callees_result_is_solved_for(tmp_path, monkeypatch):
    """Generator 32: with a callee that answers one way stubbed, the compare-guided stage runs again,
    the stub's result an input it solves for (g's path needs ones to return 3, which no input of g
    makes it). The random inputs after it are turned off here: they would find the 3 too, a constant
    of the code, which a value read from memory would not give them."""
    import subprocess
    from test_bench import TARGET
    from dread import autocase, elfimport
    monkeypatch.setattr(autocase, "DECIDER_RUNS", 0)
    (tmp_path / "t.s").write_text(DECIDER_SOLVE_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "useg", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         'limit = 0x08010000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n'
                                         'cflags = ["-mthumb", "-mcpu=cortex-m3", "-Os"]\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"),
                           "--write-startup", elf]) == 0
    t = Target.load(str(tmp_path))
    lines = []
    assert lift.check(t, t.image(), ["g"], out=lines.append, jobs=1) == ["g"], "\n".join(lines)
