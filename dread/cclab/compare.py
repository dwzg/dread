"""dread cclab compare: two compilers measured in one work directory, side by side.

DREAD rebuilds a function with GCC or clang and links it over an image another compiler made, so
the rebuilt C calls the original's code and is called by it. That works where the two compilers
agree on the calling convention, the types and what a call keeps. The comparison lists where they
do not, and the options of the rebuilding compiler that close a difference where it has one:

    dread cclab compare --work DIR ORIGINAL REBUILD     (adapter names, both measured in DIR)

It writes WORK/compare-ORIGINAL-REBUILD.md: the probes whose results differ, each compiler's
results with the builds that gave them (a result that varies with a compiler's options shows its
builds), the options (GCC's names; clang takes most of them) and, apart, the operations whose
helpers or inline code differ, which matter to a reader, not to linking.

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
    if fn.startswith("ret_s"):
        if original.startswith("memory") and not rebuild.startswith("memory"):
            return "-fpcc-struct-return (every struct returned through memory)"
        if rebuild.startswith("memory") and not original.startswith("memory"):
            return "-freg-struct-return (small structs returned in registers)"
    return None


def write(work, original, rebuild, out=print):
    """Write WORK/compare-ORIGINAL-REBUILD.md; returns 0, or 1 when either was not measured."""
    a_builds, a_arch, a, a_align = results(work, original)
    b_builds, b_arch, b, b_align = results(work, rebuild)
    for name, builds in ((original, a_builds), (rebuild, b_builds)):
        if not builds:
            out("dread cclab compare: nothing measured for %s in %s" % (name, work))
            return 1
    lines = ["# dread cclab: %s against %s" % (original, rebuild), "",
             "%s (the original's compiler): %d builds; %s (the rebuilding one): %d builds. A probe is "
             "listed where the two differ, each result with the builds that gave it." % (
                 original, len(a_builds), rebuild, len(b_builds))]
    if a_arch != b_arch:
        lines += ["", "Different architectures (%s, %s): their code cannot be linked together." % (a_arch, b_arch)]
    found = {"abi": [], "code": []}
    hints, same = collections.OrderedDict(), 0
    for key in list(a) + [k for k in b if k not in a]:
        fn, side = key
        ra, rb = a.get(key), b.get(key)
        if ra is None or rb is None:
            found[side].append("| `%s` | %s | %s | %s |" % (fn, (ra or rb)["note"], "-" if ra is None else "measured",
                                                           "-" if rb is None else "measured"))
            continue
        if set(ra["texts"]) == set(rb["texts"]):
            same += side == "abi"
            continue
        shown = ["; ".join("%s (%s)" % (t, report.builds_text(keys, builds)) for t, keys in r["texts"].items())
                 for r, builds in ((ra, a_builds), (rb, b_builds))]
        found[side].append("| `%s` | %s | %s | %s |" % (fn, ra["note"], *shown))
        if side == "abi" and len(ra["texts"]) == 1 and len(rb["texts"]) == 1:
            h = hint(fn, next(iter(ra["texts"])), next(iter(rb["texts"])))
            if h:
                hints.setdefault(h, []).append(fn)
    if a_align != b_align:
        found["abi"].append("| stack alignment at calls | the largest power of two dividing every frame at a "
                            "call | %s | %s |" % (a_align, b_align))
    head = ["| probe | what | %s | %s |" % (original, rebuild), "|---|---|---|---|"]
    lines += ["", "## Calling convention, types, what a call keeps", ""]
    lines += head + found["abi"] if found["abi"] else ["They agree on every probe."]
    if hints:
        lines += ["", "## Options of %s that close a difference" % rebuild, ""]
        lines += ["- %s: %s" % (h, ", ".join("`%s`" % f for f in fns)) for h, fns in hints.items()]
    if found["code"]:
        lines += ["", "## The code: helpers, inline code, saves and frames, where they differ", "",
                  "These matter to a reader of the two compilers' code, not to linking it."] + [""] + head + found["code"]
    lines += ["", "%d probes agree on what linking depends on." % same]
    path = os.path.join(work, "compare-%s-%s.md" % (original, rebuild))
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    out("dread cclab compare: %s (%d differences that matter for linking, %d in the code)" % (
        path, len(found["abi"]), len(found["code"])))
    return 0
