"""dread regflow on gizmo: what callers read after a call, what functions read at their entry, and
that the runner and the integration run compare the registers it finds.

Both gizmo builds have one register a compiler kept across a call: uart_puts keeps its string
pointer in r1 across bl uart_putc (GCC -fipa-ra, the two are in one file). On a32 the remainder of
__aeabi_uidivmod comes back in r1 too. Anything else found would be a false alarm."""
import pytest

from dread import build, integration, regflow, switches
from dread.program import Program
from conftest import have
from test_runner import run_all, src_copy

SIGNATURES = {           # registers each function reads at its entry, from the C (beyond 4: the stack)
    "clamp": "r0 r1 r2", "checksum": "r0 r1", "parse_uint": "r0 r1", "parse_kv": "r0 r1 r2 r3",
    "sum6": "r0 r1 r2 r3", "mul64": "r0 r1", "udiv_var": "r0 r1", "fmt_hex": "r0 r1 r2", "fmt_dec": "r0 r1",
    "mode_name": "r0", "ring_put": "r0 r1", "ring_get": "r0", "dispatch": "r0 r1", "uart_putc": "r0",
    "uart_puts": "r0", "uart_getc": "", "print_status": "", "main": "", "apply_config": "r0",
    "strcmp": "r0 r1", "memcpy": "r0 r1 r2", "strncpy": "r0 r1 r2", "delay": "r0", "timer_irq": "",
    "script_run": "r0 r1", "ram_crc": "r0 r1", "__ram_crc_veneer": "r0 r1",
}


def test_kept_registers(target, variant):
    flow = regflow.load(target, target.image())
    first = target.arch.arg_regs(1)[0]
    extra = {name for name in flow.kept if [r for r in flow.kept_regs(name) if r != first]}
    if variant == "rv32":       # clang inlined uart_putc into uart_puts, the caller that read r1 on ARM
        assert extra == set()
    else:
        assert extra == ({"uart_putc", "__aeabi_uidivmod"} if variant == "a32" else {"uart_putc"})
        assert flow.kept_regs("uart_putc") == ["r1"]
        assert [caller for caller, _ in flow.sites_of("uart_putc", "r1")] == ["uart_puts"]
    assert first in flow.kept_regs("parse_uint") and first not in flow.kept_regs("ring_put")


def test_kept_registers_are_classified(target, variant):
    if variant == "rv32":
        pytest.skip("no register is kept beyond a0 on rv32 (test_kept_registers)")
    flow = regflow.load(target, target.image())
    assert flow.always("uart_putc", "r1") and flow.preserved_regs("uart_putc") == ["r1"]
    assert flow.why("uart_putc", "r1").endswith("the original leaves it unchanged")
    if "__aeabi_uidivmod" in flow.kept:
        assert flow.always("__aeabi_uidivmod", "r1") and "r1" not in flow.preserved_regs("__aeabi_uidivmod")


def jumps(program, name):
    fn = program.by_name[name]
    return fn, [a for a in fn.insns if program.arch.flow(program.insn(a))[0] == "ijump"]


def test_indirect_jumps_are_resolved(target, variant):
    """script_run's switch (a byte table and add pc on a32, tbb on m3, a table of addresses behind
    andi a0, a0, 7 and jr on rv32), dispatch's table of function pointers (tail calls) and the veneer
    to the RAM function (clang builds neither: dispatch's handlers are inlined, the far call is
    auipc + jalr)."""
    program = Program(target)
    fn, (jump,) = jumps(program, "script_run")
    inside, outside = switches.resolve(program, fn, jump)
    assert len(inside) == (8 if variant == "rv32" else 7) and not outside and set(inside) <= set(fn.insns)
    if variant == "rv32":
        assert jumps(program, "dispatch")[1] == [] and "__ram_crc_veneer" not in program.by_name
        return
    if variant in ("m3", "m4"):     # the emulated dispatch agrees with decoding the tbb table
        decoded = program.arch.switch_targets(program.insn(jump), program, lambda a: (a & ~1) in set(fn.insns))
        assert sorted(decoded) == inside
    fn, (jump,) = jumps(program, "dispatch")
    assert switches.resolve(program, fn, jump) == ([], sorted(program.by_name[n].entry for n in
                                                              ("cmd_led", "cmd_beep", "cmd_mode", "cmd_volume")))
    fn, (jump,) = jumps(program, "__ram_crc_veneer")
    assert switches.resolve(program, fn, jump) == ([], [program.by_name["ram_crc"].entry])


def test_entry_reads_match_the_signatures(target, variant):
    flow = regflow.load(target, target.image())
    want = dict(SIGNATURES)
    if variant == "rv32":       # a0-a7 for the arguments: sum6 takes all six in registers; no veneer
        want = {name: " ".join("a" + r[1:] for r in regs.split()) for name, regs in want.items()}
        want["sum6"] = "a0 a1 a2 a3 a4 a5"
        del want["__ram_crc_veneer"]
    got = {name: " ".join(flow.read_regs(name)) for name in want}
    assert got == want


def test_cache_follows_the_tables(target, tmp_path, monkeypatch):
    image = target.image()
    first = regflow.cache_key(target, image)
    monkeypatch.setattr(target.abi, "callee_saved", target.abi.callee_saved[:-1])
    assert regflow.cache_key(target, image) != first


CLOBBER = ("HWREG(UART_BASE) = (u8)c;", 'HWREG(UART_BASE) = (u8)c;\n    __asm__ volatile("mov r1, #0" ::: "r1");')


@pytest.mark.slow
@pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_clobbered_kept_register_fails(target, variant, tmp_path, capsys, monkeypatch):
    """A uart_putc that ends with r1 = 0: everything its own tests compared before agrees, but
    uart_puts reads r1 after the call."""
    if variant == "rv32":
        pytest.skip("on rv32 uart_puts has uart_putc inlined: no caller reads a register after it")
    src = src_copy(tmp_path, variant, CLOBBER)
    payload = build.build(target, target.image(), src=str(src), out=str(tmp_path / "recon"), quiet=True)
    failed, uncovered, text = run_all(target, payload, names=["uart_putc"])
    assert failed and "r1 after the return" in text and "uart_puts" in text, text
    # an argument in kept.tsv takes r1 out of the comparison, and the result says so
    kept = tmp_path / "kept.tsv"
    kept.write_text("uart_putc\tr1\tthe planted change, argued for the test\n")
    monkeypatch.setattr(regflow, "argued_path", lambda image: str(kept))
    failed, uncovered, text = run_all(target, payload, names=["uart_putc"])
    assert not failed and "r1 at the return not compared (kept.tsv): the planted change" in text, text
    monkeypatch.undo()
    # through the caller it shows only for strings of two or more characters: all generated inputs,
    # while uart_puts is the original's code; lifted, it keeps its r1 across the call itself (the C's
    # variable: uart_putc never writes r1 in the original) and the clobber cannot reach it
    assert integration.main(["--target", target.root, "--recon", payload, "--cases", "40", "uart_puts"]) == 0
    capsys.readouterr()
    names = [n for n in build.read_names(target.image().table("lifted")) if n != "uart_puts"]
    original = build.build(target, target.image(), src=str(src), out=str(tmp_path / "recon-original-caller"),
                           quiet=True, lifted=names)
    assert integration.main(["--target", target.root, "--recon", original, "--cases", "40", "uart_puts"]) == 1
    assert "DIFF uart_puts" in capsys.readouterr().out


def test_switch_fallback_without_resolution(target, monkeypatch):
    """With neither the emulated dispatch nor a known table shape, an indirect jump goes on at the
    roots of the code no direct path reaches (script_run's cases), and the analysis still finds the
    same registers."""
    from dread import switches
    from dread.program import Program

    def extra(flow):
        return {n for n in flow.kept if [r for r in flow.kept_regs(n) if r != "r0"]}
    reference = extra(regflow.Analysis(Program(target)).solve().result())
    monkeypatch.setattr(switches, "resolve", lambda program, fn, address: None)
    program = Program(target)
    monkeypatch.setattr(program.arch, "switch_targets", lambda insn, prog, inside: None)
    analysis = regflow.Analysis(program).solve()
    code = analysis.rows["script_run"]
    jump = next(i for i, row in enumerate(code) if row.kind == "ijump")
    assert len(analysis.graphs["script_run"][0][jump]) >= 2
    assert extra(analysis.result()) == reference


@pytest.mark.skipif(not (have("clang") and have("ld.lld")), reason="needs clang and ld.lld")
def test_a_variadic_spill_is_no_use(tmp_path):
    """clang's variadic functions keep r1-r3 next to their stack arguments with stores (add r0, sp,
    #12; stm r0!, {r1, r2, r3}) where GCC pushes them; counted as uses, they made every caller of an
    assertion handler that calls printf look as if it read r3 (FreeRTOS's clang builds: most of the
    lifter's refusals). A store of a register nothing has written yet where sp points, in the entry's
    straight run, is a spill like a push."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.c").write_text(
        "#include <stdarg.h>\n"
        "int sink(int);\n"
        "int sum(int n, ...) { va_list ap; va_start(ap, n); int s = 0; while (n--) s += va_arg(ap, int);"
        " va_end(ap); return sink(s); }\n"
        "int sink(int x) { return x + 1; }\n"
        "int caller(int a) { return sum(1, a); }\n")
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["clang", "--target=thumbv7m-none-eabi", "-mcpu=cortex-m3", "-O2", "-ffreestanding", "-c",
                    "-ffunction-sections", "-o", obj, str(tmp_path / "t.c")], check=True)
    (tmp_path / "t.ld").write_text("SECTIONS { . = 0x08000000; .text : { *(.text*) } }\n")
    subprocess.run(["ld.lld", "-T", str(tmp_path / "t.ld"), "-e", "caller", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\nkind = "clang"\ntriple = "thumbv7m-none-eabi"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    target = Target.load(str(tmp_path))
    program = Program(target)
    stores = [i for i in program.instructions(program.by_name["sum"]) if i.mnemonic.startswith(("stm", "str"))]
    assert stores, "clang spilled with pushes this time: the test shows nothing"
    flow = regflow.analyse(program)
    assert flow.read_regs("sum") == ["r0"], flow.read_regs("sum")


@pytest.mark.skipif(not have("riscv64-elf-gcc"), reason="needs riscv64-elf-gcc")
def test_an_ecall_reads_nothing(tmp_path):
    """FreeRTOS's RISC-V port yields with ecall, and its handler saves every register. Counted as a
    system call that reads a0-a7, the ecall made every callee in front of a yield look as if its
    callers read a1-a7 after it (most of the lifter's refusals on RISC-V)."""
    import subprocess
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.c").write_text("__attribute__((noinline)) int helper(int x) { return x + 1; }\n"
                                  "int yield_after(int x) { int r = helper(x); __asm__ volatile (\"ecall\"); return r; }\n")
    elf = str(tmp_path / "t.elf")
    (tmp_path / "t.ld").write_text("SECTIONS { . = 0x80000000; .text : { *(.text*) } }\n")
    subprocess.run(["riscv64-elf-gcc", "-march=rv32imac", "-mabi=ilp32", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "yield_after", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text('''[target]
name = "ecall"
[arch]
name = "riscv"
isa = "rv32"
extensions = "imac"
[memory]
regions = [{name = "rom", base = 0x80000000, size = 0x10000, kind = "rom"}]
stack = {top = 0x80090000, size = 0x1000}
sentinel = 0x7fff0000
[image.main]
original = "original/t.bin"
base = 0x80000000
[compiler]
cross = "riscv64-elf-"
cflags = ["-march=rv32imac", "-mabi=ilp32", "-Os"]
''')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    target = Target.load(str(tmp_path))
    flow = regflow.analyse(Program(target))
    assert not set(flow.kept_regs("helper")) - {"a0"}, flow.kept_regs("helper")      # (a0..a7 before)


def _rv_flow(tmp_path, files, cflags=("-O2",)):
    """regflow of a small RV32 program built from {name: text} (C or assembly)."""
    import subprocess
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    (tmp_path / "t.ld").write_text("SECTIONS { . = 0x80000000; .text : { *(.text*) } }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["riscv64-elf-gcc", "-march=rv32imac_zicsr", "-mabi=ilp32", "-nostdlib", "-ffunction-sections",
                    *cflags, "-T", str(tmp_path / "t.ld"), "-Wl,-e,0", "-o", elf] + [str(tmp_path / n) for n in files],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text('''[target]
name = "flow"
[arch]
name = "riscv"
isa = "rv32"
extensions = "imac"
[memory]
regions = [{name = "rom", base = 0x80000000, size = 0x10000, kind = "rom"}]
stack = {top = 0x80090000, size = 0x1000}
sentinel = 0x7fff0000
[image.main]
original = "original/t.bin"
base = 0x80000000
[compiler]
cross = "riscv64-elf-"
cflags = ["-march=rv32imac_zicsr", "-mabi=ilp32"]
''')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    return regflow.analyse(Program(Target.load(str(tmp_path))))


@pytest.mark.skipif(not have("riscv64-elf-gcc"), reason="needs riscv64-elf-gcc")
def test_arguments_kept_in_the_frame_are_read(tmp_path):
    """-O0 keeps every argument in its frame at the entry and loads it later: a use, not a variadic
    save area (the rule for those made every -O0 function read nothing)."""
    flow = _rv_flow(tmp_path, {"t.c": "int add(int a, int b) { return a + b; }\n"}, ("-O0",))
    assert flow.read_regs("add") == ["a0", "a1"], flow.read_regs("add")


@pytest.mark.skipif(not have("riscv64-elf-gcc"), reason="needs riscv64-elf-gcc")
def test_a_variadic_save_area_beside_a_named_argument(tmp_path):
    """sprintf keeps its unnamed argument registers and its first argument in its frame (the first
    one's address goes to a callee): the save area is no use, the first argument is."""
    flow = _rv_flow(tmp_path, {"t.c": '''#include <stdarg.h>
__attribute__((noinline)) int sink(char **out, const char *fmt, va_list ap) { return **out + *fmt + va_arg(ap, int); }
int fmt2(char *buf, const char *fmt, ...) { va_list ap; va_start(ap, fmt); int r = sink(&buf, fmt, ap); va_end(ap); return r; }
'''})
    assert flow.read_regs("fmt2") == ["a0", "a1"], flow.read_regs("fmt2")


@pytest.mark.skipif(not have("riscv64-elf-gcc"), reason="needs riscv64-elf-gcc")
def test_a_save_around_a_call_of_a_register_never_set(tmp_path):
    """GCC saves a register it believes live around a call also where nothing set it (FreeRTOS's
    tick on RISC-V: t1 around its assertion call, and 135 functions looked as if their callers read
    t1). Such a save and its restore are neither a read nor a write; an argument register so only in
    a function that takes no frame address (g(&a) keeps an argument there for the callee)."""
    flow = _rv_flow(tmp_path, {"t.S": '''
    .text
    .globl g, f, h, k
    .type g, @function
g:  li t1, 7
    li a3, 9
    ret
    .size g, .-g
    .type f, @function
f:  addi sp, sp, -16
    sw ra, 12(sp)
    beqz a0, 1f
    sw t1, 8(sp)
    sw a3, 4(sp)
    call g
    lw t1, 8(sp)
    lw a3, 4(sp)
1:  lw ra, 12(sp)
    addi sp, sp, 16
    ret
    .size f, .-f
    .type h, @function
h:  addi sp, sp, -16
    sw ra, 12(sp)
    addi a1, sp, 4
    sw a3, 8(sp)
    call g
    lw a3, 8(sp)
    lw ra, 12(sp)
    addi sp, sp, 16
    ret
    .size h, .-h
    .type k, @function
k:  addi sp, sp, -16
    sw ra, 12(sp)
    li t1, 3
    li a0, 1
    call f
    mv a0, t1
    lw ra, 12(sp)
    addi sp, sp, 16
    ret
    .size k, .-k
'''})
    assert flow.read_regs("f") == ["a0"], flow.read_regs("f")
    assert "a3" in flow.read_regs("h"), flow.read_regs("h")      # it takes a frame address
    assert flow.read_regs("k") == [], flow.read_regs("k")        # f leaves t1 as it found it


def test_arm_frame_slots():
    """The ARM layer's frame slots: single-register str/ldr at sp plus a constant; writeback forms
    (pushes and pops) and loads of pc are none."""
    from dread.arch.arm import Arm
    a = Arm(isa="t32", profile="m", cpu="cortex-m3")
    slot = lambda code: a.frame_slot(a.decode(code, 0x1000, "t32"))
    assert slot(b"\xcd\xf8\x08\xc0") == ("store", "r12", 8) and slot(b"\xdd\xf8\x08\xc0") == ("load", "r12", 8)
    assert slot(b"\x02\x93") == ("store", "r3", 8) and slot(b"\x02\x9b") == ("load", "r3", 8)
    assert slot(b"\x4d\xf8\x04\x3d") is None and slot(b"\x5d\xf8\x04\xfb") is None


def test_float_arguments_and_results_with_hard_float():
    """With hard float the analysis follows s0-s15 like the scratch registers: gizmo-m4's lp_filter
    reads s0-s2 at its entry, to_millivolts s0; the M3's build has no such registers to follow."""
    from dread.program import Program
    from dread.target import Target
    from conftest import gizmo_dir
    target = Target.load(gizmo_dir("m4"))
    flow = regflow.load(target, target.image(), Program(target, target.image()))
    assert {"s0", "s1", "s2"} <= set(flow.read_regs("lp_filter"))
    assert "s0" in flow.read_regs("to_millivolts") and "s1" not in flow.read_regs("to_millivolts")
