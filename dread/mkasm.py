"""dread asm: a listing of an image that the target's assembler rebuilds byte for byte.

Writes `<out>/firmware.s`, which includes one file per region (64 KiB by default) with every byte
of the image as an instruction or as data: labels at function entries and at the targets of
branches and pc-relative loads, the names from the function table, the annotations as comments.
The layout is fixed: every address keeps its value. Branches and literal loads use labels, but
absolute addresses in literal pools and tables stay numbers, so code cannot move in this listing.

Code the startup copies to RAM is written where its bytes are stored. Its branches and literal
loads keep labels when source and target lie in the same copy (the relative distance is the same at
the stored and the run address). A branch from such code to elsewhere, or into it, is written as a
raw instruction (its bytes).

`--verify` assembles the listing, links it at the image's base and compares it with the image. An
instruction the assembler encodes differently, or rejects, is added to the image's `asm_raw_words`
table and written as raw bytes from then on; the loop repeats until the rebuild is identical. An
instruction assembled at another length shifts everything behind it, so only the first mismatch of
a region whose next instruction does not match either is taken per pass.

The syntax is the architecture layer's (`arch.asm()`): GNU as for ARM (Thumb branches and literal
loads carry an explicit width, `.w` or `.n`), clang's integrated assembler for RISC-V
(`.option exact`: nothing is compressed or relaxed behind the listing's back).

    dread asm [--target DIR] [--image NAME] [--out DIR] [--verify] [--region BYTES] [--raw FILE]
"""
import argparse
import os
import re
import struct
import subprocess
import sys

from dread import target as tgt
from dread.program import Program

ASM_ERR_RE = re.compile(r"^(?:.*/)?([0-9a-f]{8}\.s):(\d+):(?:\d+:)? (?:Error|error)", re.M)
LABEL_CHARS = re.compile(r"[^A-Za-z0-9_.$]")


def load_raw_words(path):
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {int(l.split()[0], 16) for l in f if l.strip() and not l.startswith("#")}


def save_raw_words(path, raw):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("# Instructions written as raw bytes because the assembler encodes them differently from\n"
                "# the original bytes (stored addresses). Maintained by `dread asm --verify`.\n")
        for addr in sorted(raw):
            f.write("0x%x\n" % addr)


class Listing:
    """The listing of one image, in stored addresses (where the bytes lie)."""

    def __init__(self, prog, raw, title):
        self.prog, self.raw, self.title = prog, raw, title
        self.arch, self.image = prog.arch, prog.image
        self.syntax = prog.arch.asm()           # the assembler's dialect (dread.arch.*.Asm)
        self.base, self.data = prog.base, prog.data
        self.end = self.base + len(self.data)
        self.code = {}                      # stored address -> (run address, length, isa)
        for run, (length, isa) in prog.code.items():
            st = prog.stored(run)
            if self.base <= st and st + length <= self.end:
                self.code[st] = (run, length, isa)
        self.funcs = {}                     # stored address -> (label, function)
        seen = set()
        for fn in prog.functions:
            st = prog.stored(fn.entry)
            if not (self.base <= st < self.end):
                continue
            label = LABEL_CHARS.sub("_", fn.name)
            if label in seen or not label or label[0].isdigit():
                label = "%s_at_%x" % (label, st)
            seen.add(label)
            self.funcs[st] = (label, fn)
        self.notes = {}
        for a, lines in prog.annotations().items():
            self.notes.setdefault(prog.stored(a), []).extend(lines)
        self.ins = {}                       # stored address -> (mnemonic, operands, comment)
        self.labels = set()                 # stored addresses that need a local label
        self.line_addr = {}                 # (file, line) -> stored address

    # ---- helpers ----

    def space(self, run):
        """Which copy a run address belongs to (index), or -1 for the image itself."""
        for i, c in enumerate(self.prog.copies):
            if c.run <= run < c.run + c.size:
                return i
        return -1

    def has(self, st, n=1):
        return self.base <= st and st + n <= self.end

    def word(self, st):
        return struct.unpack_from("<I", self.data, st - self.base)[0]

    def label(self, st):
        return self.funcs[st][0] if st in self.funcs else "L_%08x" % st

    def labelable(self, st):
        """Whether a label can stand at `st`: at an instruction, or in data; not inside an
        instruction (a branch into the second half of a 32-bit Thumb instruction)."""
        if st in self.code:
            return True
        return not any(st - k in self.code and self.code[st - k][1] > k for k in (1, 2, 3))

    def cstring(self, st, limit=48):
        if not self.has(st):
            return None
        chunk = self.data[st - self.base:st - self.base + limit + 1]
        nul = chunk.find(b"\0")
        text = chunk[:nul] if nul >= 0 else chunk[:limit]
        if len(text) >= 3 and all(0x20 <= c < 0x7F for c in text):
            return text.decode() + ("" if nul >= 0 else "...")
        return None

    def describe_value(self, value):
        st = self.prog.stored(value & ~1)
        if (value & ~1) in self.prog.by_entry:
            return self.prog.by_entry[value & ~1].name
        text = self.cstring(st) if self.has(st) else None
        return '"%s"' % text if text else ""

    def raw_text(self, st):
        run, length, isa = self.code[st]
        return self.syntax.raw(self.data[st - self.base:st - self.base + length], isa)

    # ---- decoding ----

    def decode(self):
        prog = self.prog
        for st in sorted(self.code):
            if st in self.raw:
                continue
            run, length, isa = self.code[st]
            insn = prog.insn(run)
            if insn is None or insn.size != length:
                self.raw.add(st)
                continue
            got = self.syntax.operands(self, insn, run, st, isa)
            if got is None:
                self.raw.add(st)
            else:
                self.ins[st] = got

    def render_ins(self, st):
        """ops: text, ("label", the operands in front, target) for an operand that is a place, or
        ("sub", pattern, text, target) for a place inside the operands (the pattern's match becomes
        the label)."""
        mnem, ops, comment = self.ins[st]
        if isinstance(ops, tuple):
            if ops[0] == "label":
                rest = ops[1].rstrip(", ")
                ops = (rest + ", " if rest else "") + self.label(ops[2])
            else:
                ops = ops[1].sub(self.label(ops[3]), ops[2])
        text = "\t%-7s %s" % (mnem, ops)
        return text + ("\t%s %s" % (self.syntax.comment, comment) if comment else "")

    # ---- writing ----

    def marked(self, a):
        return a in self.labels or a in self.funcs or a in self.notes

    def emit_label(self, st, out):
        c = self.syntax.comment
        if st in self.funcs:
            label, fn = self.funcs[st]
            where = "0x%x" % st if fn.entry == st else "stored at 0x%x, runs at 0x%x" % (st, fn.entry)
            out.append("")
            out.append("%s ---- %s (%s, %d instruction bytes) ----" % (c, fn.name, where, fn.size))
        for text in self.notes.get(st, ()):
            out.append("%s %s" % (c, text))
        if st in self.funcs:
            label, fn = self.funcs[st]
            out += self.syntax.function_lines(label, fn.isa, st in self.code)
            out.append("%s:" % label)
        elif st in self.labels:
            out.append("L_%08x:" % st)

    def render_data(self, start, end, out):
        """Bytes [start, end) as strings, words, halfwords and bytes."""
        data, base = self.data, self.base
        d, c = self.syntax.directives, self.syntax.comment
        pos = start
        while pos < end:
            if self.marked(pos):
                self.emit_label(pos, out)
            limit = end
            for nxt in range(pos + 1, min(end, pos + 64)):
                if self.marked(nxt):
                    limit = nxt
                    break
            off = pos - base
            run = 0
            while pos + run < limit and 0x20 <= data[off + run] < 0x7F:
                run += 1
            if run >= 4 and pos + run < limit and data[off + run] == 0:
                text = data[off:off + run].decode().replace("\\", "\\\\").replace('"', '\\"')
                out.append('\t%-7s "%s"' % (d["text"], text))
                pos += run + 1
            elif pos % 4 == 0 and pos + 4 <= limit:
                words = []
                while len(words) < 4 and pos + 4 <= limit and not (words and self.marked(pos)):
                    o = pos - base
                    r = 0
                    while pos + r < limit and 0x20 <= data[o + r] < 0x7F:
                        r += 1
                    if words and r >= 4 and pos + r < limit and data[o + r] == 0:
                        break
                    words.append(self.word(pos))
                    pos += 4
                line = "\t%-7s " % d["word"] + ", ".join("0x%08x" % w for w in words)
                if len(words) == 1:
                    what = self.describe_value(words[0])
                    if what:
                        line += "\t%s %s" % (c, what)
                out.append(line)
            elif pos % 2 == 0 and pos + 2 <= limit and (pos + 2) % 4 == 0:
                out.append("\t%-7s 0x%04x" % (d["half"], struct.unpack_from("<H", data, off)[0]))
                pos += 2
            else:
                n = min(limit, (pos | 3) + 1) - pos
                out.append("\t%-7s " % d["byte"] + ", ".join("0x%02x" % b for b in data[off:off + n]))
                pos += n

    def write(self, outdir, region):
        os.makedirs(outdir, exist_ok=True)
        for name in os.listdir(outdir):
            if name.endswith(".s"):
                os.remove(os.path.join(outdir, name))
        first_isa = self.code[min(self.code)][2] if self.code else self.arch.isa
        regions = []
        isa = first_isa
        for rstart in range(self.base - self.base % region, self.end, region):
            lo, hi = max(rstart, self.base), min(rstart + region, self.end)
            fname = "%08x.s" % rstart
            cm = self.syntax.comment
            out = ["%s %s, 0x%08x-0x%08x. Generated by dread asm; change the tables and regenerate"
                   % (cm, self.title, lo, hi), "%s rather than editing this file." % cm, ""]
            out += self.syntax.switch(isa)
            addr = lo
            while addr < hi:
                c = self.code.get(addr)
                if c is not None:
                    if c[2] != isa:
                        isa = c[2]
                        out += self.syntax.switch(isa)
                    self.emit_label(addr, out)
                    self.line_addr[(fname, len(out) + 1)] = addr
                    if addr in self.ins:
                        out.append(self.render_ins(addr))
                    else:
                        directive, value = self.raw_text(addr)
                        insn = self.prog.insn(c[0])
                        text = "%s %s" % (insn.mnemonic, insn.op_str) if insn else "?"
                        out.append("\t%-7s %s\t%s %s" % (directive, value, self.syntax.comment, text))
                    addr += c[1]
                else:
                    stop = addr
                    while stop < hi and stop not in self.code:
                        stop += 1
                    self.render_data(addr, stop, out)
                    addr = stop
            with open(os.path.join(outdir, fname), "w") as f:
                f.write("\n".join(out) + "\n")
            regions.append(fname)
        with open(os.path.join(outdir, "firmware.s"), "w") as f:
            c = self.syntax.comment
            f.write("%s %s, image %s at 0x%x.\n%s Generated by dread asm.\n\n" % (c, self.title, self.image.name,
                                                                                 self.base, c))
            f.write("\n".join(self.syntax.prelude(first_isa)) + "\n\t.section .text, \"ax\"\n\n")
            f.writelines('\t.include "%s"\n' % r for r in regions)


def build(target, base, outdir, builddir):
    """Assemble and link; (bytes or None, assembler messages)."""
    os.makedirs(builddir, exist_ok=True)
    obj, elf, binf = (os.path.join(builddir, n) for n in ("firmware.o", "firmware.elf", "payload.bin"))
    compiler = target.compiler
    r = subprocess.run(compiler.assembler() + ["-I", outdir, "-o", obj, os.path.join(outdir, "firmware.s")],
                       capture_output=True, text=True)
    if r.returncode:
        return None, r.stderr
    for cmd in (compiler.command("ld") + compiler.ldflags + ["-Ttext=0x%x" % base, "-e", "0x%x" % base, "-o", elf, obj],
                compiler.command("objcopy") + ["-O", "binary", "-j", ".text", elf, binf]):
        r2 = subprocess.run(cmd, capture_output=True, text=True)
        if r2.returncode:
            return None, r.stderr + r2.stderr
    with open(binf, "rb") as f:
        return f.read(), r.stderr


def mismatches(listing, binary):
    """The stored addresses of instructions that came out differently: every one whose next
    instruction is right again (same length, other bytes), and per region the first of the rest
    (a length change shifts what follows, so the rest is not evidence yet)."""
    data, base = listing.data, listing.base
    bad, first = set(), {}
    for st in sorted(listing.ins):
        length = listing.code[st][1]
        o = st - base
        if binary[o:o + length] == data[o:o + length]:
            continue
        nxt = st + length
        if nxt in listing.code and binary[nxt - base:nxt - base + 2] == data[nxt - base:nxt - base + 2]:
            bad.add(st)
        else:
            first.setdefault(st // 0x10000, st)
    return bad | set(first.values())


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread asm", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--out", help="output directory (default: asm, or asm/IMAGE for a target "
                        "with several images)")
    parser.add_argument("--build", help="work directory of --verify (default: BUILD/asm-verify/IMAGE)")
    parser.add_argument("--region", type=lambda v: int(v, 0), default=0x10000, help="bytes per file")
    parser.add_argument("--verify", action="store_true",
                        help="assemble, compare with the image, grow asm_raw_words until identical")
    parser.add_argument("--passes", type=int, default=40, help="at most this many --verify passes")
    parser.add_argument("--raw", metavar="FILE", help="the table of raw instructions (default: the image's "
                        "asm_raw_words table)")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        prog = Program(target, image)
    except tgt.TargetError as e:
        print("dread asm: %s" % e, file=sys.stderr)
        return 1
    outdir = args.out or target.path("asm" if len(target.images) == 1 else os.path.join("asm", image.name))
    builddir = args.build or os.path.join(target.build, "asm-verify", image.name)
    raw_path = args.raw or image.table("asm_raw_words")
    raw = load_raw_words(raw_path)
    title = target.title
    for attempt in range(1, args.passes + 1):
        listing = Listing(prog, set(raw), title)
        listing.decode()
        listing.write(outdir, args.region)
        raw = listing.raw
        print("pass %d: %d instructions, %d raw, %d functions, %d annotated addresses"
              % (attempt, len(listing.ins), len(raw & set(listing.code)), len(listing.funcs), len(listing.notes)))
        if not args.verify:
            return 0
        binary, stderr = build(target, image.base, outdir, builddir)
        if binary is None:
            bad = set()
            for fname, line in ASM_ERR_RE.findall(stderr):
                addr = listing.line_addr.get((fname, int(line)))
                if addr is not None:
                    bad.add(addr)
            if not bad:
                sys.stderr.write(stderr[:4000])
                print("dread asm: the assembler failed with errors that do not map to instructions",
                      file=sys.stderr)
                return 1
            print("  the assembler rejected %d instructions" % len(bad))
        elif binary == prog.data:
            save_raw_words(raw_path, raw & set(listing.code))
            print("  the rebuilt image is byte-identical to %s" % image.original)
            return 0
        else:
            if len(binary) < len(prog.data):
                binary = binary + b"\0" * (len(prog.data) - len(binary))
            bad = mismatches(listing, binary)
            if not bad:
                print("dread asm: data bytes differ although every instruction matches", file=sys.stderr)
                return 1
            print("  %d instructions assembled to other bytes" % len(bad))
        raw |= bad
        save_raw_words(raw_path, raw & set(listing.code))
    print("dread asm: no identical rebuild after %d passes" % args.passes, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
