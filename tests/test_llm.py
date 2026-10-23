"""dread.llm's command provider (a command line that answers: an agent's headless mode, a local
model's wrapper) and the dread llm command, with a stand-in program that reports what it was given."""
import json
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
