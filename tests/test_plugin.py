"""The Claude Code plugin in plugins/dread: its manifests parse, every skill and agent has the front
matter Claude Code reads, and every `dread COMMAND` the skills, the agents, the README and the method
and porting guides name exists (they only point at the tools; a renamed command must not leave them
behind)."""
import json
import os
import re

from dread.cli import COMMANDS
from conftest import ROOT

PLUGIN = os.path.join(ROOT, "plugins", "dread")


def documents():
    for sub in ("skills", "agents"):
        for dirpath, _, files in os.walk(os.path.join(PLUGIN, sub)):
            for f in files:
                if f.endswith(".md") and f != "TARGET.md":
                    yield os.path.join(dirpath, f)


def test_manifests_parse():
    market = json.load(open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")))
    plugin = json.load(open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json")))
    assert [p["name"] for p in market["plugins"]] == [plugin["name"]] == ["dread"]
    assert os.path.normpath(os.path.join(ROOT, market["plugins"][0]["source"])) == PLUGIN


def test_front_matter():
    found = list(documents())
    assert len(found) >= 6
    for path in found:
        text = open(path).read()
        head = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        assert head, path
        fields = dict(line.split(":", 1) for line in head.group(1).splitlines() if ":" in line)
        assert fields.get("name", "").strip() and len(fields.get("description", "").strip()) > 40, path
        if os.sep + "skills" + os.sep in path:
            assert fields["name"].strip() == os.path.basename(os.path.dirname(path)), path


def test_named_commands_exist():
    docs = [os.path.join(ROOT, "README.md")] + [os.path.join(ROOT, "docs", f) for f in
                                                ("method.md", "porting.md", "usage.md", "gui.md", "features.md")]
    todo = open(os.path.join(ROOT, "docs", "todo.md")).read()
    planned = {c for item in re.findall(r"^- \[ \].*?(?=^- \[|^#|\Z)", todo, re.S | re.M)
               for c in re.findall(r"`dread ([a-z-]+)", item)}       # open items may name what is to come
    for path in list(documents()) + docs:
        for command in re.findall(r"`dread ([a-z-]+)", open(path).read()):
            allowed = COMMANDS if path in documents_list() else set(COMMANDS) | planned
            assert command in allowed, "%s names `dread %s`" % (path, command)


def documents_list():
    return list(documents())
