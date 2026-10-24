"""dread gui's Setup page in a real browser (headless Chrome or Chromium through Playwright, where both are
installed; skipped otherwise, as on CI): a server without a target opens on the page; a target is made from
an ELF by its path, opened, and its first analyses run as a chain (cheap commands standing for them); a
provider is added with a key typed into a password field (the key never comes back to the page, its file
has mode 600) and tried against a stand-in server, and one behind a proxy is edited without losing its url
or its comment; a read-only server's page offers no change. No script
error on the way. The API is tested in tests/test_gui_setup.py either way."""
import http.server
import json
import os
import shutil
import stat
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from conftest import GIZMO                               # noqa: E402
from dread import gui                                   # noqa: E402
from dread.guipages import setup                        # noqa: E402

pytestmark = [pytest.mark.browser, pytest.mark.slow]
BROWSERS = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser")
WAIT = 60_000
KEY = "sk-browser-0123456789abcdefgh"


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


class StandIn(http.server.BaseHTTPRequestHandler):
    """An OpenAI-compatible server that says whether the key came."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        came = self.headers.get("authorization") == "Bearer " + KEY
        body = json.dumps({"choices": [{"message": {"content": "here, the key came: %s" % came}, "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 5, "completion_tokens": 5}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def bare(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "cfg").mkdir(mode=0o700)
    monkeypatch.setenv("DREAD_LLM", str(tmp_path / "cfg" / "llm.toml"))
    srv = gui.serve(None, None, port=0, home=str(home))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, home
    srv.shutdown()
    srv.server_close()


def opened(browser, srv, errors):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.set_default_timeout(WAIT)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    return page, "http://127.0.0.1:%d/" % srv.server_address[1]


def text_has(page, selector, text):
    page.wait_for_function("([s, t]) => { const e = document.querySelector(s); return !!e && e.textContent.includes(t); }",
                           arg=[selector, text])


def test_the_setup_page_in_a_browser(browser, bare, monkeypatch, tmp_path):
    srv, home = bare
    shutil.copy(os.path.join(GIZMO, "m3", "original", "gizmo.elf"), home / "gizmo.elf")
    steps = [{"step": "functions", "command": None, "values": None, "why": "there"},
             {"step": "regflow", "command": "info", "values": {}, "why": "first"},
             {"step": "lift", "command": "functions", "values": {}, "why": "second"}]
    monkeypatch.setattr(setup.newtarget, "first_analyses", lambda target, image=None: [dict(s) for s in steps])
    errors = []
    page, url = opened(browser, srv, errors)
    page.goto(url)
    page.wait_for_url("**/#/setup")                     # (no target: the Setup page)
    page.wait_for_selector("#su-doctor .su-verdict")
    assert "no target yet" in page.text_content("#where")
    page.fill("#su-dir", "fw")
    page.fill("#su-path", "gizmo.elf")
    text_has(page, "#su-inspect", "an ELF for arm")
    assert page.is_hidden("#su-base-row")               # (an ELF says where it lies)
    text_has(page, "#su-new-line", "dread new fw gizmo.elf")
    page.click("#su-make")
    page.wait_for_function("() => { const h = document.querySelector('#su-target-head b'); return !!h && "
                           "h.textContent.startsWith('fw'); }")
    assert "fw (from gizmo.elf)" in page.text_content("#where")
    page.wait_for_selector("#su-chain-start")
    page.click("#su-chain-start")
    page.wait_for_function("() => document.querySelectorAll('#su-chain .su-s-done').length === 3")
    assert "dread info --target ." in page.text_content("#su-chain")
    page.click("#su-prov-new")
    stand_in = http.server.HTTPServer(("127.0.0.1", 0), StandIn)
    thread = threading.Thread(target=stand_in.serve_forever, daemon=True)
    thread.start()
    try:
        page.fill("#sp-name", "standin")
        page.select_option("#sp-kind", "openai")
        page.fill("#sp-model", "m")
        page.fill("#sp-url", "http://127.0.0.1:%d" % stand_in.server_address[1])
        assert page.get_attribute("#sp-key", "type") == "password"
        page.fill("#sp-key", KEY)
        text_has(page, "#sp-line", "--url http://127.0.0.1:%d" % stand_in.server_address[1])
        text_has(page, "#sp-keyline", "dread llm key standin")
        page.click("#sp-save")
        text_has(page, '[data-provider="standin"]', "set")
        assert KEY not in page.content()
        key_file = tmp_path / "cfg" / "keys" / "standin"
        assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
        page.click('[data-test="standin"]')
        text_has(page, '[data-test-of="standin"]', "here, the key came: True")
        assert KEY not in page.content()
        config = tmp_path / "cfg" / "llm.toml"         # (an Anthropic provider behind a proxy, edited: its url stays)
        config.write_text(config.read_text() + '\n[providers.proxy]  # through a proxy\nkind = "anthropic"\n'
                          'url = "https://proxy.example.invalid"\nmodel = "a"\n')
        page.goto(url + "#/")
        page.goto(url + "#/setup")
        page.click('[data-edit="proxy"]')
        assert page.input_value("#sp-url") == "https://proxy.example.invalid"
        page.fill("#sp-model", "b")
        text_has(page, "#sp-line", "--model b proxy")
        page.click("#sp-save")
        text_has(page, '[data-provider="proxy"]', "b")
        text = config.read_text()
        assert 'url = "https://proxy.example.invalid"' in text and 'model = "b"' in text and "# through a proxy" in text
    finally:
        stand_in.shutdown()
        stand_in.server_close()
        thread.join(10)
    page.goto(url + "#/")
    page.wait_for_selector(".ov h1")                    # (the target's overview now)
    page.keyboard.press("u")
    page.wait_for_url("**/#/setup")
    assert not errors, errors


def test_a_read_only_setup_page(browser, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("DREAD_LLM", str(tmp_path / "llm.toml"))
    viewer = gui.serve(None, None, port=0, home=str(tmp_path), read_only=True)
    threading.Thread(target=viewer.serve_forever, daemon=True).start()
    errors = []
    try:
        page, url = opened(browser, viewer, errors)
        page.goto(url + "#/setup")
        page.wait_for_selector("#su-doctor .su-verdict")
        assert "read-only" in page.text_content("#su-blocked")
        assert page.is_disabled("#su-make") and page.is_disabled("#su-open") and page.is_disabled("#su-prov-new")
        assert not errors, errors
    finally:
        viewer.shutdown()
        viewer.server_close()
