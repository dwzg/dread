"""Check the MSP430 engine against GDB's MSP430 simulator (msp430-elf-run, part of TI's MSP430 GCC).

Random blocks of up to eight instructions (every two-operand and one-operand instruction but
those that move the stack or the program counter, byte and word, every addressing mode, the
constant generators, conditional jumps over the next instruction) start from random registers,
flags and memory. One program holds a batch of them: for each, a setup routine loads the block's
starting state from a table, the block runs, and a save routine stores the registers and sr and
prints them with the block's memory. The engine runs the same program from the same tables, setup
included, up to the end of each block. Registers, sr and memory must agree.

Pointers: r4 (BUF + 16) is the base of indexed operands, r5 (BUF + 8) the pointer of word
operations (@r5, @r5+), r6 (BUF + 20) that of byte operations (@r6, @r6+), so that no word access
is ever odd: the hardware ignores bit 0 of a word address, the simulator does not. r7-r15 hold
values. Not covered: push, call, reti, writes to pc, sp and sr.

The simulator differs from TI's MSP430x2xx family user's guide (SLAU144) in these places, which the
check steps around (tests/test_engines.py checks the engine against the guide there):
- it keeps V in sr bit 7 (SCG1) instead of bit 8: the starting states have V clear, and the
  simulator's bit 7 is read as V;
- sxt does not set C to not Z: a clrc follows every sxt;
- rrc sets V when C came in set (the guide: V cleared; the older MSP430x1xx guide, SLAU049: V set
  when the operand was positive and C set), and dadd sets V in a way of its own (the guide: V
  undefined; the engine leaves it), and sub never sets V (cmp and subc do): a bic #0x180, sr (V
  in both places) follows every rrc, dadd and sub;
- dadd on digits above 9 (undefined in the guide) carries the sum's tens, up to 3, where the engine
  adjusts each digit as decimal adjusters do (above 9: add 6, carry 1): dadd gets valid BCD only;
- it reads the destination before the source, so that add @r5+, 0(r5) reads the old address and
  writes the new one (hardware computes a destination address once): no @r5+ source with a
  destination through r5.

    python -m dread.engines.msp430_check [--blocks N] [--seed S] [--keep DIR] [--cc CC] [--run RUN]
"""
import argparse
import os
import random
import shutil
import subprocess
import sys
import tempfile

BUF = 32                    # bytes of memory the blocks use
DOUBLE = ("mov", "add", "addc", "sub", "subc", "cmp", "dadd", "bit", "bic", "bis", "xor", "and")
SINGLE = ("rrc", "rra", "swpb", "sxt")
JUMPS = ("jne", "jeq", "jnc", "jc", "jn", "jge", "jl")
EDGES = (0, 1, 2, 0x7F, 0x80, 0xFF, 0x100, 0x7FFF, 0x8000, 0x8001, 0xFFFE, 0xFFFF)
CG = (0, 1, 2, 4, 8, -1)
BATCH = 120                 # blocks per program (the tables live in its 32 KiB of flash)

C_SOURCE = r"""
#include <stdio.h>
extern void run_all(void);
unsigned short SAVE[13];
unsigned char BUF[%d];
void dump(void)
{
    int i;
    for (i = 0; i < 13; i++)
        printf("%%04x ", SAVE[i]);
    for (i = 0; i < (int)sizeof BUF; i++)
        printf("%%02x", BUF[i]);
    printf("\n");
}
int main(void)
{
    run_all();
    return 0;
}
""" % BUF

SETUP = """
setup:                      ; the block's table (in cur): BUF, r4-r14, sr, r15
    mov &cur, r15
    mov #BUF, r14
    mov #%d, r13
1:  mov @r15+, 0(r14)
    incd r14
    dec r13
    jnz 1b
    mov @r15+, r4
    mov @r15+, r5
    mov @r15+, r6
    mov @r15+, r7
    mov @r15+, r8
    mov @r15+, r9
    mov @r15+, r10
    mov @r15+, r11
    mov @r15+, r12
    mov @r15+, r13
    mov @r15+, r14
    mov @r15+, r2
    mov @r15, r15
    ret

save:                       ; sr first: nothing has touched the flags since the block
    mov r2, &SAVE+24
    mov r4, &SAVE+0
    mov r5, &SAVE+2
    mov r6, &SAVE+4
    mov r7, &SAVE+6
    mov r8, &SAVE+8
    mov r9, &SAVE+10
    mov r10, &SAVE+12
    mov r11, &SAVE+14
    mov r12, &SAVE+16
    mov r13, &SAVE+18
    mov r14, &SAVE+20
    mov r15, &SAVE+22
    call #dump
    ret
""" % (BUF // 2)


def value(rng):
    return rng.choice(EDGES) if rng.random() < 0.4 else rng.randrange(0x10000)


def imm(rng, byte):
    if rng.random() < 0.3:
        return "#%d" % rng.choice(CG)
    v = value(rng)
    return "#%d" % (v & 0xFF if byte else v)


def mem_operand(rng, byte, inc_ok):
    """A memory operand inside BUF (see the module's docstring for the pointers)."""
    step = 1 if byte else 2
    kind = rng.choice(["idx", "abs", "sym", "ind"] + (["inc"] if inc_ok else []))
    if kind == "idx":
        return "%d(r4)" % (rng.randrange(-16, 16, step))
    if kind == "abs":
        return "&BUF+%d" % rng.randrange(0, BUF, step)
    if kind == "sym":
        return "BUF+%d" % rng.randrange(0, BUF, step)
    pointer = "r6" if byte else "r5"
    return "@%s+" % pointer if kind == "inc" else "@" + pointer


def bcd(rng, byte):
    digits = 2 if byte else 4
    return sum(rng.randrange(10) << 4 * i for i in range(digits))


def instruction(rng, label):
    """One random instruction's text, or several lines for a group (and whether it is a jump over
    the next instruction, to `label`)."""
    kind = rng.choices(["double", "single", "jump"], [70, 20, 10])[0]
    if kind == "jump":
        return "%s %s" % (rng.choice(JUMPS), label), True
    if kind == "single":
        op = rng.choice(SINGLE)
        byte = op in ("rrc", "rra") and rng.random() < 0.4
        operand = "r%d" % rng.randrange(7, 16) if rng.random() < 0.6 else mem_operand(rng, byte, False)
        text = "%s%s %s" % (op, ".b" if byte else "", operand)
        return text + {"sxt": "\nclrc", "rrc": "\nbic #384, r2"}.get(op, ""), False
    op = rng.choice(DOUBLE)
    if op == "dadd":                        # decimal operands only: dadd #bcd or rN, both loaded first
        byte = rng.random() < 0.4
        b = ".b" if byte else ""
        dst, src = rng.sample(range(7, 16), 2)
        lines = ["mov #%d, r%d" % (bcd(rng, byte), dst)]
        if rng.random() < 0.5:
            lines.append("dadd%s #%d, r%d" % (b, bcd(rng, byte), dst))
        else:
            lines += ["mov #%d, r%d" % (bcd(rng, byte), src), "dadd%s r%d, r%d" % (b, src, dst)]
        return "\n".join(lines + ["bic #384, r2"]), False
    byte = rng.random() < 0.4
    r = rng.random()
    if r < 0.35:
        src = "r%d" % rng.randrange(4, 16)
    elif r < 0.65:
        src = imm(rng, byte)
    else:
        src = mem_operand(rng, byte, True)
    dst = "r%d" % rng.randrange(7, 16) if rng.random() < 0.6 else mem_operand(rng, byte, False)
    if dst.startswith("@"):                 # no indirect destinations on the MSP430
        dst = "0" + dst[1:].rstrip("+").join("()") if not src.endswith("+") else \
            "%d(r4)" % rng.randrange(-16, 16, 1 if byte else 2)
    return "%s%s %s, %s" % (op, ".b" if byte else "", src, dst) + ("\nbic #384, r2" if op == "sub" else ""), False


def make_blocks(rng, n):
    """[(lines, table words)]: each block's instructions and its starting state (BUF as words,
    r4-r14, sr, r15; r4 and r5 as symbols)."""
    blocks = []
    for k in range(n):
        lines, pending = [], None
        for i in range(rng.randrange(1, 9)):
            text, jump = instruction(rng, ".Lj%d_%d" % (k, i))
            lines += text.split("\n")
            if pending:
                lines.append(pending + ":")
                pending = None
            if jump:
                pending = ".Lj%d_%d" % (k, i)
        if pending:
            lines.append("nop")
            lines.append(pending + ":")
        words = ["%d" % value(rng) for _ in range(BUF // 2)]
        regs = ["BUF+16", "BUF+8", "BUF+20"] + ["%d" % value(rng) for _ in range(8)]
        sr = sum(bit for bit in (1, 2, 4) if rng.random() < 0.5)      # no V: see the module's docstring
        blocks.append((lines, words + regs + ["%d" % sr, "%d" % value(rng)]))
    return blocks


def assembly(blocks):
    out = ["\t.text", "\t.global run_all", "run_all:"]
    for k, (lines, _) in enumerate(blocks):
        out += ["setup_%d:" % k, "\tmov #table_%d, &cur" % k, "\tcall #setup", "block_%d:" % k]
        out += ["\t" + l if not l.endswith(":") else l for l in lines]
        out += ["end_%d:" % k, "\tcall #save"]
    out += ["\tret", SETUP, "\t.section .rodata", "\t.balign 2"]
    for k, (_, table) in enumerate(blocks):
        out += ["table_%d:" % k, "\t.word " + ", ".join(table)]
    out += ["\t.section .bss", "\t.balign 2", "cur:\t.skip 2", ""]
    return "\n".join(out)


def build(blocks, folder, cc):
    with open(os.path.join(folder, "check.c"), "w") as f:
        f.write(C_SOURCE)
    with open(os.path.join(folder, "blocks.s"), "w") as f:
        f.write(assembly(blocks))
    elf = os.path.join(folder, "check.elf")
    r = subprocess.run([cc, "-mcpu=msp430", "-msim", "-O1", "-o", elf, os.path.join(folder, "check.c"),
                        os.path.join(folder, "blocks.s")], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("building the check program failed:\n" + r.stderr)
    return elf


def simulate(elf, run):
    r = subprocess.run([run, elf], capture_output=True, text=True, timeout=600)
    if r.returncode:
        raise RuntimeError("the simulator failed (%d): %s" % (r.returncode, r.stderr))
    return [l.split() for l in r.stdout.splitlines() if l.strip()]


def emulate(elf_path, n):
    """The engine's state at the end of each block: [(registers r4-r15, sr, BUF bytes)]."""
    from dread.elf import Elf
    from dread.engines.msp430 import Msp430Engine
    elf = Elf(elf_path)
    sym = {s.name: s.value for s in elf.symbols if s.name}
    engine = Msp430Engine()
    engine.mem_map(0, 0x10000)
    for lma, data, _ in elf.loaded():
        engine.mem_write(lma, data)
    out = []
    for k in range(n):
        engine.reg_write(1, sym.get("__stack", 0x8000) - 0x40)
        engine.emu_start(sym["setup_%d" % k], sym["end_%d" % k], count=10000)
        if engine.reg_read(0) != sym["end_%d" % k]:
            raise RuntimeError("block %d did not reach its end (pc 0x%x)" % (k, engine.reg_read(0)))
        regs = [engine.reg_read(r) for r in range(4, 16)]
        out.append((regs, engine.reg_read(2), bytes(engine.mem_read(sym["BUF"], BUF))))
    return out


def check(blocks, cc="msp430-elf-gcc", run="msp430-elf-run", keep=None):
    """[(block number, block lines, what differs)] for the blocks on which the engine and the
    simulator disagree."""
    folder = keep or tempfile.mkdtemp(prefix="msp430check-")
    os.makedirs(folder, exist_ok=True)
    try:
        elf = build(blocks, folder, cc)
        sim = simulate(elf, run)
        if len(sim) != len(blocks):
            raise RuntimeError("the simulator printed %d states for %d blocks" % (len(sim), len(blocks)))
        mine = emulate(elf, len(blocks))
    finally:
        if keep is None:
            shutil.rmtree(folder, ignore_errors=True)
    bad = []
    for k, ((lines, table), words, (regs, sr, buf)) in enumerate(zip(blocks, sim, mine)):
        theirs = [int(w, 16) for w in words[:13]]
        if theirs[12] & 0x80:               # the simulator's V (see the module's docstring)
            theirs[12] = (theirs[12] & ~0x80) | 0x100
        their_buf = bytes.fromhex(words[13])
        diffs = ["r%d 0x%04x vs 0x%04x" % (4 + i, a, b) for i, (a, b) in enumerate(zip(regs, theirs[:12])) if a != b]
        if sr != theirs[12]:
            diffs.append("sr 0x%04x vs 0x%04x" % (sr, theirs[12]))
        if buf != their_buf:
            diffs += ["BUF+%d 0x%02x vs 0x%02x" % (i, a, b) for i, (a, b) in enumerate(zip(buf, their_buf)) if a != b]
        if diffs:
            bad.append((k, lines, table, diffs))
    return bad


def run_check(n, seed, cc="msp430-elf-gcc", run="msp430-elf-run", keep=None):
    rng = random.Random(seed)
    bad, done = [], 0
    while done < n:
        blocks = make_blocks(rng, min(BATCH, n - done))
        for k, lines, table, diffs in check(blocks, cc, run, keep):
            bad.append((done + k, lines, table, diffs))
        done += len(blocks)
    return bad


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--blocks", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--keep", help="keep the programs in this folder")
    parser.add_argument("--cc", default="msp430-elf-gcc")
    parser.add_argument("--run", default="msp430-elf-run")
    args = parser.parse_args(argv)
    for tool in (args.cc, args.run):
        if not shutil.which(tool):
            print("msp430_check: %s not found (TI's MSP430 GCC has both)" % tool, file=sys.stderr)
            return 2
    bad = run_check(args.blocks, args.seed, args.cc, args.run, args.keep)
    for k, lines, table, diffs in bad[:20]:
        print("block %d: %s" % (k, "; ".join(diffs)))
        print("    " + "\n    ".join(lines))
        print("    start: BUF %s, r4-r14 %s, sr %s, r15 %s" % (" ".join(table[:BUF // 2]), " ".join(table[BUF // 2:-2]),
                                                             table[-2], table[-1]))
    print("%d blocks, %d disagree" % (args.blocks, len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
