"""dread doctor: what DREAD needs on this machine, what it finds, and what to do about what is missing.

It looks and changes nothing. Each thing it looks for is a finding: what it is, whether it is essential
or optional (and what needs it), what was found (a version, a path) or why it is wrong, and how to get
it: the command for this system's package manager (apt, pacman, dnf or brew, from /etc/os-release or the
platform; all four with --all or where the system is not known) and pip for Python's packages, printed,
never run. The findings:
- Python 3.11 or newer, unicorn 2.1.4 (pinned: the tools rely on its hook behaviour) and capstone 5:
  what every command needs (essential);
- z3-solver (dread readable proves its rewrites with it; the `prove` extra), pyelftools (dread readable
  --score and dread bench read debug information with it; `dwarf`), pytest (DREAD's own tests; `test`),
  Playwright and a Chrome or Chromium (tests/test_gui_browser.py; `browser`);
- the cross compilers by architecture: GCC for ARM (arm-none-eabi-), for RISC-V (riscv64-elf- or
  riscv64-unknown-elf-, with the -march it takes for RV32IMAC: GCC 12 and later need _zicsr for the
  CSR instructions, GCC 10 does not know it) and for the MSP430 (TI's msp430-elf-), each with its
  binutils; clang with ld.lld, llvm-objcopy and llvm-nm, and the targets of DREAD's architectures it
  has (arm, thumb, riscv32, mipsel, msp430);
- podman (dread bench's toolchain images, bin/with-toolchain), git (dread bench fetch), Ghidra with a
  java (dread ghidra: the functions of an image without symbols);
- the language-model providers of llm.toml ($DREAD_LLM, else ~/.config/dread/llm.toml): each one's
  kind and model, whether its key is there (dread.llm key_status: from the environment or a key file,
  never shown), a local server whether it answers (GET /v1/models, within --timeout seconds; a server
  elsewhere is not contacted), a command provider whether its program is on PATH;
- with a target (--target, $DREAD_TARGET, or a dread.toml above the working directory): the compiler,
  assembler, linker and objcopy its [compiler] names, and whether the compiler takes its cflags
  (an empty file preprocessed with them). Essential: dread recon and the lift check compile with them.

Exit status 0 when every essential finding is there, 1 otherwise. --json prints the findings as JSON
(dread gui's Setup page shows the same: check()). What it finds depends on the machine and the moment
(a server that is slow to answer), so its output is not deterministic. Not established: that a compiler
found builds every target of its architecture (its multilibs, its C library), and that a key is valid
(only that one is there: `dread llm ask` tries a provider).

    dread doctor [--target DIR] [--json] [--all] [--timeout SECONDS]
"""
import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ESSENTIAL, OPTIONAL = "essential", "optional"
OK, MISSING, WRONG = "ok", "missing", "wrong"
MANAGERS = ("apt", "pacman", "dnf", "brew")
TIMEOUT = 2.0               # seconds a local model server has to answer
PROBE = 20.0                # seconds a compiler or tool has to answer --version and the like
OS_RELEASE = "/etc/os-release"
BROWSERS = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser")
CLANG_TARGETS = ("arm", "thumb", "riscv32", "mipsel", "msp430")     # clang's names of DREAD's architectures
RISCV_GCC = ("riscv64-elf-", "riscv64-unknown-elf-")
RISCV_MARCH = ("rv32imac_zicsr", "rv32imac")

# How to get what is missing: the package manager's command (None: none known), pip's, words.
FIXES = {
    "python": {"text": "Python 3.11 or newer (tomllib): make DREAD's virtual environment with it "
                       "(python3.12 -m venv .venv && .venv/bin/pip install -e '.[test]')",
               "apt": "sudo apt install python3 python3-venv", "pacman": "sudo pacman -S python",
               "dnf": "sudo dnf install python3", "brew": "brew install python"},
    "arm-gcc": {"apt": "sudo apt install gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi",
                "pacman": "sudo pacman -S arm-none-eabi-gcc arm-none-eabi-binutils arm-none-eabi-newlib",
                "dnf": "sudo dnf install arm-none-eabi-gcc-cs arm-none-eabi-binutils-cs arm-none-eabi-newlib",
                "brew": "brew install arm-none-eabi-gcc arm-none-eabi-binutils"},
    "riscv-gcc": {"apt": "sudo apt install gcc-riscv64-unknown-elf binutils-riscv64-unknown-elf "
                         "picolibc-riscv64-unknown-elf",
                  "pacman": "sudo pacman -S riscv64-elf-gcc riscv64-elf-binutils riscv64-elf-newlib",
                  "dnf": None, "brew": "brew install riscv64-elf-gcc riscv64-elf-binutils",
                  "text": "Fedora packages no bare-metal RISC-V GCC as far as known: clang with lld compiles for "
                          "RV32 as well (a target's [compiler] kind = \"clang\")"},
    "clang": {"apt": "sudo apt install clang lld llvm", "pacman": "sudo pacman -S clang lld llvm",
              "dnf": "sudo dnf install clang lld llvm", "brew": "brew install llvm lld",
              "text": "Homebrew's llvm is not on PATH by itself: add $(brew --prefix llvm)/bin"},
    "msp430-gcc": {"text": "TI's MSP430 GCC (msp430-elf-gcc and its binutils: MSP430-GCC-OPENSOURCE on ti.com), "
                           "unpacked, its bin directory on PATH; the distributions' main repositories have none "
                           "as far as known (Debian's gcc-msp430 is the older msp430- one)"},
    "podman": {"apt": "sudo apt install podman", "pacman": "sudo pacman -S podman", "dnf": "sudo dnf install podman",
               "brew": "brew install podman"},
    "git": {"apt": "sudo apt install git", "pacman": "sudo pacman -S git", "dnf": "sudo dnf install git",
            "brew": "brew install git"},
    "ghidra": {"pacman": "sudo pacman -S ghidra", "brew": "brew install --cask ghidra", "apt": None, "dnf": None,
               "text": "or Ghidra's release from github.com/NationalSecurityAgency/ghidra/releases, unpacked, "
                       "GHIDRA_INSTALL_DIR set to its directory (dread ghidra also looks in /opt/ghidra); it runs "
                       "on a JDK, the version its installation guide names"},
    "java": {"apt": "sudo apt install openjdk-21-jdk", "pacman": "sudo pacman -S jdk21-openjdk",
             "dnf": "sudo dnf install java-21-openjdk-devel", "brew": "brew install openjdk@21",
             "text": "the JDK the Ghidra release names (21 for Ghidra 11.2 and later, not confirmed for others)"},
    "chrome": {"text": "a Chrome or Chromium on PATH, or Playwright's own: python -m playwright install chromium"},
}
# Python's packages: (distribution, module, need, what needs it, pip requirement, extra).
PACKAGES = (
    ("unicorn", "unicorn", ESSENTIAL, "the emulator every comparison runs in (pinned: the tools rely on its hook "
                                      "behaviour)", "unicorn==2.1.4", None),
    ("capstone", "capstone", ESSENTIAL, "the disassembler of ARM, RISC-V and MIPS", "capstone>=5,<6", None),
    ("z3-solver", "z3", OPTIONAL, "dread readable proves its rewrites of conditions and values with it",
     "z3-solver>=4.12", "prove"),
    ("pyelftools", "elftools", OPTIONAL, "dread readable --score and dread bench read debug information with it",
     "pyelftools>=0.29", "dwarf"),
    ("pytest", "pytest", OPTIONAL, "DREAD's own tests", "pytest", "test"),
    ("playwright", "playwright", OPTIONAL, "tests/test_gui_browser.py looks at dread gui's pages in a browser with it",
     "playwright>=1.40", "browser"),
)


# ---- looking ----

def run(cmd, timeout=PROBE, stdin=""):
    """(exit status, output and errors) of a command, or (None, why) where it cannot be run."""
    try:
        done = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, "not found"
    except subprocess.TimeoutExpired:
        return None, "no answer within %.0f s" % timeout
    except OSError as e:
        return None, e.strerror or str(e)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def first_line(text):
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), "")


def version(tool, flag="--version"):
    """The first line a tool prints for --version, or None where it does not run."""
    code, out = run([tool, flag])
    return first_line(out) if code is not None else None


def system(os_release=None):
    """{"platform", "name", "manager"}: the operating system and its package manager (None where not
    known), from the platform and /etc/os-release (ID and ID_LIKE; os_release: another file)."""
    if sys.platform == "darwin":
        return {"platform": "macOS", "name": "macOS " + platform.mac_ver()[0], "manager": "brew"}
    info = {}
    try:
        with open(os_release or OS_RELEASE) as f:
            for line in f:
                key, _, value = line.strip().partition("=")
                info[key] = value.strip().strip('"')
    except OSError:
        pass
    ids = " ".join([info.get("ID", "")] + info.get("ID_LIKE", "").split()).lower().split()
    manager = None
    for words, name in ((("debian", "ubuntu"), "apt"), (("arch",), "pacman"),
                        (("fedora", "rhel", "centos"), "dnf")):
        if any(w in ids for w in words):
            manager = name
            break
    return {"platform": platform.system() or sys.platform, "name": info.get("PRETTY_NAME") or info.get("NAME") or
            platform.system(), "manager": manager}


def finding(fid, group, what, need, state, found="", detail="", fix=None):
    return {"id": fid, "group": group, "what": what, "need": need, "state": state, "found": found, "detail": detail,
            "fix": FIXES.get(fix) if isinstance(fix, str) else fix}


def python_findings():
    out = []
    v = sys.version_info
    out.append(finding("python", "essentials", "Python", ESSENTIAL, OK if v >= (3, 11) else WRONG,
                       "%d.%d.%d at %s" % (v[0], v[1], v[2], sys.executable),
                       "3.11 or newer (tomllib)", None if v >= (3, 11) else "python"))
    for dist, module, need, why, requirement, extra in PACKAGES:
        try:
            have = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            have = None
        importable = have is not None and importlib.util.find_spec(module) is not None
        fix = {"pip": "%s -m pip install '%s'" % (sys.executable, requirement),
               "text": "the extra %r of DREAD: pip install -e '.[%s]' in the checkout" % (extra, extra) if extra else
               "pip install -e . in DREAD's checkout"}
        state, detail = OK, why
        if not importable:
            state = MISSING
        elif dist == "unicorn" and have != "2.1.4":
            state, detail = WRONG, "2.1.4 is pinned, %s is installed: %s" % (have, why)
        elif dist == "capstone" and not have.startswith("5."):
            state, detail = WRONG, "capstone 5 is needed, %s is installed" % have
        group = "essentials" if need == ESSENTIAL else "python"
        out.append(finding(dist, group, dist, need, state, have or "", detail, None if state == OK else fix))
        if dist == "playwright" and state == OK:
            browser = next((shutil.which(b) for b in BROWSERS if shutil.which(b)), None)
            out.append(finding("chrome", "python", "a browser for Playwright", OPTIONAL, OK if browser else MISSING,
                               browser or "", "tests/test_gui_browser.py runs headless Chrome or Chromium",
                               None if browser else "chrome"))
    return out


def binutils(prefix, tools=("as", "ld", "objcopy", "nm")):
    """The tools of a GNU toolchain that are not on PATH."""
    return [prefix + t for t in tools if not shutil.which(prefix + t)]


def gcc_finding(fid, what, prefixes, fix, purpose, probe=None):
    """A GNU cross toolchain: the first of prefixes found, its version and binutils (and probe(gcc):
    (state, words) for what it takes)."""
    for prefix in prefixes:
        gcc = prefix + "gcc"
        if not shutil.which(gcc):
            continue
        found = version(gcc) or gcc
        lacking = binutils(prefix)
        state, detail = OK, purpose
        if lacking:
            state, detail = MISSING, "%s; without %s" % (purpose, ", ".join(lacking))
        if probe is not None and state == OK:
            state, words = probe(gcc)
            detail = "%s; %s" % (purpose, words)
        return finding(fid, "compilers", what, OPTIONAL, state, found, detail, None if state == OK else fix)
    return finding(fid, "compilers", what, OPTIONAL, MISSING, "", "%s; none of %s" % (
        purpose, ", ".join(p + "gcc" for p in prefixes)), fix)


def riscv_march(gcc):
    """(state, words): the -march for RV32IMAC a RISC-V GCC takes."""
    for march in RISCV_MARCH:
        code, _ = run([gcc, "-march=" + march, "-mabi=ilp32", "-E", "-x", "c", "-"])
        if code == 0:
            return OK, "takes -march=%s%s" % (march, "" if "_zicsr" in march else " (no _zicsr: a GCC before 12)")
    return WRONG, "takes neither -march=%s" % " nor -march=".join(RISCV_MARCH)


def clang_targets(clang):
    """The targets a clang registers (--print-targets), as names."""
    code, out = run([clang, "--print-targets"])
    if code != 0:
        return []
    return sorted({m.group(1) for m in re.finditer(r"^\s+([\w.-]+)\s+-\s", out, re.M)})


def clang_finding():
    clang = shutil.which("clang")
    if not clang:
        return finding("clang", "compilers", "clang", OPTIONAL, MISSING, "", "for targets of [compiler] kind = \"clang\" "
                       "(gizmo's RV32 and MIPS builds); none on PATH", "clang")
    targets = clang_targets(clang)
    ours = [t for t in CLANG_TARGETS if t in targets]
    lacking = [t for t in ("ld.lld", "llvm-objcopy", "llvm-nm") if not shutil.which(t)]
    detail = "targets of DREAD's architectures: %s" % (", ".join(ours) or "none")
    state = OK
    if lacking:
        state, detail = MISSING, "%s; without %s" % (detail, ", ".join(lacking))
    elif not ours:
        state = WRONG
    return finding("clang", "compilers", "clang", OPTIONAL, state, version(clang) or clang, detail,
                   None if state == OK else "clang")


def compiler_findings():
    return [
        gcc_finding("arm-gcc", "GCC for ARM", ("arm-none-eabi-",), "arm-gcc", "for ARM targets (ARM state, Thumb, "
                    "Cortex-M)"),
        gcc_finding("riscv-gcc", "GCC for RISC-V", RISCV_GCC, "riscv-gcc", "for RISC-V targets", riscv_march),
        clang_finding(),
        gcc_finding("msp430-gcc", "GCC for the MSP430", ("msp430-elf-",), "msp430-gcc", "for MSP430 targets"),
    ]


def tool_findings(target=None):
    out = []
    podman = shutil.which("podman")
    out.append(finding("podman", "tools", "podman", OPTIONAL, OK if podman else MISSING,
                       (version("podman") or podman) if podman else "",
                       "dread bench's toolchain images and bin/with-toolchain (older compilers, as CI has them)",
                       None if podman else "podman"))
    git = shutil.which("git")
    out.append(finding("git", "tools", "git", OPTIONAL, OK if git else MISSING, (version("git") or git) if git else "",
                       "dread bench fetch (a benchmark's pinned source)", None if git else "git"))
    from dread.ghidra import find_ghidra
    where = find_ghidra(target)
    java = shutil.which("java")
    if where and java:
        out.append(finding("ghidra", "tools", "Ghidra", OPTIONAL, OK, where, "dread ghidra: the functions of an image "
                           "without symbols; java: %s" % (first_line(run([java, "-version"])[1]) or java)))
    elif where:
        out.append(finding("ghidra", "tools", "Ghidra", OPTIONAL, MISSING, where, "found, but no java on PATH to run it",
                           "java"))
    else:
        out.append(finding("ghidra", "tools", "Ghidra", OPTIONAL, MISSING, "", "dread ghidra: the functions of an image "
                           "without symbols; not in $GHIDRA_INSTALL_DIR or the usual places", "ghidra"))
    return out


def local(url):
    """Whether a URL names this machine."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in ("localhost", "::1", "0.0.0.0") or host.startswith("127.")


def answers(url, timeout=TIMEOUT):
    """(whether a server answers at its base URL, words): GET BASE/v1/models; any HTTP answer counts."""
    probe = url.rstrip("/") + "/v1/models"
    try:
        with urllib.request.urlopen(urllib.request.Request(probe, method="GET"), timeout=timeout) as r:
            return True, "answers (HTTP %d)" % r.status
    except urllib.error.HTTPError as e:
        return True, "answers (HTTP %d)" % e.code
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", e)
        return False, "does not answer at %s (%s)" % (url, reason if not isinstance(reason, BaseException) else
                                                      type(reason).__name__ + (": %s" % reason if str(reason) else ""))


def model_findings(path=None, timeout=TIMEOUT):
    """The providers of llm.toml, each with its key's state and its server's or command's."""
    import tomllib
    from dread import llm
    path = path or llm.config_path()
    if not os.path.exists(path):
        return [finding("llm", "models", "llm.toml", OPTIONAL, MISSING, path, "dread pilot asks a language model; no "
                        "provider is configured", {"text": "dread llm set NAME --kind anthropic --model MODEL --default "
                                                          "(then dread llm key NAME), or the Setup page of dread gui"})]
    try:
        with open(path, "rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        return [finding("llm", "models", "llm.toml", OPTIONAL, WRONG, path, "does not parse: %s" % e,
                        {"text": "fix it by hand: dread.llm's docstring shows its format"})]
    providers = config.get("providers") or {}
    out = [finding("llm", "models", "llm.toml", OPTIONAL, OK if providers else MISSING, path,
                   "%d provider%s, default %s" % (len(providers), "" if len(providers) == 1 else "s",
                                                  config.get("default") or "none (the first by name)") if providers else
                   "no provider in it", None if providers else {"text": "dread llm set NAME --kind ... --model ..."})]
    for name, spec in sorted(providers.items()):
        if not isinstance(spec, dict):
            continue
        kind, model = spec.get("kind"), spec.get("model", "?")
        what = "%s (%s, %s)" % (name, kind or "?", model)
        if kind not in llm.KINDS:
            out.append(finding("llm:" + name, "models", what, OPTIONAL, WRONG, "", "unknown kind %r (known: %s)" % (
                kind, ", ".join(llm.KINDS)), {"text": "dread llm set %s --kind openai|anthropic" % name}))
            continue
        status = llm.key_status(spec, path)
        state, words, fix = OK, [], None
        if status["set"]:
            words.append("key from " + status["how"])
        elif status["problem"]:
            state = WRONG
            words.append("key file refused: " + status["problem"])
            fix = {"text": "chmod 600 the key file, or dread llm key %s (reads it from standard input)" % name}
        elif status["needed"]:
            state = MISSING
            words.append("no key (%s)" % llm.key_places(spec, "ANTHROPIC_API_KEY" if kind == "anthropic" else None))
            fix = {"text": "dread llm key %s (reads it from standard input into a key file of mode 600), or set "
                           "the variable before dread runs" % name}
        if kind == "openai":
            url = spec.get("url", "http://127.0.0.1:8080")
            if local(url):
                up, said = answers(url, timeout)
                words.append("server " + said)
                if not up and state == OK:
                    state = MISSING
                    fix = {"text": "start the server (llama-server, Ollama, LM Studio, vLLM) or change url: "
                                   "dread llm set %s --url URL" % name}
            else:
                words.append("server %s (not on this machine: not contacted)" % url)
        if kind == "command":
            command = spec.get("command")
            program = command[0] if isinstance(command, list) and command else None
            if program and shutil.which(program):
                words.append("command %s" % shutil.which(program))
            else:
                state = MISSING if state == OK else state
                words.append("command %s not found" % (program or "(none)"))
                fix = fix or {"text": "install the command, or change llm.toml's command line by hand"}
        if name == config.get("default"):
            words.append("the default")
        out.append(finding("llm:" + name, "models", what, OPTIONAL, state, "", "; ".join(words), fix))
    return out


def target_findings(target):
    """The target's compiler, assembler, linker and objcopy, and whether the compiler takes its cflags."""
    c = target.compiler
    group = "essentials"
    what = "the compiler of %s" % target.name
    lacking = []
    for name in ("cc", "as", "ld", "objcopy"):
        tool = c.command(name)[0]
        if not shutil.which(tool):
            lacking.append(tool)
    fix = None
    cc = c.command("cc")
    family = "arm-gcc" if c.kind == "gcc" and c.cross.startswith("arm") else \
        "riscv-gcc" if c.kind == "gcc" and c.cross.startswith("riscv") else \
        "msp430-gcc" if c.kind == "gcc" and c.cross.startswith("msp430") else "clang" if c.kind == "clang" else None
    if lacking:
        return [finding("target-compiler", group, what, ESSENTIAL, MISSING, " ".join(cc),
                        "dread recon and the lift check compile with it; not on PATH: %s" % ", ".join(lacking), family)]
    code, out = run(cc + c.cflags + ["-E", "-x", "c", "-"])
    found = version(cc[0]) or cc[0]
    if code != 0:
        return [finding("target-compiler", group, what, ESSENTIAL, WRONG, found, "it refuses the target's cflags: %s"
                        % (first_line(out) or "exit %s" % code), {"text": "change [compiler] cflags in %s (a RISC-V GCC "
                        "before 12 takes -march without _zicsr)" % target.file})]
    return [finding("target-compiler", group, what, ESSENTIAL, OK, found, "%s, its cflags taken" % " ".join(cc))]


def check(target=None, timeout=TIMEOUT, llm_path=None, os_release=None):
    """{"system", "findings", "essentials" (whether every essential finding is ok), "version"}."""
    from dread import __version__
    findings = python_findings()
    if target is not None:
        findings += target_findings(target)
    findings += compiler_findings() + tool_findings(target) + model_findings(llm_path, timeout)
    return {"version": __version__, "system": system(os_release), "findings": findings,
            "essentials": all(f["state"] == OK for f in findings if f["need"] == ESSENTIAL)}


# ---- telling ----

GROUPS = (("essentials", "essentials"), ("compilers", "compilers (each for targets of its architecture)"),
          ("python", "Python's optional packages"), ("tools", "tools"), ("models", "language models"))


def fixes(fix, manager, every=False):
    """The lines of a fix for this system: its package manager's command (every manager's with every, or
    where the manager is not known), pip's, the words."""
    if not fix:
        return []
    out = []
    for m in MANAGERS:
        if m in fix and (every or manager is None or m == manager):
            out.append("%-7s %s" % (m + ":", fix[m] or "no package known"))
    if fix.get("pip"):
        out.append("pip:    " + fix["pip"])
    if fix.get("text"):
        out.append(fix["text"])
    return out


def report(result, every=False):
    """The findings as text, by group."""
    s = result["system"]
    lines = ["dread doctor: DREAD %s, Python %s; %s%s" % (result["version"], platform.python_version(), s["name"],
             " (%s)" % s["manager"] if s["manager"] else " (package manager not known: all four shown)"),
             "nothing here is run: each line under a finding is what to do about it"]
    for group, title in GROUPS:
        rows = [f for f in result["findings"] if f["group"] == group]
        if not rows:
            continue
        lines += ["", title]
        for f in rows:
            mark = {OK: "ok", MISSING: "missing", WRONG: "wrong"}[f["state"]]
            head = "  %-8s %s" % (mark, f["what"])
            if f["found"]:
                head += ": " + f["found"]
            lines.append(head)
            if f["detail"]:
                lines.append("           " + f["detail"])
            if f["state"] != OK:
                lines += ["             " + line for line in fixes(f["fix"], s["manager"], every)]
    bad = [f["what"] for f in result["findings"] if f["need"] == ESSENTIAL and f["state"] != OK]
    lines += ["", "the essentials are there" if not bad else "essentials missing or wrong: %s" % ", ".join(bad)]
    return "\n".join(lines)


def main(argv=None):
    from dread import target as tgt
    parser = argparse.ArgumentParser(prog="dread doctor", description=__doc__.split("\n\n")[0])
    tgt.add_arguments(parser, image=False)
    parser.add_argument("--json", action="store_true", help="the findings as JSON")
    parser.add_argument("--all", action="store_true", help="every package manager's command, not this system's only")
    parser.add_argument("--timeout", type=float, default=TIMEOUT, metavar="SECONDS",
                        help="seconds a local model server has to answer (default %(default)s)")
    args = parser.parse_args(argv)
    target, problem = None, None
    try:
        target = tgt.Target.load(args.target)
    except tgt.TargetError as e:
        if args.target or os.environ.get("DREAD_TARGET"):
            problem = str(e)
    result = check(target, max(0.1, args.timeout))
    if problem:
        result["findings"].insert(0, finding("target", "essentials", "the target", ESSENTIAL, WRONG, args.target or
                                             os.environ.get("DREAD_TARGET", ""), problem,
                                             {"text": "docs/target.md describes dread.toml"}))
        result["essentials"] = False
    print(json.dumps(result, indent=1, sort_keys=True) if args.json else report(result, args.all))
    return 0 if result["essentials"] else 1


if __name__ == "__main__":
    sys.exit(main())
