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
        assert local.last == {"finish": "stop", "tokens": 12, "reasoning": 3, "input": None}
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
    priced = llm.Provider("cloud", {"model": "m", "price_in": 3, "price_out": 15})
    priced.last = {"finish": "end_turn", "tokens": 1000, "input": 500000}
    priced.record()                     # its own count, not the ledger's (runs beside it share that)
    assert priced.spent() == {"answers": 1, "input": 500000, "output": 1000, "cost": 1.5 + 0.015}
    assert p.spent()["answers"] == 2 and p.spent()["cost"] is None
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
        "both": 4, "differ": 3, "judged": 3, "wins": 1, "losses": 1, "ties": 1, "p": 1.0}
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
    """dread pilot --task name asks each function five times and runs a second pass, callers first
    (measured on FreeRTOS, docs/decisions.md 2026-10-22); --refine is a second pass of its own."""
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
    assert (got["samples"], got["passes"], got["gate"], got["ablate"]) == (5, 2, None, False)
    assert pilot.main(["--target", target.root, "--task", "name", "--refine", "--samples", "1"]) == 0
    assert (got["samples"], got["passes"], got["refine"]) == (1, 1, True)


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
