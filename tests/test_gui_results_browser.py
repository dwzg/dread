"""dread gui's pages of the tools' results in a real browser (headless Chrome or Chromium through Playwright,
where both are installed; skipped otherwise, as on CI): the bench page (a recipe's builds as a table that
sorts and filters, a build's trend and its page with the chart and every figure), the compiler lab page (a
compiler's profile with the results that differ between builds and the probe that failed) and the leak check
page (a check run from the form, its hits with their lines). No script error on the way, no sideways scroll
of the page at a phone's width. The APIs are tested in tests/test_gui_bench.py, test_gui_cclab.py and
test_gui_leakcheck.py either way."""
import json
import os
import shutil
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from dread import bench, gui                              # noqa: E402
from dread.guipages import bench as bench_page            # noqa: E402
from test_bench import recipe, measurement, write_measurements    # noqa: E402
from test_gui_leakcheck import private_project            # noqa: E402

pytestmark = [pytest.mark.browser, pytest.mark.slow]
BROWSERS = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser")
WAIT = 60_000


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        path = next((shutil.which(b) for b in BROWSERS if shutil.which(b)), None)
        try:
            b = p.chromium.launch(executable_path=path, headless=True) if path else p.chromium.launch(headless=True)
        except Exception as e:                          # (Playwright without a browser of its own)
            pytest.skip("no browser for Playwright: %s" % str(e).splitlines()[0])
        yield b
        b.close()


@pytest.fixture
def bare(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("DREAD_BENCH", str(tmp_path / "bench"))
    monkeypatch.delenv("DREAD_TARGET", raising=False)
    srv = gui.serve(None, None, port=0, home=str(home))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, home
    srv.shutdown()
    srv.server_close()


def opened(browser, srv, errors, width=1360):
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.set_default_timeout(WAIT)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    return page, "http://127.0.0.1:%d/" % srv.server_address[1]


def no_sideways_scroll(page):
    w = page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth, "
                      "document.querySelector('main').scrollWidth, document.querySelector('main').clientWidth]")
    assert w[0] <= w[1] and w[2] <= w[3] + 1, w


def test_the_bench_page_in_a_browser(browser, bare, tmp_path, monkeypatch):
    srv, home = bare
    r = recipe(tmp_path)
    second = dict(r["builds"][0], name="m3-O2", cflags=[f if f != "-Os" else "-O2" for f in r["builds"][0]["cflags"]])
    r["builds"].append(second)
    monkeypatch.setattr(bench_page, "load_recipe", lambda name: r if name == "gizmo" else None)
    monkeypatch.setattr(bench_page, "recipe_files", lambda: {"gizmo": r["_file"]})
    folder = os.path.join(str(tmp_path / "bench"), "gizmo")
    write_measurements(os.path.join(folder, "m3-Os"), r, [("20261019-090000", measurement(25, 40)),
                                                          ("20261020-101500", measurement(30, 40))],
                       measurement(33, 40, readable=31))
    o2 = measurement(20, 36)
    o2["build"], o2["cflags"] = "m3-O2", "-mcpu=cortex-m3 -mthumb -O2"
    write_measurements(os.path.join(folder, "m3-O2"), r, [], o2)
    with open(os.path.join(folder, "m3-O2", "build.json")) as f:
        info = json.load(f)
    info.update(build="m3-O2", made_from="0" * 40)
    with open(os.path.join(folder, "m3-O2", "build.json"), "w") as f:
        json.dump(info, f)
    bench.write_json(bench.report_data(r), bench_page.record_path("gizmo"))
    errors = []
    page, url = opened(browser, srv, errors)
    page.goto(url + "#/bench")
    page.wait_for_selector("#bn-table tbody tr")
    assert page.eval_on_selector_all("#bn-table td.c-build a", "els => els.map(e => e.textContent)") == ["m3-O2", "m3-Os"]
    assert "changed" in page.text_content("#bn-table tbody tr:first-child")     # (m3-O2: the recipe changed since)
    page.click('button[data-sort="lifted"]')                                # (by the share lifted, most first)
    page.wait_for_function("() => document.querySelector('#bn-table td.c-build a').textContent === 'm3-Os'")
    assert "82.5 %" in page.text_content("#bn-table tbody tr:first-child")
    assert page.locator("#bn-table tbody tr:first-child svg.bn-spark-svg").count() == 1
    page.select_option('select[data-filter="opt"]', "-O2")
    page.wait_for_function("() => document.querySelectorAll('#bn-table tbody tr').length === 1")
    assert page.text_content("#bn-count") == "1 of 2 builds"
    page.select_option('select[data-filter="opt"]', "")
    page.fill("#bn-text", "O2")
    page.wait_for_function("() => document.querySelectorAll('#bn-table tbody tr').length === 1")
    page.fill("#bn-text", "")
    page.select_option("#bn-trend", "readable")
    page.wait_for_function("() => document.querySelector('#bn-table th.c-trend').textContent.includes('readable')")
    page.goto(url + "#/bench/gizmo/m3-Os")
    page.wait_for_selector("#bn-chart")
    assert page.locator("#bn-chart circle.bn-pt").count() == 2 and page.locator("#bn-chart circle.bn-end").count() == 1
    assert page.is_disabled("#bn-open") and "dread.toml" in page.text_content(".bn-open-why")
    page.hover("#bn-hit", position={"x": 5, "y": 20})
    page.wait_for_function("() => !document.querySelector('#bn-tip').hidden")
    assert "25" in page.text_content("#bn-tip")                             # (the first measurement)
    page.click('button[data-chart="noreturn"]')
    page.wait_for_function("() => document.querySelector('#bn-chart').getAttribute('aria-label').includes('never return')")
    page.wait_for_function("() => document.querySelector('#bn-measure-line').textContent.includes('dread bench measure')")
    assert "--json" in page.text_content("#bn-measure-line") and "About" in page.text_content("#bn-estimate")
    page.keyboard.press("m")
    page.wait_for_url("**/#/bench")
    page.set_viewport_size({"width": 390, "height": 800})
    page.wait_for_selector("#bn-table tbody tr")
    no_sideways_scroll(page)
    page.goto(url + "#/bench/gizmo/m3-Os")
    page.wait_for_selector("#bn-chart")
    no_sideways_scroll(page)
    assert not errors, errors


def profile_record(work):
    """A compiler's report as dread cclab report --json writes it (two builds, a result that differs, a probe
    that failed in one build)."""
    both = ["cortex-m0-Os", "cortex-m3-Os"]
    adapter = {
        "adapter": "gcc-arm", "arch": "arm", "compiler": "arm-none-eabi-gcc (Example) 13.2.1",
        "builds": [{"build": b, "flags": ["-mcpu=" + b[:9], "-Os"], "picked": {}, "measured": True, "error": None} for b in both],
        "measured": both, "failed": {}, "probes": {"pick_i5_5": "abi", "op_udiv": "ops", "spin_forever": "spin"},
        "sections": [
            {"kind": "arg", "title": "Where arguments arrive", "aligned": None, "rows": [
                {"probe": "pick_i5_5", "what": "the 5th of five ints", "results": [{"text": "stack+0", "builds": both}]},
                {"probe": "spin_forever", "what": "a probe that never returns", "results": [
                    {"text": "the run did not end within its limit", "builds": both}]}]},
            {"kind": "op", "title": "Operations: what each calls", "aligned": None, "rows": [
                {"probe": "op_udiv", "what": "", "results": [{"text": "__udivsi3 = __aeabi_uidiv", "builds": ["cortex-m0-Os"]},
                                                              {"text": "inline (udiv)", "builds": ["cortex-m3-Os"]}]}]},
            {"kind": "keeps", "title": "What a function keeps across a call", "aligned": 8, "rows": []}],
        "layout": [{"row": "entries", "what": "where the probes' functions start", "results": [{"text": "multiples of 2", "builds": both}]}],
        "helpers": [{"helper": "__udivsi3 = __aeabi_uidiv", "called_by": ["op_udiv"], "changed": ["r0", "r1", "r2", "r3"],
                     "builds": ["cortex-m0-Os"]}],
        "lift": {"builds": {b: {"accepted": 3, "checked": 3} for b in both}, "errors": {}, "rows": []},
        "failed_probes": [{"probe": "spin_forever", "kind": "arg", "what": "a probe that never returns", "results": [
            {"error": "the run did not end within its limit", "builds": both}]}],
        "profile": {"compiler": {"adapter": "gcc-arm", "version": "arm-none-eabi-gcc (Example) 13.2.1", "builds": both},
                    "helpers": {"__aeabi_uidiv": {"names": ["__aeabi_uidiv", "__udivsi3"], "results": ["r0"],
                                                  "changes": ["r0", "r1", "r2", "r3"], "called_by": ["op_udiv"]}}}}
    os.makedirs(os.path.join(work, "gcc-arm", "cortex-m0-Os"))
    with open(os.path.join(work, "gcc-arm", "version.txt"), "w") as f:
        f.write(adapter["compiler"] + "\n")
    with open(os.path.join(work, "report.json"), "w") as f:
        json.dump({"version": 1, "work": work, "adapters": {"gcc-arm": adapter}}, f)


def test_the_compiler_lab_page_in_a_browser(browser, bare):
    srv, home = bare
    profile_record(str(home / "cclab-work"))
    errors = []
    page, url = opened(browser, srv, errors)
    page.goto(url + "#/cclab")
    page.wait_for_selector(".lab-adapter")
    assert "arm-none-eabi-gcc (Example) 13.2.1" in page.text_content(".lab-adapter")
    page.wait_for_function("() => document.querySelector('#lab-line').textContent.startsWith('dread cclab run --adapter gcc-arm')")
    page.uncheck('#lab-matrix input[data-build="cortex-m0-O0"]')
    page.wait_for_function("() => document.querySelector('#lab-line').textContent.includes('--build cortex-m0-Os')")
    page.click("#lab-none")
    page.wait_for_function("() => document.querySelector('#lab-error').textContent.includes('at least one build')")
    assert page.is_disabled("#lab-go")
    page.click(".lab-adapter")
    page.wait_for_selector("#lab-sections")
    assert "spin_forever" in page.text_content("#lab-s-failed") and "never returns" in page.text_content("#lab-s-failed")
    assert page.locator("#lab-s-op tr.differ").count() == 1
    assert "multiple of 8 bytes" in page.text_content("#lab-s-keeps")
    helpers = page.text_content("#lab-s-helpers")
    assert "__aeabi_uidiv" in helpers and "r0 r1 r2 r3" in helpers
    page.check("#lab-differ")
    page.wait_for_function("() => !document.querySelector('#lab-s-arg')")
    assert page.locator("#lab-s-op").count() == 1
    page.uncheck("#lab-differ")
    page.fill("#lab-filter", "udiv")
    page.wait_for_function("() => !document.querySelector('#lab-s-arg') && !!document.querySelector('#lab-s-op')")
    page.set_viewport_size({"width": 390, "height": 800})
    no_sideways_scroll(page)
    page.goto(url + "#/cclab")
    page.wait_for_selector(".lab-adapter")
    no_sideways_scroll(page)
    assert not errors, errors


def test_the_leak_check_page_in_a_browser(browser, bare):
    srv, home = bare
    private_project(home)
    errors = []
    page, url = opened(browser, srv, errors)
    page.goto(url + "#/leakcheck")
    page.wait_for_selector("#lk-tables")
    page.fill("#lk-tree", "tree")
    page.fill("#lk-tables", "private/functions.tsv\nprivate/symbols.txt")
    page.fill("#lk-words", "private/words.txt")
    page.fill("#lk-allow", "private/allow.txt")
    page.check("#lk-history")
    page.wait_for_function("() => document.querySelector('#lk-line').textContent.includes('--history')")
    page.click("#lk-run")
    page.wait_for_selector("#lk-hits td.lk-match")
    page.wait_for_function("() => document.querySelector('#lk-job-state').textContent.includes('hits not allowed')")
    rows = page.eval_on_selector_all("#lk-hits tbody tr", "els => els.map(e => e.textContent)")
    assert any("src/a.c" in r and "zorblax_widget_init" in r and "2" in r for r in rows), rows
    assert all("0x12400" not in r for r in rows)                            # (allowed: hidden by the filter)
    page.uncheck("#lk-only-bad")
    page.wait_for_function("() => document.querySelector('#lk-hits').textContent.includes('0x12400')")
    assert "gnarvel_task" in page.text_content("main")                      # (the history's hit)
    html = page.content()
    for never in ("quuxify_frobnicator", "vexilla_handler", "canary-words-4711", "canary-allow-4711"):
        assert never not in html, never
    page.set_viewport_size({"width": 390, "height": 800})
    no_sideways_scroll(page)
    page.goto(url + "#/")
    page.keyboard.press("w")
    page.wait_for_url("**/#/leakcheck")
    page.wait_for_selector("#lk-hits")                                      # (the newest check, its form filled in)
    assert page.input_value("#lk-words").endswith("private/words.txt")
    assert not errors, errors
