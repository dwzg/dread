"""Language-model providers for dread pilot: messages in, the answer's text out.

Configured in ~/.config/dread/llm.toml (or the file $DREAD_LLM names):

    default = "local"

    [providers.local]                 # any OpenAI-compatible server: llama-server, Ollama, LM Studio, vLLM
    kind = "openai"
    url = "http://127.0.0.1:8080"     # the server's base; requests go to /v1/chat/completions
    model = "local"
    max_tokens = 32000                # reasoning models need room: an empty answer counts as none
    temperature = 0.2
    timeout = 1800

    [providers.claude]
    kind = "anthropic"
    model = "claude-sonnet-5-5"
    key_env = "ANTHROPIC_API_KEY"     # the key comes from the environment, never from a file
    max_tokens = 16000

    [providers.github-models]         # GitHub Models: OpenAI's format at a path of its own
    kind = "openai"
    url = "https://models.github.ai/inference"     # an organisation's: .../orgs/ORG/inference
    path = "/chat/completions"        # default /v1/chat/completions
    model = "openai/gpt-4.1"
    key_env = "GITHUB_MODELS_TOKEN"   # a fine-grained token with the models:read permission
    headers = {"x-github-api-version" = "2022-11-28"}   # sent as they are; never a key

    [providers.copilot]               # a command line that answers: GitHub Copilot CLI, headless
    kind = "command"
    command = ["copilot", "-p", "{prompt}", "-s", "--no-ask-user", "--model={model}",
               "--deny-tool=shell", "--deny-tool=write", "--deny-tool=url"]
    model = "claude-sonnet-4.5"       # one the Copilot plan offers
    timeout = 1800

A command provider runs its command with the conversation as one text (the system part, then each
message under its role): in {prompt} (one argument, 128 KiB at most on Linux), in a file named by
{prompt_file}, or on standard input when the command names neither; {model} is the provider's
model. Its standard output is the answer. It runs in a new, empty directory, removed afterwards,
without the environment's keys (drop_env: patterns, default DREAD_*KEY* and *_API_KEY), with no
terminal to ask on: an agent's headless mode finds nothing there to read, and the question is all it
is given. An agent's own tools are its command's business: turn them off there (the Copilot preset
denies its shell, write and url tools as GitHub's documentation describes; not tried here, where
there is no Copilot). An agent's headless mode still reads its user's own instructions (Claude
Code's ~/.claude/CLAUDE.md shaped an answer when it was tried with `claude -p`): they travel with
the question. Tokens are not known: the ledger counts its answers.

The two GitHub presets follow GitHub's documentation (2026-10): the CLI's programmatic mode
(`-p`, `-s`, `--no-ask-user`, `--model=`, `--deny-tool=`; a Business or Enterprise plan switches
the CLI on by policy) and GitHub Models' inference endpoint (a token with models:read; the
organisation must allow models). Neither was tried here. `dread llm ask` tries a provider with one
question; `dread llm providers` lists them.

Requests go out with urllib (no SDK). Keys are read from the environment only; nothing here writes a
key anywhere. A rate limit or an overload (HTTP 429, 529, 500, 502-504) is asked again up to retries
times (default 4), after the server's retry-after or retry_wait seconds (default 5), three times
longer each time.

Every answer of a provider adds a line to a usage ledger (USAGE: time, provider, model, input and
output tokens, how it ended; no text of the conversation, no key), so that what a month's runs spent
can be told: usage_summary(), `dread pilot --usage`. A provider's price_in and price_out (US dollars
per million tokens, from its price list) turn the tokens into money; without them only the tokens
are summed.

A message's content is a text, or a list of parts ({"text": ..., "cache": True}) for a question whose
first part many questions share (a function's source that several runs' names are judged against):
Anthropic's provider marks a part with cache to be kept (cache_control, its prompt cache: a later
question with the same beginning reads it for a tenth of the price, the first writes it for a quarter
more, Anthropic's list in 2026-10; price_cache_read and price_cache_write override, in US dollars per
million tokens), the others join the parts into one text (content_text). The ledger's line then gets
the tokens read from and written to the cache as two more columns.
"""
import json
import os
import urllib.error
import urllib.request

CONFIG = os.path.join(os.path.expanduser("~"), ".config", "dread", "llm.toml")
USAGE = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state"),
                     "dread", "llm-usage.tsv")


class ProviderError(Exception):
    pass


class Provider:
    """ask(system, messages, temperature=None) -> text; messages are [{"role": "user"|"assistant",
    "content": str or [parts]}] (a list of parts: see the module); temperature, when given, the
    sampling temperature of this question in place of the configuration's (an OpenAI-compatible
    server takes it; Anthropic's API is sent it, and Haiku 5.5 and Sonnet 5.5 answer any other value
    than their default 1.0 with HTTP 400, "`temperature` is deprecated for this model", tried
    2026-10-22; a command provider cannot pass it on). After each
    answer, last says how it ended: {"finish": the provider's reason ("stop", "length", "end_turn",
    "max_tokens", ...), "tokens": tokens written or None, "reasoning": characters of reasoning the
    provider returned apart from the answer}."""

    kind = None
    CUT = ("length", "max_tokens")          # finish reasons that mean: out of room
    REFUSED = ("refusal", "content_filter")  # finish reasons that mean: the provider declined (asking the
                                             # same again gets the same: Anthropic's classifiers answer so)

    def __init__(self, name, config):
        self.name, self.config = name, dict(config)
        self.model = self.config.get("model", "?")
        self.last = {}
        self.used = {"answers": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}  # (spent())

    def label(self):
        return "%s (%s)" % (self.name, self.model)

    def record(self):
        """Add the last answer's tokens to the usage ledger (a failure to write is not an error) and to
        this object's own count (spent())."""
        import time
        info = self.last
        self.used["answers"] += 1
        self.used["input"] += info.get("input") or 0
        self.used["output"] += info.get("tokens") or 0
        self.used["cache_read"] += info.get("cache_read") or 0
        self.used["cache_write"] += info.get("cache_write") or 0
        try:
            os.makedirs(os.path.dirname(USAGE), exist_ok=True)
            new = not os.path.exists(USAGE)
            with open(USAGE, "a") as f:
                if new:
                    f.write("time\tprovider\tmodel\tinput\toutput\tfinish\n")
                cached = ("\t%d\t%d" % (info.get("cache_read") or 0, info.get("cache_write") or 0)
                          if info.get("cache_read") or info.get("cache_write") else "")
                f.write("%s\t%s\t%s\t%d\t%d\t%s%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), self.name, self.model,
                                                       info.get("input") or 0, info.get("tokens") or 0,
                                                       info.get("finish") or "", cached))
        except OSError:
            pass

    def spent(self):
        """What this provider object used, its own answers only, with the cost where the configuration
        gives prices (price_in, price_out). The ledger is shared: a run that counted the difference of
        its totals counted every run beside it too (six pilot runs at once, 2026-10-21, each reported
        about six times its own tokens)."""
        out = dict(self.used, cost=None)
        prices = prices_of(self.config, self.kind)
        if prices:
            out["cost"] = cost(prices, out["input"], out["output"], out["cache_read"], out["cache_write"])
        return out

    def ask(self, system, messages, temperature=None):
        raise NotImplementedError

    RETRY = (429, 500, 502, 503, 504, 529)  # rate limits and overloads: asked again after a wait

    def _post(self, url, body, headers):
        """The reply to a request, as JSON. A rate limit or an overload (RETRY) is asked again up to
        retries times (default 4), after the server's retry-after or retry_wait seconds (default 5),
        three times longer each time: a run of thousands of questions meets them."""
        import time
        data = json.dumps(body).encode()
        tries = int(self.config.get("retries", 4)) + 1
        wait = float(self.config.get("retry_wait", 5))
        for attempt in range(tries):
            request = urllib.request.Request(url, data=data,
                                             headers=dict({"content-type": "application/json"}, **headers))
            try:
                with urllib.request.urlopen(request, timeout=float(self.config.get("timeout", 1800))) as response:
                    return json.loads(response.read().decode())
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace")[:300]
                if e.code not in self.RETRY or attempt + 1 == tries:
                    raise ProviderError("%s: HTTP %d: %s" % (self.name, e.code, text))
                try:
                    pause = min(float(e.headers.get("retry-after")), 300.0)
                except (AttributeError, TypeError, ValueError):
                    pause = wait * 3 ** attempt
                time.sleep(pause)
            except (urllib.error.URLError, OSError, ValueError) as e:
                raise ProviderError("%s: %s" % (self.name, e))


class OpenAICompatible(Provider):
    kind = "openai"

    def ask(self, system, messages, temperature=None):
        url = self.config.get("url", "http://127.0.0.1:8080").rstrip("/") + self.config.get("path",
                                                                                       "/v1/chat/completions")
        headers = dict(self.config.get("headers", {}))
        if any(k.lower() in ("authorization", "x-api-key", "api-key") for k in headers):
            raise ProviderError("%s: a key comes from the environment (key_env), not from headers" % self.name)
        key_env = self.config.get("key_env")
        if key_env and os.environ.get(key_env):
            headers["authorization"] = "Bearer " + os.environ[key_env]
        body = {"model": self.model, "messages": [{"role": "system", "content": system}] + [
                    dict(m, content=content_text(m["content"])) for m in messages],
                "max_tokens": int(self.config.get("max_tokens", 32000)),
                "temperature": float(self.config.get("temperature", 0.2) if temperature is None else temperature)}
        reply = self._post(url, body, headers)
        try:
            choice = reply["choices"][0]
            message = choice["message"]
            reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
            usage = reply.get("usage") or {}
            self.last = {"finish": choice.get("finish_reason"), "reasoning": len(reasoning),
                         "tokens": usage.get("completion_tokens"), "input": usage.get("prompt_tokens")}
            self.record()
            return message.get("content") or ""
        except (KeyError, IndexError, TypeError):
            raise ProviderError("%s: unexpected reply %s" % (self.name, json.dumps(reply)[:300]))


class Anthropic(Provider):
    kind = "anthropic"

    @staticmethod
    def blocks(content):
        """A message's content as Anthropic takes it: a text as it is; parts as text blocks, a part
        with cache marked to be cached (the prompt up to its end is kept for later questions)."""
        if isinstance(content, str):
            return content
        return [dict({"type": "text", "text": part.get("text", "")},
                     **({"cache_control": {"type": "ephemeral"}} if part.get("cache") else {})) for part in content]

    def ask(self, system, messages, temperature=None):
        key = os.environ.get(self.config.get("key_env", "ANTHROPIC_API_KEY"), "")
        if not key:
            raise ProviderError("%s: no key in $%s" % (self.name, self.config.get("key_env", "ANTHROPIC_API_KEY")))
        url = self.config.get("url", "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        body = {"model": self.model, "max_tokens": int(self.config.get("max_tokens", 16000)), "system": system,
                "messages": [dict(m, content=self.blocks(m["content"])) for m in messages]}
        if temperature is not None:         # (only when asked for: see Provider)
            body["temperature"] = float(temperature)
        reply = self._post(url, body, {"x-api-key": key, "anthropic-version": "2023-06-01"})
        try:
            usage = reply.get("usage") or {}
            self.last = {"finish": reply.get("stop_reason"), "tokens": usage.get("output_tokens"),
                         "input": sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                                  "cache_read_input_tokens")),
                         "cache_read": usage.get("cache_read_input_tokens") or 0,
                         "cache_write": usage.get("cache_creation_input_tokens") or 0,
                         "reasoning": sum(len(b.get("thinking", "")) for b in reply["content"]
                                          if b.get("type") == "thinking")}
            self.record()
            return "".join(block.get("text", "") for block in reply["content"] if block.get("type") == "text")
        except (KeyError, TypeError):
            raise ProviderError("%s: unexpected reply %s" % (self.name, json.dumps(reply)[:300]))


def content_text(content):
    """A message's content as one text: a text as it is, a list of parts ({"text": ..., "cache": ...})
    joined by blank lines."""
    if isinstance(content, str):
        return content
    return "\n\n".join(part.get("text", "") for part in content or [])


CACHE_READ, CACHE_WRITE = 0.1, 1.25     # Anthropic's prompt cache against its input price (its list, 2026-10)


def prices_of(config, kind=None):
    """(in, out, cache read, cache write) in US dollars per million tokens from a provider's
    configuration (price_in, price_out; price_cache_read and price_cache_write, else the input price
    times Anthropic's factors for an anthropic provider, the input price for others), or None."""
    if "price_in" not in config or "price_out" not in config:
        return None
    price_in = float(config["price_in"])
    anthropic = (kind or config.get("kind")) == "anthropic"
    return (price_in, float(config["price_out"]),
            float(config.get("price_cache_read", price_in * (CACHE_READ if anthropic else 1))),
            float(config.get("price_cache_write", price_in * (CACHE_WRITE if anthropic else 1))))


def cost(prices, input_tokens, output_tokens, cache_read=0, cache_write=0):
    """US dollars of an answer's or a run's tokens: input counts every input token, those read from or
    written to a cache among them."""
    price_in, price_out, price_read, price_write = prices
    plain = max(0, input_tokens - cache_read - cache_write)
    return (plain * price_in + cache_read * price_read + cache_write * price_write + output_tokens * price_out) / 1e6


def conversation_text(system, messages):
    """A conversation as one text, for a provider that takes one: the system part, then the
    question; a longer conversation with each message under its role."""
    if len(messages) == 1 and messages[0]["role"] == "user":
        return system + "\n\n" + content_text(messages[0]["content"])
    parts = [system, "The conversation so far follows, oldest first; your earlier answers are marked ASSISTANT. "
                     "Answer the last USER message."]
    parts += ["=== %s ===\n%s" % (m["role"].upper(), content_text(m["content"])) for m in messages]
    return "\n\n".join(parts)


class Command(Provider):
    """A command line that answers (see the module): an agent's headless mode (GitHub Copilot
    CLI, Claude Code), a local model's wrapper, any program that prints an answer."""

    kind = "command"
    ARG_MAX = 128 * 1024 - 1            # Linux's limit for one argument (MAX_ARG_STRLEN)
    DROP_ENV = ("DREAD_*KEY*", "*_API_KEY")

    def ask(self, system, messages, temperature=None):
        import fnmatch
        import shutil
        import subprocess
        import tempfile
        spec = self.config.get("command")
        if not isinstance(spec, list) or not spec or not all(isinstance(a, str) for a in spec):
            raise ProviderError("%s: command is a list of arguments" % self.name)
        text = conversation_text(system, messages)
        joined = "\0".join(spec)
        inline, as_file = "{prompt}" in joined, "{prompt_file}" in joined
        if inline and len(text.encode()) > self.ARG_MAX:
            raise ProviderError("%s: the question is %d KiB, too long for one argument: name {prompt_file} "
                                "or leave the prompt to standard input" % (self.name, len(text.encode()) // 1024))
        drop = self.config.get("drop_env", self.DROP_ENV)
        env = {k: v for k, v in os.environ.items() if not any(fnmatch.fnmatchcase(k, p) for p in drop)}
        timeout = float(self.config.get("timeout", 1800))
        work = tempfile.mkdtemp(prefix="dread-llm-")
        try:
            question = os.path.join(work, "question.txt")
            if as_file:
                with open(question, "w") as f:
                    f.write(text)
            argv = [a.replace("{model}", self.model).replace("{prompt_file}", question).replace("{prompt}", text)
                    for a in spec]
            feed = {"stdin": subprocess.DEVNULL} if inline or as_file else {"input": text}
            try:
                done = subprocess.run(argv, cwd=work, env=env, capture_output=True, text=True, timeout=timeout,
                                      **feed)
            except FileNotFoundError:
                raise ProviderError("%s: no command %s" % (self.name, argv[0]))
            except subprocess.TimeoutExpired:
                raise ProviderError("%s: no answer within %.0f s" % (self.name, timeout))
            except OSError as e:
                raise ProviderError("%s: %s" % (self.name, e))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if done.returncode:
            said = (done.stderr.strip() or done.stdout.strip())[-300:]
            raise ProviderError("%s: exit %d: %s" % (self.name, done.returncode, said))
        self.last = {"finish": "stop", "tokens": None, "input": None, "reasoning": 0}
        self.record()
        return done.stdout


class Scripted(Provider):
    """Answers given in advance (a list, used in order, or a function of the messages): for tests. An
    answer may be a dict {"text": ..., "finish": ..., "tokens": ...} to play how it ended."""

    kind = "scripted"

    def __init__(self, answers, name="scripted"):
        super().__init__(name, {"model": "scripted"})
        self.answers, self.asked, self.temperatures = answers, [], []

    def ask(self, system, messages, temperature=None):
        self.asked.append((system, list(messages)))
        self.temperatures.append(temperature)
        if callable(self.answers):
            answer = self.answers(messages)
        elif not self.answers:
            raise ProviderError("scripted: no answer left")
        else:
            answer = self.answers.pop(0)
        if isinstance(answer, dict):
            self.last = {"finish": answer.get("finish", "stop"), "tokens": answer.get("tokens"),
                         "reasoning": answer.get("reasoning", 0)}
            return answer.get("text", "")
        self.last = {"finish": "stop", "tokens": None, "reasoning": 0}
        return answer


KINDS = {"openai": OpenAICompatible, "anthropic": Anthropic, "command": Command}


def usage_summary(month=None, path=None, config_path=None):
    """{provider: {"model", "answers", "input", "output", "cost" (None without prices)}} of the usage
    ledger for a month ("YYYY-MM", default: this one)."""
    import time
    import tomllib
    month = month or time.strftime("%Y-%m")
    prices = {}
    config_path = config_path or os.environ.get("DREAD_LLM") or CONFIG
    if os.path.exists(config_path):
        with open(config_path, "rb") as f:
            for name, spec in tomllib.load(f).get("providers", {}).items():
                if prices_of(spec):
                    prices[name] = prices_of(spec)
    out = {}
    path = path or USAGE
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            row = line.rstrip("\n").split("\t")
            if len(row) < 6 or not row[0].startswith(month):
                continue
            entry = out.setdefault(row[1], {"model": row[2], "answers": 0, "input": 0, "output": 0, "cost": None,
                                            "cache_read": 0, "cache_write": 0})
            entry["answers"] += 1
            entry["input"] += int(row[3])
            entry["output"] += int(row[4])
            if len(row) >= 8:               # (a line with cache columns, since 2026-10-22)
                entry["cache_read"] += int(row[6] or 0)
                entry["cache_write"] += int(row[7] or 0)
    for name, entry in out.items():
        if name in prices:
            entry["cost"] = cost(prices[name], entry["input"], entry["output"], entry["cache_read"],
                                 entry["cache_write"])
    return out


def load(name=None, path=None):
    """The provider `name` (default: the file's `default`) of the configuration file."""
    import tomllib
    path = path or os.environ.get("DREAD_LLM") or CONFIG
    if not os.path.exists(path):
        raise ProviderError("no provider configuration: %s (see dread.llm)" % path)
    with open(path, "rb") as f:
        config = tomllib.load(f)
    providers = config.get("providers", {})
    name = name or config.get("default") or (sorted(providers)[0] if providers else None)
    if name not in providers:
        raise ProviderError("no provider %s in %s (there are: %s)" % (name, path, ", ".join(sorted(providers))))
    spec = providers[name]
    kind = KINDS.get(spec.get("kind"))
    if kind is None:
        raise ProviderError("provider %s: unknown kind %r (known: %s)" % (name, spec.get("kind"), ", ".join(KINDS)))
    return kind(name, spec)


def main(argv=None):
    """dread llm providers | dread llm ask [--provider NAME] [--system TEXT] TEXT...: the configured
    providers, or one question to one of them with its answer (to try a provider before a pilot run)."""
    import argparse
    import sys
    import time
    parser = argparse.ArgumentParser(prog="dread llm", description="the language-model providers: list them, "
                                                                   "or ask one a question")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("providers", help="the providers of the configuration file")
    p = sub.add_parser("ask", help="ask a provider one question and print its answer")
    p.add_argument("--provider", help="a provider of the configuration (default: its default)")
    p.add_argument("--system", default="Answer in one short paragraph.")
    p.add_argument("text", nargs="+")
    args = parser.parse_args(argv)
    if args.action == "providers":
        import tomllib
        path = os.environ.get("DREAD_LLM") or CONFIG
        if not os.path.exists(path):
            print("dread llm: no configuration %s (see dread.llm)" % path, file=sys.stderr)
            return 1
        with open(path, "rb") as f:
            config = tomllib.load(f)
        for name, spec in sorted(config.get("providers", {}).items()):
            print("%-16s %-9s %s%s" % (name, spec.get("kind", "?"), spec.get("model", "?"),
                                       "   (default)" if name == config.get("default") else ""))
        return 0
    try:
        provider = load(args.provider)
        started = time.time()
        answer = provider.ask(args.system, [{"role": "user", "content": " ".join(args.text)}])
    except ProviderError as e:
        print("dread llm: %s" % e, file=sys.stderr)
        return 1
    print(answer.strip())
    info = provider.last
    print("(%s, %.0f s, %s%s)" % (provider.label(), time.time() - started, info.get("finish") or "?",
                                   ", %d tokens" % info["tokens"] if info.get("tokens") else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
