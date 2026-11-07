"""Ground truth for the ARC decoder from binutils' objdump: tests/data/arc_objdump.tsv and the live
comparison of tests/test_arch_arc.py.

`objdump(codes)` disassembles instructions with arc-elf32-objdump -m ARC600 (each in a slot of its
own, padded with nop_s) and gives (size, mnemonic, operands) of each, objdump's comments taken off.
`python tests/arc_vectors.py` writes the vectors file anew: random 16-bit and 32-bit words (32-bit
ones with long immediates among them), a fixed seed, each row the bytes (with the long immediate
that may follow) and objdump's size, mnemonic and operands; row k lies at BASE + k * SLOT (the
address is not written: a column of addresses matched private projects' addresses by chance in
the leak check). objdump's .word and
.short rows (no instruction) are kept: the decoder must find none there either, or say "unknown".
"""
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VECTORS = os.path.join(HERE, "data", "arc_objdump.tsv")
PREFIX = "arc-elf32-"
SLOT = 16
NOP_S = bytes.fromhex("e078")
BASE = 0x10000
LIMM = bytes.fromhex("12345678")


def available():
    return shutil.which(PREFIX + "objdump") is not None and shutil.which(PREFIX + "objcopy") is not None


def word32(w):
    """A 32-bit instruction word's bytes: its high halfword first, each halfword little-endian."""
    return bytes([(w >> 16) & 0xFF, (w >> 24) & 0xFF, w & 0xFF, (w >> 8) & 0xFF])


def objdump(codes, base=BASE):
    """[(size, mnemonic, operands)] of the instruction at the start of each of codes (bytes)."""
    blob = bytearray()
    for c in codes:
        blob += c + NOP_S * ((SLOT - len(c)) // 2)
    with tempfile.TemporaryDirectory() as d:
        raw, elf = os.path.join(d, "x.bin"), os.path.join(d, "x.elf")
        with open(raw, "wb") as fh:
            fh.write(blob)
        subprocess.run([PREFIX + "objcopy", "-I", "binary", "-O", "elf32-littlearc", "-B", "arc",
                        "--rename-section", ".data=.text,code,alloc,load,contents,readonly",
                        "--change-addresses", str(base), raw, elf], check=True)
        out = subprocess.run([PREFIX + "objdump", "-d", "-z", "-w", "-m", "ARC600", elf],
                             capture_output=True, text=True, check=True).stdout
    found = {}
    for line in out.splitlines():
        m = re.match(r"\s*([0-9a-f]+):\t([0-9a-f ]+?)\s*\t(\S*)\s*(.*)$", line)
        if m and (int(m.group(1), 16) - base) % SLOT == 0:
            ops = re.sub(r"\s*;.*", "", m.group(4)).strip()
            found[(int(m.group(1), 16) - base) // SLOT] = (len(m.group(2).replace(" ", "")) // 2, m.group(3), ops)
    return [found.get(i) for i in range(len(codes))]


def random_codes(rng, n16, n32):
    codes = [bytes([h & 0xFF, h >> 8]) + LIMM for h in (rng.randrange(0x6000, 0x10000) for _ in range(n16))]
    for _ in range(n32):
        major = rng.choice([0, 1, 1, 2, 3, 4, 4, 4, 4, 4, 5, 6, 8])
        w = (major << 27) | rng.getrandbits(27)
        if rng.random() < 0.3:              # a field with the long-immediate code 0x3e
            k = rng.choice([0, 6, 12, 24])
            if k == 24:
                w = (w & ~(7 << 24) & ~(7 << 12)) | (6 << 24) | (7 << 12)
            else:
                w = (w & ~(0x3F << k)) | (0x3E << k)
        codes.append(word32(w) + LIMM)
    return codes


def write(path=VECTORS, seed=2026, n16=1500, n32=3500):
    codes = random_codes(random.Random(seed), n16, n32)
    rows = ["# bytes\tsize\tmnemonic\toperands  (arc-elf32-objdump -m ARC600; row k at BASE + k * SLOT;"
            " tests/arc_vectors.py)"]
    for code, o in zip(codes, objdump(codes)):
        rows.append("%s\t%d\t%s\t%s" % (code.hex(), o[0], o[1], o[2]))
    with open(path, "w") as fh:
        fh.write("\n".join(rows) + "\n")
    return len(codes)


def read(path=VECTORS):
    out = []
    with open(path) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            code, size, mnem, ops = line.rstrip("\n").split("\t")
            out.append((bytes.fromhex(code), BASE + len(out) * SLOT, int(size), mnem, ops))
    return out


if __name__ == "__main__":
    print(write(*sys.argv[1:2]), "vectors written")
