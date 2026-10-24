"""dread llm set and dread llm key (dread.llmconfig) and the keys of key files (dread.llm): llm.toml edited
line by line with its comments, other providers and odd layouts kept, what the editor refuses; a key
written with mode 600 and read when a request goes out, never printed, never in an error, refused in a
git work tree, in a directory others may write to, or readable by others."""
import http.server
import io
import json
import os
import stat
import threading
import tomllib

import pytest

from dread import jobs as jobs_
from dread import llm, llmconfig

CONFIG = '''# my providers
default = "local"           # the one I use

[providers.local]                 # llama-server
kind = "openai"
url = "http://127.0.0.1:8080"     # the base
model = "local"
max_tokens = 32000

# the cloud one, asked rarely
[providers.claude]
kind = "anthropic"
model = "claude-sonnet-5-5"       # the model
key_env = "ANTHROPIC_API_KEY"
price_in = 3.0
quick = {thinking = {type = "disabled"}}

[providers.copilot]
kind = "command"
command = ["copilot", "-p", "{prompt}",
           "--model={model}"]     # spans lines
model = "x"
'''
KEY = "sk-test-0123456789abcdefghij"


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "cfg" / "llm.toml"
    path.parent.mkdir(mode=0o700)
    path.write_text(CONFIG)
    monkeypatch.setenv("DREAD_LLM", str(path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return path


def parsed(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def test_a_field_changed_in_place_and_comments_kept(config):
    done = llmconfig.set_provider(str(config), "claude", {"model": "claude-haiku-5-5", "price_out": 15,
                                                          "cache_min": 512})
    text = config.read_text()
    assert 'model = "claude-haiku-5-5"       # the model' in text
    assert "# my providers" in text and "# the cloud one, asked rarely" in text and "# llama-server" in text
    assert 'command = ["copilot", "-p", "{prompt}",\n           "--model={model}"]     # spans lines' in text
    lines = text.splitlines()
    assert lines.index("price_out = 15.0") == lines.index("quick = {thinking = {type = \"disabled\"}}") + 1
    assert lines.index("cache_min = 512") == lines.index("price_out = 15.0") + 1
    new = parsed(config)
    assert new["providers"]["claude"]["model"] == "claude-haiku-5-5" and new["providers"]["claude"]["price_out"] == 15.0
    assert new["providers"]["local"] == parsed_text(CONFIG)["providers"]["local"]
    assert ("model", "claude-sonnet-5-5", "claude-haiku-5-5") in done and ("price_out", None, 15.0) in done


def parsed_text(text):
    return tomllib.loads(text)


def test_a_new_provider_the_default_and_a_field_taken_out(config):
    mode = 0o640
    os.chmod(config, mode)
    llmconfig.set_provider(str(config), "haiku", {"kind": "anthropic", "model": "claude-haiku-5-5", "price_in": 0.1,
                                                  "price_out": 0.5, "batch": False}, default=True)
    text = config.read_text()
    assert text.endswith('[providers.haiku]\nkind = "anthropic"\nmodel = "claude-haiku-5-5"\nprice_in = 0.1\n'
                         'price_out = 0.5\nbatch = false\n')
    assert 'default = "haiku"           # the one I use' in text
    assert stat.S_IMODE(os.stat(config).st_mode) == mode
    llmconfig.set_provider(str(config), "local", {}, unset=["url"])
    assert "url =" not in config.read_text().split("[providers.claude]")[0]
    assert parsed(config)["default"] == "haiku" and "url" not in parsed(config)["providers"]["local"]


def test_a_file_from_nothing_and_a_default_where_there_was_none(tmp_path):
    path = tmp_path / "new" / "llm.toml"
    llmconfig.set_provider(str(path), "a", {"kind": "openai", "model": "m", "url": "http://127.0.0.1:1"}, default=True)
    assert parsed(path) == {"default": "a", "providers": {"a": {"kind": "openai", "model": "m", "url": "http://127.0.0.1:1"}}}
    other = tmp_path / "other.toml"
    other.write_text("# only comments here\n\n[providers.b]\nkind = \"openai\"\nmodel = \"m\"\n")
    llmconfig.set_provider(str(other), "b", {}, default=True)
    assert other.read_text().startswith("# only comments here\n\ndefault = \"b\"\n\n[providers.b]")


def test_what_set_refuses_and_leaves_alone(config, tmp_path):
    before = config.read_text()
    for args, words in ((("x y", {"kind": "openai", "model": "m"}), "letters, digits"),
                        (("new", {"model": "m"}), "needs --kind"),
                        (("new", {"kind": "command", "model": "m"}), "written into llm.toml by hand"),
                        (("new", {"kind": "nope", "model": "m"}), "kind is one of"),
                        (("local", {"url": "ftp://x"}), "url is the server's base"),
                        (("local", {"price_in": -1}), "a number of 0 or more"),
                        (("local", {"cache_min": "lots"}), "a number of 0 or more"),
                        (("local", {"key_env": "1BAD"}), "names an environment variable"),
                        (("copilot", {"key_env": "X"}), "takes no key"),
                        (("local", {"headers": "x"}), "no field headers"),
                        (("local", {"model": "a\nb"}), "one line of text")):
        with pytest.raises(llmconfig.ConfigError, match=words):
            llmconfig.set_provider(str(config), *args)
    with pytest.raises(llmconfig.ConfigError, match="cannot be taken out"):
        llmconfig.set_provider(str(config), "local", {}, unset=["model"])
    assert config.read_text() == before
    inline = tmp_path / "inline.toml"
    inline.write_text('[providers]\nlocal = {kind = "openai", model = "m"}\n')
    with pytest.raises(llmconfig.ConfigError, match="inline table"):
        llmconfig.set_provider(str(inline), "local", {"model": "n"})
    dotted = tmp_path / "dotted.toml"
    dotted.write_text('providers.local.kind = "openai"\nproviders.local.model = "m"\n')
    with pytest.raises(llmconfig.ConfigError, match="dotted keys"):
        llmconfig.set_provider(str(dotted), "local", {"model": "n"})
    broken = tmp_path / "broken.toml"
    broken.write_text("[providers.a\n")
    with pytest.raises(llmconfig.ConfigError, match="does not parse"):
        llmconfig.set_provider(str(broken), "a", {"model": "n"})


def test_odd_but_valid_layouts(tmp_path):
    """Quoted keys, a multi-line string, a value with brackets and a # in a string, a table of the provider's
    own fields after it: the editor finds the provider's lines all the same."""
    path = tmp_path / "odd.toml"
    path.write_text('[providers."quoted"]\nkind = "openai"\nmodel = "a#b [c]"   # not a comment inside\n'
                    'notes = """two\nlines = 1\n"""\nurl = "http://h:1"\n[providers.quoted.headers]\nx = "y"\n')
    with pytest.raises(llmconfig.ConfigError, match="tables of its own"):
        llmconfig.set_provider(str(path), "quoted", {"model": "m2"})
    path.write_text('[providers."quoted"]\nkind = "openai"\nmodel = "a#b [c]"   # not a comment inside\n'
                    'notes = """two\nlines = 1\n"""\nurl = "http://h:1"\n')
    llmconfig.set_provider(str(path), "quoted", {"model": "m2", "price_in": 1})
    data = parsed(path)["providers"]["quoted"]
    assert data["model"] == "m2" and data["notes"] == "two\nlines = 1\n" and data["price_in"] == 1.0
    assert "# not a comment inside" in path.read_text()


def test_a_key_into_its_file_and_out_at_request_time(config, tmp_path, monkeypatch):
    llmconfig.set_provider(str(config), "claude", {}, unset=["key_env"])
    where, changed = llmconfig.write_key(str(config), "claude", "  " + KEY + "\n")
    assert changed and where == str(config.parent / "keys" / "claude")
    assert stat.S_IMODE(os.stat(where).st_mode) == 0o600 and stat.S_IMODE(os.stat(os.path.dirname(where)).st_mode) == 0o700
    assert 'key_file = "keys/claude"' in config.read_text() and KEY not in config.read_text()
    provider = llm.load("claude")
    assert provider.headers()["x-api-key"] == KEY
    status = llm.key_status(parsed(config)["providers"]["claude"], str(config))
    assert status == {"needed": True, "set": True, "how": "file " + where, "problem": None}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-not-taken-0000")     # (a key_file, no key_env: the file)
    assert llm.load("claude").headers()["x-api-key"] == KEY
    os.chmod(where, 0o644)                                              # (read when a request goes out)
    with pytest.raises(llm.ProviderError) as e:
        llm.load("claude").headers()
    assert "chmod 600" in str(e.value) and KEY not in str(e.value)
    os.chmod(where, 0o600)
    llmconfig.set_provider(str(config), "claude", {"key_env": "MY_KEY"})
    monkeypatch.setenv("MY_KEY", "sk-from-my-environment-0001")         # (key_env set: it comes first)
    assert llm.load("claude").headers()["x-api-key"] == "sk-from-my-environment-0001"
    monkeypatch.setenv("MY_KEY", "")
    assert llm.load("claude").headers()["x-api-key"] == KEY
    assert llmconfig.file_keys(str(config)) == [(KEY, "key_file of claude")]


def test_where_a_key_may_not_go(config, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(llmconfig.ConfigError, match="git work tree") as e:
        llmconfig.write_key(str(config), "claude", KEY, str(repo / "keys" / "claude"))
    assert KEY not in str(e.value) and not (repo / "keys").exists()
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    with pytest.raises(llmconfig.ConfigError, match="others may write"):
        llmconfig.write_key(str(config), "claude", KEY, str(shared / "k"))
    for key, words in (("", "no key given"), ("two words", "one word"), ("x" * 5000, "more than")):
        with pytest.raises(llmconfig.ConfigError, match=words) as e:
            llmconfig.write_key(str(config), "claude", key)
        assert key not in str(e.value) or not key
    with pytest.raises(llmconfig.ConfigError, match="takes no key"):
        llmconfig.write_key(str(config), "copilot", KEY)
    with pytest.raises(llmconfig.ConfigError, match="no provider nobody"):
        llmconfig.write_key(str(config), "nobody", KEY)
    assert not (config.parent / "keys").exists()
    good = tmp_path / "keys2" / "k"
    good.parent.mkdir(mode=0o700)
    good.write_text(KEY)
    good.chmod(0o600)
    link = tmp_path / "keys2" / "link"
    link.symlink_to(good)
    assert llm.check_key_file(str(link)) is None and llm.read_key_file(str(link)) == KEY
    assert "no key file" in llm.check_key_file(str(tmp_path / "keys2" / "none"))
    (tmp_path / "keys2" / "empty").write_text("")
    (tmp_path / "keys2" / "empty").chmod(0o600)
    assert "is empty" in llm.check_key_file(str(tmp_path / "keys2" / "empty"))


def test_the_commands_set_key_and_providers(config, monkeypatch, capsys):
    assert llm.main(["set", "mine", "--kind", "openai", "--model", "m", "--url", "http://127.0.0.1:9", "--price-in",
                     "0.2", "--no-batch"]) == 0
    out = capsys.readouterr().out
    assert "provider mine: kind \"openai\" (new)" in out and "batch false (new)" in out
    monkeypatch.setattr("sys.stdin", io.StringIO(KEY + "\n"))
    assert llm.main(["key", "mine"]) == 0
    out = capsys.readouterr()
    assert KEY not in out.out + out.err and "mode 600" in out.out and "key_file names it" in out.out
    assert llm.main(["providers"]) == 0
    listed = capsys.readouterr().out
    assert "key: file " in listed and "no key" in listed and KEY not in listed and "(default)" in listed
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert llm.main(["key", "mine"]) == 1 and "no key given" in capsys.readouterr().err
    assert llm.main(["set", "mine"]) == 1 and "nothing to change" in capsys.readouterr().err
    assert llm.main(["set", "mine", "--unset", "price_in", "--default"]) == 0
    assert parsed(config)["default"] == "mine" and "price_in" not in parsed(config)["providers"]["mine"]


class Echo(http.server.BaseHTTPRequestHandler):
    """A server that answers an error with the request's authorization in it."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps({"error": "bad request from %s" % self.headers.get("authorization")}).encode()
        self.send_response(400)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_an_error_that_echoes_the_key_does_not_show_it(tmp_path):
    keys = tmp_path / "k"
    keys.mkdir(mode=0o700)
    (keys / "key").write_text(KEY)
    (keys / "key").chmod(0o600)
    server = http.server.HTTPServer(("127.0.0.1", 0), Echo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        p = llm.OpenAICompatible("echo", {"url": "http://127.0.0.1:%d" % server.server_address[1], "model": "m",
                                          "key_file": str(keys / "key"), "retries": 0})
        with pytest.raises(llm.ProviderError) as e:
            p.ask("s", [{"role": "user", "content": "q"}])
        assert "HTTP 400" in str(e.value) and "[key]" in str(e.value) and KEY not in str(e.value)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


def test_a_jobs_log_hides_a_key_it_was_given(tmp_path):
    import sys
    import time
    jobs = jobs_.Jobs(str(tmp_path / "jobs"))
    try:
        jobs.add_secret(KEY, "key_file of claude")
        r = jobs.start([sys.executable, "-c", "print(%r)" % ("the key is " + KEY)], str(tmp_path))
        deadline = time.time() + 60
        while jobs.get(r["id"])["state"] in jobs_.LIVE and time.time() < deadline:
            time.sleep(0.05)
        text = jobs.log(r["id"], 0)["text"]
        assert "the key is [$key_file of claude]" in text and KEY not in text
        assert KEY not in json.dumps(jobs.get(r["id"]))
    finally:
        jobs.close()
