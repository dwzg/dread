"""dread gui's pilot page in a real browser (headless Chrome or Chromium through Playwright, where both are
installed: the `browser` extra), with the fake provider of tests/test_gui_pilot.py: the short form makes
the command line and its estimate, Every option hands it to the Run page's form, a run starts from it and
is followed while its Message Batch is out, Stop ends it "stopped", a run to its end shows its names, the
history and the month's costs show both, the key l opens the page, a phone-wide page scrolls nowhere
sideways. No script error on the way. Skipped without Playwright or a browser (CI has neither): the API is
tested in tests/test_gui_pilot.py either way."""
import shutil

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from test_gui_pilot import KEY, NAMES, serving_fake     # noqa: E402

pytestmark = [pytest.mark.browser, pytest.mark.slow]
BROWSERS = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser")
WAIT = 120_000                  # ms a step may take on a busy machine


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
def pilot_page(tmp_path, monkeypatch):
    yield from serving_fake(tmp_path, monkeypatch)


def opened(browser, srv, errors, width=1280):
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.set_default_timeout(WAIT)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    return page, "http://127.0.0.1:%d/" % srv.server_address[1]


def state_is(page, word):
    page.wait_for_function("(w) => (document.querySelector('#pl-run-head .pl-state') || {}).textContent === w", arg=word)


def fill(page, url, budget=""):
    page.goto(url + "#/pilot")
    page.wait_for_selector("#pl-start")
    page.click('#pl-tasks button[data-task="name"]')
    page.select_option("#pl-provider", "fake")
    page.fill("#pl-names", " ".join(NAMES))
    page.fill("#pl-budget", budget)
    page.fill('#pl-task-fields input[data-x="--samples"]', "2")
    page.fill('#pl-task-fields input[data-x="--passes"]', "1")
    page.select_option('#pl-task-fields select[data-x="--subsystems"]', "false")
    page.fill("#pl-batch-n", "1")
    page.wait_for_function("() => document.querySelector('#pl-line').textContent.includes('--no-subsystems')")
    page.wait_for_function("() => { const e = document.querySelector('#pl-estimate'); "
                           "return !e.classList.contains('stale') && e.textContent.length > 0; }")
    page.wait_for_function("() => !document.querySelector('#pl-start-btn').disabled")


def test_the_pilot_page_in_a_browser(browser, pilot_page):
    srv, target, fake = pilot_page
    errors = []
    page, url = opened(browser, srv, errors)
    fill(page, url, budget="0.05")
    line = page.text_content("#pl-line")
    assert line.startswith("dread pilot --target . --provider fake") and "--budget 0.05" in line and "--batch 1" in line
    assert "no estimate" in page.text_content("#pl-estimate") and "5 functions to ask" in page.text_content("#pl-estimate")
    facts = page.text_content("#pl-provider-facts")
    assert "key: there (env DREAD_FAKE_KEY)" in facts and "the Setup page" in facts and KEY not in facts
    page.click("#pl-every")                             # (every option: the Run page's form, filled in)
    page.wait_for_function("(l) => (document.querySelector('#cmdline') || {}).textContent === l", arg=line)
    fill(page, url, budget="0.05")
    page.click("#pl-start-btn")
    page.wait_for_url("**/#/pilot/2*")
    page.wait_for_selector("#pl-batches:not([hidden])")  # (the batch is out: the fake holds it)
    assert "msgbatch_1" in page.text_content("#pl-batches") and "of $0.05" in page.text_content("#pl-cards")
    assert page.is_visible('.nav-btn[data-page="pilot"].on')
    page.click("#pl-stop")
    state_is(page, "stopped")
    assert "ended by SIGTERM" in page.text_content("#pl-banner") and fake.cancelled == ["msgbatch_1"]
    assert "cancelled (interrupted)" in page.text_content("#pl-items")
    fake.release()
    fill(page, url)
    page.click("#pl-start-btn")
    page.wait_for_url("**/#/pilot/2*")
    state_is(page, "done")
    items = page.text_content("#pl-items")
    assert all(n + "_x" in items for n in NAMES) and "proposed" in items
    page.click("#pl-items a.fn >> nth=0")               # (a name's function page)
    page.wait_for_selector(".fn-title h1")
    page.goto(url + "#/")
    page.wait_for_selector(".ov h1")
    page.keyboard.press("l")
    page.wait_for_url("**/#/pilot")
    page.wait_for_selector(".pl-runrow >> nth=1")
    runs = page.text_content("#pl-runs")
    assert "done" in runs and "stopped" in runs and "fake" in runs
    page.wait_for_selector(".pl-usage")
    assert "100 %" in page.text_content("#pl-cost") and "where prices are known" in page.text_content("#pl-cost")
    for box in page.query_selector_all(".pl-runrow input[data-pick]"):
        box.check()
    page.wait_for_selector(".pl-pair-box")
    assert "one set of names" in page.text_content("#pl-pair")
    phone, _ = opened(browser, srv, errors, width=390)
    phone.goto(url + "#/pilot")
    phone.wait_for_selector(".pl-runrow")
    assert phone.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert not errors, errors
