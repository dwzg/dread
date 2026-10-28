"""Device models for the system run (dread.sysrun).

A model stands for one device: it owns a range of peripheral registers, answers the firmware's reads,
takes its writes, may keep time on the run's shared clock, and may raise interrupts. Models are
configured in the target's dread.toml, one table each:

    [models.uart]
    kind = "uart"               # a model class of this package, or "file.py:Class" (the target's own)
    base = 0x80000000           # and what the class takes

A model is the same for both images of a comparison; a difference between them is traced to its
instruction, never removed by changing a model. Each class's docstring says what it assumes.

A model may name the registers whose reads are plain (`plain`, ranges like `ranges`): a read of
them changes nothing (no queue popped, no flag cleared) and their value changes only through a call
into the models (a write, an event, an input, an interrupt line, an exception taken or returned
from), not with the clock or the core. The system run then does repeated reads of them without
calling read() (on Unicorn in C, dread.fasthooks) and asks read() again after every such call; an
access counts as plain when it lies wholly inside one range. The default is none, so a model of
the target's own is asked at every read until it says otherwise.

Likewise the registers whose writes are idempotent (`idempotent`): a write there, made again with
the same size and value with no other call into the models between, at any later clock, changes
nothing: the model's state stays as the first write left it, and the repeat calls nothing of the
run but interrupts_changed (no note, no interrupt line, no stop). The system run then does such
repeats without calling write() (on Unicorn in C), and without schedule() or interrupts_changed,
since nothing they depend on changed. A write whose effect depends on the clock (one that starts a
count from now) or that sends something is not idempotent. The default is none.
"""
import importlib.util
import os


class Model:
    """The interface. Subclasses set `ranges` and override what they need."""

    kind = None

    def __init__(self, system, name, config):
        self.system, self.name, self.config = system, name, dict(config)
        self.ranges = []                    # [(lo, hi)] of the registers this model answers
        self.plain = []                     # [(lo, hi)] of those whose reads are plain (above)
        self.idempotent = []                # [(lo, hi)] of those whose writes are idempotent (above)

    def number(self, key, default=None):
        value = self.config.get(key, default)
        if value is None:
            raise ValueError("model %s needs %s" % (self.name, key))
        return int(value)

    def read(self, address, size):
        """The value the firmware reads, or None for plain memory."""
        return None

    def write(self, address, size, value):
        """What a store of the firmware does (the value is in memory already)."""

    def next_event(self):
        """The shared-clock time of this model's next event, or None."""
        return None

    def event(self, clock):
        """The shared clock reached next_event()."""

    def input(self, data):
        """Input a scenario hands the model (bytes for a UART)."""
        raise ValueError("model %s takes no input" % self.name)

    def report(self):
        """What the model saw, for the run's summary (compared between two images)."""
        return {}

    # An interrupt controller also has line(number, level), pending_interrupt() -> the number to
    # take or None, taken(number) and returned() (dread.models.intc, dread.models.nvic).
    # A device of x86's port space also has ports ([(lo, hi)] of the ports it owns), port_read(port,
    # size) -> the value in reads and port_write(port, size, value) for out (dread.sysrun _port).


def without(ranges, holes):
    """`ranges` [(lo, hi)] less the address ranges `holes` [(lo, hi)]: for a model's plain ranges,
    its registers less those with a side effect or a value that moves with the clock (and for its
    idempotent ranges, less those whose writes are not)."""
    out = []
    for lo, hi in ranges:
        parts = [(lo, hi)]
        for h_lo, h_hi in holes:
            parts = [p for a, b in parts for p in ((a, min(b, h_lo)), (max(a, h_hi), b)) if p[0] < p[1]]
        out += parts
    return out


def classes():
    from dread.models import clint, gpio, intc, nvic, regs, timer, uart
    return {"uart": uart.Uart, "timer": timer.Timer, "intc": intc.Intc, "nvic": nvic.Nvic, "gpio": gpio.Gpio,
            "clint": clint.Clint, "regs": regs.Regs}


def create(system, name, config, root):
    """A model instance from its [models.NAME] table; root: the target directory, for "file.py:Class"."""
    kind = config.get("kind")
    if kind is None:
        raise ValueError("model %s has no kind" % name)
    known = classes()
    if kind in known:
        return known[kind](system, name, config)
    if ":" in kind:
        path, cls = kind.rsplit(":", 1)
        spec = importlib.util.spec_from_file_location("dread_model_%s" % name, os.path.join(root, path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return getattr(module, cls)(system, name, config)
    raise ValueError("model %s: unknown kind %r (known: %s)" % (name, kind, ", ".join(sorted(known))))
