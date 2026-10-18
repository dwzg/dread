"""Cases for the functions examples/gizmo/rewritten/rewritten.c rewrites. Run by both targets'
case files, which define HW (the peripheral bases of their build) before executing this file."""

SCR = SCRATCH


def memory(*items):
    """A setup that writes (address, bytes) items."""
    def setup(m):
        for address, data in items:
            m.write(address, data)
    return setup


# clamp: every side of both comparisons, the boundaries, signed extremes, an empty range
for x, lo, hi in [(5, 0, 9), (-1, 0, 9), (0, 0, 9), (9, 0, 9), (10, 0, 9), (-2**31, -5, 5),
                  (2**31 - 1, -5, 5), (3, 7, 2), (-7, -9, -8), (-9, -9, -8), (-8, -9, -8)]:
    case("clamp", "%d in [%d, %d]" % (x, lo, hi), memory(), x & 0xFFFFFFFF, lo & 0xFFFFFFFF, hi & 0xFFFFFFFF)

# checksum: every length up to past the unrolled loop's step, and long buffers with high bytes
for n in list(range(10)) + [64, 255]:
    data = bytes((i * 37 + 11) & 0xFF for i in range(n))
    case("checksum", "%d bytes" % n, memory((SCR, data)), SCR, n)

# parse_uint: decimal, hex in both cases, no digits, wrap-around, stops at the first non-digit
for text in [b"0", b"7", b"12345", b"4294967295", b"4294967296", b"0x1F", b"0X1f", b"0xFFFFFFFF", b"0xa9",
             b"0x", b"0xg", b"x12", b"", b"12ab", b"0x1g2", b"007", b"9/", b"0x9:", b"0x@", b"0xG", b"0x`"]:
    case("parse_uint", repr(text), memory((SCR, text + b"\0"), (SCR + 0x100, b"\xee" * 4)), SCR, SCR + 0x100)

# mode_name: every mode the table has and some it has not
for mode in list(range(10)) + [0xFFFFFFFF, 0x80000000]:
    case("mode_name", "mode %#x" % mode, memory(), mode)

# sum6: six arguments, two of them on the stack
for values in [(1, 2, 3, 4, 5, 6), (0xFFFFFFFF,) * 6, (0, 0, 0, 0, 0x10000, 0x20000), (7, 0, 0, 0, 0, 0)]:
    case("sum6", "%r" % (values,), memory(), *values)


def wide(m):
    m.wide_result = True


# mul64: the second result register counts
for a, b in [(0, 0), (1, 1), (0xFFFFFFFF, 0xFFFFFFFF), (0x12345678, 0x9ABCDEF0), (0x80000000, 2)]:
    case("mul64", "%#x * %#x" % (a, b), wide, a, b)

# udiv_var: zero divisor, quotient 0, large values
for a, b in [(10, 3), (0, 1), (0xFFFFFFFF, 1), (7, 0), (0x80000000, 0x10000), (5, 10), (0xFFFFFFFF, 0xFFFFFFFF)]:
    case("udiv_var", "%#x / %#x" % (a, b), memory(), a, b)

# fmt_hex: digits and letters, every width, no digits at all
for v, digits in [(0, 1), (0x1234abcd, 8), (0xFFFFFFFF, 8), (0x5, 4), (0xABC, 2), (0, 0), (0x9A, 3)]:
    case("fmt_hex", "%#x, %d digits" % (v, digits), memory((SCR, b"\xcc" * 12)), SCR, v, digits)


def ring(head, tail):
    return memory((SCR, bytes(range(0x40, 0x60)) + struct.pack("<II", head, tail)))


# ring_put / ring_get: empty, full, wrapping
for head, tail in [(0, 0), (5, 3), (31, 0), (30, 31), (31, 31), (0, 1), (17, 17), (3, 5)]:
    case("ring_put", "head %d, tail %d" % (head, tail), ring(head, tail), SCR, 0xA7)
    case("ring_get", "head %d, tail %d" % (head, tail), ring(head, tail), SCR)


def commands(m):
    logging_stubs(m, ("cmd_led", 0, 1), ("cmd_beep", 0, 1), ("cmd_mode", 1, 1), ("cmd_volume", 7, 1))


# dispatch: every command through the original's table (stubbed), and numbers past it
for cmd in list(range(6)) + [0xFFFFFFFF]:
    case("dispatch", "command %#x" % cmd, commands, cmd, 0x1234)


def uart(ready_from):
    def setup(m):
        m.poll_read(HW["uart"] + 4, 1, 0)
        m.poll_read(HW["uart"] + 4, ready_from, 1)
    return setup


# uart_putc: ready at once, and after waiting two reads
for c in (0x41, 0x0A, 0xFF, 0x00):
    case("uart_putc", "char %#x" % c, uart(1), c)
case("uart_putc", "waits two reads", uart(3), 0x42)

# strcmp: equal, shorter, longer, bytes above 0x7f
for a, b in [(b"", b""), (b"a", b""), (b"", b"a"), (b"abc", b"abc"), (b"abc", b"abd"), (b"\xff", b"\x01"),
             (b"ab", b"abc"), (b"b", b"a")]:
    case("strcmp", "%r vs %r" % (a, b), memory((SCR, a + b"\0"), (SCR + 0x80, b + b"\0")), SCR, SCR + 0x80)
