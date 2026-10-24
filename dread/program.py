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
import bisect
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


def read_code_ranges(path, default_isa, isas=("a32", "t32")):
    """code_ranges.tsv as [(start, end, isa)]: instruction runs, end exclusive, with an optional
    third column naming one of the architecture's instruction sets `isas` (default: the image's
    isa)."""
    out = []
    for number, text in _lines(path):
        f = text.split()
        if len(f) < 2:
            raise TargetError("%s:%d: needs start and end" % (path, number))
        isa = f[2] if len(f) > 2 else default_isa
        if isa not in isas:
            raise TargetError("%s:%d: isa is %s, not %s" % (path, number, " or ".join(isas), isa))
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


def write_annotation(path, address, lines):
    """Replace the annotation of one address in annotations.txt by `lines` (none: remove it). The
    other lines, comments included, stay as they are; the new lines take the place of the old ones,
    or go to the end."""
    old = []
    if os.path.exists(path):
        with open(path) as f:
            old = f.read().splitlines(True)
    if old and not old[-1].endswith("\n"):
        old[-1] += "\n"
    new = ["0x%x | %s\n" % (address, line.strip()) for line in lines if line.strip()]
    out, placed = [], False
    for line in old:
        head = line.split("|", 1)[0].strip() if "|" in line and not line.lstrip().startswith("#") else ""
        try:
            mine = bool(head) and int(head, 0) == address
        except ValueError:
            mine = False
        if mine:
            if not placed:
                out += new
                placed = True
            continue
        out.append(line)
    if not placed:
        if not old:
            out.append("# ADDRESS | text: notes on the code at an address, several lines per address.\n")
        out += new
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        f.writelines(out)


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


ROOM = 0xFFFF       # extent_owners: room the overlay may fill, nobody's

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
        self.copies = [c for c in self.image.startup() if c.kind in ("code", "data")]    # in the image
        self.isa = self.arch.isa
        self.code = {}                  # instruction address -> (length, isa)
        self.ranges = read_code_ranges(self.image.table("code_ranges"), self.isa, self.arch.isas())
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
        self._insns = {}                # run address -> capstone instruction (see insn())

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

    def instruction_halves(self):
        """Every halfword address an instruction covers (not only where it starts): a load of the
        second half of a 32-bit Thumb instruction is a load of code too (a case of strcmp compared a
        string at an address inside strcmp itself, and the rebuilt image has other bytes there)."""
        if getattr(self, "_halves", None) is None:
            self._halves = frozenset(a + k for a, (size, _) in self.code.items() for k in range(0, max(size, 2), 2))
        return self._halves

    def icall_sites(self):
        """{address: size} of every call through a register in the image's functions (the layer's
        flow kind "icall"), and of every tail call through a pointer (an indirect jump that
        dread.switches.pointer_tail finds is one: the lifter makes it a call), for
        dread.emu.Machine.watch_calls and the generator's pointer stub."""
        if getattr(self, "_icall_sites", None) is None:
            from dread import switches
            sites = {}
            for fn in self.functions:
                for a in fn.insns:
                    insn = self.insn(a)
                    kind = self.arch.flow(insn)[0] if insn is not None else None
                    if kind == "icall" or (kind == "ijump" and switches.pointer_tail(self, fn, a)):
                        sites[a] = self.insn_size(a)
            self._icall_sites = sites
        return self._icall_sites

    def writable_end(self):
        """The end of what a case may not store into: the image, and behind it the room the overlay
        fills up to the image's limit."""
        end = self.base + len(self.data)
        return max(end, self.image.limit or end)

    def extent_owners(self):
        """An array with one entry per halfword of the image: 1 + the index in self.functions of the
        function whose extent holds it (its instructions and the literal pools behind them, up to the
        next function: dread.overlay may place C over all of it), 0 elsewhere. A tail behind the last
        instruction that a word of the image points into is data the overlay keeps (a string between
        two functions), not part of the extent. The room the overlay fills besides (the image's free
        ranges, and behind the image up to its limit) is ROOM, nobody's. With it a load of code as
        data is told from a load of a function's own constants (dread.emu.Machine.trace_reads): cases
        of FreeRTOS that pointed into another function's pool, or behind the image, read other bytes
        in the rebuilt image. A pool another function loads from with a pc-relative load or adr (a
        compiler's constant tables shared by several functions) ends the extent in front of it: the
        overlay keeps such a pool while a reader is original, and its loads are constants, not code; so
        is the table behind a call of a switch helper (insn.dispatch_call), which the helper reads for
        its caller."""
        if getattr(self, "_owners", None) is None:
            import array
            from dread.overlay import EXTENT_TAIL
            end = self.base + len(self.data)
            limit = max(end, self.image.limit or end)
            entries = sorted(f.entry for f in self.functions if f.insns)
            words = sorted({v & ~1 for v in struct.unpack_from("<%dI" % (len(self.data) // 4), self.data)
                            if self.base < v < end})
            owners = array.array("H", bytes(2 * ((limit - self.base + 1) // 2)))
            for lo, hi in list(self.image.free) + [(end, limit)]:
                for a in range(max(lo, self.base) & ~1, min(hi, limit), 2):
                    owners[(a - self.base) >> 1] = ROOM
            readers = {}                # word address -> the functions that load it pc-relatively
            literal, pc_address = getattr(self.arch, "literal", None), getattr(self.arch, "pc_address", None)
            if literal is not None:
                for k, f in enumerate(self.functions):
                    for a in f.insns:
                        insn = self.insn(a)
                        if insn is None:
                            continue
                        ref = literal(insn, f.isa)
                        ref = ref[0] if ref else (pc_address(insn, f.isa) if pc_address is not None else None)
                        if ref is not None:
                            readers.setdefault(ref & ~3, set()).add(k)
            shared = sorted(readers)
            for k, f in enumerate(self.functions):
                if not f.insns:
                    continue
                last = self.end(f)
                i = bisect.bisect_right(entries, f.entry)
                stop = min(entries[i], end) if i < len(entries) else last
                if stop - last > EXTENT_TAIL or stop < last:
                    stop = last
                j = bisect.bisect_left(words, last)
                if j < len(words) and words[j] < stop:
                    stop = last
                j = bisect.bisect_left(shared, last)
                while j < len(shared) and shared[j] < stop:
                    if readers[shared[j]] - {k}:        # (a pool of another function's too)
                        stop = shared[j]
                        break
                    j += 1
                for a in range(max(f.insns[0] & ~1, self.base), min(stop, end), 2):
                    if owners[(a - self.base) >> 1] != ROOM:
                        owners[(a - self.base) >> 1] = min(k + 1, ROOM - 1)
            # A pool inside a function (behind a branch, where an old compiler puts its constants and
            # its neighbours load them too) is no part of the extent where another function loads
            # from it either: the overlay builds C only over runs of instructions there and keeps
            # the pool, and its loads are constants, not code (an old ARM compiler's code, whose
            # functions load from pools inside each other, lost almost every generated input so).
            for ref, ks in readers.items():
                for a in (ref, ref + 2):
                    h = (a - self.base) >> 1
                    if 0 <= h < len(owners) and owners[h] not in (0, ROOM) and a not in self.code \
                            and ks - {owners[h] - 1}:
                        owners[h] = 0
            # The table behind a call of a switch helper (insn.dispatch_call: GCC's Thumb-1
            # __gnu_thumb1_case_*, another function, reads it on its caller's behalf through lr) is no
            # part of the extent either: the helper's loads of it are the caller's constants, not code.
            # It runs from behind the call to the function's next instruction, the first case (the call
            # does not fall through). Until 2026-10-25 every input that reached such a dispatch was
            # refused as one that loads instructions as data, and no case ran the switch.
            for k, f in enumerate(self.functions):
                for i, a in enumerate(f.insns[:-1]):
                    insn = self.insn(a)
                    if insn is None or not getattr(insn, "dispatch_call", False):
                        continue
                    for b in range(a + self.code[a][0], f.insns[i + 1], 2):
                        h = (b - self.base) >> 1
                        if 0 <= h < len(owners) and owners[h] == min(k + 1, ROOM - 1) and b not in self.code:
                            owners[h] = 0
            self._owners = owners
        return self._owners

    def end(self, fn):
        """The address behind a function's last instruction (not behind a pool that follows it)."""
        if not fn.insns:
            return fn.entry
        return fn.insns[-1] + self.code[fn.insns[-1]][0]

    def _decode_range(self, start):
        """Decode the code range that holds `start` in one pass, from its first instruction:
        capstone follows Thumb IT blocks only within one pass, so an instruction decoded alone
        loses the condition its IT instruction gives it."""
        for lo, hi, isa in self.ranges:
            if lo <= start < hi:
                break
        else:
            return
        md = self.arch.disassembler(isa)
        link = getattr(self.arch, "link_pair", None)
        adapt = getattr(self.arch, "adapt", None)   # (ARM: an immediate with an explicit rotation folded)
        a = lo
        while a < hi:
            try:
                code = self.read(a, hi - a)
            except TargetError:
                return
            got, prev = False, None
            for insn in md.disasm(code, a):
                if adapt is not None:
                    insn = adapt(insn)
                if link is not None and link(prev, insn, isa):
                    insn.link_call = True       # a call through a register (arch.link_pair)
                self._insns[insn.address] = insn
                a = insn.address + insn.size
                got, prev = True, insn
            if a < hi:                      # stopped at something that does not decode
                self._insns.setdefault(a, None)
                a += self.code[a][0] if a in self.code else self.arch.min_insn(isa)
            if not got and a >= hi:
                break

    def insn(self, address):
        """The instruction at `address` (capstone CsInsn), decoded with its code range so that
        Thumb IT conditions are right; None if it does not decode."""
        if address not in self._insns:
            self._decode_range(address)
            if address not in self._insns:
                size = self.code[address][0] if address in self.code else 4
                self._insns[address] = self.arch.decode(self.read(address, size), address, self.isa_at(address))
        insn = self._insns[address]
        if insn is not None and not getattr(insn, "dread_marked", False):
            insn.dread_marked = True
            mark = getattr(self.arch, "mark_call", None)
            if mark is not None:            # calls that are not ordinary ones (arch.mark_call)
                mark(self, insn)
        return insn

    def decode(self, address, isa=None):
        """The instruction at `address`; as insn(), or decoded alone in `isa` when given (an
        instruction inside a Thumb IT block then shows no condition)."""
        if isa is None or isa == self.isa_at(address):
            return self.insn(address)
        size = self.code[address][0] if address in self.code else 4
        return self.arch.decode(self.read(address, size), address, isa)

    def instructions(self, fn):
        """The function's instructions, decoded, in address order (None for one that does not
        decode)."""
        return [self.insn(a) for a in fn.insns]

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
