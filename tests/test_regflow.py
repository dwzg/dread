"""dread regflow on gizmo: what callers read after a call, what functions read at their entry, and
that the runner and the integration run compare the registers it finds.

Both gizmo builds have one register a compiler kept across a call: uart_puts keeps its string
pointer in r1 across bl uart_putc (GCC -fipa-ra, the two are in one file). On a32 the remainder of
__aeabi_uidivmod comes back in r1 too. Anything else found would be a false alarm."""
import pytest

from dread import build, integration, regflow
from conftest import have
from test_runner import run_all, src_copy

SIGNATURES = {           # registers each function reads at its entry, from the C (beyond 4: the stack)
    "clamp": "r0 r1 r2", "checksum": "r0 r1", "parse_uint": "r0 r1", "parse_kv": "r0 r1 r2 r3",
    "sum6": "r0 r1 r2 r3", "mul64": "r0 r1", "udiv_var": "r0 r1", "fmt_hex": "r0 r1 r2", "fmt_dec": "r0 r1",
    "mode_name": "r0", "ring_put": "r0 r1", "ring_get": "r0", "dispatch": "r0 r1", "uart_putc": "r0",
    "uart_puts": "r0", "uart_getc": "", "print_status": "", "main": "", "apply_config": "r0",
    "strcmp": "r0 r1", "memcpy": "r0 r1 r2", "strncpy": "r0 r1 r2", "delay": "r0", "timer_irq": "",
}


def test_kept_registers(target, variant):
    flow = regflow.load(target, target.image())
    extra = {name for name in flow.kept if [r for r in flow.kept_regs(name) if r != "r0"]}
    assert extra == ({"uart_putc", "__aeabi_uidivmod"} if variant == "a32" else {"uart_putc"})
    assert flow.kept_regs("uart_putc") == ["r1"]
    assert [caller for caller, _ in flow.sites_of("uart_putc", "r1")] == ["uart_puts"]
    assert "r0" in flow.kept_regs("parse_uint") and "r0" not in flow.kept_regs("ring_put")


def test_entry_reads_match_the_signatures(target):
    flow = regflow.load(target, target.image())
    got = {name: " ".join(flow.read_regs(name)) for name in SIGNATURES}
    assert got == SIGNATURES


def test_cache_follows_the_tables(target, tmp_path, monkeypatch):
    image = target.image()
    first = regflow.cache_key(target, image)
    monkeypatch.setattr(target.abi, "callee_saved", target.abi.callee_saved[:-1])
    assert regflow.cache_key(target, image) != first


CLOBBER = ("REG32(UART_BASE) = (u8)c;", 'REG32(UART_BASE) = (u8)c;\n    __asm__ volatile("mov r1, #0" ::: "r1");')


@pytest.mark.slow
@pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")
def test_a_clobbered_kept_register_fails(target, variant, tmp_path, capsys):
    """A uart_putc that ends with r1 = 0: everything its own tests compared before agrees, but
    uart_puts reads r1 after the call."""
    src = src_copy(tmp_path, variant, CLOBBER)
    payload = build.build(target, target.image(), src=str(src), out=str(tmp_path / "recon"), quiet=True)
    failed, uncovered, text = run_all(target, payload, names=["uart_putc"])
    assert failed and "r1 after the return" in text and "uart_puts" in text, text
    assert integration.main(["--target", target.root, "--recon", payload, "uart_puts"]) == 1
    assert "DIFF uart_puts" in capsys.readouterr().out
