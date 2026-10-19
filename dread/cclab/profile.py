"""dread cclab's profile: what DREAD's tools take from the lab's measurements of one compiler.

WORK/ADAPTER/profile.toml, written by `report`:

    [compiler]
    adapter = "gcc-arm"
    version = "arm-none-eabi-gcc (Arch Repository) 16.2.0"
    builds = ["cortex-m0-O0", ...]

    [helpers."__aeabi_uidivmod"]    # every runtime helper the probes called (not a probe itself)
    names = ["__aeabi_uidivmod"]    # every name at its address in some build
    results = ["r0", "r1"]          # the registers the compiler's own code reads after a call of it
                                    # (dread.regflow on every build's image, joined)
    changes = ["r0", "r1", "r2", "r3", "r12"]   # the registers it changed in the probes' runs
    called_by = ["op_umod", "op_umod10"]

A target names it as `[compiler] profile = "PATH"` in its dread.toml; dread.regflow then takes a
helper's results as read after every call of a function with one of its names (what the
compiler's own code expects of the helper, whatever the image's callers read of it: compiled C
calls it by that contract; DREAD learned that on the MSP430, where rewritten C read a helper's
upper word that no original caller did).

What a profile does not establish: results the probes never read (a helper's result registers are
those some probe's compiled code read), registers a helper changes only on inputs the runs did not
give it.
"""
import glob
import json
import os

ORDER = ["r%d" % i for i in range(16)] + ["sp", "lr"]


def build_profile(work, adapter):
    """{compiler: {...}, helpers: {name: {...}}} from an adapter's measured builds."""
    from dread import regflow
    from dread.cclab import lab
    helpers, builds, version = {}, [], ""
    vpath = os.path.join(work, adapter, "version.txt")
    if os.path.exists(vpath):
        with open(vpath) as f:
            version = f.read().strip().splitlines()[0] if f else ""
    for d in sorted(glob.glob(os.path.join(work, adapter, "*"))):
        rpath = os.path.join(d, "results.json")
        if os.path.basename(d).startswith("doctor-") or not os.path.exists(rpath):
            continue
        with open(rpath) as f:
            result = json.load(f)
        builds.append(result["build"])
        probes = set(result.get("probes", {}))
        bench = lab.Bench(d)
        flow = regflow.load(bench.target, bench.image, bench.program)
        for fn, entry in result["results"].items():
            for h in entry.get("helpers", []):
                if h.get("depth", 1) != 1:
                    continue
                names = h["function"].split(" = ")
                if any(n in probes for n in names):
                    continue
                key = names[0]
                info = helpers.setdefault(key, {"names": [], "results": set(), "changes": set(), "called_by": set()})
                info["names"] += [n for n in names if n not in info["names"]]
                info["changes"] |= set(h.get("changed", []))
                info["called_by"].add(fn)
                own = bench.program.by_name.get(key) or next((bench.program.by_name[n] for n in names
                                                              if n in bench.program.by_name), None)
                if own is not None:
                    info["results"] |= set(flow.kept_regs(own.name))
    merged = []                         # one entry per helper: builds may name it by another alias
    for info in helpers.values():
        for other in merged:
            if set(other["names"]) & set(info["names"]):
                other["names"] += [n for n in info["names"] if n not in other["names"]]
                for k in ("results", "changes", "called_by"):
                    other[k] |= info[k]
                break
        else:
            merged.append(info)
    out = {}
    for info in merged:
        for k in ("results", "changes"):
            info[k] = [r for r in ORDER if r in info[k]]
        info["called_by"] = sorted(info["called_by"])
        out[sorted(info["names"], key=lambda n: (not n.startswith("__aeabi_"), n))[0]] = info
    return {"compiler": {"adapter": adapter, "version": version, "builds": builds}, "helpers": out}


def _toml_value(v):
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return json.dumps(v)


def write(work, adapter):
    """Write WORK/ADAPTER/profile.toml; returns its path, or None when nothing was measured."""
    profile = build_profile(work, adapter)
    if not profile["compiler"]["builds"]:
        return None
    lines = ["# dread cclab: the profile of %s (written by dread cclab report; see dread/cclab/profile.py)" % adapter,
             "", "[compiler]"]
    lines += ["%s = %s" % (k, _toml_value(v)) for k, v in profile["compiler"].items()]
    for name, info in sorted(profile["helpers"].items()):
        lines += ["", "[helpers.%s]" % json.dumps(name)]
        lines += ["%s = %s" % (k, _toml_value(info[k])) for k in ("names", "results", "changes", "called_by")]
    path = os.path.join(work, adapter, "profile.toml")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def load(path):
    """{helper name (every alias): [result registers]} of a profile file."""
    import tomllib
    with open(path, "rb") as f:
        data = tomllib.load(f)
    out = {}
    for info in data.get("helpers", {}).values():
        for name in info.get("names", []):
            out[name] = list(info.get("results", []))
    return out
