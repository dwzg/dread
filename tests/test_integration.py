"""dread integration on gizmo: rewritten functions and their callers agree with real callees, and a
bug in a rewritten callee shows in a caller that is still original (uart_puts calls uart_putc; a
sign extension stores 0xffffff80 for the byte 0x80)."""
import pytest

from dread import build, integration
from conftest import have
from test_runner import src_copy

pytestmark = pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")


@pytest.mark.slow
def test_callers_agree(target, capsys):
    """(Slow on a fresh checkout: inputs for the callers are generated, main's for the whole
    GENERATE_SECONDS because it never returns.)"""
    build.build(target, target.image(), quiet=True)
    assert integration.main(["--target", target.root, "--callers"]) == 0
    assert "0 differ" in capsys.readouterr().out


@pytest.mark.slow
def test_callee_bug_shows_in_a_caller(target, variant, tmp_path, capsys):
    src = src_copy(tmp_path, variant, ("REG32(UART_BASE) = (u8)c;", "REG32(UART_BASE) = (signed char)c;"))
    payload = build.build(target, target.image(), src=str(src), out=str(tmp_path / "recon"), quiet=True)
    rc = integration.main(["--target", target.root, "--recon", payload, "uart_puts"])
    out = capsys.readouterr().out
    assert rc == 1 and "DIFF uart_puts" in out and "peripheral accesses differ" in out, out
