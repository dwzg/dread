"""Device models for the system run (dread.sysrun).

A model stands for one device: it owns a range of peripheral registers, answers the firmware's reads,
takes its writes, may keep time on the run's shared clock, and may raise interrupts. Models are
configured in the target's dread.toml, one table each:

    [models.uart]
    kind = "uart"               # a model class of this package, or "file.py:Class" (the target's own)
    base = 0x80000000           # and what the class takes

A model is the same for both images of a comparison; a difference between them is traced to its
instruction, never removed by changing a model. Each class's docstring says what it assumes.
"""
import importlib.util
import os


class Model:
    """The interface. Subclasses set `ranges` and override what they need."""

    kind = None

    def __init__(self, system, name, config):
        self.system, self.name, self.config = system, name, dict(config)
        self.ranges = []                    # [(lo, hi)] of the registers this model answers

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


def classes():
    from dread.models import gpio, intc, nvic, timer, uart
    return {"uart": uart.Uart, "timer": timer.Timer, "intc": intc.Intc, "nvic": nvic.Nvic, "gpio": gpio.Gpio}


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
