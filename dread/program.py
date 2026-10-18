"""The program database: the functions of one image and the instructions they consist of.

Reads an image's tables (functions.tsv, code_ranges.tsv, symbols, annotations, data ranges) and its
bytes, and answers what every tool asks: which functions there are, which instructions belong to
each, what bytes are at an address (also for code the startup copies to RAM and runs there), and
what is known about an address.

The function table comes from the analysis (Ghidra's export, or `dread import-elf` for an image with
symbols): one row per function with its entry and its instruction bytes. Literal pools and other
data inside a function do not count in that size. A function's instructions are found by walking
the code ranges from the entry until that many instruction bytes are collected; the walk stops at
the next function's entry. This walk is the one definition of a function's extent, and every tool
takes it from here (docs/formats.md, "functions.tsv"). Entry + size is not the end of a function
that has pools inside.

Code the startup copies to RAM is analysed at its run address (where it executes); `read()` and
`stored()` map such addresses to where the bytes lie in the image.
"""
import os
import struct

from dread.target import TargetError, h

MAX_SPAN = 0x10000          # the walk gives up this far behind an entry (a table that lies)


def _lines(path):
    """The non-empty lines of a table without comments; nothing if the file does not exist."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for number, line in enumerate(f, 1):
            text = line.split("#", 1)[0].rstrip() if not line.startswith("#") else ""
            if text.strip():
                yield number, text


def _int(text, path, number):
    try:
        return int(text, 0)
    except ValueError:
        raise TargetError("%s:%d: %r is not a number" % (path, number, text))


def read_functions(path):
    """functions.tsv as [(entry, name, size)], in file order. A first line whose first field is
    not a number is the header."""
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as f:
        rows = [line.rstrip("\n").split("\t") for line in f]
    for number, row in enumerate(rows, 1):
        if not row or not row[0] or row[0].startswith("#"):
            continue
        if number == 1 and not row[0].lower().startswith("0x") and not row[0].isdigit():
            continue
        if len(row) < 3:
            raise TargetError("%s:%d: needs address, name and bytes" % (path, number))
        out.append((_int(row[0], path, number), row[1], _int(row[2], path, number)))
    return out


def read_code_ranges(path, default_isa):
    """code_ranges.tsv as [(start, end, isa)]: instruction runs, end exclusive, with an optional
    third column a32 or t32 (default: the image's isa)."""
    out = []
    for number, text in _lines(path):
        f = text.split()
        if len(f) < 2:
            raise TargetError("%s:%d: needs start and end" % (path, number))
        isa = f[2] if len(f) > 2 else default_isa
        if isa not in ("a32", "t32"):
            raise TargetError("%s:%d: isa is a32 or t32, not %s" % (path, number, isa))
        out.append((_int(f[0], path, number), _int(f[1], path, number), isa))
    return out


def read_symbols(path):
    """symbols.txt as [(kind, address, name)]: kind "f" (function), "d" (data) or "-" (a seed for
    the analysis without a name, name None)."""
    out = []
    for number, text in _lines(path):
        f = text.split()
        if f[0] not in ("f", "d", "-") or len(f) < 2:
            raise TargetError("%s:%d: expected 'f|d|- ADDRESS [NAME]'" % (path, number))
        out.append((f[0], _int(f[1], path, number), f[2] if len(f) > 2 and f[2] != "-" else None))
    return out


def read_annotations(path):
    """annotations.txt as {address: [line, ...]} ("ADDRESS | text", several lines per address)."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for number, line in enumerate(f, 1):
            if "|" not in line or line.lstrip().startswith("#"):
                continue
            head, text = line.split("|", 1)
            head = head.strip()
            if not head:
                continue
            out.setdefault(_int(head, path, number), []).append(text.strip())
    return out


def read_ranges(path):
    """A range table ("START END reason" or "START-END reason") as [(start, end, reason)], end
    exclusive. Used for data_ranges.txt, asm_only.txt and left_out.txt."""
    out = []
    for number, text in _lines(path):
        f = text.split(None, 1)
        if "-" in f[0] and not f[0].startswith("-"):
            lo, hi = f[0].split("-", 1)
            reason = f[1] if len(f) > 1 else ""
        else:
            lo = f[0]
            rest = f[1].split(None, 1) if len(f) > 1 else []
            if rest and rest[0].lower().startswith("0x"):
                hi, reason = rest[0], rest[1] if len(rest) > 1 else ""
            else:
                hi, reason = None, f[1] if len(f) > 1 else ""
        start = _int(lo, path, number)
        end = _int(hi, path, number) if hi is not None else None
        out.append((start, end, reason))
    return out


class Function:
    """One function: name, entry, size (instruction bytes), isa and its instruction addresses."""

    def __init__(self, name, entry, size, isa, insns):
        self.name, self.entry, self.size, self.isa, self.insns = name, entry, size, isa, insns

    def __repr__(self):
        return "Function(%s, %s, %d bytes, %s)" % (self.name, h(self.entry), self.size, self.isa)


class Program:
    """The functions and code of one image of a target."""

    def __init__(self, target, image=None, payload=None):
        self.target = target
        self.image = image if hasattr(image, "base") else target.image(image)
        self.arch = self.image.arch
        self.base = self.image.base
        self.data = payload if payload is not None else self.image.payload()
        self.copies = [c for c in self.image.startup() if c.kind != "zero"]
        self.isa = self.arch.isa
        self.code = {}                  # instruction address -> (length, isa)
        self.ranges = read_code_ranges(self.image.table("code_ranges"), self.isa)
        for start, end, isa in self.ranges:
            self._add_range(start, end, isa)
        self.functions = []
        self.by_name = {}
        self.by_entry = {}
        rows = sorted(read_functions(self.image.table("functions")))
        entries = [entry for entry, _, _ in rows]
        for i, (entry, name, size) in enumerate(rows):
            nxt = entries[i + 1] if i + 1 < len(entries) else None
            fn = self._function(name, entry, size, nxt)
            if name in self.by_name:
                raise TargetError("%s: %s is listed twice" % (self.image.table("functions"), name))
            self.functions.append(fn)
            self.by_name[name] = fn
            self.by_entry[entry] = fn
        self._symbols = None
        self._annotations = None
        self._owner = None

    # ---- bytes ----

    def stored(self, address):
        """Where the byte at run address `address` lies in the image's address space: through the
        startup's copies for code or data that runs elsewhere; unchanged otherwise."""
        for c in self.copies:
            if c.run <= address < c.run + c.size:
                return c.stored + (address - c.run)
        return address

    def in_image(self, address, n=1):
        a = self.stored(address)
        return self.base <= a and a + n <= self.base + len(self.data)

    def read(self, address, n):
        """n bytes at run address `address` (raises TargetError outside the image)."""
        a = self.stored(address)
        offset = a - self.base
        if offset < 0 or offset + n > len(self.data):
            raise TargetError("%s is outside image %s" % (h(address), self.image.name))
        return self.data[offset:offset + n]

    def word(self, address):
        return struct.unpack("<I", self.read(address, 4))[0]

    def half(self, address):
        return struct.unpack("<H", self.read(address, 2))[0]

    # ---- code ----

    def _add_range(self, start, end, isa):
        step = self.arch.min_insn(isa)
        if start % step:
            raise TargetError("%s: range %s-%s is not aligned for %s"
                              % (self.image.table("code_ranges"), h(start), h(end), isa))
        a = start
        while a < end:
            if isa == "a32":
                n = 4
            else:
                try:
                    n = self.arch.insn_length(self.read(a, 2), isa)
                except TargetError:
                    raise TargetError("code range %s-%s lies outside the image" % (h(start), h(end)))
            self.code[a] = (n, isa)
            a += n

    def _function(self, name, entry, size, nxt):
        isa = self.code[entry][1] if entry in self.code else self.isa
        step = self.arch.min_insn(isa)
        limit = entry + MAX_SPAN if nxt is None else min(nxt, entry + MAX_SPAN)
        insns, got, a = [], 0, entry
        while got < size and a < limit:
            c = self.code.get(a)
            if c is None:
                a += step
                continue
            insns.append(a)
            got += c[0]
            a += c[0]
        return Function(name, entry, size, isa, insns)

    def insn_size(self, address):
        return self.code[address][0]

    def isa_at(self, address):
        c = self.code.get(address)
        return c[1] if c else self.isa

    def end(self, fn):
        """The address behind a function's last instruction (not behind a pool that follows it)."""
        if not fn.insns:
            return fn.entry
        return fn.insns[-1] + self.code[fn.insns[-1]][0]

    def decode(self, address, isa=None):
        """The instruction at `address` (capstone CsInsn) in the isa the code ranges say."""
        isa = isa or self.isa_at(address)
        size = self.code[address][0] if address in self.code else 4
        return self.arch.decode(self.read(address, size), address, isa)

    def instructions(self, fn):
        """The function's instructions, decoded, in address order (None for one that does not
        decode)."""
        return [self.decode(a, fn.isa) for a in fn.insns]

    # ---- names ----

    def symbols(self):
        if self._symbols is None:
            self._symbols = read_symbols(self.image.table("symbols"))
        return self._symbols

    def annotations(self):
        if self._annotations is None:
            self._annotations = read_annotations(self.image.table("annotations"))
        return self._annotations

    def name_at(self, address):
        """The function or data name at an address, or None."""
        fn = self.by_entry.get(address)
        if fn:
            return fn.name
        for kind, a, name in self.symbols():
            if a == address and name:
                return name
        return None

    def function_at(self, address):
        """The function one of whose instructions is at `address`, or None."""
        if self._owner is None:
            self._owner = {a: fn for fn in self.functions for a in fn.insns}
        return self._owner.get(address)
