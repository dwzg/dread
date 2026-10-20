"""dread cclab's core: one build of the probes (compiled, linked, made a DREAD target) and what is
measured on it, for any architecture DREAD's emulator runs.

A build links the probes into fixed memory (MAPS: per architecture, or the adapter's [memory]),
written for the toolchain's linker (a GNU ld script, an ILINK configuration or a TI linker command
file), so that the emulator maps exactly that; DREAD's ELF import makes the linked image a target of
its own in the build's directory. The registers come from DREAD's architecture layer: the argument
registers in order (they also carry results), the callee-saved ones, the register width. Each probe
function is measured by what its spec says (the probes' TOML files):

  arg     where the argument it returns arrives: it is run with a marker in every argument register
          and in 16 stack words (a marker repeats one byte across the register, so a char or a short
          of it still names its place), and the marker that comes back names the place (r2,
          stack+4: bytes into the stack arguments; a value wider than a register: r2:r3)
  result  where a result comes back: in the first argument registers, or in memory through a
          pointer the caller passes in the first one (AAPCS's way for a struct of more than a word)
  value   what it returns, read as the spec's note says (a size, an alignment, a sign extension)
  op      what it calls and the shape of its code; one run on the spec's inputs records, for every
          function it calls, the registers at the call and at the return and the ones changed
  keeps   run with a marker in every callee-saved register: which came back changed, which of
          them (and the return address) it stored in its own frame, its frame at the calls it makes

A spec's values are C-typed: a plain number is an int; "u64:0x...", "i64:..", "float:BITS",
"double:BITS", "ptr:scratch+N" (an address in the lab's scratch memory) name their type, and the lab
splits a value into register-wide words, lowest first (an int is two bytes on the MSP430).

Every probe function also gets its shape: size, instructions, calls, the first and last few
instructions. And each build its layout (layout()): where the probes' functions start, what lies
between each and the next function (a literal pool it loads from, padding), their first and last
instructions: what tells a function's start in a stripped image of the same compiler. What this does not establish: that a probe's C means the same to every compiler
(they are plain C99), anything about code the probes do not contain, big-endian targets.
"""
import glob
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = os.path.join(HERE, "probes")

LIMIT = 200_000                     # instructions a probe's run may take
STACK_WORDS = 16                    # stack words that get a marker (arg)

# Where the probes are linked and the emulator maps memory, per architecture (an adapter's [memory]
# table, with the same keys, overrides): the stack at the top of RAM, the sentinel (the return
# address of a run) in unmapped memory.
MAPS = {
    "arm": {"flash": 0x08000000, "flash_size": 0x100000, "ram": 0x20000000, "ram_size": 0x40000,
            "scratch": 0x20100000, "scratch_size": 0x20000, "stack": 0x2000, "sentinel": 0x1FFF0000, "io": []},
    "riscv": {"flash": 0x20000000, "flash_size": 0x100000, "ram": 0x20400000, "ram_size": 0x40000,
              "scratch": 0x20500000, "scratch_size": 0x20000, "stack": 0x2000, "sentinel": 0x1FFF0000, "io": []},
    "msp430": {"flash": 0x4000, "flash_size": 0xBF00, "ram": 0x0200, "ram_size": 0x1E00,
               "scratch": 0x2200, "scratch_size": 0x1600, "stack": 0x0400, "sentinel": 0x3FF0,
               "io": [[0x0000, 0x0200]]},
}

GNU_SCRIPT = """/* dread cclab: the probes' memory; the emulator maps the same */
ENTRY(cclab_entry)
MEMORY {{
    FLASH (rx) : ORIGIN = 0x{flash:x}, LENGTH = 0x{flash_size:x}
    RAM (rwx) : ORIGIN = 0x{ram:x}, LENGTH = 0x{ram_size:x}
}}
SECTIONS {{
    .text : {{ KEEP(*(.text*)) *(.rodata*) *(.srodata*) *(.glue_7*) *(.ARM.extab* .gnu.linkonce.armextab.*) }} > FLASH
    .ARM.exidx : {{ *(.ARM.exidx* .gnu.linkonce.armexidx.*) }} > FLASH
    .data : {{ *(.data*) *(.sdata*) }} > RAM AT > FLASH
    .bss : {{ *(.bss*) *(.sbss*) *(COMMON) }} > RAM
}}
"""

ICF = """/* dread cclab: the probes' memory; the emulator maps the same */
define memory mem with size = 4G;
define region ROM_region = mem:[from 0x{flash:08x} size 0x{flash_size:x}];
define region RAM_region = mem:[from 0x{ram:08x} size 0x{ram_size:x}];
define block CSTACK with alignment = 8, size = 0x{stack:x} {{ }};
define block HEAP with alignment = 8, size = 0 {{ }};
do not initialize {{ readwrite }};
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
{profile}{cpu}
[image.main]
original = "original/image.bin"
base = 0x{flash:x}
limit = 0x{flash_end:x}
entry = "cclab_entry"

[memory]
regions = [
    {{name = "flash", base = 0x{flash:x}, size = 0x{flash_size:x}, kind = "rom"}},
    {{name = "ram", base = 0x{ram:x}, size = 0x{ram_size:x}}},
    {{name = "scratch", base = 0x{scratch:x}, size = 0x{scratch_size:x}}},
]
io = {io}
stack = {{top = 0x{stack_top:x}, size = 0x{stack:x}}}
sentinel = 0x{sentinel:x}
scratch = {{base = 0x{scratch:x}, size = 0x{scratch_size:x}}}
{llm}"""

LLM = """
# dread cclab: an LLM may be shown these functions only, nothing of the toolchain's library
[llm]
only = {names}
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


RESERVED = ("entry", "support", "cxx_support")     # the lab's own files in a build's probes/


def probes(names=None, directories=()):
    """The probe files (every C file with a TOML of specs) of DREAD's corpus and of the directories
    given (probes of one's own, kept wherever one likes), or those named. Two files of one name, or
    a probe file named like the lab's own (entry, support), are an error (ValueError): a name says
    which probe file a result came from, and every build copies the sources side by side."""
    out, seen = [], {}
    for directory in [PROBES] + list(directories):
        for source in sorted(glob.glob(os.path.join(directory, "*.c")) + glob.glob(os.path.join(directory, "*.cpp"))):
            if not os.path.exists(os.path.splitext(source)[0] + ".toml"):
                continue
            p = Probe(source)
            if p.name in seen or p.name in RESERVED:
                raise ValueError("a probe file named %s: %s%s" % (
                    p.name, source, " and " + seen[p.name] if p.name in seen else " (a name the lab keeps for itself)"))
            seen[p.name] = source
            if not names or p.name in names:
                out.append(p)
    missing = sorted(set(names or ()) - set(seen))
    if missing:
        raise ValueError("no probe file named %s (there are: %s)" % (", ".join(missing), ", ".join(sorted(seen))))
    return out


def memory(adapter):
    """The adapter's memory map: its architecture's (MAPS), with the adapter's [memory] over it."""
    base = MAPS.get(adapter.arch)
    if base is None and "memory" not in adapter.data:
        raise ValueError("no memory map for architecture %s: give the adapter a [memory] table" % adapter.arch)
    out = dict(base or {}, **adapter.data.get("memory", {}))
    out["stack_top"] = out["ram"] + out["ram_size"]
    out["flash_end"] = out["flash"] + out["flash_size"]
    from dread.emu import ARGS_ROOM
    room = (out["stack_top"], out["stack_top"] + ARGS_ROOM)     # the stack arguments of a run lie there
    if out["scratch"] < room[1] and room[0] < out["scratch"] + out["scratch_size"]:
        raise ValueError("the scratch memory 0x%x-0x%x overlaps the stack arguments above the stack top "
                         "(0x%x-0x%x)" % (out["scratch"], out["scratch"] + out["scratch_size"], *room))
    return out


# ---- building ----

def build(adapter, key, picked, flags, probe_files, work, guard=None):
    """Compile, link and import one build; returns its directory. Raises BuildError with the log
    ([{step, file, argv, code, output}]) when a step fails. The probes' sources are copied to
    probes/ (src/ is where DREAD's tools look for rewritten C of the target). Guarded (default: as
    the adapter says), the build's dread.toml names the functions an LLM may be shown: the probes
    and the lab's entry, nothing of the toolchain's library ([llm] only; dread pilot keeps to it)."""
    guard = adapter.guard if guard is None else guard
    from dread import elfimport
    d = os.path.join(work, adapter.name, key)
    shutil.rmtree(d, ignore_errors=True)
    for sub in ("probes", "obj", "original"):
        os.makedirs(os.path.join(d, sub))
    mem = memory(adapter)
    cxx = "compile_cxx" in adapter.commands
    sources = [os.path.join(PROBES, "entry.c")]
    if adapter.support:
        sources.append(os.path.join(PROBES, "support.c"))
    if cxx and adapter.cxx_support and any(p.source.endswith(".cpp") for p in probe_files):
        sources.append(os.path.join(PROBES, "cxx_support.c"))
    sources += [p.source for p in probe_files]
    log, objs, skipped = [], [], []
    for source in sources:
        name = os.path.splitext(os.path.basename(source))[0]
        if source.endswith(".cpp") and not cxx:          # C++ probes need the adapter's compile_cxx
            log.append({"step": "compile", "file": name, "argv": [], "code": 0,
                        "output": "skipped: the adapter has no compile_cxx command"})
            skipped.append(name)
            continue
        local = os.path.join(d, "probes", os.path.basename(source))   # where a Windows tool sees it
        shutil.copyfile(source, local)
        obj = os.path.join(d, "obj", name + ".o")
        extra = adapter.support_flags if name in ("support", "cxx_support") else []
        argv = adapter.command("compile_cxx" if source.endswith(".cpp") else "compile", src=local, obj=obj,
                               flags=list(flags) + adapter.cflags + extra)
        code, text = adapter.run(argv, d)
        log.append({"step": "compile", "file": name, "argv": argv, "code": code, "output": text})
        if code or not os.path.exists(obj):
            raise BuildError("compiling %s failed (%d)" % (name, code), log)
        objs.append(obj)
    template, script_name = SCRIPTS[adapter.family]
    script = os.path.join(d, script_name)
    try:
        text = (adapter.script or template).format(**mem)
    except (KeyError, IndexError, ValueError) as e:
        raise BuildError("the adapter's script template: %s (placeholders: %s; a brace of the script "
                         "itself is doubled)" % (e, ", ".join(sorted(mem))), [])
    with open(script, "w") as f:
        f.write(text)
    elf = os.path.join(d, "image.elf")
    keep = ["cclab_entry"] + [fn for p in probe_files if p.name not in skipped for fn in p.functions]
    argv = adapter.command("link", objs=objs, out=elf, script=script, flags=list(flags), ldflags=adapter.ldflags,
                           keep=keep)
    code, text = adapter.run(argv, d)
    log.append({"step": "link", "file": "image.elf", "argv": argv, "code": code, "output": text})
    if code or not os.path.exists(elf):
        raise BuildError("linking failed (%d)" % code, log)
    cpu = adapter.cpu_model(picked)
    with open(os.path.join(d, "dread.toml"), "w") as f:
        f.write(TARGET.format(adapter=adapter.name, build=key, arch=adapter.arch, isa=adapter.isa,
                              profile='profile = "%s"\n' % adapter.profile if adapter.profile else "",
                              cpu='cpu = "%s"\n' % cpu if cpu else "",
                              llm=LLM.format(names=json.dumps(keep)) if guard else "",
                              **dict(mem, io=json.dumps(mem.get("io", [])).replace('"', ""))))
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
        json.dump({"adapter": adapter.name, "build": key, "picked": picked, "flags": list(flags), "log": log,
                   "skipped": skipped}, f, indent=1)
    return d


# ---- values and markers ----

def reg_mark(i, nbytes):
    """The marker of argument register i: the byte 0x10 + i across the register."""
    return int.from_bytes(bytes([0x10 + i]) * nbytes, "little")


def fp_mark(i):
    """The marker of floating-point argument register i (32 bits): the byte 0x60 + i four times."""
    return int.from_bytes(bytes([0x60 + i]) * 4, "little")


def stack_mark(k, nbytes):
    """The marker of the k-th word of stack arguments: the byte 0x40 + k across the word."""
    return int.from_bytes(bytes([0x40 + k]) * nbytes, "little")


class Bench:
    """A built probe image in the emulator: its program, its registers, a fresh machine per run."""

    def __init__(self, directory):
        from dread.elf import Elf
        from dread.emu import Layout
        from dread.program import Program
        from dread.target import Target
        self.directory = directory
        self.target = Target.load(directory)
        self.image = self.target.image()
        self.program = Program(self.target, self.image)
        self.layout = Layout(self.target, self.image)
        self.arch = self.image.arch
        self.width = max(1, self.arch.reg_bits // 8)       # bytes in a register (2 on the MSP430)
        self.word = self.layout.word                       # bytes in a stack argument word
        self.args = list(self.target.abi.arg_regs)         # names, in order: they carry results too
        self.fp_args = list(getattr(self.arch, "fp_arg_regs", lambda: [])())   # ARM: s0-s15
        self.float_results = None                          # {"float": regs, "double": regs}: calibrate()
        self.saved = list(self.layout.saved_names)
        self.regs = list(self.arch.general_regs()) + ["sp"] + ([self.arch.link_reg] if self.arch.link_reg else [])
        mem = {}
        for region in self.target.memory.regions:
            mem[region.name] = region.base
        self.scratch = self.target.memory.scratch_base
        self.sentinel = self.layout.sentinel
        self.aliases = {}                   # address -> every function name there (libgcc's __aeabi_uidiv
        for fn in self.program.functions:   # is __udivsi3), the import's own name first
            self.aliases.setdefault(fn.entry, []).append(fn.name)
        for sym in Elf(os.path.join(directory, "image.elf")).symbols:
            if sym.kind == 2 and sym.name and (sym.value & ~1) in self.aliases \
                    and sym.name not in self.aliases[sym.value & ~1]:
                self.aliases[sym.value & ~1].append(sym.name)
        self.entries = {n: a for a, names in self.aliases.items() for n in names}

    def function(self, name):
        """The image's function of a name, also by an alias (another symbol at its entry, such as a
        C++ constructor's complete and base variants, which GCC emits at one address); or None."""
        fn = self.program.by_name.get(name)
        if fn is None and name in self.entries:
            fn = self.program.by_entry.get(self.entries[name])
        return fn

    def names(self, address):
        """Every function name at an address, joined with " = "."""
        return " = ".join(self.aliases.get(address & ~1, ["0x%x" % address]))

    def machine(self):
        from dread.emu import Machine
        m = Machine(self.layout, self.image.payload(), isa_of=self.program.isa_at)
        m.plan = []
        return m

    def mark_fp(self, m):
        """A marker in every floating-point argument register (a core without an FPU keeps them
        in Unicorn's state all the same; its code never reads them)."""
        for i, name in enumerate(self.fp_args):
            m.uc.reg_write(self.arch.reg_id(name), fp_mark(i))

    def calibrate(self):
        """Where this build returns a float and a double: the registers in which the probes ret_f
        and ret_d leave 1.5 (r0 and r0:r1 with soft float; s0 and s0:s1 with the VFP variant),
        the VFP registers first where both hold it. Without those probes, or where neither holds
        it, the core result registers."""
        if self.float_results is not None:
            return self.float_results
        out = {"float": self.args[:max(1, 4 // self.width)], "double": self.args[:max(1, 8 // self.width)]}
        for kind, name, value in (("float", "ret_f", 0x3FC00000), ("double", "ret_d", 0x3FF8000000000000)):
            fn = self.function(name)
            if fn is None:
                continue
            m = self.machine()
            self.mark_fp(m)
            if _run(self, m, fn, [reg_mark(i, self.width) for i in range(len(self.args))]):
                continue
            n = self.sizes()[kind]
            # the VFP registers first: a soft-float build never writes them, and a hard-float one
            # may leave a copy in the core ones too (TI's armcl: ldm r0, {r0, r1}; vmov d0, r0, r1)
            for regs, width in ((self.fp_args, 4), (self.args, self.width)):
                if not regs:
                    continue
                k = max(1, n // width)
                got = sum(self.reg(m, r) << (8 * width * i) for i, r in enumerate(regs[:k]))
                if got == value:
                    out[kind] = list(regs[:k])
                    break
        self.float_results = out
        return out

    def reg(self, m, name):
        return m.uc.reg_read(self.arch.reg_id(name))

    def sizes(self):
        """The C types' sizes on this target (int and pointers as wide as a register, but at least
        two bytes)."""
        w = max(2, self.width)
        return {"char": 1, "short": 2, "int": w, "unsigned": w, "long": 4, "long long": 8, "i64": 8, "u64": 8,
                "ptr": w, "float": 4, "double": 8}

    def words(self, value):
        """A spec's value as register-wide words, lowest first (see the module)."""
        if isinstance(value, int):
            kind, v = "int", value
        else:
            kind, _, text = value.partition(":")
            if not text:
                kind, text = "ptr", value
            if text.startswith("scratch"):
                _, _, offset = text.partition("+")
                v = self.scratch + (int(offset, 0) if offset else 0)
            else:
                v = int(text, 0)
        size = self.sizes()[kind]
        v &= (1 << 8 * size) - 1
        n = max(1, -(-size // self.width))
        return [(v >> (8 * self.width * i)) & ((1 << 8 * self.width) - 1) for i in range(n)]

    def place(self, word, nbytes):
        """Where a marker came from (an argument register's name, a floating-point argument
        register's, "stack+N"), from its low nbytes bytes; None if no marker."""
        low = word & 0xFF
        mask = (1 << 8 * nbytes) - 1
        if 0x10 <= low < 0x10 + len(self.args) and (word & mask) == (reg_mark(low - 0x10, self.width) & mask):
            return self.args[low - 0x10]
        if 0x60 <= low < 0x60 + len(self.fp_args) and (word & mask) == (fp_mark(low - 0x60) & mask):
            return self.fp_args[low - 0x60]
        if 0x40 <= low < 0x40 + STACK_WORDS and (word & mask) == (stack_mark(low - 0x40, self.word) & mask):
            return "stack+%d" % (self.word * (low - 0x40))
        return None


# ---- measuring ----

def shape(bench, name):
    """{size, instructions, calls, tails, head, tail, mnemonics}: a probe function's code."""
    program, arch = bench.program, bench.arch
    fn = bench.function(name)
    if fn is None:
        return {"missing": True}
    insns = [i for i in program.instructions(fn) if i is not None]
    calls, tails, ijumps = [], [], []
    for insn in insns:
        kind, target, _ = arch.flow(insn)
        callee = program.by_entry.get(target & ~1) if isinstance(target, int) else None
        if kind == "call":
            calls.append(bench.names(callee.entry) if callee is not None else "0x%x" % target)
        elif kind == "icall":
            calls.append("(through a register)")
        elif kind == "jump" and callee is not None and callee is not fn:
            tails.append(bench.names(callee.entry))
        elif kind == "ijump":           # a switch's dispatch: tbb, a computed pc, a bl to a switch helper
            helper = None
            if insn.mnemonic in ("bl", "blx") and hasattr(arch, "target"):
                t = arch.target(insn)
                helper = program.by_entry.get(t & ~1) if isinstance(t, int) else None
            ijumps.append(bench.names(helper.entry) if helper is not None else
                          insn.mnemonic + (" pc" if insn.op_str.startswith("pc") else ""))
    text = ["%s %s" % (i.mnemonic, i.op_str) for i in insns]
    return {"size": program.end(fn) - fn.entry, "instructions": len(insns), "calls": calls, "tails": tails,
            "ijumps": ijumps,
            "head": text[:6], "tail": text[-4:], "mnemonics": sorted({i.mnemonic for i in insns})}


def _run(bench, m, fn, args):
    from dread.emu import EmuError
    try:
        m.call(fn.entry, *args, max_instructions=LIMIT)
    except EmuError as e:
        return str(e)
    return None


REF_SPAN = 32                       # bytes behind each argument register in the by-reference run


def ref_byte(i, offset):
    """Byte `offset` of the by-reference run's buffer behind argument register i: it names both
    (eight registers and 32 offsets in one byte)."""
    return (i & 7) << 5 | (offset & 31)


def measure_arg(bench, spec):
    """Where the argument comes from: a marker that comes back names a register or a stack word.
    When none does (an ABI that passes the value by reference: a struct on the MSP430, one wider
    than two words on RISC-V), a second run points every argument register at a buffer of its own
    whose bytes name the register and the offset (ref_byte), and the answer is "memory at r12+2"."""
    fn = bench.function(spec["function"])
    kind = spec.get("type", "int")
    size = bench.sizes()[kind]
    n = max(1, -(-size // bench.width))
    core = bench.args[:n]
    candidates = [core]
    if kind in ("float", "double"):             # where this build returns one, then the core registers:
        candidates = [bench.calibrate()[kind]]  # a variadic function returns by the base standard
        if candidates[0] != core:               # (AAPCS), whatever the build's float variant
            candidates.append(core)
    m = bench.machine()
    bench.mark_fp(m)
    args = [reg_mark(i, bench.width) for i in range(len(bench.args))]
    args += [stack_mark(k, bench.word) for k in range(STACK_WORDS)]
    error = _run(bench, m, fn, args)
    if not error:
        found = []
        for regs in candidates:
            width = bench.width if regs[0] in bench.args else 4
            words = [bench.reg(m, r) for r in regs]
            places = [bench.place(w, min(size, width)) for w in words]
            if all(places):
                found.append((regs, places, words, width))
        # a location holding another register's marker is where the value went; one holding its
        # own markers may just be untouched (with soft float the VFP registers keep theirs)
        moved = [f for f in found if f[1] != f[0]]
        if found:
            regs, places, words, width = (moved or found)[0]
            out = {"where": ":".join(places), "words": ["0x%0*x" % (2 * width, w) for w in words]}
            if regs != candidates[0]:
                out["returned"] = ":".join(regs)
            return out
    m = bench.machine()
    for i in range(min(8, len(bench.args))):
        m.write(bench.scratch + i * REF_SPAN, bytes(ref_byte(i, j) for j in range(REF_SPAN)))
    args = [bench.scratch + i * REF_SPAN for i in range(len(bench.args))]
    args += [stack_mark(k, bench.word) for k in range(STACK_WORDS)]
    second = _run(bench, m, fn, args)
    if not second:
        value = bench.reg(m, bench.args[0])
        width = min(size, bench.width)
        data = value.to_bytes(bench.width, "little")[:width]
        i, j = data[0] >> 5, data[0] & 31
        if i < len(bench.args) and all(data[k] == ref_byte(i, j + k) for k in range(1, width)):
            return {"where": "memory at %s+%d" % (bench.args[i], j), "by_reference": True}
    if error:
        return {"error": error}
    return {"where": "?", "words": ["0x%0*x" % (2 * bench.width, bench.reg(m, r)) for r in bench.args[:n]]}


def expected_bytes(bench, spec):
    """A result spec's value as the bytes it has in memory (little endian)."""
    sizes = bench.sizes()
    if "fields" in spec:
        size = sizes[spec.get("field", "int")]
        out = bytearray()
        for v in spec["fields"]:
            while len(out) % size:
                out.append(0)
            out += (v & ((1 << 8 * size) - 1)).to_bytes(size, "little")
        return bytes(out)
    size = sizes[spec.get("type", "int")]
    return (spec["value"] & ((1 << 8 * size) - 1)).to_bytes(size, "little")


def measure_result(bench, spec):
    fn = bench.function(spec["function"])
    want = expected_bytes(bench, spec)
    m = bench.machine()
    bench.mark_fp(m)
    m.write(bench.scratch, bytes(64))
    args = [bench.scratch] + [reg_mark(i, bench.width) for i in range(1, len(bench.args))]
    error = _run(bench, m, fn, args)
    if error:
        return {"error": error}
    if bytes(m.read(bench.scratch, len(want))) == want:
        # first: a value stored through the pointer (registers may hold it by the way: strd r2, r3)
        returned = " (%s holds the address afterwards)" % bench.args[0] if bench.reg(m, bench.args[0]) == bench.scratch \
            else ""
        return {"where": "memory at the address in %s%s" % (bench.args[0], returned)}
    places = [(bench.args, bench.width)]
    if bench.fp_args:                               # a float or double in the VFP registers: looked at
        fp = (bench.fp_args, 4)                     # first for those (a soft-float build never writes
        places = [fp] + places if spec.get("type") in ("float", "double") else places + [fp]
    for regs, width in places:                      # them; a hard-float one may leave a copy in r0:r1)
        n = -(-len(want) // width)
        if n > len(regs):
            continue
        got = b"".join(bench.reg(m, r).to_bytes(width, "little") for r in regs[:n])
        if got[:len(want)] == want:
            return {"where": ":".join(regs[:n])}
    return {"where": "?", "regs": ["0x%0*x" % (2 * bench.width, bench.reg(m, r)) for r in bench.args[:4]]}


def measure_value(bench, spec):
    fn = bench.function(spec["function"])
    m = bench.machine()
    error = _run(bench, m, fn, [])
    if error:
        return {"error": error}
    v = bench.reg(m, bench.args[0])
    return {"value": v, "hex": "0x%0*x" % (2 * bench.width, v)}


def return_address(bench, uc):
    """The return address of a function at its first instruction: the link register's, or the
    word a call pushed (the MSP430)."""
    if bench.arch.link_reg:
        return uc.reg_read(bench.arch.reg_id(bench.arch.link_reg)) & ~1
    sp = uc.reg_read(bench.arch.reg_id("sp"))
    return int.from_bytes(bytes(uc.mem_read(sp, bench.word)), "little")


def measure_op(bench, spec):
    """The op's one run: every function it calls (a call instruction, or the probe's own tail call),
    with the registers at the entry and at the return and the ones changed, by a hook on every
    instruction of the image (helpers are short). An entry reached by a branch is no call (a runtime
    library's routines branch into one another); a return may close several frames at once; a tail
    call returns where the probe would, so its frame closes when the run ends."""
    from unicorn import UC_HOOK_CODE
    program, arch = bench.program, bench.arch
    fn = bench.function(spec["function"])
    fn_end = program.end(fn)
    names = bench.regs
    ids = [arch.reg_id(n) for n in names]
    link = arch.link_reg
    m = bench.machine()
    args = [w for a in spec.get("args", []) for w in bench.words(a)]
    if any(isinstance(a, str) and "scratch" in a for a in spec.get("args", [])):
        m.write(bench.scratch, bytes(range(256)) * 2)
    frames, seen = [], []
    state = {"prev": None}              # (address, flow kind, mnemonic) of the instruction before
    tables = []                         # the probe's indirect jumps that ran as a switch's dispatch
    icalls = []                         # the probe's own calls through a register that ran

    def close(frame, after):
        frame["changed"] = [n for n, a, b in zip(names, frame["regs"], after) if a != b and n != link]
        frame["results"] = ["0x%0*x" % (2 * bench.width, after[names.index(r)]) for r in bench.args[:2]]
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
        own = prev is not None and fn.entry <= prev[0] < fn_end and not frames
        helper_bl = prev is not None and prev[2] in ("bl", "blx")
        if own and prev[1] == "ijump" and (helper_bl or callee is None or callee is fn):
            tables.append(prev[2])      # a table branch, a computed pc, a bl into a switch helper
        if callee is not None and callee is not fn and prev is not None:
            regs = [uc.reg_read(r) for r in ids]
            entry_args = ["0x%0*x" % (2 * bench.width, regs[names.index(r)]) for r in bench.args[:4]]
            if prev[1] in ("call", "icall"):
                if prev[1] == "icall" and not frames:
                    icalls.append(address)
                frames.append({"function": bench.names(address), "depth": len(frames) + 1, "regs": regs,
                               "ret": return_address(bench, uc), "args": entry_args})
            elif own and (prev[1] == "jump" or (prev[1] == "ijump" and not helper_bl)):
                frames.append({"function": bench.names(address), "depth": 1, "regs": regs,   # a tail call,
                               "ret": bench.sentinel, "tail": True, "args": entry_args})    # also through a
        insn = program.insn(address)                                                         # register
        state["prev"] = (address, arch.flow(insn)[0] if insn is not None else "next",
                         insn.mnemonic if insn is not None else "")
    m.hook_add(UC_HOOK_CODE, on_code, begin=bench.image.base, end=bench.image.base + len(bench.image.payload()) - 1)
    error = _run(bench, m, fn, args)
    if error:
        return {"error": error}
    after = [m.uc.reg_read(r) for r in ids]
    for frame in frames:                # tail calls: they return where the probe returns
        close(frame, after)
    out = {"result": ["0x%0*x" % (2 * bench.width, after[names.index(r)]) for r in bench.args[:2]], "helpers": [],
           "table_jumps": len(tables), "icalls": len(icalls)}
    for frame in seen:
        out["helpers"].append({k: frame[k] for k in ("function", "depth", "args", "results", "changed")}
                              | ({"tail": True} if frame.get("tail") else {}))
    return out


def lowbit(n):
    return n & -n


def measure_keeps(bench, spec):
    """Run with a marker in every register a call must keep (the ABI's callee-saved registers, the
    i-th the byte 0x20 + i across the register; the floating-point ones, ARM's s16-s31, 0x70 + i):
    which came back changed; which of them, and the
    return address, the function stored in its own frame (read after the run, between the stack
    pointer at its entry and the lowest its own instructions set); and its frame at every call it
    makes (bytes below the entry's stack pointer, before the call pushes anything). The largest
    power of two that divides every such frame is the alignment it kept (given an aligned entry:
    AAPCS asks for 8, RISC-V for 16, the MSP430 for 2)."""
    from unicorn import UC_HOOK_CODE
    program, arch = bench.program, bench.arch
    fn = bench.function(spec["function"])
    fn_end = program.end(fn)
    m = bench.machine()
    lay = bench.layout
    m.write(lay.stack_top - lay.stack_size, bytes(lay.stack_size))     # what the scan finds, this run wrote
    marks = {}
    for i, name in enumerate(bench.saved):
        marks[name] = int.from_bytes(bytes([0x20 + i]) * bench.width, "little")
        m.uc.reg_write(arch.reg_id(name), marks[name])
    for i, name in enumerate(getattr(arch, "fp_saved_regs", lambda: [])()):    # ARM: s16-s31
        marks[name] = int.from_bytes(bytes([0x70 + i]) * 4, "little")
        m.uc.reg_write(arch.reg_id(name), marks[name])
    sp_id = arch.reg_id("sp")
    state = {"prev": None, "entry": None, "low": None}
    frames = []

    def on_code(uc, address, size, user):
        prev = state["prev"]
        if fn.entry <= address < fn_end:
            sp = uc.reg_read(sp_id)
            if state["entry"] is None:
                state["entry"] = sp
            state["low"] = sp if state["low"] is None else min(state["low"], sp)
        elif prev is not None and prev[1] in ("call", "icall") and fn.entry <= prev[0] < fn_end \
                and state["entry"] is not None:
            sp = uc.reg_read(sp_id) + (0 if arch.link_reg else bench.word)     # before a pushed return
            frames.append(state["entry"] - sp)
        insn = program.insn(address)
        state["prev"] = (address, arch.flow(insn)[0] if insn is not None else "next")
    m.hook_add(UC_HOOK_CODE, on_code, begin=bench.image.base, end=bench.image.base + len(bench.image.payload()) - 1)
    args = [w for a in spec.get("args", []) for w in bench.words(a)]
    error = _run(bench, m, fn, args)
    if error:
        return {"error": error}
    broken = [name for name, v in marks.items() if bench.reg(m, name) != v]
    found, ret = set(), False
    if state["entry"] is not None and state["low"] is not None and state["entry"] > state["low"]:
        low = state["low"] - state["low"] % bench.width
        data = bytes(m.read(low, state["entry"] - low))
        by_value = {v: n for n, v in marks.items()}
        for off in range(0, len(data) - bench.width + 1, bench.width):
            v = int.from_bytes(data[off:off + bench.width], "little")
            if v in by_value:
                found.add(by_value[v])
            elif arch.link_reg and (v & ~1) == (bench.sentinel & ~1):
                ret = True
    out = {"kept": "all" if not broken else "not " + " ".join(broken),
           "saved": [n for n in list(marks) if n in found] + (["return address"] if ret else []),
           "frames_at_calls": sorted(set(frames))}
    nonzero = [f for f in frames if f]
    if nonzero:
        out["aligned"] = min(lowbit(f) for f in nonzero)
    return out


NOPS = {"arm": (b"\x00\xbf", b"\xc0\x46"), "riscv": (b"\x01\x00", b"\x13\x00\x00\x00"),
        "msp430": (b"\x03\x43",)}    # no-op encodings a compiler pads with (Thumb-2, Thumb-1; c.nop, nop)


def padding_kind(data, arch):
    """What padding bytes are: none, zeros, no-ops (the architecture's), or other bytes."""
    if not data:
        return "none"
    if not data.strip(b"\x00"):
        return "zeros"
    for nop in NOPS.get(arch, ()):
        if len(data) % len(nop) == 0 and data == nop * (len(data) // len(nop)):
            return "no-ops"
    return "other bytes"


def layout(directory, probe_files):
    """The probes' functions in a build's image: the alignment of their entries (the largest power
    of two dividing every entry, at most 64); what lies between each one's last instruction and the
    next function: a literal pool when some function of the image loads from it or takes its
    address pc-relatively (adr; a pool may serve a whole file, as TI's does), and the bytes in front
    of the pool or of the next function as padding, by kind; and how many start and end with each
    mnemonic."""
    import bisect
    import collections
    bench = Bench(directory)
    program, arch = bench.program, bench.arch
    data, base = bench.image.payload(), bench.image.base
    entries = sorted(f.entry for f in program.functions if f.insns)
    pc_address = getattr(arch, "pc_address", None)
    refs = set()
    for f in program.functions:
        for insn in program.instructions(f):
            if insn is None:
                continue
            lit = arch.literal(insn, f.isa)
            if lit:
                refs.add(lit[0])
            target = pc_address(insn, f.isa) if pc_address else None
            if target is not None:
                refs.add(target)
    refs = sorted(refs)
    aligned, pools, padding = 64, 0, collections.Counter()
    first, last = collections.Counter(), collections.Counter()
    names = [n for p in probe_files for n in p.functions if bench.function(n) is not None]
    for name in names:
        fn = bench.function(name)
        insns = [i for i in program.instructions(fn) if i is not None]
        if not insns:
            continue
        aligned = min(aligned, lowbit(fn.entry) or 64)
        first[insns[0].mnemonic] += 1
        last[insns[-1].mnemonic] += 1
        end = program.end(fn)
        i = bisect.bisect_right(entries, fn.entry)
        stop = min(entries[i] if i < len(entries) else base + len(data), base + len(data))
        j = bisect.bisect_left(refs, end)
        pool = refs[j] if j < len(refs) and refs[j] < stop else None
        pools += pool is not None
        padding[padding_kind(bytes(data[end - base:(pool if pool is not None else stop) - base]), arch.name)] += 1
    return {"functions": len(names), "aligned": aligned, "pools": pools, "padding": dict(padding),
            "first": first.most_common(), "last": last.most_common()}


MEASURES = {"arg": measure_arg, "result": measure_result, "value": measure_value, "op": measure_op,
            "keeps": measure_keeps}


# ---- lifting (run --lift) ----

def rebuild_compiler(adapter, picked, results):
    """The [compiler] of a build's dread.toml for `run --lift`: the compiler DREAD rebuilds the
    build's functions with. The adapter's [rebuild] table when it has one; else, by architecture,
    GCC for an ARM build's core (-mcpu from its cpu, through [cpus]; the float ABI as measured: a
    float argument in s0 is hard float) or clang for a RISC-V build's -march. None where the lab
    knows no rebuilding compiler (give the adapter a [rebuild] table)."""
    own = adapter.data.get("rebuild")
    if own:
        return dict(own)
    if adapter.arch == "arm":
        hard = str((results.get("pick_f_1") or {}).get("where", "")).startswith("s")
        flags = ["-mthumb", "-mcpu=%s" % (adapter.cpu_model(picked) or "cortex-m3")]
        flags += ["-mfloat-abi=hard", "-mfpu=auto"] if hard else ["-mfloat-abi=soft"]
        return {"cross": "arm-none-eabi-", "cflags": flags + ["-ffreestanding", "-Os", "-ffunction-sections"]}
    if adapter.arch == "riscv":
        march = next((v for v in picked.values() if str(v).startswith("rv32")), "rv32imc")
        return {"kind": "clang", "triple": "riscv32-unknown-elf",
                "cflags": ["-march=%s" % march, "-mabi=ilp32", "-Os", "-ffreestanding", "-fno-builtin",
                           "-ffunction-sections", "-fno-common", "-mno-relax"]}
    return None


def lift_build(directory, compiler, names):
    """dread lift --check on a measured build's probe functions, with `compiler` as the build's
    [compiler] (written into its dread.toml once): {"compiler", "verdicts": {function: [verdict,
    reason]}, "seconds"}, or {"error"} when the check did not run. A separate process, so that the
    check has a worker pool of its own; its output in the build's lift.log."""
    from dread.bench import dread as run, verdicts_of
    from dread.target import Target
    toml = os.path.join(directory, "dread.toml")
    with open(toml) as f:
        text = f.read()
    if "\n[compiler]" not in text:
        with open(toml, "a") as f:
            f.write("\n# dread cclab run --lift: the compiler DREAD rebuilds this build's functions with\n"
                    "[compiler]\n" + "".join("%s = %s\n" % (k, json.dumps(v)) for k, v in compiler.items()))
    seconds, code, out = run("lift", "--target", directory, "--check", *names,
                             log=os.path.join(directory, "lift.log"))
    target = Target.load(directory)
    verdicts = {fn: [verdict, reason] for fn, verdict, reason in verdicts_of(target, target.image())
                if fn in names}
    if not verdicts:
        return {"compiler": compiler, "error": "the lift check gave no verdicts (exit %d; lift.log)" % code}
    return {"compiler": compiler, "verdicts": verdicts, "seconds": round(seconds, 1)}


def measure(directory, probe_files):
    """{function: {measure, note, shape, and what the measure found}} of one build."""
    bench = Bench(directory)
    skipped = set()
    try:
        with open(os.path.join(directory, "build.json")) as f:
            skipped = set(json.load(f).get("skipped", []))
    except (OSError, ValueError):
        pass
    out = {}
    for p in probe_files:
        if p.name in skipped:           # not built by this adapter (C++ without compile_cxx)
            continue
        for spec in p.specs:
            name = spec["function"]
            entry = {"probe": p.name, "measure": spec["measure"], "note": spec.get("note", ""),
                     "shape": shape(bench, name)}
            if bench.function(name) is not None:
                try:
                    entry.update(MEASURES[spec["measure"]](bench, spec))
                except Exception as e:          # a measurement that breaks is a result, not a crash
                    entry["error"] = "%s: %s" % (type(e).__name__, e)
            else:
                entry["error"] = "not in the linked image"
            out[name] = entry
    return out
