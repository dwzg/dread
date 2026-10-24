"""dread gui's views of one function (dread/guipages/views.py) on a copy of gizmo whose parse_uint is
written wrong on purpose (it multiplies by 11 where the original multiplies by 10), built, tested and
lift-checked: the failing cases the records name (dread test's tests.tsv, the lift check's verdict of
apply_config, which calls parse_uint), a trace run from the page as a job and its record read back in
both modes, a record made stale by a newer case file, the mutation check (its refusal for code that is
not C of src, the unchanged source that fails its cases, the argued survivors), the code left unrun,
the registers (dread.regflow's facts at once, dread callconv as a job), and the safety of the jobs (the
token, a read-only server, wrong requests)."""
import json
import os
import subprocess
import sys
import time

import pytest

import dread
from dread import gui
from dread.target import Target
from test_gui import answer, gizmo_copy, needs_gcc, request, serving
from test_gui_run import wait_job

pytestmark = [pytest.mark.compiles, pytest.mark.slow, needs_gcc]

WRONG = ("v = v * (hex ? 16 : 10) + d;", "v = v * (hex ? 16 : 11) + d;")


def dread_command(target, *args):
    """A command of this DREAD in the target's directory, as a job runs it; its exit status."""
    env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(dread.__file__))))
    return subprocess.run([sys.executable, "-m", "dread.cli"] + list(args) + ["--target", target.root],
                          cwd=target.root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


def planted_copy(folder, lift_check=True):
    """A copy of gizmo-m3 with parse_uint written wrong, rebuilt and tested (tests.tsv names its failing
    cases) and, with lift_check, lift-checked (apply_config dropped for a failing case)."""
    target = gizmo_copy(folder)
    path = os.path.join(os.path.dirname(target.root), "rewritten", "rewritten.c")
    with open(path) as f:
        text = f.read()
    assert WRONG[0] in text
    with open(path, "w") as f:
        f.write(text.replace(*WRONG))
    assert dread_command(target, "recon") == 0
    assert dread_command(target, "test") == 1           # (parse_uint and apply_config fail)
    if lift_check:
        assert dread_command(target, "lift", "--check") == 0
    return Target.load(target.root)


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    return planted_copy(tmp_path_factory.mktemp("views"))


@pytest.fixture
def server(planted):
    srv = serving(planted)
    yield srv, planted
    srv.shutdown()
    srv.server_close()


def run(srv, **body):
    status, r = answer(srv, "/api/views/run", body)
    assert status == 200, r
    return wait_job(srv, r["id"])


def test_the_strip_and_the_failing_cases(server):
    """The page's files; the strip's share of each view; the failing cases of dread test (all of
    tests.tsv's failing column) and the lift check's (apply_config, dropped for a failing case: traced as
    the lift check builds the image)."""
    srv, target = server
    g = answer(srv, "/api/gui")[1]
    assert {"name": "views", "script": "views.js", "style": "views.css"} in g["pages"]
    assert request(srv, "/views.js")[0] == 200 and request(srv, "/views.css")[0] == 200
    status, s = answer(srv, "/api/views?name=parse_uint")
    assert status == 200 and s["trace"]["failing"] >= 1 and s["mutation"]["rewritten"], s
    assert s["registers"]["reads"] == ["r0", "r1"] and s["uncovered"]["missed"] == 0, s
    with open(os.path.join(target.build, "recon", "main", "tests.tsv")) as f:
        rows = {r.split("\t")[0]: r.rstrip("\n").split("\t") for r in f}
    assert rows["function"] == ["function", "cases", "failed", "missed", "failing"]
    failing = json.loads(rows["parse_uint"][4])
    d = answer(srv, "/api/views/trace?name=parse_uint")[1]
    assert [c["label"] for c in d["cases"]] == failing and len(failing) == int(rows["parse_uint"][2])
    assert {(c["mode"], c["from"]) for c in d["cases"]} == {("test", "dread test")}
    assert d["chosen"] == {"mode": "test", "label": failing[0]} and d["record"] is None and d["modes"]["test"]
    d = answer(srv, "/api/views/trace?name=apply_config")[1]
    lifted = [c for c in d["cases"] if c["mode"] == "lifted"]
    assert lifted and lifted[0]["from"] == "dread lift --check" and lifted[0]["label"].startswith("generated ")
    assert answer(srv, "/api/views/trace?name=clamp")[1]["cases"] == []
    assert answer(srv, "/api/views?name=no_such_function")[0] == 404


def test_a_readable_checks_failing_case(server):
    """A function the readable check left lifted for a failing case (its verdict written here as the check
    writes it, a round file with another FAIL line of it): both cases offered in the readable mode, the
    first traced as the readable check builds the image."""
    srv, target = server
    folder = os.path.join(target.build, "readable-check", "main")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "verdicts.tsv"), "w") as f:
        f.write("function\tverdict\treason\nhandle_key\tlifted\tFAIL handle_key [generated 4] | memory 0x20000030: "
                "1 vs 2\nclamp\treadable\t\n")
    with open(os.path.join(folder, "round-1.txt"), "w") as f:
        f.write("FAIL handle_key [generated 4]\n     memory 0x20000030: 1 vs 2\nFAIL handle_key [generated 9]\n"
                "FAIL handle_key                 40/42 cases agree\n")
    d = answer(srv, "/api/views/trace?name=handle_key")[1]
    readable = [(c["label"], c["from"]) for c in d["cases"] if c["mode"] == "readable"]
    assert readable == [("generated 4", "dread readable --check"), ("generated 9", "dread readable --check")]
    job = run(srv, view="trace", name="handle_key", mode="readable", label="generated 4")
    assert "--readable" in job["line"] and job["exit"] in (0, 1), job
    r = answer(srv, "/api/views/trace?name=handle_key&mode=readable&label=generated%204")[1]["record"]
    assert r["mode"] == "readable" and r["built"] == "readable C" and r["error"] is None


def test_a_trace_run_from_the_page(server):
    """A failing case traced as a job (dread trace --json into the view's record): the record read back
    with where the runs part in parse_uint, its job, its brief in the list of cases; a newer case file
    makes it stale; apply_config's lifted case traced as the lift check builds the image."""
    srv, target = server
    d = answer(srv, "/api/views/trace?name=parse_uint")[1]
    label = d["chosen"]["label"]
    job = run(srv, view="trace", name="parse_uint", mode="test", label=label)
    assert job["exit"] == 1 and "dread trace parse_uint" in job["line"] and "--json build/gui/views/main/trace/" in job["line"]
    d = answer(srv, "/api/views/trace?name=parse_uint&mode=test&label=" + label.replace(" ", "%20"))[1]
    r = d["record"]
    assert r and not r["agrees"] and r["exit"] == 1 and r["label"] == label and r["mode"] == "test", r
    fn = srv.state.program.by_name["parse_uint"]
    first = r["first"]
    assert first["original"]["function"] == "parse_uint" and fn.entry <= first["original"]["address"] < fn.entry + 0x100
    assert r["stale"] == [] and r["path"].startswith("build/gui/views/main/trace/parse_uint/") and d["job"]["id"] == job["id"]
    brief = next(c for c in d["cases"] if c["label"] == label)["record"]
    assert brief["agrees"] is False and brief["exit"] == 1
    s = answer(srv, "/api/views?name=parse_uint")[1]
    assert s["trace"]["traced"] >= 1 and s["trace"]["parts"] >= 1
    cases = os.path.join(target.build, "autocases", "main", "parse_uint.json")
    later = time.time() + 5
    os.utime(cases, (later, later))
    d = answer(srv, "/api/views/trace?name=parse_uint&mode=test&label=" + label.replace(" ", "%20"))[1]
    assert "build/autocases/main/parse_uint.json" in d["record"]["stale"]
    d = answer(srv, "/api/views/trace?name=apply_config")[1]
    case = next(c for c in d["cases"] if c["mode"] == "lifted")
    job = run(srv, view="trace", name="apply_config", mode="lifted", label=case["label"])
    assert "--lifted" in job["line"] and job["exit"] == 1
    r = answer(srv, "/api/views/trace?name=apply_config&mode=lifted&label=" + case["label"].replace(" ", "%20"))[1]["record"]
    assert r["built"] == "lifted C" and r["first"]["original"]["statement"]["path"] == "lifted/apply_config.c"


def test_the_mutation_check(server):
    """dread mutate as a job: refused for a function src does not define, the unchanged source that fails
    its cases recorded as the run's error, checksum's argued survivors with their arguments."""
    srv, _ = server
    status, r = answer(srv, "/api/views/run", {"view": "mutation", "name": "uart_init"})
    assert status == 409 and "not written in C" in r["error"]
    d = answer(srv, "/api/views/mutation?name=uart_init")[1]
    assert not d["rewritten"] and d["why"] and d["record"] is None
    job = run(srv, view="mutation", name="parse_uint")
    assert job["exit"] == 1
    d = answer(srv, "/api/views/mutation?name=parse_uint")[1]
    assert "fails its cases" in d["record"]["error"] and answer(srv, "/api/views?name=parse_uint")[1]["mutation"]["error"]
    job = run(srv, view="mutation", name="checksum")
    assert job["exit"] == 0
    d = answer(srv, "/api/views/mutation?name=checksum")[1]
    r = d["record"]
    assert d["rewritten"] and d["source"]["path"].endswith("rewritten.c") and r["file"].endswith("rewritten.c")
    assert r["survived"] == 0 and len(r["argued"]) == 2 and all(a["argument"] and a["old"] != a["new"] for a in r["argued"])
    assert r["caught"] > 0 and d["stale"] == [] and d["job"]["id"] == job["id"]


def test_the_code_left_unrun_and_the_registers(server):
    """dread uncovered as a job for a function the lift check accepted (its cached cases run on the
    original: nothing left unrun); dread.regflow's facts of parse_uint at once (what it reads, its caller
    and what that reads after the call); dread callconv as a job, which agrees with them."""
    srv, _ = server
    d = answer(srv, "/api/views/uncovered?name=handle_key")[1]
    assert d["verdicts"] and d["record"] is None and d["coverage"]["missed"] == []
    run(srv, view="uncovered", name="handle_key")
    d = answer(srv, "/api/views/uncovered?name=handle_key")[1]
    assert d["record"]["kind"] == "cached cases" and d["record"]["regions"] == [] and d["record"]["totals"]
    d = answer(srv, "/api/views/registers?name=parse_uint")[1]
    f = d["regflow"]
    assert f["reads"] == ["r0", "r1"] and f["result"] == "r0" and "r0" in f["names"] and d["callconv"] is None
    assert [c["caller"] for c in f["callers"]] == ["parse_kv"] and f["callers"][0]["registers"][0]["register"] == "r0"
    assert f["bits"] is not None and f["leftover"] is False
    job = run(srv, view="callconv", name="parse_uint")
    assert job["exit"] == 0
    d = answer(srv, "/api/views/registers?name=parse_uint")[1]
    m = d["callconv"]
    assert m["cases"] > 0 and m["found"] == {} and m["reads"] == ["r0", "r1"] and m["summary"]["measured"] == 1
    assert answer(srv, "/api/views?name=parse_uint")[1]["registers"]["measured"]


def test_the_jobs_are_safe(server):
    """A job needs the page's token, is refused by a read-only server, and takes only what it names: a
    view, a function of the image, a trace's mode and label."""
    srv, target = server
    status, _ = answer(srv, "/api/views/run", {"view": "uncovered", "name": "clamp"}, token=False)
    assert status == 403
    for body, code in (({"view": "nosuch", "name": "clamp"}, 400), ({"view": "trace", "name": "clamp"}, 400),
                       ({"view": "trace", "name": "clamp", "label": "generated 0", "mode": "fast"}, 400),
                       ({"view": "uncovered", "name": "../clamp"}, 404)):
        assert answer(srv, "/api/views/run", body)[0] == code, body
    job = run(srv, view="trace", name="clamp", mode="test", label="--src=/")      # (a label, never an option)
    assert job["exit"] == 2 and " -- clamp " in job["line"] + " ", job
    r = answer(srv, "/api/views/trace?name=clamp&mode=test&label=--src%3D%2F")[1]["record"]
    assert "no case labelled '--src=/'" in r["error"]
    viewer = gui.serve(Target.load(target.root), target.image(), port=0, read_only=True)
    import threading
    thread = threading.Thread(target=viewer.serve_forever, daemon=True)
    thread.start()
    try:
        status, r = answer(viewer, "/api/views/run", {"view": "uncovered", "name": "clamp"})
        assert status == 403 and "read-only" in r["error"]
        assert answer(viewer, "/api/views?name=clamp")[0] == 200
    finally:
        viewer.shutdown()
        viewer.server_close()
        thread.join(30)
