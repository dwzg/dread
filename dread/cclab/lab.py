"""dread cclab's core: one build of the probes (compiled, linked, made a DREAD target) and what is
measured on it.

A build links the probes into fixed memory (flash at FLASH, RAM at RAM; GNU ld script or ILINK
configuration written here), so that the emulator maps exactly that; DREAD's ELF import makes the
linked image a target of its own (dread.toml, tables, payload) in the build's directory. Then each
probe function is measured by what its spec says (the probes' TOML files):

  arg     where the argument it returns arrives: it is run with a marker in every argument register
          and in 16 stack words, and the marker that comes back names the place (r2, stack+4; two
          words for a 64-bit value: r2:r3)
  result  where a result comes back: in registers (r0, r0:r1) or in memory through a pointer the
          caller passes in r0 (AAPCS's way for a struct of more than a word)
  value   what it returns (a size, an alignment, a sign extension), read as the spec's note says
  op      what it calls and the shape of its code; one run on the spec's inputs records, for every
          function it calls, the registers at the call and at the return and the ones changed

Every probe function also gets its shape: size, instructions, calls, the first and last few
instructions. What this does not establish: that a probe's C means the same to every compiler
(they are written in plain C99), and anything about code the probes do not contain.
"""
import glob
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = os.path.join(HERE, "probes")

FLASH, FLASH_SIZE = 0x08000000, 0x100000
RAM, RAM_SIZE = 0x20000000, 0x40000
SCRATCH, SCRATCH_SIZE = 0x20100000, 0x20000
STACK_SIZE = 0x2000
SENTINEL = 0x1FFF0000
LIMIT = 200_000                     # instructions a probe's run may take
STACK_WORDS = 16                    # stack words that get a marker (arg)

GNU_SCRIPT = """/* dread cclab: the probes' memory; the emulator maps the same */
ENTRY(cclab_entry)
MEMORY {{
    FLASH (rx) : ORIGIN = 0x{flash:08x}, LENGTH = 0x{flash_size:x}
    RAM (rwx) : ORIGIN = 0x{ram:08x}, LENGTH = 0x{ram_size:x}
}}
SECTIONS {{
    .text : {{ KEEP(*(.text*)) *(.rodata*) *(.glue_7*) *(.ARM.extab* .gnu.linkonce.armextab.*) }} > FLASH
    .ARM.exidx : {{ *(.ARM.exidx* .gnu.linkonce.armexidx.*) }} > FLASH
    .data : {{ *(.data*) }} > RAM AT > FLASH
    .bss : {{ *(.bss*) *(COMMON) }} > RAM
}}
"""

ICF = """/* dread cclab: the probes' memory; the emulator maps the same */
define memory mem with size = 4G;
define region ROM_region = mem:[from 0x{flash:08x} size 0x{flash_size:x}];
define region RAM_region = mem:[from 0x{ram:08x} size 0x{ram_size:x}];
define block CSTACK with alignment = 8, size = 0x{stack:x} {{ }};
define block HEAP with alignment = 8, size = 0 {{ }};
initialize by copy {{ readwrite }};
do not initialize {{ section .noinit }};
place in ROM_region {{ readonly }};
place in RAM_region {{ readwrite, block CSTACK, block HEAP }};
"""

TI_CMD = """/* dread cclab: the probes' memory; the emulator maps the same */
--stack_size=0x{stack:x}
--heap_size=0
MEMORY
{{
    FLASH (RX) : origin = 0x{flash:08x}, length = 0x{flash_size:08x}
    RAM (RWX) : origin = 0x{ram:08x}, length = 0x{ram_size:08x}
}}
SECTIONS
{{
    .text : > FLASH
    .const : > FLASH
    .cinit : > FLASH
    .pinit : > FLASH
    .init_array : > FLASH
    .ARM.exidx : > FLASH
    .ARM.extab : > FLASH
    .data : > RAM
    .bss : > RAM
    .sysmem : > RAM
    .stack : > RAM
}}
"""

SCRIPTS = {"gnu": (GNU_SCRIPT, "link.ld"), "iar": (ICF, "link.icf"), "ti": (TI_CMD, "link.cmd")}

TARGET = """# dread cclab: one build of the probes ({adapter}, {build})
[target]
name = "cclab-{adapter}-{build}"
title = "dread cclab: {adapter} {build}"

[arch]
name = "{arch}"
isa = "{isa}"
profile = "{profile}"
{cpu}
[image.main]
original = "original/image.bin"
base = 0x{flash:08x}
entry = "cclab_entry"

[memory]
regions = [
    {{name = "flash", base = 0x{flash:08x}, size = 0x{flash_size:x}, kind = "rom"}},
    {{name = "ram", base = 0x{ram:08x}, size = 0x{ram_size:x}}},
    {{name = "scratch", base = 0x{scratch:08x}, size = 0x{scratch_size:x}}},
]
stack = {{top = 0x{stack_top:08x}, size = 0x{stack:x}}}
sentinel = 0x{sentinel:08x}
scratch = {{base = 0x{scratch:08x}, size = 0x{scratch_size:x}}}
"""


class BuildError(Exception):
    def __init__(self, message, log):
        super().__init__(message)
        self.log = log


class Probe:
    """A probe file: its C source and its specs ([{function, measure, ...}])."""

    def __init__(self, source):
        import tomllib
        self.source = source
        self.name = os.path.splitext(os.path.basename(source))[0]
        spec = os.path.splitext(source)[0] + ".toml"
        with open(spec, "rb") as f:
            self.specs = tomllib.load(f).get("probes", [])
        self.functions = [s["function"] for s in self.specs]


def probes(names=None, directory=PROBES):
    """The probe files (every C file with a TOML of specs), or those named."""
    out = []
    for source in sorted(glob.glob(os.path.join(directory, "*.c"))):
        if os.path.exists(os.path.splitext(source)[0] + ".toml"):
            p = Probe(source)
            if not names or p.name in names:
                out.append(p)
    return out


def memory():
    return {"flash": FLASH, "flash_size": FLASH_SIZE, "ram": RAM, "ram_size": RAM_SIZE, "scratch": SCRATCH,
            "scratch_size": SCRATCH_SIZE, "stack": STACK_SIZE, "stack_top": RAM + RAM_SIZE,
            "sentinel": SENTINEL}


# ---- building ----

def build(adapter, key, picked, flags, probe_files, work):
    """Compile, link and import one build; returns its directory. Raises BuildError with the log
    ([{step, file, argv, code, output}]) when a step fails."""
    from dread import elfimport
    d = os.path.join(work, adapter.name, key)
    shutil.rmtree(d, ignore_errors=True)
    for sub in ("src", "obj", "original"):
        os.makedirs(os.path.join(d, sub))
    sources = [os.path.join(PROBES, "entry.c")]
    if adapter.support:
        sources.append(os.path.join(PROBES, "support.c"))
    sources += [p.source for p in probe_files]
    log, objs = [], []
    for source in sources:
        name = os.path.splitext(os.path.basename(source))[0]
        local = os.path.join(d, "src", os.path.basename(source))      # where a Windows tool sees it
        shutil.copyfile(source, local)
        obj = os.path.join(d, "obj", name + ".o")
        extra = adapter.support_flags if name == "support" else []
        argv = adapter.command("compile", src=local, obj=obj, flags=list(flags) + adapter.cflags + extra)
        code, text = adapter.run(argv, d)
        log.append({"step": "compile", "file": name, "argv": argv, "code": code, "output": text})
        if code or not os.path.exists(obj):
            raise BuildError("compiling %s failed (%d)" % (name, code), log)
        objs.append(obj)
    template, script_name = SCRIPTS[adapter.family]
    script = os.path.join(d, script_name)
    with open(script, "w") as f:
        f.write(template.format(**memory()))
    elf = os.path.join(d, "image.elf")
    keep = ["cclab_entry"] + [fn for p in probe_files for fn in p.functions]
    argv = adapter.command("link", objs=objs, out=elf, script=script, flags=list(flags), ldflags=adapter.ldflags,
                           keep=keep)
    code, text = adapter.run(argv, d)
    log.append({"step": "link", "file": "image.elf", "argv": argv, "code": code, "output": text})
    if code or not os.path.exists(elf):
        raise BuildError("linking failed (%d)" % code, log)
    cpu = adapter.cpu_model(picked)
    with open(os.path.join(d, "dread.toml"), "w") as f:
        f.write(TARGET.format(adapter=adapter.name, build=key, arch=adapter.arch, isa=adapter.isa,
                              profile=adapter.profile, cpu='cpu = "%s"\n' % cpu if cpu else "", **memory()))
    import contextlib
    import io
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        code = elfimport.main(["--target", d, "--payload", os.path.join(d, "original", "image.bin"),
                               "--write-startup", elf])
    log.append({"step": "import", "file": "image.elf", "argv": ["dread import-elf"], "code": code,
                "output": quiet.getvalue()})
    if code:
        raise BuildError("importing the image failed (%d)" % code, log)
    with open(os.path.join(d, "build.json"), "w") as f:
        json.dump({"adapter": adapter.name, "build": key, "picked": picked, "flags": list(flags), "log": log}, f,
                  indent=1)
    return d


# ---- measuring ----

def reg_mark(i):
    """The marker of argument register i (its low byte names it, so does its low halfword)."""
    return 0xC0DE0000 | (0x10 + i) << 8 | (0x10 + i)


def stack_mark(k):
    """The marker of stack word k above the stack pointer at the call."""
    return 0x5EA00000 | (0x40 + k) << 8 | (0x40 + k)


def place(word, nbytes=4):
    """Where a marker came from ("r2", "stack+8"), from its low nbytes bytes; None if no marker."""
    low = word & 0xFF
    mask = (1 << 8 * nbytes) - 1
    if 0x10 <= low < 0x20 and (word & mask) == (reg_mark(low - 0x10) & mask):
        return "r%d" % (low - 0x10)
    if 0x40 <= low < 0x40 + STACK_WORDS and (word & mask) == (stack_mark(low - 0x40) & mask):
        return "stack+%d" % (4 * (low - 0x40))
    return None


class Bench:
    """A built probe image in the emulator: its program and a fresh machine per run."""

    def __init__(self, directory):
        from dread.emu import Layout
        from dread.program import Program
        from dread.target import Target
        self.directory = directory
        self.target = Target.load(directory)
        self.image = self.target.image()
        self.program = Program(self.target, self.image)
        self.layout = Layout(self.target, self.image)
        self.arch = self.image.arch
        self.aliases = {}                   # address -> every function name there (libgcc's __aeabi_uidiv
        from dread.elf import Elf           # is __udivsi3), the import's own name first
        for fn in self.program.functions:
            self.aliases.setdefault(fn.entry, []).append(fn.name)
        for sym in Elf(os.path.join(directory, "image.elf")).symbols:
            if sym.kind == 2 and sym.name and (sym.value & ~1) in self.aliases \
                    and sym.name not in self.aliases[sym.value & ~1]:
                self.aliases[sym.value & ~1].append(sym.name)

    def names(self, address):
        """Every function name at an address, joined with " = "."""
        return " = ".join(self.aliases.get(address & ~1, ["0x%x" % address]))

    def machine(self):
        from dread.emu import Machine
        m = Machine(self.layout, self.image.payload(), isa_of=self.program.isa_at)
        m.plan = []
        return m

    def reg(self, m, name):
        return m.uc.reg_read(self.arch.reg_id(name))

    def value(self, word):
        """A spec's argument: a number (negative ones as 32-bit), or "scratch[+N]"."""
        if isinstance(word, str):
            base, _, offset = word.partition("+")
            if base != "scratch":
                raise ValueError("unknown argument %r" % word)
            return SCRATCH + (int(offset, 0) if offset else 0)
        return word & 0xFFFFFFFF


def shape(bench, name):
    """{size, instructions, calls, tails, head, tail}: a probe function's code as the build made it."""
    program, arch = bench.program, bench.arch
    fn = program.by_name.get(name)
    if fn is None:
        return {"missing": True}
    insns = [i for i in program.instructions(fn) if i is not None]
    calls, tails = [], []
    for insn in insns:
        kind, target, _ = arch.flow(insn)
        callee = program.by_entry.get(target & ~1) if isinstance(target, int) else None
        if kind == "call":
            calls.append(bench.names(callee.entry) if callee is not None else "0x%x" % target)
        elif kind == "icall":
            calls.append("(through a register)")
        elif kind == "jump" and callee is not None and callee is not fn:
            tails.append(bench.names(callee.entry))
    text = ["%s %s" % (i.mnemonic, i.op_str) for i in insns]
    return {"size": program.end(fn) - fn.entry, "instructions": len(insns), "calls": calls, "tails": tails,
            "head": text[:6], "tail": text[-4:], "mnemonics": sorted({i.mnemonic for i in insns})}


def measure_arg(bench, spec):
    from dread.emu import EmuError
    fn = bench.program.by_name[spec["function"]]
    m = bench.machine()
    args = [reg_mark(i) for i in range(4)] + [stack_mark(k) for k in range(STACK_WORDS)]
    try:
        m.call(fn.entry, *args, max_instructions=LIMIT)
    except EmuError as e:
        return {"error": str(e)}
    words = [bench.reg(m, "r%d" % i) for i in range(spec.get("words", 1))]
    places = [place(w, spec.get("bytes", 4)) for w in words]
    return {"where": ":".join(p or "?" for p in places), "words": ["0x%08x" % w for w in words]}


def measure_result(bench, spec):
    from dread.emu import EmuError
    fn = bench.program.by_name[spec["function"]]
    value = [v & 0xFFFFFFFF for v in spec["value"]]
    nbytes = spec.get("bytes", 4)
    mask = (1 << 8 * nbytes) - 1
    m = bench.machine()
    m.write(SCRATCH, bytes(64))
    try:
        m.call(fn.entry, SCRATCH, reg_mark(1), reg_mark(2), reg_mark(3), max_instructions=LIMIT)
    except EmuError as e:
        return {"error": str(e)}
    regs = [bench.reg(m, "r%d" % i) for i in range(4)]
    memory = m.read(SCRATCH, 4 * len(value))
    words = [int.from_bytes(memory[4 * i:4 * i + 4], "little") for i in range(len(value))]
    if all((w & mask) == v for w, v in zip(words, value)):
        # first: a value stored through the pointer (registers may hold it by the way: strd r2, r3)
        returned = " (r0 returns the address)" if regs[0] == SCRATCH else ""
        return {"where": "memory at the address in r0" + returned}
    if len(value) <= 4 and all((regs[i] & mask) == v for i, v in enumerate(value)):
        return {"where": ":".join("r%d" % i for i in range(len(value)))}
    return {"where": "?", "regs": ["0x%08x" % r for r in regs]}


def measure_value(bench, spec):
    from dread.emu import EmuError
    fn = bench.program.by_name[spec["function"]]
    m = bench.machine()
    try:
        m.call(fn.entry, max_instructions=LIMIT)
    except EmuError as e:
        return {"error": str(e)}
    r0 = bench.reg(m, "r0")
    return {"value": r0, "hex": "0x%08x" % r0}


def measure_op(bench, spec):
    """The op's one run: every function it calls (a call instruction, or the probe's own tail call),
    with the registers at the entry and at the return and the ones changed, by a hook on every
    instruction of the image (helpers are short). An entry reached by a branch is no call (a runtime
    library's routines branch into one another); a return may close several frames at once; a tail
    call returns where the probe would, so its frame closes when the run ends."""
    from unicorn import UC_HOOK_CODE
    from dread.emu import EmuError
    program, arch = bench.program, bench.arch
    fn = program.by_name[spec["function"]]
    fn_end = program.end(fn)
    names = ["r%d" % i for i in range(13)] + ["sp", "lr"]
    ids = [arch.reg_id(n) for n in names]
    m = bench.machine()
    if any(isinstance(a, str) for a in spec.get("args", [])):
        m.write(SCRATCH, bytes(range(256)) * 2)
    frames, seen = [], []
    state = {"prev": None}              # (address, flow kind) of the instruction before

    def close(frame, after):
        frame["changed"] = [n for n, a, b in zip(names, frame["regs"], after) if a != b and n != "lr"]
        frame["results"] = ["0x%08x" % v for v in after[:2]]
        seen.append(frame)

    def on_code(uc, address, size, user):
        for i in range(len(frames) - 1, -1, -1):
            if address == frames[i]["ret"]:
                after = [uc.reg_read(r) for r in ids]
                for frame in frames[i:]:
                    close(frame, after)
                del frames[i:]
                break
        prev = state["prev"]
        callee = program.by_entry.get(address)
        if callee is not None and callee is not fn and prev is not None:
            regs = [uc.reg_read(r) for r in ids]
            if prev[1] in ("call", "icall"):
                frames.append({"function": bench.names(address), "depth": len(frames) + 1, "regs": regs,
                               "ret": regs[names.index("lr")] & ~1, "args": ["0x%08x" % v for v in regs[:4]]})
            elif prev[1] == "jump" and fn.entry <= prev[0] < fn_end and not frames:
                frames.append({"function": bench.names(address), "depth": 1, "regs": regs, "ret": SENTINEL,
                               "tail": True, "args": ["0x%08x" % v for v in regs[:4]]})
        insn = program.insn(address)
        state["prev"] = (address, arch.flow(insn)[0] if insn is not None else "next")
    m.hook_add(UC_HOOK_CODE, on_code, begin=FLASH, end=FLASH + FLASH_SIZE - 1)
    args = [bench.value(a) for a in spec.get("args", [])]
    try:
        m.call(fn.entry, *args, max_instructions=LIMIT)
    except EmuError as e:
        return {"error": str(e)}
    after = [m.uc.reg_read(r) for r in ids]
    for frame in frames:                # tail calls: they return where the probe returns
        close(frame, after)
    out = {"result": ["0x%08x" % after[0], "0x%08x" % after[1]], "helpers": []}
    for frame in seen:
        out["helpers"].append({k: frame[k] for k in ("function", "depth", "args", "results", "changed")
                               if k in frame} | ({"tail": True} if frame.get("tail") else {}))
    return out


def measure_keeps(bench, spec):
    """Run with a marker in every register a call must keep (the ABI's callee-saved registers):
    which came back changed, the stack pointer's alignment at every call the function itself makes
    (seen at the callee's first instruction; AAPCS asks for 8 bytes at a public interface), and the
    saving instructions at its entry."""
    from unicorn import UC_HOOK_CODE
    from dread.emu import EmuError
    program, arch = bench.program, bench.arch
    fn = program.by_name[spec["function"]]
    fn_end = program.end(fn)
    m = bench.machine()
    marks = {}
    for i, reg in enumerate(bench.layout.saved_regs):
        marks[reg] = 0x5AFE0000 | (0x20 + i) << 8 | (0x20 + i)
        m.uc.reg_write(reg, marks[reg])
    sp_id, state, calls = arch.reg_id("sp"), {"prev": None}, []

    def on_code(uc, address, size, user):
        prev = state["prev"]
        if prev is not None and prev[1] in ("call", "icall") and fn.entry <= prev[0] < fn_end \
                and program.by_entry.get(address) is not None:
            calls.append(uc.reg_read(sp_id) % 8)
        insn = program.insn(address)
        state["prev"] = (address, arch.flow(insn)[0] if insn is not None else "next")
    m.hook_add(UC_HOOK_CODE, on_code, begin=FLASH, end=FLASH + FLASH_SIZE - 1)
    args = [bench.value(a) for a in spec.get("args", [])]
    try:
        m.call(fn.entry, *args, max_instructions=LIMIT)
    except EmuError as e:
        return {"error": str(e)}
    ids = {arch.reg_id(n): n for n in ["r%d" % i for i in range(13)]}
    broken = [ids.get(reg, str(reg)) for reg, v in marks.items() if m.uc.reg_read(reg) != v]
    head = ["%s %s" % (i.mnemonic, i.op_str) for i in [i for i in program.instructions(fn) if i is not None][:8]]
    return {"kept": "all" if not broken else "not " + " ".join(broken), "sp_mod8_at_calls": sorted(set(calls)),
            "saves": [t for t in head if t.startswith(("push", "stmdb", "sub sp", "vpush", "str"))][:3]}


MEASURES = {"arg": measure_arg, "result": measure_result, "value": measure_value, "op": measure_op,
            "keeps": measure_keeps}


def measure(directory, probe_files):
    """{function: {measure, note, shape, and what the measure found}} of one build."""
    bench = Bench(directory)
    out = {}
    for p in probe_files:
        for spec in p.specs:
            name = spec["function"]
            entry = {"probe": p.name, "measure": spec["measure"], "note": spec.get("note", ""),
                     "shape": shape(bench, name)}
            if name in bench.program.by_name:
                try:
                    entry.update(MEASURES[spec["measure"]](bench, spec))
                except Exception as e:          # a measurement that breaks is a result, not a crash
                    entry["error"] = "%s: %s" % (type(e).__name__, e)
            else:
                entry["error"] = "not in the linked image"
            out[name] = entry
    return out
