"""dread discover: an image's function and code tables, found by following its code from its entry
points: for an image no ELF and no Ghidra gives tables for (a raw image of an architecture Ghidra
does not take: ARCompact).

Entry points: the image's entry, its interrupt vectors (the layer's vector_entries), the function
starts symbols.txt gives (`f` and `-` lines), and every call target found on the way. From each,
the code is followed by the layer's flow: the next instruction, the targets of jumps and branches,
a delay slot behind a branch (delay_slot), a zero-overhead loop's end; up to a return, an indirect
jump, an instruction that stops (brk) or that does not decode, or the image's end. A jump to
another entry is a tail call. An indirect jump's targets come from dread.switches, which runs the
dispatch in front of it, once a round's tables exist: the targets inside the function are followed
as its code, those outside become entries, and the next round follows them, until a round finds
nothing new (MAX_ROUNDS at most).

A function is its entry and the instructions reachable from it without passing through another
entry. The tables say so as DREAD reads them (docs/formats.md): the instruction runs (code_ranges),
and for each function the instruction bytes from its entry to the end of its last instruction,
which the program database walks from the entry up to the next entry. A function whose code lies
past another function's entry is cut there; dread discover says how many were.

Names: symbols.txt's `f` names, FUN_xxxxxxxx for the others (as dread import-elf --unnamed and
Ghidra write them).

    dread discover [--target DIR] [--image NAME] [--dry-run]

Writes config/functions.tsv and config/code_ranges.tsv of the image (symbols.txt is read, not
written). What it does not establish: code reached only through a pointer (a callback, a table of
handlers that no switch dispatch reads) is not found unless symbols.txt names it or a vector points
at it; code behind a call that never returns is followed as if the call returned, and data there is
taken for code until something does not decode.
"""
import argparse
import os
import sys

from dread import target as tgt

MAX_ROUNDS = 6              # rounds of switch resolution at most
UNNAMED = "FUN_%08x"


class Image:
    """The bytes an image's code runs from: the payload at its base, and the startup's code copies
    at their run addresses."""

    def __init__(self, image):
        self.base = image.base
        self.data = image.payload()
        self.copies = [c for c in image.startup() if c.kind == "code"]

    def spans(self):
        return [(self.base, self.base + len(self.data))] + [(c.run, c.run + c.size) for c in self.copies]

    def stored(self, address):
        for c in self.copies:
            if c.run <= address < c.run + c.size:
                return c.stored + (address - c.run)
        return address

    def holds(self, address):
        return any(lo <= address < hi for lo, hi in self.spans())

    def read(self, address, n):
        """Up to n bytes at run address `address`, fewer at the end of the image or a copy."""
        for lo, hi in self.spans():
            if lo <= address < hi:
                a = self.stored(address) - self.base
                return bytes(self.data[a:a + min(n, hi - address)])
        return b""


class Walk:
    """What the walk found: instructions (address -> decoded), entries (set), the edges inside
    functions (address -> successors) with the jumps among them (address -> target), indirect
    jumps, instructions that did not decode, and the entries' sources (address -> why)."""

    def __init__(self, arch, image):
        self.arch, self.image = arch, image
        self.insns = {}
        self.entries = set()
        self.why = {}
        self.succ = {}
        self.jumps = {}
        self.ijumps = set()
        self.bad = set()
        self.extra = {}                     # indirect jump -> its targets inside (dread.switches)
        self.dead_slots = set()             # delay slots behind a branch that does not fall through
        self.vectors = None                 # (start, end) of the vector table, when the layer reads one
        self.through = set()                # indirect jumps dread.switches found targets for
        self.covered = {}                   # byte inside an instruction (not its first) -> the instruction
        self.decoded = {}                   # address -> what decode() gave, found or not

    def reserved(self, address):
        """Whether an address lies where no function is looked for: the vector table (its slots are
        jumps to the handlers, which are entries) and the stored bytes of the startup's code copies
        (the code runs at the copy's address)."""
        if self.vectors is not None and self.vectors[0] <= address < self.vectors[1]:
            return True
        return any(c.stored <= address < c.stored + c.size for c in self.image.copies)

    def add_entry(self, address, why):
        if address not in self.entries:
            self.entries.add(address)
            self.why.setdefault(address, why)
            return True
        return False

    def decode(self, address):
        if address in self.insns:
            return self.insns[address]
        if address in self.decoded:
            return self.decoded[address]
        insn = None
        if self.image.holds(address):
            insn = self.arch.decode(self.image.read(address, getattr(self.arch, "max_insn", 4)), address)
            if insn is not None and getattr(getattr(insn, "fields", None), "op", "") == "unknown":
                insn = None
        self.decoded[address] = insn
        return insn

    def take(self, address, insn):
        """Record an instruction as found."""
        self.insns[address] = insn
        for b in range(address + 1, address + insn.size):
            self.covered[b] = address

    def step(self, a, insn):
        """(successors inside the function, jump target or None, call target or None, whether it
        stops: an instruction that ends code abruptly, brk or unknown)."""
        kind, target, falls = self.arch.flow(insn)
        nxt = a + insn.size
        out, jump, call = [], None, None
        slot = getattr(self.arch, "delay_slot", None)
        delayed = slot is not None and slot(insn)
        if a not in self.dead_slots and (falls or delayed):
            out.append(nxt)
        if kind == "jump" and target is not None and self.image.holds(target):
            out.append(target)
            jump = target
        if kind == "call" and target is not None and self.image.holds(target):
            call = target
        if kind == "ijump":
            out += [t for t in self.extra.get(a, ()) if self.image.holds(t)]
        if delayed and not falls:
            self.dead_slots.add(nxt)
        return [o for o in out if self.image.holds(o)], jump, call, kind == "stop", delayed and not falls

    def explore(self, seeds):
        """Follow the code from the seeds; call targets become entries and are followed too."""
        todo = list(seeds)
        while todo:
            a = todo.pop()
            if a in self.succ:
                continue
            insn = self.decode(a)
            if insn is None:
                self.bad.add(a)
                self.succ[a] = ()
                continue
            self.take(a, insn)
            out, jump, call, _, _ = self.step(a, insn)
            if jump is not None:
                self.jumps[a] = jump
            if call is not None and self.add_entry(call, "a call at 0x%x" % a):
                todo.append(call)
            if self.arch.flow(insn)[0] == "ijump":
                self.ijumps.add(a)
            self.succ[a] = tuple(out)
            todo += out

    def trial(self, start, lo=None, hi=None, limit=4096):
        """Whether code from `start` looks like a function: every path decodes and ends in a
        return, an indirect jump, or a jump or call to an entry, overlapping no instruction found
        before, and (with lo, hi) stays in [lo, hi) but for jumps to entries; some path returns or
        leaves by a jump to another entry (a tail call); no instruction is made of zero bytes (data
        that is zeros decodes on some cores as a branch to itself: ARCompact's b 0). Nothing is
        recorded."""
        covered = self.covered
        seen, todo, dead = set(), [start], set()
        leaves = False
        while todo:
            a = todo.pop()
            if a in seen:
                continue
            if a != start and a in self.entries:
                leaves = True
                continue
            seen.add(a)
            if len(seen) > limit or a in covered or a in self.insns and a != start and a not in self.entries:
                return False
            if lo is not None and not lo <= a < hi:
                return False
            insn = self.decode(a)
            if insn is None or not any(insn.bytes):
                return False
            kind, target, falls = self.arch.flow(insn)
            if kind == "stop":
                return False
            if kind in ("return", "ijump"):
                leaves = True
            slot = getattr(self.arch, "delay_slot", None)
            delayed = slot is not None and slot(insn)
            if delayed and not falls:
                dead.add(a + insn.size)
            if a not in dead and (falls or delayed):
                todo.append(a + insn.size)
            if kind == "jump" and target is not None:
                if not self.image.holds(target):
                    return False
                todo.append(target)
            if kind == "call" and target is not None and not self.image.holds(target):
                return False
        return leaves

    def bodies(self):
        """{entry: sorted instruction addresses} of each function: what its entry reaches without
        passing through another entry."""
        out = {}
        for e in sorted(self.entries):
            if e not in self.insns:
                continue
            seen, todo = set(), [e]
            while todo:
                a = todo.pop()
                if a in seen or a not in self.insns or (a != e and a in self.entries):
                    continue
                seen.add(a)
                todo += self.succ.get(a, ())
            out[e] = sorted(seen)
        return out

    def split(self):
        """The target of a jump that leaves its function's span (from its entry to the next entry;
        a jump whose source lies in the span), and then code that two functions reach, are
        functions of their own (a tail call's callee, shared code such as GCC's common epilogues):
        entries until nothing changes. Whether anything did."""
        changed = False
        while True:
            bodies = self.bodies()
            starts = sorted(bodies)
            leaving, shared, owner = set(), set(), {}
            for i, e in enumerate(starts):
                nxt = starts[i + 1] if i + 1 < len(starts) else None
                span = lambda x: e <= x and (nxt is None or x < nxt)
                for a in bodies[e]:
                    if a in owner and owner[a] != e:
                        shared.add(a)
                    owner.setdefault(a, e)
                    t = self.jumps.get(a)
                    if t is not None and t in self.insns and span(a) and not span(t):
                        leaving.add(t)
            new = {a for a in (leaving or shared) if a not in self.entries}
            if not new:
                return changed
            for a in sorted(new):
                self.add_entry(a, "jumped to from another function" if leaving else "reached by two functions")
            changed = True


def code_runs(insns, isa):
    """[(start, end, isa)] of the instruction runs: instructions that follow each other."""
    runs = []
    for a in sorted(insns):
        end = a + insns[a].size
        if runs and runs[-1][1] == a:
            runs[-1][1] = end
        elif runs and runs[-1][1] > a:          # (overlapping decodings: the later one is dropped)
            continue
        else:
            runs.append([a, end])
    return [(lo, hi, isa) for lo, hi in runs]


def tables(walk, names, isa):
    """(functions, code ranges, how many functions were cut at another entry) as the tables write
    them: [(entry, name, instruction bytes)], [(start, end, isa)]."""
    bodies = walk.bodies()
    code = {a: walk.insns[a] for body in bodies.values() for a in body}
    runs = code_runs(code, isa)
    starts = sorted(bodies)
    functions, cut = [], 0
    for i, e in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else None
        end = max(a + walk.insns[a].size for a in bodies[e])
        if nxt is not None and end > nxt:
            cut += 1
            end = nxt
        size = 0
        for lo, hi, _ in runs:
            lo, hi = max(lo, e), min(hi, end)
            if lo < hi:
                size += hi - lo
        functions.append((e, names.get(e) or UNNAMED % e, size))
    return functions, runs, cut


def seeds_of(arch, image, img, symbols):
    """The entry points the image gives: its entry, its vectors, symbols.txt's function starts."""
    out = set()
    try:
        out.add(image.entry)
    except tgt.TargetError:
        pass
    vectors = getattr(arch, "vector_entries", None)
    if vectors is not None and image.vectors is not None:
        for _, address, _ in vectors(img.read, image.vectors):
            if img.holds(address):
                out.add(address)
    for kind, address, _ in symbols:
        if kind in ("f", "-") and img.holds(address):
            out.add(address)
    return out


def pointer_candidates(walk):
    """Addresses the code takes as values (the layer's immediates: a long immediate a register is
    loaded with) and words in the image outside the instructions found, that point into the image
    at an instruction boundary not yet taken: candidates for functions reached through pointers."""
    out = set()
    for insn in walk.insns.values():
        for v in walk.arch.immediates(insn):
            out.add(v)
    covered = set(walk.covered) | set(walk.insns)
    img = walk.image
    for i in range(0, len(img.data) - 3, 4):
        a = img.base + i
        if a not in covered:
            out.add(int.from_bytes(img.data[i:i + 4], "little"))
    step = walk.arch.min_insn()
    return {v for v in out if img.holds(v) and v % step == 0 and v not in covered and v not in walk.entries
            and not walk.reserved(v)}


def gap_candidates(walk, bodies):
    """The first instruction behind each function's last one (padding nops skipped) that no
    function holds, with the gap it lies in (up to the next instruction found)."""
    starts = sorted(walk.insns)
    padding = {bytes(b) for b in getattr(walk.arch, "padding", {}).get(walk.arch.isa, ())}
    out = []
    for e, body in sorted(bodies.items()):
        a = max(x + walk.insns[x].size for x in body)
        while walk.image.read(a, 2) in padding or walk.image.read(a, 4) in padding:
            a += 2 if walk.image.read(a, 2) in padding else 4
        if a in walk.insns or not walk.image.holds(a) or walk.reserved(a):
            continue
        later = [s for s in starts if s > a]
        hi = later[0] if later else max(h for _, h in walk.image.spans() if _ <= a)
        out.append((a, hi))
    return out


def discover(target, image, write=True, rounds=MAX_ROUNDS, gaps=True, pointers=True, log=lambda text: None):
    """Walk the image's code (the module says how) and write its tables; returns (functions, code
    ranges, cut, the indirect jumps left without targets, {entry: why})."""
    from dread.program import read_symbols
    from dread.elfimport import write_tables
    arch = image.arch
    img = Image(image)
    symbols = read_symbols(image.table("symbols"))
    names = {address: name for kind, address, name in symbols if kind == "f" and name}
    walk = Walk(arch, img)
    if getattr(arch, "vector_entries", None) is not None and image.vectors is not None:
        table = arch.vector_entries(img.read, image.vectors)
        if table and getattr(arch, "VECTORS", None):         # (ARC: 8 bytes a slot, as many as it read)
            walk.vectors = (image.vectors, image.vectors + 8 * (max(i for i, _, _ in table) + 1))
    for a in seeds_of(arch, image, img, symbols):
        walk.add_entry(a, "the image's entry, a vector or symbols.txt")
    resolved = set()
    for n in range(rounds):
        while True:
            walk.explore(sorted(walk.entries) + [t for ts in walk.extra.values() for t in ts])
            if walk.split():
                continue
            found = False
            if pointers:
                for v in sorted(pointer_candidates(walk)):
                    if walk.trial(v):
                        walk.add_entry(v, "a pointer to it")
                        walk.explore([v])
                        found = True
            if gaps and not found:
                for a, hi in gap_candidates(walk, walk.bodies()):
                    if a not in walk.insns and walk.trial(a, a, hi):
                        walk.add_entry(a, "code in a gap behind a function")
                        walk.explore([a])
                        found = True
            if not found:
                break
        functions, runs, cut = tables(walk, names, arch.isa)
        log("round %d: %d functions, %d instruction runs, %d indirect jumps" % (
            n + 1, len(functions), len(runs), len(walk.ijumps)))
        open_jumps = sorted(walk.ijumps - resolved)
        if not open_jumps or not write:
            break
        _write(image, functions, runs, symbols, write_tables)
        if not _resolve(target, image, walk, open_jumps, resolved):
            break
        walk.succ = {}                      # (followed anew with the switches' edges)
    functions, runs, cut = tables(walk, names, arch.isa)
    if write:
        _write(image, functions, runs, symbols, write_tables)
    left = sorted(walk.ijumps - set(walk.extra) - walk.through)
    return functions, runs, cut, left, dict(walk.why)


def _write(image, functions, runs, symbols, write_tables):
    os.makedirs(image.config, exist_ok=True)
    write_tables(image, functions, runs, [s for s in symbols if s[2]], header="dread discover")


def _resolve(target, image, walk, jumps, resolved):
    """The targets of the indirect jumps by dread.switches, on this round's tables: those inside
    the function go into walk.extra, those outside become entries. Whether anything was new."""
    from dread import switches
    from dread.program import Program
    program = Program(target, image)
    new = False
    for a in jumps:
        resolved.add(a)
        fn = program.function_at(a)
        if fn is None:
            continue
        lifter, states = switches._plain_states(program, fn)
        insn = program.insn(a)
        if states and a in states and insn is not None and lifter.ret_jump(insn, states[a][1]):
            walk.through.add(a)             # a return through a copy of the return address (libgcc's
            continue                        # __umodsi3 on ARC: mov r7,blink ... j.d [r7])
        got = switches.resolve(program, fn, a)
        if got is None:
            continue
        inside, outside = got
        later = [e for e in walk.entries if e > fn.entry]
        end = min(later) if later else None         # (cases behind the table: the function's own)
        targets = set(inside) | {t for t in outside if fn.entry <= t and (end is None or t < end)}
        if targets and targets - set(walk.extra.get(a, ())):
            walk.extra[a] = tuple(sorted(targets))
            new = True
        for t in outside:
            if walk.image.holds(t) and t not in targets and walk.add_entry(t, "a tail call through a table"):
                new = True
        walk.through.add(a)
    return new


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread discover", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser)
    parser.add_argument("--dry-run", action="store_true", help="report what would be written, write nothing")
    parser.add_argument("--no-pointers", action="store_true", help="no functions from pointers (immediates, words)")
    parser.add_argument("--no-gaps", action="store_true", help="no functions from code in the gaps behind functions")
    args = parser.parse_args(argv)
    try:
        target, image = tgt.from_args(args)
        functions, runs, cut, left, why = discover(target, image, write=not args.dry_run, gaps=not args.no_gaps,
                                                  pointers=not args.no_pointers,
                                                  log=lambda text: print("dread discover: " + text))
    except tgt.TargetError as e:
        print("dread discover: %s" % e, file=sys.stderr)
        return 1
    print("dread discover: %d functions, %d instruction runs (%d instruction bytes)%s%s" % (
        len(functions), len(runs), sum(hi - lo for lo, hi, _ in runs),
        "; %d cut at another function's entry" % cut if cut else "",
        "; %d indirect jumps without targets" % len(left) if left else ""))
    if not args.dry_run:
        print("dread discover: written to %s" % image.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
