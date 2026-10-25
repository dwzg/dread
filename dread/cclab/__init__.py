"""dread cclab: a compiler laboratory. Put a compiler in, learn how it compiles.

A corpus of small probe programs (dread/cclab/probes: plain C99, each function asking one
question) is compiled and linked by a compiler adapter (dread.cclab.adapters) for every
combination of its option matrix; each build is made a DREAD target and measured in the emulator
(dread.cclab.lab): where arguments arrive and results come back, the sizes of the basic types,
which runtime helper each operation calls and what that helper reads, returns and changes, and the
shape of every probe's code. The results go to WORK/ADAPTER/BUILD/results.json, and `report`
compares the builds.

    dread cclab adapters                         the built-in adapters
    dread cclab doctor --adapter A [--work DIR]  the tools, their versions, one probe built and run
    dread cclab run --adapter A [--work DIR] [--build KEY...] [--probes NAME...] [--probe-dir DIR...]
                    [--jobs N] [--guard]
    dread cclab report [--work DIR] [--adapter A] [--json FILE]
    dread cclab compare [--work DIR] ORIGINAL REBUILD   where two compilers differ, and the options
                                                        of the rebuilding one that close a difference
    dread cclab bundle [--work DIR] --out FILE.zip   what may leave the work directory

For a proprietary toolchain (the adapter's guard = true, or --guard), `run` also writes a CLAUDE.md
and Claude Code deny rules into the work directory, and each build's dread.toml limits what an LLM
may be shown to the probes (dread.cclab.workdir).

The adapter is a built-in name or a TOML file of your own (a copy of a built-in one, edited); a
--probe-dir adds probes of one's own (C files with a TOML of specs each, like dread/cclab/probes). WORK
defaults to ./cclab-work. Nothing outside the work directory is written; nothing is sent anywhere.

What an LLM may see (the user's rule for IAR at work, 2026-10-19): the probes, their compiled code
and the compilers' own messages; never a company's code and never a compiler's runtime library code
(a build's image holds it beside the probes: results.json says which functions are probes).

What this does not establish: anything about code the probes do not contain, and that a probe's C
means the same to every compiler (they are plain C99 for that reason).
"""
import argparse
import json
import os
import sys

from dread.cclab import adapters as ad
from dread.cclab import lab


def _one(job):
    """One build in a worker: build and measure; returns (key, result or error)."""
    adapter_path, key, picked, flags, work, names, guard, probe_dirs = job
    adapter = ad.load(adapter_path)
    probe_files = lab.probes(names, probe_dirs)
    try:
        d = lab.build(adapter, key, picked, flags, probe_files, work, guard)
    except lab.BuildError as e:
        # the failed build's log beside its files (a run prints one line per build: what failed,
        # with which command and output, is only here)
        where = os.path.join(work, adapter.name, key)
        if os.path.isdir(where):
            with open(os.path.join(where, "failed.json"), "w") as f:
                json.dump({"adapter": adapter.name, "build": key, "error": str(e), "log": e.log}, f, indent=1)
        return key, {"error": str(e), "log": e.log}
    except ad.AdapterError as e:
        return key, {"error": str(e), "log": []}
    results = lab.measure(d, probe_files)
    try:
        layout = lab.layout(d, probe_files)
    except Exception as e:                  # a layout that cannot be read is a result, not a crash
        layout = {"error": "%s: %s" % (type(e).__name__, e)}
    out = {"layout": layout, "adapter": adapter.name, "arch": adapter.arch, "build": key, "picked": picked, "flags": flags,
           "probes": {fn: p.name for p in probe_files for fn in p.functions}, "results": results}
    with open(os.path.join(d, "results.json"), "w") as f:
        json.dump(out, f, indent=1)
    return key, out


def run(adapter, work, only=None, names=None, jobs=None, out=print, guard=None, probe_dirs=(), lift=False):
    """Build and measure every build of the adapter's matrix (or those in only); returns
    {key: results or {"error", "log"}}. guard: as the adapter says unless given; probe_dirs:
    directories of one's own probes besides DREAD's. lift: then dread lift --check on each measured
    build's probe functions, one build after the other (lab.lift_build), the verdicts into its
    results.json ("lift"): whether DREAD handles this compiler's code, not only how it calls."""
    guard = adapter.guard if guard is None else guard
    probe_dirs = [os.path.abspath(d) for d in probe_dirs]
    try:
        lab.probes(names, probe_dirs)           # a wrong name fails here, not in every build
    except ValueError as e:
        raise ad.AdapterError(str(e))
    from dread.util.polite import polite, workers
    builds = [b for b in adapter.builds() if not only or b[0] in only]
    if not builds:
        raise ad.AdapterError("no build matches %s (there are: %s)" % (
            ", ".join(only or []), ", ".join(k for k, _, _ in adapter.builds())))
    os.makedirs(os.path.join(work, adapter.name), exist_ok=True)
    if guard:
        from dread.cclab import workdir
        workdir.guard(work)
    version = adapter.version(work)
    with open(os.path.join(work, adapter.name, "version.txt"), "w") as f:
        f.write(version + "\n")
    jobs_list = [(adapter.path, key, picked, flags, work, names, guard, probe_dirs) for key, picked, flags in builds]
    results = {}
    n = jobs or workers()
    if n > 1 and len(jobs_list) > 1:
        import multiprocessing
        polite()
        with multiprocessing.get_context("fork").Pool(min(n, len(jobs_list))) as pool:
            for key, result in pool.imap_unordered(_one, jobs_list):
                results[key] = result
                out("%-28s %s" % (key, "error: " + result["error"] if "error" in result else "measured"))
    else:
        for job in jobs_list:
            key, result = _one(job)
            results[key] = result
            out("%-28s %s" % (key, "error: " + result["error"] if "error" in result else "measured"))
    if lift:
        picks = {key: picked for key, picked, _ in builds}
        for key in sorted(results):
            result = results[key]
            if "error" in result:
                continue
            d = os.path.join(work, adapter.name, key)
            compiler = lab.rebuild_compiler(adapter, picks[key], result["results"])
            if compiler is None:
                result["lift"] = {"error": "no rebuilding compiler for %s: give the adapter a [rebuild] table"
                                           % adapter.arch}
            else:
                probes = [fn for fn, e in result["results"].items() if e.get("error") != "not in the linked image"]
                result["lift"] = lab.lift_build(d, compiler, probes)
            with open(os.path.join(d, "results.json"), "w") as f:
                json.dump(result, f, indent=1)
            got = result["lift"]
            if "error" in got:
                out("%-28s lift: %s" % (key, got["error"]))
            else:
                n = sum(1 for v in got["verdicts"].values() if v[0] == "accepted")
                out("%-28s lifted %d of %d probe functions (%.0f s)" % (key, n, len(got["verdicts"]), got["seconds"]))
    failed = sorted(k for k, r in results.items() if "error" in r)
    if failed:
        out("the failed builds' commands and output: %s" % os.path.join(work, adapter.name, failed[0], "failed.json")
            + (" (and the same in the other %d)" % (len(failed) - 1) if len(failed) > 1 else ""))
    return results


def doctor(adapter, work, out=print, guard=None):
    """The adapter's tools found, the compiler's version, and the first build of the matrix built
    with the smallest probe and run: what fails is said with the command and its output. Returns 0
    when all went through."""
    ok = True
    guard = adapter.guard if guard is None else guard
    out("adapter %s (%s, family %s%s%s)" % (adapter.name, adapter.path, adapter.family,
                                           ", Windows tools through WSL" if adapter.windows else "",
                                           ", guarded" if guard else ""))
    if guard:
        from dread.cclab import workdir
        workdir.guard(work)
    for name in adapter.tools:
        try:
            path = adapter.tool(name)
            found = os.path.exists(path) or bool(__import__("shutil").which(path))
            out("  %-8s %s%s" % (name, path, "" if found else "   NOT FOUND"))
            ok &= found
        except ad.AdapterError as e:
            out("  %-8s %s" % (name, e))
            ok = False
    if adapter.windows:
        here = os.path.abspath(work)
        if not here.startswith("/mnt/"):
            out("  the work directory %s is not on the Windows side: a Windows compiler may not reach it "
                "(use --work /mnt/c/...)" % here)
    out("version:\n  " + adapter.version(work).replace("\n", "\n  "))
    if not ok:
        out("doctor: tools missing; set [tools] in a copy of the adapter and pass --adapter FILE")
        return 1
    key, picked, flags = adapter.builds()[0]
    probe_files = lab.probes(["abi"])
    try:
        d = lab.build(adapter, "doctor-" + key, picked, flags, probe_files, work, guard)
    except (lab.BuildError, ad.AdapterError) as e:
        out("build %s: %s" % (key, e))
        for step in getattr(e, "log", []):
            out("  %s %s: exit %d\n    %s\n    %s" % (step["step"], step["file"], step["code"], " ".join(step["argv"]),
                                                   step["output"].strip().replace("\n", "\n    ")[:2000]))
        out("doctor: fix the failing command in a copy of the adapter ([commands]) and pass --adapter FILE")
        return 1
    results = lab.measure(d, probe_files)
    probe = results.get("pick_i5_5", {})
    out("build %s: compiled, linked and imported into %s" % (key, d))
    out("run: the 5th of five ints arrives in %s" % probe.get("where", probe.get("error", "?")))
    if "where" not in probe:
        out("doctor: the probe did not run; see %s/build.json" % d)
        return 1
    out("doctor: all went through")
    return 0


def make_report(work, adapter_name=None, out=print, json_path=None):
    """Markdown comparing the builds of each adapter in work (written to WORK/ADAPTER/report.md), and
    with json_path the same as data there (dread.cclab.report data)."""
    import importlib
    return importlib.import_module("dread.cclab.report").write(work, adapter_name, out, json_path=json_path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dread cclab", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("adapters", help="the built-in adapters")
    p = sub.add_parser("ce-list", help="Compiler Explorer's compilers (an adapter: --adapter ce:ID)")
    p.add_argument("words", nargs="*", help="only those whose name or id has every word")
    p.add_argument("--lang", default="c", choices=("c", "c++"))
    p = sub.add_parser("doctor", help="check an adapter: tools, version, one probe built and run")
    p.add_argument("--adapter", required=True)
    p.add_argument("--work", default="cclab-work")
    p.add_argument("--guard", action="store_true", default=None,
                   help="guard the work directory and the builds as for a proprietary toolchain")
    p = sub.add_parser("run", help="build and measure the probes for every build of an adapter")
    p.add_argument("--adapter", required=True)
    p.add_argument("--work", default="cclab-work")
    p.add_argument("--build", action="append", help="only this build (repeatable)")
    p.add_argument("--probes", action="append", help="only these probe files (repeatable)")
    p.add_argument("--probe-dir", action="append", default=[], metavar="DIR",
                   help="a directory of one's own probes besides DREAD's (repeatable)")
    p.add_argument("--jobs", type=int)
    p.add_argument("--guard", action="store_true", default=None,
                   help="guard the work directory and the builds as for a proprietary toolchain")
    p.add_argument("--lift", action="store_true",
                   help="then dread lift --check on each build's probe functions (GCC or clang rebuilds them; "
                        "the adapter's [rebuild] says otherwise): what DREAD makes of this compiler's code")
    p = sub.add_parser("report", help="compare the builds measured in the work directory")
    p.add_argument("--work", default="cclab-work")
    p.add_argument("--adapter")
    p.add_argument("--json", metavar="FILE",
                   help="also write the report of each adapter, its failed probes and its profile as JSON to FILE "
                        "(docs/formats.md)")
    p = sub.add_parser("compare", help="where two measured compilers differ, with the options that close it")
    p.add_argument("--work", default="cclab-work")
    p.add_argument("original", help="the adapter of the compiler that made the image")
    p.add_argument("rebuild", help="the adapter of the compiler that rebuilds its functions")
    p.add_argument("--json", metavar="FILE", help="also write the comparison as JSON to FILE (docs/formats.md)")
    p = sub.add_parser("bundle", help="zip what may leave the work directory (no images, binaries, tables)")
    p.add_argument("--work", default="cclab-work")
    p.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "adapters":
            for name, path in ad.builtin().items():
                a = ad.load(path)
                print("%-12s %-4s %3d builds  %s" % (name, a.family, len(a.builds()),
                                                     ", ".join("%s: %s" % (d, " ".join(c)) for d, c in a.matrix.items())))
            return 0
        if args.action == "ce-list":
            from dread.cclab import ce
            try:
                found = ce.compilers(args.lang)
            except ce.CompilerExplorerError as e:
                raise ad.AdapterError(str(e))
            for c in found:
                text = (c["id"] + " " + c["name"]).lower()
                if all(w.lower() in text for w in args.words):
                    print("%-22s %-10s %s" % (c["id"], c.get("instructionSet") or "-", c["name"]))
            return 0
        if args.action == "report":
            return make_report(args.work, args.adapter, json_path=args.json)
        if args.action == "compare":
            from dread.cclab import compare
            return compare.write(args.work, args.original, args.rebuild, json_path=args.json)
        if args.action == "bundle":
            from dread.cclab import workdir
            packed = workdir.bundle(args.work, args.out)
            print("dread cclab: %d files into %s (no images, binaries or tables)" % (len(packed), args.out))
            return 0
        adapter = ad.load(args.adapter)
        if args.action == "doctor":
            return doctor(adapter, args.work, guard=args.guard)
        if adapter.ce and args.probe_dir and not os.environ.get("DREAD_CE_URL"):
            # (one's own probes may come from a private project's code: rule 1)
            raise ad.AdapterError("a Compiler Explorer adapter compiles DREAD's own probes only on the public "
                                  "instance; --probe-dir needs an instance of one's own (DREAD_CE_URL)")
        results = run(adapter, args.work, args.build, args.probes, args.jobs, guard=args.guard,
                      probe_dirs=args.probe_dir, lift=args.lift)
        failed = [k for k, r in results.items() if "error" in r]
        print("dread cclab: %d builds measured, %d failed, in %s" % (len(results) - len(failed), len(failed),
                                                                   os.path.join(args.work, adapter.name)))
        return 1 if failed else 0
    except ad.AdapterError as e:
        print("dread cclab: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
