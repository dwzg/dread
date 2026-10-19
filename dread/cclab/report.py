"""dread cclab's report: the builds of an adapter compared, as Markdown (WORK/ADAPTER/report.md).

For each probe function, the distinct results and the builds that gave them ("all" when every
build agrees); for each runtime helper the probes called directly, its names (aliases at one
address), the operations that called it, and the registers it changed besides its results, over
every call in every build. A build that failed is listed with its error. It says what was measured;
it explains nothing (that is a human's or an LLM's reading of it).
"""
import collections
import glob
import json
import os

SECTIONS = (("arg", "Where arguments arrive", "where"), ("result", "Where results come back", "where"),
            ("value", "Types and values", "hex"), ("op", "Operations: what each calls", None),
            ("keeps", "What a function keeps across a call", None))


def load(work, adapter):
    """{build key: results} of an adapter's builds in work (failed builds: {"error"})."""
    out = {}
    for d in sorted(glob.glob(os.path.join(work, adapter, "*"))):
        if not os.path.isdir(d) or os.path.basename(d).startswith("doctor-"):
            continue
        path = os.path.join(d, "results.json")
        if os.path.exists(path):
            with open(path) as f:
                out[os.path.basename(d)] = json.load(f)
        elif os.path.exists(os.path.join(d, "build.json")):
            out[os.path.basename(d)] = {"error": "built but not measured"}
        else:
            out[os.path.basename(d)] = {"error": "not built (see the run's output)"}
    return out


NOTABLE = {      # instructions that mark an idiom in inline code, per architecture
    "arm": ("udiv", "sdiv", "umull", "smull", "smmul", "tbb", "tbh", "ldrd", "strd", "umlal", "mla", "mls"),
    "riscv": ("mul", "mulh", "mulhu", "mulhsu", "div", "divu", "rem", "remu"),
}
NOTABLE_PREFIXES = {"arm": ("ldm", "stm")}


def op_text(entry, arch="arm"):
    """An op's result in one line: its direct calls (or inline) and, inline, its code's notable
    instructions (division, table branches)."""
    if "error" in entry:
        return "error: " + entry["error"][:60]
    direct = [h["function"] + (" (tail call)" if h.get("tail") else "") for h in entry.get("helpers", [])
              if h.get("depth", 1) == 1]
    if direct:
        return ", ".join(dict.fromkeys(direct))
    notable = [m for m in entry.get("shape", {}).get("mnemonics", [])
               if m in NOTABLE.get(arch, ()) or m.startswith(NOTABLE_PREFIXES.get(arch, ()))]
    return "inline" + (" (%s)" % ", ".join(notable) if notable else "")


def keeps_text(entry):
    """A keeps probe in one line: what came back changed, what its frame saved, its frame at calls."""
    if "error" in entry:
        return "error: " + entry["error"][:60]
    text = "kept %s; saves %s" % (entry.get("kept", "?"), " ".join(entry.get("saved", [])) or "nothing")
    frames = entry.get("frames_at_calls", [])
    if frames:
        text += "; frame at calls %s" % "/".join(str(f) for f in frames)
    return text


LAYOUT = (("entries", "where the probes' functions start"),
          ("between", "what lies between a function's last instruction and the next function"),
          ("first", "the instructions they start with (most often first)"),
          ("last", "the instructions they end with (most often first)"))


def layout_cells(layout):
    """A build's layout as {row: text}, texts that builds with the same layout share."""
    if not layout:
        return {}
    if "error" in layout:
        return {"entries": "error: " + layout["error"][:60]}
    n = layout.get("functions", 0)
    kinds = ", ".join(k for k, _ in sorted(layout.get("padding", {}).items(), key=lambda kv: -kv[1]))
    pools = layout.get("pools", 0)
    return {"entries": "multiples of %d" % layout.get("aligned", 0),
            "between": "%s; padding: %s" % ("literal pools behind %s" % ("some" if pools < n else "all") if pools
                                            else "no literal pools", kinds or "none"),
            "first": ", ".join(m for m, _ in layout.get("first", [])[:4]),
            "last": ", ".join(m for m, _ in layout.get("last", [])[:4])}


def builds_text(keys, every):
    return "all %d" % len(every) if len(keys) == len(every) else ", ".join(keys)


def write(work, adapter_name=None, out=print):
    adapters = [adapter_name] if adapter_name else sorted(
        d for d in os.listdir(work) if os.path.isdir(os.path.join(work, d)))
    for name in adapters:
        builds = load(work, name)
        if not builds:
            out("dread cclab report: nothing measured for %s in %s" % (name, work))
            continue
        good = {k: v for k, v in builds.items() if "results" in v}
        arch = next((v.get("arch") for v in good.values() if v.get("arch")), "arm")
        lines = ["# dread cclab: %s" % name, ""]
        version = os.path.join(work, name, "version.txt")
        if os.path.exists(version):
            with open(version) as f:
                lines += ["Compiler: `%s`" % f.read().strip().splitlines()[0], ""]
        lines += ["%d builds measured: %s." % (len(good), ", ".join(good))]
        bad = {k: v for k, v in builds.items() if "results" not in v}
        if bad:
            lines += ["", "Failed: " + "; ".join("%s (%s)" % (k, v["error"]) for k, v in bad.items())]
        functions = []
        for result in good.values():
            for fn in result["results"]:
                if fn not in functions:
                    functions.append(fn)
        for kind, title, field in SECTIONS:
            rows = []
            for fn in functions:
                seen = collections.OrderedDict()
                note = ""
                for key, result in good.items():
                    entry = result["results"].get(fn)
                    if entry is None or entry.get("measure") != kind:
                        continue
                    note = entry.get("note", "")
                    if kind == "op":
                        text = op_text(entry, arch)
                    elif kind == "keeps":
                        text = keeps_text(entry)
                    else:
                        text = entry.get(field, entry.get("error", "?"))
                    seen.setdefault(text, []).append(key)
                if seen:
                    cells = "; ".join("%s (%s)" % (text, builds_text(keys, good)) for text, keys in seen.items())
                    rows.append("| `%s` | %s | %s |" % (fn, note, cells))
            if rows:
                lines += ["", "## " + title, "", "| probe | what | result (builds) |", "|---|---|---|"] + rows
            aligned = [e["aligned"] for r in good.values() for e in r["results"].values()
                       if e.get("measure") == kind == "keeps" and e.get("aligned")]
            if aligned:
                lines += ["", "Every frame at a call is a multiple of %d bytes, over all builds: the stack alignment "
                              "the compiler keeps at calls (given an aligned entry)." % min(aligned)]
        rows = collections.OrderedDict()
        for key, result in good.items():
            for row, text in layout_cells(result.get("layout")).items():
                rows.setdefault(row, collections.OrderedDict()).setdefault(text, []).append(key)
        if rows:
            notes = dict(LAYOUT)
            lines += ["", "## Where the functions lie", "", "| what | result (builds) |", "|---|---|"]
            for row, seen in rows.items():
                lines.append("| %s | %s |" % (notes.get(row, row), "; ".join(
                    "%s (%s)" % (text, builds_text(keys, good)) for text, keys in seen.items())))
        helpers = collections.OrderedDict()
        for key, result in good.items():
            for fn, entry in result["results"].items():
                for h in entry.get("helpers", []):
                    if h.get("depth", 1) != 1:
                        continue
                    info = helpers.setdefault(h["function"], {"ops": set(), "changed": set(), "builds": set()})
                    info["ops"].add(fn)
                    info["changed"] |= set(h.get("changed", []))
                    info["builds"].add(key)
        if helpers:
            lines += ["", "## Runtime helpers the probes call", "",
                      "Registers changed: over every call in every build (results included).", "",
                      "| helper | called by | registers changed | builds |", "|---|---|---|---|"]
            from dread.cclab.profile import ORDER
            for h, info in helpers.items():
                changed = [r for r in ORDER if r in info["changed"]] + sorted(info["changed"] - set(ORDER))
                lines.append("| `%s` | %s | %s | %s |" % (h, ", ".join(sorted(info["ops"])), " ".join(changed) or "-",
                                                         builds_text(sorted(info["builds"]), good)))
        path = os.path.join(work, name, "report.md")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        from dread.cclab import profile
        made = profile.write(work, name)
        out("dread cclab report: %s%s" % (path, ", " + made if made else ""))
    return 0
