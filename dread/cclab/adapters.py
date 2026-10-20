"""Compiler adapters of dread cclab: how one compiler, and its linker, compiles and links the probes.

An adapter is a TOML file: the built-in ones lie in dread/cclab/adapters/ (`dread cclab adapters`
lists them), and a copy of one, edited, is the way to fit a compiler installed elsewhere or with
other options. Its fields:

    name = "gcc-arm"          # the builds' directories are named after it
    family = "gnu"            # how the probes are linked: "gnu" (a GNU ld script), "iar" (an ILINK
                              # configuration) or "ti" (a TI linker command file); each puts code and
                              # data where the emulator has memory
    windows = false           # Windows executables run from WSL: every file name in a command goes
                              # through `wslpath -w`, and the work directory should be on the
                              # Windows side (/mnt/c/...), where the Windows program sees it
    support = true            # link the lab's support file (memcpy and the like, for a link without
                              # a C library); false where the toolchain's own library is linked
    cxx_support = true        # link the lab's C++ pieces with C++ probes (operator new and delete,
                              # __cxa_pure_virtual); default: as support
    guard = false             # a proprietary toolchain: its runtime library must not reach an LLM;
                              # `run` then writes the work directory's CLAUDE.md and deny rules, and
                              # each build's dread.toml limits an LLM to the probes (dread.cclab.workdir)
    arch = "arm"              # the emulator's architecture layer, as [arch] in a dread.toml
    isa = "t32"
    profile = "m"

    [tools]                   # a name on PATH, a path, or a glob (the newest match is taken:
    cc = "arm-none-eabi-gcc"  # "/mnt/c/Program Files/IAR Systems/*/arm/bin/iccarm.exe"); or a list
                              # of those, the first that exists taken (install places differ)
    ld = "arm-none-eabi-gcc"

    [commands]                # argument lists; {cc} and {ld} are the tools, {src}, {obj}, {out} and
                              # {script} file names; {flags}, {ldflags}, {objs} and {keep} expand to
                              # several arguments ({keep}: [keep] once per probe function)
    version = ["{cc}", "--version"]
    compile = ["{cc}", "-c", "{src}", "-o", "{obj}", "{flags}"]
    compile_cxx = [...]       # how a C++ probe (.cpp) is compiled; without it C++ probes are skipped
    link = ["{ld}", "{objs}", "-o", "{out}", "-T", "{script}", "-nostdlib", "{ldflags}", "-lgcc"]
    keep = []                 # the arguments that keep one function in the link ("{symbol}")

    [matrix]                  # every combination of one choice per dimension is one build
    cpu = {"cortex-m3" = ["-mcpu=cortex-m3", "-mthumb"]}
    opt = {"O0" = ["-O0"], "O2" = ["-O2"]}

    [cpus]                    # a matrix cpu key as the emulator's CPU model (DREAD's [arch] cpu);
    "cortex-m3" = "cortex-m3" # default: the key itself

    cflags = []               # options of every compile, after the matrix's
    ldflags = []              # options of every link
    support_flags = []        # options for the support file only
    script = ""               # the linker script or configuration, if the family's does not suit:
                              # a template with {flash:x}, {flash_size:x}, {ram:x}, {ram_size:x},
                              # {stack:x}, {stack_top:x}, {scratch:x}, {scratch_size:x} and
                              # {sentinel:x} (braces of the script itself doubled: {{ }}); the lab's
                              # own for each family is in dread/cclab/lab.py (GNU_SCRIPT, ICF, TI_CMD)

What an adapter does not establish: that the options mean what their names say. The doctor
(`dread cclab doctor`) compiles, links and runs a trivial probe with the first build of the matrix,
and the results of every build carry the compiler's own version text.
"""
import glob
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 300                       # seconds one compiler or linker run may take


class AdapterError(Exception):
    pass


def builtin():
    """{name: path} of the adapters that come with DREAD."""
    out = {}
    for path in sorted(glob.glob(os.path.join(HERE, "adapters", "*.toml"))):
        out[os.path.splitext(os.path.basename(path))[0]] = path
    return out


# Compiler Explorer's compilers by architecture and kind: the built-in adapter each is made from, and
# the local assembler of what it writes (dread.cclab.ce)
CE_BASES = {("arm", "gcc"): ("gcc-arm", "arm-none-eabi-as"),
            ("arm", "clang"): ("clang-arm", "clang -c -x assembler"),
            ("riscv", "gcc"): ("gcc-riscv", "riscv64-elf-as"),
            ("msp430", "gcc"): ("gcc-msp430", "msp430-elf-as")}


def ce_adapter(ident):
    """The adapter of Compiler Explorer's compiler ident (dread.cclab.ce): the built-in one of its
    architecture and kind, compiling there and assembling here; its C++ probes go to the C++
    compiler of the same id without the C prefix, and are skipped when there is none."""
    import copy
    import sys
    import tomllib
    from dread.cclab import ce
    try:
        c = ce.compiler(ident, "c")
    except ce.CompilerExplorerError as e:
        raise AdapterError(str(e))
    iset = (c.get("instructionSet") or "").lower()
    kind = "clang" if "clang" in ((c.get("compilerType") or "") + c["name"]).lower() else "gcc"
    arch = ("arm" if iset == "arm32" or (iset.startswith("arm") and "64" not in iset) else
            "riscv" if iset.startswith("riscv") else "msp430" if "msp430" in iset else iset)
    if (arch, kind) not in CE_BASES:
        raise AdapterError("no adapter for Compiler Explorer's %s (%s: %s, %s)" % (ident, c["name"], iset, kind))
    base, assembler = CE_BASES[(arch, kind)]
    with open(builtin()[base], "rb") as f:
        data = copy.deepcopy(tomllib.load(f))
    data["name"] = "ce-" + ident
    data["ce"] = ident
    data["guard"] = False
    script = os.path.abspath(ce.__file__)       # (by its file: it needs nothing of DREAD, whichever is installed)
    driver = [sys.executable, script, "--compiler", ident, "--as", assembler]
    commands = data["commands"]
    commands["version"] = driver + ["--version"]
    commands["compile"] = driver + ["--"] + [w for w in commands["compile"][1:]]
    cxx = ident[1:] if ident.startswith("c") else None
    try:
        known = cxx is not None and any(x["id"] == cxx for x in ce.compilers("c++"))
    except ce.CompilerExplorerError:
        known = False
    if known and "compile_cxx" in commands:
        rest = [w for w in commands["compile_cxx"][1:]]
        if rest[:2] == ["-x", "c++"]:
            rest = rest[2:]
        commands["compile_cxx"] = [sys.executable, script, "--compiler", cxx, "--lang", "c++",
                                   "--as", assembler, "--"] + rest
    else:
        commands.pop("compile_cxx", None)
    return Adapter(data, "ce:" + ident)       # (the spec load() makes it from again: the lab's workers do)


RELINK_BASES = {"arm": "gcc-arm", "riscv": "gcc-riscv"}


def relink_adapter(directory):
    """An adapter of the objects a compiler made elsewhere, measured here: a bundle's adapter
    directory (WORK/ADAPTER as `bundle` packs it: each build's obj/ and version.txt). The compile
    step copies the bundle's object of each build and file (dread/cclab/relink.py); the link is the
    GNU one of the built-in adapter of the bundle's architecture, with the C library and libgcc for
    the runtime helpers by their standard names and the lab's relink_support.c for the C++ type_info
    tables, so that nothing of the other compiler's library takes part (a compiler that runs at work
    only: its code is measured and lifted at home). Its builds are the bundle's: a cpu and an
    optimisation each (cortex-m3-Ohz), the cpu's link flags the base adapter's."""
    import copy
    import json
    import sys
    import tomllib
    directory = os.path.abspath(os.path.expanduser(directory)).rstrip("/")
    if not os.path.isdir(directory):
        raise AdapterError("relink: no directory %s" % directory)
    builds = sorted(d for d in os.listdir(directory)
                    if os.path.isdir(os.path.join(directory, d, "obj")) and not d.startswith("doctor-"))
    if not builds:
        raise AdapterError("relink: %s holds no builds with objects (a bundle's WORK/ADAPTER)" % directory)
    arch = "arm"
    for b in builds:
        path = os.path.join(directory, b, "results.json")
        if os.path.exists(path):
            with open(path) as f:
                arch = json.load(f).get("arch", arch)
            break
    if arch not in RELINK_BASES:
        raise AdapterError("relink: no GNU adapter to link %s objects with" % arch)
    with open(builtin()[RELINK_BASES[arch]], "rb") as f:
        base = tomllib.load(f)
    data = copy.deepcopy(base)
    data["name"] = os.path.basename(directory) + "-relinked"
    data["guard"] = False
    objs = os.listdir(os.path.join(directory, builds[0], "obj"))
    data["support"] = "support.o" in objs           # the bundle's builds bring the lab's own files
    data["cxx_support"] = "cxx_support.o" in objs
    here = os.path.dirname(os.path.abspath(__file__))
    driver = [sys.executable, os.path.join(here, "relink.py"), directory]
    commands = data["commands"]
    commands["version"] = driver + ["--version"]
    commands["compile"] = driver + ["{obj}"]
    commands["compile_cxx"] = driver + ["{obj}"]
    link = list(commands["link"])
    at = link.index("{objs}") + 1
    link[at:at] = [os.path.join(here, "relink_support.c")]
    link[len(link) - 1:len(link) - 1] = ["-lc"]      # (before libgcc, the last)
    commands["link"] = link
    cpus = base["matrix"].get("cpu", {})
    data["matrix"] = {"cpu": {c: cpus.get(c, ["-mcpu=%s" % c, "-mthumb"]) for c in
                              sorted({b.rsplit("-", 1)[0] for b in builds})},
                      "opt": {o: [] for o in sorted({b.rsplit("-", 1)[1] for b in builds})}}
    data.pop("cflags", None)
    data["relink"] = directory
    return Adapter(data, "relink:" + directory)


def load(spec):
    """The adapter named spec (a built-in one), in the file spec, ce:ID for Compiler Explorer's
    compiler ID (ce_adapter), or relink:DIR for a bundle's objects linked here (relink_adapter)."""
    import tomllib
    if spec.startswith("ce:"):
        return ce_adapter(spec[3:])
    if spec.startswith("relink:"):
        return relink_adapter(spec[len("relink:"):])
    path = builtin().get(spec, spec)
    if not os.path.exists(path):
        raise AdapterError("no adapter %s (built-in: %s; or a file)" % (spec, ", ".join(builtin())))
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise AdapterError("adapter %s: %s" % (path, e))
    return Adapter(data, path)


class Adapter:
    """One compiler as the lab runs it (see the module)."""

    FAMILIES = ("gnu", "iar", "ti")

    def __init__(self, data, path):
        self.path = path
        self.data = data
        for key in ("name", "tools", "commands", "matrix"):
            if key not in data:
                raise AdapterError("adapter %s: %s is missing" % (path, key))
        self.name = data["name"]
        self.family = data.get("family", "gnu")
        if self.family not in self.FAMILIES:
            raise AdapterError("adapter %s: family is one of %s, not %r" % (path, ", ".join(self.FAMILIES),
                                                                           self.family))
        self.windows = bool(data.get("windows", False))
        self.guard = bool(data.get("guard", False))
        self.support = bool(data.get("support", self.family == "gnu"))
        self.cxx_support = bool(data.get("cxx_support", self.support))
        self.arch = data.get("arch", "arm")
        self.isa = data.get("isa", "t32")
        self.profile = data.get("profile", "m")
        self.tools = dict(data["tools"])
        self.commands = dict(data["commands"])
        for key in ("compile", "link"):
            if key not in self.commands:
                raise AdapterError("adapter %s: [commands] %s is missing" % (path, key))
        self.matrix = {dim: dict(choices) for dim, choices in data["matrix"].items()}
        self.cpus = dict(data.get("cpus", {}))
        self.cflags = list(data.get("cflags", []))
        self.ldflags = list(data.get("ldflags", []))
        self.support_flags = list(data.get("support_flags", []))
        self.script = data.get("script") or None
        self.ce = data.get("ce")                # Compiler Explorer's compiler id (ce_adapter)
        self._resolved = {}

    # ---- the builds ----

    def builds(self):
        """[(key, {dimension: choice}, flags)] of every combination of the matrix, in its order:
        key joins the choices with "-" (cortex-m3-O2)."""
        out = [("", {}, [])]
        for dim, choices in self.matrix.items():
            out = [(key + ("-" if key else "") + choice, dict(picked, **{dim: choice}), flags + list(extra))
                   for key, picked, flags in out for choice, extra in choices.items()]
        return out

    def cpu_model(self, picked):
        """The emulator's CPU model for a build (its matrix cpu choice, through [cpus])."""
        key = picked.get("cpu")
        return self.cpus.get(key, key) if key else None

    # ---- running the tools ----

    def tool(self, name):
        """The executable (or directory) of a tool: a glob's newest match, a path, or a name on
        PATH; of a list, the first of those that exists."""
        if name in self._resolved:
            return self._resolved[name]
        spec = self.tools.get(name)
        if spec is None:
            raise AdapterError("adapter %s: [tools] %s is missing" % (self.path, name))
        candidates = spec if isinstance(spec, list) else [spec]
        found = None
        for one in candidates:
            one = os.path.expanduser(one)
            if any(c in one for c in "*?["):
                matches = sorted(glob.glob(one))
                if matches:
                    found = matches[-1]
                    break
            elif os.sep not in one:
                if shutil.which(one):
                    found = shutil.which(one)
                    break
            elif os.path.exists(one):
                found = one
                break
        if found is None:
            if len(candidates) > 1 or any(c in candidates[0] for c in "*?["):
                raise AdapterError("%s: none of %s exists" % (name, ", ".join(candidates)))
            found = os.path.expanduser(candidates[0])      # (the run says it is missing)
        self._resolved[name] = found
        return found

    def host_path(self, path):
        """A file name as the tool sees it: through `wslpath -w` for a Windows tool under WSL."""
        path = os.path.abspath(path)
        if not self.windows:
            return path
        try:
            got = subprocess.run(["wslpath", "-w", path], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise AdapterError("wslpath: %s (windows = true needs WSL)" % e)
        if got.returncode:
            raise AdapterError("wslpath -w %s: %s" % (path, got.stderr.strip()))
        return got.stdout.strip()

    def command(self, which, **values):
        """The argument list of a command template, filled in (see the module)."""
        template = self.commands.get(which)
        if template is None:
            raise AdapterError("adapter %s: [commands] %s is missing" % (self.path, which))
        files = {k: self.host_path(v) for k, v in values.items() if k in ("src", "obj", "out", "script")}
        scalars = dict(files)
        for name in self.tools:
            if "{%s}" % name in " ".join(template):
                scalars[name] = self.tool(name)
        out = []
        for part in template:
            if part == "{flags}":
                out += list(values.get("flags", []))
            elif part == "{ldflags}":
                out += list(values.get("ldflags", []))
            elif part == "{objs}":
                out += [self.host_path(o) for o in values.get("objs", [])]
            elif part == "{keep}":
                for symbol in values.get("keep", []):
                    out += [k.replace("{symbol}", symbol) for k in self.commands.get("keep", [])]
            else:
                try:
                    out.append(part.format(**scalars))
                except KeyError as e:
                    raise AdapterError("adapter %s: [commands] %s uses %s, which is not known there" % (
                        self.path, which, e))
        return out

    def run(self, argv, cwd):
        """(exit status, output) of one tool run; output is stdout and stderr together, decoded
        leniently (a Windows tool may write in its code page)."""
        os.makedirs(cwd, exist_ok=True)     # (a missing cwd raised the same error as a missing tool)
        try:
            got = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=TIMEOUT)
        except FileNotFoundError:
            return 127, "%s: not found" % argv[0]
        except subprocess.TimeoutExpired:
            return 124, "%s: no end within %d s" % (argv[0], TIMEOUT)
        except OSError as e:
            return 126, "%s: %s" % (argv[0], e)
        text = (got.stdout + got.stderr).decode("utf-8", errors="replace")
        return got.returncode, text

    def version(self, cwd="."):
        """The compiler's own version text (the version command's first lines), or the error."""
        if "version" not in self.commands:
            return "(no version command)"
        code, text = self.run(self.command("version"), cwd)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines[:3]) if code == 0 else "(version failed, %d: %s)" % (code, " ".join(lines[:2]))
