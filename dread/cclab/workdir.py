"""dread cclab's work directory: what an LLM there may read, and the bundle that leaves it.

A build's directory holds our probes (probes/), their objects (obj/: our code as the compiler made it),
the linked image (image.elf, original/: the probes and the toolchain's runtime library, which for a
proprietary compiler is its vendor's code), the tables made from it (config/) and the results. The
user's rule (2026-10-19): an LLM sees our probes, their compiled code and the compilers' messages,
never the company's code and never a compiler's runtime code. So `run` writes, once, a CLAUDE.md and
Claude Code deny rules into the work directory (an adapter of a proprietary toolchain asks for that
with guard = true, `--guard` asks for it with any), each build's dread.toml then names the functions
an LLM may be shown ([llm] only, which dread pilot keeps to), and `bundle` packs only what may leave:
results, logs, reports, the compiler's version text, probe sources and objects; no image, binary or
table.
"""
import json
import os
import zipfile

CLAUDE_MD = """# A dread cclab work directory

The probes in */*/probes are ours (DREAD's, public); */*/obj holds them as the compiler compiled
them; results.json, build.json, report.md and version.txt say what was measured. Those may be read,
and an LLM may be shown them. `dread pilot` on a build asks only about the functions its dread.toml
lists under [llm] only (the probes).

Never read, show or send: */*/image.elf, */*/original/, */*/config/ (the linked image and its tables
hold the toolchain's runtime library, a vendor's code), and nothing outside this directory (the
user's company code). Fix an adapter in a copy of its TOML and run `dread cclab doctor --adapter
COPY`; every proposal is tried before it counts.
"""

SETTINGS = {"permissions": {"deny": ["Read(./**/image.elf)", "Read(./**/original/**)", "Read(./**/config/**)",
                                     "Read(./**/*.bin)"]}}

BUNDLED = ("results.json", "build.json", "report.md", "version.txt")      # and compare-*.md


def guard(work):
    """Write the work directory's CLAUDE.md and .claude/settings.json unless they are there."""
    os.makedirs(os.path.join(work, ".claude"), exist_ok=True)
    md = os.path.join(work, "CLAUDE.md")
    if not os.path.exists(md):
        with open(md, "w") as f:
            f.write(CLAUDE_MD)
    settings = os.path.join(work, ".claude", "settings.json")
    if not os.path.exists(settings):
        with open(settings, "w") as f:
            json.dump(SETTINGS, f, indent=1)


def bundle(work, out):
    """A zip of what may leave the work directory (see the module); returns the files packed."""
    packed = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(work):
            rel_root = os.path.relpath(root, work)
            parts = rel_root.split(os.sep)
            if any(p in ("original", "config", ".claude") for p in parts):
                continue
            for name in files:
                keep = name in BUNDLED or (len(parts) >= 3 and parts[-1] in ("probes", "obj")) or \
                    (rel_root == "." and name.startswith("compare-") and name.endswith(".md"))
                if keep and not name.endswith((".elf", ".bin", ".out")):
                    path = os.path.join(root, name)
                    z.write(path, os.path.relpath(path, work))
                    packed.append(os.path.relpath(path, work))
    return packed
