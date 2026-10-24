"""dread pilot with a scripted provider on a copy of gizmo: an answer the tools accept stays, a wrong
one goes back with the tools' own words and the corrected one is accepted, one that never gets right
is removed again; and the providers' wire formats against a local server."""
import json
import os
import re
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
        assert body["temperature"] == 0.2                       # (the configuration's, by default)
        assert local.last == {"finish": "stop", "tokens": 12, "reasoning": 3, "input": None, "cost": None}
        github = llm.OpenAICompatible("gh", {"url": url + "/inference/", "path": "/chat/completions", "model": "o/m",
                                             "headers": {"x-github-api-version": "2022-11-28"}})
        assert github.ask("be brief", [{"role": "user", "content": "hello"}]) == "openai says hi"
        path, headers, body = Fake.seen[-1]
        assert path == "/inference/chat/completions" and body["model"] == "o/m"
        assert {k.lower(): v for k, v in headers.items()}["x-github-api-version"] == "2022-11-28"
        with pytest.raises(llm.ProviderError, match="environment"):
            llm.OpenAICompatible("gh", {"url": url, "headers": {"Authorization": "Bearer x"}}).ask("s", [])
        local.ask("be brief", [{"role": "user", "content": "hello"}], temperature=0.9)
        assert Fake.seen[-1][2]["temperature"] == 0.9            # (a question's own, for a vote's samples)
        monkeypatch.setenv("TEST_KEY", "secret")
        remote = llm.Anthropic("claude", {"url": url, "model": "c", "key_env": "TEST_KEY"})
        assert remote.ask("be brief", [{"role": "user", "content": "hello"}]) == "anthropic says hi"
        path, headers, body = Fake.seen[-1]
        assert path == "/v1/messages" and body["system"] == "be brief"
        assert "temperature" not in body                        # (Haiku 5.5 and Sonnet 5.5 refuse all but 1.0)
        remote.ask("be brief", [{"role": "user", "content": "hello"}], temperature=1.0)
        assert Fake.seen[-1][2]["temperature"] == 1.0
        assert remote.last == {"finish": "max_tokens", "tokens": 34, "reasoning": 4, "input": 57, "cache_read": 7,
                               "cache_write": 0, "cache_write_1h": 0, "cost": None}     # (no prices: no cost)
        assert {k.lower(): v for k, v in headers.items()}["x-api-key"] == "secret"
        parts = [{"text": "the sources", "cache": True}, {"text": "the names"}]
        remote.ask("be brief", [{"role": "user", "content": parts}])     # (a part to cache: Anthropic's block;
        assert Fake.seen[-1][2]["messages"][0]["content"] == [          # the API joins blocks as they are, so
            {"type": "text", "text": "the sources", "cache_control": {"type": "ephemeral"}},    # the blank line
            {"type": "text", "text": "\n\nthe names"}]               # content_text joins them with is sent)
        local.ask("be brief", [{"role": "user", "content": parts}])      # (one text for the others)
        assert Fake.seen[-1][2]["messages"][1]["content"] == "the sources\n\nthe names"
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


def test_a_name_question_shows_readable_c_with_texts_or_the_listing(gizmo):
    """The question shows a function's readable C, a constant that points to text followed by the
    text, other functions by the run's proposals; a function that does not lift, its listing."""
    from dread import lift
    target, image, program, flow = gizmo
    namer = pilot.Namer(target, image, program, flow)
    text, form = namer.question(program.by_name["apply_config"], {"parse_kv": "split_key_value"})
    assert form.startswith("readable") and "```c" in text and '0x80007e4u /* "volume" */' in text
    assert "split_key_value(" in text and "parse_kv(" not in text and "It is called by: " in text

    def refuse(fn):
        raise lift.Refused("not today")
    namer.lifter.lift = refuse
    text, form = namer.question(program.by_name["apply_config"])
    assert form == "listing" and "machine code (no C could be made)" in text and '"volume"' in text


def test_callees_first_puts_functions_that_call_each_other_in_one_round(monkeypatch):
    import types
    from dread import lift
    calls = {"main": {"a", "b"}, "a": {"leaf"}, "b": {"c"}, "c": {"b", "leaf"}, "leaf": set()}
    monkeypatch.setattr(lift, "direct_callees", lambda program, fn: calls[fn])
    program = types.SimpleNamespace(by_name={n: n for n in calls})
    assert pilot.callees_first(program, ["main", "a", "b", "c", "leaf"]) == [["leaf"], ["a", "b", "c"], ["main"]]
    assert pilot.callees_first(program, ["main", "c"]) == [["main", "c"]]   # (main reaches c only through b)
    assert pilot.callees_first(program, ["main", "b"]) == [["b"], ["main"]]


def test_names_are_asked_callees_first_with_the_proposals_so_far(gizmo):
    """name_functions: led_on calls gpio_set, so gpio_set is asked first and led_on's question shows its
    proposal; a run started again asks only what has no proposal yet; --score compares with the ELF."""
    target, image, program, flow = gizmo
    seen = []

    def answer(messages):
        question = messages[0]["content"]
        name = re.search(r"The function (\w+)", question).group(1)
        seen.append((name, question))
        return {"gpio_set": "name: pin_write\nwhat: drives an output pin",
                "led_on": "name: led_on\nwhat: taken", "delay": "name: busy_wait\nwhat: spins"}[name] \
            if len(messages) == 1 else "name: led_switch_on\nwhat: switches the LED on"
    made, asked, spent = pilot.name_functions(target, image, program, lambda: llm.Scripted(answer),
                                              ["led_on", "gpio_set"], flow, jobs=2, out=lambda *a: None)
    assert (made, asked) == (2, 2)
    assert [n for n, _ in seen] == ["gpio_set", "led_on", "led_on"]     # (led_on is a name taken: asked again)
    assert "pin_write(" in seen[1][1] and "gpio_set(" not in seen[1][1]
    rows = [r.split("\t") for r in open(pilot.names_path(target, image)).read().splitlines()]
    assert rows[0] == list(pilot.NAMES_COLUMNS)         # (the first six as before 2026-10-22, more behind them)
    assert rows[0][:6] == ["address", "now", "proposed", "what", "provider", "shown"]
    assert [r[1:3] for r in rows[1:]] == [["gpio_set", "pin_write"], ["led_on", "led_switch_on"]]
    seen.clear()
    made, asked, _ = pilot.name_functions(target, image, program, lambda: llm.Scripted(answer),
                                          ["led_on", "gpio_set", "delay"], flow, out=lambda *a: None)
    assert (made, asked) == (1, 1) and [n for n, _ in seen] == ["delay"]
    lines = []
    totals = pilot.score_names(target, image, os.path.join(os.path.dirname(target.root), "m3", "original",
                                                           "gizmo.elf"), out=lines.append)
    assert totals["functions"] == 3 and totals["exact"] == 0 and totals["same_words"] == 0
    assert "3 proposals with a true name" in lines[0]
    judge = llm.Scripted(['```json\n{"1": "same", "2": "Close", "3": "maybe"}\n```'])
    lines.clear()
    totals = pilot.score_names(target, image, os.path.join(os.path.dirname(target.root), "m3", "original",
                                                           "gizmo.elf"), out=lines.append, judge=judge)
    assert (totals["judged"], totals["judged_same"], totals["judged_close"]) == (2, 1, 1)
    assert "| " in judge.asked[0][1][0]["content"] and "judged by scripted" in lines[-1]


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


def test_a_run_stops_asking_a_provider_that_keeps_declining(gizmo):
    """Three questions declined in a row end the run (a provider's classifiers decline the same kind of
    question again); an answer in between starts the count anew; the summary says what was declined."""
    target, image, program, flow = gizmo
    refused = {"text": "", "finish": "refusal", "tokens": 0}
    provider = llm.Scripted([refused, "no idea", refused, refused, refused, "never asked"])
    names = [fn.name for fn in program.functions if len(fn.insns) > 4][:6]
    asked = [(name, [program.by_name[name].insns[2]]) for name in names]
    lines = []
    line = pilot.input_task(target, image, program, provider, asked, tries=1, flow=flow, out=lines.append)
    assert len(provider.asked) == 5 and provider.answers == ["never asked"]
    assert line == "5 functions of 6, 0 with every missed instruction run, 0 of 5 missed instructions run, " \
                   "4 declined by the provider", line
    assert sum("declined 3 questions in a row" in x for x in lines) == 1, lines
    asking = pilot.Asking(llm.Scripted([]), out=lines.append)
    assert asking.go_on() and asking.provider.last == {}    # a provider error leaves no finish: no refusal
    asking.asked()
    assert (asking.row, asking.declined, asking.done) == (0, 0, 1)


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
    prices them where the configuration gives prices, each line as it came: an answer of a Message
    Batch (mode batch) at the batch rate, tokens written to the prompt cache for an hour at their price;
    lines of an older ledger (six columns, eight with the cache's) are read as before."""
    import time
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
    assert ledger.read_text().splitlines()[0].split("\t") == list(llm.USAGE_COLUMNS)
    priced = llm.Provider("cloud", {"model": "m", "price_in": 3, "price_out": 15})
    priced.last = {"finish": "end_turn", "tokens": 1000, "input": 500000}
    priced.record()                     # its own count, not the ledger's (runs beside it share that)
    assert priced.spent() == {"answers": 1, "input": 500000, "output": 1000, "cache_read": 0, "cache_write": 0,
                              "batch": 0, "cost": 1.5 + 0.015}
    assert p.spent()["answers"] == 2 and p.spent()["cost"] is None
    assert abs(summary["cloud"]["cost"] - (3.0 + 0.03)) < 1e-9
    assert summary["local"]["cost"] is None and summary["local"]["output"] == 10
    assert "\tcloud\tm\t500000\t1000\tend_turn\t0\t0\t\t0\n" in ledger.read_text()
    cached = llm.Anthropic("cloud", {"model": "m", "price_in": 3, "price_out": 15})    # (a prompt cache's tokens:
    cached.last = {"finish": "end_turn", "tokens": 0, "input": 1000000, "cache_read": 800000, "cache_write": 100000}
    cached.record()                     # read at a tenth of the input price, written at a quarter more)
    assert cached.spent()["cost"] == pytest.approx((100000 * 3 + 800000 * 0.3 + 100000 * 3.75) / 1e6)
    assert "\tcloud\tm\t1000000\t0\tend_turn\t800000\t100000\t\t0\n" in ledger.read_text()
    assert llm.usage_summary()["cloud"]["cost"] == pytest.approx(     # (three answers as before, the cached one)
        (1500000 * 3 + 3000 * 15 + 100000 * 3 + 800000 * 0.3 + 100000 * 3.75) / 1e6)
    batch = llm.Anthropic("cloud", {"model": "m", "price_in": 3, "price_out": 15})   # (a batch's answer: half the
    batch.last = {"finish": "end_turn", "tokens": 2000, "input": 600000, "cache_read": 0, "cache_write": 200000,
                  "cache_write_1h": 50000}                      # price, its hour-long cache writes at twice the input)
    batch.record("batch")
    one = (400000 * 3 + 150000 * 3.75 + 50000 * 6 + 2000 * 15) / 1e6 * 0.5
    assert batch.spent() == {"answers": 1, "input": 600000, "output": 2000, "cache_read": 0, "cache_write": 200000,
                             "batch": 1, "cost": pytest.approx(one)}
    assert "\tcloud\tm\t600000\t2000\tend_turn\t0\t200000\tbatch\t50000\n" in ledger.read_text()
    now = llm.usage_summary()["cloud"]
    assert now["batch"] == 1 and now["answers"] == 5 and now["cost"] == pytest.approx(
        (1500000 * 3 + 3000 * 15 + 100000 * 3 + 800000 * 0.3 + 100000 * 3.75) / 1e6 + one)
    month = time.strftime("%Y-%m")      # (lines of older ledgers: six columns, then eight)
    with open(ledger, "a") as f:
        f.write("%s-01T00:00:00\tcloud\tm\t1000\t100\tend_turn\n" % month)
        f.write("%s-01T00:00:01\tcloud\tm\t1000\t100\tend_turn\t500\t0\n" % month)
    later = llm.usage_summary()["cloud"]
    assert later["answers"] == 7 and later["cache_read"] == 800500 and later["cost"] == pytest.approx(
        now["cost"] + (1000 * 3 + 100 * 15) / 1e6 + (500 * 3 + 500 * 0.3 + 100 * 15) / 1e6)
    discount = tmp_path / "other.toml"  # (a price list's own batch rate)
    discount.write_text('[providers.cloud]\nkind = "anthropic"\nmodel = "m"\nprice_in = 3\nprice_out = 15\n'
                        'price_batch = 0.25\n')
    assert llm.usage_summary(config_path=str(discount))["cloud"]["cost"] == pytest.approx(later["cost"] - one / 2)


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


@needs_gcc
def test_an_input_that_points_a_callback_into_a_function_is_refused(tmp_path):
    """A callback pointed into the middle of a function runs code the rebuilt image does not have
    there (deepseek-flash once gave _fwalk_sglue a callback into itself): the tools refuse it and
    ask again; the function's entry is kept."""
    import subprocess
    from test_bench import TARGET
    from dread import elfimport
    (tmp_path / "t.c").write_text(r'''
__attribute__((noinline)) int twice(int x) { return x + x + 3; }
__attribute__((noinline)) int apply(int (*f)(int), int x) { if (!f) return -1; return f(x) * 5; }
__attribute__((naked, noreturn)) void reset(void) { for (;;) ; }
__attribute__((section(".vectors"), used)) void (*const vectors[2])(void) = {(void (*)(void))0x20005000, reset};
''')
    (tmp_path / "t.ld").write_text("MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K\n"
                                   " RAM (rw) : ORIGIN = 0x20000000, LENGTH = 16K }\n"
                                   "SECTIONS { .text : { KEEP(*(.vectors)) *(.text*) *(.rodata*) } > FLASH }\n")
    elf = str(tmp_path / "t.elf")
    subprocess.run(["arm-none-eabi-gcc", "-mcpu=cortex-m3", "-mthumb", "-O2", "-nostdlib", "-ffunction-sections",
                    "-T", str(tmp_path / "t.ld"), "-e", "reset", "-o", elf, str(tmp_path / "t.c")], check=True)
    (tmp_path / "original").mkdir()
    (tmp_path / "dread.toml").write_text(TARGET + '''
[image.main]
original = "original/t.bin"
base = 0x08000000
entry = "reset"

[compiler]
cross = "arm-none-eabi-"
''')
    assert elfimport.main(["--target", str(tmp_path), "--payload", str(tmp_path / "original" / "t.bin"), elf]) == 0
    t = Target.load(str(tmp_path))
    image = t.image()
    program = Program(t, image)
    twice, apply = program.by_name["twice"], program.by_name["apply"]
    inside = twice.insns[1]
    answer = '```json\n{"args": ["0x%x", 4, 0, 0], "mem": [], "poll": []}\n```'
    provider = llm.Scripted([answer % (inside | 1), answer % (twice.entry | 1)])
    still = pilot.find_inputs(t, image, program, provider, "apply", list(apply.insns), tries=2,
                              flow=regflow.load(t, image, program), out=lambda *a: None)
    first = provider.asked[1][1][-1]["content"]
    assert "call 0x%x through a pointer it gave: inside twice, not a function's entry" % inside in first, first
    cases = json.load(open(os.path.join(image.extra_cases, "apply.json")))["cases"]
    assert len(cases) == 1 and int(str(cases[0]["args"][0]), 0) == twice.entry | 1, cases
    assert len(still) < len(apply.insns)


def test_a_dry_run_of_the_input_task_asks_nothing(gizmo, tmp_path, monkeypatch, capsys):
    """--dry-run asks no provider (none is needed) for --task input: it lists the functions with
    instructions no input ran (once it asked the default provider about each one)."""
    target, image, program, flow = gizmo
    monkeypatch.setenv("DREAD_LLM", str(tmp_path / "no-such-llm.toml"))
    assert pilot.main(["--target", target.root, "--task", "input", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "nothing asked" in out and "missed instructions" in out


@needs_gcc
def test_the_input_question_shows_the_callees_and_a_stopped_input_where_it_stopped(gizmo):
    """--task input: the question holds the listings of the functions the function calls (without
    names a model cannot tell what a helper reads: FreeRTOS made nameless, 2026-10-21), and an input
    that does not finish comes back with where it stopped and why: the fault, the function and the
    instruction, the registers."""
    target, image, program, flow = gizmo
    fn = program.by_name["uart_puts"]
    provider = llm.Scripted(['```json\n{"args": ["0xdead0000", 0, 0, 0], "mem": [], "poll": []}\n```'])
    pilot.find_inputs(target, image, program, provider, "uart_puts", list(fn.insns), tries=1, flow=flow,
                      out=lambda *a: None)
    question = provider.asked[0][1][0]["content"]
    assert "The functions it calls" in question and "; uart_putc " in question, question[-1500:]
    log = open(os.path.join(target.build, "pilot", image.name, "uart_puts.inputs.md")).read()
    told = log.split("## The tools, after answer 1")[1]
    assert "does not finish" in told and "in uart_puts" in told and "r0=0xdead0000" in told, told


class Busy(BaseHTTPRequestHandler):
    """Overloaded twice (529, then 429 with retry-after), then an answer."""
    calls = []

    def do_POST(self):
        self.rfile.read(int(self.headers["content-length"]))
        Busy.calls.append(self.path)
        if len(Busy.calls) <= 2:
            data = b'{"type": "error", "error": {"type": "overloaded_error"}}'
            self.send_response(529 if len(Busy.calls) == 1 else 429)
            if len(Busy.calls) == 2:
                self.send_header("retry-after", "0")
        else:
            data = json.dumps({"content": [{"type": "text", "text": "at last"}], "stop_reason": "end_turn",
                               "usage": {"output_tokens": 2, "input_tokens": 3}}).encode()
            self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def test_a_provider_asks_again_after_an_overload_or_a_rate_limit(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), Busy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % server.server_port
    monkeypatch.setenv("TEST_KEY", "secret")
    try:
        Busy.calls.clear()
        remote = llm.Anthropic("claude", {"url": url, "model": "c", "key_env": "TEST_KEY", "retry_wait": 0.01})
        assert remote.ask("s", [{"role": "user", "content": "hello"}]) == "at last" and len(Busy.calls) == 3
        Busy.calls.clear()
        once = llm.Anthropic("claude", {"url": url, "model": "c", "key_env": "TEST_KEY", "retry_wait": 0.01,
                                        "retries": 1})
        with pytest.raises(llm.ProviderError, match="HTTP 429"):
            once.ask("s", [{"role": "user", "content": "hello"}])
        assert len(Busy.calls) == 2
    finally:
        server.shutdown()


def test_a_naming_run_stops_asking_a_provider_that_keeps_declining(gizmo):
    """Three declined in a row end the run: the questions not begun yet are not asked."""
    target, image, program, flow = gizmo
    seen = []

    def answer(messages):
        import time
        seen.append(messages[0]["content"])
        time.sleep(0.05)                    # (an answer takes a while: the rest wait in the queue)
        return {"text": "", "finish": "refusal"}
    names = [fn.name for fn in program.functions][:12]
    lines = []
    made, asked, _ = pilot.name_functions(target, image, program, lambda: llm.Scripted(answer), names, flow,
                                          jobs=1, out=lines.append)
    assert made == 0 and asked == len(seen) and 3 <= asked <= 4
    assert any("declined 3 questions in a row" in line for line in lines)


def test_a_second_pass_goes_callers_first_with_the_first_names(gizmo):
    """--refine: led_on, which calls gpio_set, is asked again first; gpio_set's question then shows
    led_on's second name and its own first one; the answers are kept marked refined, and a second
    refining run asks nothing."""
    target, image, program, flow = gizmo
    first = {"gpio_set": "pin_write", "led_on": "led_switch_on"}
    second = {"gpio_set": "led_pin_write", "led_on": "status_led_on"}
    seen = []

    def answer(messages):
        question = messages[0]["content"]
        name = re.search(r"The function (\w+)", question).group(1)
        seen.append((name, question))
        table = second if "A first reading" in question else first
        return "name: %s\nwhat: does it" % table[name]
    pilot.name_functions(target, image, program, lambda: llm.Scripted(answer), ["led_on", "gpio_set"], flow,
                         out=lambda *a: None)
    seen.clear()
    made, asked, _ = pilot.name_functions(target, image, program, lambda: llm.Scripted(answer),
                                          ["led_on", "gpio_set"], flow, out=lambda *a: None, refine=True)
    assert (made, asked) == (2, 2) and [n for n, _ in seen] == ["led_on", "gpio_set"]
    assert "named it pin_write: does it" in seen[1][1] and "status_led_on" in seen[1][1]
    refined = set()
    proposals = pilot.read_proposals(target, image, refined)
    assert {p for p, _ in proposals.values()} == set(second.values()) and len(refined) == 2
    seen.clear()
    assert pilot.name_functions(target, image, program, lambda: llm.Scripted(answer), ["led_on", "gpio_set"],
                                flow, out=lambda *a: None, refine=True)[:2] == (0, 0)


def test_the_globals_alone_and_the_name_questions_show_them(gizmo, monkeypatch, capsys):
    """--task globals names the globals of every function's readable C, with no readable table (gizmo
    has none), and asks nothing else; a function's name is refused; the questions for function names
    show the names then. A run started again asks only for the globals still without one, and after a
    naming run its question shows the functions by their proposals, which no global may take."""
    from dread import naming
    target, image, program, flow = gizmo
    asked = []

    def answer(messages):
        asked.append(messages[0]["content"])
        if len(asked) == 1:
            return '```json\n{"g_2000004c": "tick_count", "io_40011000": "UART_DATA", "g_20000034": "gpio_set"}\n```'
        return '```json\n{"g_20000034": "show_settings", "g_20000038": "mode"}\n```'
    monkeypatch.setattr(llm, "load", lambda name=None, path=None: llm.Scripted(answer))
    assert pilot.main(["--target", target.root, "--task", "globals"]) == 0
    out = capsys.readouterr().out
    assert len(asked) == 1 and "g_2000004c (0x2000004c, u32, in 2 functions):" in asked[0], asked
    assert "    print_status: " in asked[0] and pilot.PROPOSED_FUNCTIONS not in asked[0]
    assert "refused g_20000034=gpio_set" in out and "2 globals and 0 structures named" in out, out
    assert {k: e["name"] for k, e in naming.load(image, "globals").items()} == {
        "0x2000004c u32": "tick_count", "0x40011000 u32": "UART_DATA"}
    assert not os.path.exists(pilot.names_path(target, image))         # (no function asked)
    text, _ = pilot.Namer(target, image, program, flow).question(program.by_name["main"])
    assert "while (tick_count == " in text and "g_2000004c" not in text, text
    pilot.keep_proposal(target, image, program.by_name["print_status"], "show_settings", "prints them",
                        llm.Scripted([]), "readable")
    assert pilot.main(["--target", target.root, "--task", "globals"]) == 0
    out = capsys.readouterr().out
    assert len(asked) == 2 and "io_40011004 (" in asked[1] and "g_2000004c (" not in asked[1], asked
    assert asked[1].startswith(pilot.PROPOSED_FUNCTIONS) and "    show_settings: " in asked[1], asked
    assert "print_status" not in asked[1] and "refused g_20000034=show_settings" in out, out
    assert naming.load(image, "globals")["0x20000038 u32"]["name"] == "mode"


def test_a_naming_run_with_behaviour_shows_the_runs_with_the_proposals_so_far(gizmo):
    """--behaviour: uart_puts's question shows what it did with a written input ("hi!"): uart_putc
    called once for each character, by the name proposed for uart_putc; names.tsv says the questions
    showed runs."""
    target, image, program, flow = gizmo
    uart = target.models["uart"]
    buf = target.memory.scratch_base + 0x1000
    os.makedirs(image.extra_cases, exist_ok=True)
    with open(os.path.join(image.extra_cases, "uart_puts.json"), "w") as f:
        json.dump({"cases": [{"args": [buf, 0, 0, 0], "mem": [[buf, 4, int.from_bytes(b"hi!\0", "little")],
                                                               [uart["base"] + uart["status"], 4, uart["tx_ready"]]]}]}, f)
    cache = os.path.join(target.build, "autocases", image.name, "uart_puts.json")
    if os.path.exists(cache):
        os.remove(cache)
    seen = []

    def answer(messages):
        question = messages[0]["content"]
        name = re.search(r"The function (\w+)", question).group(1)
        seen.append((name, question))
        return {"uart_putc": "name: serial_write_char\nwhat: sends one character",
                "uart_puts": "name: serial_write_text\nwhat: sends a text"}[name]
    lines = []
    made, asked, _ = pilot.name_functions(target, image, program, lambda: llm.Scripted(answer),
                                          ["uart_puts", "uart_putc"], flow, jobs=1, out=lines.append, behaviour=True)
    assert (made, asked) == (2, 2) and [n for n, _ in seen] == ["uart_putc", "uart_puts"]
    question = seen[1][1]
    assert 'uart_puts(r0=0x%x "hi!") returned' % buf in question
    assert 'serial_write_char(x) (3 times), x = "hi!"' in question and "uart_putc" not in question
    rows = [r.split("\t") for r in open(pilot.names_path(target, image)).read().splitlines()[1:]]
    assert len(rows) == 2 and all(r[5].endswith(pilot.BEHAVIOUR) for r in rows)
    assert any("runs of their cases for 2 of 2 functions" in line for line in lines)


def gizmo_elf(target):
    return os.path.join(os.path.dirname(target.root), "m3", "original", "gizmo.elf")


def scripted_names(answers, seen):
    """A provider's answers for name questions: the n-th question about a function gets answers[its
    name][n] (in the order asked: the tests ask one question at a time)."""
    def answer(messages):
        question = messages[0]["content"]
        name = re.search(r"The function (\w+)", question).group(1)
        seen.append((name, question))
        return "name: %s\nwhat: does it" % answers[name][sum(1 for n, _ in seen if n == name) - 1]
    return answer


def test_a_vote_keeps_the_name_most_answers_agree_with(gizmo):
    """--samples 3: each function is asked three times, the later questions with its callers in an order
    of their own; the name most answers agree with is kept, the agreement its confidence, and
    names.tsv keeps every answer."""
    target, image, program, flow = gizmo
    seen, lines = [], []
    answers = {"gpio_set": ["pin_write", "gpio_pin_write", "uart_send"], "led_on": ["led_switch_on"] * 3}
    made, asked, _ = pilot.name_functions(target, image, program, lambda: llm.Scripted(scripted_names(answers, seen)),
                                          ["led_on", "gpio_set"], flow, jobs=1, out=lines.append, samples=3)
    assert (made, asked) == (2, 2) and [n for n, _ in seen] == ["gpio_set"] * 3 + ["led_on"] * 3
    asked_gpio = [q.split("It is called by: ") for n, q in seen if n == "gpio_set"]
    assert len({code for code, _ in asked_gpio}) == 1                       # (the code as it was)
    assert len({callers for _, callers in asked_gpio}) == 3                 # (the callers in three orders)
    assert len({tuple(sorted(callers.rstrip(".").split(", "))) for _, callers in asked_gpio}) == 1
    rows = {r["now"]: r for r in pilot.read_name_rows(pilot.names_path(target, image))}
    assert (rows["gpio_set"]["proposed"], rows["gpio_set"]["confidence"]) == ("pin_write", "0.67")
    assert rows["gpio_set"]["samples"] == "pin_write gpio_pin_write uart_send"
    assert (rows["led_on"]["confidence"], rows["led_on"]["names_shown"], rows["led_on"]["pass"]) == ("1.00", "1", "1")
    assert rows["gpio_set"]["names_shown"] == "0" and "pin_write(" in seen[3][1]
    assert any("2 of 3 agree" in line for line in lines), lines


def test_names_go_in_waves_of_batches(gizmo):
    """--batch (name_functions in waves): every function whose callees have their names is asked in one
    wave, a wave of batch questions or more as a Message Batch, an answer that cannot be used asked again
    in the next round (another batch here); the proposals are those of the questions asked one at a time,
    with the same answers."""
    target, image, program, flow = gizmo
    rows, made = {}, []

    def answer(messages):                   # (gpio_set's first answers cannot be used, those asked again can)
        name = re.search(r"The function (\w+)", llm.content_text(messages[0]["content"])).group(1)
        if name == "gpio_set":
            return "no idea" if len(messages) == 1 else "name: pin_write\nwhat: drives a pin"
        return "name: led_switch_on\nwhat: switches it on"
    for batch in (0, 1):
        def provider():
            made.append(llm.Scripted(answer, batches=True))
            return made[-1]
        shutil.rmtree(os.path.join(target.build, "pilot"), ignore_errors=True)
        lines = []
        done, asked, spent = pilot.name_functions(target, image, program, provider, ["led_on", "gpio_set"], flow,
                                                  jobs=1, out=lines.append, samples=2, batch=batch)
        assert (done, asked) == (2, 2), lines
        rows[batch] = {r["now"]: (r["proposed"], r["samples"], r["confidence"], r["what"])
                       for r in pilot.read_name_rows(pilot.names_path(target, image))}
        if batch:
            batcher = next(p for p in made if p.submitted)
            sizes = [len(b["requests"]) for b in batcher.submitted]
            assert sizes == [2, 2, 2], sizes  # (gpio_set's two samples, both asked again, led_on's two)
            again = batcher.submitted[1]["requests"][0][1]["messages"]
            assert again[-1]["content"].startswith("That name cannot be used") and spent["batch"] == 6
            assert "pin_write(" in llm.content_text(batcher.submitted[2]["requests"][0][1]["messages"][0]["content"])
            assert any("in waves" in line for line in lines), lines
    assert rows[0] == rows[1] and rows[0]["gpio_set"][0] == "pin_write", rows


def test_the_samples_share_their_code_in_the_prompt_cache(gizmo):
    """Where the provider keeps it (llm.Provider.cacheable), the beginning a function's samples share (the
    device line and its code; the callers' order differs) is marked for the prompt cache, the text as
    content_text joins the parts the same as before; asked one at a time the first sample goes first and
    the others once it has ended; in a batch the first samples come first."""
    target, image, program, flow = gizmo
    asked, made = [], []

    def answer(messages):
        content = messages[0]["content"]
        asked.append(content)
        return "name: %s_x\nwhat: does it" % re.search(r"The function (\w+)", llm.content_text(content)).group(1)
    pilot.name_functions(target, image, program, lambda: llm.Scripted(answer, caches=True), ["led_on", "gpio_set"],
                         flow, jobs=4, samples=3)
    gpio = [c for c in asked if "The function gpio_set" in llm.content_text(c)]
    assert len(gpio) == 3 and all(isinstance(c, list) and c[0]["cache"] for c in gpio), gpio
    assert len({c[0]["text"] for c in gpio}) == 1 and gpio[0][0]["text"].endswith("```")
    text, _, shared = pilot.Namer(target, image, program, flow).question_parts(program.by_name["gpio_set"])
    assert llm.content_text(pilot.cache_parts(text, shared)) == text and shared < len(text)
    assert [llm.content_text(c).startswith(gpio[0][0]["text"]) for c in gpio] == [True] * 3
    shutil.rmtree(os.path.join(target.build, "pilot"))
    asked.clear()

    def provider():
        made.append(llm.Scripted(answer, caches=True, batches=True))
        return made[-1]
    pilot.name_functions(target, image, program, provider, ["led_on", "gpio_set"], flow, jobs=4, samples=3, batch=1)
    batcher = next(p for p in made if p.submitted)
    first = [p["messages"][0]["content"] for _, p in batcher.submitted[0]["requests"]]
    assert len(first) == 3 and [isinstance(c, list) for c in first] == [True] * 3
    assert pilot.shared_parts(None, "s", [("a\n\nb", 1), ("a\n\nc", 1)]) == [("a\n\nb", None), ("a\n\nc", None)]


def test_a_gate_shows_sure_names_only_and_later_passes_ask_what_changed(gizmo):
    """--samples 3 --gate 0.6 --passes 5: gpio_set's first vote is unsure (one of three), so led_on's
    question shows gpio_set; pass 2 (callers first) asks gpio_set again, its question now showing
    led_on's sure name, and its first name stays in a tie, sure now; pass 3 asks led_on again, which
    now sees it; nothing a question shows changes then, and no pass 4 runs."""
    target, image, program, flow = gizmo
    seen, lines = [], []
    answers = {"gpio_set": ["pin_write", "uart_send", "clock_begin", "pin_write", "pin_write", "gpio_write"],
               "led_on": ["led_switch_on"] * 6}
    made, asked, _ = pilot.name_functions(target, image, program, lambda: llm.Scripted(scripted_names(answers, seen)),
                                          ["led_on", "gpio_set"], flow, jobs=1, out=lines.append, samples=3,
                                          gate=0.6, passes=5)
    assert [n for n, _ in seen] == ["gpio_set"] * 3 + ["led_on"] * 3 + ["gpio_set"] * 3 + ["led_on"] * 3
    assert "gpio_set(" in seen[3][1] and "pin_write" not in seen[3][1]     # (unsure: not shown)
    assert "led_switch_on" in seen[6][1] and "pin_write" not in seen[6][1]  # (a function's own name: never)
    assert "pin_write(" in seen[9][1]
    rows = pilot.read_name_rows(pilot.names_path(target, image))
    assert [(r["now"], r["pass"], r["proposed"], r["confidence"]) for r in rows] == [
        ("gpio_set", "1", "pin_write", "0.33"), ("led_on", "1", "led_switch_on", "1.00"),
        ("gpio_set", "2", "pin_write", "0.67"), ("led_on", "3", "led_switch_on", "1.00")]
    passes = [line for line in lines if " pass " in line]
    assert len(passes) == 3, lines
    assert "pass 2 (callers first): asked 1, proposed 1, shown names changed 1" in passes[1]
    assert "pass 3 (callees first): asked 1, proposed 1, shown names changed 0" in passes[2]
    assert (made, asked) == (4, 4)
    assert pilot.read_proposals(target, image, upto_pass=2)[program.by_name["led_on"].entry][0] == "led_switch_on"


def test_ablation_asks_again_with_no_other_name_shown(gizmo):
    """--ablate: each function is asked also with no proposal shown; led_on, whose question showed
    gpio_set's proposal, answers otherwise without it (depends); gpio_set's question showed none, so
    it was asked the same question twice (the votes' own noise); --score gives the verdicts of each."""
    target, image, program, flow = gizmo
    seen = []

    def answer(messages):
        question = messages[0]["content"]
        name = re.search(r"The function (\w+)", question).group(1)
        seen.append((name, question))
        proposed = "pin_write" if name == "gpio_set" else "led_switch_on" if "pin_write(" in question else "gpio_enable"
        return "name: %s\nwhat: does it" % proposed
    pilot.name_functions(target, image, program, lambda: llm.Scripted(answer), ["led_on", "gpio_set"], flow,
                         jobs=1, out=lambda *a: None, ablate=True)
    assert [n for n, _ in seen] == ["gpio_set", "gpio_set", "led_on", "led_on"]
    assert seen[0][1] == seen[1][1] and "pin_write(" in seen[2][1] and "gpio_set(" in seen[3][1]
    rows = {r["now"]: r for r in pilot.read_name_rows(pilot.names_path(target, image))}
    assert [rows["led_on"][k] for k in ("proposed", "hidden", "depends", "names_shown")] == [
        "led_switch_on", "gpio_enable", "yes", "1"]
    assert [rows["gpio_set"][k] for k in ("hidden", "depends", "names_shown")] == ["pin_write", "no", "0"]
    lines = []
    totals = pilot.score_names(target, image, gizmo_elf(target), out=lines.append,
                               judge=llm.Scripted(['```json\n{"1": "different", "2": "different"}\n```']))
    assert totals["ablation"][("yes", False)]["different"] == 1 and totals["ablation"][("no", True)]["n"] == 1
    assert any("names shown, the name depends on them: 1, judged the same 0 (0 %)" in line for line in lines), lines


def test_an_older_names_file_is_read_and_extended(gizmo):
    """A names.tsv of six columns (before votes) reads as proposals without a confidence, a refined
    row as pass 2; a new proposal gives the file the new header and keeps the old rows."""
    target, image, program, flow = gizmo
    path = pilot.names_path(target, image)
    os.makedirs(os.path.dirname(path))
    led, gpio = program.by_name["led_on"], program.by_name["gpio_set"]
    with open(path, "w") as f:
        f.write("address\tnow\tproposed\twhat\tprovider\tshown\n")
        f.write("0x%x\tled_on\tled_up\tlights it\thaiku (m)\treadable\n" % led.entry)
        f.write("0x%x\tled_on\tled_light\tlights it\thaiku (m)\treadable, refined\n" % led.entry)
    refined = set()
    assert pilot.read_proposals(target, image, refined) == {led.entry: ("led_light", "lights it")}
    assert refined == {led.entry}
    rows = pilot.read_name_rows(path)
    assert [r["pass"] for r in rows] == ["1", "2"] and pilot.confidence(rows[0]) is None
    assert pilot.read_proposals(target, image, upto_pass=1) == {led.entry: ("led_up", "lights it")}
    pilot.keep_proposal(target, image, gpio, "pin_write", "drives\ta pin", llm.Scripted([]), "readable",
                        {"confidence": "0.80", "pass": "1"})
    text = open(path).read().splitlines()
    assert text[0].split("\t") == list(pilot.NAMES_COLUMNS) and len(text) == 4
    assert text[1].endswith("\treadable") and len(text[1].split("\t")) == 6     # (the old rows as they were)
    rows = pilot.last_rows(pilot.read_name_rows(path))
    assert pilot.confidence(rows[gpio.entry]) == 0.8 and rows[gpio.entry]["what"] == "drives a pin"


def test_scores_by_confidence_and_two_runs_judged_pairwise(gizmo, tmp_path):
    """--score with confidences gives the verdicts of each confidence (the calibration); --against
    asks the judge, where this run's proposal and another run's differ, which is closer to the true
    name, in both orders (A and B swapped): a side wins when the two verdicts add up to it."""
    target, image, program, flow = gizmo
    fns = [program.by_name[n] for n in ("led_on", "uart_putc", "gpio_set", "delay")]
    mine = ["led_turn_on", "uart_send_char", "gpio_write", "wait_ticks"]
    theirs = ["led_turn_on", "serial_out", "pin_toggle", "sleep"]
    for fn, name, sure in zip(fns, mine, ("1.00", "0.40", "1.00", "0.40")):
        pilot.keep_proposal(target, image, fn, name, "x", llm.Scripted([]), "readable", {"confidence": sure})
    other = tmp_path / "other.tsv"
    other.write_text("address\tnow\tproposed\twhat\tprovider\tshown\n" + "".join(
        "0x%x\t%s\t%s\tx\tp\treadable\n" % (fn.entry, fn.name, name) for fn, name in zip(fns, theirs)))
    order = [fn.name for fn in sorted(fns[1:], key=lambda f: f.entry)]       # (the lines go by address)
    first = {"uart_putc": "A", "gpio_set": "A", "delay": "B"}               # (A: this run's)
    second = {"uart_putc": "B", "gpio_set": "A", "delay": "A"}              # (A: the other's)
    judge = llm.Scripted([
        '```json\n{"1": "same", "2": "close", "3": "different", "4": "different"}\n```',
        "```json\n%s\n```" % json.dumps({str(k + 1): first[n] for k, n in enumerate(order)}),
        "```json\n%s\n```" % json.dumps({str(k + 1): second[n] for k, n in enumerate(order)})])
    lines = []
    totals = pilot.score_names(target, image, gizmo_elf(target), out=lines.append, judge=judge)
    assert sorted(totals["calibration"]) == [0.4, 1.0]
    assert totals["calibration"][0.4]["n"] == 2 and totals["calibration"][1.0]["n"] == 2
    assert any(line.startswith("dread pilot --score: confidence 0.40: 2, judged") for line in lines), lines
    scores = open(os.path.join(target.build, "pilot", image.name, "names-score.tsv")).read()
    assert pilot.compare_names(target, image, gizmo_elf(target), str(other), judge, out=lines.append) == {
        "both": 4, "differ": 3, "judged": 3, "wins": 1, "losses": 1, "ties": 1, "p": 1.0,
        "by": "source files", "groups_won": 0, "groups_lost": 0, "groups_even": 1, "groups_p": 1.0}   # (all hw.c)
    assert any(line.startswith("dread pilot --score --against: by source files, this run's closer in 0, the other's "
                               "in 0, even in 1") for line in lines), lines
    assert open(os.path.join(target.build, "pilot", image.name, "names-score.tsv")).read() == scores
    swapped = judge.asked[2][1][0]["content"]
    assert "| A: serial_out | B: uart_send_char" in swapped and "| A: uart_send_char" in judge.asked[1][1][0]["content"]
    table = open(os.path.join(target.build, "pilot", image.name, "names-pairwise.tsv")).read()
    assert "\tuart_send_char\tserial_out\tthis\n" in table and "\twait_ticks\tsleep\tother\n" in table
    assert "\tgpio_write\tpin_toggle\ttie\n" in table and "led_turn_on" not in table
    assert pilot.sign_test(10, 0) == 2 / 1024 and pilot.sign_test(0, 0) == 1.0 and pilot.sign_test(3, 3) == 1.0


def test_the_naming_options_that_need_others(gizmo):
    """--gate needs --samples (one question's proposal has no confidence); --refine and --passes are
    two kinds of later pass; --against needs a judge. Each is refused before any provider is asked."""
    target, image, program, flow = gizmo
    assert pilot.main(["--target", target.root, "--task", "name", "--gate", "0.6", "--samples", "1"]) == 1
    assert pilot.main(["--target", target.root, "--task", "name", "--refine", "--passes", "3"]) == 1
    assert pilot.main(["--target", target.root, "--task", "name", "--score", gizmo_elf(target),
                       "--against", "x.tsv"]) == 1


def test_the_naming_defaults_are_five_questions_and_two_passes(gizmo, tmp_path, monkeypatch):
    """dread pilot --task name asks each function five times, runs a second pass, callers first, and
    names the functions' groups first to show them (measured on FreeRTOS, docs/decisions.md
    2026-10-22); --refine is a second pass of its own; --no-subsystems leaves the groups out."""
    target, image, program, flow = gizmo
    config = tmp_path / "llm.toml"
    config.write_text('[providers.x]\nkind = "openai"\nmodel = "m"\n')
    monkeypatch.setenv("DREAD_LLM", str(config))
    got = {}

    def fake(*args, **options):
        got.update(options)
        return 0, 0, {"answers": 0, "input": 0, "output": 0, "cost": None}
    monkeypatch.setattr(pilot, "name_functions", fake)
    assert pilot.main(["--target", target.root, "--task", "name"]) == 0
    assert (got["samples"], got["passes"], got["gate"], got["ablate"], got["subsystems"]) == (5, 2, None, False, True)
    assert pilot.main(["--target", target.root, "--task", "name", "--refine", "--samples", "1", "--no-subsystems"]) == 0
    assert (got["samples"], got["passes"], got["refine"], got["subsystems"]) == (1, 1, True, False)


def test_votes_with_behaviour_show_the_same_runs_in_every_question(gizmo):
    """--behaviour with --samples 3: the runs are made once, before the questions, and each of a
    function's questions shows them alike (a vote's samples put only the callers in another order);
    names.tsv's shown column has the behaviour mark, and after --refine the refined one beside it,
    which is still read as refined."""
    target, image, program, flow = gizmo
    uart = target.models["uart"]
    buf = target.memory.scratch_base + 0x1000
    os.makedirs(image.extra_cases, exist_ok=True)
    with open(os.path.join(image.extra_cases, "uart_puts.json"), "w") as f:
        json.dump({"cases": [{"args": [buf, 0, 0, 0], "mem": [[buf, 4, int.from_bytes(b"hi!\0", "little")],
                                                               [uart["base"] + uart["status"], 4, uart["tx_ready"]]]}]}, f)
    cache = os.path.join(target.build, "autocases", image.name, "uart_puts.json")
    if os.path.exists(cache):
        os.remove(cache)
    seen, lines = [], []
    answers = {"uart_putc": ["serial_write_char"] * 4, "uart_puts": ["serial_write_text"] * 4}
    made, asked, _ = pilot.name_functions(target, image, program, lambda: llm.Scripted(scripted_names(answers, seen)),
                                          ["uart_puts", "uart_putc"], flow, jobs=1, out=lines.append, behaviour=True,
                                          samples=3)
    assert (made, asked) == (2, 2) and sum("runs of their cases" in line for line in lines) == 1, lines
    puts = [q for n, q in seen if n == "uart_puts"]
    runs = [q.split("It is called by: ")[1].split("\n\n", 1)[1] for q in puts]
    assert len(runs) == 3 and len(set(runs)) == 1 and 'serial_write_char(x) (3 times), x = "hi!"' in runs[0]
    rows = pilot.read_name_rows(pilot.names_path(target, image))
    assert all(r["shown"].endswith(pilot.BEHAVIOUR) and r["confidence"] == "1.00" for r in rows)
    pilot.name_functions(target, image, program, lambda: llm.Scripted(scripted_names(answers, seen)),
                         ["uart_puts", "uart_putc"], flow, jobs=1, out=lambda *a: None, behaviour=True, refine=True)
    refined = set()
    pilot.read_proposals(target, image, refined)
    rows = pilot.last_rows(pilot.read_name_rows(pilot.names_path(target, image)))
    assert refined == set(rows) and all(r["shown"].endswith(pilot.BEHAVIOUR + pilot.REFINED) and r["pass"] == "2"
                                        for r in rows.values())
    assert pilot.marked("readable, refined, behaviour", pilot.REFINED) and not pilot.marked("readable", pilot.REFINED)


def scripted_parts(seen):
    """A provider's answers for both kinds of question: a group's (dread.groups: the UART driver's for
    the group whose code shows uart_putc, "misc_" for the others) and a function's (its name with "_x"
    behind)."""
    def answer(messages):
        question = messages[0]["content"]
        seen.append(question)
        if question.startswith("The group of"):
            if re.search(r"\buart_putc\(", question):
                return "name: serial port driver\nprefix: uart\nwhat: drives the UART"
            return "name: misc\nprefix: misc_\nwhat: the rest"
        return "name: %s_x\nwhat: does it" % re.search(r"The function (\w+)", question).group(1)
    return answer


def test_groups_are_named_from_all_their_evidence(gizmo):
    """--task subsystems (name_subsystems): a question a group with its size, its functions' names, the
    texts they point to, the devices whose registers they use, its callers and callees outside it and
    its code; an answer without the three lines goes back; the prefix most answers agree with is kept
    (samples 3: uart, uart_ and serial_), the agreement its confidence; subsystems.tsv and groups.tsv
    keep it; a run started again asks nothing."""
    from dread import groups
    target, image, program, flow = gizmo
    touched = groups.evidence(target, image, program, flow)
    found = groups.find(target, image, program, flow, touched=touched)
    uart = groups.membership(found)["uart_putc"]
    namer = pilot.Namer(target, image, program, flow)
    seen = []
    in_order = ["it drives the serial port", "name: serial port driver\nprefix: uart\nwhat: drives the UART",
                "name: UART\nprefix: uart_\nwhat: the UART", "name: serial\nprefix: serial_\nwhat: serial"]

    def answer(messages):                   # (one question at a time: the UART group's answers in order)
        question = messages[0]["content"]
        seen.append(question)
        if "uart_putc" in question.split("Its functions, as C")[0]:
            return in_order.pop(0)
        return "name: misc\nprefix: misc_\nwhat: the rest"
    provider = llm.Scripted(answer)
    lines = []
    named = pilot.name_subsystems(target, image, program, lambda: provider, found, namer, touched, samples=3, jobs=1,
                                  out=lines.append)
    asked = [q for q in seen if "uart_putc" in q.split("Its functions, as C")[0]]
    question = asked[0]
    base = target.models["uart"]["base"]
    assert question.startswith("The group of %d functions at 0x" % len(found[uart]))
    assert "Device registers its functions use: 0x%x-0x%x" % (base, base + groups.DEVICE_BLOCK - 1) in question
    assert "```c\n" in question and "uart_putc(" in question and "Functions outside the group that call" in question
    assert any('Texts its functions point to: "' in q for q in seen)
    assert any(m["content"].startswith("That cannot be used: no three lines") for _, ms in provider.asked
               for m in ms if m["role"] == "user")
    rows = pilot.read_subsystems(pilot.subsystems_path(target, image))
    row = rows[pilot.members_key(program, found[uart])]
    assert (row["name"], row["prefix"], row["confidence"]) == ("serial port driver", "uart_", "0.67"), row
    assert row["samples"] == "uart_ uart_ serial_" and row["group"] == str(uart)
    assert named["uart_putc"] == (len(found[uart]), row) and set(named) == {n for g in found for n in g
                                                                            if len(g) >= pilot.SUBSYSTEM_SMALLEST}
    assert any("serial port driver, prefix uart_ (2 of 3 agree)" in line for line in lines), lines
    table = pilot.read_table(pilot.groups_path(target, image), pilot.GROUP_COLUMNS)
    assert len(table) == len(program.functions) and {r["group"] for r in table if r["now"] in UART_FUNCTIONS} == {str(uart)}
    assert pilot.function_subsystems(target, image)[program.by_name["uart_putc"].entry] == (len(found[uart]), row)
    seen.clear()
    pilot.name_subsystems(target, image, program, lambda: provider, found, namer, touched, samples=3, out=lines.append)
    assert seen == []
    assert pilot.parse_subsystem("name: x\nprefix: `fs`\nwhat: y") == ("x", "fs_", "y")
    assert pilot.subsystem_problem("a_very_long_prefix_of_words_") and not pilot.subsystem_problem("fs_")


UART_FUNCTIONS = {"uart_init", "uart_putc", "uart_puts", "uart_getc"}


def test_name_questions_show_their_group_and_the_score_counts_its_prefix(gizmo, tmp_path):
    """--subsystems: the groups are named first (one question each), then each function's question shows
    its group's name, prefix and sentence, its form marked; --score counts the proposals that begin with
    their group's prefix and puts each group beside its most common source file (DWARF), judged."""
    pytest.importorskip("elftools")
    target, image, program, flow = gizmo
    seen, lines = [], []
    made, asked, _ = pilot.name_functions(target, image, program, lambda: llm.Scripted(scripted_parts(seen)),
                                          ["uart_puts", "uart_putc"], flow, jobs=1, out=lines.append, subsystems=True)
    assert (made, asked) == (2, 2)
    groups_asked = [q for q in seen if q.startswith("The group of")]
    functions_asked = [q for q in seen if q.startswith("The function")]
    assert groups_asked and seen.index(functions_asked[0]) > seen.index(groups_asked[-1])
    assert all("named that part serial port driver, with the prefix uart_ for its functions' names: drives the UART"
               in q for q in functions_asked), functions_asked[0]
    rows = pilot.read_name_rows(pilot.names_path(target, image))
    assert all(pilot.marked(r["shown"], pilot.SUBSYSTEM) for r in rows)
    pilot.keep_proposal(target, image, program.by_name["uart_putc"], "serial_send_char", "x", llm.Scripted([]),
                        "readable")
    judge = llm.Scripted(['```json\n{}\n```'] + ['```json\n{"1": "same", "2": "close", "3": "different"}\n```'] * 9)
    lines.clear()
    totals = pilot.score_names(target, image, gizmo_elf(target), out=lines.append, judge=judge)
    parts = totals["subsystems"]
    assert (parts["proposals"], parts["use_prefix"]) == (2, 1), parts
    assert any("1 of 2 proposals in a named group begin with its prefix" in line for line in lines), lines
    table = open(os.path.join(target.build, "pilot", image.name, "names-subsystems.tsv")).read()
    assert "\tserial port driver\tuart_\thw.c\t" in table
    assert any("the groups' names against their files, judged by scripted" in line for line in lines), lines
    assert pilot.uses_prefix("uart_send_char", "uart_") and not pilot.uses_prefix("uarts_send", "uart_")
    assert pilot.uses_prefix("UartSend", "uart_") and not pilot.uses_prefix("send", "uart_")


def test_names_do_without_their_groups_when_the_grouping_fails(gizmo, monkeypatch):
    """--subsystems is the default: a grouping that fails is said in a line, and the functions are
    named as without it (no group questions, no group line, no subsystem mark)."""
    from dread import groups

    def broken(*args, **kw):
        raise RuntimeError("no facts today")
    monkeypatch.setattr(groups, "evidence", broken)
    target, image, program, flow = gizmo
    seen, lines = [], []
    made, asked, _ = pilot.name_functions(target, image, program, lambda: llm.Scripted(scripted_parts(seen)),
                                          ["uart_putc"], flow, jobs=1, out=lines.append, subsystems=True)
    assert (made, asked) == (1, 1) and len(seen) == 1 and "named that part" not in seen[0]
    assert any("no subsystems, the grouping failed: RuntimeError: no facts today" in line for line in lines), lines
    assert not pilot.marked(pilot.read_name_rows(pilot.names_path(target, image))[0]["shown"], pilot.SUBSYSTEM)


def test_names_judged_against_their_source(gizmo, tmp_path):
    """--grounded: each proposal judged against its function's source (the truth ELF's debug information,
    the files below --source), the name and its sentence apart; names-score.tsv gets each verdict and
    its reason, the totals their counts and mean. With --against, the proposals of the two runs that
    differ are judged, both runs' questions of a file sharing its sources, the better verdict closer."""
    pytest.importorskip("elftools")
    target, image, program, flow = gizmo
    fns = [program.by_name[n] for n in ("uart_putc", "gpio_set", "delay")]
    mine = ["serial_write_char", "pin_write", "wait_ticks"]
    theirs = ["serial_write_char", "pin_toggle", "busy_loop"]
    for fn, name in zip(fns, mine):
        pilot.keep_proposal(target, image, fn, name, "does it", llm.Scripted([]), "readable")
    other = tmp_path / "other.tsv"
    other.write_text("address\tnow\tproposed\twhat\tprovider\tshown\n" + "".join(
        "0x%x\t%s\t%s\tx\tp\treadable\n" % (fn.entry, fn.name, name) for fn, name in zip(fns, theirs)))
    verdict = {"serial_write_char": "right | right", "pin_write": "right | partly: says more",
               "wait_ticks": "partly: vague | right", "pin_toggle": "wrong: it sets the pin | right",
               "busy_loop": "right | right"}
    asked = []

    def answer(messages):
        parts = messages[0]["content"]
        asked.append(parts)
        assert "FUNCTION_1" in parts[0]["text"]
        numbered = re.findall(r"^(\d+)\. (\w+): ", parts[1]["text"], re.M)
        return "```json\n%s\n```" % json.dumps({n: verdict[name] for n, name in numbered})
    fw = os.path.join(os.path.dirname(target.root), "fw")
    lines = []
    totals = pilot.score_names(target, image, gizmo_elf(target), out=lines.append, grounded=llm.Scripted(answer),
                               sources=[fw])
    g = totals["grounded"]
    assert (g["verdicts"], g["right"], g["partly"], g["wrong"], g["sentence_partly"]) == (3, 2, 1, 0, 1), g
    assert g["mean"] == pytest.approx(2.5 / 3)
    assert any("against the source, judged by scripted (scripted), 3 of 3 proposals with a source: right 2 (67 %)"
               in line for line in lines), lines
    assert any("whatever the naming style" in line for line in lines)
    assert not any(parts[0]["cache"] for parts in asked)      # (one run: no cache to share)
    table = open(os.path.join(target.build, "pilot", image.name, "names-score.tsv")).read().splitlines()
    assert table[0].endswith("\tstyle\tgrounded\tgrounded_why\tsentence\tsentence_why")
    assert any(r.endswith("\tpartly\tvague\tright\t") and "\twait_ticks\t" in r for r in table), table
    assert not pilot.score_names(target, image, gizmo_elf(target), out=lines.append, grounded=llm.Scripted([]),
                                 sources=[str(tmp_path)])["grounded"]["verdicts"]       # (no source found)
    asked.clear()
    t = pilot.compare_names(target, image, gizmo_elf(target), str(other), None, out=lines.append,
                            grounded=llm.Scripted(answer), sources=[fw])
    assert (t["differ"], t["judged"], t["wins"], t["losses"], t["ties"]) == (2, 2, 1, 1, 0), t
    assert not any("serial_write_char" in parts[1]["text"] for parts in asked)       # (alike: not asked)
    assert all(parts[0]["cache"] for parts in asked)          # (two runs share each file's sources)
    pairwise = open(os.path.join(target.build, "pilot", image.name, "names-pairwise.tsv")).read()
    assert "\tpin_write\tpin_toggle\tthis\tright\twrong: it sets the pin\n" in pairwise
    assert "against the source by scripted" in lines[-4] and "the other where they differ, 2 of 2" in lines[-1]
    assert lines[-3].startswith("dread pilot --score --against: by source files, this run's closer in 0, the "
                                "other's in 0, even in 1"), lines[-3]       # (uart_putc, gpio_set, delay: hw.c)


def test_the_device_question_shows_the_texts_the_code_points_to(gizmo):
    """device_texts: the texts the code points to (literal pools and the tables they point to, read from
    the listing), counted once a function, the most used first; a text needs three letters (gizmo's
    "\\r\\n" has two); with [llm] only, the texts of the functions an LLM may be shown alone."""
    target, image, program, flow = gizmo
    texts = pilot.device_texts(target, program)
    shown = [t for t, _ in texts]
    assert dict(texts)["idle"] == 3 and dict(texts)["GIZMO 1000\\r\\n"] == 1, texts
    assert shown.index("idle") < shown.index("GIZMO 1000\\r\\n") and "\\r\\n" not in shown, shown
    assert [k for _, k in texts] == sorted((k for _, k in texts), reverse=True)
    question = pilot.device_question(target, program, texts)
    assert question.startswith("Processor: arm, t32, m, cortex-m3. Functions: %d." % len(program.functions))
    assert '"GIZMO 1000\\r\\n" (1)' in question, question
    target.llm_only = {"main"}
    assert sorted(t for t, _ in pilot.device_texts(target, program)) == [
        "GIZMO 1000\\r\\n", "READY\\r\\n", "crc ", "volume = 7\\nmode=2\\n  baud = 0x2580\\nname=3\\n"]


def test_the_device_most_answers_agree_with_is_kept():
    """choose_device: the answer whose words overlap most with the others', with the answers that
    overlap it by half or more; parse_device takes the device: line."""
    assert pilot.parse_device("Here.\ndevice: a test gadget  with a serial console.\n") == \
        "a test gadget with a serial console"
    assert pilot.parse_device("a gadget") is None and pilot.parse_device("device: .") is None
    assert pilot.choose_device(["a test gadget with a serial console", None, "test gadget, serial console",
                                "a coffee machine"]) == ("a test gadget with a serial console", 2)
    assert pilot.choose_device([None, None]) == (None, 0)
    assert pilot.choose_device(["unknown", "unknown", "a board"]) == ("unknown", 2)


def test_naming_questions_begin_with_what_the_image_is(gizmo, monkeypatch, capsys):
    """--task device asks --samples times and keeps the device most answers agree with in device.tsv
    (the conversation in device.md); the questions for globals, function names and groups then begin
    with it (DEVICE_READ), --no-device leaves it out, and a device the target states ([llm] device)
    comes first; a reading of "unknown" says nothing."""
    from dread import groups
    target, image, program, flow = gizmo
    answers = iter(["device: a test gadget with a serial console", "I think\ndevice: test gadget, serial console",
                    "device: a coffee machine"])
    seen = []

    def answer(messages):
        seen.append(messages[0]["content"])
        return next(answers)
    monkeypatch.setattr(llm, "load", lambda name=None, path=None: llm.Scripted(answer))
    assert pilot.main(["--target", target.root, "--task", "device", "--samples", "3"]) == 0
    out = capsys.readouterr().out
    assert "a test gadget with a serial console (2 of 3 answers agree" in out, out
    assert seen[0].startswith("Processor: ") and '"GIZMO 1000\\r\\n" (1)' in seen[0], seen
    row = pilot.read_device(target, image)
    assert row["device"] == "a test gadget with a serial console" and row["confidence"] == "0.67", row
    assert row["samples"] == "a test gadget with a serial console | test gadget, serial console | a coffee machine"
    assert os.path.exists(os.path.join(target.build, "pilot", image.name, "device.md"))
    line = pilot.DEVICE_READ.format(device="a test gadget with a serial console")
    assert pilot.known_device(target, image) == line

    asked = []

    def names(messages):
        asked.append(messages[0]["content"])
        if "readable C" in messages[0]["content"] or "```json" in "".join(m["content"] for m in messages[1:]):
            return "{}"
        return '```json\n{"g_2000004c": "tick_count"}\n```'
    monkeypatch.setattr(llm, "load", lambda name=None, path=None: llm.Scripted(names))
    assert pilot.main(["--target", target.root, "--task", "globals"]) == 0
    assert asked and asked[0].startswith(line + "\n\n"), asked[:1]
    text, _ = pilot.Namer(target, image, program, flow, device=line).question(program.by_name["main"])
    assert text.startswith(line + "\n\nThe function main"), text[:200]
    assert not pilot.Namer(target, image, program, flow).question(program.by_name["main"])[0].startswith(line)
    capsys.readouterr()
    assert pilot.main(["--target", target.root, "--task", "subsystems", "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "\n" + line + "\n\nThe group of " in printed, printed[:400]
    assert pilot.main(["--target", target.root, "--task", "subsystems", "--dry-run", "--no-device"]) == 0
    printed = capsys.readouterr().out
    assert line not in printed and "The group of " in printed

    touched = groups.evidence(target, image, program, flow)
    group = groups.find(target, image, program, flow, touched=touched)[0]
    stated = pilot.Namer(target, image, program, flow, device=pilot.DEVICE_STATED.format(device="a gadget"))
    assert pilot.subsystem_question(stated, group, touched).startswith(
        "What the image is, as its target states: a gadget.\n\nThe group of ")
    with open(target.file, "a") as f:
        f.write('\n[llm]\ndevice = "a gadget for tests."\n')
    again = Target.load(target.root)
    assert pilot.known_device(again, again.image()) == "What the image is, as its target states: a gadget for tests."
    with open(pilot.device_path(target, image), "a") as f:
        f.write("unknown\t1.00\tunknown\tscripted\t0\t0\n")
    assert pilot.known_device(target, image) == ""


def test_two_runs_are_compared_by_groups_too(gizmo, tmp_path):
    """group_sign counts a pairwise comparison by groups: the groups whose functions this run won more
    often, those the other won more often, the even ones, the sign test over the first two; a function
    outside every group is one of its own. pair_groups takes the run's groups.tsv where it holds the
    functions, else the truth ELF's source files, else nothing (a functions table as truth)."""
    target, image, program, flow = gizmo
    pairs = {1: "a", 2: "a", 3: "b", 4: "b", 5: "b", 6: "tie", 7: "a", 8: "b"}
    group = {1: "g", 2: "g", 3: "g", 4: "h", 5: "h", 6: "k", 7: "m", 8: "m"}
    assert pilot.group_sign(pairs, group) == (1, 1, 1, 1.0)            # (g 2:1, h 0:2, m 1:1; k only a tie)
    assert pilot.group_sign(pairs, {}) == (3, 4, 0, 1.0)               # (each alone: as by functions)
    assert pilot.group_sign({a: "a" for a in range(10)}, {})[3] == pilot.sign_test(10, 0)
    fns = [program.by_name[n] for n in ("uart_putc", "gpio_set", "led_on")]
    by_files, by = pilot.pair_groups(target, image, gizmo_elf(target), [fn.entry for fn in fns])
    assert by == "source files" and by_files[fns[0].entry] == by_files[fns[1].entry] != by_files[fns[2].entry]
    table = tmp_path / "functions.tsv"
    table.write_text("address\tname\n" + "".join("0x%x\t%s\n" % (fn.entry, fn.name) for fn in fns))
    assert pilot.pair_groups(target, image, str(table), [fn.entry for fn in fns]) == ({}, None)
    folder = os.path.join(target.build, "pilot", image.name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "groups.tsv"), "w") as f:
        f.write("address\tnow\tgroup\tmembers\n" + "".join(
            "0x%x\t%s\t%d\tk\n" % (fn.entry, fn.name, k) for k, fn in enumerate(fns)))
    by_groups, by = pilot.pair_groups(target, image, gizmo_elf(target), [fn.entry for fn in fns])
    assert by == "groups" and by_groups == {fn.entry: str(k) for k, fn in enumerate(fns)}


REPLICATES = {          # each run's proposals; the judge's verdict is the name's last word (ok, meh, bad)
    "memcpy": ("copy_ok", "copy_ok", "copy_bad", "move_bad"),
    "strlen": ("len_ok", "size_ok", "len_ok", "count_meh"),
    "checksum": ("sum_ok", "sum_ok", "sum_ok", "sum_ok"),
    "clamp": ("limit_meh", "clip_ok", "limit_ok", "bound_bad"),
    "uart_putc": ("put_ok", "put_bad", "out_ok", "put_ok"),
    "gpio_set": ("pin_ok", "pin_ok", "pin_meh", "set_meh"),
    "read_line": ("line_bad", "line_bad", "read_ok", "get_ok"),
    "print_status": ("show_ok", "status_ok", "show_bad", "print_meh")}


def test_settings_compared_over_replicate_runs(gizmo, tmp_path):
    """compare_runs (--against RUN... --vs RUN...): each run judged once against the source, every run of
    one setting paired with every run of the other from those verdicts (names that differ, the better
    verdict closer), counted by groups over all the pairings with a sign test, the runs' means with a
    permutation test over runs, the pairs within a setting as the noise floor; the grouping is the
    groups.tsv beside the runs where there is one, the source files beside it; judged again with the
    kept answers, nothing is asked."""
    pytest.importorskip("elftools")
    target, image, program, flow = gizmo
    fns = {name: program.by_name[name] for name in REPLICATES}
    runs = []
    for k, label in enumerate(("a1", "a2", "b1", "b2")):
        folder = tmp_path / label
        folder.mkdir()
        (folder / "names.tsv").write_text("address\tnow\tproposed\twhat\tprovider\tshown\n" + "".join(
            "0x%x\t%s\t%s\tdoes it\tp\treadable\n" % (fns[n].entry, n, REPLICATES[n][k]) for n in REPLICATES))
        runs.append(str(folder / "names.tsv"))
    verdict = {"ok": "right | right", "meh": "partly: vague | right", "bad": "wrong: no | right"}

    def answer(messages):
        numbered = re.findall(r"^(\d+)\. (\w+): ", messages[0]["content"][1]["text"], re.M)
        return "```json\n%s\n```" % json.dumps({n: verdict[name.rsplit("_", 1)[1]] for n, name in numbered})
    fw = os.path.join(os.path.dirname(target.root), "fw")
    kept = llm.AnswerCache(pilot.judge_cache_path(target, image))
    judge = llm.Scripted(answer)
    lines = []
    t = pilot.compare_runs(target, image, gizmo_elf(target), runs[:2], runs[2:], judge, out=lines.append,
                           sources=[fw], cache=kept)
    assert (t["functions"], t["judged"], len(judge.asked)) == (8, 8, 16)     # (4 files a run, 4 runs)
    assert t["means"] == {"a1": 6.5 / 8, "a2": 6 / 8, "b1": 5.5 / 8, "b2": 4.5 / 8}
    assert t["difference"] == pytest.approx(0.15625) and t["p_runs_means"] == pytest.approx(2 / 6)
    assert t["least_p"] == pytest.approx(2 / 6) and t["splits"] == 6 and t["every_split"]
    assert t["by"] == "source files"
    assert (t["groups_won"], t["groups_lost"], t["groups_even"]) == (3, 0, 1)    # (lib +6, util +1, hw +2, main 0)
    assert t["groups_p"] == pilot.sign_test(3, 0) and t["p_runs_groups"] == pytest.approx(2 / 6)
    assert t["majority"][:3] == (3, 0, 1) and t["functions_pooled"] == (16, 7, 3)
    assert t["pairings"][("a1", "b1")] == {"differ": 6, "wins": 3, "losses": 2, "ties": 1, "p": 1.0, "groups_won": 2,
                                           "groups_lost": 1, "groups_even": 1, "groups_p": 1.0}
    assert t["pairings"][("a2", "b1")]["groups_even"] == 2 and t["pairings"][("a2", "b1")]["groups_won"] == 1
    assert sorted(t["across"]) == [1, 1, 2, 3] and t["alike"] == [0, 1]       # (a1-a2 1:1; b1-b2 2:1)
    folder = os.path.join(target.build, "pilot", image.name)
    table = open(os.path.join(folder, "names-replicates.tsv")).read().splitlines()
    assert table[0] == "address\ttrue\tgroup\ta1\ta1_grounded\ta2\ta2_grounded\tb1\tb1_grounded\tb2\tb2_grounded"
    assert "0x%x\tclamp\tutil.c\tlimit_meh\tpartly: vague\tclip_ok\tright\tlimit_ok\tright\tbound_bad\twrong: no" % (
        fns["clamp"].entry) in table and len(table) == 9
    pairs = open(os.path.join(folder, "names-replicates-pairs.tsv")).read().splitlines()
    assert len(pairs) == 7 and "a1\tb2\tacross\t6\t5\t1\t0\t0.2188\t3\t0\t1\t0.25" in pairs, pairs
    assert any("permutation test over runs p = 0.333 (6 splits of the 4 runs; the least p possible 0.33)" in line
               for line in lines), lines
    assert any("a against b 4, from +1 to +3 (mean +1.8), 0 beyond chance" in line and
               "within a setting (the noise floor) 2, from +0 to +1 (mean size 0.5)" in line for line in lines), lines
    with open(tmp_path / "a1" / "groups.tsv", "w") as f:      # (lib and util one group, hw and main one each)
        f.write("address\tnow\tgroup\tmembers\n" + "".join("0x%x\t%s\t%d\tk\n" % (
            fns[n].entry, n, 0 if n in ("memcpy", "strlen", "checksum", "clamp") else 1 if n in ("uart_putc", "gpio_set")
            else 2) for n in REPLICATES))
    again = llm.Scripted([])
    kept = llm.AnswerCache(pilot.judge_cache_path(target, image))
    lines = []
    t = pilot.compare_runs(target, image, gizmo_elf(target), runs[:2], runs[2:], again, out=lines.append,
                           sources=[fw], cache=kept)
    assert not again.asked and kept.hits == 16                     # (every question answered from before)
    assert t["by"] == "groups" and (t["groups_won"], t["groups_lost"], t["groups_even"]) == (2, 0, 1)
    assert t["by_source_files"] == (3, 0, 1, pilot.sign_test(3, 0))
    assert any("16 answers kept from before" in line for line in lines[:1]), lines
    assert any(line.startswith("dread pilot --score --vs: by source files over all the pairings: a's names closer in "
                               "3, b's in 0, even in 1") for line in lines), lines


def test_runs_split_every_way_or_drawn():
    """run_splits: every split of the runs into the sizes compared, the observed one first, or beyond
    the limit the observed one and the rest drawn with a fixed seed (the same each time)."""
    every, whole = pilot.run_splits(6, 3)
    assert whole and len(every) == 20 and every[0] == (0, 1, 2) and len(set(every)) == 20
    drawn, whole = pilot.run_splits(10, 5, limit=50)
    assert not whole and len(drawn) == 50 and drawn[0] == tuple(range(5)) and all(len(s) == 5 for s in drawn)
    assert pilot.run_splits(10, 5, limit=50) == (drawn, False)
    assert pilot.group_nets([{1: "a", 2: "b"}, {1: "a", 3: "tie"}], {1: "g", 2: "g", 3: "h"}) == {"g": 1}
    assert pilot.net_sign({"g": 2, "h": -1, "k": 0}) == (1, 1, 1, 1.0)


def test_replicate_runs_need_both_sets_and_the_grounded_judge(gizmo, capsys):
    """--vs needs --against and --grounded; several --against need --vs. Each is refused before any
    provider is loaded."""
    target, image, program, flow = gizmo
    truth = gizmo_elf(target)
    base = ["--target", target.root, "--task", "name", "--score", truth]
    assert pilot.main(base + ["--vs", "b.tsv", "--grounded", "nothing"]) == 1
    assert "--vs needs --against" in capsys.readouterr().err
    assert pilot.main(base + ["--against", "a.tsv", "--vs", "b.tsv"]) == 1
    assert "--vs needs --grounded" in capsys.readouterr().err
    assert pilot.main(base + ["--against", "a.tsv", "a2.tsv", "--grounded", "nothing"]) == 1
    assert "several --against runs need --vs" in capsys.readouterr().err


# ---- the review of existing names (dread.namereview, --task review)

REVIEW_NAMELESS = ("uart_init", "uart_putc", "uart_puts", "uart_getc", "gpio_set", "led_on", "led_off", "delay",
                   "timer_start", "timer_irq", "irq_enable_timer", "parse_kv")


def reviewed_gizmo(gizmo, monkeypatch):
    """gizmo-m3 as a naming run leaves a stripped image: REVIEW_NAMELESS made FUN_address in its tables
    and proposed for (names.tsv), a person's vague name (beep_on in the tables called do_work), the
    groups of dread.groups with names (the UART's group misread as an SPI flash driver), and parse_kv
    pointing to another file's path. Returns (target, image, program, flow, {old name: name now})."""
    from dread import groups, namereview
    target, image, program, flow = gizmo
    now = {n: "FUN_%08x" % program.by_name[n].entry for n in REVIEW_NAMELESS}
    now["beep_on"] = "do_work"
    by_address = {"0x%x" % program.by_name[n].entry: new for n, new in now.items()}
    path = image.table("functions")
    rows = [r.split("\t") for r in open(path).read().splitlines()]
    with open(path, "w") as f:
        f.write("".join("\t".join([r[0], by_address.get(r[0], r[1])] + r[2:]) + "\n" for r in rows))
    path = image.table("symbols")
    lines = open(path).read().splitlines()
    with open(path, "w") as f:
        f.write("".join("f %s %s\n" % (l.split()[1], by_address[l.split()[1]]) if l.startswith("f ") and
                        l.split()[1] in by_address else l + "\n" for l in lines))
    target = Target.load(target.root)
    image = target.image()
    program = Program(target, image)
    flow = regflow.load(target, image, program)
    proposals = {"uart_init": "spi_init", "uart_putc": "spi_send_byte", "uart_puts": "spi_send_text",
                 "uart_getc": "spi_read_byte", "gpio_set": "led_pin_write", "led_on": "led_switch",
                 "led_off": "led_switch", "delay": "timer_wait", "timer_start": "timer_begin",
                 "timer_irq": "timer_tick_isr", "irq_enable_timer": "nvic_enable_timer_irq",
                 "parse_kv": "cfg_parse_pair"}
    for old, proposed in proposals.items():
        sure, samples = {"delay": ("0.40", "timer_wait busy_loop sleep_ms timer_wait wait_ticks"),
                         "gpio_set": ("0.80", "led_pin_write led_pin_write gpio_write led_pin_write led_pin_write")
                         }.get(old, ("1.00", " ".join([proposed] * 5)))
        pilot.keep_proposal(target, image, program.by_name[now[old]], proposed, "does %s" % proposed.replace("_", " "),
                            llm.Scripted([]), "readable", {"confidence": sure, "samples": samples, "pass": "1"})
    parts = {now["uart_putc"]: ("SPI flash driver", "spi_", "drives an SPI flash", "1.00"),
             now["led_off"]: ("LED and timer helpers", "led_", "lights the LEDs", "1.00"),
             now["delay"]: ("timer driver", "timer_", "counts time", "1.00"),
             "cmd_led": ("command parser", "cmd_", "parses commands", "1.00"),
             now["gpio_set"]: ("GPIO", "gpio_", "drives the pins", "0.60")}
    found = groups.find(target, image, program, flow)
    pilot.write_groups(target, image, program, found)
    for k, group in enumerate(found):
        for member, (name, prefix, what, sure) in parts.items():
            if member in group:
                pilot.keep_subsystem(target, image, {
                    "group": k, "first": "0x%x" % program.by_name[group[0]].entry, "functions": len(group),
                    "members": pilot.members_key(program, group), "name": name, "prefix": prefix, "what": what,
                    "confidence": sure, "samples": " ".join([prefix] * 5)})
    texts = namereview.Picture.texts
    monkeypatch.setattr(namereview.Picture, "texts", lambda self, n: ["/src/net/socket.c"] if n == now["parse_kv"]
                        else texts(self, n))
    return target, image, program, flow, now


def test_the_review_finds_reasons_against_names_by_code(gizmo, monkeypatch, capsys):
    """dread.namereview's triggers, computed by code: a split vote, two functions of one name, a vague
    name (a person's too), a name without its group's prefix, a helper of several parts named after
    one, a source file's path that its name does not match; for groups, tested at their edges: a
    described device's users in a group whose name does not say so, a device a group names but does not
    touch, few functions taking its prefix, its vote, the files its texts name. --dry-run prints the
    ranking and asks nothing."""
    from dread import namereview
    target, image, program, flow, now = reviewed_gizmo(gizmo, monkeypatch)
    picture = namereview.Picture(target, image, program, flow)
    assert picture.sources[now["delay"]] == "proposal" and picture.sources["do_work"] == "person"
    assert picture.sources["main"] == "person" and picture.name(now["uart_putc"]) == "spi_send_byte"

    def kinds(n):
        return sorted(r.kind for r in namereview.function_reasons(picture, n))
    assert kinds(now["delay"]) == ["split", "vote"]
    assert kinds(now["led_on"]) == kinds(now["led_off"]) == ["same"]
    assert kinds("do_work") == ["vague"] and kinds(now["irq_enable_timer"]) == ["prefix"]
    assert kinds(now["gpio_set"]) == ["shared", "vote"] and kinds(now["parse_kv"]) == ["file"]
    assert kinds(now["uart_puts"]) == [] and kinds("main") == []
    vote = namereview.function_reasons(picture, now["delay"])
    assert namereview.score_of(vote) == pytest.approx(2.0 * 0.6 + 0.5)
    assert "agreed 40 %" in vote[0].text and "4 readings" in vote[1].text
    assert "named the same: %s" % now["led_off"] in namereview.function_reasons(picture, now["led_on"])[0].text
    groups = {picture.subsystems[k]["prefix"]: sorted(r.kind for r in namereview.group_reasons(picture, k))
              for k in picture.subsystems}
    assert groups == {"spi_": ["device"], "led_": ["members", "unused"], "timer_": [], "cmd_": ["file", "members"],
                      "gpio_": ["members", "vote"]}, groups
    functions, ranked_groups = namereview.ranked(picture)
    assert functions[0][1] == now["delay"] and [x[0] for x in functions] == sorted((x[0] for x in functions),
                                                                                    reverse=True)
    assert [picture.subsystems[k]["prefix"] for _, k, _ in ranked_groups][:1] == ["cmd_"]
    assert pilot.main(["--target", target.root, "--task", "review", "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "would be challenged" in printed and "nothing asked" in printed and "do_work" in printed
    assert not os.path.exists(namereview.review_path(target, image))
    namer = pilot.Namer(target, image, program, flow)            # (a person's name given back confirms it)
    namer.subsystem = picture.subsystem_lines()
    result = namereview.challenge_function(llm.Scripted(lambda messages: "name: do_work\nwhat: works"), None, namer,
                                           picture, "do_work", namereview.function_reasons(picture, "do_work"),
                                           samples=2)
    assert (result["outcome"], result["name"], result["samples"]) == ("confirmed", "do_work", "do_work do_work")
    lines = []                                  # (a provider that answers nothing: one round, then it stops)
    totals = namereview.review(target, image, program, lambda: llm.Scripted([]), flow=flow, jobs=2, samples=1,
                               out=lines.append)
    assert totals["rounds"] == 1 and set(totals["functions"]) == {"no candidate"}, totals
    assert any("no challenge of round 1 got a candidate" in line for line in lines), lines


def review_answers(candidates, modes, seen):
    """A provider's answers for a review: a candidate question gets candidates[the function] (a group's
    question, found by its old prefix, the three lines of candidates["group " + prefix]); a decision
    question the verdict of modes[the function]: "new" (the candidate, whichever side it is on), "split"
    (the candidate when it is B, else same), "old", or "general" (a more general name)."""
    def answer(messages):
        question = messages[0]["content"]
        seen.append(question)
        if "\n\nThe two names:\n" in question:
            names = question.split("\n\nThe two names:\n")[1]
            prefixes = re.findall(r"^[AB]: .*\(prefix (\w+)\)", names, re.M)
            if prefixes:                        # (a group's: its key the old prefix)
                key = next("group " + p for p in prefixes if "group " + p in candidates)
                a, b = prefixes
                candidate = re.search(r"prefix: (\w+)", candidates[key]).group(1)
            else:
                key = re.search(r"The function (\w+)", question).group(1)
                a, b = re.findall(r"^[AB]: (\w+)", names, re.M)
                candidate = candidates[key]
            mode = modes[key]
            if mode == "general":
                return "better: general\nname: wait_ticks\nwhat: waits a number of ticks"
            if mode == "split":
                return "better: B" if b == candidate else "better: same"
            pick = (a == candidate) == (mode == "new")
            return "**better:** %s" % ("A" if pick else "B")
        if question.startswith("The group of"):
            prefix = re.search(r"named this part .*, with the prefix (\w+):", question).group(1)
            return candidates["group " + prefix]
        name = re.search(r"The function (\w+)", question).group(1)
        return "name: %s\nwhat: does it" % candidates[name]
    return answer


def test_a_challenged_name_changes_only_when_its_candidate_wins_both_orders(gizmo, monkeypatch, capsys):
    """--task review (dread.namereview.review), scripted: the misread group (its device's registers)
    gets a new name in both orders, and its functions, challenged in the same round as renamed, follow
    it, their questions showing the new line; a candidate that wins one order and ties the other leaves
    the old name (the margin); one alike to the old name confirms it; "general" in both orders gives the
    more general name; a person's name is flagged, never changed. Changes are new rows of names.tsv
    (the pass after the last, marked) and subsystems.tsv, each challenge a row of review.tsv."""
    from dread import namereview
    target, image, program, flow, now = reviewed_gizmo(gizmo, monkeypatch)
    candidates = {now["uart_init"]: "uart_init", now["uart_putc"]: "uart_putc", now["uart_puts"]: "uart_puts",
                  now["uart_getc"]: "uart_getc", now["gpio_set"]: "gpio_set_level", now["led_on"]: "led_turn_on",
                  now["led_off"]: "led_switch", now["delay"]: "timer_delay_ticks", now["parse_kv"]: "cfg_parse_pair",
                  "do_work": "beep_start",
                  "group spi_": "name: serial port driver\nprefix: uart_\nwhat: drives the UART",
                  "group cmd_": "name: commands\nprefix: cmd_\nwhat: parses commands",
                  "group led_": "name: LEDs and beeper\nprefix: led_\nwhat: lights the LEDs",
                  "group gpio_": "name: GPIO pins\nprefix: gpio_\nwhat: drives the pins"}
    modes = dict({now[n]: "new" for n in ("uart_init", "uart_putc", "uart_puts", "uart_getc", "led_on")},
                 **{now["gpio_set"]: "split", now["delay"]: "general", "do_work": "new", "group spi_": "new"})
    seen, lines = [], []
    totals = namereview.review(target, image, program, lambda: llm.Scripted(review_answers(candidates, modes, seen)),
                               flow=flow, jobs=1, samples=3, out=lines.append)
    assert dict(totals["groups"]) == {"changed": 1, "confirmed": 3}, totals
    assert dict(totals["functions"]) == {"changed": 5, "kept": 1, "confirmed": 2, "general": 1, "flagged": 1}, totals
    assert totals["rounds"] == 1, lines
    rows = namereview.read_review(namereview.review_path(target, image))
    outcome = {r["now"]: r["outcome"] for r in rows if r["kind"] == "function"}
    assert outcome == {now["uart_init"]: "changed", now["uart_putc"]: "changed", now["uart_puts"]: "changed",
                       now["uart_getc"]: "changed", now["led_on"]: "changed", now["led_off"]: "confirmed",
                       now["gpio_set"]: "kept", now["delay"]: "general", now["parse_kv"]: "confirmed",
                       "do_work": "flagged"}, outcome
    by = {r["now"]: r for r in rows}
    assert by[now["gpio_set"]]["votes"] == "new 1, same 1" and by[now["delay"]]["votes"] == "general 2"
    assert by["do_work"]["candidate"] == "beep_start" and by["do_work"]["name"] == "do_work"
    assert by[now["uart_puts"]]["reasons"].startswith("renamed 1.00: its group was renamed from SPI flash driver")
    assert all(r["review"] == "1" and r["round"] == "1" for r in rows)
    group = [r for r in rows if r["kind"] == "group" and r["outcome"] == "changed"][0]
    assert (group["old"], group["name"], group["votes"]) == ("SPI flash driver (spi_)", "serial port driver (uart_)",
                                                             "new 2")
    names = pilot.last_rows(pilot.read_name_rows(pilot.names_path(target, image)))
    led = names[program.by_name[now["led_on"]].entry]
    assert (led["proposed"], led["pass"], led["confidence"]) == ("led_turn_on", "2", "1.00")
    assert led["shown"].endswith(namereview.REVIEWED) and names[program.by_name[now["delay"]].entry]["proposed"] == \
        "wait_ticks"
    assert names[program.by_name[now["gpio_set"]].entry]["proposed"] == "led_pin_write"
    assert "do_work" in open(image.table("functions")).read() and program.by_name["do_work"].entry not in names
    subsystems = pilot.read_subsystems(pilot.subsystems_path(target, image))
    assert subsystems[group["group"]]["prefix"] == "uart_"
    from dread import relations                 # (what dread gui shows of it: namereview.standing, certainty)
    names_now = relations.Names(target, image, program)
    about = {n: names_now.about(program.by_name[now.get(n, n)]) for n in ("gpio_set", "uart_init", "delay", "led_off",
                                                                             "led_on", "do_work")}
    assert {n: (a["certainty"], (a["review"] or {}).get("mark")) for n, a in about.items()} == {
        "gpio_set": ("likely", "suspect"), "uart_init": ("sure", "changed"), "delay": ("unvoted", "changed"),
        "led_off": ("sure", "confirmed"), "led_on": ("sure", "changed"), "do_work": ("named", "flagged")}
    assert about["led_on"]["review"]["last"]["old"] == "led_switch" and about["led_on"]["confidence"] == 1.0
    assert names_now.group_review(group["group"], subsystems[group["group"]])["mark"] == "changed"
    decisions = [q for q in seen if "\n\nThe two names:\n" in q and "The function %s," % now["led_on"] in q]
    assert len(decisions) == 2 and "\nA: led_switch: " in decisions[0] and "\nA: led_turn_on: " in decisions[1]
    puts = [q for q in seen if "The function %s," % now["uart_puts"] in q and "The two names" not in q]
    assert puts and all("named that part serial port driver, with the prefix uart_" in q for q in puts)
    assert "Tools that compare names with the image found reasons" in puts[0]
    confirmed = [q for q in seen if "The two names" in q and "The function %s," % now["led_off"] in q]
    assert confirmed == []                      # (alike to the old name: no decision asked)
    assert os.path.exists(os.path.join(target.build, "pilot", image.name, "review", now["led_on"] + ".md"))
    seen.clear()                                # (a review again: nothing with the same name and reasons)
    again = namereview.review(target, image, program, lambda: llm.Scripted(review_answers(candidates, modes, seen)),
                              flow=flow, jobs=1, samples=3, out=lines.append)
    assert again["rounds"] == 0 and seen == [] and len(namereview.read_review(namereview.review_path(
        target, image))) == len(rows)
    loaded = []

    def load(name=None, path=None):
        loaded.append(name)
        return llm.Scripted(review_answers(candidates, modes, seen))
    monkeypatch.setattr(llm, "load", load)
    capsys.readouterr()
    assert pilot.main(["--target", target.root, "--task", "review", "--provider", "p", "--decider", "d",
                       "--samples", "3"]) == 0
    assert "dread pilot --task review: 0 rounds; groups: none; functions: none" in capsys.readouterr().out
    assert loaded[:2] == ["p", "d"] and seen == []


def test_what_the_review_says_of_the_name_a_function_has_now():
    """namereview.standing: the last row about the name a function goes by gives its mark; a name an
    earlier review gave stays "changed" when a later one confirms it; rows about another name are stale;
    the reasons split where a kind and a weight begin, not at every "; "."""
    from dread import namereview
    rows = [{"review": "1", "round": "1", "outcome": "changed", "old": "a", "name": "b", "reasons": "vote 1.20: x; y"},
            {"review": "2", "round": "1", "outcome": "confirmed", "old": "b", "name": "b",
             "reasons": "same 1.00: p; q; vague 1.00: z"}]
    got = namereview.standing(rows, "b")
    assert got["mark"] == "changed" and got["changed"]["old"] == "a" and got["last"]["outcome"] == "confirmed"
    assert [r["kind"] for r in got["last"]["reasons"]] == ["same", "vague"] and got["last"]["reasons"][0]["text"] == "p; q"
    assert [r["text"] for r in got["changed"]["reasons"]] == ["x; y"] and got["challenges"] == 2
    assert namereview.standing(rows, "c") == {"mark": None, "last": None, "changed": None, "stale": 2, "challenges": 2}
    assert namereview.standing(rows + [dict(rows[1], outcome="kept", review="3")], "b")["mark"] == "suspect"
    assert namereview.standing([dict(rows[0], outcome="flagged", name="a")], "a")["mark"] == "flagged"
    history = namereview.standing(rows, "a", history=True)["rows"]
    assert [r["stale"] for r in history] == [True, True] and history[0]["review"] == 1
    assert namereview.standing([], "b") is None


def test_a_decision_takes_both_orders_and_a_margin():
    """decide(): the old name and the candidate asked as A and B and then as B and A; a verdict that
    cannot be read is asked again; the change needs the margin over the old name's and "same"'s votes
    together; parse_decision reads "better:" lines, "general" only with its name."""
    from dread import namereview
    old, new = ("led_switch", "toggles"), ("led_turn_on", "lights it")

    def decider(first, second):
        answers = [first, second]
        return llm.Scripted(lambda messages: answers.pop(0))
    assert namereview.decide(decider("better: B", "better: A"), "code", old, new)[:2] == (
        "new", {"old": 0, "new": 2, "same": 0, "general": 0, "none": 0})
    assert namereview.decide(decider("better: B", "better: same"), "code", old, new)[0] == "old"
    assert namereview.decide(decider("better: B", "better: same"), "code", old, new, margin=0)[0] == "new"
    assert namereview.decide(decider("better: A", "better: A"), "code", old, new)[0] == "old"
    answers = ["no idea", "better: B", "better: A"]
    outcome, votes, _, log = namereview.decide(llm.Scripted(lambda m: answers.pop(0)), "code", old, new)
    assert outcome == "new" and votes["none"] == 0 and any("asked again" in part for part in log)
    general = "better: general\nname: led_set\nwhat: sets an LED"
    outcome, votes, chosen, _ = namereview.decide(decider(general, general), "code", old, new)
    assert (outcome, chosen) == ("general", ("led_set", "sets an LED"))
    assert namereview.parse_decision("**Better**: `b`") == ("b", None)
    assert namereview.parse_decision("better: general") == (None, None)
    assert namereview.parse_decision("better: general\nname: x_\nprefix: x\nwhat: y", group=True) == (
        "general", ("x_", "x_", "y"))
    assert namereview.parse_decision("I prefer A") == (None, None)
    assert namereview.alike("TaskDelay", "task_delay") and namereview.alike("queue_send_back", "queue_back_send")
    assert not namereview.alike("task_list_insert", "port_list_insert")     # (another part: decided, not confirmed)


def test_no_thinking_makes_the_naming_providers_quick(gizmo, monkeypatch, capsys):
    """--no-thinking: every provider the naming tasks make is asked to be quick (llm.Provider.quick);
    --thinking: none is; without either, the task's default (THINKING)."""
    target, image, program, flow = gizmo
    made = []

    def load(name=None, path=None):
        made.append(llm.Scripted(lambda messages: "name: x_%d\nwhat: does it" % len(made)))
        return made[-1]
    monkeypatch.setattr(llm, "load", load)
    assert pilot.main(["--target", target.root, "--task", "name", "--no-thinking", "--no-subsystems", "--samples", "1",
                       "--passes", "1", "--no-device", "led_on"]) == 0
    assert made and all(p.quick for p in made)
    made.clear()
    shutil.rmtree(os.path.join(target.build, "pilot"))
    assert pilot.main(["--target", target.root, "--task", "name", "--no-subsystems", "--samples", "1", "--passes", "1",
                       "--no-device", "led_on"]) == 0
    assert made and all(p.quick != pilot.THINKING["name"] for p in made)    # (the task's default)
    made.clear()
    shutil.rmtree(os.path.join(target.build, "pilot"))
    assert pilot.main(["--target", target.root, "--task", "name", "--thinking", "--no-subsystems", "--samples", "1",
                       "--passes", "1", "--no-device", "led_on"]) == 0
    assert made and not any(p.quick for p in made)
