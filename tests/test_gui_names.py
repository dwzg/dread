"""dread gui's names page (dread/guipages/names.py) on a copy of gizmo-m3 whose functions lost their names
(FUN_... for every one): dread samecode against the named build and dread callpair against the ARM-state
build, each run from the page as a job, their proposals read back (only those that give a function without
a name one), the chosen ones applied by dread rename as a job, dread whatcc's table read into proposals,
and the safety of the page's changes (the token, a read-only server, words that are no names)."""
import os
import shutil

import pytest

from dread import gui
from dread.target import Target
from test_gui import answer, request, serving
from test_gui_run import wait_job

pytestmark = [pytest.mark.slow]


def stripped_copy(folder):
    """gizmo copied (m3's C includes its siblings' files: dread rename compiles it) with every function of m3
    called FUN_ADDRESS (its tables and symbols)."""
    from conftest import GIZMO
    shutil.copytree(GIZMO, folder / "gizmo", ignore=shutil.ignore_patterns("build"))
    copy = folder / "gizmo" / "m3"
    functions = copy / "config" / "functions.tsv"
    rows = functions.read_text().splitlines()
    functions.write_text("\n".join([rows[0]] + ["%s\tFUN_%08x\t%s" % (r.split("\t")[0], int(r.split("\t")[0], 16),
                                                                       r.split("\t")[2]) for r in rows[1:] if r]) + "\n")
    symbols = copy / "config" / "symbols.txt"
    symbols.write_text("\n".join("f %s FUN_%08x" % (l.split()[1], int(l.split()[1], 16))
                                 if l.split()[:1] == ["f"] else l for l in symbols.read_text().splitlines()) + "\n")
    shutil.rmtree(copy / "tests", ignore_errors=True)    # (its written cases name the functions it no longer has)
    return Target.load(str(copy))


@pytest.fixture
def server(tmp_path):
    target = stripped_copy(tmp_path)
    srv = serving(target)
    yield srv, target
    srv.shutdown()
    srv.server_close()


def run(srv, **body):
    status, r = answer(srv, "/api/names/run", body)
    assert status == 200, r
    done = wait_job(srv, r["id"])
    assert done["state"] == "done", done
    return answer(srv, "/api/names")[1]


def test_samecode_from_the_page_proposes_and_dread_rename_applies(server):
    from conftest import gizmo_dir
    srv, target = server
    g = answer(srv, "/api/gui")[1]
    assert {"name": "names", "script": "names.js", "style": "names.css"} in g["pages"]
    assert request(srv, "/names.js")[0] == 200 and request(srv, "/names.css")[0] == 200
    assert answer(srv, "/api/names")[1]["samecode"] is None
    d = run(srv, tool="samecode", other=gizmo_dir("m3"))
    proposals = d["samecode"]["proposals"]
    assert len(proposals) >= 40 and all(p["here"].startswith("FUN_") and not p["there"].startswith("FUN_")
                                        for p in proposals), proposals
    assert d["other"]["other"] == gizmo_dir("m3")
    chosen = {p["here"]: p["there"] for p in proposals[:3]}
    status, r = answer(srv, "/api/names/apply", {"names": chosen})
    assert status == 200, r
    done = wait_job(srv, r["id"])
    assert done["state"] == "done", done
    from dread.program import Program
    renamed = Target.load(target.root)
    names = {fn.name for fn in Program(renamed, renamed.image()).functions}
    assert set(chosen.values()) <= names and not set(chosen) & names


def test_callpair_names_the_thumb_build_from_the_arm_build_from_the_page(server):
    from conftest import gizmo_dir
    srv, target = server
    d = run(srv, tool="callpair", other=gizmo_dir("a32"), seeds=["FUN_08000738=main"])
    assert d["callpair"]["record"]["seeds"] >= 1
    assert len(d["callpair"]["proposals"]) >= 10, d["callpair"]


def test_whatccs_table_gives_proposals_for_functions_without_a_name(server):
    """A function without a name that matched names not given as ambiguous is offered the first; a named
    one, and one whose names are all ambiguous, are not."""
    srv, target = server
    folder = os.path.join(target.build, "whatcc", target.image().name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "matches.tsv"), "w") as f:
        f.write("entry\tname\tmatched\tambiguous\ttoolchains\n"
                "0x8000100\tFUN_08000100\tmemcpy\t\tubuntu-24.04\n"
                "0x8000200\tstrlen\tstrlen\t\tubuntu-24.04\n"
                "0x8000300\tFUN_08000300\t__errno\t__errno\tubuntu-24.04\n")
    d = answer(srv, "/api/names")[1]
    assert d["whatcc"]["matched"] == 3
    assert d["whatcc"]["proposals"] == [{"entry": 0x8000100, "here": "FUN_08000100", "there": "memcpy",
                                         "matched": ["memcpy"], "toolchains": ["ubuntu-24.04"]}]


def test_the_page_changes_nothing_without_its_token_nor_on_a_read_only_server(server):
    srv, target = server
    status, _, _ = request(srv, "/api/names/run", {"tool": "samecode", "other": "x"}, token=False)
    assert status == 403
    assert answer(srv, "/api/names/run", {"tool": "nm"})[0] == 400
    assert answer(srv, "/api/names/run", {"tool": "samecode"})[0] == 400        # (no other target)
    assert answer(srv, "/api/names/run", {"tool": "callpair", "other": "x", "seeds": ["a b"]})[0] == 400
    assert answer(srv, "/api/names/apply", {"names": {"FUN_08000100": "bad name"}})[0] == 400
    assert answer(srv, "/api/names/apply", {"names": {}})[0] == 400
    viewer = gui.serve(Target.load(target.root), target.image(), port=0, read_only=True)
    import threading
    threading.Thread(target=viewer.serve_forever, daemon=True).start()
    try:
        status, r = answer(viewer, "/api/names/apply", {"names": {"FUN_08000100": "memcpy"}})
        assert status == 403, r
    finally:
        viewer.shutdown()
        viewer.server_close()


def test_the_page_in_a_browser(server):
    """The page shows samecode's proposals as a table with a box each, in a browser, without a script error."""
    sync_api = pytest.importorskip("playwright.sync_api")
    from conftest import gizmo_dir
    srv, target = server
    run(srv, tool="samecode", other=gizmo_dir("m3"))
    errors = []
    with sync_api.sync_playwright() as p:
        path = next((shutil.which(b) for b in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser")
                     if shutil.which(b)), None)
        try:
            browser = p.chromium.launch(executable_path=path, headless=True) if path else p.chromium.launch(headless=True)
        except Exception as e:                          # (Playwright without a browser of its own)
            pytest.skip("no browser for Playwright: %s" % str(e).splitlines()[0])
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.set_default_timeout(60_000)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto("http://127.0.0.1:%d/#/names" % srv.server_address[1])
        page.wait_for_selector("#nm-samecode .nm-table tbody tr")
        rows = page.locator("#nm-samecode .nm-pick").count()
        browser.close()
    assert rows >= 40 and not errors, (rows, errors)
