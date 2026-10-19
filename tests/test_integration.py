"""dread integration on gizmo: rewritten functions and their callers agree with real callees, and a
bug in a rewritten callee shows in a caller that is still original (uart_puts calls uart_putc, which
sends every character one too high)."""
import pytest

from dread import build, integration
from test_runner import src_copy

pytestmark = pytest.mark.compiles


@pytest.mark.slow
def test_callers_agree(target, capsys):
    """(Slow on a fresh checkout: inputs for the callers are generated, main's for the whole
    GENERATE_SECONDS because it never returns.)"""
    build.build(target, target.image(), quiet=True)
    assert integration.main(["--target", target.root, "--callers"]) == 0
    assert "0 differ" in capsys.readouterr().out


@pytest.mark.slow
def test_callee_bug_shows_in_a_caller(target, variant, tmp_path, capsys):
    """The caller is original code that calls the buggy uart_putc: uart_puts on the ARM builds; on
    rv32 clang inlined uart_putc into uart_puts, and handle_key's tail call is the only call left."""
    caller = "handle_key" if variant == "rv32" else "uart_puts"
    src = src_copy(tmp_path, variant, ("HWREG(UART_BASE) = (u8)c;", "HWREG(UART_BASE) = (u8)(c + 1);"))
    payload = build.build(target, target.image(), src=str(src), out=str(tmp_path / "recon"), quiet=True)
    rc = integration.main(["--target", target.root, "--recon", payload, "--cases", "40", caller])
    out = capsys.readouterr().out
    assert rc == 1 and "DIFF " + caller in out and "peripheral accesses differ" in out, out


def test_an_input_whose_outcome_follows_the_stack_is_left_out(tmp_path, capsys):
    """The stack address seed_from_stack seeds its random number with lies elsewhere in the C's
    frame, and the number is no stack address (which the run leaves uncompared anyway): its inputs
    are left out, since the original's own outcome changes with its stack lower."""
    from test_runner import stack_seed_target
    target, image, payload = stack_seed_target(tmp_path, 'auto("seed_from_stack")\nauto("plain")\n')
    rc = integration.main(["--target", target.root, "--recon", payload])
    out = capsys.readouterr().out
    assert rc == 0 and "DIFF" not in out, out
