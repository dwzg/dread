"""dread cclab's report: the builds of an adapter compared, as Markdown (WORK/ADAPTER/report.md).

For each probe function, the distinct results and the builds that gave them ("all" when every
build agrees); for each runtime helper the probes called directly, its names (aliases at one
address), the operations that called it, and the registers it changed besides its results, over
every call in every build. A build that failed is listed with its error. It says what was measured;
it explains nothing (that is a human's or an LLM's reading of it).

The report is data first (data(): what report.md shows, the probes whose measurement failed, and the
profile of profile.py), and report.md is made from it (markdown()); `dread cclab report --json FILE`
writes the data of every adapter it reports on (docs/formats.md), which dread gui's cclab page reads.
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
    "arm": ("udiv", "sdiv", "umull", "smull", "smmul", "ldrd", "strd", "umlal", "mla", "mls"),   # (tbb, tbh:
                                                                                             # the shape's ijumps)
    "riscv": ("mul", "mulh", "mulhu", "mulhsu", "div", "divu", "rem", "remu"),
}
NOTABLE_PREFIXES = {"arm": ("ldm", "stm")}


def op_text(entry, arch="arm"):
    """An op's result in one line: its direct calls (or inline) and, inline, its code's notable
    instructions (division, table branches)."""
    if "error" in entry:
        return "error: " + entry["error"][:60]
    unrun = ""
    if "(through a register)" in entry.get("shape", {}).get("calls", []) and entry.get("icalls", 1) == 0:
        unrun = "; a call through a register in its code, not taken"   # (GCC -O2: a virtual call
    direct = [h["function"] + (" (tail call)" if h.get("tail") else "") for h in entry.get("helpers", [])  # devirtualised
              if h.get("depth", 1) == 1]                                                                  # on a guess)
    if direct:
        return ", ".join(dict.fromkeys(direct)) + unrun
    notable = [m for m in entry.get("shape", {}).get("mnemonics", [])
               if m in NOTABLE.get(arch, ()) or m.startswith(NOTABLE_PREFIXES.get(arch, ()))]
    jumps = sorted(set(entry.get("shape", {}).get("ijumps", []))) if entry.get("table_jumps", 1) else []
    return "inline" + (" (%s)" % ", ".join(notable) if notable else "") + (
        ", a jump through a table (%s)" % ", ".join(jumps) if jumps else "") + unrun


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


def lift_verdicts(result):
    """{function: text} of a build's lift check (run --lift): "accepted", or the verdict with the kind
    of its reason (addresses and registers taken out, so that builds compare). {} without one."""
    from dread.bench import reason_kind
    got = (result.get("lift") or {}).get("verdicts") or {}
    return {fn: v[0] if v[0] == "accepted" else "%s: %s" % (v[0], reason_kind(v[1])) for fn, v in got.items()}


def lift_data(good):
    """A report's run --lift as data: {"builds": {build: {"accepted", "checked"}}, "errors": {build: why
    its check did not run}, "rows": [{"probe", "results": [{"text", "builds"}]}] (the probe functions not
    lifted somewhere, each verdict with the builds that gave it)}; None when no build was lifted."""
    lifted = {k: lift_verdicts(v) for k, v in good.items() if lift_verdicts(v)}
    errors = {k: v["lift"]["error"] for k, v in good.items() if "error" in (v.get("lift") or {})}
    if not lifted and not errors:
        return None
    rows = collections.OrderedDict()
    for key, verdicts in lifted.items():
        for fn, text in verdicts.items():
            if text != "accepted":
                rows.setdefault(fn, collections.OrderedDict()).setdefault(text, []).append(key)
    return {"builds": {k: {"accepted": sum(1 for t in v.values() if t == "accepted"), "checked": len(v)}
                       for k, v in lifted.items()},
            "errors": errors,
            "rows": [{"probe": fn, "results": [{"text": t, "builds": keys} for t, keys in seen.items()]}
                     for fn, seen in rows.items()]}


def result_text(kind, field, entry, arch):
    """A probe's result in a section's cell: op_text, keeps_text, or the entry's field (its error)."""
    if kind == "op":
        return op_text(entry, arch)
    if kind == "keeps":
        return keeps_text(entry)
    return entry.get(field, entry.get("error", "?"))


def data(work, name):
    """One adapter's report as data (report --json, docs/formats.md), what report.md shows: the compiler,
    the builds measured and failed, each section's probes with their distinct results and the builds that
    gave them, the stack alignment, where the functions lie, the runtime helpers, what DREAD lifts, the
    probes whose measurement failed (an entry with an error, by build) and the profile (profile.py). None
    where nothing was measured."""
    from dread.cclab.profile import ORDER
    builds = load(work, name)
    if not builds:
        return None
    good = {k: v for k, v in builds.items() if "results" in v}
    arch = next((v.get("arch") for v in good.values() if v.get("arch")), "arm")
    version = None
    vpath = os.path.join(work, name, "version.txt")
    if os.path.exists(vpath):
        with open(vpath) as f:
            lines = f.read().strip().splitlines()
        version = lines[0] if lines else ""
    functions, probe_files = [], {}
    for result in good.values():
        for fn in result["results"]:
            if fn not in functions:
                functions.append(fn)
        probe_files.update(result.get("probes") or {})
    sections, failed_probes = [], collections.OrderedDict()
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
                seen.setdefault(result_text(kind, field, entry, arch), []).append(key)
                if "error" in entry:
                    got = failed_probes.setdefault(fn, {"probe": fn, "kind": kind, "what": note,
                                                        "results": collections.OrderedDict()})
                    got["results"].setdefault(entry["error"], []).append(key)
            if seen:
                rows.append({"probe": fn, "what": note,
                             "results": [{"text": t, "builds": keys} for t, keys in seen.items()]})
        aligned = [e["aligned"] for r in good.values() for e in r["results"].values()
                   if e.get("measure") == kind == "keeps" and e.get("aligned")]
        sections.append({"kind": kind, "title": title, "rows": rows, "aligned": min(aligned) if aligned else None})
    layout = collections.OrderedDict()
    for key, result in good.items():
        for row, text in layout_cells(result.get("layout")).items():
            layout.setdefault(row, collections.OrderedDict()).setdefault(text, []).append(key)
    notes = dict(LAYOUT)
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
    from dread.cclab import profile
    return {"adapter": name, "arch": arch, "compiler": version,
            "builds": [{"build": k, "flags": v.get("flags"), "picked": v.get("picked"), "measured": "results" in v,
                        "error": v.get("error")} for k, v in builds.items()],
            "measured": list(good), "failed": {k: v["error"] for k, v in builds.items() if "results" not in v},
            "probes": probe_files, "sections": sections,
            "layout": [{"row": row, "what": notes.get(row, row), "results": [{"text": t, "builds": keys}
                                                                             for t, keys in seen.items()]}
                       for row, seen in layout.items()],
            "helpers": [{"helper": h, "called_by": sorted(info["ops"]),
                         "changed": [r for r in ORDER if r in info["changed"]] + sorted(info["changed"] - set(ORDER)),
                         "builds": sorted(info["builds"])} for h, info in helpers.items()],
            "lift": lift_data(good),
            "failed_probes": [dict(p, results=[{"error": e, "builds": keys} for e, keys in p["results"].items()])
                              for p in failed_probes.values()],
            "profile": profile.build_profile(work, name) if good else None}


def markdown(d):
    """report.md of an adapter's data()."""
    good = d["measured"]
    lines = ["# dread cclab: %s" % d["adapter"], ""]
    if d["compiler"] is not None:
        lines += ["Compiler: `%s`" % d["compiler"], ""]
    lines += ["%d builds measured: %s." % (len(good), ", ".join(good))]
    if d["failed"]:
        lines += ["", "Failed: " + "; ".join("%s (%s)" % (k, e) for k, e in d["failed"].items())]
    for s in d["sections"]:
        if s["rows"]:
            lines += ["", "## " + s["title"], "", "| probe | what | result (builds) |", "|---|---|---|"]
            lines += ["| `%s` | %s | %s |" % (row["probe"], row["what"], "; ".join(
                "%s (%s)" % (r["text"], builds_text(r["builds"], good)) for r in row["results"])) for row in s["rows"]]
        if s["aligned"]:
            lines += ["", "Every frame at a call is a multiple of %d bytes, over all builds: the stack alignment "
                          "the compiler keeps at calls (given an aligned entry)." % s["aligned"]]
    if d["layout"]:
        lines += ["", "## Where the functions lie", "", "| what | result (builds) |", "|---|---|"]
        for row in d["layout"]:
            lines.append("| %s | %s |" % (row["what"], "; ".join(
                "%s (%s)" % (r["text"], builds_text(r["builds"], good)) for r in row["results"])))
    if d["helpers"]:
        lines += ["", "## Runtime helpers the probes call", "",
                  "Registers changed: over every call in every build (results included).", "",
                  "| helper | called by | registers changed | builds |", "|---|---|---|---|"]
        for h in d["helpers"]:
            lines.append("| `%s` | %s | %s | %s |" % (h["helper"], ", ".join(h["called_by"]),
                                                     " ".join(h["changed"]) or "-", builds_text(h["builds"], good)))
    lift = d["lift"]
    if lift is not None:
        lines += ["", "## What DREAD lifts", "",
                  "dread lift --check on each build's probe functions (run --lift): the compiler's code translated "
                  "instruction by instruction, rebuilt and compared with the original in the emulator.", ""]
        lines += ["- %s: %d of %d" % (k, v["accepted"], v["checked"]) for k, v in lift["builds"].items()]
        lines += ["- %s: %s" % (k, e) for k, e in lift["errors"].items()]
        if lift["rows"]:
            lines += ["", "| probe | not lifted (builds) |", "|---|---|"]
            for row in lift["rows"]:
                lines.append("| `%s` | %s |" % (row["probe"], "; ".join(
                    "%s (%s)" % (r["text"], builds_text(r["builds"], lift["builds"])) for r in row["results"])))
    return "\n".join(lines) + "\n"


JSON_VERSION = 1


def write(work, adapter_name=None, out=print, json_path=None):
    """WORK/ADAPTER/report.md and profile.toml for each adapter in work (or the one named); with json_path,
    also every adapter's data() as JSON there ({"version", "work", "adapters": {name: data}}, written
    whole and renamed into place)."""
    adapters = [adapter_name] if adapter_name else sorted(
        d for d in os.listdir(work) if os.path.isdir(os.path.join(work, d)))
    found = {}
    for name in adapters:
        d = data(work, name)
        if d is None:
            out("dread cclab report: nothing measured for %s in %s" % (name, work))
            continue
        found[name] = d
        path = os.path.join(work, name, "report.md")
        with open(path, "w") as f:
            f.write(markdown(d))
        from dread.cclab import profile
        made = profile.write(work, name, d["profile"]) if d["profile"] is not None else None
        out("dread cclab report: %s%s" % (path, ", " + made if made else ""))
    if json_path:
        folder = os.path.dirname(os.path.abspath(json_path))
        os.makedirs(folder, exist_ok=True)
        with open(json_path + ".tmp", "w") as f:
            json.dump({"version": JSON_VERSION, "work": os.path.abspath(work), "adapters": found}, f, indent=1,
                      sort_keys=True)
            f.write("\n")
        os.replace(json_path + ".tmp", json_path)
        out("dread cclab report: %s" % json_path)
    return 0
