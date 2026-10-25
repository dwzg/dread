"""dread cclab compare: two compilers measured in one work directory, side by side.

DREAD rebuilds a function with GCC or clang and links it over an image another compiler made, so
the rebuilt C calls the original's code and is called by it. That works where the two compilers
agree on the calling convention, the types and what a call keeps. The comparison lists where they
do not, and the options of the rebuilding compiler that close a difference where it has one:

    dread cclab compare --work DIR ORIGINAL REBUILD     (adapter names, both measured in DIR)

It writes WORK/compare-ORIGINAL-REBUILD.md: the probes whose results differ, each compiler's
results with the builds that gave them (a result that varies with a compiler's options shows its
builds), the options (GCC's names; clang takes most of them) and, apart, the operations whose
helpers or inline code differ, which matter to a reader, not to linking. With --json FILE it also
writes the same as data (data(); docs/formats.md), which dread gui's compiler lab page shows.

What it does not establish: that two matrices' builds are comparable (GCC's -O2 against IAR's -Oh);
that an option it names has the effect wanted with the compiler at hand (add it to the adapter's
cflags, run again, compare again).
"""
import collections
import os

from dread.cclab import report

def cells(entry, arch):
    """A probe's result as (what linking depends on, what the code looks like): either may be None.
    A value is compared as its number (registers differ in width between architectures, the
    number does not); of what a call keeps, the registers that came back intact count for linking,
    the saves and frames are the code's shape; an operation's helpers are the code's."""
    if "error" in entry:
        return "error: " + entry["error"][:60], None
    kind = entry.get("measure")
    if kind == "op":
        return None, report.op_text(entry, arch)
    if kind == "keeps":
        return "kept " + entry.get("kept", "?"), report.keeps_text(entry)
    if kind == "value":
        return str(entry.get("value")), None
    return entry.get("where", "?"), None


def results(work, adapter):
    """(builds, arch, rows, alignment) of an adapter: rows {(function, side): {note, texts: {text:
    [build keys]}}} with side "abi" or "code"; alignment the stack alignment kept at calls over all
    builds (None when nothing was measured)."""
    builds = {k: v for k, v in report.load(work, adapter).items() if "results" in v}
    arch = next((v.get("arch") for v in builds.values() if v.get("arch")), "arm")
    rows, aligned = {}, []
    for key, result in builds.items():
        for fn, entry in result["results"].items():
            for side, text in zip(("abi", "code"), cells(entry, arch)):
                if text is not None:
                    row = rows.setdefault((fn, side), {"note": entry.get("note", ""),
                                                       "texts": collections.OrderedDict()})
                    row["texts"].setdefault(text, []).append(key)
            if entry.get("aligned"):
                aligned.append(entry["aligned"])
        notes = dict(report.LAYOUT)
        for row, text in report.layout_cells(result.get("layout")).items():
            r = rows.setdefault(("(layout) " + row, "code"), {"note": notes.get(row, ""),
                                                             "texts": collections.OrderedDict()})
            r["texts"].setdefault(text, []).append(key)
    return builds, arch, rows, min(aligned) if aligned else None


def lift_data(builds_a, builds_b):
    """run --lift's verdicts of both compilers, or None unless both were lifted: {"original", "rebuild":
    [accepted, probe functions, builds], "rows": [{"probe", "original", "rebuild": [{"text", "builds"}]}]},
    a row for each probe one compiler's code lifts in every build and the other's not in some."""
    a = {k: report.lift_verdicts(v) for k, v in builds_a.items() if report.lift_verdicts(v)}
    b = {k: report.lift_verdicts(v) for k, v in builds_b.items() if report.lift_verdicts(v)}
    if not a or not b:
        return None

    def count(side):
        return [sum(1 for v in side.values() for t in v.values() if t == "accepted"), sum(len(v) for v in side.values()),
                len(side)]
    rows = []
    for fn in sorted({fn for v in list(a.values()) + list(b.values()) for fn in v}):
        texts = []
        for side in (a, b):
            seen = collections.OrderedDict()
            for key, verdicts in side.items():
                if fn in verdicts:
                    seen.setdefault(verdicts[fn], []).append(key)
            texts.append(seen)
        if all(set(t) <= {"accepted"} for t in texts):
            continue
        rows.append({"probe": fn, "original": cell(texts[0], a), "rebuild": cell(texts[1], b)})
    return {"original": count(a), "rebuild": count(b), "rows": rows}


def lift_section(lift, original, rebuild):
    """The Markdown lines of lift_data's record ([] for None)."""
    if lift is None:
        return []
    lines = ["", "## What DREAD lifts (run --lift)", "",
             "%s: %d of %d probe functions over %d builds; %s: %d of %d over %d." % (
                 original, *lift["original"], rebuild, *lift["rebuild"])]
    rows = ["| `%s` | %s | %s |" % (r["probe"], shown(r["original"]) or "-", shown(r["rebuild"]) or "-")
            for r in lift["rows"]]
    if rows:
        lines += ["", "| probe | %s | %s |" % (original, rebuild), "|---|---|---|"] + rows
    return lines


def cell(texts, builds):
    """[{"text", "builds", "shown"}] of a result's texts ({text: [build keys]}), shown: the builds as the
    Markdown writes them (all N, or their keys)."""
    return [{"text": t, "builds": list(keys), "shown": report.builds_text(keys, builds)} for t, keys in texts.items()]


def shown(c):
    """A cell (cell()) as the Markdown writes it."""
    return "; ".join("%s (%s)" % (x["text"], x["shown"]) for x in c)


def hint(fn, original, rebuild):
    """The option of the rebuilding compiler that gives the original's result for this probe, a
    sentence where it has none, or None where nothing is known."""
    try:
        a, b = int(original), int(rebuild)
    except ValueError:
        a = b = None
    if a is not None:
        if fn == "size_enum_small":
            return "-fshort-enums" if a < b else "-fno-short-enums"
        if fn == "char_is_signed":
            return "-funsigned-char" if a == 0 else "-fsigned-char"
        if fn == "size_wchar":
            return "-fshort-wchar" if a < b else "-fno-short-wchar"
        if fn in ("size_double", "size_long_double") and a < b:
            return ("no option: GCC and clang keep it %d bytes on this target; where the original's is %d "
                    "bytes, the C has to use float" % (b, a))
    if fn == "pick_dfdf_f" and original.startswith("s") and rebuild.startswith("s"):
        return ("no option: the two fill the VFP registers differently (one leaves the single a double "
                "skipped unused, the other back-fills it, as AAPCS's VFP variant says); a function with "
                "floats after doubles so cannot be called across the two as it is")
    if fn.startswith("ret_s"):
        if original.startswith("memory") and not rebuild.startswith("memory"):
            return "-fpcc-struct-return (every struct returned through memory)"
        if rebuild.startswith("memory") and not original.startswith("memory"):
            return "-freg-struct-return (small structs returned in registers)"
    return None


def data(work, original, rebuild):
    """The comparison as data, or None when either compiler was not measured in work: {"version": 1,
    "original", "rebuild", "builds": {"original", "rebuild": [build keys]}, "arch": {"original", "rebuild"},
    "abi" and "code": [{"probe", "note", "original", "rebuild"}] (a side's cell(), or null where that
    compiler has no such probe measured), "alignment": {"original", "rebuild"} (the stack alignment kept at
    calls, null where nothing was measured), "hints": [{"option", "probes"}], "lift" (lift_data), "same" (the
    probes that agree on what linking depends on)}. abi: what linking depends on (the calling convention,
    the types, what a call keeps); code: helpers, inline code, saves and frames, which matter to a reader."""
    a_builds, a_arch, a, a_align = results(work, original)
    b_builds, b_arch, b, b_align = results(work, rebuild)
    if not a_builds or not b_builds:
        return None
    found = {"abi": [], "code": []}
    hints, same = collections.OrderedDict(), 0
    for key in list(a) + [k for k in b if k not in a]:
        fn, side = key
        ra, rb = a.get(key), b.get(key)
        if ra is not None and rb is not None and set(ra["texts"]) == set(rb["texts"]):
            same += side == "abi"
            continue
        found[side].append({"probe": fn, "note": (ra or rb)["note"],
                            "original": None if ra is None else cell(ra["texts"], a_builds),
                            "rebuild": None if rb is None else cell(rb["texts"], b_builds)})
        if ra is not None and rb is not None and side == "abi" and len(ra["texts"]) == 1 and len(rb["texts"]) == 1:
            h = hint(fn, next(iter(ra["texts"])), next(iter(rb["texts"])))
            if h:
                hints.setdefault(h, []).append(fn)
    return {"version": 1, "original": original, "rebuild": rebuild,
            "builds": {"original": list(a_builds), "rebuild": list(b_builds)},
            "arch": {"original": a_arch, "rebuild": b_arch}, "abi": found["abi"], "code": found["code"],
            "alignment": {"original": a_align, "rebuild": b_align},
            "hints": [{"option": h, "probes": fns} for h, fns in hints.items()],
            "lift": lift_data(a_builds, b_builds), "same": same}


def markdown(d):
    """The Markdown lines of data()'s record (what WORK/compare-ORIGINAL-REBUILD.md holds)."""
    original, rebuild = d["original"], d["rebuild"]
    lines = ["# dread cclab: %s against %s" % (original, rebuild), "",
             "%s (the original's compiler): %d builds; %s (the rebuilding one): %d builds. A probe is "
             "listed where the two differ, each result with the builds that gave it." % (
                 original, len(d["builds"]["original"]), rebuild, len(d["builds"]["rebuild"]))]
    if d["arch"]["original"] != d["arch"]["rebuild"]:
        lines += ["", "Different architectures (%s, %s): their code cannot be linked together." % (
            d["arch"]["original"], d["arch"]["rebuild"])]

    def row(r):
        if r["original"] is None or r["rebuild"] is None:
            return "| `%s` | %s | %s | %s |" % (r["probe"], r["note"], "-" if r["original"] is None else "measured",
                                               "-" if r["rebuild"] is None else "measured")
        return "| `%s` | %s | %s | %s |" % (r["probe"], r["note"], shown(r["original"]), shown(r["rebuild"]))
    abi = [row(r) for r in d["abi"]]
    if d["alignment"]["original"] != d["alignment"]["rebuild"]:
        abi.append("| stack alignment at calls | the largest power of two dividing every frame at a call | %s | %s |"
                   % (d["alignment"]["original"], d["alignment"]["rebuild"]))
    code = [row(r) for r in d["code"]]
    head = ["| probe | what | %s | %s |" % (original, rebuild), "|---|---|---|---|"]
    lines += lift_section(d["lift"], original, rebuild)
    lines += ["", "## Calling convention, types, what a call keeps", ""]
    lines += head + abi if abi else ["They agree on every probe."]
    if d["hints"]:
        lines += ["", "## Options of %s that close a difference" % rebuild, ""]
        lines += ["- %s: %s" % (h["option"], ", ".join("`%s`" % f for f in h["probes"])) for h in d["hints"]]
    if code:
        lines += ["", "## The code: helpers, inline code, saves and frames, where they differ", "",
                  "These matter to a reader of the two compilers' code, not to linking it."] + [""] + head + code
    lines += ["", "%d probes agree on what linking depends on." % d["same"]]
    return lines, len(abi), len(code)


def write(work, original, rebuild, out=print, json_path=None):
    """Write WORK/compare-ORIGINAL-REBUILD.md (and with json_path data()'s record, its "markdown" the .md's
    path); returns 0, or 1 when either was not measured."""
    d = data(work, original, rebuild)
    if d is None:
        for name in (original, rebuild):
            if not results(work, name)[0]:
                out("dread cclab compare: nothing measured for %s in %s" % (name, work))
                break
        return 1
    lines, abi, code = markdown(d)
    path = os.path.join(work, "compare-%s-%s.md" % (original, rebuild))
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    if json_path:
        import json
        with open(json_path, "w") as f:
            json.dump(dict(d, markdown=path), f, indent=1)
            f.write("\n")
    out("dread cclab compare: %s (%d differences that matter for linking, %d in the code)" % (path, abi, code))
    return 0
