"""dread pilot with a scripted provider on a copy of gizmo: an answer the tools accept stays, a wrong
one goes back with the tools' own words and the corrected one is accepted, one that never gets right
is removed again; and the providers' wire formats against a local server."""
import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from dread import llm, pilot, regflow
from dread.program import Program
from dread.target import Target
from conftest import GIZMO, gizmo_dir, have

needs_gcc = pytest.mark.skipif(not have("arm-none-eabi-gcc"), reason="needs arm-none-eabi-gcc")

RIGHT = 'Here it is.\n\n```c\n#include "dread.h"\nu32 gpio_set(u32 a0, u32 a1);\n\nvoid led_on(void)\n{\n    gpio_set(1, 1);\n}\n```\n'
WRONG = RIGHT.replace("gpio_set(1, 1)", "gpio_set(2, 1)")
ARGUE = "1: equivalent: gpio_set only tests its second argument for non-zero, so 1 and 2 switch the pin on alike"


@pytest.fixture
def gizmo(tmp_path):
    copy = tmp_path / "gizmo"
    shutil.copytree(GIZMO, copy, ignore=shutil.ignore_patterns("build"))
    for cache in ("autocases", "regflow"):
        here = os.path.join(gizmo_dir("m3"), "build", cache)
        if os.path.isdir(here):
            shutil.copytree(here, copy / "m3" / "build" / cache)
    target = Target.load(str(copy / "m3"))
    image = target.image()
    program = Program(target, image)
    return target, image, program, regflow.load(target, image, program)


def run(gizmo, answers, tries=2):
    target, image, program, flow = gizmo
    provider = llm.Scripted(list(answers))
    lines = []
    result = pilot.pilot_one(target, image, program, provider, "led_on", tries=tries, flow=flow, out=lines.append,
                             jobs=4)
    return result, provider, lines


@pytest.mark.slow
@needs_gcc
def test_an_answer_the_tools_accept_stays(gizmo):
    (outcome, answers, stage), provider, lines = run(gizmo, [RIGHT, ARGUE])
    target, image, _, _ = gizmo
    assert (outcome, answers, stage) == ("accepted", 2, "argued"), lines
    survivors = provider.asked[1][1][-1]["content"]         # the survivors question: gpio_set(1, 1) -> (1, 2)
    assert "N: equivalent:" in survivors and "gpio_set(1, 1);" in survivors
    from dread import mutate
    argued = open(mutate.argued_path(image)).read()
    assert "led_on\tgpio_set(1, 1);\t1 -> 2\targued by dread pilot (scripted (scripted)), not reviewed:" in argued
    text = open(os.path.join(image.src, "pilot", "led_on.c")).read()
    assert "written by dread pilot" in text and "gpio_set(1, 1)" in text
    assert 'auto("led_on")' in open(os.path.join(image.cases, "pilot.py")).read()


@pytest.mark.slow
@needs_gcc
def test_a_wrong_answer_goes_back_with_the_tools_words(gizmo):
    (outcome, answers, stage), provider, lines = run(gizmo, [WRONG, RIGHT, ARGUE], tries=3)
    assert (outcome, answers) == ("accepted", 3), lines
    feedback = provider.asked[1][1][-1]["content"]          # what the model got after the wrong answer
    assert "differs from the machine code" in feedback and "FAIL led_on" in feedback


@pytest.mark.slow
@needs_gcc
def test_an_answer_that_never_gets_right_is_removed(gizmo):
    (outcome, answers, stage), provider, lines = run(gizmo, ["I would rather not.", WRONG])
    target, image, _, _ = gizmo
    assert (outcome, answers, stage) == ("rejected", 2, "test")
    assert "no ```c block" in provider.asked[1][1][-1]["content"]
    assert not os.path.exists(os.path.join(image.src, "pilot", "led_on.c"))
    assert 'auto("led_on")' not in open(os.path.join(image.cases, "pilot.py")).read()
    folder = os.path.join(target.build, "pilot", image.name)
    assert "## Outcome\n\nrejected" in open(os.path.join(folder, "led_on.md")).read()
    assert open(os.path.join(folder, "results.tsv")).read().splitlines()[-1].startswith("led_on\trejected\t2\ttest")


def test_extract_c():
    assert pilot.extract_c("x\n```c\nint f;\n```\n") == "int f;\n"
    assert pilot.extract_c("```\nint f;\n```") is None and pilot.extract_c("") is None


class Fake(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        Fake.seen.append((self.path, dict(self.headers), body))
        reply = ({"choices": [{"message": {"content": "openai says hi"}}]} if self.path == "/v1/chat/completions"
                 else {"content": [{"type": "text", "text": "anthropic says hi"}]})
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def test_provider_wire_formats(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % server.server_port
    try:
        local = llm.OpenAICompatible("local", {"url": url, "model": "m"})
        assert local.ask("be brief", [{"role": "user", "content": "hello"}]) == "openai says hi"
        path, headers, body = Fake.seen[-1]
        assert body["messages"][0] == {"role": "system", "content": "be brief"} and body["model"] == "m"
        monkeypatch.setenv("TEST_KEY", "secret")
        remote = llm.Anthropic("claude", {"url": url, "model": "c", "key_env": "TEST_KEY"})
        assert remote.ask("be brief", [{"role": "user", "content": "hello"}]) == "anthropic says hi"
        path, headers, body = Fake.seen[-1]
        assert path == "/v1/messages" and body["system"] == "be brief"
        assert {k.lower(): v for k, v in headers.items()}["x-api-key"] == "secret"
    finally:
        server.shutdown()
