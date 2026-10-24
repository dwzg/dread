"""dread doctor on a made-up machine: a PATH of stand-in compilers (an ARM GCC, a RISC-V GCC that knows no
_zicsr, a clang with some targets, no MSP430 GCC, no podman, no git, no Ghidra), providers of every kind
(a key in the environment, none, a key file others may read, a local server that answers and one that
does not, a server elsewhere, a command that is missing), the package manager from os-release, what it
prints for each, and the exit status: the essentials (Python, unicorn, capstone, a target's compiler)."""
import http.server
import json
import os
import socket
import threading

import pytest

from conftest import GIZMO
from dread import doctor

SCRIPTS = {
    "arm-none-eabi-gcc": 'case "$1" in --version) echo "arm-none-eabi-gcc (stand-in) 13.2.0";; esac\n'
                         'for a in "$@"; do [ "$a" = "-mcpu=bad" ] && { echo "unknown cpu bad" >&2; exit 1; }; done\nexit 0',
    "riscv64-unknown-elf-gcc": 'case "$1" in --version) echo "riscv64-unknown-elf-gcc (stand-in) 10.2.0"; exit 0;; '
                               '-march=rv32imac_zicsr) echo "unknown extension zicsr" >&2; exit 1;; esac\nexit 0',
    "clang": 'case "$1" in --version) echo "clang version 99.0.0 (stand-in)";; --print-targets) printf "  Registered '
             'Targets:\\n    arm        - ARM\\n    riscv32    - 32-bit RISC-V\\n    thumb      - Thumb\\n    x86-64     - '
             '64-bit X86\\n";; esac\nexit 0',
}
TOOLS = ["arm-none-eabi-" + t for t in ("as", "ld", "objcopy", "nm")] + \
    ["riscv64-unknown-elf-" + t for t in ("as", "ld", "objcopy", "nm")] + ["ld.lld", "llvm-objcopy", "llvm-nm"]


class Answers(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b'{"data": []}'
        self.send_response(200 if self.path == "/v1/models" else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in SCRIPTS.items():
        (bin_ / name).write_text("#!/bin/sh\n" + body + "\n")
    for name in TOOLS:
        (bin_ / name).write_text("#!/bin/sh\nexit 0\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_))
    monkeypatch.delenv("GHIDRA_INSTALL_DIR", raising=False)
    import dread.ghidra
    monkeypatch.setattr(dread.ghidra, "PLACES", (str(tmp_path / "no-ghidra"),))
    server = http.server.HTTPServer(("127.0.0.1", 0), Answers)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    gone = closed.getsockname()[1]
    closed.close()
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    (keys / "open").write_text("sk-readable-by-all-0000\n")
    (keys / "open").chmod(0o644)
    config = tmp_path / "llm.toml"
    config.write_text("""default = "cloud"
[providers.cloud]
kind = "anthropic"
model = "c"
key_env = "TEST_DOCTOR_KEY"
[providers.nokey]
kind = "anthropic"
model = "c"
key_env = "TEST_DOCTOR_NONE"
[providers.openkey]
kind = "anthropic"
model = "c"
key_file = "keys/open"
[providers.here]
kind = "openai"
model = "q"
url = "http://127.0.0.1:%d"
[providers.down]
kind = "openai"
model = "q"
url = "http://127.0.0.1:%d"
[providers.far]
kind = "openai"
model = "q"
url = "https://models.example.invalid/inference"
key_env = "TEST_DOCTOR_KEY"
[providers.cmd]
kind = "command"
model = "x"
command = ["no-such-program", "{prompt}"]
""" % (server.server_address[1], gone))
    monkeypatch.setenv("DREAD_LLM", str(config))
    monkeypatch.setenv("TEST_DOCTOR_KEY", "sk-from-the-environment-1234")
    monkeypatch.delenv("TEST_DOCTOR_NONE", raising=False)
    release = tmp_path / "os-release"
    release.write_text('NAME="Somedistro"\nID=somedistro\nID_LIKE="arch"\nPRETTY_NAME="Somedistro 1"\n')
    yield tmp_path, release
    server.shutdown()
    server.server_close()
    thread.join(10)


def by_id(result):
    return {f["id"]: f for f in result["findings"]}


def test_what_it_finds_on_a_made_up_machine(machine):
    tmp_path, release = machine
    result = doctor.check(timeout=2, os_release=str(release))
    f = by_id(result)
    assert result["system"]["manager"] == "pacman" and result["essentials"] is True
    assert all(f[k]["state"] == "ok" for k in ("python", "unicorn", "capstone"))
    assert f["arm-gcc"]["state"] == "ok" and f["arm-gcc"]["found"] == "arm-none-eabi-gcc (stand-in) 13.2.0"
    assert f["riscv-gcc"]["state"] == "ok" and "takes -march=rv32imac (no _zicsr" in f["riscv-gcc"]["detail"]
    assert f["clang"]["state"] == "ok" and "arm, thumb, riscv32" in f["clang"]["detail"] and "x86" not in f["clang"]["detail"]
    assert f["msp430-gcc"]["state"] == "missing" and "ti.com" in f["msp430-gcc"]["fix"]["text"]
    assert f["podman"]["state"] == "missing" and f["podman"]["fix"]["pacman"] == "sudo pacman -S podman"
    assert f["git"]["state"] == "missing" and f["ghidra"]["state"] == "missing"
    assert f["llm"]["state"] == "ok" and "7 providers, default cloud" in f["llm"]["detail"]
    assert f["llm:cloud"]["state"] == "ok" and "env TEST_DOCTOR_KEY" in f["llm:cloud"]["detail"]
    assert f["llm:nokey"]["state"] == "missing" and "$TEST_DOCTOR_NONE" in f["llm:nokey"]["detail"]
    assert f["llm:openkey"]["state"] == "wrong" and "chmod 600" in f["llm:openkey"]["detail"]
    assert f["llm:here"]["state"] == "ok" and "answers (HTTP 200)" in f["llm:here"]["detail"]
    assert f["llm:down"]["state"] == "missing" and "does not answer" in f["llm:down"]["detail"]
    assert f["llm:far"]["state"] == "ok" and "not contacted" in f["llm:far"]["detail"]
    assert f["llm:cmd"]["state"] == "missing" and "no-such-program not found" in f["llm:cmd"]["detail"]
    text = json.dumps(result)
    assert "sk-from-the-environment" not in text and "sk-readable-by-all" not in text


def test_what_it_prints_and_its_exit_status(machine, monkeypatch, capsys):
    tmp_path, release = machine
    monkeypatch.setattr(doctor, "OS_RELEASE", str(release))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DREAD_TARGET", raising=False)
    assert doctor.main(["--timeout", "2"]) == 0
    out = capsys.readouterr().out
    assert "Somedistro 1 (pacman)" in out and "the essentials are there" in out
    assert "pacman: sudo pacman -S podman" in out and "apt:" not in out
    assert "sk-from-the-environment" not in out
    assert doctor.main(["--all", "--timeout", "2"]) == 0
    out = capsys.readouterr().out
    assert "apt:    sudo apt install podman" in out and "brew:   brew install podman" in out
    assert doctor.main(["--json", "--timeout", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["essentials"] is True


def target_with(tmp_path, cflags_line):
    """A copy of gizmo-m3's description with another [compiler]."""
    where = tmp_path / "t"
    where.mkdir()
    text = open(os.path.join(GIZMO, "m3", "dread.toml")).read()
    start = text.index("[compiler]")
    end = text.index("\n\n", start)
    (where / "dread.toml").write_text(text[:start] + cflags_line + text[end:])
    return str(where)


def test_a_targets_compiler_is_essential(machine, monkeypatch, capsys):
    tmp_path, release = machine
    monkeypatch.setattr(doctor, "OS_RELEASE", str(release))
    missing = target_with(tmp_path, '[compiler]\ncross = "nosuch-"\ncflags = ["-Os"]')
    assert doctor.main(["--target", missing, "--timeout", "1"]) == 1
    out = capsys.readouterr().out
    assert "missing  the compiler of gizmo-m3" in out and "nosuch-gcc" in out and "essentials missing or wrong" in out
    shutil_rm(tmp_path / "t")
    refused = target_with(tmp_path, '[compiler]\ncross = "arm-none-eabi-"\ncflags = ["-mcpu=bad"]')
    assert doctor.main(["--target", refused, "--timeout", "1"]) == 1
    assert "it refuses the target's cflags: unknown cpu bad" in capsys.readouterr().out
    shutil_rm(tmp_path / "t")
    good = target_with(tmp_path, '[compiler]\ncross = "arm-none-eabi-"\ncflags = ["-mcpu=cortex-m3"]')
    assert doctor.main(["--target", good, "--timeout", "1"]) == 0
    assert doctor.main(["--target", str(tmp_path / "nowhere"), "--timeout", "1"]) == 1
    assert "the target" in capsys.readouterr().out


def shutil_rm(path):
    import shutil
    shutil.rmtree(path)


@pytest.mark.parametrize("release,manager", [('ID=ubuntu\nID_LIKE=debian\n', "apt"), ("ID=debian\n", "apt"),
                                             ('ID=fedora\n', "dnf"), ('ID="rocky"\nID_LIKE="rhel centos fedora"\n', "dnf"),
                                             ("ID=arch\n", "pacman"), ("ID=plan9\n", None)])
def test_the_package_manager(tmp_path, release, manager):
    path = tmp_path / "os-release"
    path.write_text(release)
    assert doctor.system(str(path))["manager"] == manager
    assert doctor.system(str(tmp_path / "none"))["manager"] is None


def test_no_providers_yet(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAD_LLM", str(tmp_path / "none.toml"))
    found = doctor.model_findings()
    assert len(found) == 1 and found[0]["state"] == "missing" and "dread llm set" in found[0]["fix"]["text"]
    (tmp_path / "bad.toml").write_text("this is [not toml")
    found = doctor.model_findings(str(tmp_path / "bad.toml"))
    assert found[0]["state"] == "wrong" and "does not parse" in found[0]["detail"]
