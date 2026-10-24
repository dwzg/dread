"""dread.llm's command provider (a command line that answers: an agent's headless mode, a local
model's wrapper) and the dread llm command, with a stand-in program that reports what it was given."""
import json
import os
import sys

import pytest

from dread import llm

STANDIN = """import json, os, sys
args = sys.argv[1:]
if args[0] == "arg":
    question = args[1]
elif args[0] == "file":
    question = open(args[1]).read()
else:
    question = sys.stdin.read()
if "please fail" in question:
    sys.stderr.write("stand-in: asked to fail\\n")
    sys.exit(3)
print(json.dumps({"question": question, "argv": args, "cwd": sorted(os.listdir(".")),
                  "key": "DREAD_TEST_KEY" in os.environ, "token": os.environ.get("GH_TOKEN")}))
"""


@pytest.fixture
def standin(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "USAGE", str(tmp_path / "usage.tsv"))
    path = tmp_path / "answer.py"
    path.write_text(STANDIN)
    return [sys.executable, str(path)]


def test_a_command_gets_the_question_as_an_argument_a_file_or_its_input(standin, monkeypatch):
    """The conversation goes in as one text, in {prompt}, a {prompt_file} or on standard input; the
    command runs in an empty directory of its own, without the environment's keys (a token it needs
    for itself, such as GH_TOKEN, stays)."""
    monkeypatch.setenv("DREAD_TEST_KEY", "secret")
    monkeypatch.setenv("GH_TOKEN", "the cli's own")
    for mode, extra in (("arg", ["{prompt}"]), ("file", ["{prompt_file}"]), ("stdin", [])):
        p = llm.Command("cmd", {"command": standin + [mode] + extra + ["--model={model}"], "model": "m1"})
        out = json.loads(p.ask("be brief", [{"role": "user", "content": "hello"}]))
        assert out["question"] == "be brief\n\nhello", mode
        assert out["argv"][-1] == "--model=m1"
        assert out["cwd"] == (["question.txt"] if mode == "file" else []), mode
        assert out["key"] is False and out["token"] == "the cli's own"
        assert p.last["finish"] == "stop" and p.last["tokens"] is None


def test_a_conversation_goes_in_with_its_roles(standin):
    p = llm.Command("cmd", {"command": standin + ["stdin"]})
    out = json.loads(p.ask("be brief", [{"role": "user", "content": "first"}, {"role": "assistant", "content": "x"},
                                        {"role": "user", "content": "second"}]))
    text = out["question"]
    assert text.startswith("be brief\n\n") and text.index("=== USER ===\nfirst") < text.index(
        "=== ASSISTANT ===\nx") < text.index("=== USER ===\nsecond")


def test_what_a_command_provider_refuses_or_reports(standin, monkeypatch):
    """A failing command, a missing one and a question too long for one argument are the
    provider's errors, with the command's own words."""
    p = llm.Command("cmd", {"command": standin + ["arg", "{prompt}"]})
    with pytest.raises(llm.ProviderError, match="exit 3: stand-in: asked to fail"):
        p.ask("s", [{"role": "user", "content": "please fail"}])
    with pytest.raises(llm.ProviderError, match="no command"):
        llm.Command("cmd", {"command": ["/nonexistent/answer"]}).ask("s", [{"role": "user", "content": "q"}])
    with pytest.raises(llm.ProviderError, match="list of arguments"):
        llm.Command("cmd", {"command": "copilot -p"}).ask("s", [])
    monkeypatch.setattr(llm.Command, "ARG_MAX", 10)
    with pytest.raises(llm.ProviderError, match="too long for one argument"):
        p.ask("s", [{"role": "user", "content": "a question of more than ten bytes"}])
    stdin = llm.Command("cmd", {"command": standin + ["stdin"]})        # standard input has no such limit
    assert "more than ten" in json.loads(stdin.ask("s", [{"role": "user", "content": "more than ten bytes"}]))["question"]


def test_the_llm_command_lists_and_tries_providers(standin, tmp_path, monkeypatch, capsys):
    config = tmp_path / "llm.toml"
    config.write_text('default = "here"\n[providers.here]\nkind = "command"\nmodel = "stand-in"\ncommand = %s\n'
                      '[providers.cloud]\nkind = "anthropic"\nmodel = "c"\n' % json.dumps(standin + ["arg", "{prompt}"]))
    monkeypatch.setenv("DREAD_LLM", str(config))
    assert llm.main(["providers"]) == 0
    listed = capsys.readouterr().out
    assert "here" in listed and "(default)" in listed and "cloud" in listed
    assert llm.main(["ask", "--system", "S", "what", "now"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["question"] == "S\n\nwhat now" and "here (stand-in)" in captured.err
    assert llm.main(["ask", "--provider", "nowhere", "q"]) == 1


def test_answers_are_kept_by_their_question(tmp_path):
    """AnswerCache: a kept answer comes back for the same question (the provider's kind and model, the
    system part, the messages' texts and the attempt), from the file in a later process too; a part's
    cache mark does not change the key, another attempt, model or text does; a line the file cannot
    parse is skipped."""
    path = str(tmp_path / "pilot" / "answers.jsonl")
    one = llm.Scripted([], name="judge")
    question = [{"role": "user", "content": [{"text": "sources", "cache": True}, {"text": "names"}]}]
    plain = [{"role": "user", "content": [{"text": "sources"}, {"text": "names"}]}]
    kept = llm.AnswerCache(path)
    key = kept.key(one, "system", question)
    assert key == kept.key(one, "system", plain) and kept.get(key) is None and kept.hits == 0
    kept.put(key, '{"1": "right"}')
    assert kept.key(one, "system", question, attempt=1) != key
    assert kept.key(one, "another system", question) != key
    other = llm.Scripted([], name="judge")
    other.model = "another"
    assert kept.key(other, "system", question) != key
    with open(path, "a") as f:
        f.write('{"key": "cut off\n')
    again = llm.AnswerCache(path)
    assert again.get(key) == '{"1": "right"}' and again.hits == 1 and len(again.answers) == 1
    assert llm.AnswerCache(None).get(key) is None


# ---- Message Batches and the prompt cache ----

class BatchServer:
    """A stand-in for Anthropic's batch endpoints (and /v1/messages, for a question asked again alone):
    a batch ends at the second look; its results come in reverse order, the second question's errored
    as an invalid request, the third's as the server's own error (asked again one by one)."""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading
        server = self
        self.batches, self.looks, self.cancelled, self.alone = {}, {}, [], []

        class Handler(BaseHTTPRequestHandler):
            def reply(self, data, kind="application/json"):
                data = data if isinstance(data, bytes) else json.dumps(data).encode()
                self.send_response(200)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                if self.path == "/v1/messages/batches":
                    bid = "msgbatch_%d" % len(server.batches)
                    server.batches[bid] = body["requests"]
                    server.looks[bid] = 0
                    return self.reply({"id": bid, "processing_status": "in_progress"})
                if self.path.endswith("/cancel"):
                    server.cancelled.append(self.path.split("/")[-2])
                    return self.reply({"processing_status": "canceling"})
                server.alone.append(body)
                return self.reply({"content": [{"type": "text", "text": "alone: " + body["messages"][0]["content"]}],
                                   "stop_reason": "end_turn", "usage": {"input_tokens": 3, "output_tokens": 2}})

            def do_GET(self):
                if self.path.startswith("/v1/messages/batches?"):
                    return self.reply({"data": [{"id": bid, "processing_status": "in_progress", "created_at":
                                                 "2026-10-24T10:00:00Z", "request_counts": {"processing": len(r)}}
                                                for bid, r in reversed(list(server.batches.items()))]})
                parts = self.path.strip("/").split("/")
                bid = parts[3]
                if parts[-1] == "results":
                    lines = []
                    for k, request in reversed(list(enumerate(server.batches[bid]))):
                        question = request["params"]["messages"][0]["content"]
                        if k == 1:
                            result = {"type": "errored", "error": {"type": "error", "error": {
                                "type": "invalid_request_error", "message": "max_tokens: too large"}}}
                        elif k == 2:
                            result = {"type": "errored", "error": {"type": "error", "error": {
                                "type": "api_error", "message": "internal"}}}
                        else:
                            result = {"type": "succeeded", "message": {
                                "content": [{"type": "text", "text": "batch: " + question}], "stop_reason": "end_turn",
                                "usage": {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 50,
                                          "cache_creation_input_tokens": 0}}}
                        lines.append(json.dumps({"custom_id": request["custom_id"], "result": result}))
                    return self.reply(("\n".join(lines) + "\n").encode(), "application/binary")
                server.looks[bid] += 1
                ended = server.looks[bid] >= 2
                return self.reply({"id": bid, "processing_status": "ended" if ended else "in_progress",
                                   "results_url": "%s/v1/messages/batches/%s/results" % (server.url, bid)
                                   if ended else None})

            def log_message(self, *args):
                pass
        self.http = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.http.server_port
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()


def test_a_batch_is_submitted_polled_and_matched_back(tmp_path, monkeypatch):
    """ask_batch: the questions go as one Message Batch, which is looked at until it has ended; its
    results, in any order, are matched back by custom_id; an errored result fails with the API's words,
    one of the server's kind is asked again on its own; the answers are recorded as a batch's (the
    ledger's mode column, the batch rate)."""
    ledger = tmp_path / "usage.tsv"
    monkeypatch.setattr(llm, "USAGE", str(ledger))
    monkeypatch.setenv("TEST_KEY", "secret")
    server = BatchServer()
    try:
        p = llm.Anthropic("claude", {"url": server.url, "model": "c", "key_env": "TEST_KEY", "batch_poll": 0,
                                     "price_in": 1, "price_out": 5})
        lines = []
        replies = llm.ask_batch(p, [("s", [{"role": "user", "content": "q%d" % k}], None) for k in range(4)],
                                out=lines.append)
    finally:
        server.close()
    assert [r[0] for r in replies] == ["batch: q0", None, "alone: q2", "batch: q3"]
    assert "max_tokens: too large" in str(replies[1][2]) and replies[0][2] is None
    assert replies[0][1]["cache_read"] == 50 and replies[0][1]["finish"] == "end_turn"
    (bid, requests), = server.batches.items()
    assert [r["custom_id"] for r in requests] == ["q0", "q1", "q2", "q3"] and server.looks[bid] == 2
    assert requests[0]["params"]["system"] == "s" and requests[0]["params"]["model"] == "c"
    assert [b["messages"][0]["content"] for b in server.alone] == ["q2"]
    rows = [line.split("\t") for line in ledger.read_text().splitlines()[1:]]
    assert [r[8] for r in rows] == ["batch", "batch", ""]    # (two of the batch, the one asked alone)
    spent = p.spent()
    assert spent["answers"] == 3 and spent["batch"] == 2
    assert spent["cost"] == pytest.approx(2 * (100 * 1 + 50 * 0.1 + 10 * 5) / 1e6 * 0.5 + (3 * 1 + 2 * 5) / 1e6)
    assert any("as 1 Message Batch (msgbatch_0)" in line for line in lines), lines


def test_a_batch_that_does_not_end_is_cancelled():
    """A batch still running after batch_wait seconds is cancelled; its questions fail with an error
    that says so (and how to wait longer)."""
    p = llm.Scripted(["a", "b"], batches=True, never=True)
    p.config["batch_wait"] = 0
    lines = []
    replies = llm.ask_batch(p, [("s", [{"role": "user", "content": q}], None) for q in "xy"], out=lines.append)
    assert p.cancelled == ["batch_0"] and all(r[0] is None for r in replies)
    assert "did not end within 0 s" in str(replies[0][2]) and any("batch_wait" in line for line in lines)


def talk(question, good):
    """A conversation for the tests: asks question, and asks again (once) when the answer is not good."""
    answer, last, error = yield ("s", [{"role": "user", "content": question}], None)
    if error is None and answer != good:
        answer, last, error = yield ("s", [{"role": "user", "content": question},
                                           {"role": "assistant", "content": answer},
                                           {"role": "user", "content": "again"}], None)
    return answer if error is None else "error: %s" % error


def test_conversations_go_in_rounds_of_batches():
    """converse_all: the first questions of all the conversations go as one batch; the answers that are
    not good ask again in the next round, another batch when they are batch or more, else one at a time;
    the results come back in the conversations' order, as converse() gets them one by one."""
    def answer(messages):
        question = messages[0]["content"]
        return question.upper() if len(messages) > 1 or question != "b" else "no"
    p = llm.Scripted(answer, batches=True)
    got = llm.converse_all(p, [talk(q, q.upper()) for q in "abc"], batch=1)
    assert got == ["A", "B", "C"] and [len(b["requests"]) for b in p.submitted] == [3, 1]
    assert p.submitted[1]["requests"][0][1]["messages"][-1]["content"] == "again"
    alone = llm.Scripted(answer, batches=True)      # (the round of one asks one at a time with batch 2)
    assert llm.converse_all(alone, [talk(q, q.upper()) for q in "abc"], batch=2) == ["A", "B", "C"]
    assert [len(b["requests"]) for b in alone.submitted] == [3] and len(alone.asked) == 4
    plain = llm.Scripted(answer)                     # (a provider without batches, or batch 0)
    assert llm.converse_all(plain, [talk(q, q.upper()) for q in "abc"], batch=1) == ["A", "B", "C"]
    assert llm.converse(llm.Scripted(answer), talk("b", "B")) == "B"


def test_a_lead_writes_the_cache_before_the_others_read_it():
    """converse_all's leads: a conversation with a lead begins once its lead has ended (strict: a round
    of its own, also as batches); not strict, it joins its lead's batch, after it."""
    p = llm.Scripted(lambda messages: messages[0]["content"].upper(), batches=True)
    assert llm.converse_all(p, [talk(q, q.upper()) for q in "abc"], batch=1, leads=[None, 0, 0]) == ["A", "B", "C"]
    assert [[c for c, _ in b["requests"]] for b in p.submitted] == [["q0"], ["q0", "q1"]]
    loose = llm.Scripted(lambda messages: messages[0]["content"].upper(), batches=True)
    assert llm.converse_all(loose, [talk(q, q.upper()) for q in "abc"], batch=1, leads=[None, 0, 0],
                            strict=False) == ["A", "B", "C"]
    assert [[p["messages"][0]["content"] for _, p in b["requests"]] for b in loose.submitted] == [["a", "b", "c"]]
    one = llm.Scripted(lambda messages: messages[0]["content"].upper())    # (one at a time: the lead first)
    llm.converse_all(one, [talk(q, q.upper()) for q in "abc"], leads=[None, 0, 0], strict=False)
    assert [m[0]["content"] for _, m in one.asked] == ["a", "b", "c"]


def test_a_part_is_marked_for_the_cache_only_where_it_reaches_the_minimum():
    """Anthropic's blocks: a part marked for the cache gets cache_control only where the prompt up to its
    end reaches the provider's cache_min (estimated at chars_per_token); without cache_min every mark
    goes out; "1h" keeps it an hour; parts after the first begin with the blank line content_text joins
    them with, so that the model reads the text the answer cache keys."""
    parts = [{"text": "x" * 300, "cache": True}, {"text": "the rest"}]
    short = llm.Anthropic("c", {"model": "m", "cache_min": 512, "chars_per_token": 3})
    assert all("cache_control" not in b for b in short.blocks(parts, "s" * 100))     # (400 / 3 < 512)
    assert not short.cacheable("s" * 100, parts)
    long = short.blocks(parts, "s" * 1300)                                           # (1600 / 3 >= 512)
    assert long[0]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in long[1]
    assert short.cacheable("s" * 1300, parts) and not short.cacheable("s" * 1300, "a text")
    assert "".join(b["text"] for b in long) == llm.content_text(parts)
    unknown = llm.Anthropic("c", {"model": "m"})        # (no cache_min: a caller's mark goes out, but the
    hour = unknown.blocks([{"text": "y", "cache": "1h"}])  # provider claims no cache of its own)
    assert hour[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert not unknown.cacheable("s" * 9000, parts) and llm.Provider("p", {}).cacheable("s", parts) is False


def test_a_quick_question_takes_the_configurations_fields():
    """A provider asked to be quick (dread pilot --no-thinking) adds its configuration's quick fields to
    the request (Haiku 5.5: thinking disabled), and nothing without them or when not quick."""
    quick = {"thinking": {"type": "disabled"}}
    p = llm.Anthropic("c", {"model": "m", "quick": quick})
    assert "thinking" not in p.params("s", [{"role": "user", "content": "q"}])
    p.quick = True
    assert p.params("s", [{"role": "user", "content": "q"}])["thinking"] == {"type": "disabled"}
    plain = llm.Anthropic("c", {"model": "m"})
    plain.quick = True
    assert "thinking" not in plain.params("s", [{"role": "user", "content": "q"}])
    local = llm.OpenAICompatible("l", {"model": "m", "quick": {"chat_template_kwargs": {"enable_thinking": False}}})
    local.quick = True
    assert local.quick_fields() == {"chat_template_kwargs": {"enable_thinking": False}}


def test_a_terminated_run_cancels_the_batch_it_waits_for(tmp_path):
    """SIGTERM while a Message Batch is waited for (llm.ending_on_signals, as dread pilot's main has it):
    the batch is cancelled before the program ends (status 143), not left running at a price."""
    import os
    import signal
    import subprocess
    import time
    script = tmp_path / "wait.py"
    script.write_text("from dread import llm\n"
                      "p = llm.Scripted(['a'], batches=True, never=True)\n"
                      "p.config['batch_poll'] = 0.05\n"
                      "cancel = p.batch_cancel\n"
                      "p.batch_cancel = lambda bid: (print('cancelled', bid, flush=True), cancel(bid))[1]\n"
                      "with llm.ending_on_signals():\n"
                      "    llm.ask_batch(p, [('s', [{'role': 'user', 'content': 'x'}], None)],\n"
                      "                  out=lambda s: print(s, flush=True))\n")
    root = os.path.dirname(os.path.dirname(os.path.abspath(llm.__file__)))
    child = subprocess.Popen([sys.executable, "-c", "exec(open(%r).read())" % str(script)], cwd=root,
                             stdout=subprocess.PIPE, text=True)
    first = child.stdout.readline()
    assert "as 1 Message Batch" in first, first
    time.sleep(0.2)
    child.send_signal(signal.SIGTERM)
    rest, _ = child.communicate(timeout=30)
    assert "cancelled batch_0" in rest and child.returncode == 143, (rest, child.returncode)


def test_a_forked_worker_still_ends_on_sigterm():
    """Inside ending_on_signals a forked child that gets SIGTERM ends as it would without it (killed by
    the signal), and a pool of forked workers, which ends them with SIGTERM, closes: on 2026-10-24 the
    inherited handler made a pool's worker raise instead, wait on a lock, and its parent wait for it."""
    import multiprocessing
    import signal
    import time
    with llm.ending_on_signals():
        context = multiprocessing.get_context("fork")
        child = context.Process(target=time.sleep, args=(30,))
        child.start()
        time.sleep(0.2)
        child.terminate()
        child.join(10)
        assert child.exitcode == -signal.SIGTERM
        with context.Pool(2) as pool:
            assert pool.map(abs, [-1, -2, -3]) == [1, 2, 3]


def test_the_llm_command_lists_and_cancels_batches(tmp_path, monkeypatch, capsys):
    """dread llm batches: a provider's last Message Batches with how they stand; --cancel cancels those
    named (a run killed outright cannot cancel its own)."""
    server = BatchServer()
    try:
        config = tmp_path / "llm.toml"
        config.write_text('default = "c"\n[providers.c]\nkind = "anthropic"\nmodel = "m"\nurl = "%s"\n'
                          'key_env = "TEST_KEY"\n[providers.local]\nkind = "openai"\nmodel = "q"\n' % server.url)
        monkeypatch.setenv("DREAD_LLM", str(config))
        monkeypatch.setenv("TEST_KEY", "secret")
        p = llm.load("c")
        p.batch_submit([("q0", {"model": "m"}), ("q1", {"model": "m"})])
        assert llm.main(["batches"]) == 0
        listed = capsys.readouterr().out
        assert "msgbatch_0  in_progress 2026-10-24T10:00:00  processing 2" in listed, listed
        assert llm.main(["batches", "--cancel", "msgbatch_0"]) == 0 and server.cancelled == ["msgbatch_0"]
        assert llm.main(["batches", "--provider", "local"]) == 1
    finally:
        server.close()


PRICES = {"price_in": 1.0, "price_out": 5.0, "price_batch": 0.5}    # (US dollars per million tokens)


def test_a_meter_asks_only_what_fits_the_budget_and_counts_the_runs_spend():
    """A run's meter (llm.Meter, metered): a question's estimate takes its input from its text (3 characters
    a token) and its output from OUT_TOKENS before an answer of its kind, then from the mean of its kind's
    answers; Provider.ask admits a question only when the estimate fits what is left of the budget after
    the spend, counts its answer's cost (the ledger's price) into the spend, numbers the answer and makes
    it an event; the first question that does not fit is not asked (BudgetReached), marks the budget
    reached and is an event once; a provider's error frees what its question reserved."""
    events = []
    p = llm.Scripted(lambda messages: {"text": "ok", "input": 1000, "tokens": 200}, prices=PRICES)
    meter = llm.Meter(budget=0.03, emit=lambda kind, **fields: events.append((kind, fields)))
    system, question = "s" * 300, [{"role": "user", "content": "x" * 2700}]    # (3,000 characters: 1,000 tokens)
    assert meter.estimate(p, system, question) == pytest.approx((1000 * 1.0 + llm.OUT_TOKENS * 5.0) / 1e6)
    with llm.metered(meter):
        assert llm.METER is meter and p.ask(system, question) == "ok"
        assert meter.spent == pytest.approx((1000 + 200 * 5) / 1e6) and meter.reserved == pytest.approx(0)
        assert p.last["cost"] == pytest.approx(0.002) and p.last["answer"] == 1
        assert meter.estimate(p, system, question) == pytest.approx((1000 + 200 * 5.0) / 1e6)   # (its kind's mean)
        for _ in range(14):
            p.ask(system, question)
        with pytest.raises(llm.BudgetReached) as refused:     # (0.030 spent, 0.002 more would pass 0.03)
            p.ask(system, question)
        assert str(refused.value).startswith("not asked") and meter.reached == str(refused.value)
        with pytest.raises(llm.BudgetReached):
            p.ask(system, question)
    assert llm.METER is None and len(p.asked) == 15 and meter.spent <= 0.03 + 1e-12
    answers = [f for kind, f in events if kind == "answer"]
    assert [a["n"] for a in answers] == list(range(1, 16)) and answers[-1]["spent"] == pytest.approx(0.03)
    assert answers[0]["estimate"] == pytest.approx(0.021) and answers[1]["estimate"] == pytest.approx(0.002)
    assert {a["turn"] for a in answers} == {1} and answers[0]["input"] == 1000 and answers[0]["output"] == 200
    reached = [f for kind, f in events if kind == "budget"]
    assert len(reached) == 1 and reached[0]["budget"] == 0.03 and reached[0]["state"] == "reached"
    assert meter.totals()["answers"] == 15 and meter.totals()["held"] == 2 and meter.totals()["asked"] == 15
    assert p.spent()["answers"] == 15 and not os.path.exists(llm.USAGE)     # (played: never in the ledger)
    assert meter.outputs() == {llm.kind_of(p, system): {"out": 200.0, "chars": 3.0, "per_in": 0.2}}   # (the next
    longer = [{"role": "user", "content": "x" * 5700}]      # run's prior; a question twice as long: the answers' tokens
    assert meter.estimate(p, system, longer) == pytest.approx((2000 + 400 * 5.0) / 1e6)  # out a token in, 0.2)
    failing = llm.Scripted([], prices=PRICES)               # (no answer left: the provider's error)
    with llm.metered(llm.Meter(budget=1.0)) as m:
        with pytest.raises(llm.ProviderError):
            failing.ask(system, question)
        assert m.reserved == pytest.approx(0) and m.spent == 0
    prior = llm.Meter(prior={llm.kind_of(p, system): {"out": 300, "chars": 2.0}})    # (an earlier run's answers)
    assert prior.estimate(p, system, question) == pytest.approx((1500 + 300 * 5.0) / 1e6)
    assert llm.Meter().estimate(llm.Scripted([]), system, question) is None    # (no prices: no estimate)


def test_a_batch_asks_the_questions_that_fit_and_its_events_say_how_it_went():
    """ask_batch under a meter: the questions are admitted in their order at the batch rate while they fit
    the budget; those that do not are weighed again once the first are answered (the answers' lengths
    known then) and go in a batch of their own, until none fits: those fail unasked (BudgetReached), and
    only then is the budget reached. Each batch is an event when it is submitted (with its estimate),
    looked at and ended (its counts), each answer one; a round of converse_all is an event too."""
    events = []
    p = llm.Scripted(lambda messages: {"text": "ok", "input": 1000, "tokens": 200}, batches=True, prices=PRICES)
    meter = llm.Meter(budget=0.0115, emit=lambda kind, **fields: events.append((kind, fields)))
    questions = [("s" * 300, [{"role": "user", "content": "x" * 2700}], None) for _ in range(12)]
    with llm.metered(meter):    # (each estimated at (1,000 + 4,000 * 5) / 1e6 * 0.5 = 0.0105 first: one fits;
        replies = llm.ask_batch(p, questions, out=lambda *a: None)     # then at 0.001: ten more fit)
    assert [r[0] for r in replies] == ["ok"] * 11 + [None] and isinstance(replies[11][2], llm.BudgetReached)
    assert [len(b["requests"]) for b in p.submitted] == [1, 10]
    assert meter.spent == pytest.approx(11 * (1000 + 200 * 5) / 1e6 * 0.5) and meter.reserved == pytest.approx(0)
    assert meter.spent <= meter.budget
    batch = [f for kind, f in events if kind == "batch"]
    assert [b["state"] for b in batch] == ["submitted", "looked", "ended"] * 2, batch
    assert batch[0]["requests"] == 1 and batch[0]["estimate"] == pytest.approx(0.0105)
    assert batch[3]["requests"] == 10 and batch[3]["estimate"] == pytest.approx(0.010)
    assert batch[1]["status"] == "ended" and batch[1]["counts"]["succeeded"] == 1
    assert batch[5]["succeeded"] == 10 and batch[5]["errored"] == 0
    assert [f["mode"] for kind, f in events if kind == "answer"] == ["batch"] * 11
    assert [f["questions"] for kind, f in events if kind == "budget"] == [1] and meter.reached
    events.clear()
    with llm.metered(llm.Meter(emit=lambda kind, **fields: events.append((kind, fields)))):
        upper = llm.Scripted(lambda messages: messages[0]["content"].upper(), batches=True)
        assert llm.converse_all(upper, [talk(q, q.upper()) for q in "ab"], batch=1) == ["A", "B"]
    assert [f for kind, f in events if kind == "round"][0] == {"questions": 2, "batch": True}


def test_a_stopped_pool_asks_nothing_more():
    """llm.thread_pool: a block ended by an exception (a run stopped by SIGTERM) cancels the questions
    not begun and waits only for the one being asked."""
    import time
    started = []

    def work(k):
        started.append(k)
        time.sleep(0.2)
    with pytest.raises(KeyboardInterrupt):
        with llm.thread_pool(1) as pool:
            futures = [pool.submit(work, k) for k in range(5)]
            while not started:
                time.sleep(0.01)
            raise KeyboardInterrupt
    assert started == [0] and [f.cancelled() for f in futures] == [False] + [True] * 4
    with llm.thread_pool(2) as pool:            # (ended normally: everything asked)
        assert list(pool.map(abs, [-1, -2, -3])) == [1, 2, 3]
    began = time.time()                         # (a question longer than stop_wait: the block ends without it,
    with pytest.raises(KeyboardInterrupt):      # its thread going on)
        with llm.thread_pool(1, stop_wait=0.1) as pool:
            slow = pool.submit(time.sleep, 1.0)
            time.sleep(0.05)
            raise KeyboardInterrupt
    assert time.time() - began < 0.9 and not slow.done()
    slow.result(5)
