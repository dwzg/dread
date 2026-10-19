"""dread sysrun on gizmo: both builds boot to READY from reset with the same device log in the
original and the rebuilt image (and the same shared clock, although six functions run as C), a bug
planted in a rewritten function shows as a difference in the device log, and the stop rules end a
waiting and a polling firmware."""
import json
import os

import pytest

from dread import build, sysrun
from test_runner import src_copy

pytestmark = pytest.mark.compiles


def both(target, payload=None, scenario=None):
    image = target.image()
    recon = payload or os.path.join(build.recon_dir(target, image), "payload.bin")
    if payload is None:
        build.build(target, image, quiet=True)
    with open(os.path.join(os.path.dirname(recon), "placement.json")) as f:
        placed = json.load(f)["functions"]
    built = [n for n, p in placed.items() if p["placement"] != "helper"]
    scenario = scenario if scenario is not None else target.scenarios["boot"]
    with open(recon, "rb") as f:
        rebuilt = f.read()
    a = sysrun.run_image(target, image, image.payload(), scenario, built=built)
    b = sysrun.run_image(target, image, rebuilt, scenario, placement=placed)
    return a, b


def test_boot_agrees(target):
    a, b = both(target)
    assert a.how.startswith("until") and b.how.startswith("until"), (a.how, b.how)
    assert a.reports["uart"]["sent"].endswith("READY") and "GIZMO 1000" in a.reports["uart"]["sent"]
    assert sysrun.compare(a, b) == []
    assert a.clock == b.clock and a.log == b.log


def test_a_planted_bug_shows(target, variant, tmp_path):
    """fmt_hex writes the checksum line; a digit one too high changes what the UART sends."""
    src = src_copy(tmp_path, variant, ("out[i] = d < 10 ? '0' + d : 'a' + d - 10;",
                                       "out[i] = d < 10 ? '1' + d : 'a' + d - 10;"))
    payload = build.build(target, target.image(), src=str(src), out=str(tmp_path / "recon"), quiet=True)
    a, b = both(target, payload)
    diff = sysrun.compare(a, b)
    assert diff and diff[0].startswith("device logs differ"), diff
    assert "crc" in a.reports["uart"]["sent"] and a.reports != b.reports


def test_stop_rules(target):
    image = target.image()
    waits = sysrun.run_image(target, image, image.payload(), {"models": ["uart"], "limit": 3_000_000})
    assert waits.how.startswith("waits"), waits.how          # after READY without a timer: nothing comes
    polls = sysrun.run_image(target, image, image.payload(), {"models": [], "limit": 3_000_000})
    assert polls.how.startswith("polls"), polls.how          # no UART: uart_putc waits for "can send"


def test_keys_go_through_the_timer_interrupt(target):
    """'s' arrives in the receive queue; timer_irq (an interrupt: a32 IRQ, m3 exception 16) moves it
    into the ring buffer and the main loop prints the status again."""
    a, b = both(target, scenario=target.scenarios["keys"])
    assert a.how.startswith("until") and sysrun.compare(a, b) == [], (a.how, sysrun.compare(a, b))
    assert a.reports["uart"]["sent"].endswith("READY\r\nmode run volume 7")
    assert a.reports["interrupts"] and a.reports["interrupts"] == b.reports["interrupts"]


def test_ticks_agree_for_a_long_run(target):
    """No end condition: the timer keeps interrupting until the limit; both images take the same
    interrupts at the same places, and the LED key reaches the pins."""
    scenario = {"input": [[40_000, "uart", "l"]], "limit": 600_000}
    a, b = both(target, scenario=scenario)
    assert a.how.startswith("limit") and sysrun.compare(a, b) == []
    assert sum(a.reports["interrupts"].values()) > 10
    assert a.reports["gpio"]["pins"] & 2                     # led_on: GPIO_OUT bit 1


def test_a_bug_in_the_interrupt_path_shows(target, variant, tmp_path):
    src = src_copy(tmp_path, variant, ("r->buf[head] = c;", "r->buf[head] = c + 1;"))
    payload = build.build(target, target.image(), src=str(src), out=str(tmp_path / "recon"), quiet=True)
    a, b = both(target, payload, scenario=target.scenarios["keys"])
    assert sysrun.compare(a, b), (a.how, b.how)


class Clock:
    """What a model needs of the system, for unit tests."""
    def __init__(self):
        self.clock, self.lines, self.noted, self.changed = 0, {}, [], 0

    def interrupt_level(self, number, level):
        self.lines[number] = level

    def interrupts_changed(self):
        self.changed += 1

    def note(self, *what):
        self.noted.append(what)


def test_timer_counts_and_expires():
    from dread.models.timer import Timer
    system = Clock()
    t = Timer(system, "timer", {"base": 0x1000, "rate": 10, "irq": 3})
    t.write(0x1004, 4, 100)                  # load
    t.write(0x1008, 4, 3)                    # run, interrupt
    assert t.next_event() == 1000
    system.clock = 400
    assert t.read(0x1000, 4) == 60 and t.read(0x100C, 4) == 0
    system.clock = 1000
    t.event(1000)
    assert t.read(0x100C, 4) == 1 and system.lines[3] and t.next_event() == 2000
    t.write(0x100C, 4, 1)                    # clear
    assert not system.lines[3]


def test_systick():
    from dread.models.nvic import Nvic, SYST
    system = Clock()
    n = Nvic(system, "nvic", {"systick_rate": 2})
    n.write(SYST + 4, 4, 99)                 # reload
    n.write(SYST, 4, 3)                      # enable, interrupt
    assert n.next_event() == 200
    n.event(200)
    assert n.pending_interrupt() == 15 and n.read(SYST, 4) & (1 << 16)
    assert not n.read(SYST, 4) & (1 << 16)   # countflag clears on read
    n.taken(15)
    assert n.pending_interrupt() is None
    n.write(0xE000E100, 4, 1 << 4)           # enable line 4
    n.line(4, True)
    assert n.pending_interrupt() == 20


def test_a_stop_at_a_device_access_does_not_repeat_it(target, monkeypatch):
    """With a peripheral access costing much of the shared clock, events come due inside the access
    hooks. A stop there left pc on the accessing instruction (Unicorn's way), and going on repeated
    the access: the UART sent characters twice ("modee run") and input went missing. The run stops
    at the next block instead (System.due)."""
    monkeypatch.setattr(sysrun, "ACCESS", 333)
    image = target.image()
    out = sysrun.run_image(target, image, image.payload(), target.scenarios["keys"])
    assert out.how.startswith("until"), out.how
    assert out.reports["uart"]["sent"].startswith("GIZMO 1000\r\nmode run volume 7\r\ncrc c9c999c5\r\nREADY\r\nmode run")
