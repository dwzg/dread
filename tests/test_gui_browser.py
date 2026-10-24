"""dread gui's pages in a real browser: headless Chrome or Chromium through Playwright, where both are
installed (pip install -e '.[browser]', and a Chrome, a Chromium or Playwright's own). The Run page's
form makes the command line the server makes (a task's options shown for that task, a subcommand's
form, a refusal that disables Run), a job runs from it and its log and state show, a running job is
stopped from its page, a job's form is filled from its command line, the key x opens the page; a
read-only server's page offers no Run. No script error on the way. Skipped without Playwright or a
browser (CI has neither): the API is tested in tests/test_gui_run.py either way."""
import shutil
import sys
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from dread import gui                                   # noqa: E402
from dread.target import Target                         # noqa: E402
from test_gui import gizmo_copy, serving                 # noqa: E402

pytestmark = [pytest.mark.browser, pytest.mark.slow]
BROWSERS = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser")
WAIT = 60_000                   # ms a step may take on a busy machine


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
def server(tmp_path):
    target = gizmo_copy(tmp_path)
    srv = serving(target)
    yield srv, target
    srv.shutdown()
    srv.server_close()


def opened(browser, srv, errors):
    page = browser.new_page(viewport={"width": 1280, "height": 860})
    page.set_default_timeout(WAIT)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    return page, "http://127.0.0.1:%d/" % srv.server_address[1]


def text_is(page, selector, text):
    page.wait_for_function("([s, t]) => { const e = document.querySelector(s); return !!e && e.textContent === t; }",
                           arg=[selector, text])


def text_has(page, selector, text):
    page.wait_for_function("([s, t]) => { const e = document.querySelector(s); return !!e && e.textContent.includes(t); }",
                           arg=[selector, text])


def line_is(page, text):
    text_is(page, "#cmdline", text)


def test_the_run_page_in_a_browser(browser, server):
    srv, target = server
    errors = []
    page, url = opened(browser, srv, errors)
    page.goto(url + "#/run/info")
    line_is(page, "dread info --target .")
    page.click("#run-btn")
    page.wait_for_url("**/#/jobs/1")
    text_is(page, "#job-head .state", "done")
    text_has(page, "#job-log", "gizmo-m3")
    assert "exit status 0" in page.text_content("#job-head")
    page.goto(url + "#/run/pilot")
    page.wait_for_selector('#fields select[data-id="--task"]')
    assert page.is_hidden('#fields .field[data-row="--samples"]')       # (a --task name option)
    page.select_option('#fields select[data-id="--task"]', "name")
    page.wait_for_selector('#fields .field[data-row="--samples"]', state="visible")
    page.fill('#fields input[data-id="--samples"]', "5")
    line_is(page, "dread pilot --target . --samples 5 --task name")
    page.fill('#fields input[data-id="--tries"]', "x")
    text_has(page, "#cmd-error", "invalid int value")
    assert page.is_disabled("#run-btn")
    page.goto(url + "#/run/llm")
    page.click('#fields button[data-sub="ask"]')
    page.fill('#fields .subform input[data-id="text"]', "hello there")
    line_is(page, "dread llm ask hello there")
    page.goto(url + "#/run/info@1")                     # (job 1's command line in its form)
    page.wait_for_function("() => { const e = document.querySelector('#fields input[data-id=\"--target\"]'); "
                           "return !!e && e.value === '.'; }")
    page.goto(url + "#/")
    page.wait_for_selector(".ov h1")
    page.keyboard.press("x")
    page.wait_for_url("**/#/run")
    page.wait_for_selector("#cmd-list .cmd")
    assert page.is_visible('.nav-btn[data-page="run"].on')
    assert not errors, errors


def test_a_running_job_stopped_from_its_page(browser, server):
    srv, target = server
    errors = []
    r = srv.jobs.start([sys.executable, "-c", "import time; print('up', flush=True); time.sleep(60)"], target.root)
    page, url = opened(browser, srv, errors)
    page.goto(url + "#/jobs/%d" % r["id"])
    text_has(page, "#job-log", "up")
    assert page.text_content("#job-head .state") == "running"
    page.click("#job-stop")
    text_is(page, "#job-head .state", "stopped")
    assert "stopped from the page" in page.text_content("#job-head")
    page.goto(url + "#/jobs")
    page.wait_for_selector(".jobrow")
    assert page.locator(".jobrow").count() == 1
    assert not errors, errors


def test_a_read_only_page(browser, server):
    srv, target = server
    errors = []
    viewer = gui.serve(Target.load(target.root), target.image(), port=0, read_only=True)
    threading.Thread(target=viewer.serve_forever, daemon=True).start()
    try:
        page, url = opened(browser, viewer, errors)
        page.goto(url + "#/run/info")
        line_is(page, "dread info --target .")
        assert page.locator("#run-btn").count() == 0 and "read-only" in page.text_content(".run-actions")
        page.goto(url + "#/f/clamp")
        page.wait_for_selector(".fn-title h1")
        assert page.is_hidden("#rename-btn")
        assert not errors, errors
    finally:
        viewer.shutdown()
        viewer.server_close()


def test_the_views_of_a_function_in_a_browser(browser, tmp_path):
    """The function page's views on a copy of gizmo whose parse_uint is written wrong (tests/
    test_gui_views.py): the strip says its cases fail; the trace view opened by its address traces the
    first failing case as a job at once and shows where the runs part, marked in the listing; another
    case traced by its chip; the registers and the mutation views open from the strip and the address
    keeps the view; at phone width nothing scrolls sideways. No script error."""
    from dread import guipages
    if "views" not in guipages.load():
        pytest.skip("no views page")
    from test_gui_views import planted_copy
    if not shutil.which("arm-none-eabi-gcc"):
        pytest.skip("needs arm-none-eabi-gcc")
    target = planted_copy(tmp_path, lift_check=False)
    srv = serving(target)
    errors = []
    try:
        page, url = opened(browser, srv, errors)
        page.goto(url + "#/f/parse_uint/trace")
        text_has(page, '.vw-tab[data-view="trace"] .vw-sum', "failing")
        page.wait_for_selector(".vw-div", timeout=WAIT)          # (traced at once: the job's record)
        text_has(page, ".vw-div h3", "first divergence")
        assert page.locator("#listing .ln.vw-at").count() >= 1
        page.click(".vw-case:nth-child(2)")                     # (another case: traced by its chip)
        page.wait_for_function("() => [...document.querySelectorAll('.vw-case')].filter((c) => "
                               "c.textContent.includes('parts')).length >= 2", timeout=WAIT)
        page.click('.vw-tab[data-view="registers"]')
        page.wait_for_selector(".vw-facts")
        text_has(page, "#fn-view", "reads at its entry")
        assert page.evaluate("location.hash") == "#/f/parse_uint/registers"
        page.click('.vw-tab[data-view="mutation"]')
        page.wait_for_selector('.vw-run[data-view="mutation"]')
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(url + "#/f/parse_uint/trace")
        page.wait_for_selector(".vw-div")
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        assert not errors, errors
    finally:
        srv.shutdown()
        srv.server_close()
