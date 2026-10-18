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


@pytest.mark.rv32
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


@pytest.mark.rv32
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


@pytest.mark.rv32
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
    if variant == "m3":     # the emulated dispatch agrees with decoding the tbb table
        decoded = program.arch.switch_targets(program.insn(jump), program, lambda a: (a & ~1) in set(fn.insns))
        assert sorted(decoded) == inside
    fn, (jump,) = jumps(program, "dispatch")
    assert switches.resolve(program, fn, jump) == ([], sorted(program.by_name[n].entry for n in
                                                              ("cmd_led", "cmd_beep", "cmd_mode", "cmd_volume")))
    fn, (jump,) = jumps(program, "__ram_crc_veneer")
    assert switches.resolve(program, fn, jump) == ([], [program.by_name["ram_crc"].entry])


@pytest.mark.rv32
def test_entry_reads_match_the_signatures(target, variant):
    flow = regflow.load(target, target.image())
    want = dict(SIGNATURES)
    if variant == "rv32":       # a0-a7 for the arguments: sum6 takes all six in registers; no veneer
        want = {name: " ".join("a" + r[1:] for r in regs.split()) for name, regs in want.items()}
        want["sum6"] = "a0 a1 a2 a3 a4 a5"
        del want["__ram_crc_veneer"]
    got = {name: " ".join(flow.read_regs(name)) for name in want}
    assert got == want


@pytest.mark.rv32
def test_cache_follows_the_tables(target, tmp_path, monkeypatch):
    image = target.image()
    first = regflow.cache_key(target, image)
    monkeypatch.setattr(target.abi, "callee_saved", target.abi.callee_saved[:-1])
    assert regflow.cache_key(target, image) != first


CLOBBER = ("REG32(UART_BASE) = (u8)c;", 'REG32(UART_BASE) = (u8)c;\n    __asm__ volatile("mov r1, #0" ::: "r1");')


@pytest.mark.slow
@pytest.mark.rv32
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
    # through the caller it shows only for strings of two or more characters: all generated inputs
    assert integration.main(["--target", target.root, "--recon", payload, "--cases", "40", "uart_puts"]) == 1
    assert "DIFF uart_puts" in capsys.readouterr().out


@pytest.mark.rv32
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
