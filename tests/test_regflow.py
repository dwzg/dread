"""dread regflow on gizmo: what callers read after a call, what functions read at their entry, and
that the runner and the integration run compare the registers it finds.

Both gizmo builds have one register a compiler kept across a call: uart_puts keeps its string
pointer in r1 across bl uart_putc (GCC -fipa-ra, the two are in one file). On a32 the remainder of
__aeabi_uidivmod comes back in r1 too. Anything else found would be a false alarm."""
import shutil

import pytest

from dread import build, integration, regflow, switches
from dread.program import Program
from conftest import NOIPA_DEF, have, needs_riscv_gcc, RISCV_CROSS, RISCV_MARCH, riscv_toml
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
        # (__aeabi_ldiv0, libgcc's bx lr behind the division helpers, returns r0:r1 by the run-time
        # ABI: regflow 18's abi_results, whatever the image's callers read of it)
        assert extra == ({"uart_putc", "__aeabi_uidivmod", "__aeabi_ldiv0"} if variant == "a32" else {"uart_putc"})
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


def test_the_cache_key_holds_the_code_this_process_runs(target, tmp_path, monkeypatch):
    """The key holds the analysis's sources as this process read them when it imported dread.regflow,
    not as the files are when it stores: a suite that had imported the old code stored its analysis
    under the key of an edit made meanwhile, and the runs after it took that analysis for the new code
    (2026-10-24). Here every source the key holds is 'edited' after the import; the key stays."""
    import sys
    image = target.image()
    first = regflow.cache_key(target, image)
    edited = tmp_path / "edited.py"
    edited.write_text("# an edit made after the import\n")
    for name in ("dread.regflow", "dread.switches", "dread.program", "dread.arch", type(image.arch).__module__):
        monkeypatch.setattr(sys.modules[name], "__file__", str(edited))
    assert regflow.cache_key(target, image) == first


CLOBBER =("HWREG(UART_BASE) = (u8)c;", 'HWREG(UART_BASE) = (u8)c;\n    __asm__ volatile("mov r1, #0" ::: "r1");')


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
    # an argument that names the instructions it covers applies while they are all where r1 ends up
    # (regflow.argued_kept), and lapses where they are not: r1 is compared again, and the result says why
    from dread.program import Program
    program = Program(target)
    ends = regflow.ends_of(regflow.Analysis(program).solve(compared=True), regflow.load(target, target.image()),
                           "uart_putc", "r1")
    assert ends and all(fn == "uart_puts" for fn, _, _ in ends.values()), ends
    kept.write_text("uart_putc\tr1\tthe planted change, argued for the test\t%s\n" % " ".join("%x" % a for a in ends))
    failed, uncovered, text = run_all(target, payload, names=["uart_putc"])
    assert not failed and "r1 at the return not compared (kept.tsv)" in text, text
    entry = program.by_name["uart_putc"].entry
    kept.write_text("uart_putc\tr1\tthe planted change, argued for the test\t%x\n" % entry)
    failed, uncovered, text = run_all(target, payload, names=["uart_putc"])
    assert failed and "r1 after the return" in text and "kept.tsv's argument does not cover" in text, text
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
@pytest.mark.parametrize("fp", ["", "-fno-omit-frame-pointer"])
def test_a_variadic_spill_is_no_use(tmp_path, fp):
    """clang's variadic functions keep r1-r3 next to their stack arguments with stores (add r0, sp,
    #12; stm r0!, {r1, r2, r3}) where GCC pushes them; counted as uses, they made every caller of an
    assertion handler that calls printf look as if it read r3 (FreeRTOS's clang builds: most of the
    lifter's refusals). A store of a register nothing has written yet where sp points, in the entry's
    straight run, is a spill like a push. With a frame pointer the store goes through a copy of a
    copy of sp (mov r7, sp; add.w r12, r7, #8; stm.w r12, {r1, r2, r3}): clang 18, the CI's, keeps one
    at -O2 for this target, and the test failed there until regflow 15 followed such copies."""
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
                    "-ffunction-sections", "-o", obj, str(tmp_path / "t.c")] + ([fp] if fp else []), check=True)
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


PREDICATED_EDGES_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global f, g, h, caller
    .type f, %function
    .thumb_func
f:                          @ newlib's strcmp (Ubuntu's, Debian's): r3 loaded under eq, the branch on eq
    ands    ip, r0, #3
    ldr     r2, [r0]
    it      eq
    ldreq   r3, [r1]
    beq     1f
    movs    r3, #7
1:  subs    r0, r2, r3
    bx      lr
    .type g, %function
    .thumb_func
g:                          @ the fall-through edge: r3 set under ne, read where ne held
    cmp     r0, #0
    it      ne
    movne   r3, #5
    beq     2f
    adds    r0, r3, #1
    bx      lr
2:  movs    r0, #0
    bx      lr
    .type h, %function
    .thumb_func
h:                          @ against: r3 set under eq, the branch taken on ne reads the caller's r3
    cmp     r0, #0
    it      eq
    moveq   r3, #5
    bne     3f
    adds    r0, r3, #1
    bx      lr
3:  adds    r0, r3, #2
    bx      lr
    .type caller, %function
    .thumb_func
caller:
    push    {r4, lr}
    bl      f
    bl      g
    bl      h
    pop     {r4, pc}
"""


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-ld")),
                    reason="needs arm-none-eabi binutils")
def test_a_branch_edge_carries_the_writes_made_under_its_condition(tmp_path):
    """regflow 16: on the taken edge of a branch on eq, what was written under eq since the flags
    changed is written (and on its fall-through edge what was written under ne). Without it newlib's
    strcmp of Ubuntu and Debian seemed to read r3, and every function whose caller's r3 reached it was
    refused (seven a build on the benchmark's older toolchains)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.s").write_text(PREDICATED_EDGES_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "caller", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    flow = regflow.analyse(Program(Target.load(str(tmp_path))))
    assert flow.read_regs("f") == ["r0", "r1"], flow.read_regs("f")
    assert flow.read_regs("g") == ["r0"], flow.read_regs("g")
    assert "r3" in flow.read_regs("h"), flow.read_regs("h")


def test_a_variadic_save_area_behind_sub_sp(tmp_path):
    """clang -Os opens a variadic function with two named arguments by moving sp and storing the rest
    (sub sp, #8; ...; strd r2, r3, [sp, #16]: FreeRTOS's demo sprintf). The move took sp out of the
    frame stores' bases, so the save area went unseen, sprintf seemed to read r3, and 18 callers that
    pass three arguments were refused for reading the r3 a memset before had left."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.c").write_text(
        "#include <stdarg.h>\n"
        "__attribute__((noinline)) int sink(char **out, const char *f, va_list ap) { return **out + *f + va_arg(ap, int); }\n"
        "int fmt(char *out, const char *f, ...) { va_list ap; va_start(ap, f); int r = sink(&out, f, ap);"
        " va_end(ap); return r; }\n"
        "int caller(char *b) { return fmt(b, \"%d\", 3); }\n")
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["clang", "--target=thumbv7m-none-eabi", "-mcpu=cortex-m3", "-Os", "-ffreestanding", "-c",
                    "-ffunction-sections", "-o", obj, str(tmp_path / "t.c")], check=True)
    (tmp_path / "t.ld").write_text("SECTIONS { . = 0x08000000; .text : { *(.text*) } }\n")
    subprocess.run(["ld.lld", "-T", str(tmp_path / "t.ld"), "-e", "caller", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\nkind = "clang"\ntriple = "thumbv7m-none-eabi"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    program = Program(Target.load(str(tmp_path)))
    shape = [(i.mnemonic, i.op_str) for i in program.instructions(program.by_name["fmt"])][:5]
    assert shape[0][0] == "sub" and any(m.startswith("str") for m, _ in shape), shape   # (else the test shows nothing)
    flow = regflow.analyse(program)
    assert flow.read_regs("fmt") == ["r0", "r1"], flow.read_regs("fmt")


@needs_riscv_gcc
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
    subprocess.run([RISCV_CROSS + "gcc", "-march=rv32imac", "-mabi=ilp32", "-Os", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "yield_after", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml('''[target]
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
'''))
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
    subprocess.run([RISCV_CROSS + "gcc", "-march=" + RISCV_MARCH, "-mabi=ilp32", "-nostdlib", "-ffunction-sections",
                    *cflags, "-T", str(tmp_path / "t.ld"), "-Wl,-e,0", "-o", elf] + [str(tmp_path / n) for n in files],
                   check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(riscv_toml('''[target]
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
'''))
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    return regflow.analyse(Program(Target.load(str(tmp_path))))


@needs_riscv_gcc
def test_arguments_kept_in_the_frame_are_read(tmp_path):
    """-O0 keeps every argument in its frame at the entry and loads it later: a use, not a variadic
    save area (the rule for those made every -O0 function read nothing)."""
    flow = _rv_flow(tmp_path, {"t.c": "int add(int a, int b) { return a + b; }\n"}, ("-O0",))
    assert flow.read_regs("add") == ["a0", "a1"], flow.read_regs("add")


@needs_riscv_gcc
def test_a_variadic_save_area_beside_a_named_argument(tmp_path):
    """sprintf keeps its unnamed argument registers and its first argument in its frame (the first
    one's address goes to a callee): the save area is no use, the first argument is."""
    flow = _rv_flow(tmp_path, {"t.c": '''#include <stdarg.h>
__attribute__((noinline)) int sink(char **out, const char *fmt, va_list ap) { return **out + *fmt + va_arg(ap, int); }
int fmt2(char *buf, const char *fmt, ...) { va_list ap; va_start(ap, fmt); int r = sink(&buf, fmt, ap); va_end(ap); return r; }
'''})
    assert flow.read_regs("fmt2") == ["a0", "a1"], flow.read_regs("fmt2")


@needs_riscv_gcc
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
    assert flow.sites_of("g", "t1") == [], flow.sites_of("g", "t1")     # f puts t1 back itself: not g's


@needs_riscv_gcc
def test_a_restored_register_read_after_the_call_is_read_at_the_entry(tmp_path):
    """A save and restore around a call is a copy through the slot (regflow 19): newlib's _free_r
    spills r0 around __malloc_lock, which writes r0 (the lock's address for the lock routine), and
    stores errno through the restored r0 after; regflow had taken the pair for neither a read nor a
    write and the call for the end of r0, so _free_r did not read r0 (found by dread callconv). A
    restored register nothing reads afterwards is still no read (spare)."""
    flow = _rv_flow(tmp_path, {"t.S": '''
    .text
    .globl lockfn, f, spare
    .type lockfn, @function
lockfn:
    li a0, 5
    ret
    .size lockfn, .-lockfn
    .type f, @function
f:  addi sp, sp, -16
    sw ra, 12(sp)
    sw a0, 8(sp)
    call lockfn
    lw a0, 8(sp)
    li a5, 12
    sw a5, 0(a0)
    lw ra, 12(sp)
    addi sp, sp, 16
    ret
    .size f, .-f
    .type spare, @function
spare:
    addi sp, sp, -16
    sw ra, 12(sp)
    sw t1, 8(sp)
    call lockfn
    lw t1, 8(sp)
    li a0, 0
    lw ra, 12(sp)
    addi sp, sp, 16
    ret
    .size spare, .-spare
'''})
    assert "a0" in flow.read_regs("f"), flow.read_regs("f")
    assert "t1" not in flow.read_regs("spare"), flow.read_regs("spare")


@needs_riscv_gcc
def test_a_call_through_another_link_register_reads_none_of_it(tmp_path):
    """GCC's millicode is entered with jal t0, __riscv_save_N and returns through t0 (picolibc's
    _init_tls): the callee reads t0, but the call wrote it, so the caller does not read t0 (regflow
    20; before, every function on a path to _init_tls looked as if it read t0)."""
    flow = _rv_flow(tmp_path, {"t.S": '''
    .text
    .globl millicode, f, g
    .type millicode, @function
millicode:
    mv t1, t0
    jr t0
    .size millicode, .-millicode
    .type f, @function
f:  jal t0, millicode
    li a0, 5
    ret
    .size f, .-f
    .type g, @function
g:  addi sp, sp, -16
    sw ra, 12(sp)
    call f
    lw ra, 12(sp)
    addi sp, sp, 16
    ret
    .size g, .-g
'''})
    assert "t0" in flow.read_regs("millicode"), flow.read_regs("millicode")
    assert "t0" not in flow.read_regs("f") and "t0" not in flow.read_regs("g"), (flow.read_regs("f"), flow.read_regs("g"))


@needs_riscv_gcc
def test_a_dead_copy_into_a_callee_saved_register_reads_nothing(tmp_path):
    """regflow 23: GCC 10 on RISC-V copies a register nothing set into s0 and never reads s0 again
    before restoring it (FreeRTOS's xTaskCheckForTimeOut: c.mv s0, a4): no read of a4, also with a
    call behind it whose callee reads none of its caller's callee-saved registers (f). A copy into a
    register the callee does read from its caller (a compiler's shared code reads its caller's r6)
    stays a read (g)."""
    flow = _rv_flow(tmp_path, {"t.S": '''
    .text
    .globl helper, reader, f, g
    .type helper, @function
helper:
    addi sp, sp, -16
    sw s0, 8(sp)
    li s0, 3
    mv a0, s0
    lw s0, 8(sp)
    addi sp, sp, 16
    ret
    .size helper, .-helper
    .type reader, @function
reader:
    mv a0, s1
    ret
    .size reader, .-reader
    .type f, @function
f:  addi sp, sp, -16
    sw ra, 12(sp)
    sw s0, 8(sp)
    mv s0, a4
    call helper
    lw s0, 8(sp)
    lw ra, 12(sp)
    addi sp, sp, 16
    ret
    .size f, .-f
    .type g, @function
g:  addi sp, sp, -16
    sw ra, 12(sp)
    sw s1, 8(sp)
    mv s1, a3
    call reader
    lw s1, 8(sp)
    lw ra, 12(sp)
    addi sp, sp, 16
    ret
    .size g, .-g
'''})
    assert "a4" not in flow.read_regs("f"), flow.read_regs("f")
    assert "a3" in flow.read_regs("g"), flow.read_regs("g")


@needs_riscv_gcc
def test_a_register_saved_around_a_call_is_not_read_behind_it(tmp_path):
    """GCC -Os saves an argument it needs after an assertion call around that call (FreeRTOS on
    RISC-V: a2 around vAssertCalled). The restore puts back the entry's value, so the register is the
    caller's own behind the call, not what the callee left: no call site reads it, and the caller
    lifts (23 such functions were refused for reading a2 behind vAssertCalled)."""
    from dread import lift
    from dread.program import Program
    from dread.target import Target
    flow = _rv_flow(tmp_path, {"t.c": NOIPA_DEF + '''
NOIPA void fail(const char *file, int line) { while (*file != line) { } }
unsigned clear(unsigned *p, unsigned i, unsigned bits) { if (i > 2) fail("x", 1); return p[i] & ~bits; }
'''}, ("-Os",))
    assert flow.read_regs("clear") == ["a0", "a1", "a2"], flow.read_regs("clear")
    assert flow.kept_regs("fail") == [], flow.kept_regs("fail")
    program = Program(Target.load(str(tmp_path)))
    text = lift.make_lifter(program, flow).lift(program.by_name["clear"]).text
    assert "fail(" in text


def test_arm_slot_accesses():
    """The ARM layer's slot accesses: single-register str/ldr at a base register plus a constant (sp
    or any other: whether it is the frame says regflow's Frames); writeback forms (pushes and pops)
    and loads of pc are none."""
    from dread.arch.arm import Arm
    a = Arm(isa="t32", profile="m", cpu="cortex-m3")
    slot = lambda code: a.slot_access(a.decode(code, 0x1000, "t32"))
    assert slot(b"\xcd\xf8\x08\xc0") == ("store", "r12", "sp", 8)
    assert slot(b"\xdd\xf8\x08\xc0") == ("load", "r12", "sp", 8)
    assert slot(b"\x02\x93") == ("store", "r3", "sp", 8) and slot(b"\x02\x9b") == ("load", "r3", "sp", 8)
    assert slot(b"\xbb\x60") == ("store", "r3", "r7", 8)                  # str r3, [r7, #8]
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


IT_FLAGS_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global k, m, caller
    .type k, %function
    .thumb_func
k:                          @ libgcc's __aeabi_fmul: r3 set by a flag-setting instruction under ne,
    mov.w   ip, #255        @ read under ne after more new flags (and on the branch's ne edge)
    ands.w  r2, ip, r0, lsr #23
    ittt    ne
    andsne.w r3, ip, r1, lsr #23
    teqne   r2, ip
    teqne   r3, ip
    beq     1f
    adds    r0, r2, r3
    bx      lr
1:  movs    r0, #0
    bx      lr
    .type m, %function
    .thumb_func
m:                          @ against: new flags under ne, then a read under eq of the caller's r3
    cmp     r0, #0
    itt     ne
    movne   r3, #1
    cmpne   r1, #0
    it      eq
    addeq   r0, r3, #1
    bx      lr
    .type caller, %function
    .thumb_func
caller:
    push    {r4, lr}
    bl      k
    bl      m
    pop     {r4, pc}
"""


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-ld")),
                    reason="needs arm-none-eabi binutils")
def test_a_flag_write_under_a_condition_keeps_its_writes(tmp_path):
    """regflow 17: an instruction under a condition that sets the flags ran when the condition holds
    afterwards (skipped, it would have left the flags and the condition false), so what was written
    under that condition stays written for later reads under it. Without it libgcc's __aeabi_fmul
    seemed to read r3, and soft-float code that keeps a value in r3 across it was refused (keeps_fp
    of the compiler lab, built by GCC and by a second compiler)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.s").write_text(IT_FLAGS_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "caller", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    flow = regflow.analyse(Program(Target.load(str(tmp_path))))
    assert flow.read_regs("k") == ["r0", "r1"], flow.read_regs("k")
    assert flow.read_regs("m") == ["r0", "r1", "r3"], flow.read_regs("m")


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-gcc")), reason="needs arm-none-eabi-gcc")
def test_why_names_the_abi_as_a_reader(tmp_path, capsys):
    """A helper's result by the ABI is read after every call (abi_results): regflow lists the ABI as
    a caller of its own ("the ABI", address 0), and --why looked that name up as a function."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    (tmp_path / "t.c").write_text("unsigned long long q(unsigned long long a, unsigned long long b) { return a / b; }\n")
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K }\n"
                                   "SECTIONS { .text : { *(.text*) } > FLASH }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-T", str(tmp_path / "t.ld"),
                    "-e", "q", "-o", elf, str(tmp_path / "t.c"), "-lgcc"], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    assert regflow.main(["--target", str(tmp_path), "--why", "__aeabi_uldivmod"]) in (0, None)
    out = capsys.readouterr().out
    assert "by the ABI: read after every call" in out, out


SAVE_AREA_LOADS_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global walk, pf, spf, plain, caller
    .type walk, %function
    .thumb_func
walk:   ldr     r0, [r1]
        bx      lr
    .size walk, . - walk
    .type pf, %function
    .thumb_func
pf:     push    {r0, r1, r2, r3}        @ GCC -Os printf: the format kept in the save area too
        movs    r3, #0
        push    {r0, r1, r2, lr}
        add     r2, sp, #16
        ldr     r1, [r2], #4            @ the format, back from r0's slot
        mov     r0, r3
        str     r2, [sp, #4]
        bl      walk
        add     sp, sp, #12
        ldr     lr, [sp], #4
        add     sp, sp, #16
        bx      lr
    .size pf, . - pf
    .type spf, %function
    .thumb_func
spf:    push    {r1, r2, r3}            @ GCC -Os sprintf: the format in r1, kept in the save area
        push    {r0, r1, r2, r3, lr}
        add     r2, sp, #20
        ldr     r1, [r2], #4
        str     r0, [sp, #4]
        movs    r3, #0
        add     r0, sp, #4
        str     r2, [sp, #12]
        bl      walk
        add     sp, sp, #16
        ldr     lr, [sp], #4
        add     sp, sp, #12
        bx      lr
    .size spf, . - spf
    .type plain, %function
    .thumb_func
plain:  push    {r1, r2, r3}            @ a va_list handed on unread: the save area stays no use
        push    {r0, lr}
        add     r1, sp, #8
        bl      walk
        pop     {r0, lr}
        add     sp, sp, #12
        bx      lr
    .size plain, . - plain
    .type caller, %function
    .thumb_func
caller: push    {r4, lr}
        bl      pf
        bl      spf
        bl      plain
        pop     {r4, pc}
    .size caller, . - caller
"""


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-ld")),
                    reason="needs arm-none-eabi binutils")
def test_a_named_argument_loaded_back_from_the_save_area_is_read(tmp_path):
    """regflow 21: GCC -Os pushes printf's format with the save area (push {r0-r3}) and sprintf's
    (push {r1-r3}) and loads it back from its slot through a copy of sp: that register is read (dread
    callconv measured it on every ARM build; regflow had counted the whole save area as no use). The
    unnamed ones stay no use, as does a save area whose slots nothing loads at a known offset."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.s").write_text(SAVE_AREA_LOADS_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "caller", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    flow = regflow.analyse(Program(Target.load(str(tmp_path))))
    assert flow.read_regs("pf") == ["r0"], flow.read_regs("pf")
    assert flow.read_regs("spf") == ["r0", "r1"], flow.read_regs("spf")
    assert flow.read_regs("plain") == [], flow.read_regs("plain")      # (r0 only pads the push)


SPILL_OR_SAVE_AREA_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global walk2, spill, va, caller2
    .type walk2, %function
    .thumb_func
walk2:  ldr     r0, [r0]
        bx      lr
    .size walk2, . - walk2
    .type spill, %function
    .thumb_func
spill:  push    {r4, r5, r6, r7, lr}    @ GCC -O2: the fourth argument kept far below the entry
        sub     sp, #28
        str     r3, [sp, #12]
        adds    r0, r0, r1
        adds    r0, r0, r2
        ldr     r3, [sp, #12]
        adds    r0, r0, r3
        add     sp, #28
        pop     {r4, r5, r6, r7, pc}
    .size spill, . - spill
    .type va, %function
    .thumb_func
va:     sub     sp, #12                 @ a save area right below the stack arguments
        str     r1, [sp, #0]
        str     r2, [sp, #4]
        str     r3, [sp, #8]
        push    {r4, lr}
        mov     r0, sp
        bl      walk2
        pop     {r4, lr}
        add     sp, #12
        bx      lr
    .size va, . - va
    .type caller2, %function
    .thumb_func
caller2:
        push    {r4, lr}
        bl      spill
        bl      va
        pop     {r4, pc}
    .size caller2, . - caller2
"""


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-ld")),
                    reason="needs arm-none-eabi binutils")
def test_a_spill_far_below_is_no_save_area(tmp_path):
    """regflow 22: a variadic save area lies right below the stack arguments; a function that spills
    its fourth argument at the entry far below (FreeRTOS's xStreamBufferSend at GCC -O2) is no
    variadic one and reads r3. A save area where it belongs stays no use (va hands it on unread)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.s").write_text(SPILL_OR_SAVE_AREA_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "caller2", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    flow = regflow.analyse(Program(Target.load(str(tmp_path))))
    assert flow.read_regs("spill") == ["r0", "r1", "r2", "r3"], flow.read_regs("spill")
    assert flow.read_regs("va") == [], flow.read_regs("va")


FRAMES_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global walk3, clobber, va_sub, va_slot, nsave, caller3
    .type walk3, %function
    .thumb_func
walk3:  ldr     r0, [r0]
        bx      lr
    .size walk3, . - walk3
    .type clobber, %function
    .thumb_func
clobber:
        movs    r1, #0
        str     r1, [r0]
        bx      lr
    .size clobber, . - clobber
    .type va_sub, %function
    .thumb_func
va_sub: sub     sp, #12                 @ a save area right below the stack arguments
        push    {r4, r5, lr}
        add     r4, sp, #24             @ the entry's stack pointer
        sub     r5, r4, #12             @ the area, made with sub (the old shapes knew add and mov)
        stm     r5, {r1, r2, r3}
        mov     r0, r5
        bl      walk3
        pop     {r4, r5, lr}
        add     sp, #12
        bx      lr
    .size va_sub, . - va_sub
    .type va_slot, %function
    .thumb_func
va_slot:
        sub     sp, #12
        push    {r4, lr}
        sub     sp, #8
        add     r4, sp, #16             @ the area
        str     r4, [sp, #4]            @ kept in a slot (a pointer variable at -O0)
        ldr     r4, [sp, #4]            @ and loaded back: the same frame address
        stm     r4, {r1, r2, r3}
        mov     r0, r4
        bl      walk3
        add     sp, #8
        pop     {r4, lr}
        add     sp, #12
        bx      lr
    .size va_slot, . - va_slot
    .type nsave, %function
    .thumb_func
nsave:  push    {r7, lr}
        mov     r7, sp                  @ a frame pointer
        sub     sp, #8
        str     r12, [sp, #0]           @ r12 saved around the call, never set before
        sub     r0, r7, #8              @ the slot's address, through the frame pointer
        bl      clobber                 @ which may write (or read) the slot
        ldr     r12, [sp, #0]
        add     sp, #8
        pop     {r7, pc}
    .size nsave, . - nsave
    .type caller3, %function
    .thumb_func
caller3:
        push    {r4, lr}
        bl      va_sub
        bl      va_slot
        bl      nsave
        pop     {r4, pc}
    .size caller3, . - caller3
"""


def test_frames_from_the_lifters_states(tmp_path):
    """regflow 24: where a store or load goes in the frame says the lifter's states, whatever register
    addresses it, instead of regflow's own shapes (a copy of sp, a copy of that copy by add or mov,
    sp-relative slots), each found when a compiler used one they lacked. A save area reached through
    a sub from a copy of sp (va_sub) or through a frame address loaded back from a slot (va_slot) is
    no use; before, r1-r3 were read. A save and restore around a call whose callee gets the slot's
    address through the frame pointer (nsave) is no copy through the slot: the callee may read or
    write it, so the save reads r12; before, the frame address went unseen (it was not made from sp)
    and r12 was not read."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.program import Program
    from dread.target import Target
    (tmp_path / "t.s").write_text(FRAMES_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "caller3", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    flow = regflow.analyse(Program(Target.load(str(tmp_path))))
    assert flow.read_regs("va_sub") == [], flow.read_regs("va_sub")
    assert flow.read_regs("va_slot") == [], flow.read_regs("va_slot")
    assert flow.read_regs("nsave") == ["r12"], flow.read_regs("nsave")


GAUGE_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global gauge_now, gauge_hold, gauge_maybe, gauge_via, gauge_back, gauge_bump, gauge_since, gauge_poll
    .global gauge_keep, gauge_try, gauge_relay, gauge_reback, gauge_rebump, gauge_pair, gauge_spin, gauge_main
    .type gauge_now, %function
    .thumb_func
gauge_now:                  @ a getter: r0 written on every path, nothing read (xTaskGetTickCount)
        ldr     r3, =0x20000100
        ldr     r0, [r3]
        bx      lr
    .size gauge_now, . - gauge_now
    .type gauge_hold, %function
    .thumb_func
gauge_hold:                 @ empty: hands back the r0 its caller passed (newlib's lock functions)
        bx      lr
    .size gauge_hold, . - gauge_hold
    .type gauge_maybe, %function
    .thumb_func
gauge_maybe:                @ r0 written on one path only
        ldr     r3, =0x20000104
        ldr     r3, [r3]
        cbz     r3, 1f
        movs    r0, #7
1:      bx      lr
    .size gauge_maybe, . - gauge_maybe
    .type gauge_via, %function
    .thumb_func
gauge_via:                  @ the getter as a tail call
        movs    r1, #2
        b       gauge_now
    .size gauge_via, . - gauge_via
    .type gauge_back, %function
    .thumb_func
gauge_back:                 @ r0 pushed and popped around the getter's call: put back
        push    {r0, lr}
        bl      gauge_now
        pop     {r0, pc}
    .size gauge_back, . - gauge_back
    .type gauge_bump, %function
    .thumb_func
gauge_bump:                 @ writes r0 on every path, from what gauge_maybe may hand back unwritten
        push    {r4, lr}
        bl      gauge_maybe
        adds    r0, #1
        pop     {r4, pc}
    .size gauge_bump, . - gauge_bump
    .type gauge_since, %function
    .thumb_func
gauge_since:                @ the getter's value minus the argument
        push    {r4, lr}
        mov     r4, r0
        bl      gauge_now
        subs    r0, r0, r4
        pop     {r4, pc}
    .size gauge_since, . - gauge_since
    .type gauge_poll, %function
    .thumb_func
gauge_poll:                 @ takes nothing: stores the getter's value
        push    {r4, lr}
        bl      gauge_now
        ldr     r3, =0x20000108
        str     r0, [r3]
        pop     {r4, pc}
    .size gauge_poll, . - gauge_poll
    .type gauge_keep, %function
    .thumb_func
gauge_keep:                 @ the same with the empty function
        push    {r4, lr}
        bl      gauge_hold
        ldr     r3, =0x20000108
        str     r0, [r3]
        pop     {r4, pc}
    .size gauge_keep, . - gauge_keep
    .type gauge_try, %function
    .thumb_func
gauge_try:
        push    {r4, lr}
        bl      gauge_maybe
        ldr     r3, =0x20000108
        str     r0, [r3]
        pop     {r4, pc}
    .size gauge_try, . - gauge_try
    .type gauge_relay, %function
    .thumb_func
gauge_relay:
        push    {r4, lr}
        bl      gauge_via
        ldr     r3, =0x20000108
        str     r0, [r3]
        pop     {r4, pc}
    .size gauge_relay, . - gauge_relay
    .type gauge_reback, %function
    .thumb_func
gauge_reback:
        push    {r4, lr}
        bl      gauge_back
        ldr     r3, =0x20000108
        str     r0, [r3]
        pop     {r4, pc}
    .size gauge_reback, . - gauge_reback
    .type gauge_rebump, %function
    .thumb_func
gauge_rebump:
        push    {r4, lr}
        bl      gauge_bump
        ldr     r3, =0x20000108
        str     r0, [r3]
        pop     {r4, pc}
    .size gauge_rebump, . - gauge_rebump
    .type gauge_pair, %function
    .thumb_func
gauge_pair:                 @ the empty function's r0 goes only into the getter's call
        push    {r4, lr}
        bl      gauge_hold
        bl      gauge_now
        ldr     r3, =0x20000108
        str     r0, [r3]
        pop     {r4, pc}
    .size gauge_pair, . - gauge_pair
    .type gauge_spin, %function
    .thumb_func
gauge_spin:                 @ never returns
        b       gauge_spin
    .size gauge_spin, . - gauge_spin
    .type gauge_main, %function
    .thumb_func
gauge_main:
        push    {r4, lr}
        bl      gauge_since
        ldr     r3, =0x2000010c     @ (its result read: not void, dread.tidy void_results)
        str     r0, [r3]
        bl      gauge_poll
        bl      gauge_keep
        bl      gauge_try
        bl      gauge_relay
        bl      gauge_reback
        bl      gauge_rebump
        bl      gauge_pair
        bl      gauge_spin
    .ltorg
    .size gauge_main, . - gauge_main
"""


def gauge_target(tmp_path):
    """A Cortex-M3 target of GAUGE_S: getters, an empty function, one that writes r0 on one path only,
    a tail call, a push and pop of r0, and their callers (dread readable's result arguments)."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.target import Target
    (tmp_path / "t.s").write_text(GAUGE_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "gauge_main", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    return Target.load(str(tmp_path))


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-ld")),
                    reason="needs arm-none-eabi binutils")
def test_registers_a_function_overwrites(tmp_path):
    """regflow 25's overwritten: the registers a function writes on every path to its return without
    reading first what its caller left there, through its callees and tail calls. A getter overwrites
    r0, and so does a tail call of it; an empty function, one that writes r0 on one path only, and one
    that pushes r0 and pops it back after a call that writes it do not (each may hand back what its
    caller passed); nor does one that writes r0 on every path from what such a callee hands back
    (gauge_bump adds 1 to gauge_maybe's result: regflow's reads, which end r0 at a call that may write
    it, see no read of r0 there). A function that never returns overwrites what it does not read. The
    cases' environment does not see the new set (case_facts), so that every image kept its cases."""
    import json
    target = gauge_target(tmp_path)
    flow = regflow.analyse(Program(target))
    own = {fn: flow.overwritten_regs(fn) for fn in flow.reads}
    assert "r0" in own["gauge_now"] and flow.read_regs("gauge_now") == [], own["gauge_now"]
    assert {"r0", "r1"} <= set(own["gauge_via"]), own["gauge_via"]
    assert "r0" in own["gauge_poll"] and "r0" in own["gauge_relay"], (own["gauge_poll"], own["gauge_relay"])
    for name in ("gauge_hold", "gauge_maybe", "gauge_back", "gauge_bump", "gauge_keep", "gauge_try", "gauge_reback",
                 "gauge_rebump", "gauge_since"):
        assert "r0" not in own[name], (name, own[name])
    assert flow.read_regs("gauge_bump") == [] and flow.read_regs("gauge_back") == []      # (what reads sees)
    assert set(own["gauge_spin"]) >= {"r0", "r1", "r2", "r3", "r12"}, own["gauge_spin"]
    assert "overwritten" in flow.to_json() and "overwritten" not in flow.case_facts()
    again = regflow.RegFlow.from_json(json.loads(json.dumps(flow.to_json())))
    assert again.overwritten_regs("gauge_via") == flow.overwritten_regs("gauge_via")
    assert regflow.RegFlow.from_json(flow.case_facts()).overwritten_regs("gauge_now") == []   # (a cache before 25)


DEEP_S = """
    .syntax unified
    .cpu cortex-m3
    .thumb
    .text
    .global use, mid, leaf, wrap, tail_mid, top
    .type use, %function
    .thumb_func
use:                        @ reads its caller's r3 only when r0 is neither 1 nor 2 (an uninitialised
    cmp     r0, #1          @ variable of the source)
    itt     eq
    moveq   r3, #10
    beq     1f
    cmp     r0, #2
    it      eq
    moveq   r3, #20
1:  adds    r0, r3, #1
    bx      lr
    .size use, . - use
    .type mid, %function
    .thumb_func
mid:                        @ hands its caller's r3 to use
    push    {r4, lr}
    bl      use
    pop     {r4, pc}
    .size mid, . - mid
    .type leaf, %function
    .thumb_func
leaf:                       @ leaves r3, which top reads (through wrap's return, tail_mid and mid)
    movs    r3, #7
    movs    r0, #0
    bx      lr
    .size leaf, . - leaf
    .type wrap, %function
    .thumb_func
wrap:
    push    {r4, lr}
    bl      leaf
    pop     {r4, pc}
    .size wrap, . - wrap
    .type tail_mid, %function
    .thumb_func
tail_mid:
    b       mid
    .size tail_mid, . - tail_mid
    .type top, %function
    .thumb_func
top:
    push    {r4, lr}
    bl      wrap
    bl      tail_mid
    pop     {r4, pc}
    .size top, . - top
"""


@pytest.mark.skipif(not (shutil.which("arm-none-eabi-as") and shutil.which("arm-none-eabi-ld")),
                    reason="needs arm-none-eabi binutils")
def test_why_deep_follows_a_kept_register_to_where_it_is_used(tmp_path, capsys):
    """--why names the instruction behind a kept register, which may be a call whose callee reads the
    register at its entry: leaf's r3 goes back through wrap's return to top, which hands it on by a call
    of tail_mid, a tail call to mid and mid's call to use. --why --deep (Analysis.ends) follows it to
    the instruction that uses it, use's adds, with the path there: what an argument that leaf's r3 cannot
    matter (kept.tsv) has to cover."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    from dread.target import Target
    (tmp_path / "t.s").write_text(DEEP_S)
    obj, elf = str(tmp_path / "t.o"), str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-as", "-o", obj, str(tmp_path / "t.s")], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=0x08000000", "-e", "top", "-o", elf, obj], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '\n[image.main]\noriginal = "original/t.bin"\nbase = 0x08000000\n'
                                         '\n[compiler]\ncross = "arm-none-eabi-"\n')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    program = Program(Target.load(str(tmp_path)))
    flow = regflow.analyse(program)
    assert "r3" in flow.kept_regs("leaf") and "r3" in flow.read_regs("use")
    adds = next(a for a in program.by_name["use"].insns if program.insn(a).mnemonic.startswith("adds"))
    analysis = regflow.Analysis(program).solve(compared=True)
    (site,) = flow.sites_of("leaf", "r3")
    ends = analysis.ends(*site, "r3")
    assert set(ends) == {("use", adds, "read")}, ends
    steps = [name for name, _ in ends[("use", adds, "read")]]
    assert steps == ["wrap", "wrap", "top", "top", "tail_mid", "mid"], ends
    # what use reads of its own entry (its r0), and r3 where use itself wrote it, are no ends of leaf's r3
    assert regflow.main(["--target", str(tmp_path), "--why", "--deep", "leaf"]) in (0, None)
    out = capsys.readouterr().out
    assert "in the end (--deep), 1 instruction:" in out and "0x%x in use: adds r0, r3, #1" % adds in out, out
    with pytest.raises(SystemExit):
        regflow.main(["--target", str(tmp_path), "--deep", "leaf"])
    # kept.tsv: an argument naming no instructions applies as written; one naming every instruction where
    # the value ends up applies; one that misses one lapses (regflow.argued_kept)
    kept = tmp_path / "tests" / "kept.tsv"
    kept.parent.mkdir(exist_ok=True)
    image = Target.load(str(tmp_path)).image()
    assert regflow.argued_path(image) == str(kept)
    for ends, applies in (("", True), ("%x" % adds, True), ("%x %x" % (adds, adds + 2), True), ("%x" % (adds + 2), False)):
        kept.write_text("# function\tregister\targument\tends\nleaf\tr3\tuse reads it only where r0 is neither 1 nor 2"
                        "\t%s\n" % ends)
        applied, lapsed = regflow.argued_kept(program, flow, image)
        assert (("leaf", "r3") in applied) == applies and (("leaf", "r3") in lapsed) != applies, (ends, applied, lapsed)
        if not applies:
            assert lapsed[("leaf", "r3")][1] == [adds]
    regflow.main(["--target", str(tmp_path), "--why", "--deep", "leaf"])
    assert "kept.tsv argues it, but does not cover 0x%x" % adds in capsys.readouterr().out
    # a line without an argument does not apply; --ending writes such lines for every kept register whose
    # value ends only at the given instructions (leaf's, and wrap's, whose return hands leaf's on: the chain
    # one cause explains), and none for an address where nothing ends
    kept.write_text("leaf\tr3\t\t%x\n" % adds)
    assert regflow.argued_kept(program, flow, image) == ({}, {})
    assert regflow.main(["--target", str(tmp_path), "--ending", "%x" % adds]) == 0
    lines = [l for l in capsys.readouterr().out.splitlines() if not l.startswith("#")]
    assert lines == ["leaf\tr3\t\t%x" % adds, "wrap\tr3\t\t%x" % adds], lines
    assert regflow.main(["--target", str(tmp_path), "--ending", "%x" % (adds + 2)]) == 0
    assert [l for l in capsys.readouterr().out.splitlines() if not l.startswith("#")] == []
