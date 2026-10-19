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
    assert "1: equivalent: <one sentence" in survivors and "gpio_set(1, 1);" in survivors
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


def test_prototypes_and_results_take_the_registers_width():
    """The pilot declares callees with register-wide arguments and a result as wide as their callers
    read: on the MSP430 u16 arguments, a result in r12:r13 u32 and one in r12-r15 u64; on ARM u32.
    (Before, every architecture got ARM's: u32 arguments, u64 when r1 was read.)"""
    from conftest import gizmo_dir
    found, questions = {}, {}
    for arch in ("msp430", "m3"):
        target = Target.load(gizmo_dir(arch))
        image = target.image()
        program = Program(target, image)
        flow = regflow.load(target, image, program)
        found[arch] = {line for fn in program.functions for line in pilot.prototypes(program, flow, fn)}
        questions[arch] = pilot.question(target, image, program, program.by_name["checksum"], flow)[1]
    assert "u64 __mspabi_mpyll(u16 a0, u16 a1, u16 a2, u16 a3);" in found["msp430"]
    assert "u32 checksum(u16 a0, u16 a1, u16 a2);" in found["msp430"]    # (const u8 *p, u32 n): r12, r13:r14
    assert not any("u32 a" in line for line in found["msp430"])
    assert "u32 checksum(u32 a0, u32 a1);" in found["m3"]
    assert "Its callers read r12 and r13 after it: it returns a 32-bit value (u32)." in questions["msp430"]
    assert "Its callers read" not in questions["m3"]


def test_extract_c():
    assert pilot.extract_c("x\n```c\nint f;\n```\n") == "int f;\n"
    assert pilot.extract_c("```\nint f;\n```") is None and pilot.extract_c("") is None


class Fake(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        Fake.seen.append((self.path, dict(self.headers), body))
        reply = ({"choices": [{"message": {"content": "openai says hi", "reasoning_content": "hmm"},
                               "finish_reason": "stop"}], "usage": {"completion_tokens": 12}}
                 if self.path.endswith("/chat/completions")
                 else {"content": [{"type": "thinking", "thinking": "well"}, {"type": "text", "text": "anthropic says hi"}],
                       "stop_reason": "max_tokens", "usage": {"output_tokens": 34, "input_tokens": 50,
                                                               "cache_read_input_tokens": 7}})
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
        assert local.last == {"finish": "stop", "tokens": 12, "reasoning": 3, "input": None}
        github = llm.OpenAICompatible("gh", {"url": url + "/inference/", "path": "/chat/completions", "model": "o/m",
                                             "headers": {"x-github-api-version": "2022-11-28"}})
        assert github.ask("be brief", [{"role": "user", "content": "hello"}]) == "openai says hi"
        path, headers, body = Fake.seen[-1]
        assert path == "/inference/chat/completions" and body["model"] == "o/m"
        assert {k.lower(): v for k, v in headers.items()}["x-github-api-version"] == "2022-11-28"
        with pytest.raises(llm.ProviderError, match="environment"):
            llm.OpenAICompatible("gh", {"url": url, "headers": {"Authorization": "Bearer x"}}).ask("s", [])
        monkeypatch.setenv("TEST_KEY", "secret")
        remote = llm.Anthropic("claude", {"url": url, "model": "c", "key_env": "TEST_KEY"})
        assert remote.ask("be brief", [{"role": "user", "content": "hello"}]) == "anthropic says hi"
        path, headers, body = Fake.seen[-1]
        assert path == "/v1/messages" and body["system"] == "be brief"
        assert remote.last == {"finish": "max_tokens", "tokens": 34, "reasoning": 4, "input": 57}
        assert {k.lower(): v for k, v in headers.items()}["x-api-key"] == "secret"
    finally:
        server.shutdown()


def test_an_input_that_runs_missed_code_becomes_a_written_case(gizmo):
    """--task input: the emulator decides; a useful input is kept in extra_cases, by the pilot."""
    target, image, program, flow = gizmo
    fn = program.by_name["keys_read"]
    provider = llm.Scripted(["no idea", '```json\n{"args": ["0x0", 0, 0, 0], "mem": [], "poll": []}\n```'])
    still = pilot.find_inputs(target, image, program, provider, "keys_read", list(fn.insns), tries=3, flow=flow,
                              out=lambda *a: None)
    assert still == []
    assert "no ```json block" in provider.asked[1][1][-1]["content"]
    cases = json.load(open(os.path.join(image.extra_cases, "keys_read.json")))["cases"]
    assert cases[-1]["by"].startswith("dread pilot") and cases[-1]["why"].startswith("runs 0x")


def test_a_name_proposal_is_collected_not_applied(gizmo):
    target, image, program, flow = gizmo
    provider = llm.Scripted(["name: gpio_write_pin\nwhat: sets or clears one output pin", "name: main\nwhat: taken"])
    assert pilot.propose_name(target, image, program, provider, "gpio_set", flow, out=lambda *a: None) == (
        "gpio_write_pin", "sets or clears one output pin")
    assert pilot.propose_name(target, image, program, provider, "gpio_set", flow, out=lambda *a: None) is None
    rows = open(os.path.join(target.build, "pilot", image.name, "names.tsv")).read().splitlines()
    assert rows[-1].split("\t")[1:3] == ["gpio_set", "gpio_write_pin"] and "gpio_set" in program.by_name


@pytest.mark.slow
@needs_gcc
def test_an_answer_cut_off_by_the_token_limit_gets_its_own_feedback(gizmo):
    """A reasoning model that thinks until the limit returns no answer: the tools say so and ask for
    the code first, and the survivors question still gets an answer of its own on the last try."""
    cut = {"text": "", "finish": "length", "tokens": 32000, "reasoning": 98000}
    (outcome, answers, stage), provider, lines = run(gizmo, [cut, RIGHT, ARGUE], tries=2)
    assert (outcome, answers, stage) == ("accepted", 3, "argued"), lines
    second = provider.asked[1][1][-1]["content"]
    assert "ran out of room (32000 tokens, 98000 characters of reasoning)" in second
    target, image, _, _ = gizmo
    log = open(os.path.join(target.build, "pilot", image.name, "led_on.md")).read()
    assert "## Answer 1 (finish: length, 32000 tokens, 98000 characters of reasoning)" in log


@needs_gcc
def test_an_argument_with_the_templates_n_counts_when_one_change_is_asked(gizmo):
    """deepseek-flash wrote "N: equivalent: ..." as the old template stood; with one survivor the N
    can only be 1, so it counts (the template shows the real numbers now)."""
    (outcome, answers, stage), provider, lines = run(gizmo, [RIGHT, "N: " + ARGUE.split(": ", 1)[1]])
    assert (outcome, answers, stage) == ("accepted", 2, "argued"), lines


@needs_gcc
def test_a_refusal_ends_the_function_at_once(gizmo):
    """A provider that declines (Anthropic's stop reason "refusal", OpenAI's "content_filter") gives
    the same answer to the same question: no second try, and the outcome says who declined."""
    refused = {"text": "", "finish": "refusal", "tokens": 0}
    (outcome, answers, stage), provider, lines = run(gizmo, [refused, RIGHT], tries=3)
    assert (outcome, answers, stage) == ("refused", 1, "provider refusal"), lines
    assert len(provider.asked) == 1
    target, image, _, _ = gizmo
    results = open(os.path.join(target.build, "pilot", image.name, "results.tsv")).read()
    assert "led_on\trefused\t1\tprovider refusal" in results


def test_the_providers_say_how_an_answer_ended():
    scripted = llm.Scripted([{"text": "x", "finish": "length", "tokens": 7}, "y"])
    assert scripted.ask("s", []) == "x" and scripted.last == {"finish": "length", "tokens": 7, "reasoning": 0}
    assert scripted.ask("s", []) == "y" and scripted.last["finish"] == "stop"


@pytest.mark.slow
@needs_gcc
def test_a_correction_that_keeps_the_survivor_is_asked_for_the_argument(gizmo):
    """The local model answered the survivors question with new code three times, each with the same
    survivor: the second time the tools say that the code is not the reason and ask for the argument."""
    (outcome, answers, stage), provider, lines = run(gizmo, [RIGHT, RIGHT, ARGUE], tries=3)
    assert (outcome, answers, stage) == ("accepted", 3, "argued"), lines
    assert "1: equivalent: <one sentence" in provider.asked[1][1][-1]["content"]
    assert "leaves the same change unnoticed as before" in provider.asked[2][1][-1]["content"]


def test_the_usage_ledger(tmp_path, monkeypatch):
    """Every answer adds its tokens to the ledger; the month's summary sums them per provider and
    prices them where the configuration gives prices."""
    ledger, config = tmp_path / "usage.tsv", tmp_path / "llm.toml"
    config.write_text('[providers.cloud]\nkind = "anthropic"\nmodel = "m"\nprice_in = 3\nprice_out = 15\n')
    monkeypatch.setattr(llm, "USAGE", str(ledger))
    monkeypatch.setenv("DREAD_LLM", str(config))
    p = llm.Provider("cloud", {"model": "m"})
    for i in range(2):
        p.last = {"finish": "end_turn", "tokens": 1000, "input": 500000}
        p.record()
    other = llm.Provider("local", {"model": "q"})
    other.last = {"finish": "stop", "tokens": 10, "input": 20}
    other.record()
    summary = llm.usage_summary()
    assert summary["cloud"]["answers"] == 2 and summary["cloud"]["input"] == 1000000
    assert abs(summary["cloud"]["cost"] - (3.0 + 0.03)) < 1e-9
    assert summary["local"]["cost"] is None and summary["local"]["output"] == 10
    assert "\tcloud\tm\t500000\t1000\tend_turn" in ledger.read_text()


def test_inputs_for_what_the_lift_check_left_uncovered(tmp_path):
    """--task input also asks for the functions the last lift check dropped as UNCOVERED, with the
    instructions its verdicts list; a function that never returns is left out (its cases are loop
    cases, which an input of the plain form cannot make)."""
    import shutil
    from conftest import gizmo_dir
    from dread.program import Program
    from dread.target import Target
    copy = tmp_path / "m3"
    shutil.copytree(gizmo_dir("m3"), copy, ignore=shutil.ignore_patterns("build"))
    t = Target.load(str(copy))
    image = t.image()
    p = Program(t, image)
    kv, main = p.by_name["parse_kv"], p.by_name["main"]
    folder = os.path.join(t.build, "lift-check", image.name)
    os.makedirs(folder)
    with open(os.path.join(folder, "verdicts.tsv"), "w") as f:
        f.write("function\tverdict\treason\tmissed\n")
        f.write("parse_kv\tdropped\tUNCOVERED parse_kv: 2 of 40 instructions\t0x%x 0x%x\n" % (kv.insns[3], kv.insns[4]))
        f.write("main\tdropped\tUNCOVERED main: 1 of 30\t0x%x\n" % main.insns[2])
        f.write("fmt_dec\taccepted\t\t\n")
    assert pilot.lift_missed(t, image, p) == {"parse_kv": [kv.insns[3], kv.insns[4]]}
