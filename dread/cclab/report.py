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


def op_text(entry):
    """An op's result in one line: its direct calls (or inline) and, inline, its code's notable
    instructions (division, table branches)."""
    if "error" in entry:
        return "error: " + entry["error"][:60]
    direct = [h["function"] + (" (tail call)" if h.get("tail") else "") for h in entry.get("helpers", [])
              if h.get("depth", 1) == 1]
    if direct:
        return ", ".join(dict.fromkeys(direct))
    notable = [m for m in entry.get("shape", {}).get("mnemonics", [])
               if m in ("udiv", "sdiv", "umull", "smull", "smmul", "tbb", "tbh", "ldm", "stm", "ldrd", "strd",
                        "umlal", "mla", "mls") or m.startswith(("ldm", "stm"))]
    return "inline" + (" (%s)" % ", ".join(notable) if notable else "")


def keeps_text(entry):
    """A keeps probe in one line: what came back changed, the alignment at calls, the saves."""
    if "error" in entry:
        return "error: " + entry["error"][:60]
    sp = entry.get("sp_mod8_at_calls", [])
    return "kept %s%s; saves: %s" % (entry.get("kept", "?"),
                                    "; sp at calls: %s" % ("8-aligned" if sp == [0] else "mod 8 = %s" % sp) if sp else "",
                                    " / ".join(entry.get("saves", [])) or "none")


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
                        text = op_text(entry)
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
            order = ["r%d" % i for i in range(13)] + ["sp"]
            for h, info in helpers.items():
                changed = [r for r in order if r in info["changed"]]
                lines.append("| `%s` | %s | %s | %s |" % (h, ", ".join(sorted(info["ops"])), " ".join(changed) or "-",
                                                         builds_text(sorted(info["builds"]), good)))
        path = os.path.join(work, name, "report.md")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        out("dread cclab report: %s" % path)
    return 0
