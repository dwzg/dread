"""Spec for `dread record read_line`: uart_getc delivers a line, character by character, as a
stream (-1 when the stream ends). The lines mix ordinary characters with carriage returns, end
with a line feed or run dry, and are sometimes longer than the buffer."""
from dread.record import Stream

CALLS = (("uart_getc", 0),)             # stubbed, logging no argument registers
MODELS = {"uart_getc": Stream.getc}
WHY = "a line through uart_getc (examples/gizmo/specs/read_line.py)"
SCRATCH = None                          # set by dread record: the target's scratch memory


def gen(rng):
    size = rng.choice((1, 2, 4, 8, 16, 40))
    text = bytes(rng.choice(b"volume=7 mode=2 baud\r") for _ in range(rng.randrange(0, 2 * size + 2)))
    if rng.random() < 0.8:
        text += b"\n"
    return [SCRATCH + 0x100, size], [], {"stream": Stream(text)}, {}
