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

Requests go out with urllib (no SDK). Keys are read from the environment only; nothing here writes a
key anywhere.

Every answer of a provider adds a line to a usage ledger (USAGE: time, provider, model, input and
output tokens, how it ended; no text of the conversation, no key), so that what a month's runs spent
can be told: usage_summary(), `dread pilot --usage`. A provider's price_in and price_out (US dollars
per million tokens, from its price list) turn the tokens into money; without them only the tokens
are summed.
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
    """ask(system, messages) -> text; messages are [{"role": "user"|"assistant", "content": str}].
    After each answer, last says how it ended: {"finish": the provider's reason ("stop", "length",
    "end_turn", "max_tokens", ...), "tokens": tokens written or None, "reasoning": characters of
    reasoning the provider returned apart from the answer}."""

    kind = None
    CUT = ("length", "max_tokens")          # finish reasons that mean: out of room
    REFUSED = ("refusal", "content_filter")  # finish reasons that mean: the provider declined (asking the
                                             # same again gets the same: Anthropic's classifiers answer so)

    def __init__(self, name, config):
        self.name, self.config = name, dict(config)
        self.model = self.config.get("model", "?")
        self.last = {}

    def label(self):
        return "%s (%s)" % (self.name, self.model)

    def record(self):
        """Add the last answer's tokens to the usage ledger (a failure to write is not an error)."""
        import time
        info = self.last
        try:
            os.makedirs(os.path.dirname(USAGE), exist_ok=True)
            new = not os.path.exists(USAGE)
            with open(USAGE, "a") as f:
                if new:
                    f.write("time\tprovider\tmodel\tinput\toutput\tfinish\n")
                f.write("%s\t%s\t%s\t%d\t%d\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), self.name, self.model,
                                                     info.get("input") or 0, info.get("tokens") or 0,
                                                     info.get("finish") or ""))
        except OSError:
            pass

    def ask(self, system, messages):
        raise NotImplementedError

    def _post(self, url, body, headers):
        data = json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, headers=dict({"content-type": "application/json"}, **headers))
        try:
            with urllib.request.urlopen(request, timeout=float(self.config.get("timeout", 1800))) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            raise ProviderError("%s: HTTP %d: %s" % (self.name, e.code, e.read().decode(errors="replace")[:300]))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ProviderError("%s: %s" % (self.name, e))


class OpenAICompatible(Provider):
    kind = "openai"

    def ask(self, system, messages):
        url = self.config.get("url", "http://127.0.0.1:8080").rstrip("/") + "/v1/chat/completions"
        headers = {}
        key_env = self.config.get("key_env")
        if key_env and os.environ.get(key_env):
            headers["authorization"] = "Bearer " + os.environ[key_env]
        body = {"model": self.model, "messages": [{"role": "system", "content": system}] + list(messages),
                "max_tokens": int(self.config.get("max_tokens", 32000)),
                "temperature": float(self.config.get("temperature", 0.2))}
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

    def ask(self, system, messages):
        key = os.environ.get(self.config.get("key_env", "ANTHROPIC_API_KEY"), "")
        if not key:
            raise ProviderError("%s: no key in $%s" % (self.name, self.config.get("key_env", "ANTHROPIC_API_KEY")))
        url = self.config.get("url", "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        body = {"model": self.model, "max_tokens": int(self.config.get("max_tokens", 16000)), "system": system,
                "messages": list(messages)}
        reply = self._post(url, body, {"x-api-key": key, "anthropic-version": "2023-06-01"})
        try:
            usage = reply.get("usage") or {}
            self.last = {"finish": reply.get("stop_reason"), "tokens": usage.get("output_tokens"),
                         "input": sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                                  "cache_read_input_tokens")),
                         "reasoning": sum(len(b.get("thinking", "")) for b in reply["content"]
                                          if b.get("type") == "thinking")}
            self.record()
            return "".join(block.get("text", "") for block in reply["content"] if block.get("type") == "text")
        except (KeyError, TypeError):
            raise ProviderError("%s: unexpected reply %s" % (self.name, json.dumps(reply)[:300]))


class Scripted(Provider):
    """Answers given in advance (a list, used in order, or a function of the messages): for tests. An
    answer may be a dict {"text": ..., "finish": ..., "tokens": ...} to play how it ended."""

    kind = "scripted"

    def __init__(self, answers, name="scripted"):
        super().__init__(name, {"model": "scripted"})
        self.answers, self.asked = answers, []

    def ask(self, system, messages):
        self.asked.append((system, list(messages)))
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


KINDS = {"openai": OpenAICompatible, "anthropic": Anthropic}


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
                if "price_in" in spec and "price_out" in spec:
                    prices[name] = (float(spec["price_in"]), float(spec["price_out"]))
    out = {}
    path = path or USAGE
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            row = line.rstrip("\n").split("\t")
            if len(row) < 6 or not row[0].startswith(month):
                continue
            entry = out.setdefault(row[1], {"model": row[2], "answers": 0, "input": 0, "output": 0, "cost": None})
            entry["answers"] += 1
            entry["input"] += int(row[3])
            entry["output"] += int(row[4])
    for name, entry in out.items():
        if name in prices:
            entry["cost"] = (entry["input"] * prices[name][0] + entry["output"] * prices[name][1]) / 1e6
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
