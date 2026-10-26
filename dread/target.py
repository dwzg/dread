"""The target description: what DREAD knows about one firmware, read from its dread.toml.

A target is a directory with a dread.toml, usually the root of the repository that reconstructs
one firmware. Tools find it by `--target`, by $DREAD_TARGET, or by walking up from the working
directory. Every fact about the firmware a tool needs comes from here: the images and their load
addresses, the memory map, the calling convention, the compiler, where the tables are. A tool that
hardcodes such a fact is wrong (CLAUDE.md, rule 2).

A target has one or more images ([image.NAME]); the first is the default, $DREAD_IMAGE or
`--image` selects another. An image can override keys of [arch] (a Thumb boot loader in front of an
ARM-state application, for instance).

docs/target.md is the reference of the file. This module checks it (an unknown key is an error, so
a typo is not silently ignored), fills in the defaults and resolves paths against the target
directory.

    dread info [--target DIR] [--image NAME]      the target as DREAD reads it
"""
import argparse
import importlib
import importlib.util
import os
import sys
import tomllib

from dread import arch as archmod

FILE = "dread.toml"

# Default file names of an image's tables, in its config directory (docs/formats.md).
TABLES = {
    "functions": "functions.tsv",
    "code_ranges": "code_ranges.tsv",
    "symbols": "symbols.txt",
    "annotations": "annotations.txt",
    "data_ranges": "data_ranges.txt",
    "function_starts": "function_starts.txt",
    "asm_only": "asm_only.txt",
    "asm_raw_words": "asm_raw_words.txt",
    "lifted": "lifted.txt",
    "readable": "readable.txt",
    "readable_names": "readable_names.json",
    "readable_types": "readable_types.json",
    "domains": "domains.tsv",
    "stale_readers": "stale_readers.tsv",
    "wide_results": "wide_results.txt",
    "left_out": "left_out.txt",
}

# Known keys per section and their types. Sections in FREE belong to the tools that read them.
SCHEMA = {
    "target": {"name": str, "title": str},
    "arch": {"name": str, "isa": str, "profile": str, "cpu": str, "endian": str, "ghidra": str, "thumb2": bool,
             "extensions": str},
    "memory": {"regions": list, "io": list, "stack": dict, "sentinel": int, "scratch": dict},
    "abi": {"args": int, "callee_saved": list, "result": list, "float": str, "stack_args": dict,
            "formats": dict, "syscall_reads": list},
    "compiler": {"kind": str, "cross": str, "triple": str, "cflags": list, "lifted_cflags": list, "generated_std": str,
                 "cxxflags": list,
                 "ldflags": list,
                 "include": list, "idioms": str, "tools": dict, "profile": str},
    "paths": {"build": str, "src": str, "cases": str},
    "host": {"jobs": int, "free_cores": int, "nice": int},
    "llm": {"only": list, "device": str},
}
IMAGE_KEYS = {
    "original": str, "vendor": str, "container": str, "base": int, "limit": int, "erased": int,
    "entry": (int, str), "config": str, "cases": str, "extra_cases": str, "kept_cases": str,
    "record_specs": str, "src": str, "startup": (list, dict), "tables": dict, "arch": dict, "vectors": int,
    "free": list, "fixed": dict,
}
FREE = ("models", "plugins", "scenarios")


class TargetError(Exception):
    """A dread.toml that is missing, malformed or contradicts itself."""


def h(value):
    """An address as the tools print it."""
    return "0x%x" % value


def _check(section, data, schema, where):
    for key, value in data.items():
        if key not in schema:
            raise TargetError("%s: unknown key [%s] %s (known: %s)"
                              % (where, section, key, ", ".join(sorted(schema))))
        kind = schema[key]
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise TargetError("%s: [%s] %s should be %s, not %r" % (where, section, key,
                              getattr(kind, "__name__", kind), value))


class Region:
    """A range of the emulated address space: [base, base + size)."""

    def __init__(self, name, base, size, kind="ram"):
        self.name, self.base, self.size, self.kind = name, base, size, kind

    @property
    def end(self):
        return self.base + self.size

    def __contains__(self, address):
        return self.base <= address < self.end

    def __repr__(self):
        return "Region(%s, %s-%s, %s)" % (self.name, h(self.base), h(self.end), self.kind)


class Memory:
    """[memory]: what the emulator maps and what counts as a peripheral access.

    regions   [{name, base, size, kind}]; kind is "rom", "ram" or "alias" (separate memory standing
              for a mirror; not a mirror)
    io        [[low, high], ...]: peripheral ranges; every access there is logged in order
    stack     {top, size}: the stack a called function gets
    sentinel  the return address of a called function; execution stops when it gets there. On an
              M-profile core it has to lie in an executable region (0xe0000000 and up is
              execute-never there, and a return to it faults before it stops)
    scratch   {base, size}: memory nothing else uses, for the buffers of generated inputs
    """

    def __init__(self, data, where):
        self.regions = []
        for i, r in enumerate(data.get("regions", [])):
            if not isinstance(r, dict) or not {"base", "size"} <= set(r):
                raise TargetError("%s: [memory] regions[%d] needs base and size" % (where, i))
            unknown = set(r) - {"name", "base", "size", "kind"}
            if unknown:
                raise TargetError("%s: [memory] regions[%d]: unknown %s" % (where, i, sorted(unknown)))
            if r.get("kind", "ram") not in ("rom", "ram", "alias"):
                raise TargetError("%s: [memory] regions[%d] kind is rom, ram or alias" % (where, i))
            self.regions.append(Region(r.get("name", "region%d" % i), r["base"], r["size"],
                                       r.get("kind", "ram")))
        self.io = []
        for pair in data.get("io", []):
            if not (isinstance(pair, list) and len(pair) == 2 and pair[0] < pair[1]):
                raise TargetError("%s: [memory] io entries are [low, high] with low < high" % where)
            self.io.append((pair[0], pair[1]))
        stack = data.get("stack", {})
        self.stack_top = stack.get("top")
        self.stack_size = stack.get("size", 0x8000)
        self.sentinel = data.get("sentinel")
        scratch = data.get("scratch", {})
        self.scratch_base = scratch.get("base")
        self.scratch_size = scratch.get("size", 0x10000)
        spans = [(r.base, r.end, r.name) for r in self.regions] + [(a, b, "io") for a, b in self.io]
        spans.sort()
        for (a0, a1, an), (b0, b1, bn) in zip(spans, spans[1:]):
            if b0 < a1:
                raise TargetError("%s: [memory] %s and %s overlap" % (where, an, bn))

    def is_io(self, address):
        return any(lo <= address < hi for lo, hi in self.io)

    def region(self, address):
        for r in self.regions:
            if address in r:
                return r
        return None


class Abi:
    """[abi]: the calling convention as far as the tools need it.

    args          how many argument registers (r0-r3 on ARM: 4)
    callee_saved  the registers a function keeps for its caller
    result        the registers a result comes back in (r0, and r1 for 64-bit values)
    float         "hard": floats travel in the FPU's argument registers (ARM: s0-s15, the result in
                  s0 or s0:s1), as the VFP variant of the procedure call standard says; "soft": in
                  the core registers like integers. Default: hard where the compiler's flags say
                  -mfloat-abi=hard, else soft (set_float)
    stack_args    {name: words} of stack arguments the lifter cannot count
    formats       {name: index} of the functions that take a printf format besides the printf family
                  by name (dread.varargs FAMILY), with the index of the format argument: a call whose
                  format is a constant passes the registers the format takes
    syscall_reads the registers a system call's handler reads (svc, ecall): None where the target does
                  not say (every argument register is handed to it, as the lifted C does, and a case's
                  log shows none); a list (FreeRTOS's yield: []) makes readable C hand it those and the
                  registers read after the call alone, the cases log those at each system call, and
                  dread.regflow counts them read there
    """

    def __init__(self, data, arch):
        self.args = data.get("args", arch.default_args)
        self.callee_saved = list(data.get("callee_saved", arch.default_callee_saved))
        self.result = list(data.get("result", arch.default_result))
        for reg in self.callee_saved + self.result:
            arch.reg_id(reg)                    # raises on an unknown register name
        self.arg_regs = arch.arg_regs(self.args)
        self.float = data.get("float")
        # stack words named functions take where the lifter cannot count them (their stack pointer
        # is not static: a context switch)
        self.stack_args = dict(data.get("stack_args", {}))
        for name, words in self.stack_args.items():
            if not isinstance(words, int) or words < 0:
                raise ValueError("[abi] stack_args: %s takes a number of words, not %r" % (name, words))
        self.syscall_reads = data.get("syscall_reads")
        if self.syscall_reads is not None:
            self.syscall_reads = [str(r) for r in self.syscall_reads]
            for reg in self.syscall_reads:
                arch.reg_id(reg)                # raises on an unknown register name
        self.formats = dict(data.get("formats", {}))
        for name, index in self.formats.items():
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                raise ValueError("[abi] formats: %s takes the index of its format argument, not %r" % (name, index))
        self.fp_arg_regs = []

    def set_float(self, cflags, arch):
        """Settle `float` once the compiler's flags are known: the FPU's argument registers
        (fp_arg_regs) where floats travel in them."""
        if self.float is None:
            self.float = "hard" if "-mfloat-abi=hard" in cflags else "soft"
        if self.float not in ("hard", "soft"):
            raise ValueError('[abi] float is "hard" or "soft", not %r' % self.float)
        self.fp_arg_regs = list(getattr(arch, "fp_arg_regs", lambda: [])()) if self.float == "hard" else []


_LINKER_HELP = {}


def linker_knows(command, option):
    """Whether a linker lists option in its --help (asked once per command and process; a linker
    that cannot be run knows nothing)."""
    import subprocess
    key = tuple(command)
    if key not in _LINKER_HELP:
        try:
            got = subprocess.run(list(command) + ["--help"], capture_output=True, text=True, timeout=60)
            _LINKER_HELP[key] = got.stdout + got.stderr
        except (OSError, subprocess.SubprocessError):
            _LINKER_HELP[key] = ""
    return option in _LINKER_HELP[key]


class Compiler:
    """[compiler]: how C for this target is compiled.

    kind     "gcc" (default): a GNU cross toolchain, `cross` + gcc, as, ld, objcopy; "clang": clang
             --target=`triple` with its integrated assembler, ld.lld and llvm-objcopy
    cross    the GNU prefix (default arm-none-eabi-)
    triple   clang's --target (kind = "clang" needs it)
    cflags   the flags of the original's build, as far as known
    lifted_cflags  added after cflags for the lifted C (default ["-Os"], and what the architecture
             layer's lifted_cflags names for the compiler: the MSP430 with GCC -fno-ssa-phiopt, Thumb
             without Thumb-2 with GCC -fno-jump-tables, from what cflags say of the core): it
             is DREAD's translation, not the original's code; at -O0 every register of the original
             lives in the frame, slow, large and deep (FreeRTOS's GCC -O0 build: six functions more at
             -Os, -O2: four; none lost on four builds). [] compiles it with cflags alone
    generated_std  the C standard DREAD's own C (lifted and readable) is compiled to, in front of
             lifted_cflags (default gnu11; "" none): the toolchains' defaults differ, gnu90 for GCC
             4.9, gnu17 from GCC 5 to 14 and clang, gnu23 from GCC 15 on, and C23 accepted what C17
             refuses (a structure defined twice passed here and failed in CI, 2026-10-22). gnu11 is
             what GCC 4.7 and clang 3.1 on know (gnu17 needs GCC 8, which an older host toolchain
             lacks), and C17 changes nothing C11 accepts; GNU's, for the extensions the lifters write.
             A later -std in lifted_cflags overrides it
    cxxflags the flags for C++ sources in src (.cpp, .cc, .cxx): default cflags and CXX_FLAGS (no
             exceptions, no RTTI, no guards around local statics, no atexit registration: what the
             overlay can place is code and read-only data, not unwind tables, typeinfo objects or
             runtime calls of the C++ library)
    ldflags  flags for the linker (default: --no-warn-rwx-segments for a GNU ld that knows it, from
             binutils 2.39 on, none for lld or an older ld: Ubuntu 22.04's 2.38 refused it, and every
             link of a test failed so)
    include  further include directories
    idioms   the lifter's compiler profile
    profile  a dread cclab profile of the original's compiler (relative to the target's directory):
             dread.regflow takes its runtime helpers' results as read after every call
    tools    {"cc"|"cxx"|"as"|"ld"|"objcopy"|"nm": a command or a list} overriding the defaults
    """

    KINDS = ("gcc", "clang")

    def generated_cflags(self):
        """The flags DREAD's own C is compiled with after cflags: the pinned standard
        (generated_std), then lifted_cflags."""
        return (["-std=" + self.generated_std] if self.generated_std else []) + self.lifted_cflags
    CXX_FLAGS = ["-fno-exceptions", "-fno-rtti", "-fno-threadsafe-statics", "-fno-use-cxa-atexit"]

    def __init__(self, data, target):
        self.kind = data.get("kind", "gcc")
        if self.kind not in self.KINDS:
            raise TargetError("%s: [compiler] kind is one of %s, not %r" % (target.file, ", ".join(self.KINDS),
                                                                             self.kind))
        self.cross = data.get("cross", "arm-none-eabi-" if self.kind == "gcc" else "")
        self.triple = data.get("triple")
        if self.kind == "clang" and not self.triple:
            raise TargetError("%s: [compiler] kind = \"clang\" needs triple (clang's --target)" % target.file)
        self.cflags = list(data.get("cflags", []))
        arch = getattr(target, "arch", None)
        if hasattr(arch, "configure"):      # what the flags say about the core, which the layer's flags
            arch.configure(self.cflags)     # may depend on (ARM's on Thumb-2; configured again, it stays)
        self.layer_required = list(getattr(arch, "required_cflags", ()))
        layer = list(getattr(arch, "lifted_cflags", {}).get(self.kind, ()))
        self.lifted_cflags = list(data.get("lifted_cflags", ["-Os"] + layer))
        self.generated_std = data.get("generated_std", "gnu11")
        self.cxxflags = list(data.get("cxxflags", self.cflags + self.CXX_FLAGS))
        self.include = [target.path(p) for p in data.get("include", [])]
        self.idioms = data.get("idioms")
        self.tools = dict(data.get("tools", {}))
        self.profile = os.path.join(os.path.dirname(target.file), data["profile"]) if data.get("profile") else None
        self._profile_results = None
        if "ldflags" in data:
            self.ldflags = list(data["ldflags"])
        else:
            quiet = self.kind == "gcc" and linker_knows(self.command("ld"), "--no-warn-rwx-segments")
            self.ldflags = ["--no-warn-rwx-segments"] if quiet else []

    def profile_results(self):
        """{helper name: [result registers]} of the compiler profile ({} without one)."""
        if self._profile_results is None:
            self._profile_results = {}
            if self.profile:
                from dread.cclab import profile
                self._profile_results = profile.load(self.profile)
        return self._profile_results

    def command(self, name):
        """The command line (a list) that starts one tool: cc (compiles C, and assembly with
        -x assembler), cxx (compiles C++), as, ld, objcopy, nm."""
        if name in self.tools:
            value = self.tools[name]
            return list(value) if isinstance(value, list) else [value]
        if self.kind == "clang":
            clang = ["clang", "--target=" + self.triple]
            return {"cc": clang, "cxx": ["clang++"] + clang[1:], "as": clang + ["-c", "-x", "assembler"], "ld": ["ld.lld"],
                    "objcopy": ["llvm-objcopy"], "nm": ["llvm-nm"]}[name]
        return [self.cross + {"cc": "gcc", "cxx": "g++"}.get(name, name)]

    def assembler(self):
        """The command that assembles a .s file into an object (add -o OBJ FILE): GNU as takes the
        instruction set from the file's directives, clang from the flags (-march)."""
        if self.kind == "clang" and "as" not in self.tools:
            return self.command("as") + self.cflags
        return self.command("as")

    def required(self):
        """Flags dread recon adds to every compile: a section per function and no common
        symbols, so that the overlay can place each function; for GCC also
        -fno-reorder-functions (else main goes to .text.startup); and what the architecture layer
        names (required_cflags: RISC-V's -mno-relax)."""
        flags = ["-ffunction-sections", "-fno-common"]
        return flags + (["-fno-reorder-functions"] if self.kind == "gcc" else []) + \
            [f for f in self.layer_required if f not in self.cflags]

    def tool(self, name):
        """The program of one tool, as one string (a GNU toolchain's name; for clang, the first word)."""
        return self.command("cc" if name == "gcc" else name)[0]


class Copy:
    """What memory holds at `run` before the code runs, besides the image: code or data the startup
    copies from `stored` in the image, a range it zeroes ("zero"), or bytes the loader puts there
    ("load", `data`: an ELF loader such as QEMU's fills RAM from the file directly, and the startup
    copies nothing; the bytes come from a file of the target)."""

    def __init__(self, kind, run, size, stored=None, data=None):
        if kind not in ("code", "data", "zero", "load"):
            raise TargetError("startup copy kind is code, data, zero or load, not %r" % kind)
        if (stored is None) != (kind in ("zero", "load")):
            raise TargetError("a startup copy of kind %s %s a stored address"
                              % (kind, "needs" if kind in ("code", "data") else "has no"))
        if (data is None) != (kind != "load") or (data is not None and len(data) != size):
            raise TargetError("a startup copy of kind load needs its %d bytes (and only that kind has bytes)" % size)
        self.kind, self.run, self.size, self.stored, self.data = kind, run, size, stored, data

    def __contains__(self, address):
        return self.run <= address < self.run + self.size

    def __repr__(self):
        return "Copy(%s, run %s, %s bytes%s)" % (self.kind, h(self.run), h(self.size),
                                                 ", stored %s" % h(self.stored) if self.stored is not None else "")


class Image:
    """[image.NAME]: one image of the target.

    original  the raw image every comparison is made against, loaded at `base`
    vendor    the vendor's file the image comes from, when it has a container (optional)
    container plugin ("module:attr" or "file.py:attr") that unpacks and packs `vendor`
    base      the load address of `original`
    limit     the end of the room the overlay may use behind the image (default: the image's end)
    fixed     registers that hold one value in the whole program, {name = value}: RISC-V's gp
              (__global_pointer$, which GCC's code addresses small data from; import-elf writes it)
    free      ranges [[start, end], ...] inside the image that hold nothing (erased flash between the
              code and a vector table at the top): the overlay may put C there (dread import-elf
              writes them from the gaps between an ELF's sections)
    erased    the value of unprogrammed flash (default 0xff)
    entry     the reset entry, an address or a symbol name (default: base)
    vectors   where the exception vectors lie (A profile) or the vector table (M profile; the
              system run takes the stack pointer and the reset handler from it); default: base
    config    the directory of the image's tables (default "config")
    cases, extra_cases, kept_cases, record_specs, src: where its test cases and its C live
    startup   the startup's copies, as a list of {kind, run, size, stored}, {kind = "load", run, size,
              file} for bytes the loader puts into memory, or {plugin = "..."} naming a function
              plugin(image) that reads them from the image
    tables    {key: file name} overriding TABLES
    arch      keys of [arch] that differ for this image
    """

    def __init__(self, target, name, data):
        where = "%s [image.%s]" % (target.file, name)
        _check("image." + name, data, IMAGE_KEYS, target.file)
        for key in ("original", "base"):
            if key not in data:
                raise TargetError("%s: %s is required" % (where, key))
        self.target, self.name = target, name
        self.original = target.path(data["original"])
        self.vendor = target.path(data["vendor"]) if "vendor" in data else None
        self.container = data.get("container")
        self.base = data["base"]
        self._limit = data.get("limit")
        self.erased = data.get("erased", 0xFF)
        self._entry = data.get("entry", self.base)
        self.vectors = data.get("vectors", self.base)
        self.config = target.path(data.get("config", "config"))
        self.cases = target.path(data.get("cases", "tests/cases"))
        self.extra_cases = target.path(data.get("extra_cases", "tests/extra_cases"))
        self.kept_cases = target.path(data.get("kept_cases", "tests/kept_cases"))
        self.record_specs = target.path(data.get("record_specs", "tests/record_specs"))
        self.src = target.path(data.get("src", target.paths["src"]))
        unknown = set(data.get("tables", {})) - set(TABLES)
        if unknown:
            raise TargetError("%s: unknown tables %s (known: %s)" % (where, sorted(unknown), ", ".join(TABLES)))
        self.tables = dict(TABLES, **data.get("tables", {}))
        merged = dict(target.arch_data, **data.get("arch", {}))
        _check("image.%s.arch" % name, data.get("arch", {}), SCHEMA["arch"], target.file)
        self.arch = archmod.get(merged) if data.get("arch") else target.arch
        if self.arch is not target.arch and hasattr(self.arch, "configure"):
            self.arch.configure(target.compiler.cflags)
        self._startup = data.get("startup", [])
        self._free = data.get("free", [])
        self.fixed = dict(data.get("fixed", {}))
        for reg, value in self.fixed.items():
            if not isinstance(value, int):
                raise TargetError("%s: fixed takes register = value, not %s = %r" % (where, reg, value))
            try:
                self.arch.reg_id(reg)
            except (ValueError, KeyError):
                raise TargetError("%s: fixed names an unknown register %s" % (where, reg))
        for r in self._free:
            if not (isinstance(r, list) and len(r) == 2 and all(isinstance(v, int) for v in r) and r[0] < r[1]):
                raise TargetError("%s: free takes ranges [start, end], not %r" % (where, r))
        self._payload = None
        self._copies = None

    def table(self, key):
        """The path of one of the image's tables (it need not exist)."""
        return os.path.join(self.config, self.tables[key])

    def payload(self):
        """The bytes of `original`."""
        if self._payload is None:
            try:
                with open(self.original, "rb") as f:
                    self._payload = f.read()
            except OSError as e:
                raise TargetError("image %s: cannot read %s: %s" % (self.name, self.original, e.strerror))
        return self._payload

    @property
    def end(self):
        return self.base + len(self.payload())

    @property
    def limit(self):
        return self._limit if self._limit is not None else self.end

    @property
    def free(self):
        """[(start, end)] of the ranges inside the image that hold nothing, checked against it."""
        out = sorted(tuple(r) for r in self._free)
        for (lo, hi), nxt in zip(out, out[1:] + [None]):
            if not (self.base <= lo < hi <= self.end):
                raise TargetError("image %s: free range 0x%x-0x%x lies outside the image 0x%x-0x%x"
                                  % (self.name, lo, hi, self.base, self.end))
            if nxt is not None and nxt[0] < hi:
                raise TargetError("image %s: free ranges overlap at 0x%x" % (self.name, nxt[0]))
        return out

    @property
    def entry(self):
        """The reset entry as an address. A symbol name is looked up in the image's symbols."""
        if isinstance(self._entry, int):
            return self._entry
        from dread.program import read_symbols
        for kind, address, name in read_symbols(self.table("symbols")):
            if name == self._entry:
                return address
        raise TargetError("image %s: entry symbol %s is not in %s" % (self.name, self._entry, self.table("symbols")))

    def startup(self):
        """The startup's copies (list of Copy), from the description or its plugin."""
        if self._copies is None:
            spec = self._startup
            if isinstance(spec, dict):
                if set(spec) != {"plugin"}:
                    raise TargetError("image %s: startup as a table takes only plugin" % self.name)
                copies = self.target.plugin(spec["plugin"])(self)
            else:
                copies = []
                for i, c in enumerate(spec):
                    unknown = set(c) - {"kind", "run", "size", "stored", "file"}
                    if unknown or not {"kind", "run", "size"} <= set(c):
                        raise TargetError("image %s: startup[%d] has kind, run, size, stored and file"
                                          % (self.name, i))
                    data = None
                    if "file" in c:
                        try:
                            with open(self.target.path(c["file"]), "rb") as f:
                                data = f.read()
                        except OSError as e:
                            raise TargetError("image %s: startup[%d]: %s" % (self.name, i, e))
                    copies.append(Copy(c["kind"], c["run"], c["size"], c.get("stored"), data))
            self._copies = list(copies)
        return self._copies

    def __repr__(self):
        return "Image(%s, base %s)" % (self.name, h(self.base))


class Target:
    """A parsed dread.toml. `Target.load()` finds and reads one."""

    def __init__(self, file):
        self.file = os.path.abspath(file)
        self.root = os.path.dirname(self.file)
        try:
            with open(self.file, "rb") as f:
                data = tomllib.load(f)
        except OSError as e:
            raise TargetError("cannot read %s: %s" % (self.file, e.strerror))
        except tomllib.TOMLDecodeError as e:
            raise TargetError("%s: %s" % (self.file, e))
        for section in data:
            if section not in SCHEMA and section not in FREE and section != "image":
                raise TargetError("%s: unknown section [%s]" % (self.file, section))
        for section, schema in SCHEMA.items():
            _check(section, data.get(section, {}), schema, self.file)
        self.data = data
        if "name" not in data.get("target", {}):
            raise TargetError("%s: [target] name is required" % self.file)
        self.name = data["target"]["name"]
        self.title = data["target"].get("title", self.name)
        self.arch_data = data.get("arch", {})
        if "name" not in self.arch_data:
            raise TargetError("%s: [arch] name is required" % self.file)
        try:
            self.arch = archmod.get(self.arch_data)
        except ValueError as e:
            raise TargetError("%s: [arch] %s" % (self.file, e))
        self.memory = Memory(data.get("memory", {}), self.file)
        self.abi = Abi(data.get("abi", {}), self.arch)
        paths = data.get("paths", {})
        self.paths = {"build": paths.get("build", "build"), "src": paths.get("src", "src")}
        self.build = self.path(self.paths["build"])
        # where generated cases are cached (dread.autocase; the cases property): BUILD/autocases unless
        # [paths] cases says otherwise; copies of one target (a naming experiment's copy beside the target)
        # can share it, so that one's lift check spares the other's the generation (each entry carries its
        # key: an entry made for other code is made anew, never taken)
        self.cases_path = self.path(paths["cases"]) if "cases" in paths else None
        self.compiler = Compiler(data.get("compiler", {}), self)
        try:
            self.abi.set_float(self.compiler.cflags, self.arch)
        except ValueError as e:
            raise TargetError("%s: %s" % (self.file, e))
        if hasattr(self.arch, "configure"):     # what the compiler flags say about the core
            self.arch.configure(self.compiler.cflags)
        self.host = dict(data.get("host", {}))
        llm_data = data.get("llm", {})       # the functions an LLM may be shown (None: any)
        self.llm_only = set(llm_data["only"]) if "only" in llm_data else None
        self.llm_device = llm_data.get("device")    # what the image is for, as a person states it (dread pilot)
        self.models = data.get("models", {})
        self.plugins = data.get("plugins", {})
        self.scenarios = data.get("scenarios", {})
        images = data.get("image", {})
        if not images:
            raise TargetError("%s: no [image.NAME] section" % self.file)
        self.images = {}
        for name, d in images.items():
            if not isinstance(d, dict):
                raise TargetError("%s: [image.%s] is not a table" % (self.file, name))
            self.images[name] = Image(self, name, d)
        self.default_image = next(iter(self.images))

    @classmethod
    def load(cls, path=None, start=None):
        """The target named by `path` (a dread.toml or its directory), else $DREAD_TARGET, else the
        first dread.toml found walking up from `start` (default: the working directory)."""
        path = path or os.environ.get("DREAD_TARGET")
        if path:
            if os.path.isdir(path):
                path = os.path.join(path, FILE)
            if not os.path.exists(path):
                raise TargetError("no %s at %s" % (FILE, path))
            return cls(path)
        here = os.path.abspath(start or os.getcwd())
        while True:
            candidate = os.path.join(here, FILE)
            if os.path.exists(candidate):
                return cls(candidate)
            parent = os.path.dirname(here)
            if parent == here:
                raise TargetError("no %s here or above %s (use --target or $DREAD_TARGET)"
                                  % (FILE, os.path.abspath(start or os.getcwd())))
            here = parent

    @property
    def cases(self):
        """The case cache's directory: [paths] cases, else BUILD/autocases of the build directory as it is
        now (a tool or a test that points build elsewhere moves the default with it)."""
        return self.cases_path or os.path.join(self.build, "autocases")

    def cases_dir(self, image):
        """The directory of an image's cached generated cases ([paths] cases, default BUILD/autocases;
        IMAGE under it)."""
        return os.path.join(self.cases, image.name)

    def path(self, p):
        """A path of the description, relative to the target directory."""
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(self.root, p))

    def image(self, name=None):
        """The image `name`, else $DREAD_IMAGE, else the first."""
        name = name or os.environ.get("DREAD_IMAGE") or self.default_image
        if name not in self.images:
            raise TargetError("%s has no image %s (images: %s)" % (self.file, name, ", ".join(self.images)))
        return self.images[name]

    def plugin(self, spec):
        """Load "module:attr" or "file.py:attr" (relative to the target directory)."""
        if ":" not in spec:
            raise TargetError("plugin %r is not module:attr or file.py:attr" % spec)
        module, attr = spec.rsplit(":", 1)
        if module.endswith(".py") or os.sep in module or "/" in module:
            path = self.path(module)
            name = "dread_plugin_%x_%s" % (abs(hash(path)), os.path.basename(path)[:-3])
            mod = sys.modules.get(name)
            if mod is None:
                loader = importlib.util.spec_from_file_location(name, path)
                if loader is None:
                    raise TargetError("plugin %s: cannot load %s" % (spec, path))
                mod = importlib.util.module_from_spec(loader)
                sys.modules[name] = mod
                loader.loader.exec_module(mod)
        else:
            sys.path.insert(0, self.root)
            try:
                mod = importlib.import_module(module)
            finally:
                sys.path.remove(self.root)
        try:
            return getattr(mod, attr)
        except AttributeError:
            raise TargetError("plugin %s: %s has no %s" % (spec, module, attr))


def add_arguments(parser, image=True):
    parser.add_argument("--target", metavar="DIR", help="the target (a dread.toml or its directory); "
                        "default $DREAD_TARGET, or the nearest dread.toml above the working directory")
    if image:
        parser.add_argument("--image", metavar="NAME", help="the image of the target (default $DREAD_IMAGE, "
                            "or the first)")


def from_args(args):
    """(target, image) for parsed arguments of add_arguments."""
    target = Target.load(args.target)
    return target, target.image(getattr(args, "image", None))


def describe(target):
    lines = ["%s (%s), %s" % (target.title, target.name, target.file),
             "arch: %s" % target.arch.describe()]
    for name, img in target.images.items():
        try:
            size = "%d bytes" % len(img.payload())
        except TargetError:
            size = "missing"
        lines.append("image %s%s: %s at %s, limit %s, %s" % (
            name, " (default)" if name == target.default_image else "", os.path.relpath(img.original, target.root),
            h(img.base), h(img.limit) if size != "missing" or img._limit is not None else "?", size))
        if img.arch is not target.arch:
            lines.append("  arch: %s" % img.arch.describe())
        present = [k for k in TABLES if os.path.exists(img.table(k))]
        lines.append("  tables in %s: %s" % (os.path.relpath(img.config, target.root), ", ".join(present) or "none"))
        try:
            for c in img.startup():
                lines.append("  startup: %r" % c)
        except TargetError as e:
            lines.append("  startup: %s" % e)
    m = target.memory
    for r in m.regions:
        lines.append("memory %-10s %s-%s %s" % (r.name, h(r.base), h(r.end), r.kind))
    for lo, hi in m.io:
        lines.append("io         %s-%s" % (h(lo), h(hi)))
    if m.stack_top is not None:
        lines.append("stack      top %s, %s bytes" % (h(m.stack_top), h(m.stack_size)))
    if m.sentinel is not None:
        lines.append("sentinel   %s" % h(m.sentinel))
    if m.scratch_base is not None:
        lines.append("scratch    %s, %s bytes" % (h(m.scratch_base), h(m.scratch_size)))
    lines.append("abi: arguments %s, callee-saved %s, result %s" % (
        ", ".join(target.abi.arg_regs), ", ".join(target.abi.callee_saved), ", ".join(target.abi.result)))
    lines.append("compiler: %s %s" % (" ".join(target.compiler.command("cc")), " ".join(target.compiler.cflags)))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread info", description="Show the target as DREAD reads it.")
    add_arguments(parser, image=False)
    args = parser.parse_args(argv)
    try:
        target = Target.load(args.target)
    except TargetError as e:
        print("dread: %s" % e, file=sys.stderr)
        return 1
    print(describe(target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
